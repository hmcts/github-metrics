import { describe, expect, it } from 'vitest';
import {
  activeSlices,
  bandSlices,
  checksSlices,
  coverageSlices,
  distributionSlices,
  reviewSlices,
  securitySlices,
  totalValue,
  unreviewedSlices,
  type PieSlice,
} from '@/lib/chart';
import { RAG_HEX, RAG_STATES } from '@/lib/rag';
import {
  CHECKS_BANDS,
  COVERAGE_BANDS,
  REVIEW_BANDS,
  SECURITY_BANDS,
  UNREVIEWED_BANDS,
  STRONG_GOOD_HEX,
  TONE_HEX,
  type Band,
} from '@/lib/tone';
import type { OpenAlertCount, RepositoryRow, SecurityAlertEvidence } from '@/lib/types';

/** A band key of nothing in particular, for the counting `bandSlices` does over any item type. */
type Side = 'left' | 'middle' | 'right';

const slices: PieSlice[] = [
  { key: 'green', name: 'Ready', value: 3, color: '#4ade80' },
  { key: 'amber', name: 'Caution', value: 0, color: '#fbbf24' },
  { key: 'red', name: 'Blocked', value: 1, color: '#f87171' },
];

function row(fields: Partial<RepositoryRow>): RepositoryRow {
  return { repository: 'hmcts/api', team: 'platform', ...fields };
}

/** The counts a builder returned, keyed on the words its legend reads. */
function counts(built: readonly PieSlice[]): Record<string, number> {
  return Object.fromEntries(built.map((slice) => [slice.name, slice.value]));
}

