import { describe, expect, it } from 'vitest';

import type { Product, SellerEntry } from '../types';
import { deriveDiscover, discoverNote, formatKeyFor } from './productGroups';

function product(formatId: string | null, extra: Partial<Product> = {}): Product {
  return {
    product_id: `p-${formatId ?? 'none'}-${Math.random().toString(36).slice(2, 7)}`,
    ...(formatId === null ? {} : { format_ids: [formatId] }),
    ...extra,
  };
}

function fanOut(entries: SellerEntry[]): unknown {
  return { task: 'adcp_get_products', sellers_queried: entries.length, results: entries };
}

function responded(id: string, products: Product[], extra: Record<string, unknown> = {}) {
  return { seller_id: id, seller_name: `${id} agent`, response: { products, ...extra } };
}

describe('formatKeyFor', () => {
  it('matches a medium in any segment of the id', () => {
    // audio_30s names its medium FIRST. A prefix/suffix test misses it, which was a real bug in the
    // vanilla UI and is why segment matching is used.
    expect(formatKeyFor(product('audio_30s'))).toBe('audio');
    expect(formatKeyFor(product('gotham_video'))).toBe('video');
    expect(formatKeyFor(product('nyt_display_300x250'))).toBe('display');
    expect(formatKeyFor(product('podcast_midroll'))).toBe('podcast');
    expect(formatKeyFor(product('interactive_endcard'))).toBe('endcard');
  });

  it('returns null rather than forcing an unrecognised format into a group', () => {
    expect(formatKeyFor(product('carousel_rich'))).toBeNull();
    expect(formatKeyFor(product(null))).toBeNull();
  });

  it('accepts the object form of format_ids', () => {
    expect(
      formatKeyFor({ product_id: 'x', format_ids: [{ id: 'audio_15s', agent_url: 'u' }] }),
    ).toBe('audio');
  });
});

describe('deriveDiscover', () => {
  it('returns null when the payload is not a fan-out', () => {
    // So the caller reports an absence rather than rendering an empty panel that looks like a real
    // empty answer.
    expect(deriveDiscover(undefined)).toBeNull();
    expect(deriveDiscover('not json')).toBeNull();
    expect(deriveDiscover({ products: [] })).toBeNull();
  });

  it('derives each group count from the products present', () => {
    const data = deriveDiscover(
      fanOut([
        responded('triton', [product('audio_30s'), product('audio_15s'), product('podcast_mid')]),
      ]),
    );
    expect(data).not.toBeNull();
    const audio = data?.groups.find((g) => g.formatKey === 'audio');
    expect(audio?.count).toBe(2);
    expect(data?.groups.find((g) => g.formatKey === 'podcast')?.count).toBe(1);
  });

  it('produces NO group for a format with no products', () => {
    // Not an empty group and not a zero. An empty group would assert the seller offers none of that
    // format, when the truth is it was not part of this answer.
    const data = deriveDiscover(fanOut([responded('triton', [product('audio_30s')])]));
    expect(data?.groups.map((g) => g.formatKey)).toEqual(['audio']);
    expect(data?.groups.every((g) => g.count > 0)).toBe(true);
  });

  it('orders groups by the authored format order, not by seller answer order', () => {
    const data = deriveDiscover(
      fanOut([responded('s', [product('display_banner'), product('audio_30s')])]),
    );
    // FORMAT_META order is audio, podcast, display, video, endcard.
    expect(data?.groups.map((g) => g.formatKey)).toEqual(['audio', 'display']);
  });

  it('counts an errored seller as errored, not as one with no inventory', () => {
    const data = deriveDiscover(
      fanOut([
        responded('ok', [product('audio_30s')]),
        { seller_id: 'down', error: 'TimeoutError: no response' },
        { seller_id: 'nope', response: { mcp_error: 'Unknown tool' } },
      ]),
    );
    expect(data?.sellersQueried).toBe(3);
    expect(data?.sellersResponded).toBe(1);
    expect(data?.sellersErrored).toBe(2);
  });

  it('distinguishes a seller that returned an empty list from one that errored', () => {
    const data = deriveDiscover(
      fanOut([responded('empty', []), { seller_id: 'down', error: 'boom' }]),
    );
    expect(data?.sellersResponded).toBe(1);
    expect(data?.sellersErrored).toBe(1);
    expect(data?.groups).toEqual([]);
  });

  it('reports products whose format matched nothing rather than dropping them', () => {
    const data = deriveDiscover(
      fanOut([responded('s', [product('audio_30s'), product('carousel_rich')])]),
    );
    expect(data?.unmatched).toBe(1);
    expect(data?.groups.find((g) => g.formatKey === 'audio')?.count).toBe(1);
  });

  it('carries list truncation onto the group', () => {
    const data = deriveDiscover(
      fanOut([
        responded('s', [product('audio_30s')], {
          products_recording_truncated: { kept: 1, total: 200 },
        }),
      ]),
    );
    expect(data?.groups[0]?.truncation).toEqual({ kept: 1, total: 200 });
  });

  it('attributes each group to the sellers that contributed', () => {
    const data = deriveDiscover(
      fanOut([
        responded('triton', [product('audio_30s')]),
        responded('gotham', [product('audio_15s')]),
      ]),
    );
    expect(data?.groups[0]?.attribution).toEqual(['triton agent', 'gotham agent']);
  });

  it('does not repeat a seller in attribution', () => {
    const data = deriveDiscover(
      fanOut([responded('triton', [product('audio_30s'), product('audio_15s')])]),
    );
    expect(data?.groups[0]?.attribution).toEqual(['triton agent']);
  });
});

