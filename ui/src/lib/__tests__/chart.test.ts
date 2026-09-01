import { describe, expect, it } from 'vitest';
import { activeSlices, distributionSlices, totalValue, type PieSlice } from '@/lib/chart';
import { RAG_HEX, RAG_STATES } from '@/lib/rag';

const slices: PieSlice[] = [
  { name: 'Ready', value: 3, color: '#4ade80' },
  { name: 'Caution', value: 0, color: '#fbbf24' },
  { name: 'Blocked', value: 1, color: '#f87171' },
];

describe('distributionSlices', () => {
  it('names one slice per readiness state, in RAG order, with the report palette', () => {
    const built = distributionSlices({ green: 2, amber: 1, red: 0, cannot_assess: 1 });
    expect(built.map((slice) => slice.value)).toEqual([2, 1, 0, 1, 0]);
    expect(built.map((slice) => slice.color)).toEqual(RAG_STATES.map((state) => RAG_HEX[state]));
  });

  it('keeps a zero-count label as a slice, so the legend still lists it', () => {
    const built = distributionSlices({ green: 4 });
    expect(built).toHaveLength(RAG_STATES.length);
    expect(built.filter((slice) => slice.value === 0)).toHaveLength(4);
  });

  it('sums every key that resolves to one state into a single slice', () => {
    const built = distributionSlices({ not_assessed: 2, something_else: 3, green: 1 });
    const ungraded = built.find((slice) => slice.name === 'Not assessed');
    expect(ungraded?.value).toBe(5);
  });

  it('counts nothing for an empty distribution', () => {
    expect(totalValue(distributionSlices({}))).toBe(0);
  });
});

describe('totalValue', () => {
  it('adds every slice, including the empty ones', () => {
    expect(totalValue(slices)).toBe(4);
  });
});

describe('activeSlices', () => {
  it('drops only the slices with no members', () => {
    expect(activeSlices(slices).map((slice) => slice.name)).toEqual(['Ready', 'Blocked']);
  });
});
