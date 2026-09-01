/**
 * Formatters for the numbers and instants the evidence report carries.
 *
 * These mirror `metrics.render` deliberately, function for function: a figure must read the same on
 * the page as in the text report the same window prints, or the two renderings become two claims.
 * The rules that come with that:
 *
 *   • An unmeasured value is a dash, NEVER a zero. "The scanner reported no coverage" and "coverage
 *     is zero" are different findings and only the second is about the code.
 *   • A rate whose observation is `not_applicable` prints as words, because there was no eligible
 *     sample to take a percentage of.
 *   • A count out of an empty population is a dash, and a share too small to round to a tenth of a
 *     percent prints `<0.1%`, so a row somebody is in never reads like a deliberate zero.
 */

import type { DistributionObservation, Observation, RateObservation } from '@/lib/types';

export const ABSENT = '-';

export const NOT_APPLICABLE = 'not applicable';

/**
 * Format one number the way the report's `%g` does: as few digits as say it exactly.
 *
 * Six significant digits before `toString`, matching `%g`, which also drops the float noise a
 * division can leave behind (33.300000000000004 reads as 33.3).
 */
export function figure(value: number | null | undefined): string {
  return value === null || value === undefined ? ABSENT : Number(value.toPrecision(6)).toString();
}

export function percent(value: number | null | undefined): string {
  return value === null || value === undefined ? ABSENT : `${figure(value)}%`;
}

/**
 * Format one optional count as an integer, leaving an uncounted value visibly absent.
 *
 * Not through `figure`, whose six significant digits turn a million lines of code into `1e+06`.
 */
export function quantity(value: number | null | undefined): string {
  return value === null || value === undefined ? ABSENT : String(value);
}

/**
 * Round to one decimal place, the precision every percentage in the report is stated at.
 *
 * HALF TO EVEN on an exact tie, because Python's `round(value, 1)` — which `analysis.rate_percentage`
 * and `render.percentage_of` both state their percentages through — rounds that way and
 * `Math.round` does not. 78 of 96 is exactly 81.25: Python prints 81.2 and half-up would print 81.3,
 * putting two different figures for one observation on the same page, the assessment sentence
 * against the metric card beside it. Every denominator dividing into a multiple of 16 lands on a tie,
 * so this is not a rare corner.
 */
function tenth(value: number): number {
  const scaled = value * 10;
  const lower = Math.floor(scaled);
  if (scaled - lower !== 0.5) {
    return Math.round(scaled) / 10;
  }
  return (lower % 2 === 0 ? lower : lower + 1) / 10;
}

export function percentageOf(count: number, total: number): string {
  if (total === 0) {
    return ABSENT;
  }
  const share = tenth((count / total) * 100);
  return count > 0 && share === 0 ? '<0.1%' : percent(share);
}

/** A rate carries a `denominator`; a distribution carries a `unit`. That is the whole discriminator. */
export function isRate(observation: Observation): observation is RateObservation {
  return 'denominator' in observation;
}

/** Format a rate observation as its percentage, or as words when there was no sample to divide. */
export function rate(observation: RateObservation): string {
  if (observation.status !== 'observed') {
    return NOT_APPLICABLE;
  }
  return percent(tenth((observation.numerator / observation.denominator) * 100));
}

/** State what a rate was measured over, so a percentage is never read without its population. */
export function basis(observation: RateObservation): string {
  return `${observation.numerator} of ${observation.denominator}`;
}

/** Format a distribution's median with its unit — the value a metric grid leads on. */
export function median(observation: DistributionObservation): string {
  if (observation.status !== 'observed') {
    return NOT_APPLICABLE;
  }
  return `${figure(observation.median)} ${observation.unit}`;
}

/** Format either observation shape into the one value a card or a grid cell shows. */
export function summarise(observation: Observation): string {
  return isRate(observation) ? rate(observation) : median(observation);
}

/** State what a distribution was measured over, in the same "N of M" register as a rate's basis. */
export function sample(observation: DistributionObservation): string {
  const size = observation.sample_size;
  return `${size} ${size === 1 ? 'sample' : 'samples'}`;
}

/** Describe what an observation was measured over, whichever shape it is. */
export function population(observation: Observation): string {
  return isRate(observation) ? basis(observation) : sample(observation);
}

/**
 * Format one instant as UTC to the minute, as `render.instant` does.
 *
 * Converted to UTC rather than printed as sent: the report PRESERVES a written offset, so an instant
 * can arrive in a two-hour zone, and printing its wall clock under a `Z` would put the page two
 * hours away from the JSON carrying the same instant.
 */
export function instant(moment: string | null | undefined): string {
  const parsed = parse(moment);
  return parsed === null ? ABSENT : `${parsed.toISOString().slice(0, 16)}Z`;
}

/** Format one instant as the UTC day it fell on, for a window's own dates. */
export function day(moment: string | null | undefined): string {
  const parsed = parse(moment);
  return parsed === null ? ABSENT : parsed.toISOString().slice(0, 10);
}

/** State a window as the two UTC days it runs between. */
export function span(starts: string, ends: string): string {
  return `${day(starts)} to ${day(ends)}`;
}

function parse(moment: string | null | undefined): Date | null {
  if (moment === null || moment === undefined) {
    return null;
  }
  const parsed = new Date(moment);
  // An unparseable instant reads as absent rather than as `Invalid Date`: the page cannot fix the
  // value, and a dash says the same thing without looking like a bug in the browser.
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

/** Pluralise a count against its own noun, so a stat card never reads "1 repositories". */
export function count(quantity: number, singular: string, plural: string): string {
  return `${quantity} ${quantity === 1 ? singular : plural}`;
}
