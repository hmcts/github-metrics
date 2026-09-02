"""Define immutable evidence contracts."""

from enum import StrEnum

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    NonNegativeFloat,
    NonNegativeInt,
    PositiveInt,
    model_validator,
)


class EvidenceModel(BaseModel):
    """Provide shared immutable evidence behaviour."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class AvailabilityReason(StrEnum):
    """Classify why configured evidence could not be observed."""

    AUTHENTICATION_FAILED = "authentication_failed"
    NOT_APPLICABLE = "not_applicable"
    FEATURE_DISABLED = "feature_disabled"
    PERMISSION_DENIED = "permission_denied"
    NOT_FOUND_OR_INACCESSIBLE = "not_found_or_inaccessible"
    INSUFFICIENT_SAMPLE = "insufficient_sample"
    INCOMPLETE_HISTORY = "incomplete_history"
    RATE_LIMITED = "rate_limited"
    COLLECTION_FAILED = "collection_failed"


class CollectionStatus(StrEnum):
    """Describe the completeness of one collection run."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


class EvidenceKind(StrEnum):
    """Identify the evidence affected by a collection failure."""

    REPOSITORY = "repository"
    MERGE_GATE = "merge_gate"
    BEHAVIOUR = "behaviour"
    CONTINUOUS_INTEGRATION = "continuous_integration"
    SECURITY = "security"
    CODEOWNERS = "codeowners"
    MAINTENANCE = "maintenance"
    SONAR = "sonar"
    OPEN_PULL_REQUESTS = "open_pull_requests"


class EvidenceSource(StrEnum):
    """Identify one independently cached GitHub evidence source."""

    PULL_REQUEST = "pull_request"
    REVIEW = "review"
    REVIEW_COMMENT = "review_comment"
    CONVERSATION_COMMENT = "conversation_comment"
    COMMIT = "commit"
    WORKFLOW_RUN = "workflow_run"
    STATUS_CHECK = "status_check"
    DEPENDABOT_ALERT = "dependabot_alert"
    CODE_SCANNING_ALERT = "code_scanning_alert"
    CREDENTIAL_ALERT = "secret_scanning_alert"


class ObservationStatus(StrEnum):
    """Describe whether a behaviour observation has an eligible sample."""

    OBSERVED = "observed"
    NOT_APPLICABLE = "not_applicable"


class CacheStatus(StrEnum):
    """Describe how stable history was satisfied by the behaviour cache."""

    FETCHED = "fetched"
    PARTIALLY_REUSED = "partially_reused"
    FULLY_REUSED = "fully_reused"


class FindingSeverity(StrEnum):
    """Describe the configured importance of one practice finding."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class CheckConclusion(StrEnum):
    """Identify how one completed status check or check run concluded."""

    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    NEUTRAL = "NEUTRAL"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    ACTION_REQUIRED = "ACTION_REQUIRED"
    SKIPPED = "SKIPPED"
    STALE = "STALE"
    STARTUP_FAILURE = "STARTUP_FAILURE"


class CheckRollupState(StrEnum):
    """Identify the combined state of every check that ran on one commit.

    GitHub's own rollup, used only where there is no gate instant to judge individual checks
    against — see `DirectCommitFact`. `EXPECTED` and `PENDING` both mean nothing has concluded yet.
    """

    EXPECTED = "EXPECTED"
    ERROR = "ERROR"
    FAILURE = "FAILURE"
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"


class ReviewState(StrEnum):
    """Identify a submitted GitHub pull-request review state."""

    APPROVED = "APPROVED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    COMMENTED = "COMMENTED"
    DISMISSED = "DISMISSED"
    PENDING = "PENDING"


class SourceCoverage(EvidenceModel):
    """Describe one successfully cached half-open source interval."""

    organization: str
    repository: str
    source: EvidenceSource
    query_hash: str
    starts_at: AwareDatetime
    ends_at: AwareDatetime

    @model_validator(mode="after")
    def validate_interval(self) -> SourceCoverage:
        """Require the interval end to follow its start."""
        if self.ends_at <= self.starts_at:
            message = "coverage interval end must follow its start"
            raise ValueError(message)
        return self


class ReportingWindow(EvidenceModel):
    """Describe one half-open interval that a report covers."""

    starts_at: AwareDatetime
    ends_at: AwareDatetime

    @model_validator(mode="after")
    def validate_interval(self) -> ReportingWindow:
        """Require the interval end to follow its start."""
        if self.ends_at <= self.starts_at:
            message = "reporting window end must follow its start"
            raise ValueError(message)
        return self


class ReviewFact(EvidenceModel):
    """Record the compact fields needed to classify one review event."""

    identifier: PositiveInt
    submitted_at: AwareDatetime
    state: ReviewState
    author_login: str | None
    author_type: str | None
    # Defaults to 0 so a fact cached before this field existed still parses.
    comment_count: NonNegativeInt = 0
    # Whether the review carried a summary of its own, which GitHub records separately from the
    # inline comments `comment_count` counts. Stored as a boolean rather than the text: no metric
    # reads a review's words, and caching them would grow every interval for nothing.
    # Defaults to False so a fact cached before this field existed still parses.
    has_body: bool = False


class CheckFact(EvidenceModel):
    """Record one status check on a merged pull request's head commit.

    An incomplete check carries no conclusion and no completion instant, so a check still running at
    merge is distinguishable from one that finished and failed.
    """

    name: str
    conclusion: CheckConclusion | None
    completed_at: AwareDatetime | None


class PullRequestFact(EvidenceModel):
    """Record the compact fields needed for merged pull-request evidence."""

    identifier: PositiveInt
    repository: str
    number: PositiveInt
    created_at: AwareDatetime
    merged_at: AwareDatetime
    draft: bool
    # When the change left draft and asked to be reviewed, from the earliest ready-for-review event
    # on its timeline. None when the pull request was never a draft — and also on any fact cached
    # before this field existed, which is why the waiting-time metrics fall back to `created_at`
    # rather than treating an absent instant as a zero-length draft they can prove.
    ready_for_review_at: AwareDatetime | None = None
    author_login: str | None
    author_type: str | None
    reviews: tuple[ReviewFact, ...]
    title: str | None = None
    body: str | None = None
    additions: NonNegativeInt | None = None
    deletions: NonNegativeInt | None = None
    changed_files: NonNegativeInt | None = None
    checks: tuple[CheckFact, ...] = ()


class DirectCommitFact(EvidenceModel):
    """Record one commit that reached the default branch without a pull request.

    Only direct commits are collected: a commit GitHub associates with a pull request arrived
    through the process that `PullRequestFact` already describes, and caching it twice would let the
    same change be counted twice. A direct commit is unreviewed and unapproved by definition — there
    was no pull request, so there was nothing to review — which is why it needs no review fields.

    `check_state` is GitHub's combined rollup for the commit, and is None when no check ran on it at
    all. The rollup rather than the individual checks, deliberately: a direct commit has no merge
    instant to judge checks against, because nothing gated the push, so the per-check completion
    times a merged pull request needs buy nothing here — and fetching them cost one API call per
    commit, 92% of a measured cath-service collection. A merged pull request still keeps `CheckFact`s
    and is still judged as at `merged_at`; see architecture.md, "The mutable edge".
    """

    sha: str
    committed_at: AwareDatetime
    author_login: str | None
    author_type: str | None
    additions: NonNegativeInt | None = None
    deletions: NonNegativeInt | None = None
    changed_files: NonNegativeInt | None = None
    check_state: CheckRollupState | None = None


class Merges(EvidenceModel):
    """Hold every merge into one repository's default branch, by either route.

    The governance cohort is merges, not merged pull requests: a commit pushed straight to
    the default branch skipped the same review a pull request merged without one skipped, and
    splitting the two would let a repository look better for taking the more egregious route. Flow
    distributions still read `pull_requests` alone — a direct commit has no cycle to time.
    """

    pull_requests: tuple[PullRequestFact, ...]
    direct_commits: tuple[DirectCommitFact, ...] = ()


class CachedBehaviourFacts(Merges):
    """Associate one half-open reporting window with its cached facts."""

    organization: str
    repository: str
    starts_at: AwareDatetime
    ends_at: AwareDatetime


class RepositoryMetadata(EvidenceModel):
    """Describe repository state returned by GitHub."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    name: str
    default_branch: str
    archived: bool
    fork: bool
    disabled: bool
    created_at: AwareDatetime
    updated_at: AwareDatetime
    pushed_at: AwareDatetime | None


