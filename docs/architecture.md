# Metrics — Settled Architecture Decisions

Durable rulings. Do not re-litigate these; if one is wrong, change it here deliberately and say why.
Iteration plans live under [plans/](plans/), one dated file per plan (`YYYYMMDD-short-name.md`);
completed plans are kept under [completed/](../completed/).

Every section describes the code as it stands.

## Purpose

Assess a team's SDLC maturity BEFORE an AI rollout, from observable delivery behaviour. Entry
criteria first: that is the question the tool was built to answer and it remains the first one.

AMENDED 2026-08-15 — POST-ENABLEMENT TREND MEASUREMENT IS NOW IN SCOPE. This section previously said
post-rollout impact was "explicitly later phases and out of scope for now", and that sentence is
superseded deliberately rather than quietly: it was the boundary that made the entry gate coherent,
so moving it is a decision, not an edit. The reason is timing, not appetite. Enablement is beginning,
and a baseline has to be captured while pre-enablement history is still cheaply reachable — the
windowed sources can reach back a long way, but a baseline window that recedes past a repository's
practical history is gone, and no later run can recover it. Waiting until teams are enabled to decide
we want a before is how a before stops existing.

What is in scope is MEASUREMENT ONLY: the same windowed signals read as a time series against a
pre-enablement baseline, as absolute values plus deltas. What stays out of scope is unchanged —
cost/benefit modelling, and AI ATTRIBUTION in particular. Enablement is an event the series is
anchored to, never a cause the tool has demonstrated; see "Trend measurement" below for the
vocabulary and grading rulings that hold that line.

## Readiness assessment (2026-08-13) — REVERSES AN EARLIER BOUNDARY, AND IS NOW BUILT

The tool supports a go / no-go judgement: "is this team exhibiting good governance and ready for
agentic tooling?". The user's worked example — a merge gate that does not enforce pull-request review
is a hard no, RED, whatever the team's day-to-day behaviour — means merge-gate configuration can
VETO, rather than merely sitting alongside behavioural evidence.

This reverses "no scoring, no RAG labels" as recorded under scope boundaries, which is why it is
called out here rather than quietly edited. The rest of that boundary is untouched: still no
dashboards, and no personal rankings — the actor section added on 2026-08-28 lists a person's
repositories and their labels in alphabetical order of login, precisely so that it is not one.

The judgement layer does NOT belong in `behaviour_metrics/` — metrics stay neutral aggregates with no
target and no verdict, and that separation is what makes them reusable under a changed policy. It
lives in `assessment.py` (`ReadinessPolicy`), consumes metrics, gate evidence and cached facts, and is
reported as the `assessment` block on each repository in the default `evidence` report.

Decisions taken by the user on 2026-08-13, now settled:

1. **A label AND its reasons.** `label` plus THREE sections that account for every condition checked:
   `blocking` (what held the label below green, each carrying the ceiling it imposed), `caution` (not
   disqualifying, but a reader should weigh it) and `clear` (checked and imposing no ceiling, with its
   numbers — usually satisfied, and also the three merge-gate rules that bear on the label in neither
   state, which sit there whether configured or absent).
   Green is reachable only with an empty `blocking` list, so a colour is never a bare assertion, and a
   green is as auditable as a red. Every detail quantifies where a number exists. Extended on
   2026-08-13 from blocking-only, on the user's instruction: a check that is never reported cannot be
   argued with.
2. **Two veto conditions, both governance, neither configurable** — they are the definition of the
   question, not a tuning knob. A gate requiring no approving review, and a default branch with no
   protection. Explicitly REJECTED as vetoes: `applies_to_administrators: false` and absent required
   status checks. The second is already answered better by `checks-passing-at-merge`, which measures
   whether CI actually held rather than whether it was nominally required. Both are nevertheless
   REPORTED, as cautions — the user was borderline on the status-check veto, and a condition that
   close to disqualifying must not be silent. `dismiss_stale_reviews_on_push` joins them for the same
   reason: an approval surviving a later push means reviewed and merged code differ, which is sharper
   under agentic tooling than under human authorship.
   One dormant trap: `evidence` dumps JSON with `exclude_none=True`, so an `applies_to_administrators`
   GitHub did not disclose is OMITTED rather than emitted as `null`, and "unknown" looks identical to
   "field never existed". Harmless while the condition is a caution; it becomes wrong the moment any
   undisclosable field is scored as a veto, because "unknown" must not be read as either answer. Fix it
   then — drop `exclude_none` for the block or give the assessment an explicit unknown — not before.
3. **An unreadable gate is `cannot_assess`, a distinct outcome** — not a grade between amber and red.
   4 in 5 hmcts repositories refuse branch-protection detail to a non-administrator, so red would
   blame a permission gap on the team and green would pass a repository that may have no gate at all.
   A RED condition still OUTRANKS `cannot_assess`: a disqualifier found in observed behaviour stands
   without seeing the gate, and an unreadable gate must never hide one.
4. **Behavioural thresholds are configuration** (`assessment` block) and NEVER hardcoded, because
   every threshold is a policy judgement rather than a measurement and must be arguable without a
   release. Defaults ship, and are stated openly as policy choices rather than presented as facts.
   AMENDED 2026-08-13: this was once worded as "someone must own each threshold", which was read as
   a gate requiring a named sponsor before a metric could be graded. It never meant that — applied
   that way it would have blocked the 90/70 defaults already shipping for review coverage, approval
   coverage and checks. The rule is about WHERE a number lives, not who signs it off.
   FLOW METRICS ARE GRADED TOO (2026-08-13, user ruling): `pull-request-size`, `merge-cycle-time` and
   `time-to-first-review` live with the repository "in the same way merge gate and CI/CD quality do",
   so they are graded conditions with configurable boundaries like their siblings. They are
   REPOSITORY-LEVEL and are never attributed to an actor — a slow first review is a property of how a
   team works, not one person's failing. Distributions need a `maximum` boundary in a
   `DistributionThreshold` sibling of `ReadinessThresholds`, since a percentile is not a rate.
   CAPPED AT AMBER (2026-08-16, user ruling: "these were never supposed to be red"): a flow signal
   grades to amber however far above its maximum it sits. A cost is not evidence that anything
   ungoverned merged, and grading it to red made a slow well-reviewed repository indistinguishable
   from an ungoverned one — on this estate the sub-hour merge-cycle-time medians all belonged to
   repositories with almost no review coverage. The `amber_maximum` half of the pair is gone with it:
   it was set to double the green boundary, arithmetic standing in for a policy nobody had made.
5. **Per repository.** Team-level aggregation was not asked for and is not built; repositories are
   what the gate, the cohort and every existing report are keyed on.
   REAFFIRMED AND CLOSED on 2026-08-13, when team-level readiness was offered as the next iteration
   and the user declined it: the repository stays the unit of judgement, and a team is read by looking
   at its repositories case by case with the team itself. `TeamConfiguration` stays in configuration
   for accounting — it is what maps a repository to the people who own it — but no team label is
   derived from it and none is reported. See "No team roll-up" under Scope boundaries.

Two consequences worth keeping in mind. `MergeGateEvidence.rules_observed` had to be added, because
`protected: true` with empty rule arrays is otherwise indistinguishable from a gate nobody was
allowed to see; it defaults to FALSE so that state stored before the field cannot be read as a gate
with no rules. And an insufficient cohort (`minimum_pull_requests`, default 10) suppresses the graded
conditions entirely rather than sitting beside them — a rate over four merges is arithmetic, and
reporting it as a shortfall would dress a thin window up as a finding.

**`approval-coverage` is graded too (2026-08-13, later the same day).** The user chose it over the other
open candidates and chose its shape: a graded rate with its OWN configurable thresholds, exactly like
independent review coverage, rather than a caution or a contradiction check against the gate. The gap
it closes is the team that reviews without ever approving — review coverage passes while nobody is on
record as having accepted the change, which is precisely where responsibility for code a human did not
write is supposed to be taken.

The known cost was accepted deliberately: approval implies review, so the two rates move together and
a repository merging unreviewed blocks on both. That is double reporting, not double penalty — the
label is the worst ceiling imposed, never a sum — and each condition reports its own numerator and
denominator so a reader can see whether the two diverge. On cath-service they do not: both read
81.2% (78 of 96). Grade them separately anyway, because a single rate cannot show the divergence when
it happens.

**`review-depth` is graded but CAUTION-ONLY, never blocking.** `approval-coverage` decides the label,
so it matters whether an approval means anything — a repository can show 100% approval coverage where
every approval is a click with no comment. Below a configurable boundary (`review-depth-minimum-percentage`
in the `assessment` block, default 50%) it lands in `caution`, otherwise `clear`. Not blocking on first
cut: no defensible label-deciding threshold exists yet, and a caution must never impose a ceiling.
Measured via `ReviewFact.comment_count`, collected with `comments(first: 0) { totalCount }` — count
only, deliberately not a connection with real nodes, which would have re-inflated the `search`
query that already had to shrink to `first: 25` to stop timing out (see "Collection cost and
performance"). **A review's own summary counts too (corrected 2026-08-15).** GitHub records the
review body separately from the inline comments that connection counts, so counting only the
connection scored an approval reading "this changes the retry semantics, I checked X" as a wordless
click — the exact opposite of what the metric is for. `body` is selected on the review node, which
adds no round trip, and reduced to the boolean `ReviewFact.has_body` rather than cached: no metric
reads a review's words. Presence, not length — `description-quality` grades how much was written
about a change, and no boundary for how much a review must say has an owner.

**`description-quality` and `traceability-reference` are NOT in the readiness label, DECIDED.**
Description quality is a documentation habit, not evidence that a change was governed, and no
threshold has an owner. Neutral metrics only — reported like every other behaviour metric, never
read by `ReadinessPolicy`. Both search title AND body: a ticket reference in the title is
traceability, and insisting on the body would fail teams whose convention is the title.

**ALL SIX MERGE-GATE RULE TYPES ARE ATTRIBUTED, AND EACH ONE'S BEARING IS NOW FIXED (2026-08-15,
user rulings).** The move from one repository to fourteen made this urgent: `opal-common-lib` and
`opal-logging-service` run six ruleset rules each, and the collector modelled four of them while
`ReadinessPolicy` read only two. Two rules were collected and silently ignored, and two were not
collected at all — the same defect wearing two different hats. Every rule type now has a stated
bearing, and no rule may be added in future without one.

| rule | evidence | bearing on the label |
| --- | --- | --- |
| `pull_request` | `pull_requests[]` | VETO if no approving review is required |
| `required_status_checks` | `status_checks[]` | CAUTION when absent (rejected as a veto — see decision 2) |
| `non_fast_forward` | `blocks_force_pushes` | CAUTION when absent |
| `deletion` | `restricts_deletions` | `clear` only, never a ceiling |
| `required_linear_history` | its own field | `clear` only, never a ceiling |
| `branch_name_pattern` | its own field | `clear` only, never a ceiling |

**Why force-push protection earns a caution and the other three do not.** Without `non_fast_forward`
an approved history can be rewritten after review, so reviewed code and merged code differ. That is
the same reasoning that already made `dismiss_stale_reviews_on_push` a caution, and it is sharper
under agentic tooling than under human authorship. It is a CAUTION and not a veto because the veto
set is fixed at two conditions (decision 2) and nothing here reopens it.

**`required_linear_history` is neutral, ruled by the user before the table above was agreed and
unchanged by it:** not important for gating enablement at this time, useful to collect, and some
teams or managers may care about it. "For the moment" is the user's wording and is load-bearing —
this is a deferral open to revision, not a permanent exclusion like description quality. Branch
naming sits with it for a plainer reason: it is a naming convention and traces to none of the four
decision questions.

