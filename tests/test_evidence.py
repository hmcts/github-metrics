"""Test behaviour evidence projections and window loading."""

from collections import Counter
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

import pytest

from metrics.assessment import ReadinessPolicy
from metrics.behaviour import commit_query_signature, query_signature
from metrics.behaviour_metrics import behaviour_metrics
from metrics.behaviour_metrics.approval_coverage import ApprovalCoverage
from metrics.behaviour_metrics.base import BehaviourMetric
from metrics.behaviour_metrics.description_quality import DescriptionQuality
from metrics.behaviour_metrics.independent_review_coverage import IndependentReviewCoverage
from metrics.behaviour_metrics.merge_cycle_time import MergeCycleTime
from metrics.behaviour_metrics.pull_request_size import PullRequestSize
from metrics.behaviour_metrics.time_to_first_review import TimeToFirstReview
from metrics.behaviour_metrics.traceability_reference import TraceabilityReference
from metrics.config import (
    AssessmentConfiguration,
    Configuration,
    LookbackConfiguration,
    PracticeRuleConfiguration,
    TraceabilityConfiguration,
    TrivialityConfiguration,
)
from metrics.domain import (
    AlertSeverity,
    AvailabilityReason,
    BehaviourProvenance,
    CacheStatus,
    CodeownersEvidence,
    CodeownersFile,
    CodeownersReport,
    CohortSummary,
    CollectionStatus,
    DirectCommitFact,
    DistributionObservation,
    EvidenceSource,
    EvidenceUnavailable,
    FindingSeverity,
    MaintenanceEvidence,
    MaintenanceReport,
    MergeGateEvidence,
    MergeGateReport,
    Merges,
    OpenAlertCount,
    OpenPullRequestReport,
    OpenPullRequestSnapshot,
    OpenPullRequestSummary,
    PracticeFinding,
    PullRequestFact,
    PullRequestRule,
    RateObservation,
    ReadinessAssessment,
    ReadinessLabel,
    ReportingWindow,
    RepositoryInventory,
    RepositoryInventoryItem,
    RepositoryMetadata,
    RepositoryPracticeEvidence,
    ReviewFact,
    ReviewState,
    SecurityAlertEvidence,
    SecurityAlertReport,
    SonarGateLevel,
    SonarMeasures,
    SonarProjectMapping,
    SonarProjectResolution,
    SonarQualityGate,
    SonarQualityGateCondition,
    SonarRating,
    SonarReport,
    SonarResolution,
    SourceCoverage,
    WindowProvenance,
)
from metrics.evidence import (
    RepositoryEvidence,
    StoredReports,
    actor_contributions,
    actor_readiness,
    actor_slice,
    cached_repository_evidence,
    cohort_summary,
    collected_repository_evidence,
    maintenance_windows,
    metric_summaries,
    offline_practice_report,
    open_pull_request_report,
    repository_evidence,
    stored_codeowners,
    stored_maintenance,
    stored_merge_gate,
    stored_open_pull_requests,
    stored_reports,
    stored_repository_state,
    stored_security_alerts,
    stored_sonar,
)
from metrics.github import GitHubClient, GitHubError
from metrics.rules.unreviewed_merge import UnreviewedMerge
from metrics.storage import (
    StorageError,
    cache_direct_commit_facts,
    cache_pull_request_facts,
    record_repository_state,
)


def unreviewed_merge(configuration: PracticeRuleConfiguration | None = None) -> UnreviewedMerge:
    """Build the unreviewed-merge rule with default triviality thresholds."""
    return UnreviewedMerge(configuration or PracticeRuleConfiguration(), TrivialityConfiguration())


def uncollected_gate() -> MergeGateReport:
    """Build the merge gate report of a repository whose state has never been collected."""
    return MergeGateReport(detail="no repository state has been collected; run metrics collect")


def uncollected_open_pull_requests() -> OpenPullRequestReport:
    """Build the open pull-request report of a repository whose state has never been collected."""
    return OpenPullRequestReport(detail="no repository state has been collected; run metrics collect")


def uncollected_security() -> SecurityAlertReport:
    """Build the security report of a repository whose state has never been collected."""
    return SecurityAlertReport(detail="no repository state has been collected; run metrics collect")


def uncollected_codeowners() -> CodeownersReport:
    """Build the CODEOWNERS report of a repository whose state has never been collected."""
    return CodeownersReport(detail="no repository state has been collected; run metrics collect")


def uncollected_maintenance() -> MaintenanceReport:
    """Build the maintenance report of a repository whose state has never been collected."""
    return MaintenanceReport(detail="no repository state has been collected; run metrics collect")


def uncollected_sonar() -> SonarReport:
    """Build the SonarCloud report of a repository whose state has never been collected."""
    return SonarReport(detail="no repository state has been collected; run metrics collect")


def uncollected_reports() -> StoredReports:
    """Build every current-state block of a repository whose state has never been collected."""
    return StoredReports(
        merge_gate=uncollected_gate(),
        open_pull_requests=uncollected_open_pull_requests(),
        security=uncollected_security(),
        codeowners=uncollected_codeowners(),
        maintenance=uncollected_maintenance(),
        sonar=uncollected_sonar(),
    )


def default_policy() -> ReadinessPolicy:
    """Build the readiness policy with its default thresholds."""
    return ReadinessPolicy(AssessmentConfiguration(), TrivialityConfiguration())


def all_metrics() -> tuple[BehaviourMetric, ...]:
    """Build every behaviour metric with the default traceability patterns."""
    return behaviour_metrics(TraceabilityConfiguration())


def cached_facts() -> RepositoryEvidence:
    """Build repository evidence containing included and excluded review evidence."""
    starts_at = datetime(2026, 7, 1, tzinfo=UTC)
    return RepositoryEvidence(
        organization="hmcts",
        repository="cath-service",
        starts_at=starts_at,
        ends_at=starts_at + timedelta(days=30),
        provenance=WindowProvenance(offline=True, intervals_fetched=0),
        cohort=CohortSummary(merged=2, reported=2, excluded_authors={}),
        pull_requests=(
            PullRequestFact(
                identifier=101,
                repository="cath-service",
                number=11,
                created_at=starts_at,
                merged_at=starts_at + timedelta(days=2),
                draft=False,
                author_login="author",
                author_type="User",
                reviews=(
                    ReviewFact(
                        identifier=201,
                        submitted_at=starts_at + timedelta(days=1),
                        state=ReviewState.APPROVED,
                        author_login="reviewer",
                        author_type="User",
                    ),
                ),
            ),
            PullRequestFact(
                identifier=102,
                repository="cath-service",
                number=12,
                created_at=starts_at,
                merged_at=starts_at + timedelta(days=1),
                draft=False,
                author_login="second-author",
                author_type="User",
                reviews=(),
            ),
        ),
    )


def direct_commit(
    sha: str,
    author_login: str = "pusher",
    committed_at: datetime | None = None,
    additions: int | None = None,
    deletions: int | None = None,
    changed_files: int | None = None,
) -> DirectCommitFact:
    """Build one direct-commit fact made inside the standard evidence window."""
    return DirectCommitFact(
        sha=sha,
        committed_at=committed_at or datetime(2026, 7, 3, tzinfo=UTC),
        author_login=author_login,
        author_type="User",
        additions=additions,
        deletions=deletions,
        changed_files=changed_files,
    )


def test_metric_report_reconciles_rate_without_identities() -> None:
    """Return aggregate classifications without named references by default."""
    report = cached_facts().metric(
        IndependentReviewCoverage(),
        include_identities=False,
    )

    assert isinstance(report.summary, RateObservation)
    assert report.summary.numerator == 1
    assert report.summary.denominator == 2
    assert report.classifications == {"included": 1, "no-review-events": 1}
    assert not report.identities_included
    assert report.pull_requests is None


def test_metric_report_includes_named_approval_references_explicitly() -> None:
    """Include PR and eligible review identities only after explicit opt-in."""
    report = cached_facts().metric(ApprovalCoverage(), include_identities=True)

    assert report.pull_requests is not None
    assert report.pull_requests[0].url == "https://github.com/hmcts/cath-service/pull/11"
    assert report.pull_requests[0].author_login == "author"
    assert report.pull_requests[0].reviews[0].author_login == "reviewer"
    assert report.pull_requests[0].reviews[0].state is ReviewState.APPROVED
    assert report.pull_requests[1].classification == "no-review-events"


def test_metric_report_includes_merge_cycle_samples() -> None:
    """Expose each cycle-time sample used by the aggregate distribution."""
    report = cached_facts().metric(MergeCycleTime(), include_identities=True)

    assert isinstance(report.summary, DistributionObservation)
    assert report.summary.sample_size == 2
    assert report.summary.median == 36
    assert report.classifications == {"included": 2}
    assert report.pull_requests is not None
    assert tuple(item.value for item in report.pull_requests) == (48, 24)


