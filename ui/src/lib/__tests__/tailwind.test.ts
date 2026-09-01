/**
 * The one thing no other gate in this project can catch.
 *
 * Tailwind emits only the utilities it finds by scanning the files named in `content`. A class string
 * held in a file outside those globs is never emitted, and NOTHING fails: `next build` succeeds, the
 * type checker is happy, and a unit test asserting the class name passes — the page simply renders
 * without the colour. `lib/rag.ts` holds every RAG class in the app, so a `content` list covering
 * only `src/components` and `src/app` silently drops the readiness colour bar.
 *
 * The guard is on the directory rather than on the built CSS, so it runs in milliseconds and names
 * the actual fault: a new directory under `src` that holds class strings and is not scanned.
 */

import { readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import config from '../../../tailwind.config';

const SOURCE = fileURLToPath(new URL('../../', import.meta.url));

/** The `src` subdirectory each `./src/<name>/**` glob scans. */
function scanned(): string[] {
  const globs = Array.isArray(config.content) ? config.content : [];
  return globs
    .filter((glob): glob is string => typeof glob === 'string')
    .map((glob) => /^\.\/src\/([^/*]+)\//.exec(glob)?.[1])
    .filter((name): name is string => name !== undefined);
}

describe('tailwind content', () => {
  it('scans every source directory, so no class string goes unemitted', () => {
    const directories = readdirSync(SOURCE, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .map((entry) => entry.name);
    expect(directories.length).toBeGreaterThan(0);
    expect([...scanned()].sort()).toEqual([...directories].sort());
  });

  it('scans the directory the RAG classes are declared in', () => {
    // Named on its own because it is the one that was actually missed: every `rag-*`, `bg-green-950`
    // and `border-l-4` in the app is written in `lib/rag.ts` and nowhere else.
    expect(scanned()).toContain('lib');
  });
});
