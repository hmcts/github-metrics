'use client';

import clsx from 'clsx';
import { X } from 'lucide-react';
import Link from 'next/link';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useState } from 'react';
import { EmptyState } from '@/components/EmptyState';
import { RAGLabel } from '@/components/RAGCard';
import { SortHeader, type Align } from '@/components/SortHeader';
import { filterTarget } from '@/lib/filter';
import { ABSENT, figure } from '@/lib/format';
import { borderClass, severity } from '@/lib/rag';
import {
  ESTATE_FILTERS,
  answerOrder,
  codeownersPresent,
  filterRepositories,
  orderRepositories,
  parseFilters,
  type EstateFilter,
  type FilterOption,
} from '@/lib/rows';
import { nextDirection, sorted, type Direction, type SortValue } from '@/lib/sort';
import type { RepositoryRow } from '@/lib/types';
import { withWeeks } from '@/lib/weeks';

/**
 * Every configured repository in one table: what was found in this window, and what was not.
 *
 * A repository the window has no evidence for keeps its row and states its reason under the name,
 * with dashes in place of counts. Dropping it would make the list read as the whole estate when it is
 * the reportable part of it, and filling zeros in would claim nothing was merged there.
 *
 * The filter term and every donut's filter live in the URL, so a filtered table is a thing that can
 * be reloaded and shared. Sorting stays in component state: it is how one reader is looking at the
 * list right now, not a fact about the window worth sending to somebody.
 *
 * The table owns no filter control of its own. The donuts above it are the controls, and this row
 * shows what they have been set to: one chip per filtered dimension, each dismissable on its own, so
 * a reader who has stacked three of them can see all three and drop the one they did not mean.
 */
export const TERM_PARAMETER = 'repository';

export const LABEL_PARAMETER = 'label';

interface Column {
  key: string;
  label: string;
  align?: Align;
  read: (row: RepositoryRow) => SortValue;
}

const COLUMNS: readonly Column[] = [
  { key: 'team', label: 'Team', read: (row) => row.team },
  { key: 'repository', label: 'Repository', read: (row) => row.repository },
  { key: 'readiness', label: 'Readiness', read: (row) => severity(row.readiness) },
  { key: 'merged', label: 'Merged', align: 'right', read: (row) => row.merged_pull_requests },
  { key: 'direct', label: 'Direct commits', align: 'right', read: (row) => row.direct_commits },
  { key: 'open', label: 'Open', align: 'right', read: (row) => row.currently_open },
  { key: 'stale', label: 'Stale', align: 'right', read: (row) => row.stale_open },
  // Both governance answers sort on `answerOrder`, the value their own cell prints, so a header
  // click orders the column a reader is looking at rather than the count behind it: CODEOWNERS is
  // Yes at one file and at forty alike, and Sonar has no count at all. They centre rather than
  // right-align: Yes, No and a dash are words, and a right edge would read them as figures.
  {
    key: 'codeowners',
    label: 'CODEOWNERS',
    align: 'center',
    read: (row) => answerOrder(codeownersPresent(row)),
  },
  {
    key: 'sonar',
    label: 'Sonar',
    align: 'center',
    read: (row) => answerOrder(row.sonar_reported),
  },
  { key: 'findings', label: 'Findings', align: 'right', read: (row) => row.finding_occurrences },
];

