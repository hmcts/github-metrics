import { describe, expect, it } from 'vitest';
import {
  ALERT_FAMILIES,
  codeownersCard,
  conditionGroups,
  excludedDetail,
  excludedMerges,
  maintenanceRows,
  maintenanceSummary,
  mergeGateRows,
  openPullRequestCards,
  ratingLetter,
  securityCards,
  severityDetail,
  sonarGateCard,
  sonarRows,
  yesOrNo,
} from '@/lib/repository';
import type {
  CohortSummary,
  MaintenanceReport,
  MergeGateEvidence,
  ReadinessAssessment,
  SecurityAlertEvidence,
  SonarMeasures,
} from '@/lib/types';

function cohort(overrides: Partial<CohortSummary> = {}): CohortSummary {
  return { merged: 12, reported: 9, excluded_authors: {}, direct_commits: 2, ...overrides };
}

describe('yesOrNo', () => {
  it('keeps a withheld value out of the false one', () => {
    expect(yesOrNo(true)).toBe('yes');
    expect(yesOrNo(false)).toBe('no');
    expect(yesOrNo(undefined)).toBe('not disclosed');
    expect(yesOrNo(null)).toBe('not disclosed');
  });
});

describe('conditionGroups', () => {
  const assessment: ReadinessAssessment = {
    label: 'amber',
    blocking: [],
    caution: [{ condition: 'no required check', detail: 'the gate requires no status check' }],
    clear: [{ condition: 'codeowners', detail: 'a CODEOWNERS file is present' }],
  };

  it('returns all three groups so a green is as auditable as a red', () => {
    expect(conditionGroups(assessment).map((group) => group.key)).toEqual([
      'blocking',
      'caution',
      'clear',
    ]);
  });

  it('says what an empty group means rather than leaving the finding unstated', () => {
    const [blocking] = conditionGroups(assessment);
    expect(blocking?.conditions).toEqual([]);
    expect(blocking?.empty).toContain('Nothing blocked this repository');
  });

  it('carries the policy’s own conditions through untouched', () => {
    expect(conditionGroups(assessment)[1]?.conditions[0]?.detail).toBe(
      'the gate requires no status check',
    );
  });

  it('sinks the clear group’s informational rows below the graded ones', () => {
    const mixed: ReadinessAssessment = {
      ...assessment,
      clear: [
        { condition: 'sufficient-merges', detail: 'reported, not judged', informational: true },
        { condition: 'codeowners', detail: 'a CODEOWNERS file is present' },
        { condition: 'approval-coverage', detail: 'reported, not judged', informational: true },
        { condition: 'protected', detail: 'the default branch is protected', informational: false },
      ],
    };
    expect(conditionGroups(mixed)[2]?.conditions.map((condition) => condition.condition)).toEqual([
      'codeowners',
      'protected',
      'sufficient-merges',
      'approval-coverage',
    ]);
  });

  it('leaves the caution group in the policy’s order, informational rows included', () => {
    const mixed: ReadinessAssessment = {
      ...assessment,
      caution: [
        { condition: 'reported', detail: 'reported, not judged', informational: true },
        { condition: 'no required check', detail: 'the gate requires no status check' },
      ],
    };
    expect(conditionGroups(mixed)[1]?.conditions.map((condition) => condition.condition)).toEqual([
      'reported',
      'no required check',
    ]);
  });
});

describe('the cohort', () => {
  it('counts nothing excluded when no author was', () => {
    expect(excludedMerges(cohort())).toBe(0);
    expect(excludedDetail(cohort())).toBe('no author was excluded from this window');
  });

  it('sums the excluded authors and names each of them with their count', () => {
    const counted = cohort({ excluded_authors: { 'dependabot[bot]': 3, 'renovate[bot]': 1 } });
    expect(excludedMerges(counted)).toBe(4);
    expect(excludedDetail(counted)).toBe('dependabot[bot] 3 · renovate[bot] 1');
  });
});

