import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { DEFAULT_DECK_INTERVAL_MS, useDeckCycle } from './useDeckCycle';

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

const tick = (ms: number) => act(() => void vi.advanceTimersByTime(ms));

describe('useDeckCycle', () => {
  it('starts at the first card', () => {
    const { result } = renderHook(() => useDeckCycle(5, 1000));
    expect(result.current.activeIndex).toBe(0);
  });

  it('advances one card per interval', () => {
    const { result } = renderHook(() => useDeckCycle(5, 1000));
    tick(1000);
    expect(result.current.activeIndex).toBe(1);
    tick(1000);
    expect(result.current.activeIndex).toBe(2);
  });

  it('does not advance before its interval has elapsed', () => {
    const { result } = renderHook(() => useDeckCycle(5, 1000));
    tick(999);
    expect(result.current.activeIndex).toBe(0);
  });

  it('wraps round the end of the list, so an unattended card keeps cycling', () => {
    const { result } = renderHook(() => useDeckCycle(3, 1000));
    tick(3000);
    expect(result.current.activeIndex).toBe(0);
  });

  it('pauses without losing its place, and resumes from there', () => {
    const { result } = renderHook(() => useDeckCycle(5, 1000));
    tick(1000);
    expect(result.current.activeIndex).toBe(1);
    act(() => result.current.pause());
    tick(5000);
    // Held, not one-way: this is a hover pause, not a permanent stop.
    expect(result.current.activeIndex).toBe(1);
    act(() => result.current.resume());
    tick(1000);
    expect(result.current.activeIndex).toBe(2);
  });

  it('jumps to a clicked card and gives it a full interval before advancing again', () => {
    const { result } = renderHook(() => useDeckCycle(5, 1000));
    act(() => result.current.setActive(3));
    expect(result.current.activeIndex).toBe(3);
    tick(999);
    expect(result.current.activeIndex).toBe(3);
    tick(1);
    expect(result.current.activeIndex).toBe(4);
  });

  it('next() and prev() move by one and wrap in both directions', () => {
    const { result } = renderHook(() => useDeckCycle(3, 1000));
    act(() => result.current.prev());
    expect(result.current.activeIndex).toBe(2);
    act(() => result.current.next());
    expect(result.current.activeIndex).toBe(0);
  });

  it('does not run for a list of zero or one, which has nothing to cycle through', () => {
    const single = renderHook(() => useDeckCycle(1, 1000));
    tick(5000);
    expect(single.result.current.activeIndex).toBe(0);

    const empty = renderHook(() => useDeckCycle(0, 1000));
    tick(5000);
    expect(empty.result.current.activeIndex).toBe(0);
  });

  it('does not run when disabled, e.g. for reduced motion', () => {
    const { result } = renderHook(() => useDeckCycle(5, 1000, false));
    tick(5000);
    expect(result.current.activeIndex).toBe(0);
  });

  it('resets to the first card when the list length changes', () => {
    const { result, rerender } = renderHook(
      ({ length }: { length: number }) => useDeckCycle(length, 1000),
      { initialProps: { length: 5 } },
    );
    tick(2000);
    expect(result.current.activeIndex).toBe(2);
    rerender({ length: 3 });
    expect(result.current.activeIndex).toBe(0);
  });

  it('clears its timer on unmount', () => {
    const clear = vi.spyOn(window, 'clearTimeout');
    const { unmount } = renderHook(() => useDeckCycle(5, 1000));
    unmount();
    expect(clear).toHaveBeenCalled();
    clear.mockRestore();
  });

  it('has a default interval of 1.5s', () => {
    expect(DEFAULT_DECK_INTERVAL_MS).toBe(1500);
  });

  describe('hover and manual pause are independent', () => {
    // Regression coverage for the actual reported defect: a single shared `paused` flag meant the
    // stage's mouseleave (wired to an unconditional resume) silently cleared a manual pause the
    // moment the pointer left, so clicking Pause looked like it "pretended" to pause.

    it('a manual pause survives the pointer leaving the stage', () => {
      const { result } = renderHook(() => useDeckCycle(5, 1000));
      act(() => result.current.setHovering(true));
      act(() => result.current.pause());
      act(() => result.current.setHovering(false));
      // The bug: setHovering(false) used to call the same resume() a mouseleave calls, clearing the
      // manual pause it had nothing to do with.
      expect(result.current.paused).toBe(true);
      tick(5000);
      expect(result.current.activeIndex).toBe(0);
    });

    it('a hover pause does not flip the manual paused flag, and releasing it resumes', () => {
      const { result } = renderHook(() => useDeckCycle(5, 1000));
      act(() => result.current.setHovering(true));
      expect(result.current.paused).toBe(false);
      tick(5000);
      expect(result.current.activeIndex).toBe(0);
      act(() => result.current.setHovering(false));
      tick(1000);
      expect(result.current.activeIndex).toBe(1);
    });

    it('resuming manually while still hovered does not restart the cycle until the hover ends too', () => {
      const { result } = renderHook(() => useDeckCycle(5, 1000));
      act(() => result.current.setHovering(true));
      act(() => result.current.pause());
      act(() => result.current.resume());
      // Hover is still held, so the deck stays put even though the manual pause was lifted.
      tick(5000);
      expect(result.current.activeIndex).toBe(0);
      act(() => result.current.setHovering(false));
      tick(1000);
      expect(result.current.activeIndex).toBe(1);
    });
  });
});
