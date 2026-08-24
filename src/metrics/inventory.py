"""Collect configured repository inventory."""

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from requests.exceptions import JSONDecodeError

from metrics.config import Configuration, owned_repositories
from metrics.cost import CostMeter, combined
from metrics.domain import (
    AlertSeverity,
    AvailabilityReason,
    CollectionStatus,
    EvidenceKind,
    MergeGateEvidence,
    OpenAlertCount,
    PullRequestRule,
    ReportingWindow,
    RepositoryCollectionCost,
    RepositoryInventory,
    RepositoryInventoryIssue,
    RepositoryInventoryItem,
    RepositoryMetadata,
    SecurityAlertEvidence,
    StatusCheck,
    StatusChecksRule,
)
from metrics.github import GitHubClient, GitHubError


class RepositoryRule(BaseModel):
    """Select fields used from an active GitHub repository rule.

    `ruleset_id` and `ruleset_source_type` name the ruleset a rule came from, which is the only
    route to two things the branch-rules endpoint never reports: whether that ruleset is enforced
    at all, and who is exempt from it. Both are optional because a rule that names no ruleset is
    still a rule, and is kept rather than dropped for lacking an attribution.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    type: str
    parameters: dict[str, object] = Field(default_factory=dict)
    ruleset_id: int | None = None
    ruleset_source_type: str | None = None


MODELLED_RULE_TYPES = frozenset(
    {
        "pull_request",
        "required_status_checks",
        "deletion",
        "non_fast_forward",
        "required_linear_history",
        "branch_name_pattern",
    },
)
"""Every ruleset rule type this collector attributes, settled 2026-08-15.

Anything outside this set is reported by name in `MergeGateEvidence.unmodelled_rules` rather than
dropped: a rule GitHub adds later would otherwise make a gate look weaker than it is enforced.
"""


class BypassActor(BaseModel):
    """Select one actor a ruleset exempts, and how far its exemption reaches."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    actor_id: int | None = None
    actor_type: str = ""
    bypass_mode: str = ""


class Ruleset(BaseModel):
    """Select one ruleset's enforcement mode and the actors it exempts."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    id: int
    enforcement: str = ""
    bypass_actors: tuple[BypassActor, ...] = ()


ENFORCING = "active"
"""The one enforcement mode that blocks a merge.

A ruleset set to `evaluate` reports violations into its rule-insights history and lets the merge
through, and `disabled` does not even do that. Neither is part of a gate, so neither contributes a
rule to one — a distinction the branch-rules endpoint does not draw for us.
"""

ADMINISTRATOR_REPOSITORY_ROLE_ID = 5
"""GitHub numbers its base repository roles read, triage, write, maintain, admin, so 5 is admin.

