"""Test GitHub REST and GraphQL API access."""

import logging
from unittest.mock import MagicMock, patch

import pytest
from requests import ConnectionError as RequestsConnectionError
from requests import HTTPError, Response, Session
from requests.exceptions import JSONDecodeError as RequestsJSONDecodeError

from metrics.domain import AvailabilityReason
from metrics.github import GitHubClient, GitHubError, RateLimitBudget


def test_get_repository_sends_authenticated_request() -> None:
    """Send authenticated repository requests with stable API headers."""
    session = Session()

    with patch.object(session, "get") as get:
        get.return_value.status_code = 200
        GitHubClient("secret", session).get_repository("hmcts", "nfdiv-case-api")

    assert session.headers["Accept"] == "application/vnd.github+json"
    assert session.headers["Authorization"] == "Bearer secret"
    assert session.headers["X-GitHub-Api-Version"] == "2022-11-28"
    get.assert_called_once_with(
        "https://api.github.com/repos/hmcts/nfdiv-case-api",
        timeout=30,
    )
    get.return_value.raise_for_status.assert_called_once_with()


@pytest.mark.parametrize(
    ("status_code", "message"),
    [
        (401, "GitHub authentication failed"),
        (403, "GitHub permission denied"),
        (404, "GitHub repository not found or inaccessible"),
        (422, "GitHub returned HTTP 422"),
        (500, "GitHub returned HTTP 500"),
    ],
)
def test_get_reports_http_failure(status_code: int, message: str) -> None:
    """Translate GitHub response codes into actionable failures."""
    session = Session()
    response = Response()
    response.status_code = status_code
    response.url = "https://api.github.com/repos/hmcts/nfdiv-case-api"
    client = GitHubClient("secret", session, pause=MagicMock())
    with (
        patch.object(session, "get", return_value=response),
        pytest.raises(GitHubError, match=message),
    ):
        client.get("https://api.github.com/repos/hmcts/nfdiv-case-api")


def test_get_leaves_a_422_unclassified_for_every_reader_but_the_one_that_knows_it() -> None:
    """Never let one endpoint's meaning for `422` become an affirmative observation everywhere.

    `confirms_candidate` reads a `422` from the commit endpoint as "the repository does not hold
    it", but two other readers turn `NOT_FOUND_OR_INACCESSIBLE` into a published fact — "the branch
    is unprotected", "the alert family is not enabled". Classifying `422` in the shared table would
    manufacture those facts out of a malformed or throttled request, so the status is carried on the
    error and read only by the call that knows what it means.
    """
    session = Session()
    response = Response()
    response.status_code = 422
    response.url = "https://api.github.com/repos/hmcts/nfdiv-case-api/dependabot/alerts"
    client = GitHubClient("secret", session, pause=MagicMock())
    with patch.object(session, "get", return_value=response), pytest.raises(GitHubError) as captured:
        client.get("https://api.github.com/repos/hmcts/nfdiv-case-api/dependabot/alerts")

    assert captured.value.reason is AvailabilityReason.COLLECTION_FAILED
    assert captured.value.status == 422


def test_get_reports_network_failure() -> None:
    """Preserve the cause of network failures."""
    session = Session()
    pause = MagicMock()
    client = GitHubClient("secret", session, pause=pause)
    with (
        patch.object(session, "get", side_effect=RequestsConnectionError("offline")) as get,
        pytest.raises(GitHubError, match="GitHub request failed after 3 attempts: offline"),
    ):
        client.get("https://api.github.com/repos/hmcts/nfdiv-case-api")

    assert get.call_count == 3
    assert [call.args for call in pause.call_args_list] == [(1,), (2,)]


def test_repository_failure_has_structured_reason() -> None:
    """Expose machine-readable unavailable evidence classifications."""
    session = Session()
    response = Response()
    response.status_code = 403
    client = GitHubClient("secret", session)
    with (
        patch.object(session, "get", return_value=response),
        pytest.raises(GitHubError) as captured,
    ):
        client.get("https://api.github.com/repos/hmcts/nfdiv-case-api")

    assert captured.value.reason is AvailabilityReason.PERMISSION_DENIED