**This extends what `clear` means, deliberately.** It was "checked and satisfied"; it now also
carries rules that were checked and imposed no ceiling either way, including ones observed to be
ABSENT. The alternative was leaving three rules out of the assessment altogether, and decision 1 is
explicit that a check which is never reported cannot be argued with. A `clear` entry has never
implied approval — only that the condition did not hold the label back — so nothing about the label
changes. What changes is that a reader can see all six rules were examined.

The general rule this settles, and the one to apply to the next rule type GitHub exposes: A RULE
TYPE BECOMING VISIBLE IS NOT A REASON TO GRADE IT. Collect it, report it, place it in `clear`, and
leave the label alone until someone rules otherwise — the same separation that keeps metrics neutral
aggregates while `assessment.py` does the judging.

Flow signals (`pull-request-size`, `merge-cycle-time`, `time-to-first-review`) were originally OUT of
the label. That was REVERSED by the user's ruling of 2026-08-13 recorded under decision 4 above: they
are graded conditions with configurable distribution boundaries, repository-level, never attributed to
an actor. SHIPPED: each grades a single fixed percentile (the 75th for size, the median for the two
waiting times — a policy choice living in `assessment.py`, not in configuration) against its own
`DistributionThreshold` (`config.py`). The defaults match the reference tool. Each caps at amber; see
decision 4 above.

Both waiting times are anchored on `analysis.review_started_at` — the earliest ready-for-review event,
falling back to creation — so time a change spent in draft is not counted as time it waited. Anchoring
on `createdAt` made them measure how long a branch existed, which is a different question.

The question "if they are not ready, what exactly are they doing wrong?" is explicitly NOT urgent.
Mechanism does not matter: an inactive rule and a permitted admin bypass are equally bad discipline,
and the tool should not spend effort distinguishing them.

## Decision questions (agreed 2026-08-11)

Every metric and rule must trace to one of these four. A proposed signal that traces to none is out
of scope, however interesting.

1. **Is independent review consistently practised before merge?**
   Covered: `independent-review-coverage`, `approval-coverage`, `unreviewed-merge`, `review-depth`.
2. **Does work flow in small, fast increments?**
   Covered: `pull-request-size`, `merge-cycle-time`, `time-to-first-review`.
3. **Is CI trustworthy at the merge point?**
   Covered: `checks-passing-at-merge`. Checks are judged by their state at `merged_at`, never their
   state now — see the mutable edge below. Required-ness is NOT claimed: which checks a gate
   required lives in merge-gate configuration that needs `Administration: read`.
4. **Are merge gates enforced, or nominal?**
   Evidence is collected by `collect`, stored by `record_repository_state()`, read back by
   `load_repository_state()`, and REPORTED as the `merge_gate` block on each repository in the
   default `evidence` report — with the `fetched_at` instant, because a gate is current state sitting
   beside behaviour that covers a historical window. A gate that was never collected, or was not
   observable when it was, reports the reason rather than being omitted. It is also JUDGED: two gate
   conditions veto readiness outright — see "Readiness assessment" above.
   `applies_to_administrators` is collected because a gate admins can bypass is the clearest case of
   nominal enforcement, and is None when unobservable; it is deliberately not a veto.

Explicitly considered and left out as a GRADED question: "is review capacity a constraint?".
`time-to-first-review` already answers most of it, and no threshold on open-pull-request counts has
an owner. Open (unmerged) pull-request state — a different cohort with a genuinely mutable
lifetime — IS now collected and reported (the `open_pull_requests` block: opened, closed without
merge, currently open, stale open; never cached, fetched fresh, unavailable rather than zero
`--offline`), but display-only. It traces to no decision question and carries no threshold; revisit
grading it only if the flow question proves too coarse.

`description-quality` and `traceability-reference` are an accepted exception to "every metric must
trace to one of these four": they are neutral documentation-habit signals with no threshold owner,
deliberately excluded from the readiness label rather than forced onto a question they do not
answer — see "Readiness assessment" decisions above for the label-exclusion ruling.

## Command model

Three commands with disjoint responsibilities.

- **`collect` fills the cache. It does not report evidence.**
  Takes the same window flags as `evidence` (`--from`/`--to`/`--days`/`--maximum-days`), defaulting
  to `lookback.operational_days`, and fetches windowed sources for that window. Emits a *collection
  report* — resolved window, intervals fetched versus reused, fact counts, failures — never metric
  values.
- **`evidence` reports from the cache over a reporting window.**
  As a convenience it collects whatever the window needs and the cache lacks, then reports.
  `--offline` never collects: if the cache does not fully cover the window it refuses and exits
  non-zero. A silently short window is more dangerous than no answer.
- **`map-sonar` builds the durable `sonar → github` map. It collects no evidence and reports none.**
  The third command that writes to the observations file, and the only one run BY HAND, periodically:
  it is paced against whatever commit-search allowance GitHub's `x-ratelimit-*` headers report for
  the token in use, so a full rebuild of the organisation's 289 projects is tens of minutes. Takes no
  window flags — the map is not about a period. See "SonarCloud quality evidence".