The other four are not administrators, and a custom role is numbered outside that range, so none of
them moves `applies_to_administrators`: that field answers for administrators alone, and a write or
integration exemption belongs to a question nobody asked it.
"""


def bypasses_as_administrator(actor: BypassActor) -> bool:
    """Report whether one exemption is an administrator's.

    A `pull_request` exemption is not counted, by decision: the actor still cannot reach the branch
    except through a pull request, which is the route the gate exists to force. What they can then
    merge is measured by the behaviour metrics rather than asserted here.
    """
    if actor.bypass_mode != "always":
        return False
    if actor.actor_type == "OrganizationAdmin":
        return True
    return actor.actor_type == "RepositoryRole" and actor.actor_id == ADMINISTRATOR_REPOSITORY_ROLE_ID


def binds_administrators(rulesets: tuple[Ruleset | None, ...]) -> bool | None:
    """Report whether every enforcing ruleset behind a branch binds administrators.

    One unreadable ruleset returns None for the whole branch rather than a majority verdict: an
    exemption nobody was allowed to read is not an exemption that is absent, and `None` is the state
    `MergeGateEvidence` keeps for exactly that.
    """
    if not rulesets or any(ruleset is None for ruleset in rulesets):
        return None
    return not any(
        bypasses_as_administrator(actor)
        for ruleset in rulesets
        if ruleset is not None
        for actor in ruleset.bypass_actors
    )


def fetch_ruleset(
    client: GitHubClient,
    organization: str,
    repository: str,
    rule: RepositoryRule,
) -> Ruleset | None:
    """Read one ruleset's enforcement and exemptions, or None when it cannot be read.

    A refusal is logged and degrades one field rather than failing the repository: the rules
    themselves were already collected, and losing a gate entirely because its bypass list is
    private would report less than GitHub disclosed.
    """
    if rule.ruleset_id is None:
        return None
    url = (
        f"{client.api_url}/orgs/{organization}/rulesets/{rule.ruleset_id}"
        if rule.ruleset_source_type == "Organization"
        else f"{client.api_url}/repos/{organization}/{repository}/rulesets/{rule.ruleset_id}"
    )
    try:
        return Ruleset.model_validate(client.get(url).json())
    except (GitHubError, JSONDecodeError, ValidationError) as exception:
        logging.warning("Ruleset %s unreadable for %s: %s", rule.ruleset_id, repository, exception)
        return None


def collect_rulesets(
    client: GitHubClient,
    organization: str,
    repository: str,
    rules: tuple[RepositoryRule, ...],
) -> dict[int, Ruleset | None]:
    """Read each distinct ruleset behind a branch's rules exactly once.

    One ruleset commonly supplies several of a branch's rules — a `deletion`, a `non_fast_forward`
    and a `required_status_checks` from the same one is the ordinary shape — so this is one call per
    ruleset, not one per rule.
    """
    resolved: dict[int, Ruleset | None] = {}
    for rule in rules:
        if rule.ruleset_id is not None and rule.ruleset_id not in resolved:
            resolved[rule.ruleset_id] = fetch_ruleset(client, organization, repository, rule)
    return resolved


def enforcing_rules(
    rules: tuple[RepositoryRule, ...],
    rulesets: dict[int, Ruleset | None],
) -> tuple[RepositoryRule, ...]:
    """Keep the rules whose ruleset actually blocks a merge.

    A rule whose ruleset could not be read is KEPT, deliberately: refusing to disclose a ruleset is
    not evidence that it stopped enforcing, and dropping it would report a gate as weaker than the
    rules GitHub already disclosed.
    """

    def enforces(rule: RepositoryRule) -> bool:
        """Report whether one rule belongs to a ruleset that blocks a merge."""
        if rule.ruleset_id is None:
            return True
        ruleset = rulesets.get(rule.ruleset_id)
        return ruleset is None or ruleset.enforcement == ENFORCING

    return tuple(rule for rule in rules if enforces(rule))


class RepositoryBranch(BaseModel):
    """Select branch protection visibility returned by GitHub."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    protected: bool


class EnabledRule(BaseModel):
    """Select an enabled flag from a classic branch protection rule."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    enabled: bool


class ClassicPullRequestReviews(BaseModel):
    """Select classic pull-request review settings returned by GitHub."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    dismiss_stale_reviews: bool
    require_code_owner_reviews: bool
    require_last_push_approval: bool
    required_approving_review_count: int


class ClassicStatusCheck(BaseModel):
    """Select one classic required status check returned by GitHub."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    context: str
    app_id: int | None = None


class ClassicStatusChecks(BaseModel):
    """Select classic required status-check settings returned by GitHub."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    strict: bool
    contexts: tuple[str, ...] = ()
    checks: tuple[ClassicStatusCheck, ...] = ()


