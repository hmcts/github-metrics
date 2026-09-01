/**
 * One figure with its name under it — the unit every stat row on the site is built from.
 *
 * NO TONE AND NO COLOUR. A card that coloured itself would be grading the figure it shows, and the
 * readiness label is the only thing on this site that grades anything — it reaches the page through
 * `rag.ts` as a colour bar plus a word, on the rows the report actually labelled.
 *
 * An unmeasured figure is a dash, formatted by the caller through `format.ts`; there is no fallback
 * here, because a card that turned an absent value into `0` would be making the number up.
 */
export function MetricCard({
  label,
  value,
  detail,
}: {
  label: string;
  value: string | number;
  /** The line under the figure: what it was measured over, or when it was read. */
  detail?: string;
}) {
  return (
    <div className="bg-slate-900 rounded-lg p-4 border border-slate-800">
      <p className="text-xs text-slate-400 uppercase tracking-wide mb-1">{label}</p>
      <p className="text-2xl font-semibold tabular-nums text-slate-100">{value}</p>
      {detail ? <p className="text-xs text-slate-500 mt-1">{detail}</p> : null}
    </div>
  );
}
