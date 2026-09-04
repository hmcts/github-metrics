/**
 * Whether a figure on a page reads well or badly, and the classes that say so.
 *
 * REVERSAL, 2026-09-02, on the user's instruction. `MetricCard` carried a written rule that a card
 * grades nothing and that the readiness label is the only thing on the site with a colour. The
 * predecessor tool coloured its figures, the user asked for that back, and this module is where the
 * decision now lives — so a figure's colour is one threshold table a reader can check rather than a
 * hex literal in whichever component happened to need it. `rag.ts` keeps its own job: the four
 * readiness LABELS, which are the report's judgement. These four tones are presentation over figures
 * the report states without grading, and the report neither states them nor depends on them.
 *
 * `neutral` is the DEFAULT AND THE MAJORITY. A figure with no threshold worth stating — a branch
 * name, a line count, how many merges were reported — renders exactly as it did before this module
 * existed. Colour that is everywhere grades nothing, and the two rules worth stating aloud are both
 * about refusing to colour:
 *
 *   • An unreadable figure stays neutral. A security family GitHub refused, a Sonar measure the
 *     project never reported and a gate field GitHub withheld are absences, and colouring one green
 *     would report a missing permission as a passing check.
 *   • The three neutral merge-gate rules stay neutral. `ReadinessPolicy.neutral()` reports deletion
 *     protection, linear history and branch naming as bearing on the label in neither state, so
 *     grading them here would grade something the policy deliberately does not.
 *
 * Every map is total over the four tones, as in `rag.ts`, so a lookup can never miss and no
 * component needs a fallback of its own. Each threshold function names its `assessment.py`
 * counterpart where one exists, so the page and the policy can be checked against each other by
 * reading them side by side.
 */

import { RAG_HEX, type RAGState, state } from '@/lib/rag';
import type {
  AlertFamily,
  OpenAlertCount,
  ReadinessCondition,
  ReadinessLabel,
  SecurityAlertEvidence,
  SonarGateLevel,
  SonarMeasures,
  SonarRating,
  UnreviewedSubstantialOutcome,
} from '@/lib/types';

/** The four presentation states: reads well, worth weighing, reads badly, and carries no verdict. */
export type Tone = 'good' | 'warn' | 'bad' | 'neutral';

export const TONES: readonly Tone[] = ['good', 'warn', 'bad', 'neutral'];

/**
 * The colour a toned VALUE is set in — never its label and never its detail.
 *
 * `neutral` is the slate every figure on the site was already set in, so an untoned card is
 * byte-for-byte what it was before. The three coloured tones resolve through the `rag-*` palette
 * named in `tailwind.config.ts` rather than through Tailwind's own red, amber and green, so a figure
 * reading badly and a repository labelled red are the same colour on the same page.
 */
export const TONE_VALUE: Record<Tone, string> = {
  good: 'text-rag-green',
  warn: 'text-rag-amber',
  bad: 'text-rag-red',
  neutral: 'text-slate-100',
};

/**
 * The left colour bar, for a row rather than a card — the same bar `rag.ts` draws a label with.
 *
 * `neutral` keeps the bar at the panel's own border colour rather than dropping it: a list whose
 * untoned rows lost their left border would jog in and out as the eye went down it, and slate reads
 * as the absence of a verdict, which is what it is.
 */
export const TONE_BORDER: Record<Tone, string> = {
  good: 'border-l-4 border-l-rag-green',
  warn: 'border-l-4 border-l-rag-amber',
  bad: 'border-l-4 border-l-rag-red',
  neutral: 'border-l-4 border-l-slate-800',
};

/**
 * The same four tones as colour VALUES, for the marks a chart is drawn with.
 *
 * Derived from `RAG_HEX` rather than restating four hexes: recharts takes fills as strings, so a
 * donut cannot reach a Tailwind class, and a second copy of the palette is how a wedge and the row it
 * counts drift into two greens nobody chose. `neutral` reads `RAG_HEX.none`, which is the slate an
 * ungraded repository is already drawn in on the readiness donut beside these.
 */
export const TONE_HEX: Record<Tone, string> = {
  good: RAG_HEX.green,
  warn: RAG_HEX.amber,
  bad: RAG_HEX.red,
  neutral: RAG_HEX.none,
};

