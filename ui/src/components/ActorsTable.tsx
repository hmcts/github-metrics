'use client';

import Link from 'next/link';
import { useState } from 'react';
import { RAGLabel } from '@/components/RAGCard';
import { SortHeader } from '@/components/SortHeader';
import { combinationKey } from '@/lib/rag';
import { nextDirection, sorted, type Direction, type SortValue } from '@/lib/sort';
import type { ActorRow, ReadinessLabel } from '@/lib/types';
import { withWeeks } from '@/lib/weeks';

/**
 * Everyone who contributed to a reported repository in this window, by their repositories' labels.
 *
 * The list orders by a COMBINATION OF LABELS THE REPOSITORIES ALREADY CARRY, on the 2026-09-02
 * instruction that admits exactly that and nothing more: the readiness column lists the distinct
 * labels a person's repositories hold, as the text report's actor section prints them, and the sort
 * is `combinationKey` over that list. There is still no score, no metric column and no per-person
 * verdict — the repository count is how many repositories a login appears in, which is navigation.
 *
 * Ties keep the login order the service sent, because `sorted` is stable, so everybody who is all
 * green stays alphabetical among themselves.
 */
interface Column {
  key: string;
  label: string;
  numeric?: boolean;
  read: (row: ActorRow) => SortValue;
}

/** The column the list opens on, ascending: all green first, and the unlabelled last either way. */
const READINESS: Column = {
  key: 'readiness',
  label: 'Readiness',
  numeric: true,
  read: (row) => combinationKey(row.labels),
};

const COLUMNS: readonly Column[] = [
  { key: 'login', label: 'Login', read: (row) => row.login },
  { key: 'repositories', label: 'Repositories', numeric: true, read: (row) => row.repositories },
  READINESS,
];

export function ActorsTable({ rows, weeks }: { rows: readonly ActorRow[]; weeks: number }) {
  const [column, setColumn] = useState<Column>(READINESS);
  const [direction, setDirection] = useState<Direction>('ascending');

  const ordered = sorted(rows, column.read, direction);

  function sort(next: Column) {
    setDirection(nextDirection(column, next, direction));
    setColumn(next);
  }

  return (
    // No border of its own: the table sits inside a `Section` panel that already draws one.
    <div className="overflow-x-auto">
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
            <tr key={row.login} className="hover:bg-slate-800/30">
              <td className="py-2 pl-3 pr-3">
                <Link
                  href={withWeeks(`/contributors/${encodeURIComponent(row.login)}`, weeks)}
                  className="font-mono text-indigo-400 hover:text-indigo-300 break-all"
                >
                  {row.login}
                </Link>
              </td>
              <td className="py-2 pr-3 text-right tabular-nums text-slate-300">
                {row.repositories}
              </td>
              <td className="py-2 pr-3 text-right">
                <Readiness labels={row.labels} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * One person's labels as badges, flush against the table's right edge as the numeric columns are.
 *
 * SOMEBODY WITH NOTHING LEFT TO LABEL CARRIES `CANNOT ASSESS`, on the user's instruction of
 * 2026-09-02, where a dash stood until then. The dash said "no figure", which is the wrong reading:
 * the repositories behind an empty list were graded, and every one of them came back unreadable —
 * most often a merge gate a non-administrator cannot see. The badge states that in the report's own
 * vocabulary, slate rather than warm, so it is not read as a grade between amber and red.
 *
 * It stays OUT of the ordering: `combinationKey` returns nothing for an empty list, so these rows
 * sort last in both directions. `CANNOT ASSESS` has no place among green, amber and red, and "who is
 * worst" is a question about the graded people read either way round.
 *
 * A service older than 2026-09-02 sends no `labels` key at all, and reads the same way: everybody is
 * unassessable rather than the page failing to render.
 */
function Readiness({ labels }: { labels?: readonly ReadinessLabel[] }) {
  if (labels === undefined || labels.length === 0) {
    return <RAGLabel label="cannot_assess" />;
  }
  return (
    <div className="flex flex-wrap justify-end gap-1">
      {labels.map((label) => (
        <RAGLabel key={label} label={label} />
      ))}
    </div>
  );
}
