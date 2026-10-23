import { describe, expect, it } from 'vitest';

import type { SessionStep } from '../types';
import {
  exchangesForTool,
  highestStepIndex,
  isInFlight,
  latestExchangeForTool,
  pairExchanges,
  readListTruncation,
  readTruncation,
} from './exchanges';

function call(index: number, toolName: string, callId: string, input?: unknown): SessionStep {
  return {
    step_index: index,
    step_type: 'tool_call',
    content: { toolName, callId, ...(input === undefined ? {} : { input }) },
  };
}

function result(
  index: number,
  callId: string,
  status: 'success' | 'error',
  output: unknown,
  durationMs?: number,
): SessionStep {
  return {
    step_index: index,
    step_type: 'tool_result',
    content: {
      callId,
      status,
      output,
      ...(durationMs === undefined ? {} : { durationMs }),
    },
  };
}

describe('pairExchanges', () => {
  it('matches a call to its result by callId', () => {
    const exchanges = pairExchanges([
      call(0, 'adcp_get_products', 'c1'),
      result(1, 'c1', 'success', { task: 'adcp_get_products' }, 1200),
    ]);
    expect(exchanges).toHaveLength(1);
    expect(exchanges[0]?.toolName).toBe('adcp_get_products');
    expect(exchanges[0]?.result?.status).toBe('success');
    expect(exchanges[0]?.result?.durationMs).toBe(1200);
  });

  it('treats a call with no result as in flight, not as a failure', () => {
    // A fan-out across five sellers takes seconds. Reporting this as an error would flash a failure
    // on the way to every success.
    const exchanges = pairExchanges([call(0, 'adcp_get_products', 'c1')]);
    expect(exchanges).toHaveLength(1);
    expect(isInFlight(exchanges[0]!)).toBe(true);
    expect(exchanges[0]?.result).toBeUndefined();
  });

  it('orders by step_index, not by array order', () => {
    const exchanges = pairExchanges([
      call(4, 'adcp_list_creative_formats', 'c2'),
      call(0, 'adcp_get_products', 'c1'),
      result(1, 'c1', 'success', {}),
      result(5, 'c2', 'success', {}),
    ]);
    expect(exchanges.map((e) => e.toolName)).toEqual(['adcp_get_products', 'adcp_list_creative_formats']);
  });

  it('ignores a result with no preceding call rather than synthesising one', () => {
    // A synthesised call to hang an orphan result from would assert something never recorded.
    const exchanges = pairExchanges([result(3, 'ghost', 'success', {})]);
    expect(exchanges).toEqual([]);
  });

  it('ignores a call with no tool name', () => {
    const exchanges = pairExchanges([
      { step_index: 0, step_type: 'tool_call', content: { callId: 'c1' } },
    ]);
    expect(exchanges).toEqual([]);
  });

  it('keeps a call that has no callId visible as in flight', () => {
    // It can never be matched to a result, but dropping it would hide a call that really happened.
    const exchanges = pairExchanges([
      { step_index: 7, step_type: 'tool_call', content: { toolName: 'adcp_get_products' } },
    ]);
    expect(exchanges).toHaveLength(1);
    expect(isInFlight(exchanges[0]!)).toBe(true);
  });

  it('ignores non-tool steps', () => {
    const exchanges = pairExchanges([
      { step_index: 0, step_type: 'incoming_request', content: { text: 'a brief' } },
      { step_index: 1, step_type: 'thought', content: { text: 'thinking' } },
      { step_index: 2, step_type: 'response', content: { text: 'done' } },
    ]);
    expect(exchanges).toEqual([]);
  });

  it('treats an unrecognised status as a failure rather than a success', () => {
    // Defaulting the other way is the one direction that lies.
    const exchanges = pairExchanges([
      call(0, 'adcp_get_products', 'c1'),
      { step_index: 1, step_type: 'tool_result', content: { callId: 'c1', status: 'weird' } },
    ]);
    expect(exchanges[0]?.result?.status).toBe('error');
  });

  it('pairs several calls to the same tool independently', () => {
    const exchanges = pairExchanges([
      call(0, 'adcp_check_governance', 'c1'),
      result(1, 'c1', 'success', { verdict: 'approved' }),
      call(2, 'adcp_check_governance', 'c2'),
      result(3, 'c2', 'success', { verdict: 'denied' }),
    ]);
    expect(exchangesForTool(exchanges, 'adcp_check_governance')).toHaveLength(2);
    expect(latestExchangeForTool(exchanges, 'adcp_check_governance')?.callId).toBe('c2');
  });

  it('returns undefined for a tool with no exchange', () => {
    expect(latestExchangeForTool([], 'adcp_get_products')).toBeUndefined();
  });
});

describe('readTruncation', () => {
  it('is silent when nothing was trimmed, so absence means complete', () => {
    expect(readTruncation(undefined)).toBeUndefined();
    expect(readTruncation({})).toBeUndefined();
  });

  it('reports a trimmed payload', () => {
    const note = readTruncation({ _recording: { truncated: true, original_bytes: 470310 } });
    expect(note).toBeDefined();
    expect(note?.reason).toContain('trimmed');
  });

  it('reports a step that could not be recorded at all', () => {
    const note = readTruncation({
      _not_recorded: { original_bytes: 999999, reason: 'over the item limit' },
    });
    expect(note?.notRecorded).toBe(true);
    expect(note?.reason).toBe('over the item limit');
  });

  it('does not claim truncation when _recording says it was not truncated', () => {
    expect(readTruncation({ _recording: { truncated: false } })).toBeUndefined();
  });
});

describe('readListTruncation', () => {
  it('reads kept versus total for a named list', () => {
    const note = readListTruncation(
      { products: [1, 2], products_recording_truncated: { kept: 2, total: 200 } },
      'products',
    );
    expect(note).toEqual({ kept: 2, total: 200 });
  });

  it('reads character counts for a shortened string', () => {
    const note = readListTruncation(
      { blob: 'abc', blob_recording_truncated: { kept_chars: 3, total_chars: 90000 } },
      'blob',
    );
    expect(note).toEqual({ keptChars: 3, totalChars: 90000 });
  });

  it('is silent when the list was not trimmed', () => {
    expect(readListTruncation({ products: [1] }, 'products')).toBeUndefined();
    expect(readListTruncation(null, 'products')).toBeUndefined();
    expect(readListTruncation('a string', 'products')).toBeUndefined();
  });
});

describe('highestStepIndex', () => {
  it('returns the floor for an empty list', () => {
    expect(highestStepIndex([], -1)).toBe(-1);
  });

  it('returns the highest index seen', () => {
    expect(highestStepIndex([call(0, 't', 'c'), call(9, 't', 'd'), call(4, 't', 'e')])).toBe(9);
  });

  it('never regresses below the floor', () => {
    expect(highestStepIndex([call(1, 't', 'c')], 5)).toBe(5);
  });
});
