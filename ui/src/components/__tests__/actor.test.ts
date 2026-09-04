/**
 * Markup checks for the actor page's own table.
 *
 * The page itself is not rendered here — it reads cookies and the service — so what these assert is
 * the part carrying a rule: a list of one person's repositories with no way to order them by a label
 * or a figure, a readiness column that lists each repository's own label and combines none of them,
 * and drill-through links that carry the span onward.
 */

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { ActorRepositoriesTable } from '@/components/ActorRepositoriesTable';
import { PRODUCTION_BADGE } from '@/lib/production';
import { RAG_BORDER } from '@/lib/rag';
import type { ActorRepositoryReadiness } from '@/lib/types';

const ROWS: ActorRepositoryReadiness[] = [
  { repository: 'api', readiness: 'red', contributions: 9, blocking: 4, metrics: [] },
  { repository: 'web', readiness: 'green', contributions: 3, blocking: 0, metrics: [] },
  { repository: 'tools', contributions: 1, blocking: 0, metrics: [] },
];

/** The table at whatever production answer the case is about, absent by default. */
function render(production?: readonly string[]): string {
  return renderToStaticMarkup(
    createElement(ActorRepositoriesTable, {
      rows: ROWS,
      teams: { api: 'platform', web: 'digital' },
      production,
      weeks: 8,
    }),
  );
}

const markup = render();

describe('ActorRepositoriesTable', () => {
  it('links each repository and its team, carrying the span onto both', () => {
    expect(markup).toContain('/repositories/api?weeks=8');
    expect(markup).toContain('/teams/platform?weeks=8');
    expect(markup).toContain('/teams/digital?weeks=8');
    expect(markup).toContain('font-mono');
  });

  it('states each repository’s label as a colour bar and a word', () => {
    expect(markup).toContain(RAG_BORDER.red);
    expect(markup).toContain(RAG_BORDER.green);
    expect(markup).toContain('Blocked');
    expect(markup).toContain('Ready');
  });

  it('grades an ungraded repository as not assessed rather than as passing', () => {
    expect(markup).toContain(RAG_BORDER.none);
    expect(markup).toContain('Not assessed');
  });

  it('says so where the service named no owning team, rather than inventing one', () => {
    expect(markup).toContain('no owning team was reported');
  });

  /**
   * The Production column, directly right of Readiness and badged from the person's own list.
   *
   * The list is the person's, not the estate's: this table answers "which of the repositories THIS
   * PERSON worked in deploy to production", so a repository they did not touch never reaches it.
   */
  it('heads Production directly right of Readiness, and no further column', () => {
    expect(markup.indexOf('Readiness')).toBeLessThan(markup.indexOf('Production'));
    expect(markup.indexOf('Production')).toBeLessThan(markup.indexOf('Contributions'));
  });

  it('badges only the repositories the list names, and nothing where there is no list', () => {
    const badged = render(['web']);

    // Exactly one badge, and in the `web` row: it falls between the two rows either side of it,
    // whose repositories the list was read for and does not name.
    expect(badged.split(PRODUCTION_BADGE)).toHaveLength(2);
    expect(badged.indexOf('/repositories/api')).toBeLessThan(badged.indexOf(PRODUCTION_BADGE));
    expect(badged.indexOf(PRODUCTION_BADGE)).toBeLessThan(badged.indexOf('/repositories/tools'));

    // No list at all: the column stands and every cell in it is empty, which is what an unread
    // answer looks like — the same as a negative one, and deliberately so.
    expect(markup).toContain('Production');
    expect(markup).not.toContain(PRODUCTION_BADGE);
  });

  it('re-reports the counts the contract carries, per repository', () => {
    expect(markup).toContain('Contributions');
    expect(markup).toContain('Blocking occurrences');
  });

  it('keeps the contract’s order rather than offering one of its own', () => {
    expect(markup.indexOf('api')).toBeLessThan(markup.indexOf('web'));
    expect(markup).not.toContain('aria-sort');
    expect(markup).not.toContain('<button');
  });

  it('combines nothing across the repositories and scores nobody', () => {
    expect(markup).not.toMatch(/average|total|score|rank/i);
  });

  it('contains no emoji: a label is a word and a colour', () => {
    expect(markup).not.toMatch(/\p{Extended_Pictographic}/u);
  });
});
