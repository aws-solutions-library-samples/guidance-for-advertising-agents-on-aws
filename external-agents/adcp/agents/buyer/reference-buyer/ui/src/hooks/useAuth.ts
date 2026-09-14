/**
 * Sign-in state and the Cognito flows behind it.
 *
 * Holds the two-step shape the pool actually requires: a normal sign-in returns tokens, while a
 * user created by an admin is answered with NEW_PASSWORD_REQUIRED and issued no tokens until a
 * permanent password is set. Both are ordinary outcomes here, not error cases.
 */

import { useCallback, useState } from 'react';

import { CognitoError, cognitoSetNewPassword, cognitoSignIn, isAdminUser } from '../lib/cognito';
import type { CognitoConfig } from '../lib/cognito';
import {
  clearSession,
  isSessionExpired,
  loadSession,
  saveSession,
  sessionFromAuthResult,
  type StoredSession,
} from '../lib/session';
import type { NewPasswordChallenge } from '../lib/types';

export interface AuthState {
  session: StoredSession | null;
  /** Set while Cognito is mid-challenge, which is what the password step renders from. */
  challenge: NewPasswordChallenge | null;
  error: string | null;
  busy: boolean;
  isAdmin: boolean;
  signIn: (username: string, password: string) => Promise<void>;
  submitNewPassword: (newPassword: string) => Promise<void>;
  cancelChallenge: () => void;
  signOut: () => void;
}

export function useAuth(config: CognitoConfig | null): AuthState {
  // Read once on mount: a session already in storage means this tab is signed in, so the login
  // screen is skipped rather than flashed.
  //
  // An EXPIRED stored session counts as not signed in. Without this check the app stayed on the
  // journey view holding a dead token, and the failure surfaced in the worst possible place: every
  // `authHeader()` returned null, so `useInvoke` threw "Not signed in." BEFORE issuing any request.
  // No request meant no 401, no server log and nothing for a poll to report -- the session list just
  // read "No sessions recorded yet." indefinitely and ignored new sessions, which looks like a broken
  // backend rather than an hour-old login. Access tokens last one hour, so this was reached routinely.
  //
  // Storage is cleared too, so a reload cannot resurrect the same dead token.
  const [session, setSession] = useState<StoredSession | null>(() => {
    const stored = loadSession();
    if (stored && isSessionExpired(stored)) {
      clearSession();
      return null;
    }
    return stored;
  });
  const [challenge, setChallenge] = useState<NewPasswordChallenge | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const signIn = useCallback(
    async (username: string, password: string) => {
      if (!config) {
        setError('Configuration has not loaded yet, so there is no user pool to sign in to.');
        return;
      }
      setBusy(true);
      setError(null);
      try {
        const outcome = await cognitoSignIn(config, username, password);
        if (outcome.challenge) {
          setChallenge(outcome.challenge);
          return;
        }
        const stored = sessionFromAuthResult(username, outcome.authResult);
        saveSession(stored);
        setSession(stored);
      } catch (err) {
        setError(describeAuthError(err));
      } finally {
        setBusy(false);
      }
    },
    [config],
  );

  const submitNewPassword = useCallback(
    async (newPassword: string) => {
      if (!config || !challenge) return;
      setBusy(true);
      setError(null);
      try {
        const auth = await cognitoSetNewPassword(config, challenge, newPassword);
        // The challenge carries the canonical username, which can differ from what was typed.
        const stored = sessionFromAuthResult(challenge.username, auth);
        saveSession(stored);
        setSession(stored);
        setChallenge(null);
      } catch (err) {
        setError(describeAuthError(err));
      } finally {
        setBusy(false);
      }
    },
    [config, challenge],
  );

  const cancelChallenge = useCallback(() => {
    // Dropping the challenge also drops its Cognito session, so the next attempt starts a fresh
    // one. Keeping a stale session around produces an expired-session failure on submit.
    setChallenge(null);
    setError(null);
  }, []);

  const signOut = useCallback(() => {
    clearSession();
    setSession(null);
    setChallenge(null);
    setError(null);
  }, []);

  return {
    session,
    challenge,
    error,
    busy,
    // Decides what the UI offers, not what it is allowed to do: every admin action is re-checked
    // server-side against the verified token, so editing this in a console yields a 403.
    isAdmin: isAdminUser(session?.accessToken),
    signIn,
    submitNewPassword,
    cancelChallenge,
    signOut,
  };
}

/**
 * Turns a failure into something a reader can act on.
 *
 * Cognito's own message is used where there is one, because "Incorrect username or password" is
 * more useful than any paraphrase. The exception type is appended only when it adds something the
 * message does not already say.
 */
function describeAuthError(err: unknown): string {
  if (err instanceof CognitoError) {
    return err.cognitoType && !err.message.includes(err.cognitoType)
      ? `${err.message} (${err.cognitoType})`
      : err.message;
  }
  return err instanceof Error ? err.message : String(err);
}
