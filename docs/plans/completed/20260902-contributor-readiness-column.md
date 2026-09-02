# Plan: Contributor readiness column, and a nav bar in estate order

## Overview
Reorder the nav bar to Repositories, Teams, Contributors, and give the `/contributors` list a
readiness column carrying the distinct labels each person's repositories hold — the same labels the
text report's actor section prints, without its counts — sortable and sorted by default.

## Context
- Files involved:
  - `ui/src/components/Navigation.tsx` — the three nav links
  - `ui/src/components/ActorsTable.tsx` — the `/contributors` list, currently unsorted and 2 columns
  - `ui/src/app/contributors/page.tsx` — the section heading and its `detail`
  - `ui/src/lib/types.ts` — `ActorRow`
  - `ui/src/lib/rag.ts` — label presentation and the existing `severity` sort key
  - `ui/src/components/RAGCard.tsx` — `RAGLabel`, the badge every label on the site renders as
  - `src/metrics/service.py` — `ActorRow` contract (line 258) and `serve_actors` (line 1002)
  - `src/metrics/domain.py` — `ReadinessLabel`, `ActorReadiness`, `ActorRepositoryReadiness`
  - `src/metrics/render.py` — `actor_repositories` (line 783), the one place the `cannot_assess`
    exclusion is spelled, and its two call sites
  - `docs/architecture.md`, `README.md`, `ui/README.md`
- Related patterns:
  - `RepositoriesTable.tsx` is the sortable-table pattern: a `Column[]` with a `read` function,
    `useState` for column and direction, `SortHeader` per column, `sorted()` from `lib/sort.ts`
  - `rag.ts:severity` is the precedent for "sort by what a label says, not how it is spelled"
  - `sorted()` puts an `undefined` sort value last in BOTH directions and is stable, so ties keep
    the service's alphabetical order
  - `render.actor_combination` already derives a person's distinct labels, best-first, off
    `actor_repositories` — the new field is that derivation returning `ReadinessLabel` values
- Decisions taken with the user, 2026-09-02:
  - The column renders `RAGLabel` badges (READY / CAUTION / BLOCKED), best-first, right-aligned to
    the table's right edge as the numeric columns are
  - `cannot_assess` repositories are EXCLUDED, exactly as `render.actor_repositories` excludes them
    from the report's actor lines. Somebody left with no label keeps their row, shows a dash, and
    sorts last in both directions
  - Column order becomes Login, Repositories, Readiness — the repository count moves to the middle
  - The sort key is the labels mapped to `green → "1"`, `amber → "2"`, `red → "3"`, sorted and
    concatenated, ordering as `"1" < "12" < "123" < "13" < "2" < "23" < "3"`
- This reverses a written boundary. `docs/architecture.md` ("Scope boundaries") states that every
  list of people is alphabetical and carries no column to sort by. What the reversal admits is
  ordering by a COMBINATION OF LABELS THE REPOSITORIES ALREADY CARRY — the same grouping the text
  report's `Enable` / `Review` / `Blocked` sections were admitted for on 2026-09-01. No score, no
  metric, no per-person verdict and no count beside a name. The dated instruction is recorded in
  Task 5, as every previous reversal has been.

## Development Approach
- Code first, then tests, per task — these are edits to components and contracts that already have
  test files to extend
- The service change lands before the UI reads it: `/actors` cannot carry a label the contract has
  no field for
- `npm --prefix ui run check` is THE UI gate — do not substitute the individual lint, typecheck and
  test commands for it. The single exception AGENTS.md already records: if `check` fails only in its
  `next build` step with `ENOTDIR`/`ENOENT` under `.next/standalone` on a path that differs each
  run, that is the case-insensitive mount and not the change; say so in the report rather than
  hunting it
- Python coverage is enforced at 100% (`fail_under = 100`), so every new branch needs a test
- Complete each task fully before moving to the next

## Validation Commands
- `uv run poe check`
- `npm --prefix ui run check`

## Implementation Steps

### Task 1: Put the nav bar in estate order
- [x] in `ui/src/components/Navigation.tsx`, move the Teams link above the Contributors link so the
      bar reads Repositories, Teams, Contributors
- [x] keep the `/contributors` route comment attached to the Contributors link it explains
- [x] add or extend a test asserting the three nav labels appear in that order in the rendered markup
      (`ui/src/components/__tests__/components.test.ts` renders components through
      `react-dom/server`)
- [x] run `npm --prefix ui run check` - lint, typecheck and 402 tests pass; `next build` fails only
      in its `.next/standalone` copy with `ENOTDIR`, the case-insensitive mount AGENTS.md records

### Task 2: Carry each contributor's distinct readiness labels on `/actors`
- [x] add `reported_repositories(actor)` to `src/metrics/domain.py`, moving the `cannot_assess`
      exclusion out of `render.actor_repositories` unchanged, docstring and dated reasoning included
- [x] add `actor_labels(actor) -> tuple[ReadinessLabel, ...]` to `src/metrics/domain.py`: the
      DISTINCT labels of `reported_repositories`, in `ReadinessLabel` declaration order (green,
      amber, red), returning an empty tuple where nothing is left to label
- [x] delete `render.actor_repositories` and point its two call sites — `render_actors` and
      `actor_combination_counts` — at `domain.reported_repositories`, so the exclusion still has
      exactly one spelling
- [x] add `labels: tuple[ReadinessLabel, ...] = ()` to `ActorRow` in `src/metrics/service.py`, with a
      docstring stating it lists labels and combines nothing, and populate it in `serve_actors` from
      `actor_labels`