@pytest.mark.parametrize(
    ("state", "author_login", "author_type", "submitted_days", "expected"),
    [
        (ReviewState.COMMENTED, "reviewer", "User", 3, "reviews-after-merge"),
        (ReviewState.PENDING, "reviewer", "User", 1, "pending-reviews-only"),
        (ReviewState.APPROVED, "automation[bot]", "Bot", 1, "bot-or-unattributed-reviews-only"),
        (ReviewState.APPROVED, "author", "User", 1, "author-reviews-only"),
    ],
)
def test_metric_report_explains_ineligible_review_events(
    state: ReviewState,
    author_login: str,
    author_type: str,
    submitted_days: int,
    expected: str,
) -> None:
    """Classify each reason that submitted review activity is not independently eligible."""
    cached = cached_facts()
    pull_request = cached.pull_requests[0].model_copy(
        update={
            "reviews": (
                ReviewFact(
                    identifier=301,
                    submitted_at=cached.starts_at + timedelta(days=submitted_days),
                    state=state,
                    author_login=author_login,
                    author_type=author_type,
                ),
            ),
        },
    )

    report = cached.model_copy(update={"pull_requests": (pull_request,)}).metric(
        IndependentReviewCoverage(),
        include_identities=False,
    )

    assert report.classifications == {expected: 1}


def test_metric_report_includes_excluded_review_events_in_named_output() -> None:
    """Expose the event behind an exclusion only in explicit named evidence."""
    cached = cached_facts()
    author_review = cached.pull_requests[0].reviews[0].model_copy(update={"author_login": "author"})
    pull_request = cached.pull_requests[0].model_copy(update={"reviews": (author_review,)})

    report = cached.model_copy(update={"pull_requests": (pull_request,)}).metric(
        IndependentReviewCoverage(),
        include_identities=True,
    )

    assert report.pull_requests is not None
    assert report.pull_requests[0].classification == "author-reviews-only"
    assert report.pull_requests[0].reviews[0].author_login == "author"


def test_metric_report_distinguishes_review_without_approval() -> None:
    """Keep independent review coverage separate from explicit approval coverage."""
    cached = cached_facts()
    pull_request = cached.pull_requests[0].model_copy(
        update={"reviews": (cached.pull_requests[0].reviews[0].model_copy(update={"state": ReviewState.COMMENTED}),)},
    )

    report = cached.model_copy(update={"pull_requests": (pull_request,)}).metric(
        ApprovalCoverage(),
        include_identities=False,
    )

    assert report.classifications == {"independent-review-without-approval": 1}


def test_unreviewed_merge_groups_authors() -> None:
    """Group unreviewed human-authored merges while excluding reviewed and bot-authored pull requests."""
    cached = cached_facts()
    unreviewed = cached.pull_requests[1]
    author_review = cached.pull_requests[0].reviews[0].model_copy(update={"author_login": "second-author"})
    second_unreviewed = unreviewed.model_copy(
        update={
            "identifier": 103,
            "number": 13,
            "reviews": (author_review,),
        },
    )
    bot_authored = unreviewed.model_copy(
        update={
            "identifier": 104,
            "number": 14,
            "author_login": "automation[bot]",
            "author_type": "Bot",
        },
    )
    reviewed = cached.pull_requests[0].model_copy(
        update={
            "identifier": 105,
            "number": 15,
            "author_login": "second-author",
        },
    )

    rule = unreviewed_merge(PracticeRuleConfiguration(minimum_occurrences=2))
    findings = rule.findings(
        cached.model_copy(
            update={"pull_requests": (*cached.pull_requests, second_unreviewed, bot_authored, reviewed)},
        ),
    )

    assert len(findings) == 1
    assert findings[0].actor_login == "second-author"
    assert findings[0].occurrences == 2
    assert findings[0].authored_merges == 3
    assert findings[0].percentage == 66.7
    assert findings[0].message == ("second-author: 2 of 3 merges had no independent human review (66.7%) — 2 unsized")
    assert findings[0].occurrences_by_size == {"unsized": 2}
    assert tuple(reference.number for reference in findings[0].pull_requests) == (12, 13)
    # An actor who pushed nothing directly carries no key at all, not an empty array.
    assert findings[0].direct_commits is None


def test_unreviewed_merge_separates_trivial_changes_from_substantial_ones() -> None:
    """Separate a one-line fix merged without review from a multi-file change merged the same way."""
    cached = cached_facts()
    trivial = cached.pull_requests[1].model_copy(update={"additions": 1, "deletions": 0, "changed_files": 1})
    substantial = cached.pull_requests[1].model_copy(
        update={"identifier": 109, "number": 19, "additions": 30, "deletions": 12, "changed_files": 3},
    )

    findings = unreviewed_merge().findings(
        cached.model_copy(update={"pull_requests": (trivial, substantial)}),
    )

    assert findings[0].occurrences_by_size == {"substantial": 1, "trivial": 1}
    assert findings[0].pull_requests[0].size_class == "trivial"
    assert findings[0].pull_requests[0].changed_lines == 1
    assert findings[0].pull_requests[1].size_class == "substantial"
    assert findings[0].pull_requests[1].changed_lines == 42
    assert findings[0].pull_requests[1].changed_files == 3


def test_unreviewed_merge_honors_configured_triviality_thresholds() -> None:
    """Let configuration decide what counts as trivial rather than fixing a threshold in code."""
    cached = cached_facts()
    change = cached.pull_requests[1].model_copy(update={"additions": 40, "deletions": 5, "changed_files": 2})

    findings = UnreviewedMerge(
        PracticeRuleConfiguration(),
        TrivialityConfiguration(maximum_lines=50, maximum_files=2),
    ).findings(cached.model_copy(update={"pull_requests": (change,)}))

    assert findings[0].occurrences_by_size == {"trivial": 1}


def test_unreviewed_merge_reports_agent_authored_merges() -> None:
    """Surface agent-authored merges that had no independent human review rather than hiding them."""
    evidence = cached_facts()
    agent = evidence.pull_requests[1].model_copy(
        update={"identifier": 106, "number": 16, "author_login": "copilot-swe-agent[bot]", "author_type": "Bot"},
    )

    findings = unreviewed_merge().findings(
        evidence.model_copy(update={"pull_requests": (agent,)}),
    )

    assert tuple(finding.actor_login for finding in findings) == ("copilot-swe-agent[bot]",)


def test_repository_evidence_excludes_dependency_automation_but_keeps_agents(tmp_path: Path) -> None:
    """Leave dependency bots out of the cohort while retaining agent-authored work."""
    facts = cached_facts().pull_requests
    renovate = facts[1].model_copy(
        update={"identifier": 107, "number": 17, "author_login": "renovate[bot]", "author_type": "Bot"},
    )
    agent = facts[1].model_copy(
        update={"identifier": 108, "number": 18, "author_login": "copilot-swe-agent[bot]", "author_type": "Bot"},
    )

    evidence = repository_evidence(
        configuration(tmp_path),
        "cath-service",
        window(30),
        Merges(
            pull_requests=(*facts, renovate, agent),
            direct_commits=(direct_commit("aaa"), direct_commit("bbb", author_login="renovate[bot]")),
        ),
        WindowProvenance(offline=True, intervals_fetched=0),
    )

    assert tuple(fact.number for fact in evidence.pull_requests) == (11, 12, 18)
    # The same exclusions apply to a bot that pushes straight to the default branch.
    assert tuple(commit.sha for commit in evidence.direct_commits) == ("aaa",)
    assert evidence.cohort == CohortSummary(
        merged=4,
        reported=3,
        excluded_authors={"renovate[bot]": 1},
        direct_commits=1,
    )


def test_repository_practice_evidence_honors_rule_controls() -> None:
    """Allow a rule to exclude service identities or be disabled independently."""
    cached = cached_facts()
    excluded = cached.practices(
        (
            unreviewed_merge(PracticeRuleConfiguration(excluded_logins=("second-author",))),
            unreviewed_merge(PracticeRuleConfiguration(enabled=False)),
        ),
        default_policy(),
        uncollected_reports(),
        "civil",
        all_metrics(),
    )

    assert excluded.behaviour == ()


def test_repository_practice_evidence_collates_every_enabled_rule() -> None:
    """Flatten findings from enabled rule instances and skip disabled instances."""
    configuration = PracticeRuleConfiguration()
    report = cached_facts().practices(
        (
            unreviewed_merge(configuration),
            unreviewed_merge(configuration),
            unreviewed_merge(PracticeRuleConfiguration(enabled=False)),
        ),
        default_policy(),
        uncollected_reports(),
        "civil",
        all_metrics(),
    )

    assert len(report.behaviour) == 2
    assert report.merge_gate == uncollected_gate()


def test_repository_practice_evidence_omits_a_disabled_assessment() -> None:
    """Leave the assessment out entirely rather than emitting an unjudged label."""
    report = cached_facts().practices(
        (unreviewed_merge(),),
        ReadinessPolicy(AssessmentConfiguration(enabled=False), TrivialityConfiguration()),
        uncollected_reports(),
        "civil",
        all_metrics(),
    )

    assert report.assessment is None
    assert report.behaviour


def test_repository_practice_evidence_carries_the_owning_team_and_the_cohort_metrics() -> None:
    """Name the team the configuration says owns the repository, and summarise every metric over it."""
    report = cached_facts().practices(
        (unreviewed_merge(),), default_policy(), uncollected_reports(), "civil", all_metrics()
    )

    assert report.team == "civil"
    assert tuple(summary.metric for summary in report.metrics) == tuple(metric.identifier for metric in all_metrics())


def test_metric_summaries_report_what_the_drill_down_of_each_metric_reports() -> None:
    """Compute one repository's summaries exactly as a `--metric` drill-down computes its own.

    The point of the shared helper: the readable report, the JSON block and a drill-down are three
    renderings of one computation, so a figure cannot appear in one of them and not in another.
    """
    cached = cached_facts()

    summaries = metric_summaries(cached, all_metrics())

    assert {summary.metric: (summary.summary, summary.classifications) for summary in summaries} == {
        metric.identifier: (
            cached.metric(metric, include_identities=False).summary,
            cached.metric(metric, include_identities=False).classifications,
        )
        for metric in all_metrics()
    }


