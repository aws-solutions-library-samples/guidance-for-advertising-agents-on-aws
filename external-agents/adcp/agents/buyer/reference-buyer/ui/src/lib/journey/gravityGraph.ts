/**
 * The inventory constellation: products arranged around the brief they answered.
 *
 * Radial distance carries relevance, node size carries reach, node colour carries CPM. The brief sits
 * at the centre and every product is tethered to it.
 *
 * ## What each channel is driven by, and the one that is not
 *
 * | channel | source | present for |
 * |---|---|---|
 * | radius from centre | `ext.gotham_measurement.fused_score` (the score that ordered the list) | Gotham |
 * | node size | `reachable_audience`, or Triton's `forecast.points[].metrics.impressions.mid` | Gotham, Triton |
 * | node colour | `pricing_options[0].fixed_price` | every seller |
 * | tether weight | **nothing** | - |
 *
 * The spec this was built from weights each tether by an `outcomeProbability`. No seller in this
 * project returns one: there is no conversion rate, no per-product confidence, and the one
 * `confidence` field that exists belongs to governance findings and is always 1.0. So the tethers are
 * drawn uniformly and the legend says the weight is unencoded. A tether whose thickness varied would
 * read as a measurement, and inventing the strongest visual signal on the chart is exactly the
 * failure the rest of this panel is built to avoid.
 *
 * ## Why the radial scale is fitted rather than absolute
 *
 * The spec's `R = R_max * (1 - relevanceScore)` assumes relevance spans most of `0..1`. Measured
 * across every recorded Gotham row, `fused_score` spans roughly 0.34 to 0.44 - so an absolute mapping
 * puts every product on one ring at about 0.6 R_max, which is a bullseye rather than a constellation.
 * The scale is therefore fitted to the products shown, and the reference rings are labelled with the
 * real score at each radius instead of invented "80% match" tiers. A reader who thought the rings were
 * absolute would read a 0.1 spread as the whole range.
 *
 * Pure: no DOM, no randomness. Angular jitter is derived from the product key, so a node keeps its
 * position between renders instead of jumping on every state change.
 */

import type { ReachUnit } from '../products';

/**
 * One product, with its metrics already read off the wire.
 *
 * Extraction lives in `lib/products.ts` (`productRelevance`, `productReach`, `productCpm`) alongside
 * every other wire reader; this module does layout only. `relevance` is required because a product
 * without it has no radial position at all - the caller drops those and counts them.
 */
export interface GravityInput {
  readonly key: string;
  readonly label: string;
  readonly seller: string;
  readonly relevance: number | null;
  readonly reach: number | null;
  readonly reachUnit: ReachUnit | null;
  readonly cpm: number | null;
  readonly currency: string | null;
}

export interface GravityNode {
  readonly key: string;
  readonly label: string;
  readonly seller: string;
  /** The ordering score as returned, printed verbatim in the tooltip. */
  readonly relevance: number;
  readonly reach: number | null;
  readonly reachUnit: ReachUnit | null;
  readonly cpm: number | null;
  readonly currency: string | null;
  /** Position in SVG user units. */
  readonly x: number;
  readonly y: number;
  /** Node ring radius, area-proportional to reach. */
  readonly radius: number;
  /**
   * Position on the CPM ramp, `0` cheapest to `1` dearest, or `null` when the seller quoted no price.
   * Null must render as a neutral tint rather than the cheap end of the ramp.
   */
  readonly cpmRamp: number | null;
}

export interface GravityRing {
  /** Radius in SVG user units. */
  readonly r: number;
  /** The relevance score that lands on this ring, in real units. */
  readonly relevance: number;
}

export interface GravityModel {
  readonly nodes: readonly GravityNode[];
  readonly rings: readonly GravityRing[];
  readonly centre: { readonly x: number; readonly y: number };
  readonly relevanceMin: number;
  readonly relevanceMax: number;
  readonly cpmMin: number | null;
  readonly cpmMax: number | null;
  readonly reachMax: number | null;
  /**
   * Products the seller returned that carry no relevance score.
   *
   * Not placed. A node with no score has no radial position, and parking it on the outer ring would
   * assert that the seller ranked it last. Reported instead, so a constellation of three for a list
   * of ten does not read as a rendering fault.
   */
  readonly omitted: number;
}

export interface GravityLayout {
  readonly size: number;
  /** Clearance around the centre node, so a top-ranked product does not sit on top of it. */
  readonly innerRadius: number;
  /** Outermost orbit, inset from the frame by the largest node radius plus its label. */
  readonly outerRadius: number;
  readonly nodeRadiusMin: number;
  readonly nodeRadiusMax: number;
}

