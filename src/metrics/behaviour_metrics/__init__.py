"""Construct available behaviour metrics."""

from metrics.behaviour_metrics.approval_coverage import ApprovalCoverage
from metrics.behaviour_metrics.base import BehaviourMetric
from metrics.behaviour_metrics.checks_passing_at_merge import ChecksPassingAtMerge
from metrics.behaviour_metrics.description_quality import DescriptionQuality
from metrics.behaviour_metrics.independent_review_coverage import IndependentReviewCoverage
from metrics.behaviour_metrics.merge_cycle_time import MergeCycleTime
from metrics.behaviour_metrics.pull_request_size import PullRequestSize
from metrics.behaviour_metrics.review_depth import ReviewDepth
from metrics.behaviour_metrics.time_to_first_review import TimeToFirstReview
from metrics.behaviour_metrics.traceability_reference import TraceabilityReference
from metrics.config import TraceabilityConfiguration


def behaviour_metrics(traceability: TraceabilityConfiguration) -> tuple[BehaviourMetric, ...]:
    """Construct every available behaviour metric."""
    return (
        IndependentReviewCoverage(),
        ApprovalCoverage(),
        ReviewDepth(),
        MergeCycleTime(),
        TimeToFirstReview(),
        PullRequestSize(),
        ChecksPassingAtMerge(),
        DescriptionQuality(traceability),
        TraceabilityReference(traceability),
    )


def behaviour_metric(identifier: str, traceability: TraceabilityConfiguration) -> BehaviourMetric:
    """Return the behaviour metric registered for an identifier."""
    return {metric.identifier: metric for metric in behaviour_metrics(traceability)}[identifier]


def behaviour_metric_identifiers() -> tuple[str, ...]:
    """Return every accepted behaviour metric identifier."""
    return tuple(metric.identifier for metric in behaviour_metrics(TraceabilityConfiguration()))
