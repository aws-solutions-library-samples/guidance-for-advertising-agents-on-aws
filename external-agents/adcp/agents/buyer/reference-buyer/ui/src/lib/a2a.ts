/**
 * Talking to a chat agent.
 *
 * Every target speaks A2A, reached one of two ways:
 *
 *   direct  - the browser POSTs `message/stream` to the agent's own endpoint with the signed-in
 *             user's token. AWS's invoke endpoint sends permissive CORS headers, which is what
 *             makes this possible without a proxy.
 *   proxied - the agent's credential is a server-side secret that must not reach a browser, so the
 *             turn goes to our own runtime's `a2a_chat` action, which relays it.
 *
 * Both are normalised to the same small event vocabulary, so the renderer does not care which path a
 * turn took.
 *
 * The stream carries text only. It carries no tool-call or tool-result detail, verified against the
 * deployed A2A runtime, which is why tool activity is read from recorded session steps instead of
 * from here.
 */

import { streamSseJson } from './sse';
import type { ChatAgent } from './types';

/** What a turn can report. Deliberately small: text, a failure, or nothing further. */
export type TurnEvent =
  | { type: 'text'; data: string }
  | { type: 'error'; message: string }
  | { type: 'done' }
  | { type: string; [key: string]: unknown };

/** A chat target, as the registry describes it. */
export interface ChatTarget extends ChatAgent {
  /** True when the turn must be relayed by our runtime rather than called directly. */
  requires_proxy?: boolean;
  /** True when this agent records reasoning steps we can read tool activity from. */
  records_sessions?: boolean;
}

export interface StreamTurnOptions {
  target: ChatTarget;
  prompt: string;
  /** The runtime session id, reused across turns so the agent's memory accumulates. */
  sessionId: string;
  authHeader: string;
  /** Where to send a proxied turn. Required only for proxied targets. */
  runtimeInvokeUrl?: string;
  onEvent: (event: TurnEvent) => void;
  signal?: AbortSignal;
}

/**
 * Runs one turn, delivering events as they arrive.
 *
 * Throws only for failures that stop the turn from starting at all (not signed in, no endpoint, a
 * non-OK response). A failure reported inside the stream arrives as an `error` event instead, since
 * by then some of the reply may already be on screen.
 */
export async function streamAgentTurn({
  target,
  prompt,
  sessionId,
  authHeader,
  runtimeInvokeUrl,
  onEvent,
  signal,
}: StreamTurnOptions): Promise<void> {
  const proxied = Boolean(target.requires_proxy);
  const url = proxied ? runtimeInvokeUrl : target.url;
  if (!url) {
    // Says which agent and why, because "registered but not deployed yet" is a normal state here
    // and is not the same as a broken request.
    throw new Error(
      `Agent "${target.name}" has no reachable endpoint configured. ` +
        'It may be registered but not deployed yet.',
    );
  }

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    Authorization: authHeader,
    'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': sessionId,
  };

  let body: unknown;
  if (proxied) {
    body = { action: 'a2a_chat', agent_id: target.id, prompt, invoker: 'Chat UI' };
  } else {
    headers['Accept'] = 'text/event-stream';
    body = {
      jsonrpc: '2.0',
      id: crypto.randomUUID(),
      method: 'message/stream',
      params: {
        message: {
          role: 'user',
          parts: [{ kind: 'text', text: prompt }],
          messageId: crypto.randomUUID(),
          kind: 'message',
          // A2A's conversation id, held stable across turns so the agent keeps one conversation.
          // The same value as the runtime session id, so the two views of "this conversation"
          // cannot drift apart.
          contextId: sessionId,
        },
        // No sellerAgentId: a buyer agent queries every sales agent it is configured with and
        // decides which inventory fits, which is what AdCP's own multi-agent client does. Pinning
        // one seller from the UI would be this client overriding the agent's job.
        metadata: { invokerAgent: 'Chat UI' },
      },
    };
  }

  const res = await fetch(url, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
    ...(signal ? { signal } : {}),
  });

  if (!res.ok || !res.body) {
    // The body usually explains the status; include a bounded slice of it rather than reporting a
    // bare number.
    const detail = await res.text().catch(() => '');
    throw new Error(`HTTP ${res.status}${detail ? `: ${detail.slice(0, 300)}` : ''}`);
  }

  await streamSseJson<unknown>(
    res.body,
    (parsed) => {
      for (const event of normalizeTurnEvents(parsed, proxied)) onEvent(event);
    },
    {
      // A2A's stream is line-oriented; our own runtime frames with a blank line. See SseFraming.
      framing: proxied ? 'blank-line' : 'line',
      ...(signal ? { signal } : {}),
    },
  );
}

/**
 * Translates one stream payload into this UI's event vocabulary.
 *
 * A proxied response already arrives in it and passes through. A direct A2A response is a JSONRPC
 * chunk whose text lives in artifact-update parts.
 *
 * Returns an empty list for a payload carrying nothing this UI renders, which is normal: A2A sends
 * status and housekeeping frames alongside the text.
 */
export function normalizeTurnEvents(parsed: unknown, proxied: boolean): TurnEvent[] {
  if (proxied) return parsed ? [parsed as TurnEvent] : [];

  if (parsed && typeof parsed === 'object' && 'error' in parsed) {
    const raw = (parsed as { error: unknown }).error;
    const message = typeof raw === 'string' ? raw : JSON.stringify(raw);
    return [{ type: 'error', message }];
  }

  const result =
    parsed && typeof parsed === 'object' && 'result' in parsed
      ? (parsed as { result: unknown }).result
      : parsed;
  if (!result || typeof result !== 'object') return [];

  const artifact = (result as { artifact?: { parts?: unknown[] } }).artifact;
  if (!artifact || !Array.isArray(artifact.parts)) return [];

  const events: TurnEvent[] = [];
  for (const part of artifact.parts) {
    if (part && typeof part === 'object') {
      const text = (part as { text?: unknown }).text;
      if (typeof text === 'string' && text !== '') {
        events.push({ type: 'text', data: text });
      }
    }
  }
  return events;
}