/**
 * The one mark that is not a tone: `rag-green-strong`, for a band that is better than good.
 *
 * A gate requiring two approvals is not a different verdict from one requiring a single approval —
 * both clear `assessment.review_requirement` — so it is not a fifth tone, and nothing outside a donut
 * legend distinguishes them. It is a deeper shade of the same green, and it exists only where a
 * reader is looking at the whole estate at once and the distinction is the point of the picture.
 */
export const STRONG_GOOD_HEX = '#16a34a';

export function valueClass(tone: Tone | undefined): string {
  return TONE_VALUE[tone ?? 'neutral'];
}

export function borderClass(tone: Tone | undefined): string {
  return TONE_BORDER[tone ?? 'neutral'];
}

/** The three sections `ReadinessAssessment` reports every checked condition in. */
export type ConditionOutcome = 'blocking' | 'caution' | 'clear';

/**
 * The tone one assessment row reads in, or `undefined` where the policy's own label answers.
 *
 * The only place in this module where the judgement is NOT this module's: the policy graded these
 * conditions itself, so all this does is carry its verdict onto the row.
 *
 *   • A blocking row keeps its readiness label — the ceiling that condition imposed — so nothing is
 *     returned for it and `RAGRow` draws the label's own bar. A tone of the same colour would be a
 *     second opinion about a grade that has already been given.
 *   • A caution is amber and a clear condition is green, which is what the two sections mean.
 *   • An informational condition carries NO COLOUR: `ReadinessPolicy.neutral()` and the sufficient
 *     cohort are reported without being judged, and green would report them as checks that passed.
 *
 * The three overloads say which of those two answers a caller can get, because `blocking` is the only
 * outcome with no tone: a caller that has already dealt with the blocking case — `metricTone`, which
 * reads the label itself — then needs no fallback for an answer it cannot receive, and a fallback
 * that cannot be reached is a branch no test can cover.
 */
export function conditionTone(outcome: 'blocking', condition: ReadinessCondition): undefined;
export function conditionTone(outcome: 'caution' | 'clear', condition: ReadinessCondition): Tone;
export function conditionTone(
  outcome: ConditionOutcome,
  condition: ReadinessCondition,
): Tone | undefined;
export function conditionTone(outcome: ConditionOutcome, condition: ReadinessCondition): Tone | undefined {
  if (outcome === 'blocking') {
    return undefined;
  }
  // Absent from a service older than the flag, which reads as a graded condition: the three that
  // carry it say so in their detail too, so an old service loses a colour, not a fact.
  if (condition.informational ?? false) {
    return 'neutral';
  }
  return outcome === 'caution' ? 'warn' : 'good';
}

/**
 * The tone one readiness LABEL reads in, for a figure that has to carry the grade itself.
 *
 * `conditionTone` hands a blocking row back to `rag.ts`, because a row has room for the label's own
 * bar and its word. A metric card has neither, so a card whose condition blocked has to say so in
 * the only place it can — the figure — and this is the one map that translates the report's
 * judgement into this module's vocabulary rather than deciding anything of its own.
 *
 * `cannot_assess` is neutral for the reason `rag.ts` sets it slate: half the question could not be
 * read, and a warm figure would report a missing permission as a bad result.
 */
export const LABEL_TONE: Record<RAGState, Tone> = {
  green: 'good',
  amber: 'warn',
  red: 'bad',
  cannot_assess: 'neutral',
  none: 'neutral',
};

export function labelTone(label: ReadinessLabel | null | undefined): Tone {
  return LABEL_TONE[state(label)];
}

/** Resolve a tri-state flag, where a value nobody disclosed carries no verdict either way. */
function disclosed(value: boolean | null | undefined, present: Tone, absent: Tone): Tone {
  if (value == null) {
    return 'neutral';
  }
  return value ? present : absent;
}

/** Resolve a count, where an uncounted figure carries no verdict — a dash is not a zero. */
function counted(value: number | null | undefined, none: Tone, some: Tone): Tone {
  if (value == null) {
    return 'neutral';
  }
  return value === 0 ? none : some;
}

/**
 * Direct commits: a push straight onto the default branch, which no pull request reviewed.
 *
 * Amber and never red on its own. `independent-review-coverage` is the condition that grades a
 * repository for bypassing review, and one direct commit beside 300 merges is a fact to weigh
 * rather than a finding — so the card cautions and the assessment above it decides.
 */
export function directCommitTone(commits: number | null | undefined): Tone {
  return counted(commits, 'good', 'warn');
}

