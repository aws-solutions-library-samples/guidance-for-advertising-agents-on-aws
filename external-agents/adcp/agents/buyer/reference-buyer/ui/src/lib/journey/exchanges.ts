/**
 * Pairing recorded steps into tool exchanges.
 *
 * A session records a `tool_call` and, later, a `tool_result` carrying the same `callId`. Everything
 * the journey shows comes from those pairs, so this is where ordering and in-flight handling are
 * decided.
 *
 * Two rules matter more than the rest:
 *
 *   Ordering is by `step_index`, never by `timestamp`. `step_index` comes from an atomic counter on
 *   the session record and is the authoritative sequence. `timestamp` is int epoch milliseconds
 *   while `SessionStep` declares it `string` (a latent mismatch tracked separately), and it is not
 *   the ordering key regardless.
 *
 *   A call with no result is IN FLIGHT, not failed. A fan-out across five sellers routinely takes
 *   seconds, so reporting a missing result as an error would flash a failure on the way to every
 *   success.
 */

import type { SessionStep, StepContent } from '../types';
import type { ToolExchange, TruncationNote } from './types';
import { matchesToolName } from './toolNames';

/**
 * The recorder's markers, read straight off the step.
 *
 * Returns undefined when nothing was trimmed, so the absence of a note means "complete", not
 * "unknown". A truncation note travels WITH data rather than in place of it: "5 products" and
 * "5 of 200 recorded" are different claims and only one of them is true.
 */
export function readTruncation(content: StepContent | undefined): TruncationNote | undefined {
  if (!content) return undefined;

  const notRecorded = content._not_recorded;
  if (notRecorded) {
    const note: TruncationNote = {
      notRecorded: true,
      ...(typeof notRecorded.reason === 'string' ? { reason: notRecorded.reason } : {}),
    };
    return note;
  }

  const recording = content._recording;
  if (recording?.truncated) {
    return { reason: 'Payload exceeded the per-record limit and was trimmed.' };
  }
  return undefined;
}

/**
 * Truncation reported on a named list inside a payload, e.g. `products_recording_truncated`.
 *
 * Separate from `readTruncation` because the recorder writes list-level counts as siblings of the
 * list, not on the step.
 */
export function readListTruncation(
  container: unknown,
  listKey: string,
): TruncationNote | undefined {
  if (!container || typeof container !== 'object') return undefined;
  const marker = (container as Record<string, unknown>)[`${listKey}_recording_truncated`];
  if (!marker || typeof marker !== 'object') return undefined;
  const record = marker as Record<string, unknown>;
  const kept = Number(record.kept);
  const total = Number(record.total);
  const keptChars = Number(record.kept_chars);
  const totalChars = Number(record.total_chars);
  const note: TruncationNote = {
    ...(Number.isFinite(kept) ? { kept } : {}),
    ...(Number.isFinite(total) ? { total } : {}),
    ...(Number.isFinite(keptChars) ? { keptChars } : {}),
    ...(Number.isFinite(totalChars) ? { totalChars } : {}),
  };
  return Object.keys(note).length > 0 ? note : undefined;
}

function statusOf(content: StepContent | undefined): 'success' | 'error' {
  // Anything that is not explicitly "success" is treated as a failure. Defaulting the other way
  // would turn an unrecognised status into a success, which is the one direction that lies.
  return content?.status === 'success' ? 'success' : 'error';
}

/**
 * Match `tool_call` steps to their `tool_result` steps.
 *
 * Ordered by `step_index` ascending, so the returned list is the order the calls were made. A result
 * with no preceding call is ignored: it cannot be placed, and a synthesised call to hang it from
 * would assert something about the session that was never recorded.
 */
export function pairExchanges(steps: readonly SessionStep[]): ToolExchange[] {
  const ordered = [...steps].sort((a, b) => a.step_index - b.step_index);

  const byCallId = new Map<string, ToolExchange>();
  const order: string[] = [];

  for (const step of ordered) {
    const content = step.content;
    if (step.step_type === 'tool_call') {
      const toolName = content?.toolName ?? content?.name;
      if (typeof toolName !== 'string' || toolName.length === 0) continue;
      // A call with no callId cannot be matched to a result. Key it by index so it still appears as
      // in-flight rather than vanishing from the journey.
      const callId = content?.callId ?? `#${step.step_index}`;
      const truncation = readTruncation(content);
      const exchange: ToolExchange = {
        callId,
        toolName,
        input: content?.input,
        callIndex: step.step_index,
        ...(truncation ? { truncation } : {}),
      };
      byCallId.set(callId, exchange);
      order.push(callId);
      continue;
    }

    if (step.step_type === 'tool_result') {
      const callId = content?.callId;
      if (typeof callId !== 'string') continue;
      const existing = byCallId.get(callId);
      if (!existing) continue;
      const durationMs = Number(content?.durationMs);
      const truncation = readTruncation(content) ?? existing.truncation;
      byCallId.set(callId, {
        ...existing,
        ...(truncation ? { truncation } : {}),
        result: {
          output: content?.output,
          status: statusOf(content),
          ...(Number.isFinite(durationMs) ? { durationMs } : {}),
          index: step.step_index,
        },
      });
    }
  }

  return order.flatMap((callId) => {
    const exchange = byCallId.get(callId);
    return exchange ? [exchange] : [];
  });
}

/** Exchanges for one tool name, in call order. */
export function exchangesForTool(
  exchanges: readonly ToolExchange[],
  toolName: string,
): ToolExchange[] {
  return exchanges.filter((exchange) => matchesToolName(exchange.toolName, toolName));
}

/** The last exchange for a tool, which is the one a panel shows. */
export function latestExchangeForTool(
  exchanges: readonly ToolExchange[],
  toolName: string,
): ToolExchange | undefined {
  const matching = exchangesForTool(exchanges, toolName);
  return matching[matching.length - 1];
}

/** True when the call has been made and no result has landed yet. */
export function isInFlight(exchange: ToolExchange): boolean {
  return exchange.result === undefined;
}

/** The highest `step_index` seen, for polling high-water marks. */
export function highestStepIndex(steps: readonly SessionStep[], floor = -1): number {
  return steps.reduce(
    (highest, step) => (Number.isFinite(step.step_index) ? Math.max(highest, step.step_index) : highest),
    floor,
  );
}
