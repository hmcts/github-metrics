/**
 * What one repository's evidence block says, reduced to the rows and cards the page draws.
 *
 * All of it is derivation, none of it is judgement: every value here is already in the block, and
 * the only work done to it is choosing the words. The rules it inherits from `metrics.render`, which
 * prints the same block as text, are the ones worth stating:
 *
 *   • An unreadable thing is a dash and its reason, NEVER a zero. A security family GitHub refused
 *     and a family with nothing open look identical as `0` and mean opposite things.
 *   • Every row is drawn even where its value is absent, so the page says what was asked for as well
 *     as what came back — a missing Sonar row would read as a measure nobody wanted.
 *   • A tri-state stays three-valued: "not disclosed" for a gate field GitHub withheld, "unknown"
 *     for a maintenance answer the bounded search stopped short of.
 *
 * The page renders what these return in order and adds nothing to it, so a figure on the page and
 * the same figure in `metrics evidence` output are one claim rather than two.
 */

import { ABSENT, count, figure, instant, percent, quantity } from '@/lib/format';
import {
  alertTone,
  codeownersTone,
  gateFieldTone,
  maintenanceTone,
  openPullRequestTone,
  sonarGateTone,
  sonarMeasureTone,
  sonarRatingTone,
  type ConditionOutcome,
  type Tone,
} from '@/lib/tone';
import type {
  CodeownersReport,
  CohortSummary,
  MaintenanceReport,
  MergeGateEvidence,
  OpenAlertCount,
  OpenPullRequestReport,
  ReadinessAssessment,
  ReadinessCondition,
  SecurityAlertEvidence,
  SonarMeasures,
  SonarRating,
  SonarReport,
} from '@/lib/types';

/**
 * One labelled figure: what it is, what it was, and what it was measured over or read from.
 *
 * The tone is PRESENTATION rather than a fourth figure — how the value reads, resolved through the
 * threshold table in `lib/tone.ts` and never decided here. Every row a builder in this module
 * returns states one, `neutral` included, so a figure the page draws with no colour has said so
 * rather than been forgotten; a row assembled elsewhere may leave it off and reads as neutral.
 */
export interface LabelledValue {
  label: string;
  value: string;
  detail?: string;
  tone?: Tone;
}

/** Render a tri-state field without turning a value GitHub withheld into a false one. */
export function yesOrNo(value: boolean | null | undefined): string {
  if (value === null || value === undefined) {
    return 'not disclosed';
  }
  return value ? 'yes' : 'no';
}

/**
 * Group one assessment's conditions the way the policy grouped them.
 *
 * The three groups are always returned, empty ones included, because "nothing blocked this
 * repository" is the finding behind a green label and an absent group would leave it unsaid.
 *
 * The membership of each group is the policy's and nothing here moves a condition between them. The
 * one reordering is inside `clear`, where `gradedFirst` sinks the informational rows below the
 * graded ones; every condition the policy put there is still there, and still exactly once.
 */
export interface ConditionGroup {
  key: ConditionOutcome;
  heading: string;
  detail: string;
  /** What an empty group means, which is a finding rather than an absence of one. */
  empty: string;
  conditions: ReadinessCondition[];
}

/**
 * The clear group with its informational rows last, graded ones first.
 *
 * Both kinds sit in the same section but say different things: a graded condition was checked and
 * satisfied, an informational one was reported and never judged, which is why `tone.conditionTone`
 * gives the first a green bar and the second none. Reading down a section that alternates between
 * them, the ungraded rows read as checks that happened to lose their colour — so the green ones are
 * gathered at the top and the uncoloured ones below, where the break between the two is the thing
 * the eye lands on. Stable within each half: the policy's own order is what remains inside them.
 */
function gradedFirst(conditions: ReadinessCondition[]): ReadinessCondition[] {
  const graded = conditions.filter((condition) => !(condition.informational ?? false));
  const informational = conditions.filter((condition) => condition.informational ?? false);
  return [...graded, ...informational];
}

