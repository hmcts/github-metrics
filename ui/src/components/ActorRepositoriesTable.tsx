import clsx from 'clsx';
import Link from 'next/link';
import { RAGLabel } from '@/components/RAGCard';
import { team } from '@/lib/actor';
import { borderClass } from '@/lib/rag';
import type { ActorRepositoryReadiness } from '@/lib/types';
import { withWeeks } from '@/lib/weeks';

/**
 * Which repositories one person worked in this window, and how each of them is labelled.
 *
 * The rows arrive contributions-descending, ties by repository name — the contract's own order, which
 * answers "what is this person working in" — and this component keeps it. There are no sortable
 * headers: the readiness column LISTS each repository's label and re-sorting the list by it would read
 * as a grade of the person for working in a red repository, which is a verdict this project does not
 * make. The labels are also never combined: somebody in a red repository and a green one has two
 * labels, not an average of them.
 *
 * `blocking` re-reports occurrences a practice rule already found in that repository. It is a count of
 * findings the report already published, per repository, and it is compared against nobody else's.
 */
export function ActorRepositoriesTable({
  rows,
  teams,
  weeks,
}: {
  rows: readonly ActorRepositoryReadiness[];
  /** Repository to owning team, so a row can link its team without a second request. */
  teams: Record<string, string>;
  weeks: number;
}) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-x-auto">
      <table className="w-full text-xs">
        <thead className="text-slate-400 border-b border-slate-800">
          <tr>
            <th scope="col" className="py-2 pl-3 pr-3 text-left font-medium">
              Repository
            </th>
            <th scope="col" className="py-2 pr-3 text-left font-medium">
              Team
            </th>
            <th scope="col" className="py-2 pr-3 text-left font-medium">
              Readiness
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
            <tr key={row.repository} className="hover:bg-slate-800/30">
              <td className={clsx('py-2 pl-3 pr-3', borderClass(row.readiness))}>
                <Link
                  href={withWeeks(`/repositories/${encodeURIComponent(row.repository)}`, weeks)}
                  className="font-mono text-indigo-400 hover:text-indigo-300 break-all"
                >
                  {row.repository}
                </Link>
              </td>
              <td className="py-2 pr-3">
                <TeamCell team={team(teams, row.repository)} weeks={weeks} />
              </td>
              <td className="py-2 pr-3">
                <RAGLabel label={row.readiness} />
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

/** The owning team as a link, or a stated absence where the service named none. */
function TeamCell({ team: owner, weeks }: { team: string | undefined; weeks: number }) {
  if (owner === undefined) {
    return <span className="text-slate-500">no owning team was reported</span>;
  }
  return (
    <Link
      href={withWeeks(`/teams/${encodeURIComponent(owner)}`, weeks)}
      className="text-indigo-400 hover:text-indigo-300"
    >
      {owner}
    </Link>
  );
}
