"""Test pull-request behaviour collection."""

import re
from collections.abc import Callable, Mapping
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from sqlite3 import connect
from unittest.mock import MagicMock, patch

import pytest
from requests import Session

from metrics.behaviour import (
    GitHubCheckContext,
    GitHubOpenPullRequestData,
    GitHubPullRequest,
    GitHubReview,
    cache_status,
    check_fact,
    collect_direct_commits,
    collect_merged_pull_requests,
    collect_open_pull_request_state,
    collect_window,
    mutable_edge,
    open_pull_request_query,
    open_pull_request_search_queries,
    parse_checks,
    parse_commit_history,
    parse_open_pull_request_state,
    parse_reviews,
    parse_search,
    pull_request_query,
    review_query,
    synchronize_merges,
    synchronize_pull_request_facts,
    window_progress,
)
from metrics.config import Configuration, TeamConfiguration
from metrics.credentials import PersonalAccessToken
from metrics.domain import (
    AvailabilityReason,
    BehaviourProvenance,
    CacheStatus,
    CheckConclusion,
    CheckRollupState,
    CollectionStatus,
    EvidenceKind,
    OpenPullRequestSummary,
    ReportingWindow,
    RepositoryCollectionCost,
    RepositoryInventory,
    RepositoryInventoryIssue,
    RepositoryInventoryItem,
    RepositoryMetadata,
)
from metrics.github import GitHubClient, GitHubError


def page(*, has_next_page: bool = False, end_cursor: str | None = None) -> dict[str, object]:
    """Build GraphQL page information."""
    return {"hasNextPage": has_next_page, "endCursor": end_cursor}


def rollup(
    *contexts: dict[str, object], has_next_page: bool = False, end_cursor: str | None = None
) -> dict[str, object]:
    """Build a head-commit status-check rollup for one pull request."""
    return {
        "nodes": [
            {
                "commit": {
                    "statusCheckRollup": {
                        "contexts": {
                            "pageInfo": page(has_next_page=has_next_page, end_cursor=end_cursor),
                            "nodes": list(contexts),
                        },
                    },
                },
            },
        ],
    }


def unchecked() -> dict[str, object]:
    """Build a head commit that carries no status-check rollup at all."""
    return {"nodes": [{"commit": {"statusCheckRollup": None}}]}


def check_run(
    name: str, conclusion: str | None, completed_at: str | None, status: str = "COMPLETED"
) -> dict[str, object]:
    """Build one GraphQL check-run rollup context."""
    return {
        "__typename": "CheckRun",
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "completedAt": completed_at,
    }


def test_collect_merged_pull_requests_parses_paginated_pull_requests_and_reviews() -> None:
    """Collect shallow PR pages and follow only an overflowing review connection."""
    client = MagicMock(spec=GitHubClient)
    client.graphql.side_effect = [
        {
            "search": {
                "issueCount": 2,
                "pageInfo": page(has_next_page=True, end_cursor="pr-page-2"),
                "nodes": [
                    {
                        "databaseId": 101,
                        "number": 11,
                        "title": "Fix #42",
                        "body": "Closes #42, see JIRA-7 for context.",
                        "createdAt": "2026-07-01T00:00:00Z",
                        "mergedAt": "2026-07-03T00:00:00Z",
                        "isDraft": False,
                        "timelineItems": {"nodes": [{"createdAt": "2026-07-01T18:00:00Z"}]},
                        "author": {"login": "author", "__typename": "User"},
                        "reviews": {
                            "pageInfo": page(has_next_page=True, end_cursor="review-page-2"),
                            "nodes": [
                                {
                                    "databaseId": 201,
                                    "submittedAt": "2026-07-02T00:00:00Z",
                                    "state": "COMMENTED",
                                    "author": {"login": "reviewer", "__typename": "User"},
                                    "body": "   ",
                                    "comments": {"totalCount": 3},
                                },
                            ],
                        },
                        "commits": rollup(check_run("build", "SUCCESS", "2026-07-02T23:00:00Z")),
                    },
                ],
            },
        },
        {
            "repository": {
                "pullRequest": {
                    "reviews": {
                        "pageInfo": page(),
                        "nodes": [
                            {
                                "databaseId": 202,
                                "submittedAt": "2026-07-02T12:00:00Z",
                                "state": "APPROVED",
                                "author": {"login": "approver", "__typename": "User"},
                                "body": "Checked the migration order.",
                                "comments": {"totalCount": 0},
                            },
                        ],
                    },
                },
            },
        },
        {
            "search": {
                "issueCount": 2,
                "pageInfo": page(),
                "nodes": [
                    {
                        "databaseId": 102,
                        "number": 12,
                        "createdAt": "2026-07-03T00:00:00Z",
                        "mergedAt": "2026-07-04T00:00:00Z",
                        "isDraft": False,
                        "author": {"login": "second-author", "__typename": "User"},
                        "reviews": {"pageInfo": page(), "nodes": []},
                        "commits": unchecked(),
                    },
                ],
            },
        },
    ]

    facts = collect_merged_pull_requests(
        client,
        "hmcts",
        "cath-service",
        datetime(2026, 7, 1, tzinfo=UTC),
        datetime(2026, 7, 31, tzinfo=UTC),
    )

    assert tuple(fact.number for fact in facts) == (11, 12)
    assert facts[0].title == "Fix #42"
    assert facts[0].body == "Closes #42, see JIRA-7 for context."
    assert facts[1].title is None
    assert facts[1].body is None
    assert tuple(review.identifier for review in facts[0].reviews) == (201, 202)
    assert tuple(review.comment_count for review in facts[0].reviews) == (3, 0)
    # A whitespace-only body said nothing; the continuation page's approval spoke only in its body.
    assert tuple(review.has_body for review in facts[0].reviews) == (False, True)
    assert facts[1].reviews == ()
    # The ready-for-review event anchors the waiting times; a pull request opened ready has none.
    assert facts[0].ready_for_review_at == datetime(2026, 7, 1, 18, tzinfo=UTC)
    assert facts[1].ready_for_review_at is None
    assert tuple(check.name for check in facts[0].checks) == ("build",)
    assert facts[1].checks == ()
    assert client.graphql.call_count == 3
    assert client.graphql.call_args_list[1].args[1] == {
        "organization": "hmcts",
        "repository": "cath-service",
        "number": 11,
        "cursor": "review-page-2",
    }
    assert client.graphql.call_args_list[2].args[1]["cursor"] == "pr-page-2"


