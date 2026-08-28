"""Command-line entry point"""

import logging
import sys
from argparse import ArgumentParser, Namespace
from collections import Counter
from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from os import environ
from pathlib import Path

from requests import Session

from metrics.analysis import review_state_counts
from metrics.assessment import readiness_policy
from metrics.behaviour import collect_window
from metrics.behaviour_metrics import behaviour_metric, behaviour_metric_identifiers, behaviour_metrics
from metrics.config import (
    Configuration,
    ConfigurationError,
    configured_repositories,
    load_configuration,
    repository_owners,
)
from metrics.doctor import run_doctor
from metrics.domain import (
    BehaviourEvidenceCollection,
    BehaviourEvidenceReport,
    CollectionStatus,
    EvidenceUnavailable,
    OpenPullRequestReport,
    PracticeEvidenceReport,
    ReportingWindow,
    SonarProjectMapping,
    StoredSonarMapping,
    TrendReport,
)
from metrics.evidence import (
    RepositoryEvidence,
    cached_repository_evidence,
    collected_repository_evidence,
    offline_open_pull_request_report,
    open_pull_request_report,
    stored_reports,
    stored_repository_state,
)
from metrics.github import GitHubClient, GitHubError
from metrics.inventory import SonarSource, collect_inventory
from metrics.render import RepositoryDrillDown, render_report, render_trend_report
from metrics.rules import configured_rules
from metrics.sonar import (
    SonarClient,
    SonarError,
    SonarMappingOutcome,
    SonarProject,
    SonarResolutionAttempt,
    StoredProjectMap,
    search_pacer,
    sonar_to_github,
)
from metrics.storage import (
    StorageError,
    load_sonar_mapping,
    observation_database,
    prune_cache,
    record_alert_observations,
    record_repository_state,
    record_sonar_mapping,
)
from metrics.trend import SeriesRequest, series_status, trend_report
from metrics.window import parse_instant, resolve_window

INCOMPLETE_RUN = 3
"""Exit status for a run that produced some of the evidence it was asked for, but not all of it.

Its own status because the two outcomes it sits between are not interchangeable at fourteen
repositories: `0` says every configured repository was observed, `1` says nothing usable came back,
and neither describes twelve of fourteen. A partial run exiting `0` would let every downstream
denominator be read as covering the whole population when repositories are missing from it, and
exiting `1` would throw away evidence that was collected and is worth reporting.

`3` rather than `2`: `argparse` already exits `2` for a usage error, and a caller must be able to
tell "you invoked me wrongly" from "I ran, and part of the org would not answer".
"""

COHORT_COMMANDS = frozenset({"collect", "doctor", "evidence", "trend"})
"""Every command whose subject is the configured repository cohort, and which therefore needs one.

`map-sonar` and `prune` are absent deliberately: the first resolves every project a SonarCloud
organisation lists and the second deletes stale cache rows, and neither reads a team, a repository
list, or anything derived from either. Requiring a `teams:` section of them — which the schema did
until the configuration was split into a policy file and a team file — obliged a team file to be
layered in to satisfy a validator, not to be used, and reported its absence as invalid configuration
when nothing about the run was invalid. See config.py's `teams` for the other half of this.
"""


def run_status(status: CollectionStatus) -> int:
    """Map one run's completeness onto an exit status a caller can branch on."""
    if status is CollectionStatus.COMPLETE:
        return 0
    return 1 if status is CollectionStatus.FAILED else INCOMPLETE_RUN


def add_window_arguments(parser: ArgumentParser) -> None:
    """Add the shared half-open UTC window options to one subcommand parser."""
    parser.add_argument("--from", dest="starts_at", help="start of the window, inclusive: a UTC date or datetime")
    parser.add_argument("--to", dest="ends_at", help="end of the window, exclusive: a UTC date or datetime")
    parser.add_argument("--days", type=int, help="span this many days, ending at the most recent UTC midnight")
    parser.add_argument("--maximum-days", type=int, help="raise the configured maximum window span")


