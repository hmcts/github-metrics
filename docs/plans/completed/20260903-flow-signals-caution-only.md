# Plan: Flow signals report as cautions and never block readiness

## Overview
`ReadinessPolicy.distribution` (`src/metrics/assessment.py:430`) currently returns a **blocking**
condition capped at amber when a flow percentile sits above its maximum, so
`merge-cycle-time-above-target` and `time-to-first-review-above-target` hold
`am-role-assignment-batch-service` below ready for being slow. All three flow signals —
`pull-request-size`, `merge-cycle-time`, `time-to-first-review` — become **caution-only**, the same
shape `review-depth` already has: still graded against their configured `maximum`, still reported
with their numbers, still amber on their metric card, but imposing no ceiling on the label.

## Context
- Files involved:
  - `src/metrics/assessment.py` — `distribution()`, and the docstrings in `behaviour()`,
    `pull_request_size()`, `merge_cycle_time()`, `time_to_first_review()` that describe the grading
  - `src/metrics/config.py` — `DistributionThreshold` docstring ("ONE boundary … because a flow
    signal can never impose red")
  - `tests/test_assessment.py` — six `-above-target` tests (lines ~711-800) and
    `test_a_flow_signal_caps_at_amber_however_far_above_its_maximum_it_sits` (~889)
  - `ui/src/lib/metrics.ts` — `GRADED_SUFFIXES` docstring claims `above-target` and `below-target`
    are "both blocking"
  - `ui/src/lib/__tests__/metrics.test.ts` — the `GRADED` fixture puts `merge-cycle-time-above-target`
    in `blocking` as its blocking-amber example
  - `README.md` — the flow-signal paragraphs (~744-761), including "**A flow signal caps at `amber`**"
  - `docs/architecture.md` — decision 4 (lines 93-99), the scope-boundary note at line ~196
- Related patterns: `review_depth()` in the same class is the precedent — graded against a
  configured boundary, reported as `-below-target`, never blocking. The condition names keep their
  `-above-target` suffix so `ui/src/lib/metrics.ts:graded` still finds them and the card still takes
  a `warn` tone from `conditionTone('caution', …)`. No threshold moves and no configuration key
  changes.
- Dependencies: none.

## Do not touch the database
NOTHING IN THIS PLAN MAY OPEN `.metrics/metrics.sqlite3`. A collection is running against it from
outside this sandbox over the shared mount, and every entry point in `src/metrics/storage.py` opens
the file as a WRITER even for a read-only report: `with closing(connect(path)) as connection,
connection:` wraps `prepare()`, which runs `initialize()`'s `CREATE TABLE IF NOT EXISTS`
`executescript` in a transaction and calls `pin_journal_mode()`. With `JOURNAL_MODE = "delete"` there
is no WAL concurrency to fall back on, only a five-second busy timeout, so a second writer risks
`database is locked` on either side — and a mode conversion needs an exclusive lock.

So this change is verified entirely in process. DO NOT run `metrics evidence`, `metrics trend`,
`metrics collect` or `metrics-serve`, with or without `--offline` or `--refresh`. The assessment
policy takes `CachedBehaviourFacts` and a `MergeGateReport` as arguments and reads no file, which is
why every task below tests it directly. The test suite is safe as it stands: storage tests use
`tmp_path`, and the `database=Path("metrics.sqlite3")` values in `tests/test_behaviour.py` and
`tests/test_inventory.py` are inert configuration fields that reach no connection and are relative
to the repository root regardless — so any new test must keep to that pattern.

## Development Approach
- Code then tests, matching the existing suite's structure: each demoted condition already has a
  test naming it, so the change is visible as those tests being rewritten rather than deleted.
- BOTH SUITES MUST REPORT 100% COVERAGE and neither threshold may be lowered to make this change
  pass. Python enforces it with `fail_under = 100` in `pyproject.toml`; the UI enforces
  `statements`, `lines`, `branches` and `functions` at 100 in `ui/vitest.config.mts`. Those numbers
  were ratcheted up to 100 and are upwards-only. Removing the blocking branch from `distribution()`
  must not leave a partially covered line, and the rewritten tests must still reach every branch
  that survives. A genuinely unreachable line belongs in the vitest `exclude` list with a reason —
  never in a reduced threshold.
- Complete each task fully before moving to the next.

## Validation Commands
- `uv run poe check` — ruff check, ruff format --check, mypy, and pytest at 100% coverage
- `npm --prefix ui run check` — eslint, tsc --noEmit, vitest at 100% coverage, and next build

Three `tests/test_cli.py` collect tests — `test_collect_reports_current_state_and_what_the_window_fetched`,
`test_collect_summarises_every_call_by_status_outcome_and_endpoint` and
`test_collect_summarises_the_calls_of_a_run_that_could_not_store_what_it_collected` — FAIL BEFORE THIS
CHANGE and fail identically on `main` (verified in a worktree on 2026-09-04). They assert a call count
of 12 per repository and collection now issues 14, two extra pull-request searches from the interval
splitting in 41f0661 `fix: handle coverage right edge`. Nothing in this plan touches collection, so
they are out of scope: treat `poe check` as green when those three are the only failures, and do not
adjust their counts here.

`next build` is the last step of `check` and FAILS ON THIS MOUNT for environment reasons, on a
different path each run — under Turbopack it is `File exists (os error 17)` writing a
`.next/build/chunks/…` map. That is the case-insensitive host mount, not the change. If `check`
fails ONLY in `build`, having passed lint, typecheck and coverage, treat the change as green and say
in the report that the build failed for environment reasons. Confirm it properly by copying the
configs, `src` and `node_modules` — copies, not symlinks, which Turbopack rejects — into a scratch
directory on local storage and running `npx next build` there. A failure in lint, typecheck or
vitest is the change's own and must be fixed.

## Implementation Steps

### Task 1: Demote the flow distributions to cautions
- [x] In `src/metrics/assessment.py`, change the above-maximum branch of `distribution()` from
      `self.blocking(f"{identifier}-above-target", ReadinessLabel.AMBER, …)` to
      `self.caution(f"{identifier}-above-target", …)`, keeping the identifier, the detail wording and
      the at-or-below `clear` branch exactly as they are
- [x] Rewrite the `distribution()` docstring: replace the "AMBER however far above the maximum"
      rationale with the caution-only one, keeping the estate evidence (0.003 hours at 0% review
      coverage against 99.3% of 294 merges) as the argument for why a cost never decides the label,
      and state that `review_depth` is the existing precedent
- [x] Update the `behaviour()` docstring and the three per-metric method docstrings so none of them
      claims a flow signal can hold the label back
- [x] Update the `DistributionThreshold` docstring in `src/metrics/config.py` and the
      `# Flow signals graded by cost rather than compliance` comment block above `pull_request_size`
      in `AssessmentConfiguration`, so neither still says the signals cap at amber
- [x] run `uv run poe types` and `uv run poe lint` — must pass before task 2

### Task 2: Re-pin the assessment tests on the new outcome
- [x] Rewrite the three `*_above_green_and_at_or_below_amber_is_amber` tests to assert the condition
      is reported in `assessment.caution` with `label is None` and its unchanged detail, renaming
      each to say what it now checks
- [x] Replace the three `*_far_above_its_maximum_is_still_only_amber` tests with ones asserting the
      condition stays a caution however far above the maximum it sits, and that it appears in no
      blocking list
- [x] Rewrite `test_a_flow_signal_caps_at_amber_however_far_above_its_maximum_it_sits` as a test that
      a repository with a compliant gate and full review coverage reaches `ReadinessLabel.GREEN` with
      an empty `blocking` even at a 2400-hour median cycle time and 90000-line changes — the exact
      case this change exists to fix
- [x] Add a test that all three `-above-target` conditions land in `caution` together for one slow,
      large-change cohort, so a future change cannot demote one and re-promote another unnoticed
- [x] Confirm `test_every_graded_metric_condition_is_spelled_with_one_of_four_suffixes` still passes
      unchanged (it reads all three sections via `reported`), and leave it alone
- [x] `test_flow_metric_thresholds_are_configurable` read its boundary off the label, so it was
      rewritten to read the condition name instead — the boundary still moves, the label no longer does
- [x] run `uv run poe check` — must pass at 100% coverage before task 3 (100% coverage, ruff and mypy
      clean; only the three pre-existing `test_cli` collect tests fail, as noted above)

### Task 3: Correct the dashboard's account of what these conditions are
- [x] Fix the `GRADED_SUFFIXES` docstring in `ui/src/lib/metrics.ts` so `above-target` is described as
      the caution a flow signal raises rather than a blocking condition, and check the `metricTone`
      docstring's blocking/caution wording still reads true (it does — a blocking condition is still
      only ever a rate `-below-target`, amber or red, so that paragraph needed no change)
