'use client';

import type { CSSProperties } from 'react';
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts';
import { InfoTooltip } from '@/components/InfoTooltip';
import { activeSlices, totalValue, type PieSlice } from '@/lib/chart';
import { percentageOf } from '@/lib/format';

/**
 * A donut over a label distribution, with a legend that lists every label whatever the data holds.
 *
 * Zero-count slices are kept out of the wedge — with `paddingAngle` on, a zero-width wedge draws as a
 * stray tick — but stay in the legend, dimmed. Dropping them instead would make "no repository is
 * blocked" and "blocked is not a grade this report gives" look the same, and the first is the useful
 * finding.
 *
 * The whole chart is skipped when nothing was counted: an empty donut is a shape readers try to
 * interpret, so the section says it has no data instead.
 */
const TOOLTIP: CSSProperties = {
  backgroundColor: '#1e293b',
  border: '1px solid #334155',
  borderRadius: '6px',
  padding: '8px 12px',
};

export function SummaryPieChart({
  title,
  data,
  height = 175,
  tooltip,
}: {
  title: string;
  /** Every slice, including the ones counted at zero. */
  data: readonly PieSlice[];
  /** Height of the chart canvas alone — the title and legend sit outside it. */
  height?: number;
  /** What the categories mean, shown on the heading's information control. */
  tooltip?: string;
}) {
  const total = totalValue(data);
  const wedges = activeSlices(data);

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-4 flex flex-col gap-3">
      <div className="flex items-center gap-1.5">
        <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide">{title}</h3>
        {tooltip ? <InfoTooltip text={tooltip} /> : null}
      </div>

      {total === 0 ? (
        <div className="flex items-center justify-center text-slate-600 text-sm" style={{ height }}>
          No data
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={height}>
          <PieChart>
            <Pie
              data={wedges}
              cx="50%"
              cy="50%"
              innerRadius={48}
              outerRadius={68}
              paddingAngle={wedges.length > 1 ? 2 : 0}
              dataKey="value"
              strokeWidth={0}
            >
              {wedges.map((wedge) => (
                <Cell key={wedge.name} fill={wedge.color} />
              ))}
            </Pie>
            <Tooltip
              content={({ active, payload }) => {
                const slice = payload?.[0]?.payload as PieSlice | undefined;
                if (!active || slice === undefined) {
                  return null;
                }
                return (
                  <div style={TOOLTIP}>
                    <p className="text-xs font-semibold mb-0.5" style={{ color: slice.color }}>
                      {slice.name}
                    </p>
                    <p className="text-xs text-slate-300 tabular-nums">
                      {slice.value} &nbsp;&middot;&nbsp; {percentageOf(slice.value, total)}
                    </p>
                  </div>
                );
              }}
            />
          </PieChart>
        </ResponsiveContainer>
      )}

      <div className="flex flex-wrap justify-center gap-x-3 gap-y-1.5">
        {data.map((slice) => (
          <div
            key={slice.name}
            className="flex items-center gap-1.5"
            // Dimmed rather than hidden: the label exists in the report even when nothing is in it.
            style={{ opacity: slice.value === 0 ? 0.38 : 1 }}
          >
            <span
              className="shrink-0 w-2 h-2 rounded-full"
              style={{ backgroundColor: slice.color }}
              aria-hidden="true"
            />
            <span className="text-xs text-slate-400">{slice.name}</span>
            <span className="text-xs text-slate-600 tabular-nums">{slice.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
