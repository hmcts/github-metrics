/**
 * Presentation for the readiness labels the evidence report grades repositories with.
 *
 * NO EMOJI ANYWHERE. A coloured square carries no text, does not survive a screen reader, and
 * renders differently on every platform, so a label reaches the page as a colour BAR plus a word:
 * `borderClass` for the bar, `RAG_LABEL` for the word. The bar is decoration, the word is the
 * information, and neither is load-bearing on its own.
 *
 * `cannot_assess` is deliberately slate rather than a shade between amber and red: the report means
 * "a half of the question could not be read", not "nearly bad", and colouring it warm would blame a
 * missing permission on the team that owns the repository.
 *
 * Every map is total over the four labels, plus `none` for a repository the readiness policy graded
 * nothing for, so a lookup can never miss and no component needs a fallback of its own.
 */

import type { ReadinessLabel } from '@/lib/types';

/** The five presentation states: the report's four labels, and "nothing was graded". */
export type RAGState = ReadinessLabel | 'none';

export const RAG_STATES: readonly RAGState[] = ['green', 'amber', 'red', 'cannot_assess', 'none'];

/** Resolve an optional label — absent from the JSON when nothing was graded — to a state. */
export function state(label: ReadinessLabel | null | undefined): RAGState {
  return label ?? 'none';
}

export const RAG_LABEL: Record<RAGState, string> = {
  green: 'Ready',
  amber: 'Caution',
  red: 'Blocked',
  cannot_assess: 'Cannot assess',
  none: 'Not assessed',
};

/** The left colour bar that replaces every emoji use, sized so it reads at a glance in a table row. */
export const RAG_BORDER: Record<RAGState, string> = {
  green: 'border-l-4 border-l-rag-green',
  amber: 'border-l-4 border-l-rag-amber',
  red: 'border-l-4 border-l-rag-red',
  cannot_assess: 'border-l-4 border-l-rag-none',
  none: 'border-l-4 border-l-rag-none',
};

export const RAG_BADGE: Record<RAGState, string> = {
  green: 'bg-green-950 text-green-300 border border-green-800',
  amber: 'bg-amber-950 text-amber-300 border border-amber-800',
  red: 'bg-red-950 text-red-300 border border-red-800',
  cannot_assess: 'bg-slate-800 text-slate-400 border border-slate-700',
  none: 'bg-slate-800 text-slate-500 border border-slate-700',
};

export const RAG_DOT: Record<RAGState, string> = {
  green: 'bg-rag-green',
  amber: 'bg-rag-amber',
  red: 'bg-rag-red',
  cannot_assess: 'bg-rag-none',
  none: 'bg-rag-none',
};

/** Hexes for the chart marks, where a Tailwind class cannot reach: recharts takes colours as values. */
export const RAG_HEX: Record<RAGState, string> = {
  green: '#4ade80',
  amber: '#fbbf24',
  red: '#f87171',
  cannot_assess: '#64748b',
  none: '#64748b',
};

export function borderClass(label: ReadinessLabel | null | undefined): string {
  return RAG_BORDER[state(label)];
}

export function badgeClass(label: ReadinessLabel | null | undefined): string {
  return RAG_BADGE[state(label)];
}

export function labelText(label: ReadinessLabel | null | undefined): string {
  return RAG_LABEL[state(label)];
}

/**
 * Where a label sorts: by what it says, not by the first letter of the word it says it with.
 *
 * `RAG_STATES` is already in the order a reader means by "sort by readiness" — ready, caution,
 * blocked, then the one that could not be read — while the English labels sort as Blocked, Cannot
 * assess, Caution, Ready, which is an order about spelling.
 *
 * A repository the policy graded nothing for has no place in that order and sorts as unmeasured,
 * which `sorted` puts last in both directions: "which repositories are worst" is a question about
 * the graded ones, and an ungraded repository is not the answer to it read either way round.
 */
