"""Share review classifications across review-based metrics."""

from metrics.analysis import is_human_review
from metrics.domain import PullRequestFact, ReviewState


def independent_review_classification(pull_request: PullRequestFact) -> str:
    """Explain why a pull request has no eligible independent review before merge."""
    if not pull_request.reviews:
        return "no-review-events"
    submitted = tuple(review for review in pull_request.reviews if review.submitted_at <= pull_request.merged_at)
    completed = tuple(review for review in submitted if review.state is not ReviewState.PENDING)
    human = tuple(review for review in completed if is_human_review(review))
    if not submitted:
        return "reviews-after-merge"
    if not completed:
        return "pending-reviews-only"
    if not human:
        return "bot-or-unattributed-reviews-only"
    return "author-reviews-only"
