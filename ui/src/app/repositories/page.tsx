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
import {
  checksSlices,
  coverageSlices,
  distributionSlices,
  reviewSlices,
  unreviewedSlices,
} from '@/lib/chart';
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

      {/* Five donuts over the same estate, so each one's total is the number of repositories
          configured — including the ones nothing could be measured on, which are counted in an
          unknown slice rather than dropped. None of them is filtered by the search box below, for
          the reason the readiness donut never was: they describe the estate, not the table. */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        <SummaryPieChart
          title="Readiness labels"
          // `overview.labels` distributes the reported repositories only, so the unreportable ones
          // are added here: without them this donut would total less than the four beside it.
          data={distributionSlices(overview.labels, overview.unavailable)}
          tooltip="How many repositories carry each readiness label at this span. Repositories the span could not be reported for carry no label and are counted as not assessed instead."
        />
        <SummaryPieChart
          title="Enforces review"
          data={reviewSlices(repositories)}
          tooltip="How many approving reviews each repository's merge gate requires on its default branch: multiple is two or more, required is one. An unprotected branch is counted as not required, because its gate was read and it requires nothing. Unknown is a repository whose gate was not collected, whose branch is protected and whose rules GitHub withheld, or that this span could not be reported for at all."
        />
        <SummaryPieChart
          title="Enforces CI"
          data={checksSlices(repositories)}
          tooltip="Whether each repository's merge gate requires any status check to pass on its default branch. This is the configuration: it says a check is required, not which check it is or whether it passed. Unknown is a repository whose gate was not collected, whose branch is protected and whose rules GitHub withheld, or that this span could not be reported for at all."
        />
        <SummaryPieChart
          title="Unreviewed substantial merges"
          data={unreviewedSlices(repositories)}
          tooltip="How the readiness policy graded each repository's substantial merges that reached the default branch without independent review. Within allowance means some merged unreviewed and the allowance the policy was configured with forgives them. Unknown is a repository with too few merges to grade, a window holding no substantial merge, an assessment that is switched off, or a span that could not be reported for at all."
        />
        <SummaryPieChart
          title="Test coverage"
          data={coverageSlices(repositories)}
          tooltip="The line coverage each repository's SonarCloud project reports, banded where the repository's own page bands it. Unknown is a repository that resolved to no SonarCloud project, one whose measures could not be read, one whose project sent no coverage metric, or one this span could not be reported for at all — and never a project reporting 0%."
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
