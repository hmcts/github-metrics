/**
 * What the team page states about a team, and what it refuses to state.
 *
 * COUNTS, NEVER A VERDICT. A team's repositories carry labels and this page lists how many carry
 * each of them — permitted from 2026-09-01 — but the counts are never reduced to one team label, one
 * score, or one position against another team. Nothing here returns a figure two teams could be
 * ordered by, which is why there is no "worst label" and no share of green.
 *
 * The repository count is of CONFIGURED repositories, and the unreported count says how many of them
 * this span holds no evidence for, so a team whose collection is half missing reads as a team with
 * missing collection rather than as a small team.
 */

import { count } from '@/lib/format';
import type { TeamDetail } from '@/lib/types';

/** What the team owns: every repository the configuration gives it, reported or not. */
export function holdings(detail: TeamDetail): string {
  return count(detail.repositories.length, 'repository', 'repositories');
}

/** How many people authored a reported merge in this team's repositories at this span. */
export function people(detail: TeamDetail): string {
  return count(detail.actors.length, 'contributor', 'contributors');
}

/**
 * How much of the team the span could not report, or nothing to say where it reported all of it.
 *
 * Stated beside the other two counts because it is what makes them readable: six repositories with
 * two unreported is a different window from six repositories with none, and the label counts under
 * the header are taken over the reported four either way.
 */
export function unreported(detail: TeamDetail): string | undefined {
  if (detail.unavailable === 0) {
    return undefined;
  }
  return `${count(detail.unavailable, 'repository', 'repositories')} not reported at this span`;
}
