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
  stale_collection_days: 8
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
sonar_organization: hmcts
sonar_projects:
  example-service: example-service
```

Relative database paths are resolved from the directory containing the configuration file. **`database` names two
files**: the cache itself, and an observation history beside it — `metrics.sqlite3` gets `metrics-observations.sqlite3`.
The cache is disposable and deleting it costs only a refetch; the observation file holds the open security-alert counts
each `collect` run recorded, which cannot be refetched at all, and the SonarCloud project map, which can be refetched
but only at tens of minutes of rate-limited calls — so both are kept out of the file it is safe to
delete. `metrics prune` never touches it. Unknown keys and unsupported
configuration versions are rejected before any collection starts. `github_team_slugs` may optionally list GitHub teams
when the token can access that data; repository ownership remains authoritative.

`lookback.stale_collection_days` is **how old the last collection may be before the service and the CLI say so** — eight
days by default, because collection runs weekly and one missed run, not one missed day, is what the warning is for.
Past it, every page carries a notice naming the day the caches were collected through, and an offline `metrics evidence`
or `metrics trend --offline` run logs the same at WARNING.

`enablement` records **when agentic tooling was turned on for each repository**. It is configuration because it cannot be
observed: GitHub cannot be asked when a team was enabled. Each date parses exactly as a reporting window edge does — a
bare `2026-06-01` is UTC midnight, a naive `2026-06-01T09:30:00` is UTC, and an explicit offset is honoured — so one rule
governs every instant in the system. The JSON reports the instant as written, offset and all; `--format report` prints
the same instant converted to UTC, as it prints every other instant. Every name must be a configured repository; a date for an unconfigured one is a
configuration error naming the repository, because a typo would otherwise be indistinguishable from a forgotten entry. A
repository with no date is reported as having none rather than being anchored to a guess. That name check runs wherever
there is a cohort to check against, so it is skipped — not weakened — by a policy file loaded on its own for `map-sonar`
or `prune`, which owns no repository; every run that reports enablement loads a team file and is checked.

`sonar_organization` names the **SonarCloud** organisation whose projects are read. Omit it when it
matches `organization`, as it does at HMCTS; it exists only because the two names are allowed to differ, and a map
built against one SonarCloud organisation says nothing about another's project keys.

`sonar_projects` maps a configured repository to its SonarCloud project key, and is the **answer of last resort** for a
repository the stored map cannot settle — the only place a human decides a mapping, and the one rung of the resolution
ladder that beats every observation. Ambiguity is real rather than hypothetical: `rpx-xui-icp-api` declares `em-icp-api`
in its `sonar-project.properties`, and so does `em-icp-api` itself, so at most one of them owns the project and no
evidence in either repository says which. Every name must be a configured repository, for the same reason `enablement`
insists on it — a typo would otherwise read as "no project configured", indistinguishable from having forgotten the
entry — and no key may be empty, because an override silently meaning "unresolved" would read as a decision somebody
made. See [SonarCloud](#sonarcloud-quality-evidence) below for how a repository is mapped when no override is given.

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
`database`, and `teams` may come from whichever file states them, and the team file above does not load alone.
**The policy file does load alone, for the commands that read no repository cohort**: `map-sonar` resolves every project
the SonarCloud organisation lists and `prune` deletes stale cache rows, so neither needs a `teams:` section and neither
asks for one. The commands that do report the cohort — `collect`, `doctor`, `evidence`, `trend`, and `metrics-serve` — refuse without
it and say which command needed it. Everything the
schema does is unchanged — the same keys, the same defaults, the same rejection of an unknown key — because by the time
it runs there is only one document. A key given twice is resolved by YAML as it always was, with the last occurrence
winning, so a whole block is replaced rather than merged into. A relative `database` is resolved from the directory of
the **first** file given, which is the one the configuration starts in.

## Develop

```bash
uv sync
uv run poe check
```

## Authenticate

A run authenticates **either as a GitHub App installation or with a personal access token**. App auth is used when
`GH_APP_ID`, `GH_APP_INSTALLATION_ID` and a private key are all set; anything less falls back to `GH_TOKEN`, so a
half-set left over from an experiment reverts to the token rather than failing the run with a partial configuration
nobody meant to use. **What counts is whether a key was named, not whether it read back**: a key variable pointing at a
file that is missing or empty is a broken App configuration and stops the run saying so, rather than falling back to a
token whose smaller permissions would report branch protection and all three alert families as unavailable with nothing
explaining why. With neither the App set nor `GH_TOKEN`, every command that contacts GitHub stops before collecting
anything and names both options.

The key is read from **`GH_APP_PRIVATE_KEY_PATH` in preference to `GH_APP_PRIVATE_KEY`**. A path wins because it is the
safer of the two: a PEM in an environment variable is visible to every child process and to anything that dumps the
environment. The variable is accepted anyway, because CI secret stores commonly cannot hold newlines, and escaped `\n`
sequences in it are converted back to real ones.

**The two modes do not read the same things.** An App installation is granted its permissions by the organisation rather
than intersected with a user's, which is what makes branch protection, the three alert families and the GraphQL
pull-request searches readable at all — a user-intersected fine-grained token is refused on every one of them, and each
refusal is reported as availability rather than as a number. A personal access token stays supported because it is what
a developer already has in their shell, and a run that reads less is better than a run nobody can start. Every run that
contacts GitHub logs which mode it is in — the App and installation ids, which are identifiers rather than secrets, or
`a personal access token` — so a report full of refusals is one line away from its explanation.

The installation token is minted **once at startup**, so a wrong key, App id, or installation stops the run while
someone is still watching it rather than 1850 repositories in, and it is replaced five minutes before GitHub's stated
expiry, so a collection lasting longer than one token's hour never sends an expired one. Nothing about the key, the JWT,
or the token reaches the log at any level.

## Run

```bash
export GH_APP_ID=123456
export GH_APP_INSTALLATION_ID=78901234
export GH_APP_PRIVATE_KEY_PATH=~/.config/metrics/github-app.pem
uv run metrics doctor --config metrics.example.yaml
uv run metrics map-sonar --config metrics.example.yaml
uv run metrics collect --config metrics.example.yaml --from 2026-05-01 --to 2026-08-01
uv run metrics evidence --config metrics.example.yaml
uv run metrics evidence --config metrics.example.yaml --format report
uv run metrics evidence --config metrics.example.yaml --refresh
uv run metrics evidence --config metrics.example.yaml --repository example-service \
  --metric independent-review-coverage
