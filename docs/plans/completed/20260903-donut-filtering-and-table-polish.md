# Plan: Donut-driven filtering, filter chips, and repositories table polish

## Overview

Replace the five readiness filter buttons on the repositories page with BI-style filtering: every
one of the six estate donuts becomes a filter control (click a legend entry or a wedge to filter the
table), and the row where the buttons sat shows one dismissable chip per active filter, reading
`{TITLE}: {Label}` with the slice's colour dot and an × to clear it. Filters stack (AND) across
dimensions and live in the URL, so a filtered table stays shareable. The team page's readiness donut
becomes clickable the same way — it sits above the same shared `RepositoriesTable`, and a donut that
filters on one page but not another would be inconsistent UI. Also: rename "Readiness labels" to
"Readiness", centre-align the Yes/No CODEOWNERS and Sonar cells, and give the squashed numeric
column headers (Open, Stale, CODEOWNERS, Sonar, Findings) proper spacing.

## Context

- Files involved:
  - `ui/src/lib/chart.ts` — `PieSlice`, `distributionSlices`, `bandSlices` (gain a stable `key` per slice)
  - `ui/src/lib/rows.ts` — `filterRepositories`, `parseState`, `stateCounts` (grows the six-dimension filter model)
  - `ui/src/lib/tone.ts` — `REVIEW_BANDS`, `CHECKS_BANDS`, `UNREVIEWED_BANDS`, `COVERAGE_BANDS`, `SECURITY_BANDS` and their `*Band` functions (read-only: these already define every non-readiness donut)
  - `ui/src/lib/rag.ts` — `RAG_STATES`, `RAG_LABEL`, `RAG_HEX` (read-only)
  - `ui/src/lib/filter.ts` — `filterTarget` (read-only: already sets/deletes one parameter and preserves the rest, including `weeks`)
  - `ui/src/components/charts/SummaryPieChart.tsx` — gains opt-in interactivity
  - `ui/src/components/RepositoriesTable.tsx` — buttons out, chips in, combined filtering
  - `ui/src/components/SortHeader.tsx` — header padding and a centre alignment option
  - `ui/src/app/repositories/page.tsx`, `ui/src/app/teams/[team]/page.tsx` — donut wiring and title rename
  - Tests: `ui/src/lib/__tests__/{chart,rows}.test.ts`, `ui/src/components/__tests__/{charts,list-pages,team-page,tables}.test.ts`, `ui/src/components/__tests__/RepositoriesTable.test.tsx`, `ui/src/components/charts/__tests__/SummaryPieChart.test.tsx`
- Related patterns:
  - Filters live in the URL (`TERM_PARAMETER`, `LABEL_PARAMETER`); navigation is `router.replace` via
    `filterTarget(pathname, window.location.search, parameter, value)` with `{ scroll: false }` —
    every test asserts `weeks` survives the navigation.
  - Unrecognised parameter values resolve to "no filter" (see `parseState`), never to an empty table.
  - The band tables in `tone.ts` are the whole definition of a donut; filtering must classify a row
    with the same `*Band` function the donut counts with, so the filtered row count always equals
    the clicked slice's legend count.
  - jsdom component tests mock `next/navigation` and assert on the `router.replace` target (see
    `RepositoriesTable.test.tsx`); server-render markup is asserted in `charts.test.ts` /
    `tables.test.ts` via `react-dom/server`.
- Dependencies: none new. recharts `Pie` accepts `onClick`; lucide `X` is already used by `FilterSearchBox`.
- Constraints: do NOT run `next build` as a validation step — it fails on this mount; lint,
  typecheck and the vitest suite are the gate. Stage files by explicit path, never `git add -A`.

## Development Approach

- Code-then-tests within each task, keeping `npm run typecheck` and the suite green at every task
  boundary (signature changes update their call sites in the same task).
- All commands run from `ui/` (dependencies are already installed there).
- Complete each task fully before moving to the next.

## Design

Six filter dimensions, each one donut, each one URL parameter:

