#!/usr/bin/env python3
"""Survey how an organisation's repositories expose their default-branch merge gate.

Emits a JSON array on stdout, one object per repository. Everything a human reads — progress,
warnings, the summary — goes to stderr, so stdout can be redirected straight into a file and
queried with jq.

    uv run scripts/survey_merge_gate_access.py [n] [--org hmcts] [--sort pushed]
                                               [--skip-archived] [--continue survey.json]

With no n, EVERY repository in the organisation is surveyed. Requires a token in GH_TOKEN or
GITHUB_TOKEN, or `gh` logged in. USER-RUN ONLY: the loop that maintains plan.md never holds a
GitHub token (architecture.md, "Access"), so neither this survey nor its results can be produced
inside it.

COST, AND WHAT HAPPENS WHEN THE QUOTA RUNS OUT. Up to five API calls per repository against a
5,000/hour authenticated limit, so an all-repos run over an organisation the size of hmcts (~1,900
repositories) is several thousand calls and will exhaust the quota partway through. The run is
limited by that quota and not by latency, which is why it stays sequential: concurrency would buy
nothing and would risk the secondary limits.

A rate limit is waited out only briefly — RATE_PAUSE_SECONDS, a few times — and then the run STOPS,
closing its JSON array so that what it has is valid. It does not sit out the remainder of the hour,
and it does not carry on writing `error` rows it cannot possibly answer: either would spend an hour
to produce nothing. Instead, rerun with `--continue` pointing at the output, and every repository
already answered is reprinted from that file without a single API call. Pass n to survey the n most
recently pushed only, or --skip-archived to spend nothing on repositories that can no longer
receive a merge.

WHY THIS ORDER OF CALLS. It is `collect_merge_gate` in src/metrics/inventory.py, by hand:
effective rulesets first, classic branch protection only when no ruleset applies, and the branch
metadata `protected` flag only when classic detail is refused. Asking in any other order would
report a different population, because a repository on rulesets is readable with ordinary access
while the same repository's classic endpoint would answer 404 and look unprotected. Being a
deliberate second implementation of that function, this script shares no code with it — a
by-hand check that imported the thing it checks would agree with it by construction.

THE THREE ANSWERS THE SURVEY EXISTS TO SEPARATE:
    ruleset       rules readable with ordinary access; no admin needed
    classic-full  classic protection, and this token may read its detail (admin access)
    classic-flag  classic protection refused; only the on/off indicator is visible (no admin)
plus `unprotected` (GitHub answered: no protection) and `error` (anything else). `classic-flag`
and `unprotected` are NOT the same observation and must never be merged: one is a blind spot, the
other is evidence.

ONE OBJECT PER REPOSITORY, AND WHAT EACH FIELD MEANS:
    repository      name within the organisation
    default_branch  the branch the gate was asked about; null for an empty repository
    archived        archived repositories cannot receive a merge, but they are still reported
    access          the highest role this token holds here — admin, maintain, push, triage or pull,
                    `none` where the listing reported no role and `unknown` where it reported
                    nothing at all. It costs no extra call: the organisation listing already carries
                    it. Reported because a fine-grained token cannot exceed its OWNER's access, so
                    this is the ceiling on everything else asked about the repository, and because
                    Dependabot alerts turn out to be readable at `push` (architecture.md, "Access")
                    — which makes this field the selector for the population that source can cover.
                    Alone among the fields it is refreshed on a `--continue` run, which is how an
                    earlier survey gains it without re-asking anything; see `survey`
    gate            one of the five answers above
    protected       true/false AS OBSERVED, or null where nothing observed it. On a `classic-flag`
                    row this is the on/off indicator itself — the only thing the token learns —
                    and it can be false, which is why it is a field and not implied by `gate`.
    active_rules    the rule types in force: read from the ruleset for `ruleset`, mapped from the
                    equivalent classic settings for `classic-full` (see `classic_rule_types`), []
                    where GitHub answered that there are none, and null where they could not be
                    READ. [] and null are different answers and are kept apart, exactly as `gate`
                    keeps `unprotected` and `classic-flag` apart. Which gates are in force, not
                    how strict each one is: approval counts and named checks are not reported.
    owning_teams    every team handle named anywhere in CODEOWNERS, sorted, the organisation's own
                    prefix stripped; [] if the file names none, null if there is no file to read
    codeowners      found | absent | HTTP <status> | unreachable, so an unread file is never
                    reported as an unowned repository
    error           what a call that did not answer answered instead — an HTTP status, or
                    `unreachable` where it never answered at all — even where a classification was
                    still reached without it; null when everything answered

--continue REUSES ANSWERS, NOT FAILURES. The named file is an earlier run's output, indexed by
repository name. A row that records an answer — including `classic-flag`, which is an answer about
permission — is reprinted verbatim and costs nothing. A row that records a run cut short instead of
an answer (`error`, or anything `unreachable`) is dropped and asked again, because a rate limit that
stopped the last run must never be preserved as though it were this organisation's blind spot. That
is the one conflation this whole survey exists to prevent, and a cache is the easiest place to
reintroduce it. Repositories in the file but absent from this run's population are reported and
otherwise ignored.

TEAMS COME FROM CODEOWNERS, not from `GET /repos/{org}/{repo}/teams`, for two reasons: it is
readable with ordinary repository access, where the teams endpoint needs a token scoped to see
them and would reintroduce exactly the permission cliff this survey exists to measure; and it
names the team that OWNS the code, where the teams endpoint lists every team GRANTED access, which
on an HMCTS repository is mostly org-wide admin teams. It is a convention and not an API, so a
repository without a CODEOWNERS file reports `absent` — no file, not no team.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

DEFAULT_API_URL = "https://api.github.com"
API_VERSION = "2022-11-28"
JSON_ACCEPT = "application/vnd.github+json"
RAW_ACCEPT = "application/vnd.github.raw"
PAGE_SIZE = 100
REQUEST_TIMEOUT = 30
SUBPROCESS_TIMEOUT = 30
# A fixed short pause, repeated a few times, rather than a wait until the quota resets: a secondary
# limit clears in seconds, and an exhausted hourly quota does not clear at all within any wait worth
# making. The second case is for `--continue` to solve, not for this script to sit through.
RATE_PAUSE_SECONDS = 60
MAXIMUM_RATE_PAUSES = 3
MAXIMUM_NETWORK_ATTEMPTS = 3
NETWORK_RETRY_SECONDS = 5

# Not a status GitHub can return, so it cannot be confused with one: it stands for a call that never
# answered at all. `describe_status` is the only thing that renders it.
NETWORK_FAILURE_STATUS = 0
UNREACHABLE = "unreachable"
CODEOWNERS_PATHS = (".github/CODEOWNERS", "CODEOWNERS")
SORT_FIELDS = ("pushed", "created", "updated", "full_name")

# Most permissive first, so the first one held is the highest. GitHub reports all of them as
# independent booleans and an admin holds every one, so the order is what makes a single answer of
# them.
ACCESS_LEVELS = ("admin", "maintain", "push", "triage", "pull")
NO_ACCESS = "none"
UNKNOWN_ACCESS = "unknown"

# Team handles only: `@org/team`. A bare `@user` is an individual and is not a team, so it is not
# reported as one.
TEAM_HANDLE_PATTERN = re.compile(r"@([A-Za-z0-9-]+)/([A-Za-z0-9._-]+)")
RATE_LIMIT_MARKERS = ("rate limit", "secondary rate", "abuse detection")

HELP_EPILOG = """\
picking the survey back up after it runs out of API quota:

  uv run scripts/survey_merge_gate_access.py > survey.json     # stops when the quota is gone
  cp survey.json done.json                                     # the redirect below truncates it
  uv run scripts/survey_merge_gate_access.py --continue done.json > survey.json

