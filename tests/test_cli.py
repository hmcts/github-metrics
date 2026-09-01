"""Test the command-line interface."""

import json
import logging
from collections.abc import Callable
from contextlib import AbstractContextManager, closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from sqlite3 import connect
from unittest.mock import ANY, MagicMock, patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from requests import ConnectionError as RequestsConnectionError
from requests import Response

from metrics.cli import INCOMPLETE_RUN, log_call_summary, main
from metrics.config import load_configuration
from metrics.credentials import CredentialsError
from metrics.domain import (
    AlertSeverity,
    CodeownersEvidence,
    CodeownersFile,
    CohortSummary,
    CollectionStatus,
    DirectCommitFact,
    EvidenceUnavailable,
    MaintenanceEvidence,
    MergeGateEvidence,
    MergeGateReport,
    OpenAlertCount,
    OpenPullRequestReport,
    OpenPullRequestSnapshot,
    OpenPullRequestSummary,
    PullRequestFact,
    PullRequestRule,
    ReportingWindow,
    RepositoryInventory,
    RepositoryInventoryItem,
    RepositoryMetadata,
    ReviewFact,
    ReviewState,
    SecurityAlertEvidence,
    SonarProjectMapping,
    SonarResolution,
    StoredSonarMapping,
    WindowProvenance,
)
from metrics.evidence import RepositoryEvidence
from metrics.storage import (
    StorageError,
    load_sonar_mapping,
    observation_database,
    record_repository_state,
    record_sonar_mapping,
)


@pytest.fixture
def configuration_path(tmp_path: Path) -> Path:
    """Create a minimal valid configuration."""
    path = tmp_path / "metrics.yaml"
    path.write_text(
        """\
version: 1
organization: hmcts
database: metrics.sqlite3
teams:
  - identifier: divorce
    display_name: Divorce
    repositories:
      - nfdiv-case-api
""",
        encoding="utf-8",
    )
    return path


def open_pull_request_payload(
    opened_in_window: int = 0,
    closed_without_merge: int = 0,
    currently_open: int = 0,
    stale_open: int = 0,
) -> dict[str, object]:
    """Build one bundled open-pull-request payload, all four counts zero by default."""
    return {
        "openedInWindow": {"issueCount": opened_in_window},
        "closedWithoutMerge": {"issueCount": closed_without_merge},
        "currentlyOpen": {"issueCount": currently_open},
        "staleOpen": {"issueCount": stale_open},
    }


def graphql_responses(
    search: dict[str, object],
    history: dict[str, object],
    checks: dict[str, object],
    standards: dict[str, object] | None = None,
    open_pull_requests: dict[str, object] | None = None,
) -> Callable[..., MagicMock]:
    """Answer each GraphQL request with the payload belonging to the document it sent."""

    def respond(url: str, *, json: dict[str, object], headers: dict[str, str], timeout: int) -> MagicMock:
        """Return the response for one posted GraphQL document."""
        _ = url, headers, timeout
        query = str(json["query"])
        if "githubCodeowners" in query:
            # The bundled repository-standards query: an empty branch and no CODEOWNERS by default.
            payload = standards if standards is not None else {"repository": {"defaultBranchRef": None}}
        elif "openedInWindow" in query:
            # The bundled open-pull-request query, which `collect` now stores the answer to.
            payload = open_pull_requests if open_pull_requests is not None else open_pull_request_payload()
        elif "defaultBranchRef" in query:
            payload = history
        elif "object(oid:" in query:
            payload = checks
        else:
            payload = search
        response = MagicMock(status_code=200, headers={})
        response.json.return_value = {"data": payload}
        return response

    return respond


