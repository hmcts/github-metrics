# Plan: Anchor reporting windows to the last collection, not to today

## Overview
Every reporting window is anchored at `midnight(now)`, while a cached window is only reportable up to
the last `collect` run's stable edge. The day after a run, all 1697 repositories are therefore short
of coverage by the right-hand edge alone and the whole estate reports as `unavailable` at every span.
Anchor offline reporting — the HTTP service, `metrics evidence` without `--refresh`, and
`metrics trend --offline` — at the most recent midnight the caches actually cover to, and say on the
page and in the log when the collection behind it is older than the configured cadence.

## Context
- The cause, in three places:
  - `src/metrics/service.py:293` `reporting_window` ends every span at `midnight(reference)`, where
    `reference` is `datetime.now(UTC)` (`WindowCache.bundle`, line 488).
  - `src/metrics/evidence.py:569` `cached_repository_evidence` returns `EvidenceUnavailable` when
    `find_missing_cached_coverage` reports any uncovered interval, which the last few hours of
    "today" always is.
  - `src/metrics/behaviour.py:921` `fill_cached_source` records coverage for the STABLE side only —
    up to `mutable_edge` (`reference - lookback.mutable_hours`, clamped to the collection window's
    own end). So the cache's covered edge is the last run's window end, never "now".
- Measured against the working cache (`.metrics/metrics.sqlite3`, 2026-09-02):
  - Both sources' current query signature cover to `2026-09-01T00:00:00+00:00`; 1697 repositories are
    covered to that instant, 103 only to `2026-08-25` from an earlier run.
  - With the anchor at 2026-09-01, 1593 of 1697 repositories report at 1w, and 1593 at 4w, 8w and 12w
    — against zero today. The ~104 that were not in the last run stay `unavailable`, which is the
    honest answer and the existing meaning of that field.
  - `source_coverage` holds SEVEN pull-request query signatures and two commit signatures from older
    builds. The anchor must be read for the CURRENT signature only: a superseded signature's rows are
    not coverage this build can report from, and one of them reaching further ahead would set an
    anchor nothing current covers.
- Files involved:
  - `src/metrics/storage.py` — new read of the latest recorded coverage edge, beside
    `find_missing_cached_coverage`.
  - `src/metrics/window.py` — the two pure decisions: the anchor, and whether a collection is stale.
  - `src/metrics/behaviour.py` — `requested_coverage`'s inline `signatures` map lifted to a named
    function so the anchor query and the coverage request cannot use different signatures.
  - `src/metrics/config.py` — `LookbackConfiguration.stale_collection_days`.
  - `src/metrics/evidence.py` — `collected_through`, the one place both sources are reduced to a
    single instant.
  - `src/metrics/service.py` — `reporting_window`, `report_bundle`, `repository_series`,
    `resolve_periods`, `WindowCache`, `OverviewSummary`, `WindowOptions`, `serve_windows`.
  - `src/metrics/cli.py` — `emit_evidence` and `emit_trend` reference instants; `collect` untouched.
  - `ui/src/lib/types.ts`, new `ui/src/lib/collection.ts`, new
    `ui/src/components/CollectionNotice.tsx`, `ui/src/app/page.tsx` and the three detail pages.
  - `README.md`, `docs/architecture.md`, `metrics.example.yaml`.
- Decisions taken with the user on 2026-09-02:
  - The anchor is the LATEST instant the caches cover to — the last run's edge — not the latest
    instant every repository covers. A single repository that has not been collected for a month must
    not drag the whole estate's window back a month; it reports as `unavailable`, as it does now.
  - Collection runs weekly, so `stale_collection_days` defaults to 8: one missed run, not one missed
    day, is what the warning is for.
  - The staleness notice appears on every page, not only the overview, and is a warning bar rather
    than a quiet note.
  - `metrics collect` and `metrics evidence --refresh` still anchor at now. They are the runs that
    reach GitHub, and an anchor behind now would ask them to collect less than they can.
- Related patterns:
  - `window.py` holds pure window arithmetic and nothing else; storage reads go through `storage.py`
    and raise `StorageError`; the reference instant is passed in, never read at the point of use.
  - The service is offline always. A cache that cannot be read must degrade to "nothing collected"
    rather than fail a request, matching how `stored_reports` reports an unreadable row.
  - The UI mirrors the service's models by hand in `ui/src/lib/types.ts`; a value the service omits
    under `response_model_exclude_none` is mirrored as `field?: T` and guarded with `== null`.
  - No emoji anywhere in the UI. A state reaches the page as a colour plus a word (`ui/src/lib/rag.ts`).
- Dependencies: none.

## Development Approach
- Code then tests, per task, matching how the existing modules were built.
- Coverage is enforced at 100% (`fail_under`), so every branch added needs a test that reaches it.
- Complete each task fully before moving to the next.

## Validation Commands
- `uv run poe check`
- `npm --prefix ui run check`

## Implementation Steps

