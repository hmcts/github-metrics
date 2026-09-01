import Link from 'next/link';
import type { ContributorRow } from '@/lib/types';
import { withWeeks } from '@/lib/weeks';

/**
 * Who authored this repository's window, and how much of it each of them authored.
 *
 * The service sends these contributions-descending, ties by login — the order `ActorReadiness`
 * already puts a person's repositories in, read the other way round — and this component keeps it
 * rather than imposing one of its own. It orders CHANGES, not people: the question a repository page
 * asks is which merges make up this window, and the answer to it is weightiest-first.
 *
 * There are no sortable headers for that reason. Alphabetical is the order for a list ABOUT people,
 * which is what `/actors` is; this list is about one repository, and re-sorting it by login would
 * only make the largest share harder to find.
 *
 * `blocking` re-reports occurrences a practice rule already found in this repository. It is a count
 * of findings, not a judgement of the person, and it is not compared against anybody else's.
 */
export function ContributorsTable({
  rows,
  weeks,
}: {
  rows: readonly ContributorRow[];
  weeks: number;
}) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-x-auto">
      <table className="w-full text-xs">
        <thead className="text-slate-400 border-b border-slate-800">
          <tr>
            <th scope="col" className="py-2 pl-3 pr-3 text-left font-medium">
              Login
            </th>
            <th scope="col" className="py-2 pr-3 text-right font-medium">
              Contributions
            </th>
            <th scope="col" className="py-2 pr-3 text-right font-medium">
              Blocking occurrences
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
                {row.contributions}
              </td>
              <td className="py-2 pr-3 text-right tabular-nums text-slate-300">{row.blocking}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
