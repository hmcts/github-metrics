"""Serve stored evidence over HTTP, so a browser can read what `metrics evidence` prints.

READ-ONLY, AND CREDENTIAL-FREE ALWAYS. There is no GitHub API client, no session and no token
anywhere in this module, and EVERY EVIDENCE RESPONSE is assembled by `evidence.offline_practice_report`
from the SQLite caches a `metrics collect` run wrote. A window nobody has collected reports itself
through the report's own `unavailable` entries rather than as thinner data, which is the whole reason
the open pull-request state was made cacheable first. See architecture.md, "Scope boundaries", for the
dated reversal that admitted a service and a UI at all, and the boundaries it left standing: no
personal rankings, no cross-repository averaging, no combined team verdict, score or ordering.

THE ONE THING THIS MODULE FETCHES is the public production approvals list (`metrics.production`), and
it is a NARROW, DATED EXCEPTION to the offline invariant rather than a door left open — see
architecture.md, "Scope boundaries", 2026-09-04. It carries no credential, it names no reporting
window, and it is a classification of repositories rather than evidence about anything the report
measures, so a fetch that fails costs a badge rather than a figure: the field goes absent and every
count on the page is the count it would have been. Nothing else here may contact anything.

Its own entry point, `metrics-serve`, rather than a `metrics` subcommand — it shares no argument with
the collection commands, and `cli.py` is edited heavily elsewhere.
"""

import asyncio
import logging
import threading
from argparse import ArgumentParser, ArgumentTypeError, Namespace
from collections import Counter
from collections.abc import AsyncIterator, Callable, Collection, Iterable, Iterator, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from itertools import groupby
from operator import itemgetter
from pathlib import Path
from typing import Annotated, Self

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from pydantic import NonNegativeFloat, NonNegativeInt, PositiveInt, model_validator

from metrics.config import (
    Configuration,
    ConfigurationError,
    enablement_instants,
    load_configuration,
    owned_repositories,
    repository_owners,
)
from metrics.domain import (
    ActorReadiness,
    BehaviourMetricSummary,
    EvidenceModel,
    MergeGateEvidence,
    MergeGateReport,
    PracticeEvidenceReport,
    ReadinessLabel,
    ReportingWindow,
    RepositoryPracticeEvidence,
    RepositoryTrend,
    SecurityAlertEvidence,
    SonarRating,
    UnreviewedSubstantialOutcome,
    actor_labels,
)
from metrics.evidence import collected_through, offline_practice_report
from metrics.production import fetch_production_repositories
from metrics.storage import StorageError, observation_database
from metrics.trend import SeriesRequest, repository_trend
from metrics.window import collected_anchor, collection_is_stale, period_windows

WEEKS_OPTIONS: tuple[int, ...] = (1, 4, 8, 12, 26)
"""Every window span a reader may ask for, in weeks, shortest first.

A fixed list rather than a free span in days: each span is a whole report held in memory, and an
arbitrary one would build a bundle per distinct request and reuse none of them. These five are the
spans the predecessor app's week selector offered, which is what the UI replicates.
"""

DEFAULT_WEEKS = 4
"""The span served when a request names none, matching the predecessor app's own default."""

DEFAULT_BUNDLE_AGE_SECONDS = 3600
"""How long a built bundle is served before it is rebuilt, absent `--max-bundle-age`.

An hour is a compromise, not a measurement: a collection normally lands once a day, and the source
stamp below notices one the moment it does, so this exists only to bound how long a bundle can be
served after something the stamp cannot see — a clock, a configuration reload — has moved on.
"""

DEFAULT_WARM_INTERVAL_SECONDS = 300
"""How often the warmer refreshes every span on offer, absent `--warm-interval`.

Below `DEFAULT_BUNDLE_AGE_SECONDS` on purpose, and by a wide margin: the warmer's interval is also
the margin it refreshes within, so a span is rebuilt on the wake BEFORE the one where a reader would
have met a stale bundle. An interval at or above the age would refresh a span only after somebody had
already waited for it, which is the whole thing the warmer exists to prevent.
"""

WARMER_SHUTDOWN_SECONDS = 5.0
"""How long shutdown waits for the warmer to notice the stopping event before leaving it behind.

Bounded rather than open-ended because the thread may be in the middle of a build, and a service
being shut down should not hold the port for the length of a cohort load. The thread is a daemon, so
whatever is left of a build when the wait runs out dies with the process rather than outliving it.
"""

DEFAULT_PERIOD_DAYS = 28
"""How long one period of a series runs when a request names no span, as `metrics trend` defaults it.

Four whole weeks, so every period holds the same mix of weekdays and a series cannot move because
one period caught an extra Monday.
"""

MAXIMUM_PERIOD_DAYS = 365
"""The longest period a series will cut, so a mistyped span cannot ask for a century of history."""

MAXIMUM_PERIODS = 26
"""How many whole periods one series will report at most.

Each period is a separate load of one repository's cached facts, so the count bounds the work a
single request can ask for. Refused rather than truncated: a series silently cut to 26 periods would
be read as the whole history since enablement.

The bound is on the series a request RESOLVES to, not only on the number it names. `periods` is
optional and, left out, asks for every whole period since enablement, which for a long-lived
repository at a short `period_days` is hundreds of cache loads under that cut's build lock. A request
that resolves above this count is refused with the count it asked for, so the caller names a cut
rather than being handed a truncated one under the name of the whole history.
"""

PRODUCTION_RETRY_FLOOR = timedelta(seconds=60)
"""How long a FAILED production list fetch is left alone before another is attempted.

Without this, an unreachable content host costs the pages rather than the badges — which is the one
thing the fetch was admitted to this module on the promise of not doing. A reader reads the list with
no margin, so every request to `/repositories`, `/teams/{team}` and the two detail routes would find
nothing held, attempt its own GET, and wait out `metrics.production.REQUEST_TIMEOUT` behind the one
lock the list keeps: a firewall with no route to `raw.githubusercontent.com` would turn every page
view into a 30-second stall, serialised, on a thread pool the rest of the routes share.

Deliberately well BELOW `DEFAULT_WARM_INTERVAL_SECONDS`, so what this suppresses is only the request
path retrying a known-bad fetch — never the warmer's wake, which is what is meant to retry it and
which arrives long after this floor has passed. A failure therefore costs at most one attempt a
minute, and recovery from a transient one is still a wake away rather than an hour.
"""

MAXIMUM_HELD_SERIES = 32
"""How many built series one cache holds before the least recently read is dropped.

Unlike the window bundles, whose keys are the five spans on offer, a series key carries the cut a
request named: `period_days` alone runs 1 to 365, so the keys are effectively unbounded and a cache
that only ever inserted would grow for the life of the process. Thirty-two is a working set, not a
measurement — enough that a reader flicking between repositories rebuilds nothing.
"""

NOT_ASSESSED = "not_assessed"
"""How a label distribution names the repositories the readiness policy graded nothing for.

Its own key rather than a missing one, and deliberately not a label: a repository below the minimum
cohort, or one reported under a disabled policy, was not judged, and counting it under a colour
would grade something the report left ungraded.
"""


class ServiceHealth(EvidenceModel):
    """Answer whether the service is up, and which organisation it was configured for."""

    status: str
    organization: str


class WindowOptions(EvidenceModel):
    """List the spans this service reports, the one it serves by default, and the trend cut.

    `trend_periods` is here because a series must be cut by the CALLER: left out, `periods` asks for
    every whole period since enablement, which `MAXIMUM_PERIODS` refuses rather than truncates. A
    client that restated the cut as a constant of its own would keep asking for a series this
    service has since stopped cutting, so the cut is published beside the spans and read from here.

    THE COLLECTION STATE IS PUBLISHED HERE for the same reason: every page already fetches this
    route for the span selector, and the notice that the figures are anchored at an old collection
    belongs on every page rather than on the overview alone. `collected_through` is absent when
    nothing has been collected under the current query signature, which is when `collection_stale`
    is true without an instant to name.
    """

    options: tuple[PositiveInt, ...]
    default: PositiveInt
    trend_periods: PositiveInt
    collected_through: datetime | None = None
    collection_stale: bool


