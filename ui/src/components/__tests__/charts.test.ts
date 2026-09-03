/**
 * What the two recharts components put on the page from the server.
 *
 * recharts draws from a measured width, and `react-dom/server` measures nothing, so a chart's marks
 * are unreachable in this renderer — `ResponsiveContainer` emits a sized placeholder and stops. That
 * makes these thin assertions, and they are the ones that matter across a recharts major: the
 * container has to be reached at the height the page asked for, and it has to be reached without
 * throwing. recharts 3 rewrote the chart internals onto a store, and a chart that now needs a
 * browser API to build its children would fail here rather than in a browser nobody runs in CI.
 *
 * The donut's own arithmetic is tested in `src/lib/__tests__/chart.test.ts`, and the tooltip and
 * label callbacks — which need a hover — in the jsdom tests.
 *
 * JSX is written as `createElement` calls so the tests stay `.ts` files alongside the library tests.
 */

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { SummaryPieChart } from '@/components/charts/SummaryPieChart';
import { TrendChart, type TrendSeries } from '@/components/charts/TrendChart';
import type { PieSlice } from '@/lib/chart';

// A filtering donut reads the URL and reaches the router, neither of which exists in this renderer.
// The click itself is not reachable from here at all — `SummaryPieChart.test.tsx` asserts where a
// legend entry navigates to — so the mock only has to let the markup be produced.
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: () => undefined }),
  usePathname: () => '/repositories',
  useSearchParams: () => new URLSearchParams('label=red'),
}));

const COVERAGE: TrendSeries = {
  key: 'approval-coverage',
  label: 'Approval coverage',
  color: '#4ade80',
};

const SERIES: TrendSeries[] = [
  COVERAGE,
  { key: 'review-depth', label: 'Review depth', color: '#fbbf24' },
];

/** The second period observes both series; the first leaves `review-depth` as a gap. */
const OBSERVED: Record<string, unknown> = {
  starts_at: '2026-06-29T00:00:00Z',
  'approval-coverage': 90,
  'review-depth': 40,
};

const ROWS: Record<string, unknown>[] = [
  { starts_at: '2026-06-01T00:00:00Z', 'approval-coverage': 80, 'review-depth': null },
  OBSERVED,
];

const SLICES: PieSlice[] = [
  { key: 'green', name: 'Ready', value: 3, color: '#4ade80' },
  { key: 'amber', name: 'Caution', value: 0, color: '#fbbf24' },
  { key: 'red', name: 'Blocked', value: 1, color: '#f87171' },
];

describe('TrendChart', () => {
  it('reaches its container at the shared height, whatever shape was asked for', () => {
    for (const shape of ['line', 'bar'] as const) {
      const rendered = renderToStaticMarkup(
        createElement(TrendChart, { data: ROWS, series: SERIES, shape, unit: '%' }),
      );
      expect(rendered).toContain('recharts-responsive-container');
      expect(rendered).toContain('height:250px');
    }
  });

  it('renders a series holding a gap without throwing, the same as a full one', () => {
    const gapped = renderToStaticMarkup(
      createElement(TrendChart, { data: ROWS, series: SERIES }),
    );
    const whole = renderToStaticMarkup(
      createElement(TrendChart, { data: [OBSERVED], series: [COVERAGE] }),
    );
    expect(gapped).toContain('recharts-responsive-container');
    expect(whole).toContain('recharts-responsive-container');
  });
});

describe('SummaryPieChart', () => {
  const rendered = renderToStaticMarkup(
    createElement(SummaryPieChart, {
      title: 'Readiness',
      data: SLICES,
      tooltip: 'How each repository was graded',
    }),
  );

  it('draws the donut at the height it was given', () => {
    expect(rendered).toContain('recharts-responsive-container');
    expect(rendered).toContain('height:175px');
  });

  it('lists every label in the legend, including the one nothing is in', () => {
    for (const slice of SLICES) {
      expect(rendered).toContain(slice.name);
      expect(rendered).toContain(`background-color:${slice.color}`);
    }
  });

  it('dims the empty label rather than dropping it', () => {
    expect(rendered).toContain('opacity:0.38');
  });

  it('says it has no data instead of drawing an empty donut', () => {
    const empty = renderToStaticMarkup(
      createElement(SummaryPieChart, {
        title: 'Readiness',
        data: SLICES.map((slice) => ({ ...slice, value: 0 })),
      }),
    );
    expect(empty).toContain('No data');
    expect(empty).not.toContain('recharts-responsive-container');
  });

  it('leaves the legend inert where no parameter says what it would filter', () => {
    // The information control is a button of its own, so the absence is asserted on the property a
    // filter entry carries rather than on the tag: no entry is pressable, and none is a control.
    expect(rendered).not.toContain('aria-pressed');
    expect(rendered).not.toContain('role="group"');
    expect(rendered).toContain('<div class="flex items-center gap-1.5" style="opacity:1"');
  });
});

/**
 * The same donut told which parameter it filters, which is how every donut on a list page is drawn.
 *
 * The wedges are unreachable in this renderer, so what is asserted is the legend: it stays the full
 * label set — the band nothing fell in included, dimmed as before — and each entry is now a control
 * that says whether it is the one being filtered on.
 */
describe('SummaryPieChart as a filter control', () => {
  const rendered = renderToStaticMarkup(
    createElement(SummaryPieChart, { title: 'Readiness', data: SLICES, parameter: 'label' }),
  );

  it('makes every legend entry a control, whatever its count', () => {
    for (const slice of SLICES) {
      expect(rendered).toContain(slice.name);
      expect(rendered).toContain(`background-color:${slice.color}`);
    }
    expect([...rendered.matchAll(/aria-pressed=/g)]).toHaveLength(SLICES.length);
    expect(rendered).toContain('role="group" aria-label="Readiness filter"');
  });

  it('presses the entry the URL is filtered to, and no other', () => {
    // `label=red` in the stubbed URL, which is `Blocked` in this distribution. Read as which label
    // is pressed rather than as how many are: a donut pressing the wrong entry presses one too.
    expect(pressedLabels(rendered)).toEqual(['Blocked']);
  });

  it('still dims the label nothing is in rather than dropping it', () => {
    expect(rendered).toContain('Caution');
    expect(rendered).toContain('opacity:0.38');
  });
});

/** The labels of the legend entries drawn as pressed, which is the filter the donut is showing. */
function pressedLabels(markup: string): string[] {
  return markup
    .split('<button')
    .filter((entry) => entry.includes('aria-pressed="true"'))
    .map((entry) => /text-slate-400[^"]*">([^<]+)</.exec(entry)?.[1] ?? 'unlabelled');
}