class PullRequestRule(EvidenceModel):
    """Describe one active pull-request merge rule."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    dismiss_stale_reviews_on_push: bool
    require_code_owner_review: bool
    require_last_push_approval: bool
    required_approving_review_count: NonNegativeInt
    required_review_thread_resolution: bool


class StatusCheck(EvidenceModel):
    """Describe one check required before a branch update."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    context: str
    integration_id: int | None = None


class StatusChecksRule(EvidenceModel):
    """Describe one active required-status-check rule."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    strict_required_status_checks_policy: bool
    required_status_checks: tuple[StatusCheck, ...]


class MergeGateEvidence(EvidenceModel):
    """Describe active rules applied to a repository branch.

    `applies_to_administrators` is None when it was not observable, never False: a gate whose
    enforcement is unknown must not be reported as one that admins can bypass.

    `rules_observed` says whether GitHub disclosed the detailed rules. It is False when a
    non-administrator was refused branch-protection detail, and False by default so that state
    stored before the field existed cannot be read as a gate with no rules — `protected: true` with
    empty rule arrays is otherwise indistinguishable from a gate nobody was allowed to see.

    `restricts_deletions`, `blocks_force_pushes`, `requires_linear_history` and
    `restricts_branch_names` describe THE REPOSITORY's configuration, and each is two-valued on
    purpose: False means the rule is not configured, and `rules_observed` — not a third state on
    the boolean — is what separates that from nobody having been allowed to look.

    `unmodelled_rules` describes THIS TOOL's limits, not the repository's configuration. It names
    every active rule type the collector does not interpret, sorted and deduplicated, so a rule
    GitHub adds after this release is reported rather than silently dropped. An empty tuple means
    every observed rule type was attributed, not that the repository configured nothing.
    """

    branch: str
    protected: bool
    pull_requests: tuple[PullRequestRule, ...]
    status_checks: tuple[StatusChecksRule, ...]
    restricts_deletions: bool
    blocks_force_pushes: bool
    applies_to_administrators: bool | None = None
    rules_observed: bool = False
    requires_linear_history: bool = False
    restricts_branch_names: bool = False
    unmodelled_rules: tuple[str, ...] = ()


class MergeGateReport(EvidenceModel):
    """Report one repository's stored merge gate, or why there is none to show.

    A gate is current state read at `fetched_at`, while the findings beside it cover a historical
    window, so a reader has to be able to see that the two describe different instants. A gate that
    was never collected, or that was not observable when it was, carries the reason instead: an
    absent block would read as an absent gate.
    """

    fetched_at: AwareDatetime | None = None
    gate: MergeGateEvidence | None = None
    detail: str | None = None

    @model_validator(mode="after")
    def validate_availability(self) -> MergeGateReport:
        """Require either an observed gate or the reason there is none."""
        if (self.gate is None) == (self.detail is None):
            message = "a merge gate report must carry either a gate or the reason it is unavailable"
            raise ValueError(message)
        return self


class OpenPullRequestSummary(EvidenceModel):
    """Describe the open and unmerged pull-request state observed at one instant.

    Two of the four counts are windowed and two are current, which is why the window they were
    measured over travels with them in `OpenPullRequestSnapshot` rather than being inferred by
    whoever reads them back. Collected and stored by `collect` since 2026-09-01; `evidence
    --refresh` still observes it fresh. See architecture.md.
    """

    opened_in_window: NonNegativeInt
    closed_without_merge: NonNegativeInt
    currently_open: NonNegativeInt
    stale_open: NonNegativeInt


class OpenPullRequestSnapshot(EvidenceModel):
    """Pair one observation of open pull-request state with the window two of its counts cover.

    `starts_at` and `ends_at` are the COLLECTION window `opened_in_window` and
    `closed_without_merge` were measured over, stored beside them because a count over an
    unremembered window is uninterpretable: a reader comparing two stored rows has to know whether
    the figures cover ninety days or one. The instant the whole observation was made is the
    `fetched_at` of the stored repository state around it, which is where every other current-state
    block takes its timestamp from too.
    """

    starts_at: AwareDatetime
    ends_at: AwareDatetime
    summary: OpenPullRequestSummary


class OpenPullRequestReport(EvidenceModel):
    """Report one repository's open pull-request state, or why it is unavailable.

    Unavailable, never a remembered or zeroed count: a permission or transient failure must not be
    read as "none open", and neither must a row stored before this state was collected.

    `starts_at` and `ends_at` name the window `opened_in_window` and `closed_without_merge` were
    measured over — the collection window on the stored path, the reporting window on the refresh
    path. They ride as their own fields rather than as prose beside the counts because the validator
    below forbids a detail next to a summary: a reader must never have to decide whether a sentence
    under four numbers is a caveat about them or the reason they are missing.
    """

    fetched_at: AwareDatetime | None = None
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None
    summary: OpenPullRequestSummary | None = None
    detail: str | None = None

    @model_validator(mode="after")
    def validate_availability(self) -> OpenPullRequestReport:
        """Require either an observed summary or the reason there is none."""
        if (self.summary is None) == (self.detail is None):
            message = "an open pull-request report must carry either a summary or the reason it is unavailable"
            raise ValueError(message)
        return self


class AlertFamily(StrEnum):
    """Name one independently permissioned family of security alerts.

    Three separate REST endpoints behind three separate permissions, so a family is the unit at
    which availability, storage and reporting all work. The order of declaration is the order every
    rendering lists them in, so a reader comparing two reports reads the same rows in the same
    places.

    `CREDENTIAL_SCANNING` is named as `EvidenceSource.CREDENTIAL_ALERT` is, because a member called
    `SECRET_SCANNING` reads to Ruff's S105 as a hardcoded credential. The wire value is GitHub's
    own name for the family and is unaffected.
    """

    DEPENDABOT = "dependabot"
    CODE_SCANNING = "code-scanning"
    CREDENTIAL_SCANNING = "secret-scanning"


class AlertSeverity(StrEnum):
    """Grade one security alert, using only the severities GitHub itself asserts."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class OpenAlertCount(EvidenceModel):
    """Count one family's open security alerts, or say why they could not be read.

    Each family is a separate REST endpoint behind its own permission, so one may be readable while
    another is refused; availability is therefore recorded per family rather than for the block.
    `open` is None exactly when `detail` explains its absence — an unreadable family must never
    report zero open alerts, which would read as a clean repository.

    `by_severity` counts only the severities GitHub asserted, and DOES NOT NECESSARILY SUM TO `open`:
    - secret-scanning alerts carry no severity at all, so the mapping is always empty for that
      family. Grading every leaked secret critical was considered and rejected — see architecture.md.
    - a code-scanning alert whose rule carries no `security_severity_level` is a quality finding
      rather than a graded security one. It is counted in `open`, because it is open, and left out
      of `by_severity`, because inventing a grade is the same error in a different place.
    """

    open: NonNegativeInt | None = None
    by_severity: dict[AlertSeverity, NonNegativeInt] = {}
    detail: str | None = None

    @model_validator(mode="after")
    def validate_availability(self) -> OpenAlertCount:
        """Require either a count or the reason there is none, and never a severity without one."""
        if (self.open is None) == (self.detail is None):
            message = "an open alert count must carry either a count or the reason it is unavailable"
            raise ValueError(message)
        if self.open is None and self.by_severity:
            message = "an unreadable alert family cannot carry severity counts"
            raise ValueError(message)
        return self


