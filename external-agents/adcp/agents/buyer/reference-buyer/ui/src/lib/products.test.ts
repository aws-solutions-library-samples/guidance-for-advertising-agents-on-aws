import { describe, expect, it } from 'vitest';

import {
  FORMAT_ICONS,
  GENERIC_ICON,
  formatCompactCount,
  productCpm,
  productFacts,
  productFormatInfo,
  productPriceLabel,
  productPriceShort,
  productReach,
  productRelevance,
  productSimilarityBars,
  productTitle,
  productsTrimmedNote,
} from './products';
import type { Product } from './types';

/**
 * These tests are mostly about the distinction the whole display rests on: a field the seller
 * omitted must produce nothing, while a zero must be shown. Getting that backwards reports
 * data in one direction and hides a measurement in the other.
 */

describe('productFormatInfo', () => {
  it('reads the format id as sent', () => {
    const product: Product = { format_ids: [{ id: 'gotham_newsletter' }] };
    expect(productFormatInfo(product).id).toBe('gotham_newsletter');
  });

  it('picks the mark from the id suffix, not a per-seller table', () => {
    for (const [id, expected] of [
      ['gotham_display', 'display'],
      ['gotham_video', 'video'],
      ['gotham_newsletter', 'newsletter'],
      ['gotham_podcast', 'podcast'],
      ['gotham_native', 'native'],
      ['audio_30s', 'audio'],
    ] as const) {
      const info = productFormatInfo({ format_ids: [{ id }] });
      expect(info.icon, `${id} should resolve to the ${expected} mark`).not.toBe(GENERIC_ICON);
    }
  });

  it('resolves a seller it has never seen from the same segment', () => {
    const known = productFormatInfo({ format_ids: [{ id: 'gotham_video' }] });
    const novel = productFormatInfo({ format_ids: [{ id: 'nyt_video' }] });
    expect(novel.icon).toBe(known.icon);
  });

  it('matches a medium in any segment, not just the last', () => {
    // Regression: the vanilla UI matched suffix-or-"_medium", so triton's `audio_30s` fell
    // through to the generic mark because the medium is the first segment.
    const audio = productFormatInfo({ format_ids: [{ id: 'audio_30s' }] });
    expect(audio.icon).toBe(FORMAT_ICONS['audio']);
    const middle = productFormatInfo({ format_ids: [{ id: 'nyt_display_300x250' }] });
    expect(middle.icon).toBe(FORMAT_ICONS['display']);
  });

  it('does not match a medium embedded in a longer word', () => {
    // "videography" is not the video medium; segment matching is what keeps that true.
    expect(productFormatInfo({ format_ids: [{ id: 'gotham_videography' }] }).icon).toBe(
      GENERIC_ICON,
    );
  });

  it('accepts a bare string format id', () => {
    expect(productFormatInfo({ format_ids: ['gotham_display'] }).id).toBe('gotham_display');
  });

  it('draws the generic mark for an unrecognised medium rather than guessing', () => {
    const info = productFormatInfo({ format_ids: [{ id: 'gotham_hologram' }] });
    expect(info.id).toBe('gotham_hologram');
    expect(info.icon).toBe(GENERIC_ICON);
  });

  it('reports no id when the product carries no format', () => {
    expect(productFormatInfo({}).id).toBeNull();
    expect(productFormatInfo({ format_ids: [] }).id).toBeNull();
  });

  it('uses only stroke marks, never a fill', () => {
    for (const icon of [GENERIC_ICON, productFormatInfo({ format_ids: ['x_video'] }).icon]) {
      expect(icon.path).not.toMatch(/fill=/);
    }
  });
});

describe('productPriceLabel', () => {
  it('formats the seller rate with currency and model', () => {
    expect(
      productPriceLabel({
        pricing_options: [{ currency: 'USD', pricing_model: 'cpm', fixed_price: 16 }],
      }),
    ).toBe('USD 16.00 CPM');
  });

  it('returns null when there is no pricing at all, so no chip is drawn', () => {
    expect(productPriceLabel({})).toBeNull();
    expect(productPriceLabel({ pricing_options: [] })).toBeNull();
  });

  it('treats a missing or non-numeric rate as unpriced rather than zero', () => {
    expect(productPriceLabel({ pricing_options: [{ currency: 'USD' }] })).toBeNull();
    expect(
      productPriceLabel({
        pricing_options: [{ fixed_price: Number.NaN, currency: 'USD' }],
      }),
    ).toBeNull();
  });

  it('shows a genuine zero rate, which is a real price', () => {
    expect(productPriceLabel({ pricing_options: [{ fixed_price: 0, currency: 'USD' }] })).toBe(
      'USD 0.00',
    );
  });
});

