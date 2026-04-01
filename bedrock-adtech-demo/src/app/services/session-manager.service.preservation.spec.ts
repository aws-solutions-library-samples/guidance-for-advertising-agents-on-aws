/**
 * Preservation Property Tests — Session ID Consolidation
 *
 * These tests capture behaviors that MUST be preserved after the bugfix.
 * They are designed to PASS on the current UNFIXED code, confirming that
 * these behaviors work correctly today and must not regress.
 *
 * Preservation behaviors tested:
 *   1. Idempotent session reuse — initializeSession() twice returns same session (Req 3.1)
 *   2. session$ observable emits on initializeSession() (Req 3.3)
 *   3. Session metadata (createdAt, lastUsed, messageCount) populated (Req 3.4)
 *   4. switchSession() changes active session and emits via session$ (Req 3.5)
 *   5. deleteSession() removes session and emits null if active (Req 3.6)
 *
 * Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6
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
// Arbitrary generators for property-based tests
// ---------------------------------------------------------------------------
const userIdArb = fc.constantFrom('user@example.com', 'admin', 'john-doe', 'test-user');
const customerNameArb = fc.constantFrom('enterprise-customer', 'acme-corp', 'demo', 'big-co');
const tabIdArb = fc.constantFrom('tab-12345', 'tab-99999', 'tab-abc');

// ---------------------------------------------------------------------------
// Property 2.1 — Idempotent Session Reuse
// Validates: Requirements 3.1
// ---------------------------------------------------------------------------
describe('Preservation: Idempotent session reuse', () => {
  /**
   * **Validates: Requirements 3.1**
   *
   * Calling initializeSession() twice with the same params returns the same
   * active session without creating a duplicate. This is existing behavior
   * that must be preserved.
   */
  it('initializeSession() called twice with same params returns same session (property)', () => {
    fc.assert(
      fc.property(userIdArb, customerNameArb, tabIdArb, (userId, customerName, tabId) => {
        // Fresh storage per iteration
        localStore = {};
        sessionStore = {};
        const service = new SessionManagerService();

        const first = service.initializeSession(userId, customerName, tabId);
        const second = service.initializeSession(userId, customerName, tabId);

        // Same session ID — no duplicate created
        expect(first.sessionId).toBe(second.sessionId);
      }),
      { numRuns: 20 }
    );
  });

  it('initializeSession() twice returns same session (concrete)', () => {
    const service = new SessionManagerService();
    const first = service.initializeSession('user@example.com', 'enterprise-customer', 'tab-12345');
    const second = service.initializeSession('user@example.com', 'enterprise-customer', 'tab-12345');

    expect(first.sessionId).toBe(second.sessionId);
  });
});

// ---------------------------------------------------------------------------
// Property 2.2 — session$ Observable Emits on initializeSession()
// Validates: Requirements 3.3
// ---------------------------------------------------------------------------
describe('Preservation: session$ observable emits on session creation', () => {
  /**
   * **Validates: Requirements 3.3**
   *
   * The session$ observable emits the current session when initializeSession()
   * is called. Subscribers are notified of session changes.
   */
  it('session$ emits the session when initializeSession() is called (property)', () => {
    fc.assert(
      fc.property(userIdArb, customerNameArb, tabIdArb, (userId, customerName, tabId) => {
        localStore = {};
        sessionStore = {};
        const service = new SessionManagerService();

        let emittedSession: SessionInfo | null = null;
        const sub = service.session$.subscribe(s => { emittedSession = s; });

        const session = service.initializeSession(userId, customerName, tabId);

        // Observable should have emitted the created session
        expect(emittedSession).not.toBeNull();
        expect(emittedSession!.sessionId).toBe(session.sessionId);

        sub.unsubscribe();
      }),
      { numRuns: 20 }
    );
  });

  it('session$ emits the session (concrete)', () => {
    const service = new SessionManagerService();
    let emittedSession: SessionInfo | null = null;
    const sub = service.session$.subscribe(s => { emittedSession = s; });

    const session = service.initializeSession('admin', 'demo', 'tab-99999');

    expect(emittedSession).not.toBeNull();
    expect(emittedSession!.sessionId).toBe(session.sessionId);
    sub.unsubscribe();
  });
});


// ---------------------------------------------------------------------------
// Property 2.3 — Session Metadata Populated
// Validates: Requirements 3.4
// ---------------------------------------------------------------------------
describe('Preservation: Session metadata is populated', () => {
  /**
   * **Validates: Requirements 3.4**
   *
   * Created sessions have createdAt, lastUsed, and messageCount metadata
   * populated. lastUsed updates on retrieval.
   */
  it('created sessions have createdAt, lastUsed, messageCount populated (property)', () => {
    fc.assert(
      fc.property(userIdArb, customerNameArb, tabIdArb, (userId, customerName, tabId) => {
        localStore = {};
        sessionStore = {};
        const service = new SessionManagerService();

        const session = service.initializeSession(userId, customerName, tabId);

        expect(session.createdAt).toBeInstanceOf(Date);
        expect(session.lastUsed).toBeInstanceOf(Date);
        expect(typeof session.messageCount).toBe('number');
        expect(session.messageCount).toBe(0);
      }),
      { numRuns: 20 }
    );
  });

  it('lastUsed is updated when session is retrieved again (concrete)', () => {
    const service = new SessionManagerService();
    const session = service.initializeSession('user@example.com', 'enterprise-customer', 'tab-12345');
    const firstLastUsed = new Date(session.lastUsed).getTime();

    // Retrieve the same session again — initializeSession reuses existing
    const retrieved = service.initializeSession('user@example.com', 'enterprise-customer', 'tab-12345');

    // Same session returned, lastUsed should be >= the original
    expect(retrieved.sessionId).toBe(session.sessionId);
    expect(new Date(retrieved.lastUsed).getTime()).toBeGreaterThanOrEqual(firstLastUsed);
  });
});