def test_collect_merged_pull_requests_follows_an_overflowing_status_check_rollup() -> None:
    """Request further rollup pages only after a bounded check connection overflows."""
    client = MagicMock(spec=GitHubClient)
    client.graphql.side_effect = [
        {
            "search": {
                "issueCount": 1,
                "pageInfo": page(),
                "nodes": [
                    {
                        "databaseId": 101,
                        "number": 11,
                        "createdAt": "2026-07-01T00:00:00Z",
                        "mergedAt": "2026-07-03T00:00:00Z",
                        "isDraft": False,
                        "author": {"login": "author", "__typename": "User"},
                        "reviews": {"pageInfo": page(), "nodes": []},
                        "commits": rollup(
                            check_run("build", "SUCCESS", "2026-07-02T23:00:00Z"),
                            has_next_page=True,
                            end_cursor="check-page-2",
                        ),
                    },
                ],
            },
        },
        {
            "repository": {
                "pullRequest": {
                    "commits": rollup(
                        {
                            "__typename": "StatusContext",
                            "context": "legacy-status",
                            "state": "FAILURE",
                            "createdAt": "2026-07-02T22:00:00Z",
                        },
                    ),
                },
            },
        },
    ]

    facts = collect_merged_pull_requests(
        client,
        "hmcts",
        "cath-service",
        datetime(2026, 7, 1, tzinfo=UTC),
        datetime(2026, 7, 31, tzinfo=UTC),
    )

    assert tuple(check.name for check in facts[0].checks) == ("build", "legacy-status")
    assert facts[0].checks[1].conclusion is CheckConclusion.FAILURE
    assert facts[0].checks[1].completed_at == datetime(2026, 7, 2, 22, tzinfo=UTC)
    assert client.graphql.call_args_list[1].args[1]["cursor"] == "check-page-2"


def test_check_fact_keeps_an_unfinished_check_distinct_from_a_failure() -> None:
    """Leave a running check without a conclusion so it cannot be read as a completed failure."""
    running = check_fact(
        GitHubCheckContext.model_validate(check_run("slow", None, None, status="IN_PROGRESS")),
    )
    pending_status = check_fact(
        GitHubCheckContext.model_validate(
            {
                "__typename": "StatusContext",
                "context": "queued",
                "state": "PENDING",
                "createdAt": "2026-07-02T22:00:00Z",
            },
        ),
    )

    assert running.conclusion is None
    assert running.completed_at is None
    assert pending_status.conclusion is None
    assert pending_status.completed_at is None


@pytest.mark.parametrize(
    ("parser", "payload", "message"),
    [
        (parse_search, {}, "invalid pull-request behaviour data"),
        (parse_reviews, {}, "invalid review behaviour data"),
        (parse_reviews, {"repository": {"pullRequest": None}}, "omitted a pull request"),
        (parse_checks, {}, "invalid status-check data"),
        (parse_checks, {"repository": {"pullRequest": None}}, "omitted a pull request"),
        (parse_checks, {"repository": {"pullRequest": {"commits": {"nodes": []}}}}, "omitted a pull request"),
    ],
)
def test_behaviour_parsers_reject_missing_payloads(
    parser: object,
    payload: dict[str, object],
    message: str,
) -> None:
    """Reject incomplete GitHub behaviour payloads rather than treating them as empty evidence."""
    with pytest.raises(GitHubError, match=message):
        parser(payload)  # type: ignore[operator]


