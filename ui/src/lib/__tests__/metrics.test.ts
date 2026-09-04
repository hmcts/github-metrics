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
  /** One card to take a tone for. The observation only matters to the two completeness rates. */
  function card(metric: string, observation: Observation = RATE): BehaviourMetricSummary {
    return { metric, summary: observation, classifications: {} };
  }

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
      // The rates are the only graded conditions that still block: `assessment.rate` labels a
      // shortfall amber above its amber boundary and red below it.
      { condition: 'approval-coverage-below-target', label: 'amber', detail: '88.4%, below the 95% target' },
    ],
    [
      { condition: 'review-depth-below-target', detail: '12%, below the 30% boundary' },
      // A flow signal, graded against its maximum and reported as a caution since 2026-09-03.
      {
        condition: 'merge-cycle-time-above-target',
        detail: 'merge-cycle-time median is 96 hours, above the 24 hours target',
      },
      { condition: 'time-to-first-review-not-observed', detail: 'no observations in this window' },
    ],
    [
      { condition: 'checks-passing-at-merge-at-target', detail: '92.3%, at or above the 90% target' },
      { condition: 'sufficient-merges', detail: '96 merges, at or above the minimum of 5', informational: true },
    ],
  );

  it('reads a metric the policy cleared as reading well', () => {
    expect(metricTone(card('checks-passing-at-merge'), GRADED)).toBe('good');
  });

  it('takes the ceiling a blocking condition imposed, red and amber alike', () => {
    // The card can never contradict the block above it: red there is red here, and a rate the
    // policy capped at amber is amber here rather than the worse colour.
    expect(metricTone(card('independent-review-coverage'), GRADED)).toBe('bad');
    expect(metricTone(card('approval-coverage'), GRADED)).toBe('warn');
  });

  it('reads a caution as worth weighing — `review-depth` imposes nothing and never reads badly', () => {
    expect(metricTone(card('review-depth'), GRADED)).toBe('warn');
  });

  it('keeps a flow signal above its target amber, now that it releases the label', () => {
    // `merge-cycle-time-above-target` is a caution rather than a block: the card still says the
    // median cost more than the target allows, and the label above it is decided without the cost.
    expect(metricTone(card('merge-cycle-time'), GRADED)).toBe('warn');
  });

  it('reads a metric with nothing to grade as the caution the policy raised for it', () => {
    expect(metricTone(card('time-to-first-review'), GRADED)).toBe('warn');
  });

  it('leaves a metric no condition names uncoloured rather than guessing at one', () => {
    expect(metricTone(card('pull-request-size'), GRADED)).toBe('neutral');
  });

  it('colours nothing without an assessment, which is what an actor page renders with', () => {
    // The policy grades repositories and not people, so one person's slice of one repository
    // arrives with no assessment at all — and must render rather than throw.
    expect(metricTone(card('independent-review-coverage'), undefined)).toBe('neutral');
  });

  it('grades nothing off a condition whose ceiling could not be read', () => {
    const unreadable = assessment(
      [{ condition: 'approval-coverage-below-target', label: 'cannot_assess', detail: 'half the question' }],
      [],
      [],
    );
    expect(metricTone(card('approval-coverage'), unreadable)).toBe('neutral');
  });

  it('carries an informational condition without colour, as the assessment block does', () => {
    const reported = assessment(
      [],
      [],
      [{ condition: 'approval-coverage-at-target', detail: 'reported, not judged', informational: true }],
    );
    expect(metricTone(card('approval-coverage'), reported)).toBe('neutral');
  });

  it('matches the whole metric name, not a metric whose name it starts with', () => {
    // `approval-coverage` is not `independent-review-coverage`, and a suffix match on the wrong
    // side of the hyphen would hand one metric the other's grade.
    expect(metricTone(card('coverage'), GRADED)).toBe('neutral');
    expect(metricTone(card('independent-review-coverage-below'), GRADED)).toBe('neutral');
  });

  const COMPLETENESS = ['description-quality', 'traceability-reference'] as const;

  it.each(COMPLETENESS)('reads %s green when every eligible merge is counted', (metric) => {
    const whole: Observation = { status: 'observed', numerator: 96, denominator: 96 };
    expect(metricTone(card(metric, whole), GRADED)).toBe('good');
  });

  it.each(COMPLETENESS)('leaves %s uncoloured one merge short of complete', (metric) => {
    const short: Observation = { status: 'observed', numerator: 95, denominator: 96 };
    expect(metricTone(card(metric, short), GRADED)).toBe('neutral');
  });

  it.each(COMPLETENESS)('leaves %s uncoloured at a rate that only rounds to 100%%', (metric) => {
    // 2499 of 2500 is 99.96%, which `rate` prints as `100%` at one decimal place. Colouring the
    // card off that string would say the one unreferenced merge does not exist.
    const nearly: Observation = { status: 'observed', numerator: 2499, denominator: 2500 };
    expect(metricValue(card(metric, nearly))).toBe('100%');
    expect(metricTone(card(metric, nearly), GRADED)).toBe('neutral');
  });

  it.each(COMPLETENESS)('leaves %s uncoloured where there was nothing to count', (metric) => {
    const none: Observation = { status: 'not_applicable', numerator: 0, denominator: 0 };
    expect(metricTone(card(metric, none), GRADED)).toBe('neutral');
    // And an empty denominator the service did send as observed: nothing measured is not
    // everything measured, however the two zeroes compare.
    const empty: Observation = { status: 'observed', numerator: 0, denominator: 0 };
    expect(metricTone(card(metric, empty), GRADED)).toBe('neutral');
  });

  it.each(COMPLETENESS)('reads %s green on a contributor page, where no assessment arrives', (metric) => {
    // The rule asks the assessment nothing, which is what lets it be the one card with colour on a
    // grid the policy graded no part of.
    const whole: Observation = { status: 'observed', numerator: 7, denominator: 7 };
    expect(metricTone(card(metric, whole), undefined)).toBe('good');
    expect(metricTone(card(metric, { ...whole, numerator: 6 }), undefined)).toBe('neutral');
  });

  it.each(COMPLETENESS)('leaves %s uncoloured for a distribution, which has no denominator', (metric) => {
    // Neither is a distribution today; if one were reshaped into one, the rule has no completeness
    // to read and must stay silent rather than colouring a median green.
    expect(metricTone(card(metric, DISTRIBUTION), GRADED)).toBe('neutral');
  });
});
