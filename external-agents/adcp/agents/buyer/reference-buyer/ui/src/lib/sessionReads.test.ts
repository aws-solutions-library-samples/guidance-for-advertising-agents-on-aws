/**
 * Direct DynamoDB session reads, and the fallback that keeps the dashboard working without them.
 *
 * ## What this is for
 *
 * The dashboard's reads moved off the AgentCore `/invocations` hop. Measured against the deployed
 * stack: 1,089 ms per poll before, 24 ms after, with ~843 ms of the old figure being pure transport.
 *
 * Two things have to hold for that to be a safe substitution rather than a second data model:
 *
 *   1. **Shape parity.** The direct read must return exactly what the action returned, field for
 *      field, so `derive.ts` and every panel stay untouched. A direct read that returned raw items
 *      would work today and let a consumer start depending on `pk`/`ttl`/`gsi1sk` tomorrow.
 *   2. **The fallback fires.** `identity_pool_id` is empty on a stack deployed before the pool
 *      existed, and the exchange can fail for reasons a reader cannot act on. Either way the
 *      dashboard must degrade to the slower path, not go blank — a frozen dashboard that looks
 *      current is the failure mode this project keeps coming back to.
 *
 * The paging detail in `listSessionsDirect` is also pinned here. The filters are applied AFTER the
 * query because gsi1's key is a constant partition plus a timestamp and cannot express them, so the
 * query over-fetches. Without that, a filtered list looks truncated rather than filtered — a bug the
 * Python side records having had.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  createSessionReader,
  directReadsConfigured,
  getSessionStepsDirect,
  listSessionsDirect,
  userPoolIdFromToken,
} from './sessionReads';
import type { AppConfig } from './types';

const CONFIG = {
  aws_region: 'us-east-1',
  identity_pool_id: 'us-east-1:pool',
  sessions_table_name: 'sessions',
} as unknown as AppConfig;

/** A DynamoDB item as the wire returns it: every value wrapped in an AttributeValue. */
const meta = (sessionId: string, extra: Record<string, unknown> = {}) => ({
  pk: { S: `SESSION#${sessionId}` },
  sk: { S: 'META' },
  session_id: { S: sessionId },
  status: { S: 'active' },
  invoker: { S: 'sam' },
  mode: { S: 'chat' },
  agent_id: { S: 'buyer' },
  agent_name: { S: 'Buyer' },
  seller_agent_id: { S: 'gotham' },
  request_preview: { S: 'find ctv' },
  started_at: { S: '2026-09-01T00:00:00Z' },
  updated_at: { S: '2026-09-01T00:01:00Z' },
  completed_at: { NULL: true },
  error_message: { NULL: true },
  // Present on the real item and deliberately NOT in the projection.
  gsi1pk: { S: 'SESSION' },
  gsi1sk: { S: '2026-09-01T00:01:00Z' },
  ttl: { N: '123' },
  ...extra,
});

const step = (index: number) => ({
  pk: { S: 'SESSION#s1' },
  sk: { S: `STEP#${String(index).padStart(6, '0')}` },
  step_index: { N: String(index) },
  step_type: { S: 'tool_call' },
  timestamp: { N: '1700000000' },
  content: { M: { toolName: { S: 'adcp_get_products' }, input: { M: { brief: { S: 'ctv' } } } } },
});

function fakeClient(responses: Record<string, unknown>[]) {
  const calls: unknown[] = [];
  let next = 0;
  return {
    calls,
    client: {
      send: vi.fn(async (command: { input: unknown }) => {
        calls.push(command.input);
        const response = responses[Math.min(next, responses.length - 1)];
        next += 1;
        return response;
      }),
    } as never,
  };
}

describe('directReadsConfigured', () => {
  it('needs both the pool id and the table name', () => {
    expect(directReadsConfigured(CONFIG)).toBe(true);
    expect(directReadsConfigured({ ...CONFIG, identity_pool_id: '' } as AppConfig)).toBe(false);
    expect(directReadsConfigured({ ...CONFIG, sessions_table_name: '' } as AppConfig)).toBe(false);
    expect(directReadsConfigured(null)).toBe(false);
  });
});

