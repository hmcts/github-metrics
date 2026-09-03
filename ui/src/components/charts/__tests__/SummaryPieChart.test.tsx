/**
 * @vitest-environment jsdom
 */

/**
 * The donut's hover card, which is the one thing on the chart a reader has to trust.
 *
 * `components/__tests__/charts.test.ts` asserts what the server render puts on the page — the
 * container at the asked-for height, the full legend, the dimmed zero and the no-data branch — and
 * stops there, because `react-dom/server` measures nothing and recharts draws from a measured
 * width. The wedges therefore do not exist in that renderer, and neither does the card they are
 * hovered for.
 *
 * That card is not decoration: it is where a slice's count and its share of the whole are read, and
 * the share is the only figure this component derives. So the chart is given a width here through a
 * stubbed `ResizeObserver`, its opening animation is run out on fake timers, and each wedge is
 * hovered in turn with the events a pointer would send.
 *
 * The timers are faked rather than waited out. recharts reveals the wedges over an animation, so
 * real time gives a nondeterministic count part-way through it — three seconds of fake time reaches
 * the settled ring every run, and in milliseconds.
 */

import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { SummaryPieChart } from '@/components/charts/SummaryPieChart';
import type { PieSlice } from '@/lib/chart';

/** A readiness distribution whose shares are not round, so a wrong divisor is visible. */
const SLICES: PieSlice[] = [
  { name: 'Ready', value: 5, color: '#4ade80' },
  { name: 'Caution', value: 0, color: '#fbbf24' },
  { name: 'Blocked', value: 3, color: '#f87171' },
];

/** Longer than the opening animation, so every wedge has reached its final angle. */
const SETTLED_MILLISECONDS = 3000;

/**
 * The size a browser would measure, which jsdom never does.
 *
 * `ResponsiveContainer` renders nothing at all until an observation arrives — its initial dimension
 * is negative and it waits for one — so without this the chart is a sized empty div and every
 * assertion below would pass vacuously against markup with no wedges in it.
 */
class ObservedAt400By175 {
  constructor(private readonly report: ResizeObserverCallback) {}

  observe(target: Element): void {
    const entry = { target, contentRect: { width: 400, height: 175 } };
    this.report([entry as unknown as ResizeObserverEntry], this as unknown as ResizeObserver);
  }

  unobserve(): void {}

  disconnect(): void {}
}

const noResizeObserver = globalThis.ResizeObserver;

beforeAll(() => {
  Object.assign(globalThis, { ResizeObserver: ObservedAt400By175 });
});

afterAll(() => {
  Object.assign(globalThis, { ResizeObserver: noResizeObserver });
});

beforeEach(() => {
  // `performance` and the animation frame as well as the timers: react-smooth drives the wedges
  // from `requestAnimationFrame` and reads the clock for its easing, so faking the timers alone
  // would leave the animation running in real time and the ring half-drawn.
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

/** Render the donut and run its opening animation out, so the ring is settled before it is read. */
async function drawn(data: readonly PieSlice[] = SLICES): Promise<HTMLElement> {
  const { container } = render(<SummaryPieChart title="Readiness" data={data} />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(SETTLED_MILLISECONDS);
  });
  return container;
}

/** The drawn wedges, keyed by the colour each was filled with rather than by their draw order. */
function wedges(container: HTMLElement): Record<string, Element> {
  return Object.fromEntries(
    [...container.querySelectorAll('.recharts-pie-sector')].map((sector) => [
      sector.querySelector('path')?.getAttribute('fill') ?? 'unfilled',
      sector,
    ]),
  );
}

/** One wedge by its fill, refusing rather than hovering nothing if the ring did not draw it. */
function wedge(container: HTMLElement, colour: string): Element {
  const drawn = wedges(container)[colour];
  if (drawn === undefined) {
    throw new Error(`no wedge was drawn in ${colour}`);
  }
  return drawn;
}

/** What the hover card says right now, or the empty string when there is no card. */
function card(container: HTMLElement): string {
  return container.querySelector('.recharts-tooltip-wrapper')?.textContent ?? '';
}

describe('SummaryPieChart hover card', () => {
  it('draws one wedge per counted slice, leaving the empty one out of the ring', async () => {
    const container = await drawn();

    // Caution was counted at zero: it stays in the legend, dimmed, but a zero-width wedge with
    // `paddingAngle` on draws as a stray tick, so it is not in the ring.
    expect(Object.keys(wedges(container)).sort()).toEqual(['#4ade80', '#f87171']);
    expect(screen.getByText('Caution')).toBeTruthy();
  });

  it('names the hovered slice with its count and its share of the total', async () => {
    const container = await drawn();

    fireEvent.mouseOver(wedge(container, '#4ade80'));
    // 5 of 8, in the same words `percentageOf` prints in the figures beside the chart.
    expect(card(container)).toContain('Ready');
    expect(card(container)).toContain('5');
    expect(card(container)).toContain('62.5%');
  });

  it('answers for the wedge under the pointer, not the one hovered before it', async () => {
    const container = await drawn();

    fireEvent.mouseOver(wedge(container, '#4ade80'));
    fireEvent.mouseOver(wedge(container, '#f87171'));

    expect(card(container)).toContain('Blocked');
    expect(card(container)).toContain('37.5%');
    expect(card(container)).not.toContain('Ready');
  });

  it('colours the slice name to match the wedge it came from', async () => {
    const container = await drawn();

    fireEvent.mouseOver(wedge(container, '#f87171'));

    // Scoped to the card: `Blocked` is in the legend too, in its own colour, which would pass this
    // whatever the card did.
    const name = within(container.querySelector('.recharts-tooltip-wrapper') as HTMLElement)
      .getByText('Blocked');
    expect(name.getAttribute('style')).toContain('rgb(248, 113, 113)');
  });

  it('takes the card away when the pointer leaves, rather than leaving a figure up', async () => {
    const container = await drawn();

    fireEvent.mouseOver(wedge(container, '#4ade80'));
    expect(card(container)).toContain('Ready');

    fireEvent.mouseLeave(container.querySelector('.recharts-wrapper') as Element);

    expect(card(container)).toBe('');
  });

  it('has nothing to hover when nothing was counted, and says so instead', async () => {
    const container = await drawn([
      { name: 'Ready', value: 0, color: '#4ade80' },
      { name: 'Blocked', value: 0, color: '#f87171' },
    ]);

    expect(wedges(container)).toEqual({});
    expect(screen.getByText('No data')).toBeTruthy();
  });

  it('draws a single slice as one whole wedge, with no gap to pad', async () => {
    const container = await drawn([{ name: 'Ready', value: 4, color: '#4ade80' }]);

    expect(Object.keys(wedges(container))).toEqual(['#4ade80']);
    fireEvent.mouseOver(wedge(container, '#4ade80'));
    expect(card(container)).toContain('100%');
  });
});
