"""Resolve half-open reporting windows from user options."""

from datetime import UTC, datetime, timedelta

from metrics.domain import ReportingWindow


def parse_instant(value: str) -> datetime:
    """Parse an ISO 8601 date or datetime as a timezone-aware UTC instant."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exception:
        message = f"expected a date or datetime such as 2026-08-01, 2026-08-01T14:30 or 2026-08-01T14:30:00Z: {value}"
        raise ValueError(message) from exception
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def midnight(reference: datetime) -> datetime:
    """Return the most recent UTC midnight at or before an instant."""
    return reference.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def collected_anchor(collected_through: datetime | None, reference: datetime) -> datetime:
    """Return the midnight offline reporting should anchor at.

    A window ending where the caches end is reportable; one ending at today's midnight the day
    after a collection is not, because the last few hours of the collection's own day were never
    recorded as covered. Anchoring at the collection's edge means a span served the day after a run
    reports the same figures it did the day the run landed.

    Nothing collected falls back to the reference's midnight, so a cold cache still resolves a
    window and reports it as unavailable. The clamp is deliberate: a window recorded by a `--to` in
    the future must not anchor a report ahead of today.
    """
    floor = midnight(reference)
    return floor if collected_through is None else min(floor, midnight(collected_through))


def collection_is_stale(
    collected_through: datetime | None,
    reference: datetime,
    stale_after: timedelta,
) -> bool:
    """Return whether the collection the report is anchored at is older than the cadence allows.

    Figures anchored at an old collection are honest but easy to misread as current, so the service
    and the CLI say when the last run is further behind than `stale_after`. Nothing collected is
    stale too: there is no run to be current.
    """
    if collected_through is None:
        return True
    return reference - collected_through > stale_after


def baseline_window(enablement: datetime, span: timedelta) -> ReportingWindow:
    """Return the window of one period length ending at the enablement instant.

    One period, not several, so the before and the after are the same shape and comparing them needs
    no averaging. Half-open like every other window here: `[enablement - span, enablement)`.
    """
    return ReportingWindow(starts_at=enablement - span, ends_at=enablement)


def period_windows(
    enablement: datetime,
    span: timedelta,
    periods: int | None,
    reference: datetime,
) -> tuple[ReportingWindow, ...]:
    """Return each WHOLE period since enablement, the trailing partial period excluded.

    Whole periods are counted to the most recent UTC midnight, the anchor `resolve_window` already
    uses, so two runs on the same day resolve the same series. A short trailing period is excluded
    rather than reported: it is not comparable with a full one, and including it would show every
    series dipping at its right-hand edge for arithmetic reasons alone.

    `periods` caps the count from the ENABLEMENT end of the series, keeping the EARLIEST periods and
    dropping the recent ones: the question is what happened after enablement, and the baseline
    comparison reads from there. An enablement instant in the future yields no period rather than a
    negative count.
    """
    whole = max((midnight(reference) - enablement) // span, 0)
    count = whole if periods is None else min(periods, whole)
    return tuple(
        ReportingWindow(starts_at=enablement + index * span, ends_at=enablement + (index + 1) * span)
        for index in range(count)
    )


def resolve_window(
    starts_at: datetime | None,
    ends_at: datetime | None,
    days: int | None,
    default_days: int,
    reference: datetime,
) -> ReportingWindow:
    """Resolve a reporting window from at most two of its start, end, and span."""
    if days is not None and days < 1:
        message = "a reporting window must span at least one day"
        raise ValueError(message)
    if starts_at is not None and ends_at is not None and days is not None:
        message = "give at most two of --from, --to, and --days"
        raise ValueError(message)

    span = timedelta(days=default_days if days is None else days)
    anchor = midnight(reference)
    if starts_at is not None and ends_at is not None:
        resolved = (starts_at, ends_at)
    elif starts_at is not None:
        resolved = (starts_at, starts_at + span if days is not None else anchor)
    elif ends_at is not None:
        resolved = (ends_at - span, ends_at)
    else:
        resolved = (anchor - span, anchor)
    return ReportingWindow(starts_at=resolved[0], ends_at=resolved[1])