uv run metrics trend --config metrics.example.yaml --offline
uv run metrics trend --config metrics.example.yaml --offline --format report
```

**`evidence` contacts nothing unless `--refresh` is given (breaking change, 2026-09-01).** It used to collect by
default and take `--offline` to stop it; that flag is gone, and the default is what `--offline` used to mean. Every
block a report carries now has a stored source — the last collection's open pull-request counts included — so the
default answer costs no GitHub call and needs no credential at all. `--refresh` is the single live call: it collects
whatever history the requested window lacks and observes open pull-request state as of now, overriding the stored
block. A run without it refuses a window the cache does not fully cover rather than reporting a short one.

The two collecting commands have disjoint jobs. **`collect` fills the cache and never reports a metric value.**
**`evidence` reports from the cache**, and under `--refresh` collects whatever the requested window lacks. Only
`evidence` computes metrics, so the two commands cannot disagree about a number. `trend` reports the same metrics as a
series of windows anchored to each repository's enablement date, through the same code — see
[Trend](#trend-measuring-periods-after-enablement) below. `trend` keeps its own `--offline` flag: it still collects by
default, because a series reaching back to enablement has periods no collection window ever covered.

`doctor` validates the configuration and verifies that the credential the run holds can read every configured
repository. It does not query GitHub's repository-team endpoint. That began as a permission limit — a fine-grained
personal access token is refused it — but it is not one under App auth, where an installation granted
`Administration: read` can read it. It stays unqueried because nothing needs the answer: repository ownership comes from
the configuration and is authoritative there, so asking GitHub would check a credential against data this tool does not
use.

`map-sonar` resolves which GitHub repository each SonarCloud project analyses and stores the answer, so that `collect`
can read a repository's quality state without having to work out whose it is. It is **run by hand, periodically — not on
every `collect`** — and the reason is a rate limit: nothing on either side records the link, so each project is resolved
by searching GitHub for the commit its latest analysis ran against, and that search is limited to **10 requests a minute
unauthenticated and 30 authenticated**. The `hmcts` organisation lists 289 projects, so a full rebuild is tens of
minutes of paced calls, which is not a cost worth paying on every collection to refresh a map that only changes when a
project is analysed against a different repository. See [SonarCloud](#sonarcloud-quality-evidence) below.

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

`costs` reports what the run spent per repository — `requests`, every call issued for it across both collection phases,
GitHub and SonarCloud alike, since one repository's collection is one unit of work — and
`elapsed_seconds`, measured on a monotonic clock — ordered **slowest first**, because the question it
answers is which repository dominates a run. A repository whose collection failed is reported too: the calls and the
seconds spent before the failure are the cost of the attempt. Unlike everything else `collect` emits, these figures
describe the run rather than the repository, so two runs over identical evidence will differ — the second reuses the
cache and is cheaper. They are deliberately absent from `evidence`, which is a reproducible contract, and they are not
stored: nothing in the cache remembers what a previous run cost.

**`collect` reports where it has reached, one line per repository per phase.** A collection visits every repository twice —
current state first, then the window — and at 1850 repositories each pass is long enough that a run printing nothing
until it finishes cannot be told apart from one that has hung. Each line carries the position, the total, the
repository, what that phase established, and the calls and seconds it cost, at `INFO`:

```
[   1/1850] cath-service  state ok (37 calls, 12.4s)
[   2/1850] rpx-shared-infrastructure  state ok, dependabot and code scanning not enabled (9 calls, 1.1s)
[   3/1850] cp-amp-terraform  state 1 unavailable: merge_gate permission denied (11 calls, 2.0s)
[   1/1850] cath-service  window fetched 2 intervals (14 calls, 8.1s)
[   2/1850] rpx-shared-infrastructure  window fully reused (0 calls, 0.0s)
```

The two sequences run one after the other, not interleaved: all current state is collected before any window.
`evidence --refresh` collects too and prints no progress lines: it fills its window one repository at a time through a
different path, and both these lines and the call summary below were left off it deliberately rather than missed. Plain
`evidence` reads the caches and contacts nothing, so there is no collection to report on.

**Every GitHub call logs exactly one line, at a level chosen by what came back** — `GitHub ok` and `GitHub disabled` at
`DEBUG`, `GitHub errors`, `GitHub refused` and `GitHub failed` at `WARNING`. `--logging` sets the level and defaults to
`info`, so a default run shows the progress lines, the call summary and any failure, and nothing per successful call;
`--logging debug` adds the line for every call. GitHub answers `403` both for a feature nobody enabled and for a token
that may not look, and only its message tells them apart, so a `403` explaining that the feature is off is logged beside
a `200` rather than as a warning. **At the default level, a `403` in the log is always a genuine permission problem.**

**The status alone never decides the word.** `refused` is reserved for the three statuses that are an access decision —
`401`, `403`, `404` — and every other error status is `failed`, because nobody refused a `502`; calling a bad gateway a
refusal put a transient in the summary under the word that means a permission to chase. In the other direction, a
GraphQL failure arrives as HTTP `200` with an `errors` array, so the body is read before the call is counted and it is
logged and counted as `errors` rather than as a success. One exception is left: a call being retried after a rate limit
or a `5xx` logs its retry warning instead, one per attempt.

**A GraphQL refusal is reported at the `403` it is, not at the `200` it arrived in**, marked `(equivalent)` so nobody
reads it as a status GitHub returned:

```
GitHub errors 403 (equivalent) POST https://api.github.com/graphql {"searchQuery": "repo:hmcts/cath-service is:pr is:merged merged:2026-05-25T00:00:00Z..2026-06-24T00:00:00Z"}: FORBIDDEN: Resource not accessible by personal access token (x76)
```

One line carrying the equivalent status, the variables naming the repository the call was about, and each kind of error
once with its count. **Grepping a log for `403` is how a permission problem is found**, and while these were reported as
the `200` they travelled in, the largest one on the estate was invisible to that search. The equivalence is the same
judgement the caller is handed — a body classified as a permission denial — so a failure that stops being read as a
refusal stops being reported as one. Every other GraphQL error keeps the response's own status: a repository that was
renamed is not a `403`, and a collection failure names no status worth searching for.

Response bodies are never logged — a secret-scanning alert record carries the detected credential itself — so a success
logs its byte count and a failure logs GitHub's own `message` and nothing else of the body. GraphQL errors are logged as
`TYPE: message`, **each kind once with its count**, because GitHub returns one error per node it will not answer for: a
single search returned `FORBIDDEN: Resource not accessible by personal access token` 76 times, and one run wrote that
sentence out 6,880 times, which buries the error that appears once. Only the `403` is split by message: a `404` is
`refused` at `WARNING` as it always was, including from the endpoints that read one as "not enabled" or "not protected",
because the client cannot tell those from a `404` on a repository that really is unreadable. At `DEBUG`, `--logging
debug` also turns on the `urllib3` connection line beside ours, which is not this tool's and is left alone; dropping
back to `info` silences it.

**A retried call is counted once, at the response it ended on.** A `502` retried twice and answered on the third attempt
is one `200 ok`; a `502` that never recovers is one `502 failed`, and its three round trips are three retry warnings
and one counted call. The summary reports what the run acted on, never a response it superseded.

**`collect` ends with a summary of what GitHub did with the whole run's calls**, counted by status, by outcome, and by
endpoint, with the organisation, repository, id, branch, commit and pagination values in each URL replaced by placeholders so
that one endpoint read for 1850 repositories is one line. Highest status first, and within one status refusals before
GraphQL errors before failures before disabled features before successes, then the largest count — within one status,
ordering by count alone would bury twelve refused calls under hundreds of disabled ones, and 181 failed GraphQL queries
under the 1977 that worked:

```
GitHub calls issued for 1850 repositories:
     2  502  failed    POST https://api.github.com/graphql
    12  403  refused   GET https://api.github.com/repos/{organization}/{repository}/secret-scanning/alerts?state=open&per_page=100
   181  403  errors    POST https://api.github.com/graphql
   830  403  disabled  GET https://api.github.com/repos/{organization}/{repository}/code-scanning/alerts?state=open&per_page=100
   113  403  disabled  GET https://api.github.com/repos/{organization}/{repository}/dependabot/alerts?state=open&per_page=100
  1848  200  ok        GET https://api.github.com/repos/{organization}/{repository}
  1977  200  ok        POST https://api.github.com/graphql