class SecurityAlertEvidence(EvidenceModel):
    """Describe the open security alerts of all three families at one instant.

    Current state, not history: GitHub cannot say what was open in May, and the question these
    answer is what is unfixed NOW. Resolution time — how long alerts stayed open — is windowed and
    is deliberately NOT here; see architecture.md, "Source taxonomy".
    """

    dependabot: OpenAlertCount
    code_scanning: OpenAlertCount
    secret_scanning: OpenAlertCount

    def families(self) -> tuple[tuple[AlertFamily, OpenAlertCount], ...]:
        """Pair each family with its count, in the one order every reader of this block uses.

        Defined here rather than rebuilt at each call site: the observation series, the readable
        report and the stored rows must agree about which count belongs to which family, and three
        separate literal tuples is how they would come to disagree.
        """
        return (
            (AlertFamily.DEPENDABOT, self.dependabot),
            (AlertFamily.CODE_SCANNING, self.code_scanning),
            (AlertFamily.CREDENTIAL_SCANNING, self.secret_scanning),
        )


class AlertObservation(EvidenceModel):
    """Record what one alert family held open for one repository at one observed instant.

    THE ONE THING IN THIS SYSTEM THAT IS NOT RECOMPUTABLE AND NOT DISPOSABLE. Open alert counts are
    current state, stored latest-only, so each collection overwrites the count the one before it
    saw; GitHub cannot be asked what was open last month. An observation is therefore appended per
    run and kept, under the storage rule's own test — store what cannot be recomputed — and lives in
    a file of its own so the cache stays disposable. See architecture.md, "Storage rule".

    `open` is never None here: a family that could not be read appends NOTHING and keeps reporting
    its reason on the current-state block, because an unreadable family is not zero open alerts and
    a row saying so would become an indistinguishable dip in the series a year later.

    `fetched_at` is when the collection observed the count, not a period boundary. The series is
    reported at the instants `collect` happened to run, and is honest about being sampled.
    """

    family: AlertFamily
    fetched_at: AwareDatetime
    open: NonNegativeInt
    by_severity: dict[AlertSeverity, NonNegativeInt] = {}

    @model_validator(mode="after")
    def validate_severity(self) -> AlertObservation:
        """Hold the secret-scanning no-severity ruling in the observation schema as well.

        GitHub grades neither a leaked test fixture nor a live production key, so neither may this
        tool — ruled 2026-08-14. Enforced here rather than trusted to the collector, because a
        severity invented once is stored for ever.
        """
        if self.family is AlertFamily.CREDENTIAL_SCANNING and self.by_severity:
            message = "secret-scanning alerts carry no severity, so an observation of them cannot count one"
            raise ValueError(message)
        return self


class SecurityAlertReport(EvidenceModel):
    """Report one repository's stored security alerts, or why there are none to show.

    Stored current state read at `fetched_at`, exactly like the merge gate beside it, so a cached
    `evidence` run serves the last collection's answer rather than refusing. A repository whose state was never
    collected carries the reason instead: an absent block would read as a repository with no alerts.
    """

    fetched_at: AwareDatetime | None = None
    alerts: SecurityAlertEvidence | None = None
    detail: str | None = None

    @model_validator(mode="after")
    def validate_availability(self) -> SecurityAlertReport:
        """Require either observed alerts or the reason there are none."""
        if (self.alerts is None) == (self.detail is None):
            message = "a security alert report must carry either alerts or the reason they are unavailable"
            raise ValueError(message)
        return self


class CodeownersFile(EvidenceModel):
    """Describe one CODEOWNERS file found at one of the six checked locations.

    `recognised_by_github` says whether GitHub reads a file at this path (`.github/CODEOWNERS`,
    `CODEOWNERS` or `docs/CODEOWNERS`) rather than merely hosting it: a `CODEOWNERS.md` satisfies
    the letter of the minimum standard while doing nothing on GitHub, and the two must not read
    alike. `size_bytes` keeps an empty file visible as found-but-empty rather than silently passing.
    """

    path: str
    size_bytes: NonNegativeInt
    recognised_by_github: bool


class CodeownersEvidence(EvidenceModel):
    """List every CODEOWNERS file one repository holds at the checked locations.

    An empty `files` tuple means every location was checked and no file was found — an OBSERVATION,
    never a failure. Report-only and ungraded: `ReadinessPolicy` reads none of this, per the rule
    that a signal becoming visible is not a reason to grade it (architecture.md, "Readiness
    assessment").
    """

    files: tuple[CodeownersFile, ...]


class CodeownersReport(EvidenceModel):
    """Report one repository's stored CODEOWNERS state, or why there is none to show.

    Stored current state read at `fetched_at`, exactly like the merge gate beside it. A repository
    whose state was never collected, or whose query failed, carries the reason instead: an absent
    block would read as an absent file.
    """

    fetched_at: AwareDatetime | None = None
    codeowners: CodeownersEvidence | None = None
    detail: str | None = None

    @model_validator(mode="after")
    def validate_availability(self) -> CodeownersReport:
        """Require either observed CODEOWNERS state or the reason there is none."""
        if (self.codeowners is None) == (self.detail is None):
            message = "a codeowners report must carry either its evidence or the reason it is unavailable"
            raise ValueError(message)
        return self


class MaintenanceEvidence(EvidenceModel):
    """Record when one repository's default branch last received a commit, by anyone and by a person.

    `last_commit_at` is the newest commit on the default branch, and None when the branch holds no
    commits. It is deliberately NOT `pushed_at`/`updated_at`, which move when a bot pushes a
    pull-request branch that never merges — the insufficiency the minimum-standards request names.

    `last_human_commit_at` is the newest commit whose author passes the shared human predicate, and
    is None when the bounded search found none. `searched_back_to` is the oldest instant the search
    examined, recorded exactly when no human commit was found, so a report can keep "none within the
    window" apart from "unknown beyond the commits examined" — the search is bounded by a page cap,
    and the two absences are different answers.

    No ordering between `last_human_commit_at` and `last_commit_at` is enforced: history is walked
    in topological order and `committedDate` is whatever the committer's clock said, so a rebased or
    skewed commit deeper in the history may legitimately carry the later instant.
    """

    branch: str
    last_commit_at: AwareDatetime | None
    last_human_commit_at: AwareDatetime | None
    searched_back_to: AwareDatetime | None

    @model_validator(mode="after")
    def validate_shape(self) -> MaintenanceEvidence:
        """Hold the three instants to the shapes the bounded search can actually produce."""
        if self.last_commit_at is None:
            if self.last_human_commit_at is not None or self.searched_back_to is not None:
                message = "a branch with no commits has nothing to have searched"
                raise ValueError(message)
            return self
        if self.last_human_commit_at is None:
            if self.searched_back_to is None:
                message = "an absent human commit must say how far back the search examined"
                raise ValueError(message)
            return self
        if self.searched_back_to is not None:
            message = "a found human commit carries no search bound"
            raise ValueError(message)
        return self


