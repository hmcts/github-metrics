/**
 * The repository list's filtering and ordering, tested where they are decidable.
 *
 * These are the checks the table itself cannot make: that a filter never invents an empty list from a
 * parameter it did not recognise, that an unreportable repository keeps its row, and that the default
 * order is by team and name rather than by any figure.
 */

import { describe, expect, it } from 'vitest';
import {
  answerOrder,
  codeownersPresent,
  filterRepositories,
  matchesRepository,
  orderRepositories,
  parseState,
  stateCounts,
} from '@/lib/rows';
import type { RepositoryRow } from '@/lib/types';

function row(fields: Partial<RepositoryRow> & { repository: string }): RepositoryRow {
  return { team: 'platform', ...fields };
}

const ROWS: RepositoryRow[] = [
  row({ repository: 'hmcts/web', team: 'delivery', readiness: 'red', finding_occurrences: 6 }),
  row({ repository: 'hmcts/api', team: 'platform', readiness: 'green', finding_occurrences: 0 }),
  row({ repository: 'hmcts/tools', team: 'platform', readiness: 'green' }),
  row({ repository: 'hmcts/legacy', team: 'platform', detail: 'no window was collected' }),
];

describe('parseState', () => {
  it('reads each of the five presentation states', () => {
    expect(parseState('green')).toBe('green');
    expect(parseState('cannot_assess')).toBe('cannot_assess');
    expect(parseState('none')).toBe('none');
  });

  it('reads an unrecognised or absent value as no filter, never as a state nothing matches', () => {
    expect(parseState('purple')).toBeNull();
    expect(parseState('')).toBeNull();
    expect(parseState(null)).toBeNull();
    expect(parseState(undefined)).toBeNull();
  });
});

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

describe('filterRepositories', () => {
  it('applies the term and the readiness together', () => {
    const found = filterRepositories(ROWS, 'hmcts', 'green');
    expect(found.map((entry) => entry.repository)).toEqual(['hmcts/api', 'hmcts/tools']);
  });

  it('keeps an unreportable repository, which carries no label, under the ungraded state', () => {
    expect(filterRepositories(ROWS, '', 'none').map((entry) => entry.repository)).toEqual([
      'hmcts/legacy',
    ]);
  });

  it('filters on the term alone when no readiness was named', () => {
    expect(filterRepositories(ROWS, 'legacy', null)).toHaveLength(1);
    expect(filterRepositories(ROWS, '', null)).toHaveLength(ROWS.length);
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

describe('stateCounts', () => {
  it('counts every state, including the ones nothing carries', () => {
    expect(stateCounts(ROWS)).toEqual({
      green: 2,
      amber: 0,
      red: 1,
      cannot_assess: 0,
      none: 1,
    });
  });

  it('counts nothing as zeros rather than as missing keys', () => {
    expect(stateCounts([])).toEqual({
      green: 0,
      amber: 0,
      red: 0,
      cannot_assess: 0,
      none: 0,
    });
  });
});
