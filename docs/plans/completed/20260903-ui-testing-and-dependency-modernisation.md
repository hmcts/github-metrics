# Plan: UI dependency modernisation and coverage gate

## Overview
Clear every install-time deprecation warning and audit vulnerability in `ui/`, add coverage reporting
to the UI gate, and raise coverage from the measured 86% lines / 87% functions towards 100% by
testing the untested route files and the interactive client components.

## Context
- **Measured baseline** (v8 provider, `src/**`): 86.0% lines, 94.95% branches, 87.3% functions;
  419 tests in 26 files, all passing.
- **Fully covered already:** all 17 files in `src/lib` (100%), `src/middleware.ts`, and most of
  `src/components`.
- **Coverage gaps:** `src/app/layout.tsx`, `src/app/page.tsx`,
  `src/app/contributors/[login]/{page,loading}.tsx`, `src/app/teams/[team]/{page,loading}.tsx`,
  `src/app/repositories/[repository]/loading.tsx` (all 0%); `FilterSearchBox.tsx` (52.8% lines,
  20% funcs), `RepositoriesTable.tsx` (89.2% lines, 14.3% funcs), `SummaryPieChart.tsx` (79.2%),
  `ActorsTable.tsx` (42.9% funcs), `NavWeekSelector.tsx` (50% funcs).
- **Warning root causes, traced with `npm why`:**
  - `inflight`, `rimraf@3`, `glob@7`, `@humanwhocodes/{object-schema,config-array}` — one chain, all
    from `eslint@8.57.1` (via `flat-cache@3`). ESLint 9 drops it entirely. This is the memory-leak
    warning.
  - `glob@10.3.10` and its high-severity command-injection advisory —
    `@next/eslint-plugin-next@14.2.35`, needs `eslint-config-next@16` (peer: `eslint >=9`).
  - `esbuild<=0.24.2` (moderate), `@vitest/mocker` (**critical**) and the
    `The CJS build of Vite's Node API is deprecated` message — all `vitest@2` / `vite@5`.
  - `recharts@2.15.4` deprecation — the recharts 2 branch is EOL.
  - 6 high advisories on `next@14.2.35` and its bundled `postcss` — need `next@16`.
- **Confirmed from the Next 16 package, not from memory:** `next lint` is gone (no `next-lint.js` in
  `dist/cli`), so `package.json`'s `lint` script must call `eslint` directly. `MIDDLEWARE_FILENAME`
  is still `middleware`, so `src/middleware.ts` needs no rename to `proxy.ts`.
  `experimental.staleTimes` is still under `experimental`, so `next.config.mjs` needs no change
  there. The React peer range is `^18.2.0 || ^19.0.0`, so **React 19 is not forced** and React stays
  at 18.
- **Test pattern to follow:** `src/components/__tests__/components.test.ts` — `createElement` plus
  `renderToStaticMarkup`, with JSX written as `createElement` so the tests stay `.ts`. New DOM tests
  will be `.tsx` with a `@vitest-environment jsdom` docblock, leaving every existing server-render
  test on the `node` environment untouched.
- **ESLint 10 is not reachable, checked against the packages, not from memory:** every published
  `eslint@9.x` is registry-deprecated ("no longer supported"), so one `npm warn deprecated
  eslint@9.39.5` line survives Task 1. ESLint 10 removes it, but `eslint-config-next@16.3.4`'s
  plugin set does not run on 10 — `eslint-plugin-react@7.37.5` peers at `^9.7` and its
  `util/Components.js` throws on a removed context API, and `@typescript-eslint/scope-manager`
  reaches ESLint 10 through a code path that wants `scopeManager.addGlobals`. Both were reproduced
  on eslint 10.9.1. So `eslint` stays on `^9` and the eslint deprecation line is upstream's, not
  ours; Task 9 verifies the remaining warnings are only that one.
