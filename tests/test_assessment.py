"""Test the readiness assessment's labels and the conditions reported in each of its sections."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from metrics.assessment import ReadinessPolicy, readiness_policy
from metrics.behaviour_metrics.approval_coverage import ApprovalCoverage
from metrics.behaviour_metrics.checks_passing_at_merge import ChecksPassingAtMerge
from metrics.behaviour_metrics.independent_review_coverage import IndependentReviewCoverage
from metrics.behaviour_metrics.merge_cycle_time import MergeCycleTime
from metrics.behaviour_metrics.pull_request_size import PullRequestSize
from metrics.behaviour_metrics.review_depth import ReviewDepth
from metrics.behaviour_metrics.time_to_first_review import TimeToFirstReview
from metrics.config import (
    AssessmentConfiguration,
    Configuration,
    DistributionThreshold,
    ReadinessThresholds,
    TrivialityConfiguration,
)
from metrics.domain import (
    CachedBehaviourFacts,
    CheckConclusion,
    CheckFact,
    DirectCommitFact,
    DistributionObservation,
    MergeGateEvidence,
    MergeGateReport,
    ObservationStatus,
    PullRequestFact,
    PullRequestRule,
    RateObservation,
    ReadinessAssessment,
    ReadinessLabel,
    ReviewFact,
    ReviewState,
    StatusCheck,
    StatusChecksRule,
)


def policy(configuration: AssessmentConfiguration | None = None) -> ReadinessPolicy:
    """Build the readiness policy with default triviality thresholds."""
    return ReadinessPolicy(configuration or AssessmentConfiguration(), TrivialityConfiguration())


def pull_request(
    number: int,
    *,
    reviewed: bool,
    approved: bool = True,
    passing: bool = True,
    substantial: bool = True,
    comment_count: int = 1,
    lines: int | None = None,
    cycle_hours: float = 24,
    review_wait_hours: float = 8,
) -> PullRequestFact:
    """Build one merged pull request with the review, check, and size properties a test needs.

    `approved` only means anything alongside `reviewed`: it separates a review that signs the change
    off from one that merely comments, which is the whole distinction approval coverage grades.
    `comment_count` defaults to a review that carries one, so a fixture is exemplary — including for
    `review-depth` — unless a test asks for a shallower one. `cycle_hours` and `review_wait_hours`
    default to the flow metrics' own green boundaries (24 and 8 hours), so a fixture is exemplary for
    them too unless a test asks otherwise; `review_wait_hours` is measured from `created_at`, so it
    must not exceed `cycle_hours`.
    """
    merged_at = datetime(2026, 7, 1, tzinfo=UTC) + timedelta(days=number)
    created_at = merged_at - timedelta(hours=cycle_hours)
    return PullRequestFact(
        identifier=number,
        repository="cath-service",
        number=number,
        created_at=created_at,
        merged_at=merged_at,
        draft=False,
        author_login="author",
        author_type="User",
        reviews=(
            ReviewFact(
                identifier=number,
                submitted_at=created_at + timedelta(hours=review_wait_hours),
                state=ReviewState.APPROVED if approved else ReviewState.COMMENTED,
                author_login="reviewer",
                author_type="User",
                comment_count=comment_count,
            ),
        )
        if reviewed
        else (),
        additions=lines if lines is not None else (200 if substantial else 1),
        deletions=0,
        changed_files=5 if substantial else 1,
        checks=(
            CheckFact(
                name="build",
                conclusion=CheckConclusion.SUCCESS if passing else CheckConclusion.FAILURE,
                completed_at=merged_at - timedelta(hours=1),
            ),
        ),
    )


def direct_commit(number: int, *, substantial: bool = True) -> DirectCommitFact:
    """Build one commit pushed straight to the default branch."""
    return DirectCommitFact(
        sha=f"{number:040x}",
        committed_at=datetime(2026, 7, 1, tzinfo=UTC) + timedelta(days=number),
        author_login="pusher",
        author_type="User",
        additions=200 if substantial else 1,
        deletions=0,
        changed_files=5 if substantial else 1,
    )


def facts(
    *pull_requests: PullRequestFact,
    direct_commits: tuple[DirectCommitFact, ...] = (),
) -> CachedBehaviourFacts:
    """Build one cached window holding the given merges."""
    return CachedBehaviourFacts(
        organization="hmcts",
        repository="cath-service",
        starts_at=datetime(2026, 7, 1, tzinfo=UTC),
        ends_at=datetime(2026, 8, 1, tzinfo=UTC),
        pull_requests=pull_requests,
        direct_commits=direct_commits,
    )


def compliant_facts(count: int = 10) -> CachedBehaviourFacts:
    """Build a cohort large enough to grade, with nothing wrong in it."""
    return facts(*(pull_request(number, reviewed=True) for number in range(1, count + 1)))


def gate(
    *,
    protected: bool = True,
    review_count: int = 1,
    rules_observed: bool = True,
    checks: tuple[str, ...] = ("build",),
    administrators: bool | None = True,
    dismiss_stale_reviews: bool = True,
    blocks_force_pushes: bool = True,
    restricts_deletions: bool = True,
    requires_linear_history: bool = True,
    restricts_branch_names: bool = True,
) -> MergeGateReport:
    """Build a stored merge gate report, exemplary unless a test asks for a weakness.

    All six attributed rule types are configured by default, so the fixture is the six-rule ruleset
    the survey found on `opal-common-lib` rather than the four-rule shape `cath-service` reports.
    The last three are neutral rather than exemplary: absent, they are no weaker, only reported so.
    """
    return MergeGateReport(
        fetched_at=datetime(2026, 8, 8, 9, tzinfo=UTC),
        gate=MergeGateEvidence(
            branch="master",
            protected=protected,
            pull_requests=(
                PullRequestRule(
                    dismiss_stale_reviews_on_push=dismiss_stale_reviews,
                    require_code_owner_review=False,
                    require_last_push_approval=False,
                    required_approving_review_count=review_count,
                    required_review_thread_resolution=False,
                ),
            ),
            status_checks=(
                StatusChecksRule(
                    strict_required_status_checks_policy=True,
                    required_status_checks=tuple(StatusCheck(context=context) for context in checks),
                ),
            )
            if checks
            else (),
            restricts_deletions=restricts_deletions,
            blocks_force_pushes=blocks_force_pushes,
            applies_to_administrators=administrators,
            rules_observed=rules_observed,
            requires_linear_history=requires_linear_history,
            restricts_branch_names=restricts_branch_names,
        ),
    )


def reported(assessment: ReadinessAssessment) -> tuple[str, ...]:
    """Return every condition the assessment reported, from whichever section holds it."""
    return tuple(
        condition.condition
        for section in (assessment.blocking, assessment.caution, assessment.clear)
        for condition in section
    )


def detail(assessment: ReadinessAssessment, condition: str) -> str:
    """Return the detail reported for one named condition."""
    return next(
        item.detail
        for section in (assessment.blocking, assessment.caution, assessment.clear)
        for item in section
        if item.condition == condition
    )


def test_a_governed_repository_behaving_well_is_green() -> None:
    """Reach green only with an enforcing gate, a sufficient cohort, and nothing blocking."""
    assessment = policy().assess(compliant_facts(), gate())

    assert assessment.label is ReadinessLabel.GREEN
    assert assessment.blocking == ()
    assert assessment.caution == ()


def test_every_checked_condition_is_reported_in_exactly_one_section() -> None:
    """Account for every check, so a reader sees what was examined rather than only what failed."""
    assessment = policy().assess(compliant_facts(), gate())
    conditions = reported(assessment)

    assert sorted(conditions) == sorted(
        (
            "branch-protected",
            "pull-request-review-required",
            "status-checks-required",
            "gate-applies-to-administrators",
            "stale-reviews-dismissed",
            "force-pushes-blocked",
            "branch-deletion-restricted",
            "linear-history-required",
            "branch-names-restricted",
            "sufficient-merges",
            "independent-review-coverage-at-target",
            "approval-coverage-at-target",
            "checks-passing-at-merge-at-target",
            "review-depth-at-target",
            "pull-request-size-at-target",
            "merge-cycle-time-at-target",
            "time-to-first-review-at-target",
            "substantial-changes-reviewed",
        ),
    )
    assert len(set(conditions)) == len(conditions)
    assert len(assessment.clear) == len(conditions)


def test_a_clear_condition_carries_its_numbers_and_imposes_no_label() -> None:
    """Quantify what passed, because a green with no numbers is an assertion rather than evidence."""
    assessment = policy().assess(compliant_facts(), gate(review_count=2, checks=("build", "test")))

    assert detail(assessment, "independent-review-coverage-at-target") == (
        "independent-review-coverage is 100% (10 of 10), at or above the 90% target"
    )
    assert detail(assessment, "substantial-changes-reviewed") == (
        "substantial merges with no independent human review: 0 of 10 (0%), within the maximum of 1% or 0 merges"
    )
    assert detail(assessment, "sufficient-merges") == (
        "merges into the default branch: 10 (10 merged pull requests and 0 direct commits), "
        "at or above the minimum of 10"
    )
    assert detail(assessment, "pull-request-review-required") == (
        "approving reviews required before merging to master: 2"
    )
    assert detail(assessment, "status-checks-required") == (
        "status checks required before merging to master: 2 (build, test)"
    )
    assert detail(assessment, "review-depth-at-target") == (
        "review-depth is 100% (10 of 10), at or above the 50% boundary"
    )
    assert all(condition.label is None for condition in assessment.clear)


def test_a_gate_requiring_no_status_check_is_a_caution_rather_than_a_veto() -> None:
    """Report the condition closest to a veto without silently passing or failing on it."""
    assessment = policy().assess(compliant_facts(), gate(checks=()))

    assert assessment.label is ReadinessLabel.GREEN
    assert tuple(condition.condition for condition in assessment.caution) == ("status-checks-not-required",)
    assert assessment.caution[0].detail == (
        "status checks required before merging to master: 0, so CI cannot block a merge"
    )
    assert assessment.caution[0].label is None


@pytest.mark.parametrize(
    ("report", "condition"),
    [
        (gate(administrators=False), "administrators-can-bypass-the-gate"),
        (gate(administrators=None), "gate-enforcement-on-administrators-unknown"),
    ],
)
def test_administrator_enforcement_is_reported_without_deciding_the_label(
    report: MergeGateReport,
    condition: str,
) -> None:
    """Weigh a bypassable or undisclosed gate without vetoing on it, as the user decided."""
    assessment = policy().assess(compliant_facts(), report)

    assert assessment.label is ReadinessLabel.GREEN
    assert tuple(item.condition for item in assessment.caution) == (condition,)


def test_an_approval_surviving_a_later_push_is_a_caution() -> None:
    """Flag a gate under which reviewed code and merged code can differ."""
    assessment = policy().assess(compliant_facts(), gate(dismiss_stale_reviews=False))

    assert tuple(condition.condition for condition in assessment.caution) == ("stale-reviews-not-dismissed",)


def test_an_unblocked_force_push_is_a_caution_and_imposes_no_ceiling() -> None:
    """Flag a gate under which an approved history can be rewritten once review is done."""
    assessment = policy().assess(compliant_facts(), gate(blocks_force_pushes=False))

    assert assessment.label is ReadinessLabel.GREEN
    assert assessment.blocking == ()
    assert tuple(condition.condition for condition in assessment.caution) == ("force-pushes-not-blocked",)
    assert assessment.caution[0].detail == (
        "master accepts a force push, so an approved history can be rewritten after review"
    )
    assert assessment.caution[0].label is None


def test_a_gate_blocking_force_pushes_reports_one_clear_condition() -> None:
    """Report the rule that was satisfied, so a green is as auditable as the caution beside it."""
    assessment = policy().assess(compliant_facts(), gate())

    assert assessment.caution == ()
    assert detail(assessment, "force-pushes-blocked") == (
        "a force push to master is blocked, so an approved history cannot be rewritten after review"
    )


@pytest.mark.parametrize(
    ("report", "condition", "expected"),
    [
        (gate(), "branch-deletion-restricted", "deleting master is blocked by the gate"),
        (
            gate(restricts_deletions=False),
            "branch-deletion-not-restricted",
            "deleting master is not blocked by the gate",
        ),
        (gate(), "linear-history-required", "merging to master requires a linear history"),
        (
            gate(requires_linear_history=False),
            "linear-history-not-required",
            "merging to master does not require a linear history",
        ),
        (gate(), "branch-names-restricted", "the gate on master restricts branch names"),
        (
            gate(restricts_branch_names=False),
            "branch-names-not-restricted",
            "the gate on master does not restrict branch names",
        ),
    ],
)
def test_a_neutral_rule_is_reported_clear_whether_it_is_configured_or_not(
    report: MergeGateReport,
    condition: str,
    expected: str,
) -> None:
    """Report deletion, linear history and branch naming in both states, never as a shortfall.

    An absent neutral rule is the case the ruling turns on: it is examined and reported, and it is
    still `clear`, because it bears on none of the four decision questions.
    """
    assessment = policy().assess(compliant_facts(), report)

    assert assessment.label is ReadinessLabel.GREEN
    assert assessment.caution == ()
    assert condition in tuple(item.condition for item in assessment.clear)
    assert detail(assessment, condition) == f"{expected}, which does not bear on the readiness label"


def test_a_rule_the_policy_declines_to_judge_is_marked_informational() -> None:
    """Mark what was reported without being judged, so a renderer need not infer it from a name.

    Every condition `neutral()` produces, and nothing else in the clear section: `clear` means
    "checked, and did not hold the label back", and a rule nobody grades sitting beside a check that
    passed reads as approval the policy never gave.
    """
    assessment = policy().assess(compliant_facts(), gate())
    informational = {condition.condition for condition in assessment.clear if condition.informational}

    assert informational == {
        "branch-deletion-restricted",
        "linear-history-required",
        "branch-names-restricted",
        "sufficient-merges",
    }


def test_an_absent_neutral_rule_is_informational_in_that_state_too() -> None:
    """Carry the flag whichever way the three neutral rules were observed, as their detail does."""
    report = gate(restricts_deletions=False, requires_linear_history=False, restricts_branch_names=False)
    assessment = policy().assess(compliant_facts(), report)
    informational = {condition.condition for condition in assessment.clear if condition.informational}

    assert informational == {
        "branch-deletion-not-restricted",
        "linear-history-not-required",
        "branch-names-not-restricted",
        "sufficient-merges",
    }


@pytest.mark.parametrize(
    "condition",
    [
        "branch-protected",
        "pull-request-review-required",
        "status-checks-required",
        "independent-review-coverage-at-target",
        "approval-coverage-at-target",
        "substantial-changes-reviewed",
    ],
)
def test_a_graded_clear_condition_is_not_informational(condition: str) -> None:
    """Keep the flag off a check that was actually satisfied: it is the thing green is for."""
    assessment = policy().assess(compliant_facts(), gate())
    graded = next(item for item in assessment.clear if item.condition == condition)

    assert graded.informational is False


def test_every_graded_metric_condition_is_spelled_with_one_of_four_suffixes() -> None:
    """Pin the condition names a dashboard reads a behaviour metric card's colour off.

    `ui/src/lib/metrics.ts` finds the condition that graded a metric by appending `-at-target`,
    `-below-target`, `-above-target` and `-not-observed` to the metric's own identifier, because
    those are the only spellings `rate`, `review_depth` and `distribution` produce. A fifth spelling
    here, or a rename of one of the four, would leave every card grey with both suites green — the
    UI's tests read hand-written fixtures and cannot see this end of the agreement — so it is
    asserted rather than assumed.
    """
    suffixes = {"at-target", "below-target", "above-target", "not-observed"}
    graded_metrics = (
        IndependentReviewCoverage,
        ApprovalCoverage,
        ChecksPassingAtMerge,
        ReviewDepth,
        PullRequestSize,
        MergeCycleTime,
        TimeToFirstReview,
    )
    cohorts = (
        # Exemplary: every graded metric sitting at its target.
        compliant_facts(),
        # Nothing merged through a pull request, so the flow signals have no sample to place.
        facts(direct_commits=tuple(direct_commit(number) for number in range(1, 11))),
        # Nothing reviewed, which puts both coverage rates below their target.
        facts(*(pull_request(number, reviewed=False, substantial=False) for number in range(1, 11))),
        # Changes far past the size maximum, which is the distribution form of the same shortfall.
        facts(*(pull_request(number, reviewed=True, lines=5000) for number in range(1, 11))),
    )
    conditions = tuple(condition for cohort in cohorts for condition in reported(policy().assess(cohort, gate())))

    spellings: set[str] = set()
    for metric in graded_metrics:
        named = {condition for condition in conditions if condition.startswith(f"{metric.identifier}-")}
        assert named, f"{metric.identifier} was graded under no condition"
        spellings |= {condition.removeprefix(f"{metric.identifier}-") for condition in named}

    assert spellings == suffixes


def test_the_informational_flag_moves_no_label_and_no_section() -> None:
    """Confirm the flag is presentation: the same conditions, in the same sections, as before it."""
    assessment = policy().assess(compliant_facts(), gate())

    assert assessment.label is ReadinessLabel.GREEN
    assert assessment.blocking == ()
    assert assessment.caution == ()
    assert all(condition.label is None for condition in assessment.clear)


def test_two_repositories_differing_only_in_linear_history_share_a_label() -> None:
    """Pin the ruling most easily undone by accident: a neutral rule never moves the label."""
    required = policy().assess(compliant_facts(), gate(requires_linear_history=True))
    absent = policy().assess(compliant_facts(), gate(requires_linear_history=False))

    assert required.label is absent.label
    assert required.blocking == absent.blocking
    assert required.caution == absent.caution
    assert len(required.clear) == len(absent.clear)


def test_the_veto_set_is_still_exactly_two_governance_conditions() -> None:
    """Confirm no rule given a bearing on 2026-08-15 widened the veto set beyond its fixed two."""
    weaknesses = (
        gate(),
        gate(checks=()),
        gate(administrators=False),
        gate(administrators=None),
        gate(dismiss_stale_reviews=False),
        gate(blocks_force_pushes=False),
        gate(restricts_deletions=False),
        gate(requires_linear_history=False),
        gate(restricts_branch_names=False),
        gate(review_count=0),
        gate(protected=False),
    )

    vetoes = {
        condition.condition
        for report in weaknesses
        for condition in policy().assess(compliant_facts(), report).blocking
        if condition.label is ReadinessLabel.RED
    }

    assert vetoes == {"pull-request-review-not-required", "branch-not-protected"}


def test_a_gate_that_does_not_require_review_is_red_however_the_team_behaves() -> None:
    """Veto on governance alone: a gate that never requires review is a hard no."""
    assessment = policy().assess(compliant_facts(), gate(review_count=0))

    assert assessment.label is ReadinessLabel.RED
    assert tuple(condition.condition for condition in assessment.blocking) == ("pull-request-review-not-required",)
    assert assessment.blocking[0].detail == (
        "approving reviews required before merging to master: 0, so review is not enforced at all"
    )
    assert "branch-protected" in reported(assessment)


def test_an_unprotected_branch_is_red_rather_than_unassessable() -> None:
    """Treat an observed absence of protection as a veto, not as a blind spot."""
    assessment = policy().assess(compliant_facts(), gate(protected=False))

    assert assessment.label is ReadinessLabel.RED
    assert assessment.blocking[0].condition == "branch-not-protected"


@pytest.mark.parametrize(
    ("report", "condition"),
    [
        (MergeGateReport(detail="no repository state has been collected"), "merge-gate-not-collected"),
        (gate(review_count=0, rules_observed=False), "merge-gate-rules-not-observable"),
    ],
)
def test_a_gate_that_cannot_be_read_is_neither_passed_nor_blamed(report: MergeGateReport, condition: str) -> None:
    """Report cannot_assess for an unreadable gate rather than inventing either answer.

    The second case matters most: `protected: true` with empty rule arrays is what a non-administrator
    sees, and grading it would report "no review required" for a gate that may well require review.
    Nothing else about the gate is reported either, because none of it was observed.
    """
    assessment = policy().assess(compliant_facts(), report)

    assert assessment.label is ReadinessLabel.CANNOT_ASSESS
    assert tuple(item.condition for item in assessment.blocking) == (condition,)
    assert "branch-protected" not in reported(assessment)


def test_an_unreadable_gate_does_not_hide_a_behavioural_disqualifier() -> None:
    """Let red outrank cannot_assess, because a disqualifier stands without seeing the gate."""
    unreviewed = facts(*(pull_request(number, reviewed=False) for number in range(1, 11)))

    assessment = policy().assess(unreviewed, MergeGateReport(detail="never collected"))

    assert assessment.label is ReadinessLabel.RED
    assert tuple(item.label for item in assessment.blocking) == (
        ReadinessLabel.CANNOT_ASSESS,
        ReadinessLabel.RED,
        ReadinessLabel.RED,
        ReadinessLabel.AMBER,
    )


def test_a_cohort_too_small_to_show_a_pattern_is_not_graded() -> None:
    """Refuse to turn four merges into a rate, and say so rather than reporting a shortfall."""
    assessment = policy().assess(facts(*(pull_request(number, reviewed=False) for number in range(1, 5))), gate())

    assert assessment.label is ReadinessLabel.CANNOT_ASSESS
    assert tuple(item.condition for item in assessment.blocking) == ("insufficient-merges",)
    assert assessment.blocking[0].detail == (
        "merges into the default branch: 4 (4 merged pull requests and 0 direct commits), "
        "below the minimum of 10, "
        "so no behavioural condition was graded"
    )
    assert "independent-review-coverage-below-target" not in reported(assessment)
    # The gate was still judged, because a veto does not depend on the size of the cohort.
    assert "pull-request-review-required" in reported(assessment)


def test_an_empty_cohort_is_not_graded_as_zero_coverage() -> None:
    """Never read an absent cohort as observed failure."""
    assessment = policy().assess(facts(), gate())

    assert assessment.label is ReadinessLabel.CANNOT_ASSESS
    assert tuple(item.condition for item in assessment.blocking) == ("insufficient-merges",)


def test_review_coverage_between_the_boundaries_is_amber() -> None:
    """Grade a rate that misses the target but clears the amber boundary as amber.

    An unreviewed merge is unapproved too, so both coverage rates block here. That is the expected
    cost of grading them separately, and the reason each reports its own numbers.
    """
    cohort = facts(
        *(pull_request(number, reviewed=True) for number in range(1, 9)),
        *(pull_request(number, reviewed=False, substantial=False) for number in range(9, 11)),
    )

    assessment = policy().assess(cohort, gate())

    assert assessment.label is ReadinessLabel.AMBER
    assert tuple(item.condition for item in assessment.blocking) == (
        "independent-review-coverage-below-target",
        "approval-coverage-below-target",
    )
    assert assessment.blocking[0].detail == "independent-review-coverage is 80% (8 of 10), below the 90% target"


def test_review_without_approval_is_graded_even_though_review_coverage_passes() -> None:
    """Close the gap approval coverage exists for: a diligent reviewer who never signs anything off.

    Every one of these pull requests carries an eligible independent human review, so review coverage
    is a clean 100%. None carries an approval, so nobody is on record as having accepted the change.
    """
    cohort = facts(*(pull_request(number, reviewed=True, approved=False) for number in range(1, 11)))

    assessment = policy().assess(cohort, gate())

    assert assessment.label is ReadinessLabel.RED
    assert "independent-review-coverage-at-target" in reported(assessment)
    assert tuple(item.condition for item in assessment.blocking) == ("approval-coverage-below-target",)
    assert assessment.blocking[0].detail == "approval-coverage is 0% (0 of 10), below the 90% target"


def test_review_coverage_below_the_amber_boundary_is_red() -> None:
    """Grade a rate below the amber boundary as red without needing a gate finding."""
    cohort = facts(
        *(pull_request(number, reviewed=True) for number in range(1, 6)),
        *(pull_request(number, reviewed=False, substantial=False) for number in range(6, 11)),
    )

    assessment = policy().assess(cohort, gate())

    assert assessment.label is ReadinessLabel.RED
    assert assessment.blocking[0].label is ReadinessLabel.RED


def test_failing_checks_at_merge_are_graded_against_their_own_threshold() -> None:
    """Judge CI trustworthiness separately from review coverage."""
    cohort = facts(
        *(pull_request(number, reviewed=True) for number in range(1, 9)),
        *(pull_request(number, reviewed=True, passing=False) for number in range(9, 11)),
    )

    assessment = policy().assess(cohort, gate())

    assert tuple(item.condition for item in assessment.blocking) == ("checks-passing-at-merge-below-target",)
    assert assessment.blocking[0].detail == "checks-passing-at-merge is 80% (8 of 10), below the 90% target"


def test_review_depth_below_the_boundary_is_a_caution_never_a_block() -> None:
    """Weigh a shallow approval rate without ever letting it hold the label below green."""
    cohort = facts(*(pull_request(number, reviewed=True, comment_count=0) for number in range(1, 11)))

    assessment = policy().assess(cohort, gate())

    assert assessment.label is ReadinessLabel.GREEN
    assert tuple(condition.condition for condition in assessment.caution) == ("review-depth-below-target",)
    assert assessment.caution[0].label is None
    assert assessment.caution[0].detail == "review-depth is 0% (0 of 10), below the 50% boundary"


def test_review_depth_at_or_above_the_boundary_is_clear() -> None:
    """Report a healthy approval-comment rate as satisfied, with its numbers."""
    assessment = policy().assess(compliant_facts(), gate())

    assert tuple(condition.condition for condition in assessment.clear if "review-depth" in condition.condition) == (
        "review-depth-at-target",
    )


def test_review_depth_is_not_observed_when_nothing_was_ever_approved() -> None:
    """State not-observed rather than a manufactured shortfall when no approval exists to rate."""
    cohort = facts(*(pull_request(number, reviewed=True, approved=False) for number in range(1, 11)))

    assessment = policy().assess(cohort, gate())

    assert tuple(condition.condition for condition in assessment.caution) == ("review-depth-not-observed",)


def test_review_depth_boundary_is_configurable() -> None:
    """Let configuration decide the caution boundary rather than a fixed 50%."""
    cohort = facts(*(pull_request(number, reviewed=True, comment_count=0) for number in range(1, 11)))
    lenient = AssessmentConfiguration.model_validate({"review-depth-minimum-percentage": 0})

    assert policy(lenient).assess(cohort, gate()).label is ReadinessLabel.GREEN
    assert "review-depth-at-target" in reported(policy(lenient).assess(cohort, gate()))
    assert "review-depth-below-target" in reported(policy().assess(cohort, gate()))


def test_pull_request_size_at_or_below_the_green_boundary_is_clear() -> None:
    """Grade the 75th percentile of changed lines against its own green target."""
    cohort = facts(*(pull_request(number, reviewed=True, lines=400) for number in range(1, 11)))

    assessment = policy().assess(cohort, gate())

    assert detail(assessment, "pull-request-size-at-target") == (
        "pull-request-size 75th percentile is 400 lines, at or below the 400 lines target"
    )


def test_pull_request_size_above_green_and_at_or_below_amber_is_amber() -> None:
    """Grade a p75 above the green target but within the amber ceiling as amber, not red."""
    cohort = facts(*(pull_request(number, reviewed=True, lines=800) for number in range(1, 11)))

    assessment = policy().assess(cohort, gate())

    assert assessment.label is ReadinessLabel.AMBER
    condition = next(item for item in assessment.blocking if item.condition == "pull-request-size-above-target")
    assert condition.label is ReadinessLabel.AMBER
    assert condition.detail == "pull-request-size 75th percentile is 800 lines, above the 400 lines target"


def test_pull_request_size_far_above_its_maximum_is_still_only_amber() -> None:
    """Hold a flow signal at amber whatever it costs, because a cost is not a governance failure."""
    cohort = facts(*(pull_request(number, reviewed=True, lines=801) for number in range(1, 11)))

    assessment = policy().assess(cohort, gate())

    assert assessment.label is ReadinessLabel.AMBER
    condition = next(item for item in assessment.blocking if item.condition == "pull-request-size-above-target")
    assert condition.label is ReadinessLabel.AMBER


def test_merge_cycle_time_at_or_below_the_green_boundary_is_clear() -> None:
    """Grade the median merge cycle time against its own green target."""
    cohort = facts(*(pull_request(number, reviewed=True, cycle_hours=24) for number in range(1, 11)))

    assessment = policy().assess(cohort, gate())

    assert detail(assessment, "merge-cycle-time-at-target") == (
        "merge-cycle-time median is 24 hours, at or below the 24 hours target"
    )


def test_merge_cycle_time_above_green_and_at_or_below_amber_is_amber() -> None:
    """Grade a median cycle time above the green target but within the amber ceiling as amber."""
    cohort = facts(*(pull_request(number, reviewed=True, cycle_hours=48) for number in range(1, 11)))

    assessment = policy().assess(cohort, gate())

    assert assessment.label is ReadinessLabel.AMBER
    condition = next(item for item in assessment.blocking if item.condition == "merge-cycle-time-above-target")
    assert condition.label is ReadinessLabel.AMBER


def test_merge_cycle_time_far_above_its_maximum_is_still_only_amber() -> None:
    """Hold a flow signal at amber whatever it costs, because a cost is not a governance failure."""
    cohort = facts(*(pull_request(number, reviewed=True, cycle_hours=49) for number in range(1, 11)))

    assessment = policy().assess(cohort, gate())

    assert assessment.label is ReadinessLabel.AMBER
    condition = next(item for item in assessment.blocking if item.condition == "merge-cycle-time-above-target")
    assert condition.label is ReadinessLabel.AMBER


def test_time_to_first_review_at_or_below_the_green_boundary_is_clear() -> None:
    """Grade the median waiting time for a first review against its own green target."""
    cohort = facts(*(pull_request(number, reviewed=True, review_wait_hours=8) for number in range(1, 11)))

    assessment = policy().assess(cohort, gate())

    assert detail(assessment, "time-to-first-review-at-target") == (
        "time-to-first-review median is 8 hours, at or below the 8 hours target"
    )


def test_time_to_first_review_above_green_and_at_or_below_amber_is_amber() -> None:
    """Grade a median waiting time above the green target but within the amber ceiling as amber."""
    cohort = facts(*(pull_request(number, reviewed=True, review_wait_hours=16) for number in range(1, 11)))

    assessment = policy().assess(cohort, gate())

    assert assessment.label is ReadinessLabel.AMBER
    condition = next(item for item in assessment.blocking if item.condition == "time-to-first-review-above-target")
    assert condition.label is ReadinessLabel.AMBER


def test_time_to_first_review_far_above_its_maximum_is_still_only_amber() -> None:
    """Hold a flow signal at amber whatever it costs, because a cost is not a governance failure."""
    cohort = facts(*(pull_request(number, reviewed=True, review_wait_hours=17) for number in range(1, 11)))

    assessment = policy().assess(cohort, gate())

    assert assessment.label is ReadinessLabel.AMBER
    condition = next(item for item in assessment.blocking if item.condition == "time-to-first-review-above-target")
    assert condition.label is ReadinessLabel.AMBER


def test_flow_metrics_are_not_observed_when_the_cohort_has_no_pull_requests() -> None:
    """State not-observed for every flow signal when a repository merges only by direct commit."""
    cohort = facts(direct_commits=tuple(direct_commit(number) for number in range(1, 11)))

    assessment = policy().assess(cohort, gate())

    assert "pull-request-size-not-observed" in reported(assessment)
    assert "merge-cycle-time-not-observed" in reported(assessment)
    assert "time-to-first-review-not-observed" in reported(assessment)


def test_pull_request_size_is_not_observed_when_github_never_sized_any_change() -> None:
    """State not-observed for size alone when GitHub withheld the diff stat, unlike the other flow metrics."""
    unsized = tuple(
        pull_request(number, reviewed=True).model_copy(update={"additions": None, "deletions": None})
        for number in range(1, 11)
    )
    cohort = facts(*unsized)

    assessment = policy().assess(cohort, gate())

    assert "pull-request-size-not-observed" in reported(assessment)
    assert "merge-cycle-time-at-target" in reported(assessment)
    assert "time-to-first-review-at-target" in reported(assessment)


def test_flow_metric_thresholds_are_configurable() -> None:
    """Let configuration decide each flow signal's boundary rather than a fixed default."""
    cohort = facts(*(pull_request(number, reviewed=True, lines=500) for number in range(1, 11)))
    lenient = AssessmentConfiguration.model_validate(
        {"pull-request-size": {"maximum": 500}},
    )

    assert policy(lenient).assess(cohort, gate()).label is ReadinessLabel.GREEN
    assert policy().assess(cohort, gate()).label is ReadinessLabel.AMBER


