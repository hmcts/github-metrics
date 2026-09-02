import Link from 'next/link';
import type { TeamActorRow } from '@/lib/types';
import { withWeeks } from '@/lib/weeks';

/**
 * Everyone who authored a reported merge in one team's repositories, alphabetically.
 *
 * ALPHABETICAL AND NOTHING ELSE: there are no sortable headers, and
 * the two figures are counts of things somebody did inside this team — how many of its repositories
 * they worked in, and how many merges they authored across them. A sum of merges is still a number of
 * merges, so it is reported; nothing is averaged, and nobody is placed above anybody else by it.
 *
 * The `/contributors` list orders by the labels its rows carry, on the 2026-09-02 instruction. These
 * rows carry none — a team row is two counts inside one team — so there is nothing here to order by
 * that would not be a ranking.
 *
 * The contributions column is scoped to THIS TEAM's repositories. Somebody who also works in another
 * team's repositories has a different count on that team's page and a third on their own, and each of
 * the three answers the question its page asks rather than being one person-wide total.
 */
export function TeamActorsTable({ rows, weeks }: { rows: readonly TeamActorRow[]; weeks: number }) {
  return (
    // No border of its own: the table sits inside a `Section` panel that already draws one.
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead className="text-slate-400 border-b border-slate-800">
          <tr>
            <th scope="col" className="py-2 pl-3 pr-3 text-left font-medium">
              Login
            </th>
            <th scope="col" className="py-2 pr-3 text-right font-medium">
              Repositories in team
            </th>
            <th scope="col" className="py-2 pr-3 text-right font-medium">
              Merges in team
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-800/50">
          {rows.map((row) => (
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
              <td className="py-2 pr-3 text-right tabular-nums text-slate-300">
                {row.contributions}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