export const DEFAULT_LAYOUT: GravityLayout = {
  size: 320,
  innerRadius: 34,
  outerRadius: 132,
  nodeRadiusMin: 3.5,
  nodeRadiusMax: 13,
};

/** Rings at these fractions of the orbit band. Three tiers, as the spec asks for. */
const RING_FRACTIONS = [0.25, 0.5, 0.75, 1] as const;

/** Fraction of a node's angular slice it may be nudged by, to break up spoke alignment. */
const JITTER_SHARE = 0.3;

/**
 * A stable pseudo-random offset in `[-1, 1)` derived from a string.
 *
 * Deliberately not `Math.random`: a node must keep its angle across re-renders, and a position that
 * moves when an unrelated bit of state changes reads as the data having changed. FNV-1a, for no
 * reason other than being short and well spread.
 */
export function jitterFor(key: string): number {
  let hash = 0x811c9dc5;
  for (let i = 0; i < key.length; i += 1) {
    hash ^= key.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  // >>> 0 to read it unsigned; the division maps it onto [0, 1), then onto [-1, 1).
  return ((hash >>> 0) / 0xffffffff) * 2 - 1;
}


/** Normalise into `0..1`, centring when every value is identical rather than dividing by zero. */
function fit(value: number, min: number, max: number): number {
  return max === min ? 0.5 : (value - min) / (max - min);
}

/**
 * Lay the products out around the brief, or `null` when none carries a relevance score.
 *
 * One product is still drawn: unlike the scatter this replaced, a single node against labelled rings
 * says something real - where that product sat relative to the brief - because the rings carry its
 * own score.
 */
export function buildGravity(
  inputs: readonly GravityInput[],
  layout: GravityLayout = DEFAULT_LAYOUT,
): GravityModel | null {
  const scored: Array<GravityInput & { relevance: number }> = [];
  let omitted = 0;

  for (const item of inputs) {
    if (item.relevance === null || !Number.isFinite(item.relevance)) {
      omitted += 1;
      continue;
    }
    scored.push({ ...item, relevance: item.relevance });
  }

  if (scored.length === 0) return null;

  const relevances = scored.map((s) => s.relevance);
  const relevanceMin = Math.min(...relevances);
  const relevanceMax = Math.max(...relevances);

  const reaches = scored.map((s) => s.reach).filter((v): v is number => v !== null);
  const reachMax = reaches.length > 0 ? Math.max(...reaches) : null;

  const cpms = scored.map((s) => s.cpm).filter((v): v is number => v !== null);
  const cpmMin = cpms.length > 0 ? Math.min(...cpms) : null;
  const cpmMax = cpms.length > 0 ? Math.max(...cpms) : null;

  const centre = { x: layout.size / 2, y: layout.size / 2 };
  const band = layout.outerRadius - layout.innerRadius;
  const slice = (Math.PI * 2) / scored.length;

  const nodes: GravityNode[] = scored.map((s, index) => {
    // Highest relevance closest in, per the spec's inverse mapping - fitted to this set.
    const orbit = layout.innerRadius + (1 - fit(s.relevance, relevanceMin, relevanceMax)) * band;
    const theta = slice * index + jitterFor(s.key) * slice * JITTER_SHARE;

    // Area-proportional, so a node twice the width is not read as twice the reach. A product with no
    // reach figure gets the minimum, and the component draws it dashed so "not measured" is not read
    // as "very small".
    const reachFraction =
      s.reach !== null && reachMax !== null && reachMax > 0 ? Math.sqrt(s.reach / reachMax) : 0;
    const radius =
      layout.nodeRadiusMin + reachFraction * (layout.nodeRadiusMax - layout.nodeRadiusMin);

    return {
      key: s.key,
      label: s.label,
      seller: s.seller,
      relevance: s.relevance,
      reach: s.reach,
      reachUnit: s.reachUnit,
      cpm: s.cpm,
      currency: s.currency,
      x: centre.x + orbit * Math.cos(theta),
      y: centre.y + orbit * Math.sin(theta),
      radius,
      cpmRamp:
        s.cpm !== null && cpmMin !== null && cpmMax !== null ? fit(s.cpm, cpmMin, cpmMax) : null,
    };
  });

  // Each ring labelled with the score that lands on it, because the scale is fitted and a percentage
  // would be invented. The mapping is the inverse of `orbit` above.
  const rings: GravityRing[] = RING_FRACTIONS.map((fraction) => ({
    r: layout.innerRadius + band * fraction,
    relevance: relevanceMax - fraction * (relevanceMax - relevanceMin),
  }));

  return {
    nodes,
    rings,
    centre,
    relevanceMin,
    relevanceMax,
    cpmMin,
    cpmMax,
    reachMax,
    omitted,
  };
}
