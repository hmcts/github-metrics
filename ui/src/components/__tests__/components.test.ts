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
 * The one interactive state asserted here is the week selector's pending group, and only because
 * `WeekSpanButtons` takes it as a prop: `useTransition` reports `false` under `react-dom/server`, so
 * what a waiting reader sees is unreachable through the component that owns the router.
 *
 * JSX is written as `createElement` calls so the tests stay `.ts` files alongside the library tests.
 */

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import LoadingContributor from '@/app/contributors/[login]/loading';
import LoadingContributors from '@/app/contributors/loading';
import LoadingRepository from '@/app/repositories/[repository]/loading';
import LoadingRepositories from '@/app/repositories/loading';
import LoadingTeam from '@/app/teams/[team]/loading';
import LoadingTeams from '@/app/teams/loading';
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
import { WeekSpanButtons } from '@/components/NavWeekSelector';
import { OrganisationHeader } from '@/components/OrganisationHeader';
import { ProductionBadge } from '@/components/ProductionBadge';
import { RAGCard, RAGLabel, RAGRow } from '@/components/RAGCard';
import { Section } from '@/components/Section';
import { SortHeader } from '@/components/SortHeader';
import { TeamActorsTable } from '@/components/TeamActorsTable';
import { TeamsList } from '@/components/TeamsList';
import { PRODUCTION_BADGE, PRODUCTION_HEX, PRODUCTION_LABEL } from '@/lib/production';
import { RAG_BORDER } from '@/lib/rag';
import { people } from '@/lib/team';
import { TONES, TONE_BORDER, TONE_VALUE } from '@/lib/tone';
import type { OverviewSummary, PracticeFinding, ReadinessAssessment } from '@/lib/types';

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

