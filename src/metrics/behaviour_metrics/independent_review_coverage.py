"""Define the independent-review coverage metric."""

from typing import ClassVar

from metrics.analysis import eligible_reviews
from metrics.behaviour_metrics.base import GovernanceRate
from metrics.behaviour_metrics.reviews import independent_review_classification
from metrics.domain import PullRequestFact


class IndependentReviewCoverage(GovernanceRate):
    """Measure merges into the default branch carrying independent human review."""

    identifier: ClassVar[str] = "independent-review-coverage"
    counted: ClassVar[str] = "included"

    def classification(self, pull_request: PullRequestFact) -> str:
        """Classify whether a pull request had independent human review."""
        return "included" if eligible_reviews(pull_request) else independent_review_classification(pull_request)
