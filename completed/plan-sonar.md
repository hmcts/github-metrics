# Plan: SonarCloud project mapping and quality evidence

## Overview

Resolve which SonarCloud project belongs to each configured repository, and report that project's
quality state — gate pass/fail with its conditions, coverage, duplication, open issues and the four
ratings — as a current-state block beside the merge gate. Resolution is by configured override
first, then the repository's own `sonar-project.properties` cross-checked against observed evidence,
then a stored `sonar → github` map built by a separate `map-sonar` command that resolves each project
through the commit SHA of its latest analysis.

## Context

- Files involved:
  - `src/metrics/config.py` — `sonar_organization`, per-repository `sonar_projects` override
  - `src/metrics/domain.py` — mapping, measures, gate-condition and report models; `EvidenceKind`
  - `src/metrics/sonar.py` — NEW: the SonarCloud client and `sonar_to_github`
  - `src/metrics/storage.py` — the `sonar_project_map` table in the observations database
  - `src/metrics/inventory.py` — read the properties file in the existing bundled query; collect measures
  - `src/metrics/evidence.py` — project the stored row into a report block
  - `src/metrics/render.py` — the report block
  - `src/metrics/cli.py` — the `map-sonar` command and the evidence wiring
  - `README.md`, `docs/architecture.md` — the ruling and the user-facing description
- Related patterns:
  - **Evidence-or-reason** — every report block carries either its evidence or the reason there is
    none, enforced by a `model_validator`. See `MergeGateReport`, `CodeownersReport`,
    `MaintenanceReport`.
  - **Current-state source** — fetched fresh by `collect`, stored latest-only per repository with a
    `fetched_at` the report surfaces, served from storage by `evidence` so `--offline` works.
    See architecture.md "Source taxonomy".
  - **Report-only and ungraded** — `ReadinessPolicy` reads none of this, per the standing rule that a
    signal becoming visible is not a reason to grade it. Same as security, CODEOWNERS and maintenance.
  - **Optional per-repository configuration** — `enablement: dict[str, EnablementInstant]` with a
    validator rejecting names no team owns. `sonar_projects` follows it exactly.
  - **Withheld is not merely off** — a repository with no SonarCloud project is an OBSERVATION and
    records no failure; a SonarCloud or GitHub call that failed is a failure and exits `3`.
- Dependencies: none new. SonarCloud is read anonymously over `requests`, as `scripts/audit_sonar_project_mapping.py`
  already does; `SONAR_TOKEN`/`SONARCLOUD_TOKEN` widen the listing to private projects when set.

### Endpoint behaviour, measured 2026-08-27

All four calls verified anonymously against the live `hmcts` organisation:

- `GET /api/components/search_projects?organization=X&ps=500&f=analysisDate` — 289 projects, one page
  per 500. Returns `key`, `visibility`, `analysisDate`, `analysisDateAllBranches`. 275 of 289 were
  analysed within the last four months; 14 have never been analysed.
- `GET /api/project_analyses/search?project=K&ps=N` — returns `analyses[]` with `date` and
  `revision`, the commit SHA the analysis ran against.
- `GET https://api.github.com/search/commits?q=org:X+hash:<sha>` — resolves the SHA to its
  repository. Verified on four projects, including the two no name rule can reach:
  `hmcts.cath → cath-service` and `SSCSSYAF → sscs-submit-your-appeal`. **Rate limited to 10 requests
  per minute unauthenticated, 30 authenticated** — this is the cost that makes `map-sonar` a separate,
  periodically-run command.
- `GET /repos/{org}/{repo}/commits/{sha}` — `200` when the repository holds the commit, `422` when it
  does not. Spends the core quota (5,000/hour), not the search quota, so **confirming a candidate is
  cheap and only discovering an unknown repository needs the search API**.
- `GET /api/measures/component?component=K&metricKeys=...` — anonymous. **One invalid metric key
  returns `measures: null` for the whole call**, silently: `maintainability_rating` does not exist
  (it is `sqale_rating`), and including it discards every other metric in the request. The key set
  must therefore be a single validated constant, asserted in a test.

Verified working key set, one call:
`alert_status`, `quality_gate_details`, `coverage`, `duplicated_lines_density`, `ncloc`,
`violations`, `software_quality_reliability_issues`, `software_quality_maintainability_issues`,
`software_quality_security_issues`, `security_hotspots`, `reliability_rating`, `sqale_rating`,
`security_rating`, `security_review_rating`.

