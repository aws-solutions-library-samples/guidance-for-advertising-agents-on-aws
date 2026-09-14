/**
 * The radial arcs, shared by the Govern and Score panels.
 *
 * One renderer for both, deliberately. The prototype gives them the same `radial` panel type, and a
 * component per phase would let the buyer-side and seller-side governance panels drift apart
 * visually, which the exact-reproduction rule forbids.
 *
 * The rule this component exists to hold: an arc whose finding reported NO confidence draws no bar at
 * all. Not a bar of length zero. A zero-length arc reads as a measured zero-confidence finding, which
 * is a different claim from "this finding carried no confidence", and the reader has no way to tell
 * them apart.
 */

import type { Arc } from '../../../lib/journey/types';
import { CheckIcon } from '../icons';

export interface Verdict {
  /** The headline count, e.g. how many findings. */
  readonly count: number;
  readonly countSuffix: string;
  readonly title: string;
  readonly sub: string;
  readonly pill: string;
  /**
   * The governance mode this verdict was produced under, when it was not `enforce`.
   *
   * Shown because an `approved` from an audit-mode agent means the agent always approves and could
   * not have blocked anything. Presenting that identically to an enforced approval would overstate
   * the strongest claim on the screen. Null when the mode was `enforce`; the string "not reported"
   * when the agent did not say, which is a third state and must not read as `enforce`.
   */
  readonly modeNote: string | null;
}

export function RadialBody({
  arcs,
  verdict,
  revealed,
}: {
  arcs: readonly Arc[];
  verdict: Verdict;
  revealed: boolean;
}) {
  return (
    <>
      <div className="radial-wrap">
        <div className="radial">
          <svg width="180" height="180" viewBox="0 0 180 180">
            {arcs.map((arc) => {
              const circumference = 2 * Math.PI * arc.radius;
              return (
                <circle
                  key={`track-${arc.categoryId}`}
                  cx={90}
                  cy={90}
                  r={arc.radius}
                  className="track"
                  strokeDasharray={circumference}
                />
              );
            })}
            {arcs.map((arc) => {
              // No confidence, no bar. See this file's header.
              if (arc.confidence === null) return null;
              const circumference = 2 * Math.PI * arc.radius;
              return (
                <circle
                  key={`bar-${arc.categoryId}`}
                  cx={90}
                  cy={90}
                  r={arc.radius}
                  className={`bar${revealed ? ' on' : ''}`}
                  stroke={arc.colour}
                  style={{
                    ['--c' as string]: `${circumference}`,
                    ['--off' as string]: `${circumference * (1 - arc.confidence)}`,
                  }}
                />
              );
            })}
          </svg>
        </div>
        <div className="rlegend">
          {arcs.map((arc) => (
            <div className={`rl${revealed ? ' on' : ''}`} key={arc.categoryId}>
              <div className="rt2">
                <span className="dot" style={{ background: arc.colour }} />
                {arc.categoryId}
                <span className="rv">
                  {/* An absent confidence is stated as absent rather than rendered as 0%. */}
                  {arc.confidence === null
                    ? 'not reported'
                    : `${Math.round(arc.confidence * 100)}%`}
                </span>
              </div>
              <div className="rd">
                {arc.explanation}
                {arc.severity ? ` \u00b7 ${arc.severity}` : ''}
              </div>
            </div>
          ))}
        </div>
      </div>
      <div className={`verdict${revealed ? ' on' : ''}`}>
        <div className="bigwrap">
          <div className="big">{verdict.count}</div>
          <div className="bigsuf">{verdict.countSuffix}</div>
        </div>
        <div>
          <div className="vt">{verdict.title}</div>
          <div className="vs">{verdict.sub}</div>
          <span className="pill pass">
            <CheckIcon /> {verdict.pill}
          </span>
          {verdict.modeNote !== null ? (
            // Same pill styling, so nothing visual is introduced; only the qualification is added.
            <span className="pill pass">{verdict.modeNote}</span>
          ) : null}
        </div>
      </div>
    </>
  );
}
