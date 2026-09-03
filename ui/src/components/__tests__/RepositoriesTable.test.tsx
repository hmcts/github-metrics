/**
 * @vitest-environment jsdom
 */

/**
 * What a click does to the estate table: the sort it holds in state, and the filter it puts in the
 * URL.
 *
 * Neither is reachable through `react-dom/server`, which is why `tables.test.ts` leaves this
 * component out and tests its decidable half as pure functions in `lib/__tests__/rows.test.ts`
 * instead. The join between them — that a header click reaches the right column's reader, and that
 * the readiness chips navigate rather than filter in place — is only visible from a browser.
 *
 * The `weeks` in the URL is asserted on every navigation. A filter that dropped the span would
 * silently re-render the page at the default window, showing a different window's figures under the
 * filter the reader just applied.
 */

import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { RepositoriesTable } from '@/components/RepositoriesTable';
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
    expect(sortBy('Merged')).toEqual(['web', 'docs', 'api']);
    expect(sortBy('Direct commits')).toEqual(['docs', 'web', 'api']);
    expect(sortBy('Open')).toEqual(['web', 'docs', 'api']);
    expect(sortBy('Stale')).toEqual(['docs', 'web', 'api']);
    expect(sortBy('Findings')).toEqual(['docs', 'web', 'api']);
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

describe('RepositoriesTable readiness filter', () => {
  it('counts every state over the term-filtered rows, listing the ones with none at zero', () => {
    mount();

    expect(chip('Ready').textContent).toContain('1');
    expect(chip('Blocked').textContent).toContain('1');
    expect(chip('Caution').textContent).toContain('0');
    expect(chip('Not assessed').textContent).toContain('1');
  });

  it('navigates on a chip, keeping the span and any term already in the URL', () => {
    url('weeks=26&repository=e');
    mount();

    fireEvent.click(chip('Ready'));

    expect(replaced).toEqual(['/repositories?weeks=26&repository=e&label=green']);
  });

  it('clears the filter on the active chip, by dropping the parameter rather than emptying it', () => {
    url('weeks=12&label=green');
    mount();

    expect(chip('Ready').getAttribute('aria-pressed')).toBe('true');
    fireEvent.click(chip('Ready'));

    expect(replaced).toEqual(['/repositories?weeks=12']);
  });

  it('applies the filter in the URL to the rows, and counts unaffected by it', () => {
    url('weeks=12&label=red');
    mount();

    expect(order()).toEqual(['web']);
    // The counts still describe the whole term-filtered estate, so the chips stay usable.
    expect(chip('Ready').textContent).toContain('1');
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
});

function chip(label: string): HTMLElement {
  return within(screen.getByRole('group', { name: 'Readiness filter' })).getByRole('button', {
    name: new RegExp(label),
  });
}

/** What a screen reader is told about a column's sort — the property of the `<th>`, not the button. */
function announced(label: string): string | null {
  return screen.getByRole('columnheader', { name: new RegExp(label) }).getAttribute('aria-sort');
}
