"""Test post-enablement period series assembled from cached facts.

Every fixture here spans SEVERAL consecutive windows, because that is the only place this behaviour
shows up: a metric moving between periods, a thin period in the middle of a complete series, and a
trailing partial period that must not be reported at all.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from metrics.behaviour import commit_query_signature, query_signature
from metrics.behaviour_metrics import behaviour_metrics
from metrics.behaviour_metrics.independent_review_coverage import IndependentReviewCoverage
from metrics.behaviour_metrics.merge_cycle_time import MergeCycleTime
from metrics.behaviour_metrics.pull_request_size import PullRequestSize
from metrics.behaviour_metrics.time_to_first_review import TimeToFirstReview
from metrics.config import Configuration
from metrics.domain import (
    AlertSeverity,
    CollectionStatus,
    DeltaBasis,
    DirectCommitFact,
    EvidenceSource,
    Merges,
    ObservationStatus,
    OpenAlertCount,
    Percentile,
    PullRequestFact,
    RateObservation,
    ReportingWindow,
    RepositoryInventory,
    RepositoryInventoryItem,
    RepositoryMetadata,
    ReviewFact,
    ReviewState,
    SecurityAlertEvidence,
    SourceCoverage,
    TrendDelta,
    TrendMetric,
    TrendPeriod,
    WindowProvenance,
)
from metrics.evidence import RepositoryEvidence, cached_repository_evidence, repository_evidence
from metrics.storage import (
    cache_direct_commit_facts,
    cache_pull_request_facts,
    observation_database,
    record_alert_observations,
)
from metrics.trend import (
    NO_ENABLEMENT_DATE,
    NO_WHOLE_PERIOD,
    SeriesRequest,
    series_status,
    trend_report,
)

ENABLEMENT = datetime(2026, 7, 18, tzinfo=UTC)
"""The anchor every fixture series uses: exactly three whole seven-day periods before the reference."""

REFERENCE = datetime(2026, 8, 8, 14, 58, tzinfo=UTC)
"""A mid-afternoon reference instant, so the trailing partial period is genuinely partial."""

SPAN = timedelta(days=7)


def configuration(tmp_path: Path, enablement: dict[str, str] | None = None) -> Configuration:
    """Build a configuration owning two repositories, only one of which was enabled.

    `minimum_merges` is lowered to two so a thin period can be written in two facts rather than ten;
    what is under test is the suppression, not the size of the shipped default.
    """
    return Configuration.model_validate(
        {
            "version": 1,
            "organization": "hmcts",
            "database": tmp_path / "metrics.sqlite3",
            "assessment": {"minimum_merges": 2},
            "teams": [
                {
                    "identifier": "civil",
                    "display_name": "Civil",
                    "repositories": ["cath-service", "opal-common-lib"],
                },
            ],
            "enablement": {"cath-service": "2026-07-18"} if enablement is None else enablement,
        },
    )


def offline_request(periods: int | None = None) -> SeriesRequest:
    """Build a request for a seven-day series read from the cache alone."""
    return SeriesRequest(period_days=7, periods=periods, reference=REFERENCE, client=None)


def window(index: int) -> ReportingWindow:
    """Return the baseline window at index 0, and the whole period numbered by any other index."""
    starts_at = ENABLEMENT + (index - 1) * SPAN
    return ReportingWindow(starts_at=starts_at, ends_at=starts_at + SPAN)


def merged(
    number: int,
    merged_at: datetime,
    *,
    reviewed: bool,
    hours: int = 4,
    lines: int = 48,
    author: str = "author",
    author_type: str = "User",
) -> PullRequestFact:
    """Build one merged pull request, carrying independent human review when asked for.

    `hours` and `lines` move the two distribution metrics between windows: a series only shows a
    delta where the underlying sample actually changed, so a fixture whose pull requests are all
    identical can assert nothing about movement.

    `author` and `author_type` default to one anonymous person, so every fixture not about who
    merged reads as it did; the contributor fixtures name people because that count is the only
    thing in the series that distinguishes them.
    """
    return PullRequestFact(
        identifier=number,
        repository="cath-service",
        number=number,
        created_at=merged_at - timedelta(hours=hours),
        merged_at=merged_at,
        draft=False,
        author_login=author,
        author_type=author_type,
        reviews=(
            ReviewFact(
                identifier=number * 10,
                submitted_at=merged_at - timedelta(hours=1),
                state=ReviewState.APPROVED,
                author_login="reviewer",
                author_type="User",
                comment_count=1,
            ),
        )
        if reviewed
        else (),
        additions=lines - 8,
        deletions=8,
        changed_files=3,
    )


def pushed(sha: str, committed_at: datetime, author: str = "pusher", author_type: str = "User") -> DirectCommitFact:
    """Build one commit that reached the default branch without a pull request."""
    return DirectCommitFact(
        sha=sha,
        committed_at=committed_at,
        author_login=author,
        author_type=author_type,
        additions=12,
        deletions=1,
        changed_files=2,
    )


def coverage(covered: ReportingWindow, source: EvidenceSource) -> SourceCoverage:
    """Describe complete coverage of one window for one independently cached source."""
    signatures = {EvidenceSource.PULL_REQUEST: query_signature, EvidenceSource.COMMIT: commit_query_signature}
    return SourceCoverage(
        organization="hmcts",
        repository="cath-service",
        source=source,
        query_hash=signatures[source](),
        starts_at=covered.starts_at,
        ends_at=covered.ends_at,
    )


def seed(
    settings: Configuration,
    covered: ReportingWindow,
    pull_requests: tuple[PullRequestFact, ...] = (),
    direct_commits: tuple[DirectCommitFact, ...] = (),
) -> None:
    """Cache one window of settled history for both routes onto the default branch."""
    cache_pull_request_facts(
        settings.database,
        coverage(covered, EvidenceSource.PULL_REQUEST),
        pull_requests,
        complete=True,
    )
    cache_direct_commit_facts(
        settings.database, coverage(covered, EvidenceSource.COMMIT), direct_commits, complete=True
    )


def seeded_series(tmp_path: Path) -> Configuration:
    """Cache a baseline and three whole periods whose review coverage moves between them.

    One of two merges reviewed in the baseline, then two of two, then a single-merge period that
    must be suppressed, then two of three once a direct commit joins the denominator.
    """
    settings = configuration(tmp_path)
    seed(
        settings,
        window(0),
        (
            merged(1, window(0).starts_at + timedelta(days=1), reviewed=True),
            merged(2, window(0).starts_at + timedelta(days=3), reviewed=False),
        ),
    )
    seed(
        settings,
        window(1),
        (
            merged(3, window(1).starts_at + timedelta(days=1), reviewed=True),
            merged(4, window(1).starts_at + timedelta(days=2), reviewed=True),
        ),
    )
    seed(settings, window(2), (merged(5, window(2).starts_at + timedelta(days=1), reviewed=True),))
    seed(
        settings,
        window(3),
        (
            merged(6, window(3).starts_at + timedelta(days=1), reviewed=True),
            merged(7, window(3).starts_at + timedelta(days=2), reviewed=True),
        ),
        (pushed("aaa", window(3).starts_at + timedelta(days=3)),),
    )
    return settings


def review_coverage(metrics: tuple[TrendMetric, ...]) -> RateObservation:
    """Return the independent-review-coverage observation from one window's metric values."""
    summary = next(item.summary for item in metrics if item.metric == IndependentReviewCoverage.identifier)
    assert isinstance(summary, RateObservation)
    return summary


