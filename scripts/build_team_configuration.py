#!/usr/bin/env python3
"""Write the `teams:` section of a configuration file covering EVERY repository in an organisation.

    uv run scripts/build_team_configuration.py --cache .metrics/teams-cache.json > hmcts.yml

WHAT IT IS FOR, AND HOW IT DIFFERS FROM survey_to_teams.py. That script configures the population
the tool can assess, selected by merge gate, and leaves out anything whose owner it could not read.
This one has the opposite priority: completeness. Every non-archived repository in the organisation
appears exactly once, and a repository whose team could not be established is placed under
`unknown` rather than left out. Team attribution is best effort and is never a reason to omit a
repository — which is why this script asks nothing about merge gates and needs no admin access.

WHERE THE TEAMS COME FROM, strongest evidence first. Each repository is decided by the first rule
below that answers, and the rule that decided it is counted in the report on stderr:

  teams-api-admin   exactly one GitHub team holds `admin` on the repository. A single team
                    administering a repository is the closest thing to a declared owner the API has.
  codeowners-sole   CODEOWNERS names exactly one team, so there is nothing to choose between.
  teams-api-write   several teams hold write-or-better. The most permissive wins, and a tie goes to
                    the team holding the FEWEST repositories: where an organisation-wide platform
                    team and a service team both hold `push` on that service, the specific claim is
                    the informative one. `pull` is never ownership and is ignored throughout.
  codeowners-first  CODEOWNERS names several teams; the one owning fewest repositories wins, then
                    alphabetical order, which is the tie-break survey_to_teams.py also applies.
  name-prefix       nothing claimed the repository, but its name shares a prefix with repositories
                    the rules above agreed on — `sscs-`, `ia-`, `opal-` and so on. The prefix index
                    is LEARNED from the decisions in this run and never hardcoded, and a prefix must
                    reach `--prefix-support` repositories and `--prefix-dominance` agreement before
                    it may speak. Inference never feeds itself: the index is built once, from
                    evidence only, so an inferred repository can never widen a prefix. Nor may a team
                    the organisation did not list attribute anything this way — see below.
  unknown           no rule answered. A normal outcome, and not a failure.

Precedence is deliberate about one thing in particular: a sole `admin` team outranks CODEOWNERS,
but a sole CODEOWNERS team outranks any contested API claim. Access describes who CAN merge and
CODEOWNERS describes who is EXPECTED to review, and neither is ownership; where they disagree the
less ambiguous of the two is the better guess.

TWO KINDS OF HANDLE ARE NOT OWNERS, and they are rejected for different reasons. `--exclude-team`
rejects by identity: `all-org-members` is the whole organisation wearing a team's clothes, and
attributing a repository to it says only that the repository is in the organisation, which this file
already says by listing it. No threshold can fix that, so it is named — and rejected in CODEOWNERS
as well as in team access, since a handle that is not a team is not a team wherever it is written.
`--maximum-team-share` rejects by proportion: a team holding write across the estate holds it
administratively, and being the only claim on much of that estate it would otherwise absorb every
repository no service team happened to claim, displacing an honest `unknown` with a confident wrong
answer. That one spares CODEOWNERS, where naming a team in one repository is a per-repository act.
`--skip-teams` is the blunt version of both — if team access turns out to say less about ownership at
HMCTS than CODEOWNERS does, it can be dropped entirely without discarding the cache.

NO TEAM HERE IS INVENTED. Every slug emitted was named as an owner by the teams API or by a
CODEOWNERS file; the name-prefix rule chooses among teams already attributed and never coins one
from a repository name. API slugs are real by construction, but a CODEOWNERS handle is only as good
as the file it was typed into, so where the team list was read every emitted slug is checked against
it and the ones missing are named in the header and on stderr. Those teams may still GROUP the
repositories that name them — that is faithful to what the file says — but they may not attribute
anything by name, because spreading a possibly-dead label across a name family is worse than
`unknown`. Absence from the list is reported rather than acted on further: a token is not shown
every team, and a secret one would look identical to a deleted one.

WHAT IS NOT LEFT TO JUDGEMENT. Every repository appears under exactly one team, every identifier is
unique, and no team is emitted empty — all three are things `metrics.config` rejects a file for. The
generated YAML is read back and validated against `TeamConfiguration` before this script exits.

COST, AND HOW TO AVOID PAYING IT TWICE. Covering a whole organisation costs one call per hundred
repositories, one per hundred teams, one per hundred repositories per team, and up to two per
repository for CODEOWNERS. `--cache` writes every fact fetched and reuses it next run, so re-deciding
the grouping — a different `--prefix-support`, say — costs nothing. `--survey`, given the JSON from
survey_merge_gate_access.py, takes CODEOWNERS from there instead of fetching it, which is most of
those calls. With either one, `--offline` decides the whole file with no network at all.

USER-RUN ONLY, like its siblings: the loop that maintains plan.md never holds a GitHub token
(architecture.md, "Access"). Requires a token in GH_TOKEN or GITHUB_TOKEN, or `gh` logged in, unless
`--offline`. Listing teams needs a token permitted to see them (`read:org`); when that is refused the
run continues on CODEOWNERS and names alone and says so, because a partial attribution covering
every repository is exactly what this script is for.
"""

