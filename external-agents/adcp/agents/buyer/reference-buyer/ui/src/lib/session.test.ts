import { beforeEach, describe, expect, it } from 'vitest';

import {
  MIN_RUNTIME_SESSION_ID_LENGTH,
  adoptRuntimeSessionId,
  authHeader,
  clearSession,
  isSessionExpired,
  loadSession,
  newRuntimeSessionId,
  runtimeSessionId,
  saveSession,
  sessionFromAuthResult,
} from './session';

beforeEach(() => {
  sessionStorage.clear();
});

describe('saveSession / loadSession', () => {
  it('round-trips a session', () => {
    saveSession({ username: 'sam', accessToken: 'tok' });
    expect(loadSession()).toEqual({ username: 'sam', accessToken: 'tok' });
  });

  it('returns null when nothing is stored', () => {
    expect(loadSession()).toBeNull();
  });

  it('returns null for corrupted storage rather than throwing', () => {
    sessionStorage.setItem('adcp_buyer_agent_session', '{not json');
    expect(loadSession()).toBeNull();
  });

  it('treats a stored value with no token as absent', () => {
    // Handing back a session that cannot authenticate anything is worse than reporting none.
    sessionStorage.setItem('adcp_buyer_agent_session', JSON.stringify({ username: 'sam' }));
    expect(loadSession()).toBeNull();
    sessionStorage.setItem(
      'adcp_buyer_agent_session',
      JSON.stringify({ username: 'sam', accessToken: '' }),
    );
    expect(loadSession()).toBeNull();
  });

  it('omits optional fields rather than storing undefined', () => {
    saveSession({ username: 'sam', accessToken: 'tok' });
    expect(Object.keys(loadSession() ?? {})).toEqual(['username', 'accessToken']);
  });
});

describe('clearSession', () => {
  it('clears the runtime session id too, so a new sign-in starts a new conversation', () => {
    saveSession({ username: 'sam', accessToken: 'tok' });
    const before = runtimeSessionId();
    clearSession();
    expect(loadSession()).toBeNull();
    // Leaving this behind would let a new user inherit the previous conversation's memory.
    expect(runtimeSessionId()).not.toBe(before);
  });
});

describe('sessionFromAuthResult', () => {
  it('carries the tokens across', () => {
    const stored = sessionFromAuthResult('sam', {
      AccessToken: 'a',
      IdToken: 'i',
      RefreshToken: 'r',
    });
    expect(stored).toMatchObject({ username: 'sam', accessToken: 'a', idToken: 'i', refreshToken: 'r' });
  });

  it('converts ExpiresIn into an absolute expiry', () => {
    const stored = sessionFromAuthResult('sam', { AccessToken: 'a', ExpiresIn: 3600 });
    expect(stored.expiresAt).toBeGreaterThan(Date.now());
  });

  it('leaves expiry unset when Cognito did not say', () => {
    expect(sessionFromAuthResult('sam', { AccessToken: 'a' }).expiresAt).toBeUndefined();
  });
});

describe('runtimeSessionId', () => {
  it('is stable within a tab, so turns accumulate in one conversation', () => {
    expect(runtimeSessionId()).toBe(runtimeSessionId());
  });

  it('meets the platform minimum length', () => {
    expect(runtimeSessionId().length).toBeGreaterThanOrEqual(MIN_RUNTIME_SESSION_ID_LENGTH);
  });

  it('replaces a stored value that is too short to be accepted', () => {
    // AgentCore rejects anything shorter, and the resulting error describes a header length
    // rather than the stale storage behind it.
    sessionStorage.setItem('adcp_buyer_agent_runtime_session_id', 'too-short');
    const id = runtimeSessionId();
    expect(id).not.toBe('too-short');
    expect(id.length).toBeGreaterThanOrEqual(MIN_RUNTIME_SESSION_ID_LENGTH);
  });

  it('replaces a blank stored value', () => {
    sessionStorage.setItem('adcp_buyer_agent_runtime_session_id', '');
    expect(runtimeSessionId().length).toBeGreaterThanOrEqual(MIN_RUNTIME_SESSION_ID_LENGTH);
  });
});

describe('authHeader', () => {
  it('builds a bearer header, or nothing when signed out', () => {
    expect(authHeader({ username: 'sam', accessToken: 'tok' })).toBe('Bearer tok');
    expect(authHeader(null)).toBeNull();
  });

  it('refuses an expired token rather than sending one certain to be rejected', () => {
    expect(
      authHeader({ username: 'sam', accessToken: 'tok', expiresAt: Date.now() - 1000 }),
    ).toBeNull();
  });

  it('sends a token that has not expired yet', () => {
    expect(
      authHeader({ username: 'sam', accessToken: 'tok', expiresAt: Date.now() + 60_000 }),
    ).toBe('Bearer tok');
  });
});

describe('isSessionExpired', () => {
  it('treats a session with no recorded expiry as usable', () => {
    // Cognito did not say when it ends, and refusing on a guess would lock someone out. The runtime
    // rejects a genuinely expired token anyway, so the cost of being wrong is a 401.
    expect(isSessionExpired({ username: 'sam', accessToken: 'tok' })).toBe(false);
  });

  it('reports an elapsed expiry', () => {
    expect(
      isSessionExpired({ username: 'sam', accessToken: 'tok', expiresAt: Date.now() - 1 }),
    ).toBe(true);
  });

  it('treats absence of a session as expired', () => {
    expect(isSessionExpired(null)).toBe(true);
  });
});

describe('newRuntimeSessionId', () => {
  it('replaces the current id, so the agent starts a conversation with no history', () => {
    const before = runtimeSessionId();
    const after = newRuntimeSessionId();
    expect(after).not.toBe(before);
    // And it is what subsequent turns use, not just a value that was returned and dropped.
    expect(runtimeSessionId()).toBe(after);
  });

  it('still meets the platform minimum length', () => {
    expect(newRuntimeSessionId().length).toBeGreaterThanOrEqual(MIN_RUNTIME_SESSION_ID_LENGTH);
  });
});

describe('adoptRuntimeSessionId', () => {
  it('adopts an existing id so the next turn continues that recorded conversation', () => {
    const existing = `adcp-buyer-agent-ui-${'0'.repeat(36)}`;
    expect(adoptRuntimeSessionId(existing)).toBe(true);
    // The adopted id is what subsequent turns use, which is what ties them to the same agent memory.
    expect(runtimeSessionId()).toBe(existing);
  });

  it('refuses an id shorter than the platform minimum rather than storing an unusable one', () => {
    const before = runtimeSessionId();
    expect(adoptRuntimeSessionId('too-short')).toBe(false);
    // Storage is left as it was, so a failed adopt does not blank the current conversation.
    expect(runtimeSessionId()).toBe(before);
  });
});
