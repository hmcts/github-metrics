"""Assemble post-enablement period series over the same evidence path `evidence` reports one window with.

Measurement only. No value here is graded, no delta carries a threshold or a colour, and
`ReadinessPolicy` reads nothing from any of it — see architecture.md, "Trend measurement". A
worsening series is a fact for a human conversation, not a verdict.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from metrics.analysis import contributor_logins, rate_percentage
from metrics.behaviour_metrics import behaviour_metrics
from metrics.behaviour_metrics.base import BehaviourMetric
from metrics.config import Configuration, enablement_instants
from metrics.domain import (
    AlertObservation,
    CollectionStatus,
    DeltaBasis,
    DistributionObservation,
    EvidenceUnavailable,
    Percentile,
    RateObservation,
    ReportingWindow,
    RepositoryTrend,
    TrendDelta,
    TrendMetric,
    TrendPeriod,
    TrendReport,
    TrendThroughput,
    TrendWindow,
)
from metrics.evidence import RepositoryEvidence, cached_repository_evidence, collected_repository_evidence
from metrics.github import GitHubClient
from metrics.storage import load_alert_observations, observation_database
from metrics.window import baseline_window, period_windows

NO_ENABLEMENT_DATE = "no enablement date configured"
"""Why a configured repository has no series, in the wording architecture.md settles.

Reported rather than guessed at, and reported rather than dropped: the tool cannot observe when
tooling was turned on, and an anchor nobody chose would be worse than none.
"""

NO_WHOLE_PERIOD = "no whole period has elapsed since enablement"
"""Why an anchored repository has no series yet: only the trailing partial period exists so far."""

BASELINE_ZERO = "baseline-zero"
"""Why a count that grew from nothing reports no percentage change, in the wording the plan fixes.

Every increase from zero is an infinite percentage, and an infinity in a leadership-facing report is
arithmetic dressed as evidence. The two counts are reported and the reader draws their own line.
"""


def no_baseline_delta(detail: str) -> str:
    """Say why a series compares none of its periods, quoting what the baseline itself reported."""
    return f"the baseline window is not comparable, so no delta was computed: {detail}"


@dataclass(frozen=True)
class SeriesRequest:
    """Hold one trend run's shared inputs: how the series is cut, and how its windows are answered.

    `client` is None exactly when the run is offline, which is what makes the cache the only source,
    exactly as it is for `evidence`. One request per run, so every repository's series is cut the
    same way and anchored against the same reference instant.
    """

    period_days: int
    periods: int | None
    reference: datetime
    client: GitHubClient | None

    def __post_init__(self) -> None:
        """Reject a request that cannot describe a period, before any window is resolved.

        Checked here rather than at the first repository carrying an enablement date: a
        configuration where nobody is enabled yet must still refuse `--period-days 0` rather than
        report an empty series as though the request were fine.
        """
        if self.period_days < 1:
            message = "a period must span at least one day"
            raise ValueError(message)
        if self.periods is not None and self.periods < 1:
            message = "a series must report at least one period"
            raise ValueError(message)

    @property
    def span(self) -> timedelta:
        """Return the span each period of the series covers."""
        return timedelta(days=self.period_days)


def window_throughput(observed: RepositoryEvidence) -> TrendThroughput:
    """Count one window's merges, and the people who authored them, over the cohort it carries.

    The merge counts are taken after the configured author exclusions, so throughput and the
    governance denominators describe the same set of changes; counting merges the rates ignore would
    let the two blocks of one period contradict each other.

    Contributors are counted over that SAME cohort — the fact tuples are the ones the exclusions
    already filtered — and then narrowed again to people, because a bot account is not a
    contributor even where its merges are reported. The narrowing lives in `contributor_logins`
    rather than here, so the trend and every other count of people agree on what a bot is.
    """
    cohort = observed.cohort
    return TrendThroughput(
        merges=cohort.reported + cohort.direct_commits,
        merged_pull_requests=cohort.reported,
        direct_commits=cohort.direct_commits,
        active_contributors=len(contributor_logins((*observed.pull_requests, *observed.direct_commits))),
    )


def trend_metric(metric: BehaviourMetric, summary: RateObservation | DistributionObservation) -> TrendMetric:
    """Report one metric's observation beside the single number a series compares windows on.

    A rate is compared as a percentage; a distribution at the fixed percentile the metric declares
    and the readiness assessment grades, read through the metric class so the two cannot diverge.
    Either is None where the window observed no eligible sample, and a metric with no value in one
    of the two windows has no delta rather than a delta against nothing.
    """
    if isinstance(summary, DistributionObservation):
        return TrendMetric(
            metric=metric.identifier,
            summary=summary,
            value=metric.percentile.of(summary),
            percentile=metric.percentile,
        )
    return TrendMetric(metric=metric.identifier, summary=summary, value=rate_percentage(summary))


def window_metrics(configuration: Configuration, evidence: RepositoryEvidence) -> tuple[TrendMetric, ...]:
    """Compute every behaviour metric for one window through the path `evidence` uses.

    `RepositoryEvidence.metric` is the single implementation, called here per period exactly as the
    `evidence` command calls it per window. Copying the computation is how `collect` once disagreed
    with `evidence` about the same repository; `test_trend_and_evidence_agree_over_one_window` pins
    the agreement so the copy cannot be reintroduced quietly.
    """
    return tuple(
        trend_metric(metric, evidence.metric(metric, include_identities=False).summary)
        for metric in behaviour_metrics(configuration.traceability)
    )


def thin_window(counted: int, minimum: int) -> str | None:
    """Return why a window's metric values are suppressed, or None when its cohort is big enough."""
    if counted >= minimum:
        return None
    return f"{counted} merges into the default branch, below the minimum of {minimum}, so metric values are suppressed"


