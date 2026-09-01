"""Test the plain-text rendering of evidence reports."""

from datetime import UTC, datetime, timedelta
from itertools import combinations

import pytest

from metrics.domain import (
    ActorReadiness,
    ActorRepositoryReadiness,
    AlertFamily,
    AlertObservation,
    AlertSeverity,
    BehaviourEvidenceCollection,
    BehaviourEvidenceReport,
    BehaviourMetricSummary,
    CodeownersEvidence,
    CodeownersFile,
    CodeownersReport,
    CohortSummary,
    DeltaBasis,
    DistributionObservation,
    EvidenceUnavailable,
    FindingSeverity,
    MaintenanceEvidence,
    MaintenanceReport,
    MaintenanceWindowStatus,
    MergeGateEvidence,
    MergeGateReport,
    ObservationStatus,
    OpenAlertCount,
    OpenPullRequestReport,
    OpenPullRequestSummary,
    Percentile,
    PracticeEvidenceReport,
    PracticeFinding,
    PullRequestRule,
    RateObservation,
    ReadinessAssessment,
    ReadinessCondition,
    ReadinessLabel,
    RepositoryPracticeEvidence,
    RepositoryTrend,
    SecurityAlertEvidence,
    SecurityAlertReport,
    SonarGateLevel,
    SonarMeasures,
    SonarProjectMapping,
    SonarQualityGate,
    SonarQualityGateCondition,
    SonarRating,
    SonarReport,
    SonarResolution,
    StatusCheck,
    StatusChecksRule,
    TrendDelta,
    TrendMetric,
    TrendPeriod,
    TrendReport,
    TrendThroughput,
    TrendWindow,
    WindowProvenance,
)
from metrics.render import (
    ACTOR_COMBINATION_ORDER,
    ACTOR_GROUPS,
    ACTOR_ROW_INDENT,
    ACTOR_UNGROUPED,
    RepositoryDrillDown,
    actor_combination,
    actor_combination_counts,
    actor_group,
    percentage_of,
    render_practice_report,
    render_report,
    render_trend_report,
)


def starts_at() -> datetime:
    """Return the start of the window every fixture covers."""
    return datetime(2026, 5, 1, tzinfo=UTC)


def ends_at() -> datetime:
    """Return the exclusive end of the window every fixture covers."""
    return datetime(2026, 8, 1, tzinfo=UTC)


def provenance() -> WindowProvenance:
    """Build the provenance of a window served entirely from the cache."""
    return WindowProvenance(offline=True, intervals_fetched=0)


def assessment() -> ReadinessAssessment:
    """Build an assessment carrying a blocking condition, a caution, and no clear condition."""
    return ReadinessAssessment(
        label=ReadinessLabel.AMBER,
        blocking=(
            ReadinessCondition(
                condition="approval-coverage-below-target",
                label=ReadinessLabel.AMBER,
                detail="approval-coverage is 50% (1 of 2), below the 90% target",
            ),
        ),
        caution=(
            ReadinessCondition(
                condition="status-checks-not-required",
                detail="status checks required before merging to master: 0, so CI cannot block a merge",
            ),
        ),
        clear=(),
    )


def gate_report() -> MergeGateReport:
    """Build a stored gate whose enforcement on administrators GitHub did not disclose."""
    return MergeGateReport(
        fetched_at=datetime(2026, 8, 2, 9, 30, tzinfo=UTC),
        gate=MergeGateEvidence(
            branch="master",
            protected=True,
            pull_requests=(
                PullRequestRule(
                    dismiss_stale_reviews_on_push=False,
                    require_code_owner_review=False,
                    require_last_push_approval=False,
                    required_approving_review_count=1,
                    required_review_thread_resolution=False,
                ),
            ),
            status_checks=(
                StatusChecksRule(
                    strict_required_status_checks_policy=True,
                    required_status_checks=(StatusCheck(context="build"),),
                ),
            ),
            restricts_deletions=True,
            blocks_force_pushes=True,
            rules_observed=True,
        ),
    )


def open_pull_requests_report() -> OpenPullRequestReport:
    """Build the stored open pull-request state, observed over a window of its own."""
    return OpenPullRequestReport(
        fetched_at=datetime(2026, 8, 2, 9, 30, tzinfo=UTC),
        starts_at=datetime(2026, 7, 26, tzinfo=UTC),
        ends_at=datetime(2026, 8, 2, tzinfo=UTC),
        summary=OpenPullRequestSummary(
            opened_in_window=5,
            closed_without_merge=1,
            currently_open=3,
            stale_open=2,
        ),
    )


def finding() -> PracticeFinding:
    """Build one actor-level finding split across two size classes."""
    return PracticeFinding(
        rule="unreviewed-merge",
        severity=FindingSeverity.MEDIUM,
        actor_login="author",
        occurrences=2,
        authored_merges=4,
        percentage=50.0,
        message="author: 2 of 4 merges had no independent human review (50%) — 1 substantial, 1 trivial",
        occurrences_by_size={"substantial": 1, "trivial": 1},
        pull_requests=(),
    )


def metric_summary(
    metric: str,
    summary: RateObservation | DistributionObservation,
    classifications: dict[str, int] | None = None,
) -> BehaviourMetricSummary:
    """Summarise one metric over the shared window, as a repository block carries it."""
    return BehaviourMetricSummary(
        metric=metric,
        summary=summary,
        classifications={} if classifications is None else classifications,
    )


def behaviour_report(
    metric: str,
    summary: RateObservation | DistributionObservation,
    classifications: dict[str, int] | None = None,
) -> BehaviourEvidenceReport:
    """Build one metric drill-down over the shared window."""
    return BehaviourEvidenceReport(
        organization="hmcts",
        repository="cath-service",
        metric=metric,
        starts_at=starts_at(),
        ends_at=ends_at(),
        provenance=provenance(),
        cohort=CohortSummary(merged=3, reported=2, excluded_authors={"renovate": 1}),
        summary=summary,
        classifications={} if classifications is None else classifications,
        identities_included=False,
    )


def rate(numerator: int, denominator: int) -> RateObservation:
    """Build an observed rate."""
    return RateObservation(status=ObservationStatus.OBSERVED, numerator=numerator, denominator=denominator)


def hours() -> DistributionObservation:
    """Build an observed distribution measured in hours."""
    return DistributionObservation(
        status=ObservationStatus.OBSERVED,
        sample_size=2,
        unit="hours",
        median=12.5,
        percentile_75=30.25,
        percentile_90=48.0,
    )


def unobserved() -> RateObservation:
    """Build a rate with no denominator to divide by."""
    return RateObservation(status=ObservationStatus.NOT_APPLICABLE, numerator=0, denominator=0)


def drill_down() -> RepositoryDrillDown:
    """Build the metric aggregates and review counts shown beside the practice evidence."""
    return RepositoryDrillDown(
        behaviour=(
            metric_summary("approval-coverage", rate(1, 2)),
            metric_summary("merge-cycle-time", hours()),
            metric_summary("checks-passing-at-merge", unobserved()),
        ),
        review_states={"APPROVED": 3, "COMMENTED": 1},
    )


def security_report() -> SecurityAlertReport:
    """Build stored security alerts covering a graded family, an ungraded one and a refused one."""
    return SecurityAlertReport(
        fetched_at=datetime(2026, 8, 2, 9, 30, tzinfo=UTC),
        alerts=SecurityAlertEvidence(
            dependabot=OpenAlertCount(
                open=5,
                by_severity={AlertSeverity.CRITICAL: 1, AlertSeverity.HIGH: 2, AlertSeverity.LOW: 1},
            ),
            code_scanning=OpenAlertCount(detail="GitHub permission denied"),
            secret_scanning=OpenAlertCount(open=2),
        ),
    )


def codeowners_report() -> CodeownersReport:
    """Build stored CODEOWNERS state holding a file GitHub reads and a `.md` variant it ignores."""
    return CodeownersReport(
        fetched_at=datetime(2026, 8, 2, 9, 30, tzinfo=UTC),
        codeowners=CodeownersEvidence(
            files=(
                CodeownersFile(path=".github/CODEOWNERS", size_bytes=120, recognised_by_github=True),
                CodeownersFile(path="docs/CODEOWNERS.md", size_bytes=0, recognised_by_github=False),
            ),
        ),
    )


def maintenance_report() -> MaintenanceReport:
    """Build stored maintenance state whose last human commit sits between the 6- and 12-month cutoffs."""
    return MaintenanceReport(
        fetched_at=datetime(2026, 8, 2, 9, 30, tzinfo=UTC),
        maintenance=MaintenanceEvidence(
            branch="master",
            last_commit_at=datetime(2026, 7, 30, 8, 0, tzinfo=UTC),
            last_human_commit_at=datetime(2026, 1, 10, tzinfo=UTC),
            searched_back_to=None,
        ),
        windows=(
            MaintenanceWindowStatus(months=6, committed_within=True, human_committed_within=False),
            MaintenanceWindowStatus(months=12, committed_within=True, human_committed_within=True),
            MaintenanceWindowStatus(months=24, committed_within=True, human_committed_within=True),
        ),
    )


def sonar_report() -> SonarReport:
    """Build the SonarCloud report of a repository no SonarCloud project is mapped to."""
    return SonarReport(
        fetched_at=datetime(2026, 8, 2, 9, 30, tzinfo=UTC),
        detail="no SonarCloud project is mapped to this repository",
    )


def sonar_mapping(**overrides: object) -> SonarProjectMapping:
    """Attribute the live half of a duplicate project pair to the repository the fixtures report."""
    mapping = SonarProjectMapping(
        project_key="rpx-xui-webapp_2",
        repository="cath-service",
        method=SonarResolution.STORED_MAP,
        analysis_at=datetime(2026, 8, 1, 4, 12, tzinfo=UTC),
        revision="9f1c0d4a2b6e8f70d1c3a5b7e9f1c0d4a2b6e8f7",
    )
    return mapping.model_copy(update=overrides)


def sonar_measures(**overrides: object) -> SonarMeasures:
    """Build a fully measured project whose gate passed, overriding selected measures."""
    measures = SonarMeasures(
        project_key="rpx-xui-webapp_2",
        analysis_at=datetime(2026, 8, 1, 4, 12, tzinfo=UTC),
        gate=SonarQualityGate(
            level=SonarGateLevel.OK,
            conditions=(
                SonarQualityGateCondition(
                    metric="new_coverage",
                    comparator="LT",
                    threshold="80",
                    actual="91.4",
                    level=SonarGateLevel.OK,
                ),
            ),
        ),
        coverage=74.2,
        duplicated_lines_density=1.5,
        # Over a million, so a `%g` rendering would print it as an exponent.
        lines_of_code=1234567,
        violations=318,
        reliability_issues=12,
        maintainability_issues=280,
        security_issues=3,
        security_hotspots=7,
        reliability_rating=SonarRating(value=3.0),
        maintainability_rating=SonarRating(value=1.0),
        security_rating=SonarRating(value=2.0),
        security_review_rating=SonarRating(value=5.0),
    )
    return measures.model_copy(update=overrides)


