/**
 * "Highlight the card that's in view", for a scrolling strip.
 *
 * The card most visible inside the strip's own scroll box becomes the active one; hovering a card
 * overrides that and holds it active until the pointer leaves. This replaces the timed carousel the
 * strips used to run: with a bounded, scrollable list the natural interaction is to scroll, and the
 * highlight should follow the scroll rather than a clock.
 *
 * jsdom (the test env) has no IntersectionObserver, and neither does an ancient browser. When it is
 * absent the hook degrades to "the first card is active" — deterministic, and enough for the unit
 * tests that assert the active card's content. When `enabled` is false (reduced motion), the caller
 * renders every card active and this does nothing.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

export interface ScrollSpyActive {
  /** Index of the card to render active. */
  readonly active: number;
  /** Ref for the scroll container (the observer's root). */
  readonly containerRef: (el: HTMLElement | null) => void;
  /** Ref factory for each card element, by index. */
  readonly itemRef: (index: number) => (el: HTMLElement | null) => void;
  /** Hover handlers: hovering a card pins it active; leaving resumes scroll tracking. */
  readonly onItemEnter: (index: number) => void;
  readonly onItemLeave: () => void;
}

export function useScrollSpyActive(count: number, enabled: boolean): ScrollSpyActive {
  const [active, setActive] = useState(0);
  const containerEl = useRef<HTMLElement | null>(null);
  const items = useRef<(HTMLElement | null)[]>([]);
  const ratios = useRef<number[]>([]);
  const hovered = useRef(false);

  const containerRef = useCallback((el: HTMLElement | null) => {
    containerEl.current = el;
  }, []);

  const itemRef = useCallback(
    (index: number) => (el: HTMLElement | null) => {
      items.current[index] = el;
    },
    [],
  );

  const onItemEnter = useCallback((index: number) => {
    hovered.current = true;
    setActive(index);
  }, []);

  const onItemLeave = useCallback(() => {
    hovered.current = false;
  }, []);

  useEffect(() => {
    if (!enabled) return;
    if (typeof IntersectionObserver === 'undefined') return;

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const index = items.current.indexOf(entry.target as HTMLElement);
          if (index >= 0) ratios.current[index] = entry.isIntersecting ? entry.intersectionRatio : 0;
        }
        // A hovered card owns the highlight; scroll position does not fight the pointer.
        if (hovered.current) return;
        let best = 0;
        let bestRatio = -1;
        for (let i = 0; i < count; i += 1) {
          const ratio = ratios.current[i] ?? 0;
          if (ratio > bestRatio) {
            bestRatio = ratio;
            best = i;
          }
        }
        setActive(best);
      },
      {
        root: containerEl.current,
        // Several thresholds so the active card updates smoothly as one scrolls past, not only when a
        // card fully enters or leaves.
        threshold: [0, 0.25, 0.5, 0.75, 1],
      },
    );

    const observed = items.current.slice(0, count).filter((el): el is HTMLElement => el !== null);
    observed.forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, [enabled, count]);

  return { active, containerRef, itemRef, onItemEnter, onItemLeave };
}
