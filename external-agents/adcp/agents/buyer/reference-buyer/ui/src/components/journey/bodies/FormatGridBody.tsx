/**
 * The Discover panel's product list.
 *
 * One group per format the sellers actually returned, and within each group a plain list of rows,
 * one per product — the format's mark, then the product's own title, then which seller returned it
 * and whatever else that seller supplied. The counts and the labels are both derived, so what is on
 * screen is what is in the record.
 *
 * A long list scrolls inside a box capped to the same height as the constellation below it
 * (`SCROLL_MAX_HEIGHT_PX`, read from the graph's own layout constant rather than a second copy of
 * the number), and autoscrolls slowly and infinitely — a plain CSS animation on a doubled copy of the
 * rows, not a JS timer or a cycling state machine. There is nothing here to pause, resume, or get
 * out of sync with itself: the animation is a `transform: translateY` loop the browser drives on its
 * own, and `prefers-reduced-motion` turns it off the same way every other motion in this file is
 * turned off.
 *
 * Two carousel-style layouts were tried here before this and both were reverted: an overlapping
 * receding stack, whose collapsed cards could show nothing but a synthetic price badge because the
 * card in front of them covered everything real; and a horizontal filmstrip, whose collapsed cards
 * were too narrow to hold anything legible. Both replaced a list that was working. A plain
 * auto-scrolling list keeps every product's full information visible exactly once it scrolls past,
 * with no per-card state, no pause/resume to get backwards, and no card that has to stand in for
 * content it cannot show.
 *
 * The icons keep the authored `.dot-t` treatment and its reveal transition; the label column and the
 * scrolling box are ours (`styles/journey-products.css`), since `journey.css` is diffed against the
 * prototype.
 *
 * A group is only ever passed here with at least one product: the deriver drops empty ones, because
 * an empty group would assert "this format was offered and has nothing", which is a different claim
 * from "this format was not part of the answer".
 */

import { useEffect, useMemo, useState } from 'react';

import { DEFAULT_LAYOUT } from '../../../lib/journey/gravityGraph';
import type { FormatKey } from '../../../lib/journey/phases';
import type { DiscoverData, FormatGroupProduct } from '../../../lib/journey/types';
import type { SimilarityBar } from '../../../lib/products';
import { FormatIcon } from '../icons';
import { GravityGraph } from './GravityGraph';

/** Stagger between rows appearing, from the prototype's own `i * 32`. */
const DOT_STAGGER_MS = 32;

/**
 * The scroll box's max height, matched to the constellation's own square footprint rather than to a
 * second, independently tuned number. `GravityGraph` draws at `DEFAULT_LAYOUT.size` regardless of how
 * many products it places, so this is stable across sessions.
 */
const SCROLL_MAX_HEIGHT_PX = DEFAULT_LAYOUT.size;

/**
 * The per-axis similarities as figures, with the distribution left to the group's scatter.
 *
 * Two per-row visuals were tried and both removed: a progress bar, then an angle dial. Each drew the
 * row on an absolute scale, and the real values are too close together for that to say anything —
 * content similarity spans 0.412 to 0.457 across every recorded Gotham row, so ten rows produced ten
 * identical marks. The figures below carry the precision; `SimilarityScatter` carries the comparison.
 *
 * Three decimals, because the two axes routinely differ only there (0.460 against 0.463).
 */
function SimilarityFigures({ bars }: { bars: readonly SimilarityBar[] }) {
  return (
    <span
      className="fmtitem-sims"
      title={
        'Cosine similarity between the brief and this unit\u2019s own description, per axis. ' +
        'Ranges \u22121 to 1 \u2014 not a percentage, and not a share of an audience.'
      }
    >
      <span className="fmtitem-sims-label">brief similarity</span>
      {bars.map((bar) => (
        <span className={`fmtitem-sim ${bar.axis}`} key={bar.axis}>
          <span className="fmtitem-sim-axis">{bar.axis}</span>{' '}
          <span className="fmtitem-sim-value">{bar.value.toFixed(3)}</span>
        </span>
      ))}
    </span>
  );
}

function ProductRow({
  product,
  formatKey,
  on,
  duplicate,
}: {
  product: FormatGroupProduct;
  formatKey: FormatKey;
  on: boolean;
  /** True for a row in the second, autoscroll-only copy of the list; see ProductGroupList. */
  duplicate?: boolean;
}) {
  return (
    <li className={`fmtitem${on ? ' on' : ''}`} aria-hidden={duplicate ? 'true' : undefined}>
      <div className="dot-t fill on">
        <FormatIcon format={formatKey} />
      </div>
      <div className="fmtitem-text">
        <span className="fmtitem-title">{product.title}</span>
        <span className="fmtitem-meta">
          {/* Attribution first, and never omitted: the one detail whose absence would let a reader
              assign a product to the wrong seller. */}
          <span className="fmtitem-seller">{product.seller}</span>
          {/* Then what the seller said about THIS product — placement, edition, dimensions,
              duration, market, whichever it stated. These come before the format id and the price
              because they are what differs between two rows of the same format. */}
          {product.facts?.map((fact) => (
            <span className="fmtitem-fact" key={fact.key}>
              {fact.label ? <span className="fmtitem-fact-label">{fact.label} </span> : null}
              {fact.value}
            </span>
          ))}
          {/* The remaining details appear only when the seller sent them. No placeholder stands in
              for one it did not — see FormatGroupProduct. */}
          {product.formatId ? <span className="fmtitem-fact">{product.formatId}</span> : null}
          {product.price ? <span className="fmtitem-fact">{product.price}</span> : null}
        </span>
        {/* Measurements get their own line, because they are the seller's numbers rather than its
            description of the inventory, and a bar sitting inline with the text reads as
            decoration. Absent entirely when the seller measured nothing. */}
        {product.reach || product.similarity ? (
          <span className="fmtitem-measures">
            {product.reach ? (
              <span className="fmtitem-reach">
                <b>{product.reach.audience.toLocaleString()}</b> reachable
              </span>
            ) : null}
            {product.similarity ? <SimilarityFigures bars={product.similarity} /> : null}
          </span>
        ) : null}
      </div>
    </li>
  );
}

