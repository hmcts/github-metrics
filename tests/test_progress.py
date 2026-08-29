"""Test the shared per-repository progress line."""

import pytest

from metrics.domain import RepositoryCollectionCost
from metrics.progress import log_progress


def test_log_progress_pads_the_position_to_the_width_of_the_total(caplog: pytest.LogCaptureFixture) -> None:
    """Pad the counter so that the names down a long run start in the same column."""
    cost = RepositoryCollectionCost(repository="cath-service", requests=37, elapsed_seconds=12.44)

    with caplog.at_level("INFO"):
        log_progress(7, 1850, cost, "state ok")

    assert caplog.messages == ["[   7/1850] cath-service  state ok (37 calls, 12.4s)"]


def test_log_progress_leaves_a_short_run_unpadded(caplog: pytest.LogCaptureFixture) -> None:
    """Spend no columns on padding a run has no use for."""
    cost = RepositoryCollectionCost(repository="opal-common-lib", requests=0, elapsed_seconds=0.0)

    with caplog.at_level("INFO"):
        log_progress(2, 3, cost, "window fully reused")

    assert caplog.messages == ["[2/3] opal-common-lib  window fully reused (0 calls, 0.0s)"]
