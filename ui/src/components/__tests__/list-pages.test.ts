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
import { ESTATE_FILTERS } from '@/lib/rows';
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
  repositories: 3,
  unavailable: 1,
  teams: 1,
  actors: 1,
  merged_pull_requests: 9,
  direct_commits: 1,
  // The reported two only: `label_counts` counts the unreportable repository nowhere, which is why
  // the readiness donut has to be told how many the span left out.
  labels: { green: 1, amber: 1 },
};

/**
 * Three repositories: one measured well, one measured badly, and one nothing could be read on.
 *
 * The third is what the donuts are counted against — no label and every field absent, so each of
 * the six bands it lands in is the ungraded one and every donut still totals three.
 */
const CLEAR_ALERTS = {
  dependabot: { open: 0, by_severity: {} },
  code_scanning: { open: 0, by_severity: {} },
  secret_scanning: { open: 0, by_severity: {} },
};

const REPOSITORIES: RepositoryRow[] = [
  {
    repository: 'api',
    team: 'platform',
    readiness: 'green',
    required_approving_reviews: 2,
    required_status_checks: 3,
    unreviewed_substantial: 'none',
    sonar_coverage: 92.5,
    security: CLEAR_ALERTS,
    sonar_security_rating: { value: 1 },
    sonar_security_issues: 0,
  },
  {
    repository: 'web',
    team: 'platform',
    readiness: 'amber',
    required_approving_reviews: 0,
    required_status_checks: 0,
    unreviewed_substantial: 'above',
    sonar_coverage: 41,
    security: { ...CLEAR_ALERTS, dependabot: { open: 2, by_severity: { critical: 1, low: 1 } } },
    sonar_security_rating: { value: 2 },
    sonar_security_issues: 3,
  },
  { repository: 'batch', team: 'platform', detail: 'no window could be reported for this repository' },
];

const ACTORS: ActorRow[] = [{ login: 'ada', repositories: 2, labels: ['green'] }];

const TEAMS: TeamRow[] = [
  { team: 'platform', repositories: 2, unavailable: 0, actors: 1, labels: { green: 1 } },
];

/** Every path the stubbed service was asked for, in the order the pages asked for them. */
let requested: string[] = [];

vi.mock('next/headers', () => ({
  cookies: () => Promise.resolve({ get: () => undefined }),
}));

/** What the client components on the page read the URL as, which a test about filtering sets. */
let search = new URLSearchParams();

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: () => undefined }),
  usePathname: () => '/repositories',
  useSearchParams: () => search,
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
  search = new URLSearchParams();
});

