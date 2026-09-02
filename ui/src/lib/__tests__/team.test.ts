/**
 * What the team page says about a team, and what it will not say.
 *
 * The counts are of things — repositories configured, people who worked in them, repositories the
 * span could not report — and none of them combines a label, a rate or a verdict. The last test is
 * the guardrail itself: nothing here produces a figure two teams could be ordered by.
 */

import { describe, expect, it } from 'vitest';
import { holdings, people, unreported } from '@/lib/team';
import type { TeamDetail } from '@/lib/types';

function team(detail: Partial<TeamDetail> = {}): TeamDetail {
  return {
    team: 'platform',
    repositories: [
      { repository: 'api', team: 'platform', readiness: 'green' },
      { repository: 'web', team: 'platform', readiness: 'red' },
    ],
    actors: [
      { login: 'alice', repositories: 2, contributions: 9 },
      { login: 'bob', repositories: 1, contributions: 3 },
    ],
    unavailable: 0,
    labels: { green: 1, red: 1 },
    ...detail,
  };
}

describe('holdings', () => {
  it('counts every configured repository, reported or not', () => {
    expect(holdings(team({ unavailable: 1 }))).toBe('2 repositories');
  });

  it('pluralises against its own noun', () => {
    expect(holdings(team({ repositories: [{ repository: 'api', team: 'platform' }] }))).toBe(
      '1 repository',
    );
  });
});

describe('people', () => {
  it('counts everyone who authored a reported merge in the team', () => {
    expect(people(team())).toBe('2 contributors');
  });

  it('counts nobody where the span holds no reported merge for the team', () => {
    expect(people(team({ actors: [] }))).toBe('0 contributors');
  });
});

describe('unreported', () => {
  it('says how much of the team the span could not report', () => {
    expect(unreported(team({ unavailable: 2 }))).toBe('2 repositories not reported at this span');
    expect(unreported(team({ unavailable: 1 }))).toBe('1 repository not reported at this span');
  });

  it('says nothing at all where every repository was reported', () => {
    expect(unreported(team())).toBeUndefined();
  });
});

describe('the team guardrails', () => {
  it('offers no combined label, score or ordering of teams', () => {
    const stated = [holdings(team()), people(team()), unreported(team({ unavailable: 1 }))].join(
      ' ',
    );
    expect(stated).not.toMatch(/score|verdict|rank|average|overall/i);
  });
});