describe('deriveDiscover product labels', () => {
  it('keeps count and the product list as one fact', () => {
    // Two independently-supplied numbers for the same thing is how a panel ends up showing five
    // icons under a heading that says six.
    const data = deriveDiscover(
      fanOut([responded('triton', [product('audio_30s'), product('audio_15s')])]),
    );
    data?.groups.forEach((group) => expect(group.count).toBe(group.products.length));
  });

  it('carries each product title, or its id when it has no name', () => {
    const data = deriveDiscover(
      fanOut([
        responded('triton', [
          product('audio_30s', { product_id: 'p-named', name: 'Drive Time National' }),
          product('audio_15s', { product_id: 'p-unnamed' }),
        ]),
      ]),
    );
    expect(data?.groups[0]?.products.map((p) => p.title)).toEqual([
      'Drive Time National',
      'p-unnamed',
    ]);
  });

  it('attributes every product to the seller that returned it', () => {
    // Per product, not just per group: a group can hold products from several sellers, and that is
    // exactly when an unattributed row misleads.
    const data = deriveDiscover(
      fanOut([
        responded('triton', [product('audio_30s', { name: 'A' })]),
        responded('gotham', [product('audio_15s', { name: 'B' })]),
      ]),
    );
    expect(data?.groups[0]?.products.map((p) => [p.title, p.seller])).toEqual([
      ['A', 'triton agent'],
      ['B', 'gotham agent'],
    ]);
  });

  it('omits the price entirely when the seller quoted none', () => {
    // Not zero, not an empty string. "Unpriced" and "free" are different claims.
    const data = deriveDiscover(fanOut([responded('triton', [product('audio_30s')])]));
    const [first] = data?.groups[0]?.products ?? [];
    expect(first).toBeDefined();
    expect('price' in first!).toBe(false);
  });

  it('formats the price the seller did quote', () => {
    const data = deriveDiscover(
      fanOut([
        responded('triton', [
          product('audio_30s', {
            pricing_options: [{ pricing_model: 'cpm', currency: 'USD', fixed_price: 24 }],
          }),
        ]),
      ]),
    );
    expect(data?.groups[0]?.products[0]?.price).toBe('USD 24.00 CPM');
  });

  it('treats a real zero price as a price', () => {
    // A seller that quoted 0 said something; dropping it would misstate its offer.
    const data = deriveDiscover(
      fanOut([
        responded('triton', [
          product('audio_30s', {
            pricing_options: [{ pricing_model: 'cpm', currency: 'USD', fixed_price: 0 }],
          }),
        ]),
      ]),
    );
    expect(data?.groups[0]?.products[0]?.price).toBe('USD 0.00 CPM');
  });

  it('carries the format id as the seller sent it', () => {
    const data = deriveDiscover(fanOut([responded('triton', [product('audio_30s')])]));
    expect(data?.groups[0]?.products[0]?.formatId).toBe('audio_30s');
  });

  it('gives every product a distinct key, even across sellers reusing an id', () => {
    // React keys: two sellers legitimately returning the same product_id must not collide.
    const data = deriveDiscover(
      fanOut([
        responded('triton', [
          { product_id: 'shared', format_ids: ['audio_30s'] },
          { product_id: 'shared', format_ids: ['audio_15s'] },
        ]),
        responded('gotham', [{ product_id: 'shared', format_ids: ['audio_30s'] }]),
      ]),
    );
    const keys = data?.groups[0]?.products.map((p) => p.key) ?? [];
    expect(keys).toHaveLength(3);
    expect(new Set(keys).size).toBe(3);
  });

  it('keeps products in the order their sellers returned them', () => {
    const data = deriveDiscover(
      fanOut([
        responded('triton', [
          product('audio_30s', { name: 'first' }),
          product('audio_15s', { name: 'second' }),
        ]),
      ]),
    );
    expect(data?.groups[0]?.products.map((p) => p.title)).toEqual(['first', 'second']);
  });
});