def test_get_retries_transient_server_failure() -> None:
    """Retry transient server failures with exponential backoff."""
    session = Session()
    unavailable = Response()
    unavailable.status_code = 503
    success = Response()
    success.status_code = 200
    pause = MagicMock()
    client = GitHubClient("secret", session, pause=pause)
    with patch.object(session, "get", side_effect=[unavailable, success]) as get:
        assert client.get("https://api.github.com/example") is success

    assert get.call_count == 2
    pause.assert_called_once_with(1)


def test_get_honors_retry_after() -> None:
    """Honour GitHub's secondary rate-limit Retry-After header."""
    session = Session()
    limited = Response()
    limited.status_code = 429
    limited.headers["retry-after"] = "5"
    success = Response()
    success.status_code = 200
    pause = MagicMock()
    client = GitHubClient("secret", session, pause=pause)
    with patch.object(session, "get", side_effect=[limited, success]):
        assert client.get("https://api.github.com/example") is success

    pause.assert_called_once_with(5)


def test_get_honors_primary_rate_limit_reset() -> None:
    """Wait until GitHub's primary rate-limit reset time."""
    session = Session()
    limited = Response()
    limited.status_code = 403
    limited.headers.update({"x-ratelimit-remaining": "0", "x-ratelimit-reset": "112"})
    success = Response()
    success.status_code = 200
    pause = MagicMock()
    client = GitHubClient("secret", session, pause=pause, clock=lambda: 100)
    with patch.object(session, "get", side_effect=[limited, success]):
        assert client.get("https://api.github.com/example") is success

    pause.assert_called_once_with(12)


def test_get_uses_secondary_rate_limit_default() -> None:
    """Wait at least one minute when a secondary limit omits delay headers."""
    session = Session()
    limited = MagicMock(status_code=403, headers={}, text="secondary rate limit exceeded")
    success = Response()
    success.status_code = 200
    pause = MagicMock()
    client = GitHubClient("secret", session, pause=pause)
    with patch.object(session, "get", side_effect=[limited, success]):
        assert client.get("https://api.github.com/example") is success

    pause.assert_called_once_with(60)


def test_get_never_retries_a_403_that_is_a_refusal_rather_than_a_rate_limit() -> None:
    """Refuse a permission denial immediately: a 403 carrying no rate-limit signal is not one.

    GitHub answers both a spent quota and "you may not read this" with 403, and the commit search
    meets both. Waiting a minute for a refusal to change its mind buys nothing and is indistinguishable
    from a hang in a log, so only `retry-after`, an exhausted remaining count, or a body that says
    "rate limit" may cost a pause.
    """
    session = Session()
    refused = MagicMock(
        status_code=403,
        headers={"x-ratelimit-remaining": "27", "x-ratelimit-limit": "30", "x-ratelimit-resource": "search"},
        text='{"message":"Resource not accessible by personal access token"}',
    )
    refused.raise_for_status.side_effect = HTTPError(response=refused)
    refused.json.return_value = {"message": "Resource not accessible by personal access token"}
    pause = MagicMock()
    client = GitHubClient("secret", session, pause=pause)
    with (
        patch.object(session, "get", return_value=refused) as get,
        pytest.raises(GitHubError, match="GitHub permission denied") as captured,
    ):
        client.get("https://api.github.com/search/commits")

    assert captured.value.reason is AvailabilityReason.PERMISSION_DENIED
    assert get.call_count == 1
    pause.assert_not_called()


