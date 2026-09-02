import { collectionNotice } from '@/lib/collection';
import type { WindowOptions } from '@/lib/types';

/**
 * The bar every page carries when its figures are anchored at an old collection.
 *
 * Above the content rather than below it, and on every page rather than on the landing page alone: the
 * window a repository page states is the same anchored window, and a reader who arrives from a link
 * never passes through the landing page to be told so.
 *
 * NO EMOJI, as everywhere else on this site: an amber bar down the left edge and the sentence beside
 * it, with the sentence carrying all of the information (`rag.ts`). `role="status"` rather than
 * `alert`, because nothing here is happening now — it is a standing condition of the page, and an
 * assertive announcement would interrupt a reader for a fortnight-old collection on every navigation.
 *
 * Renders nothing at all when the collection is current. A page that said "the collection is recent"
 * on every visit would train the reader to skip the bar on the one visit it matters.
 */
export function CollectionNotice({ windows }: { windows: WindowOptions }) {
  const notice = collectionNotice(windows);
  if (notice === null) {
    return null;
  }
  return (
    <div
      role="status"
      className="bg-amber-950/40 border border-amber-900 border-l-4 border-l-rag-amber rounded-lg px-4 py-3"
    >
      <p className="text-sm text-amber-200">{notice}</p>
    </div>
  );
}