- [x] In `ui/src/lib/__tests__/metrics.test.ts`, move `merge-cycle-time-above-target` out of the
      `GRADED` fixture's `blocking` array into `caution`, and give the blocking array a real amber
      condition the policy can still produce so `takes the ceiling a blocking condition imposed, red
      and amber alike` still tests what it names — `approval-coverage-below-target` at amber rather
      than the `substantial-changes-merged-unreviewed` this plan named, because that condition carries
      none of the four `GRADED_SUFFIXES`, so `graded` finds it for no metric and no card could have
      read an amber ceiling off it
- [x] Add an assertion that a flow card whose condition is now a caution still reads `warn`, so the
      card keeps its colour while the label is released
- [x] run `npm --prefix ui run check` — must pass at 100% coverage before task 4, with `build` the
      only step allowed to fail and only for the environment reason above (lint, typecheck and vitest
      clean at 100% of statements, branches, functions and lines; `next build` died on the mount with
      `File exists (os error 17)` writing `.next/server/app/_global-error/page/next-font-manifest.json`
      and succeeded on a local-storage copy of the same tree)

### Task 4: Record the ruling in the docs that carry the rationale
- [x] Amend decision 4 in `docs/architecture.md`: keep the 2026-08-13 "flow metrics are graded too"
      and 2026-08-16 "capped at amber" history, and add the 2026-09-03 ruling that they are now
      CAUTION-ONLY, with the reason — a flow cost is not a governance failure and must not gate
      enablement
