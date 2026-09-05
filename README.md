# metrics

Collects evidence about teams' software delivery practices from GitHub and SonarCloud to support AI
enablement decisions and other software delivery governance. Reports per-repository readiness, behaviour metrics, merge-gate state, security
alerts, and trends since enablement, as JSON, a plain-text report, or via a web dashboard.

## Quick start

```bash
# 1. Authenticate and collect
export GH_APP_ID=123456
export GH_APP_INSTALLATION_ID=78901234
export GH_APP_PRIVATE_KEY_PATH=~/.config/metrics/github-app.pem
uv run metrics doctor --config config.yml # just a smoke test
uv run metrics collect --config config.yml --days 90

# 2a. Serve the dashboard
CONFIG=config.yml docker compose up --build   # UI on http://localhost

# 2b. Or report in the terminal
uv run metrics evidence --config config.yml --format report

# 2c. Or raw JSON
uv run metrics evidence --config config.yml
```

Every command takes `--help` for the full set of switches.

## Configuration

`config.yml` in this repository is the working configuration; edit it to change teams,
repositories, or thresholds. [`metrics.example.yaml`](metrics.example.yaml) shows the minimal
config:

```yaml
version: 1
organization: hmcts
database: .metrics/metrics.sqlite3
lookback:
  operational_days: 90
assessment:
  enabled: true
  minimum_merges: 10
teams:
  - identifier: example
    display_name: Example Team
    repositories:
      - example-service
enablement:
  example-service: 2026-06-01
sonar_projects:
  example-service: example-service
```

Most relevant keys are:

- `teams` assigns each repository to exactly one team. Ownership comes from this file; GitHub is
  used for evidence, and this list decides which repositories are collected and reported.
- `database` names the SQLite cache. A second file beside it (`metrics-observations.sqlite3`) holds
  security-alert history and the SonarCloud project map. The cache is disposable; the observations
  file holds history that GitHub cannot resupply, so keep it.
- `enablement` records when agentic tooling was switched on per repository. It drives `trend`, and
  a repository without a date is reported as having none.
- `assessment` holds the green/amber/red thresholds. Each is a policy choice; the defaults are a
  starting point to argue with.
- `sonar_projects` overrides the SonarCloud project mapping where automatic resolution gets it
  wrong or has nothing to go on.

Unknown keys are rejected at load. `--config` can be repeated: the files are concatenated and
parsed as one document, so shared policy (thresholds, lookbacks) can live in one file and each
team's repositories in another.

## Authentication

Any process that contacts GitHub needs a credential: `doctor`, `collect`, `evidence --refresh`, and
`trend` without `--offline`.
Reporting from the cache, e.g. plain `evidence`, `trend --offline`, and the dashboard does not.

- **GitHub App (preferred)**: set `GH_APP_ID`, `GH_APP_INSTALLATION_ID`, and
  `GH_APP_PRIVATE_KEY_PATH` pointing at the PEM file. If you need to use an inline secret for continuous integration, use `GH_APP_PRIVATE_KEY` instead of `GH_APP_PRIVATE_KEY_PATH`.
- **Personal access token (fallback)**: set `GH_TOKEN` instead of the other variables. Collection works, and the sources the token cannot read are reported as unavailable.

SonarCloud is read anonymously; set `SONAR_TOKEN` to include private projects.

## Collection

```bash
uv run metrics collect --config config.yml --days 14
```

`collect` fills the cache but computes no metrics. Pull-request, review, and direct-commit history
is fetched once per window and reused on later runs; current state (merge gate, security alerts,
open pull requests, CODEOWNERS, maintenance, SonarCloud measures) is refreshed every run. Progress
is printed per repository, and the run ends with a summary of every GitHub call.

Run it on a schedule; weekly is enough. Security-alert history is only recorded when a run
observes it, so a missed run is a permanent gap in the trend series.

Windows are half-open UTC intervals: `--days N` ends at the most recent UTC midnight, and
`--from`/`--to` name an explicit span (used for backfills). Exit status is `0` when every
repository was observed, `3` when the run recorded at least one failure, and `1` when it produced
nothing usable.

Two supporting commands:

- `metrics map-sonar` resolves which repository each SonarCloud project analyses and stores the
  map. Run it by hand, periodically; it is rate-limited, which is why it is separate from
  `collect`.
- `metrics prune --days N` deletes cache intervals no collection has used for N days.

## Dashboard

Two processes:
- `metrics-serve` answers JSON from the caches, and 
- a Next.js app in [`ui/`](ui/)
renders it.

Neither process requires any credentials: all figures come from the files `collect` wrote.

With Docker Compose:

```bash
uv run metrics collect --config config.yml --days 90
docker compose up --build
```

Notes:
- The UI is published on `http://localhost` (host port 80, container port 3000; change the host half
of `80:3000` in `docker-compose.yml` if 80 is taken).
- The docker-compose file defaults to configuration in `config.yml`; prefix with `CONFIG=something-else.yml` to override.
- The configuration's `database` must resolve inside `.metrics`,
which is the directory the compose file mounts.

Without Docker:

```bash
uv sync --extra service
uv run metrics-serve --config config.yml
npm --prefix ui install
API_URL=http://localhost:8000 npm --prefix ui run dev
```

The service pre-builds a report per window span (1–26 weeks) and refreshes them in the background;
`--warm-interval` and `--max-bundle-age` tune that. Its one network call is an unauthenticated
fetch of the public production-approvals list (`production_list_url`), which puts the `Production`
badge on rows; set it to `null` to turn the fetch off. See [`ui/README.md`](ui/README.md) for the
pages and UI development.

## Reports

`evidence` reports from the cache and contacts GitHub only under `--refresh`:

```bash
uv run metrics evidence --config config.yml                    # JSON (the default)
uv run metrics evidence --config config.yml --format report    # plain-text report
uv run metrics evidence --config config.yml --refresh          # collect missing history first
uv run metrics evidence --config config.yml --repository example-service \
  --metric independent-review-coverage                         # one metric, drill-down
```

JSON is the machine-readable contract. `--format report` renders the same figures as plain ASCII
for piping, diffing, and pasting into tickets; it is presentation only, so the two formats cannot
disagree. `evidence` refuses a window the cache does not fully cover: run `collect` first, or pass
`--refresh`.

`trend` reports the same metrics as a series of fixed-length periods since each repository's
enablement date, each with a delta against a pre-enablement baseline, plus the security-alert
observations recorded over the span. Values are reported without grading.

```bash
uv run metrics trend --config config.yml --offline --format report
```

## What is measured

Each repository report carries:

- **Readiness assessment**: `green`, `amber`, `red`, or `cannot_assess`, with every condition
  checked listed as blocking, caution, or clear, so a green is as auditable as a red. An
  unprotected default branch, or a merge gate requiring no approving review, is red outright;
  behaviour shortfalls grade against the configured thresholds. `cannot_assess` means the merge
  gate could not be read or too little merged to grade (`assessment.minimum_merges`).
- **Nine behaviour metrics**: `independent-review-coverage`, `approval-coverage`, `review-depth`,
  `merge-cycle-time`, `time-to-first-review`, `pull-request-size`, `checks-passing-at-merge`,
  `description-quality`, and `traceability-reference`.
- **Current-state blocks**: merge-gate rules, open security alerts by family and severity, open
  pull-request counts, CODEOWNERS files, maintenance (last commit and last human commit), and the
  mapped SonarCloud project's quality gate and measures. These show the state at the last
  collection, with the instant it was read.
- **Actors**: the same evidence read per person, listing each contributor with the readiness
  labels of the repositories they merged into.

The cohort is every merge into the default branch: a commit pushed directly counts as an
unreviewed, unapproved merge, so bypassing the process shows up in the same rates. Dependency bots
(`renovate`, `dependabot` by default, via `cohort.excluded_authors`) are excluded; agent-authored
work stays in, because it is the subject under study. Unavailable data is reported with its
reason: an unreadable security feed or a hidden merge gate reads as unknown, never as zero.

There is no team-level or per-person score: labels are per repository, and teams are read by
reading their repositories.

## Development

```bash
uv sync
uv run poe check              # lint, format, types, tests with coverage
npm --prefix ui run check     # the same gate for the UI
```

Design decisions, measurements, and scope boundaries are recorded in
[`docs/architecture.md`](docs/architecture.md).
