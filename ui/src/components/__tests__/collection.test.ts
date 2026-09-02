/**
 * The bar that says a page's figures are anchored at an old collection.
 *
 * Rendered through `renderToStaticMarkup`, the renderer a server component actually reaches the
 * reader through, so what is asserted is the markup: the amber edge, the sentence, and — for a
 * current collection — nothing whatsoever.
 *
 * JSX is written as `createElement` calls so this stays a `.ts` file alongside the others.
 */

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { CollectionNotice } from '@/components/CollectionNotice';
import type { WindowOptions } from '@/lib/types';

function windows(options: Partial<WindowOptions> = {}): WindowOptions {
  return {
    options: [1, 4],
    default: 4,
    trend_periods: 26,
    collected_through: '2026-09-01T00:00:00+00:00',
    collection_stale: false,
    ...options,
  };
}

describe('CollectionNotice', () => {
  it('warns with the collected day, as a colour bar and words rather than an emoji', () => {
    const markup = renderToStaticMarkup(
      createElement(CollectionNotice, {
        windows: windows({ collected_through: '2026-08-04T00:00:00+00:00', collection_stale: true }),
      }),
    );
    expect(markup).toContain('border-l-rag-amber');
    expect(markup).toContain('2026-08-04');
    expect(markup).toContain('role="status"');
    expect(markup).not.toMatch(/\p{Extended_Pictographic}/u);
  });

  it('warns without a day where nothing has been collected', () => {
    const markup = renderToStaticMarkup(
      createElement(CollectionNotice, {
        windows: windows({ collected_through: undefined, collection_stale: true }),
      }),
    );
    expect(markup).toContain('Nothing has been collected');
  });

  it('renders nothing at all while the collection is current', () => {
    expect(renderToStaticMarkup(createElement(CollectionNotice, { windows: windows() }))).toBe('');
  });
});
