/**
 * The read-only replay of a recorded session.
 *
 * This is where the duplicate-message bug lived in the vanilla UI, so the guards against it are the
 * point of this file rather than an incidental detail:
 *
 *   1. **One poll at a time.** The interval is 1s and a request to the runtime takes roughly half
 *      that on a warm session, so without a guard a second tick reads the same step index, asks for
 *      the same range and renders it again. Every message then appeared exactly twice, with
 *      identical text and timestamp. The guard matters MORE at 1s than it did at 2s.
 *   2. **Applied steps are never re-applied.** Each step's index is checked against the high-water
 *      mark before use, so even an overlapping response cannot draw a step twice.
 *   3. **A stale poll cannot release the guard.** Polls are tagged with the session they belong to;
 *      one left over from a session the reader has navigated away from is discarded and does not free
 *      the guard for the current view. Getting this wrong was a real hole in the first fix.
 *
 * A run of failed polls is reported rather than swallowed. A frozen transcript that looks like the
 * whole conversation is the same class of problem as showing a stale status as current.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type { Message } from '../lib/conversation';
import { highestStepIndex, replaySteps } from '../lib/replay';
import { findSessionStepsEvent, type SessionMeta } from '../lib/types';
import { toolLogReducer, initialToolLog, type ToolLogState } from './toolLog';
import type { InvokeFn } from './useInvoke';

const POLL_MS = 1000;
/** Consecutive failures before the transcript admits it has stopped tracking the session. */
const FAILURES_BEFORE_WARNING = 3;

export interface SessionViewerState {
  /** The session being replayed, or null when the reader is in their own conversation. */
  viewedSessionId: string | null;
  meta: SessionMeta | null;
  messages: Message[];
  toolLog: ToolLogState;
  /** What to say instead of a transcript: still loading, expired, or nothing recorded yet. */
  status: string | null;
  /** Set once a run of polls has failed, so a frozen view does not read as a complete one. */
  staleNote: string | null;
  view: (sessionId: string) => void;
  stop: () => void;
}

export function useSessionViewer(invoke: InvokeFn): SessionViewerState {
  const [viewedSessionId, setViewedSessionId] = useState<string | null>(null);
  const [meta, setMeta] = useState<SessionMeta | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [toolLog, setToolLog] = useState<ToolLogState>(initialToolLog);
  const [status, setStatus] = useState<string | null>(null);
  const [staleNote, setStaleNote] = useState<string | null>(null);

  // Mutable poll state, held in refs because the interval callback must see current values without
  // being torn down and rebuilt on every change.
  const lastIndex = useRef(-1);
  const inFlight = useRef(false);
  /** Which session the in-flight poll belongs to, so a stale one cannot release the guard. */
  const pollOwner = useRef<string | null>(null);
  const failures = useRef(0);

  const view = useCallback((sessionId: string) => {
    setViewedSessionId(sessionId);
    setMeta(null);
    setMessages([]);
    setToolLog(initialToolLog);
    setStatus('Loading conversation…');
    setStaleNote(null);
    lastIndex.current = -1;
    failures.current = 0;
    // Released here so this session's first poll is not skipped. A poll still outstanding for the
    // previous session will find `pollOwner` changed and will not free the guard from under this one.
    inFlight.current = false;
    pollOwner.current = null;
  }, []);

  const stop = useCallback(() => {
    setViewedSessionId(null);
    setMeta(null);
    setMessages([]);
    setToolLog(initialToolLog);
    setStatus(null);
    setStaleNote(null);
    lastIndex.current = -1;
    inFlight.current = false;
    pollOwner.current = null;
  }, []);

  useEffect(() => {
    const sessionId = viewedSessionId;
    if (sessionId === null) return;

    let cancelled = false;

    const poll = async () => {
      if (inFlight.current) return;
      inFlight.current = true;
      pollOwner.current = sessionId;
      try {
        const events = await invoke({
          action: 'get_session_steps',
          session_id: sessionId,
          since_index: lastIndex.current,
        });
        // A selection change mid-request must not paint into the new view.
        if (cancelled) return;

        const event = findSessionStepsEvent(events);
        if (!event) return;
        const nextMeta = event.meta ?? null;
        const steps = Array.isArray(event.steps) ? event.steps : [];

        // Only steps past the mark, so a repeated or overlapping range cannot draw twice.
        const fresh = steps.filter(
          (step) => Number.isFinite(step.step_index) && step.step_index > lastIndex.current,
        );
        lastIndex.current = highestStepIndex(fresh, lastIndex.current);

        setMeta(nextMeta);
        if (fresh.length > 0) {
          const { messages: additions, toolActions } = replaySteps(fresh);
          if (additions.length > 0) setMessages((current) => [...current, ...additions]);
          if (toolActions.length > 0) {
            setToolLog((current) => toolActions.reduce(toolLogReducer, current));
          }
        }

        // Say what is actually true rather than spinning on "Loading…": a session with no META
        // record at all has expired or never existed, which differs from one whose first step has
        // not landed yet.
        if (steps.length > 0 || lastIndex.current >= 0) {
          setStatus(null);
        } else if (!nextMeta) {
          setStatus(
            'No record found for this session. Session records are removed 24 hours after they start.',
          );
        } else {
          setStatus('No steps recorded yet for this session.');
        }

        // A failed turn's message lives on the meta record rather than in the steps, so the
        // transcript would otherwise just stop with no explanation.
        if (nextMeta?.status === 'error' && nextMeta.error_message) {
          setMessages((current) =>
            current.some((m) => m.id === 'meta-error')
              ? current
              : [
                  ...current,
                  {
                    kind: 'note',
                    id: 'meta-error',
                    label: 'Turn failed',
                    text: nextMeta.error_message ?? '',
                  },
                ],
          );
        }

        failures.current = 0;
        setStaleNote(null);
      } catch (err) {
        if (cancelled) return;
        failures.current += 1;
        if (failures.current >= FAILURES_BEFORE_WARNING) {
          setStaleNote(
            `This transcript has stopped updating: the last ${failures.current} refreshes failed ` +
              `(${err instanceof Error ? err.message : 'reason not reported'}). ` +
              'What is shown may be incomplete.',
          );
        }
      } finally {
        // Only the current view's poll releases the guard.
        if (pollOwner.current === sessionId) inFlight.current = false;
      }
    };

    void poll();
    const timer = setInterval(() => void poll(), POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [viewedSessionId, invoke]);

  return {
    viewedSessionId,
    meta,
    messages,
    toolLog,
    status,
    staleNote,
    view,
    stop,
  };
}
