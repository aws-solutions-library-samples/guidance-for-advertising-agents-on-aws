"""A2A client tool provider construction for Strands agents.

Builds A2AClientToolProvider instances from an agent's
``external_agent_configs`` list, handling OAuth, IAM, and no-auth paths.

Error handling wraps every provider creation in try/except so that a
single misconfigured external agent never prevents the remaining agents
from being registered.  A 120-second timeout is applied to all outbound
HTTP requests.
"""

import hashlib
import logging
import os
import re
from typing import Any, Dict, List
from uuid import uuid4

from strands_tools.a2a_client import A2AClientToolProvider
from shared.a2a_auth import A2ATokenManager

logger = logging.getLogger(__name__)

# Timeout in seconds for all outbound A2A HTTP requests (Requirement 9.1)
A2A_REQUEST_TIMEOUT_SECONDS = 120

# Length bounds AgentCore enforces on runtimeSessionId. The front-end generates
# session ids that already satisfy the minimum, which lets this module reuse the
# front-end id verbatim so browser-initiated and tool-initiated calls land on the
# same external runtime session.
RUNTIME_SESSION_ID_MIN_LENGTH = 33
RUNTIME_SESSION_ID_MAX_LENGTH = 256

# Front-end session id for the invocation currently being served.
#
# Tools are built once per agent instance and then reused across turns — and the
# runtime reuses a warm agent even when the user has moved to a different
# conversation. A session id captured when the tool was built would therefore go
# stale, sending a later conversation's traffic to the earlier conversation's
# external runtime session. The handler sets this per invocation and the tools
# read it at call time so the id always tracks the live conversation.
_active_front_end_session_id: str = ""


def set_active_session_id(session_id: str) -> None:
    """Record the front-end session id for the invocation being served now.

    Called by the handler on every invocation, before any tool runs.
    """
    global _active_front_end_session_id
    _active_front_end_session_id = session_id or ""


def get_active_runtime_session_id(fallback_runtime_session_id: str = "") -> str:
    """Resolve the runtimeSessionId a tool should use for a call happening now.

    Prefers the live invocation's front-end session id. Falls back to the value
    captured when the tool was built, which is the best available answer when the
    handler has not set one (for example a directly-constructed tool in a test).
    """
    if _active_front_end_session_id:
        return _derive_runtime_session_id(_active_front_end_session_id)
    return fallback_runtime_session_id


def _sanitize_error_message(error: Exception) -> str:
    """Return a safe error description that never leaks credentials.

    Strips common credential-bearing fields from the string representation
    of the exception so that passwords, tokens, and secrets are not
    propagated to the calling agent or logs.
    """
    msg = str(error)
    # Remove anything that looks like a bearer token or password value
    for sensitive_keyword in ("password", "secret", "token", "credential", "Bearer"):
        if sensitive_keyword.lower() in msg.lower():
            msg = f"{type(error).__name__}: [details redacted for security]"
            break
    return msg


def build_bearer_auth_header(token: str) -> Dict[str, str]:
    """Build the outbound Authorization header for a static bearer token.

    The token is embedded verbatim with no encoding or transformation, so
    parsing the header value back (stripping the ``Bearer `` scheme prefix)
    yields exactly the original token. Centralized so the outbound provider
    path and the AgentCore-ARN invoke path stay byte-for-byte identical.
    """
    return {"Authorization": f"Bearer {token}"}


