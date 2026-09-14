/**
 * The delivery meters.
 *
 * A meter with no real source stays UNFILLED and says why. The clearest case is fill rate: it is
 * normally delivered impressions over available impressions, and AdCP's delivery metrics carry no
 * availability figure, so there is no denominator to draw against. Filling that track from something else
 * would put a measurement on screen that nothing measured.
 */

import { METER_GRADIENT, METER_SEGMENTS } from '../../../lib/journey/phases';
import type { Meter } from '../../../lib/journey/types';

/** The authored pink-to-orange gradient across a track. */
function segmentColour(position: number): string {
  const t = METER_SEGMENTS > 1 ? position / (METER_SEGMENTS - 1) : 0;
  const { from, to } = METER_GRADIENT;
  const r = Math.round(from.r + (to.r - from.r) * t);
  const g = Math.round(from.g + (to.g - from.g) * t);
  const b = Math.round(from.b + (to.b - from.b) * t);
  return `rgb(${r},${g},${b})`;
}

export function MetersBody({ meters, revealed }: { meters: readonly Meter[]; revealed: boolean }) {
  return (
    <div className="meters">
      {meters.map((meter) => {
        // A null fraction lights nothing. The text carries the reason.
        const lit = meter.fraction === null ? 0 : Math.round(meter.fraction * METER_SEGMENTS);
        return (
          <div className={`meter${revealed ? ' on' : ''}`} key={meter.name}>
            <div className="mtop">
              <span>{meter.name}</span>
              <span className="mv">{meter.text}</span>
            </div>
            <div className="mtrack">
              {Array.from({ length: METER_SEGMENTS }, (_unused, index) => {
                const isLit = revealed && index < lit;
                return (
                  <i
                    key={index}
                    className={`seg${isLit ? ' lit' : ''}${isLit && index === lit - 1 ? ' tip' : ''}`}
                    style={{ ['--sc' as string]: segmentColour(index) }}
                  />
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}
