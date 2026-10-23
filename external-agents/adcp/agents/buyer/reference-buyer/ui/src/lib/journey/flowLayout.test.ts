import { describe, expect, it } from 'vitest';

import {
  connectorBetween,
  FLOW_GAP_PX,
  packFlowItems,
  type FlowItem,
  type FlowRect,
} from './flowLayout';

const card = (height: number): FlowItem => ({ kind: 'card', height });
const wide = (height: number): FlowItem => ({ kind: 'wide', height });

/** Where a card's box ends, excluding the trailing gap it reserves. */
const bottomOf = (p: { rowStart: number; rowSpan: number }) => p.rowStart + p.rowSpan - FLOW_GAP_PX;

describe('packFlowItems: the next card goes into the shortest column', () => {
  it('fills left to right when the columns are level', () => {
    const placed = packFlowItems([card(300), card(300)], 2);
    expect(placed.map((p) => p.column)).toEqual([1, 2]);
    expect(placed.map((p) => p.rowStart)).toEqual([1, 1]);
  });

  it('puts the third card under the SHORTER of the two, not back at the left', () => {
    // The rule stated as "wrap to the right when the card above on the right is shorter". Card 2 is the
    // short one, so card 3 belongs beneath it.
    const placed = packFlowItems([card(600), card(200), card(100)], 2);
    expect(placed[2]?.column).toBe(2);
    expect(placed[2]?.rowStart).toBe(200 + FLOW_GAP_PX + 1);
  });

  it('puts it under the left card when the right one is taller', () => {
    // The inverse case, and the one in the screenshot: a tall card on the right means the next box goes
    // under the first item of the pair.
    const placed = packFlowItems([card(200), card(600), card(100)], 2);
    expect(placed[2]?.column).toBe(1);
    expect(placed[2]?.rowStart).toBe(200 + FLOW_GAP_PX + 1);
  });

  it('leaves no gap the size of the height difference', () => {
    // The defect this replaces. In a row-based grid the third card would start below the TALLER of the
    // first two, stranding 400px of dead space under the shorter one.
    const placed = packFlowItems([card(600), card(200), card(100)], 2);
    const shortCardBottom = bottomOf(placed[1]!);
    expect(placed[2]?.rowStart).toBe(shortCardBottom + FLOW_GAP_PX);
    // Explicitly: it does NOT wait for the tall card.
    expect(placed[2]!.rowStart).toBeLessThan(bottomOf(placed[0]!));
  });

  it('keeps stacking into whichever column is behind', () => {
    // Four cards of very different heights: each one lands under the column that is currently shortest,
    // so neither column runs far ahead of the other.
    const placed = packFlowItems([card(500), card(100), card(100), card(100)], 2);
    expect(placed.map((p) => p.column)).toEqual([1, 2, 2, 2]);
  });
});

describe('packFlowItems: order is preserved', () => {
  it('never starts a card above the one before it', () => {
    // Two cards can begin level — that is what filling two columns looks like — but a later card must not
    // begin higher than an earlier one, because the flow is a chronology.
    const heights = [420, 180, 260, 640, 120, 300, 90, 510];
    const placed = packFlowItems(heights.map(card), 2);
    placed.forEach((p, i) => {
      if (i === 0) return;
      expect(p.rowStart).toBeGreaterThanOrEqual(placed[i - 1]!.rowStart);
    });
  });

  it('reserves exactly one gap per card', () => {
    const placed = packFlowItems([card(300)], 2);
    expect(placed[0]?.rowSpan).toBe(300 + FLOW_GAP_PX);
  });

  it('leaves the vertical gap equal to the column gap, and no more', () => {
    /**
     * The visible gap below a card is `rowSpan - height`, so it is exactly `FLOW_GAP_PX` only while the
     * height fed in here is the card's real rendered height. It was not: heights were measured with
     * `scrollHeight`, which reported systematically more than the layout height, and every card carried
     * that excess as extra space beneath it — so a card stacked under another sat lower than the column
     * gap beside it, and the two columns visibly disagreed.
     *
     * The arithmetic is pinned here; `useFlowGeometry` is where the height now comes from
     * (`getBoundingClientRect().height` on the untransformed wrapper).
     */
    const heights = [180, 640, 260];
    const placed = packFlowItems(heights.map(card), 2);
    placed.forEach((p, i) => {
      expect(p.rowSpan - (heights[i] as number)).toBe(FLOW_GAP_PX);
    });

    // And the same holds between two cards stacked in one column: card 3 goes under card 1 (the shorter),
    // exactly one gap below where card 1 ends.
    expect(placed[2]?.column).toBe(placed[0]?.column);
    expect(placed[2]!.rowStart - bottomOf(placed[0]!)).toBe(FLOW_GAP_PX);
  });

  it('rounds a fractional height up', () => {
    // A fractional row unit leaves a sub-pixel sliver of the next card showing through the gap.
    expect(packFlowItems([card(300.2)], 2)[0]?.rowSpan).toBe(301 + FLOW_GAP_PX);
  });
});