def build_a2a_client_tools(
    agent_name: str, agent_config: dict, session_id: str = ""
) -> List:
    """Build A2AClientToolProvider instances from external_agent_configs.

    For every entry where ``isA2A`` is True and ``enabled`` is True, creates
    an A2AClientToolProvider with the entry's ARN as the endpoint.

    Authentication is configured per-entry:
    - oauth: retrieves a bearer token via A2ATokenManager.
    - iam: creates the provider without extra auth (SigV4 handled by SDK).
    - none: no authentication headers.

    Error handling (Requirements 9.1–9.5):
    - Each provider creation is wrapped in try/except so one failure does
      not prevent the remaining providers from being built.
    - A 120-second timeout is set on all outbound HTTP requests.
    - Timeout, connection, and auth errors produce descriptive messages
      without exposing credentials.
    - Failed invocations are **not** retried automatically.

    Args:
        agent_name: Name of the owning agent (for logging).
        agent_config: Agent configuration dict with ``external_agent_configs``.
        session_id: Front-end conversation session id. Threaded to the
            AgentCore invoke tools as a stable runtimeSessionId so external
            runtimes retain conversation continuity across turns.

    Returns:
        List of A2AClientToolProvider instances.
    """
    external_configs = agent_config.get("external_agent_configs", [])
    if not external_configs:
        return []

    providers: List = []
    token_manager = None

    for entry in external_configs:
        if not entry.get("isA2A", False) or not entry.get("enabled", False):
            continue

        entry_name = entry.get("name", "unknown")
        arn = entry.get("arn", "")
        if not arn:
            logger.warning(
                "⚠️ A2A_TOOLS: Skipping entry '%s' for %s — missing ARN",
                entry_name,
                agent_name,
            )
            continue

        auth_type = entry.get("authType", "none")

        try:
            # Base httpx client args with 120-second timeout (Req 9.1)
            httpx_args: Dict[str, Any] = {
                "timeout": A2A_REQUEST_TIMEOUT_SECONDS,
            }

            if auth_type == "oauth":
                oauth_creds = entry.get("oauthCredentials", {})
                if oauth_creds.get("hasCredentials") and oauth_creds.get("ssmPath"):
                    if token_manager is None:
                        token_manager = A2ATokenManager()

                    pool_id = entry.get(
                        "cognitoPoolId", os.environ.get("A2A_POOL_ID", "")
                    )
                    client_id = entry.get(
                        "cognitoClientId", os.environ.get("A2A_CLIENT_ID", "")
                    )
                    token, err = token_manager.get_bearer_token(
                        oauth_creds["ssmPath"], pool_id, client_id
                    )
                    if err:
                        # Auth error — log without credential details
                        logger.error(
                            "❌ A2A_TOOLS: OAuth token acquisition failed for '%s' "
                            "(agent=%s)",
                            entry_name,
                            agent_name,
                        )
                        continue

                    httpx_args["headers"] = {
                        "Authorization": f"Bearer {token}"
                    }
                else:
                    logger.warning(
                        "⚠️ A2A_TOOLS: OAuth configured but no credentials for '%s'",
                        entry_name,
                    )
                    continue

            elif auth_type == "bearer":
                # Static, operator-pasted bearer token. Stored verbatim in an
                # SSM SecureString and sent verbatim as `Authorization: Bearer
                # <token>` — no Cognito exchange, SigV4, or token minting, so
                # this works against A2A peers that are not on AWS.
                bearer = entry.get("bearerToken") or {}
                ssm_path = bearer.get("ssmPath", "")
                if not bearer.get("hasToken") or not ssm_path:
                    # Fail closed — never send the request unauthenticated.
                    logger.warning(
                        "⚠️ A2A_TOOLS: Bearer Token selected but no token stored "
                        "for '%s' — skipping",
                        entry_name,
                    )
                    continue

                # Local import avoids any import-time coupling with the config
                # loader; use_cache=False so a freshly re-pasted token (after
                # expiry) is picked up without a runtime restart.
                from shared.dynamodb_config_loader import resolve_ssm_parameter

                token = resolve_ssm_parameter(ssm_path, use_cache=False)
                if not token:
                    logger.warning(
                        "⚠️ A2A_TOOLS: Bearer token unavailable in parameter "
                        "store for '%s' — skipping",
                        entry_name,
                    )
                    continue

                httpx_args["headers"] = build_bearer_auth_header(token)

            # Create the provider with timeout-enabled httpx args
            provider = A2AClientToolProvider(
                agent_url=arn,
                httpx_client_args=httpx_args,
            )

            providers.append(provider)
            logger.info(
                "✅ A2A_TOOLS: Created provider for '%s' "
                "(auth=%s) targeting %s",
                entry_name,
                auth_type,
                arn,
            )

        except Exception as e:
            # Catch-all: log at ERROR with agent name and endpoint,
            # but sanitize the message to avoid leaking credentials (Req 9.2, 9.4)
            safe_msg = _sanitize_error_message(e)
            logger.error(
                "❌ A2A_TOOLS: Failed to create provider for '%s' "
                "(agent=%s, endpoint=%s): %s",
                entry_name,
                agent_name,
                arn,
                safe_msg,
            )
            # Continue processing remaining entries — do not fail the whole list

    if providers:
        logger.info(
            "🔗 A2A_TOOLS: Built %d A2A client tool provider(s) for %s",
            len(providers),
            agent_name,
        )

    # Check if any entries use AgentCore ARNs (not HTTP URLs).
    # A2AClientToolProvider expects HTTP URLs for A2A protocol discovery.
    # AgentCore runtimes use ARNs and must be invoked via the
    # bedrock-agentcore InvokeAgentRuntime API instead.
    has_agentcore_arns = any(
        (entry.get("arn", "") or entry.get("runtime_arn", "")).startswith("arn:aws:bedrock-agentcore")
        for entry in external_configs
    )

    if has_agentcore_arns:
        # Skip A2A HTTP tools — they'll fail against AgentCore ARNs.
        # Use direct InvokeAgentRuntime instead.
        logger.info(
            "🔗 A2A_TOOLS: AgentCore ARNs detected — using direct invoke instead of A2A HTTP protocol"
        )
        agentcore_tools = _build_agentcore_invoke_tools(
            agent_name, external_configs, session_id=session_id
        )
        return agentcore_tools

    # For non-AgentCore entries (real HTTP URLs), extract tools from providers.
    # If a provider exposes no tools (or raises), fall back to passing the
    # provider object itself — Strands Agent accepts providers as tool sources.
    all_tools = []
    for provider in providers:
        try:
            provider_tools = provider.tools
            if provider_tools:
                all_tools.extend(provider_tools)
                logger.info(
                    "🔗 A2A_TOOLS: Extracted %d tool(s) from provider for %s",
                    len(provider_tools),
                    agent_name,
                )
            else:
                # If .tools is empty, fall back to the provider object itself —
                # Strands Agent accepts providers as tool sources.
                logger.warning(
                    "⚠️ A2A_TOOLS: Provider returned no tools for %s — using provider object",
                    agent_name,
                )
                all_tools.append(provider)
        except Exception as e:
            safe_msg = _sanitize_error_message(e)
            logger.error(
                "❌ A2A_TOOLS: Failed to extract tools from provider for %s: %s — using provider object",
                agent_name,
                safe_msg,
            )
            all_tools.append(provider)

    return all_tools


