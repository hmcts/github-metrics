import clsx from 'clsx';
import { RAGLabel } from '@/components/RAGCard';
import { borderClass } from '@/lib/rag';
import type { ReadinessLabel } from '@/lib/types';

/**
 * The head of a repository, actor or team page: where you are now, and nothing about how you got here.
 *
 * NO BREADCRUMBS. Drill-through here is a graph, not a tree — a repository leads to its actors, an
 * actor back to other repositories, either to a team — so a trail would claim a hierarchy that does
 * not exist and would differ depending on which link the reader happened to follow. The `context`
 * line instead names what this entity is related to, as links onward.
 */
export type EntityKind = 'repository' | 'actor' | 'team';

export function EntityHeader({
  kind,
  name,
  label,
  context,
  action,
}: {
  kind: EntityKind;
  name: string;
  /** Present only for a repository: actors and teams are never graded. */
  label?: ReadinessLabel;
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
