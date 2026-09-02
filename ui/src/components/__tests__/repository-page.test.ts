/**
 * The repository page's own markup: what order its sections come in, and which of them are paired.
 *
 * Every other test here renders a component. This one renders the PAGE, because the thing under
 * test is the arrangement rather than any one section — a pair that quietly became two full-width
 * sections again, or a heading that drifted above the block it explains, is invisible to a component
 * test and to the type checker both.
 *
 * The page is a server component that reads cookies and the service, so three things are stubbed:
 * `next/headers` for the cookie, `next/navigation` for the router the week selector owns, and
 * `fetch` for the evidence. The trend request is left to fail, which the page already tolerates —
 * it keeps the chart's client-side rendering out of a static render.
 */

import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import RepositoryPage from '@/app/repositories/[repository]/page';
import type { RepositoryDetail, RepositoryPracticeEvidence, WindowOptions } from '@/lib/types';

vi.mock('next/headers', () => ({
  cookies: () => ({ get: () => undefined }),
}));

vi.mock('next/navigation', () => ({
  notFound: () => {
    throw new Error('notFound');
  },
  useRouter: () => ({ replace: () => undefined }),
  usePathname: () => '/repositories/api',
  useSearchParams: () => new URLSearchParams(),
}));

const WINDOWS: WindowOptions = {
  options: [4, 8, 12],
  default: 8,
  trend_periods: 8,
  collection_stale: false,
};

/** One repository's evidence, full in every block the page draws, before any narrowing. */
function evidence(): RepositoryPracticeEvidence {
  return {
    repository: 'api',
    team: 'platform',
    starts_at: '2026-07-06T00:00:00Z',
    ends_at: '2026-08-31T00:00:00Z',
    provenance: { offline: false, intervals_fetched: 2 },
    cohort: { merged: 12, reported: 9, excluded_authors: { 'dependabot[bot]': 3 }, direct_commits: 1 },
    assessment: {
      label: 'amber',
      blocking: [],
      caution: [{ condition: 'stale-open-pull-request', detail: 'one open pull request is stale' }],
      clear: [{ condition: 'codeowners', detail: 'a CODEOWNERS file is present' }],
    },
    merge_gate: {
      fetched_at: '2026-08-31T00:00:00Z',
      gate: {
        branch: 'main',
        protected: true,
        pull_requests: [
          {
            dismiss_stale_reviews_on_push: true,
            require_code_owner_review: true,
            require_last_push_approval: false,
            required_approving_review_count: 1,
            required_review_thread_resolution: true,
          },
        ],
        status_checks: [
          {
            strict_required_status_checks_policy: true,
            required_status_checks: [{ context: 'build' }],
          },
        ],
        restricts_deletions: true,
        blocks_force_pushes: true,
        applies_to_administrators: false,
        rules_observed: true,
        requires_linear_history: false,
        restricts_branch_names: false,
        unmodelled_rules: [],
      },
    },
    open_pull_requests: {
      fetched_at: '2026-08-31T00:00:00Z',
      summary: {
        opened_in_window: 14,
        closed_without_merge: 2,
        currently_open: 5,
        stale_open: 1,
      },
    },
    security: {
      fetched_at: '2026-08-31T00:00:00Z',
      alerts: {
        dependabot: { open: 3, by_severity: { high: 1, low: 2 } },
        code_scanning: { open: 0, by_severity: {} },
        secret_scanning: { open: 0, by_severity: {} },
      },
    },
    codeowners: {
      fetched_at: '2026-08-31T00:00:00Z',
      codeowners: {
        files: [{ path: '.github/CODEOWNERS', size_bytes: 120, recognised_by_github: true }],
      },
    },
    maintenance: {
      fetched_at: '2026-08-31T00:00:00Z',
      maintenance: { branch: 'main', last_commit_at: '2026-08-30T00:00:00Z' },
      windows: [{ months: 3, committed_within: true, human_committed_within: true }],
    },
    sonar: {
      fetched_at: '2026-08-31T00:00:00Z',
      measures: { project_key: 'hmcts_api', coverage: 74.2, lines_of_code: 18400 },
    },
    metrics: [
      {
        metric: 'independent-review-coverage',
        summary: { status: 'observed', numerator: 8, denominator: 9 },
        classifications: { independent: 8, none: 1 },
      },
    ],
    behaviour: [],
  };
}

/**
 * Render the page against a stubbed service, at whatever evidence the case is about.
 *
 * The page is an async server component, so it is awaited into an element tree and that tree — all
 * synchronous components — is what `renderToStaticMarkup` is handed.
 */