def _extract_a2a_text(parsed: dict, raw: str) -> str:
    """Extract human-readable text from an A2A JSON-RPC 2.0 response.

    A2A servers return HTTP 200 even for errors, with the real status in the
    JSON-RPC body. A success carries ``result`` (a Message or a Task with
    ``artifacts``); a failure carries ``error`` with a code/message.
    """
    # JSON-RPC error (HTTP is still 200 per the A2A contract)
    error = parsed.get("error")
    if error:
        code = error.get("code", "unknown")
        msg = error.get("message", "A2A request failed")
        return f"A2A error {code}: {msg}"

    result = parsed.get("result")
    if not isinstance(result, dict):
        return raw

    texts: List[str] = []

    # Task response: result.artifacts[].parts[].text
    for artifact in result.get("artifacts", []) or []:
        for part in artifact.get("parts", []) or []:
            text = part.get("text")
            if text:
                texts.append(text)

    # Message response: result.parts[].text
    if not texts:
        for part in result.get("parts", []) or []:
            text = part.get("text")
            if text:
                texts.append(text)

    return "\n".join(texts) if texts else raw


def _resolve_oauth_ssm_path(entry: dict) -> str:
    """Return the SSM path holding the target's inbound OAuth credentials.

    Prefers the explicit ``oauthCredentials.ssmPath`` set on the entry (the UI
    writes this when an operator enables A2A on an agent's Inbound
    Authentication settings). Falls back to the repo's path convention
    ``/{STACK_PREFIX}/a2a-inbound-tokens/{UNIQUE_ID}/{name}`` when both env
    vars are present. Returns "" when it cannot be resolved — the caller then
    surfaces an explicit "not configured" error rather than guessing.
    """
    oauth_creds = entry.get("oauthCredentials") or {}
    ssm_path = oauth_creds.get("ssmPath") or ""
    if ssm_path:
        return ssm_path

    stack_prefix = os.environ.get("STACK_PREFIX", "")
    unique_id = os.environ.get("UNIQUE_ID", "")
    if not (stack_prefix and unique_id):
        return ""
    # The inbound credentials are stored under the target agent's name; strip a
    # trailing "_Runtime" suffix from the external config entry name.
    name = entry.get("name", "")
    if name.endswith("_Runtime"):
        name = name[: -len("_Runtime")]
    if not name:
        return ""
    return f"/{stack_prefix}/a2a-inbound-tokens/{unique_id}/{name}"


