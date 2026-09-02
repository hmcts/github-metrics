import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  ApiError,
  DEFAULT_API_URL,
  apiFetch,
  isNotFound,
  getActor,
  getActors,
  getOverview,
  getRepositories,
  getRepository,
  getTeam,
  getTeams,
  getTrend,
  getWindows,
} from '@/lib/api';

const calls: { url: string; options: RequestInit | undefined }[] = [];

function respond(body: unknown, ok = true, status = 200, text = ''): void {
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, options: RequestInit | undefined) => {
      calls.push({ url, options });
      return Promise.resolve({
        ok,
        status,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(text),
      });
    }),
  );
}

beforeEach(() => {
  calls.length = 0;
  delete process.env.API_URL;
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('apiFetch', () => {
  it('reads the service on localhost when no API_URL is configured', async () => {
    respond({ status: 'ok' });
    await apiFetch('/healthz');
    expect(calls[0]?.url).toBe(`${DEFAULT_API_URL}/healthz`);
  });

  it('reads API_URL per call, so a deployment can change it without a rebuild', async () => {
    respond({ status: 'ok' });
    process.env.API_URL = 'http://evidence.internal:9000';
    await apiFetch('/healthz');
    expect(calls[0]?.url).toBe('http://evidence.internal:9000/healthz');
  });

  it('never caches, because the service rebuilds a window when a collection lands', async () => {
    respond({ status: 'ok' });
    await apiFetch('/healthz');
    expect(calls[0]?.options).toEqual({ cache: 'no-store' });
  });

  it("raises with the service's own detail, which names what was refused", async () => {
    respond(null, false, 404, '{"detail":"repository is not configured: nothing-here"}');
    await expect(apiFetch('/repositories/nothing-here')).rejects.toThrow(
      'API 404 for /repositories/nothing-here: {"detail":"repository is not configured: nothing-here"}',
    );
  });

  it('still raises when the failed response has no readable body', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve({
          ok: false,
          status: 500,
          json: () => Promise.resolve(null),
          text: () => Promise.reject(new Error('connection reset')),
        }),
      ),
    );
    await expect(apiFetch('/overview')).rejects.toThrow('API 500 for /overview: ');
  });

  it('reads an unparseable success as a fault of its own rather than a bare SyntaxError', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.reject(new SyntaxError('Unexpected token <')),
          text: () => Promise.resolve(''),
        }),
      ),
    );
    const refused = await apiFetch('/overview').catch((error: unknown) => error);
    expect(refused).toBeInstanceOf(ApiError);
    expect((refused as ApiError).message).toBe('API 200 for /overview: response was not JSON');
    expect(isNotFound(refused)).toBe(false);
  });

  it('carries the status, so a page can answer a 404 with not-found and nothing else', async () => {
    respond(null, false, 404, '{"detail":"repository is not configured: nothing-here"}');
    const refused = await apiFetch('/repositories/nothing-here').catch((error: unknown) => error);
    expect(refused).toBeInstanceOf(ApiError);
    expect((refused as ApiError).status).toBe(404);
    expect(isNotFound(refused)).toBe(true);
  });

  it('reads any other failure as a fault rather than as a name nobody configured', async () => {
    respond(null, false, 503, 'the caches are being rebuilt');
    const refused = await apiFetch('/overview').catch((error: unknown) => error);
    expect(isNotFound(refused)).toBe(false);
    expect(isNotFound(new Error('connection reset'))).toBe(false);
  });
});

describe('endpoints', () => {
  it('asks each route for the requested span', async () => {
    respond([]);
    await getWindows();
    await getOverview(8);
    await getRepositories(8);
    await getActors(1);
    await getTeams(26);
    expect(calls.map((call) => call.url.replace(DEFAULT_API_URL, ''))).toEqual([
      '/windows',
      '/overview?weeks=8',
      '/repositories?weeks=8',
      '/actors?weeks=1',
      '/teams?weeks=26',
    ]);
  });

  it('encodes the name in every path, so a slug can never open a path of its own', async () => {
    respond({});
    await getRepository('cath/service', 4);
    await getActor('Alice Smith', 4);
    await getTeam('team one', 4);
    expect(calls.map((call) => call.url.replace(DEFAULT_API_URL, ''))).toEqual([
      '/repositories/cath%2Fservice?weeks=4',
      '/actors/Alice%20Smith?weeks=4',
      '/teams/team%20one?weeks=4',
    ]);
  });

  it('asks for a series with no window but always with a cut, which the service has no default for', async () => {
    respond({});
    await getTrend('cath-service', 26);
    expect(calls[0]?.url.replace(DEFAULT_API_URL, '')).toBe(
      '/repositories/cath-service/trend?periods=26',
    );
  });

  it('encodes the repository in a trend path too', async () => {
    respond({});
    await getTrend('cath/service', 26);
    expect(calls[0]?.url.replace(DEFAULT_API_URL, '')).toBe(
      '/repositories/cath%2Fservice/trend?periods=26',
    );
  });

  it('returns the parsed body to the caller', async () => {
    const body = {
      options: [1, 4],
      default: 4,
      trend_periods: 26,
      collected_through: '2026-09-01T00:00:00+00:00',
      collection_stale: false,
    };
    respond(body);
    await expect(getWindows()).resolves.toEqual(body);
  });
});
