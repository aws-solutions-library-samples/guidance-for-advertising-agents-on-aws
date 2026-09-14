import { describe, expect, it } from 'vitest';

import {
  initialToolLog,
  toolLogReducer as reduce,
  type ToolLogAction,
  type ToolLogState,
} from './toolLog';

function run(actions: ToolLogAction[], from: ToolLogState = initialToolLog) {
  return actions.reduce(reduce, from);
}

const started: ToolLogAction = {
  type: 'started',
  call: { id: 'call-1', toolName: 'adcp_get_products', status: 'pending' },
};

describe('a call in flight', () => {
  it('is recorded as pending', () => {
    const state = run([started]);
    expect(state.calls).toHaveLength(1);
    expect(state.calls[0]).toMatchObject({ toolName: 'adcp_get_products', status: 'pending' });
  });
});

describe('a completed call', () => {
  it('records success and the raw payload for a non-fan-out result', () => {
    const state = run([
      started,
      {
        type: 'finished',
        callId: 'call-1',
        output: { adcp: { version: '1' } },
        ok: true,
        trimNote: '',
        durationMs: 120,
      },
    ]);
    expect(state.calls[0]?.status).toBe('success');
    expect(state.calls[0]?.body).toContain('"version"');
    expect(state.calls[0]?.body).toContain('120ms');
  });

  it('summarises a fan-out and lists each seller separately', () => {
    const state = run([
      started,
      {
        type: 'finished',
        callId: 'call-1',
        output: {},
        ok: true,
        trimNote: '',
        entries: [
          { seller_name: 'Gotham', response: { products: [{}, {}] } },
          { seller_name: 'Triton', response: { products: [] } },
        ],
      },
    ]);
    expect(state.calls[0]?.body).toContain('2 sales agents queried');
    expect(state.calls[0]?.body).toContain('Gotham, 2 products');
    expect(state.calls[0]?.sellers?.map((s) => s.name)).toEqual(['Gotham', 'Triton']);
  });

  it('takes the status from the entries, not from the call, when there are entries', () => {
    // Each entry's own outcome is authoritative; a blanket status would hide a partial failure.
    const state = run([
      started,
      {
        type: 'finished',
        callId: 'call-1',
        output: {},
        ok: true,
        trimNote: '',
        entries: [
          { seller_name: 'Gotham', response: { products: [] } },
          { seller_name: 'Broken', error: 'timed out' },
        ],
      },
    ]);
    expect(state.calls[0]?.status).toBe('error');
    expect(state.calls[0]?.sellers?.map((s) => s.failed)).toEqual([false, true]);
  });

  it('marks all-succeeded fan-outs as success', () => {
    const state = run([
      started,
      {
        type: 'finished',
        callId: 'call-1',
        output: {},
        ok: false,
        trimNote: '',
        entries: [{ seller_name: 'Gotham', response: { products: [] } }],
      },
    ]);
    expect(state.calls[0]?.status).toBe('success');
  });

  it('carries the recorder trim note into the body', () => {
    const state = run([
      started,
      {
        type: 'finished',
        callId: 'call-1',
        output: {},
        ok: true,
        trimNote: '\n\nOnly part of this result was recorded',
        entries: [{ seller_name: 'Gotham', response: { products: [] } }],
      },
    ]);
    expect(state.calls[0]?.body).toContain('Only part of this result was recorded');
  });
});

describe('a result with no matching call', () => {
  it('is still recorded rather than dropped', () => {
    // The baseline high-water mark can sit between a call and its result, and dropping the result
    // would hide real tool activity.
    const state = run([
      {
        type: 'finished',
        callId: 'orphan',
        toolName: 'adcp_get_products',
        output: {},
        ok: true,
        trimNote: '',
      },
    ]);
    expect(state.calls).toHaveLength(1);
    expect(state.calls[0]).toMatchObject({ toolName: 'adcp_get_products', status: 'success' });
  });

  it('falls back to a neutral name when none was reported', () => {
    const state = run([
      { type: 'finished', callId: 'orphan', output: {}, ok: true, trimNote: '' },
    ]);
    expect(state.calls[0]?.toolName).toBe('tool');
  });
});

describe('an unrecorded result', () => {
  it('is not reported as an error, because the call may have succeeded', () => {
    const state = run([
      started,
      { type: 'notRecorded', callId: 'call-1', note: 'This result was not recorded (900 bytes)' },
    ]);
    expect(state.calls[0]?.status).toBe('not_recorded');
    expect(state.calls[0]?.status).not.toBe('error');
    expect(state.calls[0]?.body).toContain('not recorded');
  });
});

describe('when the turn ends', () => {
  it('stops a call still pending and says what is actually known', () => {
    const state = run([started, { type: 'abandonPending' }]);
    expect(state.calls[0]?.status).toBe('abandoned');
    // Leaving a spinner would imply work is still in flight.
    expect(state.calls[0]?.body).toContain('may still have succeeded');
  });

  it('leaves completed calls alone', () => {
    const state = run([
      started,
      { type: 'finished', callId: 'call-1', output: {}, ok: true, trimNote: '' },
      { type: 'abandonPending' },
    ]);
    expect(state.calls[0]?.status).toBe('success');
  });
});

describe('clear', () => {
  it('empties the log between turns', () => {
    expect(run([started, { type: 'clear' }])).toEqual(initialToolLog);
  });
});
