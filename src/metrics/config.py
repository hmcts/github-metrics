"""Load and validate metrics collection configuration."""

import re
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveInt,
    ValidationError,
    model_validator,
)

from metrics.domain import FindingSeverity
from metrics.window import parse_instant


def normalise_enablement_instant(value: object) -> object:
    """Parse one enablement instant exactly as a reporting window edge is parsed.

    YAML resolves an unquoted `2026-01-05` to a `date` and `2026-01-05T09:00:00Z` to a `datetime`
    before pydantic sees either, so both are rendered back to ISO 8601 text and handed to the one
    implementation in `window.py`. That keeps a single rule for every instant in the system — bare
    date means UTC midnight, a naive datetime means UTC — rather than a second rule that could drift
    from the window edges the enablement date is compared against.

    Anything that is not text or a YAML date is rejected here rather than passed to pydantic, which
    would otherwise read a bare number as a Unix timestamp — a second instant rule, arriving by
    accident, that no configuration file should be able to reach.
    """
    if isinstance(value, date):  # `datetime` is a `date`, so both arrive here first.
        value = value.isoformat()
    if isinstance(value, str):
        return parse_instant(value)
    # A `ValueError` deliberately, not the `TypeError` the type check suggests: pydantic converts
    # only `ValueError` into a reported validation error, and a `TypeError` would escape as a crash.
    message = f"expected a date or datetime such as 2026-08-01 or 2026-08-01T14:30:00Z: {value!r}"
    raise ValueError(message)


EnablementInstant = Annotated[datetime, BeforeValidator(normalise_enablement_instant)]


class ConfigurationError(ValueError):
    """Report invalid metrics configuration."""


