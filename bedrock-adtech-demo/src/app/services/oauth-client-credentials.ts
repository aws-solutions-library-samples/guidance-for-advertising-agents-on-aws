/**
 * OAuth 2.0 client-credentials ("machine-to-machine") support shared by every
 * auth surface in this app: an agent's own inbound auth, external A2A peers,
 * MCP servers, and the invocation-notification webhook.
 *
 * Secret material lives only in SSM SecureString, written to the SAME parameter
 * path each surface already uses for its other credential modes. The stored
 * document carries a `grant_type` discriminator so a reader can tell a
 * client-credentials document apart from the existing Cognito
 * USER_PASSWORD_AUTH document (`{client_id, username, password}`) without a
 * second parameter path or additional IAM grants.
 *
 * The AgentCore runtime reads the same document shape — see
 * `agentcore/deployment/agent/shared/a2a_auth.py`.
 */

/** Discriminator value stored in the credential document. */
export const OAUTH_M2M_GRANT_TYPE = 'client_credentials';

/**
 * Non-secret reference persisted alongside an agent config in DynamoDB. The
 * client secret is never part of this record.
 */
export interface OAuthClientCredentialsRef {
  /** True only after a successful SSM write. */
  hasCredentials: boolean;
  /** SSM parameter path holding the credential document. */
  ssmPath?: string;
  /** Token endpoint, kept here so the editor can show it without reading SSM. */
  tokenUrl?: string;
  /** Space-separated scopes requested at the token endpoint, if any. */
  scope?: string;
  /** `audience` parameter, for providers that require one (e.g. Auth0 APIs). */
  audience?: string;
}

/** Operator-entered values collected by the editor forms. */
export interface OAuthClientCredentialsInput {
  clientId: string;
  clientSecret: string;
  tokenUrl: string;
  scope?: string;
  audience?: string;
}

/** The document written to SSM SecureString. */
export interface OAuthClientCredentialsDocument {
  grant_type: typeof OAUTH_M2M_GRANT_TYPE;
  client_id: string;
  client_secret: string;
  token_url: string;
  scope?: string;
  audience?: string;
}

/** Upper bound on any single stored field, mirroring the bearer-token bound. */
const MAX_FIELD_LENGTH = 4096;

/** Refresh this many seconds before the provider's stated expiry. */
const TOKEN_EXPIRY_BUFFER_SECONDS = 60;

/** Applied when a token response omits `expires_in`. */
const DEFAULT_TOKEN_LIFETIME_SECONDS = 3600;

interface CachedToken {
  token: string;
  /** Epoch milliseconds. */
  expiresAt: number;
}

/**
 * Minted tokens, keyed by the caller's cache key (the SSM path in practice).
 * Module-level so a token is reused across the several services that may
 * authenticate against the same endpoint within one page session.
 */
const tokenCache = new Map<string, CachedToken>();

/** Validate a token endpoint. https is required — the secret is sent in it. */
export function isValidTokenUrl(value: string): boolean {
  try {
    return new URL(value).protocol === 'https:';
  } catch {
    return false;
  }
}

/**
 * Validate the operator's input and return the JSON document to store.
 * Throws with a field-level message on invalid input. The thrown message never
 * contains the client secret.
 */
export function buildClientCredentialsDocument(input: OAuthClientCredentialsInput): string {
  const clientId = (input.clientId || '').trim();
  const clientSecret = (input.clientSecret || '').trim();
  const tokenUrl = (input.tokenUrl || '').trim();
  const scope = (input.scope || '').trim();
  const audience = (input.audience || '').trim();

  const missing: string[] = [];
  if (!clientId) missing.push('Client ID');
  if (!clientSecret) missing.push('Client Secret');
  if (!tokenUrl) missing.push('Token URL');
  if (missing.length > 0) {
    throw new Error(`Please fill in: ${missing.join(', ')}. All three are required.`);
  }

  if (!isValidTokenUrl(tokenUrl)) {
    throw new Error('Token URL must be a valid https:// URL.');
  }

  for (const [label, value] of [
    ['Client ID', clientId],
    ['Client Secret', clientSecret],
    ['Token URL', tokenUrl],
    ['Scope', scope],
    ['Audience', audience]
  ] as const) {
    if (value.length > MAX_FIELD_LENGTH) {
      throw new Error(`${label} is too long (max ${MAX_FIELD_LENGTH} characters).`);
    }
  }

  const doc: OAuthClientCredentialsDocument = {
    grant_type: OAUTH_M2M_GRANT_TYPE,
    client_id: clientId,
    client_secret: clientSecret,
    token_url: tokenUrl
  };
  if (scope) doc.scope = scope;
  if (audience) doc.audience = audience;

  return JSON.stringify(doc);
}