describe('formatCompactCount', () => {
  it('leaves small numbers untouched', () => {
    expect(formatCompactCount(368)).toBe('368');
    expect(formatCompactCount(0)).toBe('0');
  });

  it('abbreviates thousands with one decimal below ten', () => {
    expect(formatCompactCount(9_400)).toBe('9.4K');
    expect(formatCompactCount(4_200)).toBe('4.2K');
  });

  it('drops the decimal at or above ten of the unit', () => {
    expect(formatCompactCount(368_336)).toBe('368K');
    expect(formatCompactCount(10_000)).toBe('10K');
  });

  it('abbreviates millions the same way', () => {
    expect(formatCompactCount(2_500_000)).toBe('2.5M');
    expect(formatCompactCount(5_000_000)).toBe('5M');
  });

  it('strips a trailing .0 rather than showing a false precision', () => {
    expect(formatCompactCount(1_000)).toBe('1K');
  });

  it('preserves sign', () => {
    expect(formatCompactCount(-9_400)).toBe('-9.4K');
    expect(formatCompactCount(-368_336)).toBe('-368K');
  });
});

describe('productPriceShort', () => {
  it('rounds to a whole unit with no pricing-model suffix', () => {
    expect(
      productPriceShort({
        pricing_options: [{ currency: 'USD', pricing_model: 'cpm', fixed_price: 16 }],
      }),
    ).toBe('USD 16');
  });

  it('rounds rather than truncating', () => {
    expect(productPriceShort({ pricing_options: [{ currency: 'USD', fixed_price: 24.6 }] })).toBe(
      'USD 25',
    );
  });

  it('returns null when there is no usable pricing, same as the full label', () => {
    expect(productPriceShort({})).toBeNull();
    expect(productPriceShort({ pricing_options: [{ currency: 'USD' }] })).toBeNull();
  });

  it('shows a genuine zero rate', () => {
    expect(productPriceShort({ pricing_options: [{ fixed_price: 0, currency: 'USD' }] })).toBe(
      'USD 0',
    );
  });
});

describe('productSimilarityBars', () => {
  const measured = (m: Record<string, number>): Product => ({ ext: { gotham_measurement: m } });

  it('returns both axes separately', () => {
    const bars = productSimilarityBars(
      measured({ content_similarity: 0.425883, audience_similarity: 0.456952 }),
    );
    expect(bars.map((b) => b.axis)).toEqual(['content', 'audience']);
    expect(bars[0]?.value).toBe(0.425883);
    expect(bars[1]?.value).toBe(0.456952);
  });

  it('returns nothing when the seller sent no measurement', () => {
    expect(productSimilarityBars({})).toEqual([]);
    expect(productSimilarityBars({ ext: {} })).toEqual([]);
  });

  it('includes a score of exactly zero, which is a measurement', () => {
    const bars = productSimilarityBars(measured({ content_similarity: 0, audience_similarity: 0.5 }));
    expect(bars).toHaveLength(2);
    expect(bars[0]?.value).toBe(0);
  });

  it('omits only the axis that is missing', () => {
    const bars = productSimilarityBars(measured({ audience_similarity: 0.3 }));
    expect(bars.map((b) => b.axis)).toEqual(['audience']);
  });

  it('clamps the bar width but prints the score as returned', () => {
    const [bar] = productSimilarityBars(measured({ content_similarity: 1.4 }));
    expect(bar?.value).toBe(1.4);
    expect(bar?.fraction).toBe(1);
  });
});

describe('productReach', () => {
  it('reads the figure and ignores the label the service sends with it', () => {
    // `reach_label` is a whole sentence ("synthetic sandbox figure - The Gotham Gazette is a
    // fictional publication") and rendering it put that on every row. It stays on the wire.
    const reach = productReach({
      ext: {
        gotham_measurement: {
          reachable_audience: 368336,
          reach_label: 'synthetic sandbox figure',
        },
      },
    });
    expect(reach).toEqual({ audience: 368336, unit: 'audience' });
  });

  it('reports a reach of zero, which differs from not knowing', () => {
    expect(productReach({ ext: { gotham_measurement: { reachable_audience: 0 } } })).toEqual({
      audience: 0,
      unit: 'audience',
    });
  });

  it('returns null when reach was not supplied', () => {
    expect(productReach({})).toBeNull();
    expect(productReach({ ext: { gotham_measurement: {} } })).toBeNull();
  });
});

