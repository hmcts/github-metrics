"""Access evidence through GitHub's REST and GraphQL APIs."""

import json
import logging
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from http import HTTPStatus
from time import sleep, time
from typing import ClassVar
from urllib.parse import parse_qsl, urlencode, urlsplit

from requests import HTTPError, RequestException, Response, Session
from requests.exceptions import JSONDecodeError

from metrics.credentials import GitHubCredentials
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


@dataclass(frozen=True)
class BodyFailure:
    """Report a failure an API stated in the body of a response HTTP called a success.

    `status` is the status the failure IS, which for GraphQL is not the status it arrived under: a
    query a token may not run comes back as HTTP 200 with a `FORBIDDEN` error, and reporting that as
    200 hides the estate's single most-searched-for problem from anyone grepping a log for `403`. The
    log says `403 (equivalent)` so that nobody reads it as a status HTTP returned, and the run
    summary counts it at 403 beside the refusals it belongs with.

    A failure with no equivalent status carries the response's own, so the two never disagree.
    """

    summary: str
    status: int


OWNED_PREFIXES: Mapping[str, tuple[str, ...]] = {
    "repos": ("{organization}", "{repository}"),
    "orgs": ("{organization}",),
}
"""The path segments that name WHOSE repository or organisation a call is about.

Every one of them is a value rather than a route, so counting them apart turns one endpoint read for
1850 repositories into 1850 endpoints nobody can read a total from.
"""

NAMED_VALUES: Mapping[str, str] = {"branches": "{branch}", "commits": "{sha}"}
"""The path segments whose SUCCESSOR is a value, and the placeholder each one's value takes.

A branch name and a commit SHA are values the same way an organisation is, but neither is at a fixed
position and neither is all digits, so the two rules above miss them. The SHA matters most: it is
never repeated, so `GET /repos/{org}/{repo}/commits/{sha}` — the Sonar mapping's cheap confirmation,
issued once per repository declaring a key — would otherwise contribute one summary line per
repository, which is the exact fragmentation these placeholders exist to prevent.
"""

PAGINATION_PARAMETERS = frozenset({"page", "after", "before", "cursor"})
"""The query parameters whose value is a POSITION in a list rather than a description of the call.

Left alone, a paginated read fragments into one counted endpoint per page, and a Link header carrying
a cursor rather than a page number fragments it per repository — the same failure the path
placeholders fix. GraphQL never reaches this: it collapses to its bare URL above, and its cursors
travel in the POST body.
"""

CallOutcome = tuple[int, str, str, str]
"""One counted kind of call: status, `ok`/`errors`/`disabled`/`refused`/`failed`, method, endpoint.

The status alone does not say what happened to a call. A 403 is a refusal or a feature nobody turned
on, and a 502 is neither — so the word beside the status is what a reader counts by, and
`CALL_OUTCOME_ORDER` in `cli` ranks them. The status is the one the failure IS rather than the one it
travelled in, so a GraphQL refusal is counted at 403 with the refusals rather than at the 200 GitHub
wrapped it in; `BodyFailure` carries that judgement.
"""


