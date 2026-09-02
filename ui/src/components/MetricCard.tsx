import { type Tone, valueClass } from '@/lib/tone';

/**
 * One figure with its name over it — the unit every stat row on the site is built from.
 *
 * FLAT: no background, no border, no box. The card used to draw itself as a bordered panel, which on
 * a page of nine sections came to about forty bordered boxes, nested inside their sections and
 * saying nothing about which figures belonged with which. The box is now the section (`Section.tsx`)
 * and the card is the figure inside it, so a grid of them reads as one surface with the gap between
 * cards doing the separating.
 *
 * TONE COLOURS THE VALUE, AND ONLY THE VALUE. The card carried the opposite rule until 2026-09-02 —
 * that a card grades nothing, and that the readiness label is the only thing on the site with a
 * colour — and the user reversed it: the predecessor tool coloured its figures and the colour is
 * what let a page be read at a glance. The threshold that decides the tone is never here; it is a
 * table in `lib/tone.ts`, alongside the `assessment.py` condition it answers to where there is one.
 * The label and the detail stay slate whatever the tone, so a coloured page still reads as one
 * surface with a few figures standing out of it rather than as a traffic light.
 *
 * No tone is the default and the majority: a figure with no threshold worth stating renders exactly
 * as it did before any of this, which is what keeps a colour meaning something where there is one.
 *
 * An unmeasured figure is a dash, formatted by the caller through `format.ts`; there is no fallback
 * here, because a card that turned an absent value into `0` would be making the number up.
 */
export function MetricCard({
  label,
  value,
  detail,
  tone,
}: {
  label: string;
  value: string | number;
  /** The line under the figure: what it was measured over, or when it was read. */
  detail?: string;
  /** How the figure reads, from `lib/tone.ts`; absent means it carries no verdict. */
  tone?: Tone;
}) {
  return (
    <div>
      <p className="text-xs text-slate-400 uppercase tracking-wide mb-1">{label}</p>
      <p className={`text-2xl font-semibold tabular-nums ${valueClass(tone)}`}>{value}</p>
      {detail ? <p className="text-xs text-slate-500 mt-1">{detail}</p> : null}
    </div>
  );
}
