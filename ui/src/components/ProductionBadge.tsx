import clsx from 'clsx';
import { PRODUCTION_BADGE, PRODUCTION_LABEL } from '@/lib/production';

/**
 * The `Production` badge: a repository's deployment approval, as a word beside its name.
 *
 * `RAGLabel`'s shape, sharing its span classes so a header carrying both reads as one row of labels
 * rather than two sizes of them, and with NO DISMISS CONTROL — it is not a filter chip and there is
 * nothing about it to clear.
 *
 * IT RENDERS NOTHING FOR `false` AND FOR `undefined` ALIKE. There is no non-production badge, so a
 * repository the list does not name and one whose list could not be read look the same to a reader,
 * which is intended: the badge's absence is not a claim. The distinction between those two answers
 * is kept in the field itself and spent by the filter and its count, not here — which is why this
 * guard is a single `!== true` rather than a pair of tests that would imply they differ on the page.
 */
export function ProductionBadge({ production }: { production?: boolean }) {
  if (production !== true) {
    return null;
  }
  return (
    <span
      className={clsx(
        'inline-block rounded px-1.5 py-0.5 text-xs uppercase tracking-wide whitespace-nowrap',
        PRODUCTION_BADGE,
      )}
    >
      {PRODUCTION_LABEL}
    </span>
  );
}
