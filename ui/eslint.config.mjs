import next from '@next/eslint-plugin-next';
import reactHooks from 'eslint-plugin-react-hooks';
import tseslint from 'typescript-eslint';

/**
 * The three plugins assembled by hand rather than through `eslint-config-next`.
 *
 * `eslint-config-next` bundles five plugins, and three of them — `eslint-plugin-react`,
 * `eslint-plugin-jsx-a11y` and `eslint-plugin-import` — cap their `eslint` peer at `^9`. ESLint 9 is
 * itself out of support and warns on install, so keeping that config means choosing between one
 * deprecation warning and three ERESOLVE peer warnings. Naming the plugins that do support ESLint 10
 * gives a clean install instead, and halves the dependency tree on the way past: 148 packages rather
 * than 305, without `unrs-resolver` and the postinstall script it needs.
 *
 * WHAT THIS GIVES UP: the accessibility rules from `eslint-plugin-jsx-a11y`, which has no release
 * that supports ESLint 10 at all. The ARIA the pages rely on is asserted directly in
 * `src/components/__tests__` instead — the sortable headers, the week selector's `aria-busy` — so it
 * is checked, just not by a linter. Add the plugin back when it supports ESLint 10.
 */
const config = [
  // ESLint 9 dropped .eslintignore, so build output and dependencies are ignored here instead.
  { ignores: ['.next/**', 'node_modules/**', 'next-env.d.ts', 'coverage/**'] },
  ...tseslint.configs.recommended,
  next.configs['core-web-vitals'],
  // `configs.flat` is the plugin's namespace for the flat-config versions; `configs` at the top
  // level still holds the eslintrc shapes, which ESLint 10 rejects outright.
  reactHooks.configs.flat['recommended-latest'],
];

export default config;
