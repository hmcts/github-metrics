"""Test configured repository inventory collection."""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
from pydantic import ValidationError
from requests import Response, Session
from requests.exceptions import JSONDecodeError as RequestsJSONDecodeError

from metrics.config import Configuration, TeamConfiguration
from metrics.domain import (
    AlertSeverity,
    AvailabilityReason,
    CollectionStatus,
    EvidenceKind,
    MergeGateEvidence,
    OpenAlertCount,
    ReportingWindow,
    RepositoryInventoryIssue,
    RepositoryMetadata,
)
from metrics.github import GitHubClient, GitHubError
from metrics.inventory import (
    BypassActor,
    Ruleset,
    binds_administrators,
    bypasses_as_administrator,
    collect_inventory,
    collect_merge_gate,
    collect_repository,
    collect_rulesets,
    collect_security_alerts,
    count_by_severity,
    enforcing_rules,
    fetch_ruleset,
)
from metrics.inventory import RepositoryRule as InventoryRule


def collection_window() -> ReportingWindow:
    """Build the collection window an inventory run resolves before collecting."""
    return ReportingWindow(
        starts_at=datetime(2026, 5, 1, tzinfo=UTC),
        ends_at=datetime(2026, 8, 1, tzinfo=UTC),
    )


def test_collect_repository_preserves_unavailable_reason() -> None:
    """Keep transport failure classifications in inventory evidence."""
    client = GitHubClient("secret", Session())
    error = GitHubError("GitHub permission denied", AvailabilityReason.PERMISSION_DENIED)
    with patch.object(client, "get_repository", side_effect=error):
        result = collect_repository(client, "hmcts", "divorce", "nfdiv-case-api")

    assert isinstance(result, RepositoryInventoryIssue)
    assert result.evidence is EvidenceKind.REPOSITORY
    assert result.reason is AvailabilityReason.PERMISSION_DENIED
    assert result.detail == "GitHub permission denied"


def test_collect_repository_returns_typed_metadata() -> None:
    """Parse repository inventory fields from GitHub JSON."""
    client = GitHubClient("secret", Session())
    response = MagicMock()
    response.json.return_value = {
        "name": "nfdiv-case-api",
        "default_branch": "master",
        "archived": False,
        "fork": False,
        "disabled": False,
        "created_at": "2020-01-01T00:00:00Z",
        "updated_at": "2026-08-01T12:00:00Z",
        "pushed_at": "2026-08-01T11:00:00Z",
        "url": "https://api.github.com/repos/hmcts/nfdiv-case-api",
    }
    with patch.object(client, "get_repository", return_value=response):
        result = collect_repository(client, "hmcts", "divorce", "nfdiv-case-api")

    assert not isinstance(result, RepositoryInventoryIssue)
    assert result.repository.default_branch == "master"
    assert result.repository.updated_at.isoformat() == "2026-08-01T12:00:00+00:00"


@pytest.mark.parametrize(
    ("side_effect", "detail"),
    [
        ({}, "name: Field required"),
        (RequestsJSONDecodeError("invalid", "{", 1), "invalid repository JSON at line 1, column 2"),
    ],
)
def test_collect_repository_rejects_invalid_payload(side_effect: object, detail: str) -> None:
    """Classify malformed repository payloads as collection failures."""
    client = GitHubClient("secret", Session())
    response = MagicMock()
    if isinstance(side_effect, Exception):
        response.json.side_effect = side_effect
    else:
        response.json.return_value = side_effect
    with patch.object(client, "get_repository", return_value=response):
        result = collect_repository(client, "hmcts", "divorce", "nfdiv-case-api")

    assert isinstance(result, RepositoryInventoryIssue)
    assert result.reason is AvailabilityReason.COLLECTION_FAILED
    assert detail in result.detail


def test_collect_merge_gate_preserves_unavailable_reason() -> None:
    """Keep branch-rule failure classifications separate from repository evidence."""
    client = GitHubClient("secret", Session())
    metadata = repository_metadata()
    error = GitHubError("GitHub permission denied", AvailabilityReason.PERMISSION_DENIED)
    with patch.object(client, "get_paginated", side_effect=error):
        result = collect_merge_gate(client, "hmcts", "divorce", metadata)

    assert result.evidence is None
    assert result.failure is not None
    assert result.failure.evidence is EvidenceKind.MERGE_GATE
    assert result.failure.reason is AvailabilityReason.PERMISSION_DENIED


def test_collect_merge_gate_returns_effective_branch_rules() -> None:
    """Parse active merge rules applied to the selected branch."""
    client = GitHubClient("secret", Session())
    records = (
        {
            "type": "pull_request",
            "parameters": {
                "dismiss_stale_reviews_on_push": True,
                "require_code_owner_review": True,
                "require_last_push_approval": False,
                "required_approving_review_count": 2,
                "required_review_thread_resolution": True,
                "required_reviewers": [],
                "allowed_merge_methods": ["squash"],
            },
        },
        {
            "type": "required_status_checks",
            "parameters": {
                "strict_required_status_checks_policy": True,
                "required_status_checks": [{"context": "build", "integration_id": 42, "future_field": True}],
                "do_not_enforce_on_create": False,
            },
        },
        {"type": "deletion"},
        {"type": "non_fast_forward"},
    )
    repository = repository_metadata().model_copy(update={"default_branch": "feature/default"})
    with patch.object(client, "get_paginated", return_value=records) as get_paginated:
        result = collect_merge_gate(client, "hmcts", "divorce", repository)

    assert isinstance(result.evidence, MergeGateEvidence)
    assert result.failure is None
    assert result.evidence.pull_requests[0].required_approving_review_count == 2
    assert result.evidence.status_checks[0].required_status_checks[0].context == "build"
    assert result.evidence.restricts_deletions
    assert result.evidence.blocks_force_pushes
    assert result.evidence.rules_observed
    # The four types modelled before 2026-08-15 are unaffected by the two added beside them.
    assert not result.evidence.requires_linear_history
    assert not result.evidence.restricts_branch_names
    assert result.evidence.unmodelled_rules == ()
    get_paginated.assert_called_once_with(
        "https://api.github.com/repos/hmcts/nfdiv-case-api/rules/branches/feature%2Fdefault",
    )


