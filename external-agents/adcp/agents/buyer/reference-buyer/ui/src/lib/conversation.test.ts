import { describe, expect, it } from 'vitest';

import {
  conversationReducer as reduce,
  initialConversation,
  type ConversationAction,
  type ConversationState,
} from './conversation';

function run(actions: ConversationAction[], from: ConversationState = initialConversation) {
  return actions.reduce(reduce, from);
}

describe('sending a turn', () => {
  it('adds the user message and marks the turn pending', () => {
    const state = run([{ type: 'send', id: 't1', text: 'find inventory' }]);
    expect(state.messages).toEqual([{ kind: 'user', id: 't1', text: 'find inventory' }]);
    expect(state.pending).toBe(true);
  });

  it('clears a previous error when a new turn starts', () => {
    const state = run([
      { type: 'send', id: 't1', text: 'a' },
      { type: 'turnFailed', id: 't1', message: 'boom' },
      { type: 'send', id: 't2', text: 'b' },
    ]);
    expect(state.error).toBeNull();
  });
});

describe('streaming agent text', () => {
  it('creates the bubble on first text, not when the turn starts', () => {
    const afterSend = run([{ type: 'send', id: 't1', text: 'q' }]);
    expect(afterSend.messages.some((m) => m.kind === 'agent')).toBe(false);
    const afterText = reduce(afterSend, { type: 'agentText', id: 't1', text: 'hi' });
    expect(afterText.messages.filter((m) => m.kind === 'agent')).toHaveLength(1);
  });

  it('appends successive chunks into one bubble', () => {
    const state = run([
      { type: 'send', id: 't1', text: 'q' },
      { type: 'agentText', id: 't1', text: 'Hello ' },
      { type: 'agentText', id: 't1', text: 'there' },
    ]);
    const agent = state.messages.find((m) => m.kind === 'agent');
    expect(agent).toMatchObject({ text: 'Hello there', streaming: true });
    expect(state.messages.filter((m) => m.kind === 'agent')).toHaveLength(1);
  });
});

describe('ordering of seller results against the agent reply', () => {
  it('places seller results before the agent bubble when one is already streaming', () => {
    // The agent summarises what the sellers returned, so its summary must read after them.
    const state = run([
      { type: 'send', id: 't1', text: 'q' },
      { type: 'agentText', id: 't1', text: 'Let me check' },
      {
        type: 'sellerResults',
        toolName: 'get_products',
        idPrefix: 't1-r0',
        entries: [{ seller_name: 'Gotham', response: { products: [] } }],
      },
    ]);
    expect(state.messages.map((m) => m.kind)).toEqual(['user', 'seller', 'agent']);
  });

  it('appends seller results when no reply has started yet', () => {
    const state = run([
      { type: 'send', id: 't1', text: 'q' },
      {
        type: 'sellerResults',
        toolName: 'get_products',
        idPrefix: 't1-r0',
        entries: [{ seller_name: 'Gotham', response: { products: [] } }],
      },
    ]);
    expect(state.messages.map((m) => m.kind)).toEqual(['user', 'seller']);
  });

  it('gives one message per seller, named from the response', () => {
    const state = run([
      { type: 'send', id: 't1', text: 'q' },
      {
        type: 'sellerResults',
        toolName: 'get_products',
        idPrefix: 't1-r0',
        entries: [
          { seller_name: 'Gotham', response: { products: [] } },
          { seller_name: 'Triton', response: { products: [] } },
        ],
      },
    ]);
    const sellers = state.messages.filter((m) => m.kind === 'seller');
    expect(sellers.map((m) => (m.kind === 'seller' ? m.sellerName : ''))).toEqual([
      'Gotham',
      'Triton',
    ]);
  });

  it('keeps a failed seller and shows its error rather than dropping it', () => {
    // A missing seller would read as "it had nothing", which is a different claim.
    const state = run([
      { type: 'send', id: 't1', text: 'q' },
      {
        type: 'sellerResults',
        toolName: 'get_products',
        idPrefix: 't1-r0',
        entries: [{ seller_name: 'Broken', error: 'timed out' }],
      },
    ]);
    const seller = state.messages.find((m) => m.kind === 'seller');
    expect(seller && seller.kind === 'seller' ? seller.payload : null).toEqual({
      error: 'timed out',
    });
  });

  it('gives every seller message a distinct id', () => {
    const state = run([
      { type: 'send', id: 't1', text: 'q' },
      {
        type: 'sellerResults',
        toolName: 'get_products',
        idPrefix: 't1-r0',
        entries: [{ seller_name: 'A' }, { seller_name: 'B' }, { seller_name: 'C' }],
      },
    ]);
    const ids = state.messages.filter((m) => m.kind === 'seller').map((m) => m.id);
    expect(new Set(ids).size).toBe(3);
  });
});

