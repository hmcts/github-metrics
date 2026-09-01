# Plan: Authenticate as a GitHub App installation

## Overview
Replace the single long-lived PAT with a GitHub App installation token minted per run and refreshed as it
expires, so that the endpoints a user-intersected fine-grained token is refused on — branch protection, the
three alert families, and GraphQL PR searches — are readable. A PAT remains a supported fallback.

## Context

- Files involved:
  - `src/metrics/credentials.py` (new) and `tests/test_credentials.py` (new)
  - `src/metrics/github.py` — `GitHubClient.__init__`, `send`, `send_graphql`, `request`
  - `src/metrics/cli.py` — five client constructions and three `GH_TOKEN` checks
  - `src/metrics/doctor.py` — `run_doctor(configuration, token, session)`
  - `tests/test_github.py`, `tests/test_inventory.py`, `tests/test_sonar.py`, `tests/test_cost.py` — 96
    `GitHubClient("secret", session)` constructions
  - `README.md`, `pyproject.toml`
- Related patterns:
  - `sonar.py:408` `sonar_token(environment: Mapping[str, str])` is the house style for env-driven auth: a
    module function taking a mapping, defaulted to `os.environ` at the client boundary. Mirror it.
  - `GitHubError(message, reason, status)` carries an `AvailabilityReason`, which grades one repository's
    evidence. A credentials failure grades the run, not a repository, so it gets its own exception.
  - `cli.py:386` builds the client inside an `ExitStack` so an offline run opens no session. The new factory
    preserves that.
- Dependencies: `pyjwt[crypto]`. The `crypto` extra is required — PyJWT raises on RS256 without
  `cryptography`. PyJWT ships `py.typed`, so strict mypy needs no stub package.
- Out of scope: `scripts/report_copilot_usage.py` and the other `scripts/` utilities keep their own token
  resolution. `GH_BILLING_TOKEN` is not implemented — the enhanced billing and premium-request endpoints it
  would guard are reached only from `scripts/probe-copilot-usage.sh`, and nothing in `src/metrics` collects
  billing or Copilot data.

## Design decisions taken

- A `GitHubCredentials` protocol with two implementations, `PersonalAccessToken` and `AppInstallation`, both
  exposing `token() -> str` and `refresh() -> bool`. `GitHubClient` takes the credentials object rather than a
  `str`, which is why 96 test constructions change.
- `refresh()` reports whether a new token was actually minted. A PAT returns `False`, so the 401 retry does not
  fire in PAT mode and `test_get_reports_http_failure[401]` keeps its single-call assertion. Retrying a PAT
  that was just refused buys nothing.
- The `Authorization` header moves off the session and onto each request. The session keeps `Accept` and
  `X-GitHub-Api-Version`. This is what "no call site holds a token beyond a single request" requires.
- The token exchange is the one GitHub call that cannot go through the provider, because it is what mints the
  token. `AppInstallation` owns a plain `Session` POST with its own bounded retry, and it is deliberately not
  counted in `call_outcomes`: that counter measures evidence collection, and an auth call is not evidence.
- A `threading.Lock`, documented as defensive. There is no `threading`, `asyncio`, or `concurrent.futures`
  anywhere in the codebase, and collection is a sequential loop.
- Credentials are proven at startup by minting once in the factory rather than by probing an endpoint. In App
  mode this forces the exchange the first request would make anyway, so a wrong key or installation ID stops
  the run before collection starts. In PAT mode it returns the configured string. No extra HTTP call.

## Development Approach
- Code then tests within each task: the 100% coverage gate makes an untested branch a build failure rather
  than a review comment.
- Complete each task fully before moving to the next.

## Validation Commands
- `uv run poe check` — Ruff lint, Ruff format check, strict mypy, and pytest at 100% coverage
- `uv run poe cover` — when iterating on coverage alone

## Implementation Steps

### Task 1: Resolve credentials from the environment
- [x] add `pyjwt[crypto]` to `pyproject.toml` dependencies and update `uv.lock` through `uv add`
- [x] create `src/metrics/credentials.py` with a `CredentialsError`, a `GitHubCredentials` protocol exposing
      `token() -> str` and `refresh() -> bool`, and a `PersonalAccessToken` implementation whose `refresh()`
      returns `False`
- [x] add `private_key(environment)` reading `GH_APP_PRIVATE_KEY_PATH` in preference to `GH_APP_PRIVATE_KEY`,
      converting escaped `\n` in the latter to real newlines
- [x] add `resolve_credentials(session, environment)` selecting App auth when `GH_APP_ID`,
      `GH_APP_INSTALLATION_ID` and a key variable are all set, falling back to `GH_TOKEN`, and raising
      `CredentialsError` naming both options when neither is configured
- [x] write `tests/test_credentials.py` covering both modes, both key sources, path precedence, escaped
      newlines, a non-integer `GH_APP_ID`, an unreadable key path, and the unconfigured failure
- [x] run `uv run poe check` — must pass before task 2

Note for task 2: `resolve_credentials` needs an object to return, so a first-cut `AppInstallation` already
exists — it signs the RS256 JWT (`iat` −60s, `exp` +10m, `iss` the App id as a string, because PyJWT refuses
a numeric one), exchanges it, holds the token, and parses `expires_at`. Task 2 adds the five-minute re-mint
margin, the lock, the bounded retry, and the injected clock.

