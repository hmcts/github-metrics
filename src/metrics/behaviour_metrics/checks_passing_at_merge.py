"""Define the checks-passing-at-merge metric."""

from typing import ClassVar

from metrics.analysis import eligible_checks, is_passing_check
from metrics.behaviour_metrics.base import GovernanceRate
from metrics.domain import CheckRollupState, DirectCommitFact, PullRequestFact


class ChecksPassingAtMerge(GovernanceRate):
    """Measure merges into the default branch whose status checks had passed."""

    identifier: ClassVar[str] = "checks-passing-at-merge"
    counted: ClassVar[str] = "passing"

    def classification(self, pull_request: PullRequestFact) -> str:
        """Explain the state of one pull request's checks at the instant it merged."""
        if not pull_request.checks:
            return "no-checks"
        completed = eligible_checks(pull_request)
        if not all(is_passing_check(check) for check in completed):
            return "failing"
        if len(completed) < len(pull_request.checks):
            return "incomplete-at-merge"
        return "passing"

    # GitHub's rollup states, mapped onto the vocabulary a merged pull request already uses.
    commit_states: ClassVar[dict[CheckRollupState, str]] = {
        CheckRollupState.SUCCESS: "passing",
        CheckRollupState.FAILURE: "failing",
        CheckRollupState.ERROR: "failing",
        CheckRollupState.PENDING: "checks-unfinished",
        CheckRollupState.EXPECTED: "checks-unfinished",
    }

    def commit_classification(self, direct_commit: DirectCommitFact) -> str:
        """Explain the state of the checks that ran on one direct commit.

        Judged by conclusion rather than as at an instant, unlike a pull request. Nothing gated the
        push, so there is no merge point to measure against and no check could have finished before
        the commit existed; requiring one would report every direct commit as unchecked and hide the
        difference between a repository whose CI runs on the default branch and one whose does not.
        """
        if direct_commit.check_state is None:
            return "no-checks"
        return self.commit_states[direct_commit.check_state]
