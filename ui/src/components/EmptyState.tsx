/**
 * What a section shows instead of rows, and why it always says which kind of empty it is.
 *
 * "No findings in this window" and "this repository is not in the cached window" look identical as a
 * blank table and mean opposite things — one is a clean result, the other is missing data. The
 * `message` states which, and `detail` carries the instruction if there is one to give (typically
 * running `metrics collect` for the span being asked for).
 */
export function EmptyState({ message, detail }: { message: string; detail?: string }) {
  return (
    <div className="bg-slate-900 border border-dashed border-slate-800 rounded-lg p-5">
      <p className="text-sm text-slate-400">{message}</p>
      {detail ? <p className="text-xs text-slate-500 mt-1">{detail}</p> : null}
    </div>
  );
}