### Task 2: Mint and cache the installation token
- [x] add `AppInstallation` to `credentials.py`, signing an RS256 JWT with `iat` at now minus 60s, `exp` at now
      plus 10 minutes, and `iss` set to the App ID
- [x] exchange the JWT at `POST /app/installations/{id}/access_tokens`, storing the returned token and parsing
      `expires_at` into a timezone-aware UTC instant
- [x] have `token()` re-mint when within 5 minutes of `expires_at`, and `refresh()` re-mint unconditionally and
      return `True`, both under a `threading.Lock` whose docstring records that the tool is single-threaded
      today
- [x] give the exchange a bounded retry on transient failure and raise `CredentialsError` when it fails
      terminally, carrying GitHub's own `message` but no token, JWT, or key material
- [x] inject the clock as a `Callable[[], datetime]` parameter, as `GitHubClient` already does for `time`, so
      expiry is testable without sleeping
- [x] write tests for a first mint, a cached hit, a proactive re-mint inside the five-minute margin, a forced
      `refresh()`, a malformed `expires_at`, and a failed exchange
- [x] run `uv run poe check` — must pass before task 3

### Task 3: Route every request through the provider
- [x] change `GitHubClient.__init__` to take `credentials: GitHubCredentials`, leaving `Accept` and
      `X-GitHub-Api-Version` on the session and removing `Authorization` from it
- [x] pass `Authorization: Bearer {credentials.token()}` per call in `send` and `send_graphql`
- [x] in `request`, retry once on a 401 when `credentials.refresh()` returns `True`, without consuming a
      transient-retry attempt, and leave the existing classification untouched when it returns `False`
- [x] update all 96 `GitHubClient(...)` constructions across `tests/test_github.py`, `tests/test_inventory.py`,
      `tests/test_sonar.py` and `tests/test_cost.py` to pass `PersonalAccessToken("secret")`
- [x] amend `test_get_repository_sends_authenticated_request` to assert the bearer arrives in the per-request
      headers rather than on `session.headers`
- [x] add a test pinning that `record_rate_limit` reads a 12,500 `x-ratelimit-limit` off the headers rather
      than assuming 5,000, and that `wait_for_rate_limit` waits on the reported reset
- [x] add tests for the 401 refresh-and-retry path in both App and PAT mode
- [x] run `uv run poe check` — must pass before task 4

### Task 4: Build the client once in the CLI
- [x] add a factory to `cli.py` that resolves credentials, logs the active auth mode, mints the first token so
      that unusable credentials stop the run before any collection, and constructs the client
- [x] route all five constructions — `emit_evidence`, `emit_trend`, `collect_evidence`, `map_sonar_projects`,
      `run_doctor` — through the factory, changing the `token: str` parameters of `collect_evidence`,
      `map_sonar_projects` and `run_doctor` to take credentials
- [x] replace the three `GH_TOKEN is not set` checks so they report that no GitHub credentials are configured,
      keeping the `use --offline` advice on the two reporting commands
- [x] preserve the `ExitStack` behaviour in `emit_evidence` and `emit_trend` so an offline run still constructs
      no session and resolves no credentials
- [x] write tests for mode logging, a failed first mint exiting non-zero with a clear message in each mode, and
      offline runs needing no credentials at all
- [x] run `uv run poe check` — must pass before task 5

### Task 5: Keep secrets out of the log
- [x] audit the new code and `log_outcome` for any path that could reach a token, JWT, or PEM, and add a
      redacting helper if any header mapping is ever logged
- [x] add tests asserting that a failed exchange, a 401 retry, and a DEBUG-level successful call emit no
      substring of the token, the JWT, or the private key, read through `caplog`
- [x] record in the `AppInstallation` docstring that `http.client` debuglevel would defeat this, since the
      client never enables it
- [x] run `uv run poe check` — must pass before task 6

Audit result: no path in `github.py` logs a header mapping, so nothing there needed redacting — recorded in
`GitHubClient.authorization`'s docstring so the next reader does not have to re-derive it. `credentials.py`
did have a real leak: `refusal` truncated a refusal body to 200 characters, so a proxy error page quoting the
`Authorization` header back left the first 200 characters of the JWT in a WARNING. `redacted(text, *secrets)`
now blanks each secret whole and line by line, and `refusal` applies it BEFORE the truncation — cutting first
leaves a prefix no later pass can match.

### Task 6: Verify acceptance criteria
- [x] document the new variables in `README.md` — the three App variables, the key-path precedence, and the
      PAT fallback — replacing the bare `export GH_TOKEN=` at line 128
- [x] check whether `README.md`'s claim that `doctor` avoids the repository-team endpoint because fine-grained
      tokens cannot read it still holds under App auth, and correct it or leave it on the evidence
- [x] run `uv run poe check`
- [x] run `uv run poe cover` and confirm 100% coverage holds

Documentation result: a new `## Authenticate` section between `Develop` and `Run` states the selection rule (all three
App variables or fall back to `GH_TOKEN`), the key-path precedence and why a path is preferred, the escaped-newline
allowance, what the two modes differ in reading, the startup mint, the five-minute renewal, and that no credential
material is logged. The `Run` block now exports the three App variables instead of `GH_TOKEN`.

Repository-team claim: the reason no longer holds, so it was corrected rather than left. `doctor` still does not query
the endpoint, but under an App installation with `Administration: read` it could — the endpoint is unqueried now
because repository ownership comes from the configuration and is authoritative there, not because the credential cannot
read it.
