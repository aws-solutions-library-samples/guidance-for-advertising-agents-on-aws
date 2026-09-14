// `fireEvent` rather than a native `.click()`: it wraps the dispatch in `act`, so React has
// flushed the resulting state update by the time the assertion runs.
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ProductStack } from './ProductStack';
import type { Product, SellerResponse } from '../lib/types';

/**
 * Presentation checks. The field-by-field derivation is covered in lib/products.test.ts, the
 * looping track's geometry and timing in lib/marquee.test.ts, and the selection index in
 * hooks/useDeckCycle.test.ts; what matters here is that the track actually renders those pieces
 * together, that an absent field produces no element on screen, and that a real zero does.
 */

const gothamProduct: Product = {
  product_id: 'gg-2d92eb43c6',
  name: 'Gotham Sports Desk — Basketball',
  description: 'newsletter in the top-banner placement, US National edition, any',
  delivery_type: 'non_guaranteed',
  format_ids: [{ id: 'gotham_newsletter', agent_url: 'https://gotham-seller.example/adcp/mcp' }],
  pricing_options: [
    { pricing_option_id: 'cpm-gg', pricing_model: 'cpm', currency: 'USD', fixed_price: 16 },
  ],
  ext: {
    gotham_measurement: {
      content_similarity: 0.425883,
      audience_similarity: 0.456952,
      reachable_audience: 368336,
      reach_label: 'a label the card does not render',
    },
  },
};