def test_get_logs_githubs_own_message_for_a_refusal_without_carrying_it_into_the_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Diagnose a 403 from the log, while the exception keeps the fixed shareable message.

    "API rate limit exceeded", "secondary rate limit", "not accessible by personal access token" and
    an IP-allow-list refusal are four different problems with four different fixes, and every one of
    them is HTTP 403. The exception message becomes a repository's `detail` in a report meant to be
    shared, so GitHub's text goes to the log instead of into it.
    """
    session = Session()
    refused = MagicMock(status_code=403, headers={}, text="", url="https://api.github.com/search/commits")
    refused.raise_for_status.side_effect = HTTPError(response=refused)
    refused.json.return_value = {"message": "Although you appear to have the correct authorization credentials"}
    client = GitHubClient("secret", session, pause=MagicMock())
    with (
        caplog.at_level(logging.WARNING),
        patch.object(session, "get", return_value=refused),
        pytest.raises(GitHubError) as captured,
    ):
        client.get("https://api.github.com/search/commits")

    assert "Although you appear to have the correct authorization credentials" in caplog.text
    assert "Although you appear" not in str(captured.value)


@pytest.mark.parametrize(
    "headers",
    [
        {"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"},
        {"x-ratelimit-remaining": "0"},
    ],
)
def test_get_survives_rate_limit_headers_it_cannot_read_as_numbers(headers: dict[str, str]) -> None:
    """Grade a 403 whose delay headers are unusable, rather than crashing out of the retry path.

    `retry-after` is allowed to be an HTTP-date, and `x-ratelimit-reset` can be missing from the very
    response whose remaining count is `0`. Both used to raise out of `rate_limit_delay` as a
    ValueError or KeyError — an unclassified crash in place of a graded refusal.
    """
    session = Session()
    refused = MagicMock(status_code=403, headers=headers, text="")
    refused.raise_for_status.side_effect = HTTPError(response=refused)
    refused.json.return_value = {"message": "Resource not accessible"}
    client = GitHubClient("secret", session, pause=MagicMock())
    with (
        patch.object(session, "get", return_value=refused),
        pytest.raises(GitHubError) as captured,
    ):
        client.get("https://api.github.com/example")

    assert captured.value.reason is AvailabilityReason.PERMISSION_DENIED


def test_the_client_reports_the_budget_github_named_so_a_scarce_quota_can_be_paced() -> None:
    """Expose the recorded budget under GitHub's OWN resource name, which need not be the caller's.

    The commit search is issued under `commit-search` so that `wait_for_rate_limit` — whose reserve
    of 100 is tuned to a 5,000-an-hour quota — leaves a 30-a-minute one alone. GitHub reports it as
    `search`, and the hand pacer needs the real numbers to pace off.
    """
    session = Session()
    response = MagicMock(
        status_code=200,
        headers={
            "x-ratelimit-resource": "search",
            "x-ratelimit-limit": "10",
            "x-ratelimit-remaining": "7",
            "x-ratelimit-used": "3",
            "x-ratelimit-reset": "160",
        },
    )
    client = GitHubClient("secret", session, pause=MagicMock(), clock=lambda: 100)
    with patch.object(session, "get", return_value=response):
        client.get("https://api.github.com/search/commits", resource="commit-search")

    assert client.budget("search") == RateLimitBudget(limit=10, remaining=7, used=3, resets_at=160.0)
    assert client.budget("commit-search") is None


def test_get_stops_retrying_rate_limit() -> None:
    """Classify a rate limit that remains after bounded retries."""
    session = Session()
    limited = Response()
    limited.status_code = 429
    limited.headers["retry-after"] = "1"
    pause = MagicMock()
    client = GitHubClient("secret", session, pause=pause)
    with (
        patch.object(session, "get", return_value=limited),
        pytest.raises(GitHubError, match="rate limit exceeded after 3 attempts") as captured,
    ):
        client.get("https://api.github.com/example")

    assert captured.value.reason is AvailabilityReason.RATE_LIMITED
    assert [call.args for call in pause.call_args_list] == [(1,), (1,)]


def test_get_paginated_follows_link_headers() -> None:
    """Preserve the requested page size and follow GitHub's next URL."""
    session = Session()
    first = MagicMock(status_code=200)
    first.json.return_value = [{"id": 1}]
    first.links = {"next": {"url": "https://api.github.com/example?page=2"}}
    second = MagicMock(status_code=200)
    second.json.return_value = [{"id": 2}]
    second.links = {}
    client = GitHubClient("secret", session)
    with patch.object(session, "get", side_effect=[first, second]) as get:
        records = client.get_paginated("https://api.github.com/example", {"state": "open", "per_page": 50})

    assert records == ({"id": 1}, {"id": 2})
    assert get.call_args_list[0].kwargs == {"timeout": 30, "params": {"state": "open", "per_page": 50}}
    assert get.call_args_list[1].kwargs == {"timeout": 30}


