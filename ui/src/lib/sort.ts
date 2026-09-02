/**
 * Column ordering for the tables, and the one rule that is not negotiable while doing it.
 *
 * Repositories and teams may be ordered by any column the reader clicks. PEOPLE MAY NOT BE RANKED:
 * the scope boundaries forbid ordering actors by any metric or finding, so no actor table gets a
 * sortable metric column. What `/contributors` does sort by, from 2026-09-02, is the combination of
 * labels its rows' repositories already carry, plus the repository count that is navigation beside
 * it. Nothing here enforces any of that — it is a decision about which headers each table hands to
 * `SortHeader`.
 *
 * An unmeasured value sorts last in BOTH directions. Sorting descending on "stale open pull
 * requests" is a question about the repositories that have some; a repository whose count could not
 * be read is not the answer to it, in either direction.
 */

export type Direction = 'ascending' | 'descending';

/** What a cell can be ordered on — `undefined` for a figure the service could not observe. */
export type SortValue = string | number | undefined;

export function reverse(direction: Direction): Direction {
  return direction === 'ascending' ? 'descending' : 'ascending';
}

/**
 * Where a header click leaves the direction: reversed on the column already active, ascending on any
 * other, which is what "sort by this one" means — from the top of it.
 *
 * Carrying the previous column's direction over would open a newly clicked column descending for no
 * reason a reader could see. It lives here rather than in the two tables that sort so that both
 * spell the transition once, and so a click's effect can be tested without a click: the components
 * hold this in `useState` and are rendered statically by the component tests.
 */
export function nextDirection<Column>(
  active: Column | null,
  next: Column,
  direction: Direction,
): Direction {
  return next === active ? reverse(direction) : 'ascending';
}

/**
 * Order two present values: numerically when both are numbers, otherwise as text.
 *
 * The text comparison is locale-aware so that accented logins and repository names sort where a
 * reader expects them rather than by code point.
 */
export function compare(left: SortValue, right: SortValue): number {
  if (typeof left === 'number' && typeof right === 'number') {
    return left - right;
  }
  return String(left ?? '').localeCompare(String(right ?? ''));
}

export function sorted<Row>(
  rows: readonly Row[],
  read: (row: Row) => SortValue,
  direction: Direction,
): Row[] {
  const present = rows.filter((row) => read(row) !== undefined);
  const absent = rows.filter((row) => read(row) === undefined);
  // Negating the comparator rather than reversing the sorted array keeps ties in their original
  // order in both directions, so a second click on a column does not shuffle equal rows.
  const ordered = [...present].sort((left, right) => {
    const ordering = compare(read(left), read(right));
    return direction === 'ascending' ? ordering : -ordering;
  });
  return [...ordered, ...absent];
}
