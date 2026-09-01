import { Info } from 'lucide-react';

/**
 * The small explanation beside a heading — what a band means, what a metric counts.
 *
 * Not `title=`, which the predecessor used: a native tooltip takes a second to appear, cannot be
 * reached by keyboard, and never appears at all on a touch screen. The text is carried by
 * `aria-label` on the trigger, so assistive technology has it immediately, and the visible bubble is
 * plain CSS on hover and on focus. The bubble itself is `aria-hidden` so the same sentence is not
 * announced twice.
 */
export function InfoTooltip({ text }: { text: string }) {
  return (
    <span className="relative inline-flex group">
      <button
        type="button"
        aria-label={text}
        className="cursor-help text-slate-600 transition-colors hover:text-slate-300 focus:outline-none focus-visible:text-slate-300"
      >
        <Info className="w-3 h-3" aria-hidden="true" />
      </button>
      <span
        aria-hidden="true"
        className="pointer-events-none absolute left-1/2 top-full z-20 mt-1 hidden w-64 -translate-x-1/2 rounded border border-slate-700 bg-slate-800 p-2 text-xs font-normal normal-case tracking-normal text-slate-300 shadow-lg group-hover:block group-focus-within:block"
      >
        {text}
      </span>
    </span>
  );
}