MAINTENANCE_WINDOWS: tuple[tuple[int, int], ...] = ((6, 183), (12, 365), (24, 730))
"""The reported maintenance windows as (months, days): "6 months", "12 months", "24 months".

Day counts, because a month is not a fixed span; each window is the half-open interval
`[fetched_at - days, fetched_at)` against the stored observation instant, so the derived rows
reproduce offline from the JSON alone. Declared here, beside the evidence it is derived from, so the
collector's search bound and the report's widest window are one number rather than two that drift.
"""


class MaintenanceWindowStatus(EvidenceModel):
    """Answer one maintenance window, derived at report assembly from the stored instants.

    `committed_within` is always decidable once `last_commit_at` is known — an empty branch has no
    commit within any window. `human_committed_within` is three-valued on purpose: True, False when
    the search reached the window's cutoff and found nobody, and None when the page cap stopped the
    search first, carrying `human_detail` so the unknown states its reason. Unavailable data never
    becomes zero, and here it never becomes False either.
    """

    months: PositiveInt
    committed_within: bool
    human_committed_within: bool | None = None
    human_detail: str | None = None

    @model_validator(mode="after")
    def validate_availability(self) -> MaintenanceWindowStatus:
        """Require an unknown human answer to carry its reason, and a decided one to carry none."""
        if (self.human_committed_within is None) == (self.human_detail is None):
            message = "a window's human answer is either decided or carries the reason it is unknown"
            raise ValueError(message)
        return self


class MaintenanceReport(EvidenceModel):
    """Report one repository's stored maintenance state, or why there is none to show.

    Stored current state read at `fetched_at`, exactly like the merge gate beside it. `windows` is
    DERIVED at report assembly from the stored instants against `fetched_at` — the observation
    instant, so the JSON reproduces offline — and accompanies the evidence, never a reason. A
    repository whose state was never collected carries the reason instead: an absent block would
    read as an unmaintained repository nobody can date.
    """

    fetched_at: AwareDatetime | None = None
    maintenance: MaintenanceEvidence | None = None
    windows: tuple[MaintenanceWindowStatus, ...] = ()
    detail: str | None = None

    @model_validator(mode="after")
    def validate_availability(self) -> MaintenanceReport:
        """Require either observed maintenance with its window rows or the reason there is none."""
        if (self.maintenance is None) == (self.detail is None):
            message = "a maintenance report must carry either its evidence or the reason it is unavailable"
            raise ValueError(message)
        if (self.maintenance is None) != (not self.windows):
            message = "window rows accompany maintenance evidence, never a reason"
            raise ValueError(message)
        return self


SONAR_RATING_LETTERS = "ABCDE"
"""The A-to-E letters SonarCloud's 1.0-to-5.0 rating scale stands for, best first."""


class SonarRating(EvidenceModel):
    """Hold one SonarCloud rating as the number it was reported as, and the letter it names.

    SonarCloud reports a rating as `1.0` to `5.0` and every human reads it as `A` to `E`, so the float is
    stored — it is what the API said — and the letter is derived for rendering. `letter` is None for
    any value outside the scale rather than clamped to an end of it: a rating this build does not
    understand must not be reported as the best one, which is what defaulting to `A` would do.
    """

    value: float

    @property
    def letter(self) -> str | None:
        """Return the `A` to `E` letter this rating names, or None when it is off the scale."""
        if not self.value.is_integer() or not 1 <= self.value <= len(SONAR_RATING_LETTERS):
            return None
        return SONAR_RATING_LETTERS[int(self.value) - 1]


class SonarGateLevel(StrEnum):
    """Identify how one SonarCloud quality gate stands, using only the levels SonarCloud reports.

    `NONE` is SonarCloud's own value for a project with no gate result — a project that has been
    created and never analysed, of which 14 of the organisation's 289 were on 2026-08-27. It is a
    third answer and not a failure: the project exists, and nothing has been measured against it.
    """

    OK = "OK"
    ERROR = "ERROR"
    NONE = "NONE"


class SonarQualityGateCondition(EvidenceModel):
    """State one condition behind a quality gate's level, as `quality_gate_details` reported it.

    `threshold` and `actual` are kept as the strings SonarCloud returned. The two are not the same
    kind of number from one condition to the next — `1` against a rating means "worse than A" while
    `80` against new coverage means a percentage — so parsing them into floats here would invent a
    type the wire does not have, and the block renders them beside the metric that gives them their
    meaning. `actual` is None for a condition SonarCloud reported without a measured value.
    """

    metric: str
    comparator: str
    threshold: str | None = None
    actual: str | None = None
    level: SonarGateLevel


class SonarQualityGate(EvidenceModel):
    """Report one project's gate level together with every condition behind it.

    The conditions are carried, not just the verdict, for the same reason the merge gate reports its
    rules and the readiness assessment reports what it checked: "the gate failed" is an assertion,
    and "new coverage 62.1% against a threshold of 80%" is evidence. An empty `conditions` tuple is
    accepted — a never-analysed project has a level and nothing behind it.
    """

    level: SonarGateLevel
    conditions: tuple[SonarQualityGateCondition, ...] = ()


class SonarMeasures(EvidenceModel):
    """Record what SonarCloud measured for one project at the instant of its latest analysis.

    EVERY measure is optional, and an absent one means SonarCloud returned no value for it — never
    zero. A project can be listed and never analysed, in which case there is nothing here but the
    key; a project can also be analysed by a scanner that reported no coverage, and a rendered `0.0%`
    would be a claim about the code rather than about the measurement.

    `maintainability_rating` is named for what it measures. SonarCloud's metric key for it is
    `sqale_rating`, and `maintainability_rating` IS NOT A VALID KEY — requesting it returns
    `measures: null` for the whole call, discarding every other metric silently. The key set is a
    single guarded constant in `metrics.sonar` for exactly that reason; this field name never reaches
    the API.
    """

    project_key: str
    analysis_at: AwareDatetime | None = None
    gate: SonarQualityGate | None = None
    coverage: NonNegativeFloat | None = None
    duplicated_lines_density: NonNegativeFloat | None = None
    lines_of_code: NonNegativeInt | None = None
    violations: NonNegativeInt | None = None
    reliability_issues: NonNegativeInt | None = None
    maintainability_issues: NonNegativeInt | None = None
    security_issues: NonNegativeInt | None = None
    security_hotspots: NonNegativeInt | None = None
    reliability_rating: SonarRating | None = None
    maintainability_rating: SonarRating | None = None
    security_rating: SonarRating | None = None
    security_review_rating: SonarRating | None = None