def mapped_sonar_report(**overrides: object) -> SonarReport:
    """Build the stored SonarCloud state of a repository whose project resolved and was measured."""
    report = SonarReport(
        fetched_at=datetime(2026, 8, 2, 9, 30, tzinfo=UTC),
        mapping=sonar_mapping(),
        measures=sonar_measures(),
    )
    return report.model_copy(update=overrides)


def practice_evidence(**overrides: object) -> RepositoryPracticeEvidence:
    """Build one repository's practice evidence, overriding selected fields."""
    evidence = RepositoryPracticeEvidence(
        repository="cath-service",
        team="crime",
        starts_at=starts_at(),
        ends_at=ends_at(),
        provenance=provenance(),
        cohort=CohortSummary(merged=3, reported=2, excluded_authors={"renovate": 1}, direct_commits=1),
        assessment=assessment(),
        merge_gate=gate_report(),
        open_pull_requests=open_pull_requests_report(),
        security=security_report(),
        codeowners=codeowners_report(),
        maintenance=maintenance_report(),
        sonar=sonar_report(),
        metrics=drill_down().behaviour,
        behaviour=(finding(),),
    )
    return evidence.model_copy(update=overrides)


def teams() -> dict[str, str]:
    """Map every repository the fixtures name to the team that owns it."""
    return {"cath-service": "crime", "other-service": "civil"}


def practice_report(
    evidence: RepositoryPracticeEvidence | None = None,
    unavailable: tuple[EvidenceUnavailable, ...] = (),
    actors: tuple[ActorReadiness, ...] = (),
) -> PracticeEvidenceReport:
    """Build the default practice report for one repository."""
    return PracticeEvidenceReport(
        organization="hmcts",
        repositories=(practice_evidence() if evidence is None else evidence,),
        unavailable=unavailable,
        actors=actors,
    )


@pytest.fixture
def report() -> str:
    """Render the default practice report for the shared fixtures."""
    return render_practice_report(practice_report(), {"cath-service": drill_down()}, teams())


def index_lines(report: str) -> list[str]:
    """Return the index block's lines, its column titles included and its heading excluded."""
    lines = report.splitlines()
    body = lines[lines.index("Index") + 2 :]
    return body[: body.index("")]


def test_every_section_of_the_practice_report_is_rendered(report: str) -> None:
    """Print the index, header, readiness, cohort, gate, behaviour, reviews, and findings."""
    assert "\n==================\nhmcts/cath-service" in report
    assert "Window      2026-05-01T00:00Z to 2026-08-01T00:00Z (92 days)" in report
    assert "Provenance  offline, 0 intervals fetched" in report
    assert "Readiness: AMBER" in report
    for section in (
        "Cohort",
        "Merge gate, read 2026-08-02T09:30Z",
        "Security alerts, read 2026-08-02T09:30Z",
        "SonarCloud, read 2026-08-02T09:30Z",
        "CODEOWNERS, read 2026-08-02T09:30Z",
        "Maintenance, read 2026-08-02T09:30Z",
        "Open pull requests, read 2026-08-02T09:30Z",
        "Behaviour",
        "Review breakdown",
        "Findings",
    ):
        assert f"\n{section}\n{'-' * len(section)}\n" in report


def test_a_blocking_condition_shows_the_ceiling_it_imposes(report: str) -> None:
    """Distinguish amber from red inside the blocking list, above the evidence for it."""
    assert "    approval-coverage-below-target  [amber]" in report
    assert "      approval-coverage is 50% (1 of 2), below the 90% target" in report


def test_a_caution_imposes_no_ceiling_and_shows_no_label(report: str) -> None:
    """Report a caution with its evidence but without a label it does not impose."""
    assert (
        "  Caution\n"
        "    status-checks-not-required\n"
        "      status checks required before merging to master: 0, so CI cannot block a merge\n"
    ) in report


def test_an_empty_assessment_section_says_so_rather_than_vanishing(report: str) -> None:
    """State that a section was checked and held nothing, so silence is never ambiguous."""
    assert "  Clear: none" in report


def test_an_unassessed_repository_says_why_there_is_no_label() -> None:
    """Report a disabled assessment rather than printing an unjudged label."""
    rendered = render_practice_report(
        practice_report(practice_evidence(assessment=None)),
        {"cath-service": drill_down()},
        teams(),
    )

    assert "  not assessed: assessment.enabled is false" in rendered
    assert "Readiness: " not in rendered


def test_the_cohort_reconciles_against_its_excluded_authors(report: str) -> None:
    """Show the merged total, the reported cohort, the bypasses, and every author left out."""
    assert "  Merged pull requests  3" in report
    assert "  Reported in cohort    2" in report
    assert "  Direct commits        1" in report
    # The denominator the governance rates beside it are measured over, rather than left to arithmetic.
    assert "  Total merges          3" in report
    assert "  Excluded renovate     1" in report


def test_an_unattributed_excluded_author_is_named_rather_than_left_blank() -> None:
    """Never render an excluded author as an empty label."""
    cohort = CohortSummary(merged=2, reported=1, excluded_authors={"": 1})
    rendered = render_practice_report(
        practice_report(practice_evidence(cohort=cohort)),
        {"cath-service": drill_down()},
        teams(),
    )

    assert "Excluded (unattributed)  1" in rendered


def test_the_gate_reports_its_rules_and_the_instant_it_was_read(report: str) -> None:
    """Show the declared gate beside the instant it was collected, not the reporting window."""
    assert "  Branch                         master" in report
    assert "  Approving reviews required     1" in report
    assert "  Required status checks         build" in report
    assert "  Dismiss stale reviews on push  no" in report


def test_undisclosed_gate_enforcement_is_not_rendered_as_a_bypass(report: str) -> None:
    """Never present an unobservable field as a gate administrators can bypass."""
    assert "  Applies to administrators      not disclosed" in report


def test_the_gate_reports_the_two_rule_types_added_on_2026_08_15(report: str) -> None:
    """Show linear history and branch naming in the report, not only in the JSON."""
    assert "  Requires linear history        no" in report
    assert "  Restricts branch names         no" in report


def test_a_gate_with_no_uninterpreted_rules_says_so_rather_than_vanishing(report: str) -> None:
    """State that nothing was dropped, so an empty catch-all is never ambiguous."""
    assert "  Rules not interpreted          none" in report


def test_the_gate_names_the_rules_this_tool_could_not_interpret() -> None:
    """Admit the tool's limits in the report — hiding them there is worse than in the JSON."""
    gate = gate_report()
    assert gate.gate is not None
    limited = MergeGateReport(
        fetched_at=gate.fetched_at,
        gate=gate.gate.model_copy(
            update={
                "requires_linear_history": True,
                "restricts_branch_names": True,
                "unmodelled_rules": ("merge_queue", "tag_name_pattern"),
            },
        ),
    )
    rendered = render_practice_report(
        practice_report(practice_evidence(merge_gate=limited)),
        {"cath-service": drill_down()},
        teams(),
    )

    assert "  Requires linear history        yes" in rendered
    assert "  Restricts branch names         yes" in rendered
    assert "  Rules not interpreted          merge_queue, tag_name_pattern" in rendered


def test_a_gate_that_was_never_collected_reports_the_reason() -> None:
    """State why there is no gate instead of omitting the block, which would read as no gate."""
    detail = "no repository state has been collected; run metrics collect"
    rendered = render_practice_report(
        practice_report(practice_evidence(merge_gate=MergeGateReport(detail=detail))),
        {"cath-service": drill_down()},
        teams(),
    )

    assert f"  not available: {detail}" in rendered


def test_open_pull_requests_reports_the_four_counts(report: str) -> None:
    """Show every counted state beside the instant the state was observed."""
    assert "  Opened in window        5" in report
    assert "  Closed without merge    1" in report
    assert "  Currently open          3" in report
    assert "  Stale open              2" in report


def test_open_pull_requests_names_the_window_the_two_windowed_counts_cover(report: str) -> None:
    """Print the window the stored counts were measured over, not the window of the report.

    The block is current state read back from the last collection, so its window is that
    collection's — a reader taking the report's own window instead would read seven days of opened
    pull requests as ninety.
    """
    assert "  Opened and closed over  2026-07-26T00:00Z to 2026-08-02T00:00Z" in report
    assert "Window      2026-05-01T00:00Z to 2026-08-01T00:00Z (92 days)" in report


def test_open_pull_requests_never_collected_states_the_reason() -> None:
    """State why open pull-request state is missing rather than omitting the block.

    No window is printed here, because a report with nothing to report was measured over nothing.
    """
    detail = "open pull-request state was not collected when repository state was stored; run metrics collect"
    rendered = render_practice_report(
        practice_report(practice_evidence(open_pull_requests=OpenPullRequestReport(detail=detail))),
        {"cath-service": drill_down()},
        teams(),
    )

    assert f"  not available: {detail}" in rendered
    assert "Opened and closed over" not in rendered


def test_rates_and_distributions_fill_the_columns_each_of_them_has(report: str) -> None:
    """Leave a column a metric cannot fill visibly empty rather than printing an unmeasured zero."""
    assert "  Metric                             Rate  Sample   Unit  Median    p75  p90" in report
    assert "  approval-coverage                   50%  1 of 2      -       -      -    -" in report
    assert "  merge-cycle-time                      -       2  hours    12.5  30.25   48" in report


def test_an_empty_cohort_is_not_applicable_rather_than_an_observed_zero(report: str) -> None:
    """Never let a missing denominator render as behaviour that was measured at zero."""
    assert "  checks-passing-at-merge  not applicable" in report


def test_reviews_are_counted_by_the_state_they_were_submitted_in(report: str) -> None:
    """Break the cohort's review events down by state."""
    assert "  APPROVED   3" in report
    assert "  COMMENTED  1" in report


def test_a_cohort_with_no_reviews_says_so() -> None:
    """State that no review events exist rather than rendering an empty block."""
    rendered = render_practice_report(
        practice_report(),
        {"cath-service": RepositoryDrillDown(behaviour=drill_down().behaviour, review_states={})},
        teams(),
    )

    assert "  no review events in the cohort" in rendered


def test_findings_carry_one_column_for_every_observed_size_class(report: str) -> None:
    """Split occurrences by size, because a one-line fix and a large change are different facts."""
    assert "  Actor               Rule  Occurrences  Authored  Percent  substantial  trivial" in report
    assert "  author  unreviewed-merge            2         4      50%            1        1" in report


def test_a_repository_with_no_findings_says_none() -> None:
    """Report an empty findings list explicitly."""
    rendered = render_practice_report(
        practice_report(practice_evidence(behaviour=())),
        {"cath-service": drill_down()},
        teams(),
    )

    assert "Findings\n--------\n  none" in rendered


def test_repositories_without_cached_evidence_are_listed_rather_than_omitted() -> None:
    """Name every configured repository the window could not be reported for."""
    unavailable = (EvidenceUnavailable(repository="other-service", detail="cached evidence does not cover 2026-05-01"),)
    rendered = render_practice_report(
        practice_report(unavailable=unavailable),
        {"cath-service": drill_down()},
        teams(),
    )

    assert "  other-service  cached evidence does not cover 2026-05-01" in rendered


