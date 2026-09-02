# Plan: Repository page visual upgrade

## Overview

Carry the predecessor tool's visual grouping and its good/bad colouring onto the new repository page:
sections become one bounded panel each rather than a field of about forty free-floating cards, figures
carry tone, the merge gate and maintenance blocks condense into lists, and the contributors table
states what each person actually did.

## Context

- Pages and components: `ui/src/app/repositories/[repository]/page.tsx`, `ui/src/components/Section.tsx`,
  `MetricCard.tsx`, `MetricsGrid.tsx`, `AssessmentSection.tsx`, `RAGCard.tsx`, `ContributorsTable.tsx`,
  `Navigation.tsx`, `EntityHeader.tsx`, `FindingsTable.tsx`.
- Pure logic, tested under `ui/src/lib/__tests__`: `lib/repository.ts`, `lib/metrics.ts`, `lib/rag.ts`,
  and two new modules, `lib/tone.ts` and `lib/contributor.ts`.
- Service: `src/metrics/domain.py` (`ReadinessCondition`), `src/metrics/assessment.py` (`neutral`,
  `sufficient-merges`), `src/metrics/service.py` (`ContributorRow`).
- Related patterns: `lib/rag.ts` is the model for `lib/tone.ts` — total class maps keyed on a small
  closed set of states, with the palette named in `tailwind.config.ts` rather than as hex literals in
  components. `lib/repository.ts` is the model for every derivation: pure functions returning what the
  page renders verbatim.
- Dependencies: none new, on either side.

## Doctrine this reverses

Three written rules are being changed on the user's instruction, and each has to be rewritten rather
than quietly broken:

- `MetricCard`'s "NO TONE AND NO COLOUR. A card that coloured itself would be grading the figure it
  shows, and the readiness label is the only thing on this site that grades anything."
- `ui/README.md`'s design-token section, which says colour on a page is the readiness label and
  nothing else.
- `docs/architecture.md`, "Scope boundaries": per-person counts beside a login on a repository page
  are admitted. Ordering or ranking people by them is NOT, and stays excluded.

## Decisions taken

1. **Where judgement lives, split.** The readiness policy labels its own conditions; it already owns
   that call. Sonar, merge-gate, cohort, security and maintenance tone is a threshold table in
   `ui/src/lib/tone.ts`, each entry naming its `assessment.py` counterpart in a comment where one
   exists, so the page and the policy can be checked against each other by reading.
2. **Behaviour metric cards take their tone from the assessment condition** — `<metric>-at-target`
   under `clear` is green, `<metric>-below-target` under `blocking` takes the label it imposed, a
   caution is amber. No metric threshold is duplicated in the UI and a card can never contradict the
   CLEAR list above it. `checks-passing-at-merge` at 92.3% therefore reads green against its
   configured 90% target; raising the target in the `assessment` configuration is what makes it amber.
3. **Contributor figures are derived in the UI.** `service.ContributorRow` carries the actor's metric
   summaries verbatim and `ui/src/lib/contributor.ts` does the subtraction. The size column reads
   MEDIAN, not "average": `pull-request-size` is a distribution and carries no mean.
4. **The `blocking` column is dropped.** It counts occurrences the Findings table above already lists
   per rule and per person.

## Tone rules

Four states: `good`, `warn`, `bad`, `neutral`. `neutral` renders exactly as the page does today — no
colour and no border tint — and is the default for any figure not named below.

