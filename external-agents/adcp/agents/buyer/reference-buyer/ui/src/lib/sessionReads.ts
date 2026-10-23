/**
 * Reading recorded sessions straight from DynamoDB, with a fallback to the runtime's JSON actions.
 *
 * ## Why
 *
 * The dashboard polls for new steps. Those reads used to go through the AgentCore runtime's
 * `/invocations` endpoint, because a Cognito user-pool JWT is not an AWS credential. Measured against
 * the deployed stack:
 *
 *     the actual work, two DynamoDB reads          245 ms
 *     the same read via /invocations             1,089 ms   (~843 ms of it transport)
 *     with Identity Pool credentials, direct        24 ms   (p50 of 10, min 23, max 43)
 *
 * Payload size was never the issue — 565 bytes and 18 KB both cost ~1.1 s through the runtime.
 *
 * ## The fallback is not optional
 *
 * `identity_pool_id` is empty on a stack deployed before the pool existed, and the credential
 * exchange can fail for reasons the reader cannot act on (expired ID token, pool reconfigured, region
 * mismatch). Either way the dashboard must keep working through `/invocations` rather than go blank,
 * so every entry point here takes the action-based reader as its fallback and uses it on any failure.
 *
 * ## Shapes are the server's, not new ones
 *
 * `listSessions` and `getSessionSteps` return exactly what `_handle_list_sessions` and
 * `_handle_get_session_steps` return, field for field, so `derive.ts` and every panel are untouched.
 * That is what makes this substitutable rather than a second data model. `sessionReads.test.ts`
 * asserts the parity; the projections and filters below mirror `session_store.py` deliberately, and
 * the comments say which function each one mirrors.
 */
import { DynamoDBClient, QueryCommand, type QueryCommandOutput } from '@aws-sdk/client-dynamodb';
import { fromCognitoIdentityPool } from '@aws-sdk/credential-provider-cognito-identity';
import type { AppConfig, SessionMeta, SessionStep } from './types';

/** `session_records.GSI1_PK_VALUE`. The whole account's sessions share this one partition value. */
const GSI1_PK_VALUE = 'SESSION';

/** `session_store.UNCORRELATED_SESSION_PREFIX`. */
const UNCORRELATED_SESSION_PREFIX = 'adcp-seller-uncorrelated-';

/** The fields `session_store._session_summary` returns, in its order. */
const SUMMARY_FIELDS = [
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
] as const;

export interface SessionListResult {
  readonly sessions: readonly Record<string, unknown>[];
  readonly agent_id: string;
  readonly conversations_only: boolean;
}

export interface StepsResult {
  readonly meta: SessionMeta | null;
  readonly steps: readonly SessionStep[];
}

/** Whether direct reads are even possible. Both values come from `/config`. */
export function directReadsConfigured(config: AppConfig | null): boolean {
  return Boolean(config?.identity_pool_id) && Boolean(config?.sessions_table_name);
}

/**
 * A DynamoDB client whose credentials come from the identity pool, cached per ID token.
 *
 * Cached because the exchange costs ~507 ms and would otherwise be paid on every 1s poll, which would
 * make the direct path slower than the one it replaces. Keyed on the token so a re-login or a new user
 * gets a new client rather than silently reusing the previous identity's credentials.
 *
 * The SDK refreshes the underlying temporary credentials itself as they near expiry; that is the one
 * piece of this deliberately not hand-rolled.
 */
let cached: { token: string; client: DynamoDBClient } | null = null;

export function dynamoClientFor(
  config: AppConfig,
  idToken: string,
  userPoolId: string,
): DynamoDBClient {
  if (cached && cached.token === idToken) return cached.client;

  const region = config.aws_region;
  const client = new DynamoDBClient({
    region,
    credentials: fromCognitoIdentityPool({
      clientConfig: { region },
      identityPoolId: config.identity_pool_id as string,
      logins: {
        // The ID token, not the access token. Cognito Identity rejects an access token with
        // `NotAuthorizedException: Invalid login token`, which reads like a credential problem
        // rather than a wrong-token-type problem.
        //
        // `userPoolId` comes from the token's own `iss` claim rather than from config: the token
        // states which pool issued it, and a separately-configured value could disagree with the
        // token in hand, producing that same misleading error.
        [`cognito-idp.${region}.amazonaws.com/${userPoolId}`]: idToken,
      },
    }),
  });
  cached = { token: idToken, client };
  return client;
}