export function severity(label: ReadinessLabel | null | undefined): number | undefined {
  const resolved = state(label);
  return resolved === 'none' ? undefined : RAG_STATES.indexOf(resolved);
}

/**
 * The digit each state contributes to a combination key, total over `RAGState` as every map here is.
 *
 * `cannot_assess` and `none` contribute nothing and SAY SO rather than being left out: a label added
 * to `ReadinessLabel` then has to be given a digit or ruled out of the order deliberately, where a
 * partial map would silently drop it and sort everybody carrying only it as if they had no label.
 */
const COMBINATION_DIGIT: Record<RAGState, string | undefined> = {
  green: '1',
  amber: '2',
  red: '3',
  cannot_assess: undefined,
  none: undefined,
};

/**
 * Where a COMBINATION of labels sorts: the distinct digits of its labels, sorted and concatenated.
 *
 * The key is a string of digits rather than a number because the order wanted is the order of the
 * combinations themselves — `green` before `green, amber` before `green, amber, red` before
 * `green, red`, which is `"1" < "12" < "123" < "13"`. No arithmetic on a severity gives that: a sum
 * puts `green, red` (1 + 3) level with `green, amber, red` (1 + 2 + 3) or above it depending on the
 * weights, a maximum loses `green` entirely, and a minimum loses `red`. Comparing text prefix by
 * prefix is what reads "all green, then green with a caution, then green with a caution and a block,
 * then green with a block", and it is the whole reason the digits are sorted before they are joined.
 *
 * `cannot_assess` is dropped, as `domain.reported_repositories` drops it from the person's line in
 * the text report — the labels here are the ones the report would print. A person with nothing left
 * to label has no place in the order and is unmeasured, which `sorted` puts last in both directions:
 * "who is all green" and "who is blocked" are both questions about the people with a label.
 *
 * An ABSENT list reads as an empty one. A service of this version defaults `ActorRow.labels` to `()`
 * and serves it empty rather than omitting it, so the key can only be missing against a
 * `metrics-serve` older than 2026-09-02 — which `API_URL` being read per request allows. Such a
 * person is unlabelled, which is the answer the empty case already gives; iterating the absent list
 * instead would throw and take the page with it.
 */
export function combinationKey(labels: readonly ReadinessLabel[] | undefined): string | undefined {
  const digits = new Set<string>();
  for (const label of labels ?? []) {
    const digit = COMBINATION_DIGIT[label];
    if (digit !== undefined) {
      digits.add(digit);
    }
  }
  return digits.size === 0 ? undefined : [...digits].sort().join('');
}

/**
 * Whether the readiness policy graded ANYTHING in this window, read off a label distribution.
 *
 * The four labels are the policy's answers and `not_assessed` is its absence, so a distribution
 * where every repository is counted under the latter means the policy graded nothing at all —
 * `AssessmentConfiguration.enabled` off, which `evidence.py` reports as `assessment=None` for every
 * repository. That is a different fact from a repository the policy graded and could not read, and
 * the two are indistinguishable from one person's row: `domain.actor_labels` returns an empty list
 * for both, so a page that reads emptiness alone states the wrong one for half its readers.
 *
 * Total over `RAGStates` rather than over a list of the service's four keys, as every map in this
 * module is: a label added to `ReadinessLabel` counts here without this function being touched.
 */
export function anyLabelled(labels: Record<string, number>): boolean {
  return RAG_STATES.some((resolved) => resolved !== 'none' && (labels[resolved] ?? 0) > 0);
}

/**
 * Resolve a key from a label DISTRIBUTION to a state.
 *
 * The service counts ungraded repositories under `not_assessed`, its own key rather than a label, so
 * a donut slice and a table row reach the same colour for the same repositories.
 */
export function distributionState(key: string): RAGState {
  return RAG_STATES.includes(key as RAGState) ? (key as RAGState) : 'none';
}
