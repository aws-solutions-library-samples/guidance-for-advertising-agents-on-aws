/**
 * The Media Buy strip decoder, and the Book panel's selection.
 *
 * This suite carries forward the guarantees the previous `decodeBookCard` / `decodeBookRoster` pair
 * were pinned by two live incidents, now expressed against the richer strip:
 *
 *   - A refused buy must never render as a booked one. Poseidon once refused a `create_media_buy` over
 *     a missing field — `{errors: [...], status: "completed"}`, no `media_buy_id` — and the panel read
 *     the envelope's task status and printed "completed" under "MEDIA BUY BOOKED". A refusal is checked
 *     BEFORE any success field, so it can never be described by the fields a booked buy would carry.
 *   - The panel must not blank between products while a batch is being booked. Booking five products
 *     made the panel flicker empty-then-filled once per product, because the selected exchange was an
 *     unresolved call. `selector: 'latest_settled'` plus a strip whose card COUNT does not depend on
 *     which call is in flight fix it: an in-flight call is its own card state, not a missing one.
 *
 * The strip also decodes the full `Package` a seller echoes, so most of this file is the extraction:
 * each detail line reads one real AdCP sub-field, and a field a seller omitted is omitted here.
 */
import { describe, expect, it } from 'vitest';

import { derivePhases, exchangeForPhase } from './derive';
import { decodeMediaBuyStrip } from './governanceCards';
import { phaseByKey } from './phases';
import type { SessionStep } from '../types';
import type { PackageCard, ToolExchange } from './types';

const BOOK = phaseByKey('book');
if (BOOK === undefined) throw new Error('the book phase must exist for this suite to mean anything');

function buy(index: number, productId: string, output?: unknown): ToolExchange {
  return {
    callId: `call-${index}`,
    toolName: 'adcp_create_media_buy',
    input: { product_id: productId },
    callIndex: index,
    ...(output === undefined ? {} : { result: { output, status: 'success' as const, index } }),
  };
}

const booked = (id: string) => ({ media_buy_id: id, media_buy_status: 'active' });

/** Recorded steps for `n` buys where the first `settled` of them have results. */
function steps(n: number, settled: number): SessionStep[] {
  const out: SessionStep[] = [];
  for (let i = 0; i < n; i += 1) {
    out.push({
      step_index: out.length,
      step_type: 'tool_call',
      content: { toolName: 'adcp_create_media_buy', callId: `c${i}`, input: { product_id: `p${i}` } },
    } as unknown as SessionStep);
    if (i < settled) {
      out.push({
        step_index: out.length,
        step_type: 'tool_result',
        content: {
          toolName: 'adcp_create_media_buy',
          callId: `c${i}`,
          status: 'success',
          output: booked(`mb-${i}`),
        },
      } as unknown as SessionStep);
    }
  }
  return out;
}

const card = (id: string): PackageCard => {
  const cards = decodeMediaBuyStrip([buy(0, 'p', bookedWithPackage(id))])!.cards;
  return cards[0]!;
};

/** A response echoing one full package, so the extraction can be asserted field by field. */
function bookedWithPackage(id: string, pkg: Record<string, unknown> = RICH_PACKAGE): unknown {
  return {
    media_buy_id: id,
    media_buy_status: 'active',
    currency: 'USD',
    confirmed_at: '2026-10-01T09:14:00Z',
    creative_deadline: '2026-09-28T00:00:00Z',
    revision: 3,
    packages: [pkg],
  };
}

const RICH_PACKAGE: Record<string, unknown> = {
  package_id: 'pkg_1',
  product_id: 'prod_d979b543',
  budget: 2800,
  currency: 'USD',
  pacing: 'even',
  pricing_option_id: 'cpm_usd_fixed',
  bid_price: 12.0,
  impressions: 233333,
  paused: false,
  start_time: '2026-10-01T00:00:00Z',
  end_time: '2026-10-31T23:59:59Z',
  format_ids: [{ id: 'audio_midroll_30s' }],
  optimization_goals: [
    {
      kind: 'event',
      event_sources: [{ event_type: 'listen_complete' }],
      target: { kind: 'cost_per', value: 0.08 },
    },
  ],
  targeting_overlay: { geo_countries: ['US'], frequency_cap: { max_impressions: 3, window: 'day' } },
  creative_assignments: [{ creative_id: 'cr_audio_001', weight: 1.0 }],
  measurement_terms: {
    billing_measurement: { vendor: { domain: 'measure.example' } },
    makegood_policy: { available_remedies: ['credit'] },
  },
  performance_standards: [{ metric: 'listen_through', threshold: 0.7 }],
  catalogs: [{ catalog_id: 'cat_holiday_2026', name: 'Holiday 2026', type: 'product' }],
  context: { buyer_line_item: 'LI-4401' },
  agency_estimate_number: 'PO-2026-NW-1041',
};