def add_configuration_argument(parser: ArgumentParser) -> None:
    """Add the repeatable configuration option to one subcommand parser."""
    parser.add_argument(
        "--config",
        type=Path,
        action="append",
        required=True,
        metavar="PATH",
        help="path to the YAML configuration; repeat to layer files, later files winning",
    )


def parse_arguments() -> Namespace:
    """Build the parser and parse the given command-line arguments."""
    parser = ArgumentParser(
        prog="metrics",
        description="Collect evidence for SDLC maturity and AI enablement decisions.",
    )
    parser.add_argument("--logging", default="info", help="set the logging level")
    commands = parser.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser("doctor", help="validate configuration and GitHub access")
    add_configuration_argument(doctor)
    collect = commands.add_parser("collect", help="collect repository inventory")
    add_configuration_argument(collect)
    add_window_arguments(collect)
    prune = commands.add_parser("prune", help="delete cached intervals that have not been used recently")
    add_configuration_argument(prune)
    prune.add_argument("--days", type=int, default=30, help="delete cached intervals unused for this many days")
    # Its own command rather than part of `collect`, because it is paced against GitHub's
    # 30-per-minute commit search: 289 projects cannot be resolved inside a collection run, and
    # nothing about the answer changes between analyses. Run it by hand, periodically.
    map_sonar = commands.add_parser(
        "map-sonar",
        help="resolve each SonarCloud project to the GitHub repository it analyses, and store the map",
    )
    add_configuration_argument(map_sonar)
    evidence = commands.add_parser("evidence", help="explain cached behaviour evidence without GitHub access")
    add_configuration_argument(evidence)
    evidence.add_argument("--repository", help="limit results to one configured repository")
    add_window_arguments(evidence)
    evidence.add_argument(
        "--offline",
        action="store_true",
        help="never contact GitHub, and report a repository the cache does not cover as unavailable",
    )
    evidence.add_argument("--metric", choices=behaviour_metric_identifiers(), help="show one raw metric")
    evidence.add_argument(
        "--include-identities",
        action="store_true",
        help="include raw PR, author, and reviewer references for a metric",
    )
    evidence.add_argument(
        "--format",
        dest="output_format",
        choices=("json", "report"),
        default="json",
        help="emit the machine-readable contract, or the same evidence as a readable report",
    )
    trend = commands.add_parser("trend", help="report each repository's periods since it was enabled")
    add_configuration_argument(trend)
    # Four whole weeks by default, so every period holds the same mix of weekdays and a series
    # cannot move because one period caught an extra Monday.
    trend.add_argument("--period-days", type=int, default=28, help="span each period this many days")
    trend.add_argument("--periods", type=int, help="report at most this many whole periods since enablement")
    trend.add_argument(
        "--offline",
        action="store_true",
        help="never contact GitHub, and refuse a period the cache does not cover",
    )
    trend.add_argument(
        "--format",
        dest="output_format",
        choices=("json", "report"),
        default="json",
        help="emit the machine-readable contract, or the same series as a readable report",
    )

    return parser.parse_args()


def requested_window(configuration: Configuration, options: Namespace, reference: datetime) -> ReportingWindow:
    """Resolve the window one command was asked for and enforce the configured maximum span."""
    window = resolve_window(
        None if options.starts_at is None else parse_instant(options.starts_at),
        None if options.ends_at is None else parse_instant(options.ends_at),
        options.days,
        configuration.lookback.operational_days,
        reference,
    )
    maximum = configuration.lookback.maximum_days if options.maximum_days is None else options.maximum_days
    if window.ends_at - window.starts_at > timedelta(days=maximum):
        message = f"a {(window.ends_at - window.starts_at).days}-day span exceeds the {maximum}-day maximum"
        raise ValueError(message)
    return window


def unusable_request(configured: tuple[str, ...], options: Namespace) -> str | None:
    """Return why the requested evidence options cannot be satisfied."""
    if options.repository is not None and options.repository not in configured:
        return f"Repository is not configured: {options.repository}"
    if options.metric is None and options.include_identities:
        return "--include-identities requires --metric"
    return None


