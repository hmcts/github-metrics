/**
 * The window span every page is read at, and how a page decides which one that is.
 *
 * The service is the authority on which spans exist — it filters its own list against the
 * configuration's maximum lookback, so a span the caches can never cover is not on offer — and
 * `GET /windows` is where a page learns them. Nothing here holds a list of its own: every page
 * awaits that answer before it resolves a span, and a copy kept beside it could only ever drift out
 * of step with the spans actually served.
 *
 * Resolution priority, highest first:
 *   1. an explicit `?weeks=` in the URL — the link the reader followed said so
 *   2. the `weeks` cookie — the last span they chose in the selector
 *   3. the service's own default
 *
 * The cookie exists so that arriving at a page through a nav link, which carries no `?weeks=`, shows
 * the span the reader was last reading at rather than resetting to four weeks. Two things write it:
 * the selector, before it navigates, and the proxy, for a reader who arrived on a link that
 * named a span without ever pressing a button — see `rememberableWeeks`.
 */

export const WEEKS_COOKIE = 'weeks';

/** A year, so the choice survives closing the browser: it is a reading preference, not a session. */
export const WEEKS_COOKIE_MAXIMUM_AGE = 365 * 24 * 3600;

/**
 * Parse one raw span, from a URL parameter or a cookie, against the spans actually on offer.
 *
 * A span off the list is rejected rather than clamped to the nearest one: the service refuses it for
 * the same reason, and a page showing 26 weeks of figures under a `?weeks=30` would be read as
 * covering the thirty.
 */
export function parseWeeks(raw: SearchValue, options: readonly number[]): number | null {
  const only = single(raw);
  if (only === undefined || only.trim() === '') {
    return null;
  }
  const parsed = Number(only);
  return Number.isInteger(parsed) && options.includes(parsed) ? parsed : null;
}

/**
 * One value of a query-string key, which Next.js hands over as an ARRAY when the key is repeated.
 *
 * `?weeks=1&weeks=4` is a URL anyone can type or a link anyone can mangle, and it arrives here as
 * `['1', '4']`. The first is taken rather than the request refused: a page is a reading of the
 * evidence and should render at some span, not answer a stray parameter with a server error.
 */
export type SearchValue = string | string[] | null | undefined;

function single(raw: SearchValue): string | undefined {
  if (raw === null || raw === undefined) {
    return undefined;
  }
  return Array.isArray(raw) ? raw[0] : raw;
}

/** Resolve the span a page renders at: URL, then cookie, then the service's default. */
export function resolveWeeks(
  parameter: SearchValue,
  cookie: string | null | undefined,
  options: readonly number[],
  fallback: number,
): number {
  return parseWeeks(parameter, options) ?? parseWeeks(cookie, options) ?? fallback;
}

/** Carry the current span onto a drill-through link, so no navigation silently changes the window. */
export function withWeeks(path: string, weeks: number): string {
  return carry(path, String(weeks));
}

/** One path with one raw span on it, encoded — the one place the query string is spelled out. */
function carry(path: string, weeks: string): string {
  return `${path}?${new URLSearchParams({ weeks }).toString()}`;
}

/** The route `/` redirects to, which is the repositories list. */
export const LANDING_PATH = '/repositories';

/**
 * Where a request for `/` goes, carrying the span it named.
 *
 * `/` was the overview holding all three lists until 2026-09-02 and is a redirect now. The raw
 * parameter is carried rather than a parsed span, so this needs no list of the spans on offer and the
 * one that arrives is resolved by the landing page exactly as it would have been by the old overview.
 * It is put back through `URLSearchParams`, so a value somebody typed reaches the redirect encoded
 * rather than as whatever they typed.
 */
export function landingTarget(parameter: SearchValue): string {
  const only = single(parameter);
  if (only === undefined || only.trim() === '') {
    return LANDING_PATH;
  }
  return carry(LANDING_PATH, only);
}

/**
 * The span in a raw `?weeks=` value worth remembering as the reader's preference, or null.
 *
 * The three links in the navigation bar carry no parameter — the bar is rendered by the layout,
 * which is handed no search parameters — so the span they land on comes from the cookie. A reader
 * who followed a shared `?weeks=26` link and never touched the selector has no cookie, and the
 * first nav click would drop them to the service's default with nothing saying the window moved.
 * The proxy closes that by writing what the URL asked for, which is the same preference the
 * selector would have written had they pressed the button themselves.
 *
 * The value is NOT checked against the spans on offer: the proxy would need a round trip to
 * `/windows` on every request to know them, and `resolveWeeks` already drops a cookie holding a span
 * off the list. A positive integer is the whole check, and it keeps what could not be a span out of
 * a cookie that lives a year. A number is returned rather than the raw text so the cookie is written
 * through `weeksCookie` like the selector's, and `04` reaches it as the `4` `parseWeeks` can match.
 */
export function rememberableWeeks(raw: SearchValue): number | null {
  const only = single(raw);
  if (only === undefined) {
    return null;
  }
  const parsed = Number(only.trim());
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}

/** Build the cookie the selector writes before it navigates. */
export function weeksCookie(weeks: number): string {
  return [
    `${WEEKS_COOKIE}=${weeks}`,
    'path=/',
    `max-age=${WEEKS_COOKIE_MAXIMUM_AGE}`,
    'SameSite=Lax',
  ].join('; ');
}
