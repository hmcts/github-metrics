# Plan: Actor combination summary, Enable/Review/Blocked grouping, and percentages

## Overview
Add a second counting block to `--format report`: how many people carry each combination of readiness
labels, with multiplicity ignored, grouped into Enable, Review and Blocked. Group the rendered actor
list under the same three names, give every count in both summaries a percentage of the population it
is counted over, and rename the existing `Summary` block to `Repository Summary`.

## Context
- Files involved:
  - `src/metrics/render.py` — `render_summary`, `readiness_counts`, `render_actors`, `actor_line`,
    `actor_repositories`, `readiness_tally`, `render_practice_report`, and the `percent` helper.
  - `tests/test_render.py` — `summary_lines`, `actor_lines`, `population_report`, `actors_report` and
    the actor and summary test groups around lines 1009-1060 and 1200-1370.
  - `tests/test_cli.py` — the end-to-end report assertion around line 1417 asserts
    `report.startswith("Summary\n-------\n…")` and breaks on the rename.
  - `docs/architecture.md` — "Scope boundaries", the per-label-count and per-actor-listing entries.
  - `README.md` — the report section around lines 845-880, which shows the `Summary` block twice.
- Related patterns:
  - Presentation only. Every figure is read off the models the JSON already emits, so the two
    renderings cannot disagree; nothing here measures, re-judges or collects anything.
  - Layout goes through the existing helpers: `heading`, `row_text`, `pairs`, `percent`. Nested rows
    are produced by indenting the first cell, not by a second table implementation.
  - Label names come from `READINESS_LABEL_NAMES` / `actor_readiness_label`, never spelled a second
    time, so a label added to `ReadinessLabel` cannot leave two lists out of step.
  - `actor_repositories` already drops `cannot_assess` from this section; combinations are taken from
    what it returns, so no combination can carry that label.
- Decisions taken with the user on 2026-09-01:
  - Each group prints its own subtotal with a percentage, as well as the combination rows beneath it.
    This is a further, dated reversal of the no-roll-up ruling and has to be recorded as one.
  - The `Actor Summary` block sits immediately above the `Actors` section it summarises, mirroring
    `Repository Summary` sitting immediately above the `Index`.
  - The seven combinations the user listed print at every run, zero included, so two runs diff line
    for line. A combination the list does not cover — only reachable through `NOT ASSESSED`, since all
    seven non-empty subsets of GREEN/AMBER/RED are mapped — prints under `Ungrouped` where it occurs.
  - Percentages are added to the two summary blocks only. `Review breakdown`, `Cohort` and the metric
    classification counts are left alone.
- Dependencies: none.

## Development Approach
- Code then tests, per task, matching how the existing render blocks were built.
- Coverage is enforced at 100% (`fail_under`), so every branch added needs a test that reaches it.
- Complete each task fully before moving to the next.

## Validation Commands
- `uv run poe test`
- `uv run poe check`

## Implementation Steps

### Task 1: Percentages and the Repository Summary rename
- [x] Add `percentage_of(count: int, total: int) -> str` to `src/metrics/render.py`, returning
      `percent(round(count / total * 100, 1))` and delegating to `percent(None)` — a dash — when
      `total` is zero, so a report covering nothing prints no `0%` it never divided.
- [x] Change `render_summary` to render three cells per row — name, count, percentage — over the total
      of every row it counts (repositories plus unavailable), computing the widths across all three.
- [x] Rename the block's heading from `Summary` to `Repository Summary`, and update its docstring to
      say what the percentage is a percentage of.
- [x] Update `summary_lines` in `tests/test_render.py` to find the renamed heading, and the existing
      summary tests to assert the percentage column.
- [x] Add a test that a report covering no repositories prints dashes rather than percentages.
- [x] Update the `report.startswith(...)` assertion in `tests/test_cli.py` for the new heading and
      column.
- [x] run `uv run poe test` - must pass before task 2

### Task 2: Actor label combinations and their groups
- [x] Add `actor_combination(repositories: Sequence[ActorRepositoryReadiness]) -> tuple[str, ...]`
      returning the DISTINCT labels one person's line carries, best first, in
      `READINESS_LABEL_NAMES` order with `NOT ASSESSED` last — so `RED x 6` and `RED` are one
      combination, and `RED x 2, GREEN` is `GREEN, RED`.