def evidence_report(
    configuration: Configuration,
    options: Namespace,
    evidence: tuple[RepositoryEvidence, ...],
    unavailable: tuple[EvidenceUnavailable, ...],
    open_pull_requests: Mapping[str, OpenPullRequestReport],
) -> BehaviourEvidenceReport | BehaviourEvidenceCollection | PracticeEvidenceReport:
    """Build the report for the requested evidence mode."""
    if options.metric is None:
        rules = configured_rules(configuration)
        policy = readiness_policy(configuration)
        # One load per repository, projected into every block that reads it: the merge gate, the
        # security alerts, CODEOWNERS presence, maintenance and the SonarCloud measures are five
        # views of the same stored row.
        stored = {item.repository: stored_repository_state(configuration, item.repository) for item in evidence}
        return PracticeEvidenceReport(
            organization=configuration.organization,
            repositories=tuple(
                item.practices(
                    rules,
                    policy,
                    open_pull_requests[item.repository],
                    stored_reports(stored[item.repository]),
                )
                for item in evidence
            ),
            unavailable=unavailable,
        )
    metric = behaviour_metric(options.metric, configuration.traceability)
    reports = tuple(item.metric(metric, include_identities=options.include_identities) for item in evidence)
    if options.repository is not None:
        return reports[0]
    return BehaviourEvidenceCollection(
        organization=configuration.organization,
        metric=metric.identifier,
        repositories=reports,
        unavailable=unavailable,
    )


def repository_drill_down(configuration: Configuration, evidence: RepositoryEvidence) -> RepositoryDrillDown:
    """Build the metric aggregates and review counts the readable report shows beside the findings.

    Computed for the report format alone. Nothing here reaches the JSON, and every figure is one a
    `--metric` drill-down over the same window already emits, so the two renderings cannot diverge.
    """
    return RepositoryDrillDown(
        behaviour=tuple(
            evidence.metric(metric, include_identities=False)
            for metric in behaviour_metrics(configuration.traceability)
        ),
        review_states=review_state_counts(evidence.pull_requests),
    )


def evidence_output(
    configuration: Configuration,
    options: Namespace,
    report: BehaviourEvidenceReport | BehaviourEvidenceCollection | PracticeEvidenceReport,
    evidence: tuple[RepositoryEvidence, ...],
) -> str:
    """Render one built report in the requested output format."""
    if options.output_format == "json":
        return report.model_dump_json(indent=2, exclude_none=True)
    drill_downs = (
        {item.repository: repository_drill_down(configuration, item) for item in evidence}
        if options.metric is None
        else {}
    )
    return render_report(report, drill_downs, repository_owners(configuration))


def gather_evidence(
    configuration: Configuration,
    options: Namespace,
    client: GitHubClient | None,
    window: ReportingWindow,
    reference: datetime,
) -> tuple[RepositoryEvidence | EvidenceUnavailable, ...]:
    """Report every selected repository, collecting missing history unless there is no client.

    `client` is None exactly when the run is offline, which is what makes the cache the only source.

    Selection follows `configured_repositories` — team identifier, then repository name — so both
    renderings order repositories the same way and neither is reordered by an edit to the
    configuration file.
    """
    selected = (options.repository,) if options.repository is not None else configured_repositories(configuration)
    if client is None:
        return tuple(cached_repository_evidence(configuration, repository, window) for repository in selected)
    return tuple(
        collected_repository_evidence(configuration, client, repository, window, reference) for repository in selected
    )


