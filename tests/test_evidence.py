"""Test behaviour evidence projections and window loading."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

import pytest

from metrics.assessment import ReadinessPolicy
from metrics.behaviour import commit_query_signature, query_signature
from metrics.behaviour_metrics.approval_coverage import ApprovalCoverage
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
    MaintenanceEvidence,
    MaintenanceReport,
    MergeGateEvidence,
    MergeGateReport,
    Merges,
    OpenAlertCount,
    OpenPullRequestSummary,
    PullRequestFact,
    PullRequestRule,
    RateObservation,
    ReportingWindow,
    RepositoryInventory,
    RepositoryInventoryItem,
    RepositoryMetadata,
    ReviewFact,
    ReviewState,
    SecurityAlertEvidence,
    SecurityAlertReport,
    SourceCoverage,
    WindowProvenance,
)
from metrics.evidence import (
    RepositoryEvidence,
    cached_repository_evidence,
    collected_repository_evidence,
    maintenance_windows,
    offline_open_pull_request_report,
    open_pull_request_report,
    repository_evidence,
    stored_codeowners,
    stored_maintenance,
    stored_merge_gate,
    stored_repository_state,
    stored_security_alerts,
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


def uncollected_security() -> SecurityAlertReport:
    """Build the security report of a repository whose state has never been collected."""
    return SecurityAlertReport(detail="no repository state has been collected; run metrics collect")


def uncollected_codeowners() -> CodeownersReport:
    """Build the CODEOWNERS report of a repository whose state has never been collected."""
    return CodeownersReport(detail="no repository state has been collected; run metrics collect")


def uncollected_maintenance() -> MaintenanceReport:
    """Build the maintenance report of a repository whose state has never been collected."""
    return MaintenanceReport(detail="no repository state has been collected; run metrics collect")


def default_policy() -> ReadinessPolicy:
    """Build the readiness policy with its default thresholds."""
    return ReadinessPolicy(AssessmentConfiguration(), TrivialityConfiguration())


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
        uncollected_gate(),
        default_policy(),
        offline_open_pull_request_report(),
        uncollected_security(),
        uncollected_codeowners(),
        uncollected_maintenance(),
    )

    assert excluded.behaviour == ()


def test_repository_practice_evidence_collates_every_enabled_rule() -> None:
    """Flatten findings from enabled rule instances and skip disabled instances."""
    configuration = PracticeRuleConfiguration()
    gate = uncollected_gate()
    report = cached_facts().practices(
        (
            unreviewed_merge(configuration),
            unreviewed_merge(configuration),
            unreviewed_merge(PracticeRuleConfiguration(enabled=False)),
        ),
        gate,
        default_policy(),
        offline_open_pull_request_report(),
        uncollected_security(),
        uncollected_codeowners(),
        uncollected_maintenance(),
    )

    assert len(report.behaviour) == 2
    assert report.merge_gate == gate


def test_repository_practice_evidence_omits_a_disabled_assessment() -> None:
    """Leave the assessment out entirely rather than emitting an unjudged label."""
    report = cached_facts().practices(
        (unreviewed_merge(),),
        uncollected_gate(),
        ReadinessPolicy(AssessmentConfiguration(enabled=False), TrivialityConfiguration()),
        offline_open_pull_request_report(),
        uncollected_security(),
        uncollected_codeowners(),
        uncollected_maintenance(),
    )

    assert report.assessment is None
    assert report.behaviour


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


def test_open_pull_request_report_fetches_the_four_counts_fresh(tmp_path: Path) -> None:
    """Report the counts and the instant they were fetched, never a cached or remembered figure."""
    client = MagicMock(spec=GitHubClient)
    client.graphql.return_value = open_pull_request_data(5, 1, 3, 2)
    reference = datetime(2026, 8, 8, 12, tzinfo=UTC)

    report = open_pull_request_report(client, configuration(tmp_path), "cath-service", window(7), reference)

    assert report.summary == OpenPullRequestSummary(
        opened_in_window=5,
        closed_without_merge=1,
        currently_open=3,
        stale_open=2,
    )
    assert report.fetched_at == reference
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
    assert report.detail == "GitHub permission denied"


def test_offline_open_pull_request_report_refuses_to_answer() -> None:
    """State that offline evidence never observes this never-cached state."""
    report = offline_open_pull_request_report()

    assert report.summary is None
    assert report.detail == "open pull-request state is never cached; omit --offline to observe it"


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