export function conditionGroups(assessment: ReadinessAssessment): ConditionGroup[] {
  return [
    {
      key: 'blocking',
      heading: 'Blocking',
      detail: 'held the label below ready, each carrying the ceiling it imposed',
      empty: 'Nothing blocked this repository: an empty blocking group is the only way to reach ready.',
      conditions: assessment.blocking,
    },
    {
      key: 'caution',
      heading: 'Caution',
      detail: 'not disqualifying, but worth weighing',
      empty: 'Nothing the policy checked was flagged for caution.',
      conditions: assessment.caution,
    },
    {
      key: 'clear',
      heading: 'Clear',
      detail: 'checked, and imposing no ceiling',
      empty: 'Nothing the policy checked came back clear.',
      conditions: gradedFirst(assessment.clear),
    },
  ];
}

/**
 * How many merges the cohort left out, and who they were left out for.
 *
 * Excluded authors are bots and any login the configuration names, and the count is stated beside
 * the reported one rather than folded into it: a repository where half the merges are Dependabot's
 * has a very different window from one where none are, and only the two figures together say which.
 */
export function excludedMerges(cohort: CohortSummary): number {
  return Object.values(cohort.excluded_authors).reduce((total, merges) => total + merges, 0);
}

/** Name the excluded authors and their merge counts, or say there were none to exclude. */
export function excludedDetail(cohort: CohortSummary): string {
  const authors = Object.entries(cohort.excluded_authors);
  if (authors.length === 0) {
    return 'no author was excluded from this window';
  }
  return authors.map(([login, merges]) => `${login} ${merges}`).join(' · ');
}

/**
 * The merge gate as rows, in the order `metrics.render` prints them.
 *
 * The rules are FLATTENED, not summarised away: GitHub can answer with several pull-request rules
 * and several status-check rulesets for one branch, so the required approvals are the strictest of
 * them and the required contexts are all of them, each named once, which is what actually has to be
 * satisfied to merge. `unmodelled_rules` is printed for the same reason the JSON carries it — a limitation the
 * contract admits and the page hides is worse than one neither admits.
 *
 * No gate means no rows: the block carries the reason instead, and the page prints that rather than
 * a dozen dashes that would read as a branch with no protection on it.
 */