def gather_open_pull_requests(
    configuration: Configuration,
    client: GitHubClient | None,
    repositories: tuple[str, ...],
    window: ReportingWindow,
    reference: datetime,
) -> dict[str, OpenPullRequestReport]:
    """Fetch each selected repository's open pull-request state fresh; refuse it under --offline.

    This state is never cached (see architecture.md), so it is fetched independently of the
    windowed merge evidence `gather_evidence` collects or reads back from the cache. It shares the
    client with that phase rather than building a second one: the rate-limit budget is recorded on
    the client, so a fresh one would start believing there was none and could spend past the
    configured reserve before its first response taught it otherwise.
    """
    if client is None:
        return {repository: offline_open_pull_request_report() for repository in repositories}
    return {
        repository: open_pull_request_report(client, configuration, repository, window, reference)
        for repository in repositories
    }


def prune_evidence_cache(configuration: Configuration, options: Namespace) -> int:
    """Delete cached intervals unused for the requested number of days."""
    try:
        removed = prune_cache(configuration.database, datetime.now(UTC) - timedelta(days=options.days))
    except StorageError as exception:
        logging.error("Prune failed: %s", exception)
        return 1
    logging.info("Removed %s unused cached intervals", removed)
    return 0


def emit_evidence(configuration: Configuration, options: Namespace) -> int:
    """Emit configured practice findings or an optional raw metric drill-down.

    A repository the cache cannot answer for is reported under `unavailable` and left out of the
    numbers, whether the run was offline or collecting: the two modes fail the same way, so a
    repository GitHub refused during collection does not also make every cached repository beside it
    unreportable. Only a run where NOTHING could be reported refuses outright.
    """
    unusable = unusable_request(configured_repositories(configuration), options)
    if unusable is not None:
        logging.error(unusable)
        return 1
    if not options.offline and environ.get("GH_TOKEN") is None:
        logging.error("GH_TOKEN is not set; use --offline to report cached evidence only")
        return 1
    reference = datetime.now(UTC)
    try:
        window = requested_window(configuration, options, reference)
    except ValueError as exception:
        logging.error("Unusable reporting window: %s", exception)
        return 1

    # One session and one client for both phases of the run, so the second does not discard the
    # rate-limit budget the first learned. Neither phase contacts GitHub when the run is offline.
    # ExitStack rather than `with Session()`: an offline run must construct no session at all, which
    # `test_evidence_runs_offline_without_token_or_session` pins.
    with ExitStack() as stack:
        client = None if options.offline else GitHubClient(environ["GH_TOKEN"], stack.enter_context(Session()))
        loaded = gather_evidence(configuration, options, client, window, reference)
        evidence = tuple(item for item in loaded if isinstance(item, RepositoryEvidence))
        unavailable = tuple(item for item in loaded if isinstance(item, EvidenceUnavailable))
        if unavailable and not evidence:
            logging.error(
                "Evidence unavailable for all %s repositories, first %s: %s",
                len(unavailable),
                unavailable[0].repository,
                unavailable[0].detail,
            )
            return 1
        if unavailable:
            # Named, not just counted: the report carries the same list, but a run whose output was
            # redirected to a file shows nothing on the terminal except this.
            logging.warning(
                "Evidence unavailable for %s of %s repositories, omitted from the numbers: %s",
                len(unavailable),
                len(unavailable) + len(evidence),
                ", ".join(item.repository for item in unavailable),
            )

        open_pull_requests = (
            {}
            if options.metric is not None
            else gather_open_pull_requests(
                configuration,
                client,
                tuple(item.repository for item in evidence),
                window,
                reference,
            )
        )
    report = evidence_report(configuration, options, evidence, unavailable, open_pull_requests)
    sys.stdout.write(f"{evidence_output(configuration, options, report, evidence)}\n")
    # Anything unavailable here is one repository among several that did report, because the case
    # where nothing reported has already returned above.
    return INCOMPLETE_RUN if unavailable else 0


def trend_output(configuration: Configuration, options: Namespace, report: TrendReport) -> str:
    """Render one series in the requested output format.

    The JSON is the contract and the report is a rendering of it: both are built from the one set of
    models, so a number cannot appear in one and not the other.
    """
    if options.output_format == "json":
        return report.model_dump_json(indent=2, exclude_none=True)
    return render_trend_report(report, repository_owners(configuration))


