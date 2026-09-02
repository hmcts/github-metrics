"""Project cached behaviour facts into auditable evidence."""

import logging
from collections import Counter
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from metrics.analysis import Merge, excluded_authors, in_cohort, is_human_account
from metrics.assessment import ReadinessPolicy, readiness_policy
from metrics.behaviour import (
    collect_open_pull_request_state,
    requested_coverage,
    source_signature,
    synchronize_merges,
)
from metrics.behaviour_metrics import behaviour_metrics
from metrics.behaviour_metrics.base import BehaviourMetric
from metrics.config import Configuration, configured_repositories, repository_owners
from metrics.domain import (
    MAINTENANCE_WINDOWS,
    ActorReadiness,
    ActorRepositoryReadiness,
    BehaviourEvidenceReport,
    BehaviourMetricSummary,
    CachedBehaviourFacts,
    CodeownersReport,
    CohortSummary,
    EvidenceSource,
    EvidenceUnavailable,
    MaintenanceEvidence,
    MaintenanceReport,
    MaintenanceWindowStatus,
    MergeGateReport,
    Merges,
    OpenPullRequestReport,
    PracticeEvidenceReport,
    ReportingWindow,
    RepositoryPracticeEvidence,
    SecurityAlertReport,
    SonarReport,
    SourceCoverage,
    StoredRepositoryState,
    WindowProvenance,
)
from metrics.github import GitHubClient, GitHubError
from metrics.rules import configured_rules
from metrics.rules.base import PracticeRule
from metrics.storage import (
    StorageError,
    find_missing_cached_coverage,
    load_cached_direct_commit_facts,
    load_cached_pull_request_facts,
    load_repository_state,
    prevailing_cached_coverage,
)


def metric_classifications(cohort: Merges, metric: BehaviourMetric) -> dict[str, int]:
    """Count how one metric classified every merge in a cohort, by either route onto the branch.

    Factored out of `RepositoryEvidence.metric` so that a summary of one person's merges and a
    drill-down over the whole cohort count classifications by the same rules: a direct commit the
    metric does not classify is absent rather than counted under a name it never assigned.
    """
    classifications = Counter(metric.classification(pull_request) for pull_request in cohort.pull_requests)
    classifications.update(
        classification
        for commit in cohort.direct_commits
        if (classification := metric.commit_classification(commit)) is not None
    )
    return dict(sorted(classifications.items()))


def metric_summaries(cohort: Merges, metrics: Iterable[BehaviourMetric]) -> tuple[BehaviourMetricSummary, ...]:
    """Summarise every given metric over one cohort of merges.

    The cohort is whatever the caller narrowed it to — a whole repository window, or one person's
    merges inside it — and the summaries say nothing about which: the aggregate is the same
    computation either way, and what it is measured over is stated by whatever carries the tuple.
    """
    return tuple(
        BehaviourMetricSummary(
            metric=metric.identifier,
            summary=metric.summary(cohort),
            classifications=metric_classifications(cohort, metric),
        )
        for metric in metrics
    )


def actor_slice(cohort: Merges, login: str) -> Merges:
    """Narrow one repository's merges to those one person authored, by either route.

    Matched case-insensitively, because a GitHub login is unique case-insensitively and the actor
    section already treats `Alice` and `alice` as the one person they are. Both fact tuples are
    filtered: a person who pushed straight to the default branch contributed there as much as one who
    merged a pull request, and slicing only the pull requests would measure them as if they had not.
    """
    wanted = login.casefold()
    return Merges(
        pull_requests=tuple(
            fact
            for fact in cohort.pull_requests
            if fact.author_login is not None and fact.author_login.casefold() == wanted
        ),
        direct_commits=tuple(
            commit
            for commit in cohort.direct_commits
            if commit.author_login is not None and commit.author_login.casefold() == wanted
        ),
    )