export function mergeGateRows(gate: MergeGateEvidence): LabelledValue[] {
  const required = Math.max(0, ...gate.pull_requests.map((rule) => rule.required_approving_review_count));
  // DISTINCT contexts, matching `MergeGateEvidence.required_contexts`: two rulesets both demanding
  // `build` demand one check, so naming it twice would print a row the text report does not and
  // count a check the estate donut does not.
  const contexts = [
    ...new Set(gate.status_checks.flatMap((rule) => rule.required_status_checks.map((check) => check.context))),
  ];
  const dismissed = gate.pull_requests.some((rule) => rule.dismiss_stale_reviews_on_push);
  // A protected branch whose rules GitHub WITHHELD arrives with the same empty rule arrays as one
  // that carries no rules at all — `inventory.merge_gate_without_rule_details` builds both, and
  // `rules_observed` is the only thing that separates them — so the three figures read off those
  // arrays come out as `0`, `none` and `no` either way and mean opposite things. The policy stops at
  // `merge-gate-rules-not-observable` there and grades none of the three, so the tone stops with it:
  // the withheld value is `undefined`, for which `gateFieldTone` answers neutral. The PRINTED value
  // is untouched and stays what `render.py` prints, so a page and the text report still state one
  // thing; it is the colour that would have blamed a missing permission on the team.
  const withheld = !gate.rules_observed;
  // The branch a gate was read on and the rules this build did not interpret are passed no value at
  // all: `GateValue` is the flag or the count a tone could be read off, and neither of those two
  // fields is one. `gateFieldTone` answers neutral for both, which is where that stays decided.
  return [
    { label: 'Branch', value: gate.branch, tone: gateFieldTone('branch') },
    { label: 'Protected', value: yesOrNo(gate.protected), tone: gateFieldTone('protected', gate.protected) },
    {
      label: 'Rules observed',
      value: yesOrNo(gate.rules_observed),
      tone: gateFieldTone('rules_observed', gate.rules_observed),
    },
    {
      label: 'Approving reviews required',
      value: String(required),
      tone: gateFieldTone('required_approving_review_count', withheld ? undefined : required),
    },
    {
      label: 'Required status checks',
      value: [...contexts].sort().join(', ') || 'none',
      tone: gateFieldTone('required_status_checks', withheld ? undefined : contexts.length),
    },
    {
      label: 'Dismiss stale reviews on push',
      value: yesOrNo(dismissed),
      tone: gateFieldTone('dismiss_stale_reviews_on_push', withheld ? undefined : dismissed),
    },
    {
      label: 'Applies to administrators',
      value: yesOrNo(gate.applies_to_administrators),
      tone: gateFieldTone('applies_to_administrators', gate.applies_to_administrators),
    },
    {
      label: 'Restricts deletions',
      value: yesOrNo(gate.restricts_deletions),
      tone: gateFieldTone('restricts_deletions', gate.restricts_deletions),
    },
    {
      label: 'Blocks force pushes',
      value: yesOrNo(gate.blocks_force_pushes),
      tone: gateFieldTone('blocks_force_pushes', gate.blocks_force_pushes),
    },
    {
      label: 'Requires linear history',
      value: yesOrNo(gate.requires_linear_history),
      tone: gateFieldTone('requires_linear_history', gate.requires_linear_history),
    },
    {
      label: 'Restricts branch names',
      value: yesOrNo(gate.restricts_branch_names),
      tone: gateFieldTone('restricts_branch_names', gate.restricts_branch_names),
    },
    {
      label: 'Rules not interpreted',
      value: gate.unmodelled_rules.join(', ') || 'none',
      tone: gateFieldTone('unmodelled_rules'),
    },
  ];
}

/**
 * The four open pull-request counts, each beside the period it actually describes.
 *
 * Two of them are bounded by the window the COLLECTION measured, which is not the window this page
 * is being read at, and two describe the queue as it stood when the state was read. Saying so on
 * each card is the whole point: a stale count under this page's own dates would be a wrong claim
 * about when it was true. No summary means no cards — the block carries a reason instead, and the
 * page prints that rather than four dashes.
 */
export function openPullRequestCards(report: OpenPullRequestReport): LabelledValue[] {
  const summary = report.summary;
  if (summary === undefined) {
    return [];
  }
  const measured =
    report.starts_at === undefined || report.ends_at === undefined
      ? undefined
      : `${instant(report.starts_at)} to ${instant(report.ends_at)}`;
  const read = report.fetched_at === undefined ? undefined : `as at ${instant(report.fetched_at)}`;
  return [
    {
      label: 'Opened in window',
      value: String(summary.opened_in_window),
      detail: measured,
      tone: openPullRequestTone('opened_in_window', summary.opened_in_window),
    },
    {
      label: 'Closed without merge',
      value: String(summary.closed_without_merge),
      detail: measured,
      tone: openPullRequestTone('closed_without_merge', summary.closed_without_merge),
    },
    {
      label: 'Currently open',
      value: String(summary.currently_open),
      detail: read,
      tone: openPullRequestTone('currently_open', summary.currently_open),
    },
    {
      label: 'Stale open',
      value: String(summary.stale_open),
      detail: read,
      tone: openPullRequestTone('stale_open', summary.stale_open),
    },
  ];
}

/** The three alert families, in the one order every rendering of this block lists them in. */
export const ALERT_FAMILIES = ['dependabot', 'code-scanning', 'secret-scanning'] as const;

export type AlertFamily = (typeof ALERT_FAMILIES)[number];

const SEVERITIES = ['critical', 'high', 'medium', 'low'] as const;

/**
 * Break one family's open alerts down by severity, or say why there is no breakdown to give.
 *
 * Secret-scanning alerts carry no severity at all, so the family says so rather than printing four
 * zeros: GitHub asserts nothing there, and a row of zeros would read as an assertion that it did.
 */
