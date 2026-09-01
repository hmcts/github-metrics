import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

// The `@/*` alias is declared in tsconfig.json for the type checker and the Next.js bundler; vitest
// reads neither, so it is repeated here rather than pulled in with another plugin dependency.
export default defineConfig({
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  // tsconfig.json leaves JSX for the Next.js compiler to transform (`preserve`), which esbuild would
  // honour and hand vitest JSX it cannot run; the components are rendered here through
  // `react-dom/server`, so the automatic runtime is stated for the test transform alone.
  esbuild: {
    jsx: 'automatic',
  },
  test: {
    environment: 'node',
    // `.tsx` as well as `.ts`: components are rendered here through `react-dom/server`, and a test
    // file that reached for JSX under a `.ts`-only pattern would be collected by nothing and report
    // as neither run nor failed.
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
  },
});
