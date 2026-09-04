# Plan: Production service filter, badge and column

## Overview

Read HMCTS's `environment-approvals.yml` — the public list of repositories approved to deploy to
production — and carry "this is a production service" through the service onto the pages: a permanent
royal-blue `Production` toggle at the head of the repositories filter bar, and a `Production` badge in a
new column to the right of readiness on the repositories list, the team page, the contributor page and
the repository detail header.

The list is fetched by the SERVICE and refreshed by the window warmer, so it moves on the same interval
every other refresh does (`--warm-interval`, 300 seconds by default) rather than waiting for a
collection. That is a deliberate, dated exception to `service.py`'s offline invariant and is recorded as
one in Task 3 — it is credential-free, it is not evidence about a reporting window, and a fetch that
fails costs the badges rather than the page.

`metrics evidence` DELIBERATELY DOES NOT GAIN A PRODUCTION COLUMN. The fetch lives in the service, so
the text report cannot state it, and inventing a second source at collect time so that it could would
be a much larger change than was asked for. Do not "fix" this by adding it to `render.py`.

## Context

- Files involved:
  - `src/metrics/production.py` (new), `tests/test_production.py` (new)
  - `src/metrics/config.py`, `tests/test_config.py`, `metrics.example.yaml`
  - `src/metrics/service.py`, `tests/test_service.py`
  - `docs/architecture.md`, `README.md`, `ui/README.md`
  - `ui/src/lib/types.ts`, `ui/src/lib/rows.ts`, `ui/src/lib/production.ts` (new), `ui/tailwind.config.ts`
  - `ui/src/components/ProductionBadge.tsx` (new), `ui/src/components/RepositoriesTable.tsx`,
    `ui/src/components/ActorRepositoriesTable.tsx`, `ui/src/components/EntityHeader.tsx`
  - `ui/src/app/repositories/[repository]/page.tsx`, `ui/src/app/contributors/[login]/page.tsx`
  - tests under `ui/src/lib/__tests__` and `ui/src/components/__tests__`
- Related patterns:
  - The toggle's shape is the readiness filter bar as it stood at commit `b5afac1`, before
    `649f202` ("feat: BI filters") replaced it with the dismissible donut chips:
    `aria-pressed`, a dot, the words, a `tabular-nums` count, and `bg-slate-800 text-slate-400` when
    inactive against `bg-slate-700 text-slate-100` when active. Read that revision of
    `ui/src/components/RepositoriesTable.tsx` before writing the button.
  - `WindowCache.bundle` and `WindowCache.warm` are the pattern the production cache copies: read the
    held thing outside every lock, check again inside it, and refresh a MARGIN ahead of expiry so the
    warmer rebuilds on the wake before a reader would have met a stale value.
  - `ActorDetail.teams` is the precedent for carrying a per-repository fact beside a contract model the
    service must not modify: `ActorDetail.production` sits beside it and is read the same way.
  - `rag.ts` is the precedent for a presentation module — a label's word, badge classes, dot class and
    hex in one file, with no hex literal in any component. `lib/production.ts` is its parallel. This is
    NOT a job for `tone.ts`: production is an attribute of a repository, not a graded figure, and
    `tone.ts` is explicitly the file where a FIGURE's colour is decided.
  - `answerOrder` in `rows.ts` is how a three-valued column sorts (`undefined` passes through so
    `sorted` holds it back from both ends).
- Dependencies: none new. `requests` and `pyyaml` are already direct dependencies.
- Constraints:
  - `GH_TOKEN` is not in this environment and the list is a public raw URL, so every test stubs the
    HTTP call. No task here requires a live fetch.
  - Both gates enforce 100% coverage (`fail_under = 100` for pytest, and `vitest.config.mts` pins
    statements, branches, functions and lines at 100% for `ui/src/**`). Every new branch needs a test.
  - Every service route is registered `response_model_exclude_none`, so an OPTIONAL field arrives as a
    MISSING KEY. Mirror it in `types.ts` as `field?: T`, never `T | null`, and guard with `== null`.
  - `ui/src/lib` is scanned by `tailwind.config.ts`'s `content`, so class strings in
    `lib/production.ts` are emitted. `lib/__tests__/tailwind.test.ts` asserts that every directory
    under `src` is scanned; adding a file to an already-scanned directory does not affect it.