def test_the_series_reports_a_baseline_and_every_whole_period(tmp_path: Path) -> None:
    """Anchor the series to enablement and stop before the trailing partial period."""
    report = trend_report(seeded_series(tmp_path), offline_request())

    series = report.repositories[0]
    assert report.period_days == 7
    assert series.enablement_at == ENABLEMENT
    assert series.baseline is not None
    assert (series.baseline.starts_at, series.baseline.ends_at) == (window(0).starts_at, ENABLEMENT)
    assert tuple(period.index for period in series.periods) == (1, 2, 3)
    assert tuple(period.starts_at for period in series.periods) == tuple(window(index).starts_at for index in (1, 2, 3))
    assert series.periods[-1].ends_at == datetime(2026, 8, 8, tzinfo=UTC)


def test_the_series_reports_what_each_period_merged(tmp_path: Path) -> None:
    """Count both routes onto the default branch per period, from the cohort already carried."""
    report = trend_report(seeded_series(tmp_path), offline_request())

    period = report.repositories[0].periods[2]
    assert period.throughput is not None
    assert (period.throughput.merges, period.throughput.merged_pull_requests) == (3, 2)
    assert period.throughput.direct_commits == 1
    assert period.cohort is not None
    assert period.cohort.reported == 2


def contributor_series(tmp_path: Path) -> Configuration:
    """Cache a baseline and one period whose merges name people, an agent, and one repeat author.

    Deliberately shaped so the contributor count matches NONE of the merge counts beside it: the
    period merges five changes over four accounts, one of which is an agent, leaving three people.
    A fixture where the two happened to agree could not tell a genuine count of people apart from a
    count of changes wearing the wrong label.
    """
    settings = configuration(tmp_path)
    seed(
        settings,
        window(0),
        (
            merged(1, window(0).starts_at + timedelta(days=1), reviewed=True, author="alice"),
            merged(2, window(0).starts_at + timedelta(days=3), reviewed=True, author="bob"),
        ),
    )
    seed(
        settings,
        window(1),
        (
            merged(3, window(1).starts_at + timedelta(days=1), reviewed=True, author="alice"),
            merged(4, window(1).starts_at + timedelta(days=2), reviewed=True, author="alice"),
            merged(5, window(1).starts_at + timedelta(days=3), reviewed=True, author="bob"),
            merged(
                6,
                window(1).starts_at + timedelta(days=4),
                reviewed=True,
                author="copilot-swe-agent[bot]",
                author_type="Bot",
            ),
        ),
        (pushed("aaa", window(1).starts_at + timedelta(days=5), author="carol"),),
    )
    return settings


