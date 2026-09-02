import { describe, expect, it } from 'vitest';
import { filterTarget, matches } from '@/lib/filter';

describe('filterTarget', () => {
  it('writes the term into the query', () => {
    expect(filterTarget('/', '', 'repository', 'api')).toBe('/?repository=api');
  });

  it('keeps every other parameter, above all the window', () => {
    const target = filterTarget('/teams/platform', '?weeks=12&label=red', 'repository', 'api');
    expect(target).toBe('/teams/platform?weeks=12&label=red&repository=api');
  });

  it('replaces a term already in the query rather than appending a second one', () => {
    expect(filterTarget('/', '?repository=old&weeks=4', 'repository', 'new')).toBe(
      '/?repository=new&weeks=4',
    );
  });

  it('trims the term, so a trailing space does not filter for nothing', () => {
    expect(filterTarget('/', '', 'repository', '  api  ')).toBe('/?repository=api');
  });

  it('removes the parameter for an empty term instead of leaving it empty', () => {
    expect(filterTarget('/', '?repository=api&weeks=4', 'repository', '')).toBe('/?weeks=4');
    expect(filterTarget('/', '?repository=api&weeks=4', 'repository', '   ')).toBe('/?weeks=4');
  });

  it('returns a bare path when clearing the only parameter', () => {
    expect(filterTarget('/contributors', '?repository=api', 'repository', '')).toBe('/contributors');
  });

  it('encodes a term that would otherwise break the query', () => {
    expect(filterTarget('/', '', 'repository', 'hmcts/api service')).toBe(
      '/?repository=hmcts%2Fapi+service',
    );
  });
});

describe('matches', () => {
  it('matches a substring whatever the case', () => {
    expect(matches('hmcts/API-service', 'api')).toBe(true);
    expect(matches('hmcts/api-service', 'API')).toBe(true);
  });

  it('does not match text the term is absent from', () => {
    expect(matches('hmcts/api-service', 'frontend')).toBe(false);
  });

  it('matches everything for an empty or blank term', () => {
    expect(matches('hmcts/api-service', '')).toBe(true);
    expect(matches('hmcts/api-service', '  ')).toBe(true);
  });
});
