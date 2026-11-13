/**
 * Session agent tracking: which agents took part in a session (FR1.2) and
 * that resuming keeps the same session id (FR6.1).
 */
// The @Injectable decorator is a no-op outside Angular.
jest.mock('@angular/core', () => ({
  Injectable: () => (target: any) => target,
}));
// uuid 14 ships ESM only, which ts-jest in CommonJS mode cannot load.
let mockUuidCounter = 0;
jest.mock('uuid', () => ({
  v4: () => {
    mockUuidCounter += 1;
    return `00000000-0000-4000-8000-${mockUuidCounter.toString(16).padStart(12, '0')}`;
  },
}));

import { SessionManagerService } from './session-manager.service';

describe('SessionManagerService agent tracking', () => {
  let service: SessionManagerService;

  beforeEach(() => {
    service = new SessionManagerService();
  });

  it('records each invoked agent once, in order', () => {
    const session = service.getOrCreateSession('tab-a');

    service.recordAgent(session.sessionId, 'AgencyAgent');
    service.recordAgent(session.sessionId, 'Media_Planning_Agent');
    service.recordAgent(session.sessionId, 'AgencyAgent');
    service.recordAgent(session.sessionId, '   ');
    service.recordAgent('not-a-session-id-from-this-page-0', 'AgencyAgent');

    expect(service.getOrCreateSession('tab-a').agentNames).toEqual(['AgencyAgent', 'Media_Planning_Agent']);
    expect(service.hasSession(session.sessionId)).toBe(true);
    expect(service.hasSession('not-a-session-id-from-this-page-0')).toBe(false);
  });

  it('switchSession returns the same session id with its agents', () => {
    const first = service.getOrCreateSession('tab-a');
    service.recordAgent(first.sessionId, 'AgencyAgent');
    const second = service.forceNewSession('tab-a');
    expect(second.sessionId).not.toBe(first.sessionId);

    const resumed = service.switchSession(first.sessionId, 'tab-a');

    expect(resumed?.sessionId).toBe(first.sessionId);
    expect(resumed?.agentNames).toEqual(['AgencyAgent']);
    expect(service.getCurrentSessionId(null, null, 'tab-a')).toBe(first.sessionId);
    expect(service.switchSession(first.sessionId, 'tab-b')).toBeNull();
  });
});
