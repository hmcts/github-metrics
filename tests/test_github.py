"""Test GitHub REST and GraphQL API access."""

import logging
from typing import cast
from unittest.mock import MagicMock, patch

import pytest
from requests import ConnectionError as RequestsConnectionError
from requests import HTTPError, Response, Session
from requests.exceptions import JSONDecodeError as RequestsJSONDecodeError

from metrics.domain import AvailabilityReason
from metrics.github import CallOutcome, GitHubClient, GitHubError, RateLimitBudget, endpoint_template


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


def test_get_reports_network_failure(caplog: pytest.LogCaptureFixture) -> None:
    """Preserve the cause of network failures, and say which call they happened to.

    A failure that never produced a response has no URL of its own to log, so the warning carries
    the description built before the request. Without it a run that died on read timeouts recorded
    three timeouts and no endpoint.
    """
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
    named = [
        record
        for record in caplog.records
        if "GET https://api.github.com/repos/hmcts/nfdiv-case-api" in record.getMessage()
    ]
    assert len(named) == len(caplog.records) == 2


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


def test_get_stops_retrying_rate_limit(caplog: pytest.LogCaptureFixture) -> None:
    """Classify a rate limit that remains after bounded retries, and still report the call it ended on.

    The response the retries ran out on is a call like any other: it is logged once and counted, or
    the summary of the run a rate limit killed omits the very calls that killed it.
    """
    session = Session()
    limited = Response()
    limited.status_code = 429
    limited.headers["retry-after"] = "1"
    pause = MagicMock()
    client = GitHubClient("secret", session, pause=pause)
    with (
        caplog.at_level(logging.DEBUG),
        patch.object(session, "get", return_value=limited),
        pytest.raises(GitHubError, match="rate limit exceeded after 3 attempts") as captured,
    ):
        client.get("https://api.github.com/example")

    assert captured.value.reason is AvailabilityReason.RATE_LIMITED
    assert [call.args for call in pause.call_args_list] == [(1,), (1,)]
    # `failed`, not `refused`: a spent quota is not an access decision, and `refused` is the word
    # reserved for the three statuses GitHub answers one with.
    assert client.call_outcomes == {(429, "failed", "GET", "https://api.github.com/example"): 1}
    # One line per attempt: two retry warnings, then the outcome of the response it gave up on.
    assert [record.levelno for record in caplog.records] == [logging.WARNING] * 3
    assert caplog.records[2].getMessage().startswith("GitHub failed 429 GET https://api.github.com/example")


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
    # Only a refusal has an equivalent status: a repository that was renamed is not a 403, and
    # `COLLECTION_FAILED` names no status a reader would search a log for.
    assert "GitHub errors 200 POST" in caplog.text
    assert "equivalent" not in caplog.text


def test_a_graphql_failure_is_counted_rather_than_hidden_inside_the_successes() -> None:
    """Count a query GitHub refused as a failure, though HTTP answered it 200.

    THE BUG THIS EXISTS FOR. A GraphQL failure arrives as HTTP 200 with an `errors` array, and the
    status was read on its own: one run reported `2158 200 ok POST /graphql` and no GraphQL problem
    whatever, when 181 of those calls had failed FORBIDDEN and cost the run 181 repository windows.

    Counted at the 403 the refusal IS, beside the refusals of the REST endpoints, rather than at the
    200 GitHub wrapped it in — which is the status nobody looking for a permission problem searches.
    """
    session = Session()
    refused = MagicMock(status_code=200, headers={}, content=b"{}")
    refused.json.return_value = {"errors": [{"type": "FORBIDDEN", "message": "Resource not accessible"}]}
    answered = MagicMock(status_code=200, headers={}, content=b"{}")
    answered.json.return_value = {"data": {"repository": {"name": "cath-service"}}}
    client = GitHubClient("secret", session)

    with patch.object(session, "post", side_effect=[answered, refused]):
        client.graphql("query { repository { name } }", {"repository": "cath-service"})
        with pytest.raises(GitHubError, match="GitHub GraphQL returned errors"):
            client.graphql("query { repository { name } }", {"repository": "hidden"})

    assert client.call_outcomes == {
        (200, "ok", "POST", "https://api.github.com/graphql"): 1,
        (403, "errors", "POST", "https://api.github.com/graphql"): 1,
    }