describe('productTitle', () => {
  it('prefers the name, then the id, and adds no text of its own', () => {
    expect(productTitle({ name: 'Gotham Sports Desk', product_id: 'gg-1' })).toBe(
      'Gotham Sports Desk',
    );
    expect(productTitle({ product_id: 'gg-1' })).toBe('gg-1');
    expect(productTitle({})).toBe('Unnamed product');
  });
});

describe('productsTrimmedNote', () => {
  it('states how many of how many when the recorder trimmed', () => {
    expect(productsTrimmedNote(4, { total: 50 })).toBe('4 of 50 recorded');
  });

  it('says nothing when nothing was trimmed', () => {
    expect(productsTrimmedNote(10, undefined)).toBeNull();
    expect(productsTrimmedNote(10, { total: 10 })).toBeNull();
    expect(productsTrimmedNote(10, {})).toBeNull();
  });
});

/**
 * `productFacts` exists because ten Gotham products that differ by placement, edition and device
 * rendered as ten identical rows. Each source below is a separate AdCP carrier, and the last group
 * of tests is the one that matters most: a characteristic the seller did not state produces no fact,
 * so a row never gains a label with nothing behind it.
 */
describe('productFacts', () => {
  const values = (product: Product) => productFacts(product).map((f) => f.value);

  it('reads a placement name, which is where AdCP puts on-page position', () => {
    // No AdCP enum exists for right-rail/leaderboard, so `placements[].name` is the vocabulary.
    const product: Product = {
      placements: [{ kind: 'seller_inline', placement_id: 'rr', name: 'right-rail', mode: 'included' }],
    };
    expect(values(product)).toEqual(['right-rail']);
  });

  it('joins several placements rather than showing only the first', () => {
    const product: Product = {
      placements: [{ name: 'right-rail' }, { name: 'leaderboard' }],
    };
    expect(values(product)).toEqual(['right-rail, leaderboard']);
  });

  it('reads labelled pairs out of a vendor-namespaced ext block, by shape', () => {
    // AdCP requires `ext` keys to be vendor-namespaced, so there is no well-known name to look for.
    // The recognisable thing is the `{label, value}` pair shape. Gotham sends edition, environment
    // and language this way because the 3.0 wire schema has no field for any of them.
    const product: Product = {
      ext: {
        gotham_attributes: [
          { label: 'Edition', value: 'US National' },
          { label: 'Environment', value: 'mobile-web' },
        ],
      },
    };
    expect(productFacts(product)).toEqual([
      { key: 'gotham_attributes:Edition', label: 'Edition', value: 'US National' },
      { key: 'gotham_attributes:Environment', label: 'Environment', value: 'mobile-web' },
    ]);
  });

  it('does not mistake a block of scores for a list of characteristics', () => {
    // `ext.gotham_measurement` is an object of numbers, not a list of pairs. Matching by shape is
    // what keeps internal scoring out of the row; matching by key name would not.
    const product: Product = {
      ext: { gotham_measurement: { fused_score: 0.81, content_similarity: 0.77 } },
    };
    expect(productFacts(product)).toEqual([]);
  });

  it('ignores ext list entries that are not labelled pairs', () => {
    const product: Product = {
      ext: {
        vendor_things: [{ label: 'Kept', value: 'yes' }, 'a string', 42, null, { label: 'no value' }],
      },
    };
    expect(values(product)).toEqual(['yes']);
  });

  it("carries the seller's own specifications as label and value", () => {
    const product: Product = {
      product_card_detailed: {
        specifications: [
          { label: 'Edition', value: 'US National' },
          { label: 'Environment', value: 'mobile-web' },
        ],
      },
    };
    expect(productFacts(product)).toEqual([
      { key: 'spec:Edition', label: 'Edition', value: 'US National' },
      { key: 'spec:Environment', label: 'Environment', value: 'mobile-web' },
    ]);
  });

  it('drops a specification missing either half', () => {
    // A label with no value is the seller saying it has nothing for that characteristic; rendering
    // "Edition" alone would imply otherwise.
    const product: Product = {
      product_card_detailed: {
        specifications: [{ label: 'Edition' }, { value: 'orphaned' }, { label: ' ', value: 'x' }],
      },
    };
    expect(productFacts(product)).toEqual([]);
  });

  it('reads dimensions from the parameterised format reference', () => {
    const product: Product = { format_ids: [{ id: 'display_static', width: 300, height: 250 }] };
    expect(values(product)).toEqual(['300\u00d7250']);
  });

  it('needs both dimensions, since one alone describes no shape', () => {
    expect(values({ format_ids: [{ id: 'display_static', width: 300 }] })).toEqual([]);
  });

  it('renders duration in whole seconds when it divides evenly', () => {
    const product: Product = { format_ids: [{ id: 'audio_30s', duration_ms: 30000 }] };
    expect(values(product)).toEqual(['30s']);
  });

  it('keeps one decimal for a duration that does not', () => {
    expect(values({ format_ids: [{ id: 'audio', duration_ms: 6500 }] })).toEqual(['6.5s']);
  });

  it('ignores a bare string format id, which carries no parameters', () => {
    expect(values({ format_ids: ['audio_30s'] })).toEqual([]);
  });

  it('reports the four OpenRTB-derived placement enums a product may declare', () => {
    const product: Product = {
      video_placement_types: ['instream'],
      audio_distribution_types: ['broadcast'],
      social_placement_surfaces: ['feed', 'stories'],
      sponsored_placement_types: ['sponsored_search'],
    };
    expect(values(product)).toEqual([
      'instream',
      'broadcast',
      'feed, stories',
      'sponsored_search',
    ]);
  });

  it("reads Triton's non-standard top-level fields, which AdCP permits but does not define", () => {
    const product: Product = {
      market: 'Cleveland, OH',
      group: 'iHeartMedia',
      genre: 'News/Talk',
      content_types: ['live'],
      positions: ['preroll', 'midroll'],
      language: 'en',
    };
    expect(productFacts(product)).toEqual([
      { key: 'market', value: 'Cleveland, OH' },
      { key: 'group', label: 'group', value: 'iHeartMedia' },
      { key: 'genre', value: 'News/Talk' },
      { key: 'content_types', value: 'live' },
      { key: 'positions', value: 'preroll, midroll' },
      { key: 'language', value: 'en' },
    ]);
  });

  it('does not surface a seller\'s internal scoring as a characteristic', () => {
    // Reflecting over unknown top-level keys would label relevance_score and forecast as product
    // characteristics. The seller-specific fields are listed by name for exactly this reason.
    const product: Product = { relevance_score: 0.83, forecast: { impressions: 1000 } };
    expect(productFacts(product)).toEqual([]);
  });

  it('reports channels and normalises delivery_type for reading', () => {
    const product: Product = { channels: ['radio'], delivery_type: 'non_guaranteed' };
    expect(values(product)).toEqual(['radio', 'non-guaranteed']);
  });

  it('leads with what distinguishes two rows of the same format', () => {
    // Placement and edition vary between Gotham rows; delivery_type is identical across all of them.
    const product: Product = {
      delivery_type: 'non_guaranteed',
      channels: ['display'],
      placements: [{ name: 'right-rail' }],
      product_card_detailed: { specifications: [{ label: 'Edition', value: 'US National' }] },
    };
    expect(values(product)).toEqual([
      'right-rail',
      'US National',
      'display',
      'non-guaranteed',
    ]);
  });

  it('says nothing at all about a product that stated nothing', () => {
    expect(productFacts({})).toEqual([]);
    expect(productFacts({ product_id: 'p', name: 'n' })).toEqual([]);
  });

  it('skips empty and non-string entries rather than rendering blanks', () => {
    const product: Product = {
      channels: ['', '  ', 'radio', 42 as unknown as string],
      placements: [{ name: '  ' }, {}],
    };
    expect(values(product)).toEqual(['radio']);
  });

  it('prints a value once when two carriers hold it', () => {
    // The real recorded triton podcast: `content_types` and `channels` both say "podcast".
    // "podcast · podcast" reads as a bug, not as corroboration.
    const product: Product = { content_types: ['podcast'], channels: ['podcast'] };
    expect(values(product)).toEqual(['podcast']);
  });

  it('keeps two same-valued facts apart when one is labelled', () => {
    // "group: Meridian" and a market of "Meridian" are different statements about the product.
    const product: Product = { group: 'Meridian', market: 'Meridian' };
    expect(productFacts(product)).toEqual([
      { key: 'market', value: 'Meridian' },
      { key: 'group', label: 'group', value: 'Meridian' },
    ]);
  });

  it('gives every fact a key unique within the product', () => {
    const product: Product = {
      placements: [{ name: 'right-rail' }],
      channels: ['display'],
      delivery_type: 'guaranteed',
      product_card_detailed: {
        specifications: [
          { label: 'Edition', value: 'US National' },
          { label: 'Environment', value: 'app' },
        ],
      },
    };
    const keys = productFacts(product).map((f) => f.key);
    expect(new Set(keys).size).toBe(keys.length);
  });
});