import argparse
import json
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from http import HTTPStatus
from pathlib import Path
from typing import Any, Final

from survey_merge_gate_access import (
    ACCESS_LEVELS,
    CODEOWNERS_PATHS,
    DEFAULT_API_URL,
    PAGE_SIZE,
    RAW_ACCEPT,
    GitHubReader,
    access_level,
    describe_status,
    progress,
    resolve_token,
    team_handles,
)
from survey_to_teams import Team, display_name_for, identifier_for, render, validate

UNKNOWN_IDENTIFIER: Final = "unknown"
UNKNOWN_DISPLAY_NAME: Final = "Unknown (team not established)"

# Ownership is a write-or-better relationship. Read access only says a team may look, which platform
# and security teams hold across a whole organisation, and counting it would drown every specific
# claim in the same handful of org-wide handles.
OWNING_ACCESS_LEVELS: Final = ("admin", "maintain", "push")

# How many leading hyphen-separated segments of a repository name may stand as a prefix. Three
# reaches `api-cp-crime` without reaching whole names, which match nothing but themselves.
MAXIMUM_PREFIX_SEGMENTS: Final = 3
# A one- or two-character prefix is an accident of naming rather than a family.
MINIMUM_PREFIX_LENGTH: Final = 3
DEFAULT_PREFIX_SUPPORT: Final = 3
DEFAULT_PREFIX_DOMINANCE: Final = 0.8
# A quarter of an organisation is far past any team that could be said to own what it holds. On an
# estate of 1,850 repositories that is over 460, which no service team reaches.
DEFAULT_MAXIMUM_TEAM_SHARE: Final = 0.25

# Handles that are not teams in the sense this file means. `all-org-members` is every member of the
# organisation, so attributing a repository to it says only that the repository is in the
# organisation — which the file already says by listing it. It is excluded by identity rather than
# by size because it would still be the wrong answer if it held two repositories: no threshold makes
# an org-wide membership group into an owner. `--exclude-team` adds to this list; nothing is removed
# from it automatically, because a name this specific is not guessed at.
DEFAULT_EXCLUDED_TEAMS: Final = frozenset({"all-org-members"})

EVIDENCE_RULES: Final = ("teams-api-admin", "codeowners-sole", "teams-api-write", "codeowners-first")
# Every rule in precedence order, so the report reads as the order it describes.
RULE_ORDER: Final = (*EVIDENCE_RULES, "name-prefix", UNKNOWN_IDENTIFIER)


@dataclass(frozen=True)
class Options:
    """What to cover, how far to guess, and where to write it."""

    organization: str
    cache: Path | None
    survey: Path | None
    offline: bool
    skip_teams: bool
    prefix_support: int
    prefix_dominance: float
    maximum_team_share: float
    excluded_teams: frozenset[str]
    include_header: bool


@dataclass(frozen=True)
class Facts:
    """What the API and the survey said, before anything is decided. This is what `--cache` holds.

    `teams_read` is kept separate from an empty `team_repositories` so that a token which was not
    permitted to list teams is never reported as an organisation whose teams claim nothing.

    `known_teams` holds EVERY team the organisation listed, not only those holding write somewhere:
    it is the authority against which a handle read out of a CODEOWNERS file can be checked, and a
    team that administers nothing still exists.
    """

    organization: str
    repositories: tuple[str, ...]
    archived: int
    teams_read: bool
    known_teams: frozenset[str]
    team_repositories: Mapping[str, Mapping[str, str]]
    codeowners: Mapping[str, tuple[str, ...]]
    unreadable_codeowners: Mapping[str, str]


@dataclass(frozen=True)
class TeamAccess:
    """The organisation's teams: which exist, and which repositories each holds write-or-better on."""

    read: bool
    known: frozenset[str]
    repositories: Mapping[str, Mapping[str, str]]


@dataclass(frozen=True)
class Survey:
    """A merge-gate survey read for the two things this script wants from it.

    The population is carried as well as the CODEOWNERS answers so that a survey alone is enough to
    decide a whole file offline — a survey of the organisation already listed it.
    """

    repositories: tuple[str, ...]
    codeowners: Mapping[str, tuple[str, ...]]
    unreadable: Mapping[str, str]


@dataclass(frozen=True)
class Evidence:
    """The facts reduced to what the rules actually consult, with the sizes they tie-break on."""

    claims: Mapping[str, Mapping[str, str]]
    team_sizes: Mapping[str, int]
    codeowners: Mapping[str, tuple[str, ...]]
    codeowner_sizes: Mapping[str, int]
    broad_teams: frozenset[str]


@dataclass(frozen=True)
class Decision:
    """One repository, the team it was attributed to, and the rule that attributed it."""

    repository: str
    team: str
    rule: str


class FetchError(RuntimeError):
    """A listing the API refused, named by the status it refused with."""


