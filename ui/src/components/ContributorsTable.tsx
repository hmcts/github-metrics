import clsx from 'clsx';
import Link from 'next/link';
import { contributorFigures } from '@/lib/contributor';
import { quantity } from '@/lib/format';
import { directCommitTone, unreviewedMergeTone, valueClass, type Tone } from '@/lib/tone';
import type { ContributorRow } from '@/lib/types';
import { withWeeks } from '@/lib/weeks';

/**
 * Who authored this repository's window, and what each of them actually did in it.
 *
 * The service sends these contributions-descending, ties by login — the order `ActorReadiness`
 * already puts a person's repositories in, read the other way round — and this component keeps it
 * rather than imposing one of its own. It orders CHANGES, not people: the question a repository page
 * asks is which merges make up this window, and the answer to it is weightiest-first.
 *
 * THERE ARE NO SORTABLE HEADERS, and the four columns beside the login are why it matters. They are
 * counts of what was done in ONE repository — pull requests merged, pushes straight to the branch,
 * merges that carried no independent review, and the median size of a change — never a score and
 * never a rate to compare people on. A sortable header would turn the block into a league table by
 * offering to order people by the numbers, which the scope boundaries exclude. What `/contributors`
 * sorts by, from 2026-09-02, is the combination of labels its rows' repositories already carry;
 * these rows carry no label, only counts of what was done in this one repository.
 *
 * `Blocking occurrences` was dropped here on 2026-09-02: it counted the same occurrences the
 * findings table above already lists per rule and per person, and the space reads better spent on
 * what the person did than on a second copy of what a rule found.
 *
 * Only the two columns about bypassed process carry tone, from `lib/tone.ts`, and both cap at amber:
 * a direct push or an unreviewed merge is a fact to weigh, and the assessment above the table is
 * what grades the repository. Contributions and median size stay plain — neither has a better value.
 */
export function ContributorsTable({
  rows,
  weeks,
}: {
  rows: readonly ContributorRow[];
  weeks: number;
}) {
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
              Contributions
            </th>
            <th scope="col" className="py-2 pr-3 text-right font-medium">
              Merged PRs
            </th>
            <th scope="col" className="py-2 pr-3 text-right font-medium">
              Direct pushes
            </th>
            <th scope="col" className="py-2 pr-3 text-right font-medium">
              Unreviewed merges
            </th>
            {/* Named median rather than size: `pull-request-size` is a distribution and has no mean,
                so a column headed "size" would be read as one. */}
            <th scope="col" className="py-2 pr-3 text-right font-medium">
              Median size
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-800/50">
          {rows.map((row) => {
            const figures = contributorFigures(row);
            return (
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
                  {row.contributions}
                </td>
                <td className="py-2 pr-3 text-right tabular-nums text-slate-300">
                  {quantity(figures.merged)}
                </td>
                <td
                  className={clsx(
                    'py-2 pr-3 text-right tabular-nums',
                    cellClass(directCommitTone(figures.directPushes)),
                  )}
                >
                  {quantity(figures.directPushes)}
                </td>
                <td
                  className={clsx(
                    'py-2 pr-3 text-right tabular-nums',
                    cellClass(unreviewedMergeTone(figures.unreviewed)),
                  )}
                >
                  {quantity(figures.unreviewed)}
                </td>
                <td className="py-2 pr-3 text-right tabular-nums text-slate-300">{figures.size}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/**
 * The colour one toned cell is set in, keeping an uncoloured one in the table's own slate.
 *
 * `valueClass` sets a neutral figure in `text-slate-100`, which is right for a card leading on its
 * value and wrong in a row of `text-slate-300` cells: the one absent count would read as the
 * brightest number in the column. Only the three coloured tones are taken from `lib/tone.ts`.
 */
function cellClass(tone: Tone): string {
  return tone === 'neutral' ? 'text-slate-300' : valueClass(tone);
}
