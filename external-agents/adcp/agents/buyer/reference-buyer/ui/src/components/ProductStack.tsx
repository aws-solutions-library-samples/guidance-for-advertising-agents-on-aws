/**
 * A seller's product list, as a cycling card deck.
 *
 * Every element is driven by a field the seller actually returned. A product with no pricing gets
 * no price chip, and one with no measurement gets no bars or reach, rather than a zero standing in
 * for "not supplied". The derivation lives in lib/products.ts and is unit-tested there; the deck's
 * own geometry lives in lib/deck.ts and its timer in hooks/useDeckCycle.ts, both unit-tested there
 * too. This file is presentation: it lays the cards out and reads the front one's detail.
 *
 * A long product list used to be a tall column, one row per product, which made ten results a page of
 * scrolling before the reply below them was even visible. It then became an angled fan of cards that
 * cycled every 1.5s, which fixed the height but left nine of ten cards showing only a corner -- the
 * only way to read a given product was to wait for the cycle to reach it.
 *
 * It is now a horizontal track that loops continuously left to right, so several whole cards are
 * legible at once and a long list is scanned rather than waited on. The loop's seam and its timing
 * live in lib/marquee.ts, unit-tested there; the keyframes live in index.css.
 *
 * Two deliberate consequences of that change:
 *
 *   - The scroll pauses, the SELECTION does not advance on its own. The detail panel below reads the
 *     selected product, and a panel that changed every 1.5s while being read was the fan's worst
 *     trait rather than a feature to carry over.
 *   - Hovering pauses the track, which is also what makes a card clickable without it drifting out
 *     from under the pointer.
 */

import { useMemo, useState } from 'react';

import { deckCounterLabel } from '../lib/deck';
import { useDeckCycle } from '../hooks/useDeckCycle';
import {
  MARQUEE_COPIES,
  marqueeDurationSeconds,
  marqueeShouldLoop,
  marqueeTrackItems,
} from '../lib/marquee';
import {
  formatCompactCount,
  productFormatInfo,
  productPriceLabel,
  productPriceShort,
  productReach,
  productSimilarityBars,
  productTitle,
  productsTrimmedNote,
} from '../lib/products';
import type { Product, SellerResponse } from '../lib/types';

function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

export interface ProductStackProps {
  response: SellerResponse;
}

export function ProductStack({ response }: ProductStackProps) {
  const products = response.products ?? [];
  const [showJson, setShowJson] = useState(false);
  const reducedMotion = useMemo(prefersReducedMotion, []);

  // Selection only: the timer is off, because the track's motion is CSS and the detail panel below
  // should not change under the reader. `useDeckCycle` still supplies the wrapping prev/next/setActive.
  const deck = useDeckCycle(products.length, 1500, false);
  const trimmed = productsTrimmedNote(products.length, response.products_recording_truncated);
  const front = products[deck.activeIndex];

  return (
    <div className="flex flex-col">
      <div className="flex items-center gap-2 border-b border-line-soft px-3 py-2">
        <span className="text-[13px] font-bold text-ink">
          {products.length} product{products.length === 1 ? '' : 's'}
        </span>
        {/* What the recorder kept versus what the seller sent. Without this a trimmed list reads as
            the seller's complete answer. */}
        {trimmed !== null && (
          <span className="text-[13px] font-semibold text-orange">{trimmed}</span>
        )}
        <span className="ml-auto flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => setShowJson((s) => !s)}
            className="rounded-full border border-line px-2.5 py-0.5 text-[13px] font-semibold text-ink-2 hover:bg-line-soft"
          >
            {showJson ? 'Hide JSON' : 'Show JSON'}
          </button>
        </span>
      </div>

      {products.length > 0 && (
        <>
          <ProductTrack products={products} deck={deck} reducedMotion={reducedMotion} />
          {front !== undefined && <ProductDetail product={front} />}
        </>
      )}

      {showJson && (
        <pre className="m-0 max-h-[220px] overflow-auto border-t border-line-soft px-3 py-2.5 font-mono text-[13px] break-words whitespace-pre-wrap text-ink-2">
          {JSON.stringify(response, null, 2)}
        </pre>
      )}
    </div>
  );
}

interface ProductTrackProps {
  products: Product[];
  deck: ReturnType<typeof useDeckCycle>;
  reducedMotion: boolean;
}

/** Card footprint and the gap between cards; the loop's distance is derived from these. */
const CARD_WIDTH = 236;
const CARD_HEIGHT = 84;
const CARD_GAP = 10;

