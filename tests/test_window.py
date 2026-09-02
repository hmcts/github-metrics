"""Test reporting window resolution and instant parsing."""

from datetime import UTC, datetime, timedelta

import pytest

from metrics.window import (
    baseline_window,
    collected_anchor,
    collection_is_stale,
    midnight,
    parse_instant,
    period_windows,
    resolve_window,
)


def reference() -> datetime:
    """Return a fixed mid-afternoon instant to anchor relative windows."""
    return datetime(2026, 8, 8, 14, 58, 46, tzinfo=UTC)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-08-01", datetime(2026, 8, 1, tzinfo=UTC)),
        ("2026-08-01T14:30", datetime(2026, 8, 1, 14, 30, tzinfo=UTC)),
        ("2026-08-01T14:30:00Z", datetime(2026, 8, 1, 14, 30, tzinfo=UTC)),
        ("2026-08-01T14:30:00+01:00", datetime(2026, 8, 1, 13, 30, tzinfo=UTC)),
    ],
)
def test_parse_instant_accepts_dates_and_datetimes(value: str, expected: datetime) -> None:
    """Treat a bare date as midnight and a naive datetime as UTC."""
    assert parse_instant(value) == expected


def test_parse_instant_rejects_an_unusable_value() -> None:
    """Name the accepted forms when a boundary cannot be parsed."""
    with pytest.raises(ValueError, match="expected a date or datetime"):
        parse_instant("last tuesday")


def test_midnight_truncates_to_the_most_recent_day_boundary() -> None:
    """Anchor relative windows to a stable instant rather than the current time."""
    assert midnight(reference()) == datetime(2026, 8, 8, tzinfo=UTC)


@pytest.mark.parametrize(
    ("starts_at", "ends_at", "days", "expected"),
    [
        (None, None, None, (datetime(2026, 5, 10, tzinfo=UTC), datetime(2026, 8, 8, tzinfo=UTC))),
        (None, None, 7, (datetime(2026, 8, 1, tzinfo=UTC), datetime(2026, 8, 8, tzinfo=UTC))),
        (
            datetime(2026, 8, 1, tzinfo=UTC),
            datetime(2026, 8, 5, tzinfo=UTC),
            None,
            (datetime(2026, 8, 1, tzinfo=UTC), datetime(2026, 8, 5, tzinfo=UTC)),
        ),
        (
            datetime(2026, 8, 1, tzinfo=UTC),
            None,
            2,
            (datetime(2026, 8, 1, tzinfo=UTC), datetime(2026, 8, 3, tzinfo=UTC)),
        ),
        (
            datetime(2026, 8, 1, tzinfo=UTC),
            None,
            None,
            (datetime(2026, 8, 1, tzinfo=UTC), datetime(2026, 8, 8, tzinfo=UTC)),
        ),
        (
            None,
            datetime(2026, 8, 5, tzinfo=UTC),
            2,
            (datetime(2026, 8, 3, tzinfo=UTC), datetime(2026, 8, 5, tzinfo=UTC)),
        ),
        (
            None,
            datetime(2026, 8, 5, tzinfo=UTC),
            None,
            (datetime(2026, 5, 7, tzinfo=UTC), datetime(2026, 8, 5, tzinfo=UTC)),
        ),
    ],
)
def test_resolve_window_derives_a_half_open_interval(
    starts_at: datetime | None,
    ends_at: datetime | None,
    days: int | None,
    expected: tuple[datetime, datetime],
) -> None:
    """Resolve every supported combination of start, end, and span."""
    window = resolve_window(starts_at, ends_at, days, 90, reference())

    assert (window.starts_at, window.ends_at) == expected


def test_resolve_window_excludes_its_end_instant() -> None:
    """Cover whole days without counting the day named by the end boundary."""
    window = resolve_window(
        datetime(2026, 8, 1, tzinfo=UTC),
        datetime(2026, 8, 8, tzinfo=UTC),
        None,
        90,
        reference(),
    )

    assert window.ends_at - window.starts_at == timedelta(days=7)


@pytest.mark.parametrize(
    ("starts_at", "ends_at", "days", "message"),
    [
        (None, None, 0, "at least one day"),
        (
            datetime(2026, 8, 1, tzinfo=UTC),
            datetime(2026, 8, 5, tzinfo=UTC),
            2,
            "at most two of --from, --to, and --days",
        ),
        (
            datetime(2026, 8, 5, tzinfo=UTC),
            datetime(2026, 8, 1, tzinfo=UTC),
            None,
            "end must follow its start",
        ),
    ],
)
def test_resolve_window_rejects_an_unusable_request(
    starts_at: datetime | None,
    ends_at: datetime | None,
    days: int | None,
    message: str,
) -> None:
    """Refuse a contradictory, empty, or reversed window."""
    with pytest.raises(ValueError, match=message):
        resolve_window(starts_at, ends_at, days, 90, reference())