/**
 * The characteristics that make one row distinguishable from the next.
 *
 * `toGroupProduct` used to keep four fields and drop everything else, so two products differing only
 * by placement and edition produced two identical rows. These assert the wiring, not the extraction:
 * which AdCP field each fact comes from is `products.test.ts`'s job.
 */
describe('deriveDiscover carries each product\'s characteristics', () => {
  const first = (output: unknown) => deriveDiscover(output)?.groups[0]?.products[0];

  it('carries the facts the seller stated', () => {
    const data = first(
      fanOut([
        responded('gotham', [
          product('audio_30s', {
            placements: [{ kind: 'seller_inline', placement_id: 'rr', name: 'right-rail' }],
            product_card_detailed: {
              specifications: [{ label: 'Edition', value: 'US National' }],
            },
          }),
        ]),
      ]),
    );
    expect(data?.facts?.map((f) => f.value)).toEqual(['right-rail', 'US National']);
  });

  it('omits facts entirely when the seller stated none', () => {
    // Absent, not an empty array: a renderer should not have to distinguish the two.
    const data = first(fanOut([responded('triton', [{ format_ids: ['audio_30s'] }])]));
    expect(data).not.toHaveProperty('facts');
  });

  it('carries the reach figure the seller measured', () => {
    const data = first(
      fanOut([
        responded('gotham', [
          product('audio_30s', {
            ext: {
              gotham_measurement: { reachable_audience: 187328, reach_label: 'a label' },
            },
          }),
        ]),
      ]),
    );
    // The label is on the wire and deliberately not carried through: the panel's seller name
    // already says "(sandbox, MCP)" on the same row.
    expect(data?.reach).toEqual({ audience: 187328, unit: 'audience' });
  });

  it('carries the score the seller ranked by, and the rate as a number', () => {
    // The constellation positions on these. `price` is the same rate already formatted for a row.
    const data = first(
      fanOut([
        responded('gotham', [
          product('gotham_display', {
            ext: { gotham_measurement: { fused_score: 0.42 } },
            pricing_options: [{ pricing_model: 'cpm', currency: 'USD', fixed_price: 9 }],
          }),
        ]),
      ]),
    );
    expect(data?.relevance).toBeCloseTo(0.42);
    expect(data?.cpm).toBe(9);
    expect(data?.currency).toBe('USD');
  });

  it('omits the ranking score when the seller returned none', () => {
    // A product with no score cannot be placed radially, and must not be defaulted onto a ring.
    const data = first(fanOut([responded('triton', [product('audio_30s')])]));
    expect(data).not.toHaveProperty('relevance');
  });

  it('carries a reach of zero, which is a figure', () => {
    const data = first(
      fanOut([
        responded('gotham', [
          product('audio_30s', { ext: { gotham_measurement: { reachable_audience: 0 } } }),
        ]),
      ]),
    );
    expect(data?.reach?.audience).toBe(0);
  });

  it('omits reach when the seller could not measure it', () => {
    const data = first(
      fanOut([
        responded('gotham', [
          product('audio_30s', { ext: { gotham_measurement: { content_similarity: 0.7 } } }),
        ]),
      ]),
    );
    expect(data).not.toHaveProperty('reach');
  });

  it('keeps the two similarity axes apart', () => {
    const data = first(
      fanOut([
        responded('gotham', [
          product('audio_30s', {
            ext: { gotham_measurement: { content_similarity: 0.77, audience_similarity: 0.88 } },
          }),
        ]),
      ]),
    );
    expect(data?.similarity?.map((b) => [b.axis, b.value])).toEqual([
      ['content', 0.77],
      ['audience', 0.88],
    ]);
  });

  it('carries the axis the seller scored even when the other is absent', () => {
    const data = first(
      fanOut([
        responded('gotham', [
          product('audio_30s', { ext: { gotham_measurement: { audience_similarity: 0.5 } } }),
        ]),
      ]),
    );
    expect(data?.similarity?.map((b) => b.axis)).toEqual(['audience']);
  });

  it('omits similarity when the seller returned no scores', () => {
    const data = first(fanOut([responded('triton', [product('audio_30s')])]));
    expect(data).not.toHaveProperty('similarity');
  });

  it('distinguishes products that share a title, format and price', () => {
    // The reported defect, as a test. Same name, same format, same rate; different placement.
    const data = deriveDiscover(
      fanOut([
        responded('gotham', [
          product('gotham_display', {
            name: 'Gotham Daily — Investigations',
            placements: [{ name: 'right-rail' }],
          }),
          product('gotham_display', {
            name: 'Gotham Daily — Investigations',
            placements: [{ name: 'leaderboard' }],
          }),
        ]),
      ]),
    );
    const rows = data?.groups[0]?.products ?? [];
    expect(rows).toHaveLength(2);
    expect(rows.map((p) => p.facts?.map((f) => f.value).join(' '))).toEqual([
      'right-rail',
      'leaderboard',
    ]);
  });
});