class SonarResolution(StrEnum):
    """Say how one repository was attributed to one SonarCloud project.

    Recorded on the evidence rather than inferred later, because every method here has a different
    strength and a wrong mapping is diagnosable only if the report says which one answered. The
    ladder that produces these is in `metrics.sonar`; a NAME RULE is deliberately not among them, and
    architecture.md records the measurement that rejected it.
    """

    CONFIGURED = "configured"
    DECLARED_CONFIRMED_BY_MAP = "declared_confirmed_by_map"
    DECLARED_CONFIRMED_BY_COMMIT = "declared_confirmed_by_commit"
    STORED_MAP = "stored_map"
    ANALYSIS_REVISION = "analysis_revision"


class SonarProjectMapping(EvidenceModel):
    """Attribute one SonarCloud project to one GitHub repository, and say how.

    `analysis_at` and `revision` are the evidence behind a resolution that used one: the commit an
    analysis ran against, and when it ran. Both are absent for a configured override, which is an
    instruction rather than an observation, and for a resolution that read neither.
    """

    project_key: str
    repository: str
    method: SonarResolution
    analysis_at: AwareDatetime | None = None
    revision: str | None = None


class StoredSonarMapping(EvidenceModel):
    """Pair one row of the stored `sonar → github` map with the instant it was resolved.

    Either the mapping the resolution produced or the reason it produced none, never both: AN
    UNRESOLVED PROJECT IS STORED WITH ITS REASON, because the alternative is spending the scarcest
    quota there is re-asking the same hopeless question on every run.

    `resolved_at` belongs to the resolution and not to the project, so it sits beside the mapping
    rather than inside it, exactly as `StoredRepositoryState.fetched_at` does.
    """

    project_key: str
    resolved_at: AwareDatetime
    mapping: SonarProjectMapping | None = None
    detail: str | None = None

    @property
    def analysis_at(self) -> AwareDatetime | None:
        """Return the analysis instant this row was resolved from, or None when it had none."""
        return None if self.mapping is None else self.mapping.analysis_at

    @model_validator(mode="after")
    def validate_availability(self) -> StoredSonarMapping:
        """Require either a resolved mapping or the reason the project could not be resolved."""
        if (self.mapping is None) == (self.detail is None):
            message = "a stored sonar mapping must carry either its mapping or the reason it has none"
            raise ValueError(message)
        if self.mapping is not None and self.mapping.project_key != self.project_key:
            message = "a stored sonar mapping must be filed under the project key it names"
            raise ValueError(message)
        return self


class SonarRepositoryProject(EvidenceModel):
    """Name the SonarCloud project the stored map attributes to one repository, and how many claimed it.

    THE REVERSE LOOKUP IS MANY-TO-ONE. SonarCloud has no rename, so a re-created project leaves its
    abandoned twin behind under the old key — `rpx-xui-webapp` and `rpx-xui-webapp_2` both resolve to
    one repository, and it is the suffixed one that is still being analysed. The winner is therefore
    the most recently analysed candidate, and `candidates` reports how many there were so that a human
    reading the block can see a choice was made rather than assuming there was only one.
    """

    mapping: SonarProjectMapping
    candidates: PositiveInt = 1


class SonarProjectResolution(EvidenceModel):
    """Record the SonarCloud project one collection attributed to one repository, or why none was.

    Stored beside the measures rather than recomputed when the report is assembled, because HOW a
    project was chosen is not recoverable later: the configured override that answered may have been
    edited since, the stored map may have moved the project, and a cached report may contact
    neither. The method is the thing that makes a wrong mapping diagnosable, so it is stored with the
    evidence it produced.

    It is also what keeps "NO SONARCLOUD PROJECT IS MAPPED TO THIS REPOSITORY" — an answer, and the
    answer for most of the organisation — apart from "measures were not collected when this row was
    stored", which is a gap. The first stores a resolution carrying its reason; the second stores
    nothing at all, which is what a row written before this source existed holds.

    `mapping` and `detail` may appear TOGETHER, unlike the report models' strict either-or: a project
    can be named and its measures still be unreadable, and then the reason belongs beside the project
    it is about. Only having neither is refused, because that would be a resolution that says nothing.
    """

    mapping: SonarProjectMapping | None = None
    detail: str | None = None

    @model_validator(mode="after")
    def validate_availability(self) -> SonarProjectResolution:
        """Require either the project this repository was attributed to or the reason it has none."""
        if self.mapping is None and self.detail is None:
            message = "a sonar project resolution must name a project or say why it names none"
            raise ValueError(message)
        return self


class SonarReport(EvidenceModel):
    """Report one repository's stored SonarCloud state, or why there is none to show.

    Stored current state read at `fetched_at`, exactly like the merge gate beside it, and REPORT-ONLY
    AND UNGRADED: `ReadinessPolicy` reads none of it, per the standing rule that a signal becoming
    visible is not a reason to grade it. A repository with no SonarCloud project carries the reason,
    which most of the organisation's repositories do — an absent block would read as a project whose
    gate nobody has looked at.

    `mapping` sits BESIDE the measures as readily as instead of them: a project can be resolved and
    its measures still be unreadable — a failed call, or a row stored before this source existed — and
    naming the project the reason is about is more useful than withholding it. Where both are
    reported they must name the same project: a block headed by one project key reporting another
    project's coverage would be worse than no block at all.
    """

    fetched_at: AwareDatetime | None = None
    mapping: SonarProjectMapping | None = None
    measures: SonarMeasures | None = None
    detail: str | None = None

    @model_validator(mode="after")
    def validate_availability(self) -> SonarReport:
        """Require either observed measures or the reason there are none, for one named project."""
        if (self.measures is None) == (self.detail is None):
            message = "a sonar report must carry either its evidence or the reason it is unavailable"
            raise ValueError(message)
        mapping, measures = self.mapping, self.measures
        if mapping is not None and measures is not None and mapping.project_key != measures.project_key:
            message = "sonar measures must be reported for the project the mapping names"
            raise ValueError(message)
        return self


class ReadinessLabel(StrEnum):
    """Judge one repository's readiness for agentic tooling.

    `CANNOT_ASSESS` is not a grade between amber and red. It means a half of the question could not
    be read at all — most often a merge gate a non-administrator cannot see — so no go / no-go answer
    exists. Reporting it as red would blame a permission gap on the team; reporting it as green would
    pass a repository that may have no gate whatever.
    """

    GREEN = "green"
    AMBER = "amber"
    RED = "red"
    CANNOT_ASSESS = "cannot_assess"


class ReadinessCondition(EvidenceModel):
    """State one checked condition and the evidence behind it.

    `label` is the ceiling the condition imposes on its repository's label, so only a blocking
    condition carries one: a caution and a clear condition impose nothing. Details quantify wherever
    a number exists, because "below the target" is an assertion and "81.2% (78 of 96)" is evidence.
    """

    condition: str
    label: ReadinessLabel | None = None
    detail: str


class ReadinessAssessment(EvidenceModel):
    """Answer the enablement question for one repository, with everything that was checked.

    Every condition the policy checked appears in exactly one section, so a reader sees what was
    examined rather than only what failed:

    - `blocking` held the label below green, each condition carrying the ceiling it imposed.
    - `caution` is not disqualifying but should be weighed — a gate requiring no status check is the
      standing example, being the condition closest to a veto without being one.
    - `clear` was checked and imposes no ceiling, with its numbers, so a green is as auditable as a
      red. Usually that means satisfied; it also holds the three merge-gate rules that bear on the
      readiness label in neither state, which sit here whether configured or absent, each saying so
      in its own detail rather than being dressed as a caution.

    An empty `blocking` is the only way to reach green, so the label is never a bare assertion. A red
    condition outranks `cannot_assess`: a disqualifier found in observed behaviour does not need the
    gate to be visible, and an unreadable gate must not hide one.
    """

    label: ReadinessLabel
    blocking: tuple[ReadinessCondition, ...]
    caution: tuple[ReadinessCondition, ...]
    clear: tuple[ReadinessCondition, ...]


