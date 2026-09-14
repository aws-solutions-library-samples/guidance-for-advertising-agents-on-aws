/**
 * The dashed arrows between consecutive phases.
 *
 * One arrow per adjacent pair of revealed phases, drawn through the gap between their two cells. Under
 * the serpentine placement the next phase is always beside or directly below the previous one, so every
 * arrow is a short axis-aligned run and none crosses a card.
 *
 * Presentation only: the geometry is `lib/journey/flowLayout.ts`'s and the measurement is
 * `useFlowGeometry`'s. Nothing here decides where a line goes.
 *
 * DOM rather than SVG, following `.glink` in journeyGovernance.css — the existing connector in this
 * project is a positioned element with a gradient track and a CSS-triangle head, and a second
 * technique for the same idea would be two things to maintain. The dashes are a repeating gradient and
 * the draw-on is a `clip-path` wipe, which reveals them progressively without scaling them; animating
 * a `transform: scaleX` instead would stretch each dash as the line grew.
 *
 * `aria-hidden`, and deliberately: the arrows restate the phase order, which the rail already conveys
 * as text and which the panels convey by their own numbered headings. A screen reader announcing eight
 * decorative arrows would be adding noise, not information.
 */

import { connectorBetween, type FlowRect } from '../../lib/journey/flowLayout';

export interface FlowConnectorsProps {
  /** Phase keys in registry order, filtered to those actually rendered. */
  readonly keys: readonly string[];
  /** Which of them have been revealed. An arrow needs both ends present. */
  readonly revealed: ReadonlySet<string>;
  readonly rects: ReadonlyMap<string, FlowRect>;
  /** Accent colour per phase, for the arrow that arrives at it. */
  readonly colours: ReadonlyMap<string, string>;
}

export function FlowConnectors({ keys, revealed, rects, colours }: FlowConnectorsProps) {
  const arrows = [];

  for (let i = 1; i < keys.length; i += 1) {
    const fromKey = keys[i - 1];
    const toKey = keys[i];
    if (fromKey === undefined || toKey === undefined) continue;
    // Both ends must be on screen. A phase beyond the frontier has no cell to point at, and pointing
    // at where it will eventually be would promise a step that has not happened.
    if (!revealed.has(fromKey) || !revealed.has(toKey)) continue;
    const from = rects.get(fromKey);
    const to = rects.get(toKey);
    if (!from || !to) continue;

    const line = connectorBetween(from, to);
    if (line === null) continue;

    const horizontal = line.axis === 'horizontal';
    const classes = [
      'flow-arrow',
      horizontal ? 'flow-arrow-h' : 'flow-arrow-v',
      line.direction === 'reverse' ? 'flow-arrow-back' : '',
    ]
      .filter(Boolean)
      .join(' ');

    arrows.push(
      <div
        // Keyed by the pair, so an arrow mounts once and therefore animates once. Re-keying on
        // geometry would redraw every arrow on every resize.
        key={`${fromKey}->${toKey}`}
        className={classes}
        style={{
          left: line.left,
          top: line.top,
          // The destination's colour: the arrow belongs to the step being introduced.
          ['--rc' as string]: colours.get(toKey) ?? 'var(--purple-lt)',
          ['--flow-arrow-len' as string]: `${line.length}px`,
        }}
      >
        <i className="flow-arrow-head" />
      </div>,
    );
  }

  if (arrows.length === 0) return null;
  return (
    <div className="flow-arrows" aria-hidden>
      {arrows}
    </div>
  );
}