- **Installing UI dependencies:** `npm i` with real work to do fails inside `ui/` on this virtiofs
  mount with spurious `ENOTDIR`/`ENOTEMPTY` from arborist's concurrent reify. Install in a scratch
  dir on local storage and `rsync -a --delete` `node_modules` back — scripted at
  `/home/agent/nm/ui-install.sh`. An already-satisfied `npm --prefix ui i` is unaffected.
- **Deliberately not fixed:** the `install-scripts` notice for `unrs-resolver`, a legitimate
  postinstall script of a package we need (it comes from `eslint-import-resolver-typescript` inside
  `eslint-config-next`), so it will still be there after the upgrades. Task 1 records it as approved
  if the npm client supports a manifest field; otherwise it is local client config, not a repo
  change. The `esbuild` notice this originally paired it with is moot after Task 2: Vite 8 replaced
  esbuild with oxc, and `npm ls esbuild` in the upgraded tree is empty. Neither notice is printed by
  the npm client in this sandbox — a clean install after Task 2 emits only the two `npm warn
  deprecated` lines.

## Development Approach
- Toolchain first (ESLint, vitest), then coverage instrumentation so the later tasks have
  measurement, then the two library majors, then the test-writing tasks. The Next 16 migration
  changes every page signature, so it lands **before** the new route tests are written.
- Coverage thresholds are ratcheted, never lowered: Task 3 pins them at the baseline so nothing can
  regress, and Task 8 raises them to what was actually achieved.
- Code-then-tests for the migrations, where the existing suite is the regression net; TDD for the new
  coverage tasks.
- `npm --prefix ui run check` ends in `next build`, which fails on this case-insensitive mount with
  `ENOTDIR` or `ENOENT` under `.next/standalone` for environment reasons — see `AGENTS.md`. When only
  that step fails, run `lint`, `typecheck` and `test` separately and say so; do not treat it as a
  code failure.
- Complete each task fully before moving to the next.

## Validation Commands
- `npm --prefix ui run check`
- `npm --prefix ui run lint`
- `npm --prefix ui run typecheck`
- `npm --prefix ui run test`
- `npm --prefix ui audit`

## Implementation Steps

### Task 1: ESLint 9 with flat config
- [x] Upgrade `eslint` to `^9` and `eslint-config-next` to `16.3.4` in `ui/package.json`
      devDependencies
- [x] Replace `ui/.eslintrc.json` with `ui/eslint.config.mjs` using `eslint-config-next`'s flat
      config export, keeping the `core-web-vitals` rule set
- [x] Change the `lint` script to invoke `eslint` directly rather than `next lint`, which Next 16
      removes
- [x] Add `.next` and `node_modules` to the flat config's `ignores`, which `.eslintignore` no longer
      covers under ESLint 9
- [x] Confirm `npm --prefix ui i` no longer warns about `inflight`, `rimraf`, `glob`,
      `@humanwhocodes/*` or `eslint` — the first four are gone; `eslint@9.39.5` itself still warns
      and cannot be fixed here, see the ESLint 10 note in Context
- [x] Fix any lint findings the newer rule set reports — `import/no-anonymous-default-export` in the
      new flat config, and `react-hooks/set-state-in-effect` in `NavWeekSelector.tsx`
- [x] run `npm --prefix ui run lint` and `npm --prefix ui run test` - both must pass before task 2

### Task 2: vitest 4
- [x] Upgrade `vitest` to `^4` in `ui/package.json` — resolved to `4.1.11`, which brings Vite 8
- [x] Update `ui/vitest.config.ts` for any vitest 4 config changes, keeping the `@` alias and the
      `esbuild.jsx: 'automatic'` setting the `.ts` component tests depend on — Vite 8 has replaced
      esbuild with oxc as the transformer and deprecated the `esbuild` config key, so this became
      `oxc: { jsx: { runtime: 'automatic' } }`; without it oxc honours tsconfig's `jsx: preserve`
      and 8 of the 26 files fail to parse. Also renamed to `vitest.config.mts`: Vite 8's native
      config loader treats a `.ts` config as CommonJS (no `"type": "module"` in `ui/package.json`)
      and warns on its ESM syntax on every run. `**/*.mts` added to `tsconfig.json`'s `include` so
      the config stays typechecked and linted — verified with `tsc --listFiles` and `eslint --debug`
