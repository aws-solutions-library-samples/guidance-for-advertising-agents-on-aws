/**
 * The Discover panel's grid placement.
 *
 * Two format groups to a row, and the constellation dropped into the half-empty cell a lone group
 * leaves behind. What makes that possible is that the graph is a SIBLING of the groups inside
 * `.fmtgrids` rather than a block underneath it, so that is what these tests assert — structure, not
 * computed geometry, since jsdom does not lay out CSS grid.
 *
 * This exists because the first attempt at a side-by-side layout shipped broken. It wrapped the
 * groups and the graph in a flex row, the graph claimed its max-content width, the list column
 * collapsed to zero and `.fmtscroll`'s `overflow-x: hidden` clipped every product row out of sight.
 * Every test passed, because no test knew where the graph was supposed to sit.
 */

import { cleanup, render } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import type { DiscoverData, FormatGroup, FormatGroupProduct } from '../../../lib/journey/types';
import { FormatGridBody } from './FormatGridBody';

afterEach(cleanup);

/**
 * A product carrying a ranking score, so the constellation has something to place.
 *
 * `relevance` is the field the graph positions by — not a nested `ranking` object, and not
 * `relevance_score`. Built to the real type with no cast: an `as FormatGroupProduct` here silently
 * produced products the graph refused to plot, and the tests then failed against a graph that was
 * never rendered rather than against its placement.
 */
function product(key: string, relevance: number): FormatGroupProduct {
  return {
    key,
    title: `Product ${key}`,
    seller: 'Poseidon Inventory Seller (sandbox, MCP)',
    relevance,
  };
}

function group(formatKey: string, products: readonly FormatGroupProduct[]): FormatGroup {
  return {
    formatKey,
    label: formatKey,
    cssVar: '--audio',
    products,
    count: products.length,
    attribution: ['Poseidon Inventory Seller (sandbox, MCP)'],
  };
}

function data(groupCount: number): DiscoverData {
  return {
    groups: Array.from({ length: groupCount }, (_, i) =>
      group(`fmt${i}`, [product(`${i}a`, 0.4), product(`${i}b`, 0.36)]),
    ),
    unmatched: 0,
    sellersQueried: 1,
    sellersResponded: 1,
    sellersErrored: 0,
  };
}

function renderBody(groupCount: number) {
  return render(<FormatGridBody data={data(groupCount)} note="note" revealed />);
}

describe('FormatGridBody: the graph shares the groups grid', () => {
  it('renders the constellation inside .fmtgrids, not as a sibling block', () => {
    // The load-bearing assertion. A graph outside the grid cannot occupy a group's empty cell, which
    // is the entire compact layout.
    const { container } = renderBody(1);
    const grid = container.querySelector('.fmtgrids');
    expect(grid).not.toBeNull();
    expect(grid?.querySelector('figure.gravity')).not.toBeNull();
  });

  it('puts the graph last, so it flows into the cell after the final group', () => {
    const { container } = renderBody(3);
    const grid = container.querySelector('.fmtgrids');
    const children = [...(grid?.children ?? [])];
    expect(children).toHaveLength(4);
    expect(children.at(-1)?.tagName.toLowerCase()).toBe('figure');
    expect(children.slice(0, 3).every((el) => el.classList.contains('fmtgroup'))).toBe(true);
  });
});

describe('FormatGridBody: the graph only takes a full row when no cell is free', () => {
  it('leaves an odd group count alone, so the graph sits beside the lone last group', () => {
    for (const groupCount of [1, 3, 5]) {
      const { container } = renderBody(groupCount);
      expect(container.querySelector('figure.gravity')?.className).toBe('gravity');
      cleanup();
    }
  });

  it('widens the graph for an even group count, where every row is already full', () => {
    for (const groupCount of [2, 4]) {
      const { container } = renderBody(groupCount);
      expect(container.querySelector('figure.gravity')?.classList).toContain('fmtgraph-wide');
      cleanup();
    }
  });

  it('leaves an empty grid rather than an empty cell when nothing was returned', () => {
    // No groups means no products, and a graph with nothing to place renders nothing at all. Worth
    // pinning: the graph is the grid's last child, so were it to render an empty figure here the
    // panel would show a blank half-width cell with no data behind it.
    const { container } = render(<FormatGridBody data={data(0)} note="note" revealed />);
    expect(container.querySelector('.fmtgrids')?.children).toHaveLength(0);
    expect(container.querySelector('figure.gravity')).toBeNull();
  });
});
