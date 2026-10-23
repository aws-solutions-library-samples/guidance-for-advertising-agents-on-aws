/**
 * Deriving what a product card should show.
 *
 * The governing rule: an element appears only when the field behind it is present. A product the
 * seller priced shows a price; one it did not shows no chip rather than $0. A `reachable_audience`
 * of 0 is a measurement and is shown, while a missing one is omitted, because "no reachable
 * readers" and "we could not ask" are different statements and a buyer acting on the second as
 * though it were the first would conclude the inventory is worthless.
 *
 * This mirrors the discipline the sellers already apply when building these payloads; see
 * gotham_product_search.to_adcp_product.
 */

import type { FormatRef, Product, SellerMeasurement } from './types';

/** A format's mark: stroke-only SVG path data plus the token to tint it with. */
export interface FormatIcon {
  color: string;
  path: string;
}

/**
 * Stroke-only marks, one per medium. Filled shapes and emoji are both out per the style guide.
 */
export const FORMAT_ICONS: Record<string, FormatIcon> = {
  display: {
    color: 'var(--color-blue)',
    path: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 15l5-4 4 3 3-2 6 4"/>',
  },
  video: {
    color: 'var(--color-purple)',
    path: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M10 9.5l5 2.5-5 2.5z"/>',
  },
  newsletter: {
    color: 'var(--color-orange)',
    path: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/>',
  },
  podcast: {
    color: 'var(--color-pink)',
    path: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
  },
  audio: {
    color: 'var(--color-pink)',
    path: '<path d="M4 12v-2M8 16V8M12 19V5M16 16V8M20 12v-2"/>',
  },
  native: {
    color: 'var(--color-green)',
    path: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 10h6M7 14h10"/>',
  },
};

/** Drawn for a format whose medium is not recognised, rather than guessing one. */
export const GENERIC_ICON: FormatIcon = {
  color: 'var(--color-muted)',
  path: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 12h10"/>',
};

export interface FormatInfo {
  /** The format id exactly as the seller sent it, or null when it sent none. */
  id: string | null;
  icon: FormatIcon;
}

/**
 * The product's format and the mark to draw for it.
 *
 * The medium is matched against the id's underscore-separated segments, so `gotham_video`,
 * `audio_30s` and a future `nyt_display_300x250` all resolve without a per-seller table. An id with
 * no recognisable segment draws the generic mark rather than being forced into one of the known
 * media.
 *
 * Segment matching rather than prefix/suffix matching, because both of those miss real formats.
 * The vanilla UI tested `endsWith(medium) || includes('_' + medium)`, and triton's `audio_30s`
 * satisfies neither: the medium is the *first* segment, so it silently drew the generic mark. A
 * test written against the real format ids caught it.
 *
 * Segments are checked in the order they appear in the id, so an id naming more than one medium
 * resolves by its own leading term rather than by the order of this table.
 */
export function productFormatInfo(product: Product): FormatInfo {
  const first = product.format_ids?.[0];
  const id = typeof first === 'string' ? first : (first?.id ?? null);
  if (!id) return { id: null, icon: GENERIC_ICON };

  for (const segment of id.toLowerCase().split('_')) {
    const icon = FORMAT_ICONS[segment];
    if (icon) return { id, icon };
  }
  return { id, icon: GENERIC_ICON };
}

/**
 * The seller's rate, formatted, or null when the product carries no usable pricing.
 *
 * Null means no chip is drawn at all. A product with pricing_options whose fixed_price is missing or
 * non-numeric is treated as unpriced rather than shown as zero.
 */
export function productPriceLabel(product: Product): string | null {
  const option = product.pricing_options?.[0];
  if (!option) return null;
  const price = Number(option.fixed_price);
  if (!Number.isFinite(price)) return null;

  const currency = option.currency ? `${option.currency} ` : '';
  const model = option.pricing_model ? ` ${option.pricing_model.toUpperCase()}` : '';
  const amount = price.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  return `${currency}${amount}${model}`;
}

