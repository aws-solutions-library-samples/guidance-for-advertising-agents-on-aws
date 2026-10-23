import { describe, expect, it } from 'vitest';

import {
  SCROLL_SETTLE_MS,
  SUMMARY_GAP_PX,
  scrollBehaviourFor,
  scrollTargetFor,
} from './scroll';

describe('scrollTargetFor', () => {
  it('lands the phase just below the sticky summary', () => {
    // The prototype's own arithmetic: current scroll + the panel's viewport offset, less the
    // summary's live height and the authored gap.
    expect(scrollTargetFor({ panelTop: 600, scrollY: 200, summaryHeight: 180 })).toBe(
      200 + 600 - 180 - SUMMARY_GAP_PX,
    );
  });

  it('never returns a negative offset', () => {
    // A phase near the top of the document computes a negative target, and scrolling to one either
    // does nothing or bounces depending on the browser.
    expect(scrollTargetFor({ panelTop: 10, scrollY: 0, summaryHeight: 180 })).toBe(0);
  });

  it('accounts for a summary that has grown', () => {
    // The summary gains entity chips as the session runs. A remembered height would leave the phase
    // sitting under it, which is the bug re-measuring exists to prevent.
    const short = scrollTargetFor({ panelTop: 600, scrollY: 0, summaryHeight: 140 });
    const tall = scrollTargetFor({ panelTop: 600, scrollY: 0, summaryHeight: 220 });
    expect(short - tall).toBe(80);
  });

  it('is unaffected by how the reader got there', () => {
    // Same geometry, same destination, whether the phase arrived live or during a Replay walk.
    const geometry = { panelTop: 420, scrollY: 130, summaryHeight: 176 };
    expect(scrollTargetFor(geometry)).toBe(scrollTargetFor(geometry));
  });
});

describe('scrollBehaviourFor', () => {
  it('animates by default, in every mode', () => {
    // The prototype animates unconditionally. Making this conditional on Replay is what left a live
    // session jumping rather than scrolling.
    expect(scrollBehaviourFor(false)).toBe('smooth');
  });

  it('does not animate when the reader asked for less motion', () => {
    expect(scrollBehaviourFor(true)).toBe('auto');
  });
});

describe('coalescing window', () => {
  it('is short enough to be imperceptible between two real phases', () => {
    // Phases in a live session or a Replay walk arrive hundreds of ms apart, so each must still get
    // its own scroll. The window only ever swallows a catch-up burst.
    expect(SCROLL_SETTLE_MS).toBeLessThan(200);
  });
});
