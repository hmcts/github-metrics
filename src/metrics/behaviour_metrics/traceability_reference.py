"""Define the traceability-reference metric."""

import re
from typing import ClassVar

from metrics.analysis import rate
from metrics.behaviour_metrics.base import BehaviourMetric
from metrics.config import TraceabilityConfiguration
from metrics.domain import Merges, PullRequestFact, RateObservation


class TraceabilityReference(BehaviourMetric):
    """Measure the share of merged pull requests that reference an issue or ticket.

    A neutral aggregate only, for the same reason as `description-quality`: it never enters the
    readiness label. Title and body are both searched, because a ticket key in the title is
    traceability too, and insisting on the body would fail a team whose convention is the title.
    Direct commits are never counted — there was no pull request to carry a reference.
    """

    identifier: ClassVar[str] = "traceability-reference"

    def __init__(self, configuration: TraceabilityConfiguration) -> None:
        """Compile the configured reference patterns once, rather than per pull request."""
        self.patterns = tuple(re.compile(pattern) for pattern in configuration.reference_patterns)

    def references_ticket(self, pull_request: PullRequestFact) -> bool:
        """Return whether one pull request's title or body matches a configured reference pattern."""
        text = f"{pull_request.title or ''}\n{pull_request.body or ''}"
        return any(pattern.search(text) for pattern in self.patterns)

    def summary(self, cohort: Merges) -> RateObservation:
        """Report the share of merged pull requests that reference an issue or ticket."""
        pull_requests = cohort.pull_requests
        return rate(sum(self.references_ticket(pull_request) for pull_request in pull_requests), len(pull_requests))

    def classification(self, pull_request: PullRequestFact) -> str:
        """Classify one pull request by whether it references an issue or ticket."""
        return "referenced" if self.references_ticket(pull_request) else "reference-missing"

    def reviews(self, pull_request: PullRequestFact) -> tuple[()]:
        """Exclude review references from traceability-reference evidence."""
        return ()