/**
 * A merge that reached the default branch with no independent human review behind it.
 *
 * `assessment.independent-review-coverage` grades the repository's whole rate against a configured
 * target, and this is a count beside one login rather than that rate: how many of one person's pull
 * requests merged unreviewed. Amber and never red for the reason `directCommitTone` is — one
 * unreviewed merge is a fact to weigh, the condition above the table is what grades the repository,
 * and nothing in this column grades the person.
 */
export function unreviewedMergeTone(merges: number | null | undefined): Tone {
  return counted(merges, 'good', 'warn');
}

/**
 * CODEOWNERS: how many files were found, or `undefined` where nobody could look.
 *
 * Three answers, and this keeps them apart the way `repository.codeownersCard` does: a file that was
 * found reads well, no file at any checked location is worth weighing, and a repository whose
 * contents GitHub refused is not graded at all.
 */
export function codeownersTone(files: number | null | undefined): Tone {
  return counted(files, 'warn', 'good');
}

/** The twelve merge-gate fields, keyed on the field rather than on the words it renders as. */
export type GateField =
  | 'branch'
  | 'protected'
  | 'rules_observed'
  | 'required_approving_review_count'
  | 'required_status_checks'
  | 'dismiss_stale_reviews_on_push'
  | 'applies_to_administrators'
  | 'restricts_deletions'
  | 'blocks_force_pushes'
  | 'requires_linear_history'
  | 'restricts_branch_names'
  | 'unmodelled_rules';

/** What a gate field arrives as: a flag, a count, or nothing where GitHub withheld it. */
export type GateValue = boolean | number | null | undefined;

/** Read a flag off a gate value; anything else is a field that was not disclosed as a flag. */
function flag(value: GateValue): boolean | undefined {
  return typeof value === 'boolean' ? value : undefined;
}

/** Read a count off a gate value; anything else is a field that was not disclosed as a count. */
function tally(value: GateValue): number | undefined {
  return typeof value === 'number' ? value : undefined;
}

const GATE_TONE: Record<GateField, (value: GateValue) => Tone> = {
  // The branch a gate is read on and the rules this build did not interpret are facts about what was
  // looked at, not answers about it. Neither has a better or a worse value.
  branch: () => 'neutral',
  unmodelled_rules: () => 'neutral',
  // `assessment.governance`: an unprotected default branch is the first veto, and red there.
  protected: (value) => disclosed(flag(value), 'good', 'bad'),
  // `assessment.governance`: rules a protected branch did not disclose are cannot_assess, which is
  // slate in `rag.ts` for the reason it is neutral here — the answer could not be read, and warm
  // colour would blame a missing permission on the team that owns the repository.
  rules_observed: (value) => (flag(value) === true ? 'good' : 'neutral'),
  // `assessment.review_requirement`: no required approval is the second veto, and red there.
  required_approving_review_count: (value) => counted(tally(value), 'bad', 'good'),
  // `assessment.status_checks`: a caution in the policy, because `checks-passing-at-merge` measures
  // what CI actually held at the merge point. Red here for the same reason the rule reads plainly:
  // a gate requiring no check cannot block anything, and the card states the configuration.
  required_status_checks: (value) => counted(tally(value), 'bad', 'good'),
  // `assessment.stale_reviews`: a caution — reviewed and merged code can differ.
  dismiss_stale_reviews_on_push: (value) => disclosed(flag(value), 'good', 'warn'),
  // `assessment.administrators`: a caution, and undisclosed is a caution in the policy too. Neutral
  // here: the card can only say what was disclosed, and the condition above it does the weighing.
  applies_to_administrators: (value) => disclosed(flag(value), 'good', 'warn'),
  // The neutral trio, from `ReadinessPolicy.neutral()`: reported in both states, bearing on the
  // label in neither. Colouring them would grade what the policy deliberately does not.
  restricts_deletions: () => 'neutral',
  requires_linear_history: () => 'neutral',
  restricts_branch_names: () => 'neutral',
  // `assessment.force_pushes` is a caution, so this field has a counterpart that the tone table in
  // the plan does not name — and an unnamed figure is neutral by that table's own default rule.
  blocks_force_pushes: () => 'neutral',
};

/**
 * The tone one merge-gate field reads in, from the field and the value the report sent for it.
 *
 * `value` is optional because two of the twelve fields have none a tone could be read off — the
 * branch a gate was read on, and the rules this build did not interpret — and passing them an
 * explicit `undefined` read as a value that had gone missing rather than one that never existed.
 */
