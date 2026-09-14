/**
 * Following the newest session, for the journey view.
 *
 * The view runs unattended at a booth, reacting to briefs triggered from another system. So the
 * default is to follow whatever is newest and to switch the instant something newer appears, even
 * mid-animation. Nobody is standing there reading a run when a new request lands, and a screen stuck
 * on a stale session is silently wrong for as long as it takes someone to notice.
 *
 * Three guards are carried over from the existing session viewer, and all three are load-bearing.
 * Their absence caused a duplicate-message bug in the vanilla UI that reached production:
 *
 *   1. One poll at a time. The interval is shorter than a slow request, so without a guard a second
 *      tick reads the same high-water mark, asks for the same range and applies it twice.
 *   2. Applied steps are never re-applied. Each step's index is checked against the mark, so even an
 *      overlapping response cannot draw a step twice.
 *   3. A stale poll cannot release the guard. Polls are tagged with the session they belong to; one
 *      left over from a session we have navigated away from is discarded and does not free the guard
 *      for the current view. Getting this wrong was a real hole in the first fix.
 *
 * This hook adds a fourth, needed because auto-switching is new: a `generation` counter, incremented
 * on every switch. Anything scheduled under an older generation is dropped at the point of EFFECT
 * rather than at scheduling, because a promise that has already resolved has no timer left to clear.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import {
  BUYER_A2A_AGENT_ID,
  SESSION_LIST_LIMIT,
  isSessionForAgent,
  sortByRecency,
} from '../lib/sessions';
import {
  findErrorMessage,
  findSessionStepsEvent,
  findSessionsEvent,
  type SessionMeta,
  type SessionStep,
} from '../lib/types';
import type { InvokeFn } from './useInvoke';

/** How often the session list is re-read. Matches the existing sessions poll. */
const LIST_POLL_MS = 5000;
/** How often the selected session's steps are re-read. Matches the existing viewer. */
const STEP_POLL_MS = 1000;
/** Consecutive failures before the view admits it has stopped tracking. */
const FAILURES_BEFORE_WARNING = 3;

export interface JourneySessionState {
  /** The session on screen, or null before the first list arrives. */
  readonly sessionId: string | null;
  readonly meta: SessionMeta | null;
  readonly steps: readonly SessionStep[];
  /** Every recent conversation, newest first, for the dropdown. */
  readonly sessions: readonly SessionMeta[];
  /** Whether the current selection came from the reader or from following the newest. */
  readonly origin: 'auto' | 'manual';
  /** Increments on every switch. Consumers tag work with it and drop stale work. */
  readonly generation: number;
  /** Set once a run of polls has failed, so a frozen view does not read as a complete one. */
  readonly staleNote: string | null;
  /**
   * Why the session list could not be read, or null when the last read succeeded.
   *
   * Distinct from `staleNote`, which is about the STEP poll for an already-chosen session. This
   * exists because an empty list and an unreadable list are different facts, and the view previously
   * rendered both as "No sessions recorded yet." -- stating a negative result it had not established.
   * The case that made this matter: an expired token makes `invoke` throw before it sends anything,
   * so there is no request, no 401 and no server-side trace, and the only available signal is here.
   */
  readonly listError: string | null;
  /** What to say instead of a journey: still loading, or nothing recorded. */
  readonly status: string | null;
  readonly selectSession: (sessionId: string) => void;
  readonly followNewest: () => void;
}

