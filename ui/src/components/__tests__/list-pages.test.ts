/**
 * The three list routes' own wiring: which span they fetch at, and which span their links carry.
 *
 * The estate was one page until 2026-09-02 and is `/repositories`, `/contributors` and `/teams`
 * now, each resolving the span for itself and each handing it to its own table. That resolution is
 * the same three lines three times, and a page that fetched at `windows.default` while linking at
 * the resolved span — or the reverse — type-checks perfectly and reads as a window that changes
 * when a reader follows a link. Nothing below the page can see it: `resolveWeeks` is tested on its
 * own inputs and the tables are tested on the `weeks` they are handed, so the join between them is
 * only visible from here.
 *
 * The pages are async server components reading cookies and the service, so `next/headers` and
 * `fetch` are stubbed and the awaited tree is handed to `renderToStaticMarkup`, exactly as
 * `repository-page.test.ts` does it.
 */

import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import ContributorsPage from '@/app/contributors/page';
import RepositoriesPage from '@/app/repositories/page';
import TeamsPage from '@/app/teams/page';
import type {
  ActorRow,
  OverviewSummary,
  RepositoryRow,
  TeamRow,
  WindowOptions,
} from '@/lib/types';

const WINDOWS: WindowOptions = {
  options: [4, 12, 26],
  default: 4,
  trend_periods: 8,
  collection_stale: false,
};

const OVERVIEW: OverviewSummary = {
  organization: 'hmcts',
  weeks: 12,
  starts_at: '2026-06-08T00:00:00Z',
  ends_at: '2026-08-31T00:00:00Z',
  built_at: '2026-08-31T01:00:00Z',
  collected_through: '2026-08-31T00:00:00Z',
  repositories: 2,
  unavailable: 0,
  teams: 1,
  actors: 1,
  merged_pull_requests: 9,
  direct_commits: 1,
  labels: { green: 1, amber: 1 },
};

const REPOSITORIES: RepositoryRow[] = [{ repository: 'api', team: 'platform', readiness: 'green' }];

const ACTORS: ActorRow[] = [{ login: 'ada', repositories: 2, labels: ['green'] }];

const TEAMS: TeamRow[] = [
  { team: 'platform', repositories: 2, unavailable: 0, actors: 1, labels: { green: 1 } },
];

/** Every path the stubbed service was asked for, in the order the pages asked for them. */
let requested: string[] = [];

vi.mock('next/headers', () => ({
  cookies: () => ({ get: () => undefined }),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: () => undefined }),
  usePathname: () => '/repositories',
  useSearchParams: () => new URLSearchParams(),
}));

/** Answer each list endpoint from the fixtures above, recording the path it was asked for. */
function stubService(): void {
  requested = [];
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string) => {
      requested.push(url);
      const body = (() => {
        if (url.includes('/windows')) return WINDOWS;
        if (url.includes('/overview')) return OVERVIEW;
        if (url.includes('/repositories')) return REPOSITORIES;
        if (url.includes('/actors')) return ACTORS;
        return TEAMS;
      })();
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
    }),
  );
}

/** The spans the data endpoints were asked at, which `/windows` itself does not take. */
function spans(): string[] {
  return requested
    .filter((url) => !url.includes('/windows'))
    .map((url) => new URL(url).searchParams.get('weeks') ?? 'none');
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('the three list routes', () => {
  it('fetches the repositories list at the span asked for, and links at the same one', async () => {
    stubService();
    const markup = renderToStaticMarkup(await RepositoriesPage({ searchParams: { weeks: '26' } }));

    expect(spans()).toEqual(['26', '26']);
    expect(markup).toContain('href="/repositories/api?weeks=26"');
    expect(markup).toContain('href="/teams/platform?weeks=26"');
  });

  it('fetches the contributors list at the span asked for, and links at the same one', async () => {
    stubService();
    const markup = renderToStaticMarkup(await ContributorsPage({ searchParams: { weeks: '26' } }));

    expect(spans()).toEqual(['26', '26']);
    expect(markup).toContain('href="/contributors/ada?weeks=26"');
  });

  it('fetches the teams list at the span asked for, and links at the same one', async () => {
    stubService();
    const markup = renderToStaticMarkup(await TeamsPage({ searchParams: { weeks: '26' } }));

    expect(spans()).toEqual(['26', '26']);
    expect(markup).toContain('href="/teams/platform?weeks=26"');
  });

  /**
   * A page asked for no span falls back to the service's default, and links there too.
   *
   * This is the case a nav link arrives in: the links carry no parameter, so the span comes from the
   * cookie and then from `/windows`. A page hard-coding a span, or one linking at the span it was
   * last rendered at, reads identically until the default moves.
   */
  it('falls back to the service default where no span was asked for', async () => {
    stubService();
    const markup = renderToStaticMarkup(await RepositoriesPage({ searchParams: {} }));

    expect(spans()).toEqual(['4', '4']);
    expect(markup).toContain('href="/repositories/api?weeks=4"');
  });

  /** A span off the list is not a span: `/windows` says what is on offer and the page keeps to it. */
  it('ignores a span the service does not offer', async () => {
    stubService();
    renderToStaticMarkup(await ContributorsPage({ searchParams: { weeks: '99' } }));

    expect(spans()).toEqual(['4', '4']);
  });

  it('says so rather than drawing an empty table where a list came back empty', async () => {
    requested = [];
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string) =>
        Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(url.includes('/windows') ? WINDOWS : url.includes('/overview') ? OVERVIEW : []),
        }),
      ),
    );
    const markup = renderToStaticMarkup(await TeamsPage({ searchParams: {} }));

    expect(markup).toContain('No team is configured for this organisation.');
  });
});
