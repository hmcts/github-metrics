# metrics

Collect evidence about teams' software delivery practices to support AI enablement decisions.

## Configure

Start from [`metrics.example.yaml`](metrics.example.yaml). Repository ownership is explicit: each repository may belong
to exactly one team, and an excluded repository may not also have an owner.

```yaml
version: 1
organization: hmcts
database: .metrics/metrics.sqlite3
lookback:
  operational_days: 90
  maximum_days: 365
triviality:
  maximum_lines: 10
  maximum_files: 1
assessment:
  enabled: true
  minimum_merges: 10
practices:
  unreviewed-merge:
    enabled: true
    severity: high
    minimum_occurrences: 1
    excluded_logins: []
excluded_repositories: []
teams:
  - identifier: example
    display_name: Example Team
    repositories:
      - example-service
enablement:
  example-service: 2026-06-01
```

Relative database paths are resolved from the directory containing the configuration file. **`database` names two
files**: the cache itself, and an observation history beside it — `metrics.sqlite3` gets `metrics-observations.sqlite3`.
The cache is disposable and deleting it costs only a refetch; the observation file holds the open security-alert counts
each `collect` run recorded, which cannot be refetched at all, and is therefore kept out of the file it is safe to
delete. `metrics prune` never touches it. Unknown keys and unsupported
configuration versions are rejected before any collection starts. `github_team_slugs` may optionally list GitHub teams
when the token can access that data; repository ownership remains authoritative.

`enablement` records **when agentic tooling was turned on for each repository**. It is configuration because it cannot be
observed: GitHub cannot be asked when a team was enabled. Each date parses exactly as a reporting window edge does — a
bare `2026-06-01` is UTC midnight, a naive `2026-06-01T09:30:00` is UTC, and an explicit offset is honoured — so one rule
governs every instant in the system. The JSON reports the instant as written, offset and all; `--format report` prints
the same instant converted to UTC, as it prints every other instant. Every name must be a configured repository; a date for an unconfigured one is a
configuration error naming the repository, because a typo would otherwise be indistinguishable from a forgotten entry. A
repository with no date is reported as having none rather than being anchored to a guess.

### Splitting the configuration across several files

**`--config` may be given more than once**, and the files are read in the order written and parsed as one document.
This exists so the policy every report shares — assessment thresholds, practices, lookbacks — is written once and
reused, rather than restated in each team's file where copies drift apart:

```yaml
# policy.yaml — the rules, and no teams
version: 1
organization: hmcts
database: .metrics/metrics.sqlite3
assessment:
  minimum_merges: 25
  independent-review-coverage:
    green_percentage: 95
    amber_percentage: 80
```

```yaml
# civil.yaml — the team set, and no rules
teams:
  - identifier: civil
    display_name: Civil
    repositories:
      - civil-service
```

```bash
uv run metrics evidence --config policy.yaml --config civil.yaml
uv run metrics evidence --config policy.yaml --config family.yaml
```

**The split is about how the text is stored, and nothing below it knows about it.** The files are concatenated and
parsed once, so a file is not a configuration in its own right and is never checked as one: `version`, `organization`,
`database`, and `teams` may come from whichever file states them, and neither file above loads alone. Everything the
schema does is unchanged — the same keys, the same defaults, the same rejection of an unknown key — because by the time
it runs there is only one document. A key given twice is resolved by YAML as it always was, with the last occurrence
winning, so a whole block is replaced rather than merged into. A relative `database` is resolved from the directory of
the **first** file given, which is the one the configuration starts in.

## Develop

```bash
uv sync
uv run poe check
```

## Run

```bash
export GH_TOKEN=your-fine-grained-personal-access-token
uv run metrics doctor --config metrics.example.yaml
uv run metrics collect --config metrics.example.yaml --from 2026-05-01 --to 2026-08-01
uv run metrics evidence --config metrics.example.yaml
uv run metrics evidence --config metrics.example.yaml --format report
uv run metrics evidence --config metrics.example.yaml --repository example-service \
  --metric independent-review-coverage
uv run metrics trend --config metrics.example.yaml --offline
uv run metrics trend --config metrics.example.yaml --offline --format report
```

