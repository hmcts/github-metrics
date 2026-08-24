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
from requests import ConnectionError as RequestsConnectionError
from requests import Response

from metrics.cli import INCOMPLETE_RUN, main
from metrics.config import load_configuration
from metrics.domain import (
    AlertSeverity,
    CohortSummary,
    CollectionStatus,
    DirectCommitFact,
    EvidenceUnavailable,
    MergeGateEvidence,
    MergeGateReport,
    OpenAlertCount,
    OpenPullRequestReport,
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
    WindowProvenance,
)
from metrics.evidence import RepositoryEvidence
from metrics.storage import StorageError, record_repository_state


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


def graphql_responses(
    search: dict[str, object],
    history: dict[str, object],
    checks: dict[str, object],
) -> Callable[..., MagicMock]:
    """Answer each GraphQL request with the payload belonging to the document it sent."""

    def respond(url: str, *, json: dict[str, object], timeout: int) -> MagicMock:
        """Return the response for one posted GraphQL document."""
        _ = url, timeout
        query = str(json["query"])
        payload = history if "defaultBranchRef" in query else checks if "object(oid:" in query else search
        response = MagicMock(status_code=200, headers={})
        response.json.return_value = {"data": payload}
        return response

    return respond


def test_doctor_succeeds(configuration_path: Path) -> None:
    """Validate configuration and accessible repositories."""
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
        timeout=30,
    )


def test_doctor_requires_token(configuration_path: Path) -> None:
    """Fail before making requests when the token is absent."""
    with (
        patch("sys.argv", ["metrics", "doctor", "--config", str(configuration_path)]),
        patch.dict("os.environ", {}, clear=True),
        patch("metrics.cli.Session") as session_class,
    ):
        assert main() == 1

    session_class.assert_not_called()


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
    # One repository's whole run, both phases: six current-state calls — the repository, its rules,
    # its protection and one page per alert family — and four GraphQL queries for the window — a
    # pull-request search over the stable interval and another over the mutable edge, the commit
    # history, and one check rollup for the single direct commit it returned.
    assert output["costs"] == [
        {"repository": "nfdiv-case-api", "requests": 10, "elapsed_seconds": ANY},
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
        timeout=30,
    )


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


def empty_window_responses() -> Callable[..., MagicMock]:
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
    )


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


def offline_open_pull_requests() -> OpenPullRequestReport:
    """Build the open pull-request report of a window that never asked GitHub for it."""
    return OpenPullRequestReport(detail="open pull-request state is never cached; omit --offline to observe it")


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
                "--offline",
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


def test_evidence_defaults_to_all_observed_behaviour(
    configuration_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Emit observed behaviour for every configured repository when no filters are supplied."""
    with (
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path), "--offline"]),
        patch.dict("os.environ", {}, clear=True),
        patch("metrics.cli.Session") as session_class,
        cached_evidence(),
    ):
        assert main() == 0

    output = json.loads(capsys.readouterr().out)
    assert output["organization"] == "hmcts"
    assert output["unavailable"] == []
    assert output["repositories"][0]["repository"] == "nfdiv-case-api"
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
            ["metrics", "evidence", "--config", str(configuration_path), "--offline", "--format", "report"],
        ),
        patch.dict("os.environ", {}, clear=True),
        patch("metrics.cli.stored_merge_gate", return_value=collected_gate()),
        cached_evidence(),
    ):
        assert main() == 0

    report = capsys.readouterr().out
    assert "\n====================\nhmcts/nfdiv-case-api" in report
    # The index reads every cell from the same models the block beneath it renders.
    assert report.startswith(
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
                "--offline",
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
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path), "--offline"]),
        patch.dict("os.environ", {}, clear=True),
        patch("metrics.cli.stored_merge_gate", return_value=collected_gate()),
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
                "--offline",
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
            ["metrics", "evidence", "--config", str(configuration_path), "--offline", "--format", "report"],
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
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path), "--offline"]),
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


def test_evidence_reports_open_pull_requests_as_unavailable_offline(
    configuration_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """State why open pull-request state is missing rather than a remembered or zeroed count."""
    with (
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path), "--offline"]),
        patch.dict("os.environ", {}, clear=True),
        patch("metrics.cli.stored_merge_gate", return_value=collected_gate()),
        cached_evidence(),
    ):
        assert main() == 0

    report = json.loads(capsys.readouterr().out)["repositories"][0]
    assert "summary" not in report["open_pull_requests"]
    assert (
        report["open_pull_requests"]["detail"]
        == "open pull-request state is never cached; omit --offline to observe it"
    )


def test_evidence_reports_open_pull_requests_fetched_fresh_when_collecting(
    configuration_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Fetch open pull-request state alongside a collecting run and report the counts fresh."""
    fetched_at = datetime(2026, 8, 8, 12, tzinfo=UTC)
    summary = OpenPullRequestReport(
        fetched_at=fetched_at,
        summary=OpenPullRequestSummary(opened_in_window=5, closed_without_merge=1, currently_open=3, stale_open=2),
    )
    with (
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path)]),
        patch.dict("os.environ", {"GH_TOKEN": "secret"}, clear=True),
        patch("metrics.cli.Session"),
        patch(
            "metrics.cli.collected_repository_evidence",
            side_effect=lambda *arguments: evidence_for(arguments[3]),
        ),
        patch("metrics.cli.stored_merge_gate", return_value=collected_gate()),
        patch("metrics.cli.open_pull_request_report", return_value=summary),
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


