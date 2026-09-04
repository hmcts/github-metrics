/**
 * Presentation for the one attribute a repository carries that the report does not grade: whether it
 * is approved to deploy to production.
 *
 * NOT `tone.ts`, and the difference is worth stating because both files hold colour decisions.
 * `tone.ts` decides A FIGURE'S colour — it is a threshold table over merged counts, coverage
 * percentages and alert totals, and every one of its four tones is a judgement about how a number
 * reads. Production is not a number and carries no judgement: a service that deploys to production
 * is not thereby better or worse than one that does not, it is a different KIND of thing, and
 * putting it through a good/warn/bad/neutral vocabulary would grade an estate's shape as if it were
 * its health. It is not `rag.ts` either — see `tailwind.config.ts` for why the colour is deliberately
 * outside the `rag` group.
 *
 * NO EMOJI, as in `rag.ts`: the word is the information and the colour only supports it. THE BADGE
 * KEEPS A BORDER for `RAGLabel`'s reason — a colour alone does not survive a monochrome print of the
 * page, and a fill without an edge is the first thing a greyscale printer flattens into the surface
 * behind it.
 *
 * Every class string the app spends this colour through is HERE AND NOWHERE ELSE, so the decision
 * can be read in one place and `tailwind.config.ts`'s `content` list has one file to scan for it. No
 * component holds a `royal-*` utility or a hex literal of its own.
 */

/** The word. A repository is either a production service or it is not badged at all. */
export const PRODUCTION_LABEL = 'Production';

/** The badge: `RAG_BADGE`'s shape in the royal palette, border included and for its reason. */
export const PRODUCTION_BADGE = 'bg-royal-surface text-royal-text border border-royal-border';

/**
 * The filter toggle when it is on: the royal surface and its border, so the bar reads as pressed.
 *
 * `ring-1` rather than `border`, unlike the badge: the toggle sits in a row of chips sized by their
 * padding, and a border would move the words a pixel as it turned on.
 */
export const PRODUCTION_TOGGLE_ACTIVE = 'bg-royal-surface text-royal-text ring-1 ring-royal-border';

/** The toggle when it is off: the greyed slate the readiness filter used, hover included. */
export const PRODUCTION_TOGGLE_INACTIVE =
  'bg-slate-800 text-slate-400 hover:bg-slate-700 hover:text-slate-200';

/** The toggle's dot, royal blue in BOTH states so the control's colour is legible while it is off. */
export const PRODUCTION_DOT = 'bg-royal';

/** The hex, for anywhere a class cannot reach — a chart mark takes a colour as a value. */
export const PRODUCTION_HEX = '#4169e1';