The two collecting commands have disjoint jobs. **`collect` fills the cache and never reports a metric value.**
**`evidence` reports from the cache**, and as a convenience collects whatever the requested window lacks. Only
`evidence` computes metrics, so the two commands cannot disagree about a number. `trend` reports the same metrics as a
series of windows anchored to each repository's enablement date, through the same code — see
[Trend](#trend-measuring-periods-after-enablement) below.

`doctor` validates the configuration and verifies that the token can read every configured repository. It does not query
GitHub's repository-team endpoint, which is unavailable to fine-grained personal access tokens.

`collect` takes the same window options as `evidence` and emits a collection report: the resolved window, each
repository's current state, and what the window fetched or reused. Per repository, `collection` reports whether stable
history was `fetched`, `partially_reused`, or `fully_reused`, how many stable intervals were requested, the refreshed
mutable boundary, and the pull-request, review, and direct-commit fact counts the window holds. Identical counts across
runs are expected when the underlying evidence has not changed.

**Repositories are reported in one fixed order — team identifier, then repository name — by both commands and both
`evidence` formats**, whatever order the configuration file happens to list them in. Ordering follows the configuration
only in the sense that it is derived from it: moving a `teams:` block no longer reorders a report, so two runs stay
diffable and a diff shows what actually changed. It is presentation, not aggregation — nothing is grouped, and no
team's repositories are reduced to one label. `costs` is the one exception, ordered slowest first because it answers a
different question.

`costs` reports what the run spent per repository — `requests`, the GitHub calls issued for it across both collection
phases, and `elapsed_seconds`, measured on a monotonic clock — ordered **slowest first**, because the question it
answers is which repository dominates a run. A repository whose collection failed is reported too: the calls and the
seconds spent before the failure are the cost of the attempt. Unlike everything else `collect` emits, these figures
describe the run rather than the repository, so two runs over identical evidence will differ — the second reuses the
cache and is cheaper. They are deliberately absent from `evidence`, which is a reproducible contract, and they are not
stored: nothing in the cache remembers what a previous run cost.

Not everything `collect` fetches is windowed. Pull-request, review, and direct-commit facts are historical and
accumulate in the cache.
Repository metadata, merge-gate state, CODEOWNERS presence and default-branch maintenance instants are *current-state*
sources: GitHub cannot report what branch protection was three months ago, and the question is whether the gate protects
merges now. They are therefore always fetched fresh and stored as one latest row per repository, replacing the previous
one. So `collect --from X --to Y` means: fetch windowed
sources for `[X, Y)`, and refresh current state as of now.

Open security-alert counts are the one exception to "replacing the previous one". They are current state too, but the
replaced count cannot be recovered from anything, so each run also **appends** an observation of every readable family
to the observation file — that append is the only thing that makes a past open count reportable at all. See
[Trend](#trend-measuring-periods-after-enablement).

Direct commits are collected as their own source, cached under their own coverage and their own query signature, so
widening one source's queries never discards the other's settled history. `collect` walks the default branch through
`defaultBranchRef`, so it needs no branch name, and keeps only the commits GitHub associates with **no** pull request:
everything a merge brought in is already described by the pull-request facts, and caching it twice would let one change
be counted twice. Sizes come free with the walk; the status checks that ran on a commit are fetched afterwards, for the
direct commits alone, because a status-check rollup is the connection that made the pull-request search time out.

Collecting that source costs one call per 50 commits on the default branch and nothing per commit:
a 90-day cath-service window is 14 calls. Everything needed comes back on the history node, including
GitHub's combined status-check rollup for the commit. Fetching each direct commit's individual checks
instead was measured at 159 of 173 calls for the same window, so that design was replaced; the
arithmetic is in `docs/architecture.md` under "Collection cost and performance".

That split follows one rule — store what cannot be recomputed, recompute what can. Pull-request facts are stored because
refetching is expensive and rate-limited. Merge-gate state is stored because it is irrecoverable afterwards. Derived
metric values are never stored: they are reconstructable from the fact cache under whatever cohort definition is
current, which is better than a snapshot frozen under an old one.

Merge-gate evidence preserves whether the branch is protected, pull-request and status-check rules independently, and
the deletion, force-push, linear-history and branch-name restrictions — all six attributed rule types — beside
`unmodelled_rules`, which names any type the collector does not interpret. It assigns no score or RAG status. When GitHub hides classic branch-protection
details from a non-administrator, `protected` remains true while the unavailable detailed rule arrays remain empty.
`failures` reports inaccessible, incomplete, or malformed evidence and identifies the affected evidence; it is not a
count of GitHub issues. Collection failures remain explicit rather than becoming zero-valued evidence.

**One repository's failure does not sink the rest.** Both commands collect repository by repository, and a repository
that refuses is recorded and stepped over rather than ending the run: the repositories collected before it keep their
cached history, the repositories after it are still collected, and the refused one reports a reason instead of an empty
window. The same holds within a repository — pull-request and direct-commit history are cached under separate coverage,
so a rate limit arriving between them keeps the half already fetched. Coverage is only ever recorded for an interval
GitHub actually returned, so a partial run never leaves the cache claiming a window it did not read.

**Exit status** distinguishes the three outcomes, because "twelve of fourteen" is neither success nor failure:

| status | meaning |
| --- | --- |
| `0` | every configured repository was observed |
| `1` | the run produced nothing usable — bad configuration, no token, an unwritable cache, or every repository refusing |
| `2` | `argparse` usage error: the command line itself was wrong |
| `3` | partial — the run produced evidence and recorded at least one failure, and `failures`/`unavailable` say which |

`trend` uses the same three values, but measures them over the windows a run **resolved** rather than over the
configured repositories, and reports each reason as that window's `detail` rather than under `failures`/`unavailable` —
see [Trend](#trend-measuring-periods-after-enablement).

A partial run still writes its report, and so does a wholly failed one: naming the repositories that refused is the most
useful thing such a run produces. Note that a failure need not be a whole repository: a repository that reports with one
source withheld records one too, and a protected default branch whose classic branch-protection detail is refused to a
non-administrator is the common case across hmcts. So `collect` exits `3` on a population where every repository
reported, and `collect && evidence` stops there. Branch on `1` if the intent is to stop only on a run with nothing
usable in it. The exception is `evidence --offline`, which refuses outright if the cache does not
fully cover the window for every selected repository — a silently short window is more dangerous than no answer.

Successfully collected stable intervals are recorded as source coverage and are not requested again. A window reaching
the present also refreshes a short mutable edge, `lookback.mutable_hours` (six hours by default), replacing the facts in
that interval. A window that ends in the settled past has no mutable edge at all and is served entirely from cache. The
edge is deliberately short because every current metric is anchored to the merge instant: a review submitted after a
merge is not eligible, so settled history cannot change. It exists to absorb GitHub's eventually consistent search
index, not late reviews, and should be widened only when sources whose state genuinely evolves after merge are added.

The cache exists only to avoid repeating GitHub calls; it is not a separate data model and it is disposable. Facts are
stored as JSON payloads beside the few columns needed to query them, so recording an additional field needs no schema
change. Coverage is keyed on a hash of the GraphQL documents, so widening a query automatically invalidates the affected
intervals and they are collected again rather than served incomplete. `metrics prune --config ... --days N` deletes
intervals that have not been used for N days, together with any facts they leave behind.

This release widened the pull-request query three times — a review's comment count and its summary body, for
`review-depth`, and the pull-request body, for `description-quality` and `traceability-reference` — so **every
previously cached pull-request interval is invalidated**. Run `collect` before relying on `--offline`, which otherwise refuses a window the cache no longer covers.
At fourteen repositories that first run is a full refetch rather than an edge refresh.

`evidence` reports the requested window, collecting whatever the cache lacks unless `--offline` is given. With only
`--config`, it evaluates every enabled practice rule for every configured repository and reports each repository's
findings under `behaviour`, beside the `merge_gate` block described below: the gate is the governance a repository
declares, `behaviour` is what its merges actually did. The initial `unreviewed-merge` rule groups authors whose merges
had no eligible independent human review and provides compact references supporting each finding. It
does not claim who clicked merge because that fact is not yet collected. Bot
authors are deliberately not excluded — an agent-authored merge with no human review is exactly the finding wanted —
though `excluded_logins` can omit specific service accounts. Each rule has independent enablement, severity, and
minimum-occurrence configuration. Repositories without cached evidence are listed under `unavailable` rather than
silently omitted.

Each repository in the practice report also carries a `merge_gate` block holding the state the last `collect` stored,
with the `fetched_at` instant it was read. The two halves of that report describe different times on purpose:
`behaviour` covers the requested historical window, while the gate is current state read minutes ago, so a reader
can see a ninety-day behaviour pattern beside a gate configured yesterday. `collect` must therefore have run at least
once before `evidence` can show a gate. Where it has not, or where the gate was not observable when it was collected,
the block states that reason instead of a gate and is never omitted — an absent block would read as an absence of any
gate. Where `applies_to_administrators` was not disclosed by GitHub it is omitted rather than reported as false, because
a gate whose enforcement is unknown must not be presented as one administrators can bypass. The block itself is a
rendering of stored state and computes nothing, and it appears in the practice report rather than in a `--metric`
drill-down, which reports one aggregate and nothing else. Every rule type in it is nevertheless read by the readiness
assessment, each with the bearing set out under [Readiness assessment](#readiness-assessment) below.

Six ruleset rule types are attributed: `pull_request` and `required_status_checks` as the rules beside them, plus
`deletion` (`restricts_deletions`), `non_fast_forward` (`blocks_force_pushes`), `required_linear_history`
(`requires_linear_history`) and `branch_name_pattern` (`restricts_branch_names`). Each of the four booleans describes
**the repository's configuration** and is two-valued on purpose: false means the rule is not configured, and
`rules_observed` — not a third state on the boolean — is what separates that from a gate nobody was allowed to look at.
Classic branch protection carries `required_linear_history` under its own key rather than as a rule type, and the
collector reads it, so `requires_linear_history` is a real observation on that path too. Only `restricts_branch_names`
has no classic equivalent this collector reads, so a classic repository reports it as false, which is accurate rather
than a gap.

Beside them, `unmodelled_rules` describes **this tool's limits** rather than the repository's configuration. It names
every active rule type the collector does not interpret, sorted and deduplicated, and is populated only on the ruleset
path — classic protection has a fixed shape, so an uninterpreted key there would be a schema surprise rather than a
rule. GitHub will add a seventh rule type eventually, and naming it is the honest failure mode; dropping it would make a
gate look weaker than it is enforced. An empty tuple means every observed type was attributed, and `--format report`
prints it as `Rules not interpreted  none` rather than hiding the row, because a limitation the JSON admits and the
report conceals is worse than one neither admits.

Each repository also carries an `open_pull_requests` block: everything above covers merged pull requests only, so this
is the one place the report says what is in flight or stuck. It counts pull requests **opened in the reporting
window**, **closed without merge** in that window, **currently open** right now, and **stale open** — currently open
and last updated more than `lookback.stale_open_days` (14 by default) ago, measured from the last update, not from
when the pull request was opened. Unlike every other source, this state is **never cached**: an open pull request has
no settled state to cache, the four counts cost one bundled GraphQL call, and a cached "stale for 14 days" figure that
is itself stale would be worse than the extra call. It is therefore fetched fresh on every `evidence` run that is not
`--offline`, and `--offline` cannot report it at all — the block carries the reason instead of a remembered or zeroed
count, because unavailable data must never become zero. The block carries `fetched_at`, the instant it was read, beside
the four counts. Like the merge gate, it is display only and appears in the practice report, not in a `--metric`
drill-down.

Each repository also carries a `security` block: the open security alerts of all three families — `dependabot`,
`code_scanning` and `secret_scanning` — each with an `open` total and a `by_severity` breakdown. Like the merge gate it
is **current state**, stored latest-only by `collect` and served with the `fetched_at` instant it was read, so
`--offline` shows the last collection's answer rather than refusing. GitHub cannot say what was open in May, and the
question this answers is what is unfixed *now*; how long alerts took to resolve is a historical question this
deliberately does not answer yet.

Three details matter when reading it:

- **`by_severity` need not sum to `open`.** It counts only the severities GitHub itself asserts. A code-scanning alert
  whose rule carries no `security_severity_level` is a quality finding rather than a graded security one: it is counted
  in `open`, because it is open, and left out of the breakdown, because inventing a grade would assert something GitHub
  does not.
- **Secret-scanning alerts have no severity at all**, so their breakdown is always empty. GitHub's secret-scanning API
  exposes `secret_type` and `resolution` but no severity, and grading every leaked secret critical would rank a test
  fixture alongside a live production key.
- **A family that cannot be read reports the reason, never zero.** The three are separate endpoints behind separate
  permissions — `Dependabot alerts: read`, `Code scanning alerts: read` and `Secret scanning alerts: read` on a
  fine-grained token — so one being refused says nothing about the other two; each carries its own availability, and an
  unreadable family is rejected outright if it tries to carry counts. A refused family is also recorded in `failures`,
  so `collect` exits `3` rather than `0`: the three alert permissions do not travel with a ruleset migration, and a run
  that answered for every repository while withholding every alert family is a partial run, not a complete one.
- **A family that is simply switched off is not a failure.** GitHub answers `404` for a feature the repository does not
  use, and by then its metadata has already been read with the same token, so this is an observation and not a
  permission problem. It reports `not enabled for this repository` and still never reports zero, but it records no
  failure and does not make the run partial — otherwise `collect` would exit `3` for every repository that simply does
  not use the feature. A `403` is still a refusal, because GitHub returns it both for a token without the scope and for
  Advanced Security being disabled, and only the response text tells those apart.

Collection costs one call per family plus one more per additional hundred open alerts. Measured against
hmcts/cath-service on 2026-08-14: three calls, against a whole-repository collection of roughly fourteen. Like the merge
gate the block is display only — nothing scores it, because no open-alert threshold has an owner — and it appears in the
practice report rather than in a `--metric` drill-down.

Each repository also carries a `codeowners` and a `maintenance` block, answering the gov.uk minimum standard for
publicly accessible systems: is there a named owner, and is the repository maintained. Both are **current state**,
collected by `collect` in one bundled GraphQL query per repository, stored latest-only beside the merge gate, and
served with the `fetched_at` instant they were read, so `--offline` shows the last collection's answer. Both are
**report-only and ungraded**: the readiness assessment reads neither, per the standing rule that a signal becoming
visible is not a reason to grade it.

`codeowners` lists every CODEOWNERS file found, with its path and byte size — an empty file is found-but-empty, never
silently passing. Six locations are checked: `.github/CODEOWNERS`, `CODEOWNERS`, `docs/CODEOWNERS`, and their `.md`
variants. Each file carries `recognised_by_github`, and only the first three locations qualify: a `CODEOWNERS.md`
satisfies the letter of the minimum standard while doing nothing on GitHub, so the report keeps the two apart rather
than counting them the same. An empty list means checked everywhere and absent — an observation, never a collection
failure — and a repository whose state was stored before this source existed reports that reason instead.

`maintenance` reports when the default branch last received a commit, and when it last received one from a human.
`last_commit_at` is the newest commit on the default branch — deliberately not the repository's `pushed_at` or
`updated_at`, which move whenever a bot pushes a pull-request branch that never merges. `last_human_commit_at` is the
newest commit whose author passes the human predicate: a commit counts as automation when its linked account is a bot
(GitHub type `Bot`, or a `[bot]` login suffix) or its login matches `cohort.excluded_authors` under the usual
normalisation; where GitHub links no account, the git author name is tested the same two ways and otherwise counts as
human; a commit carrying neither a linked account nor an author name is nobody and does not count as a human commit.
All bot accounts fail the predicate, not just Renovate and Dependabot — an agent-authored commit is work the
cohort keeps, but it is not a person maintaining the repository, the same distinction `active-contributors` draws.

The human search is bounded, because a bot-dominated repository is exactly where the answer is interesting and exactly
where "page until a human appears" is unbounded. Pagination stops when a human commit is found, when the 24-month
cutoff is passed, or at a page cap — 100 commits per page, 10 pages, a cost limit rather than a policy threshold.
Whenever no human commit was found, `searched_back_to` records the oldest instant examined, which keeps two different
absences apart: "none within 24 months" (the search reached the cutoff, or exhausted the history) and "unknown beyond
the commits examined" (the cap stopped it first). Three window rows — 6, 12 and 24 months, as 183, 365 and 730 days
against `fetched_at` — each report two answers: whether *any* commit fell within the window, always decidable once
`last_commit_at` is known, and whether a *human* commit did — yes, no, or unknown with its reason. Unavailable data
never becomes zero, and here it never becomes "no" either. The rows are derived at report assembly from the stored
instants against `fetched_at`, so the JSON reproduces offline; `--format report` renders them as a table with the
unknown reasons spelled out beneath it.

If the bundled query fails, both blocks report the shared reason, one failure is recorded per block, and the run exits
`3` — the same shape as a refused alert family. A missing CODEOWNERS file is never a failure.

### Readiness assessment

Each repository also carries an `assessment`: the go/no-go answer to "is this team exhibiting good enough governance and
discipline to be given agentic tooling?". It is a `label` — `green`, `amber`, `red`, or `cannot_assess` — and three
sections that between them account for **every** condition the policy checked, so a reader sees what was examined rather
than only what failed:

- **`blocking`** held the label below green. Each condition carries the `label` it imposed, so amber and red are
  distinguishable inside the list. Green is only reachable with an empty `blocking`, so the label is never a bare
  assertion a reader has to trust.
- **`caution`** is not disqualifying but is worth weighing — a gate requiring no status check is the standing example,
  being the condition closest to a veto without being one.
- **`clear`** was checked and **did not hold the label back**, with its numbers where a number exists, so a green is as
  auditable as a red. That is wider than "checked and satisfied": it also carries the three merge-gate rules that impose
  no ceiling in either state, including ones observed to be **absent**. A `clear` entry has never implied approval, only
  that the condition did not hold the label back, so nothing about how a label is reached changes — what changes is that
  a reader can see all six rule types were examined rather than three of them going unmentioned.

**There is no team-level label.** The assessment is reported per repository and nowhere else, even though the question it
answers is asked about a team. Reducing several repositories to one verdict needs a rule for combining them — worst label
wins, a pooled cohort, or a weighting by activity — and each of those is a policy choice that would quietly decide the
answer. Read a team by reading its repositories, with the team. The `teams` block in configuration is for accounting:
it records who owns a repository, and no label is derived from it.

Details quantify wherever a number exists — `independent-review-coverage is 81.2% (78 of 96), below the 90% target`,
`substantial merges with no independent human review: 14 of 85 (16.5%), above the maximum of 1% or 0 merges` — because "below
the target" is an assertion and a count is evidence. Only blocking conditions carry a `label`: a caution and a clear
condition impose no ceiling, so they have none to report.

Governance can veto. A merge gate that requires no approving review is `red` however well the team behaves day to day,
and so is a default branch with no protection at all: both mean nothing stands between an agent and `master`. Behavioural
shortfalls are graded rather than absolute — `independent-review-coverage`, `approval-coverage` and
`checks-passing-at-merge` are `amber` below their green target and `red` below their amber boundary, and any substantial
change merged without independent review is `amber`, enough to stop a green without being counted twice against the
coverage rate that already reflects it.

Review coverage and approval coverage are graded **separately, against their own thresholds**. An approval implies a
review, so the two rates move together for most teams and a repository merging unreviewed blocks on both — on
cath-service they are the same 81.2% (78 of 96), because every independent review there ends in an approval. What the
second rate adds is the team that reviews diligently and never approves: review coverage passes, but no one is on record
as having accepted the change, and under agentic tooling the sign-off is the point at which a human takes responsibility
for code they did not write. Set `approval-coverage` to `green_percentage: 0` and `amber_percentage: 0` if that
distinction does not matter to your team — the condition then always reports as clear, with its numbers, rather than
disappearing.

Four gate conditions are reported as cautions rather than deciding the label. A gate requiring **no status check** — CI
then cannot block a merge at all, though `checks-passing-at-merge` shows what CI actually did, which is the stronger
signal than what a rule nominally demanded. A gate that **does not bind administrators**, or whose enforcement GitHub did
not disclose, so a bypass is neither confirmed nor ruled out. A gate under which **an approval survives a later
push**, which matters most where an agent revises a branch after review: the reviewed code and the merged code are then
not the same code. And a gate that **does not block force pushes** (`force-pushes-not-blocked`), for that same reason
from the other direction: an approved history can be rewritten once review is done. None of the four is a veto — the
veto set is fixed at the two governance conditions above and is not a tuning knob.

Three further gate conditions are reported and never decide the label, in either of their states — **branch deletion**
(`branch-deletion-restricted` / `branch-deletion-not-restricted`), **linear history** (`linear-history-required` /
`linear-history-not-required`) and **branch naming** (`branch-names-restricted` / `branch-names-not-restricted`). Each
lands in `clear` whether the rule is configured or absent, and its detail says so. They trace to none of the four
decision questions this tool exists to answer, so an absent one is not a shortfall and is not dressed as a caution;
reporting them at all is the point, because a check that is never reported cannot be argued with. Linear history in
particular is a deferral open to revision rather than a permanent exclusion: not important for gating enablement at
this time, and some teams may care about it.

`cannot_assess` is not a grade between amber and red — it means a half of the question could not be read, so no answer
exists. It has two causes. The first is an unreadable merge gate: measured across the 50 most recently pushed hmcts
repositories on 2026-08-15, 36 expose only the `protected` on/off indicator to a non-administrator — against 20 of 25 on
2026-08-11, the gap closing as repositories migrate to rulesets, which are readable with ordinary access — and
`protected: true` with empty rule arrays is indistinguishable from a
gate that genuinely requires nothing. Calling that red would blame a permission gap on the team; calling it green would
pass a repository that may have no gate whatever. The second is a cohort too small to show a pattern —
`assessment.minimum_merges`, ten by default — because a rate over four changes is arithmetic, not evidence. It
counts direct commits too, so a repository doing most of its work outside pull requests is graded on that work rather
than reported as unassessable exactly where the bypass is worst. An
insufficient sample suppresses the graded behaviour conditions entirely rather than reporting a thin window as a
shortfall, and is the one case where a check goes unreported: its own detail says nothing behavioural was graded. The gate
is still judged, because a veto does not depend on how many pull requests happened to merge.

A `red` condition still outranks `cannot_assess`: a disqualifier found in observed behaviour stands without seeing the
gate, and an unreadable gate must never hide one. `metrics collect` must have run since `rules_observed` was added for a
gate to be assessable — state stored before it defaults to not-observed, deliberately, because an unread gate must not be
read as an empty one. The same applies to each merge-gate rule individually: a gate stored by an earlier release reports
any rule type added since at its default, so `Requires linear history no` on stored state that predates the six-rule
attribution means "not stored", not "not configured". One `collect` run settles it.

Thresholds are configuration, because each one is a policy judgement someone has to own and argue for:

```yaml
assessment:
  enabled: true
  minimum_merges: 10
  independent-review-coverage:
    green_percentage: 90
    amber_percentage: 70
  approval-coverage:
    green_percentage: 90
    amber_percentage: 70
  checks-passing-at-merge:
    green_percentage: 90
    amber_percentage: 70
  pull-request-size:
    maximum: 400
  merge-cycle-time:
    maximum: 24
  time-to-first-review:
    maximum: 8
  unreviewed-substantial-merges:
    maximum_count: 0
    maximum_percentage: 1
  review-depth-minimum-percentage: 50
```

The two vetoes are deliberately not configurable: they are the definition of the question, not a tuning knob.

`unreviewed-substantial-merges` carries **two** allowances and a repository is within the policy when **either** one
forgives it. A bare count says nothing on its own — two unreviewed substantial merges out of 246 is a pair of lapses in
a repository that reviews almost everything, and two out of 20 is a habit — so `maximum_percentage` is the boundary
that scales with how much a team merges, and `maximum_count` is the absolute floor beneath it that keeps a thin cohort
from being condemned by arithmetic. The 1% default is a policy choice to argue with rather than a measurement: it
replaced a fixed maximum of zero that every assessable HMCTS repository tripped, and a condition that fires on every
repository distinguishes none of them. Setting both allowances to `0` restores that absolute reading. The condition is
still `amber` at most, whichever allowance it exceeds. This block replaces the earlier scalar
`maximum_unreviewed_substantial_merges`, which is now rejected at load time rather than silently ignored.

Flow signals — `pull-request-size`, `merge-cycle-time`, `time-to-first-review` — live with the repository the same way
the merge gate and CI quality do: they describe how a team works, not one person's failing, so they are never
attributed to an actor. Each is graded against its own `maximum`, the opposite comparison
from a rate's thresholds because these measure cost rather than compliance — a green result sits **at or below** its
maximum. `pull-request-size` grades its 75th percentile, since a long tail of large changes is the risk a median would
hide; `merge-cycle-time` and `time-to-first-review` grade their median. The defaults (400 changed lines, 24
hours, 8 hours) match the reference tool.

**A flow signal caps at `amber`, however far above its maximum it sits.** It says what a team's way of working costs
them, never that anything ungoverned reached the default branch, and `red` is the label for the latter. Grading a cost
to red made the two indistinguishable: across the HMCTS estate the fastest merge-cycle-time medians all belonged to
repositories that merge almost nothing through review — 0.003 hours on one with 0% review coverage — while a
repository reviewing 99.3% of 294 merges was held at red for taking four days over each. This replaced a
`green_maximum`/`amber_maximum` pair whose amber boundary was simply double the green one, which was arithmetic
standing in for a policy nobody had made. A cohort with nothing to measure — a repository
merging only by direct commit, or one GitHub never sized — reports `-not-observed` as a caution, exactly like a rate
with no denominator, rather than a manufactured shortfall. Flow distributions stay pull-request-only even when
graded: a direct commit has no cycle and no review to wait for, so the samples are never padded to include it.

**Both waiting times start when a change entered review, not when its branch was opened.** `merge-cycle-time` and
`time-to-first-review` are measured from the earliest `READY_FOR_REVIEW_EVENT` GitHub recorded, falling back to
`createdAt` where there is none — a pull request opened ready has no event, and neither does a fact cached before the
event was collected. A change opened as a draft and worked on for a fortnight was not waiting on the merge gate or on
its reviewers during that fortnight, and anchoring on creation made both metrics report how long a *branch existed*.
`isDraft` alone could never answer this: it is the state at merge, false for every merged pull request. An eligible
review submitted before the ready event takes precedence over it, because reviewing a draft is still reviewing — which
is also what stops a waiting time from coming out negative. A later conversion back to draft is deliberately not read:
rework after review has begun is part of the cycle being measured, whereas the wait before anyone was asked to look is
not. Collecting the event widens the pull-request query signature, so the next `collect` refetches the window once;
until it does, cached facts report the old creation-anchored figures.
Setting `assessment.enabled: false` omits the block rather than emitting an unjudged label.

`review-depth` is graded too, but only ever as a caution: below `review-depth-minimum-percentage` (50% by default) it
reports `review-depth-below-target`, and at or above it, `review-depth-at-target` — neither carries a `label`, so
neither can move the repository off green. No defensible line exists yet between a healthily brief approval and a
rubber stamp, so the condition is reported for a reader to weigh rather than used to decide the answer.

Every finding splits its occurrences by change size, because a one-line configuration fix merged without independent
review is a different fact from a three-file change merged the same way, and the two should never be summed into one
number. `triviality.maximum_lines` and `triviality.maximum_files` decide the boundary — a change is `trivial` only when
it is at or below both. Anything larger is `substantial`, and a change GitHub did not size is `unsized` rather than
being guessed into either class. `occurrences_by_size` carries the counts and each supporting pull-request reference
carries its own `size_class`, `changed_lines`, and `changed_files`, so a reviewer can confirm the judgement rather than
trust it. A finding's `pull_requests` array names the merges behind it and a sibling `direct_commits` array names the
pushes, each commit with its SHA, URL, instant, and size. The two are kept apart because they are different evidence and
a reader has to see which route a change took; an actor who pushed nothing directly carries **no** `direct_commits` key
at all, rather than an empty one.

`--repository` narrows the practice report. `--metric` switches to a raw aggregate drill-down and can be used with or
without a repository filter. Available metrics are `independent-review-coverage`, `approval-coverage`,
`review-depth`, `merge-cycle-time`, `time-to-first-review`, `pull-request-size`, `checks-passing-at-merge`,
`description-quality`, and `traceability-reference`. Metric JSON contains the
resolved window, aggregate
summary, and classification counts. Rates retain their numerator and denominator; an empty merged cohort is
`not_applicable`, not observed zero behaviour. Author reviews, bot reviews, pending reviews, and reviews submitted after
merge are excluded from independent-review evidence. Add `--include-identities` only to a metric drill-down when every
contributing PR and review event is needed for diagnosis; it is not part of the default findings workflow.

`review-depth` answers whether an approval means anything: a repository can show 100% approval coverage where every
approval is a click with no comment. It rates the share of eligible, independent, approving reviews that said anything
at all — an inline comment, counted with `comments(first: 0) { totalCount }`, a count-only connection that never
fetches the comments themselves, or a review summary of the reviewer's own, which GitHub records separately and which
counts by presence rather than length — over every eligible approval in the window, not over merges. A pull request with no eligible approval
classifies as `no-eligible-approval`; one whose approvals carry no comment at all classifies as
`uncommented-approval-only`. It is not graded against a green/amber/red threshold: no defensible label-deciding
boundary exists yet, so it appears in the readiness assessment only as a caution, never as something that holds the
label down.

`time-to-first-review` measures the hours between opening a pull request and its first independent human review, and
reports only the pull requests that received one; the classification counts explain why the rest are absent rather than
letting them count as zero. `pull-request-size` reports added plus removed lines for the pull requests GitHub sized.

`checks-passing-at-merge` answers whether CI was trustworthy at the merge point. Every status check and check run on the
merged pull request's head commit is collected with its completion instant, and only checks that had **finished at or
before `merged_at`** are judged — a check completing after a merge is not evidence the merge gate ever saw it, exactly
as a review submitted after a merge is ineligible. This is what keeps settled history immutable and lets the mutable
edge stay at six hours: without it, a check that went green minutes after a questionable merge would silently repaint
that merge as clean. Each merged pull request classifies as `passing` (every check finished by merge, none failing),
`failing` (a check finished by merge and did not pass), `incomplete-at-merge` (nothing failing, but at least one check
had not finished), or `no-checks` (the commit carried no checks at all). Neutral and skipped checks count as passing
because they do not block a merge, matching GitHub's own rollup.

A direct commit carries GitHub's combined rollup for the commit instead, judged by its state rather than as at an
instant: nothing gated the push, so there is no merge point to measure against, and no check could have finished before
the commit existed. `SUCCESS` classifies as `passing`, `FAILURE` and `ERROR` as `failing`, `PENDING` and `EXPECTED` as
`checks-unfinished`, and a commit no check ran on as `no-checks`. The rate's denominator
is every merge into the branch, so a repository with no CI reports a low rate rather than a flattering one — "there is no
CI" is an answer to the question, not missing data. The metric does not claim which checks were *required*, because required-ness lives in merge-gate
configuration that a token without `Administration: read` cannot see.

`description-quality` and `traceability-reference` measure how well a merged pull request explains itself. Neither
enters the readiness label: a documentation habit is not evidence a change was governed, and no threshold here has an
owner, so they are neutral aggregates only, reported the same way as any other `--metric` drill-down.
`description-quality` rates the share of merged pull requests whose body is at least `traceability.minimum_description`
characters (30 by default, matching the reference tool) once surrounding whitespace is stripped; a pull request
classifies as `described` or `description-too-short`. `traceability-reference` rates the share that reference an issue
or ticket, matched against `traceability.reference_patterns` — a **list** of regular expressions, defaulting to
`#\d+` (a GitHub issue) and `[A-Z][A-Z0-9]+-\d+` (a Jira key), because HMCTS uses both and the next organisation will
use neither. It searches the pull request's **title and body together**: a ticket key in the title is traceability
too, and insisting on the body would fail a team whose convention is the title. A pull request classifies as
`referenced` or `reference-missing`. Both metrics count merged pull requests only — a direct commit carried no
description and referenced nothing, because there was no pull request to hold either.

```yaml
traceability:
  minimum_description: 30
  reference_patterns:
    - '#\d+'
    - '[A-Z][A-Z0-9]+-\d+'
```

### The cohort is every merge, not every merged pull request

A commit pushed straight to the default branch bypasses the gate, the review, and the checks. It **counts exactly as a
pull request merged with no review**: pushing past the process and merging without review are the same failure, and
splitting them would let a repository look better for taking the more egregious route. So the denominator of
`independent-review-coverage`, `approval-coverage`, and `checks-passing-at-merge` is every merge into the branch, and
every direct commit is unreviewed and unapproved by definition — there was no pull request, so there was nothing to review. It
appears in the classification counts as `direct-commit`, so a grown denominator is always explained rather than merely
larger.

The `cohort` block reports `direct_commits` beside `merged` and `reported` for the same reason: a poor coverage rate
reads very differently when it is process bypassed rather than review skipped. It grades nothing on its own, and there
is no separate threshold for it. The same author exclusions apply, so a dependency bot pushing directly is excluded just
as its pull requests are.

Flow distributions stay pull-request-only. `merge-cycle-time` and `time-to-first-review` describe a cycle a direct
commit does not have, and `pull-request-size` keeps meaning pull-request size — padding those samples would invent data.
Direct-commit sizes are reported with the findings they support instead.

Every report covers a cohort defined by `cohort.excluded_authors`, which defaults to `renovate`
and `dependabot`. Automated dependency updates are excluded because mechanical version bumps, usually approved by their
own bots and merged within seconds, otherwise dominate review coverage, cycle time, and change size without saying
anything about how a team works. Authors are matched without GitHub's `[bot]` suffix, so `renovate` also excludes
`renovate[bot]`. Bots are deliberately not excluded as a class: agent-authored pull requests are part of the subject
under study and always remain in the cohort. Each report states the `cohort` it covers, including who was excluded and
how many pull requests each excluded author raised, so a denominator can always be reconciled against GitHub.

Both commands take the same window options, and every window is a half-open UTC interval. `--from 2026-05-01
--to 2026-05-08` is seven whole days: **May 8 is excluded.** `--days N` anchors to the most recent UTC midnight,
`[midnight - N days, midnight)`, chosen for determinism — it is stable all day and excludes today's partial data.
`--from` alone ends at that same midnight; `--to` with `--days` counts backwards from the end; `--from` with `--days`
counts forwards from the start; giving all three is rejected. With no window options at all the window is
`lookback.operational_days`. A span longer than `lookback.maximum_days` (365) is refused unless `--maximum-days` raises
it; that guard exists to catch a mistyped number, not to cap intent, because a pattern of behaviour may need the life of
a project to be visible.

`--offline` never contacts GitHub, and refuses outright if the cache does not fully cover the requested window rather
than emitting a short one — for **either** source, naming the one it could not answer for: a window whose pull requests
are cached but whose direct commits were never collected would understate every governance denominator by exactly the
bypasses it could not see — a silently truncated window is more dangerous than no answer. Because the mutable edge is
cached without recording coverage, `--offline` also refuses any window overlapping the last `mutable_hours`. That is
intended: `--offline` means settled, cached evidence or nothing. Ask for a window ending at the most recent UTC midnight
and it is served from cache.

Merge-gate collection first uses effective repository rulesets. If none apply, it requests detailed classic branch
protection and normalises pull-request reviews, status checks, deletion restrictions, force-push restrictions, and the
linear-history requirement into the same evidence fields. Classic protection has a fixed shape, so it can produce
neither `restricts_branch_names` nor an `unmodelled_rules` entry: a classic repository reports the boolean as False,
which is accurate rather than a gap. If GitHub denies access to those classic details, collection falls back to the branch metadata
protection indicator. When that indicator confirms protection, the basic evidence is retained and `failures` records the
permission-limited merge-gate detail, making the run partial. An unprotected indicator does not create a failure.

GitHub requests use bounded retries for transient failures, honour rate-limit response headers, and follow HTTPS
pagination links only within `api.github.com`. Pull-request behaviour uses 30-day GraphQL search shards, paginates search
results, and fetches additional review pages only when a bounded review connection reports more data. A shard above
GitHub's 1,000-search-result cap is reported as incomplete history rather than silently truncated.

### Output format

`--format` chooses how `evidence` and `trend` present what they found. The default, `json`, is the contract each
command's sections describe, and it is byte-for-byte unaffected by this option existing. `--format report` renders the
identical evidence as plain ASCII — no colour and no emoji, because the output gets piped, diffed, and pasted into
tickets — for reading aloud in a meeting, which is the one thing the JSON is bad at. The rules below are written against
the `evidence` report; [Trend](#trend-measuring-periods-after-enablement) describes the tables a series adds, and every
one of them obeys the same discipline — presentation only, every figure read from the JSON.

The report opens with an **index**: one line per configured repository giving the owning team, the repository, its
readiness label, and whether the merge gate's rules could be read at all. It is a table of contents for a report that at
fourteen repositories is roughly fourteen times longer than it was at one, and it answers the three questions a reader
opens such a report with — which repositories are assessable, which are RED, and which need reading in full — before any
scrolling. A repository whose collection failed gets a row too, reading `unavailable`, so the index covers the
population rather than the part of it that answered. Every cell comes from the same models the block below it renders.

**The index is not a roll-up.** There is no combined label, no per-team verdict, and deliberately no count or total of
any kind. `teams` in the configuration says who owns a repository, not how several repositories reduce to one label;
listing fourteen labels is presentation, and combining them is out of scope by decision — see
`docs/architecture.md` under "Scope boundaries". The index exists only in `--format report`; the JSON is unchanged.

The report prints, per repository: a header with the resolved window and its provenance; the readiness label above
**every** condition behind it, blocking, caution and clear alike; the cohort, the direct commits, the total merges
those two add up to, and who was excluded; the merge gate
with the instant it was read; the open security alerts by family and severity, or the reason a family could not be
read; the CODEOWNERS files found with their sizes and whether GitHub recognises the location, or `absent`, or the reason
the state is unavailable; the maintenance instants (last commit, last human commit, search bound) with the 6/12/24-month
window table and the reason beneath any `unknown`; the open pull-request counts with the instant they were fetched, or
the reason they are unavailable; one row per metric; the cohort's review events counted by state; and the `unreviewed-merge`
findings split into one column per observed size class. A `--metric` drill-down renders instead as that metric's
aggregate and the classification counts behind it.

It is presentation only and measures nothing of its own — every figure comes from a report the JSON already emits, so the
two renderings cannot disagree. Rates and distributions fill different columns of the behaviour table rather than being
forced into one, and an empty cohort prints `not applicable` rather than a zero nobody measured. The review breakdown
counts **every** review event in the cohort, eligible or not, including a pull-request author's own reviews and reviews
submitted after merge: it describes what reviewing looked like, and whether a review counted towards coverage is the
question `independent-review-coverage` already answers. Those same events are listed individually by
`--metric independent-review-coverage --include-identities`.

```
Index
-----
  Team        Repository      Readiness  Gate observed
  crime     cath-service          AMBER            yes
  opal   opal-common-lib          GREEN            yes
  sds     unreadable-svc  CANNOT_ASSESS             no

==================
hmcts/cath-service
==================
  Window      2026-05-09T00:00Z to 2026-08-08T00:00Z (91 days)
  Provenance  offline, 0 intervals fetched

Readiness: AMBER
----------------
  Blocking
    independent-review-coverage-below-target  [amber]
      independent-review-coverage is 81.2% (78 of 96), below the 90% target
...

Cohort
------
  Merged pull requests  182
  Reported in cohort    96
  Direct commits        0
  Total merges          96
  Excluded renovate     86

Security alerts, read 2026-08-02T09:30Z
---------------------------------------
  family           open  critical  high  medium  low
  dependabot          5         1     2       0    1
  code-scanning       -         -     -       -    -
  secret-scanning     2         0     0       0    0
  code-scanning not available: GitHub permission denied
  secret-scanning alerts carry no severity, so their severity columns are always zero

Open pull requests, read 2026-08-02T09:30Z
------------------------------------------
  Opened in window      41
  Closed without merge   6
  Currently open        18
  Stale open             7

Behaviour
---------
  Metric                        Rate    Sample   Unit   Median      p75      p90
  independent-review-coverage  81.2%  78 of 96      -        -        -        -
  approval-coverage            81.2%  78 of 96      -        -        -        -
  review-depth                 46.2%  36 of 78      -        -        -        -
  merge-cycle-time                 -        96  hours  129.318  330.804  647.656
  time-to-first-review             -        78  hours   44.453  119.873  272.379
  pull-request-size                -        96  lines    579.5     3333     7322
  checks-passing-at-merge      80.2%  77 of 96      -        -        -        -
  description-quality          72.5%  66 of 91      -        -        -        -
  traceability-reference       58.2%  53 of 91      -        -        -        -
```

### Trend: measuring periods after enablement

`evidence` answers one question at one instant — is this repository ready for agentic tooling now. `trend` answers the
second one: once a team **is** enabled, what do the same signals do over time. It reports, per repository, a **baseline**
window ending at the enablement instant and a series of whole **periods** after it, with each period's values and its
**delta** against that baseline.

```bash
uv run metrics trend --config metrics.example.yaml --offline
uv run metrics trend --config metrics.example.yaml --period-days 28 --periods 6 --format report
```

- `--period-days` (default `28`) sets the length of the baseline and of every period. Four whole weeks by default, so
  every period holds the same mix of weekdays and a series cannot move because one period caught an extra Monday.
- `--periods N` caps the series at the `N` periods **nearest the enablement instant**, dropping the most recent ones.
  The series is anchored at enablement and read against the baseline, so cutting the tail preserves the comparison the
  report exists to make. Without it, every whole period between enablement and the most recent UTC midnight is reported.
- `--offline` behaves exactly as it does for `evidence`: never contacts GitHub, and refuses any period the cache does not
  fully cover, including one overlapping the mutable edge. Without it, `trend` collects what the cache lacks, which costs
  one fetch per uncovered period on a cold cache and nothing on a warm one.

The **baseline** is the window of one period length ending at the enablement instant, `[enablement - period_days,
enablement)`. **Periods** are consecutive half-open windows of the same length starting at that instant. The trailing
**partial period is excluded**: a short period is not comparable with a full one, and including it would show every
series dipping at its right-hand edge for arithmetic reasons alone. Enablement dates come from the `enablement` block in
configuration; a repository without one is reported with that reason and no series, never anchored to a guess.

Each shape of measure moves in its own arithmetic, and every delta says which on the wire:

| measure | per-period value | delta against the baseline |
| --- | --- | --- |
| rate — `independent-review-coverage`, `approval-coverage`, `review-depth`, `checks-passing-at-merge`, `description-quality`, `traceability-reference` | the rate, with its numerator and denominator | **percentage points** (81.2% → 85.0% is +3.8 points), never a relative per cent of a per cent |
| distribution — `pull-request-size`, `merge-cycle-time`, `time-to-first-review` | the same fixed percentile the readiness assessment grades | percentage change of that percentile |
| count — merges, merged pull requests, direct commits, active contributors | the count | percentage change, with both counts always shown; a **zero baseline** reports the counts, no percentage, and the reason `baseline-zero`, never an infinity |

**Every behaviour metric `evidence` reports appears in a trend**, so a full series carries all nine of them beside the
four counts; the worked example below is abridged to keep it readable. `baseline-zero` is not confined to counts
either: any zero baseline under `percentage_change` — a zero-hour median cycle time, a pull request that moved no lines
— reports both measured values, no percentage, and that same reason. A rate is unaffected, moving in percentage points,
where a zero baseline subtracts perfectly well.

**`active-contributors` counts people, and is the only number in the series that does not count changes.** It is the
count of distinct accounts that authored a merge into the default branch over the window, by either route — a person
who merged six pull requests and pushed a commit is one contributor, and a login spelled two ways is one person. It
answers a question the merge counts cannot: three merges by one person and three merges by three people are different
weeks, and only this row tells them apart.

**Bot accounts are excluded from it**, both those GitHub types as `Bot` and those following the `[bot]` login
convention while typed as ordinary users. This is a narrower rule than `cohort.excluded_authors`, and deliberately so:
the cohort drops dependency automation and **keeps agent-authored merges**, because agent-authored work is work this
report covers. An agent is still not a person who became active, so its merges are counted and it is not. A period
where an agent raised every change therefore reports its merges beside zero contributors, which is a fact about how
that period's work was produced rather than a gap in the data. Reviewers are not counted either — this row measures who
put changes onto the default branch, and mixing reviewers in would describe a different population from the merges
printed beside it. Nothing is combined across repositories: a person active in two repositories is one contributor in
each series and is never added into an organisation total.

**Nothing in a trend is graded.** No value carries a threshold, a boundary or a colour, and the readiness assessment
reads nothing from any of it. A worsening series is a fact for a human conversation, not a verdict: every threshold is a
policy judgement that must be arguable, and no boundary for "how much should review coverage improve after enablement"
has an owner. The field names say what was measured — `baseline`, `periods`, `delta` — and deliberately never claim a
cause: team composition, seasonality and workload all move these numbers.

Suppression is per window, and the baseline and a period are treated differently on purpose. A period whose cohort is
below `assessment.minimum_merges` reports its cohort counts and suppresses its **metric values**, with the reason — a
rate over four merges is arithmetic rather than a pattern. Its **counts are still compared**, because the counts are how
a reader sees the period was thin. A baseline that is itself unobservable — too small a cohort, or history that does not
reach it — suppresses **every delta of every period** and says why once, while the series still reports in full: the
periods without deltas are still evidence, and a delta without a stated baseline is a lie.

`trend` computes each period through the same functions `evidence` computes one window with, so the two commands cannot
disagree about a number over the same window from the same cache; a test asserts that equality directly. Exit status
follows the same three values as the other commands, measured over the windows a run **resolved**: `0` when every
resolved window was observed, `3` when some were and some were not, `1` when none were. A repository with no enablement
date, or one enabled too recently for a whole period, resolves no window and counts as neither — otherwise a run would
report itself partial forever while most of the organisation is not yet enabled. A wholly failed run still writes its
report: the per-window reasons are the useful thing it produces.

Open pull-request state **cannot** appear in a trend. It is never cached and current by definition, so there is no
history to compare against; it stays a current-state block in `evidence` only.

**Security alerts appear as an observation series, not as periods.** Open alert counts are current state — GitHub cannot
say what was open last month — so every `collect` run **appends** one observation per repository per readable family
(the open count, its severity breakdown, and the instant it was observed) to the observation file beside the cache. A
trend reports the observations falling inside the span its windows cover, at the instants they were observed. Nothing is
interpolated onto a period boundary and nothing is zero-filled between two observations: the series is sampled, and a
stretch with no row is a stretch nobody looked at. A family GitHub refused or that is not enabled records nothing and
keeps reporting its reason under `evidence` — an unreadable family is not zero open alerts. No alert count is compared
against a baseline or given a delta.

This makes the collection cadence load-bearing. **Run `collect` on a schedule from enablement day onward** — a weekly
run is enough — because a missed run is a hole in the series for ever and nothing can fill it in afterwards.

`--format report` renders the same series as plain ASCII, built from the models the JSON emits, so the two cannot
disagree. It opens with an index — one row per configured repository giving the team, the repository, its enablement
instant or `not configured`, and how many of its own periods were observed. **Like the evidence index it is not a
roll-up**: there is no organisation figure, no per-team row and no average of deltas, which is the same roll-up
architecture.md forbids wearing a different hat. Repositories follow the one fixed team-then-repository order every other
command uses.

Per repository it prints the anchor and how the series is cut, the dates each window covers, then two tables: **metrics
in rows and periods in columns**, so one measure reads across the whole series on a single line and the tables grow
sideways as periods accumulate rather than forcing a reader to follow a metric down a column. The first holds each
window's value, the second each period's movement from the baseline beside the basis it moved on. A window that observed
nothing, and a value that was suppressed, print as `-` — never as a zero nobody measured — and the reason is printed
under the table it belongs to.

```
Index
-----
  Team        Repository            Enabled  Periods observed
  crime     cath-service  2026-07-18T00:00Z            2 of 3
  opal   opal-common-lib     not configured              none

==================
hmcts/cath-service
==================
  Enabled  2026-07-18T00:00Z
  Period   7 days
  Series   3 whole periods since enablement

Windows
-------
  Window               Starts               Ends
  Baseline  2026-07-11T00:00Z  2026-07-18T00:00Z
  P1        2026-07-18T00:00Z  2026-07-25T00:00Z
  P2        2026-07-25T00:00Z  2026-08-01T00:00Z
  P3        2026-08-01T00:00Z  2026-08-08T00:00Z
  P2: 1 merges into the default branch, below the minimum of 2, so metric values are suppressed
  P3: cached pull_request evidence does not cover 2026-08-01T00:00Z

Values
------
  Measure                         Unit  Baseline    P1  P2  P3
  merges                         count         2     3   1   -
  merged-pull-requests           count         2     2   1   -
  direct-commits                 count         0     1   0   -
  active-contributors            count         2     3   1   -
  independent-review-coverage  percent        50   100   -   -
  merge-cycle-time (median)      hours      12.5  6.25   -   -

Change from baseline
--------------------
  Measure                                  Basis   P1   P2  P3
  merges                       percentage_change  +50  -50   -
  merged-pull-requests         percentage_change    0  -50   -
  direct-commits               percentage_change    -    -   -
  active-contributors          percentage_change  +50  -50   -
  independent-review-coverage  percentage_points  +50    -   -
  merge-cycle-time (median)    percentage_change  -50    -   -
  P1 direct-commits: baseline-zero
  P2 direct-commits: baseline-zero

Security alert observations
---------------------------
  Family                    Observed  open  critical  high  medium  low
  dependabot       2026-07-14T09:00Z     6         0     4       0    2
  dependabot       2026-07-28T09:00Z     4         0     4       0    0
  secret-scanning  2026-07-14T09:00Z     1         0     0       0    0
  secret-scanning  2026-07-28T09:00Z     1         0     0       0    0
  observed when collect ran, so a gap between rows is a gap in the collection cadence
  secret-scanning alerts carry no severity, so their severity columns are always zero
```