def test_collected_anchor_falls_back_to_the_reference_when_nothing_is_collected() -> None:
    """Resolve a window from a cold cache rather than refusing to report at all."""
    assert collected_anchor(None, reference()) == datetime(2026, 8, 8, tzinfo=UTC)


@pytest.mark.parametrize(
    ("collected_through", "expected"),
    [
        (datetime(2026, 8, 8, 6, 0, tzinfo=UTC), datetime(2026, 8, 8, tzinfo=UTC)),
        (datetime(2026, 8, 7, 9, 15, tzinfo=UTC), datetime(2026, 8, 7, tzinfo=UTC)),
        (datetime(2026, 8, 1, tzinfo=UTC), datetime(2026, 8, 1, tzinfo=UTC)),
        (datetime(2026, 8, 12, tzinfo=UTC), datetime(2026, 8, 8, tzinfo=UTC)),
    ],
)
def test_collected_anchor_floors_the_collected_edge(
    collected_through: datetime,
    expected: datetime,
) -> None:
    """Anchor at the collected edge's own midnight, never later than today's."""
    assert collected_anchor(collected_through, reference()) == expected


@pytest.mark.parametrize(
    ("collected_through", "expected"),
    [
        (None, True),
        (datetime(2026, 7, 31, 14, 58, 46, tzinfo=UTC), False),
        (datetime(2026, 7, 31, 14, 58, 45, tzinfo=UTC), True),
        (datetime(2026, 8, 7, tzinfo=UTC), False),
    ],
)
def test_collection_is_stale_measures_the_gap_against_the_cadence(
    collected_through: datetime | None,
    expected: bool,  # noqa: FBT001 - parametrised expectation
) -> None:
    """Call a collection stale only once it is further behind than the cadence allows."""
    assert collection_is_stale(collected_through, reference(), timedelta(days=8)) is expected


def enablement() -> datetime:
    """Return an enablement instant sitting a whole number of periods before the reference."""
    return datetime(2026, 5, 3, tzinfo=UTC)


def test_baseline_window_ends_at_the_enablement_instant() -> None:
    """Measure the before against exactly one period ending where the after begins."""
    window = baseline_window(enablement(), timedelta(days=28))

    assert window.starts_at == datetime(2026, 4, 5, tzinfo=UTC)
    assert window.ends_at == enablement()


def test_period_windows_are_consecutive_and_half_open() -> None:
    """Cut the series into equal periods that meet without overlapping."""
    windows = period_windows(enablement(), timedelta(days=28), None, reference())

    assert tuple((window.starts_at, window.ends_at) for window in windows) == (
        (datetime(2026, 5, 3, tzinfo=UTC), datetime(2026, 5, 31, tzinfo=UTC)),
        (datetime(2026, 5, 31, tzinfo=UTC), datetime(2026, 6, 28, tzinfo=UTC)),
        (datetime(2026, 6, 28, tzinfo=UTC), datetime(2026, 7, 26, tzinfo=UTC)),
    )


def test_period_windows_exclude_the_trailing_partial_period() -> None:
    """Stop at the last WHOLE period: a short one is not comparable with a full one.

    97 days separate this enablement instant from the reference midnight, so three 28-day periods
    are whole and the 13 days after them are not a period at all.
    """
    windows = period_windows(enablement(), timedelta(days=28), None, reference())

    assert len(windows) == 3
    assert windows[-1].ends_at + timedelta(days=28) > midnight(reference())


def test_period_windows_keep_the_enablement_time_of_day() -> None:
    """Anchor every period to the enablement instant rather than to a midnight near it."""
    anchored = datetime(2026, 5, 3, 9, 30, tzinfo=UTC)

    windows = period_windows(anchored, timedelta(days=28), None, reference())

    assert windows[0].starts_at == anchored
    assert windows[1].starts_at == anchored + timedelta(days=28)
    assert all(window.ends_at.hour == 9 for window in windows)


def test_period_windows_cap_the_series_at_the_requested_count() -> None:
    """Report the periods nearest enablement when a shorter series is asked for."""
    windows = period_windows(enablement(), timedelta(days=28), 2, reference())

    assert tuple(window.starts_at for window in windows) == (
        datetime(2026, 5, 3, tzinfo=UTC),
        datetime(2026, 5, 31, tzinfo=UTC),
    )


def test_period_windows_report_every_whole_period_when_fewer_exist_than_requested() -> None:
    """Ask for more periods than have elapsed and get the whole ones, never an invented window."""
    assert len(period_windows(enablement(), timedelta(days=28), 9, reference())) == 3


@pytest.mark.parametrize(
    "anchor",
    [datetime(2026, 8, 8, tzinfo=UTC), datetime(2026, 9, 1, tzinfo=UTC)],
)
def test_period_windows_report_nothing_before_a_whole_period_has_elapsed(anchor: datetime) -> None:
    """Report no period for an anchor that is too recent, and none for one in the future."""
    assert period_windows(anchor, timedelta(days=28), None, reference()) == ()