def test_collect_merged_pull_requests_rejects_truncated_search() -> None:
    """Report incomplete history when a date shard exceeds GitHub's search cap."""
    client = MagicMock(spec=GitHubClient)
    client.graphql.return_value = {
        "search": {
            "issueCount": 1001,
            "pageInfo": page(),
            "nodes": [],
        },
    }
    starts_at = datetime(2026, 7, 1, tzinfo=UTC)
    ends_at = datetime(2026, 7, 2, tzinfo=UTC)

    with pytest.raises(GitHubError, match="1,000-result limit") as captured:
        collect_merged_pull_requests(client, "hmcts", "cath-service", starts_at, ends_at)

    assert captured.value.reason is AvailabilityReason.INCOMPLETE_HISTORY


def configuration(database: Path = Path("metrics.sqlite3")) -> Configuration:
    """Build a minimal behaviour collection configuration."""
    return Configuration(
        version=1,
        organization="hmcts",
        database=database,
        teams=(
            TeamConfiguration(
                identifier="civil",
                display_name="Civil",
                repositories=("cath-service",),
            ),
        ),
    )


def repository_item(name: str = "cath-service", team_identifier: str = "civil") -> RepositoryInventoryItem:
    """Build one repository inventory item for behaviour result tests."""
    observed_at = datetime(2026, 8, 1, tzinfo=UTC)
    return RepositoryInventoryItem(
        team_identifier=team_identifier,
        repository=RepositoryMetadata(
            name=name,
            default_branch="master",
            archived=False,
            fork=False,
            disabled=False,
            created_at=observed_at,
            updated_at=observed_at,
            pushed_at=observed_at,
        ),
    )


def test_synchronize_pull_request_facts_reuses_stable_history_and_refreshes_mutable_edge(tmp_path: Path) -> None:
    """Fetch stable history once while refreshing only the configured mutable edge on a repeated run."""
    client = MagicMock(spec=GitHubClient)
    client.graphql.return_value = {
        "search": {
            "issueCount": 0,
            "pageInfo": page(),
            "nodes": [],
        },
    }
    ends_at = datetime(2026, 7, 11, tzinfo=UTC)
    window = ReportingWindow(starts_at=datetime(2026, 7, 1, tzinfo=UTC), ends_at=ends_at)
    configured = configuration(tmp_path / "metrics.sqlite3")

    first_facts, first = synchronize_pull_request_facts(configured, client, "cath-service", window, ends_at)
    second_facts, second = synchronize_pull_request_facts(configured, client, "cath-service", window, ends_at)

    assert first_facts == second_facts
    assert cache_status(first, window) is CacheStatus.FETCHED
    assert len(first) == 1
    assert cache_status(second, window) is CacheStatus.FULLY_REUSED
    assert second == ()
    assert mutable_edge(configured, window, ends_at) == datetime(2026, 7, 10, 18, tzinfo=UTC)
    assert client.graphql.call_count == 3
    assert (
        "merged:2026-07-01T00:00:00Z..2026-07-10T18:00:00Z" in client.graphql.call_args_list[0].args[1]["searchQuery"]
    )
    assert all(
        "merged:2026-07-10T18:00:00Z..2026-07-11T00:00:00Z" in call.args[1]["searchQuery"]
        for call in client.graphql.call_args_list[1:]
    )


def test_synchronize_pull_request_facts_reports_partial_reuse_for_rolling_window(tmp_path: Path) -> None:
    """Report partial reuse when a later run promotes part of the former mutable edge."""
    client = MagicMock(spec=GitHubClient)
    client.graphql.return_value = {
        "search": {
            "issueCount": 0,
            "pageInfo": page(),
            "nodes": [],
        },
    }
    configured = configuration(tmp_path / "metrics.sqlite3")
    synchronize_pull_request_facts(
        configured,
        client,
        "cath-service",
        ReportingWindow(starts_at=datetime(2026, 7, 1, tzinfo=UTC), ends_at=datetime(2026, 7, 11, tzinfo=UTC)),
        datetime(2026, 7, 11, tzinfo=UTC),
    )

    window = ReportingWindow(starts_at=datetime(2026, 7, 1, tzinfo=UTC), ends_at=datetime(2026, 7, 12, tzinfo=UTC))
    _, intervals = synchronize_pull_request_facts(
        configured,
        client,
        "cath-service",
        window,
        datetime(2026, 7, 12, tzinfo=UTC),
    )

    assert cache_status(intervals, window) is CacheStatus.PARTIALLY_REUSED
    assert len(intervals) == 1


def test_collect_window_preserves_repository_failure_as_partial(tmp_path: Path) -> None:
    """Keep a repository and classify its unavailable behaviour evidence explicitly."""
    inventory = RepositoryInventory(
        status=CollectionStatus.COMPLETE,
        organization="hmcts",
        collected_at=datetime(2026, 8, 1, tzinfo=UTC),
        starts_at=datetime(2026, 7, 1, tzinfo=UTC),
        ends_at=datetime(2026, 8, 1, tzinfo=UTC),
        repositories=(repository_item(),),
        failures=(),
    )
    client = MagicMock(spec=GitHubClient, requests_issued=0)
    client.graphql.side_effect = GitHubError("GitHub permission denied", AvailabilityReason.PERMISSION_DENIED)

    result = collect_window(
        configuration(tmp_path / "metrics.sqlite3"),
        client,
        inventory,
        ReportingWindow(starts_at=datetime(2026, 7, 1, tzinfo=UTC), ends_at=datetime(2026, 8, 1, tzinfo=UTC)),
        datetime(2026, 8, 1, tzinfo=UTC),
    )

    assert result.status is CollectionStatus.PARTIAL
    assert result.repositories[0].collection is None
    assert result.failures[0].evidence is EvidenceKind.BEHAVIOUR
    assert result.failures[0].reason is AvailabilityReason.PERMISSION_DENIED