class RepositoryEvidence(CachedBehaviourFacts):
    """Build metric and practice reports for one repository reporting window."""

    provenance: WindowProvenance
    cohort: CohortSummary

    def metric(self, metric: BehaviourMetric, *, include_identities: bool) -> BehaviourEvidenceReport:
        """Build one injected metric's offline evidence report."""
        return BehaviourEvidenceReport(
            organization=self.organization,
            repository=self.repository,
            metric=metric.identifier,
            starts_at=self.starts_at,
            ends_at=self.ends_at,
            provenance=self.provenance,
            cohort=self.cohort,
            summary=metric.summary(self),
            classifications=metric_classifications(self, metric),
            identities_included=include_identities,
            pull_requests=tuple(metric.reference(self.organization, fact) for fact in self.pull_requests)
            if include_identities
            else None,
            direct_commits=metric.commit_references(self) if include_identities else None,
        )

    def practices(
        self,
        rules: Iterable[PracticeRule],
        policy: ReadinessPolicy,
        current_state: StoredReports,
        team: str,
        metrics: Iterable[BehaviourMetric],
    ) -> RepositoryPracticeEvidence:
        """Assess readiness and evaluate every enabled practice rule beside the collected merge gate.

        The stored blocks arrive together in `current_state` because they ARE one thing: six
        projections of the single stored row this repository's last collection wrote. Passing them
        one by one grew a parameter for every source added and let a call site pair a repository's
        gate with another's alerts. The open pull-request block joined them on 2026-09-01: it was
        the last argument passed separately, and a refresh overrides it in `current_state` before it
        arrives rather than travelling alongside.

        `security` is reported but grades nothing: no open-alert threshold has an owner, and the same
        reasoning keeps description quality out of the label (architecture.md, "Readiness assessment").
        CODEOWNERS, maintenance and SonarCloud are reported and ungraded for the same reason: a
        signal becoming visible is not a reason to grade it. A failing SonarCloud quality gate is the
        sharpest example — it is somebody else's threshold, set per project, and adopting it here
        would import a judgment this tool did not make.

        `team` and `metrics` arrive from the caller for the same reason the stored blocks do: the
        owning team is configuration and the metric set is built from it, and neither is derivable
        from a repository's cached facts.
        """
        return RepositoryPracticeEvidence(
            repository=self.repository,
            team=team,
            starts_at=self.starts_at,
            ends_at=self.ends_at,
            provenance=self.provenance,
            cohort=self.cohort,
            assessment=policy.assess(self, current_state.merge_gate) if policy.enabled else None,
            merge_gate=current_state.merge_gate,
            open_pull_requests=current_state.open_pull_requests,
            security=current_state.security,
            codeowners=current_state.codeowners,
            maintenance=current_state.maintenance,
            sonar=current_state.sonar,
            metrics=metric_summaries(self, metrics),
            behaviour=tuple(finding for rule in rules if rule.enabled for finding in rule.findings(self)),
        )


def cohort_summary(changes: Merges, excluded: Collection[str]) -> CohortSummary:
    """Summarise which merges the report covers and who was left out.

    Direct commits are counted after the same author exclusions as pull requests: a dependency bot
    pushing straight to the default branch is still a mechanical bump, and including it would
    inflate a denominator the exclusions exist to keep meaningful.
    """
    return CohortSummary(
        merged=len(changes.pull_requests),
        reported=sum(in_cohort(fact, excluded) for fact in changes.pull_requests),
        excluded_authors=dict(
            sorted(
                Counter(
                    fact.author_login or "" for fact in changes.pull_requests if not in_cohort(fact, excluded)
                ).items(),
            ),
        ),
        direct_commits=sum(in_cohort(commit, excluded) for commit in changes.direct_commits),
    )


def authored_logins(changes: Merges) -> tuple[str, ...]:
    """Return the author login of every merge a person authored, in the order the facts carry.

    Both routes onto the default branch, because a person who pushed straight to it contributed as
    much as one who merged a pull request. Bot accounts are left out here, which drops MORE accounts
    than the cohort's own exclusions do: an agent-authored merge stays in the cohort and in every
    rate measured over it, but an agent is not a person to review. Unattributed changes are left out
    for the same reason — a commit GitHub matched to no account names nobody.
    """
    merges: tuple[Merge, ...] = (*changes.pull_requests, *changes.direct_commits)
    return tuple(
        login
        for change in merges
        if (login := change.author_login) is not None and is_human_account(login, change.author_type)
    )


