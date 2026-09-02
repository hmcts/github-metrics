import { collectedLabel } from '@/lib/collection';
import { count, instant, span } from '@/lib/format';
import type { OverviewSummary } from '@/lib/types';

/**
 * The head of each estate list: which organisation, at which span, built from which collection.
 *
 * One component rather than a copy per list page, from 2026-09-02 when the three lists became three
 * routes. Each of them states the same window over a different third of the same bundle, so a header
 * kept three times could claim three different things about one span — and the claim is the point of
 * the block. `EntityHeader` is the same block for a page about one repository, contributor or team,
 * and this takes its `action` the same way, so the two headers behave alike where they overlap.
 *
 * The organisation is named in mono, as every entity name on this site is: it is an identifier
 * somebody wrote in a configuration file, not a title.
 */
export function OrganisationHeader({
  overview,
  action,
}: {
  overview: OverviewSummary;
  /** The header's own control — the week selector, which every page carries at the top right. */
  action?: React.ReactNode;
}) {
  const collected = collectedLabel(overview.collected_through);

  return (
    <header className="bg-slate-900 border border-slate-800 rounded-lg p-5 space-y-2">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-xs font-semibold text-slate-500 uppercase tracking-wide">
          organization
        </span>
        <h1 className="font-mono text-xl text-slate-100 break-all">{overview.organization}</h1>
        {action ? <div className="ml-auto">{action}</div> : null}
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-slate-400">
        <span>{span(overview.starts_at, overview.ends_at)}</span>
        <span>{count(overview.weeks, 'week', 'weeks')}</span>
        {/* Beside the window rather than in place of the build stamp: the window ends where the
            caches end, and the two instants answer different questions — what the figures cover,
            and when this bundle was assembled from them. */}
        {collected ? <span>{collected}</span> : null}
        <span className="text-slate-500">Report built {instant(overview.built_at)}</span>
        {overview.unavailable > 0 ? (
          <span className="text-slate-500">
            {count(overview.unavailable, 'repository', 'repositories')} not reported at this span
          </span>
        ) : null}
      </div>
    </header>
  );
}