def population_inventory(*names: str) -> RepositoryInventory:
    """Build an inventory of several repositories, which is the smallest thing isolation shows up in.

    A single-repository inventory cannot fail an isolation test: with one repository there is no
    difference between "the run stopped" and "that repository failed".
    """
    return RepositoryInventory(
        status=CollectionStatus.COMPLETE,
        organization="hmcts",
        collected_at=datetime(2026, 8, 1, tzinfo=UTC),
        starts_at=datetime(2026, 7, 1, tzinfo=UTC),
        ends_at=datetime(2026, 8, 1, tzinfo=UTC),
        repositories=tuple(repository_item(name) for name in names),
        failures=(),
    )


def population_configuration(database: Path, *names: str) -> Configuration:
    """Configure a population across two teams, mirroring how `hmcts.yml` spreads fourteen."""
    return Configuration(
        version=1,
        organization="hmcts",
        database=database,
        teams=(
            TeamConfiguration(identifier="civil", display_name="Civil", repositories=names[:1]),
            TeamConfiguration(identifier="divorce", display_name="Divorce", repositories=names[1:]),
        ),
    )


def refusing_client(refused: str, *, refuse_commits_only: bool = False) -> MagicMock:
    """Answer every repository's window emptily, refusing one of them the way a rate limit does.

    Keyed on the repository each query names rather than on call order, so the failure stays pinned
    to one repository however many calls its neighbours take.
    """
    client = MagicMock(spec=GitHubClient, requests_issued=0)

    def respond(query: str, variables: Mapping[str, object] | None = None) -> dict[str, object]:
        """Answer one GraphQL document for whichever repository asked."""
        asked = variables or {}
        commits = "defaultBranchRef" in query
        named = str(asked.get("repository") if commits else asked.get("searchQuery", ""))
        if refused in named and (commits or not refuse_commits_only):
            message = "GitHub rate limit exceeded after 3 attempts"
            raise GitHubError(message, AvailabilityReason.RATE_LIMITED)
        if commits:
            return {"repository": {"defaultBranchRef": {"target": {"history": {"pageInfo": page(), "nodes": []}}}}}
        return {"search": {"issueCount": 0, "pageInfo": page(), "nodes": []}}

    client.graphql.side_effect = respond
    return client


def cached_sources(database: Path) -> set[tuple[str, str]]:
    """Return every (repository, source) pair the cache claims settled coverage for."""
    with closing(connect(database)) as connection:
        rows = connection.execute("SELECT DISTINCT repository, source FROM source_coverage").fetchall()
    return {(repository, source) for repository, source in rows}


def test_collect_window_isolates_one_repositorys_failure_from_the_rest_of_the_population(tmp_path: Path) -> None:
    """Keep every other repository collected and cached when one of them is refused.

    The refused repository is the MIDDLE of three, so the test fails both if the run stops at the
    failure and if the repositories before it are rolled back with it.
    """
    database = tmp_path / "metrics.sqlite3"
    window = ReportingWindow(starts_at=datetime(2026, 7, 1, tzinfo=UTC), ends_at=datetime(2026, 8, 1, tzinfo=UTC))

    result = collect_window(
        population_configuration(database, "cath-service", "opal-common-lib", "nfdiv-case-api"),
        refusing_client("opal-common-lib"),
        population_inventory("cath-service", "opal-common-lib", "nfdiv-case-api"),
        window,
        datetime(2026, 8, 1, tzinfo=UTC),
    )

    assert result.status is CollectionStatus.PARTIAL
    collected = {item.repository.name: item.collection for item in result.repositories}
    assert collected["cath-service"] is not None
    assert collected["nfdiv-case-api"] is not None
    # A reason, not an empty window: the refused repository keeps the current state it did collect
    # and reports why its window is missing rather than reporting a window of nothing.
    assert collected["opal-common-lib"] is None
    assert len(result.failures) == 1
    assert result.failures[0].repository == "opal-common-lib"
    assert result.failures[0].evidence is EvidenceKind.BEHAVIOUR
    assert result.failures[0].reason is AvailabilityReason.RATE_LIMITED
    # Both sources are settled for the two that answered, and the refused repository claims neither:
    # coverage is only ever recorded for an interval GitHub actually returned.
    assert cached_sources(database) == {
        ("cath-service", "pull_request"),
        ("cath-service", "commit"),
        ("nfdiv-case-api", "pull_request"),
        ("nfdiv-case-api", "commit"),
    }