def actor_contributions(changes: Merges) -> Counter[str]:
    """Count how many of one repository's merges each person authored, keyed by case-folded login."""
    return Counter(login.casefold() for login in authored_logins(changes))


def actor_blocking_occurrences(practice: RepositoryPracticeEvidence) -> Counter[str]:
    """Sum the occurrences behind one repository's practice findings, keyed by case-folded login.

    Occurrences rather than findings: two rules each reporting an actor six times is twelve
    occurrences, and counting findings would make a person look better the fewer rules ran.
    """
    counts: Counter[str] = Counter()
    for finding in practice.behaviour:
        counts[finding.actor_login.casefold()] += finding.occurrences
    return counts


def actor_readiness(
    reported: Iterable[tuple[RepositoryEvidence, RepositoryPracticeEvidence]],
    metrics: Sequence[BehaviourMetric],
) -> tuple[ActorReadiness, ...]:
    """List every person who contributed to the reported repositories, with each repository's label.

    Takes the pairs rather than either side alone: the contributions come from the cached facts and
    the label and findings come from the practice report built from them, so pairing them at the
    call site is what keeps one repository's merges from being read against another's assessment.

    Each row's metric summaries are measured over that person's merges IN THAT REPOSITORY, sliced
    from the same facts the repository's own summaries are measured over. They are listed per
    repository and never combined: a person's cycle time in a repository they merged twice in is not
    comparable with one they merged eighty times in, and one figure spanning both would say nothing.
    """
    rows: dict[str, list[ActorRepositoryReadiness]] = {}
    spellings: dict[tuple[str, str], str] = {}
    for evidence, practice in reported:
        blocking = actor_blocking_occurrences(practice)
        logins = authored_logins(evidence)
        # Reversed so the earliest spelling seen in this repository is the one left assigned.
        spelled = {login.casefold(): login for login in reversed(logins)}
        for actor, contributions in actor_contributions(evidence).items():
            rows.setdefault(actor, []).append(
                ActorRepositoryReadiness(
                    readiness=practice.assessment.label if practice.assessment is not None else None,
                    repository=practice.repository,
                    contributions=contributions,
                    # A finding attributed to somebody who authored nothing here cannot exist, but a
                    # person with no findings is the ordinary case and blocks nothing.
                    blocking=blocking.get(actor, 0),
                    metrics=metric_summaries(actor_slice(evidence, actor), metrics),
                ),
            )
            spellings[actor, practice.repository] = spelled[actor]
    actors = []
    for actor in sorted(rows):
        # The weightiest repository leads, and it also decides how the login is spelled.
        repositories = tuple(sorted(rows[actor], key=lambda row: (-row.contributions, row.repository)))
        actors.append(
            ActorReadiness(actor_login=spellings[actor, repositories[0].repository], repositories=repositories),
        )
    return tuple(actors)


def repository_evidence(
    configuration: Configuration,
    repository: str,
    window: ReportingWindow,
    changes: Merges,
    provenance: WindowProvenance,
) -> RepositoryEvidence:
    """Build repository evidence for the configured cohort of one window's changes."""
    excluded = excluded_authors(configuration.cohort.excluded_authors)
    return RepositoryEvidence(
        organization=configuration.organization,
        repository=repository,
        starts_at=window.starts_at,
        ends_at=window.ends_at,
        pull_requests=tuple(fact for fact in changes.pull_requests if in_cohort(fact, excluded)),
        direct_commits=tuple(commit for commit in changes.direct_commits if in_cohort(commit, excluded)),
        provenance=provenance,
        cohort=cohort_summary(changes, excluded),
    )


@dataclass(frozen=True)
class StoredState:
    """One repository's stored current state, or the reason there is none to read.

    Loaded ONCE per repository and projected into each block that reads it. Every block below is a
    different view of the same row, and loading per block would reopen the database and revalidate
    the same payload once for each of them.
    """

    latest: StoredRepositoryState | None = None
    unreadable: str | None = None


