/**
 * How one behaviour metric summary reads as a card: a value, and what it was measured over.
 *
 * The nine summaries are the same objects on a repository page and on an actor page — the service
 * emits `BehaviourMetricSummary` in both — so the formatting lives here once. An actor's summaries
 * are per repository and stay that way: nothing in this module combines two summaries, because a
 * mean of one person's rate across repositories is the cross-repository averaging the scope
 * boundaries exclude.
 *
 * A rate leads on its percentage and states its population; a distribution leads on its median and
 * states its sample size, with p75 beside it — the second figure that says whether the median is the
 * whole story or the tail is somewhere else entirely.
 */

import { figure, isRate, population, summarise } from '@/lib/format';
import type { BehaviourMetricSummary, Observation } from '@/lib/types';

/** The value a metric card leads on: the rate's percentage, or the distribution's median. */
export function metricValue(summary: BehaviourMetricSummary): string {
  return summarise(summary.summary);
}

/** The line under the value: the population, plus the p75 a median alone would not show. */
export function metricDetail(summary: BehaviourMetricSummary): string {
  const observation = summary.summary;
  const measured = population(observation);
  const tail = percentile(observation);
  return tail === null ? measured : `${measured} · ${tail}`;
}

/**
 * State a distribution's 75th percentile, or nothing for a shape that has none.
 *
 * Absent for a rate, for an unobserved distribution, and for one whose p75 the sample was too small
 * to place — in each case there is no figure, and printing a dash beside a sample size would read
 * as a measurement that came back empty.
 */
export function percentile(observation: Observation): string | null {
  // `== null` for the reason `maintenanceSummary` records: an unplaced percentile arrives as a
  // missing key, so a strict `=== null` would print `p75 -` beside a sample size.
  if (isRate(observation) || observation.status !== 'observed' || observation.percentile_75 == null) {
    return null;
  }
  return `p75 ${figure(observation.percentile_75)} ${observation.unit}`;
}