- [x] Add the group table as a module-level literal transcribing the user's instruction exactly, rows
      in the order given: `Enable` — `GREEN`, `GREEN, AMBER`; `Review` — `AMBER`,
      `GREEN, AMBER, RED`, `GREEN, RED`; `Blocked` — `AMBER, RED`, `RED`. Written out rather than
      derived from a rule, because it is a ruling about what to do next and not a property of the
      labels; document that in the constant's docstring.
- [x] Add `actor_group(combination: tuple[str, ...]) -> str` resolving through that table and falling
      back to `Ungrouped`, and note in its docstring that only a combination carrying `NOT ASSESSED`
      can reach the fallback, since all seven graded combinations are mapped.
- [x] Add `actor_combination_counts(actors: Sequence[ActorReadiness]) -> Mapping[tuple[str, ...], int]`
      counting the people behind each combination, over the same `actor_repositories` the rendered
      lines use and skipping anyone it leaves with no repository.
- [x] write tests for combination collapsing, ordering, every one of the seven group assignments, the
      `Ungrouped` fallback, and that an actor excluded from the list is excluded from the counts
- [x] run `uv run poe test` - must pass before task 3

### Task 3: Render the Actor Summary block
- [x] Add `render_actor_summary(actors: Sequence[ActorReadiness]) -> tuple[str, ...]` printing the
      heading `Actor Summary`, then per group a subtotal row and one indented row per combination,
      each with a count and a percentage of the people the section reports.
- [x] Print the seven listed combinations at every run, zero included; print an `Ungrouped` group and
      its rows only where something carries them.
- [x] Align every row through one `row_text` width calculation across the whole block, indenting
      combination rows by prefixing the first cell so the numbers stay in one column.
- [x] Wire it into `render_practice_report` immediately above `render_actors`.
- [x] Record in the docstring that the subtotals are the user's 2026-09-01 instruction and are a
      dated reversal of the no-roll-up ruling, pointing at architecture.md "Scope boundaries".
- [x] write tests for the block's position in the report, the fixed row set, the subtotals, the
      percentages, a report with no actors, and a report whose only actors are excluded
- [x] run `uv run poe test` - must pass before task 4

### Task 4: Group the rendered actor list
- [x] Change `render_actors` to emit each person's line beneath their group's name, groups in the
      order Enable, Review, Blocked, Ungrouped, keeping the report's alphabetical login order within
      each group.
- [x] Omit a group nobody is in, rather than printing a heading with nothing under it — the Actor
      Summary already reports that zero — and keep the existing `none:` lines for a section with
      nobody to report.
- [x] Compute the login column width once across the whole section, so a person's labels line up
      across groups.
- [x] Update the existing actor tests for the group lines, and add tests for a section spanning
      several groups and for an omitted empty group.
- [x] run `uv run poe test` - must pass before task 5

### Task 5: Record the decisions and update the report documentation
- [x] Add an entry under "Scope boundaries" in `docs/architecture.md`, dated 2026-09-01 and attributed
      to the user's instruction: per-combination actor counts are in scope, the Enable/Review/Blocked
      table is a named action grouping with subtotals, and that grouping is a further reversal of the
      no-roll-up ruling. Reproduce the table, and state what stays excluded — no per-team figure, no
      score, and no ordering of people by what they carry.
- [x] Note in the same entry that percentages are on the two summary blocks only, and that
      `render.actor_combination` and the group table are the two places to change if the grouping is
      revised.
- [x] Update the per-actor-listing entry to say the rendered section is now grouped, and the
      per-label-count entry for the `Repository Summary` rename and its percentages.
- [x] Update the report section of `README.md`: rename both `Summary` samples to `Repository Summary`
      with percentages, add an `Actor Summary` sample, show the grouped actor list, and say what each
      percentage is a percentage of.
- [x] run `uv run poe test` - must pass before task 6

### Task 6: Verify acceptance criteria
- [x] confirm the report renders, in order: `Repository Summary`, `Index`, the repository blocks,
      `Unavailable`, `Actor Summary`, `Actors`
- [x] confirm every count in both summary blocks carries a percentage, and that no percentage is
      printed where its population is empty
- [x] run `uv run poe check`
