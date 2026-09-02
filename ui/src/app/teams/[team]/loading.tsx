import { SkeletonChart, SkeletonHeader, SkeletonPage, SkeletonSection } from '@/components/Skeleton';

/**
 * One team's bones: the entity header, the readiness donut, and the two tables under it — the
 * repositories the team owns and the people who worked in them.
 */
export default function LoadingTeam() {
  return (
    <SkeletonPage>
      <SkeletonHeader />
      <SkeletonChart />
      <SkeletonSection rows={8} />
      <SkeletonSection rows={6} />
    </SkeletonPage>
  );
}
