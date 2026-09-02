# Plan: Navigation and layout, round two

## Overview

Make the window-span selector respond immediately, split the three estate lists onto routes of their
own with repositories as the landing page, and tighten the repository page's layout. The slowness and
the missing feedback are two faults with one symptom, and both are fixed: the service keeps every span
warm so there is usually nothing to wait for, and the UI says so on the occasions there is.

## Context

- Service: `src/metrics/service.py` — `WindowCache`, `create_app`, `main`, `parse_arguments`. Tests in
  `tests/test_service.py`.
- Routes: `ui/src/app/page.tsx` (the current overview, which holds all three lists),
  `ui/src/app/repositories/[repository]/page.tsx`, `ui/src/app/actors/[login]/page.tsx`,
  `ui/src/app/teams/[team]/page.tsx`.
- Components: `ui/src/components/NavWeekSelector.tsx`, `Navigation.tsx`, `Section.tsx` (`Section` and
  `Panel`), `MetricsGrid.tsx`, `AssessmentSection.tsx`, and the tables that link to a person —
  `ActorsTable`, `TeamActorsTable`, `ContributorsTable`, `FindingsTable`, `ActorRepositoriesTable`.
- Logic: `ui/src/lib/metrics.ts` (`metricTone`), `ui/src/lib/tone.ts`, `ui/src/lib/weeks.ts`
  (`withWeeks`), `ui/src/lib/api.ts`.
- Related patterns: the round-one plan,
  `docs/plans/20260902-repository-page-visual-upgrade.md`, established that tone comes from the
  assessment where the policy grades a figure and from a UI threshold where it does not. Task 7 here
  is the second kind.

## What is actually slow

Worth stating, because the fix is not where it looks:

- `WindowCache` holds **one lock for every span**. A cold span's build walks every configured
  repository's cached facts under that lock, so while it runs, every other request — including ones
  for a span already built — waits behind it.
- `DEFAULT_BUNDLE_AGE_SECONDS` is 3600, so **every span goes stale hourly** and the next reader pays
  the whole rebuild, even on the span they were already reading.
- Only the default span is built before the service listens. The other four are cold until somebody
  asks for one.
- The overview page fetches four lists from the same bundle, so a cold span is entered four times at
  once and three of those requests wait on the lock for nothing.

## Decisions taken

1. **Background warmer plus per-span locks, and UI feedback besides.** The warmer is what removes the
   wait; the feedback covers the first load and the rebuild after a collection lands, which can still
   be cold. Neither alone is the answer.
2. **Repositories is the landing page.** The organisation header, the four estate tiles and the
   readiness donut go with it. `/contributors` and `/teams` carry the same organisation header and
   their list, nothing else. The Home button goes.
3. **`/actors/[login]` becomes `/contributors/[login]`.** Round one deliberately deferred the route
   rename, but a `/contributors` list above an `/actors/[login]` detail is a split personality, and
   this is the change that already touches routing. Bookmarked actor URLs will break; the service's
   own `/actors` endpoints and field names are untouched.
4. **`description-quality` and `traceability-reference` read green at 100%.** They are the two rates
   the readiness policy never grades, so there is no condition to borrow a tone from and a UI rule is
   the only place the judgement can live.

## Development approach

- Code then tests, per task, in the layout the repository already keeps: pure logic in `ui/src/lib`
  with its tests under `ui/src/lib/__tests__`, markup in `ui/src/components` asserted through
  `renderToStaticMarkup`, and the service's own behaviour in `tests/test_service.py`.
- The service tasks come first: the UI feedback in task 3 is meant to be the exception rather than the
  usual path, and it is easier to judge once there is nothing to wait for.
- Complete each task fully before moving to the next.

## Validation Commands

- `uv run poe check`
- `npm --prefix ui run lint`
- `npm --prefix ui run typecheck`
- `npm --prefix ui run test`

NOT `npm --prefix ui run check`. Its `next build` step fails on this mounted filesystem at the
`output: 'standalone'` trace-copy for environment reasons unrelated to the code, and the three
commands above are the parts that judge the change.

## Implementation Steps

### Task 1: One lock per span, not one for the cache

- [x] Replace `WindowCache.lock` with a lock per span and a lock per series key, each handed out
      through a small guarded registry, so a cold 26-week build no longer blocks a request for a span
      already held
