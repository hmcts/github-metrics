import { EmptyState } from '@/components/EmptyState';
import { Section } from '@/components/Section';
import { TrendChart } from '@/components/charts/TrendChart';
import { count, day } from '@/lib/format';
import {
  ROUTE_HEX,
  cutDetail,
  metricCharts,
  periodSpan,
  throughputRows,
  windowReasons,
  type MetricChart,
} from '@/lib/trend';
import type { RepositoryTrend } from '@/lib/types';

/**
 * One repository's periods since it was enabled, and only ever one repository's.
 *
 * Two kinds of chart: what each period put onto the default branch by both routes, and one line per
 * behaviour metric the series observed. Both are drawn from the baseline rightwards, so the span
 * before enablement is visible as the thing the periods moved from.
 *
 * NOTHING HERE IS GRADED. No line is coloured by a label, no movement is called an improvement, and
 * the delta basis is printed under each chart because percentage points and percentage change both
 * read as bare numbers. A period the caches could not answer for is a gap in the line and a reason
 * under it, never a zero.
 *
 * `cut` is the count the series was asked for, so the heading can say when it holds that many and is
 * therefore the first periods since enablement rather than every one of them.
 */
export function TrendSection({ series, cut }: { series: RepositoryTrend; cut: number }) {
  const span = periodSpan(series);
  const reasons = windowReasons(series);
  const charts = metricCharts(series);

  return (
    <Section
      heading="Trend"
      detail={[
        count(series.periods.length, 'whole period', 'whole periods'),
        span === null ? undefined : `of ${count(span, 'day', 'days')}`,
        series.enablement_at === undefined ? undefined : `since ${day(series.enablement_at)}`,
        cutDetail(series, cut),
      ]
        .filter((part) => part !== undefined)
        .join(' ')}
    >
      <div className="space-y-4">
        <ChartCard title="Merges by route" detail="what each period put onto the default branch">
          <TrendChart
            data={throughputRows(series)}
            shape="bar"
            series={[
              { key: 'merged_pull_requests', label: 'pull requests', color: ROUTE_HEX.pullRequests },
              { key: 'direct_commits', label: 'direct commits', color: ROUTE_HEX.directCommits },
            ]}
          />
        </ChartCard>

        {charts.length === 0 ? (
          <EmptyState
            message="No behaviour metric was observed in any period of this series."
            detail="A period below the configured minimum cohort reports its counts and no metric values, because a rate over a handful of merges is arithmetic rather than a pattern."
          />
        ) : (
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
            {charts.map((chart) => (
              <MetricSeriesCard key={chart.metric} chart={chart} />
            ))}
          </div>
        )}

        {series.delta_detail === undefined ? null : (
          <p className="text-xs text-slate-500">No period was compared: {series.delta_detail}</p>
        )}

        {reasons.length === 0 ? null : (
          <ul className="text-xs text-slate-500 space-y-1">
            {reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        )}
      </div>
    </Section>
  );
}

function MetricSeriesCard({ chart }: { chart: MetricChart }) {
  return (
    <ChartCard title={chart.label} detail={`${chart.unit} · ${chart.basis}`}>
      <TrendChart
        data={chart.rows}
        unit={chart.axis}
        series={[{ key: 'value', label: chart.label, color: ROUTE_HEX.pullRequests }]}
      />
    </ChartCard>
  );
}

/**
 * The card every chart in this section sits in: a title, what it is measured in, and the canvas.
 *
 * Flat, like `MetricCard`: the section is the box, and a bordered chart card inside a bordered
 * panel was one of the nested boxes this page was rebuilt to lose. A chart is bounded by its own
 * axes, so the grid gap is enough to keep two of them apart.
 */
function ChartCard({
  title,
  detail,
  children,
}: {
  title: string;
  detail: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-baseline gap-x-2">
        <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide font-mono">{title}</h3>
        <span className="text-xs text-slate-500">{detail}</span>
      </div>
      {children}
    </div>
  );
}