def test_a_metric_drill_down_renders_its_aggregate_and_classifications() -> None:
    """Explain a single metric with the classification counts behind its aggregate."""
    rendered = render_report(
        behaviour_report("time-to-first-review", hours(), {"included": 2, "no-review-events": 1}),
        {},
        teams(),
    )

    assert "Metric: time-to-first-review" in rendered
    assert "  time-to-first-review     -       2  hours    12.5  30.25   48" in rendered
    assert "  included          2" in rendered
    assert "  no-review-events  1" in rendered


def test_a_metric_collection_renders_every_reported_repository() -> None:
    """Render one drill-down per repository, and name those with no evidence."""
    unavailable = (EvidenceUnavailable(repository="other-service", detail="cached evidence does not cover 2026-05-01"),)
    rendered = render_report(
        BehaviourEvidenceCollection(
            organization="hmcts",
            metric="approval-coverage",
            repositories=(behaviour_report("approval-coverage", rate(1, 2)),),
            unavailable=unavailable,
        ),
        {},
        teams(),
    )

    assert "hmcts/cath-service" in rendered
    assert "Metric: approval-coverage" in rendered
    assert "  other-service  cached evidence does not cover 2026-05-01" in rendered


def test_the_practice_report_is_rendered_when_no_metric_was_requested() -> None:
    """Dispatch on the report shape the requested evidence mode produced."""
    rendered = render_report(practice_report(), {"cath-service": drill_down()}, teams())

    assert "Readiness: AMBER" in rendered


def test_security_alerts_are_tabulated_by_family_and_severity(report: str) -> None:
    """Show one row per family with its open total and the severities GitHub asserted."""
    assert (
        "  family           open  critical  high  medium  low\n  dependabot          5         1     2       0    1\n"
    ) in report


def test_a_refused_alert_family_renders_absent_rather_than_zero(report: str) -> None:
    """Mark every cell of an unreadable family absent, and give its reason its own line.

    A zero here would report a repository nobody was allowed to look at as one with nothing open,
    and a long refusal inside the table would stretch the numeric columns out of alignment.
    """
    assert "  code-scanning       -         -     -       -    -\n" in report
    assert "  code-scanning not available: GitHub permission denied" in report


def test_secret_scanning_zeros_are_explained_rather_than_read_as_a_grading(report: str) -> None:
    """Say why the secret-scanning severity columns are always zero, so they are not read as a finding."""
    assert "  secret-scanning     2         0     0       0    0\n" in report
    assert "secret-scanning alerts carry no severity, so their severity columns are always zero" in report


def test_uncollected_security_alerts_say_so_rather_than_vanishing() -> None:
    """State that alerts were never collected, because an absent block would read as none open."""
    evidence = practice_evidence(
        security=SecurityAlertReport(detail="no repository state has been collected; run metrics collect"),
    )
    rendered = render_practice_report(practice_report(evidence), {"cath-service": drill_down()}, teams())

    assert "\nSecurity alerts\n---------------\n" in rendered
    assert "  not available: no repository state has been collected; run metrics collect" in rendered


def block_lines(report: str, title: str) -> list[str]:
    """Return one section's lines, its column titles included and its heading excluded."""
    lines = report.splitlines()
    body = lines[lines.index(title) + 2 :]
    return body[: body.index("")] if "" in body else body


def test_codeowners_lists_each_found_location_with_its_size_and_recognition(report: str) -> None:
    """Show every location a file was found at, keeping an ignored `.md` variant apart from one GitHub reads.

    The byte size keeps an empty file visible as found-but-empty rather than silently passing.
    """
    block = block_lines(report, "CODEOWNERS, read 2026-08-02T09:30Z")
    assert block[0].split() == ["Location", "Bytes", "Recognised", "by", "GitHub"]
    assert block[1].split() == [".github/CODEOWNERS", "120", "yes"]
    assert block[2].split() == ["docs/CODEOWNERS.md", "0", "no"]


def test_a_repository_without_codeowners_reports_absent_rather_than_an_empty_table() -> None:
    """Say "absent" when every location was checked and none held a file — an observation, not a failure."""
    evidence = practice_evidence(
        codeowners=CodeownersReport(
            fetched_at=datetime(2026, 8, 2, 9, 30, tzinfo=UTC),
            codeowners=CodeownersEvidence(files=()),
        ),
    )
    rendered = render_practice_report(practice_report(evidence), {"cath-service": drill_down()}, teams())

    assert "  absent: no CODEOWNERS file at any of the checked locations" in rendered


def test_uncollected_codeowners_state_says_so_rather_than_vanishing() -> None:
    """State that CODEOWNERS presence was never collected, because an absent block would read as an absent file."""
    evidence = practice_evidence(
        codeowners=CodeownersReport(detail="no repository state has been collected; run metrics collect"),
    )
    rendered = render_practice_report(practice_report(evidence), {"cath-service": drill_down()}, teams())

    assert "\nCODEOWNERS\n----------\n" in rendered
    assert "  not available: no repository state has been collected; run metrics collect" in rendered


def test_maintenance_reports_its_instants_and_the_three_window_answers(report: str) -> None:
    """Show the stored instants above one row per window, each answering any-commit and human-commit."""
    block = block_lines(report, "Maintenance, read 2026-08-02T09:30Z")
    assert block[0].split(maxsplit=1) == ["Branch", "master"]
    assert block[1].split(maxsplit=2) == ["Last", "commit", "2026-07-30T08:00Z"]
    assert block[2].split(maxsplit=3) == ["Last", "human", "commit", "2026-01-10T00:00Z"]
    assert block[3].split() == ["Window", "Any", "commit", "Human", "commit"]
    assert block[4].split() == ["6", "months", "yes", "no"]
    assert block[5].split() == ["12", "months", "yes", "yes"]
    assert block[6].split() == ["24", "months", "yes", "yes"]


def test_an_undecided_human_answer_renders_unknown_with_its_reason() -> None:
    """Spell "unknown" out with its reason where the bounded search stopped short of a window's cutoff.

    The search bound is printed beside the instants, because it is what separates "no human commit
    within the window" from "unknown beyond the commits examined" — and neither reads as False.
    """
    detail = "the bounded search examined commits no older than 2026-01-15T00:00Z"
    evidence = practice_evidence(
        maintenance=MaintenanceReport(
            fetched_at=datetime(2026, 8, 2, 9, 30, tzinfo=UTC),
            maintenance=MaintenanceEvidence(
                branch="master",
                last_commit_at=datetime(2026, 7, 30, 8, 0, tzinfo=UTC),
                last_human_commit_at=None,
                searched_back_to=datetime(2026, 1, 15, tzinfo=UTC),
            ),
            windows=(
                MaintenanceWindowStatus(months=6, committed_within=True, human_committed_within=False),
                MaintenanceWindowStatus(months=12, committed_within=True, human_detail=detail),
                MaintenanceWindowStatus(months=24, committed_within=True, human_detail=detail),
            ),
        ),
    )
    rendered = render_practice_report(practice_report(evidence), {"cath-service": drill_down()}, teams())

    block = block_lines(rendered, "Maintenance, read 2026-08-02T09:30Z")
    assert block[2].split(maxsplit=3) == ["Last", "human", "commit", "none found"]
    assert block[3].split(maxsplit=3) == ["Searched", "back", "to", "2026-01-15T00:00Z"]
    assert block[5].split() == ["6", "months", "yes", "no"]
    assert block[6].split() == ["12", "months", "yes", "unknown"]
    assert f"  12 months human answer unknown: {detail}" in rendered
    assert f"  24 months human answer unknown: {detail}" in rendered


def test_an_empty_default_branch_reports_no_commits_rather_than_a_zero() -> None:
    """Say in words that the branch holds no commits, instead of rendering an absent instant as anything else."""
    evidence = practice_evidence(
        maintenance=MaintenanceReport(
            fetched_at=datetime(2026, 8, 2, 9, 30, tzinfo=UTC),
            maintenance=MaintenanceEvidence(
                branch="master",
                last_commit_at=None,
                last_human_commit_at=None,
                searched_back_to=None,
            ),
            windows=(
                MaintenanceWindowStatus(months=6, committed_within=False, human_committed_within=False),
                MaintenanceWindowStatus(months=12, committed_within=False, human_committed_within=False),
                MaintenanceWindowStatus(months=24, committed_within=False, human_committed_within=False),
            ),
        ),
    )
    rendered = render_practice_report(practice_report(evidence), {"cath-service": drill_down()}, teams())

    assert "none: the branch has no commits" in rendered
    block = block_lines(rendered, "Maintenance, read 2026-08-02T09:30Z")
    assert block[4].split() == ["6", "months", "no", "no"]


def test_uncollected_maintenance_state_says_so_rather_than_vanishing() -> None:
    """State that maintenance was never collected, because an absent block would read as an undatable repository."""
    evidence = practice_evidence(
        maintenance=MaintenanceReport(detail="no repository state has been collected; run metrics collect"),
    )
    rendered = render_practice_report(practice_report(evidence), {"cath-service": drill_down()}, teams())

    assert "\nMaintenance\n-----------\n" in rendered
    assert "  not available: no repository state has been collected; run metrics collect" in rendered


def sonar_rendering(report: SonarReport) -> str:
    """Render the practice report of a repository carrying one SonarCloud report."""
    evidence = practice_evidence(sonar=report)
    return render_practice_report(practice_report(evidence), {"cath-service": drill_down()}, teams())


def test_sonar_reports_the_project_it_resolved_its_gate_and_every_measure() -> None:
    """Head the block with the project and the rung that answered, then the gate and its measures.

    The resolution method is printed because a wrong mapping is otherwise mysterious: a configured
    override and a stored map's inference from one commit are not the same strength of answer.
    """
    rendered = sonar_rendering(mapped_sonar_report())

    block = block_lines(rendered, "SonarCloud, read 2026-08-02T09:30Z")
    assert block[0].split(maxsplit=1) == ["Project", "rpx-xui-webapp_2"]
    assert block[1].split(maxsplit=2) == ["Resolved", "by", "stored_map"]
    assert block[2].split(maxsplit=1) == ["Analysed", "2026-08-01T04:12Z"]
    assert block[3].split(maxsplit=2) == ["Quality", "gate", "OK"]
    assert block[4].split() == ["Metric", "Comparator", "Threshold", "Actual", "Level"]
    assert block[5].split() == ["new_coverage", "LT", "80", "91.4", "OK"]
    assert block[6].split(maxsplit=1) == ["Coverage", "74.2%"]
    assert block[7].split(maxsplit=2) == ["Duplicated", "lines", "1.5%"]
    assert block[8].split(maxsplit=3) == ["Lines", "of", "code", "1234567"]
    assert block[9].split(maxsplit=1) == ["Violations", "318"]
    assert block[10].split(maxsplit=2) == ["Reliability", "issues", "12"]
    assert block[11].split(maxsplit=2) == ["Maintainability", "issues", "280"]
    assert block[12].split(maxsplit=2) == ["Security", "issues", "3"]
    assert block[13].split(maxsplit=2) == ["Security", "hotspots", "7"]