def test_actor_slice_narrows_a_cohort_to_one_person_by_either_route() -> None:
    """Keep the merges one person authored, however they were spelled and however they arrived."""
    cached = cached_facts().model_copy(
        update={
            "pull_requests": (
                cached_facts().pull_requests[0].model_copy(update={"author_login": "Author"}),
                cached_facts().pull_requests[1],
            ),
            "direct_commits": (direct_commit("aaa", author_login="AUTHOR"), direct_commit("bbb")),
        },
    )

    sliced = actor_slice(cached, "author")

    assert tuple(fact.number for fact in sliced.pull_requests) == (11,)
    assert tuple(commit.sha for commit in sliced.direct_commits) == ("aaa",)


def test_actor_slice_attributes_nothing_to_a_change_nobody_is_named_for() -> None:
    """Leave a merge GitHub matched to no account out of everybody's slice rather than out of one."""
    cached = cached_facts().model_copy(
        update={
            "pull_requests": (cached_facts().pull_requests[0].model_copy(update={"author_login": None}),),
            "direct_commits": (direct_commit("aaa").model_copy(update={"author_login": None}),),
        },
    )

    sliced = actor_slice(cached, "author")

    assert sliced == Merges(pull_requests=(), direct_commits=())


def test_metric_report_measures_time_to_first_independent_review() -> None:
    """Measure the wait for a first independent review and exclude pull requests that had none."""
    report = cached_facts().metric(TimeToFirstReview(), include_identities=True)

    assert isinstance(report.summary, DistributionObservation)
    assert report.summary.sample_size == 1
    assert report.summary.unit == "hours"
    assert report.summary.median == 24
    assert report.classifications == {"included": 1, "no-review-events": 1}
    assert report.pull_requests is not None
    assert tuple(item.value for item in report.pull_requests) == (24, None)


def test_time_to_first_review_uses_the_earliest_eligible_review() -> None:
    """Ignore later reviews and reviews that are not independently eligible."""
    evidence = cached_facts()
    first = evidence.pull_requests[0]
    reviews = (
        first.reviews[0].model_copy(update={"identifier": 301, "submitted_at": first.created_at + timedelta(hours=40)}),
        first.reviews[0].model_copy(update={"identifier": 302, "submitted_at": first.created_at + timedelta(hours=8)}),
        first.reviews[0].model_copy(
            update={"identifier": 303, "submitted_at": first.created_at + timedelta(hours=2), "author_login": "author"},
        ),
    )

    assert TimeToFirstReview().value(first.model_copy(update={"reviews": reviews})) == 8


def test_metric_report_measures_pull_request_size() -> None:
    """Report changed lines and separate pull requests GitHub did not size."""
    evidence = cached_facts()
    sized = evidence.pull_requests[0].model_copy(update={"additions": 30, "deletions": 12})
    report = evidence.model_copy(update={"pull_requests": (sized, evidence.pull_requests[1])}).metric(
        PullRequestSize(),
        include_identities=True,
    )

    assert isinstance(report.summary, DistributionObservation)
    assert report.summary.sample_size == 1
    assert report.summary.unit == "lines"
    assert report.summary.median == 42
    assert report.classifications == {"included": 1, "size-unavailable": 1}
    assert report.pull_requests is not None
    assert tuple(item.value for item in report.pull_requests) == (42, None)


def test_metric_report_measures_description_quality() -> None:
    """Report the share of merged pull requests with a sufficiently long description."""
    evidence = cached_facts()
    described = evidence.pull_requests[0].model_copy(update={"body": "x" * 40})
    report = evidence.model_copy(update={"pull_requests": (described, evidence.pull_requests[1])}).metric(
        DescriptionQuality(TraceabilityConfiguration()),
        include_identities=True,
    )

    assert isinstance(report.summary, RateObservation)
    assert report.summary.numerator == 1
    assert report.summary.denominator == 2
    assert report.classifications == {"described": 1, "description-too-short": 1}
    assert report.pull_requests is not None
    assert tuple(item.reviews for item in report.pull_requests) == ((), ())


def test_metric_report_measures_traceability_reference() -> None:
    """Report the share of merged pull requests that reference an issue or ticket."""
    evidence = cached_facts()
    referenced = evidence.pull_requests[0].model_copy(update={"title": "Fix #42"})
    report = evidence.model_copy(update={"pull_requests": (referenced, evidence.pull_requests[1])}).metric(
        TraceabilityReference(TraceabilityConfiguration()),
        include_identities=True,
    )

    assert isinstance(report.summary, RateObservation)
    assert report.summary.numerator == 1
    assert report.summary.denominator == 2
    assert report.classifications == {"referenced": 1, "reference-missing": 1}
    assert report.pull_requests is not None
    assert tuple(item.reviews for item in report.pull_requests) == ((), ())


def window(days: int) -> ReportingWindow:
    """Build a reporting window ending at a fixed instant."""
    ends_at = datetime(2026, 8, 8, tzinfo=UTC)
    return ReportingWindow(starts_at=ends_at - timedelta(days=days), ends_at=ends_at)


def configuration(tmp_path: Path) -> Configuration:
    """Build a configuration owning one repository in a temporary database."""
    return Configuration.model_validate(
        {
            "version": 1,
            "organization": "hmcts",
            "database": tmp_path / "metrics.sqlite3",
            "teams": [{"identifier": "civil", "display_name": "Civil", "repositories": ["cath-service"]}],
        },
    )


def merge_gate() -> MergeGateEvidence:
    """Build a merge gate that enforces one approving review but exempts administrators."""
    return MergeGateEvidence(
        branch="main",
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
        status_checks=(),
        restricts_deletions=True,
        blocks_force_pushes=True,
        applies_to_administrators=False,
    )


def collected_state(
    gate: MergeGateEvidence | None,
    security: SecurityAlertEvidence | None = None,
    codeowners: CodeownersEvidence | None = None,
    maintenance: MaintenanceEvidence | None = None,
    sonar: SonarMeasures | None = None,
    sonar_project: SonarProjectResolution | None = None,
    open_pull_requests: OpenPullRequestSnapshot | None = None,
) -> RepositoryInventory:
    """Build one collection whose stored state carries the given current-state blocks."""
    collected_at = datetime(2026, 8, 8, 9, tzinfo=UTC)
    return RepositoryInventory(
        status=CollectionStatus.COMPLETE,
        organization="hmcts",
        collected_at=collected_at,
        starts_at=collected_at - timedelta(days=7),
        ends_at=collected_at,
        repositories=(
            RepositoryInventoryItem(
                team_identifier="civil",
                repository=RepositoryMetadata(
                    name="cath-service",
                    default_branch="main",
                    archived=False,
                    fork=False,
                    disabled=False,
                    created_at=datetime(2021, 1, 1, tzinfo=UTC),
                    updated_at=collected_at,
                    pushed_at=collected_at,
                ),
                merge_gate=gate,
                security=security,
                codeowners=codeowners,
                maintenance=maintenance,
                sonar=sonar,
                sonar_project=sonar_project,
                open_pull_requests=open_pull_requests,
            ),
        ),
        failures=(),
    )


def test_stored_merge_gate_reports_the_gate_with_the_instant_it_was_read(tmp_path: Path) -> None:
    """Show the stored gate and its freshness, because it is current state beside a historical window."""
    settings = configuration(tmp_path)
    record_repository_state(settings.database, collected_state(merge_gate()))

    report = stored_merge_gate(stored_repository_state(settings, "cath-service"))

    assert report.gate == merge_gate()
    assert report.fetched_at == datetime(2026, 8, 8, 9, tzinfo=UTC)
    assert report.detail is None


def test_stored_merge_gate_reports_a_repository_that_was_never_collected(tmp_path: Path) -> None:
    """State that no gate has been collected rather than omitting the block or implying no gate."""
    report = stored_merge_gate(stored_repository_state(configuration(tmp_path), "cath-service"))

    assert report.gate is None
    assert report.fetched_at is None
    assert report.detail == "no repository state has been collected; run metrics collect"


def test_stored_merge_gate_separates_an_unobservable_gate_from_an_uncollected_one(tmp_path: Path) -> None:
    """Distinguish a gate that could not be read from a repository that was never collected."""
    settings = configuration(tmp_path)
    record_repository_state(settings.database, collected_state(None))

    report = stored_merge_gate(stored_repository_state(settings, "cath-service"))

    assert report.gate is None
    assert report.fetched_at == datetime(2026, 8, 8, 9, tzinfo=UTC)
    assert report.detail == "the merge gate was not observable when repository state was collected"


def test_stored_merge_gate_preserves_storage_failure(tmp_path: Path) -> None:
    """Describe an unreadable state table instead of raising through the report."""
    with patch("metrics.evidence.load_repository_state", side_effect=StorageError("cache unreadable")):
        report = stored_merge_gate(stored_repository_state(configuration(tmp_path), "cath-service"))

    assert report == MergeGateReport(detail="cache unreadable")


def collected_alerts() -> SecurityAlertEvidence:
    """Build stored alerts where one family was graded, one refused, and one carries no severity."""
    return SecurityAlertEvidence(
        dependabot=OpenAlertCount(open=3, by_severity={AlertSeverity.CRITICAL: 1, AlertSeverity.LOW: 2}),
        code_scanning=OpenAlertCount(detail="GitHub permission denied"),
        secret_scanning=OpenAlertCount(open=0),
    )


