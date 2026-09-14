/**
 * The inventory constellation: every product the sellers offered, arranged around the brief.
 *
 * Distance from the centre is the score the seller ranked by, node size is reach, node colour is CPM.
 * The reference rings are labelled with the real score at each radius, because the radial scale is
 * fitted to the products shown - see `lib/journey/gravityGraph.ts` for why an absolute scale collapses
 * the whole set onto one ring.
 *
 * **The tethers carry no data.** The specification this follows weights each line by an
 * `outcomeProbability`; no seller in this project returns one. They are drawn uniformly and the legend
 * says so, because a line whose thickness varied would read as a measurement.
 *
 * Layout is pure and lives in the lib module. This file is the SVG, the palette, and the three
 * interactions: hover, the CPM filter, and click-to-select.
 */

import { useMemo, useState } from 'react';

import { prefersReducedMotion, useNodeTour } from '../../../hooks/useNodeTour';

import {
  buildGravity,
  DEFAULT_LAYOUT,
  type GravityInput,
  type GravityNode,
} from '../../../lib/journey/gravityGraph';
import type { FormatGroupProduct } from '../../../lib/journey/types';

/**
 * The CPM ramp, cool to warm, from the journey palette.
 *
 * Interpolated in RGB across three stops rather than two so the middle of the range stays
 * distinguishable instead of washing through grey.
 */
const CPM_RAMP: ReadonlyArray<readonly [number, number, number]> = [
  [128, 240, 248], // --cyan, cheapest
  [140, 140, 250], // --peri
  [240, 108, 0], // --orange, dearest
];

/** A node with no quoted rate. Neutral, so absent is not read as the cheap end of the ramp. */
const NO_CPM_COLOUR = '#6f6591';

export function rampColour(t: number | null): string {
  if (t === null) return NO_CPM_COLOUR;
  const clamped = Math.max(0, Math.min(1, t));
  const scaled = clamped * (CPM_RAMP.length - 1);
  const lower = Math.min(CPM_RAMP.length - 1, Math.floor(scaled));
  const upper = Math.min(CPM_RAMP.length - 1, lower + 1);
  const f = scaled - lower;
  const a = CPM_RAMP[lower]!;
  const b = CPM_RAMP[upper]!;
  const mix = (i: number) => Math.round(a[i]! + (b[i]! - a[i]!) * f);
  return `rgb(${mix(0)}, ${mix(1)}, ${mix(2)})`;
}

function toInputs(products: readonly FormatGroupProduct[]): GravityInput[] {
  return products.map((product) => ({
    key: product.key,
    label: product.title,
    seller: product.seller,
    relevance: product.relevance ?? null,
    reach: product.reach?.audience ?? null,
    reachUnit: product.reach?.unit ?? null,
    cpm: product.cpm ?? null,
    currency: product.currency ?? null,
  }));
}

const money = (value: number, currency: string | null): string =>
  `${currency ? `${currency} ` : ''}${value.toFixed(2)} CPM`;

const REACH_WORD: Record<'audience' | 'impressions', string> = {
  audience: 'reachable',
  impressions: 'forecast impressions',
};