def test_the_four_ratings_render_as_the_letters_they_name() -> None:
    """Render each 1-to-5 rating as the `A`-to-`E` letter every SonarCloud reader knows it by."""
    rendered = sonar_rendering(mapped_sonar_report())

    block = block_lines(rendered, "SonarCloud, read 2026-08-02T09:30Z")
    assert block[14].split(maxsplit=2) == ["Reliability", "rating", "C"]
    assert block[15].split(maxsplit=2) == ["Maintainability", "rating", "A"]
    assert block[16].split(maxsplit=2) == ["Security", "rating", "B"]
    assert block[17].split(maxsplit=3) == ["Security", "review", "rating", "E"]


def test_a_rating_off_the_scale_is_not_reported_as_a_letter() -> None:
    """Print a rating this build does not understand as the number it was sent, marked unknown.

    Defaulting to `A` would report an unrecognised rating as the best there is, and defaulting to `E`
    as the worst; neither is what SonarCloud said.
    """
    measures = sonar_measures(reliability_rating=SonarRating(value=7.0))
    rendered = sonar_rendering(mapped_sonar_report(measures=measures))

    block = block_lines(rendered, "SonarCloud, read 2026-08-02T09:30Z")
    assert block[14].split(maxsplit=2) == ["Reliability", "rating", "unknown (7)"]


def test_a_failing_gate_condition_is_shown_beside_the_threshold_it_missed() -> None:
    """Print every condition behind a failure, so the verdict can be argued with rather than obeyed.

    Nothing here is graded — the failure imposes no readiness label — so the conditions are the whole
    of what the block offers a reader deciding whether the gate is measuring anything they care about.
    """
    gate = SonarQualityGate(
        level=SonarGateLevel.ERROR,
        conditions=(
            SonarQualityGateCondition(
                metric="new_coverage",
                comparator="LT",
                threshold="80",
                actual="62.1",
                level=SonarGateLevel.ERROR,
            ),
            SonarQualityGateCondition(
                metric="new_duplicated_lines_density",
                comparator="GT",
                threshold="3",
                actual="0.4",
                level=SonarGateLevel.OK,
            ),
            SonarQualityGateCondition(
                metric="new_security_rating",
                comparator="GT",
                threshold="1",
                level=SonarGateLevel.ERROR,
            ),
        ),
    )
    rendered = sonar_rendering(mapped_sonar_report(measures=sonar_measures(gate=gate)))

    block = block_lines(rendered, "SonarCloud, read 2026-08-02T09:30Z")
    assert block[3].split(maxsplit=2) == ["Quality", "gate", "ERROR"]
    assert block[5].split() == ["new_coverage", "LT", "80", "62.1", "ERROR"]
    assert block[6].split() == ["new_duplicated_lines_density", "GT", "3", "0.4", "OK"]
    assert block[7].split() == ["new_security_rating", "GT", "1", "-", "ERROR"]


def test_a_never_analysed_project_says_so_rather_than_printing_an_instant() -> None:
    """Report a listed but never-analysed project as one, gate level and all — 14 of 289 are.

    The project exists and nothing has been measured against it, which is a third answer and not a
    failure, so the gate's own `NONE` level is printed with a line saying nothing sits behind it.
    """
    measures = sonar_measures(
        analysis_at=None,
        gate=SonarQualityGate(level=SonarGateLevel.NONE),
    )
    rendered = sonar_rendering(mapped_sonar_report(measures=measures))

    block = block_lines(rendered, "SonarCloud, read 2026-08-02T09:30Z")
    assert block[2].split(maxsplit=1) == ["Analysed", "never"]
    assert block[3].split(maxsplit=2) == ["Quality", "gate", "NONE"]
    assert block[4] == "  no gate conditions were reported for this project"


def test_every_absent_measure_renders_as_a_dash_rather_than_a_zero() -> None:
    """Leave a measure SonarCloud returned no value for visibly absent, in every row of the block.

    A rendered `0.0%` coverage would be a claim about the code where the measurement is the thing
    that is missing, and every row is still printed so the block says what was asked for.
    """
    rendered = sonar_rendering(mapped_sonar_report(measures=SonarMeasures(project_key="rpx-xui-webapp_2")))

    block = block_lines(rendered, "SonarCloud, read 2026-08-02T09:30Z")
    assert block[3].split(maxsplit=2) == ["Quality", "gate", "not reported"]
    assert [line.split()[-1] for line in block[4:]] == ["-"] * len(block[4:])
    assert len(block[4:]) == 12


@pytest.mark.parametrize(
    ("fetched_at", "detail"),
    [
        (None, "the observations database at metrics.sqlite3 could not be read"),
        (None, "no repository state has been collected; run metrics collect"),
        (
            datetime(2026, 8, 2, 9, 30, tzinfo=UTC),
            "SonarCloud measures were not collected when repository state was stored; run metrics collect",
        ),
        (datetime(2026, 8, 2, 9, 30, tzinfo=UTC), "no SonarCloud project is mapped to this repository"),
    ],
)
def test_each_reason_for_having_no_sonar_evidence_is_stated_rather_than_vanishing(
    fetched_at: datetime | None,
    detail: str,
) -> None:
    """Keep the block and print its reason, because an absent one would read as a gate nobody checked.

    Most of the organisation's repositories have no SonarCloud project at all, and that answer has to
    be distinguishable from a report that never looked.
    """
    rendered = sonar_rendering(SonarReport(fetched_at=fetched_at, detail=detail))

    read = "" if fetched_at is None else ", read 2026-08-02T09:30Z"
    title = f"SonarCloud{read}"
    assert f"\n{title}\n{'-' * len(title)}\n" in rendered
    assert f"  not available: {detail}" in rendered


def test_a_resolved_project_whose_measures_failed_is_still_named_above_the_reason() -> None:
    """Name the project a refusal was about, which is the first thing needed to chase it."""
    rendered = sonar_rendering(
        SonarReport(
            fetched_at=datetime(2026, 8, 2, 9, 30, tzinfo=UTC),
            mapping=sonar_mapping(method=SonarResolution.CONFIGURED),
            detail="SonarCloud refused the measures request: HTTP 503",
        ),
    )

    block = block_lines(rendered, "SonarCloud, read 2026-08-02T09:30Z")
    assert block[0].split(maxsplit=1) == ["Project", "rpx-xui-webapp_2"]
    assert block[1].split(maxsplit=2) == ["Resolved", "by", "configured"]
    assert block[2] == "  not available: SonarCloud refused the measures request: HTTP 503"


def test_the_sonar_block_sits_with_the_other_stored_current_state() -> None:
    """Keep the stored current-state blocks together, between the security alerts and CODEOWNERS."""
    rendered = sonar_rendering(mapped_sonar_report())

    assert rendered.index("\nSecurity alerts") < rendered.index("\nSonarCloud") < rendered.index("\nCODEOWNERS")


def population_teams() -> dict[str, str]:
    """Own four repositories across two teams, so ordering within a team is visible too."""
    return {
        "cath-service": "crime",
        "opal-common-lib": "opal",
        "opal-logging-service": "opal",
        "refused-service": "opal",
    }


def population_drill_downs() -> dict[str, RepositoryDrillDown]:
    """Give every reported repository in the population the same drill-down."""
    return {name: drill_down() for name in population_teams()}


def labelled(label: ReadinessLabel) -> ReadinessAssessment:
    """Build an assessment carrying one label and no conditions, for counting a population by label."""
    return ReadinessAssessment(label=label, blocking=(), caution=(), clear=())


def population_report(
    repositories: tuple[RepositoryPracticeEvidence, ...],
    unavailable: tuple[EvidenceUnavailable, ...] = (),
) -> str:
    """Render a practice report covering several repositories at once."""
    return render_practice_report(
        PracticeEvidenceReport(
            organization="hmcts",
            repositories=repositories,
            unavailable=unavailable,
            actors=(),
        ),
        population_drill_downs(),
        population_teams(),
    )


def test_the_report_opens_with_an_index_of_the_repositories_it_covers(report: str) -> None:
    """Answer which repositories are assessable, which are RED and which need reading, up front."""
    assert report.startswith("Repository Summary\n------------------\n")
    assert "\nIndex\n-----\n" in report
    assert index_lines(report) == [
        "  Team     Repository  Readiness  Gate observed",
        "  crime  cath-service      AMBER            yes",
    ]


def summary_lines(rendered: str) -> list[str]:
    """Return the summary block's lines, its heading excluded."""
    lines = rendered.splitlines()
    body = lines[lines.index("Repository Summary") + 2 :]
    return body[: body.index("")]


def test_the_summary_counts_the_repositories_carrying_each_readiness_label() -> None:
    """Give the population's shape before the index, which at hmcts scale is thousands of rows long."""
    rendered = population_report(
        (
            practice_evidence(repository="cath-service", assessment=labelled(ReadinessLabel.RED)),
            practice_evidence(repository="opal-common-lib", assessment=labelled(ReadinessLabel.RED)),
            practice_evidence(repository="opal-logging-service", assessment=labelled(ReadinessLabel.GREEN)),
        ),
    )

    assert summary_lines(rendered) == [
        "  GREEN          1  33.3%",
        "  AMBER          0     0%",
        "  RED            2  66.7%",
        "  CANNOT_ASSESS  0     0%",
    ]


def test_the_summary_prints_every_label_including_the_ones_nobody_carries() -> None:
    """Keep the row set fixed, so two runs diff line for line and an empty label is an observation."""
    rendered = population_report((practice_evidence(assessment=labelled(ReadinessLabel.AMBER)),))

    assert [line.split()[0] for line in summary_lines(rendered)] == ["GREEN", "AMBER", "RED", "CANNOT_ASSESS"]


def test_the_summary_counts_the_repositories_the_index_could_not_label() -> None:
    """Reconcile with the index's own column: an unassessed and an unavailable row are counted too."""
    rendered = population_report(
        (
            practice_evidence(repository="cath-service", assessment=None),
            practice_evidence(repository="opal-common-lib", assessment=labelled(ReadinessLabel.CANNOT_ASSESS)),
        ),
        (EvidenceUnavailable(repository="refused-service", detail="GitHub rate limit exceeded"),),
    )

    assert summary_lines(rendered)[3:] == [
        "  CANNOT_ASSESS  1  33.3%",
        "  not assessed   1  33.3%",
        "  unavailable    1  33.3%",
    ]


def test_the_summary_leaves_out_the_non_labels_nothing_carries() -> None:
    """Print no `unavailable 0`: a repository is either assessed or not, and a zero there is nothing."""
    rendered = population_report((practice_evidence(),))

    assert [line.split()[0] for line in summary_lines(rendered)] == ["GREEN", "AMBER", "RED", "CANNOT_ASSESS"]


def test_the_summary_counts_each_label_separately_and_combines_none_of_them() -> None:
    """Hold one row per label: a total, a score or a per-team figure is still the forbidden roll-up.

    The no-count ruling was reversed for this block on 2026-08-31 — see architecture.md, "Scope
    boundaries" — and what the reversal admitted is a distribution and nothing else.
    """
    rendered = population_report(
        (
            practice_evidence(repository="cath-service", assessment=labelled(ReadinessLabel.RED)),
            practice_evidence(repository="opal-common-lib", assessment=labelled(ReadinessLabel.GREEN)),
        ),
    )

    assert [line.split() for line in summary_lines(rendered)] == [
        ["GREEN", "1", "50%"],
        ["AMBER", "0", "0%"],
        ["RED", "1", "50%"],
        ["CANNOT_ASSESS", "0", "0%"],
    ]


