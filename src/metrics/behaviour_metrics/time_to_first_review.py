"""Define the time-to-first-review metric."""

from typing import ClassVar

from metrics.analysis import distribution, eligible_reviews, review_started_at
from metrics.behaviour_metrics.base import BehaviourMetric
from metrics.behaviour_metrics.reviews import independent_review_classification
from metrics.domain import DistributionObservation, Merges, Percentile, PullRequestFact


class TimeToFirstReview(BehaviourMetric):
    """Measure how long a merged pull request waited for its first independent review.

    Measured from `review_started_at`, so time spent in draft is not counted as waiting: nobody was
    asked to look at the change yet, and charging that delay to the reviewers described the wrong
    team.
    """

    identifier: ClassVar[str] = "time-to-first-review"
    percentile: ClassVar[Percentile] = Percentile.MEDIAN
    """Read at the median: a property of how a team works, not of its slowest single review."""

    def summary(self, cohort: Merges) -> DistributionObservation:
        """Report waiting-time percentiles across the pull requests that were reviewed."""
        waits = tuple(wait for fact in cohort.pull_requests if (wait := self.value(fact)) is not None)
        return distribution(waits, "hours")

    def classification(self, pull_request: PullRequestFact) -> str:
        """Explain why an unreviewed pull request contributes no waiting time."""
        return "included" if eligible_reviews(pull_request) else independent_review_classification(pull_request)

    def value(self, pull_request: PullRequestFact) -> float | None:
        """Return the hours between entering review and the first independent review, when one exists."""
        reviews = eligible_reviews(pull_request)
        if not reviews:
            return None
        first = min(review.submitted_at for review in reviews)
        return (first - review_started_at(pull_request)).total_seconds() / 3600
