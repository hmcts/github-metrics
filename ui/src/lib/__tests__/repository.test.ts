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
    expect(rows[0]).toEqual({ label: '3 months', value: 'yes', detail: 'human commit: yes' });
  });

  it('keeps an unsearched human answer unknown, with the reason beside it', () => {
    expect(maintenanceRows(report)[1]?.detail).toBe(
      'human commit: unknown — the search stopped at 2026-02-01',
    );
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
    expect(card.detail).toBe(
      '.github/CODEOWNERS (240 bytes, recognised by GitHub) · docs/CODEOWNERS.md (0 bytes, not recognised by GitHub)',
    );
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
    expect(rows).toHaveLength(12);
    expect(rows[0]).toEqual({ label: 'Coverage', value: '62.1%' });
    expect(rows[1]).toEqual({ label: 'Duplicated lines', value: '-' });
  });

  it('prints a million lines of code as a million, not in exponent form', () => {
    expect(sonarRows(measures)[2]?.value).toBe('1000000');
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
    });
    expect(sonarGateCard({}).detail).toBe('not available');
  });

  it('reports a project that has never been analysed as exactly that', () => {
    expect(sonarGateCard({ measures: { project_key: 'hmcts_api' } })).toEqual({
      label: 'Quality gate',
      value: 'not reported',
      detail: 'never analysed',
    });
  });
});
