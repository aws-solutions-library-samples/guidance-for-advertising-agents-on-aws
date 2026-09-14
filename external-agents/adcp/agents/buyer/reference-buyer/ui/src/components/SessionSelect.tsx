/**
 * The header control for choosing which conversation the chat column shows.
 *
 * Carries only the newest few sessions; the rest live in the all-sessions panel behind the last
 * entry. Beyond a handful a select stops being glanceable.
 */

import {
  SESSION_DROPDOWN_LIMIT,
  VIEW_ALL_VALUE,
  sessionOptionLabel,
  shortSessionId,
} from '../lib/sessions';
import type { SessionMeta } from '../lib/types';

export interface SessionSelectProps {
  sessions: SessionMeta[];
  viewedSessionId: string | null;
  onView: (sessionId: string) => void;
  onStop: () => void;
  onOpenPanel: () => void;
}

export function SessionSelect({
  sessions,
  viewedSessionId,
  onView,
  onStop,
  onOpenPanel,
}: SessionSelectProps) {
  const shown = sessions.slice(0, SESSION_DROPDOWN_LIMIT);
  const viewedIsListed = shown.some((s) => s.session_id === viewedSessionId);
  const viewedKnown = sessions.find((s) => s.session_id === viewedSessionId);

  return (
    <select
      // The value is purely a view of what is on screen, so a periodic refresh cannot kick the
      // reader out of a session they are watching, nor leave a stale id selected.
      value={viewedSessionId ?? ''}
      title="Other active or recent agent sessions"
      onChange={(e) => {
        const value = e.target.value;
        if (value === VIEW_ALL_VALUE) {
          // Not a session: intercepted before it can be treated as one.
          onOpenPanel();
          return;
        }
        if (value === '') {
          onStop();
          return;
        }
        onView(value);
      }}
      className="max-w-[260px] rounded-full border border-line bg-surface px-3 py-1.5 text-[13px] text-ink-2"
    >
      <option value="">— My session —</option>
      {shown.map((session) => (
        <option key={session.session_id} value={session.session_id}>
          {sessionOptionLabel(session)}
        </option>
      ))}
      {/* The session on screen stays selectable once it is older than the newest few, or the header
          would claim "My session" while the column still replays someone else's. */}
      {viewedSessionId !== null && !viewedIsListed && (
        <option value={viewedSessionId}>
          {viewedKnown
            ? sessionOptionLabel(viewedKnown)
            : // Aged out, TTLed, or filtered by the conversations-only default. "Not in this list"
              // rather than "no longer listed", which would claim the record is gone.
              `${shortSessionId(viewedSessionId)} — not in this list`}
        </option>
      )}
      {sessions.length > 0 && (
        // No count on it: the poll fetches a page, so the number beyond what is listed is not
        // something this control knows.
        <option value={VIEW_ALL_VALUE}>View all sessions…</option>
      )}
    </select>
  );
}