| Figure | good | warn | bad | neutral |
| --- | --- | --- | --- | --- |
| Direct commits | 0 | 1 or more | — | — |
| CODEOWNERS | 1 or more files | absent | — | unreadable |
| Merges reported, Merges excluded, Branch, Rules not interpreted, Lines of code | — | — | — | always |
| Protected | yes | — | no | not disclosed |
| Approving reviews required | 1 or more | — | 0 | not disclosed |
| Required status checks | any | — | none | not disclosed |
| Dismiss stale reviews on push | yes | no | — | not disclosed |
| Applies to administrators | yes | no | — | not disclosed |
| Restricts deletions, Requires linear history, Restricts branch names | — | — | — | always |
| Rules observed | yes | — | — | no |
| Opened in window, Closed without merge, Currently open | — | — | — | always |
| Stale open | 0 | 1 or more | — | — |
| Dependabot, code-scanning | 0 | medium or low open | any critical or high open | not collected |
| Secret scanning | 0 | — | 1 or more | not collected |
| Maintenance window | yes | no | — | unknown |
| Quality gate | OK | — | ERROR | not reported |
| Coverage | 90% or above | 80% up to 90% | below 80% | absent |
| Duplicated lines | 3% or below | above 3% up to 5% | above 5% | absent |
| Sonar ratings | A | B, C | D, E | absent |
| Issue counts | 0 | above 0 | above 0 and its rating is D or E | absent |

Two rules need stating aloud:

- **The neutral trio is the policy's own.** Deletions, linear history and branch names are reported by
  `ReadinessPolicy.neutral()` as bearing on the label in neither state, so the gate cards for them
  carry no colour either. Colouring them here would grade something the policy deliberately does not.
- **An issue count borrows severity from its own rating.** A raw count has no absolute threshold — 71
  issues in 8,607 lines and 71 in a million are different findings — so `reliability_issues` reads the
  reliability rating, `security_issues` the security rating, `maintainability_issues` the
  maintainability rating, and `security_hotspots` the security review rating. `violations` has no
  rating of its own, so above zero it is amber. This gives reliability issues 71 red, security issues
  13 red, security hotspots 0 green, and maintainability issues 1,358 amber despite its A rating.

## Development approach

- Code then tests, per task, matching how the repository's own modules are laid out: the derivation
  goes in `ui/src/lib` where a test can reach it, the markup in `ui/src/components` where
  `renderToStaticMarkup` can assert what reaches the reader.
- Complete each task fully before moving to the next.
- No figure is computed that the report does not carry or that a reader could not reach by adding the
  report's own rows up. Tone is presentation, not a new figure.

## Validation Commands

- `uv run poe check`
- `npm --prefix ui run lint`
- `npm --prefix ui run typecheck`
- `npm --prefix ui run test`

NOT `npm --prefix ui run check`. Its `next build` step fails on this mounted filesystem at the
`output: 'standalone'` trace-copy for environment reasons unrelated to the code, and the three
commands above are the parts that judge the change.

## Implementation Steps

### Task 1: Section becomes a panel and MetricCard goes flat

- [x] Rewrite `ui/src/components/Section.tsx` so the heading, its detail, its action and its children
      sit inside one container — `bg-slate-900/40 border border-slate-800 rounded-lg`, heading row
      separated from the body by `border-b border-slate-800` — instead of a bare heading above loose
      children
- [x] Drop the `bg-slate-900 rounded-lg border border-slate-800` from `ui/src/components/MetricCard.tsx`
      so a grid of cards inside a panel reads as one surface rather than as boxes nested in a box; keep
      the label, value and detail typography unchanged
- [x] Update the component doc comment on each to say why: the page had about forty bordered boxes with
      nothing saying which belonged together, and the box is now the section rather than the figure
- [x] Check `/`, `/actors/[login]` and `/teams/[team]` for sections that now double-box — tables and
      charts already carry their own border — and flatten the inner border where they do
- [x] Update the `Section` and `MetricCard` assertions in `ui/src/components/__tests__/components.test.ts`
      to the new markup, and add a case asserting a `Section` renders its heading inside the panel
- [x] run `npm --prefix ui run lint`, `npm --prefix ui run typecheck` and `npm --prefix ui run test` —
      all three must pass before task 2

### Task 2: The tone vocabulary

