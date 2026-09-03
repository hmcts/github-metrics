/**
 * @vitest-environment jsdom
 */

/**
 * The span selector's two halves: the guess it shows immediately, and the transition it waits in.
 *
 * `components.test.ts` asserts the markup of `WeekSpanButtons` at both pending states, because
 * `useTransition` reports `false` under `react-dom/server` and the dimming is otherwise unreachable.
 * What that cannot reach is the component that OWNS the router — whether the pending flag is ever
 * true, and whether the optimistic highlight is dropped when a render arrives at another span.
 *
 * The in-flight navigation is real rather than asserted about: `router.replace` here moves the
 * harness's span inside the transition, and a sibling under a `Suspense` boundary suspends on any
 * span the test has declared unfetched until it lets that render land. That is what a span change is
 * in production — a server render, the slowest thing on a cold span — so `pending` stays true across
 * it exactly as it does for a reader, and the reset the guess needs happens on the render that
 * lands.
 */

import { Suspense, useEffect, useState } from 'react';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NavWeekSelector } from '@/components/NavWeekSelector';

const OPTIONS = [4, 12, 26] as const;

let replaced: string[] = [];

/** Set from the harness's mount effect so the stubbed router can move the span it renders at. */
let arrive: (weeks: number) => void = () => undefined;

vi.mock('next/navigation', () => ({
  useRouter: () => ({
    replace: (target: string) => {
      replaced.push(target);
      arrive(Number(new URL(target, 'https://metrics.test').searchParams.get('weeks')));
    },
  }),
  usePathname: () => '/teams',
}));

/**
 * The spans whose render has not arrived, with the promise a render at one suspends on.
 *
 * Built by `inFlight` from a test's own body rather than during a render: the promise has to exist
 * before the component reaches for it, and creating one — or capturing its resolver — while
 * rendering is the side effect `react-hooks/globals` is about.
 */
const unfetched = new Map<number, { readonly render: Promise<void>; readonly land: () => void }>();

/** Declare that a render at `span` has not arrived yet, so reaching it suspends. */
function inFlight(span: number): void {
  let land = () => undefined as void;
  const render = new Promise<void>((arrived) => {
    land = () => {
      unfetched.delete(span);
      arrived();
    };
  });
  unfetched.set(span, { render, land });
}

/** Let the render at `span` arrive, as the server answering would. */
async function land(span: number): Promise<void> {
  const flight = unfetched.get(span);
  await act(async () => flight?.land());
}

/** The figures beside the selector: present for a fetched span, suspended for an unfetched one. */
function ServerRender({ span }: { span: number }) {
  const flight = unfetched.get(span);
  if (flight !== undefined) {
    throw flight.render;
  }
  return <p>{span} weeks of figures</p>;
}

/** The page around the selector, re-rendering at whichever span the router was sent to. */
function Page() {
  const [span, setSpan] = useState(4);
  useEffect(() => {
    arrive = setSpan;
  }, []);
  return (
    <>
      <NavWeekSelector options={OPTIONS} active={span} />
      <Suspense fallback={<p>Loading</p>}>
        <ServerRender span={span} />
      </Suspense>
    </>
  );
}

function group(): HTMLElement {
  return screen.getByRole('group', { name: 'Reporting window' });
}

function button(weeks: number): HTMLElement {
  return screen.getByLabelText(`${weeks} week window`);
}

/** The span the group says is selected, which is the guess while a render is in flight. */
function pressed(): number | undefined {
  return OPTIONS.find((weeks) => button(weeks).getAttribute('aria-pressed') === 'true');
}

function busy(): boolean {
  return group().getAttribute('aria-busy') === 'true';
}

/** Move the span without anybody pressing a button — a nav link, or the back button. */
async function navigateTo(span: number): Promise<void> {
  await act(async () => arrive(span));
}

beforeEach(() => {
  replaced = [];
  unfetched.clear();
  document.cookie = 'weeks=; path=/; max-age=0';
  window.history.replaceState(null, '', '/teams');
});

afterEach(cleanup);

