"""Report where a collection has reached, one line per repository per phase.

A run visits every configured repository twice — current state first, then the window — and at 1850
repositories each pass is long enough that a run printing nothing until it finishes cannot be told
apart from a run that has hung. The format lives here rather than in either phase so that the two
pad and order it identically, and so that a third caller joins the same sequence instead of
inventing a shape of its own.

This module reports; it decides nothing. What a phase found is phrased by the phase that found it
and arrives here as a finished clause.
"""

import logging

from metrics.domain import RepositoryCollectionCost


def log_progress(position: int, total: int, cost: RepositoryCollectionCost, outcome: str) -> None:
    """Log one repository's place in a phase, what the phase established, and what it cost.

    The position is padded to the width of the total so that the names down a run's output start in
    the same column and can be read as a list, and the total is repeated on every line because a
    reader who joins a run partway through has not seen its first one.

    One `RepositoryCollectionCost` carries the repository and both figures, because it is the reading
    the phase already took for its own report: a line built from the same reading cannot disagree
    with the `costs` table it will be read beside.
    """
    logging.info(
        "[%*d/%d] %s  %s (%d calls, %.1fs)",
        len(str(total)),
        position,
        total,
        cost.repository,
        outcome,
        cost.requests,
        cost.elapsed_seconds,
    )