### Task 1: Read the latest recorded coverage edge from the cache
> Revised during code review, not with the user: the function shipped as
> `prevailing_cached_coverage` and returns the MODAL per-repository edge (`MAX(ends_at)` per
> repository, then the commonest of those, the later on a tie) rather than the greatest edge, because
> one `evidence --refresh --repository x` moving the estate's anchor a day forward blanks every other
> repository. See `docs/architecture.md`, "Offline reporting anchors at the last collection's edge".
> This supersedes the second bullet under "Decisions taken with the user" above and needs confirming.
- [x] Add `prevailing_cached_coverage(path: Path, organization: str, source: EvidenceSource, query_hash: str) -> datetime | None`
      to `src/metrics/storage.py`, beside `find_missing_cached_coverage`, returning the parsed modal
      per-repository `MAX(ends_at)` for exactly that organisation, source and signature, and `None`
      when no row matches.
- [x] Follow the neighbours' shape: `closing(connect(path))`, `prepare(connection)`, and
      `(Error, OSError)` wrapped as `StorageError("could not read collection cache: …")`. Do not
      touch `accessed_at`: reading the edge is not a use of any interval, and stamping it would keep
      dead signatures alive against `prune`.
- [x] Comment why the comparison is safe in SQL: `record_source_coverage` normalises every instant to
      a UTC `isoformat()`, so the stored text is fixed-width through the seconds and sorts
      chronologically.
- [x] Comment why `query_hash` is part of the filter, citing the superseded signatures the working
      cache still holds.
- [x] Write tests in `tests/test_storage.py`: `None` for an empty cache; the latest edge across
      several repositories and intervals; rows for another organisation, another source and another
      signature all ignored; `StorageError` for an unreadable path.
- [x] Run `uv run poe check` — must pass before task 2.

### Task 2: Name the two pure decisions in `window.py`
- [x] Add `collected_anchor(collected_through: datetime | None, reference: datetime) -> datetime`:
      `midnight(reference)` when nothing has been collected, otherwise
      `min(midnight(reference), midnight(collected_through))`. The clamp is deliberate — a window
      recorded by a `--to` in the future must not anchor a report ahead of today.
- [x] Add `collection_is_stale(collected_through: datetime | None, reference: datetime, stale_after: timedelta) -> bool`,
      true when nothing has been collected or `reference - collected_through > stale_after`.
- [x] Docstring both with the reason the anchor exists: a window ending where the caches end is
      reportable, and one ending at today's midnight the day after a run is not.
- [x] Write tests in `tests/test_window.py`: no collection falls back to the reference's midnight; an
      edge earlier the same day floors to that day's midnight; an edge before the reference anchors
      to its own midnight; a future edge is clamped; staleness at, one second past, and well inside
      the threshold, and with nothing collected.
- [x] Run `uv run poe check` — must pass before task 3.

### Task 3: Configure the collection cadence the warning is measured against
- [x] Add `stale_collection_days: PositiveInt = 8` to `LookbackConfiguration` in
      `src/metrics/config.py`, with a comment saying eight days is one missed weekly run rather than
      one missed day.
- [x] Add the key to `metrics.example.yaml`'s `lookback` block and to the configuration block in
      `README.md`, describing it as how old the last collection may be before the service and the CLI
      say so.
- [x] Write tests in `tests/test_config.py`: the default; an override; `0` rejected as it is for every
      other `PositiveInt`; an unknown neighbouring key still rejected.
- [x] Run `uv run poe check` — must pass before task 4.

### Task 4: Reduce both sources to one collected instant
- [x] In `src/metrics/behaviour.py`, lift the `signatures` map inside `requested_coverage` to a
      module-level `source_signature(source: EvidenceSource) -> str` and call it from
      `requested_coverage`, so the anchor query and the coverage request cannot diverge.
- [x] Add `collected_through(configuration: Configuration) -> datetime | None` to
      `src/metrics/evidence.py`: read `prevailing_cached_coverage` for `EvidenceSource.PULL_REQUEST` and
      `EvidenceSource.COMMIT` at `source_signature`, and return the EARLIER of the two — a window
      needs both sources, so an instant only one of them reaches is not covered.
- [x] Return `None` when either source has no coverage, and when the cache raises `StorageError`,
      logging the failure with a root `logging.warning` and lazy formatting. A cache that cannot be
      read reports as nothing collected, so a page says "not reported" rather than failing.
- [x] Write tests in `tests/test_evidence.py`: both sources collected returns the earlier edge; one
      source missing returns `None`; a superseded signature's later edge is ignored; an unreadable
      cache returns `None` and logs.
- [x] Run `uv run poe check` — must pass before task 5.

### Task 5: Anchor every span and every series the service builds
- [x] Change `reporting_window(weeks: int, anchor: datetime)` in `src/metrics/service.py` to take the
      resolved anchor rather than a reference instant, and update its docstring: the window ends where
      the caches end, so a span served the day after a collection reports the same figures it did the
      day the collection landed.
