# Plan: Remove the deprecated security hotspot metrics

## Overview

Sonar is transitioning security hotspots into vulnerabilities, so `security_hotspots` and
`security_review_rating` are on the way out. Remove both end-to-end — collector, domain model, served
row, text report, SonarCloud tab and the security donut — so nothing reads a metric Sonar has stopped
standing behind.

## Context

- Files involved:
  - `src/metrics/sonar.py`, `src/metrics/domain.py`, `src/metrics/service.py`, `src/metrics/render.py`
  - `tests/test_sonar.py`, `tests/test_domain.py`, `tests/test_service.py`, `tests/test_render.py`,
    `tests/test_evidence.py`
  - `ui/src/lib/types.ts`, `ui/src/lib/tone.ts`, `ui/src/lib/repository.ts`
  - `ui/src/lib/__tests__/tone.test.ts`, `ui/src/lib/__tests__/chart.test.ts`,
    `ui/src/lib/__tests__/rows.test.ts`, `ui/src/lib/__tests__/repository.test.ts`,
    `ui/src/components/__tests__/list-pages.test.ts`
  - `README.md`, `ui/README.md`, `docs/architecture.md`

- THIS IS OUR CHOICE, NOT A FORCED MIGRATION. The deprecation page
  (<https://docs.sonarsource.com/sonarqube-cloud/deprecations-and-removals>) says only that rules
  which raised security hotspots "will start raising vulnerabilities (type) or security issues
  (software quality)". It names no removal date and does not list either metric key as dead, and both
  keys still resolve today. Do not add fallbacks, shims or a deprecation warning — just stop reading
  them.

- THE METRIC IS ALREADY INERT ON THIS ESTATE. Measured 2026-09-04 against a collected snapshot: of
  the 266 repositories reporting `security_hotspots`, all 266 report ZERO. Removing it from the donut
  moves no repository between bands, which is what Task 6 pins. The card being removed could only ever
  have read `0`.

- THE TWO FIELDS STAY ON `SonarMeasures`, DELIBERATELY, AND ARE SIMPLY NEVER POPULATED AGAIN. This is
  the whole reason the plan has no field-deletion task. `SonarMeasures` is an `EvidenceModel`,
  validated `extra="forbid"`, and it is nested inside the `RepositoryInventoryItem` that
  `collect` serialises into `repository_state.payload`. Every payload already written carries
  `sonar.security_hotspots` and `sonar.security_review_rating`, so DELETING the fields makes those
  rows fail validation — measured on the real nested model on 2026-09-04, which rejected exactly
  `('sonar', 'security_hotspots')` and `('sonar', 'security_review_rating')`. Retaining them costs two
  field declarations that are always `None` for anything collected from now on, and it means no cached
  row is ever invalidated.
  This is SYMMETRIC WITH AN EXISTING RULE rather than a new exception: `RepositoryInventoryItem`'s
  docstring already records that its own optional fields "default to None so a row stored before they
  existed still parses". A field retained so an OLD row still parses is the same rule pointing
  backwards.
  Do NOT set `extra="ignore"` on `SonarMeasures` to achieve this instead — that weakens a guard the
  codebase leans on deliberately (`load_repository_state`'s docstring depends on it to catch payloads
  from a newer build) and it would silently accept genuine typos. And do NOT delete the fields and lean
  on `collect --refresh` to repair the cache: it works, but it turns a metric removal into an estate-wide
  recollection, which is a cost nobody asked for.

- THE NOTHING-FURTHER RULE IS WHAT ACTUALLY MATTERS. The point of this plan is that the two values
  reach NO reader: not the text report, not the served row, not the SonarCloud tab, not the donut. A
  retained field that stays unread is the goal; a retained field that quietly acquires a new caller is
  the failure. Task 4 pins this with a test rather than trusting it.

- `SONAR_METRIC_KEYS` IS A GUARDED CONSTANT because one bad key nulls every measure for the whole
  call — HTTP 200, `measures: null`, no error text, every other metric silently discarded. REMOVING
  keys is safe; adding or renaming is not. Leave the docstring's warning intact.

- Related patterns:
  - The SonarCloud tab is a flat card list built by `sonarMeasureCards` in `ui/src/lib/repository.ts`.
    Every card's tone comes from `tone.ts` and no component holds a boundary, so a card is removed by
    deleting its entry and the tone-table entry behind it.
  - Sonar COUNTS are graded by their own rating through `issueTone`, never by the count alone. That is
    the only reason `security_review_rating` exists anywhere in the UI — it is the rating Sonar grades
    hotspots with — so it leaves with the count it graded and has no other caller.
  - Optional fields on `RepositoryRow` are mirrored in `ui/src/lib/types.ts` as `field?: T`, never
    `T | null`, because every service route is registered `response_model_exclude_none` and an absent
    value arrives as a MISSING KEY. Removing a field follows the same contract: absent means unmeasured.

- Dependencies: none. Nothing is added.

- Preconditions: satisfied. The C-to-amber security banding change landed as commit `2052dcc`
  ("fix: change security banding"), which touched `securityBand`, its docstring and `tone.test.ts` —
  all of which Task 6 edits again. Start from a clean tree at or after that commit.

## Development Approach

- Code then tests, per task; every task leaves both gates green before the next begins.
- The removal is ORDERED SO THE TREE IS NEVER BROKEN MID-TASK: stop collecting first, then drop each
  reader one at a time. Nothing is deleted from the domain model at all, so no task can leave
  `sonar.py`, `render.py` and `service.py` referencing a field that no longer exists.
- Complete each task fully before moving to the next.
- `npm --prefix ui run check` is NOT a validation command here: it runs `next build`, which fails
  writing chunks on this mount. Lint, typecheck and tests are the real gate.
- Both gates enforce 100% coverage (`fail_under = 100` for pytest, and `vitest.config.mts` pins
  statements, branches, functions and lines at 100% for `ui/src/**`). A removal must take its tests
  with it or coverage fails on the lines left behind.

## Validation Commands

- `uv run poe check`
- `npm --prefix ui run lint`
- `npm --prefix ui run typecheck`
- `npm --prefix ui run test`

## Implementation Steps

### Task 1: Stop requesting the two metrics

- [x] Remove `"security_hotspots"` and `"security_review_rating"` from `SONAR_METRIC_KEYS` in
      `src/metrics/sonar.py`, leaving the docstring's whole-set-lost-to-one-bad-key warning in place
- [x] Remove the `security_hotspots=` and `security_review_rating=` arguments from the `SonarMeasures`
      construction in `build_measures`, so both fields fall to their `None` defaults for everything
      collected from now on. The fields THEMSELVES stay — see the Context note on why
- [x] Update `tests/test_sonar.py`: drop both keys from the requested-key assertion, drop the two stub
      measure values from the response fixture, and drop the two field assertions
- [x] run `uv run poe check` - must pass before task 2

### Task 2: Drop the two rows from the evidence text report

- [x] Remove the `Security hotspots` and `Security review rating` rows from the Sonar block in
      `src/metrics/render.py`
- [x] Update `tests/test_render.py`: remove both fields from the measures fixture and assert neither
      label appears in the rendered block
- [x] run `uv run poe check` - must pass before task 3

### Task 3: Drop the hotspot count from the served row

- [x] Remove `sonar_security_hotspots` from `RepositoryRow` in `src/metrics/service.py` and from the
      `repository_row` construction
- [x] Remove `sonar_security_hotspots` from `RepositoryRow` in `ui/src/lib/types.ts`, keeping the
      mirror exact
- [x] Update `tests/test_service.py`: drop the `hotspots` argument from the measures fixture and
      replace the four `sonar_security_hotspots` assertions with one asserting the key is absent from
      a reported row
- [x] run `uv run poe check` - must pass before task 4
- [x] Dropping the field from the `types.ts` mirror broke the UI typecheck on three row fixtures that
      set it, so Task 6's fixture edit to `chart.test.ts`, `rows.test.ts` and `list-pages.test.ts`
      was pulled forward to here — the tree cannot be left with a red UI gate for three tasks. All
      four gates are green

### Task 4: Retire the domain fields without removing them

- [x] Keep `security_hotspots` and `security_review_rating` on `SonarMeasures` in
      `src/metrics/domain.py`, and add a docstring note stating that both were RETIRED on 2026-09-04
      as deprecated by Sonar: they are no longer requested and no reader consumes them, and they are
      retained ONLY so a `repository_state` payload written before that date still parses under
      `extra="forbid"`. Say plainly that anything collected after that date carries `None` for both,
      and that a future reader wanting to delete them must first accept invalidating every cached row
- [x] Add a test in `tests/test_domain.py` asserting a stored payload carrying BOTH keys still parses
      and reads back their values, so the backward-parsing guarantee is pinned and cannot be broken by
      a later tidy-up
- [x] Add a test asserting that measures built from a Sonar response are `None` for both fields, so
      "no longer collected" is pinned separately from "still parseable". It lives in
      `tests/test_sonar.py`, beside the other `component_measures` tests, and feeds a response that
      still carries both metrics so the assertion is about the builder rather than the fixture
- [x] Update `tests/test_evidence.py` so its fixtures no longer SET either field, leaving the
      round-trip test added above as the one place they appear. Its round-trip assertion moved to
      `security_rating`, which still exercises a rating surviving JSON storage
- [x] run `uv run poe check` - must pass before task 5

### Task 5: Remove the two SonarCloud tab cards

- [x] Remove the `Security hotspots` and `Security review rating` cards from `sonarMeasureCards` in
      `ui/src/lib/repository.ts` (the function is named `sonarRows`; its docstring's count of the
      toned measures went from eight to seven with them)
- [x] Remove `security_hotspots` and `security_review_rating` from `SonarMeasures` in
      `ui/src/lib/types.ts`
- [x] Remove `'security_hotspots'` from the `SonarMeasure` union in `ui/src/lib/tone.ts`, remove its
      `SONAR_MEASURE_TONE` entry, and correct the union's docstring from eight numeric measures to
      seven
- [x] Update `ui/src/lib/__tests__/tone.test.ts` and `ui/src/lib/__tests__/repository.test.ts`: drop
      both fields from fixtures, drop the `sonarMeasureTone('security_hotspots', …)` cases including
      the one asserting it read the security REVIEW rating, and drop both labels from the card-list
      assertion. That last case was the whole of "reads each issue count against its own rating and
      no other", which went with it — hotspots were its only example. The card-list assertion now
      names ten rows and asserts neither label is among them
- [x] run `npm --prefix ui run lint`, `npm --prefix ui run typecheck` and `npm --prefix ui run test` -
      all must pass before task 6

### Task 6: Remove the hotspot signal from the security donut

- [x] Remove `sonar_security_hotspots` from `SecuritySignals` and its `counted(…)` entry from
      `securityBand` in `ui/src/lib/tone.ts`
- [x] Update the `securityBand` docstring: FIVE signals rather than six, "the other four read" rather
      than five, and Sonar's ONE remaining count rather than two — with a line recording that the
      hotspot signal was removed on 2026-09-04 and that it moved NO repository, because every one of
      the 266 repositories reporting it reported zero
- [x] The `SecuritySignals` docstring and `securitySlices` in `ui/src/lib/chart.ts` both counted the
      same signals, so they moved with it: four fields holding six signals became three holding five
- [x] Update `ui/src/lib/__tests__/tone.test.ts`: drop the two
      `securityBand({ sonar_security_hotspots: … })` cases and drop the field from the worst-signal
      and all-signals-absent fixtures
- [x] Update `ui/src/lib/__tests__/chart.test.ts`, `ui/src/lib/__tests__/rows.test.ts` and
      `ui/src/components/__tests__/list-pages.test.ts` to drop the field from their row fixtures
      (done in Task 3, which is where removing the field from the mirror made it compulsory)
- [x] Add a test asserting that a row with a clean security block and no Sonar measures beyond a clear
      rating still bands Clear, so removing a signal did not turn a measured repository Unknown
- [x] run `npm --prefix ui run lint`, `npm --prefix ui run typecheck` and `npm --prefix ui run test` -
      all must pass before task 7

### Task 7: Documentation

- [x] `README.md`: drop `security hotspots` from the Sonar measures prose, drop both lines from the
      sample `evidence` output block, and drop the `sonar_security_hotspots` row from the row-fields
      table. The prose's "four ratings" became three and the table's "Six more landed the same day"
      became five, since both counted what was removed
- [x] `ui/README.md`: five signals rather than six in the security donut section, and drop hotspots
      from both the signal list and the Medium definition
- [x] `docs/architecture.md`: drop security hotspots from the Sonar current-state paragraph (whose
      "four ratings" became three for the same reason), and amend the 2026-09-03 donut decision so
      its signal list matches what `securityBand` now reads
- [x] Add a dated decision to `docs/architecture.md`, in the form the file already uses, recording: what
      Sonar deprecated and that it is a gradual transition rather than a dated removal; that the metric
      was already reporting zero across all 266 rated repositories, so nothing was lost; that
      `RepositoryRow` lost a field; and that A RETIRED EVIDENCE FIELD IS KEPT RATHER THAN DELETED —
      `SonarMeasures` is validated `extra="forbid"` inside the stored `repository_state` payload, so
      deleting a field invalidates every row already written, and the mirror of the existing
      "default to None so an older row still parses" rule is to retain a field so an older row still
      parses. Name this as the precedent for retiring any future evidence field. Added as a `###`
      subsection under "SonarCloud quality evidence", which is the section it amends
- [x] run `uv run poe check` - must pass before task 8

### Task 8: Verify acceptance criteria

- [x] run `uv run poe check` — 1249 passed, 100% coverage
- [x] run `npm --prefix ui run lint`, `npm --prefix ui run typecheck` and `npm --prefix ui run test` —
      lint and typecheck clean, 656 tests passed across 37 files, 100% on all four coverage axes
- [x] grep the tree for `security_hotspots`, `security_review_rating` and `sonar_security_hotspots` and
      confirm the only remaining hits are the two retained `SonarMeasures` fields and their docstring
      note, the two tests from Task 4, the dated decisions in `docs/architecture.md`, and completed
      plans under `docs/plans/completed/`. Confirmed, with three additions to that list, all of them
      absence assertions rather than readers: `tests/test_render.py` and `tests/test_service.py` name
      the labels and the row key only to assert they are GONE, as does the card-list assertion in
      `ui/src/lib/__tests__/repository.test.ts`. One further hit sits outside the named directory —
      `completed/plan-sonar.md` at the repo root, a completed plan in the same category as those under
      `docs/plans/completed/`. `README.md` and `ui/README.md` are clean
- [x] confirm no hit outside `src/metrics/domain.py` and Task 4's tests READS either field — the
      retained fields must have no consumer in `sonar.py`, `render.py`, `service.py` or anywhere
      under `ui/src`. Confirmed: those three modules hold no hit at all, and `ui/src` holds none
      outside the negative test assertion above. In `domain.py` the two names appear only as field
      declarations and in the docstring note
- [x] grep separately for the BARE WORD `hotspot`, which the three key-name terms above do not reach.
      This is the check the first grep should have been: the security donut's tooltip in
      `ui/src/app/repositories/page.tsx` describes the signals in PROSE, and it named hotspots twice
      as a live signal after the code stopped reading one — a wrong string renders as well as a right
      one, and neither gate can see it. Corrected in review, along with the same sentence's stale
      `C or worse` boundary, and pinned by an assertion on the rendered panel in
      `ui/src/components/__tests__/list-pages.test.ts` so the next banding change has to update the
      copy. Retiring any future field means grepping the prose word as well as the field name
- [x] confirm `sonarMeasureCards` builds seven cards and `securityBand` reads five signals. `sonarRows`
      returns ten rows — seven toned counts, as its docstring now says, plus the three ratings, which
      is the shape Task 5 recorded. `securityBand` reads five: three alert families, the security
      rating and Sonar's one remaining count