- [x] Confirm the `CJS build of Vite's Node API is deprecated` message no longer appears on
      `npm --prefix ui run test`
- [x] Confirm `npm --prefix ui audit` no longer reports the critical `@vitest/mocker` advisory or the
      moderate `esbuild` one — both gone; the 2 high `next`/`postcss` advisories remain for Task 5
- [x] Fix any test failures from vitest 4 API changes — no API changes bit; the only failures were
      the JSX transform ones fixed by the `oxc` config above
- [x] run `npm --prefix ui run test` - all 419 tests must pass before task 3 — 419 passed in 26
      files, with `lint` and `typecheck` also clean

### Task 3: Coverage reporting in the UI gate
- [x] Add `@vitest/coverage-v8`, matching the vitest 4 version, to `ui/package.json` devDependencies
      — pinned to `^4.1.11`
- [x] Configure `coverage` in `ui/vitest.config.mts`: v8 provider, `include: ['src/**']`, `all: true`,
      `text` and `html` reporters, and a gitignored `reportsDirectory` — all except `all`, which
      vitest 4 removed from `CoverageOptions`; naming `include` is now what puts untested files in
      the table, confirmed by `layout.tsx` reporting 0% rather than being omitted.
      `reportsDirectory: './coverage'`, added to `ui/.gitignore`
- [x] Exclude `src/lib/types.ts` from coverage with a comment saying why — it declares types only and
      emits no runtime code; verified absent from the HTML report's `src/lib` listing
- [x] Add a `coverage` script and make `test`, and so `check`, report coverage so
      `npm --prefix ui run check` prints the table — `coverage` is `vitest run --coverage` and `test`
      delegates to it, so there is one definition of the command
- [x] Set `thresholds` to the measured baseline (85% lines, 94% branches, 87% functions) so coverage
      cannot regress while the later tasks raise it — lines 85 and functions 87 as planned; branches
      pinned at **89**, not 94. The 94.95% in Context predates this config: `include: ['src/**']`
      now counts the untested route files' branches, and the real figure is 89.56%. A 94 threshold
      failed the gate outright
- [x] run `npm --prefix ui run check` - the coverage table must print and the thresholds must pass
      before task 4 — table prints (87.31% stmts, 89.56% branches, 88.27% funcs, 87.5% lines) and
      the thresholds pass; `lint` and `typecheck` clean. Only the trailing `next build` fails, with
      the documented `.next/standalone` `ENOENT` from the mount

### Task 4: recharts 3
- [x] Upgrade `recharts` to `^3` in `ui/package.json` — resolved to `3.10.1`
- [x] Update `src/components/charts/TrendChart.tsx` and `src/components/charts/SummaryPieChart.tsx`
      for the v3 API, keeping `ResponsiveContainer`, `Tooltip`, `Pie` and `Cell` behaviour identical
      — no source change was needed: every prop the two components pass (`contentStyle`,
      `labelFormatter`, `tickFormatter`, `wrapperStyle`, `paddingAngle`, `connectNulls`, the `dot`
      object, `Cell` children of `Pie`, and the `Tooltip` `content` render prop with
      `payload[0].payload`) survives v3 unchanged, and `tsc` accepts the inferred `content`
      signature. Verified by rendering both charts and asserting nothing was written to
      `console.warn`/`console.error`
- [x] Confirm `src/lib/chart.ts` and `src/lib/rag.ts` still hand recharts the shapes it expects —
      both pass colours as strings, which v3 keeps (`RAG_HEX` is `Record<RAGState, string>`,
      `PieSlice.color` and `TrendSeries.color` are `string`)
- [x] Confirm `npm --prefix ui i` no longer warns about `recharts` — `npm view recharts@2.15.4
      deprecated` returns the EOL notice and `recharts@3.10.1` returns nothing; a clean install of
      the upgraded tree emits only the `eslint@9.39.5` line