export function gateFieldTone(field: GateField, value?: GateValue): Tone {
  return GATE_TONE[field](value);
}

/** The four open pull-request counts, keyed as the service's own summary fields. */
export type OpenPullRequestField =
  | 'opened_in_window'
  | 'closed_without_merge'
  | 'currently_open'
  | 'stale_open';

const OPEN_PULL_REQUEST_TONE: Record<OpenPullRequestField, (value: number | null | undefined) => Tone> = {
  // Throughput, not a verdict: a repository that opened forty pull requests is busier than one that
  // opened four, and neither figure is better. Closing without merging is how a proposal is
  // declined, and how many are open now is a queue depth rather than a shortfall.
  opened_in_window: () => 'neutral',
  closed_without_merge: () => 'neutral',
  currently_open: () => 'neutral',
  // The one that ages: a pull request open past the staleness bound is work nobody is finishing.
  // No assessment condition grades it, which is why the threshold is stated here.
  stale_open: (value) => counted(value, 'good', 'warn'),
};

export function openPullRequestTone(field: OpenPullRequestField, value: number | null | undefined): Tone {
  return OPEN_PULL_REQUEST_TONE[field](value);
}

/** The severities that make an open alert family read badly rather than merely warrant weighing. */
const SEVERE = ['critical', 'high'] as const;

/**
 * One security alert family, from the count the report sent and its severity breakdown.
 *
 * Ungraded by the readiness policy — no assessment condition reads these — so the thresholds are
 * stated here and nowhere else. Severity is what separates the two warm tones: a family with a
 * critical or a high alert open reads badly, and one holding only medium and low alerts is worth
 * weighing. Secret scanning reports no severity at all, so any open secret reads badly: a leaked
 * credential has no low-severity form.
 *
 * A family GitHub refused is NOT graded. `count.open` is absent exactly there, and a family with
 * nothing open and a family nobody could read look identical as a zero and mean opposite things.
 */
export function alertTone(family: AlertFamily, count: OpenAlertCount): Tone {
  const open = count.open;
  if (open == null) {
    return 'neutral';
  }
  if (open === 0) {
    return 'good';
  }
  if (family === 'secret-scanning') {
    return 'bad';
  }
  return SEVERE.some((severity) => (count.by_severity[severity] ?? 0) > 0) ? 'bad' : 'warn';
}

/**
 * One maintenance window's answer: was there a commit inside it.
 *
 * `undefined` is the third value and stays uncoloured — the bounded search stopped at its own
 * limit, so "unknown" is not "no". Amber rather than red for a window with no commit in it: no
 * assessment condition grades maintenance at all, and an unmaintained repository is a fact about
 * the repository rather than something ungoverned reaching its default branch.
 */
export function maintenanceTone(committedWithin: boolean | null | undefined): Tone {
  return disclosed(committedWithin, 'good', 'warn');
}

/**
 * The SonarCloud quality gate, which the report carries and grades nothing on.
 *
 * `NONE` is Sonar's own word for a project with no gate conditions configured, so it is an absence
 * rather than a pass — neutral, like a project whose measures could not be read at all.
 */
export function sonarGateTone(level: SonarGateLevel | null | undefined): Tone {
  if (level == null || level === 'NONE') {
    return 'neutral';
  }
  return level === 'OK' ? 'good' : 'bad';
}

/**
 * One SonarCloud rating, from the `A`-to-`E` scale the report sends as 1 to 5.
 *
 * A value off the scale is neutral rather than either extreme, for the reason
 * `repository.ratingLetter` refuses to name a letter for one: reporting a rating this build does
 * not understand as the best or the worst there is would be making the judgement up.
 */
export function sonarRatingTone(rating: SonarRating | null | undefined): Tone {
  if (rating == null || !Number.isInteger(rating.value)) {
    return 'neutral';
  }
  if (rating.value === 1) {
    return 'good';
  }
  if (rating.value === 2 || rating.value === 3) {
    return 'warn';
  }
  return rating.value === 4 || rating.value === 5 ? 'bad' : 'neutral';
}

/** The seven numeric Sonar measures, keyed as the fields `SonarMeasures` carries them in. */
export type SonarMeasure =
  | 'coverage'
  | 'duplicated_lines_density'
  | 'lines_of_code'
  | 'violations'
  | 'reliability_issues'
  | 'maintainability_issues'
  | 'security_issues';