def fetch_pages(reader: GitHubReader, path: str, parameters: Mapping[str, Any] | None = None) -> list[Any]:
    """Read every page of a listing, refusing to treat a refusal as the end of the results.

    A listing that stopped early would understate a population silently, which is the one failure
    this script cannot afford: the whole point is that every repository is present.
    """
    entries: list[Any] = []
    page = 1
    while True:
        response = reader.get(path, parameters={**(parameters or {}), "per_page": PAGE_SIZE, "page": page})
        if response.status != HTTPStatus.OK:
            message = f"{describe_status(response.status)}: {response.message() or path}"
            raise FetchError(message)
        payload = response.items()
        if not payload:
            return entries
        entries.extend(payload)
        page += 1


def list_repositories(reader: GitHubReader, organization: str) -> tuple[tuple[str, ...], int]:
    """List the organisation's non-archived repositories, and count the archived ones left out."""
    entries = fetch_pages(reader, f"/orgs/{organization}/repos", {"type": "all"})
    names = tuple(sorted(str(entry["name"]) for entry in entries if not entry.get("archived")))
    progress(f"  {len(names)} non-archived repositories, {len(entries) - len(names)} archived")
    return names, len(entries) - len(names)


def read_team_repositories(reader: GitHubReader, organization: str) -> TeamAccess:
    """Map each GitHub team to the repositories it holds write-or-better on, and its access there.

    A refusal to list the teams at all is reported and returns an unread `TeamAccess`, because
    CODEOWNERS and names can still attribute most of an organisation and a file covering every
    repository is worth more than an exit. A refusal on ONE team's repositories is reported and that
    team simply claims nothing, for the same reason — but it stays in `known`, because a team whose
    repositories nobody was allowed to list is a team that exists.
    """
    try:
        teams = fetch_pages(reader, f"/orgs/{organization}/teams")
    except FetchError as failure:
        progress(f"  could not list teams ({failure}); continuing on CODEOWNERS and names alone")
        return TeamAccess(read=False, known=frozenset(), repositories={})
    progress(f"  {len(teams)} teams listed")
    known = frozenset(canonical(str(team["slug"])) for team in teams)
    claimed: dict[str, dict[str, str]] = {}
    for index, team in enumerate(teams, start=1):
        slug = str(team["slug"])
        try:
            entries = fetch_pages(reader, f"/orgs/{organization}/teams/{slug}/repos")
        except FetchError as failure:
            progress(f"  team {slug}: {failure}")
            continue
        owned = {
            str(entry["name"]): access_level(entry) for entry in entries if access_level(entry) in OWNING_ACCESS_LEVELS
        }
        if owned:
            claimed[slug] = owned
        if index % PAGE_SIZE == 0:
            progress(f"  {index} of {len(teams)} teams read")
    return TeamAccess(read=True, known=known, repositories=claimed)


def read_codeowner_teams(reader: GitHubReader, organization: str, repository: str) -> tuple[str, ...] | str:
    """Read a repository's CODEOWNERS teams, or return the status that refused to let us look.

    Both conventional paths are tried, in the order GitHub resolves them. A 404 at both is a
    repository with no CODEOWNERS, which is an answer of none; any other refusal is not an answer at
    all, and is kept apart so the report cannot pass it off as one.
    """
    refused: int | None = None
    for path in CODEOWNERS_PATHS:
        response = reader.get(f"/repos/{organization}/{repository}/contents/{path}", accept=RAW_ACCEPT)
        if response.status == HTTPStatus.OK:
            return tuple(team_handles(response.text, organization))
        if response.status != HTTPStatus.NOT_FOUND:
            refused = response.status
    return () if refused is None else describe_status(refused)


def load_survey(path: Path) -> Survey:
    """Take the population and the CODEOWNERS answers from a merge-gate survey.

    The survey distinguishes a repository with no CODEOWNERS from one whose CODEOWNERS nobody was
    allowed to read, and that distinction is carried through rather than flattened. Archived rows are
    dropped here, so a survey taken without `--skip-archived` is as usable as one taken with it.
    """
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except OSError as failure:
        sys.exit(f"could not read {path}: {failure}")
    except ValueError as failure:
        sys.exit(f"{path} is not valid JSON ({failure}) — an interrupted survey may need its closing ']'")
    if not isinstance(body, list):
        sys.exit(f"{path} must hold a JSON array of survey records, as survey_merge_gate_access.py writes")
    owners: dict[str, tuple[str, ...]] = {}
    unreadable: dict[str, str] = {}
    for row in body:
        if not isinstance(row, Mapping) or not isinstance(row.get("repository"), str) or row.get("archived") is True:
            continue
        repository, status = str(row["repository"]), str(row.get("codeowners"))
        if status == "found":
            named = row.get("owning_teams")
            owners[repository] = tuple(str(owner) for owner in named) if isinstance(named, list) else ()
        elif status == "absent":
            owners[repository] = ()
        else:
            unreadable[repository] = status
    progress(f"  {len(owners)} CODEOWNERS answers from {path}, {len(unreadable)} unreadable")
    return Survey(tuple(sorted(owners | unreadable)), owners, unreadable)