def emit_trend(configuration: Configuration, options: Namespace) -> int:
    """Emit each configured repository's series of periods since it was enabled.

    Collects what the cache lacks exactly as `evidence` does, so a cold cache costs one fetch per
    uncovered period and a warm one costs nothing; `--offline` refuses any period the cache does not
    fully cover rather than reporting a short one.
    """
    if not options.offline and environ.get("GH_TOKEN") is None:
        logging.error("GH_TOKEN is not set; use --offline to report cached evidence only")
        return 1
    reference = datetime.now(UTC)
    # ExitStack rather than `with Session()`, as `emit_evidence` does: an offline run must construct
    # no session at all, so the trend of a cached series never needs credentials.
    with ExitStack() as stack:
        client = None if options.offline else GitHubClient(environ["GH_TOKEN"], stack.enter_context(Session()))
        try:
            request = SeriesRequest(
                period_days=options.period_days,
                periods=options.periods,
                reference=reference,
                client=client,
            )
        except ValueError as exception:
            logging.error("Unusable trend request: %s", exception)
            return 1
        try:
            report = trend_report(configuration, request)
        except StorageError as exception:
            # The alert observation history is read straight through, unlike the windowed evidence a
            # repository degrades to `EvidenceUnavailable` for: a series that silently reported no
            # observation because the file could not be opened is indistinguishable from one nobody
            # ever collected, which is the single thing this history exists to keep apart.
            logging.error("Trend failed: %s", exception)
            return 1
    sys.stdout.write(f"{trend_output(configuration, options, report)}\n")
    return run_status(series_status(report))


def sonar_source(configuration: Configuration, session: Session) -> SonarSource:
    """Build the SonarCloud source a collection reads each repository's quality state through.

    The map it resolves through is the one `map-sonar` wrote into the DURABLE observations file, and
    it is read rather than built here: attributing a project to a repository costs a commit search
    limited to 30 calls a minute, which a collection cannot afford. A configuration whose map is
    empty resolves nothing, reports the reason on every repository, and exits `0`.
    """
    return SonarSource(
        client=SonarClient(session),
        project_map=StoredProjectMap(
            path=observation_database(configuration.database),
            sonar_organization=configuration.sonar_organization_name,
        ),
        overrides=configuration.sonar_projects,
    )


def collect_evidence(configuration: Configuration, options: Namespace, token: str) -> int:
    """Fill the cache for one collection window and report what was fetched or reused.

    The collection report is written whatever the run's completeness, including when nothing could
    be collected at all: `failures` naming the repositories that refused is the most useful thing a
    wholly failed run produces, and suppressing it would leave a non-zero status with no reason.

    Every run APPENDS an open-alert observation per repository per readable family, beside the
    latest-only state it replaces. That is the only way a past open count is ever reportable —
    GitHub answers for now and nothing else — so the alert series a trend shows holds exactly the
    instants at which a collection actually ran, and a missed run is a permanent gap in it.
    """
    reference = datetime.now(UTC)
    try:
        window = requested_window(configuration, options, reference)
    except ValueError as exception:
        logging.error("Unusable collection window: %s", exception)
        return 1
    # Two sessions, as `map-sonar` uses: the GitHub client puts its bearer token on the session it is
    # given, and sharing one would send a GitHub token to SonarCloud on every request.
    with Session() as session, Session() as sonar_session:
        client = GitHubClient(token, session)
        try:
            inventory = collect_window(
                configuration,
                client,
                collect_inventory(configuration, client, window, sonar_source(configuration, sonar_session)),
                window,
                reference,
            )
            record_repository_state(configuration.database, inventory)
            appended = record_alert_observations(observation_database(configuration.database), inventory)
        except StorageError as exception:
            logging.error("Storage failed: %s", exception)
            return 1
    logging.info("Appended %s alert observations", appended)
    sys.stdout.write(f"{inventory.model_dump_json(indent=2)}\n")
    return run_status(inventory.status)