def test_the_summary_prints_a_dash_where_it_covers_no_repositories_at_all() -> None:
    """Divide nothing by nothing: a report covering no population has no share to print, only a dash."""
    rendered = population_report(())

    assert [line.split() for line in summary_lines(rendered)] == [
        ["GREEN", "0", "-"],
        ["AMBER", "0", "-"],
        ["RED", "0", "-"],
        ["CANNOT_ASSESS", "0", "-"],
    ]


def test_the_index_names_a_repository_whose_gate_was_never_collected() -> None:
    """Mark an unread gate as unobserved, which is the repository that needs a permission."""
    evidence = practice_evidence(
        assessment=ReadinessAssessment(label=ReadinessLabel.CANNOT_ASSESS, blocking=(), caution=(), clear=()),
        merge_gate=MergeGateReport(detail="no repository state has been collected; run metrics collect"),
    )
    rendered = render_practice_report(practice_report(evidence), {"cath-service": drill_down()}, teams())

    assert "  crime  cath-service  CANNOT_ASSESS             no" in rendered


def test_the_index_marks_a_protected_branch_whose_rules_were_withheld_as_unobserved() -> None:
    """Separate a gate that exists from one that could be read: only the second can be argued with."""
    withheld = MergeGateReport(
        fetched_at=datetime(2026, 8, 2, 9, 30, tzinfo=UTC),
        gate=MergeGateEvidence(
            branch="master",
            protected=True,
            pull_requests=(),
            status_checks=(),
            restricts_deletions=False,
            blocks_force_pushes=False,
            rules_observed=False,
        ),
    )
    rendered = render_practice_report(
        practice_report(practice_evidence(merge_gate=withheld)),
        {"cath-service": drill_down()},
        teams(),
    )

    assert "  crime  cath-service      AMBER             no" in rendered


def test_the_index_says_when_a_repository_was_not_assessed_at_all() -> None:
    """Never leave a disabled assessment looking like a label, and never invent one for it."""
    rendered = render_practice_report(
        practice_report(practice_evidence(assessment=None)),
        {"cath-service": drill_down()},
        teams(),
    )

    assert "  crime  cath-service  not assessed            yes" in rendered


def test_the_index_lists_a_repository_whose_collection_failed() -> None:
    """Name a repository that reported nothing, so the index covers the population and not a subset."""
    unavailable = (EvidenceUnavailable(repository="other-service", detail="GitHub rate limit exceeded"),)
    rendered = render_practice_report(
        practice_report(unavailable=unavailable),
        {"cath-service": drill_down()},
        teams(),
    )

    assert index_lines(rendered) == [
        "  Team      Repository    Readiness  Gate observed",
        "  crime   cath-service        AMBER            yes",
        "  civil  other-service  unavailable              -",
    ]


def test_the_index_follows_the_order_the_report_renders_the_repositories_in() -> None:
    """Keep the table of contents and the document it points at in one order, across and within teams."""
    rendered = population_report(
        (
            practice_evidence(repository="cath-service"),
            practice_evidence(repository="opal-common-lib"),
            practice_evidence(repository="opal-logging-service"),
        ),
        (EvidenceUnavailable(repository="refused-service", detail="GitHub rate limit exceeded"),),
    )

    assert [line.split()[1] for line in index_lines(rendered)[1:]] == [
        "cath-service",
        "opal-common-lib",
        "opal-logging-service",
        "refused-service",
    ]
    positions = [rendered.index(f"hmcts/{name}") for name in ("opal-common-lib", "opal-logging-service")]
    assert positions == sorted(positions)


def test_the_index_combines_nothing_and_counts_nothing() -> None:
    """Hold exactly one row per repository: reducing fourteen labels to one is forbidden outright.

    Guards the boundary architecture.md draws — listing labels is presentation, combining them is a
    team roll-up. A total, a score or a per-team verdict would all show up here as an extra row.
    """
    rendered = population_report(
        (
            practice_evidence(repository="cath-service"),
            practice_evidence(repository="opal-common-lib"),
            practice_evidence(repository="opal-logging-service"),
        ),
    )

    rows = index_lines(rendered)[1:]
    # Asserted as the exact row set rather than by probing for digits: a per-team verdict row could
    # carry no digit at all and slip through, while a repository named with one would fail a guard
    # that only looked for digits. Every row here names one repository and nothing else does.
    assert [line.split() for line in rows] == [
        ["crime", "cath-service", "AMBER", "yes"],
        ["opal", "opal-common-lib", "AMBER", "yes"],
        ["opal", "opal-logging-service", "AMBER", "yes"],
    ]


def actor_repository(
    repository: str,
    contributions: int,
    readiness: ReadinessLabel | None = ReadinessLabel.RED,
) -> ActorRepositoryReadiness:
    """Build one line of an actor's readiness list, defaulting the label the section leads with."""
    return ActorRepositoryReadiness(
        readiness=readiness,
        repository=repository,
        contributions=contributions,
        blocking=0,
        metrics=(),
    )


def actor(login: str, *repositories: ActorRepositoryReadiness) -> ActorReadiness:
    """Build one person's readiness list, in the contribution order the aggregation emits it in."""
    return ActorReadiness(actor_login=login, repositories=repositories)


ACTORS_HEADING = "Actors (cannot_assess repositories excluded)"


def actor_lines(rendered: str) -> list[str]:
    """Return the actor block's lines, its heading excluded.

    Read to the end of the document rather than to the next blank line: the block is rendered last,
    so a line escaping past it would otherwise go unread by every assertion here.
    """
    lines = rendered.splitlines()
    return lines[lines.index(ACTORS_HEADING) + 2 :]


def actors_report(*actors: ActorReadiness) -> str:
    """Render the practice report carrying the given actor section."""
    return render_practice_report(practice_report(actors=actors), {"cath-service": drill_down()}, teams())


def test_an_actor_whose_repositories_share_one_label_is_not_told_which_they_are() -> None:
    """Abridge a uniform actor to the tally: six names would draw no distinction between them."""
    rendered = actors_report(
        actor("bob", *(actor_repository(f"opal-service-{index}", 10 - index) for index in range(6))),
    )

    assert actor_lines(rendered) == ["  Blocked", "    bob  RED x 6"]


def test_one_label_is_tallied_once_however_the_repositories_carrying_it_are_spread() -> None:
    """Group a label wherever it sits in the list, so no line reports the same label twice."""
    rendered = actors_report(
        actor(
            "alice",
            actor_repository("project-x", 41),
            actor_repository("project-z", 30, ReadinessLabel.GREEN),
            actor_repository("project-u", 20),
        ),
    )

    assert actor_lines(rendered) == ["  Review", "    alice  RED x 2 (project-x, project-u), GREEN (project-z)"]


def test_groups_are_ordered_by_the_contributions_behind_them_not_by_first_appearance() -> None:
    """Lead with the label a person does most of their work under, wherever it first appears."""
    rendered = actors_report(
        actor(
            "alice",
            actor_repository("project-a", 10, ReadinessLabel.GREEN),
            actor_repository("project-b", 6),
            actor_repository("project-c", 5),
        ),
    )

    assert actor_lines(rendered) == ["  Review", "    alice  RED x 2 (project-b, project-c), GREEN (project-a)"]


def test_two_groups_of_equal_weight_are_ordered_worst_label_first() -> None:
    """Break a tie by the policy's own severity, so equal weight can never bury a red behind a green."""
    rendered = actors_report(
        actor(
            "alice",
            actor_repository("project-a", 5, ReadinessLabel.GREEN),
            actor_repository("project-b", 5, ReadinessLabel.AMBER),
            actor_repository("project-c", 5),
        ),
    )

    assert actor_lines(rendered) == ["  Review", "    alice  RED (project-c), AMBER (project-b), GREEN (project-a)"]


def test_a_repository_that_could_not_be_assessed_is_left_out_of_a_persons_line() -> None:
    """Drop a label that reports somebody else's missing permission, not the work of the person named."""
    rendered = actors_report(
        actor(
            "alice",
            actor_repository("project-a", 40, ReadinessLabel.CANNOT_ASSESS),
            actor_repository("project-b", 5, ReadinessLabel.AMBER),
        ),
    )

    assert actor_lines(rendered) == ["  Review", "    alice  AMBER"]


def test_a_person_working_only_in_unassessable_repositories_gets_no_line() -> None:
    """Leave out a name with no label beside it, which would read as a finding about that person."""
    rendered = actors_report(
        actor("alice", actor_repository("project-a", 40, ReadinessLabel.CANNOT_ASSESS)),
        actor("bob", actor_repository("project-b", 5, ReadinessLabel.RED)),
    )

    assert actor_lines(rendered) == ["  Blocked", "    bob  RED"]


def test_a_section_left_empty_by_the_exclusion_says_that_rather_than_that_nobody_merged() -> None:
    """Separate a report nobody contributed to from one whose contributors were all excluded here."""
    rendered = actors_report(actor("alice", actor_repository("project-a", 40, ReadinessLabel.CANNOT_ASSESS)))

    assert actor_lines(rendered) == [
        "  none: every repository the reported people contributed to could not be assessed",
    ]


def test_the_actor_heading_says_the_unassessable_repositories_are_left_out(report: str) -> None:
    """Name the exclusion in the report, so a shortened list is not read as the whole of somebody's work."""
    assert f"\n{ACTORS_HEADING}\n" in report


def test_an_actor_in_one_repository_renders_the_bare_label() -> None:
    """Print neither a count of one nor a name that distinguishes nothing."""
    rendered = actors_report(actor("carol", actor_repository("cath-service", 4, ReadinessLabel.AMBER)))

    assert actor_lines(rendered) == ["  Review", "    carol  AMBER"]


def test_a_repository_that_was_never_assessed_is_named_rather_than_left_blank() -> None:
    """Say a repository carries no label, so an unjudged one is never read as a mild one."""
    rendered = actors_report(
        actor(
            "carol",
            actor_repository("cath-service", 9, None),
            actor_repository("other-service", 2, ReadinessLabel.AMBER),
        ),
    )

    assert actor_lines(rendered) == ["  Ungrouped", "    carol  NOT ASSESSED (cath-service), AMBER (other-service)"]


def test_an_unassessed_group_sorts_behind_the_label_it_ties_with() -> None:
    """Rank the absence of a label last on a tie, rather than among the labels as a grade between them."""
    rendered = actors_report(
        actor(
            "carol",
            actor_repository("cath-service", 5, None),
            actor_repository("other-service", 5, ReadinessLabel.GREEN),
        ),
    )

    assert actor_lines(rendered) == ["  Ungrouped", "    carol  GREEN (other-service), NOT ASSESSED (cath-service)"]


def test_actors_are_rendered_one_line_each_in_the_order_the_report_lists_them() -> None:
    """Keep the order within a group the JSON's order, so the two renderings cannot disagree."""
    # Listed against alphabetical order, so the assertion tells order preserved from order re-sorted.
    rendered = actors_report(
        actor("bob", actor_repository("project-x", 6)),
        actor("alice", actor_repository("project-x", 41)),
    )

    assert actor_lines(rendered) == ["  Blocked", "    bob    RED", "    alice  RED"]