```

The `403 errors` line is the GraphQL refusals, counted at their equivalent status so they sit with the refusals they
are rather than under the successes they arrived among.

Not everything `collect` fetches is windowed. Pull-request, review, and direct-commit facts are historical and
accumulate in the cache.
Repository metadata, merge-gate state, open pull-request counts, CODEOWNERS presence, default-branch maintenance
instants and the SonarCloud
measures of the project a repository maps to are *current-state*
sources: GitHub cannot report what branch protection was three months ago, and the question is whether the gate protects
merges now. SonarCloud is the first current-state source that is not GitHub, and it obeys the same rules. They are therefore always fetched fresh and stored as one latest row per repository, replacing the previous
one. So `collect --from X --to Y` means: fetch windowed
sources for `[X, Y)`, and refresh current state as of now.

Open pull-request state is the one current-state source that carries a window with it. Two of its four counts — opened
in window and closed without merge — are bounded by the collection's own `[X, Y)`, so the window is stored beside them
as `starts_at`/`ends_at` and printed in the block rather than left to be read off the surrounding report. A stored
observation was measured over the window of the run that made it, which is not the window whatever reads it back covers.

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
usable in it. The exception is `evidence`, which refuses outright if the cache does not
fully cover the window for every selected repository — a silently short window is more dangerous than no answer. Under
`--refresh` it collects the missing intervals instead and refuses only what GitHub would not answer for.

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
intervals no collection has touched for N days, together with any facts they leave behind. Collection is what counts as
touching one: reporting from the cache deliberately leaves the interval's timestamp alone, so an interval the current
queries no longer ask for ages out even while the service is serving reports from its neighbours.

This release widened the pull-request query three times — a review's comment count and its summary body, for
`review-depth`, and the pull-request body, for `description-quality` and `traceability-reference` — so **every
previously cached pull-request interval is invalidated**. Run `collect` before relying on `evidence`, which otherwise refuses a window the cache no longer covers.
At fourteen repositories that first run is a full refetch rather than an edge refresh.

`evidence` reports the requested window from the caches, collecting whatever they lack only under `--refresh`. With only
`--config`, it evaluates every enabled practice rule for every configured repository and reports each repository's
findings under `behaviour`, beside the `merge_gate` block described below: the gate is the governance a repository
declares, `behaviour` is what its merges actually did. The initial `unreviewed-merge` rule groups authors whose merges
had no eligible independent human review and provides compact references supporting each finding. It
does not claim who clicked merge because that fact is not yet collected. Bot
authors are deliberately not excluded — an agent-authored merge with no human review is exactly the finding wanted —
though `excluded_logins` can omit specific service accounts. Each rule has independent enablement, severity, and
minimum-occurrence configuration. Repositories without cached evidence are listed under `unavailable` rather than
silently omitted.

Each repository block also names the `team` the configuration says owns it, and carries a `metrics` array holding the
nine neutral aggregates the readable report has always shown: one entry per metric with its identifier, its aggregate
observation and the classification counts behind it — the same figures a `--metric` drill-down emits over the same
window, computed once and shared, so the two renderings cannot disagree. The team is accounting, not a roll-up: it says
who owns a repository, and no team label, score or ordering follows from it.

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
when the pull request was opened. Like the merge gate, it is **current state**: `collect` observes the four counts and
stores them latest-only, together with the collection window the two windowed counts were measured over, because a
count over a window nobody remembers cannot be read. A repository whose counts GitHub refused records that as
unavailable evidence and keeps every other block it did report — unavailable data must never become zero. The block
carries `fetched_at`, the instant it was read, and `starts_at`/`ends_at`, the window the two windowed counts cover,
beside the four counts. `evidence` serves it from that stored row; `evidence --refresh` observes it live instead and
carries the reporting window it just measured. A row stored before this state was collected reports that as its reason
rather than as four zeros. Like the merge gate, it is display only and appears in the practice report, not in a
`--metric` drill-down.

Each repository also carries a `security` block: the open security alerts of all three families — `dependabot`,
`code_scanning` and `secret_scanning` — each with an `open` total and a `by_severity` breakdown. Like the merge gate it
is **current state**, stored latest-only by `collect` and served with the `fetched_at` instant it was read, so
`evidence` shows the last collection's answer rather than refusing. GitHub cannot say what was open in May, and the
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
  not use the feature. **A `403` whose message says the feature is off is read the same way**, as of 2026-08-29: GitHub
  returns `403` both for a token without the scope and for a feature or plan that does not cover the repository, and
  across 1850 hmcts repositories every `403` was the second kind — `Code Security must be enabled for this repository to
  use code scanning`, `Dependabot alerts are disabled for this repository`, `Upgrade to GitHub Pro …`. Only a short list
  of phrases anchored on `for this repository` counts, so an organisation-level refusal can never match one, and a `403`
  whose message is not recognised stays a refusal: an unfamiliar "disabled" message is reported as a permission problem
  a human can read in the log, rather than a real refusal being hidden by a phrase this tool guessed at.

Collection costs one call per family plus one more per additional hundred open alerts. Measured against
hmcts/cath-service on 2026-08-14: three calls, against a whole-repository GitHub collection of roughly fourteen. That
denominator predates SonarCloud, which adds its own calls on top — see [SonarCloud](#sonarcloud-quality-evidence). Like the merge
gate the block is display only — nothing scores it, because no open-alert threshold has an owner — and it appears in the
practice report rather than in a `--metric` drill-down.

Each repository also carries a `codeowners` and a `maintenance` block, answering the gov.uk minimum standard for
publicly accessible systems: is there a named owner, and is the repository maintained. Both are **current state**,
collected by `collect` in one bundled GraphQL query per repository, stored latest-only beside the merge gate, and
served with the `fetched_at` instant they were read, so `evidence` shows the last collection's answer. Both are
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

### SonarCloud quality evidence

Each repository also carries a `sonar` block: the quality state of the SonarCloud project that repository maps to. It
reports the project key, **how the project was resolved**, when it was last analysed, the quality gate level with
**every condition behind it** — metric, comparator, threshold, actual value and level — and the measures beside them:
coverage, duplicated lines, lines of code, total violations, the reliability, maintainability and security issue counts,
security hotspots, and the four ratings as `A`–`E` letters. Like the merge gate it is **current state**, stored
latest-only by `collect` and served with the `fetched_at` instant it was read, so `evidence` shows the last
collection's answer. SonarCloud is read anonymously; setting `SONAR_TOKEN` or `SONARCLOUD_TOKEN` widens the project
listing to private projects.

It is **report-only and ungraded**. A failing quality gate imposes no readiness ceiling and carries no label, per the
standing rule that a signal becoming visible is not a reason to grade it. The conditions are printed in full anyway, as
the merge gate prints its rules: "the gate failed" is an assertion, and `coverage < 80, actual 62.1` is the evidence for
it. An absent measure prints as a dash and never as zero, and every row is printed even when its measure is missing, so
the block says which measurements were asked for as well as which came back.

**Which project a repository maps to is the hard part**, because nothing on either side records it. It is resolved in
this order, and the rung that answered is reported as `Resolved by`, because a wrong mapping is only diagnosable if the
report says which one produced it:

| `Resolved by` | how |
| --- | --- |
| `configured` | the `sonar_projects` override — a human's instruction, which beats every observation |
| `declared_confirmed_by_map` | the repository's `sonar-project.properties` key, and the stored map agrees it is this repository's |
| `declared_confirmed_by_commit` | the declared key, confirmed because this repository holds the commit that project was last analysed against |
| `stored_map` | the map `map-sonar` built, resolving the project through its latest analysis commit |

A declared key is a **hypothesis, never an answer**, and is always tested. Measured across 3,372 hmcts repositories: 240
declare a key, 123 of those name no project SonarCloud lists, and 69 collide — `rpe-expressjs-template` is declared by 14
repositories scaffolded from that template. So a declaration the map attributes to a *different* repository is treated as
refuted, and resolution falls through to the map's own answer. Matching repositories to projects **by name** was measured
too, and rejected: it was wrong for 6 of the 70 repositories where it answered, and three of those six preferred an
abandoned project to the live one, because SonarCloud has no rename and `rpx-xui-webapp_2` is the project still being
analysed. `docs/architecture.md` records the full measurement.

Two projects may legitimately map to one repository — those same duplicate pairs — so the repository's project is the
candidate with the **most recent analysis**, which picks the live project in every measured pair, and `map-sonar` warns
with both keys so a human can see there was a choice.

**A repository no project is mapped to is an observation, not a failure.** The block says so, no failure is recorded, and
the run exits `0` — most of the organisation is in that state, and exiting `3` for it would empty the status of meaning.
That is deliberately distinct from "SonarCloud measures were not collected when repository state was stored", which is a
gap `collect` closes. A SonarCloud or GitHub call that actually **failed** while resolving or measuring records one
failure against the `sonar` evidence kind and exits `3`. SonarCloud calls are counted in the `costs` table beside the
GitHub ones, so a repository's collection stays one measurable figure.

`map-sonar` stores every row as it resolves it, so a run stopped by a rate limit keeps everything it already paid for,
and skips any project that has not been analysed since its stored row was written — nothing has happened that could
change the answer, so there is nothing to learn and no call to spend. It summarises what it learned: projects listed,
resolved, unchanged, never analysed, unresolvable, failed, and repositories now mapped. It exits `0` when every project
was answered, `3` when at least one call failed or a rate limit stopped the run, and `1` when the run produced nothing
usable: the organisation lists nothing, the listing itself was refused, the durable map could not be read or written, or
no project was answered at all. A never-analysed or unresolvable project **is** answered — it is stored with its reason,
so the next run does not spend the scarcest quota in the system asking the same hopeless question again.

Two limitations follow from all of this rather than being defects. A repository whose project has not been analysed
recently enough for SonarCloud to still hold the analysis, and which carries no properties file, cannot be mapped until
`map-sonar` runs again after an analysis; the configured override is the escape hatch. And `map-sonar` must be run
periodically, because a project that moves to a different repository keeps reporting the old one until it does.

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

Every condition also carries `informational`, which is `true` where the policy **reported it without judging it** — the
three merge-gate rules described below that bear on the label in neither state, and the `sufficient-merges` precondition
that says the window held enough to grade rather than that anything went well. It is a rendering aid, so a reader can
tell a check that was satisfied from one nobody grades: `metrics evidence --format report` prints no extra line for it,
the dashboard sets those rows in no colour, and the flag moves no label and no section. Every other condition carries it
as `false`.

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

Reporting from the caches — `evidence` without `--refresh`, and `trend --offline` — contacts GitHub not at all, and
refuses outright if the cache does not fully cover the requested window rather
than emitting a short one — for **either** source, naming the one it could not answer for: a window whose pull requests
are cached but whose direct commits were never collected would understate every governance denominator by exactly the
bypasses it could not see — a silently truncated window is more dangerous than no answer. Because the mutable edge is
cached without recording coverage, a cached report also refuses any window overlapping the last `mutable_hours`. That is
intended: it means settled, cached evidence or nothing.

Which is why a cached report anchors its window where the caches end rather than where today does. A collection records
coverage only up to the stable edge of the run that wrote it, so the morning after a run a window ending at today's
midnight is short of coverage by that edge alone and every repository reports as not reported. Wherever the end is not
named outright — no options at all, `--days` on its own, `--from` on its own — `evidence` without `--refresh` and
`trend --offline` therefore end at the most recent midnight the caches actually cover to: the last whole run's edge,
taking the earlier of the two sources, and never later than today's midnight. The same command run the day after a
collection prints the figures that collection supports, rather than nothing. An explicit `--to` is still honoured
exactly as given. A repository off that edge is still reported as unavailable at that window: the anchor is the instant
most of the estate is covered to, so neither one repository missed for a month nor one repository refreshed on its own
this morning moves the whole estate's window. The run says where it landed — an INFO line naming the collected
instant whenever the anchor is behind today's midnight, and a WARNING when the last collection is older than
`lookback.stale_collection_days`. `collect` and `evidence --refresh` are unaffected and still anchor at now: they are
the runs that reach GitHub, and an anchor behind now would ask them to collect less than they can.

Merge-gate collection first uses effective repository rulesets. If none apply, it requests detailed classic branch
protection and normalises pull-request reviews, status checks, deletion restrictions, force-push restrictions, and the
linear-history requirement into the same evidence fields. Classic protection has a fixed shape, so it can produce
neither `restricts_branch_names` nor an `unmodelled_rules` entry: a classic repository reports the boolean as False,
which is accurate rather than a gap. If GitHub denies access to those classic details, collection falls back to the branch metadata
protection indicator. When that indicator confirms protection, the basic evidence is retained and `failures` records the
permission-limited merge-gate detail, making the run partial. An unprotected indicator does not create a failure.

Where GitHub answers `403` because the repository's plan carries no branch protection at all — `Upgrade to GitHub Pro or
make this repository public to enable this feature` — the gate is reported as **observed and unprotected**, with no
failure and no effect on the exit status: there is no gate for a token to have been refused a sight of. That is the same
reading a `404` from the classic protection endpoint gets. A rulesets `403` is not taken as the answer on its own, since
a repository can be gated by classic protection alone, so classic protection is still asked and answers the same `403`.
Being an observation rather than a blind spot, readiness grades on it: such a repository is vetoed `red` for an
unprotected default branch rather than reported as `cannot_assess`.

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

The report opens with a **repository summary**: how many repositories carry each readiness label, and each label's share
of the whole population the run covers. At fourteen repositories the index below it can be read as a shape; across hmcts
it is 1,863 rows and cannot, so the counts come first. All four labels are printed, zero included, so two runs diff line
for line and a label nobody carries reads as an observation rather than an omission. A repository the policy left
unjudged adds a `not assessed` row and one whose collection failed an `unavailable` row, matching the two non-labels the
index's own column prints; neither is shown where nothing carries it. Add the index's rows up and you arrive at these
figures. Each percentage is over every repository the report covers — the ones that reported and the ones that could
not — so the counts account for the whole population. Each share is rounded to a tenth on its own, so the printed column
can read 99.9 or 100.1 rather than exactly a hundred; the counts are what reconcile against the index. A run covering no
repositories prints dashes rather than a `0%` it never divided, and a count too small to round to a tenth prints
`<0.1%`, so a row somebody is in never reads like the zero rows beside it.

```
Repository Summary
------------------
  GREEN           18   1.2%
  AMBER          506  34.1%
  RED            612  41.2%
  CANNOT_ASSESS  349  23.5%
