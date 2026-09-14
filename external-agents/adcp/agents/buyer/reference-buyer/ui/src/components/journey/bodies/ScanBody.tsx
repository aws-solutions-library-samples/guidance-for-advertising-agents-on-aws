/**
 * The creative scan.
 *
 * The rule this component holds: **no scan animation runs unless an evaluator actually returned
 * results.** A scanline sweeping across a card is a claim that something was scanned, and a reader
 * cannot tell a scan that happened from a decorative one.
 *
 * So `ScanBody` renders cards only from evaluator output. Formats a seller accepts are shown
 * without a verdict and without a scanline, because knowing which formats exist is not the same as
 * having checked a creative against them.
 */

import { useEffect, useState } from 'react';

export interface CreativeCard {
  /** The format id, exactly as the seller reported it. */
  readonly format: string;
  readonly name: string;
  /**
   * The seller that returned this format.
   *
   * Q9 of the U4 Functional Design plan: mandatory, matching Discover's own
   * `FormatGroupProduct.seller` precedent and its stated reason — a row without attribution invites
   * the reader to assume it came from whichever seller they last read about. Populated via
   * `sellerEntryName`, the same fan-out attribution Discover already uses.
   */
  readonly seller: string;
  /**
   * The evaluator's verdict, or null when no evaluator ran.
   *
   * Null means the card shows no verdict and no scan. It does not mean "clean".
   */
  readonly clean: boolean | null;
  /** Feature ids the evaluator returned. Empty when it did not run. */
  readonly features: readonly string[];
}

/** Duration of the authored scanline sweep, from the prototype's `@keyframes scan`. */
const SCAN_MS = 1100;
/** Stagger between cards, from the prototype's `await wait(220)`. */
const CARD_STAGGER_MS = 220;

export function ScanBody({
  cards,
  revealed,
}: {
  cards: readonly CreativeCard[];
  revealed: boolean;
}) {
  const [shown, setShown] = useState(0);
  const [scanning, setScanning] = useState<number | null>(null);
  const [done, setDone] = useState<ReadonlySet<number>>(new Set());

  useEffect(() => {
    if (!revealed) {
      setShown(0);
      setScanning(null);
      setDone(new Set());
      return;
    }
    let cancelled = false;
    let index = 0;

    const next = () => {
      if (cancelled || index >= cards.length) return;
      const current = index;
      index += 1;
      setShown(index);

      // Only a card with a real verdict is scanned. A card with none appears and stays put.
      if (cards[current]?.clean === null) {
        window.setTimeout(next, CARD_STAGGER_MS);
        return;
      }
      setScanning(current);
      window.setTimeout(() => {
        if (cancelled) return;
        setScanning(null);
        setDone((existing) => new Set([...existing, current]));
        window.setTimeout(next, CARD_STAGGER_MS);
      }, SCAN_MS);
    };

    window.setTimeout(next, CARD_STAGGER_MS);
    return () => {
      cancelled = true;
    };
  }, [revealed, cards]);

  return (
    <div className="creatives">
      {cards.map((card, index) => {
        const isShown = index < shown;
        const isScanning = scanning === index;
        const isDone = done.has(index);
        const verdictClass = card.clean === null ? '' : card.clean ? ' clean-card' : ' blocked-card';
        return (
          <div
            key={`${card.seller}-${card.format}`}
            className={`cre${isShown ? ' on' : ''}${isScanning ? ' scanning' : ''}${
              isDone ? ` done${verdictClass}` : ''
            }`}
          >
            <div className="scanline" />
            <div className="cf">{card.format}</div>
            <div className="cn">
              {card.name}{' '}
              <span className="cf" style={{ fontSize: 13 }}>
                {card.seller}
              </span>
            </div>
            {card.clean === null ? (
              // No evaluator ran. Says so, rather than showing a verdict nobody produced.
              <div className="res clean" style={{ opacity: 1, color: 'inherit' }}>
                not evaluated
              </div>
            ) : (
              <div className={`res ${card.clean ? 'clean' : 'blocked'}`}>
                {card.clean ? 'clean' : 'blocked'}
              </div>
            )}
            {card.features.length > 0 ? (
              <div className="feats">
                {card.features.map((feature) => (
                  <span className="feat" key={feature}>
                    {feature}
                  </span>
                ))}
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
