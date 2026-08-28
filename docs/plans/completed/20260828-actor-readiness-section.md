# Plan: Actor readiness section in the evidence report

## Overview
Add a per-actor section to the `evidence` practice report: for every person who contributed to the
repositories in scope, list the readiness label of each repository they contributed to, ordered by their
contribution count. It is emitted in the JSON contract as a structured list and in `--format report` as one
abridged line per actor.

## Context
- Files involved:
  - `src/metrics/domain.py` — new `ActorRepositoryReadiness` and `ActorReadiness` models, and a new `actors`
    field on `PracticeEvidenceReport`.
  - `src/metrics/evidence.py` — the aggregation, as pure functions beside `cohort_summary`.
  - `src/metrics/cli.py` — `evidence_report` already holds both the `RepositoryEvidence` facts and the
    `RepositoryPracticeEvidence` built from them; it pairs them and calls the aggregation.
  - `src/metrics/render.py` — `render_actors`, appended last in `render_practice_report`.
  - `tests/test_evidence.py`, `tests/test_cli.py`, `tests/test_render.py` — mirrored tests.
  - `README.md` — the "Output format" section.
- Related patterns: `cohort_summary` for pure aggregation over cached facts; `comparable_login` and
  `in_cohort` in `analysis.py` for login normalisation; `index_readiness` in `render.py` for how a label is
  cased and how an unassessed repository is named; `pairs` for a two-column report block.
- Dependencies: none beyond what the project already uses.

## Definitions fixed by this plan
- **Actor**: a person who authored changes in a repository's cohort (`RepositoryEvidence.pull_requests` and
  `direct_commits`), matched case-insensitively through `comparable_login`, and displayed with the spelling
  used in the repository where they contributed most. **Bot accounts are left out** — the section is for
  reviewing people — using `is_human_account`, the same test `contributor_logins` applies. That is wider
  than the cohort's own exclusions: an agent-authored merge stays in the cohort and in every rate measured
  over it, but an agent is not a person to review, so it gets no actor line.
- **Contributions**: merged pull requests plus direct commits that actor authored in the window, after
  cohort exclusions.
- **Blocking**: the sum of `occurrences` across the `PracticeFinding` rows attributed to that actor in that
  repository, so twelve unreviewed merges count as twelve.
- **Readiness**: the repository's `assessment.label`. It is omitted from the JSON when the readiness policy
  is disabled, and rendered as `NOT ASSESSED` in the report, matching `index_readiness`.
- Scope is the default practice report only. A `--metric` drill-down is unchanged, and repositories reported
  in `unavailable` contribute no actors because no facts were read for them.

## JSON shape
```json
"actors": [
  {"actor_login": "alice",
   "repositories": [
     {"readiness": "red",   "repository": "project-x", "contributions": 41, "blocking": 12},
     {"readiness": "green", "repository": "project-z", "contributions": 3,  "blocking": 0}]}
]
```
Actors are alphabetical by case-folded login. Each actor's repositories are ordered by contributions
descending, ties broken by repository name.

## Report shape
```
Actors
------
  alice    RED x 2 (project-x, project-u), GREEN (project-z)
  bob      RED x 6
  carol    AMBER
```
A label is never repeated on a line: every repository carrying it is tallied into one group, wherever it sits
in the list. Groups are ordered by the total contributions behind them, largest first, ties broken by the
policy's severity precedence — red, cannot assess, amber, green, then not assessed — so an actor's weightiest
label leads. Repositories within a group keep the contribution order. `x N` is omitted when N is 1.
Repository names appear only when the actor has more than one distinct label, so a uniform actor renders
`RED x 6` alone.

This lists labels and never combines them: there is no per-actor verdict and no total, which is the same
discipline `render_index` already follows under "Scope boundaries" in `docs/architecture.md`.

## Development Approach
- Code then tests, per task, matching how the existing modules are covered.
- Complete each task fully before moving to the next.

## Validation Commands
- `uv run poe test`
- `uv run poe check`

## Implementation Steps

### Task 1: Model and aggregate actor readiness
- [x] Add `ActorRepositoryReadiness` (readiness, repository, contributions, blocking) and `ActorReadiness`
      (actor_login, repositories) to `src/metrics/domain.py`, with docstrings stating what each count means
- [x] Add the required `actors: tuple[ActorReadiness, ...]` field to `PracticeEvidenceReport`
- [x] Add `actor_contributions` to `src/metrics/evidence.py`: case-folded login to merged pull requests plus
      direct commits authored, skipping unattributed changes and every account `is_human_account` rejects
- [x] Add `actor_blocking_occurrences` to `src/metrics/evidence.py`: case-folded login to the summed
      `occurrences` of that actor's practice findings
- [x] Add `actor_readiness` to `src/metrics/evidence.py`, taking the paired repository evidence and practice
      evidence and returning the alphabetical actor list with each actor's contribution-ordered repositories
- [x] Write tests in `tests/test_evidence.py`: ordering by contributions with a name tie-break, alphabetical
      actors, two spellings of one login merged, an unassessed repository carrying no label, an actor
      present in one repository only, blocking occurrences summed across findings, and a bot account that
      merged into the cohort getting no actor entry
- [x] run `uv run poe test` - must pass before task 2

### Task 2: Build the section in the evidence command
- [x] Pair each `RepositoryEvidence` with the `RepositoryPracticeEvidence` built from it in
      `evidence_report` and populate `actors` from the pairs
- [x] Write tests in `tests/test_cli.py`: the JSON carries the section, `--repository` narrows it to that
      repository's actors, and a repository reported as unavailable contributes no actors
- [x] run `uv run poe test` - must pass before task 3

### Task 3: Render the abridged actor lines
- [x] Add `readiness_tally` to `src/metrics/render.py`, tallying each label once and ordering the groups by
      the contributions behind them, worst label first on a tie
- [x] Add `render_actors` and append it last in `render_practice_report`
- [x] Write tests in `tests/test_render.py`: uniform labels render `RED x 6` with no repository names, a
      label split across non-adjacent repositories still renders as one group, groups order by their summed
      contributions rather than by first appearance, a single repository renders the bare label, an
      unassessed repository renders `NOT ASSESSED`, and an empty actor list says so rather than vanishing
- [x] run `uv run poe test` - must pass before task 4

### Task 4: Verify acceptance criteria
- [x] Document the section in README "Output format": the JSON fields, the abridged line format, that bots
      are left out while their merges stay in the cohort, and that it lists labels rather than combining them
- [x] run `uv run poe check` - Ruff lint and formatting, strict mypy, and pytest at 100% coverage
