/**
 * Markup checks for the two server-rendered estate lists, `/contributors` and `/teams`.
 *
 * `RepositoriesTable` is not here: it reads its filters from the router, which this renderer has no
 * context for, so its decidable part is tested as pure functions in `lib/__tests__/rows.test.ts`
 * instead. What these assert is what the guardrails are about — a contributor list with no sortable
 * header and no metric column, and team cards carrying label COUNTS and no combined verdict.
 */

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { ActorsTable } from '@/components/ActorsTable';
import { TeamActorsTable } from '@/components/TeamActorsTable';
import { TeamsList } from '@/components/TeamsList';

describe('ActorsTable', () => {
  const markup = renderToStaticMarkup(
    createElement(ActorsTable, {
      rows: [
        { login: 'alice', repositories: 3 },
        { login: 'bob', repositories: 1 },
      ],
      weeks: 8,
    }),
  );

  it('links each login in mono, carrying the span onto the drill-through', () => {
    expect(markup).toContain('font-mono');
    expect(markup).toContain('/contributors/alice?weeks=8');
    expect(markup).toContain('/contributors/bob?weeks=8');
  });

  it('offers no way to order people: no sortable header, no metric column', () => {
    expect(markup).not.toContain('<button');
    expect(markup).not.toContain('aria-sort');
    expect(markup).toContain('Repositories');
  });

  it('keeps the order it was given rather than imposing one of its own', () => {
    expect(markup.indexOf('alice')).toBeLessThan(markup.indexOf('bob'));
  });
});

describe('TeamActorsTable', () => {
  const markup = renderToStaticMarkup(
    createElement(TeamActorsTable, {
      rows: [
        { login: 'alice', repositories: 2, contributions: 9 },
        { login: 'bob', repositories: 1, contributions: 3 },
      ],
      weeks: 12,
    }),
  );

  it('links each login in mono, carrying the span onto the drill-through', () => {
    expect(markup).toContain('font-mono');
    expect(markup).toContain('/contributors/alice?weeks=12');
    expect(markup).toContain('/contributors/bob?weeks=12');
  });

  it('states both figures as counts scoped to this team', () => {
    expect(markup).toContain('Repositories in team');
    expect(markup).toContain('Merges in team');
    expect(markup).toContain('>9<');
  });

  it('offers no way to order people: no sortable header and no rate', () => {
    expect(markup).not.toContain('<button');
    expect(markup).not.toContain('aria-sort');
    expect(markup).not.toMatch(/score|rank|average|%/i);
  });

  it('keeps the alphabetical order the service sent', () => {
    expect(markup.indexOf('alice')).toBeLessThan(markup.indexOf('bob'));
  });
});

describe('TeamsList', () => {
  const markup = renderToStaticMarkup(
    createElement(TeamsList, {
      rows: [
        {
          team: 'platform',
          repositories: 4,
          unavailable: 1,
          actors: 6,
          labels: { green: 2, amber: 1, red: 0, cannot_assess: 0, not_assessed: 1 },
        },
      ],
      weeks: 4,
    }),
  );

  it('states what a team owns and who worked in it', () => {
    expect(markup).toContain('/teams/platform?weeks=4');
    expect(markup).toContain('4 repositories');
    expect(markup).toContain('6 contributors');
    expect(markup).toContain('1 not reported');
  });

  it('carries label counts, grouped as the donut groups them, with no combined verdict', () => {
    expect(markup).toContain('Ready');
    expect(markup).toContain('Caution');
    expect(markup).toContain('Blocked');
    expect(markup).toContain('Not assessed');
    expect(markup).not.toMatch(/score|verdict|rank/i);
  });

  it('dims a label nothing carries instead of dropping it', () => {
    expect(markup).toContain('opacity:0.38');
  });

  it('draws no box of its own, because the section it renders inside draws one', () => {
    expect(markup).not.toContain('border');
    expect(markup).not.toContain('bg-slate-900');
  });

  it('says nothing at all when no team was configured', () => {
    expect(renderToStaticMarkup(createElement(TeamsList, { rows: [], weeks: 4 }))).not.toContain(
      'rounded-lg',
    );
  });

  it('contains no emoji: a label is a word and a colour', () => {
    expect(markup).not.toMatch(/\p{Extended_Pictographic}/u);
  });
});
