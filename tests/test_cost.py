"""Test the per-repository collection cost instrument."""

from contextlib import AbstractContextManager
from unittest.mock import MagicMock, patch

import pytest
from requests import Session

from metrics.cost import CostMeter, combined
from metrics.domain import RepositoryCollectionCost
from metrics.github import GitHubClient


def fake_clock(*readings: float) -> AbstractContextManager[object]:
    """Replace the meter's monotonic source with the given readings, in order.

    The module-level clock is the meter's ONLY seam, and it is the same one the collection tests
    patch, so a unit test here and an end-to-end test there constrain the same code. Deterministic on
    purpose: a test that asserted on a real duration would assert on the machine it ran on, so every
    elapsed figure here is the difference between two numbers chosen by the test.
    """
    return patch("metrics.cost.monotonic", side_effect=readings)


def counting_client(calls: int) -> GitHubClient:
    """Build a client that has already issued the given number of calls."""
    client = GitHubClient("secret", Session())
    client.issued = calls
    return client


def test_the_meter_reports_the_calls_and_the_seconds_one_stretch_used() -> None:
    """Measure a repository as the difference between two readings of a shared client."""
    client = counting_client(17)
    meter = CostMeter(client)
    with fake_clock(100.0, 104.5), meter.measure():
        client.issued += 6

    assert meter.cost("nfdiv-case-api") == RepositoryCollectionCost(
        repository="nfdiv-case-api",
        requests=6,
        elapsed_seconds=4.5,
    )


def test_the_meter_adds_every_stretch_to_the_same_totals() -> None:
    """Accumulate the phases of one repository's collection rather than replacing them."""
    client = counting_client(0)
    meter = CostMeter(client)
    with fake_clock(10.0, 12.0, 20.0, 23.5):
        with meter.measure():
            client.issued += 2
        with meter.measure():
            client.issued += 3

    assert meter.cost("cath-service").requests == 5
    assert meter.cost("cath-service").elapsed_seconds == 5.5


def test_the_meter_records_the_cost_of_a_stretch_that_failed() -> None:
    """Report what a failed attempt spent, because that is the run a reader needs to see."""
    client = counting_client(0)
    meter = CostMeter(client)

    def refused() -> None:
        """Spend four calls and then fail, as a repository stopped by a rate limit does."""
        client.issued += 4
        message = "rate limited"
        raise RuntimeError(message)

    with fake_clock(0.0, 7.25), pytest.raises(RuntimeError, match="rate limited"), meter.measure():
        refused()

    assert meter.cost("opal-common-lib") == RepositoryCollectionCost(
        repository="opal-common-lib",
        requests=4,
        elapsed_seconds=7.25,
    )


def test_the_meter_reads_the_monotonic_clock_rather_than_the_wall_clock() -> None:
    """Take elapsed time from the monotonic clock rather than subtracting wall-clock instants.

    Asserted by replacing the monotonic source and reading the difference back: `elapsed >= 0` would
    hold for a wall-clock meter too, so it would prove nothing this test is named for.
    """
    meter = CostMeter(MagicMock(spec=GitHubClient, requests_issued=0))
    with fake_clock(100.0, 102.5), meter.measure():
        pass

    assert meter.elapsed == 2.5


def cost(repository: str, requests: int, elapsed: float) -> RepositoryCollectionCost:
    """Build one per-repository cost reading."""
    return RepositoryCollectionCost(repository=repository, requests=requests, elapsed_seconds=elapsed)


def test_combined_sums_every_reading_taken_for_one_repository() -> None:
    """Report a repository's total, not the last phase of it."""
    assert combined((cost("a", 3, 1.5), cost("b", 1, 0.5), cost("a", 4, 2.0))) == (
        cost("a", 7, 3.5),
        cost("b", 1, 0.5),
    )


def test_combined_orders_repositories_slowest_first() -> None:
    """Name the repository that dominates a run first, whatever order it was collected in."""
    ordered = combined((cost("quick", 2, 0.5), cost("slow", 90, 61.0), cost("middling", 12, 8.0)))

    assert tuple(item.repository for item in ordered) == ("slow", "middling", "quick")


def test_combined_breaks_ties_on_the_repository_name() -> None:
    """Keep two equally slow repositories in the same order between runs."""
    ordered = combined((cost("zebra", 1, 3.0), cost("aardvark", 1, 3.0), cost("mongoose", 1, 3.0)))

    assert tuple(item.repository for item in ordered) == ("aardvark", "mongoose", "zebra")


def test_combined_reports_nothing_for_no_readings() -> None:
    """Report an empty ordering rather than a row for a run that collected nothing."""
    assert combined(()) == ()
