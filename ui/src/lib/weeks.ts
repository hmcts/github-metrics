/**
 * The window span every page is read at, and how a page decides which one that is.
 *
 * The service is the authority on which spans exist — it filters its own list against the
 * configuration's maximum lookback, so a span the caches can never cover is not on offer — and
 * `GET /windows` is where a page learns them. The constants here are only what a page falls back to
 * before that answer arrives, or when a URL carries a span the service has since stopped offering.
 *
 * Resolution priority, highest first:
 *   1. an explicit `?weeks=` in the URL — the link the reader followed said so
 *   2. the `weeks` cookie — the last span they chose in the selector
 *   3. the service's own default
 *
 * The cookie exists so that arriving at a page through a nav link, which carries no `?weeks=`, shows
 * the span the reader was last reading at rather than resetting to four weeks.
 */

export const WEEKS_COOKIE = 'weeks';

export const FALLBACK_WEEKS = 4;

export const FALLBACK_OPTIONS: readonly number[] = [1, 4, 8, 12, 26];

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
  return `${path}?${new URLSearchParams({ weeks: String(weeks) }).toString()}`;
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
