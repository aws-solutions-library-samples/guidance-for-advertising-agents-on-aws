import { describe, expect, it } from 'vitest';

import {
  MARQUEE_COPIES,
  MARQUEE_PX_PER_SECOND,
  marqueeCopyWidth,
  marqueeDurationSeconds,
  marqueeShouldLoop,
  marqueeTrackItems,
} from './marquee';

describe('marqueeCopyWidth', () => {
  it('counts every item plus a trailing gap, so the two copies do not butt together', () => {
    // Without the trailing gap the seam shows up as a missing gap between the last card of one copy
    // and the first of the next -- which is exactly as visible as a jump.
    expect(marqueeCopyWidth(3, 100, 10)).toBe(330);
  });

  it('is zero for an empty list rather than a negative or NaN width', () => {
    expect(marqueeCopyWidth(0, 100, 10)).toBe(0);
    expect(marqueeCopyWidth(-2, 100, 10)).toBe(0);
  });
});

describe('marqueeDurationSeconds', () => {
  it('holds speed constant as the list grows', () => {
    // The property that matters: a long list must not race. Twice the width, twice the duration.
    const short = marqueeDurationSeconds(10, 100, 10);
    const long = marqueeDurationSeconds(20, 100, 10);
    expect(long).toBeCloseTo(short * 2, 5);
  });

  it('derives duration from the configured pixels per second', () => {
    const width = marqueeCopyWidth(10, 100, 10);
    expect(marqueeDurationSeconds(10, 100, 10)).toBeCloseTo(width / MARQUEE_PX_PER_SECOND, 5);
  });

  it('floors the duration, so a tiny track does not animate absurdly fast', () => {
    expect(marqueeDurationSeconds(1, 4, 0)).toBeGreaterThanOrEqual(4);
  });

  it('returns the floor rather than dividing by zero for a nonsensical speed', () => {
    expect(Number.isFinite(marqueeDurationSeconds(5, 100, 10, 0))).toBe(true);
  });
});

describe('marqueeShouldLoop', () => {
  it('does not loop a list with nothing to travel past', () => {
    expect(marqueeShouldLoop(0)).toBe(false);
    expect(marqueeShouldLoop(1)).toBe(false);
    expect(marqueeShouldLoop(2)).toBe(true);
  });
});

describe('marqueeTrackItems', () => {
  const keyOf = (item: string) => item;

  it('repeats the list, which is what makes a half-width translate seamless', () => {
    const track = marqueeTrackItems(['a', 'b', 'c'], keyOf);
    expect(track).toHaveLength(3 * MARQUEE_COPIES);
    expect(track.map((t) => t.item)).toEqual(['a', 'b', 'c', 'a', 'b', 'c']);
  });

  it('keeps every key unique, since two React children cannot share one', () => {
    const keys = marqueeTrackItems(['a', 'b', 'c'], keyOf).map((t) => t.key);
    expect(new Set(keys).size).toBe(keys.length);
  });

  it('points every copy back at the original index, so a click on either selects the same product', () => {
    const track = marqueeTrackItems(['a', 'b'], keyOf);
    expect(track.map((t) => t.sourceIndex)).toEqual([0, 1, 0, 1]);
  });

  it('marks the repeats as duplicates, so they can be hidden from assistive tech', () => {
    const track = marqueeTrackItems(['a', 'b'], keyOf);
    expect(track.filter((t) => !t.isDuplicate).map((t) => t.item)).toEqual(['a', 'b']);
    expect(track.filter((t) => t.isDuplicate).map((t) => t.item)).toEqual(['a', 'b']);
  });

  it('does not duplicate a single item, which has nowhere to loop to', () => {
    const track = marqueeTrackItems(['only'], keyOf);
    expect(track).toHaveLength(1);
    expect(track[0]!.isDuplicate).toBe(false);
  });

  it('renders an empty list as an empty track', () => {
    expect(marqueeTrackItems([], keyOf)).toEqual([]);
  });
});
