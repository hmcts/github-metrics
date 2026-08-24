"""Define the uniform interface for configured practice rules."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar

from metrics.config import PracticeRuleConfiguration, TrivialityConfiguration
from metrics.domain import CachedBehaviourFacts, PracticeFinding


@dataclass(frozen=True)
class PracticeRule(ABC):
    """Evaluate one configured engineering-practice rule."""

    identifier: ClassVar[str]
    configuration: PracticeRuleConfiguration
    triviality: TrivialityConfiguration

    @property
    def enabled(self) -> bool:
        """Report whether this rule should be evaluated."""
        return self.configuration.enabled

    @abstractmethod
    def findings(self, cached: CachedBehaviourFacts) -> tuple[PracticeFinding, ...]:
        """Return this rule's findings for one repository window."""
