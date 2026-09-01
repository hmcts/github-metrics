/**
 * Markup checks for the shared components.
 *
 * Rendered with `renderToStaticMarkup`, the same renderer Next.js uses for a server component, so
 * these assert what actually reaches the reader: the colour bar classes from `rag.ts`, the ARIA a
 * table header needs to be sortable by keyboard, and the absence of any emoji anywhere in the site's
 * vocabulary. The behaviour behind the interactive components is tested as pure functions in
 * `src/lib` instead — `filter.ts` for the search box, `sort.ts` for the headers, `chart.ts` for the
 * donut — because a click cannot be made in this renderer.
 *
 * JSX is written as `createElement` calls so the tests stay `.ts` files alongside the library tests.
 */

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { EmptyState } from '@/components/EmptyState';
import { EntityHeader } from '@/components/EntityHeader';
import { InfoTooltip } from '@/components/InfoTooltip';
import { MetricCard } from '@/components/MetricCard';
import { RAGCard, RAGLabel, RAGRow } from '@/components/RAGCard';
import { Section } from '@/components/Section';
import { SortHeader } from '@/components/SortHeader';
import { RAG_BORDER } from '@/lib/rag';

describe('MetricCard', () => {
  it('states the figure, its name, and what it was measured over', () => {
    const markup = renderToStaticMarkup(
      createElement(MetricCard, { label: 'Merged', value: '42', detail: '42 of 50' }),
    );
    expect(markup).toContain('Merged');
    expect(markup).toContain('42 of 50');
    expect(markup).toContain('tabular-nums');
  });

  it('renders an unmeasured figure as the dash it was given', () => {
    const markup = renderToStaticMarkup(createElement(MetricCard, { label: 'Coverage', value: '-' }));
    expect(markup).toContain('>-<');
  });

  it('grades nothing: the card carries one border whatever the figure is', () => {
    const low = renderToStaticMarkup(createElement(MetricCard, { label: 'Open', value: 0 }));
    const high = renderToStaticMarkup(createElement(MetricCard, { label: 'Open', value: 900 }));
    expect(low).toContain('border-slate-800');
    expect(high).toContain('border-slate-800');
    expect(high).not.toMatch(/border-(red|amber)-/);
  });
});

describe('Section', () => {
  it('renders one heading style, with its detail and its own control', () => {
    const markup = renderToStaticMarkup(
      createElement(
        Section,
        {
          heading: 'Repositories',
          detail: '2026-08-01 to 2026-09-01',
          action: createElement('span', null, 'filter'),
        },
        createElement('p', null, 'rows'),
      ),
    );
    expect(markup).toContain('uppercase tracking-wide');
    expect(markup).toContain('2026-08-01 to 2026-09-01');
    expect(markup).toContain('filter');
    expect(markup).toContain('rows');
  });

  it('omits the detail and control rows when there are none', () => {
    const markup = renderToStaticMarkup(
      createElement(Section, { heading: 'Findings' }, createElement('p', null, 'rows')),
    );
    expect(markup).not.toContain('ml-auto');
  });
});

describe('EmptyState', () => {
  it('says which kind of empty it is, and what to do about it', () => {
    const markup = renderToStaticMarkup(
      createElement(EmptyState, {
        message: 'This repository is not in the cached window.',
        detail: 'Run metrics collect for the span being asked for.',
      }),
    );
    expect(markup).toContain('not in the cached window');
    expect(markup).toContain('metrics collect');
  });

  it('renders the message alone when there is no instruction to give', () => {
    const markup = renderToStaticMarkup(
      createElement(EmptyState, { message: 'No findings in this window.' }),
    );
    expect(markup).toContain('No findings in this window.');
    expect(markup).not.toContain('text-slate-500');
  });
});

