/**
 * The session picker, revealed on hover over the top of the viewport.
 *
 * The design asks for a control hidden until the pointer reaches the top 5% of the screen, so the
 * journey itself stays uncluttered. It also opens on KEYBOARD FOCUS and on TAP — not additions to the
 * design but a consequence of it: hover does not exist on a touch screen and cannot be produced by a
 * keyboard, so a hover-only control is unreachable on those inputs and switching sessions would be
 * impossible.
 *
 * ## Why the sessions are a list and not a `<select>`
 *
 * The first version used a native `<select>`, and it could not be used at all. Two causes, both worth
 * recording because both are easy to reintroduce:
 *
 *   1. **A native select's popup is not in the DOM tree.** Opening it moves the pointer out of this
 *      container, `onMouseLeave` fires, the whole sheet unmounts, and the popup dies with it. The
 *      dropdown appeared to refuse to stay open.
 *   2. **The container's own `onClick` toggled the sheet.** A click on the select bubbled up and
 *      closed the thing containing it, so selecting was impossible even when the popup survived.
 *
 * A list of rows rendered inside the sheet has neither problem: nothing leaves the DOM tree, so hover
 * is continuous, and the sheet stops click propagation so a row press cannot close its own container.
 * It is also better at a booth — every session is visible at a glance instead of behind a second
 * interaction.
 *
 * Styling lives in `styles/journey-session.css`, which is ours rather than authored. It borrows the
 * prototype's variables and its hairline-plus-bloom treatment; it does not add rules to the generated
 * `journey.css`, which is diffed against the prototype.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { agentLabelFor, sessionBriefLabel, shortSessionId } from '../../lib/sessions';
import type { SessionMeta } from '../../lib/types';

export interface SessionHoverZoneProps {
  readonly sessions: readonly SessionMeta[];
  readonly selectedId: string | null;
  readonly origin: 'auto' | 'manual';
  readonly onSelect: (sessionId: string) => void;
  readonly onFollowNewest: () => void;
  /** Names the other seller journeys in this session, or null when there are none. */
  readonly notice: string | null;
  readonly staleNote: string | null;
  /**
   * Why the list could not be read, or null when it was read successfully.
   *
   * An empty list and an unreadable list are different facts and must not share one message. The
   * empty state below is only correct when a read actually succeeded and returned nothing.
   */
  readonly listError: string | null;
}

