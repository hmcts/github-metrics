/**
 * What a repository list shows, and in what order, before any of it reaches a component.
 *
 * The filtering and ordering live here rather than in `RepositoriesTable` because they are the part
 * that can be wrong in a way nobody notices: a term that only matched repository names would quietly
 * hide a team the reader searched for, and a readiness filter that resolved an unknown key to "no
 * matches" would show an empty table for a mistyped URL instead of the whole list.
 *
 * The default order is team then repository, so a reader scanning the list reads one team's
 * repositories together. It is deliberately not an order by any figure: the reader can sort by a
 * column, but the list does not open ranked by findings, which would read as a league table.
 */

import { matches } from '@/lib/filter';
import { RAG_STATES, state, type RAGState } from '@/lib/rag';
import { compare, type SortValue } from '@/lib/sort';
import type { RepositoryRow } from '@/lib/types';

/**
 * Read a readiness state from a URL parameter, or nothing when it names none.
 *
 * An unrecognised value resolves to no filter rather than to a state nothing matches: the parameter
 * is typed by hand and shared in links, and a stale one should show the list rather than a blank.
 */
export function parseState(raw: string | null | undefined): RAGState | null {
  return RAG_STATES.includes(raw as RAGState) ? (raw as RAGState) : null;
}

/** The default order: one team's repositories together, alphabetically within the team. */
export function orderRepositories(rows: readonly RepositoryRow[]): RepositoryRow[] {
  return [...rows].sort(
    (left, right) =>
      compare(left.team, right.team) || compare(left.repository, right.repository),
  );
}

/** A term matches a row on either name a reader would type: the repository or its team. */
export function matchesRepository(row: RepositoryRow, term: string): boolean {
  return matches(row.repository, term) || matches(row.team, term);
}

export function filterRepositories(
  rows: readonly RepositoryRow[],
  term: string,
  label: RAGState | null,
): RepositoryRow[] {
  return rows.filter(
    (row) =>
      matchesRepository(row, term) && (label === null || state(row.readiness) === label),
  );
}

/**
 * Whether the repository holds a CODEOWNERS file, or nothing where nobody could read its contents.
 *
 * Three-valued on purpose. `0` files is a real answer — every location CODEOWNERS is allowed to live
 * in was looked at and none held one — while an absent count is a repository whose contents the token
 * could not read, which says nothing about whether ownership is declared there.
 */
export function codeownersPresent(row: RepositoryRow): boolean | undefined {
  return row.codeowners_files === undefined ? undefined : row.codeowners_files >= 1;
}

/**
 * Order a Yes/No/dash cell: No below Yes, and an unreadable answer last in either direction.
 *
 * `undefined` passes straight through rather than becoming a number, because that is the value
 * `sorted` holds back from both ends — the same rule the numeric columns sort an unmeasured count by.
 */
export function answerOrder(answer: boolean | undefined): SortValue {
  return answer === undefined ? undefined : Number(answer);
}

/**
 * Count the rows carrying each readiness state, every state present.
 *
 * Total over the five states so a filter control can show `0` for a state nothing carries — the
 * useful finding that nothing is blocked, which a missing chip would hide.
 */
export function stateCounts(rows: readonly RepositoryRow[]): Record<RAGState, number> {
  return Object.fromEntries(
    RAG_STATES.map((readiness) => [
      readiness,
      rows.filter((row) => state(row.readiness) === readiness).length,
    ]),
  ) as Record<RAGState, number>;
}
