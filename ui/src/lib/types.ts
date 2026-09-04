/**
 * TypeScript mirror of the shapes `metrics.service` emits.
 *
 * Field names are snake_case because the service emits the Python models verbatim; renaming them
 * here would put a translation layer between the JSON a reader can curl and the page rendering it.
 * Every route is registered `response_model_exclude_none`, so a field the service could not observe
 * is ABSENT rather than null — hence `?:` rather than `| null` throughout. The distinction matters:
 * an absent count means nobody measured it, and a zero means somebody measured nothing.
 *
 * Instants arrive as ISO-8601 strings, typed as `string` rather than parsed into `Date` at the
 * boundary, so the value a component holds is the value the service sent.
 */

export type ReadinessLabel = 'green' | 'amber' | 'red' | 'cannot_assess';

export type ObservationStatus = 'observed' | 'not_applicable';

export type AlertSeverity = 'critical' | 'high' | 'medium' | 'low';

export type FindingSeverity = 'high' | 'medium' | 'low';

export type SonarGateLevel = 'OK' | 'ERROR' | 'NONE';

/**
 * Where a window's unreviewed substantial merging sat against the allowance it was judged by.
 *
 * `domain.UnreviewedSubstantialOutcome` verbatim. `within` is neither a pass nor a failure: it is the
 * allowance forgiving what it was configured to forgive, which is a different fact from nothing
 * having merged unreviewed at all — so the three words are three answers, not a scale of two.
 */
export type UnreviewedSubstantialOutcome = 'none' | 'within' | 'above';

export interface RateObservation {
  status: ObservationStatus;
  numerator: number;
  denominator: number;
}

/** Optional rather than nullable, for the `exclude_none` reason `MaintenanceEvidence` records. */
export interface DistributionObservation {
  status: ObservationStatus;
  sample_size: number;
  unit: string;
  median?: number;
  percentile_75?: number;
  percentile_90?: number;
}

/** A rate carries no `unit`, which is what tells the two observation shapes apart at runtime. */
export type Observation = RateObservation | DistributionObservation;

export interface BehaviourMetricSummary {
  metric: string;
  summary: Observation;
  classifications: Record<string, number>;
}

/**
 * `informational` marks a condition the policy reported without judging — the three neutral
 * merge-gate rules and a sufficient cohort. The assessment is recomputed on every build rather than
 * stored, so a service of this version always sends the boolean it holds, `false` for a graded
 * condition rather than omitted. It is optional because the field only exists from 2026-09-02: a
 * service deployed before the UI sends no key at all, which every read guards on `== null`.
 */
export interface ReadinessCondition {
  condition: string;
  label?: ReadinessLabel;
  detail: string;
  informational?: boolean;
}

export interface ReadinessAssessment {
  label: ReadinessLabel;
  blocking: ReadinessCondition[];
  caution: ReadinessCondition[];
  clear: ReadinessCondition[];
}

export interface WindowProvenance {
  offline: boolean;
  intervals_fetched: number;
}

export interface CohortSummary {
  merged: number;
  reported: number;
  excluded_authors: Record<string, number>;
  direct_commits: number;
}

export interface PullRequestRule {
  dismiss_stale_reviews_on_push: boolean;
  require_code_owner_review: boolean;
  require_last_push_approval: boolean;
  required_approving_review_count: number;
  required_review_thread_resolution: boolean;
}

export interface StatusCheck {
  context: string;
  integration_id?: number;
}

export interface StatusChecksRule {
  strict_required_status_checks_policy: boolean;
  required_status_checks: StatusCheck[];
}

export interface MergeGateEvidence {
  branch: string;
  protected: boolean;
  pull_requests: PullRequestRule[];
  status_checks: StatusChecksRule[];
  restricts_deletions: boolean;
  blocks_force_pushes: boolean;
  applies_to_administrators?: boolean;
  rules_observed: boolean;
  requires_linear_history: boolean;
  restricts_branch_names: boolean;
  unmodelled_rules: string[];
}

export interface MergeGateReport {
  fetched_at?: string;
  gate?: MergeGateEvidence;
  detail?: string;
}