def test_stored_security_alerts_reports_the_alerts_with_the_instant_they_were_read(tmp_path: Path) -> None:
    """Serve open alerts from storage with their freshness: current state stored beside the merge gate."""
    settings = configuration(tmp_path)
    record_repository_state(settings.database, collected_state(merge_gate(), collected_alerts()))

    report = stored_security_alerts(stored_repository_state(settings, "cath-service"))

    assert report.alerts == collected_alerts()
    assert report.fetched_at == datetime(2026, 8, 8, 9, tzinfo=UTC)
    assert report.detail is None


def test_stored_security_alerts_survive_the_storage_round_trip(tmp_path: Path) -> None:
    """Keep per-family counts, severities and refusals intact through JSON storage.

    The severity mapping is keyed by an enum and the refusal is a per-family reason, so a round trip
    that quietly stringified either would still typecheck while changing the report.
    """
    settings = configuration(tmp_path)
    record_repository_state(settings.database, collected_state(merge_gate(), collected_alerts()))

    alerts = stored_security_alerts(stored_repository_state(settings, "cath-service")).alerts

    assert alerts is not None
    assert alerts.dependabot.by_severity == {AlertSeverity.CRITICAL: 1, AlertSeverity.LOW: 2}
    assert alerts.code_scanning.open is None
    assert alerts.code_scanning.detail == "GitHub permission denied"
    assert alerts.secret_scanning.open == 0


def test_stored_security_alerts_report_a_repository_that_was_never_collected(tmp_path: Path) -> None:
    """State that nothing was collected rather than omitting the block or implying no alerts."""
    report = stored_security_alerts(stored_repository_state(configuration(tmp_path), "cath-service"))

    assert report.alerts is None
    assert report.fetched_at is None
    assert report.detail == "no repository state has been collected; run metrics collect"


def test_stored_security_alerts_separate_state_collected_before_alerts_existed(tmp_path: Path) -> None:
    """Distinguish state stored without alerts from a repository that was never collected.

    State stored by a build predating this source has no `security` field, and reporting that as
    three families with nothing open would invent a clean bill of health nobody observed.
    """
    settings = configuration(tmp_path)
    record_repository_state(settings.database, collected_state(merge_gate(), None))

    report = stored_security_alerts(stored_repository_state(settings, "cath-service"))

    assert report.alerts is None
    assert report.fetched_at == datetime(2026, 8, 8, 9, tzinfo=UTC)
    assert report.detail == "security alerts were not collected when repository state was stored; run metrics collect"


def test_stored_security_alerts_preserve_storage_failure(tmp_path: Path) -> None:
    """Describe an unreadable state table instead of raising through the report."""
    with patch("metrics.evidence.load_repository_state", side_effect=StorageError("cache unreadable")):
        report = stored_security_alerts(stored_repository_state(configuration(tmp_path), "cath-service"))

    assert report == SecurityAlertReport(detail="cache unreadable")


STANDARDS_FETCHED_AT = datetime(2026, 8, 8, 9, tzinfo=UTC)
"""The instant `collected_state` stamps on the stored row, against which windows derive."""


def collected_codeowners() -> CodeownersEvidence:
    """Build stored CODEOWNERS files where one location counts on GitHub and one does not."""
    return CodeownersEvidence(
        files=(
            CodeownersFile(path=".github/CODEOWNERS", size_bytes=120, recognised_by_github=True),
            CodeownersFile(path="docs/CODEOWNERS.md", size_bytes=0, recognised_by_github=False),
        ),
    )


def maintained(
    last_commit_at: datetime | None,
    last_human_commit_at: datetime | None = None,
    searched_back_to: datetime | None = None,
) -> MaintenanceEvidence:
    """Build stored maintenance instants on the default branch."""
    return MaintenanceEvidence(
        branch="main",
        last_commit_at=last_commit_at,
        last_human_commit_at=last_human_commit_at,
        searched_back_to=searched_back_to,
    )


def test_stored_codeowners_reports_the_files_with_the_instant_they_were_read(tmp_path: Path) -> None:
    """Serve CODEOWNERS presence from storage with its freshness, like the merge gate beside it."""
    settings = configuration(tmp_path)
    record_repository_state(settings.database, collected_state(merge_gate(), codeowners=collected_codeowners()))

    report = stored_codeowners(stored_repository_state(settings, "cath-service"))

    assert report.codeowners == collected_codeowners()
    assert report.fetched_at == STANDARDS_FETCHED_AT
    assert report.detail is None


def test_stored_codeowners_reports_a_repository_that_was_never_collected(tmp_path: Path) -> None:
    """State that nothing was collected rather than omitting the block or implying an absent file."""
    report = stored_codeowners(stored_repository_state(configuration(tmp_path), "cath-service"))

    assert report.codeowners is None
    assert report.fetched_at is None
    assert report.detail == "no repository state has been collected; run metrics collect"


def test_stored_codeowners_separates_state_collected_before_the_check_existed(tmp_path: Path) -> None:
    """Distinguish a row stored without the check from a repository that was never collected.

    Reporting an old row as checked-and-absent would invent an observation nobody made — the same
    rule that keeps a pre-alerts row from reading as a clean bill of health.
    """
    settings = configuration(tmp_path)
    record_repository_state(settings.database, collected_state(merge_gate()))

    report = stored_codeowners(stored_repository_state(settings, "cath-service"))

    assert report.codeowners is None
    assert report.fetched_at == STANDARDS_FETCHED_AT
    assert (
        report.detail == "CODEOWNERS presence was not collected when repository state was stored; run metrics collect"
    )


def test_stored_codeowners_preserves_storage_failure(tmp_path: Path) -> None:
    """Describe an unreadable state table instead of raising through the report."""
    with patch("metrics.evidence.load_repository_state", side_effect=StorageError("cache unreadable")):
        report = stored_codeowners(stored_repository_state(configuration(tmp_path), "cath-service"))

    assert report == CodeownersReport(detail="cache unreadable")


def test_stored_maintenance_derives_every_window_from_the_stored_instants(tmp_path: Path) -> None:
    """Serve the stored instants with the three window rows derived against `fetched_at`."""
    settings = configuration(tmp_path)
    evidence = maintained(
        STANDARDS_FETCHED_AT - timedelta(days=1),
        last_human_commit_at=STANDARDS_FETCHED_AT - timedelta(days=10),
    )
    record_repository_state(settings.database, collected_state(merge_gate(), maintenance=evidence))

    report = stored_maintenance(stored_repository_state(settings, "cath-service"))

    assert report.maintenance == evidence
    assert report.fetched_at == STANDARDS_FETCHED_AT
    assert report.detail is None
    assert tuple(row.months for row in report.windows) == (6, 12, 24)
    assert all(row.committed_within for row in report.windows)
    assert all(row.human_committed_within for row in report.windows)
    assert all(row.human_detail is None for row in report.windows)


def test_stored_maintenance_reports_a_repository_that_was_never_collected(tmp_path: Path) -> None:
    """State that nothing was collected rather than implying an unmaintained repository."""
    report = stored_maintenance(stored_repository_state(configuration(tmp_path), "cath-service"))

    assert report.maintenance is None
    assert report.windows == ()
    assert report.detail == "no repository state has been collected; run metrics collect"


def test_stored_maintenance_separates_state_collected_before_the_check_existed(tmp_path: Path) -> None:
    """Distinguish a row stored without the check from a repository that was never collected."""
    settings = configuration(tmp_path)
    record_repository_state(settings.database, collected_state(merge_gate()))

    report = stored_maintenance(stored_repository_state(settings, "cath-service"))

    assert report.maintenance is None
    assert report.windows == ()
    assert report.fetched_at == STANDARDS_FETCHED_AT
    assert report.detail == "maintenance state was not collected when repository state was stored; run metrics collect"


def test_stored_maintenance_preserves_storage_failure(tmp_path: Path) -> None:
    """Describe an unreadable state table instead of raising through the report."""
    with patch("metrics.evidence.load_repository_state", side_effect=StorageError("cache unreadable")):
        report = stored_maintenance(stored_repository_state(configuration(tmp_path), "cath-service"))

    assert report == MaintenanceReport(detail="cache unreadable")


SONAR_ANALYSED_AT = datetime(2026, 8, 7, 6, 30, tzinfo=UTC)
"""The instant the stored measures below were analysed at, before the row was stored."""


def collected_project(project_key: str = "hmcts.cath") -> SonarProjectResolution:
    """Build the stored resolution of a repository attributed to one project by the stored map."""
    return SonarProjectResolution(
        mapping=SonarProjectMapping(
            project_key=project_key,
            repository="cath-service",
            method=SonarResolution.DECLARED_CONFIRMED_BY_MAP,
            analysis_at=SONAR_ANALYSED_AT,
            revision="ce34e614",
        ),
    )


