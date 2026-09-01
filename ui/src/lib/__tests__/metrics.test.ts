import { describe, expect, it } from 'vitest';
import { metricDetail, metricValue, percentile } from '@/lib/metrics';
import type { BehaviourMetricSummary, Observation } from '@/lib/types';

function summary(observation: Observation): BehaviourMetricSummary {
  return { metric: 'review-depth', summary: observation, classifications: {} };
}

const RATE: Observation = { status: 'observed', numerator: 78, denominator: 96 };

const DISTRIBUTION: Observation = {
  status: 'observed',
  sample_size: 6,
  unit: 'hours',
  median: 4.5,
  percentile_75: 9,
  percentile_90: 21.25,
};

describe('metricValue', () => {
  it('leads a rate on its percentage, rounded as the text report rounds it', () => {
    // 78 of 96 is exactly 81.25, the tie Python's half-to-even `round` takes down to 81.2. Half-up
    // would print 81.3 here and 81.2 in the assessment sentence beside it, for one observation.
    expect(metricValue(summary(RATE))).toBe('81.2%');
  });

  it('leads a distribution on its median, with the unit that gives it meaning', () => {
    expect(metricValue(summary(DISTRIBUTION))).toBe('4.5 hours');
  });

  it('says in words that a metric had no eligible sample, rather than showing a zero', () => {
    expect(metricValue(summary({ ...RATE, status: 'not_applicable' }))).toBe('not applicable');
  });
});

describe('metricDetail', () => {
  it('states a rate’s population, so a percentage is never read without it', () => {
    expect(metricDetail(summary(RATE))).toBe('78 of 96');
  });

  it('states a distribution’s sample size beside its p75', () => {
    expect(metricDetail(summary(DISTRIBUTION))).toBe('6 samples · p75 9 hours');
  });

  it('states the sample alone when the p75 could not be placed', () => {
    // Absent, not null: the service omits an unplaced percentile rather than sending one.
    expect(metricDetail(summary({ ...DISTRIBUTION, percentile_75: undefined }))).toBe('6 samples');
  });

  it('states the sample alone for a distribution nothing was observed for', () => {
    expect(metricDetail(summary({ ...DISTRIBUTION, status: 'not_applicable' }))).toBe('6 samples');
  });
});

describe('percentile', () => {
  it('has nothing to add to a rate, which has no tail', () => {
    expect(percentile(RATE)).toBeNull();
  });

  it('formats the p75 as few digits as state it exactly', () => {
    expect(percentile({ ...DISTRIBUTION, percentile_75: 21.250000000000004 })).toBe('p75 21.25 hours');
  });
});