@pytest.mark.parametrize("page", [{"id": 1}, [1]])
def test_get_paginated_rejects_invalid_records(page: object) -> None:
    """Reject paginated payloads that are not arrays of records."""
    session = Session()
    response = MagicMock(status_code=200)
    response.json.return_value = page
    response.links = {}
    client = GitHubClient("secret", session)
    with (
        patch.object(session, "get", return_value=response),
        pytest.raises(GitHubError, match="invalid paginated records"),
    ):
        client.get_paginated("https://api.github.com/example")


def test_get_paginated_rejects_invalid_json() -> None:
    """Report malformed JSON returned by a paginated endpoint."""
    session = Session()
    response = MagicMock(status_code=200)
    response.json.side_effect = RequestsJSONDecodeError("invalid", "{", 1)
    client = GitHubClient("secret", session)
    with (
        patch.object(session, "get", return_value=response),
        pytest.raises(GitHubError, match="invalid paginated JSON at line 1, column 2"),
    ):
        client.get_paginated("https://api.github.com/example")


def test_get_paginated_rejects_unsafe_next_url() -> None:
    """Do not send authenticated pagination requests to another origin."""
    session = Session()
    response = MagicMock(status_code=200)
    response.json.return_value = []
    response.links = {"next": {"url": "https://example.com/next"}}
    client = GitHubClient("secret", session)
    with (
        patch.object(session, "get", return_value=response) as get,
        pytest.raises(GitHubError, match="unsafe pagination URL"),
    ):
        client.get_paginated("https://api.github.com/example")

    get.assert_called_once()


def test_graphql_sends_query_and_returns_data() -> None:
    """Send GraphQL queries through the authenticated Requests session."""
    session = Session()
    response = MagicMock(status_code=200)
    response.json.return_value = {"data": {"repository": {"name": "cath-service"}}}
    client = GitHubClient("secret", session)
    query = 'query Repository($organization: String!) { repository(owner: $organization, name: "cath-service") }'
    with patch.object(session, "post", return_value=response) as post:
        data = client.graphql(query, {"organization": "hmcts"})

    assert data == {"repository": {"name": "cath-service"}}
    post.assert_called_once_with(
        "https://api.github.com/graphql",
        json={"query": query, "variables": {"organization": "hmcts"}},
        timeout=30,
    )
    response.raise_for_status.assert_called_once_with()


def test_graphql_retries_rate_limit_returned_with_http_success() -> None:
    """Apply shared rate-limit retries to GraphQL HTTP 200 error responses."""
    session = Session()
    limited = MagicMock(status_code=200, headers={})
    limited.json.return_value = {"errors": [{"type": "RATE_LIMITED", "message": "API rate limit exceeded"}]}
    success = MagicMock(status_code=200)
    success.json.return_value = {"data": {"viewer": {"login": "octocat"}}}
    pause = MagicMock()
    client = GitHubClient("secret", session, pause=pause)
    with patch.object(session, "post", side_effect=[limited, success]) as post:
        data = client.graphql("query { viewer { login } }")

    assert data == {"viewer": {"login": "octocat"}}
    assert post.call_count == 2
    pause.assert_called_once_with(60)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "invalid GraphQL response"),
        ({"data": None}, "omitted its data object"),
        ({"data": {1: "invalid key"}}, "omitted its data object"),
    ],
)
def test_graphql_rejects_invalid_response_shapes(payload: object, message: str) -> None:
    """Reject GraphQL payloads without a string-keyed data object."""
    session = Session()
    response = MagicMock(status_code=200)
    response.json.return_value = payload
    client = GitHubClient("secret", session)
    with (
        patch.object(session, "post", return_value=response),
        pytest.raises(GitHubError, match=message) as captured,
    ):
        client.graphql("query { viewer { login } }")

    assert captured.value.reason is AvailabilityReason.COLLECTION_FAILED


