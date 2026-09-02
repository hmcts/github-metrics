import { describe, expect, it } from 'vitest';
import { WEEKS_COOKIE, parseWeeks, resolveWeeks, weeksCookie, withWeeks } from '@/lib/weeks';

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

describe('weeksCookie', () => {
  it('writes a year-long, path-wide, same-site cookie', () => {
    const written = weeksCookie(12);
    expect(written).toContain(`${WEEKS_COOKIE}=12`);
    expect(written).toContain('path=/');
    expect(written).toContain('max-age=31536000');
    expect(written).toContain('SameSite=Lax');
  });
});