describe('ProductionBadge', () => {
  it('states the attribute as a word in the configured palette', () => {
    const markup = renderToStaticMarkup(createElement(ProductionBadge, { production: true }));
    expect(markup).toContain(PRODUCTION_LABEL);
    expect(markup).toContain(PRODUCTION_BADGE);
  });

  it('reads as a label rather than a chip: no dismiss control anywhere in it', () => {
    const markup = renderToStaticMarkup(createElement(ProductionBadge, { production: true }));
    expect(markup).not.toContain('button');
    expect(markup).not.toContain('aria-label');
    expect(markup).not.toContain('>×<');
    expect(markup).not.toContain('Remove');
  });

  it('wears the same span as RAGLabel, so a header carrying both reads as one row', () => {
    const production = renderToStaticMarkup(createElement(ProductionBadge, { production: true }));
    const rag = renderToStaticMarkup(createElement(RAGLabel, { label: 'green' }));
    const shape = 'inline-block rounded px-1.5 py-0.5 text-xs uppercase tracking-wide whitespace-nowrap';
    expect(production).toContain(shape);
    expect(rag).toContain(shape);
  });

  // There is no non-production badge, so the two answers the field keeps apart — "the list does not
  // name it" and "no list could be read" — deliberately look identical here.
  it('renders nothing at all for a false answer and for an absent one alike', () => {
    expect(renderToStaticMarkup(createElement(ProductionBadge, { production: false }))).toBe('');
    expect(renderToStaticMarkup(createElement(ProductionBadge, {}))).toBe('');
  });

  it('spends no hex of its own: the colour is the class from lib/production.ts', () => {
    const markup = renderToStaticMarkup(createElement(ProductionBadge, { production: true }));
    expect(markup).not.toContain(PRODUCTION_HEX);
    expect(markup).not.toContain('#');
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

  it('badges a production repository directly after its label', () => {
    const markup = renderToStaticMarkup(
      createElement(EntityHeader, {
        kind: 'repository',
        name: 'hmcts/api-service',
        label: 'green',
        production: true,
      }),
    );
    expect(markup).toContain(PRODUCTION_BADGE);
    expect(markup.indexOf('Ready')).toBeLessThan(markup.indexOf(PRODUCTION_LABEL));
  });

  it('badges nothing where the repository is not one, or where no list was read', () => {
    for (const production of [false, undefined]) {
      const markup = renderToStaticMarkup(
        createElement(EntityHeader, { kind: 'repository', name: 'api', label: 'green', production }),
      );
      expect(markup).not.toContain(PRODUCTION_BADGE);
      expect(markup).not.toContain(PRODUCTION_LABEL);
    }
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

describe('OrganisationHeader', () => {
  /** An overview with every repository reported, which is the header at its shortest. */
  const OVERVIEW: OverviewSummary = {
    organization: 'hmcts',
    weeks: 4,
    starts_at: '2026-08-03T00:00:00Z',
    ends_at: '2026-08-31T00:00:00Z',
    built_at: '2026-09-01T09:00:00Z',
    collected_through: '2026-08-31T00:00:00Z',
    repositories: 12,
    unavailable: 0,
    teams: 3,
    actors: 9,
    merged_pull_requests: 40,
    direct_commits: 2,
    labels: {},
  };

  function header(overview: OverviewSummary): string {
    return renderToStaticMarkup(
      createElement(OrganisationHeader, {
        overview,
        action: createElement('span', null, 'selector'),
      }),
    );
  }

  it('names the organisation and states what the span covers, with its own control', () => {
    const markup = header(OVERVIEW);
    expect(markup).toContain('organization');
    expect(markup).toContain('font-mono');
    expect(markup).toContain('hmcts');
    expect(markup).toContain('4 weeks');
    expect(markup).toContain('Report built');
    expect(markup).toContain('selector');
  });

  // The three list routes render this one block, so what it claims about a span is one claim.
  it('says how many repositories the span could not report, and nothing when all could', () => {
    expect(header({ ...OVERVIEW, unavailable: 2 })).toContain(
      '2 repositories not reported at this span',
    );
    expect(header(OVERVIEW)).not.toContain('not reported at this span');
  });

  it('names no collection where nothing has been collected', () => {
    const markup = header({ ...OVERVIEW, collected_through: undefined });
    expect(markup).not.toContain('Collected');
    expect(markup).toContain('Report built');
  });

  it('leaves the control slot out where the page handed it none', () => {
    // `action` is optional, and an empty wrapper pushed to the right of the organisation name would
    // be an invisible box holding the row open for a control that was never passed.
    const markup = renderToStaticMarkup(createElement(OrganisationHeader, { overview: OVERVIEW }));
    expect(markup).toContain('hmcts');
    expect(markup).not.toContain('ml-auto');
  });
});

describe('Navigation', () => {
  /**
   * Order, not presence. Three links whose labels all render is what the site had before
   * 2026-09-02; what the instruction asked for is that they read down the estate — a repository,
   * the team that owns it, the people who work in it — so the assertion is on their positions.
   */
  it('lists the three estate links in estate order', () => {
    const markup = renderToStaticMarkup(createElement(Navigation));

    const order = ['Repositories', 'Teams', 'Contributors'].map((label) =>
      markup.indexOf(`>${label}</a>`),
    );

    expect(order).not.toContain(-1);
    expect(order).toStrictEqual([...order].sort((first, second) => first - second));
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
        align: 'right',
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

  it('centres a column that asked for the middle', () => {
    const markup = renderToStaticMarkup(
      createElement(SortHeader, {
        label: 'CODEOWNERS',
        active: false,
        direction: 'ascending',
        onSort: () => undefined,
        align: 'center',
      }),
    );
    expect(markup).toContain('text-center');
  });

  /**
   * The gap between two titles, which is what put this prop here: CODEOWNERS and Sonar rendered
   * flush against each other, reading as one word. The padding matches the body cells' own, so a
   * title still sits over its column.
   */
  it('pads every header on the right, and the first column on the left as its cells are', () => {
    function header(first: boolean): string {
      return renderToStaticMarkup(
        createElement(SortHeader, {
          label: 'Team',
          active: false,
          direction: 'ascending',
          onSort: () => undefined,
          first,
        }),
      );
    }

    expect(header(true)).toContain('pr-3');
    expect(header(true)).toContain('pl-3');
    // Only the leading column takes the inset: an interior one would be pushed off its column.
    expect(header(false)).toContain('pr-3');
    expect(header(false)).not.toContain('pl-3');
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

describe('WeekSpanButtons', () => {
  /** The group at rest: 4 weeks is the span the page was rendered at, and nothing is in flight. */
  function settled(): string {
    return renderToStaticMarkup(
      createElement(WeekSpanButtons, {
        options: [4, 12],
        current: 4,
        pending: false,
        onChoose: () => undefined,
      }),
    );
  }

  /** The same group after 12 weeks was pressed, with the server render still on its way. */
  function waiting(): string {
    return renderToStaticMarkup(
      createElement(WeekSpanButtons, {
        options: [4, 12],
        current: 12,
        pending: true,
        onChoose: () => undefined,
      }),
    );
  }

  it('presses the span the page is at, and claims nothing is loading', () => {
    const markup = settled();
    expect(markup).toContain('aria-busy="false"');
    expect(markup).not.toContain('opacity-50');
    expect(markup).toContain(
      'aria-label="4 week window" class="px-3 py-1.5 rounded text-sm transition-colors bg-indigo-600',
    );
  });

  // The pressed highlight says which button was clicked; this says the page behind it has not
  // arrived. On a cold span in the service that wait is seconds long, and a highlight on its own
  // over the previous span's figures reads as a window switch that silently did nothing.
  it('dims the group and reports itself busy while the new span is in flight', () => {
    const markup = waiting();
    expect(markup).toContain('aria-busy="true"');
    expect(markup).toContain('opacity-50');
    expect(markup).toContain('transition-opacity');
  });

  it('leaves the buttons live while waiting, so a reader can change their mind', () => {
    expect(waiting()).not.toContain('disabled');
  });

  it('differs from the settled group in the busy state alone', () => {
    const dimmed = waiting();
    const restored = dimmed
      .replace(' opacity-50', '')
      .replace('aria-busy="true"', 'aria-busy="false"');
    // Both render 12 as the pressed span, so what is left to differ is the dimming and nothing else.
    expect(restored).toEqual(
      renderToStaticMarkup(
        createElement(WeekSpanButtons, {
          options: [4, 12],
          current: 12,
          pending: false,
          onChoose: () => undefined,
        }),
      ),
    );
  });
});

describe('the loading skeletons', () => {
  /**
   * The landing page's skeleton, which is the widest of the six: header, cards, chart and a table.
   *
   * A `loading.tsx` is handed no props by Next.js, so rendering it with none is the whole contract —
   * a skeleton that needed a figure to draw itself could not be drawn before the figures arrive.
   */
  it('draws the page it is standing in for, on the same panel, from no props at all', () => {
    const markup = renderToStaticMarkup(createElement(LoadingRepositories));
    expect(markup).toContain('<section class="bg-slate-900/40 border border-slate-800 rounded-lg">');
    expect(markup).toContain('border-b border-slate-800');
    expect(markup).toContain('animate-pulse');
  });

  it('says it is loading in words, and hides the bars from a screen reader', () => {
    const markup = renderToStaticMarkup(createElement(LoadingRepositories));
    expect(markup).toContain('<p role="status" class="sr-only">Loading</p>');
    expect(markup).toContain('aria-hidden="true"');
  });

  it('lays a card row out across the viewport rather than compiling its columns away', () => {
    // Tailwind reads class names out of the source, so an interpolated `lg:grid-cols-${n}` would
    // reach the browser as a class no stylesheet defines and every card would stack.
    expect(renderToStaticMarkup(createElement(LoadingRepositories))).toContain('lg:grid-cols-4');
  });

  /**
   * A bone per donut, and the row that wraps them — six on the landing page, one on a team's.
   *
   * The count is the whole point of the shape: a single bone where six charts are about to land
   * leaves the table below it jumping down the page as they arrive, which is exactly what the
   * loading boundary exists to prevent. The wider grid is asserted with it, because a row of six
   * drawn one-up at every width is not the page it is standing in for either.
   */
  it('draws a bone per donut the page is about to hold, in the grid that page uses', () => {
    const markup = renderToStaticMarkup(createElement(LoadingRepositories));

    expect(markup.match(/h-48 w-full/g)).toHaveLength(6);
    expect(markup).toContain('grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4');

    // A team's page opens on one donut, and one keeps the narrower row it always had.
    const team = renderToStaticMarkup(createElement(LoadingTeam));
    expect(team.match(/h-48 w-full/g)).toHaveLength(1);
    expect(team).toContain('grid grid-cols-1 lg:grid-cols-3 gap-4');
  });

  /**
   * The list routes' skeleton, which the other two of the three lists share.
   *
   * Rendered here as well as the landing page's, because `SkeletonList` is a second shape and not a
   * narrowing of the first: a page whose `loading.tsx` announced nothing would leave a screen reader
   * with silence for the seconds a cold bundle takes, and the announcement is markup no type checks.
   */
  it('announces a list route as loading, and draws a bar per row it expects', () => {
    const contributors = renderToStaticMarkup(createElement(LoadingContributors));
    const teams = renderToStaticMarkup(createElement(LoadingTeams));

    expect(contributors).toContain('<p role="status" class="sr-only">Loading</p>');
    expect(teams).toContain('<p role="status" class="sr-only">Loading</p>');
    // A row each, and the two pages ask for different counts: twelve people against six teams.
    expect(contributors.match(/h-4 w-full/g)).toHaveLength(12);
    expect(teams.match(/h-4 w-full/g)).toHaveLength(6);
  });

  /**
   * The three detail routes' skeletons, which are three shapes rather than one.
   *
   * Each stands in for a different page — a repository's cohort row and evidence blocks, a team's
   * donut above two tables, a contributor's repositories above one behaviour section — so a
   * skeleton copied from the wrong route would draw bones the page then does not fill, and the
   * layout would jump as the figures land. That is exactly what no type check can see.
   */
  it('draws each detail route’s own shape, announced as loading like the lists are', () => {
    const repository = renderToStaticMarkup(createElement(LoadingRepository));
    const team = renderToStaticMarkup(createElement(LoadingTeam));
    const contributor = renderToStaticMarkup(createElement(LoadingContributor));

    for (const markup of [repository, team, contributor]) {
      expect(markup).toContain('<p role="status" class="sr-only">Loading</p>');
      expect(markup).toContain('aria-hidden="true"');
      expect(markup).toContain('animate-pulse');
    }

    // The repository page opens on the cohort row, which is the only card row of the three.
    expect(repository).toContain('lg:grid-cols-4');
    expect(team).not.toContain('lg:grid-cols-4');
    expect(contributor).not.toContain('lg:grid-cols-4');

    // The team page is the only one of the three that opens on a chart.
    expect(team).toContain('h-48 w-full');
    expect(repository).not.toContain('h-48 w-full');
    expect(contributor).not.toContain('h-48 w-full');

    // And each asks for the rows its own page holds: three sections, two, and two.
    expect(repository.match(/border-b border-slate-800/g)).toHaveLength(3);
    expect(team.match(/border-b border-slate-800/g)).toHaveLength(2);
    expect(contributor.match(/border-b border-slate-800/g)).toHaveLength(2);
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
      renderToStaticMarkup(createElement(ProductionBadge, { production: true })),
    ].join('');
    expect(markup).not.toMatch(/\p{Extended_Pictographic}/u);
  });

  /**
   * The word for a person is CONTRIBUTOR, from 2026-09-02 at the user's instruction.
   *
   * Every component that names people at all is rendered here together, because the rename is only
   * true if it is true everywhere: one heading still reading "Actors" is the whole point of the
   * change missed. The contract's own field names keep the old spelling deliberately, so what is
   * asserted is the prose the reader meets rather than the JSON behind it.
   */
  it('calls a person a contributor everywhere the reader can see one', () => {
    const prose = naming().replace(/href="[^"]*"/g, '');

    expect(prose).not.toMatch(/actor/i);
    expect(prose).toContain('Contributor');
  });

  /**
   * The routes followed the word later the same day: a person's page is `/contributors/[login]`.
   *
   * Rendered together for the same reason the prose is. A link left at `/actors/…` would 404 rather
   * than misread, and it would do it from whichever one table nobody thought to change — the
   * findings table, say, which is the one place a link to a person is not in a list of people.
   */
  it('links a person at the route their page is served from', () => {
    const rendered = naming();

    expect(rendered).not.toContain('/actors/');
    expect(rendered).not.toContain('#actors');
    expect(rendered).toContain('href="/contributors"');
  });

  /** Every component that names or links a person, rendered as one string. */
  function naming(): string {
    return [
      renderToStaticMarkup(createElement(Navigation)),
      renderToStaticMarkup(createElement(EntityHeader, { kind: 'contributor', name: 'octocat' })),
      renderToStaticMarkup(
        createElement(FindingsTable, { findings: [FINDING], weeks: 4 }),
      ),
      renderToStaticMarkup(
        createElement(ContributorsTable, {
          rows: [{ login: 'octocat', contributions: 1, blocking: 0, metrics: [] }],
          weeks: 4,
        }),
      ),
      renderToStaticMarkup(
        createElement(ActorsTable, {
          // Labelled, so the readiness column's badges are swept for the word and the route too: a
          // cell that renders through `RAGLabel` is the one part of this list that is not a login.
          rows: [{ login: 'octocat', repositories: 1, labels: ['green'] }],
          weeks: 4,
          labelled: true,
        }),
      ),
      renderToStaticMarkup(
        createElement(TeamActorsTable, {
          rows: [{ login: 'octocat', repositories: 1, contributions: 1 }],
          weeks: 4,
        }),
      ),
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
  }
});

/** One finding, so the findings table renders the row that links the person a rule fired on. */
const FINDING: PracticeFinding = {
  rule: 'unreviewed-merge',
  severity: 'high',
  actor_login: 'octocat',
  occurrences: 1,
  authored_merges: 1,
  percentage: 100,
  message: 'merged without an independent review',
  occurrences_by_size: {},
  pull_requests: [],
};
