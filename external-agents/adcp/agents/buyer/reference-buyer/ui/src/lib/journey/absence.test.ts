import { describe, expect, it } from 'vitest';

import { classify, describeReason, isFailure } from './absence';
import { phaseByKey } from './phases';
import type { BuildCapabilities, ToolExchange } from './types';

const DISCOVER = phaseByKey('discover')!;
const GOVERN = phaseByKey('govern')!;

/** A build that can call get_products but has no governance. This is U1's real shape. */
const U1_CAPS: BuildCapabilities = { callableTools: ['adcp_get_products', 'adcp_list_creative_formats'] };
const ALL_CAPS: BuildCapabilities = {
  callableTools: ['adcp_get_products', 'adcp_check_governance', 'adcp_list_creative_formats'],
};

function exchange(partial: Partial<ToolExchange> = {}): ToolExchange {
  return {
    callId: 'c1',
    toolName: 'adcp_get_products',
    input: {},
    callIndex: 0,
    ...partial,
  };
}

describe('classify', () => {
  it('reports not_implemented when this build cannot make the call', () => {
    const state = classify(GOVERN, undefined, U1_CAPS);
    expect(state.state).toBe('not_implemented');
    if (state.state === 'not_implemented') {
      expect(state.detail).toContain('adcp_check_governance');
      // Says plainly that it is not a failure of this session, which is the distinction a
      // demonstrator needs.
      expect(state.detail).toContain('not a failure');
    }
  });

  it('reports not_reached when the call is possible but has not happened', () => {
    expect(classify(DISCOVER, undefined, U1_CAPS)).toEqual({ state: 'not_reached' });
  });

  it('reports not_reached for a call still in flight', () => {
    // The distinction that matters most in a live session: a fan-out takes seconds.
    expect(classify(DISCOVER, exchange(), U1_CAPS)).toEqual({ state: 'not_reached' });
  });

  it('reports unreachable for a failed result', () => {
    const state = classify(
      DISCOVER,
      exchange({ result: { output: 'ConnectionError: refused', status: 'error', index: 1 } }),
      U1_CAPS,
    );
    expect(state.state).toBe('unreachable');
    if (state.state === 'unreachable') expect(state.detail).toContain('ConnectionError');
  });

  it('routes an UNRECOGNISED error to unreachable, never to not_reached', () => {
    // The residual rule. An error nobody anticipated must land on the variant that admits something
    // went wrong; defaulting to not_reached would instead say the call was never made.
    const state = classify(
      DISCOVER,
      exchange({ result: { output: 'something nobody predicted', status: 'error', index: 1 } }),
      U1_CAPS,
    );
    expect(state.state).toBe('unreachable');
  });

  it('reports unreachable when the result could not be recorded', () => {
    const state = classify(
      DISCOVER,
      exchange({
        truncation: { notRecorded: true, reason: 'over the item limit' },
        result: { output: undefined, status: 'success', index: 1 },
      }),
      U1_CAPS,
    );
    expect(state.state).toBe('unreachable');
    if (state.state === 'unreachable') expect(state.detail).toContain('too large to record');
  });

  it('reports unreachable when every seller in a fan-out failed', () => {
    const state = classify(
      DISCOVER,
      exchange({
        result: {
          status: 'success',
          index: 1,
          output: {
            task: 'adcp_get_products',
            sellers_queried: 2,
            results: [
              { seller_id: 'a', error: 'TimeoutError' },
              { seller_id: 'b', response: { mcp_error: 'Unknown tool' } },
            ],
          },
        },
      }),
      U1_CAPS,
    );
    expect(state.state).toBe('unreachable');
    if (state.state === 'unreachable') expect(state.detail).toContain('2 sales agents');
  });

  it('does NOT report unreachable when only some sellers failed', () => {
    // "Two sellers could not be asked" and "two sellers have no inventory" are different facts, so a
    // partial failure must not blank the panel. classify falls through to the caller's decode path.
    const state = classify(
      DISCOVER,
      exchange({
        result: {
          status: 'success',
          index: 1,
          output: {
            results: [{ seller_id: 'a', error: 'TimeoutError' }, { seller_id: 'b', response: {} }],
          },
        },
      }),
      U1_CAPS,
    );
    // Still an absence from classify's point of view (it never returns ready), but not attributed to
    // the fan-out having wholly failed.
    expect(state.state).toBe('unreachable');
    if (state.state === 'unreachable') expect(state.detail).not.toContain('All 2');
  });

  it('never returns ready, because deciding data exists is the caller\u2019s job', () => {
    const inputs: Array<ToolExchange | undefined> = [
      undefined,
      exchange(),
      exchange({ result: { output: {}, status: 'success', index: 1 } }),
      exchange({ result: { output: 'boom', status: 'error', index: 1 } }),
    ];
    inputs.forEach((input) => {
      expect(classify(DISCOVER, input, ALL_CAPS).state).not.toBe('ready');
    });
  });

  it('treats a callable tool with no exchange as not_reached even when others are missing', () => {
    expect(classify(DISCOVER, undefined, ALL_CAPS)).toEqual({ state: 'not_reached' });
    expect(classify(GOVERN, undefined, ALL_CAPS)).toEqual({ state: 'not_reached' });
  });
});

describe('describeReason', () => {
  it('says nothing has gone wrong for not_reached', () => {
    const text = describeReason(DISCOVER, { state: 'not_reached' });
    expect(text).toContain('has not run');
    expect(text).toContain('Nothing has gone wrong');
  });

  it('passes through the real detail for not_implemented and unreachable', () => {
    expect(describeReason(GOVERN, { state: 'not_implemented', detail: 'no governance here' })).toBe(
      'no governance here',
    );
    expect(describeReason(GOVERN, { state: 'unreachable', detail: 'agent timed out' })).toBe(
      'agent timed out',
    );
  });

  it('describes a ready phase by the tool that supplied it', () => {
    // The sentence names the AdCP TASK (`op`), not the agent's local tool name. It is read aloud by a
    // demonstrator, and "get_products" is the protocol word that means something to a listener;
    // "adcp_get_products" is an implementation detail of this particular agent.
    expect(describeReason(DISCOVER, { state: 'ready', data: {} })).toContain('get_products');
    expect(DISCOVER.op).toBe('get_products');
  });
});

describe('isFailure', () => {
  it('counts only unreachable as a failure', () => {
    expect(isFailure({ state: 'unreachable', detail: '' })).toBe(true);
    expect(isFailure({ state: 'not_reached' })).toBe(false);
    expect(isFailure({ state: 'not_implemented', detail: '' })).toBe(false);
    expect(isFailure({ state: 'ready', data: {} })).toBe(false);
  });
});
