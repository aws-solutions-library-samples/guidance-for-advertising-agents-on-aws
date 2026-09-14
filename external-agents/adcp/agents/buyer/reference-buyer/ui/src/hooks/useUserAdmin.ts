/**
 * Cognito user administration.
 *
 * The panel this drives is offered only to members of the admin group, but that is a convenience:
 * every action is re-checked server-side against the verified token, so a button obtained by editing
 * client state returns 403.
 */

import { useCallback, useState } from 'react';

import {
  findErrorMessage,
  findUserCreatedEvent,
  findUsersEvent,
  type PoolUser,
  type UserCreatedEvent,
} from '../lib/types';
import type { InvokeFn } from './useInvoke';

export interface UserAdminState {
  users: PoolUser[];
  /** The pool's admin group name, so the UI labels the real group rather than a guess. */
  adminGroup: string | null;
  /** True while the list has never loaded, as distinct from having loaded and being empty. */
  loading: boolean;
  error: string | null;
  /** The one-time credentials for a user just created. Not recoverable afterwards. */
  created: UserCreatedEvent | null;
  busy: boolean;
  refresh: () => Promise<void>;
  createUser: (username: string, email: string) => Promise<void>;
  dismissCreated: () => void;
}

export function useUserAdmin(invoke: InvokeFn): UserAdminState {
  const [users, setUsers] = useState<PoolUser[]>([]);
  const [adminGroup, setAdminGroup] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<UserCreatedEvent | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const events = await invoke({ action: 'list_users' });
      const event = findUsersEvent(events);
      if (!event) {
        setError(findErrorMessage(events) ?? 'The runtime returned no user list.');
        return;
      }
      setUsers(Array.isArray(event.users) ? event.users : []);
      setAdminGroup(event.admin_group ?? null);
    } catch (err) {
      setError(`Could not load users: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setLoading(false);
    }
  }, [invoke]);

  const createUser = useCallback(
    async (username: string, email: string) => {
      setBusy(true);
      setError(null);
      setCreated(null);
      try {
        const events = await invoke({ action: 'create_user', username, email });
        const event = findUserCreatedEvent(events);
        if (!event) {
          setError(
            findErrorMessage(events) ?? 'The runtime returned no result for this request.',
          );
          return;
        }
        setCreated(event);
        // Refreshed so the new user appears with the status Cognito actually assigned, rather than
        // one assumed from the creation call.
        await refresh();
      } catch (err) {
        setError(
          `Could not create the user: ${err instanceof Error ? err.message : String(err)}`,
        );
      } finally {
        setBusy(false);
      }
    },
    [invoke, refresh],
  );

  return {
    users,
    adminGroup,
    loading,
    error,
    created,
    busy,
    refresh,
    createUser,
    dismissCreated: () => setCreated(null),
  };
}