describe('packFlowItems: wide items clear every column', () => {
  it('spans all tracks', () => {
    expect(packFlowItems([wide(400)], 2)[0]).toMatchObject({ column: 1, span: 2 });
  });

  it('starts below the TALLEST column, not the shortest', () => {
    // It covers both columns, so it cannot begin until both have finished. Starting at the shortest would
    // overlap the taller card.
    const placed = packFlowItems([card(600), card(200), wide(300)], 2);
    expect(placed[2]?.rowStart).toBe(600 + FLOW_GAP_PX + 1);
  });

  it('levels the columns behind it, so packing resumes from a clean line', () => {
    const placed = packFlowItems([card(600), card(200), wide(300), card(50), card(50)], 2);
    // Both cards after the wide item start at the same line.
    expect(placed[3]?.rowStart).toBe(placed[4]?.rowStart);
    expect(placed[3]?.column).toBe(1);
    expect(placed[4]?.column).toBe(2);
  });

  it('handles a wide item first, which is the real case', () => {
    // Discover leads the registry and is the only wide panel.
    const placed = packFlowItems([wide(500), card(200), card(300)], 2);
    expect(placed[0]).toMatchObject({ column: 1, span: 2, rowStart: 1 });
    expect(placed[1]?.rowStart).toBe(500 + FLOW_GAP_PX + 1);
    expect(placed[2]?.rowStart).toBe(500 + FLOW_GAP_PX + 1);
  });

  it('stacks consecutive wide items', () => {
    // The status line, the notice and Discover can all be present at once.
    const placed = packFlowItems([wide(40), wide(40), wide(400)], 2);
    expect(placed.map((p) => p.rowStart)).toEqual([1, 61, 121]);
    placed.forEach((p) => expect(p.span).toBe(2));
  });
});

describe('packFlowItems: degenerate inputs', () => {
  it('stacks in one column', () => {
    const placed = packFlowItems([card(100), card(200)], 1);
    expect(placed.map((p) => p.column)).toEqual([1, 1]);
    expect(placed[1]?.rowStart).toBe(100 + FLOW_GAP_PX + 1);
  });

  it('treats an unmeasured column count as one column rather than dividing by zero', () => {
    for (const columns of [0, -3, 0.4]) {
      const placed = packFlowItems([card(100), card(100)], columns);
      expect(placed.map((p) => p.column)).toEqual([1, 1]);
    }
  });

  it('assumes a plausible height before anything has been measured', () => {
    // Every card would otherwise pack at height zero and pile into column 1. The correction lands in a
    // layout effect before paint, but the uncorrected layout still has to be sane.
    const placed = packFlowItems([{ kind: 'card' }, { kind: 'card' }], 2);
    expect(placed.map((p) => p.column)).toEqual([1, 2]);
    placed.forEach((p) => expect(p.rowSpan).toBeGreaterThan(FLOW_GAP_PX));
  });

  it('places nothing for no items', () => {
    expect(packFlowItems([], 2)).toEqual([]);
  });
});

const rect = (left: number, top: number, width = 100, height = 80): FlowRect => ({
  left,
  top,
  width,
  height,
});

