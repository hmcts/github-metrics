/**
 * One team's page: which span it reads at, what it counts, and what it refuses to combine.
 *
 * The rule this page is built around is that a team is COUNTED and never graded. There is no team
 * label, no team score and no comparison against another team anywhere on it — the reversal of
 * 2026-09-01 permitted per-team counts for display and nothing beyond them — so what is asserted
 * here is the label distribution as counts, the two headline counts beside them, and the absence of
 * any verdict about the team itself. The rest is the join no component below can see: the span the
 * team was fetched at is the span its repository and contributor links carry.
 *
 * Stubbed as `repository-page.test.ts` and `contributor-page.test.ts` stub theirs: `next/headers`
 * for the cookie, `next/navigation` for the router the week selector owns and for `notFound`, and
 * `fetch` for the service.
 */

import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import TeamPage, { dynamic } from '@/app/teams/[team]/page';
import type { RepositoryRow, TeamActorRow, TeamDetail, WindowOptions } from '@/lib/types';

const WINDOWS: WindowOptions = {
  options: [4, 12, 26],
  default: 4,
  trend_periods: 8,
  collection_stale: false,
};

/** The `weeks` cookie this render sees, which a test sets to reach the second resolution step. */
let cookie: string | undefined;

vi.mock('next/headers', () => ({
  cookies: () =>
    Promise.resolve({ get: () => (cookie === undefined ? undefined : { value: cookie }) }),
}));

vi.mock('next/navigation', () => ({
  notFound: () => {
    throw new Error('notFound');
  },
  useRouter: () => ({ replace: () => undefined }),
  usePathname: () => '/teams/platform',
  useSearchParams: () => new URLSearchParams(),
}));

const REPOSITORIES: RepositoryRow[] = [
  { repository: 'api', team: 'platform', readiness: 'green' },
  { repository: 'web', team: 'platform', readiness: 'amber' },
];

const ACTORS: TeamActorRow[] = [
  { login: 'ada', repositories: 2, contributions: 9 },
  { login: 'grace', repositories: 1, contributions: 3 },
];

/** One team, full in every block the page draws, before any narrowing. */
function team(): TeamDetail {
  return {
    team: 'platform',
    repositories: REPOSITORIES,
    actors: ACTORS,
    unavailable: 0,
    labels: { green: 1, amber: 1 },
  };
}

/** Every path the stubbed service was asked for, in the order the page asked for them. */
let requested: string[] = [];

/**
 * Answer `/windows` and `/teams/<team>` from the fixtures, at whatever the case is about.
 *
 * `status` is how a refusal is chosen: 404 for an identifier the configuration does not hold, and
 * anything else for a fault the page must surface rather than dress up as a team nobody configured.
 */
function stubService(
  change: (detail: TeamDetail) => TeamDetail = (detail) => detail,
  status = 200,
): void {
  requested = [];
  const detail = change(team());
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string) => {
      requested.push(url);
      if (url.includes('/windows')) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(WINDOWS) });
      }
      if (status !== 200) {
        return Promise.resolve({
          ok: false,
          status,
          text: () => Promise.resolve('{"detail":"no team platform is configured"}'),
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(detail) });
    }),
  );
}

/** The span the team was read at, which `/windows` itself does not take. */
function span(): string | null {
  const url = requested.find((each) => !each.includes('/windows'));
  return url === undefined ? 'none' : new URL(url).searchParams.get('weeks');
}

async function render(weeks?: string): Promise<string> {
  const page = await TeamPage({
    params: Promise.resolve({ team: 'platform' }),
    searchParams: Promise.resolve(weeks === undefined ? {} : { weeks }),
  });
  return renderToStaticMarkup(page);
}

beforeEach(() => {
  cookie = undefined;
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('the team page', () => {
  it('names the team and counts what it holds and who worked in it', async () => {
    stubService();
    const markup = await render();

    expect(markup).toContain('>platform<');
    expect(markup).toContain('team');
    expect(markup).toContain('2 repositories');
    expect(markup).toContain('2 contributors');
  });

  it('reads the team at the span asked for, and links at the same one', async () => {
    stubService();
    const markup = await render('26');

    expect(span()).toBe('26');
    expect(markup).toContain('href="/repositories/api?weeks=26"');
    expect(markup).toContain('href="/contributors/ada?weeks=26"');
  });

  it('falls back to the reader’s cookie, and then to the service default', async () => {
    cookie = '12';
    stubService();
    expect(await render()).toContain('href="/repositories/api?weeks=12"');
    expect(span()).toBe('12');

    cookie = undefined;
    stubService();
    expect(await render()).toContain('href="/repositories/api?weeks=4"');
    expect(span()).toBe('4');
  });

  /**
   * The unreported count, which is what makes the other two readable.
   *
   * Six repositories with two unreported is a different window from six with none, and the label
   * counts under it only add up to the reported ones.
   */
  it('says how much of the team the span could not report, and nothing where it reported all', async () => {
    stubService((detail) => ({ ...detail, unavailable: 2 }));
    expect(await render()).toContain('2 repositories not reported at this span');

    stubService();
    expect(await render()).not.toContain('not reported at this span');
  });

  it('states the readiness labels as a count each, and grades the team with none of them', async () => {
    stubService();
    const markup = await render();

    expect(markup).toContain('Readiness labels');
    expect(markup).toContain('not combined into a label for the team');
    // The header states the team and its counts, and carries no label of its own: a readiness word
    // beside the team's name would be exactly the combined grade the donut's tooltip disclaims.
    const [header] = markup.split('</header>');
    expect(header).toContain('platform');
    expect(header).not.toMatch(/Ready|Caution|Blocked|Not assessed|score|average/i);
  });

  it('says so where the configuration holds no repository for the team', async () => {
    stubService((detail) => ({ ...detail, repositories: [], labels: {} }));
    const markup = await render();

    expect(markup).toContain('No repository is configured for platform.');
    expect(markup).toContain('Add repositories to this team');
    expect(markup).toContain('0 repositories');
  });

  it('says so where nobody authored a reported merge in the team’s repositories', async () => {
    stubService((detail) => ({ ...detail, actors: [] }));
    const markup = await render();

    expect(markup).toContain('Nobody authored a reported merge in platform’s repositories');
    expect(markup).toContain('0 contributors');
  });

  it('answers an identifier the configuration does not hold as not found', async () => {
    stubService((detail) => detail, 404);
    await expect(render()).rejects.toThrow('notFound');
  });

  it('lets every other refusal surface as the fault it is', async () => {
    stubService((detail) => detail, 503);
    await expect(render()).rejects.toThrow('API 503');
  });

  it('is dynamic, so a page is never served at another reader’s span', () => {
    expect(dynamic).toBe('force-dynamic');
  });
});