export function useJourneySession(invoke: InvokeFn, enabled: boolean): JourneySessionState {
  const [sessions, setSessions] = useState<SessionMeta[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [origin, setOrigin] = useState<'auto' | 'manual'>('auto');
  const [meta, setMeta] = useState<SessionMeta | null>(null);
  const [steps, setSteps] = useState<SessionStep[]>([]);
  const [generation, setGeneration] = useState(0);
  const [staleNote, setStaleNote] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);

  // Mutable poll state in refs: the interval callbacks must see current values without being torn
  // down and rebuilt on every change.
  const listInFlight = useRef(false);
  const stepInFlight = useRef(false);
  /** Which session the in-flight step poll belongs to, so a stale one cannot release the guard. */
  const stepOwner = useRef<string | null>(null);
  const lastIndex = useRef(-1);
  const failures = useRef(0);
  const originRef = useRef<'auto' | 'manual'>('auto');

  originRef.current = origin;

  /** Adopt a session: reset every per-session accumulator and bump the generation. */
  const adopt = useCallback((next: string, nextOrigin: 'auto' | 'manual') => {
    setSessionId(next);
    setOrigin(nextOrigin);
    setMeta(null);
    setSteps([]);
    setStatus('Loading session\u2026');
    setStaleNote(null);
    lastIndex.current = -1;
    failures.current = 0;
    // Released here so this session's first poll is not skipped. A poll still outstanding for the
    // previous session finds `stepOwner` changed and does not free the guard from under this one.
    stepInFlight.current = false;
    stepOwner.current = null;
    setGeneration((g) => g + 1);
  }, []);

  const selectSession = useCallback(
    (next: string) => {
      if (next === sessionId && originRef.current === 'manual') return;
      adopt(next, 'manual');
    },
    [adopt, sessionId],
  );

  const followNewest = useCallback(() => {
    setOrigin('auto');
    const newest = sessions[0]?.session_id;
    if (typeof newest === 'string' && newest !== sessionId) adopt(newest, 'auto');
  }, [adopt, sessions, sessionId]);

  // --- the session list -----------------------------------------------------------------------
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;

    const poll = async () => {
      if (listInFlight.current) return;
      listInFlight.current = true;
      try {
        const events = await invoke({
          action: 'list_sessions',
          limit: SESSION_LIST_LIMIT,
          // This runtime's sessions only. Asked of the server rather than filtered on arrival so the
          // page budget is spent on rows this view can show: the sellers and the HTTP runtime record
          // into the same table, and a page of 50 was mostly theirs.
          agent_id: BUYER_A2A_AGENT_ID,
          // Conversations, not standalone tool calls. A verification run once produced 95
          // uncorrelated records out of 101, which would make auto-switch chase noise.
          conversations_only: true,
        });
        if (cancelled) return;
        const event = findSessionsEvent(events);
        if (!event) return;
        // A2A buyer sessions only, newest first.
        //
        // Seller-recorded sessions are children of a buyer session (their ids embed it), so listing
        // them made the dropdown several times longer than the number of real journeys AND pushed the
        // newest buyer session away from index 0. Auto-switch reads `sessions[0]`, so that single fact
        // caused both reported symptoms: a badly ordered list, and no switch when a new invocation
        // arrived.
        const list = sortByRecency(
          (Array.isArray(event.sessions) ? event.sessions : []).filter((session) =>
            isSessionForAgent(session, BUYER_A2A_AGENT_ID),
          ),
        );
        setSessions(list);
        setListError(null);
      } catch (err) {
        if (cancelled) return;
        // A failed list poll leaves the last known list in place, but it must not leave the view
        // claiming there is nothing to show. Previously this was swallowed entirely on the reasoning
        // that the step poll already reports staleness -- which is true only once a session has been
        // chosen. With an empty list there is no step poll running, so nothing reported anything and
        // the empty state read as fact.
        setListError(err instanceof Error ? err.message : 'Could not read the session list.');
      } finally {
        listInFlight.current = false;
      }
    };

    void poll();
    const timer = setInterval(() => void poll(), LIST_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [enabled, invoke]);

  // --- follow the newest ----------------------------------------------------------------------
  useEffect(() => {
    if (originRef.current === 'manual') return;
    const newest = sessions[0]?.session_id;
    if (typeof newest !== 'string') return;
    if (newest === sessionId) return;
    // Switch immediately, interrupting any run. Decided deliberately: the booth is unattended, and
    // finishing an old animation while the newly triggered session runs off screen is the failure
    // mode that matters here.
    adopt(newest, 'auto');
  }, [sessions, sessionId, adopt]);

  // --- the selected session's steps -----------------------------------------------------------
  useEffect(() => {
    if (!enabled) return;
    const current = sessionId;
    if (current === null) return;
    let cancelled = false;

    const poll = async () => {
      if (stepInFlight.current) return;
      stepInFlight.current = true;
      stepOwner.current = current;
      try {
        const events = await invoke({
          action: 'get_session_steps',
          session_id: current,
          since_index: lastIndex.current,
        });
        if (cancelled) return;
        const event = findSessionStepsEvent(events);
        if (!event) {
          const message = findErrorMessage(events);
          if (message !== null) throw new Error(message);
          return;
        }

        const nextMeta = event.meta ?? null;
        const incoming = Array.isArray(event.steps) ? event.steps : [];
        // Only steps past the mark, so a repeated or overlapping range cannot be applied twice.
        const fresh = incoming.filter(
          (step) => Number.isFinite(step.step_index) && step.step_index > lastIndex.current,
        );
        if (fresh.length > 0) {
          lastIndex.current = fresh.reduce(
            (highest, step) => Math.max(highest, step.step_index),
            lastIndex.current,
          );
          setSteps((existing) => [...existing, ...fresh]);
        }
        setMeta(nextMeta);

        // Say what is true rather than spinning on "Loading". A session with no META record has
        // expired or never existed, which differs from one whose first step has not landed.
        if (incoming.length > 0 || lastIndex.current >= 0) setStatus(null);
        else if (!nextMeta)
          setStatus(
            'No record found for this session. Session records are removed 24 hours after they start.',
          );
        else setStatus('No steps recorded yet for this session.');

        failures.current = 0;
        setStaleNote(null);
      } catch (err) {
        if (cancelled) return;
        failures.current += 1;
        if (failures.current >= FAILURES_BEFORE_WARNING) {
          setStaleNote(
            `This journey has stopped updating: the last ${failures.current} refreshes failed ` +
              `(${err instanceof Error ? err.message : 'reason not reported'}). ` +
              'What is shown may be incomplete.',
          );
        }
      } finally {
        // Only the current view's poll releases the guard.
        if (stepOwner.current === current) stepInFlight.current = false;
      }
    };

    void poll();
    const timer = setInterval(() => void poll(), STEP_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [enabled, invoke, sessionId]);

  const orderedSteps = useMemo(
    () => [...steps].sort((a, b) => a.step_index - b.step_index),
    [steps],
  );

  return {
    sessionId,
    meta,
    steps: orderedSteps,
    sessions,
    origin,
    generation,
    staleNote,
    status,
    listError,
    selectSession,
    followNewest,
  };
}
