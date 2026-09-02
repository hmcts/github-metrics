import clsx from 'clsx';
import { badgeClass, borderClass, labelText } from '@/lib/rag';
import { borderClass as toneBorderClass, type Tone } from '@/lib/tone';
import type { ReadinessLabel } from '@/lib/types';

/**
 * A readiness label as a word, for a table cell or beside a name.
 *
 * NO EMOJI. The predecessor put a coloured square in front of every row; a screen reader read it as
 * nothing useful, and the same square rendered as three different shapes across the platforms the
 * team reads on. The word is the information here and the colour only supports it, which is also why
 * the badge keeps a border: a colour alone would not survive a monochrome print of the page.
 */
export function RAGLabel({ label }: { label?: ReadinessLabel }) {
  return (
    <span
      className={clsx(
        'inline-block rounded px-1.5 py-0.5 text-xs uppercase tracking-wide whitespace-nowrap',
        badgeClass(label),
      )}
    >
      {labelText(label)}
    </span>
  );
}

/**
 * A card carrying a graded thing: the colour bar down its left edge, the name, and the label.
 *
 * The bar is `border-l-4` from `rag.ts` rather than a background wash, so a row of cards with mixed
 * labels still reads as one surface, and the grade is legible down the edge without any card
 * shouting louder than the figures inside it.
 */
export function RAGCard({
  label,
  heading,
  detail,
  children,
}: {
  label?: ReadinessLabel;
  heading: React.ReactNode;
  /** The report's own sentence about why this grade was reached. */
  detail?: string;
  children?: React.ReactNode;
}) {
  return (
    <div
      className={clsx(
        'bg-slate-900 border border-slate-800 rounded-lg p-5 hover:border-slate-700 transition-colors',
        borderClass(label),
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm text-slate-100">{heading}</span>
        <RAGLabel label={label} />
      </div>
      {detail ? <p className="text-xs text-slate-400 mt-2">{detail}</p> : null}
      {children}
    </div>
  );
}

/**
 * The same treatment for one line inside a card — a readiness condition, a check that blocked.
 *
 * A row, not a card, because the conditions belong to the assessment above them; nesting cards would
 * read as a second set of independent findings.
 *
 * An unlabelled row gets no badge at all rather than a "Not assessed" one. Only a blocking condition
 * carries a label — it is the ceiling that condition imposed — and stamping the readiness policy's
 * "nothing was graded" wording onto a condition that deliberately grades nothing would read as a
 * gap in the assessment instead of as its normal shape.
 *
 * `tone` colours the bar on a row that carries no label, which is how a caution, a clear condition
 * and one the policy reported without judging tell themselves apart down the left edge. A LABEL
 * ALWAYS WINS: the policy's own ceiling is the stronger statement, and two colours on one row would
 * be two verdicts about it. What decides a row's tone is `tone.conditionTone`, not this component.
 */
export function RAGRow({
  label,
  tone,
  condition,
  detail,
}: {
  label?: ReadinessLabel;
  tone?: Tone;
  condition: string;
  detail: string;
}) {
  return (
    <div
      className={clsx(
        'bg-slate-900/50 rounded-r py-2 pl-3 pr-4',
        label ? borderClass(label) : toneBorderClass(tone),
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm text-slate-200">{condition}</span>
        {label ? <RAGLabel label={label} /> : null}
      </div>
      <p className="text-xs text-slate-400 mt-1">{detail}</p>
    </div>
  );
}