def test_collect_window_keeps_the_source_a_refused_repository_had_already_read(tmp_path: Path) -> None:
    """Keep one repository's settled pull-request history when its commit history is refused.

    Isolation is per source as well as per repository: the two are cached under separate coverage,
    so a rate limit arriving between them must not discard the half already paid for.
    """
    database = tmp_path / "metrics.sqlite3"
    window = ReportingWindow(starts_at=datetime(2026, 7, 1, tzinfo=UTC), ends_at=datetime(2026, 8, 1, tzinfo=UTC))

    result = collect_window(
        population_configuration(database, "cath-service", "opal-common-lib"),
        refusing_client("opal-common-lib", refuse_commits_only=True),
        population_inventory("cath-service", "opal-common-lib"),
        window,
        datetime(2026, 8, 1, tzinfo=UTC),
    )

    assert result.status is CollectionStatus.PARTIAL
    assert result.failures[0].repository == "opal-common-lib"
    assert cached_sources(database) == {
        ("cath-service", "pull_request"),
        ("cath-service", "commit"),
        ("opal-common-lib", "pull_request"),
    }


def test_collect_window_preserves_failed_status_without_repositories() -> None:
    """Keep a failed inventory failed when no repository is available for behaviour collection."""
    issue = RepositoryInventoryIssue(
        team_identifier="civil",
        repository="cath-service",
        evidence=EvidenceKind.REPOSITORY,
        reason=AvailabilityReason.NOT_FOUND_OR_INACCESSIBLE,
        detail="GitHub repository not found or inaccessible",
    )
    inventory = RepositoryInventory(
        status=CollectionStatus.FAILED,
        organization="hmcts",
        collected_at=datetime(2026, 8, 1, tzinfo=UTC),
        starts_at=datetime(2026, 7, 1, tzinfo=UTC),
        ends_at=datetime(2026, 8, 1, tzinfo=UTC),
        repositories=(),
        failures=(issue,),
    )

    result = collect_window(
        configuration(),
        MagicMock(spec=GitHubClient),
        inventory,
        ReportingWindow(starts_at=datetime(2026, 7, 1, tzinfo=UTC), ends_at=datetime(2026, 8, 1, tzinfo=UTC)),
        datetime(2026, 8, 1, tzinfo=UTC),
    )

    assert result.status is CollectionStatus.FAILED
    assert result.failures == (issue,)


def history_commit(
    oid: str,
    committed_at: str,
    *,
    pull_request: int | None = None,
    author: dict[str, object] | None = None,
    additions: int | None = 12,
    rollup: str | None = "SUCCESS",
) -> dict[str, object]:
    """Build one default-branch history commit."""
    return {
        "oid": oid,
        "committedDate": committed_at,
        "additions": additions,
        "deletions": 3,
        "changedFilesIfAvailable": 2,
        "author": {"user": author} if author is not None else None,
        "associatedPullRequests": {"nodes": [] if pull_request is None else [{"number": pull_request}]},
        "statusCheckRollup": None if rollup is None else {"state": rollup},
    }


def history(
    *commits: dict[str, object],
    has_next_page: bool = False,
    end_cursor: str | None = None,
) -> dict[str, object]:
    """Build one page of default-branch history."""
    return {
        "repository": {
            "defaultBranchRef": {
                "target": {
                    "history": {
                        "pageInfo": page(has_next_page=has_next_page, end_cursor=end_cursor),
                        "nodes": list(commits),
                    },
                },
            },
        },
    }


def test_collect_direct_commits_keeps_only_the_commits_no_pull_request_introduced() -> None:
    """Separate the changes that bypassed the process from the commits a merge brought in."""
    client = MagicMock(spec=GitHubClient)
    client.graphql.side_effect = [
        history(
            history_commit("aaa", "2026-07-05T00:00:00Z", author={"login": "pusher", "__typename": "User"}),
            history_commit("bbb", "2026-07-06T00:00:00Z", pull_request=11),
            has_next_page=True,
            end_cursor="history-page-2",
        ),
        # Inclusive GitHub bounds can return the window's own end instant, which is excluded here.
        history(history_commit("ccc", "2026-07-11T00:00:00Z")),
    ]

    facts = collect_direct_commits(
        client,
        "hmcts",
        "cath-service",
        datetime(2026, 7, 1, tzinfo=UTC),
        datetime(2026, 7, 11, tzinfo=UTC),
    )

    assert tuple(fact.sha for fact in facts) == ("aaa",)
    assert facts[0].author_login == "pusher"
    assert facts[0].additions == 12
    assert facts[0].changed_files == 2
    assert facts[0].check_state is CheckRollupState.SUCCESS
    assert client.graphql.call_args_list[0].args[1]["since"] == "2026-07-01T00:00:00Z"
    assert client.graphql.call_args_list[1].args[1]["cursor"] == "history-page-2"
    # One call per page of history and none per commit, which is the whole cost of the source.
    assert client.graphql.call_count == 2


