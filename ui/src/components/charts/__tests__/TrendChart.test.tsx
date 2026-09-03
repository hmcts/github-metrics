/**
 * @vitest-environment jsdom
 */

/**
 * How the trend charts label a period, which is the one thing on them a reader has to trust.
 *
 * `components/__tests__/charts.test.ts` asserts what the server render puts on the page — the
 * container at the asked-for height, reached without throwing for a line, a bar, a gapped series
 * and a single-period series — and stops there, because `react-dom/server` measures nothing and
 * recharts draws from a measured width. No axis exists in that renderer, so neither does the
 * formatter every tick on it passes through.
 *
 * That formatter is not decoration. The rows carry `starts_at` as a full UTC timestamp, and a chart
 * that printed it raw would put `2026-06-01T00:00:00Z` under every tick and lose the axis to
 * overlap; worse, a formatter that dropped to local time would name a Monday period as the Sunday
 * before it for half the world. So the chart is given a width here through a stubbed
 * `ResizeObserver` and the drawn ticks are read back, for both shapes — the axes are written out
 * twice in the component, once per branch, so a formatter lost from one of them is invisible to a
 * test that only renders the other.
 *
 * The timers are faked for the same reason as the donut's tests: recharts reveals its marks over an
 * animation, and real time gives a half-drawn chart at a nondeterministic point in it.
 */

import { act, cleanup, render } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { TrendChart, type TrendSeries } from '@/components/charts/TrendChart';

const COVERAGE: TrendSeries = {
  key: 'approval-coverage',
  label: 'Approval coverage',
  color: '#4ade80',
};

/** Two periods a day apart in UTC but a month apart on the axis, so a wrong tick is legible. */
const ROWS: Record<string, unknown>[] = [
  { starts_at: '2026-06-01T00:00:00Z', 'approval-coverage': 80 },
  { starts_at: '2026-06-29T00:00:00Z', 'approval-coverage': 90 },
];

/** Longer than the opening animation, so every mark has reached its final position. */
const SETTLED_MILLISECONDS = 3000;

/**
 * The size a browser would measure, which jsdom never does.
 *
 * `ResponsiveContainer` renders nothing at all until an observation arrives — its initial dimension
 * is negative and it waits for one — so without this the chart is a sized empty div and every
 * assertion below would pass vacuously against markup with no axis in it.
 */
class ObservedAt400By250 {
  constructor(private readonly report: ResizeObserverCallback) {}

  observe(target: Element): void {
    const entry = { target, contentRect: { width: 400, height: 250 } };
    this.report([entry as unknown as ResizeObserverEntry], this as unknown as ResizeObserver);
  }

  unobserve(): void {}

  disconnect(): void {}
}

const noResizeObserver = globalThis.ResizeObserver;

beforeAll(() => {
  Object.assign(globalThis, { ResizeObserver: ObservedAt400By250 });
});

afterAll(() => {
  Object.assign(globalThis, { ResizeObserver: noResizeObserver });
});

beforeEach(() => {
  // `performance` and the animation frame as well as the timers: react-smooth drives the marks from
  // `requestAnimationFrame` and reads the clock for its easing, so faking the timers alone would
  // leave the animation running in real time and the chart part-drawn.
  vi.useFakeTimers({
    toFake: [
      'setTimeout',
      'clearTimeout',
      'requestAnimationFrame',
      'cancelAnimationFrame',
      'performance',
      'Date',
    ],
  });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

/** Render a chart and run its opening animation out, so the marks are settled before they are read. */
async function drawn(
  shape: 'line' | 'bar',
  data: readonly Record<string, unknown>[] = ROWS,
): Promise<HTMLElement> {
  const { container } = render(
    <TrendChart data={data} series={[COVERAGE]} shape={shape} unit="%" />,
  );
  await act(async () => {
    await vi.advanceTimersByTimeAsync(SETTLED_MILLISECONDS);
  });
  return container;
}

/**
 * The labels drawn under the period axis, in the order the axis put them in.
 *
 * Read from the tick-label group rather than from `.xAxis`: recharts 3 draws an axis in two places,
 * hoisting its labels into a z-index layer of their own so they paint above the marks, and the
 * `.xAxis` group left behind holds only the tick lines. Descending from the axis therefore finds an
 * empty list, which is a passing `toHaveLength(0)` and a silently unchecked formatter.
 */
function periodTicks(container: HTMLElement): string[] {
  const labels = container.querySelector('.recharts-xAxis-tick-labels');
  if (labels === null) {
    throw new Error('the period axis was not drawn');
  }
  return [...labels.querySelectorAll('.recharts-cartesian-axis-tick-value')].map(
    (tick) => tick.textContent ?? '',
  );
}

describe('TrendChart period axis', () => {
  it('labels each period as its UTC day, not as the timestamp the row carries', async () => {
    const container = await drawn('line');

    expect(periodTicks(container)).toEqual(['2026-06-01', '2026-06-29']);
  });

  it('labels a bar chart the same way, from the axis written out in its own branch', async () => {
    const container = await drawn('bar');

    expect(periodTicks(container)).toEqual(['2026-06-01', '2026-06-29']);
  });

  it('names a period whose timestamp will not parse rather than dropping the tick', async () => {
    const container = await drawn('line', [
      { starts_at: 'not a moment', 'approval-coverage': 80 },
      { starts_at: '2026-06-29T00:00:00Z', 'approval-coverage': 90 },
    ]);

    // The formatter's absent-value branch, which `src/lib/__tests__/format.test.ts` pins the words
    // of. An axis short a tick would silently shift every period label along by one.
    expect(periodTicks(container)).toEqual(['-', '2026-06-29']);
  });
});
