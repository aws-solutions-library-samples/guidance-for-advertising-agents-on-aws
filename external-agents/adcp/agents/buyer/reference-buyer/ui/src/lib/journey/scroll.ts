/**
 * Bringing the active phase up under the sticky summary.
 *
 * The prototype scrolls on every phase it plays, and it always animates:
 * `scrollPanelIntoView()` in `.kiro/specs/journey.html` measures the summary's live height, subtracts
 * it and a 30px gap from the panel's position, and calls `window.scrollTo` with `behavior:'smooth'`
 * unconditionally. The port made the animation conditional on a Replay walk being in progress, which
 * left a live session jumping instead of scrolling — the phase arrived at the right place, but nothing
 * connected where the reader had been looking to where they were now looking.
 *
 * So: one behaviour for both modes, animated, exactly as authored. What the port keeps is the
 * COALESCING below, which the prototype does not need and this does.
 *
 * The height is re-measured on every call rather than cached, because the summary grows as entity
 * chips arrive and a cached height drifts the target down the page.
 */

/** The gap the prototype leaves between the summary's lower edge and the panel. Its own constant. */
export const SUMMARY_GAP_PX = 30;

/**
 * How long successive phase changes are allowed to collapse into a single scroll.
 *
 * Catching up — first load, a session switch, or several phases arriving together — changes the active
 * phase repeatedly within a few frames. One animated scroll per change queues animations that chase
 * each other down the page, and the reader sees the view lurch through intermediate phases nobody
 * asked to look at. Waiting this long before scrolling means a burst produces exactly one scroll, to
 * the phase the burst ended on.
 *
 * Short enough to be imperceptible for the single-phase case, which is what a live session and a
 * Replay walk both are: their phases arrive hundreds of milliseconds apart, so each still scrolls on
 * its own.
 */
export const SCROLL_SETTLE_MS = 80;

export interface ScrollGeometry {
  /** The phase wrapper's top edge, relative to the viewport. */
  readonly panelTop: number;
  /** The document's current scroll offset. */
  readonly scrollY: number;
  /** The sticky summary's height, measured now rather than remembered. */
  readonly summaryHeight: number;
}

/**
 * Where the document should scroll to put this phase just below the summary.
 *
 * Clamped at zero: a phase near the top of the document computes a negative offset, and scrolling to
 * a negative position is either ignored or bounces, depending on the browser.
 */
export function scrollTargetFor(geometry: ScrollGeometry): number {
  const { panelTop, scrollY, summaryHeight } = geometry;
  return Math.max(0, scrollY + panelTop - summaryHeight - SUMMARY_GAP_PX);
}

/**
 * How to get there.
 *
 * Animated in every mode, per the prototype. Reduced motion is the one exception, and it is the
 * reader's own instruction rather than a mode of ours.
 */
export function scrollBehaviourFor(reducedMotion: boolean): ScrollBehavior {
  return reducedMotion ? 'auto' : 'smooth';
}