def test_collect_merge_gate_attributes_all_six_rule_types() -> None:
    """Attribute every rule type the 2026-08-15 survey found running on hmcts repositories.

    `opal-common-lib` and `opal-logging-service` each run six rules, and the two beyond the
    originally modelled four were reported as absent until they were collected.
    """
    client = GitHubClient("secret", Session())
    records = (
        {
            "type": "pull_request",
            "parameters": {
                "dismiss_stale_reviews_on_push": True,
                "require_code_owner_review": True,
                "require_last_push_approval": False,
                "required_approving_review_count": 1,
                "required_review_thread_resolution": True,
            },
        },
        {
            "type": "required_status_checks",
            "parameters": {
                "strict_required_status_checks_policy": False,
                "required_status_checks": [{"context": "build"}],
            },
        },
        {"type": "deletion"},
        {"type": "non_fast_forward"},
        {"type": "required_linear_history"},
        {"type": "branch_name_pattern", "parameters": {"operator": "starts_with", "pattern": "feature/"}},
    )
    with patch.object(client, "get_paginated", return_value=records):
        result = collect_merge_gate(client, "hmcts", "opal", repository_metadata())

    assert isinstance(result.evidence, MergeGateEvidence)
    assert result.failure is None
    assert result.evidence.requires_linear_history
    assert result.evidence.restricts_branch_names
    # Six attributed types leave nothing for the catch-all to name.
    assert result.evidence.unmodelled_rules == ()


def test_collect_merge_gate_names_rule_types_it_cannot_interpret() -> None:
    """Name an uninterpreted rule type rather than dropping it, sorted and deduplicated."""
    client = GitHubClient("secret", Session())
    records = (
        {"type": "tag_name_pattern"},
        {"type": "deletion"},
        {"type": "merge_queue", "parameters": {"merge_method": "SQUASH"}},
        {"type": "tag_name_pattern"},
    )
    with patch.object(client, "get_paginated", return_value=records):
        result = collect_merge_gate(client, "hmcts", "opal", repository_metadata())

    assert isinstance(result.evidence, MergeGateEvidence)
    assert result.failure is None
    # The tool's limit, named: sorted, deduplicated, and no crash on a type invented after release.
    assert result.evidence.unmodelled_rules == ("merge_queue", "tag_name_pattern")
    assert result.evidence.restricts_deletions
    assert not result.evidence.requires_linear_history


def test_collect_merge_gate_falls_back_to_branch_protection() -> None:
    """Preserve classic protection when its administrative details are inaccessible."""
    client = GitHubClient("secret", Session())
    error = GitHubError("GitHub permission denied", AvailabilityReason.PERMISSION_DENIED)
    response = MagicMock()
    response.json.return_value = {"name": "master", "protected": True}
    with (
        patch.object(client, "get_paginated", return_value=()),
        patch.object(client, "get", side_effect=[error, response]) as get,
    ):
        result = collect_merge_gate(client, "hmcts", "divorce", repository_metadata())

    assert isinstance(result.evidence, MergeGateEvidence)
    assert result.failure is not None
    assert result.evidence.protected
    assert result.evidence.pull_requests == ()
    # Empty rule arrays here mean "not disclosed", and only this flag separates that from "no rules".
    assert not result.evidence.rules_observed
    # Nobody was allowed to look, so the six rule properties stay at their defaults and say nothing.
    assert not result.evidence.requires_linear_history
    assert not result.evidence.restricts_branch_names
    assert result.evidence.unmodelled_rules == ()
    assert result.failure.reason is AvailabilityReason.PERMISSION_DENIED
    assert get.call_args_list == [
        call("https://api.github.com/repos/hmcts/nfdiv-case-api/branches/master/protection"),
        call("https://api.github.com/repos/hmcts/nfdiv-case-api/branches/master"),
    ]


@pytest.mark.parametrize(
    ("protected", "status", "failure_count"),
    [
        (True, CollectionStatus.PARTIAL, 1),
        (False, CollectionStatus.COMPLETE, 0),
    ],
)
def test_collect_inventory_reports_permission_limited_branch_protection(
    *,
    protected: bool,
    status: CollectionStatus,
    failure_count: int,
) -> None:
    """Report unavailable classic details only when basic protection is enabled."""
    configuration = Configuration(
        version=1,
        organization="hmcts",
        database=Path("metrics.sqlite3"),
        teams=(
            TeamConfiguration(
                identifier="divorce",
                display_name="Divorce",
                repositories=("nfdiv-case-api",),
            ),
        ),
    )
    client = GitHubClient("secret", Session())
    repository_response = MagicMock()
    repository_response.json.return_value = repository_metadata().model_dump(mode="json")
    permission_error = GitHubError("GitHub permission denied", AvailabilityReason.PERMISSION_DENIED)
    branch_response = MagicMock()
    branch_response.json.return_value = {"protected": protected}
    with (
        patch.object(client, "get_repository", return_value=repository_response),
        patch.object(client, "get_paginated", return_value=()),
        patch.object(client, "get", side_effect=[permission_error, branch_response]),
    ):
        inventory = collect_inventory(configuration, client, collection_window())

    assert inventory.status is status
    assert inventory.repositories[0].merge_gate is not None
    assert inventory.repositories[0].merge_gate.protected is protected
    assert len(inventory.failures) == failure_count
    if protected:
        assert inventory.failures[0].evidence is EvidenceKind.MERGE_GATE
        assert inventory.failures[0].reason is AvailabilityReason.PERMISSION_DENIED