class OverviewSummary(EvidenceModel):
    """Count what one window covers, for the header and stat cards of the overview page.

    COUNTS ONLY. The label distribution says how many repositories carry each readiness label, which
    a reader could arrive at by adding the repository list up by hand — the same ground the report's
    own `Repository Summary` block stands on. Nothing here is a score, a combined label, or a figure
    per team, and `merged_pull_requests` and `direct_commits` are sums of changes rather than
    averages of anything (architecture.md, "Scope boundaries").

    `collected_through` sits beside the window rather than in place of `built_at`: the header prints
    the window the figures cover and the collection that window is anchored to, which are different
    instants from the moment this bundle was assembled. It is absent when nothing was collected.
    """

    organization: str
    weeks: PositiveInt
    starts_at: datetime
    ends_at: datetime
    built_at: datetime
    collected_through: datetime | None = None
    repositories: NonNegativeInt
    unavailable: NonNegativeInt
    teams: NonNegativeInt
    actors: NonNegativeInt
    merged_pull_requests: NonNegativeInt
    direct_commits: NonNegativeInt
    labels: dict[str, NonNegativeInt]


class RepositoryRow(EvidenceModel):
    """Summarise one repository for a list, whether this window could be reported for it or not.

    Every count is optional and `detail` carries the reason there are none, because a repository the
    cache cannot cover must not appear among zeros: "nothing merged" and "nobody collected this
    window" are different answers, and the list is where they would be confused first.

    Everything after `finding_occurrences` says what the row's own counts cannot, so a list can
    distribute the estate and answer for its governance without loading every evidence block. Each is
    UNMEASURED WHEN ABSENT, as every count above it is: the two gate figures where there is no gate to
    read or its rules were withheld, `unreviewed_substantial` where the policy graded nothing,
    `sonar_coverage` and the two Sonar security measures where no SonarCloud project resolved, its
    measures could not be read, or the project sent no such metric, `codeowners_files` where nobody
    could read the repository's contents, and `security` where the whole alert block carries a reason
    instead of alerts. All of them are absent besides on a repository this window could not be
    reported for at all, which is the branch carrying `detail`. None of them is zero by default.

    `sonar_reported` is the one exception, and is `False` RATHER THAN ABSENT on a reportable
    repository whose measures could not be read or whose project never resolved: the column it feeds
    answers "is there Sonar information here", so "no Sonar" and "no report" have to stay apart, and
    only the unreportable branch leaves it absent.

    `security` carries `SecurityAlertEvidence` verbatim rather than flattening its three families
    into scalars, because the per-family `open`/`by_severity`/`detail` is what a band needs — a
    family with nothing open and one GitHub refused are different answers, and only the block itself
    keeps them apart.

    `production` is the one field here that is not read from the window at all: it says whether the
    organisation's production approvals list holds this repository, and it follows the same rule
    every count above it does. ABSENT MEANS NO LIST COULD BE READ, and is NOT `false` — a repository
    the list does not name is not approved to deploy to production, while an unreadable list has
    said nothing about any repository, and a service that answered `false` for both would report an
    estate with no production services as confidently as it reports the truth.
    """

    repository: str
    team: str
    readiness: ReadinessLabel | None = None
    merged_pull_requests: NonNegativeInt | None = None
    direct_commits: NonNegativeInt | None = None
    currently_open: NonNegativeInt | None = None
    stale_open: NonNegativeInt | None = None
    finding_occurrences: NonNegativeInt | None = None
    required_approving_reviews: NonNegativeInt | None = None
    required_status_checks: NonNegativeInt | None = None
    unreviewed_substantial: UnreviewedSubstantialOutcome | None = None
    sonar_coverage: NonNegativeFloat | None = None
    codeowners_files: NonNegativeInt | None = None
    sonar_reported: bool | None = None
    security: SecurityAlertEvidence | None = None
    sonar_security_rating: SonarRating | None = None
    sonar_security_issues: NonNegativeInt | None = None
    production: bool | None = None
    detail: str | None = None


class ContributorRow(EvidenceModel):
    """State how much one person contributed to one repository, and what was measured over it.

    Every field is the one `ActorRepositoryReadiness` already carries, re-read per repository so a
    repository page can list its contributors without loading every actor. None of them judges the
    person: `blocking` re-reports occurrences a practice rule already found.

    `metrics` is carried VERBATIM and THIS SERVICE DERIVES NOTHING FROM IT. The summaries are that
    person's merges in this repository alone, and a page that wants how many pull requests they
    merged or how many merged unreviewed subtracts it out of the review coverage denominator itself
    (`ui/src/lib/contributor.ts`). Deriving it here would put a second arithmetic beside the one the
    contract already states, and the rows stay contributions-ordered counts of what was done rather
    than anything people are ordered by (architecture.md, "Scope boundaries").
    """

    login: str
    contributions: PositiveInt
    blocking: NonNegativeInt
    metrics: tuple[BehaviourMetricSummary, ...] = ()


class RepositoryDetail(EvidenceModel):
    """Carry one repository's whole evidence block, or the reason this window has none.

    The block is the DOMAIN model the contract emits, unflattened: a detail page shows the assessment,
    the gate, the four open pull-request counts, the alert families, CODEOWNERS, maintenance, Sonar,
    the nine metric summaries and the findings, and re-modelling any of that here would be a second
    place for it to drift. Only the three things the block cannot state are added — the owning team
    for a repository that could not be reported, the contributor rows the actor section holds, and
    whether the repository is approved to deploy to production.

    `production` is ABSENT WHERE NO LIST COULD BE READ and is NOT `false`, the rule `RepositoryRow`
    states for it and for every count beside it. It is set on BOTH BRANCHES, the unavailable one
    included: whether a repository deploys to production is not a fact about the reporting window,
    so a span with no evidence to report still knows the answer and still shows the badge.
    """

    repository: str
    team: str
    evidence: RepositoryPracticeEvidence | None = None
    contributors: tuple[ContributorRow, ...] = ()
    production: bool | None = None
    detail: str | None = None

    @model_validator(mode="after")
    def validate_availability(self) -> Self:
        """Require either an evidence block or the reason this window has none."""
        if (self.evidence is None) == (self.detail is None):
            message = "a repository detail must carry either an evidence block or the reason it is unavailable"
            raise ValueError(message)
        return self


class ActorRow(EvidenceModel):
    """Name one person, how many repositories they contributed to, and the labels those carry.

    `labels` LISTS the distinct labels of the person's reported repositories, best first, and
    COMBINES NOTHING: there is no per-person label, no score and no count beside a name. It is empty
    where nothing is left to label — `cannot_assess` repositories are excluded, as they are from the
    text report's actor section, and a readiness policy that is off labels no repository at all.

    A count of repositories and a list of labels the repositories already carry, and nothing else,
    which is the personal-ranking boundary this service is bound by.
    """

    login: str
    repositories: PositiveInt
    labels: tuple[ReadinessLabel, ...] = ()