def test_the_actor_list_is_grouped_by_the_action_its_labels_put_each_person_under() -> None:
    """Group the printed people as the summary counts them, in the order the summary reports."""
    rendered = actors_report(
        actor("ann", actor_repository("project-a", 8, ReadinessLabel.GREEN)),
        actor("bea", actor_repository("project-b", 7, ReadinessLabel.AMBER)),
        actor("cam", actor_repository("project-c", 6)),
        actor("dee", actor_repository("project-d", 5, None)),
    )

    assert actor_lines(rendered) == [
        "  Enable",
        "    ann  GREEN",
        "  Review",
        "    bea  AMBER",
        "  Blocked",
        "    cam  RED",
        "  Ungrouped",
        "    dee  NOT ASSESSED",
    ]


def test_a_group_nobody_is_in_is_left_out_rather_than_printed_empty() -> None:
    """Leave the zero to the Actor Summary: an empty heading here reads as a list that failed to render."""
    rendered = actors_report(actor("bob", actor_repository("project-b", 5)))

    assert actor_lines(rendered) == ["  Blocked", "    bob  RED"]


def test_the_login_column_is_one_width_across_the_whole_section_not_one_per_group() -> None:
    """Start every person's labels in one column, so two groups can be read down as one list."""
    rendered = actors_report(
        actor("alexandra", actor_repository("project-a", 9)),
        actor("bo", actor_repository("project-b", 8, ReadinessLabel.GREEN)),
    )

    assert actor_lines(rendered) == [
        "  Enable",
        f"    {'bo':<9}  GREEN",
        "  Blocked",
        f"    {'alexandra':<9}  RED",
    ]


def test_a_report_with_no_actors_says_so_rather_than_dropping_the_section() -> None:
    """Distinguish a report nobody contributed to from one whose section was never rendered."""
    rendered = actors_report()

    assert actor_lines(rendered) == ["  none: no person authored a merge in the reported repositories"]


def test_the_same_label_several_times_over_is_one_combination() -> None:
    """Ignore multiplicity: six red repositories say what one says, and counting them apart splits one population."""
    combination = actor_combination(tuple(actor_repository(f"opal-service-{index}", 10 - index) for index in range(6)))

    assert combination == ("RED",)


def test_a_combination_is_ordered_best_label_first_however_the_contributions_fall() -> None:
    """Order by the labels, not by the weight the printed line leads with, so one pair of labels is one key."""
    heavy_red = actor_combination(
        (
            actor_repository("project-x", 41),
            actor_repository("project-z", 30, ReadinessLabel.GREEN),
            actor_repository("project-u", 20),
        ),
    )
    heavy_green = actor_combination(
        (
            actor_repository("project-a", 40, ReadinessLabel.GREEN),
            actor_repository("project-b", 5),
        ),
    )

    assert heavy_red == heavy_green == ("GREEN", "RED")


def test_an_unassessed_repository_comes_last_in_a_combination() -> None:
    """Rank the absence of a label behind every label, rather than among them as a grade between them."""
    combination = actor_combination(
        (
            actor_repository("cath-service", 9, None),
            actor_repository("other-service", 2, ReadinessLabel.AMBER),
        ),
    )

    assert combination == ("AMBER", "NOT ASSESSED")


@pytest.mark.parametrize(
    ("combination", "group"),
    [
        (("GREEN",), "Enable"),
        (("GREEN", "AMBER"), "Enable"),
        (("AMBER",), "Review"),
        (("GREEN", "AMBER", "RED"), "Review"),
        (("GREEN", "RED"), "Review"),
        (("AMBER", "RED"), "Blocked"),
        (("RED",), "Blocked"),
    ],
)
def test_every_graded_combination_is_grouped_as_the_user_instructed(combination: tuple[str, ...], group: str) -> None:
    """Assert each of the seven assignments separately, so a transcription slip names the row it is in."""
    assert actor_group(combination) == group


def test_the_group_table_covers_every_graded_combination_exactly_once() -> None:
    """Guard the docstring's claim that only an unassessed line can fall through to the fallback."""
    grades = ("GREEN", "AMBER", "RED")
    every = {subset for size in (1, 2, 3) for subset in combinations(grades, size)}
    grouped = [combination for rows in ACTOR_GROUPS.values() for combination in rows]

    assert sorted(grouped) == sorted(every)


def test_the_group_table_names_only_labels_the_section_can_print() -> None:
    """Catch a renamed label leaving a transcribed row unreachable, since the rows spell the labels out."""
    named = {name for rows in ACTOR_GROUPS.values() for combination in rows for name in combination}

    assert named <= set(ACTOR_COMBINATION_ORDER)


def test_a_combination_the_table_does_not_cover_is_reported_as_ungrouped() -> None:
    """Say a line carrying no grade is not grouped, rather than guessing an action for it."""
    assert actor_group(("GREEN", "NOT ASSESSED")) == ACTOR_UNGROUPED
    assert actor_group(("NOT ASSESSED",)) == ACTOR_UNGROUPED


def test_the_people_behind_each_combination_are_counted_once_each() -> None:
    """Count people, not repositories, so a person with six red repositories counts as one person."""
    counted = actor_combination_counts(
        (
            actor("alice", actor_repository("project-x", 41), actor_repository("project-y", 3)),
            actor("bob", actor_repository("project-x", 6, ReadinessLabel.AMBER)),
            actor("carol", actor_repository("project-z", 2), actor_repository("project-w", 1, ReadinessLabel.GREEN)),
        ),
    )

    assert counted == {("RED",): 1, ("AMBER",): 1, ("GREEN", "RED"): 1}


def test_a_person_the_actor_list_leaves_out_is_left_out_of_the_counts() -> None:
    """Count over the same repositories the lines are built from, so the counts and the lines cannot disagree."""
    counted = actor_combination_counts(
        (
            actor("alice", actor_repository("project-a", 40, ReadinessLabel.CANNOT_ASSESS)),
            actor("bob", actor_repository("project-b", 5, ReadinessLabel.AMBER)),
        ),
    )

    assert counted == {("AMBER",): 1}


ACTOR_SUMMARY_HEADING = "Actor Summary"


def actor_summary_lines(rendered: str) -> list[str]:
    """Return the actor summary block's lines, its heading excluded."""
    lines = rendered.splitlines()
    body = lines[lines.index(ACTOR_SUMMARY_HEADING) + 2 :]
    return body[: body.index("")]


def test_the_actor_summary_sits_immediately_above_the_section_it_summarises() -> None:
    """Put the counts where the list they describe starts, as the repository counts sit above the index."""
    rendered = whole_report()
    lines = rendered.splitlines()

    # Read from the end of the summary's own rows rather than from its heading, so the assertion is
    # that nothing sits between the two blocks — not merely that one falls somewhere below the other.
    summary_end = lines.index(ACTOR_SUMMARY_HEADING) + 2 + len(actor_summary_lines(rendered))
    assert lines[summary_end : summary_end + 2] == ["", ACTORS_HEADING]


def test_the_actor_summary_counts_the_people_carrying_each_combination_of_labels() -> None:
    """Say how the population divides, which a list of a thousand abridged lines cannot be read for."""
    rendered = actors_report(
        actor("alice", actor_repository("project-x", 41)),
        actor("bob", actor_repository("project-y", 6, ReadinessLabel.AMBER)),
        actor("carol", actor_repository("project-z", 2), actor_repository("project-w", 1, ReadinessLabel.GREEN)),
        actor("dave", actor_repository("project-v", 3, ReadinessLabel.GREEN)),
    )

    assert actor_summary_lines(rendered) == [
        "  Enable               1  25%",
        "    GREEN              1  25%",
        "    GREEN, AMBER       0   0%",
        "  Review               2  50%",
        "    AMBER              1  25%",
        "    GREEN, AMBER, RED  0   0%",
        "    GREEN, RED         1  25%",
        "  Blocked              1  25%",
        "    AMBER, RED         0   0%",
        "    RED                1  25%",
    ]


def test_the_actor_summary_prints_every_combination_including_the_ones_nobody_carries() -> None:
    """Keep the row set fixed, so two runs diff line for line and an empty combination is an observation."""
    rendered = actors_report(actor("alice", actor_repository("project-x", 41)))

    assert [line.rsplit(maxsplit=2)[0].strip() for line in actor_summary_lines(rendered)] == [
        "Enable",
        "GREEN",
        "GREEN, AMBER",
        "Review",
        "AMBER",
        "GREEN, AMBER, RED",
        "GREEN, RED",
        "Blocked",
        "AMBER, RED",
        "RED",
    ]


def test_each_group_subtotals_the_combinations_printed_under_it() -> None:
    """Give each named action its own figure, per the user's instruction of 2026-09-01."""
    rendered = actors_report(
        actor("alice", actor_repository("project-x", 41), actor_repository("project-w", 2, ReadinessLabel.AMBER)),
        actor("bob", actor_repository("project-y", 6, ReadinessLabel.AMBER)),
        actor("carol", actor_repository("project-z", 2, ReadinessLabel.GREEN), actor_repository("project-u", 1)),
        actor("dave", actor_repository("project-t", 3, ReadinessLabel.GREEN)),
    )
    counted = {line.rsplit(maxsplit=2)[0].strip(): line.split()[-2] for line in actor_summary_lines(rendered)}

    assert counted["Enable"] == "1"
    assert counted["Review"] == "2"
    assert counted["Blocked"] == "1"
    assert sum(int(counted[name]) for name in ("Enable", "Review", "Blocked")) == 4


def test_a_combination_the_group_table_does_not_cover_is_printed_under_ungrouped() -> None:
    """Report a line carrying no grade where it occurs, rather than dropping the people behind it."""
    rendered = actors_report(
        actor("alice", actor_repository("project-x", 41)),
        actor("bob", actor_repository("project-y", 6, None)),
        actor("carol", actor_repository("project-z", 2, None), actor_repository("project-w", 1, ReadinessLabel.GREEN)),
    )

    assert actor_summary_lines(rendered)[-3:] == [
        "  Ungrouped              2  66.7%",
        "    GREEN, NOT ASSESSED  1  33.3%",
        "    NOT ASSESSED         1  33.3%",
    ]


def test_the_ungrouped_group_is_left_out_where_nobody_reaches_it() -> None:
    """Print no fourth action where the user named three: an empty heading would offer one."""
    rendered = actors_report(actor("alice", actor_repository("project-x", 41)))

    assert ACTOR_UNGROUPED not in rendered


def test_the_actor_summary_prints_a_dash_where_the_section_reports_nobody() -> None:
    """Divide nothing by nothing: a report nobody contributed to has no population to take a share of."""
    rendered = actors_report()

    assert actor_summary_lines(rendered) == [
        "  Enable               0  -",
        "    GREEN              0  -",
        "    GREEN, AMBER       0  -",
        "  Review               0  -",
        "    AMBER              0  -",
        "    GREEN, AMBER, RED  0  -",
        "    GREEN, RED         0  -",
        "  Blocked              0  -",
        "    AMBER, RED         0  -",
        "    RED                0  -",
    ]


