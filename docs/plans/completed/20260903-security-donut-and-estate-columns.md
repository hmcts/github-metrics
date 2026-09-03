# Plan: Security donut, CODEOWNERS and Sonar columns, and three renamed bands

## Overview
Add a sixth estate donut to `/repositories` distributing every configured repository by security issues,
add CODEOWNERS and Sonar columns to the estate table after Stale, and rename three donut bands —
"Required" to Enforced, "Not required" to Unenforced, "None unreviewed" to Clear. The donut and the two
columns need facts the `/repositories` row does not carry, so the row gains six optional fields read off
evidence blocks the service already holds.

## Context

### Where the data comes from
Six optional fields on `RepositoryRow` (`src/metrics/service.py`), read off the evidence block
`repository_row` already has in hand:

| Field | Read from | Absent when |
| --- | --- | --- |
| `codeowners_files` | `len(codeowners.codeowners.files)` | the block carries a reason instead — nobody could read the repository's contents |
| `sonar_reported` | `sonar.measures is not None` | never on a reportable row: `False` where the block carries a reason instead of measures |
| `security` | `security.alerts`, the existing `SecurityAlertEvidence` verbatim | the whole security block carries a reason instead of alerts |
| `sonar_security_rating` | `sonar.measures.security_rating` | no measures, or the project reported no rating |
| `sonar_security_issues` | `sonar.measures.security_issues` | the same two cases |
| `sonar_security_hotspots` | `sonar.measures.security_hotspots` | the same two cases |

All six are absent on the `detail` branch — a repository the span could not report at all. `security`
reuses `SecurityAlertEvidence` rather than flattening three families into six scalars: the model is
already mirrored in `ui/src/lib/types.ts`, and its per-family `open`/`by_severity`/`detail` is exactly
what the band needs, including the distinction between a family with nothing open and one GitHub refused.

`sonar_reported` is `True` where measures were read — a project that resolved but whose measures could
not be read reads **No**, because the column answers "is there Sonar information here".

### The security band
`securityBand` in `ui/src/lib/tone.ts`, beside the other five band tables. It resolves each signal to a
tone, or to nothing where there is no data, and the worst tone present decides the band:

| Signal | Read by | High | Medium | Clear | No data |
| --- | --- | --- | --- | --- | --- |
| dependabot, code scanning | `alertTone`, unchanged | a critical or high alert open | any other alert open | 0 open | family refused |
| secret scanning | `alertTone`, unchanged | any alert open | — | 0 open | family refused |
| Sonar security rating | new threshold | C, D or E (3–5) | B (2) | A (1) | absent, or off the 1–5 scale |
| Sonar security issues, hotspots | count | — | above zero | 0 | absent |

Code scanning is banded on the same rule as dependabot, and a readable code-scanning family counts as
data. The three alert families delegate to `alertTone`, which the repository page already colours its
security cards with, so the two cannot disagree about one family.

The rating threshold is the one place this donut is deliberately **stricter than the repository page**:
`sonarRatingTone` puts C at amber, following Sonar's own scale, and this puts C at High on the user's
instruction. The divergence is stated in the code so it is not later "fixed" into agreement.

`unknown` is only where every signal above has no data — an unreportable row, or one whose three alert
families were all refused and which has no Sonar measures. A repository with readable alert families and
no Sonar project is Clear, not Unknown.

Bands read best first, `Clear · Medium · High · Unknown`, as the five donuts beside it do.

### The two columns
Between Stale and Findings, on the shared `RepositoriesTable` — which `/teams/[team]` also renders, so
both columns appear there too:

| Column | Yes | No | Dash |
| --- | --- | --- | --- |
| CODEOWNERS | `codeowners_files >= 1` | `0` — every checked location was looked at and held no file | absent: nobody could read the contents |
| Sonar | `sonar_reported` | measures could not be read, or no project resolved | absent: the span could not report the repository |

Both are sortable, and both sort unmeasured last in either direction, as every numeric column does.
Neither is coloured: no figure in this table carries a tone today, and the Sonar answer grades nothing at
all.

### Renamed bands
`REVIEW_BANDS` and `CHECKS_BANDS` rename `required` to **Enforced** and `none` to **Unenforced**;
`UNREVIEWED_BANDS` renames `none` to **Clear**. Keys are untouched — they are not user-visible, and
`unreviewedBand` matches the policy's own `none`/`within`/`above` values against them. The
`/repositories` tooltips and both READMEs move to the new words with them.

