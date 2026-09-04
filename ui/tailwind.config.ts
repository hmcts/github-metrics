import type { Config } from 'tailwindcss';

// The RAG hexes are the palette the report's readiness labels are read in; naming them here keeps
// the label-to-colour decision in one place instead of scattered hex literals in components.
const config: Config = {
  // `src/lib` is scanned as well as the components: the RAG class strings live only in `lib/rag.ts`,
  // and leaving that directory out drops every `rag-*`, `bg-green-950` and `border-l-4` utility from
  // the built CSS while `next build` still succeeds — the colour bar silently renders colourless.
  content: ['./src/components/**/*.{ts,tsx}', './src/app/**/*.{ts,tsx}', './src/lib/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        rag: {
          red: '#f87171',
          amber: '#fbbf24',
          green: '#4ade80',
          none: '#64748b',
          // A CHART MARK ONLY, and named here so the five colours the site draws with are one list.
          // No class reads it: the one band it fills — a gate requiring two or more approvals — is
          // better than the green beside it rather than a fifth verdict, and nothing outside a donut
          // legend makes that distinction.
          'green-strong': '#16a34a',
        },
        accent: '#818cf8',
        // The production badge and its toggle, resolved by `lib/production.ts`. DELIBERATELY NOT IN
        // THE `rag` GROUP: whether a repository deploys to production is an attribute of the
        // repository, not one of the report's verdicts about it, and sitting under `rag` would offer
        // it to any component reaching in there for a grade. The three shades are what a badge in
        // this palette needs — a dark fill, a border that survives a monochrome print, and a word
        // legible on the fill — with `DEFAULT` the royal blue itself, for the dot and for charts.
        royal: {
          DEFAULT: '#4169e1',
          surface: '#131d3f',
          border: '#2d3f87',
          text: '#a5b8f4',
        },
      },
    },
  },
  plugins: [],
};

export default config;
