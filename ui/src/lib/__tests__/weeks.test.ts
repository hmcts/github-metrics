import { describe, expect, it } from 'vitest';
import {
  LANDING_PATH,
  WEEKS_COOKIE,
  landingTarget,
  parseWeeks,
  rememberableWeeks,
  resolveWeeks,
  weeksCookie,
  withWeeks,
} from '@/lib/weeks';

/** The spans a service serving the default configuration offers, as `GET /windows` reports them. */
const OPTIONS: readonly number[] = [1, 4, 8, 12, 26];
const FALLBACK_WEEKS = 4;

describe('parseWeeks', () => {
  it('accepts a span the service offers', () => {
    expect(parseWeeks('12', OPTIONS)).toBe(12);
  });

  it('rejects a span off the list rather than clamping it to the nearest', () => {
    expect(parseWeeks('30', OPTIONS)).toBeNull();
    expect(parseWeeks('3', OPTIONS)).toBeNull();
  });

  it('rejects anything that is not a whole number of weeks', () => {
    expect(parseWeeks('soon', OPTIONS)).toBeNull();
    expect(parseWeeks('4.5', OPTIONS)).toBeNull();
    expect(parseWeeks('-4', OPTIONS)).toBeNull();
  });

  it('reads an absent or empty value as no choice', () => {
    expect(parseWeeks(null, OPTIONS)).toBeNull();
    expect(parseWeeks(undefined, OPTIONS)).toBeNull();
    expect(parseWeeks('  ', OPTIONS)).toBeNull();
  });

  it('honours a shortened option list, as a low maximum lookback produces', () => {
    expect(parseWeeks('26', [1, 4])).toBeNull();
    expect(parseWeeks('4', [1, 4])).toBe(4);
  });

  it('takes the first of a repeated parameter rather than failing on the array Next hands over', () => {
    // `?weeks=1&weeks=4` arrives as an array. Reading it as a string would call `trim` on one and
    // throw inside a server component, answering a stray parameter with a 500.
    expect(parseWeeks(['1', '4'], OPTIONS)).toBe(1);
    expect(parseWeeks(['30', '4'], OPTIONS)).toBeNull();
    expect(parseWeeks([], OPTIONS)).toBeNull();
  });
});

describe('resolveWeeks', () => {
  it('prefers the URL parameter, which is what the followed link said', () => {
    expect(resolveWeeks('8', '26', OPTIONS, FALLBACK_WEEKS)).toBe(8);
  });

  it('falls back to the cookie when the URL carries no span', () => {
    expect(resolveWeeks(null, '26', OPTIONS, FALLBACK_WEEKS)).toBe(26);
  });

  it('falls back to the cookie when the URL span is not on offer', () => {
    expect(resolveWeeks('30', '26', OPTIONS, FALLBACK_WEEKS)).toBe(26);
  });

  it("falls back to the service default when neither names an offered span", () => {
    expect(resolveWeeks(null, null, OPTIONS, FALLBACK_WEEKS)).toBe(4);
    expect(resolveWeeks('30', 'nonsense', OPTIONS, FALLBACK_WEEKS)).toBe(4);
  });

  it('takes the default from the caller, not from this module', () => {
    expect(resolveWeeks(null, null, [1, 4], 1)).toBe(1);
  });
});

describe('withWeeks', () => {
  it('carries the span onto a drill-through link', () => {
    expect(withWeeks('/repositories/cath-service', 8)).toBe('/repositories/cath-service?weeks=8');
  });
});

describe('landingTarget', () => {
  it('sends a bare request for the old overview to the repositories list', () => {
    expect(landingTarget(undefined)).toBe(LANDING_PATH);
    expect(landingTarget(null)).toBe(LANDING_PATH);
    expect(landingTarget('  ')).toBe(LANDING_PATH);
  });

  it('carries a span the link named, so the redirect does not change the window', () => {
    expect(landingTarget('26')).toBe('/repositories?weeks=26');
    expect(landingTarget(['26', '4'])).toBe('/repositories?weeks=26');
  });

  // The spans on offer are not known here, and the landing page resolves what arrives exactly as
  // the overview did: an unoffered span falls back to the cookie and then to the service's default.
  it('carries a span off the list rather than dropping it, and encodes whatever was typed', () => {
    expect(landingTarget('30')).toBe('/repositories?weeks=30');
    expect(landingTarget('4&x=/y')).toBe('/repositories?weeks=4%26x%3D%2Fy');
  });
});

describe('rememberableWeeks', () => {
  it('remembers the span a link named', () => {
    expect(rememberableWeeks('26')).toBe(26);
  });

  it('remembers a span off this service’s list, which resolveWeeks then drops', () => {
    // The proxy cannot ask `/windows` per request, so the list is not checked here. A cookie
    // holding 30 is read as no choice by `parseWeeks` and the page falls back, as it does today.
    expect(rememberableWeeks('30')).toBe(30);
    expect(parseWeeks(String(rememberableWeeks('30')), OPTIONS)).toBeNull();
  });

  it('takes the first of a repeated parameter, as the pages do', () => {
    expect(rememberableWeeks(['26', '4'])).toBe(26);
  });

  it('remembers nothing where no span was named', () => {
    expect(rememberableWeeks(undefined)).toBeNull();
    expect(rememberableWeeks(null)).toBeNull();
    expect(rememberableWeeks('')).toBeNull();
    expect(rememberableWeeks('  ')).toBeNull();
  });

  it('keeps what could not be a span out of a cookie that lives a year', () => {
    expect(rememberableWeeks('soon')).toBeNull();
    expect(rememberableWeeks('4.5')).toBeNull();
    expect(rememberableWeeks('-4')).toBeNull();
    expect(rememberableWeeks('0')).toBeNull();
    expect(rememberableWeeks('4; path=/evil')).toBeNull();
  });

  it('re-spells the span from the number, so the cookie is what parseWeeks matches', () => {
    expect(rememberableWeeks(' 04 ')).toBe(4);
  });
});

describe('weeksCookie', () => {
  it('writes a year-long, path-wide, same-site cookie', () => {
    const written = weeksCookie(12);
    expect(written).toContain(`${WEEKS_COOKIE}=12`);
    expect(written).toContain('path=/');
    expect(written).toContain('max-age=31536000');
    expect(written).toContain('SameSite=Lax');
  });
});