class RateObservation(EvidenceModel):
    """Describe an aggregate rate without assigning a target or judgment."""

    status: ObservationStatus
    numerator: NonNegativeInt
    denominator: NonNegativeInt


class DistributionObservation(EvidenceModel):
    """Describe selected percentiles for one aggregate numeric sample."""

    status: ObservationStatus
    sample_size: NonNegativeInt
    unit: str
    median: NonNegativeFloat | None
    percentile_75: NonNegativeFloat | None
    percentile_90: NonNegativeFloat | None


class Percentile(StrEnum):
    """Name the single percentile one distribution is read at, wherever one number is needed.

    A distribution has three percentiles and a report that compares windows needs ONE of them, the
    same one every time: comparing this period's median against the baseline's 75th percentile would
    be movement invented by the choice of percentile. The wire value is the field it names on
    `DistributionObservation`, so a reader can find the number the comparison was taken from.
    """

    MEDIAN = "median"
    PERCENTILE_75 = "percentile_75"

    @property
    def label(self) -> str:
        """Return how this percentile is named in a rendering."""
        return "median" if self is Percentile.MEDIAN else "75th percentile"

    def of(self, observation: DistributionObservation) -> float | None:
        """Return this percentile's observed value, or None when the window observed no sample."""
        return observation.median if self is Percentile.MEDIAN else observation.percentile_75


class BehaviourProvenance(EvidenceModel):
    """Describe cache reuse and compact facts used for one behaviour result."""

    stable_history: CacheStatus
    stable_intervals_fetched: NonNegativeInt
    mutable_starts_at: AwareDatetime
    pull_request_facts_loaded: NonNegativeInt
    review_facts_loaded: NonNegativeInt
    direct_commit_facts_loaded: NonNegativeInt = 0
    direct_commit_intervals_fetched: NonNegativeInt = 0


class WindowProvenance(EvidenceModel):
    """Describe how one reporting window was satisfied."""

    offline: bool
    intervals_fetched: NonNegativeInt


class CohortSummary(EvidenceModel):
    """Describe which merges into the default branch a report covers.

    `merged`, `reported` and `excluded_authors` account for pull requests, so the three reconcile
    against GitHub's merged list. `direct_commits` counts the direct commits left after the same
    author exclusions, and exists to explain a governance denominator: it says how much of a poor
    coverage rate is process bypassed rather than review skipped. It grades nothing on its own.
    """

    merged: NonNegativeInt
    reported: NonNegativeInt
    excluded_authors: dict[str, NonNegativeInt]
    direct_commits: NonNegativeInt = 0


class ReviewEvidenceReference(EvidenceModel):
    """Identify one review event used to explain a pull-request classification."""

    submitted_at: AwareDatetime
    state: ReviewState
    author_login: str | None
    author_type: str | None


class PullRequestEvidenceReference(EvidenceModel):
    """Explain how one pull request contributes to a behaviour observation."""

    number: PositiveInt
    url: str
    merged_at: AwareDatetime
    author_login: str | None
    classification: str
    value: NonNegativeFloat | None = None
    reviews: tuple[ReviewEvidenceReference, ...] = ()


class CommitEvidenceReference(EvidenceModel):
    """Explain how one direct commit contributes to a behaviour observation."""

    sha: str
    url: str
    committed_at: AwareDatetime
    author_login: str | None
    classification: str


class BehaviourMetricSummary(EvidenceModel):
    """Summarise one metric over one cohort, without the named evidence a drill-down carries.

    The three fields are the three a `BehaviourEvidenceReport` holds that are about the METRIC rather
    than about the window it was measured over: the identifier, the aggregate, and the classifications
    the aggregate was counted from. The window, the organisation, the provenance and the cohort are
    left out because whatever carries a tuple of these already states them once — a repository block
    for the whole cohort, an actor row for one person's merges inside it.

    Carried per repository and never combined across repositories: nine summaries of one person's
    merges in one repository are an observation, and averaging them over the repositories they
    contributed to would invent a per-person figure this tool does not measure (architecture.md,
    "Scope boundaries").
    """

    metric: str
    summary: RateObservation | DistributionObservation
    classifications: dict[str, NonNegativeInt]


class BehaviourEvidenceReport(EvidenceModel):
    """Describe an offline aggregate and optional named contributing evidence.

    `direct_commits` sits beside `pull_requests` rather than inside it, and only for a metric whose
    cohort they join: the two routes are different evidence and a reader has to see which one a
    change took. Both are absent from the report entirely when there is nothing to name.
    """

    organization: str
    repository: str
    metric: str
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    provenance: WindowProvenance
    cohort: CohortSummary
    summary: RateObservation | DistributionObservation
    classifications: dict[str, NonNegativeInt]
    identities_included: bool
    pull_requests: tuple[PullRequestEvidenceReference, ...] | None = None
    direct_commits: tuple[CommitEvidenceReference, ...] | None = None


class PracticePullRequestReference(EvidenceModel):
    """Identify one pull request supporting a practice finding."""

    number: PositiveInt
    url: str
    merged_at: AwareDatetime
    size_class: str
    changed_lines: NonNegativeInt | None = None
    changed_files: NonNegativeInt | None = None


class PracticeCommitReference(EvidenceModel):
    """Identify one direct commit supporting a practice finding.

    Deliberately shaped like `PracticePullRequestReference` so the two read alike, and deliberately
    kept apart from it so the route a change took stays visible.
    """

    sha: str
    url: str
    committed_at: AwareDatetime
    size_class: str
    changed_lines: NonNegativeInt | None = None
    changed_files: NonNegativeInt | None = None


class PracticeFinding(EvidenceModel):
    """Describe one person-level finding produced by a configured rule.

    `occurrences`, `authored_merges` and `occurrences_by_size` cover both routes a change can take
    onto the default branch. An actor with no direct commits carries no `direct_commits` key at all
    rather than an empty one, because the report is dumped excluding None fields.
    """

    rule: str
    severity: FindingSeverity
    actor_login: str
    occurrences: PositiveInt
    authored_merges: PositiveInt
    percentage: NonNegativeFloat
    message: str
    occurrences_by_size: dict[str, NonNegativeInt]
    pull_requests: tuple[PracticePullRequestReference, ...]
    direct_commits: tuple[PracticeCommitReference, ...] | None = None


class RepositoryPracticeEvidence(EvidenceModel):
    """Contain one cached repository window's governance and the behaviour observed under it.

    The findings are reported as `behaviour` so that the two halves of the block name what they are:
    `merge_gate` is the governance a repository declares, `behaviour` is what its merges actually did.

    `team` is the identifier of the team the configuration says owns this repository. It rides in the
    block because every reader of the block groups by it — the readable report looked it up from a map
    passed beside the report, and a consumer reading the JSON alone had no way to.

    `metrics` holds the nine neutral aggregates over this repository's whole cohort, which the
    readable report has always shown and the JSON did not carry. They are the same figures a
    `--metric` drill-down emits, computed once and shared, so the two renderings cannot diverge.
    """

    repository: str
    team: str
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    provenance: WindowProvenance
    cohort: CohortSummary
    assessment: ReadinessAssessment | None = None
    merge_gate: MergeGateReport
    open_pull_requests: OpenPullRequestReport
    security: SecurityAlertReport
    codeowners: CodeownersReport
    maintenance: MaintenanceReport
    sonar: SonarReport
    metrics: tuple[BehaviourMetricSummary, ...]
    behaviour: tuple[PracticeFinding, ...]


