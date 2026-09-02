import Link from 'next/link';
import { BarChart3, Building2, FolderGit2, Home, Users } from 'lucide-react';

/**
 * The bar every page sits under: the site name and the four anchors into the overview.
 *
 * NO ORGANISATION NAME AND NO WEEK SELECTOR HERE. Both were slots on this component once, but the
 * layout renders it without props and each page states its own span and its own subject in its
 * header, beside the figures they apply to — which is where a reader checking what a number covers
 * looks. Two places claiming the span is worse than one.
 */
export function Navigation() {
  return (
    <nav className="bg-slate-900 border-b border-slate-800 sticky top-0 z-50">
      <div className="max-w-screen-2xl mx-auto px-6 h-14 flex items-center gap-6">
        <Link href="/" className="flex items-center gap-2 font-semibold text-indigo-400 shrink-0">
          <BarChart3 className="w-5 h-5" />
          <span>GitHub Metrics</span>
        </Link>

        {/* The three lists are sections of the overview page, so these are anchors into it rather
            than routes of their own: one request builds one window, and three list pages would each
            fetch the same bundle to show a third of it. */}
        <div className="flex items-center gap-1">
          <NavigationLink href="/" icon={<Home className="w-4 h-4" />} label="Home" />
          <NavigationLink
            href="/#repositories"
            icon={<FolderGit2 className="w-4 h-4" />}
            label="Repositories"
          />
          {/* The anchor stays `#actors` — the section id, the route and the contract field are all
              still spelled that way. The word the reader sees is CONTRIBUTORS, from 2026-09-02: it
              is what these people are, where "actor" is the service's own name for the author of a
              merge. Renaming the routes is a separate change. */}
          <NavigationLink
            href="/#actors"
            icon={<Users className="w-4 h-4" />}
            label="Contributors"
          />
          <NavigationLink href="/#teams" icon={<Building2 className="w-4 h-4" />} label="Teams" />
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