| Parameter    | Title (chip + donut)             | Values (slice keys)                       | Row classifier                              |
| ------------ | -------------------------------- | ----------------------------------------- | ------------------------------------------- |
| `label`      | Readiness                        | `RAG_STATES`                              | `state(row.readiness)`                      |
| `review`     | Enforces review                  | `REVIEW_BANDS` keys                       | `reviewBand(row.required_approving_reviews)`|
| `checks`     | Enforces CI                      | `CHECKS_BANDS` keys                       | `checksBand(row.required_status_checks)`    |
| `unreviewed` | Unreviewed substantial merges    | `UNREVIEWED_BANDS` keys                   | `unreviewedBand(row.unreviewed_substantial)`|
| `coverage`   | Test coverage                    | `COVERAGE_BANDS` keys                     | `coverageBand(row.sonar_coverage)`          |
| `security`   | Security issues                  | `SECURITY_BANDS` keys                     | `securityBand(row)`                         |

`label` keeps its existing parameter name so shared readiness-filter links keep working. Clicking a
slice sets its dimension's parameter; clicking the already-active slice clears it; distinct
dimensions AND together. A chip's dot colour is the slice's chart colour (`RAG_HEX` /
`Band.mark`), applied as an inline style because the marks are hex values, not Tailwind classes.

## Validation Commands

- `cd ui && npm run lint`
- `cd ui && npm run typecheck`
- `cd ui && npm test`

## Implementation Steps

### Task 1: Slice keys and the estate filter model

- [x] Add `key: string` to `PieSlice` in `ui/src/lib/chart.ts`; populate it in `distributionSlices`
      (the `RAGState`) and `bandSlices` (the band key), leaving `name`/`value`/`color` untouched.
- [x] Add the filter model to `ui/src/lib/rows.ts`: an exported `ESTATE_FILTERS` table with one
      entry per dimension in the Design table — `{ parameter, title, options: [{key, name, color}],
      band: (row: RepositoryRow) => string }` — where readiness options come from
      `RAG_STATES`/`RAG_LABEL`/`RAG_HEX` and the other five reuse the `tone.ts` band tables and
      band functions verbatim. Export the parameter list and a `RepositoryFilters` type
      (partial record of parameter → option key).
- [x] Add `parseFilters(read: (parameter: string) => string | null): RepositoryFilters` that
      validates each parameter's value against that dimension's option keys, dropping unknown
      values exactly as `parseState` does (a stale link shows the whole list, not a blank).
- [x] Change `filterRepositories(rows, term, filters: RepositoryFilters)` to apply the term plus
      every active dimension (AND), classifying each row with the dimension's `band` function; keep
      `parseState` working (readiness maps through `state(row.readiness)`); update the existing
      call in `RepositoriesTable.tsx` to the new signature with `{ label }` so behaviour is
      unchanged for now.
- [x] Update `ui/src/lib/__tests__/chart.test.ts` for slice keys, and extend
      `ui/src/lib/__tests__/rows.test.ts`: `parseFilters` accepts each dimension's keys and drops
      junk; `filterRepositories` filters on each dimension alone and on two dimensions combined,
      agreeing with what the matching donut slice would count.
- [x] Run the suite (`cd ui && npm test`) and typecheck — must pass before task 2.

### Task 2: Clickable donut

- [x] Add an optional `parameter?: string` prop to `SummaryPieChart`. When absent, the render is
      byte-for-byte what it is today — every current page ends up passing one (task 4), but the
      static mode stays supported and tested for any future donut without a table under it.
- [x] When `parameter` is set, render each legend entry as a `<button>` that toggles the filter:
      `router.replace(filterTarget(pathname, window.location.search, parameter, next), { scroll:
      false })` where `next` is the slice `key`, or `''` when that slice is already active (read the
      active value from `useSearchParams`). Mark the active entry `aria-pressed`, give the group a
      sensible `aria-label`, and keep the resting visuals identical to the static legend — hover
      lightens the text (`hover:text-slate-200`) and the focus ring follows the site pattern
      (`focus-visible:ring-1 focus-visible:ring-indigo-500`).
- [x] Make the ring clickable too when interactive: `onClick` on the `Pie` toggling the clicked
      wedge's key the same way, with `cursor-pointer` on the wedges.
- [x] Update `ui/src/components/__tests__/charts.test.ts`: the static donut still renders spans; an
      interactive one renders legend buttons (still listing every label, dimmed zeros included).