class ActorDetail(EvidenceModel):
    """Carry one person's contract row beside the team owning each repository named in it.

    `actor` is the domain model unchanged, so the page reads the same per-repository rows the JSON
    carries — labels LISTED per repository and combined nowhere. `teams` is accounting the row cannot
    state on its own, so a repository link can carry its team without a second request.

    `production` sits beside `teams` for `teams`' own reason: it names which of THIS PERSON'S
    repositories deploy to production, which is accounting the contract's `ActorReadiness` cannot
    state, and the contract model is passed through unchanged rather than gaining a field. It is
    ABSENT where no list could be read and EMPTY where the list was read and names none of them,
    which are different answers for the reason `RepositoryRow.production` gives.
    """

    actor: ActorReadiness
    teams: dict[str, str]
    production: tuple[str, ...] | None = None


class TeamActorRow(EvidenceModel):
    """Name one person who contributed to one team's repositories, and how much they contributed there.

    `contributions` is summed across THAT TEAM'S repositories, and is a count of merges rather than
    an average of anything: no label, metric or verdict is combined, and the rows are alphabetical so
    nobody is ordered by what they did.
    """

    login: str
    repositories: PositiveInt
    contributions: PositiveInt


class TeamRow(EvidenceModel):
    """Summarise one team for a list: what it owns, who worked in it, and its label distribution.

    PER-TEAM LABEL COUNTS ARE PERMITTED from 2026-09-01 (architecture.md). A distribution is not a
    verdict: `labels` says how many of this team's repositories carry each label, and there is no
    combined team label, no score, and no ordering of teams by any of it.
    """

    team: str
    repositories: NonNegativeInt
    unavailable: NonNegativeInt
    actors: NonNegativeInt
    labels: dict[str, NonNegativeInt]


class TeamDetail(EvidenceModel):
    """Carry one team's repositories and contributors, with the same label counts the list shows."""

    team: str
    repositories: tuple[RepositoryRow, ...]
    actors: tuple[TeamActorRow, ...]
    unavailable: NonNegativeInt
    labels: dict[str, NonNegativeInt]


def window_options(maximum_days: int) -> tuple[int, ...]:
    """Return the spans a configuration's maximum window span allows, shortest first.

    Filtered rather than clamped: a configuration that refuses a 182-day window must not be served a
    26-week report shortened to fit, because the selector would then offer a span whose figures cover
    something else. The span simply is not on offer.
    """
    return tuple(weeks for weeks in WEEKS_OPTIONS if timedelta(weeks=weeks) <= timedelta(days=maximum_days))


def default_window(options: Collection[int]) -> int:
    """Return the span served when a request names none: four weeks, or the longest allowed below it."""
    return DEFAULT_WEEKS if DEFAULT_WEEKS in options else max(options)


def reporting_window(weeks: int, anchor: datetime) -> ReportingWindow:
    """Return the half-open window one span covers, ending at the midnight it is anchored to.

    The RESOLVED anchor rather than a reference instant, because the anchor this service reports
    from is where the caches end and not where today does: `collected_anchor` floors the last
    collection's edge, so a span served the day after a collection reports the same figures it did
    the day the collection landed instead of asking for hours nobody has collected.
    """
    return ReportingWindow(starts_at=anchor - timedelta(weeks=weeks), ends_at=anchor)


type SourceStamp = tuple[tuple[float, int] | None, ...]
"""What both cache files looked like when something was built from them."""


def file_stamp(path: Path) -> tuple[float, int] | None:
    """Describe one cache file closely enough to notice a collection, or say it is not there yet."""
    try:
        status = path.stat()
    except OSError:
        return None
    return status.st_mtime, status.st_size


def source_stamp(configuration: Configuration) -> SourceStamp:
    """Stamp both cache files, so a bundle built before a collection is never served after one.

    Modification time and size rather than a content hash: the caches run to hundreds of megabytes
    and every write moves both, while a request cannot afford to read them. Both files are stamped
    because a collection writes the windowed facts to one and the alert history to the other.
    """
    return tuple(file_stamp(path) for path in (configuration.database, observation_database(configuration.database)))


@dataclass(frozen=True)
class CachedBuild:
    """Say when something was built and what the caches looked like when it was.

    Shared by the two things this service holds — a window's whole report, and one repository's
    series — because both are rebuilt on the same two conditions, and a second copy of the age and
    stamp fields is how one of them would come to be rebuilt on only one of them.
    """

    built_at: datetime
    stamp: SourceStamp


@dataclass(frozen=True)
class ReportBundle(CachedBuild):
    """Hold one window's report and every index the endpoints read it through.

    Built once per span and reused, because assembling it loads every configured repository's cached
    facts: the indexes exist so that a repository page, an actor page and a team page cost a lookup
    rather than a walk of the whole report each time.

    `stamp` and `built_at` are what `WindowCache` decides to rebuild on. They are properties of THIS
    bundle rather than of the cache, so a span nobody has asked for since a collection cannot be
    served from a bundle that predates it.

    `collected_through` is the instant `window` was anchored at, carried so that a response can name
    the collection its figures cover without reading the cache a second time.
    """

    weeks: int
    window: ReportingWindow
    collected_through: datetime | None
    report: PracticeEvidenceReport
    owners: Mapping[str, str]
    teams: Mapping[str, tuple[str, ...]]
    repositories: Mapping[str, RepositoryPracticeEvidence]
    unavailable: Mapping[str, str]
    actors: Mapping[str, ActorReadiness]
    contributors: Mapping[str, tuple[ContributorRow, ...]]


@dataclass(frozen=True)
class ProductionList:
    """Hold the production repository pairs last read, and the instant they were read at.

    NOT a `CachedBuild`, and deliberately: every other thing this cache holds is rebuilt when the
    source stamp moves or when it ages out, and this one has no source stamp to compare against — the
    source is a document on another host, and nothing local moves when it changes. Age is therefore
    the whole rule, and the field is its own type rather than a `CachedBuild` carrying a stamp that
    could only ever be empty.
    """

    built_at: datetime
    repositories: frozenset[tuple[str, str]]


@dataclass(frozen=True)
class TrendBundle(CachedBuild):
    """Hold one repository's series, built for one cut of it.

    Beside the window bundles rather than inside one: a series is anchored to the repository's own
    enablement instant and cut into periods of its own, so it belongs to no reporting window and
    would be rebuilt by every span the reader flicks through if it were held by one.
    """

    series: RepositoryTrend


def team_repositories(configuration: Configuration) -> Mapping[str, tuple[str, ...]]:
    """Group every configured repository under the team that owns it, in the reporting order.

    Read from configuration rather than from the report, so a team whose every repository the window
    could not be reported for still exists and still has a page saying so.
    """
    return {
        team: tuple(repository for _, repository in group)
        for team, group in groupby(owned_repositories(configuration), key=itemgetter(0))
    }


def contributor_rows(report: PracticeEvidenceReport) -> Mapping[str, tuple[ContributorRow, ...]]:
    """Re-read the actor section by repository, each repository's weightiest contributor first.

    Contributions descending, ties by login, which is the order `ActorReadiness` already orders a
    person's repositories by — the contract's own ordering, read the other way round. It orders
    CHANGES, not people: the rows say how much of one repository's window each person authored.
    """
    rows = sorted(
        (
            (
                row.repository,
                ContributorRow(
                    login=actor.actor_login,
                    contributions=row.contributions,
                    blocking=row.blocking,
                    metrics=row.metrics,
                ),
            )
            for actor in report.actors
            for row in actor.repositories
        ),
        key=lambda item: (item[0], -item[1].contributions, item[1].login),
    )
    return {repository: tuple(row for _, row in group) for repository, group in groupby(rows, key=itemgetter(0))}


def label_counts(repositories: Iterable[RepositoryPracticeEvidence]) -> dict[str, int]:
    """Count how many of the given repositories carry each readiness label, zeros included.

    Every label is present at every window, so two responses hold the same keys and a label nobody
    carries reads as an observation rather than an omission — the rule the rendered summary block
    follows, for its reason. The repositories the window could not be reported for are counted
    nowhere here: they carry no assessment to distribute, and are reported as their own figure.
    """
    counted = Counter(NOT_ASSESSED if item.assessment is None else item.assessment.label.value for item in repositories)
    return {key: counted[key] for key in (*(label.value for label in ReadinessLabel), NOT_ASSESSED)}


