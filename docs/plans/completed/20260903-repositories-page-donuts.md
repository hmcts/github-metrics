# Plan: Estate donuts on the repositories page

## Overview
Add four donuts after the readiness one on `/repositories` — enforced review, enforced CI, unreviewed
substantial merges and test coverage — each counting every configured repository at the span. Three of
the four need facts the `/repositories` row does not carry, so the row gains four optional fields and
the evidence block gains the policy's verdict on unreviewed substantial merging.

## Context

### Where the data comes from
`RepositoryRow` carries readiness, merge counts, open counts and finding occurrences, and nothing on it
can answer any of the four donuts. Four optional fields are added, read off the evidence block
`service.repository_row` already holds:

| Field | Read from | Absent when |
| --- | --- | --- |
| `required_approving_reviews` | the strictest `required_approving_review_count` across the gate's rules | no gate was collected, or the branch is protected and its rules were withheld |
| `required_status_checks` | how many required contexts the gate's rulesets name | the same two cases |
| `unreviewed_substantial` | the new evidence-block field below | the policy is off, the cohort is too thin to grade, or no substantial merge was measured |
| `sonar_coverage` | `sonar.measures.coverage` | the repository resolved to no SonarCloud project, or its measures could not be read |

All four are absent on a repository this span could not report at all, which is the `detail` branch of
`repository_row`.

The two gate fields follow the precedence `assessment.governance` already reads the gate in, which is
what puts an unprotected default branch in the red slice rather than the grey one:

1. no gate collected — absent, and the donut counts it unknown;
2. `protected` false — `0`, and the donut counts it not required, whatever else the gate says;
3. `protected` true and `rules_observed` false — absent: a protected branch whose rules GitHub withheld
   is evidence of nothing, and reporting it as requiring no review would blame a missing permission on
   the team that owns the repository;
4. otherwise the count the rules state.

### Unreviewed substantial merging
The percentage exists only inside the wording of an assessment condition today, so nothing can read it
without parsing a sentence. `ReadinessPolicy` gains one projection of the judgement it already makes —
`none`, `within` or `above` against its own allowance (`maximum_percentage` or `maximum_count`, either
one forgiving) — sharing the counts and the rounded percentage with the judgement so the two cannot
report the same window differently.

It returns nothing where the policy graded nothing: a cohort below `minimum_merges`, a window holding no
substantial merge, and an assessment that is disabled. All three land in the grey slice, because an
unmeasured thing is never reported as a pass.

### Bands and colour
`ui/src/lib/tone.ts` holds the thresholds and the marks, as it holds every other threshold in the UI;
`ui/src/lib/chart.ts` only counts rows into slices.

| Donut | Bands, best first |
| --- | --- |
| Enforces review | `>= 2` Multiple (deeper green) · `== 1` Required (green) · `== 0` Not required (red) · absent Unknown (slate) |
| Enforces CI | `>= 1` Required (green) · `== 0` Not required (red) · absent Unknown (slate) |
| Unreviewed substantial merges | `none` None unreviewed (green) · `within` Within allowance (amber) · `above` Above allowance (red) · absent Unknown (slate) |
| Test coverage | `>= 90%` 90% or more (green) · `>= 80%` 80% to 90% (amber) · below Below 80% (red) · absent Unknown (slate) |

The coverage boundary is the `floor(90, 80)` the repository page already colours coverage with, factored
into a shared `coverageTone` so that moving it moves both. `TONE_HEX` derives from `RAG_HEX`, so the one
new hex in the change is the deeper green for two or more required approvals, `#16a34a`, named in
`tailwind.config.ts` beside the other four and used as a chart mark only — no Tailwind class reads it.

`SummaryPieChart` needs no change: it already keeps a zero-count band out of the wedge and in the
legend, dimmed, and prints "No data" where nothing was counted at all.

### Scope
- Contract changes are additive and optional: four fields on `RepositoryRow` and one on
  `RepositoryPracticeEvidence`. Nothing is renamed or moved, and no stored state or collected fact
  changes, so the figures come out of caches already on disk.
- The text report is unchanged. `render_merge_gate` already prints both gate figures, and the assessment
  conditions already print the unreviewed substantial counts and the allowance they were judged against.
- No new table column. The donuts read the fields; the repository page states all four in its own words.
- The donuts count every configured repository at the span and are not filtered by the search box, as
  the readiness donut is not.