def population_of(
    reader: GitHubReader | None,
    options: Options,
    cached: Facts | None,
    survey: Survey | None,
) -> tuple[tuple[str, ...], int]:
    """Establish the repositories to cover, preferring a listing taken now over a remembered one.

    Offline, an earlier cache and a survey are both acceptable sources, in that order: the cache was
    written by this script against the whole organisation, where a survey may have been limited.
    """
    if reader is not None:
        progress("listing repositories")
        return list_repositories(reader, options.organization)
    if cached is not None and cached.repositories:
        return cached.repositories, cached.archived
    if survey is not None and survey.repositories:
        progress(f"  population taken from the survey: {len(survey.repositories)} non-archived repositories")
        return survey.repositories, 0
    sys.exit("--offline needs a --cache or a --survey to read the repository list from")


def gather(reader: GitHubReader | None, options: Options, cached: Facts | None, survey: Survey | None) -> Facts:
    """Assemble the facts, fetching only what neither the cache nor the survey already answered."""
    repositories, archived = population_of(reader, options, cached, survey)
    teams = gather_team_claims(reader, options, cached)
    codeowners, unreadable = gather_codeowners(reader, options, cached, survey, repositories)
    return Facts(
        organization=options.organization,
        repositories=repositories,
        archived=archived,
        teams_read=teams.read,
        known_teams=teams.known,
        team_repositories=teams.repositories,
        codeowners=codeowners,
        unreadable_codeowners=unreadable,
    )


def gather_team_claims(reader: GitHubReader | None, options: Options, cached: Facts | None) -> TeamAccess:
    """Return the organisation's teams and their claims, from the cache when it holds them.

    `--skip-teams` stops teams being FETCHED, and deliberately does not stop them being read from the
    cache. What this run declines to use, it must not delete: the cache is a record of what the API
    said, and a comparison run that emptied it would silently throw away an hour of somebody's
    quota. `without_team_access` is where the option actually takes effect.
    """
    if cached is not None and cached.teams_read:
        progress(f"  {len(cached.known_teams)} teams from the cache, {len(cached.team_repositories)} holding write")
        return TeamAccess(read=True, known=cached.known_teams, repositories=cached.team_repositories)
    if reader is None or options.skip_teams:
        return TeamAccess(read=False, known=frozenset(), repositories={})
    progress("reading team access")
    return read_team_repositories(reader, options.organization)


def without_team_access(facts: Facts) -> Facts:
    """Drop the teams' claims, keeping the list of which teams exist, for `--skip-teams`.

    The list is kept because it is a different fact from the access: whether a CODEOWNERS handle
    names a real team is still worth checking in a run that is ignoring what teams can reach.
    """
    return replace(facts, team_repositories={})


def gather_codeowners(
    reader: GitHubReader | None,
    options: Options,
    cached: Facts | None,
    survey: Survey | None,
    repositories: Sequence[str],
) -> tuple[Mapping[str, tuple[str, ...]], Mapping[str, str]]:
    """Return a CODEOWNERS answer per repository, fetching only the ones nothing else answered."""
    owners: dict[str, tuple[str, ...]] = {}
    unreadable: dict[str, str] = {}
    if cached is not None:
        owners.update(cached.codeowners)
        unreadable.update(cached.unreadable_codeowners)
    if survey is not None:
        owners.update(survey.codeowners)
        unreadable.update({name: status for name, status in survey.unreadable.items() if name not in owners})
    outstanding = [name for name in repositories if name not in owners]
    if not outstanding or reader is None:
        if outstanding:
            progress(f"  {len(outstanding)} repositories have no CODEOWNERS answer and none can be fetched")
        return owners, unreadable
    progress(f"reading CODEOWNERS for {len(outstanding)} repositories")
    for index, repository in enumerate(outstanding, start=1):
        answer = read_codeowner_teams(reader, options.organization, repository)
        if isinstance(answer, str):
            unreadable[repository] = answer
        else:
            owners[repository] = answer
        if index % PAGE_SIZE == 0:
            progress(f"  {index} of {len(outstanding)} read")
    return owners, unreadable


def codeowner_handles(facts: Facts) -> frozenset[str]:
    """Name every team any CODEOWNERS file mentions, so an exclusion can be reported as having bitten."""
    return frozenset(canonical(slug) for teams in facts.codeowners.values() for slug in teams)


def applied_exclusions(facts: Facts, options: Options) -> tuple[str, ...]:
    """Name the excluded handles this organisation actually has, not every one that was configured.

    Reporting the configured list would say the same thing whatever was surveyed; reporting the ones
    that were really there says which of them changed this file.
    """
    return tuple(sorted(options.excluded_teams & (facts.known_teams | codeowner_handles(facts))))


