/**
 * @vitest-environment jsdom
 */

/**
 * The filter box's behaviour over time, which is the whole of what it does.
 *
 * Every other component test in this directory renders through `react-dom/server`, where an effect
 * never runs and a timer never fires — so the debounce, the follow-the-URL effect and the unmount
 * cleanup are all unreachable there. This file opts into jsdom for that alone, with the docblock
 * above; the default environment stays `node` for the rest of the suite.
 *
 * The router is stubbed rather than driven: `router.replace` in Next is a server render, and what is
 * worth asserting is WHEN it is called and WITH WHAT, not what it renders. `useSearchParams` returns
 * one stable object that the tests replace only when the URL is meant to have changed, because the
 * follow-the-URL effect keys on its identity — handing back a fresh instance per render would fire
 * it on every keystroke and hide the bug it exists to avoid.
 */

import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FILTER_DEBOUNCE_MILLISECONDS, FilterSearchBox } from '@/components/FilterSearchBox';

/** What the stubbed router was asked to navigate to, in order. */
let replaced: string[] = [];

/** The query the stubbed `useSearchParams` reports, replaced only on a deliberate URL change. */
let parameters = new URLSearchParams();

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: (target: string) => void replaced.push(target) }),
  usePathname: () => '/repositories',
  useSearchParams: () => parameters,
}));

/** Move the URL on as a navigation would, giving the effect a new object to notice. */
function url(query: string): void {
  parameters = new URLSearchParams(query);
  window.history.replaceState(null, '', query === '' ? '/repositories' : `/repositories?${query}`);
}

function box(): HTMLInputElement {
  return screen.getByLabelText('Filter repositories') as HTMLInputElement;
}

function type(term: string): void {
  fireEvent.change(box(), { target: { value: term } });
}

/** Let the debounce elapse inside `act`, so the state the navigation sets is flushed. */
async function settle(milliseconds = FILTER_DEBOUNCE_MILLISECONDS): Promise<void> {
  await act(async () => {
    vi.advanceTimersByTime(milliseconds);
  });
}

function mount() {
  return render(<FilterSearchBox parameter="repository" placeholder="Filter repositories" />);
}

beforeEach(() => {
  vi.useFakeTimers();
  replaced = [];
  url('');
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('FilterSearchBox', () => {
  it('opens on the term already in the URL, so a shared link shows its own filter', () => {
    url('weeks=12&repository=api');
    mount();

    expect(box().value).toBe('api');
  });

  it('updates the input on every keystroke but navigates only once the typing stops', async () => {
    mount();

    type('a');
    type('ap');
    type('api');
    expect(box().value).toBe('api');
    await settle(FILTER_DEBOUNCE_MILLISECONDS - 1);
    expect(replaced).toEqual([]);

    await settle(1);
    expect(replaced).toEqual(['/repositories?repository=api']);
  });

  it('carries the rest of the query through the navigation, above all the span', async () => {
    url('weeks=26');
    mount();

    type('api');
    await settle();

    expect(replaced).toEqual(['/repositories?weeks=26&repository=api']);
  });

  it('clears on the button, immediately and by dropping the parameter rather than emptying it', async () => {
    url('repository=api');
    mount();

    fireEvent.click(screen.getByLabelText('Clear filter'));

    expect(box().value).toBe('');
    // No timer advanced: emptying the box is a decision, not a keystroke on the way to one.
    expect(replaced).toEqual(['/repositories']);
  });

  it('offers no clear button while the box is empty', () => {
    mount();

    expect(screen.queryByLabelText('Clear filter')).toBeNull();
  });

  it('drops a pending navigation when the term is cleared before the debounce fires', async () => {
    mount();

    type('api');
    fireEvent.click(screen.getByLabelText('Clear filter'));
    await settle();

    expect(replaced).toEqual(['/repositories']);
  });

  it('follows the URL when something else changes it, so the box matches the table', async () => {
    url('repository=api');
    const view = mount();
    expect(box().value).toBe('api');

    // The back button: a navigation this box did not make.
    url('');
    await act(async () => view.rerender(<FilterSearchBox parameter="repository" placeholder="Filter repositories" />));

    expect(box().value).toBe('');
  });

  it('does not overwrite what has since been typed when its own navigation lands', async () => {
    const view = mount();

    type('api');
    await settle();
    expect(replaced).toEqual(['/repositories?repository=api']);

    // The reader carries on typing while that server render is still in flight.
    type('apis');

    // …and it arrives, bringing the term this box itself sent.
    url('repository=api');
    await act(async () => view.rerender(<FilterSearchBox parameter="repository" placeholder="Filter repositories" />));

    expect(box().value).toBe('apis');
  });

  it('follows the URL again after skipping one of its own navigations', async () => {
    const view = mount();

    type('api');
    await settle();
    url('repository=api');
    await act(async () => view.rerender(<FilterSearchBox parameter="repository" placeholder="Filter repositories" />));

    // Back, to the unfiltered page: the skip must have been spent on the render above.
    url('');
    await act(async () => view.rerender(<FilterSearchBox parameter="repository" placeholder="Filter repositories" />));

    expect(box().value).toBe('');
  });

  it('does not navigate after unmounting, which would replace the route the reader moved to', async () => {
    const view = mount();

    type('api');
    view.unmount();
    await settle();

    expect(replaced).toEqual([]);
  });

  it('sizes itself from the class it is given, and to a readable default without one', () => {
    const { container } = render(<FilterSearchBox parameter="repository" className="w-40" />);

    expect(container.firstElementChild?.className).toContain('w-40');
    cleanup();
    expect(mount().container.firstElementChild?.className).toContain('max-w-sm');
  });
});