def test_each_distribution_metric_is_graded_on_the_percentile_its_policy_names() -> None:
    """Pin which percentile decides each of the three graded distributions.

    Every other distribution test builds a cohort of identical pull requests, where the median, the
    75th and the 90th percentile are the same number — so grading size on its median, or a waiting
    time on its p90, would pass all of them while changing which repositories fail in production.
    Only a spread observation separates the three, and the choice is policy: size is graded on p75
    because a long tail of large changes is the risk, the two times on their median because a single
    forgotten pull request must not label a team.
    """
    spread = DistributionObservation(
        status=ObservationStatus.OBSERVED,
        sample_size=4,
        unit="hours",
        median=1,
        percentile_75=2,
        percentile_90=3,
    )

    assert policy().pull_request_size(spread.model_copy(update={"unit": "lines"})).condition.detail == (
        "pull-request-size 75th percentile is 2 lines, at or below the 400 lines target"
    )
    assert policy().merge_cycle_time(spread).condition.detail == (
        "merge-cycle-time median is 1 hours, at or below the 24 hours target"
    )
    assert policy().time_to_first_review(spread).condition.detail == (
        "time-to-first-review median is 1 hours, at or below the 8 hours target"
    )


def test_an_unobserved_distribution_is_reported_as_ungraded_rather_than_as_a_shortfall() -> None:
    """Hold the line that unavailable data never becomes zero, even reaching the check directly.

    `behaviour()` stops an empty cohort at the sample condition, so this guard is the second line of
    defence: a distribution with no sample describes nothing and must not be reported as a shortfall.
    """
    judgement = policy().distribution(
        PullRequestSize,
        DistributionThreshold(maximum=400),
        DistributionObservation(
            status=ObservationStatus.NOT_APPLICABLE,
            sample_size=0,
            unit="lines",
            median=None,
            percentile_75=None,
            percentile_90=None,
        ),
    )

    assert judgement.condition.condition == "pull-request-size-not-observed"
    assert judgement.condition.label is None


