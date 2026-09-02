"""Render evidence reports as readable plain text.

Presentation only. Every number rendered here comes from a report model the JSON already carries, so
the two renderings cannot disagree: the JSON stays the contract, and this exists because it cannot be
read aloud in a meeting. Plain ASCII with no colour and no emoji, because the output is piped,
diffed, and pasted into tickets.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from metrics.assessment import ReadinessPolicy
from metrics.domain import (
    ActorReadiness,
    ActorRepositoryReadiness,
    AlertObservation,
    AlertSeverity,
    BehaviourEvidenceCollection,
    BehaviourEvidenceReport,
    BehaviourMetricSummary,
    CodeownersReport,
    CohortSummary,
    DistributionObservation,
    EvidenceUnavailable,
    MaintenanceReport,
    MaintenanceWindowStatus,
    MergeGateReport,
    ObservationStatus,
    OpenAlertCount,
    OpenPullRequestReport,
    PracticeEvidenceReport,
    PracticeFinding,
    RateObservation,
    ReadinessAssessment,
    ReadinessCondition,
    ReadinessLabel,
    RepositoryPracticeEvidence,
    RepositoryTrend,
    SecurityAlertReport,
    SonarMeasures,
    SonarProjectMapping,
    SonarQualityGate,
    SonarRating,
    SonarReport,
    TrendDelta,
    TrendMetric,
    TrendPeriod,
    TrendReport,
    TrendThroughput,
    TrendWindow,
    WindowProvenance,
    reported_repositories,
)


@dataclass(frozen=True)
class RepositoryDrillDown:
    """Carry the metric aggregates and review counts shown beside one repository's practice evidence.

    The review counts are the report's own. The metric summaries are the ones the repository block of
    the JSON now carries, computed by `metric_summaries` for both, so a figure cannot appear in one
    rendering and not the other.
    """

    behaviour: tuple[BehaviourMetricSummary, ...]
    review_states: Mapping[str, int]


def instant(moment: datetime) -> str:
    """Format one instant as UTC to the minute.

    Converted rather than assumed: `parse_instant` and the `enablement` block both PRESERVE a
    written offset, so `2026-01-05T09:30:00+02:00` reaches here as 09:30 in a two-hour zone. Printing
    its wall clock under a `Z` would put the report two hours away from the JSON that carries the
    same instant, and the two renderings are not allowed to disagree.
    """
    return f"{moment.astimezone(UTC):%Y-%m-%dT%H:%MZ}"


def number(value: float | None) -> str:
    """Format one optional percentile, leaving an unmeasured value visibly absent."""
    return "-" if value is None else f"{value:g}"


def percent(value: float | None) -> str:
    """Format one optional percentage, leaving an unmeasured value visibly absent.

    Absent is a dash and never `0%`: a project whose scanner reported no coverage measurement and one
    measured at zero coverage are different findings, and only the second is a claim about the code.
    """
    return "-" if value is None else f"{value:g}%"


def percentage_of(count: int, total: int) -> str:
    """Format one count as a percentage of the population it was counted over.

    A total of zero is a dash rather than `0%`, through the same `percent` an unmeasured value goes
    through: a report covering nothing has no population to take a share of, and printing `0%` would
    claim a division it never did.

    A count too small to round to a tenth of a percent prints `<0.1%` for the same reason: across a
    population of thousands one person is 0.05%, and rounding that to `0%` would make a row somebody
    is in read exactly like the deliberately-printed zero rows beside it.
    """
    if total == 0:
        return percent(None)
    share = round(count / total * 100, 1)
    return "<0.1%" if count and not share else percent(share)


def quantity(value: int | None) -> str:
    """Format one optional count, leaving an uncounted value visibly absent.

    Formatted as an integer rather than through `number`, whose `%g` turns a million lines of code
    into `1e+06`.
    """
    return "-" if value is None else str(value)


def yes_or_no(*, value: bool | None) -> str:
    """Render a tri-state gate field without turning an undisclosed value into a false one."""
    if value is None:
        return "not disclosed"
    return "yes" if value else "no"


def rendered(lines: Iterable[str]) -> str:
    """Join rendered lines into one document without leading or trailing blank lines."""
    return "\n".join(lines).strip("\n")


def heading(title: str) -> tuple[str, ...]:
    """Return one underlined section heading, preceded by a separating blank line."""
    return "", title, "-" * len(title)


def pairs(rows: Sequence[tuple[str, str]]) -> tuple[str, ...]:
    """Render a two-column block of labels and their values."""
    width = max((len(label) for label, _ in rows), default=0)
    return tuple(f"  {label:<{width}}  {value}" for label, value in rows)


def table(columns: Sequence[str], rows: Sequence[Sequence[str]]) -> tuple[str, ...]:
    """Render a fixed-width table with a left-aligned first column and right-aligned values."""
    widths = tuple(max([len(name), *(len(row[index]) for row in rows)]) for index, name in enumerate(columns))
    return tuple(row_text(cells, widths) for cells in (columns, *rows))


def row_text(cells: Sequence[str], widths: Sequence[int]) -> str:
    """Render one table row to the given column widths."""
    values = (f"{cell:>{width}}" for cell, width in zip(cells[1:], widths[1:], strict=True))
    return "  ".join((f"  {cells[0]:<{widths[0]}}", *values))


def banner(title: str) -> tuple[str, ...]:
    """Return one repository banner, preceded by a separating blank line."""
    return "", "=" * len(title), title, "=" * len(title)


def render_header(
    organization: str,
    repository: str,
    starts_at: datetime,
    ends_at: datetime,
    provenance: WindowProvenance,
) -> tuple[str, ...]:
    """Render the repository banner, the resolved window, and how the window was satisfied."""
    span = (ends_at - starts_at).days
    source = "offline" if provenance.offline else "collected"
    return (
        *banner(f"{organization}/{repository}"),
        *pairs(
            (
                ("Window", f"{instant(starts_at)} to {instant(ends_at)} ({span} days)"),
                ("Provenance", f"{source}, {provenance.intervals_fetched} intervals fetched"),
            ),
        ),
    )


def render_condition(condition: ReadinessCondition) -> tuple[str, ...]:
    """Render one checked condition above the evidence behind it."""
    imposed = "" if condition.label is None else f"  [{condition.label.value}]"
    return f"    {condition.condition}{imposed}", f"      {condition.detail}"


def render_section(title: str, conditions: Sequence[ReadinessCondition]) -> tuple[str, ...]:
    """Render one titled group of checked conditions, saying so when the group is empty."""
    if not conditions:
        return (f"  {title}: none",)
    return f"  {title}", *(line for condition in conditions for line in render_condition(condition))


def render_assessment(assessment: ReadinessAssessment | None) -> tuple[str, ...]:
    """Render the readiness label above every condition that was checked to reach it."""
    if assessment is None:
        return *heading("Readiness"), "  not assessed: assessment.enabled is false"
    sections = (
        ("Blocking", assessment.blocking),
        ("Caution", assessment.caution),
        ("Clear", assessment.clear),
    )
    return (
        *heading(f"Readiness: {assessment.label.value.upper()}"),
        *(line for title, conditions in sections for line in render_section(title, conditions)),
    )


def render_cohort(cohort: CohortSummary) -> tuple[str, ...]:
    """Render which merges the window covers and who was left out of them.

    The total is the denominator every governance rate beside it is measured over, so it is
    shown rather than left to be worked out from the two rows above it.
    """
    excluded = tuple(
        (f"Excluded {login or '(unattributed)'}", str(count)) for login, count in cohort.excluded_authors.items()
    )
    return (
        *heading("Cohort"),
        *pairs(
            (
                ("Merged pull requests", str(cohort.merged)),
                ("Reported in cohort", str(cohort.reported)),
                ("Direct commits", str(cohort.direct_commits)),
                ("Total merges", str(cohort.reported + cohort.direct_commits)),
                *excluded,
            ),
        ),
    )


def render_merge_gate(report: MergeGateReport) -> tuple[str, ...]:
    """Render the stored merge gate, or the reason there is none to show."""
    read = "" if report.fetched_at is None else f", read {instant(report.fetched_at)}"
    title = f"Merge gate{read}"
    gate = report.gate
    if gate is None:
        return *heading(title), f"  not available: {report.detail}"
    required = max((rule.required_approving_review_count for rule in gate.pull_requests), default=0)
    contexts = tuple(check.context for rule in gate.status_checks for check in rule.required_status_checks)
    dismissed = any(rule.dismiss_stale_reviews_on_push for rule in gate.pull_requests)
    return (
        *heading(title),
        *pairs(
            (
                ("Branch", gate.branch),
                ("Protected", yes_or_no(value=gate.protected)),
                ("Rules observed", yes_or_no(value=gate.rules_observed)),
                ("Approving reviews required", str(required)),
                ("Required status checks", ", ".join(sorted(contexts)) or "none"),
                ("Dismiss stale reviews on push", yes_or_no(value=dismissed)),
                ("Applies to administrators", yes_or_no(value=gate.applies_to_administrators)),
                ("Restricts deletions", yes_or_no(value=gate.restricts_deletions)),
                ("Blocks force pushes", yes_or_no(value=gate.blocks_force_pushes)),
                ("Requires linear history", yes_or_no(value=gate.requires_linear_history)),
                ("Restricts branch names", yes_or_no(value=gate.restricts_branch_names)),
                # A limitation the JSON admits and the report hides is worse than one neither admits.
                ("Rules not interpreted", ", ".join(gate.unmodelled_rules) or "none"),
            ),
        ),
    )


def measured_window(report: OpenPullRequestReport) -> tuple[tuple[str, str], ...]:
    """Name the window the two windowed counts cover, as a row, or no row where none travelled.

    Two of the four counts are bounded by a window and two describe now, so the window is printed
    where the counts are rather than left to be read off the reporting window above them: a stored
    observation was measured over the window of the collection that made it, which is not the window
    the surrounding report covers.
    """
    if report.starts_at is None or report.ends_at is None:
        return ()
    return (("Opened and closed over", f"{instant(report.starts_at)} to {instant(report.ends_at)}"),)


def render_open_pull_requests(report: OpenPullRequestReport) -> tuple[str, ...]:
    """Render the stored open pull-request state, or the reason there is none to show.

    `read` is the instant the state was OBSERVED — the stored row's `fetched_at` on the default path,
    the run's own instant under `--refresh` — and never implies which of the two this report is.
    """
    read = "" if report.fetched_at is None else f", read {instant(report.fetched_at)}"
    title = f"Open pull requests{read}"
    measured = measured_window(report)
    summary = report.summary
    if summary is None:
        return *heading(title), f"  not available: {report.detail}"
    return (
        *heading(title),
        *pairs(
            (
                *measured,
                ("Opened in window", str(summary.opened_in_window)),
                ("Closed without merge", str(summary.closed_without_merge)),
                ("Currently open", str(summary.currently_open)),
                ("Stale open", str(summary.stale_open)),
            ),
        ),
    )


def alert_cells(count: OpenAlertCount) -> tuple[str, ...]:
    """Render one alert family's open total and its severity breakdown.

    A family GitHub refused renders every cell as the same absent marker the behaviour table uses,
    never as a zero, which would read as a family with nothing open. Its reason is printed under the
    table, where it cannot stretch a numeric column.
    """
    if count.open is None:
        return ("-",) * (len(AlertSeverity) + 1)
    return (str(count.open), *(str(count.by_severity.get(severity, 0)) for severity in AlertSeverity))


def render_security(report: SecurityAlertReport) -> tuple[str, ...]:
    """Render the stored open security alerts, or the reason there are none to show."""
    read = "" if report.fetched_at is None else f", read {instant(report.fetched_at)}"
    title = f"Security alerts{read}"
    alerts = report.alerts
    if alerts is None:
        return *heading(title), f"  not available: {report.detail}"
    families = alerts.families()
    return (
        *heading(title),
        *table(
            ("family", "open", *(severity.value for severity in AlertSeverity)),
            tuple((family.value, *alert_cells(count)) for family, count in families),
        ),
        *(f"  {family.value} not available: {count.detail}" for family, count in families if count.open is None),
        "  secret-scanning alerts carry no severity, so their severity columns are always zero",
    )


def rating_letter(rating: SonarRating | None) -> str:
    """Render one SonarCloud rating as the `A`-to-E letter every human reads it as.

    A value this build does not understand prints as the number SonarCloud sent under an explicit
    "unknown", never as a letter: the domain refuses to derive one, and defaulting to either end of
    the scale would report an unrecognised rating as the best or the worst there is.
    """
    if rating is None:
        return "-"
    letter = rating.letter
    return f"unknown ({rating.value:g})" if letter is None else letter


def sonar_project_rows(mapping: SonarProjectMapping | None) -> tuple[tuple[str, str], ...]:
    """Name the project the block reports and how it was attributed, or nothing where none was.

    The method is printed rather than left implicit because every rung of the resolution ladder has a
    different strength: a configured override is an instruction, and a stored map's answer is an
    inference from one commit. A wrong mapping is diagnosable only if the report says which answered.
    """
    if mapping is None:
        return ()
    return ("Project", mapping.project_key), ("Resolved by", mapping.method.value)


def render_gate_conditions(gate: SonarQualityGate | None) -> tuple[str, ...]:
    """Render every condition behind a gate level, saying in words where a gate reported none.

    The conditions are what make the verdict arguable, exactly as the merge gate prints its rules and
    the readiness assessment prints what it checked: "the gate failed" is an assertion, and "coverage
    62.1 against a threshold of 80" is the evidence for it. Both numbers are printed as the strings
    SonarCloud sent, beside the metric that gives them their meaning — a `1` against a rating and an
    `80` against coverage are not the same kind of number.
    """
    if gate is None:
        return ()
    if not gate.conditions:
        return ("  no gate conditions were reported for this project",)
    rows = tuple(
        (
            condition.metric,
            condition.comparator,
            "-" if condition.threshold is None else condition.threshold,
            "-" if condition.actual is None else condition.actual,
            condition.level.value,
        )
        for condition in gate.conditions
    )
    return table(("Metric", "Comparator", "Threshold", "Actual", "Level"), rows)


def sonar_measure_rows(measures: SonarMeasures) -> tuple[tuple[str, str], ...]:
    """Render every measure the project reported, leaving each unreported one visibly absent.

    Every row is printed even when its measure is absent, so the block says which measurements were
    asked for as well as which came back: a missing row would read as a metric nobody requested.
    """
    return (
        ("Coverage", percent(measures.coverage)),
        ("Duplicated lines", percent(measures.duplicated_lines_density)),
        ("Lines of code", quantity(measures.lines_of_code)),
        ("Violations", quantity(measures.violations)),
        ("Reliability issues", quantity(measures.reliability_issues)),
        ("Maintainability issues", quantity(measures.maintainability_issues)),
        ("Security issues", quantity(measures.security_issues)),
        ("Security hotspots", quantity(measures.security_hotspots)),
        ("Reliability rating", rating_letter(measures.reliability_rating)),
        ("Maintainability rating", rating_letter(measures.maintainability_rating)),
        ("Security rating", rating_letter(measures.security_rating)),
        ("Security review rating", rating_letter(measures.security_review_rating)),
    )


def render_sonar(report: SonarReport) -> tuple[str, ...]:
    """Render the stored SonarCloud state, or the reason there is none to show.

    REPORT-ONLY AND UNGRADED. A failing quality gate imposes no readiness ceiling and carries no
    label, per the standing rule that a signal becoming visible is not a reason to grade it; the
    conditions are printed so the gate can be read and argued with rather than obeyed.

    A project that resolved but whose measures could not be read is still NAMED above its reason,
    because the project a refusal was about is the first thing needed to chase it. A repository no
    project is mapped to says so — which most of the organisation's repositories do, and an absent
    block would read as a project whose gate nobody has looked at.
    """
    read = "" if report.fetched_at is None else f", read {instant(report.fetched_at)}"
    title = f"SonarCloud{read}"
    mapping, measures = report.mapping, report.measures
    if measures is None:
        return *heading(title), *pairs(sonar_project_rows(mapping)), f"  not available: {report.detail}"
    gate = measures.gate
    analysed = "never" if measures.analysis_at is None else instant(measures.analysis_at)
    return (
        *heading(title),
        *pairs(
            (
                *sonar_project_rows(mapping),
                ("Analysed", analysed),
                ("Quality gate", "not reported" if gate is None else gate.level.value),
            ),
        ),
        *render_gate_conditions(gate),
        *pairs(sonar_measure_rows(measures)),
    )


def render_codeowners(report: CodeownersReport) -> tuple[str, ...]:
    """Render the stored CODEOWNERS presence, or the reason there is none to show.

    A checked-and-absent repository says "absent" rather than rendering an empty table, because an
    observation of nothing and no observation must not read alike. The size column keeps an empty
    file visible as found-but-empty, and the recognised column keeps a `.md` variant — reported
    because the minimum standard names it, ignored by GitHub — apart from a file GitHub reads.
    """
    read = "" if report.fetched_at is None else f", read {instant(report.fetched_at)}"
    title = f"CODEOWNERS{read}"
    codeowners = report.codeowners
    if codeowners is None:
        return *heading(title), f"  not available: {report.detail}"
    if not codeowners.files:
        return *heading(title), "  absent: no CODEOWNERS file at any of the checked locations"
    rows = tuple(
        (file.path, str(file.size_bytes), yes_or_no(value=file.recognised_by_github)) for file in codeowners.files
    )
    return *heading(title), *table(("Location", "Bytes", "Recognised by GitHub"), rows)


def human_window_cell(window: MaintenanceWindowStatus) -> str:
    """Render one window's three-valued human answer, spelling the undecided value out.

    "unknown" rather than the gate's "not disclosed": nothing was withheld — the bounded search
    stopped before this window's cutoff — and its reason is printed under the table, where it
    cannot stretch the column.
    """
    if window.human_committed_within is None:
        return "unknown"
    return yes_or_no(value=window.human_committed_within)


def render_maintenance(report: MaintenanceReport) -> tuple[str, ...]:
    """Render the stored maintenance instants and their window rows, or the reason there are none.

    Presentation only: the window answers were derived at report assembly against `fetched_at`, so
    this prints them rather than re-deciding them. The search bound is shown whenever the JSON
    carries one, because it is what separates "no human commit within the window" from "unknown
    beyond the commits examined".
    """
    read = "" if report.fetched_at is None else f", read {instant(report.fetched_at)}"
    title = f"Maintenance{read}"
    maintenance = report.maintenance
    if maintenance is None:
        return *heading(title), f"  not available: {report.detail}"
    bound = maintenance.searched_back_to
    searched = () if bound is None else (("Searched back to", instant(bound)),)
    rows = tuple(
        (f"{window.months} months", yes_or_no(value=window.committed_within), human_window_cell(window))
        for window in report.windows
    )
    reasons = tuple(
        f"  {window.months} months human answer unknown: {window.human_detail}"
        for window in report.windows
        if window.human_detail is not None
    )
    return (
        *heading(title),
        *pairs(
            (
                ("Branch", maintenance.branch),
                (
                    "Last commit",
                    "none: the branch has no commits"
                    if maintenance.last_commit_at is None
                    else instant(maintenance.last_commit_at),
                ),
                (
                    "Last human commit",
                    "none found"
                    if maintenance.last_human_commit_at is None
                    else instant(maintenance.last_human_commit_at),
                ),
                *searched,
            ),
        ),
        *table(("Window", "Any commit", "Human commit"), rows),
        *reasons,
    )


def observation_cells(summary: RateObservation | DistributionObservation) -> tuple[str, ...]:
    """Render one metric aggregate into the shared value columns.

    A rate and a distribution are different shapes, so each fills the columns it has and leaves the
    rest visibly empty rather than printing a zero it never measured.
    """
    if summary.status is not ObservationStatus.OBSERVED:
        return "not applicable", "-", "-", "-", "-", "-"
    if isinstance(summary, RateObservation):
        percentage = round(summary.numerator / summary.denominator * 100, 1)
        return f"{percentage:g}%", f"{summary.numerator} of {summary.denominator}", "-", "-", "-", "-"
    return (
        "-",
        str(summary.sample_size),
        summary.unit,
        number(summary.median),
        number(summary.percentile_75),
        number(summary.percentile_90),
    )


def metric_columns() -> tuple[str, ...]:
    """Return the shared column titles for one metric aggregate."""
    return "Metric", "Rate", "Sample", "Unit", "Median", "p75", "p90"


def render_behaviour(summaries: Sequence[BehaviourMetricSummary]) -> tuple[str, ...]:
    """Render one row for every metric observed over the cohort."""
    rows = tuple((summary.metric, *observation_cells(summary.summary)) for summary in summaries)
    return *heading("Behaviour"), *table(metric_columns(), rows)


def render_review_states(counts: Mapping[str, int]) -> tuple[str, ...]:
    """Render how the cohort's review events were distributed across review states."""
    if not counts:
        return *heading("Review breakdown"), "  no review events in the cohort"
    return *heading("Review breakdown"), *pairs(tuple((state, str(count)) for state, count in counts.items()))