```

Below it comes the **index**: one line per configured repository giving the owning team, the repository, its
readiness label, and whether the merge gate's rules could be read at all. It is a table of contents for a report that at
fourteen repositories is roughly fourteen times longer than it was at one, and it answers the three questions a reader
opens such a report with — which repositories are assessable, which are RED, and which need reading in full — before any
scrolling. A repository whose collection failed gets a row too, reading `unavailable`, so the index covers the
population rather than the part of it that answered. Every cell comes from the same models the block below it renders.

**Neither block is a roll-up.** There is no combined label, no per-team verdict and no score anywhere: the summary counts
each label separately and combines none of them, and no index row carries a figure. `teams` in the configuration says who
owns a repository, not how several repositories reduce to one label; listing and counting labels is presentation, and
combining them is out of scope by decision — see `docs/architecture.md` under "Scope boundaries", which records the
counts as a reversal, dated 2026-08-31, of the ruling that kept them out of the index. Both blocks exist only in
`--format report`; the JSON is unchanged.

Above the actor list, mirroring where the repository summary sits above the index, comes the **actor summary**: how many
people carry each combination of readiness labels, and each count's share of the people that section reports. See
[Actors](#actors-the-same-evidence-read-person-by-person) for the combinations, the three named groups and what their
subtotals do and do not claim.

The report prints, per repository: a header with the resolved window and its provenance; the readiness label above
**every** condition behind it, blocking, caution and clear alike; the cohort, the direct commits, the total merges
those two add up to, and who was excluded; the merge gate
with the instant it was read; the open security alerts by family and severity, or the reason a family could not be
read; the SonarCloud project with how it was resolved, its analysis instant, its quality gate and every condition behind
it, and its measures and ratings — or the reason no project is mapped; the CODEOWNERS files found with their sizes and
whether GitHub recognises the location, or `absent`, or the reason
the state is unavailable; the maintenance instants (last commit, last human commit, search bound) with the 6/12/24-month
window table and the reason beneath any `unknown`; the open pull-request counts with the instant they were read and the
window two of them cover, or the reason they are unavailable; one row per metric; the cohort's review events counted by state; and the `unreviewed-merge`
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
Repository Summary
------------------
  GREEN          1  33.3%
  AMBER          1  33.3%
  RED            0     0%
  CANNOT_ASSESS  1  33.3%

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

SonarCloud, read 2026-08-02T09:30Z
----------------------------------
  Project       rpx-xui-webapp_2
  Resolved by   stored_map
  Analysed      2026-08-01T04:12Z
  Quality gate  OK
  Metric        Comparator  Threshold  Actual  Level
  new_coverage          LT         80    91.4     OK
  Coverage                74.2%
  Duplicated lines        1.5%
  Lines of code           1234567
  Violations              318
  Reliability issues      12
  Maintainability issues  280
  Security issues         3
  Security hotspots       7
  Reliability rating      C
  Maintainability rating  A
  Security rating         B
  Security review rating  E

Open pull requests, read 2026-08-02T09:30Z
------------------------------------------
  Opened and closed over  2026-05-09T00:00Z to 2026-08-08T00:00Z
  Opened in window        41
  Closed without merge    6
  Currently open          18
  Stale open              7

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

Actor Summary
-------------
  Enable               1  33.3%
    GREEN              1  33.3%
    GREEN, AMBER       0     0%
  Review               2  66.7%
    AMBER              2  66.7%
    GREEN, AMBER, RED  0     0%
    GREEN, RED         0     0%
  Blocked              0     0%
    AMBER, RED         0     0%
    RED                0     0%

Actors (cannot_assess repositories excluded)
--------------------------------------------
  Enable
    carol  GREEN
  Review
    alice  AMBER
    bob    AMBER
```