describe('NavWeekSelector', () => {
  it('presses the span the page was rendered at, with nothing in flight', () => {
    render(<Page />);

    expect(pressed()).toBe(4);
    expect(busy()).toBe(false);
  });

  it('writes the cookie before it navigates, so the next render sees the new preference', () => {
    render(<Page />);

    fireEvent.click(button(12));

    expect(document.cookie).toContain('weeks=12');
    expect(replaced).toEqual(['/teams?weeks=12']);
  });

  it('carries a filter another control put in the URL through the span change', () => {
    window.history.replaceState(null, '', '/teams?repository=api');
    render(<Page />);

    fireEvent.click(button(26));

    expect(replaced).toEqual(['/teams?repository=api&weeks=26']);
  });

  it('presses the chosen span at once and says it is working until the render lands', async () => {
    inFlight(26);
    render(<Page />);

    fireEvent.click(button(26));

    // The guess: 26 is pressed while the page behind it is still the four-week one.
    expect(pressed()).toBe(26);
    expect(busy()).toBe(true);
    expect(screen.getByText('4 weeks of figures')).toBeTruthy();

    await land(26);

    // Both halves lift together — neither is left standing over figures the other gave up on.
    expect(pressed()).toBe(26);
    expect(busy()).toBe(false);
    expect(screen.getByText('26 weeks of figures')).toBeTruthy();
  });

  it('stays live while pending, so a reader can change their mind without waiting', async () => {
    inFlight(26);
    inFlight(12);
    render(<Page />);

    fireEvent.click(button(26));
    expect(busy()).toBe(true);
    fireEvent.click(button(12));

    expect(pressed()).toBe(12);
    expect(replaced).toEqual(['/teams?weeks=26', '/teams?weeks=12']);

    await land(12);

    expect(pressed()).toBe(12);
    expect(busy()).toBe(false);
    expect(screen.getByText('12 weeks of figures')).toBeTruthy();
  });

  // The component stays mounted across a span change — only the query string moves — so an
  // optimistic value that outlived its render would highlight the span the reader has just left.
  it('drops the guess when a render arrives at a span it was not made against', async () => {
    inFlight(26);
    render(<Page />);

    fireEvent.click(button(26));
    await land(26);
    expect(pressed()).toBe(26);

    // Back: the four-week page returns without anybody pressing a button.
    await navigateTo(4);

    expect(pressed()).toBe(4);
    expect(busy()).toBe(false);
  });

  it('does not resurrect the guess on a later render at another span', async () => {
    inFlight(12);
    render(<Page />);

    fireEvent.click(button(12));
    await land(12);
    await navigateTo(4);
    expect(pressed()).toBe(4);

    // Forward again, this time on a link rather than a click: still no stale 12.
    await navigateTo(26);

    expect(pressed()).toBe(26);
  });

  /**
   * Two clicks in flight, and the render for the FIRST one arrives.
   *
   * The reset the component does during render fires on any span it did not guess at, so a
   * superseded navigation answering could pull the highlight back to a span the reader has already
   * changed their mind about. It does not, and the reason is above the component: a span change is
   * one router navigation, so the second click replaces the first rather than racing it and the
   * render that lands is the second click's. The guess is dropped only when the span the page is
   * actually at moves — which is what the two tests below this exercise.
   *
   * Worth pinning because the reset cannot see the difference: it compares `active` against its own
   * last guess and knows nothing about how many clicks are outstanding.
   */
  it('keeps the later click pressed when the render for the earlier one answers', async () => {
    inFlight(12);
    inFlight(26);
    render(<Page />);

    fireEvent.click(button(12));
    fireEvent.click(button(26));
    expect(pressed()).toBe(26);
    expect(replaced).toEqual(['/teams?weeks=12', '/teams?weeks=26']);

    await land(12);

    // 12's page is not shown and 12 is not pressed: the reader's second choice superseded it, and
    // the figures beside the selector are still the four-week ones until 26 answers.
    expect(pressed()).toBe(26);
    expect(busy()).toBe(true);
    expect(screen.getByText('4 weeks of figures')).toBeTruthy();

    await land(26);

    expect(pressed()).toBe(26);
    expect(busy()).toBe(false);
    expect(screen.getByText('26 weeks of figures')).toBeTruthy();
  });

  it('offers exactly the spans it was given and no others', () => {
    render(<Page />);

    expect(screen.getAllByRole('button').map((element) => element.textContent)).toEqual([
      '4w',
      '12w',
      '26w',
    ]);
  });
});
