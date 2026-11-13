/**
 * Turns AgentCore Memory events into a chat transcript.
 *
 * Pure functions with no Angular or SDK imports. `MemoryEvent` is the subset of
 * the SDK's `ListEvents` event that this mapping reads, so SDK events can be
 * passed in directly.
 */

export type HistoryRole = 'user' | 'assistant';

export interface HistoryEntry {
  role: HistoryRole;
  /** The agent whose memory actor held the event. */
  agentName: string;
  text: string;
  timestamp: Date;
}

export interface MemoryEventPayload {
  conversational?: {
    role?: string;
    content?: { text?: string };
  };
}

export interface MemoryEvent {
  eventId?: string;
  eventTimestamp?: Date | string | number;
  payload?: MemoryEventPayload[];
}

export interface AgentEvents {
  agentName: string;
  events: MemoryEvent[];
}

const USER_QUESTION_PREFIX = 'User Question: ';
const CONTEXT_SEPARATOR = '\n\nContext:';

/** The markers the runtime already uses to recognise stringified tool content. */
const TOOL_TEXT_MARKERS = ["'toolUse'", '"toolUse"', "'toolResult'", '"toolResult"', 'tooluse_', 'tool_use_id'];

/** Memory actor id for an agent, as `handler.py` builds it. */
export function toActorId(agentName: string): string {
  return (agentName || '').replace(/_/g, '-');
}

/**
 * The text the user typed, without the `User Question: ` prefix and the
 * `Context:` block the chat adds to the first message sent to an agent.
 */
export function cleanUserText(text: string): string {
  let cleaned = (text || '').trimStart();
  if (!cleaned.startsWith(USER_QUESTION_PREFIX)) {
    return cleaned.trim();
  }
  cleaned = cleaned.slice(USER_QUESTION_PREFIX.length);
  const contextStart = cleaned.indexOf(CONTEXT_SEPARATOR);
  if (contextStart >= 0) {
    cleaned = cleaned.slice(0, contextStart);
  }
  return cleaned.trim();
}

/** True for a tool call or tool result that an older writer stored as text. */
export function isToolText(text: string): boolean {
  return TOOL_TEXT_MARKERS.some(marker => (text || '').includes(marker));
}

/**
 * One entry per user or assistant payload, merged across agents oldest first.
 * Empty and tool text is dropped, and duplicates written by the older pair of
 * memory writers are removed (see `dropRepeatedRuns`). Agent text is kept
 * verbatim so `<visualization-data>` blocks reach the renderer unchanged.
 */
export function eventsToHistory(eventsByAgent: AgentEvents[]): HistoryEntry[] {
  const perAgent = (eventsByAgent || []).map(({ agentName, events }) =>
    dropRepeatedRuns(sortByTime(toEntries(agentName, events || [])))
  );
  return sortByTime(perAgent.flat());
}

function toEntries(agentName: string, events: MemoryEvent[]): HistoryEntry[] {
  const entries: HistoryEntry[] = [];
  for (const event of events) {
    const timestamp = toDate(event.eventTimestamp);
    for (const payload of event.payload || []) {
      const entry = toEntry(agentName, payload, timestamp);
      if (entry) {
        entries.push(entry);
      }
    }
  }
  return entries;
}

function toEntry(agentName: string, payload: MemoryEventPayload, timestamp: Date): HistoryEntry | null {
  const conversational = payload?.conversational;
  const role = toRole(conversational?.role);
  const rawText = conversational?.content?.text;
  if (!role || typeof rawText !== 'string' || isToolText(rawText)) {
    return null;
  }
  const text = role === 'user' ? cleanUserText(rawText) : rawText.trim();
  return text ? { role, agentName, text, timestamp } : null;
}

function toRole(role: string | undefined): HistoryRole | null {
  switch ((role || '').toUpperCase()) {
    case 'USER':
      return 'user';
    case 'ASSISTANT':
      return 'assistant';
    default:
      return null;
  }
}

function toDate(value: MemoryEvent['eventTimestamp']): Date {
  const date = value instanceof Date ? value : new Date(value ?? 0);
  return isNaN(date.getTime()) ? new Date(0) : date;
}

/** Stable: entries with equal times keep their input order. */
function sortByTime(entries: HistoryEntry[]): HistoryEntry[] {
  return [...entries].sort((a, b) => a.timestamp.getTime() - b.timestamp.getTime());
}

function sameEntry(a: HistoryEntry, b: HistoryEntry): boolean {
  return a.agentName === b.agentName && a.role === b.role && a.text === b.text;
}

/**
 * Removes a run of entries that repeats the run immediately before it, within
 * one agent's ordered entries. A run of one is an adjacent duplicate. Longer
 * runs come from the two older writers: one stored each message as it was
 * added, the other stored the whole turn again when the turn ended, so a turn
 * appears as `user, agent, user, agent` rather than as adjacent pairs.
 */
function dropRepeatedRuns(entries: HistoryEntry[]): HistoryEntry[] {
  const kept: HistoryEntry[] = [];
  let index = 0;
  while (index < entries.length) {
    const repeated = repeatedRunLength(kept, entries, index);
    if (repeated > 0) {
      index += repeated;
    } else {
      kept.push(entries[index]);
      index += 1;
    }
  }
  return kept;
}

/** Length of the shortest run starting at `start` that equals the last run of `kept`, or 0. */
function repeatedRunLength(kept: HistoryEntry[], entries: HistoryEntry[], start: number): number {
  const maxLength = Math.min(kept.length, entries.length - start);
  for (let length = 1; length <= maxLength; length++) {
    const keptStart = kept.length - length;
    let matches = true;
    for (let offset = 0; offset < length && matches; offset++) {
      matches = sameEntry(kept[keptStart + offset], entries[start + offset]);
    }
    if (matches) {
      return length;
    }
  }
  return 0;
}