### Files involved
- `src/metrics/service.py`, `tests/test_service.py`
- `ui/src/lib/types.ts`, `ui/src/lib/tone.ts`, `ui/src/lib/chart.ts`, `ui/src/lib/rows.ts`
- `ui/src/components/RepositoriesTable.tsx`, `ui/src/app/repositories/page.tsx`,
  `ui/src/app/repositories/loading.tsx`
- `ui/src/lib/__tests__/{tone,chart,rows}.test.ts`,
  `ui/src/components/__tests__/{list-pages,tables,components}.test.ts`,
  `ui/src/components/__tests__/RepositoriesTable.test.tsx`
- `README.md`, `ui/README.md`

### Related patterns
- The 2026-09-03 donuts change is the precedent end to end: facts on the row, thresholds in `tone.ts`,
  counting in `chart.ts`, tooltip saying what unknown covers.
- Security is **report-only and ungraded** in the service (`architecture.md`, "Readiness assessment"), so
  the band is decided in the UI's threshold table and no grade is added to `assessment.py`.
- `tone.ts` must not import from `repository.ts`, which imports it — so the three family names are listed
  locally rather than through `ALERT_FAMILIES`.

### Dependencies
None. No new package, no collected fact, no stored state: the figures come out of caches already on disk.

## Development Approach
- Code then tests, task by task. Both gates sit at 100% coverage, so a new branch without a test fails
  the build in the task that introduced it.
- Complete each task fully before moving to the next.
- Run one command per shell call, per `AGENTS.md`.

## Validation Commands
- `uv run poe check`
- `npm --prefix ui run check`

## Implementation Steps

### Task 1: Rename the three band words
- [x] Rename `REVIEW_BANDS`' and `CHECKS_BANDS`' `required` band to `Enforced` and their `none` band to `Unenforced` in `ui/src/lib/tone.ts`, leaving both keys and every threshold untouched
- [x] Rename `UNREVIEWED_BANDS`' `none` band to `Clear`, noting that the key stays the policy's own value `unreviewedBand` matches against
- [x] Move the "Enforces review", "Enforces CI" and "Unreviewed substantial merges" tooltips in `ui/src/app/repositories/page.tsx` to the new words
- [x] Update the band words in the existing assertions in `ui/src/lib/__tests__/chart.test.ts` and `ui/src/components/__tests__/list-pages.test.ts`
- [x] Run `npm --prefix ui run check` — lint, typecheck and 556 tests pass at 100% coverage; `next build` fails writing into `.next` (`File exists`, os error 17), the known sandbox mount problem, not this change

### Task 2: Serve the six row fields
- [x] Add `codeowners_files`, `sonar_reported`, `security`, `sonar_security_rating`, `sonar_security_issues` and `sonar_security_hotspots` to `RepositoryRow` in `src/metrics/service.py`, all optional, documenting that absent means unmeasured as every other field on the row does
- [x] Populate all six in `repository_row` from the CODEOWNERS block's file count, whether Sonar measures were read, the security block's alerts and the three Sonar security measures, leaving all six absent on the unreportable branch
- [x] Document in the model's docstring that `sonar_reported` is `False` rather than absent where a reportable repository has no readable measures, so the column can tell "no Sonar" from "no report"
- [x] Write tests in `tests/test_service.py`: CODEOWNERS files found, none found and the block refused; measures present and absent; a security block with alerts and one carrying a reason instead; and a repository the span could not report leaving all six absent
- [x] Run `uv run poe check` — lint, format, mypy and 1187 tests pass at 100% coverage

### Task 3: Mirror the fields and band the security signals
- [x] Mirror the six fields on `RepositoryRow` in `ui/src/lib/types.ts` as `field?: T`, reusing `SecurityAlertEvidence` and `SonarRating`
- [x] Add a `SecuritySignals` input shape and `SECURITY_BANDS` to `ui/src/lib/tone.ts` — Clear, Medium, High, Unknown, best first, marks from `TONE_HEX`
- [x] Add `securityBand`, resolving the three alert families through `alertTone`, the rating through a threshold of its own and the two counts by whether they are above zero, then returning the worst tone present and `unknown` where no signal carried data
- [x] State in the comment why the rating threshold puts C at High while `sonarRatingTone` puts it at amber, and why `ALERT_FAMILIES` is not imported from `repository.ts`
- [x] Write tests in `ui/src/lib/__tests__/tone.test.ts`: each family raising High and Medium on its own, a refused family carrying no data, ratings 1 through 5 and off the scale, issues and hotspots at zero and above, every signal absent giving unknown, and a repository with clear alerts and no Sonar giving clear
- [x] Run `npm --prefix ui run check` — lint, typecheck and 564 tests pass at 100% coverage; `next build` fails writing into `.next` (`File exists`, os error 17), the known sandbox mount problem, not this change

