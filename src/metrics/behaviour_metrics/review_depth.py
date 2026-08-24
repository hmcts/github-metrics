"""Define the review-depth metric."""

from typing import ClassVar

from metrics.analysis import eligible_reviews, rate
from metrics.behaviour_metrics.base import BehaviourMetric
from metrics.domain import Merges, PullRequestFact, RateObservation, ReviewFact, ReviewState


class ReviewDepth(BehaviourMetric):
    """Measure whether an approval means anything: the share backed by something the reviewer said.

    Rated over eligible approving reviews, not over merges — a repository with 100% approval
    coverage where every approval is a wordless click is exactly the gap this closes.

    An approval is judged against everything ITS OWN REVIEWER said on that pull request, not against
    the approval event alone. Approving is a click, and GitHub records it as a review of its own:
    the common way to review is to leave the remarks first, as `COMMENTED` reviews, and come back to
    approve once they are answered. Reading only the approval scores that reviewer as a rubber stamp
    and is simply wrong about what happened — on pcs-api it classified 70 pull requests carrying 224
    comment-reviews from a non-author as `uncommented-approval-only`.

    Attribution stays PER REVIEWER rather than per pull request. Where two approvals are required and
    one reviewer argues the change through while the second clicks approve in silence, the second
    approval carries no scrutiny and the metric must still say so; crediting it with the first
    reviewer's comments would hide exactly the rubber stamp this exists to find.
    """

    identifier: ClassVar[str] = "review-depth"

    def approving_reviews(self, pull_request: PullRequestFact) -> tuple[ReviewFact, ...]:
        """Return the eligible reviews that approved one pull request."""
        return tuple(review for review in eligible_reviews(pull_request) if review.state is ReviewState.APPROVED)

    def spoke(self, review: ReviewFact) -> bool:
        """Return whether one review event said anything at all, inline or as its own summary.

        BOTH, because GitHub records them separately and a reviewer may use either: "this changes the
        retry semantics, I checked X" with no inline comment is not a wordless click, and counting
        only `comments` would score it as one. Presence, not length — `description-quality` grades
        how much was written about a change, and no boundary for how much a review must say has an
        owner.
        """
        return review.comment_count > 0 or review.has_body

    def commented(self, pull_request: PullRequestFact, approval: ReviewFact) -> bool:
        """Return whether the reviewer behind one approval said anything on that pull request.

        Every eligible review by that reviewer counts, whenever it was submitted: remarks left before
        the approval are the normal case, and a reviewer who answers a question after approving has
        still scrutinised the change. `eligible_reviews` has already excluded the author's own
        reviews, bot reviews, and anything submitted after the merge.
        """
        return any(
            self.spoke(review)
            for review in eligible_reviews(pull_request)
            if review.author_login == approval.author_login
        )

    def summary(self, cohort: Merges) -> RateObservation:
        """Report the share of eligible approvals whose reviewer said something on the pull request."""
        approvals = tuple(
            (pull_request, approval)
            for pull_request in cohort.pull_requests
            for approval in self.approving_reviews(pull_request)
        )
        return rate(sum(self.commented(pull_request, approval) for pull_request, approval in approvals), len(approvals))

    def classification(self, pull_request: PullRequestFact) -> str:
        """Classify one pull request by the depth of its eligible approving reviews."""
        approving = self.approving_reviews(pull_request)
        if not approving:
            return "no-eligible-approval"
        if any(self.commented(pull_request, approval) for approval in approving):
            return "commented"
        return "uncommented-approval-only"