## Development Approach

- Code then tests, per task: the Python half is threading one value through existing shapes and the UI
  half is markup, and neither is arithmetic a test can drive out ahead of the code. The one exception is
  Task 1's parsing, which IS pure logic over a real document — write the fixture and its expectations
  first there.
- Complete each task fully before moving to the next.
- `production` IS OPTIONAL AND ABSENCE MEANS UNREAD. A repository is `true` when the fetched list holds
  it, `false` when the list was read and does not, and the field is ABSENT when no list could be read at
  all. Never default it to `false`: "not approved for production" and "nobody could say" are different
  answers, and this project's rule is that unavailable data never becomes zero. Both render no badge —
  that is what was asked for — but the filter and the count can only be honest about the difference if
  the field keeps it.

## Validation Commands

- `uv run poe check`
- `npm --prefix ui run lint`
- `npm --prefix ui run typecheck`
- `npm --prefix ui run test`

`npm --prefix ui run check` is the UI's real gate and runs all three of the above plus `next build`. Its
build step fails intermittently on this virtiofs mount — `File exists (os error 17)` under `.next`, on a
different chunk each run, which is the environment and not the change. Run `check` where the build
works, and the three commands above where it does not; they are the parts that judge the change. See
`ui/README.md`.

## Implementation Steps

### Task 1: Read the production list

- [x] Write `src/metrics/production.py` with the URL, environment key and timeout as named module
      constants: `PRODUCTION_LIST_URL` (the HMCTS
      `cnp-jenkins-config/refs/heads/master/environment-approvals.yml` raw URL),
      `PRODUCTION_ENVIRONMENT = "prod"` and a request timeout matching `github.py`'s.
- [x] Add `parse_repository_url(url: str) -> tuple[str, str] | None`, returning the CASEFOLDED
      `(organization, repository)` pair with any `.git` suffix stripped, and `None` for anything it
      cannot read as an owner and a name. Casefolding is not optional: the real document contains
      `https://github.com/HMCTS/adoption-shared-infrastructure.git` among 200-odd lowercase `hmcts`
      entries, so a case-sensitive match would silently drop that repository's badge.
- [x] Add `parse_production_repositories(document: str) -> frozenset[tuple[str, str]]`, reading the
      `prod:` sequence and ignoring every other top-level key. An entry it cannot read costs a debug
      log line and that entry only — one malformed line must not discard 250 good ones — but a document
      that is not a mapping, or that holds no `prod:` key, raises rather than returning an empty set,
      because "the file changed shape" and "no repository is production" must not read alike.
- [x] Add `fetch_production_repositories(url: str) -> frozenset[tuple[str, str]] | None`: one
      unauthenticated GET with a timeout, returning `None` and logging a warning for a network failure,
      a non-200, or a document that could not be parsed. It carries NO credential and must not be given
      one — the file is public, and sending a token to a raw content host is a leak with no benefit.
- [x] Write `tests/test_production.py`: the real document's shape parsed to pairs (including the
      mixed-case entry folding to the same pair as its lowercase siblings and a `.git`-less URL), a
      malformed entry skipped while its neighbours survive, a document with no `prod:` key raising, and
      `fetch_production_repositories` returning `None` for each of a `RequestException`, a non-200 and
      an unparseable body — asserting the warning is logged and that no `Authorization` header was sent.
- [x] run the project test suite - must pass before task 2

### Task 2: Configure where the list is read from

- [x] Add `production_list_url: str | None = PRODUCTION_LIST_URL` to `Configuration` in
      `src/metrics/config.py`, beside `sonar_organization`, which is the precedent for a single
      optional string carrying a default the common case does not restate. Document in its comment that
      the default is an HMCTS URL stated as a policy default to argue with — as
      `cohort.excluded_authors` and `traceability.reference_patterns` already are — and that `null`
      turns the fetch off, which is what an organisation with no such list wants.
