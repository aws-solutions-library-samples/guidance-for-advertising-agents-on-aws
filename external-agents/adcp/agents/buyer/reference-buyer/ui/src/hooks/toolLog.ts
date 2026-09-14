/**
 * The tool activity log's state.
 *
 * One entry per tool call, plus one per sales agent that answered. A fan-out call produces a summary
 * on the original entry and a separate entry per seller, because a single blob containing every
 * seller's catalogue was unreadable and said nothing about which seller returned what.
 *
 * Statuses are kept distinct rather than collapsed to success/failure. In particular "the result was
 * not recorded" is not an error: the call may well have succeeded, and only the record of it is
 * missing. Reporting that as a failure would blame the tool for the recorder's limit.
 */

import { sellerEntryFailed, sellerEntryName, sellerEntryPayload, sellerEntrySummary } from '../lib/sellers';
import type { SellerEntry } from '../lib/types';

export type ToolStatus = 'pending' | 'success' | 'error' | 'not_recorded' | 'abandoned';

export interface ToolCall {
  id: string;
  toolName: string;
  status: ToolStatus;
  input?: unknown;
  /** The rendered body: raw JSON, a summary, or an explanation. */
  body?: string;
  durationMs?: number;
  /** Present for a fan-out result, one per sales agent that answered. */
  sellers?: ToolSellerRow[];
}

export interface ToolSellerRow {
  id: string;
  name: string;
  failed: boolean;
  summary: string;
  payload: unknown;
}

export interface ToolLogState {
  calls: ToolCall[];
}

export const initialToolLog: ToolLogState = { calls: [] };

export type ToolLogAction =
  | { type: 'started'; call: ToolCall }
  | {
      type: 'finished';
      callId: string;
      toolName?: string;
      output: unknown;
      durationMs?: number;
      ok: boolean;
      trimNote: string;
      entries?: SellerEntry[];
    }
  | { type: 'notRecorded'; callId: string; note: string }
  | { type: 'abandonPending' }
  | { type: 'clear' };

export function toolLogReducer(state: ToolLogState, action: ToolLogAction): ToolLogState {
  switch (action.type) {
    case 'started':
      return { calls: [...state.calls, action.call] };

    case 'finished': {
      const entries = action.entries;
      const body = entries
        ? summariseFanOut(entries, action.durationMs, action.trimNote)
        : formatPayload(action.output) + action.trimNote + durationSuffix(action.durationMs);

      const sellers: ToolSellerRow[] | undefined = entries?.map((entry, index) => ({
        id: `${action.callId}-seller-${index}`,
        name: sellerEntryName(entry),
        failed: sellerEntryFailed(entry),
        summary: sellerEntrySummary(entry),
        payload: sellerEntryPayload(entry),
      }));

      // When the result has per-seller entries, each entry's own outcome is authoritative; a blanket
      // status taken from the call would overstate or understate what happened.
      const status: ToolStatus = entries
        ? entries.some(sellerEntryFailed)
          ? 'error'
          : 'success'
        : action.ok
          ? 'success'
          : 'error';

      return {
        calls: upsert(state.calls, action.callId, action.toolName, (call) => ({
          ...call,
          status,
          body,
          ...(action.durationMs !== undefined ? { durationMs: action.durationMs } : {}),
          ...(sellers ? { sellers } : {}),
        })),
      };
    }

    case 'notRecorded':
      return {
        calls: upsert(state.calls, action.callId, undefined, (call) => ({
          ...call,
          status: 'not_recorded',
          body: action.note,
        })),
      };

    case 'abandonPending':
      return {
        calls: state.calls.map((call) =>
          call.status === 'pending'
            ? {
                ...call,
                status: 'abandoned',
                body:
                  'The turn ended with no result recorded for this call. The call itself may still ' +
                  'have succeeded, and only its recorded result is missing.',
              }
            : call,
        ),
      };

    case 'clear':
      return initialToolLog;
  }
}

/**
 * Applies an update to a call, creating it if the matching `tool_call` step was never seen.
 *
 * A result can arrive without its call: the baseline high-water mark may sit between the two, or the
 * call step may have been trimmed. Dropping the result would hide real tool activity.
 */
function upsert(
  calls: ToolCall[],
  callId: string,
  toolName: string | undefined,
  update: (call: ToolCall) => ToolCall,
): ToolCall[] {
  const index = calls.findIndex((c) => c.id === callId);
  if (index === -1) {
    return [...calls, update({ id: callId, toolName: toolName ?? 'tool', status: 'pending' })];
  }
  return calls.map((call, i) => (i === index ? update(call) : call));
}

/** A short precis of a fan-out, listing each seller and what it returned. */
function summariseFanOut(
  entries: SellerEntry[],
  durationMs: number | undefined,
  trimNote: string,
): string {
  const rows = entries.map((entry) => {
    const mark = sellerEntryFailed(entry) ? '✗' : '✓';
    const detail = sellerEntrySummary(entry);
    return `${mark} ${sellerEntryName(entry)}${detail ? `, ${detail}` : ''}`;
  });
  const heading = `${entries.length} sales agent${entries.length === 1 ? '' : 's'} queried:`;
  return [heading, ...rows].join('\n') + trimNote + durationSuffix(durationMs);
}

function durationSuffix(durationMs: number | undefined): string {
  return durationMs === undefined ? '' : `\n\n(${durationMs}ms)`;
}

function formatPayload(output: unknown): string {
  if (typeof output === 'string') return output;
  return JSON.stringify(output, null, 2);
}