describe('connectorBetween: side by side', () => {
  it('runs through the column gap, pointing right', () => {
    expect(connectorBetween(rect(0, 0), rect(120, 0))).toEqual({
      axis: 'horizontal',
      left: 100,
      top: 40,
      length: 20,
      direction: 'forward',
    });
  });

  it('points left when the next card is to the left', () => {
    expect(connectorBetween(rect(120, 0), rect(0, 0))).toMatchObject({
      axis: 'horizontal',
      direction: 'reverse',
    });
  });

  it('centres on the range the two cards share', () => {
    const c = connectorBetween(rect(0, 0, 100, 200), rect(120, 0, 100, 40));
    expect(c?.top).toBe(20);
  });

  it('still runs horizontally when the cards only partly overlap', () => {
    // Under masonry two consecutive cards are rarely level. Any vertical overlap at all means they read
    // as side by side.
    expect(connectorBetween(rect(0, 0, 100, 80), rect(120, 60, 100, 80))?.axis).toBe('horizontal');
  });

  it('stays INSIDE the overlap when the cards barely overlap', () => {
    /**
     * The defect: the centre was `max(tops) + min(heights) / 2`, which equals the overlap's midpoint only
     * when both cards start at the same height. Here they overlap over [60, 80] and the old formula gave
     * 100 — past the bottom of both cards, so the arrow floated in the gutter attached to nothing. That is
     * the arrow that looked like it came out of a card's middle and pointed at empty space.
     */
    const from = rect(0, 0, 100, 80);
    const to = rect(120, 60, 100, 300);
    const c = connectorBetween(from, to);
    expect(c?.top).toBe(70);
    // Stated as the invariant rather than just the number: the arrow must touch both cards.
    expect(c!.top).toBeGreaterThanOrEqual(Math.max(from.top, to.top));
    expect(c!.top).toBeLessThanOrEqual(
      Math.min(from.top + from.height, to.top + to.height),
    );
  });

  it('touches both cards for every overlap, whichever is taller or lower', () => {
    // The real masonry shape: a tall card in one column beside a shorter one that started lower.
    const cases: readonly [FlowRect, FlowRect][] = [
      [rect(0, 0, 100, 500), rect(120, 300, 100, 400)],
      [rect(0, 300, 100, 400), rect(120, 0, 100, 500)],
      [rect(0, 0, 100, 60), rect(120, 40, 100, 900)],
      [rect(0, 200, 100, 900), rect(120, 180, 100, 60)],
    ];
    for (const [from, to] of cases) {
      const c = connectorBetween(from, to);
      expect(c).not.toBeNull();
      const top = Math.max(from.top, to.top);
      const bottom = Math.min(from.top + from.height, to.top + to.height);
      expect(c!.top).toBeGreaterThanOrEqual(top);
      expect(c!.top).toBeLessThanOrEqual(bottom);
    }
  });
});

describe('connectorBetween: stacked', () => {
  it('drops vertically through the gap', () => {
    expect(connectorBetween(rect(0, 0), rect(0, 100))).toEqual({
      axis: 'vertical',
      left: 50,
      top: 80,
      length: 20,
      direction: 'forward',
    });
  });

  it('centres on the overlap, not on a wide panel it hangs from', () => {
    // Discover spans both columns. Dropping from ITS centre would land the arrow well to the side of the
    // card it feeds; the overlap's centre is that card's own centre.
    expect(connectorBetween(rect(0, 0, 900, 80), rect(620, 100, 280, 80))?.left).toBe(760);
  });

  it('aims at the destination when the two share no horizontal range', () => {
    expect(connectorBetween(rect(0, 0, 100, 80), rect(400, 100, 100, 80))?.left).toBe(450);
  });
});

describe('connectorBetween: nothing worth drawing', () => {
  it('returns null for touching cards', () => {
    expect(connectorBetween(rect(0, 0), rect(100, 0))).toBeNull();
    expect(connectorBetween(rect(0, 0), rect(0, 80))).toBeNull();
  });

  it('returns null for a sub-pixel gap', () => {
    expect(connectorBetween(rect(0, 0), rect(100.4, 0))).toBeNull();
  });

  it('returns null for overlapping cards', () => {
    expect(connectorBetween(rect(0, 0), rect(40, 0))).toBeNull();
  });
});