@dataclass(frozen=True)
class StoredReports:
    """Hold every current-state block one stored row projects into, for one repository.

    Assembled once by `stored_reports` and handed to `practices` whole. The blocks travel together
    because they all describe the same observation instant: a reader comparing a merge gate against
    the CODEOWNERS beside it is entitled to assume both were read from the same row, and separate
    arguments made that an assumption rather than a fact.
    """

    merge_gate: MergeGateReport
    open_pull_requests: OpenPullRequestReport
    security: SecurityAlertReport
    codeowners: CodeownersReport
    maintenance: MaintenanceReport
    sonar: SonarReport


def stored_repository_state(configuration: Configuration, repository: str) -> StoredState:
    """Load one repository's stored current state, keeping an unreadable cache as a reason."""
    try:
        latest = load_repository_state(configuration.database, configuration.organization, repository)
    except StorageError as exception:
        return StoredState(unreadable=str(exception))
    return StoredState(latest=latest)


def stored_merge_gate(stored: StoredState) -> MergeGateReport:
    """Report the merge gate the last collection stored, or state why there is none to report.

    A gate stored by a build predating a rule type reports that rule at its default, which is why
    `metrics collect` must have run since the type was added; see README, "Readiness assessment".
    """
    if stored.unreadable is not None:
        return MergeGateReport(detail=stored.unreadable)
    if stored.latest is None:
        return MergeGateReport(detail="no repository state has been collected; run metrics collect")
    if stored.latest.state.merge_gate is None:
        return MergeGateReport(
            fetched_at=stored.latest.fetched_at,
            detail="the merge gate was not observable when repository state was collected",
        )
    return MergeGateReport(fetched_at=stored.latest.fetched_at, gate=stored.latest.state.merge_gate)


def stored_open_pull_requests(stored: StoredState) -> OpenPullRequestReport:
    """Report the open pull-request state the last collection stored, or why there is none.

    The window travels back out of storage with the counts, because two of the four are bounded by
    the window they were collected over and a count over a forgotten window cannot be read. A row
    stored before this state was collected reports the gap rather than four zeros: "nothing is open"
    and "nobody asked" are different answers. See architecture.md, "Source taxonomy".
    """
    if stored.unreadable is not None:
        return OpenPullRequestReport(detail=stored.unreadable)
    if stored.latest is None:
        return OpenPullRequestReport(detail="no repository state has been collected; run metrics collect")
    snapshot = stored.latest.state.open_pull_requests
    if snapshot is None:
        return OpenPullRequestReport(
            fetched_at=stored.latest.fetched_at,
            detail="open pull-request state was not collected when repository state was stored; run metrics collect",
        )
    return OpenPullRequestReport(
        fetched_at=stored.latest.fetched_at,
        starts_at=snapshot.starts_at,
        ends_at=snapshot.ends_at,
        summary=snapshot.summary,
    )


def stored_security_alerts(stored: StoredState) -> SecurityAlertReport:
    """Report the security alerts the last collection stored, or state why there are none to report.

    Read from storage rather than fetched: open alerts are current state stored latest-only beside
    the merge gate, so an offline report serves the last collection's answer with its `fetched_at`
    instead of refusing.
    """
    if stored.unreadable is not None:
        return SecurityAlertReport(detail=stored.unreadable)
    if stored.latest is None:
        return SecurityAlertReport(detail="no repository state has been collected; run metrics collect")
    if stored.latest.state.security is None:
        return SecurityAlertReport(
            fetched_at=stored.latest.fetched_at,
            detail="security alerts were not collected when repository state was stored; run metrics collect",
        )
    return SecurityAlertReport(fetched_at=stored.latest.fetched_at, alerts=stored.latest.state.security)


