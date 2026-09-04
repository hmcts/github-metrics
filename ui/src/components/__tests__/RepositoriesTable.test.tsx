/**
 * @vitest-environment jsdom
 */

/**
 * What a click does to the estate table: the sort it holds in state, and the filter it puts in the
 * URL.
 *
 * Neither is reachable through `react-dom/server`, which is why `tables.test.ts` leaves this
 * component out and tests its decidable half as pure functions in `lib/__tests__/rows.test.ts`
 * instead. The join between them — that a header click reaches the right column's reader, and that a
 * chip's × navigates rather than filtering in place — is only visible from a browser.
 *
 * The `weeks` in the URL is asserted on every navigation. A filter that dropped the span would
 * silently re-render the page at the default window, showing a different window's figures under the
 * filter the reader just applied.
 */

import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { RepositoriesTable } from '@/components/RepositoriesTable';
import { PRODUCTION_TOGGLE_ACTIVE, PRODUCTION_TOGGLE_INACTIVE } from '@/lib/production';
import type { RepositoryRow } from '@/lib/types';

let replaced: string[] = [];

let parameters = new URLSearchParams();

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: (target: string) => void replaced.push(target) }),
  usePathname: () => '/repositories',
  useSearchParams: () => parameters,
}));

function url(query: string): void {
  parameters = new URLSearchParams(query);
  window.history.replaceState(null, '', query === '' ? '/repositories' : `/repositories?${query}`);
}

/**
 * Three rows that separate every column: no two share a value on any of them, and `platform/api`
 * carries no figures at all so the unmeasured-sorts-last rule is exercised by the numeric columns.
 */
const ROWS: RepositoryRow[] = [
  {
    repository: 'web',
    team: 'delivery',
    readiness: 'red',
    merged_pull_requests: 3,
    direct_commits: 9,
    currently_open: 2,
    stale_open: 5,
    finding_occurrences: 7,
    codeowners_files: 2,
    sonar_reported: false,
    // A second dimension the donuts filter on, so two parameters in the URL can be seen to AND
    // rather than to overwrite one another: `web` requires no approval and `docs` requires two.
    required_approving_reviews: 0,
    // The only production service here, so the toggle's count is one and the row it leaves is
    // known: `docs` was read and is not one, and `api`'s answer is absent entirely.
    production: true,
  },
  {
    repository: 'api',
    team: 'platform',
    detail: 'No merge activity in this window.',
  },
  {
    repository: 'docs',
    team: 'content',
    readiness: 'green',
    merged_pull_requests: 8,
    direct_commits: 1,
    currently_open: 6,
    stale_open: 0,
    finding_occurrences: 2,
    // The two answers are crossed over from `web`'s, so a header wired to the other column's reader
    // sorts the rows the other way round and fails rather than agreeing by coincidence.
    codeowners_files: 0,
    sonar_reported: true,
    required_approving_reviews: 2,
    production: false,
  },
];

/** The repository names down the table, in the order the current render puts them. */
function order(): string[] {
  return screen
    .getAllByRole('row')
    .slice(1)
    .map((row) => within(row).getAllByRole('cell')[1]?.textContent ?? '')
    .map((cell) => cell.replace('No merge activity in this window.', ''));
}

/**
 * One row's CODEOWNERS and Sonar cells, found where the header order puts those two columns.
 *
 * Read by header name rather than by a hardcoded pair of positions: a column inserted before Stale
 * would otherwise leave these tests silently comparing two unrelated cells, and passing.
 */
function governanceCells(entry: HTMLElement): (HTMLElement | undefined)[] {
  const headers = screen.getAllByRole('columnheader').map((cell) => cell.textContent);
  const row = within(entry).getAllByRole('cell');
  return ['CODEOWNERS', 'Sonar'].map((label) => row[headers.indexOf(label)]);
}

/** One row's two governance answers as text. */
function answers(repository: string): (string | undefined)[] {
  const entry = screen
    .getAllByRole('row')
    .slice(1)
    .find((row) => within(row).getAllByRole('cell')[1]?.textContent?.startsWith(repository));
  return governanceCells(entry as HTMLElement).map((cell) => cell?.textContent);
}

/**
 * One row's Production cell, found under the header rather than at a fixed position.
 *
 * By name for `governanceCells`' reason: a column inserted to its left would otherwise leave these
 * assertions reading the readiness label beside it, and agreeing with itself.
 */
