/**
 * Labels and limits for the recorded-session views.
 *
 * The recurring rule: a record written by an agent that did not report its identity is labelled
 * "unknown agent" rather than attributed to the buyer agent by assumption, and a status the recorder
 * did not write is "unknown".
 */

import type { SessionMeta } from './types';

/** How many sessions the header control itself carries. Beyond a handful it stops being glanceable. */
export const SESSION_DROPDOWN_LIMIT = 4;

/** How many the poll fetches. The dropdown shows the newest few; the panel shows all of them. */
export const SESSION_LIST_LIMIT = 50;

/** Sentinel for the dropdown's route to the full list. Not a session id, so it must be intercepted. */
export const VIEW_ALL_VALUE = '__view_all__';

/**
 * The agent whose sessions the session views show.
 *
 * Same value as `BUYER_A2A_AGENT_ID` in `agents_registry.py`, which the A2A runtime writes into every
 * META record it starts. The HTTP runtime writes `buyer-http` and each seller writes its own id into
 * the same table, so this is the field that separates the A2A buyer's journeys from every other
 * recorder's records.
 */
export const BUYER_A2A_AGENT_ID = 'buyer-a2a';

export function shortSessionId(id: string | null | undefined): string {
  if (!id) return '';
  return id.length > 16 ? `${id.slice(0, 8)}…${id.slice(-4)}` : id;
}

/**
 * Which agent ran the session.
 *
 * With several agents recording into one store this is the first thing a reader needs, and it is not
 * the same question as who invoked it.
 */
export function agentLabelFor(session: SessionMeta): string {
  return session.agent_name || session.agent_id || 'unknown agent';
}

export function invokerLabelFor(session: SessionMeta): string {
  return session.invoker || 'Unknown invoker';
}

/**
 * How much of the brief a session row carries.
 *
 * Long enough that two sessions about different campaigns read differently, short enough that the
 * row stays one glanceable line.
 */
export const SESSION_BRIEF_MAX_CHARS = 72;

/**
 * The brief this session opened with, for telling one session from another.
 *
 * Agent name and session id do not distinguish two sessions run by the same agent, which is what a
 * list of them mostly is — so the brief is the field that actually separates them.
 *
 * Null when the record carries none, so the row shows nothing at all rather than empty quotes or a
 * placeholder. `request_preview` is the recorder's own preview of the opening request and may already
 * have been shortened by it; shortening it further is marked with an ellipsis, so a truncated brief
 * never reads as the whole of what was asked. Whitespace is collapsed because the row is one line;
 * the words themselves are untouched.
 */
export function sessionBriefLabel(
  session: SessionMeta,
  maxChars: number = SESSION_BRIEF_MAX_CHARS,
): string | null {
  const preview = session.request_preview?.replace(/\s+/g, ' ').trim();
  if (!preview) return null;
  if (preview.length <= maxChars) return preview;
  return `${preview.slice(0, Math.max(0, maxChars - 1)).trimEnd()}\u2026`;
}

/** A one-line label for the dropdown. */
export function sessionOptionLabel(session: SessionMeta): string {
  const preview = session.request_preview ?? '';
  const truncated = `${preview.slice(0, 32)}${preview.length > 32 ? '…' : ''}`;
  return `${agentLabelFor(session)} · ${invokerLabelFor(session)} — ${truncated} [${session.status}]`;
}

/**
 * When the session started, in the reader's locale.
 *
 * Today's sessions show a time and older ones a full date, since a bare time is ambiguous once a
 * record is more than a day old. An unparsable timestamp is reported as unknown rather than as the
 * epoch.
 */
export function sessionStartedLabel(session: SessionMeta): string {
  if (!session.started_at) return 'unknown';
  const when = new Date(session.started_at);
  if (Number.isNaN(when.getTime())) return 'unknown';
  return when.toDateString() === new Date().toDateString()
    ? when.toLocaleTimeString()
    : when.toLocaleString();
}