def test_a_flow_signal_caps_at_amber_however_far_above_its_maximum_it_sits() -> None:
    """Keep red for what is ungoverned, never for what is merely slow or large.

    A repository reviewing every merge but taking a fortnight over each one is a different finding
    from one merging unreviewed, and grading a cost to red made the two indistinguishable.
    """
    cohort = facts(*(pull_request(number, reviewed=True, cycle_hours=2400, lines=90000) for number in range(1, 11)))

    assessment = policy().assess(cohort, gate())

    assert assessment.label is ReadinessLabel.AMBER
    assert {condition.label for condition in assessment.blocking} == {ReadinessLabel.AMBER}
    assert detail(assessment, "merge-cycle-time-above-target") == (
        "merge-cycle-time median is 2400 hours, above the 24 hours target"
    )


def test_unreviewed_substantial_merges_are_counted_against_every_substantial_merge() -> None:
    """Contrast the unreviewed substantial merges with the substantial merges they came from."""
    cohort = facts(
        *(pull_request(number, reviewed=True) for number in range(1, 20)),
        pull_request(20, reviewed=False),
    )

    assessment = policy().assess(cohort, gate())

    assert assessment.label is ReadinessLabel.AMBER
    assert tuple(item.condition for item in assessment.blocking) == ("substantial-changes-merged-unreviewed",)
    assert assessment.blocking[0].detail == (
        "substantial merges with no independent human review: 1 of 20 (5%), above the maximum of 1% or 0 merges"
    )


