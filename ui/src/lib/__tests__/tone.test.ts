/**
 * Every threshold in `tone.ts`, at its boundary and on both sides of it.
 *
 * The cases that matter most are the ones asserting NO colour: an unreadable figure, the three
 * merge-gate rules the readiness policy reports without judging, and a Sonar measure the project
 * never sent. A threshold that quietly turned one of those green would report a refusal as a pass,
 * which is the one failure mode a colour on a page can have.
 */

import { describe, expect, it } from 'vitest';
import { RAG_HEX } from '@/lib/rag';
import {
  CHECKS_BANDS,
  COVERAGE_BANDS,
  REVIEW_BANDS,
  SECURITY_BANDS,
  STRONG_GOOD_HEX,
  TONES,
  TONE_BORDER,
  TONE_HEX,
  TONE_VALUE,
  UNREVIEWED_BANDS,
  alertTone,
  borderClass,
  checksBand,
  codeownersTone,
  conditionTone,
  coverageBand,
  coverageTone,
  directCommitTone,
  gateFieldTone,
  maintenanceTone,
  openPullRequestTone,
  reviewBand,
  securityBand,
  sonarGateTone,
  sonarMeasureTone,
  sonarRatingTone,
  unreviewedBand,
  valueClass,
} from '@/lib/tone';
import type { Band, GateField, OpenPullRequestField, SonarMeasure } from '@/lib/tone';
import type {
  OpenAlertCount,
  SecurityAlertEvidence,
  SonarMeasures,
  UnreviewedSubstantialOutcome,
} from '@/lib/types';

/** The measures a repository reported, with only the fields one case is about filled in. */
function measures(fields: Partial<SonarMeasures> = {}): SonarMeasures {
  return { project_key: 'hmcts_api', ...fields };
}

function alerts(fields: Partial<OpenAlertCount> = {}): OpenAlertCount {
  return { by_severity: {}, ...fields };
}

/** A security block whose three families are all clear, bar the one a case is about. */
function security(families: Partial<SecurityAlertEvidence> = {}): SecurityAlertEvidence {
  return {
    dependabot: alerts({ open: 0 }),
    code_scanning: alerts({ open: 0 }),
    secret_scanning: alerts({ open: 0 }),
    ...families,
  };
}

/** A block GitHub refused every family of, which is the shape that carries no security data. */
function refusedSecurity(): SecurityAlertEvidence {
  const refused = alerts({ detail: 'alerts are not enabled for this repository' });
  return { dependabot: refused, code_scanning: refused, secret_scanning: refused };
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

  it('draws its chart marks in the report palette rather than a second copy of it', () => {
    expect(Object.keys(TONE_HEX).sort()).toEqual([...TONES].sort());
    expect(TONE_HEX.good).toBe(RAG_HEX.green);
    expect(TONE_HEX.warn).toBe(RAG_HEX.amber);
    expect(TONE_HEX.bad).toBe(RAG_HEX.red);
    expect(TONE_HEX.neutral).toBe(RAG_HEX.none);
  });

  it('keeps the one mark that is not a tone out of the four', () => {
    // A deeper green than `good`, and nothing else on the site is set in it.
    expect(STRONG_GOOD_HEX).toBe('#16a34a');
    expect(Object.values(TONE_HEX)).not.toContain(STRONG_GOOD_HEX);
  });
});

describe('donut bands', () => {
  /** Every table: the marks a reader tells the bands apart by, and the order they read in. */
  const TABLES: readonly (readonly Band[])[] = [
    REVIEW_BANDS,
    CHECKS_BANDS,
    UNREVIEWED_BANDS,
    COVERAGE_BANDS,
    SECURITY_BANDS,
  ];

  it('ends every table with the unmeasured band, in the slate an ungraded row is drawn in', () => {
    for (const table of TABLES) {
      const last = table.at(-1);
      expect(last?.key).toBe('unknown');
      expect(last?.name).toBe('Unknown');
      expect(last?.mark).toBe(TONE_HEX.neutral);
    }
  });

  it('gives every band its own key, its own words and its own mark', () => {
    for (const table of TABLES) {
      expect(new Set(table.map((band) => band.key)).size).toBe(table.length);
      expect(new Set(table.map((band) => band.name)).size).toBe(table.length);
      expect(new Set(table.map((band) => band.mark)).size).toBe(table.length);
    }
  });

  it('orders each table best first, so the legend reads down into the estate’s worst', () => {
    expect(REVIEW_BANDS.map((band) => band.key)).toEqual(['multiple', 'required', 'none', 'unknown']);
    expect(CHECKS_BANDS.map((band) => band.key)).toEqual(['required', 'none', 'unknown']);
    expect(UNREVIEWED_BANDS.map((band) => band.key)).toEqual(['none', 'within', 'above', 'unknown']);
    expect(COVERAGE_BANDS.map((band) => band.key)).toEqual(['high', 'moderate', 'low', 'unknown']);
    expect(SECURITY_BANDS.map((band) => band.key)).toEqual(['clear', 'medium', 'high', 'unknown']);
  });
});

