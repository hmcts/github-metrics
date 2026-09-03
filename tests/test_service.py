"""Test the read-only evidence service."""

import logging
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from metrics.behaviour import source_signature
from metrics.config import Configuration, LookbackConfiguration
from metrics.domain import (
    ActorReadiness,
    ActorRepositoryReadiness,
    AlertSeverity,
    BehaviourMetricSummary,
    CodeownersEvidence,
    CodeownersFile,
    CodeownersReport,
    CohortSummary,
    EvidenceSource,
    EvidenceUnavailable,
    FindingSeverity,
    MaintenanceReport,
    MergeGateEvidence,
    MergeGateReport,
    ObservationStatus,
    OpenAlertCount,
    OpenPullRequestReport,
    OpenPullRequestSummary,
    PracticeEvidenceReport,
    PracticeFinding,
    PullRequestRule,
    RateObservation,
    ReadinessAssessment,
    ReadinessCondition,
    ReadinessLabel,
    ReportingWindow,
    RepositoryPracticeEvidence,
    RepositoryTrend,
    SecurityAlertEvidence,
    SecurityAlertReport,
    SonarMeasures,
    SonarRating,
    SonarReport,
    SourceCoverage,
    StatusCheck,
    StatusChecksRule,
    TrendPeriod,
    TrendThroughput,
    UnreviewedSubstantialOutcome,
    WindowProvenance,
)
from metrics.service import (
    DEFAULT_BUNDLE_AGE_SECONDS,
    DEFAULT_PERIOD_DAYS,
    DEFAULT_WARM_INTERVAL_SECONDS,
    MAXIMUM_HELD_SERIES,
    MAXIMUM_PERIODS,
    NOT_ASSESSED,
    WEEKS_OPTIONS,
    ReportBundle,
    RepositoryDetail,
    WindowCache,
    contributor_rows,
    create_app,
    default_window,
    file_stamp,
    keep_warm,
    main,
    reporting_window,
    source_stamp,
    warm_spans,
    window_options,
)
from metrics.storage import StorageError, cache_direct_commit_facts, cache_pull_request_facts
from metrics.trend import NO_ENABLEMENT_DATE, SeriesRequest
from metrics.window import midnight

MAXIMUM_AGE = timedelta(seconds=DEFAULT_BUNDLE_AGE_SECONDS)
"""The bundle lifetime every test uses unless it is testing the lifetime itself."""

WARM_INTERVAL = timedelta(seconds=DEFAULT_WARM_INTERVAL_SECONDS)
"""How often the warmer wakes in a test, which is also the margin it refreshes within."""


def waited_for(condition: Callable[[], bool], timeout: float = 5.0) -> bool:
    """Spin until another thread has made the progress a condition names, or give up and say so.

    Polled rather than signalled because what the concurrency tests wait on is a thread reaching a
    lock, which the thread itself cannot announce: the registry's holder count is the announcement.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def starts_at() -> datetime:
    """Return the start of the window the fixture report covers."""
    return datetime(2026, 8, 1, tzinfo=UTC)


def ends_at() -> datetime:
    """Return the exclusive end of the window the fixture report covers."""
    return datetime(2026, 8, 29, tzinfo=UTC)


def configuration(tmp_path: Path, **overrides: object) -> Configuration:
    """Build a configuration owning three repositories across two teams."""
    loaded = Configuration.model_validate(
        {
            "version": 1,
            "organization": "hmcts",
            "database": tmp_path / "metrics.sqlite3",
            "teams": [
                {
                    "identifier": "crime",
                    "display_name": "Crime",
                    "repositories": ["cath-service", "other-service"],
                },
                {"identifier": "civil", "display_name": "Civil", "repositories": ["civil-service"]},
            ],
            # One repository anchored and one not, so a series and the reason there is none are both
            # reachable without a second configuration.
            "enablement": {"cath-service": "2026-06-01"},
        },
    )
    return loaded.model_copy(update=overrides)


def record_collection(settings: Configuration, edge: datetime) -> None:
    """Record both sources as covering every configured repository up to one instant.

    A collection is what `collected_through` reads, so a service test that wants an anchor has to
    leave one behind: the interval reaches far enough back that the longest span on offer is covered
    whole, and it carries no fact, because what is under test is the window rather than the figures.
    """
    for repository in ("cath-service", "other-service", "civil-service"):
        for source, write in (
            (EvidenceSource.PULL_REQUEST, cache_pull_request_facts),
            (EvidenceSource.COMMIT, cache_direct_commit_facts),
        ):
            write(
                settings.database,
                SourceCoverage(
                    organization=settings.organization,
                    repository=repository,
                    source=source,
                    query_hash=source_signature(source),
                    starts_at=edge - timedelta(days=400),
                    ends_at=edge,
                ),
                (),
                complete=True,
            )


def assessment() -> ReadinessAssessment:
    """Build an amber assessment carrying the one condition that held it below green.

    The two clear conditions are the pair the `informational` flag exists to keep apart: a check
    that was satisfied, and a rule the policy reports without judging.
    """
    return ReadinessAssessment(
        label=ReadinessLabel.AMBER,
        blocking=(
            ReadinessCondition(
                condition="approval-coverage-below-target",
                label=ReadinessLabel.AMBER,
                detail="approval-coverage is 50% (1 of 2), below the 90% target",
            ),
        ),
        caution=(),
        clear=(
            ReadinessCondition(
                condition="branch-protected",
                detail="the default branch main is protected",
            ),
            ReadinessCondition(
                condition="linear-history-not-required",
                detail="main does not require a linear history",
                informational=True,
            ),
        ),
    )


def finding() -> PracticeFinding:
    """Build one actor-level finding worth two occurrences."""
    return PracticeFinding(
        rule="unreviewed-merge",
        severity=FindingSeverity.HIGH,
        actor_login="Alice",
        occurrences=2,
        authored_merges=4,
        percentage=50.0,
        message="Alice: 2 of 4 merges had no independent human review (50%)",
        occurrences_by_size={"substantial": 2},
        pull_requests=(),
    )


def metrics() -> tuple[BehaviourMetricSummary, ...]:
    """Summarise one metric, as a repository block and an actor row both carry it."""
    return (
        BehaviourMetricSummary(
            metric="approval-coverage",
            summary=RateObservation(status=ObservationStatus.OBSERVED, numerator=1, denominator=2),
            classifications={"approved": 1, "unapproved": 1},
        ),
    )


def open_pull_requests() -> OpenPullRequestReport:
    """Build the stored open pull-request state, measured over a collection window of its own."""
    return OpenPullRequestReport(
        fetched_at=datetime(2026, 8, 29, 6, 0, tzinfo=UTC),
        starts_at=datetime(2026, 5, 31, tzinfo=UTC),
        ends_at=datetime(2026, 8, 29, tzinfo=UTC),
        summary=OpenPullRequestSummary(
            opened_in_window=9,
            closed_without_merge=2,
            currently_open=4,
            stale_open=1,
        ),
    )


def review_rule(count: int) -> PullRequestRule:
    """Return a pull-request rule requiring `count` approving reviews and nothing else."""
    return PullRequestRule(
        dismiss_stale_reviews_on_push=False,
        require_code_owner_review=False,
        require_last_push_approval=False,
        required_approving_review_count=count,
        required_review_thread_resolution=False,
    )


def checks_rule(*contexts: str) -> StatusChecksRule:
    """Return a status-check rule requiring the named contexts."""
    return StatusChecksRule(
        strict_required_status_checks_policy=False,
        required_status_checks=tuple(StatusCheck(context=context) for context in contexts),
    )


def protected_gate() -> MergeGateEvidence:
    """Return an observed gate on a protected branch requiring two approvals and three checks.

    Two pull-request rules of differing strictness and two status-check rulesets, so that a row
    reporting the strictest approval count and the whole context set is distinguishable from one
    reporting the first rule it found.
    """
    return MergeGateEvidence(
        branch="main",
        protected=True,
        pull_requests=(review_rule(1), review_rule(2)),
        status_checks=(checks_rule("build", "test"), checks_rule("lint")),
        restricts_deletions=True,
        blocks_force_pushes=True,
        rules_observed=True,
    )


def sonar_report(coverage: float | None) -> SonarReport:
    """Return a Sonar block whose measures carry that coverage, or none where SonarCloud reported none."""
    return SonarReport(
        fetched_at=ends_at(),
        measures=SonarMeasures(project_key="hmcts_cath-service", coverage=coverage),
    )


def sonar_security_report(
    rating: SonarRating | None = None,
    issues: int | None = None,
    hotspots: int | None = None,
) -> SonarReport:
    """Return a Sonar block whose measures carry the given security figures and nothing else."""
    return SonarReport(
        fetched_at=ends_at(),
        measures=SonarMeasures(
            project_key="hmcts_cath-service",
            security_rating=rating,
            security_issues=issues,
            security_hotspots=hotspots,
        ),
    )


def codeowners_report(*paths: str, recognised: bool = True) -> CodeownersReport:
    """Return a CODEOWNERS block reporting a file at each named path, or none at all."""
    return CodeownersReport(
        fetched_at=ends_at(),
        codeowners=CodeownersEvidence(
            files=tuple(CodeownersFile(path=path, size_bytes=120, recognised_by_github=recognised) for path in paths),
        ),
    )


def security_report() -> SecurityAlertReport:
    """Return a security block with one readable family, one clear family and one GitHub refused."""
    return SecurityAlertReport(
        fetched_at=ends_at(),
        alerts=SecurityAlertEvidence(
            dependabot=OpenAlertCount(open=3, by_severity={AlertSeverity.HIGH: 2, AlertSeverity.LOW: 1}),
            code_scanning=OpenAlertCount(open=0),
            secret_scanning=OpenAlertCount(detail="alerts are not readable with this token"),
        ),
    )


def practice_evidence(repository: str, team: str, **overrides: object) -> RepositoryPracticeEvidence:
    """Build one repository's practice evidence, overriding selected blocks."""
    evidence = RepositoryPracticeEvidence(
        repository=repository,
        team=team,
        starts_at=starts_at(),
        ends_at=ends_at(),
        provenance=WindowProvenance(offline=True, intervals_fetched=0),
        cohort=CohortSummary(merged=5, reported=4, excluded_authors={"renovate": 1}, direct_commits=2),
        assessment=assessment(),
        merge_gate=MergeGateReport(detail="no repository state has been collected; run metrics collect"),
        open_pull_requests=open_pull_requests(),
        security=SecurityAlertReport(detail="no repository state has been collected; run metrics collect"),
        codeowners=CodeownersReport(detail="no repository state has been collected; run metrics collect"),
        maintenance=MaintenanceReport(detail="no repository state has been collected; run metrics collect"),
        sonar=SonarReport(detail="no repository state has been collected; run metrics collect"),
        metrics=metrics(),
        behaviour=(finding(),),
    )
    return evidence.model_copy(update=overrides)