- [x] Add the key to `metrics.example.yaml` with a comment saying what the document is, that the fetch
      is unauthenticated, and that `null` disables it.
- [x] Write tests in `tests/test_config.py`: the default is present when the key is omitted, an explicit
      URL wins, and `production_list_url: null` loads as `None`.
- [x] run the project test suite - must pass before task 3

### Task 3: Hold the list in the service and refresh it with the warmer

- [x] Amend `service.py`'s module docstring. The invariant currently reads "READ-ONLY AND OFFLINE
      ALWAYS … there is no client, no session and no credential anywhere in this module", and this task
      makes that false. Replace it with the invariant that now holds: no credential and no GitHub API
      client anywhere, every EVIDENCE response still assembled from the caches alone, and ONE
      credential-free fetch of a public classification document that is not evidence about any window
      and whose failure costs a badge rather than a figure.
- [x] Add a dated decision to `docs/architecture.md` recording that reversal in the form the file
      already uses: what the invariant was, what it is now, why the production list is not a
      current-state source collected by `collect` (it is one organisation-wide document, not a
      per-repository state, and the user asked for it to move on the refresh interval rather than the
      collection cadence), and that a fetch failure is reported as an ABSENT field rather than as
      `false`.
- [x] Add a `ProductionList` build to `WindowCache`, holding the fetched pairs beside `built_at` and
      guarded by a lock of its own, with `production_list(margin: timedelta = timedelta(0))` reading it
      exactly as `bundle` and `warm` read a span: held value returned if it will still be usable at
      `now + margin`, otherwise refetched under the lock with a second check inside it. There is no
      source stamp to compare against — the source is remote — so age is the whole rule.
- [x] Keep the LAST GOOD LIST when a refresh fails. A transient 502 from a content host must cost the
      badges nothing; only a service that has never once read the list serves an absent field. Log the
      failure at warning level so a list that has been failing for a day is visible.
- [x] Return the held value unchanged when `configuration.production_list_url` is `None`, without
      fetching: the field is then absent on every row, which is what a configuration that names no list
      is saying.
- [x] Refresh the list from `warm_spans` on every wake, passing the interval as the margin exactly as
      the spans do, and inside the same blind `except Exception` that already keeps a span's failure
      from taking the warmer's thread with it.
- [x] Write tests in `tests/test_service.py`, beside the existing warm tests: a first read fetches, a
      second read inside the margin does not, a read whose held value would expire before the next wake
      refetches, a failed refetch keeps the previous list and logs, a cache that has never fetched
      returns `None`, a configured `None` URL never calls the fetcher, two concurrent reads of a cold
      list fetch once between them, and `warm_spans` refreshing the list alongside the spans.
- [x] run the project test suite - must pass before task 4

### Task 4: Serve production on the rows, the detail and the actor

- [x] Add `production: bool | None = None` to `RepositoryRow` and to `RepositoryDetail` in
      `service.py`, documenting in each docstring that absent means NO LIST COULD BE READ and is not
      `false` — the same rule the block already states for every count above it. On `RepositoryDetail`
      it is set on BOTH branches, the unavailable one included: whether a repository deploys to
      production is not a fact about the reporting window, so a span with no evidence still knows it.
- [x] Add `production: tuple[str, ...] | None = None` to `ActorDetail`, carrying which of THIS
      PERSON'S repositories are production, and say in the docstring that it sits beside `teams` for
      `teams`' own reason — it is accounting the contract's `ActorReadiness` cannot state, and the
      contract model is passed through unchanged.
- [x] Thread the list through: give `repository_row` and `repository_detail` a
      `production: frozenset[tuple[str, str]] | None` parameter and resolve a row by looking up
      `(configuration.organization.casefold(), repository.casefold())`, passing `None` straight through
      as an absent field. Update `team_detail` and every call site.