function productionCell(repository: string): HTMLElement | undefined {
  const headers = screen.getAllByRole('columnheader').map((cell) => cell.textContent);
  const entry = screen
    .getAllByRole('row')
    .slice(1)
    .find((row) => within(row).getAllByRole('cell')[1]?.textContent?.startsWith(repository));
  return within(entry as HTMLElement).getAllByRole('cell')[headers.indexOf('Production')];
}

/** Every CODEOWNERS and Sonar cell in the table, whatever each of them answers. */
function governance(): (HTMLElement | undefined)[] {
  return screen.getAllByRole('row').slice(1).flatMap(governanceCells);
}

function header(label: string): HTMLElement {
  return within(screen.getByRole('columnheader', { name: new RegExp(label) })).getByRole('button');
}

/** Sort on a column and report the order it left, so a click reads as one line in a test. */
function sortBy(label: string): string[] {
  fireEvent.click(header(label));
  return order();
}

function mount(rows: readonly RepositoryRow[] = ROWS) {
  return render(<RepositoriesTable rows={rows} weeks={12} />);
}

beforeEach(() => {
  replaced = [];
  url('weeks=12');
});

afterEach(cleanup);

describe('RepositoriesTable sorting', () => {
  it('opens on team then repository, which is neither column sorted', () => {
    mount();

    expect(order()).toEqual(['docs', 'web', 'api']);
    for (const label of ['Team', 'Repository', 'Readiness', 'Merged']) {
      expect(announced(label)).toBe('none');
    }
  });

  // Every column at once: each header hands the table its own reader function, and a mis-wired one
  // sorts by the column beside it, which no single-column assertion would catch.
  it('sorts on each column ascending, putting a repository with no figure last', () => {
    mount();

    // By team: content, then delivery, then platform.
    expect(sortBy('Team')).toEqual(['docs', 'web', 'api']);
    expect(sortBy('Repository')).toEqual(['api', 'docs', 'web']);
    // `api` has no grade, and an ungraded repository is not an answer to "which is worst".
    expect(sortBy('Readiness')).toEqual(['docs', 'web', 'api']);
    // No below Yes, and `api`, whose list could not be read, last rather than counted as either.
    expect(sortBy('Production')).toEqual(['docs', 'web', 'api']);
    expect(sortBy('Merged')).toEqual(['web', 'docs', 'api']);
    expect(sortBy('Direct commits')).toEqual(['docs', 'web', 'api']);
    expect(sortBy('Open')).toEqual(['web', 'docs', 'api']);
    expect(sortBy('Stale')).toEqual(['docs', 'web', 'api']);
    // No below Yes, and `api`'s unreadable answer last — the two columns disagree on which
    // repository answers Yes, so each header has to be reading its own field.
    expect(sortBy('CODEOWNERS')).toEqual(['docs', 'web', 'api']);
    expect(sortBy('Sonar')).toEqual(['web', 'docs', 'api']);
    expect(sortBy('Findings')).toEqual(['docs', 'web', 'api']);
  });

  it('keeps the unreadable answer last when either governance column is reversed', () => {
    mount();

    sortBy('CODEOWNERS');
    expect(sortBy('CODEOWNERS')).toEqual(['web', 'docs', 'api']);
    expect(announced('CODEOWNERS')).toBe('descending');

    sortBy('Sonar');
    expect(sortBy('Sonar')).toEqual(['docs', 'web', 'api']);
    expect(announced('Sonar')).toBe('descending');
  });

  it('reverses the column already sorted, and opens any other one ascending', () => {
    mount();

    expect(sortBy('Merged')).toEqual(['web', 'docs', 'api']);
    expect(announced('Merged')).toBe('ascending');

    expect(sortBy('Merged')).toEqual(['docs', 'web', 'api']);
    expect(announced('Merged')).toBe('descending');

    // A different column starts from its own top rather than inheriting the reversal.
    expect(sortBy('Repository')).toEqual(['api', 'docs', 'web']);
    expect(announced('Repository')).toBe('ascending');
    expect(announced('Merged')).toBe('none');
  });

  it('keeps the unmeasured rows last when the direction is reversed', () => {
    mount();

    sortBy('Stale');
    expect(sortBy('Stale')).toEqual(['web', 'docs', 'api']);
  });

  it('sorts without navigating: how one reader is looking at the list is not in the URL', () => {
    mount();

    sortBy('Merged');

    expect(replaced).toEqual([]);
  });
});

