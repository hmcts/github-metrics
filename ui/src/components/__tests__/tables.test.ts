/**
 * Markup checks for the two server-rendered estate lists, `/contributors` and `/teams`.
 *
 * `RepositoriesTable` is not here: it reads its filters from the router, which this renderer has no
 * context for, so its decidable part is tested as pure functions in `lib/__tests__/rows.test.ts`
 * instead. What these assert is what the guardrails are about — a contributor list ordered by the
 * labels its rows already carry and by nothing else, a team's own people list with no sortable header
 * at all, and team cards carrying label COUNTS and no combined verdict.
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
        { login: 'alice', repositories: 3, labels: ['red'] },
        { login: 'bob', repositories: 1, labels: [] },
        { login: 'carol', repositories: 2, labels: ['green', 'amber'] },
        { login: 'dan', repositories: 4, labels: ['green'] },
      ],
      weeks: 8,
      labelled: true,
    }),
  );

  it('links each login in mono, carrying the span onto the drill-through', () => {
    expect(markup).toContain('font-mono');
    expect(markup).toContain('/contributors/alice?weeks=8');
    expect(markup).toContain('/contributors/bob?weeks=8');
  });

  it('heads three columns, Login then Repositories then Readiness', () => {
    expect(markup.indexOf('Login')).toBeLessThan(markup.indexOf('Repositories'));
    expect(markup.indexOf('Repositories')).toBeLessThan(markup.indexOf('Readiness'));
  });

  // Per row rather than over the whole table: a cell handed the wrong person's labels, or every
  // person's labels, still puts each of these words somewhere in the markup.
  function cells(login: string): string {
    return markup.split('<tr').find((row) => row.includes(`>${login}<`)) ?? '';
  }

  it('carries a badge for every label a person’s repositories hold, and none they do not', () => {
    expect(cells('carol')).toContain('Ready');
    expect(cells('carol')).toContain('Caution');
    expect(cells('carol')).not.toContain('Blocked');
    expect(cells('alice')).toContain('Blocked');
    expect(cells('alice')).not.toContain('Ready');
    expect(markup).toContain('justify-end');
  });

  // The repositories behind an empty list were graded and came back unreadable, so the cell says so
  // in the report's own word rather than dashing, which would read as nothing having been checked.
  it('badges a person with nothing left to label as cannot assess', () => {
    expect(cells('bob')).toContain('Cannot assess');
    expect(cells('bob')).not.toContain('>-<');
    expect(markup).not.toContain('Not assessed');
  });

  it('opens on readiness ascending: all green first, then a caution, then a block', () => {
    expect(markup.indexOf('dan')).toBeLessThan(markup.indexOf('carol'));
    expect(markup.indexOf('carol')).toBeLessThan(markup.indexOf('alice'));
  });

  // Only the opening render is decidable here: the direction is component state, and `sorted` is
  // what puts an unmeasured cell last in BOTH directions, asserted over `combinationKey` in
  // `lib/__tests__/rag.test.ts` and over the reversal itself in `lib/__tests__/sort.test.ts`.
  it('puts the person with no label last on the render the list opens with', () => {
    expect(markup.indexOf('alice')).toBeLessThan(markup.indexOf('bob'));
  });

  it('ranks nobody: the headers order labels and counts, and state no verdict', () => {
    expect(markup).not.toMatch(/score|rank|verdict|average|%/i);
  });

  it('contains no emoji: a label is a word and a colour', () => {
    expect(markup).not.toMatch(/\p{Extended_Pictographic}/u);
  });

  // Against a `metrics-serve` older than 2026-09-02 the field is absent, not empty, and `API_URL` is
  // read per request so a deployment can be pointed at one. The list must render unlabelled.
  it('renders a row a service too old to send labels sent, rather than failing', () => {
    const older = renderToStaticMarkup(
      createElement(ActorsTable, {
        rows: [{ login: 'erin', repositories: 2 }],
        weeks: 8,
        labelled: true,
      }),
    );
    expect(older).toContain('>erin<');
    expect(older).toContain('Cannot assess');
    expect(older).not.toContain('Not assessed');
  });

  /**
   * The other cause of an empty label list, which the badge above would state as the wrong fact.
   *
   * With `AssessmentConfiguration.enabled` off, `evidence.py` grades no repository, so every
   * `/actors` row arrives with an empty list and `/overview` counts the whole estate under
   * `not_assessed`. "Cannot assess" would report unreadable merge gates across an estate nothing
   * was ever asked about — and `/repositories` and `/teams` say "Not assessed" about the very same
   * repositories on the same deployment.
   */
  it('says not assessed, not cannot assess, where the policy graded nothing in the window', () => {
    const ungraded = renderToStaticMarkup(
      createElement(ActorsTable, {
        rows: [
          { login: 'erin', repositories: 2, labels: [] },
          { login: 'frank', repositories: 1, labels: [] },
        ],
        weeks: 8,
        labelled: false,
      }),
    );
    expect(ungraded).toContain('>erin<');
    expect(ungraded).toContain('Not assessed');
    expect(ungraded).not.toContain('Cannot assess');
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
