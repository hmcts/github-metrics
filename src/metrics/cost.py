"""Measure what one collection run spends on each repository.

An instrument and nothing more: it measures, it does not optimise, and no caller may read it as
permission to. Architecture.md's cost discipline is that AN ESTIMATE OF A NEW SOURCE'S COST IS NOT
EVIDENCE — the direct-commit source measured at 18 times its estimate before it was fixed — and the
same rule applies now the population is fourteen repositories rather than one.
"""

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from time import monotonic

from metrics.domain import RepositoryCollectionCost
from metrics.github import GitHubClient


def elapsed() -> float:
    """Read the monotonic clock a meter measures elapsed seconds between.

    Named rather than used directly so that the clock is one indirection a test can replace, and
    monotonic rather than wall-clock so that a correction to the system clock partway through a
    fourteen-repository run cannot report a repository as having taken a negative amount of time.
    """
    return monotonic()


@dataclass
class CostMeter:
    """Accumulate the calls and the seconds one repository's collection used.

    Mutable, unlike every evidence model it feeds, because it is an instrument rather than an
    observation: `measure()` may be entered more than once and adds each stretch to the same running
    totals. A repository IS collected in more than one stretch — its current state first, then its
    window — but the two phases run far apart and take a meter each, so what joins them into one
    per-repository figure is `combined()` rather than a shared meter.

    The client's counter is shared by every repository, so a per-repository figure is the difference
    between two readings taken around that repository's work. Elapsed seconds come from a MONOTONIC
    clock and are never the difference of two wall-clock instants: a clock correction mid-run would
    otherwise report a repository as having taken a negative amount of time.
    """

    client: GitHubClient
    requests: int = field(default=0, init=False)
    elapsed: float = field(default=0.0, init=False)

    @contextmanager
    def measure(self) -> Iterator[None]:
        """Add one stretch of collection work to the running totals, whether or not it succeeded.

        The totals are updated even when the measured work raises: a repository that failed still
        spent the calls and the seconds it spent before failing, and a run stopped by a rate limit is
        precisely the run whose cost a reader needs to see.
        """
        issued = self.client.requests_issued
        started = elapsed()
        try:
            yield
        finally:
            self.requests += self.client.requests_issued - issued
            self.elapsed += elapsed() - started

    def cost(self, repository: str) -> RepositoryCollectionCost:
        """Report the running totals as evidence for one named repository."""
        return RepositoryCollectionCost(
            repository=repository,
            requests=self.requests,
            elapsed_seconds=self.elapsed,
        )


def combined(costs: Iterable[RepositoryCollectionCost]) -> tuple[RepositoryCollectionCost, ...]:
    """Sum every reading taken for the same repository and order the result slowest first.

    Summing rather than replacing, because the phases of a run are measured separately: current
    state is collected for every repository before any window is, so a repository's total is the
    readings from both. Ties break on the repository name so that two repositories which took the
    same time do not swap places between runs for no reason.
    """
    totals: dict[str, RepositoryCollectionCost] = {}
    for cost in costs:
        previous = totals.get(cost.repository)
        totals[cost.repository] = (
            cost
            if previous is None
            else previous.model_copy(
                update={
                    "requests": previous.requests + cost.requests,
                    "elapsed_seconds": previous.elapsed_seconds + cost.elapsed_seconds,
                },
            )
        )
    return tuple(sorted(totals.values(), key=lambda cost: (-cost.elapsed_seconds, cost.repository)))
