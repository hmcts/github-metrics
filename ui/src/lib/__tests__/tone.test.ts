/**
 * Every threshold in `tone.ts`, at its boundary and on both sides of it.
 *
 * The cases that matter most are the ones asserting NO colour: an unreadable figure, the three
 * merge-gate rules the readiness policy reports without judging, and a Sonar measure the project
 * never sent. A threshold that quietly turned one of those green would report a refusal as a pass,
 * which is the one failure mode a colour on a page can have.
 */

import { describe, expect, it } from 'vitest';
import {
  TONES,
  TONE_BORDER,
  TONE_VALUE,
  alertTone,
  borderClass,
  codeownersTone,
  conditionTone,
  directCommitTone,
  gateFieldTone,
  maintenanceTone,
  openPullRequestTone,
  sonarGateTone,
  sonarMeasureTone,
  sonarRatingTone,
  valueClass,
} from '@/lib/tone';
import type { GateField, OpenPullRequestField, SonarMeasure } from '@/lib/tone';
import type { OpenAlertCount, SonarMeasures } from '@/lib/types';

/** The measures a repository reported, with only the fields one case is about filled in. */
function measures(fields: Partial<SonarMeasures> = {}): SonarMeasures {
  return { project_key: 'hmcts_api', ...fields };
}

function alerts(fields: Partial<OpenAlertCount> = {}): OpenAlertCount {
  return { by_severity: {}, ...fields };
}

describe('tone maps', () => {
  it('covers every tone in every map, so no lookup needs a fallback', () => {
    for (const map of [TONE_VALUE, TONE_BORDER]) {
      expect(Object.keys(map).sort()).toEqual([...TONES].sort());
    }
  });

  it('resolves the three coloured tones through the report palette, not a second one', () => {
    expect(TONE_VALUE.good).toBe('text-rag-green');
    expect(TONE_VALUE.warn).toBe('text-rag-amber');
    expect(TONE_VALUE.bad).toBe('text-rag-red');
  });

  it('leaves a neutral figure in the slate every figure was already set in', () => {
    expect(TONE_VALUE.neutral).toBe('text-slate-100');
    expect(TONE_VALUE.neutral).not.toMatch(/rag-|red|amber|green/);
    expect(TONE_BORDER.neutral).not.toMatch(/rag-|red|amber|green/);
  });

  it('gives every tone a left bar of the same weight, so a list does not jog', () => {
    for (const tone of TONES) {
      expect(TONE_BORDER[tone]).toContain('border-l-4');
    }
  });

  it('reads an absent tone as neutral rather than throwing on the lookup', () => {
    expect(valueClass(undefined)).toBe(TONE_VALUE.neutral);
    expect(borderClass(undefined)).toBe(TONE_BORDER.neutral);
    expect(valueClass('bad')).toBe(TONE_VALUE.bad);
    expect(borderClass('good')).toBe(TONE_BORDER.good);
  });
});

describe('assessment conditions', () => {
  it('leaves a blocking condition to the label it imposed', () => {
    expect(
      conditionTone('blocking', {
        condition: 'branch-not-protected',
        label: 'red',
        detail: 'the default branch master has no protection',
      }),
    ).toBeUndefined();
  });

  it('cautions on a caution and passes a graded clear condition', () => {
    expect(
      conditionTone('caution', {
        condition: 'status-checks-not-required',
        detail: 'status checks required before merging to master: 0',
        informational: false,
      }),
    ).toBe('warn');
    expect(
      conditionTone('clear', {
        condition: 'independent-review-coverage-at-target',
        detail: 'independent-review-coverage is 100% (10 of 10)',
        informational: false,
      }),
    ).toBe('good');
  });

  it('grades a condition the policy reported without judging not at all', () => {
    // `ReadinessPolicy.neutral()` and the sufficient cohort: green here would report a rule nobody
    // grades as a check that passed.
    for (const condition of ['linear-history-not-required', 'sufficient-merges']) {
      expect(conditionTone('clear', { condition, detail: 'reported, not judged', informational: true })).toBe(
        'neutral',
      );
    }
  });

  it('reads a block stored before the flag existed as a graded condition', () => {
    expect(conditionTone('clear', { condition: 'branch-protected', detail: 'protected' })).toBe('good');
  });
});