def test_a_period_counts_the_distinct_people_who_authored_its_merges(tmp_path: Path) -> None:
    """Count people rather than changes: two merges by one person are one active contributor."""
    series = trend_report(contributor_series(tmp_path), offline_request(periods=1)).repositories[0]

    assert series.baseline is not None
    assert series.baseline.throughput is not None
    assert series.periods[0].throughput is not None
    assert series.baseline.throughput.active_contributors == 2
    assert series.periods[0].throughput.active_contributors == 3


def test_a_contributor_is_counted_once_however_many_routes_they_used(tmp_path: Path) -> None:
    """Count a person who both merged pull requests and pushed straight to the branch exactly once."""
    settings = configuration(tmp_path)
    seed(settings, window(0), (merged(1, window(0).starts_at + timedelta(days=1), reviewed=True, author="alice"),))
    seed(
        settings,
        window(1),
        (merged(2, window(1).starts_at + timedelta(days=1), reviewed=True, author="alice"),),
        (pushed("aaa", window(1).starts_at + timedelta(days=2), author="alice"),),
    )

    series = trend_report(settings, offline_request(periods=1)).repositories[0]

    assert series.periods[0].throughput is not None
    assert series.periods[0].throughput.merges == 2
    assert series.periods[0].throughput.active_contributors == 1


def test_a_bot_leaves_the_contributor_count_while_its_merges_stay_in_the_counts(tmp_path: Path) -> None:
    """Keep agent-authored merges in the cohort, as the exclusions intend, and out of the people count.

    The two rules are meant to differ. `cohort.excluded_authors` drops dependency automation only,
    so an agent's work is reported as work; the contributor count then asks a different question —
    how many PEOPLE were active — which no bot answers whatever the cohort keeps.
    """
    series = trend_report(contributor_series(tmp_path), offline_request(periods=1)).repositories[0]

    period = series.periods[0]
    assert period.throughput is not None
    assert (period.throughput.merged_pull_requests, period.throughput.direct_commits) == (4, 1)
    assert period.throughput.merges == 5
    assert period.throughput.active_contributors == 3


