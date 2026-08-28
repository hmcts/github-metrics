"""Access evidence through GitHub's REST and GraphQL APIs."""

import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from http import HTTPStatus
from time import sleep, time
from typing import ClassVar
from urllib.parse import urlsplit

from requests import HTTPError, RequestException, Response, Session
from requests.exceptions import JSONDecodeError

from metrics.domain import AvailabilityReason


class GitHubError(RuntimeError):
    """Report a GitHub access failure.

    The status is carried alongside the reason, and is None for a failure no HTTP response caused,
    so that a caller who knows what ONE endpoint means by ONE status can read it without that
    meaning being asserted for every other endpoint. `reason` stays the classification everything
    else grades on.
    """

    def __init__(self, message: str, reason: AvailabilityReason, status: int | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.status = status


@dataclass(frozen=True)
class RateLimitBudget:
    """Record the latest GitHub budget reported for one API resource."""

    limit: int
    remaining: int
    used: int
    resets_at: float


class GitHubClient:
    """Make authenticated GitHub API requests."""

    api_url: ClassVar[str] = "https://api.github.com"
    graphql_url: ClassVar[str] = "https://api.github.com/graphql"
    http_failures: ClassVar[Mapping[int, tuple[str, AvailabilityReason]]] = {
        HTTPStatus.UNAUTHORIZED: ("GitHub authentication failed", AvailabilityReason.AUTHENTICATION_FAILED),
        HTTPStatus.FORBIDDEN: ("GitHub permission denied", AvailabilityReason.PERMISSION_DENIED),
        HTTPStatus.NOT_FOUND: (
            "GitHub repository not found or inaccessible",
            AvailabilityReason.NOT_FOUND_OR_INACCESSIBLE,
        ),
    }
    """How one HTTP status is classified for EVERY endpoint this client reads.

    422 is deliberately absent. `/repos/{org}/{repo}/commits/{sha}` answers it for a SHA the
    repository does not hold, which refutes a candidate mapping — but that meaning belongs to that
    one endpoint, and classifying it here would give it to all of them. Two readers turn
    `NOT_FOUND_OR_INACCESSIBLE` into an affirmative observation: `collect_classic_merge_gate` reads
    it as "the branch is unprotected" and `open_alerts` as "the family is not enabled". A 422 from
    either — a malformed query, a spammed endpoint — would then be published as a fact nobody
    observed. It stays a collection failure here and is read as a refutation only by the call that
    knows what it means, through `GitHubError.status`.
    """

    def __init__(
        self,
        token: str,
        session: Session,
        pause: Callable[[float], None] = sleep,
        clock: Callable[[], float] = time,
        rate_limit_reserve: int = 0,
    ) -> None:
        session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        self.session = session
        self.pause = pause
        self.clock = clock
        # ZERO BY DEFAULT: a budget with calls left in it is not exhausted, and stopping short of it
        # buys nothing. See `wait_for_rate_limit`. A caller sharing one token with something else can
        # still hold quota back by asking for it.
        self.rate_limit_reserve = max(rate_limit_reserve, 0)
        self.rate_limits: dict[str, RateLimitBudget] = {}
        self.maximum_attempts = 3
        self.issued = 0

    @property
    def requests_issued(self) -> int:
        """Count every GitHub call this client has issued since it was built.

        A running total shared by every caller, because the client is shared: one repository's
        figure is the DIFFERENCE between two readings taken around its collection, never this
        number on its own. No elapsed time is recorded here for exactly that reason — a duration
        read off a shared object would be wrong the moment anything else used it.

        One call is counted per `get` and per `graphql`, so a paginated read counts once per page
        and a request retried after a 502 or a rate limit still counts once: the figure answers
        "how many calls did this repository need", not "how many HTTP round trips did they take".
        """
        return self.issued

    def get_repository(self, organization: str, repository: str) -> Response:
        """Request a repository from GitHub."""
        return self.get(f"{self.api_url}/repos/{organization}/{repository}")

    def get_paginated(
        self,
        url: str,
        parameters: dict[str, str | int] | None = None,
        resource: str = "core",
    ) -> tuple[dict[str, object], ...]:
        """Return every record from a list endpoint by following GitHub Link headers."""
        records: tuple[dict[str, object], ...] = ()
        parameters = dict(parameters or {})
        parameters.setdefault("per_page", 100)
        while url:
            response = self.get(url, parameters, resource)
            try:
                page = response.json()
            except JSONDecodeError as exception:
                message = f"GitHub returned invalid paginated JSON at line {exception.lineno}, column {exception.colno}"
                raise GitHubError(message, AvailabilityReason.COLLECTION_FAILED) from exception
            if not isinstance(page, list) or not all(isinstance(record, dict) for record in page):
                message = "GitHub returned invalid paginated records"
                raise GitHubError(message, AvailabilityReason.COLLECTION_FAILED)
            records += tuple(page)
            url = response.links.get("next", {}).get("url", "")
            destination = urlsplit(url)
            if url and (destination.scheme != "https" or destination.netloc != "api.github.com"):
                message = "GitHub returned an unsafe pagination URL"
                raise GitHubError(message, AvailabilityReason.COLLECTION_FAILED)
            parameters = None
        return records

    def get(
        self,
        url: str,
        parameters: dict[str, str | int] | None = None,
        resource: str = "core",
    ) -> Response:
        """Make a GET request with bounded transient and rate-limit retries."""
        self.issued += 1
        return self.request(lambda: self.send(url, parameters), self.response_retry_delay, resource)

    def graphql(self, query: str, variables: Mapping[str, object] | None = None) -> dict[str, object]:
        """Execute one GraphQL query and return its validated data object."""
        self.issued += 1
        response = self.request(
            lambda: self.send_graphql(query, variables),
            self.graphql_retry_delay,
            "graphql",
        )
        try:
            payload: object = response.json()
        except JSONDecodeError as exception:
            message = f"GitHub returned invalid GraphQL JSON at line {exception.lineno}, column {exception.colno}"
            raise GitHubError(message, AvailabilityReason.COLLECTION_FAILED) from exception
        if not isinstance(payload, dict):
            message = "GitHub returned an invalid GraphQL response"
            raise GitHubError(message, AvailabilityReason.COLLECTION_FAILED)
        errors: object = payload.get("errors")
        if errors:
            reason = self.graphql_error_reason(errors)
            # Logged, but still kept out of the exception message: that message becomes one
            # repository's `detail` in a report meant to be shared, which is why
            # `test_graphql_classifies_errors_without_exposing_details` pins it to a fixed string.
            # A log is the diagnostic channel instead, and it carries the variables because the
            # client cannot name the repository a failure belongs to any other way.
            logging.warning(
                "GitHub GraphQL returned errors for %s: %s",
                json.dumps(dict(variables or {})),
                self.graphql_error_summary(errors),
            )
            message = "GitHub GraphQL returned errors"
            raise GitHubError(message, reason)
        data: object = payload.get("data")
        if not isinstance(data, dict) or not all(isinstance(key, str) for key in data):
            message = "GitHub GraphQL response omitted its data object"
            raise GitHubError(message, AvailabilityReason.COLLECTION_FAILED)
        return {key: value for key, value in data.items() if isinstance(key, str)}

    def request(
        self,
        send: Callable[[], Response],
        retry_delay: Callable[[Response, int], float | None],
        resource: str,
    ) -> Response:
        """Execute one HTTP operation with bounded transient and rate-limit retries."""
        self.wait_for_rate_limit(resource)
        attempt = 0
        while True:
            attempt += 1
            try:
                response = send()
            except RequestException as exception:
                if attempt == self.maximum_attempts:
                    message = f"GitHub request failed after {self.maximum_attempts} attempts: {exception}"
                    raise GitHubError(message, AvailabilityReason.COLLECTION_FAILED) from exception
                backoff = 2 ** (attempt - 1)
                logging.warning(
                    "GitHub request failed (attempt %s of %s), retrying in %ss: %s",
                    attempt,
                    self.maximum_attempts,
                    backoff,
                    exception,
                )
                self.pause(backoff)
                continue

            self.record_rate_limit(response)
            # The body is deliberately NOT logged. Secret-scanning alert records carry the literal
            # detected credential in a `secret` field, so dumping every response at DEBUG would copy
            # live keys out of GitHub's access controls into a plain file on disk. Its size is logged
            # instead: enough to tell an empty page from a full one when diagnosing a collection.
            logging.debug(
                "GitHub response %s %s %s bytes",
                response.status_code,
                response.url,
                len(response.content or b""),
            )
            delay = retry_delay(response, attempt)
            if delay is not None:
                # The status code is logged because the two causes need telling apart: a 5xx backoff
                # is 1s then 2s, while a real rate limit waits for retry-after or the reset instant.
                # GitHub's own message goes with it, because a 403 alone does not say WHICH limit was
                # hit — a primary quota names the resource and clears at its reset, a secondary limit
                # names no resource and clears when it feels like it, and the fix differs.
                logging.warning(
                    "GitHub returned HTTP %s for %s (attempt %s of %s), retrying in %.0fs: %s [%s %s/%s, resets in %.0fs]",
                    response.status_code,
                    resource,
                    attempt,
                    self.maximum_attempts,
                    delay,
                    self.failure_message(response),
                    response.headers.get("x-ratelimit-resource", "unknown resource"),
                    response.headers.get("x-ratelimit-remaining", "?"),
                    response.headers.get("x-ratelimit-limit", "?"),
                    max((self.header_number(response, "x-ratelimit-reset") or self.clock()) - self.clock(), 0),
                )
                self.pause(delay)
                continue
            return self.validate(response)

    def wait_for_rate_limit(self, resource: str) -> None:
        """Wait out a window this client has nothing left in, before spending a call proving it.

        A BUDGET WITH CALLS LEFT IN IT IS NOT EXHAUSTED, and this used to stop at one that had. The
        reserve defaulted to 100 and was ABSOLUTE, so `graphql` parked for the rest of its hour with
        75 of 5000 still in hand — 100 unspent calls and a log line calling a 98%-spent quota
        "exhausted", which reads as a bug in the reporting rather than a policy nobody asked for.
        Holding quota back is only meaningful when something ELSE shares the token, and nothing here
        does, so the default is now zero and the reserve is opt-in.

        WAITING AT ALL IS STILL WORTH IT at genuine zero: the alternative is issuing a call certain to
        be refused, then sleeping out the same window anyway with one of three retry attempts already
        spent. What it must never do is decide the window is spent when it is not — GraphQL charges
        POINTS rather than calls, so `remaining` falls in steps of whatever the last query cost, and
        a threshold above zero throws away everything between it and zero.

        The wait is announced at WARNING rather than DEBUG because it is the one place a collection
        stops for minutes at a time without issuing a request: a silent pause here is indistinguishable
        from a hang, since the next line any log would otherwise show is the request that comes after it.
        """
        budget = self.rate_limits.get(resource)
        if budget is None or budget.remaining > self.rate_limit_reserve or budget.resets_at <= self.clock():
            return
        delay = max(budget.resets_at - self.clock(), 0)
        logging.warning(
            "GitHub %s quota spent (%s of %s left, reserve %s), waiting %.0fs for the window to reset",
            resource,
            budget.remaining,
            budget.limit,
            self.rate_limit_reserve,
            delay,
        )
        self.pause(delay)
        del self.rate_limits[resource]

    def record_rate_limit(self, response: Response) -> None:
        """Record a complete resource budget reported in response headers."""
        resource = response.headers.get("x-ratelimit-resource")
        if resource is None:
            return
        try:
            budget = RateLimitBudget(
                limit=int(response.headers["x-ratelimit-limit"]),
                remaining=int(response.headers["x-ratelimit-remaining"]),
                used=int(response.headers["x-ratelimit-used"]),
                resets_at=float(response.headers["x-ratelimit-reset"]),
            )
        except KeyError, ValueError:
            return
        logging.debug("GitHub budget %s %s/%s remaining", resource, budget.remaining, budget.limit)
        self.rate_limits[resource] = budget

    def budget(self, resource: str) -> RateLimitBudget | None:
        """Return the budget GitHub last reported for one of ITS OWN resource names, or None.

        Keyed by what the `x-ratelimit-resource` header says, which is not always the name a caller
        waits under: the commit search is issued as `commit-search` precisely so that
        `wait_for_rate_limit` leaves it alone, and GitHub reports it as `search`. A caller that paces
        a scarce quota by hand reads the real budget through here rather than assuming a limit — the
        assumed 30-a-minute search limit is 10 a minute for some tokens, and a run pacing to the
        wrong one spends a minute in a 403 backoff for every ten searches it issues.
        """
        return self.rate_limits.get(resource)

    def send(self, url: str, parameters: dict[str, str | int] | None) -> Response:
        """Send one HTTP request."""
        logging.debug("GitHub request GET %s %s", url, json.dumps(parameters or {}))
        if parameters:
            return self.session.get(url, params=parameters, timeout=30)
        return self.session.get(url, timeout=30)

    def send_graphql(self, query: str, variables: Mapping[str, object] | None) -> Response:
        """Send one GraphQL HTTP request."""
        payload = {"query": query, "variables": dict(variables or {})}
        logging.debug("GitHub request POST %s %s", self.graphql_url, json.dumps(payload))
        return self.session.post(self.graphql_url, json=payload, timeout=30)

    def graphql_retry_delay(self, response: Response, attempt: int) -> float | None:
        """Return the delay required for HTTP or GraphQL rate-limit failures."""
        delay = self.response_retry_delay(response, attempt)
        if delay is not None or not self.graphql_rate_limited(response):
            return delay
        if attempt == self.maximum_attempts:
            message = f"GitHub rate limit exceeded after {self.maximum_attempts} attempts"
            raise GitHubError(message, AvailabilityReason.RATE_LIMITED)
        if "retry-after" in response.headers:
            return float(response.headers["retry-after"])
        if response.headers.get("x-ratelimit-remaining") == "0":
            return max(float(response.headers["x-ratelimit-reset"]) - self.clock(), 0)
        return 60

    @staticmethod
    def graphql_rate_limited(response: Response) -> bool:
        """Report whether a successful HTTP response contains a GraphQL rate-limit error."""
        try:
            payload: object = response.json()
        except JSONDecodeError:
            return False
        if not isinstance(payload, dict):
            return False
        errors: object = payload.get("errors")
        if not isinstance(errors, list):
            return False
        return any(
            isinstance(error, dict)
            and (
                "rate limit" in str(error.get("message", "")).lower()
                or "rate_limit" in str(error.get("type", "")).lower()
            )
            for error in errors
        )

    @staticmethod
    def graphql_error_summary(errors: object) -> str:
        """Summarise GraphQL errors as `TYPE: message` for the log.

        GitHub distinguishes a repository that was renamed or deleted from one a token may not read
        only in this text, so a collection that lost repositories is undiagnosable without it.
        """
        if not isinstance(errors, list):
            return str(errors)
        return "; ".join(
            f"{error.get('type', 'UNKNOWN')}: {error.get('message', '')}" if isinstance(error, dict) else str(error)
            for error in errors
        )

    @staticmethod
    def graphql_error_reason(errors: object) -> AvailabilityReason:
        """Classify terminal GraphQL errors without exposing response content."""
        if isinstance(errors, list) and any(
            isinstance(error, dict)
            and (
                str(error.get("type", "")).upper() == "FORBIDDEN"
                or "not accessible" in str(error.get("message", "")).lower()
            )
            for error in errors
        ):
            return AvailabilityReason.PERMISSION_DENIED
        return AvailabilityReason.COLLECTION_FAILED

    def response_retry_delay(self, response: Response, attempt: int) -> float | None:
        """Return the delay required before retrying a response."""
        delay = self.rate_limit_delay(response)
        if delay is not None:
            if attempt == self.maximum_attempts:
                message = f"GitHub rate limit exceeded after {self.maximum_attempts} attempts"
                raise GitHubError(message, AvailabilityReason.RATE_LIMITED)
            return delay
        if response.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR and attempt < self.maximum_attempts:
            return float(2 ** (attempt - 1))
        return None

    def rate_limit_delay(self, response: Response) -> float | None:
        """Return GitHub's required delay for a rate-limited response, or None if it is not one.

        A 403 is GitHub's answer both to a spent quota and to a refusal, and the two must never be
        confused: retrying a refusal spends a minute learning nothing, and reporting a spent quota as
        a refusal records "nobody may look" as a fact about the repository. Only the three signals
        GitHub documents are read as a rate limit — `retry-after`, an exhausted `x-ratelimit-remaining`,
        or a body that says so — and a 403 carrying none of them falls through to `validate`, which
        classifies it as PERMISSION_DENIED without pausing.

        Both header reads are guarded. `retry-after` is allowed to be an HTTP-date by the HTTP spec
        (GitHub sends seconds, but a proxy in the path need not), and `x-ratelimit-reset` can be
        absent on the very response whose `x-ratelimit-remaining` is `0`. Either would have raised
        out of the retry path as an unclassified crash instead of a graded refusal.
        """
        if response.status_code not in {HTTPStatus.FORBIDDEN, HTTPStatus.TOO_MANY_REQUESTS}:
            return None
        seconds = self.header_number(response, "retry-after")
        if seconds is not None:
            return max(seconds, 0)
        resets_at = self.header_number(response, "x-ratelimit-reset")
        if response.headers.get("x-ratelimit-remaining") == "0" and resets_at is not None:
            return max(resets_at - self.clock(), 0)
        if response.status_code == HTTPStatus.TOO_MANY_REQUESTS or "rate limit" in response.text.lower():
            return 60
        return None

    @staticmethod
    def header_number(response: Response, name: str) -> float | None:
        """Read one numeric response header, or None when it is absent or not a number."""
        value = response.headers.get(name)
        if value is None:
            return None
        try:
            return float(value)
        except ValueError:
            return None

    @staticmethod
    def failure_message(response: Response) -> str:
        """Return GitHub's own `message` for a failed response, for the log and nothing else.

        THE ONE PIECE OF A BODY THIS CLIENT WILL COPY OUT. Everything else is withheld deliberately —
        a secret-scanning alert record carries the detected credential itself — but a 403 is
        undiagnosable without it: "API rate limit exceeded for user 123", "You have exceeded a
        secondary rate limit", "Resource not accessible by personal access token" and an IP-allow-list
        refusal are four different problems with four different fixes, and all four look identical in
        a log that records only the status. It stays out of `GitHubError`, whose message becomes one
        repository's `detail` in a report meant to be shared.
        """
        try:
            payload: object = response.json()
        except JSONDecodeError:
            return "no message"
        if not isinstance(payload, dict):
            return "no message"
        return str(payload.get("message", "no message"))

    def validate(self, response: Response) -> Response:
        """Classify terminal GitHub HTTP responses."""
        try:
            response.raise_for_status()
        except HTTPError as exception:
            message, reason = self.http_failures.get(
                response.status_code,
                (f"GitHub returned HTTP {response.status_code}", AvailabilityReason.COLLECTION_FAILED),
            )
            # Logged, not carried: `message` becomes a repository's `detail` in a shared report and
            # stays the fixed string the suite pins, while the log gets GitHub's own reason so a
            # refused token can be told from a missing repository without re-running the collection.
            logging.warning(
                "GitHub refused %s %s: %s",
                response.status_code,
                response.url,
                self.failure_message(response),
            )
            raise GitHubError(message, reason, response.status_code) from exception
        return response
