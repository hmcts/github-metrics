'use client';

import { usePathname, useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
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
 */
export function NavWeekSelector({ options, active }: { options: readonly number[]; active: number }) {
  const pathname = usePathname();
  const router = useRouter();
  // Tracks the click so the button highlights immediately, without waiting for the server render the
  // navigation triggers — otherwise the pressed state lags the whole round trip.
  const [selected, setSelected] = useState<number | null>(null);
  const current = selected ?? active;

  // Dropped the moment a render arrives at a different span. Only the query string changes on a span
  // switch, so React keeps this component mounted and the optimistic value would outlive what it was
  // guessing at: pressing Back would leave the button for the span the reader just left highlighted
  // over figures that are now the other window's.
  useEffect(() => setSelected(null), [active]);

  function choose(weeks: number) {
    setSelected(weeks);
    document.cookie = weeksCookie(weeks);
    // Read from the live location rather than `useSearchParams`, so any filter a client component
    // put in the URL with `replaceState` survives a span change.
    const parameters = new URLSearchParams(window.location.search);
    parameters.set('weeks', String(weeks));
    router.replace(`${pathname}?${parameters.toString()}`);
  }

  return (
    <div className="flex items-center gap-1.5" role="group" aria-label="Reporting window">
      {options.map((weeks) => (
        <button
          key={weeks}
          type="button"
          onClick={() => choose(weeks)}
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