def test_graphql_rejects_invalid_json() -> None:
    """Report malformed JSON returned by the GraphQL endpoint."""
    session = Session()
    response = MagicMock(status_code=200)
    response.json.side_effect = RequestsJSONDecodeError("invalid", "{", 1)
    client = GitHubClient("secret", session)
    with (
        patch.object(session, "post", return_value=response),
        pytest.raises(GitHubError, match="invalid GraphQL JSON at line 1, column 2"),
    ):
        client.graphql("query { viewer { login } }")


@pytest.mark.parametrize(
    ("errors", "reason"),
    [
        ([{"type": "FORBIDDEN", "message": "sensitive detail"}], AvailabilityReason.PERMISSION_DENIED),
        ([{"message": "Resource not accessible by integration"}], AvailabilityReason.PERMISSION_DENIED),
        ([{"type": "NOT_FOUND", "message": "sensitive detail"}], AvailabilityReason.COLLECTION_FAILED),
        ("invalid errors", AvailabilityReason.COLLECTION_FAILED),
    ],
)
def test_graphql_classifies_errors_without_exposing_details(errors: object, reason: AvailabilityReason) -> None:
    """Classify GraphQL failures without returning external error content."""
    session = Session()
    response = MagicMock(status_code=200)
    response.json.return_value = {"errors": errors}
    client = GitHubClient("secret", session)
    with (
        patch.object(session, "post", return_value=response),
        pytest.raises(GitHubError, match="GitHub GraphQL returned errors") as captured,
    ):
        client.graphql("query { viewer { login } }")

    assert captured.value.reason is reason
    assert "sensitive detail" not in str(captured.value)


def test_graphql_logs_the_error_detail_it_keeps_out_of_the_report(caplog: pytest.LogCaptureFixture) -> None:
    """Log which repository failed and why, while the raised message stays fixed.

    A collection that lost repositories reports one identical `detail` for every one of them, so the
    log is the only place a repository that was renamed can be told apart from one a token may not
    read.
    """
    session = Session()
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "errors": [{"type": "NOT_FOUND", "message": "Could not resolve to a Repository with the name 'hmcts/gone'."}],
    }
    client = GitHubClient("secret", session)
    with (
        caplog.at_level(logging.WARNING),
        patch.object(session, "post", return_value=response),
        pytest.raises(GitHubError, match="GitHub GraphQL returned errors"),
    ):
        client.graphql("query { viewer { login } }", {"repository": "gone"})

    assert "NOT_FOUND: Could not resolve to a Repository with the name 'hmcts/gone'." in caplog.text
    assert '{"repository": "gone"}' in caplog.text


@pytest.mark.parametrize(
    ("headers", "clock", "delay"),
    [
        ({"retry-after": "5"}, 0, 5),
        ({"x-ratelimit-remaining": "0", "x-ratelimit-reset": "112"}, 100, 12),
    ],
)
def test_graphql_honors_rate_limit_headers(headers: dict[str, str], clock: int, delay: int) -> None:
    """Use GitHub's explicit GraphQL rate-limit wait when available."""
    session = Session()
    limited = MagicMock(status_code=200, headers=headers)
    limited.json.return_value = {"errors": [{"type": "RATE_LIMITED"}]}
    success = MagicMock(status_code=200)
    success.json.return_value = {"data": {"viewer": {"login": "octocat"}}}
    pause = MagicMock()
    client = GitHubClient("secret", session, pause=pause, clock=lambda: clock)
    with patch.object(session, "post", side_effect=[limited, success]):
        client.graphql("query { viewer { login } }")

    pause.assert_called_once_with(delay)


