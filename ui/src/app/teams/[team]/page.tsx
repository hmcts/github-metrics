import { cookies } from 'next/headers';
import { notFound } from 'next/navigation';
import { CollectionNotice } from '@/components/CollectionNotice';
import { EmptyState } from '@/components/EmptyState';
import { EntityHeader } from '@/components/EntityHeader';
import { FilterSearchBox } from '@/components/FilterSearchBox';
import { NavWeekSelector } from '@/components/NavWeekSelector';
import { RepositoriesTable, TERM_PARAMETER } from '@/components/RepositoriesTable';
import { Section } from '@/components/Section';
import { SummaryPieChart } from '@/components/charts/SummaryPieChart';
import { TeamActorsTable } from '@/components/TeamActorsTable';
import { getTeam, getWindows, isNotFound } from '@/lib/api';
import { distributionSlices } from '@/lib/chart';
import { holdings, people, unreported } from '@/lib/team';
import type { TeamDetail } from '@/lib/types';
import { WEEKS_COOKIE, resolveWeeks, type SearchValue } from '@/lib/weeks';

/**
 * One team's window: what it owns, how those repositories are labelled, and who worked in them.
 *
 * The label distribution is a count per label and stops there. There is no combined team label, no
 * team score, and no comparison against another team anywhere on this page — the reversal of
 * 2026-09-01 permitted per-team COUNTS for display and nothing beyond them (architecture.md, "Scope
 * boundaries"). The contributor list is alphabetical for the same reason the `/contributors` list is.
 *
 * The repositories table is the shared component, handed this team's rows: a team page and the
 * repositories list then agree about what a repository row says, and the filter and readiness chips work here
 * exactly as they do there.
 */
export const dynamic = 'force-dynamic';

export default async function TeamPage({
  params,
  searchParams,
}: {
  params: { team: string };
  searchParams?: { weeks?: SearchValue };
}) {
  const windows = await getWindows();
  const weeks = resolveWeeks(
    searchParams?.weeks,
    cookies().get(WEEKS_COOKIE)?.value,
    windows.options,
    windows.default,
  );
  const detail = await readTeam(params.team, weeks);
  const missing = unreported(detail);

  return (
    <div className="space-y-8">
      <CollectionNotice windows={windows} />

      <EntityHeader
        kind="team"
        name={detail.team}
        action={<NavWeekSelector options={windows.options} active={weeks} />}
        context={
          <>
            <span>{holdings(detail)}</span>
            <span>{people(detail)}</span>
            {missing ? <span className="text-slate-500">{missing}</span> : null}
          </>
        }
      />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <SummaryPieChart
          title="Readiness labels"
          data={distributionSlices(detail.labels)}
          tooltip="How many of this team’s repositories carry each readiness label at this span. The counts are per repository: they are not combined into a label for the team, and no team is ranked against another."
        />
      </div>

      <Section
        heading="Repositories"
        detail="team then repository"
        action={
          <FilterSearchBox parameter={TERM_PARAMETER} placeholder="Filter by repository…" />
        }
      >
        {detail.repositories.length === 0 ? (
          <EmptyState
            message={`No repository is configured for ${detail.team}.`}
            detail="Add repositories to this team in the configuration the service was started with."
          />
        ) : (
          <RepositoriesTable rows={detail.repositories} weeks={weeks} />
        )}
      </Section>

      <Section
        heading="Contributors"
        detail="alphabetical, counted within this team’s repositories"
      >
        {detail.actors.length === 0 ? (
          <EmptyState
            message={`Nobody authored a reported merge in ${detail.team}’s repositories at this span.`}
            detail="Read the team at a longer span, or run metrics collect for the span being asked for."
          />
        ) : (
          <TeamActorsTable rows={detail.actors} weeks={weeks} />
        )}
      </Section>
    </div>
  );
}

/**
 * Read one team, answering not-found for an identifier the configuration does not hold.
 *
 * Only a 404 becomes a not-found page: a team is configured or it is not, and every other refusal is
 * a fault in the service or the network that should surface as one rather than as a team nobody has
 * heard of.
 */
async function readTeam(team: string, weeks: number): Promise<TeamDetail> {
  try {
    return await getTeam(team, weeks);
  } catch (error) {
    if (isNotFound(error)) {
      notFound();
    }
    throw error;
  }
}
