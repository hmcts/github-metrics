# Metrics — Settled Architecture Decisions

Durable rulings. Do not re-litigate these; if one is wrong, change it here deliberately and say why.
Iteration plans live under [plans/](plans/), one dated file per plan (`YYYYMMDD-short-name.md`);
completed plans are kept under [completed/](plans/completed/).

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
called out here rather than quietly edited. The personal-rankings half of that boundary holds in the
report: the actor section added on 2026-08-28 lists a person's repositories and their labels in
alphabetical order of login, precisely so that it is not one. Its UI half was amended on 2026-09-02 —
a contributor list may be ordered by the combination of labels its rows' repositories already carry,
and by nothing else about a person; see "Scope boundaries". The no-dashboards half was itself reversed on
2026-09-01 — see "Scope boundaries" — and the service and UI it admitted render these labels rather
than deriving any of their own.

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
   CAUTION-ONLY (2026-09-03, user ruling): the amber cap went the rest of the way. All three flow
   signals now report as CAUTIONS and impose no ceiling at all. The 2026-08-16 reasoning was right
   and did not go far enough: if a cost is not evidence that anything ungoverned merged, it is not
   grounds for withholding enablement either, so it cannot gate the label at amber any more than at
   red. `am-role-assignment-batch-service` was the case — held below ready by
   `merge-cycle-time-above-target` and `time-to-first-review-above-target` for being slow, with a
   compliant gate and reviewed merges. Nothing else changes: each is still graded against its
   configured `maximum`, still reported with its numbers under its `-above-target` name, and still
   amber on its metric card. `review_depth` is the precedent — graded, reported, never blocking.
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
`DistributionThreshold` (`config.py`). The defaults match the reference tool. None of them imposes a
ceiling on the label — each reports as a caution above its maximum; see decision 4 above.

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
merge, currently open, stale open; stored latest-only since 2026-09-01 with the window its two
windowed counts cover, observed fresh under `evidence --refresh`, unavailable rather than zero where
it was refused), but display-only. It traces to no decision question and carries no threshold;
revisit grading it only if the flow question proves too coarse.

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
- **`evidence` reports from the caches over a reporting window, and contacts nothing.**
  REVERSED 2026-09-01 AT THE USER'S INSTRUCTION, A BREAKING CHANGE: it collected by default and took
  `--offline` to stop it, and now it is offline-first and `--refresh` is the single live call —
  collecting the intervals the window lacks and observing open pull-request state fresh. `--offline`
  is gone; the default IS what it meant. Without `--refresh`, a window the caches do not fully cover
  is refused rather than reported short, and no credential is resolved and no session opened at all.

  What forced it is that every block a report carries now has a stored source — open pull-request
  state was the last one that did not, and `collect` stores it since the same date. A read-only
  service serving several windows off the caches (see "Scope boundaries") cannot have the default
  path of its own report command reach for GitHub, and a flag nobody remembers to pass is not a
  guarantee. A run that wants live figures still says so, in one word.
- **`map-sonar` builds the durable `sonar → github` map. It collects no evidence and reports none.**
  The third command that writes to the observations file, and the only one run BY HAND, periodically:
  it is paced against whatever commit-search allowance GitHub's `x-ratelimit-*` headers report for
  the token in use, so a full rebuild of the organisation's 289 projects is tens of minutes. Takes no
  window flags — the map is not about a period. See "SonarCloud quality evidence".