- [x] Extend `ui/src/components/charts/__tests__/SummaryPieChart.test.tsx` with a mocked
      `next/navigation`: clicking a legend entry navigates to `?{parameter}={key}` preserving
      `weeks`, clicking the active entry clears the parameter, and a wedge click filters the same
      as its legend entry.
- [x] Run the suite and typecheck — must pass before task 3.

### Task 3: Filter chips replace the button row

- [x] In `RepositoriesTable.tsx`, remove the five-button readiness row, `choose()`, and the
      `stateCounts` usage; delete `stateCounts` from `rows.ts` and its `rows.test.ts` block (it has
      no other caller).
- [x] Read all six parameters with `parseFilters` over `useSearchParams` and pass the result to
      `filterRepositories`, so the table honours every donut's filter.
- [x] Render the chip row in the buttons' old place, one chip per active filter: colour dot (inline
      `backgroundColor` from the option's colour), text `{title}: {name}` with the title in the
      site's uppercase small-cap style, and an `X` button (`aria-label="Remove {title} filter"`)
      that clears just that parameter via `filterTarget` — `weeks`, the search term and the other
      chips all survive. Render nothing at all when no filter is active.
- [x] Reword the filtered `EmptyState` detail to cover any combination of term and donut filters,
      not just the readiness one.
- [x] Rewrite the filter tests in `ui/src/components/__tests__/RepositoriesTable.test.tsx`: chips
      render from the URL (correct text and count), an × removes only its own parameter and keeps
      `weeks` and the term, two dimensions in the URL AND together in the rows shown, and no chip
      row exists when the URL carries no filter.
- [x] Run the suite and typecheck — must pass before task 4.

### Task 4: Wire the donuts and rename the title

- [x] In `ui/src/app/repositories/page.tsx`, pass each donut its `parameter` from the Design table
      and rename the first donut's title from "Readiness labels" to "Readiness".
- [x] In `ui/src/app/teams/[team]/page.tsx`, rename its donut title to "Readiness" and pass it
      `parameter` for the readiness dimension (`label`): the page renders the shared
      `RepositoriesTable` below the donut, so clicking a slice filters that table and shows the
      dismissable chip exactly as on the repositories page — no team-page special casing.
- [x] Update `ui/src/components/__tests__/list-pages.test.ts` and `team-page.test.ts` for the new
      title and the interactive legends where they assert donut markup.
- [x] Run the suite and typecheck — must pass before task 5.

### Task 5: Table alignment and header spacing

- [x] Give `SortHeader` an `align?: 'left' | 'right' | 'center'` prop replacing the bare `numeric`
      flag's alignment role (keep `numeric` as an alias for `right` or migrate the call sites —
      pick one, consistently), and add horizontal padding to the `<th>` (`pr-3`, plus `pl-3` on the
      first column) so the Open, Stale, CODEOWNERS, Sonar and Findings titles stop touching.
- [x] Centre the CODEOWNERS and Sonar columns: `text-center` on those two headers and on the
      `Answer` cell's `<td>`.
- [x] Check every other `SortHeader` caller (`ActorsTable`, `ContributorsTable`,
      `ActorRepositoriesTable`, `TeamActorsTable`, …) still typechecks and renders with the same
      alignment it had. (`ActorsTable` is the only other caller — the rest write plain `<th>`s.)
- [x] Update any test pinning the old header/cell classes (`tables.test.ts`,
      `RepositoriesTable.test.tsx`, `components.test.ts`) and add an assertion that the Answer
      cells are centred.
- [x] Run the suite and typecheck — must pass before task 6.

### Task 6: Verify acceptance criteria

- [x] Run the full test suite (`cd ui && npm test`) — must pass. (36 files, 603 tests, 100% coverage)
- [x] Run the linter (`cd ui && npm run lint`) and typecheck (`cd ui && npm run typecheck`) — both clean.
- [x] Confirm no stray references remain: `LABEL_PARAMETER` still exported and used, no dead
      `stateCounts`, no "Readiness labels" string left in `ui/src`.
- [x] Update `ui/README.md` if it describes the readiness filter buttons or the donut behaviour.
