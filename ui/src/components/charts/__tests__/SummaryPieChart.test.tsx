/**
 * @vitest-environment jsdom
 */

/**
 * The donut's hover card and its clicks — the two halves of it that need a browser.
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
 *
 * The clicks are here for the same reason as the card. A donut given a `parameter` is the filter
 * control for its dimension, and where a legend entry or a wedge navigates to is only decidable
 * from a browser — `components/__tests__/charts.test.ts` can see that the entries are controls and
 * no further. Every navigation below is asserted to carry `weeks` through: a filter that dropped
 * the span would re-render the page at the default window under the filter just applied.
 */

import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { SummaryPieChart } from '@/components/charts/SummaryPieChart';
import type { PieSlice } from '@/lib/chart';

let replaced: string[] = [];

let parameters = new URLSearchParams();

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: (target: string) => void replaced.push(target) }),
  usePathname: () => '/repositories',
  useSearchParams: () => parameters,
}));

/** Put the page at a query string, in both places the component reads one from. */
function url(query: string): void {
  parameters = new URLSearchParams(query);
  window.history.replaceState(null, '', query === '' ? '/repositories' : `/repositories?${query}`);
}

/** A readiness distribution whose shares are not round, so a wrong divisor is visible. */
const SLICES: PieSlice[] = [
  { key: 'green', name: 'Ready', value: 5, color: '#4ade80' },
  { key: 'amber', name: 'Caution', value: 0, color: '#fbbf24' },
  { key: 'red', name: 'Blocked', value: 3, color: '#f87171' },
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
  replaced = [];
  url('weeks=12');
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
async function drawn(
  data: readonly PieSlice[] = SLICES,
  parameter?: string,
): Promise<HTMLElement> {
  const { container } = render(
    <SummaryPieChart title="Readiness" data={data} parameter={parameter} />,
  );
  await act(async () => {
    await vi.advanceTimersByTimeAsync(SETTLED_MILLISECONDS);
  });
  return container;
}

/** The same donut wired to the readiness parameter, which is how a list page draws it. */
function filtering(data: readonly PieSlice[] = SLICES): Promise<HTMLElement> {
  return drawn(data, 'label');
}

/** One legend entry as the control it is when the donut filters, found by the words on it. */
function entry(label: string): HTMLElement {
  return within(screen.getByRole('group', { name: 'Readiness filter' })).getByRole('button', {
    name: new RegExp(label),
  });
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
      { key: 'green', name: 'Ready', value: 0, color: '#4ade80' },
      { key: 'red', name: 'Blocked', value: 0, color: '#f87171' },
    ]);

    expect(wedges(container)).toEqual({});
    expect(screen.getByText('No data')).toBeTruthy();
  });

  it('draws a single slice as one whole wedge, with no gap to pad', async () => {
    const container = await drawn([{ key: 'green', name: 'Ready', value: 4, color: '#4ade80' }]);

    expect(Object.keys(wedges(container))).toEqual(['#4ade80']);
    fireEvent.mouseOver(wedge(container, '#4ade80'));
    expect(card(container)).toContain('100%');
  });
});

describe('SummaryPieChart as a filter control', () => {
  it('filters on the slice key rather than on the words beside it', async () => {
    url('weeks=26&repository=e');
    await filtering();

    fireEvent.click(entry('Ready'));

    // `green`, the key, not `Ready`: the legend can be reworded without breaking a shared link.
    expect(replaced).toEqual(['/repositories?weeks=26&repository=e&label=green']);
  });

  it('clears the filter on the active slice, by dropping the parameter rather than emptying it', async () => {
    url('weeks=12&label=green');
    await filtering();

    expect(entry('Ready').getAttribute('aria-pressed')).toBe('true');
    fireEvent.click(entry('Ready'));

    expect(replaced).toEqual(['/repositories?weeks=12']);
  });

  it('filters on the slice counted at zero as readily as on a populated one', async () => {
    await filtering();

    // Dimmed but not disabled: "which repositories are cautioned" is a question worth asking of a
    // window where the answer is none, and the empty table says so.
    fireEvent.click(entry('Caution'));

    expect(replaced).toEqual(['/repositories?weeks=12&label=amber']);
  });

  it('filters from the ring itself, the same as from the entry beside it', async () => {
    const container = await filtering();

    fireEvent.click(wedge(container, '#f87171'));

    expect(replaced).toEqual(['/repositories?weeks=12&label=red']);
  });

  it('clears the filter from the ring too, on the wedge already filtered on', async () => {
    url('weeks=12&label=red');
    const container = await filtering();

    fireEvent.click(wedge(container, '#f87171'));

    expect(replaced).toEqual(['/repositories?weeks=12']);
  });

  it('stays a picture where nothing said what it would filter', async () => {
    await drawn();

    fireEvent.click(screen.getByText('Ready'));

    expect(replaced).toEqual([]);
    expect(screen.queryByRole('group', { name: 'Readiness filter' })).toBeNull();
  });
});
