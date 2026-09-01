/**
 * What the actor page derives from one contract row, and what it refuses to derive.
 *
 * The refusals are the point of most of these: merges add up because a sum of merges is a number of
 * merges, and nothing else does — no rate is averaged across repositories, and no figure produced
 * here could order one person against another.
 */

import { describe, expect, it } from 'vitest';
import { activity, measured, merges, team } from '@/lib/actor';
import type { ActorReadiness, ActorRepositoryReadiness } from '@/lib/types';

function row(
  repository: string,
  contributions: number,
  metrics: ActorRepositoryReadiness['metrics'] = [],
): ActorRepositoryReadiness {
  return { repository, contributions, blocking: 0, metrics };
}

const ACTOR: ActorReadiness = {
  actor_login: 'Alice',
  repositories: [row('api', 9), row('web', 3)],
};

describe('merges', () => {
  it('adds the contributions across the repositories somebody worked in', () => {
    expect(merges(ACTOR)).toBe(12);
  });

  it('counts nothing for somebody with no reported repository', () => {
    expect(merges({ actor_login: 'bob', repositories: [] })).toBe(0);
  });
});

describe('activity', () => {
  it('states both counts, so neither reads as a rating of the person', () => {
    expect(activity(ACTOR)).toBe('12 merges across 2 repositories');
  });

  it('pluralises each count against its own noun', () => {
    expect(activity({ actor_login: 'bob', repositories: [row('api', 1)] })).toBe(
      '1 merge across 1 repository',
    );
  });

  it('is a count of merges and never a mean of anything', () => {
    expect(activity(ACTOR)).not.toMatch(/average|per|%|score/i);
  });
});

describe('team', () => {
  it('resolves the team owning one repository', () => {
    expect(team({ api: 'platform', web: 'digital' }, 'api')).toBe('platform');
  });

  it('resolves nothing rather than a guess where the service named no owner', () => {
    expect(team({ api: 'platform' }, 'web')).toBeUndefined();
  });
});

describe('measured', () => {
  it('states what a repository’s summaries were measured over', () => {
    expect(measured(row('api', 9))).toBe('9 merges in this repository');
    expect(measured(row('web', 1))).toBe('1 merge in this repository');
  });
});