- [x] Keep the stamp read outside every lock, as it already is, and keep the double-check inside: a
      request that waited for a build must return what the builder produced rather than build again
- [x] Keep `retain` and the least-recently-read reordering of `self.series` under a single lock of
      their own — the eviction order is shared state and is not per key
- [x] Rewrite the class docstring: the one-lock-for-every-span reasoning it currently records is what
      this task reverses, and the new bound is that one span builds once while another span is free to
      build beside it
- [x] Add tests in `tests/test_service.py` for two spans building concurrently, for one span under
      contention building exactly once, and for a series eviction staying correct under the split
- [x] run `uv run poe check` — must pass before task 2

### Task 2: Keep every span warm

- [x] Add `WindowCache.warm(weeks, margin)`: build and store the span's bundle when none is held, when
      the stamp has moved, or when the held one is within `margin` of `maximum_age`, and return
      without building otherwise — `bundle()` cannot serve this, because it deliberately returns a
      bundle that is still usable
- [x] Add a warmer that runs on a daemon thread: build every offered span once at start, then wake on
      an interval and `warm` each of them, refreshing before a reader meets a stale one rather than
      after
- [x] Stop it on application shutdown through a `threading.Event` and a FastAPI lifespan handler in
      `create_app`, so a test that builds an app does not leak a thread
- [x] Log at info when a span is warmed and at warning when a warm raises, and never let an exception
      kill the thread: a cache file that cannot be read must cost a log line, not every later refresh
- [x] Add `--warm-interval` to `parse_arguments`, defaulting to a value below `--max-bundle-age`, and
      validate it the way `positive_seconds` validates the age
- [x] Keep `main`'s synchronous build of the default span before uvicorn listens: a cache the service
      cannot read must still fail while a human is watching, rather than on a background thread
- [x] Add tests for the warmer building every offered span, refreshing on a changed stamp, refreshing
      inside the margin, not rebuilding a fresh bundle, surviving a build that raises, and stopping
      when the event is set
- [x] run `uv run poe check` — must pass before task 3

### Task 3: Say when a page is waiting

- [x] Wrap the `router.replace` in `ui/src/components/NavWeekSelector.tsx` in `useTransition`, and
      while `isPending` dim the button group and mark it `aria-busy` — the optimistic `selected` state
      already highlights the pressed button, and this is the other half: the page has not arrived yet
- [x] Keep the existing `useEffect` that drops the optimistic value when a render arrives at a
      different span; the transition ends on the same render, so the two must not fight
- [x] Add a `loading.tsx` skeleton for each route segment — the three list routes, the repository
      detail, the contributor detail and the team detail — drawn from `Panel` so a loading page has
      the same bones as the page it becomes
- [x] Add tests for the selector's pending markup and for one skeleton rendering without props
- [x] run `npm --prefix ui run lint`, `npm --prefix ui run typecheck` and `npm --prefix ui run test` —
      all three must pass before task 4

### Task 4: Split the lists onto their own routes

- [x] Add `ui/src/app/repositories/page.tsx` carrying what the overview carries today: the
      organisation header with the week selector, the four estate tiles, the readiness donut and the
      repositories table with its filter box
- [x] Add `ui/src/app/contributors/page.tsx` and `ui/src/app/teams/page.tsx`, each fetching
      `/windows`, `/overview` and its own list — three requests where the overview made five — and
      each rendering the same organisation header above its table
- [x] Extract that header into a component so the three pages cannot drift apart in what they claim
      about the span
- [x] Replace `ui/src/app/page.tsx` with a redirect to `/repositories`, carrying `?weeks=` through
      where one was given so a link to the old landing page does not silently change the window
- [x] Move `ui/src/app/actors/[login]/page.tsx` to `ui/src/app/contributors/[login]/page.tsx`,
      unchanged but for the route it lives at, and delete the old segment
- [x] Update every link to a person — `ActorsTable`, `TeamActorsTable`, `ContributorsTable`,
      `FindingsTable`, `ActorRepositoriesTable` and the repository page — to
      `/contributors/${encodeURIComponent(login)}`
- [x] Rewrite `ui/src/components/Navigation.tsx`: drop the Home link, point the logo at
      `/repositories`, and make the three links routes rather than `#` anchors; the comment explaining
      why they were anchors is now the record of why they no longer are
- [x] Drop the `id="repositories"`, `id="actors"` and `id="teams"` wrappers, which existed for those
      anchors