export interface OpenPullRequestSummary {
  opened_in_window: number;
  closed_without_merge: number;
  currently_open: number;
  stale_open: number;
}

export interface OpenPullRequestReport {
  fetched_at?: string;
  starts_at?: string;
  ends_at?: string;
  summary?: OpenPullRequestSummary;
  detail?: string;
}

export interface OpenAlertCount {
  open?: number;
  by_severity: Partial<Record<AlertSeverity, number>>;
  detail?: string;
}

export interface SecurityAlertEvidence {
  dependabot: OpenAlertCount;
  code_scanning: OpenAlertCount;
  secret_scanning: OpenAlertCount;
}

export interface SecurityAlertReport {
  fetched_at?: string;
  alerts?: SecurityAlertEvidence;
  detail?: string;
}

export interface CodeownersFile {
  path: string;
  size_bytes: number;
  recognised_by_github: boolean;
}

export interface CodeownersReport {
  fetched_at?: string;
  codeowners?: { files: CodeownersFile[] };
  detail?: string;
}

/**
 * OPTIONAL, NOT NULLABLE. Every route is registered `response_model_exclude_none`, and pydantic
 * applies it through nested models too, so an unobserved instant arrives as a MISSING KEY rather
 * than as `null` — `searched_back_to` is absent on the common path, where a human commit was found.
 */
export interface MaintenanceEvidence {
  branch: string;
  last_commit_at?: string;
  last_human_commit_at?: string;
  searched_back_to?: string;
}

export interface MaintenanceWindowStatus {
  months: number;
  committed_within: boolean;
  human_committed_within?: boolean;
  human_detail?: string;
}

export interface MaintenanceReport {
  fetched_at?: string;
  maintenance?: MaintenanceEvidence;
  windows: MaintenanceWindowStatus[];
  detail?: string;
}

export interface SonarRating {
  value: number;
}

export interface SonarQualityGateCondition {
  metric: string;
  comparator: string;
  threshold?: string;
  actual?: string;
  level: SonarGateLevel;
}

export interface SonarQualityGate {
  level: SonarGateLevel;
  conditions: SonarQualityGateCondition[];
}

export interface SonarProjectMapping {
  project_key: string;
  repository: string;
  method: string;
  analysis_at?: string;
  revision?: string;
}

export interface SonarMeasures {
  project_key: string;
  analysis_at?: string;
  gate?: SonarQualityGate;
  coverage?: number;
  duplicated_lines_density?: number;
  lines_of_code?: number;
  violations?: number;
  reliability_issues?: number;
  maintainability_issues?: number;
  security_issues?: number;
  reliability_rating?: SonarRating;
  maintainability_rating?: SonarRating;
  security_rating?: SonarRating;
}

export interface SonarReport {
  fetched_at?: string;
  mapping?: SonarProjectMapping;
  measures?: SonarMeasures;
  detail?: string;
}

export interface PracticePullRequestReference {
  number: number;
  url: string;
  merged_at: string;
  size_class: string;
  changed_lines?: number;
  changed_files?: number;
}

export interface PracticeCommitReference {
  sha: string;
  url: string;
  committed_at: string;
  size_class: string;
  changed_lines?: number;
  changed_files?: number;
}

export interface PracticeFinding {
  rule: string;
  severity: FindingSeverity;
  actor_login: string;
  occurrences: number;
  authored_merges: number;
  percentage: number;
  message: string;
  occurrences_by_size: Record<string, number>;
  pull_requests: PracticePullRequestReference[];
  direct_commits?: PracticeCommitReference[];
}

export interface RepositoryPracticeEvidence {
  repository: string;
  team: string;
  starts_at: string;
  ends_at: string;
  provenance: WindowProvenance;
  cohort: CohortSummary;
  assessment?: ReadinessAssessment;
  unreviewed_substantial?: UnreviewedSubstantialOutcome;
  merge_gate: MergeGateReport;
  open_pull_requests: OpenPullRequestReport;
  security: SecurityAlertReport;
  codeowners: CodeownersReport;
  maintenance: MaintenanceReport;
  sonar: SonarReport;
  metrics: BehaviourMetricSummary[];
  behaviour: PracticeFinding[];
}