describe('RepositoriesTable columns', () => {
  it('heads the eleven columns in order, Production right of Readiness', () => {
    mount();

    expect(screen.getAllByRole('columnheader').map((cell) => cell.textContent)).toEqual([
      'Team',
      'Repository',
      'Readiness',
      'Production',
      'Merged',
      'Direct commits',
      'Open',
      'Stale',
      'CODEOWNERS',
      'Sonar',
      'Findings',
    ]);
  });

  // All three answers a governance cell can print, on the three rows that produce them: a file
  // found, a repository read that held none, and one nobody could read.
  it('prints Yes, No and a dash, never a zero for an answer that was not read', () => {
    mount();

    expect(answers('web')).toEqual(['Yes', 'No']);
    expect(answers('docs')).toEqual(['No', 'Yes']);
    expect(answers('api')).toEqual(['-', '-']);
  });

  // Header and cell together: a centred column whose header still read from the left, or the other
  // way round, would put the title off the answers under it.
  it('centres both governance answers under centred headers', () => {
    mount();

    for (const cell of governance()) {
      expect(cell?.className).toContain('text-center');
    }
    for (const label of ['CODEOWNERS', 'Sonar']) {
      const heading = screen.getByRole('columnheader', { name: new RegExp(label) });
      expect(heading.className).toContain('text-center');
    }
  });

  // Read off the whole cell rather than its own class list: the readiness cell three columns to the
  // left is toned by a span INSIDE an uncoloured `<td>`, so a governance answer coloured the same way
  // would slip past an assertion that only looked at the cell element.
  it('tones neither answer: no cell in this table carries a grade', () => {
    mount();

    for (const cell of governance()) {
      expect(cell?.outerHTML).not.toMatch(/emerald|amber|rose|rag-|red|green/);
    }
  });
});

describe('RepositoriesTable production column', () => {
  it('badges a production repository and leaves every other cell empty', () => {
    mount();

    expect(productionCell('web')?.textContent).toBe('Production');
    // A repository the list was read for and does not name, and one whose list could not be read
    // at all: both cells are empty, because there is no non-production badge to draw.
    expect(productionCell('docs')?.textContent).toBe('');
    expect(productionCell('api')?.textContent).toBe('');
  });

  it('holds an unread answer back from both ends of its own sort', () => {
    mount();

    sortBy('Production');
    expect(sortBy('Production')).toEqual(['web', 'docs', 'api']);
    expect(announced('Production')).toBe('descending');
  });

  it('grades nothing in the column: production is an attribute, not a verdict', () => {
    mount();

    for (const repository of ['web', 'docs', 'api']) {
      expect(productionCell(repository)?.outerHTML).not.toMatch(/emerald|amber|rose|rag-/);
    }
  });
});

describe('RepositoriesTable filter chips', () => {
  it('keeps the bar and shows no chip on it when the URL carries no filter', () => {
    mount();

    expect(chips()).toEqual([]);
    expect(order()).toEqual(['docs', 'web', 'api']);
  });

  it('reads one chip per filtered dimension, each naming its donut and the slice', () => {
    url('weeks=12&label=green&review=multiple');
    mount();

    expect(chips()).toEqual(['Readiness: Ready', 'Enforces review: Multiple']);
  });

  it('leaves a parameter naming no slice of its donut off the chips and off the rows', () => {
    url('weeks=12&label=purple');
    mount();

    expect(chips()).toEqual([]);
    expect(order()).toEqual(['docs', 'web', 'api']);
  });

  it('applies every filter in the URL together, not just the last one read', () => {
    url('weeks=12&label=green&review=multiple');
    mount();

    expect(order()).toEqual(['docs']);
  });

  it('says two dimensions matched nothing rather than ignoring one of them', () => {
    url('weeks=12&label=red&review=multiple');
    mount();

    expect(screen.queryByRole('table')).toBeNull();
    expect(chips()).toHaveLength(2);
  });

  it('drops only its own parameter on the ×, keeping the span, the term and the other chip', () => {
    url('weeks=26&repository=e&label=green&review=multiple');
    mount();

    fireEvent.click(remove('Readiness'));

    expect(replaced).toEqual(['/repositories?weeks=26&repository=e&review=multiple']);
  });

  it('clears a filter by dropping the parameter rather than by emptying it', () => {
    url('weeks=12&review=multiple');
    mount();

    fireEvent.click(remove('Enforces review'));

    expect(replaced).toEqual(['/repositories?weeks=12']);
  });

  it('applies the term in the URL to both the repository name and its team', () => {
    url('weeks=12&repository=PLAT');
    mount();

    expect(order()).toEqual(['api']);
  });

  it('says a filter matched nothing rather than drawing an empty table', () => {
    url('weeks=12&repository=nothing-here');
    mount();

    expect(screen.queryByRole('table')).toBeNull();
    expect(screen.getByText(/No repository matches this filter/)).toBeTruthy();
  });

  it('names the production toggle among the things an empty table can be cleared of', () => {
    url('weeks=12&repository=nothing-here');
    mount();

    expect(screen.getByText(/Clear the term, the Production toggle, or a filter/)).toBeTruthy();
  });
});