- [x] Update the affected tests in `ui/src/components/__tests__` and add a case asserting no component
      links to `/actors/`
- [x] run `npm --prefix ui run lint`, `npm --prefix ui run typecheck` and `npm --prefix ui run test` —
      all three must pass before task 5

### Task 5: Pair the repository page's sections

- [x] Put Merge gate beside Open pull requests, and Security alerts beside Maintenance, each pair in a
      `grid grid-cols-1 lg:grid-cols-2 gap-4` that stacks on a narrow viewport
- [x] Give the open pull-request cards a two-across grid inside their half rather than the four-across
      the full width allowed, so the four counts stay on two tidy rows instead of one cramped one
- [x] Check the paired sections line up when one is an `EmptyState` and the other is full — an
      unreadable gate beside a full open pull-request block is the common case on this estate
- [x] Update the repository page markup test for the new order and grouping
- [x] run `npm --prefix ui run lint`, `npm --prefix ui run typecheck` and `npm --prefix ui run test` —
      all three must pass before task 6

### Task 6: Behaviour above the assessment

- [x] Move the Behaviour section above the blocking, caution and clear groups, directly under the
      cohort row, so the measurements come before the verdict drawn from them
- [x] Update the page's own doc comment, which states the page renders the evidence block in the
      block's own order — that is no longer true of these two sections, and the reason is that a
      reader wants the figures before the grading
- [x] Update the repository page markup test to assert the new order
- [x] run `npm --prefix ui run lint`, `npm --prefix ui run typecheck` and `npm --prefix ui run test` —
      all three must pass before task 7

### Task 7: Green at 100% for the two ungraded rates

- [x] Add a rule to `metricTone` in `ui/src/lib/metrics.ts` for `description-quality` and
      `traceability-reference`: an observed rate whose numerator equals its denominator reads green,
      and everything else — short of 100%, not applicable, or a distribution — stays neutral
- [x] Read the observation rather than the formatted string: a rate that rounds to 100% is not 100%,
      and colouring it green would say the last unreferenced merge does not exist
- [x] Rewrite the module doc comment, which currently records that these two carry no colour because
      the policy never reads them; the reason they are a UI rule is exactly that, so the comment
      inverts rather than disappears
- [x] Keep the rule independent of the assessment, so it applies on the contributor page too, where
      there is no assessment and every other card is neutral
- [x] Extend `ui/src/lib/__tests__/metrics.test.ts` with both identifiers at 100%, just below 100%, at
      a rate that rounds to 100% but is not, `not_applicable`, and with no assessment present
- [x] run `npm --prefix ui run lint`, `npm --prefix ui run typecheck` and `npm --prefix ui run test` —
      all three must pass before task 8

### Task 8: Record the changes in the documentation

- [x] Add to `docs/architecture.md`, under the service's entry in "Scope boundaries", that the cache
      locks per span and that a warmer keeps every offered span built — with the reason, which is that
      one lock for the whole cache made a cold span's build everybody's wait, and that an hourly age
      made every span cold again
- [x] Record the route change in the same place: the three lists are routes rather than sections of
      one page, `/` redirects to `/repositories`, and `/actors/[login]` became
      `/contributors/[login]` while the service's `/actors` endpoints and field names did not move
- [x] Update the route table and the design tokens in `ui/README.md` for the new pages, the loading
      skeletons and the paired sections
- [x] Note the `--warm-interval` flag in `README.md` beside `--max-bundle-age`
- [x] run `uv run poe check` — must pass before task 9

### Task 9: Verify acceptance criteria

- [x] run `uv run poe check`
- [x] run `npm --prefix ui run lint`
- [x] run `npm --prefix ui run typecheck`
- [x] run `npm --prefix ui run test`
- [x] confirm each of the five requests is met: a span switch responds at once and shows it is working
      when it cannot, the three lists are separate pages with repositories as the landing page and no
      Home button, the repository page pairs merge gate with open pull requests and security alerts
      with maintenance, Behaviour sits above the blocking and caution and clear groups, and both
      `description-quality` and `traceability-reference` read green only at a true 100%
- [x] update `README.md` if any user-facing behaviour outside `ui/` changed beyond the new flag —
      nothing did: the warmer, the per-span locks and `--warm-interval` are the whole of it and task 8
      recorded them. The endpoint list was split back out of the warming paragraph it had been
      appended to.