def stored_codeowners(stored: StoredState) -> CodeownersReport:
    """Report the CODEOWNERS presence the last collection stored, or state why there is none.

    A row stored by a build predating this source has no `codeowners` field, and reporting that as
    checked-and-absent would invent an observation nobody made.
    """
    if stored.unreadable is not None:
        return CodeownersReport(detail=stored.unreadable)
    if stored.latest is None:
        return CodeownersReport(detail="no repository state has been collected; run metrics collect")
    if stored.latest.state.codeowners is None:
        return CodeownersReport(
            fetched_at=stored.latest.fetched_at,
            detail="CODEOWNERS presence was not collected when repository state was stored; run metrics collect",
        )
    return CodeownersReport(fetched_at=stored.latest.fetched_at, codeowners=stored.latest.state.codeowners)


def stored_sonar(stored: StoredState) -> SonarReport:
    """Report the SonarCloud measures the last collection stored, or state why there are none.

    THE ANSWER IS READ FROM THE STORED RESOLUTION, NOT FROM THE ABSENCE OF MEASURES. "No SonarCloud
    project is mapped to this repository" is an answer, and the answer for most of the organisation;
    "measures were not collected when this row was stored" is a gap that `metrics collect` closes.
    Both leave `sonar` unset, so telling them apart means reading `sonar_project`: the collection
    stored one carrying its reason for the first, and a row written by a build predating this source —
    or by a run configured to read no SonarCloud state — has neither field.

    A resolved project whose measures could not be read reports the project ANYWAY, beside the reason:
    naming the project a failed call was about is more useful than withholding it.
    """
    if stored.unreadable is not None:
        return SonarReport(detail=stored.unreadable)
    if stored.latest is None:
        return SonarReport(detail="no repository state has been collected; run metrics collect")
    resolution = stored.latest.state.sonar_project
    if resolution is None:
        return SonarReport(
            fetched_at=stored.latest.fetched_at,
            detail="SonarCloud measures were not collected when repository state was stored; run metrics collect",
        )
    measures = stored.latest.state.sonar
    if measures is None:
        return SonarReport(
            fetched_at=stored.latest.fetched_at,
            mapping=resolution.mapping,
            # A resolution names a project or says why it names none, so one of the two is always
            # here; the fallback covers only a stored row this build did not write.
            detail=resolution.detail or "SonarCloud measured nothing for this project",
        )
    return SonarReport(fetched_at=stored.latest.fetched_at, mapping=resolution.mapping, measures=measures)


def stored_reports(stored: StoredState) -> StoredReports:
    """Project one stored row into every current-state block a repository's report carries."""
    return StoredReports(
        merge_gate=stored_merge_gate(stored),
        open_pull_requests=stored_open_pull_requests(stored),
        security=stored_security_alerts(stored),
        codeowners=stored_codeowners(stored),
        maintenance=stored_maintenance(stored),
        sonar=stored_sonar(stored),
    )


def human_window_answer(evidence: MaintenanceEvidence, cutoff: datetime) -> tuple[bool | None, str | None]:
    """Answer whether a human commit fell inside one window, or say why that is unknown.

    A found human commit decides every window either way. An absent one decides False only where
    the search is known to have examined everything after the window's cutoff: an empty branch
    (nothing exists to find), or `searched_back_to` at or past the cutoff. Otherwise the page cap
    stopped the search short of this window and the answer is unknown with its reason — unavailable
    data never becomes zero, and here it never becomes False either.
    """
    if evidence.last_human_commit_at is not None:
        return evidence.last_human_commit_at >= cutoff, None
    searched_back_to = evidence.searched_back_to
    if searched_back_to is None or searched_back_to <= cutoff:
        return False, None
    return None, (
        f"the bounded search examined commits no older than {searched_back_to:%Y-%m-%dT%H:%MZ}, "
        f"which does not reach this window's cutoff"
    )


def maintenance_windows(evidence: MaintenanceEvidence, fetched_at: datetime) -> tuple[MaintenanceWindowStatus, ...]:
    """Derive the 6/12/24-month window rows from the stored instants against the observation instant."""
    rows = []
    for months, days in MAINTENANCE_WINDOWS:
        cutoff = fetched_at - timedelta(days=days)
        human_committed_within, human_detail = human_window_answer(evidence, cutoff)
        rows.append(
            MaintenanceWindowStatus(
                months=months,
                committed_within=evidence.last_commit_at is not None and evidence.last_commit_at >= cutoff,
                human_committed_within=human_committed_within,
                human_detail=human_detail,
            ),
        )
    return tuple(rows)


