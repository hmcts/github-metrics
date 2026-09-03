import {
  SkeletonCards,
  SkeletonChart,
  SkeletonHeader,
  SkeletonPage,
  SkeletonSection,
} from '@/components/Skeleton';

/**
 * The landing page's bones: the organisation header, the four estate figures, the six donuts and
 * the repositories table, in that order and at those sizes.
 *
 * The one page whose skeleton is not just a header over a table, because it is the one page carrying
 * figures above its list — a reader arriving here on a cold span should see the shape they are about
 * to get rather than a table that then has three blocks pushed in above it.
 */
export default function LoadingRepositories() {
  return (
    <SkeletonPage>
      <SkeletonHeader />
      <SkeletonCards count={4} />
      <SkeletonChart count={6} />
      <SkeletonSection rows={12} />
    </SkeletonPage>
  );
}