export function severityDetail(family: AlertFamily, count: OpenAlertCount): string {
  if (count.open === undefined) {
    return count.detail ?? 'not available';
  }
  if (family === 'secret-scanning') {
    return 'no severity is reported for this family';
  }
  return SEVERITIES.map((severity) => `${severity} ${count.by_severity[severity] ?? 0}`).join(' · ');
}

export function securityCards(alerts: SecurityAlertEvidence): LabelledValue[] {
  const counts: Record<AlertFamily, OpenAlertCount> = {
    dependabot: alerts.dependabot,
    'code-scanning': alerts.code_scanning,
    'secret-scanning': alerts.secret_scanning,
  };
  return ALERT_FAMILIES.map((family) => ({
    label: family,
    value: figure(counts[family].open),
    detail: severityDetail(family, counts[family]),
    tone: alertTone(family, counts[family]),
  }));
}

/**
 * State one maintenance window's two answers: any commit, and a human one.
 *
 * The human answer is three-valued and stays that way. "unknown" is not "no": the search that would
 * have answered it stopped at its own bound, and the reason the block carries is printed with it.
 */
export function maintenanceRows(report: MaintenanceReport): LabelledValue[] {
  return report.windows.map((window) => ({
    label: `${window.months} months`,
    value: yesOrNo(window.committed_within),
    tone: maintenanceTone(window.committed_within),
    detail:
      window.human_detail === undefined
        ? `human commit: ${humanAnswer(window.human_committed_within)}`
        : `human commit: ${humanAnswer(window.human_committed_within)} — ${window.human_detail}`,
  }));
}

function humanAnswer(value: boolean | undefined): string {
  return value === undefined ? 'unknown' : yesOrNo(value);
}

/**
 * The instants the window answers were derived from, or the reason there are none.
 *
 * The search bound is stated whenever the block carries one, because it is what separates "no human
 * commit within this window" from "unknown beyond the commits that were examined".
 */
export function maintenanceSummary(report: MaintenanceReport): string {
  const maintenance = report.maintenance;
  if (maintenance === undefined) {
    return report.detail ?? 'not available';
  }
  // `== null` catches both shapes on purpose: the service omits an unobserved instant rather than
  // sending `null`, and a strict `=== null` here would print `searched back to -` on every
  // repository whose search DID find a human commit — the common path, and the one absence this
  // block exists to keep apart from "none within the window".
  const lastCommit =
    maintenance.last_commit_at == null
      ? 'none: the branch has no commits'
      : instant(maintenance.last_commit_at);
  const lastHuman =
    maintenance.last_human_commit_at == null ? 'none found' : instant(maintenance.last_human_commit_at);
  const searched =
    maintenance.searched_back_to == null
      ? []
      : [`searched back to ${instant(maintenance.searched_back_to)}`];
  return [
    `branch ${maintenance.branch}`,
    `last commit ${lastCommit}`,
    `last human commit ${lastHuman}`,
    ...searched,
  ].join(' · ');
}

/**
 * CODEOWNERS as one card: how many files were found, or that none was, or why nobody could look.
 *
 * Found-and-empty, absent, and unreadable are three answers and this keeps them apart. The size
 * keeps an empty file visible as found-but-empty, and the recognised flag keeps a `.md` variant —
 * which the minimum standard names and GitHub ignores — apart from a file GitHub actually reads.
 */
export function codeownersCard(report: CodeownersReport): LabelledValue {
  const codeowners = report.codeowners;
  if (codeowners === undefined) {
    return {
      label: 'CODEOWNERS',
      value: ABSENT,
      detail: report.detail ?? 'not available',
      tone: codeownersTone(undefined),
    };
  }
  if (codeowners.files.length === 0) {
    return {
      label: 'CODEOWNERS',
      value: 'absent',
      detail: 'no CODEOWNERS file at any of the checked locations',
      tone: codeownersTone(0),
    };
  }
  return {
    label: 'CODEOWNERS',
    value: count(codeowners.files.length, 'file', 'files'),
    tone: codeownersTone(codeowners.files.length),
    detail: codeowners.files
      .map(
        (file) =>
          `${file.path} (${file.size_bytes} bytes, ${file.recognised_by_github ? 'recognised' : 'not recognised'} by GitHub)`,
      )
      .join(' · '),
  };
}

