/**
 * Bind: the governance binding drawn as a flow rather than a list of rows.
 *
 * Three facts, and they have a direction: a governance agent is registered AT a seller, AGAINST an
 * account. Two labelled nodes with a link between them says that in one glance; three label/value
 * rows said it in three lines and did not say the direction at all.
 *
 * The travelling pulse runs agent to account, which is the direction the registration actually
 * travels. It is decoration on a completed fact, not a progress indicator: this panel only renders
 * once the seller has confirmed the binding, so nothing here is animating in anticipation of a result.
 *
 * A node with no value renders nothing. There is no dash and no "n/a" standing in for a field a seller
 * did not return.
 */

import type { BindData, BindLink } from '../../../lib/journey/types';

function Node({
  label,
  value,
  sub,
  mono,
}: {
  label: string;
  value: string;
  sub?: string;
  mono?: boolean;
}) {
  return (
    <div className="gnode">
      <div className="gk">{label}</div>
      <div className={`gv${mono === true ? ' mono' : ''}`}>{value}</div>
      {sub !== undefined ? <div className="gsub">{sub}</div> : null}
    </div>
  );
}

function Link({ revealed, link }: { revealed: boolean; link: BindLink }) {
  const agent = link.agent;
  const account = link.account;

  return (
    <div className={`gbind${revealed ? ' on' : ''}`}>
      {agent !== null ? <Node label="governance agent" value={agent} mono /> : null}
      {agent !== null && account !== null ? (
        // Decorative, and hidden from assistive technology: the relationship it draws is already
        // stated by the two labelled nodes either side of it.
        <div className="glink" aria-hidden="true">
          <span className="gtrack" />
          <span className="gdot" />
          <span className="gtip" />
        </div>
      ) : null}
      {account !== null ? <Node label="bound to account" value={account} /> : null}
      {link.status !== null ? (
        <span className="gstatus">
          <span className="live-dot" />
          {link.status}
        </span>
      ) : null}
    </div>
  );
}

export function BindFlowBody({ data, revealed }: { data: BindData; revealed: boolean }) {
  const several = data.links.length > 1;

  return (
    <div className="gbindset">
      {data.links.map((link, index) => (
        <div key={link.sellerId ?? `link-${index}`}>
          {/* Only when more than one seller bound. A single unlabelled row reads cleanly, and naming
              the one seller twice (here and in the panel's own role line) is noise. */}
          {several && link.sellerId !== null ? <div className="gseller">{link.sellerId}</div> : null}
          <Link revealed={revealed} link={link} />
        </div>
      ))}
    </div>
  );
}