describe('the cohort', () => {
  it('cautions on any direct commit and never on none', () => {
    expect(directCommitTone(0)).toBe('good');
    expect(directCommitTone(1)).toBe('warn');
    expect(directCommitTone(214)).toBe('warn');
  });

  it('grades an uncounted cohort figure not at all', () => {
    expect(directCommitTone(undefined)).toBe('neutral');
  });
});

describe('CODEOWNERS', () => {
  it('separates found, absent and unreadable', () => {
    expect(codeownersTone(1)).toBe('good');
    expect(codeownersTone(3)).toBe('good');
    expect(codeownersTone(0)).toBe('warn');
    expect(codeownersTone(undefined)).toBe('neutral');
  });
});

describe('the merge gate', () => {
  it('grades the two veto fields red where they are unsatisfied', () => {
    expect(gateFieldTone('protected', true)).toBe('good');
    expect(gateFieldTone('protected', false)).toBe('bad');
    expect(gateFieldTone('required_approving_review_count', 1)).toBe('good');
    expect(gateFieldTone('required_approving_review_count', 2)).toBe('good');
    expect(gateFieldTone('required_approving_review_count', 0)).toBe('bad');
  });

  it('grades a gate with no required status check red and one with any check green', () => {
    expect(gateFieldTone('required_status_checks', 0)).toBe('bad');
    expect(gateFieldTone('required_status_checks', 4)).toBe('good');
  });

  it('cautions rather than blocks on the two review-freshness fields', () => {
    expect(gateFieldTone('dismiss_stale_reviews_on_push', true)).toBe('good');
    expect(gateFieldTone('dismiss_stale_reviews_on_push', false)).toBe('warn');
    expect(gateFieldTone('applies_to_administrators', true)).toBe('good');
    expect(gateFieldTone('applies_to_administrators', false)).toBe('warn');
  });

  it('reads unobserved rules as unanswered rather than as a failing gate', () => {
    // cannot_assess in the policy, and slate in `rag.ts` for the same reason: nobody could read it.
    expect(gateFieldTone('rules_observed', true)).toBe('good');
    expect(gateFieldTone('rules_observed', false)).toBe('neutral');
  });

  it('grades no field GitHub withheld', () => {
    const withheld: GateField[] = [
      'protected',
      'rules_observed',
      'required_approving_review_count',
      'required_status_checks',
      'dismiss_stale_reviews_on_push',
      'applies_to_administrators',
    ];
    for (const field of withheld) {
      expect(gateFieldTone(field, undefined)).toBe('neutral');
      expect(gateFieldTone(field, null)).toBe('neutral');
    }
  });

  it('leaves the policy’s own neutral trio uncoloured in both states', () => {
    // `ReadinessPolicy.neutral()` reports these whether configured or not, and they bear on the
    // label in neither state — so colouring them here would grade what the policy will not.
    const trio: GateField[] = ['restricts_deletions', 'requires_linear_history', 'restricts_branch_names'];
    for (const field of trio) {
      expect(gateFieldTone(field, true)).toBe('neutral');
      expect(gateFieldTone(field, false)).toBe('neutral');
    }
  });

  it('leaves the descriptive fields uncoloured whatever they say', () => {
    expect(gateFieldTone('branch', undefined)).toBe('neutral');
    expect(gateFieldTone('unmodelled_rules', 2)).toBe('neutral');
    // Unnamed by the tone table, so neutral by that table's default — its `force-pushes-not-blocked`
    // counterpart is a caution, and this is the one field where the two do not line up.
    expect(gateFieldTone('blocks_force_pushes', false)).toBe('neutral');
  });
});