def test_collect_merge_gate_maps_classic_branch_protection() -> None:
    """Normalise readable classic protection into effective merge rules."""
    client = GitHubClient("secret", Session())
    response = MagicMock()
    response.json.return_value = {
        "required_pull_request_reviews": {
            "dismiss_stale_reviews": False,
            "require_code_owner_reviews": True,
            "require_last_push_approval": True,
            "required_approving_review_count": 1,
        },
        "required_status_checks": {
            "strict": True,
            "contexts": ["build"],
            "checks": [{"context": "build", "app_id": 42}],
        },
        "required_conversation_resolution": {"enabled": True},
        "required_linear_history": {"enabled": False},
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
        "enforce_admins": {"enabled": False},
    }
    with (
        patch.object(client, "get_paginated", return_value=()),
        patch.object(client, "get", return_value=response) as get,
    ):
        result = collect_merge_gate(client, "hmcts", "divorce", repository_metadata())

    assert isinstance(result.evidence, MergeGateEvidence)
    assert result.failure is None
    assert result.evidence.protected
    assert result.evidence.pull_requests[0].required_approving_review_count == 1
    assert result.evidence.pull_requests[0].require_code_owner_review
    assert result.evidence.pull_requests[0].require_last_push_approval
    assert result.evidence.pull_requests[0].required_review_thread_resolution
    assert result.evidence.status_checks[0].strict_required_status_checks_policy
    assert result.evidence.status_checks[0].required_status_checks[0].context == "build"
    assert result.evidence.status_checks[0].required_status_checks[0].integration_id == 42
    assert result.evidence.restricts_deletions
    assert result.evidence.blocks_force_pushes
    assert result.evidence.applies_to_administrators is False
    assert result.evidence.rules_observed
    # Linear history is read from the classic payload's own key and is disabled here. Branch names
    # have no classic equivalent at all, and the fixed shape cannot carry an uninterpreted type, so
    # False for those two is accurate rather than a gap.
    assert not result.evidence.requires_linear_history
    assert not result.evidence.restricts_branch_names
    assert result.evidence.unmodelled_rules == ()
    get.assert_called_once_with("https://api.github.com/repos/hmcts/nfdiv-case-api/branches/master/protection")


@pytest.mark.parametrize(
    ("protection", "required"),
    [
        ({"required_linear_history": {"enabled": True}}, True),
        ({"required_linear_history": {"enabled": False}}, False),
        # Absent rather than disabled: GitHub omits the key for a repository whose protection predates
        # it, and an unread rule must report False rather than raising.
        ({}, False),
    ],
)
def test_collect_merge_gate_reads_classic_linear_history(protection: dict[str, object], *, required: bool) -> None:
    """Read linear history from classic protection rather than defaulting it to not-required.

    The assessment states "does not require a linear history" as a fact about the repository, so a
    rule GitHub discloses and this collector never looked at would put an unfalsifiable claim into an
    auditable report.
    """
    client = GitHubClient("secret", Session())
    response = MagicMock()
    response.json.return_value = {
        "required_conversation_resolution": {"enabled": True},
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
        **protection,
    }
    with (
        patch.object(client, "get_paginated", return_value=()),
        patch.object(client, "get", return_value=response),
    ):
        result = collect_merge_gate(client, "hmcts", "divorce", repository_metadata())

    assert isinstance(result.evidence, MergeGateEvidence)
    assert result.evidence.requires_linear_history is required
    assert result.evidence.rules_observed


def test_collect_merge_gate_maps_classic_contexts_without_pull_request_reviews() -> None:
    """Preserve legacy status contexts when classic checks and reviews are absent."""
    client = GitHubClient("secret", Session())
    response = MagicMock()
    response.json.return_value = {
        "required_pull_request_reviews": None,
        "required_status_checks": {"strict": False, "contexts": ["legacy"], "checks": []},
        "required_conversation_resolution": {"enabled": False},
        "allow_force_pushes": {"enabled": True},
        "allow_deletions": {"enabled": True},
    }
    with (
        patch.object(client, "get_paginated", return_value=()),
        patch.object(client, "get", return_value=response),
    ):
        result = collect_merge_gate(client, "hmcts", "divorce", repository_metadata())

    assert isinstance(result.evidence, MergeGateEvidence)
    assert result.failure is None
    assert result.evidence.pull_requests == ()
    assert result.evidence.status_checks[0].required_status_checks[0].context == "legacy"
    assert result.evidence.status_checks[0].required_status_checks[0].integration_id is None
    assert not result.evidence.restricts_deletions
    assert not result.evidence.blocks_force_pushes
    # Absent enforcement stays unknown: a gate we could not read must not look bypassable.
    assert result.evidence.applies_to_administrators is None
    # These rules WERE disclosed and require no review, which readiness must be able to veto on.
    assert result.evidence.rules_observed


def test_collect_merge_gate_treats_missing_classic_protection_as_unprotected() -> None:
    """Represent a classic protection 404 as an unprotected branch."""
    client = GitHubClient("secret", Session())
    error = GitHubError("GitHub repository not found or inaccessible", AvailabilityReason.NOT_FOUND_OR_INACCESSIBLE)
    with (
        patch.object(client, "get_paginated", return_value=()),
        patch.object(client, "get", side_effect=error),
    ):
        result = collect_merge_gate(client, "hmcts", "divorce", repository_metadata())

    assert isinstance(result.evidence, MergeGateEvidence)
    assert result.failure is None
    assert not result.evidence.protected
    # GitHub answered the question, so this is an observation and readiness can veto on it.
    assert result.evidence.rules_observed


def test_collect_merge_gate_preserves_classic_protection_failure() -> None:
    """Do not hide classic protection failures unrelated to permissions or absence."""
    client = GitHubClient("secret", Session())
    error = GitHubError("GitHub rate limit exceeded", AvailabilityReason.RATE_LIMITED)
    with (
        patch.object(client, "get_paginated", return_value=()),
        patch.object(client, "get", side_effect=error),
    ):
        result = collect_merge_gate(client, "hmcts", "divorce", repository_metadata())

    assert result.evidence is None
    assert result.failure is not None
    assert result.failure.reason is AvailabilityReason.RATE_LIMITED


@pytest.mark.parametrize(
    ("side_effect", "detail"),
    [
        ({}, "required_conversation_resolution: Field required"),
        (RequestsJSONDecodeError("invalid", "{", 1), "invalid branch JSON at line 1, column 2"),
    ],
)
def test_collect_merge_gate_rejects_invalid_classic_protection(side_effect: object, detail: str) -> None:
    """Classify malformed classic protection payloads as collection failures."""
    client = GitHubClient("secret", Session())
    response = MagicMock()
    if isinstance(side_effect, Exception):
        response.json.side_effect = side_effect
    else:
        response.json.return_value = side_effect
    with (
        patch.object(client, "get_paginated", return_value=()),
        patch.object(client, "get", return_value=response),
    ):
        result = collect_merge_gate(client, "hmcts", "divorce", repository_metadata())

    assert result.evidence is None
    assert result.failure is not None
    assert result.failure.reason is AvailabilityReason.COLLECTION_FAILED
    assert detail in result.failure.detail


