/**
 * Where each flow card sits, and where the connector between two of them runs.
 *
 * A pure module for the same reason `scroll.ts` and `gravityGraph.ts` are: arithmetic with enough edge
 * cases to be worth testing on its own, and none of it needs a DOM.
 *
 * ## Columns, not rows
 *
 * Cards are packed into columns, each card going into whichever column is currently SHORTEST. There are
 * no rows. That is the whole design, and it is what removes the gaps: in a row-based grid every row is
 * as tall as its tallest card, so a short card leaves dead space beneath it the size of the height
 * difference. Here a card sits directly under the one above it in its own column, and the columns are
 * independent.
 *
 * Shortest-column-first is also exactly the rule "wrap to the left when the card above on the right is
 * taller, and to the right when it is shorter" — a taller right column means the left one is shorter, so
 * that is where the next card goes. One rule rather than two cases.
 *
 * An earlier version of this module snaked the reading order instead (row 1 left to right, row 2 right to
 * left) so that consecutive phases were always adjacent for the connectors' sake. It has been removed:
 * it needed shared rows to work, and shared rows are the thing that produces the gaps.
 *
 * ## How the vertical position is expressed
 *
 * As a span of very fine grid rows — `grid-auto-rows: 1px` — so one row unit is one pixel and a card
 * spanning `n` units is `n` pixels tall. The browser still owns the stacking; this module only says which
 * unit each card starts at and how many it covers.
 *
 * `row-gap` must therefore be ZERO, and the gap is baked into each span instead (see `FLOW_GAP_PX`). A
 * real `row-gap` would be inserted between every one of the thousands of 1px rows.
 */

/**
 * The gap between cards, in pixels.
 *
 * Used two ways, and they have to agree: the stylesheet applies it as `column-gap`, and this module adds
 * it to every card's row span to make the vertical gap. It is exported so the coupling is visible from
 * both sides rather than being two independent `20`s.
 */
export const FLOW_GAP_PX = 20;

/**
 * A height to place an unmeasured card at.
 *
 * The first render happens before any measurement, so every card would otherwise pack at height zero and
 * pile into one column. The correction lands in a layout effect, before paint, so this is never seen —
 * but it has to be plausible enough that the pre-correction layout is not absurd.
 */
const ASSUMED_HEIGHT_PX = 320;

/**
 * How wide an item is.
 *
 * `wide` items take every column: the products panel, the session status line and the other-sellers
 * notice. The last two are prose about the run rather than cards in it, and the notice in particular has
 * to sit on its own line directly above the panel it describes.
 */
export type FlowItemKind = 'wide' | 'card';

export interface FlowItem {
  readonly kind: FlowItemKind;
  /** Measured height in pixels, or `undefined` before the first measurement. */
  readonly height?: number;
}

export interface FlowPlacement {
  /** 1-based grid column line to start at. */
  readonly column: number;
  /** Column tracks spanned. `columns` for a wide item, 1 for a card. */
  readonly span: number;
  /** 1-based grid row line to start at, in 1px row units. */
  readonly rowStart: number;
  /** Row units covered, including the trailing gap. */
  readonly rowSpan: number;
}

/**
 * Pack items into columns, each card going into the shortest column so far.
 *
 * Reading order is preserved in the sense that matters: a card's top is never above the top of the card
 * before it. `rowStart` is derived from `min(columnHeights)`, which only ever grows, so the sequence of
 * starts is non-decreasing. Two cards can begin level with each other — that is what filling two columns
 * looks like — but a later card cannot begin higher than an earlier one.
 *
 * Ties go to the leftmost column, so an empty grid fills left to right.
 */
export function packFlowItems(
  items: readonly FlowItem[],
  columns: number,
): readonly FlowPlacement[] {
  // A zero or negative track count is a measurement that has not happened yet, not a layout. One column
  // degenerates to a plain stack, which is also the narrow-viewport case.
  const tracks = Math.max(1, Math.floor(columns));
  /** Row units consumed in each column so far. */
  const heights = new Array<number>(tracks).fill(0);
  const placements: FlowPlacement[] = [];

  for (const item of items) {
    // Rounded up: a fractional row unit would leave a sub-pixel sliver of the next card's gap showing.
    const span = Math.ceil(item.height ?? ASSUMED_HEIGHT_PX) + FLOW_GAP_PX;

    if (item.kind === 'wide') {
      // Below EVERY column, not just the shortest: it spans them all, so it cannot start until all of
      // them have finished. Afterwards every column is level with its bottom, which is why a wide item
      // resets the packing rather than leaving one column permanently ahead.
      const start = Math.max(...heights);
      placements.push({ column: 1, span: tracks, rowStart: start + 1, rowSpan: span });
      heights.fill(start + span);
      continue;
    }

    let shortest = 0;
    for (let c = 1; c < tracks; c += 1) {
      // Strictly less than, so ties keep the leftmost column and the grid fills left to right.
      if ((heights[c] ?? 0) < (heights[shortest] ?? 0)) shortest = c;
    }
    const start = heights[shortest] ?? 0;
    placements.push({ column: shortest + 1, span: 1, rowStart: start + 1, rowSpan: span });
    heights[shortest] = start + span;
  }

  return placements;
}

