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
import { RAG_HEX, RAG_LABEL, RAG_STATES, distributionState, state } from '@/lib/rag';
import { compare, type SortValue } from '@/lib/sort';
import {
  CHECKS_BANDS,
  COVERAGE_BANDS,
  REVIEW_BANDS,
  SECURITY_BANDS,
  UNREVIEWED_BANDS,
  checksBand,
  coverageBand,
  reviewBand,
  securityBand,
  unreviewedBand,
  type Band,
} from '@/lib/tone';
import type { RepositoryRow } from '@/lib/types';

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

/** The six dimensions the estate can be filtered by, one per donut and one per URL parameter. */
export type FilterParameter = 'label' | 'review' | 'checks' | 'unreviewed' | 'coverage' | 'security';

/** One value a dimension can be filtered to: the slice a reader clicked, in the slice's own words. */
export interface FilterOption {
  /** The value that goes in the URL — a `RAGState` or a band key, never the words beside it. */
  key: string;
  name: string;
  /** A colour value, not a class: the chip's dot is the mark its donut drew the slice with. */
  color: string;
}

/**
 * One filterable dimension: its parameter, the words a chip reads it as, its values, and how a row
 * is placed in one of them.
 */
export interface EstateFilter {
  parameter: FilterParameter;
  /** The title a chip prints before the value, and the donut's own heading. */
  title: string;
  options: readonly FilterOption[];
  /** Which option a row falls in, by the same function the donut counted it with. */
  band: (row: RepositoryRow) => string;
}

/** Read a band table as filter options, so a legend entry and a chip can never say different words. */
function bandOptions(bands: readonly Band[]): FilterOption[] {
  return bands.map((entry) => ({ key: entry.key, name: entry.name, color: entry.mark }));
}

/**
 * The six donuts as filters, each classifying a row with the function its own donut counts by.
 *
 * The `band` functions are `tone.ts`'s, not copies of them: the filtered row count has to equal the
 * legend count of the slice that was clicked, and a second definition of "moderate coverage" is how
 * a table shows nine rows under a wedge that says eleven. Readiness comes from `rag.ts` for the same
 * reason — the donut is drawn from `RAG_HEX` and the chip's dot is the same hex.
 *
 * `label` keeps the parameter name the readiness filter has always used, so links shared before the
 * other five dimensions existed still filter what they filtered.
 */
export const ESTATE_FILTERS: readonly EstateFilter[] = [
  {
    parameter: 'label',
    title: 'Readiness',
    options: RAG_STATES.map((readiness) => ({
      key: readiness,
      name: RAG_LABEL[readiness],
      color: RAG_HEX[readiness],
    })),
    // Folded through `distributionState`, as the donut counts through it: a label this build does
    // not know is counted under "Not assessed" in the slice, so it has to be selected by that slice
    // too. Without the fold the row falls in no option and the table shows one row fewer than the
    // wedge said — the one divergence every other band function here is written to avoid.
    band: (row) => distributionState(state(row.readiness)),
  },
  {
    parameter: 'review',
    title: 'Enforces review',
    options: bandOptions(REVIEW_BANDS),
    band: (row) => reviewBand(row.required_approving_reviews),
  },
  {
    parameter: 'checks',
    title: 'Enforces CI',
    options: bandOptions(CHECKS_BANDS),
    band: (row) => checksBand(row.required_status_checks),
  },
  {
    parameter: 'unreviewed',
    title: 'Unreviewed substantial merges',
    options: bandOptions(UNREVIEWED_BANDS),
    band: (row) => unreviewedBand(row.unreviewed_substantial),
  },
  {
    parameter: 'coverage',
    title: 'Test coverage',
    options: bandOptions(COVERAGE_BANDS),
    band: (row) => coverageBand(row.sonar_coverage),
  },
  {
    parameter: 'security',
    title: 'Security issues',
    options: bandOptions(SECURITY_BANDS),
    band: (row) => securityBand(row),
  },
];

