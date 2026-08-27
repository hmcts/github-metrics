#!/usr/bin/env python3
"""Audit the three ways a repository could be matched to its SonarCloud project.

Emits a JSON array on stdout, one object per repository. Everything a human reads — progress, the
summary, the list of disagreements — goes to stderr, so stdout can be redirected straight into a
file and queried with jq.

    uv run scripts/audit_sonar_project_mapping.py [n] [--org hmcts] [--sonar-org hmcts]
                                                  [--repositories repos.json] [--skip-properties]
                                                  [--skip-archived] [--continue audit.json]

WHY THIS EXISTS. Quality-gate metrics can only be collected for a repository once its SonarCloud
project is known, and there is no field on either side that states the pairing. Three candidate
answers were proposed, and this script measures all three against each other over a whole
organisation rather than arguing about them:

    1  the repository declares it — `sonar.projectKey` in a root sonar-project.properties
    2  the organisation's project listing is read and mapped back onto repositories
    3  the key is predicted from the repository name

A fourth turned up while the other three were being measured, and `--probe-bindings` measures it
alongside them: a SonarCloud project that has analysed a pull request stores that pull request's own
URL, which names the GitHub repository outright. `hmcts.cath` says `github.com/hmcts/cath-service`
and settles the case no name rule can. It is one anonymous call per project and it is the only
answer here that neither guesses nor depends on a convention being followed — but it is bounded by
retention, not permission, so a project with no pull-request analysis left in its window says
nothing at all. It is reported as found, not as proposed, and its numbers are there to be compared
with the three.

None of the three is assumed to be the truth. Where 1 and 3 both answer, the summary reports how
often they AGREE, because that proportion is the whole decision: a name rule that disagrees with the
repository's own declaration is a wrong answer that looks like a right one. hmcts/cath-service
declares `hmcts.cath`, which no rule here would predict from `cath-service` — and worse, would
happily award to a repository named `cath` if one existed.

WHAT IS AND IS NOT READ FOR OPTION 1. The root sonar-project.properties, and nothing else, because
that is the option as proposed. A Gradle or Maven repository normally sets its key in build.gradle
or pom.xml instead, and a GitHub Actions workflow can pass it on the scanner command line; those
repositories report `absent` here, which is a true statement about the file and NOT a statement that
the repository has no key. The summary quantifies the shortfall directly: an unattributed project key
of the form `group:artifact` is the fingerprint of a build-file declaration, so that count is the
size of the population option 1 would need extending to reach.

VISIBILITY, AND WHY A TOKEN CHANGES THE NUMBERS. The project listing is read anonymously unless
SONAR_TOKEN (or SONARCLOUD_TOKEN) is set, and an anonymous read of SonarCloud returns PUBLIC
projects only. So a declared key that is not in the listing means one of: never analysed, private,
or in another SonarCloud organisation — three different things, and this script says which it can
rule out (`sonar.organization`, where the file names one) rather than reporting the key as missing.
The summary reports how many listed projects are public and how many private, which is how a reader
tells an anonymous run from an authenticated one.

COST. One GitHub call per repository for the properties file, plus the organisation listing, so a
full hmcts run is under 2,000 calls against a 5,000/hour limit — it fits in one quota, unlike the
merge-gate survey. The SonarCloud side is one call per 500 projects and is not rate-limited in any
way that matters. Both `--repositories` and `--skip-properties` exist to spend even less: pass an
earlier merge-gate survey (or any JSON array of repository names) to skip the listing, and
`--skip-properties` audits options 2 and 3 alone with no GitHub calls at all.

ONE OBJECT PER REPOSITORY, AND WHAT EACH FIELD MEANS:
    repository              name within the GitHub organisation
    archived                as the listing reported it, or null when the population came from a file
                            that did not say
    properties              found | absent | not-read | HTTP <status> | unreachable — the status of
                            the root sonar-project.properties read, so a file nobody was allowed to
                            read, and one nobody looked for, are never reported as a repository that
                            has none
    declared_key            `sonar.projectKey` from that file, or null where the file was absent,
                            unreadable, or present but silent on the key. The last of the three is a
                            real case — a properties file can configure sources and exclusions and
                            leave the key to the build — and it is why `properties` and
                            `declared_key` are separate fields
    declared_organization   `sonar.organization` from the same file, or null. Reported because a key
                            belonging to another SonarCloud organisation is not visible here and
                            must not be counted as never analysed
    declared_key_visible    whether `declared_key` names a project in the listing; null when nothing
                            was declared
    name_rule               the strongest rule that matched (see THE RULE LADDER), or null when none
                            did or when more than one project matched at that strength
    name_key                the project key that rule matched, or null. Null with a non-null
                            `name_rule` never happens; null with `name_candidates` set means the
                            prediction was ambiguous, which is a failure of option 3 and not a match
    name_candidates         every key that matched at the strongest matching rule, present only when
                            there was more than one — the prediction that cannot be made
    name_keys               every key matched by ANY rule, sorted. Usually one, or none. More than
                            one is worth seeing: it is normally SonarCloud holding duplicate projects
                            for one repository, and it is what makes the reverse direction (option 2)
                            answerable for keys the winning rule did not claim
    bound_keys              every project whose pull-request analyses name THIS repository, sorted;
                            [] where none did and null where `--probe-bindings` was not given, which
                            are again two different facts. More than one is a duplicate project pair
                            that only a human can choose between
    resolution              which of options 1 and 3 answered, and whether they agreed — the field
                            the audit is for. See `Resolution`

THE RULE LADDER for option 3, strongest first. Each is tried in turn and the first to match wins, so
a repository with an exactly-named project is never awarded a loosely-matched one:
    exact             the key IS the repository name                       civil-service
    artifact          the key is `group:artifact` and the artifact is the   uk.gov.hmcts.reform:darts-gateway
                      repository name                                      → darts-gateway
    org-prefix        the key is the repository name behind an             hmcts_ccd-case-ui-toolkit
                      organisation prefix (`hmcts.`, `hmcts_`, `hmcts-`)   → ccd-case-ui-toolkit
    case              one of the above, ignoring case                      Caveat → caveat
    separators        one of the above, ignoring case AND separators       IACASEAPI → ia-case-api
    duplicate-suffix  one of the above after dropping a trailing `-2`      rpx-xui-webapp_2
                      or `_2` duplicate marker                            → rpx-xui-webapp
The summary reports what each rule adds on its own, because the ladder is a proposal: the weakest
rungs are where a wrong answer is most likely, and their incremental value is what says whether they
are worth the risk of one.

--continue REUSES FILE READS, NOTHING ELSE. The named file is an earlier run's output. A row whose
`properties` is an answer (`found` or `absent`) contributes its file read and costs no call; a row
that records a refusal or a dropped connection is asked again, so a rate limit is never preserved as
though it were this organisation's blind spot. Nothing else is reused: the project listing is read
fresh every run and every name match is recomputed, both being free, so a resumed run never reports
yesterday's SonarCloud against today's repositories.
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
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from http import HTTPStatus
from pathlib import Path
from typing import Any

import requests

DEFAULT_API_URL = "https://api.github.com"
DEFAULT_SONAR_URL = "https://sonarcloud.io"
API_VERSION = "2022-11-28"
JSON_ACCEPT = "application/vnd.github+json"
RAW_ACCEPT = "application/vnd.github.raw"
PAGE_SIZE = 100
SONAR_PAGE_SIZE = 500
REQUEST_TIMEOUT = 30
SUBPROCESS_TIMEOUT = 30
# A fixed short pause, repeated a few times, rather than a wait until the quota resets: a secondary
# limit clears in seconds, and an exhausted hourly quota does not clear at all within any wait worth
# making. The second case is for `--continue` to solve, not for this script to sit through.
RATE_PAUSE_SECONDS = 60
MAXIMUM_RATE_PAUSES = 3
MAXIMUM_NETWORK_ATTEMPTS = 3
NETWORK_RETRY_SECONDS = 5

# Not a status either API can return, so it cannot be confused with one: it stands for a call that
# never answered at all. `describe_status` is the only thing that renders it.
NETWORK_FAILURE_STATUS = 0
UNREACHABLE = "unreachable"
PROPERTIES_PATH = "sonar-project.properties"
FOUND = "found"
ABSENT = "absent"
# What `--skip-properties` records. Deliberately not `absent`: a file nobody looked for is not a
# file that is not there, and the whole audit turns on which of the two a row means.
NOT_READ = "not-read"
PROJECT_KEY_PROPERTY = "sonar.projectKey"
ORGANIZATION_PROPERTY = "sonar.organization"
RATE_LIMIT_MARKERS = ("rate limit", "secondary rate", "abuse detection")
CONFLICT_EXAMPLES = 20

# The repository a pull-request analysis names, out of the URL SonarCloud stores beside it. This is
# the only place either API states the pairing outright, which is why --probe-bindings exists.
PULL_REQUEST_URL_PATTERN = re.compile(r"https?://[^/]*github\.com/([^/\s]+)/([^/\s]+)/pull/")

# `key=value`, `key:value` or `key value`, as the Java properties format allows. The name cannot
# contain either separator, so splitting at whichever comes first is unambiguous — which matters
# here, because the VALUE routinely contains a colon (`uk.gov.hmcts.reform:darts-gateway`).
PROPERTY_PATTERN = re.compile(r"^([A-Za-z0-9_.\-]+)\s*[=:\s]\s*(.*)$")
PROPERTY_COMMENT_MARKERS = ("#", "!")
# Separators are dropped, not translated, so that IACASEAPI and ia-case-api come out the same.
SEPARATOR_PATTERN = re.compile(r"[-_.:\s]")
# SonarCloud has no rename, so a project analysed under a new key leaves the old one behind and the
# new one carries a counter: rpx-xui-webapp_2, appreg-frontend-2, cpo-case-payment-orders-api-1.
DUPLICATE_SUFFIX_PATTERN = re.compile(r"[-_]\d+$")


class Resolution(StrEnum):
    """Which of the three options answered for a repository, and whether they agreed."""

    AGREE = "agree"
    CONFLICT = "conflict"
    DECLARED_ONLY = "declared-only"
    NAME_ONLY = "name-only"
    AMBIGUOUS = "ambiguous"
    NONE = "none"
    UNREADABLE = "unreadable"
    NOT_COMPARED = "not-compared"


RESOLUTION_DESCRIPTIONS = (
    (Resolution.AGREE, "declared and predicted, and the same project"),
    (Resolution.CONFLICT, "declared and predicted, and DIFFERENT projects"),
    (Resolution.DECLARED_ONLY, "declared only; no rule predicted it"),
    (Resolution.NAME_ONLY, "predicted only; the repository declares no key"),
    (Resolution.AMBIGUOUS, "no declaration, and more than one project fits the name"),
    (Resolution.NONE, "neither option identified a project"),
    (Resolution.UNREADABLE, "the properties file could not be read, so option 1 is unanswered"),
    (Resolution.NOT_COMPARED, "the properties file was not looked for, under --skip-properties"),
)


def as_written(text: str) -> str:
    """Compare a key form to a repository name exactly as both are spelled."""
    return text


def casefolded(text: str) -> str:
    """Compare ignoring case, which SonarCloud keys and repository names differ in freely."""
    return text.casefold()


def squashed(text: str) -> str:
    """Compare ignoring case and separators, so IACASEAPI and ia-case-api are one name."""
    return SEPARATOR_PATTERN.sub("", text).casefold()


@dataclass(frozen=True)
class NameRule:
    """One rung of the ladder: which key forms it considers, and how loosely it compares them."""

    label: str
    description: str
    normalize: Callable[[str], str]


NAME_RULES = (
    NameRule("exact", "the key is the repository name", as_written),
    NameRule("artifact", "the key is group:artifact, artifact is the name", as_written),
    NameRule("org-prefix", "the key is the name behind an organisation prefix", as_written),
    NameRule("case", "one of the above, ignoring case", casefolded),
    NameRule("separators", "one of the above, ignoring case and separators", squashed),
    NameRule("duplicate-suffix", "one of the above, dropping a -2 duplicate marker", squashed),
)


@dataclass(frozen=True)
class Options:
    """What to audit."""

    organization: str
    sonar_organization: str
    sonar_url: str
    limit: int | None
    skip_archived: bool
    skip_properties: bool
    probe_bindings: bool
    population: Path | None
    resume: Path | None


@dataclass(frozen=True)
class Repository:
    """A repository as the population that produced it describes it."""

    name: str
    # Null where the population came from a file that did not record it: an unknown archive state
    # must not be reported as an active repository.
    archived: bool | None


@dataclass(frozen=True)
class Project:
    """A SonarCloud project, as the organisation's listing describes it."""

    key: str
    visibility: str