export function stepTimestampLabel(timestamp: string | undefined): string {
  if (!timestamp) return '';
  const when = new Date(timestamp);
  return Number.isNaN(when.getTime()) ? '' : when.toLocaleTimeString();
}

export type SessionStatusTone = 'live' | 'completed' | 'error' | 'unknown';

/**
 * The recorded status, as recorded.
 *
 * Anything the recorder did not write is 'unknown'. Defaulting an unrecognised status to completed
 * would report a conversation as finished on no evidence.
 */
export function sessionStatusTone(status: string | undefined): {
  tone: SessionStatusTone;
  label: string;
} {
  switch (status) {
    case 'active':
      return { tone: 'live', label: 'live' };
    case 'completed':
      return { tone: 'completed', label: 'completed' };
    case 'error':
      return { tone: 'error', label: 'error' };
    default:
      return { tone: 'unknown', label: status || 'unknown' };
  }
}

/**
 * Whether a record is a standalone tool call rather than a conversation.
 *
 * These are real records, but they are not conversations: a seller call arriving without a buyer
 * correlation id is recorded as its own session. One verification run produced 95 of them, enough to
 * fill the entire page the dropdown reads from and push every real conversation off it.
 */
export function isUncorrelatedToolCall(sessionId: string | undefined): boolean {
  return typeof sessionId === 'string' && sessionId.startsWith('adcp-seller-uncorrelated-');
}

/**
 * Whether one agent wrote this record.
 *
 * Several writers share the table. One buyer invocation over A2A produces all of these, confirmed
 * against the live table:
 *
 *     5c6d6c96168d4eb2ba70abb29aea3fe5f            agent_id=buyer-a2a        <- the buyer session
 *     adcp-seller-reference-5c6d6c96168d4eb2ba70…  agent_id=reference-seller
 *     adcp-seller-triton-5c6d6c96168d4eb2ba70…     agent_id=triton-seller
 *     adcp-seller-uncorrelated-108f50e6-…          agent_id=gotham-seller
 *
 * The seller ids embed the buyer session id, so they are children of one journey rather than peers of
 * it. Listing them beside their own parent makes the list several times longer than the number of
 * real journeys and pushes the newest buyer session off index 0, which auto-switch reads.
 *
 * Matched on `agent_id`, replacing an earlier test on the `adcp-seller-` id prefix. The prefix could
 * only tell seller recordings from everything else, so the HTTP runtime's own sessions passed it — the
 * reason a list asked to hold A2A journeys carried `AdCP Buyer Agent (HTTP)` rows. A record whose
 * writer reported no `agent_id` fails this test rather than being counted as this agent's for not
 * looking like anyone else's.
 *
 * The list poll also asks the server for a single agent, so this is a second check on the same fact.
 * It is worth keeping: the UI and the runtimes deploy separately, and a new bundle served against a
 * runtime that does not yet read the parameter would otherwise show every agent's sessions again.
 */
export function isSessionForAgent(
  session: Pick<SessionMeta, 'agent_id'>,
  agentId: string,
): boolean {
  return session.agent_id === agentId;
}

/** The recency a session should be ordered by: last activity, falling back to when it started. */
export function sessionRecency(session: Pick<SessionMeta, 'updated_at' | 'started_at'>): string {
  return session.updated_at ?? session.started_at ?? '';
}

/**
 * Newest first, by last activity.
 *
 * Sorted here rather than trusting the order the server returned. The backend orders by its GSI sort
 * key, and its own docstring notes the consequence -- "a conversation in progress sorted below a newer
 * abandoned one". Auto-switch reads `sessions[0]` and treats it as the newest, so that field has to
 * mean newest by activity or the view follows the wrong session.
 *
 * ISO-8601 timestamps compare correctly as strings, so no date parsing is needed. Ties keep their
 * relative order, which keeps the list stable across polls instead of shuffling equal entries.
 */
export function sortByRecency<T extends Pick<SessionMeta, 'updated_at' | 'started_at'>>(
  sessions: readonly T[],
): T[] {
  return [...sessions].sort((a, b) => sessionRecency(b).localeCompare(sessionRecency(a)));
}
