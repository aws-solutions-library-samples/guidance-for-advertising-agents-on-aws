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
  it('localStorage keys should not contain tab identifiers', () => {
    const service = new SessionManagerService();
    const tabId = 'tab-12345';

    service.initializeSession('user@example.com', 'enterprise-customer', tabId);

    const keys = Object.keys(localStore);
    for (const key of keys) {
      expect(key).not.toContain('tab-12345');
      expect(key).not.toContain('tab-');
      expect(key).not.toMatch(/tab-[a-zA-Z0-9]/);
    }
  });
});

// ---------------------------------------------------------------------------
// Property 4 — SessionInfo Must Not Have tabId Populated
// Validates: Requirements 1.5, 2.5
// ---------------------------------------------------------------------------
describe('Bug Condition: SessionInfo does not have tabId field populated', () => {
  /**
   * **Validates: Requirements 1.5, 2.5**
   *
   * The EXPECTED behavior: SessionInfo returned from session creation does NOT
   * have a tabId field populated. On UNFIXED code this MUST FAIL because
   * createNewSessionInternal sets tabId on the SessionInfo object.
   */
  it('SessionInfo should not have a tabId field populated', () => {
    const service = new SessionManagerService();
    const session = service.initializeSession(
      'user@example.com',
      'enterprise-customer',
      'tab-12345'
    );

    // tabId should be undefined/absent — not populated from tab-tracking logic
    expect(session.tabId).toBeUndefined();
  });
});
