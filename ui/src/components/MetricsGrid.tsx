import { EmptyState } from '@/components/EmptyState';
import { MetricCard } from '@/components/MetricCard';
import { metricDetail, metricValue } from '@/lib/metrics';
import type { BehaviourMetricSummary } from '@/lib/types';

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
 */
export function MetricsGrid({
  summaries,
  empty,
}: {
  summaries: readonly BehaviourMetricSummary[];
  /** What no metrics means here — no eligible merges, or a repository this window cannot report. */
  empty: string;
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
        />
      ))}
    </div>
  );
}
