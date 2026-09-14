/**
 * Plan: the registered campaign, as a figure, a window and two sets of chips.
 *
 * Eight label/value rows became four groups that each read at a glance: the budget as the one large
 * figure, the flight as a window, the authorised channels as chips, and the categories the governance
 * agent will assess as a visually distinct second set of chips.
 *
 * ## Two things this deliberately does NOT draw
 *
 * **The flight is a window, not a progress bar.** A filled bar would read as elapsed delivery, and
 * nothing in a `sync_plans` response measures delivery. It is a span between two end ticks with no
 * fill, labelled as the authorised window.
 *
 * **`will evaluate` is not a verdict.** Those are the categories the agent said it will assess. They
 * are tinted neutrally and carry no tick, because a green chip beside "geo_compliance" would claim the
 * campaign had passed a check that has not run. Registering a plan is not approval.
 *
 * Terms the agent did not echo back are marked `submitted`, since they are read from the recorded
 * request. See `governanceCards.ts` for why that distinction is load-bearing rather than pedantic.
 */

import type { PlanData } from '../../../lib/journey/types';

function Chips({ values, kind }: { values: readonly string[]; kind?: 'willeval' }) {
  return (
    <div className="gchips">
      {values.map((value) => (
        <span className={`gchip${kind === 'willeval' ? ' willeval' : ''}`} key={value}>
          {value}
        </span>
      ))}
    </div>
  );
}

export function PlanVizBody({ data, revealed }: { data: PlanData; revealed: boolean }) {
  const hasWindow = data.flightStart !== null && data.flightEnd !== null;

  return (
    <div className={`gplan${revealed ? ' on' : ''}`}>
      {/* The budget only renders with a figure. A currency alone under a blank would read as a
          value that failed to load. `0` is a figure and does render. */}
      {data.budgetAmount !== null ? (
        <div className="gbudget">
          <span className="gamount">{data.budgetAmount}</span>
          <span className="gcur">
            {data.currency !== null ? `${data.currency} submitted` : 'submitted'}
          </span>
        </div>
      ) : null}

      <div className="gplan-main">
        <div className="gmeta">
          {data.planId !== null ? <span className="gbadge">{data.planId}</span> : null}
          {data.status !== null ? (
            <span className="gbadge">
              status <b>{data.status}</b>
            </span>
          ) : null}
          {data.version !== null ? (
            <span className="gbadge">
              v<b>{data.version}</b>
            </span>
          ) : null}
        </div>

        {hasWindow ? (
          <div className="gflight">
            <div className="gfrow">
              <span>{data.flightStart}</span>
              <span>{data.flightEnd}</span>
            </div>
            {/* Decorative: both dates are already stated above it. */}
            <div className="gspan" aria-hidden="true">
              <span className="gtick s" />
              <span className="gtick e" />
            </div>
            <div className="glabel">flight window submitted</div>
          </div>
        ) : null}

        {data.channels.length > 0 ? (
          <div>
            <div className="glabel">channels submitted</div>
            <Chips values={data.channels} />
          </div>
        ) : null}

        {data.willEvaluate.length > 0 ? (
          <div>
            <div className="glabel">the governance agent will evaluate</div>
            <Chips values={data.willEvaluate} kind="willeval" />
          </div>
        ) : null}

        {/* An empty array is an answer and says so. A null means the field was absent, which is not
            an answer, so nothing is shown rather than implying the agent reported none. */}
        {data.resolvedPolicies !== null ? (
          <div className="dg-note">
            {data.resolvedPolicies.length > 0
              ? `resolved policies: ${data.resolvedPolicies.join(', ')}`
              : 'resolved policies: none returned'}
          </div>
        ) : null}
      </div>
    </div>
  );
}
