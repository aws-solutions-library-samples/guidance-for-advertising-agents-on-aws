import { describe, expect, it } from 'vitest';

import { buildGravity, DEFAULT_LAYOUT, jitterFor, type GravityInput } from './gravityGraph';

/**
 * A product with its metrics already read off the wire, which is what this module takes. The reading
 * itself is `products.test.ts`'s job.
 */
function node(
  key: string,
  relevance: number | null,
  reach: number | null = null,
  cpm: number | null = null,
): GravityInput {
  return {
    key,
    label: `product ${key}`,
    seller: 'gotham agent',
    relevance,
    reach,
    reachUnit: reach === null ? null : 'audience',
    cpm,
    currency: cpm === null ? null : 'USD',
  };
}

describe('jitterFor', () => {
  it('is stable for a key, so a node keeps its angle between renders', () => {
    // Math.random here would move nodes on unrelated state changes, which reads as the data moving.
    expect(jitterFor('gotham#p1#0')).toBe(jitterFor('gotham#p1#0'));
  });

  it('differs between keys, which is the point of it', () => {
    expect(jitterFor('a')).not.toBe(jitterFor('b'));
  });

  it('stays within the unit interval it promises', () => {
    for (const key of ['a', 'bb', 'gotham#p1#0', '', 'a much longer product key 12345']) {
      expect(jitterFor(key)).toBeGreaterThanOrEqual(-1);
      expect(jitterFor(key)).toBeLessThan(1);
    }
  });
});

