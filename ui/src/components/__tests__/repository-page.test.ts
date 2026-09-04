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
import { PRODUCTION_BADGE } from '@/lib/production';
import type {
  ContributorRow,
  PracticeFinding,
  RepositoryDetail,
  RepositoryPracticeEvidence,
  RepositoryTrend,
  TrendWindow,
  WindowOptions,
} from '@/lib/types';

vi.mock('next/headers', () => ({
  cookies: () => Promise.resolve({ get: () => undefined }),
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
 * Render the page against a stubbed service answering with one whole `RepositoryDetail`.
 *
 * The page is an async server component, so it is awaited into an element tree and that tree — all
 * synchronous components — is what `renderToStaticMarkup` is handed.
 *
 * `series` is the trend, which is the one fetch on this page allowed to fail: `null` refuses it, and
 * every case that is not about the trend section refuses it so the charts stay out of the markup.
 */
async function renderDetail(
  detail: RepositoryDetail,
  series: RepositoryTrend | null = null,
): Promise<string> {
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string) => {
      if (url.includes('/trend')) {
        return series === null
          ? Promise.reject(new Error('no trend in this test'))
          : Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(series) });
      }
      const body = url.includes('/windows') ? WINDOWS : detail;
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
    }),
  );
  const page = await RepositoryPage({
    params: Promise.resolve({ repository: 'api' }),
    searchParams: Promise.resolve({}),
  });
  return renderToStaticMarkup(page);
}

/** Render the page at whatever evidence the case is about, on an otherwise full detail. */
async function render(
  change: (block: RepositoryPracticeEvidence) => RepositoryPracticeEvidence = (block) => block,
): Promise<string> {
  return renderDetail({
    repository: 'api',
    team: 'platform',
    evidence: change(evidence()),
    contributors: [],
  });
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

/**
 * The production badge in the header, which is the one thing here not read from the window.
 *
 * It rides on the header rather than on a section, so it is drawn before the page branches on
 * whether the span holds evidence: whether a repository deploys to production is not a fact about
 * the reporting window, and a repository the caches cannot report still is or is not one.
 */
describe('the repository header’s production badge', () => {
  it('badges a production repository beside its readiness label', async () => {
    const markup = await renderDetail({
      repository: 'api',
      team: 'platform',
      evidence: evidence(),
      contributors: [],
      production: true,
    });

    expect(markup).toContain(PRODUCTION_BADGE);
    expect(markup.indexOf('Caution')).toBeLessThan(markup.indexOf(PRODUCTION_BADGE));
  });

  it('badges it on a span with no evidence too, which the header is built above', async () => {
    const markup = await renderDetail({
      repository: 'api',
      team: 'platform',
      contributors: [],
      production: true,
    });

    expect(markup).toContain('This span holds no evidence for api.');
    expect(markup).toContain(PRODUCTION_BADGE);
  });

  it('badges nothing for a repository the list does not name', async () => {
    expect(await render()).not.toContain(PRODUCTION_BADGE);
  });
});

/**
 * What the page draws for a repository the span holds nothing for, and what it refuses to draw.
 *
 * The rule is that a configured repository keeps its page either way: a 404 for one would read as a
 * repository nobody has heard of, when what happened is that the caches do not cover this span.
 */
describe('a repository the span cannot be reported for', () => {
  /** The detail the service sends for a repository it could not report: no evidence, and a reason. */
  function unavailable(detail?: string): RepositoryDetail {
    return { repository: 'api', team: 'platform', contributors: [], detail };
  }

  it('keeps the page, states the span holds nothing, and passes the service’s reason on', async () => {
    const markup = await renderDetail(unavailable('the caches hold no window at 8 weeks'));

    expect(markup).toContain('This span holds no evidence for api.');
    expect(markup).toContain('the caches hold no window at 8 weeks');
    expect(markup).toContain('run metrics collect for the span being asked for');
    // The header is still there, with the team link and the selector, and carries no label.
    expect(markup).toContain('href="/teams/platform?weeks=8"');
    expect(markup).toContain('Reporting window');
    expect(markup).not.toContain('border-l-4');
  });

  it('says no reason was given rather than a blank where the service gave none', async () => {
    const markup = await renderDetail(unavailable());

    expect(markup).toContain('no reason was given —');
  });

  it('draws none of the evidence blocks, and asks for no trend it could not draw', async () => {
    const asked: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string) => {
        asked.push(url);
        const body = url.includes('/windows') ? WINDOWS : unavailable('nothing collected');
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
      }),
    );
    const markup = renderToStaticMarkup(
      await RepositoryPage({
        params: Promise.resolve({ repository: 'api' }),
        searchParams: Promise.resolve({}),
      }),
    );

    for (const block of ['Merges reported', 'Merge gate', 'SonarCloud', 'Findings']) {
      expect(markup).not.toContain(block);
    }
    // A series is cut from the caches per period, which is work worth doing only for a page that is
    // going to draw the rest of the block too.
    expect(asked.some((url) => url.includes('/trend'))).toBe(false);
  });

  /** Refuse the repository read with `status`, which is how the two refusals are told apart. */
  async function refuse(status: number): Promise<unknown> {
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string) =>
        url.includes('/windows')
          ? Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(WINDOWS) })
          : Promise.resolve({
              ok: false,
              status,
              text: () => Promise.resolve('{"detail":"no repository api is configured"}'),
            }),
      ),
    );
    return RepositoryPage({
      params: Promise.resolve({ repository: 'api' }),
      searchParams: Promise.resolve({}),
    });
  }

  it('answers a name the configuration does not hold as not found', async () => {
    await expect(refuse(404)).rejects.toThrow('notFound');
  });

  it('lets every other refusal surface as the fault it is', async () => {
    await expect(refuse(500)).rejects.toThrow('API 500');
  });
});