def test_a_reviewer_who_merged_nothing_is_not_an_active_contributor(tmp_path: Path) -> None:
    """Count authorship, which is what every other number in this block is measured over.

    Both merges below are authored by one person and reviewed by another, so a count that took
    reviewers as contributors would report two. It reports one: this block counts who put changes
    onto the default branch, and a count mixing in reviewers would describe a different population
    from the merges printed beside it.
    """
    settings = configuration(tmp_path)
    seed(settings, window(0), (merged(1, window(0).starts_at + timedelta(days=1), reviewed=True, author="alice"),))
    seed(
        settings,
        window(1),
        (
            merged(2, window(1).starts_at + timedelta(days=1), reviewed=True, author="alice"),
            merged(3, window(1).starts_at + timedelta(days=2), reviewed=True, author="alice"),
        ),
    )

    series = trend_report(settings, offline_request(periods=1)).repositories[0]

    assert series.periods[0].throughput is not None
    assert series.periods[0].throughput.active_contributors == 1


def test_the_contributor_count_is_compared_against_the_baseline(tmp_path: Path) -> None:
    """Compare people between windows as a count, so a team that grew is visible as a fact."""
    series = trend_report(contributor_series(tmp_path), offline_request(periods=1)).repositories[0]

    contributors = delta_for(series.periods[0], "active-contributors")
    assert (contributors.baseline, contributors.period, contributors.change) == (2, 3, 50.0)
    assert (contributors.basis, contributors.unit) == (DeltaBasis.PERCENTAGE_CHANGE, None)


def test_a_period_every_merge_of_which_was_a_bot_reports_no_contributors(tmp_path: Path) -> None:
    """Report nobody rather than nothing: the merges happened, and no person is behind them."""
    settings = configuration(tmp_path)
    seed(settings, window(0), (merged(1, window(0).starts_at + timedelta(days=1), reviewed=True, author="alice"),))
    seed(
        settings,
        window(1),
        (
            merged(
                2,
                window(1).starts_at + timedelta(days=1),
                reviewed=True,
                author="copilot-swe-agent[bot]",
                author_type="Bot",
            ),
            merged(
                3,
                window(1).starts_at + timedelta(days=2),
                reviewed=True,
                author="copilot-swe-agent[bot]",
                author_type="Bot",
            ),
        ),
    )

    series = trend_report(settings, offline_request(periods=1)).repositories[0]

    assert series.periods[0].throughput is not None
    assert series.periods[0].throughput.merged_pull_requests == 2
    assert series.periods[0].throughput.active_contributors == 0


def test_a_metric_moves_between_periods(tmp_path: Path) -> None:
    """Report the same metric per period, so movement away from the baseline is visible."""
    series = trend_report(seeded_series(tmp_path), offline_request()).repositories[0]

    assert series.baseline is not None
    baseline = review_coverage(series.baseline.metrics)
    first = review_coverage(series.periods[0].metrics)
    third = review_coverage(series.periods[2].metrics)
    assert (baseline.numerator, baseline.denominator) == (1, 2)
    assert (first.numerator, first.denominator) == (2, 2)
    assert (third.numerator, third.denominator) == (2, 3)


def test_a_thin_period_keeps_its_counts_and_suppresses_its_metric_values(tmp_path: Path) -> None:
    """Report a mid-series period whose cohort is too small as counts plus the reason, never a rate."""
    series = trend_report(seeded_series(tmp_path), offline_request()).repositories[0]

    thin = series.periods[1]
    assert thin.metrics == ()
    assert thin.detail == "1 merges into the default branch, below the minimum of 2, so metric values are suppressed"
    assert thin.throughput is not None
    assert thin.throughput.merges == 1
    assert series.periods[2].metrics != ()