def report_bundle(configuration: Configuration, weeks: int, reference: datetime) -> ReportBundle:
    """Assemble one span's report from the caches and index it.

    The stamp is taken BEFORE the report is loaded, so a collection landing while this runs
    invalidates the bundle it did not get into rather than being missed until the next one. The
    collected edge is read AFTER the stamp, for the same ordering reason read the other way round: a
    bundle may be anchored at a collection older than the caches it was built from, but never at one
    newer than the stamp that will invalidate it.
    """
    stamp = source_stamp(configuration)
    collected = collected_through(configuration)
    window = reporting_window(weeks, collected_anchor(collected, reference))
    report = offline_practice_report(configuration, window)
    return ReportBundle(
        weeks=weeks,
        window=window,
        collected_through=collected,
        report=report,
        built_at=reference,
        stamp=stamp,
        owners=repository_owners(configuration),
        teams=team_repositories(configuration),
        repositories={item.repository: item for item in report.repositories},
        unavailable={item.repository: item.detail for item in report.unavailable},
        # Case-folded, because a GitHub login is unique case-insensitively and the actor section
        # already treats `Alice` and `alice` as the one person they are.
        actors={actor.actor_login.casefold(): actor for actor in report.actors},
        contributors=contributor_rows(report),
    )


def repository_series(
    configuration: Configuration,
    repository: str,
    period_days: int,
    periods: int | None,
    reference: datetime,
) -> RepositoryTrend:
    """Cut one repository's series from the caches, contacting nothing.

    `client=None` is what makes the run offline in `metrics.trend`, exactly as `metrics trend
    --offline` does it: a period the caches do not fully cover is refused there and reported as the
    window's own reason, rather than being collected behind a reader's back.

    The series is cut against the collected anchor rather than against `reference`, so the trailing
    period a reader sees is one the caches cover whole. `reference` is still what the bundle holding
    this series ages against, which is why it is passed in rather than read here.
    """
    anchor = collected_anchor(collected_through(configuration), reference)
    request = SeriesRequest(period_days=period_days, periods=periods, reference=anchor, client=None)
    return repository_trend(configuration, request, repository, enablement_instants(configuration)[repository])


type SeriesKey = tuple[str, int, int | None]
"""What one held series is keyed by: the repository, the period span, and the period count."""


class LockRegistry[Key]:
    """Hand out one lock per key, guarding the handing out with a lock of its own.

    The guard is held for a dictionary lookup and NEVER ACROSS THE WORK, which is the whole point:
    the expensive build runs under the key's own lock, where it blocks only the requests that would
    otherwise have repeated it.

    Reference counted rather than accumulating, because a series key carries the cut a request named
    and the key space is effectively unbounded (`MAXIMUM_HELD_SERIES`). A lock is dropped only once
    no thread holds it or waits for it, so dropping cannot race a build: the thread that would
    collide with an evicted lock is exactly the thread whose count keeps it alive.
    """

    def __init__(self) -> None:
        self.guard = threading.Lock()
        self.locks: dict[Key, threading.Lock] = {}
        self.holders: Counter[Key] = Counter()

    @contextmanager
    def hold(self, key: Key) -> Iterator[None]:
        """Run the caller's block under this key's lock, and under no other key's."""
        with self.guard:
            lock = self.locks.setdefault(key, threading.Lock())
            self.holders[key] += 1
        try:
            with lock:
                yield
        finally:
            with self.guard:
                self.holders[key] -= 1
                if not self.holders[key]:
                    del self.holders[key]
                    del self.locks[key]


