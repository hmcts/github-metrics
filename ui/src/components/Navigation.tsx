import Link from 'next/link';
import { BarChart3, Building2, FolderGit2, Users } from 'lucide-react';

/**
 * The bar every page sits under: the site name and the three estate lists.
 *
 * These were anchors into one overview page until 2026-09-02, because three list pages would each
 * have fetched the same bundle to show a third of it. They are routes now: the bundle is held per
 * span and kept warm by the service, so a list page costs a round trip against a report already
 * built, and in exchange each list has a URL to link to, its own loading state, and a browser Back
 * that goes back rather than jumping up the page it never left.
 *
 * THE ORDER IS THE ESTATE'S, from 2026-09-02: repositories, then the teams that own them, then the
 * people who work in them. Contributors sat second until then, which put the widest list of the
 * three between a repository and the team holding it.
 *
 * NO HOME LINK. Repositories is the landing page, so a Home button would be a second name for a link
 * already in this bar. `/` redirects there for everything already pointing at it, and the site name
 * to the left goes to the same place — which is what a logo is for.
 *
 * NO ORGANISATION NAME AND NO WEEK SELECTOR HERE. Both were slots on this component once, but the
 * layout renders it without props and each page states its own span and its own subject in its
 * header, beside the figures they apply to — which is where a reader checking what a number covers
 * looks. Two places claiming the span is worse than one.
 *
 * These links are therefore the only ones in the app that carry no `?weeks=`: a layout is handed no
 * search parameters, so this component cannot know the span to put on them. The span survives a click
 * through the `weeks` cookie instead, which `src/proxy.ts` writes for any request that named one.
 */
export function Navigation() {
  return (
    <nav className="bg-slate-900 border-b border-slate-800 sticky top-0 z-50">
      <div className="max-w-screen-2xl mx-auto px-6 h-14 flex items-center gap-6">
        <Link
          href="/repositories"
          className="flex items-center gap-2 font-semibold text-indigo-400 shrink-0"
        >
          <BarChart3 className="w-5 h-5" />
          <span>GitHub Metrics</span>
        </Link>

        <div className="flex items-center gap-1">
          <NavigationLink
            href="/repositories"
            icon={<FolderGit2 className="w-4 h-4" />}
            label="Repositories"
          />
          <NavigationLink href="/teams" icon={<Building2 className="w-4 h-4" />} label="Teams" />
          {/* The route is spelled the reader's way from 2026-09-02: `/contributors`, over a service
              that still calls the author of a merge an actor and still serves `/actors`. A
              `/contributors` list above an `/actors/[login]` detail was one thing with two names. */}
          <NavigationLink
            href="/contributors"
            icon={<Users className="w-4 h-4" />}
            label="Contributors"
          />
        </div>
      </div>
    </nav>
  );
}

function NavigationLink({
  href,
  icon,
  label,
}: {
  href: string;
  icon: React.ReactNode;
  label: string;
}) {
  return (
    <Link
      href={href}
      className="flex items-center gap-1.5 px-3 py-1.5 rounded text-sm text-slate-400 hover:text-slate-100 hover:bg-slate-800 transition-colors"
    >
      {icon}
      {label}
    </Link>
  );
}
