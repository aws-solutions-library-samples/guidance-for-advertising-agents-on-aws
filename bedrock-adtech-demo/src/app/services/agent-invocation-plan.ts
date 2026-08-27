/**
 * How to invoke an agent, derived from three independent settings.
 *
 * These three used to be one `is_a2a` flag plus a runtime ARN, which meant the
 * auth mode ended up choosing the transport: supplying a bearer token to an
 * ARN-addressed agent silently routed the call through the SigV4 AgentCore SDK,
 * which cannot carry a bearer at all. The axes are kept separate here:
 *
 * - **protocol** — the request envelope and response shape (`a2a` JSON-RPC 2.0
 *   or plain `http` JSON). Nothing else.
 * - **endpoint** — where the agent lives: an AgentCore runtime ARN or an
 *   absolute URL.
 * - **auth** — which credentials to present. Never selects a transport.
 *
 * The transport is then *derived*, never configured, because only some
 * combinations are physically possible: `InvokeAgentRuntimeCommand` requires an
 * ARN and always signs with SigV4, so any header-based credential has to go
 * over the runtime's HTTPS data-plane endpoint instead.
 */

/** Request envelope and response shape. Independent of endpoint and auth. */
export type AgentProtocol = 'a2a' | 'http';

/** Credential mode. Supplies credentials only — never picks a transport. */
export type AgentAuthType = 'none' | 'oauth' | 'oauth_m2m' | 'iam' | 'bearer';

export type EndpointKind = 'arn' | 'url' | 'none';

/**
 * - `agentcore_sdk`: `InvokeAgentRuntimeCommand`, SigV4-signed by the AWS SDK.
 * - `https`: a direct POST this code signs or authorizes itself.
 */
export type InvocationTransport = 'agentcore_sdk' | 'https';

export interface ResolvedEndpoint {
  kind: EndpointKind;
  /** The raw configured value, trimmed. */
  value: string;
}

export interface InvocationPlan {
  protocol: AgentProtocol;
  endpoint: ResolvedEndpoint;
  transport: InvocationTransport;
  /** URL to POST to. Empty when transport is `agentcore_sdk`. */
  requestUrl: string;
  authType: AgentAuthType;
  /** Set when the configuration cannot be invoked at all. */
  problem?: string;
  /** Set when the call is possible but worth flagging. */
  warning?: string;
}

/** Auth modes that authorize a request with an `Authorization` header. */
export function authUsesHeader(authType: AgentAuthType): boolean {
  return authType === 'bearer' || authType === 'oauth' || authType === 'oauth_m2m';
}

/** Classify a configured endpoint as an AgentCore ARN, a URL, or absent. */
export function classifyEndpoint(raw: string | undefined | null): ResolvedEndpoint {
  const value = (raw || '').trim();
  if (!value) return { kind: 'none', value: '' };
  if (value.startsWith('arn:')) return { kind: 'arn', value };
  if (/^https?:\/\//i.test(value)) return { kind: 'url', value };
  return { kind: 'none', value };
}

/** The HTTPS data-plane URL that invokes an AgentCore runtime ARN. */
export function buildAgentCoreInvocationUrl(
  arn: string,
  region: string,
  qualifier: string = 'DEFAULT'
): string {
  return `https://bedrock-agentcore.${region}.amazonaws.com/runtimes/${encodeURIComponent(arn)}/invocations?qualifier=${qualifier}`;
}

/**
 * Protocol for a top-level agent config.
 *
 * `agent_protocol` is authoritative. Records predating it are read through the
 * old `is_a2a` flag, which meant "speaks A2A" among other things.
 */
export function resolveAgentProtocol(config: {
  agent_protocol?: AgentProtocol;
  is_a2a?: boolean;
}): AgentProtocol {
  if (config.agent_protocol) return config.agent_protocol;
  return config.is_a2a ? 'a2a' : 'http';
}

/**
 * Endpoint for a top-level agent config. `agent_endpoint` accepts an ARN or a
 * URL; `runtime_arn` is the older ARN-only field.
 */
export function resolveAgentEndpoint(config: {
  agent_endpoint?: string;
  runtime_arn?: string;
}): ResolvedEndpoint {
  const explicit = classifyEndpoint(config.agent_endpoint);
  if (explicit.kind !== 'none') return explicit;
  return classifyEndpoint(config.runtime_arn);
}

/** Protocol for an external A2A/HTTP peer entry. */
export function resolveEntryProtocol(entry: {
  protocol?: AgentProtocol;
  isA2A?: boolean;
}): AgentProtocol {
  if (entry.protocol) return entry.protocol;
  return entry.isA2A ? 'a2a' : 'http';
}

/** Endpoint for an external A2A/HTTP peer entry. */
export function resolveEntryEndpoint(entry: {
  endpoint?: string;
  arn?: string;
}): ResolvedEndpoint {
  const explicit = classifyEndpoint(entry.endpoint);
  if (explicit.kind !== 'none') return explicit;
  return classifyEndpoint(entry.arn);
}

/**
 * Derive the transport for a protocol/endpoint/auth combination.
 *
 * Returns a plan with `problem` set rather than throwing, so callers can report
 * a specific misconfiguration instead of failing opaquely mid-request.
 */
export function planInvocation(input: {
  endpoint: ResolvedEndpoint;
  protocol: AgentProtocol;
  authType: AgentAuthType;
  region: string;
  qualifier?: string;
}): InvocationPlan {
  const { endpoint, protocol, authType, region } = input;

  const base: InvocationPlan = {
    protocol,
    endpoint,
    transport: 'https',
    requestUrl: '',
    authType
  };

  if (endpoint.kind === 'none') {
    return {
      ...base,
      problem: endpoint.value
        ? `Endpoint "${endpoint.value}" is neither an AgentCore runtime ARN (arn:...) nor an absolute URL (https://...).`
        : 'No endpoint configured. Set an AgentCore runtime ARN or an agent URL.'
    };
  }

  if (endpoint.kind === 'url') {
    // A URL is invoked directly. The AgentCore SDK cannot address it, so the
    // transport does not depend on the auth mode here.
    const plan: InvocationPlan = { ...base, transport: 'https', requestUrl: endpoint.value };
    if (/^http:\/\//i.test(endpoint.value) && authUsesHeader(authType)) {
      plan.warning =
        'Credentials will be sent over plain HTTP. Use https:// unless this endpoint is local.';
    }
    return plan;
  }

  // ARN endpoint. SigV4 is only available through the SDK, and the SDK cannot
  // attach an Authorization header — so the credential mode decides which of
  // the two AgentCore transports can carry the call.
  if (authUsesHeader(authType)) {
    return {
      ...base,
      transport: 'https',
      requestUrl: buildAgentCoreInvocationUrl(endpoint.value, region, input.qualifier)
    };
  }

  return { ...base, transport: 'agentcore_sdk', requestUrl: '' };
}

/**
 * One-line description of what a plan will actually do, for the editor UI so an
 * operator can see the derived transport instead of inferring it.
 */
export function describeInvocationPlan(plan: InvocationPlan): string {
  const envelope = plan.protocol === 'a2a' ? 'A2A JSON-RPC 2.0' : 'HTTP JSON';
  if (plan.problem) return plan.problem;
  if (plan.transport === 'agentcore_sdk') {
    return `${envelope} over InvokeAgentRuntime (SigV4-signed by the AWS SDK).`;
  }
  if (plan.endpoint.kind === 'arn') {
    return `${envelope} over the runtime's HTTPS data-plane endpoint, authorized with a bearer token.`;
  }
  return `${envelope} POSTed directly to ${plan.endpoint.value}.`;
}