describe('open pull requests', () => {
  it('cautions on a stale pull request and on nothing else', () => {
    expect(openPullRequestTone('stale_open', 0)).toBe('good');
    expect(openPullRequestTone('stale_open', 1)).toBe('warn');
  });

  it('grades throughput and queue depth not at all', () => {
    const plain: OpenPullRequestField[] = ['opened_in_window', 'closed_without_merge', 'currently_open'];
    for (const field of plain) {
      expect(openPullRequestTone(field, 0)).toBe('neutral');
      expect(openPullRequestTone(field, 87)).toBe('neutral');
    }
  });

  it('grades an uncounted figure not at all', () => {
    expect(openPullRequestTone('stale_open', undefined)).toBe('neutral');
  });
});

describe('security alerts', () => {
  it('reads a family with nothing open as clear', () => {
    expect(alertTone('dependabot', alerts({ open: 0 }))).toBe('good');
    expect(alertTone('code-scanning', alerts({ open: 0 }))).toBe('good');
    expect(alertTone('secret-scanning', alerts({ open: 0 }))).toBe('good');
  });

  it('takes its severity from the alerts that are actually open', () => {
    expect(alertTone('dependabot', alerts({ open: 9, by_severity: { medium: 4, low: 5 } }))).toBe('warn');
    expect(alertTone('dependabot', alerts({ open: 10, by_severity: { high: 1, medium: 4, low: 5 } }))).toBe(
      'bad',
    );
    expect(alertTone('code-scanning', alerts({ open: 1, by_severity: { critical: 1 } }))).toBe('bad');
    expect(alertTone('code-scanning', alerts({ open: 2, by_severity: { low: 2 } }))).toBe('warn');
  });

  it('reads any open secret as bad, since the family reports no severity to weigh', () => {
    expect(alertTone('secret-scanning', alerts({ open: 1 }))).toBe('bad');
  });

  it('cautions on an open alert whose severity nobody broke down', () => {
    expect(alertTone('dependabot', alerts({ open: 3 }))).toBe('warn');
  });

  it('grades a family GitHub refused not at all: an absence is not a zero', () => {
    const refused = alerts({ detail: 'alerts are not enabled for this repository' });
    for (const family of ['dependabot', 'code-scanning', 'secret-scanning'] as const) {
      expect(alertTone(family, refused)).toBe('neutral');
    }
  });
});

describe('maintenance', () => {
  it('cautions on a window with no commit in it and clears one with a commit', () => {
    expect(maintenanceTone(true)).toBe('good');
    expect(maintenanceTone(false)).toBe('warn');
  });

  it('leaves an unknown answer uncoloured: the search stopped at its own bound', () => {
    expect(maintenanceTone(undefined)).toBe('neutral');
    expect(maintenanceTone(null)).toBe('neutral');
  });
});

describe('the Sonar quality gate', () => {
  it('reads OK as good and ERROR as bad', () => {
    expect(sonarGateTone('OK')).toBe('good');
    expect(sonarGateTone('ERROR')).toBe('bad');
  });

  it('grades an unconfigured or unreported gate not at all', () => {
    expect(sonarGateTone('NONE')).toBe('neutral');
    expect(sonarGateTone(undefined)).toBe('neutral');
  });
});

describe('Sonar ratings', () => {
  it('grades A green, B and C amber, D and E red', () => {
    expect(sonarRatingTone({ value: 1 })).toBe('good');
    expect(sonarRatingTone({ value: 2 })).toBe('warn');
    expect(sonarRatingTone({ value: 3 })).toBe('warn');
    expect(sonarRatingTone({ value: 4 })).toBe('bad');
    expect(sonarRatingTone({ value: 5 })).toBe('bad');
  });

  it('grades an absent rating, and one off the scale, not at all', () => {
    expect(sonarRatingTone(undefined)).toBe('neutral');
    expect(sonarRatingTone({ value: 0 })).toBe('neutral');
    expect(sonarRatingTone({ value: 6 })).toBe('neutral');
    expect(sonarRatingTone({ value: 1.5 })).toBe('neutral');
  });
});

