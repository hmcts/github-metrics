import { describe, expect, it } from 'vitest';
import {
  RAG_BADGE,
  RAG_BORDER,
  RAG_DOT,
  RAG_HEX,
  RAG_LABEL,
  RAG_STATES,
  badgeClass,
  borderClass,
  distributionState,
  labelText,
  severity,
  state,
} from '@/lib/rag';
import { sorted } from '@/lib/sort';

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
