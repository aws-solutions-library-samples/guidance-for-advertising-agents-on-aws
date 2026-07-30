// Mock @angular/core so the @Injectable decorator is a no-op — this avoids
// loading Angular's ESM-only package build under this repo's CommonJS
// Jest/ts-jest config (same workaround already used by
// session-manager.service.bug-condition.spec.ts).
jest.mock('@angular/core', () => ({
  Injectable: () => (target: any) => target,
}));

import { NotifyDispatchService } from './notify-dispatch.service';
import { AwsConfigService } from './aws-config.service';
import { AgentDynamoDBService } from './agent-dynamodb.service';
import { EnrichedAgent } from '../models/application-models';

/**
 * Unit tests for NotifyDispatchService.
 * Feature: a2a-invocation-notify-hook
 */

function makeAgent(overrides: Partial<EnrichedAgent> = {}): EnrichedAgent {
  return {
    name: 'TestAgent',
    agentType: 'TestAgent',
    status: 'active',
    id: 'TestAgent',
    aliasId: 'latest',
    displayName: 'Test Agent',
    color: '#6842ff',
    icon: 'smart_toy',
    alternativeNames: [],
    description: 'A test agent',
    key: 'TestAgent',
    ...overrides,
  } as EnrichedAgent;
}

describe('NotifyDispatchService', () => {
  let service: NotifyDispatchService;
  let mockAwsConfig: jest.Mocked<Pick<AwsConfigService, 'getCachedAuthSession' | 'getRegion'>>;
  let mockAgentDynamoDBService: jest.Mocked<Pick<AgentDynamoDBService, 'getNotifyBearerToken'>>;
  let fetchMock: jest.Mock;

  beforeEach(() => {
    mockAwsConfig = {
      getCachedAuthSession: jest.fn().mockResolvedValue({
        credentials: { accessKeyId: 'AKIA', secretAccessKey: 'secret', sessionToken: 'token' },
      }),
      getRegion: jest.fn().mockReturnValue('us-east-1'),
    };
    mockAgentDynamoDBService = {
      getNotifyBearerToken: jest.fn().mockResolvedValue(null),
    };

    fetchMock = jest.fn().mockResolvedValue({ ok: true, status: 200 } as any);
    (global as any).fetch = fetchMock;

    // Instantiate directly (no Angular TestBed / DI container) — this
    // service has exactly two constructor dependencies, both easily mocked,
    // and this repo's own test infra cannot load @angular/core/testing (an
    // ESM-only build) under the current Jest/ts-jest CommonJS config; see
    // the pre-existing, unrelated failure in visualization-cache.service.spec.ts.
    service = new NotifyDispatchService(
      mockAwsConfig as unknown as AwsConfigService,
      mockAgentDynamoDBService as unknown as AgentDynamoDBService,
    );
  });

  afterEach(() => {
    jest.clearAllMocks();
  });

  // Small helper to let pending microtasks (the fire-and-forget promise chain) flush.
  const flush = () => new Promise(resolve => setImmediate(resolve));

  it('is a no-op when notify_on_invocation is absent', async () => {
    const agent = makeAgent();
    service.dispatchIfConfigured(agent, { sessionId: 's1', invocationPayload: { prompt: 'hi' } });
    await flush();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('is a no-op when notify_on_invocation has no endpoint', async () => {
    const agent = makeAgent({ notify_on_invocation: { endpoint: '', auth_type: 'none' } });
    service.dispatchIfConfigured(agent, { sessionId: 's1', invocationPayload: { prompt: 'hi' } });
    await flush();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('sends a POST with the correct payload shape for auth_type none', async () => {
    const agent = makeAgent({
      notify_on_invocation: { endpoint: 'https://example.com/hook', auth_type: 'none' },
    });
    const invocationPayload = { prompt: 'hello', session_id: 's1', agent_name: 'TestAgent' };

    service.dispatchIfConfigured(agent, { sessionId: 's1', invocationPayload });
    await flush();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('https://example.com/hook');
    expect(init.method).toBe('POST');
    expect(init.headers['Content-Type']).toBe('application/json');
    expect(init.headers['Authorization']).toBeUndefined();

    const body = JSON.parse(init.body);
    expect(body.sessionId).toBe('s1');
    expect(body.stepType).toBe('incoming_request');
    expect(body.content).toEqual(invocationPayload);
    expect(typeof body.timestamp).toBe('string');
    expect(new Date(body.timestamp).toString()).not.toBe('Invalid Date');
    expect(body.stepIndex).toBe(0);
  });

  it('increments stepIndex per call for the same sessionId, starting at 0', async () => {
    const agent = makeAgent({
      notify_on_invocation: { endpoint: 'https://example.com/hook', auth_type: 'none' },
    });

    for (let i = 0; i < 3; i++) {
      service.dispatchIfConfigured(agent, { sessionId: 'shared-session', invocationPayload: { prompt: `turn ${i}` } });
    }
    await flush();

    expect(fetchMock).toHaveBeenCalledTimes(3);
    const stepIndexes = fetchMock.mock.calls.map(([, init]) => JSON.parse(init.body).stepIndex);
    expect(stepIndexes).toEqual([0, 1, 2]);
  });

  it('starts stepIndex at 0 for a new, distinct sessionId', async () => {
    const agent = makeAgent({
      notify_on_invocation: { endpoint: 'https://example.com/hook', auth_type: 'none' },
    });

    service.dispatchIfConfigured(agent, { sessionId: 'session-a', invocationPayload: { prompt: 'a' } });
    service.dispatchIfConfigured(agent, { sessionId: 'session-a', invocationPayload: { prompt: 'a2' } });
    service.dispatchIfConfigured(agent, { sessionId: 'session-b', invocationPayload: { prompt: 'b' } });
    await flush();

    const bodies = fetchMock.mock.calls.map(([, init]) => JSON.parse(init.body));
    expect(bodies.find(b => b.sessionId === 'session-b')!.stepIndex).toBe(0);
    expect(bodies.filter(b => b.sessionId === 'session-a').map(b => b.stepIndex)).toEqual([0, 1]);
  });

  it('sets Authorization: Bearer <token> for auth_type bearer when a token is stored', async () => {
    mockAgentDynamoDBService.getNotifyBearerToken.mockResolvedValue('secret-token-123');
    const agent = makeAgent({
      notify_on_invocation: {
        endpoint: 'https://example.com/hook',
        auth_type: 'bearer',
        bearer_token: { hasToken: true, ssmPath: '/some/path' },
      },
    });

    service.dispatchIfConfigured(agent, { sessionId: 's1', invocationPayload: { prompt: 'hi' } });
    await flush();

    expect(mockAgentDynamoDBService.getNotifyBearerToken).toHaveBeenCalledWith('TestAgent');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers['Authorization']).toBe('Bearer secret-token-123');
  });

  it('short-circuits without calling fetch when auth_type is bearer but no token is stored', async () => {
    mockAgentDynamoDBService.getNotifyBearerToken.mockResolvedValue(null);
    const agent = makeAgent({
      notify_on_invocation: { endpoint: 'https://example.com/hook', auth_type: 'bearer' },
    });

    service.dispatchIfConfigured(agent, { sessionId: 's1', invocationPayload: { prompt: 'hi' } });
    await flush();

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('signs the request with SigV4 and includes signed headers for auth_type iam', async () => {
    const agent = makeAgent({
      notify_on_invocation: { endpoint: 'https://example.execute-api.us-east-1.amazonaws.com/prod/hook', auth_type: 'iam' },
    });

    service.dispatchIfConfigured(agent, { sessionId: 's1', invocationPayload: { prompt: 'hi' } });
    await flush();

    expect(mockAwsConfig.getCachedAuthSession).toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, init] = fetchMock.mock.calls[0];
    // SigV4 signing adds an Authorization header of the AWS4-HMAC-SHA256 form
    expect(init.headers['Authorization'] || init.headers['authorization']).toMatch(/AWS4-HMAC-SHA256/);
  });

  it('never throws or produces an unhandled rejection when fetch rejects', async () => {
    fetchMock.mockRejectedValue(new Error('network down'));
    const agent = makeAgent({
      notify_on_invocation: { endpoint: 'https://example.com/hook', auth_type: 'none' },
    });

    expect(() => {
      service.dispatchIfConfigured(agent, { sessionId: 's1', invocationPayload: { prompt: 'hi' } });
    }).not.toThrow();

    await flush();
    // If we reach here without the test runner reporting an unhandled rejection, the contract holds.
  });

  it('only ever constructs stepType "incoming_request"', async () => {
    const agent = makeAgent({
      notify_on_invocation: { endpoint: 'https://example.com/hook', auth_type: 'none' },
    });
    service.dispatchIfConfigured(agent, { sessionId: 's1', invocationPayload: { prompt: 'hi' } });
    await flush();

    const [, init] = fetchMock.mock.calls[0];
    const body = JSON.parse(init.body);
    expect(body.stepType).toBe('incoming_request');
  });
});