def test_collect_merge_gate_rejects_invalid_branch_fallback() -> None:
    """Classify malformed permission-limited branch metadata as a collection failure."""
    client = GitHubClient("secret", Session())
    error = GitHubError("GitHub permission denied", AvailabilityReason.PERMISSION_DENIED)
    response = MagicMock()
    response.json.return_value = {}
    with (
        patch.object(client, "get_paginated", return_value=()),
        patch.object(client, "get", side_effect=[error, response]),
    ):
        result = collect_merge_gate(client, "hmcts", "divorce", repository_metadata())

    assert result.evidence is None
    assert result.failure is not None
    assert result.failure.reason is AvailabilityReason.COLLECTION_FAILED
    assert "protected: Field required" in result.failure.detail


def test_collect_merge_gate_rejects_invalid_rules() -> None:
    """Classify malformed effective branch rules as collection failures."""
    client = GitHubClient("secret", Session())
    with patch.object(client, "get_paginated", return_value=({"type": "pull_request", "parameters": {}},)):
        result = collect_merge_gate(client, "hmcts", "divorce", repository_metadata())

    assert result.evidence is None
    assert result.failure is not None
    assert result.failure.reason is AvailabilityReason.COLLECTION_FAILED
    assert "invalid branch rules" in result.failure.detail


def repository_metadata() -> RepositoryMetadata:
    """Create repository metadata for inventory tests."""
    return RepositoryMetadata(
        name="nfdiv-case-api",
        default_branch="master",
        archived=False,
        fork=False,
        disabled=False,
        created_at=datetime(2021, 2, 22, tzinfo=UTC),
        updated_at=datetime(2026, 7, 31, tzinfo=UTC),
        pushed_at=None,
    )


@pytest.mark.parametrize("status", [CollectionStatus.PARTIAL, CollectionStatus.FAILED])
def test_collect_inventory_derives_incomplete_status(status: CollectionStatus) -> None:
    """Distinguish partial evidence from a wholly failed collection."""
    configuration = Configuration(
        version=1,
        organization="hmcts",
        database=Path("metrics.sqlite3"),
        teams=(
            TeamConfiguration(
                identifier="divorce",
                display_name="Divorce",
                repositories=("nfdiv-case-api", "nfdiv-frontend"),
            ),
        ),
    )
    client = GitHubClient("secret", Session())
    error = GitHubError("GitHub permission denied", AvailabilityReason.PERMISSION_DENIED)
    metadata = repository_metadata()
    response = MagicMock()
    response.json.return_value = metadata.model_dump(mode="json")
    side_effects = [response, error] if status is CollectionStatus.PARTIAL else [error, error]
    with (
        patch.object(client, "get_repository", side_effect=side_effects),
        patch.object(client, "get_paginated", return_value=()),
        patch.object(
            client,
            "get",
            side_effect=GitHubError(
                "GitHub repository not found or inaccessible",
                AvailabilityReason.NOT_FOUND_OR_INACCESSIBLE,
            ),
        ),
    ):
        inventory = collect_inventory(configuration, client, collection_window())

    assert inventory.status is status


def alert_client(**families: object) -> MagicMock:
    """Build a client answering each alert family with records or an error, keyed by endpoint."""
    client = MagicMock(spec=GitHubClient)
    client.api_url = GitHubClient.api_url

    def paginated(url: str, parameters: dict[str, str | int] | None = None) -> tuple[dict[str, object], ...]:
        """Answer one alert request from the family its URL names."""
        del parameters
        family = url.rsplit("/", 2)[-2]
        answer = families[family.replace("-", "_")]
        if isinstance(answer, Exception):
            raise answer
        return tuple(answer)  # type: ignore[arg-type]

    client.get_paginated.side_effect = paginated
    return client


def test_collect_security_alerts_counts_open_dependabot_alerts_by_severity() -> None:
    """Group Dependabot alerts by the severity their advisory carries."""
    client = alert_client(
        dependabot=[
            {"number": 1, "security_advisory": {"severity": "critical"}},
            {"number": 2, "security_advisory": {"severity": "high"}},
            {"number": 3, "security_advisory": {"severity": "high"}},
        ],
        code_scanning=[],
        secret_scanning=[],
    )

    alerts = collect_security_alerts(client, "hmcts", "divorce", "cath-service").evidence

    assert alerts.dependabot.open == 3
    assert alerts.dependabot.by_severity == {AlertSeverity.CRITICAL: 1, AlertSeverity.HIGH: 2}


def test_collect_security_alerts_requests_only_open_alerts() -> None:
    """Ask each family for open alerts alone: this is current state, not resolution history."""
    client = alert_client(dependabot=[], code_scanning=[], secret_scanning=[])

    collect_security_alerts(client, "hmcts", "divorce", "cath-service")

    assert client.get_paginated.call_count == 3
    for made in client.get_paginated.call_args_list:
        assert made.args[1] == {"state": "open"}


def test_collect_security_alerts_asks_each_family_its_own_endpoint() -> None:
    """Pin the three endpoint paths, which nothing else in the suite constrains.

    A mistyped path yields a 404, which becomes `not available` on the family for every repository —
    indistinguishable in the report from a token that lacks the permission, so the defect would be
    diagnosed as an access problem and never found.
    """
    client = alert_client(dependabot=[], code_scanning=[], secret_scanning=[])

    collect_security_alerts(client, "hmcts", "divorce", "cath-service")

    assert [made.args[0] for made in client.get_paginated.call_args_list] == [
        "https://api.github.com/repos/hmcts/cath-service/dependabot/alerts",
        "https://api.github.com/repos/hmcts/cath-service/code-scanning/alerts",
        "https://api.github.com/repos/hmcts/cath-service/secret-scanning/alerts",
    ]


