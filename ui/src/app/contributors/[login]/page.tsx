import { cookies } from 'next/headers';
import { notFound } from 'next/navigation';
import { ActorRepositoriesTable } from '@/components/ActorRepositoriesTable';
import { CollectionNotice } from '@/components/CollectionNotice';
import { EmptyState } from '@/components/EmptyState';
import { EntityHeader } from '@/components/EntityHeader';
import { MetricsGrid } from '@/components/MetricsGrid';
import { NavWeekSelector } from '@/components/NavWeekSelector';
import { Section } from '@/components/Section';
import { activity, measured } from '@/lib/actor';
import { getActor, getWindows, isNotFound } from '@/lib/api';
import type { ActorDetail } from '@/lib/types';
import { WEEKS_COOKIE, resolveWeeks, type SearchValue } from '@/lib/weeks';

/**
 * One person's window: which repositories they worked in, and what was measured in each of them.
 *
 * The page states counts and lists observations. It carries no per-person verdict, no score, and no
 * comparison against anybody else, because there is nothing here to compare: the behaviour summaries
 * are measured per repository and stay in their own section, so a rate from one repository is never
 * averaged with a rate from another (architecture.md, "Scope boundaries").
 *
 * The login in the URL is matched case-insensitively by the service — a GitHub login is unique
 * case-insensitively — and the page displays the spelling the report uses rather than the spelling
 * that was typed, so two links to the same person read as one page about them.
 */
export const dynamic = 'force-dynamic';

export default async function ActorPage({
  params,
  searchParams,
}: {
  params: Promise<{ login: string }>;
  searchParams?: Promise<{ weeks?: SearchValue }>;
}) {
  const windows = await getWindows();
  const weeks = resolveWeeks(
    (await searchParams)?.weeks,
    (await cookies()).get(WEEKS_COOKIE)?.value,
    windows.options,
    windows.default,
  );
  const detail = await readActor((await params).login, weeks);
  const actor = detail.actor;

  return (
    <div className="space-y-8">
      <CollectionNotice windows={windows} />

      <EntityHeader
        kind="contributor"
        name={actor.actor_login}
        action={<NavWeekSelector options={windows.options} active={weeks} />}
        context={<span>{activity(actor)}</span>}
      />

      <Section heading="Repositories" detail="the contributions the window holds, weightiest first">
        {actor.repositories.length === 0 ? (
          <EmptyState
            message={`${actor.actor_login} authored no reported merge at this span.`}
            detail="Read this person at a longer span, or run metrics collect for the span being asked for."
          />
        ) : (
          <ActorRepositoriesTable
            rows={actor.repositories}
            teams={detail.teams}
            production={detail.production}
            weeks={weeks}
          />
        )}
      </Section>

      {actor.repositories.map((row) => (
        <Section
          key={row.repository}
          heading={`Behaviour in ${row.repository}`}
          detail={measured(row)}
        >
          <MetricsGrid
            summaries={row.metrics}
            empty={`No behaviour metric could be measured over ${actor.actor_login}’s merges in ${row.repository}.`}
          />
        </Section>
      ))}
    </div>
  );
}

/**
 * Read one person, answering not-found for a login nobody in this window is spelled with.
 *
 * Only a 404 becomes a not-found page. Unlike a repository, a contributor is not configured
 * anywhere: the set of them is whoever authored a reported merge, so a login with no contributions
 * in the window genuinely has no page here, and the service says so rather than the page inventing
 * an empty one.
 */
async function readActor(login: string, weeks: number): Promise<ActorDetail> {
  try {
    return await getActor(login, weeks);
  } catch (error) {
    if (isNotFound(error)) {
      notFound();
    }
    throw error;
  }
}
