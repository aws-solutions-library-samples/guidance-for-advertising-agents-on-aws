import { describe, expect, it } from 'vitest';

import {
  notRecordedNote,
  parseFanOut,
  parseOutput,
  recordingTrimNote,
  sellerEntryFailed,
  sellerEntryName,
  sellerEntryPayload,
  sellerEntrySummary,
} from './sellers';
import type { SellerEntry } from './types';

describe('sellerEntryFailed', () => {
  it('treats a thrown call as failed', () => {
    expect(sellerEntryFailed({ error: 'connection refused' })).toBe(true);
  });

  it('treats each error envelope as failed, not just the transport ones', () => {
    for (const key of ['mcp_error', 'a2a_error', 'adcp_error'] as const) {
      expect(sellerEntryFailed({ response: { [key]: 'boom' } }), key).toBe(true);
    }
  });

  it('treats a real answer as succeeded', () => {
    expect(sellerEntryFailed({ response: { products: [] } })).toBe(false);
  });

  it('does not treat an empty product list as a failure', () => {
    // "No qualifying inventory" is an answer, not an error.
    expect(sellerEntryFailed({ response: { products: [] } })).toBe(false);
  });
});

describe('sellerEntrySummary', () => {
  it('counts products from the response own array', () => {
    expect(sellerEntrySummary({ response: { products: [{}, {}, {}] } })).toBe('3 products');
  });

  it('uses the singular for one product', () => {
    expect(sellerEntrySummary({ response: { products: [{}] } })).toBe('1 product');
  });

  it('says how many of how many when the recorder trimmed the list', () => {
    const entry: SellerEntry = {
      response: { products: [{}, {}], products_recording_truncated: { total: 50 } },
    };
    // "2 products" here would not match what the seller sent.
    expect(sellerEntrySummary(entry)).toBe('2 of 50 products recorded');
  });

  it('ignores a truncation marker that does not exceed what is shown', () => {
    expect(
      sellerEntrySummary({ response: { products: [{}, {}], products_recording_truncated: { total: 2 } } }),
    ).toBe('2 products');
  });

  it('reports zero products plainly', () => {
    expect(sellerEntrySummary({ response: { products: [] } })).toBe('0 products');
  });

  it('summarises formats and capabilities', () => {
    expect(sellerEntrySummary({ response: { formats: [{}, {}] } })).toBe('2 formats');
    expect(sellerEntrySummary({ response: { adcp: { version: '1' } } })).toBe(
      'capabilities returned',
    );
  });

  it('surfaces the error rather than a count', () => {
    expect(sellerEntrySummary({ error: 'timed out after 5s' })).toBe('timed out after 5s');
    expect(sellerEntrySummary({ response: { mcp_error: 'Unknown tool' } })).toContain('Unknown tool');
  });

  it('gives no summary for a shape it does not recognise, rather than guessing', () => {
    expect(sellerEntrySummary({ response: { something_else: true } })).toBe('');
    expect(sellerEntrySummary({})).toBe('');
  });

  it('truncates a long error instead of overflowing the line', () => {
    expect(sellerEntrySummary({ error: 'x'.repeat(200) })).toHaveLength(80);
  });
});

describe('sellerEntryName', () => {
  it('prefers the name, then the id, then says it is unknown', () => {
    expect(sellerEntryName({ seller_name: 'The Gotham Gazette', seller_id: 'gotham' })).toBe(
      'The Gotham Gazette',
    );
    expect(sellerEntryName({ seller_id: 'gotham' })).toBe('gotham');
    expect(sellerEntryName({})).toBe('unknown seller');
  });
});

describe('sellerEntryPayload', () => {
  it('shows the error for a failed call and the response otherwise', () => {
    expect(sellerEntryPayload({ error: 'nope' })).toEqual({ error: 'nope' });
    expect(sellerEntryPayload({ response: { products: [] } })).toEqual({ products: [] });
  });
});

describe('recordingTrimNote', () => {
  it('states the size when the recorder trimmed', () => {
    const note = recordingTrimNote({ _recording: { truncated: true, original_bytes: 123456 } });
    expect(note).toContain('123,456 bytes');
  });

  it('says nothing when nothing was trimmed', () => {
    expect(recordingTrimNote(undefined)).toBe('');
    expect(recordingTrimNote({})).toBe('');
    expect(recordingTrimNote({ _recording: { truncated: false } })).toBe('');
  });

  it('says oversized when the size is not reported', () => {
    expect(recordingTrimNote({ _recording: { truncated: true } })).toContain('oversized');
  });
});

describe('notRecordedNote', () => {
  it('gives the reason the step was not stored', () => {
    const note = notRecordedNote({ _not_recorded: { original_bytes: 900, reason: 'too large' } });
    expect(note).toContain('900 bytes');
    expect(note).toContain('too large');
  });

  it('does not claim a reason it was not given', () => {
    expect(notRecordedNote({ _not_recorded: {} })).toContain('reason not given');
  });

  it('says nothing for a step that was recorded', () => {
    expect(notRecordedNote({})).toBe('');
    expect(notRecordedNote(undefined)).toBe('');
  });
});

describe('parseFanOut', () => {
  it('reads entries from an object', () => {
    expect(parseFanOut({ results: [{ seller_id: 'a' }] })).toEqual([{ seller_id: 'a' }]);
  });

  it('reads entries from the JSON string form the buyer records', () => {
    expect(parseFanOut('{"results":[{"seller_id":"a"}]}')).toEqual([{ seller_id: 'a' }]);
  });

  it('returns null for a payload that is not a fan-out', () => {
    // Null means "render as it is" rather than pretending there is per-seller structure.
    expect(parseFanOut({ products: [] })).toBeNull();
    expect(parseFanOut('plain text')).toBeNull();
    expect(parseFanOut(null)).toBeNull();
    expect(parseFanOut({ results: 'not an array' })).toBeNull();
  });
});

describe('parseOutput', () => {
  it('parses JSON and leaves other text alone', () => {
    expect(parseOutput('{"a":1}')).toEqual({ a: 1 });
    expect(parseOutput('not json')).toBe('not json');
    expect(parseOutput({ a: 1 })).toEqual({ a: 1 });
  });
});
