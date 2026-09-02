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

import { type RAGState, state } from '@/lib/rag';
import type {
  AlertFamily,
  OpenAlertCount,
  ReadinessCondition,
  ReadinessLabel,
  SonarGateLevel,
  SonarMeasures,
  SonarRating,
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
 */
export function conditionTone(outcome: ConditionOutcome, condition: ReadinessCondition): Tone | undefined {
  if (outcome === 'blocking') {
    return undefined;
  }
  // Absent from a block stored before the flag existed, which reads as a graded condition: the
  // three that carry it say so in their detail too, so an old block loses a colour, not a fact.
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

export function gateFieldTone(field: GateField, value: GateValue): Tone {
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

/** The eight numeric Sonar measures, keyed as the fields `SonarMeasures` carries them in. */
export type SonarMeasure =
  | 'coverage'
  | 'duplicated_lines_density'
  | 'lines_of_code'
  | 'violations'
  | 'reliability_issues'
  | 'maintainability_issues'
  | 'security_issues'
  | 'security_hotspots';

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

const SONAR_MEASURE_TONE: Record<SonarMeasure, (measures: SonarMeasures) => Tone> = {
  // Sonar's own default gate boundary for new code is 80%, and 90% is the target a project aiming
  // higher sets. Neither is a readiness condition: the policy reads no Sonar measure at all.
  coverage: (measures) => floor(measures.coverage, 90, 80),
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
  // Reviewed against the security REVIEW rating, which is the one Sonar grades hotspots with.
  security_hotspots: (measures) => issueTone(measures.security_hotspots, measures.security_review_rating),
};

export function sonarMeasureTone(measure: SonarMeasure, measures: SonarMeasures): Tone {
  return SONAR_MEASURE_TONE[measure](measures);
}
