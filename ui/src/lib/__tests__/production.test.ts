/**
 * What `lib/production.ts` owes the rest of the app.
 *
 * The interesting assertion is the last group: every colour this module spends is a NAMED Tailwind
 * colour declared in `tailwind.config.ts`, not a hex written into a class string. Nothing else
 * catches that — an arbitrary-value class like `bg-[#4169e1]` type-checks, lints, renders and would
 * pass an assertion on the class name, while quietly putting the site's palette in two places.
 */

import { describe, expect, it } from 'vitest';
import {
  PRODUCTION_BADGE,
  PRODUCTION_DOT,
  PRODUCTION_HEX,
  PRODUCTION_LABEL,
  PRODUCTION_TOGGLE_ACTIVE,
  PRODUCTION_TOGGLE_INACTIVE,
} from '@/lib/production';
import config from '../../../tailwind.config';

/** Every class string the module publishes, which is every one the app is allowed to use. */
const CLASSES = [
  PRODUCTION_BADGE,
  PRODUCTION_TOGGLE_ACTIVE,
  PRODUCTION_TOGGLE_INACTIVE,
  PRODUCTION_DOT,
];

/** The palette as `tailwind.config.ts` declares it, cast rather than narrowed: the shape is ours. */
const COLORS = config.theme!.extend!.colors as {
  rag: Record<string, string>;
  royal: Record<string, string>;
};

describe('production vocabulary', () => {
  it('names the attribute in a word', () => {
    expect(PRODUCTION_LABEL).toBe('Production');
  });

  it('names no emoji anywhere', () => {
    expect(JSON.stringify([PRODUCTION_LABEL, ...CLASSES, PRODUCTION_HEX])).not.toMatch(
      /\p{Extended_Pictographic}/u,
    );
  });
});

describe('the badge classes', () => {
  it('keeps a border, so the badge survives a monochrome print', () => {
    expect(PRODUCTION_BADGE.split(' ')).toContain('border');
    expect(PRODUCTION_BADGE).toContain('border-royal-border');
  });

  it('carries no dismiss affordance of its own: it is not a chip', () => {
    expect(PRODUCTION_BADGE).not.toContain('cursor');
  });
});

describe('the toggle classes', () => {
  it('is royal when active and greyed when inactive', () => {
    expect(PRODUCTION_TOGGLE_ACTIVE).toContain('royal');
    expect(PRODUCTION_TOGGLE_INACTIVE).not.toContain('royal');
    expect(PRODUCTION_TOGGLE_INACTIVE).toContain('slate');
  });

  // The shade is named `border`, so this asserts on the class TOKENS rather than the string: it is
  // `ring-royal-border` that is wanted and a bare `border` utility that is not.
  it('shifts nothing as it turns on: the active state adds a ring, not a border', () => {
    const tokens = PRODUCTION_TOGGLE_ACTIVE.split(' ');
    expect(tokens).toContain('ring-1');
    expect(tokens).not.toContain('border');
  });

  it('keeps the dot royal in both states, so the control names its colour while off', () => {
    expect(PRODUCTION_DOT).toBe('bg-royal');
  });
});

describe('the configured colour', () => {
  it('resolves the hex from the palette rather than restating it', () => {
    expect(PRODUCTION_HEX).toBe(COLORS.royal.DEFAULT);
  });

  it('gives the badge a surface, a border and a legible word', () => {
    expect(Object.keys(COLORS.royal).sort()).toEqual(['DEFAULT', 'border', 'surface', 'text']);
  });

  it('keeps the colour out of the rag group, which holds the report’s verdicts', () => {
    expect(Object.keys(COLORS.rag)).not.toContain('royal');
    expect(JSON.stringify(COLORS.rag)).not.toContain(PRODUCTION_HEX);
  });

  it('writes every colour as a named utility, with no hex literal in any class string', () => {
    for (const classes of CLASSES) {
      expect(classes).not.toMatch(/#|\[/);
    }
  });

  it('names only shades the palette declares', () => {
    const declared = Object.keys(COLORS.royal).map((shade) =>
      shade === 'DEFAULT' ? '' : `-${shade}`,
    );
    const used = CLASSES.flatMap((classes) => classes.split(' '))
      .map((name) => /royal(-[a-z]+)?$/.exec(name)?.[0])
      .filter((name): name is string => name !== undefined);
    expect(used.length).toBeGreaterThan(0);
    for (const name of used) {
      expect(declared).toContain(name.replace('royal', ''));
    }
  });
});
