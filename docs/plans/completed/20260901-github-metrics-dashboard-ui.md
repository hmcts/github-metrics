# Plan: GitHub Metrics Dashboard UI

## Overview

Add a web UI to the `metrics` project for exploring `metrics evidence` reports, replicating the look and feel
of the predecessor app, a copy of which sits inside this repository at `ai-in-sdlc-github-analysis/`
(untracked; do not modify or commit it) (Next.js 14 App Router +
Tailwind + recharts + lucide-react, dark slate/indigo theme). A FastAPI service in this repo reuses
`metrics.evidence` as a library, serves only from the SQLite caches (never contacts GitHub), and supports
live window selection. Prerequisite Python work: cache open-PR state at collect time, flip `evidence` to
offline-by-default with `--refresh`, and add per-actor behaviour metrics plus a team stamp to the evidence
JSON.

## Context

**Why.** The predecessor app (`ai-in-sdlc-github-analysis`) had slow collection and some wrong numbers, but a
UI the user likes. This plan reuses its UI framework and visual language over our correct, cached data. The
user decided: FastAPI service (like the predecessor, for live window selection), full per-actor behaviour
metrics, and team-level label distributions (team roll-up reversal approved 2026-09-01).

**Files involved (this repo, worktree root):**
- `src/metrics/domain.py` — `OpenPullRequestSnapshot`, `BehaviourMetricSummary`, new fields on
  `RepositoryInventoryItem` (~line 1467), `RepositoryPracticeEvidence` (~1165), `ActorRepositoryReadiness`
  (~1194), `OpenPullRequestReport` (~385), `EvidenceKind` (~44)
- `src/metrics/inventory.py` — `collect_repository_state` (~1368), `collect_inventory` (~1466)
- `src/metrics/evidence.py` — stored projections pattern (~270–357), `StoredReports` (~245),
  `stored_reports()` (~360), `RepositoryEvidence.practices` (~79), `RepositoryEvidence.metric` (~54),
  `actor_readiness` (~172), `offline_open_pull_request_report` (~452, to delete)
- `src/metrics/cli.py` — evidence parser (`--offline` at ~157/181), `gather_open_pull_requests` (~325),
  `evidence_report` (~221), `collect_evidence` (~537). Keep churn minimal: the in-flight `gh-app` branch
  touches this file heavily.
- `src/metrics/render.py` — open-PR block (~263)
- `src/metrics/service.py` — NEW FastAPI service
- `docs/architecture.md` — rulings to reverse with dated entries: open-PR "never cached, DECIDED" (~508,
  also ~230, ~971), "No dashboards" (~1200), "NO TEAM ROLL-UP" (~1206, ~1230)
- `ui/` — NEW Next.js app

**Porting sources (predecessor, read-only):** under `ai-in-sdlc-github-analysis/web/` at the repository
root (an untracked copy of the predecessor repo — read it, never edit or commit it):
`src/app/globals.css` (9 lines), `src/app/layout.tsx`, `src/components/Navigation.tsx`,
`src/components/MetricCard.tsx`, `src/components/charts/TrendChart.tsx`,
`src/components/charts/SummaryPieChart.tsx`, `src/components/FilterSearchBox.tsx`,
`src/components/NavWeekSelector.tsx`, `src/lib/repoHealthUtils.ts`, `src/lib/api.ts`,
`src/lib/weeksPreference.ts`, `src/lib/types.ts` (reference for typed-mirror style). Do NOT port dead code:
`UsersFilteredTable.tsx`, `HideEmptyToggle.tsx`, `RepositorySearchBox.tsx`, Cloaking*, PathGate, export
utilities, `date-fns`.

