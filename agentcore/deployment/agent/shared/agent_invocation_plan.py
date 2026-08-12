"""
How to invoke an agent, derived from three independent settings.

Mirrors ``bedrock-adtech-demo/src/app/services/agent-invocation-plan.ts`` so the
UI and the runtime agree on what a given configuration means.

These three used to be one ``isA2A``/``is_a2a`` flag plus a runtime ARN, which
meant the auth mode ended up choosing the transport. They are kept separate:

- **protocol** — the request envelope and response shape (``a2a`` JSON-RPC 2.0
  or plain ``http`` JSON). Nothing else.
- **endpoint** — where the agent lives: an AgentCore runtime ARN or an absolute
  URL.
- **auth** — which credentials to present. Never selects a transport.

The transport is then derived, never configured, because only some combinations
are physically possible: ``invoke_agent_runtime`` requires an ARN and always
signs with SigV4, so any header-based credential has to go over the runtime's
HTTPS data-plane endpoint instead.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote

# Request envelopes.
PROTOCOL_A2A = "a2a"
PROTOCOL_HTTP = "http"

# Endpoint kinds.
ENDPOINT_ARN = "arn"
ENDPOINT_URL = "url"
ENDPOINT_NONE = "none"

# Transports.
TRANSPORT_AGENTCORE_SDK = "agentcore_sdk"
TRANSPORT_HTTPS = "https"

# Auth modes that authorize a request with an ``Authorization`` header, and so
# cannot travel over the SigV4-only SDK transport.
HEADER_AUTH_TYPES = frozenset({"bearer", "oauth", "oauth_m2m"})


def auth_uses_header(auth_type: Optional[str]) -> bool:
    """Whether this auth mode authorizes with an ``Authorization`` header."""
    return (auth_type or "none").lower() in HEADER_AUTH_TYPES


def classify_endpoint(raw: Optional[str]) -> Tuple[str, str]:
    """Classify a configured endpoint. Returns ``(kind, value)``."""
    value = (raw or "").strip()
    if not value:
        return ENDPOINT_NONE, ""
    if value.startswith("arn:"):
        return ENDPOINT_ARN, value
    lowered = value.lower()
    if lowered.startswith("https://") or lowered.startswith("http://"):
        return ENDPOINT_URL, value
    return ENDPOINT_NONE, value


def build_agentcore_invocation_url(
    arn: str, region: str, qualifier: str = "DEFAULT"
) -> str:
    """The HTTPS data-plane URL that invokes an AgentCore runtime ARN."""
    return (
        f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/"
        f"{quote(arn, safe='')}/invocations?qualifier={qualifier}"
    )


def resolve_agent_protocol(config: Dict[str, Any]) -> str:
    """Protocol for a top-level agent config, falling back to the old flag."""
    protocol = (config.get("agent_protocol") or "").lower()
    if protocol in (PROTOCOL_A2A, PROTOCOL_HTTP):
        return protocol
    return PROTOCOL_A2A if config.get("is_a2a") else PROTOCOL_HTTP


def resolve_agent_endpoint(config: Dict[str, Any]) -> Tuple[str, str]:
    """Endpoint for a top-level agent config. ``agent_endpoint`` wins over ``runtime_arn``."""
    kind, value = classify_endpoint(config.get("agent_endpoint"))
    if kind != ENDPOINT_NONE:
        return kind, value
    return classify_endpoint(config.get("runtime_arn"))


def resolve_entry_protocol(entry: Dict[str, Any]) -> str:
    """Protocol for an external peer entry, falling back to the old flag."""
    protocol = (entry.get("protocol") or "").lower()
    if protocol in (PROTOCOL_A2A, PROTOCOL_HTTP):
        return protocol
    return PROTOCOL_A2A if entry.get("isA2A") else PROTOCOL_HTTP


def resolve_entry_endpoint(entry: Dict[str, Any]) -> Tuple[str, str]:
    """Endpoint for an external peer entry. ``endpoint`` wins over ``arn``."""
    kind, value = classify_endpoint(entry.get("endpoint"))
    if kind != ENDPOINT_NONE:
        return kind, value
    # Some deploy-written entries use runtime_arn instead of arn.
    kind, value = classify_endpoint(entry.get("arn"))
    if kind != ENDPOINT_NONE:
        return kind, value
    return classify_endpoint(entry.get("runtime_arn"))


@dataclass
class InvocationPlan:
    """A resolved, ready-to-execute description of how to call an agent."""

    protocol: str
    endpoint_kind: str
    endpoint: str
    transport: str
    #: URL to POST to. Empty when ``transport`` is ``agentcore_sdk``.
    request_url: str
    auth_type: str
    #: Set when the configuration cannot be invoked at all.
    problem: Optional[str] = None

    @property
    def is_a2a(self) -> bool:
        return self.protocol == PROTOCOL_A2A

    @property
    def uses_sdk(self) -> bool:
        return self.transport == TRANSPORT_AGENTCORE_SDK


def plan_invocation(
    endpoint_kind: str,
    endpoint: str,
    protocol: str,
    auth_type: str,
    region: str,
    qualifier: str = "DEFAULT",
) -> InvocationPlan:
    """Derive the transport for a protocol/endpoint/auth combination.

    Returns a plan with ``problem`` set rather than raising, so callers can
    report a specific misconfiguration instead of failing opaquely.
    """
    auth_type = (auth_type or "none").lower()
    plan = InvocationPlan(
        protocol=protocol,
        endpoint_kind=endpoint_kind,
        endpoint=endpoint,
        transport=TRANSPORT_HTTPS,
        request_url="",
        auth_type=auth_type,
    )

    if endpoint_kind == ENDPOINT_NONE:
        plan.problem = (
            f'Endpoint "{endpoint}" is neither an AgentCore runtime ARN (arn:...) '
            f"nor an absolute URL (https://...)."
            if endpoint
            else "No endpoint configured. Set an AgentCore runtime ARN or an agent URL."
        )
        return plan

    if endpoint_kind == ENDPOINT_URL:
        # A URL is posted to directly; the SDK cannot address it, so the auth
        # mode does not affect the transport here.
        plan.request_url = endpoint
        return plan

    # ARN endpoint. SigV4 is only available through the SDK, and the SDK cannot
    # attach an Authorization header — so the credential mode decides which of
    # the two AgentCore transports can carry the call.
    if auth_uses_header(auth_type):
        plan.request_url = build_agentcore_invocation_url(endpoint, region, qualifier)
        return plan

    plan.transport = TRANSPORT_AGENTCORE_SDK
    return plan


def plan_for_entry(
    entry: Dict[str, Any], region: str, qualifier: str = "DEFAULT"
) -> InvocationPlan:
    """Build a plan for an ``external_agent_configs`` entry."""
    kind, value = resolve_entry_endpoint(entry)
    return plan_invocation(
        endpoint_kind=kind,
        endpoint=value,
        protocol=resolve_entry_protocol(entry),
        auth_type=(entry.get("authType") or "none").lower(),
        region=region,
        qualifier=qualifier,
    )


def plan_for_agent(
    config: Dict[str, Any], region: str, qualifier: str = "DEFAULT"
) -> InvocationPlan:
    """Build a plan for a top-level agent config."""
    kind, value = resolve_agent_endpoint(config)
    return plan_invocation(
        endpoint_kind=kind,
        endpoint=value,
        protocol=resolve_agent_protocol(config),
        auth_type=(config.get("a2a_auth_type") or "none").lower(),
        region=region,
        qualifier=qualifier,
    )


def sigv4_headers(
    url: str, body: bytes, headers: Dict[str, str], service: str, region: str
) -> Dict[str, str]:
    """SigV4-sign a POST and return the headers to send.

    Used for the one IAM case the AWS SDK cannot cover: a URL endpoint, which
    ``invoke_agent_runtime`` cannot address.
    """
    import boto3
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest

    credentials = boto3.Session().get_credentials()
    if credentials is None:
        raise RuntimeError("No AWS credentials available to sign the request.")

    request = AWSRequest(method="POST", url=url, data=body, headers=dict(headers))
    SigV4Auth(credentials.get_frozen_credentials(), service, region).add_auth(request)
    return dict(request.headers)


def signing_service_for(plan: InvocationPlan, entry: Optional[Dict[str, Any]] = None) -> str:
    """SigV4 service name for a plan.

    An AgentCore ARN is invoked against ``bedrock-agentcore``. A URL endpoint is
    whatever the entry declares, defaulting to API Gateway, which is what an
    IAM-authorized agent URL normally is.
    """
    if plan.endpoint_kind == ENDPOINT_ARN:
        return "bedrock-agentcore"
    declared = ((entry or {}).get("awsAuth") or {}).get("service")
    return declared or "execute-api"
