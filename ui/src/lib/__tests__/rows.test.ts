/**
 * The repository list's filtering and ordering, tested where they are decidable.
 *
 * These are the checks the table itself cannot make: that a filter never invents an empty list from a
 * parameter it did not recognise, that an unreportable repository keeps its row, and that the default
 * order is by team and name rather than by any figure.
 */

import { describe, expect, it } from 'vitest';
import {
  checksSlices,
  coverageSlices,
  distributionSlices,
  reviewSlices,
  securitySlices,
  unreviewedSlices,
  type PieSlice,
} from '@/lib/chart';
import { RAG_STATES, state } from '@/lib/rag';
import {
  ESTATE_FILTERS,
  FILTER_PARAMETERS,
  PRODUCTION_PARAMETER,
  PRODUCTION_VALUE,
  answerOrder,
  codeownersPresent,
  filterRepositories,
  matchesRepository,
  orderRepositories,
  parseFilters,
  parseProduction,
  productionCount,
  type FilterParameter,
} from '@/lib/rows';
import type { RepositoryRow } from '@/lib/types';

function row(fields: Partial<RepositoryRow> & { repository: string }): RepositoryRow {
  return { team: 'platform', ...fields };
}

/**
 * Four repositories spread across every dimension, including one the window could not report.
 *
 * The gate figures, the policy's verdict and the Sonar measures are here so a filter can be checked
 * against the donut that draws the same dimension: an estate where every row is unmeasured would
 * agree with any classifier at all.
 *
 * All three production answers are represented, which is what the toggle's rule needs: two
 * repositories the list names, one it was read and does not name, and one whose list could not be
 * read at all — the row that has to be left out rather than guessed either way.
 */
const ROWS: RepositoryRow[] = [
  row({
    repository: 'hmcts/web',
    team: 'delivery',
    readiness: 'red',
    finding_occurrences: 6,
    required_approving_reviews: 0,
    required_status_checks: 0,
    unreviewed_substantial: 'above',
    sonar_coverage: 12.5,
    sonar_security_issues: 4,
    production: true,
  }),
  row({
    repository: 'hmcts/api',
    team: 'platform',
    readiness: 'green',
    finding_occurrences: 0,
    required_approving_reviews: 2,
    required_status_checks: 3,
    unreviewed_substantial: 'none',
    sonar_coverage: 95,
    sonar_security_issues: 0,
    production: true,
  }),
  row({
    repository: 'hmcts/tools',
    team: 'platform',
    readiness: 'green',
    required_approving_reviews: 1,
    required_status_checks: 0,
    unreviewed_substantial: 'within',
    sonar_coverage: 85,
    sonar_security_rating: { value: 4 },
    production: false,
  }),
  row({ repository: 'hmcts/legacy', team: 'platform', detail: 'no window was collected' }),
];

/**
 * The donut each dimension is drawn from, so a filter can be held against the slice it came off.
 *
 * The readiness donut counts a label DISTRIBUTION rather than rows — that is what the service sends
 * the pages — so its entry distributes these rows first, by the same `state` the filter bands with.
 */
const DONUT: Record<FilterParameter, (rows: readonly RepositoryRow[]) => PieSlice[]> = {
  label: (rows) =>
    distributionSlices(
      Object.fromEntries(
        RAG_STATES.map((readiness) => [
          readiness,
          rows.filter((entry) => state(entry.readiness) === readiness).length,
        ]),
      ),
    ),
  review: reviewSlices,
  checks: checksSlices,
  unreviewed: unreviewedSlices,
  coverage: coverageSlices,
  security: securitySlices,
};

describe('orderRepositories', () => {
  it('groups a team together, alphabetically within it, ordered by no figure', () => {
    expect(orderRepositories(ROWS).map((entry) => entry.repository)).toEqual([
      'hmcts/web',
      'hmcts/api',
      'hmcts/legacy',
      'hmcts/tools',
    ]);
  });

  it('leaves the rows it was given untouched', () => {
    const original = [...ROWS];
    orderRepositories(ROWS);
    expect(ROWS).toEqual(original);
  });
});

describe('matchesRepository', () => {
  it('matches either name a reader would type, case-insensitively', () => {
    const entry = row({ repository: 'hmcts/API-service', team: 'Platform' });
    expect(matchesRepository(entry, 'api')).toBe(true);
    expect(matchesRepository(entry, 'platform')).toBe(true);
    expect(matchesRepository(entry, 'delivery')).toBe(false);
  });

  it('matches everything on an empty term', () => {
    expect(matchesRepository(row({ repository: 'hmcts/api' }), '  ')).toBe(true);
  });
});

describe('ESTATE_FILTERS', () => {
  it('names one dimension per donut, each with the parameter its links are written in', () => {
    expect(FILTER_PARAMETERS).toEqual([
      'label',
      'review',
      'checks',
      'unreviewed',
      'coverage',
      'security',
    ]);
  });

  it('offers exactly the values its donut has slices for, in the same order and colours', () => {
    for (const filter of ESTATE_FILTERS) {
      const slices = DONUT[filter.parameter](ROWS);
      expect(filter.options.map((option) => option.key)).toEqual(slices.map((slice) => slice.key));
      expect(filter.options.map((option) => option.name)).toEqual(slices.map((slice) => slice.name));
      expect(filter.options.map((option) => option.color)).toEqual(
        slices.map((slice) => slice.color),
      );
    }
  });
});