def stored_maintenance(stored: StoredState) -> MaintenanceReport:
    """Report the maintenance instants the last collection stored, or state why there are none.

    The window rows are derived here — at report assembly, not at collection and not in rendering —
    against the stored `fetched_at`, so the same stored row always yields the same report.
    """
    if stored.unreadable is not None:
        return MaintenanceReport(detail=stored.unreadable)
    if stored.latest is None:
        return MaintenanceReport(detail="no repository state has been collected; run metrics collect")
    if stored.latest.state.maintenance is None:
        return MaintenanceReport(
            fetched_at=stored.latest.fetched_at,
            detail="maintenance state was not collected when repository state was stored; run metrics collect",
        )
    return MaintenanceReport(
        fetched_at=stored.latest.fetched_at,
        maintenance=stored.latest.state.maintenance,
        windows=maintenance_windows(stored.latest.state.maintenance, stored.latest.fetched_at),
    )


def open_pull_request_report(
    client: GitHubClient,
    configuration: Configuration,
    repository: str,
    window: ReportingWindow,
    reference: datetime,
) -> OpenPullRequestReport:
    """Observe one repository's open pull-request state fresh, for the `--refresh` path only.

    The stored block `stored_open_pull_requests` serves is what every other run reports; this exists
    for the reader who needs the counts as of now rather than as of the last collection, and it
    overrides that block. The window it carries is the reporting window the two windowed counts were
    just measured over, so a refreshed report is read exactly like a stored one.
    """
    try:
        summary = collect_open_pull_request_state(
            client,
            configuration.organization,
            repository,
            window,
            configuration.lookback.stale_open_days,
            reference,
        )
    except GitHubError as exception:
        return OpenPullRequestReport(detail=str(exception))
    return OpenPullRequestReport(
        fetched_at=reference,
        starts_at=window.starts_at,
        ends_at=window.ends_at,
        summary=summary,
    )


def collected_through(configuration: Configuration) -> datetime | None:
    """Return the instant the caches can report a whole window up to for most of the estate.

    The one place the two independently cached sources are reduced to a single instant, so that
    everything anchoring a window offline — the service, `metrics evidence` without `--refresh`, and
    `metrics trend --offline` — anchors at the same place.

    THE EARLIER OF THE TWO EDGES: a window needs merged pull requests and direct commits both, so an
    instant only one source reaches is not an instant a window can end at. `None` when either source
    has no coverage at all under the current signature, which is what a cold cache looks like.

    An unreadable cache reports as nothing collected rather than raising: the service is offline
    always, and a page saying nothing has been collected is a better answer than a failed request.
    """

    def edge(source: EvidenceSource) -> datetime | None:
        return prevailing_cached_coverage(
            configuration.database,
            configuration.organization,
            source,
            source_signature(source),
        )

    try:
        pull_requests = edge(EvidenceSource.PULL_REQUEST)
        commits = edge(EvidenceSource.COMMIT)
    except StorageError as exception:
        logging.warning("Could not read the collection cache's coverage: %s", exception)
        return None
    if pull_requests is None or commits is None:
        return None
    return min(pull_requests, commits)


def uncovered_interval(missing: SourceCoverage) -> str:
    """Explain which source the cache could not answer for, and over what interval."""
    return (
        f"cached {missing.source.value} evidence does not cover "
        f"{missing.starts_at:%Y-%m-%dT%H:%MZ} to {missing.ends_at:%Y-%m-%dT%H:%MZ}"
    )


