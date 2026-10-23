/**
 * The Bind and Plan decoders, on shapes the one recorded session does not contain.
 *
 * `derive.recorded.test.ts` proves these work on recorded data, which is the case that matters most and
 * the one a hand-written fixture cannot establish. This file covers the rest: absent fields, failed
 * sellers, multiple sellers, and the zero-versus-absent distinction — cases a single happy-path
 * recording will never exercise, and where the absent-versus-zero rules actually bite.
 */

import { describe, expect, it } from 'vitest';

import { decodeBindCard, decodePlanCard } from './governanceCards';
import type { ToolExchange } from './types';

function exchange(output: unknown, input: unknown = {}): ToolExchange {
  return {
    callId: 'call-1',
    toolName: 'adcp_sync_governance',
    input,
    callIndex: 0,
    result: { output, status: 'success', index: 1 },
  };
}

const BOUND_ACCOUNT = {
  account: { brand: { domain: 'acme.example' }, operator: 'acme-media' },
  status: 'synced',
  governance_agents: [
    {
      url: 'https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/arn%3Aaws%3Abedrock-agentcore%3Aus-east-1%3A123%3Aruntime%2FRefGovernance_x-abc/invocations',
    },
  ],
};

function fanOut(...results: unknown[]): unknown {
  return { task: 'sync_governance', sellers_queried: results.length, results };
}

describe('decodeBindCard', () => {
  it('reads a single seller into one directed link', () => {
    const data = decodeBindCard(
      exchange(fanOut({ seller_id: 'reference', response: { accounts: [BOUND_ACCOUNT] } })),
    );
    expect(data?.links).toHaveLength(1);
    const link = data!.links[0]!;
    expect(link.account).toBe('acme.example / acme-media');
    expect(link.status).toBe('synced');
    // The runtime id, not the 200-character invoke URL. Shortening is display, not substitution.
    expect(link.agent).toBe('RefGovernance_x-abc');
    expect(link.sellerId).toBe('reference');
  });

  it('parses a result the recorder stored as a JSON string', () => {
    // How every real session arrives. A decoder that only handled objects would pass every fixture
    // here and find nothing in production.
    const raw = JSON.stringify(
      fanOut({ seller_id: 'reference', response: { accounts: [BOUND_ACCOUNT] } }),
    );
    expect(decodeBindCard(exchange(raw))?.links).toHaveLength(1);
  });

  it('returns one link per seller when several bound', () => {
    const data = decodeBindCard(
      exchange(
        fanOut(
          { seller_id: 'reference', response: { accounts: [BOUND_ACCOUNT] } },
          { seller_id: 'triton', response: { accounts: [BOUND_ACCOUNT] } },
        ),
      ),
    );
    expect(data?.links.map((link) => link.sellerId)).toEqual(['reference', 'triton']);
  });

  it('ignores a failed seller rather than reporting it as bound', () => {
    const data = decodeBindCard(
      exchange(
        fanOut(
          { seller_id: 'reference', response: { accounts: [BOUND_ACCOUNT] } },
          { seller_id: 'gotham', error: 'connection refused' },
        ),
      ),
    );
    // The failure is reported by the seller notice rather than shown as a binding here.
    expect(data?.links.map((link) => link.sellerId)).toEqual(['reference']);
    expect(JSON.stringify(data)).not.toContain('gotham');
  });

  it('returns null when nothing usable came back', () => {
    // Null, not an empty link list: the caller turns null into a classified absence with a reason,
    // whereas an empty panel would read as a real, empty answer.
    expect(decodeBindCard(exchange(fanOut({ seller_id: 'reference', response: {} })))).toBeNull();
    expect(decodeBindCard(exchange('not json at all'))).toBeNull();
    expect(decodeBindCard(exchange(undefined))).toBeNull();
  });

  it('leaves the agent null when the seller returned none, rather than substituting one', () => {
    const data = decodeBindCard(
      exchange(
        fanOut({
          seller_id: 'reference',
          response: { accounts: [{ ...BOUND_ACCOUNT, governance_agents: [] }] },
        }),
      ),
    );
    const link = data!.links[0]!;
    expect(link.account).toBe('acme.example / acme-media');
    expect(link.agent).toBeNull();
  });
});