- [x] Read `collected_through(configuration)` once per build in `report_bundle`, AFTER the source
      stamp, and resolve the window through `collected_anchor(...)`. Ordering matters: a bundle may be
      older than the caches, never newer than the stamp it will be invalidated by.
- [x] Carry `collected_through: datetime | None` on `ReportBundle`, and add `collected_through` to
      `OverviewSummary` so the header prints the window beside the collection it is anchored to.
- [x] Pass the same anchor as the `SeriesRequest.reference` in `repository_series`, and use it in
      `resolve_periods` in place of `datetime.now(UTC)`, so the count a request is refused for is the
      count that would have been built and no trailing period reaches past the caches.
- [x] Add `collected_through: datetime | None` and `collection_stale: bool` to `WindowOptions`, and
      compute them in `serve_windows` from a fresh `collected_through` read and
      `collection_is_stale(..., timedelta(days=configuration.lookback.stale_collection_days))`.
      `/windows` is what every page already fetches for the span selector, which is why the notice
      every page shows is published there; extend the model's docstring to say so.
- [x] Write tests in `tests/test_service.py`: a cache covering to the previous midnight serves a
      window ending there at every offered span, with the repositories reported rather than
      unavailable; a cold cache still anchors at today's midnight and reports everything unavailable;
      `collected_through` appears on the overview and is absent when nothing is collected;
      `/windows` reports `collection_stale` either side of the threshold; a series is cut against the
      collected anchor, not against now.
- [x] Run `uv run poe check` — must pass before task 6.

### Task 6: Anchor the offline CLI the same way
- [x] In `src/metrics/cli.py`, resolve `emit_evidence`'s window against
      `collected_anchor(collected_through(configuration), datetime.now(UTC))` when `--refresh` was
      not given, and against `datetime.now(UTC)` when it was. Keep `reference` — the instant a fresh
      observation is stamped with — as now in both cases; only the window's anchor moves.
- [x] In `emit_trend`, build the `SeriesRequest` reference from the collected anchor when `--offline`
      was given, and from now otherwise.
- [x] Log at INFO, once per run, when the anchor is behind today's midnight, naming the instant the
      caches cover to; log a WARNING when `collection_is_stale`, naming the last collection and the
      configured threshold. Both through root `logging` with lazy formatting.
- [x] Leave `collect` alone, and add a test that pins it: a collection still fetches up to now.
- [x] Write tests in `tests/test_cli.py`: `evidence` against a cache covering to the previous midnight
      prints that window and reports the repository rather than an unavailable entry; `--refresh`
      still anchors at now; `trend --offline` cuts its periods against the collected anchor; the
      stale warning is logged and the INFO line names the collected instant.
- [x] Run `uv run poe check` — must pass before task 7.

### Task 7: Mirror the collection state in the UI and show it
- [x] Mirror the new fields in `ui/src/lib/types.ts`: `collected_through?: string` and
      `collection_stale: boolean` on `WindowOptions`, `collected_through?: string` on
      `OverviewSummary`.
- [x] Add `ui/src/lib/collection.ts` with the pure text: a function returning the notice for a stale
      collection — the day it was collected and that figures cover the window ending there — and
      `null` when the collection is current, plus the "Collected through <day>" label the header
      prints. Reuse `day`/`instant` from `ui/src/lib/format.ts` rather than formatting dates again.
- [x] Add `ui/src/components/CollectionNotice.tsx`, a server component rendering an amber-bordered bar
      from that text and nothing at all when there is no notice. No emoji: a colour bar plus the
      words, as `rag.ts` requires.
- [x] Render the notice above the content on `ui/src/app/page.tsx` and on the repository, team and
      actor pages, each of which already awaits `getWindows()`, and add the "Collected through" label
      to the overview header beside the window span.
- [x] Write tests in `ui/src/lib/__tests__/collection.test.ts` for the text either side of staleness
      and with nothing collected, and a `ui/src/components/__tests__` case that the bar renders when
      stale and renders nothing when not.
- [x] Run `npm --prefix ui run check` — must pass before task 8.

### Task 8: Record the decision and verify acceptance criteria
- [x] Add a dated entry to `docs/architecture.md` recording that offline reporting anchors at the last
      collection's edge rather than at now, why the latest edge was chosen over the edge every
      repository shares, and that `collect` and `--refresh` still anchor at now.
- [x] Update `README.md` where the service, the UI header and `metrics evidence` describe their
      window, so the documented behaviour matches: a window ends where the caches end, and a
      repository missed by the last run is reported as unavailable at that window.
- [x] Run `uv run poe check`. 1083 passed, coverage 100%.
- [x] Run `npm --prefix ui run check`. Lint, typecheck and 251 tests pass. `next build` fails in the
      sandbox at the `output: 'standalone'` trace copy with `ENOTDIR`/`ENOENT` on directories that
      exist — a mounted-host-filesystem artifact, not a code failure: the identical tree builds clean
      when `.next` is written outside the mount.