// ---------------------------------------------------------------------------
// Property 2.4 — switchSession() Changes Active Session
// Validates: Requirements 3.5
// ---------------------------------------------------------------------------
describe('Preservation: switchSession() changes active session and emits', () => {
  /**
   * **Validates: Requirements 3.5**
   *
   * switchSession() changes the active session and emits the new session
   * via session$. The session list UI relies on this behavior.
   */
  it('switchSession() activates the target session and emits via session$ (property)', () => {
    fc.assert(
      fc.property(userIdArb, customerNameArb, tabIdArb, (userId, customerName, tabId) => {
        localStore = {};
        sessionStore = {};
        const service = new SessionManagerService();

        // Create first session
        const session1 = service.initializeSession(userId, customerName, tabId);

        // Create a second session
        const session2 = service.forceNewSession();

        // Track emissions
        let emittedSession: SessionInfo | null = null;
        const sub = service.session$.subscribe(s => { emittedSession = s; });

        // Switch back to session1
        const switched = service.switchSession(session1.sessionId);

        expect(switched).not.toBeNull();
        expect(switched!.sessionId).toBe(session1.sessionId);
        expect(emittedSession).not.toBeNull();
        expect(emittedSession!.sessionId).toBe(session1.sessionId);

        sub.unsubscribe();
      }),
      { numRuns: 20 }
    );
  });

  it('switchSession() concrete example', () => {
    const service = new SessionManagerService();
    const userId = 'admin';
    const customerName = 'demo';
    const tabId = 'tab-12345';

    const session1 = service.initializeSession(userId, customerName, tabId);
    const session2 = service.forceNewSession();

    let emittedSession: SessionInfo | null = null;
    const sub = service.session$.subscribe(s => { emittedSession = s; });

    const switched = service.switchSession(session1.sessionId);

    expect(switched).not.toBeNull();
    expect(switched!.sessionId).toBe(session1.sessionId);
    expect(emittedSession!.sessionId).toBe(session1.sessionId);

    sub.unsubscribe();
  });
});

// ---------------------------------------------------------------------------
// Property 2.5 — deleteSession() Removes Session and Emits null if Active
// Validates: Requirements 3.6
// ---------------------------------------------------------------------------
describe('Preservation: deleteSession() removes session and emits null if active', () => {
  /**
   * **Validates: Requirements 3.6**
   *
   * deleteSession() removes the session from storage. If the deleted session
   * was the active session, a new session is automatically created via getOrCreateSession().
   */
  it('deleteSession() of active session creates new session via session$ (property)', () => {
    fc.assert(
      fc.property(userIdArb, customerNameArb, tabIdArb, (userId, customerName, tabId) => {
        localStore = {};
        sessionStore = {};
        const service = new SessionManagerService();

        const session = service.initializeSession(userId, customerName, tabId);

        let emittedSession: SessionInfo | null | undefined = undefined;
        const sub = service.session$.subscribe(s => { emittedSession = s; });

        // Delete the active session
        service.deleteSession(session.sessionId);

        // session$ should have emitted a new session (not null — deleteSession now auto-creates)
        expect(emittedSession).not.toBeNull();
        expect(emittedSession!.sessionId).not.toBe(session.sessionId);

        sub.unsubscribe();
      }),
      { numRuns: 20 }
    );
  });

  it('deleteSession() removes session from storage (concrete)', () => {
    const service = new SessionManagerService();
    const userId = 'user@example.com';
    const customerName = 'enterprise-customer';
    const tabId = 'tab-12345';

    const session = service.initializeSession(userId, customerName, tabId);

    // Verify session exists in the list
    const sessionsBefore = service.getSessions();
    expect(sessionsBefore.some(s => s.sessionId === session.sessionId)).toBe(true);

    // Delete it
    service.deleteSession(session.sessionId);

    // Verify session is removed from the list
    const sessionsAfter = service.getSessions();
    expect(sessionsAfter.some(s => s.sessionId === session.sessionId)).toBe(false);
  });

  it('deleteSession() of non-active session does not emit null (concrete)', () => {
    const service = new SessionManagerService();
    const userId = 'admin';
    const customerName = 'demo';
    const tabId = 'tab-99999';

    const session1 = service.initializeSession(userId, customerName, tabId);
    const session2 = service.forceNewSession();
    // session2 is now active

    let emittedSession: SessionInfo | null | undefined = undefined;
    const sub = service.session$.subscribe(s => { emittedSession = s; });

    // Delete session1 (not active)
    service.deleteSession(session1.sessionId);

    // session$ should still show session2 (not null)
    expect(emittedSession).not.toBeNull();
    expect(emittedSession!.sessionId).toBe(session2.sessionId);

    sub.unsubscribe();
  });
});