describe('distributionSlices', () => {
  it('names one slice per readiness state, in RAG order, with the report palette', () => {
    const built = distributionSlices({ green: 2, amber: 1, red: 0, cannot_assess: 1 });
    expect(built.map((slice) => slice.value)).toEqual([2, 1, 0, 1, 0]);
    expect(built.map((slice) => slice.color)).toEqual(RAG_STATES.map((state) => RAG_HEX[state]));
  });

  it('keys each slice on its state rather than on the words the legend reads', () => {
    // The key is what a filtered link carries, so it has to survive the labels being reworded.
    expect(distributionSlices({}).map((slice) => slice.key)).toEqual([...RAG_STATES]);
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

  it('counts the repositories the span could not report at all as not assessed', () => {
    // The service distributes the reported repositories only, so a donut drawn beside one over the
    // whole estate has to be told how many it left out or it totals the smaller number.
    const built = distributionSlices({ green: 2, amber: 1 }, 3);
    expect(counts(built)['Not assessed']).toBe(3);
    expect(totalValue(built)).toBe(6);
  });

  it('adds nothing where the caller distributes labels rather than an estate', () => {
    expect(counts(distributionSlices({ green: 2 }))['Not assessed']).toBe(0);
  });
});

describe('bandSlices', () => {
  it('returns one slice per band, in the table order, with the band words and marks', () => {
    const built = bandSlices(REVIEW_BANDS, [row({ required_approving_reviews: 1 })], (repository) =>
      repository.required_approving_reviews === 1 ? 'required' : 'unknown',
    );
    expect(built.map((slice) => slice.name)).toEqual(REVIEW_BANDS.map((band) => band.name));
    expect(built.map((slice) => slice.color)).toEqual(REVIEW_BANDS.map((band) => band.mark));
  });

  it("keys each slice on the band's own key, which is what a filter is written in", () => {
    const built = reviewSlices([row({ required_approving_reviews: 1 })]);
    expect(built.map((slice) => slice.key)).toEqual(REVIEW_BANDS.map((band) => band.key));
  });

  it('counts each item under the band its classifier returns, including the bands nothing fell in', () => {
    // Over a plain item rather than a `RepositoryRow`, which is what the item type is generic for:
    // the counting is the helper's one job and the four builders below only supply a classifier.
    const bands: readonly Band<Side>[] = [
      { key: 'left', name: 'Left', mark: '#000000' },
      { key: 'middle', name: 'Middle', mark: '#111111' },
      { key: 'right', name: 'Right', mark: '#222222' },
    ];
    const items: readonly Side[] = ['left', 'right', 'left'];
    const built = bandSlices(bands, items, (item) => item);

    expect(counts(built)).toEqual({ Left: 2, Middle: 0, Right: 1 });
    expect(totalValue(built)).toBe(3);
  });
});

describe('reviewSlices', () => {
  it('bands every row by the approvals its gate requires', () => {
    const built = reviewSlices([
      row({ required_approving_reviews: 3 }),
      row({ required_approving_reviews: 2 }),
      row({ required_approving_reviews: 1 }),
      row({ required_approving_reviews: 0 }),
      row({}),
    ]);
    expect(counts(built)).toEqual({ Multiple: 2, Enforced: 1, Unenforced: 1, Unknown: 1 });
  });

  it('marks two or more approvals apart from one, deeper green against green', () => {
    const built = reviewSlices([row({ required_approving_reviews: 2 })]);
    expect(built.map((slice) => slice.color).slice(0, 2)).toEqual([STRONG_GOOD_HEX, TONE_HEX.good]);
  });

  it('counts a row with no gate figure unknown rather than dropping it', () => {
    const built = reviewSlices([row({}), row({ required_approving_reviews: 1 })]);
    expect(counts(built).Unknown).toBe(1);
    expect(totalValue(built)).toBe(2);
  });

  it('returns every band at zero for an empty list', () => {
    const built = reviewSlices([]);
    expect(built).toHaveLength(REVIEW_BANDS.length);
    expect(totalValue(built)).toBe(0);
  });
});

describe('checksSlices', () => {
  it('bands every row by whether its gate requires any check', () => {
    const built = checksSlices([
      row({ required_status_checks: 4 }),
      row({ required_status_checks: 1 }),
      row({ required_status_checks: 0 }),
      row({}),
    ]);
    expect(counts(built)).toEqual({ Enforced: 2, Unenforced: 1, Unknown: 1 });
  });

  it('keeps a band nothing fell in, so the legend still lists it', () => {
    const built = checksSlices([row({ required_status_checks: 2 })]);
    expect(built).toHaveLength(CHECKS_BANDS.length);
    expect(counts(built).Unenforced).toBe(0);
    expect(counts(built).Unknown).toBe(0);
  });

  it('counts nothing for an empty list', () => {
    expect(totalValue(checksSlices([]))).toBe(0);
  });
});

describe('unreviewedSlices', () => {
  it("bands every row by the policy's own verdict", () => {
    const built = unreviewedSlices([
      row({ unreviewed_substantial: 'none' }),
      row({ unreviewed_substantial: 'within' }),
      row({ unreviewed_substantial: 'within' }),
      row({ unreviewed_substantial: 'above' }),
      row({}),
    ]);
    expect(counts(built)).toEqual({
      Clear: 1,
      'Within allowance': 2,
      'Above allowance': 1,
      Unknown: 1,
    });
  });

  it('counts a repository the policy graded nothing for as unknown, never as clean', () => {
    const built = unreviewedSlices([row({}), row({})]);
    expect(counts(built).Clear).toBe(0);
    expect(counts(built).Unknown).toBe(2);
  });

  it('returns every band at zero for an empty list', () => {
    const built = unreviewedSlices([]);
    expect(built).toHaveLength(UNREVIEWED_BANDS.length);
    expect(totalValue(built)).toBe(0);
  });
});

describe('coverageSlices', () => {
  it('bands every row on the boundary the repository page colours coverage with', () => {
    const built = coverageSlices([
      row({ sonar_coverage: 90 }),
      row({ sonar_coverage: 94.2 }),
      row({ sonar_coverage: 80 }),
      row({ sonar_coverage: 12.5 }),
      row({}),
    ]);
    expect(counts(built)).toEqual({
      '90% or more': 2,
      '80% to under 90%': 1,
      'Below 80%': 1,
      Unknown: 1,
    });
  });

  it('counts an unmeasured repository unknown rather than at nought per cent', () => {
    const built = coverageSlices([row({}), row({ sonar_coverage: 0 })]);
    expect(counts(built).Unknown).toBe(1);
    expect(counts(built)['Below 80%']).toBe(1);
  });

  it('returns every band at zero for an empty list', () => {
    const built = coverageSlices([]);
    expect(built).toHaveLength(COVERAGE_BANDS.length);
    expect(totalValue(built)).toBe(0);
  });
});

describe('securitySlices', () => {
  /** A security block whose three families are all clear, bar the one a case is about. */
  function security(families: Partial<SecurityAlertEvidence> = {}): SecurityAlertEvidence {
    const clear: OpenAlertCount = { open: 0, by_severity: {} };
    return { dependabot: clear, code_scanning: clear, secret_scanning: clear, ...families };
  }

  it('bands every row on the worst of the five signals it carries', () => {
    const built = securitySlices([
      row({ security: security({ secret_scanning: { open: 1, by_severity: {} } }) }),
      row({ security: security(), sonar_security_rating: { value: 3 } }),
      row({ security: security(), sonar_security_issues: 2 }),
      row({ security: security(), sonar_security_issues: 0 }),
      row({}),
    ]);
    // The C-rated row is Medium, not High, since the 2026-09-04 reversal; the secret scanning row is
    // the one that reaches High here. `tone.test.ts` covers the rating boundary letter by letter.
    expect(counts(built)).toEqual({ Clear: 1, Medium: 2, High: 1, Unknown: 1 });
  });

  it('keeps a band nothing fell in, so the legend still lists it', () => {
    const built = securitySlices([row({ security: security() })]);
    expect(built).toHaveLength(SECURITY_BANDS.length);
    expect(counts(built)).toEqual({ Clear: 1, Medium: 0, High: 0, Unknown: 0 });
  });

  it('counts a row carrying no security signal unknown rather than dropping it', () => {
    const built = securitySlices([row({}), row({ security: security() })]);
    expect(counts(built).Unknown).toBe(1);
    expect(totalValue(built)).toBe(2);
  });

  it('returns every band at zero for an empty list', () => {
    const built = securitySlices([]);
    expect(built).toHaveLength(SECURITY_BANDS.length);
    expect(totalValue(built)).toBe(0);
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
