/**
 * The arithmetic behind the repository trend charts.
 *
 * What these pin is the difference between a gap and a zero, which a chart cannot show on its own: a
 * period the caches could not answer for must contribute `null`, and a period whose values were
 * suppressed must keep its counts while contributing no metric value.
 */

import { describe, expect, it } from 'vitest';
import {
  BASIS_LABEL,
  NO_BASIS,
  axisUnit,
  cutDetail,
  deltaBasis,
  hasPeriods,
  metricCharts,
  metricLabel,
  metricUnit,
  namedWindows,
  periodSpan,
  seriesMeasures,
  throughputRows,
  windowReasons,
} from '@/lib/trend';
import type { RepositoryTrend, TrendMetric, TrendPeriod, TrendWindow } from '@/lib/types';

const RATE: TrendMetric = {
  metric: 'approval-coverage',
  summary: { status: 'observed', numerator: 8, denominator: 10 },
  value: 80,
};

const DISTRIBUTION: TrendMetric = {
  metric: 'merge-cycle-time',
  summary: {
    status: 'observed',
    sample_size: 10,
    unit: 'hours',
    median: 6,
    percentile_75: 12,
    percentile_90: 20,
  },
  value: 6,
  percentile: 'median',
};

function observed(overrides: Partial<TrendWindow> = {}): TrendWindow {
  return {
    starts_at: '2026-06-01T00:00:00Z',
    ends_at: '2026-06-29T00:00:00Z',
    provenance: { offline: true, intervals_fetched: 0 },
    cohort: { merged: 10, reported: 10, excluded_authors: {}, direct_commits: 2 },
    throughput: { merges: 12, merged_pull_requests: 10, direct_commits: 2, active_contributors: 3 },
    metrics: [RATE, DISTRIBUTION],
    ...overrides,
  };
}

function period(index: number, overrides: Partial<TrendPeriod> = {}): TrendPeriod {
  const starts = new Date(Date.UTC(2026, 5, 29) + (index - 1) * 28 * 86_400_000);
  return {
    ...observed({
      starts_at: starts.toISOString(),
      ends_at: new Date(starts.getTime() + 28 * 86_400_000).toISOString(),
    }),
    index,
    deltas: [
      {
        measure: 'merged-pull-requests',
        basis: 'percentage_change',
        baseline: 10,
        period: 12,
        change: 20,
      },
      { measure: 'approval-coverage', basis: 'percentage_points', baseline: 75, period: 80, change: 5 },
    ],
    ...overrides,
  };
}

function series(overrides: Partial<RepositoryTrend> = {}): RepositoryTrend {
  return {
    repository: 'cath-service',
    enablement_at: '2026-06-01T00:00:00Z',
    baseline: observed(),
    periods: [period(1), period(2)],
    alert_observations: [],
    ...overrides,
  };
}

describe('namedWindows', () => {
  it('leads with the baseline, so the periods have something to have moved from', () => {
    expect(namedWindows(series()).map((named) => named.name)).toEqual(['Baseline', 'P1', 'P2']);
  });

  it('numbers periods as the contract numbers them, not by their place in the list', () => {
    const truncated = series({ baseline: undefined, periods: [period(4), period(5)] });
    expect(namedWindows(truncated).map((named) => named.name)).toEqual(['P4', 'P5']);
  });
});

describe('hasPeriods', () => {
  it('is false for a repository whose first whole period has not elapsed', () => {
    expect(hasPeriods(series({ periods: [], baseline: undefined }))).toBe(false);
    expect(hasPeriods(series())).toBe(true);
  });

  it('is false for a series the endpoint refused, so the page drops the section and nothing else', () => {
    expect(hasPeriods(null)).toBe(false);
  });
});

describe('cutDetail', () => {
  it('says a series holding the whole cut is the first periods and not every one of them', () => {
    expect(cutDetail(series(), 2)).toBe('— the first 2, the most one request may ask for');
  });

  it('says nothing about a cut the series did not reach', () => {
    expect(cutDetail(series(), 26)).toBeUndefined();
  });
});

describe('periodSpan', () => {
  it('reads the span off the periods themselves rather than off the request', () => {
    expect(periodSpan(series())).toBe(28);
  });

  it('has no span to state where there is no period', () => {
    expect(periodSpan(series({ periods: [], baseline: undefined }))).toBeNull();
  });

  it('states no span for a period whose dates cannot be read', () => {
    expect(periodSpan(series({ periods: [period(1, { ends_at: 'the day after' })] }))).toBeNull();
  });
});