describe('settling a turn', () => {
  it('stops the streaming flag and clears pending', () => {
    const state = run([
      { type: 'send', id: 't1', text: 'q' },
      { type: 'agentText', id: 't1', text: 'done' },
      { type: 'turnSettled' },
    ]);
    expect(state.pending).toBe(false);
    expect(state.messages.find((m) => m.kind === 'agent')).toMatchObject({ streaming: false });
  });

  it('keeps partial text when the turn fails, and adds the reason', () => {
    const state = run([
      { type: 'send', id: 't1', text: 'q' },
      { type: 'agentText', id: 't1', text: 'I found ' },
      { type: 'turnFailed', id: 't1', message: 'HTTP 500' },
    ]);
    const agent = state.messages.find((m) => m.kind === 'agent');
    expect(agent).toMatchObject({ text: 'I found ', streaming: false });
    const note = state.messages.find((m) => m.kind === 'note');
    expect(note).toMatchObject({ label: 'Turn failed', text: 'HTTP 500' });
    expect(state.error).toBe('HTTP 500');
    expect(state.pending).toBe(false);
  });

  it('records a failure that produced no text at all', () => {
    const state = run([
      { type: 'send', id: 't1', text: 'q' },
      { type: 'turnFailed', id: 't1', message: 'not signed in' },
    ]);
    expect(state.messages.map((m) => m.kind)).toEqual(['user', 'note']);
  });
});

describe('reset', () => {
  it('clears everything, so a replayed session cannot mix with the live one', () => {
    const state = run([
      { type: 'send', id: 't1', text: 'q' },
      { type: 'agentText', id: 't1', text: 'x' },
      { type: 'reset' },
    ]);
    expect(state).toEqual(initialConversation);
  });
});

describe('resuming a conversation', () => {
  it('replaces the transcript with the loaded history and is neither pending nor errored', () => {
    // A prior turn's leftover state must not carry into the resumed conversation: resuming starts no
    // turn, so pending/error describe nothing here.
    const dirty = run([
      { type: 'send', id: 't1', text: 'earlier' },
      { type: 'turnFailed', id: 't1', message: 'boom' },
    ]);
    expect(dirty.pending).toBe(false);
    expect(dirty.error).toBe('boom');

    const history: import('./conversation').Message[] = [
      { kind: 'user', id: 'h1', text: 'find CTV inventory' },
      { kind: 'agent', id: 'h2', text: 'Here is what I found.', streaming: false },
    ];
    const resumed = reduce(dirty, { type: 'loadHistory', messages: history });

    expect(resumed.messages).toEqual(history);
    expect(resumed.pending).toBe(false);
    expect(resumed.error).toBeNull();
  });

  it('lets a new turn append after the loaded history', () => {
    const history: import('./conversation').Message[] = [
      { kind: 'user', id: 'h1', text: 'earlier brief' },
      { kind: 'agent', id: 'h2', text: 'earlier reply', streaming: false },
    ];
    const state = run(
      [
        { type: 'loadHistory', messages: history },
        { type: 'send', id: 't2', text: 'continue please' },
      ],
    );
    expect(state.messages).toHaveLength(3);
    expect(state.messages[2]).toEqual({ kind: 'user', id: 't2', text: 'continue please' });
    expect(state.pending).toBe(true);
  });
});