describe('mergeGateRows', () => {
  function gate(overrides: Partial<MergeGateEvidence> = {}): MergeGateEvidence {
    return {
      branch: 'master',
      protected: true,
      pull_requests: [],
      status_checks: [],
      restricts_deletions: true,
      blocks_force_pushes: true,
      applies_to_administrators: undefined,
      rules_observed: true,
      requires_linear_history: false,
      restricts_branch_names: false,
      unmodelled_rules: [],
      ...overrides,
    };
  }

  function value(rows: ReturnType<typeof mergeGateRows>, label: string): string | undefined {
    return rows.find((row) => row.label === label)?.value;
  }

  it('takes the strictest approval count across every pull-request rule on the branch', () => {
    const rows = mergeGateRows(
      gate({
        pull_requests: [
          {
            dismiss_stale_reviews_on_push: false,
            require_code_owner_review: false,
            require_last_push_approval: false,
            required_approving_review_count: 1,
            required_review_thread_resolution: false,
          },
          {
            dismiss_stale_reviews_on_push: true,
            require_code_owner_review: true,
            require_last_push_approval: false,
            required_approving_review_count: 2,
            required_review_thread_resolution: true,
          },
        ],
      }),
    );
    expect(value(rows, 'Approving reviews required')).toBe('2');
    // Any rule dismissing stale reviews dismisses them: the strictest rule is what has to be met.
    expect(value(rows, 'Dismiss stale reviews on push')).toBe('yes');
  });

  it('gathers the required contexts from every ruleset rather than the first', () => {
    const rows = mergeGateRows(
      gate({
        status_checks: [
          { strict_required_status_checks_policy: true, required_status_checks: [{ context: 'sonar' }] },
          { strict_required_status_checks_policy: false, required_status_checks: [{ context: 'build' }] },
        ],
      }),
    );
    expect(value(rows, 'Required status checks')).toBe('build, sonar');
  });

  it('names a context two rulesets both require once, as the text report does', () => {
    const rows = mergeGateRows(
      gate({
        status_checks: [
          { strict_required_status_checks_policy: true, required_status_checks: [{ context: 'build' }] },
          {
            strict_required_status_checks_policy: false,
            required_status_checks: [{ context: 'build' }, { context: 'sonar' }],
          },
        ],
      }),
    );
    expect(value(rows, 'Required status checks')).toBe('build, sonar');
  });

  it('says none rather than nothing where a branch requires no check and no rule was unmodelled', () => {
    const rows = mergeGateRows(gate());
    expect(value(rows, 'Required status checks')).toBe('none');
    expect(value(rows, 'Rules not interpreted')).toBe('none');
    expect(value(rows, 'Approving reviews required')).toBe('0');
  });

  it('keeps an administrator answer GitHub withheld out of the false one', () => {
    expect(value(mergeGateRows(gate()), 'Applies to administrators')).toBe('not disclosed');
    expect(value(mergeGateRows(gate({ applies_to_administrators: false })), 'Applies to administrators')).toBe(
      'no',
    );
  });

  it('names the rules this build does not interpret rather than hiding them', () => {
    const rows = mergeGateRows(gate({ unmodelled_rules: ['merge_queue', 'tag_name_pattern'] }));
    expect(value(rows, 'Rules not interpreted')).toBe('merge_queue, tag_name_pattern');
  });

  function tone(rows: ReturnType<typeof mergeGateRows>, label: string): string | undefined {
    return rows.find((row) => row.label === label)?.tone;
  }

  it('reads a gate requiring no approval and no check as badly as it configures', () => {
    const rows = mergeGateRows(gate());
    expect(tone(rows, 'Protected')).toBe('good');
    expect(tone(rows, 'Rules observed')).toBe('good');
    expect(tone(rows, 'Approving reviews required')).toBe('bad');
    expect(tone(rows, 'Required status checks')).toBe('bad');
    // No pull-request rule dismisses a stale review, which is the caution `assessment.stale_reviews`
    // raises rather than a veto.
    expect(tone(rows, 'Dismiss stale reviews on push')).toBe('warn');
  });

  it('reads a gate that requires review and checks as satisfying both', () => {
    const rows = mergeGateRows(
      gate({
        pull_requests: [
          {
            dismiss_stale_reviews_on_push: true,
            require_code_owner_review: true,
            require_last_push_approval: false,
            required_approving_review_count: 1,
            required_review_thread_resolution: false,
          },
        ],
        status_checks: [
          { strict_required_status_checks_policy: true, required_status_checks: [{ context: 'build' }] },
        ],
        applies_to_administrators: true,
      }),
    );
    expect(tone(rows, 'Approving reviews required')).toBe('good');
    expect(tone(rows, 'Required status checks')).toBe('good');
    expect(tone(rows, 'Dismiss stale reviews on push')).toBe('good');
    expect(tone(rows, 'Applies to administrators')).toBe('good');
  });

  it('grades an unprotected branch red and one whose rules could not be read not at all', () => {
    const rows = mergeGateRows(gate({ protected: false, rules_observed: false }));
    expect(tone(rows, 'Protected')).toBe('bad');
    // cannot_assess, not a failure: the rules were not readable, which is not the same as absent.
    expect(tone(rows, 'Rules observed')).toBe('neutral');
  });

  it('colours no rule of a protected gate GitHub refused to disclose', () => {
    // `inventory.merge_gate_without_rule_details` for a permission-denied read: protected, with the
    // same empty rule arrays a gate carrying no rules has. The policy stops at
    // `merge-gate-rules-not-observable`, so red on these rows would report a missing Administration
    // permission as a gate requiring no review.
    const rows = mergeGateRows(gate({ protected: true, rules_observed: false }));
    expect(tone(rows, 'Protected')).toBe('good');
    expect(tone(rows, 'Rules observed')).toBe('neutral');
    expect(tone(rows, 'Approving reviews required')).toBe('neutral');
    expect(tone(rows, 'Required status checks')).toBe('neutral');
    expect(tone(rows, 'Dismiss stale reviews on push')).toBe('neutral');
    // The printed values are what `render.py` prints for the same block, uncoloured rather than
    // reworded: the page and the text report state one thing.
    expect(value(rows, 'Approving reviews required')).toBe('0');
    expect(value(rows, 'Required status checks')).toBe('none');
    expect(value(rows, 'Dismiss stale reviews on push')).toBe('no');
  });

  it('colours no field GitHub withheld and none the policy reports without judging', () => {
    const rows = mergeGateRows(gate({ requires_linear_history: true, restricts_branch_names: true }));
    // A gate field GitHub did not disclose: `applies_to_administrators` is absent on this fixture.
    expect(value(rows, 'Applies to administrators')).toBe('not disclosed');
    expect(tone(rows, 'Applies to administrators')).toBe('neutral');
    // The neutral trio from `ReadinessPolicy.neutral()`, coloured in neither state.
    expect(tone(rows, 'Restricts deletions')).toBe('neutral');
    expect(tone(rows, 'Requires linear history')).toBe('neutral');
    expect(tone(rows, 'Restricts branch names')).toBe('neutral');
    // Facts about what was looked at rather than answers about it.
    expect(tone(rows, 'Branch')).toBe('neutral');
    expect(tone(rows, 'Rules not interpreted')).toBe('neutral');
    // Cautioned by `assessment.force_pushes`, unnamed by the tone table, so neutral by its default.
    expect(tone(rows, 'Blocks force pushes')).toBe('neutral');
  });
});