def render_findings(findings: Sequence[PracticeFinding]) -> tuple[str, ...]:
    """Render one row per actor-level finding, split by the size of the changes behind it."""
    if not findings:
        return *heading("Findings"), "  none"
    sizes = tuple(sorted({name for finding in findings for name in finding.occurrences_by_size}))
    rows = tuple(
        (
            finding.actor_login,
            finding.rule,
            str(finding.occurrences),
            str(finding.authored_merges),
            f"{finding.percentage:g}%",
            *(str(finding.occurrences_by_size.get(name, 0)) for name in sizes),
        )
        for finding in findings
    )
    return *heading("Findings"), *table(("Actor", "Rule", "Occurrences", "Authored", "Percent", *sizes), rows)


def render_unavailable(unavailable: Sequence[EvidenceUnavailable]) -> tuple[str, ...]:
    """Render the configured repositories the window could not be reported for."""
    if not unavailable:
        return ()
    return *heading("Unavailable"), *pairs(tuple((item.repository, item.detail) for item in unavailable))


def gate_observed(report: MergeGateReport) -> str:
    """Report whether one repository's merge-gate rules could be read at all.

    A gate that was never collected and a protected branch whose rules GitHub withheld are both
    `no` here, because neither can be argued with and both are what produce a `cannot_assess`
    label. The index exists partly to find those repositories, which need a permission rather than
    a change to how they work.
    """
    return yes_or_no(value=report.gate is not None and report.gate.rules_observed)