# Module-level token manager so the in-memory bearer cache is shared across
# repeated tool invocations within the runtime process.
_TOKEN_MANAGER = None


def _get_token_manager():
    global _TOKEN_MANAGER
    if _TOKEN_MANAGER is None:
        _TOKEN_MANAGER = A2ATokenManager()
    return _TOKEN_MANAGER


def _derive_runtime_session_id(session_id: str) -> str:
    """Return the AgentCore runtimeSessionId for a front-end conversation.

    An external runtime keeps one continuous session per front-end conversation
    only if every turn reaches it with the SAME runtimeSessionId — and that has
    to hold across BOTH callers: the browser, which puts the front-end session
    id straight into the
    ``X-Amzn-Bedrock-AgentCore-Runtime-Session-Id`` header, and this module,
    which invokes the same runtime as a tool. So whenever the front-end session
    id is already a legal runtimeSessionId it is used VERBATIM. Decorating it
    (an earlier version prefixed ``a2a-``) split a single conversation into two
    runtime sessions depending on which path happened to invoke.

    AgentCore requires 33-256 characters. The front-end already generates ids
    that satisfy this (see ``generateSessionId`` in session-manager.service.ts),
    so the verbatim path is the normal case; the transformations below exist
    only for ids that could not be sent as-is:

    - characters outside ``[A-Za-z0-9_-]`` are replaced, since they cannot go in
      the id at all;
    - ids shorter than the minimum are extended with a hash OF THE ORIGINAL id,
      deterministically, so the result is still stable turn over turn.

    A random id is returned ONLY when there is no front-end session id at all.
    Continuity is genuinely impossible in that case, so starting a fresh session
    is the honest outcome — better than colliding unrelated conversations onto
    one shared id.
    """
    if not session_id:
        return f"a2a-{uuid4().hex}{uuid4().hex}"

    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "-", session_id)

    if len(sanitized) >= RUNTIME_SESSION_ID_MIN_LENGTH:
        # Already valid — pass through so this matches the browser's header byte
        # for byte (identical when the id needed no character substitution).
        return sanitized[:RUNTIME_SESSION_ID_MAX_LENGTH]

    # Too short to be accepted. Pad deterministically from the original id so
    # every turn of this conversation still derives the same value.
    pad = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return f"{sanitized}-{pad}"[:RUNTIME_SESSION_ID_MAX_LENGTH]