def actor_repository(
    repository: str,
    contributions: int,
    blocking: int = 0,
    readiness: ReadinessLabel | None = ReadinessLabel.AMBER,
) -> ActorRepositoryReadiness:
    """State one person's contribution to one repository, as the contract carries it."""
    return ActorRepositoryReadiness(
        readiness=readiness,
        repository=repository,
        contributions=contributions,
        blocking=blocking,
        metrics=metrics(),
    )


def practice_report() -> PracticeEvidenceReport:
    """Build the report every endpoint test reads.

    Three configured repositories: one fully reported, one reported without an assessment and with
    no open pull-request state stored, and one the window could not be covered for at all.
    """
    return PracticeEvidenceReport(
        organization="hmcts",
        repositories=(
            practice_evidence(
                "civil-service",
                "civil",
                assessment=None,
                open_pull_requests=OpenPullRequestReport(detail="open pull-request state was not collected"),
            ),
            practice_evidence("cath-service", "crime"),
        ),
        unavailable=(
            EvidenceUnavailable(
                repository="other-service",
                detail="cached pull_request evidence does not cover 2026-08-01T00:00Z to 2026-08-29T00:00Z",
            ),
        ),
        actors=(
            ActorReadiness(
                actor_login="Alice",
                repositories=(actor_repository("cath-service", 3, blocking=2), actor_repository("civil-service", 1)),
            ),
            ActorReadiness(actor_login="bob", repositories=(actor_repository("cath-service", 1),)),
        ),
    )


@dataclass
class Loader:
    """Stand in for the offline report loader, recording every assembly it was asked for."""

    calls: list[tuple[Configuration, ReportingWindow]] = field(default_factory=list)

    def __call__(self, configuration: Configuration, window: ReportingWindow) -> PracticeEvidenceReport:
        """Record what was asked for and return the one fixture report."""
        self.calls.append((configuration, window))
        return practice_report()

    @property
    def windows(self) -> list[ReportingWindow]:
        """Return the window of every assembly, in the order they were asked for."""
        return [window for _, window in self.calls]


def trend_period(index: int, merged: int, direct: int) -> TrendPeriod:
    """Build one whole period of a series, carrying the counts a throughput chart reads."""
    starts = datetime(2026, 6, 1, tzinfo=UTC) + timedelta(days=28 * (index - 1))
    return TrendPeriod(
        index=index,
        starts_at=starts,
        ends_at=starts + timedelta(days=28),
        provenance=WindowProvenance(offline=True, intervals_fetched=0),
        cohort=CohortSummary(merged=merged, reported=merged, excluded_authors={}, direct_commits=direct),
        throughput=TrendThroughput(
            merges=merged + direct,
            merged_pull_requests=merged,
            direct_commits=direct,
            active_contributors=2,
        ),
    )


def anchored_series(repository: str, enablement: datetime) -> RepositoryTrend:
    """Build the two-period series the trend endpoint tests read."""
    return RepositoryTrend(
        repository=repository,
        enablement_at=enablement,
        baseline=trend_period(1, 4, 1),
        periods=(trend_period(1, 6, 2), trend_period(2, 8, 0)),
    )


@dataclass
class Series:
    """Stand in for the offline series builder, recording every cut it was asked to make."""

    calls: list[tuple[str, SeriesRequest]] = field(default_factory=list)

    def __call__(
        self,
        configuration: Configuration,
        request: SeriesRequest,
        repository: str,
        enablement: datetime | None,
    ) -> RepositoryTrend:
        """Record the cut and answer with a series, or with the reason a repository has none."""
        _ = configuration
        self.calls.append((repository, request))
        if enablement is None:
            return RepositoryTrend(repository=repository, detail=NO_ENABLEMENT_DATE)
        return anchored_series(repository, enablement)

    @property
    def cuts(self) -> list[tuple[str, int, int | None]]:
        """Return what each call asked for: the repository, the period span, and the period count."""
        return [(repository, request.period_days, request.periods) for repository, request in self.calls]


@pytest.fixture
def loader(monkeypatch: pytest.MonkeyPatch) -> Loader:
    """Serve every window from the fixture report, so no test touches a database."""
    replacement = Loader()
    monkeypatch.setattr("metrics.service.offline_practice_report", replacement)
    return replacement


@pytest.fixture
def series(monkeypatch: pytest.MonkeyPatch) -> Series:
    """Cut every series from the fixture, so no trend test touches a database either."""
    replacement = Series()
    monkeypatch.setattr("metrics.service.repository_trend", replacement)
    return replacement


@pytest.fixture
def client(tmp_path: Path, loader: Loader, series: Series) -> Iterator[TestClient]:
    """Build a client over an application serving the fixture report."""
    _ = loader, series
    settings = configuration(tmp_path)
    with TestClient(create_app(settings, WindowCache(settings, MAXIMUM_AGE))) as connected:
        yield connected


Listing = Callable[..., dict[str, object]]
"""Serve the repository list over a report whose cath-service block carries the given overrides."""


@pytest.fixture
def listed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Listing:
    """Return the cath-service row a report carrying the blocks under test is listed with."""

    def row(**overrides: object) -> dict[str, object]:
        report = practice_report()
        replaced = tuple(
            practice_evidence(item.repository, item.team, **overrides) if item.repository == "cath-service" else item
            for item in report.repositories
        )
        amended = report.model_copy(update={"repositories": replaced})
        monkeypatch.setattr("metrics.service.offline_practice_report", lambda _configuration, _window: amended)
        settings = configuration(tmp_path)
        with TestClient(create_app(settings, WindowCache(settings, MAXIMUM_AGE))) as connected:
            listing: dict[str, dict[str, object]] = {
                item["repository"]: item for item in connected.get("/repositories").json()
            }
        return listing["cath-service"]

    return row


def test_the_spans_on_offer_exclude_any_the_configured_maximum_forbids() -> None:
    assert window_options(365) == WEEKS_OPTIONS
    assert window_options(90) == (1, 4, 8, 12)
    assert window_options(7) == (1,)
    assert window_options(3) == ()


def test_the_default_span_falls_back_to_the_longest_one_still_on_offer() -> None:
    assert default_window(WEEKS_OPTIONS) == 4
    assert default_window((1,)) == 1


def test_a_window_ends_at_the_anchor_it_was_resolved_for() -> None:
    """Take the resolved anchor as given: where the caches end is the caller's decision to make."""
    window = reporting_window(4, datetime(2026, 8, 29, tzinfo=UTC))

    assert window.starts_at == datetime(2026, 8, 1, tzinfo=UTC)
    assert window.ends_at == datetime(2026, 8, 29, tzinfo=UTC)


def test_a_cache_file_that_does_not_exist_yet_stamps_as_absent(tmp_path: Path) -> None:
    assert file_stamp(tmp_path / "metrics.sqlite3") is None
    assert source_stamp(configuration(tmp_path)) == (None, None)


def test_a_written_cache_file_stamps_by_its_size_and_modification_time(tmp_path: Path) -> None:
    database = tmp_path / "metrics.sqlite3"
    database.write_bytes(b"cached")

    stamped = file_stamp(database)

    assert stamped is not None
    assert stamped[1] == len(b"cached")


def test_one_span_is_assembled_once_and_then_reused(tmp_path: Path, loader: Loader) -> None:
    settings = configuration(tmp_path)
    cache = WindowCache(settings, MAXIMUM_AGE)

    first = cache.bundle(4)
    second = cache.bundle(4)

    assert first is second
    assert len(loader.windows) == 1