def endpoint_template(target: str) -> str:
    """Normalise one call's URL into the endpoint it belongs to, so a run can be counted by it.

    The values a URL carries — the organisation, the repository, a ruleset id, a branch name, a
    commit SHA, a page or a cursor — are replaced by placeholders, and everything else is left
    exactly as it was. What
    survives is the shape of the call: `GET /repos/{organization}/{repository}/code-scanning/alerts`
    is one line in a summary whether it was issued once or 1850 times.

    It reads the URL rather than taking an endpoint name from each caller, so none of the several
    dozen call sites has to be told what it is asking for, and a template can never drift from the
    request it claims to describe. GraphQL collapses to its bare URL: every GraphQL call is a POST to
    the same address, and the query naming what it asked for is in the body.
    """
    split = urlsplit(target)
    segments = split.path.strip("/").split("/")
    if segments == ["graphql"]:
        return f"{split.scheme}://{split.netloc}/graphql"
    owned = OWNED_PREFIXES.get(segments[0], ())
    templated: list[str] = []
    for position, segment in enumerate(segments):
        if 0 < position <= len(owned):
            templated.append(owned[position - 1])
        elif segment.isdigit():
            templated.append("{id}")
        elif position and segments[position - 1] in NAMED_VALUES:
            templated.append(NAMED_VALUES[segments[position - 1]])
        else:
            templated.append(segment)
    query = "&".join(
        f"{name}={{{name}}}" if name in PAGINATION_PARAMETERS else f"{name}={value}"
        for name, value in parse_qsl(split.query, keep_blank_values=True)
    )
    return f"{split.scheme}://{split.netloc}/{'/'.join(templated)}{'?' + query if query else ''}"


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

    feature_disabled_phrases: ClassVar[frozenset[str]] = frozenset(
        {
            "is disabled for this repository",
            "are disabled for this repository",
            "must be enabled for this repository",
            "is not enabled for this repository",
            "upgrade to github pro",
        },
    )
    """What GitHub says when a 403 means "this feature is off" rather than "you may not look".

    A 403 is GitHub's answer to both, and only this text tells them apart. Grading all of them as
    refusals was the earlier ruling, and the evidence overturned it: across 1850 repositories every
    403 was a feature or plan message — 830 `Code Security must be enabled for this repository to use
    code scanning`, 113 `Dependabot alerts are disabled for this repository`, 2 `Upgrade to GitHub Pro
    or make this repository public to enable this feature` — and not one was a genuine refusal. A run
    that grades 945 disabled features as refusals exits 3 and buries the real permission problem among
    them.

    EVERY PHRASE BUT ONE IS ANCHORED ON `for this repository`, so an organisation-level refusal can
    never match: GitHub does not say a token was refused "for this repository". The plan message is
    anchored on GitHub's own product name instead. AN UNRECOGNISED 403 STAYS A REFUSAL — the failure
    direction is deliberate. A disabled message nobody listed here is reported as a refusal, which a
    human reads in the log and adds; a refusal is never hidden by a phrase we guessed at.
    """

    def __init__(
        self,
        credentials: GitHubCredentials,
        session: Session,
        pause: Callable[[float], None] = sleep,
        clock: Callable[[], float] = time,
        rate_limit_reserve: int = 0,
    ) -> None:
        # ONLY the two headers that are the same on every call this client will ever make. The
        # Authorization header is deliberately NOT among them: an installation token expires within
        # the hour, and a session holding one hands a stale credential to anything else given the
        # same session. It is asked for per request instead, from the credentials object, so no call
        # site holds a token beyond the request it is sent on.
        session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        self.credentials = credentials
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
        self.outcomes: Counter[CallOutcome] = Counter()

    @property
    def call_outcomes(self) -> Mapping[CallOutcome, int]:
        """Count every response this client has seen, by status, outcome, method and endpoint.

        A run's answer to "what did GitHub actually do with 20,000 calls". Beside `requests_issued`,
        which counts calls a repository cost, this counts RESPONSES by what came back, so the 830
        repositories with code scanning switched off show up as one line rather than as 830 warnings
        nobody reads to the end of.

        A copy, because a caller that could decrement this would be silently rewriting the record of
        a run. Retried attempts are excluded for the same reason they are in `requests_issued`: only
        the response a call ended on is counted, once.
        """
        return dict(self.outcomes)

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
        suffix = f"?{urlencode(parameters)}" if parameters else ""
        return self.request(
            lambda: self.send(url, parameters),
            self.response_retry_delay,
            resource,
            f"GET {url}{suffix}",
        )

    def graphql(self, query: str, variables: Mapping[str, object] | None = None) -> dict[str, object]:
        """Execute one GraphQL query and return its validated data object."""
        self.issued += 1
        response = self.request(
            lambda: self.send_graphql(query, variables),
            self.graphql_retry_delay,
            "graphql",
            # The variables go in the description because every GraphQL call is a POST to the same
            # URL: they are the only part of the request that names the repository it is about.
            f"POST {self.graphql_url} {json.dumps(dict(variables or {}))}",
            self.graphql_body_failure,
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
            # The errors themselves were logged and counted by `log_outcome`, which read this same
            # body through `graphql_body_failure` before the call was counted — so the one line the
            # failure gets carries the variables naming the repository it belongs to. They stay out
            # of the exception message, which becomes one repository's `detail` in a report meant to
            # be shared, and which `test_graphql_classifies_errors_without_exposing_details` pins.
            message = "GitHub GraphQL returned errors"
            raise GitHubError(message, self.graphql_error_reason(errors))
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
        description: str,
        body_failure: Callable[[Response], BodyFailure | None] | None = None,
    ) -> Response:
        """Execute one HTTP operation with bounded transient and rate-limit retries.

        `description` is what the log calls this call — the method and target the caller asked for,
        built before the request so that a failure which never produced a response can still be
        named. Every line this method writes carries it, because the pre-request line that used to
        say which call was in flight is gone: without it a run that died on retries recorded a
        timeout with no endpoint on it.

        `body_failure` is how an API that reports failure in the body rather than in the status says
        so, and only GraphQL passes one. It is read where the call is logged and counted, never on an
        attempt that is about to be retried, so a response the client superseded is not graded on a
        body nobody acted on.

        A 401 is retried ONCE, and only when the credentials can actually produce a different token.
        A collection of 1850 repositories outlives the hour an installation token lives for, so a
        token expiring mid-flight is an expected event with an obvious remedy, not a repository whose
        evidence is unavailable. A personal access token answers `refresh()` with False and takes the
        untouched path straight to `AUTHENTICATION_FAILED`, because sending a just-refused token a
        second time buys nothing.
        """
        self.wait_for_rate_limit(resource)
        attempt = 0
        refreshed = False
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
                    "GitHub request failed for %s (attempt %s of %s), retrying in %ss: %s",
                    description,
                    attempt,
                    self.maximum_attempts,
                    backoff,
                    exception,
                )
                self.pause(backoff)
                continue

            self.record_rate_limit(response)
            if response.status_code == HTTPStatus.UNAUTHORIZED and not refreshed and self.credentials.refresh():
                refreshed = True
                # The transient budget is NOT charged for this. Those three attempts exist to outlast
                # a bad minute at GitHub, and an expired token has spent none of it — a call that
                # then hits a 502 should still get its full complement of retries.
                attempt -= 1
                logging.warning(
                    "GitHub returned HTTP 401 for %s, retrying once with a freshly minted token",
                    description,
                )
                continue
            try:
                delay = retry_delay(response, attempt)
            except GitHubError:
                # A retry budget spent on the last attempt still ENDED ON A RESPONSE, and that
                # response is a call. Logging and counting it here is what keeps one line per call
                # true for the run a rate limit killed — the run with a non-zero status, no report
                # on stdout, and a summary that is the only account of what it spent.
                self.log_outcome(response, description, body_failure)
                raise
            if delay is not None:
                # The status code is logged because the two causes need telling apart: a 5xx backoff
                # is 1s then 2s, while a real rate limit waits for retry-after or the reset instant.
                # GitHub's own message goes with it, because a 403 alone does not say WHICH limit was
                # hit — a primary quota names the resource and clears at its reset, a secondary limit
                # names no resource and clears when it feels like it, and the fix differs.
                # The call is named as well as the status: with the pre-request line gone, a run that
                # spent its retries has nothing else to say WHICH call it was retrying. GitHub's own
                # resource name stays in the bracket, which is the one this budget belongs to.
                logging.warning(
                    "GitHub returned HTTP %s for %s (attempt %s of %s), retrying in %.0fs: %s [%s %s/%s, resets in %.0fs]",
                    response.status_code,
                    description,
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
            self.log_outcome(response, description, body_failure)
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

    def authorization(self) -> dict[str, str]:
        """Return the Authorization header for ONE request, asking credentials for the token now.

        Asked for per request rather than once, because in App mode the token is minted on demand
        and replaced before it expires: a header built at construction would be an hour stale by the
        end of a collection that takes longer than that. In PAT mode this returns the same string
        every time and costs a dictionary.

        The dictionary goes to `requests` and nowhere else. NO PATH IN THIS CLIENT LOGS A HEADER
        MAPPING: `log_outcome` writes a status, the description the caller built, and GitHub's own
        `message`; the retry warnings read individual `x-ratelimit-*` values by name; and the 401
        line names the call rather than the token it was refused for. That is why there is no
        redaction step here to remember to apply — the token has nowhere to leak to. Anything added
        that logs headers wholesale has to redact them, and `credentials.redacted` is how.
        """
        return {"Authorization": f"Bearer {self.credentials.token()}"}

    def send(self, url: str, parameters: dict[str, str | int] | None) -> Response:
        """Send one HTTP request."""
        if parameters:
            return self.session.get(url, params=parameters, headers=self.authorization(), timeout=30)
        return self.session.get(url, headers=self.authorization(), timeout=30)

    def send_graphql(self, query: str, variables: Mapping[str, object] | None) -> Response:
        """Send one GraphQL HTTP request."""
        payload = {"query": query, "variables": dict(variables or {})}
        return self.session.post(self.graphql_url, json=payload, headers=self.authorization(), timeout=30)

    def graphql_retry_delay(self, response: Response, attempt: int) -> float | None:
        """Return the delay required for HTTP or GraphQL rate-limit failures.

        Both header reads are guarded for the same reason `rate_limit_delay` guards its own: the HTTP
        spec allows `retry-after` to be a date rather than seconds, and `x-ratelimit-reset` can be
        absent on the very response whose `x-ratelimit-remaining` is `0`. Unguarded, either raised a
        `ValueError` or `KeyError` out of `request`'s `retry_delay` call, which catches `GitHubError`
        alone — so a header this client merely failed to parse ended a whole collection in a traceback
        instead of the graded rate-limit failure the caller knows how to record. Falling through to
        the flat 60s is the same answer this method already gives a rate limit that names no deadline.
        """
        delay = self.response_retry_delay(response, attempt)
        if delay is not None or not self.graphql_rate_limited(response):
            return delay
        if attempt == self.maximum_attempts:
            message = f"GitHub rate limit exceeded after {self.maximum_attempts} attempts"
            raise GitHubError(message, AvailabilityReason.RATE_LIMITED)
        seconds = self.header_number(response, "retry-after")
        if seconds is not None:
            return max(seconds, 0)
        resets_at = self.header_number(response, "x-ratelimit-reset")
        if response.headers.get("x-ratelimit-remaining") == "0" and resets_at is not None:
            return max(resets_at - self.clock(), 0)
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

    def graphql_body_failure(self, response: Response) -> BodyFailure | None:
        """Summarise the errors one GraphQL response carries, or None when it carries none.

        GITHUB ANSWERS A GRAPHQL FAILURE WITH HTTP 200 AND AN `errors` ARRAY, so a status read on its
        own grades a query nobody was allowed to run as a call that worked. One run counted 2158
        GraphQL calls as `200 ok` when 181 of them had failed FORBIDDEN, which is a tenth of the
        GraphQL evidence missing from a summary that reported no GraphQL problem at all. The body is
        read here, before `log_outcome` counts the call, so the summary says what the run got.

        The equivalent status comes from `graphql_error_reason` rather than from a second reading of
        the same errors: the permission failure a caller is handed and the 403 the log is grepped for
        are then one judgement, and a body that stops being read as a refusal cannot go on being
        reported as one. Every other reason keeps the response's own status, because `COLLECTION_FAILED`
        names no status a reader would search for.

        Only an `errors` array is read as a failure. A body that is not JSON, or whose `data` is
        missing, is a failure `graphql` raises on and this does not claim to have classified.
        """
        try:
            payload: object = response.json()
        except JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        errors: object = payload.get("errors")
        if not errors:
            return None
        refused = self.graphql_error_reason(errors) is AvailabilityReason.PERMISSION_DENIED
        return BodyFailure(
            summary=self.graphql_error_summary(errors),
            status=HTTPStatus.FORBIDDEN if refused else response.status_code,
        )

    @staticmethod
    def graphql_error_summary(errors: object) -> str:
        """Summarise GraphQL errors as `TYPE: message` for the log, each kind reported once.

        GitHub distinguishes a repository that was renamed or deleted from one a token may not read
        only in this text, so a collection that lost repositories is undiagnosable without it.

        COUNTED RATHER THAN LISTED, because GitHub returns one error per node it would not answer
        for: a single search returned `FORBIDDEN: Resource not accessible by personal access token`
        76 times, and one run wrote that same sentence out 6,880 times. Repeating it says nothing the
        first copy did not, and it buries the error that appears once — which is the one worth
        reading. First-appearance order is kept for that reason: a rare kind is never pushed below a
        common one by its count.
        """
        if not isinstance(errors, list):
            return str(errors)
        counted = Counter(
            f"{error.get('type', 'UNKNOWN')}: {error.get('message', '')}" if isinstance(error, dict) else str(error)
            for error in errors
        )
        return "; ".join(f"{text} (x{count})" if count > 1 else text for text, count in counted.items())

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

    @staticmethod
    def reports_feature_disabled(message: str) -> bool:
        """Report whether GitHub's own message says a feature is off rather than that access was refused."""
        folded = message.casefold()
        return any(phrase in folded for phrase in GitHubClient.feature_disabled_phrases)

    def feature_disabled(self, response: Response) -> bool:
        """Report whether a response is a 403 GitHub explained as a feature nobody turned on."""
        return response.status_code == HTTPStatus.FORBIDDEN and self.reports_feature_disabled(
            self.failure_message(response),
        )

    def classify(self, response: Response) -> tuple[str, AvailabilityReason]:
        """Return the shareable message and the reason for a response GitHub did not answer.

        A 403 explained as a disabled feature is `FEATURE_DISABLED`, which readers treat as an
        observation rather than a failure — the same way they already read a 404 from an alert
        endpoint. Everything else defers to `http_failures`, so an unrecognised 403 stays
        `PERMISSION_DENIED`.
        """
        if self.feature_disabled(response):
            return ("GitHub reports the feature is not enabled", AvailabilityReason.FEATURE_DISABLED)
        return self.http_failures.get(
            response.status_code,
            (f"GitHub returned HTTP {response.status_code}", AvailabilityReason.COLLECTION_FAILED),
        )

    def log_outcome(
        self,
        response: Response,
        description: str,
        body_failure: Callable[[Response], BodyFailure | None] | None = None,
    ) -> None:
        """Log EXACTLY ONE LINE for one GitHub response, at a level chosen by what came back.

        A failed call used to emit three lines of ours — a pre-request line, a response line and a
        refusal warning — for one call, which is why a collection of 1850 repositories was unreadable.
        There is now one, and a disabled feature logs at DEBUG beside a 200 rather than at WARNING, so
        AT THE DEFAULT LEVEL A 403 IN THE LOG IS ALWAYS A GENUINE PERMISSION PROBLEM.

        One exception is documented rather than engineered away: a retried response logs its retry
        WARNING instead of this line, one per attempt. A response the retries ran out on is not
        retried, so it logs this line and is counted here. urllib3's own connection line is left
        alone — it is not ours, and `--logging info` silences it.

        The body is deliberately NOT logged. Secret-scanning alert records carry the literal detected
        credential in a `secret` field, so dumping every response at DEBUG would copy live keys out of
        GitHub's access controls into a plain file on disk. Its size is logged instead: enough to tell
        an empty page from a full one when diagnosing a collection. A failure logs GitHub's own
        `message` and nothing else of the body, which is the one part `failure_message` may copy out.

        FIVE WORDS, AND THE STATUS DECIDES NONE OF THEM ON ITS OWN:

        - `ok` answered, and `errors` answered with a body saying it did not — which is the only
          shape a GraphQL failure has, since GitHub returns those inside a 200.
        - `disabled` is a 403 GitHub explained as a feature nobody turned on.
        - `refused` is one of the three statuses `http_failures` grades as an access decision, and
          `failed` is every other error status, which `classify` grades as a collection failure. A
          502 is not a refusal — nobody refused anything — and calling it one put a bad gateway in
          the summary under the word reserved for a token that may not look.

        THE STATUS IS THE ONE THE FAILURE IS, not always the one it arrived under: a GraphQL refusal
        is logged and counted at 403, marked `(equivalent)` so nobody reads it as a status HTTP
        returned. A log grepped for `403` is how a permission problem is found, and the estate's
        largest one was invisible to that grep while it was reported as the 200 it travelled in.

        The word and the status the line is logged under are the word and the status the call is
        counted under, so the end-of-run summary and the log cannot disagree about a call.
        """
        failure = body_failure(response) if body_failure is not None else None
        status = response.status_code if failure is None else failure.status
        if response.status_code < HTTPStatus.BAD_REQUEST:
            if failure is None:
                outcome = "ok"
                logging.debug(
                    "GitHub ok %s %s (%s bytes)",
                    status,
                    description,
                    len(response.content or b""),
                )
            else:
                outcome = "errors"
                equivalent = f"{status} (equivalent)" if status != response.status_code else str(status)
                logging.warning("GitHub errors %s %s: %s", equivalent, description, failure.summary)
        else:
            message = self.failure_message(response)
            if self.feature_disabled(response):
                outcome = "disabled"
                logging.debug("GitHub disabled %s %s: %s", status, description, message)
            elif response.status_code in self.http_failures:
                outcome = "refused"
                logging.warning("GitHub refused %s %s: %s", status, description, message)
            else:
                outcome = "failed"
                logging.warning("GitHub failed %s %s: %s", status, description, message)
        self.count_call(status, outcome, description)

    def count_call(self, status: int, outcome: str, description: str) -> None:
        """Count one response under the endpoint it was issued against.

        The description is what the caller asked for — `GET <url>`, or `POST <url> <variables>` for
        GraphQL — so the method is its first word and the endpoint its second. Reading them back out
        of it keeps the counted endpoint and the logged one the same string by construction.
        """
        method, _, remainder = description.partition(" ")
        self.outcomes[(status, outcome, method, endpoint_template(remainder.partition(" ")[0]))] += 1

    def validate(self, response: Response) -> Response:
        """Classify terminal GitHub HTTP responses.

        Classification only: `log_outcome` has already reported this response, and GitHub's own text
        went there rather than into the exception, whose message becomes one repository's `detail` in
        a report meant to be shared.
        """
        try:
            response.raise_for_status()
        except HTTPError as exception:
            message, reason = self.classify(response)
            raise GitHubError(message, reason, response.status_code) from exception
        return response