`quality_gate_details` returns every condition with its `metric`, `op`, `error` threshold, `actual`
value and `level` — which is what lets the block show every condition behind the gate, as the merge
gate and the readiness assessment already do rather than printing a bare pass/fail.

### Why the name rules are not implemented

`scripts/audit_sonar_project_mapping.py` measured all three proposed options over 3,372 repositories.
The name-rule ladder is deliberately **not** carried into `src/`:

- 240 repositories declare a key in a root `sonar-project.properties`. **123 of those name no project
  SonarCloud lists**, and **69 sit in 26 collision groups** — `rpe-expressjs-template` is declared by
  14 different repositories that were scaffolded from the template and never changed the key. Only 79
  of 240 declarations are both visible and unique. A declaration is therefore a *hypothesis to
  confirm*, never an answer.
- The name rules were wrong for 6 of the 70 repositories where both options answered, and that 8.6%
  is measured on a biased sample — repositories carrying a properties file skew to JavaScript, while
  Gradle and Maven repositories declare the key in a build file the audit never read.
- Three of those six conflicts are duplicate projects (`rpx-xui-webapp` vs `rpx-xui-webapp_2`), where
  the ladder prefers the exact name and the exact name is the ABANDONED project: each `_2` was
  analysed on 2026-08-26 while its bare-name twin went quiet in July. SonarCloud has no rename, so
  the counter suffix marks the live project.

The stored map resolves all three cases from evidence instead of from a convention, so the ladder has
no remaining job. It stays in the audit script, where it is a measurement rather than a mechanism.

### Reverse lookup must be many-to-one

Two projects legitimately resolve to one repository — the duplicate pairs above. `repository_project`
therefore returns the project with the **most recent `analysis_at`**, which picks the live project in
every measured pair, and the count of candidates is reported so a human can see there was a choice.

## Development Approach

- Code then tests, per task, following the existing suite's style: `tests/test_sonar.py` for the
  client and resolution, additions to `test_config.py`, `test_domain.py`, `test_storage.py`,
  `test_inventory.py`, `test_evidence.py`, `test_render.py`, `test_cli.py`.
- HTTP is faked at the client seam exactly as `tests/test_inventory.py` fakes `GitHubClient` — no
  network in the suite.
- Complete each task fully before moving to the next.

## Validation Commands

- `uv run pytest`
- `uv run ruff check .`
- `uv run ruff format --check .`
- `uv run mypy src`

## Implementation Steps

### Task 1: Configure the SonarCloud organisation and per-repository overrides

- [x] Add `sonar_organization: str | None = None` to `Configuration`, with a `sonar_organization_name`
      helper (or property) returning `sonar_organization or organization`, so the common case where
      both names match is not restated in the file
- [x] Add `sonar_projects: dict[str, str] = Field(default_factory=dict)` mapping a configured
      repository name to its SonarCloud project key — the override that short-circuits resolution
- [x] Add a `model_validator` rejecting a `sonar_projects` key that names no configured repository,
      worded and reasoned like `validate_enablement`: a typo would otherwise read as "no project
      configured", indistinguishable from having forgotten it
- [x] Reject an empty-string project key, so an override cannot silently mean "unresolved"
- [x] Document both keys in `metrics.example.yaml` with a comment saying the override is the answer of
      last resort for the ambiguous cases the map cannot settle (`rpx-xui-icp-api` declaring
      `em-icp-api`, which `em-icp-api` also declares)
- [x] write tests for this task in `tests/test_config.py`: the default falls back to `organization`,
      an explicit value wins, an unconfigured repository is rejected, an empty key is rejected
- [x] run the project test suite - must pass before task 2

### Task 2: Add the SonarCloud evidence models

- [x] Add `EvidenceKind.SONAR` for failure records
- [x] Add `SonarRating` handling: SonarCloud returns `1.0`–`5.0`, so store the float and expose the
      `A`–`E` letter for rendering; an unknown value must not become `A`
- [x] Add `SonarQualityGateCondition` (metric, comparator, threshold, actual, level) and
      `SonarQualityGate` (level `OK`/`ERROR`/`NONE`, plus its conditions), parsed from
      `quality_gate_details`
- [x] Add `SonarMeasures`: project key, `analysis_at`, gate, coverage, duplication density, lines of
      code, total violations, the three software-quality issue counts, security hotspots, and the four
      ratings — every field optional, because a project can be listed and never analysed (14 are), and
      an absent measure must never read as zero
