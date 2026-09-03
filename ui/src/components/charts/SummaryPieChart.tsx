'use client';

import clsx from 'clsx';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import type { CSSProperties } from 'react';
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts';
import { InfoTooltip } from '@/components/InfoTooltip';
import { activeSlices, totalValue, type PieSlice } from '@/lib/chart';
import { filterTarget } from '@/lib/filter';
import { percentageOf } from '@/lib/format';

/** The hover card's frame, which is inline because recharts styles its tooltip wrapper directly. */
const TOOLTIP: CSSProperties = {
  backgroundColor: '#1e293b',
  border: '1px solid #334155',
  borderRadius: '6px',
  padding: '8px 12px',
};

interface DonutProperties {
  title: string;
  /** Every slice, including the ones counted at zero. */
  data: readonly PieSlice[];
  /** Height of the chart canvas alone — the title and legend sit outside it. */
  height?: number;
  /** What the categories mean, shown on the heading's information control. */
  tooltip?: string;
  /**
   * The query parameter this donut filters on, where it filters anything.
   *
   * Absent means a static chart: no navigation, no buttons, and the markup a reader gets today.
   */
  parameter?: string;
}

/** Which slice is filtered on right now, and what a click on one of them should do about it. */
interface SliceFilter {
  /** The slice key in the URL, or the empty string where the dimension is unfiltered. */
  active: string;
  toggle: (key: string) => void;
}

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
 *
 * Given a `parameter`, the donut is also the filter control for the dimension it draws: a legend
 * entry and its wedge both write the slice's key to that query parameter, and the table below the
 * chart reads it back. Without one the donut is a picture and nothing else, which is the mode a
 * donut with no list under it wants — so the interactive half is opt-in rather than the default.
 */
export function SummaryPieChart({ parameter, ...donut }: DonutProperties) {
  // Two components rather than one with a conditional hook: `useRouter` throws where no router is
  // mounted, and a static donut is drawn by pages and tests that have none.
  return parameter === undefined ? (
    <Donut {...donut} />
  ) : (
    <FilteringDonut {...donut} parameter={parameter} />
  );
}

/** The same donut, wired to the URL: reads the dimension's current value and writes the next one. */
function FilteringDonut({
  parameter,
  ...donut
}: Omit<DonutProperties, 'parameter'> & { parameter: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParameters = useSearchParams();
  const active = searchParameters.get(parameter) ?? '';

  function toggle(key: string) {
    // Clicking the slice already filtered on clears the filter, which is the parameter's absence —
    // the same navigation the search box makes when its box is emptied. `window.location.search`
    // rather than `searchParameters`, so a parameter another control wrote is carried through.
    const chosen = key === active ? '' : key;
    router.replace(filterTarget(pathname, window.location.search, parameter, chosen), {
      scroll: false,
    });
  }

  return <Donut {...donut} filter={{ active, toggle }} />;
}

function Donut({
  title,
  data,
  height = 175,
  tooltip,
  filter,
}: Omit<DonutProperties, 'parameter'> & { filter?: SliceFilter }) {
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
              className={filter ? 'cursor-pointer' : undefined}
              onClick={filter ? (sector) => filter.toggle(clickedKey(sector)) : undefined}
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

      <div
        className="flex flex-wrap justify-center gap-x-3 gap-y-1.5"
        role={filter ? 'group' : undefined}
        aria-label={filter ? `${title} filter` : undefined}
      >
        {data.map((slice) =>
          filter ? (
            <button
              key={slice.key}
              type="button"
              onClick={() => filter.toggle(slice.key)}
              aria-pressed={slice.key === filter.active}
              // `-mx-1 px-1` so the highlight has room without the entry moving when it is applied.
              className={clsx(
                'group flex items-center gap-1.5 -mx-1 px-1 rounded transition-colors',
                'focus:outline-none focus-visible:ring-1 focus-visible:ring-indigo-500',
                slice.key === filter.active ? 'bg-slate-800' : null,
              )}
              style={{ opacity: slice.value === 0 ? 0.38 : 1 }}
            >
              <LegendEntry slice={slice} interactive />
            </button>
          ) : (
            <div
              key={slice.key}
              className="flex items-center gap-1.5"
              // Dimmed rather than hidden: the label exists in the report even when nothing is in it.
              style={{ opacity: slice.value === 0 ? 0.38 : 1 }}
            >
              <LegendEntry slice={slice} />
            </div>
          ),
        )}
      </div>
    </div>
  );
}

/**
 * The slice key of a clicked wedge.
 *
 * recharts spreads the datum it drew the sector from into the sector it hands the handler, so the
 * clicked wedge carries the slice's own `key` — read from there rather than by indexing the drawn
 * array, which is a position two things have to agree on and a lookup that can miss. The cast is
 * because the sector's declared type describes the geometry and not what was plotted.
 */
function clickedKey(sector: unknown): string {
  return (sector as PieSlice).key;
}

/**
 * One legend entry's three marks: the slice's colour, its label and its count.
 *
 * Shared by both legends so a donut that filters and one that does not read identically at rest —
 * the interactive entry adds only what a control has to have, a hover state and a focus ring.
 */
function LegendEntry({ slice, interactive }: { slice: PieSlice; interactive?: boolean }) {
  return (
    <>
      <span
        className="shrink-0 w-2 h-2 rounded-full"
        style={{ backgroundColor: slice.color }}
        aria-hidden="true"
      />
      <span className={clsx('text-xs text-slate-400', interactive && 'group-hover:text-slate-200')}>
        {slice.name}
      </span>
      <span className="text-xs text-slate-600 tabular-nums">{slice.value}</span>
    </>
  );
}