(`doctor`, `prune` and `trend` take no part in the collect/report split: the first two touch no
evidence, and the third reports through `evidence`'s own code.)

**`metrics-serve` is a SEPARATE ENTRY POINT, not a fourth command (2026-09-01).** The read-only
service (`metrics.service`, admitted by the dashboards reversal under "Scope boundaries") serves the
report `evidence` prints, over HTTP, for one of five fixed window spans. It is its own console script
with its own small parser because it shares no argument with the commands above but `--config` and
`--logging` — no window flags, no output format, no repository — and because keeping it out of
`cli.py` keeps that file's argument surface about collection and reporting. It reports the configured
cohort, so it makes the same refusal `COHORT_COMMANDS` makes below when no team is configured. FastAPI
and uvicorn are an optional extra (`uv sync --extra service`), so a collection host installs neither.

**The JSON is the contract; `--format report` is a rendering of it (2026-08-13).** `evidence --format report` prints
the same evidence as plain ASCII and is PRESENTATION ONLY: `render.py` measures nothing and collects nothing, so
the two renderings cannot disagree. Since 2026-08-31 it does derive presentation-only figures — how many repositories
or people carry each label the models already state, and each count's share of the population it was counted over —
which a reader could arrive at from the JSON by hand; nothing it prints depends on data the JSON does not carry. The report shows the metric aggregates and the cohort's review-state counts beside
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
which empties the status of the signal this ruling built. A `403` STAYS A FAILURE ONLY WHERE GITHUB
DOES NOT SAY THE FEATURE IS OFF (superseded 2026-08-29). The ruling this replaces read: "a `403`
stays a failure: GitHub returns it both for a token without the scope and for Advanced Security being
off, and only the response text tells them apart, which is too fragile a thing to grade a run on."
The evidence overturned it. Across 1850 repositories every single 403 was a feature or a plan
message — 830 `Code Security must be enabled for this repository to use code scanning`, 113
`Dependabot alerts are disabled for this repository`, 2 `Upgrade to GitHub Pro or make this
repository public to enable this feature` — and not one was a genuine refusal. Grading all 945 as
refusals is what exited `3` over the whole population and buried the real permission problem among
them: the same emptying of the signal the 404 correction above was made to avoid.

The text is read through a short anchored list, `GitHubClient.feature_disabled_phrases`, matched
case-folded: `is disabled for this repository`, `are disabled for this repository`, `must be enabled
for this repository`, `is not enabled for this repository`, `upgrade to github pro`. Every phrase but
the last is anchored on `for this repository`, so an organisation-level refusal can never match one —
GitHub does not say a token was refused "for this repository" — and the last is anchored on GitHub's
own product name. A matching 403 classifies as `FEATURE_DISABLED`, AN OBSERVATION AND NOT A
`RepositoryInventoryIssue` AT THE THREE ENDPOINTS THAT READ IT: it is reported on the block, records
no failure, and does not move the exit status, exactly as a 404 from the same endpoint does. The three
are `open_alerts`, `collect_merge_gate` and `collect_classic_merge_gate`, through
`FEATURE_NOT_CONFIGURED`; anywhere else the reason travels onto an issue like any other, because only
an endpoint whose repository has already been read with the same token can say that a 403 describes
the repository rather than the caller.

The merge gate reads it the same way, with one call in between: a rules endpoint answering "upgrade to
GitHub Pro" takes the same route as a rules endpoint that returned no rules and ASKS CLASSIC
PROTECTION, which answers the same 403 for a plan that carries neither and lands on `protected=False`,
`rules_observed=True`, no failure. The message is about rulesets, and a repository can be gated by
classic protection alone, so reading "this branch is unprotected" straight off it would publish a fact
nobody observed — and readiness vetoes on that fact, grading the repository red for a gate it has.
Where the plan genuinely carries no protection the answer is unchanged and costs one extra call on the
two repositories in 1850 that are in that position.

THE FAILURE DIRECTION IS DELIBERATE: AN UNRECOGNISED 403 STAYS `PERMISSION_DENIED`. A disabled
message nobody has listed yet is reported as a refusal, which a human then reads in the log and adds
to the list; a real refusal is never hidden by a phrase we guessed at. Do not widen the list to
unanchored words like `disabled` or `enabled` — that fragility is what the superseded ruling was
right about, and the anchors are the only reason this one is safe. A partial
run exiting `0` would let every downstream denominator read as covering the whole population when
repositories are missing from it, which at fourteen is no longer a hypothetical. `3` rather than `2`
because `argparse` already exits `2` for a usage error, and "you invoked me wrongly" is not "part of
the org would not answer". Both a partial and a wholly failed run still write their report: naming
the repositories that refused is the most useful thing a failed run produces. A cached `evidence`
run is the deliberate exception and keeps refusing outright, per the ruling above.

## Run logging (decided 2026-08-29)

EXACTLY ONE LINE PER GITHUB CALL, AT A LEVEL CHOSEN BY WHAT CAME BACK. A failed call used to emit
three lines of ours — a pre-request line, a response line, and a refusal warning — and at 1850
repositories that is a log nobody reads to the end of. `GitHubClient.log_outcome` emits the single
line, and the three words it logs under are the three the call is counted under, so the log and the
end-of-run summary can never disagree about a call:

```
DEBUG    GitHub ok 200 GET https://api.github.com/repos/hmcts/batch-audio-transcription/rulesets/17187159 (1393 bytes)
DEBUG    GitHub disabled 403 GET https://api.github.com/repos/hmcts/jaia-client/code-scanning/alerts?state=open&per_page=100: Code Security must be enabled for this repository to use code scanning.
WARNING  GitHub refused 403 GET https://api.github.com/repos/hmcts/x/secret-scanning/alerts?state=open&per_page=100: Resource not accessible by personal access token
```

A DISABLED 403 LOGS AT DEBUG, THE SAME LEVEL AS A 200. That is the point of the split: at the default
level, a `403` in the log is always a genuine permission problem, rather than one refusal in among 943
features nobody turned on. The GraphQL line carries its variables, because every GraphQL call is a
POST to the same URL and the variables are the only part of the request that names the repository it
is about. Bodies are still never logged — see "Semantics" below.

TWO EXCEPTIONS, DOCUMENTED RATHER THAN ENGINEERED AWAY. A retried response — a rate limit, a 5xx —
logs the existing retry WARNING instead of the outcome line, one per attempt, because the call has
not ended yet and only the response it ends on is logged and counted. That warning NAMES THE CALL as
well as the status: with the pre-request line gone, it is the only thing that can say which endpoint
a run that died on retries was reading. A GraphQL 200 carrying `errors`
logs the DEBUG outcome line *and* the existing errors WARNING, because those errors are only visible
after the call returned and the line describing the HTTP response is still true. At INFO and above it
is always exactly one line per call — apart from the `GitHub budget` line at DEBUG, which is
`record_rate_limit` reporting the quota rather than the call.

A RESPONSE THE RETRIES RAN OUT ON IS LOGGED AND COUNTED LIKE ANY OTHER. It is not retried, so the
outcome line is its line. It used to be neither: the rate-limit exhaustion raises from inside
`retry_delay`, which left the run a rate limit killed — the run whose summary is the only account of
what it spent — with its final calls in `requests_issued` and absent from the summary. It is counted
at the status HTTP returned, which for a GraphQL rate limit is the 200 its body arrived in.

URLLIB3'S OWN CONNECTION LINE IS NOT OURS AND IS LEFT ALONE. `--logging info` silences it. Do not add
a filter for it: a third-party logger this project reconfigures behind a reader's back is a worse
surprise than a duplicate line at DEBUG.

THE ENDPOINT TEMPLATE IS DERIVED FROM THE REQUESTED URL, NOT PASSED IN. `endpoint_template` replaces
the values a URL carries — `/repos/{organization}/{repository}`, `/orgs/{organization}`, all-digit
segments to `{id}`, the segment after `branches` to `{branch}`, and the `page`, `after`, `before` and
`cursor` query values with placeholders — and leaves everything else exactly as it was. GraphQL
collapses to the bare `POST https://api.github.com/graphql`. Reading the URL rather than taking a
name from each caller means none of the several dozen call sites has to be told what it is asking
for, and a template cannot drift from the request it claims to describe. It is the URL asked for
rather than the one answered, so a call GitHub redirects — a renamed repository — is counted and
logged under the name this tool used, which is the name its configuration and its report use. The
pagination placeholders matter as much as the path ones: left alone, one paginated read fragments into
a counted endpoint per page, and a Link header carrying a cursor rather than a page fragments it into
one per repository — the same failure as counting one endpoint read 1850 times as 1850 endpoints. Page
one carries no `page` parameter at all, so a read that paginated is two lines rather than one; that is
the intended floor, and it does not grow with the number of pages or repositories.

`collect` LOGS THE CALL SUMMARY AFTER THE RUN, INCLUDING A RUN THAT FAILED, from the counter the
client keeps beside `requests_issued`. Sorted by status descending, then `refused` before `disabled`
before `ok`, then count descending: within one status, count alone would bury twelve refused calls
under 943 disabled ones, which is the one thing the summary exists to surface. It is one log record
rather than one per row, because a logger writing a timestamp and a level in front of each row would
break the columns it just built.

```
GitHub calls issued for 1850 repositories:
    12  403  refused   GET https://api.github.com/repos/{organization}/{repository}/secret-scanning/alerts?state=open&per_page=100
   830  403  disabled  GET https://api.github.com/repos/{organization}/{repository}/code-scanning/alerts?state=open&per_page=100
   113  403  disabled  GET https://api.github.com/repos/{organization}/{repository}/dependabot/alerts?state=open&per_page=100
  1848  200  ok        GET https://api.github.com/repos/{organization}/{repository}
  1848  200  ok        POST https://api.github.com/graphql
```

ONLY THE 403 IS SPLIT. Every other failed status is `refused`, which includes the 404 that
`open_alerts` and `collect_classic_merge_gate` read as an observation: the client sees one endpoint's
404 and cannot tell it from a 404 on repository metadata, which is a real failure, so the outcome word
and the WARNING level for a 404 are exactly what they were before this ruling. The cost is that those
404s sort above the 403 refusals in the summary, which sorts by status first. Left as it is
deliberately — the alternative is a fourth outcome word decided per endpoint, threading the meaning of
a status back into a client whose whole design keeps it out (see `http_failures`).

`collect` ONLY, FOR BOTH THE SUMMARY AND THE PROGRESS LINES. `evidence` collects too and meets the
same 403s: it fills its window through `synchronize_merges` rather than through the two phases, and
neither was left off it by accident. Adding the summary there is one more call to `log_call_summary`.

PROGRESS IS ONE LINE PER REPOSITORY PER PHASE, AT INFO, formatted in `metrics/progress.py` so that
both phases and any later caller pad and order it identically. Each line carries the position, the
total, the repository, what the phase established, and the calls and seconds the `CostMeter` already
measured for the `costs` table — the same reading, so a line and the table cannot disagree. The two
phases are separate loops, all current state before any window, so this is two sequences of 1850
lines rather than one line per repository. Joining them was tried before and rejected for the
complexity it adds to the run; the phases stay apart.

```
[   1/1850] cath-service  state ok (37 calls, 12.4s)
[   2/1850] rpx-shared-infrastructure  state ok, dependabot and code scanning not enabled (9 calls, 1.1s)
[   3/1850] cp-amp-terraform  state 1 unavailable: merge_gate permission denied (11 calls, 2.0s)
[   1/1850] cath-service  window fetched 2 intervals (14 calls, 8.1s)
[   2/1850] rpx-shared-infrastructure  window fully reused (0 calls, 0.0s)
```

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
  that stored it so a cached report shows the last answer rather than refusing.

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

**The production approvals list is NEITHER KIND, and `collect` does not gather it (2026-09-04).** It
is one organisation-wide classification document rather than a per-repository state or a window of
history, it is fetched credential-free by the SERVICE and refreshed by the window warmer, and it is
stored nowhere. See "Scope boundaries", 2026-09-04, for the whole ruling and for why it is not a
third kind of collected source.

**Open pull-request state is CURRENT STATE, stored latest-only beside the merge gate. REVERSED
2026-09-01 at the user's instruction.** It was ruled a third kind — never cached, fetched fresh on
every `evidence` run, the then-`--offline` path reporting a reason rather than a remembered or zeroed number — on
the grounds that opened/closed-without-merge/currently-open/stale-open counts have no settled state
to cache and cost only one bundled GraphQL call (four aliased `search { issueCount }` selections),
so a stale "stale for 14 days" figure would be worse than the extra call.

What reversed it is that `evidence` must be able to answer with no network at all, because a
read-only service and UI serve several windows off the caches (see "Scope boundaries"). A block that
refuses these four counts unless GitHub can be reached is a permanent hole in every report served
that way, and "we could have stored this and did not" is a worse answer than a timestamped
observation. So `collect` now observes the counts per repository and stores them latest-only, in the
same row as the merge gate and the alert counts, replaced on every run.

Three things the reversal does not change:

- THE WINDOW TRAVELS WITH THE COUNTS. Two of the four — opened, closed without merge — are bounded by
  the collection window, so the stored `OpenPullRequestSnapshot` carries its `starts_at` and
  `ends_at`. A count over a window nobody remembers cannot be read: 40 opened over ninety days and
  40 over one week are different repositories. The instant the observation was made is the stored
  row's `fetched_at`, which is where every other current-state block takes its timestamp from.
- FRESH IS STILL AVAILABLE, on request: `evidence --refresh` observes the state live and overrides
  the stored block, which is the answer for a reader who needs the counts as of now rather than as of
  the last collection.
- A REFUSAL IS STILL UNAVAILABLE EVIDENCE, recorded as `EvidenceKind.OPEN_PULL_REQUESTS` and never as
  a zeroed count: "nothing is open" and "nobody would say" are different answers, and a repository
  whose counts were refused keeps every other block it did report.

The report side, added the same day: `evidence.stored_open_pull_requests` projects the stored snapshot
into the `open_pull_requests` block beside the five projections already reading that row, and
`StoredReports` carries all six so a call site can never pair one repository's counts with another's
gate. The window rides on `OpenPullRequestReport` as its own `starts_at`/`ends_at` rather than as
prose beside the counts, because the model forbids a detail next to a summary — a sentence under four
numbers must always be the reason they are missing, never a caveat about them. A row stored before
this state was collected reports THAT, not four zeros, exactly as the pre-alerts and pre-CODEOWNERS
rows do. Under `--refresh` the fresh observation carries the REPORTING window it was just measured
over and replaces the stored block before assembly, so a refreshed report is read exactly like a
stored one.

`lookback.stale_open_days` (default 14) measures from LAST UPDATE, not from opening, and back from
the instant the run started rather than from the end of its window — "stale for 14 days" means 14
days before the observation, whatever window the run was asked for. Does not widen
`lookback.mutable_hours` — that would weaken settled merged-history caching for an unrelated reason.

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
grade it. A failing quality gate imposes no ceiling and is graded by nothing in the report. The
dashboard sets it in a presentation tone from 2026-09-02 — see "A FIGURE ON A PAGE MAY CARRY TONE"
under Scope boundaries — which is a colour on a page and not a grade: no boundary in
`ui/src/lib/tone.ts` reaches `assessment.py` or the JSON. The conditions are
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

Open pull-request state CANNOT appear in a trend, unaffected by its 2026-09-01 reversal. It is stored
LATEST-ONLY: every run replaces the row, so there is still no history to compare against and no
honest way to reconstruct one. It is deliberately not appended as an observation series the way open
alert counts are — that would be a fresh decision about a source nobody has asked to see over time,
and taking it silently because the row now exists is exactly how a trend acquires a measure nobody
chose. It stays a current-state block in `evidence` only.

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
- ONLY A COLLECTING RUN STAMPS `accessed_at` (decided 2026-09-02). `find_missing_cached_coverage`
  takes `record_use`, true from `fill_cached_source` and false from `cached_repository_evidence`.
  Reporting from the cache is not what keeps an interval alive — the collection that records it is,
  and a collection stamps every interval it writes — while a report that stamped it would write to
  the cache file on every read. The service decides whether a held bundle still describes the caches
  by their mtime and size, so that write moved the stamp its own build had just been compared
  against: every request rebuilt the whole cohort's report, and with the collected anchor in place
  that rebuild is a full walk of every repository's facts rather than a short-circuit to
  `unavailable`. `prevailing_cached_coverage` was written this way from the start, for the same
  reason.
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

The mutable interval is cached without recording source coverage, so a cached report — `evidence`
with no `--refresh`, `trend --offline` — refuses any window overlapping it. That is deliberate
(decided 2026-08-11): reporting from the caches means "cached, settled evidence or nothing".
Document the behaviour; do not soften it.

### Offline reporting anchors at the last collection's edge (decided 2026-09-02)

THE EDGE IS WHERE THE CACHES END, NOT WHERE TODAY DOES. Every reporting window used to end at
`midnight(now)`, and the ruling above is what made that unservable: a collection records coverage up
to its own stable edge, so the morning after a run every repository is short of coverage by the
right-hand edge alone and the entire estate reports `unavailable` at every span. Measured against the
working cache on 2026-09-02: 1697 repositories covered to 2026-09-01, zero reportable at any span.
Anchored at the collected edge instead, 1593 of them report at 1w, 4w, 8w and 12w. The offline
paths — the HTTP service, `metrics evidence` without `--refresh`, `metrics trend --offline` — now end
their windows at `min(midnight(now), midnight(collected_through))`. The clamp is not decoration: a
window recorded by a `--to` in the future must not anchor a report ahead of today.

THE EDGE MOST REPOSITORIES ARE AT. The anchor is the modal per-repository edge — `MAX(ends_at)` per
repository, then the commonest of those, the later on a tie — which is the edge the last WHOLE run
left behind. Both extremes were considered and rejected, because each hands the whole estate's window
to a single repository:

- The edge EVERY repository shares hands it to the worst straggler. One repository missed for a month
  would drag the window back a month, and a thousand repositories would report a stale window to hide
  one gap.
- The GREATEST edge — what this was first built as — hands it to whichever repository ran last on its
  own. `evidence --refresh --repository x`, a collecting `metrics trend`, and a `collect` that dies
  part-way all record coverage to TODAY'S midnight for the repositories they touch, so any one of
  them moves the estate's anchor a day forward and leaves all 1697 others short of coverage by that
  day alone. One single-repository refresh would blank the dashboard and fail `metrics evidence`
  outright until the next full collection — the failure this whole ruling exists to stop.

The mode is moved by neither a straggler nor a MINORITY of repositories running ahead, and preserves
the measured behaviour above: 1697 repositories at 2026-09-01 against 103 at 2026-08-25 still anchors
at 2026-09-01. What it does not survive is a `collect` that dies past HALFWAY: 1000 repositories
recorded at a new edge against 850 at the old one carries the mode forward, and those 850 then report
`unavailable` at every span until the next run reaches them. That is the residual case, accepted
knowingly — the mode narrows the exposure from "any one repository blanks the estate" to "a run that
died past halfway blanks the part it never reached", and inferring completeness from a row count
cannot do better. A run's own completion marker could, and is where to look if this ever bites.

A repository BEHIND the anchor stays `unavailable` there, which is the existing meaning of that field
and the honest answer. One AHEAD of it still reports: `record_source_coverage` coalesces the newer
interval into the interval already stored, so its coverage spans the anchor and
`find_missing_cached_coverage` finds no gap. The edge is read for the CURRENT query
signature only — the working cache still holds seven superseded pull-request signatures and two
commit ones, and a dead signature reaching further ahead would set an anchor nothing current can
report from. Both sources are then reduced to ONE instant by taking the EARLIER of the pull-request
and commit edges, since a window needs both and an instant only one of them reaches is not covered.

`collect` AND `--refresh` STILL ANCHOR AT NOW. They are the runs that reach GitHub, and an anchor
behind now would ask them to collect less than they can — the anchor would then ratchet backwards,
each run collecting to the edge the run before it set.

THE ANCHOR IS SAID OUT LOUD. A window that silently means "as at the last collection" is read as "as
at today", so the collected instant is published on `/windows` (which every page already fetches for
the span selector) and on each estate list's header, and the CLI logs it once per run at INFO whenever the
anchor is behind today's midnight. `lookback.stale_collection_days` (default 8 — one missed WEEKLY
run, not one missed day) is the threshold above which the service marks the collection stale and
every page shows a warning bar, and the CLI logs a WARNING naming the last collection.

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
  `GitHubClient.log_outcome` logs the status, the URL and the BYTE COUNT at DEBUG, deliberately not
  `response.text`: secret-scanning alert records carry the literal detected credential in a `secret`
  field, so one debug run would copy live keys out of GitHub's access controls into a plain file on
  disk. The byte count is enough to tell an empty page from a full one. DO NOT "restore" the body
  for diagnostics — add a targeted, redacted log at the call site that needs it instead. A FAILED
  RESPONSE LOGS GITHUB'S OWN `message` AND NOTHING ELSE OF THE BODY (`failure_message`), which is the
  single exception and the reason a 403 is diagnosable at all — see "Run logging" above.
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

