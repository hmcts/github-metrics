import { cookies } from 'next/headers';
import { CollectionNotice } from '@/components/CollectionNotice';
import { EmptyState } from '@/components/EmptyState';
import { FilterSearchBox } from '@/components/FilterSearchBox';
import { MetricCard } from '@/components/MetricCard';
import { NavWeekSelector } from '@/components/NavWeekSelector';
import { OrganisationHeader } from '@/components/OrganisationHeader';
import { RepositoriesTable, TERM_PARAMETER } from '@/components/RepositoriesTable';
import { Panel, Section } from '@/components/Section';
import { SummaryPieChart } from '@/components/charts/SummaryPieChart';
import { getOverview, getRepositories, getWindows } from '@/lib/api';
import { distributionSlices } from '@/lib/chart';
import { count, span } from '@/lib/format';
import { WEEKS_COOKIE, resolveWeeks, type SearchValue } from '@/lib/weeks';

/**
 * The estate at one window span: what was covered, how it is labelled, and every repository in it.
 *
 * The landing page, from 2026-09-02: the three lists that used to be sections of one overview are
 * three routes now, and this is the one a reader arrives at. The estate figures and the readiness
 * donut stay here rather than being repeated on the other two — they are counts over repositories,
 * which is what this page is a list of.
 *
 * Rendered on every request. The service holds one built report per span and rebuilds it when a
 * collection lands, so a cached page here would show figures whose source has moved on with nothing
 * on the page to say so — and `cookies()` is read for the span preference besides.
 */
export const dynamic = 'force-dynamic';

export default async function RepositoriesPage({
  searchParams,
}: {
  searchParams?: Promise<{ weeks?: SearchValue }>;
}) {
  const windows = await getWindows();
  const weeks = resolveWeeks(
    (await searchParams)?.weeks,
    (await cookies()).get(WEEKS_COOKIE)?.value,
    windows.options,
    windows.default,
  );
  // Two requests against one bundle, fetched together rather than in sequence: they come from the
  // same built report in the service, so serialising them would only add a round trip to it.
  const [overview, repositories] = await Promise.all([getOverview(weeks), getRepositories(weeks)]);
  const window = span(overview.starts_at, overview.ends_at);

  return (
    <div className="space-y-8">
      <CollectionNotice windows={windows} />

      <OrganisationHeader
        overview={overview}
        action={<NavWeekSelector options={windows.options} active={weeks} />}
      />

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
  );
}