- [x] Add `ui/src/lib/tone.ts` with the `Tone` type (`good | warn | bad | neutral`), total class maps
      over it for a value colour and for a left border, resolved through the existing `rag-*` palette,
      and a doc comment recording that `MetricCard`'s no-colour rule was reversed on 2026-09-02 at the
      user's instruction
- [x] Add the threshold functions for the figures in the tone table above — cohort, CODEOWNERS, gate
      field, open pull requests, alert family, maintenance window, Sonar gate, Sonar measure and Sonar
      rating — each a pure function taking the value the report sent and returning a `Tone`, with a
      comment naming its `assessment.py` counterpart where one exists
- [x] Give `MetricCard` an optional `tone` prop, defaulting to `neutral`, that colours the value only —
      the label and detail stay slate, so a coloured page still reads as one surface
- [x] Write tests in `ui/src/lib/__tests__/tone.ts` covering every threshold boundary in the table,
      including the neutral trio, an absent value staying neutral rather than becoming good, and an
      issue count taking its rating's severity (written as `tone.test.ts`: vitest collects
      `src/**/*.test.ts` only, so a `tone.ts` under `__tests__` would be collected by nothing)
- [x] Rewrite the "grades nothing" case in `ui/src/components/__tests__/components.test.ts` as its
      replacement: a card given no tone is still colourless, and a card given one colours the value
      and nothing else
- [x] run `npm --prefix ui run lint`, `npm --prefix ui run typecheck` and `npm --prefix ui run test` —
      all three must pass before task 3

`Blocks force pushes` is the one merge-gate field the tone table does not name, though
`assessment.force_pushes` cautions on it exactly as `stale_reviews` does. It is left neutral by the
table's own default rule for an unnamed figure; say so if it should read amber like its counterpart.

### Task 3: Informational conditions, service side

- [x] Add `informational: bool = False` to `ReadinessCondition` in `src/metrics/domain.py`, with a
      docstring line saying it marks a condition the policy reports without judging, so a renderer can
      tell a satisfied check from one that bears on the label in neither state
- [x] Set it in `ReadinessPolicy.neutral()` in `src/metrics/assessment.py`, and on the
      `sufficient-merges` clear — it states a precondition for grading, not a practice that went well
- [x] Confirm `src/metrics/render.py` output is unchanged: the flag is for the UI and adds no line to
      the text report
- [x] Mirror the field in `ui/src/lib/types.ts` as `informational?: boolean`, guarded with `== null`
      where read, per the missing-key rule
- [x] Colour the rows in `ui/src/components/AssessmentSection.tsx` through `tone.ts`: a blocking
      condition keeps its own readiness label, a caution is amber, a clear condition is green, and an
      informational one carries no colour at all
- [x] Add pytest cases under `tests/` asserting the flag is set on every `neutral()` condition and on
      `sufficient-merges`, and unset on a graded clear such as `independent-review-coverage-at-target`
- [x] Add a component test asserting an informational clear row renders without a colour class while a
      graded clear row renders green
- [x] run `uv run poe check` and the three `npm --prefix ui` commands — all must pass before task 4

### Task 4: Tone on the repository figures

- [x] Add an optional `tone` to `LabelledValue` in `ui/src/lib/repository.ts`
- [x] Set it through `tone.ts` in `mergeGateRows`, `openPullRequestCards`, `securityCards`,
      `maintenanceRows`, `codeownersCard`, `sonarGateCard` and `sonarRows`, and on the three cohort
      cards built in the page
- [x] Pass it through the `ValueCards` helper in `ui/src/app/repositories/[repository]/page.tsx` to
      `MetricCard`
- [x] Extend `ui/src/lib/__tests__/repository.test.ts` with the tone each row carries, including the
      unreadable cases that must stay neutral — a refused alert family, an absent Sonar measure, a gate
      field GitHub did not disclose
- [x] run `npm --prefix ui run lint`, `npm --prefix ui run typecheck` and `npm --prefix ui run test` —
      all three must pass before task 5

### Task 5: Behaviour metric tone from the assessment