def test_a_graphql_failure_logs_one_warning_carrying_the_variables_it_failed_for(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Report the failure once, under the status and word it is counted by, naming the repository.

    One line, not two: the errors used to be logged by `graphql` on top of the `200 ok` line
    `log_outcome` had already written, so one failed call said it had worked and then said it had not.

    `403 (equivalent)` rather than `403`, because HTTP did not return one — but it IS greppable for
    `403`, which is how a permission problem is looked for in a log of twenty thousand calls.
    """
    session = Session()
    refused = MagicMock(status_code=200, headers={}, content=b"{}")
    refused.json.return_value = {"errors": [{"type": "FORBIDDEN", "message": "Resource not accessible"}]}
    client = GitHubClient("secret", session)

    with (
        caplog.at_level(logging.DEBUG),
        patch.object(session, "post", return_value=refused),
        pytest.raises(GitHubError, match="GitHub GraphQL returned errors"),
    ):
        client.graphql("query { repository { name } }", {"repository": "hidden"})

    assert [(record.levelno, record.getMessage()) for record in caplog.records] == [
        (
            logging.WARNING,
            (
                "GitHub errors 403 (equivalent) POST https://api.github.com/graphql "
                '{"repository": "hidden"}: FORBIDDEN: Resource not accessible'
            ),
        ),
    ]


def test_repeated_graphql_errors_are_counted_rather_than_written_out_one_by_one(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Report each kind of error once with its count, keeping a rare kind above a common one.

    GitHub returns one error per node it will not answer for, so a single search returned the same
    FORBIDDEN sentence 76 times and one run wrote it out 6,880 times. The 76th copy says nothing the
    first did, and the error that appears once is the one worth reading.
    """
    session = Session()
    refused = MagicMock(status_code=200, headers={}, content=b"{}")
    refused.json.return_value = {
        "errors": [
            {"type": "NOT_FOUND", "message": "Could not resolve to a User"},
            *({"type": "FORBIDDEN", "message": "Resource not accessible"} for _ in range(76)),
        ],
    }
    client = GitHubClient("secret", session)

    with (
        caplog.at_level(logging.WARNING),
        patch.object(session, "post", return_value=refused),
        pytest.raises(GitHubError, match="GitHub GraphQL returned errors"),
    ):
        client.graphql("query { repository { name } }", {"repository": "hidden"})

    summarised = "NOT_FOUND: Could not resolve to a User; FORBIDDEN: Resource not accessible (x76)"
    assert caplog.text.count("Resource not accessible") == 1
    assert caplog.records[0].getMessage().endswith(summarised)


def test_a_graphql_body_that_is_not_a_json_object_carries_no_counted_errors() -> None:
    """Grade only an `errors` array as a failure this classified, leaving the rest to `graphql`.

    A body that is not JSON, or that is not an object at all, is a failure `graphql` raises on. The
    outcome does not claim to have read errors it never saw.
    """
    session = Session()
    client = GitHubClient("secret", session)
    unreadable = MagicMock(status_code=200, headers={}, content=b"[]")
    unreadable.json.return_value = []
    invalid = MagicMock(status_code=200, headers={}, content=b"{")
    invalid.json.side_effect = RequestsJSONDecodeError("invalid", "{", 1)

    assert client.graphql_body_failure(unreadable) is None
    assert client.graphql_body_failure(invalid) is None


def test_a_bad_gateway_is_a_failed_call_rather_than_a_refused_one(caplog: pytest.LogCaptureFixture) -> None:
    """Keep `refused` for an access decision: nobody refused a 502.

    A bad gateway was counted under the word reserved for a token that may not look, which reads in
    the summary as a permission problem to chase rather than the transient it is. It is retried
    first — three attempts — and only the response those ended on is counted.
    """
    session = Session()
    unavailable = MagicMock(status_code=502, headers={}, text="", url="https://api.github.com/graphql")
    unavailable.raise_for_status.side_effect = HTTPError(response=unavailable)
    unavailable.json.return_value = {}
    pause = MagicMock()
    client = GitHubClient("secret", session, pause=pause)

    with (
        caplog.at_level(logging.DEBUG),
        patch.object(session, "post", return_value=unavailable) as post,
        pytest.raises(GitHubError, match="GitHub returned HTTP 502") as captured,
    ):
        client.graphql("query { viewer { login } }")

    assert captured.value.reason is AvailabilityReason.COLLECTION_FAILED
    assert post.call_count == 3
    assert [call.args for call in pause.call_args_list] == [(1,), (2,)]
    assert client.call_outcomes == {(502, "failed", "POST", "https://api.github.com/graphql"): 1}
    assert caplog.records[-1].getMessage().startswith("GitHub failed 502 POST https://api.github.com/graphql")


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


@pytest.mark.parametrize(
    "headers",
    [
        {"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"},
        {"x-ratelimit-remaining": "0"},
    ],
)
def test_graphql_survives_rate_limit_headers_it_cannot_read_as_numbers(headers: dict[str, str]) -> None:
    """Fall back to the flat wait when GraphQL's delay headers are unusable, rather than crashing.

    The same two shapes `rate_limit_delay` already guards against, on the path that reaches GitHub's
    GraphQL rate limit: `retry-after` may be an HTTP-date, and `x-ratelimit-reset` can be missing
    from the very response whose remaining count is `0`. Unguarded, both raised out of `request`'s
    `retry_delay` call as a ValueError or KeyError — neither of which is a `GitHubError`, so a header
    this client merely could not parse ended the whole collection in a traceback.
    """
    session = Session()
    limited = MagicMock(status_code=200, headers=headers)
    limited.json.return_value = {"errors": [{"type": "RATE_LIMITED"}]}
    success = MagicMock(status_code=200)
    success.json.return_value = {"data": {"viewer": {"login": "octocat"}}}
    pause = MagicMock()
    client = GitHubClient("secret", session, pause=pause, clock=lambda: 100)
    with patch.object(session, "post", side_effect=[limited, success]):
        client.graphql("query { viewer { login } }")

    pause.assert_called_once_with(60)


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
    # Counted at the status HTTP answered, under the outcome the BODY answered: a GraphQL rate limit
    # arrives inside a 200, and counting it `ok` would report the call that killed the run as one
    # that worked.
    assert client.call_outcomes == {(200, "errors", "POST", "https://api.github.com/graphql"): 1}


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


def refused_response(message: str) -> MagicMock:
    """Build one 403 carrying GitHub's own message, with no rate-limit signal in it."""
    response = MagicMock(status_code=403, headers={}, text="", url="https://api.github.com/example")
    response.raise_for_status.side_effect = HTTPError(response=response)
    response.json.return_value = {"message": message}
    return response


@pytest.mark.parametrize(
    "message",
    [
        "Code Security must be enabled for this repository to use code scanning.",
        "Dependabot alerts are disabled for this repository.",
        "Advanced Security is disabled for this repository.",
        "Secret scanning is not enabled for this repository.",
        "Upgrade to GitHub Pro or make this repository public to enable this feature.",
    ],
)
def test_a_403_that_says_the_feature_is_off_is_not_a_refusal(message: str) -> None:
    """Read GitHub's "the feature is off" 403 as a disabled feature, not as a permission denial.

    THE BUG THIS EXISTS FOR. Across 1850 repositories every single 403 was one of these messages —
    830 code scanning, 113 Dependabot, 2 plan-limited — and not one was a genuine refusal. Grading
    them all as refusals exited the run 3 and buried any real permission problem among 945 that were
    not one.
    """
    session = Session()
    client = GitHubClient("secret", session, pause=MagicMock())
    with (
        patch.object(session, "get", return_value=refused_response(message)),
        pytest.raises(GitHubError) as captured,
    ):
        client.get("https://api.github.com/example")

    assert captured.value.reason is AvailabilityReason.FEATURE_DISABLED
    assert captured.value.status == 403


@pytest.mark.parametrize(
    "message",
    [
        "Resource not accessible by personal access token",
        (
            "Although you appear to have the correct authorization credentials, the `hmcts` organization "
            "has an IP allow list enabled, and 10.0.0.1 is not permitted to access this resource."
        ),
        "Sorry, this feature is temporarily unavailable.",
    ],
)
def test_a_403_this_client_does_not_recognise_stays_a_refusal(message: str) -> None:
    """Fail towards the refusal: an unlisted message is reported, never quietly excused.

    The phrases are anchored on `for this repository` so an organisation-level refusal can never
    match one, and a disabled message nobody listed is graded as a refusal — which a human reads in
    the log and adds. The reverse direction would hide a real refusal behind a phrase we guessed at.
    """
    session = Session()
    client = GitHubClient("secret", session, pause=MagicMock())
    with (
        patch.object(session, "get", return_value=refused_response(message)),
        pytest.raises(GitHubError, match="GitHub permission denied") as captured,
    ):
        client.get("https://api.github.com/example")

    assert captured.value.reason is AvailabilityReason.PERMISSION_DENIED


def test_a_successful_call_logs_one_debug_line(caplog: pytest.LogCaptureFixture) -> None:
    """Emit one line per call, naming what was asked for and how much came back."""
    session = Session()
    response = MagicMock(status_code=200, headers={}, content=b'[{"number": 1}]')
    client = GitHubClient("secret", session)
    with caplog.at_level(logging.DEBUG), patch.object(session, "get", return_value=response):
        client.get("https://api.github.com/repos/hmcts/cath-service/rulesets/17187159")

    assert [(record.levelno, record.getMessage()) for record in caplog.records] == [
        (
            logging.DEBUG,
            "GitHub ok 200 GET https://api.github.com/repos/hmcts/cath-service/rulesets/17187159 (15 bytes)",
        ),
    ]


def test_a_disabled_feature_logs_one_debug_line_beside_the_calls_that_worked(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Keep a disabled feature out of the warnings, so a 403 at INFO is always a real access problem."""
    session = Session()
    disabled = refused_response("Code Security must be enabled for this repository to use code scanning.")
    client = GitHubClient("secret", session, pause=MagicMock())
    with (
        caplog.at_level(logging.DEBUG),
        patch.object(session, "get", return_value=disabled),
        pytest.raises(GitHubError),
    ):
        client.get("https://api.github.com/repos/hmcts/jaia-client/code-scanning/alerts", {"state": "open"})

    assert [(record.levelno, record.getMessage()) for record in caplog.records] == [
        (
            logging.DEBUG,
            (
                "GitHub disabled 403 GET https://api.github.com/repos/hmcts/jaia-client/code-scanning/alerts"
                "?state=open: Code Security must be enabled for this repository to use code scanning."
            ),
        ),
    ]


def test_a_refused_call_logs_one_warning_line_and_no_other(caplog: pytest.LogCaptureFixture) -> None:
    """Report a genuine refusal once, at the level a run is read at.

    Three of the four lines a failed call used to emit were ours — a pre-request line, a response
    line and this warning — which is what made a 1850-repository log unreadable.
    """
    session = Session()
    refused = refused_response("Resource not accessible by personal access token")
    client = GitHubClient("secret", session, pause=MagicMock())
    with (
        caplog.at_level(logging.DEBUG),
        patch.object(session, "get", return_value=refused),
        pytest.raises(GitHubError),
    ):
        client.get("https://api.github.com/repos/hmcts/x/secret-scanning/alerts")

    assert [(record.levelno, record.getMessage()) for record in caplog.records] == [
        (
            logging.WARNING,
            (
                "GitHub refused 403 GET https://api.github.com/repos/hmcts/x/secret-scanning/alerts: "
                "Resource not accessible by personal access token"
            ),
        ),
    ]


def test_a_graphql_call_names_the_repository_its_variables_carry(caplog: pytest.LogCaptureFixture) -> None:
    """Log the variables of a GraphQL call: every one is a POST to the same URL, so nothing else names it."""
    session = Session()
    response = MagicMock(status_code=200, headers={}, content=b"{}")
    response.json.return_value = {"data": {"repository": {"name": "cath-service"}}}
    client = GitHubClient("secret", session)
    with caplog.at_level(logging.DEBUG), patch.object(session, "post", return_value=response):
        client.graphql("query { viewer { login } }", {"organization": "hmcts", "repository": "cath-service"})

    assert [(record.levelno, record.getMessage()) for record in caplog.records] == [
        (
            logging.DEBUG,
            (
                'GitHub ok 200 POST https://api.github.com/graphql {"organization": "hmcts", '
                '"repository": "cath-service"} (2 bytes)'
            ),
        ),
    ]


def test_a_retried_call_logs_its_retry_warning_rather_than_an_outcome_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Log one line per attempt: a retried response is reported by the retry warning, not twice."""
    session = Session()
    unavailable = MagicMock(status_code=503, headers={}, text="", content=b"")
    success = MagicMock(status_code=200, headers={}, content=b"{}")
    client = GitHubClient("secret", session, pause=MagicMock())
    with caplog.at_level(logging.DEBUG), patch.object(session, "get", side_effect=[unavailable, success]):
        client.get("https://api.github.com/example")

    levels = [record.levelno for record in caplog.records]
    assert levels == [logging.WARNING, logging.DEBUG]
    assert "GitHub returned HTTP 503" in caplog.records[0].getMessage()
    assert caplog.records[1].getMessage() == "GitHub ok 200 GET https://api.github.com/example (2 bytes)"


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


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        (
            "https://api.github.com/repos/hmcts/nfdiv-case-api",
            "https://api.github.com/repos/{organization}/{repository}",
        ),
        (
            "https://api.github.com/orgs/hmcts/rulesets/17187159",
            "https://api.github.com/orgs/{organization}/rulesets/{id}",
        ),
        (
            "https://api.github.com/repos/hmcts/cath-service/branches/master/protection",
            "https://api.github.com/repos/{organization}/{repository}/branches/{branch}/protection",
        ),
        (
            "https://api.github.com/repos/hmcts/cath-service/commits/3fa85f64571b4ee2a1c9d3f8e7b6a2c1d0e9f8a7",
            "https://api.github.com/repos/{organization}/{repository}/commits/{sha}",
        ),
        (
            "https://api.github.com/repos/hmcts/jaia-client/code-scanning/alerts?state=open&per_page=100&page=3",
            (
                "https://api.github.com/repos/{organization}/{repository}/code-scanning/alerts"
                "?state=open&per_page=100&page={page}"
            ),
        ),
        (
            "https://api.github.com/repos/hmcts/jaia-client/issues?after=Y3Vyc29yOjE%3D&per_page=100",
            "https://api.github.com/repos/{organization}/{repository}/issues?after={after}&per_page=100",
        ),
        ("https://api.github.com/graphql", "https://api.github.com/graphql"),
        ("https://api.github.com/rate_limit", "https://api.github.com/rate_limit"),
    ],
)
def test_an_endpoint_is_templated_by_the_values_its_url_carries(target: str, expected: str) -> None:
    """Replace the organisation, repository, id, branch, commit and page a URL names, and nothing else.

    Without this, one endpoint read once per repository is 1850 endpoints in a summary, and the page
    a paginated read stopped on splits it further still. A commit SHA fragments it just as badly and
    is never repeated: the Sonar mapping confirms a declared key against one, once per repository.
    """
    assert endpoint_template(target) == expected


