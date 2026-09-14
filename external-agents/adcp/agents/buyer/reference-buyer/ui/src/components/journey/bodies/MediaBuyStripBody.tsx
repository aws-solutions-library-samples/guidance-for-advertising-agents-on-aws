/**
 * Book: the media buy as a strip of package cards.
 *
 * The structure is harvested from the "Package Strip Chart" mockup — a header with the buy's summable
 * facts and a flight window, a scroll of package cards each with a budget bar and an expandable detail
 * drawer, and a pacing legend — and rebuilt with the journey's own palette and reveal conventions
 * (`styles/journey-mediabuy.css`), which is ours rather than the byte-checked `journey.css`.
 *
 * Every value shown is one a seller actually echoed on the `create_media_buy` response; the decoder
 * (`decodeMediaBuyStrip`) omits any field a seller left out, so a section with nothing to say renders
 * nothing rather than a dash. A refused, in-flight or failed buy renders its own card state rather
 * than a budget bar, so a declined buy is never dressed up as a booked one.
 *
 * ## Motion
 *
 * The list scrolls inside a bounded box, and one card is "active" at a time: it sits at full opacity
 * with its detail drawer open while the rest are dimmed. The active card is the one most in view —
 * it follows the scroll (see `useScrollSpyActive`) — and hovering a card pins it active until the
 * pointer leaves. This is presentation over settled data — the buy is already booked by the time
 * this panel renders — not a progress indicator, and it claims nothing. Under `prefers-reduced-motion`
 * every card is active, so all the data is visible at once with no movement.
 */

import { useCallback, useEffect, useState } from 'react';

import { useAutoScrollLoop } from '../../../hooks/useAutoScrollLoop';
import { useScrollSpyActive } from '../../../hooks/useScrollSpyActive';
import type { MediaBuyStripData, PackageCard } from '../../../lib/journey/types';

/**
 * Above this many cards the list is capped (see `.mb-list` in journey-mediabuy.css) and auto-scrolls.
 * At or below it, every card fits in the box and nothing moves. Three visible is the design cap.
 */
const AUTO_SCROLL_MIN_CARDS = 4;

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

/** `$X` for USD, `X CUR` otherwise, bare number when no currency was echoed. */
function money(amount: number, currency: string | null): string {
  const n = amount.toLocaleString('en-US');
  if (currency === null) return n;
  if (currency === 'USD') return `$${n}`;
  return `${n} ${currency}`;
}

function AudioIcon() {
  return (
    <span className="mb-fmt-ic audio" aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M9 18V5l12-2v13" />
        <circle cx="6" cy="18" r="3" />
        <circle cx="18" cy="16" r="3" />
      </svg>
    </span>
  );
}

function DisplayIcon() {
  return (
    <span className="mb-fmt-ic display" aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="2" y="3" width="20" height="14" rx="2" />
        <line x1="8" y1="21" x2="16" y2="21" />
        <line x1="12" y1="17" x2="12" y2="21" />
      </svg>
    </span>
  );
}

/** Audio note for an audio/podcast format, a monitor otherwise. Format strings come from the seller. */
function FormatIcon({ formats }: { formats: readonly string[] }) {
  const audio = formats.some((f) => f.includes('audio') || f.includes('podcast') || f.includes('hostread'));
  return audio ? <AudioIcon /> : <DisplayIcon />;
}

/** A label/value pair for the detail drawer. */
type DetailRow = readonly [key: string, value: string];

function drawerSections(card: PackageCard): { title: string; rows: DetailRow[] }[] {
  const other: DetailRow[] = [
    ...card.catalogs.map((v): DetailRow => ['catalog', v]),
    ...(card.context !== null ? [['context', card.context] as DetailRow] : []),
    ...(card.confirmedAt !== null ? [['confirmed', card.confirmedAt] as DetailRow] : []),
    ...(card.creativeDeadline !== null ? [['creatives due', card.creativeDeadline] as DetailRow] : []),
    ...(card.revision !== null ? [['revision', card.revision] as DetailRow] : []),
    ...(card.agencyEstimate !== null ? [['estimate', card.agencyEstimate] as DetailRow] : []),
  ];
  return [
    {
      title: 'Optimization & targeting',
      rows: [
        ...card.optimizationGoals.map((v): DetailRow => ['goal', v]),
        ...card.targeting.map((v): DetailRow => ['targeting', v]),
      ],
    },
    { title: 'Creatives', rows: card.creativeAssignments.map((v): DetailRow => ['assigned', v]) },
    {
      title: 'Measurement',
      rows: [
        ...card.measurement.map((v): DetailRow => ['billing', v]),
        ...card.performanceStandards.map((v): DetailRow => ['standard', v]),
      ],
    },
    { title: 'Other', rows: other },
  ].filter((section) => section.rows.length > 0);
}