def cached_repository_evidence(
    configuration: Configuration,
    repository: str,
    window: ReportingWindow,
) -> RepositoryEvidence | EvidenceUnavailable:
    """Report one window from the cache alone, refusing any interval either source does not cover."""
    try:
        coverage = {
            source: requested_coverage(configuration, repository, window, source)
            for source in (EvidenceSource.PULL_REQUEST, EvidenceSource.COMMIT)
        }
        missing = tuple(
            interval
            for requested in coverage.values()
            # `record_use=False`: reporting from the cache is not what keeps an interval alive — the
            # collection that records it is — and a write here would move the cache file's stamp,
            # which is what the service decides a held bundle is still current against.
            for interval in find_missing_cached_coverage(configuration.database, requested, record_use=False)
        )
        if missing:
            return EvidenceUnavailable(repository=repository, detail=uncovered_interval(missing[0]))
        changes = Merges(
            pull_requests=load_cached_pull_request_facts(
                configuration.database,
                coverage[EvidenceSource.PULL_REQUEST],
            ),
            direct_commits=load_cached_direct_commit_facts(configuration.database, coverage[EvidenceSource.COMMIT]),
        )
    except StorageError as exception:
        return EvidenceUnavailable(repository=repository, detail=str(exception))
    return repository_evidence(
        configuration,
        repository,
        window,
        changes,
        WindowProvenance(offline=True, intervals_fetched=0),
    )


def collected_repository_evidence(
    configuration: Configuration,
    client: GitHubClient,
    repository: str,
    window: ReportingWindow,
    reference: datetime,
) -> RepositoryEvidence | EvidenceUnavailable:
    """Collect whatever the window needs that the cache lacks, then report it."""
    try:
        changes, provenance = synchronize_merges(configuration, client, repository, window, reference)
    except (GitHubError, StorageError) as exception:
        return EvidenceUnavailable(repository=repository, detail=str(exception))
    return repository_evidence(
        configuration,
        repository,
        window,
        changes,
        WindowProvenance(
            offline=False,
            intervals_fetched=provenance.stable_intervals_fetched + provenance.direct_commit_intervals_fetched,
        ),
    )


def practice_report(
    configuration: Configuration,
    reported: Iterable[tuple[RepositoryEvidence, StoredReports]],
    unavailable: Iterable[EvidenceUnavailable],
) -> PracticeEvidenceReport:
    """Assemble the practice report from evidence already loaded, however it was loaded.

    The one assembly both callers use: `offline_practice_report` below loads the evidence from the
    caches and hands it here, and `metrics evidence` hands over what it loaded — with a `--refresh`
    run's fresh open pull-request block already substituted into the stored blocks. Neither builds a
    report of its own, so the rules, the readiness policy, the owning team and the metric set are
    read from the configuration once and the two paths cannot disagree about any of them.

    Every repository arrives paired with its own stored blocks, and the practice evidence is paired
    with the facts it was built from before the actor section reads either: that is what stops one
    repository's merges from being read against another's assessment or another's merge gate.
    """
    owners = repository_owners(configuration)
    metrics = behaviour_metrics(configuration.traceability)
    rules = configured_rules(configuration)
    policy = readiness_policy(configuration)
    paired = tuple(
        (evidence, evidence.practices(rules, policy, blocks, owners[evidence.repository], metrics))
        for evidence, blocks in reported
    )
    return PracticeEvidenceReport(
        organization=configuration.organization,
        repositories=tuple(practice for _, practice in paired),
        unavailable=tuple(unavailable),
        actors=actor_readiness(paired, metrics),
    )


def offline_practice_report(configuration: Configuration, window: ReportingWindow) -> PracticeEvidenceReport:
    """Report every configured repository's practices for one window from the caches alone.

    The whole report in one call, contacting nothing: a reader that is not the command line — the
    read-only service the UI is served from — asks for a window and gets the same report `metrics
    evidence` prints, including the `unavailable` entries for the repositories the cache cannot cover.
    A window nobody has collected therefore reports itself as uncovered rather than as thinner data.
    """
    loaded = tuple(
        cached_repository_evidence(configuration, repository, window)
        for repository in configured_repositories(configuration)
    )
    return practice_report(
        configuration,
        tuple(
            (item, stored_reports(stored_repository_state(configuration, item.repository)))
            for item in loaded
            if isinstance(item, RepositoryEvidence)
        ),
        tuple(item for item in loaded if isinstance(item, EvidenceUnavailable)),
    )
