"""Judge readiness for AI enablement from declared governance and observed behaviour.

This is the policy layer and it is deliberately outside `behaviour_metrics/`: metrics stay neutral
aggregates with no target and no verdict, which is what makes them reusable when the policy changes.
The assessment consumes them.

Every condition the policy checks reports into exactly one section of its result — blocking, caution,
or clear — so a reader can see what was examined rather than only what failed, and a green label is as
auditable as a red one.

`clear` means "checked, and did not hold the label back" rather than "checked and satisfied". That
widening was taken deliberately on 2026-08-15, when the three neutral merge-gate rules — deletion
protection, linear history and branch naming — were given their bearing: each is reported whether it
is configured or NOT, because it bears on none of the four decision questions and the alternative
was leaving three collected rules out of the assessment entirely. Decision 1 in architecture.md is
explicit that a check which is never reported cannot be argued with. A `clear` entry has never
implied approval, so nothing about how a label is reached changes.

Those entries are marked `informational` on the condition since 2026-09-02, so a renderer can say
what the section has always meant: the three neutral rules and a sufficient cohort were reported and
not judged. The flag decides no label and adds no line to the text report.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from metrics.analysis import Merge, eligible_reviews, rate_percentage, size_class
from metrics.behaviour_metrics.approval_coverage import ApprovalCoverage
from metrics.behaviour_metrics.base import BehaviourMetric
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
    DistributionObservation,
    MergeGateEvidence,
    MergeGateReport,
    ObservationStatus,
    RateObservation,
    ReadinessAssessment,
    ReadinessCondition,
    ReadinessLabel,
)


class Outcome(StrEnum):
    """Group one checked condition into the section of the assessment that reports it."""

    BLOCKING = "blocking"
    CAUTION = "caution"
    CLEAR = "clear"


@dataclass(frozen=True)
class Judgement:
    """Pair one reported condition with the section it belongs in."""

    outcome: Outcome
    condition: ReadinessCondition


@dataclass(frozen=True)
class ReadinessPolicy:
    """Judge one repository from the gate it declares and the behaviour observed under it."""

    # Worst first. Red outranks cannot_assess so an unreadable gate can never hide a disqualifier.
    precedence: ClassVar[tuple[ReadinessLabel, ...]] = (
        ReadinessLabel.RED,
        ReadinessLabel.CANNOT_ASSESS,
        ReadinessLabel.AMBER,
    )
    configuration: AssessmentConfiguration
    triviality: TrivialityConfiguration

    @property
    def enabled(self) -> bool:
        """Report whether readiness should be assessed at all."""
        return self.configuration.enabled

    def blocking(self, condition: str, label: ReadinessLabel, detail: str) -> Judgement:
        """Report one condition that holds the label below green."""
        return Judgement(Outcome.BLOCKING, ReadinessCondition(condition=condition, label=label, detail=detail))

    def caution(self, condition: str, detail: str) -> Judgement:
        """Report one condition that is not disqualifying but that a reader should weigh."""
        return Judgement(Outcome.CAUTION, ReadinessCondition(condition=condition, detail=detail))

    def clear(self, condition: str, detail: str, *, informational: bool = False) -> Judgement:
        """Report one condition that was checked and did not hold the label back.

        `informational` says the condition was reported without being judged, which is a different
        thing from having been satisfied — see `ReadinessCondition`.
        """
        return Judgement(
            Outcome.CLEAR,
            ReadinessCondition(condition=condition, detail=detail, informational=informational),
        )

    def neutral(self, condition: str, detail: str) -> Judgement:
        """Report one rule that was checked and imposes no ceiling in either of its states.

        Always `clear`, whether the rule is configured or absent: the three rules judged this way
        trace to none of the four decision questions, so an absent one is not a shortfall and must
        not be dressed as a caution. The detail says so, since a reader meeting an absent rule under
        `clear` would otherwise have to work out why it is there.

        Informational by definition: this method IS the policy declining to judge, so the flag and
        the sentence in the detail are one statement rather than two that could disagree.
        """
        return self.clear(
            condition,
            f"{detail}, which does not bear on the readiness label",
            informational=True,
        )

    def section(self, judgements: tuple[Judgement, ...], outcome: Outcome) -> tuple[ReadinessCondition, ...]:
        """Return the conditions reported in one section of the assessment."""
        return tuple(judgement.condition for judgement in judgements if judgement.outcome is outcome)

    def label(self, blocking: tuple[ReadinessCondition, ...]) -> ReadinessLabel:
        """Return the most severe label any blocking condition imposes."""
        imposed = {condition.label for condition in blocking}
        return next((label for label in self.precedence if label in imposed), ReadinessLabel.GREEN)

    def review_requirement(self, gate: MergeGateEvidence) -> Judgement:
        """Judge whether the gate requires an approving review, which is the first veto."""
        required = max((rule.required_approving_review_count for rule in gate.pull_requests), default=0)
        if not required:
            return self.blocking(
                "pull-request-review-not-required",
                ReadinessLabel.RED,
                f"approving reviews required before merging to {gate.branch}: 0, so review is not enforced at all",
            )
        return self.clear(
            "pull-request-review-required",
            f"approving reviews required before merging to {gate.branch}: {required}",
        )

    def status_checks(self, gate: MergeGateEvidence) -> Judgement:
        """Note whether the gate requires any status check.

        A caution rather than a veto: `checks-passing-at-merge` measures whether CI actually held at
        the merge point, which is the stronger signal than whether a rule nominally demanded it.
        """
        contexts = tuple(check.context for rule in gate.status_checks for check in rule.required_status_checks)
        if not contexts:
            return self.caution(
                "status-checks-not-required",
                f"status checks required before merging to {gate.branch}: 0, so CI cannot block a merge",
            )
        return self.clear(
            "status-checks-required",
            f"status checks required before merging to {gate.branch}: {len(contexts)} ({', '.join(sorted(contexts))})",
        )

    def administrators(self, gate: MergeGateEvidence) -> Judgement:
        """Note whether the gate binds administrators, which was deliberately rejected as a veto."""
        if gate.applies_to_administrators is None:
            return self.caution(
                "gate-enforcement-on-administrators-unknown",
                f"whether the gate on {gate.branch} binds administrators was not disclosed, "
                "so a bypass is neither confirmed nor ruled out",
            )
        if not gate.applies_to_administrators:
            return self.caution(
                "administrators-can-bypass-the-gate",
                f"the gate on {gate.branch} does not apply to administrators, so it can be bypassed",
            )
        return self.clear(
            "gate-applies-to-administrators",
            f"the gate on {gate.branch} applies to administrators too",
        )

    def stale_reviews(self, gate: MergeGateEvidence) -> Judgement:
        """Note whether an approval survives a later push.

        Worth weighing before agentic tooling in particular: where an agent revises a branch after
        approval, a surviving approval means the reviewed code and the merged code are not the same.
        """
        if any(rule.dismiss_stale_reviews_on_push for rule in gate.pull_requests):
            return self.clear(
                "stale-reviews-dismissed",
                f"an approval on {gate.branch} is dismissed when the branch is pushed again",
            )
        return self.caution(
            "stale-reviews-not-dismissed",
            f"an approval on {gate.branch} survives a later push, so reviewed and merged code can differ",
        )

    def force_pushes(self, gate: MergeGateEvidence) -> Judgement:
        """Note whether the branch can be force pushed after a review has been given.

        Without `non_fast_forward` an approved history can be rewritten once review is done, so the
        reviewed code and the merged code are not the same code — the same reasoning that already
        makes `dismiss_stale_reviews_on_push` a caution, and sharper under agentic tooling than
        under human authorship. It is a CAUTION and never a veto: the veto set is fixed at two
        conditions by architecture.md decision 2, and this does not reopen it.
        """
        if not gate.blocks_force_pushes:
            return self.caution(
                "force-pushes-not-blocked",
                f"{gate.branch} accepts a force push, so an approved history can be rewritten after review",
            )
        return self.clear(
            "force-pushes-blocked",
            f"a force push to {gate.branch} is blocked, so an approved history cannot be rewritten after review",
        )

    def deletions(self, gate: MergeGateEvidence) -> Judgement:
        """Report whether the gate blocks deleting the branch, which never decides the label."""
        if gate.restricts_deletions:
            return self.neutral("branch-deletion-restricted", f"deleting {gate.branch} is blocked by the gate")
        return self.neutral(
            "branch-deletion-not-restricted",
            f"deleting {gate.branch} is not blocked by the gate",
        )

    def linear_history(self, gate: MergeGateEvidence) -> Judgement:
        """Report whether a linear history is required, ruled useful to collect but not to gate on.

        The user's ruling is a deferral rather than a permanent exclusion: not important for gating
        enablement at this time, though some teams or managers may care about it.
        """
        if gate.requires_linear_history:
            return self.neutral("linear-history-required", f"merging to {gate.branch} requires a linear history")
        return self.neutral(
            "linear-history-not-required",
            f"merging to {gate.branch} does not require a linear history",
        )

    def branch_names(self, gate: MergeGateEvidence) -> Judgement:
        """Report whether branch names are restricted, a naming convention rather than a control."""
        if gate.restricts_branch_names:
            return self.neutral("branch-names-restricted", f"the gate on {gate.branch} restricts branch names")
        return self.neutral(
            "branch-names-not-restricted",
            f"the gate on {gate.branch} does not restrict branch names",
        )

    def governance(self, report: MergeGateReport) -> tuple[Judgement, ...]:
        """Judge the declared merge gate, or state that it could not be read.

        The order is the argument. An unprotected default branch is an observed fact and vetoes even
        though no rule detail came with it, while a protected branch whose rules GitHub withheld is
        evidence of nothing at all — so it must not be read as a gate that requires no review, and
        nothing further about it is reported.

        All six attributed rule types are judged here, and the three that can never hold the label
        back are reported LAST so that a run of neutral rule names cannot crowd out the graded and
        veto-adjacent conditions above them.
        """
        gate = report.gate
        if gate is None:
            return (
                self.blocking(
                    "merge-gate-not-collected",
                    ReadinessLabel.CANNOT_ASSESS,
                    report.detail or "the merge gate has not been collected",
                ),
            )
        if not gate.protected:
            return (
                self.blocking(
                    "branch-not-protected",
                    ReadinessLabel.RED,
                    f"the default branch {gate.branch} has no protection, so any push can bypass review",
                ),
            )
        if not gate.rules_observed:
            return (
                self.blocking(
                    "merge-gate-rules-not-observable",
                    ReadinessLabel.CANNOT_ASSESS,
                    f"{gate.branch} is protected but GitHub did not disclose its rules; "
                    "reading them needs Administration access, or a move to rulesets",
                ),
            )
        return (
            self.clear("branch-protected", f"the default branch {gate.branch} is protected"),
            self.review_requirement(gate),
            self.status_checks(gate),
            self.administrators(gate),
            self.stale_reviews(gate),
            self.force_pushes(gate),
            self.deletions(gate),
            self.linear_history(gate),
            self.branch_names(gate),
        )

    def sample(self, cached: CachedBehaviourFacts) -> Judgement:
        """Judge whether the cohort is large enough to show a pattern at all.

        Counted over both routes onto the default branch. A repository doing most of its work in
        direct commits has plenty of evidence to grade, and counting merged pull requests alone
        would report it as unassessable precisely where the bypass is worst.
        """
        merged = len(cached.pull_requests)
        commits = len(cached.direct_commits)
        reported = merged + commits
        minimum = self.configuration.minimum_merges
        measured = (
            f"merges into the default branch: {reported} ({merged} merged pull requests and {commits} direct commits)"
        )
        if reported < minimum:
            return self.blocking(
                "insufficient-merges",
                ReadinessLabel.CANNOT_ASSESS,
                f"{measured}, below the minimum of {minimum}, so no behavioural condition was graded",
            )
        # Informational: a cohort large enough to grade is a PRECONDITION for grading rather than a
        # practice that went well. A repository is not better governed for having merged more.
        return self.clear(
            "sufficient-merges",
            f"{measured}, at or above the minimum of {minimum}",
            informational=True,
        )

    def measured_rate(self, identifier: str, observation: RateObservation) -> tuple[float, str] | None:
        """Compute one rate and how it reads, or None when there was no denominator to divide by.

        Shared by every graded rate so that a change to how a percentage is phrased is made once:
        two copies of this would drift, and the phrasing is what a reader argues with. The
        percentage itself comes from `analysis.rate_percentage`, which a trend reads too.
        """
        percentage = rate_percentage(observation)
        if percentage is None:
            return None
        return percentage, f"{identifier} is {percentage:g}% ({observation.numerator} of {observation.denominator})"

    def rate(
        self,
        metric: BehaviourMetric,
        thresholds: ReadinessThresholds,
        observation: RateObservation,
    ) -> Judgement:
        """Compare one observed rate against its configured green and amber boundaries."""
        graded = self.measured_rate(metric.identifier, observation)
        if graded is None:
            return self.caution(
                f"{metric.identifier}-not-observed",
                f"{metric.identifier} has no denominator in this window, so it was not graded",
            )
        percentage, measured = graded
        if percentage >= thresholds.green_percentage:
            return self.clear(
                f"{metric.identifier}-at-target",
                f"{measured}, at or above the {thresholds.green_percentage:g}% target",
            )
        return self.blocking(
            f"{metric.identifier}-below-target",
            ReadinessLabel.AMBER if percentage >= thresholds.amber_percentage else ReadinessLabel.RED,
            f"{measured}, below the {thresholds.green_percentage:g}% target",
        )

    def review_depth(self, observation: RateObservation) -> Judgement:
        """Weigh whether approvals carry evidence of scrutiny, without ever imposing a ceiling.

        Caution only, on the first cut: no defensible label-deciding threshold exists yet for how
        many approvals ought to carry a comment, and a caution must never impose one regardless.
        """
        identifier = ReviewDepth.identifier
        graded = self.measured_rate(identifier, observation)
        if graded is None:
            return self.caution(
                f"{identifier}-not-observed",
                f"{identifier} has no denominator in this window, so it was not graded",
            )
        percentage, measured = graded
        boundary = self.configuration.review_depth_minimum_percentage
        if percentage >= boundary:
            return self.clear(f"{identifier}-at-target", f"{measured}, at or above the {boundary:g}% boundary")
        return self.caution(f"{identifier}-below-target", f"{measured}, below the {boundary:g}% boundary")

    def distribution(
        self,
        metric: type[BehaviourMetric],
        threshold: DistributionThreshold,
        observation: DistributionObservation,
    ) -> Judgement:
        """Compare one observed percentile against its configured maximum, capping the cost at amber.

        Which percentile is read comes from the metric class, so the assessment and a trend compare
        the same number: a series measuring movement in the median while the label graded the 75th
        percentile would be two reports describing one window differently.

        Lower is better here, the opposite direction from `rate()`: a green result sits at or below
        its maximum rather than at or above a minimum, since the metric measures cost, not compliance.

        AMBER however far above the maximum a value sits, and deliberately so. A flow signal says
        what a team's way of working costs it, never that anything ungoverned reached the default
        branch, and red is the label for the latter. Grading these to red made the two
        indistinguishable: on the HMCTS estate the fastest merge-cycle-time medians all belonged to
        repositories that merge almost nothing through review — 0.003 hours on a repository with 0%
        review coverage — while a repository reviewing 99.3% of 294 merges was held at red for
        taking four days. A slow, well-reviewed repository and an ungoverned one are not the same
        finding and must not carry the same label.
        """
        identifier = metric.identifier
        graded_value = metric.percentile.of(observation)
        if observation.status is not ObservationStatus.OBSERVED or graded_value is None:
            return self.caution(
                f"{identifier}-not-observed",
                f"{identifier} has no observations in this window, so it was not graded",
            )
        measured = f"{identifier} {metric.percentile.label} is {graded_value:g} {observation.unit}"
        if graded_value <= threshold.maximum:
            return self.clear(
                f"{identifier}-at-target",
                f"{measured}, at or below the {threshold.maximum:g} {observation.unit} target",
            )
        return self.blocking(
            f"{identifier}-above-target",
            ReadinessLabel.AMBER,
            f"{measured}, above the {threshold.maximum:g} {observation.unit} target",
        )

    def pull_request_size(self, observation: DistributionObservation) -> Judgement:
        """Grade pull-request size at the percentile the metric declares."""
        return self.distribution(PullRequestSize, self.configuration.pull_request_size, observation)

    def merge_cycle_time(self, observation: DistributionObservation) -> Judgement:
        """Grade merge cycle time, repository-level and never attributed to an actor."""
        return self.distribution(MergeCycleTime, self.configuration.merge_cycle_time, observation)

    def time_to_first_review(self, observation: DistributionObservation) -> Judgement:
        """Grade waiting time for a first review, a property of how a team works."""
        return self.distribution(TimeToFirstReview, self.configuration.time_to_first_review, observation)

    def substantial(self, change: Merge) -> bool:
        """Report whether one merge is too large to be treated as trivial."""
        return size_class(change, self.triviality.maximum_lines, self.triviality.maximum_files) == "substantial"

    def unreviewed_substantial(self, cached: CachedBehaviourFacts) -> Judgement:
        """Contrast substantial merges made unreviewed against every substantial merge.

        Counted from facts rather than from the `unreviewed-merge` rule's findings, so disabling that
        rule cannot turn an unmeasured count into a passing one. Amber rather than red on its own: the
        coverage rate is what shows a systemic failure, and this stops a green without duplicating it.

        Graded against a proportional allowance and an absolute one, and clear when EITHER forgives
        it. A count alone cannot separate a lapse from a habit — the same two unreviewed merges are
        noise against 246 and a pattern against 20 — so `maximum_percentage` carries the judgement
        and `maximum_count` is the floor that keeps a thin cohort from being condemned by
        arithmetic. The percentage compared is the rounded one the detail reports, so a reader can
        never see a figure that appears to sit inside a boundary it was judged outside of.
        """
        merged = tuple(pull_request for pull_request in cached.pull_requests if self.substantial(pull_request))
        pushed = tuple(commit for commit in cached.direct_commits if self.substantial(commit))
        # Every substantial direct commit is unreviewed: there was no pull request to review it.
        unreviewed = sum(not eligible_reviews(pull_request) for pull_request in merged) + len(pushed)
        merges = len(merged) + len(pushed)
        thresholds = self.configuration.unreviewed_substantial_merges
        percentage = round(unreviewed / merges * 100, 1) if merges else 0.0
        measured = f"substantial merges with no independent human review: {unreviewed} of {merges} ({percentage:g}%)"
        boundary = f"{thresholds.maximum_percentage:g}% or {thresholds.maximum_count} merges"
        if percentage <= thresholds.maximum_percentage or unreviewed <= thresholds.maximum_count:
            return self.clear(
                "substantial-changes-reviewed",
                f"{measured}, within the maximum of {boundary}",
            )
        return self.blocking(
            "substantial-changes-merged-unreviewed",
            ReadinessLabel.AMBER,
            f"{measured}, above the maximum of {boundary}",
        )

    def behaviour(self, cached: CachedBehaviourFacts) -> tuple[Judgement, ...]:
        """Judge observed behaviour, or state that the window holds too little to judge.

        An insufficient sample suppresses every graded condition rather than sitting beside them: a
        rate over four merges is arithmetic, and reporting it as a shortfall would dress a thin
        window up as a finding.

        Every graded rate is measured over merges made by either route, so a repository that
        bypasses pull requests is judged on what it actually did rather than on the fraction of its
        work that happened to go through review at all.

        Approval coverage is graded beside independent review rather than instead of it. An approval
        implies a review, so the two rates move together for most teams and a repository merging
        unreviewed will block on both; what the second rate adds is the team that reviews diligently
        and never approves, where the reviewed code carries no record of anyone accepting it.

        The flow signals — pull-request size, merge cycle time, time to first review — are graded
        too, repository-level and never attributed to an actor: a slow first review is a property of
        how a team works, not one person's failing. They stay pull-request-only, since a direct
        commit has no cycle and no review to wait for.
        """
        sample = self.sample(cached)
        if sample.outcome is Outcome.BLOCKING:
            return (sample,)
        review_coverage = IndependentReviewCoverage()
        approval_coverage = ApprovalCoverage()
        checks = ChecksPassingAtMerge()
        return (
            sample,
            self.rate(
                review_coverage,
                self.configuration.independent_review_coverage,
                review_coverage.summary(cached),
            ),
            self.rate(
                approval_coverage,
                self.configuration.approval_coverage,
                approval_coverage.summary(cached),
            ),
            self.rate(
                checks,
                self.configuration.checks_passing_at_merge,
                checks.summary(cached),
            ),
            self.review_depth(ReviewDepth().summary(cached)),
            self.pull_request_size(PullRequestSize().summary(cached)),
            self.merge_cycle_time(MergeCycleTime().summary(cached)),
            self.time_to_first_review(TimeToFirstReview().summary(cached)),
            self.unreviewed_substantial(cached),
        )

    def assess(self, cached: CachedBehaviourFacts, report: MergeGateReport) -> ReadinessAssessment:
        """Judge one repository's readiness for agentic tooling."""
        judgements = self.governance(report) + self.behaviour(cached)
        blocking = self.section(judgements, Outcome.BLOCKING)
        return ReadinessAssessment(
            label=self.label(blocking),
            blocking=blocking,
            caution=self.section(judgements, Outcome.CAUTION),
            clear=self.section(judgements, Outcome.CLEAR),
        )


def readiness_policy(configuration: Configuration) -> ReadinessPolicy:
    """Construct the readiness policy with its validated configuration."""
    return ReadinessPolicy(configuration.assessment, configuration.triviality)
