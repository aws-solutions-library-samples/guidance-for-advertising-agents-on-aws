/**
 * Browser-side session storage.
 *
 * `sessionStorage`, not `localStorage`, deliberately: tokens live for the tab and are gone when it
 * closes, rather than persisting on a shared machine.
 */

import type { AuthResult } from './types';

const SESSION_KEY = 'adcp_buyer_agent_session';
const RUNTIME_SESSION_KEY = 'adcp_buyer_agent_runtime_session_id';

/** AgentCore's minimum length for a runtime session id. Shorter values are rejected outright. */
export const MIN_RUNTIME_SESSION_ID_LENGTH = 33;

export interface StoredSession {
  username: string;
  accessToken: string;
  idToken?: string;
  refreshToken?: string;
  expiresAt?: number;
}

export function saveSession(session: StoredSession): void {
  sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
}

export function loadSession(): StoredSession | null {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    // A stored value without a token is unusable; treating it as absent is more useful than
    // handing callers a session they cannot authenticate with.
    if (!parsed || typeof parsed !== 'object') return null;
    const candidate = parsed as Partial<StoredSession>;
    if (typeof candidate.accessToken !== 'string' || candidate.accessToken === '') return null;
    return {
      username: typeof candidate.username === 'string' ? candidate.username : '',
      accessToken: candidate.accessToken,
      ...(candidate.idToken !== undefined ? { idToken: candidate.idToken } : {}),
      ...(candidate.refreshToken !== undefined ? { refreshToken: candidate.refreshToken } : {}),
      ...(candidate.expiresAt !== undefined ? { expiresAt: candidate.expiresAt } : {}),
    };
  } catch {
    return null;
  }
}

export function clearSession(): void {
  sessionStorage.removeItem(SESSION_KEY);
  // Cleared together with the tokens so a new sign-in starts a new conversation rather than
  // inheriting the previous user's history from the agent's session memory.
  sessionStorage.removeItem(RUNTIME_SESSION_KEY);
}

/** Turns Cognito's token set into what gets stored. */
export function sessionFromAuthResult(username: string, auth: AuthResult): StoredSession {
  return {
    username,
    accessToken: auth.AccessToken,
    ...(auth.IdToken !== undefined ? { idToken: auth.IdToken } : {}),
    ...(auth.RefreshToken !== undefined ? { refreshToken: auth.RefreshToken } : {}),
    ...(auth.ExpiresIn !== undefined
      ? { expiresAt: Date.now() + auth.ExpiresIn * 1000 }
      : {}),
  };
}

/**
 * The conversation's runtime session id, generated once per tab and reused.
 *
 * This is what ties successive `/invocations` calls into one ongoing conversation, so the agent's
 * S3-backed memory accumulates turns instead of restarting on every message.
 *
 * A stored value shorter than the platform minimum is replaced rather than sent. That guard exists
 * because a blank or truncated value is rejected by AgentCore, and the resulting failure describes a
 * header length rather than the stale storage that caused it.
 */
export function runtimeSessionId(): string {
  const existing = sessionStorage.getItem(RUNTIME_SESSION_KEY);
  if (existing && existing.length >= MIN_RUNTIME_SESSION_ID_LENGTH) return existing;
  return newRuntimeSessionId();
}

/**
 * Mints a fresh runtime session id, discarding the current one.
 *
 * What "new session" means, and it has to happen on this side rather than by clearing the transcript:
 * the agent's memory is keyed on this id, so a cleared transcript with the id kept would leave the
 * agent still answering in the context of turns the reader can no longer see.
 */
export function newRuntimeSessionId(): string {
  const fresh = `adcp-buyer-agent-ui-${crypto.randomUUID()}`;
  sessionStorage.setItem(RUNTIME_SESSION_KEY, fresh);
  return fresh;
}

/**
 * Adopts an existing recorded session id as the conversation's runtime session id, so the next turn
 * CONTINUES that conversation rather than starting a new one. This is what "resume" is built on: the
 * agent's memory is keyed on this id, so adopting it is what lets a resumed turn answer in the
 * context of the earlier ones (the backend's S3-backed session memory is restored on the next call).
 *
 * Rejects a value shorter than the platform minimum rather than storing it: AgentCore refuses a
 * too-short session-id header, and the resulting failure would describe a length instead of the
 * resume that caused it. Returns whether the id was adopted.
 */
export function adoptRuntimeSessionId(id: string): boolean {
  if (id.length < MIN_RUNTIME_SESSION_ID_LENGTH) return false;
  sessionStorage.setItem(RUNTIME_SESSION_KEY, id);
  return true;
}

/**
 * Whether a stored session's token has expired.
 *
 * A session with no recorded expiry is treated as usable: Cognito did not say when it ends, and
 * refusing to send a token that may well be valid would lock someone out on a guess. The runtime
 * rejects a genuinely expired token regardless, so the cost of being wrong here is a 401 rather than
 * unauthorised access.
 */
export function isSessionExpired(session: StoredSession | null): boolean {
  if (!session) return true;
  if (session.expiresAt === undefined) return false;
  return Date.now() >= session.expiresAt;
}

/**
 * The Authorization header for the stored session, or null when there is nothing usable.
 *
 * Returns null for an expired session rather than sending a token that is certain to be refused, so
 * the caller reports "not signed in" instead of an opaque 401.
 */
export function authHeader(session: StoredSession | null): string | null {
  if (!session || isSessionExpired(session)) return null;
  return `Bearer ${session.accessToken}`;
}