class ClassicBranchProtection(BaseModel):
    """Select classic branch protection settings returned by GitHub."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    required_pull_request_reviews: ClassicPullRequestReviews | None = None
    required_status_checks: ClassicStatusChecks | None = None
    required_conversation_resolution: EnabledRule
    required_linear_history: EnabledRule | None = None
    allow_force_pushes: EnabledRule
    allow_deletions: EnabledRule
    enforce_admins: EnabledRule | None = None


@dataclass(frozen=True)
class MergeGateResult:
    """Carry merge-gate evidence and any limitation affecting its detail."""

    evidence: MergeGateEvidence | None
    failure: RepositoryInventoryIssue | None


@dataclass
class AlertFamilyResult:
    """Carry one alert family's count and, when it was refused, why.

    `reason` is not on `OpenAlertCount` itself because it belongs to the collection run rather than
    to the repository's durable state, and the count is stored where the reason would go stale.
    """

    count: OpenAlertCount
    reason: AvailabilityReason | None


@dataclass
class SecurityAlertResult:
    """Carry the security-alert block and one failure for each family that would not answer."""

    evidence: SecurityAlertEvidence
    failures: tuple[RepositoryInventoryIssue, ...]


def merge_gate_from_classic_protection(branch: str, protection: ClassicBranchProtection) -> MergeGateEvidence:
    """Normalise classic branch protection into merge-gate evidence."""
    reviews = protection.required_pull_request_reviews
    pull_requests = (
        (
            PullRequestRule(
                dismiss_stale_reviews_on_push=reviews.dismiss_stale_reviews,
                require_code_owner_review=reviews.require_code_owner_reviews,
                require_last_push_approval=reviews.require_last_push_approval,
                required_approving_review_count=reviews.required_approving_review_count,
                required_review_thread_resolution=protection.required_conversation_resolution.enabled,
            ),
        )
        if reviews is not None
        else ()
    )
    checks = protection.required_status_checks
    status_checks: tuple[StatusChecksRule, ...] = ()
    if checks is not None:
        checked_contexts = frozenset(check.context for check in checks.checks)
        status_checks = (
            StatusChecksRule(
                strict_required_status_checks_policy=checks.strict,
                required_status_checks=tuple(
                    StatusCheck(context=check.context, integration_id=check.app_id) for check in checks.checks
                )
                + tuple(StatusCheck(context=context) for context in checks.contexts if context not in checked_contexts),
            ),
        )
    return MergeGateEvidence(
        branch=branch,
        protected=True,
        pull_requests=pull_requests,
        status_checks=status_checks,
        restricts_deletions=not protection.allow_deletions.enabled,
        blocks_force_pushes=not protection.allow_force_pushes.enabled,
        # Classic protection carries this rule under its own key rather than as a ruleset type, and
        # it must be read: the assessment reports "does not require a linear history" as a statement
        # about the repository, so leaving the field at its default would assert something unread.
        requires_linear_history=protection.required_linear_history is not None
        and protection.required_linear_history.enabled,
        applies_to_administrators=protection.enforce_admins.enabled if protection.enforce_admins else None,
        rules_observed=True,
    )


def merge_gate_without_rule_details(branch: str, *, protected: bool, rules_observed: bool) -> MergeGateEvidence:
    """Represent branch protection carrying no detailed rules.

    Two different situations produce empty rule arrays and only `rules_observed` separates them:
    GitHub reporting that a branch has no protection at all, and GitHub refusing to disclose the
    protection a branch does have.
    """
    return MergeGateEvidence(
        branch=branch,
        protected=protected,
        pull_requests=(),
        status_checks=(),
        restricts_deletions=False,
        blocks_force_pushes=False,
        rules_observed=rules_observed,
    )


def collect_classic_merge_gate(
    client: GitHubClient,
    organization: str,
    team_identifier: str,
    repository: RepositoryMetadata,
    branch: str,
) -> MergeGateResult:
    """Collect classic protection details with a permission-limited fallback."""
    protection_url = f"{client.api_url}/repos/{organization}/{repository.name}/branches/{branch}/protection"
    try:
        response = client.get(protection_url)
    except GitHubError as exception:
        if exception.reason is AvailabilityReason.NOT_FOUND_OR_INACCESSIBLE:
            # GitHub answered: the branch has no protection. That is an observation, not a blind spot.
            return MergeGateResult(
                evidence=merge_gate_without_rule_details(
                    repository.default_branch,
                    protected=False,
                    rules_observed=True,
                ),
                failure=None,
            )
        if exception.reason is not AvailabilityReason.PERMISSION_DENIED:
            raise
        response = client.get(f"{client.api_url}/repos/{organization}/{repository.name}/branches/{branch}")
        protected = RepositoryBranch.model_validate(response.json()).protected
        evidence = merge_gate_without_rule_details(
            repository.default_branch,
            protected=protected,
            rules_observed=False,
        )
        failure = (
            RepositoryInventoryIssue(
                team_identifier=team_identifier,
                repository=repository.name,
                evidence=EvidenceKind.MERGE_GATE,
                reason=exception.reason,
                detail=str(exception),
            )
            if protected
            else None
        )
        return MergeGateResult(evidence=evidence, failure=failure)
    protection = ClassicBranchProtection.model_validate(response.json())
    return MergeGateResult(
        evidence=merge_gate_from_classic_protection(repository.default_branch, protection),
        failure=None,
    )


class DependabotAdvisory(BaseModel):
    """Select the severity GitHub assigns to one Dependabot advisory."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    severity: str


class DependabotAlert(BaseModel):
    """Select the severity of one Dependabot alert, which it carries on its advisory."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    security_advisory: DependabotAdvisory | None = None


class CodeScanningRule(BaseModel):
    """Select the security grading of one code-scanning rule.

    `security_severity_level` is null for a rule that is not a security rule — CodeQL's quality
    suites raise those too — which is why it is optional and why an ungraded alert contributes to
    the family's open count without contributing a severity.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    security_severity_level: str | None = None