def test_collect_security_alerts_counts_an_ungraded_code_scanning_alert_without_grading_it() -> None:
    """Count a non-security code-scanning rule as open, and refuse to invent a severity for it.

    CodeQL's quality suites raise alerts whose rule carries no `security_severity_level`. They are
    open alerts, so they belong in the total, but grading them would assert what GitHub does not.
    """
    client = alert_client(
        dependabot=[],
        code_scanning=[
            {"number": 1, "rule": {"security_severity_level": "medium"}},
            {"number": 2, "rule": {"security_severity_level": None}},
            {"number": 3, "rule": None},
        ],
        secret_scanning=[],
    )

    alerts = collect_security_alerts(client, "hmcts", "divorce", "cath-service").evidence

    assert alerts.code_scanning.open == 3
    assert alerts.code_scanning.by_severity == {AlertSeverity.MEDIUM: 1}


def test_collect_security_alerts_never_grades_a_secret_scanning_alert() -> None:
    """Count leaked secrets without a severity axis, because GitHub's API asserts none.

    Treating every secret as critical would rank a leaked test fixture alongside a live production
    key. Ruled 2026-08-14; see architecture.md.
    """
    client = alert_client(
        dependabot=[],
        code_scanning=[],
        secret_scanning=[{"number": 1, "secret_type": "aws_access_key_id"}, {"number": 2}],
    )

    alerts = collect_security_alerts(client, "hmcts", "divorce", "cath-service").evidence

    assert alerts.secret_scanning.open == 2
    assert alerts.secret_scanning.by_severity == {}


def test_collect_security_alerts_lets_one_refused_family_leave_the_others_readable() -> None:
    """Report a refusal per family: three endpoints, three permissions, one says nothing about another."""
    client = alert_client(
        dependabot=GitHubError("GitHub permission denied", AvailabilityReason.PERMISSION_DENIED),
        code_scanning=[{"number": 1, "rule": {"security_severity_level": "high"}}],
        secret_scanning=[],
    )

    alerts = collect_security_alerts(client, "hmcts", "divorce", "cath-service").evidence

    assert alerts.dependabot.open is None
    assert alerts.dependabot.detail == "GitHub permission denied"
    assert alerts.code_scanning.open == 1
    assert alerts.secret_scanning.open == 0


def test_a_refused_alert_family_cannot_be_built_carrying_counts() -> None:
    """Reject a refusal that also claims counts, so unreadable can never render as a clean result.

    The distinction matters at the boundary: an unreadable family and a family with nothing open
    both have no severities to show, and only the model stops the first being written as the second.
    """
    with pytest.raises(ValidationError, match="cannot carry severity counts"):
        OpenAlertCount(detail="GitHub permission denied", by_severity={AlertSeverity.HIGH: 2})

    with pytest.raises(ValidationError, match="either a count or the reason"):
        OpenAlertCount(open=1, detail="GitHub permission denied")

    with pytest.raises(ValidationError, match="either a count or the reason"):
        OpenAlertCount()


def test_collect_security_alerts_reports_invalid_records_rather_than_raising() -> None:
    """Describe a family whose records did not parse instead of failing the whole collection."""
    client = alert_client(
        dependabot=[{"security_advisory": {"severity": 5}}],
        code_scanning=[],
        secret_scanning=[],
    )

    alerts = collect_security_alerts(client, "hmcts", "divorce", "cath-service").evidence

    assert alerts.dependabot.open is None
    assert alerts.dependabot.detail is not None
    assert alerts.dependabot.detail.startswith("GitHub returned invalid dependabot/alerts records")


def test_collect_security_alerts_records_a_failure_for_every_refused_family() -> None:
    """Record a refused family as a collection failure, not only as a reason on the block.

    The exit status is computed from `failures`, and the three alert permissions do not travel with
    a ruleset migration: without this, a population that answered for every repository while
    withholding every alert family would exit `0` as though the security block were complete.
    """
    client = alert_client(
        dependabot=GitHubError("GitHub permission denied", AvailabilityReason.PERMISSION_DENIED),
        code_scanning=[{"number": 1, "rule": {"security_severity_level": "high"}}],
        secret_scanning=GitHubError("GitHub rate limit exceeded", AvailabilityReason.RATE_LIMITED),
    )

    result = collect_security_alerts(client, "hmcts", "divorce", "cath-service")

    assert [(issue.evidence, issue.reason, issue.detail) for issue in result.failures] == [
        (EvidenceKind.SECURITY, AvailabilityReason.PERMISSION_DENIED, "dependabot/alerts: GitHub permission denied"),
        (EvidenceKind.SECURITY, AvailabilityReason.RATE_LIMITED, "secret-scanning/alerts: GitHub rate limit exceeded"),
    ]
    assert all(issue.repository == "cath-service" for issue in result.failures)
    assert all(issue.team_identifier == "divorce" for issue in result.failures)
    assert result.evidence.code_scanning.open == 1


def test_collect_security_alerts_records_no_failure_when_every_family_answers() -> None:
    """Leave `failures` empty when all three families answered, however few alerts they held."""
    client = alert_client(dependabot=[], code_scanning=[], secret_scanning=[])

    assert collect_security_alerts(client, "hmcts", "divorce", "cath-service").failures == ()


def test_collect_security_alerts_treats_a_disabled_family_as_an_observation_not_a_failure() -> None:
    """Record a family GitHub answered `404` for as switched off rather than as a refused one.

    The repository's metadata was read with this same token moments earlier, so a `404` here says
    the feature is not enabled, not that the repository is missing. Recording it as a failure would
    exit `3` for every repository that simply does not use the feature — most of them — and would
    tell the reader a permission problem exists where none does.
    """
    client = alert_client(
        dependabot=[{"security_advisory": {"severity": "high"}}],
        code_scanning=GitHubError(
            "GitHub repository not found or inaccessible", AvailabilityReason.NOT_FOUND_OR_INACCESSIBLE
        ),
        secret_scanning=GitHubError(
            "GitHub repository not found or inaccessible", AvailabilityReason.NOT_FOUND_OR_INACCESSIBLE
        ),
    )

    result = collect_security_alerts(client, "hmcts", "divorce", "cath-service")

    assert result.failures == ()
    assert result.evidence.code_scanning.open is None
    assert result.evidence.code_scanning.detail == "code-scanning/alerts is not enabled for this repository"
    assert result.evidence.secret_scanning.detail == "secret-scanning/alerts is not enabled for this repository"
    assert result.evidence.dependabot.open == 1