describe('buildGravity', () => {
  it('puts the most relevant product closest to the brief', () => {
    const model = buildGravity([
      node('near', 0.44),
      node('far', 0.34),
    ])!;
    const distance = (key: string) => {
      const n = model.nodes.find((node) => node.key === key)!;
      return Math.hypot(n.x - model.centre.x, n.y - model.centre.y);
    };
    expect(distance('near')).toBeLessThan(distance('far'));
    // The best sits on the inner clearance, not on top of the centre node.
    expect(distance('near')).toBeCloseTo(DEFAULT_LAYOUT.innerRadius, 5);
    expect(distance('far')).toBeCloseTo(DEFAULT_LAYOUT.outerRadius, 5);
  });

  it('fits the radial scale to the set, because real scores cluster', () => {
    // fused_score spans about 0.34..0.44 in practice. An absolute R = Rmax * (1 - score) would put
    // both of these on nearly the same ring.
    const model = buildGravity([node('a', 0.402), node('b', 0.404)])!;
    const rs = model.nodes.map((n) => Math.hypot(n.x - model.centre.x, n.y - model.centre.y));
    expect(Math.abs(rs[0]! - rs[1]!)).toBeCloseTo(
      DEFAULT_LAYOUT.outerRadius - DEFAULT_LAYOUT.innerRadius,
      5,
    );
  });

  it('labels each ring with the score that lands on it, not a made-up percentage', () => {
    const model = buildGravity([node('a', 0.44), node('b', 0.34)])!;
    expect(model.rings).toHaveLength(4);
    // Innermost ring carries the best score, outermost the worst.
    expect(model.rings[0]!.relevance).toBeCloseTo(0.44 - 0.25 * 0.1, 6);
    expect(model.rings[3]!.relevance).toBeCloseTo(0.34, 6);
    // And the ring radii are ordered outward.
    for (let i = 1; i < model.rings.length; i += 1) {
      expect(model.rings[i]!.r).toBeGreaterThan(model.rings[i - 1]!.r);
    }
  });

  it('centres every node when the seller scored them all identically', () => {
    // A zero span must not divide by zero, and must not fan identical scores across the band.
    const model = buildGravity([node('a', 0.4), node('b', 0.4)])!;
    const rs = model.nodes.map((n) => Math.hypot(n.x - model.centre.x, n.y - model.centre.y));
    expect(rs[0]).toBeCloseTo(rs[1]!, 6);
  });

  it('sizes nodes by the square root of reach, so area tracks the figure', () => {
    const model = buildGravity([
      node('big', 0.44, 10000),
      node('quarter', 0.34, 2500),
    ])!;
    const big = model.nodes.find((n) => n.key === 'big')!;
    const quarter = model.nodes.find((n) => n.key === 'quarter')!;
    const span = DEFAULT_LAYOUT.nodeRadiusMax - DEFAULT_LAYOUT.nodeRadiusMin;
    // sqrt(2500/10000) = 0.5, so the smaller sits halfway up the radius range.
    expect(big.radius).toBeCloseTo(DEFAULT_LAYOUT.nodeRadiusMax, 5);
    expect(quarter.radius).toBeCloseTo(DEFAULT_LAYOUT.nodeRadiusMin + 0.5 * span, 5);
  });

  it('gives an unmeasured product the minimum radius and no reach', () => {
    // The component draws these dashed, so "not measured" does not read as "very small".
    const model = buildGravity([node('a', 0.44, 5000), node('b', 0.34)])!;
    const bare = model.nodes.find((n) => n.key === 'b')!;
    expect(bare.reach).toBeNull();
    expect(bare.reachUnit).toBeNull();
    expect(bare.radius).toBeCloseTo(DEFAULT_LAYOUT.nodeRadiusMin, 5);
  });

  it('places each product on the CPM ramp, cheapest to dearest', () => {
    const model = buildGravity([
      node('cheap', 0.44, 100, 5),
      node('mid', 0.4, 100, 10),
      node('dear', 0.34, 100, 15),
    ])!;
    const ramp = (key: string) => model.nodes.find((n) => n.key === key)!.cpmRamp;
    expect(ramp('cheap')).toBeCloseTo(0, 6);
    expect(ramp('mid')).toBeCloseTo(0.5, 6);
    expect(ramp('dear')).toBeCloseTo(1, 6);
    expect(model.cpmMin).toBe(5);
    expect(model.cpmMax).toBe(15);
  });

  it('leaves an unpriced product off the ramp rather than at its cheap end', () => {
    const model = buildGravity([node('a', 0.44, 100, 9), node('b', 0.34, 100)])!;
    expect(model.nodes.find((n) => n.key === 'b')!.cpmRamp).toBeNull();
  });

  it('counts products with no score rather than parking them on the outer ring', () => {
    // Placing them would assert the seller ranked them last, which it did not say.
    const model = buildGravity([
      node('scored', 0.4),
      node('unscored', null),
      node('also', null),
    ])!;
    expect(model.nodes).toHaveLength(1);
    expect(model.omitted).toBe(2);
  });

  it('draws a single scored product, since the rings carry its own score', () => {
    const model = buildGravity([node('only', 0.41, 1000, 9)])!;
    expect(model.nodes).toHaveLength(1);
    expect(model.rings[0]!.relevance).toBeCloseTo(0.41, 6);
  });

  it('is null when nothing was scored at all', () => {
    // Triton and the reference seller send no similarity today, so this is the common case.
    expect(buildGravity([node('a', null), node('b', null)])).toBeNull();
    expect(buildGravity([])).toBeNull();
  });

  it('keeps every node inside the frame', () => {
    const model = buildGravity(
      Array.from({ length: 12 }, (_, i) => node(`p${i}`, 0.3 + i * 0.01, 1000 * i, i)),
    )!;
    for (const node of model.nodes) {
      expect(node.x - node.radius).toBeGreaterThanOrEqual(0);
      expect(node.y - node.radius).toBeGreaterThanOrEqual(0);
      expect(node.x + node.radius).toBeLessThanOrEqual(DEFAULT_LAYOUT.size);
      expect(node.y + node.radius).toBeLessThanOrEqual(DEFAULT_LAYOUT.size);
    }
  });

  it('spreads nodes around the full turn rather than stacking them on one spoke', () => {
    const model = buildGravity(
      Array.from({ length: 8 }, (_, i) => node(`p${i}`, 0.4)),
    )!;
    // All at the same radius (identical scores), so distinct angles are the only thing separating
    // them. Every node must have its own position.
    const positions = new Set(model.nodes.map((n) => `${n.x.toFixed(3)},${n.y.toFixed(3)}`));
    expect(positions.size).toBe(8);
  });

  it('reports the reach unit, so audience and impressions are not conflated', () => {
    const model = buildGravity([
      node('gotham', 0.44, 187328),
      {
        key: 'triton',
        label: 'triton product',
        seller: 'triton agent',
        relevance: 0.34,
        reach: 28647,
        reachUnit: 'impressions',
        cpm: null,
        currency: null,
      },
    ])!;
    expect(model.nodes.find((n) => n.key === 'gotham')!.reachUnit).toBe('audience');
    expect(model.nodes.find((n) => n.key === 'triton')!.reachUnit).toBe('impressions');
  });
});
