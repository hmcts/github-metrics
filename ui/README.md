# metrics dashboard

A Next.js app that reads `metrics-serve` and renders the evidence report as pages. It computes no metric of its own:
every figure on a page is one the service sent, formatted by a pure function in `src/lib`, so a page and
`metrics evidence` for the same span state one thing rather than two that can drift.

## Run

```bash
npm --prefix ui install
API_URL=http://localhost:8000 npm --prefix ui run dev
```

The full loop — collecting, then serving, then this — is in the [root README](../README.md#dashboard). Without a
service to read, every page is an error: this app holds no data of its own and no fixtures.

`API_URL` defaults to `http://localhost:8000` and is read per request rather than baked in at build time, so a
deployment can be pointed elsewhere without rebuilding. Every fetch happens on the Next.js server, which is why the
service publishes no CORS headers, and every one is `cache: 'no-store'`: the service already holds one built report
per span and rebuilds it when a collection lands, so a second cache here would serve figures whose source has moved
on with nothing on the page to say so.

```bash
npm --prefix ui run check
```

`check` is the whole gate in one step — `next lint`, `tsc --noEmit`, `vitest run`, `next build` — the counterpart of
`uv run poe check` for the Python. The Python tooling never sees this directory and this gate never sees the Python.

## Pages

| Route | What it answers |
| --- | --- |
| `/` | the estate at one span: counts, the label distribution, and the three lists to drill into |
| `/repositories/[repository]` | one repository's whole evidence block, in the block's own order, and its periods since enablement |
| `/actors/[login]` | which repositories one person worked in, and what was measured in each of them |
| `/teams/[team]` | what a team owns, how those repositories are labelled, and who worked in them |

Every page is `dynamic = 'force-dynamic'`, reads the span with `resolveWeeks`, and carries the span onto every link
with `withWeeks`, so no navigation quietly changes the window. The span resolves from `?weeks=` first, then the
`weeks` cookie the selector writes, then the service's own default — the cookie exists so that a nav link, which
carries no parameter, lands on the span the reader was last reading at.

Drill-through is a graph rather than a tree: a repository leads to its actors and its team, an actor back to other
repositories, a team to both. There are no breadcrumbs for that reason — a trail would claim a hierarchy that does
not exist and would read differently depending on which link was followed — and each page's `EntityHeader` says only
where you are now.

## Design tokens

The visual language is class-string idioms rather than a theme config, ported from the predecessor app.

| Element | Classes |
| --- | --- |
| page | `bg-slate-950 text-slate-100` |
| card | `bg-slate-900 border border-slate-800 rounded-lg p-5 hover:border-slate-700` |
| section heading | `text-sm font-semibold text-slate-300 uppercase tracking-wide` |
| table | `text-xs`, head `text-slate-400 border-b border-slate-800`, body `divide-y divide-slate-800/50` |
| row hover | `hover:bg-slate-800/30` |
| metric value / label | `text-2xl font-semibold` / `text-xs text-slate-400 uppercase tracking-wide` |
| links and buttons | `text-indigo-400`, `bg-indigo-600 hover:bg-indigo-500` |
| nav | `h-14 bg-slate-900 border-b border-slate-800`, sticky |

Logins and repository names are `font-mono`; numeric columns are `tabular-nums`. The readiness palette is named in
`tailwind.config.ts` as `rag-red #f87171`, `rag-amber #fbbf24`, `rag-green #4ade80`, `rag-none #64748b` and
`accent #818cf8`, and resolved through `src/lib/rag.ts` — `borderClass` for the `border-l-4` colour bar, `RAG_LABEL`
for the word, `RAG_HEX` for chart marks, where a class cannot reach.

**No emoji anywhere.** A coloured square carries no text, does not survive a screen reader, and renders differently
on every platform. A label reaches a page as a colour bar plus a word: the bar is decoration, the word is the
information, and neither is load-bearing alone. `cannot_assess` is slate rather than a shade between amber and red,
because the report means "half the question could not be read", not "nearly bad".

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

- **No personal rankings.** Every list of people is alphabetical and carries no column to sort by. `ActorsTable` and
  `TeamActorsTable` have no sortable headers at all, and the counts beside a login — repositories, merges — are
  counts of things done, never scores.
- **No cross-repository averaging.** A person's behaviour metrics are measured per repository and stay in their own
  section on the actor page. Merges add up across repositories because a sum of merges is still a number of merges;
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
`react-dom/server` — the client tables that read the router are checked as their pure parts instead.
