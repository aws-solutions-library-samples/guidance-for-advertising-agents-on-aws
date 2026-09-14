/**
 * An expired stored session must count as not signed in.
 *
 * From a live defect. The journey view sat there reporting "No sessions recorded yet." and ignoring
 * new sessions, while the backend was healthy and returning 31 of them. The chain:
 *
 *   1. `loadSession()` returned an hour-old session, so `auth.session !== null`.
 *   2. `App` therefore rendered the journey view instead of the login screen, with `enabled=true`.
 *   3. `authHeader()` DOES check expiry, so it returned null.
 *   4. `useInvoke` threw "Not signed in." **before issuing any fetch**.
 *   5. The session-list poll caught that and swallowed it.
 *
 * Step 4 is why it was so hard to see: no request was ever sent, so there was no status code, no
 * CloudWatch entry and nothing for anyone to find server-side. Access tokens last one hour, so this
 * was reached in normal use rather than as an edge case.
 */
import { beforeEach, describe, expect, it } from 'vitest';

import { loadSession, saveSession, type StoredSession } from '../lib/session';

const HOUR = 60 * 60 * 1000;

function stored(expiresAt: number | undefined): StoredSession {
  return {
    username: 'testuser',
    accessToken: 'header.payload.signature',
    idToken: 'header.payload.signature',
    ...(expiresAt === undefined ? {} : { expiresAt }),
  } as StoredSession;
}

/**
 * The mount-time gate from `useAuth`, as a plain function.
 *
 * Duplicated deliberately rather than rendering the hook: `useAuth` needs a Cognito config and the
 * behaviour under test is the initial-state decision, which is pure. `useAuth.ts` carries a comment
 * pointing here.
 */
function gate(): StoredSession | null {
  const s = loadSession();
  if (s && s.expiresAt !== undefined && Date.now() >= s.expiresAt) return null;
  return s;
}

describe('the mount-time session gate', () => {
  beforeEach(() => {
    // sessionStorage, not localStorage: tokens live for the tab (see lib/session.ts).
    window.sessionStorage.clear();
  });

  it('accepts a session that has not expired', () => {
    saveSession(stored(Date.now() + HOUR));
    expect(gate()).not.toBeNull();
  });

  it('rejects a session that expired an hour ago', () => {
    saveSession(stored(Date.now() - HOUR));
    expect(gate()).toBeNull();
  });

  it('rejects a session that expired one millisecond ago', () => {
    saveSession(stored(Date.now() - 1));
    expect(gate()).toBeNull();
  });

  it('accepts a session with no recorded expiry', () => {
    // `isSessionExpired` documents this: Cognito did not say when it ends, and the platform rejects a
    // genuinely dead token anyway, so the cost of being wrong is a visible 401 rather than silence.
    saveSession(stored(undefined));
    expect(gate()).not.toBeNull();
  });

  it('returns null when nothing is stored', () => {
    expect(gate()).toBeNull();
  });
});