- [x] Update the "Each caps at amber; see decision 4 above" line in the scope-boundaries section of
      `docs/architecture.md` to say they impose no ceiling
- [x] Rewrite the `README.md` flow-signal paragraphs: replace "**A flow signal caps at `amber`,
      however far above its maximum it sits**" with the caution-only statement, keep the estate
      evidence, and note that `-above-target` and `-not-observed` now both land in `caution` so a
      flow signal never decides a label
- [x] Check the readiness-assessment section of `README.md` (~620-645) still reads correctly —
      "Only blocking conditions carry a `label`" stays true, since a caution has no label to report —
      and the `caution` bullet now names the flow signals alongside the status-check gate
- [x] run `uv run poe check` — must pass at 100% coverage before task 5 (100% coverage, ruff and mypy
      clean; only the three pre-existing `test_cli` collect tests fail, as noted above)

### Task 5: Verify acceptance criteria
- [x] run `uv run poe check` and confirm the coverage summary reads 100% (4520 of 4520 statements;
      ruff and mypy clean; only the three pre-existing `test_cli` collect tests fail, as noted above)
- [x] run `npm --prefix ui run check` and confirm the coverage summary reads 100%, with `build` the
      only step allowed to fail and only for the environment reason above (605 tests, 100% of
      statements, branches, functions and lines; `next build` compiled and typechecked, then died on
      the mount with `ENOENT … mkdir '.next/standalone/node_modules/next/dist/server/lib/trace'`)
- [x] confirm neither coverage threshold was lowered: `fail_under = 100` in `pyproject.toml` and all
      four `thresholds` still at 100 in `ui/vitest.config.mts`
- [x] grep `src`, `ui/src`, `README.md` and `docs/architecture.md` for remaining claims that a flow
      signal or distribution blocks or caps at amber, and fix any that survived — none did. The
      surviving "cap at amber" wording belongs to `ContributorsTable`'s bypass columns, to a comment
      about a RATE the policy caps at amber, and to decision 4's 2026-08-16 history line, which the
      2026-09-03 ruling beneath it supersedes and which task 4 kept on purpose
- [x] add a `tests/test_render.py` case asserting `render_assessment` prints the flow `-above-target`
      conditions under `Caution` and leaves `Blocking` empty for an otherwise-compliant repository,
      building the assessment in memory from the policy
