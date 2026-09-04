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
 *
 * A card's TONE is also decided here, and for seven of the nine it is not decided by a threshold:
 * the readiness policy grades those metrics itself, so `metricTone` reads the condition that graded
 * one and carries that verdict across. `description-quality` and `traceability-reference` are the
 * exception, because the policy deliberately never reads them — which leaves no condition to borrow
 * a verdict from, and makes this module the only place a judgement about them can live. It makes the
 * narrowest one there is: complete reads green, and anything short of complete carries no colour.
 */

import { figure, isRate, population, summarise } from '@/lib/format';
import { conditionTone, labelTone, type ConditionOutcome, type Tone } from '@/lib/tone';
import type {
  BehaviourMetricSummary,
  Observation,
  ReadinessAssessment,
  ReadinessCondition,
} from '@/lib/types';

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

/**
 * The condition names `assessment.py` grades one behaviour metric under.
 *
 * Spelled as suffixes rather than as a list of conditions, because that is how the policy spells
 * them: `Judgement` identifiers are built as `f"{metric.identifier}-at-target"` and friends, so a
 * metric added to the report is graded here the day it is added, with nothing to keep in step.
 *
 * `above-target` is the distribution form — a median above its maximum costs more than the target
 * allows, where a rate below its target falls short of it. NEITHER SUFFIX DECIDES THE OUTCOME: which
 * of the three sections a condition arrives in does, and `graded` reads the section rather than the
 * name. A rate below its target blocks; a shortfall on `review-depth` or on any of the three flow
 * distributions is a CAUTION instead — measured against its configured boundary, reported with its
 * numbers, warm on the card, and imposing no ceiling on the label. At target each of the four is
 * `clear` and its card reads green. `not-observed` is the caution the policy raises for a metric it
 * had no denominator or no sample to grade.
 */
const GRADED_SUFFIXES = ['at-target', 'below-target', 'above-target', 'not-observed'] as const;

/** The three sections in the order they are searched; a condition appears in exactly one. */
const OUTCOMES: readonly ConditionOutcome[] = ['blocking', 'caution', 'clear'];

/** The assessment condition that graded one metric, or nothing where no condition names it. */
function graded(
  metric: string,
  assessment: ReadinessAssessment,
): { outcome: ConditionOutcome; condition: ReadinessCondition } | undefined {
  const names: readonly string[] = GRADED_SUFFIXES.map((suffix) => `${metric}-${suffix}`);
  for (const outcome of OUTCOMES) {
    const condition = assessment[outcome].find((each) => names.includes(each.condition));
    if (condition !== undefined) {
      return { outcome, condition };
    }
  }
  return undefined;
}

/**
 * The two rates the readiness policy reports without grading, and this module colours instead.
 *
 * Both are completeness counts — merges with a usable description, merges carrying a traceability
 * reference — and the assessment configuration names no target for either, so `graded` finds nothing
 * for them on any repository. That is the reason the rule is here rather than a threshold nobody
 * wrote: there is no condition to read, and complete is the one verdict that needs no target to be
 * worth stating.
 */
const COMPLETENESS_RATES: readonly string[] = ['description-quality', 'traceability-reference'];

/**
 * Whether one of those two rates is complete: every eligible merge counted, none missed.
 *
 * READ OFF THE OBSERVATION, never off the formatted percentage. `rate` states 99.96% as `100%`, at
 * one decimal place and rounding up, so a card taking its colour from the string would read green on
 * a window with an unreferenced merge in it — announcing the one thing the metric exists to find.
 * The numerator against the denominator has no rounding to be wrong about.
 *
 * An empty denominator is NOT complete even where the service sends it as observed: nothing measured
 * is not everything measured, and `0 === 0` would colour a card green for a window with no eligible
 * merges at all.
 */
function complete(observation: Observation): boolean {
  if (!isRate(observation) || observation.status !== 'observed') {
    return false;
  }
  return observation.denominator > 0 && observation.numerator === observation.denominator;
}

/**
 * How one behaviour metric card reads, taken from the condition that graded it and never recomputed.
 *
 * NO METRIC THRESHOLD IS DUPLICATED IN THE UI. The other tone functions in `lib/tone.ts` state a
 * boundary because nothing in the report states one for those figures; a graded metric is different,
 * because `assessment.py` weighs it against a configured target. So a card borrows its condition's
 * outcome — a clear reads well, a caution is worth weighing, and a blocking condition takes the
 * ceiling it imposed, amber or red — and a card can never contradict the CLEAR list printed above it
 * on the same page. Raising a target in the assessment configuration is what turns a card amber;
 * nothing here needs editing for that.
 *
 * NEUTRAL WITHOUT AN ASSESSMENT, and neutral for a metric no condition names. A contributor page
 * renders this same grid for one person's slice of one repository, where there is no assessment: the
 * readiness policy grades repositories and not people, so those cards carry no colour, which is the
 * scope boundary showing through rather than an omission. A repository the policy graded nothing for
 * reaches the same place by the same route.
 *
 * THE COMPLETENESS RULE IS CHECKED FIRST AND ASKS THE ASSESSMENT NOTHING. `COMPLETENESS_RATES`
 * records why there is nothing to ask; the consequence is that the rule reads the same on a
 * contributor page, where no assessment arrives and every other card on the grid is colourless.
 */
export function metricTone(
  summary: BehaviourMetricSummary,
  assessment: ReadinessAssessment | undefined,
): Tone {
  if (COMPLETENESS_RATES.includes(summary.metric)) {
    return complete(summary.summary) ? 'good' : 'neutral';
  }
  // `== null` per the missing-key rule, for the two cases above: a contributor page passes none, and
  // a repository the readiness policy graded nothing for is served without one.
  if (assessment == null) {
    return 'neutral';
  }
  const found = graded(summary.metric, assessment);
  if (found === undefined) {
    return 'neutral';
  }
  if (found.outcome === 'blocking') {
    return labelTone(found.condition.label);
  }
  // The clear and caution sections read exactly as they do in the assessment above, informational
  // conditions included — none of the nine metrics is informational today, and if one becomes so
  // the card follows the policy rather than needing to be told again here.
  //
  // No fallback: the blocking outcome returned above, and `conditionTone` answers with a tone for
  // both of the two that are left.
  return conditionTone(found.outcome, found.condition);
}
