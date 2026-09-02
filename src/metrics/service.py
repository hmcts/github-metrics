"""Serve stored evidence over HTTP, so a browser can read what `metrics evidence` prints.

READ-ONLY AND OFFLINE ALWAYS. Every response is assembled by `evidence.offline_practice_report` from
the SQLite caches a `metrics collect` run wrote, and nothing here can reach GitHub: there is no
client, no session and no credential anywhere in this module. A window nobody has collected reports
itself through the report's own `unavailable` entries rather than as thinner data, which is the whole
reason the open pull-request state was made cacheable first. See architecture.md, "Scope boundaries",
for the dated reversal that admitted a service and a UI at all, and the boundaries it left standing:
no personal rankings, no cross-repository averaging, no combined team verdict, score or ordering.

Its own entry point, `metrics-serve`, rather than a `metrics` subcommand — it shares no argument with
the collection commands, and `cli.py` is edited heavily elsewhere.
"""

import logging
import threading
from argparse import ArgumentParser, ArgumentTypeError, Namespace
from collections import Counter
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from itertools import groupby
from operator import itemgetter
from pathlib import Path
from typing import Annotated, Self

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from pydantic import NonNegativeInt, PositiveInt, model_validator

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
    PracticeEvidenceReport,
    ReadinessLabel,
    ReportingWindow,
    RepositoryPracticeEvidence,
    RepositoryTrend,
)
from metrics.evidence import collected_through, offline_practice_report
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
repository at a short `period_days` is hundreds of cache loads under the one build lock. A request
that resolves above this count is refused with the count it asked for, so the caller names a cut
rather than being handed a truncated one under the name of the whole history.
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
    """

    repository: str
    team: str
    readiness: ReadinessLabel | None = None
    merged_pull_requests: NonNegativeInt | None = None
    direct_commits: NonNegativeInt | None = None
    currently_open: NonNegativeInt | None = None
    stale_open: NonNegativeInt | None = None
    finding_occurrences: NonNegativeInt | None = None
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
    place for it to drift. Only the two things the block cannot state are added — the owning team for
    a repository that could not be reported, and the contributor rows the actor section holds.
    """

    repository: str
    team: str
    evidence: RepositoryPracticeEvidence | None = None
    contributors: tuple[ContributorRow, ...] = ()
    detail: str | None = None

    @model_validator(mode="after")
    def validate_availability(self) -> Self:
        """Require either an evidence block or the reason this window has none."""
        if (self.evidence is None) == (self.detail is None):
            message = "a repository detail must carry either an evidence block or the reason it is unavailable"
            raise ValueError(message)
        return self


class ActorRow(EvidenceModel):
    """Name one person and how many repositories they contributed to in this window.

    A count of repositories and nothing else: the list it fills is ALPHABETICAL and carries no metric
    to order people by, which is the personal-ranking boundary this service is bound by.
    """

    login: str
    repositories: PositiveInt


class ActorDetail(EvidenceModel):
    """Carry one person's contract row beside the team owning each repository named in it.

    `actor` is the domain model unchanged, so the page reads the same per-repository rows the JSON
    carries — labels LISTED per repository and combined nowhere. `teams` is accounting the row cannot
    state on its own, so a repository link can carry its team without a second request.
    """

    actor: ActorReadiness
    teams: dict[str, str]


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


class WindowCache:
    """Hold one built bundle per span and per series cut, rebuilding on a collection or on age.

    ONE LOCK FOR EVERY SPAN, so two requests for a cold span build it once instead of both walking
    every configured repository's cached facts. Building under the lock makes a request for another
    span wait behind it, which is the accepted cost of never doing the same expensive build twice.
    """

    def __init__(self, configuration: Configuration, maximum_age: timedelta) -> None:
        self.configuration = configuration
        self.maximum_age = maximum_age
        self.lock = threading.Lock()
        self.bundles: dict[int, ReportBundle] = {}
        self.series: dict[tuple[str, int, int | None], TrendBundle] = {}

    def usable(self, built: CachedBuild, stamp: SourceStamp, reference: datetime) -> bool:
        """Decide whether something held still describes the caches it was built from."""
        return built.stamp == stamp and reference - built.built_at <= self.maximum_age

    def bundle(self, weeks: int) -> ReportBundle:
        """Return the bundle for one span, building it if there is no usable one held.

        The stamp is read outside the lock: it is two `stat` calls, and reading it under the lock
        would serialise every request behind whichever one is building.
        """
        reference = datetime.now(UTC)
        stamp = source_stamp(self.configuration)
        with self.lock:
            held = self.bundles.get(weeks)
            if held is not None and self.usable(held, stamp, reference):
                return held
            built = report_bundle(self.configuration, weeks, reference)
            self.bundles[weeks] = built
            return built

    def trend(self, repository: str, period_days: int, periods: int | None) -> RepositoryTrend:
        """Return one repository's series for one cut of it, building it if none is held.

        Keyed by the cut as well as the repository, because two cuts of the same history are two
        different series: reusing a 28-day series for a 7-day request would answer with periods four
        times the length of the ones asked for, and nothing in the response would say so.
        """
        reference = datetime.now(UTC)
        stamp = source_stamp(self.configuration)
        key = (repository, period_days, periods)
        with self.lock:
            held = self.series.get(key)
            if held is not None and self.usable(held, stamp, reference):
                # Reinsert so the key moves to the end: `retain` drops from the front, which makes
                # insertion order least-recently-read order and keeps a reread series out of the way.
                del self.series[key]
                self.series[key] = held
                return held.series
            built = TrendBundle(
                built_at=reference,
                stamp=stamp,
                series=repository_series(self.configuration, repository, period_days, periods, reference),
            )
            self.series.pop(key, None)
            self.series[key] = built
            self.retain()
            return built.series

    def retain(self) -> None:
        """Drop the least recently read series until no more than `MAXIMUM_HELD_SERIES` are held."""
        while len(self.series) > MAXIMUM_HELD_SERIES:
            del self.series[next(iter(self.series))]


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