INDEX_NOT_ASSESSED = "not assessed"
"""How the index's readiness column names a repository the readiness policy left unjudged."""

INDEX_UNAVAILABLE = "unavailable"
"""How the index's readiness column names a repository the window could not be reported for."""


def index_readiness(assessment: ReadinessAssessment | None) -> str:
    """Render one repository's readiness label for the index, or say it was not judged.

    A label is upper-cased exactly as the section heading below renders it; anything that is not a
    label stays lower case, so a reader scanning the column cannot mistake one for the other.
    """
    return INDEX_NOT_ASSESSED if assessment is None else assessment.label.value.upper()


READINESS_LABEL_NAMES = tuple(label.value.upper() for label in ReadinessLabel)
"""Every readiness label as the index and the summary spell it, in declaration order, best first."""

SUMMARY_ROWS = (*READINESS_LABEL_NAMES, INDEX_NOT_ASSESSED, INDEX_UNAVAILABLE)
"""Every value the index's readiness column can hold, in the order the summary counts them.

The labels are read off `ReadinessLabel` rather than written out again, so a label added there is
counted here without a second list to keep in step. The two non-labels follow, because neither is a
grade and reading them among the labels would make them one."""


def counted_row(name: str, count: int, total: int) -> tuple[str, str, str]:
    """Render one summary row: what was counted, how many there are, and their share of the total."""
    return name, str(count), percentage_of(count, total)