class CodeScanningAlert(BaseModel):
    """Select the rule grading of one code-scanning alert."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    rule: CodeScanningRule | None = None


def count_by_severity(severities: Iterable[str | None]) -> dict[AlertSeverity, int]:
    """Count the gradings GitHub asserted, ignoring anything it did not grade or this tool cannot name.

    An unrecognised grading is dropped rather than mapped onto the nearest known one: GitHub adding a
    severity should leave the counts it does understand correct, not silently reclassify the new one.
    """
    counts: dict[AlertSeverity, int] = {}
    for severity in severities:
        if severity is None:
            continue
        try:
            graded = AlertSeverity(severity.lower())
        except ValueError:
            continue
        counts[graded] = counts.get(graded, 0) + 1
    return {severity: counts[severity] for severity in AlertSeverity if severity in counts}


def open_alerts(
    client: GitHubClient,
    organization: str,
    repository: str,
    family: str,
    severity_of: Callable[[dict[str, object]], str | None],
) -> AlertFamilyResult:
    """Count one family's open alerts by severity, or report why the family could not be read.

    Only open alerts are requested: this is a current-state source, and how long a resolved alert
    took to close is a windowed question this deliberately does not answer.

    A refusal carries its `AvailabilityReason` beside the count so that the caller can record it as
    a collection failure. Invalid JSON needs no branch of its own: `get_paginated` already converts
    it into a `GitHubError`, and a second handler here would be a path no response can reach.

    A 404 is NOT a refusal (decided 2026-08-15). `collect_repository_state` reads the repository's
    metadata with this same token before asking for its alerts, so by the time this runs the
    repository is known to exist and be readable; GitHub answering 404 for one family therefore says
    that family is not turned on, exactly as a 404 from the branch-protection endpoint says the
    branch is unprotected. Reporting it as `not found or inaccessible` would assert a permission
    problem that is not there, and counting it as a collection failure would make `collect` exit `3`
    for every repository that simply does not use the feature — which is most of them, and which
    would empty the three-valued exit status of the signal it was built to carry. A 403 stays a
    refusal: GitHub returns it both for a token without the scope and for Advanced Security being
    off, and only the response text tells them apart, which is too fragile a thing to grade a run on.
    """
    try:
        records = client.get_paginated(
            f"{client.api_url}/repos/{organization}/{repository}/{family}",
            {"state": "open"},
        )
    except GitHubError as exception:
        if exception.reason is AvailabilityReason.NOT_FOUND_OR_INACCESSIBLE:
            return AlertFamilyResult(
                count=OpenAlertCount(detail=f"{family} is not enabled for this repository"),
                reason=None,
            )
        return AlertFamilyResult(count=OpenAlertCount(detail=str(exception)), reason=exception.reason)
    try:
        severities = tuple(severity_of(record) for record in records)
    except ValidationError as exception:
        details = ", ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exception.errors(include_url=False, include_input=False)
        )
        return AlertFamilyResult(
            count=OpenAlertCount(detail=f"GitHub returned invalid {family} records: {details}"),
            reason=AvailabilityReason.COLLECTION_FAILED,
        )
    return AlertFamilyResult(
        count=OpenAlertCount(open=len(records), by_severity=count_by_severity(severities)),
        reason=None,
    )


def dependabot_severity(record: dict[str, object]) -> str | None:
    """Read one Dependabot alert's severity from the advisory that carries it."""
    advisory = DependabotAlert.model_validate(record).security_advisory
    return None if advisory is None else advisory.severity


def code_scanning_severity(record: dict[str, object]) -> str | None:
    """Read one code-scanning alert's security grading, which a non-security rule does not have."""
    rule = CodeScanningAlert.model_validate(record).rule
    return None if rule is None else rule.security_severity_level


def no_severity(record: dict[str, object]) -> str | None:
    """Grade a secret-scanning alert, which GitHub does not grade and neither may this tool.

    Not a placeholder for a lookup nobody has written yet: GitHub's secret-scanning API exposes no
    severity, and treating every leaked secret as critical would rank a test fixture alongside a
    live production key. Ruled 2026-08-14; see architecture.md.
    """
    del record
    return None


