import { describe, expect, it } from 'vitest';

import { highestStepIndex, replaySteps } from './replay';
import type { Message } from './conversation';
import type { SessionStep } from './types';

const step = (over: Partial<SessionStep> & Pick<SessionStep, 'step_index' | 'step_type'>): SessionStep =>
  over as SessionStep;

/**
 * The label of a message, for the variants that carry one.
 *
 * Narrows rather than casting, so a future variant without a label is a compile error here instead
 * of an undefined at runtime.
 */
function labelOf(message: Message | undefined): string {
  if (!message) return '';
  switch (message.kind) {
    case 'user':
    case 'agent':
    case 'thought':
      return message.label ?? '';
    case 'note':
      return message.label;
    case 'seller':
      return '';
  }
}

describe('replaySteps', () => {
  it('renders the caller message as a user bubble naming the caller', () => {
    const { messages } = replaySteps([
      step({
        step_index: 0,
        step_type: 'incoming_request',
        content: { from: 'External Agent', text: 'Find inventory' },
        timestamp: '2026-08-06T12:00:00Z',
      }),
    ]);
    expect(messages[0]).toMatchObject({ kind: 'user', text: 'Find inventory' });
    expect(labelOf(messages[0])).toContain('External Agent');
    // The time is part of the label, since a replay has no other ordering cue.
    expect(labelOf(messages[0])).toMatch(/·/);
  });

  it('says the caller is unknown rather than naming one', () => {
    const { messages } = replaySteps([
      step({ step_index: 0, step_type: 'incoming_request', content: { text: 'hi' } }),
    ]);
    expect(labelOf(messages[0])).toContain('Unknown caller');
  });

  it('labels reasoning as reasoning, so it is not read as the reply', () => {
    const { messages } = replaySteps([
      step({ step_index: 1, step_type: 'thought', content: { text: 'considering' } }),
    ]);
    expect(messages[0]).toMatchObject({ kind: 'thought', text: 'considering' });
    expect(labelOf(messages[0])).toContain('reasoning');
  });

  it('renders the reply as a completed agent bubble, not a streaming one', () => {
    const { messages } = replaySteps([
      step({ step_index: 4, step_type: 'response', content: { to: 'External Agent', text: 'done' } }),
    ]);
    expect(messages[0]).toMatchObject({ kind: 'agent', text: 'done', streaming: false });
    expect(labelOf(messages[0])).toContain('External Agent');
  });

  it('opens a pending tool entry for a call', () => {
    const { toolActions } = replaySteps([
      step({
        step_index: 1,
        step_type: 'tool_call',
        content: { toolName: 'adcp_get_products', callId: 'c1', input: { brief: 'x' } },
      }),
    ]);
    expect(toolActions[0]).toMatchObject({
      type: 'started',
      call: { id: 'c1', toolName: 'adcp_get_products', status: 'pending' },
    });
  });

  it('completes the entry and adds one seller message per answering agent', () => {
    const { messages, toolActions } = replaySteps([
      step({
        step_index: 2,
        step_type: 'tool_result',
        content: {
          callId: 'c1',
          toolName: 'adcp_get_products',
          status: 'success',
          durationMs: 900,
          output: JSON.stringify({
            results: [
              { seller_name: 'Gotham', response: { products: [{}] } },
              { seller_name: 'Triton', response: { products: [] } },
            ],
          }),
        },
      }),
    ]);
    expect(toolActions[0]).toMatchObject({ type: 'finished', callId: 'c1', ok: true });
    expect(messages.map((m) => (m.kind === 'seller' ? m.sellerName : ''))).toEqual([
      'Gotham',
      'Triton',
    ]);
  });

  it('names each seller from its own response, not from the session record', () => {
    // A fan-out queried every agent, so one recorded seller_agent_id would mislabel most entries.
    const { messages } = replaySteps([
      step({
        step_index: 2,
        step_type: 'tool_result',
        content: {
          output: { results: [{ seller_id: 'gotham' }, { seller_id: 'triton' }] },
        },
      }),
    ]);
    expect(messages.map((m) => (m.kind === 'seller' ? m.sellerName : ''))).toEqual([
      'gotham',
      'triton',
    ]);
  });

  it('keeps a failed seller and shows its error', () => {
    const { messages } = replaySteps([
      step({
        step_index: 2,
        step_type: 'tool_result',
        content: { output: { results: [{ seller_name: 'Broken', error: 'timed out' }] } },
      }),
    ]);
    expect(messages[0]?.kind === 'seller' ? messages[0].payload : null).toEqual({
      error: 'timed out',
    });
  });

  it('reports an unrecorded result without calling it an error', () => {
    const { toolActions, messages } = replaySteps([
      step({
        step_index: 2,
        step_type: 'tool_result',
        content: { callId: 'c1', _not_recorded: { original_bytes: 900, reason: 'too large' } },
      }),
    ]);
    expect(toolActions[0]).toMatchObject({ type: 'notRecorded', callId: 'c1' });
    expect(messages).toHaveLength(0);
  });

  it('adds no seller messages for a non-fan-out result', () => {
    const { messages, toolActions } = replaySteps([
      step({
        step_index: 2,
        step_type: 'tool_result',
        content: { callId: 'c1', output: { adcp: { version: '1' } }, status: 'success' },
      }),
    ]);
    expect(messages).toHaveLength(0);
    expect(toolActions[0]).toMatchObject({ type: 'finished' });
  });

  it('skips a step type it does not know rather than rendering an empty bubble', () => {
    // Steps are written by services that may add types this build has not seen.
    const { messages, toolActions } = replaySteps([
      step({ step_index: 9, step_type: 'some_future_type', content: { text: 'x' } }),
    ]);
    expect(messages).toHaveLength(0);
    expect(toolActions).toHaveLength(0);
  });

  it('renders a whole conversation in recorded order', () => {
    const { messages } = replaySteps([
      step({ step_index: 0, step_type: 'incoming_request', content: { text: 'q' } }),
      step({ step_index: 1, step_type: 'thought', content: { text: 'thinking' } }),
      step({
        step_index: 2,
        step_type: 'tool_result',
        content: { output: { results: [{ seller_name: 'Gotham' }] } },
      }),
      step({ step_index: 3, step_type: 'response', content: { text: 'here you go' } }),
    ]);
    expect(messages.map((m) => m.kind)).toEqual(['user', 'thought', 'seller', 'agent']);
  });

  it('gives every message a distinct id, so applying a batch cannot collide', () => {
    const { messages } = replaySteps([
      step({
        step_index: 2,
        step_type: 'tool_result',
        content: { output: { results: [{ seller_name: 'A' }, { seller_name: 'B' }] } },
      }),
      step({ step_index: 3, step_type: 'response', content: { text: 'x' } }),
    ]);
    expect(new Set(messages.map((m) => m.id)).size).toBe(messages.length);
  });
});

describe('highestStepIndex', () => {
  it('returns the highest index seen', () => {
    expect(
      highestStepIndex(
        [step({ step_index: 3, step_type: 'response' }), step({ step_index: 7, step_type: 'thought' })],
        -1,
      ),
    ).toBe(7);
  });

  it('keeps the current mark for an empty batch', () => {
    expect(highestStepIndex([], 4)).toBe(4);
  });

  it('never moves the mark backwards', () => {
    expect(highestStepIndex([step({ step_index: 1, step_type: 'response' })], 5)).toBe(5);
  });
});