/**
 * The metrics the constellation positions products by. Read here, laid out in `gravityGraph.ts`.
 */
describe('productRelevance', () => {
  it('prefers the fused score, which is what ordered the list', () => {
    expect(
      productRelevance({ ext: { gotham_measurement: { fused_score: 0.42 } } }),
    ).toBeCloseTo(0.42);
  });

  it('falls back to the content axis for a single-axis answer', () => {
    expect(
      productRelevance({ ext: { gotham_measurement: { content_similarity: 0.37 } } }),
    ).toBeCloseTo(0.37);
  });

  it("reads Triton's cosine, from its own vendor namespace", () => {
    // Hardcoding `gotham_measurement` is what kept Triton's products off the constellation after it
    // started sending a score. Any `*_measurement` block counts.
    expect(productRelevance({ ext: { triton_measurement: { cosine: 0.512 } } })).toBeCloseTo(0.512);
  });

  it('prefers a fused score over a bare cosine when a seller sends both', () => {
    expect(
      productRelevance({ ext: { gotham_measurement: { fused_score: 0.4, cosine: 0.9 } } }),
    ).toBeCloseTo(0.4);
  });

  it('ignores an ext block that is not a measurement', () => {
    // `ext.search_metadata` and `ext.gotham_attributes` sit alongside; neither is a score.
    expect(
      productRelevance({
        ext: { search_metadata: { cosine: 0.9 }, gotham_attributes: [{ label: 'a', value: 'b' }] },
      }),
    ).toBeNull();
  });

  it('ignores relevance_score, which is 1.0 on every product the filter returns', () => {
    // Reading it would stack every node on the centre and claim a perfect match for all of them.
    expect(productRelevance({ relevance_score: 1 })).toBeNull();
  });

  it('keeps a negative score, which is a real cosine', () => {
    expect(productRelevance({ ext: { gotham_measurement: { fused_score: -0.2 } } })).toBeCloseTo(
      -0.2,
    );
  });

  it('is null when the seller scored nothing', () => {
    expect(productRelevance({})).toBeNull();
    expect(productRelevance({ ext: {} })).toBeNull();
  });
});

