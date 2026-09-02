"""Test the read-only evidence service."""

import logging
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from metrics.behaviour import source_signature
from metrics.config import Configuration, LookbackConfiguration
from metrics.domain import (
    ActorReadiness,
    ActorRepositoryReadiness,
    BehaviourMetricSummary,
    CodeownersReport,
    CohortSummary,
    EvidenceSource,
    EvidenceUnavailable,
    FindingSeverity,
    MaintenanceReport,
    MergeGateReport,
    ObservationStatus,
    OpenPullRequestReport,
    OpenPullRequestSummary,
    PracticeEvidenceReport,
    PracticeFinding,
    RateObservation,
    ReadinessAssessment,
    ReadinessCondition,
    ReadinessLabel,
    ReportingWindow,
    RepositoryPracticeEvidence,
    RepositoryTrend,
    SecurityAlertReport,
    SonarReport,
    SourceCoverage,
    TrendPeriod,
    TrendThroughput,
    WindowProvenance,
)
from metrics.service import (
    DEFAULT_BUNDLE_AGE_SECONDS,
    DEFAULT_PERIOD_DAYS,
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
    main,
    reporting_window,
    source_stamp,
    window_options,
)
from metrics.storage import StorageError, cache_direct_commit_facts, cache_pull_request_facts
from metrics.trend import NO_ENABLEMENT_DATE, SeriesRequest
from metrics.window import midnight

MAXIMUM_AGE = timedelta(seconds=DEFAULT_BUNDLE_AGE_SECONDS)
"""The bundle lifetime every test uses unless it is testing the lifetime itself."""


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


def actor_repository(repository: str, contributions: int, blocking: int = 0) -> ActorRepositoryReadiness:
    """State one person's contribution to one repository, as the contract carries it."""
    return ActorRepositoryReadiness(
        readiness=ReadinessLabel.AMBER,
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
    """Build once under the lock, so a cold span does not walk every repository twice at once.

    The class promises this and every other test is single-threaded, so a lock dropped or narrowed to
    the dictionary write alone would let a thundering herd on a cold span run one whole cohort load
    per waiting request with nothing failing.
    """
    released = threading.Event()
    loader = Loader()

    def blocking(settings: Configuration, window: ReportingWindow) -> PracticeEvidenceReport:
        released.wait(timeout=5)
        return loader(settings, window)

    monkeypatch.setattr("metrics.service.offline_practice_report", blocking)
    cache = WindowCache(configuration(tmp_path), MAXIMUM_AGE)
    built: list[ReportBundle] = []
    threads = [threading.Thread(target=lambda: built.append(cache.bundle(4))) for _ in range(2)]

    for thread in threads:
        thread.start()
    released.set()
    for thread in threads:
        thread.join(timeout=5)

    assert len(loader.windows) == 1
    assert built[0] is built[1]


def test_reading_the_caches_to_build_a_bundle_does_not_invalidate_it(tmp_path: Path) -> None:
    """Hold a bundle built from a REAL cache, not a stubbed loader, across the next request.

    The build reads every configured repository's coverage, and a read that stamped `accessed_at`
    would write to the cache file — moving the modification time and size the held bundle is compared
    against, so its own build would invalidate it and every request would rebuild the whole cohort
    under the lock. `test_one_span_is_assembled_once_and_then_reused` cannot see that: its loader
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


def test_actors_are_listed_alphabetically_with_their_repository_count(client: TestClient) -> None:
    rows = client.get("/actors").json()

    assert rows == [
        {"login": "Alice", "repositories": 2},
        {"login": "bob", "repositories": 1},
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
    one-day period that is a cache load per day since the repository was enabled, all of them under
    the one build lock. Refused rather than truncated, as `MAXIMUM_PERIODS` says.
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
    whole cohort's report under the one build lock — which reads as a service that has stopped
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
    monkeypatch.setattr("sys.argv", ["metrics-serve", "--config", str(path), "--max-bundle-age", "45"])

    assert main() == 0
    assert held == [timedelta(seconds=45)]


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