describe('discoverNote', () => {
  it('states real totals only', () => {
    const data = deriveDiscover(
      fanOut([responded('s', [product('audio_30s'), product('podcast_mid')])]),
    )!;
    const note = discoverNote(data);
    expect(note).toContain('2 products across 2 formats');
    expect(note).toContain('1 of 1 sales agent responded');
  });

  it('reports errored sellers', () => {
    const data = deriveDiscover(
      fanOut([responded('s', [product('audio_30s')]), { seller_id: 'd', error: 'x' }]),
    )!;
    expect(discoverNote(data)).toContain('1 errored');
  });

  it('reports unmatched products and trimmed lists', () => {
    const data = deriveDiscover(
      fanOut([
        responded('s', [product('audio_30s'), product('carousel_rich')], {
          products_recording_truncated: { kept: 2, total: 50 },
        }),
      ]),
    )!;
    const note = discoverNote(data);
    expect(note).toContain('1 in other formats');
    expect(note).toContain('trimmed when recorded');
  });

  it('says nothing about errors or trimming when there were none', () => {
    const data = deriveDiscover(fanOut([responded('s', [product('audio_30s')])]))!;
    const note = discoverNote(data);
    expect(note).not.toContain('errored');
    expect(note).not.toContain('trimmed');
    expect(note).not.toContain('other formats');
  });
});