describe('a refused create_media_buy', () => {
  const REFUSED = {
    errors: [{ code: 'VALIDATION_ERROR', message: 'missing required field: packages.0.priority.type' }],
    status: 'completed',
  };

  it('is a refused card, never a booked one reporting the envelope status', () => {
    const strip = decodeMediaBuyStrip([buy(0, 'p', REFUSED)])!;
    const first = strip.cards[0]!;

    expect(first.outcome).toBe('refused');
    expect(first.status).toBe('refused by seller');
    expect(first.status).not.toBe('completed');
  });

  it('names the seller\u2019s own code and message', () => {
    const first = decodeMediaBuyStrip([buy(0, 'p', REFUSED)])!.cards[0]!;

    expect(first.refusalCode).toBe('VALIDATION_ERROR');
    expect(String(first.refusalDetail)).toContain('priority.type');
  });

  it('emits no media_buy_id and no budget, because there is no media buy', () => {
    const first = decodeMediaBuyStrip([buy(0, 'p', REFUSED)])!.cards[0]!;

    expect(first.mediaBuyId).toBeNull();
    expect(first.budget).toBeNull();
  });

  it('reports the refusal even when a media_buy_id is present alongside the errors', () => {
    // Checked BEFORE the id, so a seller returning both cannot mask the failure with success fields.
    const first = decodeMediaBuyStrip([buy(0, 'p', { ...REFUSED, media_buy_id: 'mb_123' })])!.cards[0]!;

    expect(first.outcome).toBe('refused');
    expect(first.mediaBuyId).toBeNull();
  });

  it('does not treat a warning-severity error as a refusal', () => {
    const first = decodeMediaBuyStrip([
      buy(0, 'p', { media_buy_id: 'mb-0', errors: [{ code: 'SLOW', severity: 'warning' }] }),
    ])!.cards[0]!;

    expect(first.outcome).toBe('booked');
    expect(first.mediaBuyId).toBe('mb-0');
  });
});

describe('a successful create_media_buy', () => {
  it('reports the id and prefers media_buy_status over the envelope status', () => {
    // AdCP 3.1 renamed the field; the specific status wins over the envelope's `completed`.
    const first = decodeMediaBuyStrip([
      buy(0, 'p', { media_buy_id: 'mb_abc', media_buy_status: 'pending', status: 'completed' }),
    ])!.cards[0]!;

    expect(first.mediaBuyId).toBe('mb_abc');
    expect(first.status).toBe('pending');
  });

  it('says a success carried no media_buy_id rather than implying one', () => {
    const first = decodeMediaBuyStrip([buy(0, 'p', { status: 'completed' })])!.cards[0]!;

    expect(first.outcome).toBe('no_id');
    expect(first.mediaBuyId).toBeNull();
  });
});

describe('the strip does not blank across a multi-product batch', () => {
  it('picks the last SETTLED buy for selection, not the unresolved one after it', () => {
    const exchanges = [buy(0, 'p0', booked('mb-0')), buy(1, 'p1', booked('mb-1')), buy(2, 'p2')];

    const picked = exchangeForPhase(BOOK, exchanges);

    expect(picked?.callId).toBe('call-1');
    expect(picked?.result?.status).toBe('success');
  });

  it('falls back to the unresolved call when nothing has settled yet', () => {
    const picked = exchangeForPhase(BOOK, [buy(0, 'p0')]);

    expect(picked?.callId).toBe('call-0');
    expect(picked?.result).toBeUndefined();
  });

  it('is ready at every point in a five-product loop, never absent mid-flight', () => {
    const context = { capabilities: { callableTools: ['adcp_create_media_buy'] } } as never;
    const readiness: string[] = [];
    for (let settled = 1; settled <= 5; settled += 1) {
      const outstanding = Math.min(settled + 1, 5);
      const mid = derivePhases(steps(outstanding, settled), context);
      readiness.push(String(mid.get('book')?.state));
    }

    expect(readiness).toEqual(['ready', 'ready', 'ready', 'ready', 'ready']);
  });

  it('gives every buy a card, with the in-flight one carrying its own state', () => {
    const strip = decodeMediaBuyStrip([
      buy(0, 'gg-a', booked('mb-0')),
      buy(1, 'gg-b', booked('mb-1')),
      buy(2, 'gg-c'),
    ])!;

    expect(strip.cards).toHaveLength(3);
    expect(strip.cards.map((c) => c.outcome)).toEqual(['booked', 'booked', 'in_flight']);
    // The in-flight card names the product it is booking, from the recorded request.
    expect(strip.cards[2]?.title).toBe('gg-c');
  });

  it('returns null for no exchanges, so the caller classifies the absence', () => {
    expect(decodeMediaBuyStrip([])).toBeNull();
  });

  it('marks a failed CALL distinctly from a seller refusal', () => {
    const failed: ToolExchange = {
      callId: 'c9',
      toolName: 'adcp_create_media_buy',
      input: { product_id: 'p9' },
      callIndex: 9,
      result: { output: null, status: 'error', index: 9 },
    };

    expect(decodeMediaBuyStrip([failed])!.cards[0]?.outcome).toBe('call_failed');
  });
});

