"""Test immutable evidence contracts."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from metrics.domain import (
    AlertFamily,
    AlertObservation,
    AlertSeverity,
    CodeownersEvidence,
    CodeownersFile,
    CodeownersReport,
    CohortSummary,
    DeltaBasis,
    DistributionObservation,
    EvidenceSource,
    MaintenanceEvidence,
    MaintenanceReport,
    MaintenanceWindowStatus,
    MergeGateEvidence,
    MergeGateReport,
    ObservationStatus,
    OpenAlertCount,
    OpenPullRequestReport,
    OpenPullRequestSummary,
    RateObservation,
    RepositoryInventoryIssue,
    RepositoryInventoryItem,
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


MAINTENANCE_INSTANT = datetime(2026, 8, 1, tzinfo=UTC)


def observed_maintenance() -> MaintenanceEvidence:
    """Describe a branch whose newest commit was authored by a person."""
    return MaintenanceEvidence(
        branch="main",
        last_commit_at=MAINTENANCE_INSTANT,
        last_human_commit_at=MAINTENANCE_INSTANT,
        searched_back_to=None,
    )


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {
            "codeowners": CodeownersEvidence(files=()),
            "detail": "no repository state has been collected; run metrics collect",
        },
    ],
)
def test_codeowners_report_requires_evidence_or_the_reason_there_is_none(fields: dict[str, object]) -> None:
    """Refuse a CODEOWNERS block that is silently empty, or that both reports and disclaims one."""
    with pytest.raises(ValidationError, match="either its evidence or the reason it is unavailable"):
        CodeownersReport.model_validate(fields)


def test_a_codeowners_report_keeps_an_unrecognised_file_visible_as_one() -> None:
    """Report a `.md` variant as found and not read by GitHub, so the letter and the effect stay apart."""
    report = CodeownersReport(
        fetched_at=MAINTENANCE_INSTANT,
        codeowners=CodeownersEvidence(
            files=(CodeownersFile(path="CODEOWNERS.md", size_bytes=120, recognised_by_github=False),),
        ),
    )

    assert report.codeowners is not None
    assert report.codeowners.files[0].recognised_by_github is False


@pytest.mark.parametrize(
    ("fields", "match"),
    [
        (
            {"last_commit_at": None, "last_human_commit_at": MAINTENANCE_INSTANT, "searched_back_to": None},
            "nothing to have searched",
        ),
        (
            {"last_commit_at": None, "last_human_commit_at": None, "searched_back_to": MAINTENANCE_INSTANT},
            "nothing to have searched",
        ),
        (
            {"last_commit_at": MAINTENANCE_INSTANT, "last_human_commit_at": None, "searched_back_to": None},
            "how far back the search examined",
        ),
        (
            {
                "last_commit_at": MAINTENANCE_INSTANT,
                "last_human_commit_at": MAINTENANCE_INSTANT,
                "searched_back_to": MAINTENANCE_INSTANT,
            },
            "a found human commit carries no search bound",
        ),
    ],
)
def test_maintenance_evidence_rejects_shapes_the_search_cannot_produce(
    fields: dict[str, object],
    match: str,
) -> None:
    """Refuse instants no run of the bounded search could have recorded together."""
    with pytest.raises(ValidationError, match=match):
        MaintenanceEvidence.model_validate({"branch": "main"} | fields)


def test_maintenance_evidence_accepts_each_shape_the_search_produces() -> None:
    """Accept an empty branch, a search that found nobody, and a found human commit."""
    empty_branch = MaintenanceEvidence(
        branch="main",
        last_commit_at=None,
        last_human_commit_at=None,
        searched_back_to=None,
    )
    nobody_found = MaintenanceEvidence(
        branch="main",
        last_commit_at=MAINTENANCE_INSTANT,
        last_human_commit_at=None,
        searched_back_to=MAINTENANCE_INSTANT - timedelta(days=730),
    )

    assert empty_branch.last_commit_at is None
    assert nobody_found.searched_back_to is not None
    assert observed_maintenance().last_human_commit_at == MAINTENANCE_INSTANT


@pytest.mark.parametrize(
    "added",
    [{}, {"human_committed_within": True, "human_detail": "the search examined commits back to 2026-05-01"}],
)
def test_a_window_human_answer_is_decided_or_carries_its_reason(added: dict[str, object]) -> None:
    """Refuse an unknown human answer with no reason, and a decided one that carries one anyway."""
    with pytest.raises(ValidationError, match="either decided or carries the reason it is unknown"):
        MaintenanceWindowStatus.model_validate({"months": 6, "committed_within": True} | added)


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {
            "maintenance": observed_maintenance(),
            "windows": (MaintenanceWindowStatus(months=6, committed_within=True, human_committed_within=True),),
            "detail": "no repository state has been collected; run metrics collect",
        },
    ],
)
def test_maintenance_report_requires_evidence_or_the_reason_there_is_none(fields: dict[str, object]) -> None:
    """Refuse a maintenance block that is silently empty, or that both reports and disclaims one."""
    with pytest.raises(ValidationError, match="either its evidence or the reason it is unavailable"):
        MaintenanceReport.model_validate(fields)


@pytest.mark.parametrize(
    "fields",
    [
        {"maintenance": observed_maintenance()},
        {
            "detail": "no repository state has been collected; run metrics collect",
            "windows": (MaintenanceWindowStatus(months=6, committed_within=True, human_committed_within=True),),
        },
    ],
)
def test_maintenance_window_rows_accompany_evidence_and_never_a_reason(fields: dict[str, object]) -> None:
    """Refuse evidence with no derived window rows, and window rows derived from nothing."""
    with pytest.raises(ValidationError, match="window rows accompany maintenance evidence"):
        MaintenanceReport.model_validate(fields)


def test_a_maintenance_report_carries_its_window_rows_beside_its_evidence() -> None:
    """Report the derived 6/12/24-month answers beside the instants they were derived from."""
    report = MaintenanceReport(
        fetched_at=MAINTENANCE_INSTANT,
        maintenance=observed_maintenance(),
        windows=(
            MaintenanceWindowStatus(months=6, committed_within=True, human_committed_within=True),
            MaintenanceWindowStatus(
                months=12,
                committed_within=True,
                human_detail="the search examined commits back to 2026-05-01, short of the 12-month cutoff",
            ),
        ),
    )

    assert report.windows[0].human_committed_within is True
    assert report.windows[1].human_committed_within is None


def test_a_repository_state_row_stored_before_the_minimum_standards_blocks_still_parses() -> None:
    """Read an old stored row back as "not collected", never as an absent file or an empty branch."""
    item = RepositoryInventoryItem.model_validate(
        {
            "team_identifier": "jwt",
            "repository": {
                "name": "cath-service",
                "default_branch": "master",
                "archived": False,
                "fork": False,
                "disabled": False,
                "created_at": "2020-01-01T00:00:00Z",
                "updated_at": "2026-08-01T00:00:00Z",
                "pushed_at": None,
            },
        },
    )

    assert item.codeowners is None
    assert item.maintenance is None


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
