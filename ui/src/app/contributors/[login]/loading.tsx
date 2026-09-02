import { SkeletonHeader, SkeletonPage, SkeletonSection } from '@/components/Skeleton';

/**
 * One contributor's bones: the entity header, the repositories they worked in, and one behaviour
 * section under it.
 *
 * One behaviour section rather than a guess at how many repositories the person touched: the page
 * renders one per repository, so the count is unknowable here and a single block is the honest
 * placeholder for "and then a section for each of these".
 */
export default function LoadingContributor() {
  return (
    <SkeletonPage>
      <SkeletonHeader />
      <SkeletonSection rows={5} />
      <SkeletonSection rows={4} />
    </SkeletonPage>
  );
}