describe('throughputRows', () => {
  it('plots both routes onto the default branch, per window', () => {
    expect(throughputRows(series())).toEqual([
      { starts_at: '2026-06-01T00:00:00Z', merged_pull_requests: 10, direct_commits: 2 },
      { starts_at: period(1).starts_at, merged_pull_requests: 10, direct_commits: 2 },
      { starts_at: period(2).starts_at, merged_pull_requests: 10, direct_commits: 2 },
    ]);
  });

  it('leaves a window that observed nothing as a gap rather than as an empty period', () => {
    const missing: TrendWindow = {
      starts_at: '2026-06-01T00:00:00Z',
      ends_at: '2026-06-29T00:00:00Z',
      metrics: [],
      detail: 'cached pull_request evidence does not cover this window',
    };
    const rows = throughputRows(series({ baseline: missing }));
    expect(rows[0]).toEqual({
      starts_at: '2026-06-01T00:00:00Z',
      merged_pull_requests: null,
      direct_commits: null,
    });
  });
});

describe('measures', () => {
  it('names a distribution with the percentile it was compared at', () => {
    expect(metricLabel(DISTRIBUTION)).toBe('merge-cycle-time (median)');
    expect(metricLabel(RATE)).toBe('approval-coverage');
  });

  it('measures a rate in per cent and a distribution in its own unit', () => {
    expect(metricUnit(RATE)).toBe('percent');
    expect(metricUnit(DISTRIBUTION)).toBe('hours');
  });

  it('suffixes the axis only where the unit has a short form', () => {
    expect(axisUnit('percent')).toBe('%');
    expect(axisUnit('hours')).toBeUndefined();
  });

  it('collects the measures across every window, not from whichever one comes first', () => {
    const suppressed = series({
      baseline: observed({ metrics: [], detail: '3 merges, below the minimum of 10' }),
    });
    expect(seriesMeasures(suppressed).map((metric) => metric.metric)).toEqual([
      'approval-coverage',
      'merge-cycle-time',
    ]);
  });
});

describe('deltaBasis', () => {
  it('states the arithmetic the contract compared the measure with', () => {
    expect(deltaBasis(series(), 'approval-coverage')).toBe(BASIS_LABEL.percentage_points);
    expect(deltaBasis(series(), 'merged-pull-requests')).toBe(BASIS_LABEL.percentage_change);
  });

  it('says so rather than borrowing a basis where no period compared the measure', () => {
    expect(deltaBasis(series(), 'merge-cycle-time')).toBe(NO_BASIS);
    expect(deltaBasis(series({ periods: [period(1, { deltas: [] })] }), 'approval-coverage')).toBe(NO_BASIS);
  });
});

describe('metricCharts', () => {
  const charts = metricCharts(
    series({ periods: [period(1, { metrics: [RATE] }), period(2)] }),
  );

  it('draws one chart per measure the series observed, in the order it observed them', () => {
    expect(charts.map((chart) => chart.metric)).toEqual(['approval-coverage', 'merge-cycle-time']);
  });

  it('carries the unit, the axis suffix and the delta basis with the rows', () => {
    expect(charts[0]).toMatchObject({
      label: 'approval-coverage',
      unit: 'percent',
      axis: '%',
      basis: BASIS_LABEL.percentage_points,
    });
    expect(charts[1]).toMatchObject({ label: 'merge-cycle-time (median)', unit: 'hours', basis: NO_BASIS });
  });

  it('leaves a window with no value for a measure as a gap in that line alone', () => {
    expect(charts[1]?.rows.map((row) => row.value)).toEqual([6, null, 6]);
    expect(charts[0]?.rows.map((row) => row.value)).toEqual([80, 80, 80]);
  });
});

describe('windowReasons', () => {
  it('names the window each reason belongs to, under the charts rather than on them', () => {
    const suppressed = series({
      baseline: observed({ metrics: [], detail: '3 merges, below the minimum of 10' }),
    });
    expect(windowReasons(suppressed)).toEqual([
      'Baseline (2026-06-01): 3 merges, below the minimum of 10',
    ]);
  });

  it('has nothing to explain where every window observed its cohort', () => {
    expect(windowReasons(series())).toEqual([]);
  });
});
