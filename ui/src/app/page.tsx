import { cookies } from 'next/headers';
import { ActorsTable } from '@/components/ActorsTable';
import { CollectionNotice } from '@/components/CollectionNotice';
import { EmptyState } from '@/components/EmptyState';
import { FilterSearchBox } from '@/components/FilterSearchBox';
import { MetricCard } from '@/components/MetricCard';
import { NavWeekSelector } from '@/components/NavWeekSelector';
import { RepositoriesTable, TERM_PARAMETER } from '@/components/RepositoriesTable';
import { Panel, Section } from '@/components/Section';
import { SummaryPieChart } from '@/components/charts/SummaryPieChart';
import { TeamsList } from '@/components/TeamsList';
import { getActors, getOverview, getRepositories, getTeams, getWindows } from '@/lib/api';
import { distributionSlices } from '@/lib/chart';
import { collectedLabel } from '@/lib/collection';
import { count, instant, span } from '@/lib/format';
import { WEEKS_COOKIE, resolveWeeks, type SearchValue } from '@/lib/weeks';

/**
 * The whole estate at one window span: what was covered, and everything there is to drill into.
 *
 * Rendered on every request. The service holds one built report per span and rebuilds it when a
 * collection lands, so a cached page here would show figures whose source has moved on with nothing
 * on the page to say so — and `cookies()` is read for the span preference besides.
 *
 * The four lists are fetched together rather than in sequence: they come from the same bundle in the
 * service, so serialising the requests would only add round trips to a report already built.
 */
export const dynamic = 'force-dynamic';

export default async function OverviewPage({
  searchParams,
}: {
  searchParams?: { weeks?: SearchValue };
}) {
  const windows = await getWindows();
  const weeks = resolveWeeks(
    searchParams?.weeks,
    cookies().get(WEEKS_COOKIE)?.value,
    windows.options,
    windows.default,
  );
  const [overview, repositories, actors, teams] = await Promise.all([
    getOverview(weeks),
    getRepositories(weeks),
    getActors(weeks),
    getTeams(weeks),
  ]);
  const window = span(overview.starts_at, overview.ends_at);
  const collected = collectedLabel(overview.collected_through);

  return (
    <div className="space-y-8">
      <CollectionNotice windows={windows} />

      <header className="bg-slate-900 border border-slate-800 rounded-lg p-5 space-y-2">
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-xs font-semibold text-slate-500 uppercase tracking-wide">
            organization
          </span>
          <h1 className="font-mono text-xl text-slate-100 break-all">{overview.organization}</h1>
          <div className="ml-auto">
            <NavWeekSelector options={windows.options} active={weeks} />
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-slate-400">
          <span>{window}</span>
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

      {/* The estate's headline figures share the panel every section is drawn on: the cards
          themselves are flat now, and four unbounded figures would float on the page background. */}
      <Panel>
        <div className="grid grid-cols-1 lg:grid-cols-4 gap-4 p-4">
          <MetricCard
            label="Repositories"
            value={overview.repositories}
            detail={
              overview.unavailable > 0
                ? `${overview.repositories - overview.unavailable} reported`
                : 'all reported'
            }
          />
          <MetricCard label="Teams" value={overview.teams} />
          <MetricCard
            label="Contributors"
            value={overview.actors}
            detail="contributed to a reported repository"
          />
          <MetricCard
            label="Merged pull requests"
            value={overview.merged_pull_requests}
            detail={`${count(overview.direct_commits, 'direct commit', 'direct commits')} besides`}
          />
        </div>
      </Panel>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <SummaryPieChart
          title="Readiness labels"
          data={distributionSlices(overview.labels)}
          tooltip="How many repositories carry each readiness label at this span. Repositories the span could not be reported for carry no label and are counted as not reported instead."
        />
      </div>

      <div id="repositories">
        <Section
          heading="Repositories"
          detail={window}
          action={
            <FilterSearchBox parameter={TERM_PARAMETER} placeholder="Filter by repository or team…" />
          }
        >
          {repositories.length === 0 ? (
            <EmptyState
              message="No repository is configured for this organisation."
              detail="Add repositories to the configuration the service was started with."
            />
          ) : (
            <RepositoriesTable rows={repositories} weeks={weeks} />
          )}
        </Section>
      </div>

      {/* The id stays `actors`: the navigation links to it and so does anything else already
          bookmarked. The heading reads CONTRIBUTORS, which is what the list is of. */}
      <div id="actors">
        <Section heading="Contributors" detail="alphabetical">
          {actors.length === 0 ? (
            <EmptyState
              message="Nobody contributed to a reported repository at this span."
              detail="Run metrics collect for the span being asked for, or widen the window."
            />
          ) : (
            <ActorsTable rows={actors} weeks={weeks} />
          )}
        </Section>
      </div>

      <div id="teams">
        <Section heading="Teams" detail={window}>
          {teams.length === 0 ? (
            <EmptyState
              message="No team is configured for this organisation."
              detail="The service refuses a configuration without teams, so this is an empty team list."
            />
          ) : (
            <TeamsList rows={teams} weeks={weeks} />
          )}
        </Section>
      </div>
    </div>
  );
}
