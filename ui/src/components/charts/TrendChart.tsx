'use client';

import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { day } from '@/lib/format';

/**
 * A period series for one repository, and only ever for one repository.
 *
 * The trend endpoint returns per-period figures for a single repository because averaging periods
 * across repositories would state a number nobody can act on; this component therefore takes rows
 * and series, never a repository set. Lines never `connectNulls`, so a period the caches do not
 * cover is a gap in the data rather than a drop to zero — the two look identical on a chart and mean
 * opposite things — and every observed period carries a dot, because a gap either side of one leaves
 * it with no segment to be drawn as.
 *
 * The axes are written out in both branches rather than shared through a fragment: recharts reads its
 * own children by type to build the chart, and a fragment between it and an `<XAxis>` is a way to
 * lose an axis silently.
 */
export interface TrendSeries {
  /** The row key this series reads. */
  key: string;
  label: string;
  /** A colour value, not a class: recharts takes strokes and fills as strings. */
  color: string;
}

const TOOLTIP = {
  backgroundColor: '#1e293b',
  border: '1px solid #334155',
  borderRadius: '6px',
  color: '#f1f5f9',
};

const TICK = { fill: '#94a3b8', fontSize: 11 };

const GRID = '#1e293b';

const LEGEND = { fontSize: 12, color: '#94a3b8' };

const MARGIN = { top: 4, right: 8, left: 0, bottom: 0 };

/** The row key every period is named by, and the height every chart on the page is drawn at. */
const X_KEY = 'starts_at';

const HEIGHT = 250;

const tick = (value: unknown) => day(String(value));

export function TrendChart({
  data,
  series,
  shape = 'line',
  unit,
}: {
  data: readonly Record<string, unknown>[];
  series: readonly TrendSeries[];
  shape?: 'line' | 'bar';
  /** Suffix for the value axis, e.g. `%` or ` h`. */
  unit?: string;
}) {
  const rows = [...data];

  if (shape === 'bar') {
    return (
      <ResponsiveContainer width="100%" height={HEIGHT}>
        <BarChart data={rows} margin={MARGIN}>
          <CartesianGrid strokeDasharray="3 3" stroke={GRID} vertical={false} />
          <XAxis dataKey={X_KEY} tick={TICK} axisLine={false} tickLine={false} tickFormatter={tick} />
          <YAxis tick={TICK} axisLine={false} tickLine={false} unit={unit} />
          <Tooltip contentStyle={TOOLTIP} labelFormatter={tick} />
          <Legend wrapperStyle={LEGEND} />
          {series.map((bar) => (
            <Bar
              key={bar.key}
              dataKey={bar.key}
              name={bar.label}
              fill={bar.color}
              radius={[3, 3, 0, 0]}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={HEIGHT}>
      <LineChart data={rows} margin={MARGIN}>
        <CartesianGrid strokeDasharray="3 3" stroke={GRID} vertical={false} />
        <XAxis dataKey={X_KEY} tick={TICK} axisLine={false} tickLine={false} tickFormatter={tick} />
        <YAxis tick={TICK} axisLine={false} tickLine={false} unit={unit} />
        <Tooltip contentStyle={TOOLTIP} labelFormatter={tick} />
        <Legend wrapperStyle={LEGEND} />
        {series.map((line) => (
          <Line
            key={line.key}
            type="monotone"
            dataKey={line.key}
            name={line.label}
            stroke={line.color}
            strokeWidth={2}
            // Dots, not a bare line. A line is drawn from segments between adjacent points, so a
            // period observed with a gap on either side — or a series holding one whole period —
            // has no segment and renders as an empty canvas without a mark of its own.
            dot={{ r: 2, fill: line.color, stroke: line.color }}
            // Never `connectNulls`. A period that observed nothing contributes `null`, and bridging
            // it would draw a straight measured-looking segment across a span nothing was measured
            // in — the gap-is-not-a-zero rule this module exists for, asserted in reverse.
            connectNulls={false}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
