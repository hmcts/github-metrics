"""Test immutable evidence contracts."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from metrics.domain import (
    AlertFamily,
    AlertObservation,
    AlertSeverity,
    CohortSummary,
    DeltaBasis,
    DistributionObservation,
    EvidenceSource,
    MergeGateEvidence,
    MergeGateReport,
    ObservationStatus,
    OpenAlertCount,
    OpenPullRequestReport,
    OpenPullRequestSummary,
    RateObservation,
    RepositoryInventoryIssue,
    RepositoryTrend,
    SecurityAlertEvidence,
    SecurityAlertReport,
    SourceCoverage,
    TrendDelta,
    TrendMetric,
    TrendPeriod,
    TrendThroughput,
    TrendWindow,
    WindowProvenance,
)


def test_internal_evidence_rejects_unknown_fields() -> None:
    """Reject misspelled or unsupported fields in application-owned evidence."""
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        RepositoryInventoryIssue.model_validate(
            {
                "team_identifier": "jwt",
                "repository": "jwt-middleware",
                "evidence": "merge_gate",
                "reason": "collection_failed",
                "detail": "GitHub returned invalid branch rules",
                "unexpected": True,
            },
        )


def test_source_coverage_requires_a_forward_interval() -> None:
    """Reject empty and reversed cache coverage intervals."""
    boundary = datetime(2026, 8, 6, tzinfo=UTC)

    with pytest.raises(ValidationError, match="coverage interval end must follow its start"):
        SourceCoverage(
            organization="hmcts",
            repository="cath-service",
            source=EvidenceSource.PULL_REQUEST,
            query_hash="testhash",
            starts_at=boundary,
            ends_at=boundary,
        )


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {
            "gate": MergeGateEvidence(
                branch="main",
                protected=False,
                pull_requests=(),
                status_checks=(),
                restricts_deletions=False,
                blocks_force_pushes=False,
            ),
            "detail": "no repository state has been collected",
        },
    ],
)
def test_merge_gate_report_requires_a_gate_or_the_reason_there_is_none(fields: dict[str, object]) -> None:
    """Refuse a merge gate block that is silently empty, or that both reports and disclaims a gate."""
    with pytest.raises(ValidationError, match="either a gate or the reason it is unavailable"):
        MergeGateReport.model_validate(fields)


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {
            "summary": OpenPullRequestSummary(
                opened_in_window=1,
                closed_without_merge=0,
                currently_open=1,
                stale_open=0,
            ),
            "detail": "open pull-request state is never cached; omit --offline to observe it",
        },
    ],
)
def test_open_pull_request_report_requires_a_summary_or_the_reason_there_is_none(fields: dict[str, object]) -> None:
    """Refuse a report that is silently empty, or that both reports and disclaims a summary."""
    with pytest.raises(ValidationError, match="either a summary or the reason it is unavailable"):
        OpenPullRequestReport.model_validate(fields)


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {
            "alerts": SecurityAlertEvidence(
                dependabot=OpenAlertCount(open=0),
                code_scanning=OpenAlertCount(open=0),
                secret_scanning=OpenAlertCount(open=0),
            ),
            "detail": "no repository state has been collected; run metrics collect",
        },
    ],
)
def test_security_alert_report_requires_alerts_or_the_reason_there_are_none(fields: dict[str, object]) -> None:
    """Refuse a report that is silently empty, or that both reports and disclaims alerts.

    An omitted block would read as a repository with nothing open, which is the opposite of a
    repository nobody has looked at.
    """
    with pytest.raises(ValidationError, match="either alerts or the reason they are unavailable"):
        SecurityAlertReport.model_validate(fields)


def trend_window_fields() -> dict[str, object]:
    """Describe one observed trend window: its interval, and the block that observed it."""
    starts_at = datetime(2026, 7, 18, tzinfo=UTC)
    return {
        "starts_at": starts_at,
        "ends_at": starts_at + timedelta(days=28),
        "provenance": WindowProvenance(offline=True, intervals_fetched=0),
        "cohort": CohortSummary(merged=4, reported=4, excluded_authors={}, direct_commits=1),
        "throughput": TrendThroughput(merges=5, merged_pull_requests=4, direct_commits=1, active_contributors=3),
    }


@pytest.mark.parametrize("removed", ["throughput", "provenance", "cohort"])
def test_trend_window_reports_its_cohort_throughput_and_provenance_together(removed: str) -> None:
    """Refuse a window carrying counts without the provenance and throughput that describe them."""
    fields = {key: value for key, value in trend_window_fields().items() if key != removed}

    with pytest.raises(ValidationError, match="cohort, throughput and provenance together"):
        TrendWindow.model_validate(fields)


@pytest.mark.parametrize(
    "added",
    [
        {},
        {
            "detail": "cached pull_request evidence does not cover 2026-07-18T00:00Z to 2026-08-15T00:00Z",
            "metrics": (
                TrendMetric(
                    metric="independent-review-coverage",
                    summary=RateObservation(status=ObservationStatus.OBSERVED, numerator=1, denominator=2),
                ),
            ),
        },
    ],
)
def test_trend_window_that_observed_nothing_carries_the_reason_and_no_values(added: dict[str, object]) -> None:
    """Refuse a silently empty window, and refuse metric values from a window that observed none."""
    fields = {key: value for key, value in trend_window_fields().items() if key in {"starts_at", "ends_at"}} | added

    with pytest.raises(ValidationError, match="carries the reason and no metric values"):
        TrendWindow.model_validate(fields)


def test_a_repository_with_no_enablement_instant_reports_no_series() -> None:
    """Refuse a series that is not anchored to the instant it claims to be measured against."""
    with pytest.raises(ValidationError, match="no series to anchor"):
        RepositoryTrend(
            repository="cath-service",
            periods=(TrendPeriod.model_validate(trend_window_fields() | {"index": 1}),),
        )


def test_a_repository_reporting_no_period_says_why() -> None:
    """Refuse an empty series that does not say whether it lacks an anchor or lacks history."""
    with pytest.raises(ValidationError, match="must carry the reason"):
        RepositoryTrend(repository="cath-service")


def test_a_repository_reporting_no_period_carries_no_alert_observation() -> None:
    """Refuse observations on a repository with no windows to have selected them across.

    The range they are read over is the series itself, so a repository without one has nothing to
    have selected them by and any rows here would have come from somewhere else.
    """
    with pytest.raises(ValidationError, match="no range to observe alerts over"):
        RepositoryTrend(
            repository="cath-service",
            detail="no enablement date configured",
            alert_observations=(
                AlertObservation(
                    family=AlertFamily.DEPENDABOT,
                    fetched_at=datetime(2026, 7, 14, tzinfo=UTC),
                    open=3,
                ),
            ),
        )


def test_an_observation_of_secret_scanning_cannot_carry_a_severity() -> None:
    """Hold the no-severity ruling in the schema, because a severity invented once is stored for ever.

    GitHub grades neither a leaked test fixture nor a live production key, so a stored observation
    that graded one would be an assertion this tool is not entitled to make — and unlike a rendering,
    it would survive every later run.
    """
    with pytest.raises(ValidationError, match="carry no severity"):
        AlertObservation(
            family=AlertFamily.CREDENTIAL_SCANNING,
            fetched_at=datetime(2026, 7, 14, tzinfo=UTC),
            open=2,
            by_severity={AlertSeverity.CRITICAL: 2},
        )


def test_the_alert_families_are_paired_with_their_counts_in_one_fixed_order() -> None:
    """Pair each family with its own count once, so no reader of the block can mis-pair them."""
    alerts = SecurityAlertEvidence(
        dependabot=OpenAlertCount(open=1),
        code_scanning=OpenAlertCount(open=2),
        secret_scanning=OpenAlertCount(detail="not enabled"),
    )

    assert alerts.families() == (
        (AlertFamily.DEPENDABOT, alerts.dependabot),
        (AlertFamily.CODE_SCANNING, alerts.code_scanning),
        (AlertFamily.CREDENTIAL_SCANNING, alerts.secret_scanning),
    )


@pytest.mark.parametrize(
    "added",
    [{}, {"change": 50.0, "detail": "baseline-zero"}],
)
def test_a_delta_carries_either_its_change_or_the_reason_it_has_none(added: dict[str, object]) -> None:
    """Refuse a delta that reports no movement without saying why, and one that does both."""
    with pytest.raises(ValidationError, match="either its change or the reason"):
        TrendDelta.model_validate(
            {"measure": "merges", "basis": DeltaBasis.PERCENTAGE_CHANGE, "baseline": 0, "period": 3} | added,
        )


def test_a_repository_reporting_why_it_has_no_delta_cannot_carry_one() -> None:
    """Refuse a series that says its baseline is not comparable and then compares against it."""
    period = TrendPeriod.model_validate(
        trend_window_fields()
        | {
            "index": 1,
            "deltas": (
                TrendDelta(measure="merges", basis=DeltaBasis.PERCENTAGE_CHANGE, baseline=4, period=5, change=25.0),
            ),
        },
    )

    with pytest.raises(ValidationError, match="cannot carry one"):
        RepositoryTrend(
            repository="cath-service",
            enablement_at=datetime(2026, 7, 18, tzinfo=UTC),
            periods=(period,),
            delta_detail="the baseline window is not comparable, so no delta was computed: nothing was observed",
        )


def test_distribution_rejects_negative_percentiles() -> None:
    """Reject impossible durations in behaviour evidence."""
    with pytest.raises(ValidationError, match="Input should be greater than or equal to 0"):
        DistributionObservation(
            status=ObservationStatus.OBSERVED,
            sample_size=1,
            unit="hours",
            median=-1,
            percentile_75=1,
            percentile_90=1,
        )