def canonical(slug: str) -> str:
    """Fold a team handle to the slug GitHub itself resolves it to, which is lower case.

    GitHub team slugs are case-insensitive, and the two sources disagree about case: the teams API
    returns `appreg`, while CODEOWNERS returns whatever the author typed — `@hmcts/AppReg`. Grouped
    as written, one team is emitted twice under two identifiers, each holding part of its estate,
    which is both wrong and hard to see in a file of this size. Folding here rather than at either
    source keeps the cache faithful to what was fetched.
    """
    return slug.casefold()


def more_permissive(existing: str | None, access: str) -> str:
    """Keep the higher of two access levels, for a team that arrived twice under different case."""
    if existing is None:
        return access
    return min(existing, access, key=ACCESS_LEVELS.index)


def evidence_of(facts: Facts, options: Options) -> Evidence:
    """Reduce the facts to per-repository claims and the team sizes the rules tie-break on.

    Read access is discarded HERE and not only where it is fetched, because this is where it would
    do harm: a cache written by an older run, or a fetch filter someone loosens later, must not be
    able to turn permission to look at a repository into a claim to own it.

    Both sizes are counted over THIS population only. A team's weight ought to be how much of the
    organisation it holds now, not how much it once held or holds in archived repositories.

    Two filters keep a team from being read as an owner, and they answer different questions.
    `--exclude-team` is identity: `all-org-members` is not a team that owns things, it is the
    organisation wearing a team's clothes, and no threshold can make it one — so it is named. It is
    applied to CODEOWNERS as well as to access, because a handle that is not a team is not a team
    wherever it is written. `--maximum-team-share` is proportion: a team holding write across the
    estate holds it administratively rather than editorially, and since it is the ONLY claim on much
    of that estate, left in it absorbs every repository no service team happened to claim and a
    confident wrong answer replaces an honest `unknown`. That one is deliberately NOT applied to
    CODEOWNERS, where naming a team in one repository is a per-repository act rather than a blanket
    grant. The tie-break already prefers the specific claim where a repository is contested; both of
    these are for where it is not.
    """
    population = set(facts.repositories)
    owning: dict[str, dict[str, str]] = {}
    for slug, owned in facts.team_repositories.items():
        if canonical(slug) in options.excluded_teams:
            continue
        held = owning.setdefault(canonical(slug), {})
        for repository, access in owned.items():
            if repository in population and access in OWNING_ACCESS_LEVELS:
                held[repository] = more_permissive(held.get(repository), access)
    team_sizes = {slug: len(held) for slug, held in owning.items()}
    ceiling = options.maximum_team_share * len(population)
    broad_teams = frozenset(slug for slug, size in team_sizes.items() if size > ceiling)
    claims: dict[str, dict[str, str]] = {}
    for slug, held in owning.items():
        if slug in broad_teams:
            continue
        for repository, access in held.items():
            claims.setdefault(repository, {})[slug] = access
    # Deduplicated after folding, because a CODEOWNERS file naming both `@hmcts/AppReg` and
    # `@hmcts/appreg` names ONE team twice, and left as two the sole-owner rule would not fire.
    codeowners = {
        name: tuple(
            slug for slug in dict.fromkeys(canonical(slug) for slug in teams) if slug not in options.excluded_teams
        )
        for name, teams in facts.codeowners.items()
        if name in population
    }
    codeowner_sizes = Counter(slug for teams in codeowners.values() for slug in teams)
    return Evidence(
        claims=claims,
        team_sizes=team_sizes,
        codeowners=codeowners,
        codeowner_sizes=codeowner_sizes,
        broad_teams=broad_teams,
    )


def sole_admin(claims: Mapping[str, str]) -> str | None:
    """Name the one team holding admin, or nothing when none or several do."""
    admins = [slug for slug, access in claims.items() if access == "admin"]
    return admins[0] if len(admins) == 1 else None


def most_specific_claim(claims: Mapping[str, str], team_sizes: Mapping[str, int]) -> str:
    """Choose between competing write-or-better claims: most permissive, then smallest team."""
    return min(claims, key=lambda slug: (ACCESS_LEVELS.index(claims[slug]), team_sizes.get(slug, 0), slug))


def most_specific_owner(owners: Sequence[str], codeowner_sizes: Mapping[str, int]) -> str:
    """Choose between the teams CODEOWNERS names: the one owning fewest, then alphabetically."""
    return min(owners, key=lambda slug: (codeowner_sizes.get(slug, 0), slug))


def decide(repository: str, evidence: Evidence) -> Decision | None:
    """Attribute one repository from evidence alone, or return nothing for the name pass to try.

    The rules are in precedence order and the first to answer decides; the module docstring explains
    why that order is what it is.
    """
    claims = evidence.claims.get(repository, {})
    owners = evidence.codeowners.get(repository, ())
    administrator = sole_admin(claims)
    if administrator is not None:
        return Decision(repository, administrator, "teams-api-admin")
    if len(owners) == 1:
        return Decision(repository, owners[0], "codeowners-sole")
    if claims:
        return Decision(repository, most_specific_claim(claims, evidence.team_sizes), "teams-api-write")
    if owners:
        return Decision(repository, most_specific_owner(owners, evidence.codeowner_sizes), "codeowners-first")
    return None