def test_the_same_unreviewed_count_is_read_against_how_much_a_repository_merges() -> None:
    """Separate a pair of lapses from a habit, which a fixed count can never do.

    Two unreviewed substantial merges are the same number in both cohorts and mean opposite things:
    0.8% of a repository that reviews almost everything, and 10% of one that does not.
    """
    busy = facts(
        *(pull_request(number, reviewed=True) for number in range(1, 249)),
        *(pull_request(number, reviewed=False) for number in range(249, 251)),
    )
    quiet = facts(
        *(pull_request(number, reviewed=True) for number in range(1, 19)),
        *(pull_request(number, reviewed=False) for number in range(19, 21)),
    )

    assert policy().assess(busy, gate()).label is ReadinessLabel.GREEN
    assert detail(policy().assess(busy, gate()), "substantial-changes-reviewed") == (
        "substantial merges with no independent human review: 2 of 250 (0.8%), within the maximum of 1% or 0 merges"
    )
    assert policy().assess(quiet, gate()).label is ReadinessLabel.AMBER
    assert detail(policy().assess(quiet, gate()), "substantial-changes-merged-unreviewed") == (
        "substantial merges with no independent human review: 2 of 20 (10%), above the maximum of 1% or 0 merges"
    )


def test_the_absolute_allowance_forgives_a_cohort_the_percentage_would_condemn() -> None:
    """Let either allowance clear the condition, so a thin cohort is not condemned by arithmetic."""
    cohort = facts(
        *(pull_request(number, reviewed=True) for number in range(1, 19)),
        *(pull_request(number, reviewed=False) for number in range(19, 21)),
    )
    tolerant = AssessmentConfiguration.model_validate(
        {"unreviewed-substantial-merges": {"maximum_count": 2, "maximum_percentage": 1}},
    )

    assert detail(policy(tolerant).assess(cohort, gate()), "substantial-changes-reviewed") == (
        "substantial merges with no independent human review: 2 of 20 (10%), within the maximum of 1% or 2 merges"
    )


