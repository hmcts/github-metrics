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
        },
        accent: '#818cf8',
      },
    },
  },
  plugins: [],
};

export default config;
