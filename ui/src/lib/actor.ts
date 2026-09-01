/**
 * What one person's contract row says, reduced to the counts and words the actor page shows.
 *
 * COUNTS, NEVER SCORES. Merges are summed across the repositories somebody contributed to because a
 * sum of merges is still a number of merges: it says how much of this window they authored. Nothing
 * else here is combined — a label, a rate or a median from two repositories is never averaged, which
 * is the cross-repository averaging the scope boundaries exclude, and no figure produced here orders
 * one person against another.
 *
 * The repositories arrive in the contract's own order — contributions descending, ties by name — and
 * these functions preserve it. It answers "what is this person working in", which is the question the
 * page exists for, and is not a ranking of the repositories either.
 */

import { count } from '@/lib/format';
import type { ActorReadiness, ActorRepositoryReadiness } from '@/lib/types';

/** How many merges this person authored in the window, added up over their repositories. */
export function merges(actor: ActorReadiness): number {
  return actor.repositories.reduce((total, row) => total + row.contributions, 0);
}

/**
 * The header's one line about this person: what they authored, and how widely.
 *
 * Both halves are counts of things, stated together so neither reads as a rating. "8 merges across 1
 * repository" and "8 merges across 6 repositories" are different windows, and either figure alone
 * would hide which of the two is being read.
 */
export function activity(actor: ActorReadiness): string {
  const across = count(actor.repositories.length, 'repository', 'repositories');
  return `${count(merges(actor), 'merge', 'merges')} across ${across}`;
}

/**
 * The team owning one of this person's repositories, or nothing where the page was told none.
 *
 * The service sends a team for every repository in the row, so an absent one means the accounting and
 * the row disagree. The page then shows the repository without a team link rather than inventing a
 * team for it: a wrong owner is worse than a missing one.
 */
export function team(teams: Record<string, string>, repository: string): string | undefined {
  return teams[repository];
}

/**
 * How much of one repository's window this person authored, for a metric section's own subtitle.
 *
 * Stated on every section because the summaries under it are measured over THESE merges alone: nine
 * rates read without knowing they came from two merges would be read as if they came from two
 * hundred.
 */
export function measured(row: ActorRepositoryReadiness): string {
  return `${count(row.contributions, 'merge', 'merges')} in this repository`;
}
