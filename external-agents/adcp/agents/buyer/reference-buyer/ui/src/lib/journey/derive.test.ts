import { describe, expect, it } from 'vitest';

import type { Product, SessionStep } from '../types';
import { deriveJourney, derivePhases, entityChipsFor, readyPhases } from './derive';
import { PHASES } from './phases';
import type { DeriveContext, DiscoverData } from './types';

/** U1's real capability set: the buyer's five tools, none of them governance or media buy. */
const U1: DeriveContext = {
  capabilities: {
    callableTools: [
      'adcp_get_products',
      'get_adcp_capabilities',
      'adcp_list_creative_formats',
    ],
  },
  sellerPriority: [],
};

const product = (formatId: string): Product => ({
  product_id: `p-${formatId}`,
  format_ids: [formatId],
});

function discoverySession(products: Product[], sellerId = 'triton'): SessionStep[] {
  return [
    { step_index: 0, step_type: 'incoming_request', content: { text: 'premium streaming audio' } },
    { step_index: 1, step_type: 'tool_call', content: { toolName: 'adcp_get_products', callId: 'c1' } },
    {
      step_index: 2,
      step_type: 'tool_result',
      content: {
        callId: 'c1',
        status: 'success',
        output: {
          task: 'adcp_get_products',
          sellers_queried: 1,
          results: [{ seller_id: sellerId, seller_name: `${sellerId} agent`, response: { products } }],
        },
      },
    },
  ];
}

describe('derivePhases', () => {
  it('returns a state for every registry phase, none missing', () => {
    const states = derivePhases([], U1);
    expect(states.size).toBe(PHASES.length);
    PHASES.forEach((phase) => expect(states.has(phase.key)).toBe(true));
  });

  it('reports not_reached for a callable tool that has not run', () => {
    expect(derivePhases([], U1).get('discover')).toEqual({ state: 'not_reached' });
  });

  it('reports not_implemented for the phases this build cannot call', () => {
    // Eight dark panels is this unit's correct, complete behaviour, not a gap. Every phase except
    // Discover names a tool this build cannot call.
    // Creative is deliberately NOT in this list any more. Correcting its binding to
    // `adcp_list_creative_formats` — a tool the agent really registers — moved it from "this build cannot
    // call it" to "it has not run yet", which is a different sentence to say to someone at a booth and is
    // the whole point of the change.
    const states = derivePhases([], U1);
    (['bind', 'plan', 'govern', 'score', 'book', 'report', 'activate'] as const).forEach((key) =>
      expect(states.get(key)?.state).toBe('not_implemented'),
    );
    expect(states.get('creative')?.state).toBe('not_reached');
  });

  it('reports ready for Discover from a real fan-out', () => {
    const states = derivePhases(discoverySession([product('audio_30s')]), U1);
    const discover = states.get('discover');
    expect(discover?.state).toBe('ready');
    if (discover?.state === 'ready') {
      const data = discover.data as DiscoverData;
      expect(data.groups[0]?.formatKey).toBe('audio');
      expect(data.groups[0]?.count).toBe(1);
    }
  });

  it('treats a fan-out where every seller returned nothing as a real, empty answer', () => {
    // The sellers were asked and have nothing matching. That is data, not absence.
    const states = derivePhases(discoverySession([]), U1);
    const discover = states.get('discover');
    expect(discover?.state).toBe('ready');
    if (discover?.state === 'ready') {
      expect((discover.data as DiscoverData).groups).toEqual([]);
    }
  });

  it('reports not_reached while the discovery call is still in flight', () => {
    const steps: SessionStep[] = [
      { step_index: 0, step_type: 'tool_call', content: { toolName: 'adcp_get_products', callId: 'c1' } },
    ];
    expect(derivePhases(steps, U1).get('discover')).toEqual({ state: 'not_reached' });
  });

  it('reports unreachable when the discovery call failed', () => {
    const steps: SessionStep[] = [
      { step_index: 0, step_type: 'tool_call', content: { toolName: 'adcp_get_products', callId: 'c1' } },
      {
        step_index: 1,
        step_type: 'tool_result',
        content: { callId: 'c1', status: 'error', output: 'ConnectionError' },
      },
    ];
    expect(derivePhases(steps, U1).get('discover')?.state).toBe('unreachable');
  });

  it('ignores a tool no phase is bound to', () => {
    const steps: SessionStep[] = [
      {
        step_index: 0,
        step_type: 'tool_call',
        content: { toolName: 'vendor_get_market_rates', callId: 'c1' },
      },
      { step_index: 1, step_type: 'tool_result', content: { callId: 'c1', status: 'success', output: {} } },
    ];
    const states = derivePhases(steps, U1);
    // No phase becomes ready; the journey simply does not place that tool.
    expect(readyPhases(states)).toEqual([]);
  });

  it('uses the latest exchange when a tool ran more than once', () => {
    const steps: SessionStep[] = [
      ...discoverySession([product('audio_30s')]),
      { step_index: 3, step_type: 'tool_call', content: { toolName: 'adcp_get_products', callId: 'c2' } },
      {
        step_index: 4,
        step_type: 'tool_result',
        content: {
          callId: 'c2',
          status: 'success',
          output: {
            results: [
              { seller_id: 's', response: { products: [product('podcast_mid'), product('podcast_pre')] } },
            ],
          },
        },
      },
    ];
    const discover = derivePhases(steps, U1).get('discover');
    if (discover?.state === 'ready') {
      const data = discover.data as DiscoverData;
      expect(data.groups[0]?.formatKey).toBe('podcast');
      expect(data.groups[0]?.count).toBe(2);
    }
  });
});