def _invoke_agentcore_oauth(
    arn: str,
    region: str,
    payload: bytes,
    ssm_path: str,
    client_id: str = "",
    session_id: str = "",
):
    """Invoke an OAuth-protected AgentCore runtime over HTTPS with a bearer.

    Mirrors the UI's OAuth invoke path: acquire a Cognito bearer from the
    stored credentials (via A2ATokenManager) and POST to the runtime's
    data-plane invocations endpoint. Returns ``(response_text, error)`` where
    exactly one is non-None. The bearer never appears in the returned error.

    ``session_id`` is the AgentCore runtimeSessionId to use — pass the derived
    front-end session id so the external runtime keeps a continuous session.
    """
    import requests
    from urllib.parse import quote

    if not ssm_path:
        return None, (
            "OAuth credentials not configured for this agent. Store the "
            "inbound Cognito credentials (Auth Client ID, Username, Password) "
            "via the agent's Inbound Authentication settings."
        )

    token, err = _get_token_manager().get_bearer_token(ssm_path, client_id=client_id)
    if err or not token:
        return None, (err or "Failed to acquire OAuth bearer token")

    endpoint = (
        f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/"
        f"{quote(arn, safe='')}/invocations?qualifier=DEFAULT"
    )
    # Use the caller-provided (front-end-derived) runtime session id so the
    # external runtime maintains a continuous session across turns. Only fall
    # back to a random id when none was supplied.
    runtime_session_id = session_id or f"a2a-{uuid4().hex}{uuid4().hex}"
    try:
        resp = requests.post(
            endpoint,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
                "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": runtime_session_id,
            },
            timeout=A2A_REQUEST_TIMEOUT_SECONDS,
        )
    except Exception as e:  # noqa: BLE001 - surface a sanitized transport error
        return None, _sanitize_error_message(e)

    if resp.status_code >= 400:
        # Body may echo request detail; keep it short and free of the bearer.
        return None, f"OAuth invocation failed (HTTP {resp.status_code})"
    return resp.text, None


def _invoke_agentcore_bearer(
    arn: str,
    region: str,
    payload: bytes,
    token: str,
    session_id: str = "",
):
    """Invoke a bearer-protected AgentCore runtime over HTTPS with a static token.

    Sends the operator-provided token verbatim as ``Authorization: Bearer
    <token>`` over the data-plane invocations endpoint — no Cognito exchange
    and no SigV4. Returns ``(response_text, error)`` where exactly one is
    non-None. The token never appears in the returned error.

    ``session_id`` is the AgentCore runtimeSessionId — pass the derived
    front-end session id so the external runtime keeps a continuous session.
    """
    import requests
    from urllib.parse import quote

    if not token:
        # Fail closed — the caller must not proceed unauthenticated.
        return None, "Bearer token not configured for this agent."

    endpoint = (
        f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/"
        f"{quote(arn, safe='')}/invocations?qualifier=DEFAULT"
    )
    runtime_session_id = session_id or f"a2a-{uuid4().hex}{uuid4().hex}"
    try:
        resp = requests.post(
            endpoint,
            data=payload,
            headers={
                "Content-Type": "application/json",
                **build_bearer_auth_header(token),
                "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": runtime_session_id,
            },
            timeout=A2A_REQUEST_TIMEOUT_SECONDS,
        )
    except Exception as e:  # noqa: BLE001 - surface a sanitized transport error
        return None, _sanitize_error_message(e)

    if resp.status_code >= 400:
        # Keep the message short and free of the token value.
        return None, f"Bearer invocation failed (HTTP {resp.status_code})"
    return resp.text, None