### Task 4: Draw the sixth donut
- [x] Add `securitySlices` to `ui/src/lib/chart.ts`, counting rows through `securityBand` into `SECURITY_BANDS`
- [x] Write tests in `ui/src/lib/__tests__/chart.test.ts`: every band populated, a band left at zero and still returned, rows with no signals counted unknown, and an empty list summing to zero
- [x] Add the chart after Test coverage in `ui/src/app/repositories/page.tsx`, titled "Security issues", with a tooltip naming what raises High and Medium and stating that unknown is a repository with no security data at all rather than one with no Sonar project
- [x] Pass `count={6}` to `SkeletonChart` in `ui/src/app/repositories/loading.tsx` and update its docstring
- [x] Write tests: the six donut titles and the counts behind them in `ui/src/components/__tests__/list-pages.test.ts`, and the skeleton panel count in `ui/src/components/__tests__/components.test.ts`
- [x] Run `npm --prefix ui run check` — lint, typecheck and 568 tests pass at 100% coverage; `next build` fails writing into `.next/standalone` (`ENOTDIR`), the known sandbox mount problem, not this change

### Task 5: Add the CODEOWNERS and Sonar columns
- [x] Add `codeownersPresent` and an `answerOrder` sort value to `ui/src/lib/rows.ts`, both three-valued so an unreadable answer is `undefined` and sorts last in either direction
- [x] Add the two columns between Stale and Findings in `ui/src/components/RepositoriesTable.tsx`, reading `answerOrder` so each header sorts on what its cell prints
- [x] Add an `Answer` cell beside `Figure` rendering `Yes`, `No` or `ABSENT`, untoned as every other cell in this table is
- [x] Write tests in `ui/src/lib/__tests__/rows.test.ts` for both helpers over present, absent-with-zero and unmeasured rows, and for the column order and the three rendered answers — the latter in `ui/src/components/__tests__/RepositoriesTable.test.tsx` rather than `tables.test.ts`, which renders statically and has no router context for this component
- [x] Extend `ui/src/components/__tests__/RepositoriesTable.test.tsx` so a click on each new header sorts on it and leaves unmeasured rows last
- [x] Run `npm --prefix ui run check` — lint, typecheck and 576 tests pass at 100% coverage; `next build` fails writing into `.next/standalone` (`ENOTDIR`), the known sandbox mount problem, not this change

### Task 6: Document the change
- [x] Document the six row fields in `README.md`'s service section, including that `sonar_reported` is `False` rather than absent on a reportable repository with no readable measures
- [x] Update `README.md`'s gate precedence wording from "not required" to "unenforced" so the prose matches the donut
- [x] Update `ui/README.md`'s Pages table and donut paragraph for six donuts, the security band rule, and why its rating threshold is stricter than the repository page's
- [x] Note the two new columns in `ui/README.md`, including that the shared table shows them on `/teams/[team]` too
- [x] Run `npm --prefix ui run check` — lint, typecheck and 576 tests pass at 100% coverage; `next build` fails writing into `.next` (`ENOENT` on `mkdir .next/types`), the known sandbox mount problem, not this change

### Task 7: Verify acceptance criteria
- [x] Run `uv run poe check` — lint, format, mypy and 1187 tests pass at 100% coverage
- [x] Run `npm --prefix ui run check` and confirm both coverage gates still report 100% — the service reports 100% over 4513 statements, the UI 100% over statements, branches, functions and lines across 576 tests; `next build` fails writing a chunk into `.next` (`File exists`, os error 17), the known sandbox mount problem, not this change
- [x] Confirm the six donuts read in this order on `/repositories`: readiness labels, enforces review, enforces CI, unreviewed substantial merges, test coverage, security issues — the six `title=` props in `ui/src/app/repositories/page.tsx` read in exactly that order
- [x] Confirm the estate table's columns read: Team, Repository, Readiness, Merged, Direct commits, Open, Stale, CODEOWNERS, Sonar, Findings — `COLUMNS` in `ui/src/components/RepositoriesTable.tsx` reads in exactly that order