class WindowCache:
    """Hold one built bundle per span and per series cut, rebuilding on a collection or on age.

    ONE LOCK PER SPAN AND PER SERIES CUT, not one for the whole cache. The bound it keeps is that a
    span builds ONCE — two requests for a cold span walk every configured repository's cached facts
    between them rather than each — while another span is free to build beside it. One lock for
    every span kept the first half of that at the price of the second: a cold 26-week build held up
    every other request, including ones for a span already built, which is what a reader flicking
    the week selector was waiting for.

    The eviction order of `self.series` is the one piece of state that belongs to no single key, so
    it keeps a lock of its own that `retain` and the least-recently-read reordering both run under.

    The production list is a single held value rather than one per key, so it keeps a lock of its own
    for the same reason and for one more: it is the only thing here whose refresh reaches the network,
    and a fetch behind a span's build lock would put a reader waiting for a bundle behind a content
    host's timeout.
    """

    def __init__(self, configuration: Configuration, maximum_age: timedelta) -> None:
        self.configuration = configuration
        self.maximum_age = maximum_age
        self.builds: LockRegistry[int] = LockRegistry()
        self.cuts: LockRegistry[SeriesKey] = LockRegistry()
        self.eviction = threading.Lock()
        self.approvals = threading.Lock()
        self.bundles: dict[int, ReportBundle] = {}
        self.series: dict[SeriesKey, TrendBundle] = {}
        self.production: ProductionList | None = None
        self.production_failed_at: datetime | None = None

    def usable(self, built: CachedBuild, stamp: SourceStamp, reference: datetime) -> bool:
        """Decide whether something held still describes the caches it was built from."""
        return built.stamp == stamp and reference - built.built_at <= self.maximum_age

    def bundle(self, weeks: int) -> ReportBundle:
        """Return the bundle for one span, building it if there is no usable one held.

        The stamp is read outside every lock: it is two `stat` calls, and reading it under a lock
        would put every request behind whichever one is building.

        Checked once before the lock and again inside it. The check inside is what makes a request
        that waited for a build return what the builder produced rather than build it again — the
        held bundle is then newer than this request's own `reference`, which `usable` reads as
        having no age at all.
        """
        reference = datetime.now(UTC)
        stamp = source_stamp(self.configuration)
        held = self.bundles.get(weeks)
        if held is not None and self.usable(held, stamp, reference):
            return held
        with self.builds.hold(weeks):
            held = self.bundles.get(weeks)
            if held is not None and self.usable(held, stamp, reference):
                return held
            built = report_bundle(self.configuration, weeks, reference)
            self.bundles[weeks] = built
            return built

    def warm(self, weeks: int, margin: timedelta) -> bool:
        """Build one span's bundle before a reader can meet a stale one, and say whether it built.

        `bundle` CANNOT SERVE THIS. It deliberately returns a bundle that is still usable, so a
        warmer asking it for one would refresh nothing until the bundle had already expired — which
        is to say, until a reader had already waited for the rebuild. `margin` is how far ahead of
        `maximum_age` a bundle is refreshed instead: a span that would go stale before the warmer's
        next wake is rebuilt on this one, and the reader never meets the build at all.

        Under the span's own build lock and checked again inside it, exactly as `bundle` is, so a
        warm landing beside a reader's own build waits for it rather than walking the whole cohort's
        cached facts a second time.
        """
        reference = datetime.now(UTC)
        stamp = source_stamp(self.configuration)
        if self.warmed(weeks, stamp, reference + margin):
            return False
        with self.builds.hold(weeks):
            if self.warmed(weeks, stamp, reference + margin):
                return False
            self.bundles[weeks] = report_bundle(self.configuration, weeks, reference)
            return True

    def warmed(self, weeks: int, stamp: SourceStamp, horizon: datetime) -> bool:
        """Say whether the bundle held for one span will still be usable at the given instant."""
        held = self.bundles.get(weeks)
        return held is not None and self.usable(held, stamp, horizon)

    def production_list(self, margin: timedelta = timedelta(0)) -> frozenset[tuple[str, str]] | None:
        """Return the production repositories, refetching one that will not outlive the margin.

        Read exactly as `bundle` and `warm` read a span, and for their reasons: the held value is
        checked outside every lock, checked again inside it so a request that waited comes away with
        what the other thread fetched, and refreshed a MARGIN ahead of expiry so the warmer's wake
        renews it before a reader could meet a stale one. Age is the whole rule here — there is no
        source stamp, because the source is a document on another host.

        A FAILED REFRESH KEEPS THE LAST GOOD LIST. A content host's transient 502 must cost the
        badges nothing, so only a cache that has never once read the list answers `None`, and the
        failure is logged at warning level so a list that has been failing all day is visible in the
        log rather than only in the missing badges.

        A FAILURE IS ALSO REMEMBERED FOR `PRODUCTION_RETRY_FLOOR`, and that is what keeps a failing
        fetch off the request path: a reader reads with NO MARGIN, so without it every request would
        find nothing usable held and wait out the fetch's whole timeout for itself, behind this one
        lock. The floor is checked inside the lock only — once a failure is held the lock is
        uncontended, and a reader arriving while a fetch is in flight waits for its answer either
        way.

        Configured with no URL, the held value is returned without fetching — which for a service
        that has never fetched is `None`, and an absent field on every row is what a configuration
        naming no list is saying.
        """
        url = self.configuration.production_list_url
        held = self.production
        if url is None:
            return None if held is None else held.repositories
        reference = datetime.now(UTC)
        if held is not None and self.current(held, reference + margin):
            return held.repositories
        with self.approvals:
            held = self.production
            if held is not None and self.current(held, reference + margin):
                return held.repositories
            if self.failing(reference):
                return None if held is None else held.repositories
            fetched = fetch_production_repositories(url)
            if fetched is None:
                self.production_failed_at = reference
                logging.warning("Could not refresh the production list from %s; keeping what was last read", url)
                return None if held is None else held.repositories
            self.production = ProductionList(built_at=reference, repositories=fetched)
            return fetched

    def current(self, held: ProductionList, horizon: datetime) -> bool:
        """Say whether a fetched production list will still be young enough at the given instant."""
        return horizon - held.built_at <= self.maximum_age

    def failing(self, reference: datetime) -> bool:
        """Say whether the last production list fetch failed too recently to be worth repeating."""
        failed_at = self.production_failed_at
        return failed_at is not None and reference - failed_at <= PRODUCTION_RETRY_FLOOR

    def trend(self, repository: str, period_days: int, periods: int | None) -> RepositoryTrend:
        """Return one repository's series for one cut of it, building it if none is held.

        Keyed by the cut as well as the repository, because two cuts of the same history are two
        different series: reusing a 28-day series for a 7-day request would answer with periods four
        times the length of the ones asked for, and nothing in the response would say so.

        Read before the cut's lock and again inside it, for the reason `bundle` states.
        """
        reference = datetime.now(UTC)
        stamp = source_stamp(self.configuration)
        key = (repository, period_days, periods)
        held = self.reread(key, stamp, reference)
        if held is not None:
            return held
        with self.cuts.hold(key):
            held = self.reread(key, stamp, reference)
            if held is not None:
                return held
            built = TrendBundle(
                built_at=reference,
                stamp=stamp,
                series=repository_series(self.configuration, repository, period_days, periods, reference),
            )
            self.store(key, built)
            return built.series

    def reread(self, key: SeriesKey, stamp: SourceStamp, reference: datetime) -> RepositoryTrend | None:
        """Return the held series for one cut if it still describes the caches, or nothing.

        Under the eviction lock, because reading is what makes a series recently read: the key moves
        to the end of `self.series`, and the order that move maintains is shared by every key.
        """
        with self.eviction:
            held = self.series.get(key)
            if held is None or not self.usable(held, stamp, reference):
                return None
            # Reinsert so the key moves to the end: `retain` drops from the front, which makes
            # insertion order least-recently-read order and keeps a reread series out of the way.
            del self.series[key]
            self.series[key] = held
            return held.series

    def store(self, key: SeriesKey, built: TrendBundle) -> None:
        """Hold one freshly cut series as the most recently read, and drop what no longer fits."""
        with self.eviction:
            self.series.pop(key, None)
            self.series[key] = built
            self.retain()

    def retain(self) -> None:
        """Drop the least recently read series until no more than `MAXIMUM_HELD_SERIES` are held.

        Called with `self.eviction` held: the order it drops from is the order `reread` maintains,
        and a trim reading it while a reinsertion moved a key would drop a series somebody is
        reading now.
        """
        while len(self.series) > MAXIMUM_HELD_SERIES:
            del self.series[next(iter(self.series))]


def warm_spans(cache: WindowCache, spans: Iterable[int], margin: timedelta) -> None:
    """Refresh every span that would be stale within the margin, and survive one that will not build.

    A build that raises costs a log line rather than the warmer: a cache file that cannot be read
    now is a repository state that may be readable at the next wake, and a thread that died on it
    would take every later refresh with it — leaving a service that looks warm and is not.

    The production list is refreshed here too, on the same wake and with the same margin, which is
    what puts it on `--warm-interval` rather than on a collection's cadence. AFTER the spans, because
    it is the one refresh that reaches another host: a content host taking its whole timeout to
    answer must not hold up the bundles a reader is actually waiting for.
    """
    for weeks in spans:
        try:
            refreshed = cache.warm(weeks, margin)
        # Blind, and with the traceback logged: whatever reading a cache raises must cost this
        # refresh rather than the thread, and a warm nobody asked for has no caller to re-raise to.
        except Exception:
            logging.warning("Could not warm the %s-week window", weeks, exc_info=True)
        else:
            if refreshed:
                logging.info("Warmed the %s-week window", weeks)
    try:
        cache.production_list(margin)
    # Blind for the reason above, and doubly so here: `fetch_production_repositories` answers every
    # failure it knows of with `None`, so anything reaching this is something nobody anticipated —
    # which is exactly what must not be allowed to take the warmer's thread down with it.
    except Exception:
        logging.warning("Could not refresh the production list", exc_info=True)


def keep_warm(cache: WindowCache, spans: Iterable[int], interval: timedelta, stopping: threading.Event) -> None:
    """Build every offered span, then refresh them on the interval until the stopping event is set.

    The interval is passed as the margin as well, so what is refreshed on each wake is whatever
    would not have survived until the next one. Only the first pass ever builds a cold span: without
    it, every span but the default is cold until somebody asks for it, and that reader pays for the
    whole cohort's cached facts.

    Waiting on the event rather than sleeping, so shutdown costs whatever is left of a build rather
    than the rest of an interval.
    """
    offered = tuple(spans)
    warm_spans(cache, offered, interval)
    while not stopping.wait(interval.total_seconds()):
        warm_spans(cache, offered, interval)


@dataclass(frozen=True)
class ServiceState:
    """What every request reads: the cache to serve from, and the spans on offer."""

    configuration: Configuration
    cache: WindowCache
    options: tuple[int, ...]
    default: int


def service_state(request: Request) -> ServiceState:
    """Return the state `create_app` attached to the application serving this request.

    Starlette's `state` is deliberately untyped, so the whole service's one unchecked read lives here
    rather than in every endpoint that needs the cache.
    """
    state: ServiceState = request.app.state.service
    return state


def requested_bundle(request: Request, weeks: int | None = None) -> ReportBundle:
    """Serve the bundle for the requested span, refusing any span this service does not offer.

    Refused rather than clamped or rounded to the nearest span: a report answering for 26 weeks when
    30 were asked for would be read as covering the 30, and a caller reading a figure over the wrong
    window cannot tell. The status is the one FastAPI already uses for a rejected query value, so a
    span off the list and a `weeks=soon` are refused alike.
    """
    service = service_state(request)
    if weeks is None:
        return service.cache.bundle(service.default)
    if weeks not in service.options:
        detail = f"weeks must be one of {', '.join(str(option) for option in service.options)}"
        raise HTTPException(status_code=HTTPStatus.UNPROCESSABLE_CONTENT, detail=detail)
    return service.cache.bundle(weeks)