def test_evidence_fetches_both_phases_over_one_client(configuration_path: Path) -> None:
    """Collect the window and the open pull requests over a single session and client.

    The rate-limit budget lives on the client, so a second one built for the second phase would start
    believing GitHub had imposed no limit and could spend past the configured reserve before its
    first response taught it otherwise.
    """
    with (
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path)]),
        patch.dict("os.environ", {"GH_TOKEN": "secret"}, clear=True),
        patch("metrics.cli.Session") as session_class,
        patch("metrics.cli.GitHubClient") as client_class,
        patch(
            "metrics.cli.collected_repository_evidence",
            side_effect=lambda *arguments: evidence_for(arguments[3]),
        ),
        patch("metrics.cli.stored_merge_gate", return_value=collected_gate()),
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
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path)]),
        patch.dict("os.environ", {"GH_TOKEN": "secret"}, clear=True),
        patch("metrics.cli.Session"),
        patch("metrics.cli.collected_repository_evidence", side_effect=collected),
        patch("metrics.cli.stored_merge_gate", return_value=collected_gate()),
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
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path)]),
        patch.dict("os.environ", {"GH_TOKEN": "secret"}, clear=True),
        patch("metrics.cli.Session"),
        patch("metrics.cli.collected_repository_evidence", side_effect=collected),
        patch("metrics.cli.stored_merge_gate", return_value=collected_gate()),
        patch("metrics.cli.open_pull_request_report", return_value=offline_open_pull_requests()),
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
            ["metrics", "evidence", "--config", str(configuration_path), "--offline", "--metric", "approval-coverage"],
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
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path), "--offline"]),
        patch.dict("os.environ", {}, clear=True),
        patch("metrics.cli.stored_merge_gate", return_value=collected_gate()),
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
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path), "--offline"]),
        patch.dict("os.environ", {}, clear=True),
        patch(
            "metrics.cli.stored_merge_gate",
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


def test_evidence_metric_without_repository_reports_all_cached_repositories(
    configuration_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Apply an optional metric filter across every configured repository."""
    with (
        patch(
            "sys.argv",
            ["metrics", "evidence", "--config", str(configuration_path), "--offline", "--metric", "approval-coverage"],
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
            ["metrics", "evidence", "--config", str(configuration_path), "--offline", "--include-identities"],
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
                "--offline",
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
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path), "--offline"]),
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
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path), "--offline"]),
        patch.dict("os.environ", {}, clear=True),
        patch("metrics.cli.stored_merge_gate", return_value=collected_gate()),
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


def test_evidence_requires_a_token_before_collecting(
    configuration_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Explain that reporting collects unless it is asked to stay offline."""
    with (
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path)]),
        patch.dict("os.environ", {}, clear=True),
    ):
        assert main() == 1

    assert "use --offline to report cached evidence only" in caplog.text


def test_evidence_collects_the_requested_window(
    configuration_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Collect whatever the requested window needs when not asked to stay offline."""
    with (
        patch(
            "sys.argv",
            [
                "metrics",
                "evidence",
                "--config",
                str(configuration_path),
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
        patch("metrics.cli.open_pull_request_report", return_value=offline_open_pull_requests()) as open_pull_requests,
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
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path), "--offline", "--days", "7"]),
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
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path), "--offline", *arguments]),
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
                "--offline",
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
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path), "--offline"]),
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
            ["metrics", "evidence", "--config", str(configuration_path), "--offline", "--format", "report"],
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


def test_trend_requires_a_token_before_collecting(
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
        patch("sys.argv", ["metrics", "evidence", "--config", str(configuration_path), "--offline"]),
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