def readiness_counts(report: PracticeEvidenceReport) -> Mapping[str, int]:
    """Count the repositories behind each value the index's readiness column shows.

    Keyed by the strings the column itself prints, through `index_readiness`, so a reader can add the
    index's rows up and arrive at these figures rather than at a second set of names for them.
    """
    counted = dict.fromkeys(SUMMARY_ROWS, 0)
    for item in report.repositories:
        counted[index_readiness(item.assessment)] += 1
    counted[INDEX_UNAVAILABLE] += len(report.unavailable)
    return counted


def render_summary(report: PracticeEvidenceReport) -> tuple[str, ...]:
    """Render how many repositories carry each readiness label, and each label's share of them.

    Each percentage is over every repository the report covers — the ones that reported and the ones
    that could not — which is the same population every row here is counted over, so the counts
    account for all of it. Each share is rounded to a tenth on its own, so the printed column can
    read 99.9 or 100.1 rather than exactly a hundred; the counts are what reconcile, not the shares.

    A DISTRIBUTION, NOT A VERDICT — the distinction the roll-up boundary turns on. Across the hmcts
    organisation the index runs to more than a thousand rows and cannot be read as a shape, so the
    user asked on 2026-08-31 for the counts to open the report; architecture.md "Scope boundaries"
    records the reversal of the no-count ruling that governed the index until then. What stays excluded is
    unchanged: no combined label, no per-team figure, no score, and no ordering of teams or people by
    what they carry. Every count is over the whole population the report covers.

    The four labels are always printed, zero included, so two runs diff line for line and a label
    nobody carries reads as an observation rather than an omission. The two non-labels are printed
    only where they occur, because a repository is either assessed or it is not, and a zero there is
    the absence of a state rather than a count of one.
    """
    counted = readiness_counts(report)
    total = sum(counted.values())
    rows = tuple(
        counted_row(name, counted[name], total)
        for name in SUMMARY_ROWS
        if name in READINESS_LABEL_NAMES or counted[name]
    )
    widths = tuple(max(len(row[column]) for row in rows) for column in range(3))
    return *heading("Repository Summary"), *(row_text(row, widths) for row in rows)