- [x] Update the chart tests in `src/components/__tests__` and `src/lib/__tests__/chart.test.ts` for
      any markup change — `chart.test.ts` asserts pure arithmetic and needed nothing. The server
      markup did change, but only inside the placeholder `ResponsiveContainer` emits when it has no
      measured width (`min-width:0` on the container, `overflow-x` rather than `overflow` on the
      inner div), which no test asserted. Added `src/components/__tests__/charts.test.ts` to pin
      what a server render of each chart is: the container at the height the page asked for, reached
      without throwing for a line, a bar, a gapped series and a single-period series, plus the
      donut's full legend, its dimmed zero-count label and its no-data branch
- [x] run `npm --prefix ui run test` and `npm --prefix ui run typecheck` - both must pass before
      task 5 — 425 tests in 27 files pass, `typecheck` and `lint` clean, and the Task 3 thresholds
      still pass (branches up to 89.94% from 89.56%)

### Task 5: Next 16 and the async request API
- [x] Upgrade `next` to `16.3.4` in `ui/package.json`, leaving React at 18 — Next 16's peer range
      accepts it; installed 16.3.4 against `react@18.3.1` with no peer complaint
- [x] Await `searchParams` and `params` in all 7 page components (`src/app/page.tsx`,
      `contributors/page.tsx`, `contributors/[login]/page.tsx`, `repositories/page.tsx`,
      `repositories/[repository]/page.tsx`, `teams/page.tsx`, `teams/[team]/page.tsx`) and change
      their prop types to `Promise<…>` — `LandingPage` became `async` to await the redirect target's
      span; the other six were already async
- [x] Await `cookies()` at each of its 6 call sites, keeping the `WEEKS_COOKIE` and `resolveWeeks`
      logic unchanged — `(await cookies()).get(WEEKS_COOKIE)?.value` in place, `resolveWeeks`
      untouched
- [x] Confirm `src/middleware.ts` still works unrenamed and that `next.config.mjs`'s
      `output: 'standalone'` and `experimental.staleTimes` are still valid — a build off the mount
      compiles the file and prints `ƒ Proxy (Middleware)` in the route table, `staleTimes` is
      accepted (listed under Experiments) and `.next/standalone/server.js` is emitted, so
      `ui/Dockerfile`'s two COPYs still find what they name. Next 16 does print a **new build-time
      warning**: `The "middleware" file convention is deprecated. Please use "proxy" instead.` The
      convention still works, and renaming to `proxy.ts` is outside this plan's scope — worth its
      own change
- [x] Update the page tests in `src/components/__tests__/{repository-page,list-pages,repository,actor}.test.ts`
      to pass promises for `params` and `searchParams` — only `repository-page` and `list-pages`
      render a page; `repository.test.ts` and `actor.test.ts` test `lib` helpers and components and
      needed nothing. Both `next/headers` mocks now resolve a promise, matching Next 16's `cookies()`
- [x] Confirm `npm --prefix ui audit` reports 0 vulnerabilities and `npm --prefix ui i` prints no
      deprecation warnings — audit is 0 across all severities (the 6 high `next`/`postcss` advisories
      are gone). A clean install prints one line, `eslint@9.39.5`, which Context documents as
      upstream's and Task 9 verifies
- [x] run `npm --prefix ui run lint`, `npm --prefix ui run typecheck` and `npm --prefix ui run test` -
      all must pass before task 6 — all three clean, 425 tests in 27 files, Task 3's thresholds still
      pass (87.31% stmts, 89.94% branches, 88.27% funcs, 87.5% lines). `check`'s `next build` step
      still fails on the mount, now as Turbopack's `File exists (os error 17)` under
      `.next/build/chunks` rather than the old `.next/standalone` `ENOTDIR`; the same tree builds
      clean off the mount, and `ui/README.md` and `AGENTS.md` were updated to describe the new shape

