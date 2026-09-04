import Link from 'next/link';
import { percent } from '@/lib/format';
import type { PracticeFinding } from '@/lib/types';
import { withWeeks } from '@/lib/weeks';

/**
 * What the practice rules found in one repository's window, and the merges behind each finding.
 *
 * ROWS ARRIVE BY RULE, THEN ALPHABETICAL BY CONTRIBUTOR, and the table keeps that order and offers
 * no sortable headers. A finding names a person, so ordering by occurrences would produce a list of
 * people ranked by how often a rule fired on their work: the personal ranking the scope boundaries
 * exclude, and it would appear by default, before anyone chose to look at it that way.
 *
 * Every occurrence links to the pull request it was found in. A rule that fires on six merges is an
 * assertion until the six merges are reachable; the links are what make a finding arguable, and a
 * finding nobody can check is one nobody should be asked to act on.
 */
export function FindingsTable({
  findings,
  weeks,
}: {
  findings: readonly PracticeFinding[];
  weeks: number;
}) {
  return (
    // No border of its own: the table sits inside a `Section` panel that already draws one.
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead className="text-slate-400 border-b border-slate-800">
          <tr>
            <th scope="col" className="py-2 pl-3 pr-3 text-left font-medium">
              Rule
            </th>
            <th scope="col" className="py-2 pr-3 text-left font-medium">
              Severity
            </th>
            <th scope="col" className="py-2 pr-3 text-left font-medium">
              Contributor
            </th>
            <th scope="col" className="py-2 pr-3 text-right font-medium">
              Occurrences
            </th>
            <th scope="col" className="py-2 pr-3 text-right font-medium">
              Authored
            </th>
            <th scope="col" className="py-2 pr-3 text-right font-medium">
              Share
            </th>
            <th scope="col" className="py-2 pr-3 text-left font-medium">
              Merges
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-800/50">
          {findings.map((finding) => (
            <tr key={`${finding.rule}/${finding.actor_login}`} className="hover:bg-slate-800/30">
              <td className="py-2 pl-3 pr-3 align-top">
                <span className="text-slate-200">{finding.rule}</span>
                <p className="text-slate-500 mt-0.5">{finding.message}</p>
              </td>
              <td className="py-2 pr-3 align-top uppercase tracking-wide text-slate-400">
                {finding.severity}
              </td>
              <td className="py-2 pr-3 align-top">
                <Link
                  href={withWeeks(`/contributors/${encodeURIComponent(finding.actor_login)}`, weeks)}
                  className="font-mono text-indigo-400 hover:text-indigo-300 break-all"
                >
                  {finding.actor_login}
                </Link>
              </td>
              <td className="py-2 pr-3 align-top text-right tabular-nums text-slate-300">
                {finding.occurrences}
              </td>
              <td className="py-2 pr-3 align-top text-right tabular-nums text-slate-300">
                {finding.authored_merges}
              </td>
              <td className="py-2 pr-3 align-top text-right tabular-nums text-slate-300">
                {percent(finding.percentage)}
              </td>
              <td className="py-2 pr-3 align-top">
                <div className="flex flex-wrap gap-x-2 gap-y-1">
                  {finding.pull_requests.map((reference) => (
                    <a
                      key={reference.url}
                      href={reference.url}
                      className="font-mono text-indigo-400 hover:text-indigo-300"
                    >
                      #{reference.number}
                    </a>
                  ))}
                  {(finding.direct_commits ?? []).map((reference) => (
                    <a
                      key={reference.url}
                      href={reference.url}
                      // A direct commit has no number to cite, so the short SHA stands in for one;
                      // it is labelled as a commit because a rule firing on a commit that bypassed
                      // review is a different finding from the same rule firing on a pull request.
                      className="font-mono text-indigo-400 hover:text-indigo-300"
                    >
                      commit {reference.sha.slice(0, 7)}
                    </a>
                  ))}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