describe('openPullRequestCards', () => {
  const summary = {
    opened_in_window: 7,
    closed_without_merge: 2,
    currently_open: 4,
    stale_open: 1,
  };

  it('has no cards to draw when the state was never collected', () => {
    expect(openPullRequestCards({ detail: 'not collected' })).toEqual([]);
  });

  it('dates the two windowed counts by the window they were measured over', () => {
    const cards = openPullRequestCards({
      summary,
      fetched_at: '2026-08-30T09:15:00Z',
      starts_at: '2026-08-01T00:00:00Z',
      ends_at: '2026-08-29T00:00:00Z',
    });
    expect(cards.map((card) => card.value)).toEqual(['7', '2', '4', '1']);
    expect(cards[0]?.detail).toBe('2026-08-01T00:00Z to 2026-08-29T00:00Z');
    expect(cards[1]?.detail).toBe('2026-08-01T00:00Z to 2026-08-29T00:00Z');
  });

  it('dates the two standing counts by when the queue was read', () => {
    const cards = openPullRequestCards({ summary, fetched_at: '2026-08-30T09:15:00Z' });
    expect(cards[2]?.detail).toBe('as at 2026-08-30T09:15Z');
    expect(cards[3]?.detail).toBe('as at 2026-08-30T09:15Z');
  });

  it('claims no period at all where the block carries none', () => {
    const cards = openPullRequestCards({ summary });
    expect(cards.every((card) => card.detail === undefined)).toBe(true);
  });

  it('colours the one count that ages and leaves throughput uncoloured', () => {
    expect(openPullRequestCards({ summary }).map((card) => card.tone)).toEqual([
      'neutral',
      'neutral',
      'neutral',
      'warn',
    ]);
    expect(
      openPullRequestCards({ summary: { ...summary, stale_open: 0 } })[3]?.tone,
    ).toBe('good');
  });
});

