'use client';

import Link from 'next/link';
import { useState } from 'react';
import { RAGLabel } from '@/components/RAGCard';
import { SortHeader, type Align } from '@/components/SortHeader';
import { combinationKey } from '@/lib/rag';
import { nextDirection, sorted, type Direction, type SortValue } from '@/lib/sort';
import type { ActorRow, ReadinessLabel } from '@/lib/types';
import { withWeeks } from '@/lib/weeks';

interface Column {
  key: string;
  label: string;
  align?: Align;
  read: (row: ActorRow) => SortValue;
}

/** The column the list opens on, ascending: all green first, and the unlabelled last either way. */
const READINESS: Column = {
  key: 'readiness',
  label: 'Readiness',
  align: 'right',
  read: (row) => combinationKey(row.labels),
};

const COLUMNS: readonly Column[] = [
  { key: 'login', label: 'Login', read: (row) => row.login },
  { key: 'repositories', label: 'Repositories', align: 'right', read: (row) => row.repositories },
  READINESS,
];

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
 * green stays in the order `/actors` served them in. That order is the service's own — Python's
 * default sort over the logins — and the `Login` header re-sorts locale-aware through `compare`, so
 * a header click can move mixed-case logins relative to each other. Both are alphabetical orders and
 * neither ranks anybody; the header states which one the reader is looking at.
 *
 * `labelled` says whether the readiness policy graded anything at all in this window, which one
 * person's row cannot say on its own — see `Readiness`.
 */
export function ActorsTable({
  rows,
  weeks,
  labelled,
}: {
  rows: readonly ActorRow[];
  weeks: number;
  labelled: boolean;
}) {
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
                <Readiness labels={row.labels} labelled={labelled} />
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
 * WHICH NEEDS `labelled`, because an empty list has two causes and `domain.actor_labels` says so:
 * every repository `cannot_assess`, or a readiness policy switched off so no repository carries a
 * label at all. Only the first is what the badge above describes. Under the second, "Cannot assess"
 * would tell every reader their estate's merge gates were unreadable when nothing was ever graded —
 * and `/repositories` and `/teams` would be saying "Not assessed" about the same repositories on the
 * same deployment. So the page reads the window's label distribution once, through `anyLabelled`,
 * and an ungraded estate gets the site's own word for ungraded.
 *
 * A service older than 2026-09-02 sends no `labels` key at all, and reads the same way as an empty
 * one: unassessable rather than the page failing to render.
 */
function Readiness({ labels, labelled }: { labels?: readonly ReadinessLabel[]; labelled: boolean }) {
  if (!labelled) {
    return <RAGLabel />;
  }
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