State = Annotated[ServiceState, Depends(service_state)]
Bundle = Annotated[ReportBundle, Depends(requested_bundle)]


def readable_gate(report: MergeGateReport) -> MergeGateEvidence | None:
    """Return the gate whose rules can be read as configuration, or nothing where they cannot.

    The precedence is the one `ReadinessPolicy.governance` reads a gate in, which is why only two
    cases are withheld: a gate nobody collected, and a protected branch whose rules GitHub did not
    disclose. The second is evidence of nothing — reporting it as a branch requiring no review would
    blame a missing permission on the team that owns the repository.

    An UNPROTECTED default branch is returned rather than withheld, because it is an observed fact:
    it enforces nothing whatever rules ride with it, so its caller reports 0 rather than nothing.
    """
    gate = report.gate
    if gate is None or (gate.protected and not gate.rules_observed):
        return None
    return gate


def deploys_to_production(
    bundle: ReportBundle,
    repository: str,
    production: frozenset[tuple[str, str]] | None,
) -> bool | None:
    """Say whether one repository is on the production list, or nothing where none could be read.

    `None` IN IS `None` OUT, and that is the whole reason this is a function rather than a `in`
    against a set the caller defaulted to empty: an unread list has said nothing about this
    repository, and an empty one has said it deploys nothing.

    Matched on the CASEFOLDED pair, because `parse_repository_url` folds what it reads and GitHub
    owner and repository names are case-insensitive: the live document holds one `HMCTS/…` URL among
    200-odd lowercase ones, and a case-sensitive match would drop that repository's badge alone. The
    organisation is the report's, which is the configured one `offline_practice_report` carried into
    it.
    """
    if production is None:
        return None
    return (bundle.report.organization.casefold(), repository.casefold()) in production


def repository_row(
    bundle: ReportBundle,
    repository: str,
    production: frozenset[tuple[str, str]] | None,
) -> RepositoryRow:
    """Summarise one repository for a list, saying why instead when the window has no evidence.

    The two branches partition the configured repositories exactly: `offline_practice_report` reports
    each one either as evidence or as unavailable, so a repository is in one map or the other.

    `production` is answered on both of them, because it is not a fact about the window: a repository
    this span cannot report is still or is still not a production service, and passing `None` through
    is how the row says nobody could tell.
    """
    evidence = bundle.repositories.get(repository)
    if evidence is None:
        # The team is read from configuration here, being the one fact about an unreportable
        # repository that survives having no evidence block to state it.
        return RepositoryRow(
            repository=repository,
            team=bundle.owners[repository],
            production=deploys_to_production(bundle, repository, production),
            detail=bundle.unavailable[repository],
        )
    summary = evidence.open_pull_requests.summary
    gate = readable_gate(evidence.merge_gate)
    # 0 on an unprotected branch and the rules' own figures on a protected one, so that a branch
    # anybody can push to is counted as requiring nothing rather than as unknown.
    enforcing = gate is not None and gate.protected
    measures = evidence.sonar.measures
    codeowners = evidence.codeowners.codeowners
    return RepositoryRow(
        repository=repository,
        team=evidence.team,
        readiness=None if evidence.assessment is None else evidence.assessment.label,
        merged_pull_requests=evidence.cohort.reported,
        direct_commits=evidence.cohort.direct_commits,
        # Absent, never zero, when the block carries a reason instead of a summary: an uncollected
        # open pull-request count must not read as an empty queue.
        currently_open=None if summary is None else summary.currently_open,
        stale_open=None if summary is None else summary.stale_open,
        # Occurrences rather than findings, as `blocking` counts them: two rules each reporting six
        # merges is twelve occurrences, and counting findings would flatter the repository.
        finding_occurrences=sum(item.occurrences for item in evidence.behaviour),
        required_approving_reviews=None if gate is None else gate.required_approvals if enforcing else 0,
        required_status_checks=None if gate is None else len(gate.required_contexts) if enforcing else 0,
        # Carried as the policy graded it, absent included: the block's own field already says that
        # absent means nothing was graded rather than that nothing was found.
        unreviewed_substantial=evidence.unreviewed_substantial,
        sonar_coverage=None if measures is None else measures.coverage,
        # 0 where every checked location was looked at and held no file, and absent only where the
        # block carries a reason: an unreadable repository must not read as one owning nothing. Every
        # file found is counted, the `.md` variants GitHub does not read included, because the
        # repository page's card tones on the same total and names each file's recognition in its
        # detail line — a filtered count here would have the two disagree about one repository.
        codeowners_files=None if codeowners is None else len(codeowners.files),
        # False rather than absent on this branch, so the column can tell a repository with no Sonar
        # information from one this window could not report at all.
        sonar_reported=measures is not None,
        # The alert block verbatim, its per-family reasons included, or nothing where the whole block
        # carries a reason instead of alerts.
        security=evidence.security.alerts,
        sonar_security_rating=None if measures is None else measures.security_rating,
        sonar_security_issues=None if measures is None else measures.security_issues,
        production=deploys_to_production(bundle, repository, production),
    )


def repository_detail(
    bundle: ReportBundle,
    repository: str,
    production: frozenset[tuple[str, str]] | None,
) -> RepositoryDetail:
    """Carry one repository's evidence block and contributors, or the reason the window has none.

    The production answer is on both branches, as it is on the row and for the row's reason: the
    header carrying the badge is built before the page reaches the no-evidence branch.
    """
    evidence = bundle.repositories.get(repository)
    if evidence is None:
        return RepositoryDetail(
            repository=repository,
            team=bundle.owners[repository],
            production=deploys_to_production(bundle, repository, production),
            detail=bundle.unavailable[repository],
        )
    return RepositoryDetail(
        repository=repository,
        team=evidence.team,
        evidence=evidence,
        contributors=bundle.contributors.get(repository, ()),
        production=deploys_to_production(bundle, repository, production),
    )


def team_actors(bundle: ReportBundle, repositories: Collection[str]) -> tuple[TeamActorRow, ...]:
    """List everyone who contributed to the given repositories, with how much they contributed there.

    Alphabetical, because the report's actor section is: `evidence.actor_readiness` sorts by
    case-folded login, and re-ordering people by contributions here would be the personal ranking the
    scope boundaries exclude. Somebody who contributed to none of these repositories gets no row.
    """
    return tuple(
        TeamActorRow(
            login=actor.actor_login,
            repositories=len(inside),
            contributions=sum(row.contributions for row in inside),
        )
        for actor in bundle.report.actors
        if (inside := tuple(row for row in actor.repositories if row.repository in repositories))
    )


def reported_repositories(bundle: ReportBundle, repositories: Iterable[str]) -> tuple[RepositoryPracticeEvidence, ...]:
    """Return the evidence blocks the window holds for the given repositories, in their own order."""
    return tuple(
        evidence for repository in repositories if (evidence := bundle.repositories.get(repository)) is not None
    )


def team_row(bundle: ReportBundle, team: str) -> TeamRow:
    """Summarise one team for a list: what it owns, who worked in it, and its label distribution."""
    repositories = bundle.teams[team]
    reported = reported_repositories(bundle, repositories)
    return TeamRow(
        team=team,
        repositories=len(repositories),
        unavailable=len(repositories) - len(reported),
        actors=len(team_actors(bundle, repositories)),
        labels=label_counts(reported),
    )