def test_trend_and_evidence_agree_over_one_window(tmp_path: Path) -> None:
    """Compute what `evidence` computes for the same window from the same cache, for every metric.

    The agreement that matters most: a divergence here is the `collect`-computed-metrics bug reborn,
    so it is asserted directly rather than left to a review of which function called which.
    """
    settings = seeded_series(tmp_path)
    report = trend_report(settings, offline_request())
    observed = cached_repository_evidence(settings, "cath-service", window(3))

    assert isinstance(observed, RepositoryEvidence)
    period = report.repositories[0].periods[2]
    assert period.metrics != ()
    assert {item.metric: item.summary for item in period.metrics} == {
        metric.identifier: observed.metric(metric, include_identities=False).summary
        for metric in behaviour_metrics(settings.traceability)
    }


def test_offline_refuses_a_period_the_cache_does_not_cover(tmp_path: Path) -> None:
    """Refuse an uncovered period rather than reporting a silently empty one."""
    settings = configuration(tmp_path)
    seed(settings, window(0), (merged(1, window(0).starts_at + timedelta(days=1), reviewed=True),))
    seed(settings, window(1), (merged(2, window(1).starts_at + timedelta(days=1), reviewed=True),))

    report = trend_report(settings, offline_request())

    uncovered = report.repositories[0].periods[1]
    assert uncovered.cohort is None
    assert uncovered.throughput is None
    assert uncovered.metrics == ()
    assert uncovered.detail is not None
    assert "cached pull_request evidence does not cover" in uncovered.detail
    assert series_status(report) is CollectionStatus.PARTIAL


def test_a_repository_without_an_enablement_date_is_reported_rather_than_dropped(tmp_path: Path) -> None:
    """Name the missing anchor instead of guessing one or leaving the repository out."""
    report = trend_report(seeded_series(tmp_path), offline_request())

    unanchored = report.repositories[1]
    assert tuple(item.repository for item in report.repositories) == ("cath-service", "opal-common-lib")
    assert unanchored.enablement_at is None
    assert unanchored.baseline is None
    assert unanchored.periods == ()
    assert unanchored.detail == NO_ENABLEMENT_DATE


def test_a_repository_enabled_inside_the_current_period_reports_no_series_yet(tmp_path: Path) -> None:
    """Report the anchor and the reason when only a partial period has elapsed since it."""
    report = trend_report(configuration(tmp_path, {"cath-service": "2026-08-05"}), offline_request())

    assert report.repositories[0].enablement_at == datetime(2026, 8, 5, tzinfo=UTC)
    assert report.repositories[0].periods == ()
    assert report.repositories[0].detail == NO_WHOLE_PERIOD
    assert series_status(report) is CollectionStatus.FAILED


def test_a_shorter_series_reports_the_periods_nearest_the_anchor(tmp_path: Path) -> None:
    """Cap the series where asked, keeping the periods the baseline is compared against."""
    report = trend_report(seeded_series(tmp_path), offline_request(periods=2))

    assert tuple(period.index for period in report.repositories[0].periods) == (1, 2)


def test_a_complete_series_reports_a_complete_run(tmp_path: Path) -> None:
    """Report completeness only when every window of every anchored series was observed."""
    report = trend_report(seeded_series(tmp_path), offline_request())

    assert series_status(report) is CollectionStatus.COMPLETE


def test_a_run_collects_the_periods_the_cache_lacks_when_it_is_not_offline(tmp_path: Path) -> None:
    """Collect each window of the series through the path `evidence` collects one window with."""
    settings = configuration(tmp_path)
    request = SeriesRequest(period_days=7, periods=None, reference=REFERENCE, client=MagicMock())
    collected = patch(
        "metrics.trend.collected_repository_evidence",
        side_effect=lambda settings, _, repository, requested, __: repository_evidence(
            settings,
            repository,
            requested,
            Merges(pull_requests=()),
            WindowProvenance(offline=False, intervals_fetched=1),
        ),
    )
    with patch("metrics.trend.cached_repository_evidence") as cached, collected as collect:
        report = trend_report(settings, request)

    assert collect.call_count == 4
    cached.assert_not_called()
    assert report.repositories[0].baseline is not None
    assert report.repositories[0].baseline.provenance == WindowProvenance(offline=False, intervals_fetched=1)


