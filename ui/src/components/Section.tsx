/**
 * The one section wrapper for the whole site, and the only box on a page that means anything.
 *
 * The predecessor grew three of these — a bare `<h2>`, a card with a heading, and a heading with a
 * control floated right — which drifted apart in spacing and in heading weight until two sections on
 * the same page looked like two kinds of thing. One component, one heading style.
 *
 * A section is now a BOUNDED PANEL rather than a heading with loose children under it. The
 * repository page had reached about forty bordered boxes with nothing on the page saying which of
 * them belonged together, so the box moved up a level: it is the section that is drawn, and the
 * figures inside it share one surface (see `MetricCard`, which gave its own border up for this).
 *
 * `detail` is for what the section was measured over, next to the heading rather than under it, so a
 * figure and its window are read together. `action` is the section's own control — a filter box, a
 * toggle — and sits at the end of the heading row, which a border divides from the body.
 */
export function Section({
  heading,
  detail,
  action,
  children,
}: {
  heading: string;
  detail?: string;
  action?: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <Panel>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-2 border-b border-slate-800 px-4 py-3">
        <h2 className="text-sm font-semibold text-slate-300 uppercase tracking-wide">{heading}</h2>
        {detail ? <span className="text-xs text-slate-500">{detail}</span> : null}
        {action ? <div className="ml-auto">{action}</div> : null}
      </div>
      {/* No body at all rather than an empty padded one: a panel with a heading and nothing under it
          is a section that had nothing to say, and blank space below the divider would read as
          content that failed to load. */}
      {children ? <div className="p-4">{children}</div> : null}
    </Panel>
  );
}

/**
 * The surface a `Section` is drawn on, for the two card rows that carry no heading of their own.
 *
 * The overview's four estate figures and a repository's cohort row are headline figures rather than
 * sections: they answer the question the page is titled with. They still need the panel, because
 * `MetricCard` no longer draws one and four unbounded figures would float on the page background —
 * so the surface is one component and its classes are written once.
 */
export function Panel({ children }: { children: React.ReactNode }) {
  return <section className="bg-slate-900/40 border border-slate-800 rounded-lg">{children}</section>;
}
