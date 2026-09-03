'use client';

import clsx from 'clsx';
import { usePathname, useRouter } from 'next/navigation';
import { useState, useTransition } from 'react';
import { weeksCookie } from '@/lib/weeks';

/**
 * The window-span selector each page puts in its own header, beside the subject it applies to.
 *
 * `options` are the spans the service reports, fetched from `GET /windows` by the page that renders
 * this, so the buttons can never offer a span the caches cannot cover. `active` is the span the page
 * was rendered at, already resolved from URL then cookie, which is why there is no effect here
 * reading either: the first paint is correct and never flashes.
 *
 * A click writes the cookie BEFORE navigating, so the server render it triggers already sees the new
 * preference when a page reads it as the default for a link that carries no `?weeks=`.
 *
 * `router.replace` rather than `push`: changing the window is re-reading the same page, and a history
 * entry per click would make the back button walk through span changes instead of leaving the page.
 *
 * The navigation runs inside a transition so the group can say it is working. The pressed highlight
 * alone claims the span has changed; while the server render is still in flight it has not, and on a
 * cold span that render is the slowest thing on the page. The two halves are the whole answer: which
 * button was pressed, and whether the page behind it has arrived.
 */
export function NavWeekSelector({ options, active }: { options: readonly number[]; active: number }) {
  const pathname = usePathname();
  const router = useRouter();
  // Tracks the click so the button highlights immediately, without waiting for the server render the
  // navigation triggers — otherwise the pressed state lags the whole round trip.
  const [selected, setSelected] = useState<number | null>(null);
  // The span the guess above was made against, so a render at a different one can be recognised.
  const [guessedFrom, setGuessedFrom] = useState(active);
  const [pending, startTransition] = useTransition();

  // The guess is dropped the moment a render arrives at a different span. Only the query string
  // changes on a span switch, so React keeps this component mounted and the optimistic value would
  // outlive what it was guessing at: pressing Back would leave the button for the span the reader
  // just left highlighted over figures that are now the other window's.
  //
  // Adjusted during render rather than in an effect: an effect would commit one paint showing the
  // stale guess over the new figures, and calling setState from an effect body cascades renders.
  // React re-runs this component with the reset state before painting, so nothing stale is shown.
  const stale = guessedFrom !== active;
  if (stale) {
    setGuessedFrom(active);
    setSelected(null);
  }

  // The transition ends on the same render this reset answers to, so the guess and the dimming lift
  // together: neither is left standing over figures the other has already given up on. No arm for
  // the stale case: the reset above re-runs this component before anything is committed, so by the
  // time a value reaches the DOM `selected` is already null and this is `active`.
  const current = selected ?? active;

  function choose(weeks: number) {
    setSelected(weeks);
    document.cookie = weeksCookie(weeks);
    // Read from the live location rather than `useSearchParams`, so any filter a client component
    // put in the URL with `replaceState` survives a span change.
    const parameters = new URLSearchParams(window.location.search);
    parameters.set('weeks', String(weeks));
    startTransition(() => router.replace(`${pathname}?${parameters.toString()}`));
  }

  return (
    <WeekSpanButtons options={options} current={current} pending={pending} onChoose={choose} />
  );
}

/**
 * The buttons themselves, with no router and no state of their own.
 *
 * Split out because `useTransition` reports `false` under `react-dom/server` by design — there is no
 * transition to be in — so the pending markup is unreachable through the parent in the renderer the
 * component tests use. Handed `pending` as a prop, what a reader actually sees while waiting is
 * asserted directly.
 *
 * The buttons stay live while pending: a reader who pressed 26 weeks and thought better of it can
 * press 4 without waiting out the first navigation, so the group is dimmed rather than disabled.
 */
export function WeekSpanButtons({
  options,
  current,
  pending,
  onChoose,
}: {
  options: readonly number[];
  current: number;
  pending: boolean;
  onChoose: (weeks: number) => void;
}) {
  return (
    <div
      className={clsx('flex items-center gap-1.5 transition-opacity', pending && 'opacity-50')}
      role="group"
      aria-label="Reporting window"
      aria-busy={pending}
    >
      {options.map((weeks) => (
        <button
          key={weeks}
          type="button"
          onClick={() => onChoose(weeks)}
          aria-pressed={current === weeks}
          aria-label={`${weeks} week window`}
          className={`px-3 py-1.5 rounded text-sm transition-colors ${
            current === weeks
              ? 'bg-indigo-600 text-white'
              : 'bg-slate-800 text-slate-400 hover:bg-slate-700 hover:text-slate-200'
          }`}
        >
          {weeks}w
        </button>
      ))}
    </div>
  );
}
