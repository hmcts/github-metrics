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
 * A card's TONE is also decided here, and unlike every other figure on the site it is not decided by
 * a threshold: the readiness policy grades these metrics itself, so `metricTone` reads the condition
 * that graded one and carries that verdict across. Seven of the nine are graded —
 * `description-quality` and `traceability-reference` are neutral aggregates the policy deliberately
 * never reads — so those two cards carry no colour, which is the policy showing through rather than
 * a threshold nobody wrote.
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
 * allows, where a rate below its target falls short of it — and both are blocking. `not-observed`
 * is the caution the policy raises for a metric it had no denominator or no sample to grade.
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
 * NEUTRAL WITHOUT AN ASSESSMENT, and neutral for a metric no condition names. An actor page renders
 * this same grid for one person's slice of one repository, where there is no assessment at all: the
 * readiness policy grades repositories and not people, so those cards carry no colour, which is the
 * scope boundary showing through rather than an omission. A repository the policy graded nothing for
 * reaches the same place by the same route.
 */
export function metricTone(metric: string, assessment: ReadinessAssessment | undefined): Tone {
  // `== null` per the missing-key rule: an older stored block sends no assessment at all.
  if (assessment == null) {
    return 'neutral';
  }
  const found = graded(metric, assessment);
  if (found === undefined) {
    return 'neutral';
  }
  if (found.outcome === 'blocking') {
    return labelTone(found.condition.label);
  }
  // The clear and caution sections read exactly as they do in the assessment above, informational
  // conditions included — none of the nine metrics is informational today, and if one becomes so
  // the card follows the policy rather than needing to be told again here.
  return conditionTone(found.outcome, found.condition) ?? 'neutral';
}