### Task 6: DOM tests for the interactive client components
- [x] Add `jsdom`, `@testing-library/react` and `@vitejs/plugin-react` to `ui/package.json`
      devDependencies and register the React plugin in `ui/vitest.config.mts` — `jsdom` pinned to
      `^29.1.1`, not the current `30.0.1`: 30's engine range starts at node `^22.22.2` and this
      sandbox runs 22.22.1, so 30 installs with an `EBADENGINE` warning. 29's range covers both this
      node and the `node:26` in `ui/Dockerfile`. `@testing-library/react@16.3.3` and
      `@vitejs/plugin-react@6.1.1` install clean against React 18 and Vite 8
- [x] Keep `environment: 'node'` as the default so the existing `renderToStaticMarkup` tests are
      unaffected, and document that DOM tests opt in per file with a `@vitest-environment jsdom`
      docblock — stated in `vitest.config.mts` beside the `environment` key and in `ui/README.md`'s
      Layout section, whose claim that the router-reading tables are checked as their pure parts
      only is no longer true and was rewritten
- [x] Write `src/components/__tests__/FilterSearchBox.test.tsx` covering the debounced navigate, the
      clear button, the follow-the-URL effect including the self-navigation skip, and the unmount
      timer cleanup — with fake timers and a mocked `next/navigation`. 11 tests. `useSearchParams`
      returns ONE object the tests replace only on a deliberate URL change: the effect keys on its
      identity, so a fresh instance per render would fire it on every keystroke and hide the bug the
      skip exists for
- [x] Write `src/components/__tests__/RepositoriesTable.test.tsx` and `ActorsTable.test.tsx` covering
      the sort and paging handlers — 11 and 7 tests. Neither table pages: the whole list is the
      estate, so there is no paging handler to cover. What is there is sorting (every column clicked,
      so each `read` is reached, plus the reversal and the unmeasured-last guardrail in both
      directions) and, for `RepositoriesTable`, the readiness chips that navigate
- [x] Write `src/components/__tests__/NavWeekSelector.test.tsx` covering the `useTransition` pending
      state that `react-dom/server` reports as `false`, and the render-time reset of the optimistic
      selection that Task 1 put in place of the effect — including that pressing Back does not
      resurrect the guess. 8 tests. The pending state is reached with a real in-flight navigation
      rather than a claim about one: the stubbed `router.replace` moves the harness's span inside the
      transition and a sibling under a `Suspense` boundary suspends until the test lands it. Checked
      by mutation — disabling the render-time reset fails two of these tests
- [x] Write `src/components/charts/__tests__/SummaryPieChart.test.tsx` covering the tooltip and label
      callbacks — 7 tests, hovering the real wedges. Two things are needed to get there:
      `ResponsiveContainer` renders NOTHING until an observation arrives (its initial dimension is
      negative), so a stubbed `ResizeObserver` reports 400×175; and recharts reveals the wedges over
      an animation, which in real time gives a nondeterministic wedge count part-way through, so the
      animation frame, `performance` and `Date` are faked and run out. Wedges are keyed by fill
      colour, not draw order
- [x] run `npm --prefix ui run check` - `FilterSearchBox`, `RepositoriesTable`, `ActorsTable`,
      `NavWeekSelector` and `SummaryPieChart` must all reach 100% functions before task 7 — all five
      are gone from the coverage table entirely, which is 100% on every column. `src/components` is
      100% statements/functions/lines, `src/components/charts` 100% lines with only `TrendChart`
      short. Overall 94.95% stmts, 93.73% branches, 96.6% funcs, 94.87% lines from 469 tests in 32
      files; `lint` and `typecheck` clean. `check`'s `next build` step still fails on the mount, as
      the documented `.next/standalone` `ENOTDIR`, after compiling and typechecking successfully.
      That step also rewrites `ui/tsconfig.json` (`jsx: react-jsx`, plus a reformat); reverted, as
      Task 5 left it

### Task 7: Cover the untested route files
- [x] Write tests for `src/app/layout.tsx` and `src/app/page.tsx`, the latter asserting the
      `landingTarget` redirect — `src/components/__tests__/layout.test.ts`, 9 tests. The shell is
      asserted as the document it renders (`<html lang="en" class="dark">`, the bar above the one
      `<main>`) and the metadata Next puts in the head; the landing segment renders no markup at all,
      so `next/navigation` is stubbed and what is asserted is the target — including the repeated
      `?weeks=` a mangled link arrives as, and the missing `searchParams` prop Next omits for a
      request with no query string