describe('security alerts', () => {
  const alerts: SecurityAlertEvidence = {
    dependabot: { open: 5, by_severity: { critical: 1, high: 2 } },
    code_scanning: { by_severity: {}, detail: 'code scanning is disabled for this repository' },
    secret_scanning: { open: 0, by_severity: {} },
  };

  it('lists the three families in the order every rendering of this block uses', () => {
    expect(securityCards(alerts).map((card) => card.label)).toEqual([...ALERT_FAMILIES]);
  });

  it('counts an asserted severity and zero for one GitHub asserted nothing about', () => {
    expect(severityDetail('dependabot', alerts.dependabot)).toBe(
      'critical 1 · high 2 · medium 0 · low 0',
    );
  });

  it('says a refused family is unreadable rather than reporting it clean', () => {
    const [, codeScanning] = securityCards(alerts);
    expect(codeScanning?.value).toBe('-');
    expect(codeScanning?.detail).toBe('code scanning is disabled for this repository');
  });

  it('falls back to a plain refusal where the family gave no reason', () => {
    expect(severityDetail('dependabot', { by_severity: {} })).toBe('not available');
  });

  it('takes a family’s colour from its severities, and refuses one for a family nobody read', () => {
    const [dependabot, codeScanning, secretScanning] = securityCards(alerts);
    expect(dependabot?.tone).toBe('bad');
    // The unreadable case: a refused family is not a clean one, so it is not coloured as one.
    expect(codeScanning?.tone).toBe('neutral');
    expect(secretScanning?.tone).toBe('good');
  });

  it('weighs a family holding only medium and low alerts rather than condemning it', () => {
    const cards = securityCards({
      ...alerts,
      dependabot: { open: 3, by_severity: { medium: 1, low: 2 } },
      secret_scanning: { open: 1, by_severity: {} },
    });
    expect(cards[0]?.tone).toBe('warn');
    // A leaked credential has no low-severity form, and secret scanning reports no severity at all.
    expect(cards[2]?.tone).toBe('bad');
  });

  it('says secret scanning carries no severity instead of printing four zeros', () => {
    expect(severityDetail('secret-scanning', alerts.secret_scanning)).toBe(
      'no severity is reported for this family',
    );
  });
});

