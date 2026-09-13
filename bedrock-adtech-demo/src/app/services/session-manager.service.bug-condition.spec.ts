/**
 * Bug Condition Exploration Test — Session ID Consolidation
 *
 * These tests encode the EXPECTED (fixed) behavior. They are designed to
 * FAIL on the current unfixed code, confirming the bugs exist.
 *
 * Bug conditions tested:
 *   1. Session IDs exceed 34 chars and contain non-hex characters (Req 1.1, 2.1)
 *   2. Multiple entry points produce inconsistent results (Req 1.2, 2.2)
 *   3. Storage keys contain tab identifiers (Req 1.4, 2.4)
 *   4. SessionInfo contains a tabId field (Req 1.5, 2.5)
 *
 * Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 2.1, 2.2
 */

// Mock @angular/core so the @Injectable decorator is a no-op
jest.mock('@angular/core', () => ({
  Injectable: () => (target: any) => target,
}));

import * as fc from 'fast-check';
import { SessionManagerService, SessionInfo } from './session-manager.service';

// ---------------------------------------------------------------------------
// Browser API mocks (tests run in Node via Jest)
// ---------------------------------------------------------------------------
let localStore: Record<string, string>;
let sessionStore: Record<string, string>;

function makeStorageMock(store: () => Record<string, string>): Storage {
  return {
    getItem: (key: string) => store()[key] ?? null,
    setItem: (key: string, value: string) => { store()[key] = value; },
    removeItem: (key: string) => { delete store()[key]; },
    clear: () => { Object.keys(store()).forEach(k => delete store()[k]); },
    get length() { return Object.keys(store()).length; },
    key: (index: number) => Object.keys(store())[index] ?? null,
  };
}

beforeAll(() => {
  (globalThis as any).window = (globalThis as any).window || {};
  (globalThis as any).window.crypto = {
    getRandomValues: (arr: Uint8Array) => {
      for (let i = 0; i < arr.length; i++) arr[i] = Math.floor(Math.random() * 256);
      return arr;
    },
  };
});

beforeEach(() => {
  localStore = {};
  sessionStore = {};
  (globalThis as any).localStorage = makeStorageMock(() => localStore);
  (globalThis as any).sessionStorage = makeStorageMock(() => sessionStore);
});

// ---------------------------------------------------------------------------
// Property 1 — Session ID Length and Format
// Validates: Requirements 1.1, 2.1
// ---------------------------------------------------------------------------
describe('Bug Condition: Session ID length and format', () => {
  /**
   * **Validates: Requirements 1.1, 2.1**
   *
   * The EXPECTED behavior after the fix: session IDs are at most 34 chars
   * and contain only hex characters [0-9a-f].
   *
   * On UNFIXED code this MUST FAIL because IDs are 50-100 chars with hyphens
   * and alphanumeric user/customer strings.
   */
  it('should produce session IDs at most 34 chars of only hex characters (property)', () => {
    fc.assert(
      fc.property(
        fc.constantFrom(
          { userId: 'user@example.com', customerName: 'enterprise-customer', tabId: 'tab-12345' },
          { userId: 'john-doe', customerName: 'acme-corp', tabId: 'tab-99999' },
          { userId: 'admin', customerName: 'demo', tabId: 'tab-abc-def' },
          { userId: 'long-user-name@enterprise.org', customerName: 'big-company-inc', tabId: 'tab-00001' },
        ),
        ({ userId, customerName, tabId }) => {
          // Fresh service + storage per iteration
          localStore = {};
          sessionStore = {};
          const service = new SessionManagerService();
          const session = service.initializeSession(userId, customerName, tabId);
          const id = session.sessionId;

          // Expected behavior: ID is at most 34 chars, only hex
          expect(id.length).toBeLessThanOrEqual(34);
          expect(id).toMatch(/^[0-9a-f]+$/);
        }
      ),
      { numRuns: 20 }
    );
  });

  /**
   * Concrete example that directly demonstrates the bug.
   */
  it('generateSessionId with typical inputs should produce <=34 hex-only chars', () => {
    const service = new SessionManagerService();
    const session = service.initializeSession(
      'user@example.com',
      'enterprise-customer',
      'tab-12345'
    );
    const id = session.sessionId;

    // Expected: at most 34 chars, hex only
    expect(id.length).toBeLessThanOrEqual(34);
    expect(id).toMatch(/^[0-9a-f]+$/);
  });
});