def render_index(report: PracticeEvidenceReport, teams: Mapping[str, str]) -> tuple[str, ...]:
    """Render a table of contents for a report covering a population of repositories.

    One row per configured repository, in the order the body renders them: the repositories that
    reported, then the ones that could not. At fourteen repositories the report is long enough that
    the three questions a reader opens it with — which repositories are assessable at all, which are
    RED, and which need reading in full — should be answerable before scrolling.

    NOT A ROLL-UP. There is no combined label and no per-team verdict: listing fourteen labels is
    presentation, and reducing them to one is what architecture.md "Scope boundaries" forbids. The
    counts the report now opens with are `render_summary`, which counts each label separately and
    combines none of them; no row here carries a figure. `teams` says who owns a repository, nothing
    more.

    Every cell is read from the same models `render_repository` consumes, so the index and the block
    it points at cannot disagree.
    """
    rows = (
        *(
            (teams[item.repository], item.repository, index_readiness(item.assessment), gate_observed(item.merge_gate))
            for item in report.repositories
        ),
        *((teams[item.repository], item.repository, INDEX_UNAVAILABLE, "-") for item in report.unavailable),
    )
    return *heading("Index"), *table(("Team", "Repository", "Readiness", "Gate observed"), rows)


READINESS_ORDER = (*ReadinessPolicy.precedence, ReadinessLabel.GREEN)
"""Every label worst first, extending the policy's own precedence with the green it falls back to.

Read off the policy rather than written out a second time, so reordering severity there cannot leave
the tie-break here sorting labels against the order they were assigned under."""


@dataclass(frozen=True)
class ReadinessGroup:
    """Tally one readiness label across every repository of one actor's that carries it.

    `contributions` is the total behind the group and is what orders it, because the label somebody
    does most of their work under is the one worth leading with. The number itself is not printed:
    the line abridges, and the JSON carries the per-repository counts it was summed from.
    """

    readiness: ReadinessLabel | None
    repositories: tuple[str, ...]
    contributions: int


def readiness_severity(readiness: ReadinessLabel | None) -> int:
    """Rank one label for the tie-break, sorting an unassessed repository after every label.

    Last rather than first: a repository with no label is not the mildest finding on a line, it is
    the absence of one, and ranking it among the labels would read as a grade between them.
    """
    return len(READINESS_ORDER) if readiness is None else READINESS_ORDER.index(readiness)


def readiness_tally(repositories: Sequence[ActorRepositoryReadiness]) -> tuple[ReadinessGroup, ...]:
    """Group one actor's repositories by label, tallying each label exactly once.

    Grouped rather than listed in the order they arrive, because the same label twice on a line reads
    as two separate findings about a person rather than one. Groups are ordered by the contributions
    behind them and, where those tie, by severity, so neither a heavy green nor an ordering accident
    can put a red anywhere but where its weight puts it. Repositories keep the contribution order
    they arrive in, so the repository a group is mostly about names it first.
    """
    grouped: dict[ReadinessLabel | None, list[ActorRepositoryReadiness]] = {}
    for row in repositories:
        grouped.setdefault(row.readiness, []).append(row)
    groups = (
        ReadinessGroup(
            readiness=readiness,
            repositories=tuple(row.repository for row in rows),
            contributions=sum(row.contributions for row in rows),
        )
        for readiness, rows in grouped.items()
    )
    return tuple(sorted(groups, key=lambda group: (-group.contributions, readiness_severity(group.readiness))))


def actor_readiness_label(readiness: ReadinessLabel | None) -> str:
    """Render one tallied label, naming an unassessed repository rather than leaving a gap.

    Upper case throughout, where `index_readiness` keeps "not assessed" lower to separate it from the
    labels in its column: here the labels are the whole of the line, and one lower-case entry among
    them would read as a comment on the line rather than an entry in it.
    """
    return "NOT ASSESSED" if readiness is None else readiness.value.upper()


def render_readiness_group(group: ReadinessGroup, *, named: bool) -> str:
    """Render one tallied label, with a count where it covers several repositories."""
    count = len(group.repositories)
    label = actor_readiness_label(group.readiness)
    tally = label if count == 1 else f"{label} x {count}"
    return f"{tally} ({', '.join(group.repositories)})" if named else tally


def actor_line(repositories: Sequence[ActorRepositoryReadiness]) -> str:
    """Render one person's labels, weightiest first, naming repositories only where they differ.

    An actor whose repositories all carry one label renders `RED x 6` alone: the names would be that
    person's whole list of repositories printed for no distinction. Where the labels differ the names
    are the point, because which of them is the red one is the next thing asked.
    """
    groups = readiness_tally(repositories)
    return ", ".join(render_readiness_group(group, named=len(groups) > 1) for group in groups)


ACTOR_COMBINATION_ORDER = (*READINESS_LABEL_NAMES, actor_readiness_label(None))
"""Every label one person's line can carry, best first, with the absence of a label last.

Read off `READINESS_LABEL_NAMES` and `actor_readiness_label` rather than written out again, so a label
added to `ReadinessLabel` orders itself here. `NOT ASSESSED` is appended rather than sorted among the
labels, for the reason `readiness_severity` ranks it last: it is the absence of a grade, not the
mildest one."""


def actor_combination(repositories: Sequence[ActorRepositoryReadiness]) -> tuple[str, ...]:
    """Return the DISTINCT labels one person's line carries, best first.

    Distinct because multiplicity is not part of the combination: `RED x 6` and `RED` say the same
    thing about a person — everything they work in is red — and telling them apart would split one
    population by how many repositories somebody happens to author in. `RED x 2, GREEN` is therefore
    `GREEN, RED`.

    Ordered by `ACTOR_COMBINATION_ORDER` rather than by the weight `readiness_tally` orders the
    printed line by, so one pair of labels is one key however the contributions behind them fall.
    """
    carried = {actor_readiness_label(row.readiness) for row in repositories}
    return tuple(name for name in ACTOR_COMBINATION_ORDER if name in carried)


ACTOR_GROUPS: Mapping[str, tuple[tuple[str, ...], ...]] = {
    "Enable": (("GREEN",), ("GREEN", "AMBER")),
    "Review": (("AMBER",), ("GREEN", "AMBER", "RED"), ("GREEN", "RED")),
    "Blocked": (("AMBER", "RED"), ("RED",)),
}
"""Which combination of labels puts a person under which of the three named actions.

Transcribed from the user's instruction of 2026-09-01, groups and rows in the order given. WRITTEN
OUT rather than derived from a rule, because it is a ruling about what to do next and not a property
of the labels: `GREEN, AMBER` enables while `AMBER` alone is reviewed, which no ordering or severity
of the labels yields. A label added to `ReadinessLabel` is consequently not grouped by default — it
reaches `Ungrouped` until somebody rules on it, which is the outcome a defaulted guess would hide.

The labels are spelled here, where the rest of the module reads them off `READINESS_LABEL_NAMES`,
because these rows are the ruling's own text; a test asserts every name in the table is one this
section can print, so a renamed label cannot leave the table quietly unreachable."""

