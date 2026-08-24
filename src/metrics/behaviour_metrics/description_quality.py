"""Define the description-quality metric."""

from typing import ClassVar

from metrics.analysis import rate
from metrics.behaviour_metrics.base import BehaviourMetric
from metrics.config import TraceabilityConfiguration
from metrics.domain import Merges, PullRequestFact, RateObservation


class DescriptionQuality(BehaviourMetric):
    """Measure the share of merged pull requests whose description meets a minimum length.

    A neutral aggregate only: a documentation habit is not evidence a change was governed, and no
    threshold here has an owner, so this never enters the readiness label. Direct commits are never
    counted — there was no pull request, so there is no description to judge.
    """

    identifier: ClassVar[str] = "description-quality"

    def __init__(self, configuration: TraceabilityConfiguration) -> None:
        """Store the configured minimum description length."""
        self.minimum_description = configuration.minimum_description

    def described(self, pull_request: PullRequestFact) -> bool:
        """Return whether one pull request's body meets the configured minimum length."""
        body = pull_request.body or ""
        return len(body.strip()) >= self.minimum_description

    def summary(self, cohort: Merges) -> RateObservation:
        """Report the share of merged pull requests with a sufficiently described change."""
        pull_requests = cohort.pull_requests
        return rate(sum(self.described(pull_request) for pull_request in pull_requests), len(pull_requests))

    def classification(self, pull_request: PullRequestFact) -> str:
        """Classify one pull request by whether its description meets the minimum length."""
        return "described" if self.described(pull_request) else "description-too-short"

    def reviews(self, pull_request: PullRequestFact) -> tuple[()]:
        """Exclude review references from description-quality evidence."""
        return ()
