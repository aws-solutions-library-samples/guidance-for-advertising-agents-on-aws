import { describe, expect, it } from 'vitest';

import { DECK_MAX_VISIBLE, deckCounterLabel, deckDepth, deckLayout, deckStageSize } from './deck';

describe('deckDepth', () => {
  it('is 0 for the active index itself', () => {
    expect(deckDepth(2, 2, 5)).toBe(0);
  });

  it('counts forward from the active index', () => {
    expect(deckDepth(3, 2, 5)).toBe(1);
    expect(deckDepth(4, 2, 5)).toBe(2);
  });

  it('wraps round the end of the list', () => {
    // Active at the last index; index 0 is one step forward, wrapping round.
    expect(deckDepth(0, 4, 5)).toBe(1);
  });

  it('wraps when the active index itself is ahead of the queried one', () => {
    expect(deckDepth(1, 3, 5)).toBe(3);
  });

  it('is 0 for an empty list rather than dividing by zero', () => {
    expect(deckDepth(0, 0, 0)).toBe(0);
  });
});

describe('deckLayout', () => {
  it('marks depth 0 as the front card, undimmed and unshrunk', () => {
    const front = deckLayout(0);
    expect(front.isFront).toBe(true);
    expect(front.hidden).toBe(false);
    expect(front.opacity).toBe(1);
    expect(front.transform).toBe('translate(0px, 0px) scale(1)');
    expect(front.zIndex).toBe(DECK_MAX_VISIBLE);
  });

  it('shrinks, fades and pushes back monotonically as depth increases', () => {
    const a = deckLayout(1);
    const b = deckLayout(2);
    const c = deckLayout(3);
    // Read the scale back out of the transform string rather than duplicating the constant here,
    // so this test tracks the actual rendered value.
    const scaleOf = (transform: string) => Number(transform.match(/scale\(([\d.]+)\)/)?.[1]);
    expect(scaleOf(a.transform)).toBeGreaterThan(scaleOf(b.transform));
    expect(scaleOf(b.transform)).toBeGreaterThan(scaleOf(c.transform));
    expect(a.opacity).toBeGreaterThan(b.opacity);
    expect(b.opacity).toBeGreaterThan(c.opacity);
    expect(a.zIndex).toBeGreaterThan(b.zIndex);
    expect(b.zIndex).toBeGreaterThan(c.zIndex);
  });

  it('floors scale and opacity rather than letting a deep card vanish or invert', () => {
    const deep = deckLayout(9);
    const scaleOf = (transform: string) => Number(transform.match(/scale\(([\d.]+)\)/)?.[1]);
    expect(scaleOf(deep.transform)).toBeGreaterThanOrEqual(0.5);
    expect(deep.opacity).toBeGreaterThan(0);
  });

  it('hides a card at or beyond maxVisible, rather than laying out an ever-fainter sliver', () => {
    const atCap = deckLayout(10, 10);
    const beyondCap = deckLayout(11, 10);
    expect(atCap.hidden).toBe(true);
    expect(atCap.opacity).toBe(0);
    expect(beyondCap.hidden).toBe(true);
  });

  it('keeps zIndex ordering consistent with the visible cap', () => {
    const front = deckLayout(0, 10);
    const back = deckLayout(9, 10);
    expect(front.zIndex).toBeGreaterThan(back.zIndex);
    expect(back.zIndex).toBeGreaterThanOrEqual(0);
  });
});

describe('deckStageSize', () => {
  it('is exactly the card size for a single visible card', () => {
    expect(deckStageSize(200, 100, 1)).toEqual({ width: 200, height: 100 });
  });

  it('grows with the visible count, so the fan has room to peek', () => {
    const one = deckStageSize(200, 100, 1);
    const three = deckStageSize(200, 100, 3);
    expect(three.width).toBeGreaterThan(one.width);
    expect(three.height).toBeGreaterThan(one.height);
  });

  it('never shrinks below the card size for zero visible cards', () => {
    expect(deckStageSize(200, 100, 0)).toEqual({ width: 200, height: 100 });
  });
});

describe('deckCounterLabel', () => {
  it('is one-indexed for a reader, not zero-indexed like the state', () => {
    expect(deckCounterLabel(0, 5)).toBe('1 of 5');
    expect(deckCounterLabel(4, 5)).toBe('5 of 5');
  });

  it('reads as empty rather than dividing by zero for an empty list', () => {
    expect(deckCounterLabel(0, 0)).toBe('0 of 0');
  });
});