def test_both_allowances_at_zero_restore_an_absolute_reading() -> None:
    """Keep the strictest policy expressible: no unreviewed substantial merge at any cohort size."""
    cohort = facts(
        *(pull_request(number, reviewed=True) for number in range(1, 249)),
        *(pull_request(number, reviewed=False) for number in range(249, 251)),
    )
    strict = AssessmentConfiguration.model_validate(
        {"unreviewed-substantial-merges": {"maximum_count": 0, "maximum_percentage": 0}},
    )

    assert policy(strict).assess(cohort, gate()).label is ReadinessLabel.AMBER
    assert detail(policy(strict).assess(cohort, gate()), "substantial-changes-merged-unreviewed") == (
        "substantial merges with no independent human review: 2 of 250 (0.8%), above the maximum of 0% or 0 merges"
    )


def test_a_trivial_unreviewed_merge_does_not_count_as_substantial() -> None:
    """Keep a one-line fix out of the substantial count, as the triviality thresholds define it."""
    cohort = facts(
        *(pull_request(number, reviewed=True) for number in range(1, 20)),
        pull_request(20, reviewed=False, substantial=False),
    )

    assessment = policy().assess(cohort, gate())

    assert assessment.label is ReadinessLabel.GREEN
    assert detail(assessment, "substantial-changes-reviewed") == (
        "substantial merges with no independent human review: 0 of 19 (0%), within the maximum of 1% or 0 merges"
    )