def seeded_deltas(tmp_path: Path) -> Configuration:
    """Cache a baseline and two periods whose values move in both directions away from it.

    The first period halves both distributions and reviews everything; the second doubles them and
    reviews nothing, adding a direct commit to a baseline that had none. So every shape in the
    semantics table is asserted moving up and moving down, and one metric loses its sample entirely.
    """
    settings = configuration(tmp_path)
    seed(
        settings,
        window(0),
        (
            merged(1, window(0).starts_at + timedelta(days=1), reviewed=True),
            merged(2, window(0).starts_at + timedelta(days=3), reviewed=False),
        ),
    )
    seed(
        settings,
        window(1),
        (
            merged(3, window(1).starts_at + timedelta(days=1), reviewed=True, hours=2, lines=24),
            merged(4, window(1).starts_at + timedelta(days=2), reviewed=True, hours=2, lines=24),
        ),
    )
    seed(
        settings,
        window(2),
        (
            merged(5, window(2).starts_at + timedelta(days=1), reviewed=False, hours=8, lines=96),
            merged(6, window(2).starts_at + timedelta(days=2), reviewed=False, hours=8, lines=96),
        ),
        (pushed("bbb", window(2).starts_at + timedelta(days=3)),),
    )
    return settings


def delta_for(period: TrendPeriod, measure: str) -> TrendDelta:
    """Return one measure's delta from a period, failing the test when the period carries none."""
    return next(item for item in period.deltas if item.measure == measure)


def test_a_rate_delta_is_reported_in_percentage_points(tmp_path: Path) -> None:
    """Subtract the two rates rather than dividing one by the other: 50% to 100% is +50 points.

    The relative form would call the same movement +100%, and a reader cannot tell the two apart by
    looking, which is why the basis is on the wire beside the number.
    """
    series = trend_report(seeded_deltas(tmp_path), offline_request(periods=2)).repositories[0]

    improved = delta_for(series.periods[0], IndependentReviewCoverage.identifier)
    worsened = delta_for(series.periods[1], IndependentReviewCoverage.identifier)
    assert series.delta_detail is None
    assert (improved.baseline, improved.period, improved.change) == (50.0, 100.0, 50.0)
    assert (improved.basis, improved.unit, improved.percentile) == (DeltaBasis.PERCENTAGE_POINTS, "percent", None)
    assert (worsened.baseline, worsened.period, worsened.change) == (50.0, 0.0, -50.0)


def test_a_distribution_delta_moves_the_percentile_the_assessment_grades(tmp_path: Path) -> None:
    """Compare each distribution at its declared percentile, as a percentage change of it."""
    series = trend_report(seeded_deltas(tmp_path), offline_request(periods=2)).repositories[0]

    size = delta_for(series.periods[0], PullRequestSize.identifier)
    cycle = delta_for(series.periods[1], MergeCycleTime.identifier)
    assert (size.baseline, size.period, size.change) == (48, 24, -50.0)
    assert (size.basis, size.unit, size.percentile) == (DeltaBasis.PERCENTAGE_CHANGE, "lines", Percentile.PERCENTILE_75)
    assert (cycle.baseline, cycle.period, cycle.change) == (4, 8, 100.0)
    assert (cycle.unit, cycle.percentile) == ("hours", Percentile.MEDIAN)


def test_a_count_delta_reports_both_counts_beside_its_percentage_change(tmp_path: Path) -> None:
    """Report what each window merged, by both routes, so the percentage is never read alone."""
    series = trend_report(seeded_deltas(tmp_path), offline_request(periods=2)).repositories[0]

    merges = delta_for(series.periods[1], "merges")
    pull_requests = delta_for(series.periods[1], "merged-pull-requests")
    assert (merges.baseline, merges.period, merges.change) == (2, 3, 50.0)
    assert (merges.basis, merges.unit, merges.percentile) == (DeltaBasis.PERCENTAGE_CHANGE, None, None)
    assert (pull_requests.baseline, pull_requests.period, pull_requests.change) == (2, 2, 0.0)