describe('maintenance', () => {
  const report: MaintenanceReport = {
    fetched_at: '2026-08-30T09:15:00Z',
    maintenance: {
      branch: 'main',
      last_commit_at: '2026-08-28T11:00:00Z',
      last_human_commit_at: '2026-08-20T08:00:00Z',
      searched_back_to: '2026-02-01T00:00:00Z',
    },
    windows: [
      { months: 3, committed_within: true, human_committed_within: true },
      { months: 12, committed_within: true, human_detail: 'the search stopped at 2026-02-01' },
    ],
  };

  it('answers each window with its own two answers', () => {
    const rows = maintenanceRows(report);
    expect(rows[0]).toEqual({
      label: '3 months',
      value: 'yes',
      tone: 'good',
      detail: 'human commit: yes',
    });
  });

  it('keeps an unsearched human answer unknown, with the reason beside it', () => {
    expect(maintenanceRows(report)[1]?.detail).toBe(
      'human commit: unknown — the search stopped at 2026-02-01',
    );
  });

  it('weighs a window with no commit in it rather than condemning the repository', () => {
    const rows = maintenanceRows({
      ...report,
      windows: [{ months: 3, committed_within: false }, { months: 12, committed_within: true }],
    });
    expect(rows.map((row) => row.tone)).toEqual(['warn', 'good']);
  });

  it('states the instants the window answers were derived from', () => {
    expect(maintenanceSummary(report)).toBe(
      'branch main · last commit 2026-08-28T11:00Z · last human commit 2026-08-20T08:00Z · searched back to 2026-02-01T00:00Z',
    );
  });

  it('distinguishes a branch with no commits from a human one nobody found', () => {
    // The instants are OMITTED, not null: `response_model_exclude_none` drops an unobserved one
    // through the nested model, so this is the shape the service actually sends.
    expect(
      maintenanceSummary({ ...report, maintenance: { branch: 'main' } }),
    ).toBe('branch main · last commit none: the branch has no commits · last human commit none found');
  });

  it('states no search bound where the search found a human commit, the common shape', () => {
    // The whole point of the bound: it is recorded only when nothing was found, so the key is
    // absent here and printing `searched back to -` would invent an examined range.
    expect(
      maintenanceSummary({
        ...report,
        maintenance: {
          branch: 'main',
          last_commit_at: '2026-08-28T11:00:00Z',
          last_human_commit_at: '2026-08-20T08:00:00Z',
        },
      }),
    ).toBe('branch main · last commit 2026-08-28T11:00Z · last human commit 2026-08-20T08:00Z');
  });

  it('gives the reason where the block could not be read at all', () => {
    expect(maintenanceSummary({ windows: [], detail: 'the branch was not readable' })).toBe(
      'the branch was not readable',
    );
    expect(maintenanceSummary({ windows: [] })).toBe('not available');
  });
});

describe('codeownersCard', () => {
  it('keeps found-and-empty apart from absent and from unreadable', () => {
    expect(codeownersCard({ codeowners: { files: [] } }).value).toBe('absent');
    expect(codeownersCard({ detail: 'the contents endpoint was refused' })).toEqual({
      label: 'CODEOWNERS',
      value: '-',
      detail: 'the contents endpoint was refused',
      tone: 'neutral',
    });
    expect(codeownersCard({}).detail).toBe('not available');
  });

  it('names each file with its size and whether GitHub reads it', () => {
    const card = codeownersCard({
      codeowners: {
        files: [
          { path: '.github/CODEOWNERS', size_bytes: 240, recognised_by_github: true },
          { path: 'docs/CODEOWNERS.md', size_bytes: 0, recognised_by_github: false },
        ],
      },
    });
    expect(card.value).toBe('2 files');
    expect(card.tone).toBe('good');
    expect(card.detail).toBe(
      '.github/CODEOWNERS (240 bytes, recognised by GitHub) · docs/CODEOWNERS.md (0 bytes, not recognised by GitHub)',
    );
  });

  it('weighs a repository with no CODEOWNERS and grades one nobody could look in not at all', () => {
    expect(codeownersCard({ codeowners: { files: [] } }).tone).toBe('warn');
    expect(codeownersCard({ detail: 'the contents endpoint was refused' }).tone).toBe('neutral');
  });
});