def test_the_actor_summary_counts_nobody_where_every_person_is_excluded_from_the_section() -> None:
    """Count over the people the section reports, so a report whose contributors were all excluded is empty."""
    rendered = actors_report(actor("alice", actor_repository("project-a", 40, ReadinessLabel.CANNOT_ASSESS)))

    assert {line.split()[-1] for line in actor_summary_lines(rendered)} == {"-"}
    assert {line.split()[-2] for line in actor_summary_lines(rendered)} == {"0"}


def test_each_group_subtotal_counts_the_people_printed_under_that_group_in_the_list() -> None:
    """Read both blocks out of one rendering, so the count and the list it counts cannot disagree."""
    rendered = actors_report(
        actor("ann", actor_repository("project-a", 8, ReadinessLabel.GREEN)),
        actor("bea", actor_repository("project-b", 7, ReadinessLabel.AMBER)),
        actor("cam", actor_repository("project-c", 6)),
        actor("dee", actor_repository("project-d", 5, ReadinessLabel.AMBER)),
        actor("eve", actor_repository("project-e", 4, None)),
    )
    subtotals = {
        line.rsplit(maxsplit=2)[0].strip(): int(line.rsplit(maxsplit=2)[1])
        for line in actor_summary_lines(rendered)
        if not line.startswith(f"  {ACTOR_ROW_INDENT}")
    }
    listed: dict[str, int] = {}
    group = ""
    for line in actor_lines(rendered):
        if line.startswith(f"  {ACTOR_ROW_INDENT}"):
            listed[group] += 1
        else:
            group = line.strip()
            listed[group] = 0

    assert listed == {"Enable": 1, "Review": 2, "Blocked": 1, "Ungrouped": 1}
    assert {name: count for name, count in subtotals.items() if count} == listed


def test_a_count_too_small_to_round_to_a_tenth_is_not_printed_as_a_measured_zero() -> None:
    """Separate a row somebody is in from the zero rows beside it, as `percent` separates absent from zero."""
    assert percentage_of(1, 3000) == "<0.1%"
    assert percentage_of(0, 3000) == "0%"


def whole_report() -> str:
    """Render a practice report carrying every block, so the blocks can be read in the order they fall."""
    return render_practice_report(
        practice_report(
            unavailable=(EvidenceUnavailable(repository="other-service", detail="GitHub rate limit exceeded"),),
            actors=(actor("alice", actor_repository("project-x", 41)),),
        ),
        {"cath-service": drill_down()},
        teams(),
    )


def test_the_report_renders_its_blocks_in_the_order_a_reader_works_down_them() -> None:
    """Put each set of counts above the list it describes, and the repositories between the two."""
    lines = whole_report().splitlines()
    positions = [
        lines.index(title)
        for title in ("Repository Summary", "Index", "Unavailable", ACTOR_SUMMARY_HEADING, ACTORS_HEADING)
    ]

    assert positions == sorted(positions)
    assert lines.index("Index") < lines.index("hmcts/cath-service") < lines.index("Unavailable")


def test_every_count_in_the_two_summary_blocks_is_given_a_share_of_its_population() -> None:
    """Report each figure against what it was counted over, so no row is read as a share of the report."""
    rendered = whole_report()
    counted = [line.rsplit(maxsplit=2) for line in (*summary_lines(rendered), *actor_summary_lines(rendered))]

    assert counted
    assert all(count.isdigit() and share.endswith("%") for _, count, share in counted)


REVIEW_COVERAGE = "independent-review-coverage"
CYCLE_TIME = "merge-cycle-time"
SUPPRESSED = "1 merges into the default branch, below the minimum of 2, so metric values are suppressed"
UNCOVERED = "cached pull_request evidence does not cover 2026-08-01T00:00Z"


def series_window(index: int) -> tuple[datetime, datetime]:
    """Return the half-open seven-day window at the given offset from the baseline's start."""
    starts_at = datetime(2026, 7, 11, tzinfo=UTC) + index * timedelta(days=7)
    return starts_at, starts_at + timedelta(days=7)


def cycle(median: float) -> DistributionObservation:
    """Build an observed distribution of merge cycle times in hours."""
    return DistributionObservation(
        status=ObservationStatus.OBSERVED,
        sample_size=2,
        unit="hours",
        median=median,
        percentile_75=median * 2,
        percentile_90=median * 3,
    )


def series_metrics(numerator: int, denominator: int, median: float) -> tuple[TrendMetric, ...]:
    """Build one window's metric values: a rate and a distribution read at its fixed percentile."""
    return (
        TrendMetric(
            metric=REVIEW_COVERAGE,
            summary=rate(numerator, denominator),
            value=round(numerator / denominator * 100, 1),
        ),
        TrendMetric(metric=CYCLE_TIME, summary=cycle(median), value=median, percentile=Percentile.MEDIAN),
    )


def series_throughput(reported: int, direct_commits: int, contributors: int) -> TrendThroughput:
    """Count what one observed window put onto the default branch, and how many people put it there.

    `contributors` is passed rather than derived from the merge counts, because the two are
    genuinely independent: one person can merge six changes, and six merges can carry no person at
    all where an agent raised every one of them.
    """
    return TrendThroughput(
        merges=reported + direct_commits,
        merged_pull_requests=reported,
        direct_commits=direct_commits,
        active_contributors=contributors,
    )


def series_period(
    index: int,
    reported: int,
    direct_commits: int,
    metrics: tuple[TrendMetric, ...],
    deltas: tuple[TrendDelta, ...],
    *,
    detail: str | None = None,
    contributors: int = 2,
) -> TrendPeriod:
    """Build one observed period of a series, measured against the shared baseline."""
    starts_at, ends_at = series_window(index)
    return TrendPeriod(
        index=index,
        starts_at=starts_at,
        ends_at=ends_at,
        provenance=provenance(),
        cohort=CohortSummary(merged=reported, reported=reported, excluded_authors={}),
        throughput=series_throughput(reported, direct_commits, contributors),
        metrics=metrics,
        deltas=deltas,
        detail=detail,
    )


def trend_baseline() -> TrendWindow:
    """Build the baseline window: two merges, half of them independently reviewed."""
    starts_at, ends_at = series_window(0)
    return TrendWindow(
        starts_at=starts_at,
        ends_at=ends_at,
        provenance=provenance(),
        cohort=CohortSummary(merged=2, reported=2, excluded_authors={}),
        throughput=series_throughput(2, 0, 2),
        metrics=series_metrics(1, 2, 12.5),
    )


def improved_period() -> TrendPeriod:
    """Build a first period that reviewed everything, merged faster, pushed a commit, and grew a person."""
    return series_period(
        1,
        2,
        1,
        series_metrics(2, 2, 6.25),
        (
            TrendDelta(measure="merges", basis=DeltaBasis.PERCENTAGE_CHANGE, baseline=2, period=3, change=50.0),
            TrendDelta(
                measure="merged-pull-requests",
                basis=DeltaBasis.PERCENTAGE_CHANGE,
                baseline=2,
                period=2,
                change=0.0,
            ),
            TrendDelta(
                measure="direct-commits",
                basis=DeltaBasis.PERCENTAGE_CHANGE,
                baseline=0,
                period=1,
                detail="baseline-zero",
            ),
            TrendDelta(
                measure="active-contributors",
                basis=DeltaBasis.PERCENTAGE_CHANGE,
                baseline=2,
                period=3,
                change=50.0,
            ),
            TrendDelta(
                measure=REVIEW_COVERAGE,
                basis=DeltaBasis.PERCENTAGE_POINTS,
                baseline=50.0,
                period=100.0,
                change=50.0,
                unit="percent",
            ),
            TrendDelta(
                measure=CYCLE_TIME,
                basis=DeltaBasis.PERCENTAGE_CHANGE,
                baseline=12.5,
                period=6.25,
                change=-50.0,
                unit="hours",
                percentile=Percentile.MEDIAN,
            ),
        ),
        contributors=3,
    )


def thin_period() -> TrendPeriod:
    """Build a mid-series period whose cohort is too small to carry a metric value."""
    return series_period(
        2,
        1,
        0,
        (),
        (
            TrendDelta(measure="merges", basis=DeltaBasis.PERCENTAGE_CHANGE, baseline=2, period=1, change=-50.0),
            TrendDelta(
                measure="merged-pull-requests",
                basis=DeltaBasis.PERCENTAGE_CHANGE,
                baseline=2,
                period=1,
                change=-50.0,
            ),
            TrendDelta(
                measure="direct-commits",
                basis=DeltaBasis.PERCENTAGE_CHANGE,
                baseline=0,
                period=0,
                detail="baseline-zero",
            ),
            TrendDelta(
                measure="active-contributors",
                basis=DeltaBasis.PERCENTAGE_CHANGE,
                baseline=2,
                period=1,
                change=-50.0,
            ),
        ),
        detail=SUPPRESSED,
        contributors=1,
    )


def unobserved_period() -> TrendPeriod:
    """Build a period the cache could not answer for at all."""
    starts_at, ends_at = series_window(3)
    return TrendPeriod(index=3, starts_at=starts_at, ends_at=ends_at, detail=UNCOVERED)


def trend_alerts() -> tuple[AlertObservation, ...]:
    """Build the open-alert counts two collection runs recorded across the series.

    Two runs, not four: the observations are samples taken whenever `collect` ran, and there is
    deliberately no row per period. Code scanning was withheld on both runs and so appears at all.
    """
    return (
        AlertObservation(
            family=AlertFamily.DEPENDABOT,
            fetched_at=datetime(2026, 7, 14, 9, tzinfo=UTC),
            open=6,
            by_severity={AlertSeverity.HIGH: 4, AlertSeverity.LOW: 2},
        ),
        AlertObservation(
            family=AlertFamily.DEPENDABOT,
            fetched_at=datetime(2026, 7, 28, 9, tzinfo=UTC),
            open=4,
            by_severity={AlertSeverity.HIGH: 4},
        ),
        AlertObservation(
            family=AlertFamily.CREDENTIAL_SCANNING, fetched_at=datetime(2026, 7, 14, 9, tzinfo=UTC), open=1
        ),
    )


def repository_trend(**overrides: object) -> RepositoryTrend:
    """Build one repository's series, overriding selected fields."""
    trend = RepositoryTrend(
        repository="cath-service",
        enablement_at=datetime(2026, 7, 18, tzinfo=UTC),
        baseline=trend_baseline(),
        periods=(improved_period(), thin_period(), unobserved_period()),
        alert_observations=trend_alerts(),
    )
    return trend.model_copy(update=overrides)


def unanchored_trend() -> RepositoryTrend:
    """Build the series of a repository nobody recorded an enablement date for."""
    return RepositoryTrend(repository="opal-common-lib", detail="no enablement date configured")


def trend_teams() -> dict[str, str]:
    """Map every repository the trend fixtures name to the team that owns it."""
    return {"cath-service": "crime", "opal-common-lib": "opal"}


def trend_of(*repositories: RepositoryTrend) -> TrendReport:
    """Build a trend report over the given repositories."""
    return TrendReport(organization="hmcts", period_days=7, repositories=repositories)