export interface ActorRepositoryReadiness {
  readiness?: ReadinessLabel;
  repository: string;
  contributions: number;
  blocking: number;
  metrics: BehaviourMetricSummary[];
}

export interface ActorReadiness {
  actor_login: string;
  repositories: ActorRepositoryReadiness[];
}

/** The single percentile a distribution is compared at, named as the field it reads. */
export type Percentile = 'median' | 'percentile_75';

/** What arithmetic a delta reports: a rate moves in points, a count and a percentile in per cent. */
export type DeltaBasis = 'percentage_points' | 'percentage_change';

export type AlertFamily = 'dependabot' | 'code-scanning' | 'secret-scanning';

export interface TrendThroughput {
  merges: number;
  merged_pull_requests: number;
  direct_commits: number;
  active_contributors: number;
}

export interface TrendMetric {
  metric: string;
  summary: Observation;
  /** Absent exactly where the window observed no eligible sample — never a zero standing in for one. */
  value?: number;
  percentile?: Percentile;
}

export interface TrendDelta {
  measure: string;
  basis: DeltaBasis;
  baseline: number;
  period: number;
  change?: number;
  unit?: string;
  percentile?: Percentile;
  detail?: string;
}

export interface TrendWindow {
  starts_at: string;
  ends_at: string;
  provenance?: WindowProvenance;
  cohort?: CohortSummary;
  throughput?: TrendThroughput;
  metrics: TrendMetric[];
  detail?: string;
}

export interface TrendPeriod extends TrendWindow {
  index: number;
  deltas: TrendDelta[];
}

export interface AlertObservation {
  family: AlertFamily;
  fetched_at: string;
  open: number;
  by_severity: Partial<Record<AlertSeverity, number>>;
}

export interface RepositoryTrend {
  repository: string;
  enablement_at?: string;
  baseline?: TrendWindow;
  periods: TrendPeriod[];
  alert_observations: AlertObservation[];
  detail?: string;
  delta_detail?: string;
}

export interface ServiceHealth {
  status: string;
  organization: string;
}

/**
 * The spans on offer, and the collection every one of them is anchored to.
 *
 * The collection state rides on this route because every page already fetches it for the span
 * selector, and the notice that the figures sit at an old collection belongs on every page rather
 * than on the overview alone. `collected_through` is ABSENT when nothing has been collected under
 * the current query signature — the one stale case with no instant to name.
 */
export interface WindowOptions {
  options: number[];
  default: number;
  /** The most periods one trend request may ask for, which a series must be cut to. */
  trend_periods: number;
  collected_through?: string;
  collection_stale: boolean;
}

export interface OverviewSummary {
  organization: string;
  weeks: number;
  starts_at: string;
  ends_at: string;
  built_at: string;
  /** The instant the caches cover to, which the window is anchored at; absent when nothing was collected. */
  collected_through?: string;
  repositories: number;
  unavailable: number;
  teams: number;
  actors: number;
  merged_pull_requests: number;
  direct_commits: number;
  labels: Record<string, number>;
}

/**
 * One repository in a list, whether this window could be reported for it or not.
 *
 * Everything after `finding_occurrences` says what the row's own counts cannot, so `/repositories`
 * can distribute the estate and answer for its governance without loading every evidence block. Each
 * is UNMEASURED WHEN ABSENT, as every count above it is: the two gate figures where there is no gate
 * to read or its rules were withheld, `unreviewed_substantial` where the policy graded nothing,
 * `sonar_coverage` and the two Sonar security measures where no SonarCloud project resolved, its
 * measures could not be read, or it sent no such metric, `codeowners_files` where nobody could read
 * the repository's contents, and `security` where the whole alert block carries a reason instead of
 * alerts. All of them are absent besides on a repository the window could not be reported for at all,
 * the row that carries `detail`. None of them is zero by default — an unprotected default branch is
 * the one thing that reads as a real `0`, because the gate was read and it requires nothing.
 *
 * `sonar_reported` is the one exception, and is `false` RATHER THAN ABSENT on a reportable repository
 * whose measures could not be read or whose project never resolved: the column it feeds answers "is
 * there Sonar information here", so "no Sonar" and "no report" have to stay apart.
 *
 * `security` carries `SecurityAlertEvidence` verbatim rather than flattened into scalars, because the
 * per-family `open`/`by_severity`/`detail` is what `securityBand` needs — a family with nothing open
 * and one GitHub refused are different answers, and only the block itself keeps them apart.
 */