/**
 * The seller's rate, abbreviated for a space too narrow for `productPriceLabel`'s full form.
 *
 * Whole currency units, no pricing model suffix: `"USD 16"` rather than `"USD 16.00 CPM"`. Rounded
 * for display only, never re-derived from a different source, so the peek badge and the detail
 * panel's full label always agree on the underlying number.
 */
export function productPriceShort(product: Product): string | null {
  const option = product.pricing_options?.[0];
  if (!option) return null;
  const price = Number(option.fixed_price);
  if (!Number.isFinite(price)) return null;

  const currency = option.currency ? `${option.currency} ` : '';
  return `${currency}${Math.round(price).toLocaleString()}`;
}

export interface SimilarityBar {
  axis: 'content' | 'audience';
  /** The score as returned, printed verbatim. */
  value: number;
  /** Clamped to 0..1 for the bar's width only, so an out-of-range score stays visible as text. */
  fraction: number;
}

/**
 * The dual-vector scores, when the seller returned them.
 *
 * The two axes are never merged into one number: which of them is strong is the interesting part.
 * A score of exactly 0 is a measurement and is included; a missing score is omitted.
 */
export function productSimilarityBars(product: Product): SimilarityBar[] {
  const m: SellerMeasurement | undefined = product.ext?.gotham_measurement;
  if (!m) return [];

  const candidates: Array<[SimilarityBar['axis'], number | undefined]> = [
    ['content', m.content_similarity],
    ['audience', m.audience_similarity],
  ];

  const bars: SimilarityBar[] = [];
  for (const [axis, raw] of candidates) {
    const value = Number(raw);
    if (!Number.isFinite(value)) continue;
    bars.push({ axis, value, fraction: Math.max(0, Math.min(1, value)) });
  }
  return bars;
}

/**
 * What a reach figure counts.
 *
 * The two sellers measure different things and must not be conflated: Gotham's reach service returns
 * people, Triton's `DeliveryForecast` returns impressions. One number labelled as the other overstates
 * or understates an audience by whatever the frequency happens to be.
 */
export type ReachUnit = 'audience' | 'impressions';

export interface ReachInfo {
  audience: number;
  readonly unit: ReachUnit;
}

/**
 * A count, abbreviated to fit a space too narrow for `toLocaleString`'s full digit run.
 *
 * One decimal place below ten of the unit (`"24.6K"`), none at or above it (`"368K"`), because a
 * second significant digit stops being distinguishable from noise once the whole number is that
 * large. A trailing `.0` is stripped rather than shown, so a round figure like 5,000,000 reads as
 * `"5M"` rather than `"5.0M"`.
 */
export function formatCompactCount(value: number): string {
  if (!Number.isFinite(value)) return '';
  const sign = value < 0 ? '-' : '';
  const abs = Math.abs(value);

  const scale = (divisor: number, suffix: string) => {
    const scaled = abs / divisor;
    const rendered = scaled >= 10 ? Math.round(scaled).toString() : scaled.toFixed(1);
    return `${sign}${rendered.replace(/\.0$/, '')}${suffix}`;
  };

  if (abs >= 1_000_000) return scale(1_000_000, 'M');
  if (abs >= 1_000) return scale(1_000, 'K');
  return `${sign}${abs}`;
}

/**
 * Reach, only when the seller supplied it.
 *
 * Zero is a figure and is returned.
 *
 * `reach_label` is deliberately not read. The reach service sends "synthetic sandbox figure — The
 * Gotham Gazette is a fictional publication" with every figure, and rendering it put that sentence
 * on every row of the panel. The seller's own name already reads "The Gotham Gazette (sandbox,
 * MCP)" on the same row, so the sentence repeated what was next to it. The field stays on the wire,
 * where FR-6.4 wants it and where a client can read it.
 */