def test_collect_security_alerts_still_records_a_refused_family_as_a_failure_beside_a_disabled_one() -> None:
    """Keep `403` a refusal even when another family is merely off, so the two never collapse.

    GitHub returns `403` both for a token without the scope and for Advanced Security being
    disabled, and only the response text tells them apart — too fragile a thing to grade a run on.
    """
    client = alert_client(
        dependabot=GitHubError("GitHub permission denied", AvailabilityReason.PERMISSION_DENIED),
        code_scanning=GitHubError(
            "GitHub repository not found or inaccessible", AvailabilityReason.NOT_FOUND_OR_INACCESSIBLE
        ),
        secret_scanning=[],
    )

    result = collect_security_alerts(client, "hmcts", "divorce", "cath-service")

    assert [(issue.reason, issue.detail) for issue in result.failures] == [
        (AvailabilityReason.PERMISSION_DENIED, "dependabot/alerts: GitHub permission denied"),
    ]


def test_collect_security_alerts_records_a_failure_for_records_that_did_not_parse() -> None:
    """Treat a family whose records did not parse as a withheld source, not a readable one."""
    client = alert_client(
        dependabot=[{"security_advisory": {"severity": 5}}],
        code_scanning=[],
        secret_scanning=[],
    )

    result = collect_security_alerts(client, "hmcts", "divorce", "cath-service")

    assert [issue.reason for issue in result.failures] == [AvailabilityReason.COLLECTION_FAILED]


def test_count_by_severity_drops_a_grading_this_tool_cannot_name() -> None:
    """Ignore an unknown severity rather than mapping it onto the nearest known one.

    GitHub adding a severity should leave the counts this tool understands correct, not silently
    reclassify the new one into a neighbour and misreport both.
    """
    counts = count_by_severity(("critical", "moderate", None, "HIGH"))

    assert counts == {AlertSeverity.CRITICAL: 1, AlertSeverity.HIGH: 1}


def test_count_by_severity_orders_counts_worst_first() -> None:
    """Order the mapping by severity so a reader meets the worst grading first."""
    counts = count_by_severity(("low", "critical", "medium", "high"))

    assert tuple(counts) == (AlertSeverity.CRITICAL, AlertSeverity.HIGH, AlertSeverity.MEDIUM, AlertSeverity.LOW)


def measured_clock(*readings: float) -> Callable[[], float]:
    """Return a clock answering the given monotonic readings in order.

    A fake so that elapsed figures are chosen by the test rather than by the machine it runs on.
    Two readings are taken per repository: one before its collection and one after.
    """
    remaining = iter(readings)
    return lambda: next(remaining)


def two_repository_configuration() -> Configuration:
    """Configure the smallest population that can show one repository costing more than another."""
    return Configuration(
        version=1,
        organization="hmcts",
        database=Path("metrics.sqlite3"),
        teams=(
            TeamConfiguration(
                identifier="divorce",
                display_name="Divorce",
                repositories=("nfdiv-case-api", "nfdiv-frontend"),
            ),
        ),
    )


def unprotected_repository_responses(name: str) -> list[object]:
    """Build every response one unprotected repository's current-state collection asks for."""
    metadata = MagicMock(status_code=200)
    metadata.json.return_value = repository_metadata().model_copy(update={"name": name}).model_dump(mode="json")
    rules = MagicMock(status_code=200, links={})
    rules.json.return_value = []
    protection = Response()
    protection.status_code = 404
    alerts = []
    for _ in range(3):
        family = MagicMock(status_code=200, links={})
        family.json.return_value = []
        alerts.append(family)
    return [metadata, rules, protection, *alerts]


def test_collect_inventory_reports_what_each_repository_cost() -> None:
    """Count the calls and the seconds each repository needed, slowest first."""
    session = Session()
    client = GitHubClient("secret", session, pause=MagicMock())
    responses = unprotected_repository_responses("nfdiv-case-api") + unprotected_repository_responses("nfdiv-frontend")
    with (
        patch.object(session, "get", side_effect=responses),
        patch("metrics.cost.monotonic", measured_clock(100.0, 101.5, 200.0, 209.0)),
    ):
        inventory = collect_inventory(two_repository_configuration(), client, collection_window())

    assert inventory.status is CollectionStatus.COMPLETE
    # Slowest first, which is not the configured order: the point of the figure is to name the
    # repository that dominates a run.
    assert tuple(cost.repository for cost in inventory.costs) == ("nfdiv-frontend", "nfdiv-case-api")
    assert tuple(cost.elapsed_seconds for cost in inventory.costs) == (9.0, 1.5)
    # One repository read, one rules page, one protection read and one page per alert family.
    assert tuple(cost.requests for cost in inventory.costs) == (6, 6)
    assert client.requests_issued == 12


def test_collect_inventory_keys_a_renamed_repository_s_cost_on_the_name_github_answered_with() -> None:
    """Meter against GitHub's canonical name, which is what the window phase adds its readings to.

    GitHub follows a rename, so a repository renamed since the configuration was written answers to
    its new name. Keying this phase on the configured name and the window phase on the canonical one
    would split one repository's cost across two rows of the same report, each understating it.
    """
    session = Session()
    client = GitHubClient("secret", session, pause=MagicMock())
    configuration = two_repository_configuration().model_copy(
        update={
            "teams": (
                TeamConfiguration(
                    identifier="divorce",
                    display_name="Divorce",
                    repositories=("nfdiv-case-api-old-name",),
                ),
            ),
        },
    )
    with (
        patch.object(session, "get", side_effect=unprotected_repository_responses("nfdiv-case-api")),
        patch("metrics.cost.monotonic", measured_clock(0.0, 2.0)),
    ):
        inventory = collect_inventory(configuration, client, collection_window())

    assert tuple(cost.repository for cost in inventory.costs) == ("nfdiv-case-api",)


