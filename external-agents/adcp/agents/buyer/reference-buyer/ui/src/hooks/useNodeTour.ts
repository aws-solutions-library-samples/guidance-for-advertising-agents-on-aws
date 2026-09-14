/**
 * Walks an ordered list of keys on a timer, so a chart reads itself when nobody is touching it.
 *
 * The constellation's detail panel only fills on hover, and at a booth there is no pointer: the
 * screen sits there with a legend and an empty panel. This steps through the nodes in ranking order
 * so the same information arrives without anyone reaching for a mouse.
 *
 * Three things it deliberately does:
 *
 * - **Yields permanently to a real interaction.** `stop()` is one-way. A tour that resumed after
 *   someone moved the pointer away would fight them for control of the panel.
 * - **Does not run under `prefers-reduced-motion`.** An automatic cycle is motion the user did not
 *   ask for, and it is the whole behaviour rather than a decorative flourish, so it is switched off
 *   rather than shortened.
 * - **Restarts when the set changes.** A new session or a filtered set is a new list; carrying an
 *   index across would land on an unrelated node.
 */

import { useEffect, useMemo, useState } from 'react';

/**
 * One node per beat.
 *
 * Set by watching it: 2600ms read as sluggish on a booth screen, where a passer-by gives the panel a
 * few seconds in total. Four or five short labelled figures are legible inside this.
 */
export const DEFAULT_TOUR_INTERVAL_MS = 1300;

export interface NodeTour {
  /** The key currently being presented, or null when the tour is not running. */
  readonly key: string | null;
  readonly running: boolean;
  /** Hand control over for good. Called on the first real hover, focus or click. */
  readonly stop: () => void;
}

/**
 * True when the visitor has asked for less animation.
 *
 * Read through a guard because `matchMedia` is absent in jsdom and in any non-browser render; absent
 * means "no preference expressed", which is the same as not reducing.
 */
export function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

export function useNodeTour(
  order: readonly string[],
  enabled: boolean,
  intervalMs: number = DEFAULT_TOUR_INTERVAL_MS,
): NodeTour {
  const [index, setIndex] = useState(0);
  const [stopped, setStopped] = useState(false);

  // Compared by content rather than by array identity: the caller rebuilds this list on every render,
  // so an identity check would restart the tour continuously and it would never leave the first node.
  const signature = useMemo(() => order.join('\u0000'), [order]);

  useEffect(() => {
    setIndex(0);
  }, [signature]);

  // One node has nothing to tour through, and its panel is already filled by being the only one.
  const running = enabled && !stopped && order.length > 1;
  const length = order.length;

  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => {
      setIndex((current) => (current + 1) % length);
    }, intervalMs);
    return () => window.clearInterval(id);
  }, [running, length, intervalMs, signature]);

  return {
    // Modulo again on read: the list can shrink between a tick and a render, and an index past the
    // end would blank the panel mid-tour.
    key: running ? order[index % length] ?? null : null,
    running,
    stop: () => setStopped(true),
  };
}