def window_facts(
    configuration: Configuration,
    window: ReportingWindow,
    observed: RepositoryEvidence | EvidenceUnavailable,
) -> TrendWindow:
    """Describe one window: its counts and metric values, or the reason it has none.

    The reported model is built here rather than in a private shape mapped into it afterwards, so a
    window's invariant — cohort, throughput and provenance together or not at all — is checked where
    the window is described instead of one layer later.

    The thin-window discipline is applied PER PERIOD: below `assessment.minimum_merges` the counts
    are reported and the metric values are suppressed with the reason. The minimum is read whether
    or not the readiness assessment is enabled — it says when a rate stops being arithmetic, which
    is a measurement question rather than a grading one, and nothing here is graded either way.
    """
    if isinstance(observed, EvidenceUnavailable):
        return TrendWindow(starts_at=window.starts_at, ends_at=window.ends_at, detail=observed.detail)
    suppressed = thin_window(
        observed.cohort.reported + observed.cohort.direct_commits,
        configuration.assessment.minimum_merges,
    )
    return TrendWindow(
        starts_at=window.starts_at,
        ends_at=window.ends_at,
        provenance=observed.provenance,
        cohort=observed.cohort,
        throughput=window_throughput(observed),
        metrics=() if suppressed is not None else window_metrics(configuration, observed),
        detail=suppressed,
    )


def observe_window(
    configuration: Configuration,
    request: SeriesRequest,
    repository: str,
    window: ReportingWindow,
) -> TrendWindow:
    """Report one window from the cache, collecting what it lacks unless the run is offline.

    No new cache semantics: `--offline` refuses any window the cache does not fully cover, including
    one overlapping the mutable edge, because that refusal is `cached_repository_evidence`'s and is
    reused rather than reimplemented.
    """
    if request.client is None:
        return window_facts(configuration, window, cached_repository_evidence(configuration, repository, window))
    return window_facts(
        configuration,
        window,
        collected_repository_evidence(configuration, request.client, repository, window, request.reference),
    )


@dataclass(frozen=True)
class Comparison:
    """Describe how one measure's two window values are compared, per the settled semantics table.

    Bundled rather than passed one argument at a time so that the shape of a measure is stated where
    the measure is, and so `delta()` keeps one signature for all three shapes.
    """

    basis: DeltaBasis
    unit: str | None = None
    percentile: Percentile | None = None