def test_collect_inventory_reports_the_cost_of_a_repository_that_failed() -> None:
    """Report what an attempt spent even when it produced no repository at all."""
    session = Session()
    client = GitHubClient("secret", session, pause=MagicMock())
    refused = Response()
    refused.status_code = 404
    responses = [*unprotected_repository_responses("nfdiv-case-api"), refused]
    with (
        patch.object(session, "get", side_effect=responses),
        patch("metrics.cost.monotonic", measured_clock(0.0, 4.0, 10.0, 11.0)),
    ):
        inventory = collect_inventory(two_repository_configuration(), client, collection_window())

    assert inventory.status is CollectionStatus.PARTIAL
    assert tuple(item.repository.name for item in inventory.repositories) == ("nfdiv-case-api",)
    # The repository is absent from the inventory and present in the costs: an attempt that failed
    # after one refused call still spent that call.
    assert tuple(cost.repository for cost in inventory.costs) == ("nfdiv-case-api", "nfdiv-frontend")
    assert inventory.costs[1].requests == 1
    assert inventory.costs[1].elapsed_seconds == 1.0


def ruleset_response(payload: dict[str, object]) -> MagicMock:
    """Answer one ruleset request with a stored payload."""
    response = MagicMock()
    response.json.return_value = payload
    return response