ACTOR_UNGROUPED = "Ungrouped"
"""What a combination the group table does not cover is reported under."""


def actor_group(combination: tuple[str, ...]) -> str:
    """Name the action a combination of labels puts a person under, or say it is not grouped.

    Only a combination carrying `NOT ASSESSED` can reach the fallback: all seven non-empty subsets of
    GREEN, AMBER and RED are mapped, and `reported_repositories` has already dropped `cannot_assess`, so
    there is nothing else a graded line can be.
    """
    for name, combinations in ACTOR_GROUPS.items():
        if combination in combinations:
            return name
    return ACTOR_UNGROUPED


def actor_combination_counts(actors: Sequence[ActorReadiness]) -> Mapping[tuple[str, ...], int]:
    """Count the people behind each combination of labels the actor section reports.

    Counted over the same `reported_repositories` the rendered lines are built from, so the counts and
    the lines cannot disagree. Anyone that leaves with no repository is skipped for the reason they
    get no line: a person with no label beside them is not a finding to count.
    """
    counted: dict[tuple[str, ...], int] = {}
    for item in actors:
        repositories = reported_repositories(item)
        if repositories:
            combination = actor_combination(repositories)
            counted[combination] = counted.get(combination, 0) + 1
    return counted


ACTOR_ROW_INDENT = "  "
"""How far a row is set in from the group name above it, in the summary block and in the list."""


def ungrouped_combinations(counted: Mapping[tuple[str, ...], int]) -> tuple[tuple[str, ...], ...]:
    """Return the combinations the group table does not cover, ordered as their labels are.

    Ordered through `ACTOR_COMBINATION_ORDER` rather than alphabetically, so these rows read in the
    same best-first order as the labels within each of them, and two runs over the same population
    print them the same way round.
    """
    position = {name: index for index, name in enumerate(ACTOR_COMBINATION_ORDER)}
    ungrouped = (combination for combination in counted if actor_group(combination) == ACTOR_UNGROUPED)
    return tuple(sorted(ungrouped, key=lambda combination: [position[name] for name in combination]))


def actor_summary_groups(counted: Mapping[tuple[str, ...], int]) -> tuple[tuple[str, tuple[tuple[str, ...], ...]], ...]:
    """Return the groups this block prints, in order, with the combination rows under each.

    The three named groups and their seven rows are printed at every run, zero included, for the
    reason `render_summary` prints every label: a combination nobody carries is an observation about
    the population and reads as one only where the row is there to be read. `Ungrouped` is printed
    only where something reaches it, because it is not one of the actions the user named — a heading
    with nothing under it would offer a fourth.
    """
    ungrouped = ungrouped_combinations(counted)
    return (*ACTOR_GROUPS.items(), *(((ACTOR_UNGROUPED, ungrouped),) if ungrouped else ()))


def render_actor_summary(actors: Sequence[ActorReadiness]) -> tuple[str, ...]:
    """Render how many people carry each combination of labels, under the action it puts them under.

    Each percentage is over the people the actor section reports — the same population every row is
    counted over, and not everyone the report covers, because somebody left with no repository by the
    `cannot_assess` exclusion gets no line and is counted in nothing here. Every share is rounded to a
    tenth on its own, so a group's printed share can differ from its rows' shares added up by a tenth
    or two; the counts are what reconcile, and a subtotal is always the sum of the counts under it.

    THE SUBTOTALS ARE A ROLL-UP, and a deliberate one: the user instructed on 2026-09-01 that each of
    Enable, Review and Blocked carries its own figure, which is a further dated reversal of the
    no-roll-up ruling — architecture.md "Scope boundaries" records it. What the reversal admits is a
    count of people per named action and nothing more: no per-team figure, no score, no verdict on
    any individual, and no ordering of people by what they carry.
    """
    counted = actor_combination_counts(actors)
    total = sum(counted.values())
    rows = tuple(
        row
        for name, combinations in actor_summary_groups(counted)
        for row in (
            counted_row(name, sum(counted.get(combination, 0) for combination in combinations), total),
            *(
                counted_row(f"{ACTOR_ROW_INDENT}{', '.join(combination)}", counted.get(combination, 0), total)
                for combination in combinations
            ),
        )
    )
    widths = tuple(max(len(row[column]) for row in rows) for column in range(3))
    return *heading("Actor Summary"), *(row_text(row, widths) for row in rows)


def render_actors(actors: Sequence[ActorReadiness]) -> tuple[str, ...]:
    """Render one abridged line per person, under the action their labels put them under.

    STILL NOT A PER-PERSON VERDICT: a person contributing to a red repository and a green one has no
    single readiness, so the line lists both labels and combines them nowhere. The group name above
    them is the action `ACTOR_GROUPS` puts that combination under, by the user's instruction of
    2026-09-01 — it names what to do next about a pair of labels, not a judgement of the person
    carrying them — and there is no score, no per-team figure and no ordering of people by what they
    carry. architecture.md "Scope boundaries" records the instruction.

    A group nobody is in is left out rather than printed empty: `render_actor_summary` reports that
    zero above, and a heading with nothing under it here would read as a list that failed to render.

    The heading says the `cannot_assess` repositories are left out, so a reader comparing a person's
    labels with the index cannot read the shorter list as the whole of what they work in.
    """
    title = "Actors (cannot_assess repositories excluded)"
    reported = tuple(
        (actor.actor_login, repositories) for actor in actors if (repositories := reported_repositories(actor))
    )
    if not reported:
        detail = (
            "no person authored a merge in the reported repositories"
            if not actors
            else "every repository the reported people contributed to could not be assessed"
        )
        return *heading(title), f"  none: {detail}"
    # One width over the whole section rather than one per group, so a person's labels start in the
    # same column throughout and two groups can be read down as one list.
    width = max(len(login) for login, _ in reported)
    grouped: dict[str, list[str]] = {}
    for login, repositories in reported:
        line = f"{ACTOR_ROW_INDENT}  {login:<{width}}  {actor_line(repositories)}"
        grouped.setdefault(actor_group(actor_combination(repositories)), []).append(line)
    order = (*ACTOR_GROUPS, ACTOR_UNGROUPED)
    return (
        *heading(title),
        *(line for name in order if name in grouped for line in (f"  {name}", *grouped[name])),
    )