describe('Sonar measures', () => {
  it('grades coverage at its two boundaries', () => {
    expect(sonarMeasureTone('coverage', measures({ coverage: 90 }))).toBe('good');
    expect(sonarMeasureTone('coverage', measures({ coverage: 89.9 }))).toBe('warn');
    expect(sonarMeasureTone('coverage', measures({ coverage: 80 }))).toBe('warn');
    expect(sonarMeasureTone('coverage', measures({ coverage: 79.9 }))).toBe('bad');
    expect(sonarMeasureTone('coverage', measures({ coverage: 0 }))).toBe('bad');
  });

  it('grades duplication at its two boundaries, where lower is better', () => {
    expect(sonarMeasureTone('duplicated_lines_density', measures({ duplicated_lines_density: 0 }))).toBe('good');
    expect(sonarMeasureTone('duplicated_lines_density', measures({ duplicated_lines_density: 3 }))).toBe('good');
    expect(sonarMeasureTone('duplicated_lines_density', measures({ duplicated_lines_density: 3.1 }))).toBe(
      'warn',
    );
    expect(sonarMeasureTone('duplicated_lines_density', measures({ duplicated_lines_density: 5 }))).toBe('warn');
    expect(sonarMeasureTone('duplicated_lines_density', measures({ duplicated_lines_density: 5.1 }))).toBe(
      'bad',
    );
  });

  it('grades the size of the project not at all', () => {
    expect(sonarMeasureTone('lines_of_code', measures({ lines_of_code: 8607 }))).toBe('neutral');
    expect(sonarMeasureTone('lines_of_code', measures({ lines_of_code: 0 }))).toBe('neutral');
  });

  it('cautions on a violation count, which has no rating of its own to read', () => {
    expect(sonarMeasureTone('violations', measures({ violations: 0 }))).toBe('good');
    expect(sonarMeasureTone('violations', measures({ violations: 1442 }))).toBe('warn');
  });

  it('takes an issue count’s severity from its own rating', () => {
    // The estate figures the tone table was written from: 71 reliability issues under a D read red,
    // 1,358 maintainability issues under an A read amber, and 0 hotspots read green either way.
    const reported = measures({
      reliability_issues: 71,
      reliability_rating: { value: 4 },
      maintainability_issues: 1358,
      maintainability_rating: { value: 1 },
      security_issues: 13,
      security_rating: { value: 5 },
      security_hotspots: 0,
      security_review_rating: { value: 5 },
    });
    expect(sonarMeasureTone('reliability_issues', reported)).toBe('bad');
    expect(sonarMeasureTone('maintainability_issues', reported)).toBe('warn');
    expect(sonarMeasureTone('security_issues', reported)).toBe('bad');
    expect(sonarMeasureTone('security_hotspots', reported)).toBe('good');
  });

  it('cautions on an issue count whose rating was never reported', () => {
    expect(sonarMeasureTone('security_issues', measures({ security_issues: 4 }))).toBe('warn');
  });

  it('reads each issue count against its own rating and no other', () => {
    // security_hotspots reads the security REVIEW rating, which is the one Sonar grades it with.
    const mixed = measures({
      security_hotspots: 6,
      security_rating: { value: 5 },
      security_review_rating: { value: 1 },
    });
    expect(sonarMeasureTone('security_hotspots', mixed)).toBe('warn');
  });

  it('grades a measure the project never reported not at all', () => {
    const nothing = measures();
    const every: SonarMeasure[] = [
      'coverage',
      'duplicated_lines_density',
      'lines_of_code',
      'violations',
      'reliability_issues',
      'maintainability_issues',
      'security_issues',
      'security_hotspots',
    ];
    for (const measure of every) {
      expect(sonarMeasureTone(measure, nothing)).toBe('neutral');
    }
  });
});
