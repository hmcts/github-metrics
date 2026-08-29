# Plan: One log line per GitHub call, honest 403 classification, and run progress

## Overview
Collapse GitHub call logging to exactly one line per attempt, split GitHub's misleading "feature is
off" 403s away from genuine permission refusals so that a WARNING-level 403 always means a real
access problem, count every call by status and templated endpoint for an end-of-run summary, and log
per-repository progress through both collection phases.

## Context
- Files involved:
  - `src/metrics/github.py` — the single-line logging, the 403 split, the endpoint template, the call
    counter.
  - `src/metrics/inventory.py` — `open_alerts` and the two merge-gate collectors read
    `FEATURE_DISABLED` as an observation; `collect_inventory` logs progress.
  - `src/metrics/behaviour.py` — `collect_window` logs progress.
  - `src/metrics/cli.py` — `collect_evidence` emits the call summary.
  - `src/metrics/domain.py` — no change; `AvailabilityReason.FEATURE_DISABLED` already exists and is
    currently unused.
  - `tests/test_github.py`, `tests/test_inventory.py`, `tests/test_behaviour.py`, `tests/test_cli.py`,
    `docs/architecture.md`, `README.md`.
- Related patterns: `report_attempt` in `cli.py` for `(n/N)` progress logging; `CostMeter` for the
  per-repository calls and seconds; `failure_message` in `github.py` for the one piece of a response
  body this client is allowed to copy out.
- Dependencies: none beyond what the project already uses.

## Decisions fixed by this plan

