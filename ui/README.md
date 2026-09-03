# metrics dashboard

A Next.js app that reads `metrics-serve` and renders the evidence report as pages. It measures nothing of its own:
every figure on a page is one the service sent, or one a reader could reach by adding the report's own rows up
(`lib/contributor.ts` is the widest case, subtracting the contributor columns out of the summaries a person's row
carries), formatted by a pure function in `src/lib` — so a page and `metrics evidence` for the same span state one
thing rather than two that can drift.

## Run

```bash
npm --prefix ui install
API_URL=http://localhost:8000 npm --prefix ui run dev
```

The full loop — collecting, then serving, then this — is in the [root README](../README.md#dashboard). Without a
service to read, every page is an error: this app holds no data of its own and no fixtures.

`docker compose up --build` from the repository root runs this app and the service together; `next.config.mjs` sets
`output: 'standalone'` for that image alone, so `ui/Dockerfile` can copy a server and its dependencies rather than the
whole `node_modules`.

`API_URL` defaults to `http://localhost:8000` and is read per request rather than baked in at build time, so a
deployment can be pointed elsewhere without rebuilding. Every fetch happens on the Next.js server, which is why the
service publishes no CORS headers, and every one is `cache: 'no-store'`: the service already holds one built report
per span and rebuilds it when a collection lands, so a second cache here would serve figures whose source has moved
on with nothing on the page to say so.

```bash
npm --prefix ui run check
```

`check` is the whole gate in one step — `eslint`, `tsc --noEmit`, `vitest run --coverage`, `next build` — the
counterpart of `uv run poe check` for the Python. The Python tooling never sees this directory and this gate never
sees the Python. The coverage table prints as part of it and its thresholds are part of the pass, so a change that
adds an untested branch fails `check` rather than passing it quietly; see [Coverage](#coverage).

Its `next build` step needs a filesystem whose directory cache is coherent under concurrent writes. On the
virtiofs mount this repository is checked out on, Turbopack fails writing a chunk it has just created —
`File exists (os error 17)` under `.next`, on a different chunk each run — and before Next 16 the same
incoherency showed as `ENOTDIR` or `ENOENT` under `.next/standalone`. The same tree builds clean with `.next` written
outside the mount, so it is the environment and not the code under it. Where that happens, the three commands that
judge a change are

```bash
npm --prefix ui run lint
npm --prefix ui run typecheck
npm --prefix ui run test
```

and `check` is the one to run wherever the build works, because a page that will not build is not a passing change.

### Install notices

A clean install prints none. No `npm warn deprecated` lines, no ERESOLVE peer warnings, 0 vulnerabilities from
`npm audit`, and no package in the tree runs an install script — so an npm client that asks about install scripts has
nothing to ask about.

Keeping it that way is why the lint config names its three plugins rather than extending `eslint-config-next`. Every
published `eslint@9.x` is registry-deprecated, so staying on 9 costs a deprecation warning; but `eslint-config-next`
bundles `eslint-plugin-react`, `eslint-plugin-jsx-a11y` and `eslint-plugin-import`, all three of which peer at `^9`,
so moving to 10 with it costs three ERESOLVE warnings instead — and `eslint-plugin-react@7.37.5` then throws on a
context API 10 removed. Naming `@next/eslint-plugin-next`, `typescript-eslint` and `eslint-plugin-react-hooks`
directly, which all support 10, costs neither. It halves the tree on the way past — 365 packages rather than 529 —
and drops `unrs-resolver`, the platform-binding `postinstall` that `eslint-config-next` pulled in through
`eslint-import-resolver-typescript`. `esbuild`'s install script went the same way when vitest 4 moved to Vite 8,
which transforms with oxc.

What that gives up is the accessibility rules from `eslint-plugin-jsx-a11y`, which has no release supporting ESLint 10
at all. The ARIA the pages depend on is asserted directly in `src/components/__tests__` instead — the sortable
headers' `aria-sort`, the week selector's `aria-busy` — so it is checked, just not by a linter. Add the plugin back
when it supports 10; `eslint.config.mjs` says the same thing beside the config it applies to.

## Pages

| Route | What it answers |
| --- | --- |
| `/` | nothing of its own: it redirects to `/repositories`, carrying `?weeks=` through where one was given |
| `/repositories` | the estate at one span: counts, five donuts over every configured repository, and the repositories to drill into |
| `/teams` | every configured team at that span, with the labels its repositories carry |
| `/contributors` | everyone who contributed to a reported repository at that span, by the labels their repositories carry |
| `/repositories/[repository]` | one repository's whole evidence block and its periods since enablement |
| `/teams/[team]` | what a team owns, how those repositories are labelled, and who worked in them |
| `/contributors/[login]` | which repositories one person worked in, and what was measured in each of them |

The rows are in the order the nav bar puts them, which from 2026-09-02 reads down the estate:
repositories, the teams that own them, then the people who work in them.

**The donuts on `/repositories` count every configured repository at the span, and the search box does not filter
them.** They read Readiness labels, Enforces review, Enforces CI, Unreviewed substantial merges, then Test coverage —
the label distribution off `/overview` and the other four off the rows' four service fields, added on 2026-09-03.
`/overview`'s distribution covers the repositories the span could REPORT, so `distributionSlices` is handed
`overview.unavailable` beside it and counts those repositories as not assessed; without that the readiness donut would
total less than the four next to it and read as a smaller estate. The
table narrows as a reader types and the pictures above it do not, because a donut that moved with a filter would still
be read as the estate; the readiness one has always worked that way and the four follow it. Every band is drawn in the
legend even where it counted nothing, so a legend states the whole scale while the wedge states only what exists, and a
span with nothing to count says "No data" rather than drawing an empty ring. An unknown slice is always a repository
nothing could be read for and never one measured at zero, and each donut's tooltip says which absences its own unknown
covers — for the two gate donuts, that the gate says a check is required rather than which check it is or whether it
passed.

**The three lists were one page until 2026-09-02**, with the nav pointing at `#repositories`, `#actors` and `#teams`
anchors down it. They are routes now, so a link names what it lands on and a reader loads the third of the estate they
asked about rather than all of it: each list page fetches `/windows`, `/overview` and its own list — three requests
where the overview made five, all against the one bundle the service already holds for that span. `OrganisationHeader`
is that shared head, one component rather than a copy per page, because three headers stating the same window could
claim three different things about it. The Home button went with the split: repositories is the landing page, so a
button back to a page that redirects there would be a second name for where the logo already goes.

`/actors/[login]` became `/contributors/[login]` at the same time, which breaks bookmarked actor URLs deliberately —
a `/contributors` list above an `/actors` detail is a split personality, and this was the change that already touched
routing. The service's `/actors` endpoints and the `actors` field names are untouched: they are what the JSON contract
and `metrics evidence` call them.

Every page is `dynamic = 'force-dynamic'`, reads the span with `resolveWeeks`, and carries the span onto every link
with `withWeeks`, so no navigation quietly changes the window. The span resolves from `?weeks=` first, then the
`weeks` cookie, then the service's own default — the cookie exists so that a nav link, which carries no parameter,
lands on the span the reader was last reading at.

The three links in the nav bar are the one place a link cannot carry the span: the bar is rendered by the layout, and
Next.js hands a layout no search parameters. The cookie is what covers them, so `src/proxy.ts` writes it for any
request that named a span — not just for a press of the selector, which is the only writer a reader who arrived on
somebody else's `?weeks=26` link never triggers. Without it, their first nav click dropped the whole page to four
weeks with nothing saying the window had moved. The value is not checked against the spans on offer there, because
the proxy would need a `/windows` round trip per request to know them and `resolveWeeks` already drops a cookie
holding a span off the list; a positive integer is the whole check.

The file is `src/proxy.ts` exporting `proxy`, which is Next 16's name for what earlier versions called
`src/middleware.ts` exporting `middleware`. The old convention still runs, but it prints a deprecation warning at
build time, and a build that prints nothing is easier to read than one whose noise has to be remembered as harmless.

Every segment carries a `loading.tsx` drawn from `Skeleton.tsx`, and the week selector wraps its `router.replace` in
`useTransition` — dimming the button group and marking it `aria-busy` while the page is on its way. The service keeps
every span warm (see the [root README](../README.md#dashboard)), so a cold bundle is the exception rather than the
usual path; but a first visit and the rebuild after a collection lands can still take seconds, and Next.js holds the
old page on screen until the new one is ready. Without both halves, a reader who pressed 26 weeks sees the 4-week
figures with nothing saying they are stale. The skeletons are the same panels and rows with no numbers in them, so the
page does not jump when it arrives; one `role="status"` says "Loading" in words and the bars are `aria-hidden`, because
a screen reader wants the sentence and not thirty empty boxes.

Drill-through is a graph rather than a tree: a repository leads to its contributors and its team, a contributor back to
other repositories, a team to both. There are no breadcrumbs for that reason — a trail would claim a hierarchy that does
not exist and would read differently depending on which link was followed — and each page's `EntityHeader` says only
where you are now.

## Design tokens

The visual language is class-string idioms rather than a theme config, ported from the predecessor app.

| Element | Classes |
| --- | --- |
| page | `bg-slate-950 text-slate-100` |
| panel — `Section`, `Panel` | `bg-slate-900/40 border border-slate-800 rounded-lg`, heading row divided by `border-b border-slate-800`, body `p-4` |
| section heading | `text-sm font-semibold text-slate-300 uppercase tracking-wide` |
| definition list — `DefinitionList` | `<dl>`, rows `divide-y divide-slate-800/50`, label `text-sm text-slate-400`, value `ml-auto text-sm font-medium text-right` |
| table | `text-xs`, head `text-slate-400 border-b border-slate-800`, body `divide-y divide-slate-800/50` |
| row hover | `hover:bg-slate-800/30` |
| metric value / label | `text-2xl font-semibold tabular-nums` / `text-xs text-slate-400 uppercase tracking-wide` |
| paired sections — `SectionPair` | `grid grid-cols-1 lg:grid-cols-2 gap-4` |
| card row | `grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4`, or `sm:grid-cols-2` inside a pair |
| loading bone — `SkeletonBar` | `animate-pulse rounded bg-slate-800`, sized by the caller and nothing else |
| links and buttons | `text-indigo-400`, `bg-indigo-600 hover:bg-indigo-500` |
| nav | `h-14 bg-slate-900 border-b border-slate-800`, sticky |

**The box is the section, not the figure.** `MetricCard` and the tables draw no border of their own — a repository page
of nine sections had reached about forty bordered boxes with nothing saying which figures belonged together, so the
panel moved up a level and a grid of cards inside one reads as a single surface. `Panel` is the same surface without a
heading, for the two headline card rows that answer the question their page is titled with. A `Section` with no
children renders its heading and no empty padded body, and a `DefinitionList` with no rows renders nothing at all:
blank space under a divider reads as content that failed to load, and the page says what is missing with an
`EmptyState` carrying the reason.

**Two short sections share a row.** `SectionPair` puts Merge gate beside Open pull requests and Security alerts beside
Maintenance on the repository page, stacking again below `lg`. Each of those four is a settings list or four counts,
so at full width each was a wide band with its answers at one end, and the four bands ran the length of a scroll.
The open pull-request cards go two across inside their half rather than the four across the full width allowed — four
counts squeezed into half a row read as one cramped line where two tidy rows read as a block. On this estate the
common case is an unreadable gate beside a full open pull-request block, so the pair is a grid rather than a flex row:
an `EmptyState` next to a full section leaves its half short and does not stretch to match it.

**The repository page is no longer in the evidence block's own order.** Behaviour sits directly under the cohort row,
above the blocking, caution and clear groups, because a reader wants the measurements before the verdict drawn from
them. There is a second departure INSIDE the clear group: `gradedFirst` in `lib/repository.ts` sinks the informational
rows below the graded ones, because a section that alternates between a green bar and no bar reads as checks that lost
their colour. No condition moves between groups and the policy's own order survives inside each half. Both departures
are recorded in the page's own doc comment.

**A settings block is a list, not a grid of cards.** The twelve merge-gate fields, the three maintenance windows and
the three alert families go through `DefinitionList`, where the labels line up and the block is read down. Twelve
two-word answers at headline size across a four-across grid put `yes` and `not disclosed` in the same weight as the
cohort figures above them. Cards stay for a headline figure — the cohort row, the open pull-request counts, the Sonar
measures — where the number is what the reader came for and the label only names it.

Logins and repository names are `font-mono`; numeric columns are `tabular-nums`, and `DefinitionList` applies it to a
numeric answer only, a dash included, so a column of counts stays aligned while `not disclosed` keeps the
proportional face.

### Colour

Two palettes, one file: `tailwind.config.ts` names `rag-red #f87171`, `rag-amber #fbbf24`, `rag-green #4ade80`,
`rag-green-strong #16a34a`, `rag-none #64748b` and `accent #818cf8`. Two modules resolve them, and the split is which
of them is judging.

`rag-green-strong` is a chart mark and nothing else: no class reads it, and the one band it fills — a merge gate
requiring two or more approving reviews — is better than the green beside it rather than a fifth verdict. It is named
in the config all the same, so the colours the site draws with are one list.

`src/lib/rag.ts` carries the report's own judgement — the four readiness LABELS. `borderClass` for the `border-l-4`
colour bar, `RAG_LABEL` for the word, `RAG_HEX` for chart marks where a class cannot reach.

`src/lib/tone.ts` carries the four presentation tones — `good`, `warn`, `bad`, `neutral` — over figures the report
states without grading. **This reverses the rule that colour on a page is the readiness label and nothing else,
on 2026-09-02 at the user's instruction** (see `docs/architecture.md`, "Scope boundaries"). The rules that hold it in
place:

- **Tone colours the value and only the value**, through `valueClass`; the label and the detail stay slate whatever the
  tone, so a coloured page reads as one surface with a few figures standing out of it rather than as a traffic light.
  `borderClass` there is the row equivalent, and keeps a neutral row's bar at the panel's own slate so a list does not
  jog in and out as the eye goes down it.
- **`neutral` is the default and the majority.** A branch name, a line count, how many merges were reported — any
  figure with no threshold worth stating renders exactly as it did before the module existed, which is what keeps a
  colour meaning something where there is one.
- **The threshold lives in `tone.ts` and nowhere else.** No component holds a boundary and no hex literal appears in
  one. Each function names its `assessment.py` counterpart in a comment where one exists, so a page and the policy can
  be checked against each other by reading them side by side. The four donut band tables are there for that reason
  too — the key, the word and the mark per band, best first, with `lib/chart.ts` only counting rows into them — and
  `coverageTone` is one function the repository page's Sonar measure and the coverage donut both read, so moving the
  90/80 boundary moves both.
- **A chart mark comes from `TONE_HEX`**, which derives from `RAG_HEX` rather than restating the four hexes: recharts
  takes a fill as a string, so a donut cannot reach a Tailwind class, and a second copy of the palette is how a wedge
  and the row it counts drift into two greens nobody chose. `STRONG_GOOD_HEX` is the single mark that is not a tone.
- **Where the policy already graded a figure, the page carries its verdict.** A behaviour metric card takes its tone
  from the assessment condition that graded the metric, so a card can never contradict the CLEAR list above it, and a
  blocking assessment row keeps the readiness label it imposed rather than a second opinion in the same colour.
- **Where the policy grades nothing, the threshold is the narrowest one that says anything.**
  `description-quality` and `traceability-reference` are the two rates the readiness policy never reads, so there is no
  condition to borrow a verdict from and `metricTone` is the only place a judgement about them can live. Complete reads
  green — numerator equal to denominator, read off the observation and never off the formatted string, because a rate
  that rounds to 100% is not 100% and colouring it green would say the last unreferenced merge does not exist.
  Everything else stays neutral, and the rule is independent of the assessment so it holds on the contributor page too.
- **An unreadable figure stays uncoloured**, and so do the three merge-gate rules the policy reports without judging.
  A gate field GitHub withheld, an alert family it refused and a Sonar measure a project never reported are absences —
  green there would report a missing permission as a check that passed — and `ReadinessCondition.informational` is how
  a row says it was reported without being judged.

**No emoji anywhere.** A coloured square carries no text, does not survive a screen reader, and renders differently
on every platform. A label reaches a page as a colour bar plus a word: the bar is decoration, the word is the
information, and neither is load-bearing alone. `cannot_assess` is slate rather than a shade between amber and red,
because the report means "half the question could not be read", not "nearly bad".

### Absent values

An unmeasured value is a dash and never a zero. "Nobody measured it" and "somebody measured nothing" are different
findings, and `src/lib/format.ts` keeps them apart the way `metrics.render` does — down to rounding halves to even, as
Python's `round` does, so 78 of 96 reads as 81.2% in an assessment sentence and on the metric card beside it rather
than as two figures for one observation.

An unmeasured value arrives as a MISSING KEY, not as `null`: every route is served with `response_model_exclude_none`,
and pydantic applies that through nested models too. `src/lib/types.ts` mirrors those fields as `field?: T` and the
code guards them with `== null`, which catches both shapes. A strict `=== null` would silently take the wrong branch on
every response the service actually sends.

## Guardrails

These come from `docs/architecture.md`, "Scope boundaries", and they bind the UI as much as the Python:

- **No personal rankings.** No list of people carries a metric column to sort by. `TeamActorsTable` and
  `ContributorsTable` have no sortable headers at all: the first is alphabetical, the second keeps the
  contributions-descending order the service sends, because a repository page asks which merges make up its window,
  and the five figures beside a login there — contributions, merged pull requests, direct pushes, unreviewed merges,
  median size — are counts of what was done in that one repository (2026-09-02), never scores and never rates to
  compare people on. `Blocking occurrences` was dropped from that table the same day as a second copy of the findings
  table above it. `ActorsTable` is the exception, admitted on 2026-09-02: `/contributors` opens sorted by a readiness
  column listing the distinct labels a person's repositories carry, best first, and `combinationKey` in `lib/rag.ts`
  orders those combinations `GREEN`, `GREEN, AMBER`, `GREEN, AMBER, RED`, `GREEN, RED`, `AMBER`, `AMBER, RED`, `RED`.
  It sorts by labels the rows already carry, not by anything derived about a person — and there is still no score, no
  metric column and no per-person verdict. Somebody the `cannot_assess` exclusion leaves with no label carries the
  `CANNOT ASSESS` badge rather than the dash an absent value gets, because their repositories were graded and came
  back unreadable; they sort last in both directions all the same, since `combinationKey` returns nothing for them. All three of its headers sort, the repository count included, because that
  count is navigation: ordering by it says who appears in the most repositories, which is what the column already
  prints. A rate, a median or a finding count beside a login would not be allowed one.
- **No cross-repository averaging.** A person's behaviour metrics are measured per repository and stay in their own
  section on the contributor page. Merges add up across repositories because a sum of merges is still a number of merges;
  a rate, a median or a label never does.
- **No combined team verdict and no ordering of teams.** Per-team label *counts* are permitted (2026-09-01) and are
  what the team page's donut shows. There is no team label, no team score, and the team list keeps the
  configuration's own order.
- **A trend is measured, not graded, and belongs to one repository.** The trend section draws one repository's
  periods in indigo and sky rather than in the RAG palette, because a green line would grade a series the report
  leaves ungraded, and it states each metric's delta basis — percentage points and percentage change both read as
  bare numbers. No two repositories' periods share a series. The cut is the count `GET /windows` publishes as
  `trend_periods`, named on every request because the service refuses an unbounded series rather than truncating one,
  and a series holding that whole count says on the page that it is the first periods since enablement.
- **Nothing is computed here that the report does not carry.** Where a page needs a figure the JSON does not state,
  it must be one a reader could reach by adding the report's own rows up — otherwise it belongs in `evidence.py`.

## Layout

`src/lib` holds the logic as pure functions, tested under `src/lib/__tests__`; `src/components` holds the markup,
with `'use client'` only where recharts or component state needs it; `src/app` holds the routes, which do the I/O and
little else. A component that can be checked has its markup asserted in `src/components/__tests__` through
`react-dom/server`.

Vitest's default environment is `node`, because that renderer needs no document. A test that needs one — a debounce,
a hover, a header click, anything that only happens after the first paint — opts in for its own file with a
`@vitest-environment jsdom` docblock at the top, and drives the component with `@testing-library/react`. The two live
side by side in the same directories; nothing configures a suite-wide DOM.

### Coverage

```bash
npm --prefix ui run coverage       # the text report, as `check` runs it
npm --prefix ui run coverage:html  # the browsable report under ui/coverage
```

**The target is 100% of `src/**` on all four measures**, and `vitest.config.mts` sets the thresholds there, so it is
the gate's answer rather than an aspiration in a document. `test` and so `check` both run with `--coverage`; the first
script above exists to run it alone.

The text report prints no file rows while everything is at 100% — istanbul lists only the files that miss something,
so an empty table under a full summary is the report saying nothing is missing, and rows appear as soon as a figure
drops. The HTML report is the second script rather than part of the gate: it writes a directory per source tree, and
on this virtiofs mount that intermittently dies with `ENOENT: mkdir coverage/src`, the same incoherency that breaks
`next build`. Vitest does not fail a run over a crashed reporter, so keeping it in `check` meant a green gate that
printed a stack trace. `coverage/` is gitignored either way.

100% is reachable here because this app measures nothing of its own: the arithmetic is pure functions in `src/lib`,
the routes are fetch-and-render, and a component's markup is a return value. The two things that made the last few
percent awkward were both testable rather than exempt — a client component's handlers need a document, which is what
the jsdom files above are for, and recharts draws nothing at all until something measures it, so those tests stub a
`ResizeObserver` and run the opening animation out on fake timers.

A threshold is ratcheted upwards and never lowered. Genuinely uninstrumentable code goes in the config's `exclude`
with a comment saying why — `src/lib/types.ts` is the one entry, a module `tsc` erases entirely — because an
exclusion names what is not covered where a reduced percentage only hides it.