export function SessionHoverZone({
  sessions,
  selectedId,
  origin,
  onSelect,
  onFollowNewest,
  notice,
  staleNote,
  listError,
}: SessionHoverZoneProps) {
  const [open, setOpen] = useState(false);

  /**
   * Set when the reader dismisses with Escape.
   *
   * A REF rather than state, deliberately. Escape returns focus to the zone, and focus is itself an
   * opening trigger, so the focus handler runs synchronously inside the same event. A state update
   * would not be visible to it yet — the handler would read the previous value, reopen the sheet, and
   * Escape would appear to do nothing. A ref is current immediately.
   */
  const dismissed = useRef(false);
  const container = useRef<HTMLDivElement | null>(null);

  const close = useCallback(() => setOpen(false), []);

  // Escape closes and returns focus to the zone, so a keyboard reader is never left with focus inside
  // a control that has just disappeared.
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      dismissed.current = true;
      setOpen(false);
      container.current?.focus();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [open]);

  /** Move focus between rows with the arrow keys, which is what a listbox owes a keyboard reader. */
  const onListKeyDown = (event: React.KeyboardEvent<HTMLUListElement>) => {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
    event.preventDefault();
    const rows = Array.from(
      event.currentTarget.querySelectorAll<HTMLButtonElement>('.session-option'),
    );
    if (rows.length === 0) return;
    const at = rows.findIndex((row) => row === document.activeElement);
    const step = event.key === 'ArrowDown' ? 1 : -1;
    // Wraps, so a reader holding an arrow key is never stuck against an end with no feedback.
    const next = at === -1 ? 0 : (at + step + rows.length) % rows.length;
    rows[next]?.focus();
  };

  const selectedMeta = sessions.find((session) => session.session_id === selectedId) ?? null;

  return (
    <div
      ref={container}
      className="session-zone"
      data-testid="session-hover-zone"
      tabIndex={0}
      role="group"
      aria-label="Session controls"
      aria-expanded={open}
      onMouseEnter={() => {
        dismissed.current = false;
        setOpen(true);
      }}
      onMouseLeave={(event) => {
        // Keep it open while focus is inside: a keyboard reader who tabbed in must not lose the sheet
        // because the pointer happened to drift off it.
        if (event.currentTarget.contains(document.activeElement)) return;
        setOpen(false);
      }}
      onFocus={() => {
        if (!dismissed.current) setOpen(true);
      }}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
          setOpen(false);
          dismissed.current = false;
        }
      }}
      // Touch has no hover, so a tap on the STRIP has to open it. Clicks inside the sheet are stopped
      // there, so this cannot fire for them — see the sheet's own handler.
      onClick={() => {
        dismissed.current = false;
        setOpen((current) => !current);
      }}
    >
      {open ? (
        <div
          className="session-sheet"
          data-testid="session-dropdown"
          // The fix for cause 2. Without this, pressing a row bubbles to the strip's toggle and closes
          // the sheet the row lives in, so nothing can ever be selected.
          onClick={(event) => event.stopPropagation()}
        >
          <div className="session-head">
            <span className="session-label">session</span>
            <span className="session-current" data-testid="session-current">
              {selectedMeta
                ? `${agentLabelFor(selectedMeta)} · ${shortSessionId(selectedMeta.session_id)}`
                : selectedId
                  ? // Selected but no longer in the list. Said plainly rather than silently reset, so
                    // the control never disagrees with what is on screen.
                    `${shortSessionId(selectedId)} (no longer listed)`
                  : 'none selected'}
            </span>
            {origin === 'manual' ? (
              <button
                type="button"
                className="btn ghost"
                style={{ marginLeft: 'auto' }}
                onClick={onFollowNewest}
              >
                Follow newest
              </button>
            ) : (
              <span className="session-following" data-testid="following-newest">
                following newest
              </span>
            )}
          </div>

          {notice !== null || staleNote !== null ? (
            <div className="session-notes">
              {notice !== null ? (
                <span className="session-note" data-testid="seller-notice">
                  {notice}
                </span>
              ) : null}
              {staleNote !== null ? (
                <span className="session-note" data-testid="stale-note">
                  {staleNote}
                </span>
              ) : null}
            </div>
          ) : null}

          {sessions.length === 0 && listError !== null ? (
            // The list could not be read, so say that instead of reporting an absence we cannot
            // vouch for. An expired token throws before any request is sent, which produces no
            // status code and no server-side trace -- this message is the only signal a reader gets.
            <p className="session-empty" data-testid="session-list-error">
              Session list unavailable: {listError}
            </p>
          ) : sessions.length === 0 ? (
            // An empty state. Not "loading", because this is also what no sessions looks like.
            <p className="session-empty" data-testid="session-list-empty">
              No sessions recorded yet.
            </p>
          ) : (
            <ul
              className="session-list"
              role="listbox"
              aria-label="Recorded sessions"
              onKeyDown={onListKeyDown}
              data-testid="session-list"
            >
              {sessions.map((session) => {
                const selected = session.session_id === selectedId;
                const brief = sessionBriefLabel(session);
                return (
                  <li key={session.session_id}>
                    <button
                      type="button"
                      className="session-option"
                      role="option"
                      aria-selected={selected}
                      onClick={() => {
                        onSelect(session.session_id);
                        close();
                      }}
                    >
                      <span className="so-main">
                        <span className="so-agent">{agentLabelFor(session)}</span>
                        <span className="so-id">{shortSessionId(session.session_id)}</span>
                      </span>
                      {/* The brief is what separates two sessions run by the same agent. Rendered
                          only when the record carries one. */}
                      {brief !== null ? (
                        <span className="so-brief" data-testid="session-brief">
                          {brief}
                        </span>
                      ) : null}
                      {/* A status the recorder did not write reads "unknown". */}
                      <span className="so-status" data-status={session.status || 'unknown'}>
                        {session.status || 'unknown'}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      ) : null}
    </div>
  );
}
