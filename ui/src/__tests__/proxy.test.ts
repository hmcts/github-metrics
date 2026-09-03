import { NextRequest } from 'next/server';
import { describe, expect, it } from 'vitest';
import { config, proxy } from '@/proxy';

/**
 * What the navigation bar relies on: a request that named a span leaves with that span remembered.
 *
 * The bar's links carry no `?weeks=`, so they resolve from the cookie. These cases are the reader who
 * arrived on a shared link and never pressed a button — the one case the selector's own write cannot
 * cover.
 */
function ask(url: string, cookie?: string): NextRequest {
  const request = new NextRequest(new URL(url, 'https://metrics.example'));
  if (cookie !== undefined) {
    request.cookies.set('weeks', cookie);
  }
  return request;
}

function written(url: string, cookie?: string): string | null {
  return proxy(ask(url, cookie)).headers.get('set-cookie');
}

describe('proxy', () => {
  it('remembers the span a link named, so the next nav link keeps it', () => {
    expect(written('/repositories?weeks=26')).toContain('weeks=26');
    expect(written('/repositories?weeks=26')).toContain('path=/');
  });

  it('remembers it for a detail page as well as a list', () => {
    expect(written('/contributors/someone?weeks=12')).toContain('weeks=12');
  });

  it('writes nothing where the request named no span', () => {
    expect(written('/repositories')).toBeNull();
    expect(written('/teams?filter=platform')).toBeNull();
  });

  it('writes nothing where the cookie already holds the span', () => {
    expect(written('/repositories?weeks=26', '26')).toBeNull();
  });

  it('replaces a cookie holding a different span', () => {
    expect(written('/repositories?weeks=26', '4')).toContain('weeks=26');
  });

  it('writes nothing for a value that could not be a span', () => {
    expect(written('/repositories?weeks=soon')).toBeNull();
    expect(written('/repositories?weeks=-4')).toBeNull();
  });

  it('lets the page it was asked for through untouched', () => {
    expect(proxy(ask('/repositories?weeks=26')).status).toBe(200);
  });

  it('runs for pages and not for the build’s own assets', () => {
    const [matcher] = config.matcher;
    const pattern = new RegExp(`^${matcher}$`);
    expect(pattern.test('/repositories')).toBe(true);
    expect(pattern.test('/contributors/someone')).toBe(true);
    expect(pattern.test('/_next/static/chunks/main.js')).toBe(false);
  });
});
