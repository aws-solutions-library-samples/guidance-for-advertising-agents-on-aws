/**
 * Advances a "which one is active" index on a timer, for a stack that cycles through its items.
 *
 * The automatic advance is a single self-rescheduling timer, not a `setInterval`: it is armed
 * directly inside its own callback (and inside every manual move) rather than by an effect that
 * re-runs after `activeIndex` changes and commits. Driving it from an effect dependent on
 * `activeIndex` was tried first and is fragile under both real and faked timers, because it needs a
 * render-and-effect round trip between one fire and the next being armed; a burst of fake-timer
 * advance that spans several intervals in one call can outrun that round trip and only fire once.
 * Arming the next timer from inside the current callback has no such gap.
 *
 * Every advance, automatic or manual, cancels any pending timer and arms a fresh one, so a click on
 * a peeking card always gets a full interval before the next automatic advance rather than being cut
 * short by a tick already in flight.
 *
 * **Two independent reasons to hold the sequence, not one.** An earlier version had a single
 * `paused` flag set by both the hover handlers and the manual Pause button, which meant leaving the
 * stage with the mouse called `resume()` unconditionally and overrode a manual pause the instant the
 * pointer moved away — the button looked like it "pretended" to pause because the very next
 * `mouseleave` silently undid it. Hovering and manually pausing are held as separate booleans here so
 * that clearing one can never clear the other; the deck runs only when NEITHER is set.
 */

import { useEffect, useRef, useState } from 'react';

export const DEFAULT_DECK_INTERVAL_MS = 1500;

export interface DeckCycle {
  readonly activeIndex: number;
  /** Whether the deck is manually paused. Does not reflect a transient hover; see `setHovering`. */
  readonly paused: boolean;
  readonly setActive: (index: number) => void;
  readonly next: () => void;
  readonly prev: () => void;
  /** Manual pause, e.g. from a Pause button. Independent of hover. */
  readonly pause: () => void;
  /** Manual resume, e.g. from a Play button. Independent of hover. */
  readonly resume: () => void;
  /** Hover pause/resume, e.g. from mouseenter/mouseleave on the stage. Independent of `pause`/`resume`. */
  readonly setHovering: (hovering: boolean) => void;
}

function wrap(index: number, length: number): number {
  if (length <= 0) return 0;
  return ((index % length) + length) % length;
}

export function useDeckCycle(
  length: number,
  intervalMs: number = DEFAULT_DECK_INTERVAL_MS,
  enabled: boolean = true,
): DeckCycle {
  const [activeIndex, setActiveIndex] = useState(0);
  const [manuallyPaused, setManuallyPaused] = useState(false);
  const [hovering, setHovering] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clear = () => {
    if (timer.current !== null) {
      clearTimeout(timer.current);
      timer.current = null;
    }
  };

  // Held if EITHER reason is set. A manual pause must survive the pointer leaving the stage, and a
  // hover pause must survive nothing else changing, so neither may clear the other.
  const running = enabled && !manuallyPaused && !hovering && length > 1;

  /** Cancels any pending timer and, if the deck should be running, arms exactly one more. */
  const arm = () => {
    clear();
    if (!running) return;
    timer.current = setTimeout(() => {
      setActiveIndex((current) => wrap(current + 1, length));
      arm();
    }, intervalMs);
  };

  // A different product list is a different deck; carrying an index across would land on whatever
  // happens to share that position in the new list.
  useEffect(() => {
    setActiveIndex(0);
  }, [length]);

  useEffect(() => {
    arm();
    return clear;
    // Re-arms whenever whether-it-should-run changes (enabled, either pause reason, length crossing
    // 1, or the interval itself). It deliberately does NOT depend on `activeIndex`: automatic
    // advances re-arm themselves from inside their own callback, and re-arming here too on every
    // index change is what made the chained timer fragile.
  }, [running, length, intervalMs]);

  const moveTo = (index: number) => {
    setActiveIndex(wrap(index, length));
    // A manual move gets its own full interval, same as an automatic one, rather than inheriting
    // whatever time was already left on the timer it interrupted.
    arm();
  };

  return {
    activeIndex: wrap(activeIndex, length),
    paused: manuallyPaused,
    setActive: moveTo,
    next: () => moveTo(wrap(activeIndex + 1, length)),
    prev: () => moveTo(wrap(activeIndex - 1, length)),
    pause: () => setManuallyPaused(true),
    resume: () => setManuallyPaused(false),
    setHovering,
  };
}