export function productReach(product: Product): ReachInfo | null {
  const measured = Number(product.ext?.gotham_measurement?.reachable_audience);
  if (Number.isFinite(measured)) return { audience: measured, unit: 'audience' };

  // Triton's native AdCP `DeliveryForecast`. Read defensively: a seller may send a forecast with no
  // points, or a point carrying a different metric.
  const forecast = product.forecast as
    | { points?: Array<{ metrics?: { impressions?: { mid?: unknown } } }> }
    | undefined;
  for (const point of forecast?.points ?? []) {
    const mid = Number(point?.metrics?.impressions?.mid);
    if (Number.isFinite(mid)) return { audience: mid, unit: 'impressions' };
  }
  return null;
}

/**
 * The score the seller ranked this product by, or null.
 *
 * `fused_score` first, because that is the number that actually ordered the list; `content_similarity`
 * is the fallback for a single-axis answer, where the fused score equals it anyway.
 *
 * `relevance_score` is deliberately NOT read. It is `satisfied / stated` constraints against a strict
 * AND filter, so it is 1.0 on every product that comes back. Using it would claim a perfect match for
 * all of them.
 */
/**
 * The keys a seller may report its ranking score under, in the order they are preferred.
 *
 * `fused_score` beats `content_similarity` because a dual-vector seller ranked by the fused number.
 * `cosine` is the single-vector form. All three are cosines on the same `-1..1` scale, which is what
 * makes one list rather than a per-seller branch correct — a different metric could not share it.
 */
const RELEVANCE_KEYS = ['fused_score', 'content_similarity', 'cosine'] as const;

/** Vendor-namespaced measurement blocks, e.g. `gotham_measurement`, `triton_measurement`. */
function measurementBlocks(product: Product): Array<Record<string, unknown>> {
  const blocks: Array<Record<string, unknown>> = [];
  for (const [key, value] of Object.entries(product.ext ?? {})) {
    if (!key.endsWith('_measurement')) continue;
    if (typeof value !== 'object' || value === null || Array.isArray(value)) continue;
    blocks.push(value as Record<string, unknown>);
  }
  return blocks;
}

export function productRelevance(product: Product): number | null {
  // Any `*_measurement` block, not `gotham_measurement` by name: Gotham was the only seller emitting
  // a score for a while, and hardcoding its namespace is what kept Triton's products off the
  // constellation after it started sending one.
  for (const block of measurementBlocks(product)) {
    for (const key of RELEVANCE_KEYS) {
      const value = Number(block[key]);
      if (block[key] !== undefined && Number.isFinite(value)) return value;
    }
  }
  return null;
}

export interface CpmInfo {
  readonly value: number;
  readonly currency: string | null;
}

/** The quoted rate as a number, for scales. Zero is a price; absent is not. */
export function productCpm(product: Product): CpmInfo | null {
  const option = product.pricing_options?.[0];
  const value = Number(option?.fixed_price);
  if (!Number.isFinite(value)) return null;
  return { value, currency: option?.currency ?? null };
}

/** The product's display title, falling back to its id rather than to placeholder text. */
export function productTitle(product: Product): string {
  return product.name || product.product_id || 'Unnamed product';
}

/** One labelled characteristic of a product, ready to render. */
export interface ProductFact {
  /** Stable within a product, so React can key on it. */
  readonly key: string;
  /**
   * Shown before the value when the value alone would not identify it. Absent for values that are
   * self-describing, like `300x250` or `right-rail`.
   */
  readonly label?: string;
  readonly value: string;
}

/** A format's dimensions, when the seller parameterised its format reference with them. */
function dimensionsFact(ref: FormatRef): ProductFact | null {
  const { width, height } = ref;
  if (!Number.isFinite(width) || !Number.isFinite(height)) return null;
  return { key: 'dimensions', value: `${width}\u00d7${height}` };
}

/**
 * A format's duration, from `duration_ms`.
 *
 * Rendered in seconds because that is how every format id in this repo names it (`audio_30s`), and
 * whole seconds when it divides evenly so a 30s spot does not read as `30.0s`.
 */