def collected_measures(project_key: str = "hmcts.cath") -> SonarMeasures:
    """Build stored measures where the gate failed on one condition and one measure is absent."""
    return SonarMeasures(
        project_key=project_key,
        analysis_at=SONAR_ANALYSED_AT,
        gate=SonarQualityGate(
            level=SonarGateLevel.ERROR,
            conditions=(
                SonarQualityGateCondition(
                    metric="new_coverage",
                    comparator="LT",
                    threshold="80",
                    actual="61.4",
                    level=SonarGateLevel.ERROR,
                ),
                SonarQualityGateCondition(
                    metric="new_duplicated_lines_density",
                    comparator="GT",
                    threshold="3",
                    actual="0.0",
                    level=SonarGateLevel.OK,
                ),
            ),
        ),
        coverage=62.1,
        lines_of_code=18422,
        violations=57,
        reliability_issues=3,
        maintainability_issues=41,
        security_issues=1,
        security_hotspots=4,
        reliability_rating=SonarRating(value=2.0),
        maintainability_rating=SonarRating(value=1.0),
        security_rating=SonarRating(value=3.0),
        security_review_rating=SonarRating(value=5.0),
    )


def test_stored_sonar_reports_the_measures_with_the_project_they_were_measured_for(tmp_path: Path) -> None:
    """Serve the stored quality state with its freshness and the project it belongs to.

    The mapping travels with the measures because a block headed by nothing cannot be checked: which
    project answered, and by which rung of the ladder, is the thing that makes a wrong attribution
    diagnosable later rather than mysterious.
    """
    settings = configuration(tmp_path)
    record_repository_state(
        settings.database,
        collected_state(merge_gate(), sonar=collected_measures(), sonar_project=collected_project()),
    )

    report = stored_sonar(stored_repository_state(settings, "cath-service"))

    assert report.measures == collected_measures()
    assert report.mapping == collected_project().mapping
    assert report.fetched_at == STANDARDS_FETCHED_AT
    assert report.detail is None


def test_stored_sonar_survives_the_storage_round_trip(tmp_path: Path) -> None:
    """Keep the gate conditions, the ratings and the absent measures intact through JSON storage.

    A round trip that stringified a rating or defaulted an absent measure to zero would still
    typecheck while changing what the block claims about the code.
    """
    settings = configuration(tmp_path)
    record_repository_state(
        settings.database,
        collected_state(merge_gate(), sonar=collected_measures(), sonar_project=collected_project()),
    )

    measures = stored_sonar(stored_repository_state(settings, "cath-service")).measures

    assert measures is not None
    assert measures.analysis_at == SONAR_ANALYSED_AT
    assert measures.gate is not None
    assert measures.gate.level is SonarGateLevel.ERROR
    assert tuple(condition.metric for condition in measures.gate.conditions) == (
        "new_coverage",
        "new_duplicated_lines_density",
    )
    assert measures.gate.conditions[0].actual == "61.4"
    assert measures.security_review_rating is not None
    assert measures.security_review_rating.letter == "E"
    # Absent stays absent: SonarCloud measured no duplication, which is not the same as none.
    assert measures.duplicated_lines_density is None


def test_stored_sonar_reports_a_repository_that_was_never_collected(tmp_path: Path) -> None:
    """State that nothing was collected rather than implying a repository with no project."""
    report = stored_sonar(stored_repository_state(configuration(tmp_path), "cath-service"))

    assert report.measures is None
    assert report.mapping is None
    assert report.fetched_at is None
    assert report.detail == "no repository state has been collected; run metrics collect"


def test_stored_sonar_separates_state_collected_before_the_source_existed(tmp_path: Path) -> None:
    """Distinguish a row stored without the source from a repository no project is mapped to.

    A row written by a build predating this source carries neither field, and reading that as "no
    SonarCloud project" would answer a question nobody asked: the first is a gap `metrics collect`
    closes, and the second is a settled answer.
    """
    settings = configuration(tmp_path)
    record_repository_state(settings.database, collected_state(merge_gate()))

    report = stored_sonar(stored_repository_state(settings, "cath-service"))

    assert report.measures is None
    assert report.mapping is None
    assert report.fetched_at == STANDARDS_FETCHED_AT
    assert (
        report.detail == "SonarCloud measures were not collected when repository state was stored; run metrics collect"
    )


def test_stored_sonar_reports_a_repository_no_project_is_mapped_to(tmp_path: Path) -> None:
    """Report the resolution's own reason, because having no project IS the answer for most repositories."""
    settings = configuration(tmp_path)
    reason = "no SonarCloud project in hmcts is mapped to this repository"
    record_repository_state(
        settings.database,
        collected_state(merge_gate(), sonar_project=SonarProjectResolution(detail=reason)),
    )

    report = stored_sonar(stored_repository_state(settings, "cath-service"))

    assert report.measures is None
    assert report.mapping is None
    assert report.fetched_at == STANDARDS_FETCHED_AT
    assert report.detail == reason


def test_stored_sonar_names_the_project_a_failed_measurement_was_about(tmp_path: Path) -> None:
    """Report a resolved project beside the reason its measures are missing, rather than withholding it."""
    settings = configuration(tmp_path)
    resolution = collected_project().model_copy(update={"detail": "SonarCloud request failed"})
    record_repository_state(settings.database, collected_state(merge_gate(), sonar_project=resolution))

    report = stored_sonar(stored_repository_state(settings, "cath-service"))

    assert report.measures is None
    assert report.mapping == collected_project().mapping
    assert report.fetched_at == STANDARDS_FETCHED_AT
    assert report.detail == "SonarCloud request failed"


def test_stored_sonar_preserves_storage_failure(tmp_path: Path) -> None:
    """Describe an unreadable state table instead of raising through the report."""
    with patch("metrics.evidence.load_repository_state", side_effect=StorageError("cache unreadable")):
        report = stored_sonar(stored_repository_state(configuration(tmp_path), "cath-service"))

    assert report == SonarReport(detail="cache unreadable")


def test_repository_practice_evidence_grades_nothing_from_sonar_evidence() -> None:
    """Leave the readiness label identical with and without a FAILING quality gate in evidence.

    The sharpest case of the standing rule that a signal becoming visible is not a reason to grade
    it: a SonarCloud gate is somebody else's threshold, set per project, and letting it move the
    label would import a judgment this tool did not make.
    """
    uncollected = uncollected_reports()
    failing = replace(
        uncollected,
        sonar=SonarReport(
            fetched_at=STANDARDS_FETCHED_AT,
            mapping=collected_project().mapping,
            measures=collected_measures(),
        ),
    )

    without = cached_facts().practices(
        (unreviewed_merge(),),
        default_policy(),
        uncollected,
        "civil",
        all_metrics(),
    )
    with_sonar = cached_facts().practices(
        (unreviewed_merge(),),
        default_policy(),
        failing,
        "civil",
        all_metrics(),
    )

    assert without.assessment is not None
    assert with_sonar.assessment == without.assessment
    assert with_sonar.sonar.measures == collected_measures()
    assert without.sonar == uncollected_sonar()


@pytest.mark.parametrize(
    ("offset", "committed_within_six_months"),
    [
        # The window is half-open [fetched_at - days, fetched_at): its start instant is inside.
        (timedelta(0), True),
        (timedelta(seconds=1), True),
        (timedelta(seconds=-1), False),
    ],
)
def test_maintenance_windows_decide_committed_at_the_exact_cutoff(
    offset: timedelta,
    committed_within_six_months: bool,  # noqa: FBT001 - parametrised expectation
) -> None:
    """Hold the 183-day boundary exactly: the cutoff instant itself is inside the window."""
    committed_at = STANDARDS_FETCHED_AT - timedelta(days=183) + offset

    windows = maintenance_windows(maintained(committed_at, last_human_commit_at=committed_at), STANDARDS_FETCHED_AT)

    assert windows[0].committed_within is committed_within_six_months
    assert windows[0].human_committed_within is committed_within_six_months
    # A commit a second past the 6-month cutoff is still comfortably inside 12 and 24 months.
    assert windows[1].committed_within is True
    assert windows[2].committed_within is True


def test_maintenance_windows_grade_a_found_human_commit_per_window() -> None:
    """Decide every window both ways from a found human commit: no bound, so nothing is unknown."""
    evidence = maintained(
        STANDARDS_FETCHED_AT - timedelta(days=1),
        last_human_commit_at=STANDARDS_FETCHED_AT - timedelta(days=200),
    )

    windows = maintenance_windows(evidence, STANDARDS_FETCHED_AT)

    assert tuple(row.committed_within for row in windows) == (True, True, True)
    assert tuple(row.human_committed_within for row in windows) == (False, True, True)
    assert all(row.human_detail is None for row in windows)


def test_maintenance_windows_decide_false_where_the_search_reached_the_cutoff() -> None:
    """Answer False for every window the search fully examined without finding a human commit."""
    evidence = maintained(
        STANDARDS_FETCHED_AT - timedelta(days=1),
        searched_back_to=STANDARDS_FETCHED_AT - timedelta(days=730),
    )

    windows = maintenance_windows(evidence, STANDARDS_FETCHED_AT)

    assert tuple(row.committed_within for row in windows) == (True, True, True)
    assert tuple(row.human_committed_within for row in windows) == (False, False, False)
    assert all(row.human_detail is None for row in windows)


def test_maintenance_windows_report_unknown_where_the_page_cap_stopped_the_search() -> None:
    """Keep "none found" apart from "not examined": a capped search decides only the windows it covered."""
    evidence = maintained(
        STANDARDS_FETCHED_AT - timedelta(days=1),
        searched_back_to=STANDARDS_FETCHED_AT - timedelta(days=300),
    )

    windows = maintenance_windows(evidence, STANDARDS_FETCHED_AT)

    assert windows[0].human_committed_within is False
    assert windows[0].human_detail is None
    assert windows[1].human_committed_within is None
    assert windows[2].human_committed_within is None
    assert (
        windows[1].human_detail == "the bounded search examined commits no older than 2025-10-12T09:00Z, "
        "which does not reach this window's cutoff"
    )


