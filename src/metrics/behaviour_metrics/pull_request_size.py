"""Define the pull-request size metric."""

from typing import ClassVar

from metrics.analysis import distribution
from metrics.behaviour_metrics.base import BehaviourMetric
from metrics.domain import DistributionObservation, Merges, Percentile, PullRequestFact


class PullRequestSize(BehaviourMetric):
    """Measure how many lines each merged pull request changed."""

    identifier: ClassVar[str] = "pull-request-size"
    percentile: ClassVar[Percentile] = Percentile.PERCENTILE_75
    """Read at the 75th percentile: a long tail of large changes is the risk, not the typical size."""

    def summary(self, cohort: Merges) -> DistributionObservation:
        """Report changed-line percentiles across the pull requests that report a size.

        Pull-request size keeps meaning pull-request size: a direct commit is sized too, but folding
        it in here would rename the metric without saying so. Its size is reported with the finding
        it supports instead.
        """
        sizes = tuple(size for fact in cohort.pull_requests if (size := self.value(fact)) is not None)
        return distribution(sizes, "lines")

    def classification(self, pull_request: PullRequestFact) -> str:
        """Separate measurable pull requests from those GitHub did not size."""
        return "included" if self.value(pull_request) is not None else "size-unavailable"

    def reviews(self, pull_request: PullRequestFact) -> tuple[()]:
        """Exclude review references from size evidence."""
        return ()

    def value(self, pull_request: PullRequestFact) -> float | None:
        """Return the lines added and removed, when both were collected."""
        if pull_request.additions is None or pull_request.deletions is None:
            return None
        return float(pull_request.additions + pull_request.deletions)
