import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { DEFAULT_TOUR_INTERVAL_MS, prefersReducedMotion, useNodeTour } from './useNodeTour';

const ORDER = ['best', 'middle', 'worst'];

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

const tick = (ms: number) => act(() => void vi.advanceTimersByTime(ms));

describe('useNodeTour', () => {
  it('starts on the first key, which the caller orders best-first', () => {
    const { result } = renderHook(() => useNodeTour(ORDER, true));
    expect(result.current.key).toBe('best');
    expect(result.current.running).toBe(true);
  });

  it('advances one key per interval', () => {
    const { result } = renderHook(() => useNodeTour(ORDER, true, 1000));
    tick(1000);
    expect(result.current.key).toBe('middle');
    tick(1000);
    expect(result.current.key).toBe('worst');
  });

  it('wraps round, so an unattended screen keeps cycling', () => {
    const { result } = renderHook(() => useNodeTour(ORDER, true, 1000));
    tick(3000);
    expect(result.current.key).toBe('best');
  });

  it('does not advance before its interval has elapsed', () => {
    const { result } = renderHook(() => useNodeTour(ORDER, true, 1000));
    tick(999);
    expect(result.current.key).toBe('best');
  });

  it('hands over for good once a real interaction stops it', () => {
    // One-way: a tour that resumed would fight the visitor for the detail panel.
    const { result } = renderHook(() => useNodeTour(ORDER, true, 1000));
    act(() => result.current.stop());
    expect(result.current.key).toBeNull();
    expect(result.current.running).toBe(false);
    tick(5000);
    expect(result.current.key).toBeNull();
  });

  it('runs nothing when disabled', () => {
    const { result } = renderHook(() => useNodeTour(ORDER, false));
    expect(result.current.key).toBeNull();
    expect(result.current.running).toBe(false);
  });

  it('does not tour a single node, which has nothing to cycle through', () => {
    const { result } = renderHook(() => useNodeTour(['only'], true));
    expect(result.current.running).toBe(false);
    expect(result.current.key).toBeNull();
  });

  it('does not tour an empty set', () => {
    const { result } = renderHook(() => useNodeTour([], true));
    expect(result.current.key).toBeNull();
  });

  it('restarts at the best node when the set changes', () => {
    // A new session or a filtered set is a new list; keeping the index would land on an unrelated node.
    const { result, rerender } = renderHook(
      ({ order }: { order: string[] }) => useNodeTour(order, true, 1000),
      { initialProps: { order: ORDER } },
    );
    tick(2000);
    expect(result.current.key).toBe('worst');
    rerender({ order: ['fresh-best', 'fresh-second'] });
    expect(result.current.key).toBe('fresh-best');
  });

  it('keeps its place when the caller rebuilds an identical list', () => {
    // The component rebuilds this array every render. Comparing by identity rather than by content
    // would restart the tour continuously and it would never leave the first node.
    const { result, rerender } = renderHook(
      ({ order }: { order: string[] }) => useNodeTour(order, true, 1000),
      { initialProps: { order: [...ORDER] } },
    );
    tick(1000);
    expect(result.current.key).toBe('middle');
    rerender({ order: [...ORDER] });
    expect(result.current.key).toBe('middle');
  });

  it('survives the list shrinking under it mid-tour', () => {
    const { result, rerender } = renderHook(
      ({ order }: { order: string[] }) => useNodeTour(order, true, 1000),
      { initialProps: { order: ORDER } },
    );
    tick(2000);
    rerender({ order: ['a', 'b'] });
    // Restarted rather than left pointing past the end of the new list.
    expect(result.current.key).toBe('a');
  });

  it('clears its timer on unmount', () => {
    const clear = vi.spyOn(window, 'clearInterval');
    const { unmount } = renderHook(() => useNodeTour(ORDER, true, 1000));
    unmount();
    expect(clear).toHaveBeenCalled();
    clear.mockRestore();
  });

  it('has a default interval in the range that reads as paced rather than frantic', () => {
    // Bounded on both sides on purpose. Too slow reads as stalled on a booth screen; too fast and the
    // panel changes before a figure can be read.
    expect(DEFAULT_TOUR_INTERVAL_MS).toBeGreaterThanOrEqual(900);
    expect(DEFAULT_TOUR_INTERVAL_MS).toBeLessThanOrEqual(2000);
  });
});

describe('prefersReducedMotion', () => {
  it('reports no preference when matchMedia is unavailable', () => {
    // jsdom has no matchMedia. Absent means "nothing expressed", which is not the same as "reduce".
    const original = window.matchMedia;
    // @ts-expect-error - deliberately removing it to exercise the guard.
    delete window.matchMedia;
    expect(prefersReducedMotion()).toBe(false);
    window.matchMedia = original;
  });

  it('reports the preference when the browser expresses one', () => {
    const original = window.matchMedia;
    window.matchMedia = ((query: string) => ({
      matches: query.includes('reduce'),
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    })) as unknown as typeof window.matchMedia;
    expect(prefersReducedMotion()).toBe(true);
    window.matchMedia = original;
  });
});
