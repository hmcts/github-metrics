import { EmptyState } from '@/components/EmptyState';
import { MetricCard } from '@/components/MetricCard';
import { metricDetail, metricTone, metricValue } from '@/lib/metrics';
import type { BehaviourMetricSummary, ReadinessAssessment } from '@/lib/types';

/**
 * The behaviour metrics for one cohort, one card each, in the order the report computes them.
 *
 * The metric's own identifier is the card's label, spelled exactly as `metrics evidence` spells it,
 * so a reader comparing the page against the report or the JSON is reading one name rather than a
 * prettified alias of it.
 *
 * The order is the report's and no figure reorders it. The grid is used for a repository's cohort
 * and for one person's slice of one repository, and in the second case ordering by value would be
 * the beginning of a ranking of people.
 *
 * The assessment is OPTIONAL and only ever a source of colour. Given one, each card takes the tone
 * of the condition that graded that metric, so the figures agree with the readiness block above
 * them. An actor page passes none — the policy grades repositories, not people — and every card
 * there stays colourless rather than the grid inventing a threshold to grade a person by, bar the
 * two completeness rates `metricTone` reads green at a true 100% with or without an assessment.
 */
export function MetricsGrid({
  summaries,
  empty,
  assessment,
}: {
  summaries: readonly BehaviourMetricSummary[];
  /** What no metrics means here — no eligible merges, or a repository this window cannot report. */
  empty: string;
  /** The readiness assessment these metrics were graded by, where one graded them. */
  assessment?: ReadinessAssessment;
}) {
  if (summaries.length === 0) {
    return <EmptyState message={empty} />;
  }
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
      {summaries.map((summary) => (
        <MetricCard
          key={summary.metric}
          label={summary.metric}
          value={metricValue(summary)}
          detail={metricDetail(summary)}
          tone={metricTone(summary, assessment)}
        />
      ))}
    </div>
  );
}
