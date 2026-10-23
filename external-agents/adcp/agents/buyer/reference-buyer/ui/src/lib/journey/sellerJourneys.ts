/**
 * Choosing whose journey is on screen, and what to say about the others.
 *
 * The prototype showed one seller. Every real buyer tool call fans out to every configured sales
 * agent, so a session holds several sellers' answers at once and one of them has to be chosen.
 *
 * Two rules:
 *
 *   An unlisted seller ranks LAST, never hidden. A priority list is a display preference, not an
 *   allowlist, and dropping a seller because nobody thought to list it would hide the very thing the
 *   fan-out exists to reveal.
 *
 *   An errored seller is still named, marked as errored. Omitting it would read as "that seller was
 *   not involved", when in fact it was asked and could not answer.
 */

import { parseFanOut, sellerEntryFailed, sellerEntryName, sellerEntrySummary } from '../sellers';
import type { SellerEntry } from '../types';
import type { SellerJourney } from './types';

/** Rank low to high. Unlisted sellers get a rank past the end of the list. */
function rankOf(sellerId: string, priority: readonly string[]): number {
  const index = priority.indexOf(sellerId);
  return index === -1 ? priority.length : index;
}

/**
 * One `SellerJourney` per entry, in configured priority order.
 *
 * The sort is stable, so sellers sharing a rank (which is every seller when no priority is
 * configured) keep the order the fan-out returned them in. That makes an absent priority list
 * degrade to "the order the sellers were asked" rather than to an arbitrary one.
 */
export function rank(
  entries: readonly SellerEntry[],
  priority: readonly string[],
): SellerJourney[] {
  return entries
    .map((entry, index) => {
      const sellerId = entry.seller_id ?? sellerEntryName(entry);
      const failed = sellerEntryFailed(entry);
      return {
        journey: {
          sellerId,
          sellerName: sellerEntryName(entry),
          rank: rankOf(sellerId, priority),
          outcome: failed ? ('errored' as const) : ('responded' as const),
          summary: sellerEntrySummary(entry),
        },
        index,
      };
    })
    .sort((a, b) => a.journey.rank - b.journey.rank || a.index - b.index)
    .map(({ journey }) => journey);
}

export interface SellerSelection {
  /** The journey on screen, or null when the session names no sellers at all. */
  readonly shown: SellerJourney | null;
  /** Every other seller present, in the same ranking order. */
  readonly others: readonly SellerJourney[];
}

/**
 * Which seller's journey to show.
 *
 * Prefers the highest-ranked seller that actually RESPONDED, because a journey built from an errored
 * seller has no phases to show. Falls back to the highest-ranked seller overall when every one
 * errored, so the panel can report the failure against a named seller rather than against nothing.
 */
export function select(
  entries: readonly SellerEntry[],
  priority: readonly string[],
): SellerSelection {
  const ranked = rank(entries, priority);
  if (ranked.length === 0) return { shown: null, others: [] };

  const responded = ranked.find((journey) => journey.outcome === 'responded');
  const shown = responded ?? ranked[0] ?? null;
  return {
    shown,
    others: ranked.filter((journey) => journey !== shown),
  };
}

/** Convenience: select straight from a recorded fan-out payload. */
export function selectFromOutput(
  output: unknown,
  priority: readonly string[],
): SellerSelection {
  const entries = parseFanOut(output);
  if (!entries) return { shown: null, others: [] };
  return select(entries, priority);
}

/**
 * The notice naming the other active seller journeys.
 *
 * Returns null when there are none, so the notice is absent rather than an empty box.
 */
export function othersNotice(others: readonly SellerJourney[]): string | null {
  if (others.length === 0) return null;
  const described = others.map((journey) =>
    journey.outcome === 'errored'
      ? `${journey.sellerName} (errored)`
      : journey.sellerName,
  );
  return `Also in this session: ${described.join(', ')}`;
}