- [x] Write tests for `src/app/contributors/[login]/page.tsx` and `src/app/teams/[team]/page.tsx`,
      following the mocking pattern in `src/components/__tests__/repository-page.test.ts` —
      `contributor-page.test.ts` (9 tests) and `team-page.test.ts` (10). Both cover the three-step
      span resolution as the join no component below can see, both empty states, and both refusals:
      a 404 becomes `notFound`, every other status surfaces as the `ApiError` it is
- [x] Write tests for the three uncovered `loading.tsx` files (`contributors/[login]`,
      `repositories/[repository]`, `teams/[team]`), matching the existing `LoadingContributors` tests
      in `components.test.ts` — one test in that file's existing `the loading skeletons` describe,
      asserting each detail route's own shape rather than three copies of one: the cohort card row is
      the repository's alone, the donut bone the team's alone, and each asks for the section count its
      own page holds
- [x] Cover the remaining uncovered branches in `contributors/page.tsx` (lines 51-54),
      `repositories/page.tsx` (65, 99-102), `repositories/[repository]/page.tsx`, `teams/page.tsx`
      (32), `OrganisationHeader.tsx` (34), `TrendSection.tsx` (41-42) and `metrics.ts` (164) — the two
      list empty states and the `unavailable > 0` card detail in `list-pages.test.ts`; the header with
      no `action` in `components.test.ts`; the heading parts a series carries neither of in
      `trend.test.ts`; and the repository detail page in two new describes — the unavailable
      repository that keeps its page, both refusals, each uncollected signal's own sentence, a block
      with no `fetched_at` to stamp, the findings and contributors lists drawn rather than empty, and
      the trend section present, empty and refused. `teams/page.tsx` (32) was already covered before
      this task: the empty-teams case Task 5 left in place reaches it. `metrics.ts` (164) was not
      coverable as written — `conditionTone` answers `undefined` only for a blocking outcome, which
      `metricTone` returns on the line above, so the `?? 'neutral'` was a fallback no input could
      reach. `conditionTone` now carries three overloads saying which outcome gets which answer, and
      the fallback is gone rather than excused
- [x] run `npm --prefix ui run check` - must pass before task 8 — 510 tests in 35 files pass,
      `lint` and `typecheck` clean, and coverage is **100% lines and 100% branches** (99.85%
      statements, 99.69% functions) with only `TrendChart.tsx` short: one function and one statement,
      left for Task 8 to ratchet or record. Every route file is off the coverage table. `check`'s
      trailing `next build` still fails on the mount, as the documented `ENOENT` under `.next`

### Task 8: Ratchet the coverage thresholds
- [x] Raise the `thresholds` in `ui/vitest.config.mts` to the coverage now achieved, targeting 100%
      lines, branches and functions — all four at **100**, `statements` named as well as the three
      the plan lists, so the one measure that was short cannot drift back unnoticed
- [x] Where 100% is not reachable, record the shortfall as an explicit per-file threshold with a
      comment naming what is uncovered and why, rather than lowering the global figure — **no
      per-file threshold was needed**. Task 7's only shortfall was `TrendChart.tsx`'s `tick`
      (1 of 4 functions, 1 of 16 statements), the axis and tooltip formatter recharts calls only
      from a measured chart, so `react-dom/server` never reaches it. It is now covered rather than
      excused, by `src/components/charts/__tests__/TrendChart.test.tsx` — 3 jsdom tests reading the
      drawn tick labels back for the line and the bar branch separately (the axes are written out
      once per branch, so one shape's render says nothing about the other's) plus the unparseable
      timestamp that formats as `ABSENT`. Two things had to be right: the same stubbed
      `ResizeObserver` and run-out animation the donut's tests need, and reading the labels from
      `.recharts-xAxis-tick-labels` rather than from `.xAxis` — recharts 3 hoists tick labels into
      their own z-index layer, leaving only tick lines under the axis group, so descending from
      `.xAxis` finds an empty list and asserts nothing. The config's comment says all of this and
      says an unreachable line belongs in `exclude` with a reason, as `src/lib/types.ts` is
