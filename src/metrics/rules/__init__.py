"""Construct the configured engineering-practice rules."""

from metrics.config import Configuration
from metrics.rules.base import PracticeRule
from metrics.rules.unreviewed_merge import UnreviewedMerge


def configured_rules(configuration: Configuration) -> tuple[PracticeRule, ...]:
    """Construct every available rule with its validated configuration."""
    return (UnreviewedMerge(configuration.practices.unreviewed_merge, configuration.triviality),)