function withProducts(products: Product[], extra: Partial<SellerResponse> = {}): SellerResponse {
  return { products, ...extra };
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('ProductStack', () => {
  it('states the real count', () => {
    render(<ProductStack response={withProducts([gothamProduct, gothamProduct])} />);
    expect(screen.getByText('2 products')).toBeTruthy();
  });

  it('uses the singular for one', () => {
    render(<ProductStack response={withProducts([gothamProduct])} />);
    expect(screen.getByText('1 product')).toBeTruthy();
  });

  it('shows the front card and its detail: name, description, price and format', () => {
    render(<ProductStack response={withProducts([gothamProduct])} />);
    expect(screen.getByText('Gotham Sports Desk — Basketball')).toBeTruthy();
    expect(screen.getByText(/newsletter in the top-banner placement/)).toBeTruthy();
    expect(screen.getByText('USD 16.00 CPM')).toBeTruthy();
    expect(screen.getByText('gotham_newsletter')).toBeTruthy();
  });

  it('shows the reach figure without a disclaimer after it', () => {
    render(<ProductStack response={withProducts([gothamProduct])} />);
    expect(screen.getByText(/reach 368,336/)).toBeTruthy();
  });

  it('shows both similarity scores as returned', () => {
    render(<ProductStack response={withProducts([gothamProduct])} />);
    expect(screen.getByText('0.426')).toBeTruthy();
    expect(screen.getByText('0.457')).toBeTruthy();
  });

  it('renders one card per product, capped at the deck maximum, for a long list', () => {
    const many = Array.from({ length: 15 }, (_, i) => ({
      ...gothamProduct,
      product_id: `gg-${i}`,
      name: `Product ${i}`,
    }));
    const { container } = render(<ProductStack response={withProducts(many)} />);
    // Every product gets a card now, and the track holds two copies of the list -- the duplication is
    // what makes the loop seamless, so the DOM count is deliberately 2x the product count.
    expect(screen.getByText('15 products')).toBeTruthy();
    expect(container.querySelectorAll('[data-track-card]').length).toBe(30);
    expect(screen.getByText('1 of 15')).toBeTruthy();
  });

  it('leaves the selection alone as time passes, so the detail panel does not change while it is read', () => {
    // The fan advanced its own selection every 1.5s. The track moves instead, and the panel below
    // holds still -- a detail panel that changed under the reader was the fan's worst trait, not a
    // feature to carry over.
    render(
      <ProductStack
        response={withProducts([
          { ...gothamProduct, product_id: 'a', name: 'Product A' },
          { ...gothamProduct, product_id: 'b', name: 'Product B' },
        ])}
      />,
    );
    expect(screen.getByText('1 of 2')).toBeTruthy();
    act(() => void vi.advanceTimersByTime(10000));
    expect(screen.getByText('1 of 2')).toBeTruthy();
  });

  it('loops the track, at a duration derived from how much there is to travel', () => {
    const { container } = render(
      <ProductStack
        response={withProducts([
          { ...gothamProduct, product_id: 'a', name: 'Product A' },
          { ...gothamProduct, product_id: 'b', name: 'Product B' },
        ])}
      />,
    );
    const track = container.querySelector('[data-testid="product-track"]') as HTMLElement;
    expect(track.className).toContain('marquee-track');
    // A real duration, not a placeholder: a track with no duration never moves.
    expect(track.style.animationDuration).toMatch(/^[\d.]+s$/);
    expect(parseFloat(track.style.animationDuration)).toBeGreaterThan(0);
    expect(track.style.animationPlayState).toBe('running');
  });

  it('holds the track while hovered and resumes on mouse leave', () => {
    // Also what makes a card clickable: without this the card drifts out from under the pointer.
    const { container } = render(
      <ProductStack
        response={withProducts([
          { ...gothamProduct, product_id: 'a', name: 'Product A' },
          { ...gothamProduct, product_id: 'b', name: 'Product B' },
        ])}
      />,
    );
    const stage = screen.getByRole('group', { name: '2 products' });
    const track = () => container.querySelector('[data-testid="product-track"]') as HTMLElement;
    expect(track().style.animationPlayState).toBe('running');
    fireEvent.mouseEnter(stage);
    expect(track().style.animationPlayState).toBe('paused');
    fireEvent.mouseLeave(stage);
    expect(track().style.animationPlayState).toBe('running');
  });

  it('selects a product when its card is clicked', () => {
    const { container } = render(
      <ProductStack
        response={withProducts([
          { ...gothamProduct, product_id: 'a', name: 'Product A' },
          { ...gothamProduct, product_id: 'b', name: 'Product B' },
          { ...gothamProduct, product_id: 'c', name: 'Product C' },
        ])}
      />,
    );
    // Queried by data attribute because the track holds two copies of every card, so the name alone
    // is ambiguous. Clicking either copy must select the same product -- they share a sourceIndex.
    const card = container.querySelector('[data-track-card="c"]');
    expect(card).not.toBeNull();
    fireEvent.click(card as HTMLElement);
    expect(screen.getByText('3 of 3')).toBeTruthy();
  });

  it('moves one card per click of the prev/next controls', () => {
    render(
      <ProductStack
        response={withProducts([
          { ...gothamProduct, product_id: 'a', name: 'Product A' },
          { ...gothamProduct, product_id: 'b', name: 'Product B' },
          { ...gothamProduct, product_id: 'c', name: 'Product C' },
        ])}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Next product' }));
    expect(screen.getByText('2 of 3')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Previous product' }));
    expect(screen.getByText('1 of 3')).toBeTruthy();
  });

  it('the Pause control holds the track and Play resumes it', () => {
    const { container } = render(
      <ProductStack
        response={withProducts([
          { ...gothamProduct, product_id: 'a', name: 'Product A' },
          { ...gothamProduct, product_id: 'b', name: 'Product B' },
        ])}
      />,
    );
    const track = () => container.querySelector('[data-testid="product-track"]') as HTMLElement;
    fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
    expect(track().style.animationPlayState).toBe('paused');
    fireEvent.click(screen.getByRole('button', { name: 'Play' }));
    expect(track().style.animationPlayState).toBe('running');
  });

  it('keeps a manual pause when the pointer leaves, rather than silently resuming', () => {
    // The lesson useDeckCycle's docstring records, at this layer: hover and the Pause button are two
    // independent reasons to hold, so a mouseleave must not undo a deliberate pause.
    const { container } = render(
      <ProductStack
        response={withProducts([
          { ...gothamProduct, product_id: 'a', name: 'Product A' },
          { ...gothamProduct, product_id: 'b', name: 'Product B' },
        ])}
      />,
    );
    const stage = screen.getByRole('group', { name: '2 products' });
    const track = () => container.querySelector('[data-testid="product-track"]') as HTMLElement;
    fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
    fireEvent.mouseEnter(stage);
    fireEvent.mouseLeave(stage);
    expect(track().style.animationPlayState).toBe('paused');
  });

  it('does not offer track controls for a list with nothing to loop past', () => {
    render(<ProductStack response={withProducts([gothamProduct])} />);
    expect(screen.queryByRole('button', { name: 'Next product' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Pause' })).toBeNull();
  });

  describe('with reduced motion requested', () => {
    function stubReducedMotion(matches: boolean) {
      vi.stubGlobal('matchMedia', (query: string) => ({
        matches,
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }));
    }

    afterEach(() => {
      vi.unstubAllGlobals();
    });

    const twoProducts = [
      { ...gothamProduct, product_id: 'a', name: 'Product A' },
      { ...gothamProduct, product_id: 'b', name: 'Product B' },
    ];

    it('does not animate the track at all', () => {
      // Not "animates very fast": the global reduced-motion rule in index.css would collapse the
      // duration, which parks the track at its end keyframe. The component drops the animation
      // outright so there is one answer to where the track sits.
      stubReducedMotion(true);
      const { container } = render(<ProductStack response={withProducts(twoProducts)} />);
      const track = container.querySelector('[data-testid="product-track"]') as HTMLElement;
      expect(track.className).not.toContain('marquee-track');
      expect(track.style.animationDuration).toBe('');
    });

    it('makes the row scrollable instead, so every card is still reachable', () => {
      stubReducedMotion(true);
      render(<ProductStack response={withProducts(twoProducts)} />);
      const stage = screen.getByRole('group', { name: '2 products' });
      expect(stage.className).toContain('overflow-x-auto');
    });

    it('offers no Pause control, since there is no motion to hold', () => {
      stubReducedMotion(true);
      render(<ProductStack response={withProducts(twoProducts)} />);
      expect(screen.queryByRole('button', { name: 'Pause' })).toBeNull();
      // Selection is still navigable, which is the accessible path through the list.
      expect(screen.getByRole('button', { name: 'Next product' })).toBeTruthy();
    });

    it('renders one copy of each card, with no duplicate to loop with', () => {
      stubReducedMotion(true);
      const { container } = render(<ProductStack response={withProducts(twoProducts)} />);
      expect(container.querySelectorAll('[data-track-card]').length).toBe(2);
    });
  });

  it('keeps the raw payload one click away, hidden by default', () => {
    render(<ProductStack response={withProducts([gothamProduct])} />);
    expect(screen.queryByText(/"product_id"/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Show JSON' }));
    expect(screen.getByText(/"product_id"/)).toBeTruthy();
  });

  it('states how many of how many the recorder kept', () => {
    render(
      <ProductStack
        response={withProducts([gothamProduct], { products_recording_truncated: { total: 50 } })}
      />,
    );
    expect(screen.getByText('1 of 50 recorded')).toBeTruthy();
  });

  describe('a field the seller omitted produces no element', () => {
    const bare: Product = { product_id: 'bare', name: 'Bare product' };

    it('omits the price chip, the format chip, the bars and the reach', () => {
      render(<ProductStack response={withProducts([bare])} />);
      expect(screen.getByText('Bare product')).toBeTruthy();
      expect(screen.queryByText(/USD/)).toBeNull();
      expect(screen.queryByText(/reach/)).toBeNull();
      expect(screen.queryByText('content')).toBeNull();
      expect(screen.queryByText('audience')).toBeNull();
    });

    it('leaks no zero into the detail panel', () => {
      const { container } = render(<ProductStack response={withProducts([bare])} />);
      const detail = container.querySelector('.border-t.border-line-soft.px-3.py-2\\.5');
      expect(detail?.textContent).not.toMatch(/\b0\b/);
    });
  });

  describe('a real zero is a measurement and is shown', () => {
    const zeroed: Product = {
      product_id: 'z',
      name: 'Zero reach',
      ext: {
        gotham_measurement: {
          content_similarity: 0,
          audience_similarity: 0.5,
          reachable_audience: 0,
        },
      },
    };

    it('renders 0.000 rather than omitting the axis', () => {
      render(<ProductStack response={withProducts([zeroed])} />);
      expect(screen.getByText('0.000')).toBeTruthy();
      expect(screen.getByText('content')).toBeTruthy();
    });

    it('renders a reach of zero, which differs from not knowing', () => {
      render(<ProductStack response={withProducts([zeroed])} />);
      expect(screen.getByText(/reach 0\b/)).toBeTruthy();
    });
  });

  it('renders an empty list as a result rather than as an error', () => {
    render(<ProductStack response={withProducts([])} />);
    expect(screen.getByText('0 products')).toBeTruthy();
    expect(screen.queryAllByRole('group')).toHaveLength(0);
  });

  it('uses stroke-only marks, never a fill', () => {
    const { container } = render(<ProductStack response={withProducts([gothamProduct])} />);
    const svg = container.querySelector('[data-track-card] svg');
    expect(svg?.getAttribute('stroke')).toBe('currentColor');
    expect(svg?.getAttribute('fill')).toBe('none');
  });

  it('announces each product once, hiding the duplicate copy from assistive tech', () => {
    const { container } = render(
      <ProductStack
        response={withProducts([
          { ...gothamProduct, product_id: 'a', name: 'Product A' },
          { ...gothamProduct, product_id: 'b', name: 'Product B' },
        ])}
      />,
    );
    // Every real card is announced now, because every card is fully legible -- the fan hid them only
    // because all but the front one showed a corner. What is hidden is the second COPY of the list,
    // which exists to make the loop seamless and would otherwise be read out twice.
    const cards = Array.from(container.querySelectorAll('[data-track-card="b"]'));
    expect(cards).toHaveLength(2);
    expect(cards[0]?.getAttribute('aria-hidden')).toBe('false');
    expect(cards[1]?.getAttribute('aria-hidden')).toBe('true');
    // The first copy is the accessible one, so the name still resolves to exactly one element.
    expect(screen.getByRole('button', { name: 'Product A' })).toBeTruthy();
  });
});