def test_two_requests_for_a_cold_span_assemble_it_once_between_them(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Build once under the span's own lock, so a cold span is not walked twice at once.

    The class promises this and every other test is single-threaded, so a lock dropped or narrowed to
    the dictionary write alone would let a thundering herd on a cold span run one whole cohort load
    per waiting request with nothing failing. The second reader must also come away with what the
    builder produced, which is what the check inside the lock is for.
    """
    entered = threading.Event()
    released = threading.Event()
    loader = Loader()

    def blocking(settings: Configuration, window: ReportingWindow) -> PracticeEvidenceReport:
        entered.set()
        released.wait(timeout=5)
        return loader(settings, window)

    monkeypatch.setattr("metrics.service.offline_practice_report", blocking)
    cache = WindowCache(configuration(tmp_path), MAXIMUM_AGE)
    built: list[ReportBundle] = []
    threads = [threading.Thread(target=lambda: built.append(cache.bundle(4))) for _ in range(2)]

    threads[0].start()
    assert entered.wait(timeout=5)
    threads[1].start()
    # Both waited for in turn rather than started together: the second request must reach the span's
    # lock while the first is still inside it, or it would answer off the check outside the lock and
    # the contention this test is about would never happen.
    assert waited_for(lambda: cache.builds.holders[4] == 2)
    released.set()
    for thread in threads:
        thread.join(timeout=5)

    assert len(loader.windows) == 1
    assert built[0] is built[1]


def test_a_cold_span_builds_beside_another_span_rather_than_behind_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hold one lock per span, so a cold 26-week build is not every other request's wait.

    The 26-week build is released BY the 1-week build, so one lock for every span cannot pass this
    test by merely being slower: the short span would be stuck behind the long one, the long one
    would come out of its wait on the timeout instead, and `waited` would say so.
    """
    entered = threading.Event()
    released = threading.Event()
    waited: list[bool] = []
    loader = Loader()

    def paired(settings: Configuration, window: ReportingWindow) -> PracticeEvidenceReport:
        if window.ends_at - window.starts_at == timedelta(weeks=26):
            entered.set()
            waited.append(released.wait(timeout=5))
        else:
            released.set()
        return loader(settings, window)

    monkeypatch.setattr("metrics.service.offline_practice_report", paired)
    cache = WindowCache(configuration(tmp_path), MAXIMUM_AGE)
    long_span = threading.Thread(target=lambda: cache.bundle(26))
    long_span.start()
    assert entered.wait(timeout=5)

    short = cache.bundle(1)
    long_span.join(timeout=5)

    assert waited == [True]
    assert short.weeks == 1
    assert cache.bundles[26].weeks == 26


def test_two_requests_for_a_cold_cut_of_one_series_make_it_once_between_them(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cut once under that cut's own lock, for the reason a span is built once under its own."""
    entered = threading.Event()
    released = threading.Event()
    replacement = Series()

    def blocking(
        settings: Configuration,
        request: SeriesRequest,
        repository: str,
        enablement: datetime | None,
    ) -> RepositoryTrend:
        entered.set()
        released.wait(timeout=5)
        return replacement(settings, request, repository, enablement)

    monkeypatch.setattr("metrics.service.repository_trend", blocking)
    cache = WindowCache(configuration(tmp_path), MAXIMUM_AGE)
    cut: list[RepositoryTrend] = []
    threads = [threading.Thread(target=lambda: cut.append(cache.trend("cath-service", 28, None))) for _ in range(2)]

    threads[0].start()
    assert entered.wait(timeout=5)
    threads[1].start()
    assert waited_for(lambda: cache.cuts.holders["cath-service", 28, None] == 2)
    released.set()
    for thread in threads:
        thread.join(timeout=5)

    assert replacement.cuts == [("cath-service", 28, None)]
    assert cut[0] is cut[1]


@pytest.mark.usefixtures("series")
def test_the_series_bound_holds_when_every_cut_is_made_at_once(tmp_path: Path) -> None:
    """Keep the eviction order under one lock, which is the state no single cut's lock covers.

    Every other eviction test is single-threaded, so a bound maintained under the per-cut locks
    alone would hold there and let concurrent readers trim against an order another thread was
    reordering — dropping a series somebody had just read, or holding more than the bound.
    """
    cache = WindowCache(configuration(tmp_path), MAXIMUM_AGE)
    spans = range(1, MAXIMUM_HELD_SERIES * 2 + 1)
    threads = [threading.Thread(target=partial(cache.trend, "cath-service", days, 5)) for days in spans]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert len(cache.series) == MAXIMUM_HELD_SERIES


def test_no_lock_is_held_for_a_span_or_a_cut_nobody_is_reading(
    tmp_path: Path,
    loader: Loader,
    series: Series,
) -> None:
    """Drop each lock once nobody holds or waits for it, so the registries do not grow.

    A series key carries the cut a request named, so the keys are effectively unbounded — the reason
    `MAXIMUM_HELD_SERIES` bounds the series themselves, and a registry that only ever inserted would
    keep a lock per cut anybody ever asked for.
    """
    _ = loader, series
    cache = WindowCache(configuration(tmp_path), MAXIMUM_AGE)

    cache.bundle(4)
    cache.trend("cath-service", 28, None)

    assert cache.builds.locks == {}
    assert cache.builds.holders == Counter()
    assert cache.cuts.locks == {}
    assert cache.cuts.holders == Counter()


def test_no_lock_is_held_for_a_span_whose_build_raised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop the lock on the way out of a build that failed, not only one that finished.

    The failure path is the one that matters for the registries: an unreadable cache is exactly what
    a repeated request retries, and a count left standing per failed build would keep a lock for
    every span and every cut anybody ever asked for — the accumulation the reference counting exists
    to prevent, and one no successful build would ever show.
    """

    def failing(settings: Configuration, window: ReportingWindow) -> PracticeEvidenceReport:
        _ = settings, window
        message = "the cache is not a database"
        raise StorageError(message)

    monkeypatch.setattr("metrics.service.offline_practice_report", failing)
    cache = WindowCache(configuration(tmp_path), MAXIMUM_AGE)

    with pytest.raises(StorageError):
        cache.bundle(4)
    # Swallowed by the warmer, which is the other caller that can leave a build part-way.
    warm_spans(cache, (4,), WARM_INTERVAL)

    assert cache.bundles == {}
    assert cache.builds.locks == {}
    assert cache.builds.holders == Counter()


def test_reading_the_caches_to_build_a_bundle_does_not_invalidate_it(tmp_path: Path) -> None:
    """Hold a bundle built from a REAL cache, not a stubbed loader, across the next request.

    The build reads every configured repository's coverage, and a read that stamped `accessed_at`
    would write to the cache file — moving the modification time and size the held bundle is compared
    against, so its own build would invalidate it and every request would rebuild the whole cohort
    for itself. `test_one_span_is_assembled_once_and_then_reused` cannot see that: its loader
    never opens the cache at all.
    """
    settings = configuration(tmp_path)
    record_collection(settings, midnight(datetime.now(UTC)) - timedelta(days=1))
    cache = WindowCache(settings, MAXIMUM_AGE)

    first = cache.bundle(4)
    second = cache.bundle(4)

    assert first is second
    assert sorted(first.repositories) == ["cath-service", "civil-service", "other-service"]


def test_each_span_is_assembled_over_its_own_window(tmp_path: Path, loader: Loader) -> None:
    settings = configuration(tmp_path)
    cache = WindowCache(settings, MAXIMUM_AGE)

    spans = tuple(cache.bundle(weeks).window for weeks in (1, 26))

    assert loader.windows == list(spans)
    assert spans[0].ends_at - spans[0].starts_at == timedelta(weeks=1)
    assert spans[1].ends_at - spans[1].starts_at == timedelta(weeks=26)


def test_a_collection_landing_after_a_bundle_was_built_rebuilds_it(tmp_path: Path, loader: Loader) -> None:
    settings = configuration(tmp_path)
    cache = WindowCache(settings, MAXIMUM_AGE)
    first = cache.bundle(4)

    settings.database.write_bytes(b"collected")
    second = cache.bundle(4)

    assert second is not first
    assert len(loader.windows) == 2


def test_a_bundle_older_than_the_configured_maximum_age_is_rebuilt(tmp_path: Path, loader: Loader) -> None:
    settings = configuration(tmp_path)
    cache = WindowCache(settings, MAXIMUM_AGE)
    first = cache.bundle(4)
    # Aged in place rather than by waiting: the rebuild must depend on the bundle's own age.
    cache.bundles[4] = replace(first, built_at=first.built_at - timedelta(seconds=DEFAULT_BUNDLE_AGE_SECONDS * 2))

    second = cache.bundle(4)

    assert second is not first
    assert len(loader.windows) == 2


def test_a_warm_builds_a_span_nothing_is_holding(tmp_path: Path, loader: Loader) -> None:
    settings = configuration(tmp_path)
    cache = WindowCache(settings, MAXIMUM_AGE)

    assert cache.warm(4, WARM_INTERVAL) is True
    assert cache.bundles[4].weeks == 4
    assert len(loader.windows) == 1


def test_a_warm_leaves_a_bundle_that_will_outlive_the_next_wake_alone(tmp_path: Path, loader: Loader) -> None:
    """Return without building, so a warmer that wakes every five minutes is not a rebuild loop."""
    settings = configuration(tmp_path)
    cache = WindowCache(settings, MAXIMUM_AGE)
    first = cache.bundle(4)

    assert cache.warm(4, WARM_INTERVAL) is False
    assert cache.bundles[4] is first
    assert len(loader.windows) == 1


def test_a_warm_rebuilds_a_bundle_that_would_go_stale_before_the_next_wake(tmp_path: Path, loader: Loader) -> None:
    """Refresh inside the margin, which `bundle` cannot: it serves what is still usable.

    The bundle aged here is one a reader would be handed as it stands, and that is the point — by
    the warmer's next wake it would have expired, and whoever asked for it then would have waited
    for the whole cohort's cached facts. A warmer written on top of `bundle` would refresh nothing
    until exactly that had happened.
    """
    settings = configuration(tmp_path)
    cache = WindowCache(settings, MAXIMUM_AGE)
    first = cache.bundle(4)
    # A second inside the margin rather than outside it: still usable, and not usable for long.
    aged = replace(first, built_at=first.built_at - MAXIMUM_AGE + WARM_INTERVAL - timedelta(seconds=1))
    cache.bundles[4] = aged

    assert cache.bundle(4) is aged
    assert cache.warm(4, WARM_INTERVAL) is True
    assert cache.bundles[4] is not aged
    assert len(loader.windows) == 2


def test_a_warm_leaves_a_bundle_alone_a_second_outside_the_margin(tmp_path: Path, loader: Loader) -> None:
    """Refresh INSIDE the margin and no further, so the margin is a horizon rather than a direction.

    The mirror of the test above, one second the other side of the same edge: this bundle expires
    just after the next wake rather than just before it, so the wake that finds it leaves it and the
    one after rebuilds it. Without this the margin could be widened — to twice the interval, or to
    the age itself — with every other warm test still green and the warmer rebuilding every span on
    every wake, which is the fault `main` refuses when the interval is at or above the age.
    """
    settings = configuration(tmp_path)
    cache = WindowCache(settings, MAXIMUM_AGE)
    first = cache.bundle(4)
    aged = replace(first, built_at=first.built_at - MAXIMUM_AGE + WARM_INTERVAL + timedelta(seconds=1))
    cache.bundles[4] = aged

    assert cache.warm(4, WARM_INTERVAL) is False
    assert cache.bundles[4] is aged
    assert len(loader.windows) == 1


def test_a_collection_landing_after_a_span_was_warmed_is_warmed_in(tmp_path: Path, loader: Loader) -> None:
    """Refresh on a moved stamp as a request does, so a wake after a collection lands rebuilds."""
    settings = configuration(tmp_path)
    cache = WindowCache(settings, MAXIMUM_AGE)
    cache.warm(4, WARM_INTERVAL)

    settings.database.write_bytes(b"collected")

    assert cache.warm(4, WARM_INTERVAL) is True
    assert len(loader.windows) == 2


def test_two_warms_of_a_cold_span_build_it_once_between_them(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Check inside the span's build lock as `bundle` does, so a warm beside a build waits for it.

    Two warms is not the shape this arrives in — a reader's build and the wake that lands on it is —
    but both reach the same lock, and whichever gets there second must come away with what the first
    produced rather than walking the whole cohort's cached facts again.
    """
    entered = threading.Event()
    released = threading.Event()
    loader = Loader()

    def blocking(settings: Configuration, window: ReportingWindow) -> PracticeEvidenceReport:
        entered.set()
        released.wait(timeout=5)
        return loader(settings, window)

    monkeypatch.setattr("metrics.service.offline_practice_report", blocking)
    cache = WindowCache(configuration(tmp_path), MAXIMUM_AGE)
    warmed: list[bool] = []
    threads = [threading.Thread(target=lambda: warmed.append(cache.warm(4, WARM_INTERVAL))) for _ in range(2)]

    threads[0].start()
    assert entered.wait(timeout=5)
    threads[1].start()
    assert waited_for(lambda: cache.builds.holders[4] == 2)
    released.set()
    for thread in threads:
        thread.join(timeout=5)

    assert len(loader.windows) == 1
    assert sorted(warmed) == [False, True]


def test_the_warmer_builds_every_span_on_offer_and_leaves_a_built_one_alone(
    tmp_path: Path,
    loader: Loader,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Build the four cold spans, and skip the one `main` built before anything was listening.

    Only the default span was ever built ahead of a reader, so the other four were cold until
    somebody asked for one — and that reader paid for the whole cohort. This is the pass that
    removes the wait.
    """
    caplog.set_level(logging.INFO)
    settings = configuration(tmp_path)
    cache = WindowCache(settings, MAXIMUM_AGE)
    default = cache.bundle(4)
    stopping = threading.Event()
    stopping.set()

    keep_warm(cache, WEEKS_OPTIONS, WARM_INTERVAL, stopping)

    assert sorted(cache.bundles) == sorted(WEEKS_OPTIONS)
    assert cache.bundles[4] is default
    assert len(loader.windows) == len(WEEKS_OPTIONS)
    assert "Warmed the 26-week window" in caplog.text
    assert "Warmed the 4-week window" not in caplog.text


def test_a_span_that_cannot_be_built_costs_a_log_line_rather_than_the_warmer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Carry on to the next span: a cache unreadable now may be readable at the next wake.

    A thread that died on one unreadable file would take every later refresh with it, leaving a
    service that looks warm, serves whatever it happened to hold, and never says why.
    """
    caplog.set_level(logging.INFO)
    loader = Loader()

    def failing(settings: Configuration, window: ReportingWindow) -> PracticeEvidenceReport:
        if window.ends_at - window.starts_at == timedelta(weeks=1):
            message = "the cache is not a database"
            raise StorageError(message)
        return loader(settings, window)

    monkeypatch.setattr("metrics.service.offline_practice_report", failing)
    cache = WindowCache(configuration(tmp_path), MAXIMUM_AGE)
    stopping = threading.Event()
    stopping.set()

    keep_warm(cache, (1, 4), WARM_INTERVAL, stopping)

    assert sorted(cache.bundles) == [4]
    assert "Could not warm the 1-week window" in caplog.text
    assert "Warmed the 4-week window" in caplog.text


def test_the_warmer_refreshes_on_the_interval_until_it_is_stopped(tmp_path: Path, loader: Loader) -> None:
    """Keep waking after the first pass, and come out of the wait the moment the event is set.

    The loop is what keeps a span warm once a collection has landed on it; the event is what keeps a
    closed application from leaving a thread behind. A sleep in place of the wait would pass the
    first half of this and hold every shutdown for the rest of an interval.
    """
    settings = configuration(tmp_path)
    # An age no bundle outlives, so every pass rebuilds and the passes can be counted.
    cache = WindowCache(settings, timedelta(microseconds=1))
    stopping = threading.Event()
    warmer = threading.Thread(target=keep_warm, args=(cache, (4,), timedelta(milliseconds=10), stopping))

    warmer.start()
    assert waited_for(lambda: len(loader.windows) >= 2)
    stopping.set()
    warmer.join(timeout=5)

    assert not warmer.is_alive()


def test_the_application_warms_every_span_while_it_serves_and_stops_when_it_closes(
    tmp_path: Path,
    loader: Loader,
) -> None:
    """Start the warmer with the application and stop it with the application.

    A test that builds an application and closes it must not leave a thread refreshing a cache
    nobody is reading, which is what the lifespan handler and the stopping event are between them.
    """
    _ = loader
    settings = configuration(tmp_path)
    cache = WindowCache(settings, MAXIMUM_AGE)

    with TestClient(create_app(settings, cache, warm_interval=WARM_INTERVAL)) as connected:
        assert connected.get("/healthz").status_code == 200
        assert waited_for(lambda: sorted(cache.bundles) == sorted(WEEKS_OPTIONS))

    assert [thread for thread in threading.enumerate() if thread.name == "window-warmer"] == []


def test_an_application_given_no_interval_starts_no_warmer(tmp_path: Path, loader: Loader) -> None:
    """Serve every route and build a span when something asks for one, without a thread of its own."""
    settings = configuration(tmp_path)
    cache = WindowCache(settings, MAXIMUM_AGE)

    with TestClient(create_app(settings, cache)) as connected:
        assert connected.get("/healthz").status_code == 200

    assert cache.bundles == {}
    assert loader.windows == []


def test_the_default_warm_interval_refreshes_a_span_before_it_can_go_stale() -> None:
    """The interval is the margin, so an interval at or above the age refreshes only after a wait."""
    assert DEFAULT_WARM_INTERVAL_SECONDS < DEFAULT_BUNDLE_AGE_SECONDS


def test_every_span_ends_where_the_collection_ends(tmp_path: Path) -> None:
    """Anchor every span at the collection's edge, and report the repositories it covers.

    The whole point of the anchor: with the window ending at today's midnight, the last hours of
    yesterday are uncovered and every repository reports as unavailable the day after a run. Built
    from the real offline report rather than the fixture one, because what the anchor buys is the
    coverage check passing, which the fixture loader cannot show.
    """
    settings = configuration(tmp_path)
    edge = midnight(datetime.now(UTC)) - timedelta(days=1)
    record_collection(settings, edge)
    cache = WindowCache(settings, MAXIMUM_AGE)

    bundles = [cache.bundle(weeks) for weeks in WEEKS_OPTIONS]

    assert [bundle.window.ends_at for bundle in bundles] == [edge] * len(WEEKS_OPTIONS)
    assert [bundle.collected_through for bundle in bundles] == [edge] * len(WEEKS_OPTIONS)
    assert [sorted(bundle.repositories) for bundle in bundles] == [
        ["cath-service", "civil-service", "other-service"],
    ] * len(WEEKS_OPTIONS)
    assert [dict(bundle.unavailable) for bundle in bundles] == [{}] * len(WEEKS_OPTIONS)


def test_a_cache_nobody_has_collected_into_still_resolves_a_window(tmp_path: Path) -> None:
    """Fall back to today's midnight when there is no collection, and report the honest answer.

    A cold cache covers nothing, so every repository is unavailable — which is what it was before
    the anchor existed, and the state the notice on every page exists to explain.
    """
    settings = configuration(tmp_path)
    cache = WindowCache(settings, MAXIMUM_AGE)

    bundle = cache.bundle(4)

    assert bundle.window.ends_at == midnight(datetime.now(UTC))
    assert bundle.collected_through is None
    assert sorted(bundle.unavailable) == ["cath-service", "civil-service", "other-service"]
    assert bundle.repositories == {}


def test_the_overview_names_the_collection_its_window_is_anchored_to(tmp_path: Path, loader: Loader) -> None:
    _ = loader
    settings = configuration(tmp_path)
    edge = midnight(datetime.now(UTC)) - timedelta(days=1)
    record_collection(settings, edge)

    with TestClient(create_app(settings, WindowCache(settings, MAXIMUM_AGE))) as connected:
        body = connected.get("/overview").json()

    assert body["collected_through"].startswith(f"{edge:%Y-%m-%dT00:00:00}")
    assert body["ends_at"].startswith(f"{edge:%Y-%m-%dT00:00:00}")


def test_the_overview_omits_the_collection_when_there_has_been_none(client: TestClient) -> None:
    """Absent rather than null, as every other unobserved figure is."""
    assert "collected_through" not in client.get("/overview").json()


@pytest.mark.parametrize(("age", "stale"), [(timedelta(days=7), False), (timedelta(days=9), True)])
def test_the_published_collection_state_says_when_the_last_run_is_too_old(
    tmp_path: Path,
    loader: Loader,
    age: timedelta,
    *,
    stale: bool,
) -> None:
    """Measure staleness against the configured cadence, which defaults to one missed weekly run."""
    _ = loader
    settings = configuration(tmp_path)
    edge = midnight(datetime.now(UTC) - age)
    record_collection(settings, edge)

    with TestClient(create_app(settings, WindowCache(settings, MAXIMUM_AGE))) as connected:
        body = connected.get("/windows").json()

    assert body["collection_stale"] is stale
    assert body["collected_through"].startswith(f"{edge:%Y-%m-%dT00:00:00}")


def test_the_configured_cadence_is_what_staleness_is_measured_against(tmp_path: Path, loader: Loader) -> None:
    """Answer against `lookback.stale_collection_days`, not against the eight days it defaults to.

    A three-day-old collection is current at the default and stale for an estate collecting daily.
    Nothing else in the suite moves this key off its default, so a hardcoded eight would pass every
    other test and quietly disconnect the knob from the decision it configures.
    """
    _ = loader
    settings = configuration(tmp_path)
    daily = settings.model_copy(
        update={"lookback": settings.lookback.model_copy(update={"stale_collection_days": 2})},
    )
    record_collection(daily, midnight(datetime.now(UTC) - timedelta(days=3)))

    with TestClient(create_app(settings, WindowCache(settings, MAXIMUM_AGE))) as weekly:
        assert weekly.get("/windows").json()["collection_stale"] is False
    with TestClient(create_app(daily, WindowCache(daily, MAXIMUM_AGE))) as connected:
        assert connected.get("/windows").json()["collection_stale"] is True


def test_a_series_is_cut_against_the_collection_rather_than_against_now(tmp_path: Path, series: Series) -> None:
    """Cut the periods the caches cover, so a trailing period is never one nobody collected."""
    settings = configuration(tmp_path)
    edge = midnight(datetime.now(UTC)) - timedelta(days=3)
    record_collection(settings, edge)
    cache = WindowCache(settings, MAXIMUM_AGE)

    cache.trend("cath-service", 28, None)

    assert [request.reference for _, request in series.calls] == [edge]


def test_the_service_reports_that_it_is_up_and_what_it_was_configured_for(client: TestClient) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "organization": "hmcts"}


def test_the_spans_on_offer_are_published_with_the_default(client: TestClient) -> None:
    response = client.get("/windows")

    assert response.json() == {
        "options": list(WEEKS_OPTIONS),
        "default": 4,
        "trend_periods": MAXIMUM_PERIODS,
        # Nothing has been collected into the fixture cache, so there is no instant to name and the
        # collection is stale: there is no run to be current.
        "collection_stale": True,
    }


def test_the_published_trend_cut_is_one_the_trend_endpoint_serves(client: TestClient, series: Series) -> None:
    """The cut a client reads off `/windows` must be a cut this service answers.

    Published rather than restated in the client because a series is refused, not truncated, when it
    resolves above `MAXIMUM_PERIODS`: at a one-day period every repository enabled more than 26 days
    ago would be refused, and the only caller that asks for a series would get no section at all.
    """
    cut = client.get("/windows").json()["trend_periods"]

    response = client.get("/repositories/cath-service/trend", params={"period_days": 1, "periods": cut})

    assert response.status_code == 200
    assert series.cuts == [("cath-service", 1, MAXIMUM_PERIODS)]


def test_a_configuration_refusing_long_windows_offers_fewer_spans(tmp_path: Path, loader: Loader) -> None:
    _ = loader
    settings = configuration(tmp_path, lookback=LookbackConfiguration(maximum_days=7))
    with TestClient(create_app(settings, WindowCache(settings, MAXIMUM_AGE))) as connected:
        response = connected.get("/windows")

    assert response.json() == {
        "options": [1],
        "default": 1,
        "trend_periods": MAXIMUM_PERIODS,
        "collection_stale": True,
    }


def test_the_overview_counts_the_population_the_window_covers(client: TestClient) -> None:
    body = client.get("/overview").json()

    assert body["organization"] == "hmcts"
    assert body["weeks"] == 4
    assert body["repositories"] == 3
    assert body["unavailable"] == 1
    assert body["teams"] == 2
    assert body["actors"] == 2
    assert body["merged_pull_requests"] == 8
    assert body["direct_commits"] == 4


def test_the_overview_distributes_labels_without_combining_them(client: TestClient) -> None:
    labels = client.get("/overview").json()["labels"]

    assert labels == {"green": 0, "amber": 1, "red": 0, "cannot_assess": 0, NOT_ASSESSED: 1}


def test_the_overview_names_the_window_the_figures_were_measured_over(client: TestClient) -> None:
    body = client.get("/overview").json()

    assert body["starts_at"].startswith(f"{reporting_window(4, midnight(datetime.now(UTC))).starts_at:%Y-%m-%d}")


def test_every_configured_repository_is_listed_in_the_reporting_order(client: TestClient) -> None:
    rows = client.get("/repositories").json()

    assert [row["repository"] for row in rows] == ["civil-service", "cath-service", "other-service"]
    assert [row["team"] for row in rows] == ["civil", "crime", "crime"]


def test_a_listed_repository_carries_its_label_counts_and_findings(client: TestClient) -> None:
    rows = {row["repository"]: row for row in client.get("/repositories").json()}

    assert rows["cath-service"]["readiness"] == "amber"
    assert rows["cath-service"]["merged_pull_requests"] == 4
    assert rows["cath-service"]["direct_commits"] == 2
    assert rows["cath-service"]["currently_open"] == 4
    assert rows["cath-service"]["stale_open"] == 1
    assert rows["cath-service"]["finding_occurrences"] == 2


def test_an_unassessed_repository_omits_the_label_rather_than_inventing_one(client: TestClient) -> None:
    rows = {row["repository"]: row for row in client.get("/repositories").json()}

    assert "readiness" not in rows["civil-service"]


def test_uncollected_open_pull_request_state_is_absent_rather_than_zero(client: TestClient) -> None:
    rows = {row["repository"]: row for row in client.get("/repositories").json()}

    assert "currently_open" not in rows["civil-service"]
    assert "stale_open" not in rows["civil-service"]


def test_a_repository_the_window_cannot_cover_is_listed_with_the_reason(client: TestClient) -> None:
    rows = {row["repository"]: row for row in client.get("/repositories").json()}

    assert rows["other-service"]["detail"].startswith("cached pull_request evidence does not cover")
    assert "merged_pull_requests" not in rows["other-service"]


def test_a_repository_the_window_cannot_cover_states_none_of_the_four_estate_figures(client: TestClient) -> None:
    """Leave every derived field absent where there is no evidence block, as the counts beside it are."""
    row = {item["repository"]: item for item in client.get("/repositories").json()}["other-service"]

    assert "required_approving_reviews" not in row
    assert "required_status_checks" not in row
    assert "unreviewed_substantial" not in row
    assert "sonar_coverage" not in row


def test_an_uncollected_merge_gate_leaves_both_gate_figures_unknown(client: TestClient) -> None:
    """Report nothing for a gate nobody collected: 0 would read as a repository requiring nothing."""
    row = {item["repository"]: item for item in client.get("/repositories").json()}["cath-service"]

    assert "required_approving_reviews" not in row
    assert "required_status_checks" not in row


def test_a_repository_the_policy_graded_nothing_for_omits_the_unreviewed_verdict(client: TestClient) -> None:
    row = {item["repository"]: item for item in client.get("/repositories").json()}["cath-service"]

    assert "unreviewed_substantial" not in row


def test_a_repository_with_no_sonar_measures_omits_its_coverage_rather_than_reading_zero(
    client: TestClient,
) -> None:
    row = {item["repository"]: item for item in client.get("/repositories").json()}["cath-service"]

    assert "sonar_coverage" not in row


def test_a_listed_repository_states_what_its_merge_gate_requires(listed: Listing) -> None:
    row = listed(merge_gate=MergeGateReport(fetched_at=ends_at(), gate=protected_gate()))

    assert row["required_approving_reviews"] == 2
    assert row["required_status_checks"] == 3


def test_an_unprotected_default_branch_requires_nothing_rather_than_being_unknown(listed: Listing) -> None:
    """Count a branch anybody can push to as requiring nothing, whatever rules ride with it.

    The gate is reported with rules attached and unprotected at once, which the collector does not
    produce, because the precedence is what is under test: protection is read before the rules are.
    """
    unprotected = protected_gate().model_copy(update={"protected": False})
    row = listed(merge_gate=MergeGateReport(fetched_at=ends_at(), gate=unprotected))

    assert row["required_approving_reviews"] == 0
    assert row["required_status_checks"] == 0


def test_a_protected_branch_whose_rules_were_withheld_is_unknown_rather_than_unrequired(listed: Listing) -> None:
    """Withhold both figures where GitHub disclosed no rules, rather than blaming the permission gap."""
    withheld = protected_gate().model_copy(update={"rules_observed": False})
    row = listed(merge_gate=MergeGateReport(fetched_at=ends_at(), gate=withheld))

    assert "required_approving_reviews" not in row
    assert "required_status_checks" not in row


def test_a_protected_branch_with_no_rules_at_all_requires_nothing(listed: Listing) -> None:
    bare = protected_gate().model_copy(update={"pull_requests": (), "status_checks": ()})
    row = listed(merge_gate=MergeGateReport(fetched_at=ends_at(), gate=bare))

    assert row["required_approving_reviews"] == 0
    assert row["required_status_checks"] == 0


@pytest.mark.parametrize("outcome", list(UnreviewedSubstantialOutcome))
def test_a_listed_repository_carries_the_policys_verdict_on_unreviewed_substantial_merging(
    listed: Listing,
    outcome: UnreviewedSubstantialOutcome,
) -> None:
    row = listed(unreviewed_substantial=outcome)

    assert row["unreviewed_substantial"] == outcome.value


def test_a_listed_repository_carries_the_coverage_sonar_measured(listed: Listing) -> None:
    row = listed(merge_gate=MergeGateReport(fetched_at=ends_at(), gate=protected_gate()), sonar=sonar_report(81.5))

    assert row["sonar_coverage"] == 81.5


def test_a_sonar_project_that_reported_no_coverage_leaves_the_figure_absent(listed: Listing) -> None:
    """Separate a project SonarCloud measured no coverage for from one it measured none of at all."""
    row = listed(sonar=sonar_report(None))

    assert "sonar_coverage" not in row


def test_a_project_covering_none_of_its_lines_is_served_as_zero_rather_than_omitted(listed: Listing) -> None:
    """Keep 0% a measurement: the donut bands it red, and omitting it would read as unmeasured."""
    row = listed(sonar=sonar_report(0))

    assert row["sonar_coverage"] == 0


def test_a_listed_repository_counts_the_codeowners_files_it_holds(listed: Listing) -> None:
    row = listed(codeowners=codeowners_report(".github/CODEOWNERS", "docs/CODEOWNERS"))

    assert row["codeowners_files"] == 2


def test_a_repository_holding_no_codeowners_file_is_counted_as_zero_rather_than_omitted(listed: Listing) -> None:
    """Keep an observation an observation: every checked location was read and held no file."""
    row = listed(codeowners=codeowners_report())

    assert row["codeowners_files"] == 0


def test_a_file_github_does_not_read_is_still_counted_by_the_row(listed: Listing) -> None:
    """Count every file found, so the row and the repository card cannot disagree about one repository.

    `codeownersCard` tones on the same raw total and names each file's `recognised_by_github` in its
    detail line. Filtering the count here would leave the estate column answering No where the card
    beside it reads one file, and the detail the distinction lives in is on the card, not the row.
    """
    row = listed(codeowners=codeowners_report("docs/CODEOWNERS.md", recognised=False))

    assert row["codeowners_files"] == 1


def test_an_unreadable_codeowners_block_leaves_the_count_absent_rather_than_zero(listed: Listing) -> None:
    """Separate a repository owning nothing from one whose contents nobody could read."""
    row = listed(codeowners=CodeownersReport(detail="repository contents are not readable with this token"))

    assert "codeowners_files" not in row


def test_a_listed_repository_with_readable_measures_reports_sonar(listed: Listing) -> None:
    row = listed(sonar=sonar_report(81.5))

    assert row["sonar_reported"] is True


def test_a_repository_with_no_readable_measures_reports_no_sonar_rather_than_no_answer(listed: Listing) -> None:
    """Answer False on a reportable row, so the column tells "no Sonar" from "no report"."""
    row = listed(sonar=SonarReport(detail="no SonarCloud project resolved for this repository"))

    assert row["sonar_reported"] is False


def test_a_listed_repository_carries_the_three_sonar_security_measures(listed: Listing) -> None:
    row = listed(sonar=sonar_security_report(rating=SonarRating(value=3.0), issues=7, hotspots=2))

    assert row["sonar_security_rating"] == {"value": 3.0}
    assert row["sonar_security_issues"] == 7
    assert row["sonar_security_hotspots"] == 2


def test_measures_reporting_no_security_metrics_leave_all_three_absent(listed: Listing) -> None:
    """Distinguish a project measuring nothing about security from one measuring none of it."""
    row = listed(sonar=sonar_report(81.5))

    assert row["sonar_reported"] is True
    assert "sonar_security_rating" not in row
    assert "sonar_security_issues" not in row
    assert "sonar_security_hotspots" not in row


def test_a_project_with_nothing_open_reports_zeros_rather_than_omitting_them(listed: Listing) -> None:
    """Keep a clean project a measurement: the security donut bands it clear."""
    row = listed(sonar=sonar_security_report(issues=0, hotspots=0))

    assert row["sonar_security_issues"] == 0
    assert row["sonar_security_hotspots"] == 0


def test_an_unreadable_sonar_block_leaves_all_three_security_measures_absent(listed: Listing) -> None:
    row = listed(sonar=SonarReport(detail="no SonarCloud project resolved for this repository"))

    assert "sonar_security_rating" not in row
    assert "sonar_security_issues" not in row
    assert "sonar_security_hotspots" not in row


def test_a_listed_repository_carries_its_open_alerts_family_by_family(listed: Listing) -> None:
    """Serve the alert block verbatim, so a clear family and a refused one stay distinguishable."""
    row = listed(security=security_report())
    alerts = cast("dict[str, object]", row["security"])

    assert alerts["dependabot"] == {"open": 3, "by_severity": {"high": 2, "low": 1}}
    assert alerts["code_scanning"] == {"open": 0, "by_severity": {}}
    assert alerts["secret_scanning"] == {"by_severity": {}, "detail": "alerts are not readable with this token"}


def test_a_security_block_carrying_a_reason_leaves_the_alerts_absent(listed: Listing) -> None:
    row = listed(security=SecurityAlertReport(detail="no repository state has been collected; run metrics collect"))

    assert "security" not in row


def test_a_repository_the_window_cannot_cover_states_none_of_the_six_security_facts(client: TestClient) -> None:
    """Leave all six absent where there is no evidence block, `sonar_reported` included.

    `sonar_reported` is the one field that answers False rather than absent on a reportable
    repository, so this is where "no report" has to stay apart from "no Sonar".
    """
    row = {item["repository"]: item for item in client.get("/repositories").json()}["other-service"]

    assert "codeowners_files" not in row
    assert "sonar_reported" not in row
    assert "security" not in row
    assert "sonar_security_rating" not in row
    assert "sonar_security_issues" not in row
    assert "sonar_security_hotspots" not in row


def test_a_repository_reports_its_whole_evidence_block(client: TestClient) -> None:
    body = client.get("/repositories/cath-service").json()

    assert body["team"] == "crime"
    assert body["evidence"]["assessment"]["label"] == "amber"
    assert body["evidence"]["open_pull_requests"]["summary"]["stale_open"] == 1
    assert [item["metric"] for item in body["evidence"]["metrics"]] == ["approval-coverage"]
    assert [item["rule"] for item in body["evidence"]["behaviour"]] == ["unreviewed-merge"]


def test_the_informational_flag_reaches_a_reader_over_the_wire(client: TestClient) -> None:
    """Pin the flag on the JSON rather than only on the policy object that carries it.

    The dashboard leaves an informational row uncoloured and colours every other clear condition
    green, so a serialisation that dropped the flag would report a rule nobody grades as a check
    that passed — with the policy tests still green, since they read the object and not the body.
    """
    conditions = {
        condition["condition"]: condition
        for condition in client.get("/repositories/cath-service").json()["evidence"]["assessment"]["clear"]
    }

    assert conditions["linear-history-not-required"]["informational"] is True
    assert conditions["branch-protected"]["informational"] is False


def test_a_repository_lists_its_contributors_weightiest_first(client: TestClient) -> None:
    contributors = client.get("/repositories/cath-service").json()["contributors"]
    # The person's own summaries for this repository, sent as the contract carries them: the
    # repository page subtracts what they merged and what they pushed straight to the branch out of
    # these, so the figures beside a login and the ones in the JSON are the same figures.
    summaries = [summary.model_dump(mode="json", exclude_none=True) for summary in metrics()]

    assert contributors == [
        {"login": "Alice", "contributions": 3, "blocking": 2, "metrics": summaries},
        {"login": "bob", "contributions": 1, "blocking": 0, "metrics": summaries},
    ]


def test_a_repository_with_no_contributors_reports_an_empty_list(client: TestClient) -> None:
    body = client.get("/repositories/other-service").json()

    assert body["contributors"] == []
    assert "evidence" not in body
    assert body["detail"].startswith("cached pull_request evidence does not cover")


def test_an_unconfigured_repository_is_not_found(client: TestClient) -> None:
    response = client.get("/repositories/nothing-here")

    assert response.status_code == 404
    assert response.json()["detail"] == "repository is not configured: nothing-here"


def test_actors_are_listed_alphabetically_with_their_repository_count_and_labels(client: TestClient) -> None:
    rows = client.get("/actors").json()

    assert rows == [
        {"login": "Alice", "repositories": 2, "labels": ["amber"]},
        {"login": "bob", "repositories": 1, "labels": ["amber"]},
    ]


def test_a_contributors_labels_are_distinct_best_first_and_exclude_the_unassessable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Serve the labels a person's repositories carry, `cannot_assess` out and repeats collapsed.

    The repository count is untouched by the exclusion: it says what somebody works in, and the
    labels say how those repositories are graded where a grade could be reached.
    """
    report = practice_report().model_copy(
        update={
            "actors": (
                ActorReadiness(
                    actor_login="Alice",
                    repositories=(
                        actor_repository("cath-service", 3, readiness=ReadinessLabel.RED),
                        actor_repository("civil-service", 1, readiness=ReadinessLabel.GREEN),
                        actor_repository("other-service", 1, readiness=ReadinessLabel.RED),
                    ),
                ),
                ActorReadiness(
                    actor_login="bob",
                    repositories=(actor_repository("cath-service", 1, readiness=ReadinessLabel.CANNOT_ASSESS),),
                ),
            ),
        },
    )
    monkeypatch.setattr("metrics.service.offline_practice_report", lambda _configuration, _window: report)
    settings = configuration(tmp_path)

    with TestClient(create_app(settings, WindowCache(settings, MAXIMUM_AGE))) as connected:
        rows = connected.get("/actors").json()

    assert rows == [
        {"login": "Alice", "repositories": 3, "labels": ["green", "red"]},
        {"login": "bob", "repositories": 1, "labels": []},
    ]


def test_one_actor_reports_every_repository_they_contributed_to_with_its_team(client: TestClient) -> None:
    body = client.get("/actors/Alice").json()

    assert body["actor"]["actor_login"] == "Alice"
    assert [row["repository"] for row in body["actor"]["repositories"]] == ["cath-service", "civil-service"]
    assert body["teams"] == {"cath-service": "crime", "civil-service": "civil"}


def test_an_actor_carries_the_metric_summaries_measured_in_each_repository(client: TestClient) -> None:
    rows = client.get("/actors/Alice").json()["actor"]["repositories"]

    assert [item["metric"] for row in rows for item in row["metrics"]] == ["approval-coverage", "approval-coverage"]


def test_an_actor_is_matched_case_insensitively_and_spelled_as_the_report_spells_them(
    client: TestClient,
) -> None:
    body = client.get("/actors/ALICE").json()

    assert body["actor"]["actor_login"] == "Alice"


def test_an_actor_nobody_reported_is_not_found(client: TestClient) -> None:
    response = client.get("/actors/nobody")

    assert response.status_code == 404
    assert response.json()["detail"] == "nobody by that login contributed to a reported repository: nobody"


def test_teams_are_listed_with_their_repositories_people_and_label_counts(client: TestClient) -> None:
    rows = {row["team"]: row for row in client.get("/teams").json()}

    assert rows["crime"]["repositories"] == 2
    assert rows["crime"]["unavailable"] == 1
    assert rows["crime"]["actors"] == 2
    assert rows["crime"]["labels"] == {"green": 0, "amber": 1, "red": 0, "cannot_assess": 0, NOT_ASSESSED: 0}
    assert rows["civil"]["labels"] == {"green": 0, "amber": 0, "red": 0, "cannot_assess": 0, NOT_ASSESSED: 1}


def test_one_team_reports_its_repository_rows_and_its_contributors(client: TestClient) -> None:
    body = client.get("/teams/crime").json()

    assert [row["repository"] for row in body["repositories"]] == ["cath-service", "other-service"]
    assert body["actors"] == [
        {"login": "Alice", "repositories": 1, "contributions": 3},
        {"login": "bob", "repositories": 1, "contributions": 1},
    ]
    assert body["unavailable"] == 1


def test_a_team_counts_only_the_contributions_made_inside_its_own_repositories(client: TestClient) -> None:
    body = client.get("/teams/civil").json()

    assert body["actors"] == [{"login": "Alice", "repositories": 1, "contributions": 1}]


def test_an_unconfigured_team_is_not_found(client: TestClient) -> None:
    response = client.get("/teams/nothing-here")

    assert response.status_code == 404
    assert response.json()["detail"] == "team is not configured: nothing-here"


def test_a_repository_reports_its_periods_since_it_was_enabled(client: TestClient, series: Series) -> None:
    body = client.get("/repositories/cath-service/trend").json()

    assert body["repository"] == "cath-service"
    assert [period["index"] for period in body["periods"]] == [1, 2]
    assert [period["throughput"]["merged_pull_requests"] for period in body["periods"]] == [6, 8]
    assert series.cuts == [("cath-service", DEFAULT_PERIOD_DAYS, None)]


def test_a_series_is_cut_the_way_the_request_asked_for(client: TestClient, series: Series) -> None:
    response = client.get("/repositories/cath-service/trend", params={"period_days": 7, "periods": 3})

    assert response.status_code == 200
    assert series.cuts == [("cath-service", 7, 3)]


def test_a_repository_with_no_enablement_date_reports_the_reason_and_no_series(client: TestClient) -> None:
    body = client.get("/repositories/civil-service/trend").json()

    assert body["detail"] == NO_ENABLEMENT_DATE
    assert body["periods"] == []
    assert "enablement_at" not in body


def test_an_unconfigured_repository_has_no_series(client: TestClient, series: Series) -> None:
    response = client.get("/repositories/nothing-here/trend")

    assert response.status_code == 404
    assert response.json()["detail"] == "repository is not configured: nothing-here"
    # Refused before anything was cut, so an unknown name cannot cost a walk of the caches.
    assert series.cuts == []


@pytest.mark.parametrize(
    "parameters",
    [
        {"period_days": 0},
        {"period_days": 400},
        {"period_days": "soon"},
        {"periods": 0},
        {"periods": 99},
    ],
)
def test_a_series_nobody_can_cut_is_refused_rather_than_clamped(
    client: TestClient,
    parameters: dict[str, object],
) -> None:
    assert client.get("/repositories/cath-service/trend", params=parameters).status_code == 422


def test_a_series_resolving_to_more_periods_than_one_request_may_ask_for_is_refused(
    client: TestClient,
    series: Series,
) -> None:
    """Bound the series a request RESOLVES to, not only the count it names.

    `periods` is optional, and left out it asks for every whole period since enablement: at a
    one-day period that is a cache load per day since the repository was enabled, all of them inside
    one request. Refused rather than truncated, as `MAXIMUM_PERIODS` says.
    """
    response = client.get("/repositories/cath-service/trend", params={"period_days": 1})

    assert response.status_code == 422
    assert "exceeds the 26 one request may ask for" in response.json()["detail"]
    # Refused before anything was cut, so the request costs no walk of the caches at all.
    assert series.cuts == []


def test_naming_a_cut_makes_a_long_history_servable_again(client: TestClient, series: Series) -> None:
    """The refusal names the way out, so a caller who cuts the series is served."""
    response = client.get("/repositories/cath-service/trend", params={"period_days": 1, "periods": 5})

    assert response.status_code == 200
    assert series.cuts == [("cath-service", 1, 5)]


def test_an_unreadable_observation_history_is_reported_rather_than_answered_with_a_hole(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Degrade the way `emit_trend` does: a series that could not read its history is no series.

    The alert observation history is read straight through, so a series that silently reported no
    observation because the file could not be opened is indistinguishable from one nobody ever
    collected — the single thing that history exists to keep apart.
    """

    def refuse(*_: object) -> RepositoryTrend:
        message = "the observation history is not a database"
        raise StorageError(message)

    monkeypatch.setattr("metrics.service.repository_trend", refuse)

    response = client.get("/repositories/cath-service/trend")

    assert response.status_code == 503
    assert "the observation history could not be read" in response.json()["detail"]
    assert "Trend failed for cath-service" in caplog.text


@pytest.mark.usefixtures("series")
def test_the_least_recently_read_series_is_dropped_once_the_cache_is_full(tmp_path: Path) -> None:
    """Bound the series cache, whose keys carry the cut and are effectively unbounded.

    Unlike the window bundles, keyed by the five spans on offer, `period_days` alone runs 1 to 365,
    so a cache that only ever inserted would grow for the life of the process.
    """
    cache = WindowCache(configuration(tmp_path), MAXIMUM_AGE)
    for period_days in range(1, MAXIMUM_HELD_SERIES + 2):
        cache.trend("cath-service", period_days, 5)

    assert len(cache.series) == MAXIMUM_HELD_SERIES
    assert ("cath-service", 1, 5) not in cache.series
    assert ("cath-service", MAXIMUM_HELD_SERIES + 1, 5) in cache.series


@pytest.mark.usefixtures("series")
def test_a_reread_series_outlives_one_nobody_has_asked_for_since(tmp_path: Path) -> None:
    """Least RECENTLY READ, not first built: reading a series again keeps it out of the way."""
    cache = WindowCache(configuration(tmp_path), MAXIMUM_AGE)
    cache.trend("cath-service", 1, 5)
    for period_days in range(2, MAXIMUM_HELD_SERIES + 1):
        cache.trend("cath-service", period_days, 5)
    cache.trend("cath-service", 1, 5)

    cache.trend("cath-service", MAXIMUM_HELD_SERIES + 1, 5)

    assert ("cath-service", 1, 5) in cache.series
    assert ("cath-service", 2, 5) not in cache.series


def test_one_cut_of_one_series_is_made_once_and_then_reused(tmp_path: Path, series: Series) -> None:
    settings = configuration(tmp_path)
    cache = WindowCache(settings, MAXIMUM_AGE)

    first = cache.trend("cath-service", 28, None)
    second = cache.trend("cath-service", 28, None)

    assert first is second
    assert series.cuts == [("cath-service", 28, None)]


def test_two_cuts_of_one_series_are_held_apart(tmp_path: Path, series: Series) -> None:
    settings = configuration(tmp_path)
    cache = WindowCache(settings, MAXIMUM_AGE)

    cache.trend("cath-service", 28, None)
    cache.trend("cath-service", 7, None)

    assert series.cuts == [("cath-service", 28, None), ("cath-service", 7, None)]


def test_a_collection_landing_after_a_series_was_cut_recuts_it(tmp_path: Path, series: Series) -> None:
    settings = configuration(tmp_path)
    cache = WindowCache(settings, MAXIMUM_AGE)
    cache.trend("cath-service", 28, None)

    settings.database.write_bytes(b"collected")
    cache.trend("cath-service", 28, None)

    assert len(series.cuts) == 2


def test_a_series_older_than_the_configured_maximum_age_is_recut(tmp_path: Path, series: Series) -> None:
    settings = configuration(tmp_path)
    cache = WindowCache(settings, MAXIMUM_AGE)
    cache.trend("cath-service", 28, None)
    held = cache.series["cath-service", 28, None]
    # Aged in place rather than by waiting: the recut must depend on the series' own age.
    cache.series["cath-service", 28, None] = replace(
        held,
        built_at=held.built_at - timedelta(seconds=DEFAULT_BUNDLE_AGE_SECONDS * 2),
    )

    cache.trend("cath-service", 28, None)

    assert len(series.cuts) == 2


def test_a_requested_span_on_the_list_is_served(client: TestClient) -> None:
    body = client.get("/overview", params={"weeks": 12}).json()

    assert body["weeks"] == 12
    assert body["ends_at"] > body["starts_at"]


def test_a_span_off_the_list_is_refused_rather_than_rounded(client: TestClient) -> None:
    response = client.get("/overview", params={"weeks": 3})

    assert response.status_code == 422
    assert response.json()["detail"] == "weeks must be one of 1, 4, 8, 12, 26"


def test_a_span_that_is_not_a_number_is_refused(client: TestClient) -> None:
    assert client.get("/repositories", params={"weeks": "soon"}).status_code == 422


def test_a_repository_detail_may_not_carry_both_evidence_and_a_reason() -> None:
    with pytest.raises(ValidationError, match="either an evidence block or the reason"):
        RepositoryDetail(repository="cath-service", team="crime")


def test_contributor_rows_are_grouped_by_the_repository_they_were_measured_in() -> None:
    grouped = contributor_rows(practice_report())

    assert sorted(grouped) == ["cath-service", "civil-service"]
    assert [row.login for row in grouped["civil-service"]] == ["Alice"]


def test_a_contributor_row_carries_the_actors_own_metrics_unchanged() -> None:
    grouped = contributor_rows(practice_report())

    # Verbatim: the UI subtracts what a person merged and what they pushed out of these, and the
    # service deriving any of it here would put a second arithmetic beside the contract's own.
    assert [row.metrics for row in grouped["civil-service"]] == [metrics()]


def test_the_service_serves_the_default_window_and_says_what_it_is_serving(
    tmp_path: Path,
    loader: Loader,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    served: list[tuple[str, str, int, str]] = []

    def run(app: FastAPI, host: str, port: int, log_level: str) -> None:
        """Stand in for uvicorn, recording what the service asked it to serve and where."""
        served.append((app.title, host, port, log_level))

    monkeypatch.setattr("metrics.service.uvicorn.run", run)
    path = tmp_path / "metrics.yml"
    path.write_text(configuration_text(tmp_path), encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["metrics-serve", "--config", str(path), "--port", "9001"])

    status = main()

    assert status == 0
    assert served == [("metrics evidence service", "127.0.0.1", 9001, "info")]
    # Pre-warmed before anything listened, so the first reader waits on a request and not the caches.
    assert len(loader.windows) == 1
    assert "Serving 3 repositories over 4 weeks, 1 unavailable" in caplog.text


def test_an_unreadable_configuration_is_refused_before_anything_listens(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr("sys.argv", ["metrics-serve", "--config", str(tmp_path / "absent.yml")])

    assert main() == 1
    assert "Invalid configuration" in caplog.text


def test_a_configuration_naming_no_team_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = tmp_path / "policy.yml"
    path.write_text(
        f"version: 1\norganization: hmcts\ndatabase: {tmp_path / 'metrics.sqlite3'}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("sys.argv", ["metrics-serve", "--config", str(path)])

    assert main() == 1
    assert "no team is configured" in caplog.text


def test_a_configuration_forbidding_every_span_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = tmp_path / "metrics.yml"
    path.write_text(
        f"{configuration_text(tmp_path)}lookback:\n  maximum_days: 3\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("sys.argv", ["metrics-serve", "--config", str(path)])

    assert main() == 1
    assert "shorter than the shortest span the service offers" in caplog.text


@pytest.mark.parametrize("age", ["0", "-1"])
def test_a_bundle_age_that_would_make_every_bundle_born_stale_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    age: str,
) -> None:
    """Refuse the flag rather than serve a cache nothing can ever hit.

    A zero or negative age makes `usable` false for everything held, so every request rebuilds the
    whole cohort's report under its span's build lock — which reads as a service that has stopped
    rather than as a mistyped flag.
    """
    path = tmp_path / "metrics.yml"
    path.write_text(configuration_text(tmp_path), encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["metrics-serve", "--config", str(path), "--max-bundle-age", age])

    with pytest.raises(SystemExit) as refused:
        main()

    assert refused.value.code == 2


def test_a_bundle_age_of_a_whole_number_of_seconds_is_taken_as_given(
    tmp_path: Path,
    loader: Loader,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bound refuses only what it says it refuses: an ordinary age reaches the cache intact."""
    _ = loader
    held: list[timedelta] = []

    def record(settings: Configuration, maximum_age: timedelta) -> WindowCache:
        """Build the cache the service will serve from, recording the age it was given."""
        held.append(maximum_age)
        return WindowCache(settings, maximum_age)

    monkeypatch.setattr("metrics.service.uvicorn.run", lambda *_, **__: None)
    monkeypatch.setattr("metrics.service.WindowCache", record)
    path = tmp_path / "metrics.yml"
    path.write_text(configuration_text(tmp_path), encoding="utf-8")
    # The warm interval comes down with the age: it is also the margin a span is refreshed within,
    # and `main` refuses one at or above the age rather than warming every span on every wake.
    monkeypatch.setattr(
        "sys.argv",
        ["metrics-serve", "--config", str(path), "--max-bundle-age", "45", "--warm-interval", "15"],
    )

    assert main() == 0
    assert held == [timedelta(seconds=45)]


@pytest.mark.parametrize("interval", ["0", "-1"])
def test_a_warm_interval_that_would_never_rest_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interval: str,
) -> None:
    """Refuse the flag rather than run a thread rebuilding every span in a loop.

    The same fault as a bundle age of zero, read from the other side: the caches would never be
    quiet, and every reader would be competing with the warmer for the span they asked for.
    """
    path = tmp_path / "metrics.yml"
    path.write_text(configuration_text(tmp_path), encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["metrics-serve", "--config", str(path), "--warm-interval", interval])

    with pytest.raises(SystemExit) as refused:
        main()

    assert refused.value.code == 2


@pytest.mark.parametrize("interval", ["60", "90"])
def test_a_warm_interval_at_or_above_the_bundle_age_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    interval: str,
) -> None:
    """Refuse the pair, because neither flag is out of range on its own.

    The interval is the margin a span is refreshed within, so an interval that outlasts the age asks
    whether a bundle will still be usable further ahead than a bundle can live: the answer is no for
    a bundle built a second ago, and the warmer rebuilds every span on every wake for ever. A short
    age is the natural way to ask for fresher figures, which is how the pair is reached without
    either number looking wrong — so it is named here rather than left to be inferred from load.
    """
    path = tmp_path / "metrics.yml"
    path.write_text(configuration_text(tmp_path), encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        ["metrics-serve", "--config", str(path), "--max-bundle-age", "60", "--warm-interval", interval],
    )

    assert main() == 1
    assert "--warm-interval" in caplog.text
    assert "--max-bundle-age" in caplog.text


def test_the_warm_interval_reaches_the_application_as_given(
    tmp_path: Path,
    loader: Loader,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The service warms on the configured interval rather than on the one it defaults to."""
    _ = loader
    given: list[timedelta | None] = []
    build = create_app

    def record(settings: Configuration, cache: WindowCache, warm_interval: timedelta | None = None) -> FastAPI:
        """Build the application the service will serve, recording the interval it was given."""
        given.append(warm_interval)
        # Built without the interval: nothing here ever serves a request, so nothing needs a warmer.
        return build(settings, cache)

    monkeypatch.setattr("metrics.service.uvicorn.run", lambda *_, **__: None)
    monkeypatch.setattr("metrics.service.create_app", record)
    path = tmp_path / "metrics.yml"
    path.write_text(configuration_text(tmp_path), encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["metrics-serve", "--config", str(path), "--warm-interval", "30"])

    assert main() == 0
    assert given == [timedelta(seconds=30)]


def configuration_text(tmp_path: Path) -> str:
    """Write out the configuration `main` reads, owning the same three repositories."""
    return (
        "version: 1\n"
        "organization: hmcts\n"
        f"database: {tmp_path / 'metrics.sqlite3'}\n"
        "teams:\n"
        "  - identifier: crime\n"
        "    display_name: Crime\n"
        "    repositories: [cath-service, other-service]\n"
        "  - identifier: civil\n"
        "    display_name: Civil\n"
        "    repositories: [civil-service]\n"
    )