class EvidenceUnavailable(EvidenceModel):
    """Describe cached evidence unavailable for one configured repository."""

    repository: str
    detail: str


class ActorRepositoryReadiness(EvidenceModel):
    """State how much one person contributed to one repository, and how that repository is labelled.

    `readiness` is the repository's assessed label, and is absent when the readiness policy is
    disabled — there is then no label to carry, and inventing one would grade a repository the
    report deliberately left ungraded.

    `contributions` counts the merges that person authored in the window, by either route, after the
    cohort's author exclusions. `blocking` counts the occurrences behind their practice findings in
    that repository, so twelve unreviewed merges count as twelve rather than as one finding. Neither
    number judges the person: `blocking` is what a rule already reported, gathered per repository.

    `metrics` summarises the same nine aggregates the repository block carries, measured over THIS
    PERSON'S merges in THIS REPOSITORY alone. It is a list of observations and nothing more: the rows
    are never combined across the repositories a person contributed to, and people are never ordered
    by any of them (architecture.md, "Scope boundaries").
    """

    readiness: ReadinessLabel | None = None
    repository: str
    contributions: PositiveInt
    blocking: NonNegativeInt
    metrics: tuple[BehaviourMetricSummary, ...]


class ActorReadiness(EvidenceModel):
    """List the readiness of every repository one person contributed to, weightiest first.

    The section exists to answer "what is this person working in", which is why `repositories` is
    ordered by contributions descending — ties broken by repository name — rather than by label. It
    LISTS labels and never combines them: a person contributing to a red repository and a green one
    has no single readiness, and averaging the two would invent a per-person verdict this tool does
    not make.

    `actor_login` is spelled as the repository the person contributed most to spells it. Logins are
    matched case-insensitively, because a GitHub login is unique case-insensitively, so `Alice` and
    `alice` are one actor rather than two.
    """

    actor_login: str
    repositories: tuple[ActorRepositoryReadiness, ...]


class PracticeEvidenceReport(EvidenceModel):
    """Contain practice findings for the selected configured repositories.

    `actors` re-reads the same reported repositories person by person: the repository blocks answer
    how each repository is doing, and one reviewer's next question is which repositories a given
    person works in. Bot accounts carry no actor entry even though their merges stay in every
    repository's cohort — the section is for reviewing people.
    """

    organization: str
    repositories: tuple[RepositoryPracticeEvidence, ...]
    unavailable: tuple[EvidenceUnavailable, ...]
    actors: tuple[ActorReadiness, ...]


class BehaviourEvidenceCollection(EvidenceModel):
    """Contain one metric drill-down for each selected repository."""

    organization: str
    metric: str
    repositories: tuple[BehaviourEvidenceReport, ...]
    unavailable: tuple[EvidenceUnavailable, ...]


class TrendThroughput(EvidenceModel):
    """Count what one window of a series put onto the default branch, and who put it there.

    A cohort property rather than a behaviour metric: the three merge counts are ones
    `CohortSummary` already carries, taken after the same author exclusions and named as the counts
    the trend compares between periods. `merges` is the two routes added together — the same
    denominator the governance rates are measured over — so a period that merged more than the one
    before it is visible as a fact.

    `active_contributors` counts the DISTINCT PEOPLE who authored those merges by either route, and
    is the one number here that is not a count of changes. It excludes every bot account, which the
    merge counts deliberately do not: dependency automation is already out of the cohort, but
    agent-authored merges stay in it on purpose, and an agent raising pull requests is still not a
    person who became active. A period can therefore report merges and no contributor behind them,
    which is a fact about how that period's work was produced rather than a gap in the data.

    Nothing here is graded and nothing is combined across repositories — a person active in two
    repositories is one contributor in each series and is never added up into an organisation total.
    """

    merges: NonNegativeInt
    merged_pull_requests: NonNegativeInt
    direct_commits: NonNegativeInt
    active_contributors: NonNegativeInt

    def measures(self) -> tuple[tuple[str, int], ...]:
        """Pair each count with the measure name its delta is reported under, in one fixed order.

        Defined here rather than rebuilt at each call site, exactly as `SecurityAlertEvidence`
        pairs its families: the deltas and the rendered rows must agree about which name belongs to
        which count, and two separate literal tuples is how they would come to disagree — silently,
        since a rendered row that finds no delta of its name simply prints as absent.
        """
        return (
            ("merges", self.merges),
            ("merged-pull-requests", self.merged_pull_requests),
            ("direct-commits", self.direct_commits),
            ("active-contributors", self.active_contributors),
        )


class TrendMetric(EvidenceModel):
    """Report one behaviour metric's value for one window of a series.

    `summary` is the observation the `evidence` command reports for the same window from the same
    cache, computed by the same code: see architecture.md, "Trend measurement". `value` is the one
    number of it the series is compared on — a rate as a percentage, or the fixed percentile named
    in `percentile` for a distribution — so the comparison a delta makes is visible per window
    rather than reconstructed by a reader. It is None exactly when the window observed no eligible
    sample, which is why no delta can exist for it; unavailable data never becomes zero.
    """

    metric: str
    summary: RateObservation | DistributionObservation
    value: float | None = None
    percentile: Percentile | None = None


class DeltaBasis(StrEnum):
    """Say what arithmetic one delta reports, so no reader has to infer it from the number.

    A rate moves in PERCENTAGE POINTS — 81.2% to 85.0% is +3.8 points — because the relative change
    of a percentage (+4.7%) reads as an improvement four times smaller or larger depending on where
    the rate started, and both numbers would be called "per cent". A percentile and a count move in
    percentage change, where the relative figure is what a reader is asking for.
    """

    PERCENTAGE_POINTS = "percentage_points"
    PERCENTAGE_CHANGE = "percentage_change"


class TrendDelta(EvidenceModel):
    """Report how one measured value moved between the baseline and one period.

    Derived arithmetic and NOTHING else: no threshold, no boundary, no colour, and nothing here is
    read by `ReadinessPolicy`. A delta is a fact for a human conversation, not a verdict, and it
    describes ONE repository — an average of deltas across repositories is a team roll-up wearing a
    different hat, and is built nowhere. See architecture.md, "Trend measurement".

    Both measured values are always reported, so a reader can see what moved rather than only by how
    much, and `change` is None exactly when `detail` says why there is none — a baseline of zero has
    no percentage change, and reporting an infinite one would be arithmetic dressed as evidence.

    `measure` names a behaviour metric or one of the cohort counts; `percentile` says which
    percentile of a distribution was compared, and is absent for the other two shapes.
    """

    measure: str
    basis: DeltaBasis
    baseline: float
    period: float
    change: float | None = None
    unit: str | None = None
    percentile: Percentile | None = None
    detail: str | None = None

    @model_validator(mode="after")
    def validate_availability(self) -> TrendDelta:
        """Require either a computed change or the reason there is none."""
        if (self.change is None) == (self.detail is None):
            message = "a delta must carry either its change or the reason it has none"
            raise ValueError(message)
        return self


