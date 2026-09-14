/**
 * The step rail across the top of the summary.
 *
 * Exactly one step is `on` at a time; earlier revealed steps are `done`; a step whose phase never had
 * data stays idle. A phase that never ran is not marked done just because a later one did, since that
 * would report work that did not happen.
 *
 * Status is conveyed by text as well as by colour, so it does not depend on colour alone.
 */

import type { RailStep } from '../../hooks/useJourneyPlayback';
import { CheckIcon } from './icons';

export function PhaseRail({ steps }: { steps: readonly RailStep[] }) {
  return (
    <div className="rail">
      {steps.map((step) => {
        const classes = ['rstep'];
        if (step.status === 'active') classes.push('on');
        if (step.status === 'done') classes.push('done');
        return (
          <div
            className={classes.join(' ')}
            key={step.key}
            style={{ ['--rc' as string]: step.col }}
          >
            <div className="rail-pad">
              <div className="rl">
                <span className="rn">{step.n}</span>
                <span className="rt">{step.name}</span>
                {/* Read by assistive technology; the visual state is the bar and the tick. */}
                <span className="sr-only" style={{ position: 'absolute', left: '-9999px' }}>
                  {step.status === 'active'
                    ? ' (in progress)'
                    : step.status === 'done'
                      ? ' (complete)'
                      : ' (not started)'}
                </span>
                <span className="rk">
                  <CheckIcon />
                </span>
              </div>
              <div className="rbar">
                <i />
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