def repository_row(bundle: ReportBundle, repository: str) -> RepositoryRow:
    """Summarise one repository for a list, saying why instead when the window has no evidence.

    The two branches partition the configured repositories exactly: `offline_practice_report` reports
    each one either as evidence or as unavailable, so a repository is in one map or the other.
    """
    evidence = bundle.repositories.get(repository)
    if evidence is None:
        # The team is read from configuration here, being the one fact about an unreportable
        # repository that survives having no evidence block to state it.
        return RepositoryRow(
            repository=repository,
            team=bundle.owners[repository],
            detail=bundle.unavailable[repository],
        )
    summary = evidence.open_pull_requests.summary
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
    )


def repository_detail(bundle: ReportBundle, repository: str) -> RepositoryDetail:
    """Carry one repository's evidence block and contributors, or the reason the window has none."""
    evidence = bundle.repositories.get(repository)
    if evidence is None:
        return RepositoryDetail(
            repository=repository,
            team=bundle.owners[repository],
            detail=bundle.unavailable[repository],
        )
    return RepositoryDetail(
        repository=repository,
        team=evidence.team,
        evidence=evidence,
        contributors=bundle.contributors.get(repository, ()),
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


def team_detail(bundle: ReportBundle, team: str) -> TeamDetail:
    """Carry one team's repository rows, its contributors, and the same counts the list shows."""
    repositories = bundle.teams[team]
    reported = reported_repositories(bundle, repositories)
    return TeamDetail(
        team=team,
        repositories=tuple(repository_row(bundle, repository) for repository in repositories),
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


def serve_repositories(bundle: Bundle) -> tuple[RepositoryRow, ...]:
    """List every configured repository in the reporting order, unreportable ones included."""
    return tuple(repository_row(bundle, repository) for repository in bundle.owners)


def serve_repository(repository: str, bundle: Bundle) -> RepositoryDetail:
    """Report one repository's whole evidence block, or why this window has none for it."""
    if repository not in bundle.owners:
        detail = f"repository is not configured: {repository}"
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=detail)
    return repository_detail(bundle, repository)


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
    """List everyone who contributed to a reported repository, alphabetically and never ranked."""
    return tuple(
        ActorRow(login=actor.actor_login, repositories=len(actor.repositories)) for actor in bundle.actors.values()
    )


def serve_actor(login: str, bundle: Bundle) -> ActorDetail:
    """Report one person's repositories, matched case-insensitively and spelled as the report spells it."""
    actor = bundle.actors.get(login.casefold())
    if actor is None:
        detail = f"nobody by that login contributed to a reported repository: {login}"
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=detail)
    return ActorDetail(
        actor=actor,
        teams={row.repository: bundle.owners[row.repository] for row in actor.repositories},
    )


def serve_teams(bundle: Bundle) -> tuple[TeamRow, ...]:
    """List every configured team with its repository count, its people, and its label counts."""
    return tuple(team_row(bundle, team) for team in bundle.teams)


def serve_team(team: str, bundle: Bundle) -> TeamDetail:
    """Report one team's repositories and contributors."""
    if team not in bundle.teams:
        detail = f"team is not configured: {team}"
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=detail)
    return team_detail(bundle, team)


def create_app(configuration: Configuration, cache: WindowCache) -> FastAPI:
    """Build the application, serving every route from the given cache and nothing else.

    Every route is registered with `response_model_exclude_none`, so the JSON reads exactly as
    `metrics evidence` prints it: an absent figure is an absent key, never a `null` a consumer has to
    tell apart from an observed zero.

    No CORS and no middleware: the Next.js server fetches these routes server-side, so no browser
    ever calls the service directly and a permissive origin policy would be inviting one to.
    """
    options = window_options(configuration.lookback.maximum_days)
    app = FastAPI(title="metrics evidence service", description=__doc__)
    app.state.service = ServiceState(
        configuration=configuration,
        cache=cache,
        options=options,
        default=default_window(options),
    )
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
    return parser.parse_args()


def positive_seconds(raw: str) -> int:
    """Parse a whole number of seconds, refusing one that would make every bundle born stale.

    Bounded here rather than left to `int`: a zero or negative age makes `usable` false for
    everything held, so every request rebuilds the whole cohort's report under the one build lock,
    which reads as a service that has simply stopped rather than as a mistyped flag.
    """
    seconds = int(raw)
    if seconds < 1:
        message = f"a bundle age must be at least one second, not {seconds}"
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
    app = create_app(configuration, cache)
    # Built before anything is listening, so the first reader waits on a request rather than on the
    # whole cohort's cached facts, and a cache the service cannot read fails while a human watches.
    bundle = cache.bundle(default_window(spans))
    logging.info(
        "Serving %s repositories over %s weeks, %s unavailable; spans on offer: %s",
        len(bundle.owners),
        bundle.weeks,
        len(bundle.unavailable),
        ", ".join(str(span) for span in spans),
    )
    uvicorn.run(app, host=options.host, port=options.port, log_level=options.logging.lower())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