describe('SonarCloud', () => {
  const measures: SonarMeasures = {
    project_key: 'hmcts_api',
    analysis_at: '2026-08-29T04:00:00Z',
    gate: { level: 'ERROR', conditions: [] },
    coverage: 62.1,
    lines_of_code: 1000000,
    reliability_rating: { value: 1 },
    security_rating: { value: 2.5 },
  };

  it('reads a rating as the letter every human reads it as', () => {
    expect(ratingLetter({ value: 1 })).toBe('A');
    expect(ratingLetter({ value: 5 })).toBe('E');
  });

  it('never guesses a letter for a value off the scale, or for one that never came', () => {
    expect(ratingLetter({ value: 2.5 })).toBe('unknown (2.5)');
    expect(ratingLetter({ value: 6 })).toBe('unknown (6)');
    expect(ratingLetter(undefined)).toBe('-');
  });

  it('draws every measure, leaving an unreported one visibly absent', () => {
    const rows = sonarRows(measures);
    expect(rows).toHaveLength(10);
    expect(rows[0]).toEqual({ label: 'Coverage', value: '62.1%', tone: 'bad' });
    expect(rows[1]).toEqual({ label: 'Duplicated lines', value: '-', tone: 'neutral' });
    // Retired on 2026-09-04 with the metrics Sonar deprecated: neither card is drawn any more.
    expect(rows.map((row) => row.label)).not.toContain('Security hotspots');
    expect(rows.map((row) => row.label)).not.toContain('Security review rating');
  });

  it('prints a million lines of code as a million, not in exponent form', () => {
    expect(sonarRows(measures)[2]?.value).toBe('1000000');
  });

  it('carries a tone on every measure and every rating', () => {
    const rows = sonarRows(measures);
    const tone = (label: string) => rows.find((row) => row.label === label)?.tone;
    expect(tone('Coverage')).toBe('bad');
    expect(tone('Reliability rating')).toBe('good');
    // The size of the project: the denominator for the rest, and neither good nor bad.
    expect(tone('Lines of code')).toBe('neutral');
    // Absent measures stay uncoloured — a measure nobody reported is not a measure that passed.
    expect(tone('Duplicated lines')).toBe('neutral');
    expect(tone('Violations')).toBe('neutral');
    expect(tone('Maintainability rating')).toBe('neutral');
    // Off the A-to-E scale, so no letter and no colour rather than a guess at either end.
    expect(tone('Security rating')).toBe('neutral');
  });

  it('takes an issue count’s severity from the rating that covers it', () => {
    const rows = sonarRows({
      project_key: 'hmcts_api',
      coverage: 92.4,
      duplicated_lines_density: 4.2,
      violations: 5,
      reliability_issues: 71,
      maintainability_issues: 1358,
      security_issues: 0,
      reliability_rating: { value: 4 },
      maintainability_rating: { value: 1 },
      security_rating: { value: 1 },
    });
    const tone = (label: string) => rows.find((row) => row.label === label)?.tone;
    expect(tone('Coverage')).toBe('good');
    expect(tone('Duplicated lines')).toBe('warn');
    // 71 issues under a D reads badly; 1,358 under an A is worth weighing and no worse.
    expect(tone('Reliability issues')).toBe('bad');
    expect(tone('Maintainability issues')).toBe('warn');
    expect(tone('Security issues')).toBe('good');
    // The one count with no rating of its own: above zero it is amber and never red.
    expect(tone('Violations')).toBe('warn');
    expect(tone('Reliability rating')).toBe('bad');
  });

  it('states the gate beside the project the verdict is about', () => {
    expect(
      sonarGateCard({
        measures,
        mapping: { project_key: 'hmcts_api', repository: 'hmcts/api', method: 'configured' },
      }),
    ).toEqual({
      label: 'Quality gate',
      value: 'ERROR',
      detail: 'project hmcts_api · analysed 2026-08-29T04:00Z',
      tone: 'bad',
    });
  });

  it('names a project whose measures could not be read, above the reason', () => {
    expect(
      sonarGateCard({
        mapping: { project_key: 'hmcts_api', repository: 'hmcts/api', method: 'stored-map' },
        detail: 'the measures endpoint was refused',
      }).detail,
    ).toBe('project hmcts_api · the measures endpoint was refused');
  });

  it('says a repository no project is mapped to has no gate rather than a failing one', () => {
    expect(sonarGateCard({ detail: 'no SonarCloud project is mapped to this repository' })).toEqual({
      label: 'Quality gate',
      value: '-',
      detail: 'no SonarCloud project is mapped to this repository',
      tone: 'neutral',
    });
    expect(sonarGateCard({}).detail).toBe('not available');
  });

  it('colours a passing gate green and a project with no gate configured not at all', () => {
    expect(
      sonarGateCard({ measures: { ...measures, gate: { level: 'OK', conditions: [] } } }).tone,
    ).toBe('good');
    // NONE is Sonar's word for a project with no gate conditions, which is an absence not a pass.
    expect(
      sonarGateCard({ measures: { ...measures, gate: { level: 'NONE', conditions: [] } } }).tone,
    ).toBe('neutral');
  });

  it('reports a project that has never been analysed as exactly that', () => {
    expect(sonarGateCard({ measures: { project_key: 'hmcts_api' } })).toEqual({
      label: 'Quality gate',
      value: 'not reported',
      detail: 'never analysed',
      tone: 'neutral',
    });
  });
});