### Actors: the same evidence read person by person

The report closes with an **actors** section, and `actors` is a field of the JSON whatever `--format` is chosen. The
per-repository blocks answer "how is this repository doing"; this answers "what is this person working in", by listing
one line per person who authored a merge in the reported repositories with the readiness label of every repository they
contributed to. It is the default practice report only: a `--metric` drill-down is unchanged, and a repository reported
in `unavailable` contributes no actors, because no facts were read for it.

```json
"actors": [
  {"actor_login": "alice",
   "repositories": [
     {"readiness": "red", "repository": "project-x", "contributions": 41, "blocking": 12,
      "metrics": [{"metric": "independent-review-coverage",
                   "summary": {"status": "observed", "numerator": 12, "denominator": 41},
                   "classifications": {"included": 12, "no-review-events": 29}}]},
     {"readiness": "green", "repository": "project-z", "contributions": 3, "blocking": 0,
      "metrics": [{"metric": "independent-review-coverage",
                   "summary": {"status": "observed", "numerator": 3, "denominator": 3},
                   "classifications": {"included": 3}}]}]}
]
```

Each row's `metrics` holds the same nine aggregates the repository block carries — abridged to the first of them above —
measured over **that person's merges in that repository** and nothing else. They are observations listed per repository, never combined across the
repositories somebody works in: averaging a coverage rate over three merges with one over three hundred would weight the
two equally and describe neither. Nobody is ordered or compared by any of them, and a bot gets no row at all, so it gets
no summaries either.