def prefixes_of(repository: str) -> tuple[str, ...]:
    """List a repository name's candidate family prefixes, longest first."""
    segments = repository.split("-")
    limit = min(len(segments), MAXIMUM_PREFIX_SEGMENTS)
    candidates = ("-".join(segments[:count]) for count in range(limit, 0, -1))
    return tuple(prefix for prefix in candidates if len(prefix) >= MINIMUM_PREFIX_LENGTH)


def prefix_index(decisions: Iterable[Decision], known_teams: frozenset[str]) -> dict[str, Counter[str]]:
    """Count which teams each name prefix was attributed to, over evidenced decisions only.

    A team the organisation did not list is kept OUT of the index when there is a list to check
    against. Such a team came from a CODEOWNERS file naming a handle that has since been renamed,
    deleted or mistyped, and while grouping the repository that names it is still faithful to what
    that file says, spreading a label across a whole name family on the strength of it is not:
    attributing three more repositories to a team that may not exist is worse than `unknown`.
    """
    index: dict[str, Counter[str]] = {}
    for decision in decisions:
        if known_teams and decision.team not in known_teams:
            continue
        for prefix in prefixes_of(decision.repository):
            index.setdefault(prefix, Counter())[decision.team] += 1
    return index


def infer_from_name(repository: str, index: Mapping[str, Counter[str]], options: Options) -> str | None:
    """Attribute a repository by name family, taking the longest prefix that agrees well enough."""
    for prefix in prefixes_of(repository):
        counts = index.get(prefix)
        if counts is None:
            continue
        total = sum(counts.values())
        team, agreeing = counts.most_common(1)[0]
        if total >= options.prefix_support and agreeing / total >= options.prefix_dominance:
            return team
    return None


def attribute(facts: Facts, options: Options) -> tuple[Decision, ...]:
    """Attribute every repository in the population, in two passes: evidence, then name families.

    The passes are ordered and not interleaved so that inference reads an index built entirely from
    evidence. Were an inferred repository allowed into the index, one wrong guess could reach the
    support threshold on its own and then attribute a whole family to the team it was wrong about.
    """
    evidence = evidence_of(facts, options)
    excluded = applied_exclusions(facts, options)
    if excluded:
        progress(f"  excluded as not owning teams: {', '.join(excluded)}")
    if evidence.broad_teams:
        progress(
            f"  holding write on over {options.maximum_team_share:.0%} of the estate, so not read as"
            f" owners: {', '.join(sorted(evidence.broad_teams))}",
        )
    decided = {name: decision for name in facts.repositories if (decision := decide(name, evidence)) is not None}
    index = prefix_index(decided.values(), facts.known_teams)
    decisions = []
    for repository in facts.repositories:
        decision = decided.get(repository)
        if decision is None:
            inferred = infer_from_name(repository, index, options)
            rule = "name-prefix" if inferred is not None else UNKNOWN_IDENTIFIER
            decision = Decision(repository, inferred or UNKNOWN_IDENTIFIER, rule)
        decisions.append(decision)
    return tuple(decisions)


def build_teams(decisions: Sequence[Decision]) -> tuple[Team, ...]:
    """Group the decisions into the teams the configuration file describes, `unknown` last.

    `unknown` carries no `github_team_slugs` because it is not a GitHub team: it is the bucket for
    repositories no team could be established for, and naming a slug there would invent one.
    """
    grouped: dict[str, list[str]] = {}
    for decision in decisions:
        grouped.setdefault(decision.team, []).append(decision.repository)
    teams: list[Team] = []
    for slug in sorted(grouped, key=lambda name: (name == UNKNOWN_IDENTIFIER, name)):
        unknown = slug == UNKNOWN_IDENTIFIER
        teams.append(
            Team(
                identifier=identifier_for(slug, [team.identifier for team in teams]),
                display_name=UNKNOWN_DISPLAY_NAME if unknown else display_name_for(slug),
                github_team_slugs=() if unknown else (slug,),
                repositories=tuple(sorted(grouped[slug])),
            ),
        )
    return tuple(teams)


