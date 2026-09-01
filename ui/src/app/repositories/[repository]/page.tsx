import { cookies } from 'next/headers';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import { AssessmentSection } from '@/components/AssessmentSection';
import { ContributorsTable } from '@/components/ContributorsTable';
import { EmptyState } from '@/components/EmptyState';
import { EntityHeader } from '@/components/EntityHeader';
import { FindingsTable } from '@/components/FindingsTable';
import { MetricCard } from '@/components/MetricCard';
import { MetricsGrid } from '@/components/MetricsGrid';
import { NavWeekSelector } from '@/components/NavWeekSelector';
import { Section } from '@/components/Section';
import { TrendSection } from '@/components/TrendSection';
import { getRepository, getTrend, getWindows, isNotFound } from '@/lib/api';
import { count, instant, span } from '@/lib/format';
import { hasPeriods } from '@/lib/trend';
import {
  codeownersCard,
  excludedDetail,
  excludedMerges,
  maintenanceRows,
  maintenanceSummary,
  mergeGateRows,
  openPullRequestCards,
  securityCards,
  sonarGateCard,
  sonarRows,
  type LabelledValue,
} from '@/lib/repository';
import type { RepositoryDetail, SonarReport } from '@/lib/types';
import { WEEKS_COOKIE, resolveWeeks, withWeeks, type SearchValue } from '@/lib/weeks';

/**
 * One repository's whole evidence block at one window span.
 *
 * The page is the block, in the block's own order: what the readiness policy decided and on what
 * evidence, then the cohort it was decided over, then each collected signal, then the behaviour
 * metrics, the findings, and who authored the window. Nothing is recomputed here — every figure is
 * one the service sent, formatted by `lib/repository.ts` — so the page and `metrics evidence` for
 * the same span are one claim rather than two that can drift.
 *
 * A repository the span cannot be reported for keeps its page and says why. It is a configured
 * repository either way, and a 404 for one would read as a repository nobody has heard of.
 */
export const dynamic = 'force-dynamic';