def collect_security_alerts(
    client: GitHubClient,
    organization: str,
    team_identifier: str,
    repository: str,
) -> SecurityAlertResult:
    """Collect open security alerts from all three families, each failing independently.

    Three separate endpoints behind three separate permissions, so one family being refused says
    nothing about the other two and must not suppress them. Measured against hmcts/cath-service on
    2026-08-14: one call per family there, and ceil(open / 100) in general.

    A refused family is BOTH reported on the block and recorded as a collection failure. The block
    is what a reader sees; the failure is what the exit status is computed from, and the three
    alert permissions do not travel with a ruleset migration — so a population that answers for
    every repository while withholding every alert family must not exit `0` as though it were
    complete. A family that is merely not enabled is reported on the block and is NOT a failure;
    see `open_alerts`. Both cases leave `open` unset, because a family nobody can read and a family
    nobody turned on are equally not zero open alerts. See architecture.md, "Exit status is
    three-valued".
    """
    families = {
        "dependabot/alerts": open_alerts(client, organization, repository, "dependabot/alerts", dependabot_severity),
        "code-scanning/alerts": open_alerts(
            client,
            organization,
            repository,
            "code-scanning/alerts",
            code_scanning_severity,
        ),
        "secret-scanning/alerts": open_alerts(
            client,
            organization,
            repository,
            "secret-scanning/alerts",
            no_severity,
        ),
    }
    return SecurityAlertResult(
        evidence=SecurityAlertEvidence(
            dependabot=families["dependabot/alerts"].count,
            code_scanning=families["code-scanning/alerts"].count,
            secret_scanning=families["secret-scanning/alerts"].count,
        ),
        failures=tuple(
            RepositoryInventoryIssue(
                team_identifier=team_identifier,
                repository=repository,
                evidence=EvidenceKind.SECURITY,
                reason=result.reason,
                detail=f"{family}: {result.count.detail}",
            )
            for family, result in families.items()
            if result.reason is not None
        ),
    )


def collect_repository(
    client: GitHubClient,
    organization: str,
    team_identifier: str,
    repository: str,
) -> RepositoryInventoryItem | RepositoryInventoryIssue:
    """Collect one repository or describe why it is unavailable."""
    try:
        response = client.get_repository(organization, repository)
        metadata = RepositoryMetadata.model_validate(response.json())
        return RepositoryInventoryItem(team_identifier=team_identifier, repository=metadata)
    except GitHubError as exception:
        reason = exception.reason
        detail = str(exception)
    except JSONDecodeError as exception:
        reason = AvailabilityReason.COLLECTION_FAILED
        detail = f"GitHub returned invalid repository JSON at line {exception.lineno}, column {exception.colno}"
    except ValidationError as exception:
        reason = AvailabilityReason.COLLECTION_FAILED
        details = ", ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exception.errors(include_url=False, include_input=False)
        )
        detail = f"GitHub returned invalid repository metadata: {details}"

    return RepositoryInventoryIssue(
        team_identifier=team_identifier,
        repository=repository,
        evidence=EvidenceKind.REPOSITORY,
        reason=reason,
        detail=detail,
    )


def collect_merge_gate(
    client: GitHubClient,
    organization: str,
    team_identifier: str,
    repository: RepositoryMetadata,
) -> MergeGateResult:
    """Collect default-branch merge rules or describe why they are unavailable."""
    try:
        branch = quote(repository.default_branch, safe="")
        records = client.get_paginated(
            f"{client.api_url}/repos/{organization}/{repository.name}/rules/branches/{branch}",
        )
        rules = tuple(RepositoryRule.model_validate(record) for record in records)
        if not rules:
            return collect_classic_merge_gate(client, organization, team_identifier, repository, branch)
        rulesets = collect_rulesets(client, organization, repository.name, rules)
        # A branch whose every ruleset only evaluates is a branch no ruleset gates, so it takes the
        # same route as one that returned no rules at all: classic protection may still gate it, and
        # commonly does — a repository can carry both, and only the classic side reports on admins.
        active = enforcing_rules(rules, rulesets)
        if not active:
            return collect_classic_merge_gate(client, organization, team_identifier, repository, branch)
        contributing = tuple(dict.fromkeys(rule.ruleset_id for rule in active))
        return MergeGateResult(
            evidence=MergeGateEvidence(
                branch=repository.default_branch,
                protected=True,
                pull_requests=tuple(
                    PullRequestRule.model_validate(rule.parameters) for rule in active if rule.type == "pull_request"
                ),
                status_checks=tuple(
                    StatusChecksRule.model_validate(rule.parameters)
                    for rule in active
                    if rule.type == "required_status_checks"
                ),
                restricts_deletions=any(rule.type == "deletion" for rule in active),
                blocks_force_pushes=any(rule.type == "non_fast_forward" for rule in active),
                rules_observed=True,
                requires_linear_history=any(rule.type == "required_linear_history" for rule in active),
                restricts_branch_names=any(rule.type == "branch_name_pattern" for rule in active),
                # The ruleset path reports this too now. It was null for every ruleset-governed
                # repository until 2026-08-18, which read as "not disclosed" when what GitHub had
                # actually disclosed, one call away, was often an administrator exemption.
                applies_to_administrators=binds_administrators(
                    tuple(rulesets.get(identifier) if identifier is not None else None for identifier in contributing),
                ),
                # Only the ruleset path can produce this: classic protection has a fixed shape, so
                # an uninterpreted key there would be a schema surprise rather than an active rule.
                # `requires_linear_history` above is NOT in that position — classic protection
                # carries it under its own key and the classic path reads it.
                unmodelled_rules=tuple(sorted({rule.type for rule in active} - MODELLED_RULE_TYPES)),
            ),
            failure=None,
        )
    except GitHubError as exception:
        reason = exception.reason
        detail = str(exception)
    except JSONDecodeError as exception:
        reason = AvailabilityReason.COLLECTION_FAILED
        detail = f"GitHub returned invalid branch JSON at line {exception.lineno}, column {exception.colno}"
    except ValidationError as exception:
        reason = AvailabilityReason.COLLECTION_FAILED
        details = ", ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exception.errors(include_url=False, include_input=False)
        )
        detail = f"GitHub returned invalid branch rules: {details}"

    return MergeGateResult(
        evidence=None,
        failure=RepositoryInventoryIssue(
            team_identifier=team_identifier,
            repository=repository.name,
            evidence=EvidenceKind.MERGE_GATE,
            reason=reason,
            detail=detail,
        ),
    )