COUNT = Comparison(DeltaBasis.PERCENTAGE_CHANGE)
"""How a cohort count is compared: relative change, with both absolute counts always reported."""

RATE = Comparison(DeltaBasis.PERCENTAGE_POINTS, "percent")
"""How a rate is compared: in percentage points, never as a relative percentage of a percentage."""


def metric_comparison(item: TrendMetric) -> Comparison:
    """Describe how one behaviour metric moves, from the shape of the observation itself."""
    if isinstance(item.summary, RateObservation):
        return RATE
    return Comparison(DeltaBasis.PERCENTAGE_CHANGE, item.summary.unit, item.percentile)


def delta(measure: str, comparison: Comparison, baseline: float, period: float) -> TrendDelta:
    """Report how one measure moved between the baseline and one period.

    Derived arithmetic only: nothing here consults a threshold, and no result is graded or coloured.
    Rates are subtracted after both are rounded to the tenth, so the reported movement is exactly
    the difference between the two reported values rather than a third number rounded separately.
    """
    if comparison.basis is DeltaBasis.PERCENTAGE_POINTS:
        change, detail = round(period - baseline, 1), None
    elif baseline == 0:
        change, detail = None, BASELINE_ZERO
    else:
        change, detail = round((period - baseline) / baseline * 100, 1), None
    return TrendDelta(
        measure=measure,
        basis=comparison.basis,
        baseline=baseline,
        period=period,
        change=change,
        unit=comparison.unit,
        percentile=comparison.percentile,
        detail=detail,
    )


def metric_delta(baseline: TrendMetric | None, period: TrendMetric) -> TrendDelta | None:
    """Compare one metric between the two windows, or return None where either observed no value."""
    if baseline is None or baseline.value is None or period.value is None:
        return None
    return delta(period.metric, metric_comparison(period), baseline.value, period.value)


def metric_deltas(baseline: tuple[TrendMetric, ...], period: tuple[TrendMetric, ...]) -> tuple[TrendDelta, ...]:
    """Compare every metric BOTH windows observed, and no others."""
    observed = {item.metric: item for item in baseline}
    return tuple(computed for item in period if (computed := metric_delta(observed.get(item.metric), item)) is not None)


def throughput_deltas(baseline: TrendThroughput, period: TrendThroughput) -> tuple[TrendDelta, ...]:
    """Compare what the two windows put onto the default branch, by both routes and in total.

    The measure names come from the model rather than being written out here, so the deltas and the
    rendered count rows cannot come to name the same count differently.
    """
    counts = dict(baseline.measures())
    return tuple(delta(measure, COUNT, counts[measure], value) for measure, value in period.measures())


def period_deltas(baseline: TrendWindow, period: TrendWindow) -> tuple[TrendDelta, ...]:
    """Compare one period against the baseline, or compare nothing where that would invent evidence.

    A baseline carrying a reason of its own — nothing observed, or a cohort below the minimum — is
    not comparable, and suppresses every delta of every period per architecture.md; the repository
    reports why once. A period that observed nothing has nothing to compare. A period whose values
    are suppressed still compares its COUNTS, which are honest: the counts are how a reader sees
    that the period was thin.
    """
    if baseline.throughput is None or period.throughput is None or baseline.detail is not None:
        return ()
    return throughput_deltas(baseline.throughput, period.throughput) + metric_deltas(baseline.metrics, period.metrics)


def trend_period(index: int, observed: TrendWindow, baseline: TrendWindow) -> TrendPeriod:
    """Number one observed window as a whole period, measured against the baseline it follows."""
    return TrendPeriod(
        index=index,
        starts_at=observed.starts_at,
        ends_at=observed.ends_at,
        provenance=observed.provenance,
        cohort=observed.cohort,
        throughput=observed.throughput,
        metrics=observed.metrics,
        deltas=period_deltas(baseline, observed),
        detail=observed.detail,
    )