def header(facts: Facts, teams: Sequence[Team], counts: Counter[str], options: Options) -> str:
    """Write the provenance a reader of the generated file needs in order to trust it correctly."""
    attributed = len(facts.repositories) - counts[UNKNOWN_IDENTIFIER]
    lines = [
        f"# Generated by scripts/build_team_configuration.py for the {facts.organization} organisation. Every one",
        f"# of the {len(facts.repositories)} non-archived repositories appears exactly once, across {len(teams)} teams",
        f"# ({facts.archived} archived repositories are deliberately absent, having no merges to assess). Of those,",
        f"# {attributed} were attributed to a team and {counts[UNKNOWN_IDENTIFIER]} could not be, and are under `unknown`:",
        "#",
    ]
    lines += [f"#   {rule:<16} {counts[rule]:>5}" for rule in RULE_ORDER if counts[rule]]
    lines += [
        "#",
        "# ATTRIBUTION IS BEST EFFORT AND SOME OF IT IS WRONG. `teams-api-*` rows read GitHub team ACCESS and",
        "# `codeowners-*` rows read who is expected to REVIEW; neither is a statement of ownership, and where",
        "# several teams claimed a repository the most specific claim was taken. `name-prefix` rows were not",
        "# claimed by anything and were attributed by sharing a name family with repositories that were.",
        "# Correct any of it by hand — the population is what this file guarantees, not the grouping.",
    ]
    excluded = applied_exclusions(facts, options)
    if excluded:
        lines.append(
            f"# Excluded as not owning teams, so no repository is attributed to them: {', '.join(excluded)}.",
        )
    if not facts.teams_read:
        lines.append("# GitHub teams could not be listed on this run, so no row here rests on team access.")
    if facts.unreadable_codeowners:
        lines.append(
            f"# CODEOWNERS was unreadable for {len(facts.unreadable_codeowners)} repositories, which are attributed"
            " by name or `unknown`.",
        )
    unverified = unverified_teams(facts, teams)
    if unverified:
        lines += [
            "#",
            f"# {len(unverified)} of the teams below are NOT in the organisation's team list, so each is a handle a",
            "# CODEOWNERS file names that has since been renamed, deleted or mistyped — or a team this token may",
            "# not see. The repositories under them are grouped by what their CODEOWNERS says and are sound as a",
            "# grouping, but the slug should not be relied on. None of them was allowed to attribute by name:",
            *[f"#   {slug}" for slug in unverified],
        ]
    return "\n".join(lines)


def unverified_teams(facts: Facts, teams: Sequence[Team]) -> tuple[str, ...]:
    """Name the emitted teams the organisation did not list, which is checkable only if it was read.

    Absence from that list is not proof a team never existed — a token need not be shown every team,
    and a secret one it cannot see would look the same. So this reports rather than deletes.

    An empty list is no list, not an organisation with no teams: a cache written before this script
    recorded them says nothing about any team, and must not be read as denying all of them.
    """
    if not facts.teams_read or not facts.known_teams:
        return ()
    return tuple(
        team.identifier
        for team in teams
        if team.identifier != UNKNOWN_IDENTIFIER and team.identifier not in facts.known_teams
    )


def facts_to_cache(facts: Facts) -> dict[str, Any]:
    """Render the facts as the JSON the cache holds."""
    return {
        "organization": facts.organization,
        "repositories": list(facts.repositories),
        "archived": facts.archived,
        "teams_read": facts.teams_read,
        "known_teams": sorted(facts.known_teams),
        "team_repositories": {slug: dict(owned) for slug, owned in facts.team_repositories.items()},
        "codeowners": {name: list(teams) for name, teams in facts.codeowners.items()},
        "unreadable_codeowners": dict(facts.unreadable_codeowners),
    }


def cache_to_facts(body: Mapping[str, Any], organization: str) -> Facts | None:
    """Read a cache written by an earlier run, ignoring one taken against another organisation."""
    if str(body.get("organization")) != organization:
        progress(f"  cache is for {body.get('organization')}, not {organization}; ignoring it")
        return None
    teams = body.get("team_repositories")
    codeowners = body.get("codeowners")
    return Facts(
        organization=organization,
        repositories=tuple(str(name) for name in body.get("repositories", ())),
        archived=int(body.get("archived", 0)),
        teams_read=bool(body.get("teams_read")),
        known_teams=frozenset(canonical(str(slug)) for slug in body.get("known_teams", ())),
        team_repositories={
            str(slug): {str(name): str(access) for name, access in owned.items()}
            for slug, owned in (teams if isinstance(teams, Mapping) else {}).items()
        },
        codeowners={
            str(name): tuple(str(slug) for slug in slugs)
            for name, slugs in (codeowners if isinstance(codeowners, Mapping) else {}).items()
        },
        unreadable_codeowners={
            str(name): str(status) for name, status in body.get("unreadable_codeowners", {}).items()
        },
    )


def load_cache(path: Path | None, organization: str) -> Facts | None:
    """Read the cache if there is one, treating an unreadable or stale file as simply no cache."""
    if path is None or not path.exists():
        return None
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as failure:
        progress(f"  ignoring unreadable cache {path}: {failure}")
        return None
    if not isinstance(body, Mapping):
        progress(f"  ignoring cache {path}: not a JSON object")
        return None
    facts = cache_to_facts(body, organization)
    if facts is not None:
        progress(f"reusing cache {path}: {len(facts.repositories)} repositories")
    return facts


def write_cache(path: Path | None, facts: Facts) -> None:
    """Save the facts so that re-deciding the grouping costs nothing."""
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(facts_to_cache(facts), indent=1, sort_keys=True), encoding="utf-8")
    progress(f"cache written to {path}")


