"""Collect configured repository inventory."""

import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError
from requests.exceptions import JSONDecodeError

from metrics.analysis import excluded_authors, is_human_commit_author
from metrics.config import Configuration, owned_repositories
from metrics.cost import CostMeter, combined
from metrics.domain import (
    MAINTENANCE_WINDOWS,
    AlertSeverity,
    AvailabilityReason,
    CodeownersEvidence,
    CodeownersFile,
    CollectionStatus,
    EvidenceKind,
    MaintenanceEvidence,
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
    SonarMeasures,
    SonarProjectResolution,
    StatusCheck,
    StatusChecksRule,
)
from metrics.github import GitHubClient, GitHubError
from metrics.sonar import (
    SONAR_PROPERTIES_PATH,
    SonarClient,
    SonarDeclaration,
    SonarError,
    SonarProjectMap,
    declared_project,
    github_to_sonar,
)


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


MAINTENANCE_HISTORY_PAGE_SIZE = 100
"""Commits per default-branch history page in the repository-standards query.

GitHub's maximum, taken deliberately where `commit_history_query()` stays at 50: these nodes carry
only a date and an author identity — none of the rollup cost that forced the tuned page sizes — so
the page is cheap and fewer round trips win. The measured confirmation comes from the `costs` table
of the first real `collect` run, per the tuned-page-size rule.
"""

MAINTENANCE_HISTORY_PAGE_LIMIT = 10
"""Pages the human-commit search may follow before recording the answer as unknown.

A COST LIMIT, not a policy threshold, which is why it is a module constant rather than
configuration: a bot-dominated repository is exactly where the human-maintenance answer is
interesting, and exactly where paging until a human appears is unbounded — the flux-config
repositories hold roughly 20k direct commits per quarter. Ten pages of 100 bounds the search at
1,000 commits.
"""

HUMAN_MAINTENANCE_SEARCH_DAYS = max(days for _, days in MAINTENANCE_WINDOWS)
"""How far back the human-commit search reaches: the widest reported window, "24 months".

Derived from the windows rather than restated, because `human_window_answer` decides False only
where the search is known to have reached a window's cutoff: a bound narrower than the widest
window would silently turn every exhausted-history answer for that window into "unknown".
"""

CODEOWNERS_LOCATIONS: tuple[tuple[str, str, bool], ...] = (
    ("githubCodeowners", ".github/CODEOWNERS", True),
    ("rootCodeowners", "CODEOWNERS", True),
    ("docsCodeowners", "docs/CODEOWNERS", True),
    ("githubCodeownersMd", ".github/CODEOWNERS.md", False),
    ("rootCodeownersMd", "CODEOWNERS.md", False),
    ("docsCodeownersMd", "docs/CODEOWNERS.md", False),
)
"""Each checked CODEOWNERS location: its query alias, its path, and whether GitHub reads it.

The minimum-standards request names `CODEOWNERS` or `CODEOWNERS.md` in the repository root,
`.github/` or `docs/`; GitHub itself reads only the three extensionless paths. Both facts are
carried so the report can say a `.md` variant satisfies the letter of the standard while doing
nothing on GitHub.
"""

SONAR_PROPERTIES_ALIAS = "sonarProperties"
"""The alias the root `sonar-project.properties` is selected under, beside the CODEOWNERS blobs.

Read in the SAME bundled call rather than in one of its own: this is a current-state document like
the CODEOWNERS files beside it, it is not windowed, and no cached coverage signature is keyed on
this query — so the whole declaration costs no extra round trip.
"""


class CodeownersBlob(BaseModel):
    """Select the size of one CODEOWNERS blob, absent when the path names no blob.

    `byte_size` is None when the path exists but is not a blob — a directory named CODEOWNERS —
    which is treated as no file found, exactly like a missing path.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    byte_size: int | None = Field(default=None, alias="byteSize")


class PropertiesBlob(BaseModel):
    """Select the text of the root `sonar-project.properties`, absent where the path names no blob.

    `text` is null for a blob GitHub considers binary as well as for a path that is not a blob at
    all, and both are read the same way: nothing was declared that this build can read.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    text: str | None = None


class CommitAccount(BaseModel):
    """Select the GitHub account linked to one commit's authorship."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    login: str
    typename: str = Field(alias="__typename")


class CommitAuthor(BaseModel):
    """Select one commit's git author name and any account GitHub links it to.

    `user` is null when the commit's email address matches no account, which is when the author
    NAME becomes the only identity the human predicate can test.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    name: str | None = None
    user: CommitAccount | None = None