### Files involved
- `src/metrics/domain.py`, `src/metrics/assessment.py`, `src/metrics/evidence.py`,
  `src/metrics/render.py`, `src/metrics/service.py`
- `ui/src/lib/types.ts`, `ui/src/lib/tone.ts`, `ui/src/lib/chart.ts`, `ui/tailwind.config.ts`
- `ui/src/app/repositories/page.tsx`, `ui/src/app/repositories/loading.tsx`,
  `ui/src/components/Skeleton.tsx`
- `tests/test_domain.py`, `tests/test_assessment.py`, `tests/test_evidence.py`, `tests/test_render.py`,
  `tests/test_service.py`
- `ui/src/lib/__tests__/{tone,chart}.test.ts`, `ui/src/components/__tests__/list-pages.test.ts`,
  `ui/src/components/__tests__/components.test.ts`
- `README.md`, `ui/README.md`

### Related patterns
- `SonarRating.letter` in `domain.py` is the precedent for a derived property on an evidence model:
  pydantic does not serialise one, so the JSON contract is untouched by it.
- `distributionSlices` in `ui/src/lib/chart.ts` is the shape the four new slice builders follow — every
  band returned, including the ones counted at zero.
- `ui/src/lib/tone.ts` is where a figure's colour is decided and where each threshold names its
  `assessment.py` counterpart in a comment.

### Dependencies
None. No new package on either side.

## Development Approach
- Code then tests, task by task. Both gates sit at 100% coverage, so a new branch without a test fails
  the build in the task that introduced it.
- Complete each task fully before moving to the next.
- Run one command per shell call, per `AGENTS.md`.

## Validation Commands
- `uv run poe check`
- `npm --prefix ui run lint`
- `npm --prefix ui run typecheck`
- `npm --prefix ui run test`

`npm --prefix ui run check` is the UI's single gate where its `next build` step survives this mount; the
three commands above are the parts that judge the change when it does not.

## Implementation Steps

### Task 1: Derive the two gate figures once
- [x] Add a `required_approvals` property to `MergeGateEvidence` in `src/metrics/domain.py` returning the strictest `required_approving_review_count` across `pull_requests`, and `0` where there is no rule
- [x] Add a `required_contexts` property returning every required status-check context across `status_checks` in rule order, leaving callers to sort it for display as both already do
- [x] Read both properties in `ReadinessPolicy.review_requirement` and `ReadinessPolicy.status_checks` in `src/metrics/assessment.py`, replacing the two inline expressions
- [x] Read both in `render_merge_gate` in `src/metrics/render.py`, keeping the rendered lines byte-for-byte what they were
- [x] Write tests in `tests/test_domain.py` for both properties: no rules, one rule, several rules of differing strictness, and several rulesets naming contexts
- [x] Run `uv run poe check` - must pass before task 2

### Task 2: Report the policy's verdict on unreviewed substantial merging
- [x] Add an `UnreviewedSubstantialOutcome` string enum to `src/metrics/domain.py` with `none`, `within` and `above`, documenting that it projects a judgement the policy already makes rather than adding one
- [x] Add `unreviewed_substantial: UnreviewedSubstantialOutcome | None = None` to `RepositoryPracticeEvidence`, documenting that absent means the policy graded nothing and never that nothing was found
- [x] Factor `ReadinessPolicy.sufficient` out of `ReadinessPolicy.sample` in `src/metrics/assessment.py`, and factor the unreviewed counts, the rounded percentage and the allowance test out of `ReadinessPolicy.unreviewed_substantial` so the judgement's wording is unchanged and the arithmetic exists once
- [x] Add `ReadinessPolicy.unreviewed_substantial_outcome`, returning `None` where the cohort is insufficient or no substantial merge was measured, `none` where nothing merged unreviewed, `within` where the allowance forgives it, and `above` where it does not
- [x] Set the field in `PracticeEvidence.practices` in `src/metrics/evidence.py`, only where the policy is enabled
- [x] Write tests in `tests/test_assessment.py` for every band and every absent case, and in `tests/test_evidence.py` for the block carrying the outcome and for a disabled policy leaving it absent
- [x] Run `uv run poe check` - must pass before task 3

