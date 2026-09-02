import { describe, expect, it } from 'vitest';
import {
  ABSENT,
  NOT_APPLICABLE,
  basis,
  count,
  day,
  figure,
  instant,
  isRate,
  median,
  percent,
  percentageOf,
  population,
  quantity,
  rate,
  sample,
  span,
  summarise,
} from '@/lib/format';
import type { DistributionObservation, RateObservation } from '@/lib/types';

function observedRate(numerator: number, denominator: number): RateObservation {
  return { status: 'observed', numerator, denominator };
}

function observedDistribution(overrides: Partial<DistributionObservation> = {}): DistributionObservation {
  return {
    status: 'observed',
    sample_size: 6,
    unit: 'hours',
    median: 4.5,
    percentile_75: 9,
    percentile_90: 21.25,
    ...overrides,
  };
}

describe('figure', () => {
  it('prints as few digits as state the value exactly', () => {
    expect(figure(33.3)).toBe('33.3');
    expect(figure(100)).toBe('100');
    expect(figure(0)).toBe('0');
  });

  it('drops the float noise a division leaves behind', () => {
    expect(figure(33.300000000000004)).toBe('33.3');
  });

  it('leaves an unmeasured value visibly absent, never zero', () => {
    expect(figure(null)).toBe(ABSENT);
    expect(figure(undefined)).toBe(ABSENT);
  });
});

describe('percent', () => {
  it('suffixes a measured share', () => {
    expect(percent(12.5)).toBe('12.5%');
    expect(percent(0)).toBe('0%');
  });

  it('leaves an unmeasured share absent', () => {
    expect(percent(null)).toBe(ABSENT);
  });
});

describe('quantity', () => {
  it('states a large count in full rather than in exponent form', () => {
    expect(quantity(1000000)).toBe('1000000');
    expect(quantity(0)).toBe('0');
  });

  it('leaves an uncounted value absent', () => {
    expect(quantity(undefined)).toBe(ABSENT);
    expect(quantity(null)).toBe(ABSENT);
  });
});

describe('percentageOf', () => {
  it('takes a share of the population it was counted over', () => {
    expect(percentageOf(3, 4)).toBe('75%');
  });

  it('has no share to state over an empty population', () => {
    expect(percentageOf(0, 0)).toBe(ABSENT);
  });

  it('never rounds one person down to a deliberate zero', () => {
    expect(percentageOf(1, 4000)).toBe('<0.1%');
  });

  it('prints a measured zero as a zero', () => {
    expect(percentageOf(0, 20)).toBe('0%');
  });

  it('breaks an exact tie the way Python does, so the page and the report agree', () => {
    // 78 of 96 is exactly 81.25: `round(value, 1)` gives 81.2 and half-up gives 81.3, which would
    // put two figures for one observation on the same page. 3 of 16 is exactly 18.75, the tie whose
    // even neighbour is upwards, and both ways of rounding agree on 18.8.
    expect(percentageOf(78, 96)).toBe('81.2%');
    expect(rate(observedRate(78, 96))).toBe('81.2%');
    expect(percentageOf(3, 16)).toBe('18.8%');
  });
});

describe('isRate', () => {
  it('tells the two observation shapes apart', () => {
    expect(isRate(observedRate(1, 2))).toBe(true);
    expect(isRate(observedDistribution())).toBe(false);
  });
});

describe('rate', () => {
  it('states the percentage the report states', () => {
    expect(rate(observedRate(1, 3))).toBe('33.3%');
    expect(rate(observedRate(20, 20))).toBe('100%');
  });

  it('says so in words when there was no eligible sample', () => {
    expect(rate({ status: 'not_applicable', numerator: 0, denominator: 0 })).toBe(NOT_APPLICABLE);
  });

  it('names the population a percentage was taken over', () => {
    expect(basis(observedRate(6, 20))).toBe('6 of 20');
  });
});

describe('median', () => {
  it('carries the unit the observation was measured in', () => {
    expect(median(observedDistribution())).toBe('4.5 hours');
  });

  it('leaves an unmeasured percentile absent beside its unit', () => {
    // Absent, not null: the service omits an unobserved percentile rather than sending one.
    expect(median(observedDistribution({ median: undefined }))).toBe(`${ABSENT} hours`);
  });

  it('says so in words when there was no eligible sample', () => {
    expect(median(observedDistribution({ status: 'not_applicable' }))).toBe(NOT_APPLICABLE);
  });

  it('counts its sample, pluralised', () => {
    expect(sample(observedDistribution())).toBe('6 samples');
    expect(sample(observedDistribution({ sample_size: 1 }))).toBe('1 sample');
  });
});

describe('summarise', () => {
  it('reads whichever shape the metric carries', () => {
    expect(summarise(observedRate(1, 4))).toBe('25%');
    expect(summarise(observedDistribution())).toBe('4.5 hours');
  });

  it('describes what either shape was measured over', () => {
    expect(population(observedRate(1, 4))).toBe('1 of 4');
    expect(population(observedDistribution())).toBe('6 samples');
  });
});

describe('instant', () => {
  it('prints UTC to the minute', () => {
    expect(instant('2026-08-25T09:30:12Z')).toBe('2026-08-25T09:30Z');
  });

  it('converts a written offset rather than printing its wall clock', () => {
    expect(instant('2026-01-05T09:30:00+02:00')).toBe('2026-01-05T07:30Z');
  });

  it('reads an absent or unparseable instant as absent', () => {
    expect(instant(undefined)).toBe(ABSENT);
    expect(instant(null)).toBe(ABSENT);
    expect(instant('one tuesday')).toBe(ABSENT);
  });
});

describe('day and span', () => {
  it('prints the UTC day an instant fell on', () => {
    expect(day('2026-08-25T23:30:00Z')).toBe('2026-08-25');
  });

  it('reads an absent day as absent', () => {
    expect(day(null)).toBe(ABSENT);
  });

  it('states a window as the days it runs between', () => {
    expect(span('2026-07-28T00:00:00Z', '2026-08-25T00:00:00Z')).toBe('2026-07-28 to 2026-08-25');
  });
});

describe('count', () => {
  it('pluralises against its own noun', () => {
    expect(count(1, 'repository', 'repositories')).toBe('1 repository');
    expect(count(0, 'repository', 'repositories')).toBe('0 repositories');
    expect(count(14, 'repository', 'repositories')).toBe('14 repositories');
  });
});
