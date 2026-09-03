/**
 * The app shell, and the one segment that is not a page: `/`, which redirects off itself.
 *
 * Neither is reachable from a component test. The layout is the only thing that renders `<html>`,
 * the navigation bar and the `<main>` every page is drawn into, and nothing below it can see whether
 * it still does — a layout that lost its `Navigation` type-checks, and every page keeps rendering
 * with no bar above it. `/` renders no markup at all: it is a `redirect` call, so what is asserted
 * is the target it asks for and whether the span the reader followed survives it.
 *
 * `next/navigation` is stubbed for the redirect, which throws its own control-flow error in a real
 * request. `layout.tsx` also imports `globals.css`, which Vitest resolves to nothing with CSS
 * processing off, so the shell renders here without Tailwind having to run.
 */

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import RootLayout, { metadata } from '@/app/layout';
import LandingPage, { dynamic } from '@/app/page';
import { LANDING_PATH } from '@/lib/weeks';

/**
 * Every target the stubbed `redirect` was asked for.
 *
 * Read only inside a test, so the mock factory below closing over it is safe despite being hoisted
 * above the declaration.
 */
const targets: string[] = [];

vi.mock('next/navigation', () => ({
  redirect: (target: string) => {
    targets.push(target);
  },
}));

beforeEach(() => {
  targets.length = 0;
});

describe('the root layout', () => {
  // The page goes in as a third argument rather than a `children` prop: the layout's own signature
  // names `children`, and `createElement` fills it either way.
  const markup = renderToStaticMarkup(
    createElement(RootLayout, null, createElement('p', null, 'the page itself')),
  );

  it('renders the document, in the dark theme the palette is written for', () => {
    // The site has one theme: every colour in `tailwind.config.ts` is chosen against slate-950, so
    // a shell that dropped `dark` would leave the whole estate unreadable rather than merely pale.
    expect(markup).toContain('<html lang="en" class="dark">');
    expect(markup).toContain('bg-slate-950');
    expect(markup).toContain('text-slate-100');
  });

  it('carries the navigation bar, so every page has one without asking for it', () => {
    expect(markup).toContain('<nav');
    for (const list of ['/repositories', '/teams', '/contributors']) {
      expect(markup).toContain(`href="${list}"`);
    }
  });

  it('draws the page it was handed inside the one main element', () => {
    expect(markup).toContain('<main class="max-w-full mx-auto px-6 py-8">');
    expect(markup).toContain('the page itself');
    expect(markup.split('<main').length).toBe(2);
    // The bar comes first: a page rendered above its own navigation would read as a page with none.
    expect(markup.indexOf('<nav')).toBeLessThan(markup.indexOf('<main'));
  });

  it('names the site in the metadata Next.js puts in the head', () => {
    expect(metadata.title).toBe('GitHub Metrics Dashboard');
    expect(metadata.description).toContain('metrics caches');
  });
});

describe('the landing redirect', () => {
  it('sends a request for / to the repositories list', async () => {
    await LandingPage({ searchParams: Promise.resolve({}) });
    expect(targets).toEqual([LANDING_PATH]);
  });

  /**
   * A link that named a span keeps it across the redirect.
   *
   * Dropping the parameter would land the reader on whatever span their cookie holds while the URL
   * they followed claimed 26 weeks — the one thing this segment exists to prevent.
   */
  it('carries a span the link named onto the list it redirects to', async () => {
    await LandingPage({ searchParams: Promise.resolve({ weeks: '26' }) });
    expect(targets).toEqual([`${LANDING_PATH}?weeks=26`]);
  });

  it('redirects with no span where the request was handed no search parameters at all', async () => {
    // Next.js omits the prop rather than passing an empty object for a request with no query
    // string, and awaiting a missing promise would throw rather than redirect.
    await LandingPage({});
    expect(targets).toEqual([LANDING_PATH]);
  });

  it('takes the first of a repeated parameter rather than answering a mangled link with an error', async () => {
    await LandingPage({ searchParams: Promise.resolve({ weeks: ['12', '26'] }) });
    expect(targets).toEqual([`${LANDING_PATH}?weeks=12`]);
  });

  it('is dynamic, so the redirect is not cached with one reader’s span baked into it', () => {
    expect(dynamic).toBe('force-dynamic');
  });
});
