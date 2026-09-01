/**
 * The rows and labels one repository's period series is charted from.
 *
 * MEASUREMENT ONLY, and for ONE repository. Nothing here is graded: no value carries a colour, no
 * movement is called an improvement, and no two repositories' periods are ever put in the same
 * series — an average of deltas is the team roll-up the scope boundaries exclude, wearing a
 * different hat. The chart colours are deliberately not the RAG palette for the same reason: a green
 * line would grade a series the report leaves ungraded.
 *
 * The rules the shapes here inherit from `metrics.render`, which prints the same series as text:
 *
 *   • A window that observed nothing is a GAP, never a zero. The two are the same shape on a chart
 *     and mean opposite things, so an unobserved period contributes `null` and the line skips it.
 *   • The measures are taken across every window rather than from one, because a period whose values
 *     were suppressed reports no metric at all and would otherwise decide the rows for the series.
 *   • A delta's basis is stated beside its chart rather than left implicit: percentage points and
 *     percentage change both print as bare numbers and are different quantities.
 */

import { day } from '@/lib/format';
import type { DeltaBasis, Percentile, RepositoryTrend, TrendMetric, TrendWindow } from '@/lib/types';

/** The series colours: indigo for the pull-request route, sky for what bypassed it. */
export const ROUTE_HEX = { pullRequests: '#818cf8', directCommits: '#38bdf8' } as const;

/** One period's two merge routes, keyed as the throughput chart reads them. */
export type ThroughputRow = {
  starts_at: string;
  merged_pull_requests: number | null;
  direct_commits: number | null;
};

/** One period's value for one measure. */
export type MetricRow = {
  starts_at: string;
  value: number | null;
};

/** One metric's own chart: what it is, what it is measured in, and how its deltas are stated. */
export type MetricChart = {
  metric: string;
  /** The metric named as the report names it, with the percentile where one was compared. */
  label: string;
  /** What the values are measured in — `percent`, or the distribution's own unit. */
  unit: string;
  /** Suffix for the value axis, where the unit has a short one. */
  axis?: string;
  basis: string;
  rows: MetricRow[];
};

export const PERCENTILE_LABEL: Record<Percentile, string> = {
  median: 'median',
  percentile_75: '75th percentile',
};

export const BASIS_LABEL: Record<DeltaBasis, string> = {
  percentage_points: 'compared in percentage points',
  percentage_change: 'compared in percentage change',
};

export const NO_BASIS = 'no period could be compared with the baseline';

/** One window of a series beside the name its column and its reason are reported under. */
export type NamedWindow = {
  name: string;
  window: TrendWindow;
};

/**
 * Every window the series resolved, named: its baseline, then each whole period.
 *
 * The baseline leads the charts because it is the span BEFORE enablement and is what the periods are
 * measured against — a series drawn without it shows movement with nothing to have moved from. The
 * periods are numbered from the enablement instant by the contract, not by their place in this list,
 * so a truncated series still names its periods the way the JSON does.
 */
export function namedWindows(series: RepositoryTrend): NamedWindow[] {
  return [
    ...(series.baseline === undefined ? [] : [{ name: 'Baseline', window: series.baseline }]),
    ...series.periods.map((period) => ({ name: `P${period.index}`, window: period })),
  ];
}

export function resolvedWindows(series: RepositoryTrend): TrendWindow[] {
  return namedWindows(series).map((named) => named.window);
}

/**
 * Whether there is a series to draw at all: a repository with no whole period has none.
 *
 * `null` is accepted and answers false, so a page whose trend fetch was refused takes the same
 * branch as one whose series is empty — in both cases there is nothing to draw, and the rest of the
 * page is unaffected either way.
 */
export function hasPeriods(series: RepositoryTrend | null): series is RepositoryTrend {
  return series !== null && series.periods.length > 0;
}

/**
 * Say so when the series sits at the cut, because then it is not the whole history.
 *
 * A request must name a cut — the service refuses an unbounded series rather than truncating one —
 * and the periods a cut keeps are the ones nearest enablement. So a series holding exactly the
 * requested count is a series whose later periods may exist and are not drawn, and a section that
 * printed only "26 whole periods since <enablement>" would read as everything since that date.
 */
