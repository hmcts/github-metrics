import { cookies } from 'next/headers';
import { ActorsTable } from '@/components/ActorsTable';
import { CollectionNotice } from '@/components/CollectionNotice';
import { EmptyState } from '@/components/EmptyState';
import { NavWeekSelector } from '@/components/NavWeekSelector';
import { OrganisationHeader } from '@/components/OrganisationHeader';
import { Section } from '@/components/Section';
import { getActors, getOverview, getWindows } from '@/lib/api';
import { WEEKS_COOKIE, resolveWeeks, type SearchValue } from '@/lib/weeks';

/**
 * Everyone who contributed to a reported repository at one window span, alphabetically.
 *
 * A route of its own from 2026-09-02, where it was a section of the overview before. The page carries
 * the organisation header and the list and nothing else: the estate figures and the readiness donut
 * are counts over repositories and stay on the page that lists them.
 *
 * Three requests where the overview made five — `/windows` for the spans, `/overview` for what the
 * header states, and `/actors` for the rows. `/overview` is fetched for the header rather than the
 * figures, which is a request this page would otherwise not need; it comes from the same built bundle
 * as the list, so it costs a round trip and no rebuild.
 */
export const dynamic = 'force-dynamic';

export default async function ContributorsPage({
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
  const [overview, actors] = await Promise.all([getOverview(weeks), getActors(weeks)]);

  return (
    <div className="space-y-8">
      <CollectionNotice windows={windows} />

      <OrganisationHeader
        overview={overview}
        action={<NavWeekSelector options={windows.options} active={weeks} />}
      />

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
  );
}