(`doctor`, `prune` and `trend` take no part in the collect/report split: the first two touch no
evidence, and the third reports through `evidence`'s own code.)

**The JSON is the contract; `--format report` is a rendering of it (2026-08-13).** `evidence --format report` prints
the same evidence as plain ASCII and is PRESENTATION ONLY: `render.py` consumes report models and computes nothing, so
the two renderings cannot disagree. The report shows the metric aggregates and the cohort's review-state counts beside
the practice report, which the default JSON does not carry — both are built at the CLI boundary from existing models
(`BehaviourEvidenceReport`, and `PullRequestFact.reviews` via `analysis.review_state_counts`) and both are already
emitted by a `--metric` drill-down, so the report still claims nothing the JSON cannot corroborate. A number that exists
in neither belongs in a metric first.

`collect` computing metrics is what allowed the two commands to disagree: it never applied
`cohort.excluded_authors`, so on cath-service it reported independent-review-coverage as 80/188
while `evidence` reported the same signal over a 96-PR cohort with 86 renovate PRs excluded. Only
`evidence` computes metrics, so this cannot recur.

**`--config` is repeatable, and the files are read as ONE document (decided 2026-08-17).** Every command takes
`--config` as many times as it is given; the files are concatenated in order and parsed once, before any validation.
This is a storage question, not a schema one: the policy every report shares — assessment thresholds, practices,
lookbacks — is written once and paired with whichever team file a run is about, instead of a file per team set each
restating the whole policy, where copies drift and two reports become incomparable without anyone noticing. **Nothing
below the loader knows how many files there were**, and deliberately so: there is no notion of a valid partial file, no
per-file check, and no merge algorithm of ours to reason about. A key given twice is resolved by YAML as it always was,
the last occurrence winning, so a block is replaced and not merged into. A relative `database` resolves against the
first file, the one the configuration starts in.

**`teams:` is required by the commands that report the cohort, not by the schema (decided 2026-08-27).** The schema
made it mandatory, which made a policy file unloadable on its own — so `map-sonar`, which resolves every project a
SonarCloud organisation lists, and `prune`, which deletes stale cache rows, both refused to start until a team file was
layered in to satisfy a validator neither command's work goes near. That refusal reported invalid configuration when
nothing about the run was invalid. The requirement now sits where the need is: `COHORT_COMMANDS` in cli.py — `collect`,
`doctor`, `evidence`, `trend` — refuses an empty cohort and names the command that needed one, and the schema defaults
`teams` to empty. The two cross-checks that compare a configured name against the cohort (`enablement` and
`sonar_projects`) are skipped where there is no cohort, because one side of the comparison is absent and every name
would be reported as unconfigured; nothing is lost, since either key only ever affects a run that reports the cohort,
and every such run loads a team file. `sonar_projects` keys are still checked for blankness, which needs no cohort.

**Invalid configuration is reported by location, not by document (decided 2026-08-27).** pydantic's default rendering
prints the whole input document beside the first offending key plus a link to its error index, so `teams: Field
required` arrived buried in the configuration that lacked it. `describe_validation_error` renders `location: problem`
per rejected key — the rendering already used for rejected GitHub responses in inventory.py — and names the files the
document was read from, because with a repeatable `--config` the fix is usually a file that was not given rather than a
key that is wrong. A YAML *syntax* error still reports its position in the concatenated document, which does not
correspond to a line in any single file when several were given.

**Failure isolation is per repository and per source (decided 2026-08-15, at fourteen repositories).**
One repository refusing must not end a run over the other thirteen. Both commands iterate repository
by repository and turn a refusal into a recorded reason rather than an exception: repositories
already collected keep their cached history, repositories after it are still collected, and the
refused one reports why rather than an empty window. The same holds *within* a repository, because
pull-request and direct-commit history are cached under separate coverage — a rate limit arriving
between them keeps the half already paid for. Coverage is recorded only for an interval GitHub
actually returned, so a partial run never leaves the cache claiming a window it did not read. This
was largely already true when the population widened; what was missing was the exit status.

**Exit status is three-valued, not two (decided 2026-08-15).** `0` every configured repository was
observed; `1` the run produced nothing usable; `3` partial — the run produced evidence and recorded
at least one failure. A failure need not be a whole repository: a repository that reports with one
source withheld records one too, so a population where every repository reported but classic
branch-protection detail was refused — the common hmcts shape — exits `3`. EACH REFUSED SECURITY-ALERT
FAMILY IS SUCH A FAILURE (extended 2026-08-15): the three alert permissions are separate from
branch-protection access and do not travel with a ruleset migration, so a run whose every repository
answered while every alert family was withheld must not read as complete. The reason still appears
on the block as well — the failure drives the exit status, the block is what a reader sees.
WITHHELD, NOT MERELY OFF (corrected 2026-08-15): a `404` from an alert endpoint is an observation,
not a refusal, exactly as a `404` from the branch-protection endpoint is. The repository's metadata
was read with the same token moments earlier, so the family is not enabled rather than missing; it
is reported on the block as "not enabled for this repository" and records no failure. Treating it as
one would have exited `3` for every repository that simply does not use the feature — most of them —
which empties the status of the signal this ruling built. A `403` stays a failure: GitHub returns it
both for a token without the scope and for Advanced Security being off, and only the response text
tells them apart, which is too fragile a thing to grade a run on. A partial
run exiting `0` would let every downstream denominator read as covering the whole population when
repositories are missing from it, which at fourteen is no longer a hypothetical. `3` rather than `2`
because `argparse` already exits `2` for a usage error, and "you invoked me wrongly" is not "part of
the org would not answer". Both a partial and a wholly failed run still write their report: naming
the repositories that refused is the most useful thing a failed run produces. `evidence --offline`
is the deliberate exception and keeps refusing outright, per the ruling above.

## Source taxonomy (decided 2026-08-11)

Two kinds of source, with different collection semantics. Conflating them is what made `collect`
confusing.

- **Windowed sources** — pull-request, review, and direct-commit facts. Historical, accumulate,
  cached by interval, each source under its own coverage and its own query signature so that
  widening one source's queries never discards another's settled history.
  Behaviour is a *pattern*, so these may legitimately reach back the life of a project (six months
  or more) to show whether a team's discipline is consistent.
- **Current-state sources** — repository metadata, merge gates, branch protection, rulesets,
  CODEOWNERS presence, default-branch maintenance instants (see "Minimum-standards evidence"
  below) and the SonarCloud measures of the project a repository maps to (see "SonarCloud quality
  evidence" below). GitHub
  cannot tell you what branch protection was in May, and knowing would not help: the question is
  whether the gate protects merges *now*. Always fetched fresh, stored as a single latest row per
  repository with a `fetched_at` the report surfaces.
  SonarCloud is the first current-state source that is not GitHub, and it obeys the same rules: its
  quality gate, coverage, duplication, issue counts and ratings describe the project's HEAD analysis
  now, no history of them is retrievable, and the block carries the `fetched_at` of the collection
  that stored it so `--offline` shows the last answer rather than refusing.

KNOWN LIMITATION — A RENAMED REPOSITORY LOSES ITS STORED STATE. `collect` follows GitHub's rename
redirect and stores the row under the name GitHub answered with, while `evidence` reads it back
under the name the configuration file gives. So a repository renamed since `hmcts.yml` was written
reports "no repository state has been collected; run metrics collect" for its merge gate and its
security block for ever — running `collect` again writes the same divergent key. The fix is to
update the configured name; that is deliberate, because the alternative is a second key whose only
purpose is to keep a stale configuration working. The cost table keys on GitHub's name for the
opposite reason and says so at `inventory.collect_inventory`: both phases of a run must agree, or
one repository's cost splits across two rows in the same report.

So `collect --from X --to Y` means: fetch windowed sources for `[X, Y)`, and refresh current-state
sources as of now. The window does not apply to current state and never will.

**Open pull-request state is a third kind: never cached, DECIDED.** Opened/closed-without-merge/
currently-open/stale-open counts have no settled state to cache — unlike windowed history, they are
current by definition, and unlike current-state sources they are cheap enough (one bundled GraphQL
call with four aliased `search { issueCount }` selections) that a stale "stale for 14 days" figure
would be worse than the extra call. Fetched fresh on every `evidence` run; `--offline` cannot report
it and says so with a reason rather than a remembered or zeroed number. `lookback.stale_open_days`
(default 14) measures from LAST UPDATE, not from opening. Does not widen `lookback.mutable_hours` —
that would weaken settled merged-history caching for an unrelated reason.

**Security alerts SPAN BOTH KINDS, DECIDED 2026-08-14** — the one decision point the security-posture
plan reserved,
taken by the user once `scripts/probe-alert-access.sh` proved the endpoints reachable (see "Access").
This is the first source to sit in two kinds at once, so the split is stated here rather than
inferred:

- OPEN ALERT COUNTS BY SEVERITY ARE CURRENT STATE. GitHub cannot say what was open in May and
  knowing would not help — the question is what is unfixed *now*. Latest row per repository, a
  `fetched_at` the report surfaces, refreshed on every run, never windowed.
- RESOLUTION TIME IS WINDOWED. How long an alert stayed open is history and only exists for alerts
  that were resolved, so it is computed over alerts resolved inside `[starts_at, ends_at)` and cached
  by interval under its own coverage and its own signature, exactly like pull-request facts.

Why both halves and not just the first: an open-alert count alone cannot separate a team holding 40
alerts it fixes within two days from a team holding 40 it has ignored since March. The count says
what is outstanding; the resolution time says whether the team resolves anything.

RESOLUTION TIME IS REPORTED AS MEDIAN / p75 / p90, NOT AS A MEAN (decided 2026-08-14), through the
same percentile path as `merge-cycle-time` and `time-to-first-review`. The security-posture plan's
own wording said "mean time to resolution" and the user's reference tool shows a mean; both were overridden
deliberately. A mean is dragged around by outliers, and one alert forgotten for 300 days would swamp
thirty fixed within a day — the exact failure the flow metrics already avoid. Do not "restore" the
mean to match the plan text.

ONLY THE CURRENT-STATE HALF IS BUILT. THE WINDOWED HALF STAYS DEFERRED (2026-08-14, RE-EXAMINED AND
UNCHANGED 2026-08-15). Open alert counts by severity are collected, stored and reported; resolution
time is designed above and is deliberately absent from the code. The deferral was never about the
feature's value — it was about testability: `cath-service`, the only repository measured, holds 0
open alerts across all three families and 22 code-scanning alerts ever, all resolved, so every
sample a windowed resolution-time cache produced would have been "not observed", and a source whose
only worked example cannot exercise it is a source measured by its own tests.

Widening to 14 repositories on 2026-08-15 was the first thing that could have changed that input, and
it has NOT been shown to. `scripts/measure-alert-volume.sh` across the 13 new repositories is the
measurement that would settle it, and it has not been run — it needs a real token, so it is user-run
like the other three scripts (see "Access"). Until its table exists the deferral stands, because the
cost discipline below cuts the same way for volume as it does for calls: AN ESTIMATE OF A NEW
SOURCE'S COST IS NOT EVIDENCE, and neither is an assumption that 13 unmeasured repositories hold
resolved alerts. Note also that alert volume is gated on alert ACCESS, which is itself unmeasured
across the 13 — a repository that answers 403 contributes no volume, so the two open tables are
ordered: run `probe-alert-access.sh` first, `measure-alert-volume.sh` second.

What the table decides, so nobody has to re-derive it: if no repository shows meaningful
resolved-alert volume, the feature stays deferred permanently-until-revisited and this paragraph is
the record of why; if one does, the windowed half is built against THAT repository as its worked
example, as median / p75 / p90 per the ruling above, with its own coverage and query signature under
the source taxonomy. Either way the secret-scanning ruling below is untouched.

SECRET-SCANNING ALERTS HAVE NO SEVERITY AND MUST NOT BE GIVEN ONE (decided 2026-08-14). Dependabot
and code-scanning alerts carry a severity; GitHub's secret-scanning API does not — it carries
`secret_type` and `resolution`. That family therefore reports a bare open count and its resolution
times, with no severity axis. Treating every secret as critical was considered and REJECTED: it
invents a grading GitHub does not assert, and would grade a leaked test fixture the same as a live
production key. This is the same rule as "unavailable data never becomes zero", applied to a
dimension rather than a number.

## Minimum-standards evidence: CODEOWNERS and maintenance status (decided 2026-08-24)

Requested 2026-08-21 by the minimum-standards audience (Martijn Schot; "Requirements for Scanning
tool.pdf", repository root), against the gov.uk minimum standard for publicly accessible systems.
Three per-repository checks: a CODEOWNERS file (the standard's "named owner"), time since the last
default-branch commit, and time since the last commit not authored by dependency automation — the
latter two against 6/12/24-month windows.

- **CURRENT-STATE SOURCES under the source taxonomy above.** One bundled GraphQL query per
  repository — six aliased CODEOWNERS blob selections plus the first default-branch history page —
  executed in `collect_repository_state` after the metadata read, stored latest-only on the
  `repository_state` row, reported with `fetched_at`. It is a NEW, SEPARATE query: no windowed
  query changed, so no coverage signature moved and no settled history was invalidated. Rows
  stored before this source existed read back as None and report "not collected when repository
  state was stored", never as checked-and-absent.
- **REPORT-ONLY AND UNGRADED.** `ReadinessPolicy` reads none of it — the same rule as "a rule type
  becoming visible is not a reason to grade it". Grading any of this is a fresh user ruling that
  belongs here first. The blocks join the `evidence` report rather than a separate report because
  the same audience reads both, and a second report would fragment the answer.
- **`pushed_at`/`updated_at` are deliberately NOT the maintenance signal.** They move whenever a
  bot pushes a pull-request branch that never merges; that insufficiency is what the request names.
  `last_commit_at` is the newest commit on the default branch, None when the branch has no commits.
- **THE HUMAN PREDICATE IS SHARED, NOT RESPELLED.** `analysis.is_human_commit_author` reuses
  `is_human_account` and the `excluded_authors` normalisation: linked account first (type `Bot` or
  a `[bot]` login suffix fails, as does a login in `cohort.excluded_authors`); where GitHub links
  no account, the git author NAME is tested the same two ways and otherwise counts as human; a
  commit with neither a linked account nor an author name is nobody, not a person. Deliberately WIDER than the cohort exclusion: ALL bot accounts fail, not just Renovate and
  Dependabot — an agent-authored commit is work the cohort keeps, but it is not a person
  maintaining the repository, the same distinction `active-contributors` draws.
- **THE COST BOUND: cost must not grow with the failure being measured.** A bot-dominated
  repository is exactly where the human answer is interesting and exactly where a naive "page
  until a human appears" is unbounded — the flux-config repositories hold roughly 20k direct
  commits a quarter. Pagination therefore stops when a human commit is found, when the 24-month
  cutoff (from the fetch instant) is passed, or at a page cap: module constants in `inventory.py`,
  100 nodes per page and 10 pages. The cap is a COST LIMIT, not a policy threshold, which is why
  it is a constant and not configuration.
- **THE HUMAN ANSWER IS THREE-VALUED, and the bound is why.** When no human commit was found,
  `searched_back_to` records the oldest instant examined — the cutoff itself when the search
  reached it or exhausted the history (every commit after the cutoff was examined, so a window
  decides False), the oldest commit read when the page cap stopped it first (the window past that
  instant is UNKNOWN, with the reason). Unavailable data never becomes zero, and here it never
  becomes False either. Window rows (6/12/24 months as 183/365/730 days, constants in
  `evidence.py`) are derived at REPORT ASSEMBLY from the stored instants against `fetched_at` —
  not at collection, not in rendering — so the same stored row always yields the same report,
  offline.
- **CODEOWNERS: six locations, and GitHub's recognition is part of the evidence.** The request
  names `CODEOWNERS` or `CODEOWNERS.md` in the root, `.github/` or `docs/`; GitHub reads only the
  non-`.md` three. Each found file carries its path, byte size (an empty file is found-but-empty,
  never silently passing) and `recognised_by_github`, because a `CODEOWNERS.md` satisfies the
  email's letter and does nothing on GitHub. A missing file is an OBSERVATION — checked
  everywhere, found nowhere — never a failure.
- **Failure semantics match the alert families.** The bundled query failing records one
  `RepositoryInventoryIssue` per evidence kind (`codeowners`, `maintenance`), both blocks carry
  the shared reason, and the run exits `3`.

**THE EMAIL'S THREE OTHER MINIMUM STANDARDS GET NO NEW CODE, and this is the mapping** — recorded
so the answer to the request is written down rather than re-derived. Secrets: the secret-scanning
open counts already on the security block. Automated hygiene: the dependabot open counts, the
force-push and stale-review cautions in the readiness assessment, and `checks-passing-at-merge`.
Secure-by-design: judged NOT AUTOMATABLE from repository evidence — no observable repository fact
answers it, and inventing a proxy would be the drift the defect-signal ruling under "Trend
measurement" below is pinned against.

## SonarCloud quality evidence (decided 2026-08-27)

Each repository reports the quality state of the SonarCloud project it maps to — gate level with
every condition behind it, coverage, duplication, lines of code, violations, the three
software-quality issue counts, security hotspots and the four ratings — as a current-state block
beside the merge gate. SonarCloud is read anonymously over `requests`; `SONAR_TOKEN` then
`SONARCLOUD_TOKEN` widen the listing to private projects when set, and a bad token is worse than no
token (`401` where anonymous gets `200`), so the header is sent only when one is present.

**THE HARD PROBLEM IS NOT THE MEASURES, IT IS THE MAPPING.** Nothing on either side records which
repository a SonarCloud project analyses. The resolution ladder, strongest rung first, is
`sonar.github_to_sonar`:

1. the CONFIGURED override (`sonar_projects` in configuration) — a human's instruction, which beats
   every observation;
2. the repository's own `sonar-project.properties` declaration, CONFIRMED either by the stored map or
   by asking GitHub whether this repository holds the commit the declared project was last analysed
   against — and DROPPED where either refutes it;
3. the stored `sonar → github` map, built by `map-sonar` from analysis commit SHAs;
4. otherwise unresolved, carrying every reason collected on the way down.

The rung that answered travels on the mapping as `SonarResolution` and is printed, because a wrong
mapping is diagnosable only if the report says which rung produced it.

**THE NAME-RULE LADDER WAS MEASURED AND REJECTED, and stays in the audit script as a measurement
rather than a mechanism.** `scripts/audit_sonar_project_mapping.py` ran all three proposed options
over 3,372 repositories. What it found:

- 240 repositories declare a key in a root properties file; **123 of those name no project SonarCloud
  lists**, and **69 sit in 26 collision groups** — `rpe-expressjs-template` is declared by 14
  repositories scaffolded from that template that never changed the key. Only 79 of 240 declarations
  are both visible and unique. A declaration is therefore a HYPOTHESIS TO CONFIRM, never an answer,
  which is why rung 2 above always tests it and why a declaration the map attributes to a different
  repository is treated as refuted rather than as a tie.
- The name rules were wrong for **6 of the 70 repositories where both options answered**, and that
  8.6% is measured on a BIASED SAMPLE: repositories carrying a properties file skew to JavaScript,
  while Gradle and Maven repositories declare the key in a build file the audit never read. The true
  error rate is unknown and is not smaller for having been measured on the friendlier half.
- Three of those six conflicts are duplicate projects (`rpx-xui-webapp` vs `rpx-xui-webapp_2`), where
  the ladder prefers the exact name and **the exact name is the ABANDONED project**: each `_2` was
  analysed on 2026-08-26 while its bare-name twin went quiet in July. SonarCloud has no rename, so the
  counter suffix marks the live project and a convention that reads names cannot know that.

So a name rule is not a rung, and adding one back is a fresh ruling that belongs here first.

**THE MAP LIVES IN THE DURABLE OBSERVATIONS FILE, NOT THE CACHE.** Rebuilding it costs one paced
GitHub commit search per project across 289 projects, against a documented limit of 10 requests a
minute unauthenticated and 30 authenticated — ten to thirty minutes of calls. THE DOCUMENTED NUMBER
IS NOT ALWAYS THE ISSUED ONE: a run on 2026-08-27 was given ten a minute while holding a token, so
the pacing is taken from the `x-ratelimit-*` headers of the previous response rather than from the
documented figure, and the documented one survives only as the interval used before the first
response has been seen. The cache is
DISPOSABLE by ruling ("Cache design"), and `rm metrics.sqlite3` must not silently shrink the next
evidence run's coverage to whichever repositories happen to declare a properties file. This stretches
the observations file past its original name — it now holds an alert history that cannot be refetched
at any price and a map that can be refetched but only slowly. A THIRD FILE for
expensive-but-recoverable state was considered and rejected: it would mean a third lifecycle rule for
a reader to learn and a third path for a backup to miss, where "deleting the cache is always safe" is
the distinction that actually matters and holds either way. The table is keyed on
`(sonar_organization, project_key)`, not on the GitHub organisation, because the two names may differ
and a map built against one SonarCloud organisation says nothing about another's keys.

**AN UNRESOLVABLE PROJECT IS STORED WITH ITS REASON.** A never-analysed project (14 of 289) answers
the same way every run, so remembering the answer is what stops the next run paying the scarcest
quota in the system to learn it again. Six outcomes are kept apart, and five of them are OBSERVATIONS
about the project: resolved, no analysis, no revision on the analysis, the revision matched no commit
in the organisation, the revision matched a repository outside it. Only a FAILED CALL is a failure of
the run, and only a failed call may make one partial.

**THE REVERSE LOOKUP IS MANY-TO-ONE, RESOLVED BY ANALYSIS RECENCY.** Two projects legitimately map to
one repository — the duplicate pairs above — so `repository_project` returns the candidate with the
newest `analysis_at`, which picks the live project in every measured pair, together with the COUNT of
candidates so a human can see a choice was made. `map-sonar` warns with both keys for every repository
two projects claim. Upserts are newest-analysis-wins for the same reason: a project that has moved
between repositories follows the newer analysis, and a re-run holding stale data cannot move it back.

**REPORT-ONLY AND UNGRADED.** `ReadinessPolicy` reads none of it — the same standing rule that keeps
security, CODEOWNERS and maintenance out of the label: a signal becoming visible is not a reason to
grade it. A failing quality gate imposes no ceiling and carries no colour. The conditions are
nevertheless printed in full, as the merge gate prints its rules, because "the gate failed" is an
assertion and "coverage 62.1 against a threshold of 80" is the evidence for it. Grading any of this
is a fresh user ruling and belongs here first.

**WITHHELD IS NOT MERELY OFF, again.** A repository no project maps to is an OBSERVATION, records no
failure and exits `0` — most of the organisation is in that state, and treating it as a failure would
empty the exit status of meaning exactly as it would have for the alert families. A SonarCloud or
GitHub call that FAILED records one `RepositoryInventoryIssue` with `EvidenceKind.SONAR` and exits
`3`. The SonarCloud calls are counted by the existing `CostMeter`, which takes the Sonar client beside
the GitHub one, so a repository's collection stays one measurable unit.

A `404` ON A DECLARED KEY IS A REFUTATION, NOT A FAILED CALL. SonarCloud answers `404` for a project
it does not have, and 123 of the 240 declarations name exactly that — stale keys left in properties
files. Carrying that out as a reason would record half the organisation's properties files as failed
calls and exit `3` for them, which is the same mistake the alert families' `404` ruling avoids. It is
read as "the hypothesis is false", the ladder falls through to the map, and only a read that was
REFUSED — `401`, `403`, `429`, or a body this build cannot parse — keeps its reason. THE SAME RULING
APPLIES TO A PROJECT ALREADY MAPPED: a project can be deleted, renamed, or made private after the map
was built, and `map-sonar` walks only what SonarCloud still lists, so it never clears the row. A
failure there would exit `3` on every run for a condition no operator action fixes, so it is stored
as an observation on the resolution — naming the project and the method that resolved it, because
that is what says whether to re-run `map-sonar` or to fix a `sonar_projects` override.

GITHUB'S `422` BELONGS TO ONE ENDPOINT, NOT TO THE CLIENT. `/commits/{sha}` answers `422` for a SHA
the repository does not hold, which is how `confirms_candidate` refutes a candidate; that meaning is
read from `GitHubError.status` at that call rather than classified in `GitHubClient.http_failures`.
Classifying it there would hand it to every other read, and two of them turn
`NOT_FOUND_OR_INACCESSIBLE` into an affirmative observation — the branch-protection read into "the
branch is unprotected", the alert-family read into "the family is not enabled" — so a malformed or
throttled request would be published as a fact nobody observed.

**ONE INVALID METRIC KEY SILENTLY EMPTIES THE WHOLE RESPONSE.** Measured 2026-08-27:
`/api/measures/component` returns `measures: null` for the ENTIRE call if any requested key does not
exist — `maintainability_rating` does not (it is `sqale_rating`) — so an unnoticed typo would report
every repository as having no measures rather than erroring. `SONAR_METRIC_KEYS` is therefore a single
constant guarded by a test that asserts every member is one of the documented valid keys and that
`maintainability_rating` is not among them. Do not add a key without extending that test.

`map-sonar` IS A SEPARATE, PERIODICALLY-RUN COMMAND, and the 10-per-minute commit-search limit is the
whole reason. It cannot ride on `collect`: 289 projects at that rate is a run measured in tens of
minutes, paid on every collection, to refresh a map that changes when a project is re-analysed against
a new repository. It stores every row as it is resolved, so a run stopped by a rate limit keeps
everything it already paid for; it skips a project whose listed `analysisDate` is no later than the
instant its stored row was written, because nothing has been analysed since and there is nothing to
learn. THE SKIP IS KEYED ON WHEN THE ROW WAS WRITTEN, NOT ON WHICH ANALYSIS ANSWERED: `sonar_to_github`
walks back up to five analyses and records the instant of whichever one resolved, so keying on that
would never converge for the force-pushed-branch case the walk exists for — the row would be
re-searched every run and `record_sonar_mapping` would then refuse the identical write. Because the
skip is keyed on that instant, A REFUSED WRITE STILL MOVES IT: when the answer a run arrives at does
not supersede the stored one, `record_sonar_mapping` leaves the answer alone and touches `resolved_at`
anyway, recording that the question was asked. Without that, the very case the walk exists for would
never converge either — the newer analysis that provoked the search would still stand above the
watermark next run, and the search would be paid for again on every run forever. Confirming a
CANDIDATE is cheap and is used wherever possible —
`GET /repos/{org}/{repo}/commits/{sha}` spends the 5,000-an-hour core quota, and only DISCOVERING an
unknown repository needs the search API.

A REVISION IS AN OBJECT NAME OR IT IS NOTHING. `searchable_revisions` drops any revision that is not
7–64 hexadecimal characters. Nothing else can match a commit, so the filter costs no answers — and it
is also what keeps a value SonarCloud chose out of a URL this code builds: `requests` resolves `..`
segments and honours a `?` in a path, so an unconstrained revision could turn
`/repos/{org}/{repo}/commits/{sha}` into a different authenticated endpoint whose `200` the
declaration check would read as a confirmation.

PER-REPOSITORY COST OF THE `collect` PATH. Two to three SonarCloud reads (`project_analyses` on the
declared key where one is declared, then `project_analyses` and `component_measures` on the resolved
project) and at most one core-quota GitHub read (`/commits/{sha}`, only to confirm a declared key the
map has never resolved). ZERO of either where the repository declares nothing and the map knows
nothing about it, which is most of the organisation. No commit search is ever issued here — that
quota belongs to `map-sonar` alone. Both clients feed the one `costs` figure, which is the
measurement: the standing rule that AN ESTIMATE OF A NEW SOURCE'S COST IS NOT EVIDENCE is satisfied
by reading `costs` off a real run rather than by the shape above.

TWO KNOWN LIMITATIONS, both consequences of the above rather than defects to fix:

- A repository whose project has not been analysed within SonarCloud's pull-request retention and
  which carries no properties file CANNOT BE MAPPED until `map-sonar` next runs after an analysis.
  There is no third source of evidence to fall back to, and the configured override is the deliberate
  escape hatch.
- `map-sonar` MUST BE RUN PERIODICALLY. A project that moves to a different repository keeps
  reporting its old repository until the command runs again, because the map is the only thing that
  knows and nothing in `collect` refreshes it.

## Storage rule (decided 2026-08-11)

**Store what cannot be recomputed; recompute what can.**

- Pull-request and direct-commit facts: stored, because refetching is expensive and rate-limited.
- Derived metric observations: never stored. They are reconstructable from the fact cache under
  whatever cohort definition is current — which is strictly better than a snapshot frozen under an
  old one.
- Current state: stored as latest-only, because it is irrecoverable after the fact.

This removed the `runs` table's reason to exist: run history held dated observation and merge-gate
snapshots, and neither is wanted. The schema is pull-request facts + source coverage + one
current-state row per repository.

### Extension: open-alert counts are appended as an observation series (decided 2026-08-15)

THE RULE IS EXTENDED, NOT BROKEN, AND THE EXTENSION IS DELIBERATE. Open alert counts are current
state, stored latest-only, so every `collect` run overwrites the count the run before it saw. GitHub
cannot be asked what was open last month — there is no endpoint, and no other stored thing implies
it — so a past open count is IRRECOVERABLE, which is precisely the test the rule already applies to
current state. Each run therefore APPENDS one row per repository per readable alert family (the open
count, the severity breakdown where the family has one, and the instant observed) beside the
latest-only row it replaces. Nothing else changes: no derived metric is stored, and the merge gate
stays latest-only, because an earlier gate genuinely is not wanted.

TWO FILES, DECIDED. Observations live in a SEPARATE SQLite file beside the cache —
`metrics.sqlite3` gets `metrics-observations.sqlite3`, derived from the configured path rather than
configured separately so the two cannot be pointed at different directories by accident. The reason
is the cache design's own ruling: the cache is DISPOSABLE, "blowing it away and refetching is fine",
and that is load-bearing — it is why the cache carries no migrations and why a widened query may
simply invalidate coverage. Observations are the first data in this system that is not disposable,
and putting them in the cache would quietly retire the disposability ruling: `rm metrics.sqlite3`
would go from a safe operation to permanent data loss, and no one would find out until they needed
the history. One file was considered and rejected on exactly that trade; if it is ever reversed,
record here that deleting the cache then costs history.

A WITHHELD FAMILY APPENDS NOTHING. A family GitHub refused, or that is not enabled, records no row
and keeps reporting its reason on the current-state block. A row saying "unreadable" would be
indistinguishable from a genuine fall to a low count once the reason is a year old — the same rule as
"unavailable data never becomes zero", applied to a series rather than to a number. `metrics prune`
does not touch this file: it deletes unused CACHE intervals, and an observation has no coverage to
expire.

THE SERIES IS SAMPLED AND SAYS SO. It holds the instants at which a collection actually ran, and
nothing between them: never interpolated onto a period boundary, never zero-filled across a gap. So
`collect` must run on a REGULAR CADENCE from enablement onward — a missed week is a hole in the
series for ever, and the tool cannot fill it in later.

## Cohort definition (decided 2026-08-10)

- Do NOT exclude bots as a class. Agent/AI-authored pull requests are authored by bots and are the
  subject under study; they always stay in the cohort. There may be none at entry assessment, but
  some teams are already doing AI work and those must be included.
- DO exclude dependency-update automation: `cohort.excluded_authors`, default
  `("renovate", "dependabot")`. Matching strips GitHub's `[bot]` suffix, so `renovate` also matches
  `renovate[bot]`. Mechanical version bumps, bot-approved and merged in seconds, otherwise dominate
  review coverage, cycle time and PR size — 86 of 182 merged pull requests on cath-service.
- Reviewer eligibility separately excludes bots (`is_human_review`): a bot approval is not
  independent human review. Cohort membership (author) and review eligibility (reviewer) are
  different questions.
- `UnreviewedMerge` does not filter bot authors: an agent-authored merge with no human review is
  exactly the finding wanted.
- Every report carries a `cohort` block (merged / reported / excluded_authors counts) so
  denominators reconcile against GitHub.

## Windows and time

- All internal times are timezone-aware UTC. All intervals are half-open `[start, end)`.
- `--from 2026-05-01 --to 2026-05-08` is 7 whole days; May 8 is EXCLUDED. Document loudly — it is
  the classic off-by-one trap.
- `--days N` anchors to the most recent UTC midnight: `[midnight - N days, midnight)`. Chosen for
  determinism: stable all day, excludes today's partial data. Do NOT anchor to `now`.
- `--from` alone: end defaults to that same midnight. `--to` + `--days`: start = end - days.
  `--from` + `--days`: end = from + days. `--days` with both `--from` and `--to`: reject.
- Default with no flags: `lookback.operational_days`.
- Parsed by `datetime.fromisoformat` (no dependency): bare date = midnight, naive = UTC. Anything
  else is rejected with the accepted forms listed.
- Both commands resolve windows through `metrics/window.py`, so a coverage interval and a report
  window have the same edges.

## Trend measurement (decided 2026-08-15)

The entry gate answers one question at one instant: is this repository ready NOW. This section
settles the second question the Purpose amendment above admits — once a team IS enabled, what
changed — as a TIME SERIES over the same windowed signals, anchored to an enablement instant.

**The enablement instant is configuration, not an observation.** Someone turned the tooling on for a
team on a date; GitHub cannot be asked when. It lives in the `enablement` block and parses exactly as
a window edge does (`datetime.fromisoformat`, bare date = UTC midnight, naive = UTC), so one rule
governs every instant in the system. A repository with no configured date reports that reason and no
series — a guessed anchor is worse than none, and unavailable data never becomes zero.

**Baseline**: the window of ONE PERIOD LENGTH ending at the enablement instant —
`[enablement - period_days, enablement)`. One period, not several, so the before and the after are
the same shape and no averaging is needed to compare them.

**Period**: consecutive half-open windows of EQUAL LENGTH starting at the enablement instant —
`[enablement + k·period_days, enablement + (k+1)·period_days)`. The most recent PARTIAL period is
EXCLUDED: a short period is not comparable with a full one, and including it would show every series
dipping at its right-hand edge for arithmetic reasons alone. Half-open `[start, end)` throughout, as
everywhere else.

**Suppression**: a period whose cohort is below `assessment.minimum_merges` reports its cohort counts
and SUPPRESSES its metric values, with the reason — the existing thin-window discipline applied per
period, since a rate over four merges is arithmetic. A baseline that is itself unobservable
(insufficient cohort, or the repository's history does not reach it) suppresses every DELTA and says
why, but never the series: the periods without deltas are still evidence, while a delta without a
stated baseline is a lie.

The semantics table, fixed so that no implementation has to guess a shape:

| metric shape | examples | per-period value | delta against baseline |
| --- | --- | --- | --- |
| rate | independent-review-coverage, approval-coverage, checks-passing-at-merge | the rate, with numerator and denominator | PERCENTAGE-POINT difference (81.2% → 85.0% is +3.8 points), never a relative % of a % |
| distribution | pull-request-size p75, merge-cycle-time median, time-to-first-review median | the SAME fixed percentile the assessment grades | percentage change of the percentile |
| count | merges per period, pull requests merged, direct commits, active contributors | the count | percentage change, absolute counts always shown; a zero baseline reports the counts and no percentage, with the reason `baseline-zero`, never infinity |

**ACTIVE CONTRIBUTORS ARE COUNTED, AND BOTS ARE NOT CONTRIBUTORS (2026-08-19, user instruction).** The count row
`active-contributors` reports the DISTINCT ACCOUNTS that authored a merge into the default branch over the window, by
either route, and is the only number in the throughput block that counts people rather than changes. It moves as a
count, per the table above.

Three rulings hold it in place.

- **Bots are excluded, by two independent signals** — `author_type == "Bot"`, which GitHub sets only for a GitHub App,
  and the `[bot]` login suffix, which is how an ordinary user account running as automation gives itself away. Neither
  catches the other's cases. The predicate is `analysis.is_human_account`, shared with `is_human_review`, because two
  spellings of "is this a bot" would eventually disagree about the same account and a report would then call a review
  human and its author not.
- **This is NARROWER than `cohort.excluded_authors`, and the two are meant to differ.** The cohort drops dependency
  automation and deliberately KEEPS agent-authored merges — that ruling stands, and agent-authored work remains work
  this report covers. An agent is nevertheless not a person who became active, so its merges are counted and it is not.
  A period where an agent raised every change reports its merges beside zero contributors. That is a measurement of how
  the period's work was produced, not a hole in the data, and it must never be backfilled with a merge count.
- **AUTHORSHIP ONLY: reviewers are not contributors.** Every other number in the throughput block is measured over the
  merges the cohort covers, and a count mixing reviewers in would describe a different population from the counts
  printed beside it. Widening it to reviewers is a fresh ruling and belongs here first.

Logins are case-folded before they are deduplicated, because a GitHub login is unique case-insensitively. Nothing is
combined across repositories, exactly as nothing else in this report is: a person active in two repositories is one
contributor in each series, and an organisation-wide count of distinct people would be a roll-up wearing a new hat.
This count is NOT graded — no threshold for "how many people should be active" has an owner, and the general rule below
covers it without exception.

**`baseline-zero` covers EVERY percentage change, not only counts (2026-08-15, settled while the
deltas were built).** The table records the rule on the count row, because a period that merged
nothing is the obvious case, but a percentile can be zero too — a repository merging same-hour
changes has a zero-hour median cycle time, and a pull request that only moved a file has zero changed
lines. The arithmetic is identical and so is the dishonesty: an infinite percentage in a
leadership-facing report is arithmetic dressed as evidence. So any zero baseline under
`percentage_change` reports both measured values, no percentage, and the reason `baseline-zero`. A
rate is unaffected, moving in percentage points, where a zero baseline subtracts perfectly well.

**A thin BASELINE and a thin PERIOD are not symmetrical.** An insufficient baseline suppresses every
delta of every period, as above. An insufficient period suppresses its own metric VALUES, and its
COUNTS are still compared: the counts are how a reader sees that the period was thin, and dropping
them would hide the reason the values are missing. Neither is graded.

**VOCABULARY IS RULED, for the reason the "landed" ruling exists.** On the wire and in every
rendering: `trend`, `baseline`, `periods`, `delta`. BANNED: `impact`, `benefit`, `uplift`,
`attributable`. Those words claim a cause the tool has not demonstrated — team composition,
seasonality and workload all move these numbers, and a field called `impact` asserts that enablement
did. The failure mode is the same drift "landed" was pinned against: prose written across sessions
slides toward the more flattering word unless the vocabulary is fixed here. A grep over `src/`,
`tests/` and the README should find none of the four.

**NO DELTA IS GRADED.** No trend value carries a threshold, a boundary or a colour, and
`ReadinessPolicy` reads NOTHING from any of it — the trend report and the assessment do not meet. A
worsening trend is a fact for a human conversation, not a verdict: grading a delta would need a
threshold, every threshold is a policy judgement that must be arguable (decision 4 under "Readiness
assessment"), and no boundary for "how much should review coverage improve after enablement" has an
owner. Extending the assessment over trends requires a fresh user ruling recorded here first.

Open pull-request state CANNOT appear in a trend. It is the third source kind — never cached, current
by definition — so there is no history to compare against and no honest way to reconstruct one. It
stays a current-state block in `evidence` only.

**Security alerts DO appear, as an observation series rather than as periods (2026-08-15).** Open
alert counts are appended per collection run under the storage-rule extension above, and a trend
reports the observations that fall inside the span its windows cover — `[baseline start, last whole
period end)` — at the instants they were observed. They are deliberately NOT mapped onto the periods
beside them: an observation is a sample of what was outstanding on one afternoon, not a measurement
of a window, and laying the samples out under period headings would assert a count for every instant
nobody looked. An observation inside the trailing PARTIAL period is excluded for the same reason the
period itself is — it belongs to a stretch of time the report does not cover. No alert count is
graded, compared against a baseline or given a delta: unlike the windowed measures there is no
comparable "baseline count", only a sequence of instants, and inventing one would be arithmetic
dressed as evidence.

### A defect signal: NO PROXY IS RULED, and the candidates were measured (2026-08-15)

The trend carries NO defect column, and the absence is a recorded decision rather than an omission. A
defect signal was asked for; GitHub exposes no defect source, so every candidate is a PROXY, and each
was measured against the cache — 3,826 merged pull requests across 13 repositories over the settled
2026-05-09 → 2026-08-08 window, plus 19,638 direct commits — rather than estimated. The full working
is in the plan; the durable findings are these.

- **Revert pull requests** cost nothing (the title is already cached; no new source, no signature
  change) and are the WRONG measure in the only repositories where they move. 71 of the 81 reverts in
  the window are in the two deployment-config repositories, where `Revert demo to prod` is how an
  environment is returned to its released state — routine work, not a failure. Elsewhere the signal is
  silent: 40 of 52 twenty-eight-day windows read zero and 12 of 13 repositories have a zero baseline,
  so `baseline-zero` would blank the delta column for almost every repository. Revert chains
  (one measured four deep) count one incident four times, and a revert pushed as a direct commit is
  invisible — `DirectCommitFact` carries no message.
- **Bug-labelled issues** would be a new windowed source in the cheapest available shape (an aliased
  `search { issueCount }`, own coverage and signature, flat in the number of bugs, no refetch). It
  measures the LABELLING CONVENTION, and the convention here is Jira: 1,749 of 3,826 pull requests
  carry a Jira-style key against 543 carrying any `#nnn`, and the practice is not uniform across the
  13. Movement would be a change in defect rate, in labelling discipline, or in where a team tracks
  work — indistinguishable, and the third is most likely precisely during an enablement programme.
  ITS PREMISE IS UNMEASURED: whether these repositories hold labelled issues at all needs a user-run
  probe, exactly as alert access did.
- **Hotfix branches** measure a branch-naming convention this population does not use — 1 of 3,826
  titles. Reading `headRefName` widens `pull_request_query()`, so the signature changes and every
  repository's settled history is refetched, including the baselines a trend cannot re-collect later.
  Reading the branch list instead is current state, so it counts only branches that still exist and
  can produce no series.

THE CLOSE IS "NONE YET", BY DEFAULT RATHER THAN BY USER RULING — the loop that maintains the plan
holds no user, and "build nothing" is the only close it is entitled to take. A user ruling reopens
this at any time and belongs here first, before any collection code.

TWO RULES BIND WHATEVER IS EVENTUALLY CHOSEN. It is a windowed source under the taxonomy above, with
its own coverage and its own query signature, so it can never invalidate settled history. And IT IS
NAMED FOR WHAT IT IS — `revert-rate`, `bug-labelled-issues` — never `defect-rate`: "defect" names
what someone hopes the number stands for, which is the same drift the banned causal vocabulary above
is pinned against. It stays neutral and ungraded like every other trend value.

The trend's second absence is the COST AXIS — Copilot token usage and credit spend — which is
deferred for a different reason: not that the measure would be dishonest, but that nobody has yet
measured whether the source is reachable at all. It is recorded under "Access", with the probe that
settles it and the org-level design question it poses.

## Cache design (decided 2026-08-10)

- The cache exists ONLY to cut GitHub API calls and keep analysis fast. It is not a separate offline
  data model, and it is disposable — blowing it away and refetching is fine and expected. Do not
  over-engineer invalidation.
- No privacy constraint on cached content. The earlier "never retain titles, descriptions, review
  bodies or comment text" rule was dropped by the user. Store whatever is useful.
- SQLite: single file, no server, transactional interval replacement. Facts are key columns plus a
  JSON `payload`, so recording a new field needs no schema change. Mongo was considered and
  rejected — it removes only schema-change friction, not the refetch, and adds a server.
- Coverage is keyed on a hash of the GraphQL documents (whitespace-normalised). Widen a query and
  the hash changes, so stale coverage no longer matches and is refetched automatically.
- `accessed_at` supports `metrics prune --days N`. Cache size is not a real concern; keep it simple.
- Never let "what is already cached" decide what gets built. Decide the signal, then fetch what it
  needs.
- Typed Pydantic models remain the ANALYSIS contract, parsed from stored JSON. Strict typing where
  it matters; flexibility in the cache.

## The mutable edge

Every current metric is anchored to the merge instant: `eligible_reviews()` requires
`submitted_at <= merged_at` and `eligible_checks()` requires `completed_at <= merged_at`, so neither
a review nor a check arriving after a merge is eligible, and settled history cannot change. The edge
exists solely to absorb GitHub's eventually consistent search index — hence `lookback.mutable_hours`,
default 6, not the original hard-coded 7 days.

Status checks were the first source whose *raw* state genuinely evolves after merge, and they were
deliberately collected in a form that keeps the edge narrow: individual checks with completion
instants, rather than the current `statusCheckRollup.state`. Collecting the rollup state alone would
have forced a much wider edge and still lied about the past, because a check completing after a
questionable merge would repaint it green. Apply the same test to any future source — collect the
timestamps that let it be evaluated as at the merge instant, or accept a wider edge knowingly.

The mutable interval is cached without recording source coverage, so `--offline` refuses any window
overlapping it. That is deliberate (decided 2026-08-11): `--offline` means "cached, settled evidence
or nothing". Document the behaviour; do not soften it.

## Two-layer evidence model

- `behaviour_metrics/`: neutral repository aggregates, no judgment. `rules/`: configurable
  actor-level findings, i.e. policy.
- They share factual predicates in `analysis.py` (`eligible_reviews()`, `is_human_review()`) but are
  deliberately separate output contracts. Do not merge or alias their identifiers.
- `RepositoryEvidence` (evidence.py) extends `CachedBehaviourFacts` and owns report assembly for both
  layers.

## Plugin pattern (metrics and rules alike)

- One self-contained class per metric or rule, one file each, in `behaviour_metrics/` or `rules/`.
- `identifier: ClassVar[str]` on the class. No identifier enums, no extra layers.
- Configuration is injected at construction; orchestrators never receive config.
- Registry/factory in the package `__init__.py` (`behaviour_metrics()`, `configured_rules()`); the
  single dict lookup happens at the CLI boundary.
- Orchestrators are generic: iterate instances, check `.enabled`, flatten results. No concrete names
  outside the registry.
- Adding a metric is: one file in `behaviour_metrics/`, one entry in `behaviour_metrics()`, tests, and
  a README signal-dictionary entry.

## Semantics (do not revisit)

- RESPONSE BODIES ARE NEVER LOGGED (decided 2026-08-14, when security alerts were collected).
  `GitHubClient.request` logs the status, the URL and the BYTE COUNT at DEBUG, deliberately not
  `response.text`: secret-scanning alert records carry the literal detected credential in a `secret`
  field, so one debug run would copy live keys out of GitHub's access controls into a plain file on
  disk. The byte count is enough to tell an empty page from a full one. DO NOT "restore" the body
  for diagnostics — add a targeted, redacted log at the call site that needs it instead.
- Merger identity is NOT collected and NOT relevant. "Who clicked merge" was explicitly rejected;
  the signal is `unreviewed-merge` — no eligible independent review by merge time.
- Cohort: EVERY MERGE INTO THE DEFAULT BRANCH in a half-open UTC window `[starts_at, ends_at)` —
  merged pull requests plus direct commits. Ruled by the user on 2026-08-13 and BUILT: a direct
  commit COUNTS THE SAME AS A PULL REQUEST MERGED WITH NO REVIEW, and is unreviewed and unapproved by
  definition, because there was nothing to review. It is not a separate signal with its own
  threshold: pushing past the process and merging without review are the same failure, and splitting
  them would let a repository look better by choosing the more egregious route.
  This applies to the GOVERNANCE rates only — independent review coverage, approval coverage, checks
  passing, and the `unreviewed-merge` findings. Flow distributions (merge cycle time, time to first
  review, pull-request size) stay pull-request-only: a direct commit has no cycle and no review to
  wait for, and padding those samples with zeroes would be inventing data.
  The identifiers `unreviewed-merge` and `checks-passing-at-merge` KEEP their names. A commit landing
  on the default branch is a merge into that branch, so they remain accurate; a rename was proposed
  and rejected on 2026-08-13. The number of direct commits is reported separately in the `cohort`
  block so a grown denominator can be explained, but it grades nothing on its own.
- A DIRECT COMMIT IS ONE NO PULL REQUEST INTRODUCED. `associatedPullRequests` is the discriminator,
  not `git log --first-parent`: GraphQL history follows every parent, so the commits a merge brought
  in carry their pull request and only a genuine bypass is left without one. Only direct commits are
  cached — the rest are already described by `PullRequestFact`, and caching both would let one change
  be counted twice.
  On the default branch the field returns the MERGED pull request that introduced a commit and never
  an open one; open pull requests come back only for commits absent from that branch. So an open pull
  request cannot mask a direct push. Verified on cath-service 2026-08-13: 664 commits split 505 to
  183 pull requests and 159 to none.
  DO NOT "CORRECT" THIS TO `git log --first-parent`, which is the intuitive alternative and is worse.
  A squash merge puts one non-merge commit per pull request on the first-parent line, so git cannot
  tell it from a push: on cath-service git counted 295 direct commits where 136 were squashes and
  only 159 were real. The two methods were compared on 2026-08-13 and the tool over-counted nothing.
- THE TWO HALVES OF THE COHORT USE DIFFERENT CLOCKS, unavoidably. Pull requests are windowed by
  `merged_at`; direct commits by `committedDate`, because GitHub's history exposes no push instant.
  A pull request merged just after a window therefore contributes commits dated inside it, and one
  merged inside a window whose commits predate it contributes none. Neither miscounts a direct
  commit, but the two halves do not cover identical instants and reports should not imply they do.
- A DIRECT COMMIT'S CHECKS ARE JUDGED BY THEIR CONCLUSION, not as at an instant — the single
  deliberate exception to the merge-anchored rule below. Nothing gated the push, so there is no merge
  point to measure against, and no check can finish before the commit it runs on exists; requiring
  one would report every direct commit as unchecked and erase the difference between a repository
  whose CI runs on the default branch and one whose does not. The exception is bounded: it applies to
  commits that were never gated, never to a merged pull request.
  It is therefore collected as the BARE `statusCheckRollup { state }` on the history node, which
  "The mutable edge" rejects for merged pull requests and which is right here for the same reason the
  exception above is: there is no instant to judge against, so the rollup's description of the
  present is all the evidence there is. Measured on 2026-08-13, fetching each commit's individual
  checks instead cost one API call per direct commit — 92% of a cath-service collection — for detail
  nothing reads. THE REJECTION FOR MERGED PULL REQUESTS STANDS: do not generalise the cheap version
  to them, where a check going green after the merge would repaint it.
- The sample gate counts direct commits too (`assessment.minimum_merges`, renamed from
  `minimum_pull_requests` when this shipped). Counting merged pull requests alone would report
  `cannot_assess` precisely for the repository doing most of its work outside them, which is where
  the bypass is worst.
- Eligible review: submitted <= `merged_at`, not PENDING, human (type != Bot, no `[bot]` suffix),
  author != pull-request author.
- Unavailable data never becomes zero. Reports must not claim facts that were not collected.
- A BOUNDED SEARCH QUALIFIER USES GITHUB'S `start..end` RANGE, NEVER A PAIR OF COMPARISONS.
  Measured against hmcts/cath-service on 2026-08-14, the first live run of the open-pull-request
  source: `is:pr created:>=2026-05-09T00:00:00Z created:<2026-08-08T00:00:00Z` returned 614 over a
  window whose merged (182), closed-without-merge and still-open counts together account for about
  240, and 614 is the count of every pull request the repository has ever opened — GitHub applied
  only the last qualifier instead of intersecting the two. `closed:>=... closed:<...` was wrong the
  same way and merely looked plausible at 29. The half-open window `[starts_at, ends_at)` is
  therefore carried by an inclusive range ending one second inside `ends_at`; search resolves
  timestamps to the second. The merged-pull-request shards already used `merged:A..B` and were
  never affected. DO NOT "CORRECT" A RANGE BACK TO COMPARISONS to make the half-open boundary
  explicit — that is exactly the change this ruling reverses.

## Style rulings

- BRITISH SPELLING throughout (2026-08-13): `behaviour`, never `behavior`. That includes module and
  package names (`behaviour.py`, `behaviour_metrics/`), every identifier, docstrings, docs, and the
  wire values a report emits (`EvidenceKind.BEHAVIOUR` serialises as `"behaviour"`). A reader who
  greps for the American spelling should find nothing. The rule reaches past `behaviour`:
  `normalise`, not `normalize` — a 2026-08-15 sweep found four stragglers in docstrings and one local
  and corrected them.
  - ONE NAMED EXCEPTION, and it is deliberate: `organization`. It is GitHub's own spelling for the
    field, and it is a CONFIGURATION KEY users have already written into their files and a wire value
    reports already emit. Correcting it would be a breaking rename of someone else's vocabulary for
    a cosmetic gain. Prose says "organisation"; the identifier stays as GitHub spells it. Do not
    "fix" this.
- `attributable` IS BANNED ON THE TREND WIRE ONLY, not in the codebase (recorded 2026-08-15, when the
  trend vocabulary ruling below first made the word ambiguous). The merge-gate rules use it in the
  ATTRIBUTION sense — a commit attributable to a login, i.e. whose author is known — which predates
  this plan and claims nothing about cause. `UnreviewedMerge.attributable()` and its docstrings keep
  it. What is forbidden is the CAUSAL sense in a trend field name or rendering, asserting that a
  movement is attributable to enablement. A grep therefore finds the word in `rules/` and
  `analysis.py` and must find it nowhere in `trend.py`, the trend report models, or their rendering.
- Say "MERGE INTO THE DEFAULT BRANCH", never "landed" (2026-08-13). A first cut of the direct-commit
  work invented a parallel "landed" vocabulary for a concept the codebase already named; the user
  rejected it and it was removed everywhere. Their objection: "landed" is not a word engineers reach
  for outside a few kernel-adjacent communities, and its overuse is a tell for machine-written prose —
  which matters here, because these documents are written by agents across sessions and drift that way
  unless the vocabulary is pinned. The model is `Merges`, the protocol `Merge`, the metric base
  `GovernanceRate`, the report row `Total merges`. DO NOT REINTRODUCE IT.
- No if/elif chains on identity — dict lookup or polymorphism.
- No hard-coded concrete names in generic functions — inject instances.
- Avoid free functions that repeatedly unpack a model — make them methods of the owning object.
- Keep everything as simple as possible. No speculative layers.

## Scope boundaries

- No dashboards. No personal rankings: the actor section lists a person's repositories and the label
  of each, ordered alphabetically by login, and nothing scores or ranks people.
- Scoring and RAG labels were previously excluded; that exclusion was REVERSED on 2026-08-13 for the
  readiness assessment only — see "Readiness assessment" above. Metrics themselves stay neutral.
- No merger-identity collection.
- NO TEAM ROLL-UP (decided 2026-08-13). Readiness is reported per repository and nowhere else. A team
  label would need a rule for combining repositories — worst label, pooled cohort, or activity
  weighted — and the user declined to introduce one, preferring to interpret a team's repositories
  case by case with that team. Configuration keeps `teams` purely for accounting: it says who owns a
  repository, not how several repositories reduce to one verdict. Do not build this without a fresh
  instruction, and if it is ever asked for, note that a naive worst-label rule would report
  `cannot_assess` for almost every hmcts team while the access problem stands, because a single
  unreadable gate would outrank every assessable repository beside it.
  - ORDERING AND INDEXING ARE IN SCOPE, and were built on 2026-08-15. Every command reports
    repositories in one fixed order — team identifier, then repository name — so an edit to the
    configuration file cannot reorder a report and make two runs undiffable, and `--format report`
    opens with an index: one row per configured repository giving its team, its readiness label and
    whether the gate's rules could be read. The boundary this respects is exact — the index LISTS
    labels, it does not COMBINE them. It carries no total, no count and no per-team verdict, because
    each of those is the roll-up rule the user declined to choose. If it ever grows one, cut it back.
  - THE PER-ACTOR LISTING IS IN SCOPE, and was built on 2026-08-28. The evidence report closes with
    one line per person who authored a merge into a reported repository, giving each repository they
    contributed to and its readiness label. It respects the same boundary the index does, and for
    the same reason: it LISTS labels and COMBINES them nowhere. A person contributing to a red
    repository and a green one has no single readiness, so there is no per-person verdict, no
    worst-label summary and no count of people. The rulings that hold it in place:
    - `contributions` and `blocking` are per repository. `blocking` re-reports the occurrences a
      practice rule already found there, and one repository's findings never reach another's row.
      The evidence and the practice report are paired at the point of construction in
      `evidence_report` for exactly this reason.
    - Bots get no line, by `analysis.is_human_account` — the predicate `is_human_review` and
      `contributor_logins` already share, and a fourth spelling of "is this a bot" must not appear.
      Their merges STAY in every cohort, as the `active-contributors` ruling above records.
    - `readiness` is OMITTED, not defaulted, when the readiness policy is disabled. A null or a
      stand-in label would grade a repository the report deliberately left ungraded.
    - The label groups on a rendered line are ordered by the contributions behind them. That sum is
      an ORDERING KEY and is never printed; it is per person within one line and combines nothing
      across the report. If it is ever reported as a figure, cut it back.
    - Actors are ordered alphabetically by case-folded login, never by blocking count. Ordering
      people by what they blocked is the personal ranking this boundary excludes.
  - NO CROSS-REPOSITORY AVERAGING (decided 2026-08-15, when trend measurement was admitted). AN
    AVERAGE OF DELTAS IS A ROLL-UP. This is recorded beside the entry above because the trend report
    makes the forbidden thing subtler than a team label ever did: "the org improved 12%" reads as a
    summary rather than a verdict, and it is the same combining rule the user declined to choose,
    arrived at by arithmetic instead of by policy. It is also worse than a worst-label rule, because
    a mean silently weights a repository merging four changes equally with one merging four hundred.
    Trends are PER REPOSITORY, full stop: nothing averages, sums or otherwise combines values across
    repositories, in code, in rendering or in documentation. A test guards the trend index against
    growing one. If leadership asks for an organisation-level figure, that is a fresh instruction for
    the user to give — record the request, do not build it.
- No collection of WHY a gate was bypassed — ruleset bypass actors were considered and rejected.
- No schema migrations — recreate the SQLite database on schema change.

## Collection cost and performance (measured 2026-08-11 and 2026-08-13)

Collection is strictly sequential across repositories. Concurrency was WRITTEN, MEASURED AS A DESIGN,
AND DELIBERATELY REVERTED — this section exists so that decision is not re-taken blindly.

Measured on cath-service, cold cache, 90-day window, 182 merged pull requests: about 40 seconds
wall-clock, dominated by roughly 10 GraphQL search calls at about 4 seconds each, plus 4 REST calls
per repository. Budget: 5,000 GraphQL points/hour and 5,000 REST requests/hour, and GitHub reports
`cost: 1` per call, so CALLS ARE THE BUDGET and GRAPHQL BINDS FIRST at roughly 500 repositories/hour.

| repositories | sequential | 8 workers | rate-limit floor |
|---|---|---|---|
| 3 | 2 min | 40 s | negligible |
| 25 | 17 min | 2-3 min | negligible |
| 100 | 67 min | 8 min | 12 min |
| 500 | 5.5 h | 42 min | 1 h |
| 7,000 | 78 h | — | 14 h |

Conclusions: below ~100 repositories the budget is nowhere near binding; above ~500 it binds and
threads only close the gap to the floor, never below it. Maximum useful speedup is about 5.5x and
MORE THAN ABOUT 8 WORKERS BUYS NOTHING at any repository count. For the first few repositories the
whole question is worth about 80 seconds — do not implement.

Cheaper than threading, effective at every scale because they cut calls rather than overlap them:
1. ADAPTIVE SHARDING — request the whole window and split only when the 1,000-result search cap is
   hit, instead of `date_shards()` always paying one call per 30-day piece. The biggest win at scale,
   because most of several thousand repositories are low-activity.
2. DROP THE KNOWN-403 PROBE — safe only where permissions are uniform (a GitHub App installation),
   NOT for a fine-grained PAT, where access is granted per selected repository.
3. SKIP ARCHIVED REPOSITORIES — no new merges, still ~5 calls each.
4. SHORTER WINDOWS FOR MASS SCANS — cost is roughly linear in merged pull requests.
5. LEAN ON THE CACHE — steady state collects one new day, not ninety.

PAGE SIZES ARE TUNED, NOT GUESSED: `search: 25`, `reviews: 50`, `contexts: 50`. At GitHub's maximum
of 100 the status-check rollup made the search query so expensive the server returned 502/504 after
eleven seconds, deterministically — retries fail identically. The rationale lives in
`pull_request_query()`'s docstring so the numbers are not later "simplified" back to 100. The same
caution applies to `history(first: 50)` for direct commits: each history node makes the server
compute a rollup, so raise it only against a measurement.

THE DIRECT-COMMIT LESSON (2026-08-13): the first implementation fetched each direct commit's checks
in a query of its own — measured at 159 of 173 calls (92%) for a 90-day cath-service window, 18x the
pull-request baseline and 18x what was estimated when the trade was accepted. Worse, the shape rose
with the number of bypasses, so collection was most expensive on exactly the worst-governed
repositories. Fixed the same day by reading the bare `statusCheckRollup { state }` on the history
node (admissible for commits, NOT for merged pull requests — see Semantics), cutting the window to
14 calls. AN ESTIMATE OF A NEW SOURCE'S COST IS NOT EVIDENCE: measure the call shape of any new
source before committing to it at scale, and prefer shapes whose cost does not grow with the failure
being measured.

Known concurrency hazards, if it is ever implemented (both were hit, fixed, then reverted):
- SQLite needs `journal_mode=WAL` and a `busy_timeout`, or concurrent interval writes fail with
  "database is locked".
- `GitHubClient.wait_for_rate_limit()` does a check-then-`del` on `rate_limits`; two threads
  observing the same exhausted budget make the second `del` raise `KeyError` — a crash that only
  fires when actually rate-limited, exactly when threads would matter. `pop(resource, None)` fixes
  it. Everything else on the client is set in `__init__` and never mutated, so sharing one client
  and one `requests.Session` across threads is otherwise safe.

Priority order, agreed with the user: time to answer for the first two or three repositories, then
internal decisions, then performance work for the rest. Do not optimise ahead of that order.

THE INSTRUMENT (built 2026-08-15, for the fourteen-repository population). Both measurements above
were taken by hand, one repository at a time, which does not scale to fourteen and cannot be
repeated by anyone else. `collect` now reports `costs`: per repository, the calls issued for
it — GitHub and SonarCloud together, because the unit measured is one repository's collection and
splitting it by API would answer a question about quotas this instrument does not track — and the
monotonic seconds they took, summed across both phases of a run — current state and
window — and ORDERED SLOWEST FIRST, because the figure exists to name the repository that dominates
a run. `CostMeter` reads any `CallCounter` (a `requests_issued` property), which both `GitHubClient`
and `SonarClient` satisfy; the window phase has no SonarCloud counter to read, so it passes None.
The counter is one per `get` and per `graphql`, so a
paginated read counts per page and a retry counts once, and it carries NO timing: it is shared by every
repository, so a duration read off it would be wrong the moment anything else used it. A
per-repository figure is the difference between two readings taken around that repository's work,
which is what `metrics.cost.CostMeter` does.

WHERE IT IS REPORTED, AND WHY (ruled 2026-08-15). A duration is not reproducible: two runs over
identical cached evidence differ, because the second reuses the cache. That is why `costs` appears
in the COLLECTION report — which is a per-run summary, already carrying per-run figures like
`stable_intervals_fetched` — and in neither the evidence report, which is a contract, nor the stored
repository state, which describes the repository rather than the run. A log line was considered and
rejected: the table has to be diffable and pasteable to be argued with, and the run that most needs
its cost read is the one nobody thought to keep the logs of.

This measures; it does not optimise. The priority order above is unchanged, and the performance task
it eventually justifies must be written against the repository the measured table blames.

## Access — the constraint that shapes everything

Measured 2026-08-11 across the 25 most recently pushed hmcts repositories: 20 on classic branch
protection returning 403 without `Administration: read`, 5 on rulesets and fully readable. The user
has admin on cath-service specifically, so it is a complete worked example; the org-wide picture is
unchanged, and 4 in 5 repositories assess as `cannot_assess` for want of permission.

MEASURED AGAIN 2026-08-15 (the survey script, then a shell script, user-run), across the 50 most
recently pushed hmcts repositories: 13 `ruleset` (rules readable with ordinary access), 36
`classic-flag` (protected, but only the on/off indicator is visible without admin), 1 `classic-full`
(cath-service, the admin case), 0 `unprotected`, 0 `error`.

Both figures are kept. The later measurement does not replace the earlier one: two measurements four
days apart over different population sizes are evidence of a TREND toward rulesets — 5 in 25 on
2026-08-11, 13 in 50 on 2026-08-15 — and a single overwritten number is not. Each repository that
migrates to rulesets becomes assessable without anyone granting a permission, which is why the
survey is worth re-running rather than treating either figure as settled.

THE POPULATION THE TOOL IS RUN AGAINST. `hmcts.yml` now configures those 13 ruleset repositories
alongside cath-service — 14 repositories across 9 teams. That, not the access problem, is now the
population: the tool is no longer a one-repository worked example, and behaviour that only shows up
across several repositories at once (differing rule types, one failure among thirteen successes,
report ordering) is now behaviour in production rather than a hypothetical. `hmcts.yml` is
gitignored and lives only on the user's machine; `metrics.example.yaml` is the tracked example.

OWNING TEAMS ARE READ FROM CODEOWNERS, RULED 2026-08-15. The survey takes the owning team from a
repository's CODEOWNERS file rather than from `GET /repos/{org}/{repo}/teams`, for two reasons.
CODEOWNERS is readable with ordinary repository access, where the teams endpoint needs a scoped
token and would reintroduce exactly the permission cliff the survey exists to measure; and
CODEOWNERS names the team that OWNS the code, where the teams endpoint lists every team GRANTED
access, which on an hmcts repository is mostly org-wide admin teams.

THE SURVEY EMITS JSON, REWRITTEN IN PYTHON 2026-08-17. It reports every repository in the
organisation by default, one JSON object each, so a population can be selected mechanically instead
of read off a table by eye. `scripts/survey_to_teams.py` is the second half of that: it takes a
survey, selects a gate (`ruleset` by default, because those are the repositories readable with
ordinary access), groups by the first CODEOWNERS handle, and writes the `teams:` section. Three
consequences worth recording. The `+N` weakness of the old table is gone: every
handle CODEOWNERS names is now reported, so the `opal` / `finrem` split and the two `flux` teams in
`hmcts.yml` can be confirmed against the full owner list rather than against a lead handle. The
fields keep apart what a table had to conflate — `active_rules` is `[]` where GitHub answered that
there are none and `null` where they could not be READ, and `protected` on a `classic-flag` row
carries the on/off indicator itself, which can be false.

AN ALL-REPOS RUN DOES NOT FIT IN ONE HOUR'S QUOTA, MEASURED 2026-08-17: it reached the limit after
about 1,750 repositories. Several thousand calls against 5,000/hour is the arithmetic, and no
scheduling trick beats it, so the survey is explicitly resumable instead. It pauses briefly for a
rate limit and then STOPS, closing its JSON array so the partial result is valid; `--continue
<earlier output>` reprints every repository already answered without an API call, and asks only
about the rest. Waiting out the reset instead was tried and rejected — it spends the better part of
an hour to produce nothing, and a run that carried on regardless would fill its remaining rows with
`error`, which is precisely the blind-spot conflation this survey exists to prevent. For the same
reason `--continue` reuses ANSWERS ONLY: a `classic-flag` row is an answer about permission and is
reused, while `error` and `unreachable` rows are asked again rather than promoted into evidence.
`--skip-archived`, or a repository count, reduces what has to be asked at all.

WHAT THE GENERATOR DECIDES, AND WHAT IT REFUSES TO. `survey_to_teams.py` guarantees only the two
things `metrics.config` would reject a file for — each repository under exactly one team, every
identifier unique — and it proves it by reading its own YAML back through `TeamConfiguration` before
exiting. Everything else is a draft. Grouping by the alphabetically-first CODEOWNERS handle still
merges unrelated services that share a lead handle and still splits one service whose repositories
name different leads, which is why `hmcts.yml` carries hand-written notes at exactly those places;
`display_name` is derived from the slug and cannot recover a programme name like "Financial Remedy"
from `developer-enablement`. Two distinctions it does NOT blur: a repository with no CODEOWNERS is
grouped under `unowned`, because an unowned repository on rulesets is still assessable, while a
repository whose CODEOWNERS could not be READ is reported and left out rather than being called
unowned — the same refusal to conflate a blind spot with an observation that the survey makes.

Re-deriving the population is `scripts/survey_merge_gate_access.py`, which joins the other user-run
scripts — `probe-alert-access.sh` (which of the three alert families answer 200),
`measure-alert-volume.sh` (whether there are enough resolved alerts to measure anything),
`verify-direct-commits.sh` (reconciliation against the git history) and `probe-copilot-usage.sh`
(whether the Copilot usage and billing endpoints answer at all). All five need a real token and
are therefore run by the user: the loop that maintains plan.md never holds one, so no figure
in this section can be produced or refreshed from inside it.

RULED (2026-08-13): ACCESS IS NOT THE TOOL'S PROBLEM. The user will resolve it administratively;
build the tool, test against reachable repositories, and let `cannot_assess` stay the correct answer
for the rest. Do not treat access as blocking and do not keep raising it. Its one bearing on the
plan is security-posture work: check that at least one REACHABLE repository exposes security alerts
before building, as a testability check, not an access campaign.

SECURITY-ALERT ACCESS, MEASURED 2026-08-14 (`scripts/probe-alert-access.sh`, user-run with a real
token): hmcts/cath-service answers **200 on all three** families — `dependabot/alerts`,
`code-scanning/alerts` and `secret-scanning/alerts`. The testability check above is therefore MET
and security-posture work is no longer blocked on it. Scope of the evidence: ONE repository, the one
the user has admin on. It says nothing about how the three permissions generalise across the org,
and the branch-protection result above (4 in 5 unreadable) is the reason not to assume they do —
so the "reports the reason, never zeroes" requirement for an unreadable repository is load-bearing,
not defensive decoration, and must be built and tested even though the worked example never hits it.

MEASURED ACROSS THE POPULATION 2026-08-17, not by the probe but by a `collect` run: `base.report`
carries the per-family answer for all 14 configured repositories, so the table the previous note
asked for already existed as a by-product of ordinary use. Of 14: `dependabot` readable on 3,
`code-scanning` on 1 with 2 more reporting the feature simply not enabled, `secret-scanning` on 1.
The "reports the reason, never zeroes" path has therefore now met real 403s in production, on 11
repositories, and is no longer exercised by tests alone.

WHY THOSE THREE, AND THE RULE IT ESTABLISHES. The readable repositories are not a random three. A
fine-grained PAT cannot exceed the access of the USER who owns it — its permission list is a
ceiling, not a grant — so every question in this section is really a question about the user's own
role. cath-service answers all three families because the user is admin there. cnp-flux-config and
sds-flux-config answer `dependabot` and nothing else, and `gh api repos/hmcts/{repository}` reports
`push: true, admin: false` on both, against `push: false` on pcs-api which is refused. So:

    dependabot        WRITE is enough
    secret-scanning   write is NOT enough (refused on the two write repositories)
    code-scanning     unknown at write level — the feature is off on both, so nothing observed it

The consequence is that Dependabot needs NO permission grant at all, and its assessable population
is every repository the user can push to rather than the 14 configured here — a much larger set.
`survey_merge_gate_access.py` therefore reports an `access` field, free of extra calls because the
organisation listing already carries it, and `--help` carries the jq that selects on it. This is the
same widening the ruleset migration produced, and found the same way: by asking what is ALREADY
readable rather than by asking for permission. Only `secret-scanning`, and probably `code-scanning`,
genuinely need an identity the user does not have.

SECURITY MANAGER WAS CONSIDERED AND REJECTED, 2026-08-17. It is the obvious org role for this and it
would work, but it carries read on every repository in the organisation, WRITE on security alerts
across all of them — dismissing and reopening — and management of the organisation's own code
security and analysis settings. That is a wider grant than a reporting tool has any business holding,
and wider than the GitHub App below, which can be scoped to the three alert families read-only and
grants no human anything. Where the App is the bigger engineering lift, it is the SMALLER permission
ask, and that is the way round to put it to platform operations.

Two routes to fixing it, both outside the codebase: a GITHUB APP installed by an org owner with
`Administration: read`, `Metadata: read`, `Pull requests: read` and — on the 2026-08-17 finding
above — `Dependabot alerts: read`, `Code scanning alerts: read` and `Secret scanning alerts: read`
(installation-scoped, one-hour tokens, revocable, audit-logged — a fine-grained PAT always belongs
to a person), or MIGRATION TO RULESETS, which are readable with ordinary repository access and are
GitHub's successor to classic branch protection.

The App route has a second argument for it that is not about permissions at all. Platform operations
noted when the current PAT was approved that a token under a personal account is fine temporarily but
should sit under the platops service account if this becomes an active service — a credential tied
to a person dies with their account and audits every automated read as that person. An App is that
same argument's conclusion, reached properly: an identity of its own rather than a shared human one.
Note the one-hour token lifetime is a real change to `GitHubClient`, which fixes the token into its
session headers at construction; a run longer than an hour needs a refresh callable, not a string. This, plus the cheap call reductions above, is what gates widening beyond a
handful of repositories — not anything in the code. The ruleset route is the one that has actually
delivered: the 13 repositories added to `hmcts.yml` on 2026-08-15 were widened into without any
permission being granted.

### Copilot token usage and credit spend: DEFERRED UNMEASURED (2026-08-15)

The trend's cost axis — tokens consumed, credits burned, implied spend — is NOT built and must not
be until a probe table exists. `scripts/probe-copilot-usage.sh` is written and joins the other four
user-run scripts; it needs a real token, so like them it cannot be run from inside the loop that
maintains the plan, and its output cannot be estimated. Until the table is pasted into the plan and
copied here, there is NO collection code, NO schema, NO cache coverage and NO report block for this
source. This paragraph is the record of that state, not a note that the work is pending.

The probe covers three endpoints, separately gated and answering three different questions, so a
status on one says nothing about the others — the same lesson the three alert families taught:
`GET /orgs/{org}/copilot/metrics` (usage over time, the only one with a series),
`GET /orgs/{org}/copilot/billing` (seats, current state) and
`GET /organizations/{org}/settings/billing/usage` (the enhanced billing platform's usage report,
where credits and net amounts live). It reports the HTTP status per endpoint and, on a 200, the two
things documentation cannot settle: the GRANULARITY the response actually carries (aggregate, or
per user) and its REACH (the earliest and latest dated row, against the window asked for).

Reach is the expensive one to get wrong, and it decides a storage question rather than a reporting
one. If retention is short, the history a trend needs exists ONLY if something snapshots it, exactly
as open alert counts are snapshotted under the storage rule above — and every day nobody snapshots
is a day that cannot be recovered afterwards. That is why the probe is written before the design and
not alongside it.

THE OPEN DESIGN QUESTION THE PROBE DOES NOT ANSWER, recorded so it is not answered by accident when
the data arrives: these would be the first ORG-LEVEL sources in this system. Every existing source is
keyed on a repository, and the report is per repository with no roll-up (see "Scope boundaries"), so
an org-wide usage figure is a different kind of number from everything else printed and has no home
in the per-repository blocks. Where it sits — its own block, its own command, or nothing at all —
is decided when its data is real and its granularity is known, and a per-repository split that the
API does not actually provide must not be invented to make it fit.

## Environment

Python 3.14 exactly, uv-managed. Gate: `uv run poe check` (Ruff, format, strict mypy, pytest, 100%
coverage). All must pass.