function Drawer({ card, open }: { card: PackageCard; open: boolean }) {
  const sections = drawerSections(card);
  if (sections.length === 0) return null;
  return (
    <div className={`mb-detail${open ? ' open' : ''}`}>
      <div className="mb-detail-inner">
        {sections.map((section) => (
          <div className="mb-section" key={section.title}>
            <div className="mb-section-title">{section.title}</div>
            {section.rows.map(([key, value], index) => (
              <div className="mb-drow" key={`${key}-${index}`}>
                <span className="mb-dk">{key}</span>
                <span className="mb-dv" title={value}>
                  {value}
                </span>
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

function flightText(card: PackageCard): string | null {
  if (card.startTime !== null && card.endTime !== null) return `${card.startTime} \u2192 ${card.endTime}`;
  if (card.endTime !== null) return `\u2192 ${card.endTime}`;
  return card.startTime;
}

function PackageCardView({
  card,
  maxBudget,
  active,
  innerRef,
  onActivate,
  onLeave,
}: {
  card: PackageCard;
  maxBudget: number;
  active: boolean;
  innerRef: (el: HTMLElement | null) => void;
  onActivate: () => void;
  onLeave: () => void;
}) {
  const classes = ['mb-pkg'];
  if (active) classes.push('active');
  if (card.paused) classes.push('paused');
  if (card.outcome === 'refused' || card.outcome === 'call_failed') classes.push('failed');

  const pace = card.pacing ?? 'none';

  return (
    <div ref={innerRef} className={classes.join(' ')} onMouseEnter={onActivate} onMouseLeave={onLeave}>
      <div className="mb-pkg-top">
        <span className="mb-pkg-name">
          <FormatIcon formats={card.formats} /> {card.title}
        </span>
        <span className="mb-pills">
          {card.paused ? <span className="mb-pill paused">paused</span> : null}
          {card.pricingOptionId !== null ? (
            <span className="mb-pill pricing">{card.pricingOptionId}</span>
          ) : null}
          {card.pacing !== null ? (
            <span className={`mb-pill pace pace-${pace}`}>{card.pacing.replace(/_/g, ' ')}</span>
          ) : null}
          {card.formats.map((f) => (
            <span className="mb-pill format" key={f}>
              {f.replace(/_/g, ' ')}
            </span>
          ))}
        </span>
      </div>

      {renderBody(card, maxBudget, pace)}

      <div className="mb-pkg-foot">
        <span>{flightText(card) ?? ''}</span>
        <span className="mb-id">{card.mediaBuyId ?? card.productId ?? card.packageId ?? ''}</span>
      </div>

      <Drawer card={card} open={active} />
    </div>
  );
}

function renderBody(card: PackageCard, maxBudget: number, pace: string) {
  if (card.outcome === 'refused') {
    return (
      <div className="mb-state failed">
        <div className="mb-state-line">
          refused by seller{card.refusalCode !== null ? ` \u00b7 ${card.refusalCode}` : ''}
        </div>
        {card.refusalDetail !== null ? <div className="mb-state-sub">{card.refusalDetail}</div> : null}
        {card.refusalField !== null ? (
          <div className="mb-state-sub mono">{card.refusalField}</div>
        ) : null}
      </div>
    );
  }
  if (card.outcome === 'in_flight') {
    return (
      <div className="mb-state live">
        <span className="live-dot" /> booking…
      </div>
    );
  }
  if (card.outcome === 'call_failed') {
    // A bare "call failed" when the seller/tool gave no reason; otherwise the reason it did give
    // (e.g. `no_prior_check`), so a buy that only ever failed says why rather than looking empty.
    return (
      <div className="mb-state failed">
        <div className="mb-state-line">
          {card.refusalCode !== null ? `call failed \u00b7 ${card.refusalCode}` : 'call failed'}
        </div>
        {card.refusalDetail !== null ? <div className="mb-state-sub">{card.refusalDetail}</div> : null}
      </div>
    );
  }

  // booked / no_id: a budget bar when a budget was echoed, else the status as it stands.
  if (card.budget === null) {
    return (
      <div className="mb-state">
        {card.status ?? (card.outcome === 'no_id' ? 'no media_buy_id returned' : 'booked')}
      </div>
    );
  }

  const pct = maxBudget > 0 ? Math.max(22, (card.budget / maxBudget) * 100) : 100;
  const parts = [money(card.budget, card.currency)];
  if (card.bidPrice !== null) parts.push(`bid ${money(card.bidPrice, card.currency)}`);

  return (
    <div className="mb-bar-wrap">
      <div className={`mb-bar pace-${pace}`} style={{ width: `${pct}%` }}>
        <span className="mb-bar-label">{parts.join(' \u00b7 ')}</span>
      </div>
      {card.impressions !== null ? (
        <span className="mb-bar-right">{card.impressions.toLocaleString('en-US')} imp</span>
      ) : null}
    </div>
  );
}

function MediaBuyHeader({ data }: { data: MediaBuyStripData }) {
  const hasWindow = data.flightStart !== null && data.flightEnd !== null;
  return (
    <div className="mb-head">
      <div className="mb-chips">
        {data.totalBudget !== null ? (
          <span className="mb-chip budget">
            <span className="mb-lbl">total</span> {data.totalBudget}
          </span>
        ) : null}
        <span className="mb-chip">
          <span className="mb-lbl">packages</span> {data.packageCount}
        </span>
      </div>
      {hasWindow ? (
        <div className="mb-flight">
          <div className="mb-flight-label">flight window</div>
          <div className="mb-flight-track" aria-hidden="true">
            <div className="mb-flight-fill" />
          </div>
          <div className="mb-flight-dates">
            <span>{data.flightStart}</span>
            <span>{data.flightEnd}</span>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function MediaBuyLegend({ cards }: { cards: readonly PackageCard[] }) {
  const paces = [...new Set(cards.map((c) => c.pacing).filter((p): p is string => p !== null))];
  const hasPaused = cards.some((c) => c.paused);
  if (paces.length === 0 && !hasPaused) return null;
  return (
    <div className="mb-legend">
      {paces.map((p) => (
        <span className="mb-legend-item" key={p}>
          <span className={`mb-legend-dot pace-${p}`} /> {p.replace(/_/g, ' ')}
        </span>
      ))}
      {hasPaused ? (
        <span className="mb-legend-item">
          <span className="mb-legend-dot paused" /> paused
        </span>
      ) : null}
    </div>
  );
}

export function MediaBuyStripBody({
  data,
  revealed,
}: {
  data: MediaBuyStripData;
  revealed: boolean;
}) {
  const reduceMotion = usePrefersReducedMotion();
  const spy = useScrollSpyActive(data.cards.length, !reduceMotion);
  // At 4+ cards the capped list auto-scrolls so the overflow is seen without manual scrolling; the
  // scroll-spy highlight follows the motion. Off under reduced motion (every card is shown active).
  const autoScroll = useAutoScrollLoop(!reduceMotion && data.cards.length >= AUTO_SCROLL_MIN_CARDS);

  // Both the scroll-spy (as its IntersectionObserver root) and the auto-scroll loop need the same
  // scroll container element, so feed the one node to both refs.
  const listRef = useCallback(
    (el: HTMLElement | null) => {
      spy.containerRef(el);
      autoScroll.containerRef(el);
    },
    [spy, autoScroll],
  );

  return (
    <div className={`mbstrip${revealed ? ' on' : ''}`}>
      <MediaBuyHeader data={data} />
      <div
        className="mb-list"
        ref={listRef}
        onMouseEnter={autoScroll.onEnter}
        onMouseLeave={autoScroll.onLeave}
      >
        {data.cards.map((card, index) => (
          <PackageCardView
            key={card.key}
            card={card}
            maxBudget={data.maxBudget}
            // Every card is active under reduced motion, so all data is visible without cycling;
            // otherwise the card most in view is active.
            active={reduceMotion || index === spy.active}
            innerRef={spy.itemRef(index)}
            onActivate={() => spy.onItemEnter(index)}
            onLeave={spy.onItemLeave}
          />
        ))}
      </div>
      <MediaBuyLegend cards={data.cards} />
    </div>
  );
}