- [x] Read the list in the endpoints that need it — `serve_repositories`, `serve_repository`,
      `serve_team`, `serve_actor` — through the `State` dependency they can already take, so the value
      is as fresh as the last refresh rather than as old as the span's bundle.
- [x] Write tests in `tests/test_service.py`: a production repository serves `production: true`, a
      configured repository absent from the list serves `false`, an unread list omits the key entirely
      from all four responses, the match is case-insensitive on both halves of the pair, a repository
      the window could not report still carries its production answer, and `ActorDetail.production`
      lists only the person's own production repositories.
- [x] run the project test suite - must pass before task 5

### Task 5: The badge, its palette, and the UI mirror

- [x] Mirror the new fields in `ui/src/lib/types.ts`: `production?: boolean` on `RepositoryRow` and
      `RepositoryDetail`, `production?: string[]` on `ActorDetail`. Optional, not nullable, because
      every route is served `response_model_exclude_none` — and guard with `== null`.
- [x] Add the royal blue to `ui/tailwind.config.ts` as a named colour (`royal: '#4169e1'`) beside the
      `rag` palette and `accent`, with the surface and border shades the badge needs. It is deliberately
      NOT in the `rag` group: it is an attribute of a repository, not one of the report's verdicts, and
      putting it there would offer it to a component looking for a grade.
- [x] Write `ui/src/lib/production.ts` holding the whole decision: the word (`Production`), the badge
      class string, the toggle's active and inactive class strings, the dot class, and the hex for
      anywhere a class cannot reach. State in the module docstring why this is not `tone.ts` — a figure's
      colour is decided there and this is not a figure — and why the badge keeps a border, which is
      `RAGLabel`'s reason: a colour alone does not survive a monochrome print.
- [x] Write `ui/src/components/ProductionBadge.tsx`: the same shape as `RAGLabel` (an
      `inline-block rounded px-1.5 py-0.5 text-xs uppercase tracking-wide whitespace-nowrap` span) with
      the production classes and NO dismiss control. It renders `null` for `false` and for `undefined`
      alike — there is no non-production badge, so an absent answer and a negative one look the same to
      a reader, which is intended.
- [x] Write `ui/src/lib/__tests__/production.test.ts` and a component test asserting the badge's
      markup, that both `false` and `undefined` render nothing, and that the class strings resolve to
      the configured colour rather than a literal.
- [x] run `npm --prefix ui run lint`, `npm --prefix ui run typecheck` and `npm --prefix ui run test` -
      all must pass before task 6

### Task 6: The permanent Production toggle, and a filter bar that is always there

- [x] Add the dimension to `ui/src/lib/rows.ts`: `PRODUCTION_PARAMETER = 'production'`, the single value
      the toggle writes, and `parseProduction(read)` returning whether it is on. Keep it OUT of
      `ESTATE_FILTERS` — that list is the six donuts, each with options, a colour and a band function,
      and every one of them raises a dismissible chip. This is a two-state toggle with no donut, so
      folding it in would give it a chip with an x button, which is the one thing it must not have.
- [x] Extend `filterRepositories` to take the production flag and AND it with the term and the six
      dimensions, matching only rows whose `production` is `true` — so a repository whose answer could
      not be read is excluded from the filter rather than assumed either way.
- [x] Add `productionCount(rows, term, filters)`, counting production rows among those matching the term
      and the OTHER active dimensions with the production dimension itself excluded. That is the rule
      the old readiness bar counted by (`stateCounts(filterRepositories(rows, term, null))`): a count
      that included its own filter would read `n` before a click and `n` after it, saying nothing.
- [x] Render the bar UNCONDITIONALLY in `RepositoriesTable`, dropping the `chips.length > 0` guard, with
      the Production toggle as its first item and the dismissible chips after it. The bar no longer
      appears and disappears as donut filters are toggled, which is what it did before `649f202` and
      what makes the donut chips feel like additions to a control rather than the control's existence.
