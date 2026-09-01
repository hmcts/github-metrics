/**
 * Markup checks for the repository page's own components.
 *
 * The page itself is not rendered here — it reads cookies and the service — so what these assert is
 * the part that carries a rule: an assessment that shows what CLEARED as well as what blocked, a
 * findings table with no way to order people by how often a rule fired on them, and a contributors
 * table that keeps the service's weightiest-first order rather than inventing one.
 */

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { AssessmentSection } from '@/components/AssessmentSection';
import { ContributorsTable } from '@/components/ContributorsTable';
import { FindingsTable } from '@/components/FindingsTable';
import { MetricsGrid } from '@/components/MetricsGrid';
import { RAG_BORDER } from '@/lib/rag';
import type { BehaviourMetricSummary, PracticeFinding, ReadinessAssessment } from '@/lib/types';

const ASSESSMENT: ReadinessAssessment = {
  label: 'red',
  blocking: [
    { condition: 'merge gate', label: 'red', detail: 'the default branch is unprotected' },
  ],
  caution: [],
  clear: [{ condition: 'codeowners', detail: 'a CODEOWNERS file is present' }],
};

describe('AssessmentSection', () => {
  const markup = renderToStaticMarkup(createElement(AssessmentSection, { assessment: ASSESSMENT }));

  it('shows what blocked, with the ceiling that condition imposed', () => {
    expect(markup).toContain('the default branch is unprotected');
    expect(markup).toContain(RAG_BORDER.red);
    expect(markup).toContain('Blocked');
  });

  it('shows what cleared too, so a grade can be argued with rather than only read', () => {
    expect(markup).toContain('a CODEOWNERS file is present');
  });

  it('grades nothing for a condition that imposes nothing', () => {
    expect(markup).not.toContain('Not assessed');
  });

  it('states what an empty group means instead of leaving a blank', () => {
    expect(markup).toContain('Nothing the policy checked was flagged for caution.');
  });
});

describe('MetricsGrid', () => {
  const summaries: BehaviourMetricSummary[] = [
    {
      metric: 'independent-review-coverage',
      summary: { status: 'observed', numerator: 78, denominator: 96 },
      classifications: { independent: 78, none: 18 },
    },
    {
      metric: 'merge-cycle-time',
      summary: {
        status: 'observed',
        sample_size: 96,
        unit: 'hours',
        median: 4.5,
        percentile_75: 9,
        percentile_90: 21,
      },
      classifications: {},
    },
  ];

  it('names each metric as the report names it, with its value and its population', () => {
    const markup = renderToStaticMarkup(createElement(MetricsGrid, { summaries, empty: 'none' }));
    expect(markup).toContain('independent-review-coverage');
    expect(markup).toContain('81.2%');
    expect(markup).toContain('78 of 96');
    expect(markup).toContain('4.5 hours');
    expect(markup).toContain('96 samples · p75 9 hours');
  });

  it('keeps the report’s order rather than ordering by any figure', () => {
    const markup = renderToStaticMarkup(createElement(MetricsGrid, { summaries, empty: 'none' }));
    expect(markup.indexOf('independent-review-coverage')).toBeLessThan(
      markup.indexOf('merge-cycle-time'),
    );
  });

  it('says why there are no metrics rather than drawing an empty grid', () => {
    const markup = renderToStaticMarkup(
      createElement(MetricsGrid, { summaries: [], empty: 'No metric was computed at this span.' }),
    );
    expect(markup).toContain('No metric was computed at this span.');
  });
});

describe('FindingsTable', () => {
  const findings: PracticeFinding[] = [
    {
      rule: 'unreviewed-merge',
      severity: 'high',
      actor_login: 'alice',
      occurrences: 6,
      authored_merges: 12,
      percentage: 50,
      message: 'merged without an independent review',
      occurrences_by_size: { large: 2, small: 4 },
      pull_requests: [
        { number: 41, url: 'https://github.com/hmcts/api/pull/41', merged_at: '2026-08-02T00:00:00Z', size_class: 'small' },
      ],
      direct_commits: [
        {
          sha: 'abcdef1234567890',
          url: 'https://github.com/hmcts/api/commit/abcdef1234567890',
          committed_at: '2026-08-03T00:00:00Z',
          size_class: 'small',
        },
      ],
    },
    {
      rule: 'unreviewed-merge',
      severity: 'low',
      actor_login: 'bob',
      occurrences: 9,
      authored_merges: 9,
      percentage: 100,
      message: 'merged without an independent review',
      occurrences_by_size: {},
      pull_requests: [],
    },
  ];

  const markup = renderToStaticMarkup(createElement(FindingsTable, { findings, weeks: 8 }));

  it('links the actor a rule fired on, carrying the span onto the drill-through', () => {
    expect(markup).toContain('/actors/alice?weeks=8');
    expect(markup).toContain('font-mono');
  });

  it('links every merge behind a finding, so the finding can be checked', () => {
    expect(markup).toContain('https://github.com/hmcts/api/pull/41');
    expect(markup).toContain('#41');
    expect(markup).toContain('https://github.com/hmcts/api/commit/abcdef1234567890');
    expect(markup).toContain('commit abcdef1');
  });

  it('states the occurrences against what was authored, as a share', () => {
    expect(markup).toContain('50%');
    expect(markup).toContain('100%');
  });

  it('offers no way to order the rows: a finding names a person', () => {
    expect(markup).not.toContain('aria-sort');
    expect(markup).not.toContain('<button');
    expect(markup.indexOf('alice')).toBeLessThan(markup.indexOf('bob'));
  });
});

describe('ContributorsTable', () => {
  const markup = renderToStaticMarkup(
    createElement(ContributorsTable, {
      rows: [
        { login: 'carol', contributions: 9, blocking: 2 },
        { login: 'alice', contributions: 3, blocking: 0 },
      ],
      weeks: 4,
    }),
  );

  it('keeps the service’s order, which is about the changes rather than the people', () => {
    expect(markup.indexOf('carol')).toBeLessThan(markup.indexOf('alice'));
    expect(markup).not.toContain('aria-sort');
  });

  it('links each login and re-reports what a rule already found there', () => {
    expect(markup).toContain('/actors/carol?weeks=4');
    expect(markup).toContain('Blocking occurrences');
  });

  it('contains no emoji: this page states figures in words and colour bars', () => {
    expect(markup).not.toMatch(/\p{Extended_Pictographic}/u);
  });
});