@dataclass
class MappingProgress:
    """Tally what one `map-sonar` run learned and what it spent, as it goes.

    Accumulated in one object rather than assembled at the end because the run is INTERRUPTIBLE: it
    is paced against the scarcest quota this project spends, and a rate limit or a human can stop it
    part way through an organisation. The summary must then describe the projects it did answer,
    which is the whole reason each row is written as it is resolved rather than in one batch.

    `claims` is keyed by repository and holds every project key that named it, so that the
    many-to-one case is visible: SonarCloud has no rename, so a re-created project leaves its
    abandoned twin behind claiming the same repository, and a human deciding which key is live wants
    both names rather than a count.
    """

    listed: int
    unchanged: int = 0
    stopped: bool = False
    outcomes: Counter[SonarMappingOutcome] = field(default_factory=Counter)
    claims: dict[str, list[str]] = field(default_factory=dict)

    def claim(self, mapping: SonarProjectMapping | None) -> None:
        """Record that one project names one repository, keeping every project that named it."""
        if mapping is not None:
            self.claims.setdefault(mapping.repository, []).append(mapping.project_key)

    def record(self, attempt: SonarResolutionAttempt) -> None:
        """Count one attempt's outcome, and the repository it claimed if it resolved one."""
        self.outcomes[attempt.outcome] += 1
        self.claim(attempt.mapping)

    def count(self, *outcomes: SonarMappingOutcome) -> int:
        """Count the visited projects that ended in any of the given outcomes."""
        return sum(self.outcomes[outcome] for outcome in outcomes)

    @property
    def answered(self) -> int:
        """Count the projects this run has an answer for, whether it resolved one or read one back.

        A never-analysed or unresolvable project IS answered: there is nothing more to learn about it
        until it is analysed again, which is why it is stored with its reason. Only a failed call
        leaves a project unanswered.
        """
        return self.unchanged + sum(count for outcome, count in self.outcomes.items() if outcome.is_observation)

    @property
    def disputed(self) -> dict[str, tuple[str, ...]]:
        """List every repository more than one project claims, which is the duplicate-project list."""
        return {
            repository: tuple(projects) for repository, projects in sorted(self.claims.items()) if len(projects) > 1
        }

    @property
    def status(self) -> CollectionStatus:
        """Describe the run's completeness the way every other command's status is described."""
        if not self.answered:
            return CollectionStatus.FAILED
        if self.stopped or self.outcomes[SonarMappingOutcome.FAILED]:
            return CollectionStatus.PARTIAL
        return CollectionStatus.COMPLETE


def already_answered(project: SonarProject, stored: StoredSonarMapping) -> bool:
    """Decide whether the stored map already answers for this project, so no call need be spent.

    THE WATERMARK IS WHEN THE ROW WAS WRITTEN, NOT WHICH ANALYSIS ANSWERED. A row is skipped when
    NOTHING HAS BEEN ANALYSED SINCE it was stored: whatever the last run learned — a repository, or
    the reason there is none — is a property of the analyses that existed then, and asking again
    before a newer one exists spends the scarcest quota there is on a question already answered. It
    falls through the moment a newer analysis exists, which is the only thing that can change the
    answer.

    Comparing against the RESOLVING analysis instead would never converge for the projects
    `sonar_to_github` walks back for: it records the instant of whichever analysis resolved, which is
    older than the listed one whenever the newest commit is not findable — so the row would be
    re-resolved every run, and `record_sonar_mapping` would then refuse the identical write.
    """
    return project.analysis_at is None or project.analysis_at <= stored.resolved_at