def render_repository(
    organization: str,
    evidence: RepositoryPracticeEvidence,
    drill_down: RepositoryDrillDown,
) -> tuple[str, ...]:
    """Render one repository's readiness, cohort, stored current state, behaviour, and findings."""
    return (
        *render_header(organization, evidence.repository, evidence.starts_at, evidence.ends_at, evidence.provenance),
        *render_assessment(evidence.assessment),
        *render_cohort(evidence.cohort),
        *render_merge_gate(evidence.merge_gate),
        *render_security(evidence.security),
        *render_sonar(evidence.sonar),
        *render_codeowners(evidence.codeowners),
        *render_maintenance(evidence.maintenance),
        *render_open_pull_requests(evidence.open_pull_requests),
        *render_behaviour(drill_down.behaviour),
        *render_review_states(drill_down.review_states),
        *render_findings(evidence.behaviour),
    )


def render_metric_report(report: BehaviourEvidenceReport) -> tuple[str, ...]:
    """Render one metric drill-down: its aggregate and the classifications behind it."""
    return (
        *render_header(report.organization, report.repository, report.starts_at, report.ends_at, report.provenance),
        *render_cohort(report.cohort),
        *heading(f"Metric: {report.metric}"),
        *table(metric_columns(), ((report.metric, *observation_cells(report.summary)),)),
        *heading("Classifications"),
        *pairs(tuple((name, str(count)) for name, count in report.classifications.items())),
    )


def render_practice_report(
    report: PracticeEvidenceReport,
    drill_downs: Mapping[str, RepositoryDrillDown],
    teams: Mapping[str, str],
) -> str:
    """Render the default practice report for every reported repository, under its counts and index."""
    return rendered(
        (
            *render_summary(report),
            *render_index(report, teams),
            *(
                line
                for evidence in report.repositories
                for line in render_repository(report.organization, evidence, drill_downs[evidence.repository])
            ),
            *render_unavailable(report.unavailable),
            *render_actor_summary(report.actors),
            *render_actors(report.actors),
        ),
    )


def render_metric_collection(report: BehaviourEvidenceCollection) -> str:
    """Render one metric drill-down for every reported repository."""
    return rendered(
        (
            *(line for item in report.repositories for line in render_metric_report(item)),
            *render_unavailable(report.unavailable),
        ),
    )


TREND_COUNTS = tuple(
    name
    for name, _ in TrendThroughput(
        merges=0,
        merged_pull_requests=0,
        direct_commits=0,
        active_contributors=0,
    ).measures()
)
"""The cohort counts every window of a series carries, named exactly as its deltas compare them.

Read off the model rather than written out again: a series every window of which observed nothing
still prints these rows, so the names are needed without an observation to read them from, and a
second literal here is what would let a renamed count render as a permanently absent row."""


@dataclass(frozen=True)
class TrendMeasure:
    """Name one row of a rendered series: what is compared, how it is labelled, and in what unit.

    Built once per series and used by both tables, so a measure cannot be labelled one way beside
    its values and another beside its deltas.
    """

    measure: str
    label: str
    unit: str


def throughput_values(throughput: TrendThroughput) -> dict[str, float]:
    """Return one window's cohort counts under the measure names its deltas are reported by."""
    return dict(throughput.measures())


def window_values(window: TrendWindow) -> dict[str, float | None]:
    """Return every value one window observed, by measure: its cohort counts and its metric values.

    A measure absent from the mapping and one present as None both render as the same absent
    marker, which is the honest rendering of each: a window that observed nothing and a metric with
    no eligible sample have neither of them measured a zero.
    """
    values: dict[str, float | None] = {}
    if window.throughput is not None:
        values.update(throughput_values(window.throughput))
    values.update({item.metric: item.value for item in window.metrics})
    return values


def metric_measure(item: TrendMetric) -> TrendMeasure:
    """Describe one behaviour metric's row, naming the percentile a distribution is read at.

    The percentile is printed rather than left implicit: a series read at the median and one read at
    the 75th percentile are different measurements, and a reader cannot tell which from the number.
    """
    unit = "percent" if isinstance(item.summary, RateObservation) else item.summary.unit
    label = item.metric if item.percentile is None else f"{item.metric} ({item.percentile.label})"
    return TrendMeasure(measure=item.metric, label=label, unit=unit)


def series_measures(windows: Sequence[TrendWindow]) -> tuple[TrendMeasure, ...]:
    """Return one row per measure the series reports: the cohort counts, then every metric observed.

    Taken across every window rather than from one, because a window whose values are suppressed
    reports no metric at all and would otherwise decide the rows for the whole series.
    """
    metrics: dict[str, TrendMetric] = {}
    for window in windows:
        for item in window.metrics:
            metrics.setdefault(item.metric, item)
    counts = tuple(TrendMeasure(measure=name, label=name, unit="count") for name in TREND_COUNTS)
    return counts + tuple(metric_measure(item) for item in metrics.values())


def period_columns(periods: Sequence[TrendPeriod]) -> tuple[str, ...]:
    """Return one column title per whole period, numbered from the enablement instant."""
    return tuple(f"P{period.index}" for period in periods)


def named_windows(baseline: TrendWindow, periods: Sequence[TrendPeriod]) -> tuple[tuple[str, TrendWindow], ...]:
    """Return every window of one series beside the name its columns and reasons are printed under.

    Takes the baseline a caller has already found rather than the repository: a series with none is
    rendered as its summary alone and never reaches here, so there is no absent baseline to handle.
    """
    return (("Baseline", baseline), *((f"P{period.index}", period) for period in periods))


def render_trend_windows(named: Sequence[tuple[str, TrendWindow]]) -> tuple[str, ...]:
    """Render which dates each window of the series covers, and why any of them observed nothing.

    A reason gets its own line under the table, as the alert families do: a refusal inside a column
    would stretch the instants out of alignment, and a suppressed window is exactly what a reader
    scanning a flat stretch of the series needs to see explained.
    """
    rows = tuple((name, instant(window.starts_at), instant(window.ends_at)) for name, window in named)
    reasons = tuple(f"  {name}: {window.detail}" for name, window in named if window.detail is not None)
    return *heading("Windows"), *table(("Window", "Starts", "Ends"), rows), *reasons


def render_trend_values(
    baseline: TrendWindow,
    periods: Sequence[TrendPeriod],
    measures: Sequence[TrendMeasure],
) -> tuple[str, ...]:
    """Render one row per measure and one column per window, so a series reads left to right.

    Metrics in rows rather than periods in rows: the columns then grow with the number of periods
    and the rows stay fixed, which is what a six-period series needs — a reader follows one metric
    across the whole series on one line, instead of reading a metric's history down a column.
    """
    observed = tuple(window_values(window) for window in (baseline, *periods))
    rows = tuple(
        (measure.label, measure.unit, *(number(values.get(measure.measure)) for values in observed))
        for measure in measures
    )
    return *heading("Values"), *table(("Measure", "Unit", "Baseline", *period_columns(periods)), rows)