@pytest.fixture(scope="session")
def app_private_key(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Write a throwaway RSA private key where `GH_APP_PRIVATE_KEY_PATH` expects to find one.

    A real key, so the assertion the exchange carries is really signed: a stub would pass every test
    here and leave the one failure that matters — a process that cannot sign at all — undetected.
    Generated once for the session, because an RSA keygen per test is the slowest thing in the suite.
    """
    generated = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path = tmp_path_factory.mktemp("app") / "app.pem"
    path.write_bytes(
        generated.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ),
    )
    return path


def app_environment(key: Path) -> dict[str, str]:
    """Configure App auth and nothing else, so no assertion can be satisfied by a fallback token."""
    return {"GH_APP_ID": "1234", "GH_APP_INSTALLATION_ID": "5678", "GH_APP_PRIVATE_KEY_PATH": str(key)}


def installation_token(payload: object, status_code: int = 201) -> MagicMock:
    """Build one stubbed answer to the installation token exchange."""
    response = MagicMock(status_code=status_code, ok=status_code < 400, text="")
    response.json.return_value = payload
    return response


MINTED = {"token": "ghs_minted", "expires_at": "2126-01-01T00:00:00Z"}
"""One minted installation token, expiring long after any machine that runs these tests will.

The expiry is a century out rather than an hour, because the credential reads the real clock here:
an instant in the past would put every read inside the renewal margin and mint a second token.
"""

EXCHANGE_URL = "https://api.github.com/app/installations/5678/access_tokens"


def test_doctor_succeeds(configuration_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Validate configuration and accessible repositories."""
    caplog.set_level(logging.INFO)
    with (
        patch("sys.argv", ["metrics", "doctor", "--config", str(configuration_path)]),
        patch.dict("os.environ", {"GH_TOKEN": "secret"}, clear=True),
        patch("metrics.cli.Session") as session_class,
    ):
        session_class.return_value.__enter__.return_value.get.return_value.status_code = 200
        assert main() == 0

    session = session_class.return_value.__enter__.return_value
    session.get.assert_called_once_with(
        "https://api.github.com/repos/hmcts/nfdiv-case-api",
        headers={"Authorization": "Bearer secret"},
        timeout=30,
    )
    # The mode, on every run that contacts GitHub: what a token may read is not what an installation
    # may read, so a report of refusals is one line away from its explanation.
    assert "Authenticating to GitHub as a personal access token" in caplog.text
    # No call minted anything: a PAT is already the token, so proving it costs nothing.
    session.post.assert_not_called()


def test_doctor_authenticates_as_a_github_app(
    configuration_path: Path,
    app_private_key: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Mint an installation token before asking about a repository, and send the token that arrives."""
    caplog.set_level(logging.INFO)
    with (
        patch("sys.argv", ["metrics", "doctor", "--config", str(configuration_path)]),
        patch.dict("os.environ", app_environment(app_private_key), clear=True),
        patch("metrics.cli.Session") as session_class,
    ):
        session = session_class.return_value.__enter__.return_value
        session.post.return_value = installation_token(MINTED)
        session.get.return_value.status_code = 200

        assert main() == 0

    assert "Authenticating to GitHub as GitHub App 1234, installation 5678" in caplog.text
    assert session.post.call_args.args == (EXCHANGE_URL,)
    session.get.assert_called_once_with(
        "https://api.github.com/repos/hmcts/nfdiv-case-api",
        headers={"Authorization": "Bearer ghs_minted"},
        timeout=30,
    )
    # No part of the credential reaches the log: neither the token GitHub issued, nor the assertion
    # that bought it, nor the key that signed it.
    assert "ghs_minted" not in caplog.text


def test_doctor_requires_credentials(configuration_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Name both ways of authenticating, and ask about no repository, when neither is configured."""
    with (
        patch("sys.argv", ["metrics", "doctor", "--config", str(configuration_path)]),
        patch.dict("os.environ", {}, clear=True),
        patch("metrics.cli.Session") as session_class,
    ):
        assert main() == 1

    session = session_class.return_value.__enter__.return_value
    session.get.assert_not_called()
    session.post.assert_not_called()
    assert "GH_APP_ID" in caplog.text
    assert "GH_TOKEN" in caplog.text


def test_doctor_reports_a_refused_installation_token(
    configuration_path: Path,
    app_private_key: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Stop the run at the exchange, carrying GitHub's own account of why the App cannot be used."""
    with (
        patch("sys.argv", ["metrics", "doctor", "--config", str(configuration_path)]),
        patch.dict("os.environ", app_environment(app_private_key), clear=True),
        patch("metrics.cli.Session") as session_class,
    ):
        session = session_class.return_value.__enter__.return_value
        session.post.return_value = installation_token({"message": "Integration not found"}, status_code=404)

        assert main() == 1

    assert "Cannot authenticate to GitHub" in caplog.text
    assert "Integration not found" in caplog.text
    # Nothing was asked about a repository: an inaccessible repository and an unusable credential are
    # different problems, and reporting fourteen of the first for one of the second wastes a morning.
    session.get.assert_not_called()


def test_a_credential_that_fails_after_the_run_started_ends_it_with_one_line(
    configuration_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Answer a renewal that fails mid-run exactly as a failure to mint at startup is answered.

    Startup is not the only place this happens. An installation token is replaced inside the
    five-minute margin partway through any run long enough to outlive one, and again if a 401 gets
    past that — both deep inside a request, where every handler between there and here grades ONE
    repository's evidence and is looking for a `GitHubError`. Without a handler of its own the
    renewal a bad minute at GitHub defeats ends the run in a traceback instead of in the line that
    says what to fix.
    """
    credentials = MagicMock()
    credentials.token.side_effect = ["ghs_minted", CredentialsError("the token exchange failed")]
    with (
        patch("sys.argv", ["metrics", "doctor", "--config", str(configuration_path)]),
        patch.dict("os.environ", {}, clear=True),
        patch("metrics.cli.Session"),
        patch("metrics.cli.resolve_credentials", return_value=credentials),
    ):
        assert main() == 1

    assert "Cannot authenticate to GitHub: the token exchange failed" in caplog.text


def test_doctor_fails_for_inaccessible_repository(configuration_path: Path) -> None:
    """Return a failure when GitHub access cannot be verified."""
    with (
        patch("sys.argv", ["metrics", "doctor", "--config", str(configuration_path)]),
        patch.dict("os.environ", {"GH_TOKEN": "secret"}, clear=True),
        patch("metrics.cli.Session") as session_class,
    ):
        session_class.return_value.__enter__.return_value.get.side_effect = RequestsConnectionError("offline")

        assert main() == 1


def alert_responses() -> list[MagicMock]:
    """Build the three empty security-alert pages one repository collection now also requests.

    One per family: they are separate endpoints behind separate permissions, so collection asks all
    three every time rather than inferring one family's state from another's.
    """
    responses = []
    for _ in range(3):
        response = MagicMock(status_code=200, links={})
        response.json.return_value = []
        responses.append(response)
    return responses


def test_collect_requires_credentials(configuration_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Refuse a collection that cannot authenticate, before it asks GitHub about anything."""
    with (
        patch("sys.argv", ["metrics", "collect", "--config", str(configuration_path)]),
        patch.dict("os.environ", {}, clear=True),
        patch("metrics.cli.Session") as session_class,
    ):
        assert main() == 1

    session = session_class.return_value.__enter__.return_value
    session.get.assert_not_called()
    session.post.assert_not_called()
    assert "Cannot authenticate to GitHub" in caplog.text
    # No offline advice here: `collect` has nothing to do without GitHub, and offering a flag that
    # does not exist on this command is worse than offering nothing.
    assert "--offline" not in caplog.text


def test_collect_reports_current_state_and_what_the_window_fetched(
    configuration_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Report current repository state and collection provenance, never a metric value."""
    with (
        patch("sys.argv", ["metrics", "collect", "--config", str(configuration_path)]),
        patch.dict("os.environ", {"GH_TOKEN": "secret"}, clear=True),
        patch("metrics.cli.Session") as session_class,
    ):
        session = session_class.return_value.__enter__.return_value
        repository_response = MagicMock(status_code=200)
        repository_response.json.return_value = {
            "name": "nfdiv-case-api",
            "default_branch": "master",
            "archived": False,
            "fork": False,
            "disabled": False,
            "created_at": "2020-01-01T00:00:00Z",
            "updated_at": "2026-08-01T12:00:00Z",
            "pushed_at": "2026-08-01T11:00:00Z",
        }
        rules_response = MagicMock(status_code=200, links={})
        rules_response.json.return_value = []
        protection_response = Response()
        protection_response.status_code = 404
        session.get.side_effect = [repository_response, rules_response, protection_response, *alert_responses()]
        session.post.side_effect = graphql_responses(
            {
                "search": {
                    "issueCount": 0,
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [],
                },
            },
            {
                "repository": {
                    "defaultBranchRef": {
                        "target": {
                            "history": {
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                                "nodes": [
                                    {
                                        "oid": "aaa",
                                        "committedDate": "2026-07-01T00:00:00Z",
                                        "additions": 4,
                                        "deletions": 1,
                                        "changedFilesIfAvailable": 1,
                                        "author": {"user": {"login": "pusher", "__typename": "User"}},
                                        "associatedPullRequests": {"nodes": []},
                                    },
                                ],
                            },
                        },
                    },
                },
            },
            {"repository": {"object": {"statusCheckRollup": None}}},
        )

        assert main() == 0

    output = json.loads(capsys.readouterr().out)
    assert output["organization"] == "hmcts"
    assert output["status"] == "complete"
    assert output["repositories"][0]["repository"]["default_branch"] == "master"
    assert output["repositories"][0]["merge_gate"]["branch"] == "master"
    assert not output["repositories"][0]["merge_gate"]["protected"]
    assert output["repositories"][0]["collection"]["stable_history"] == "fetched"
    assert output["repositories"][0]["collection"]["stable_intervals_fetched"] == 1
    assert output["repositories"][0]["collection"]["pull_request_facts_loaded"] == 0
    assert output["repositories"][0]["collection"]["direct_commit_facts_loaded"] == 1
    assert output["repositories"][0]["collection"]["direct_commit_intervals_fetched"] == 1
    # Current state now includes the open pull requests, carrying the window the two windowed counts
    # of the four were measured over.
    assert output["repositories"][0]["open_pull_requests"] == {
        "starts_at": output["starts_at"],
        "ends_at": output["ends_at"],
        "summary": {"opened_in_window": 0, "closed_without_merge": 0, "currently_open": 0, "stale_open": 0},
    }
    # One repository's whole run, both phases: eight current-state calls — the repository, the
    # bundled standards query, its rules, its protection, one page per alert family and the bundled
    # open-pull-request query — and four GraphQL queries for the window — a pull-request search over
    # the stable interval and another over the mutable edge, the commit history, and one check
    # rollup for the single direct commit it returned.
    assert output["costs"] == [
        {"repository": "nfdiv-case-api", "requests": 12, "elapsed_seconds": ANY},
    ]
    assert "observations" not in json.dumps(output)
    assert datetime.fromisoformat(output["ends_at"]) - datetime.fromisoformat(output["starts_at"]) == timedelta(
        days=90,
    )
    assert output["failures"] == []
    with closing(connect(configuration_path.with_name("metrics.sqlite3"))) as connection:
        assert connection.execute("SELECT organization, repository FROM repository_state").fetchone() == (
            "hmcts",
            "nfdiv-case-api",
        )
        # The observations live in their own file, so "delete the cache and refetch" stays safe.
        assert connection.execute("SELECT name FROM sqlite_master WHERE name = 'alert_observations'").fetchall() == []
    with closing(connect(configuration_path.with_name("metrics-observations.sqlite3"))) as connection:
        assert connection.execute(
            "SELECT repository, family FROM alert_observations ORDER BY family",
        ).fetchall() == [
            ("nfdiv-case-api", "code-scanning"),
            ("nfdiv-case-api", "dependabot"),
            ("nfdiv-case-api", "secret-scanning"),
        ]


def test_main_layers_repeated_configuration_options(configuration_path: Path) -> None:
    """Load every `--config` in the order given, so one policy file can serve several team files."""
    teams_path = configuration_path.with_name("teams.yaml")
    teams_path.write_text(
        """\
teams:
  - identifier: opal
    display_name: Opal
    repositories:
      - opal-common-lib
""",
        encoding="utf-8",
    )
    with (
        patch("sys.argv", ["metrics", "doctor", "--config", str(configuration_path), "--config", str(teams_path)]),
        patch.dict("os.environ", {"GH_TOKEN": "secret"}, clear=True),
        patch("metrics.cli.Session") as session_class,
    ):
        session_class.return_value.__enter__.return_value.get.return_value.status_code = 200
        assert main() == 0

    session = session_class.return_value.__enter__.return_value
    session.get.assert_called_once_with(
        "https://api.github.com/repos/hmcts/opal-common-lib",
        headers={"Authorization": "Bearer secret"},
        timeout=30,
    )


@pytest.mark.parametrize("command", ["collect", "doctor", "evidence", "trend"])
def test_main_requires_a_cohort_for_the_commands_that_report_one(
    configuration_path: Path,
    caplog: pytest.LogCaptureFixture,
    command: str,
) -> None:
    """Name the command and how to fix it when a policy file is loaded without its team file."""
    content = configuration_path.read_text(encoding="utf-8")
    configuration_path.write_text(content[: content.index("teams:")], encoding="utf-8")
    with (
        patch("sys.argv", ["metrics", command, "--config", str(configuration_path)]),
        patch.dict("os.environ", {"GH_TOKEN": "secret"}, clear=True),
        patch("metrics.cli.Session") as session_class,
    ):
        assert main() == 1

    session_class.assert_not_called()
    assert f"{command} reports the configured repositories, but no team is configured" in caplog.text


def test_main_rejects_invalid_configuration(tmp_path: Path) -> None:
    """Return a failure before opening a session for invalid configuration."""
    path = tmp_path / "missing.yaml"
    with (
        patch("sys.argv", ["metrics", "doctor", "--config", str(path)]),
        patch.dict("os.environ", {"GH_TOKEN": "secret"}, clear=True),
        patch("metrics.cli.Session") as session_class,
    ):
        assert main() == 1

    session_class.assert_not_called()


def test_collect_reports_storage_failure(configuration_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Return a failure without emitting unstored inventory."""
    with (
        patch("sys.argv", ["metrics", "collect", "--config", str(configuration_path)]),
        patch.dict("os.environ", {"GH_TOKEN": "secret"}, clear=True),
        patch("metrics.cli.Session") as session_class,
        patch("metrics.cli.record_repository_state", side_effect=StorageError("disk full")),
    ):
        repository_response = MagicMock(status_code=200)
        repository_response.json.return_value = {
            "name": "nfdiv-case-api",
            "default_branch": "master",
            "archived": False,
            "fork": False,
            "disabled": False,
            "created_at": "2020-01-01T00:00:00Z",
            "updated_at": "2026-08-01T12:00:00Z",
            "pushed_at": None,
        }
        rules_response = MagicMock(status_code=200, links={})
        rules_response.json.return_value = []
        protection_response = Response()
        protection_response.status_code = 404
        session_class.return_value.__enter__.return_value.get.side_effect = [
            repository_response,
            rules_response,
            protection_response,
            *alert_responses(),
        ]
        behaviour_response = MagicMock(status_code=200, headers={})
        behaviour_response.json.return_value = {
            "data": {
                "search": {
                    "issueCount": 0,
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [],
                },
            },
        }
        session_class.return_value.__enter__.return_value.post.return_value = behaviour_response

        assert main() == 1

    assert capsys.readouterr().out == ""


def test_collect_rejects_an_unusable_collection_window(
    configuration_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Refuse an impossible collection window before contacting GitHub."""
    with (
        patch("sys.argv", ["metrics", "collect", "--config", str(configuration_path), "--days", "800"]),
        patch.dict("os.environ", {"GH_TOKEN": "secret"}, clear=True),
        patch("metrics.cli.Session") as session_class,
    ):
        assert main() == 1

    assert "exceeds the 365-day maximum" in caplog.text
    session_class.assert_not_called()


def population_configuration_path(tmp_path: Path) -> Path:
    """Configure three repositories across two teams, so one can fail while two do not."""
    path = tmp_path / "metrics.yaml"
    path.write_text(
        """\
version: 1
organization: hmcts
database: metrics.sqlite3
teams:
  - identifier: divorce
    display_name: Divorce
    repositories:
      - nfdiv-case-api
  - identifier: opal
    display_name: Opal
    repositories:
      - opal-common-lib
      - opal-logging-service
""",
        encoding="utf-8",
    )
    return path


def collectable_repository_responses(name: str) -> list[object]:
    """Build every current-state response one unprotected repository's collection asks for."""
    metadata = MagicMock(status_code=200)
    metadata.json.return_value = {
        "name": name,
        "default_branch": "master",
        "archived": False,
        "fork": False,
        "disabled": False,
        "created_at": "2020-01-01T00:00:00Z",
        "updated_at": "2026-08-01T12:00:00Z",
        "pushed_at": "2026-08-01T11:00:00Z",
    }
    rules = MagicMock(status_code=200, links={})
    rules.json.return_value = []
    protection = Response()
    protection.status_code = 404
    return [metadata, rules, protection, *alert_responses()]


def inaccessible_repository_response() -> Response:
    """Build the response GitHub gives for a repository the token cannot see at all."""
    response = Response()
    response.status_code = 404
    return response


def empty_window_responses(open_pull_requests: dict[str, object] | None = None) -> Callable[..., MagicMock]:
    """Answer every windowed query with an empty result, whichever repository asked."""
    return graphql_responses(
        {"search": {"issueCount": 0, "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []}},
        {
            "repository": {
                "defaultBranchRef": {
                    "target": {"history": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []}},
                },
            },
        },
        {"repository": {"object": {"statusCheckRollup": None}}},
        open_pull_requests=open_pull_requests,
    )


def test_collect_measures_open_pull_requests_against_the_run_s_window_and_instant(
    configuration_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Thread the run's window and instant from the command through to the stored counts.

    The two windowed counts are bounded by the same window the merged history is collected over, and
    stored beside it. The stale cutoff is measured back from the instant the run started, NOT from
    the end of that window: a run reporting an old window would otherwise report which pull requests
    were stale then, under a heading that reads as now.
    """
    posted: list[dict[str, object]] = []
    responses = empty_window_responses(
        open_pull_request_payload(opened_in_window=9, closed_without_merge=2, currently_open=4, stale_open=1),
    )

    def record(url: str, *, json: dict[str, object], headers: dict[str, str], timeout: int) -> MagicMock:
        """Answer as GitHub's GraphQL endpoint would, keeping every document that was posted."""
        posted.append(json)
        return responses(url, json=json, headers=headers, timeout=timeout)

    with (
        patch(
            "sys.argv",
            [
                "metrics",
                "collect",
                "--config",
                str(configuration_path),
                "--from",
                "2026-05-01T00:00:00Z",
                "--to",
                "2026-06-01T00:00:00Z",
            ],
        ),
        patch.dict("os.environ", {"GH_TOKEN": "secret"}, clear=True),
        patch("metrics.cli.Session") as session_class,
    ):
        session = session_class.return_value.__enter__.return_value
        session.get.side_effect = collectable_repository_responses("nfdiv-case-api")
        session.post.side_effect = record

        assert main() == 0

    output = json.loads(capsys.readouterr().out)
    snapshot = output["repositories"][0]["open_pull_requests"]
    assert (snapshot["starts_at"], snapshot["ends_at"]) == (output["starts_at"], output["ends_at"])
    assert snapshot["summary"] == {
        "opened_in_window": 9,
        "closed_without_merge": 2,
        "currently_open": 4,
        "stale_open": 1,
    }
    variables = [document["variables"] for document in posted if "openedInWindow" in str(document["query"])]
    assert len(variables) == 1
    queries = variables[0]
    assert isinstance(queries, dict)
    # The requested window, to the second, and the stale cutoff a fortnight back from the run itself
    # — which the requested window ended three months before.
    assert queries["openedQuery"].endswith("created:2026-05-01T00:00:00Z..2026-05-31T23:59:59Z")
    stale_cutoff = datetime.fromisoformat(str(queries["staleOpenQuery"]).split("updated:<")[1])
    assert stale_cutoff > datetime.fromisoformat(output["ends_at"])
    assert stale_cutoff <= datetime.now(UTC) - timedelta(days=14)


def test_collect_exits_incomplete_when_one_repository_of_three_could_not_be_read(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Distinguish a partial run from a complete one in the exit status, not only in the report.

    A partial run exiting zero is the failure mode this guards: at fourteen repositories every
    downstream denominator would read as covering the population when two of them are missing.
    """
    configuration_path = population_configuration_path(tmp_path)
    with (
        patch("sys.argv", ["metrics", "collect", "--config", str(configuration_path)]),
        patch.dict("os.environ", {"GH_TOKEN": "secret"}, clear=True),
        patch("metrics.cli.Session") as session_class,
    ):
        session = session_class.return_value.__enter__.return_value
        session.get.side_effect = [
            *collectable_repository_responses("nfdiv-case-api"),
            inaccessible_repository_response(),
            *collectable_repository_responses("opal-logging-service"),
        ]
        session.post.side_effect = empty_window_responses()

        assert main() == 3

    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "partial"
    # The repository between the two that answered failed, and neither of its neighbours went with it.
    assert [item["repository"]["name"] for item in output["repositories"]] == [
        "nfdiv-case-api",
        "opal-logging-service",
    ]
    assert [failure["repository"] for failure in output["failures"]] == ["opal-common-lib"]
    assert output["failures"][0]["reason"] == "not_found_or_inaccessible"
    # Every repository is costed, including the one that failed after a single refused call.
    assert {cost["repository"] for cost in output["costs"]} == {
        "nfdiv-case-api",
        "opal-common-lib",
        "opal-logging-service",
    }


def test_collect_exits_one_and_still_says_why_when_nothing_could_be_collected(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Separate nothing-collected from partly-collected, and report the reasons either way."""
    configuration_path = population_configuration_path(tmp_path)
    with (
        patch("sys.argv", ["metrics", "collect", "--config", str(configuration_path)]),
        patch.dict("os.environ", {"GH_TOKEN": "secret"}, clear=True),
        patch("metrics.cli.Session") as session_class,
    ):
        session = session_class.return_value.__enter__.return_value
        session.get.side_effect = [inaccessible_repository_response() for _ in range(3)]

        assert main() == 1
        session.post.assert_not_called()

    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "failed"
    assert output["repositories"] == []
    assert len(output["failures"]) == 3


def pair_configuration_path(tmp_path: Path) -> Path:
    """Configure two repositories, so one endpoint read for both can be seen counted as one line."""
    path = tmp_path / "metrics.yaml"
    path.write_text(
        """\
version: 1
organization: hmcts
database: metrics.sqlite3
teams:
  - identifier: opal
    display_name: Opal
    repositories:
      - opal-common-lib
      - opal-logging-service
""",
        encoding="utf-8",
    )
    return path


def alert_403(message: str) -> Response:
    """Build the 403 an alert endpoint answers with, carrying GitHub's own explanation of it."""
    response = Response()
    response.status_code = 403
    response._content = json.dumps({"message": message}).encode()  # noqa: SLF001
    return response


def empty_alert_page() -> MagicMock:
    """Build one alert family's answer of no open alerts at all."""
    response = MagicMock(status_code=200, links={})
    response.json.return_value = []
    return response


def partly_refused_repository_responses(name: str) -> list[object]:
    """Build one repository's current-state calls with a disabled family beside a refused one.

    The two 403s are the pair the summary exists to keep apart: GitHub answers the same status for a
    feature nobody turned on and for a token that may not look, and only its message tells them
    apart.
    """
    metadata, rules, protection, *_ = collectable_repository_responses(name)
    return [
        metadata,
        rules,
        protection,
        alert_403("Dependabot alerts are disabled for this repository."),
        empty_alert_page(),
        alert_403("Resource not accessible by personal access token"),
    ]


def call_summary(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Return every line of the one end-of-run summary the run logged."""
    summaries = [record.getMessage() for record in caplog.records if record.getMessage().startswith("GitHub calls")]
    assert len(summaries) == 1
    return summaries[0].splitlines()


def test_the_call_summary_pads_its_columns_and_ranks_refusals_above_the_rest(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Render the counted calls as columns a person reads down, worst first.

    Asserted as whole lines rather than split into fields, because the padding IS the feature: a
    summary whose counts and outcomes do not line up is read one row at a time instead of scanned.
    """
    caplog.set_level(logging.INFO)
    alerts = "https://api.github.com/repos/{organization}/{repository}/code-scanning/alerts"

    log_call_summary(
        {
            (200, "ok", "GET", "https://api.github.com/repos/{organization}/{repository}"): 1850,
            (403, "disabled", "GET", alerts): 830,
            (403, "refused", "GET", "https://api.github.com/repos/{organization}/{repository}/dependabot/alerts"): 12,
            (200, "ok", "POST", "https://api.github.com/graphql"): 1848,
        },
        1850,
    )

    assert caplog.records[0].getMessage().splitlines() == [
        "GitHub calls issued for 1850 repositories:",
        "    12  403  refused   GET https://api.github.com/repos/{organization}/{repository}/dependabot/alerts",
        f"   830  403  disabled  GET {alerts}",
        "  1850  200  ok        GET https://api.github.com/repos/{organization}/{repository}",
        "  1848  200  ok        POST https://api.github.com/graphql",
    ]


def test_the_call_summary_ranks_a_graphql_refusal_with_the_refusals_it_is_one_of(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Rank the three answers one 403 carries, and keep a bad gateway out of the refusals.

    A GraphQL refusal is counted at the 403 it is equivalent to, so a single status now holds a
    refusal, a GraphQL refusal and a feature nobody turned on — and count alone would bury the first
    two under the third. `failed` is its own word for the same reason: nobody refused a 502.
    """
    caplog.set_level(logging.INFO)
    graphql = "https://api.github.com/graphql"
    alerts = "https://api.github.com/repos/{organization}/{repository}/code-scanning/alerts"

    log_call_summary(
        {
            (200, "ok", "POST", graphql): 1977,
            (403, "errors", "POST", graphql): 181,
            (403, "disabled", "GET", alerts): 830,
            (403, "refused", "GET", alerts): 12,
            (502, "failed", "POST", graphql): 2,
        },
        1863,
    )

    assert caplog.records[0].getMessage().splitlines() == [
        "GitHub calls issued for 1863 repositories:",
        f"     2  502  failed    POST {graphql}",
        f"    12  403  refused   GET {alerts}",
        f"   181  403  errors    POST {graphql}",
        f"   830  403  disabled  GET {alerts}",
        f"  1977  200  ok        POST {graphql}",
    ]


def test_collect_summarises_every_call_by_status_outcome_and_endpoint(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Total a whole run's calls into one line per kind, worst first, whatever it spent them on.

    The log holds one DEBUG line per call, which at 1850 repositories nobody reads and nobody can
    total. This is the same record counted: two repositories reading one endpoint are one line, the
    refused call sits above the 830 features nobody turned on, and both sit above everything that
    worked.
    """
    caplog.set_level(logging.INFO)
    configuration_path = pair_configuration_path(tmp_path)
    with (
        patch("sys.argv", ["metrics", "collect", "--config", str(configuration_path)]),
        patch.dict("os.environ", {"GH_TOKEN": "secret"}, clear=True),
        patch("metrics.cli.Session") as session_class,
    ):
        session = session_class.return_value.__enter__.return_value
        session.get.side_effect = [
            *partly_refused_repository_responses("opal-common-lib"),
            *partly_refused_repository_responses("opal-logging-service"),
        ]
        session.post.side_effect = empty_window_responses()

        assert main() == INCOMPLETE_RUN

    lines = call_summary(caplog)
    assert lines[0] == "GitHub calls issued for 2 repositories:"
    repository = "https://api.github.com/repos/{organization}/{repository}"
    alerts = "?state=open&per_page=100"
    assert [line.split(maxsplit=3) for line in lines[1:]] == [
        ["2", "404", "refused", f"GET {repository}/branches/{{branch}}/protection"],
        ["2", "403", "refused", f"GET {repository}/secret-scanning/alerts{alerts}"],
        ["2", "403", "disabled", f"GET {repository}/dependabot/alerts{alerts}"],
        ["12", "200", "ok", "POST https://api.github.com/graphql"],
        ["2", "200", "ok", f"GET {repository}"],
        ["2", "200", "ok", f"GET {repository}/code-scanning/alerts{alerts}"],
        ["2", "200", "ok", f"GET {repository}/rules/branches/{{branch}}?per_page=100"],
    ]


def test_collect_summarises_the_calls_of_a_run_that_could_not_store_what_it_collected(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Report what a failed run spent: it writes no report, so this is the only account of its calls."""
    caplog.set_level(logging.INFO)
    configuration_path = pair_configuration_path(tmp_path)
    with (
        patch("sys.argv", ["metrics", "collect", "--config", str(configuration_path)]),
        patch.dict("os.environ", {"GH_TOKEN": "secret"}, clear=True),
        patch("metrics.cli.Session") as session_class,
        patch("metrics.cli.record_repository_state", side_effect=StorageError("disk full")),
    ):
        session = session_class.return_value.__enter__.return_value
        session.get.side_effect = [
            *collectable_repository_responses("opal-common-lib"),
            *collectable_repository_responses("opal-logging-service"),
        ]
        session.post.side_effect = empty_window_responses()

        assert main() == 1

    lines = call_summary(caplog)
    assert lines[0] == "GitHub calls issued for 2 repositories:"
    # The body, not just the header: a summary of a failed run that listed no calls would be no
    # account of them at all, and this run's report never reaches stdout to be read instead.
    counts = [int(line.split()[0]) for line in lines[1:]]
    assert len(counts) == 7
    assert sum(counts) == 24


def test_prune_reports_removed_intervals(configuration_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Delete cached intervals unused for the requested number of days."""
    caplog.set_level(logging.INFO)
    with (
        patch("sys.argv", ["metrics", "prune", "--config", str(configuration_path), "--days", "7"]),
        patch.dict("os.environ", {}, clear=True),
        patch("metrics.cli.prune_cache", return_value=3) as prune,
    ):
        assert main() == 0

    assert "Removed 3 unused cached intervals" in caplog.text
    prune.assert_called_once()


def test_prune_reports_storage_failure(configuration_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Return a readable failure when the cache cannot be pruned."""
    with (
        patch("sys.argv", ["metrics", "prune", "--config", str(configuration_path)]),
        patch.dict("os.environ", {}, clear=True),
        patch("metrics.cli.prune_cache", side_effect=StorageError("database locked")),
    ):
        assert main() == 1

    assert "Prune failed: database locked" in caplog.text


def sonar_answers(
    projects: list[dict[str, object]],
    analyses: dict[str, list[dict[str, object]]] | None = None,
    commits: dict[str, str] | None = None,
    refusing: frozenset[str] = frozenset(),
    exhausted: frozenset[str] = frozenset(),
) -> Callable[..., MagicMock]:
    """Answer the project listing, each project's analyses and each commit search from one responder.

    One responder for both APIs, because `map-sonar` reads SonarCloud and GitHub over two sessions of
    the same faked class and the URL says which of them a call belongs to. `refusing` names the
    projects whose analyses SonarCloud rejects, and `exhausted` the revisions whose commit search
    reports the per-minute limit spent.
    """
    analysed = analyses or {}
    found = commits or {}

    def respond(
        url: str,
        *,
        params: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
        timeout: int = 30,
    ) -> MagicMock:
        """Return the response belonging to one requested URL."""
        _ = headers, timeout
        parameters = params or {}
        if url.endswith("/api/components/search_projects"):
            payload: object = {"paging": {"total": len(projects)}, "components": projects}
        elif url.endswith("/api/project_analyses/search"):
            project = str(parameters["project"])
            if project in refusing:
                return MagicMock(status_code=401)
            payload = {"analyses": analysed.get(project, [])}
        elif url.endswith("/search/commits"):
            revision = str(parameters["q"]).rpartition("hash:")[2]
            if revision in exhausted:
                # A spent commit-search window: no reserve left and a reset instant already passed,
                # so the client's own retries cost nothing and still end in a rate-limit failure.
                return MagicMock(status_code=403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "0"})
            full_name = found.get(revision)
            payload = (
                {"total_count": 0, "items": []}
                if full_name is None
                else {"total_count": 1, "items": [{"sha": revision, "repository": {"full_name": full_name}}]}
            )
        else:
            message = f"unexpected request: {url}"
            raise AssertionError(message)
        response = MagicMock(status_code=200, headers={})
        response.json.return_value = payload
        return response

    return respond


def map_sonar_run(configuration_path: Path, respond: Callable[..., MagicMock]) -> tuple[int, MagicMock]:
    """Run `map-sonar` over one faked session, returning its exit status and the session it read."""
    with (
        patch("sys.argv", ["metrics", "map-sonar", "--config", str(configuration_path)]),
        patch.dict("os.environ", {"GH_TOKEN": "secret"}, clear=True),
        # The commit-search pacing is asserted in tests/test_sonar.py; a real pacer here would make
        # every one of these tests wait two seconds per search for nothing.
        patch("metrics.cli.search_pacer"),
        patch("metrics.cli.Session") as session_class,
    ):
        session = session_class.return_value.__enter__.return_value
        session.get.side_effect = respond
        status = main()
        # Two sessions, not one: the GitHub client puts its bearer token on the session it is given,
        # and a shared session would send a GitHub token to SonarCloud on every request.
        assert session_class.call_count == 2
    return status, session


def sonar_map(configuration_path: Path, project_key: str) -> StoredSonarMapping | None:
    """Read back what one `map-sonar` run stored for one project, from the observations file."""
    return load_sonar_mapping(observation_database(configuration_path.parent / "metrics.sqlite3"), "hmcts", project_key)


def searched_projects(session: MagicMock) -> list[str]:
    """List the projects whose analyses were read, which is what a resolution actually costs."""
    return [
        str(call.kwargs["params"]["project"])
        for call in session.get.call_args_list
        if call.args[0].endswith("/api/project_analyses/search")
    ]


def test_map_sonar_resolves_every_listed_project_and_stores_what_it_learned(
    configuration_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Resolve each project through its analysed commit, and store the answer even when there is none.

    The duplicate pair is the case the map exists for: SonarCloud has no rename, so a re-created
    project leaves its abandoned twin claiming the same repository, and both keys are reported so a
    human can see the reverse lookup had a choice to make.
    """
    caplog.set_level(logging.INFO)
    respond = sonar_answers(
        projects=[
            {"key": "hmcts.cath", "visibility": "public", "analysisDate": "2026-08-27T09:49:35+0000"},
            {"key": "rpx-xui-webapp_2", "visibility": "public", "analysisDate": "2026-08-26T11:00:00+0000"},
            {"key": "rpx-xui-webapp", "visibility": "public", "analysisDate": "2026-07-01T11:00:00+0000"},
            {"key": "hmcts.never-analysed", "visibility": "public"},
        ],
        analyses={
            "hmcts.cath": [{"date": "2026-08-27T09:49:35+0000", "revision": "ce34e614"}],
            "rpx-xui-webapp_2": [{"date": "2026-08-26T11:00:00+0000", "revision": "11ee0001"}],
            "rpx-xui-webapp": [{"date": "2026-07-01T11:00:00+0000", "revision": "aba7d011"}],
        },
        commits={
            "ce34e614": "hmcts/cath-service",
            "11ee0001": "hmcts/nfdiv-case-api",
            "aba7d011": "hmcts/nfdiv-case-api",
        },
    )

    status, session = map_sonar_run(configuration_path, respond)

    assert status == 0
    resolved = sonar_map(configuration_path, "hmcts.cath")
    assert resolved is not None
    assert resolved.mapping is not None
    assert resolved.mapping.repository == "cath-service"
    assert resolved.mapping.method is SonarResolution.ANALYSIS_REVISION
    assert resolved.mapping.revision == "ce34e614"
    assert resolved.mapping.analysis_at == datetime(2026, 8, 27, 9, 49, 35, tzinfo=UTC)
    assert resolved.detail is None
    # A never-analysed project is an ANSWER, stored with its reason so the next run spends nothing
    # on it: 14 of the live organisation's 289 projects are in exactly this state.
    never = sonar_map(configuration_path, "hmcts.never-analysed")
    assert never is not None
    assert never.mapping is None
    assert never.detail is not None
    assert "no analysis" in never.detail
    assert searched_projects(session) == [
        "hmcts.cath",
        "rpx-xui-webapp_2",
        "rpx-xui-webapp",
        "hmcts.never-analysed",
    ]
    assert "3 resolved, 0 unchanged, 1 never analysed, 0 unresolvable, 0 failed; 2 repositories mapped" in caplog.text
    assert "nfdiv-case-api is claimed by 2 projects: rpx-xui-webapp_2, rpx-xui-webapp" in caplog.text


def test_map_sonar_skips_a_project_nothing_has_been_analysed_for_since_it_was_resolved(
    configuration_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Spend no call on a project whose stored answer came from the analysis the listing still names.

    The whole reason the command is separate is the 30-per-minute commit search, so a second run over
    an organisation that has analysed one new project must cost one project's worth of calls.
    """
    caplog.set_level(logging.INFO)
    analysis_at = datetime(2026, 8, 27, 9, 49, 35, tzinfo=UTC)
    record_sonar_mapping(
        observation_database(configuration_path.parent / "metrics.sqlite3"),
        "hmcts",
        StoredSonarMapping(
            project_key="hmcts.cath",
            resolved_at=datetime(2026, 8, 27, 12, 0, tzinfo=UTC),
            mapping=SonarProjectMapping(
                project_key="hmcts.cath",
                repository="cath-service",
                method=SonarResolution.ANALYSIS_REVISION,
                analysis_at=analysis_at,
                revision="ce34e614",
            ),
        ),
    )
    respond = sonar_answers(
        projects=[
            {"key": "hmcts.cath", "visibility": "public", "analysisDate": "2026-08-27T09:49:35+0000"},
            {"key": "hmcts.newly-analysed", "visibility": "public", "analysisDate": "2026-08-28T09:00:00+0000"},
        ],
        analyses={"hmcts.newly-analysed": [{"date": "2026-08-28T09:00:00+0000", "revision": "f8e50011"}]},
        commits={"f8e50011": "hmcts/nfdiv-case-api"},
    )

    status, session = map_sonar_run(configuration_path, respond)

    assert status == 0
    assert searched_projects(session) == ["hmcts.newly-analysed"]
    assert "SKIP   hmcts.cath" in caplog.text
    assert "1 resolved, 1 unchanged" in caplog.text
    # The skipped project still counts towards what the map holds, and its stored row is untouched.
    stored = sonar_map(configuration_path, "hmcts.cath")
    assert stored is not None
    assert stored.mapping is not None
    assert stored.mapping.analysis_at == analysis_at
    assert "2 repositories mapped" in caplog.text


def test_map_sonar_exits_incomplete_when_one_project_could_not_be_read(
    configuration_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Keep every project that answered, and still refuse to call the run complete."""
    caplog.set_level(logging.INFO)
    respond = sonar_answers(
        projects=[
            {"key": "hmcts.cath", "visibility": "public", "analysisDate": "2026-08-27T09:49:35+0000"},
            {"key": "hmcts.refused", "visibility": "private", "analysisDate": "2026-08-27T09:49:35+0000"},
        ],
        analyses={"hmcts.cath": [{"date": "2026-08-27T09:49:35+0000", "revision": "ce34e614"}]},
        commits={"ce34e614": "hmcts/cath-service"},
        refusing=frozenset({"hmcts.refused"}),
    )

    status, _ = map_sonar_run(configuration_path, respond)

    assert status == INCOMPLETE_RUN
    assert sonar_map(configuration_path, "hmcts.cath") is not None
    # Nothing is written for a call that failed: a refusal is this run's problem, not the project's
    # answer, and storing it would stop the next run from ever asking again.
    assert sonar_map(configuration_path, "hmcts.refused") is None
    assert "1 resolved, 0 unchanged, 0 never analysed, 0 unresolvable, 1 failed" in caplog.text


def test_map_sonar_stops_at_a_spent_commit_search_quota_and_keeps_what_it_paid_for(
    configuration_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Stop the run at an exhausted rate limit rather than writing it into every remaining project.

    The limit applies to every project still to come exactly as it applied to this one, so a run that
    carried on would record its own exhaustion as each project's dead end.
    """
    respond = sonar_answers(
        projects=[
            {"key": "hmcts.cath", "visibility": "public", "analysisDate": "2026-08-27T09:49:35+0000"},
            {"key": "hmcts.limited", "visibility": "public", "analysisDate": "2026-08-27T09:49:35+0000"},
            {"key": "hmcts.unvisited", "visibility": "public", "analysisDate": "2026-08-27T09:49:35+0000"},
        ],
        analyses={
            "hmcts.cath": [{"date": "2026-08-27T09:49:35+0000", "revision": "ce34e614"}],
            "hmcts.limited": [{"date": "2026-08-27T09:49:35+0000", "revision": "c0dee111"}],
        },
        commits={"ce34e614": "hmcts/cath-service"},
        exhausted=frozenset({"c0dee111"}),
    )

    status, session = map_sonar_run(configuration_path, respond)

    assert status == INCOMPLETE_RUN
    assert sonar_map(configuration_path, "hmcts.cath") is not None
    assert sonar_map(configuration_path, "hmcts.limited") is None
    assert searched_projects(session) == ["hmcts.cath", "hmcts.limited"]
    assert "stopping and keeping the 1 projects already resolved" in caplog.text


def test_map_sonar_exits_one_when_the_organisation_lists_no_project(
    configuration_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Refuse an organisation that lists nothing, rather than reporting an empty map as a success."""
    status, session = map_sonar_run(configuration_path, sonar_answers(projects=[]))

    assert status == 1
    assert searched_projects(session) == []
    assert "SonarCloud lists no project for organisation hmcts" in caplog.text


def test_map_sonar_reports_an_unreadable_project_listing(
    configuration_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Say why nothing could be mapped when the listing itself is refused."""

    def refuse(url: str, *, params: dict[str, object] | None = None, timeout: int = 30) -> MagicMock:
        """Refuse every request, which is what an unreadable organisation looks like."""
        _ = url, params, timeout
        return MagicMock(status_code=401)

    status, _ = map_sonar_run(configuration_path, refuse)

    assert status == 1
    assert "SonarCloud project listing failed for hmcts" in caplog.text


def test_map_sonar_searches_github_under_the_github_organisation(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Keep the two organisation names apart: SonarCloud lists one, GitHub is searched for the other.

    They are the same string at HMCTS and are allowed to differ anywhere else, so collapsing them
    would qualify the commit search by an organisation that need not exist on GitHub at all — and
    every project would then be stored as unresolvable, permanently, since that is an observation.
    """
    caplog.set_level(logging.INFO)
    configuration_path = tmp_path / "metrics.yaml"
    configuration_path.write_text(
        """\
version: 1
organization: hmcts
sonar_organization: hmcts-sonar
database: metrics.sqlite3
teams:
  - identifier: divorce
    display_name: Divorce
    repositories:
      - nfdiv-case-api
""",
        encoding="utf-8",
    )
    respond = sonar_answers(
        projects=[{"key": "hmcts.cath", "visibility": "public", "analysisDate": "2026-08-27T09:49:35+0000"}],
        analyses={"hmcts.cath": [{"date": "2026-08-27T09:49:35+0000", "revision": "ce34e614"}]},
        commits={"ce34e614": "hmcts/cath-service"},
    )

    status, session = map_sonar_run(configuration_path, respond)

    assert status == 0
    searches = [
        str(call.kwargs["params"]["q"])
        for call in session.get.call_args_list
        if call.args[0].endswith("/search/commits")
    ]
    assert searches == ["org:hmcts hash:ce34e614"]
    listings = [
        str(call.kwargs["params"]["organization"])
        for call in session.get.call_args_list
        if call.args[0].endswith("/api/components/search_projects")
    ]
    assert listings == ["hmcts-sonar"]
    # Keyed under the SonarCloud organisation, which is what a later evidence run reads it back by.
    database = observation_database(configuration_path.parent / "metrics.sqlite3")
    assert load_sonar_mapping(database, "hmcts", "hmcts.cath") is None
    stored = load_sonar_mapping(database, "hmcts-sonar", "hmcts.cath")
    assert stored is not None
    assert stored.mapping is not None
    assert stored.mapping.repository == "cath-service"


def test_map_sonar_skips_an_unresolved_project_nothing_has_been_analysed_for_since(
    configuration_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Never re-ask a hopeless question: an unresolved row is an answer until a newer analysis exists."""
    caplog.set_level(logging.INFO)
    record_sonar_mapping(
        observation_database(configuration_path.parent / "metrics.sqlite3"),
        "hmcts",
        StoredSonarMapping(
            project_key="hmcts.unresolvable",
            resolved_at=datetime(2026, 8, 27, 12, 0, tzinfo=UTC),
            detail="no commit in hmcts matches any of the 5 most recently analysed revisions",
        ),
    )
    respond = sonar_answers(
        projects=[{"key": "hmcts.unresolvable", "visibility": "public", "analysisDate": "2026-08-27T09:49:35+0000"}],
    )

    status, session = map_sonar_run(configuration_path, respond)

    assert status == 0
    assert searched_projects(session) == []
    assert "0 resolved, 1 unchanged" in caplog.text


def test_map_sonar_re_resolves_an_unresolved_project_that_has_since_been_analysed(
    configuration_path: Path,
) -> None:
    """Ask again the moment there is something new to learn from, which is a newer analysis."""
    record_sonar_mapping(
        observation_database(configuration_path.parent / "metrics.sqlite3"),
        "hmcts",
        StoredSonarMapping(
            project_key="hmcts.cath",
            resolved_at=datetime(2026, 8, 20, 12, 0, tzinfo=UTC),
            detail="the project has no analysis to resolve from",
        ),
    )
    respond = sonar_answers(
        projects=[{"key": "hmcts.cath", "visibility": "public", "analysisDate": "2026-08-27T09:49:35+0000"}],
        analyses={"hmcts.cath": [{"date": "2026-08-27T09:49:35+0000", "revision": "ce34e614"}]},
        commits={"ce34e614": "hmcts/cath-service"},
    )

    status, session = map_sonar_run(configuration_path, respond)

    assert status == 0
    assert searched_projects(session) == ["hmcts.cath"]
    stored = sonar_map(configuration_path, "hmcts.cath")
    assert stored is not None
    assert stored.mapping is not None
    assert stored.mapping.repository == "cath-service"


def test_map_sonar_skips_a_project_resolved_from_an_older_analysis(
    configuration_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Converge on the projects resolution walks back for, rather than re-buying the same answer.

    `sonar_to_github` records the instant of whichever analysis resolved, which is older than the one
    the listing names whenever the newest commit is not findable. Keying the skip on that instant
    would re-search the project every run and then have `record_sonar_mapping` refuse the identical
    write — the exact quota the separate command exists to protect.
    """
    caplog.set_level(logging.INFO)
    record_sonar_mapping(
        observation_database(configuration_path.parent / "metrics.sqlite3"),
        "hmcts",
        StoredSonarMapping(
            project_key="hmcts.cath",
            resolved_at=datetime(2026, 8, 27, 12, 0, tzinfo=UTC),
            mapping=SonarProjectMapping(
                project_key="hmcts.cath",
                repository="cath-service",
                method=SonarResolution.ANALYSIS_REVISION,
                # Resolved from the analysis UNDER the one the listing reports below.
                analysis_at=datetime(2026, 8, 24, 9, 24, 56, tzinfo=UTC),
                revision="ce34e614",
            ),
        ),
    )
    respond = sonar_answers(
        projects=[{"key": "hmcts.cath", "visibility": "public", "analysisDate": "2026-08-27T09:49:35+0000"}],
    )

    status, session = map_sonar_run(configuration_path, respond)

    assert status == 0
    assert searched_projects(session) == []
    assert "1 unchanged" in caplog.text


def test_map_sonar_keeps_a_stored_mapping_resolved_from_a_newer_analysis(
    configuration_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Never let a re-resolution from older evidence overwrite a mapping a newer analysis produced."""
    caplog.set_level(logging.DEBUG)
    newer = datetime(2026, 9, 2, 9, 0, tzinfo=UTC)
    record_sonar_mapping(
        observation_database(configuration_path.parent / "metrics.sqlite3"),
        "hmcts",
        StoredSonarMapping(
            project_key="hmcts.cath",
            # Written before the newer analysis was listed, so the skip does not answer for it.
            resolved_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
            mapping=SonarProjectMapping(
                project_key="hmcts.cath",
                repository="cath-service",
                method=SonarResolution.ANALYSIS_REVISION,
                analysis_at=newer,
                revision="ce34e614",
            ),
        ),
    )
    respond = sonar_answers(
        projects=[{"key": "hmcts.cath", "visibility": "public", "analysisDate": "2026-08-27T09:49:35+0000"}],
        analyses={"hmcts.cath": [{"date": "2026-08-27T09:49:35+0000", "revision": "0ldc0m1"}]},
        commits={"0ldc0m1": "hmcts/moved-elsewhere"},
    )

    status, _ = map_sonar_run(configuration_path, respond)

    assert status == 0
    stored = sonar_map(configuration_path, "hmcts.cath")
    assert stored is not None
    assert stored.mapping is not None
    assert stored.mapping.repository == "cath-service"
    assert stored.mapping.analysis_at == newer
    assert "kept the stored mapping for hmcts.cath" in caplog.text


def test_map_sonar_exits_one_when_no_project_could_be_answered(
    configuration_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Refuse a run that learned nothing at all, rather than reporting a partial run that has no part."""
    caplog.set_level(logging.INFO)
    respond = sonar_answers(
        projects=[{"key": "hmcts.refused", "visibility": "private", "analysisDate": "2026-08-27T09:49:35+0000"}],
        refusing=frozenset({"hmcts.refused"}),
    )

    status, _ = map_sonar_run(configuration_path, respond)

    assert status == 1
    assert sonar_map(configuration_path, "hmcts.refused") is None
    assert "0 resolved, 0 unchanged, 0 never analysed, 0 unresolvable, 1 failed" in caplog.text


def test_map_sonar_reports_a_storage_failure(
    configuration_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Say why a run stopped when the file its whole output goes into cannot be read or written."""
    respond = sonar_answers(
        projects=[{"key": "hmcts.cath", "visibility": "public", "analysisDate": "2026-08-27T09:49:35+0000"}],
    )
    with patch("metrics.cli.load_sonar_mapping", side_effect=StorageError("database is locked")):
        status, _ = map_sonar_run(configuration_path, respond)

    assert status == 1
    assert "Storage failed: database is locked" in caplog.text


def test_map_sonar_requires_credentials(configuration_path: Path) -> None:
    """Call neither API: the commit search that resolves a project cannot be made anonymously.

    A session IS opened, unlike every version of this before App auth: proving an installation means
    exchanging a JWT for a token, and that exchange is itself an HTTP call.
    """
    with (
        patch("sys.argv", ["metrics", "map-sonar", "--config", str(configuration_path)]),
        patch.dict("os.environ", {}, clear=True),
        patch("metrics.cli.Session") as session_class,
    ):
        assert main() == 1

    session = session_class.return_value.__enter__.return_value
    session.get.assert_not_called()
    session.post.assert_not_called()


def test_map_sonar_runs_without_a_configured_team(configuration_path: Path) -> None:
    """Map the organisation from the policy file alone: no team, no repository list, is read here."""
    content = configuration_path.read_text(encoding="utf-8")
    configuration_path.write_text(content[: content.index("teams:")], encoding="utf-8")
    respond = sonar_answers(
        projects=[{"key": "hmcts.cath", "visibility": "public", "analysisDate": "2026-08-27T09:49:35+0000"}],
        analyses={"hmcts.cath": [{"date": "2026-08-27T09:49:35+0000", "revision": "ce34e614"}]},
        commits={"ce34e614": "hmcts/cath-service"},
    )

    status, _ = map_sonar_run(configuration_path, respond)

    assert status == 0
    stored = sonar_map(configuration_path, "hmcts.cath")
    assert stored is not None
    assert stored.mapping is not None
    assert stored.mapping.repository == "cath-service"


def evidence_for(
    window: ReportingWindow,
    repository: str = "nfdiv-case-api",
    body: str | None = None,
) -> RepositoryEvidence:
    """Build repository evidence spanning one window with a single unreviewed merge."""
    return RepositoryEvidence(
        organization="hmcts",
        repository=repository,
        starts_at=window.starts_at,
        ends_at=window.ends_at,
        provenance=WindowProvenance(offline=True, intervals_fetched=0),
        cohort=CohortSummary(merged=1, reported=1, excluded_authors={}),
        pull_requests=(
            PullRequestFact(
                identifier=101,
                repository=repository,
                number=11,
                created_at=window.starts_at,
                merged_at=window.starts_at + timedelta(days=1),
                draft=False,
                author_login="author",
                author_type="User",
                reviews=(),
                body=body,
            ),
        ),
    )


def collected_gate() -> MergeGateReport:
    """Build the merge gate report a previous collection would have stored."""
    return MergeGateReport(
        fetched_at=datetime(2026, 8, 8, 9, tzinfo=UTC),
        gate=MergeGateEvidence(
            branch="master",
            protected=True,
            pull_requests=(
                PullRequestRule(
                    dismiss_stale_reviews_on_push=True,
                    require_code_owner_review=False,
                    require_last_push_approval=False,
                    required_approving_review_count=1,
                    required_review_thread_resolution=False,
                ),
            ),
            status_checks=(),
            restricts_deletions=True,
            blocks_force_pushes=True,
            applies_to_administrators=False,
            rules_observed=True,
        ),
    )


def refused_open_pull_requests() -> OpenPullRequestReport:
    """Build the open pull-request report of a refresh GitHub would not answer."""
    return OpenPullRequestReport(detail="GitHub permission denied")


def cached_evidence() -> AbstractContextManager[MagicMock]:
    """Patch the cache loader so it echoes the window the command resolved."""
    return patch("metrics.cli.cached_repository_evidence", side_effect=lambda *arguments: evidence_for(arguments[2]))


def test_evidence_runs_offline_without_token_or_session(
    configuration_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Explain cached evidence without opening a GitHub session or requiring credentials."""
    with (
        patch(
            "sys.argv",
            [
                "metrics",
                "evidence",
                "--config",
                str(configuration_path),
                "--repository",
                "nfdiv-case-api",
                "--metric",
                "independent-review-coverage",
            ],
        ),
        patch.dict("os.environ", {}, clear=True),
        patch("metrics.cli.Session") as session_class,
        cached_evidence(),
    ):
        assert main() == 0

    output = json.loads(capsys.readouterr().out)
    assert output["metric"] == "independent-review-coverage"
    assert output["summary"] == {"status": "observed", "numerator": 0, "denominator": 1}
    assert output["classifications"] == {"no-review-events": 1}
    assert output["provenance"] == {"offline": True, "intervals_fetched": 0}
    assert not output["identities_included"]
    assert "pull_requests" not in output
    session_class.assert_not_called()


def test_evidence_resolves_no_credentials_offline(configuration_path: Path) -> None:
    """Never look for a credential in an offline run, rather than resolving one and not using it.

    Stronger than asserting no session was opened: an App would be resolved from a key on disk, which
    a run that promised to contact nothing has no business reading either.
    """
    with (
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path)]),
        patch.dict("os.environ", {}, clear=True),
        patch("metrics.cli.resolve_credentials") as resolve,
        cached_evidence(),
    ):
        assert main() == 0

    resolve.assert_not_called()


def test_evidence_defaults_to_all_observed_behaviour(
    configuration_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Emit observed behaviour for every configured repository when no filters are supplied."""
    with (
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path)]),
        patch.dict("os.environ", {}, clear=True),
        patch("metrics.cli.Session") as session_class,
        cached_evidence(),
    ):
        assert main() == 0

    output = json.loads(capsys.readouterr().out)
    assert output["organization"] == "hmcts"
    assert output["unavailable"] == []
    assert output["repositories"][0]["repository"] == "nfdiv-case-api"
    # The owning team and the nine aggregates ride in the block, so a reader of the JSON alone needs
    # neither the configuration beside it nor a second `--metric` run to group and read it.
    assert output["repositories"][0]["team"] == "divorce"
    assert [summary["metric"] for summary in output["repositories"][0]["metrics"]] == [
        "independent-review-coverage",
        "approval-coverage",
        "review-depth",
        "merge-cycle-time",
        "time-to-first-review",
        "pull-request-size",
        "checks-passing-at-merge",
        "description-quality",
        "traceability-reference",
    ]
    assert output["repositories"][0]["behaviour"][0]["rule"] == "unreviewed-merge"
    assert output["repositories"][0]["behaviour"][0]["actor_login"] == "author"
    assert output["repositories"][0]["behaviour"][0]["pull_requests"][0]["number"] == 11
    assert output["repositories"][0]["merge_gate"] == {
        "detail": "no repository state has been collected; run metrics collect",
    }
    session_class.assert_not_called()


def test_evidence_renders_the_same_evidence_as_a_readable_report(
    configuration_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Print every section of the practice report without changing what the JSON contract holds."""
    with (
        patch(
            "sys.argv",
            ["metrics", "evidence", "--config", str(configuration_path), "--format", "report"],
        ),
        patch.dict("os.environ", {}, clear=True),
        patch("metrics.evidence.stored_merge_gate", return_value=collected_gate()),
        cached_evidence(),
    ):
        assert main() == 0

    report = capsys.readouterr().out
    assert "\n====================\nhmcts/nfdiv-case-api" in report
    # The summary counts the labels the index below it lists, over the same models.
    assert report.startswith(
        "Repository Summary\n"
        "------------------\n"
        "  GREEN          0    0%\n"
        "  AMBER          0    0%\n"
        "  RED            0    0%\n"
        "  CANNOT_ASSESS  1  100%\n"
        "\n"
        "Index\n"
        "-----\n"
        "  Team         Repository      Readiness  Gate observed\n"
        "  divorce  nfdiv-case-api  CANNOT_ASSESS            yes\n",
    )
    for section in ("Cohort", "Behaviour", "Review breakdown", "Findings"):
        assert f"\n{section}\n{'-' * len(section)}\n" in report
    assert "Readiness: CANNOT_ASSESS" in report
    assert "Merge gate, read 2026-08-08T09:00Z" in report
    # The behaviour table is built from the same cached facts as the findings beneath it.
    assert "  independent-review-coverage              0%  0 of 1" in report
    assert "  review-depth                 not applicable       -" in report
    assert "  time-to-first-review         not applicable       -" in report
    assert "  no review events in the cohort" in report
    assert "  author  unreviewed-merge            1         1     100%" in report


def test_evidence_renders_a_metric_drill_down_as_a_report(
    configuration_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Render whichever report shape the requested evidence mode produced."""
    with (
        patch(
            "sys.argv",
            [
                "metrics",
                "evidence",
                "--config",
                str(configuration_path),
                "--metric",
                "approval-coverage",
                "--format",
                "report",
            ],
        ),
        patch.dict("os.environ", {}, clear=True),
        cached_evidence(),
    ):
        assert main() == 0

    report = capsys.readouterr().out
    assert "Metric: approval-coverage" in report
    assert "  no-review-events  1" in report
    # A drill-down reports one neutral aggregate, so it carries neither a readiness label nor a gate.
    assert "Readiness" not in report
    assert "Merge gate" not in report


def test_evidence_shows_the_collected_merge_gate_beside_observed_behaviour(
    configuration_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Report governance and behaviour together, with the instant each was observed."""
    with (
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path)]),
        patch.dict("os.environ", {}, clear=True),
        patch("metrics.evidence.stored_merge_gate", return_value=collected_gate()),
        cached_evidence(),
    ):
        assert main() == 0

    report = json.loads(capsys.readouterr().out)["repositories"][0]
    assert report["merge_gate"]["fetched_at"] == "2026-08-08T09:00:00Z"
    assert report["merge_gate"]["gate"]["pull_requests"][0]["required_approving_review_count"] == 1
    assert report["merge_gate"]["gate"]["applies_to_administrators"] is False
    # The gate is current state while the behaviour covers a historical window, so both are dated.
    assert report["behaviour"][0]["rule"] == "unreviewed-merge"
    assert report["ends_at"] != report["merge_gate"]["fetched_at"]


def traceability_configuration(tmp_path: Path) -> Path:
    """Write a configuration whose reference pattern neither shipped default would match."""
    path = tmp_path / "metrics.yaml"
    path.write_text(
        """\
version: 1
organization: hmcts
database: metrics.sqlite3
teams:
  - identifier: divorce
    display_name: Divorce
    repositories:
      - nfdiv-case-api
traceability:
  reference_patterns:
    - "ref:\\\\d+"
""",
        encoding="utf-8",
    )
    return path


def test_evidence_grades_a_metric_against_the_configured_traceability_patterns(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Carry the configured `traceability` block into the metric the command builds.

    Two of the nine metrics are configuration-dependent, and every other test uses the defaults, so
    building them from a fresh `TraceabilityConfiguration()` would pass the whole suite while
    silently ignoring what a user wrote. `ref:42` is chosen because neither shipped default pattern
    matches it: the reference is counted only if the configured pattern is the one in force.
    """
    configuration_path = traceability_configuration(tmp_path)
    with (
        patch(
            "sys.argv",
            [
                "metrics",
                "evidence",
                "--config",
                str(configuration_path),
                "--metric",
                "traceability-reference",
            ],
        ),
        patch.dict("os.environ", {}, clear=True),
        patch(
            "metrics.cli.cached_repository_evidence",
            side_effect=lambda *arguments: evidence_for(arguments[2], body="ref:42"),
        ),
    ):
        assert main() == 0

    output = json.loads(capsys.readouterr().out)["repositories"][0]
    assert output["summary"] == {"status": "observed", "numerator": 1, "denominator": 1}
    assert output["classifications"] == {"referenced": 1}


def test_evidence_report_drill_down_uses_the_configured_traceability_patterns(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Carry the configured `traceability` block into the readable report's behaviour table too.

    The table is built from its own construction of all nine metrics, separate from the `--metric`
    path, so the two have to be pinned separately or one of them silently reverts to the defaults.
    """
    configuration_path = traceability_configuration(tmp_path)
    with (
        patch(
            "sys.argv",
            ["metrics", "evidence", "--config", str(configuration_path), "--format", "report"],
        ),
        patch.dict("os.environ", {}, clear=True),
        patch(
            "metrics.cli.cached_repository_evidence",
            side_effect=lambda *arguments: evidence_for(arguments[2], body="ref:42"),
        ),
    ):
        assert main() == 0

    report = capsys.readouterr().out
    row = next(line for line in report.splitlines() if "traceability-reference" in line)
    assert "100%" in row
    assert "1 of 1" in row


def stored_state_for(repository: str) -> RepositoryInventory:
    """Build the current state a previous `collect` run would have stored for one repository."""
    collected_at = datetime(2026, 8, 8, 9, tzinfo=UTC)
    return RepositoryInventory(
        status=CollectionStatus.COMPLETE,
        organization="hmcts",
        collected_at=collected_at,
        starts_at=collected_at - timedelta(days=7),
        ends_at=collected_at,
        repositories=(
            RepositoryInventoryItem(
                team_identifier="divorce",
                repository=RepositoryMetadata(
                    name=repository,
                    default_branch="master",
                    archived=False,
                    fork=False,
                    disabled=False,
                    created_at=datetime(2021, 1, 1, tzinfo=UTC),
                    updated_at=collected_at,
                    pushed_at=collected_at,
                ),
                merge_gate=None,
                security=SecurityAlertEvidence(
                    dependabot=OpenAlertCount(open=3, by_severity={AlertSeverity.CRITICAL: 1, AlertSeverity.LOW: 2}),
                    code_scanning=OpenAlertCount(detail="GitHub permission denied"),
                    secret_scanning=OpenAlertCount(open=0),
                ),
                codeowners=CodeownersEvidence(
                    files=(CodeownersFile(path="docs/CODEOWNERS.md", size_bytes=42, recognised_by_github=False),),
                ),
                maintenance=MaintenanceEvidence(
                    branch="master",
                    last_commit_at=collected_at - timedelta(days=2),
                    last_human_commit_at=None,
                    searched_back_to=collected_at - timedelta(days=300),
                ),
            ),
        ),
        failures=(),
    )


def test_evidence_serves_the_security_block_from_what_collect_stored(
    configuration_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Join `collect`'s storage to what `evidence` prints, over the real database rather than a stub.

    Both ends of this seam are unit-tested and neither test crosses it: an `evidence` run that
    dropped the block, or read it from the wrong repository, would report every family as "not
    collected" — indistinguishable in the output from a token that never had the permissions.
    """
    record_repository_state(load_configuration(configuration_path).database, stored_state_for("nfdiv-case-api"))
    with (
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path)]),
        patch.dict("os.environ", {}, clear=True),
        cached_evidence(),
    ):
        assert main() == 0

    security = json.loads(capsys.readouterr().out)["repositories"][0]["security"]
    assert security["fetched_at"] == "2026-08-08T09:00:00Z"
    assert security["alerts"]["dependabot"] == {"open": 3, "by_severity": {"critical": 1, "low": 2}}
    # An unreadable family carries its reason and no count, never a zero that reads as a clean family.
    assert security["alerts"]["code_scanning"] == {"by_severity": {}, "detail": "GitHub permission denied"}
    assert "open" not in security["alerts"]["code_scanning"]
    assert security["alerts"]["secret_scanning"]["open"] == 0


def test_evidence_serves_the_standards_blocks_from_what_collect_stored(
    configuration_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Join `collect`'s storage to the CODEOWNERS and maintenance blocks `evidence` prints.

    The default JSON report carries both blocks with the stored `fetched_at`, the window rows
    derived at assembly, and the three-valued human answer kept three-valued on the wire: an
    unknown window omits the boolean and carries its reason instead.
    """
    record_repository_state(load_configuration(configuration_path).database, stored_state_for("nfdiv-case-api"))
    with (
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path)]),
        patch.dict("os.environ", {}, clear=True),
        cached_evidence(),
    ):
        assert main() == 0

    report = json.loads(capsys.readouterr().out)["repositories"][0]
    codeowners = report["codeowners"]
    assert codeowners["fetched_at"] == "2026-08-08T09:00:00Z"
    assert codeowners["codeowners"]["files"] == [
        {"path": "docs/CODEOWNERS.md", "size_bytes": 42, "recognised_by_github": False},
    ]
    maintenance = report["maintenance"]
    assert maintenance["fetched_at"] == "2026-08-08T09:00:00Z"
    assert maintenance["maintenance"]["last_commit_at"] == "2026-08-06T09:00:00Z"
    assert "last_human_commit_at" not in maintenance["maintenance"]
    assert maintenance["maintenance"]["searched_back_to"] == "2025-10-12T09:00:00Z"
    six_months, twelve_months, twenty_four_months = maintenance["windows"]
    assert six_months == {"months": 6, "committed_within": True, "human_committed_within": False}
    assert twelve_months["committed_within"] is True
    assert "human_committed_within" not in twelve_months
    assert twelve_months["human_detail"].startswith("the bounded search examined commits no older than")
    assert "human_committed_within" not in twenty_four_months


def test_collect_then_evidence_decides_the_widest_window_from_an_exhausted_search(
    configuration_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Pin the cross-layer invariant the 24-month human answer rests on.

    An exhausted bot-only history records `searched_back_to` as the collector's cutoff, and the
    report derives the 24-month cutoff from the stored `collected_at`, which is stamped AFTER every
    repository is collected. Only that ordering makes the widest window decidable as False rather
    than unknown; stamping `collected_at` at the start of the run would break it silently.
    """
    with (
        patch("sys.argv", ["metrics", "collect", "--config", str(configuration_path)]),
        patch.dict("os.environ", {"GH_TOKEN": "secret"}, clear=True),
        patch("metrics.cli.Session") as session_class,
    ):
        session = session_class.return_value.__enter__.return_value
        repository_response = MagicMock(status_code=200)
        repository_response.json.return_value = {
            "name": "nfdiv-case-api",
            "default_branch": "master",
            "archived": False,
            "fork": False,
            "disabled": False,
            "created_at": "2020-01-01T00:00:00Z",
            "updated_at": "2026-08-01T12:00:00Z",
            "pushed_at": "2026-08-01T11:00:00Z",
        }
        rules_response = MagicMock(status_code=200, links={})
        rules_response.json.return_value = []
        protection_response = Response()
        protection_response.status_code = 404
        session.get.side_effect = [repository_response, rules_response, protection_response, *alert_responses()]
        session.post.side_effect = graphql_responses(
            {"search": {"issueCount": 0, "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []}},
            {
                "repository": {
                    "defaultBranchRef": {
                        "target": {
                            "history": {
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                                "nodes": [],
                            }
                        }
                    }
                }
            },
            {"repository": {"object": {"statusCheckRollup": None}}},
            standards={
                "repository": {
                    "defaultBranchRef": {
                        "target": {
                            "history": {
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                                "nodes": [
                                    {
                                        "committedDate": "2026-08-01T00:00:00Z",
                                        "author": {
                                            "name": "renovate[bot]",
                                            "user": {"login": "renovate[bot]", "__typename": "Bot"},
                                        },
                                    },
                                ],
                            }
                        }
                    }
                }
            },
        )
        assert main() == 0
    capsys.readouterr()

    with (
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path)]),
        patch.dict("os.environ", {}, clear=True),
        cached_evidence(),
    ):
        assert main() == 0

    maintenance = json.loads(capsys.readouterr().out)["repositories"][0]["maintenance"]
    assert "last_human_commit_at" not in maintenance["maintenance"]
    assert [window["human_committed_within"] for window in maintenance["windows"]] == [False, False, False]
    assert all("human_detail" not in window for window in maintenance["windows"])


def test_evidence_reports_standards_blocks_for_a_repository_never_collected(
    configuration_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Report the reason on both blocks for a repository whose state was never collected."""
    with (
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path)]),
        patch.dict("os.environ", {}, clear=True),
        cached_evidence(),
    ):
        assert main() == 0

    report = json.loads(capsys.readouterr().out)["repositories"][0]
    assert report["codeowners"] == {"detail": "no repository state has been collected; run metrics collect"}
    assert report["maintenance"] == {
        "windows": [],
        "detail": "no repository state has been collected; run metrics collect",
    }


def test_evidence_serves_open_pull_requests_from_what_collect_stored(
    configuration_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Join `collect`'s storage to the open pull-request block, over the real database.

    The block that used to refuse offline is now read back with the window the collection measured
    its two windowed counts over, which is not the window this report covers.
    """
    stored = stored_state_for("nfdiv-case-api")
    collected_at = stored.collected_at
    snapshot = OpenPullRequestSnapshot(
        starts_at=collected_at - timedelta(days=7),
        ends_at=collected_at,
        summary=OpenPullRequestSummary(opened_in_window=5, closed_without_merge=1, currently_open=3, stale_open=2),
    )
    record_repository_state(
        load_configuration(configuration_path).database,
        stored.model_copy(
            update={"repositories": (stored.repositories[0].model_copy(update={"open_pull_requests": snapshot}),)},
        ),
    )
    with (
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path)]),
        patch.dict("os.environ", {}, clear=True),
        cached_evidence(),
    ):
        assert main() == 0

    report = json.loads(capsys.readouterr().out)["repositories"][0]["open_pull_requests"]
    assert report["fetched_at"] == "2026-08-08T09:00:00Z"
    assert report["starts_at"] == "2026-08-01T09:00:00Z"
    assert report["ends_at"] == "2026-08-08T09:00:00Z"
    assert report["summary"] == {
        "opened_in_window": 5,
        "closed_without_merge": 1,
        "currently_open": 3,
        "stale_open": 2,
    }


def test_evidence_reports_open_pull_requests_never_collected_rather_than_zeroing_them(
    configuration_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """State why the counts are missing rather than reporting a repository with nothing open."""
    with (
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path)]),
        patch.dict("os.environ", {}, clear=True),
        patch("metrics.evidence.stored_merge_gate", return_value=collected_gate()),
        cached_evidence(),
    ):
        assert main() == 0

    report = json.loads(capsys.readouterr().out)["repositories"][0]
    assert "summary" not in report["open_pull_requests"]
    assert report["open_pull_requests"]["detail"] == "no repository state has been collected; run metrics collect"


def test_evidence_refresh_overrides_the_stored_open_pull_request_block(
    configuration_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Observe open pull-request state live under `--refresh` and report that instead of the stored block."""
    stored = stored_state_for("nfdiv-case-api")
    record_repository_state(
        load_configuration(configuration_path).database,
        stored.model_copy(
            update={
                "repositories": (
                    stored.repositories[0].model_copy(
                        update={
                            "open_pull_requests": OpenPullRequestSnapshot(
                                starts_at=stored.starts_at,
                                ends_at=stored.ends_at,
                                summary=OpenPullRequestSummary(
                                    opened_in_window=0,
                                    closed_without_merge=0,
                                    currently_open=0,
                                    stale_open=0,
                                ),
                            ),
                        },
                    ),
                ),
            },
        ),
    )
    fresh = OpenPullRequestReport(
        fetched_at=datetime(2026, 8, 8, 12, tzinfo=UTC),
        starts_at=datetime(2026, 8, 1, tzinfo=UTC),
        ends_at=datetime(2026, 8, 8, tzinfo=UTC),
        summary=OpenPullRequestSummary(opened_in_window=5, closed_without_merge=1, currently_open=3, stale_open=2),
    )
    with (
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path), "--refresh"]),
        patch.dict("os.environ", {"GH_TOKEN": "secret"}, clear=True),
        patch("metrics.cli.Session"),
        patch(
            "metrics.cli.collected_repository_evidence",
            side_effect=lambda *arguments: evidence_for(arguments[3]),
        ),
        patch("metrics.evidence.stored_merge_gate", return_value=collected_gate()),
        patch("metrics.cli.open_pull_request_report", return_value=fresh),
    ):
        assert main() == 0

    report = json.loads(capsys.readouterr().out)["repositories"][0]
    assert report["open_pull_requests"]["fetched_at"] == "2026-08-08T12:00:00Z"
    assert report["open_pull_requests"]["summary"] == {
        "opened_in_window": 5,
        "closed_without_merge": 1,
        "currently_open": 3,
        "stale_open": 2,
    }
    # Every other block still comes from the stored row the refresh did not replace.
    assert report["security"]["fetched_at"] == "2026-08-08T09:00:00Z"


def test_evidence_refresh_keeps_the_stored_block_when_github_refuses(
    configuration_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Keep the stored open pull-request counts when the live observation a refresh asked for failed.

    A rate limit or a 403 on one repository would otherwise answer `--refresh` with strictly less
    than the same command without it: the stored block is readable and dated, and replacing it with
    "not available" loses counts that were true as at the instant they record.
    """
    stored = stored_state_for("nfdiv-case-api")
    record_repository_state(
        load_configuration(configuration_path).database,
        stored.model_copy(
            update={
                "repositories": (
                    stored.repositories[0].model_copy(
                        update={
                            "open_pull_requests": OpenPullRequestSnapshot(
                                starts_at=stored.starts_at,
                                ends_at=stored.ends_at,
                                summary=OpenPullRequestSummary(
                                    opened_in_window=4,
                                    closed_without_merge=1,
                                    currently_open=2,
                                    stale_open=1,
                                ),
                            ),
                        },
                    ),
                ),
            },
        ),
    )
    with (
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path), "--refresh"]),
        patch.dict("os.environ", {"GH_TOKEN": "secret"}, clear=True),
        patch("metrics.cli.Session"),
        patch(
            "metrics.cli.collected_repository_evidence",
            side_effect=lambda *arguments: evidence_for(arguments[3]),
        ),
        patch("metrics.evidence.stored_merge_gate", return_value=collected_gate()),
        patch("metrics.cli.open_pull_request_report", return_value=refused_open_pull_requests()),
    ):
        assert main() == 0

    report = json.loads(capsys.readouterr().out)["repositories"][0]
    assert report["open_pull_requests"]["summary"] == {
        "opened_in_window": 4,
        "closed_without_merge": 1,
        "currently_open": 2,
        "stale_open": 1,
    }
    # And the block still dates itself to the collection, not to the refresh that could not answer.
    assert report["open_pull_requests"]["fetched_at"] == "2026-08-08T09:00:00Z"


def test_evidence_fetches_both_phases_over_one_client(configuration_path: Path) -> None:
    """Collect the window and the open pull requests over a single session and client.

    The rate-limit budget lives on the client, so a second one built for the second phase would start
    believing GitHub had imposed no limit and could spend past the configured reserve before its
    first response taught it otherwise.
    """
    with (
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path), "--refresh"]),
        patch.dict("os.environ", {"GH_TOKEN": "secret"}, clear=True),
        patch("metrics.cli.Session") as session_class,
        patch("metrics.cli.GitHubClient") as client_class,
        patch(
            "metrics.cli.collected_repository_evidence",
            side_effect=lambda *arguments: evidence_for(arguments[3]),
        ),
        patch("metrics.evidence.stored_merge_gate", return_value=collected_gate()),
        patch(
            "metrics.cli.open_pull_request_report",
            return_value=OpenPullRequestReport(detail="not fetched by this test"),
        ) as open_pull_requests,
    ):
        assert main() == 0

    assert session_class.call_count == 1
    assert client_class.call_count == 1
    assert open_pull_requests.call_args.args[0] is client_class.return_value


def test_evidence_online_skips_open_pull_requests_for_a_repository_that_failed_collection(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Fetch open pull-request state only for repositories whose merge evidence collected successfully.

    `gather_open_pull_requests` is scoped to `evidence`, not every configured repository, precisely so
    the lookup in `evidence_report` never raises `KeyError` for a repository `gather_evidence` could
    not collect.
    """
    configuration_path = tmp_path / "metrics.yaml"
    configuration_path.write_text(
        """\
version: 1
organization: hmcts
database: metrics.sqlite3
teams:
  - identifier: divorce
    display_name: Divorce
    repositories:
      - nfdiv-case-api
      - nfdiv-case-orchestration
""",
        encoding="utf-8",
    )
    unavailable = EvidenceUnavailable(
        repository="nfdiv-case-orchestration", detail="cached evidence does not cover 2026-08-01"
    )
    summary = OpenPullRequestReport(
        fetched_at=datetime(2026, 8, 8, 12, tzinfo=UTC),
        summary=OpenPullRequestSummary(opened_in_window=1, closed_without_merge=0, currently_open=1, stale_open=0),
    )

    def collected(
        _configuration: object, _client: object, repository: str, window: ReportingWindow, _reference: object
    ) -> RepositoryEvidence | EvidenceUnavailable:
        return unavailable if repository == "nfdiv-case-orchestration" else evidence_for(window)

    with (
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path), "--refresh"]),
        patch.dict("os.environ", {"GH_TOKEN": "secret"}, clear=True),
        patch("metrics.cli.Session"),
        patch("metrics.cli.collected_repository_evidence", side_effect=collected),
        patch("metrics.evidence.stored_merge_gate", return_value=collected_gate()),
        patch("metrics.cli.open_pull_request_report", return_value=summary) as fetch,
    ):
        assert main() == 3

    fetch.assert_called_once()
    assert fetch.call_args.args[2] == "nfdiv-case-api"
    report = json.loads(capsys.readouterr().out)
    assert [item["repository"] for item in report["repositories"]] == ["nfdiv-case-api"]
    assert report["repositories"][0]["open_pull_requests"]["summary"]["currently_open"] == 1
    assert report["unavailable"] == [
        {"repository": "nfdiv-case-orchestration", "detail": "cached evidence does not cover 2026-08-01"}
    ]


def test_evidence_omits_a_failed_repository_from_the_numbers_rather_than_zeroing_it(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Report two of three repositories and say why the third is missing, never as a zeroed row.

    A zeroed row is the dangerous shape: `merged: 0` reads as a repository nobody merged into, and
    averaged across a population it silently drags every rate toward a denominator that was never
    observed. The same rule the per-family alert counts already follow.
    """
    configuration_path = population_configuration_path(tmp_path)
    unavailable = EvidenceUnavailable(
        repository="opal-common-lib",
        detail="GitHub rate limit exceeded after 3 attempts",
    )

    def collected(
        _configuration: object, _client: object, repository: str, window: ReportingWindow, _reference: object
    ) -> RepositoryEvidence | EvidenceUnavailable:
        return unavailable if repository == "opal-common-lib" else evidence_for(window, repository)

    with (
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path), "--refresh"]),
        patch.dict("os.environ", {"GH_TOKEN": "secret"}, clear=True),
        patch("metrics.cli.Session"),
        patch("metrics.cli.collected_repository_evidence", side_effect=collected),
        patch("metrics.evidence.stored_merge_gate", return_value=collected_gate()),
        patch("metrics.cli.open_pull_request_report", return_value=refused_open_pull_requests()),
    ):
        assert main() == 3

    report = json.loads(capsys.readouterr().out)
    assert [item["repository"] for item in report["repositories"]] == ["nfdiv-case-api", "opal-logging-service"]
    assert report["unavailable"] == [
        {"repository": "opal-common-lib", "detail": "GitHub rate limit exceeded after 3 attempts"},
    ]
    # The refused repository contributes no cohort, no assessment and no finding — it is named once,
    # under `unavailable`, and nowhere among the figures.
    assert all(item["cohort"]["merged"] > 0 for item in report["repositories"])
    assert [item["repository"] for item in report["repositories"] if item["repository"] == "opal-common-lib"] == []


def test_evidence_metric_drill_down_never_fetches_open_pull_requests(configuration_path: Path) -> None:
    """Skip the extra GitHub round trip entirely for a raw metric drill-down."""
    with (
        patch(
            "sys.argv",
            ["metrics", "evidence", "--config", str(configuration_path), "--metric", "approval-coverage"],
        ),
        patch.dict("os.environ", {}, clear=True),
        cached_evidence(),
        patch("metrics.cli.gather_open_pull_requests") as gather,
    ):
        assert main() == 0

    gather.assert_not_called()


def test_evidence_assesses_readiness_from_the_gate_and_observed_behaviour(
    configuration_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Answer the enablement question per repository, and never with a bare label."""
    with (
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path)]),
        patch.dict("os.environ", {}, clear=True),
        patch("metrics.evidence.stored_merge_gate", return_value=collected_gate()),
        cached_evidence(),
    ):
        assert main() == 0

    assessment = json.loads(capsys.readouterr().out)["repositories"][0]["assessment"]
    # One merged pull request cannot show a pattern, and an enforcing gate is not enough on its own.
    assert assessment["label"] == "cannot_assess"
    assert assessment["blocking"][0]["condition"] == "insufficient-merges"
    assert assessment["blocking"][0]["label"] == "cannot_assess"
    # This gate mirrors cath-service: it requires a review, but no CI check and not of administrators.
    assert [condition["condition"] for condition in assessment["caution"]] == [
        "status-checks-not-required",
        "administrators-can-bypass-the-gate",
    ]
    # The three neutral rules are reported in whichever state they were observed, never as a shortfall.
    assert [condition["condition"] for condition in assessment["clear"]] == [
        "branch-protected",
        "pull-request-review-required",
        "stale-reviews-dismissed",
        "force-pushes-blocked",
        "branch-deletion-restricted",
        "linear-history-not-required",
        "branch-names-not-restricted",
    ]
    # A caution and a clear condition impose no ceiling, so neither carries a label.
    assert "label" not in assessment["caution"][0]
    assert "label" not in assessment["clear"][0]


def test_evidence_reds_a_repository_whose_gate_requires_no_review(
    configuration_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Veto on governance whatever the window holds, including a window too thin to grade."""
    nominal = collected_gate()
    assert nominal.gate is not None
    with (
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path)]),
        patch.dict("os.environ", {}, clear=True),
        patch(
            "metrics.evidence.stored_merge_gate",
            return_value=nominal.model_copy(
                update={"gate": nominal.gate.model_copy(update={"pull_requests": ()})},
            ),
        ),
        cached_evidence(),
    ):
        assert main() == 0

    assessment = json.loads(capsys.readouterr().out)["repositories"][0]["assessment"]
    assert assessment["label"] == "red"
    assert assessment["blocking"][0]["condition"] == "pull-request-review-not-required"


def two_repository_configuration(tmp_path: Path) -> Path:
    """Write a configuration listing two repositories of one team."""
    path = tmp_path / "metrics.yaml"
    path.write_text(
        """\
version: 1
organization: hmcts
database: metrics.sqlite3
teams:
  - identifier: divorce
    display_name: Divorce
    repositories:
      - nfdiv-case-api
      - nfdiv-case-orchestration
""",
        encoding="utf-8",
    )
    return path


def evidence_authored_by(window: ReportingWindow, repository: str, author: str) -> RepositoryEvidence:
    """Build one repository's evidence with its single unreviewed merge authored by the given person."""
    evidence = evidence_for(window, repository)
    return evidence.model_copy(
        update={"pull_requests": (evidence.pull_requests[0].model_copy(update={"author_login": author}),)},
    )


def test_evidence_lists_each_actor_beside_the_readiness_of_what_they_contributed_to(
    configuration_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Carry the actor section in the JSON contract, built from the same repositories it reports."""
    with (
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path)]),
        patch.dict("os.environ", {}, clear=True),
        patch("metrics.evidence.stored_merge_gate", return_value=collected_gate()),
        cached_evidence(),
    ):
        assert main() == 0

    report = json.loads(capsys.readouterr().out)
    assert [actor["actor_login"] for actor in report["actors"]] == ["author"]
    row = report["actors"][0]["repositories"][0]
    # The label is the one the repository block carries, and the merge counted here is the one the
    # unreviewed-merge finding beside it is attributed to.
    assert {key: value for key, value in row.items() if key != "metrics"} == {
        "readiness": "cannot_assess",
        "repository": "nfdiv-case-api",
        "contributions": 1,
        "blocking": 1,
    }
    # Every metric the repository block reports, measured over this person's merges in this
    # repository alone — the one merge they authored here, which nobody reviewed.
    assert [summary["metric"] for summary in row["metrics"]] == [
        summary["metric"] for summary in report["repositories"][0]["metrics"]
    ]
    assert row["metrics"][0] == {
        "metric": "independent-review-coverage",
        "summary": {"status": "observed", "numerator": 0, "denominator": 1},
        "classifications": {"no-review-events": 1},
    }


def test_evidence_narrows_the_actor_section_to_the_requested_repository(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Report the actors of the one repository asked for, not of every repository configured."""
    configuration_path = two_repository_configuration(tmp_path)
    authors = {"nfdiv-case-api": "alice", "nfdiv-case-orchestration": "bob"}

    def cached(_configuration: object, repository: str, window: ReportingWindow) -> RepositoryEvidence:
        return evidence_authored_by(window, repository, authors[repository])

    with (
        patch(
            "sys.argv",
            [
                "metrics",
                "evidence",
                "--config",
                str(configuration_path),
                "--repository",
                "nfdiv-case-api",
            ],
        ),
        patch.dict("os.environ", {}, clear=True),
        patch("metrics.evidence.stored_merge_gate", return_value=collected_gate()),
        patch("metrics.cli.cached_repository_evidence", side_effect=cached),
    ):
        assert main() == 0

    report = json.loads(capsys.readouterr().out)
    assert [actor["actor_login"] for actor in report["actors"]] == ["alice"]
    assert [row["repository"] for row in report["actors"][0]["repositories"]] == ["nfdiv-case-api"]


def test_evidence_names_each_actor_against_the_repository_their_merges_were_read_from(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Pair every repository's authors with its own assessment, so no line reads one against another's."""
    configuration_path = two_repository_configuration(tmp_path)
    authors = {"nfdiv-case-api": "alice", "nfdiv-case-orchestration": "bob"}

    def cached(_configuration: object, repository: str, window: ReportingWindow) -> RepositoryEvidence:
        return evidence_authored_by(window, repository, authors[repository])

    with (
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path)]),
        patch.dict("os.environ", {}, clear=True),
        patch("metrics.evidence.stored_merge_gate", return_value=collected_gate()),
        patch("metrics.cli.cached_repository_evidence", side_effect=cached),
    ):
        assert main() == 0

    report = json.loads(capsys.readouterr().out)
    # Both repositories are available and both carry one merge, so only correct pairing puts each
    # author against the repository whose facts named them and the finding raised against it.
    assert {
        actor["actor_login"]: [(row["repository"], row["blocking"]) for row in actor["repositories"]]
        for actor in report["actors"]
    } == {"alice": [("nfdiv-case-api", 1)], "bob": [("nfdiv-case-orchestration", 1)]}


def test_evidence_omits_readiness_from_an_actor_row_when_the_policy_is_disabled(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Leave the key out of the JSON rather than publishing a null, which would read as a label of its own."""
    configuration_path = tmp_path / "metrics.yaml"
    configuration_path.write_text(
        """\
version: 1
organization: hmcts
database: metrics.sqlite3
assessment:
  enabled: false
teams:
  - identifier: divorce
    display_name: Divorce
    repositories:
      - nfdiv-case-api
""",
        encoding="utf-8",
    )

    with (
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path)]),
        patch.dict("os.environ", {}, clear=True),
        patch("metrics.evidence.stored_merge_gate", return_value=collected_gate()),
        cached_evidence(),
    ):
        assert main() == 0

    report = json.loads(capsys.readouterr().out)
    assert "readiness" not in report["actors"][0]["repositories"][0]


def test_evidence_gives_an_unavailable_repository_no_actors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Leave a repository no facts were read for out of the actor section, as it is left out of the numbers."""
    configuration_path = two_repository_configuration(tmp_path)
    unavailable = EvidenceUnavailable(
        repository="nfdiv-case-orchestration",
        detail="cached evidence does not cover 2026-08-01",
    )

    def cached(
        _configuration: object,
        repository: str,
        window: ReportingWindow,
    ) -> RepositoryEvidence | EvidenceUnavailable:
        if repository == "nfdiv-case-orchestration":
            return unavailable
        return evidence_authored_by(window, repository, "alice")

    with (
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path)]),
        patch.dict("os.environ", {}, clear=True),
        patch("metrics.evidence.stored_merge_gate", return_value=collected_gate()),
        patch("metrics.cli.cached_repository_evidence", side_effect=cached),
    ):
        assert main() == INCOMPLETE_RUN

    report = json.loads(capsys.readouterr().out)
    # Whoever contributed to the unavailable repository is unknown, so nobody is listed against it:
    # the section says as much about that repository as the numbers do, which is nothing.
    assert [actor["actor_login"] for actor in report["actors"]] == ["alice"]
    assert [row["repository"] for row in report["actors"][0]["repositories"]] == ["nfdiv-case-api"]


def test_evidence_metric_without_repository_reports_all_cached_repositories(
    configuration_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Apply an optional metric filter across every configured repository."""
    with (
        patch(
            "sys.argv",
            ["metrics", "evidence", "--config", str(configuration_path), "--metric", "approval-coverage"],
        ),
        patch.dict("os.environ", {}, clear=True),
        cached_evidence(),
    ):
        assert main() == 0

    output = json.loads(capsys.readouterr().out)
    assert output["metric"] == "approval-coverage"
    assert output["repositories"][0]["repository"] == "nfdiv-case-api"
    assert output["repositories"][0]["summary"] == {
        "status": "observed",
        "numerator": 0,
        "denominator": 1,
    }


def test_evidence_rejects_raw_identities_without_metric(
    configuration_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Keep raw review-event expansion limited to explicit metric drill-downs."""
    with (
        patch(
            "sys.argv",
            ["metrics", "evidence", "--config", str(configuration_path), "--include-identities"],
        ),
        patch.dict("os.environ", {}, clear=True),
    ):
        assert main() == 1

    assert "--include-identities requires --metric" in caplog.text


def test_evidence_rejects_unconfigured_repository(configuration_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Do not expose cached facts for a repository outside configured ownership."""
    with (
        patch(
            "sys.argv",
            [
                "metrics",
                "evidence",
                "--config",
                str(configuration_path),
                "--repository",
                "other-service",
                "--metric",
                "approval-coverage",
            ],
        ),
        patch.dict("os.environ", {}, clear=True),
    ):
        assert main() == 1

    assert "Repository is not configured: other-service" in caplog.text


def test_evidence_offline_refuses_a_window_no_repository_covers(
    configuration_path: Path,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Refuse rather than emit an empty report when the cache answers for nothing at all.

    The repository is named because the interval alone reads as "the cache is empty" when what
    actually happened is that one named repository was never collected.
    """
    unavailable = EvidenceUnavailable(repository="nfdiv-case-api", detail="cached evidence does not cover 2026-08-01")
    with (
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path)]),
        patch.dict("os.environ", {}, clear=True),
        patch("metrics.cli.cached_repository_evidence", return_value=unavailable),
    ):
        assert main() == 1

    assert "Evidence unavailable for all 1 repositories, first nfdiv-case-api" in caplog.text
    assert "cached evidence does not cover" in caplog.text
    assert capsys.readouterr().out == ""


def test_evidence_offline_reports_the_repositories_the_cache_does_cover(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Report a partly covered cache exactly as a partly collected run does, rather than refusing.

    One repository a collection lost to GitHub would otherwise make every cached repository beside it
    unreportable, which is the whole point of a cache. The status stays INCOMPLETE_RUN, and the
    dropped repository is named on the terminal as well as in the report, because a run redirected to
    a file shows nothing else.
    """
    configuration_path = tmp_path / "metrics.yaml"
    configuration_path.write_text(
        """\
version: 1
organization: hmcts
database: metrics.sqlite3
teams:
  - identifier: divorce
    display_name: Divorce
    repositories:
      - nfdiv-case-api
      - nfdiv-case-orchestration
""",
        encoding="utf-8",
    )
    unavailable = EvidenceUnavailable(
        repository="nfdiv-case-orchestration", detail="cached evidence does not cover 2026-08-01"
    )

    def cached(
        _configuration: object, repository: str, window: ReportingWindow
    ) -> RepositoryEvidence | EvidenceUnavailable:
        return unavailable if repository == "nfdiv-case-orchestration" else evidence_for(window)

    with (
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path)]),
        patch.dict("os.environ", {}, clear=True),
        patch("metrics.evidence.stored_merge_gate", return_value=collected_gate()),
        patch("metrics.cli.cached_repository_evidence", side_effect=cached),
    ):
        assert main() == INCOMPLETE_RUN

    report = json.loads(capsys.readouterr().out)
    assert [item["repository"] for item in report["repositories"]] == ["nfdiv-case-api"]
    assert report["unavailable"] == [
        {"repository": "nfdiv-case-orchestration", "detail": "cached evidence does not cover 2026-08-01"}
    ]
    assert "Evidence unavailable for 1 of 2 repositories" in caplog.text
    assert "nfdiv-case-orchestration" in caplog.text


def test_evidence_requires_credentials_only_under_refresh(
    configuration_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Explain that dropping the flag reports from the caches, which is the default."""
    with (
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path), "--refresh"]),
        patch.dict("os.environ", {}, clear=True),
    ):
        assert main() == 1

    assert "omit --refresh to report cached evidence only" in caplog.text
    # And what was missing, in the same record: the remedy alone leaves a human who wanted to refresh
    # guessing which variables to set.
    assert "GH_APP_ID" in caplog.text
    assert "GH_TOKEN" in caplog.text


def test_evidence_collects_the_requested_window_under_refresh(
    configuration_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Collect whatever the requested window needs when the run was asked to refresh."""
    with (
        patch(
            "sys.argv",
            [
                "metrics",
                "evidence",
                "--config",
                str(configuration_path),
                "--refresh",
                "--from",
                "2026-08-01",
                "--to",
                "2026-08-08",
            ],
        ),
        patch.dict("os.environ", {"GH_TOKEN": "secret"}, clear=True),
        patch("metrics.cli.Session"),
        patch(
            "metrics.cli.collected_repository_evidence",
            side_effect=lambda *arguments: evidence_for(arguments[3]),
        ) as collect,
        patch("metrics.cli.open_pull_request_report", return_value=refused_open_pull_requests()) as open_pull_requests,
    ):
        assert main() == 0

    output = json.loads(capsys.readouterr().out)
    assert output["repositories"][0]["starts_at"] == "2026-08-01T00:00:00Z"
    assert output["repositories"][0]["ends_at"] == "2026-08-08T00:00:00Z"
    collect.assert_called_once()
    open_pull_requests.assert_called_once()


def test_evidence_narrows_the_reporting_window(
    configuration_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Anchor a relative window to the most recent UTC midnight."""
    with (
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path), "--days", "7"]),
        patch.dict("os.environ", {}, clear=True),
        cached_evidence(),
    ):
        assert main() == 0

    output = json.loads(capsys.readouterr().out)
    starts_at = datetime.fromisoformat(output["repositories"][0]["starts_at"])
    ends_at = datetime.fromisoformat(output["repositories"][0]["ends_at"])
    assert ends_at - starts_at == timedelta(days=7)
    assert ends_at.hour == 0
    assert ends_at.minute == 0


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (("--days", "0"), "at least one day"),
        (("--days", "800"), "exceeds the 365-day maximum"),
        (("--from", "2026-08-01", "--to", "2026-08-05", "--days", "2"), "at most two of --from, --to, and --days"),
        (("--from", "yesterday"), "expected a date or datetime"),
        (("--from", "2026-08-08", "--to", "2026-08-01"), "end must follow its start"),
    ],
)
def test_evidence_rejects_an_unusable_reporting_window(
    configuration_path: Path,
    caplog: pytest.LogCaptureFixture,
    arguments: tuple[str, ...],
    message: str,
) -> None:
    """Refuse a window that is empty, contradictory, unparseable, or too long."""
    with (
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path), *arguments]),
        patch.dict("os.environ", {}, clear=True),
        cached_evidence(),
    ):
        assert main() == 1

    assert message in caplog.text


def test_evidence_reports_a_longer_window_when_the_maximum_is_raised(
    configuration_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Allow a longer span only when the configured maximum is explicitly raised."""
    with (
        patch(
            "sys.argv",
            [
                "metrics",
                "evidence",
                "--config",
                str(configuration_path),
                "--days",
                "120",
                "--maximum-days",
                "180",
            ],
        ),
        patch.dict("os.environ", {}, clear=True),
        cached_evidence(),
    ):
        assert main() == 0

    output = json.loads(capsys.readouterr().out)
    starts_at = datetime.fromisoformat(output["repositories"][0]["starts_at"])
    ends_at = datetime.fromisoformat(output["repositories"][0]["ends_at"])
    assert ends_at - starts_at == timedelta(days=120)


def test_evidence_reports_direct_commits_beside_the_denominator_they_grew(
    configuration_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Explain a governance rate end to end: the bypass count, and the finding it belongs to."""
    bypassed = patch(
        "metrics.cli.cached_repository_evidence",
        side_effect=lambda *arguments: evidence_for(arguments[2]).model_copy(
            update={
                "cohort": CohortSummary(merged=1, reported=1, excluded_authors={}, direct_commits=2),
                "direct_commits": (
                    DirectCommitFact(
                        sha="aaa",
                        committed_at=arguments[2].starts_at + timedelta(days=2),
                        author_login="author",
                        author_type="User",
                        additions=40,
                        deletions=2,
                        changed_files=3,
                    ),
                    DirectCommitFact(
                        sha="bbb",
                        committed_at=arguments[2].starts_at + timedelta(days=3),
                        author_login="author",
                        author_type="User",
                    ),
                ),
            },
        ),
    )
    with (
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path)]),
        patch.dict("os.environ", {}, clear=True),
        bypassed,
    ):
        assert main() == 0

    repository = json.loads(capsys.readouterr().out)["repositories"][0]
    finding = repository["behaviour"][0]

    assert repository["cohort"]["direct_commits"] == 2
    assert finding["occurrences"] == 3
    assert finding["authored_merges"] == 3
    assert [reference["sha"] for reference in finding["direct_commits"]] == ["aaa", "bbb"]
    assert finding["direct_commits"][0]["url"] == "https://github.com/hmcts/nfdiv-case-api/commit/aaa"


def test_the_readable_report_shows_the_denominator_the_rates_are_measured_over(
    configuration_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Print the bypass count and the total beside the rates they explain."""
    with (
        patch(
            "sys.argv",
            ["metrics", "evidence", "--config", str(configuration_path), "--format", "report"],
        ),
        patch.dict("os.environ", {}, clear=True),
        patch(
            "metrics.cli.cached_repository_evidence",
            side_effect=lambda *arguments: evidence_for(arguments[2]).model_copy(
                update={
                    "cohort": CohortSummary(merged=1, reported=1, excluded_authors={}, direct_commits=1),
                    "direct_commits": (
                        DirectCommitFact(
                            sha="aaa",
                            committed_at=arguments[2].starts_at + timedelta(days=2),
                            author_login="author",
                            author_type="User",
                        ),
                    ),
                },
            ),
        ),
    ):
        assert main() == 0

    report = capsys.readouterr().out

    assert "  Direct commits        1" in report
    assert "  Total merges          2" in report
    assert "  independent-review-coverage              0%  0 of 2" in report


@pytest.fixture
def trend_configuration_path(tmp_path: Path) -> Path:
    """Configure one repository that was enabled and one that never was."""
    path = tmp_path / "metrics.yaml"
    path.write_text(
        """\
version: 1
organization: hmcts
database: metrics.sqlite3
assessment:
  minimum_merges: 1
teams:
  - identifier: divorce
    display_name: Divorce
    repositories:
      - nfdiv-case-api
      - nfdiv-frontend
enablement:
  nfdiv-case-api: 2024-01-01
""",
        encoding="utf-8",
    )
    return path


def cached_periods() -> AbstractContextManager[MagicMock]:
    """Patch the cache loader the series reads through so it echoes each period it was asked for."""
    return patch(
        "metrics.trend.cached_repository_evidence",
        side_effect=lambda *arguments: evidence_for(arguments[2], arguments[1]),
    )


def test_trend_reports_a_series_per_repository_without_token_or_session(
    trend_configuration_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Anchor each period to the configured enablement instant, reading the cache alone."""
    with (
        patch(
            "sys.argv",
            ["metrics", "trend", "--config", str(trend_configuration_path), "--offline", "--periods", "3"],
        ),
        patch.dict("os.environ", {}, clear=True),
        patch("metrics.cli.Session") as session_class,
        cached_periods(),
    ):
        assert main() == 0

    output = json.loads(capsys.readouterr().out)
    series = output["repositories"][0]
    assert output["period_days"] == 28
    assert series["repository"] == "nfdiv-case-api"
    assert series["enablement_at"] == "2024-01-01T00:00:00Z"
    assert (series["baseline"]["starts_at"], series["baseline"]["ends_at"]) == (
        "2023-12-04T00:00:00Z",
        "2024-01-01T00:00:00Z",
    )
    assert [period["index"] for period in series["periods"]] == [1, 2, 3]
    assert series["periods"][0]["starts_at"] == "2024-01-01T00:00:00Z"
    assert series["periods"][0]["ends_at"] == "2024-01-29T00:00:00Z"
    assert series["periods"][0]["throughput"] == {
        "merges": 1,
        "merged_pull_requests": 1,
        "direct_commits": 0,
        "active_contributors": 1,
    }
    assert series["periods"][0]["metrics"][0]["metric"] == "independent-review-coverage"
    assert output["repositories"][1] == {
        "repository": "nfdiv-frontend",
        "detail": "no enablement date configured",
        "periods": [],
        "alert_observations": [],
    }
    session_class.assert_not_called()


def test_trend_exits_incomplete_when_one_period_could_not_be_read(
    trend_configuration_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Report the periods that were observed, and say which one was not, rather than reporting none."""
    uncovered = EvidenceUnavailable(repository="nfdiv-case-api", detail="cached evidence does not cover 2024-01-29")
    with (
        patch(
            "sys.argv",
            ["metrics", "trend", "--config", str(trend_configuration_path), "--offline", "--periods", "3"],
        ),
        patch.dict("os.environ", {}, clear=True),
        patch(
            "metrics.trend.cached_repository_evidence",
            side_effect=lambda *arguments: (
                uncovered
                if arguments[2].starts_at == datetime(2024, 1, 29, tzinfo=UTC)
                else evidence_for(arguments[2], arguments[1])
            ),
        ),
    ):
        assert main() == 3

    periods = json.loads(capsys.readouterr().out)["repositories"][0]["periods"]
    assert periods[1]["detail"] == "cached evidence does not cover 2024-01-29"
    assert "cohort" not in periods[1]
    assert periods[2]["cohort"]["reported"] == 1


def test_trend_exits_one_and_still_says_why_when_no_series_could_be_read(
    trend_configuration_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Emit the reasons a wholly unusable run produced: they are the most useful thing it has."""
    unavailable = EvidenceUnavailable(repository="nfdiv-case-api", detail="cached evidence does not cover the series")
    with (
        patch(
            "sys.argv",
            ["metrics", "trend", "--config", str(trend_configuration_path), "--offline", "--periods", "3"],
        ),
        patch.dict("os.environ", {}, clear=True),
        patch("metrics.trend.cached_repository_evidence", return_value=unavailable),
    ):
        assert main() == 1

    output = json.loads(capsys.readouterr().out)
    assert output["repositories"][0]["periods"][0]["detail"] == "cached evidence does not cover the series"


def test_trend_reports_an_unreadable_observation_history_rather_than_crashing(
    trend_configuration_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Fail with the reason when the alert observation file cannot be read, as `collect` does.

    A read that raises is the one thing a series must not absorb: an empty observation series and one
    the file refused to answer for look identical on the wire, and this history exists precisely to
    tell a stretch nobody collected apart from a stretch with nothing open.
    """
    with (
        patch("sys.argv", ["metrics", "trend", "--config", str(trend_configuration_path), "--offline"]),
        patch.dict("os.environ", {}, clear=True),
        cached_periods(),
        patch("metrics.trend.load_alert_observations", side_effect=StorageError("database is locked")),
    ):
        assert main() == 1

    assert "Trend failed: database is locked" in caplog.text


def test_trend_renders_an_enablement_offset_as_the_utc_instant_the_json_carries(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Print every window in UTC, whatever offset the enablement date was written in.

    A configured `+02:00` is preserved through to the JSON deliberately, so the report has to convert
    it rather than stamp a `Z` on a wall clock two hours away from the contract it claims to render.
    """
    path = tmp_path / "metrics.yaml"
    path.write_text(
        """\
version: 1
organization: hmcts
database: metrics.sqlite3
assessment:
  minimum_merges: 1
teams:
  - identifier: divorce
    display_name: Divorce
    repositories:
      - nfdiv-case-api
enablement:
  nfdiv-case-api: 2024-01-01T09:30:00+02:00
""",
        encoding="utf-8",
    )

    contract = json.loads(trend_run(path, "json", capsys))
    report = trend_run(path, "report", capsys)

    assert contract["repositories"][0]["enablement_at"] == "2024-01-01T09:30:00+02:00"
    assert "  Enabled  2024-01-01T07:30Z" in report
    assert "  Baseline  2023-12-04T07:30Z  2024-01-01T07:30Z" in report
    assert "  P1        2024-01-01T07:30Z  2024-01-29T07:30Z" in report


def trend_run(
    configuration_path: Path,
    output_format: str,
    capsys: pytest.CaptureFixture[str],
    cache: AbstractContextManager[MagicMock] | None = None,
) -> str:
    """Run one offline series over the echoing cache and return what the requested format emitted."""
    with (
        patch(
            "sys.argv",
            [
                "metrics",
                "trend",
                "--config",
                str(configuration_path),
                "--offline",
                "--periods",
                "2",
                "--format",
                output_format,
            ],
        ),
        patch.dict("os.environ", {}, clear=True),
        cache if cache is not None else cached_periods(),
    ):
        assert main() == 0

    return capsys.readouterr().out


DISTINCT_WINDOWS = {
    datetime(2023, 12, 4, tzinfo=UTC): (2, 0),
    datetime(2024, 1, 1, tzinfo=UTC): (3, 1),
    datetime(2024, 1, 29, tzinfo=UTC): (4, 3),
}
"""How many merges each window of the two-period series holds, and how many were reviewed.

Every window differs from every other, which is what lets an assertion about a column say anything:
a series whose windows all measure the same thing reads identically however its columns are ordered.
"""


def distinct_evidence(window: ReportingWindow, repository: str = "nfdiv-case-api") -> RepositoryEvidence:
    """Build evidence whose counts and review coverage differ from every other window's."""
    merges, reviewed = DISTINCT_WINDOWS[window.starts_at]
    return RepositoryEvidence(
        organization="hmcts",
        repository=repository,
        starts_at=window.starts_at,
        ends_at=window.ends_at,
        provenance=WindowProvenance(offline=True, intervals_fetched=0),
        cohort=CohortSummary(merged=merges, reported=merges, excluded_authors={}),
        pull_requests=tuple(
            PullRequestFact(
                identifier=100 + number,
                repository=repository,
                number=number,
                created_at=window.starts_at,
                merged_at=window.starts_at + timedelta(days=1),
                draft=False,
                author_login="author",
                author_type="User",
                reviews=(
                    ReviewFact(
                        identifier=200 + number,
                        submitted_at=window.starts_at + timedelta(hours=1),
                        state=ReviewState.APPROVED,
                        author_login="reviewer",
                        author_type="User",
                    ),
                )
                if number <= reviewed
                else (),
            )
            for number in range(1, merges + 1)
        ),
    )


def distinct_periods() -> AbstractContextManager[MagicMock]:
    """Patch the cache loader so every window of the series answers with different evidence."""
    return patch(
        "metrics.trend.cached_repository_evidence",
        side_effect=lambda *arguments: distinct_evidence(arguments[2], arguments[1]),
    )


def test_trend_renders_the_series_as_a_readable_report(
    trend_configuration_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Render the same series as plain ASCII, under an index covering every configured repository."""
    output = trend_run(trend_configuration_path, "report", capsys)

    assert output.startswith("Index\n-----\n")
    assert "  divorce  nfdiv-case-api  2024-01-01T00:00Z            2 of 2" in output
    assert "  divorce  nfdiv-frontend     not configured              none" in output
    assert "  Enabled  2024-01-01T00:00Z" in output
    assert "  Series   2 whole periods since enablement" in output
    assert "  Baseline  2023-12-04T00:00Z  2024-01-01T00:00Z" in output
    assert "  P1        2024-01-01T00:00Z  2024-01-29T00:00Z" in output


def test_the_rendered_trend_and_the_trend_json_carry_the_same_numbers(
    trend_configuration_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Pin the two renderings of one series together end to end, because the JSON is the contract.

    Both are built from the same models, so the rendering can only disagree with the JSON by
    computing something of its own — which is what this asserts it does not do.

    Every window measures something different, so a column read from the wrong window fails here: an
    assertion over a series whose baseline and periods hold identical numbers passes whatever order
    the renderer prints them in, and pins nothing.
    """
    metric = "independent-review-coverage"
    contract = json.loads(trend_run(trend_configuration_path, "json", capsys, distinct_periods()))
    report = trend_run(trend_configuration_path, "report", capsys, distinct_periods())

    series = contract["repositories"][0]
    windows = (series["baseline"], *series["periods"])
    measured = [next(item["value"] for item in window["metrics"] if item["metric"] == metric) for window in windows]
    changes = [next(item["change"] for item in period["deltas"] if item["measure"] == metric) for period in windows[1:]]
    counts = [window["throughput"]["merges"] for window in windows]
    values_row, changes_row = (line.split() for line in report.splitlines() if line.startswith(f"  {metric}  "))
    merges_row, merge_changes_row = (line.split() for line in report.splitlines() if line.startswith("  merges  "))

    assert measured == [0, 33.3, 75]
    assert counts == [2, 3, 4]
    assert values_row[-3:] == [f"{value:g}" for value in measured]
    assert changes_row[-2:] == [f"{change:+g}" for change in changes]
    assert merges_row[-3:] == [str(count) for count in counts]
    assert merge_changes_row[-2:] == [
        f"{next(item['change'] for item in period['deltas'] if item['measure'] == 'merges'):+g}"
        for period in series["periods"]
    ]


def test_trend_collects_the_periods_the_cache_lacks(
    trend_configuration_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Collect what a period needs when the run is not asked to stay offline."""
    with (
        patch(
            "sys.argv",
            ["metrics", "trend", "--config", str(trend_configuration_path), "--periods", "2"],
        ),
        patch.dict("os.environ", {"GH_TOKEN": "secret"}, clear=True),
        patch("metrics.cli.Session"),
        patch(
            "metrics.trend.collected_repository_evidence",
            side_effect=lambda *arguments: evidence_for(arguments[3], arguments[2]),
        ) as collect,
    ):
        assert main() == 0

    assert collect.call_count == 3
    assert len(json.loads(capsys.readouterr().out)["repositories"][0]["periods"]) == 2


def test_trend_requires_credentials_before_collecting(
    trend_configuration_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Explain that a series collects what the cache lacks unless it is asked to stay offline."""
    with (
        patch("sys.argv", ["metrics", "trend", "--config", str(trend_configuration_path)]),
        patch.dict("os.environ", {}, clear=True),
    ):
        assert main() == 1

    assert "use --offline to report cached evidence only" in caplog.text


def test_trend_resolves_no_credentials_offline(trend_configuration_path: Path) -> None:
    """Read a cached series with no credential of any kind, as `evidence --offline` does."""
    with (
        patch(
            "sys.argv",
            ["metrics", "trend", "--config", str(trend_configuration_path), "--offline", "--periods", "3"],
        ),
        patch.dict("os.environ", {}, clear=True),
        patch("metrics.cli.resolve_credentials") as resolve,
        cached_periods(),
    ):
        assert main() == 0

    resolve.assert_not_called()


@pytest.mark.parametrize(
    ("arguments", "message"),
    [(("--period-days", "0"), "at least one day"), (("--periods", "0"), "at least one period")],
)
def test_trend_rejects_a_series_it_cannot_describe(
    trend_configuration_path: Path,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    arguments: tuple[str, ...],
    message: str,
) -> None:
    """Refuse an impossible series rather than reporting an empty one as though it were fine."""
    with (
        patch("sys.argv", ["metrics", "trend", "--config", str(trend_configuration_path), "--offline", *arguments]),
        patch.dict("os.environ", {}, clear=True),
    ):
        assert main() == 1

    assert message in caplog.text
    assert capsys.readouterr().out == ""


def unordered_configuration_path(tmp_path: Path) -> Path:
    """Configure three repositories across two teams, in neither the team nor the name order."""
    path = tmp_path / "metrics.yaml"
    path.write_text(
        """\
version: 1
organization: hmcts
database: metrics.sqlite3
teams:
  - identifier: opal
    display_name: Opal
    repositories:
      - opal-logging-service
      - opal-common-lib
  - identifier: divorce
    display_name: Divorce
    repositories:
      - nfdiv-case-api
""",
        encoding="utf-8",
    )
    return path


def test_collect_visits_repositories_by_team_and_name_whatever_order_the_file_lists(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Order the collection report so an edit to the configuration cannot reorder two runs' output."""
    configuration_path = unordered_configuration_path(tmp_path)
    with (
        patch("sys.argv", ["metrics", "collect", "--config", str(configuration_path)]),
        patch.dict("os.environ", {"GH_TOKEN": "secret"}, clear=True),
        patch("metrics.cli.Session") as session_class,
    ):
        session = session_class.return_value.__enter__.return_value
        session.get.side_effect = [
            *collectable_repository_responses("nfdiv-case-api"),
            *collectable_repository_responses("opal-common-lib"),
            *collectable_repository_responses("opal-logging-service"),
        ]
        session.post.side_effect = empty_window_responses()

        assert main() == 0

        requested = [
            call.args[0].removeprefix("https://api.github.com/repos/hmcts/")
            for call in session.get.call_args_list
            if call.args[0].count("/") == 5
        ]

    assert requested == ["nfdiv-case-api", "opal-common-lib", "opal-logging-service"]
    output = json.loads(capsys.readouterr().out)
    assert [item["repository"]["name"] for item in output["repositories"]] == requested
    assert [item["team_identifier"] for item in output["repositories"]] == ["divorce", "opal", "opal"]


def test_evidence_orders_repositories_by_team_and_name_whatever_order_the_file_lists(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Guarantee one JSON ordering, which is the only change this report's contract permits.

    Ordering is presentation, not aggregation: the repositories are listed, never combined.
    """
    configuration_path = unordered_configuration_path(tmp_path)
    with (
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path)]),
        patch.dict("os.environ", {}, clear=True),
        patch(
            "metrics.cli.cached_repository_evidence",
            side_effect=lambda *arguments: evidence_for(arguments[2], arguments[1]),
        ),
    ):
        assert main() == 0

    output = json.loads(capsys.readouterr().out)
    assert [item["repository"] for item in output["repositories"]] == [
        "nfdiv-case-api",
        "opal-common-lib",
        "opal-logging-service",
    ]