`--continue` cannot read the file it is being redirected over: the shell empties it before the
script starts. Copy it, or redirect somewhere new, and keep going until a run reports no `error`
rows and stops nothing short of the population.

turning the result into the `teams:` section of a config file:

  uv run scripts/survey_to_teams.py survey.json > teams.yml

That script selects a population by gate, groups it by owning team, and validates what it writes
against the model that will load it. Query the survey directly for anything else — every field is
documented above, so, for example, the repositories this token can read in full are:

  jq -r '.[] | select(.gate == "ruleset" or .gate == "classic-full") | .repository' survey.json

and the repositories whose Dependabot alerts this token can read, which is a different and much
larger population — write access is enough for that family, where the merge gate needs admin unless
the repository is on rulesets (architecture.md, "Access"):

  jq -r '.[] | select(.archived == false)
             | select(.access == "admin" or .access == "maintain" or .access == "push")
             | .repository' survey.json
"""


class Gate(StrEnum):
    """How a default branch is gated, as far as this token is permitted to see."""

    RULESET = "ruleset"
    CLASSIC_FULL = "classic-full"
    CLASSIC_FLAG = "classic-flag"
    UNPROTECTED = "unprotected"
    ERROR = "error"


@dataclass(frozen=True)
class Options:
    """What to survey."""

    organization: str
    sort: str
    limit: int | None
    skip_archived: bool
    resume: Path | None


@dataclass(frozen=True)
class Repository:
    """A repository as the organisation listing describes it."""

    name: str
    default_branch: str
    archived: bool
    access: str


@dataclass(frozen=True)
class Owners:
    """The teams CODEOWNERS names, and how the file itself answered."""

    teams: list[str] | None
    codeowners: str


@dataclass(frozen=True)
class MergeGate:
    """A classified merge gate, and the status of anything that refused to answer."""

    gate: Gate
    protected: bool | None
    active_rules: list[str] | None
    error: str | None = None


class Cache:
    """An earlier survey's answers, indexed by repository, reused rather than asked for again."""

    def __init__(self, answers: Mapping[str, dict[str, Any]] | None = None, discarded: int = 0) -> None:
        """Hold the reusable answers, and the count of rows that were not answers at all."""
        self.answers = dict(answers or {})
        self.discarded = discarded
        self.hits = 0

    def get(self, repository: str) -> dict[str, Any] | None:
        """Return the answer already recorded for a repository, counting the calls it saves."""
        entry = self.answers.get(repository)
        if entry is not None:
            self.hits += 1
        return entry

    def unused(self) -> int:
        """Count answers that were never asked for, because the population no longer holds them."""
        return len(self.answers) - self.hits