**A 403 whose message says the feature is off is `FEATURE_DISABLED`, not `PERMISSION_DENIED`.**
This reverses the ruling recorded in `architecture.md` under "Exit status is three-valued" ("only the
response text tells them apart, which is too fragile a thing to grade a run on") and restated in
`open_alerts`'s docstring. The evidence for reversing it: across 1850 repositories every single 403
was a feature or plan message — 830 `Code Security must be enabled for this repository to use code
scanning`, 113 `Dependabot alerts are disabled for this repository`, 2 `Upgrade to GitHub Pro or make
this repository public to enable this feature` — and not one was a genuine refusal. Grading all of
them as refusals is what makes the run exit `3` and buries a real permission problem among 943 that
are not.

Matching is on a small set of phrases anchored on `for this repository`, so an organisation-level
access refusal can never match one:

| phrase (case-folded) |
|---|
| `is disabled for this repository` |
| `are disabled for this repository` |
| `must be enabled for this repository` |
| `is not enabled for this repository` |
| `upgrade to github pro` |

A 403 whose message is not recognised stays `PERMISSION_DENIED`. **The failure direction is
deliberate**: an unfamiliar disabled-message is reported as a refusal, which a human can then read in
the log and add to the list, and no real refusal is ever hidden by a phrase we guessed at. 404s are
not touched — an alert-endpoint 404 is already read as "not enabled" by `open_alerts`.

**A `FEATURE_DISABLED` outcome is an observation and never a `RepositoryInventoryIssue`.** It reports
on the block, records no failure, and does not move the exit status — exactly as a 404 from the same
endpoint does today. For the merge gate, a rules or protection endpoint answering "upgrade to GitHub
Pro" means branch protection cannot be configured on that repository at all, so it takes the same
path as a 404: `protected=False, rules_observed=True`, no failure.

**One line per attempt, at a level chosen by what came back.** Three of the four lines a failed call
emits today are ours. The pre-request line goes, and the response line and the refusal warning become
one line:

```
DEBUG    GitHub ok 200 GET https://api.github.com/repos/hmcts/batch-audio-transcription/rulesets/17187159 (1393 bytes)
DEBUG    GitHub disabled 403 GET https://api.github.com/repos/hmcts/jaia-client/code-scanning/alerts?state=open&per_page=100: Code Security must be enabled for this repository to use code scanning.
WARNING  GitHub refused 403 GET https://api.github.com/repos/hmcts/x/secret-scanning/alerts?state=open&per_page=100: Resource not accessible by personal access token
```

A disabled 403 logs at DEBUG, the same level as a 200, so at the default INFO level a `403` in the
log is always a genuine permission problem. urllib3's own line is left alone — it is not ours, and
`--logging info` silences it. Two exceptions are documented rather than engineered away: a retried
response (rate limit, 5xx) logs its existing retry WARNING instead of the outcome line, one per
attempt; and a GraphQL 200 carrying `errors` logs the DEBUG outcome line plus the existing errors
WARNING, because the errors are only visible after the call returns. At INFO and above it is always
exactly one line per call.

**The endpoint template is derived from the response URL**, so no call site changes and no parameter
is threaded anywhere. `/repos/{organization}/{repository}/…`, `/orgs/{organization}/…`, an all-digit
segment to `{id}`, the segment after `branches` to `{branch}`, and the values of `page`, `after`,
`before` and `cursor` to placeholders so pagination does not fragment a count. GraphQL collapses to
`POST https://api.github.com/graphql`.

## Call summary shape
Logged once by `collect` after the run, from a counter the client keeps beside the `issued` counter
it already has. Sorted by status descending, then `refused` before `disabled` before `ok`, then count
descending — as Task 5 records — so anything that failed is at the top:

```
GitHub calls issued for 1850 repositories:
    12  403  refused   GET https://api.github.com/repos/{organization}/{repository}/secret-scanning/alerts?state=open&per_page=100
   830  403  disabled  GET https://api.github.com/repos/{organization}/{repository}/code-scanning/alerts?state=open&per_page=100
   113  403  disabled  GET https://api.github.com/repos/{organization}/{repository}/dependabot/alerts?state=open&per_page=100
  1848  200  ok        GET https://api.github.com/repos/{organization}/{repository}
  1848  200  ok        POST https://api.github.com/graphql
```

`collect` only. `evidence` also collects and meets the same 403s; adding it there later is one more
call to the same function.

## Progress shape
The two phases are separate loops — all current state is collected before any window — so this is two
sequences of 1850 lines rather than one line per repository. Joining them was tried before and
rejected for the complexity it adds to the run, so the phases stay apart.

```
[   1/1850] cath-service  state ok (37 calls, 12.4s)
[   2/1850] rpx-shared-infrastructure  state ok, dependabot and code scanning not enabled (9 calls, 1.1s)
[   3/1850] cp-amp-terraform  state 1 unavailable: merge_gate permission denied (11 calls, 2.0s)
...
[   1/1850] cath-service  window fetched 2 intervals (14 calls, 8.1s)
[   2/1850] rpx-shared-infrastructure  window fully reused (0 calls, 0.0s)
```

## Out of scope
Nothing about caching or failure recovery changes. A refused call is already never cached — the
current-state phase re-reads everything each run, and `fill_cached_source` records coverage only for
an interval GitHub actually returned — so a permission granted later is picked up by the next run
without a switch. No `--rerun-failures` flag is added.

## Development Approach
- Code, then tests, per task; the suite enforces 100% coverage, so each task's tests land with it.
- Complete each task fully before moving to the next.

## Validation Commands
- `uv run poe check`
- `uv run poe cover`

## Implementation Steps

### Task 1: Split the misleading 403 and log one line per call
- [x] Add `feature_disabled_phrases` as a `ClassVar` frozenset on `GitHubClient` and a
      `reports_feature_disabled` static method testing GitHub's own message against it, case-folded
- [x] Add a `classify` method returning the `(message, reason)` pair for a failed response, answering
      `FEATURE_DISABLED` for a matching 403 and otherwise deferring to `http_failures`
- [x] Delete the pre-request `logging.debug` from `send` and `send_graphql`, and add a `description`
      parameter to `request` that `get` and `graphql` supply (the GraphQL one carries its variables,
      which is the only way a call names its repository)
- [x] Add `log_outcome`, emitting one line per response — DEBUG `GitHub ok`, DEBUG `GitHub disabled`,
      WARNING `GitHub refused` — moving the `GitHub response … bytes` and `GitHub refused …` lines
      into it and leaving `validate` to classify without logging
- [x] Write tests in `tests/test_github.py`: each phrase classifies as `FEATURE_DISABLED`; an
      unrecognised 403 body and an organisation-level refusal both stay `PERMISSION_DENIED`; a
      successful call, a disabled call and a refused call each emit exactly one record at the
      expected level via `caplog`
- [x] run `uv run poe check` — must pass before task 2

### Task 2: Count calls by status and templated endpoint
- [x] Add an `endpoint_template` module function normalising a response URL:
      `/repos/{organization}/{repository}`, `/orgs/{organization}`, all-digit segments to `{id}`, the
      segment after `branches` to `{branch}`, and `page`/`after`/`before`/`cursor` values to
      placeholders
- [x] Add a `Counter` to `GitHubClient.__init__` keyed by `(status, outcome, method, template)`,
      incremented in `log_outcome`, exposed through a `call_outcomes` property beside
      `requests_issued`
- [x] Write tests: a repository URL, an org ruleset URL, a branch-protection URL, a Link-header
      pagination URL and a GraphQL POST each template as expected; two calls to the same endpoint for
      different repositories count as one entry
- [x] run `uv run poe check` — must pass before task 3

### Task 3: Read a disabled feature as an observation, not a failure
- [x] In `open_alerts`, return the `FEATURE_DISABLED` outcome down the same branch as a 404 —
      `detail` of `"<family> is not enabled for this repository"`, `reason=None` — and rewrite the
      docstring paragraph that argues a 403 must stay a refusal
- [x] In `collect_merge_gate`, return unprotected-and-observed evidence with no failure when the
      rules endpoint answers `FEATURE_DISABLED`
- [x] In `collect_classic_merge_gate`, treat `FEATURE_DISABLED` from the protection endpoint the same
      way as `NOT_FOUND_OR_INACCESSIBLE`
- [x] Write tests in `tests/test_inventory.py`: a disabled alert family reports its reason on the
      block, records no failure, and leaves a run `COMPLETE`; a genuinely refused family still records
      one; a plan-limited merge gate reports unprotected with no failure
- [x] run `uv run poe check` — must pass before task 4

### Task 4: Log per-repository progress through both phases
- [x] Add a progress line to `collect_inventory`'s loop: position, total, repository name, `state ok`
      or the unavailable evidence kinds and reasons, any alert families reported as not enabled, and
      the meter's calls and elapsed seconds
- [x] Add a progress line to `collect_window`'s loop: position, total, repository name, whether the
      window was fetched, partially reused or fully reused with the interval count, and the meter's
      figures
- [x] Extract the shared `[   n/total] name  …` formatting so both phases and any later caller pad
      and order it identically
- [x] Write tests in `tests/test_inventory.py` and `tests/test_behaviour.py` asserting one INFO record
      per repository per phase, with the counter, name, outcome and cost in it
- [x] run `uv run poe check` — must pass before task 5

### Task 5: Emit the call summary at the end of a collection
- [x] Add a `log_call_summary` function to `cli.py` rendering the client's `call_outcomes` sorted by
      status descending then count descending, and call it from `collect_evidence` after the run,
      including when the run failed — sorted status descending, then `refused` before `disabled`
      before `ok`, then count descending, which is what the sample above shows: within one status,
      count alone would bury the twelve refused calls under 943 disabled ones
- [x] Write tests in `tests/test_cli.py`: the summary is logged once, orders failures above successes,
      groups two repositories' calls to one endpoint into one line, and distinguishes `refused` from
      `disabled` at the same status
- [x] run `uv run poe check` — must pass before task 6

### Task 6: Record the decisions and verify acceptance criteria
- [x] Amend `docs/architecture.md` — the 2026-08-15 ruling that a 403 stays a failure is superseded
      2026-08-29, with the 1850-repository evidence, the anchored phrase list, and the
      fail-toward-refusal direction
- [x] Add the one-line-per-call and log-level rules to `docs/architecture.md`, including the two
      documented exceptions and that urllib3's line is not ours
- [x] Update the `Run` section of `README.md` with the progress lines and the call summary
- [x] run `uv run poe check`
- [x] run `uv run poe cover`