describe('the package extraction reads real AdCP fields', () => {
  it('carries the identity, budget bar and flight fields the seller echoed', () => {
    const c = card('mb-7fb5');

    expect(c.outcome).toBe('booked');
    expect(c.title).toBe('prod_d979b543');
    expect(c.packageId).toBe('pkg_1');
    expect(c.mediaBuyId).toBe('mb-7fb5');
    expect(c.status).toBe('active');
    expect(c.budget).toBe(2800);
    expect(c.currency).toBe('USD');
    expect(c.bidPrice).toBe(12);
    expect(c.pricingOptionId).toBe('cpm_usd_fixed');
    expect(c.pacing).toBe('even');
    expect(c.impressions).toBe(233333);
    expect(c.paused).toBe(false);
    expect(c.formats).toEqual(['audio_midroll_30s']);
    // ISO reduced to the date portion.
    expect(c.startTime).toBe('2026-10-01');
    expect(c.endTime).toBe('2026-10-31');
    expect(c.confirmedAt).toBe('2026-10-01');
    // The package carried no own deadline, so it inherits the media buy's.
    expect(c.creativeDeadline).toBe('2026-09-28');
    expect(c.revision).toBe('3');
    expect(c.agencyEstimate).toBe('PO-2026-NW-1041');
  });

  it('formats each detail-drawer line from its own AdCP sub-field', () => {
    const c = card('mb-7fb5');

    expect(c.optimizationGoals).toEqual(['listen_complete \u2192 cost_per 0.08']);
    expect(c.targeting).toEqual(['geo US', 'freq 3 / day']);
    expect(c.creativeAssignments).toEqual(['cr_audio_001 (w:1)']);
    expect(c.measurement).toEqual(['billing measure.example', 'makegood policy']);
    expect(c.performanceStandards).toEqual(['listen_through >= 0.7']);
    expect(c.catalogs).toEqual(['Holiday 2026 (product)']);
    expect(c.context).toBe('{"buyer_line_item":"LI-4401"}');
  });

  it('omits a field the seller left out rather than showing a dash', () => {
    const strip = decodeMediaBuyStrip([
      buy(0, 'p', {
        media_buy_id: 'mb-min',
        media_buy_status: 'active',
        packages: [{ package_id: 'pkg', product_id: 'x', budget: 100, currency: 'USD' }],
      }),
    ])!;
    const c = strip.cards[0]!;

    expect(c.optimizationGoals).toEqual([]);
    expect(c.targeting).toEqual([]);
    expect(c.creativeAssignments).toEqual([]);
    expect(c.measurement).toEqual([]);
    expect(c.performanceStandards).toEqual([]);
    expect(c.catalogs).toEqual([]);
    expect(c.context).toBeNull();
    expect(c.bidPrice).toBeNull();
    expect(c.impressions).toBeNull();
  });
});

describe('the aggregate header', () => {
  it('sums a single-currency budget and reports the counts and window', () => {
    const strip = decodeMediaBuyStrip([
      buy(0, 'a', bookedWithPackage('mb-0')),
      buy(1, 'b', bookedWithPackage('mb-1', { ...RICH_PACKAGE, budget: 1700, product_id: 'prod-b' })),
    ])!;

    expect(strip.totalBudget).toBe('$4,500');
    expect(strip.currency).toBe('USD');
    expect(strip.packageCount).toBe(2);
    expect(strip.maxBudget).toBe(2800);
    expect(strip.flightStart).toBe('2026-10-01');
    expect(strip.flightEnd).toBe('2026-10-31');
    // Both buys are `active`, so the summary reads as one status rather than counting the buys.
    expect(strip.statusSummary).toBe('active');
  });

  it('does not sum budgets across two currencies', () => {
    const strip = decodeMediaBuyStrip([
      buy(0, 'a', bookedWithPackage('mb-0')),
      buy(1, 'b', bookedWithPackage('mb-1', { ...RICH_PACKAGE, currency: 'EUR', product_id: 'prod-b' })),
    ])!;

    expect(strip.totalBudget).toBeNull();
    expect(strip.currency).toBeNull();
  });

  it('reports a single buy\u2019s own status as the summary', () => {
    expect(decodeMediaBuyStrip([buy(0, 'a', bookedWithPackage('mb-0'))])!.statusSummary).toBe('active');
  });

  it('does not hide a refusal behind a shared status when the outcomes are mixed', () => {
    const strip = decodeMediaBuyStrip([
      buy(0, 'a', bookedWithPackage('mb-0')),
      buy(1, 'b', { errors: [{ code: 'VALIDATION_ERROR', message: 'nope' }], status: 'completed' }),
    ])!;

    // Not "active": one buy was refused, so the summary is a booked count, and the refused card
    // still shows its own state in the panel.
    expect(strip.statusSummary).toBe('1 booked');
  });
});

