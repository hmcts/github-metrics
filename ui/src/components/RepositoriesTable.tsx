'use client';

import clsx from 'clsx';
import Link from 'next/link';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useState } from 'react';
import { EmptyState } from '@/components/EmptyState';
import { RAGLabel } from '@/components/RAGCard';
import { SortHeader } from '@/components/SortHeader';
import { filterTarget } from '@/lib/filter';
import { figure } from '@/lib/format';
import { RAG_DOT, RAG_LABEL, RAG_STATES, borderClass, severity, type RAGState } from '@/lib/rag';
import { filterRepositories, orderRepositories, parseState, stateCounts } from '@/lib/rows';
import { reverse, sorted, type Direction, type SortValue } from '@/lib/sort';
import type { RepositoryRow } from '@/lib/types';
import { withWeeks } from '@/lib/weeks';

/**
 * Every configured repository in one table: what was found in this window, and what was not.
 *
 * A repository the window has no evidence for keeps its row and states its reason under the name,
 * with dashes in place of counts. Dropping it would make the list read as the whole estate when it is
 * the reportable part of it, and filling zeros in would claim nothing was merged there.
 *
 * The filter term and the readiness filter both live in the URL, so a filtered table is a thing that
 * can be reloaded and shared. Sorting stays in component state: it is how one reader is looking at
 * the list right now, not a fact about the window worth sending to somebody.
 */
export const TERM_PARAMETER = 'repository';

export const LABEL_PARAMETER = 'label';

interface Column {
  key: string;
  label: string;
  numeric?: boolean;
  read: (row: RepositoryRow) => SortValue;
}

const COLUMNS: readonly Column[] = [
  { key: 'team', label: 'Team', read: (row) => row.team },
  { key: 'repository', label: 'Repository', read: (row) => row.repository },
  { key: 'readiness', label: 'Readiness', read: (row) => severity(row.readiness) },
  { key: 'merged', label: 'Merged', numeric: true, read: (row) => row.merged_pull_requests },
  { key: 'direct', label: 'Direct commits', numeric: true, read: (row) => row.direct_commits },
  { key: 'open', label: 'Open', numeric: true, read: (row) => row.currently_open },
  { key: 'stale', label: 'Stale', numeric: true, read: (row) => row.stale_open },
  { key: 'findings', label: 'Findings', numeric: true, read: (row) => row.finding_occurrences },
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
  const label = parseState(searchParameters.get(LABEL_PARAMETER));
  const found = filterRepositories(rows, term, label);
  const ordered = column === null ? orderRepositories(found) : sorted(found, column.read, direction);
  const counts = stateCounts(filterRepositories(rows, term, null));

  function sort(next: Column) {
    setDirection(next === column ? reverse(direction) : 'ascending');
    setColumn(next);
  }

  function choose(readiness: RAGState) {
    // Clicking the active chip clears the filter, which is the parameter's absence — the same
    // navigation the search box makes when its box is emptied.
    const chosen = readiness === label ? '' : readiness;
    router.replace(filterTarget(pathname, window.location.search, LABEL_PARAMETER, chosen), {
      scroll: false,
    });
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Readiness filter">
        {RAG_STATES.map((readiness) => (
          <button
            key={readiness}
            type="button"
            onClick={() => choose(readiness)}
            aria-pressed={readiness === label}
            className={clsx(
              'flex items-center gap-1.5 rounded px-2 py-1 text-xs transition-colors',
              'focus:outline-none focus-visible:ring-1 focus-visible:ring-indigo-500',
              readiness === label
                ? 'bg-slate-700 text-slate-100'
                : 'bg-slate-800 text-slate-400 hover:bg-slate-700 hover:text-slate-200',
            )}
          >
            <span
              className={clsx('shrink-0 w-2 h-2 rounded-full', RAG_DOT[readiness])}
              aria-hidden="true"
            />
            {RAG_LABEL[readiness]}
            <span className="tabular-nums text-slate-500">{counts[readiness]}</span>
          </button>
        ))}
      </div>

      {ordered.length === 0 ? (
        <EmptyState
          message="No repository matches this filter."
          detail="Clear the term or the readiness filter to see the whole estate."
        />
      ) : (
        <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-slate-400 border-b border-slate-800">
              <tr>
                {COLUMNS.map((entry) => (
                  <SortHeader
                    key={entry.key}
                    label={entry.label}
                    active={entry === column}
                    direction={direction}
                    numeric={entry.numeric}
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

/** One numeric cell: the figure the service observed, or a dash where it observed none. */
function Figure({ value }: { value?: number }) {
  return <td className="py-2 pr-3 text-right tabular-nums text-slate-300">{figure(value)}</td>;
}