/**
 * Grade one issue COUNT by the rating that covers it, never by the count on its own.
 *
 * A raw count has no absolute threshold: 71 issues in 8,607 lines and 71 in a million lines are
 * different findings, and Sonar has already weighed the count against the size of the project to
 * arrive at a letter. So above zero the count is worth weighing, and it reads badly exactly where
 * its own rating does — which puts 1,358 maintainability issues at amber under an A rating and 71
 * reliability issues at red under a D.
 */
function issueTone(count: number | undefined, rating: SonarRating | undefined): Tone {
  if (count == null) {
    return 'neutral';
  }
  if (count === 0) {
    return 'good';
  }
  return sonarRatingTone(rating) === 'bad' ? 'bad' : 'warn';
}

/** Grade one measure against a boundary where lower is better, leaving an unreported one neutral. */
function ceiling(value: number | undefined, good: number, warn: number): Tone {
  if (value == null) {
    return 'neutral';
  }
  if (value <= good) {
    return 'good';
  }
  return value <= warn ? 'warn' : 'bad';
}

/** Grade one measure against a boundary where higher is better, leaving an unreported one neutral. */
function floor(value: number | undefined, good: number, warn: number): Tone {
  if (value == null) {
    return 'neutral';
  }
  if (value >= good) {
    return 'good';
  }
  return value >= warn ? 'warn' : 'bad';
}

/**
 * Test coverage, the one Sonar measure two pages grade: a repository's card and the estate donut.
 *
 * Factored out of `SONAR_MEASURE_TONE` so the boundary is stated once. Sonar's own default gate
 * boundary for new code is 80%, and 90% is the target a project aiming higher sets. Neither is a
 * readiness condition: the policy reads no Sonar measure at all.
 */
export function coverageTone(coverage: number | null | undefined): Tone {
  return floor(coverage ?? undefined, 90, 80);
}

const SONAR_MEASURE_TONE: Record<SonarMeasure, (measures: SonarMeasures) => Tone> = {
  coverage: (measures) => coverageTone(measures.coverage),
  // Sonar's default gate errors above 3% duplication; 5% is where it stops being a rounding matter.
  duplicated_lines_density: (measures) => ceiling(measures.duplicated_lines_density, 3, 5),
  // The size of the project, which is neither good nor bad and is the denominator for the rest.
  lines_of_code: () => 'neutral',
  // The only count with no rating of its own, so above zero it is worth weighing and never more.
  violations: (measures) => counted(measures.violations, 'good', 'warn'),
  reliability_issues: (measures) => issueTone(measures.reliability_issues, measures.reliability_rating),
  maintainability_issues: (measures) =>
    issueTone(measures.maintainability_issues, measures.maintainability_rating),
  security_issues: (measures) => issueTone(measures.security_issues, measures.security_rating),
};

export function sonarMeasureTone(measure: SonarMeasure, measures: SonarMeasures): Tone {
  return SONAR_MEASURE_TONE[measure](measures);
}

/**
 * One band of a donut: the key rows are counted under, the words it reads as, and the mark it draws.
 *
 * A band table is the whole definition of a donut — `lib/chart.ts` counts rows into one and derives
 * a slice per entry, so the order here IS the order of the legend, and a band left at zero is still
 * listed. Best first, so the picture reads top to bottom as the estate getting worse, and `unknown`
 * is last in every table: an unmeasured repository is not a good result or a bad one.
 */
export interface Band<Key extends string = string> {
  key: Key;
  /** The words the legend and the hover card read; the mark beside them is decoration. */
  name: string;
  /** A colour value, not a class: recharts takes fills as strings. */
  mark: string;
}

/** How many approving reviews the merge gate requires, banded. */
export type ReviewBand = 'multiple' | 'required' | 'none' | 'unknown';

export const REVIEW_BANDS: readonly Band<ReviewBand>[] = [
  { key: 'multiple', name: 'Multiple', mark: STRONG_GOOD_HEX },
  { key: 'required', name: 'Enforced', mark: TONE_HEX.good },
  { key: 'none', name: 'Unenforced', mark: TONE_HEX.bad },
  { key: 'unknown', name: 'Unknown', mark: TONE_HEX.neutral },
];