`contributions` counts the merges that person authored in the window, by either route, after the cohort's author
exclusions. `blocking` sums the `occurrences` behind their practice findings in that repository, so twelve unreviewed
merges count as twelve rather than as one finding; it reports what a rule already found, gathered per repository.
`readiness` is the repository's assessed label, and is **absent** when the readiness policy is disabled — there is then
no label to carry, and inventing one would grade a repository the report deliberately left ungraded. Actors are
alphabetical by case-folded login — in the JSON, and within each group of the rendered list — and each actor's
repositories are ordered by contributions, largest first, ties
broken by repository name. Logins are matched case-insensitively, because a GitHub login is unique
case-insensitively — `Alice` and `alice` are one person — and are spelled as the repository they contributed most to
spells them.

**The rendered section leaves out `cannot_assess` repositories**, by instruction on 2026-08-31; the heading says so. The
label is not a grade — it means a half of the question could not be read, most often a merge gate a non-administrator
cannot see — so beside a person's name it reports somebody else's missing permission, and at hmcts scale it reports that
about most repositories most people work in, crowding out the labels the section exists to show. A person left with no
other repository gets no line, because a name with no label beside it reads as a finding about them. The exclusion is
rendering only: those repositories are unchanged in the JSON's `actors`, in the index, and in the body of the report.
Where it empties the whole section, the section says so in its own words — `none: every repository the reported people
contributed to could not be assessed` — which is deliberately not the wording used when nobody authored a merge at all
(`none: no person authored a merge in the reported repositories`), so the two causes are never read as one.

