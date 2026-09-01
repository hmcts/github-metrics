'use client';

import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';
import { Search, X } from 'lucide-react';
import { filterTarget } from '@/lib/filter';

/**
 * A filter term that lives in the URL rather than in this component's state alone.
 *
 * Everything a reader can see on a page should be reachable from its address: a filtered table is
 * the thing worth sending to somebody, and a term held only in React would be lost by the reload.
 * `router.replace`, not `push`, so typing eight characters does not put eight entries in the back
 * button.
 *
 * The navigation is debounced because each one is a server render that refetches the window; the
 * input itself is controlled and updates on every keystroke, so the box never feels laggy while the
 * table catches up.
 */
export const FILTER_DEBOUNCE_MILLISECONDS = 300;

export function FilterSearchBox({
  parameter,
  placeholder = 'Filter…',
  className,
}: {
  /** The query-string key this box reads and writes, e.g. `repository`. */
  parameter: string;
  placeholder?: string;
  className?: string;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParameters = useSearchParams();
  const [value, setValue] = useState<string>(() => searchParameters.get(parameter) ?? '');
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // The last term this box navigated to, so the effect below can tell a URL change it caused from
  // one it did not.
  const sent = useRef<string | null>(null);

  // Follow the URL when it changes under us — the back button, or a nav link to a page where this
  // box carries no term — so the input never shows a filter the table below it is not applying.
  //
  // BUT NOT OUR OWN NAVIGATION. Each `router.replace` is a server render that refetches the window,
  // and when one outlasts the next keystroke this effect would overwrite what the reader has just
  // typed with the term they typed 300ms ago: the input visibly reverts and the caret jumps. A term
  // this box sent is therefore skipped, leaving only the changes something else made.
  useEffect(() => {
    const term = searchParameters.get(parameter) ?? '';
    if (term === sent.current) {
      sent.current = null;
      return;
    }
    sent.current = null;
    setValue(term);
  }, [searchParameters, parameter]);

  // A pending navigation must not outlive the component: the timer captured this page's pathname,
  // and firing after the reader has moved on would replace the new route with the old one.
  useEffect(() => () => clearTimer(timer), []);

  function navigate(term: string) {
    sent.current = term;
    // `window.location.search` rather than `searchParameters`: the latter only updates on a router
    // navigation, so a parameter another control wrote with `replaceState` would be dropped here.
    router.replace(filterTarget(pathname, window.location.search, parameter, term), {
      scroll: false,
    });
  }

  function change(event: React.ChangeEvent<HTMLInputElement>) {
    const term = event.target.value;
    setValue(term);
    clearTimer(timer);
    timer.current = setTimeout(() => navigate(term), FILTER_DEBOUNCE_MILLISECONDS);
  }

  function clear() {
    setValue('');
    clearTimer(timer);
    navigate('');
  }

  return (
    <div className={`relative ${className ?? 'w-full max-w-sm'}`}>
      <Search
        className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500 pointer-events-none"
        aria-hidden="true"
      />
      <input
        type="text"
        value={value}
        onChange={change}
        placeholder={placeholder}
        aria-label={placeholder}
        className="w-full pl-9 pr-8 py-2 rounded-md bg-slate-900 border border-slate-700 text-sm text-slate-100 placeholder-slate-500 transition-colors focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
      />
      {value ? (
        <button
          type="button"
          onClick={clear}
          aria-label="Clear filter"
          className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-500 transition-colors hover:text-slate-300"
        >
          <X className="w-3.5 h-3.5" aria-hidden="true" />
        </button>
      ) : null}
    </div>
  );
}

function clearTimer(timer: React.MutableRefObject<ReturnType<typeof setTimeout> | null>) {
  if (timer.current) {
    clearTimeout(timer.current);
    timer.current = null;
  }
}
