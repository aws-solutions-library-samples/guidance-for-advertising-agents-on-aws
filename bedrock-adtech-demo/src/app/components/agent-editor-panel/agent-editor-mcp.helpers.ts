/**
 * MCP Server helper functions extracted from AgentEditorPanelComponent.
 * These are pure/stateless utility functions that operate on passed-in data.
 */
import { MCPServerConfig } from '../agent-management-modal/agent-management-modal.component';
import { MCPToolInfo, MCPToolListResult } from './agent-editor-panel.constants';
import { AwsConfigService } from '../../services/aws-config.service';
import { AgentDynamoDBService } from '../../services/agent-dynamodb.service';
import { acquireClientCredentialsToken } from '../../services/oauth-client-credentials';

/** Generate a unique ID for an MCP server */
export function generateMcpServerId(): string {
  return `mcp_${Date.now().toString(36)}_${Math.random().toString(36).substr(2, 5)}`;
}

/** Get transport icon for display */
export function getMcpTransportIcon(transport: string): string {
  switch (transport) {
    case 'stdio': return 'terminal';
    case 'http': return 'http';
    case 'sse': return 'stream';
    default: return 'extension';
  }
}

/** Get transport display name */
export function getMcpTransportName(transport: string): string {
  switch (transport) {
    case 'stdio': return 'Command Line (stdio)';
    case 'http': return 'HTTP';
    case 'sse': return 'Server-Sent Events';
    default: return transport;
  }
}

/**
 * Fetch tools from an MCP server endpoint using the JSON-RPC protocol.
 * Handles plain HTTP, AWS IAM SigV4, and OAuth Bearer Token authenticated requests.
 */
export async function fetchMcpTools(
  server: MCPServerConfig,
  awsConfigService: AwsConfigService,
  agentDynamoDBService?: AgentDynamoDBService,
  agentName?: string
): Promise<MCPToolInfo[]> {
  const url = server.url!;

  const jsonRpcBody = JSON.stringify({
    jsonrpc: '2.0',
    id: 1,
    method: 'tools/list',
    params: {}
  });

  // Streamable HTTP servers must be offered both content types; some reject a
  // request that only accepts JSON.
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    'Accept': 'application/json, text/event-stream'
  };

  if (server.headers) {
    Object.assign(headers, server.headers);
  }

  const bearer = await resolveMcpBearer(server, awsConfigService, agentDynamoDBService, agentName);
  if (bearer) {
    headers['Authorization'] = `Bearer ${bearer}`;
  }

  let response: Response;

  if (server.awsAuth) {
    response = await fetchWithSigV4(url, jsonRpcBody, headers, server.awsAuth, awsConfigService);
  } else {
    response = await fetch(url, {
      method: 'POST',
      headers,
      body: jsonRpcBody
    });
  }

  if (!response.ok) {
    const errorText = await response.text().catch(() => '');
    throw new Error(`HTTP ${response.status}: ${response.statusText}${errorText ? ' - ' + errorText.substring(0, 200) : ''}`);
  }

  const responseText = await response.text();
  const data = parseMcpResponseBody(responseText, response.headers.get('content-type') || '');

  if (data.error) {
    throw new Error(`MCP error: ${data.error.message || JSON.stringify(data.error)}`);
  }

  const tools: MCPToolInfo[] = (data.result?.tools || data.tools || []).map((tool: any) => ({
    name: tool.name,
    description: tool.description || '',
    inputSchema: tool.inputSchema
  }));

  return tools;
}

/**
 * Parse a JSON-RPC response that arrived either as plain JSON or as an SSE
 * stream (`data: {...}` lines), which streamable HTTP servers may return.
 */
function parseMcpResponseBody(text: string, contentType: string): any {
  const tryJson = (s: string) => { try { return JSON.parse(s); } catch { return undefined; } };

  if (!contentType.includes('text/event-stream')) {
    const parsed = tryJson(text);
    if (parsed !== undefined) return parsed;
  }

  // SSE: use the last `data:` event that parses as a JSON-RPC message.
  const events = text.split(/\r?\n\r?\n/)
    .map(block => block.split(/\r?\n/)
      .filter(line => line.startsWith('data:'))
      .map(line => line.slice(5).trimStart())
      .join('\n'))
    .filter(Boolean)
    .map(tryJson)
    .filter(msg => msg && (msg.result !== undefined || msg.error !== undefined));
  if (events.length) return events[events.length - 1];

  throw new Error('Invalid response from MCP server (expected JSON or an SSE JSON-RPC message).');
}

/**
 * Resolve the bearer token to send for this server's auth mode, or null when
 * the mode does not use one. Throws with an actionable message when a mode is
 * selected but its credential cannot be read or exchanged.
 */
async function resolveMcpBearer(
  server: MCPServerConfig,
  awsConfigService: AwsConfigService,
  agentDynamoDBService?: AgentDynamoDBService,
  agentName?: string
): Promise<string | null> {
  const mode = effectiveMcpAuthType(server);
  if (mode === 'none' || mode === 'aws_iam') return null;

  const ref = mode === 'bearer_token' ? server.bearerToken
    : mode === 'oauth_m2m' ? server.oauthClientCredentials
    : server.oauthToken;
  const stored = mode === 'oauth_m2m'
    ? !!server.oauthClientCredentials?.hasCredentials
    : !!(ref as { hasToken?: boolean } | undefined)?.hasToken;
  if (!stored) {
    throw new Error('Authentication is selected for this server but no credentials are stored. Save them first.');
  }
  if (!agentDynamoDBService) {
    throw new Error('Credential storage is unavailable. Sign in again and retry.');
  }

  const raw = await agentDynamoDBService.getMcpCredentialDocument(agentName || '', server.id, ref?.ssmPath);
  if (!raw) {
    throw new Error('Stored credentials could not be read from SSM. Re-enter them in the server\'s Authentication settings.');
  }

  switch (mode) {
    case 'bearer_token':
      return raw.trim();
    case 'oauth_m2m':
      return acquireClientCredentialsToken(raw, ref?.ssmPath || `mcp:${agentName}:${server.id}`);
    case 'bearer':
      return acquireCognitoPasswordToken(raw, awsConfigService.getRegion());
  }
}