- [x] Add `SonarProjectMapping` (project key, repository, `analysis_at`, revision, resolution method)
      and `SonarReport` with an evidence-or-reason `model_validator`, matching `MaintenanceReport`
- [x] Add `sonar: SonarMeasures | None = None` to `RepositoryInventoryItem` (defaulted, so rows stored
      before this source existed still parse) and `sonar: SonarReport` to
      `RepositoryPracticeEvidence`
- [x] write tests for this task in `tests/test_domain.py`: the evidence-or-reason rule both ways, the
      rating letter mapping including an out-of-range value, a gate with no conditions
- [x] run the project test suite - must pass before task 3

### Task 3: Add the SonarCloud client

- [x] Create `src/metrics/sonar.py` with a `SonarClient` over `requests.Session`, reading
      `SONAR_TOKEN` then `SONARCLOUD_TOKEN` and sending `Authorization: Bearer` only when one is set —
      anonymous reads work and a bad token returns `401` where no token returns `200`
- [x] Add `SonarError` carrying an `AvailabilityReason`, so a failure classifies like a `GitHubError`
- [x] Add `list_projects(organization)` paging `/api/components/search_projects` at `ps=500` with
      `f=analysisDate`, returning key, visibility and analysis instant
- [x] Add `project_analyses(project, limit)` over `/api/project_analyses/search`, returning
      `(date, revision)` newest first
- [x] Add `component_measures(project)` over `/api/measures/component` using a single
      `SONAR_METRIC_KEYS` constant, parsing the response into `SonarMeasures` and treating a missing
      metric as absent rather than zero
- [x] write tests for this task in `tests/test_sonar.py` with a faked session: paging, a token being
      sent only when set, a `401` classifying as a failure, an unparseable body classifying as a
      failure, and a measures response missing several metrics
- [x] Add a test asserting every key in `SONAR_METRIC_KEYS` is one of the documented valid keys and
      that `maintainability_rating` is not among them — one invalid key silently empties the whole
      response, so the constant is the thing that must be guarded
- [x] run the project test suite - must pass before task 4

### Task 4: Resolve a SonarCloud project to its GitHub repository

- [x] Add `sonar_to_github(sonar_client, github_client, organization, project)` to `sonar.py`: read the
      newest analyses, and for each one search `org:{organization} hash:{revision}` until a commit
      resolves — returning the repository name, the analysis instant and the revision that answered
- [x] Walk more than one analysis before giving up, because a pull-request analysis can name a commit
      on a branch since force-pushed or deleted; cap the attempts and record how many were tried
- [x] Add `confirms_candidate(github_client, organization, repository, revision)` using
      `GET /repos/{org}/{repo}/commits/{sha}` — `200` confirms, `422`/`404` refutes. This spends the
      core quota rather than the 10-per-minute search quota, and is the call the properties-file
      cross-check uses
- [x] Classify the outcomes distinctly: resolved; no analysis to resolve from (a never-analysed
      project); the revision matched no commit in the organisation; the revision matched a repository
      outside it; and the search call failed. The first four are observations about the project, the
      last is a failure
- [x] Pace the search calls to stay inside the documented limit and treat a `403` naming a rate limit
      as a pause rather than a refusal, as `scripts/audit_sonar_project_mapping.py` does
- [x] write tests for this task in `tests/test_sonar.py`: a first-analysis hit, a fallback to an older
      analysis, a project with no analyses, a SHA matching nothing, a SHA matching another
      organisation, a search failure, and a candidate confirmation returning both `200` and `422`
- [x] run the project test suite - must pass before task 5

### Task 5: Store the mapping in the observations database

- [x] Add `sonar_project_map` to `initialize_observations`, keyed on
      `(sonar_organization, project_key)`: `repository`, `analysis_at`, `revision`, `method`,
      `resolved_at`, and a `detail` for a project that could not be resolved — storing the failure
      keeps `map-sonar` from spending the search quota on the same hopeless project every run
- [x] Record in the table's docstring why it is in the DURABLE file rather than the cache: rebuilding
      it costs 289 rate-limited search calls, so `rm metrics.sqlite3` must not silently shrink the
      next evidence run's coverage. Note that this stretches the observations file beyond alert
      history, and that the alternative was a third file
- [x] Add `record_sonar_mapping`, upserting **only when the incoming `analysis_at` is newer** than the
      stored one, so a project that has moved between repositories follows the newer analysis and a
      re-run with stale data cannot move it back