@dataclass(frozen=True)
class Catalogue:
    """The SonarCloud side of the audit, read once and then only looked things up in."""

    projects: tuple[Project, ...]
    # The rule ladder's index: rule label, then normalised name, then the keys that answer to it.
    index: Mapping[str, Mapping[str, list[str]]]
    # What each project said about its own repository, and the reverse of it — both null where
    # `--probe-bindings` was not given, because nothing was asked.
    bindings: Mapping[str, tuple[str, ...]] | None
    bound: Mapping[str, list[str]] | None

    @property
    def keys(self) -> frozenset[str]:
        """Name every project the listing showed, which is what a declared key is checked against."""
        return frozenset(project.key for project in self.projects)


@dataclass(frozen=True)
class Declaration:
    """What the root sonar-project.properties said, and how the read of it went."""

    status: str
    key: str | None
    organization: str | None


@dataclass(frozen=True)
class Prediction:
    """What the rule ladder made of a repository name."""

    rule: str | None
    key: str | None
    candidates: list[str] | None
    every_key: list[str]


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


class Cache:
    """An earlier run's file reads, indexed by repository, reused rather than fetched again."""

    def __init__(self, reads: Mapping[str, Declaration] | None = None, discarded: int = 0) -> None:
        """Hold the reusable reads, and the count of rows that recorded no answer at all."""
        self.reads = dict(reads or {})
        self.discarded = discarded
        self.hits = 0

    def get(self, repository: str) -> Declaration | None:
        """Return the file read already recorded for a repository, counting the call it saves."""
        declaration = self.reads.get(repository)
        if declaration is not None:
            self.hits += 1
        return declaration

    def unused(self) -> int:
        """Count reads never asked for, because the population no longer holds their repository."""
        return len(self.reads) - self.hits