- NO DASHBOARDS WAS REVERSED ON 2026-09-01 AT THE USER'S INSTRUCTION, and a read-only service and UI
  are in scope. DECIDED. `metrics.service`, run as `metrics-serve`, serves the same
  `PracticeEvidenceReport` that `metrics evidence` prints — assembled by
  `evidence.offline_practice_report` for one of the five window spans in `service.WEEKS_OPTIONS` — and
  `ui/` renders it. What the reversal admits is a SECOND RENDERING of the existing contract, nothing
  more: no metric, label or count is computed in the service or the UI that the JSON does not already
  carry or that a reader could not arrive at by adding the report's own rows up, so a figure cannot
  appear on a page and nowhere in the evidence.
  THE SERVICE NEVER CONTACTS GITHUB. It holds no client, no session and no credential, reads only the
  two SQLite files a `collect` run wrote, and reports a window nobody has collected through the
  report's own `unavailable` entries rather than as thinner data. That last requirement is what forced
  open pull-request state to become cacheable first — see "Source taxonomy" — and it is the reason the
  windows on offer are a fixed list rather than a free span: each is a whole report held in memory,
  keyed on a source stamp of both cache files so a collection landing mid-day is picked up.
  THAT INVARIANT WAS NARROWED ON 2026-09-04, AT THE USER'S INSTRUCTION, AND THIS IS THE ONE
  EXCEPTION TO IT. It read "there is no client, no session and no credential anywhere in this
  module" and the service fetched nothing at all; it now reads that there is no credential and no
  GitHub API client anywhere, that every EVIDENCE response is still assembled from the caches alone,
  and that ONE credential-free GET of a public classification document is made by the service and
  refreshed by the window warmer. That document is HMCTS's `environment-approvals.yml`, whose `prod:`
  sequence is the only public statement of which repositories deploy to production, and it is what
  puts a `Production` badge on a row (`metrics.production`, `WindowCache.production_list`).
  IT IS NOT A CURRENT-STATE SOURCE AND `collect` DOES NOT GATHER IT. Everything `collect` writes is
  per repository and is evidence about a reporting window; this is ONE organisation-wide document
  stating a classification that belongs to no window, and the user asked for it to move on the
  refresh interval — `--warm-interval`, 300 seconds by default — rather than wait for the next
  collection, which is a cadence a cache keyed on a collection stamp cannot give it. It carries no
  token: the file is on a public raw content host, where a credential would buy nothing and be a leak
  with no benefit.
  A FETCH THAT FAILS COSTS A BADGE RATHER THAN A FIGURE, AND IS REPORTED AS AN ABSENT FIELD RATHER
  THAN AS `false`. The last good list is kept across a failed refresh, so a transient 502 costs
  nothing at all, and a failure is remembered for `PRODUCTION_RETRY_FLOOR` so that a host with no
  route to it is not asked again by every request that arrives: a reader reads the list with no
  margin, and without that floor an unreachable host would spend a fetch timeout per page view and
  cost the pages the badge was promised to cost instead. Only a service that has never once read the
  list serves the field absent, and
  `production_list_url: null` — for an organisation with no such list — is the same absence stated
  deliberately. "Not approved for production" and "nobody could say" are different answers, and this
  project's rule that unavailable data never becomes zero is what keeps them apart. `metrics
  evidence` DELIBERATELY GAINS NO PRODUCTION COLUMN: the fetch lives in the service, so the text
  report cannot state it, and inventing a second source at collect time so that it could would be a
  larger change than the one asked for.
  THE SERVICE ALSO SERVES `/repositories/{repository}/trend`, added 2026-09-01, through
  `metrics.trend`'s offline path with `client=None` — the same series `metrics trend --offline`
  prints, and per repository only, so the "no organisation-level trend figure" ruling below stands
  untouched. A series is cached BESIDE the window bundles rather than inside one: it is anchored to
  the repository's own enablement instant and cut into periods of its own, so it belongs to no
  reporting window and would be rebuilt by every span a reader flicks through if a bundle held it.
  Its key carries the cut, which unlike the five window spans is not a fixed list — `period_days`
  runs 1 to 365 — so that cache is bounded by count and drops the least recently read, where the
  bundle cache needs no bound at all. A cut above `MAXIMUM_PERIODS` is REFUSED RATHER THAN
  TRUNCATED, on the same reasoning that refuses an off-list `?weeks=`: a series quietly stopped at 26
  periods would be read as the whole history since enablement. The bound is on the series a request
  RESOLVES to, not only the count it names, because `periods` is optional and leaving it out asks for
  every whole period since enablement — which is where an unbounded request would otherwise live.
  THE CACHE LOCKS PER SPAN, AND A WARMER KEEPS EVERY OFFERED SPAN BUILT (2026-09-02). `WindowCache`
  held ONE lock for the whole cache, so a cold 26-week build — which walks every configured
  repository's cached facts — was every other request's wait, including requests for a span already
  built. It now hands out a lock per span and a lock per series cut through a small guarded registry
  (`LockRegistry`), keeping the bound that matters, that a span builds once, and dropping the one
  that was an accident, that only one span could build at a time. The eviction order of the series
  cache stays under a single lock of its own, because the order is shared by every key and belongs to
  none of them.
  The second half is that an hourly `--max-bundle-age` made EVERY span cold again every hour, so the
  next reader paid the rebuild on the span they were already reading. `WindowCache.warm` builds a
  span when none is held, when the source stamp has moved, or when the held bundle would go stale
  inside the margin it is given — which `bundle` cannot do, because `bundle` deliberately returns a
  bundle that is still usable, and a warmer asking for one would refresh nothing until a reader had
  already waited. `keep_warm` runs it for every offered span on a daemon thread started and stopped
  by a FastAPI lifespan handler, on `--warm-interval` (default 300 seconds, validated like the age
  and REFUSED at or above it, since the interval is also the margin: an interval outlasting the age
  asks whether a bundle will be usable further ahead than a bundle can live, and the answer for one
  built a second ago is no, so every span would be rebuilt on every wake). A warm that raises costs a
  log line and not the thread: a cache file unreadable now may be readable at the next wake, and a
  thread that died on it would leave a service that looks warm and is not. `main` still builds the
  default span synchronously before uvicorn listens, despite the warmer building it moments later —
  a cache the service cannot read must fail while a human is watching.
  THE THREE ESTATE LISTS ARE ROUTES, NOT SECTIONS OF ONE PAGE (2026-09-02). `/repositories` is the
  landing page and carries what the overview carried — the organisation header, the four estate
  figures and the readiness donut, joined on 2026-09-03 by five more donuts (enforced review,
  enforced CI, unreviewed substantial merging, test coverage and security issues) drawn off the
  `/repositories` rows'
  own fields, each counting EVERY configured repository at the span with an unknown band for the ones
  nothing could be read for, and none of the six filtered by the search box, because a picture that
  moved with a filter would still be read as the estate — while `/contributors` and `/teams` carry the same header above
  their own list and nothing else; `/` redirects to `/repositories`, carrying `?weeks=` through so a
  link to the old landing page does not silently change the window. THE NAV BAR'S OWN THREE LINKS
  CANNOT CARRY THE SPAN — the layout renders that bar and a layout is handed no search parameters —
  so `ui/src/proxy.ts` — Next 16's name for what was `middleware.ts` — writes the `weeks` cookie for
  any request that named a span, and the
  links resolve from it. The selector's write alone was not enough: a reader who arrived on somebody
  else's `?weeks=26` link had no cookie, and their first nav click reset the window silently. Each
  list fetches three
  responses where the overview fetched five, which also stops a cold span being entered four times
  at once. `/actors/[login]` became `/contributors/[login]`, deliberately breaking bookmarked actor
  URLs rather than leaving a `/contributors` list above an `/actors` detail: THE SERVICE'S `/actors`
  ENDPOINTS AND FIELD NAMES DID NOT MOVE, and must not be renamed to match a route — `actors` is
  what the JSON contract and the report call them. THE BAR READS DOWN THE ESTATE (2026-09-02, at the
  user's instruction): Repositories, then Teams, then Contributors — a repository, the team that owns
  it, then the people who work in it. Contributors sat second until then, which put the widest of the
  three lists between a repository and the team holding it.
  No personal rankings, BINDING ON THE UI, and amended on 2026-09-02: the actor section lists a
  person's repositories and the label of each, ordered alphabetically by login within the group their
  labels put them under, and nothing scores or ranks people.
  - A CONTRIBUTOR LIST MAY BE ORDERED BY THE COMBINATION OF LABELS ITS ROWS ALREADY CARRY
    (2026-09-02, at the user's instruction). This reverses the sentence that stood here — that every
    list of people is alphabetical and none of them carries a column to sort by. `/contributors`
    opens sorted by a readiness column holding the DISTINCT labels of a person's reported
    repositories, best first, and the header reverses it. It is the same grouping the report's
    `Enable` / `Review` / `Blocked` sections were admitted for on 2026-09-01, read as an order rather
    than as headings: `ui/src/lib/rag.ts:combinationKey` maps green, amber and red to `1`, `2` and
    `3`, dedupes, sorts and joins the digits, so `GREEN` precedes `GREEN, AMBER` precedes
    `GREEN, AMBER, RED` precedes `GREEN, RED`, which no arithmetic on a severity gives. Ties keep the
    service's alphabetical order because `sorted` is stable, and somebody the `cannot_assess`
    exclusion leaves with no label keeps their row and sorts last in both directions. THAT ROW
    CARRIES THE `CANNOT ASSESS` BADGE, on the user's further instruction the same day, where a dash
    stood first: the repositories behind an empty list were graded and every one of them came back
    unreadable, and a dash — the site's mark for "nobody measured it" — would report that as nothing
    having been checked. It is the badge alone and never an ordering: `combinationKey` still returns
    nothing for an empty list, because `cannot_assess` is not a grade between amber and red here any
    more than it is anywhere else.
    WHAT STAYS BINDING IS UNCHANGED: no score, no metric column, no count beside a name and no
    per-person verdict. The labels are the repositories' own, carried on `ActorRow.labels` as a LIST
    the service combines nothing into, and the repository count beside a login is how many
    repositories that login appears in — navigation, as the boundary has always allowed it to be, and
    its header sorts with the other two for that reason: ordering by it says who appears in the most
    repositories, which is the fact the column already prints rather than a judgement about anybody.
    `TeamActorsTable` and `ContributorsTable` are untouched and stay unsortable.
  - A FIGURE ON A PAGE MAY CARRY TONE FROM 2026-09-02, AT THE USER'S INSTRUCTION. This reverses the
    UI's own written rule that colour on a page is the readiness label and nothing else: the
    predecessor tool coloured its figures, the user asked for that back, and a page of about forty
    uncoloured boxes could not be read at a glance. THE READINESS POLICY REMAINS THE ONLY THING THAT
    GRADES A REPOSITORY. Its label, its three condition lists and the ceiling each blocking
    condition imposes are untouched, and no threshold in `assessment.py` is restated in the UI.
    What the reversal admits is PRESENTATION over figures the report already states without grading
    — an open critical alert, a quality gate reading ERROR, a coverage percentage — as four states
    (`good`, `warn`, `bad`, `neutral`) in one place, `ui/src/lib/tone.ts`, with `neutral` the
    default for any figure that table does not name.
    THE UI'S THRESHOLDS ARE PRESENTATION WHICH THE REPORT NEITHER STATES NOR DEPENDS ON. `render.py`
    prints no colour, the JSON carries no tone field, and a boundary moved in `tone.ts` changes what
    a page looks like and nothing a reader could cite. That is what keeps the reversal from growing
    into a second grading system, and it splits along one line: where the policy has already judged
    a figure the page carries ITS verdict — a behaviour metric card reads the
    `<metric>-at-target`, `-below-target`, `-above-target` or `-not-observed` condition that graded
    it, so a card can never contradict the CLEAR list above it — and where the policy judges nothing
    the boundary is stated in `tone.ts` with a comment naming its `assessment.py` counterpart where
    one exists, so page and policy can be checked against each other by reading them side by side.
    TWO REFUSALS ARE PART OF THE RULING. An unreadable figure stays uncoloured: a gate field GitHub
    withheld, an alert family it refused and a Sonar measure a project never reported are absences,
    and green there would report a missing permission as a check that passed. And the three
    merge-gate rules `ReadinessPolicy.neutral()` reports without judging stay uncoloured too,
    because colouring them would grade what the policy deliberately does not. `informational` on
    `ReadinessCondition` exists for the second of those — it marks a condition the policy reports
    without judging, which is the neutral trio and `sufficient-merges` — so a renderer can tell a
    satisfied check from one that bears on the label in neither state. It adds no line to the text
    report and changes no label.
    A DONUT MAY BAND A FIGURE STRICTER THAN THE CARD BESIDE IT (2026-09-03, at the user's
    instruction). `securityBand` bands the estate's security donut on the worst of six signals — the
    three alert families through `alertTone`, and SonarCloud's security rating, issues and hotspots —
    and puts a security rating of C at High where `sonarRatingTone` puts the same letter at amber,
    following Sonar's own scale. This is the first case of TWO UI THRESHOLDS GRADING ONE FIGURE
    DIFFERENTLY, and it is allowed because the two answer different questions about the letter: the
    repository page's card reports Sonar's grading, and the donut answers which repositories are
    worth opening. Both boundaries stay in `tone.ts` with the divergence stated beside each, neither
    reaches `assessment.py`, and security stays report-only and ungraded in the report. The three
    alert families are the countervailing rule: the donut delegates to the same `alertTone` the cards
    use, so donut and card can never disagree about a family.
    EVERY DONUT IS ALSO THE FILTER CONTROL FOR THE DIMENSION IT DRAWS (2026-09-03, at the user's
    instruction). A legend entry or a wedge writes its slice's key to that dimension's query
    parameter — `label`, `review`, `checks`, `unreviewed`, `coverage`, `security` — clicking the
    slice already set clears it, and distinct dimensions AND, so the filter state is in the URL and a
    filtered table is a thing that can be reloaded and shared. `label` keeps the name the readiness
    filter always used, so links shared before the other five existed still filter what they
    filtered. `ESTATE_FILTERS` in `ui/src/lib/rows.ts` is the ONE definition of all six — parameter,
    chip title, options and the `tone.ts` band function that classifies a row — because a filtered
    table holding a different number from the wedge that was clicked is how a reader stops believing
    either. The team page's donut works the same way: it sits above the same `RepositoriesTable`, and
    a donut that filtered on one page but not the other would be one control with two behaviours.
    THE TABLE OWNS NO FILTER CONTROL OF ITS OWN beyond its search box — the five readiness buttons
    went the same day — and the row where they sat reports what the donuts are set to, one
    dismissable chip per filtered dimension. A donut is still counted over the WHOLE estate, never
    over the filtered rows: a picture that moved with the filter it had just set would report the
    rows left over as the estate. That is why a donut drawn off a distribution the service builds
    from the reported repositories alone is handed the unreportable count beside it — both the
    estate's readiness donut and the team page's — since the table under it holds every configured
    repository and a slice must select exactly the rows it counted.
  - PER-PERSON COUNTS BESIDE A LOGIN ON A REPOSITORY PAGE ARE IN SCOPE (2026-09-02, at the user's
    instruction). The contributors table on `/repositories/[repository]` carries, per login,
    contributions, pull requests merged, pushes straight onto the default branch, merges that
    carried no independent review, and the median size of a change. Each is a count or a median of
    what was done IN ONE REPOSITORY, derived in `ui/src/lib/contributor.ts` from the per-actor
    metric summaries the service sends verbatim, by the subtraction a reader could do from the
    report's own numerators and denominators.
    ORDERING OR RANKING PEOPLE BY THEM STAYS EXCLUDED, UNCHANGED. The table keeps the
    contributions-descending order the service sends and has no sortable headers, and its doc
    comment says why: the order is about which merges make up this window, never about who did most.
    No column is a rate, no figure spans the repositories somebody works in, and nothing compares
    one login with another. If a sortable header, a rank column or a cross-repository sum appears,
    cut it back.
    `Blocking occurrences` WAS DROPPED from that table the same day. It counted the occurrences the
    findings table above it already lists per rule and per person, so the column was a second copy
    of a block the reader had just passed. RENDERING ONLY — `ActorRow.blocking` in the JSON and the
    `Actor Summary` the report prints are untouched.
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
  - PER-TEAM LABEL COUNTS ARE IN SCOPE (2026-09-01, at the user's instruction), which reverses the
    no-count half of this ruling for a second time: the first reversal was the per-label repository
    count of 2026-08-31, admitted for the whole population, and this admits the same distribution per
    team. `service.TeamRow` and `service.TeamDetail` carry `labels` — how many of one team's
    repositories hold each readiness label, zeros included — beside how many of them the window could
    not be reported for at all. It stands on the distinction the earlier reversal turned on, that a
    distribution is not a verdict, and what forced it is that a team's page is where its repositories
    are read case by case, which is what the original ruling asked for; a reader arriving there counts
    the rows by hand otherwise.
    STILL EXCLUDED, UNCHANGED: no combined team label, no worst-of or pooled-cohort rule, no team
    score, and no ordering of teams by anything they carry — the teams endpoint lists them in the
    configured reporting order. A PER-TEAM CONTRIBUTION COUNT is admitted on the same terms
    (`service.TeamActorRow.contributions`, summed over that team's repositories): it counts merges,
    combines no label, rate or distribution, and the rows stay alphabetical so nobody is ordered by it.
  - ORDERING AND INDEXING ARE IN SCOPE, and were built on 2026-08-15. Every command reports
    repositories in one fixed order — team identifier, then repository name — so an edit to the
    configuration file cannot reorder a report and make two runs undiffable, and `--format report`
    opens with an index: one row per configured repository giving its team, its readiness label and
    whether the gate's rules could be read. The boundary this respects is exact — the index LISTS
    labels, it does not COMBINE them. No index row carries a total or a per-team verdict, because
    each of those is the roll-up rule the user declined to choose. If it ever grows one, cut it back.
  - A PER-LABEL COUNT IS IN SCOPE, and the no-count half of the ruling above was REVERSED on
    2026-08-31 at the user's instruction. `--format report` now opens with a `Repository Summary`
    block — named `Summary` until the actor block below it needed the two told apart, renamed
    2026-09-01 — giving how many repositories carry each readiness label and each label's share of
    the population the report covers, above the index. A DISTRIBUTION IS NOT A
    VERDICT, which is where the line now sits: `RED 612` counts the rows a reader could count by
    hand, while a team label, an organisation score or a worst-of rule would decide something the
    user declined to decide. What forced it is scale — the hmcts configuration carries 1,863
    non-archived repositories, so the index is 1,863 rows and unreadable as a shape. Still excluded,
    unchanged: any combined label, any per-team figure, any score, and any ordering of teams by what
    they carry. The last of those covered people too until 2026-09-02, when ordering a CONTRIBUTOR
    LIST by the combination of labels its rows' repositories already carry was admitted for the UI —
    see "No personal rankings" above. Nothing DERIVED about a person orders a list even now, and the
    report's own actor section is unchanged.
    All four labels print at every run, zero included, so the block diffs line for line; the two
    non-labels the index column can hold — `not assessed`, `unavailable` — print only where something
    carries them, because a zero there is the absence of a state rather than a count of one. Each
    row's percentage is over every repository the report covers, the ones that reported and the ones
    that could not, so the counts account for all of it. Each share is rounded to a tenth on its own,
    so the printed column can read 99.9 or 100.1 rather than exactly a hundred — the counts are what
    reconcile against the index, not the shares. A report covering nothing prints dashes rather than
    a `0%` it never divided, and a count too small to round to a tenth prints `<0.1%` rather than a
    `0%` that would read as a row nobody is in.
  - THE PER-ACTOR LISTING IS IN SCOPE, and was built on 2026-08-28. The evidence report closes with
    one line per person who authored a merge into a reported repository, giving each repository they
    contributed to and its readiness label. It respects the same boundary the index does, and for
    the same reason: it LISTS labels and COMBINES them nowhere. A person contributing to a red
    repository and a green one has no single readiness, so there is no per-person verdict and no
    worst-label summary. Since 2026-09-01 THE RENDERED SECTION IS GROUPED: each line prints under the
    Enable, Review or Blocked name the entry below puts its combination of labels under, groups in
    that order with `Ungrouped` last, alphabetical login order kept within each group and a group
    nobody is in left out rather than printed empty. The group name is still not a per-person verdict:
    it names what to do next about a combination of labels, and the line beneath it lists the labels
    unchanged. The count of people that ruling once excluded is now reported too — see the entry
    below. The rulings that hold it in place:
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
    - Actors are ordered alphabetically by case-folded login within their group, never by blocking
      count. Ordering people by what they blocked is the personal ranking this boundary excludes, and
      the groups are ordered by the ruling that names them rather than by anything a person did.
    - `cannot_assess` REPOSITORIES ARE LEFT OUT OF THE RENDERED SECTION, by the user's instruction of
      2026-08-31. The label reports that a half of the question could not be read — most often a
      merge gate a non-administrator cannot see — so beside a person's name it names somebody else's
      missing permission, and across hmcts it names it for most repositories most people work in,
      crowding out the labels the section exists to show. Two consequences kept deliberately: a
      person left with no other repository gets NO LINE, because a name with no label beside it reads
      as a finding about them, and the heading says the exclusion outright, so a shortened list is
      never read as the whole of somebody's work. RENDERING ONLY — `actors` in the JSON, the index
      and the repository blocks are all unchanged, and the counts in `Repository Summary` still cover
      the whole population. It is `domain.reported_repositories`, the one place to change if it is
      ever reversed, and the counts in `Actor Summary` are taken from what it returns, so no
      combination can carry `CANNOT_ASSESS` and nobody it leaves with no repository is counted. It
      moved out of `render.py` on 2026-09-02 because the service needs the same exclusion to fill
      `ActorRow.labels` — `domain.actor_labels` is that list, and both readers of the rule go through
      the one function rather than the exclusion getting a second spelling.
  - A PER-COMBINATION ACTOR COUNT IS IN SCOPE, AND ITS GROUPS CARRY SUBTOTALS (2026-09-01, at the
    user's instruction). `--format report` prints an `Actor Summary` block immediately above the
    actor list — mirroring `Repository Summary` sitting immediately above the index — counting how
    many people carry each combination of readiness labels, with each count's share of the people the
    section reports. Multiplicity is not part of a combination: `RED x 6` and `RED` say the same
    thing about a person, so they are one row, and splitting them would divide the population by how
    many repositories somebody happens to author in. This is the count of people the entry above
    excluded, so it is a REVERSAL of that ruling and dated as one, admitted on the same grounds the
    per-label count was — a distribution is not a verdict.
    The combinations are grouped under three named actions. The table is a RULING about what to do
    next rather than a property of the labels, which is why it is written out rather than derived:

    | group | combinations |
    | --- | --- |
    | `Enable` | `GREEN`; `GREEN, AMBER` |
    | `Review` | `AMBER`; `GREEN, AMBER, RED`; `GREEN, RED` |
    | `Blocked` | `AMBER, RED`; `RED` |

    `GREEN, AMBER` enables while `AMBER` alone is reviewed, which no severity or ordering of the
    labels yields. All seven non-empty subsets of the three graded labels are mapped, so only a
    combination carrying `NOT ASSESSED` reaches the `Ungrouped` fallback; a label added to
    `ReadinessLabel` lands there too, deliberately, rather than being grouped by a guess.
    EACH GROUP PRINTS ITS OWN SUBTOTAL, by the user's instruction of the same day. A subtotal is a
    roll-up, so this is a FURTHER dated reversal of the no-roll-up ruling above, and it admits
    exactly one thing: a count of people per named action. Still excluded, unchanged — no per-team
    figure, no score, no verdict on any individual, and no ordering of people by what they carry. The
    seven listed rows print at every run, zero included, so two runs diff line for line; `Ungrouped`
    and its rows print only where something carries them, because it is not one of the actions the
    user named and an empty heading would offer a fourth.
    PERCENTAGES ARE ON THE TWO SUMMARY BLOCKS ONLY. `Repository Summary` takes its share of every
    repository the report covers, `Actor Summary` of the people the actor section reports. The
    `Review breakdown`, the `Cohort` and the metric classification counts were left alone on purpose:
    each already prints its own denominator beside it.
    `render.actor_combination` and the `render.ACTOR_GROUPS` table are THE TWO PLACES TO CHANGE if
    the grouping is ever revised — the first decides what counts as one combination, the second which
    action it falls under. The summary block and the grouped list both read them, so neither can be
    revised into disagreeing with the other.
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
  - PER-ACTOR BEHAVIOUR METRICS ARE IN SCOPE, PER REPOSITORY ONLY (2026-09-01, at the user's
    instruction). Each row of `actors` in the JSON now carries the same nine neutral aggregates the
    repository block beside it carries, measured over that person's merges IN THAT REPOSITORY:
    `evidence.actor_slice` narrows the cohort by case-folded `author_login` across both routes onto
    the default branch, and `evidence.metric_summaries` summarises it exactly as a `--metric`
    drill-down summarises the whole cohort — one computation, so a figure cannot appear in one
    rendering and not another. The boundary is the one the listing above already keeps: the rows are
    LISTED per repository and COMBINED nowhere. There is no per-person figure spanning the
    repositories somebody works in — an average of two coverage rates would weight a repository they
    merged twice in equally with one they merged eighty times in, which is the cross-repository
    averaging the entry below excludes — and nobody is ordered by any of them. Bots get no row, so
    they get no summaries either. If a per-person total, an ordering by metric, or a comparison
    between people ever appears, cut it back.
  - THE OWNING TEAM RIDES IN THE REPOSITORY BLOCK from the same date. `RepositoryPracticeEvidence.team`
    is the identifier `repository_owners` resolves from configuration, carried so that a consumer
    reading the JSON alone can group by team without the configuration beside it. IT IS ACCOUNTING,
    NOT A ROLL-UP: the field names an owner, and no team verdict, score or ordering follows it.
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
AMENDED 2026-08-29: the same figures are now ALSO logged live, one line per repository per phase, from
the same `CostMeter` reading (see "Run logging"). That does not reopen the ruling. The line reports
PROGRESS, cannot be diffed and is gone with the terminal; the `costs` table stays the reported figure,
and both are still absent from the evidence report and from stored repository state.

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
repositories, and is no longer exercised by tests alone. RE-READ AFTER 2026-08-29: most 403s of that
shape carry a feature-disabled message and are now observations rather than failures, so the 11 above
measures alert ACCESS, not the failure path — that path is met by whatever 403 the phrase list does
not recognise.

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