function ProductTrack({ products, deck, reducedMotion }: ProductTrackProps) {
  // Two independent reasons to hold the track, kept as separate booleans for the same reason
  // useDeckCycle keeps its two apart: a manual pause must survive the pointer leaving, and clearing
  // one must never clear the other.
  const [hovering, setHovering] = useState(false);
  const [manuallyPaused, setManuallyPaused] = useState(false);

  const looping = marqueeShouldLoop(products.length) && !reducedMotion;
  const running = looping && !hovering && !manuallyPaused;

  // One copy when the track is not looping. The duplicate exists solely to hide the animation's
  // seam, and under reduced motion the row is manually scrollable instead -- so a second copy would
  // just mean scrolling past every product twice.
  const track = useMemo(
    () =>
      marqueeTrackItems(
        products,
        (product, index) => product.product_id ?? `product-${index}`,
        looping ? MARQUEE_COPIES : 1,
      ),
    [products, looping],
  );
  const durationSeconds = marqueeDurationSeconds(products.length, CARD_WIDTH, CARD_GAP);

  if (products.length === 1) {
    // Nothing to loop past; a single product is just its own card, no track and no controls.
    return (
      <div className="px-3 pt-3">
        {products[0] !== undefined && (
          <ProductTrackCard product={products[0]} index={0} isSelected onSelect={() => {}} />
        )}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2 pt-3">
      <div
        role="group"
        aria-roledescription="carousel"
        aria-label={`${products.length} products`}
        // `overflow-x-auto` only when the track is not animating, so a reader who has asked for
        // reduced motion can still reach every card by scrolling. While it animates the row is
        // clipped instead: a scrollbar on a moving track fights the animation for the same axis.
        className={`relative ${reducedMotion ? 'overflow-x-auto' : 'overflow-hidden'}`}
        style={{ height: CARD_HEIGHT }}
        onMouseEnter={() => setHovering(true)}
        onMouseLeave={() => setHovering(false)}
      >
        <div
          data-testid="product-track"
          className={`flex ${looping ? 'marquee-track' : ''}`}
          style={{
            gap: CARD_GAP,
            paddingLeft: 12,
            paddingRight: CARD_GAP,
            ...(looping
              ? {
                  animationDuration: `${durationSeconds}s`,
                  animationPlayState: running ? 'running' : 'paused',
                }
              : {}),
          }}
        >
          {track.map(({ item, sourceIndex, key, isDuplicate }) => (
            <ProductTrackCard
              key={key}
              product={item}
              index={sourceIndex}
              isSelected={sourceIndex === deck.activeIndex}
              isDuplicate={isDuplicate}
              onSelect={() => deck.setActive(sourceIndex)}
            />
          ))}
        </div>
      </div>

      <div className="flex items-center gap-2 px-3">
        <button
          type="button"
          onClick={deck.prev}
          aria-label="Previous product"
          className="flex h-6 w-6 items-center justify-center rounded-full border border-line text-ink-2 hover:bg-line-soft"
        >
          <ChevronIcon direction="left" />
        </button>
        <span className="font-mono text-[13px] text-muted">
          {deckCounterLabel(deck.activeIndex, products.length)}
        </span>
        <button
          type="button"
          onClick={deck.next}
          aria-label="Next product"
          className="flex h-6 w-6 items-center justify-center rounded-full border border-line text-ink-2 hover:bg-line-soft"
        >
          <ChevronIcon direction="right" />
        </button>
        {/* Offered only when there is motion to hold. Under reduced motion the track is already
            still, so a Pause button would claim to control something that is not happening. */}
        {looping && (
          <button
            type="button"
            onClick={() => setManuallyPaused((p) => !p)}
            aria-pressed={manuallyPaused}
            className="ml-1 rounded-full border border-line px-2 py-0.5 text-[13px] font-semibold text-ink-2 hover:bg-line-soft"
          >
            {manuallyPaused ? 'Play' : 'Pause'}
          </button>
        )}
      </div>
    </div>
  );
}

interface ProductTrackCardProps {
  product: Product;
  index: number;
  isSelected: boolean;
  /** A repeat of a card already in the track: visible, but not a second entry for assistive tech. */
  isDuplicate?: boolean;
  onSelect: () => void;
}

/**
 * One card in the track.
 *
 * Every card shows the same thing, because every card is fully visible now -- the fan's "peek badge"
 * existed only because a card behind the front one showed nothing but a corner. The selected card is
 * ringed, and its full detail is in the panel below.
 */
function ProductTrackCard({
  product,
  index,
  isSelected,
  isDuplicate = false,
  onSelect,
}: ProductTrackCardProps) {
  const format = productFormatInfo(product);
  const priceShort = productPriceShort(product);
  const reach = productReach(product);

  return (
    <button
      type="button"
      data-track-card={product.product_id ?? `product-${index}`}
      data-track-index={index}
      onClick={onSelect}
      // The duplicate copy is presentational. Announcing it would read the whole list twice; the
      // prev/next controls and the detail panel are the accessible path through the same data.
      aria-hidden={isDuplicate}
      tabIndex={isDuplicate ? -1 : 0}
      aria-label={productTitle(product)}
      aria-pressed={isDuplicate ? undefined : isSelected}
      className={`flex flex-none flex-col items-start justify-center gap-1.5 rounded-[12px] border px-3 py-2.5 text-left transition-shadow ${
        isSelected
          ? 'border-blue bg-surface shadow-[0_0_0_1.5px_var(--color-blue),0_14px_28px_-14px_rgb(59_130_246_/_0.5)]'
          : 'cursor-pointer border-line bg-surface shadow-[var(--shadow-raised)] hover:border-sky'
      }`}
      style={{ width: CARD_WIDTH, height: CARD_HEIGHT }}
    >
      <span className="flex w-full items-center gap-1.5">
        <span
          className="flex h-[20px] w-[20px] flex-none items-center justify-center rounded-md bg-line-soft"
          style={{ fontSize: '11px', color: format.icon.color }}
          aria-hidden
        >
          <svg
            width="1em"
            height="1em"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.7"
            strokeLinecap="round"
            strokeLinejoin="round"
            dangerouslySetInnerHTML={{ __html: format.icon.path }}
          />
        </span>
        <span className="truncate text-[13px] leading-tight font-bold text-ink">
          {productTitle(product)}
        </span>
      </span>

      {/* Only what the seller actually returned: no price chip for an unpriced product, no reach for
          one with no measurement, rather than a zero standing in for "not supplied". */}
      <span className="flex items-center gap-1.5">
        {priceShort !== null && (
          <span className="rounded-full bg-[#e3f8ee] px-1.5 py-0.5 font-mono text-[13px] font-bold text-[#0b6b4f]">
            {priceShort}
          </span>
        )}
        {reach !== null && (
          <span className="rounded-full bg-line-soft px-1.5 py-0.5 font-mono text-[13px] font-bold text-ink-2">
            {formatCompactCount(reach.audience)}
          </span>
        )}
      </span>
    </button>
  );
}

/**
 * The front card's full detail: description, chips and similarity bars, at the same level of
 * completeness the old vertical row showed. Below the deck rather than inside the fanned card,
 * because a card in the fan has to stay the same size as every card behind it.
 */
function ProductDetail({ product }: { product: Product }) {
  const price = productPriceLabel(product);
  const bars = productSimilarityBars(product);
  const reach = productReach(product);

  return (
    <div className="flex flex-col gap-[3px] border-t border-line-soft px-3 py-2.5">
      {product.description !== undefined && product.description !== '' && (
        <span className="line-clamp-2 text-[13px] leading-snug text-muted">
          {product.description}
        </span>
      )}

      <span className="mt-0.5 flex flex-wrap items-center gap-1.5">
        {price !== null && <Chip className="bg-[#e3f8ee] text-[#0b6b4f]">{price}</Chip>}
        {productFormatInfo(product).id !== null && (
          <Chip className="bg-[#eef2ff] text-[#4338ca]">{productFormatInfo(product).id}</Chip>
        )}
        {product.delivery_type !== undefined && <Chip>{product.delivery_type}</Chip>}
        {reach !== null && (
          <Chip>
            reach {reach.audience.toLocaleString()}
            <span className="ml-1 font-normal text-muted">
              ({formatCompactCount(reach.audience)})
            </span>
          </Chip>
        )}
      </span>

      {bars.length > 0 && (
        <div className="mt-1.5 flex flex-col gap-[3px]">
          {bars.map((bar) => (
            <div key={bar.axis} className="flex items-center gap-1.5">
              <span className="w-[58px] flex-none text-[13px] text-muted">{bar.axis}</span>
              <span className="h-1 flex-1 overflow-hidden rounded-full bg-line-soft">
                <span
                  className={`block h-full rounded-full ${
                    bar.axis === 'content' ? 'bg-blue' : 'bg-purple'
                  }`}
                  style={{ width: `${bar.fraction * 100}%` }}
                />
              </span>
              {/* The score as returned, so an out-of-range value stays visible even though the
                  bar width is clamped. */}
              <span className="w-[38px] flex-none text-right font-mono text-[13px] text-ink-2">
                {bar.value.toFixed(3)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Chip({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return (
    <span
      className={`rounded-full px-1.5 py-0.5 font-mono text-[13px] font-bold ${
        className || 'bg-line-soft text-ink-2'
      }`}
    >
      {children}
    </span>
  );
}

function ChevronIcon({ direction }: { direction: 'left' | 'right' }) {
  const d = direction === 'left' ? 'M15 6l-6 6 6 6' : 'M9 6l6 6-6 6';
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d={d} />
    </svg>
  );
}
