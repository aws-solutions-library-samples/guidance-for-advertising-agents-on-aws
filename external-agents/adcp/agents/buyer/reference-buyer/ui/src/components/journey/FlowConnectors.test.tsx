/**
 * Which arrows get drawn, and which deliberately do not.
 *
 * The geometry itself is `flowLayout.test.ts`'s. What is tested here is the gating: an arrow asserts
 * "the run went from here to there", so it must not appear unless both ends are genuinely on screen.
 */

import { cleanup, render } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import type { FlowRect } from '../../lib/journey/flowLayout';
import { FlowConnectors } from './FlowConnectors';

afterEach(cleanup);

const rect = (left: number, top: number, width = 200, height = 100): FlowRect => ({
  left,
  top,
  width,
  height,
});

const colours = new Map([
  ['a', '#8c2afc'],
  ['b', '#e4006c'],
  ['c', '#f06c00'],
]);

function renderArrows(
  keys: readonly string[],
  revealed: readonly string[],
  rects: ReadonlyMap<string, FlowRect>,
) {
  return render(
    <FlowConnectors
      keys={keys}
      revealed={new Set(revealed)}
      rects={rects}
      colours={colours}
    />,
  );
}

const sideBySide = new Map([
  ['a', rect(0, 0)],
  ['b', rect(220, 0)],
]);

describe('FlowConnectors: an arrow needs both ends present', () => {
  it('draws one between two revealed neighbours', () => {
    const { container } = renderArrows(['a', 'b'], ['a', 'b'], sideBySide);
    expect(container.querySelectorAll('.flow-arrow')).toHaveLength(1);
  });

  it('draws nothing when the destination has not been revealed', () => {
    // Pointing at a phase beyond the frontier would promise a step that has not happened.
    const { container } = renderArrows(['a', 'b'], ['a'], sideBySide);
    expect(container.querySelector('.flow-arrow')).toBeNull();
  });

  it('draws nothing when a cell has not been measured yet', () => {
    // The first render happens before the layout effect has read any positions. An arrow placed at 0,0
    // would flash in the corner of the panel.
    const { container } = renderArrows(['a', 'b'], ['a', 'b'], new Map([['a', rect(0, 0)]]));
    expect(container.querySelector('.flow-arrow')).toBeNull();
  });

  it('renders no container at all when there is nothing to draw', () => {
    const { container } = renderArrows(['a'], ['a'], new Map([['a', rect(0, 0)]]));
    expect(container.querySelector('.flow-arrows')).toBeNull();
  });

  it('skips a pair with no gap between them rather than drawing a headless stub', () => {
    const flush = new Map([
      ['a', rect(0, 0)],
      ['b', rect(200, 0)],
    ]);
    const { container } = renderArrows(['a', 'b'], ['a', 'b'], flush);
    expect(container.querySelector('.flow-arrow')).toBeNull();
  });
});

describe('FlowConnectors: orientation follows the serpentine', () => {
  it('marks a same-row arrow horizontal and forward', () => {
    const { container } = renderArrows(['a', 'b'], ['a', 'b'], sideBySide);
    const arrow = container.querySelector('.flow-arrow')!;
    expect(arrow.classList).toContain('flow-arrow-h');
    expect(arrow.classList).not.toContain('flow-arrow-back');
  });

  it('marks a right-to-left row arrow as reversed', () => {
    // The serpentine's alternating rows: the run continues leftward, so the head swaps ends.
    const reversed = new Map([
      ['a', rect(220, 0)],
      ['b', rect(0, 0)],
    ]);
    const { container } = renderArrows(['a', 'b'], ['a', 'b'], reversed);
    expect(container.querySelector('.flow-arrow')!.classList).toContain('flow-arrow-back');
  });

  it('marks a row change vertical', () => {
    const stacked = new Map([
      ['a', rect(0, 0)],
      ['b', rect(0, 140)],
    ]);
    const { container } = renderArrows(['a', 'b'], ['a', 'b'], stacked);
    const arrow = container.querySelector('.flow-arrow')!;
    expect(arrow.classList).toContain('flow-arrow-v');
    expect(arrow.classList).not.toContain('flow-arrow-h');
  });

  it('takes the destination phase\u2019s colour, not the origin\u2019s', () => {
    // The arrow introduces the step it arrives at.
    const { container } = renderArrows(['a', 'b'], ['a', 'b'], sideBySide);
    expect(container.querySelector<HTMLElement>('.flow-arrow')!.style.getPropertyValue('--rc')).toBe(
      '#e4006c',
    );
  });
});

describe('FlowConnectors: chains', () => {
  it('draws one arrow per adjacent pair, not between every pair', () => {
    const three = new Map([
      ['a', rect(0, 0)],
      ['b', rect(220, 0)],
      ['c', rect(440, 0)],
    ]);
    const { container } = renderArrows(['a', 'b', 'c'], ['a', 'b', 'c'], three);
    expect(container.querySelectorAll('.flow-arrow')).toHaveLength(2);
  });

  it('does not bridge across an unrevealed phase in the middle', () => {
    // Skipping the gap would draw a line from 'a' straight to 'c', asserting a transition the run did
    // not make.
    const three = new Map([
      ['a', rect(0, 0)],
      ['b', rect(220, 0)],
      ['c', rect(440, 0)],
    ]);
    const { container } = renderArrows(['a', 'b', 'c'], ['a', 'c'], three);
    expect(container.querySelector('.flow-arrow')).toBeNull();
  });

  it('gives every arrow a head', () => {
    const three = new Map([
      ['a', rect(0, 0)],
      ['b', rect(220, 0)],
      ['c', rect(440, 0)],
    ]);
    const { container } = renderArrows(['a', 'b', 'c'], ['a', 'b', 'c'], three);
    expect(container.querySelectorAll('.flow-arrow-head')).toHaveLength(2);
  });

  it('hides the whole overlay from assistive technology', () => {
    // The arrows restate the phase order, which the rail already conveys as text.
    const { container } = renderArrows(['a', 'b'], ['a', 'b'], sideBySide);
    expect(container.querySelector('.flow-arrows')!.getAttribute('aria-hidden')).toBe('true');
  });
});