/** Same inference as the editor's `getMcpAuthType`, for records without `authType`. */
function effectiveMcpAuthType(server: MCPServerConfig): NonNullable<MCPServerConfig['authType']> {
  if (server.authType) return server.authType;
  if (server.awsAuth) return 'aws_iam';
  if (server.oauthClientCredentials?.hasCredentials) return 'oauth_m2m';
  if (server.bearerToken?.hasToken) return 'bearer_token';
  if (server.oauthToken?.hasToken) return 'bearer';
  return 'none';
}

/**
 * Exchange a stored `{client_id, username, password}` document for a Cognito
 * access token via USER_PASSWORD_AUTH, the same flow the runtime uses.
 * Error messages never include the password.
 */
async function acquireCognitoPasswordToken(raw: string, region: string): Promise<string> {
  let doc: any;
  try {
    doc = JSON.parse(raw);
  } catch {
    doc = null;
  }
  const clientId = doc?.client_id || doc?.clientId;
  if (!clientId || !doc?.username || !doc?.password) {
    throw new Error(
      'Stored OAuth credentials are missing the Auth Client ID, username, or password. ' +
      'Click Update and re-enter all three.'
    );
  }

  const response = await fetch(`https://cognito-idp.${region}.amazonaws.com/`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-amz-json-1.1',
      'X-Amz-Target': 'AWSCognitoIdentityProviderService.InitiateAuth'
    },
    body: JSON.stringify({
      AuthFlow: 'USER_PASSWORD_AUTH',
      ClientId: clientId,
      AuthParameters: { USERNAME: doc.username, PASSWORD: doc.password }
    })
  });

  if (!response.ok) {
    let reason = `HTTP ${response.status}`;
    try {
      const err = await response.json();
      if (err?.__type) reason += ` ${String(err.__type).split('#').pop()}`;
      if (err?.message) reason += `: ${err.message}`;
    } catch {
      // Keep the status-only reason.
    }
    throw new Error(`Cognito sign-in failed (${reason}).`);
  }

  const data = await response.json();
  const token = data?.AuthenticationResult?.AccessToken;
  if (!token) {
    throw new Error(
      `Cognito returned no access token${data?.ChallengeName ? ` (challenge: ${data.ChallengeName})` : ''}.`
    );
  }
  return token;
}

/**
 * Make a SigV4-signed HTTP request to an AWS IAM authenticated endpoint.
 */
async function fetchWithSigV4(
  url: string,
  body: string,
  headers: Record<string, string>,
  awsAuth: { region: string; service: string },
  awsConfigService: AwsConfigService
): Promise<Response> {
  const { Sha256 } = await import('@aws-crypto/sha256-js');
  const { SignatureV4 } = await import('@smithy/signature-v4');
  const { HttpRequest } = await import('@smithy/protocol-http');

  const awsConfig = await awsConfigService.getAwsConfig();
  if (!awsConfig?.credentials) {
    throw new Error('AWS credentials not available. Please sign in again.');
  }

  const parsedUrl = new URL(url);

  const request = new HttpRequest({
    method: 'POST',
    protocol: parsedUrl.protocol,
    hostname: parsedUrl.hostname,
    port: parsedUrl.port ? parseInt(parsedUrl.port) : undefined,
    path: parsedUrl.pathname,
    query: Object.fromEntries(parsedUrl.searchParams.entries()),
    headers: {
      ...headers,
      host: parsedUrl.hostname
    },
    body
  });

  const signer = new SignatureV4({
    credentials: awsConfig.credentials,
    region: awsAuth.region,
    service: awsAuth.service,
    sha256: Sha256
  });

  const signedRequest = await signer.sign(request);

  const signedHeaders: Record<string, string> = {};
  for (const [key, value] of Object.entries(signedRequest.headers)) {
    signedHeaders[key] = value as string;
  }

  return fetch(url, {
    method: 'POST',
    headers: signedHeaders,
    body
  });
}

/**
 * List tools from an MCP server. Returns the MCPToolListResult to set on the map.
 */
export async function listMcpServerTools(
  server: MCPServerConfig,
  awsConfigService: AwsConfigService,
  agentDynamoDBService?: AgentDynamoDBService,
  agentName?: string
): Promise<MCPToolListResult> {
  if (server.transport === 'stdio') {
    return {
      serverId: server.id,
      tools: [],
      error: 'Tool listing is only available for HTTP and SSE transport servers. stdio servers require a local runtime.',
      loading: false,
      expanded: true
    };
  }

  if (!server.url?.trim()) {
    return {
      serverId: server.id,
      tools: [],
      error: 'No URL configured for this server.',
      loading: false,
      expanded: true
    };
  }

  try {
    const tools = await fetchMcpTools(server, awsConfigService, agentDynamoDBService, agentName);
    return {
      serverId: server.id,
      tools,
      loading: false,
      expanded: true
    };
  } catch (error: any) {
    console.error('Error listing MCP tools:', error);
    return {
      serverId: server.id,
      tools: [],
      error: error.message || 'Failed to connect to MCP server.',
      loading: false,
      expanded: true
    };
  }
}