def test_collect_direct_commits_keeps_a_commit_github_could_not_attribute() -> None:
    """Keep a commit GitHub could match to no account rather than dropping or misattributing it."""
    client = MagicMock(spec=GitHubClient)
    client.graphql.side_effect = [history(history_commit("aaa", "2026-07-05T00:00:00Z", rollup="FAILURE"))]

    facts = collect_direct_commits(
        client,
        "hmcts",
        "cath-service",
        datetime(2026, 7, 1, tzinfo=UTC),
        datetime(2026, 7, 11, tzinfo=UTC),
    )

    assert facts[0].author_login is None
    assert facts[0].check_state is CheckRollupState.FAILURE


def test_collect_direct_commits_reports_no_history_for_an_unbuilt_default_branch() -> None:
    """Report an empty repository as no direct commits rather than as a collection failure."""
    client = MagicMock(spec=GitHubClient)
    client.graphql.return_value = {"repository": {"defaultBranchRef": None}}

    assert (
        collect_direct_commits(
            client,
            "hmcts",
            "cath-service",
            datetime(2026, 7, 1, tzinfo=UTC),
            datetime(2026, 7, 11, tzinfo=UTC),
        )
        == ()
    )


def test_collect_direct_commits_records_a_commit_that_ran_no_checks() -> None:
    """Leave a commit CI never ran on without a state, rather than inventing a passing one."""
    client = MagicMock(spec=GitHubClient)
    client.graphql.side_effect = [history(history_commit("aaa", "2026-07-05T00:00:00Z", rollup=None))]

    facts = collect_direct_commits(
        client,
        "hmcts",
        "cath-service",
        datetime(2026, 7, 1, tzinfo=UTC),
        datetime(2026, 7, 11, tzinfo=UTC),
    )

    assert facts[0].check_state is None


@pytest.mark.parametrize(
    ("parser", "payload", "message"),
    [
        (parse_commit_history, {"repository": None}, "omitted a repository"),
        (parse_commit_history, {"repository": {"defaultBranchRef": {"target": {}}}}, "invalid default-branch history"),
    ],
)
def test_commit_parsers_reject_missing_payloads(
    parser: object,
    payload: dict[str, object],
    message: str,
) -> None:
    """Reject incomplete commit payloads rather than reading them as an unbypassed default branch."""
    with pytest.raises(GitHubError, match=message):
        parser(payload)  # type: ignore[operator]


def test_synchronize_merges_reports_both_routes_onto_the_default_branch(tmp_path: Path) -> None:
    """Cache each source independently and account for both in one collection provenance."""
    client = MagicMock(spec=GitHubClient)

    def respond(query: str, variables: dict[str, object]) -> dict[str, object]:
        """Answer whichever query the synchronisation issued."""
        _ = variables
        if "defaultBranchRef" in query:
            return history(history_commit("aaa", "2026-07-05T00:00:00Z"))
        return {"search": {"issueCount": 0, "pageInfo": page(), "nodes": []}}

    client.graphql.side_effect = respond
    window = ReportingWindow(starts_at=datetime(2026, 7, 1, tzinfo=UTC), ends_at=datetime(2026, 7, 11, tzinfo=UTC))
    configured = configuration(tmp_path / "metrics.sqlite3")

    changes, provenance = synchronize_merges(
        configured,
        client,
        "cath-service",
        window,
        datetime(2026, 7, 11, tzinfo=UTC),
    )

    assert changes.pull_requests == ()
    assert tuple(commit.sha for commit in changes.direct_commits) == ("aaa",)
    assert provenance.stable_history is CacheStatus.FETCHED
    assert provenance.stable_intervals_fetched == 1
    assert provenance.direct_commit_facts_loaded == 1
    assert provenance.direct_commit_intervals_fetched == 1


def test_open_pull_request_search_queries_bound_a_window_with_one_inclusive_range_not_two_comparisons() -> None:
    """Express the exclusive upper bound by ending GitHub's inclusive range a second early.

    Two comparisons on one qualifier are not intersected by GitHub search — only the last applies —
    so the window's end is carried by the range itself, one second inside `ends_at`.
    """
    window = ReportingWindow(starts_at=datetime(2026, 7, 1, tzinfo=UTC), ends_at=datetime(2026, 7, 11, tzinfo=UTC))
    stale_cutoff = datetime(2026, 7, 4, tzinfo=UTC)

    queries = open_pull_request_search_queries("hmcts", "cath-service", window, stale_cutoff)

    assert queries == {
        "openedQuery": "repo:hmcts/cath-service is:pr created:2026-07-01T00:00:00Z..2026-07-10T23:59:59Z",
        "closedWithoutMergeQuery": (
            "repo:hmcts/cath-service is:pr is:closed -is:merged closed:2026-07-01T00:00:00Z..2026-07-10T23:59:59Z"
        ),
        "openQuery": "repo:hmcts/cath-service is:pr is:open",
        "staleOpenQuery": "repo:hmcts/cath-service is:pr is:open updated:<2026-07-04T00:00:00Z",
    }