/**
 * The blocks that state an absence, and the two lists that state what they hold.
 *
 * Every one of these is a signal the collector could not read, and each has its own sentence: a
 * merge gate GitHub withheld, open pull-request state nobody collected, a security family that was
 * refused, a maintenance search that ran no window, a Sonar project with no measures. A single
 * "not available" across them would lose which question went unanswered, and a zero in place of any
 * of them would report a missing permission as a passing check.
 */
describe('the repository page’s absences and lists', () => {
  it('says which signal was not collected, one sentence each', async () => {
    const markup = await render((block) => ({
      ...block,
      open_pull_requests: { fetched_at: block.open_pull_requests.fetched_at, detail: 'the pull-request list was refused' },
      security: { fetched_at: block.security.fetched_at, detail: 'alerts need security-events scope' },
      maintenance: { fetched_at: block.maintenance.fetched_at, maintenance: block.maintenance.maintenance, windows: [] },
      // No `fetched_at` at all: a block this build never stored has no read-at stamp to print, and a
      // heading reading "read Invalid Date" would be worse than one that says only what it is.
      sonar: { detail: 'no Sonar project is mapped' },
    }));

    expect(markup).toContain('Open pull-request state was not collected for this repository.');
    expect(markup).toContain('the pull-request list was refused');
    expect(markup).toContain('No security alert family could be read for this repository.');
    expect(markup).toContain('alerts need security-events scope');
    expect(markup).toContain('No maintenance window was checked for this repository.');
    // The Sonar section keeps its gate card and draws no measures behind it, and its heading says
    // nothing about when a block that was never stored was read.
    expect(markup).toContain('SonarCloud');
    expect(markup).not.toContain('Lines of code');
    const [, sonar = ''] = markup.split('SonarCloud');
    expect(sonar.slice(0, 200)).not.toContain('read ');
    // No count anywhere claims one of them was zero.
    expect(markup).not.toContain('Opened in window');
  });

  it('draws the findings and the contributors it was sent, in the order the service sent them', async () => {
    const findings: PracticeFinding[] = [
      {
        rule: 'unreviewed-merge',
        severity: 'high',
        actor_login: 'ada',
        occurrences: 2,
        authored_merges: 6,
        percentage: 33.3,
        message: 'merged without an independent review',
        occurrences_by_size: { small: 2 },
        pull_requests: [
          { number: 41, url: 'https://github.com/hmcts/api/pull/41', merged_at: '2026-08-02T00:00:00Z', size_class: 'small' },
        ],
      },
    ];
    const contributors: ContributorRow[] = [
      { login: 'ada', contributions: 6, blocking: 2, metrics: [] },
      { login: 'grace', contributions: 3, blocking: 0, metrics: [] },
    ];
    const markup = await renderDetail({
      repository: 'api',
      team: 'platform',
      evidence: { ...evidence(), behaviour: findings },
      contributors,
    });

    expect(markup).toContain('unreviewed-merge');
    expect(markup).toContain('merged without an independent review');
    expect(markup).toContain('href="/contributors/ada?weeks=8"');
    expect(markup).toContain('href="/contributors/grace?weeks=8"');
    expect(markup.indexOf('>ada<')).toBeLessThan(markup.indexOf('>grace<'));
    expect(markup).not.toContain('No practice rule fired on this repository at this span.');
    expect(markup).not.toContain('Nobody authored a reported merge in this repository');
  });

  /**
   * The trend section, which appears only where the series the service answered with holds periods.
   *
   * The section is the one part of this page whose fetch is allowed to fail, and an empty series is
   * the same as a refused one as far as the page is concerned: there is nothing to plot, and a chart
   * of a single baseline window would read as a period that was measured.
   */
  it('draws the trend where the series holds periods, and nothing where it does not', async () => {
    const window: TrendWindow = {
      starts_at: '2026-06-01T00:00:00Z',
      ends_at: '2026-06-29T00:00:00Z',
      provenance: { offline: true, intervals_fetched: 0 },
      cohort: { merged: 10, reported: 10, excluded_authors: {}, direct_commits: 2 },
      throughput: { merges: 12, merged_pull_requests: 10, direct_commits: 2, active_contributors: 3 },
      metrics: [],
    };
    const series: RepositoryTrend = {
      repository: 'api',
      enablement_at: '2026-06-01T00:00:00Z',
      baseline: window,
      periods: [{ ...window, starts_at: '2026-06-29T00:00:00Z', ends_at: '2026-07-27T00:00:00Z', index: 1, deltas: [] }],
      alert_observations: [],
    };
    const detail: RepositoryDetail = {
      repository: 'api',
      team: 'platform',
      evidence: evidence(),
      contributors: [],
    };

    const drawn = await renderDetail(detail, series);
    expect(drawn).toContain('Trend');
    expect(drawn).toContain('1 whole period of 28 days since 2026-06-01');
    // Cut to the count `/windows` publishes, so a long-enabled repository is served a bounded series.
    expect(drawn).toContain('Merges by route');

    const empty = await renderDetail(detail, { ...series, periods: [] });
    expect(empty).not.toContain('Merges by route');
    // And the section is absent for a series the endpoint refused outright, which `render` stubs.
    expect(await render()).not.toContain('Merges by route');
  });
});