def test_a_count_growing_from_a_zero_baseline_reports_the_counts_and_no_percentage(tmp_path: Path) -> None:
    """Report the reason rather than an infinite percentage: every rise from nothing is infinite."""
    series = trend_report(seeded_deltas(tmp_path), offline_request(periods=2)).repositories[0]

    commits = delta_for(series.periods[1], "direct-commits")
    assert (commits.baseline, commits.period) == (0, 1)
    assert commits.change is None
    assert commits.detail == "baseline-zero"


def test_a_metric_one_window_did_not_observe_carries_no_delta(tmp_path: Path) -> None:
    """Compare only what BOTH windows measured: a period with no reviews has no waiting time.

    The metric is still reported for the period, saying it observed no sample. Only the comparison
    is absent, because there is nothing to compare the baseline against.
    """
    series = trend_report(seeded_deltas(tmp_path), offline_request(periods=2)).repositories[0]

    period = series.periods[1]
    unobserved = next(item for item in period.metrics if item.metric == TimeToFirstReview.identifier)
    assert unobserved.value is None
    assert unobserved.summary.status is ObservationStatus.NOT_APPLICABLE
    assert TimeToFirstReview.identifier not in {item.measure for item in period.deltas}
    assert MergeCycleTime.identifier in {item.measure for item in period.deltas}


def test_an_unobserved_baseline_suppresses_every_delta_and_says_why(tmp_path: Path) -> None:
    """Report the whole series and compare none of it when the baseline window could not be read."""
    settings = configuration(tmp_path)
    seed(settings, window(1), (merged(3, window(1).starts_at + timedelta(days=1), reviewed=True),))
    seed(settings, window(2), (merged(5, window(2).starts_at + timedelta(days=1), reviewed=True),))

    series = trend_report(settings, offline_request(periods=2)).repositories[0]

    assert tuple(period.index for period in series.periods) == (1, 2)
    assert all(period.deltas == () for period in series.periods)
    assert series.delta_detail is not None
    assert series.delta_detail.startswith("the baseline window is not comparable, so no delta was computed: ")
    assert "cached pull_request evidence does not cover" in series.delta_detail


def test_a_thin_baseline_suppresses_every_delta_and_says_why(tmp_path: Path) -> None:
    """Refuse to compare against a baseline too small to have measured anything, and say so.

    Its counts are still reported; a rate over one merge is arithmetic, and a delta taken from one
    would show movement that is sampling noise.
    """
    settings = seeded_deltas(tmp_path)
    seed(settings, window(0), (merged(1, window(0).starts_at + timedelta(days=1), reviewed=True),))

    series = trend_report(settings, offline_request(periods=2)).repositories[0]

    assert series.baseline is not None
    assert series.baseline.throughput is not None
    assert series.baseline.throughput.merges == 1
    assert all(period.deltas == () for period in series.periods)
    assert series.delta_detail == (
        "the baseline window is not comparable, so no delta was computed: "
        "1 merges into the default branch, below the minimum of 2, so metric values are suppressed"
    )


def test_a_thin_period_compares_its_counts_and_nothing_else(tmp_path: Path) -> None:
    """Compare the counts of a suppressed period, which are honest, and none of its values.

    The counts are how a reader sees the period was thin, so dropping them would hide the reason the
    values are missing.
    """
    series = trend_report(seeded_series(tmp_path), offline_request()).repositories[0]

    thin = series.periods[1]
    assert thin.metrics == ()
    assert {item.measure for item in thin.deltas} == {
        "merges",
        "merged-pull-requests",
        "direct-commits",
        "active-contributors",
    }
    assert delta_for(thin, "merges").change == -50.0
    assert series.periods[2].deltas != thin.deltas


