# Project Guidelines

## Environment

- Target Python 3.14 only and manage the project exclusively with uv.
- Declare dependencies in `pyproject.toml`; update and commit `uv.lock` through uv, never by hand.
- Keep source under `src/metrics/` and mirror it under `tests/`.
- The dashboard UI lives in `ui/`, a Next.js app with its own npm dependencies and its own
  `package-lock.json`. Neither toolchain sees the other's directory: uv does not lint `ui/`, and the UI gate does not
  run pytest. FastAPI and uvicorn are an optional `service` extra, so the Python side installs neither by default.
- Both are packaged by `docker-compose.yml`. The root `.dockerignore` is deny-by-default, so a new runtime file
  outside `src/` needs an explicit `!` entry or it will be missing from the API image, and `ui/next.config.mjs`'s
  `output: 'standalone'` exists for `ui/Dockerfile` alone.

## Shell and Credentials

- `GH_TOKEN` is NOT in the environment. Do not test for it, and do not plan work that needs it; anything
  contacting GitHub has to be run by the user.
- Run one discrete command per shell call. No chaining (`&&`, `;`), no pipes, no `echo` separators — only
  single commands match the permission whitelist, and anything else stalls waiting for approval.
- Measurement scripts live in `scripts/` and are run by the user on macOS, whose `/bin/bash` is 3.2: no `mapfile`,
  no `readarray`, no associative arrays, and guard array expansions under `set -u`. Read configuration through a
  single command substitution rather than a process substitution — `<(...)` reports no exit status, so a failed
  `uv` is indistinguishable from an empty result. Each script's finding belongs in `docs/architecture.md`, which
  is where measurements are recorded; the script itself is only how it was taken.

## Required Checks

Run `uv run poe check` before considering work complete. It must pass Ruff linting and formatting, strict mypy, and
pytest without weakening their configuration. Use `uv run poe cover` when coverage is relevant.

Work touching `ui/` must also pass `npm --prefix ui run check` — ESLint, `tsc --noEmit`, vitest, and `next build` in one
step, the UI's equivalent of `uv run poe check`. Run both when a change spans the service and the pages it feeds.

Where `check`'s `next build` step fails on a case-insensitive mount — `ENOTDIR` or `ENOENT` under
`.next/standalone`, on a different path each run, which is the environment and not the code — run `npm --prefix ui run
lint`, `npm --prefix ui run typecheck` and `npm --prefix ui run test` instead. Those three are the parts that judge the
change; `check` is still the command to run wherever the build works. See [`ui/README.md`](ui/README.md).

## Coding Standards

- Write production-quality PEP 8 code with complete English identifiers; do not abbreviate or use single-letter names.
- Prefer pure functions, immutable data, explicit state, comprehensions, generators, and narrow scopes.
- Keep state out of module scope. Isolate I/O and other side effects at application boundaries.
- Use absolute imports, `pathlib`, timezone-aware UTC datetimes, context managers, and specific exceptions.
- Use root `logging` calls with lazy formatting for diagnostics. Reserve `print` for intended process output.
- Type hints are optional but all-or-nothing per function and must pass strict mypy.
- Give every function and method a concise docstring. Comments explain only non-obvious reasons or constraints.
- Test with pytest using fixtures and meaningful edge cases. Keep tests mirrored to the source layout.
- Follow DRY and SOLID without introducing abstractions that do not remove real complexity.

In `ui/`, the same rules in TypeScript, plus:

- Keep pure logic in `ui/src/lib`, tested under `ui/src/lib/__tests__`; `ui/src/app` does the fetching and `ui/src/components`
  the markup. A figure that can be wrong is arithmetic and belongs in `lib`, where a test can reach it.
- An initialism in a CamelCase name is all-caps or all-lower, never mixed: `RAGCard`, `apiFetch` — not `RagCard`.
- `'use client'` only where recharts or component state needs it. Everything else is a server component.
- `ui/src/lib/types.ts` mirrors the service's responses by hand. Every route is served with
  `response_model_exclude_none`, so an unobserved value arrives as a MISSING KEY: mirror it as `field?: T`, never as
  `field: T | null`, and guard it with `== null` rather than `=== null`.
- A figure must read the same on a page as in the text report the same window prints. `ui/src/lib/format.ts` mirrors
  `metrics.render` function for function, down to Python's half-to-even rounding.
- A figure's colour is decided in `ui/src/lib/tone.ts` and nowhere else: no component holds a threshold and none holds
  a hex literal. Where `assessment.py` already grades a figure the page carries that condition's verdict rather than
  restating the target, so raising a target in the assessment configuration is what changes a colour.
- Every route segment carries a `loading.tsx` drawn from `ui/src/components/Skeleton.tsx`, so a cold bundle shows the
  page's bones rather than a blank screen. A new segment without one is unfinished.
- See `ui/README.md` for the design tokens and the guardrails the pages keep.

## Git

Keep commits focused. Separate formatting, refactoring, and behaviour changes when all are needed.
