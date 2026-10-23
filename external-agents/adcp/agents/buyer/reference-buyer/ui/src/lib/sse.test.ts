import { describe, expect, it, vi } from 'vitest';

import { SseBuffer, readSseJson } from './sse';

/** A stream that hands out exactly the chunks given, to control where boundaries fall. */
function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

describe('SseBuffer', () => {
  it('yields a complete event', () => {
    expect(new SseBuffer().push('data: {"a":1}\n\n')).toEqual(['{"a":1}']);
  });

  it('yields several events from one chunk', () => {
    expect(new SseBuffer().push('data: 1\n\ndata: 2\n\ndata: 3\n\n')).toEqual(['1', '2', '3']);
  });

  it('withholds a partial event until it completes', () => {
    const sse = new SseBuffer();
    // The point of the class: a chunk boundary mid-event must not produce a half-parsed event.
    expect(sse.push('data: {"a":')).toEqual([]);
    expect(sse.push('1}\n\n')).toEqual(['{"a":1}']);
  });

  it('handles a boundary falling inside the separator', () => {
    const sse = new SseBuffer();
    expect(sse.push('data: 1\n')).toEqual([]);
    expect(sse.push('\ndata: 2\n\n')).toEqual(['1', '2']);
  });

  it('ignores blocks carrying no data field', () => {
    expect(new SseBuffer().push(': keep-alive\n\nevent: ping\n\ndata: 1\n\n')).toEqual(['1']);
  });

  it('ignores an empty data payload', () => {
    expect(new SseBuffer().push('data:\n\ndata: 1\n\n')).toEqual(['1']);
  });

  it('flush returns a trailing event that had no final blank line', () => {
    const sse = new SseBuffer();
    expect(sse.push('data: 1\n\ndata: 2')).toEqual(['1']);
    // Without flush this event would be dropped, which reads as a missing response rather than
    // as the framing detail it is.
    expect(sse.flush()).toEqual(['2']);
  });

  it('flush returns nothing when the buffer holds no event', () => {
    const sse = new SseBuffer();
    sse.push('data: 1\n\n');
    expect(sse.flush()).toEqual([]);
  });
});

describe('readSseJson', () => {
  it('parses events across arbitrary chunk boundaries', async () => {
    const events = await readSseJson<{ n: number }>(
      streamOf(['data: {"n":1}\n', '\ndata: {"n', '":2}\n\n']),
    );
    expect(events).toEqual([{ n: 1 }, { n: 2 }]);
  });

  it('keeps the intact events when one is malformed', async () => {
    const onSkipped = vi.fn();
    const events = await readSseJson<{ n: number }>(
      streamOf(['data: {"n":1}\n\ndata: not json\n\ndata: {"n":3}\n\n']),
      onSkipped,
    );
    // One bad event must not discard the ones that arrived intact.
    expect(events).toEqual([{ n: 1 }, { n: 3 }]);
    expect(onSkipped).toHaveBeenCalledTimes(1);
    expect(onSkipped.mock.calls[0]?.[0]).toBe('not json');
  });

  it('reports a skipped event rather than swallowing it silently', async () => {
    const onSkipped = vi.fn();
    await readSseJson(streamOf(['data: {oops\n\n']), onSkipped);
    expect(onSkipped).toHaveBeenCalledOnce();
  });

  it('returns a final event that arrived without a trailing blank line', async () => {
    const events = await readSseJson<{ n: number }>(streamOf(['data: {"n":9}']));
    expect(events).toEqual([{ n: 9 }]);
  });

  it('decodes a multi-byte character split across chunks', async () => {
    // Half of "é" in one chunk and half in the next. Without streaming decode this becomes U+FFFD
    // and the JSON parse fails, losing the event.
    const encoded = new TextEncoder().encode('data: {"s":"é"}\n\n');
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoded.slice(0, 12));
        controller.enqueue(encoded.slice(12));
        controller.close();
      },
    });
    expect(await readSseJson<{ s: string }>(stream)).toEqual([{ s: 'é' }]);
  });

  it('returns nothing for an empty stream', async () => {
    expect(await readSseJson(streamOf([]))).toEqual([]);
  });
});