// ---------------------------------------------------------------------------
// Property 2 — Multiple Entry Point Consistency
// Validates: Requirements 1.2, 2.2
// ---------------------------------------------------------------------------
describe('Bug Condition: Multiple entry points produce same session ID', () => {
  /**
   * **Validates: Requirements 1.2, 2.2**
   *
   * The EXPECTED behavior: initializeSession() and getOrCreateSession() called
   * with the same inputs should return the same session ID (single source of
   * truth). createNewSession() has been removed — forceNewSession() is the
   * only way to create a new session when a valid one exists.
   */
  it('initializeSession and getOrCreateSession with same inputs should return same session ID', () => {
    const service = new SessionManagerService();
    const userId = 'user@example.com';
    const customerName = 'enterprise-customer';
    const tabId = 'tab-12345';

    const sessionA = service.initializeSession(userId, customerName, tabId);
    const sessionB = service.getOrCreateSession();

    // Expected: both return the same session (single entry point)
    expect(sessionA.sessionId).toBe(sessionB.sessionId);
  });
});

// ---------------------------------------------------------------------------
// Property 3 — Storage Key Must Not Contain Tab Identifiers
// Validates: Requirements 1.4, 2.4
// ---------------------------------------------------------------------------
describe('Bug Condition: Storage key does not contain tab identifiers', () => {
  /**
   * **Validates: Requirements 1.4, 2.4**
   *
   * The EXPECTED behavior: localStorage keys used for session storage do NOT
   * contain any tab identifier. On UNFIXED code this MUST FAIL because
   * getStorageKey() embeds the tabId in the key.
   */
  it('nothing is written to browser storage at all', () => {
    const service = new SessionManagerService();

    service.initializeSession('user@example.com', 'enterprise-customer', 'tab-12345');
    service.forceNewSession('tab-67890');

    // Superseded the original "keys must not contain a tab identifier" check:
    // sessions are no longer persisted, so there must be no key to inspect.
    // Persisting them handed a reloaded page a session id whose server-side
    // conversation it could no longer see.
    expect(Object.keys(localStore)).toHaveLength(0);
    expect(Object.keys(sessionStore)).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// Property 4 — Sessions Are Owned By A Tab
// Supersedes the original Requirements 1.5 / 2.5
// ---------------------------------------------------------------------------
describe('Sessions are owned by a tab', () => {
  /**
   * REVERSAL, recorded deliberately. The original requirement was that
   * SessionInfo must NOT carry a tabId: sessions were consolidated onto one
   * app-wide active id to stop tab-scoped storage keys fragmenting them.
   *
   * That produced the opposite defect. Each tab runs its own conversation, so a
   * single active id meant whichever tab initialised last silently re-keyed the
   * others — a tab's transcript stayed on screen while the session id sent with
   * its next message changed, and the agent had no history to restore.
   *
   * Sessions are therefore owned by a tab again. What is NOT coming back is the
   * tab-scoped storage key, because nothing is persisted at all.
   */
  it('a session belongs to the tab that created it', () => {
    const service = new SessionManagerService();
    const session = service.initializeSession(
      'user@example.com',
      'enterprise-customer',
      'tab-12345'
    );

    expect(session.tabId).toBe('tab-12345');
  });

  it('two tabs get two different sessions, and neither steals the other', () => {
    const service = new SessionManagerService();

    const tabA = service.getOrCreateSession('tab-a');
    const tabB = service.getOrCreateSession('tab-b');

    expect(tabA.sessionId).not.toBe(tabB.sessionId);
    // Re-asking for tab A's session must still return tab A's, not the one that
    // happened to be created most recently.
    expect(service.getOrCreateSession('tab-a').sessionId).toBe(tabA.sessionId);
    expect(service.getOrCreateSession('tab-b').sessionId).toBe(tabB.sessionId);
  });

  it('a tab only lists and switches to its own sessions', () => {
    const service = new SessionManagerService();

    const tabA = service.getOrCreateSession('tab-a');
    const tabB = service.getOrCreateSession('tab-b');

    expect(service.getSessions('tab-a').map(s => s.sessionId)).toEqual([tabA.sessionId]);
    expect(service.getSessions('tab-b').map(s => s.sessionId)).toEqual([tabB.sessionId]);
    // tab B cannot adopt tab A's session
    expect(service.switchSession(tabA.sessionId, 'tab-b')).toBeNull();
  });

  it('forceNewSession replaces only the calling tab\'s session', () => {
    const service = new SessionManagerService();

    const tabA = service.getOrCreateSession('tab-a');
    const tabB = service.getOrCreateSession('tab-b');

    const tabAFresh = service.forceNewSession('tab-a');

    expect(tabAFresh.sessionId).not.toBe(tabA.sessionId);
    expect(service.getOrCreateSession('tab-b').sessionId).toBe(tabB.sessionId);
  });
});