class RateLimitError(RuntimeError):
    """The quota did not clear within the pauses this audit is willing to take."""


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


class Reader:
    """Reads one JSON API, turning a dropped connection into a status and a rate limit into a stop.

    A call that never answers becomes a pseudo-status rather than an exception: one unreachable
    repository should be one unreadable row, not the end of a run. A rate limit is the opposite case
    and raises, because it will apply to every remaining repository just as it applied to this one,
    and rows written under it would record this run's exhaustion as the organisation's blind spot.
    """

    def __init__(self, base_url: str, headers: Mapping[str, str]) -> None:
        """Prepare a session carrying whatever this API needs on every call."""
        self.base_url = base_url.rstrip("/")
        self.rate_pauses = 0
        self.session = requests.Session()
        self.session.headers.update(dict(headers))

    def get(self, path: str, *, accept: str = JSON_ACCEPT, parameters: Mapping[str, Any] | None = None) -> Response:
        """Fetch one path, retrying a transient network failure and briefly pausing a rate limit."""
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
            self.rate_pauses += 1
            progress(f"rate limited: waiting {RATE_PAUSE_SECONDS}s (pause {pauses} of {MAXIMUM_RATE_PAUSES})")
            time.sleep(RATE_PAUSE_SECONDS)

    def _request(self, path: str, *, accept: str, parameters: Mapping[str, Any] | None = None) -> Response:
        """Make one request, keeping the status with the body it belongs to."""
        response = self.session.get(
            f"{self.base_url}{path}",
            headers={"Accept": accept},
            params=parameters,
            timeout=REQUEST_TIMEOUT,
        )
        return Response(response.status_code, response.text)


def github_reader(token: str) -> Reader:
    """Prepare a reader for the GitHub API, with the API version pinned."""
    return Reader(
        os.environ.get("GITHUB_API_URL", DEFAULT_API_URL),
        {"Authorization": f"Bearer {token}", "X-GitHub-Api-Version": API_VERSION},
    )


def sonar_reader(base_url: str) -> Reader:
    """Prepare a reader for SonarCloud, authenticated only if a token happens to be set.

    No token is required: the listing this audit needs is readable anonymously for public projects,
    which is most of an HMCTS organisation. A token widens the listing to private projects, and the
    summary reports the split so that a reader can tell which kind of run produced the numbers.
    """
    headers = {}
    for name in ("SONAR_TOKEN", "SONARCLOUD_TOKEN"):
        token = os.environ.get(name)
        if token:
            headers["Authorization"] = f"Bearer {token}"
            break
    return Reader(base_url, headers)


def list_projects(reader: Reader, organization: str) -> list[Project]:
    """Page the SonarCloud project listing in full — option 2, and the yardstick for 1 and 3."""
    projects: list[Project] = []
    page = 1
    while True:
        response = reader.get(
            "/api/components/search_projects",
            parameters={"organization": organization, "ps": SONAR_PAGE_SIZE, "p": page},
        )
        if response.status != HTTPStatus.OK:
            hint = " (set SONAR_TOKEN if the organisation is not publicly readable)"
            failure = f"could not list SonarCloud projects for {organization} ({describe_status(response.status)})"
            sys.exit(f"{failure}{hint}: {response.text[:200]}")
        body = response.mapping()
        components = body.get("components")
        if not isinstance(components, list) or not components:
            return projects
        projects.extend(
            Project(key=str(entry["key"]), visibility=str(entry.get("visibility") or "unknown"))
            for entry in components
            if isinstance(entry, Mapping) and entry.get("key")
        )
        total = body.get("paging", {}).get("total") if isinstance(body.get("paging"), Mapping) else None
        progress(f"  {len(projects)} projects listed")
        if isinstance(total, int) and len(projects) >= total:
            return projects
        page += 1


