/**
 * The all-sessions panel.
 *
 * A keyed table. The vanilla version reassigned the whole table's markup on every changed poll, which
 * destroyed the button being pressed between mousedown and mouseup so the browser fired no click at
 * all; the first fix deferred repaints while the pointer was over the list, which froze the panel
 * instead. React's keyed reconciliation removes the cause, so neither workaround is needed.
 */

import { SESSION_LIST_LIMIT, agentLabelFor, invokerLabelFor, sessionStartedLabel, sessionStatusTone } from '../lib/sessions';
import type { SessionMeta } from '../lib/types';
import { Icon } from './Icon';

export interface SessionsPanelProps {
  sessions: SessionMeta[];
  error: string | null;
  viewedSessionId: string | null;
  onView: (sessionId: string) => void;
  onClose: () => void;
}

export function SessionsPanel({
  sessions,
  error,
  viewedSessionId,
  onView,
  onClose,
}: SessionsPanelProps) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-[rgba(15,23,41,0.45)] p-6"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      onKeyDown={(e) => {
        if (e.key === 'Escape') onClose();
      }}
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="All sessions"
        className="flex max-h-[80vh] w-full max-w-[1000px] flex-col rounded-[14px] border border-line bg-surface shadow-[var(--shadow-card)]"
      >
        <div className="flex flex-none items-center gap-3 border-b border-line px-5 py-3">
          {/* Named for what the list holds. It is scoped to one runtime, so "All sessions" would have
              described the table rather than the rows. */}
          <h2 className="m-0 text-[15px] font-bold">Buyer agent sessions</h2>
          <span className="font-mono text-[13px] text-muted">
            {/* A full page means "the newest N", not a total. */}
            {sessions.length >= SESSION_LIST_LIMIT
              ? `newest ${SESSION_LIST_LIMIT}`
              : `${sessions.length} session${sessions.length === 1 ? '' : 's'}`}
          </span>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="ml-auto rounded-full border border-line p-1.5 text-ink-2 hover:bg-line-soft"
          >
            <Icon name="close" size={13} />
          </button>
        </div>

        <div className="flex-none border-b border-line-soft px-5 py-2.5">
          {/* States the scope rather than filtering quietly. Replaced a tool-calls checkbox that the
              agent scope made inert: those records are all seller-written, so it could no longer add a
              row and would have been a control that did nothing. */}
          <p className="m-0 text-[13px] text-muted">
            Conversations recorded by the AdCP Buyer Agent (A2A). Sessions the sellers and the HTTP
            runtime recorded into the same table are not listed.
          </p>
        </div>

        {error !== null && (
          <p className="m-0 flex-none border-b border-line-soft bg-[#fde8e8] px-5 py-2 text-[13px] text-red">
            Could not refresh the session list: {error}
          </p>
        )}

        <div className="scroll-column flex-1">
          {sessions.length === 0 ? (
            // An empty list and an unavailable list are different answers.
            <p className="m-0 px-5 py-4 text-[13px] text-muted">
              {error !== null ? 'Session list unavailable.' : 'No sessions recorded.'}
            </p>
          ) : (
            <table className="w-full border-collapse text-[13px]">
              <thead>
                <tr className="text-left text-[13px] tracking-wide text-muted uppercase">
                  <th className="px-5 py-2 font-semibold">Agent</th>
                  <th className="px-3 py-2 font-semibold">Invoker</th>
                  <th className="px-3 py-2 font-semibold">Request</th>
                  <th className="px-3 py-2 font-semibold">Status</th>
                  <th className="px-3 py-2 font-semibold">Started</th>
                  <th className="px-5 py-2" />
                </tr>
              </thead>
              <tbody>
                {sessions.map((session) => {
                  const viewing = session.session_id === viewedSessionId;
                  const preview = session.request_preview ?? '';
                  const { tone, label } = sessionStatusTone(session.status);
                  return (
                    <tr
                      key={session.session_id}
                      className={`border-t border-line-soft ${viewing ? 'bg-line-soft' : ''}`}
                    >
                      <td className="px-5 py-2 font-semibold">{agentLabelFor(session)}</td>
                      <td className="px-3 py-2">{invokerLabelFor(session)}</td>
                      <td className="max-w-[320px] truncate px-3 py-2 text-muted">
                        {preview !== '' ? preview : '—'}
                      </td>
                      <td className="px-3 py-2">
                        <StatusPill tone={tone} label={label} />
                      </td>
                      <td className="px-3 py-2 whitespace-nowrap text-muted">
                        {sessionStartedLabel(session)}
                      </td>
                      <td className="px-5 py-2 text-right">
                        <button
                          type="button"
                          disabled={viewing}
                          onClick={() => {
                            onClose();
                            onView(session.session_id);
                          }}
                          className="rounded-full border border-line px-3 py-1 text-[13px] font-semibold text-ink-2 hover:bg-line-soft disabled:opacity-50"
                        >
                          {viewing ? 'Viewing' : 'View'}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}

const TONE_CLASS: Record<string, string> = {
  live: 'text-sky bg-[#e8f6fc]',
  completed: 'text-green bg-[#e3f8ee]',
  error: 'text-red bg-[#fde8e8]',
  unknown: 'text-muted bg-line-soft',
};

function StatusPill({ tone, label }: { tone: string; label: string }) {
  return (
    <span
      className={`rounded-full px-2 py-0.5 text-[13px] font-bold ${TONE_CLASS[tone] ?? TONE_CLASS['unknown']}`}
    >
      {label}
    </span>
  );
}