def test_maintenance_windows_date_nothing_on_an_empty_branch() -> None:
    """Decide every answer False on a branch with no commits: nothing exists to have been found."""
    windows = maintenance_windows(maintained(None), STANDARDS_FETCHED_AT)

    assert tuple(row.committed_within for row in windows) == (False, False, False)
    assert tuple(row.human_committed_within for row in windows) == (False, False, False)
    assert all(row.human_detail is None for row in windows)


def open_pull_request_data(
    opened: int = 0,
    closed_without_merge: int = 0,
    currently_open: int = 0,
    stale_open: int = 0,
) -> dict[str, object]:
    """Build one bundled open-pull-request GraphQL response."""
    return {
        "openedInWindow": {"issueCount": opened},
        "closedWithoutMerge": {"issueCount": closed_without_merge},
        "currentlyOpen": {"issueCount": currently_open},
        "staleOpen": {"issueCount": stale_open},
    }


def test_open_pull_request_report_observes_the_four_counts_fresh(tmp_path: Path) -> None:
    """Report the counts, the instant they were observed, and the window two of them cover."""
    client = MagicMock(spec=GitHubClient)
    client.graphql.return_value = open_pull_request_data(5, 1, 3, 2)
    reference = datetime(2026, 8, 8, 12, tzinfo=UTC)
    requested = window(7)

    report = open_pull_request_report(client, configuration(tmp_path), "cath-service", requested, reference)

    assert report.summary == OpenPullRequestSummary(
        opened_in_window=5,
        closed_without_merge=1,
        currently_open=3,
        stale_open=2,
    )
    assert report.fetched_at == reference
    # The reporting window here, as the collection window is on the stored path: a refreshed report
    # must be readable exactly like a stored one.
    assert report.starts_at == requested.starts_at
    assert report.ends_at == requested.ends_at
    assert report.detail is None


def test_open_pull_request_report_measures_staleness_from_the_configured_threshold(tmp_path: Path) -> None:
    """Carry `lookback.stale_open_days` all the way into the query it is supposed to bound.

    The key is loaded and validated elsewhere, and the collector is tested with the value handed to
    it directly, so nothing else stops this call passing the default: a user who set 30 days would
    read a "stale open" count still measured over 14 and never see a discrepancy.
    """
    client = MagicMock(spec=GitHubClient)
    client.graphql.return_value = open_pull_request_data(0, 0, 0, 0)
    settings = configuration(tmp_path).model_copy(
        update={"lookback": LookbackConfiguration(stale_open_days=30)},
    )

    open_pull_request_report(client, settings, "cath-service", window(7), datetime(2026, 8, 8, 12, tzinfo=UTC))

    assert client.graphql.call_args.args[1]["staleOpenQuery"] == (
        "repo:hmcts/cath-service is:pr is:open updated:<2026-07-09T12:00:00Z"
    )


def test_open_pull_request_report_preserves_a_github_failure(tmp_path: Path) -> None:
    """Report the reason a fetch failed rather than a zeroed or remembered count."""
    client = MagicMock(spec=GitHubClient)
    client.graphql.side_effect = GitHubError("GitHub permission denied", AvailabilityReason.PERMISSION_DENIED)

    report = open_pull_request_report(
        client,
        configuration(tmp_path),
        "cath-service",
        window(7),
        datetime(2026, 8, 8, tzinfo=UTC),
    )

    assert report.summary is None
    assert report.fetched_at is None
    assert report.starts_at is None
    assert report.detail == "GitHub permission denied"


def collected_open_pull_requests() -> OpenPullRequestSnapshot:
    """Build the open pull-request state a collection would have stored, with its own window."""
    collected_at = datetime(2026, 8, 8, 9, tzinfo=UTC)
    return OpenPullRequestSnapshot(
        starts_at=collected_at - timedelta(days=7),
        ends_at=collected_at,
        summary=OpenPullRequestSummary(
            opened_in_window=5,
            closed_without_merge=1,
            currently_open=3,
            stale_open=2,
        ),
    )


def test_stored_open_pull_requests_report_the_counts_with_the_window_they_cover(tmp_path: Path) -> None:
    """Serve the stored state with the collection window two of its four counts were measured over."""
    settings = configuration(tmp_path)
    snapshot = collected_open_pull_requests()
    record_repository_state(settings.database, collected_state(merge_gate(), open_pull_requests=snapshot))

    report = stored_open_pull_requests(stored_repository_state(settings, "cath-service"))

    assert report.summary == snapshot.summary
    assert report.fetched_at == STANDARDS_FETCHED_AT
    # The window of the collection that observed it, which is not the window of the report around it.
    assert report.starts_at == snapshot.starts_at
    assert report.ends_at == snapshot.ends_at
    assert report.detail is None


def test_stored_open_pull_requests_report_a_repository_that_was_never_collected(tmp_path: Path) -> None:
    """State that nothing was collected rather than reporting four counts nobody observed."""
    report = stored_open_pull_requests(stored_repository_state(configuration(tmp_path), "cath-service"))

    assert report.summary is None
    assert report.fetched_at is None
    assert report.detail == "no repository state has been collected; run metrics collect"


def test_stored_open_pull_requests_separate_state_stored_before_they_were_collected(tmp_path: Path) -> None:
    """Distinguish a row stored without the counts from a repository that was never collected.

    Reporting a pre-2026-09-01 row as four zeros would invent a repository with nothing open, which
    is the one reading this block has always been forbidden.
    """
    settings = configuration(tmp_path)
    record_repository_state(settings.database, collected_state(merge_gate()))

    report = stored_open_pull_requests(stored_repository_state(settings, "cath-service"))

    assert report.summary is None
    assert report.fetched_at == STANDARDS_FETCHED_AT
    assert report.detail == (
        "open pull-request state was not collected when repository state was stored; run metrics collect"
    )


def test_stored_open_pull_requests_preserve_storage_failure(tmp_path: Path) -> None:
    """Describe an unreadable state table instead of raising through the report."""
    with patch("metrics.evidence.load_repository_state", side_effect=StorageError("cache unreadable")):
        report = stored_open_pull_requests(stored_repository_state(configuration(tmp_path), "cath-service"))

    assert report == OpenPullRequestReport(detail="cache unreadable")


def test_stored_reports_project_the_open_pull_request_block_from_the_same_row(tmp_path: Path) -> None:
    """Carry the block into `StoredReports` beside the five it joined, off one load of the row."""
    settings = configuration(tmp_path)
    snapshot = collected_open_pull_requests()
    record_repository_state(settings.database, collected_state(merge_gate(), open_pull_requests=snapshot))

    reports = stored_reports(stored_repository_state(settings, "cath-service"))

    assert reports.open_pull_requests.summary == snapshot.summary
    assert reports.open_pull_requests.fetched_at == reports.merge_gate.fetched_at


def test_repository_practice_evidence_reads_open_pull_requests_from_the_stored_blocks() -> None:
    """Take the block from `current_state` rather than from a parameter of its own.

    It was the last block passed separately, which let a call site pair one repository's counts with
    another's gate — the failure the single `StoredReports` argument exists to make impossible.
    """
    stored = replace(
        uncollected_reports(),
        open_pull_requests=OpenPullRequestReport(
            fetched_at=STANDARDS_FETCHED_AT,
            starts_at=collected_open_pull_requests().starts_at,
            ends_at=collected_open_pull_requests().ends_at,
            summary=collected_open_pull_requests().summary,
        ),
    )

    report = cached_facts().practices((unreviewed_merge(),), default_policy(), stored, "civil", all_metrics())

    assert report.open_pull_requests == stored.open_pull_requests


def pull_request_coverage(requested: ReportingWindow) -> SourceCoverage:
    """Describe complete pull-request coverage of one window."""
    return SourceCoverage(
        organization="hmcts",
        repository="cath-service",
        source=EvidenceSource.PULL_REQUEST,
        query_hash=query_signature(),
        starts_at=requested.starts_at,
        ends_at=requested.ends_at,
    )


def commit_coverage(requested: ReportingWindow) -> SourceCoverage:
    """Describe complete direct-commit coverage of one window."""
    return pull_request_coverage(requested).model_copy(
        update={"source": EvidenceSource.COMMIT, "query_hash": commit_query_signature()},
    )


def test_cached_repository_evidence_refuses_an_uncovered_window(tmp_path: Path) -> None:
    """Refuse rather than silently report a window the cache does not cover."""
    unavailable = cached_repository_evidence(configuration(tmp_path), "cath-service", window(7))

    assert isinstance(unavailable, EvidenceUnavailable)
    assert "cached pull_request evidence does not cover" in unavailable.detail


def test_cached_repository_evidence_reports_a_covered_window(tmp_path: Path) -> None:
    """Report from the cache alone once the window is fully covered."""
    settings = configuration(tmp_path)
    requested = window(7)
    fact = cached_facts().pull_requests[1].model_copy(update={"merged_at": requested.starts_at + timedelta(days=1)})
    cache_pull_request_facts(
        settings.database,
        pull_request_coverage(requested),
        (fact,),
        complete=True,
    )
    cache_direct_commit_facts(
        settings.database,
        commit_coverage(requested),
        (direct_commit("aaa", committed_at=requested.starts_at + timedelta(days=2)),),
        complete=True,
    )

    evidence = cached_repository_evidence(settings, "cath-service", requested)

    assert isinstance(evidence, RepositoryEvidence)
    assert evidence.provenance == WindowProvenance(offline=True, intervals_fetched=0)
    assert tuple(item.number for item in evidence.pull_requests) == (12,)
    assert tuple(item.sha for item in evidence.direct_commits) == ("aaa",)
    assert evidence.cohort.direct_commits == 1