describe('RAG presentation', () => {
  it('states a grade as a word, never as a colour alone', () => {
    const markup = renderToStaticMarkup(createElement(RAGLabel, { label: 'red' }));
    expect(markup).toContain('Blocked');
  });

  it('reads an ungraded thing as not assessed rather than as passing', () => {
    expect(renderToStaticMarkup(createElement(RAGLabel, {}))).toContain('Not assessed');
  });

  it('carries the colour bar down the left edge of a card', () => {
    const markup = renderToStaticMarkup(
      createElement(RAGCard, { label: 'amber', heading: 'hmcts/api', detail: 'One check cautions.' }),
    );
    expect(markup).toContain(RAG_BORDER.amber);
    expect(markup).toContain('hmcts/api');
    expect(markup).toContain('One check cautions.');
    expect(markup).toContain('Caution');
  });

  it('renders a condition row with its grade and the report’s own sentence', () => {
    const markup = renderToStaticMarkup(
      createElement(RAGRow, {
        label: 'red',
        condition: 'merge gate',
        detail: 'default branch is unprotected',
      }),
    );
    expect(markup).toContain(RAG_BORDER.red);
    expect(markup).toContain('merge gate');
    expect(markup).toContain('default branch is unprotected');
  });
});

describe('EntityHeader', () => {
  it('names where the reader is now, in mono, with its kind', () => {
    const markup = renderToStaticMarkup(
      createElement(EntityHeader, {
        kind: 'repository',
        name: 'hmcts/api-service',
        label: 'green',
        context: createElement('a', { href: '/teams/platform?weeks=4' }, 'platform'),
      }),
    );
    expect(markup).toContain('repository');
    expect(markup).toContain('font-mono');
    expect(markup).toContain('hmcts/api-service');
    expect(markup).toContain('Ready');
    expect(markup).toContain('/teams/platform?weeks=4');
  });

  it('grades nothing for an actor or a team, and shows no trail of how you arrived', () => {
    const markup = renderToStaticMarkup(
      createElement(EntityHeader, { kind: 'actor', name: 'octocat' }),
    );
    expect(markup).toContain('octocat');
    expect(markup).not.toContain('border-l-4');
    expect(markup).not.toContain('Not assessed');
    expect(markup).not.toContain('nav');
  });
});

describe('SortHeader', () => {
  it('states the column ordering on the header cell and puts the control in a button', () => {
    const markup = renderToStaticMarkup(
      createElement(SortHeader, {
        label: 'Stale open',
        active: true,
        direction: 'descending',
        onSort: () => undefined,
        numeric: true,
      }),
    );
    expect(markup).toContain('aria-sort="descending"');
    expect(markup).toContain('<button');
    expect(markup).toContain('text-right');
    expect(markup).toContain('Stale open');
  });

  it('reports an unsorted column as unsorted, and draws no chevron for it', () => {
    const markup = renderToStaticMarkup(
      createElement(SortHeader, {
        label: 'Team',
        active: false,
        direction: 'ascending',
        onSort: () => undefined,
      }),
    );
    expect(markup).toContain('aria-sort="none"');
    expect(markup).not.toContain('<svg');
    expect(markup).toContain('text-left');
  });
});

describe('InfoTooltip', () => {
  it('carries its text on the trigger, not in a native title attribute', () => {
    const markup = renderToStaticMarkup(
      createElement(InfoTooltip, { text: 'Counted over merged pull requests only.' }),
    );
    expect(markup).toContain('aria-label="Counted over merged pull requests only."');
    expect(markup).not.toContain('title=');
    expect(markup).toContain('group-hover:block');
    expect(markup).toContain('group-focus-within:block');
  });
});

describe('the shared vocabulary', () => {
  it('contains no emoji: a grade is a word and a colour, never a coloured square', () => {
    const markup = [
      renderToStaticMarkup(createElement(RAGLabel, { label: 'red' })),
      renderToStaticMarkup(createElement(RAGCard, { label: 'green', heading: 'hmcts/api' })),
      renderToStaticMarkup(
        createElement(RAGRow, { label: 'amber', condition: 'sonar', detail: 'gate errored' }),
      ),
      renderToStaticMarkup(createElement(EntityHeader, { kind: 'team', name: 'platform' })),
      renderToStaticMarkup(createElement(MetricCard, { label: 'Merged', value: 1 })),
      renderToStaticMarkup(createElement(EmptyState, { message: 'Nothing here.' })),
    ].join('');
    expect(markup).not.toMatch(/\p{Extended_Pictographic}/u);
  });
});