### Task 3: Serve the four row fields
- [x] Add `required_approving_reviews`, `required_status_checks`, `unreviewed_substantial` and `sonar_coverage` to `RepositoryRow` in `src/metrics/service.py`, all optional, documenting that absent means unmeasured as every other count on the row does
- [x] Add a helper that returns the gate whose rules can be read as configuration, or nothing where they cannot: no gate, or protected with `rules_observed` false — the precedence `assessment.governance` reads the gate in, and the reason an unprotected branch reports `0` rather than nothing
- [x] Populate the four fields in `repository_row` from the gate properties, the evidence block's new outcome and `sonar.measures.coverage`, leaving all four absent on the unreportable branch
- [x] Write tests in `tests/test_service.py` covering an unprotected branch, a protected branch with rules withheld, no gate at all, a gate with rules, Sonar measures present and absent, and a repository the window could not report
- [x] Run `uv run poe check` - must pass before task 4

### Task 4: Band and colour the four figures
- [x] Mirror the four new fields in `RepositoryRow` in `ui/src/lib/types.ts` as `field?: T`, with an `UnreviewedSubstantialOutcome` union of the three service values
- [x] Name `rag.green-strong` `#16a34a` in `ui/tailwind.config.ts` beside the other four, noting it is a chart mark that no class reads
- [x] Add `TONE_HEX` to `ui/src/lib/tone.ts`, derived from `RAG_HEX` so no hex is restated, and `STRONG_GOOD_HEX` for the one mark that is not a tone
- [x] Add `coverageTone` and have `SONAR_MEASURE_TONE.coverage` delegate to it, so the repository page and the donut read one boundary
- [x] Add a band table and a classifier for each of the four donuts — the key, the word and the mark per band, in best-first order — with each threshold naming what it came from
- [x] Write tests in `ui/src/lib/__tests__/tone.test.ts` over every boundary and every absent value, including `>= 2` against exactly `1` and coverage at exactly 90 and 80
- [x] Run `npm --prefix ui run test` - must pass before task 5

### Task 5: Count the rows into slices
- [x] Add a `bandSlices` helper to `ui/src/lib/chart.ts` that counts rows into a band table's order and returns a `PieSlice` per band, zero counts included
- [x] Add the four builders over `readonly RepositoryRow[]` for enforced review, enforced CI, unreviewed substantial merges and test coverage
- [x] Write tests in `ui/src/lib/__tests__/chart.test.ts` for each builder: every band populated, a band left at zero and still returned, rows with the field absent counted unknown, and an empty list summing to zero
- [x] Run `npm --prefix ui run test` - must pass before task 6

### Task 6: Draw the donuts
- [x] Add the four charts after the readiness one in `ui/src/app/repositories/page.tsx` and widen the grid to `grid-cols-1 md:grid-cols-2 lg:grid-cols-3`
- [x] Give each a tooltip saying what the bands mean and what its unknown slice covers, including that the gate says a check is required rather than which check or whether it passed
- [x] Give `SkeletonChart` in `ui/src/components/Skeleton.tsx` a `count` defaulting to 1, and pass 5 from `ui/src/app/repositories/loading.tsx` so the bones match the page
- [x] Write tests: the route test in `ui/src/components/__tests__/list-pages.test.ts` asserts the five donut titles and the counts behind them, and the skeleton test in `ui/src/components/__tests__/components.test.ts` asserts the panel count
- [x] Run `npm --prefix ui run test` - must pass before task 7

### Task 7: Document the change
- [x] Document the four row fields in `README.md`'s service section, with the gate precedence and why an unprotected branch reads as not required while a withheld one reads as unknown
- [x] Document the evidence block's `unreviewed_substantial` field in the same place, stating that absent means the policy graded nothing
- [x] Update the Pages table and the Colour section of `ui/README.md` for the five donuts, `rag-green-strong` and `TONE_HEX`
- [x] Note in `ui/README.md` that the donuts count every repository at the span and are not filtered by the search box
- [x] Run `npm --prefix ui run lint` - must pass before task 8

### Task 8: Verify acceptance criteria
- [x] Run `uv run poe check`
- [x] Run `npm --prefix ui run lint`
- [x] Run `npm --prefix ui run typecheck`
- [x] Run `npm --prefix ui run test` and confirm both coverage gates still report 100%
- [x] Confirm the five donuts read in this order on `/repositories`: readiness labels, enforces review, enforces CI, unreviewed substantial merges, test coverage