class HistoryCommit(BaseModel):
    """Select the date and author identity of one default-branch commit."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    committed_at: AwareDatetime = Field(alias="committedDate")
    author: CommitAuthor | None = None


class HistoryPageInfo(BaseModel):
    """Select GraphQL connection pagination fields."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    has_next_page: bool = Field(alias="hasNextPage")
    end_cursor: str | None = Field(alias="endCursor")


class HistoryPage(BaseModel):
    """Select one bounded page of default-branch history."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    nodes: tuple[HistoryCommit, ...]
    page_info: HistoryPageInfo = Field(alias="pageInfo")


class HistoryTarget(BaseModel):
    """Select the history reachable from the tip of the default branch."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    history: HistoryPage


class HistoryBranchRef(BaseModel):
    """Select the tip of one repository's default branch, absent on an empty repository."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    target: HistoryTarget | None = None


class StandardsRepository(BaseModel):
    """Select the history page from one standards response, keeping the aliased CODEOWNERS blobs.

    The blobs stay as extra fields keyed by their `CODEOWNERS_LOCATIONS` alias rather than being
    declared one by one, so the tuple of locations is the single place a path is spelled;
    `found_codeowners` parses each one as it is read.
    """

    model_config = ConfigDict(extra="allow", frozen=True)

    default_branch_ref: HistoryBranchRef | None = Field(default=None, alias="defaultBranchRef")

    def blob(self, alias: str) -> CodeownersBlob | None:
        """Parse the blob one aliased CODEOWNERS selection answered with, None when it named nothing."""
        raw = (self.model_extra or {}).get(alias)
        return None if raw is None else CodeownersBlob.model_validate(raw)

    def sonar_properties(self) -> str | None:
        """Return the root `sonar-project.properties` as text, None where the repository has none."""
        raw = (self.model_extra or {}).get(SONAR_PROPERTIES_ALIAS)
        return None if raw is None else PropertiesBlob.model_validate(raw).text


class StandardsData(BaseModel):
    """Select the repository from a repository-standards response, first page or continuation."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    repository: StandardsRepository | None = None


def repository_standards_query() -> str:
    """Return the bundled current-state query answering CODEOWNERS, maintenance and the Sonar declaration.

    A NEW, SEPARATE query, deliberately: coverage is keyed on a hash of the windowed GraphQL
    documents, so adding one field to `pull_request_query()` or the direct-commit history query
    would invalidate every repository's settled history. Nothing here touches a windowed signature —
    which is also why the SonarCloud declaration is read HERE rather than in a request of its own.

    Six aliased `object(expression: "HEAD:<path>")` selections answer every CODEOWNERS location in
    one round trip, reading only `byteSize` so an empty file stays visible as found-but-empty; a
    seventh reads the root `sonar-project.properties` as `text`, since its content is the whole
    point of reading it. The history page selects only dates and author identity — none of the
    rollup cost that forced the tuned page sizes elsewhere — which is why `first` is GitHub's
    maximum rather than the 50 the windowed history query uses. One call answers everything in the
    common case; the measured confirmation comes from the `costs` table of the first real `collect`
    run.

    The same document follows continuation pages, with `$cursor` set: re-reading seven small blob
    selections costs nothing worth a second document, and one shape keeps one parser.
    """
    selections = "\n            ".join(
        [
            *(
                f'{alias}: object(expression: "HEAD:{path}") {{ ... on Blob {{ byteSize }} }}'
                for alias, path, _ in CODEOWNERS_LOCATIONS
            ),
            f'{SONAR_PROPERTIES_ALIAS}: object(expression: "HEAD:{SONAR_PROPERTIES_PATH}") {{ ... on Blob {{ text }} }}',
        ],
    )
    return f"""
        query RepositoryStandards($organization: String!, $repository: String!, $cursor: String) {{
          repository(owner: $organization, name: $repository) {{
            {selections}
            defaultBranchRef {{
              target {{
                ... on Commit {{
                  history(first: {MAINTENANCE_HISTORY_PAGE_SIZE}, after: $cursor) {{
                    pageInfo {{ hasNextPage endCursor }}
                    nodes {{ committedDate author {{ name user {{ login __typename }} }} }}
                  }}
                }}
              }}
            }}
          }}
          rateLimit {{ cost limit remaining resetAt }}
        }}
    """


