/**
 * The conversation's shape, and the transitions a turn puts it through.
 *
 * Kept as a pure reducer so the ordering rules can be tested without a network or a DOM. Those rules
 * are where the vanilla UI had to be careful, and the care is worth preserving:
 *
 *   - The agent's reply bubble is created when text first arrives, not when the turn starts, so DOM
 *     order matches chronological order. A bubble reserved up front sits above the seller results
 *     that actually arrived before it.
 *   - Seller results are inserted before the pending indicator, so they read in the order they
 *     happened rather than after the agent has apparently finished.
 *   - A failed turn keeps whatever text arrived. Replacing a partial reply with an error would
 *     discard something the agent really said.
 */

import type { SellerEntry } from './types';

export type Message =
  /** `label` overrides the default heading, which replay uses to name the caller and the time. */
  | { kind: 'user'; id: string; text: string; label?: string }
  | { kind: 'agent'; id: string; text: string; streaming: boolean; label?: string }
  /** The model's interleaved reasoning, labelled as such so it is not read as the reply. */
  | { kind: 'thought'; id: string; text: string; label?: string }
  | { kind: 'seller'; id: string; toolName: string; sellerName: string; payload: unknown }
  | { kind: 'note'; id: string; label: string; text: string };

export interface ConversationState {
  messages: Message[];
  /** True from the moment a turn is sent until it settles, whether it succeeded or not. */
  pending: boolean;
  /** Set when the turn could not run at all, as opposed to returning a poor answer. */
  error: string | null;
}

export const initialConversation: ConversationState = {
  messages: [],
  pending: false,
  error: null,
};

export type ConversationAction =
  | { type: 'send'; id: string; text: string }
  | { type: 'agentText'; id: string; text: string }
  | { type: 'sellerResults'; toolName: string; entries: SellerEntry[]; idPrefix: string }
  | { type: 'note'; id: string; label: string; text: string }
  | { type: 'turnFailed'; id: string; message: string }
  | { type: 'turnSettled' }
  /** Resume: replace the transcript with a recorded conversation's bubbles so it can be continued. */
  | { type: 'loadHistory'; messages: Message[] }
  | { type: 'reset' };

export function conversationReducer(
  state: ConversationState,
  action: ConversationAction,
): ConversationState {
  switch (action.type) {
    case 'send':
      return {
        messages: [...state.messages, { kind: 'user', id: action.id, text: action.text }],
        pending: true,
        error: null,
      };

    case 'agentText': {
      // Existence is the only question: one agent bubble per turn id, so `some` is enough and this
      // avoids needing the ES2023 lib for `findLast`.
      const existing = state.messages.some((m) => m.kind === 'agent' && m.id === action.id);
      if (!existing) {
        // First text of the turn: the bubble is created now, which is what keeps it below the
        // seller results that arrived before it.
        return {
          ...state,
          messages: [
            ...state.messages,
            { kind: 'agent', id: action.id, text: action.text, streaming: true },
          ],
        };
      }
      return {
        ...state,
        messages: state.messages.map((m) =>
          m.kind === 'agent' && m.id === action.id ? { ...m, text: m.text + action.text } : m,
        ),
      };
    }

    case 'sellerResults': {
      const additions: Message[] = action.entries.map((entry, index) => ({
        kind: 'seller',
        id: `${action.idPrefix}-${index}`,
        toolName: action.toolName,
        sellerName: entry.seller_name ?? entry.seller_id ?? '',
        payload: 'error' in entry && entry.error !== undefined ? { error: entry.error } : entry.response,
      }));
      // Inserted before the agent bubble when one already exists, so the seller answers the agent is
      // summarising appear above the summary rather than after it.
      const agentIndex = state.messages.findIndex((m) => m.kind === 'agent' && m.streaming);
      if (agentIndex === -1) {
        return { ...state, messages: [...state.messages, ...additions] };
      }
      return {
        ...state,
        messages: [
          ...state.messages.slice(0, agentIndex),
          ...additions,
          ...state.messages.slice(agentIndex),
        ],
      };
    }

    case 'note':
      return {
        ...state,
        messages: [
          ...state.messages,
          { kind: 'note', id: action.id, label: action.label, text: action.text },
        ],
      };

    case 'turnFailed': {
      // Whatever text arrived stays: it is something the agent actually said, and replacing it with
      // the error would discard it.
      const settled = state.messages.map((m) =>
        m.kind === 'agent' && m.id === action.id ? { ...m, streaming: false } : m,
      );
      return {
        messages: [
          ...settled,
          { kind: 'note', id: `${action.id}-error`, label: 'Turn failed', text: action.message },
        ],
        pending: false,
        error: action.message,
      };
    }

    case 'turnSettled':
      return {
        ...state,
        messages: state.messages.map((m) =>
          m.kind === 'agent' && m.streaming ? { ...m, streaming: false } : m,
        ),
        pending: false,
      };

    case 'loadHistory':
      // The recorded bubbles become the live transcript. Not pending and not errored: those
      // describe a turn in flight, and resuming starts no turn — the reader sends the next one.
      return { messages: [...action.messages], pending: false, error: null };

    case 'reset':
      return initialConversation;
  }
}