describe('the required-approvals band', () => {
  it('separates two approvals from exactly one, and one from none', () => {
    expect(reviewBand(3)).toBe('multiple');
    expect(reviewBand(2)).toBe('multiple');
    expect(reviewBand(1)).toBe('required');
    expect(reviewBand(0)).toBe('none');
  });

  it('counts a gate nobody could read as unmeasured rather than as requiring nothing', () => {
    expect(reviewBand(undefined)).toBe('unknown');
    expect(reviewBand(null)).toBe('unknown');
  });
});

describe('the required-checks band', () => {
  it('reads any required context as required and none as not required', () => {
    expect(checksBand(4)).toBe('required');
    expect(checksBand(1)).toBe('required');
    expect(checksBand(0)).toBe('none');
  });

  it('counts a gate nobody could read as unmeasured', () => {
    expect(checksBand(undefined)).toBe('unknown');
    expect(checksBand(null)).toBe('unknown');
  });
});

describe('the unreviewed-substantial band', () => {
  it('carries the policy’s three verdicts through unchanged', () => {
    expect(unreviewedBand('none')).toBe('none');
    expect(unreviewedBand('within')).toBe('within');
    expect(unreviewedBand('above')).toBe('above');
  });

  it('counts a window the policy graded nothing in as unmeasured, never as clean', () => {
    expect(unreviewedBand(undefined)).toBe('unknown');
    expect(unreviewedBand(null)).toBe('unknown');
  });

  it('counts a verdict this build does not know as unmeasured rather than as no band at all', () => {
    // The pages are served from a `metrics-serve` versioned apart from them, so a value off the
    // union is a real arrival; a row landing in no band would leave the donut short of the estate.
    expect(unreviewedBand('forgiven' as UnreviewedSubstantialOutcome)).toBe('unknown');
  });
});