describe('the three list routes', () => {
  it('fetches the repositories list at the span asked for, and links at the same one', async () => {
    stubService();
    const markup = renderToStaticMarkup(await RepositoriesPage({ searchParams: Promise.resolve({ weeks: '26' }) }));

    expect(spans()).toEqual(['26', '26']);
    expect(markup).toContain('href="/repositories/api?weeks=26"');
    expect(markup).toContain('href="/teams/platform?weeks=26"');
  });

  it('fetches the contributors list at the span asked for, and links at the same one', async () => {
    stubService();
    const markup = renderToStaticMarkup(await ContributorsPage({ searchParams: Promise.resolve({ weeks: '26' }) }));

    expect(spans()).toEqual(['26', '26']);
    expect(markup).toContain('href="/contributors/ada?weeks=26"');
    // The caption is the only statement of what the list is ordered by, and `ActorsTable`'s default
    // column is what makes it true: change one without the other and the page misdescribes itself.
    // The apostrophe is matched either way round because `react-dom/server` escapes it in text.
    expect(markup).toMatch(/by their repositories(&#x27;|') labels/);
  });

  it('fetches the teams list at the span asked for, and links at the same one', async () => {
    stubService();
    const markup = renderToStaticMarkup(await TeamsPage({ searchParams: Promise.resolve({ weeks: '26' }) }));

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
    const markup = renderToStaticMarkup(await RepositoriesPage({ searchParams: Promise.resolve({}) }));

    expect(spans()).toEqual(['4', '4']);
    expect(markup).toContain('href="/repositories/api?weeks=4"');
  });

  /** A span off the list is not a span: `/windows` says what is on offer and the page keeps to it. */
  it('ignores a span the service does not offer', async () => {
    stubService();
    renderToStaticMarkup(await ContributorsPage({ searchParams: Promise.resolve({ weeks: '99' }) }));

    expect(spans()).toEqual(['4', '4']);
  });

  /** Answer every list endpoint with nothing in it, which each of the three pages must say aloud. */
  function stubEmptyService(): void {
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
  }

  it('says so rather than drawing an empty table where a list came back empty', async () => {
    stubEmptyService();
    const markup = renderToStaticMarkup(await TeamsPage({ searchParams: Promise.resolve({}) }));

    expect(markup).toContain('No team is configured for this organisation.');
  });

  /**
   * The other two lists' empty states, which are three different sentences and not one.
   *
   * An estate with no repository configured, and one whose repositories nobody contributed to at
   * this span, are different facts with different remedies — the first is a configuration that names
   * nothing, the second a window too short or a collection not run — so each page names its own.
   */
  it('names what is missing per list, with the remedy that list has', async () => {
    stubEmptyService();
    const repositories = renderToStaticMarkup(
      await RepositoriesPage({ searchParams: Promise.resolve({}) }),
    );
    const contributors = renderToStaticMarkup(
      await ContributorsPage({ searchParams: Promise.resolve({}) }),
    );

    expect(repositories).toContain('No repository is configured for this organisation.');
    expect(repositories).toContain('Add repositories to the configuration');
    expect(contributors).toContain('Nobody contributed to a reported repository at this span.');
    expect(contributors).toContain('Run metrics collect for the span being asked for');
    // The headline figures are still drawn: the overview answered, and zero is a figure.
    expect(repositories).toContain('Merged pull requests');
  });

  /**
   * The repositories the span could not be reported for, stated on the card it qualifies.
   *
   * `12 repositories` with two of them unreported is a different estate from twelve reported ones,
   * and the detail under the count is the only place the page says which of the two it is.
   */
  it('says how many repositories the span reported, where it could not report them all', async () => {
    stubService();
    // The fixture estate is three repositories with one of them unreportable, which is the row the
    // donuts are counted against.
    const partial = renderToStaticMarkup(
      await RepositoriesPage({ searchParams: Promise.resolve({}) }),
    );
    expect(partial).toContain('2 reported');
    expect(partial).not.toContain('all reported');

    requested = [];
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string) => {
        const body = (() => {
          if (url.includes('/windows')) return WINDOWS;
          if (url.includes('/overview')) return { ...OVERVIEW, repositories: 12, unavailable: 0 };
          return REPOSITORIES;
        })();
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
      }),
    );
    const reported = renderToStaticMarkup(
      await RepositoriesPage({ searchParams: Promise.resolve({}) }),
    );

    expect(reported).toContain('all reported');
  });

  /** The six donut titles, in the order they are drawn in. */
  const DONUTS = [
    'Readiness',
    'Enforces review',
    'Enforces CI',
    'Unreviewed substantial merges',
    'Test coverage',
    'Security issues',
  ];

  /**
   * The legend of one donut, read off the rendered page as the band words and the counts under them.
   *
   * The chart canvas is a recharts wedge and says nothing a test can read, so the legend is where the
   * figures are — which is also where a reader finds them for a band drawn at zero.
   */
  function legend(markup: string, title: string): Record<string, number> {
    const panel = panelOf(markup, title);
    const bands: Record<string, number> = {};
    // `text-slate-400[^"]*` because every legend here is interactive now and its label carries the
    // hover class beside that one — the words and the count are what is being read either way.
    const entries = panel.matchAll(
      /text-slate-400[^"]*">([^<]+)<\/span><span class="text-xs text-slate-600 tabular-nums">(\d+)</g,
    );
    for (const match of entries) {
      bands[match[1] ?? ''] = Number(match[2]);
    }
    return bands;
  }

  /**
   * The six estate donuts, each counting EVERY repository at the span.
   *
   * The counts are the point rather than the titles: a donut wired to the wrong slice builder, or one
   * quietly dropping the rows whose field is absent, renders six headings just the same. So the
   * unmeasured repository is asserted into each ungraded band, and every donut — the readiness one
   * included, which is distributed over the REPORTED repositories and has to be told about the rest —
   * is asserted to total the three the estate holds.
   */
  it('draws six donuts over the estate, counting the unmeasured repository in each', async () => {
    stubService();
    const markup = renderToStaticMarkup(await RepositoriesPage({ searchParams: Promise.resolve({}) }));

    // Drawn in this order, which is the order the plan states and the order they read in.
    const positions = DONUTS.map((title) => markup.indexOf(`>${title}</h3>`));
    expect(positions).toEqual([...positions].sort((left, right) => left - right));
    expect(Math.min(...positions)).toBeGreaterThan(-1);
    // The grid `SkeletonChart` stands in for while the page is cold, asserted at both ends: the
    // bones and the charts drawn at different widths is the layout shift the boundary exists to stop.
    expect(markup).toContain('grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4');

    expect(legend(markup, 'Enforces review')).toEqual({
      Multiple: 1,
      Enforced: 0,
      Unenforced: 1,
      Unknown: 1,
    });
    expect(legend(markup, 'Enforces CI')).toEqual({ Enforced: 1, Unenforced: 1, Unknown: 1 });
    expect(legend(markup, 'Unreviewed substantial merges')).toEqual({
      Clear: 1,
      'Within allowance': 0,
      'Above allowance': 1,
      Unknown: 1,
    });
    expect(legend(markup, 'Test coverage')).toEqual({
      '90% or more': 1,
      '80% to under 90%': 0,
      'Below 80%': 1,
      Unknown: 1,
    });
    // Clear alerts and an A rating against a critical Dependabot alert, which outranks the B rating
    // and the open issues beside it: the band is the worst signal on the row, not a tally of them.
    expect(legend(markup, 'Security issues')).toEqual({
      Clear: 1,
      Medium: 0,
      High: 1,
      Unknown: 1,
    });
    expect(legend(markup, 'Readiness')).toEqual({
      Ready: 1,
      Caution: 1,
      Blocked: 0,
      'Cannot assess': 0,
      'Not assessed': 1,
    });

    // And each of the five totals the whole estate, which is what counting the unmeasured row into
    // an ungraded band rather than dropping it buys: the donuts and the table agree on how many
    // repositories there are.
    for (const title of DONUTS) {
      const counts = Object.values(legend(markup, title));
      expect(counts.reduce((total, value) => total + value, 0)).toBe(REPOSITORIES.length);
    }
  });

  /**
   * The security donut's own explanation of its band, pinned to what `securityBand` actually reads.
   *
   * The tooltip is the only place a reader learns what the band means, and stale prose is the one
   * thing a coverage gate cannot see: a sentence naming a signal the row no longer carries renders
   * exactly as well as a correct one. Both halves are asserted — the signal list, which lost the
   * hotspot count when Sonar retired it on 2026-09-04, and the rating boundary, which puts C at
   * Medium since the same day's reversal. `tone.test.ts` grades the letters; this says the page
   * tells the reader the same thing.
   */
  it('explains the security band with the signals and the boundary the code applies', async () => {
    stubService();
    const markup = renderToStaticMarkup(await RepositoriesPage({ searchParams: Promise.resolve({}) }));

    const panel = panelOf(markup, 'Security issues');
    expect(panel).toContain('security rating and issues');
    expect(panel).toContain('a security rating of D or worse');
    expect(panel).toContain('a rating of B or C');
    expect(panel).not.toContain('hotspot');
    expect(panel).not.toContain('C or worse');
  });

  /** The panel of one donut, from its heading to the next one's. */
  function panelOf(markup: string, title: string): string {
    const opened = markup.indexOf(`>${title}</h3>`);
    expect(opened).toBeGreaterThan(-1);
    const next = markup.indexOf('<h3', opened + 1);
    return markup.slice(opened, next === -1 ? undefined : next);
  }

  /** The labels of one donut's legend entries drawn as pressed, which is what it is filtered to. */
  function pressed(markup: string, title: string): string[] {
    return panelOf(markup, title)
      .split('<button')
      .filter((entry) => entry.includes('aria-pressed="true"'))
      .map((entry) => /text-slate-400[^"]*">([^<]+)</.exec(entry)?.[1] ?? 'unlabelled');
  }

  /**
   * The chips the table reports its filters with, as the words a reader sees on each one.
   *
   * Read out of the filter bar alone rather than off the page, because every word a chip prints is
   * also in the donut heading and the legend entry it came from — an assertion against the whole
   * markup passes just as well when no chip was rendered at all. The bar itself is always drawn now
   * that it holds the Production toggle, so failing to find it is a broken render and not an
   * unfiltered one.
   */
  function chips(markup: string): string[] {
    const opened = markup.indexOf('aria-label="Repository filters"');
    expect(opened).toBeGreaterThan(-1);
    const group = markup.slice(opened, markup.indexOf('</div>', opened));
    return [
      ...group.matchAll(
        /<span class="text-slate-400 uppercase tracking-wide">([^<]*)<\/span>([^<]*)</g,
      ),
    ].map((match) => `${match[1] ?? ''}${match[2] ?? ''}`.trim());
  }

  /** Every donut is a filter control, so each legend is a labelled group of buttons. */
  it('makes each donut the filter control for its own dimension', async () => {
    stubService();
    const markup = renderToStaticMarkup(await RepositoriesPage({ searchParams: Promise.resolve({}) }));

    for (const title of DONUTS) {
      expect(panelOf(markup, title)).toContain(`aria-label="${title} filter"`);
      expect(pressed(markup, title)).toEqual([]);
    }
  });

  /**
   * Which parameter each donut was wired to, read back off a URL that filters on one of them.
   *
   * Six donuts and six parameter names is six chances to hand a donut the parameter beside it, and
   * the page renders identically either way — the mistake only shows in what a click filters. So the
   * URL is set to a coverage band and the coverage donut has to be the one showing it: `high` is a
   * key in the security bands too, so a coverage donut wired to `security` would light up there
   * instead. The table below reads the same parameter, which is why the chip and the rows say so.
   */
  it('wires each donut to its own parameter, and the table reads them back', async () => {
    search = new URLSearchParams('coverage=high');
    stubService();
    const markup = renderToStaticMarkup(await RepositoriesPage({ searchParams: Promise.resolve({}) }));

    expect(pressed(markup, 'Test coverage')).toEqual(['90% or more']);
    for (const title of DONUTS.filter((each) => each !== 'Test coverage')) {
      expect(pressed(markup, title)).toEqual([]);
    }
    // The chip names the dimension the donut drew, read off the chip row rather than off the page:
    // both of its words are in the donut's own heading and legend whatever the chips hold, so
    // asserting them against the whole markup would pass with no chip row rendered at all.
    expect(chips(markup)).toEqual(['Test coverage: 90% or more']);
    expect(markup).toContain('Remove Test coverage filter');
    // And the table holds only the row in that band.
    expect(markup).toContain('href="/repositories/api?weeks=4"');
    expect(markup).not.toContain('href="/repositories/web?weeks=4"');

    // While every donut still counts the whole estate: they describe the estate, not the table, and
    // a donut fed the filtered rows would shrink to one as the reader clicked it.
    for (const title of DONUTS) {
      const counts = Object.values(legend(markup, title));
      expect(counts.reduce((total, value) => total + value, 0)).toBe(REPOSITORIES.length);
    }
  });

  /**
   * Every dimension round-tripped: the URL its donut writes is the URL that donut reads back.
   *
   * The case above proves the join on one parameter. This one walks all six, because five donuts
   * handed a neighbour's parameter render identically to five wired correctly — the mistake shows
   * only when a value is put in the URL and the wrong legend lights up, or none of them does.
   */
  it('reads every dimension back on the donut that writes it', async () => {
    for (const filter of ESTATE_FILTERS) {
      const option = filter.options[0];
      if (option === undefined) {
        throw new Error(`${filter.parameter} offers no option to filter on`);
      }
      search = new URLSearchParams(`${filter.parameter}=${option.key}`);
      stubService();
      const markup = renderToStaticMarkup(
        await RepositoriesPage({ searchParams: Promise.resolve({}) }),
      );

      expect(pressed(markup, filter.title)).toEqual([option.name]);
      for (const title of DONUTS.filter((each) => each !== filter.title)) {
        expect(pressed(markup, title)).toEqual([]);
      }
      expect(chips(markup)).toEqual([`${filter.title}: ${option.name}`]);
    }
  });
});