class ConfigurationModel(BaseModel):
    """Provide shared immutable configuration behaviour."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class LookbackConfiguration(ConfigurationModel):
    """Define evidence collection windows."""

    operational_days: PositiveInt = 90
    # Behaviour is a pattern, so a window may legitimately reach back the life of a project.
    maximum_days: PositiveInt = 365
    mutable_hours: PositiveInt = 6
    # Measured from an open pull request's last update, not from when it was opened.
    stale_open_days: PositiveInt = 14
    # How old the last collection may be before reporting says so. Collection runs weekly, so eight
    # days is one missed run rather than one missed day: a warning raised the morning after every
    # run would say nothing about whether the figures can still be trusted.
    stale_collection_days: PositiveInt = 8


class TeamConfiguration(ConfigurationModel):
    """Define repositories and GitHub teams for one business team."""

    identifier: Annotated[str, Field(min_length=1)]
    display_name: Annotated[str, Field(min_length=1)]
    github_team_slugs: tuple[str, ...] = ()
    repositories: Annotated[tuple[str, ...], Field(min_length=1)]


class CohortConfiguration(ConfigurationModel):
    """Define which pull-request authors the reported cohort covers."""

    # Dependency bots raise mechanical version bumps; agent-authored code stays in the cohort deliberately.
    excluded_authors: tuple[str, ...] = ("renovate", "dependabot")


class TrivialityConfiguration(ConfigurationModel):
    """Define the size below which a change is treated as trivial.

    A one-line configuration fix merged without independent review is a different fact from a
    three-file feature merged the same way, and findings are read very differently once separated.
    """

    maximum_lines: PositiveInt = 10
    maximum_files: PositiveInt = 1


class ReadinessThresholds(ConfigurationModel):
    """Define where one observed rate stops being green and stops being amber."""

    green_percentage: Annotated[float, Field(ge=0, le=100)]
    amber_percentage: Annotated[float, Field(ge=0, le=100)]

    @model_validator(mode="after")
    def validate_boundaries(self) -> Self:
        """Require the green boundary to sit at or above the amber one."""
        if self.green_percentage < self.amber_percentage:
            message = "the green percentage may not be below the amber percentage"
            raise ValueError(message)
        return self


class DistributionThreshold(ConfigurationModel):
    """Define where one observed percentile stops being green.

    A distribution measures cost, not compliance, so the comparison runs the opposite way from
    `ReadinessThresholds`: smaller is better, and the boundary is a maximum a value must stay at or
    below rather than a minimum it must reach.

    ONE boundary, not a green/amber pair, because a flow signal can never impose red. How long a
    change waited and how large it was are costs a team carries, not evidence that anything
    ungoverned reached the default branch, and red is reserved for the latter — a repository merging
    without review must not be indistinguishable from one that reviews everything slowly. The pair
    this replaced set each amber boundary at exactly double its green one, which was arithmetic
    standing in for a policy nobody had made.
    """

    maximum: Annotated[float, Field(gt=0)]


class UnreviewedSubstantialThresholds(ConfigurationModel):
    """Define how much unreviewed substantial merging a repository may do before it is reported.

    Two allowances rather than one, because a bare count says nothing on its own: two unreviewed
    merges out of 246 is a pair of lapses in a repository that reviews almost everything, and two
    out of 20 is a habit. `maximum_percentage` is therefore the boundary that scales with how much
    a team merges, and `maximum_count` is an absolute floor beneath it — a repository is within the
    policy when EITHER allowance forgives it, so a small cohort is not condemned by arithmetic and a
    large one cannot dilute a real pattern away.

    The 1% default is a policy choice to argue with, not a measurement: it was set after every
    assessable HMCTS repository tripped the previous fixed maximum of zero, which made the condition
    fire universally and therefore say nothing about any repository in particular. Set
    `maximum_percentage: 0` and `maximum_count: 0` to restore that absolute reading.
    """

    maximum_count: NonNegativeInt = 0
    maximum_percentage: Annotated[float, Field(ge=0, le=100)] = 1


class AssessmentConfiguration(ConfigurationModel):
    """Configure the readiness assessment.

    Every number here is a policy judgement rather than a measurement, which is why it is
    configuration and never hardcoded: an organisation must be able to argue with a threshold without
    waiting for a release. Defaults are stated openly as policy choices, not presented as facts.
    The gate vetoes are deliberately NOT configurable — they are the definition of the question.
    """

    enabled: bool = True
    # Below this many merges a rate is arithmetic, not a pattern, so nothing is graded.
    # It counts direct commits as well as merged pull requests, because a repository whose work
    # bypasses pull requests would otherwise escape grading by having too few of them.
    minimum_merges: PositiveInt = 10
    independent_review_coverage: ReadinessThresholds = Field(
        default=ReadinessThresholds(green_percentage=90, amber_percentage=70),
        alias="independent-review-coverage",
    )
    # Graded beside review coverage, not folded into it: an approval is the reviewer's explicit
    # sign-off, and a team that reviews without ever approving leaves no record of who accepted the change.
    approval_coverage: ReadinessThresholds = Field(
        default=ReadinessThresholds(green_percentage=90, amber_percentage=70),
        alias="approval-coverage",
    )
    checks_passing_at_merge: ReadinessThresholds = Field(
        default=ReadinessThresholds(green_percentage=90, amber_percentage=70),
        alias="checks-passing-at-merge",
    )
    # Flow signals graded by cost rather than compliance: how large a change is, and how long it
    # waited. Each grades a single fixed percentile of its distribution (the 75th for size, because
    # a long tail of large changes is the failure mode; the median for the two waiting times), a
    # policy choice that lives in `assessment.py` rather than in configuration, since which
    # percentile answers the question does not vary by organisation the way the boundary does. The
    # maxima match the reference tool. Each caps at amber whatever the shortfall — see
    # `DistributionThreshold` — so no flow signal can hold a repository at red on its own.
    pull_request_size: DistributionThreshold = Field(
        default=DistributionThreshold(maximum=400),
        alias="pull-request-size",
    )
    merge_cycle_time: DistributionThreshold = Field(
        default=DistributionThreshold(maximum=24),
        alias="merge-cycle-time",
    )
    time_to_first_review: DistributionThreshold = Field(
        default=DistributionThreshold(maximum=8),
        alias="time-to-first-review",
    )
    unreviewed_substantial_merges: UnreviewedSubstantialThresholds = Field(
        default=UnreviewedSubstantialThresholds(),
        alias="unreviewed-substantial-merges",
    )
    # A caution boundary, not a green/amber pair: no defensible label-deciding threshold exists yet,
    # so a shallow rate is something to weigh rather than something that holds the label down.
    review_depth_minimum_percentage: Annotated[float, Field(ge=0, le=100)] = Field(
        default=50,
        alias="review-depth-minimum-percentage",
    )


class TraceabilityConfiguration(ConfigurationModel):
    """Define what counts as a well-described, traceable pull request.

    Neither number decides the readiness label: a description is a documentation habit, not
    evidence a change was governed, and no threshold here has an owner. `reference_patterns` is a
    LIST because HMCTS traces work through both a GitHub issue and a Jira key, and the next
    organisation reached for will use neither.
    """

    minimum_description: PositiveInt = 30
    reference_patterns: tuple[str, ...] = (r"#\d+", r"[A-Z][A-Z0-9]+-\d+")

    @model_validator(mode="after")
    def validate_reference_patterns(self) -> Self:
        """Reject an unusable pattern at load time rather than at every pull request it scans."""
        for pattern in self.reference_patterns:
            try:
                re.compile(pattern)
            except re.error as exception:
                message = f"invalid reference pattern {pattern!r}: {exception}"
                raise ValueError(message) from exception
        return self


class PracticeRuleConfiguration(ConfigurationModel):
    """Configure one independently evaluated engineering-practice rule."""

    enabled: bool = True
    severity: FindingSeverity = FindingSeverity.HIGH
    minimum_occurrences: PositiveInt = 1
    excluded_logins: tuple[str, ...] = ()


class PracticeConfiguration(ConfigurationModel):
    """Configure the available engineering-practice findings."""

    unreviewed_merge: PracticeRuleConfiguration = Field(
        default=PracticeRuleConfiguration(),
        alias="unreviewed-merge",
    )


class Configuration(ConfigurationModel):
    """Define one metrics collection environment."""

    version: Literal[1]
    organization: Annotated[str, Field(min_length=1)]
    database: Path
    lookback: LookbackConfiguration = LookbackConfiguration()
    cohort: CohortConfiguration = CohortConfiguration()
    triviality: TrivialityConfiguration = TrivialityConfiguration()
    assessment: AssessmentConfiguration = AssessmentConfiguration()
    traceability: TraceabilityConfiguration = TraceabilityConfiguration()
    practices: PracticeConfiguration = PracticeConfiguration()
    excluded_repositories: tuple[str, ...] = ()
    # Optional at the schema, required by the commands whose subject is the cohort (see
    # `COHORT_COMMANDS` in cli.py). `map-sonar` resolves every project a SonarCloud organisation
    # lists and `prune` deletes stale cache rows: neither is about any repository a team owns, so
    # neither should oblige a team file to be layered in to say something it never reads. A run that
    # DOES report the cohort refuses an empty one there, where the message can name the command.
    teams: tuple[TeamConfiguration, ...] = ()
    # When agentic tooling was turned on for a repository. The tool cannot observe it — GitHub cannot
    # be asked — so it is an input fact like every other policy input. See architecture.md
    # "Trend measurement": a repository with no date gets no series rather than a guessed anchor.
    enablement: dict[str, EnablementInstant] = Field(default_factory=dict)
    # The SonarCloud organisation key, which is usually the GitHub organisation name and at HMCTS is
    # exactly it. Left `None` rather than defaulted to the same text so the common case is not
    # restated in every configuration file; read through `sonar_organization_name`.
    sonar_organization: str | None = None
    # The answer of last resort for a repository whose project the stored map cannot settle: an
    # explicit configured repository name to SonarCloud project key. See architecture.md.
    sonar_projects: dict[str, str] = Field(default_factory=dict)

    @property
    def sonar_organization_name(self) -> str:
        """Return the SonarCloud organisation to read, falling back to the GitHub organisation."""
        return self.sonar_organization or self.organization

    @model_validator(mode="after")
    def validate_sonar_projects(self) -> Self:
        """Reject a project override that names no configured repository, or names no project.

        The unconfigured-repository rule is `validate_enablement`'s, for its reason: a typo in a
        repository name would otherwise resolve nothing and be reported as "no SonarCloud project
        mapped" — indistinguishable from having forgotten the override entirely — so the mistake is
        named at load time instead.

        An empty key is rejected for the same reason from the other side. Resolution treats an
        override as the answer that short-circuits every other step, so a blank one would silently
        mean "unresolved" while reading as a decision somebody made.
        """
        blank = sorted(repository for repository, project in self.sonar_projects.items() if not project.strip())
        if blank:
            message = f"sonar project keys may not be empty: {', '.join(blank)}"
            raise ValueError(message)

        # The blank-key rule above holds whatever was configured: it is a fact about the override
        # itself. Only the cross-check below is conditional, and only on there being a cohort to
        # check against — see `validate_enablement` for the whole of that reasoning.
        if not self.teams:
            return self
        unconfigured = sorted(set(self.sonar_projects) - set(configured_repositories(self)))
        if unconfigured:
            message = f"sonar projects must name a configured repository: {', '.join(unconfigured)}"
            raise ValueError(message)
        return self

    @model_validator(mode="after")
    def validate_enablement(self) -> Self:
        """Reject an enablement date for a repository this configuration does not own.

        A typo in a repository name would otherwise anchor nothing and be reported as "no enablement
        date configured" — the same as having forgotten it entirely — so the mistake is named at load
        time instead.

        Skipped where no team is configured, because the rule is a comparison and one side of it is
        absent: a policy file read on its own for `map-sonar` or `prune` owns no repository, so every
        name in it would be "unconfigured" and the load would fail over a key neither command reads.
        Nothing is lost — an enablement date only ever affects a run that reports the cohort, and
        every such run loads a team file and is checked here.
        """
        if not self.teams:
            return self
        unconfigured = sorted(set(self.enablement) - set(configured_repositories(self)))
        if unconfigured:
            message = f"enablement dates must name a configured repository: {', '.join(unconfigured)}"
            raise ValueError(message)
        return self

    @model_validator(mode="after")
    def validate_ownership(self) -> Self:
        """Ensure team identifiers and repository ownership are unique."""
        identifiers = tuple(team.identifier for team in self.teams)
        if len(set(identifiers)) != len(identifiers):
            message = "team identifiers must be unique"
            raise ValueError(message)

        repositories = tuple(repository for team in self.teams for repository in team.repositories)
        duplicates = sorted(repository for repository in set(repositories) if repositories.count(repository) > 1)
        if duplicates:
            message = f"repositories may belong to only one team: {', '.join(duplicates)}"
            raise ValueError(message)

        owned_exclusions = sorted(set(repositories) & set(self.excluded_repositories))
        if owned_exclusions:
            message = f"excluded repositories may not have an owner: {', '.join(owned_exclusions)}"
            raise ValueError(message)
        return self


def owned_repositories(configuration: Configuration) -> tuple[tuple[str, str], ...]:
    """Return every configured repository beside the team that owns it, in the reporting order.

    Sorted by team identifier and then repository name rather than left in whatever order the
    configuration file happens to list them. At one repository the difference was invisible; at
    fourteen, moving a `teams:` block reorders an entire report and makes two runs undiffable, which
    hides the change a reader was looking for. Ownership is unique — `validate_ownership` enforces
    it — so the pair is a total order and no repository can appear twice.

    This is ordering, not aggregation: nothing here groups repositories or reduces a team's
    repositories to one anything. See architecture.md "Scope boundaries".
    """
    return tuple(
        sorted((team.identifier, repository) for team in configuration.teams for repository in team.repositories),
    )


def configured_repositories(configuration: Configuration) -> tuple[str, ...]:
    """Return every configured repository name in the reporting order."""
    return tuple(repository for _, repository in owned_repositories(configuration))


def enablement_instants(configuration: Configuration) -> dict[str, datetime | None]:
    """Map every configured repository, in the reporting order, to its enablement instant or `None`.

    Every configured repository appears, including the ones without a date: a repository missing an
    anchor is reported with that reason and no series, never silently dropped and never defaulted to
    an instant nobody chose.
    """
    return {
        repository: configuration.enablement.get(repository) for repository in configured_repositories(configuration)
    }


def repository_owners(configuration: Configuration) -> dict[str, str]:
    """Map each configured repository to the identifier of the team that owns it."""
    return {repository: identifier for identifier, repository in owned_repositories(configuration)}


def describe_validation_error(paths: tuple[Path, ...], exception: ValidationError) -> str:
    """Report each rejected key as `location: problem`, naming the files the document was read from.

    pydantic's own rendering prints the entire input document beside the first offending key and a
    link to its error index, which buries the two things a reader needs: WHICH key, and where it was
    looked for. The rendering is the one used for rejected GitHub responses in `inventory.py`.

    The file list is part of the message because `--config` is repeatable and the files are read as
    one document: a missing `teams:` almost always means the team file was not given, not that the
    policy file naming it is wrong, and the message cannot say so without naming what was read.
    """
    problems = ", ".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
        for error in exception.errors(include_url=False, include_input=False)
    )
    return f"{problems} (read from {', '.join(str(path) for path in paths)})"


def load_configuration(*paths: Path) -> Configuration:
    """Load a versioned metrics configuration from YAML held in one file or split across several.

    Several files are concatenated and parsed as ONE document, so how the text was stored is settled
    before anything reads it: the schema, and every rule and default below it, is unconcerned with
    whether the configuration arrived as one file or twenty. The split exists so the policy every
    report shares — assessment thresholds, practices, lookbacks — is written once and paired with
    whichever team file a run is about, rather than restated per team set and left to drift.

    Nothing here treats a file as a configuration in its own right, so a file holding only `teams:`
    is not a partial configuration to be checked; it is part of the text of the one configuration
    being read. A key given twice is resolved by YAML as it always was, the last occurrence winning.
    """
    try:
        # Joined on a newline rather than concatenated directly: a file whose last line has no
        # newline would otherwise run into the next file's first line and change what both mean.
        document = "\n".join(path.read_text(encoding="utf-8") for path in paths)
        configuration = Configuration.model_validate(yaml.safe_load(document))
    # Caught before the `ValueError` clause below, which it would otherwise be swallowed by: a
    # rejected key is the one invalid-configuration case with a location to report, and reporting it
    # as `str(exception)` is what printed the whole input document instead of the key's name.
    except ValidationError as exception:
        raise ConfigurationError(describe_validation_error(paths, exception)) from exception
    # `ValueError` still covers PyYAML's own scalar constructors, which raise a bare `ValueError`
    # for an impossible timestamp such as `2026-13-05` before pydantic ever sees the value, and it
    # is kept beside the unreadable file and the unparseable document: all three are invalid
    # configuration and must be reported as such rather than escaping the loader as a crash.
    except (OSError, ValueError, yaml.YAMLError) as exception:
        raise ConfigurationError(str(exception)) from exception

    if configuration.database.is_absolute():
        return configuration
    # Anchored to the first file, which is the one a relative path was written beside: the files
    # after it extend that configuration rather than relocating it.
    return configuration.model_copy(update={"database": paths[0].parent / configuration.database})
