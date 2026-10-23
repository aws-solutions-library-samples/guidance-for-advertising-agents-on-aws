/**
 * Server-sent event parsing, separated from the fetch that produces the bytes.
 *
 * The vanilla UI inlined this loop inside `postInvoke`, which meant the framing rules could only
 * be exercised by making a real network call. Pulling it out costs nothing and makes the parts
 * that are easy to get wrong directly testable: a chunk boundary landing mid-event, an event
 * split across two reads, a `data:` line that is not JSON.
 */

/**
 * How a producer separates events.
 *
 * Both are needed because the two streams this UI reads are framed differently, and using the wrong
 * one is not a cosmetic mismatch:
 *
 *   'blank-line' - our own runtime's `/invocations` responses, separated by a blank line.
 *   'line'       - A2A's `message/stream`, which is line-oriented.
 *
 * A line-framed stream fed to the blank-line parser stalls: no blank line ever arrives, so events
 * accumulate in the buffer and nothing is emitted until the stream closes. The vanilla UI avoided
 * this by splitting on newlines for A2A specifically.
 *
 * Line framing also happens to read a blank-line stream correctly, since each payload still sits on
 * its own `data:` line and the empty lines are skipped. It is not used for both only because it
 * would split a multi-line `data:` field into separate events. Neither producer here emits one, but
 * the distinction is cheap to keep and expensive to rediscover.
 */
export type SseFraming = 'blank-line' | 'line';

/** Accumulates bytes and yields complete `data:` payloads as they become available. */
export class SseBuffer {
  private buffer = '';
  private readonly separator: string;

  constructor(framing: SseFraming = 'blank-line') {
    this.separator = framing === 'line' ? '\n' : '\n\n';
  }

  /**
   * Feeds a chunk in and returns the event payloads completed by it.
   *
   * A trailing fragment is retained rather than parsed: splitting on the separator and processing
   * every piece would parse a half-received event as though it were whole.
   */
  push(chunk: string): string[] {
    this.buffer += chunk;
    const pieces = this.buffer.split(this.separator);
    // The last piece is either an incomplete event or an empty string; keep it for the next feed.
    this.buffer = pieces.pop() ?? '';
    const out: string[] = [];
    for (const piece of pieces) {
      const payload = dataPayload(piece);
      if (payload !== null) out.push(payload);
    }
    return out;
  }

  /**
   * Whatever is left once the stream ends.
   *
   * A well-behaved producer ends with a blank line, but a stream that closes straight after its
   * last event would otherwise drop it, which reads as a missing response rather than a framing
   * detail.
   */
  flush(): string[] {
    const remaining = this.buffer;
    this.buffer = '';
    const payload = dataPayload(remaining);
    return payload === null ? [] : [payload];
  }
}

/** The `data:` payload of one event block, or null when the block carries none. */
function dataPayload(block: string): string | null {
  const trimmed = block.trim();
  if (!trimmed.startsWith('data:')) return null;
  const payload = trimmed.slice(5).trim();
  return payload === '' ? null : payload;
}

/**
 * Reads an SSE response body and returns the parsed JSON events.
 *
 * Unparsable payloads are skipped rather than thrown on: one malformed event in a stream should
 * not discard the events that did arrive intact. Callers that need to know something was dropped
 * can pass `onSkipped`, because silently swallowing every parse failure is how a truncated
 * response comes to look like an empty one.
 */
export async function readSseJson<T>(
  body: ReadableStream<Uint8Array>,
  onSkipped?: (raw: string, error: unknown) => void,
  framing: SseFraming = 'blank-line',
): Promise<T[]> {
  const events: T[] = [];
  const sse = new SseBuffer(framing);
  const reader = body.getReader();
  const decoder = new TextDecoder();

  const take = (payloads: string[]) => {
    for (const raw of payloads) {
      try {
        events.push(JSON.parse(raw) as T);
      } catch (error) {
        onSkipped?.(raw, error);
      }
    }
  };

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      // `stream: true` so a multi-byte character split across two chunks is not mangled.
      take(sse.push(decoder.decode(value, { stream: true })));
    }
    take(sse.push(decoder.decode()));
    take(sse.flush());
  } finally {
    // Releases the lock even if the caller abandons the read, so the body can be cancelled.
    reader.releaseLock();
  }
  return events;
}

/**
 * Reads an SSE body and hands each parsed event to a callback as it arrives.
 *
 * The streaming counterpart to `readSseJson`, which collects everything first. A chat turn needs
 * text on screen while the model is still producing it, so waiting for the stream to finish is not
 * an option there.
 *
 * `signal` aborts the read. Without it, abandoning a turn leaves the reader running and its events
 * still being delivered to a caller that has moved on.
 */
export async function streamSseJson<T>(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: T) => void,
  options: {
    framing?: SseFraming;
    signal?: AbortSignal;
    onSkipped?: (raw: string, error: unknown) => void;
  } = {},
): Promise<void> {
  const { framing = 'blank-line', signal, onSkipped } = options;
  const sse = new SseBuffer(framing);
  const reader = body.getReader();
  const decoder = new TextDecoder();

  const deliver = (payloads: string[]) => {
    for (const raw of payloads) {
      let parsed: T;
      try {
        parsed = JSON.parse(raw) as T;
      } catch (error) {
        onSkipped?.(raw, error);
        continue;
      }
      onEvent(parsed);
    }
  };

  try {
    for (;;) {
      if (signal?.aborted) break;
      const { done, value } = await reader.read();
      if (done) break;
      deliver(sse.push(decoder.decode(value, { stream: true })));
    }
    if (!signal?.aborted) {
      deliver(sse.push(decoder.decode()));
      deliver(sse.flush());
    }
  } finally {
    reader.releaseLock();
  }
}