export const FILTER_PARAMETERS: readonly FilterParameter[] = ESTATE_FILTERS.map(
  (filter) => filter.parameter,
);

/**
 * The production filter's parameter, DELIBERATELY OUTSIDE `ESTATE_FILTERS`.
 *
 * Every entry in that list is a donut's dimension: it has options, each with a colour its own slice
 * was drawn in, a band function that places a row in one of them, and — because of all that — a
 * dismissible chip in the bar under the charts. Production has none of it. It is one two-state
 * toggle over an attribute nothing graphs, and folding it into the list would give it a chip with an
 * × on it, which is the one control it must not have: the toggle is part of the bar rather than
 * something a reader has added to it.
 */
export const PRODUCTION_PARAMETER = 'production';

/**
 * The only value the toggle ever writes, and so the only one that reads back as on.
 *
 * A two-state control needs no vocabulary, but it does need one spelling: matching on anything
 * truthy would make `?production=0` an odd way of saying yes.
 */
export const PRODUCTION_VALUE = 'true';

/** Whether the production filter is on, read off the URL the same way the six dimensions are. */
export function parseProduction(read: (parameter: string) => string | null): boolean {
  return read(PRODUCTION_PARAMETER) === PRODUCTION_VALUE;
}

/** Which value each dimension is filtered to, where the dimension is filtered at all. */
export type RepositoryFilters = Partial<Record<FilterParameter, string>>;

/**
 * Read every dimension's filter off the URL, keeping only the values that name one of its options.
 *
 * A value in no option is dropped rather than kept: six parameters typed by hand and shared in links
 * is six ways to arrive at a table filtered to a value nothing can carry, and an empty list is a
 * worse answer than the whole one.
 */
export function parseFilters(read: (parameter: string) => string | null): RepositoryFilters {
  const filters: RepositoryFilters = {};
  for (const filter of ESTATE_FILTERS) {
    const raw = read(filter.parameter);
    if (raw !== null && filter.options.some((option) => option.key === raw)) {
      filters[filter.parameter] = raw;
    }
  }
  return filters;
}

/**
 * The rows a reader is looking at: the term, the production toggle, and every dimension they have
 * filtered, all together.
 *
 * Dimensions AND, because that is what clicking a second donut means — the repositories that are
 * blocked AND enforce no review — and each is checked with its own donut's band function, so the
 * table under a wedge holds exactly the rows the wedge counted. The production toggle ANDs with them
 * for the same reason.
 *
 * A row whose production answer could not be read is EXCLUDED while the toggle is on, rather than
 * kept on the chance that it is one. The toggle says "show me the production services", and a
 * repository nobody could classify is not an answer to that — leaving it in would put rows under a
 * count that did not count them, and letting it in as `false` would be the same guess in reverse.
 */
export function filterRepositories(
  rows: readonly RepositoryRow[],
  term: string,
  filters: RepositoryFilters,
  production = false,
): RepositoryRow[] {
  const active = ESTATE_FILTERS.filter((filter) => filters[filter.parameter] !== undefined);
  return rows.filter(
    (row) =>
      matchesRepository(row, term) &&
      (!production || row.production === true) &&
      active.every((filter) => filter.band(row) === filters[filter.parameter]),
  );
}

/**
 * How many production repositories the reader's OTHER filters leave, which is what the toggle counts.
 *
 * The production dimension itself is excluded from its own count — the rule the readiness bar
 * counted by before the donuts replaced it, where the counts came off
 * `filterRepositories(rows, term, null)`. A count that included its own filter would read `n` before
 * the click and `n` after it, which tells a reader nothing: the number is there to say what turning
 * the toggle on would leave.
 */
export function productionCount(
  rows: readonly RepositoryRow[],
  term: string,
  filters: RepositoryFilters,
): number {
  return filterRepositories(rows, term, filters, true).length;
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
