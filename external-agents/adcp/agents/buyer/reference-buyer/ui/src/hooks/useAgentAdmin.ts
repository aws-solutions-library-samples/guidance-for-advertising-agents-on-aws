/**
 * Agent-registration administration for one registry (sellers or governance).
 *
 * Mirrors `useUserAdmin`: the screen it drives is offered only to admins, but that is cosmetic —
 * every action is re-checked server-side against the verified token, so a request made without admin
 * membership returns 403. One instance is used per kind; the AgentAdmin screen holds two.
 */

import { useCallback, useState } from 'react';

import {
  findConnectionTestEvent,
  findDeletedEvent,
  findRegistrationsEvent,
  findSavedEvent,
  type AgentKind,
  type AgentRegistration,
  type ConnectionTestEvent,
} from '../lib/agentRegistry';
import { findErrorMessage } from '../lib/types';
import type { InvokeFn } from './useInvoke';

export interface AgentAdminState {
  kind: AgentKind;
  agents: AgentRegistration[];
  loading: boolean;
  error: string | null;
  busy: boolean;
  /** The outcome of the most recent connection test, keyed by agent id. */
  testResult: ConnectionTestEvent | null;
  testingId: string | null;
  refresh: () => Promise<void>;
  save: (entry: Record<string, unknown>) => Promise<boolean>;
  /** Delete a STORED (custom) entry. */
  remove: (id: string) => Promise<void>;
  /** Remove an ENV built-in from the effective registry (a durable, reversible hide). */
  hide: (id: string) => Promise<void>;
  /** Restore a previously hidden built-in. */
  unhide: (id: string) => Promise<void>;
  test: (id: string) => Promise<void>;
  dismissTest: () => void;
}

export function useAgentAdmin(invoke: InvokeFn, kind: AgentKind): AgentAdminState {
  const [agents, setAgents] = useState<AgentRegistration[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [testResult, setTestResult] = useState<ConnectionTestEvent | null>(null);
  const [testingId, setTestingId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const events = await invoke({ action: 'list_agent_registrations', kind });
      const event = findRegistrationsEvent(events);
      if (!event) {
        setError(findErrorMessage(events) ?? 'The runtime returned no registry.');
        return;
      }
      setAgents(Array.isArray(event.agents) ? event.agents : []);
    } catch (err) {
      setError(`Could not load the ${kind} registry: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setLoading(false);
    }
  }, [invoke, kind]);

  const save = useCallback(
    async (entry: Record<string, unknown>): Promise<boolean> => {
      setBusy(true);
      setError(null);
      try {
        const events = await invoke({ action: 'upsert_agent_registration', kind, entry });
        const event = findSavedEvent(events);
        if (!event) {
          setError(findErrorMessage(events) ?? 'The registration was not saved.');
          return false;
        }
        setAgents(Array.isArray(event.agents) ? event.agents : []);
        return true;
      } catch (err) {
        setError(`Could not save: ${err instanceof Error ? err.message : String(err)}`);
        return false;
      } finally {
        setBusy(false);
      }
    },
    [invoke, kind],
  );

  const remove = useCallback(
    async (id: string) => {
      setBusy(true);
      setError(null);
      try {
        const events = await invoke({ action: 'delete_agent_registration', kind, id });
        const event = findDeletedEvent(events);
        if (!event) {
          setError(findErrorMessage(events) ?? 'The registration was not deleted.');
          return;
        }
        setAgents(Array.isArray(event.agents) ? event.agents : []);
      } catch (err) {
        setError(`Could not delete: ${err instanceof Error ? err.message : String(err)}`);
      } finally {
        setBusy(false);
      }
    },
    [invoke, kind],
  );

  const setHidden = useCallback(
    async (action: 'hide_agent_registration' | 'unhide_agent_registration', id: string) => {
      setBusy(true);
      setError(null);
      try {
        const events = await invoke({ action, kind, id });
        const event = findRegistrationsEvent(events);
        if (!event) {
          setError(findErrorMessage(events) ?? 'The registry did not update.');
          return;
        }
        setAgents(Array.isArray(event.agents) ? event.agents : []);
      } catch (err) {
        setError(`Could not update: ${err instanceof Error ? err.message : String(err)}`);
      } finally {
        setBusy(false);
      }
    },
    [invoke, kind],
  );

  const hide = useCallback((id: string) => setHidden('hide_agent_registration', id), [setHidden]);
  const unhide = useCallback((id: string) => setHidden('unhide_agent_registration', id), [setHidden]);

  const test = useCallback(
    async (id: string) => {
      setTestingId(id);
      setTestResult(null);
      setError(null);
      try {
        const events = await invoke({ action: 'test_agent_connection', kind, id });
        const event = findConnectionTestEvent(events);
        if (!event) {
          setError(findErrorMessage(events) ?? 'The connection test returned no result.');
          return;
        }
        setTestResult(event);
      } catch (err) {
        setError(`Connection test failed to run: ${err instanceof Error ? err.message : String(err)}`);
      } finally {
        setTestingId(null);
      }
    },
    [invoke, kind],
  );

  return {
    kind,
    agents,
    loading,
    error,
    busy,
    testResult,
    testingId,
    refresh,
    save,
    remove,
    hide,
    unhide,
    test,
    dismissTest: () => setTestResult(null),
  };
}
