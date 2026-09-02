import { describe, expect, it } from 'vitest';
import { metricDetail, metricTone, metricValue, percentile } from '@/lib/metrics';
import type {
  BehaviourMetricSummary,
  Observation,
  ReadinessAssessment,
  ReadinessCondition,
} from '@/lib/types';

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

describe('metricTone', () => {
  function assessment(
    blocking: ReadinessCondition[],
    caution: ReadinessCondition[],
    clear: ReadinessCondition[],
  ): ReadinessAssessment {
    return { label: 'amber', blocking, caution, clear };
  }

  const GRADED = assessment(
    [
      { condition: 'independent-review-coverage-below-target', label: 'red', detail: '41.2%, below the 95% target' },
      { condition: 'merge-cycle-time-above-target', label: 'amber', detail: 'p75 is 96 hours, above the 48 hour target' },
    ],
    [
      { condition: 'review-depth-below-target', detail: '12%, below the 30% boundary' },
      { condition: 'time-to-first-review-not-observed', detail: 'no observations in this window' },
    ],
    [
      { condition: 'checks-passing-at-merge-at-target', detail: '92.3%, at or above the 90% target' },
      { condition: 'sufficient-merges', detail: '96 merges, at or above the minimum of 5', informational: true },
    ],
  );

  it('reads a metric the policy cleared as reading well', () => {
    expect(metricTone('checks-passing-at-merge', GRADED)).toBe('good');
  });

  it('takes the ceiling a blocking condition imposed, red and amber alike', () => {
    // The card can never contradict the block above it: red there is red here, and a distribution
    // capped at amber by `assessment.distribution` is amber here rather than the worse colour.
    expect(metricTone('independent-review-coverage', GRADED)).toBe('bad');
    expect(metricTone('merge-cycle-time', GRADED)).toBe('warn');
  });

  it('reads a caution as worth weighing — `review-depth` imposes nothing and never reads badly', () => {
    expect(metricTone('review-depth', GRADED)).toBe('warn');
  });

  it('reads a metric with nothing to grade as the caution the policy raised for it', () => {
    expect(metricTone('time-to-first-review', GRADED)).toBe('warn');
  });

  it('leaves a metric no condition names uncoloured rather than guessing at one', () => {
    expect(metricTone('pull-request-size', GRADED)).toBe('neutral');
  });

  it('colours nothing without an assessment, which is what an actor page renders with', () => {
    // The policy grades repositories and not people, so one person's slice of one repository
    // arrives with no assessment at all — and must render rather than throw.
    expect(metricTone('independent-review-coverage', undefined)).toBe('neutral');
  });

  it('grades nothing off a condition whose ceiling could not be read', () => {
    const unreadable = assessment(
      [{ condition: 'approval-coverage-below-target', label: 'cannot_assess', detail: 'half the question' }],
      [],
      [],
    );
    expect(metricTone('approval-coverage', unreadable)).toBe('neutral');
  });

  it('carries an informational condition without colour, as the assessment block does', () => {
    const reported = assessment(
      [],
      [],
      [{ condition: 'approval-coverage-at-target', detail: 'reported, not judged', informational: true }],
    );
    expect(metricTone('approval-coverage', reported)).toBe('neutral');
  });

  it('matches the whole metric name, not a metric whose name it starts with', () => {
    // `approval-coverage` is not `independent-review-coverage`, and a suffix match on the wrong
    // side of the hyphen would hand one metric the other's grade.
    expect(metricTone('coverage', GRADED)).toBe('neutral');
    expect(metricTone('independent-review-coverage-below', GRADED)).toBe('neutral');
  });
});