def test_two_repositories_reading_one_endpoint_are_counted_as_one() -> None:
    """Count what GitHub was asked for rather than who it was asked about.

    The point of the summary: 830 repositories with code scanning switched off is one line, and the
    one call that was genuinely refused is another — at the same status, told apart by the outcome.
    """
    session = Session()
    repository = MagicMock(status_code=200, headers={}, content=b"{}")
    disabled = refused_response("Dependabot alerts are disabled for this repository.")
    refused = refused_response("Resource not accessible by personal access token")
    client = GitHubClient("secret", session, pause=MagicMock())

    with patch.object(session, "get", side_effect=[repository, repository, disabled, refused]):
        client.get_repository("hmcts", "cath-service")
        client.get_repository("hmcts", "nfdiv-case-api")
        for name in ("cath-service", "nfdiv-case-api"):
            with pytest.raises(GitHubError):
                client.get(f"https://api.github.com/repos/hmcts/{name}/dependabot/alerts", {"state": "open"})

    alerts = "https://api.github.com/repos/{organization}/{repository}/dependabot/alerts?state=open"
    assert client.call_outcomes == {
        (200, "ok", "GET", "https://api.github.com/repos/{organization}/{repository}"): 2,
        (403, "disabled", "GET", alerts): 1,
        (403, "refused", "GET", alerts): 1,
    }


