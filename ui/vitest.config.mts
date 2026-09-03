import { fileURLToPath } from 'node:url';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

// `.mts`, not `.ts`: ui/package.json has no `"type": "module"` (Next.js and the PostCSS/Tailwind
// configs read as CommonJS), so Vite's native config loader treats a `.ts` config as CJS and warns
// on the ESM syntax below. The explicit module extension states it instead of relying on the
// deprecated bundled loader.
//
// The `@/*` alias is declared in tsconfig.json for the type checker and the Next.js bundler; vitest
// reads neither, so it is repeated here rather than pulled in with another plugin dependency.
export default defineConfig({
  // The React plugin is what makes a client component's hooks run: it gives the transform React's
  // own JSX pipeline rather than the bare automatic runtime below, which is what the DOM tests
  // render through `@testing-library/react`. Fast Refresh, its other half, is inert here — vitest
  // never serves a module twice.
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  // Stated here because vitest reads no tsconfig at all: the `jsx` setting next door is for `tsc`
  // and the Next.js bundler, and the transform below would otherwise default to a runtime the
  // components are not written for. Next 16 mandates `react-jsx` there and rewrites the file during
  // `next build` if it says anything else, so the two are deliberately not read off each other.
  // vitest 4 runs on Vite 8, where oxc has replaced esbuild as the transformer and the `esbuild`
  // config key is deprecated — hence `oxc`, and the nested `runtime` rather than a bare string.
  oxc: {
    jsx: {
      runtime: 'automatic',
    },
  },
  test: {
    // `node`, not `jsdom`, as the default: every existing test renders through `react-dom/server`,
    // where there is no document to want, and standing a jsdom up per file would cost that for
    // nothing. The DOM tests opt in one file at a time with a `@vitest-environment jsdom` docblock
    // at the top, which vitest reads per test file — so a file that needs a browser says so, and
    // the ones that do not are unaffected.
    environment: 'node',
    // `.tsx` as well as `.ts`: components are rendered here through `react-dom/server`, and a test
    // file that reached for JSX under a `.ts`-only pattern would be collected by nothing and report
    // as neither run nor failed.
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
    coverage: {
      provider: 'v8',
      // Naming `include` is what makes untested files appear in the table at 0% rather than being
      // omitted from it — vitest 4 removed the `all` flag that used to say this, and now reports
      // every file matching `include` whether a test loaded it or not.
      include: ['src/**'],
      // Type-only module: `tsc` erases it entirely, so there is no runtime code to instrument and
      // v8 reports it as an unreachable 0% that no test could ever raise.
      exclude: ['src/lib/types.ts'],
      // `text` alone, which writes to stdout and creates no files. The `html` reporter is the one
      // worth having when a figure drops, but it writes a directory per source tree, and on this
      // repo's virtiofs mount that intermittently dies with `ENOENT: mkdir coverage/src` — the same
      // dentry-cache incoherency that breaks `next build`'s standalone copy. Vitest does not fail a
      // run over a crashed reporter, so the gate went green while printing a stack trace. It lives
      // in `npm run coverage:html` instead, where a failure is the developer's to see and not a
      // false alarm in the middle of `check`.
      //
      // The table this prints has no rows while everything is at 100%: istanbul lists the files that
      // miss something, so an empty body under a full summary is the report saying nothing is
      // missing. Rows appear as soon as a figure drops.
      reporter: ['text'],
      reportsDirectory: './coverage',
      // Fully covered, so the gate says so: every statement, branch, function and line under
      // `src/**` is reached by the suite, and anything less is a regression rather than a figure to
      // be talked down. These began as the measured baseline (85/89/87) and were ratcheted here
      // once the route files and the client components were tested — upwards only, never lowered.
      //
      // There are no per-file exemptions, because nothing needed one. The last shortfall was
      // `TrendChart`'s axis formatter, which recharts only calls from a measured chart and so was
      // unreachable through `react-dom/server`; it is covered from jsdom in
      // `src/components/charts/__tests__/TrendChart.test.tsx` rather than excused with a lower
      // number here. A genuinely unreachable line belongs in `exclude` with a reason, as
      // `src/lib/types.ts` is above — not in a threshold nobody can read the intent of.
      thresholds: {
        statements: 100,
        lines: 100,
        branches: 100,
        functions: 100,
      },
    },
  },
});