/**
 * Parse a stored credential document, returning it only when it is a
 * client-credentials document with the fields needed to mint a token.
 */
export function parseClientCredentialsDocument(
  raw: string | null | undefined
): OAuthClientCredentialsDocument | null {
  if (!raw) return null;
  let parsed: any;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!parsed || parsed.grant_type !== OAUTH_M2M_GRANT_TYPE) return null;
  if (!parsed.client_id || !parsed.client_secret || !parsed.token_url) return null;
  return parsed as OAuthClientCredentialsDocument;
}

/** Whether a stored document is a client-credentials document. */
export function isClientCredentialsDocument(raw: string | null | undefined): boolean {
  return parseClientCredentialsDocument(raw) !== null;
}

function buildFormBody(
  doc: OAuthClientCredentialsDocument,
  includeSecret: boolean
): string {
  const params = new URLSearchParams();
  params.set('grant_type', OAUTH_M2M_GRANT_TYPE);
  params.set('client_id', doc.client_id);
  if (includeSecret) params.set('client_secret', doc.client_secret);
  if (doc.scope) params.set('scope', doc.scope);
  if (doc.audience) params.set('audience', doc.audience);
  return params.toString();
}

async function requestToken(
  doc: OAuthClientCredentialsDocument,
  style: 'basic' | 'body'
): Promise<{ ok: boolean; status: number; accessToken?: string; expiresIn?: number }> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/x-www-form-urlencoded',
    Accept: 'application/json'
  };
  if (style === 'basic') {
    headers['Authorization'] = `Basic ${btoa(`${doc.client_id}:${doc.client_secret}`)}`;
  }

  const response = await fetch(doc.token_url, {
    method: 'POST',
    headers,
    body: buildFormBody(doc, style === 'body')
  });

  if (!response.ok) {
    return { ok: false, status: response.status };
  }

  const data = await response.json();
  return {
    ok: true,
    status: response.status,
    accessToken: data?.access_token,
    expiresIn: typeof data?.expires_in === 'number' ? data.expires_in : undefined
  };
}

/**
 * Exchange stored client credentials for an access token, caching it under
 * `cacheKey` until shortly before it expires.
 *
 * Credentials are presented as HTTP Basic first (the method Cognito requires
 * for clients with a secret), then retried once as `client_secret_post` body
 * parameters if the endpoint rejects that — providers accept one or the other
 * and do not advertise which to an unauthenticated caller.
 *
 * Throws on failure. The thrown message carries the HTTP status only, never the
 * credential values.
 */
export async function acquireClientCredentialsToken(
  raw: string | null | undefined,
  cacheKey: string
): Promise<string> {
  const doc = parseClientCredentialsDocument(raw);
  if (!doc) {
    throw new Error(
      'Stored credentials are not a valid OAuth client-credentials document. ' +
        'Re-enter the Client ID, Client Secret, and Token URL.'
    );
  }

  const cached = tokenCache.get(cacheKey);
  if (cached && Date.now() < cached.expiresAt) {
    return cached.token;
  }

  let result = await requestToken(doc, 'basic');
  if (!result.ok && (result.status === 400 || result.status === 401)) {
    result = await requestToken(doc, 'body');
  }

  if (!result.ok) {
    throw new Error(
      `OAuth token request failed (HTTP ${result.status}) at ${doc.token_url}.`
    );
  }
  if (!result.accessToken) {
    throw new Error(`OAuth token endpoint ${doc.token_url} returned no access_token.`);
  }

  const lifetime = result.expiresIn ?? DEFAULT_TOKEN_LIFETIME_SECONDS;
  tokenCache.set(cacheKey, {
    token: result.accessToken,
    expiresAt: Date.now() + Math.max(lifetime - TOKEN_EXPIRY_BUFFER_SECONDS, 0) * 1000
  });

  return result.accessToken;
}

/** Drop cached tokens — for one key, or all of them when omitted. */
export function clearClientCredentialsTokenCache(cacheKey?: string): void {
  if (cacheKey) {
    tokenCache.delete(cacheKey);
  } else {
    tokenCache.clear();
  }
}
