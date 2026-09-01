/**
 * The one section wrapper for the whole site.
 *
 * The predecessor grew three of these — a bare `<h2>`, a card with a heading, and a heading with a
 * control floated right — which drifted apart in spacing and in heading weight until two sections on
 * the same page looked like two kinds of thing. One component, one heading style.
 *
 * `detail` is for what the section was measured over, next to the heading rather than under it, so a
 * figure and its window are read together. `action` is the section's own control — a filter box, a
 * toggle — and sits at the end of the heading row.
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
    <section className="space-y-3">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-2">
        <h2 className="text-sm font-semibold text-slate-300 uppercase tracking-wide">{heading}</h2>
        {detail ? <span className="text-xs text-slate-500">{detail}</span> : null}
        {action ? <div className="ml-auto">{action}</div> : null}
      </div>
      {children}
    </section>
  );
}