def series_range(baseline: ReportingWindow, windows: tuple[ReportingWindow, ...]) -> ReportingWindow:
    """Return the whole span one series reports: its baseline through its last whole period.

    The range stops where the series stops rather than at the reference instant, so an observation
    is reported only beside windows the series actually reports. The trailing PARTIAL period is
    excluded from the series, and an observation inside it is excluded for the same reason: it
    belongs to a stretch of time this report does not cover.
    """
    return ReportingWindow(starts_at=baseline.starts_at, ends_at=windows[-1].ends_at)


def observed_alerts(
    configuration: Configuration,
    repository: str,
    covered: ReportingWindow,
) -> tuple[AlertObservation, ...]:
    """Load one repository's open-alert observations made across the span its series covers.

    Read from the observation history rather than fetched, whether or not the run is offline: this
    is the one signal here that cannot be collected on demand at all, since GitHub only ever answers
    for now. What a series shows is therefore exactly what past runs of `collect` recorded — a gap
    in the collection cadence is a gap in this series for ever, and it is left visibly empty rather
    than filled in.
    """
    return load_alert_observations(
        observation_database(configuration.database),
        configuration.organization,
        repository,
        covered,
    )


def repository_trend(
    configuration: Configuration,
    request: SeriesRequest,
    repository: str,
    enablement: datetime | None,
) -> RepositoryTrend:
    """Build one repository's series, or report why it has none to build."""
    if enablement is None:
        return RepositoryTrend(repository=repository, detail=NO_ENABLEMENT_DATE)
    windows = period_windows(enablement, request.span, request.periods, request.reference)
    if not windows:
        return RepositoryTrend(repository=repository, enablement_at=enablement, detail=NO_WHOLE_PERIOD)
    baseline = baseline_window(enablement, request.span)
    observed = observe_window(configuration, request, repository, baseline)
    return RepositoryTrend(
        repository=repository,
        enablement_at=enablement,
        baseline=observed,
        periods=tuple(
            trend_period(index, observe_window(configuration, request, repository, window), observed)
            for index, window in enumerate(windows, start=1)
        ),
        alert_observations=observed_alerts(configuration, repository, series_range(baseline, windows)),
        delta_detail=None if observed.detail is None else no_baseline_delta(observed.detail),
    )


def trend_report(configuration: Configuration, request: SeriesRequest) -> TrendReport:
    """Build every configured repository's series, in the reporting order every command uses.

    Every configured repository appears, including one with no enablement date and one whose windows
    the cache could not answer for: a series is reported per repository and nothing is averaged,
    summed or otherwise combined across them.
    """
    return TrendReport(
        organization=configuration.organization,
        period_days=request.period_days,
        repositories=tuple(
            repository_trend(configuration, request, repository, enablement)
            for repository, enablement in enablement_instants(configuration).items()
        ),
    )


def resolved_windows(item: RepositoryTrend) -> tuple[TrendWindow, ...]:
    """Return every window one repository resolved: its baseline beside each of its periods."""
    return ((item.baseline,) if item.baseline is not None else ()) + item.periods


def series_status(report: TrendReport) -> CollectionStatus:
    """Describe how much of what a run resolved it was able to observe.

    Completeness is measured over the windows the run RESOLVED, not over the configured repositories:
    a repository with no enablement date, and one enabled so recently that no whole period has
    elapsed, resolve no window and are neither complete nor missing. Counting them as incomplete
    would leave `trend` reporting a partial run permanently while most of an organisation is not yet
    enabled, and a status that is always 3 tells a caller nothing.

    A suppressed metric value does not make a window incomplete either: the window WAS observed and
    its counts are reported. Only a window nothing could be read for is missing evidence. This
    counts windows and says nothing about the values inside them — nothing is combined across
    repositories here or anywhere else in this report.
    """
    windows = tuple(window for item in report.repositories for window in resolved_windows(item))
    if not any(window.cohort is not None for window in windows):
        return CollectionStatus.FAILED
    if any(window.cohort is None for window in windows):
        return CollectionStatus.PARTIAL
    return CollectionStatus.COMPLETE