def test_configured_thresholds_decide_the_label_rather_than_fixed_code() -> None:
    """Let configuration own every behavioural boundary, because each one is a policy judgement."""
    cohort = facts(
        *(pull_request(number, reviewed=True) for number in range(1, 9)),
        *(pull_request(number, reviewed=False) for number in range(9, 11)),
    )
    relaxed = AssessmentConfiguration.model_validate(
        {
            "independent-review-coverage": {"green_percentage": 75, "amber_percentage": 50},
            "approval-coverage": {"green_percentage": 75, "amber_percentage": 50},
            "unreviewed-substantial-merges": {"maximum_count": 2},
        },
    )

    assert policy(relaxed).assess(cohort, gate()).label is ReadinessLabel.GREEN
    assert policy().assess(cohort, gate()).label is ReadinessLabel.AMBER


def test_readiness_policy_takes_its_thresholds_from_configuration(tmp_path: Path) -> None:
    """Construct the policy from one validated configuration, never from ambient defaults."""
    configuration = Configuration.model_validate(
        {
            "version": 1,
            "organization": "hmcts",
            "database": tmp_path / "metrics.sqlite3",
            "assessment": {"minimum_merges": 3},
            "triviality": {"maximum_lines": 500, "maximum_files": 9},
            "teams": [{"identifier": "civil", "display_name": "Civil", "repositories": ["cath-service"]}],
        },
    )

    built = readiness_policy(configuration)

    assert built.configuration.minimum_merges == 3
    assert built.triviality.maximum_files == 9
    assert built.assess(facts(pull_request(1, reviewed=True)), gate()).label is ReadinessLabel.CANNOT_ASSESS


