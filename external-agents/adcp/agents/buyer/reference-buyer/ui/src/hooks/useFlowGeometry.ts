/**
 * Measures the flow grid: how many columns it resolved to, and where each phase cell landed.
 *
 * Both are needed and neither can be known without the DOM. The column count is an input to the
 * serpentine placement (`placeFlowItems`), because a reversed row cannot be expressed as auto-flow; the
 * cell rectangles are inputs to the connectors, because a connector runs through the gap between two
 * boxes whose positions depend on how tall their neighbours turned out to be.
 *
 * ## The two-pass ordering, which is not incidental
 *
 * Placement depends on the column count, and the rectangles depend on the placement. So a single pass
 * would measure positions produced by the PREVIOUS column count. The effect below handles that by
 * returning early after a column-count change: setting the count re-renders with new placements, the
 * effect runs again, and only then are rectangles read. Collapsing this into one pass reintroduces
 * connectors drawn to where cards used to be.
 *
 * Wrappers are measured, never the `.panel` inside them, for the reason the scroll effect gives: a
 * revealing panel carries an unfinished `translateY`, so its own box is 26px from where it settles.
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

import type { FlowRect } from '../lib/journey/flowLayout';

export interface FlowGeometry {
  /** Resolved column tracks. Defaults to 1 before the first measurement, never 0. */
  readonly columns: number;
  /**
   * Card heights in DOM order, for the masonry packing.
   *
   * Indexed by position among `[data-flow-cell]` children, which is the same order the caller describes
   * its items in. Not keyed by phase, because the status line and the other-sellers notice are packed too
   * and neither is a phase.
   */
  readonly heights: readonly number[];
  /** Keyed by `data-phase`, relative to the flow's own padding box. For the connectors. */
  readonly rects: ReadonlyMap<string, FlowRect>;
}

/** How many tracks `grid-template-columns` resolved to. */
function countTracks(flow: HTMLElement): number {
  if (typeof window === 'undefined' || typeof window.getComputedStyle !== 'function') return 1;
  const template = window.getComputedStyle(flow).gridTemplateColumns;
  // Resolved values are absolute lengths, one per track: "497.33px 497.33px 497.33px". `none` is
  // what jsdom reports, and what a non-grid fallback would report.
  if (!template || template === 'none') return 1;
  const tracks = template.split(/\s+/).filter((part) => part.endsWith('px'));
  return Math.max(1, tracks.length);
}

function measureRects(flow: HTMLElement): ReadonlyMap<string, FlowRect> {
  const rects = new Map<string, FlowRect>();
  for (const cell of flow.querySelectorAll<HTMLElement>('[data-phase]')) {
    const key = cell.dataset.phase;
    if (key === undefined) continue;
    // `offset*` rather than `getBoundingClientRect`, so the numbers are already relative to the flow
    // (its `position: relative` makes it the offsetParent) and need no scroll correction.
    rects.set(key, {
      left: cell.offsetLeft,
      top: cell.offsetTop,
      width: cell.offsetWidth,
      height: cell.offsetHeight,
    });
  }
  return rects;
}

/**
 * Card heights in DOM order, as exact fractional layout heights.
 *
 * `getBoundingClientRect().height` on the WRAPPER, and each part of that matters:
 *
 *   - **Not `scrollHeight`.** It is a scroll-extent measurement rounded to an integer, and it reported
 *     systematically more than the card's layout height, so every span carried that excess as extra gap
 *     below the card. An earlier comment here claimed `scrollHeight` was needed to stop a card confirming
 *     the span it was already given. That was wrong: `align-items: start` means the wrapper's height is its
 *     content's height whatever span it is placed in, so there is no such loop to avoid. That property is
 *     load-bearing for the measurement, not only for the look.
 *   - **The wrapper, not the `.panel` inside it.** `getBoundingClientRect` includes transforms, and a
 *     revealing panel carries `translateY(26px) scale(.985)` — so measuring the panel would report ~1.5%
 *     short and would change mid-transition. The wrapper has no transform, and a child's transform does not
 *     affect its parent's layout box.
 *   - **Fractional, not rounded.** The caller rounds up once, when converting to 1px row units, so the
 *     worst case is a sub-pixel of extra gap rather than a rounding error per card.
 */
function measureHeights(flow: HTMLElement): readonly number[] {
  return Array.from(
    flow.querySelectorAll<HTMLElement>('[data-flow-cell]'),
    (cell) => cell.getBoundingClientRect().height,
  );
}

/**
 * Compared at the resolution the packing actually uses.
 *
 * Heights are fractional, and `packFlowItems` rounds each one up to a whole 1px row unit. Comparing the
 * raw floats would re-render on sub-pixel noise that cannot move a single card, and a ResizeObserver
 * produces that noise on every scrollbar appearance and zoom change.
 */
function sameHeights(a: readonly number[], b: readonly number[]): boolean {
  return (
    a.length === b.length && a.every((height, i) => Math.ceil(height) === Math.ceil(b[i] ?? -1))
  );
}

function sameRects(
  a: ReadonlyMap<string, FlowRect>,
  b: ReadonlyMap<string, FlowRect>,
): boolean {
  if (a.size !== b.size) return false;
  for (const [key, rect] of a) {
    const other = b.get(key);
    if (!other) return false;
    if (
      other.left !== rect.left ||
      other.top !== rect.top ||
      other.width !== rect.width ||
      other.height !== rect.height
    ) {
      return false;
    }
  }
  return true;
}

/**
 * @param flowRef the flow container, which must be `position: relative`.
 * @param signature changes whenever the set or order of cells changes, so a reveal re-measures.
 */
export function useFlowGeometry(
  flowRef: React.RefObject<HTMLElement | null>,
  signature: string,
): FlowGeometry {
  const [columns, setColumns] = useState(1);
  const [heights, setHeights] = useState<readonly number[]>(() => []);
  const [rects, setRects] = useState<ReadonlyMap<string, FlowRect>>(() => new Map());
  /** Bumped by the observers, to re-run a measurement whose inputs are unchanged. */
  const [tick, setTick] = useState(0);
  const rectsRef = useRef(rects);
  rectsRef.current = rects;
  const heightsRef = useRef(heights);
  heightsRef.current = heights;

  const remeasure = useCallback(() => setTick((n) => n + 1), []);

  useLayoutEffect(() => {
    const flow = flowRef.current;
    if (!flow) return;

    const nextColumns = countTracks(flow);
    if (nextColumns !== columns) {
      // Column width changes, so every card's content re-wraps and its height changes with it. Re-render
      // first and measure on the next pass rather than recording heights this render is about to
      // invalidate.
      setColumns(nextColumns);
      return;
    }

    const nextHeights = measureHeights(flow);
    if (!sameHeights(nextHeights, heightsRef.current)) {
      // Heights feed the packing, so positions are about to move too. Same reasoning as above: let the
      // re-render place the cards, then read where they landed.
      setHeights(nextHeights);
      return;
    }

    const nextRects = measureRects(flow);
    if (!sameRects(nextRects, rectsRef.current)) setRects(nextRects);
  }, [flowRef, columns, signature, tick]);

  // A card's height changes as its body reveals, which moves every card below it. Observing the flow
  // alone is not enough: its own box may not change when a child grows inside a fixed row.
  useEffect(() => {
    const flow = flowRef.current;
    if (!flow) return;
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(remeasure);
    observer.observe(flow);
    for (const cell of flow.querySelectorAll<HTMLElement>('[data-phase]')) observer.observe(cell);
    return () => observer.disconnect();
  }, [flowRef, signature, remeasure]);

  return { columns, heights, rects };
}
