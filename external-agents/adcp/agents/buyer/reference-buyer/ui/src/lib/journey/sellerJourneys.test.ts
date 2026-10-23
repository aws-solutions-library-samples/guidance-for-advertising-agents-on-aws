import { describe, expect, it } from 'vitest';

import type { SellerEntry } from '../types';
import { othersNotice, rank, select, selectFromOutput } from './sellerJourneys';

const ok = (id: string, products = 1): SellerEntry => ({
  seller_id: id,
  seller_name: `${id} agent`,
  response: { products: Array.from({ length: products }, () => ({ product_id: 'p' })) },
});

const failed = (id: string): SellerEntry => ({
  seller_id: id,
  seller_name: `${id} agent`,
  error: 'TimeoutError: no response',
});

describe('rank', () => {
  it('orders by the configured priority list', () => {
    const ranked = rank([ok('a'), ok('b'), ok('c')], ['c', 'a', 'b']);
    expect(ranked.map((j) => j.sellerId)).toEqual(['c', 'a', 'b']);
  });

  it('puts an unlisted seller LAST rather than hiding it', () => {
    // A priority list is a display preference, not an allowlist. Dropping a seller nobody listed
    // would hide the thing the fan-out exists to reveal.
    const ranked = rank([ok('unlisted'), ok('a')], ['a']);
    expect(ranked.map((j) => j.sellerId)).toEqual(['a', 'unlisted']);
    expect(ranked).toHaveLength(2);
  });

  it('degrades to fan-out order when no priority is configured', () => {
    const ranked = rank([ok('x'), ok('y'), ok('z')], []);
    expect(ranked.map((j) => j.sellerId)).toEqual(['x', 'y', 'z']);
  });

  it('keeps stable order among sellers sharing a rank', () => {
    const ranked = rank([ok('p'), ok('q'), ok('a')], ['a']);
    expect(ranked.map((j) => j.sellerId)).toEqual(['a', 'p', 'q']);
  });

  it('marks an errored seller as errored but still includes it', () => {
    const ranked = rank([failed('down'), ok('up')], []);
    expect(ranked.map((j) => j.outcome)).toEqual(['errored', 'responded']);
  });

  it('treats an AdCP error envelope as a failure', () => {
    const ranked = rank([{ seller_id: 'e', response: { adcp_error: { code: 'x' } } }], []);
    expect(ranked[0]?.outcome).toBe('errored');
  });

  it('falls back to a name when the entry carries no id', () => {
    const ranked = rank([{ seller_name: 'Nameless Co', response: {} }], []);
    expect(ranked[0]?.sellerId).toBe('Nameless Co');
  });
});

describe('select', () => {
  it('shows the highest-ranked seller that responded', () => {
    const { shown, others } = select([failed('first'), ok('second')], ['first', 'second']);
    // `first` outranks `second` but has no journey to show, so `second` is displayed and `first` is
    // still named among the others.
    expect(shown?.sellerId).toBe('second');
    expect(others.map((j) => j.sellerId)).toEqual(['first']);
  });

  it('shows the top-ranked seller when every one errored, so the failure has a name', () => {
    const { shown, others } = select([failed('a'), failed('b')], ['a', 'b']);
    expect(shown?.sellerId).toBe('a');
    expect(shown?.outcome).toBe('errored');
    expect(others.map((j) => j.sellerId)).toEqual(['b']);
  });

  it('returns no selection when the session names no sellers', () => {
    expect(select([], [])).toEqual({ shown: null, others: [] });
  });

  it('reports every other seller present', () => {
    const { others } = select([ok('a'), ok('b'), ok('c')], ['a']);
    expect(others).toHaveLength(2);
  });

  it('has no others when only one seller is present', () => {
    const { shown, others } = select([ok('solo')], []);
    expect(shown?.sellerId).toBe('solo');
    expect(others).toEqual([]);
  });
});

describe('selectFromOutput', () => {
  it('selects straight from a recorded fan-out payload', () => {
    const { shown } = selectFromOutput(
      { task: 'adcp_get_products', results: [ok('a'), ok('b')] },
      ['b'],
    );
    expect(shown?.sellerId).toBe('b');
  });

  it('accepts the JSON-string form the buyer records', () => {
    const { shown } = selectFromOutput(JSON.stringify({ results: [ok('a')] }), []);
    expect(shown?.sellerId).toBe('a');
  });

  it('returns no selection for a payload that is not a fan-out', () => {
    expect(selectFromOutput({ products: [] }, [])).toEqual({ shown: null, others: [] });
  });
});

describe('othersNotice', () => {
  it('is null when there are no other journeys, so no empty box renders', () => {
    expect(othersNotice([])).toBeNull();
  });

  it('names the others and marks the errored ones', () => {
    const { others } = select([ok('shown'), ok('quiet'), failed('down')], ['shown']);
    const notice = othersNotice(others);
    expect(notice).toContain('quiet agent');
    expect(notice).toContain('down agent (errored)');
  });
});