export function cutDetail(series: RepositoryTrend, cut: number): string | undefined {
  return series.periods.length < cut
    ? undefined
    : `— the first ${cut}, the most one request may ask for`;
}

/**
 * How long each period runs, read off the periods themselves rather than off the request.
 *
 * The measured span is the honest one: it is what the windows in the response actually cover, and a
 * span echoed back from the query would still read as 28 days if the service had cut something else.
 */
export function periodSpan(series: RepositoryTrend): number | null {
  const period = series.periods[0];
  if (period === undefined) {
    return null;
  }
  const days = (Date.parse(period.ends_at) - Date.parse(period.starts_at)) / 86_400_000;
  return Number.isNaN(days) ? null : Math.round(days);
}

/** What the throughput chart plots: the two routes onto the default branch, per window. */
export function throughputRows(series: RepositoryTrend): ThroughputRow[] {
  return resolvedWindows(series).map((window) => ({
    starts_at: window.starts_at,
    merged_pull_requests: window.throughput?.merged_pull_requests ?? null,
    direct_commits: window.throughput?.direct_commits ?? null,
  }));
}

/** Name one metric as the report names it, saying which percentile a distribution was read at. */
export function metricLabel(metric: TrendMetric): string {
  return metric.percentile === undefined
    ? metric.metric
    : `${metric.metric} (${PERCENTILE_LABEL[metric.percentile]})`;
}

/** A rate is measured in per cent; a distribution in the unit its observation carries. */
export function metricUnit(metric: TrendMetric): string {
  return 'unit' in metric.summary ? metric.summary.unit : 'percent';
}

/** The axis suffix for a percentage, and none for a unit no two-character suffix would say. */
export function axisUnit(unit: string): string | undefined {
  return unit === 'percent' ? '%' : undefined;
}

/**
 * The first observation of each measure the series reports, in the order they were first observed.
 *
 * Across every window, not from one: a window whose values were suppressed carries no metric, and
 * reading the measures off it alone would leave a series with charts for nothing.
 */
export function seriesMeasures(series: RepositoryTrend): TrendMetric[] {
  const observed = new Map<string, TrendMetric>();
  resolvedWindows(series).forEach((window) => {
    window.metrics.forEach((metric) => {
      if (!observed.has(metric.metric)) {
        observed.set(metric.metric, metric);
      }
    });
  });
  return [...observed.values()];
}

/**
 * How one measure's deltas are stated, taken from the first period that computed one.
 *
 * A measure no period compared says so rather than borrowing another measure's basis: a suppressed
 * period and an unobservable baseline both leave a series whose values are still worth reading.
 */
export function deltaBasis(series: RepositoryTrend, measure: string): string {
  const found = series.periods
    .flatMap((period) => period.deltas)
    .find((delta) => delta.measure === measure);
  return found === undefined ? NO_BASIS : BASIS_LABEL[found.basis];
}

/** One chart per measure the series observed, each carrying its own rows, unit and delta basis. */
export function metricCharts(series: RepositoryTrend): MetricChart[] {
  const windows = resolvedWindows(series);
  return seriesMeasures(series).map((metric) => {
    const unit = metricUnit(metric);
    return {
      metric: metric.metric,
      label: metricLabel(metric),
      unit,
      axis: axisUnit(unit),
      basis: deltaBasis(series, metric.metric),
      rows: windows.map((window) => ({
        starts_at: window.starts_at,
        value: window.metrics.find((item) => item.metric === metric.metric)?.value ?? null,
      })),
    };
  });
}

/**
 * Why one window observed nothing, or observed counts without values, one line per window.
 *
 * Under the charts rather than on them, as the text report puts them under its tables: a flat stretch
 * of a line is exactly what a reader needs explained, and a reason drawn inside a chart is a reason
 * nobody reads.
 */
export function windowReasons(series: RepositoryTrend): string[] {
  return namedWindows(series)
    .filter((named) => named.window.detail !== undefined)
    .map((named) => `${named.name} (${day(named.window.starts_at)}): ${named.window.detail}`);
}
