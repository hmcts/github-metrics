"""Project cached behaviour facts into auditable evidence."""

from collections import Counter
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from metrics.analysis import excluded_authors, in_cohort
from metrics.assessment import ReadinessPolicy
from metrics.behaviour import collect_open_pull_request_state, requested_coverage, synchronize_merges
from metrics.behaviour_metrics.base import BehaviourMetric
from metrics.config import Configuration
from metrics.domain import (
    MAINTENANCE_WINDOWS,
    BehaviourEvidenceReport,
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
    ReportingWindow,
    RepositoryPracticeEvidence,
    SecurityAlertReport,
    SonarReport,
    SourceCoverage,
    StoredRepositoryState,
    WindowProvenance,
)
from metrics.github import GitHubClient, GitHubError
from metrics.rules.base import PracticeRule
from metrics.storage import (
    StorageError,
    find_missing_cached_coverage,
    load_cached_direct_commit_facts,
    load_cached_pull_request_facts,
    load_repository_state,
)


class RepositoryEvidence(CachedBehaviourFacts):
    """Build metric and practice reports for one repository reporting window."""

    provenance: WindowProvenance
    cohort: CohortSummary

    def metric(self, metric: BehaviourMetric, *, include_identities: bool) -> BehaviourEvidenceReport:
        """Build one injected metric's offline evidence report."""
        classifications = Counter(metric.classification(pull_request) for pull_request in self.pull_requests)
        classifications.update(
            classification
            for commit in self.direct_commits
            if (classification := metric.commit_classification(commit)) is not None
        )
        return BehaviourEvidenceReport(
            organization=self.organization,
            repository=self.repository,
            metric=metric.identifier,
            starts_at=self.starts_at,
            ends_at=self.ends_at,
            provenance=self.provenance,
            cohort=self.cohort,
            summary=metric.summary(self),
            classifications=dict(sorted(classifications.items())),
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
        open_pull_requests: OpenPullRequestReport,
        current_state: StoredReports,
    ) -> RepositoryPracticeEvidence:
        """Assess readiness and evaluate every enabled practice rule beside the collected merge gate.

        The stored blocks arrive together in `current_state` because they ARE one thing: five
        projections of the single stored row this repository's last collection wrote. Passing them
        one by one grew a parameter for every source added and let a call site pair a repository's
        gate with another's alerts.

        `security` is reported but grades nothing: no open-alert threshold has an owner, and the same
        reasoning keeps description quality out of the label (architecture.md, "Readiness assessment").
        CODEOWNERS, maintenance and SonarCloud are reported and ungraded for the same reason: a
        signal becoming visible is not a reason to grade it. A failing SonarCloud quality gate is the
        sharpest example — it is somebody else's threshold, set per project, and adopting it here
        would import a judgment this tool did not make.
        """
        return RepositoryPracticeEvidence(
            repository=self.repository,
            starts_at=self.starts_at,
            ends_at=self.ends_at,
            provenance=self.provenance,
            cohort=self.cohort,
            assessment=policy.assess(self, current_state.merge_gate) if policy.enabled else None,
            merge_gate=current_state.merge_gate,
            open_pull_requests=open_pull_requests,
            security=current_state.security,
            codeowners=current_state.codeowners,
            maintenance=current_state.maintenance,
            sonar=current_state.sonar,
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


def stored_security_alerts(stored: StoredState) -> SecurityAlertReport:
    """Report the security alerts the last collection stored, or state why there are none to report.

    Read from storage rather than fetched, unlike open pull-request state: open alerts are current
    state stored latest-only beside the merge gate, so `--offline` serves the last collection's
    answer with its `fetched_at` instead of refusing.
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
    """Fetch one repository's open pull-request state fresh. Never cached — see architecture.md."""
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
    return OpenPullRequestReport(fetched_at=reference, summary=summary)


def offline_open_pull_request_report() -> OpenPullRequestReport:
    """Report open pull-request state as unavailable under `--offline`.

    The state is never cached, so there is no settled answer to serve; a remembered or zeroed count
    would misrepresent what `--offline` actually knows.
    """
    return OpenPullRequestReport(detail="open pull-request state is never cached; omit --offline to observe it")


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
            for interval in find_missing_cached_coverage(configuration.database, requested)
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