describe('readyPhases', () => {
  it('returns ready phases in REGISTRY order, not arrival order', () => {
    // The rail is an authored narrative. A tool answering early must not reorder it.
    const states = derivePhases(discoverySession([product('audio_30s')]), U1);
    const ready = readyPhases(states);
    const positions = ready.map((key) => PHASES.findIndex((p) => p.key === key));
    expect(positions).toEqual([...positions].sort((a, b) => a - b));
  });

  it('is empty for a session with no usable data', () => {
    expect(readyPhases(derivePhases([], U1))).toEqual([]);
  });
});

describe('entityChipsFor', () => {
  it('contributes a products chip from real counts once Discover is ready', () => {
    const states = derivePhases(discoverySession([product('audio_30s'), product('podcast_mid')]), U1);
    const chips = entityChipsFor(states);
    const products = chips.find((c) => c.key === 'products');
    expect(products?.source).toBe('tool_result');
    expect(products?.value).toContain('2 products');
  });

  it('contributes nothing while Discover is not ready', () => {
    expect(entityChipsFor(derivePhases([], U1))).toEqual([]);
  });
});

describe('deriveJourney', () => {
  it('assembles states, chips and seller selection in one pass', () => {
    const journey = deriveJourney(discoverySession([product('audio_30s')]), null, U1);
    expect(journey.states.get('discover')?.state).toBe('ready');
    expect(journey.chips[0]?.key).toBe('brief');
    expect(journey.shownSeller?.sellerId).toBe('triton');
    expect(journey.otherSellers).toEqual([]);
    expect(journey.othersNotice).toBeNull();
  });

  it('names the other sellers when the fan-out reached several', () => {
    const steps: SessionStep[] = [
      { step_index: 0, step_type: 'tool_call', content: { toolName: 'adcp_get_products', callId: 'c1' } },
      {
        step_index: 1,
        step_type: 'tool_result',
        content: {
          callId: 'c1',
          status: 'success',
          output: {
            results: [
              { seller_id: 'a', seller_name: 'A agent', response: { products: [product('audio_30s')] } },
              { seller_id: 'b', seller_name: 'B agent', error: 'TimeoutError' },
            ],
          },
        },
      },
    ];
    const journey = deriveJourney(steps, null, { ...U1, sellerPriority: ['a', 'b'] });
    expect(journey.shownSeller?.sellerId).toBe('a');
    expect(journey.othersNotice).toContain('B agent (errored)');
  });

  it('prefers plan values for the summary chips when a plan exists', () => {
    const journey = deriveJourney(
      discoverySession([product('audio_30s')]),
      null,
      U1,
      { budget: '$250,000' },
    );
    const budget = journey.chips.find((c) => c.key === 'budget');
    expect(budget?.value).toBe('$250,000');
    expect(budget?.source).toBe('plan');
  });

  it('produces no seller selection for a session with no discovery call', () => {
    const journey = deriveJourney([], null, U1);
    expect(journey.shownSeller).toBeNull();
    expect(journey.chips).toEqual([]);
  });
});