describe('userPoolIdFromToken', () => {
  it('reads the pool id out of the iss claim', () => {
    const payload = btoa(
      JSON.stringify({ iss: 'https://cognito-idp.us-east-1.amazonaws.com/us-east-1_abc123' }),
    );
    expect(userPoolIdFromToken(`h.${payload}.s`)).toBe('us-east-1_abc123');
  });

  it('returns null for a token it cannot read, rather than guessing', () => {
    expect(userPoolIdFromToken('not-a-jwt')).toBeNull();
    expect(userPoolIdFromToken(`h.${btoa('{}')}.s`)).toBeNull();
  });
});

describe('listSessionsDirect', () => {
  it('projects to exactly the fields the action returned, and no others', async () => {
    const { client } = fakeClient([{ Items: [meta('s1')] }]);

    const result = await listSessionsDirect(client, 'sessions');

    // Field-for-field parity with session_store._session_summary.
    expect(Object.keys(result.sessions[0]!)).toEqual([
      'session_id',
      'status',
      'invoker',
      'mode',
      'agent_id',
      'agent_name',
      'seller_agent_id',
      'request_preview',
      'started_at',
      'updated_at',
      'completed_at',
      'error_message',
    ]);
    // The raw item's internals must not leak into what panels consume.
    for (const internal of ['pk', 'sk', 'gsi1pk', 'gsi1sk', 'ttl']) {
      expect(result.sessions[0]).not.toHaveProperty(internal);
    }
  });

  it('unwraps AttributeValues, including nulls', async () => {
    const { client } = fakeClient([{ Items: [meta('s1')] }]);

    const [session] = (await listSessionsDirect(client, 'sessions')).sessions;

    expect(session).toMatchObject({ session_id: 's1', status: 'active', invoker: 'sam' });
    // NULL must become null, not the string "true" or an object.
    expect(session!['completed_at']).toBeNull();
  });

  it('queries newest-first on gsi1', async () => {
    const { client, calls } = fakeClient([{ Items: [] }]);

    await listSessionsDirect(client, 'sessions');

    expect(calls[0]).toMatchObject({
      IndexName: 'gsi1',
      ScanIndexForward: false,
      KeyConditionExpression: 'gsi1pk = :p',
    });
  });

  it('over-fetches when filtering, so a filtered list is not mistaken for a truncated one', async () => {
    const { client, calls } = fakeClient([{ Items: [] }]);

    await listSessionsDirect(client, 'sessions', { limit: 20, conversationsOnly: true });

    // limit * 5, capped at 200 — mirrors session_store.list_recent_sessions.
    expect((calls[0] as { Limit: number }).Limit).toBe(100);
  });

  it('does not over-fetch when there is nothing to filter', async () => {
    const { client, calls } = fakeClient([{ Items: [] }]);

    await listSessionsDirect(client, 'sessions', { limit: 20 });

    expect((calls[0] as { Limit: number }).Limit).toBe(20);
  });

  it('drops standalone tool-call records when asked, and keeps them otherwise', async () => {
    const items = [meta('adcp-seller-uncorrelated-xyz'), meta('real-conversation')];

    const filtered = await listSessionsDirect(fakeClient([{ Items: items }]).client, 'sessions', {
      conversationsOnly: true,
    });
    expect(filtered.sessions.map((s) => s['session_id'])).toEqual(['real-conversation']);

    const unfiltered = await listSessionsDirect(fakeClient([{ Items: items }]).client, 'sessions');
    expect(unfiltered.sessions).toHaveLength(2);
  });

  it('filters by agent id', async () => {
    const items = [meta('a', { agent_id: { S: 'other' } }), meta('b')];

    const result = await listSessionsDirect(fakeClient([{ Items: items }]).client, 'sessions', {
      agentId: 'buyer',
    });

    expect(result.sessions.map((s) => s['session_id'])).toEqual(['b']);
    expect(result.agent_id).toBe('buyer');
  });
});