- [x] Add a pure function to `ui/src/lib/metrics.ts` that maps one metric identifier to the assessment
      condition that graded it — `<identifier>-at-target`, `-below-target` or `-not-observed`, searched
      across the three groups — and returns the `Tone` it implies: clear is good, caution is warn,
      blocking takes the label it imposed, and a metric no condition names stays neutral
      (`-above-target` is included too: `assessment.distribution` grades the four distribution metrics
      with that suffix, and leaving it out would have left every over-target median uncoloured)
- [x] Pass the assessment into `ui/src/components/MetricsGrid.tsx` as an optional prop and give each
      card its tone; an actor page renders the same grid with no assessment, so an absent one must
      leave every card neutral rather than throw
- [x] Extend `ui/src/lib/__tests__/metrics.test.ts` with a metric graded clear, one blocking at amber,
      one blocking at red, `review-depth` under caution, a `-not-observed` condition, and a grid with
      no assessment at all (the grid case is a markup test in `components/__tests__/repository.test.ts`,
      where the grid is rendered; `metrics.test.ts` covers the absent assessment at the function)
- [x] run `npm --prefix ui run lint`, `npm --prefix ui run typecheck` and `npm --prefix ui run test` —
      all three must pass before task 6

### Task 6: Condense the gate, maintenance and security blocks

- [x] Add `ui/src/components/DefinitionList.tsx`: label left, value right, one row per line, rows
      divided by `divide-y divide-slate-800/50`, the value coloured by its tone and `tabular-nums`
      where numeric — the shape the predecessor's BRANCH PROTECTION panel read well in
- [x] Render the twelve merge-gate fields, the three maintenance windows and the three alert families
      through it instead of the four-across card grid, keeping each row's detail line where it has one
- [x] Leave the cohort cards, the four open pull-request counts and the Sonar measures as cards: they
      are headline figures rather than a settings list, and the Sonar block reads as a grid
- [x] Add markup tests for `DefinitionList` in `ui/src/components/__tests__/components.test.ts` —
      label and value both present, tone class on the value only, and an empty list rendering nothing
      rather than an empty box
- [x] run `npm --prefix ui run lint`, `npm --prefix ui run typecheck` and `npm --prefix ui run test` —
      all three must pass before task 7

### Task 7: Drop the intervals-fetched line

- [x] Remove the `intervals_fetched` span from the header context in
      `ui/src/app/repositories/[repository]/page.tsx` — it is a caching detail with no meaning to a
      reader, and "0 intervals fetched" reads as missing data
- [x] Leave `WindowProvenance` in `ui/src/lib/types.ts` and the service untouched: the field stays in
      the contract and is simply not rendered
- [x] run `npm --prefix ui run lint`, `npm --prefix ui run typecheck` and `npm --prefix ui run test` —
      all three must pass before task 8

### Task 8: Contributor columns

- [x] Add `metrics: tuple[BehaviourMetricSummary, ...]` to `service.ContributorRow` in
      `src/metrics/service.py`, passed through from `ActorRepositoryReadiness` verbatim by
      `contributor_rows`, and record in the docstring that the service derives nothing from them
- [x] Mirror it in `ui/src/lib/types.ts` and drop nothing from `ContributorRow` there yet
- [x] Add `ui/src/lib/contributor.ts` deriving, per row, the merged pull requests
      (`independent-review-coverage` denominator minus its `direct-commit` classification), the direct
      pushes (that classification), the unreviewed merges (denominator minus numerator minus direct
      commits) and the median pull-request size from `pull-request-size`; each returns an absent marker
      rather than a zero where the summary is `not_applicable` or the metric is missing
- [x] Replace the `Blocking occurrences` column in `ui/src/components/ContributorsTable.tsx` with
      Merged PRs, Direct pushes, Unreviewed merges and Median size, keeping Contributions; tone the
      direct-push and unreviewed columns through `tone.ts` (0 good, above 0 warn) and leave the rest
      plain