describe('parseFilters', () => {
  /** Read parameters off a plain object, which is the shape a `URLSearchParams` reader has. */
  function reader(query: Record<string, string>) {
    return (parameter: string) => query[parameter] ?? null;
  }

  it('reads every dimension at once, each in its own vocabulary', () => {
    expect(
      parseFilters(
        reader({
          label: 'cannot_assess',
          review: 'multiple',
          checks: 'none',
          unreviewed: 'within',
          coverage: 'moderate',
          security: 'high',
        }),
      ),
    ).toEqual({
      label: 'cannot_assess',
      review: 'multiple',
      checks: 'none',
      unreviewed: 'within',
      coverage: 'moderate',
      security: 'high',
    });
  });

  it('accepts each value its own dimension offers, and no value from another', () => {
    for (const filter of ESTATE_FILTERS) {
      for (const option of filter.options) {
        expect(parseFilters(reader({ [filter.parameter]: option.key }))).toEqual({
          [filter.parameter]: option.key,
        });
      }
    }
    // `multiple` is a review band and nothing else, so it filters no other dimension.
    expect(parseFilters(reader({ coverage: 'multiple' }))).toEqual({});
  });

  it('drops a value it does not recognise, so a stale link shows the list rather than a blank', () => {
    expect(parseFilters(reader({ label: 'purple', review: 'required' }))).toEqual({
      review: 'required',
    });
    expect(parseFilters(reader({ security: '' }))).toEqual({});
    expect(parseFilters(reader({}))).toEqual({});
  });
});

describe('filterRepositories', () => {
  it('applies the term and the readiness together', () => {
    const found = filterRepositories(ROWS, 'hmcts', { label: 'green' });
    expect(found.map((entry) => entry.repository)).toEqual(['hmcts/api', 'hmcts/tools']);
  });

  it('keeps an unreportable repository, which carries no label, under the ungraded state', () => {
    expect(filterRepositories(ROWS, '', { label: 'none' }).map((entry) => entry.repository)).toEqual(
      ['hmcts/legacy'],
    );
  });

  it('selects a label this build does not know under the state its donut counted it in', () => {
    // A `metrics-serve` newer than these pages can send a fifth label, which `distributionState`
    // counts under "Not assessed". Selecting that slice has to return the row it counted, or the
    // table shows one row fewer than the wedge beside it said.
    const future = row({ repository: 'hmcts/next', readiness: 'purple' as 'green' });
    expect(
      filterRepositories([...ROWS, future], '', { label: 'none' }).map((entry) => entry.repository),
    ).toEqual(['hmcts/legacy', 'hmcts/next']);
  });

  it('filters on the term alone when no dimension was named', () => {
    expect(filterRepositories(ROWS, 'legacy', {})).toHaveLength(1);
    expect(filterRepositories(ROWS, '', {})).toHaveLength(ROWS.length);
  });

  it('filters on each dimension on its own, by the band its own donut counts with', () => {
    const named = (filters: Parameters<typeof filterRepositories>[2]) =>
      filterRepositories(ROWS, '', filters).map((entry) => entry.repository);

    expect(named({ review: 'multiple' })).toEqual(['hmcts/api']);
    expect(named({ checks: 'none' })).toEqual(['hmcts/web', 'hmcts/tools']);
    expect(named({ unreviewed: 'above' })).toEqual(['hmcts/web']);
    expect(named({ coverage: 'moderate' })).toEqual(['hmcts/tools']);
    expect(named({ security: 'medium' })).toEqual(['hmcts/web']);
    expect(named({ security: 'high' })).toEqual(['hmcts/tools']);
    // Every dimension counts the unreportable repository under its own unmeasured band.
    expect(named({ review: 'unknown' })).toEqual(['hmcts/legacy']);
  });

  it('holds every named dimension at once, and the term with them', () => {
    expect(
      filterRepositories(ROWS, '', { label: 'green', checks: 'none' }).map(
        (entry) => entry.repository,
      ),
    ).toEqual(['hmcts/tools']);
    // Two dimensions no repository satisfies together is an empty table, which is the honest answer:
    // the reader asked for something, not for a parameter to be ignored.
    expect(filterRepositories(ROWS, '', { label: 'red', checks: 'required' })).toEqual([]);
    expect(filterRepositories(ROWS, 'tools', { label: 'green', checks: 'none' })).toHaveLength(1);
    expect(filterRepositories(ROWS, 'api', { label: 'green', checks: 'none' })).toHaveLength(0);
  });

  it('shows exactly the rows the clicked slice counted, for every slice of every donut', () => {
    for (const filter of ESTATE_FILTERS) {
      for (const slice of DONUT[filter.parameter](ROWS)) {
        expect(filterRepositories(ROWS, '', { [filter.parameter]: slice.key })).toHaveLength(
          slice.value,
        );
      }
    }
  });
});

