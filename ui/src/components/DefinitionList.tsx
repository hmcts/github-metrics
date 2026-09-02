import clsx from 'clsx';
import { valueClass, type Tone } from '@/lib/tone';

/**
 * A settings list: what was asked on the left, what came back on the right, one row per line.
 *
 * The shape the predecessor tool's BRANCH PROTECTION panel read well in, and the reason it did is
 * the one this exists for. Twelve merge-gate fields drawn as twelve `MetricCard`s put twelve
 * two-word answers at headline size across three rows of a four-across grid, so `yes` and `not
 * disclosed` shouted as loudly as the cohort figures above them and no field sat beside the one it
 * belonged with. As a list the labels line up, the answers line up, and the block is read down.
 *
 * A LIST, NOT A TABLE. There is one answer per label and no column to compare down, so this is a
 * `<dl>`: a screen reader reads "Protected, yes" as the pair it is, and no header row is invented
 * for two columns that need none.
 *
 * Cards are still right for a headline figure — the cohort row, the open pull-request counts, the
 * Sonar measures — where the number is what the reader came for and the label only names it. The
 * split is which of the two is being read: a figure, or a setting's answer.
 *
 * Tone colours the VALUE and nothing else, as on `MetricCard`, and the threshold behind it is
 * `lib/tone.ts` rather than anything decided here. `tabular-nums` goes on a numeric answer only:
 * digits that line up down the right edge are worth the fixed width, and `not disclosed` in a
 * tabular face reads as a typo.
 */
export function DefinitionList({ values }: { values: readonly DefinitionRow[] }) {
  // Nothing at all rather than an empty bordered list: a block with no rows is one that was not
  // collected, and the page says so with an `EmptyState` carrying the reason.
  if (values.length === 0) {
    return null;
  }
  return (
    <dl className="divide-y divide-slate-800/50">
      {values.map((row) => (
        <div key={row.label} className="flex flex-wrap items-baseline gap-x-4 py-2 first:pt-0 last:pb-0">
          <dt className="text-sm text-slate-400">{row.label}</dt>
          <dd
            className={clsx(
              'ml-auto text-sm font-medium text-right',
              numeric(row.value) && 'tabular-nums',
              valueClass(row.tone),
            )}
          >
            {row.value}
          </dd>
          {/* Its own line under the pair, because a detail is a sentence: the severity breakdown
              behind an alert count, when a maintenance answer was last true. */}
          {row.detail ? <dd className="w-full text-xs text-slate-500">{row.detail}</dd> : null}
        </div>
      ))}
    </dl>
  );
}

/** One row: the same shape `repository.LabelledValue` carries, so a builder's rows pass straight in. */
export interface DefinitionRow {
  label: string;
  value: string;
  /** The sentence under the pair: what the answer was read from, or how it breaks down. */
  detail?: string;
  /** How the answer reads, from `lib/tone.ts`; absent means it carries no verdict. */
  tone?: Tone;
}

/**
 * Whether an answer is a number, and so worth setting in tabular figures.
 *
 * A count, a percentage or a dash for one that was never counted — the dash included, so a column
 * of counts with one absent value keeps its digits aligned. `yes`, `not disclosed` and a list of
 * required check contexts are prose and are left in the proportional face.
 */
function numeric(value: string): boolean {
  return /^-$|^[+-]?[\d,]+(\.\d+)?%?$/.test(value.trim());
}