def report(facts: Facts, teams: Sequence[Team], counts: Counter[str]) -> None:
    """Say what was attributed by which rule, and which teams the result leans hardest on."""
    progress(f"\nattributed {len(facts.repositories)} repositories to {len(teams)} teams")
    for rule in RULE_ORDER:
        if counts[rule]:
            progress(f"  {rule:<16} {counts[rule]:>5}")
    largest = sorted(teams, key=lambda team: (-len(team.repositories), team.identifier))[:5]
    for team in largest:
        progress(f"  largest: {team.identifier} ({len(team.repositories)} repositories)")
    if facts.unreadable_codeowners:
        progress(f"  CODEOWNERS unreadable for {len(facts.unreadable_codeowners)} repositories")
    unverified = unverified_teams(facts, teams)
    if unverified:
        progress(
            f"  {len(unverified)} teams are not in the organisation's team list, and could not attribute"
            f" by name: {', '.join(unverified)}",
        )
    progress(
        "\nThe population is guaranteed and the grouping is a best-effort draft: correct teams by hand,"
        "\nbut do not remove repositories, which is what makes this file the whole organisation.",
    )


def proportion(value: str) -> float:
    """Parse a dominance threshold, which is a proportion above zero and at most one."""
    try:
        parsed = float(value)
    except ValueError:
        message = f"must be a number, got: {value}"
        raise argparse.ArgumentTypeError(message) from None
    if not 0 < parsed <= 1:
        message = f"must be above 0 and at most 1, got: {value}"
        raise argparse.ArgumentTypeError(message)
    return parsed


def positive_integer(value: str) -> int:
    """Parse a support threshold, which must be a whole number above zero."""
    if not value.isdigit() or int(value) == 0:
        message = f"must be a whole number greater than zero, got: {value}"
        raise argparse.ArgumentTypeError(message)
    return int(value)


def parse_arguments(argv: Sequence[str] | None) -> Options:
    """Read the command line."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--organization", default="hmcts", help="organisation to cover (default: hmcts)")
    parser.add_argument("--cache", type=Path, help="read and write fetched facts here, so a rerun costs nothing")
    parser.add_argument("--survey", type=Path, help="take CODEOWNERS from this survey_merge_gate_access.py JSON")
    parser.add_argument(
        "--offline", action="store_true", help="decide from the cache and survey alone, fetching nothing"
    )
    parser.add_argument(
        "--skip-teams", action="store_true", help="ignore GitHub team access, attributing by CODEOWNERS"
    )
    parser.add_argument(
        "--prefix-support",
        type=positive_integer,
        default=DEFAULT_PREFIX_SUPPORT,
        help=f"attributed repositories a name prefix needs before it may attribute others (default: {DEFAULT_PREFIX_SUPPORT})",
    )
    parser.add_argument(
        "--prefix-dominance",
        type=proportion,
        default=DEFAULT_PREFIX_DOMINANCE,
        help=f"proportion of a prefix that must agree on one team (default: {DEFAULT_PREFIX_DOMINANCE})",
    )
    parser.add_argument(
        "--maximum-team-share",
        type=proportion,
        default=DEFAULT_MAXIMUM_TEAM_SHARE,
        help=(
            "share of the estate above which a team's write access is administrative rather than "
            f"ownership, and is not read as a claim (default: {DEFAULT_MAXIMUM_TEAM_SHARE})"
        ),
    )
    parser.add_argument(
        "--exclude-team",
        dest="excluded_teams",
        action="append",
        metavar="SLUG",
        help=(
            "a handle that is not an owning team, repeatable; added to the default "
            f"{', '.join(sorted(DEFAULT_EXCLUDED_TEAMS))}"
        ),
    )
    parser.add_argument("--no-header", action="store_true", help="omit the provenance comment above `teams:`")
    parsed = parser.parse_args(argv)
    return Options(
        organization=parsed.organization,
        cache=parsed.cache,
        survey=parsed.survey,
        offline=parsed.offline,
        skip_teams=parsed.skip_teams,
        prefix_support=parsed.prefix_support,
        prefix_dominance=parsed.prefix_dominance,
        maximum_team_share=parsed.maximum_team_share,
        excluded_teams=DEFAULT_EXCLUDED_TEAMS | {canonical(slug) for slug in parsed.excluded_teams or ()},
        include_header=not parsed.no_header,
    )


def main(argv: Sequence[str] | None = None) -> None:
    """Build a configuration covering every repository in the organisation."""
    options = parse_arguments(argv)
    cached = load_cache(options.cache, options.organization)
    survey = load_survey(options.survey) if options.survey is not None else None
    reader = None if options.offline else GitHubReader(DEFAULT_API_URL, resolve_token())
    facts = gather(reader, options, cached, survey)
    if not facts.repositories:
        sys.exit(f"no non-archived repositories found for {options.organization}")
    # Cached before anything is dropped, so a run that ignores team access still preserves it.
    write_cache(options.cache, facts)
    if options.skip_teams:
        facts = without_team_access(facts)

    decisions = attribute(facts, options)
    teams = build_teams(decisions)
    counts = Counter(decision.rule for decision in decisions)
    document = f"teams:\n{render(teams)}\n"
    validate(document, teams)
    sys.stdout.write(f"{header(facts, teams, counts, options)}\n{document}" if options.include_header else document)
    report(facts, teams, counts)


if __name__ == "__main__":
    main()