def read_binding(reader: Reader, key: str) -> tuple[str, ...]:
    """Name the repositories a project's analysed pull requests belong to, where any of them say.

    SonarCloud stores the pull request's own URL beside each pull-request analysis, and that URL
    names the GitHub repository outright — the one place either side states the pairing rather than
    implying it. It is readable anonymously. Its limit is retention and not permission: pull-request
    analyses are purged after a month or so, so a project whose repository has been quiet answers
    nothing, and a project analysed only on its main branch never answers at all. Empty therefore
    means "did not say", never "no repository".
    """
    response = reader.get("/api/project_pull_requests/list", parameters={"project": key})
    if response.status != HTTPStatus.OK:
        return ()
    pull_requests = response.mapping().get("pullRequests")
    if not isinstance(pull_requests, list):
        return ()
    matches = (
        PULL_REQUEST_URL_PATTERN.match(str(entry.get("url", "")))
        for entry in pull_requests
        if isinstance(entry, Mapping)
    )
    return tuple(sorted({f"{match.group(1)}/{match.group(2)}" for match in matches if match is not None}))


def read_bindings(reader: Reader, projects: Sequence[Project]) -> Mapping[str, tuple[str, ...]]:
    """Ask every project which repository it analyses — one call each, and no token needed."""
    bindings: dict[str, tuple[str, ...]] = {}
    for position, project in enumerate(projects, start=1):
        bindings[project.key] = read_binding(reader, project.key)
        progress(f"  [{position}/{len(projects)}] {project.key} -> {', '.join(bindings[project.key]) or 'did not say'}")
    return bindings


def bound_projects(bindings: Mapping[str, tuple[str, ...]], organization: str) -> Mapping[str, list[str]]:
    """Index the bindings by repository, keeping the ones that named exactly one repository here.

    A project whose analyses name two different repositories has not identified one, so it is
    counted in the summary and left out of this index rather than attributed to both.
    """
    index: dict[str, list[str]] = {}
    prefix = f"{organization}/".casefold()
    for key, names in sorted(bindings.items()):
        if len(names) == 1 and names[0].casefold().startswith(prefix):
            index.setdefault(names[0].split("/", 1)[1], []).append(key)
    return index


def organization_prefixes(organization: str) -> tuple[str, ...]:
    """Name the prefixes an organisation puts in front of a repository name to make a key."""
    return tuple(f"{organization}{separator}" for separator in (".", "_", "-"))


def strip_prefix(text: str, prefixes: Sequence[str]) -> str:
    """Drop an organisation prefix, case-insensitively, leaving anything else untouched."""
    lowered = text.casefold()
    for prefix in prefixes:
        if lowered.startswith(prefix.casefold()):
            return text[len(prefix) :]
    return text


def key_forms(key: str, prefixes: Sequence[str]) -> Mapping[str, frozenset[str]]:
    """Group every identity a project key can be recognised by under the rule that produces it.

    A form is only offered to a rule when that rule's transformation actually did something, so
    `artifact` never claims a key with no group and `duplicate-suffix` never claims a key with no
    counter. That is what keeps the summary's per-rule counts meaningful: each one is what that rule
    contributed, not what a stronger rule would have found anyway.
    """
    artifact = {key.rsplit(":", 1)[1]} if ":" in key else set()
    structural = {key, *artifact}
    deprefixed = {stripped for form in structural if (stripped := strip_prefix(form, prefixes)) != form}
    every = frozenset(structural | deprefixed)
    deduplicated = {DUPLICATE_SUFFIX_PATTERN.sub("", form) for form in every} - every
    return {
        "exact": frozenset({key}),
        "artifact": frozenset(artifact),
        "org-prefix": frozenset(deprefixed),
        "case": every,
        "separators": every,
        "duplicate-suffix": frozenset(deduplicated),
    }


def build_index(projects: Iterable[Project], prefixes: Sequence[str]) -> Mapping[str, Mapping[str, list[str]]]:
    """Index the project keys by rule, so a repository name is looked up rather than searched for.

    Every rule keeps a list and not a single key: two projects that collide under one rule are an
    ambiguity that option 3 cannot resolve, and it has to be reported rather than decided by
    whichever happened to be indexed last.
    """
    index: dict[str, dict[str, list[str]]] = {rule.label: {} for rule in NAME_RULES}
    for project in projects:
        forms = key_forms(project.key, prefixes)
        for rule in NAME_RULES:
            bucket = index[rule.label]
            for form in forms[rule.label]:
                keys = bucket.setdefault(rule.normalize(form), [])
                if project.key not in keys:
                    keys.append(project.key)
    return index


def predict(repository: str, index: Mapping[str, Mapping[str, list[str]]]) -> Prediction:
    """Run the ladder over a repository name, keeping both the winner and everything it matched.

    The first rule to match decides, so a repository whose name IS a project key is never awarded a
    loosely-matched project instead. `every_key` collects the matches of all six rules regardless,
    because the reverse question — which listed projects can be attributed to some repository at all
    — is answered across the whole ladder and not by the winner alone.
    """
    rule: str | None = None
    matched: list[str] = []
    every: list[str] = []
    for candidate in NAME_RULES:
        keys = index[candidate.label].get(candidate.normalize(repository), [])
        every.extend(key for key in keys if key not in every)
        if keys and rule is None:
            rule, matched = candidate.label, keys
    unambiguous = len(matched) == 1
    return Prediction(
        rule=rule if unambiguous else None,
        key=matched[0] if unambiguous else None,
        candidates=sorted(matched) if len(matched) > 1 else None,
        every_key=sorted(every),
    )