@dataclass(frozen=True)
class Response:
    """One HTTP response: the status that classifies, and the body it belongs to."""

    status: int
    text: str

    def parsed(self) -> object:
        """Return the body parsed as JSON, or None when it is not JSON at all."""
        try:
            return json.loads(self.text)
        except ValueError:
            return None

    def mapping(self) -> Mapping[str, Any]:
        """Return the body as a JSON object, or an empty one when it is anything else."""
        body = self.parsed()
        return body if isinstance(body, Mapping) else {}

    def items(self) -> list[Any]:
        """Return the body as a JSON array, or an empty one when it is anything else."""
        body = self.parsed()
        return body if isinstance(body, list) else []

    def message(self) -> str:
        """Return the API's own error message, or an empty string when it gave none."""
        return str(self.mapping().get("message", ""))


def progress(message: str) -> None:
    """Write a line for the human watching, never onto the JSON on stdout."""
    sys.stderr.write(f"{message}\n")
    sys.stderr.flush()


def describe_status(status: int) -> str:
    """Name a status for reporting, including the pseudo-status for a call that never answered."""
    return UNREACHABLE if status == NETWORK_FAILURE_STATUS else f"HTTP {status}"


def is_rate_limited(response: Response) -> bool:
    """Tell a rate limit apart from a refusal: both answer 403, and only the body says which."""
    if response.status not in {HTTPStatus.FORBIDDEN, HTTPStatus.TOO_MANY_REQUESTS}:
        return False
    lowered = response.text.lower()
    return any(marker in lowered for marker in RATE_LIMIT_MARKERS)


class RateLimitError(RuntimeError):
    """The quota did not clear within the pauses this survey is willing to take."""