def test_cached_repository_evidence_refuses_a_window_missing_only_direct_commits(tmp_path: Path) -> None:
    """Refuse a window whose pull requests are cached but whose bypasses were never collected.

    Reporting it would understate every governance denominator by exactly the commits it could not
    see, which is the failure this iteration exists to remove.
    """
    settings = configuration(tmp_path)
    requested = window(7)
    cache_pull_request_facts(settings.database, pull_request_coverage(requested), (), complete=True)

    unavailable = cached_repository_evidence(settings, "cath-service", requested)

    assert isinstance(unavailable, EvidenceUnavailable)
    assert "cached commit evidence does not cover" in unavailable.detail


def test_cached_repository_evidence_preserves_storage_failure(tmp_path: Path) -> None:
    """Describe an unreadable cache instead of raising through the report."""
    with patch("metrics.evidence.find_missing_cached_coverage", side_effect=StorageError("cache unreadable")):
        unavailable = cached_repository_evidence(configuration(tmp_path), "cath-service", window(7))

    assert unavailable == EvidenceUnavailable(repository="cath-service", detail="cache unreadable")


def test_collected_repository_evidence_records_fetched_intervals(tmp_path: Path) -> None:
    """Record how many intervals a report had to collect before answering."""
    requested = window(7)
    changes = Merges(pull_requests=(cached_facts().pull_requests[0],), direct_commits=(direct_commit("aaa"),))
    provenance = BehaviourProvenance(
        stable_history=CacheStatus.PARTIALLY_REUSED,
        stable_intervals_fetched=2,
        mutable_starts_at=requested.ends_at,
        pull_request_facts_loaded=1,
        review_facts_loaded=1,
        direct_commit_facts_loaded=1,
        direct_commit_intervals_fetched=1,
    )
    with patch("metrics.evidence.synchronize_merges", return_value=(changes, provenance)) as synchronize:
        evidence = collected_repository_evidence(
            configuration(tmp_path),
            cast("GitHubClient", MagicMock()),
            "cath-service",
            requested,
            requested.ends_at,
        )

    assert isinstance(evidence, RepositoryEvidence)
    assert evidence.pull_requests == changes.pull_requests
    assert evidence.direct_commits == changes.direct_commits
    # Both sources are counted, so the figure means intervals fetched rather than intervals of one kind.
    assert evidence.provenance == WindowProvenance(offline=False, intervals_fetched=3)
    synchronize.assert_called_once()


@pytest.mark.parametrize(
    "failure",
    [
        GitHubError("permission denied", reason=AvailabilityReason.PERMISSION_DENIED),
        StorageError("cache unreadable"),
    ],
)
def test_collected_repository_evidence_preserves_collection_failure(tmp_path: Path, failure: Exception) -> None:
    """Describe a failed collection as unavailable evidence for that repository."""
    requested = window(7)
    with patch("metrics.evidence.synchronize_merges", side_effect=failure):
        unavailable = collected_repository_evidence(
            configuration(tmp_path),
            cast("GitHubClient", MagicMock()),
            "cath-service",
            requested,
            requested.ends_at,
        )

    assert isinstance(unavailable, EvidenceUnavailable)
    assert unavailable.repository == "cath-service"


def test_unreviewed_merge_counts_a_direct_commit_as_an_unreviewed_change() -> None:
    """Attribute a bypass to its author exactly as an unreviewed merge is attributed to its author."""
    cached = cached_facts()
    pushed = (
        direct_commit("aaa", "second-author", additions=1, deletions=0, changed_files=1),
        direct_commit("bbb", "second-author", additions=80, deletions=20, changed_files=4),
    )

    findings = unreviewed_merge().findings(cached.model_copy(update={"direct_commits": pushed}))

    assert len(findings) == 1
    assert findings[0].actor_login == "second-author"
    # One unreviewed merge and two commits that were never open to review, over three merges.
    assert findings[0].occurrences == 3
    assert findings[0].authored_merges == 3
    assert findings[0].occurrences_by_size == {"substantial": 1, "trivial": 1, "unsized": 1}
    assert findings[0].message == (
        "second-author: 3 of 3 merges had no independent human review (100%) — "
        "1 substantial, 1 trivial, 1 unsized; 2 pushed directly to the default branch"
    )
    assert findings[0].direct_commits is not None
    assert tuple(reference.sha for reference in findings[0].direct_commits) == ("aaa", "bbb")
    assert findings[0].direct_commits[1].url == "https://github.com/hmcts/cath-service/commit/bbb"
    assert findings[0].direct_commits[1].changed_lines == 100
    assert findings[0].direct_commits[0].size_class == "trivial"


def test_unreviewed_merge_ignores_a_direct_commit_outside_the_window() -> None:
    """Keep the finding cohort inside the reported window, by either route onto the branch."""
    cached = cached_facts()
    outside = direct_commit("aaa", "second-author", committed_at=cached.ends_at)

    findings = unreviewed_merge().findings(cached.model_copy(update={"direct_commits": (outside,)}))

    assert tuple(finding.direct_commits for finding in findings) == (None,)


def test_metric_drill_down_names_direct_commits_only_where_they_are_counted() -> None:
    """Name the bypasses behind a governance rate, and leave them out of a flow distribution."""
    cached = cached_facts().model_copy(update={"direct_commits": (direct_commit("aaa"),)})

    coverage = cached.metric(IndependentReviewCoverage(), include_identities=True)
    cycle = cached.metric(MergeCycleTime(), include_identities=True)

    assert isinstance(coverage.summary, RateObservation)
    assert (coverage.summary.numerator, coverage.summary.denominator) == (1, 3)
    assert coverage.classifications == {"direct-commit": 1, "included": 1, "no-review-events": 1}
    assert coverage.direct_commits is not None
    assert coverage.direct_commits[0].url == "https://github.com/hmcts/cath-service/commit/aaa"
    assert coverage.direct_commits[0].classification == "direct-commit"
    assert isinstance(cycle.summary, DistributionObservation)
    assert cycle.summary.sample_size == 2
    assert cycle.direct_commits is None


def authored_evidence(
    repository: str,
    pull_requests: Sequence[str] = (),
    direct_commits: Sequence[str] = (),
) -> RepositoryEvidence:
    """Build one repository's cached facts from the logins that authored its merges.

    The cohort is accounted the way `cohort_summary` accounts it — `merged` and `reported` over pull
    requests, direct commits counted apart — so a fixture cannot teach a shape production never
    produces.
    """
    starts_at = datetime(2026, 7, 1, tzinfo=UTC)
    return RepositoryEvidence(
        organization="hmcts",
        repository=repository,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(days=30),
        provenance=WindowProvenance(offline=True, intervals_fetched=0),
        cohort=CohortSummary(
            merged=len(pull_requests),
            reported=len(pull_requests),
            excluded_authors={},
            direct_commits=len(direct_commits),
        ),
        pull_requests=tuple(
            PullRequestFact(
                identifier=index + 1,
                repository=repository,
                number=index + 1,
                created_at=starts_at,
                merged_at=starts_at + timedelta(days=1),
                draft=False,
                author_login=login,
                author_type="Bot" if login.casefold().endswith("[bot]") else "User",
                reviews=(),
            )
            for index, login in enumerate(pull_requests)
        ),
        direct_commits=tuple(
            direct_commit(f"{repository}-{index}", author_login=login) for index, login in enumerate(direct_commits)
        ),
    )


def assessed_practices(
    repository: str,
    label: ReadinessLabel | None = ReadinessLabel.RED,
    behaviour: tuple[PracticeFinding, ...] = (),
) -> RepositoryPracticeEvidence:
    """Build one repository's practice evidence carrying the given label and findings."""
    starts_at = datetime(2026, 7, 1, tzinfo=UTC)
    return RepositoryPracticeEvidence(
        repository=repository,
        team="civil",
        starts_at=starts_at,
        ends_at=starts_at + timedelta(days=30),
        provenance=WindowProvenance(offline=True, intervals_fetched=0),
        cohort=CohortSummary(merged=0, reported=0, excluded_authors={}),
        assessment=None if label is None else ReadinessAssessment(label=label, blocking=(), caution=(), clear=()),
        merge_gate=uncollected_gate(),
        open_pull_requests=uncollected_open_pull_requests(),
        security=uncollected_security(),
        codeowners=uncollected_codeowners(),
        maintenance=uncollected_maintenance(),
        sonar=uncollected_sonar(),
        metrics=(),
        behaviour=behaviour,
    )


def blocking_finding(actor_login: str, occurrences: int, rule: str = "unreviewed-merge") -> PracticeFinding:
    """Build one actor-level finding carrying the given number of occurrences."""
    return PracticeFinding(
        rule=rule,
        severity=FindingSeverity.MEDIUM,
        actor_login=actor_login,
        occurrences=occurrences,
        authored_merges=occurrences,
        percentage=100.0,
        message=f"{actor_login}: {occurrences} of {occurrences} merges had no independent human review (100%)",
        occurrences_by_size={"unsized": occurrences},
        pull_requests=(),
    )