def parse_properties(text: str) -> Mapping[str, str]:
    """Read a Java properties file well enough to find the two properties this audit asks about.

    Later definitions win, as the format specifies. Comments are recognised only at the start of a
    line, again as the format specifies: a `#` inside a value is part of the value.
    """
    properties: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(PROPERTY_COMMENT_MARKERS):
            continue
        matched = PROPERTY_PATTERN.match(stripped)
        if matched is not None:
            properties[matched.group(1)] = matched.group(2).strip()
    return properties


def read_declaration(reader: Reader, organization: str, repository: str) -> Declaration:
    """Read the root sonar-project.properties, keeping a refused read apart from an absent file.

    A 404 means the file is not there. Any other refusal means the read was not permitted, and
    reporting the two the same way would claim a repository declares no key when in fact nobody
    managed to ask it.
    """
    response = reader.get(f"/repos/{organization}/{repository}/contents/{PROPERTIES_PATH}", accept=RAW_ACCEPT)
    if response.status == HTTPStatus.NOT_FOUND:
        return Declaration(ABSENT, None, None)
    if response.status != HTTPStatus.OK:
        return Declaration(describe_status(response.status), None, None)
    properties = parse_properties(response.text)
    return Declaration(
        FOUND,
        properties.get(PROJECT_KEY_PROPERTY) or None,
        properties.get(ORGANIZATION_PROPERTY) or None,
    )


def resolve(declaration: Declaration, prediction: Prediction) -> Resolution:
    """Say which options answered for one repository, and whether they agreed.

    An unreadable file outranks every other verdict: with option 1 unanswered there is no agreement
    or disagreement to report, and calling it `name-only` would quietly promote a prediction to an
    unchallenged answer. The prediction is still recorded in its own fields — it is only the verdict
    on the pair that is withheld.
    """
    if declaration.status == NOT_READ:
        return Resolution.NOT_COMPARED
    if declaration.status not in {FOUND, ABSENT}:
        return Resolution.UNREADABLE
    if declaration.key is not None:
        if prediction.key is None:
            return Resolution.DECLARED_ONLY
        return Resolution.AGREE if prediction.key == declaration.key else Resolution.CONFLICT
    if prediction.key is not None:
        return Resolution.NAME_ONLY
    return Resolution.AMBIGUOUS if prediction.candidates else Resolution.NONE


def record(
    repository: Repository,
    declaration: Declaration,
    prediction: Prediction,
    catalogue: Catalogue,
) -> dict[str, Any]:
    """Build one repository's JSON object."""
    bound = catalogue.bound
    return {
        "repository": repository.name,
        "archived": repository.archived,
        "properties": declaration.status,
        "declared_key": declaration.key,
        "declared_organization": declaration.organization,
        "declared_key_visible": None if declaration.key is None else declaration.key in catalogue.keys,
        "name_rule": prediction.rule,
        "name_key": prediction.key,
        "name_candidates": prediction.candidates,
        "name_keys": prediction.every_key,
        # Null, not [], when the bindings were not probed: nothing asked is not the same answer as
        # nothing found, and only one of the two is evidence that a repository has no project.
        "bound_keys": None if bound is None else bound.get(repository.name, []),
        "resolution": resolve(declaration, prediction).value,
    }