describe('the coverage band', () => {
  it('bands on the boundary the repository page colours coverage with', () => {
    expect(coverageBand(100)).toBe('high');
    expect(coverageBand(90)).toBe('high');
    expect(coverageBand(89.9)).toBe('moderate');
    expect(coverageBand(80)).toBe('moderate');
    expect(coverageBand(79.9)).toBe('low');
    expect(coverageBand(0)).toBe('low');
  });

  it('reads one boundary with the card beside it, rather than a second copy of the numbers', () => {
    for (const coverage of [100, 90, 89.9, 80, 79.9, 0]) {
      expect(sonarMeasureTone('coverage', measures({ coverage }))).toBe(coverageTone(coverage));
    }
  });

  it('counts an unreported project as unmeasured rather than as 0%', () => {
    expect(coverageTone(undefined)).toBe('neutral');
    expect(coverageTone(null)).toBe('neutral');
    expect(coverageBand(undefined)).toBe('unknown');
    expect(coverageBand(null)).toBe('unknown');
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

describe('the security band', () => {
  it('bands a repository on its worst signal, however clean the rest read', () => {
    expect(
      securityBand({
        security: security({ dependabot: alerts({ open: 1, by_severity: { critical: 1 } }) }),
        sonar_security_rating: { value: 1 },
        sonar_security_issues: 0,
      }),
    ).toBe('high');
  });

  it('raises High and Medium off each alert family on its own', () => {
    const severe = alerts({ open: 2, by_severity: { high: 1, low: 1 } });
    const mild = alerts({ open: 2, by_severity: { medium: 1, low: 1 } });
    expect(securityBand({ security: security({ dependabot: severe }) })).toBe('high');
    expect(securityBand({ security: security({ dependabot: mild }) })).toBe('medium');
    expect(securityBand({ security: security({ code_scanning: severe }) })).toBe('high');
    expect(securityBand({ security: security({ code_scanning: mild }) })).toBe('medium');
    // Secret scanning reports no severity, so any open secret is High and it has no Medium.
    expect(securityBand({ security: security({ secret_scanning: alerts({ open: 1 }) }) })).toBe('high');
  });

  it('reads a family GitHub refused as no data rather than as nothing open', () => {
    const refused = alerts({ detail: 'alerts are not enabled for this repository' });
    // The other two families were read and are clear, so the repository is Clear on those alone.
    expect(securityBand({ security: security({ code_scanning: refused }) })).toBe('clear');
    expect(securityBand({ security: refusedSecurity() })).toBe('unknown');
  });

  it('grades the security rating exactly as the repository page colours the letter', () => {
    // No divergence since 2026-09-04: the donut reads `sonarRatingTone`, so C is amber in both.
    expect(securityBand({ sonar_security_rating: { value: 1 } })).toBe('clear');
    expect(securityBand({ sonar_security_rating: { value: 2 } })).toBe('medium');
    expect(securityBand({ sonar_security_rating: { value: 3 } })).toBe('medium');
    expect(sonarRatingTone({ value: 3 })).toBe('warn');
    expect(securityBand({ sonar_security_rating: { value: 4 } })).toBe('high');
    expect(securityBand({ sonar_security_rating: { value: 5 } })).toBe('high');
  });

  it('keeps a severe alert red under a C rating, so only clean families reach Medium', () => {
    // The reason the C reversal cannot quietly downgrade a repository with work outstanding.
    const rating = { value: 3 } as const;
    const severe = alerts({ open: 2, by_severity: { high: 1, low: 1 } });
    const mild = alerts({ open: 2, by_severity: { medium: 1, low: 1 } });
    expect(securityBand({ sonar_security_rating: rating, security: security({ dependabot: severe }) })).toBe(
      'high',
    );
    expect(securityBand({ sonar_security_rating: rating, security: security({ dependabot: mild }) })).toBe(
      'medium',
    );
    expect(securityBand({ sonar_security_rating: rating, security: security() })).toBe('medium');
  });

  it('reads a rating off the 1-to-5 scale as no data, not as the worst there is', () => {
    expect(securityBand({ sonar_security_rating: { value: 0 } })).toBe('unknown');
    expect(securityBand({ sonar_security_rating: { value: 6 } })).toBe('unknown');
    expect(securityBand({ sonar_security_rating: { value: 2.5 } })).toBe('unknown');
    expect(securityBand({ sonar_security_rating: null })).toBe('unknown');
  });

  it('cautions on a Sonar issue above zero, and clears one at zero', () => {
    expect(securityBand({ sonar_security_issues: 0 })).toBe('clear');
    expect(securityBand({ sonar_security_issues: 1 })).toBe('medium');
  });

  it('counts a repository with no signal at all as unmeasured', () => {
    expect(securityBand({})).toBe('unknown');
    expect(
      securityBand({
        security: null,
        sonar_security_rating: null,
        sonar_security_issues: null,
      }),
    ).toBe('unknown');
  });

  it('reads clear alerts and no Sonar project as Clear rather than as unmeasured', () => {
    // Its security WAS read, and there was nothing open; the absent Sonar measures add no doubt.
    expect(securityBand({ security: security() })).toBe('clear');
  });

  it('still bands a clean repository Clear now the hotspot signal has gone', () => {
    // Removing a signal on 2026-09-04 must not turn a measured repository Unknown: clear families
    // and an A rating are enough on their own, with no Sonar count present at all.
    expect(securityBand({ security: security(), sonar_security_rating: { value: 1 } })).toBe('clear');
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
    // and 1,358 maintainability issues under an A read amber.
    const reported = measures({
      reliability_issues: 71,
      reliability_rating: { value: 4 },
      maintainability_issues: 1358,
      maintainability_rating: { value: 1 },
      security_issues: 13,
      security_rating: { value: 5 },
    });
    expect(sonarMeasureTone('reliability_issues', reported)).toBe('bad');
    expect(sonarMeasureTone('maintainability_issues', reported)).toBe('warn');
    expect(sonarMeasureTone('security_issues', reported)).toBe('bad');
  });

  it('cautions on an issue count whose rating was never reported', () => {
    expect(sonarMeasureTone('security_issues', measures({ security_issues: 4 }))).toBe('warn');
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
    ];
    for (const measure of every) {
      expect(sonarMeasureTone(measure, nothing)).toBe('neutral');
    }
  });
});