def administrator_ruleset(identifier: int, enforcement: str = "active", mode: str = "always") -> dict[str, object]:
    """Build the ruleset shape agilezebra/jwt-middleware returned on 2026-08-17."""
    return {
        "id": identifier,
        "name": "quality",
        "enforcement": enforcement,
        "bypass_actors": [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": mode}],
    }


@pytest.mark.parametrize(
    ("actor", "administrator"),
    [
        (BypassActor(actor_id=5, actor_type="RepositoryRole", bypass_mode="always"), True),
        (BypassActor(actor_id=1, actor_type="OrganizationAdmin", bypass_mode="always"), True),
        # Write, maintain and every custom role are not administrators.
        (BypassActor(actor_id=3, actor_type="RepositoryRole", bypass_mode="always"), False),
        # An exemption limited to pull requests still forces the branch's one route.
        (BypassActor(actor_id=5, actor_type="RepositoryRole", bypass_mode="pull_request"), False),
        (BypassActor(actor_id=42, actor_type="Integration", bypass_mode="always"), False),
        (BypassActor(actor_id=7, actor_type="Team", bypass_mode="always"), False),
    ],
)
def test_bypasses_as_administrator_counts_only_an_administrators_exemption(
    actor: BypassActor,
    *,
    administrator: bool,
) -> None:
    """Answer for administrators alone, and only where the exemption reaches the branch itself."""
    assert bypasses_as_administrator(actor) is administrator


def test_binds_administrators_reports_a_gate_administrators_can_bypass() -> None:
    """Report False where an administrator is exempt from any one of a branch's rulesets."""
    bound = Ruleset(id=1, enforcement="active")
    exempt = Ruleset.model_validate(administrator_ruleset(2))

    assert binds_administrators((bound, exempt)) is False
    assert binds_administrators((bound,)) is True


@pytest.mark.parametrize("rulesets", [(), (Ruleset(id=1, enforcement="active"), None)])
def test_binds_administrators_reports_unknown_rather_than_a_partial_verdict(
    rulesets: tuple[Ruleset | None, ...],
) -> None:
    """Keep None for a branch whose exemptions were not all readable, or not attributable at all.

    An exemption nobody was allowed to read is not an exemption that is absent, and a verdict taken
    from the rulesets that did answer would assert exactly that.
    """
    assert binds_administrators(rulesets) is None


def test_fetch_ruleset_reads_a_repository_ruleset() -> None:
    """Read a repository-owned ruleset from the repository endpoint."""
    client = GitHubClient("secret", Session())
    rule = InventoryRule(type="pull_request", ruleset_id=20375680, ruleset_source_type="Repository")
    with patch.object(client, "get", return_value=ruleset_response(administrator_ruleset(20375680))) as get:
        ruleset = fetch_ruleset(client, "agilezebra", "jwt-middleware", rule)

    assert ruleset is not None
    assert ruleset.bypass_actors[0].actor_type == "RepositoryRole"
    get.assert_called_once_with("https://api.github.com/repos/agilezebra/jwt-middleware/rulesets/20375680")


def test_fetch_ruleset_reads_an_organization_ruleset_from_the_organization() -> None:
    """Read an inherited ruleset where it is configured rather than from the repository."""
    client = GitHubClient("secret", Session())
    rule = InventoryRule(type="pull_request", ruleset_id=99, ruleset_source_type="Organization")
    with patch.object(client, "get", return_value=ruleset_response({"id": 99, "enforcement": "active"})) as get:
        assert fetch_ruleset(client, "hmcts", "nfdiv-case-api", rule) is not None

    get.assert_called_once_with("https://api.github.com/orgs/hmcts/rulesets/99")


def test_fetch_ruleset_returns_nothing_for_a_rule_naming_no_ruleset() -> None:
    """Ask for nothing where a rule carries no attribution to follow."""
    client = GitHubClient("secret", Session())
    with patch.object(client, "get") as get:
        assert fetch_ruleset(client, "hmcts", "nfdiv-case-api", InventoryRule(type="deletion")) is None

    get.assert_not_called()


@pytest.mark.parametrize(
    "failure",
    [
        GitHubError("GitHub permission denied", AvailabilityReason.PERMISSION_DENIED),
        RequestsJSONDecodeError("bad", "{", 0),
        ValidationError.from_exception_data("Ruleset", []),
    ],
)
def test_fetch_ruleset_degrades_one_field_rather_than_the_repository(
    failure: Exception,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Lose the bypass list to a refusal without losing the rules GitHub already disclosed."""
    client = GitHubClient("secret", Session())
    rule = InventoryRule(type="pull_request", ruleset_id=20375680, ruleset_source_type="Repository")
    with caplog.at_level("WARNING"), patch.object(client, "get", side_effect=failure):
        assert fetch_ruleset(client, "agilezebra", "jwt-middleware", rule) is None

    assert "Ruleset 20375680 unreadable for jwt-middleware" in caplog.text


def test_collect_rulesets_reads_each_ruleset_once_however_many_rules_it_supplies() -> None:
    """Cost one call per ruleset, not one per rule, for the ordinary many-rules-one-ruleset shape."""
    client = GitHubClient("secret", Session())
    rules = (
        InventoryRule(type="deletion", ruleset_id=20375680, ruleset_source_type="Repository"),
        InventoryRule(type="non_fast_forward", ruleset_id=20375680, ruleset_source_type="Repository"),
        InventoryRule(type="required_status_checks", ruleset_id=20375680, ruleset_source_type="Repository"),
        InventoryRule(type="pull_request", ruleset_id=20377108, ruleset_source_type="Repository"),
    )
    with patch.object(client, "get", return_value=ruleset_response(administrator_ruleset(20375680))) as get:
        resolved = collect_rulesets(client, "agilezebra", "jwt-middleware", rules)

    assert sorted(resolved) == [20375680, 20377108]
    assert get.call_count == 2


def test_enforcing_rules_drops_a_ruleset_that_only_evaluates() -> None:
    """Keep the rules that block a merge, and keep those whose ruleset could not be read.

    An `evaluate` ruleset reports a violation and lets the merge through, so counting its rules
    would report a gate nothing enforces. A ruleset that was refused is the opposite case: dropping
    it would report a gate as weaker than the rules GitHub already disclosed.
    """
    blocking = InventoryRule(type="pull_request", ruleset_id=1, ruleset_source_type="Repository")
    evaluating = InventoryRule(type="deletion", ruleset_id=2, ruleset_source_type="Repository")
    refused = InventoryRule(type="non_fast_forward", ruleset_id=3, ruleset_source_type="Repository")
    unattributed = InventoryRule(type="required_linear_history")
    rulesets: dict[int, Ruleset | None] = {
        1: Ruleset(id=1, enforcement="active"),
        2: Ruleset(id=2, enforcement="evaluate"),
        3: None,
    }

    kept = enforcing_rules((blocking, evaluating, refused, unattributed), rulesets)

    assert [rule.type for rule in kept] == ["pull_request", "non_fast_forward", "required_linear_history"]


def test_collect_merge_gate_reports_an_administrator_bypass_it_once_left_undisclosed() -> None:
    """Report the exemption agilezebra/jwt-middleware actually carries, rather than None.

    Reproduces that repository's 2026-08-17 configuration: two active rulesets over `master`, each
    exempting the repository admin role always. Every ruleset-governed repository reported
    `applies_to_administrators: null` before this, which read as a blind spot rather than a bypass.
    """
    client = GitHubClient("secret", Session())
    records = (
        {
            "type": "pull_request",
            "ruleset_id": 20377108,
            "ruleset_source_type": "Repository",
            "parameters": {
                "dismiss_stale_reviews_on_push": False,
                "require_code_owner_review": True,
                "require_last_push_approval": False,
                "required_approving_review_count": 1,
                "required_review_thread_resolution": True,
            },
        },
        {"type": "deletion", "ruleset_id": 20375680, "ruleset_source_type": "Repository"},
        {"type": "non_fast_forward", "ruleset_id": 20375680, "ruleset_source_type": "Repository"},
    )
    responses = [
        ruleset_response(administrator_ruleset(20377108)),
        ruleset_response(administrator_ruleset(20375680)),
    ]
    with (
        patch.object(client, "get_paginated", return_value=records),
        patch.object(client, "get", side_effect=responses),
    ):
        result = collect_merge_gate(client, "agilezebra", "personal", repository_metadata())

    assert isinstance(result.evidence, MergeGateEvidence)
    assert result.evidence.applies_to_administrators is False
    assert result.evidence.rules_observed
    assert result.evidence.restricts_deletions


def test_collect_merge_gate_reports_a_gate_that_binds_administrators() -> None:
    """Report True where the only exemptions are limited to pull requests or belong to nobody's admin."""
    client = GitHubClient("secret", Session())
    records = (
        {
            "type": "pull_request",
            "ruleset_id": 5,
            "ruleset_source_type": "Repository",
            "parameters": {
                "dismiss_stale_reviews_on_push": False,
                "require_code_owner_review": True,
                "require_last_push_approval": False,
                "required_approving_review_count": 1,
                "required_review_thread_resolution": True,
            },
        },
    )
    ruleset = {
        "id": 5,
        "enforcement": "active",
        "bypass_actors": [
            {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "pull_request"},
            {"actor_id": 42, "actor_type": "Integration", "bypass_mode": "always"},
        ],
    }
    with (
        patch.object(client, "get_paginated", return_value=records),
        patch.object(client, "get", return_value=ruleset_response(ruleset)),
    ):
        result = collect_merge_gate(client, "hmcts", "divorce", repository_metadata())

    assert isinstance(result.evidence, MergeGateEvidence)
    assert result.evidence.applies_to_administrators is True


def test_collect_merge_gate_falls_back_to_classic_when_every_ruleset_only_evaluates() -> None:
    """Take the classic route for a branch whose rulesets all report without blocking.

    A repository can carry rulesets and classic protection at once, as jwt-middleware does, and an
    evaluating ruleset must not hide the protection that is actually gating the branch.
    """
    client = GitHubClient("secret", Session())
    records = (
        {
            "type": "pull_request",
            "ruleset_id": 7,
            "ruleset_source_type": "Repository",
            "parameters": {
                "dismiss_stale_reviews_on_push": False,
                "require_code_owner_review": True,
                "require_last_push_approval": False,
                "required_approving_review_count": 1,
                "required_review_thread_resolution": True,
            },
        },
    )
    protection = ruleset_response(
        {
            "enforce_admins": {"enabled": True},
            "allow_deletions": {"enabled": False},
            "allow_force_pushes": {"enabled": False},
            "required_conversation_resolution": {"enabled": True},
        },
    )
    responses = [ruleset_response(administrator_ruleset(7, enforcement="evaluate")), protection]
    with (
        patch.object(client, "get_paginated", return_value=records),
        patch.object(client, "get", side_effect=responses) as get,
    ):
        result = collect_merge_gate(client, "agilezebra", "personal", repository_metadata())

    assert isinstance(result.evidence, MergeGateEvidence)
    assert result.evidence.applies_to_administrators is True
    assert get.call_args_list[-1] == call(
        "https://api.github.com/repos/agilezebra/nfdiv-case-api/branches/master/protection"
    )