function durationFact(ref: FormatRef): ProductFact | null {
  const ms = Number(ref.duration_ms);
  if (!Number.isFinite(ms)) return null;
  const seconds = ms / 1000;
  const rendered = Number.isInteger(seconds) ? `${seconds}` : seconds.toFixed(1);
  return { key: 'duration', value: `${rendered}s` };
}

/** The first format reference in structured form, or null when the seller sent a bare string or none. */
function structuredFormatRef(product: Product): FormatRef | null {
  const first = product.format_ids?.[0];
  return typeof first === 'object' && first !== null ? first : null;
}

function cleanStrings(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const out: string[] = [];
  for (const item of value) {
    if (typeof item !== 'string') continue;
    const trimmed = item.trim();
    if (trimmed.length > 0) out.push(trimmed);
  }
  return out;
}

/**
 * Accept a `{label, value}` pair, from wherever it came.
 *
 * Both halves or neither: a value with no label cannot be read, and a label with no value is the
 * seller saying it has nothing for that characteristic.
 */
function pushSpecification(
  push: (fact: ProductFact | null) => void,
  keyPrefix: string,
  entry: unknown,
): void {
  if (typeof entry !== 'object' || entry === null) return;
  const { label, value } = entry as { label?: unknown; value?: unknown };
  if (typeof label !== 'string' || typeof value !== 'string') return;
  const trimmedLabel = label.trim();
  const trimmedValue = value.trim();
  if (!trimmedLabel || !trimmedValue) return;
  push({ key: `${keyPrefix}:${trimmedLabel}`, label: trimmedLabel, value: trimmedValue });
}

function listFact(key: string, label: string | undefined, value: unknown): ProductFact | null {
  const items = cleanStrings(value);
  if (items.length === 0) return null;
  return { key, ...(label ? { label } : {}), value: items.join(', ') };
}

/**
 * Seller-specific top-level fields, read by name.
 *
 * These are NOT AdCP fields. Triton puts `market`, `genre`, `positions` and friends at the top level
 * of its products, which AdCP permits (`Product` is `extra='allow'`) but does not define, so no
 * generic rule finds them. Listed explicitly rather than by reflecting over unknown keys, because
 * reflection would also surface `relevance_score`, `forecast` and anything else a seller adds, and
 * label internal scoring as a product characteristic.
 */
const SELLER_TOP_LEVEL: ReadonlyArray<{ field: string; key: string; label?: string }> = [
  { field: 'market', key: 'market' },
  { field: 'group', key: 'group', label: 'group' },
  { field: 'genre', key: 'genre' },
  { field: 'content_types', key: 'content_types' },
  { field: 'positions', key: 'positions' },
  { field: 'language', key: 'language' },
];

