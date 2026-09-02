import { SkeletonCards, SkeletonHeader, SkeletonPage, SkeletonSection } from '@/components/Skeleton';

/**
 * One repository's bones: the entity header, the cohort row, and the first few sections of the
 * evidence block.
 *
 * Four sections rather than the page's ten. The block is longer than a viewport either way, and a
 * screenful of bones says a page is coming just as well as a full-length copy of it would — while
 * costing nothing when the order of the sections below changes, as it does in this plan twice.
 */
export default function LoadingRepository() {
  return (
    <SkeletonPage>
      <SkeletonHeader />
      <SkeletonCards count={4} />
      <SkeletonSection rows={4} />
      <SkeletonSection rows={6} />
      <SkeletonSection rows={4} />
    </SkeletonPage>
  );
}
