/**
 * Continuous auto-scroll for a bounded, overflowing strip.
 *
 * When a scrolling list has more than fits in its capped box, this advances the container's
 * `scrollTop` a little each animation frame and loops back to the top on reaching the bottom, so the
 * overflowed items come into view without the reader having to scroll. It pairs with
 * `useScrollSpyActive` on the same container: as the auto-scroll moves the list, the "most in view"
 * card the spy highlights follows along.
 *
 * Pauses while the pointer is over the list (via `onEnter`/`onLeave`), so a reader can stop on a card
 * to read it. Hovering a specific card also pins it active through the spy; this hook only owns the
 * motion.
 *
 * Guards:
 *   - `enabled` false (e.g. reduced motion, or fewer items than the cap) → does nothing.
 *   - no `requestAnimationFrame` (old/jsdom without rAF) → does nothing, so unit tests are unaffected.
 *   - nothing to scroll (content fits) → no-op each frame until it does.
 */

import { useCallback, useEffect, useRef } from 'react';

export interface AutoScrollLoop {
  /** Ref for the scroll container. Compose with the scroll-spy's container ref on the same element. */
  readonly containerRef: (el: HTMLElement | null) => void;
  /** Pointer entered the list — pause motion so a card can be read. */
  readonly onEnter: () => void;
  /** Pointer left the list — resume motion. */
  readonly onLeave: () => void;
}

export function useAutoScrollLoop(enabled: boolean, speedPxPerSec = 22): AutoScrollLoop {
  const el = useRef<HTMLElement | null>(null);
  const paused = useRef(false);

  const containerRef = useCallback((node: HTMLElement | null) => {
    el.current = node;
  }, []);
  const onEnter = useCallback(() => {
    paused.current = true;
  }, []);
  const onLeave = useCallback(() => {
    paused.current = false;
  }, []);

  useEffect(() => {
    if (!enabled) return;
    if (typeof window === 'undefined' || typeof window.requestAnimationFrame !== 'function') return;

    let raf = 0;
    let last = typeof performance !== 'undefined' ? performance.now() : 0;
    let carry = 0; // sub-pixel accumulator, so a slow speed still advances smoothly

    const step = (now: number) => {
      raf = window.requestAnimationFrame(step);
      const dt = now - last;
      last = now;
      const node = el.current;
      if (!node || paused.current) return;
      const maxScroll = node.scrollHeight - node.clientHeight;
      if (maxScroll <= 1) return; // content fits; nothing to scroll
      carry += (speedPxPerSec * dt) / 1000;
      if (carry < 1) return;
      const delta = Math.floor(carry);
      carry -= delta;
      const next = node.scrollTop + delta;
      // Loop: once the bottom is reached, jump back to the top and keep going.
      node.scrollTop = next >= maxScroll ? 0 : next;
    };

    raf = window.requestAnimationFrame(step);
    return () => window.cancelAnimationFrame(raf);
  }, [enabled, speedPxPerSec]);

  return { containerRef, onEnter, onLeave };
}
