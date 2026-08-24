"""Define the merge-cycle-time metric."""

from typing import ClassVar

from metrics.analysis import distribution, review_started_at
from metrics.behaviour_metrics.base import BehaviourMetric
from metrics.domain import DistributionObservation, Merges, Percentile, PullRequestFact


class MergeCycleTime(BehaviourMetric):
    """Measure elapsed time between a change entering review and being merged.

    Measured from `review_started_at`, not from `created_at`: a change opened as a draft and worked
    on for a fortnight was not waiting on the merge gate during that fortnight, and anchoring on
    creation made this metric report how long a branch existed. That is a different question, and
    grading it as cycle time held well-reviewed repositories at a shortfall for their own working
    style.
    """

    identifier: ClassVar[str] = "merge-cycle-time"
    percentile: ClassVar[Percentile] = Percentile.MEDIAN
    """Read at the median: how long a typical change waits, undistorted by the one that stalled."""

    def summary(self, cohort: Merges) -> DistributionObservation:
        """Report merge-cycle-time percentiles in hours across the merged pull requests alone."""
        return distribution(tuple(self.value(fact) for fact in cohort.pull_requests), "hours")

    def classification(self, pull_request: PullRequestFact) -> str:
        """Include every pull request in the merge-cycle-time cohort."""
        return "included"

    def reviews(self, pull_request: PullRequestFact) -> tuple[()]:
        """Exclude review references from cycle-time evidence."""
        return ()

    def value(self, pull_request: PullRequestFact) -> float:
        """Return this pull request's elapsed merge time in hours, excluding time spent in draft."""
        return (pull_request.merged_at - review_started_at(pull_request)).total_seconds() / 3600