async function render(
  change: (block: RepositoryPracticeEvidence) => RepositoryPracticeEvidence = (block) => block,
): Promise<string> {
  const detail: RepositoryDetail = {
    repository: 'api',
    team: 'platform',
    evidence: change(evidence()),
    contributors: [],
  };
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string) => {
      if (url.includes('/trend')) {
        return Promise.reject(new Error('no trend in this test'));
      }
      const body = url.includes('/windows') ? WINDOWS : detail;
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
    }),
  );
  const page = await RepositoryPage({ params: { repository: 'api' }, searchParams: {} });
  return renderToStaticMarkup(page);
}

/** The pair wrapper's classes, as one string, so a test names the layout it is asserting. */
const PAIR = 'class="grid grid-cols-1 lg:grid-cols-2 gap-4"';

/**
 * Each paired block's markup, split at the wrapper that opens it.
 *
 * The last part runs on into the rest of the page, which no assertion here relies on: what each
 * case checks is that a pair's two headings are inside the same wrapper and that the other pair's
 * are not.
 */
function pairs(markup: string): string[] {
  const [, ...rest] = markup.split(`<div ${PAIR}>`);
  return rest;
}

/**
 * Where a section's own heading sits, rather than wherever the word first appears.
 *
 * `Caution` is also the readiness label the entity header badges an amber repository with, and that
 * badge is near the top of the page — a plain `indexOf` would report the assessment groups as
 * sitting above everything.
 */
function heading(markup: string, text: string): number {
  const at = markup.indexOf(`>${text}</h2>`);
  expect(at).not.toBe(-1);
  return at;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('repository page layout', () => {
  it('pairs the merge gate with open pull requests, and alerts with maintenance', async () => {
    const markup = await render();
    const [gate, security] = pairs(markup);

    expect(gate).toContain('Merge gate');
    expect(gate).toContain('Open pull requests');
    expect(gate).not.toContain('Security alerts');

    expect(security).toContain('Security alerts');
    expect(security).toContain('Maintenance');
  });

  it('stacks each pair to one column on a narrow viewport', async () => {
    const markup = await render();
    // Two pairs and no more: a third would mean a section was folded in without being asked for.
    expect(markup.split(`<div ${PAIR}>`)).toHaveLength(3);
    expect(markup).toContain('grid-cols-1 lg:grid-cols-2');
  });

  it('keeps the pair stretching, so an empty half lines up with a full one', async () => {
    const markup = await render((block) => ({
      ...block,
      merge_gate: { fetched_at: block.merge_gate.fetched_at, detail: 'the branch is unprotected' },
    }));
    const [gate] = pairs(markup);

    // The common case on this estate: an unreadable gate beside a full open pull-request block.
    expect(gate).toContain('The merge gate could not be read for this repository.');
    expect(gate).toContain('the branch is unprotected');
    expect(gate).toContain('Opened in window');
    // No alignment override on the wrapper: a grid item stretches, and both panels end level.
    expect(markup).not.toContain('lg:grid-cols-2 gap-4 items-start');
  });

  it('draws the four open pull-request counts two across inside their half', async () => {
    const markup = await render();
    const [gate] = pairs(markup);

    expect(gate).toContain('grid grid-cols-1 sm:grid-cols-2 gap-4');
    expect(gate).toContain('Opened in window');
    expect(gate).toContain('Currently open');
    expect(gate).toContain('Stale open');
  });

  it('keeps the cohort row four across, which has the full width to spend', async () => {
    const markup = await render();
    expect(markup).toContain('grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4');
    expect(markup).toContain('Merges reported');
  });

  it('reads down the page in the order a reader needs it', async () => {
    const markup = await render();
    const order = [
      'Merges reported',
      'Behaviour',
      'Blocking',
      'Merge gate',
      'Security alerts',
      'SonarCloud',
      'Findings',
      'Contributors',
    ];
    const positions = order.map((heading) => markup.indexOf(heading));
    expect(positions).not.toContain(-1);
    expect([...positions].sort((first, second) => first - second)).toEqual(positions);
  });

  it('puts behaviour above the blocking, caution and clear groups', async () => {
    const markup = await render();

    // Named apart from the order case above because this is the swap the page makes deliberately:
    // the measurements come before the verdict drawn from them, and the cohort row stays first.
    expect(markup.indexOf('Merges reported')).toBeLessThan(heading(markup, 'Behaviour'));
    for (const group of ['Blocking', 'Caution', 'Clear']) {
      expect(heading(markup, 'Behaviour')).toBeLessThan(heading(markup, group));
    }
  });

  it('keeps behaviour above the readiness empty state where nothing was graded', async () => {
    const markup = await render((block) => ({ ...block, assessment: undefined }));

    expect(markup).toContain('Readiness');
    expect(heading(markup, 'Behaviour')).toBeLessThan(heading(markup, 'Readiness'));
  });
});
