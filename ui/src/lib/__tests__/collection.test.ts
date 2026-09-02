/**
 * What the pages say about the collection their figures are anchored to.
 *
 * Staleness is the service's decision, arriving as `collection_stale`, so these assert the reading
 * of that flag and never a threshold of their own: a page that recomputed "how old is too old"
 * would drift from the configured cadence the moment it changed.
 */

import { describe, expect, it } from 'vitest';
import { collectedLabel, collectionNotice } from '@/lib/collection';
import type { WindowOptions } from '@/lib/types';

function windows(options: Partial<WindowOptions> = {}): WindowOptions {
  return {
    options: [1, 4, 8, 12],
    default: 4,
    trend_periods: 26,
    collected_through: '2026-09-01T00:00:00+00:00',
    collection_stale: false,
    ...options,
  };
}

describe('collectionNotice', () => {
  it('says nothing at all while the collection is current', () => {
    expect(collectionNotice(windows())).toBeNull();
  });

  it('names the day a stale collection reaches, and what the figures cover instead', () => {
    const notice = collectionNotice(
      windows({ collected_through: '2026-08-04T00:00:00+00:00', collection_stale: true }),
    );
    expect(notice).toContain('2026-08-04');
    expect(notice).toContain('window ending there');
    expect(notice).toContain('metrics collect');
  });

  it('reports nothing collected as nothing reportable, with no day to name', () => {
    const notice = collectionNotice(windows({ collected_through: undefined, collection_stale: true }));
    expect(notice).toContain('Nothing has been collected');
    expect(notice).not.toContain('-');
  });

  it('states the collection edge as the UTC day, whatever offset it arrived under', () => {
    // The edge is a midnight, and it must read as the same day the window's own dates do — an
    // instant written in a two-hour zone is the day it falls on in UTC.
    const notice = collectionNotice(
      windows({ collected_through: '2026-08-04T01:00:00+02:00', collection_stale: true }),
    );
    expect(notice).toContain('2026-08-03');
  });
});

describe('collectedLabel', () => {
  it('labels the collection a window is anchored to', () => {
    expect(collectedLabel('2026-09-01T00:00:00+00:00')).toBe('Collected through 2026-09-01');
  });

  it('has nothing to label where nothing was collected', () => {
    expect(collectedLabel(undefined)).toBeNull();
    expect(collectedLabel(null)).toBeNull();
  });
});