def resolve_listed_projects(
    sonar: SonarClient,
    github: GitHubClient,
    organization: str,
    sonar_organization: str,
    database: Path,
    projects: tuple[SonarProject, ...],
) -> MappingProgress:
    """Resolve each listed project to its repository, storing every answer as it is arrived at.

    BOTH ORGANISATION NAMES ARE CARRIED, because they are allowed to differ: `organization` is the
    GitHub one the commit search is qualified by and the owner an answer must belong to, and
    `sonar_organization` is the SonarCloud one the map is keyed under. Collapsing them would search
    GitHub for an organisation that need not exist there.

    Stopped rather than failed by a rate limit that survived the client's own retries: the limit
    applies to every project still to come exactly as it applied to this one, so continuing would
    write this run's exhaustion into the map as each remaining project's own dead end.
    """
    pacer = search_pacer(github)
    progress = MappingProgress(listed=len(projects))
    for position, project in enumerate(projects, start=1):
        stored = load_sonar_mapping(database, sonar_organization, project.key)
        if stored is not None and already_answered(project, stored):
            progress.unchanged += 1
            progress.claim(stored.mapping)
            logging.info(
                "SKIP   %s (%s/%s): nothing analysed since it was resolved",
                project.key,
                position,
                len(projects),
            )
            continue
        try:
            attempt = sonar_to_github(sonar, github, organization, project.key, pacer=pacer)
        except GitHubError as exception:
            # The one error `sonar_to_github` raises rather than classifying is an exhausted rate
            # limit, which is why this is a stop and not one project's failure.
            logging.error(
                "ERROR  %s (%s/%s): %s; stopping and keeping the %s projects already resolved",
                project.key,
                position,
                len(projects),
                exception,
                progress.answered,
            )
            progress.stopped = True
            return progress
        progress.record(attempt)
        report_attempt(project, attempt, position, len(projects))
        if attempt.outcome.is_observation:
            store_attempt(database, sonar_organization, attempt)
    return progress


def report_attempt(project: SonarProject, attempt: SonarResolutionAttempt, position: int, listed: int) -> None:
    """Say on stderr what became of one project, as `doctor` reports one repository."""
    progress = f"({position}/{listed})"
    if attempt.mapping is not None:
        logging.info("OK     %s %s: %s", attempt.project_key, progress, attempt.mapping.repository)
    elif attempt.outcome is SonarMappingOutcome.FAILED:
        logging.error("ERROR  %s %s: %s", attempt.project_key, progress, attempt.detail)
    else:
        # An observation, not a failure: the project has been answered for, and the answer is that
        # nothing on either side names a repository for it.
        logging.info("NONE   %s %s: %s", attempt.project_key, progress, attempt.detail)
    logging.debug("project %s was last analysed at %s", project.key, project.analysis_at)


def store_attempt(database: Path, sonar_organization: str, attempt: SonarResolutionAttempt) -> None:
    """Upsert one answered project, saying when a newer stored answer was left in place.

    Only an answer is stored, never a failed call: `record_sonar_mapping` refuses anything whose
    analysis is not newer than the stored row's, so a resolution taken from an older analysis — and
    every unresolved reason, which has no analysis instant at all — leaves an existing mapping alone
    rather than overwriting it with less.
    """
    resolution = StoredSonarMapping(
        project_key=attempt.project_key,
        resolved_at=datetime.now(UTC),
        mapping=attempt.mapping,
        detail=attempt.detail,
    )
    if not record_sonar_mapping(database, sonar_organization, resolution):
        logging.debug("kept the stored mapping for %s: it was resolved from a newer analysis", attempt.project_key)


def log_mapping_summary(organization: str, progress: MappingProgress) -> None:
    """Report what the run cost and what the map now holds, including the repositories two projects claim."""
    logging.info(
        "Mapped %s of %s projects listed for %s: %s resolved, %s unchanged, %s never analysed, "
        "%s unresolvable, %s failed; %s repositories mapped",
        progress.answered,
        progress.listed,
        organization,
        progress.count(SonarMappingOutcome.RESOLVED),
        progress.unchanged,
        progress.count(SonarMappingOutcome.NO_ANALYSIS),
        progress.count(
            SonarMappingOutcome.NO_REVISION,
            SonarMappingOutcome.UNKNOWN_COMMIT,
            SonarMappingOutcome.OUTSIDE_ORGANIZATION,
        ),
        progress.count(SonarMappingOutcome.FAILED),
        len(progress.claims),
    )
    for repository, projects in progress.disputed.items():
        # A warning rather than an error: it is legitimate, and the reverse lookup resolves it by
        # analysis recency. It is surfaced because the alternative to a human seeing both keys is a
        # report quietly showing one project's gate for a repository that has two.
        logging.warning("%s is claimed by %s projects: %s", repository, len(projects), ", ".join(projects))


