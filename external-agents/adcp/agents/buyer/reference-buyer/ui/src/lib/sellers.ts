/**
 * Reading what a seller actually returned.
 *
 * Every function here reports only what is present in the payload. Nothing is inferred and nothing
 * is defaulted: an unrecognised shape gets no summary rather than a guessed one, and a trimmed list
 * says it was trimmed rather than presenting itself as the seller's complete answer.
 */

import type { SellerEntry, SellerResponse, StepContent } from './types';

/** The seller's display name, or a stand-in when it named none. */
export function sellerEntryName(entry: SellerEntry): string {
  return entry.seller_name || entry.seller_id || 'unknown seller';
}

/**
 * Whether an entry failed.
 *
 * Two distinct failures count: the call itself threw, or the seller answered with an AdCP/transport
 * error envelope. Both are real failures and neither should read as success.
 */
export function sellerEntryFailed(entry: SellerEntry): boolean {
  if ('error' in entry && entry.error !== undefined) return true;
  const response = entry.response;
  if (!response || typeof response !== 'object') return false;
  return 'mcp_error' in response || 'a2a_error' in response || 'adcp_error' in response;
}

/** The error envelope key present on a response, if any. */
function errorKey(response: SellerResponse): 'mcp_error' | 'a2a_error' | 'adcp_error' | null {
  if ('mcp_error' in response) return 'mcp_error';
  if ('a2a_error' in response) return 'a2a_error';
  if ('adcp_error' in response) return 'adcp_error';
  return null;
}

/**
 * A one-line factual precis of an entry.
 *
 * Counts come from the response's own arrays. A shape this does not recognise gets an empty string,
 * because a summary that guesses is worse than none.
 */
export function sellerEntrySummary(entry: SellerEntry): string {
  if ('error' in entry && entry.error !== undefined) return String(entry.error).slice(0, 80);

  const response = entry.response;
  if (!response || typeof response !== 'object') return '';

  const key = errorKey(response);
  if (key) return JSON.stringify(response[key]).slice(0, 80);

  if (Array.isArray(response.products)) {
    const shown = response.products.length;
    // The recorder states per list what it kept. Carrying that here stops "31 products" being read
    // as everything the seller sent when it actually sent 50.
    const total = Number(response.products_recording_truncated?.total);
    if (Number.isFinite(total) && total > shown) {
      return `${shown} of ${total} products recorded`;
    }
    return `${shown} product${shown === 1 ? '' : 's'}`;
  }
  if (Array.isArray(response.formats)) return `${response.formats.length} formats`;
  if (response.adcp) return 'capabilities returned';
  return '';
}

/** The payload to display for an entry: its error, or the seller's response. */
export function sellerEntryPayload(entry: SellerEntry): unknown {
  return 'error' in entry && entry.error !== undefined ? { error: entry.error } : entry.response;
}

/**
 * What the recorder trimmed, if anything, straight from the flag it wrote on the step.
 *
 * Silent when nothing was trimmed. Without this a shortened list reads as "that is all the seller
 * returned", which is a different claim from "that is all we stored".
 */
export function recordingTrimNote(content: StepContent | undefined): string {
  const info = content?._recording;
  if (!info?.truncated) return '';
  const bytes = Number(info.original_bytes);
  const size = Number.isFinite(bytes) ? `${bytes.toLocaleString()} bytes` : 'oversized';
  return (
    `\n\nOnly part of this result was recorded: the full payload was ${size}, over the ` +
    `per-record limit. Each trimmed list below carries its own "_recording_truncated" count of ` +
    `what was kept versus what the seller sent.`
  );
}

/**
 * A step the recorder could not store at all leaves a marker in its place, so the card can be
 * closed with the real reason instead of spinning for ever.
 */
export function notRecordedNote(content: StepContent | undefined): string {
  const info = content?._not_recorded;
  if (!info) return '';
  const bytes = Number(info.original_bytes);
  const size = Number.isFinite(bytes) ? ` (${bytes.toLocaleString()} bytes)` : '';
  return `This result was not recorded${size}: ${info.reason || 'reason not given'}`;
}

/**
 * The per-seller entries of a fan-out tool result, or null when the payload is not that shape.
 *
 * Recorded output arrives as a JSON string on the buyer side and as an object from the seller-side
 * recorder, so both are accepted. Null means "render this as it is" rather than pretending it has
 * per-seller structure.
 */
export function parseFanOut(output: unknown): SellerEntry[] | null {
  let parsed: unknown = output;
  if (typeof parsed === 'string') {
    try {
      parsed = JSON.parse(parsed);
    } catch {
      return null;
    }
  }
  if (!parsed || typeof parsed !== 'object') return null;
  const results = (parsed as { results?: unknown }).results;
  return Array.isArray(results) ? (results as SellerEntry[]) : null;
}

/** Parses recorded output to an object when it can, leaving strings as strings. */
export function parseOutput(output: unknown): unknown {
  if (typeof output !== 'string') return output;
  try {
    return JSON.parse(output);
  } catch {
    return output;
  }
}