- [x] write tests in `tests/test_domain.py` for `actor_labels`: one label, several distinct labels
      returned best-first, duplicates collapsed, `cannot_assess` excluded, a person whose
      repositories are all `cannot_assess` returning empty, and repositories with no `readiness`
      (policy disabled) returning empty
- [x] extend `tests/test_service.py` so a `/actors` response asserts the labels it carries, and
      confirm `tests/test_render.py` still passes against the moved helper
- [x] run `uv run poe check` - must pass before task 3

### Task 3: A sort key for a combination of labels
- [x] add `labels: ReadinessLabel[]` to `ActorRow` in `ui/src/lib/types.ts`
- [x] add `combinationKey(labels)` to `ui/src/lib/rag.ts`: map green/amber/red to `"1"`/`"2"`/`"3"`,
      drop anything else, dedupe, sort the digits, and join — returning `undefined` for an empty
      result so `sorted()` puts an unlabelled person last in both directions
- [x] document in the function why the key is a sorted string of digits rather than a number: it
      orders `green` before `green, amber` before `green, amber, red` before `green, red`, which no
      arithmetic on a severity gives
- [x] write tests in `ui/src/lib/__tests__/rag.test.ts` asserting all seven combinations produce
      `"1"`, `"12"`, `"123"`, `"13"`, `"2"`, `"23"`, `"3"`, that those seven sort in exactly that
      order, that an unordered or duplicated input gives the same key, and that `[]` and a
      `cannot_assess`-only input give `undefined`
- [x] run `npm --prefix ui run check` - lint, typecheck and 407 tests pass; `next build` fails only
      in its `.next/standalone` copy with `ENOENT`, the case-insensitive mount AGENTS.md records

### Task 4: The contributors list gets a sortable readiness column
- [x] turn `ui/src/components/ActorsTable.tsx` into a `'use client'` component following the
      `RepositoriesTable` pattern: a `Column[]` of Login, Repositories (numeric) and Readiness, each
      rendered through `SortHeader`, with `useState` for column and direction and `sorted()` doing
      the ordering
- [x] default the state to the Readiness column ascending, so the list opens all-green first
- [x] render the readiness cell as `RAGLabel` badges, best-first, in a `flex flex-wrap justify-end`
      row so the badges sit flush against the table's right edge like the numeric columns, and show
      the dash from `lib/format.ts` where a person has no label left
- [x] replace the component's "alphabetical and nothing else" docstring with what is now true: the
      list orders by a combination of labels the repositories already carry, ties fall back to the
      service's alphabetical order because `sorted` is stable, and the repository count is still
      navigation rather than a score — and with it the three neighbouring comments the change made
      untrue: `lib/sort.ts`, `TeamActorsTable` and the team page, which each cited this list as the
      reason a people list is alphabetical
- [x] update the `Section` `detail` on `ui/src/app/contributors/page.tsx` so it no longer says
      `alphabetical`
- [x] extend the `ActorsTable` tests in `ui/src/components/__tests__/tables.test.ts` and
      `components.test.ts`: the three headers in order, badges rendered for a row's labels, a dash
      for a row with none, and the default render putting an all-green contributor above a red one
      and an unlabelled contributor last
- [x] run `npm --prefix ui run check` - lint, typecheck and 412 tests pass; `next build` fails only
      in its `.next/standalone` copy with `ENOENT`, the case-insensitive mount AGENTS.md records

### Task 5: Record the ruling and the new field in the docs
- [x] in `docs/architecture.md` "Scope boundaries", amend the "No personal rankings" paragraph: state
      the 2026-09-02 instruction, that a contributor list MAY be ordered by the combination of labels
      its rows already carry, and that what stays binding is unchanged — no score, no metric column,
      no count beside a name, no per-person verdict
- [x] in the same file, update the note naming `render.actor_repositories` as "the one place to
      change" so it names `domain.reported_repositories` instead
- [x] state the new `labels` field wherever `README.md` documents the `/actors` row
- [x] update `ui/README.md`: the `/contributors` row in the routes table (line ~55) and the
      "no sortable headers" paragraph (line ~209), which now applies to `TeamActorsTable` and
      `ContributorsTable` alone
- [x] run `uv run poe check` and `npm --prefix ui run check` - 1135 Python tests at 100% coverage;
      UI lint, typecheck, 412 tests and `next build` all pass, the build included this run

### Task 6: Verify acceptance criteria
- [x] confirm the nav bar reads Repositories, Teams, Contributors - `Navigation.tsx` orders the three
      links that way and `components.test.ts:410` asserts the labels appear in that order in the
      rendered markup
- [x] confirm the contributors list shows Login, Repositories, Readiness left to right, with the
      badges right-aligned to the table border - `tables.test.ts:37` asserts the header order and
      `tables.test.ts:42` asserts the badges render inside `justify-end`, in a `text-right` cell
- [x] confirm the list opens sorted by readiness and that clicking the header reverses it - the
      default render puts an all-green login above a green+amber one, above a red one, with the
      unlabelled login last (`tables.test.ts:54`, `tables.test.ts:59`). The click itself is not
      asserted: these tests render statically, so the handler was read rather than clicked —
      `sort()` reverses only when the column is already active, over the `reverse` covered in
      `sort.test.ts:20`
- [x] run `uv run poe check` - 1135 tests pass at 100% coverage
- [x] run `npm --prefix ui run check` - lint, typecheck and 412 tests pass; `next build` fails only
      in its `.next/standalone` copy with `ENOTDIR`, the case-insensitive mount AGENTS.md records