def test_an_unobserved_rate_is_reported_as_ungraded_rather_than_as_a_shortfall() -> None:
    """Hold the line that unavailable data never becomes zero, even reaching the rate check directly.

    `behaviour()` stops an empty cohort at the sample condition, so this guard is the second line of
    defence: a rate with no denominator describes nothing and must not be reported as a failure.
    """
    judgement = policy().rate(
        IndependentReviewCoverage(),
        ReadinessThresholds(green_percentage=90, amber_percentage=70),
        RateObservation(status=ObservationStatus.NOT_APPLICABLE, numerator=0, denominator=0),
    )

    assert judgement.condition.condition == "independent-review-coverage-not-observed"
    assert judgement.condition.label is None


def test_thresholds_reject_a_green_boundary_below_amber() -> None:
    """Refuse a threshold pair that cannot be satisfied in order."""
    with pytest.raises(ValueError, match="green percentage may not be below the amber percentage"):
        ReadinessThresholds(green_percentage=50, amber_percentage=90)


def test_direct_commits_dilute_every_governance_rate_they_bypassed() -> None:
    """Grade a repository on every merge it made, not on the share that went through review."""
    merges = facts(
        *(pull_request(number, reviewed=True) for number in range(1, 11)),
        direct_commits=tuple(direct_commit(number) for number in range(1, 11)),
    )

    assessment = policy().assess(merges, gate())

    assert assessment.label is ReadinessLabel.RED
    assert detail(assessment, "independent-review-coverage-below-target") == (
        "independent-review-coverage is 50% (10 of 20), below the 90% target"
    )
    assert detail(assessment, "approval-coverage-below-target") == (
        "approval-coverage is 50% (10 of 20), below the 90% target"
    )
    assert detail(assessment, "substantial-changes-merged-unreviewed") == (
        "substantial merges with no independent human review: 10 of 20 (50%), above the maximum of 1% or 0 merges"
    )


def test_a_repository_that_mostly_bypasses_pull_requests_is_still_graded() -> None:
    """Judge the repository where the bypass is worst, rather than calling it unassessable.

    Counting merged pull requests alone would report `cannot_assess` here — four is below the
    minimum — while the eight changes that skipped review entirely went unexamined.
    """
    merges = facts(
        *(pull_request(number, reviewed=True) for number in range(1, 5)),
        direct_commits=tuple(direct_commit(number) for number in range(1, 9)),
    )

    assessment = policy().assess(merges, gate())

    assert assessment.label is ReadinessLabel.RED
    assert detail(assessment, "sufficient-merges") == (
        "merges into the default branch: 12 (4 merged pull requests and 8 direct commits), "
        "at or above the minimum of 10"
    )