export function GravityGraph({
  products,
  className,
}: {
  products: readonly FormatGroupProduct[];
  /**
   * Appended to the figure's own class. The figure is a grid item in the Discover panel, and only
   * the caller knows how many format groups it is sharing that grid with, so placement is passed in
   * rather than guessed here. Kept on the figure rather than a wrapper div so that returning `null`
   * below leaves no empty grid cell behind.
   */
  className?: string;
}) {
  const inputs = useMemo(() => toInputs(products), [products]);
  const model = useMemo(() => buildGravity(inputs), [inputs]);

  // `null` means "no ceiling set", which is different from a ceiling that happens to equal the
  // dearest product: the slider has not been touched, so nothing is filtered.
  const [maxCpm, setMaxCpm] = useState<number | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);

  /**
   * Best first, so the tour presents the seller's own ranking rather than fan-out order.
   *
   * Descending score: a higher cosine is a closer node, so this walks outward from the brief. Ties
   * break on key, which keeps the order stable rather than dependent on sort implementation.
   */
  const tourOrder = useMemo(() => {
    if (model === null) return [];
    return [...model.nodes]
      .sort((a, b) => b.relevance - a.relevance || a.key.localeCompare(b.key))
      .map((node) => node.key);
  }, [model]);

  // The reason this exists: at a booth there is no pointer, and the detail panel would sit empty.
  const tour = useNodeTour(tourOrder, !prefersReducedMotion());

  if (model === null) return null;

  const { size } = DEFAULT_LAYOUT;
  const priced = model.cpmMin !== null && model.cpmMax !== null && model.cpmMax > model.cpmMin;
  const ceiling = maxCpm ?? model.cpmMax;

  // A product with no quoted rate is never filtered out by a price ceiling: the seller did not say it
  // costs more, and hiding it would be an assumption.
  const isFiltered = (node: GravityNode) =>
    ceiling !== null && node.cpm !== null && node.cpm > ceiling;

  // A real interaction beats the tour, and stops it: `takeOver` runs on the first hover, focus or
  // click. Precedence matters - a visitor who is pointing at a node must not have the panel yanked
  // away on the next tick.
  const takeOver = tour.stop;
  const active = hovered ?? selected ?? tour.key;
  const activeNode = model.nodes.find((n) => n.key === active) ?? null;

  return (
    <figure className={className ? `gravity ${className}` : 'gravity'}>
      <figcaption className="gravity-cap">
        the offer set, around your brief
        <span className="gravity-sub">
          distance = the seller&rsquo;s own ranking score, fitted to these {model.nodes.length}{' '}
          products &middot; size = reach &middot; colour = CPM
          {model.omitted > 0 ? ` \u00b7 ${model.omitted} unscored, not placed` : ''}
        </span>
      </figcaption>

      <div className="gravity-body">
        <svg
          className="gravity-svg"
          viewBox={`0 0 ${size} ${size}`}
          width={size}
          height={size}
          role="img"
          aria-label={
            `${model.nodes.length} products arranged around the brief. Ranking score ` +
            `${model.relevanceMin.toFixed(3)} at the outer ring to ${model.relevanceMax.toFixed(3)} ` +
            `at the centre. Each product's figures are listed on its own row above.`
          }
        >
          {/* Relevance tiers. Dashed and faint: reference, not data. Labelled with the real score at
              each radius, since the scale is fitted and a percentage would be invented. */}
          {model.rings.map((ring) => (
            <g key={ring.r}>
              <circle
                className="gravity-ring"
                cx={model.centre.x}
                cy={model.centre.y}
                r={ring.r}
              />
              <text
                className="gravity-ring-label"
                x={model.centre.x}
                y={model.centre.y - ring.r - 3}
                textAnchor="middle"
              >
                {ring.relevance.toFixed(3)}
              </text>
            </g>
          ))}

          {/* Tethers first, so nodes sit on top of them. */}
          {model.nodes.map((node) => (
            <line
              className={`gravity-tether${active === node.key ? ' on' : ''}${
                isFiltered(node) ? ' out' : ''
              }`}
              key={node.key}
              x1={model.centre.x}
              y1={model.centre.y}
              x2={node.x}
              y2={node.y}
            />
          ))}

          {/* The brief. The pulse is the journey's own keyframe, reused rather than redefined. */}
          <circle className="gravity-core-halo" cx={model.centre.x} cy={model.centre.y} r={13} />
          <circle className="gravity-core" cx={model.centre.x} cy={model.centre.y} r={7} />
          <text
            className="gravity-core-label"
            x={model.centre.x}
            y={model.centre.y + 26}
            textAnchor="middle"
          >
            your brief
          </text>

          {model.nodes.map((node) => (
            <circle
              className={[
                'gravity-node',
                active === node.key ? 'on' : '',
                tour.key === node.key && hovered === null && selected === null ? 'toured' : '',
                selected === node.key ? 'picked' : '',
                isFiltered(node) ? 'out' : '',
                // Dashed when nothing measured its reach, so the minimum radius is not read as a
                // measured near-zero.
                node.reach === null ? 'unmeasured' : '',
              ]
                .filter(Boolean)
                .join(' ')}
              key={node.key}
              cx={node.x}
              cy={node.y}
              r={node.radius}
              style={{ stroke: rampColour(node.cpmRamp) }}
              tabIndex={0}
              role="button"
              aria-label={node.label}
              aria-pressed={selected === node.key}
              onMouseEnter={() => {
                takeOver();
                setHovered(node.key);
              }}
              onMouseLeave={() => setHovered(null)}
              onFocus={() => {
                takeOver();
                setHovered(node.key);
              }}
              onBlur={() => setHovered(null)}
              onClick={() => {
                takeOver();
                setSelected((current) => (current === node.key ? null : node.key));
              }}
              onKeyDown={(event) => {
                if (event.key !== 'Enter' && event.key !== ' ') return;
                event.preventDefault();
                takeOver();
                setSelected((current) => (current === node.key ? null : node.key));
              }}
            />
          ))}
        </svg>

        <div className="gravity-side">
          {/* Every metric the product actually carries, and nothing standing in for one it does not. */}
          {activeNode === null ? (
            <p className="gravity-hint">
              Hover or select a product to read its figures. {model.nodes.length} placed
              {model.omitted > 0 ? `, ${model.omitted} unscored` : ''}.
            </p>
          ) : (
            <dl className="gravity-detail">
              {tour.running && hovered === null && selected === null ? (
                <>
                  <dt className="gravity-touring">showing</dt>
                  <dd className="gravity-touring">
                    {tourOrder.indexOf(activeNode.key) + 1} of {tourOrder.length}, best first
                  </dd>
                </>
              ) : null}
              <dt>product</dt>
              <dd>{activeNode.label}</dd>
              <dt>seller</dt>
              <dd>{activeNode.seller}</dd>
              <dt>ranking score</dt>
              <dd>{activeNode.relevance.toFixed(3)}</dd>
              {activeNode.reach !== null && activeNode.reachUnit !== null ? (
                <>
                  <dt>{REACH_WORD[activeNode.reachUnit]}</dt>
                  <dd>{activeNode.reach.toLocaleString()}</dd>
                </>
              ) : null}
              {activeNode.cpm !== null ? (
                <>
                  <dt>rate</dt>
                  <dd>{money(activeNode.cpm, activeNode.currency)}</dd>
                </>
              ) : null}
            </dl>
          )}

          {priced ? (
            <label className="gravity-filter">
              <span className="gravity-filter-label">
                max CPM{' '}
                <b>
                  {(ceiling ?? model.cpmMax!).toFixed(2)}
                  {model.nodes.some(isFiltered)
                    ? ` \u00b7 ${model.nodes.filter(isFiltered).length} hidden`
                    : ''}
                </b>
              </span>
              <input
                type="range"
                min={model.cpmMin!}
                max={model.cpmMax!}
                step={(model.cpmMax! - model.cpmMin!) / 100}
                value={ceiling ?? model.cpmMax!}
                onChange={(event) => setMaxCpm(Number(event.target.value))}
              />
            </label>
          ) : null}

          {/* Says which channels are driven by data. Without this the uniform tethers look like a
              measurement that happens to be constant. */}
          <p className="gravity-legend">
            Tether weight is not encoded: no seller here returns an outcome probability.
          </p>
        </div>
      </div>
    </figure>
  );
}
