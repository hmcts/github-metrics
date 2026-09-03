import { redirect } from 'next/navigation';
import { landingTarget, type SearchValue } from '@/lib/weeks';

/**
 * The old overview, which is now three routes: this redirects to the first of them.
 *
 * `/` held all three estate lists until 2026-09-02. Repositories is the landing page now, and this
 * segment stays rather than being deleted because everything already pointing at `/` — a bookmark,
 * the logo in the navigation bar, a link in somebody's message — must still arrive somewhere.
 *
 * `?weeks=` is carried through where one was given. A link to `/?weeks=26` said which window it meant,
 * and dropping the parameter on the way would land the reader on whatever span their cookie holds
 * while the URL they followed claimed 26 weeks. The value is not checked against the spans on offer
 * here: `/repositories` resolves it the same way every page does, and a span off the list falls back
 * there rather than being validated twice.
 */
export const dynamic = 'force-dynamic';

export default async function LandingPage({
  searchParams,
}: {
  searchParams?: Promise<{ weeks?: SearchValue }>;
}) {
  redirect(landingTarget((await searchParams)?.weeks));
}