/** A measured box, in pixels, relative to the flow container's padding box. */
export interface FlowRect {
  readonly left: number;
  readonly top: number;
  readonly width: number;
  readonly height: number;
}

export type ConnectorAxis = 'horizontal' | 'vertical';

export interface FlowConnector {
  readonly axis: ConnectorAxis;
  /** Where the connector's own box starts, relative to the flow container. */
  readonly left: number;
  readonly top: number;
  /** Length along `axis`. The cross-axis size is the stylesheet's business, not this module's. */
  readonly length: number;
  /**
   * Which way the arrow points. `'forward'` is rightward for a horizontal connector and downward for a
   * vertical one; `'reverse'` is leftward. Vertical connectors are always `'forward'` — the flow never
   * runs upward.
   */
  readonly direction: 'forward' | 'reverse';
}

/**
 * The minimum gap a connector needs before it is worth drawing.
 *
 * Two cards can end up flush or overlapping by a fraction of a pixel after fractional-pixel track
 * sizing, and a 0.4px arrow is visual noise rather than information.
 */
const MIN_CONNECTOR_PX = 6;

/**
 * The connector from one card to the next, or `null` when there is no sensible line to draw.
 *
 * Measured from real rectangles rather than from placements, which is what keeps it correct under
 * masonry: two consecutive cards may be side by side, or stacked in the same column, or diagonal, and
 * which of those it is depends on heights this module never sees. The arrow follows where the cards
 * actually are.
 *
 * Three cases:
 *
 *   - **Overlapping vertically**: a horizontal run through the column gap, pointing whichever way the
 *     next card lies.
 *   - **Below**: a vertical run, drawn from the centre of the horizontal OVERLAP between the two boxes so
 *     it stays inside both even when one is a wide panel spanning every column.
 *   - **No gap**: `null`. A zero-length connector renders as an arrowhead sitting on a card edge with no
 *     line attached to it, which reads as a rendering fault.
 *
 * There is no diagonal case, and that is a consequence of the packing rather than an omission. A card only
 * goes to a different column when that column is SHORTER, so its top is above the bottom of the card
 * before it — which is the definition of overlapping vertically. Two consecutive cards are therefore either
 * stacked in one column or side by side with a shared range, never offset diagonally with a clear gap
 * between them. The remaining `null` returns cover the genuinely degenerate arrangements, and they return
 * nothing rather than a line that touches neither card.
 */
export function connectorBetween(from: FlowRect, to: FlowRect): FlowConnector | null {
  const overlapsVertically = from.top < to.top + to.height && to.top < from.top + from.height;

  if (overlapsVertically) {
    const forward = to.left >= from.left;
    const gapStart = forward ? from.left + from.width : to.left + to.width;
    const gapEnd = forward ? to.left : from.left;
    const length = gapEnd - gapStart;
    if (length < MIN_CONNECTOR_PX) return null;
    /**
     * The midpoint of the range the two cards actually SHARE.
     *
     * This was `max(tops) + min(heights) / 2`, which is only the same thing when the two cards start at
     * the same height. Under masonry they almost never do, and the error is unbounded: two cards
     * overlapping by 20px at the bottom of one and the top of the other put the arrow half the shorter
     * card's height below the overlap — outside both cards, floating in the gutter beside whichever card
     * happened to reach that far down. That is the arrow that appeared to come out of a card's middle and
     * point at nothing.
     */
    const overlapTop = Math.max(from.top, to.top);
    const overlapBottom = Math.min(from.top + from.height, to.top + to.height);
    const centre = (overlapTop + overlapBottom) / 2;
    return {
      axis: 'horizontal',
      left: gapStart,
      top: centre,
      length,
      direction: forward ? 'forward' : 'reverse',
    };
  }

  const gapStart = from.top + from.height;
  const length = to.top - gapStart;
  if (length < MIN_CONNECTOR_PX) return null;
  const overlapLeft = Math.max(from.left, to.left);
  const overlapRight = Math.min(from.left + from.width, to.left + to.width);
  const centre =
    overlapRight > overlapLeft
      ? (overlapLeft + overlapRight) / 2
      : // No horizontal overlap: the next card is in another column entirely. Aim at the destination,
        // since the arrow's job is to say where to look next.
        to.left + to.width / 2;
  return { axis: 'vertical', left: centre, top: gapStart, length, direction: 'forward' };
}
