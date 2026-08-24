"""Define the approval coverage metric."""

from typing import ClassVar

from metrics.analysis import eligible_reviews
from metrics.behaviour_metrics.base import GovernanceRate
from metrics.behaviour_metrics.reviews import independent_review_classification
from metrics.domain import PullRequestFact, ReviewState


class ApprovalCoverage(GovernanceRate):
    """Measure merges into the default branch carrying independent approval."""

    identifier: ClassVar[str] = "approval-coverage"
    counted: ClassVar[str] = "included"

    def classification(self, pull_request: PullRequestFact) -> str:
        """Classify whether a pull request had independent approval."""
        reviews = eligible_reviews(pull_request)
        if any(review.state is ReviewState.APPROVED for review in reviews):
            return "included"
        if reviews:
            return "independent-review-without-approval"
        return independent_review_classification(pull_request)