def test_actor_contributions_counts_both_routes_onto_the_default_branch() -> None:
    """Count a person's pull requests and direct pushes as the contributions they both are."""
    evidence = authored_evidence("cath-service", pull_requests=("alice", "bob"), direct_commits=("alice",))

    assert actor_contributions(evidence) == Counter({"alice": 2, "bob": 1})


def test_actor_contributions_skip_unattributed_changes() -> None:
    """Attribute nothing to a commit GitHub could match to no account, rather than to an empty login."""
    evidence = authored_evidence("cath-service", pull_requests=("alice",))
    unattributed = (direct_commit("ccc").model_copy(update={"author_login": None}),)

    assert actor_contributions(evidence.model_copy(update={"direct_commits": unattributed})) == Counter({"alice": 1})


def test_actor_readiness_orders_repositories_by_contributions_then_by_name() -> None:
    """Lead with the repository a person works in most, and settle a tie by repository name."""
    busiest = authored_evidence("project-x", pull_requests=("alice", "alice", "alice"))
    tied = authored_evidence("project-u", pull_requests=("alice",))
    also_tied = authored_evidence("project-b", pull_requests=("alice",))

    actors = actor_readiness(
        (
            (tied, assessed_practices("project-u", ReadinessLabel.AMBER)),
            (busiest, assessed_practices("project-x")),
            (also_tied, assessed_practices("project-b", ReadinessLabel.GREEN)),
        ),
        all_metrics(),
    )

    assert len(actors) == 1
    assert tuple((row.repository, row.contributions) for row in actors[0].repositories) == (
        ("project-x", 3),
        ("project-b", 1),
        ("project-u", 1),
    )
    assert tuple(row.readiness for row in actors[0].repositories) == (
        ReadinessLabel.RED,
        ReadinessLabel.GREEN,
        ReadinessLabel.AMBER,
    )


def test_actor_readiness_lists_actors_alphabetically() -> None:
    """Order the section by login, so a reader can find a person without reading every line."""
    evidence = authored_evidence("cath-service", pull_requests=("carol", "alice", "Bob"))

    actors = actor_readiness(((evidence, assessed_practices("cath-service")),), all_metrics())

    assert tuple(actor.actor_login for actor in actors) == ("alice", "Bob", "carol")


def test_actor_readiness_merges_two_spellings_of_one_login() -> None:
    """Treat `Alice` and `alice` as the one person GitHub says they are, spelled as the biggest repository spells it."""
    busiest = authored_evidence("project-x", pull_requests=("Alice", "Alice"))
    smaller = authored_evidence("project-z", pull_requests=("alice",))

    actors = actor_readiness(
        ((smaller, assessed_practices("project-z", ReadinessLabel.GREEN)), (busiest, assessed_practices("project-x"))),
        all_metrics(),
    )

    assert len(actors) == 1
    assert actors[0].actor_login == "Alice"
    assert tuple((row.repository, row.contributions) for row in actors[0].repositories) == (
        ("project-x", 2),
        ("project-z", 1),
    )


def test_actor_readiness_carries_no_label_for_an_unassessed_repository() -> None:
    """Leave the label out where the readiness policy graded nothing, rather than inventing one."""
    evidence = authored_evidence("cath-service", pull_requests=("alice",))

    actors = actor_readiness(((evidence, assessed_practices("cath-service", label=None)),), all_metrics())

    assert actors[0].repositories[0].readiness is None


def test_actor_readiness_reports_an_actor_present_in_one_repository_only() -> None:
    """List each person against the repositories they contributed to and no others."""
    first = authored_evidence("project-x", pull_requests=("alice", "bob"))
    second = authored_evidence("project-z", direct_commits=("bob",))

    actors = actor_readiness(
        ((first, assessed_practices("project-x")), (second, assessed_practices("project-z", ReadinessLabel.GREEN))),
        all_metrics(),
    )

    assert {actor.actor_login: [row.repository for row in actor.repositories] for actor in actors} == {
        "alice": ["project-x"],
        "bob": ["project-x", "project-z"],
    }


def test_actor_readiness_sums_blocking_occurrences_across_findings() -> None:
    """Count occurrences rather than findings, so twelve unreviewed merges count as twelve."""
    evidence = authored_evidence("cath-service", pull_requests=("Alice", "alice", "bob"))
    practices = assessed_practices(
        "cath-service",
        behaviour=(
            blocking_finding("alice", 4),
            blocking_finding("Alice", 8, rule="undersized-review"),
        ),
    )

    actors = actor_readiness(((evidence, practices),), all_metrics())

    assert {actor.actor_login: actor.repositories[0].blocking for actor in actors} == {"Alice": 12, "bob": 0}


def test_actor_readiness_counts_blocking_against_the_repository_that_reported_it() -> None:
    """Keep one repository's findings out of another's row, so nobody is blamed where they blocked nothing."""
    blocked = authored_evidence("project-x", pull_requests=("alice", "alice"))
    clean = authored_evidence("project-z", pull_requests=("alice",))

    actors = actor_readiness(
        (
            (blocked, assessed_practices("project-x", behaviour=(blocking_finding("alice", 12),))),
            (clean, assessed_practices("project-z", ReadinessLabel.GREEN)),
        ),
        all_metrics(),
    )

    assert tuple((row.repository, row.blocking) for row in actors[0].repositories) == (
        ("project-x", 12),
        ("project-z", 0),
    )


def test_actor_readiness_leaves_out_a_bot_that_merged_into_the_cohort() -> None:
    """Keep an agent's merges in the cohort while giving the agent no actor line to review."""
    evidence = authored_evidence(
        "cath-service",
        pull_requests=("alice", "copilot-swe-agent[bot]"),
        direct_commits=("release-bot[bot]",),
    )
    practices = assessed_practices("cath-service", behaviour=(blocking_finding("copilot-swe-agent[bot]", 3),))

    actors = actor_readiness(((evidence, practices),), all_metrics())

    assert tuple(actor.actor_login for actor in actors) == ("alice",)
    # No bot is measured either: the section lists nobody for an agent, so there is no row of its
    # merges to read, however many of them the cohort's own denominators still count.
    assert {actor.actor_login for actor in actors if actor.repositories[0].metrics} == {"alice"}
    # Measured rather than restated from the fixture: the merges the section leaves the bots out of
    # are the same ones the cohort counts, because the two exclusions answer different questions.
    assert cohort_summary(evidence, excluded=()) == CohortSummary(
        merged=2,
        reported=2,
        excluded_authors={},
        direct_commits=1,
    )


def test_actor_readiness_measures_each_person_over_their_own_merges_in_each_repository() -> None:
    """Summarise one person's merges in one repository, never the repository's whole cohort.

    Alice merged one of the two pull requests this repository reports, so her coverage rate is
    measured over one merge and not over both — and it is measured again, separately, in the second
    repository rather than being combined with the first.
    """
    busiest = authored_evidence("project-x", pull_requests=("alice", "bob"), direct_commits=("alice",))
    smaller = authored_evidence("project-z", pull_requests=("alice",))

    actors = actor_readiness(
        ((busiest, assessed_practices("project-x")), (smaller, assessed_practices("project-z"))),
        all_metrics(),
    )

    alice = next(actor for actor in actors if actor.actor_login == "alice")
    coverage = {
        row.repository: next(
            summary.summary for summary in row.metrics if summary.metric == "independent-review-coverage"
        )
        for row in alice.repositories
    }
    assert [
        observation.denominator for observation in coverage.values() if isinstance(observation, RateObservation)
    ] == [
        2,
        1,
    ]
    assert [row.repository for row in alice.repositories] == ["project-x", "project-z"]


def cached_window(settings: Configuration, requested: ReportingWindow) -> None:
    """Cache one merged pull request and one direct commit covering the whole of one window."""
    fact = cached_facts().pull_requests[1].model_copy(update={"merged_at": requested.starts_at + timedelta(days=1)})
    cache_pull_request_facts(settings.database, pull_request_coverage(requested), (fact,), complete=True)
    cache_direct_commit_facts(
        settings.database,
        commit_coverage(requested),
        (direct_commit("aaa", committed_at=requested.starts_at + timedelta(days=2)),),
        complete=True,
    )


def test_offline_practice_report_assembles_the_whole_report_from_the_caches(tmp_path: Path) -> None:
    """Report every configured repository, its team, its metrics and its actors, contacting nothing."""
    settings = configuration(tmp_path)
    requested = window(7)
    cached_window(settings, requested)

    report = offline_practice_report(settings, requested)

    assert report.organization == "hmcts"
    assert report.unavailable == ()
    assert [block.repository for block in report.repositories] == ["cath-service"]
    assert report.repositories[0].team == "civil"
    assert [summary.metric for summary in report.repositories[0].metrics] == [
        metric.identifier for metric in all_metrics()
    ]
    # The stored current state is read for each repository the cache answered for, and there is none.
    assert report.repositories[0].merge_gate == uncollected_gate()
    assert {actor.actor_login for actor in report.actors} == {"second-author", "pusher"}


def test_offline_practice_report_reports_an_uncovered_repository_as_unavailable(tmp_path: Path) -> None:
    """Name the repository the cache cannot answer for rather than reporting a thinner window."""
    report = offline_practice_report(configuration(tmp_path), window(7))

    assert report.repositories == ()
    assert report.actors == ()
    assert [item.repository for item in report.unavailable] == ["cath-service"]
    assert "cached pull_request evidence does not cover" in report.unavailable[0].detail
