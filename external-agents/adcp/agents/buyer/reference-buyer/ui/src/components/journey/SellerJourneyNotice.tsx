/**
 * Names the other seller journeys present in this session.
 *
 * One journey is on screen at a time, chosen by the configured priority. The others are named rather
 * than hidden, and an errored seller is marked as errored: "could not be asked" and "has nothing" are
 * different facts about a seller's business, and collapsing them would report one as the other.
 *
 * Renders nothing when there are no others, so no empty box appears.
 */

import type { CSSProperties } from 'react';

import type { SellerJourney } from '../../lib/journey/types';

export function SellerJourneyNotice({
  others,
  style,
  packed,
}: {
  others: readonly SellerJourney[];
  /** Marks this as a child the flow packs, so its height is measured in DOM order with the rest. */
  packed?: boolean;
  /**
   * Grid placement, supplied by the flow.
   *
   * Applied to this component's own root rather than a wrapper: the notice's guarantee is that nothing
   * sits between it and the Discover panel, and a wrapper would make its `nextElementSibling` the
   * wrapper's own end rather than that panel.
   */
  style?: CSSProperties;
}) {
  if (others.length === 0) return null;
  return (
    <div
      className="dg-note"
      data-testid="seller-journey-notice"
      {...(packed === true ? { 'data-flow-cell': '' } : {})}
      style={style}
    >
      {others.map((journey, index) => (
        <span key={journey.sellerId}>
          {index > 0 ? ' \u00b7 ' : ''}
          {journey.sellerName}
          {journey.outcome === 'errored' ? ' (errored)' : ''}
          {journey.summary ? `: ${journey.summary}` : ''}
        </span>
      ))}
    </div>
  );
}