def test_the_open_pull_request_document_declares_exactly_the_variables_and_aliases_the_code_uses() -> None:
    """Tie the query document to the two halves that must agree with it.

    Every other test drives this collector through a mock whose `graphql` returns a hand-built dict
    and discards the document, so nothing else constrains it. Rename a variable or an alias on one
    side and the suite stays green while every real run fails — either GraphQL rejecting an undefined
    variable, or the response failing validation, which the caller degrades into a per-repository
    "not available" rather than a crash.
    """
    document = open_pull_request_query()
    window = ReportingWindow(starts_at=datetime(2026, 7, 1, tzinfo=UTC), ends_at=datetime(2026, 7, 11, tzinfo=UTC))
    queries = open_pull_request_search_queries("hmcts", "cath-service", window, datetime(2026, 7, 4, tzinfo=UTC))

    assert set(re.findall(r"\$(\w+): String!", document)) == set(queries)
    for name in queries:
        assert f"query: ${name}" in document
    assert {field.alias for field in GitHubOpenPullRequestData.model_fields.values()} == set(
        re.findall(r"(\w+): search\(", document),
    )


def test_the_pull_request_documents_select_every_field_their_models_read() -> None:
    """Tie the two review-bearing documents to the models that parse their responses.

    Every other test in this file feeds `parse_search` and `parse_reviews` a hand-built dict, so a
    selection deleted from the document leaves the suite green. The two failure modes differ and both
    are silent here: dropping `comments(first: 0) { totalCount }` makes `GitHubReview` validation
    fail on every real response, losing the whole repository's window, while dropping the optional
    `body` costs no error at all and simply reports description quality and traceability as near-zero
    for ever.
    """
    search = pull_request_query()
    continuation = review_query()
    review_selections = {field.alias or name for name, field in GitHubReview.model_fields.items()}
    pull_request_selections = {field.alias or name for name, field in GitHubPullRequest.model_fields.items()}

    for selection in review_selections:
        assert re.search(rf"\b{selection}\b", search), f"the search document stopped selecting {selection}"
        assert re.search(rf"\b{selection}\b", continuation), f"the review document stopped selecting {selection}"
    for selection in pull_request_selections:
        assert re.search(rf"\b{selection}\b", search), f"the search document stopped selecting {selection}"
    # Count-only on purpose: review depth needs how many comments an approval carried, never their text.
    assert search.count("comments(first: 0) { totalCount }") == 1
    assert continuation.count("comments(first: 0) { totalCount }") == 1


def test_collect_open_pull_request_state_counts_each_of_the_four_states() -> None:
    """Parse the bundled response's four aliased searches into the compact summary."""
    client = MagicMock(spec=GitHubClient)
    client.graphql.return_value = {
        "openedInWindow": {"issueCount": 5},
        "closedWithoutMerge": {"issueCount": 1},
        "currentlyOpen": {"issueCount": 3},
        "staleOpen": {"issueCount": 2},
    }
    window = ReportingWindow(starts_at=datetime(2026, 7, 1, tzinfo=UTC), ends_at=datetime(2026, 7, 11, tzinfo=UTC))

    summary = collect_open_pull_request_state(
        client,
        "hmcts",
        "cath-service",
        window,
        14,
        datetime(2026, 7, 15, tzinfo=UTC),
    )

    assert summary == OpenPullRequestSummary(
        opened_in_window=5,
        closed_without_merge=1,
        currently_open=3,
        stale_open=2,
    )


def test_collect_open_pull_request_state_measures_staleness_from_the_reference_not_the_window() -> None:
    """Cut the staleness boundary exactly `stale_open_days` before the reference instant, never from opening."""
    client = MagicMock(spec=GitHubClient)
    client.graphql.return_value = {
        "openedInWindow": {"issueCount": 0},
        "closedWithoutMerge": {"issueCount": 0},
        "currentlyOpen": {"issueCount": 0},
        "staleOpen": {"issueCount": 0},
    }
    window = ReportingWindow(starts_at=datetime(2026, 7, 1, tzinfo=UTC), ends_at=datetime(2026, 7, 11, tzinfo=UTC))

    collect_open_pull_request_state(client, "hmcts", "cath-service", window, 14, datetime(2026, 7, 15, tzinfo=UTC))

    _, variables = client.graphql.call_args.args
    assert variables["staleOpenQuery"] == "repo:hmcts/cath-service is:pr is:open updated:<2026-07-01T00:00:00Z"


def test_parse_open_pull_request_state_rejects_an_invalid_payload() -> None:
    """Reject a malformed bundled response rather than reading it as zero counts."""
    with pytest.raises(GitHubError, match="invalid open pull-request data"):
        parse_open_pull_request_state({"openedInWindow": {}})


class CountingClient(GitHubClient):
    """Count and refuse every GraphQL call, as a repository without pull-request access does.

    A subclass rather than a mock because the cost meter reads the real counter: `issued` is what
    `requests_issued` exposes, so incrementing it here is exactly what a live call would do.
    """

    def graphql(self, query: str, variables: Mapping[str, object] | None = None) -> dict[str, object]:
        """Count one issued call and refuse it."""
        del query, variables
        self.issued += 1
        message = "GitHub permission denied"
        raise GitHubError(message, AvailabilityReason.PERMISSION_DENIED)


def measured_clock(*readings: float) -> Callable[[], float]:
    """Return a clock answering the given monotonic readings in order."""
    remaining = iter(readings)
    return lambda: next(remaining)


