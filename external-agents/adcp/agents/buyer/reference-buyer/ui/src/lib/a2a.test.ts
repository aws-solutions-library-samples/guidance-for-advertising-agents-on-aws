import { afterEach, describe, expect, it, vi } from 'vitest';

import { normalizeTurnEvents, streamAgentTurn } from './a2a';
import type { ChatTarget, TurnEvent } from './a2a';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('normalizeTurnEvents (direct A2A)', () => {
  it('extracts text from artifact parts, which is where a reply actually lives', () => {
    const events = normalizeTurnEvents(
      { result: { artifact: { parts: [{ kind: 'text', text: 'hello ' }, { text: 'world' }] } } },
      false,
    );
    expect(events).toEqual([
      { type: 'text', data: 'hello ' },
      { type: 'text', data: 'world' },
    ]);
  });

  it('accepts a payload with no jsonrpc result wrapper', () => {
    expect(normalizeTurnEvents({ artifact: { parts: [{ text: 'hi' }] } }, false)).toEqual([
      { type: 'text', data: 'hi' },
    ]);
  });

  it('turns a jsonrpc error into an error event', () => {
    expect(normalizeTurnEvents({ error: 'boom' }, false)).toEqual([
      { type: 'error', message: 'boom' },
    ]);
  });

  it('serialises a structured error rather than losing it', () => {
    const [event] = normalizeTurnEvents({ error: { code: -32000, message: 'bad' } }, false);
    expect(event?.type).toBe('error');
    expect(event && 'message' in event ? event.message : '').toContain('-32000');
  });

  it('ignores frames carrying nothing to render, which A2A sends routinely', () => {
    // Status and housekeeping frames are normal, not failures.
    expect(normalizeTurnEvents({ result: { status: { state: 'working' } } }, false)).toEqual([]);
    expect(normalizeTurnEvents({ result: { artifact: {} } }, false)).toEqual([]);
    expect(normalizeTurnEvents({}, false)).toEqual([]);
    expect(normalizeTurnEvents(null, false)).toEqual([]);
  });

  it('skips empty text parts so they cannot append nothing', () => {
    expect(
      normalizeTurnEvents({ result: { artifact: { parts: [{ text: '' }, { text: 'x' }] } } }, false),
    ).toEqual([{ type: 'text', data: 'x' }]);
  });

  it('skips non-text parts without discarding the text ones beside them', () => {
    expect(
      normalizeTurnEvents(
        { result: { artifact: { parts: [{ kind: 'data', data: { a: 1 } }, { text: 'x' }] } } },
        false,
      ),
    ).toEqual([{ type: 'text', data: 'x' }]);
  });
});

describe('normalizeTurnEvents (proxied)', () => {
  it('passes an already-normalised event straight through', () => {
    expect(normalizeTurnEvents({ type: 'text', data: 'hi' }, true)).toEqual([
      { type: 'text', data: 'hi' },
    ]);
  });

  it('drops an empty payload', () => {
    expect(normalizeTurnEvents(null, true)).toEqual([]);
  });
});

/** A response whose body streams the given chunks. */
function streamingResponse(chunks: string[], ok = true, status = 200) {
  const encoder = new TextEncoder();
  return {
    ok,
    status,
    body: new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
        controller.close();
      },
    }),
    text: async () => chunks.join(''),
  };
}

const direct: ChatTarget = { id: 'a2a', name: 'Buyer A2A', url: 'https://example.test/a2a' };