def delta_basis(measure: str, deltas: Sequence[Mapping[str, TrendDelta]]) -> str:
    """Return what arithmetic one measure's deltas report, named as the JSON names it.

    A percentage point and a percentage change are different quantities that both print as a bare
    number, so the basis is a column rather than a footnote.
    """
    found = next((entry[measure] for entry in deltas if measure in entry), None)
    return "-" if found is None else found.basis.value


def signed(value: float | None) -> str:
    """Format one movement so its direction is visible, leaving an uncomputed one absent.

    A rise carries its `+`: an unsigned `50` beside a `-50` reads as a bare value rather than a
    movement, and the two are the only things this column holds.
    """
    return f"+{value:g}" if value is not None and value > 0 else number(value)


def delta_cell(measure: str, deltas: Mapping[str, TrendDelta]) -> str:
    """Render one period's movement in one measure, leaving an uncomputed one visibly absent."""
    found = deltas.get(measure)
    return "-" if found is None else signed(found.change)


def render_trend_changes(item: RepositoryTrend, measures: Sequence[TrendMeasure]) -> tuple[str, ...]:
    """Render how each period moved from the baseline, or the reason nothing could be compared.

    Nothing here is graded: no delta carries a threshold, a boundary or a colour, and a movement in
    either direction is printed the same way. A delta that could not be computed says why under the
    table rather than printing a number nobody measured.
    """
    if item.delta_detail is not None:
        return *heading("Change from baseline"), f"  not computed: {item.delta_detail}"
    deltas = tuple({delta.measure: delta for delta in period.deltas} for period in item.periods)
    rows = tuple(
        (
            measure.label,
            delta_basis(measure.measure, deltas),
            *(delta_cell(measure.measure, entry) for entry in deltas),
        )
        for measure in measures
    )
    reasons = tuple(
        f"  P{period.index} {delta.measure}: {delta.detail}"
        for period in item.periods
        for delta in period.deltas
        if delta.detail is not None
    )
    return (
        *heading("Change from baseline"),
        *table(("Measure", "Basis", *period_columns(item.periods)), rows),
        *reasons,
    )


def render_alert_observations(observations: Sequence[AlertObservation]) -> tuple[str, ...]:
    """Render each open-alert count at the instant it was observed, and nothing between them.

    A row per observation rather than a column per period, unlike the two tables above it: these are
    SAMPLES taken whenever `collect` ran, not measurements of a window, and laying them out under
    period headings would assert that each belongs to a period. Nothing is interpolated onto a
    boundary and nothing is zero-filled between two observations — a stretch with no row is a
    stretch nobody looked at, which is exactly what it should look like.
    """
    title = "Security alert observations"
    if not observations:
        return *heading(title), "  none recorded across the windows this series reports"
    rows = tuple(
        (
            observation.family.value,
            instant(observation.fetched_at),
            str(observation.open),
            *(str(observation.by_severity.get(severity, 0)) for severity in AlertSeverity),
        )
        for observation in observations
    )
    return (
        *heading(title),
        *table(("Family", "Observed", "open", *(severity.value for severity in AlertSeverity)), rows),
        "  observed when collect ran, so a gap between rows is a gap in the collection cadence",
        "  secret-scanning alerts carry no severity, so their severity columns are always zero",
    )


def periods_reported(count: int) -> str:
    """Say how many whole periods a series holds, reading correctly at one as well as at six."""
    return f"{count} whole period{'' if count == 1 else 's'} since enablement"


def trend_summary(item: RepositoryTrend, period_days: int) -> tuple[tuple[str, str], ...]:
    """Summarise one repository's series: its anchor, how it is cut, and how much of it exists."""
    series = periods_reported(len(item.periods)) if item.periods else f"none: {item.detail}"
    return ("Enabled", index_enablement(item)), ("Period", f"{period_days} days"), ("Series", series)


def render_repository_trend(organization: str, item: RepositoryTrend, period_days: int) -> tuple[str, ...]:
    """Render one repository's series, or the summary alone where there is no series to render."""
    header = (*banner(f"{organization}/{item.repository}"), *pairs(trend_summary(item, period_days)))
    if item.baseline is None:
        return header
    measures = series_measures((item.baseline, *item.periods))
    return (
        *header,
        *render_trend_windows(named_windows(item.baseline, item.periods)),
        *render_trend_values(item.baseline, item.periods, measures),
        *render_trend_changes(item, measures),
        *render_alert_observations(item.alert_observations),
    )


def index_enablement(item: RepositoryTrend) -> str:
    """Render one repository's enablement instant, or say that none was configured."""
    return "not configured" if item.enablement_at is None else instant(item.enablement_at)


def index_periods(item: RepositoryTrend) -> str:
    """Say how many of ONE repository's own periods were observed, or that it reports none.

    A count of that repository's windows and nothing else. This column is where a roll-up would
    first appear — an average of deltas, a combined figure per team — and none is built: see
    architecture.md, "Scope boundaries".
    """
    if not item.periods:
        return "none"
    return f"{sum(1 for period in item.periods if period.cohort is not None)} of {len(item.periods)}"


def render_trend_index(report: TrendReport, teams: Mapping[str, str]) -> tuple[str, ...]:
    """Render a table of contents for the trend: one row per configured repository, combining nothing.

    Like the practice index it LISTS. There is no total, no average and no organisation-level figure,
    because an average of deltas is the team roll-up architecture.md forbids wearing a different hat.
    Every cell is read from the same models the series below it renders.
    """
    rows = tuple(
        (teams[item.repository], item.repository, index_enablement(item), index_periods(item))
        for item in report.repositories
    )
    return *heading("Index"), *table(("Team", "Repository", "Enabled", "Periods observed"), rows)


def render_trend_report(report: TrendReport, teams: Mapping[str, str]) -> str:
    """Render every repository's series under an index, in the order the report lists them.

    Presentation only, like every other rendering here: each figure comes from the trend JSON, so
    the two cannot disagree, and neither states a cause for what it reports.
    """
    return rendered(
        (
            *render_trend_index(report, teams),
            *(
                line
                for item in report.repositories
                for line in render_repository_trend(report.organization, item, report.period_days)
            ),
        ),
    )


def render_report(
    report: PracticeEvidenceReport | BehaviourEvidenceCollection | BehaviourEvidenceReport,
    drill_downs: Mapping[str, RepositoryDrillDown],
    teams: Mapping[str, str],
) -> str:
    """Render whichever report shape the requested evidence mode produced.

    `teams` is used by the practice report alone: a metric drill-down is one neutral aggregate per
    repository and needs no table of contents to find a label in.
    """
    if isinstance(report, PracticeEvidenceReport):
        return render_practice_report(report, drill_downs, teams)
    if isinstance(report, BehaviourEvidenceCollection):
        return render_metric_collection(report)
    return rendered(render_metric_report(report))