/**
 * Reads `iss` from an unverified ID token to name the login provider.
 *
 * Unverified is fine here: the claim only names which pool to present the token to, and Cognito
 * Identity verifies the signature itself. Nothing is trusted on the strength of this read.
 */
export function userPoolIdFromToken(idToken: string): string | null {
  const parts = idToken.split('.');
  if (parts.length !== 3) return null;
  try {
    const payload = parts[1];
    if (payload === undefined) return null;
    const claims: unknown = JSON.parse(atob(payload.replace(/-/g, '+').replace(/_/g, '/')));
    if (typeof claims !== 'object' || claims === null) return null;
    const iss = (claims as Record<string, unknown>)['iss'];
    if (typeof iss !== 'string') return null;
    // iss is `https://cognito-idp.{region}.amazonaws.com/{userPoolId}`.
    const id = iss.split('/').pop();
    return id !== undefined && id !== '' ? id : null;
  } catch {
    return null;
  }
}

/** Unwraps one DynamoDB AttributeValue into a plain JS value. */
function decode(value: unknown): unknown {
  if (typeof value !== 'object' || value === null) return value;
  const av = value as Record<string, unknown>;
  if ('S' in av) return av['S'];
  if ('N' in av) return Number(av['N']);
  if ('BOOL' in av) return av['BOOL'];
  if ('NULL' in av) return null;
  if ('L' in av) return (av['L'] as unknown[]).map(decode);
  if ('M' in av) {
    const out: Record<string, unknown> = {};
    for (const [key, nested] of Object.entries(av['M'] as Record<string, unknown>)) {
      out[key] = decode(nested);
    }
    return out;
  }
  return undefined;
}

function decodeItem(item: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(item)) out[key] = decode(value);
  return out;
}

/** `session_store.is_uncorrelated_tool_call`. */
function isUncorrelated(sessionId: unknown): boolean {
  return String(sessionId ?? '').startsWith(UNCORRELATED_SESSION_PREFIX);
}

/**
 * `session_store.list_recent_sessions`, including its over-fetch.
 *
 * The over-fetch is load-bearing, not defensive: `agent_id` and `conversations_only` are applied AFTER
 * the query, because gsi1's key is a constant partition plus a timestamp and cannot express either
 * filter. Fetching exactly `limit` rows and then filtering makes a filtered list look truncated rather
 * than filtered — which is the bug the Python side records having had.
 */
export async function listSessionsDirect(
  client: DynamoDBClient,
  tableName: string,
  options: { limit?: number; agentId?: string | null; conversationsOnly?: boolean } = {},
): Promise<SessionListResult> {
  const limit = options.limit ?? 20;
  const agentId = options.agentId ?? null;
  const conversationsOnly = options.conversationsOnly ?? false;
  const filtering = Boolean(agentId) || conversationsOnly;
  const queryLimit = filtering ? Math.min(limit * 5, 200) : limit;

  const response: QueryCommandOutput = await client.send(
    new QueryCommand({
      TableName: tableName,
      IndexName: 'gsi1',
      KeyConditionExpression: 'gsi1pk = :p',
      ExpressionAttributeValues: { ':p': { S: GSI1_PK_VALUE } },
      ScanIndexForward: false,
      Limit: queryLimit,
    }),
  );

  const sessions: Record<string, unknown>[] = [];
  for (const raw of response.Items ?? []) {
    const item = decodeItem(raw as Record<string, unknown>);
    if (agentId && item['agent_id'] !== agentId) continue;
    if (conversationsOnly && isUncorrelated(item['session_id'])) continue;
    const summary: Record<string, unknown> = {};
    // Projected to the same field set the action returns. A raw item would also carry pk/sk/gsi1*/ttl,
    // and a consumer could start depending on one of those without anyone deciding to expose it.
    for (const field of SUMMARY_FIELDS) summary[field] = item[field] ?? null;
    sessions.push(summary);
    if (sessions.length >= limit) break;
  }

  return { sessions, agent_id: agentId ?? '', conversations_only: conversationsOnly };
}