def map_sonar_projects(configuration: Configuration, token: str) -> int:
    """Resolve every project the configured SonarCloud organisation lists, and store the map.

    Two sessions, not one: the GitHub client puts its bearer token on the session it is given, and
    sharing that session would send a GitHub token to SonarCloud on every request.

    An organisation that lists nothing is a refusal rather than an empty success — the configured
    `sonar_organization` names nothing readable, and reporting `0` would let a later evidence run
    read an empty map as "no repository has a SonarCloud project".

    The two organisation names are kept apart throughout: SonarCloud is listed and the map is keyed
    under `sonar_organization`, while the commit search that resolves a project is qualified by the
    GitHub `organization`. They are the same string at HMCTS and need not be anywhere else.
    """
    sonar_organization = configuration.sonar_organization_name
    database = observation_database(configuration.database)
    with Session() as github_session, Session() as sonar_session:
        github = GitHubClient(token, github_session)
        sonar = SonarClient(sonar_session)
        try:
            projects = sonar.list_projects(sonar_organization)
        except SonarError as exception:
            logging.error("SonarCloud project listing failed for %s: %s", sonar_organization, exception)
            return 1
        if not projects:
            logging.error("SonarCloud lists no project for organisation %s", sonar_organization)
            return 1
        logging.info("SonarCloud lists %s projects for %s", len(projects), sonar_organization)
        try:
            progress = resolve_listed_projects(
                sonar,
                github,
                configuration.organization,
                sonar_organization,
                database,
                projects,
            )
        except StorageError as exception:
            logging.error("Storage failed: %s", exception)
            return 1
    log_mapping_summary(sonar_organization, progress)
    return run_status(progress.status)


def resolve_configuration(options: Namespace) -> Configuration | None:
    """Load the configuration, or report why it cannot serve the command that was asked for.

    Two refusals, kept together because both are answered the same way — by a human editing what
    `--config` points at — and both must happen before a session is opened or a token is looked for.
    The second is not a schema rule: a configuration without a `teams:` section is perfectly valid
    for `map-sonar` and `prune`, and invalid only for a command that reports the cohort.
    """
    try:
        configuration = load_configuration(*options.config)
    except ConfigurationError as exception:
        logging.error("Invalid configuration: %s", exception)
        return None
    if options.command in COHORT_COMMANDS and not configuration.teams:
        logging.error(
            "%s reports the configured repositories, but no team is configured: add a `teams:` "
            "section, or layer the team file in with a second --config",
            options.command,
        )
        return None
    return configuration


def main() -> int:
    """Entry point for the command-line interface."""
    options = parse_arguments()
    logging.basicConfig(level=options.logging.upper(), format="%(asctime)s:%(levelname)s: %(message)s")
    configuration = resolve_configuration(options)
    if configuration is None:
        return 1
    # The commands main() need not hold a token for: `prune` never contacts GitHub, and the two
    # reporting commands acquire one themselves only when they were not asked to stay offline.
    reporting = {"evidence": emit_evidence, "trend": emit_trend, "prune": prune_evidence_cache}
    if options.command in reporting:
        return reporting[options.command](configuration, options)

    token = environ.get("GH_TOKEN")
    if token is None:
        logging.error("GH_TOKEN is not set")
        return 1
    if options.command == "doctor":
        with Session() as session:
            logging.info("OK     Configuration valid")
            return 0 if run_doctor(configuration, token, session) else 1
    if options.command == "map-sonar":
        return map_sonar_projects(configuration, token)
    return collect_evidence(configuration, options, token)


if __name__ == "__main__":
    raise SystemExit(main())
