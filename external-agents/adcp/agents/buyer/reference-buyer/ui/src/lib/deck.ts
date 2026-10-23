/**
 * Positioning for a receding stack of cards, one of which is "on top".
 *
 * A card's `depth` is its cyclic distance forward from whichever index is currently active: the
 * active card is depth 0, the next one to become active is depth 1, and so on, wrapping round the
 * end of the list. Depth, not raw index, is what the layout renders from, so "bring the next one to
 * the front" is just "increment the active index" and every card's position updates from that one
 * number.
 *
 * `deckLayout` turns a depth into a transform, opacity and stacking order. Depth is capped at
 * `DECK_MAX_VISIBLE`: beyond that a card is marked hidden rather than given an ever-smaller,
 * ever-more-transparent style, so a 50-product list does not lay out 50 DOM nodes for the sake of 40
 * that would be indistinguishable from the canvas anyway.
 */

export const DECK_MAX_VISIBLE = 10;

/**
 * Default lateral and vertical offset per step of depth, in px, tuned for the chat product card's
 * roughly-square proportions (see `ProductStack.tsx`'s `CARD_WIDTH`/`CARD_HEIGHT`).
 *
 * Cards translate cumulatively toward the bottom-right as depth increases, and every card keeps the
 * same untransformed width/height, so a card at depth `d`'s bottom-right corner sits `stepX`/`stepY`
 * further out than depth `d-1`'s. Scale shrinks each card slightly back in from its own center,
 * which eats a few px of that peek, so the step is sized larger than the badge it reveals needs, not
 * exactly to it.
 *
 * A caller with a differently-shaped card — the journey view's Discover deck is short and wide, not
 * square — passes its own `stepX`/`stepY` to `deckLayout`/`deckStageSize` rather than using these.
 * Reusing this ratio unscaled on a 46px-tall card sent depth-9 cards 288px sideways and 162px down,
 * a diagonal spread several card-heights tall for a card that short — see
 * `journey-products.css`'s comment on `.fmtdeck-stage`.
 */
const STEP_X = 32;
const STEP_Y = 18;

/** Scale shrinks per step of depth; never below this, or a deep card reads as a sliver of a line. */
const SCALE_STEP = 0.045;
const SCALE_FLOOR = 0.75;

/** Opacity fades per step of depth; never below this, or a deep card is indistinguishable from gone. */
const OPACITY_STEP = 0.13;
const OPACITY_FLOOR = 0.3;

/**
 * How far forward (in cycle order) `index` is from `activeIndex`, wrapping round the list.
 *
 * `length` is passed rather than inferred from the two indices because the caller already has the
 * list and a length of 0 must return 0 rather than divide by it.
 */
export function deckDepth(index: number, activeIndex: number, length: number): number {
  if (length <= 0) return 0;
  return (((index - activeIndex) % length) + length) % length;
}

export interface DeckLayout {
  readonly depth: number;
  readonly isFront: boolean;
  /** True once a card is far enough back that it is not rendered at all. */
  readonly hidden: boolean;
  readonly transform: string;
  readonly opacity: number;
  readonly zIndex: number;
}

/**
 * The visual position for a card at this depth.
 *
 * Front-most first: later depths sit further right and lower, smaller, and fainter, which is what
 * "receding into a stack" and "on top" both mean here. `zIndex` is derived from `maxVisible` so the
 * cap on rendered depth and the cap on stacking order can never disagree with each other.
 *
 * `stepX`/`stepY` default to the values tuned for a roughly-square card; pass smaller ones for a
 * shorter or narrower card so the fan's spread stays proportionate to the card it is fanning.
 */
export function deckLayout(
  depth: number,
  maxVisible: number = DECK_MAX_VISIBLE,
  stepX: number = STEP_X,
  stepY: number = STEP_Y,
): DeckLayout {
  const hidden = depth >= maxVisible;
  const scale = Math.max(SCALE_FLOOR, 1 - depth * SCALE_STEP);
  const opacity = hidden ? 0 : Math.max(OPACITY_FLOOR, 1 - depth * OPACITY_STEP);
  const x = depth * stepX;
  const y = depth * stepY;
  return {
    depth,
    isFront: depth === 0,
    hidden,
    transform: `translate(${x}px, ${y}px) scale(${scale})`,
    opacity,
    zIndex: maxVisible - Math.min(depth, maxVisible),
  };
}

/**
 * The outer stack's own footprint, so its container reserves exactly the space the fan occupies.
 *
 * `stepX`/`stepY` must match whatever was passed to `deckLayout` for the same deck, or the reserved
 * footprint disagrees with where the cards actually sit.
 */
export function deckStageSize(
  cardWidth: number,
  cardHeight: number,
  visibleCount: number,
  stepX: number = STEP_X,
  stepY: number = STEP_Y,
): { width: number; height: number } {
  const depths = Math.max(0, visibleCount - 1);
  return {
    width: cardWidth + depths * stepX,
    height: cardHeight + depths * stepY,
  };
}

/** "3 of 12", for the counter next to the prev/next controls. */
export function deckCounterLabel(activeIndex: number, length: number): string {
  if (length <= 0) return '0 of 0';
  return `${activeIndex + 1} of ${length}`;
}