def list_repositories(reader: Reader, options: Options) -> tuple[list[Repository], int]:
    """Page the GitHub organisation listing, in full unless a limit was given, counting what it skips.

    The whole list is read before anything is audited so that progress can be reported against a
    known total.
    """
    repositories: list[Repository] = []
    skipped = 0
    page = 1
    while options.limit is None or len(repositories) < options.limit:
        response = reader.get(
            f"/orgs/{options.organization}/repos",
            parameters={"per_page": PAGE_SIZE, "page": page, "sort": "pushed", "direction": "desc"},
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
            archived = bool(entry.get("archived"))
            if archived and options.skip_archived:
                # Counted before it is dropped: a population reported as smaller than it is would
                # misstate the very proportions this audit exists to produce.
                skipped += 1
                continue
            repositories.append(Repository(name=str(entry["name"]), archived=archived))
        progress(f"  {len(repositories)} repositories listed")
        page += 1
    return repositories, skipped


def named_repository(entry: object) -> Repository | None:
    """Read one entry of a population file, which may be a survey row or just a name."""
    if isinstance(entry, str):
        return Repository(entry, None)
    if isinstance(entry, Mapping):
        name = entry.get("repository") or entry.get("name")
        if isinstance(name, str):
            archived = entry.get("archived")
            return Repository(name, archived if isinstance(archived, bool) else None)
    return None


def load_population(path: Path, options: Options) -> tuple[list[Repository], int]:
    """Read the population from a file, so the GitHub listing need not be paged again.

    Any JSON array whose entries carry a repository name will do, which includes the merge-gate
    survey's output and a bare array of names. `--skip-archived` applies only to entries that say
    whether they are archived; one that does not say is kept, because dropping it would silently
    shrink the population on the strength of a missing field.
    """
    body = read_records(path)
    repositories: list[Repository] = []
    skipped = 0
    unnamed = 0
    for entry in body:
        repository = named_repository(entry)
        if repository is None:
            unnamed += 1
        elif repository.archived and options.skip_archived:
            skipped += 1
        elif options.limit is None or len(repositories) < options.limit:
            repositories.append(repository)
    if unnamed:
        progress(f"  {unnamed} entries in {path} named no repository and were ignored")
    return repositories, skipped


def is_stdout(path: Path) -> bool:
    """Detect `--continue x > x`, where the shell emptied x before this script was even started."""
    try:
        target = path.stat()
        output = os.fstat(sys.stdout.fileno())
    except OSError:
        return False
    return (target.st_dev, target.st_ino) == (output.st_dev, output.st_ino)


def read_records(path: Path) -> list[Any]:
    """Read a JSON array of records from a file, failing with what is actually wrong with it."""
    if is_stdout(path):
        # Worth its own message: the generic "not valid JSON" would send someone looking for a
        # missing bracket in a file the shell had already emptied.
        sys.exit(
            f"{path} is also this run's output, so the shell emptied it before the audit started. "
            f"Copy the earlier run's output and read from the copy, or redirect somewhere new.",
        )
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except OSError as failure:
        sys.exit(f"could not read {path}: {failure}")
    except ValueError as failure:
        sys.exit(f"{path} is not valid JSON ({failure}) — an interrupted run may need its closing ']'")
    if not isinstance(body, list):
        sys.exit(f"{path} must hold a JSON array of records, as this script writes on stdout")
    return body


def load_cache(path: Path) -> Cache:
    """Index an earlier run's file reads by repository, keeping only the rows that answered.

    `found` and `absent` are answers about the file and are reused. Everything else — a refusal, a
    dropped connection — is dropped and asked again, because reusing it would turn the last run's
    interruption into this run's evidence.
    """
    reads: dict[str, Declaration] = {}
    discarded = 0
    for entry in read_records(path):
        status = entry.get("properties") if isinstance(entry, Mapping) else None
        name = entry.get("repository") if isinstance(entry, Mapping) else None
        if isinstance(entry, Mapping) and isinstance(name, str) and status in {FOUND, ABSENT}:
            reads[name] = Declaration(
                str(status),
                as_text(entry.get("declared_key")),
                as_text(entry.get("declared_organization")),
            )
        else:
            discarded += 1
    return Cache(reads, discarded)


def as_text(value: object) -> str | None:
    """Read an optional string out of parsed JSON, treating anything else as absent."""
    return value if isinstance(value, str) and value else None


def audit(
    reader: Reader | None,
    options: Options,
    repositories: Sequence[Repository],
    catalogue: Catalogue,
    cache: Cache,
) -> Iterator[dict[str, Any]]:
    """Yield one record per repository, reading the properties file only where it has to."""
    for position, repository in enumerate(repositories, start=1):
        cached = cache.get(repository.name)
        if cached is not None:
            declaration = cached
            note = " (cached)"
        elif reader is None:
            declaration = Declaration(NOT_READ, None, None)
            note = ""
        else:
            declaration = read_declaration(reader, options.organization, repository.name)
            note = ""
        progress(f"  [{position}/{len(repositories)}] {repository.name}{note}")
        yield record(repository, declaration, predict(repository.name, catalogue.index), catalogue)


def write_records(records: Iterator[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    """Write the JSON array to stdout as it is audited, keeping the records for the summary.

    Written incrementally rather than assembled and dumped at the end so that a run stopped by a
    rate limit leaves behind everything it had learned, as valid JSON, for `--continue` to resume
    from.
    """
    written: list[dict[str, Any]] = []
    interrupted = False
    sys.stdout.write("[")
    try:
        for entry in records:
            # The separator goes before each record but the first, so no path out of this loop can
            # leave a trailing comma.
            sys.stdout.write(f"{',' if written else ''}\n  {json.dumps(entry)}")
            sys.stdout.flush()
            written.append(entry)
    except RateLimitError as limit:
        progress(f"rate limit did not clear: {limit}")
        interrupted = True
    sys.stdout.write("\n]\n")
    sys.stdout.flush()
    return written, interrupted


def properties_bucket(entry: Mapping[str, Any]) -> str:
    """Sort one row into the five states option 1 can be in, which are five different facts."""
    status = entry["properties"]
    if status == FOUND:
        return "key" if entry["declared_key"] else "no-key"
    return str(status) if status in {ABSENT, NOT_READ} else "unreadable"


def write_declaration_summary(records: Sequence[Mapping[str, Any]], sonar_organization: str) -> None:
    """Report what option 1 answered, and what became of the keys it found."""
    buckets = Counter(properties_bucket(entry) for entry in records)
    progress("\noption 1 — the repository declares its own key")
    for bucket, description in (
        ("key", f"a root {PROPERTIES_PATH} declaring {PROJECT_KEY_PROPERTY}"),
        ("no-key", f"a root {PROPERTIES_PATH}, but it declares no key"),
        (ABSENT, f"no root {PROPERTIES_PATH} (the key may still be set in a build file)"),
        ("unreadable", "the file could not be read — permission or network, NOT absence"),
        (NOT_READ, "not looked for at all, under --skip-properties"),
    ):
        progress(f"  {bucket:<12}{buckets[bucket]:>5}  {description}")
    declared = [entry for entry in records if entry["declared_key"]]
    visible = sum(1 for entry in declared if entry["declared_key_visible"])
    elsewhere = sum(
        1
        for entry in declared
        if entry["declared_organization"] and entry["declared_organization"] != sonar_organization
    )
    progress(f"  of the {len(declared)} keys declared, {visible} name a project in the listing")
    progress(
        f"  the other {len(declared) - visible} were never analysed, are private, or belong to another "
        f"organisation — {elsewhere} name an organisation other than {sonar_organization}",
    )


def write_listing_summary(records: Sequence[Mapping[str, Any]], projects: Sequence[Project]) -> None:
    """Report what option 2 can attribute, which is the reverse of the question the rows answer."""
    keys = {project.key for project in projects}
    by_declaration = {entry["declared_key"] for entry in records if entry["declared_key"]} & keys
    by_name = {key for entry in records for key in entry["name_keys"]}
    attributed = by_declaration | by_name
    unattributed = sorted(keys - attributed)
    grouped = [key for key in unattributed if ":" in key]
    claims = Counter(key for entry in records for key in entry["name_keys"] if key in keys)
    contested = [key for key, count in claims.items() if count > 1]
    progress("\noption 2 — the organisation's project listing, mapped back onto repositories")
    progress(f"  {len(attributed):>5} of {len(keys)} listed projects can be attributed to a repository")
    progress(f"  {len(by_declaration):>5} by a repository's own declaration")
    progress(f"  {len(by_name):>5} by a name rule (the two overlap where they agree)")
    progress(f"  {len(unattributed):>5} could not be attributed at all")
    progress(
        f"  {len(grouped):>5} of those are group:artifact keys, which is what a build-file "
        f"declaration looks like — the population option 1 would have to read build files to reach",
    )
    progress(f"  {len(contested):>5} projects are claimed by more than one repository by name")


def write_prediction_summary(records: Sequence[Mapping[str, Any]]) -> None:
    """Report what option 3 answered, rule by rule, so each rung's own value is visible."""
    rules = Counter(entry["name_rule"] for entry in records if entry["name_rule"])
    progress("\noption 3 — the key predicted from the repository name")
    for rule in NAME_RULES:
        progress(f"  {rule.label:<18}{rules[rule.label]:>5}  {rule.description}")
    ambiguous = sum(1 for entry in records if entry["name_candidates"])
    unresolved = sum(1 for entry in records if not entry["name_rule"] and not entry["name_candidates"])
    progress(f"  {'resolved':<18}{rules.total():>5}  exactly one project matched")
    progress(f"  {'ambiguous':<18}{ambiguous:>5}  more than one project matched at the same strength")
    progress(f"  {'unmatched':<18}{unresolved:>5}  no rule matched anything")
    # A separate count from `ambiguous`, and a separate problem: the ladder does pick one of these,
    # but SonarCloud holds two projects for the repository and only one of them carries the current
    # quality gate. Whichever is read, the other is the one that should have been.
    duplicated = sum(1 for entry in records if len(entry["name_keys"]) > 1)
    progress(f"  {'duplicated':<18}{duplicated:>5}  matched at more than one strength: two projects, one repository")


def sole(keys: object) -> str | None:
    """Read a one-item key list as the single answer it is, and anything else as no answer."""
    return keys[0] if isinstance(keys, list) and len(keys) == 1 else None


def write_binding_summary(
    records: Sequence[Mapping[str, Any]],
    bindings: Mapping[str, tuple[str, ...]],
    organization: str,
) -> None:
    """Report the fourth option nobody proposed: the project naming its own repository."""
    said = {key: names for key, names in bindings.items() if names}
    contested = [key for key, names in said.items() if len(names) > 1]
    elsewhere = [
        key
        for key, names in said.items()
        if len(names) == 1 and not names[0].casefold().startswith(f"{organization}/".casefold())
    ]
    bound = [entry for entry in records if entry["bound_keys"]]
    duplicated = [entry for entry in bound if len(entry["bound_keys"]) > 1]
    progress("\noption 4 — the project names its own repository (found while auditing, not proposed)")
    progress(f"  {len(said):>5} of {len(bindings)} projects name a repository in a pull-request URL")
    progress(f"  {len(bindings) - len(said):>5} said nothing: no pull-request analysis is still retained")
    progress(f"  {len(elsewhere):>5} name a repository outside {organization}")
    progress(f"  {len(contested):>5} name more than one repository, so they identify none")
    progress(f"  {len(bound):>5} repositories in the population are named by at least one project")
    progress(f"  {len(duplicated):>5} of those are named by more than one, which is a duplicate to resolve by hand")
    for option, field in (("1 (declared)", "declared_key"), ("3 (predicted)", "name_key")):
        comparable = [entry for entry in bound if sole(entry["bound_keys"]) and entry[field]]
        wrong = [entry for entry in comparable if sole(entry["bound_keys"]) != entry[field]]
        progress(
            f"  against option {option}: {len(comparable) - len(wrong)} of {len(comparable)} agree, "
            f"{len(wrong)} disagree",
        )


def write_agreement_summary(records: Sequence[Mapping[str, Any]]) -> None:
    """Report where options 1 and 3 both answered — the comparison the audit exists to make."""
    resolutions = Counter(entry["resolution"] for entry in records)
    progress(f"\nof {len(records)} repositories:")
    for resolution, description in RESOLUTION_DESCRIPTIONS:
        progress(f"  {resolution.value:<16}{resolutions[resolution.value]:>5}  {description}")
    agree = resolutions[Resolution.AGREE.value]
    conflict = resolutions[Resolution.CONFLICT.value]
    checkable = agree + conflict
    if not checkable:
        progress("\nno repository both declared a key and matched one by name, so the two cannot be compared.")
        return
    progress(
        f"\nwhere both answered ({checkable} repositories), the name rules were wrong for {conflict} "
        f"of them — {conflict * 100 / checkable:.1f}%. That is the error rate of relying on option 3 "
        f"alone, measured only against repositories that state their own key.",
    )
    conflicts = [entry for entry in records if entry["resolution"] == Resolution.CONFLICT.value]
    progress(f"\nthe first {min(len(conflicts), CONFLICT_EXAMPLES)} disagreements, declared against predicted:")
    for entry in conflicts[:CONFLICT_EXAMPLES]:
        progress(f"  {entry['repository']:<44}{entry['declared_key']}  !=  {entry['name_key']} ({entry['name_rule']})")
    if len(conflicts) > CONFLICT_EXAMPLES:
        progress(f"  and {len(conflicts) - CONFLICT_EXAMPLES} more; the JSON on stdout holds them all")


def write_summary(
    records: Sequence[Mapping[str, Any]],
    catalogue: Catalogue,
    options: Options,
    cache: Cache,
) -> None:
    """Write the whole audit: the population, then each option, then how the options compare."""
    visibility = Counter(project.visibility for project in catalogue.projects)
    progress(
        f"\nSonarCloud {options.sonar_organization}: {len(catalogue.projects)} projects visible "
        f"({visibility['public']} public, {visibility['private']} private)",
    )
    write_declaration_summary(records, options.sonar_organization)
    write_listing_summary(records, catalogue.projects)
    write_prediction_summary(records)
    if catalogue.bindings is not None:
        write_binding_summary(records, catalogue.bindings, options.organization)
    write_agreement_summary(records)
    if cache.hits:
        progress(f"\n{cache.hits} file reads reused from an earlier run; {cache.unused()} of its rows went unused")


def resolve_token() -> str:
    """Find a GitHub token in the environment, falling back to whatever `gh` is logged in as."""
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
    sys.exit("no token: set GH_TOKEN, or log in with 'gh auth login', or pass --repositories --skip-properties")


def positive_integer(value: str) -> int:
    """Parse the optional repository count, which must be a whole number above zero."""
    if not value.isdigit() or int(value) == 0:
        message = f"must be a whole number greater than zero, got: {value}"
        raise argparse.ArgumentTypeError(message)
    return int(value)


HELP_EPILOG = r"""\
auditing every repository, which needs a GitHub token for the properties files:

  uv run scripts/audit_sonar_project_mapping.py > audit.json

auditing options 2, 3 and 4 only, with no GitHub calls and no token at all, over the population an
earlier merge-gate survey already listed:

  uv run scripts/audit_sonar_project_mapping.py --repositories repos.json --skip-properties \
      --probe-bindings > audit.json

the mapping option 4 is confident about, which is the one worth keeping:

  jq -r '.[] | select(.bound_keys | length == 1)
             | "\(.repository)\t\(.bound_keys[0])"' audit.json

querying the result — the repositories whose own declaration contradicts the name rules:

  jq -r '.[] | select(.resolution == "conflict")
             | "\(.repository)\t\(.declared_key)\t\(.name_key)"' audit.json

the repositories with no identifiable project at all, which is the work this audit sizes:

  jq -r '.[] | select(.resolution == "none") | .repository' audit.json

and the keys a repository declares that SonarCloud does not list, which are analyses that never
ran, private projects, or another organisation's:

  jq -r '.[] | select(.declared_key_visible == false)
             | "\(.repository)\t\(.declared_key)"' audit.json
"""


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
        help="audit only the n most recently pushed repositories; omit to audit every repository",
    )
    parser.add_argument("--org", dest="organization", default="hmcts", help="GitHub organisation to audit")
    parser.add_argument(
        "--sonar-org",
        dest="sonar_organization",
        default="hmcts",
        help="SonarCloud organisation whose projects the repositories are matched against",
    )
    parser.add_argument("--sonar-url", default=DEFAULT_SONAR_URL, help="SonarCloud base URL")
    parser.add_argument(
        "--repositories",
        dest="population",
        type=Path,
        metavar="PATH",
        help="take the population from this JSON array instead of paging the GitHub listing",
    )
    parser.add_argument(
        "--skip-properties",
        action="store_true",
        help="do not read any sonar-project.properties; audits options 2 and 3 with no repository calls",
    )
    parser.add_argument(
        "--skip-archived",
        action="store_true",
        help="leave archived repositories out entirely",
    )
    parser.add_argument(
        "--probe-bindings",
        action="store_true",
        help="ask every project which repository it analyses; one SonarCloud call per project, no token",
    )
    parser.add_argument(
        "--continue",
        dest="resume",
        type=Path,
        metavar="PATH",
        help="an earlier run's output; every properties file it read is reused without a call",
    )
    parsed = parser.parse_args(argv)
    return Options(
        organization=parsed.organization,
        sonar_organization=parsed.sonar_organization,
        sonar_url=parsed.sonar_url,
        limit=parsed.limit,
        skip_archived=parsed.skip_archived,
        skip_properties=parsed.skip_properties,
        probe_bindings=parsed.probe_bindings,
        population=parsed.population,
        resume=parsed.resume,
    )


def population(options: Options, cache: Cache) -> tuple[list[Repository], Reader | None]:
    """Settle the population and whether a GitHub reader is needed to complete it.

    A token is resolved only where a call actually has to be made, so the run that needs no calls —
    a population from a file, with `--skip-properties` — needs no token either.
    """
    if options.population is not None:
        repositories, skipped = load_population(options.population, options)
        source = f"{len(repositories)} repositories from {options.population}"
    else:
        progress(f"listing repositories in {options.organization}")
        repositories, skipped = list_repositories(github_reader(resolve_token()), options)
        source = f"{len(repositories)} repositories in {options.organization}"
    if not repositories:
        sys.exit("no repositories in the population")
    progress(f"population    {source}, {'first ' + str(options.limit) if options.limit else 'all'}")
    if skipped:
        progress(f"              {skipped} archived repositories skipped")
    if options.skip_properties:
        progress(f"              --skip-properties: no {PROPERTIES_PATH} will be read")
        return repositories, None
    if all(repository.name in cache.reads for repository in repositories):
        # Every file read is already known, so no token is needed to complete the run — worth saying,
        # because the alternative is exiting for want of a token that would never have been used.
        progress("              every properties file is already in the --continue file")
        return repositories, None
    return repositories, github_reader(resolve_token())


def main(argv: Sequence[str] | None = None) -> None:
    """Audit the mapping options against each other and write one record per repository to stdout."""
    options = parse_arguments(argv)
    cache = load_cache(options.resume) if options.resume is not None else Cache()
    if options.resume is not None:
        progress(f"continuing    {len(cache.reads)} file reads reused from {options.resume}")
        if cache.discarded:
            progress(f"              {cache.discarded} of its rows recorded no answer, and will be read again")

    progress(f"listing SonarCloud projects in {options.sonar_organization}")
    projects = list_projects(sonar_reader(options.sonar_url), options.sonar_organization)
    if not projects:
        sys.exit(f"no projects listed for SonarCloud organisation {options.sonar_organization}")
    bindings = None
    if options.probe_bindings:
        progress(f"asking each of the {len(projects)} projects which repository it analyses")
        bindings = read_bindings(sonar_reader(options.sonar_url), projects)
    catalogue = Catalogue(
        projects=tuple(projects),
        index=build_index(projects, organization_prefixes(options.sonar_organization)),
        bindings=bindings,
        bound=None if bindings is None else bound_projects(bindings, options.organization),
    )

    repositories, reader = population(options, cache)
    records, interrupted = write_records(audit(reader, options, repositories, catalogue, cache))
    write_summary(records, catalogue, options, cache)
    if interrupted:
        remaining = len(repositories) - len(records)
        sys.exit(
            f"\nSTOPPED {remaining} repositories short of the population, out of API quota. The "
            f"{len(records)} records written above are complete and valid JSON: rerun with --continue "
            f"pointing at a COPY of them once the quota resets, and only the remaining {remaining} "
            f"will be read.",
        )


if __name__ == "__main__":
    main()
