/**
 * Server-side client for the `metrics-serve` evidence service.
 *
 * Every function here runs in a React Server Component, never in the browser: the service publishes
 * no CORS headers precisely so that the only thing calling it is this Next.js server. `API_URL` is
 * read per call rather than captured at module scope so a deployment can change it without a rebuild.
 *
 * `cache: 'no-store'` on every request. The service already holds one built report per window and
 * rebuilds it when a collection lands, so a second cache in front of it would serve a window whose
 * source has moved on, with nothing on the page to say so.
 */

import type {
  ActorDetail,
  ActorRow,
  OverviewSummary,
  RepositoryDetail,
  RepositoryRow,
  RepositoryTrend,
  TeamDetail,
  TeamRow,
  WindowOptions,
} from '@/lib/types';

export const DEFAULT_API_URL = 'http://localhost:8000';

export const NOT_FOUND = 404;

/**
 * A refusal from the service, carrying the status beside the message.
 *
 * The status is kept because one of them is not an error to show: a 404 means the name in the URL is
 * not one the configuration holds, which a page answers with Next.js's own not-found rather than
 * with a stack trace. Every other status is a fault worth surfacing as one.
 */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = 'ApiError';
    this.status = status;
  }
}

export function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.status === NOT_FOUND;
}

function base(): string {
  return process.env.API_URL ?? DEFAULT_API_URL;
}

export async function apiFetch<T>(path: string): Promise<T> {
  const response = await fetch(`${base()}${path}`, { cache: 'no-store' });
  if (!response.ok) {
    // The body carries the service's own `detail`, which names the repository, login or window that
    // was refused — worth keeping, because a bare status cannot say which of them was wrong.
    const body = await response.text().catch(() => '');
    throw new ApiError(response.status, `API ${response.status} for ${path}: ${body}`);
  }
  try {
    return (await response.json()) as T;
  } catch (cause) {
    // A 200 carrying something that is not JSON is a fault, and it must arrive as an `ApiError` like
    // every other one: a bare `SyntaxError` escaping here would go round the `isNotFound` check the
    // pages branch on and name neither the path nor the status.
    throw new ApiError(response.status, `API ${response.status} for ${path}: response was not JSON`, {
      cause,
    });
  }
}

/** Build a `?weeks=` query, the one parameter every data endpoint takes. */
function query(weeks: number): string {
  return `?${new URLSearchParams({ weeks: String(weeks) }).toString()}`;
}

export async function getWindows(): Promise<WindowOptions> {
  return apiFetch<WindowOptions>('/windows');
}

export async function getOverview(weeks: number): Promise<OverviewSummary> {
  return apiFetch<OverviewSummary>(`/overview${query(weeks)}`);
}

export async function getRepositories(weeks: number): Promise<RepositoryRow[]> {
  return apiFetch<RepositoryRow[]>(`/repositories${query(weeks)}`);
}

export async function getRepository(repository: string, weeks: number): Promise<RepositoryDetail> {
  return apiFetch<RepositoryDetail>(`/repositories/${encodeURIComponent(repository)}${query(weeks)}`);
}

export async function getTrend(repository: string, periods: number): Promise<RepositoryTrend> {
  // No `?weeks=`: a series is cut into periods from the repository's own enablement instant, so there
  // is no reporting window to select.
  //
  // `periods` IS named, and must be. The service has no default for it: left out, a request asks for
  // every whole period since enablement, and a series resolving above the service's maximum is
  // refused rather than truncated — so an unnamed cut costs the trend section on every repository
  // enabled long enough to have exceeded it. The cut comes from `GET /windows`, so it is the
  // service's own bound rather than a copy of it kept here.
  const query = new URLSearchParams({ periods: String(periods) }).toString();
  return apiFetch<RepositoryTrend>(`/repositories/${encodeURIComponent(repository)}/trend?${query}`);
}

export async function getActors(weeks: number): Promise<ActorRow[]> {
  return apiFetch<ActorRow[]>(`/actors${query(weeks)}`);
}

export async function getActor(login: string, weeks: number): Promise<ActorDetail> {
  return apiFetch<ActorDetail>(`/actors/${encodeURIComponent(login)}${query(weeks)}`);
}

export async function getTeams(weeks: number): Promise<TeamRow[]> {
  return apiFetch<TeamRow[]>(`/teams${query(weeks)}`);
}

export async function getTeam(team: string, weeks: number): Promise<TeamDetail> {
  return apiFetch<TeamDetail>(`/teams/${encodeURIComponent(team)}${query(weeks)}`);
}