The rendered line abridges that list. **Each label is tallied once**, wherever the repositories carrying it sit in the
list, so no line reports the same label twice. Groups are ordered by the contributions behind them, largest first, ties
broken by the policy's severity precedence — red, cannot assess, amber, green, then not assessed — so a person's
weightiest label leads and neither a heavy green nor an ordering accident can put a red anywhere else. Repositories
within a group keep the contribution order. `x N` is dropped when N is 1, a repository with no assessment reads
`NOT ASSESSED`, and repository names appear only where an actor's labels differ: six red repositories render `RED x 6`,
because the names would draw no distinction. Where the labels do differ the names are the point, since which of them is
the red one is the next thing asked.

**The rendered lines are grouped**, since 2026-09-01, under the action the combination of labels a person carries puts
them under. `Enable` covers `GREEN` and `GREEN, AMBER`; `Review` covers `AMBER`, `GREEN, AMBER, RED` and `GREEN, RED`;
`Blocked` covers `AMBER, RED` and `RED`. The table is a ruling about what to do next, not a property of the labels —
`GREEN, AMBER` enables while `AMBER` alone is reviewed, which no severity ordering yields — so it is written out rather
than derived. Groups print in that order with `Ungrouped` last, alphabetical login order is kept within each group, and a
group nobody is in is left out rather than printed as a heading with nothing under it. Only a combination carrying
`NOT ASSESSED` can reach `Ungrouped`: all seven combinations of the three graded labels are covered above, and
`cannot_assess` is already excluded.

Multiplicity is not part of a combination, which is what makes the set of them finite: `RED x 6` and `RED` say the same
thing about a person — everything they work in is red — so they group together, and `RED x 2, GREEN` counts as
`GREEN, RED`.

The **actor summary** above the list counts the people behind each of those combinations, with a subtotal per group. It
prints the seven listed rows at every run, zero included, so two runs diff line for line, and prints `Ungrouped` only
where something reaches it. Every percentage is over the people this section reports — not everyone the report covers,
since somebody left with no repository by the `cannot_assess` exclusion gets no line and is counted in nothing here. Each
share is rounded to a tenth on its own, so a group's share can differ by a tenth or two from its rows' shares added up;
the counts are what reconcile, and a subtotal is always the sum of the counts printed under it. Those
are the only two blocks carrying percentages: the review breakdown, the cohort and the metric classification counts each
print their own denominator already.

**Bot accounts get no line**, both those GitHub types as `Bot` and those following the `[bot]` login convention while
typed as ordinary users — the same test `active-contributors` applies, which drops more accounts than
`cohort.excluded_authors` does. Their merges **stay in the cohort** and in every rate measured over it, because
agent-authored work is work this report covers; an agent is simply not a person to review. A repository where an agent
raised every change therefore reports its merges with nobody named against them.

**There is still no per-person verdict.** A person contributing to a red repository and a green one has no single
readiness: the line lists both labels, and averaging or reducing them would invent a verdict this tool does not make.
The group name above a line names what to do next about a pair of labels, not the person carrying them, and there is no
worst-label summary, no score, no per-team figure and no ordering of people by what they carry. The count of people and
the group subtotals were once excluded here as roll-ups; both were admitted on 2026-09-01 at the user's instruction, and
`docs/architecture.md` under "Scope boundaries" records them as dated reversals along with what stays out. If the
grouping is ever revised, `render.actor_combination` and the `render.ACTOR_GROUPS` table are the two places to change.

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
- `--offline` never contacts GitHub, and refuses any period the cache does not fully cover, including one overlapping
  the mutable edge — which is what `evidence` now does with no flag at all. `trend` keeps the flag because it still
  collects by default: a series anchored to enablement reaches back over periods no collection window ever covered, so
  the useful default is the one that fills them, at one fetch per uncovered period on a cold cache and nothing on a
  warm one.

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

Open pull-request state **cannot** appear in a trend. It is stored latest-only — every `collect` run replaces the row —
so there is no history to compare against; it stays a current-state block in `evidence` only.

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

## Dashboard

The same evidence, read in a browser instead of a terminal. Two processes: `metrics-serve` holds the report and
answers JSON, and a Next.js app in `ui/` renders it. Neither contacts GitHub — the service reads the two SQLite
files a `collect` run wrote and nothing else — so the whole loop runs without a credential.