describe('getSessionStepsDirect', () => {
  it('includes the step immediately after since_index', async () => {
    // The off-by-one that would make every incremental poll skip exactly one step. `sk >
    // STEP#000011` includes step 12; `sk > STEP#000012` would not.
    const { client, calls } = fakeClient([{ Items: [meta('s1')] }, { Items: [step(12)] }]);

    const result = await getSessionStepsDirect(client, 'sessions', 's1', 11);

    const stepQuery = calls[1] as { KeyConditionExpression: string; ExpressionAttributeValues: Record<string, { S: string }> };
    expect(stepQuery.KeyConditionExpression).toBe('pk = :pk AND sk > :sk');
    expect(stepQuery.ExpressionAttributeValues[':sk']!.S).toBe('STEP#000011');
    expect(result.steps.map((s) => s.step_index)).toEqual([12]);
  });

  it('reads every step when nothing has been seen yet', async () => {
    const { client, calls } = fakeClient([{ Items: [] }, { Items: [step(0), step(1)] }]);

    await getSessionStepsDirect(client, 'sessions', 's1', -1);

    expect((calls[1] as { KeyConditionExpression: string }).KeyConditionExpression).toBe(
      'pk = :pk AND begins_with(sk, :sk)',
    );
  });

  it('returns steps oldest-first with content decoded', async () => {
    const { client } = fakeClient([{ Items: [] }, { Items: [step(0), step(1)] }]);

    const result = await getSessionStepsDirect(client, 'sessions', 's1');

    expect(result.steps.map((s) => s.step_index)).toEqual([0, 1]);
    expect(result.steps[0]!.content).toMatchObject({
      toolName: 'adcp_get_products',
      input: { brief: 'ctv' },
    });
  });

  it('reports a missing session as null meta rather than inventing one', async () => {
    const { client } = fakeClient([{ Items: [] }, { Items: [] }]);

    const result = await getSessionStepsDirect(client, 'sessions', 'nope');

    expect(result.meta).toBeNull();
    expect(result.steps).toEqual([]);
  });
});

describe('createSessionReader fallback', () => {
  const viaActions = () => ({
    listSessions: vi.fn(async () => ({ sessions: [{ session_id: 'from-action' }], agent_id: '', conversations_only: false })),
    getSessionSteps: vi.fn(async () => ({ meta: null, steps: [] })),
  });

  const token = `h.${btoa(JSON.stringify({ iss: 'https://cognito-idp.us-east-1.amazonaws.com/us-east-1_abc' }))}.s`;

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('uses the actions when no pool is configured', async () => {
    const actions = viaActions();
    const reader = createSessionReader({ ...CONFIG, identity_pool_id: '' } as AppConfig, token, actions);

    const result = await reader.listSessions();

    expect(actions.listSessions).toHaveBeenCalled();
    expect(reader.lastPath()).toBe('invocations');
    expect(result.sessions[0]).toMatchObject({ session_id: 'from-action' });
  });

  it('uses the actions when there is no ID token', async () => {
    const actions = viaActions();
    const reader = createSessionReader(CONFIG, null, actions);

    await reader.listSessions();

    expect(actions.listSessions).toHaveBeenCalled();
    expect(reader.lastPath()).toBe('invocations');
  });

  it('falls back, and says why, when the ID token names no user pool', async () => {
    const actions = viaActions();
    const onFallback = vi.fn();
    const reader = createSessionReader(CONFIG, 'not-a-jwt', actions, onFallback);

    await reader.listSessions();

    expect(actions.listSessions).toHaveBeenCalled();
    expect(onFallback).toHaveBeenCalledWith(expect.stringContaining('did not name a user pool'));
  });

  it('announces the fallback once, not on every poll', async () => {
    const actions = viaActions();
    const onFallback = vi.fn();
    const reader = createSessionReader(CONFIG, 'not-a-jwt', actions, onFallback);

    await reader.listSessions();
    await reader.listSessions();
    await reader.listSessions();

    // A once-per-second poll would otherwise produce a once-per-second notice.
    expect(onFallback).toHaveBeenCalledTimes(1);
    expect(actions.listSessions).toHaveBeenCalledTimes(3);
  });
});
