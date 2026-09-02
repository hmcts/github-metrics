import { SkeletonList } from '@/components/Skeleton';

/** The contributor list's bones: the organisation header above one table. */
export default function LoadingContributors() {
  return <SkeletonList rows={12} />;
}