function ProductRows({
  products,
  formatKey,
  lit,
  duplicate,
}: {
  products: readonly FormatGroupProduct[];
  formatKey: FormatKey;
  lit: number;
  duplicate?: boolean;
}) {
  return (
    <>
      {products.map((product, i) => (
        <ProductRow
          key={`${duplicate ? 'dup-' : ''}${product.key}`}
          product={product}
          formatKey={formatKey}
          on={i < lit}
          {...(duplicate ? { duplicate: true } : {})}
        />
      ))}
    </>
  );
}

/** Below this many rows, autoscroll is pointless: everything already fits in the box. */
const AUTOSCROLL_MIN_ROWS = 5;

function ProductGroupList({
  products,
  formatKey,
  revealed,
}: {
  products: readonly FormatGroupProduct[];
  formatKey: FormatKey;
  revealed: boolean;
}) {
  // How many rows are lit. Ramps up on reveal so the authored stagger is reproduced, using the
  // authored `.dot-t.on`/`.fmtitem.on` transition rather than any JS animation loop.
  const [lit, setLit] = useState(0);

  useEffect(() => {
    if (!revealed) {
      setLit(0);
      return;
    }
    if (products.length === 0) return;
    let cancelled = false;
    let index = 0;
    const step = () => {
      if (cancelled) return;
      index += 1;
      setLit(index);
      if (index < products.length) window.setTimeout(step, DOT_STAGGER_MS);
    };
    step();
    return () => {
      cancelled = true;
    };
  }, [revealed, products.length]);

  const autoscroll = products.length > AUTOSCROLL_MIN_ROWS;

  return (
    <div
      className={`fmtscroll${autoscroll ? ' fmtscroll-auto' : ''}`}
      style={{ maxHeight: SCROLL_MAX_HEIGHT_PX }}
    >
      {/* A list, because it is one: rows of products, navigable as such by a screen reader. */}
      <ul className="fmtitems">
        <ProductRows products={products} formatKey={formatKey} lit={lit} />
        {/* A second, identical copy of the same rows, present ONLY to autoscroll. The CSS animation
            (`journey-products.css`'s `.fmtscroll-auto .fmtitems` keyframe) scrolls the whole list
            up by exactly 50% of its own height and then repeats, so the seam between the real rows
            ending and the duplicate rows beginning is imperceptible — the loop point is the one
            place the content is genuinely identical to where it started. Each duplicate row is
            `aria-hidden`, since it repeats content a screen reader already has. */}
        {autoscroll && (
          <ProductRows
            products={products}
            formatKey={formatKey}
            lit={products.length}
            duplicate
          />
        )}
      </ul>
    </div>
  );
}

export function FormatGridBody({
  data,
  note,
  revealed,
}: {
  data: DiscoverData;
  note: string;
  revealed: boolean;
}) {
  // Flattened once per render of THIS data, not recomputed as a fresh array identity on every
  // re-render of an ancestor: GravityGraph memoises its own derived model off this prop's identity,
  // and a caller that rebuilds the array unconditionally defeats that memoisation on every
  // unrelated re-render.
  const allProducts = useMemo(() => data.groups.flatMap((group) => group.products), [data.groups]);

  // An odd number of groups leaves the last row half empty, and the constellation fills it. An even
  // number does not, so the graph starts its own row and takes the whole width — which is also the
  // right answer for zero groups. See `.fmtgrids` in journey-products.css for the grid itself.
  const graphFillsARow = data.groups.length % 2 === 0;

  return (
    <>
      {/* Groups and the constellation are siblings in one grid, not two stacked blocks, because that
          is what lets the graph drop into the empty cell beside a lone group. */}
      <div className="fmtgrids">
        {data.groups.map((group) => (
          <div className="fmtgroup" key={group.formatKey}>
            <div className="fglabel">
              <span className="fgdot" style={{ background: `var(${group.cssVar})` }} />
              {group.label} &middot; {group.count}
              {group.truncation?.total !== undefined && group.truncation.kept !== undefined ? (
                // States what was recorded against what the seller sent. Without this, "8" reads as
                // the seller's whole answer when it may have sent 200.
                <> &middot; {group.truncation.kept} of {group.truncation.total} recorded</>
              ) : null}
            </div>
            <div style={{ ['--fc' as string]: `var(${group.cssVar})` }}>
              <ProductGroupList
                products={group.products}
                formatKey={group.formatKey as FormatKey}
                revealed={revealed}
              />
            </div>
          </div>
        ))}
        {/* One constellation for the whole panel, after every group: its centre is the brief, and
            each product answered that same brief whichever format bucket it landed in. Renders
            nothing when no seller returned a ranking score. */}
        <GravityGraph
          products={allProducts}
          {...(graphFillsARow ? { className: 'fmtgraph-wide' } : {})}
        />
      </div>
      <div className="dg-note">{note}</div>
    </>
  );
}
