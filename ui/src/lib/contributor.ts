/**
 * What one person actually did in one repository, subtracted out of their own metric summaries.
 *
 * The contributors table used to carry `blocking` beside a contribution count, which said how many
 * occurrences a rule found and nothing about the work: two numbers, neither of them answering "did
 * this person open pull requests or push to the branch". The four figures here answer that, and
 * every one of them is arithmetic on the summaries the service already sent — no figure is computed
 * that a reader could not reach by adding the report's own rows up.
 *
 * `independent-review-coverage` carries all three counts. Its denominator is every change that
 * reached the default branch by either route, its numerator the ones an independent human reviewed,
 * and its `direct-commit` classification the ones that arrived without a pull request at all
 * (`behaviour_metrics.base.GovernanceRate`). So:
 *
 *   • merged pull requests = denominator − direct commits
 *   • direct pushes = the `direct-commit` classification
 *   • unreviewed merges = denominator − numerator − direct commits, which is the pull requests that
 *     merged without independent review; a direct push is counted in its own column and not twice
 *
 * MEDIAN, NOT AVERAGE. `pull-request-size` is a distribution and carries no mean, so the size column
 * states the median it does carry and says so in its heading. It is read at the median rather than
 * at the p75 the metric is GRADED at, because this column describes one person's typical change
 * rather than the tail the assessment weighs.
 *
 * COUNTS, NEVER SCORES. These sit beside a login on one repository's page, are never combined across
 * the repositories somebody contributed to, and nobody is ordered by any of them
 * (architecture.md, "Scope boundaries").
 *
 * An absent figure is a DASH AND NEVER A ZERO, per the rule `lib/format.ts` states: a person whose
 * review coverage was not applicable, or whose row carries no summary for the metric at all, had
 * nothing measured — and "0 unreviewed merges" would report that as a clean window.
 */

import { ABSENT, figure, isRate } from '@/lib/format';
import type { BehaviourMetricSummary, ContributorRow, Observation } from '@/lib/types';

/** The metric whose rate carries the two routes a change took, and how many were reviewed. */
const REVIEW_COVERAGE = 'independent-review-coverage';

/** The metric whose distribution carries how large this person's pull requests were. */
const SIZE = 'pull-request-size';

/** The classification `GovernanceRate` counts a push straight onto the default branch under. */
const DIRECT_COMMIT = 'direct-commit';

/**
 * The four figures one contributor row renders as, counts absent where nothing was measured.
 *
 * The three counts are numbers so the table can tone them; `size` is already formatted because it
 * carries a unit and takes no tone — a median change size is neither good nor bad.
 */
export interface ContributorFigures {
  merged?: number;
  directPushes?: number;
  unreviewed?: number;
  size: string;
}

/** The summary for one metric out of a row's own summaries, or nothing where the row has none. */
export function summaryFor(row: ContributorRow, metric: string): BehaviourMetricSummary | undefined {
  return row.metrics.find((each) => each.metric === metric);
}

/**
 * Refuse a negative count rather than printing one.
 *
 * Unreachable from a consistent block — a numerator cannot exceed its denominator and the
 * classifications are counted from the same cohort — so a negative here means the arithmetic and the
 * summary disagree, and a dash says that better than a clamp to zero would.
 */
function nonNegative(value: number): number | undefined {
  return value < 0 ? undefined : value;
}

/** The three review-coverage counts, or three absences where there was no rate to read. */
function routes(row: ContributorRow): Pick<ContributorFigures, 'merged' | 'directPushes' | 'unreviewed'> {
  const summary = summaryFor(row, REVIEW_COVERAGE);
  const observation: Observation | undefined = summary?.summary;
  if (summary === undefined || observation === undefined || !isRate(observation) || observation.status !== 'observed') {
    return {};
  }
  // A classification the window never saw is a missing key rather than a zero, so `?? 0` here is
  // reading "none of this person's changes took that route" and not papering over an absence: the
  // observation is observed, which is what says the counting happened at all.
  const direct = summary.classifications[DIRECT_COMMIT] ?? 0;
  return {
    merged: nonNegative(observation.denominator - direct),
    directPushes: direct,
    unreviewed: nonNegative(observation.denominator - observation.numerator - direct),
  };
}

/**
 * The median pull-request size, formatted with the unit the report sent it in.
 *
 * Three ways to have no figure and one answer for all of them: no summary for the metric, a
 * distribution the sample was not applicable for, and one whose median the sample was too small to
 * place. The unit comes off the observation rather than being spelled "lines" here, so a report that
 * ever sizes a change differently is not mislabelled by this column.
 */
function size(row: ContributorRow): string {
  const observation: Observation | undefined = summaryFor(row, SIZE)?.summary;
  if (observation === undefined || isRate(observation) || observation.status !== 'observed') {
    return ABSENT;
  }
  // `== null` per the missing-key rule: an unplaced median arrives as no key at all.
  if (observation.median == null) {
    return ABSENT;
  }
  return `${figure(observation.median)} ${observation.unit}`;
}

/** Read one contributor row's four columns out of the summaries it carries. */
export function contributorFigures(row: ContributorRow): ContributorFigures {
  return { ...routes(row), size: size(row) };
}
