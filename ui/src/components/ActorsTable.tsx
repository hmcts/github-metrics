import Link from 'next/link';
import type { ActorRow } from '@/lib/types';
import { withWeeks } from '@/lib/weeks';

/**
 * Everyone who contributed to a reported repository in this window, alphabetically.
 *
 * ALPHABETICAL AND NOTHING ELSE. There are no sortable headers here and no metric column, because
 * ranking people by any figure is outside this project's scope: the repository count is how many
 * repositories a login appears in, which is navigation rather than a score. The service already
 * returns the rows in login order, so this component does not reorder them either.
 */
export function ActorsTable({ rows, weeks }: { rows: readonly ActorRow[]; weeks: number }) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-x-auto">
      <table className="w-full text-xs">
        <thead className="text-slate-400 border-b border-slate-800">
          <tr>
            <th scope="col" className="py-2 pl-3 pr-3 text-left font-medium">
              Login
            </th>
            <th scope="col" className="py-2 pr-3 text-right font-medium">
              Repositories
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-800/50">
          {rows.map((row) => (
            <tr key={row.login} className="hover:bg-slate-800/30">
              <td className="py-2 pl-3 pr-3">
                <Link
                  href={withWeeks(`/actors/${encodeURIComponent(row.login)}`, weeks)}
                  className="font-mono text-indigo-400 hover:text-indigo-300 break-all"
                >
                  {row.login}
                </Link>
              </td>
              <td className="py-2 pr-3 text-right tabular-nums text-slate-300">
                {row.repositories}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