describe('RepositoriesTable production toggle', () => {
  it('is the first thing in the bar, before any chip', () => {
    url('weeks=12&label=green');
    mount();

    expect(bar().children[0]).toBe(toggle());
    expect(chips()).toEqual(['Readiness: Ready']);
  });

  it('carries the count of the production repositories a reader could turn it on for', () => {
    mount();

    expect(toggle().textContent).toBe('Production1');
    expect(within(toggle()).getByText('1').className).toContain('tabular-nums');
  });

  it('counts within the term and the other dimensions, but not within itself', () => {
    url('weeks=12&repository=docs');
    mount();

    // `docs` is the one row the term leaves and it is not a production service, so the toggle
    // offers nothing — and says so rather than printing the estate's total.
    expect(toggle().textContent).toBe('Production0');
  });

  it('reads the same count while it is on, which is what excluding its own filter buys', () => {
    url('weeks=12&production=true');
    mount();

    expect(toggle().textContent).toBe('Production1');
  });

  it('is greyed and unpressed while off, and royal and pressed while on', () => {
    mount();

    expect(toggle().getAttribute('aria-pressed')).toBe('false');
    for (const name of PRODUCTION_TOGGLE_INACTIVE.split(' ')) {
      expect(toggle().className).toContain(name);
    }

    cleanup();
    url('weeks=12&production=true');
    mount();

    expect(toggle().getAttribute('aria-pressed')).toBe('true');
    for (const name of PRODUCTION_TOGGLE_ACTIVE.split(' ')) {
      expect(toggle().className).toContain(name);
    }
  });

  it('has no way to remove it: it is a control, not a chip', () => {
    url('weeks=12&production=true');
    mount();

    expect(screen.queryByRole('button', { name: /Remove Production/ })).toBeNull();
    // The chips' × is an `svg` inside the chip. The toggle holds a dot and two words and no icon,
    // so there is nothing on it a reader could read as a dismissal.
    expect(toggle().innerHTML).not.toContain('<svg');
    expect(within(bar()).getAllByRole('button')).toEqual([toggle()]);
  });

  it('holds only the production repositories while it is on', () => {
    url('weeks=12&production=true');
    mount();

    expect(order()).toEqual(['web']);
  });

  it('writes its parameter on the click, keeping the span, the term and an active chip', () => {
    url('weeks=26&repository=e&label=red');
    mount();

    fireEvent.click(toggle());

    expect(replaced).toEqual(['/repositories?weeks=26&repository=e&label=red&production=true']);
  });

  it('clears the parameter on the second click, dropping it rather than emptying it', () => {
    url('weeks=26&repository=e&label=red&production=true');
    mount();

    fireEvent.click(toggle());

    expect(replaced).toEqual(['/repositories?weeks=26&repository=e&label=red']);
  });
});

/** The filter bar, which is always drawn: it holds the Production toggle whether or not a chip is. */
function bar(): HTMLElement {
  return screen.getByRole('group', { name: 'Repository filters' });
}

/** What each chip reads, in the order the bar puts them — the toggle first, so past it. */
function chips(): string[] {
  return Array.from(bar().children, (chip) => chip.textContent ?? '').slice(1);
}

/** The Production toggle, found the way a reader does: by the word on it. */
function toggle(): HTMLElement {
  return within(bar()).getByRole('button', { name: /Production/ });
}

/** One chip's dismiss control, found by the dimension it drops. */
function remove(title: string): HTMLElement {
  return screen.getByRole('button', { name: `Remove ${title} filter` });
}

/** What a screen reader is told about a column's sort — the property of the `<th>`, not the button. */
function announced(label: string): string | null {
  return screen.getByRole('columnheader', { name: new RegExp(label) }).getAttribute('aria-sort');
}