class GitHubReader:
    """Reads the GitHub API, refusing to record a rate limit as though it were a blind spot.

    A survey long enough to cover a whole organisation will reach the hourly limit, and a run that
    reported `error` for the second half of its population because of it would be worse than
    useless: the permission blind spots this survey exists to measure would be indistinguishable
    from its own exhaustion. So a limit that does not clear stops the run instead, for
    `--continue` to resume once the quota has.
    """

    def __init__(self, api_url: str, token: str) -> None:
        """Prepare a session carrying the token and the pinned API version."""
        self.api_url = api_url.rstrip("/")
        self.rate_pauses = 0
        self.session = requests.Session()
        self.session.headers.update(
            {"Authorization": f"Bearer {token}", "X-GitHub-Api-Version": API_VERSION},
        )

    def get(self, path: str, *, accept: str = JSON_ACCEPT, parameters: Mapping[str, Any] | None = None) -> Response:
        """Fetch one path, retrying a transient network failure and briefly pausing a rate limit.

        A call that never answers becomes a pseudo-status rather than an exception: one unreachable
        repository should be one `error` row, not the end of a run with hours of work behind it. A
        rate limit is the opposite case and raises, because it will apply to every remaining
        repository just as it applied to this one.
        """
        pauses = 0
        failures = 0
        while True:
            try:
                response = self._request(path, accept=accept, parameters=parameters)
            except requests.RequestException as failure:
                failures += 1
                if failures >= MAXIMUM_NETWORK_ATTEMPTS:
                    return Response(NETWORK_FAILURE_STATUS, str(failure))
                time.sleep(NETWORK_RETRY_SECONDS * failures)
                continue
            if not is_rate_limited(response):
                return response
            if pauses >= MAXIMUM_RATE_PAUSES:
                raise RateLimitError(response.message())
            pauses += 1
            self._wait_for_rate_limit(pauses)

    def _request(self, path: str, *, accept: str, parameters: Mapping[str, Any] | None = None) -> Response:
        """Make one request, keeping the status with the body it belongs to."""
        response = self.session.get(
            f"{self.api_url}{path}",
            headers={"Accept": accept},
            params=parameters,
            timeout=REQUEST_TIMEOUT,
        )
        return Response(response.status_code, response.text)

    def _wait_for_rate_limit(self, attempt: int) -> None:
        """Pause briefly, reporting when the quota resets so the rerun can be timed against it."""
        self.rate_pauses += 1
        progress(
            f"rate limited: waiting {RATE_PAUSE_SECONDS}s "
            f"(pause {attempt} of {MAXIMUM_RATE_PAUSES}){self._reset_note()}",
        )
        time.sleep(RATE_PAUSE_SECONDS)

    def _reset_note(self) -> str:
        """Say how long the hourly quota has left to run, when the API will say."""
        reset = self._rate_limit_reset()
        if reset is None:
            return ""
        minutes = max(int((reset - time.time()) // 60), 0)
        return f"; hourly quota resets in {minutes} min"

    def _rate_limit_reset(self) -> float | None:
        """Read when the core quota resets. Asked directly, because /rate_limit cannot recurse."""
        try:
            body = self._request("/rate_limit", accept=JSON_ACCEPT).mapping()
        except requests.RequestException:
            return None
        core = body.get("resources", {}).get("core", {})
        reset = core.get("reset") if isinstance(core, Mapping) else None
        return float(reset) if isinstance(reset, int | float) else None


def team_handles(codeowners: str, organization: str) -> list[str]:
    """List every team CODEOWNERS names, sorted, with the surveyed organisation's prefix dropped.

    Owners are read from the whole file rather than only the `*` line: HMCTS repositories routinely
    give a default owner for everything and then name the same team again per directory, and a
    repository with no `*` line still has owners worth reporting. A handle from another
    organisation keeps its prefix, because there it is the part that matters.
    """
    handles = {
        slug if owner.casefold() == organization.casefold() else f"{owner}/{slug}"
        for line in codeowners.splitlines()
        # Comments are cut first: a handle someone left in a note is not an owner.
        for owner, slug in TEAM_HANDLE_PATTERN.findall(line.split("#", 1)[0])
    }
    return sorted(handles)


def read_owning_teams(reader: GitHubReader, organization: str, repository: str) -> Owners:
    """Read CODEOWNERS, keeping a file nobody was allowed to read apart from a repository with none.

    Both conventional locations are tried, in the order GitHub itself resolves them. A 404 means
    the file is not there; any other refusal means this token was not allowed to look, and
    reporting the two the same way would claim a repository names no owner when in fact nobody
    asked it — the conflation the `gate` field also refuses to make.
    """
    refused: int | None = None
    for path in CODEOWNERS_PATHS:
        response = reader.get(f"/repos/{organization}/{repository}/contents/{path}", accept=RAW_ACCEPT)
        if response.status == HTTPStatus.OK:
            return Owners(team_handles(response.text, organization), "found")
        if response.status != HTTPStatus.NOT_FOUND:
            refused = response.status
    return Owners(None, "absent" if refused is None else describe_status(refused))


def ruleset_rule_types(rules: Sequence[Any]) -> list[str]:
    """Name each distinct rule type in an effective ruleset."""
    return sorted({str(rule["type"]) for rule in rules if isinstance(rule, Mapping) and "type" in rule})


def classic_rule_types(protection: Mapping[str, Any]) -> list[str]:
    """Name the classic protections in force, under the names rulesets give the same gates.

    Only the settings with an exact ruleset counterpart are mapped, so that one `active_rules`
    field can be read across both kinds of gate. A classic setting with no equivalent is left out
    rather than invented.
    """
    enabled = {
        "pull_request": protection.get("required_pull_request_reviews") is not None,
        "required_status_checks": protection.get("required_status_checks") is not None,
        "deletion": _enabled(protection, "allow_deletions") is False,
        "non_fast_forward": _enabled(protection, "allow_force_pushes") is False,
        "required_linear_history": _enabled(protection, "required_linear_history") is True,
    }
    return sorted(name for name, present in enabled.items() if present)


def _enabled(protection: Mapping[str, Any], key: str) -> bool | None:
    """Read one classic `{"enabled": bool}` setting, or None when the response omitted it."""
    setting = protection.get(key)
    value = setting.get("enabled") if isinstance(setting, Mapping) else None
    return value if isinstance(value, bool) else None


def read_merge_gate(reader: GitHubReader, organization: str, repository: Repository) -> MergeGate:
    """Classify a repository's default-branch gate, asking rulesets first as `collect_merge_gate` does."""
    if not repository.default_branch:
        # An empty repository has no default branch, so there is no gate to ask about at all.
        return MergeGate(Gate.UNPROTECTED, protected=False, active_rules=[])
    branch = quote(repository.default_branch, safe="")
    rules = reader.get(
        f"/repos/{organization}/{repository.name}/rules/branches/{branch}",
        parameters={"per_page": PAGE_SIZE},
    )
    error = None if rules.status == HTTPStatus.OK else f"rules {describe_status(rules.status)}"
    payload = rules.items() if rules.status == HTTPStatus.OK else []
    if payload:
        return MergeGate(Gate.RULESET, protected=True, active_rules=ruleset_rule_types(payload))
    return read_classic_gate(reader, organization, repository.name, branch, error)


def read_classic_gate(
    reader: GitHubReader,
    organization: str,
    repository: str,
    branch: str,
    error: str | None,
) -> MergeGate:
    """Ask classic protection, falling back to the on/off indicator when its detail is refused."""
    protection = reader.get(f"/repos/{organization}/{repository}/branches/{branch}/protection")
    if protection.status == HTTPStatus.OK:
        rules = classic_rule_types(protection.mapping())
        return MergeGate(Gate.CLASSIC_FULL, protected=True, active_rules=rules, error=error)
    if protection.status == HTTPStatus.NOT_FOUND:
        # GitHub answered: this branch has no protection. An observation, not a blind spot.
        return MergeGate(Gate.UNPROTECTED, protected=False, active_rules=[], error=error)
    if protection.status != HTTPStatus.FORBIDDEN:
        failure = f"protection {describe_status(protection.status)}"
        return MergeGate(Gate.ERROR, protected=None, active_rules=None, error=failure)
    # Detail refused. The branch endpoint still discloses on/off, and that flag is the only thing
    # this token will ever learn about this repository's gate.
    metadata = reader.get(f"/repos/{organization}/{repository}/branches/{branch}")
    if metadata.status != HTTPStatus.OK:
        return MergeGate(
            Gate.ERROR,
            protected=None,
            active_rules=None,
            error=f"protection HTTP 403, branch {describe_status(metadata.status)}",
        )
    protected = bool(metadata.mapping().get("protected"))
    return MergeGate(Gate.CLASSIC_FLAG, protected=protected, active_rules=None, error=error)


def access_level(entry: Mapping[str, Any]) -> str:
    """Name the highest role this token holds on a repository, as the listing reported it.

    A listing that carried no `permissions` object at all answers `unknown` rather than `none`: this
    field is the ceiling on everything else the survey asks, so a listing that did not say must not
    be read as one that said no.
    """
    permissions = entry.get("permissions")
    if not isinstance(permissions, Mapping):
        return UNKNOWN_ACCESS
    return next((level for level in ACCESS_LEVELS if permissions.get(level)), NO_ACCESS)


def list_repositories(reader: GitHubReader, options: Options) -> tuple[list[Repository], int]:
    """Page the organisation listing, in full unless a limit was given, counting what it skipped.

    The whole list is read before anything is surveyed so that progress can be reported against a
    known total.
    """
    repositories: list[Repository] = []
    skipped = 0
    page = 1
    while options.limit is None or len(repositories) < options.limit:
        response = reader.get(
            f"/orgs/{options.organization}/repos",
            parameters={"per_page": PAGE_SIZE, "page": page, "sort": options.sort, "direction": "desc"},
        )
        if response.status != HTTPStatus.OK:
            failure = f"could not list repositories for {options.organization} ({describe_status(response.status)})"
            sys.exit(f"{failure}: {response.message() or response.text}".rstrip(": "))
        payload = response.items()
        if not payload:
            break
        for entry in payload:
            if options.limit is not None and len(repositories) >= options.limit:
                break
            repository = Repository(
                name=str(entry["name"]),
                default_branch=str(entry.get("default_branch") or ""),
                archived=bool(entry.get("archived")),
                access=access_level(entry),
            )
            if repository.archived and options.skip_archived:
                # Counted before it is dropped: a population reported as smaller than it is would
                # misstate the very proportions this survey exists to produce.
                skipped += 1
                continue
            repositories.append(repository)
        progress(f"  {len(repositories)} repositories listed")
        page += 1
    return repositories, skipped


def is_answered(entry: Mapping[str, Any]) -> bool:
    """Say whether a recorded row is an answer, or only the trace of a run that was cut short.

    `classic-flag` IS an answer — it is what "no permission" looks like, and re-asking it would cost
    three calls to learn the same thing. `error`, and anything `unreachable`, are not: they are what
    a rate limit or a dropped connection looks like, and reusing them would turn this run's
    interruption into next run's evidence.
    """
    if entry.get("gate") == Gate.ERROR.value:
        return False
    recorded = f"{entry.get('error') or ''} {entry.get('codeowners') or ''}"
    return UNREACHABLE not in recorded


def is_stdout(path: Path) -> bool:
    """Detect `--continue x > x`, where the shell emptied x before this script was even started."""
    try:
        target = path.stat()
        output = os.fstat(sys.stdout.fileno())
    except OSError:
        return False
    return (target.st_dev, target.st_ino) == (output.st_dev, output.st_ino)


def load_cache(path: Path) -> Cache:
    """Index an earlier survey's output by repository, keeping only the rows that answered."""
    if is_stdout(path):
        # Worth its own message: the generic "not valid JSON" would send someone looking for a
        # missing bracket in a file the shell had already emptied, and a silent empty cache would
        # spend the whole quota again re-asking what that file knew.
        sys.exit(
            f"{path} is also this run's output, so the shell emptied it before the survey started. "
            f"Copy the earlier run's output and --continue from the copy, or redirect somewhere new.",
        )
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except OSError as failure:
        sys.exit(f"could not read {path}: {failure}")
    except ValueError as failure:
        sys.exit(f"{path} is not valid JSON ({failure}) — an interrupted run may need its closing ']'")
    if not isinstance(body, list):
        sys.exit(f"{path} must hold a JSON array of survey records, as this script writes on stdout")
    answers: dict[str, dict[str, Any]] = {}
    discarded = 0
    for entry in body:
        named = isinstance(entry, Mapping) and isinstance(entry.get("repository"), str)
        if named and is_answered(entry):
            answers[str(entry["repository"])] = dict(entry)
        else:
            discarded += 1
    return Cache(answers, discarded)


def record(repository: Repository, gate: MergeGate, owners: Owners) -> dict[str, Any]:
    """Build one repository's JSON object."""
    return {
        "repository": repository.name,
        "default_branch": repository.default_branch or None,
        "archived": repository.archived,
        "access": repository.access,
        "gate": gate.gate.value,
        "protected": gate.protected,
        "active_rules": gate.active_rules,
        "owning_teams": owners.teams,
        "codeowners": owners.codeowners,
        "error": gate.error,
    }


def survey(
    reader: GitHubReader,
    options: Options,
    repositories: Sequence[Repository],
    cache: Cache,
) -> Iterator[dict[str, Any]]:
    """Yield one record per repository, in listing order, asking only about the unanswered ones.

    A cached row is reprinted as the earlier run wrote it, with ONE field refreshed: `access` is
    always this run's observation. `--continue` skips the per-repository calls but never the
    organisation listing, and the listing is what carries `access`, so refreshing it costs nothing
    and is what lets a survey written before the field existed gain it without re-asking anything.

    `archived` and `default_branch` come from that same listing and are NOT refreshed, because they
    are the inputs the cached gate was read against: a repository that has since changed its default
    branch would otherwise report today's branch beside a gate observed on yesterday's. `access` is
    not an input to anything cached — it is an independent fact about this token — so it carries no
    such tie to the run that wrote the rest of the row.
    """
    for index, repository in enumerate(repositories, start=1):
        position = f"  [{index}/{len(repositories)}] {repository.name}"
        cached = cache.get(repository.name)
        if cached is not None:
            progress(f"{position} (cached)")
            yield {**cached, "access": repository.access}
            continue
        progress(position)
        gate = read_merge_gate(reader, options.organization, repository)
        owners = read_owning_teams(reader, options.organization, repository.name)
        yield record(repository, gate, owners)


def write_survey(records: Iterator[dict[str, Any]]) -> tuple[Counter[str], bool]:
    """Write the JSON array to stdout as it is surveyed, counting each gate and reporting any stop.

    Written incrementally rather than assembled and dumped at the end: an all-repos run takes hours,
    and one stopped at hour two must leave behind everything it had learned by then. A rate limit
    that will not clear ends the array here rather than propagating, so the file stays valid JSON
    and can be handed straight back through `--continue`.
    """
    counts: Counter[str] = Counter()
    interrupted = False
    sys.stdout.write("[")
    try:
        for position, entry in enumerate(records):
            # The separator goes before each record but the first, so no path out of this loop can
            # leave a trailing comma.
            sys.stdout.write(f"{',' if position else ''}\n  {json.dumps(entry)}")
            sys.stdout.flush()
            counts[str(entry["gate"])] += 1
    except RateLimitError as limit:
        progress(f"rate limit did not clear: {limit}")
        interrupted = True
    sys.stdout.write("\n]\n")
    sys.stdout.flush()
    return counts, interrupted


def write_summary(counts: Counter[str], reader: GitHubReader, cache: Cache) -> None:
    """Write the population breakdown, including the zeroes, which are themselves evidence."""
    descriptions = (
        (Gate.RULESET, "rules readable with ordinary access"),
        (Gate.CLASSIC_FULL, "classic protection, detail readable (admin access)"),
        (Gate.CLASSIC_FLAG, "classic protection, on/off indicator only (no admin access)"),
        (Gate.UNPROTECTED, "GitHub reported no protection"),
        (Gate.ERROR, "could not be classified"),
    )
    progress(f"\nof {counts.total()} repositories reported:")
    for gate, description in descriptions:
        progress(f"  {gate.value:<14}{counts[gate.value]:>5}  {description}")
    if cache.hits:
        progress(f"  {cache.hits} reused from an earlier run, and not asked about again")
    if cache.unused():
        progress(f"  {cache.unused()} unused: cached answers for repositories outside this population")
    if reader.rate_pauses:
        progress(f"  rate-limit pauses: {reader.rate_pauses}")
    progress("\nclassic-flag is the population this tool must assess as cannot_assess for want of permission.")


def resolve_token() -> str:
    """Find a token in the environment, falling back to whatever `gh` is logged in as."""
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        token = os.environ.get(name)
        if token:
            return token
    executable = shutil.which("gh")
    if executable is not None:
        # Not a shell, and the path comes from `which` rather than from anything user-supplied.
        completed = subprocess.run(  # noqa: S603
            [executable, "auth", "token"],
            capture_output=True,
            text=True,
            check=False,
            timeout=SUBPROCESS_TIMEOUT,
        )
        if completed.returncode == 0 and completed.stdout.strip():
            return completed.stdout.strip()
    sys.exit("no token: set GH_TOKEN, or log in with 'gh auth login'")


def positive_integer(value: str) -> int:
    """Parse the optional repository count, which must be a whole number above zero."""
    if not value.isdigit() or int(value) == 0:
        message = f"must be a whole number greater than zero, got: {value}"
        raise argparse.ArgumentTypeError(message)
    return int(value)


def parse_arguments(argv: Sequence[str] | None) -> Options:
    """Read the command line."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "limit",
        nargs="?",
        type=positive_integer,
        default=None,
        metavar="n",
        help="survey only the n most recent repositories; omit to survey every repository",
    )
    parser.add_argument("--org", dest="organization", default="hmcts", help="organisation to survey")
    parser.add_argument("--sort", default="pushed", choices=SORT_FIELDS, help="listing order")
    parser.add_argument(
        "--skip-archived",
        action="store_true",
        help="leave archived repositories out entirely; they cannot receive a merge",
    )
    parser.add_argument(
        "--continue",
        dest="resume",
        type=Path,
        metavar="PATH",
        help="an earlier run's output; every repository it answered is reprinted without an API call",
    )
    parsed = parser.parse_args(argv)
    return Options(
        organization=parsed.organization,
        sort=parsed.sort,
        limit=parsed.limit,
        skip_archived=parsed.skip_archived,
        resume=parsed.resume,
    )


def main(argv: Sequence[str] | None = None) -> None:
    """Survey the organisation and write the result to stdout as a JSON array."""
    options = parse_arguments(argv)
    reader = GitHubReader(os.environ.get("GITHUB_API_URL", DEFAULT_API_URL), resolve_token())
    cache = load_cache(options.resume) if options.resume is not None else Cache()
    if options.resume is not None:
        progress(f"continuing    {len(cache.answers)} answers reused from {options.resume}")
        if cache.discarded:
            progress(f"              {cache.discarded} recorded no answer there, and will be asked again")

    progress(f"listing repositories in {options.organization}")
    repositories, skipped = list_repositories(reader, options)
    if not repositories:
        sys.exit(f"no repositories found in {options.organization}")
    total = len(repositories)
    scope = f"first {total}" if options.limit is not None else f"all {total} repositories"
    progress(f"population    {scope}, by {options.sort}, most recent first")
    if skipped:
        progress(f"              {skipped} archived repositories skipped")

    counts, interrupted = write_survey(survey(reader, options, repositories, cache))
    write_summary(counts, reader, cache)
    if interrupted:
        remaining = total - counts.total()
        sys.exit(
            f"\nSTOPPED {remaining} repositories short of the population, out of API quota. "
            f"The {counts.total()} records written above are complete and valid JSON: rerun with "
            f"--continue pointing at them once the quota resets, and only the remaining "
            f"{remaining} will be asked about.",
        )


if __name__ == "__main__":
    main()
