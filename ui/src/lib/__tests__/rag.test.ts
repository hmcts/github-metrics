import { describe, expect, it } from 'vitest';
import {
  RAG_BADGE,
  RAG_BORDER,
  RAG_DOT,
  RAG_HEX,
  RAG_LABEL,
  RAG_STATES,
  anyLabelled,
  badgeClass,
  borderClass,
  combinationKey,
  distributionState,
  labelText,
  severity,
  state,
} from '@/lib/rag';
import { sorted } from '@/lib/sort';
import type { ReadinessLabel } from '@/lib/types';

describe('rag maps', () => {
  it('covers every state in every map, so no lookup needs a fallback', () => {
    for (const map of [RAG_LABEL, RAG_BORDER, RAG_BADGE, RAG_DOT, RAG_HEX]) {
      expect(Object.keys(map).sort()).toEqual([...RAG_STATES].sort());
    }
  });

  it('names no emoji anywhere', () => {
    const rendered = JSON.stringify([RAG_LABEL, RAG_BORDER, RAG_BADGE, RAG_DOT, RAG_HEX]);
    expect(rendered).not.toMatch(/\p{Extended_Pictographic}/u);
  });

  it('uses the report palette for the chart hexes', () => {
    expect(RAG_HEX.green).toBe('#4ade80');
    expect(RAG_HEX.amber).toBe('#fbbf24');
    expect(RAG_HEX.red).toBe('#f87171');
    expect(RAG_HEX.cannot_assess).toBe('#64748b');
  });
});

describe('state', () => {
  it('keeps a graded label', () => {
    expect(state('amber')).toBe('amber');
  });

  it('reads an ungraded repository as none, whether absent or null', () => {
    expect(state(undefined)).toBe('none');
    expect(state(null)).toBe('none');
  });
});

describe('label presentation', () => {
  it('gives every state a colour bar of the same weight', () => {
    for (const label of RAG_STATES) {
      expect(RAG_BORDER[label]).toContain('border-l-4');
    }
  });

  it('resolves the accessors through the same state as the maps', () => {
    expect(borderClass('red')).toBe(RAG_BORDER.red);
    expect(badgeClass(undefined)).toBe(RAG_BADGE.none);
    expect(labelText('cannot_assess')).toBe('Cannot assess');
  });
});

describe('severity', () => {
  it('orders the graded labels by what they say rather than by how they are spelled', () => {
    const rows = [
      { readiness: 'red' as const },
      { readiness: 'green' as const },
      { readiness: 'cannot_assess' as const },
      { readiness: 'amber' as const },
    ];
    const ordered = sorted(rows, (row) => severity(row.readiness), 'ascending');
    expect(ordered.map((row) => row.readiness)).toEqual(['green', 'amber', 'red', 'cannot_assess']);
  });

  it('leaves an ungraded repository unmeasured, so it sorts last in both directions', () => {
    expect(severity(undefined)).toBeUndefined();
    const rows = [{ readiness: undefined }, { readiness: 'green' as const }];
    for (const direction of ['ascending', 'descending'] as const) {
      const ordered = sorted(rows, (row) => severity(row.readiness), direction);
      expect(ordered[1]?.readiness).toBeUndefined();
    }
  });
});

describe('combinationKey', () => {
  const COMBINATIONS: { labels: ReadinessLabel[]; key: string }[] = [
    { labels: ['green'], key: '1' },
    { labels: ['green', 'amber'], key: '12' },
    { labels: ['green', 'amber', 'red'], key: '123' },
    { labels: ['green', 'red'], key: '13' },
    { labels: ['amber'], key: '2' },
    { labels: ['amber', 'red'], key: '23' },
    { labels: ['red'], key: '3' },
  ];

  it('gives each of the seven combinations its own key', () => {
    for (const { labels, key } of COMBINATIONS) {
      expect(combinationKey(labels)).toBe(key);
    }
  });

  it('orders the seven as a reader reads them, all green first and all blocked last', () => {
    const shuffled = [3, 0, 6, 4, 2, 5, 1].map((index) => COMBINATIONS[index]!);
    const ordered = sorted(shuffled, (row) => combinationKey(row.labels), 'ascending');
    expect(ordered.map((row) => row.key)).toEqual(['1', '12', '123', '13', '2', '23', '3']);
  });

  it('reads the same key whatever order the labels arrive in, and however often', () => {
    expect(combinationKey(['red', 'green', 'amber'])).toBe('123');
    expect(combinationKey(['red', 'red', 'green', 'red'])).toBe('13');
  });

  it('leaves a person with nothing left to label unmeasured, so they sort last both ways', () => {
    expect(combinationKey([])).toBeUndefined();
    expect(combinationKey(['cannot_assess'])).toBeUndefined();
    expect(combinationKey(['cannot_assess', 'cannot_assess'])).toBeUndefined();
    const rows = [{ labels: [] as ReadinessLabel[] }, { labels: ['red'] as ReadinessLabel[] }];
    for (const direction of ['ascending', 'descending'] as const) {
      const ordered = sorted(rows, (row) => combinationKey(row.labels), direction);
      expect(ordered[1]?.labels).toEqual([]);
    }
  });

  it('drops a cannot_assess sitting beside a graded label rather than the row', () => {
    expect(combinationKey(['green', 'cannot_assess'])).toBe('1');
  });

  // A service older than 2026-09-02 sends no `labels` key, and `API_URL` is read per request, so the
  // UI can be pointed at one. Reading it as unlabelled is the whole difference between that list
  // rendering and `/contributors` throwing.
  it('reads an absent list as an unlabelled person rather than throwing', () => {
    expect(combinationKey(undefined)).toBeUndefined();
  });
});

describe('anyLabelled', () => {
  /** Every key `service.label_counts` sends, zeros included, as a window with nothing graded. */
  const UNGRADED = { green: 0, amber: 0, red: 0, cannot_assess: 0, not_assessed: 7 };

  it('reads a window the policy graded nothing in, however many repositories it holds', () => {
    expect(anyLabelled(UNGRADED)).toBe(false);
  });

  it('reads a window with any one label as graded, cannot_assess included', () => {
    expect(anyLabelled({ ...UNGRADED, green: 1 })).toBe(true);
    expect(anyLabelled({ ...UNGRADED, amber: 1 })).toBe(true);
    expect(anyLabelled({ ...UNGRADED, red: 1 })).toBe(true);
    // The distinction the contributor list needs: an estate the policy graded and could not read is
    // graded, and its people carry "Cannot assess" rather than "Not assessed".
    expect(anyLabelled({ ...UNGRADED, cannot_assess: 7, not_assessed: 0 })).toBe(true);
  });

  it('ignores the service’s not_assessed key, which is the policy’s absence and not a label', () => {
    expect(anyLabelled({ not_assessed: 12 })).toBe(false);
  });

  it('reads an empty distribution as nothing graded rather than throwing', () => {
    expect(anyLabelled({})).toBe(false);
  });
});

describe('distributionState', () => {
  it('maps a label key to its own state', () => {
    expect(distributionState('green')).toBe('green');
    expect(distributionState('cannot_assess')).toBe('cannot_assess');
  });

  it("maps the service's not_assessed key, and anything unknown, to none", () => {
    expect(distributionState('not_assessed')).toBe('none');
    expect(distributionState('something_else')).toBe('none');
  });
});