export interface RepositoryRow {
  repository: string;
  team: string;
  readiness?: ReadinessLabel;
  merged_pull_requests?: number;
  direct_commits?: number;
  currently_open?: number;
  stale_open?: number;
  finding_occurrences?: number;
  required_approving_reviews?: number;
  required_status_checks?: number;
  unreviewed_substantial?: UnreviewedSubstantialOutcome;
  sonar_coverage?: number;
  codeowners_files?: number;
  sonar_reported?: boolean;
  security?: SecurityAlertEvidence;
  sonar_security_rating?: SonarRating;
  sonar_security_issues?: number;
  detail?: string;
  /**
   * Whether the organisation's production approvals list holds this repository.
   *
   * The one field here that is not read from the window at all, and it follows the same rule as
   * every count above it: ABSENT MEANS NO LIST COULD BE READ, and `false` means the list was read
   * and does not name this repository. Both render no badge — there is no non-production badge —
   * but the filter and its count can only be honest about the difference if the field keeps it.
   */
  production?: boolean;
}

/**
 * `metrics` is this person's own summaries for this repository, sent verbatim by the service.
 *
 * The columns the table shows are subtracted out of it by `lib/contributor.ts`: the service derives
 * nothing from these, so the page and the JSON a reader can curl carry the same figures.
 */
export interface ContributorRow {
  login: string;
  contributions: number;
  blocking: number;
  metrics: BehaviourMetricSummary[];
}

export interface RepositoryDetail {
  repository: string;
  team: string;
  evidence?: RepositoryPracticeEvidence;
  contributors: ContributorRow[];
  detail?: string;
  /**
   * Whether this repository deploys to production, under `RepositoryRow.production`'s rule.
   *
   * The service answers it on BOTH branches, the unavailable one included: whether a repository
   * deploys to production is not a fact about the reporting window, so a span with no evidence for
   * it still knows this. The header is built before the no-evidence branch for that reason.
   */
  production?: boolean;
}

/**
 * `labels` LISTS the distinct labels this person's reported repositories carry, best first.
 *
 * It combines nothing: there is no per-person label, no score and no count beside a name. The
 * service sends `[]` where nothing is left to label — `cannot_assess` repositories are excluded, as
 * they are from the text report's actor section, and a readiness policy that is off labels nothing.
 *
 * OPTIONAL for the reason `ReadinessCondition.informational` is: the field only exists from
 * 2026-09-02, and `API_URL` is read per request so a deployment can be pointed at a `metrics-serve`
 * older than that without a rebuild. A service of this version always sends the key, empty rather
 * than omitted, so the absent case means an older service and nothing else — but an unguarded read
 * of it would throw while rendering and take the whole `/contributors` page down, where the guard
 * shows the list unlabelled.
 */
export interface ActorRow {
  login: string;
  repositories: number;
  labels?: ReadinessLabel[];
}

export interface ActorDetail {
  actor: ActorReadiness;
  teams: Record<string, string>;
  /**
   * Which of THIS PERSON'S repositories deploy to production, and nothing else.
   *
   * It sits beside `teams` for `teams`' own reason: it is accounting the contract's `ActorReadiness`
   * cannot state, and the contract model is passed through unchanged. ABSENT MEANS NO LIST COULD BE
   * READ, where an empty list means the list was read and none of this person's repositories is on
   * it — the distinction `RepositoryRow.production` keeps, in a list's shape.
   */
  production?: string[];
}

export interface TeamActorRow {
  login: string;
  repositories: number;
  contributions: number;
}

export interface TeamRow {
  team: string;
  repositories: number;
  unavailable: number;
  actors: number;
  labels: Record<string, number>;
}

export interface TeamDetail {
  team: string;
  repositories: RepositoryRow[];
  actors: TeamActorRow[];
  unavailable: number;
  labels: Record<string, number>;
}
