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
import { ActorRepositoriesTable } from '@/components/ActorRepositoriesTable';
import { ActorsTable } from '@/components/ActorsTable';
import { AssessmentSection } from '@/components/AssessmentSection';
import { ContributorsTable } from '@/components/ContributorsTable';
import { DefinitionList } from '@/components/DefinitionList';
import { EmptyState } from '@/components/EmptyState';
import { EntityHeader } from '@/components/EntityHeader';
import { FindingsTable } from '@/components/FindingsTable';
import { InfoTooltip } from '@/components/InfoTooltip';
import { MetricCard } from '@/components/MetricCard';
import { Navigation } from '@/components/Navigation';
import { RAGCard, RAGLabel, RAGRow } from '@/components/RAGCard';
import { Section } from '@/components/Section';
import { SortHeader } from '@/components/SortHeader';
import { TeamActorsTable } from '@/components/TeamActorsTable';
import { TeamsList } from '@/components/TeamsList';
import { RAG_BORDER } from '@/lib/rag';
import { people } from '@/lib/team';
import { TONES, TONE_BORDER, TONE_VALUE } from '@/lib/tone';
import type { ReadinessAssessment } from '@/lib/types';

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

  it('draws no box of its own: the section it sits in is the box', () => {
    const markup = renderToStaticMarkup(createElement(MetricCard, { label: 'Open', value: 0 }));
    expect(markup).not.toContain('border');
    expect(markup).not.toContain('bg-slate');
  });

  // The card graded nothing at all until 2026-09-02, when the user reversed that rule; these two
  // cases are what replaced it. The threshold behind a tone is `lib/tone.ts`, tested there — what a
  // card owes the reader is that an ungraded figure stays uncoloured and a graded one colours only
  // itself, so a page with a few red figures on it still reads as one surface.
  it('grades nothing it was given no tone for', () => {
    const low = renderToStaticMarkup(createElement(MetricCard, { label: 'Open', value: 0 }));
    const high = renderToStaticMarkup(createElement(MetricCard, { label: 'Open', value: 900 }));
    expect(low.replace('>0<', '>900<')).toEqual(high);
    expect(high).not.toMatch(/(border|text|bg)-(red|amber|green)-/);
    expect(high).not.toContain('rag-');
    expect(high).toContain(TONE_VALUE.neutral);
  });

  it('colours the value it was given a tone for, and nothing else on the card', () => {
    const markup = renderToStaticMarkup(
      createElement(MetricCard, { label: 'Direct commits', value: 214, detail: 'of 300 merges', tone: 'bad' }),
    );
    expect(markup).toContain(`tabular-nums ${TONE_VALUE.bad}`);
    // The name over the figure and the line under it stay slate: one figure is coloured per card.
    expect(markup).toContain('text-xs text-slate-400 uppercase');
    expect(markup).toContain('text-xs text-slate-500 mt-1');
    expect(markup.match(/rag-/g)).toHaveLength(1);
    expect(markup).not.toContain('border');
  });

  it('reads each tone through the one map, so a card cannot invent a colour', () => {
    for (const tone of TONES) {
      const markup = renderToStaticMarkup(createElement(MetricCard, { label: 'Coverage', value: '82%', tone }));
      expect(markup).toContain(TONE_VALUE[tone]);
    }
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

  it('bounds the whole section in one panel, heading included', () => {
    const markup = renderToStaticMarkup(
      createElement(Section, { heading: 'Merge gate' }, createElement('p', null, 'rows')),
    );
    // The panel opens before the heading and the heading row is divided from the body, so the
    // reader sees one surface per section rather than a heading floating above loose cards.
    expect(markup).toMatch(
      /^<section class="bg-slate-900\/40 border border-slate-800 rounded-lg">.*Merge gate/,
    );
    expect(markup).toContain('border-b border-slate-800');
    expect(markup).toMatch(/<div class="p-4"><p>rows<\/p><\/div><\/section>$/);
  });

  it('omits the detail and control rows when there are none', () => {
    const markup = renderToStaticMarkup(
      createElement(Section, { heading: 'Findings' }, createElement('p', null, 'rows')),
    );
    expect(markup).not.toContain('ml-auto');
  });

  it('draws no body at all for a section with nothing under its heading', () => {
    const markup = renderToStaticMarkup(createElement(Section, { heading: 'Findings' }));
    expect(markup).toContain('Findings');
    expect(markup).not.toContain('class="p-4"');
  });
});

