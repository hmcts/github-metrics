import { describe, expect, it } from 'vitest';
import { compare, reverse, sorted, type SortValue } from '@/lib/sort';

interface Row {
  repository: string;
  open?: number;
}

const rows: Row[] = [
  { repository: 'delta', open: 2 },
  { repository: 'alpha', open: 7 },
  { repository: 'charlie' },
  { repository: 'bravo', open: 2 },
];

function names(ordered: readonly Row[]): string[] {
  return ordered.map((row) => row.repository);
}

describe('reverse', () => {
  it('flips a direction', () => {
    expect(reverse('ascending')).toBe('descending');
    expect(reverse('descending')).toBe('ascending');
  });
});

describe('compare', () => {
  it('orders two numbers numerically, not as text', () => {
    expect(compare(9, 10)).toBeLessThan(0);
  });

  it('orders text by locale', () => {
    expect(compare('alpha', 'bravo')).toBeLessThan(0);
    expect(compare('bravo', 'alpha')).toBeGreaterThan(0);
    expect(compare('alpha', 'alpha')).toBe(0);
  });

  it('reads an absent value as empty text when it reaches the comparison', () => {
    const absent: SortValue = undefined;
    expect(compare(absent, 'alpha')).toBeLessThan(0);
    expect(compare(1, absent)).toBeGreaterThan(0);
  });
});

describe('sorted', () => {
  it('orders by the read value ascending', () => {
    expect(names(sorted(rows, (row) => row.repository, 'ascending'))).toEqual([
      'alpha',
      'bravo',
      'charlie',
      'delta',
    ]);
  });

  it('orders descending without disturbing ties', () => {
    expect(names(sorted(rows, (row) => row.open, 'descending'))).toEqual([
      'alpha',
      'delta',
      'bravo',
      'charlie',
    ]);
  });

  it('leaves an unmeasured figure last in both directions', () => {
    expect(names(sorted(rows, (row) => row.open, 'ascending')).at(-1)).toBe('charlie');
    expect(names(sorted(rows, (row) => row.open, 'descending')).at(-1)).toBe('charlie');
  });

  it('does not order the rows it was given', () => {
    const original = names(rows);
    sorted(rows, (row) => row.repository, 'descending');
    expect(names(rows)).toEqual(original);
  });
});
