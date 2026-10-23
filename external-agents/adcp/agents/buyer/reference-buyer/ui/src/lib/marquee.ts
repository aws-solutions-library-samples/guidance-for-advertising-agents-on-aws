/**
 * Geometry and timing for a seamlessly looping horizontal card track.
 *
 * Replaces the angled fan for a seller's product list. The fan kept a long list to roughly one card
 * of height, which was its point, but it also stacked nine cards behind the tenth where only a corner
 * of each was legible -- so the only way to read a product was to wait for the cycle to bring it
 * forward. A track shows several whole cards at once and keeps moving, so a list is scanned rather
 * than waited on.
 *
 * ## How the loop is seamless
 *
 * The item list is rendered TWICE into one track, and the track is translated by exactly half its own
 * width. At the end of that travel the second copy sits precisely where the first started, so the
 * animation restarting from its first keyframe is invisible -- there is no jump to hide, because the
 * two states are pixel-identical. This is why the duplication is structural rather than decorative:
 * remove it and the loop has a visible snap back on every iteration.
 *
 * The duplicate copy is presentational only. `marqueeAriaHiddenFromIndex` marks where it starts so a
 * screen reader is not read every product twice; the prev/next controls and the detail panel are the
 * accessible path through the same data.
 *
 * ## Constant speed, not constant duration
 *
 * Duration is derived from the track's width, so ten products and three products drift at the same
 * pixels per second. A fixed duration would make a long list race and a short one crawl, which reads
 * as the component being erratic rather than the list being longer.
 */

/** Travel speed. Slow enough to read a card title in passing. */
export const MARQUEE_PX_PER_SECOND = 28;

/** How many copies of the list the track holds. Two is what makes a half-width translate seamless. */
export const MARQUEE_COPIES = 2;

/** Minimum loop duration, so a one-card list does not animate at an absurd rate. */
const MIN_DURATION_SECONDS = 4;

/**
 * Width of one copy of the list, in px: every item plus the gaps between them.
 *
 * The trailing gap is included, so the last card of one copy is separated from the first card of the
 * next by the same gap as any other pair. Without it the two copies butt together and the seam is
 * visible as a missing gap rather than as a jump.
 */
export function marqueeCopyWidth(itemCount: number, itemWidth: number, gap: number): number {
  if (itemCount <= 0) return 0;
  return itemCount * (itemWidth + gap);
}

/** Seconds for one full loop, i.e. for the track to travel exactly one copy's width. */
export function marqueeDurationSeconds(
  itemCount: number,
  itemWidth: number,
  gap: number,
  pxPerSecond: number = MARQUEE_PX_PER_SECOND,
): number {
  const distance = marqueeCopyWidth(itemCount, itemWidth, gap);
  if (distance <= 0 || pxPerSecond <= 0) return MIN_DURATION_SECONDS;
  return Math.max(MIN_DURATION_SECONDS, distance / pxPerSecond);
}

/**
 * Whether the list is long enough to be worth looping.
 *
 * A single card has nowhere to travel to and would just drift in and out of an otherwise empty row.
 */
export function marqueeShouldLoop(itemCount: number): boolean {
  return itemCount > 1;
}

/**
 * The rendered track: the list repeated, each entry tagged with the copy it belongs to.
 *
 * `sourceIndex` is the index in the ORIGINAL list, so a click on any copy selects the same product.
 * `key` is unique across copies, because two React children cannot share one.
 */
export interface MarqueeTrackItem<T> {
  readonly item: T;
  readonly sourceIndex: number;
  readonly copy: number;
  readonly key: string;
  /** True for every copy after the first: the visual duplicate, hidden from assistive tech. */
  readonly isDuplicate: boolean;
}

export function marqueeTrackItems<T>(
  items: readonly T[],
  keyOf: (item: T, index: number) => string,
  copies: number = MARQUEE_COPIES,
): MarqueeTrackItem<T>[] {
  if (items.length === 0) return [];
  const total = marqueeShouldLoop(items.length) ? Math.max(1, copies) : 1;
  const out: MarqueeTrackItem<T>[] = [];
  for (let copy = 0; copy < total; copy += 1) {
    items.forEach((item, sourceIndex) => {
      out.push({
        item,
        sourceIndex,
        copy,
        key: `${keyOf(item, sourceIndex)}--copy${copy}`,
        isDuplicate: copy > 0,
      });
    });
  }
  return out;
}