def collect_repository_state(
    client: GitHubClient,
    organization: str,
    team_identifier: str,
    repository: str,
) -> tuple[RepositoryInventoryItem | None, tuple[RepositoryInventoryIssue, ...]]:
    """Collect one repository's metadata, merge gate and open alerts, or say what was unavailable.

    All three are collected together so that one repository's collection is one measurable unit;
    see `metrics.cost`. A repository whose metadata could not be read is not asked for its gate or
    its alerts, because there is no default branch to ask about.
    """
    result = collect_repository(client, organization, team_identifier, repository)
    if isinstance(result, RepositoryInventoryIssue):
        return None, (result,)
    gate = collect_merge_gate(client, organization, team_identifier, result.repository)
    alerts = collect_security_alerts(client, organization, team_identifier, result.repository.name)
    item = result.model_copy(
        update={
            # Security alerts fail per family, so unlike the merge gate the field is never left
            # unset: a refused family reports its reason on the block AND records a failure.
            "security": alerts.evidence,
            **({"merge_gate": gate.evidence} if gate.evidence is not None else {}),
        },
    )
    return item, (() if gate.failure is None else (gate.failure,)) + alerts.failures


def collect_inventory(
    configuration: Configuration,
    client: GitHubClient,
    window: ReportingWindow,
) -> RepositoryInventory:
    """Collect current repository state for every configured team, measuring what each one cost.

    Repositories are visited in `owned_repositories` order — team identifier, then repository name —
    so the collection report is diffable between runs and cannot be reordered by an edit to the
    configuration file. `costs` is ordered separately, slowest first, because it answers a different
    question.
    """
    repositories: tuple[RepositoryInventoryItem, ...] = ()
    failures: tuple[RepositoryInventoryIssue, ...] = ()
    costs: tuple[RepositoryCollectionCost, ...] = ()
    for team_identifier, repository in owned_repositories(configuration):
        meter = CostMeter(client)
        with meter.measure():
            item, issues = collect_repository_state(client, configuration.organization, team_identifier, repository)
        repositories += () if item is None else (item,)
        failures += issues
        # Keyed on the name GitHub answered with, which is what the window phase meters against: a
        # repository renamed since `hmcts.yml` was written is followed by the API, and keying the two
        # phases differently would split one repository's cost across two rows in the same report.
        costs += (meter.cost(repository if item is None else item.repository.name),)
    if not failures:
        status = CollectionStatus.COMPLETE
    elif repositories:
        status = CollectionStatus.PARTIAL
    else:
        status = CollectionStatus.FAILED
    return RepositoryInventory(
        status=status,
        organization=configuration.organization,
        collected_at=datetime.now(UTC),
        starts_at=window.starts_at,
        ends_at=window.ends_at,
        repositories=repositories,
        failures=failures,
        costs=combined(costs),
    )