**Theme conventions to replicate (the predecessor theme is class-string idioms, not a config):** page
`bg-slate-950 text-slate-100`; cards `bg-slate-900 border border-slate-800 rounded-lg p-5
hover:border-slate-700`; section headings `text-sm font-semibold text-slate-300 uppercase tracking-wide`;
tables `text-xs`, `thead text-slate-400 border-b border-slate-800`, `tbody divide-y divide-slate-800/50`,
rows `hover:bg-slate-800/30`; metric value `text-2xl font-semibold`, label `text-xs text-slate-400 uppercase
tracking-wide`; `font-mono` for logins/repo names, `tabular-nums` for numeric columns; indigo links/buttons
(`text-indigo-400`, `bg-indigo-600 hover:bg-indigo-500`); sticky nav `h-14 bg-slate-900 border-b
border-slate-800`. RAG hexes: red `#f87171`, amber `#fbbf24`, green `#4ade80`, none/unknown `#64748b`,
accent `#818cf8`. **No RAG emojis** — a thicker left colour bar (`border-l-4`) plus a small text label
replaces them. **No breadcrumbs** — each page shows only where you are now (`EntityHeader`). Infinite
drill-through: repo ↔ actor ↔ team, links always carrying `?weeks=`.

**Guardrails (architecture.md scope boundaries, still binding on the UI):** no personal rankings — actors
listed alphabetically, never ordered by any metric or finding; no cross-repository averaging — per-actor
metrics are per-repository, never combined; no combined team verdict or score, and no ordering of teams —
per-team label *counts* are newly permitted by this plan's reversal.

**Constraints:** Python gates are `uv run poe check` (ruff ALL @120 cols, ruff format, strict mypy, pytest
with `fail_under = 100`). `tests/test_cli.py` (~3000 lines) pins exact report text — expect updates in tasks
1–3. `GH_TOKEN` is never available to agents: every checkbox must work offline; live smokes are the user's.
One discrete shell command per Bash call. The `ui/` directory is invisible to Python tooling; its gate is a
single script mirroring `poe check`: `npm run check` (defined in `ui/package.json` as lint + `tsc --noEmit` +
vitest + build), run from `ui/` or as `npm --prefix ui run check`.

