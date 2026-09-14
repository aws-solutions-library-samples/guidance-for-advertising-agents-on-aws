/**
 * A card panel: labelled rows of facts.
 *
 * Used by Bind, Plan and Book. Every row is a field an agent actually returned; the deriver produces
 * no row for a field that was absent, so there is no blank value and no dash standing in for one.
 */

import type { CardRow } from '../../../lib/journey/types';

export function CardBody({ rows, revealed }: { rows: readonly CardRow[]; revealed: boolean }) {
  return (
    <div className={`deal${revealed ? ' on' : ''}`}>
      {rows.map((row) => (
        <div className="drow" key={row.key}>
          <span className="dk">{row.key}</span>
          <span className={`dv${row.mono === true ? ' mono' : ''}`}>
            {row.live === true ? (
              <span className="live">
                <span className="live-dot" /> {row.value}
              </span>
            ) : (
              row.value
            )}
          </span>
        </div>
      ))}
    </div>
  );
}
