/**
 * The slice arithmetic the donut chart is drawn from.
 *
 * Kept out of the component because recharts renders in the browser and this is the part that has to
 * be checkable: a chart that silently drops a category is indistinguishable from a category with no
 * members, and only one of those is true of the data.
 *
 * A zero-count category stays in the returned slices. The chart hands recharts only the non-zero
 * ones — a zero-width wedge draws as a stray tick once `paddingAngle` is on — but the legend lists
 * every slice, dimmed when empty, so the reader sees the full label set whatever this window holds.
 */

import { RAG_HEX, RAG_LABEL, RAG_STATES, distributionState } from '@/lib/rag';

export interface PieSlice {
  /** The label shown in the legend and in the hover card. */
  name: string;
  value: number;
  /** A colour value, not a class: recharts takes fills as strings. */
  color: string;
}

/**
 * Turn a label distribution from the service into the five readiness slices, in RAG order.
 *
 * Counts are summed per state rather than per key, so the service's `not_assessed` and any key it
 * adds later that resolves to `none` land in one slice instead of two slices of the same colour.
 */
export function distributionSlices(labels: Record<string, number>): PieSlice[] {
  return RAG_STATES.map((readiness) => ({
    name: RAG_LABEL[readiness],
    value: Object.entries(labels)
      .filter(([key]) => distributionState(key) === readiness)
      .reduce((total, [, count]) => total + count, 0),
    color: RAG_HEX[readiness],
  }));
}

export function totalValue(slices: readonly PieSlice[]): number {
  return slices.reduce((total, slice) => total + slice.value, 0);
}

/** The slices recharts is given: the ones that have members. */
export function activeSlices(slices: readonly PieSlice[]): PieSlice[] {
  return slices.filter((slice) => slice.value > 0);
}