def collection(fetched_at: datetime, open_alerts: int) -> RepositoryInventory:
    """Build one collection run that observed the enabled repository's open alerts at an instant.

    Code scanning is withheld throughout, so every fixture here also asserts that an unreadable
    family contributes no row rather than a zero.
    """
    return RepositoryInventory(
        status=CollectionStatus.COMPLETE,
        organization="hmcts",
        collected_at=fetched_at,
        starts_at=fetched_at - SPAN,
        ends_at=fetched_at,
        repositories=(
            RepositoryInventoryItem(
                team_identifier="civil",
                repository=RepositoryMetadata(
                    name="cath-service",
                    default_branch="main",
                    archived=False,
                    fork=False,
                    disabled=False,
                    created_at=datetime(2024, 1, 1, tzinfo=UTC),
                    updated_at=fetched_at,
                    pushed_at=fetched_at,
                ),
                security=SecurityAlertEvidence(
                    dependabot=OpenAlertCount(open=open_alerts, by_severity={AlertSeverity.HIGH: open_alerts}),
                    code_scanning=OpenAlertCount(detail="code-scanning/alerts is not enabled for this repository"),
                    secret_scanning=OpenAlertCount(open=0),
                ),
            ),
        ),
        failures=(),
    )


def observe(settings: Configuration, day: int, open_alerts: int) -> None:
    """Append what one collection run on the given day of the series saw open."""
    record_alert_observations(
        observation_database(settings.database),
        collection(datetime(2026, 7, 1, tzinfo=UTC) + timedelta(days=day - 1, hours=9), open_alerts),
    )


def test_the_series_reports_every_alert_observation_made_across_its_windows(tmp_path: Path) -> None:
    """Report the open counts at the instants they were observed, and nothing in between.

    Three runs, one in the baseline and two in the periods, so a reader sees what the count did as
    the series progressed. Nothing is interpolated onto a period boundary and no instant nobody
    looked at is filled in, which is why the rows are the three runs and not the four windows.
    """
    settings = seeded_series(tmp_path)
    for day, open_alerts in ((14, 6), (21, 4), (35, 4)):
        observe(settings, day, open_alerts)

    series = trend_report(settings, offline_request()).repositories[0]

    assert [(item.family.value, item.fetched_at.day, item.open) for item in series.alert_observations] == [
        ("dependabot", 14, 6),
        ("dependabot", 21, 4),
        ("dependabot", 4, 4),
        ("secret-scanning", 14, 0),
        ("secret-scanning", 21, 0),
        ("secret-scanning", 4, 0),
    ]
    assert series.alert_observations[0].by_severity == {AlertSeverity.HIGH: 6}


def test_an_observation_outside_the_windows_the_series_reports_is_not_reported(tmp_path: Path) -> None:
    """Bound the observations by the series, from the baseline's start to the last whole period.

    The trailing partial period is excluded from the series, so an observation inside it is excluded
    for the same reason: it belongs to a stretch of time this report does not cover.
    """
    settings = seeded_series(tmp_path)
    observe(settings, 5, 9)
    observe(settings, 21, 4)
    observe(settings, 40, 1)

    series = trend_report(settings, offline_request()).repositories[0]

    assert series.periods[-1].ends_at == datetime(2026, 8, 8, tzinfo=UTC)
    assert {item.fetched_at.day for item in series.alert_observations} == {21}


def test_a_repository_with_no_series_reports_no_alert_observations(tmp_path: Path) -> None:
    """Report no observation for a repository with no windows to have observed anything across."""
    settings = seeded_series(tmp_path)
    observe(settings, 21, 4)

    report = trend_report(settings, offline_request())

    assert report.repositories[1].alert_observations == ()


@pytest.mark.parametrize(
    ("period_days", "periods", "message"),
    [(0, None, "at least one day"), (7, 0, "at least one period")],
)
def test_a_series_request_rejects_a_period_it_cannot_describe(
    period_days: int,
    periods: int | None,
    message: str,
) -> None:
    """Refuse a request that cannot describe a period before any window is resolved."""
    with pytest.raises(ValueError, match=message):
        SeriesRequest(period_days=period_days, periods=periods, reference=REFERENCE, client=None)