def found_codeowners(repository: StandardsRepository) -> tuple[CodeownersFile, ...]:
    """List every CODEOWNERS file the six aliased selections found, in checked order."""
    return tuple(
        CodeownersFile(path=path, size_bytes=blob.byte_size, recognised_by_github=recognised)
        for alias, path, recognised in CODEOWNERS_LOCATIONS
        if (blob := repository.blob(alias)) is not None and blob.byte_size is not None
    )


def standards_repository(data: dict[str, object]) -> StandardsRepository:
    """Parse one standards response and require the repository it names.

    A response naming no repository is classified rather than parsed around: the repository's
    metadata was read with the same token moments earlier, so GitHub withholding it here is an
    access statement, not an absence of evidence.
    """
    parsed = StandardsData.model_validate(data)
    if parsed.repository is None:
        message = "GitHub GraphQL returned no repository for the standards query"
        raise GitHubError(message, AvailabilityReason.NOT_FOUND_OR_INACCESSIBLE)
    return parsed.repository


def standards_history(repository: StandardsRepository) -> HistoryPage | None:
    """Return the history page one standards response carries, or None for an empty repository."""
    reference = repository.default_branch_ref
    if reference is None or reference.target is None:
        return None
    return reference.target.history


def is_human_history_commit(commit: HistoryCommit, excluded: frozenset[str]) -> bool:
    """Report whether one history commit was authored by a person, by the shared predicate."""
    author = commit.author
    if author is None:
        return False
    account = author.user
    return is_human_commit_author(
        account.login if account is not None else None,
        account.typename if account is not None else None,
        author.name,
        excluded,
    )


def continue_history(client: GitHubClient, organization: str, repository: str, cursor: str | None) -> HistoryPage:
    """Fetch the next history page the human-commit search asked for."""
    data = client.graphql(
        repository_standards_query(),
        {"organization": organization, "repository": repository, "cursor": cursor},
    )
    parsed = StandardsData.model_validate(data)
    if parsed.repository is None:
        message = "GitHub GraphQL returned no repository for a maintenance history page"
        raise GitHubError(message, AvailabilityReason.COLLECTION_FAILED)
    page = standards_history(parsed.repository)
    if page is None:
        message = "GitHub GraphQL returned no default-branch history to continue"
        raise GitHubError(message, AvailabilityReason.COLLECTION_FAILED)
    return page


def find_human_commit(
    client: GitHubClient,
    organization: str,
    repository: str,
    first_page: HistoryPage,
    cutoff: datetime,
    excluded: frozenset[str],
) -> tuple[datetime | None, datetime | None]:
    """Find the newest human default-branch commit within the bounded search.

    Returns `(last_human_commit_at, searched_back_to)`. Pagination continues only while no human
    commit is found, the cutoff is not yet passed, and the page cap is not hit — the cost bound the
    module constants above record. A commit found human is returned even when it predates the
    cutoff, because it was already paid for and answers the question better than "none".

    `searched_back_to` keeps the two absences apart, per the three-valued rule: the oldest commit
    instant examined when the PAGE CAP stopped the search (unknown beyond it), the instant of the
    first commit found past the cutoff when the CUTOFF stopped it, and the cutoff itself when the
    history was EXHAUSTED — every commit after the cutoff was examined, so each reported window can
    decide, even though no commit is that old.
    """
    page = first_page
    searched_back_to = cutoff
    for page_number in range(1, MAINTENANCE_HISTORY_PAGE_LIMIT + 1):
        for node in page.nodes:
            if is_human_history_commit(node, excluded):
                return node.committed_at, None
            searched_back_to = node.committed_at
            if node.committed_at < cutoff:
                return None, searched_back_to
        if not page.page_info.has_next_page:
            return None, cutoff
        if page_number == MAINTENANCE_HISTORY_PAGE_LIMIT:
            break
        page = continue_history(client, organization, repository, page.page_info.end_cursor)
    return None, searched_back_to


@dataclass(frozen=True)
class RepositoryStandardsResult:
    """Carry the CODEOWNERS and maintenance blocks, and one failure per block left unobserved.

    `declaration` is not a block and never becomes one: what a repository says about its own
    SonarCloud project is a hypothesis for `github_to_sonar` to confirm or refute, not evidence to
    report. It is None both where the bundled query failed and where the repository carries no
    `sonar-project.properties`, because in neither case is there a key to test.
    """

    codeowners: CodeownersEvidence | None
    maintenance: MaintenanceEvidence | None
    failures: tuple[RepositoryInventoryIssue, ...]
    declaration: SonarDeclaration | None = None


