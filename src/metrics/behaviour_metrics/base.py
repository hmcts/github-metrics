"""Define the uniform interface for behaviour metric drill-downs."""

from abc import ABC, abstractmethod
from typing import ClassVar

from metrics.analysis import rate
from metrics.domain import (
    CachedBehaviourFacts,
    CommitEvidenceReference,
    DirectCommitFact,
    DistributionObservation,
    Merges,
    Percentile,
    PullRequestEvidenceReference,
    PullRequestFact,
    RateObservation,
    ReviewEvidenceReference,
)


class BehaviourMetric(ABC):
    """Project the merges into a default branch into one metric-specific drill-down."""

    identifier: ClassVar[str]

    percentile: ClassVar[Percentile] = Percentile.MEDIAN
    """Which percentile of this metric's distribution one number is taken from, declared ONCE.

    Read by the readiness assessment, which grades it, and by a trend, which compares it between
    windows: the two must read the same percentile or they describe the same window differently, and
    a second copy of the choice is how they would drift apart. A metric whose summary is a rate has
    no distribution to read and never consults it; the default keeps every metric answerable rather
    than adding an optional nobody can act on.
    """

    @abstractmethod
    def summary(self, cohort: Merges) -> RateObservation | DistributionObservation:
        """Calculate this metric's aggregate observation for one merge cohort."""

    @abstractmethod
    def classification(self, pull_request: PullRequestFact) -> str:
        """Classify one merged pull request for this metric."""

    def commit_classification(self, direct_commit: DirectCommitFact) -> str | None:
        """Classify one direct commit, or return None when this metric does not count commits.

        None is the flow metrics' answer: a commit pushed straight to the default branch has no
        cycle to time and no review to wait for, so padding those samples would invent data.
        """
        return None

    def reviews(self, pull_request: PullRequestFact) -> tuple[ReviewEvidenceReference, ...]:
        """Return review references relevant to this metric."""
        return tuple(
            ReviewEvidenceReference(
                submitted_at=review.submitted_at,
                state=review.state,
                author_login=review.author_login,
                author_type=review.author_type,
            )
            for review in pull_request.reviews
        )

    def value(self, pull_request: PullRequestFact) -> float | None:
        """Return this pull request's contribution to the aggregate, when it has one."""
        return None

    def reference(self, organization: str, pull_request: PullRequestFact) -> PullRequestEvidenceReference:
        """Build one named pull-request evidence reference."""
        return PullRequestEvidenceReference(
            number=pull_request.number,
            url=f"https://github.com/{organization}/{pull_request.repository}/pull/{pull_request.number}",
            merged_at=pull_request.merged_at,
            author_login=pull_request.author_login,
            classification=self.classification(pull_request),
            value=self.value(pull_request),
            reviews=self.reviews(pull_request),
        )

    def commit_references(self, cached: CachedBehaviourFacts) -> tuple[CommitEvidenceReference, ...] | None:
        """Name the direct commits this metric counts, or return None when it counts none.

        None rather than an empty tuple, because the report is dumped excluding None fields: a
        metric that ignores direct commits, and a window that saw none, both emit no key at all.
        """
        references = tuple(
            CommitEvidenceReference(
                sha=commit.sha,
                url=f"https://github.com/{cached.organization}/{cached.repository}/commit/{commit.sha}",
                committed_at=commit.committed_at,
                author_login=commit.author_login,
                classification=classification,
            )
            for commit in cached.direct_commits
            if (classification := self.commit_classification(commit)) is not None
        )
        return references or None


class GovernanceRate(BehaviourMetric):
    """Measure the share of merges that satisfy one governance test.

    The denominator is every change that reached the default branch, by either route. A direct
    commit bypassed the process rather than followed it badly, so counting it anywhere else would
    let a repository improve its governance rates by skipping pull requests altogether.
    """

    # The classification whose changes count toward the numerator.
    counted: ClassVar[str]

    def commit_classification(self, direct_commit: DirectCommitFact) -> str:
        """Classify one direct commit, which by definition arrived without review or approval."""
        return "direct-commit"

    def summary(self, cohort: Merges) -> RateObservation:
        """Report the share of merges this metric counts as satisfied."""
        classifications = (
            *(self.classification(pull_request) for pull_request in cohort.pull_requests),
            *(self.commit_classification(commit) for commit in cohort.direct_commits),
        )
        return rate(sum(name == self.counted for name in classifications), len(classifications))