def test_graphql_stops_retrying_rate_limit() -> None:
    """Classify a GraphQL rate limit that remains after bounded retries."""
    session = Session()
    limited = MagicMock(status_code=200, headers={})
    limited.json.return_value = {"errors": [{"type": "RATE_LIMITED"}]}
    pause = MagicMock()
    client = GitHubClient("secret", session, pause=pause)
    with (
        patch.object(session, "post", return_value=limited) as post,
        pytest.raises(GitHubError, match="rate limit exceeded after 3 attempts") as captured,
    ):
        client.graphql("query { viewer { login } }")

    assert captured.value.reason is AvailabilityReason.RATE_LIMITED
    assert post.call_count == 3
    assert [call.args for call in pause.call_args_list] == [(60,), (60,)]


def test_client_preserves_resource_specific_rate_limit_reserve() -> None:
    """Wait for a spent REST budget without delaying a separate GraphQL resource."""
    session = Session()
    rest = MagicMock(status_code=200)
    rest.headers = {
        "x-ratelimit-resource": "core",
        "x-ratelimit-limit": "5000",
        "x-ratelimit-remaining": "100",
        "x-ratelimit-used": "4900",
        "x-ratelimit-reset": "112",
    }
    graphql = MagicMock(status_code=200, headers={})
    graphql.json.return_value = {"data": {"viewer": {"login": "octocat"}}}
    pause = MagicMock()
    client = GitHubClient("secret", session, pause=pause, clock=lambda: 100, rate_limit_reserve=100)
    with (
        patch.object(session, "get", return_value=rest),
        patch.object(session, "post", return_value=graphql),
    ):
        client.get("https://api.github.com/example")
        client.graphql("query { viewer { login } }")
        client.get("https://api.github.com/example")

    assert client.rate_limits == {
        "core": RateLimitBudget(limit=5000, remaining=100, used=4900, resets_at=112),
    }
    pause.assert_called_once_with(12)


def test_the_client_spends_a_budget_to_its_last_call_rather_than_stopping_at_a_reserve() -> None:
    """Never park a window that still has calls in it — the default reserve is zero.

    THE BUG THIS EXISTS FOR. The reserve defaulted to an ABSOLUTE 100, so a `graphql` budget of
    5,000 stopped the run with 75 still in hand and slept out the rest of the hour: 100 calls thrown
    away, and a log line calling a 98%-spent quota "exhausted". Holding quota back means something
    only when another consumer shares the token, so it is now asked for rather than assumed.
    """
    session = Session()
    low = MagicMock(status_code=200)
    low.headers = {
        "x-ratelimit-resource": "core",
        "x-ratelimit-limit": "5000",
        "x-ratelimit-remaining": "75",
        "x-ratelimit-used": "4925",
        "x-ratelimit-reset": "585",
    }
    pause = MagicMock()
    client = GitHubClient("secret", session, pause=pause, clock=lambda: 100)
    with patch.object(session, "get", return_value=low):
        client.get("https://api.github.com/example")
        client.get("https://api.github.com/example")

    pause.assert_not_called()


