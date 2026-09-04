import clsx from 'clsx';
import { ProductionBadge } from '@/components/ProductionBadge';
import { RAGLabel } from '@/components/RAGCard';
import { borderClass } from '@/lib/rag';
import type { ReadinessLabel } from '@/lib/types';

/**
 * The head of a repository, contributor or team page: where you are now, and nothing about how you
 * got here.
 *
 * NO BREADCRUMBS. Drill-through here is a graph, not a tree — a repository leads to its
 * contributors, a contributor back to other repositories, either to a team — so a trail would claim
 * a hierarchy that does not exist and would differ depending on which link the reader happened to
 * follow. The `context` line instead names what this entity is related to, as links onward.
 *
 * The kind is RENDERED, as the word above the entity name, which is why its members are the reader's
 * words rather than the contract's: `contributor` from 2026-09-02, where the service still calls the
 * author of a merge an actor. The page's route followed the word later the same day —
 * `/contributors/[login]`, where it was `/actors/[login]` — and the service's own `/actors` endpoints
 * and field names did not move.
 */
export type EntityKind = 'repository' | 'contributor' | 'team';

export function EntityHeader({
  kind,
  name,
  label,
  production,
  context,
  action,
}: {
  kind: EntityKind;
  name: string;
  /** Present only for a repository: contributors and teams are never graded. */
  label?: ReadinessLabel;
  /**
   * Whether this repository deploys to production, badged beside the label.
   *
   * Also a repository's alone, but unlike the label it is not a grade and not a fact about the
   * window: the header carries it even where the span holds no evidence, which is why the page
   * passes it in above the unavailable branch.
   */
  production?: boolean;
  context?: React.ReactNode;
  /** The header's own control — the week selector, which every page carries at the top right. */
  action?: React.ReactNode;
}) {
  return (
    <header
      className={clsx(
        'bg-slate-900 border border-slate-800 rounded-lg p-5 space-y-2',
        label === undefined ? null : borderClass(label),
      )}
    >
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-xs font-semibold text-slate-500 uppercase tracking-wide">{kind}</span>
        <h1 className="font-mono text-xl text-slate-100 break-all">{name}</h1>
        {label ? <RAGLabel label={label} /> : null}
        <ProductionBadge production={production} />

        {action ? <div className="ml-auto">{action}</div> : null}
      </div>
      {context ? (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-slate-400">
          {context}
        </div>
      ) : null}
    </header>
  );
}