def test_collect_window_adds_what_the_window_cost_to_what_current_state_cost(tmp_path: Path) -> None:
    """Report what a repository cost, not what one phase of collecting it cost."""
    inventory = RepositoryInventory(
        status=CollectionStatus.COMPLETE,
        organization="hmcts",
        collected_at=datetime(2026, 8, 1, tzinfo=UTC),
        starts_at=datetime(2026, 7, 1, tzinfo=UTC),
        ends_at=datetime(2026, 8, 1, tzinfo=UTC),
        repositories=(repository_item(),),
        failures=(),
        costs=(RepositoryCollectionCost(repository="cath-service", requests=6, elapsed_seconds=1.5),),
    )

    with patch("metrics.cost.monotonic", measured_clock(10.0, 12.5)):
        result = collect_window(
            configuration(tmp_path / "metrics.sqlite3"),
            CountingClient(PersonalAccessToken("secret"), Session()),
            inventory,
            ReportingWindow(starts_at=datetime(2026, 7, 1, tzinfo=UTC), ends_at=datetime(2026, 8, 1, tzinfo=UTC)),
            datetime(2026, 8, 1, tzinfo=UTC),
        )

    # The window failed on its first call, and the attempt still cost that call and those seconds.
    assert result.status is CollectionStatus.PARTIAL
    assert result.costs == (RepositoryCollectionCost(repository="cath-service", requests=7, elapsed_seconds=4.0),)


def progress_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Return the progress lines the phase logged, with the elapsed figure normalised.

    The seconds are replaced rather than matched: a mocked window takes microseconds, and a test
    that asserted the reading itself would be asserting the speed of the machine it runs on.
    """
    return [re.sub(r"\d+\.\ds\)$", "0.0s)", message) for message in caplog.messages if message.startswith("[")]


def test_collect_window_logs_one_progress_line_per_repository(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Say where the window phase has reached, how each window was satisfied and what it cost."""
    database = tmp_path / "metrics.sqlite3"
    window = ReportingWindow(starts_at=datetime(2026, 7, 1, tzinfo=UTC), ends_at=datetime(2026, 8, 1, tzinfo=UTC))

    with caplog.at_level("INFO"):
        collect_window(
            population_configuration(database, "cath-service", "opal-common-lib"),
            refusing_client("no-repository-of-this-name"),
            population_inventory("cath-service", "opal-common-lib"),
            window,
            datetime(2026, 8, 1, tzinfo=UTC),
        )

    assert progress_lines(caplog) == [
        "[1/2] cath-service  window fetched 2 intervals (0 calls, 0.0s)",
        "[2/2] opal-common-lib  window fetched 2 intervals (0 calls, 0.0s)",
    ]


def test_collect_window_progress_reports_a_window_taken_entirely_from_the_cache(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Report a second run over a settled window as reuse, so a reader knows it paid nothing."""
    database = tmp_path / "metrics.sqlite3"
    window = ReportingWindow(starts_at=datetime(2026, 7, 1, tzinfo=UTC), ends_at=datetime(2026, 8, 1, tzinfo=UTC))
    arguments = (
        population_configuration(database, "cath-service", "opal-common-lib"),
        refusing_client("no-repository-of-this-name"),
        population_inventory("cath-service", "opal-common-lib"),
        window,
        datetime(2026, 8, 1, tzinfo=UTC),
    )
    collect_window(*arguments)

    with caplog.at_level("INFO"):
        collect_window(*arguments)

    assert progress_lines(caplog) == [
        "[1/2] cath-service  window fully reused (0 calls, 0.0s)",
        "[2/2] opal-common-lib  window fully reused (0 calls, 0.0s)",
    ]


def test_collect_window_progress_reports_why_a_refused_window_is_missing(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Name the reason on the line, so a refusal is visible without reading the report."""
    window = ReportingWindow(starts_at=datetime(2026, 7, 1, tzinfo=UTC), ends_at=datetime(2026, 8, 1, tzinfo=UTC))

    with caplog.at_level("INFO"):
        collect_window(
            population_configuration(tmp_path / "metrics.sqlite3", "cath-service", "opal-common-lib"),
            refusing_client("opal-common-lib"),
            population_inventory("cath-service", "opal-common-lib"),
            window,
            datetime(2026, 8, 1, tzinfo=UTC),
        )

    assert progress_lines(caplog)[1] == "[2/2] opal-common-lib  window unavailable: rate limited (0 calls, 0.0s)"


def test_window_progress_counts_the_intervals_of_both_windowed_sources() -> None:
    """Report a window that reused every pull-request interval and still paid for a commit one.

    `stable_history` alone would call this fully reused, which would tell a reader the window cost
    nothing while the figures beside it said otherwise.
    """
    provenance = BehaviourProvenance(
        stable_history=CacheStatus.FULLY_REUSED,
        stable_intervals_fetched=0,
        mutable_starts_at=datetime(2026, 7, 25, tzinfo=UTC),
        pull_request_facts_loaded=4,
        review_facts_loaded=2,
        direct_commit_intervals_fetched=1,
    )

    assert window_progress(provenance) == "window partially reused 1 interval"
