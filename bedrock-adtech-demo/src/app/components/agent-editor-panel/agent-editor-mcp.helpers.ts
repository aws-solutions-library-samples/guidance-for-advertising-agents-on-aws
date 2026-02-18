/**
 * MCP Server helper functions extracted from AgentEditorPanelComponent.
 * These are pure/stateless utility functions that operate on passed-in data.
 */
import { MCPServerConfig } from '../agent-management-modal/agent-management-modal.component';
import { MCPToolInfo, MCPToolListResult } from './agent-editor-panel.constants';
import { AwsConfigService } from '../../services/aws-config.service';

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
 * Handles both plain HTTP and AWS IAM SigV4 authenticated requests.
 */
export async function fetchMcpTools(
  server: MCPServerConfig,
  awsConfigService: AwsConfigService
): Promise<MCPToolInfo[]> {
  const url = server.url!;

  const jsonRpcBody = JSON.stringify({
    jsonrpc: '2.0',
    id: 1,
    method: 'tools/list',
    params: {}
  });

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    'Accept': 'application/json'
  };

  if (server.headers) {
    Object.assign(headers, server.headers);
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
  let data: any;

  try {
    data = JSON.parse(responseText);
  } catch {
    throw new Error('Invalid JSON response from MCP server');
  }

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
  awsConfigService: AwsConfigService
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
    const tools = await fetchMcpTools(server, awsConfigService);
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