@pytest.fixture
def trend() -> str:
    """Render the default trend fixtures: one anchored series and one repository without an anchor."""
    return render_trend_report(trend_of(repository_trend(), unanchored_trend()), trend_teams())


def block_of(report: str, repository: str) -> str:
    """Return the lines rendered under one repository's banner."""
    blocks = report.split(f"hmcts/{repository}\n")
    return blocks[1].split("\n=")[0]


def section_of(report: str, title: str) -> str:
    """Return the lines rendered under one section heading, up to the blank line that ends it."""
    return report.split(f"\n{title}\n{'-' * len(title)}\n")[1].split("\n\n", maxsplit=1)[0]


def row_of(report: str, title: str, label: str) -> list[str]:
    """Return the cells of one section's table row carrying the given first column.

    The section is named because the two tables of a series hold the same rows: one carries each
    measure's values, the other its movement, and a helper that took the first match would silently
    assert against the wrong one.
    """
    line = next(item for item in section_of(report, title).splitlines() if item.startswith(f"  {label}  "))
    return line.split()


def test_the_trend_opens_with_an_index_of_every_configured_repository(trend: str) -> None:
    """Answer which repositories are anchored, and how much of each series exists, before scrolling."""
    assert trend.startswith("Index\n-----\n")
    assert index_lines(trend) == [
        "  Team        Repository            Enabled  Periods observed",
        "  crime     cath-service  2026-07-18T00:00Z            2 of 3",
        "  opal   opal-common-lib     not configured              none",
    ]


def test_the_trend_index_combines_nothing_across_repositories(trend: str) -> None:
    """Hold one row per repository and nothing else: an average of deltas is a roll-up in disguise.

    Asserted as the exact row set rather than by probing for a suspicious word, because the shape a
    roll-up would arrive in is an extra row — an organisation total, a per-team figure, a mean
    change — and each of those fails this comparison whatever it is called.
    """
    assert [line.split() for line in index_lines(trend)[1:]] == [
        ["crime", "cath-service", "2026-07-18T00:00Z", "2", "of", "3"],
        ["opal", "opal-common-lib", "not", "configured", "none"],
    ]


def test_each_repository_states_its_anchor_and_how_the_series_is_cut(trend: str) -> None:
    """Report the enablement instant and the period length above the series measured from them."""
    assert "  Enabled  2026-07-18T00:00Z" in trend
    assert "  Period   7 days" in trend
    assert "  Series   3 whole periods since enablement" in trend


def test_the_windows_of_the_series_are_dated_baseline_first(trend: str) -> None:
    """Say which dates each column of the tables below covers, in the order they are reported."""
    windows = block_of(trend, "cath-service").splitlines()
    assert windows[windows.index("Windows") + 2 : windows.index("Windows") + 7] == [
        "  Window               Starts               Ends",
        "  Baseline  2026-07-11T00:00Z  2026-07-18T00:00Z",
        "  P1        2026-07-18T00:00Z  2026-07-25T00:00Z",
        "  P2        2026-07-25T00:00Z  2026-08-01T00:00Z",
        "  P3        2026-08-01T00:00Z  2026-08-08T00:00Z",
    ]


def test_a_window_that_reported_no_value_says_why_beneath_its_dates(trend: str) -> None:
    """Give a thin period and an unreadable one their reasons, so a flat stretch is never a mystery."""
    assert f"  P2: {SUPPRESSED}" in trend
    assert f"  P3: {UNCOVERED}" in trend


def test_metrics_are_rows_and_periods_are_columns(trend: str) -> None:
    """Read one measure across the whole series on one line, which is what a six-period series needs."""
    assert "  Measure                         Unit  Baseline    P1  P2  P3" in trend
    assert "  merges                         count         2     3   1   -" in trend
    assert "  independent-review-coverage  percent        50   100   -   -" in trend


def test_the_people_behind_a_period_are_a_row_of_their_own(trend: str) -> None:
    """Print the contributor count beside the merge counts, so a reader sees who as well as how much.

    Its own row rather than a figure folded into the merge counts: three merges by one person and
    three merges by three people are different weeks, and only this row tells them apart.
    """
    assert row_of(trend, "Values", "active-contributors")[-4:] == ["2", "3", "1", "-"]
    assert row_of(trend, "Change from baseline", "active-contributors")[-3:] == ["+50", "-50", "-"]


def test_a_distribution_is_labelled_with_the_percentile_it_is_read_at(trend: str) -> None:
    """Name the percentile a series is compared on: the median and the 75th are different measurements."""
    assert "  merge-cycle-time (median)      hours      12.5  6.25   -   -" in trend


def test_a_window_with_no_value_renders_absent_rather_than_zero(trend: str) -> None:
    """Never let a suppressed or unreadable window print a zero nobody measured."""
    assert row_of(trend, "Values", "merged-pull-requests")[-4:] == ["2", "2", "1", "-"]
    assert row_of(trend, "Values", "merge-cycle-time (median)")[-4:] == ["12.5", "6.25", "-", "-"]


def test_each_period_reports_its_movement_from_the_baseline_on_its_own_basis(trend: str) -> None:
    """Show what arithmetic each row reports, because points and per cent both print as a number."""
    assert "  Measure                                  Basis   P1   P2  P3" in trend
    assert "  merges                       percentage_change  +50  -50   -" in trend
    assert "  independent-review-coverage  percentage_points  +50    -   -" in trend
    assert "  merge-cycle-time (median)    percentage_change  -50    -   -" in trend


def test_a_delta_that_could_not_be_computed_reports_its_reason(trend: str) -> None:
    """Report a rise from nothing as the reason it has no percentage, never as an infinite one."""
    assert row_of(trend, "Change from baseline", "direct-commits")[-3:] == ["-", "-", "-"]
    assert "  P1 direct-commits: baseline-zero" in trend


def test_a_measure_no_period_observed_reports_no_basis_beside_its_absent_movements() -> None:
    """Leave the basis absent where nothing was compared, rather than naming an arithmetic nobody did.

    Reachable whenever the baseline observed a metric that every period then lacked — reviews in the
    weeks before enablement and none since — which puts a row in the values table that the change
    table has no delta for.
    """
    coverage_only = series_period(
        1,
        2,
        0,
        series_metrics(2, 2, 6.25)[:1],
        (
            TrendDelta(
                measure=REVIEW_COVERAGE,
                basis=DeltaBasis.PERCENTAGE_POINTS,
                baseline=50.0,
                period=100.0,
                change=50.0,
                unit="percent",
            ),
        ),
    )
    rendered = render_trend_report(trend_of(repository_trend(periods=(coverage_only,))), trend_teams())

    assert row_of(rendered, "Values", "merge-cycle-time (median)")[-2:] == ["12.5", "-"]
    assert row_of(rendered, "Change from baseline", "merge-cycle-time (median)")[-2:] == ["-", "-"]


def test_a_series_whose_baseline_is_not_comparable_renders_no_change_table() -> None:
    """State once why nothing was compared, rather than tabulating a column of absent movements.

    Built in the shape `trend.py` actually produces one in: a baseline the cache could not answer
    for, which is what leaves the periods carrying no deltas and the repository carrying the reason.
    """
    detail = f"the baseline window is not comparable, so no delta was computed: {UNCOVERED}"
    starts_at, ends_at = series_window(0)
    rendered = render_trend_report(
        trend_of(
            repository_trend(
                baseline=TrendWindow(starts_at=starts_at, ends_at=ends_at, detail=UNCOVERED),
                periods=(series_period(1, 2, 1, series_metrics(2, 2, 6.25), ()),),
                delta_detail=detail,
            ),
        ),
        trend_teams(),
    )

    assert f"Change from baseline\n--------------------\n  not computed: {detail}" in rendered
    assert "percentage_points" not in rendered


def test_a_repository_with_no_series_renders_its_reason_and_no_tables(trend: str) -> None:
    """Name the missing anchor rather than dropping the repository or tabulating an empty series."""
    block = block_of(trend, "opal-common-lib")

    assert "  Enabled  not configured" in block
    assert "  Series   none: no enablement date configured" in block
    assert "Windows" not in block


def test_one_whole_period_reads_as_one_rather_than_as_a_plural() -> None:
    """Report a series of one period in words that read at one as well as at six."""
    rendered = render_trend_report(
        trend_of(repository_trend(periods=(improved_period(),))),
        trend_teams(),
    )

    assert "  Series   1 whole period since enablement" in rendered


def test_the_report_states_only_the_numbers_the_json_carries(trend: str) -> None:
    """Pin the rendering to the contract: every cell is read from the models the JSON emits.

    Asserted against the models rather than against literals, so a rendering that computed a figure
    of its own — an average, a total, a re-derived percentile — could not agree with the JSON and
    would fail here.
    """
    series = repository_trend()
    assert series.baseline is not None
    baseline = {item.metric: item.value for item in series.baseline.metrics}
    period = {item.metric: item.value for item in series.periods[0].metrics}
    change = {item.measure: item.change for item in series.periods[0].deltas}

    assert row_of(trend, "Values", REVIEW_COVERAGE)[-4:-2] == [
        f"{baseline[REVIEW_COVERAGE]:g}",
        f"{period[REVIEW_COVERAGE]:g}",
    ]
    assert row_of(trend, "Change from baseline", "merges")[-3:] == [f"+{change['merges']:g}", "-50", "-"]


def test_alert_observations_are_rendered_at_the_instants_they_were_observed(trend: str) -> None:
    """Render one row per observation, never one per period and never a gap filled in.

    Three rows for three observations across four windows: a sampled series that showed a row per
    period would assert a count at instants nobody looked, and the whole point of storing these is
    that a past open count cannot be reconstructed.
    """
    rows = [line.split() for line in section_of(trend, "Security alert observations").splitlines()]

    assert rows[:4] == [
        ["Family", "Observed", "open", "critical", "high", "medium", "low"],
        ["dependabot", "2026-07-14T09:00Z", "6", "0", "4", "0", "2"],
        ["dependabot", "2026-07-28T09:00Z", "4", "0", "4", "0", "0"],
        ["secret-scanning", "2026-07-14T09:00Z", "1", "0", "0", "0", "0"],
    ]
    assert "  observed when collect ran, so a gap between rows is a gap in the collection cadence" in trend
    assert "  secret-scanning alerts carry no severity, so their severity columns are always zero" in trend


def test_a_series_with_no_alert_observations_says_so_rather_than_rendering_zeroes() -> None:
    """Say that nothing was observed, because a table of zeroes would read as a clean repository."""
    rendered = render_trend_report(trend_of(repository_trend(alert_observations=())), trend_teams())

    assert section_of(rendered, "Security alert observations") == (
        "  none recorded across the windows this series reports"
    )


def test_the_trend_renders_repositories_in_the_order_the_report_lists_them() -> None:
    """Keep the index and the series it points at in the one fixed team-then-repository order."""
    rendered = render_trend_report(
        trend_of(repository_trend(), unanchored_trend()),
        trend_teams(),
    )

    assert [line.split()[1] for line in index_lines(rendered)[1:]] == ["cath-service", "opal-common-lib"]
    positions = [rendered.index(f"hmcts/{name}") for name in ("cath-service", "opal-common-lib")]
    assert positions == sorted(positions)