describe('streamAgentTurn', () => {
  it('reads a line-framed A2A stream, which blank-line framing would stall on', async () => {
    // The framing difference is the point: A2A never sends a blank line, so a blank-line parser
    // emits nothing until the stream closes.
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        streamingResponse([
          'data: {"result":{"artifact":{"parts":[{"text":"one "}]}}}\n',
          'data: {"result":{"artifact":{"parts":[{"text":"two"}]}}}\n',
        ]),
      ),
    );
    const events: TurnEvent[] = [];
    await streamAgentTurn({
      target: direct,
      prompt: 'hi',
      sessionId: 'x'.repeat(33),
      authHeader: 'Bearer t',
      onEvent: (e) => events.push(e),
    });
    expect(events).toEqual([
      { type: 'text', data: 'one ' },
      { type: 'text', data: 'two' },
    ]);
  });

  it('sends the session id as the A2A contextId so turns share one conversation', async () => {
    const fetchMock = vi.fn().mockResolvedValue(streamingResponse([]));
    vi.stubGlobal('fetch', fetchMock);
    const sessionId = 'adcp-buyer-agent-ui-1234567890123456';
    await streamAgentTurn({
      target: direct,
      prompt: 'hi',
      sessionId,
      authHeader: 'Bearer t',
      onEvent: () => {},
    });
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(String(init.body)) as {
      params: { message: { contextId: string } };
    };
    expect(body.params.message.contextId).toBe(sessionId);
    expect((init.headers as Record<string, string>)['X-Amzn-Bedrock-AgentCore-Runtime-Session-Id']).toBe(
      sessionId,
    );
  });

  it('does not pin a seller, leaving that to the agent', async () => {
    const fetchMock = vi.fn().mockResolvedValue(streamingResponse([]));
    vi.stubGlobal('fetch', fetchMock);
    await streamAgentTurn({
      target: direct,
      prompt: 'hi',
      sessionId: 'x'.repeat(33),
      authHeader: 'Bearer t',
      onEvent: () => {},
    });
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(init.body)).not.toContain('sellerAgentId');
  });

  it('posts a proxied turn to our runtime as an a2a_chat action', async () => {
    const fetchMock = vi.fn().mockResolvedValue(streamingResponse([]));
    vi.stubGlobal('fetch', fetchMock);
    await streamAgentTurn({
      target: { id: 'external-seller', name: 'Third-Party Seller', requires_proxy: true },
      prompt: 'hi',
      sessionId: 'x'.repeat(33),
      authHeader: 'Bearer t',
      runtimeInvokeUrl: 'https://runtime.test/invocations',
      onEvent: () => {},
    });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('https://runtime.test/invocations');
    expect(JSON.parse(String(init.body))).toMatchObject({
      action: 'a2a_chat',
      agent_id: 'external-seller',
    });
  });

  it('names the agent when it has no endpoint, rather than failing obscurely', async () => {
    await expect(
      streamAgentTurn({
        target: { id: 'ghost', name: 'Unbuilt Agent' },
        prompt: 'hi',
        sessionId: 'x'.repeat(33),
        authHeader: 'Bearer t',
        onEvent: () => {},
      }),
    ).rejects.toThrow(/Unbuilt Agent.*no reachable endpoint/s);
  });

  it('includes the response body when the request is rejected', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(streamingResponse(['Missing Authorization header'], false, 401)),
    );
    await expect(
      streamAgentTurn({
        target: direct,
        prompt: 'hi',
        sessionId: 'x'.repeat(33),
        authHeader: 'Bearer t',
        onEvent: () => {},
      }),
    ).rejects.toThrow(/HTTP 401.*Missing Authorization header/s);
  });

  it('reports a mid-stream error as an event, since part of the reply may already be shown', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        streamingResponse([
          'data: {"result":{"artifact":{"parts":[{"text":"partial"}]}}}\n',
          'data: {"error":{"message":"upstream failed"}}\n',
        ]),
      ),
    );
    const events: TurnEvent[] = [];
    await streamAgentTurn({
      target: direct,
      prompt: 'hi',
      sessionId: 'x'.repeat(33),
      authHeader: 'Bearer t',
      onEvent: (e) => events.push(e),
    });
    expect(events.map((e) => e.type)).toEqual(['text', 'error']);
  });

  it('stops delivering events once aborted', async () => {
    const controller = new AbortController();
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(() =>
        Promise.resolve(
          streamingResponse([
            'data: {"result":{"artifact":{"parts":[{"text":"a"}]}}}\n',
            'data: {"result":{"artifact":{"parts":[{"text":"b"}]}}}\n',
          ]),
        ),
      ),
    );
    const events: TurnEvent[] = [];
    await streamAgentTurn({
      target: direct,
      prompt: 'hi',
      sessionId: 'x'.repeat(33),
      authHeader: 'Bearer t',
      signal: controller.signal,
      onEvent: (e) => {
        events.push(e);
        controller.abort();
      },
    });
    // Abandoning a turn must not keep feeding a caller that has moved on.
    expect(events).toHaveLength(1);
  });
});