describe('DefinitionList', () => {
  it('states each label against its own answer, with the detail line under the pair', () => {
    const markup = renderToStaticMarkup(
      createElement(DefinitionList, {
        values: [
          { label: 'Protected', value: 'yes' },
          { label: 'dependabot', value: '12', detail: 'critical 0 · high 2' },
        ],
      }),
    );
    expect(markup).toContain('<dt class="text-sm text-slate-400">Protected</dt>');
    expect(markup).toContain('>yes<');
    expect(markup).toContain('critical 0 · high 2');
    expect(markup).toContain('divide-y divide-slate-800/50');
  });

  it('colours the answer it was given a tone for, and nothing else on the row', () => {
    const markup = renderToStaticMarkup(
      createElement(DefinitionList, {
        values: [{ label: 'secret-scanning', value: '3', detail: 'no severity is reported', tone: 'bad' }],
      }),
    );
    expect(markup).toContain(TONE_VALUE.bad);
    // The label and the sentence under it stay slate: one value is coloured per row.
    expect(markup).toContain('<dt class="text-sm text-slate-400">secret-scanning</dt>');
    expect(markup).toContain('class="w-full text-xs text-slate-500"');
    expect(markup.match(/rag-/g)).toHaveLength(1);
  });

  it('sets a count in tabular figures and an answer in words in neither', () => {
    const counted = renderToStaticMarkup(
      createElement(DefinitionList, { values: [{ label: 'Approving reviews required', value: '2' }] }),
    );
    const worded = renderToStaticMarkup(
      createElement(DefinitionList, { values: [{ label: 'Protected', value: 'not disclosed' }] }),
    );
    // The dash included: a column of counts with one absent value keeps its digits aligned.
    const absent = renderToStaticMarkup(
      createElement(DefinitionList, { values: [{ label: 'code-scanning', value: '-' }] }),
    );
    expect(counted).toContain('tabular-nums');
    expect(absent).toContain('tabular-nums');
    expect(worded).not.toContain('tabular-nums');
  });

  it('renders nothing at all for a block with no rows, rather than an empty list', () => {
    expect(renderToStaticMarkup(createElement(DefinitionList, { values: [] }))).toEqual('');
  });

  it('reads each tone through the one map, so a row cannot invent a colour', () => {
    for (const tone of TONES) {
      const markup = renderToStaticMarkup(
        createElement(DefinitionList, { values: [{ label: 'Coverage', value: '82%', tone }] }),
      );
      expect(markup).toContain(TONE_VALUE[tone]);
    }
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

  it('keeps the label’s own bar on a blocking row, whatever tone it is given', () => {
    const markup = renderToStaticMarkup(
      createElement(RAGRow, {
        label: 'amber',
        tone: 'good',
        condition: 'approval-coverage-below-target',
        detail: 'approval-coverage is 50% (1 of 2)',
      }),
    );
    expect(markup).toContain(RAG_BORDER.amber);
    expect(markup).not.toContain(TONE_BORDER.good);
  });
});

describe('AssessmentSection', () => {
  /** An assessment whose clear section holds one graded condition and one merely reported. */
  const assessment: ReadinessAssessment = {
    label: 'green',
    blocking: [],
    caution: [
      {
        condition: 'status-checks-not-required',
        detail: 'status checks required before merging to master: 0',
      },
    ],
    clear: [
      {
        condition: 'independent-review-coverage-at-target',
        detail: 'independent-review-coverage is 100% (10 of 10)',
        informational: false,
      },
      {
        condition: 'linear-history-not-required',
        detail: 'merging to master does not require a linear history, which does not bear on the label',
        informational: true,
      },
    ],
  };

  /** Return the one row markup naming a condition, so a row is read apart from its neighbours. */
  function row(condition: string): string {
    const markup = renderToStaticMarkup(createElement(AssessmentSection, { assessment }));
    const rows = markup.split('<div class="bg-slate-900/50');
    return rows.find((candidate) => candidate.includes(condition)) ?? '';
  }

  it('passes a graded clear condition and cautions the section above it', () => {
    expect(row('independent-review-coverage-at-target')).toContain(TONE_BORDER.good);
    expect(row('status-checks-not-required')).toContain(TONE_BORDER.warn);
  });

  it('leaves a condition the policy reported without judging uncoloured', () => {
    const reported = row('linear-history-not-required');
    expect(reported).toContain(TONE_BORDER.neutral);
    expect(reported).not.toMatch(/rag-(green|amber|red)/);
    // The sentence is still there: the row states what was checked, it just grades nothing.
    expect(reported).toContain('does not require a linear history');
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

  it('grades nothing for a contributor or a team, and shows no trail of how you arrived', () => {
    const markup = renderToStaticMarkup(
      createElement(EntityHeader, { kind: 'contributor', name: 'octocat' }),
    );
    expect(markup).toContain('octocat');
    expect(markup).toContain('contributor');
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
      renderToStaticMarkup(
        createElement(DefinitionList, { values: [{ label: 'Protected', value: 'yes', tone: 'good' }] }),
      ),
      renderToStaticMarkup(createElement(EmptyState, { message: 'Nothing here.' })),
    ].join('');
    expect(markup).not.toMatch(/\p{Extended_Pictographic}/u);
  });

  /**
   * The word for a person is CONTRIBUTOR, from 2026-09-02 at the user's instruction.
   *
   * Every component that names people at all is rendered here together, because the rename is only
   * true if it is true everywhere: one heading still reading "Actors" is the whole point of the
   * change missed. Links are stripped before matching — `/actors/…`, `#actors` and the contract's
   * own field names keep the old spelling deliberately, and renaming the routes is a separate
   * change — so what is asserted is the prose, not the plumbing.
   */
  it('calls a person a contributor everywhere the reader can see one', () => {
    const rendered = [
      renderToStaticMarkup(createElement(Navigation)),
      renderToStaticMarkup(createElement(EntityHeader, { kind: 'contributor', name: 'octocat' })),
      renderToStaticMarkup(createElement(FindingsTable, { findings: [], weeks: 4 })),
      renderToStaticMarkup(createElement(ContributorsTable, { rows: [], weeks: 4 })),
      renderToStaticMarkup(createElement(ActorsTable, { rows: [], weeks: 4 })),
      renderToStaticMarkup(createElement(TeamActorsTable, { rows: [], weeks: 4 })),
      renderToStaticMarkup(
        createElement(ActorRepositoriesTable, { rows: [], teams: {}, weeks: 4 }),
      ),
      renderToStaticMarkup(
        createElement(TeamsList, {
          rows: [{ team: 'platform', repositories: 4, unavailable: 0, actors: 6, labels: {} }],
          weeks: 4,
        }),
      ),
      people({ team: 'platform', repositories: [], actors: [], unavailable: 0, labels: {} }),
    ].join('');
    const prose = rendered.replace(/href="[^"]*"/g, '');

    expect(prose).not.toMatch(/actor/i);
    expect(prose).toContain('Contributor');
    // The links the prose was measured without are still spelled the service's way.
    expect(rendered).toContain('href="/#actors"');
  });
});