/**
 * Every characteristic the seller stated about this product, in the order a reader wants them.
 *
 * The Discover panel showed only title, seller, format id and price, which made ten products that
 * differ by placement, edition and device render as ten identical rows. Everything here was already
 * on the wire and being discarded.
 *
 * Ordered most-distinguishing first: what varies between two rows of the same format comes before
 * what they share. Placement and the seller's own specifications lead; the format id and price stay
 * where they were, appended by the caller.
 *
 * Sources, all of them AdCP's own except where noted:
 *
 *   - `placements[].name` — position on the page. AdCP defines no enum for it, so the name and
 *     `tags` are the vocabulary (see `core/placement.json`).
 *   - `format_ids[0].width`/`height`/`duration_ms` — AdCP's parameterisation of a template format.
 *   - `video_placement_types`, `audio_distribution_types`, `social_placement_surfaces`,
 *     `sponsored_placement_types` — the four OpenRTB-derived enums a product may declare.
 *   - `product_card_detailed.specifications[]` — a labelled-fact list the SDK's own product model
 *     defines. Note that the 3.0/3.1 WIRE schemas make `product_card_detailed` a format-rendered
 *     card (`{format_id, manifest}`) with no `specifications`, so a seller cannot currently put
 *     facts there and pass validation. Read anyway, because it costs nothing and is where these
 *     belong if a later revision restores it.
 *   - `ext.<vendor>_*` arrays of `{label, value}` — matched BY SHAPE, not by key name. AdCP requires
 *     `ext` keys to be vendor-namespaced, so there is no well-known name to look for; what is
 *     recognisable is the pair shape, which is AdCP's own `Specification`. This is how a seller
 *     states something AdCP has no field for at all: Gotham sends edition, environment and language
 *     as `ext.gotham_attributes`. Shape-matching also excludes `ext.gotham_measurement`, an object
 *     of scores rather than a list of pairs, which is the point — reflecting over `ext` by name
 *     would label internal scoring as a product characteristic.
 *   - `channels`, `delivery_type`.
 *   - Triton's non-standard top-level fields, listed in `SELLER_TOP_LEVEL`.
 *
 * `description` is deliberately NOT parsed. Gotham composes it by joining whichever of placement,
 * edition and device it has, so a row missing one yields a shorter string with no positional
 * marker: a positional parse would confidently mis-assign edition as placement. The fix for that
 * belongs in the seller, which is where these fields exist structurally.
 */
export function productFacts(product: Product): ProductFact[] {
  const facts: ProductFact[] = [];
  // Deduplicated on what a reader would SEE, because two carriers legitimately hold the same value:
  // triton's `content_types: ["podcast"]` and `channels: ["podcast"]` both describe one podcast
  // product, and printing "podcast · podcast" reads as a bug rather than as corroboration.
  const seen = new Set<string>();
  const push = (fact: ProductFact | null) => {
    if (!fact) return;
    const rendered = `${fact.label ?? ''}\u0000${fact.value}`;
    if (seen.has(rendered)) return;
    seen.add(rendered);
    facts.push(fact);
  };

  const placementNames = cleanStrings(product.placements?.map((p) => p.name));
  if (placementNames.length > 0) {
    push({ key: 'placement', value: placementNames.join(', ') });
  }

  for (const spec of product.product_card_detailed?.specifications ?? []) {
    pushSpecification(push, 'spec', spec);
  }
  for (const [extKey, extValue] of Object.entries(product.ext ?? {})) {
    if (!Array.isArray(extValue)) continue;
    for (const entry of extValue) pushSpecification(push, extKey, entry);
  }

  const ref = structuredFormatRef(product);
  if (ref) {
    push(dimensionsFact(ref));
    push(durationFact(ref));
  }

  push(listFact('video_placement', undefined, product.video_placement_types));
  push(listFact('audio_distribution', undefined, product.audio_distribution_types));
  push(listFact('social_surface', undefined, product.social_placement_surfaces));
  push(listFact('sponsored_placement', undefined, product.sponsored_placement_types));

  for (const { field, key, label } of SELLER_TOP_LEVEL) {
    const raw = product[field];
    if (Array.isArray(raw)) {
      push(listFact(key, label, raw));
    } else if (typeof raw === 'string' && raw.trim().length > 0) {
      push({ key, ...(label ? { label } : {}), value: raw.trim() });
    }
  }

  push(listFact('channels', undefined, product.channels));

  // Last of the characteristics: it is the same for every product from a seller that sells one way,
  // so it distinguishes nothing between rows and only matters once a reader is looking at one.
  const delivery = product.delivery_type?.trim();
  if (delivery) push({ key: 'delivery', value: delivery.replace(/_/g, '-') });

  return facts;
}

/**
 * How many of how many the recorder kept, when it trimmed the list.
 *
 * Null when nothing was trimmed, so the note is absent.
 */
export function productsTrimmedNote(
  shown: number,
  truncated: { total?: number } | undefined,
): string | null {
  const total = Number(truncated?.total);
  if (!Number.isFinite(total) || total <= shown) return null;
  return `${shown} of ${total} recorded`;
}