- [x] Add `load_sonar_mapping(path, sonar_organization, project_key)` and
      `repository_project(path, sonar_organization, repository)` — the reverse lookup, returning the
      candidate with the newest `analysis_at` first together with the total number of candidates, so a
      duplicate pair resolves to the live project and still says a choice was made
- [x] write tests for this task in `tests/test_storage.py`: insert then read back, an older analysis
      not overwriting a newer one, a newer analysis moving a project to a different repository, the
      reverse lookup preferring the newest analysis across a duplicate pair, an unresolved row round
      tripping with its reason, and the table living in the observations file and not the cache
- [x] run the project test suite - must pass before task 6

### Task 6: Add the `map-sonar` command

- [x] Add a `map-sonar` subparser taking `--config` and reporting progress on stderr, following the
      `doctor` command's arrangement
- [x] Iterate every project the configured `sonar_organization` lists, call `sonar_to_github`, and
      upsert each result as it is resolved, so a run stopped by a rate limit keeps everything it
      already paid for
- [x] Skip a project whose stored `analysis_at` already equals the listed `analysisDate` — nothing has
      been analysed since it was last resolved, so there is nothing to learn and no call to spend
- [x] Emit a summary: projects listed, resolved, unchanged, never analysed, unresolvable, failed, and
      repositories now mapped — plus the projects that resolved to a repository already claimed by
      another project, which is the duplicate list a human may want to see
- [x] Return the three-valued exit status: `0` every project was answered, `3` at least one call
      failed, `1` nothing usable. A never-analysed or unresolvable project is an observation and does
      not make the run partial
- [x] write tests for this task in `tests/test_cli.py`: a full run over faked clients, a resumed run
      skipping unchanged projects, a failed project producing exit `3`, and an organisation with no
      projects producing exit `1`
- [x] run the project test suite - must pass before task 7

### Task 7: Resolve a repository to its SonarCloud project

- [x] Read `sonar-project.properties` in the existing bundled standards GraphQL query by adding a
      `... on Blob { text }` selection beside the CODEOWNERS `byteSize` selections — no extra round
      trip, and this document is not windowed so no cached coverage signature is affected
- [x] Parse `sonar.projectKey` and `sonar.organization` from it, reusing the property-format rules
      `scripts/audit_sonar_project_mapping.py` established: `key=value`, `key:value` or `key value`,
      comments only at the start of a line, last definition winning
- [x] Add `github_to_sonar(...)` returning the resolved project key and how it was resolved, in this
      order: configured override; declared key confirmed by the stored map or by a candidate check
      against the latest analysis revision; the stored map's own answer for the repository; otherwise
      unresolved with the reason
- [x] Treat a declared key that the map attributes to a DIFFERENT repository as refuted, and fall
      through to the map's answer — this is the `rpe-expressjs-template` case, where 14 repositories
      declare one template's key and at most one of them owns it
- [x] Record the resolution method on the evidence, so a wrong mapping is diagnosable later rather
      than mysterious
- [x] write tests for this task in `tests/test_sonar.py`: the override winning over everything, a
      declaration confirmed by the map, a declaration refuted by the map falling through, a
      declaration for a project nobody has mapped being confirmed by a candidate check, no
      declaration falling back to the map, and nothing resolving at all
- [x] run the project test suite - must pass before task 8

### Task 8: Collect the measures as current state

- [x] In `collect_repository_state`, resolve the project and fetch its measures, storing them on the
      inventory item exactly as `codeowners` and `maintenance` are — latest-only, replacing the
      previous row
- [x] Leave the field unset when no project resolved, so the block reports the reason rather than an
      empty measure set; record NO failure for this, because a repository with no SonarCloud project
      is an observation and most of the organisation has none
- [x] Record one `RepositoryInventoryIssue` with `EvidenceKind.SONAR` when a SonarCloud call failed,
      so the run exits `3` for a genuine refusal
- [x] Keep the repository's collection one measurable unit by counting the SonarCloud calls in the
      existing cost meter, or state in the docstring why they are not counted if the meter is
      GitHub-specific
- [x] write tests for this task in `tests/test_inventory.py`: a repository with a resolvable project
      storing its measures, a repository with none storing nothing and recording no failure, a
      SonarCloud failure recording one issue, and the properties blob being read from the bundled query
- [x] run the project test suite - must pass before task 9