export function RepositoriesTable({
  rows,
  weeks,
}: {
  rows: readonly RepositoryRow[];
  weeks: number;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParameters = useSearchParams();
  const [column, setColumn] = useState<Column | null>(null);
  const [direction, setDirection] = useState<Direction>('ascending');

  const term = searchParameters.get(TERM_PARAMETER) ?? '';
  const filters = parseFilters((parameter) => searchParameters.get(parameter));
  const found = filterRepositories(rows, term, filters);
  const ordered = column === null ? orderRepositories(found) : sorted(found, column.read, direction);
  const chips = activeChips(filters);

  function sort(next: Column) {
    setDirection(nextDirection(column, next, direction));
    setColumn(next);
  }

  function clear(parameter: string) {
    // Clearing a filter is the parameter's absence rather than an empty value, which would read back
    // as a filter for the empty string. `window.location.search`, so the other chips, the term and
    // the span all come through — this drops one dimension, not the reader's whole view.
    router.replace(filterTarget(pathname, window.location.search, parameter, ''), {
      scroll: false,
    });
  }

  return (
    <div className="space-y-3">
      {chips.length > 0 ? (
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Active filters">
          {chips.map(({ filter, option }) => (
            <span
              key={filter.parameter}
              className="flex items-center gap-1.5 rounded bg-slate-800 py-1 pl-2 pr-1 text-xs text-slate-200"
            >
              <span
                className="shrink-0 w-2 h-2 rounded-full"
                style={{ backgroundColor: option.color }}
                aria-hidden="true"
              />
              <span className="text-slate-400 uppercase tracking-wide">{`${filter.title}: `}</span>
              {option.name}
              <button
                type="button"
                onClick={() => clear(filter.parameter)}
                aria-label={`Remove ${filter.title} filter`}
                className="rounded text-slate-500 transition-colors hover:text-slate-200 focus:outline-none focus-visible:ring-1 focus-visible:ring-indigo-500"
              >
                <X className="w-3.5 h-3.5" aria-hidden="true" />
              </button>
            </span>
          ))}
        </div>
      ) : null}

      {ordered.length === 0 ? (
        <EmptyState
          message="No repository matches this filter."
          detail="Clear the term, or a filter above, to see the whole estate."
        />
      ) : (
        // No border of its own: the table sits inside a `Section` panel that already draws one.
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-slate-400 border-b border-slate-800">
              <tr>
                {COLUMNS.map((entry, index) => (
                  <SortHeader
                    key={entry.key}
                    label={entry.label}
                    active={entry === column}
                    direction={direction}
                    align={entry.align}
                    first={index === 0}
                    onSort={() => sort(entry)}
                  />
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/50">
              {ordered.map((row) => (
                <tr key={row.repository} className="hover:bg-slate-800/30">
                  <td className={clsx('py-2 pl-3 pr-3', borderClass(row.readiness))}>
                    <Link
                      href={withWeeks(`/teams/${encodeURIComponent(row.team)}`, weeks)}
                      className="text-indigo-400 hover:text-indigo-300"
                    >
                      {row.team}
                    </Link>
                  </td>
                  <td className="py-2 pr-3">
                    <Link
                      href={withWeeks(`/repositories/${encodeURIComponent(row.repository)}`, weeks)}
                      className="font-mono text-indigo-400 hover:text-indigo-300 break-all"
                    >
                      {row.repository}
                    </Link>
                    {row.detail ? <p className="text-slate-500 mt-0.5">{row.detail}</p> : null}
                  </td>
                  <td className="py-2 pr-3">
                    <RAGLabel label={row.readiness} />
                  </td>
                  <Figure value={row.merged_pull_requests} />
                  <Figure value={row.direct_commits} />
                  <Figure value={row.currently_open} />
                  <Figure value={row.stale_open} />
                  <Answer value={codeownersPresent(row)} />
                  <Answer value={row.sonar_reported} />
                  <Figure value={row.finding_occurrences} />
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/**
 * The dimensions filtered right now, each with the option it is filtered to.
 *
 * In `ESTATE_FILTERS` order rather than in the order the parameters were clicked, so the chips sit
 * in the order the donuts above them do and a chip does not move when another is dropped. A
 * dimension whose value named no option was already dropped by `parseFilters`, which is why the
 * lookup here cannot come back empty.
 */
function activeChips(
  filters: ReturnType<typeof parseFilters>,
): { filter: EstateFilter; option: FilterOption }[] {
  return ESTATE_FILTERS.flatMap((filter) => {
    const option = filter.options.find((entry) => entry.key === filters[filter.parameter]);
    return option === undefined ? [] : [{ filter, option }];
  });
}

/** One numeric cell: the figure the service observed, or a dash where it observed none. */
function Figure({ value }: { value?: number }) {
  return <td className="py-2 pr-3 text-right tabular-nums text-slate-300">{figure(value)}</td>;
}

/**
 * One yes-or-no cell, dashed where the answer could not be read.
 *
 * Untoned, as every cell in this table is: neither answer is a grade. A repository with no
 * CODEOWNERS file may be owned perfectly well by a rule the collector cannot see, and whether Sonar
 * reported anything is not itself good or bad news.
 *
 * Centred under its header, which the column declares as `center` — a word in a column of words is
 * not a figure to be read down a right edge.
 */
function Answer({ value }: { value?: boolean }) {
  return (
    <td className="py-2 pr-3 text-center text-slate-300">
      {value === undefined ? ABSENT : value ? 'Yes' : 'No'}
    </td>
  );
}
