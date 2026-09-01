/**
 * Column ordering for the tables, and the one rule that is not negotiable while doing it.
 *
 * Repositories and teams may be ordered by any column the reader clicks. PEOPLE MAY NOT: the scope
 * boundaries forbid ranking actors by any metric or finding, so an actor table gets no sortable
 * numeric column and stays alphabetical. Nothing here enforces that — it is a decision about which
 * headers each table hands to `SortHeader`.
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