/**
 * Band a repository by the approvals its gate requires.
 *
 * `assessment.review_requirement` vetoes a gate requiring none, which is why `0` is red rather than
 * amber, and clears one requiring any — so two or more is not a second verdict but a deeper shade of
 * the one the policy already gives. Absent is the row's own word for unmeasured: no gate was
 * collected, or the branch is protected and GitHub withheld its rules. An unprotected branch reports
 * `0` and lands in the red band, because the gate was read and it requires nothing.
 */
export function reviewBand(approvals: number | null | undefined): ReviewBand {
  if (approvals == null) {
    return 'unknown';
  }
  if (approvals >= 2) {
    return 'multiple';
  }
  return approvals >= 1 ? 'required' : 'none';
}

/** Whether the merge gate requires any status check, banded. */
export type ChecksBand = 'required' | 'none' | 'unknown';

export const CHECKS_BANDS: readonly Band<ChecksBand>[] = [
  { key: 'required', name: 'Enforced', mark: TONE_HEX.good },
  { key: 'none', name: 'Unenforced', mark: TONE_HEX.bad },
  { key: 'unknown', name: 'Unknown', mark: TONE_HEX.neutral },
];

/**
 * Band a repository by how many status checks its gate requires.
 *
 * Two bands rather than three, for the reason `gateFieldTone` gives the same field: this is the
 * CONFIGURATION — a gate requiring no check cannot block anything — and `checks-passing-at-merge` is
 * the condition that reads what CI actually held. Which checks they are, and whether any of them
 * passed, is not a question a count of required contexts answers. Absent is unmeasured, as above.
 */
export function checksBand(contexts: number | null | undefined): ChecksBand {
  if (contexts == null) {
    return 'unknown';
  }
  return contexts >= 1 ? 'required' : 'none';
}

/** The policy's verdict on unreviewed substantial merging, plus the window it graded nothing in. */
export type UnreviewedBand = UnreviewedSubstantialOutcome | 'unknown';

export const UNREVIEWED_BANDS: readonly Band<UnreviewedBand>[] = [
  // `none` stays as the key because `unreviewedBand` matches the policy's own outcome values against
  // it; only the words the legend reads change.
  { key: 'none', name: 'Clear', mark: TONE_HEX.good },
  { key: 'within', name: 'Within allowance', mark: TONE_HEX.warn },
  { key: 'above', name: 'Above allowance', mark: TONE_HEX.bad },
  { key: 'unknown', name: 'Unknown', mark: TONE_HEX.neutral },
];

/**
 * Band a repository by `ReadinessPolicy.unreviewed_substantial_outcome`, which decided this already.
 *
 * The three words are the policy's own, so nothing is graded here — `within` is amber because the
 * allowance forgiving what it was configured to forgive is worth weighing and is not a pass. Absent
 * is the third thing the policy says: a cohort too thin to grade, a window holding no substantial
 * merge, or an assessment that is off. All three are unmeasured, and an unmeasured thing is never
 * reported as clean.
 *
 * A verdict this build does not know is unmeasured too, for the reason `distributionState` folds an
 * unrecognised label: the pages are served from a `metrics-serve` versioned apart from them, and a
 * value falling in no band would be a repository counted in no slice — a donut quietly totalling
 * less than the estate rather than admitting it read something it did not understand.
 */
export function unreviewedBand(outcome: UnreviewedSubstantialOutcome | null | undefined): UnreviewedBand {
  return UNREVIEWED_BANDS.find((band) => band.key === outcome)?.key ?? 'unknown';
}

/** Test coverage, banded on the boundary `coverageTone` states. */
export type CoverageBand = 'high' | 'moderate' | 'low' | 'unknown';

export const COVERAGE_BANDS: readonly Band<CoverageBand>[] = [
  { key: 'high', name: '90% or more', mark: TONE_HEX.good },
  // Not "80% to 90%": exactly 90 is graded good, so a legend naming 90 twice would put the boundary
  // in the band it is not in.
  { key: 'moderate', name: '80% to under 90%', mark: TONE_HEX.warn },
  { key: 'low', name: 'Below 80%', mark: TONE_HEX.bad },
  { key: 'unknown', name: 'Unknown', mark: TONE_HEX.neutral },
];

/** The band each tone `coverageTone` returns falls in, total over the four so no lookup can miss. */
const COVERAGE_BAND: Record<Tone, CoverageBand> = {
  good: 'high',
  warn: 'moderate',
  bad: 'low',
  neutral: 'unknown',
};