def standards_failure_detail(exception: GitHubError | ValidationError) -> tuple[AvailabilityReason, str]:
    """Classify what stopped a standards query: the transport's reason, or a response that did not parse."""
    if isinstance(exception, GitHubError):
        return exception.reason, str(exception)
    details = ", ".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
        for error in exception.errors(include_url=False, include_input=False)
    )
    return AvailabilityReason.COLLECTION_FAILED, f"GitHub returned an invalid repository-standards response: {details}"


def collect_repository_standards(
    client: GitHubClient,
    organization: str,
    team_identifier: str,
    repository: RepositoryMetadata,
    excluded: frozenset[str],
) -> RepositoryStandardsResult:
    """Collect CODEOWNERS presence and maintenance instants, or say why each was not observed.

    A missing CODEOWNERS file is an OBSERVATION — checked everywhere, found nowhere — never a
    failure. The bundled query failing is a failure, recorded once per evidence kind with the
    shared reason so each block can report why it is absent, and neither evidence field is built:
    unavailable data never becomes an empty block. A continuation page of the human-commit search
    failing loses only the maintenance block: the CODEOWNERS answer and the last commit were
    already observed by the bundled call, and evidence paid for is not thrown away.
    """
    fetched_at = datetime.now(UTC)
    cutoff = fetched_at - timedelta(days=HUMAN_MAINTENANCE_SEARCH_DAYS)

    def failures(
        kinds: tuple[EvidenceKind, ...], exception: GitHubError | ValidationError
    ) -> tuple[RepositoryInventoryIssue, ...]:
        reason, detail = standards_failure_detail(exception)
        return tuple(
            RepositoryInventoryIssue(
                team_identifier=team_identifier,
                repository=repository.name,
                evidence=kind,
                reason=reason,
                detail=detail,
            )
            for kind in kinds
        )

    try:
        data = client.graphql(
            repository_standards_query(),
            {"organization": organization, "repository": repository.name, "cursor": None},
        )
        answered = standards_repository(data)
        files = found_codeowners(answered)
        declaration = declared_project(answered.sonar_properties())
        history = standards_history(answered)
    except (GitHubError, ValidationError) as exception:
        return RepositoryStandardsResult(
            codeowners=None,
            maintenance=None,
            failures=failures((EvidenceKind.CODEOWNERS, EvidenceKind.MAINTENANCE), exception),
        )
    codeowners = CodeownersEvidence(files=files)
    if history is None or not history.nodes:
        maintenance = MaintenanceEvidence(
            branch=repository.default_branch,
            last_commit_at=None,
            last_human_commit_at=None,
            searched_back_to=None,
        )
        return RepositoryStandardsResult(
            codeowners=codeowners,
            maintenance=maintenance,
            failures=(),
            declaration=declaration,
        )
    try:
        last_human_commit_at, searched_back_to = find_human_commit(
            client,
            organization,
            repository.name,
            history,
            cutoff,
            excluded,
        )
    except (GitHubError, ValidationError) as exception:
        return RepositoryStandardsResult(
            codeowners=codeowners,
            maintenance=None,
            failures=failures((EvidenceKind.MAINTENANCE,), exception),
            declaration=declaration,
        )
    maintenance = MaintenanceEvidence(
        branch=repository.default_branch,
        last_commit_at=history.nodes[0].committed_at,
        last_human_commit_at=last_human_commit_at,
        searched_back_to=searched_back_to,
    )
    return RepositoryStandardsResult(
        codeowners=codeowners,
        maintenance=maintenance,
        failures=(),
        declaration=declaration,
    )


@dataclass(frozen=True)
class SonarSource:
    """Everything a collection needs to read one repository's SonarCloud quality state.

    OPTIONAL at every call site that takes one: a collection given no source collects no measures and
    records no failure. That is what lets this source be added to an installation which has never run
    `map-sonar` — an empty map resolves nothing, and a run that resolved nothing is not a run that
    failed.

    `overrides` is keyed by CONFIGURED repository name, which the configuration validator has already
    checked names a real one, while resolution itself uses the name GitHub answered with: a repository
    renamed since the file was written is followed by the API, and the stored map holds GitHub's own
    spelling of every name in it.
    """

    client: SonarClient
    project_map: SonarProjectMap
    overrides: Mapping[str, str] = field(default_factory=dict)

    def override(self, repository: str) -> str | None:
        """Return the project key configured for one repository, or None where none was."""
        return self.overrides.get(repository)