def team_detail(
    bundle: ReportBundle,
    team: str,
    production: frozenset[tuple[str, str]] | None,
) -> TeamDetail:
    """Carry one team's repository rows, its contributors, and the same counts the list shows."""
    repositories = bundle.teams[team]
    reported = reported_repositories(bundle, repositories)
    return TeamDetail(
        team=team,
        repositories=tuple(repository_row(bundle, repository, production) for repository in repositories),
        actors=team_actors(bundle, repositories),
        unavailable=len(repositories) - len(reported),
        labels=label_counts(reported),
    )


def overview_summary(bundle: ReportBundle) -> OverviewSummary:
    """Count what one window covers, for the overview page's header and stat cards."""
    return OverviewSummary(
        organization=bundle.report.organization,
        weeks=bundle.weeks,
        starts_at=bundle.window.starts_at,
        ends_at=bundle.window.ends_at,
        built_at=bundle.built_at,
        collected_through=bundle.collected_through,
        repositories=len(bundle.owners),
        unavailable=len(bundle.unavailable),
        teams=len(bundle.teams),
        actors=len(bundle.actors),
        merged_pull_requests=sum(item.cohort.reported for item in bundle.report.repositories),
        direct_commits=sum(item.cohort.direct_commits for item in bundle.report.repositories),
        labels=label_counts(bundle.report.repositories),
    )


def serve_health(state: State) -> ServiceHealth:
    """Report that the service is up and which organisation it was configured for."""
    return ServiceHealth(status="ok", organization=state.configuration.organization)


def serve_windows(state: State) -> WindowOptions:
    """List the spans this service reports, the one it serves by default, the trend cut, and the collection.

    The collection state is read fresh rather than off a held bundle, so a page fetching this route
    says how old the last collection is now and not how old it was when a span was last assembled.
    """
    collected = collected_through(state.configuration)
    return WindowOptions(
        options=state.options,
        default=state.default,
        trend_periods=MAXIMUM_PERIODS,
        collected_through=collected,
        collection_stale=collection_is_stale(
            collected,
            datetime.now(UTC),
            timedelta(days=state.configuration.lookback.stale_collection_days),
        ),
    )


def serve_overview(bundle: Bundle) -> OverviewSummary:
    """Count what one window covers across every configured repository."""
    return overview_summary(bundle)


def serve_repositories(state: State, bundle: Bundle) -> tuple[RepositoryRow, ...]:
    """List every configured repository in the reporting order, unreportable ones included.

    The production list is read from the CACHE rather than off the bundle, so a row's badge is as
    fresh as the last refresh instead of as old as the span's last assembly: the two move on
    intervals of their own, and a bundle a reader is served for the rest of the hour would otherwise
    freeze the list at whatever it was when the span was built.
    """
    production = state.cache.production_list()
    return tuple(repository_row(bundle, repository, production) for repository in bundle.owners)


def serve_repository(repository: str, state: State, bundle: Bundle) -> RepositoryDetail:
    """Report one repository's whole evidence block, or why this window has none for it."""
    if repository not in bundle.owners:
        detail = f"repository is not configured: {repository}"
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=detail)
    return repository_detail(bundle, repository, state.cache.production_list())


def serve_trend(
    repository: str,
    state: State,
    period_days: Annotated[int, Query(ge=1, le=MAXIMUM_PERIOD_DAYS)] = DEFAULT_PERIOD_DAYS,
    periods: Annotated[int | None, Query(ge=1, le=MAXIMUM_PERIODS)] = None,
) -> RepositoryTrend:
    """Report one repository's periods since it was enabled, and nothing about any other.

    NO `?weeks=`: a series is cut into periods of its own from the enablement instant, and has no
    reporting window to select. The response is the contract's own `RepositoryTrend` — a repository
    with no enablement date, or none whose first whole period has elapsed, carries the reason and no
    series, and no figure here is graded or combined with another repository's.

    The count `periods` resolves to is bounded as well as the count it names, because leaving it out
    asks for every whole period since enablement — which is where the unbounded request lives.
    """
    instants = enablement_instants(state.configuration)
    # Membership, not a truthy lookup: every configured repository is in this map and a configured
    # one WITHOUT an enablement date maps to None, which is a series with a reason, not a 404.
    if repository not in instants:
        detail = f"repository is not configured: {repository}"
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=detail)
    anchor = collected_anchor(collected_through(state.configuration), datetime.now(UTC))
    resolve_periods(period_days, periods, instants[repository], anchor)
    try:
        return state.cache.trend(repository, period_days, periods)
    except StorageError as exception:
        # The alert observation history is read straight through, unlike the windowed evidence a
        # repository degrades to an `unavailable` entry for: a series that reported no observation
        # because the file could not be opened is indistinguishable from one nobody ever collected,
        # which is the single thing that history exists to keep apart. `emit_trend` refuses for the
        # same reason rather than printing a series with a hole in it.
        logging.error("Trend failed for %s: %s", repository, exception)
        detail = f"the observation history could not be read: {exception}"
        raise HTTPException(status_code=HTTPStatus.SERVICE_UNAVAILABLE, detail=detail) from exception


def resolve_periods(period_days: int, periods: int | None, enablement: datetime | None, anchor: datetime) -> None:
    """Refuse a cut that resolves to more whole periods than one request may ask for.

    Through `period_windows`, so the count refused here is the count that would have been built
    rather than a second piece of arithmetic that could drift from it. A repository with no
    enablement instant resolves to no period at all and is reported, not refused: the reason belongs
    in the series the contract carries, not in a status code.

    Counted against the collected anchor, the instant `repository_series` cuts against, so the count
    a request is refused for is the count that would have been built rather than one whole period
    more than the caches can answer for.
    """
    if enablement is None:
        return
    resolved = len(period_windows(enablement, timedelta(days=period_days), periods, anchor))
    if resolved > MAXIMUM_PERIODS:
        detail = (
            f"a series of {resolved} periods of {period_days} days exceeds the {MAXIMUM_PERIODS} "
            f"one request may ask for; name `periods` to cut it"
        )
        raise HTTPException(status_code=HTTPStatus.UNPROCESSABLE_CONTENT, detail=detail)


def serve_actors(bundle: Bundle) -> tuple[ActorRow, ...]:
    """List everyone who contributed to a reported repository, alphabetically and never ranked.

    Each row carries the distinct labels of the repositories the report grades for that person, so a
    client can group people by what they work in without asking for every person's detail. The count
    is over every repository they contributed to while the labels are over `reported_repositories`
    alone, so a row may count more repositories than it carries labels for — a person whose every
    repository is `cannot_assess` counts them all and lists nothing. An empty list means the same
    where the readiness policy is DISABLED and nothing was graded at all, which a row cannot tell
    apart: `/overview` distributes the same window's repositories over the labels and `not_assessed`,
    which is where the two are distinguishable. The order served stays alphabetical: what a client
    does with the labels is its own presentation.
    """
    return tuple(
        ActorRow(login=actor.actor_login, repositories=len(actor.repositories), labels=actor_labels(actor))
        for actor in bundle.actors.values()
    )


def serve_actor(login: str, state: State, bundle: Bundle) -> ActorDetail:
    """Report one person's repositories, matched case-insensitively and spelled as the report spells it.

    `production` names the person's own production repositories and nothing else: the page badges the
    rows it draws, and the whole organisation's list is neither its business nor its payload.
    """
    actor = bundle.actors.get(login.casefold())
    if actor is None:
        detail = f"nobody by that login contributed to a reported repository: {login}"
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=detail)
    production = state.cache.production_list()
    return ActorDetail(
        actor=actor,
        teams={row.repository: bundle.owners[row.repository] for row in actor.repositories},
        production=None
        if production is None
        else tuple(
            row.repository for row in actor.repositories if deploys_to_production(bundle, row.repository, production)
        ),
    )