const PLAN_RESPONSE = {
  status: 'completed',
  replayed: false,
  plans: [
    {
      plan_id: 'pantene-q1-audio',
      status: 'active',
      version: 1,
      categories: [{ category_id: 'budget_authority', status: 'active' }],
      resolved_policies: [],
    },
  ],
};

const PLAN_INPUT = {
  plan_id: 'pantene-q1-audio',
  budget_total: 50000,
  currency: 'USD',
  flight_start: '2026-09-01T00:00:00+00:00',
  flight_end: '2026-09-30T23:59:59+00:00',
  channels: ['streaming_audio', 'podcast'],
};

describe('decodePlanCard', () => {
  it('reads the plan identity the governance agent returned', () => {
    const data = decodePlanCard(exchange(PLAN_RESPONSE, PLAN_INPUT));
    expect(data?.planId).toBe('pantene-q1-audio');
    expect(data?.status).toBe('active');
    expect(data?.version).toBe('1');
  });

  it('splits the budget so the figure can be shown apart from its currency', () => {
    const data = decodePlanCard(exchange(PLAN_RESPONSE, PLAN_INPUT));
    expect(data?.budgetAmount).toBe('50,000');
    expect(data?.currency).toBe('USD');
  });

  it('reduces the flight to its two dates', () => {
    const data = decodePlanCard(exchange(PLAN_RESPONSE, PLAN_INPUT));
    expect(data?.flightStart).toBe('2026-09-01');
    expect(data?.flightEnd).toBe('2026-09-30');
  });

  it('keeps channels and evaluation categories separate', () => {
    // They are different kinds of fact and the panel tints them differently: channels are what the
    // buyer authorised, categories are what the agent will assess. Merging them would let a viewer
    // read a category as a purchased channel.
    const data = decodePlanCard(exchange(PLAN_RESPONSE, PLAN_INPUT));
    expect(data?.channels).toEqual(['streaming_audio', 'podcast']);
    expect(data?.willEvaluate).toEqual(['budget_authority']);
  });

  it('distinguishes "no policies resolved" from "not asked"', () => {
    // An empty array is an answer. A missing field is not, and stays null so the panel
    // says nothing at all rather than implying the agent reported none.
    expect(decodePlanCard(exchange(PLAN_RESPONSE, PLAN_INPUT))?.resolvedPolicies).toEqual([]);

    const noField = { ...PLAN_RESPONSE, plans: [{ plan_id: 'p', status: 'active', version: 1 }] };
    expect(decodePlanCard(exchange(noField, PLAN_INPUT))?.resolvedPolicies).toBeNull();
  });

  it('renders a real zero rather than dropping it', () => {
    // `version: 0` and a zero budget are measurements, not absences. A truthiness check would silently
    // delete both, which is the "absent and zero are different" rule this project already carries for
    // product fields.
    const zeroed = { plans: [{ plan_id: 'p', status: 'active', version: 0 }] };
    const data = decodePlanCard(exchange(zeroed, { budget_total: 0, currency: 'USD' }));
    expect(data?.version).toBe('0');
    expect(data?.budgetAmount).toBe('0');
  });

  it('omits the budget when the amount is missing, rather than showing a currency alone', () => {
    // A large blank above "USD" would read as a figure that failed to load.
    const data = decodePlanCard(exchange(PLAN_RESPONSE, { plan_id: 'p', currency: 'USD' }));
    expect(data?.budgetAmount).toBeNull();
  });

  it('leaves a partial flight incomplete rather than implying an open-ended window', () => {
    const data = decodePlanCard(exchange(PLAN_RESPONSE, { plan_id: 'p', flight_start: '2026-09-01' }));
    expect(data?.flightStart).toBe('2026-09-01');
    expect(data?.flightEnd).toBeNull();
  });

  it('returns null when there is no plan and no submitted terms', () => {
    expect(decodePlanCard(exchange({ status: 'completed', plans: [] }, {}))).toBeNull();
    expect(decodePlanCard(exchange('nonsense', {}))).toBeNull();
  });
});