@dataclass(frozen=True)
class SonarSubject:
    """The repository one resolution is about, and everything already known about it going in.

    Three facts that travel together and are all about the same repository — the name GitHub answered
    with, the project key a human configured for it, and the key it declares about itself — so they
    are one argument rather than three. `repository` is GitHub's own spelling because that is what the
    stored map holds and what a commit check must be addressed to, while the override was looked up
    under the CONFIGURED name by the caller that knows both.
    """

    repository: str
    override: str | None = None
    declaration: SonarDeclaration | None = None


@dataclass(frozen=True)
class SonarStateResult:
    """Carry the project one repository was attributed to, what was measured, and any failure.

    The resolution is carried whether or not measures came back, because it is an answer either way:
    a repository no project is mapped to has been answered for, and storing that reason is what keeps
    it apart from a row written before this source existed. `measures` and `failure` are then
    independent of it — a resolved project's measures can still be refused.
    """

    resolution: SonarProjectResolution
    measures: SonarMeasures | None = None
    failure: RepositoryInventoryIssue | None = None


def latest_analysis_at(client: SonarClient, project: str) -> datetime | None:
    """Read the instant of one project's most recent analysis, None for a project never analysed.

    Read fresh rather than taken from the resolution, whose instant belongs to whichever analysis
    ATTRIBUTED the project to the repository: for a stored mapping that may be months old, and for a
    configured override there is none at all. The measures fetched a moment later are the latest
    analysis's, so dating them by the resolution's evidence would misreport when the quality state
    was measured. One cheap SonarCloud read, against a quota nothing else in a collection competes
    for.
    """
    analyses = client.project_analyses(project, 1)
    return analyses[0].analysis_at if analyses else None


def sonar_failure(
    team_identifier: str,
    repository: str,
    reason: AvailabilityReason,
    detail: str,
) -> RepositoryInventoryIssue:
    """Record one SonarCloud call that would not answer, so the run exits `3` for a real refusal."""
    return RepositoryInventoryIssue(
        team_identifier=team_identifier,
        repository=repository,
        evidence=EvidenceKind.SONAR,
        reason=reason,
        detail=detail,
    )