/** `session_store.get_session_meta` + `get_session_steps`, in one round trip each. */
export async function getSessionStepsDirect(
  client: DynamoDBClient,
  tableName: string,
  sessionId: string,
  sinceIndex = -1,
): Promise<StepsResult> {
  const pk = `SESSION#${sessionId}`;
  const padded = String(Math.max(sinceIndex, 0)).padStart(6, '0');

  const [metaResponse, stepsResponse] = await Promise.all([
    client.send(
      new QueryCommand({
        TableName: tableName,
        KeyConditionExpression: 'pk = :pk AND sk = :sk',
        ExpressionAttributeValues: { ':pk': { S: pk }, ':sk': { S: 'META' } },
      }),
    ),
    client.send(
      new QueryCommand({
        TableName: tableName,
        // `sk > STEP#<since_index>`, so step `since_index + 1` IS included. Comparing against
        // `STEP#<since_index + 1>` would make every incremental poll skip exactly one step — the
        // Python side carries the same warning for the same reason.
        KeyConditionExpression:
          sinceIndex >= 0 ? 'pk = :pk AND sk > :sk' : 'pk = :pk AND begins_with(sk, :sk)',
        ExpressionAttributeValues: {
          ':pk': { S: pk },
          ':sk': { S: sinceIndex >= 0 ? `STEP#${padded}` : 'STEP#' },
        },
        ScanIndexForward: true,
      }),
    ),
  ]);

  const metaItem = (metaResponse.Items ?? [])[0];
  const meta = metaItem === undefined ? null : (decodeItem(metaItem as Record<string, unknown>) as unknown as SessionMeta);

  const steps: SessionStep[] = (stepsResponse.Items ?? []).map((raw) => {
    const item = decodeItem(raw as Record<string, unknown>);
    return {
      step_index: Number(item['step_index'] ?? 0),
      step_type: item['step_type'],
      timestamp: Number(item['timestamp'] ?? 0),
      content: item['content'] ?? {},
    } as unknown as SessionStep;
  });

  return { meta, steps };
}

/**
 * A reader that prefers direct DynamoDB and falls back to the runtime action.
 *
 * `onFallback` is called with the reason the first time the direct path is abandoned, so the UI can
 * say the dashboard is running on the slower path instead of leaving the reader to wonder why it feels
 * sluggish. Silence there would be the same class of problem as showing a stale status as current.
 */
export interface SessionReader {
  listSessions(options?: {
    limit?: number;
    agentId?: string | null;
    conversationsOnly?: boolean;
  }): Promise<SessionListResult>;
  getSessionSteps(sessionId: string, sinceIndex?: number): Promise<StepsResult>;
  /** Which path the last call actually used. For the build stamp and for tests. */
  lastPath(): 'direct' | 'invocations';
}

export function createSessionReader(
  config: AppConfig | null,
  idToken: string | null,
  viaActions: {
    listSessions(options?: {
      limit?: number;
      agentId?: string | null;
      conversationsOnly?: boolean;
    }): Promise<SessionListResult>;
    getSessionSteps(sessionId: string, sinceIndex?: number): Promise<StepsResult>;
  },
  onFallback?: (reason: string) => void,
): SessionReader {
  let path: 'direct' | 'invocations' = 'invocations';
  // Latched: once the direct path has failed, stop retrying it every second. A pool that is
  // misconfigured will not fix itself between polls, and retrying would pay the failure's latency
  // on every tick on top of the fallback's.
  let directBroken = false;
  let announced = false;

  const table = config?.sessions_table_name ?? '';

  function fallback(reason: string) {
    if (!directBroken) {
      directBroken = true;
      if (!announced) {
        announced = true;
        onFallback?.(reason);
      }
    }
    path = 'invocations';
  }

  function client(): DynamoDBClient | null {
    if (directBroken || idToken === null || !directReadsConfigured(config) || config === null) {
      return null;
    }
    const userPoolId = userPoolIdFromToken(idToken);
    if (userPoolId === null) {
      fallback('the ID token did not name a user pool, so direct reads are unavailable');
      return null;
    }
    return dynamoClientFor(config, idToken, userPoolId);
  }

  return {
    async listSessions(options = {}) {
      const db = client();
      if (db !== null) {
        try {
          const result = await listSessionsDirect(db, table, options);
          path = 'direct';
          return result;
        } catch (error) {
          fallback(describe(error));
        }
      }
      path = 'invocations';
      return viaActions.listSessions(options);
    },

    async getSessionSteps(sessionId, sinceIndex = -1) {
      const db = client();
      if (db !== null) {
        try {
          const result = await getSessionStepsDirect(db, table, sessionId, sinceIndex);
          path = 'direct';
          return result;
        } catch (error) {
          fallback(describe(error));
        }
      }
      path = 'invocations';
      return viaActions.getSessionSteps(sessionId, sinceIndex);
    },

    lastPath() {
      return path;
    },
  };
}

