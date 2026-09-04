import clsx from 'clsx';
import Link from 'next/link';
import { ProductionBadge } from '@/components/ProductionBadge';
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
 * The Production column sits directly right of Readiness, as it does on the estate table, and gains
 * no header of its own to click for the same reason the rest do not: the order here is the
 * contract's, and re-sorting a person's repositories by which of them deploy to production would
 * read as a ranking of the person by where they work.
 *
 * `blocking` re-reports occurrences a practice rule already found in that repository. It is a count of
 * findings the report already published, per repository, and it is compared against nobody else's.
 */
export function ActorRepositoriesTable({
  rows,
  teams,
  production,
  weeks,
}: {
  rows: readonly ActorRepositoryReadiness[];
  /** Repository to owning team, so a row can link its team without a second request. */
  teams: Record<string, string>;
  /**
   * Which of this person's repositories deploy to production, or nothing where no list was read.
   *
   * A LIST RATHER THAN A FLAG PER ROW, because that is the shape the service serves it in, and
   * absence of the whole prop is how "no list could be read" arrives — distinct from a list that
   * simply does not name a repository, which is a read answer of no.
   */
  production?: readonly string[];
  weeks: number;
}) {
  return (
    // No border of its own: the table sits inside a `Section` panel that already draws one.
    <div className="overflow-x-auto">
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
            <th scope="col" className="py-2 pr-3 text-left font-medium">
              Production
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
              <td className="py-2 pr-3">
                <ProductionBadge production={deploys(production, row.repository)} />
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

/**
 * Whether one repository is on the person's production list, or nothing where there is no list.
 *
 * The absent list passes straight through as an absent answer rather than becoming `false`: this
 * project's rule is that unavailable data never becomes a negative, and the badge renders nothing
 * for either — so the two look alike to a reader while staying different in the data.
 */
function deploys(
  production: readonly string[] | undefined,
  repository: string,
): boolean | undefined {
  return production === undefined ? undefined : production.includes(repository);
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