/**
 * Band a repository by its SonarCloud coverage, through the tone its own page is coloured with.
 *
 * Delegated rather than restated so the 90 and the 80 move together: a reader comparing the donut
 * with the repository's coverage card is entitled to find them agreeing. Absent is unmeasured — the
 * repository resolved to no SonarCloud project, its measures could not be read, or the project sent
 * no coverage metric — and never 0%.
 */
export function coverageBand(coverage: number | null | undefined): CoverageBand {
  return COVERAGE_BAND[coverageTone(coverage)];
}

/** How badly a repository's security signals read, worst signal deciding. */
export type SecurityBand = 'clear' | 'medium' | 'high' | 'unknown';

export const SECURITY_BANDS: readonly Band<SecurityBand>[] = [
  { key: 'clear', name: 'Clear', mark: TONE_HEX.good },
  { key: 'medium', name: 'Medium', mark: TONE_HEX.warn },
  { key: 'high', name: 'High', mark: TONE_HEX.bad },
  { key: 'unknown', name: 'Unknown', mark: TONE_HEX.neutral },
];

/**
 * The three security fields a row carries, named as the row's own so a row IS one of these.
 *
 * Three fields holding five signals between them, `security` carrying three alert families. A shape
 * of its own rather than a `RepositoryRow` parameter so the banding can be tested — and later read
 * off some other list — without inventing a repository and a team around three facts.
 */
export interface SecuritySignals {
  security?: SecurityAlertEvidence | null;
  sonar_security_rating?: SonarRating | null;
  sonar_security_issues?: number | null;
}

/**
 * Band a repository by the worst of its security signals, or unmeasured where it has none.
 *
 * Five signals, each resolved to a tone or to `neutral` where there is no data, and the worst tone
 * present decides — so one open critical Dependabot alert bands the repository High however clean the
 * other four read. The three alert families delegate to `alertTone`, which the repository page
 * already colours its security cards with, so the donut and the cards cannot disagree about a family.
 * They are named HERE rather than imported as `ALERT_FAMILIES`: `repository.ts` owns that list and
 * imports this module for `alertTone`, so reaching back for it would close an import cycle. Sonar's
 * one remaining count is worth weighing above zero and never worse: the count is graded by its own
 * rating, which is a signal here in its own right.
 *
 * The security hotspot count was a sixth signal until 2026-09-04, when Sonar's transition of hotspots
 * into vulnerabilities retired it. Dropping it moved NO repository to a WORSE band: all 266 reporting
 * the metric reported zero, so the signal could only ever resolve `good`. It could in principle move
 * a row the other way — one whose ONLY signal was that zero would now read Unknown rather than Clear
 * — but that needs Sonar measures with no rating and no issue count and all three alert families
 * withheld, and the snapshot held no such row.
 *
 * `unknown` is EVERY signal carrying no data — a row the span could not report, or one whose three
 * families GitHub all refused and which has no Sonar measures. A repository with readable families
 * and no Sonar project is Clear: its security was read, and there was nothing open.
 *
 * The rating goes through `sonarRatingTone`, so the donut and the repository page's card grade the
 * letter IDENTICALLY. This donut banded C at High until 2026-09-04, deliberately stricter than the
 * card; that is reversed, and the reason is a measurement rather than a preference — a C-at-High
 * boundary put 78 of the estate's 266 rated repositories in the red band and left 7 in amber, so the
 * middle band named the one letter almost nothing holds. Sonar has already weighed the finding
 * against the project, and a scale that reports A or nothing is not reporting a scale. Alerts are
 * untouched by that reversal: a critical or high alert is still red however the rating reads, so a
 * repository only moves to Medium when its alert families are clean.
 */
export function securityBand(signals: SecuritySignals): SecurityBand {
  const alerts = signals.security;
  const families: Tone[] = alerts
    ? [
        alertTone('dependabot', alerts.dependabot),
        alertTone('code-scanning', alerts.code_scanning),
        alertTone('secret-scanning', alerts.secret_scanning),
      ]
    : [];
  const tones = [
    ...families,
    sonarRatingTone(signals.sonar_security_rating),
    counted(signals.sonar_security_issues, 'good', 'warn'),
  ].filter((tone) => tone !== 'neutral');
  if (tones.includes('bad')) {
    return 'high';
  }
  if (tones.includes('warn')) {
    return 'medium';
  }
  return tones.length > 0 ? 'clear' : 'unknown';
}