/**
 * The SonarCloud quality gate as one card, naming the project the verdict is about.
 *
 * REPORT-ONLY AND UNGRADED, as the report has it: a failing gate imposes no readiness ceiling and
 * carries no label here either. A project that resolved but whose measures could not be read is
 * still named above its reason, because the project a refusal was about is the first thing needed
 * to chase it.
 */
export function sonarGateCard(report: SonarReport): LabelledValue {
  const named = report.mapping === undefined ? [] : [`project ${report.mapping.project_key}`];
  const measures = report.measures;
  if (measures === undefined) {
    return {
      label: 'Quality gate',
      value: ABSENT,
      detail: [...named, report.detail ?? 'not available'].join(' · '),
      tone: sonarGateTone(undefined),
    };
  }
  const analysed = measures.analysis_at === undefined ? 'never analysed' : `analysed ${instant(measures.analysis_at)}`;
  return {
    label: 'Quality gate',
    value: measures.gate === undefined ? 'not reported' : measures.gate.level,
    detail: [...named, analysed].join(' · '),
    tone: sonarGateTone(measures.gate?.level),
  };
}

/** The `A`-to-E letter behind a SonarCloud rating, or the number where this build knows no letter. */
export const SONAR_RATING_LETTERS = ['A', 'B', 'C', 'D', 'E'] as const;

export function ratingLetter(rating: SonarRating | undefined): string {
  if (rating === undefined) {
    return ABSENT;
  }
  const index = rating.value;
  if (!Number.isInteger(index) || index < 1 || index > SONAR_RATING_LETTERS.length) {
    // Never a letter for a value off the scale: defaulting to either end would report a rating this
    // build does not understand as the best or the worst there is.
    return `unknown (${figure(index)})`;
  }
  return SONAR_RATING_LETTERS[index - 1] as string;
}

/**
 * Every measure the project reported, each unreported one left visibly absent.
 *
 * The seven counts are toned against the whole measure set rather than against their own value,
 * because an issue count borrows its severity from the rating that covers it — `lib/tone.ts` reads
 * both, and a count with no rating beside it is worth weighing and never worse.
 */
export function sonarRows(measures: SonarMeasures): LabelledValue[] {
  return [
    { label: 'Coverage', value: percent(measures.coverage), tone: sonarMeasureTone('coverage', measures) },
    {
      label: 'Duplicated lines',
      value: percent(measures.duplicated_lines_density),
      tone: sonarMeasureTone('duplicated_lines_density', measures),
    },
    {
      label: 'Lines of code',
      value: quantity(measures.lines_of_code),
      tone: sonarMeasureTone('lines_of_code', measures),
    },
    {
      label: 'Violations',
      value: quantity(measures.violations),
      tone: sonarMeasureTone('violations', measures),
    },
    {
      label: 'Reliability issues',
      value: quantity(measures.reliability_issues),
      tone: sonarMeasureTone('reliability_issues', measures),
    },
    {
      label: 'Maintainability issues',
      value: quantity(measures.maintainability_issues),
      tone: sonarMeasureTone('maintainability_issues', measures),
    },
    {
      label: 'Security issues',
      value: quantity(measures.security_issues),
      tone: sonarMeasureTone('security_issues', measures),
    },
    {
      label: 'Reliability rating',
      value: ratingLetter(measures.reliability_rating),
      tone: sonarRatingTone(measures.reliability_rating),
    },
    {
      label: 'Maintainability rating',
      value: ratingLetter(measures.maintainability_rating),
      tone: sonarRatingTone(measures.maintainability_rating),
    },
    {
      label: 'Security rating',
      value: ratingLetter(measures.security_rating),
      tone: sonarRatingTone(measures.security_rating),
    },
  ];
}
