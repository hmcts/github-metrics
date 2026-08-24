# Project Guidelines

## Environment

- Target Python 3.14 only and manage the project exclusively with uv.
- Declare dependencies in `pyproject.toml`; update and commit `uv.lock` through uv, never by hand.
- Keep source under `src/metrics/` and mirror it under `tests/`.

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

## Git

Keep commits focused. Separate formatting, refactoring, and behaviour changes when all are needed.
