# Plan: Confirmed-coverage anchor and the scheduled collection window

## Overview
Make the estate's reporting anchor move only when a collection run finishes, so starting a run no longer takes most
repositories out of the "current" span, and give an ad-hoc run `--hold-anchor` so it moves nothing at all. Document
`--days N` as the mechanism scheduled collection uses in live service.

## Context
- Files involved: `src/metrics/storage.py`, `src/metrics/evidence.py`, `src/metrics/cli.py`, `docs/architecture.md`,
  `README.md`, `tests/test_storage.py`, `tests/test_evidence.py`, `tests/test_cli.py`
- `prevailing_cached_coverage` (`storage.py:354`) takes the modal per-repository edge over `source_coverage`.
  `docs/architecture.md:1226` already names the failure this plan closes and points at the fix: "A run's own completion
  marker could, and is where to look if this ever bites."
- No new window flag: `--days N` already resolves `[midnight(now) - N days, midnight(now))` in
  `window.py:resolve_window`, shared by `collect`, `evidence` and `trend`. Bullet one of the request is documentation.
- The cache carries no migrations (`storage.py:1`), so the new table is created by `initialize` and needs no upgrade
  path. The existing working cache must keep reporting through the change, which is what the fallback in Task 2 is for.
- Related patterns: `collected_through` (`evidence.py:573`) is where the two sources and their signatures are reduced to
  one instant; `report_anchored_at` (`cli.py:298`) is the precedent for a run saying out loud where it landed.
- Dependencies: none. The cache moves to PostgreSQL later, so standard SQL only —
  `INSERT ... ON CONFLICT ... DO UPDATE`, no `INSERT OR REPLACE`, and no unbounded `IN (...)` list: filter the
  repository set in Python.

## Development Approach
- Code then tests, per task. Coverage is enforced at 100% (`fail_under = 100`), so every branch needs a test.
- Complete each task fully before moving to the next.

## Validation Commands
- `uv run poe check`
- `uv run poe cover`

## Implementation Steps

### Task 1: Record what a finished run confirms
- [x] Add a `confirmed_coverage` table to `initialize` in `storage.py`: `(organization, repository, source,
      query_hash, ends_at, confirmed_at)`, primary key `(organization, repository, source, query_hash)`
- [x] Add `confirm_collection_coverage(path, organization, repositories, signatures, confirmed_at)`: read
      `MAX(ends_at)` per repository per `(source, query_hash)` from `source_coverage`, keep the rows for the named
      repositories, and upsert them in one transaction with `INSERT ... ON CONFLICT ... DO UPDATE`; degrade a failure to
      `StorageError` as its neighbours do
- [x] Docstring the two decisions: it is called at the END of a run, and it copies each repository's ACTUAL cached edge
      rather than the run's intended one — so a run that never reached a repository re-confirms that repository's old
      edge and cannot carry the estate's anchor forward on its behalf
- [x] Write tests: first confirmation on a fresh cache; a named repository with no coverage rows is skipped rather than
      stored null; a second run updates a row in place; both sources are snapshotted; an unreadable cache raises
      `StorageError`
- [x] Run the project test suite - must pass before task 2

### Task 2: Anchor at confirmed coverage
- [x] Rewrite `prevailing_cached_coverage` to take the mode over `confirmed_coverage` for the current signature — one
      row per repository already, so no inner `GROUP BY` — with the later edge on a tie
- [x] Keep the `source_coverage` mode as a fallback used only when the signature has no confirmed rows at all: the
      existing working cache, and a signature whose first run has not finished. Document that it self-heals at the first
      completed run, and that under a fresh signature the repositories behind the fallback edge have no coverage to
      report at any anchor
- [x] Update the docstring: the anchor is the edge the last completed run confirmed, so an in-progress `collect`, a
      `collect` that dies part way, `evidence --refresh --repository x` and a collecting `trend` all move nothing
- [x] Update `collected_through`'s docstring in `evidence.py` and add `confirm_collection(configuration, repositories,
      reference)` beside it, so the two sources and their signatures stay named in one place
- [x] Write tests: the mode ignores an in-progress `source_coverage` edge; a tie takes the later edge; the fallback
      fires with no confirmed rows and stops firing once there are some; a superseded signature is ignored;
      `collected_through` still takes the earlier of the two sources
- [x] Run the project test suite - must pass before task 3

### Task 3: Confirm at the end of a completed collect run
- [x] Call `confirm_collection` in `collect_evidence` after `record_repository_state` and `record_alert_observations`,
      for `configured_repositories(configuration)` — inside the `try`, never the `finally`, so an interrupted run
      confirms nothing
- [x] Add `--hold-anchor` to the `collect` subparser: a run given it collects and stores exactly as usual but skips the
      confirmation, so an ad-hoc run over any part of the estate leaves the reported window where it is. Only `collect`
      takes it; nothing else confirms
- [x] Log one INFO line naming the edge the run confirmed, or saying the anchor was held at the edge it was already at
- [x] Write tests: a completed run moves the anchor; `--hold-anchor` leaves it where it was; a run that dies mid-estate
      leaves it where it was; a run whose repositories mostly failed does not carry it forward; a partial run over a
      handful of repositories leaves the estate's anchor alone; a `StorageError` here is reported and exits 1
- [x] Run the project test suite - must pass before task 4

### Task 4: Prune orphaned confirmed rows
- [x] In `prune_cache`, delete `confirmed_coverage` rows with no surviving `source_coverage` row for the same
      `(organization, repository, source, query_hash)`, so a dead signature or a de-configured repository cannot win the
      mode with an old edge
- [x] Write tests: a dead signature's confirmed row is deleted, a live one is kept, and the returned count still counts
      intervals only
- [x] Run the project test suite - must pass before task 5

### Task 5: Document the scheduled window and the new anchor
- [x] README: scheduled collection runs `metrics collect --config ... --days N`, 14 in live service; `--from`/`--to` is
      for a backfill; the end is the most recent UTC midnight, so a run repeated inside a day asks for the same window
- [x] README: a 14-day top-up keeps the 12-week spans reportable, because a collecting run re-stamps the repository's
      whole coverage and `prune --days 30` therefore does not age the older intervals out
- [x] README: the anchor is the last completed run's confirmed edge, so starting a collection no longer changes which
      repositories the dashboard can report, and `--hold-anchor` is how an ad-hoc run keeps it that way
- [x] `docs/architecture.md`: amend "Offline reporting anchors at the last collection's edge (decided 2026-09-02)" with
      a dated 2026-09-04 amendment closing the residual case it names, recording what `--hold-anchor` is for and what
      remains: a majority-subset run that finishes without the flag still carries the mode forward. Add the
      scheduled-window line to "Windows and time"
- [x] Run the project test suite - must pass before task 6

### Task 6: Verify acceptance criteria
- [x] Run `uv run poe check` and confirm lint, format, strict mypy and pytest all pass
- [x] Confirm coverage is still 100%
- [x] Re-read the anchor's docstrings against the code, so no docstring claims behaviour the tests do not hold
