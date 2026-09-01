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
import { RAG_BORDER } from '@/lib/rag';
import type { ActorRepositoryReadiness } from '@/lib/types';

const ROWS: ActorRepositoryReadiness[] = [
  { repository: 'api', readiness: 'red', contributions: 9, blocking: 4, metrics: [] },
  { repository: 'web', readiness: 'green', contributions: 3, blocking: 0, metrics: [] },
  { repository: 'tools', contributions: 1, blocking: 0, metrics: [] },
];

const markup = renderToStaticMarkup(
  createElement(ActorRepositoriesTable, {
    rows: ROWS,
    teams: { api: 'platform', web: 'digital' },
    weeks: 8,
  }),
);

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
