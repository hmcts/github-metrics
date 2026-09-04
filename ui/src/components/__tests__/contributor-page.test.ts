/**
 * One contributor's page: which span it reads at, what it says about the person, and what it refuses.
 *
 * The parts below this page are already tested on their own inputs — `activity` and `measured` on an
 * `ActorReadiness`, `ActorRepositoriesTable` on its rows, `resolveWeeks` on a parameter and a cookie.
 * What only the page can be asked is how they are joined: that the span it fetched at is the span its
 * links carry, that a person with no reported merge at this span gets a sentence rather than an empty
 * table, that a behaviour section is drawn per repository rather than one averaged across them, and
 * that a login nobody in the window is spelled with becomes a not-found rather than a blank page.
 *
 * Stubbed exactly as `repository-page.test.ts` stubs its page: `next/headers` for the cookie,
 * `next/navigation` for the router the week selector owns and for `notFound`, and `fetch` for the
 * service. The page is an async server component, so it is awaited into an element tree and that
 * tree — all synchronous — is what `renderToStaticMarkup` is handed.
 */

import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ActorPage, { dynamic } from '@/app/contributors/[login]/page';
import { PRODUCTION_BADGE } from '@/lib/production';
import type { ActorDetail, ActorRepositoryReadiness, WindowOptions } from '@/lib/types';

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
  usePathname: () => '/contributors/Ada',
  useSearchParams: () => new URLSearchParams(),
}));

/** Two repositories one person worked in, one of them with a metric and one with none. */
const REPOSITORIES: ActorRepositoryReadiness[] = [
  {
    repository: 'api',
    readiness: 'amber',
    contributions: 6,
    blocking: 1,
    metrics: [
      {
        metric: 'independent-review-coverage',
        summary: { status: 'observed', numerator: 5, denominator: 6 },
        classifications: { independent: 5, none: 1 },
      },
    ],
  },
  { repository: 'web', readiness: 'green', contributions: 3, blocking: 0, metrics: [] },
];

/** Every path the stubbed service was asked for, in the order the page asked for them. */
let requested: string[] = [];

/**
 * Answer `/windows` and `/actors/<login>` from the fixtures, at whatever the case is about.
 *
 * `status` is how a refusal is chosen: 404 for a login the window does not hold, and anything else
 * for a service or network fault, which the page must not disguise as a person nobody has heard of.
 */
function stubService(
  repositories: ActorRepositoryReadiness[] = REPOSITORIES,
  status = 200,
  production?: string[],
): void {
  requested = [];
  const detail: ActorDetail = {
    actor: { actor_login: 'ada', repositories },
    teams: { api: 'platform', web: 'digital' },
    production,
  };
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
          text: () => Promise.resolve(`{"detail":"no actor ada at this span"}`),
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(detail) });
    }),
  );
}

/** The span the person was read at, which `/windows` itself does not take. */
function span(): string | null {
  const url = requested.find((each) => !each.includes('/windows'));
  return url === undefined ? 'none' : new URL(url).searchParams.get('weeks');
}

async function render(login = 'Ada', weeks?: string): Promise<string> {
  const page = await ActorPage({
    params: Promise.resolve({ login }),
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

describe('the contributor page', () => {
  it('names the person as the report spells them, not as the URL spelled them', async () => {
    stubService();
    const markup = await render('ADA');

    // The service matches a login case-insensitively; the page shows the spelling it answered with,
    // so two links to the same person read as one page about them.
    expect(markup).toContain('>ada<');
    expect(markup).toContain('contributor');
    expect(markup).toContain('9 merges across 2 repositories');
    // Case survives into the request: the service does the folding, not the page.
    expect(requested.some((url) => url.includes('/actors/ADA'))).toBe(true);
  });

  it('reads the person at the span asked for, and links at the same one', async () => {
    stubService();
    const markup = await render('ada', '26');

    expect(span()).toBe('26');
    expect(markup).toContain('href="/repositories/api?weeks=26"');
    expect(markup).toContain('href="/teams/platform?weeks=26"');
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
   * A behaviour section per repository, which is the rule this page is built around.
   *
   * Nothing is averaged across them: a rate measured over six merges in one repository and a rate
   * over three in another are two observations, and one figure combining them would be a per-person
   * score — which architecture.md's scope boundaries refuse.
   */
  it('draws one behaviour section per repository, in the contract’s order', async () => {
    stubService();
    const markup = await render();

    expect(markup).toContain('Behaviour in api');
    expect(markup).toContain('6 merges in this repository');
    expect(markup).toContain('Behaviour in web');
    expect(markup.indexOf('Behaviour in api')).toBeLessThan(markup.indexOf('Behaviour in web'));
    expect(markup).not.toMatch(/average|overall score|rank/i);
  });

  /**
   * The person's production list reaches the table, which is all this page does with it.
   *
   * Which repositories the list names is the service's answer and is tested there; what only the
   * page can be asked is that it hands the field on rather than dropping it — a table with the
   * column and no badges would look exactly like an estate with no production services.
   */
  it('passes the person’s production repositories into their table', async () => {
    stubService(REPOSITORIES, 200, ['web']);
    const markup = await render();

    expect(markup.split(PRODUCTION_BADGE)).toHaveLength(2);
    expect(markup.indexOf('/repositories/api')).toBeLessThan(markup.indexOf(PRODUCTION_BADGE));
  });

  it('badges nothing where the service could read no list at all', async () => {
    stubService();
    expect(await render()).not.toContain(PRODUCTION_BADGE);
  });

  it('says so where a repository observed no metric, rather than drawing an empty grid', async () => {
    stubService();
    const markup = await render();

    expect(markup).toContain('No behaviour metric could be measured over ada’s merges in web.');
  });

  it('says a person authored nothing at this span instead of drawing an empty table', async () => {
    stubService([]);
    const markup = await render();

    expect(markup).toContain('ada authored no reported merge at this span.');
    expect(markup).toContain('Read this person at a longer span');
    expect(markup).not.toContain('Contributions');
    // No repositories means no behaviour sections either: the sections are one per row.
    expect(markup).not.toContain('Behaviour in');
  });

  /**
   * A login the window does not hold has no page, and the service is what says so.
   *
   * Unlike a repository, a contributor is not configured anywhere — the set of them is whoever
   * authored a reported merge — so an invented empty page would claim a person exists on this estate.
   */
  it('answers a login nobody in this window is spelled with as not found', async () => {
    stubService(REPOSITORIES, 404);
    await expect(render('nobody')).rejects.toThrow('notFound');
  });

  it('lets every other refusal surface as the fault it is', async () => {
    stubService(REPOSITORIES, 503);
    await expect(render()).rejects.toThrow('API 503');
  });

  it('is dynamic, so a page is never served at another reader’s span', () => {
    expect(dynamic).toBe('force-dynamic');
  });
});