def _build_agentcore_invoke_tools(
    agent_name: str, external_configs: list, session_id: str = ""
) -> List:
    """Build @tool functions that invoke AgentCore runtimes via boto3.

    When A2AClientToolProvider fails (because AgentCore ARNs are not HTTP URLs),
    this creates simple tool functions that call the runtime via the
    bedrock-agentcore InvokeAgentRuntime API.

    Each external agent config with an ARN gets a dedicated tool function.

    ``session_id`` is the front-end conversation session id. It is derived into
    a stable AgentCore runtimeSessionId and passed on every invoke so the
    external runtime keeps a continuous session across turns (without it, each
    turn would start a fresh session and the external agent would forget prior
    context).
    """
    from strands import tool as strands_tool

    # Derive once so every tool built in this pass shares the same stable
    # runtime session id for this front-end conversation.
    runtime_session_id = _derive_runtime_session_id(session_id)

    tools = []

    for entry in external_configs:
        arn = entry.get("arn", entry.get("runtime_arn", ""))
        if not arn or not arn.startswith("arn:aws:bedrock-agentcore"):
            continue
        # Respect the operator's enable/disable switch. Without this a disabled
        # external agent would still get a live invoke tool, so turning it off
        # in the console would have no effect on the runtime.
        if not entry.get("enabled", True):
            logger.info(
                "⏭️ AGENTCORE_INVOKE: Skipping disabled entry '%s' for %s",
                entry.get("name", "unknown"),
                agent_name,
            )
            continue

        entry_name = entry.get("name", entry.get("agent_name", "seller_agent"))
        # Create a clean tool name from the entry name
        tool_name = f"invoke_{entry_name.lower().replace(' ', '_').replace('-', '_')}"
        description = entry.get("description", f"Invoke the {entry_name} runtime")
        region = entry.get("awsAuth", {}).get("region", os.environ.get("AWS_REGION", "us-west-2"))

        # The request envelope depends on the target's protocol. Entries
        # flagged ``isA2A`` (external AgentCore runtimes deployed with
        # --protocol A2A) speak JSON-RPC 2.0 and reject the legacy
        # {"prompt", "routing_mode"} envelope. The runtime_arn schema used by
        # CrewAI crew runtimes (AAMP seller agents) has no isA2A flag and
        # expects that legacy envelope, so it remains the default.
        is_a2a = bool(entry.get("isA2A", False))
        auth_type = (entry.get("authType") or "none").lower()
        oauth_ssm_path = _resolve_oauth_ssm_path(entry)
        oauth_client_id = entry.get("cognitoClientId") or os.environ.get(
            "A2A_CLIENT_ID", ""
        )
        # Static bearer token path (SSM SecureString written by the UI).
        bearer_ssm_path = (entry.get("bearerToken") or {}).get("ssmPath", "")

        # Capture variables in closure
        _arn = arn
        _region = region
        _entry_name = entry_name
        _is_a2a = is_a2a
        _auth_type = auth_type
        _ssm_path = oauth_ssm_path
        _client_id = oauth_client_id
        _bearer_ssm_path = bearer_ssm_path
        _session_id = runtime_session_id

        @strands_tool(name=tool_name, description=description)
        def _invoke_runtime(prompt: str) -> str:
            """Forward a request to the external agent runtime and return its response.

            Args:
                prompt: The request to send to the agent runtime.
            """
            import boto3
            import json as _json
            from uuid import uuid4

            # Resolve the session id for THIS call rather than using the value
            # captured when the tool was built. A warm agent is reused across
            # conversations, so the build-time id can belong to an earlier chat.
            active_session_id = get_active_runtime_session_id(_session_id)

            try:
                if _is_a2a:
                    # A2A JSON-RPC 2.0 message/send envelope. AgentCore passes
                    # this body through to the A2A container unmodified.
                    payload = _json.dumps({
                        "jsonrpc": "2.0",
                        "id": uuid4().hex,
                        "method": "message/send",
                        "params": {
                            "message": {
                                "role": "user",
                                "parts": [{"kind": "text", "text": prompt}],
                                "messageId": uuid4().hex,
                            }
                        },
                    }).encode("utf-8")
                else:
                    payload = _json.dumps({
                        "prompt": prompt,
                        "routing_mode": "crew",
                    }).encode("utf-8")

                logger.info(
                    "🔗 AGENTCORE_INVOKE: Calling %s at %s (protocol=%s, auth=%s)",
                    _entry_name,
                    _arn[:80],
                    "A2A" if _is_a2a else "crew",
                    _auth_type,
                )

                # OAuth runtimes are fronted by a Cognito JWT authorizer and
                # must be invoked over the HTTPS data-plane endpoint with a
                # bearer token — SigV4 invoke_agent_runtime does not satisfy a
                # JWT authorizer. This mirrors the UI's OAuth invoke path and
                # reuses A2ATokenManager (SSM credentials -> Cognito bearer).
                if _auth_type == "oauth":
                    response_body, err = _invoke_agentcore_oauth(
                        arn=_arn,
                        region=_region,
                        payload=payload,
                        ssm_path=_ssm_path,
                        client_id=_client_id,
                        session_id=active_session_id,
                    )
                    if err:
                        logger.error(
                            "❌ AGENTCORE_INVOKE: OAuth invoke failed for %s",
                            _entry_name,
                        )
                        return f"Error invoking {_entry_name}: {err}"
                elif _auth_type == "bearer":
                    # Static, operator-pasted token sent verbatim. Read fresh
                    # from SSM (no cache) so a re-pasted token is honored, then
                    # POST to the data-plane endpoint with the bearer header.
                    from shared.dynamodb_config_loader import resolve_ssm_parameter

                    token = (
                        resolve_ssm_parameter(_bearer_ssm_path, use_cache=False)
                        if _bearer_ssm_path
                        else None
                    )
                    if not token:
                        logger.error(
                            "❌ AGENTCORE_INVOKE: Bearer token unavailable for %s",
                            _entry_name,
                        )
                        return (
                            f"Error invoking {_entry_name}: bearer token not "
                            "configured or unavailable"
                        )
                    response_body, err = _invoke_agentcore_bearer(
                        arn=_arn,
                        region=_region,
                        payload=payload,
                        token=token,
                        session_id=active_session_id,
                    )
                    if err:
                        logger.error(
                            "❌ AGENTCORE_INVOKE: Bearer invoke failed for %s",
                            _entry_name,
                        )
                        return f"Error invoking {_entry_name}: {err}"
                else:
                    client = boto3.client("bedrock-agentcore", region_name=_region)
                    # Pass the front-end-derived runtime session id so the
                    # external runtime keeps one continuous session per
                    # front-end conversation (SigV4 path also honors it).
                    response = client.invoke_agent_runtime(
                        agentRuntimeArn=_arn,
                        payload=payload,
                        contentType="application/json",
                        accept="application/json",
                        runtimeSessionId=active_session_id,
                    )
                    # AgentCore returns the body under the "response" key
                    # (StreamingBody); fall back to "body" for older shapes.
                    response_body = response.get("response", response.get("body", b""))
                    if hasattr(response_body, "read"):
                        response_body = response_body.read()
                    if isinstance(response_body, bytes):
                        response_body = response_body.decode("utf-8")

                # Parse the response to extract the actual content
                try:
                    parsed = _json.loads(response_body)
                except _json.JSONDecodeError:
                    return response_body

                if _is_a2a:
                    return _extract_a2a_text(parsed, response_body)

                content = parsed.get("response", response_body)
                return str(content)

            except Exception as e:
                safe_msg = _sanitize_error_message(e)
                logger.error(
                    "❌ AGENTCORE_INVOKE: Failed to invoke %s: %s",
                    _entry_name,
                    safe_msg,
                )
                return f"Error invoking {_entry_name}: {safe_msg}"

        tools.append(_invoke_runtime)
        logger.info(
            "✅ AGENTCORE_INVOKE: Created direct invoke tool '%s' for %s → %s",
            tool_name,
            agent_name,
            arn[:80],
        )

    return tools
