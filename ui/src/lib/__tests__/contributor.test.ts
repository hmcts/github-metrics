/**
 * The arithmetic behind the contributors table's four columns.
 *
 * Every case is about the same risk: a subtraction that reads a person's window wrongly puts a
 * count of their work beside their login, and the cases asserting a DASH matter most. A row whose
 * review coverage was not applicable, and one carrying no summary for the metric at all, had nothing
 * measured — and a zero there would report an unmeasured window as a clean one.
 */

import { describe, expect, it } from 'vitest';
import { contributorFigures, summaryFor } from '@/lib/contributor';
import type { BehaviourMetricSummary, ContributorRow } from '@/lib/types';

/** One review-coverage summary, as `GovernanceRate` counts it over one person's changes. */
function coverage(
  numerator: number,
  denominator: number,
  classifications: Record<string, number> = {},
): BehaviourMetricSummary {
  return {
    metric: 'independent-review-coverage',
    summary: { status: 'observed', numerator, denominator },
    classifications,
  };
}

/** One size distribution, or an unmeasurable one where the sample placed no median. */
function size(median?: number): BehaviourMetricSummary {
  return {
    metric: 'pull-request-size',
    summary:
      median === undefined
        ? { status: 'not_applicable', sample_size: 0, unit: 'lines' }
        : { status: 'observed', sample_size: 4, unit: 'lines', median, percentile_75: median * 2 },
    classifications: { included: 4 },
  };
}

function row(metrics: BehaviourMetricSummary[], contributions = 4): ContributorRow {
  return { login: 'alice', contributions, blocking: 0, metrics };
}

describe('contributorFigures', () => {
  it('reads a person who only opened pull requests, all of them reviewed', () => {
    const figures = contributorFigures(row([coverage(6, 6, { included: 6 }), size(120)]));

    expect(figures.merged).toBe(6);
    expect(figures.directPushes).toBe(0);
    expect(figures.unreviewed).toBe(0);
    expect(figures.size).toBe('120 lines');
  });

  it('reads a person who only pushed to the branch, whose pushes are never counted as merges', () => {
    const figures = contributorFigures(row([coverage(0, 3, { 'direct-commit': 3 })]));

    expect(figures.merged).toBe(0);
    expect(figures.directPushes).toBe(3);
    // The three pushes are reported once, in their own column: they had no pull request to review,
    // so counting them here would report the same bypass twice.
    expect(figures.unreviewed).toBe(0);
    // No size distribution in the row at all, which is a dash rather than a zero.
    expect(figures.size).toBe('-');
  });

  it('separates the two routes for a mixed window, and the unreviewed merges from both', () => {
    const figures = contributorFigures(
      row([coverage(5, 9, { included: 5, 'no-review-events': 2, 'direct-commit': 2 }), size(48.5)]),
    );

    expect(figures.merged).toBe(7);
    expect(figures.directPushes).toBe(2);
    expect(figures.unreviewed).toBe(2);
    expect(figures.size).toBe('48.5 lines');
  });

  it('leaves an unmeasurable size distribution as a dash rather than a zero', () => {
    expect(contributorFigures(row([coverage(1, 1, { included: 1 }), size()])).size).toBe('-');
  });

  it('leaves a distribution whose median the sample never placed as a dash', () => {
    const unplaced: BehaviourMetricSummary = {
      metric: 'pull-request-size',
      summary: { status: 'observed', sample_size: 2, unit: 'lines' },
      classifications: {},
    };

    expect(contributorFigures(row([unplaced])).size).toBe('-');
  });

  it('reports every count absent where the row carries no review-coverage summary', () => {
    const figures = contributorFigures(row([size(10)]));

    expect(figures.merged).toBeUndefined();
    expect(figures.directPushes).toBeUndefined();
    expect(figures.unreviewed).toBeUndefined();
    expect(figures.size).toBe('10 lines');
  });

  it('reports every count absent where the rate had no eligible change to divide', () => {
    const inapplicable: BehaviourMetricSummary = {
      metric: 'independent-review-coverage',
      summary: { status: 'not_applicable', numerator: 0, denominator: 0 },
      classifications: {},
    };
    const figures = contributorFigures(row([inapplicable]));

    expect(figures.merged).toBeUndefined();
    expect(figures.unreviewed).toBeUndefined();
  });

  it('reports a count the summary contradicts as absent rather than as a negative', () => {
    // Unreachable from a consistent block: more direct commits than changes in the denominator.
    const figures = contributorFigures(row([coverage(0, 1, { 'direct-commit': 4 })]));

    expect(figures.merged).toBeUndefined();
    expect(figures.unreviewed).toBeUndefined();
    expect(figures.directPushes).toBe(4);
  });

  it('reads a row with no summaries at all as four absences', () => {
    const figures = contributorFigures(row([]));

    expect(figures).toEqual({ size: '-' });
  });
});

describe('summaryFor', () => {
  it('finds one metric among a row’s summaries and nothing for one it does not carry', () => {
    const summaries = [coverage(1, 2), size(30)];

    expect(summaryFor(row(summaries), 'pull-request-size')?.metric).toBe('pull-request-size');
    expect(summaryFor(row(summaries), 'review-depth')).toBeUndefined();
  });
});