export default async function RepositoryPage({
  params,
  searchParams,
}: {
  params: { repository: string };
  searchParams?: { weeks?: SearchValue };
}) {
  const windows = await getWindows();
  const weeks = resolveWeeks(
    searchParams?.weeks,
    cookies().get(WEEKS_COOKIE)?.value,
    windows.options,
    windows.default,
  );
  const detail = await readRepository(params.repository, weeks);
  const evidence = detail.evidence;

  const header = (
    <EntityHeader
      kind="repository"
      name={detail.repository}
      label={evidence?.assessment?.label}
      action={<NavWeekSelector options={windows.options} active={weeks} />}
      context={
        <>
          <Link
            href={withWeeks(`/teams/${encodeURIComponent(detail.team)}`, weeks)}
            className="text-indigo-400 hover:text-indigo-300"
          >
            {detail.team}
          </Link>
          {evidence ? <span>{span(evidence.starts_at, evidence.ends_at)}</span> : null}
          {evidence ? (
            <span className="text-slate-500">
              {count(evidence.provenance.intervals_fetched, 'interval', 'intervals')} fetched
            </span>
          ) : null}
        </>
      }
    />
  );

  if (evidence === undefined) {
    return (
      <div className="space-y-8">
        {header}
        <EmptyState
          message={`This span holds no evidence for ${detail.repository}.`}
          detail={`${detail.detail ?? 'no reason was given'} — run metrics collect for the span being asked for, or read the repository at a span the caches cover.`}
        />
      </div>
    );
  }

  const findings = evidence.behaviour;
  const alerts = evidence.security.alerts;
  const openPullRequests = openPullRequestCards(evidence.open_pull_requests);
  // Read after the unavailable branch above: a series is cut from the caches per period, which is
  // work worth doing only for a page that is going to draw the rest of the block too.
  //
  // Cut to the count the service publishes, so a long-enabled repository is served a bounded series
  // rather than refused one. The section says when a series sits at that cut, because the periods it
  // holds then are the first ones since enablement and not the whole history.
  //
  // The ONLY fetch on this page that is allowed to fail quietly. The trend is one section of a page
  // whose evidence has already arrived, and the section already renders only when the endpoint
  // returns periods — an observation history that could not be read should cost the reader that
  // section and not the whole page.
  const series = await getTrend(detail.repository, windows.trend_periods).catch(() => null);

  return (
    <div className="space-y-8">
      {header}

      <ValueCards
        values={[
          {
            label: 'Merges reported',
            value: String(evidence.cohort.reported),
            detail: `${evidence.cohort.merged} merged in the span`,
          },
          {
            label: 'Merges excluded',
            value: String(excludedMerges(evidence.cohort)),
            detail: excludedDetail(evidence.cohort),
          },
          {
            label: 'Direct commits',
            value: String(evidence.cohort.direct_commits),
            detail: 'landed on the default branch without a pull request',
          },
          codeownersCard(evidence.codeowners),
        ]}
      />

      {evidence.assessment ? (
        <AssessmentSection assessment={evidence.assessment} />
      ) : (
        <Section heading="Readiness">
          <EmptyState
            message="The readiness policy graded nothing for this repository at this span."
            detail="A repository with no eligible merges is not graded, which is not the same as being graded and passing."
          />
        </Section>
      )}

      <Section heading="Merge gate" detail={read(evidence.merge_gate.fetched_at)}>
        {evidence.merge_gate.gate === undefined ? (
          <EmptyState
            message="The merge gate could not be read for this repository."
            detail={evidence.merge_gate.detail}
          />
        ) : (
          <ValueCards values={mergeGateRows(evidence.merge_gate.gate)} />
        )}
      </Section>

      <Section heading="Open pull requests" detail={read(evidence.open_pull_requests.fetched_at)}>
        {openPullRequests.length === 0 ? (
          <EmptyState
            message="Open pull-request state was not collected for this repository."
            detail={evidence.open_pull_requests.detail}
          />
        ) : (
          <ValueCards values={openPullRequests} />
        )}
      </Section>

      <Section heading="Security alerts" detail={read(evidence.security.fetched_at)}>
        {alerts === undefined ? (
          <EmptyState
            message="No security alert family could be read for this repository."
            detail={evidence.security.detail}
          />
        ) : (
          <ValueCards values={securityCards(alerts)} />
        )}
      </Section>

      <Section heading="Maintenance" detail={maintenanceSummary(evidence.maintenance)}>
        {evidence.maintenance.windows.length === 0 ? (
          <EmptyState message="No maintenance window was checked for this repository." />
        ) : (
          <ValueCards values={maintenanceRows(evidence.maintenance)} />
        )}
      </Section>

      <Section heading="SonarCloud" detail={read(evidence.sonar.fetched_at)}>
        <ValueCards values={[sonarGateCard(evidence.sonar), ...sonarMeasures(evidence.sonar)]} />
      </Section>

      <Section heading="Behaviour" detail={span(evidence.starts_at, evidence.ends_at)}>
        <MetricsGrid
          summaries={evidence.metrics}
          empty="No behaviour metric was computed for this repository at this span."
        />
      </Section>

      {hasPeriods(series) ? <TrendSection series={series} cut={windows.trend_periods} /> : null}

      <Section heading="Findings" detail="in the report’s own order, never ranked by person">
        {findings.length === 0 ? (
          <EmptyState message="No practice rule fired on this repository at this span." />
        ) : (
          <FindingsTable findings={findings} weeks={weeks} />
        )}
      </Section>

      <Section heading="Contributors" detail="weightiest share of this span first">
        {detail.contributors.length === 0 ? (
          <EmptyState message="Nobody authored a reported merge in this repository at this span." />
        ) : (
          <ContributorsTable rows={detail.contributors} weeks={weeks} />
        )}
      </Section>
    </div>
  );
}

/**
 * Read one repository, answering not-found for a name the configuration does not hold.
 *
 * Only a 404 becomes a not-found page: every other refusal is a fault in the service or the network
 * and is left to surface as one, rather than being disguised as a repository that does not exist.
 */
async function readRepository(repository: string, weeks: number): Promise<RepositoryDetail> {
  try {
    return await getRepository(repository, weeks);
  } catch (error) {
    if (isNotFound(error)) {
      notFound();
    }
    throw error;
  }
}

/** When a stored block was read, for a section heading, or nothing where nothing was collected. */
function read(fetched: string | undefined): string | undefined {
  return fetched === undefined ? undefined : `read ${instant(fetched)}`;
}

/** The measures behind a Sonar gate, or no cards at all where the project reported none. */
function sonarMeasures(report: SonarReport): LabelledValue[] {
  return report.measures === undefined ? [] : sonarRows(report.measures);
}

/** A row of labelled figures: the one card shape every block on this page is drawn with. */
function ValueCards({ values }: { values: readonly LabelledValue[] }) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
      {values.map((value) => (
        <MetricCard key={value.label} label={value.label} value={value.value} detail={value.detail} />
      ))}
    </div>
  );
}