- [x] Document the UI coverage target and the `npm --prefix ui run coverage` command in
      `ui/README.md` — a `### Coverage` subsection under Layout, beside the jsdom opt-in it depends
      on: the 100% target, the command, where the HTML report is, why 100% is reachable in an app
      that measures nothing of its own, and the ratchet-and-exclude rule. The `check` paragraph
      above now names the step as `vitest run --coverage` and links to it
- [x] Update `AGENTS.md`'s Required Checks section to say `check` now includes coverage — its
      one-line description of `check` now says "vitest with coverage", followed by a paragraph
      stating the enforced 100%, the `coverage` script, and that thresholds go up and never down
- [x] run `npm --prefix ui run check` - must pass at the new thresholds — coverage is 100% on all
      four measures (694/694 statements, 525/525 branches, 324/324 functions, 664/664 lines) from
      513 tests in 36 files, and the 100 thresholds pass; `lint` and `typecheck` clean, run
      separately as well. Only the trailing `next build` fails, as the documented Turbopack
      `File exists (os error 17)` under `.next/static/chunks` from the mount

### Task 9: Verify acceptance criteria
- [x] run a clean install (see the install note in Context) and confirm the only `npm warn
      deprecated` line left is `eslint@9.39.5`, which `eslint-config-next@16.3.4` pins us to — a
      `npm ci` of this lockfile into an empty directory on local storage printed exactly one warning
      line, `eslint@9.39.5`, then `added 529 packages, and audited 530 packages`
- [x] run `npm --prefix ui audit` and confirm 0 vulnerabilities — `found 0 vulnerabilities`, and the
      clean install's own audit agrees
- [x] run `npm --prefix ui run test` and confirm no `CJS build of Vite's Node API` warning — 513
      tests in 36 files pass and the whole run contains no line matching `CJS`, `Vite` or
      `deprecat`; coverage is 100% on all four measures (694/694 statements, 525/525 branches,
      324/324 functions, 664/664 lines)
- [x] run `npm --prefix ui run check` and confirm the coverage table prints and the thresholds pass —
      `lint`, `typecheck` and the coverage step all pass, which reaching `next build` proves; the
      table and the passing thresholds are the `test` run above, the same `vitest run --coverage`
      command `check` invokes. Only the trailing build fails, as the documented Turbopack
      `File exists (os error 17)`, this run under `.next/server/chunks/ssr`
- [x] run `npm --prefix ui run lint` — clean, `eslint .` with no findings, re-run after the doc edits
- [x] confirm the only remaining `install-scripts` notice is `unrs-resolver` (`esbuild` left the tree
      with the Vite 8 upgrade in Task 2), and record it as reviewed in `ui/README.md` — walked all
      530 installed packages for a `preinstall`/`install`/`postinstall` script: `unrs-resolver` is
      the only one, and `npm ls esbuild` is empty. Recorded in a new `### Install notices`
      subsection of `ui/README.md` alongside the `eslint` line, with why each is there. Neither is
      printed as a notice by npm 9.2.0 in this sandbox
- [x] run `uv run poe check` to confirm the Python side is unaffected — 1136 tests pass at 100%
      coverage; nothing in this plan touched a `.py` file
- [x] update `ui/README.md` and `AGENTS.md` if any user-facing command changed — no command changed,
      so this is accuracy only: both named `.next/build` as where the mount's build failure lands,
      which is one of several paths it takes (this run's was `.next/server/chunks/ssr`), now `.next`
      with the chunk varying. `ui/README.md` also records Next 16's build-time
      `"middleware" file convention is deprecated` warning beside the `src/middleware.ts` it is
      about, as expected output of a passing build rather than something left broken — the rename to
      `proxy.ts` is its own change, as Task 5 found
