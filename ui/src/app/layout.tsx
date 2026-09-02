import type { Metadata } from 'next';
import './globals.css';
import { Navigation } from '@/components/Navigation';

export const metadata: Metadata = {
  title: 'GitHub Metrics Dashboard',
  description: 'Repository, contributor, and team evidence from the metrics caches',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="bg-slate-950 text-slate-100 min-h-screen antialiased">
        <Navigation />
        <main className="max-w-full mx-auto px-6 py-8">{children}</main>
      </body>
    </html>
  );
}