- [x] Keep the table contributions-ordered with no sortable headers, and update its doc comment to say
      why: these are counts of what was done in one repository, never a score, and nobody is ordered by
      them
- [x] Write `ui/src/lib/__tests__/contributor.test.ts` covering a person with only pull requests, only
      direct pushes, a mix, an unmeasurable size distribution and a missing metric summary
- [x] Add or update the pytest case for `contributor_rows` asserting the metrics arrive unchanged
- [x] run `uv run poe check` and the three `npm --prefix ui` commands — all must pass before task 9

### Task 9: CONTRIBUTOR over ACTOR in the UI

- [x] Rename the visible word in `ui/src/components/Navigation.tsx`, the overview tile and section
      heading in `ui/src/app/page.tsx`, the `Actor` column in `ui/src/components/FindingsTable.tsx`,
      and the actor page's own heading copy (the team page's own `Actors` heading, `lib/team.ts`'s
      `people()` count, the `TeamsList` per-team count and the `layout.tsx` metadata description said
      it too, so they were renamed with them: the word has to be gone everywhere or the rename is
      only half true)
- [x] Rename `EntityKind`'s `'actor'` member to `'contributor'` and update the three call sites, since
      the kind is rendered as the word above the entity name
- [x] Leave the `/actors/…` routes, the `#actors` anchor, the service field names and `ActorRow` and
      friends alone: the request is the word on the page, and renaming routes is a separate change
- [x] Update the affected assertions in `ui/src/components/__tests__` and add one asserting no page
      renders the word "Actor" to the reader (a sweep in `components.test.ts` rendering every
      component that names people, matching `/actor/i` against the markup with `href` attributes
      stripped — the routes keep the old spelling on purpose)
- [x] run `npm --prefix ui run lint`, `npm --prefix ui run typecheck` and `npm --prefix ui run test` —
      all three must pass before task 10

### Task 10: Record the reversals in the documentation

- [x] Add a dated entry to `docs/architecture.md`, "Scope boundaries", recording that a figure on a
      page may carry tone from 2026-09-02 at the user's instruction, that the readiness policy remains
      the only thing that grades a repository, and that the UI's thresholds are presentation which the
      report neither states nor depends on
- [x] Record in the same place that per-person counts beside a login on a repository page are in
      scope, while ordering or ranking people by them stays excluded, and that `blocking` was dropped
      from the contributors table as a duplicate of the findings above it
- [x] Rewrite the colour paragraph in `ui/README.md`'s design tokens for the four tones, add
      `DefinitionList` and the panel `Section` to the token table, and update the guardrails list so
      the no-personal-ranking entry matches the new columns (the token table's `card` row went with
      it: `MetricCard` no longer draws the border it described, and a token table naming classes no
      component sets is worse than no entry)
- [x] Note in `ui/README.md` that `npm --prefix ui run check` includes a `next build` which fails on a
      case-insensitive mount, and name the three commands that judge a change
- [x] run `uv run poe check` — must pass before task 11

### Task 11: Verify acceptance criteria

- [x] run `uv run poe check`
- [x] run `npm --prefix ui run lint`
- [x] run `npm --prefix ui run typecheck`
- [x] run `npm --prefix ui run test`
- [x] confirm each of the user's six requests is met: sections are one panel each, the named figures
      carry the colours listed in the tone table, the gate and maintenance blocks read as lists, the
      intervals line is gone, the contributors table carries the four new columns and no blocking
      count, and no page says "Actor"
- [x] update `README.md` if any user-facing behaviour outside `ui/` changed (the service additions —
      `ReadinessCondition.informational` and `ContributorRow.metrics` — change no report line and no
      command, so the report contract needed nothing; what did need it was the compose port this
      branch moved to 80, which `README.md` still published as `localhost:3000`, and the `check` gate
      sentence, which named a build step `ui/README.md` now records as failing on a case-insensitive
      mount)