### Task 9: Project the stored measures into the evidence report

- [x] Add `stored_sonar(stored)` to `evidence.py`, following `stored_maintenance` exactly: the
      unreadable case, the never-collected case, the collected-before-this-source-existed case, and
      the collected case
- [x] Distinguish "no SonarCloud project is mapped to this repository" from "measures were not
      collected when repository state was stored" — the first is an answer and the second is a gap
- [x] Thread the report through `assess` and `RepositoryPracticeEvidence` beside `codeowners` and
      `maintenance`, and state in the docstring that it grades nothing
- [x] Wire it into `evidence_report` in `cli.py` from the single existing stored-state load
- [x] write tests for this task in `tests/test_evidence.py`: each unavailability reason, a stored row
      round tripping, and a test asserting the readiness label is unchanged by the presence or absence
      of Sonar evidence
- [x] run the project test suite - must pass before task 10

### Task 10: Render the SonarCloud block

- [x] Add `render_sonar(report)` printing the project key, the resolution method, the analysis
      instant, and the gate level — or the reason there is none
- [x] Render the gate conditions as a table of metric, comparator, threshold, actual and level, so
      every condition behind a pass or a failure is visible rather than just the verdict
- [x] Render the measures as pairs: coverage, duplication, lines of code, total violations, the three
      software-quality issue counts, security hotspots, and the four ratings as `A`–`E` letters
- [x] Print an absent measure as a dash with no invented number, per "unavailable data never becomes
      zero"
- [x] Insert the block after `render_security` in `render_repository`, keeping the stored current-state
      blocks together
- [x] write tests for this task in `tests/test_render.py`: a full block, a gate with a failing
      condition, a project with no analysis, each unavailability reason, and a block whose measures are
      all absent
- [x] run the project test suite - must pass before task 11

### Task 11: Document the source and record the ruling

- [x] Add a `## SonarCloud quality evidence (decided 2026-08-27)` section to `docs/architecture.md`
      recording: the resolution order and why the name-rule ladder was measured and rejected (123
      phantom declarations, 69 colliding, 8.6% error on a biased sample of 70); why the map lives in
      the durable file; why the duplicate pairs resolve by analysis recency; and that the block is
      report-only and ungraded per the standing rule
- [x] Add SonarCloud to the current-state list in the "Source taxonomy" section
- [x] Note the two known limitations: a repository whose project has not been analysed within
      SonarCloud's pull-request retention and carries no properties file cannot be mapped until
      `map-sonar` next runs after an analysis; and `map-sonar` must be run periodically or a project
      that moves keeps reporting its old repository until it is
- [x] Document `sonar_organization`, `sonar_projects` and `map-sonar` in `README.md`, including that
      the command is run by hand rather than on every `collect`, and why: 289 projects against a
      10-per-minute commit-search limit
- [x] Add the Sonar block to the README's per-repository report inventory
- [x] run the project test suite - must pass before task 12

### Task 12: Verify acceptance criteria

- [x] run the full test suite — 732 passed
- [x] run the linter and the formatter check — `ruff check` clean, 55 files already formatted
- [x] run `uv run mypy src` — no issues in 32 source files
- [x] verify `metrics map-sonar --config metrics.example.yaml --help` and
      `metrics evidence --config metrics.example.yaml --format report` both work against the example
      configuration — `map-sonar --help` prints its usage; `evidence` against the example
      configuration reports `example-service` as unavailable and exits `1`, because that repository
      is fictional and no cache covers it, so the report was exercised end to end instead over a
      seeded workspace: real cached facts written through `requested_coverage` and real state through
      `record_repository_state`, then the shipped CLI run as a subprocess. It renders the block —
      project, `Resolved by`, analysis instant, `ERROR` gate, both conditions, every measure and the
      four rating letters — and exits `0`
- [x] confirm the readiness label is unaffected by Sonar evidence, and that a repository with no
      mapped project exits `0` rather than `3` — the same seeded workspace reported the identical
      label for every repository with and without Sonar evidence, including a mapped project whose
      gate was `ERROR`; `src/metrics/assessment.py` names no Sonar signal at all. The unmapped
      repository reported "no SonarCloud project is mapped to this repository" and the run exited `0`
- [x] confirm `README.md` and `docs/architecture.md` describe what was built — the README's sample
      block matches the rendered output line for line, and both the resolution ladder and the two
      known limitations are recorded in architecture.md's "SonarCloud quality evidence" section