**Coding standards for `ui/` (the user's global standards apply to TypeScript too):** identifiers are
complete, correctly-spelled English words — no abbreviations (`repository` not `repo` in identifiers;
established short forms like `api`, `id`, `url`, `config` are fine); no single-letter names (only `x`/`y`
coordinates, mathematical indices, and `_` for discarded values); prefer single-word identifiers, compounds
only for genuine disambiguation; in CamelCase an initialism is all-caps or all-lower, never mixed —
`RAGCard`, `apiFetch`, `PRCount`, not `RagCard` or `PrCount` (all-lowercase file names like `rag.ts` are
fine); comments only for *why*; core logic in `ui/src/lib` as pure functions with I/O at the page boundary;
build collections with `map`/`filter`/spreads, not push-loops. Route segment directories (`[repo]`,
`[login]`, `[team]`) are URLs, not identifiers — but rename `[repo]` to `[repository]` for consistency.

**Dependencies:** `[project.optional-dependencies] service = ["fastapi>=0.115", "uvicorn>=0.30"]`; dev group
gains `fastapi`, `uvicorn`, `httpx` (TestClient). New console script `metrics-serve = "metrics.service:main"`
— deliberately not a `metrics` subcommand, keeping `cli.py` clear of the `gh-app` collision. UI deps: next
14.2.x, react 18, recharts ^2.12, lucide-react, clsx; dev: typescript 5, tailwindcss ^3.4, postcss,
autoprefixer, eslint-config-next, vitest.

**Service design (task 4):** offline-only; `?weeks=` validated against `WEEKS_OPTIONS = (1, 4, 8, 12, 26)`
(filtered at startup against `lookback.maximum_days`), default 4. A `WindowCache` holds one `ReportBundle`
per weeks value — the `PracticeEvidenceReport` from a new `evidence.offline_practice_report(configuration,
window)` plus derived indexes (repo→block, casefolded login→actor, team→repos, repo→contributor rows, org
label distribution) — built under a `threading.Lock`, invalidated on source stamp change (mtime+size of both
SQLite files) or TTL (default 3600s). Endpoints: `GET /healthz`, `/windows`, `/overview`, `/repositories`,
`/repositories/{repository}`, `/repositories/{repository}/trend` (task 12), `/actors`, `/actors/{login}`,
`/teams`, `/teams/{team}` — list rows are thin view models, detail endpoints return domain models. Uncovered
windows surface as the report's `unavailable` entries, never silently thinner data. No CORS: the Next.js
server fetches server-side (`API_URL` env), predecessor pattern.

## Development Approach

- Code-then-tests within each task; every Python branch needs a test (coverage gate is 100%)
- Complete each task fully before moving to the next; `uv run poe check` must pass at the end of every
  Python task, the UI gates at the end of every UI task
- Python tasks first (1–4) so the service exists before any page consumes it

## Validation Commands

- `uv run poe check` (ruff + format + strict mypy + pytest at 100% coverage)
- `npm --prefix ui run check` (lint + `tsc --noEmit` + vitest + build, the full UI gate in one step)

## Implementation Steps

### Task 1: Cache open-PR state at collect time

- [x] `domain.py`: add `OpenPullRequestSnapshot(EvidenceModel)` with `starts_at`/`ends_at` (the collect
      window the two windowed counts were measured over) and `summary: OpenPullRequestSummary`; add
      `open_pull_requests: OpenPullRequestSnapshot | None = None` to `RepositoryInventoryItem` (old stored
      rows must parse as None); add `OPEN_PULL_REQUESTS` to `EvidenceKind`
- [x] `inventory.py`: add `collect_open_pull_requests(...)` wrapping the existing
      `behaviour.collect_open_pull_request_state` (behaviour.py:549, unchanged), returning
      snapshot-or-`RepositoryInventoryIssue`; wire into `collect_repository_state` (signature gains `window`
      and `reference`; `stale_open_days` from configuration) and its `model_copy(update=...)` block,
      appending failures like the other blocks
- [x] `cli.py`: thread `window` and `reference` from `collect_evidence` through `collect_inventory` — no
      other cli.py changes in this task
- [x] `docs/architecture.md`: dated reversal (2026-09-01, user instruction) of "Open pull-request state is a
      third kind: never cached, DECIDED" (~line 508; adjust ~230 and ~971 to match): now collected and
      stored latest-only beside the merge gate, window stored with the counts, `evidence --refresh` still
      observes fresh
- [x] write/update tests: snapshot model in `tests/test_domain.py`; success, GitHubError→issue, and
      item-still-stored-on-failure paths in `tests/test_inventory.py`; threading in `tests/test_cli.py`
- [x] run `uv run poe check` - must pass before task 2

### Task 2: Serve stored open-PR state; evidence defaults offline with --refresh

- [x] `domain.py`: add optional `starts_at`/`ends_at` to `OpenPullRequestReport` naming the measured window
      (its validator forbids a caveat detail beside a summary, so the window rides as fields)
- [x] `evidence.py`: add `stored_open_pull_requests(stored)` beside the five existing projections (pre-field
      rows → "open pull-request state was not collected when repository state was stored; run metrics
      collect"); add `open_pull_requests` to `StoredReports` and `stored_reports()`; drop the separate
      `open_pull_requests` parameter from `RepositoryEvidence.practices` (read from stored state); delete
      `offline_open_pull_request_report`; keep `open_pull_request_report` for the refresh path
- [x] `cli.py`: evidence parser — remove `--offline`, add `--refresh` ("contact GitHub to collect missing
      history and observe open pull-request state fresh"); GH_TOKEN required only under `--refresh`;
      `gather_open_pull_requests` becomes refresh-only, overriding the stored block; default path constructs
      no `Session` (extend the pinned `test_evidence_runs_offline_without_token_or_session` pattern)
- [x] `render.py`: open-PR block renders stored `fetched_at` and the measured window instead of implying a
      fresh fetch
- [x] `README.md` + `docs/architecture.md`: document the breaking CLI change — evidence is offline-first,
      `--refresh` is the single live call
- [x] write/update tests: stored-projection variants in `tests/test_evidence.py`; parser default/refresh in
      `tests/test_cli.py` including exact report-text updates; render window text in `tests/test_render.py`
- [x] run `uv run poe check` - must pass before task 3

### Task 3: Per-actor metrics, team stamp, and offline_practice_report

- [x] `domain.py`: add `BehaviourMetricSummary(EvidenceModel)` — `metric: str`, `summary: RateObservation |
      DistributionObservation`, `classifications: dict[str, NonNegativeInt]`; add `team: str` and
      `metrics: tuple[BehaviourMetricSummary, ...]` to `RepositoryPracticeEvidence`; add
      `metrics: tuple[BehaviourMetricSummary, ...]` to `ActorRepositoryReadiness`
- [x] `evidence.py`: factor classification counting out of `RepositoryEvidence.metric` into a helper reused
      by new `metric_summaries(cached, metrics)`; add `actor_slice(evidence, login)` filtering both fact
      tuples by casefolded `author_login` into a `Merges`; `practices()` gains `team` and `metrics`
      parameters; `actor_readiness()` gains `metrics` and fills each row's per-repo summaries from the
      paired evidence's actor slice — per repository only, never combined across repositories
- [x] `evidence.py`: add `offline_practice_report(configuration, window) -> PracticeEvidenceReport`
      assembling the full report offline (configured repositories → cached evidence → stored state →
      `practices` with `repository_owners(configuration)` and `behaviour_metrics(configuration.traceability)`
      → `actor_readiness`); `cli.evidence_report` delegates to it on the default path and passes
      owners/metrics on the refresh path; point `repository_drill_down` at `metric_summaries` so report and
      JSON share one computation
- [x] `docs/architecture.md`: scope-boundary note — per-actor metrics LIST observations per repository,
      never combine across repositories, never order people
- [x] write/update tests: summaries equal `.metric()` output; actor slice honours casefolded logins, direct
      commits, and bot exclusion; `offline_practice_report` end-to-end on fixtures; JSON snapshot updates in
      `tests/test_cli.py`
- [x] run `uv run poe check` - must pass before task 4

### Task 4: FastAPI service (metrics-serve)

- [x] `pyproject.toml`: add `[project.optional-dependencies] service = ["fastapi>=0.115", "uvicorn>=0.30"]`;
      add `fastapi`, `uvicorn`, `httpx` to the dev group; add `metrics-serve = "metrics.service:main"` to
      `[project.scripts]`; add a per-file ruff ignore for `service.py` only if FastAPI idioms require it
- [x] `src/metrics/service.py`: `WEEKS_OPTIONS = (1, 4, 8, 12, 26)` filtered at startup against
      `lookback.maximum_days`; view models (`OverviewSummary`, `RepositoryRow`,
      `RepositoryDetail`, `ActorRow`, `TeamRow`, `TeamDetail`); `ReportBundle` (report + indexes + `built_at`
      + source stamp) built from `evidence.offline_practice_report`; `WindowCache` with `threading.Lock`,
      stamp/TTL invalidation
- [x] `service.py`: `create_app(configuration, cache) -> FastAPI` exposing `/healthz`, `/windows`,
      `/overview`, `/repositories`, `/repositories/{repository}` (404 unknown; includes contributor rows and
      `unavailable` detail), `/actors` (alphabetical only), `/actors/{login}` (casefolded match, 404
      unknown), `/teams` (adds per-team label counts — permitted by this plan's reversal), `/teams/{team}`
      (repo rows, actor rows with contributions within the team's repos, label distribution); all data
      endpoints take validated `?weeks=` defaulting to 4
- [x] `service.py`: `main()` — own small argparse (`--config` repeatable, `--host` default 127.0.0.1,
      `--port` default 8000, `--logging`, `--max-bundle-age` default 3600), `config.load_configuration`
      directly (not via cli), refuse a configuration without teams, pre-warm the default window, then
      `uvicorn.run`
- [x] `docs/architecture.md`: dated reversals — "No dashboards" (~line 1200): a read-only offline service +
      UI over evidence data is in scope, service never contacts GitHub, DECIDED; "NO TEAM ROLL-UP" (~1206):
      per-team label counts permitted for display; combined team verdicts, scores, and orderings of teams or
      people remain excluded
- [x] write tests `tests/test_service.py` (TestClient with `offline_practice_report` monkeypatched to small
      fixture reports): every endpoint happy path, 404s, weeks validation (422 off-list), cache reuse vs
      stamp-change rebuild vs TTL rebuild, teams refusal, `main()` with `uvicorn.run` mocked — 100% coverage
- [x] run `uv run poe check` - must pass before task 5

### Task 5: UI scaffold (ui/)

- [x] create `ui/package.json` (including a `check` script running lint, `tsc --noEmit`, vitest, and build in
      sequence — the one-step gate mirroring `poe check`), `ui/next.config.mjs`, `ui/tsconfig.json` (strict,
      `@/*` alias), `ui/postcss.config.mjs`, `ui/.eslintrc.json` (next/core-web-vitals), `ui/.gitignore`
      (node_modules/.next); root `.gitignore` gains `.metrics/` if missing
- [x] port `globals.css` and `layout.tsx` from the predecessor minus cloaking: `<html className="dark">`,
      `bg-slate-950 text-slate-100`, sticky nav shell; `ui/tailwind.config.ts` defines semantic tokens
      `rag-red #f87171`, `rag-amber #fbbf24`, `rag-green #4ade80`, `rag-none #64748b`, `accent #818cf8`
- [x] adapt `Navigation.tsx`: org name, links Home / Repositories / Actors / Teams, slot for the week
      selector (task 6)
- [x] verify `uv run poe check` still passes untouched from repo root
- [x] run `npm install` then `npm run check` in `ui/` (at this task the `check` script covers lint, type
      check, and build; task 6 adds vitest to it) - must pass before task 6

### Task 6: UI API client, types, RAG maps, week selector

- [x] `ui/src/lib/api.ts`: port the predecessor pattern (`API_URL` env default `http://localhost:8000`,
      `cache: 'no-store'`, typed `apiFetch<T>`); one function per service endpoint, all taking `weeks`
- [x] `ui/src/lib/types.ts`: TS mirror of the service responses (snake_case as emitted, optionals per
      `exclude_none`)
- [x] `ui/src/lib/rag.ts`: adapt `repoHealthUtils.ts` — badge/dot/hex maps keyed
      `green|amber|red|cannot_assess` (cannot_assess → slate) plus a `borderClass` map
      (`border-l-4 border-l-rag-*`) replacing every emoji use; `ui/src/lib/format.ts`: percent from rate
      observations, unit-aware distribution values, dates
- [x] `ui/src/lib/weeks.ts` + `ui/src/components/NavWeekSelector.tsx`: port `weeksPreference.ts` and
      `NavWeekSelector.tsx` — options fetched from `GET /windows`, selection carried as `?weeks=` on every
      drill-through link, cookie fallback for the default
- [x] write vitest tests (`ui/vitest.config.ts`, `ui/src/lib/__tests__/`): RAG maps totality, weeks
      resolution priority, formatters; add `vitest run` to the `check` script so the gate is complete
- [x] run `npm run check` in `ui/` - must pass before task 7

### Task 7: Shared components

- [x] port `MetricCard.tsx`, `charts/TrendChart.tsx`, `charts/SummaryPieChart.tsx` (keep zero-slice legend
      dimming), `FilterSearchBox.tsx` (`'use client'` where recharts/state is used)
- [x] create `RAGCard.tsx` / RAG row treatment: `border-l-4` colour bar + small `text-xs uppercase` label —
      no coloured emojis anywhere
- [x] create `EntityHeader.tsx` ("where you are now"): kind badge (REPOSITORY/ACTOR/TEAM in the
      section-heading style), mono name, context line with links; no breadcrumbs
- [x] create one consolidated `Section.tsx` and `EmptyState.tsx` (predecessor duplicated these three ways);
      a11y while porting: sort headers as `<button aria-sort>`, tooltips as CSS group-hover with
      `aria-label` instead of bare `title=`
- [x] run `npm run check` in `ui/` - must pass before task 8

### Task 8: Overview page /

- [x] `ui/src/app/page.tsx`: org header (name, window dates, unavailable count from `/overview`), stat cards
      (repositories, teams, actors, merges in cohort), label-distribution donut via `SummaryPieChart`
- [x] `ui/src/components/RepositoriesTable.tsx` (client): team, repo (mono link), RAG left border, merged
      count, currently-open/stale PRs, findings occurrences; `FilterSearchBox` + label filter; default order
      team→repo
- [x] `ui/src/components/ActorsTable.tsx` (alphabetical only, login mono link, repository count) and
      `ui/src/components/TeamsList.tsx` (identifier, repo count, actor count, label counts) — all links carry
      `?weeks=`
- [x] run `npm run check` in `ui/` - must pass before task 9

### Task 9: Repository page /repositories/[repository]

- [x] `ui/src/app/repositories/[repository]/page.tsx`: `EntityHeader` with team link and readiness label (colour
      bar + text); `AssessmentSection.tsx` grouping conditions blocking/caution/clear, each row RAG-bordered
      with its detail string
- [x] stat cards: merges reported/excluded, direct commits, all four open-PR counts with `fetched_at` and
      measured window, security open counts per family, codeowners, maintenance windows, sonar gate/ratings
- [x] nine-metric grid from the repo's `metrics` (rate % with numerator/denominator, or median/p75 with unit
      and sample size); `FindingsTable.tsx` (rule, severity, actor link, occurrences, GitHub PR links);
      `ContributorsTable.tsx` (actor link, contributions, blocking; contributions-descending — the contract's
      own ordering)
- [x] `encodeURIComponent` on repo slugs; `notFound()` for unknown repos; explanatory empty state for
      repos in `unavailable`
- [x] run `npm run check` in `ui/` - must pass before task 10

### Task 10: Actor page /actors/[login]

- [x] `ui/src/app/actors/[login]/page.tsx`: `EntityHeader` (mono login, "N merges across M repositories" —
      counts, not scores); casefolded lookup with canonical spelling displayed; `notFound()` unknown
- [x] repositories table: repo link, team link, RAG border, contributions, blocking occurrences
- [x] per-repository behaviour metrics: one section per repository (contract's contributions order) showing
      the actor's nine metric summaries in that repo — never merged across repos, never compared against
      other people
- [x] run `npm run check` in `ui/` - must pass before task 11

### Task 11: Team page /teams/[team] and workflow docs

- [x] `ui/src/app/teams/[team]/page.tsx`: `EntityHeader` (identifier, repo count, actor count); label
      distribution donut + counts (per-team counts, no combined verdict or team ordering); repositories
      table (shared component pre-filtered); actors table (alphabetical, contributions within this team's
      repos); `notFound()` unknown
- [x] `README.md` + `ui/README.md`: full loop — `metrics collect` (now also caching open-PR state) →
      `uv sync --extra service` → `uv run metrics-serve --config ...` → `API_URL=http://localhost:8000 npm
      --prefix ui run dev`; `evidence` offline default / `--refresh`; the design-token map and the
      guardrails the UI must keep honouring
- [x] run `npm run check` in `ui/` and `uv run poe check` from root - must pass before task 12

### Task 12: Trends on the repository page

- [x] `service.py`: add `GET /repositories/{repository}/trend?period_days=&periods=` calling `metrics.trend`'s
      offline series, cached per repo beside the window bundles; 404 unknown repo
- [x] write/update tests in `tests/test_service.py` for the trend endpoint (happy path, 404, param
      validation) keeping 100% coverage
- [x] `ui/`: trend API function + types; repo page grows a trends section rendering `TrendChart` per-period
      throughput (pull-request vs direct-commit merges) and per-metric period series with the delta basis the
      contract carries — per-repo only, section renders only when the endpoint returns periods
- [x] run `uv run poe check` and `npm run check` in `ui/` - must pass before task 13

### Task 13: Verify acceptance criteria

- [x] run `uv run poe check` — full Python gate passes
- [x] run `npm run check` in `ui/` — full UI gate passes
- [x] confirm all three architecture.md reversals are recorded with 2026-09-01 dates and the scope
      boundaries still forbid rankings, cross-repo averaging, and combined team verdicts
- [x] update `README.md` if any user-facing command or flag changed beyond what task 11 documented
