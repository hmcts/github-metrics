import { Panel } from '@/components/Section';

/**
 * The bones of a page, for the `loading.tsx` each route segment now carries.
 *
 * A first visit or a link followed into a page can land on a cold bundle, and the service can take
 * seconds to build one. Next.js shows nothing of a segment until its data has arrived, so without a
 * loading boundary a reader waits on a blank page with nothing saying anything is coming. These are
 * what it shows instead: the same panels and the same rows with no numbers in them — so the page
 * does not jump when it arrives, and nothing on it can be misread as a figure.
 *
 * A SPAN SWITCH IS THE OTHER HALF AND IS NOT THIS. Only the query string changes there, so the
 * segment is already rendered and no boundary is entered: Next.js holds the old page on screen, and
 * what says the 4-week figures are on their way out is `NavWeekSelector`'s transition dimming its
 * own button group. Neither half covers the other's case.
 *
 * Drawn from `Panel`, the surface every section already uses, rather than a shape of their own. A
 * skeleton whose boxes sat somewhere else would be a second layout to keep in step with the first.
 *
 * One `role="status"` sits at the top of each skeleton and says LOADING in words. The bars are
 * `aria-hidden`: a screen reader gets the sentence, not thirty empty boxes.
 */
export function SkeletonPage({ children }: { children: React.ReactNode }) {
  return (
    <div className="space-y-8">
      <p role="status" className="sr-only">
        Loading
      </p>
      <div aria-hidden="true" className="space-y-8">
        {children}
      </div>
    </div>
  );
}

/** One pulsing bar. `className` carries its size, because a bone is only ever a size. */
export function SkeletonBar({ className }: { className: string }) {
  return <div className={`animate-pulse rounded bg-slate-800 ${className}`} />;
}

/**
 * The header block every page opens with, whether it names an organisation or one entity.
 *
 * Both are a kind word, a name in mono and a line of context under them, with the week selector at
 * the end of the first row — so one shape stands in for both rather than two that would drift.
 */
export function SkeletonHeader() {
  return (
    <header className="bg-slate-900 border border-slate-800 rounded-lg p-5 space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <SkeletonBar className="h-3 w-20" />
        <SkeletonBar className="h-6 w-64 max-w-full" />
        <div className="ml-auto flex items-center gap-1.5">
          {[0, 1, 2, 3, 4].map((index) => (
            <SkeletonBar key={index} className="h-8 w-12" />
          ))}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <SkeletonBar className="h-3 w-44" />
        <SkeletonBar className="h-3 w-24" />
        <SkeletonBar className="h-3 w-36" />
      </div>
    </header>
  );
}

/** A row of headline figures: a label bar over a figure bar, on the panel the cards share. */
export function SkeletonCards({ count }: { count: number }) {
  return (
    <Panel>
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-4 p-4">
        {Array.from({ length: count }, (_, index) => (
          <div key={index} className="space-y-2">
            <SkeletonBar className="h-3 w-24" />
            <SkeletonBar className="h-8 w-16" />
          </div>
        ))}
      </div>
    </Panel>
  );
}

/**
 * A section: the heading row, the divider under it, and `rows` bars where the body will be.
 *
 * `rows` is what the page usually holds rather than what it might — a table of five bones that
 * becomes forty rows still tells the reader a table is coming, and forty bones for a section that
 * turns out to hold two would be a worse guess in the other direction.
 */
export function SkeletonSection({ rows }: { rows: number }) {
  return (
    <Panel>
      <div className="flex items-baseline gap-x-3 border-b border-slate-800 px-4 py-3">
        <SkeletonBar className="h-4 w-32" />
        <SkeletonBar className="h-3 w-40" />
      </div>
      <div className="space-y-3 p-4">
        {Array.from({ length: rows }, (_, index) => (
          <SkeletonBar key={index} className="h-4 w-full" />
        ))}
      </div>
    </Panel>
  );
}

/**
 * `count` donut panels: a square bone where each chart goes, at the width the charts are given.
 *
 * The two grids are written out rather than interpolated, because Tailwind reads class names out of
 * the source and a built class would reach the browser undefined. A lone donut stays full width
 * until the row can hold three, which is how a detail page draws one; a row of them wraps two-up in
 * between, which is how the landing page draws five.
 */
export function SkeletonChart({ count = 1 }: { count?: number }) {
  const columns = count > 1 ? 'grid-cols-1 md:grid-cols-2 lg:grid-cols-3' : 'grid-cols-1 lg:grid-cols-3';
  return (
    <div className={`grid ${columns} gap-4`}>
      {Array.from({ length: count }, (_, index) => (
        <Panel key={index}>
          <div className="space-y-3 p-4">
            <SkeletonBar className="h-4 w-36" />
            <SkeletonBar className="h-48 w-full" />
          </div>
        </Panel>
      ))}
    </div>
  );
}

/**
 * The whole of a list route's skeleton: the organisation header above one table.
 *
 * The three list routes differ in the table they hold and in nothing above it, so they share this
 * rather than each keeping a copy of the header.
 */
export function SkeletonList({ rows }: { rows: number }) {
  return (
    <SkeletonPage>
      <SkeletonHeader />
      <SkeletonSection rows={rows} />
    </SkeletonPage>
  );
}