class TrendWindow(EvidenceModel):
    """Report what one window of a series observed, or the reason it observed nothing.

    `detail` sits BESIDE observations as readily as instead of them. A window whose cohort is below
    the configured minimum keeps its counts and reports no metric value, carrying the reason: a rate
    over four merges is arithmetic rather than a pattern, and reporting it in a series would show
    movement that is sampling noise. A window the cache could not answer for carries the reason
    alone — unavailable data never becomes zero.
    """

    starts_at: AwareDatetime
    ends_at: AwareDatetime
    provenance: WindowProvenance | None = None
    cohort: CohortSummary | None = None
    throughput: TrendThroughput | None = None
    metrics: tuple[TrendMetric, ...] = ()
    detail: str | None = None

    @model_validator(mode="after")
    def validate_availability(self) -> TrendWindow:
        """Require a window to observe a cohort with its counts and provenance, or nothing at all."""
        observed = (self.cohort is None, self.throughput is None, self.provenance is None)
        if len(set(observed)) != 1:
            message = "a trend window carries its cohort, throughput and provenance together or not at all"
            raise ValueError(message)
        if self.cohort is None and (self.metrics or self.detail is None):
            message = "a trend window that observed nothing carries the reason and no metric values"
            raise ValueError(message)
        return self


class TrendPeriod(TrendWindow):
    """Report one whole period of a series, numbered from the enablement instant.

    `index` counts from 1, so period 1 is the first whole period after enablement. The trailing
    PARTIAL period is never reported: a short period is not comparable with a full one, and
    including it would show every series dipping at its right-hand edge for arithmetic reasons.

    `deltas` compares this period against the baseline, and is EMPTY wherever that comparison cannot
    honestly be made — a period that observed nothing, a period whose values are suppressed, or a
    baseline that is not comparable, which is reported once on the repository. The baseline itself
    carries no deltas, being what the others are measured against, which is why they live here
    rather than on `TrendWindow`.
    """

    index: PositiveInt
    deltas: tuple[TrendDelta, ...] = ()


class RepositoryTrend(EvidenceModel):
    """Report one repository's series anchored to its enablement instant, or why it has none.

    `detail` explains an absent series — no enablement date configured, or no whole period elapsed
    since one. A repository is never dropped for lacking an anchor and never given a guessed one.

    `delta_detail` explains a series that reports its periods and compares none of them, which is
    what an unobservable baseline leaves: the periods are still evidence, while a delta without a
    stated baseline is a lie. It is reported once here rather than repeated on every period, because
    it is one fact about the baseline and not several about the periods.

    `alert_observations` is the one block here that is NOT a window: the open-alert counts observed
    between the start of the baseline and the end of the last whole period, at the instants they were
    observed. They are deliberately not mapped onto the periods beside them — nothing is interpolated
    onto a boundary and nothing is zero-filled between two observations, because a sampled series
    that pretends to be continuous invents evidence at every instant nobody looked.
    """

    repository: str
    enablement_at: AwareDatetime | None = None
    baseline: TrendWindow | None = None
    periods: tuple[TrendPeriod, ...] = ()
    alert_observations: tuple[AlertObservation, ...] = ()
    detail: str | None = None
    delta_detail: str | None = None

    @model_validator(mode="after")
    def validate_availability(self) -> RepositoryTrend:
        """Require an anchored series, or the reason there is none to report."""
        if self.enablement_at is None and (self.baseline is not None or self.periods):
            message = "a repository with no enablement instant has no series to anchor"
            raise ValueError(message)
        if not self.periods and self.detail is None:
            message = "a repository reporting no period must carry the reason"
            raise ValueError(message)
        if not self.periods and self.alert_observations:
            # The observations are selected over the range the reported windows cover, so a
            # repository with no series has no range to have selected them in.
            message = "a repository reporting no period has no range to observe alerts over"
            raise ValueError(message)
        if self.delta_detail is not None and any(period.deltas for period in self.periods):
            message = "a repository reporting why it has no delta cannot carry one"
            raise ValueError(message)
        return self


class TrendReport(EvidenceModel):
    """Contain one series for each configured repository.

    Like every other report here it LISTS repositories and combines nothing across them: an average
    of deltas is a team roll-up wearing a different hat. See architecture.md, "Scope boundaries".
    """

    organization: str
    period_days: PositiveInt
    repositories: tuple[RepositoryTrend, ...]


class RepositoryInventoryItem(EvidenceModel):
    """Associate observed repository state with its configured team.

    `codeowners`, `maintenance`, `sonar`, `sonar_project` and `open_pull_requests` default to None so
    a row stored before they existed still parses, read back as "not collected when repository state
    was stored" — never as an absent file, an empty branch, a project with nothing measured or a
    repository with nothing open.

    The two SonarCloud fields are a pair and are read as one: `sonar_project` says which project this
    repository was attributed to and how, `sonar` says what was measured for it. A resolved project
    whose measures could not be read carries the first without the second, and a repository no
    project is mapped to carries a `sonar_project` holding only the reason — which is an answer, and
    is what keeps it apart from a row collected before this source existed, where both are None.
    """

    team_identifier: str
    repository: RepositoryMetadata
    merge_gate: MergeGateEvidence | None = None
    security: SecurityAlertEvidence | None = None
    codeowners: CodeownersEvidence | None = None
    maintenance: MaintenanceEvidence | None = None
    sonar: SonarMeasures | None = None
    sonar_project: SonarProjectResolution | None = None
    open_pull_requests: OpenPullRequestSnapshot | None = None
    collection: BehaviourProvenance | None = None


class RepositoryCollectionCost(EvidenceModel):
    """Report what collecting one repository cost ONE RUN.

    A measurement of the run, not a property of the repository: `requests` counts every call issued
    for it — GitHub and SonarCloud together, since one repository's collection is one unit of work —
    and `elapsed_seconds` the monotonic time they took, so a second run over the same
    evidence reports smaller figures because the cache served most of it. That is why this appears in
    the collection report — a per-run summary — and deliberately nowhere in the evidence report,
    which is a reproducible contract.

    Reported for every configured repository, including one whose collection failed: the calls and
    the seconds spent before a failure are exactly the cost a reader is looking for.
    """

    repository: str
    requests: NonNegativeInt
    elapsed_seconds: NonNegativeFloat


class RepositoryInventoryIssue(EvidenceModel):
    """Describe unavailable repository evidence."""

    team_identifier: str
    repository: str
    evidence: EvidenceKind
    reason: AvailabilityReason
    detail: str


class RepositoryInventory(EvidenceModel):
    """Report current repository state and what one collection window fetched or reused.

    `costs` is ordered SLOWEST FIRST rather than following `repositories`, because the question it
    answers is which repository dominates a run. It sits beside `repositories` rather than on each
    item so that a repository whose collection failed — and which therefore has no item — still
    reports what the attempt cost.
    """

    status: CollectionStatus
    organization: str
    collected_at: AwareDatetime
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    repositories: tuple[RepositoryInventoryItem, ...]
    failures: tuple[RepositoryInventoryIssue, ...]
    costs: tuple[RepositoryCollectionCost, ...] = ()


class StoredRepositoryState(EvidenceModel):
    """Pair one repository's stored current state with the instant it was observed.

    `fetched_at` is stored beside the state rather than inside it, because it belongs to the
    observation and not to the repository.
    """

    fetched_at: AwareDatetime
    state: RepositoryInventoryItem
