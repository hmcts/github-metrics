import { cookies } from 'next/headers';
import { CollectionNotice } from '@/components/CollectionNotice';
import { EmptyState } from '@/components/EmptyState';
import { NavWeekSelector } from '@/components/NavWeekSelector';
import { OrganisationHeader } from '@/components/OrganisationHeader';
import { Section } from '@/components/Section';
import { TeamsList } from '@/components/TeamsList';
import { getOverview, getTeams, getWindows } from '@/lib/api';
import { span } from '@/lib/format';
import { WEEKS_COOKIE, resolveWeeks, type SearchValue } from '@/lib/weeks';

/**
 * Every configured team at one window span, with the readiness labels its repositories carry.
 *
 * A route of its own from 2026-09-02, where it was a section of the overview before. The cards state
 * label COUNTS per team and stop there: there is no combined team label, no team score and no
 * comparison of one team against another anywhere here (architecture.md, "Scope boundaries").
 *
 * Three requests where the overview made five — `/windows`, `/overview` for the header, and `/teams`
 * for the cards — all against the one bundle the service already holds for this span.
 */
export const dynamic = 'force-dynamic';

export default async function TeamsPage({
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
  const [overview, teams] = await Promise.all([getOverview(weeks), getTeams(weeks)]);

  return (
    <div className="space-y-8">
      <CollectionNotice windows={windows} />

      <OrganisationHeader
        overview={overview}
        action={<NavWeekSelector options={windows.options} active={weeks} />}
      />

      <Section heading="Teams" detail={span(overview.starts_at, overview.ends_at)}>
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
  );
}
