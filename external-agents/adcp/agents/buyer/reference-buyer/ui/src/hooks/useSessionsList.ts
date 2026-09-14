/**
 * The recent-sessions list, polled.
 *
 * Scoped to the A2A buyer runtime. Every seller and the HTTP runtime record into the same table, so an
 * unscoped list was mostly other writers' records — the panel is a way back into a conversation, and a
 * seller's own recording of one leg of it is not a conversation you can return to.
 *
 * The scope is asked of the server and checked again on arrival, because the two are not the same
 * guarantee: the request keeps the page budget on rows this panel can show, and the check keeps a
 * freshly served bundle honest against a runtime that has not been redeployed yet.
 *
 * A failed poll is remembered as a reason rather than producing an empty list, because "no sessions"
 * and "we could not ask" are different answers and only one of them is a fact about the store.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { BUYER_A2A_AGENT_ID, SESSION_LIST_LIMIT, isSessionForAgent } from '../lib/sessions';
import { findErrorMessage, findSessionsEvent, type SessionMeta } from '../lib/types';
import type { InvokeFn } from './useInvoke';

const POLL_MS = 5000;

export interface SessionsListState {
  sessions: SessionMeta[];
  error: string | null;
  refresh: () => void;
}

export function useSessionsList(invoke: InvokeFn, enabled: boolean): SessionsListState {
  const [sessions, setSessions] = useState<SessionMeta[]>([]);
  const [error, setError] = useState<string | null>(null);
  // The same guard the viewer needs: a poll slower than the interval must not have a second one
  // started alongside it.
  const inFlight = useRef(false);

  const refresh = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      const events = await invoke({
        action: 'list_sessions',
        limit: SESSION_LIST_LIMIT,
        agent_id: BUYER_A2A_AGENT_ID,
        // Conversations rather than the standalone records a seller call with no buyer correlation
        // produces. Those are all seller-written, so the agent scope already excludes them; this keeps
        // the request explicit about what it wants rather than relying on that.
        conversations_only: true,
      });
      const event = findSessionsEvent(events);
      if (!event) {
        setError(findErrorMessage(events) ?? 'The runtime returned no session list.');
        return;
      }
      const returned = Array.isArray(event.sessions) ? event.sessions : [];
      setSessions(returned.filter((session) => isSessionForAgent(session, BUYER_A2A_AGENT_ID)));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      inFlight.current = false;
    }
  }, [invoke]);

  useEffect(() => {
    if (!enabled) return;
    void refresh();
    const timer = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(timer);
  }, [enabled, refresh]);

  return {
    sessions,
    error,
    refresh: () => void refresh(),
  };
}
