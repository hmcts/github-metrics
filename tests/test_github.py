"""Test GitHub REST and GraphQL API access."""

import logging
from unittest.mock import MagicMock, patch

import pytest
from requests import ConnectionError as RequestsConnectionError
from requests import Response, Session
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
    """Wait for a low REST budget without delaying a separate GraphQL resource."""
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
    client = GitHubClient("secret", session, pause=pause, clock=lambda: 100)
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