def collect_sonar(
    source: SonarSource,
    client: GitHubClient,
    organization: str,
    team_identifier: str,
    subject: SonarSubject,
) -> SonarStateResult:
    """Attribute one repository to a SonarCloud project and measure it, or say why it has none.

    A REPOSITORY WITH NO SONARCLOUD PROJECT RECORDS NO FAILURE. Most of the organisation's
    repositories have none — 289 projects against 3,372 repositories — so grading that as a
    collection failure would make `collect` exit `3` for nearly every run and empty the three-valued
    exit status of the signal it was built to carry. It is an observation, and it is stored as one,
    with the reason resolution gave.

    Only a CALL that failed is a failure: a refused SonarCloud read, or a GitHub read the resolution
    ladder needed to confirm a declared key. Both are recorded as one issue under
    `EvidenceKind.SONAR`, and both still store what the resolution did establish — a project named by
    a run that could not then measure it is more useful than no project at all.

    A SONARCLOUD `404` HERE IS THE SAME ANSWER `confirm_by_commit` READS IT AS: the project is not
    there. A mapped project can be deleted, renamed, or made private after the map was built, and
    `map-sonar` walks only the projects SonarCloud still lists, so it never clears the row. Grading
    that as a refusal would exit `3` on every run for a condition no operator action fixes, while the
    truth of it — the map names a project SonarCloud will not show us — is an observation, and is
    stored as one beside the mapping it doubts. A refused read keeps its reason: `401`, `403`, `429`
    and an unparseable response all still fail the run.
    """
    attribution = github_to_sonar(
        source.client,
        client,
        organization,
        subject.repository,
        source.project_map,
        override=subject.override,
        declaration=subject.declaration,
    )
    mapping = attribution.mapping
    if mapping is None:
        detail = attribution.detail
        return SonarStateResult(
            resolution=SonarProjectResolution(detail=detail),
            failure=(
                sonar_failure(team_identifier, subject.repository, attribution.reason, detail or "")
                if attribution.reason is not None
                else None
            ),
        )
    try:
        analysis_at = latest_analysis_at(source.client, mapping.project_key)
        measures = source.client.component_measures(mapping.project_key, analysis_at)
    except SonarError as exception:
        gone = exception.reason is AvailabilityReason.NOT_FOUND_OR_INACCESSIBLE
        detail = (
            # The method is named because it is what a human needs to fix this: a stale map row is
            # cleared by re-running `map-sonar`, a wrong `sonar_projects` override by editing it.
            f"SonarCloud lists no project {mapping.project_key}, resolved for {subject.repository} "
            f"by {mapping.method.value}"
            if gone
            else str(exception)
        )
        return SonarStateResult(
            resolution=SonarProjectResolution(mapping=mapping, detail=detail),
            failure=(None if gone else sonar_failure(team_identifier, subject.repository, exception.reason, detail)),
        )
    return SonarStateResult(resolution=SonarProjectResolution(mapping=mapping), measures=measures)


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
    excluded: frozenset[str],
    sonar: SonarSource | None = None,
) -> tuple[RepositoryInventoryItem | None, tuple[RepositoryInventoryIssue, ...]]:
    """Collect one repository's metadata, standards, merge gate, open alerts and quality state.

    All of it is collected together so that one repository's collection is one measurable unit;
    see `metrics.cost`. A repository whose metadata could not be read is not asked for anything
    else, because there is no default branch to ask about. `excluded` is the comparable
    `cohort.excluded_authors` set the human-commit search tests authors against.

    `sonar` is None for a run collecting no SonarCloud state, which stores neither measures nor a
    resolution: a row that says nothing about SonarCloud is read back as "not collected", never as a
    repository without a project. The declaration the bundled standards query already read is handed
    to the resolution rather than fetched again, and the SonarCloud calls it makes are counted in the
    same meter as the GitHub ones, because the unit being measured is this repository.

    A `StorageError` from the stored map is deliberately NOT caught here. It is a run-level fault —
    the durable observations file cannot be read, which will be true for every repository after this
    one — so it belongs to the command that opened the file rather than to one repository's evidence.
    """
    result = collect_repository(client, organization, team_identifier, repository)
    if isinstance(result, RepositoryInventoryIssue):
        return None, (result,)
    standards = collect_repository_standards(client, organization, team_identifier, result.repository, excluded)
    gate = collect_merge_gate(client, organization, team_identifier, result.repository)
    alerts = collect_security_alerts(client, organization, team_identifier, result.repository.name)
    quality = (
        None
        if sonar is None
        else collect_sonar(
            sonar,
            client,
            organization,
            team_identifier,
            SonarSubject(
                repository=result.repository.name,
                override=sonar.override(repository),
                declaration=standards.declaration,
            ),
        )
    )
    item = result.model_copy(
        update={
            # Security alerts fail per family, so unlike the merge gate the field is never left
            # unset: a refused family reports its reason on the block AND records a failure.
            "security": alerts.evidence,
            **({"merge_gate": gate.evidence} if gate.evidence is not None else {}),
            **({"codeowners": standards.codeowners} if standards.codeowners is not None else {}),
            **({"maintenance": standards.maintenance} if standards.maintenance is not None else {}),
            **({"sonar_project": quality.resolution} if quality is not None else {}),
            **({"sonar": quality.measures} if quality is not None and quality.measures is not None else {}),
        },
    )
    failures = standards.failures + (() if gate.failure is None else (gate.failure,)) + alerts.failures
    return item, failures + (() if quality is None or quality.failure is None else (quality.failure,))


def collect_inventory(
    configuration: Configuration,
    client: GitHubClient,
    window: ReportingWindow,
    sonar: SonarSource | None = None,
) -> RepositoryInventory:
    """Collect current repository state for every configured team, measuring what each one cost.

    Repositories are visited in `owned_repositories` order — team identifier, then repository name —
    so the collection report is diffable between runs and cannot be reordered by an edit to the
    configuration file. `costs` is ordered separately, slowest first, because it answers a different
    question.

    `sonar` is None for a run collecting no SonarCloud state. When it is given, its client is metered
    beside the GitHub one so that a repository's figure covers everything its collection spent.
    """
    repositories: tuple[RepositoryInventoryItem, ...] = ()
    failures: tuple[RepositoryInventoryIssue, ...] = ()
    costs: tuple[RepositoryCollectionCost, ...] = ()
    excluded = excluded_authors(configuration.cohort.excluded_authors)
    for team_identifier, repository in owned_repositories(configuration):
        meter = CostMeter(client, None if sonar is None else sonar.client)
        with meter.measure():
            item, issues = collect_repository_state(
                client,
                configuration.organization,
                team_identifier,
                repository,
                excluded,
                sonar,
            )
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
