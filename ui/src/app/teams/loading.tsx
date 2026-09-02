import { SkeletonList } from '@/components/Skeleton';

/**
 * The team list's bones: the organisation header above one table.
 *
 * Fewer rows than the other two lists, because an estate holds far fewer teams than repositories or
 * contributors and a dozen bones for a list of five would overstate what is coming.
 */
export default function LoadingTeams() {
  return <SkeletonList rows={6} />;
}
