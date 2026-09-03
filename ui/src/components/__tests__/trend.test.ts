/**
 * Markup checks for the repository trend section.
 *
 * The charts themselves render to a sized container the server has no size for, so what is asserted
 * here is the text around them — which is where the rules live: the period count and the span they
 * were cut into, the delta basis beside each metric, and the reason a window observed nothing.
 */

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { TrendSection } from '@/components/TrendSection';
import { BASIS_LABEL, NO_BASIS } from '@/lib/trend';
import type { RepositoryTrend, TrendMetric, TrendPeriod, TrendWindow } from '@/lib/types';

const RATE: TrendMetric = {
  metric: 'approval-coverage',
  summary: { status: 'observed', numerator: 8, denominator: 10 },
  value: 80,
};

function window(starts: string, overrides: Partial<TrendWindow> = {}): TrendWindow {
  return {
    starts_at: starts,
    ends_at: starts,
    provenance: { offline: true, intervals_fetched: 0 },
    cohort: { merged: 10, reported: 10, excluded_authors: {}, direct_commits: 2 },
    throughput: { merges: 12, merged_pull_requests: 10, direct_commits: 2, active_contributors: 3 },
    metrics: [RATE],
    ...overrides,
  };
}

const PERIOD: TrendPeriod = {
  ...window('2026-06-29T00:00:00Z', { ends_at: '2026-07-27T00:00:00Z' }),
  index: 1,
  deltas: [
    { measure: 'approval-coverage', basis: 'percentage_points', baseline: 75, period: 80, change: 5 },
  ],
};

/** The cut the page asks for, well above anything these fixtures hold unless a test says otherwise. */
const CUT = 26;

function markup(series: RepositoryTrend, cut = CUT): string {
  return renderToStaticMarkup(createElement(TrendSection, { series, cut }));
}

const SERIES: RepositoryTrend = {
  repository: 'cath-service',
  enablement_at: '2026-06-01T00:00:00Z',
  baseline: window('2026-06-01T00:00:00Z', { ends_at: '2026-06-29T00:00:00Z' }),
  periods: [PERIOD],
  alert_observations: [],
};

describe('TrendSection', () => {
  const rendered = markup(SERIES);

  it('says how many whole periods were cut, how long they are, and from when', () => {
    expect(rendered).toContain('1 whole period of 28 days since 2026-06-01');
  });

  it('states the arithmetic each metric was compared with, beside that metric', () => {
    expect(rendered).toContain('approval-coverage');
    expect(rendered).toContain(BASIS_LABEL.percentage_points);
  });

  it('grades nothing: no readiness word reaches a series', () => {
    expect(rendered).not.toContain('Ready');
    expect(rendered).not.toContain('Blocked');
  });

  it('explains a window that observed nothing instead of drawing it as a zero', () => {
    const suppressed = markup({
      ...SERIES,
      baseline: {
        starts_at: '2026-06-01T00:00:00Z',
        ends_at: '2026-06-29T00:00:00Z',
        metrics: [],
        detail: 'cached pull_request evidence does not cover this window',
      },
      periods: [{ ...PERIOD, deltas: [] }],
      delta_detail:
        'the baseline window is not comparable, so no delta was computed: cached pull_request evidence does not cover this window',
    });
    expect(suppressed).toContain('Baseline (2026-06-01): cached pull_request evidence does not cover');
    expect(suppressed).toContain('No period was compared:');
    expect(suppressed).toContain(NO_BASIS);
  });

  it('says a series at the cut holds the first periods, not the whole history since enablement', () => {
    expect(markup(SERIES, 1)).toContain('the first 1, the most one request may ask for');
  });

  it('says nothing about a cut for a series that did not reach it', () => {
    expect(rendered).not.toContain('the most one request may ask for');
  });

  it('says why a series holds no metric chart rather than leaving a blank', () => {
    const thin = markup({
      ...SERIES,
      baseline: window('2026-06-01T00:00:00Z', { metrics: [] }),
      periods: [{ ...PERIOD, metrics: [] }],
    });
    expect(thin).toContain('No behaviour metric was observed in any period of this series.');
  });

  /**
   * The heading is built from the parts that ARRIVED, and drops each part that did not.
   *
   * A repository the service has no enablement instant for sends none, and a period whose instants
   * could not be read leaves `periodSpan` with nothing to measure. The heading then has to read as a
   * shorter sentence rather than as "of null days" or "since undefined" — a page that printed either
   * would be stating a fact the series does not carry.
   */
  it('leaves out the period length and the enablement date where the series carries neither', () => {
    const partial = markup({
      ...SERIES,
      enablement_at: undefined,
      periods: [{ ...PERIOD, starts_at: 'not an instant', ends_at: 'not an instant either' }],
    });

    expect(partial).toContain('1 whole period');
    expect(partial).not.toContain('days');
    expect(partial).not.toContain('since');
    expect(partial).not.toMatch(/null|undefined|NaN/);
  });
});