def serve_teams(bundle: Bundle) -> tuple[TeamRow, ...]:
    """List every configured team with its repository count, its people, and its label counts."""
    return tuple(team_row(bundle, team) for team in bundle.teams)


def serve_team(team: str, state: State, bundle: Bundle) -> TeamDetail:
    """Report one team's repositories and contributors."""
    if team not in bundle.teams:
        detail = f"team is not configured: {team}"
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=detail)
    return team_detail(bundle, team, state.cache.production_list())


def window_warming(
    state: ServiceState,
    interval: timedelta,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """Return a lifespan handler that keeps every offered span warm while the application serves.

    Started and stopped with the application rather than at import or in `main`: a test that builds
    an application and closes it must not leave a thread refreshing a cache nobody is reading, and
    the stopping event is what makes closing enough. A daemon thread besides, so a process that
    exits without the handler ever running does not hang waiting for one more refresh.
    """

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        """Run the warmer for as long as the application is serving."""
        _ = application
        stopping = threading.Event()
        warmer = threading.Thread(
            target=keep_warm,
            args=(state.cache, state.options, interval, stopping),
            name="window-warmer",
            daemon=True,
        )
        warmer.start()
        try:
            yield
        finally:
            stopping.set()
            # Waited for on a worker thread rather than here: `join` is a blocking call, and made
            # directly it would hold the event loop for as long as whatever build the warmer is in
            # the middle of — leaving every other shutdown step behind it.
            await asyncio.to_thread(warmer.join, WARMER_SHUTDOWN_SECONDS)

    return lifespan


def create_app(configuration: Configuration, cache: WindowCache, warm_interval: timedelta | None = None) -> FastAPI:
    """Build the application, serving every route from the given cache and nothing else.

    Every route is registered with `response_model_exclude_none`, so the JSON reads exactly as
    `metrics evidence` prints it: an absent figure is an absent key, never a `null` a consumer has to
    tell apart from an observed zero.

    No CORS and no middleware: the Next.js server fetches these routes server-side, so no browser
    ever calls the service directly and a permissive origin policy would be inviting one to.

    The warmer runs only when an interval is given, which `main` gives it. An application built
    without one still serves every route and still builds a span the first time something asks for
    it; it simply starts no thread, which is what a caller that is not serving the estate wants.
    """
    options = window_options(configuration.lookback.maximum_days)
    state = ServiceState(
        configuration=configuration,
        cache=cache,
        options=options,
        default=default_window(options),
    )
    app = FastAPI(
        title="metrics evidence service",
        description=__doc__,
        lifespan=None if warm_interval is None else window_warming(state, warm_interval),
    )
    app.state.service = state
    for path, endpoint in (
        ("/healthz", serve_health),
        ("/windows", serve_windows),
        ("/overview", serve_overview),
        ("/repositories", serve_repositories),
        ("/repositories/{repository}", serve_repository),
        ("/repositories/{repository}/trend", serve_trend),
        ("/actors", serve_actors),
        ("/actors/{login}", serve_actor),
        ("/teams", serve_teams),
        ("/teams/{team}", serve_team),
    ):
        app.add_api_route(path, endpoint, response_model_exclude_none=True)
    return app


def parse_arguments() -> Namespace:
    """Build the service's own parser and parse the given command-line arguments.

    Its own rather than a subcommand of the `metrics` parser: nothing here is a window, a repository
    or an output format, and the two commands share no argument but `--config` and `--logging`.
    """
    parser = ArgumentParser(
        prog="metrics-serve",
        description="Serve stored evidence for the metrics dashboard, contacting nothing.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        action="append",
        required=True,
        metavar="PATH",
        help="path to the YAML configuration; repeat to layer files, later files winning",
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind to this address")
    parser.add_argument("--port", type=int, default=8000, help="listen on this port")
    parser.add_argument("--logging", default="info", help="set the logging level")
    parser.add_argument(
        "--max-bundle-age",
        type=positive_seconds,
        default=DEFAULT_BUNDLE_AGE_SECONDS,
        help="rebuild a window's report after this many seconds, whatever the caches look like",
    )
    parser.add_argument(
        "--warm-interval",
        type=positive_seconds,
        default=DEFAULT_WARM_INTERVAL_SECONDS,
        help=(
            "refresh every span on offer this often, so a reader meets a bundle already built; "
            "must be below --max-bundle-age"
        ),
    )
    return parser.parse_args()


def positive_seconds(raw: str) -> int:
    """Parse a whole number of seconds, refusing one that would leave the cache rebuilding forever.

    Bounded here rather than left to `int`, and for the same fault read from either side. A zero or
    negative bundle age makes `usable` false for everything held, so every request rebuilds the
    whole cohort's report under its span's build lock; a zero or negative warm interval is a thread
    doing the same thing on its own, with the caches never quiet. Either reads as a service that has
    simply stopped rather than as a mistyped flag.
    """
    seconds = int(raw)
    if seconds < 1:
        message = f"a duration must be at least one second, not {seconds}"
        raise ArgumentTypeError(message)
    return seconds


def main() -> int:
    """Entry point for the read-only evidence service"""
    options = parse_arguments()
    logging.basicConfig(level=options.logging.upper(), format="%(asctime)s:%(levelname)s: %(message)s")
    try:
        configuration = load_configuration(*options.config)
    except ConfigurationError as exception:
        logging.error("Invalid configuration: %s", exception)
        return 1
    # The same refusal `metrics evidence` makes, for its reason: the service reports the configured
    # cohort, and a configuration naming no team has none to report.
    if not configuration.teams:
        logging.error(
            "the service reports the configured repositories, but no team is configured: add a "
            "`teams:` section, or layer the team file in with a second --config",
        )
        return 1
    # Refused as a pair, because neither flag is wrong on its own. The warmer's interval is also the
    # margin it refreshes within, so an interval at or above the age asks for a span to be rebuilt on
    # every wake however fresh it is: a bundle built a second ago is not usable an interval from now
    # if the interval outlasts the age. That is a thread walking the whole cohort's cached facts for
    # every span on offer, for ever, which is the fault `positive_seconds` refuses read from one side.
    if options.warm_interval >= options.max_bundle_age:
        logging.error(
            "--warm-interval (%ss) must be below --max-bundle-age (%ss): the interval is also the "
            "margin a span is refreshed within, so an interval at or above the age rebuilds every "
            "span on every wake",
            options.warm_interval,
            options.max_bundle_age,
        )
        return 1
    spans = window_options(configuration.lookback.maximum_days)
    if not spans:
        logging.error(
            "the configured %s-day maximum window is shorter than the shortest span the service "
            "offers (%s weeks): raise lookback.maximum_days",
            configuration.lookback.maximum_days,
            WEEKS_OPTIONS[0],
        )
        return 1
    cache = WindowCache(configuration, timedelta(seconds=options.max_bundle_age))
    app = create_app(configuration, cache, warm_interval=timedelta(seconds=options.warm_interval))
    # Built before anything is listening, so the first reader waits on a request rather than on the
    # whole cohort's cached facts, and a cache the service cannot read fails while a human watches.
    # Kept despite the warmer, which builds the same span moments later on a thread: a warmer logs a
    # cache it cannot read and carries on, and this failure has to land while a human is watching.
    bundle = cache.bundle(default_window(spans))
    logging.info(
        "Serving %s repositories over %s weeks, %s unavailable; spans on offer: %s, refreshed every %ss",
        len(bundle.owners),
        bundle.weeks,
        len(bundle.unavailable),
        ", ".join(str(span) for span in spans),
        options.warm_interval,
    )
    uvicorn.run(app, host=options.host, port=options.port, log_level=options.logging.lower())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
