/**
 * @vitest-environment jsdom
 */

/**
 * What a header click does to the contributor list, which the server render cannot reach.
 *
 * `tables.test.ts` asserts the render this table OPENS on — readiness ascending, unlabelled last —
 * and that is the half a static renderer can see. The direction lives in component state, so the
 * reversal, the switch between columns, and above all THE GUARDRAIL that survives both of them are
 * only assertable from a browser: an unlabelled person sorts last whichever way the list is turned,
 * because "who is worst" is a question about the graded people and an unreadable repository is not
 * an answer to it read either way round.
 *
 * There is no navigation to assert and none to stub: this table holds its sort in state and puts
 * nothing in the URL, and it has no filter and no paging — the whole list is the estate's people.
 */

import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { ActorsTable } from '@/components/ActorsTable';
import type { ActorRow } from '@/lib/types';

/**
 * Four people who separate all three columns, and whose labels separate the combination order:
 * `dan` is all green, `carol` green and amber, `alice` blocked, and `bob` has nothing left to label.
 */
const ROWS: ActorRow[] = [
  { login: 'alice', repositories: 3, labels: ['red'] },
  { login: 'bob', repositories: 1, labels: [] },
  { login: 'carol', repositories: 2, labels: ['green', 'amber'] },
  { login: 'dan', repositories: 4, labels: ['green'] },
];

function logins(): string[] {
  return screen
    .getAllByRole('row')
    .slice(1)
    .map((row) => within(row).getAllByRole('cell')[0]?.textContent ?? '');
}

function sortBy(label: string): string[] {
  fireEvent.click(
    within(screen.getByRole('columnheader', { name: new RegExp(label) })).getByRole('button'),
  );
  return logins();
}

function announced(label: string): string | null {
  return screen.getByRole('columnheader', { name: new RegExp(label) }).getAttribute('aria-sort');
}

function mount(rows: readonly ActorRow[] = ROWS, labelled = true) {
  return render(<ActorsTable rows={rows} weeks={8} labelled={labelled} />);
}

afterEach(cleanup);

describe('ActorsTable sorting', () => {
  it('opens on readiness ascending, and says so on the column', () => {
    mount();

    expect(logins()).toEqual(['dan', 'carol', 'alice', 'bob']);
    expect(announced('Readiness')).toBe('ascending');
    expect(announced('Login')).toBe('none');
    expect(announced('Repositories')).toBe('none');
  });

  it('sorts on each column, reaching that column’s own reader', () => {
    mount();

    expect(sortBy('Login')).toEqual(['alice', 'bob', 'carol', 'dan']);
    expect(sortBy('Repositories')).toEqual(['bob', 'carol', 'alice', 'dan']);
  });

  it('reverses the active column and opens any other one ascending', () => {
    mount();

    expect(sortBy('Login')).toEqual(['alice', 'bob', 'carol', 'dan']);
    expect(sortBy('Login')).toEqual(['dan', 'carol', 'bob', 'alice']);
    expect(announced('Login')).toBe('descending');

    expect(sortBy('Repositories')).toEqual(['bob', 'carol', 'alice', 'dan']);
    expect(announced('Repositories')).toBe('ascending');
    expect(announced('Login')).toBe('none');
  });

  // The guardrail: reversing readiness answers "who is worst", and somebody whose repositories all
  // came back unreadable is not the answer to it — they stay at the bottom either way round.
  it('keeps a person with nothing left to label last in both directions', () => {
    mount();

    expect(sortBy('Readiness')).toEqual(['alice', 'carol', 'dan', 'bob']);
    expect(announced('Readiness')).toBe('descending');
    expect(sortBy('Readiness')).toEqual(['dan', 'carol', 'alice', 'bob']);
  });

  it('badges a person the service sent no labels key for as cannot assess', () => {
    mount([{ login: 'eve', repositories: 2 }]);

    expect(screen.getByText('Cannot assess')).toBeTruthy();
  });

  /**
   * `Login` re-sorts through `compare`, which is locale-aware, and every fixture above is lowercase.
   *
   * GitHub logins are frequently capitalised, so the case the estate actually shows is the one no
   * assertion reached: a code-point sort would put every capital ahead of every lower-case login and
   * read as an order about ASCII rather than about spelling. Sorting stays alphabetical in both
   * directions and ranks nobody either way.
   */
  it('sorts mixed-case logins by their letters rather than by their case', () => {
    const mixed: ActorRow[] = [
      { login: 'Zoe', repositories: 1, labels: ['green'] },
      { login: 'alice', repositories: 1, labels: ['green'] },
      { login: 'Bob', repositories: 1, labels: ['green'] },
    ];
    mount(mixed);

    expect(sortBy('Login')).toEqual(['alice', 'Bob', 'Zoe']);
    expect(sortBy('Login')).toEqual(['Zoe', 'Bob', 'alice']);
  });

  // The second cause of an empty label list: a readiness policy switched off grades nothing, so
  // every row arrives empty and the estate's own word for that is the one the other lists use.
  it('badges nobody as cannot assess where the policy graded nothing in the window', () => {
    mount([{ login: 'eve', repositories: 2, labels: [] }], false);

    expect(screen.getByText('Not assessed')).toBeTruthy();
    expect(screen.queryByText('Cannot assess')).toBeNull();
  });

  it('links every login onto its own page at the span the list is read at', () => {
    mount();

    expect(screen.getByRole('link', { name: 'dan' }).getAttribute('href')).toBe(
      '/contributors/dan?weeks=8',
    );
  });

  it('ranks nobody: no click puts a score, a rank or a metric column on the page', () => {
    const { container } = mount();

    for (const label of ['Login', 'Repositories', 'Readiness']) {
      sortBy(label);
      expect(container.textContent).not.toMatch(/score|rank|verdict|average|%/i);
    }
  });
});