describe('automatic cleanup of retry noise', () => {
  it('classifies a top-level {error} envelope as a failure, not a no_id success', () => {
    // The real junk: a create_media_buy that returned {error, message} (a booking attempted before
    // governance approval). It has no errors[] array, so it used to fall through to a no_id card and
    // read as a success that merely lacked an id.
    const first = decodeMediaBuyStrip([
      buy(0, 'gg-x', { error: 'no_prior_check', message: ['No approved check_governance result'] }),
    ])!.cards[0]!;

    expect(first.outcome).toBe('call_failed');
    expect(first.refusalCode).toBe('no_prior_check');
    expect(first.refusalDetail).toContain('No approved check_governance');
    expect(first.mediaBuyId).toBeNull();
  });

  it('supersedes a failed attempt once the SAME product is booked', () => {
    // The screenshot: gg-1f2a4e9bdc failed once, then booked. Only the booking should remain.
    const strip = decodeMediaBuyStrip([
      buy(0, 'gg-x', { error: 'no_prior_check', message: 'blocked' }),
      buy(1, 'gg-x', booked('mb-1')),
    ])!;

    expect(strip.cards).toHaveLength(1);
    expect(strip.cards[0]!.outcome).toBe('booked');
    expect(strip.cards[0]!.mediaBuyId).toBe('mb-1');
  });

  it('keeps a failed attempt when its product was never booked, so a real failure is not hidden', () => {
    const strip = decodeMediaBuyStrip([
      buy(0, 'gg-y', { error: 'no_prior_check', message: 'blocked' }),
    ])!;

    expect(strip.cards).toHaveLength(1);
    expect(strip.cards[0]!.outcome).toBe('call_failed');
  });

  it('dedupes the same booking recorded twice (a plan viewed across sessions)', () => {
    const strip = decodeMediaBuyStrip([
      buy(0, 'gg-z', booked('mb-z')),
      buy(1, 'gg-z', booked('mb-z')),
    ])!;

    expect(strip.cards).toHaveLength(1);
    expect(strip.cards[0]!.mediaBuyId).toBe('mb-z');
  });

  it('still shows distinct products, and an in-flight attempt for an unbooked product', () => {
    // Supersession is per-product: only a product that actually booked loses its other cards.
    const strip = decodeMediaBuyStrip([
      buy(0, 'gg-a', booked('mb-0')),
      buy(1, 'gg-b', booked('mb-1')),
      buy(2, 'gg-c'),
    ])!;
    expect(strip.cards.map((c) => c.outcome)).toEqual(['booked', 'booked', 'in_flight']);
  });
});

describe('the friendly product name', () => {
  it('titles a booked card with the get_products name when one is supplied', () => {
    const names = new Map([['prod_d979b543', 'Reference Podcast Mid-roll Audio']]);
    const strip = decodeMediaBuyStrip([buy(0, 'p', bookedWithPackage('mb-name'))], names)!;

    expect(strip.cards[0]!.title).toBe('Reference Podcast Mid-roll Audio');
    // The id is still carried, untouched, for the footer and dedup identity.
    expect(strip.cards[0]!.productId).toBe('prod_d979b543');
  });

  it('falls back to the product_id when no name map is given, never inventing one', () => {
    const strip = decodeMediaBuyStrip([buy(0, 'p', bookedWithPackage('mb-noname'))])!;
    expect(strip.cards[0]!.title).toBe('prod_d979b543');
  });

  it('falls back to the product_id when the map has no entry for that product', () => {
    const names = new Map([['some-other-product', 'Unrelated']]);
    const strip = decodeMediaBuyStrip([buy(0, 'p', bookedWithPackage('mb-x'))], names)!;
    expect(strip.cards[0]!.title).toBe('prod_d979b543');
  });
});
