/**
 * SessionHistoryService reads a session back from AgentCore Memory with
 * ListEvents. The AgentCore client is a fake that records each command.
 */
// The @Injectable decorator is a no-op outside Angular.
jest.mock('@angular/core', () => ({
  Injectable: () => (target: any) => target,
}));
// The real services pull in Amplify and the full Bedrock stack; the service
// under test receives plain objects instead.
jest.mock('./aws-config.service', () => ({ AwsConfigService: class {} }));
jest.mock('./bedrock.service', () => ({ BedrockService: class {} }));
jest.mock('./session-manager.service', () => ({ SessionManagerService: class {} }));

import { ListEventsCommand, ListEventsCommandOutput } from '@aws-sdk/client-bedrock-agentcore';
import { AgentCoreClientFactory, ListEventsClient, SessionHistoryService } from './session-history.service';
import { SessionInfo } from './session-manager.service';

const SESSION_ID = '0123456789abcdef0123456789abcdef0';
const MEMORY_ID = 'sim_memory_1234-AbCdEf1234';

type Page = Pick<ListEventsCommandOutput, 'events' | 'nextToken'>;

class FakeAgentCoreClient implements ListEventsClient {
  readonly sent: unknown[] = [];

  constructor(private readonly respond: (command: ListEventsCommand) => Promise<Page>) {}

  send(command: ListEventsCommand): Promise<Page> {
    this.sent.push(command);
    return this.respond(command);
  }
}

function userEvent(id: string, text: string, at: string) {
  return {
    memoryId: MEMORY_ID,
    actorId: 'AgencyAgent',
    sessionId: SESSION_ID,
    eventId: id,
    eventTimestamp: new Date(at),
    payload: [{ conversational: { role: 'USER' as const, content: { text } } }],
  };
}

function setup(options: { agentNames?: string[]; respond?: (command: ListEventsCommand) => Promise<Page> }) {
  const session: SessionInfo = {
    sessionId: SESSION_ID,
    tabId: 'tab-a',
    createdAt: new Date('2025-01-01T00:00:00Z'),
    lastUsed: new Date('2025-01-01T00:00:00Z'),
    messageCount: 2,
    agentNames: options.agentNames ?? ['Agency_Agent'],
  };
  const sessionManager = {
    hasSession: (id: string) => id === SESSION_ID,
    getSession: (id: string) => (id === SESSION_ID ? session : null),
  };
  const awsConfig = {
    getAwsConfig: async () => ({
      region: 'us-west-2',
      credentials: { accessKeyId: 'test-access-key', secretAccessKey: 'test-secret', sessionToken: 'test-token' },
    }),
  };
  const bedrockService = { getMemoryId: () => MEMORY_ID };
  const client = new FakeAgentCoreClient(options.respond ?? (async () => ({ events: [] })));
  const createdWith: unknown[] = [];
  const factory = {
    create: (config: unknown) => {
      createdWith.push(config);
      return client;
    },
  } as unknown as AgentCoreClientFactory;

  const service = new SessionHistoryService(sessionManager as any, awsConfig as any, bedrockService as any, factory);
  return { service, client, createdWith };
}

describe('SessionHistoryService', () => {
  it('sends ListEvents with the memory id, actor id and session id', async () => {
    const { service, client, createdWith } = setup({
      respond: async () => ({ events: [userEvent('evt-1', 'Plan a campaign', '2025-01-01T10:00:00Z')] }),
    });

    const result = await service.loadHistory(SESSION_ID);

    expect(result.error).toBeUndefined();
    expect(result.entries.map(entry => entry.text)).toEqual(['Plan a campaign']);
    expect(result.entries[0].agentName).toBe('Agency_Agent');
    expect(createdWith).toEqual([expect.objectContaining({ region: 'us-west-2' })]);
    expect(client.sent).toHaveLength(1);
    expect((client.sent[0] as ListEventsCommand).input).toEqual({
      memoryId: MEMORY_ID,
      actorId: 'Agency-Agent',
      sessionId: SESSION_ID,
      includePayloads: true,
      maxResults: 100,
      nextToken: undefined,
    });
  });

  it('follows nextToken until every page is read', async () => {
    const pages: Record<string, Page> = {
      first: { events: [userEvent('evt-1', 'First', '2025-01-01T10:00:00Z')], nextToken: 'page-2' },
      'page-2': { events: [userEvent('evt-2', 'Second', '2025-01-01T10:01:00Z')], nextToken: 'page-3' },
      'page-3': { events: [userEvent('evt-3', 'Third', '2025-01-01T10:02:00Z')] },
    };
    const { service, client } = setup({
      respond: async command => pages[command.input.nextToken ?? 'first'],
    });

    const result = await service.loadHistory(SESSION_ID);

    expect(client.sent.map(command => (command as ListEventsCommand).input.nextToken)).toEqual([
      undefined,
      'page-2',
      'page-3',
    ]);
    expect(result.entries.map(entry => entry.text)).toEqual(['First', 'Second', 'Third']);
  });

  it('returns an error result instead of throwing when ListEvents fails', async () => {
    const accessDenied = Object.assign(new Error('User is not authorized to perform: bedrock-agentcore:ListEvents'), {
      name: 'AccessDeniedException',
    });
    const consoleError = jest.spyOn(console, 'error').mockImplementation(() => undefined);
    const { service } = setup({
      agentNames: ['AgencyAgent', 'MediaPlanningAgent'],
      respond: async command => {
        if (command.input.actorId === 'MediaPlanningAgent') {
          throw accessDenied;
        }
        return { events: [userEvent('evt-1', 'Still here', '2025-01-01T10:00:00Z')] };
      },
    });

    const result = await service.loadHistory(SESSION_ID);

    expect(result.error).toContain('AccessDeniedException');
    expect(result.entries.map(entry => entry.text)).toEqual(['Still here']);
    expect(consoleError).toHaveBeenCalled();
    consoleError.mockRestore();
  });

  it('refuses a session id this page did not create and calls nothing', async () => {
    const consoleError = jest.spyOn(console, 'error').mockImplementation(() => undefined);
    const { service, client, createdWith } = setup({});

    const refused = await service.loadHistory('ffffffffffffffffffffffffffffffff0');

    expect(refused).toEqual({ entries: [], error: 'Unknown session id' });
    expect(createdWith).toHaveLength(0);
    expect(client.sent).toHaveLength(0);

    // A known session sends ListEvents and nothing else (no model call).
    await service.loadHistory(SESSION_ID);
    expect(client.sent.length).toBeGreaterThan(0);
    expect(client.sent.every(command => command instanceof ListEventsCommand)).toBe(true);
    consoleError.mockRestore();
  });
});