def test_a_graphql_call_is_counted_without_the_variables_that_name_its_repository() -> None:
    """Collapse GraphQL to its one URL: the variables are in the log line, not in the counted endpoint."""
    session = Session()
    response = MagicMock(status_code=200, headers={}, content=b"{}")
    response.json.return_value = {"data": {"repository": {"name": "cath-service"}}}
    client = GitHubClient("secret", session)

    with patch.object(session, "post", return_value=response):
        client.graphql("query { repository { name } }", {"organization": "hmcts", "repository": "cath-service"})
        client.graphql("query { repository { name } }", {"organization": "hmcts", "repository": "nfdiv-case-api"})

    assert client.call_outcomes == {(200, "ok", "POST", "https://api.github.com/graphql"): 2}


def test_the_counted_outcomes_cannot_be_rewritten_by_a_reader() -> None:
    """Hand out a copy: a caller that could decrement this would be rewriting the record of a run.

    The mapping handed back is emptied itself, cast past its read-only type the way a careless
    caller reaches past it. Copying it first and emptying the copy would pass whatever the property
    returned, which is the one thing this test exists to tell apart.
    """
    session = Session()
    response = MagicMock(status_code=200, headers={}, content=b"{}")
    client = GitHubClient("secret", session)

    with patch.object(session, "get", return_value=response):
        client.get_repository("hmcts", "cath-service")
    cast("dict[CallOutcome, int]", client.call_outcomes).clear()

    assert sum(client.call_outcomes.values()) == 1