```bash
uv run metrics collect --config metrics.example.yaml --from 2026-05-01 --to 2026-09-01
uv sync --extra service
uv run metrics-serve --config metrics.example.yaml
npm --prefix ui install
API_URL=http://localhost:8000 npm --prefix ui run dev
```

The UI is the one part of this repository that needs Node — 18.17 or newer, which is what Next.js 14 requires. The
Python side needs none of it, and a host that only ever collects can ignore `ui/` entirely.

`collect` is the only step that needs `GH_TOKEN` or the App variables, and it is the step that decides what the
dashboard can show: a span the caches do not cover is reported as not reported, never as a smaller number. Since
2026-09-01 it also stores each repository's open pull-request counts, so the dashboard can show them without a live
call — that is why `collect` has to run before the service, not merely at some point in the past.

FastAPI and uvicorn are an optional extra, so a collection host that will never serve anything installs neither.
`uv sync --extra service` adds them. The `metrics-serve` script itself is installed either way; without the extra it
starts and stops on an import error naming `uvicorn`, the first of the two it imports.

`metrics-serve` takes `--config` (repeatable, as every command's does), `--host` (default `127.0.0.1`), `--port`
(default `8000`), `--logging`, and `--max-bundle-age` (default `3600` seconds, and at least `1` — a zero or negative
age would make every bundle born stale and rebuild the whole cohort on every request). It refuses a configuration with
no `teams:` section, as every cohort command does, and refuses one whose `lookback.maximum_days` is shorter than the
shortest span it offers, rather than starting with nothing to serve. It builds one whole report per window span — 1, 4,
8, 12 and 26 weeks, filtered against the configuration's `lookback.maximum_days` — and holds it until either the age
limit passes or one of the cache files changes size or mtime, which is how a collection landing at lunchtime reaches a
page somebody is already reading. The endpoints are `/healthz`, `/windows`, `/overview`, `/repositories`,
`/repositories/{repository}`, `/repositories/{repository}/trend`, `/actors`, `/actors/{login}`, `/teams` and
`/teams/{team}`; every data endpoint takes `?weeks=` and refuses a span off the list rather than clamping it. A request
naming no span gets four weeks, or the longest span the configuration allows below that.

Every one of those spans ends where the caches end, not at today's midnight — the same anchor `metrics evidence` uses
offline, and for the same reason: a service reading a cache written on Monday would otherwise report the whole estate
as not reported from Tuesday onwards. So a span served the day after a collection shows the figures that collection
supports, and a repository off that edge is reported as unavailable at that window rather than pulling everyone else's
window back to meet it or dragging it forward. The instant a window is anchored to is published as `collected_through` on
`/windows` and on `/overview`, and `/windows` also carries `collection_stale`, set when the last collection is older
than `lookback.stale_collection_days`. The UI prints `Collected through 2026-09-01` in the overview header beside the
span, and shows an amber warning bar on every page — overview, repository, team and actor — while the collection is
stale, because a page that quietly reports a fortnight-old window reads as this morning's.

The trend endpoint is the one exception to `?weeks=`: a series is cut into periods from the repository's own
enablement instant and has no reporting window to select, so it takes `period_days` (default `28`) and an optional
`periods` instead, and each cut is held beside the window bundles under the same rebuild rules. `period_days` runs 1 to
365 and `periods` 1 to 26. **A cut is refused rather than truncated**, and that applies to the series a request
*resolves* to as well as to the number it names: leaving `periods` out asks for every whole period since enablement, so
a repository enabled long enough ago is refused with the count it asked for until the caller names a cut. A series
quietly stopped at 26 periods would be read as the whole history since enablement, which is the reading the refusal
exists to prevent. Because a caller must name a cut, `/windows` publishes the one this service will answer as
`trend_periods` beside the spans it offers, and the UI asks for that rather than keeping a copy of the bound that could
drift from it — a series that comes back holding the whole cut is labelled on the page as the first periods since
enablement rather than as all of them. It reports the same `RepositoryTrend` the `metrics trend` contract carries, offline: a period the
caches do not cover reports that reason and no counts.

The UI fetches server-side, so the service needs no CORS headers and no browser ever calls it directly. `API_URL`
tells the Next.js server where it is, defaulting to `http://localhost:8000`. `npm --prefix ui run build` then
`npm --prefix ui start` serves it for real; `npm --prefix ui run check` is the whole UI gate — lint, types, tests,
build — in one step, as `uv run poe check` is for the Python. Its build step needs a case-sensitive filesystem, so on
a case-insensitive mount the three commands that judge a change are `lint`, `typecheck` and `test` run separately. See
[`ui/README.md`](ui/README.md) for that, the pages, the design tokens, and the guardrails the UI keeps.

### In containers

`docker-compose.yml` runs the same two processes, and only those two:

```bash
uv run metrics collect --config config.yml --from 2026-05-01 --to 2026-09-01
CONFIG=config.yml docker compose up --build
```

Collection stays on the host, because it is the step that needs a credential and the one that has to be scheduled.
`CONFIG` names a file in this directory — `config.yml` by default — mounted read-only at `/app/config.yml`, and
`./.metrics` is mounted read-write, so the configuration's `database` must resolve inside `.metrics` or the service
starts with nothing to serve.

Only the UI is published, on `http://localhost` — port 80 on the host, mapped to the container's 3000; the API
answers at `http://api:8000` on the compose network
alone, which is the same server-side-only arrangement the local loop has. The UI waits for the API's `/healthz` to
answer, and the API's healthcheck allows a minute before it starts failing, so the first `up` is slower than the ones
after it. The API image installs the `service` extra and nothing else, and its build context is an allowlist
(`.dockerignore`): `pyproject.toml`, `uv.lock`, `README.md` and `src/`, so a new runtime file outside `src/` has to be
added there or it will be missing from the image. The UI image is built from `ui/`, which is why `ui/next.config.mjs`
sets `output: 'standalone'`.