- [x] Draw the toggle as a `button` with `aria-pressed`, the dot, the word and a `tabular-nums` count —
      the shape at commit `b5afac1` — royal blue when active and greyed when inactive, with no `X` and
      no way to remove it. Update the group's `aria-label`, which currently says "Active filters" and is
      now a bar holding one control that is always present.
- [x] Update the empty state's detail line: it currently says "Clear the term, or a filter above", and
      the production toggle is now one of the things a reader may need to clear.
- [x] Write tests in `ui/src/lib/__tests__/rows.test.ts` (parse, filter, count — including that a row
      with an absent answer is filtered out and not counted) and in
      `ui/src/components/__tests__/RepositoriesTable.test.tsx` (the bar renders with no filters active,
      the toggle carries its count and `aria-pressed`, it has no remove button, clicking it writes and
      then clears the parameter while leaving `weeks`, the term and an active donut chip untouched).
- [x] run `npm --prefix ui run lint`, `npm --prefix ui run typecheck` and `npm --prefix ui run test` -
      all must pass before task 7

### Task 7: The Production column, and the badge on the detail pages

- [x] Add a `Production` column to `RepositoriesTable`'s `COLUMNS` immediately after `readiness`,
      sortable like every other header in that table, reading through a three-valued order function in
      the shape of `answerOrder` so an unread answer is held back from both ends of the sort. The cell
      is the badge, which is empty for a non-production repository.
- [x] Add the same column to `ActorRepositoriesTable`, again directly right of Readiness, fed by a new
      `production?: readonly string[]` prop. That table has no sortable headers and gains none: its
      order is the contract's own contributions-descending, for the reason its docstring gives.
- [x] Pass `detail.production` from `ui/src/app/contributors/[login]/page.tsx` into
      `ActorRepositoriesTable`. The team page needs no change — it renders the shared
      `RepositoriesTable` over `TeamDetail.repositories`, which already carry the field from Task 4.
- [x] Add an optional `production` badge to `EntityHeader`, rendered directly after `RAGLabel`, and pass
      `detail.production` from `ui/src/app/repositories/[repository]/page.tsx`. The header is built
      before the no-evidence branch, so a repository the span cannot report still shows its badge.
- [x] Write tests: the column's header and its position relative to Readiness on both tables, a
      production row rendering the badge and a non-production one rendering an empty cell, the sort
      putting unread answers last in both directions, the badge appearing in the repository header
      (including on the unavailable branch), and the contributor page passing the list through.
- [x] run `npm --prefix ui run lint`, `npm --prefix ui run typecheck` and `npm --prefix ui run test` -
      all must pass before task 8

### Task 8: Document it

- [x] Document `production_list_url` in `README.md` beside the other configuration keys: what the
      document is, that the fetch is unauthenticated and service-side, that it refreshes on
      `--warm-interval` rather than on a collection, that `null` disables it, and that an unreadable
      list means no badge rather than a negative one.
- [x] Document the new column and the permanent toggle in `README.md` where the repositories list and
      the filter bar are described, and state that `metrics evidence`'s text output carries no
      production column and why.
- [x] Add the royal blue to `ui/README.md`'s "Design tokens" section, which currently names the five
      `rag-*` hexes and `accent` as the site's whole palette, and say which module resolves it —
      `lib/production.ts`, beside `rag.ts` and `tone.ts` — and why it is not in the RAG group.
- [x] run the full test suite and the linter

### Task 9: Verify acceptance criteria

- [x] run `uv run poe check`
- [x] run `npm --prefix ui run lint`, `npm --prefix ui run typecheck` and `npm --prefix ui run test`
- [x] confirm each acceptance point against the code: the toggle is first in a bar that is always shown,
      is a toggle with no dismiss control, is greyed when inactive and royal blue when active, and
      carries a count; the badge is a badge with no `x`, appears in a column right of readiness on the
      repositories list, the team page and the contributor page, appears right of readiness in the
      repository header, and is absent for every non-production repository; and the list refreshes on
      the same interval as the window bundles