describe('productReach reports what it counted', () => {
  it("reads Gotham's measured audience", () => {
    expect(
      productReach({ ext: { gotham_measurement: { reachable_audience: 187328 } } }),
    ).toEqual({ audience: 187328, unit: 'audience' });
  });

  it("reads Triton's forecast impressions, which count something different", () => {
    // One labelled as the other overstates an audience by whatever the frequency happens to be.
    expect(
      productReach({ forecast: { points: [{ metrics: { impressions: { mid: 28647 } } }] } }),
    ).toEqual({ audience: 28647, unit: 'impressions' });
  });

  it('prefers a measured audience over a forecast when a seller sends both', () => {
    expect(
      productReach({
        ext: { gotham_measurement: { reachable_audience: 100 } },
        forecast: { points: [{ metrics: { impressions: { mid: 999 } } }] },
      })?.unit,
    ).toBe('audience');
  });

  it('survives a forecast with no usable point', () => {
    expect(productReach({ forecast: { points: [] } })).toBeNull();
    expect(productReach({ forecast: { points: [{}] } })).toBeNull();
  });
});

describe('productCpm', () => {
  it('reads the quoted rate and its currency', () => {
    expect(
      productCpm({ pricing_options: [{ pricing_model: 'cpm', currency: 'USD', fixed_price: 9 }] }),
    ).toEqual({ value: 9, currency: 'USD' });
  });

  it('keeps a quoted zero, which is a price', () => {
    expect(productCpm({ pricing_options: [{ fixed_price: 0 }] })?.value).toBe(0);
  });

  it('is null when the seller priced nothing', () => {
    expect(productCpm({})).toBeNull();
    expect(productCpm({ pricing_options: [{}] })).toBeNull();
  });
});