/** A reason a reader can show, without leaking credential material into the DOM. */
function describe(error: unknown): string {
  const name = error instanceof Error ? error.name : 'Error';
  if (name === 'AccessDeniedException') {
    return 'this account is not permitted to read sessions directly; using the slower path';
  }
  if (name === 'NotAuthorizedException') {
    return 'the session token was not accepted for direct reads; using the slower path';
  }
  return `direct session reads are unavailable (${name}); using the slower path`;
}


/**
 * An `InvokeFn`-shaped wrapper that serves the two read actions directly and delegates everything
 * else.
 *
 * Adapting at the transport seam rather than rewriting the hooks is deliberate. `useSessionViewer`
 * and `useJourneySession` carry three guards against a duplicate-message bug that reached production
 * once — single-flight, a step-index high-water mark, and a generation tag so a stale poll cannot
 * release the guard. Those guards are tested and they are subtle. Swapping what `invoke` does, while
 * leaving them reading `InvokeEvent[]` exactly as before, changes one line per hook and puts none of
 * that at risk.
 *
 * Returns the same event shapes `_handle_list_sessions` and `_handle_get_session_steps` yield, so
 * `findSessionStepsEvent` and friends are unchanged.
 */
export function createReadInvoke(
  config: AppConfig | null,
  idToken: string | null,
  invoke: (payload: unknown, signal?: AbortSignal) => Promise<unknown[]>,
  onFallback?: (reason: string) => void,
): (payload: unknown, signal?: AbortSignal) => Promise<unknown[]> {
  const reader = createSessionReader(
    config,
    idToken,
    {
      // The fallback path is the untouched action call, so a direct-read failure lands exactly where
      // the reader was before this feature existed.
      async listSessions(options = {}) {
        const events = await invoke({
          action: 'list_sessions',
          limit: options.limit ?? 20,
          ...(options.agentId ? { agent_id: options.agentId } : {}),
          ...(options.conversationsOnly ? { conversations_only: true } : {}),
        });
        const event = events.find(
          (candidate) => (candidate as { type?: string }).type === 'sessions',
        ) as { sessions?: Record<string, unknown>[]; agent_id?: string; conversations_only?: boolean } | undefined;
        return {
          sessions: event?.sessions ?? [],
          agent_id: event?.agent_id ?? '',
          conversations_only: event?.conversations_only ?? false,
        };
      },
      async getSessionSteps(sessionId, sinceIndex = -1) {
        const events = await invoke({
          action: 'get_session_steps',
          session_id: sessionId,
          since_index: sinceIndex,
        });
        const event = events.find(
          (candidate) => (candidate as { type?: string }).type === 'session_steps',
        ) as { meta?: SessionMeta | null; steps?: SessionStep[] } | undefined;
        // An absent event is a real failure the hooks already report (they look for an error event
        // and throw). Rethrowing here would convert it into a fallback, which would hide it.
        if (event === undefined) {
          const error = events.find(
            (candidate) => (candidate as { type?: string }).type === 'error',
          ) as { message?: string } | undefined;
          throw new SessionReadPassthrough(events, error?.message ?? 'no session_steps event');
        }
        return { meta: event.meta ?? null, steps: event.steps ?? [] };
      },
    },
    onFallback,
  );

  return async (payload: unknown, signal?: AbortSignal) => {
    const action = (payload as { action?: string } | null)?.action;

    if (action === 'get_session_steps') {
      const request = payload as { session_id: string; since_index?: number };
      try {
        const { meta, steps } = await reader.getSessionSteps(
          request.session_id,
          request.since_index ?? -1,
        );
        return [{ type: 'session_steps', meta, steps }];
      } catch (error) {
        // The action path already failed and said why; hand its own events back so the hook's
        // existing error handling sees what it would have seen.
        if (error instanceof SessionReadPassthrough) return error.events;
        throw error;
      }
    }

    if (action === 'list_sessions') {
      const request = payload as { limit?: number; agent_id?: string; conversations_only?: boolean };
      const result = await reader.listSessions({
        limit: request.limit ?? 20,
        agentId: request.agent_id ?? null,
        conversationsOnly: request.conversations_only ?? false,
      });
      return [
        {
          type: 'sessions',
          sessions: result.sessions,
          agent_id: result.agent_id,
          conversations_only: result.conversations_only,
        },
      ];
    }

    return invoke(payload, signal);
  };
}

/** Carries the action path's own events so a failure there is reported, not converted to a fallback. */
class SessionReadPassthrough extends Error {
  constructor(
    readonly events: unknown[],
    message: string,
  ) {
    super(message);
    this.name = 'SessionReadPassthrough';
  }
}