describe('parseProduction', () => {
  it('names a parameter of its own, outside the six the donuts write', () => {
    expect(PRODUCTION_PARAMETER).toBe('production');
    expect(FILTER_PARAMETERS).not.toContain(PRODUCTION_PARAMETER);
    expect(ESTATE_FILTERS.map((filter) => filter.parameter)).not.toContain(PRODUCTION_PARAMETER);
  });

  it('reads the value the toggle writes as on, and everything else as off', () => {
    expect(parseProduction(() => PRODUCTION_VALUE)).toBe(true);
    expect(parseProduction(() => null)).toBe(false);
    // Not truthiness: one spelling, so a hand-typed `?production=0` does not read as a yes.
    expect(parseProduction(() => '0')).toBe(false);
    expect(parseProduction(() => '')).toBe(false);
    expect(parseProduction(() => 'yes')).toBe(false);
  });

  it('reads its own parameter and no other, so a chip’s value cannot turn it on', () => {
    const query: Record<string, string> = { label: PRODUCTION_VALUE };
    expect(parseProduction((parameter) => query[parameter] ?? null)).toBe(false);
    query[PRODUCTION_PARAMETER] = PRODUCTION_VALUE;
    expect(parseProduction((parameter) => query[parameter] ?? null)).toBe(true);
  });
});

describe('filterRepositories with the production toggle', () => {
  it('holds only the repositories the list names when the toggle is on', () => {
    expect(filterRepositories(ROWS, '', {}, true).map((entry) => entry.repository)).toEqual([
      'hmcts/web',
      'hmcts/api',
    ]);
  });

  it('leaves the whole estate alone when the toggle is off, and by default', () => {
    expect(filterRepositories(ROWS, '', {}, false)).toHaveLength(ROWS.length);
    expect(filterRepositories(ROWS, '', {})).toHaveLength(ROWS.length);
  });

  it('excludes a repository whose answer could not be read rather than guessing it either way', () => {
    // `hmcts/tools` was read and is not a production service; `hmcts/legacy` carries no answer at
    // all. Neither is in the filtered list, and the second is the one a `false` default would have
    // silently made a decision about.
    const found = filterRepositories(ROWS, '', {}, true).map((entry) => entry.repository);
    expect(found).not.toContain('hmcts/tools');
    expect(found).not.toContain('hmcts/legacy');
  });

  it('ANDs with the term and with every dimension', () => {
    expect(filterRepositories(ROWS, 'api', {}, true).map((entry) => entry.repository)).toEqual([
      'hmcts/api',
    ]);
    expect(
      filterRepositories(ROWS, '', { label: 'green' }, true).map((entry) => entry.repository),
    ).toEqual(['hmcts/api']);
    // A dimension the production repositories do not satisfy is an empty table, not an ignored one.
    expect(filterRepositories(ROWS, '', { checks: 'none' }, true)).toEqual([
      ROWS.find((entry) => entry.repository === 'hmcts/web'),
    ]);
    expect(filterRepositories(ROWS, 'tools', { label: 'green' }, true)).toEqual([]);
  });
});

describe('productionCount', () => {
  it('counts the production repositories the reader can currently see', () => {
    expect(productionCount(ROWS, '', {})).toBe(2);
  });

  it('counts neither a repository read as non-production nor one with no answer', () => {
    expect(productionCount([ROWS[2] as RepositoryRow, ROWS[3] as RepositoryRow], '', {})).toBe(0);
  });

  it('narrows with the term and the other dimensions, which is what makes the figure move', () => {
    expect(productionCount(ROWS, 'api', {})).toBe(1);
    expect(productionCount(ROWS, '', { label: 'green' })).toBe(1);
    expect(productionCount(ROWS, '', { label: 'none' })).toBe(0);
  });

  it('excludes its own dimension, so the count says what turning the toggle on would leave', () => {
    // The count is read while the toggle is on as well as off, and it has to be the same figure:
    // one that counted its own filter would print the number already on screen.
    expect(productionCount(ROWS, '', {})).toBe(filterRepositories(ROWS, '', {}, true).length);
  });
});

describe('codeownersPresent', () => {
  it('answers yes on a file found and no on a repository that was read and held none', () => {
    expect(codeownersPresent(row({ repository: 'hmcts/api', codeowners_files: 1 }))).toBe(true);
    expect(codeownersPresent(row({ repository: 'hmcts/api', codeowners_files: 3 }))).toBe(true);
    expect(codeownersPresent(row({ repository: 'hmcts/api', codeowners_files: 0 }))).toBe(false);
  });

  it('answers nothing where the count is absent, which is contents nobody could read', () => {
    expect(codeownersPresent(row({ repository: 'hmcts/api' }))).toBeUndefined();
  });
});

describe('answerOrder', () => {
  it('orders no below yes, so ascending opens on the repositories without one', () => {
    expect(answerOrder(false)).toBe(0);
    expect(answerOrder(true)).toBe(1);
  });

  it('leaves an unreadable answer undefined, the value `sorted` holds back from both ends', () => {
    expect(answerOrder(undefined)).toBeUndefined();
  });
});