def test_the_client_waits_only_once_the_window_has_genuinely_nothing_left() -> None:
    """Wait at zero, where the next call is certain to be refused and would cost a retry to prove it."""
    session = Session()
    spent = MagicMock(status_code=200)
    spent.headers = {
        "x-ratelimit-resource": "core",
        "x-ratelimit-limit": "5000",
        "x-ratelimit-remaining": "0",
        "x-ratelimit-used": "5000",
        "x-ratelimit-reset": "585",
    }
    pause = MagicMock()
    client = GitHubClient("secret", session, pause=pause, clock=lambda: 100)
    with patch.object(session, "get", return_value=spent):
        client.get("https://api.github.com/example")
        client.get("https://api.github.com/example")

    pause.assert_called_once_with(485)


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"x-ratelimit-resource": "core"},
        {
            "x-ratelimit-resource": "core",
            "x-ratelimit-limit": "invalid",
            "x-ratelimit-remaining": "100",
            "x-ratelimit-used": "4900",
            "x-ratelimit-reset": "112",
        },
    ],
)
def test_client_ignores_incomplete_rate_limit_headers(headers: dict[str, str]) -> None:
    """Do not replace known budget state with incomplete response metadata."""
    client = GitHubClient("secret", Session())
    response = MagicMock(headers=headers)

    client.record_rate_limit(response)

    assert client.rate_limits == {}


def test_the_client_counts_every_call_it_issues() -> None:
    """Count REST, paginated and GraphQL calls on one shared running total."""
    session = Session()
    first = MagicMock(status_code=200)
    first.json.return_value = [{"id": 1}]
    first.links = {"next": {"url": "https://api.github.com/example?page=2"}}
    second = MagicMock(status_code=200)
    second.json.return_value = [{"id": 2}]
    second.links = {}
    graphql = MagicMock(status_code=200)
    graphql.json.return_value = {"data": {"viewer": {"login": "octocat"}}}
    client = GitHubClient("secret", session)

    assert client.requests_issued == 0

    with (
        patch.object(session, "get", side_effect=[first, second, first, second]),
        patch.object(session, "post", return_value=graphql),
    ):
        client.get_paginated("https://api.github.com/example")
        client.graphql("query { viewer { login } }")
        client.get_repository("hmcts", "nfdiv-case-api")

    # Two pages, one GraphQL query, one repository read: a paginated call costs one per page.
    assert client.requests_issued == 4


def test_debug_logging_never_writes_a_response_body(caplog: pytest.LogCaptureFixture) -> None:
    """Keep response bodies out of the log: one of them is a list of live credentials.

    The secret-scanning endpoint returns the literal detected secret in each alert, so logging every
    body at DEBUG would copy production keys out of GitHub's access controls into a plain file. Only
    the size is logged, which is what a collection actually needs diagnosing.
    """
    session = Session()
    response = Response()
    response.status_code = 200
    response.url = "https://api.github.com/repos/hmcts/nfdiv-case-api/secret-scanning/alerts"
    response._content = b'[{"number": 1, "secret": "ghp_averyrealtokenindeed"}]'  # noqa: SLF001
    client = GitHubClient("secret", session)

    with caplog.at_level(logging.DEBUG), patch.object(session, "get", return_value=response):
        client.get(response.url)

    assert "ghp_averyrealtokenindeed" not in caplog.text
    assert f"{len(response.content)} bytes" in caplog.text


def test_the_client_counts_a_call_that_failed() -> None:
    """Count a call GitHub refused, which cost the same as one it answered."""
    session = Session()
    response = Response()
    response.status_code = 403
    response.url = "https://api.github.com/repos/hmcts/nfdiv-case-api"
    client = GitHubClient("secret", session, pause=MagicMock())
    with patch.object(session, "get", return_value=response), pytest.raises(GitHubError):
        client.get("https://api.github.com/repos/hmcts/nfdiv-case-api")

    assert client.requests_issued == 1


def test_the_client_counts_a_retried_call_once() -> None:
    """Count calls issued rather than HTTP round trips, so a retry is not read as more work."""
    session = Session()
    failed = MagicMock(status_code=502, headers={}, text="")
    success = MagicMock(status_code=200, headers={})
    success.json.return_value = {"data": {"viewer": {"login": "octocat"}}}
    client = GitHubClient("secret", session, pause=MagicMock())
    with patch.object(session, "post", side_effect=[failed, success]) as post:
        client.graphql("query { viewer { login } }")

    assert post.call_count == 2
    assert client.requests_issued == 1
