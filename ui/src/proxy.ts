import { NextResponse, type NextRequest } from 'next/server';
import { WEEKS_COOKIE, rememberableWeeks, weeksCookie } from '@/lib/weeks';

/**
 * Remembers the span a request named, so the navigation bar cannot quietly change the window.
 *
 * Named `proxy` in `src/proxy.ts`: Next 16 deprecated the `middleware` file convention in favour of
 * this one, and the two are the same hook under two names.
 *
 * The three list links are rendered by the layout, which Next.js hands no search parameters, so they
 * carry no `?weeks=` and resolve their span from the cookie. The selector writes that cookie, which
 * covers a reader who chose a span here — but not one who arrived on a shared `/repositories?weeks=26`
 * link and clicked Contributors without touching the buttons: no cookie, so the whole page drops to
 * the service's default with nothing saying the window moved. Writing the cookie for any request that
 * named a span makes following such a link mean the same as pressing the button.
 *
 * It is written through `weeksCookie`, the same builder the selector uses, so the attributes are
 * spelled once. Nothing is written when the value could not be a span or when the cookie already
 * holds it — a `Set-Cookie` on every request would be noise on the way past.
 *
 * This does not touch what the CURRENT render reads: `resolveWeeks` takes `?weeks=` over the cookie,
 * so the page the reader asked for is the page they get, cookie or no cookie.
 */
export function proxy(request: NextRequest): NextResponse {
  const response = NextResponse.next();
  const asked = rememberableWeeks(request.nextUrl.searchParams.get(WEEKS_COOKIE));
  if (asked !== null && String(asked) !== request.cookies.get(WEEKS_COOKIE)?.value) {
    response.headers.append('set-cookie', weeksCookie(asked));
  }
  return response;
}

/**
 * Every route but the build's own assets.
 *
 * The pages are what carry `?weeks=`; a static chunk or an image never does, and running this for
 * each of them would be work with nothing to decide.
 */
export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
};
