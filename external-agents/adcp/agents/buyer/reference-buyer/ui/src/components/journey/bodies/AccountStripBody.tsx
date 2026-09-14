/**
 * Accounts: the advertiser account each seller now holds, as a highlighted strip.
 *
 * `sync_accounts` fans out to every seller, and each returns the SAME account — keyed on the
 * (brand domain, operator) pair the buyer declared — with its own `account_id`. A flat row list of
 * that was one near-identical block per seller with nothing distinguishing them. This mirrors the
 * Media Buy strip instead: the shared account key sits in the header once, and the sellers scroll as
 * cards with one highlighted at a time.
 *
 * Every value shown is one a seller actually returned; the decoder (`decodeAccountStrip`) omits any
 * field a seller left out, and a failed reply renders its own error state rather than the fields a
 * success would carry — so a failure is never dressed up as a held account.
 *
 * ## Motion
 *
 * The list scrolls inside a bounded box, and one card is "active" at a time: full opacity while the
 * rest are dimmed. The active card is the one most in view — it follows the scroll (see
 * `useScrollSpyActive`) — and hovering a card pins it active until the pointer leaves. This is
 * presentation over settled data — the accounts already exist by the time this renders — not a
 * progress indicator. Under `prefers-reduced-motion` every card is active, so all of them are visible
 * at once with no movement.
 */

import { useEffect, useState } from 'react';

import { useScrollSpyActive } from '../../../hooks/useScrollSpyActive';
import type { AccountCard, AccountStripData } from '../../../lib/journey/types';

function usePrefersReducedMotion(): boolean {
  const [reduce, setReduce] = useState(false);
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return;
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    setReduce(mq.matches);
    const handler = () => setReduce(mq.matches);
    mq.addEventListener?.('change', handler);
    return () => mq.removeEventListener?.('change', handler);
  }, []);
  return reduce;
}

/** The account's natural key, `brand / operator`, or whichever half was supplied. */
function accountKey(data: AccountStripData): string | null {
  if (data.brand !== null && data.operator !== null) return `${data.brand} / ${data.operator}`;
  return data.brand ?? data.operator;
}

function AccountHeader({ data }: { data: AccountStripData }) {
  const key = accountKey(data);
  return (
    <div className="acct-head">
      {key !== null ? (
        <div className="acct-account">
          <span className="acct-account-label">account</span>
          <span className="acct-account-key" title={key}>
            {key}
          </span>
        </div>
      ) : null}
      <span className="acct-chip">
        <span className="acct-lbl">sellers</span> {data.sellerCount}
      </span>
    </div>
  );
}

function AccountCardView({
  card,
  active,
  innerRef,
  onActivate,
  onLeave,
}: {
  card: AccountCard;
  active: boolean;
  innerRef: (el: HTMLElement | null) => void;
  onActivate: () => void;
  onLeave: () => void;
}) {
  const classes = ['acct-card'];
  if (active) classes.push('active');
  if (card.outcome === 'failed') classes.push('failed');

  return (
    <div ref={innerRef} className={classes.join(' ')} onMouseEnter={onActivate} onMouseLeave={onLeave}>
      <div className="acct-top">
        <span className="acct-seller">{card.seller}</span>
        <span className="acct-pills">
          {card.action !== null ? (
            <span className={`acct-pill action action-${card.action}`}>{card.action}</span>
          ) : null}
          {card.status !== null ? <span className="acct-pill status">{card.status}</span> : null}
        </span>
      </div>

      {card.outcome === 'failed' ? (
        <div className="acct-state failed">
          <div className="acct-state-line">{card.errorCode ?? 'failed'}</div>
          {card.errorDetail !== null ? (
            <div className="acct-state-sub">{card.errorDetail}</div>
          ) : null}
        </div>
      ) : card.accountId !== null ? (
        <div className="acct-id" title={card.accountId}>
          {card.accountId}
        </div>
      ) : null}
    </div>
  );
}

export function AccountStripBody({
  data,
  revealed,
}: {
  data: AccountStripData;
  revealed: boolean;
}) {
  const reduceMotion = usePrefersReducedMotion();
  const spy = useScrollSpyActive(data.cards.length, !reduceMotion);

  return (
    <div className={`acctstrip${revealed ? ' on' : ''}`}>
      <AccountHeader data={data} />
      <div className="acct-list" ref={spy.containerRef}>
        {data.cards.map((card, index) => (
          <AccountCardView
            key={card.key}
            card={card}
            // Every card is active under reduced motion; otherwise the card most in view is active.
            active={reduceMotion || index === spy.active}
            innerRef={spy.itemRef(index)}
            onActivate={() => spy.onItemEnter(index)}
            onLeave={spy.onItemLeave}
          />
        ))}
      </div>
    </div>
  );
}
