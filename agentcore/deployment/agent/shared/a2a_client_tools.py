"""A2A client tool provider construction for Strands agents.

Builds A2AClientToolProvider instances from an agent's
``external_agent_configs`` list, handling OAuth (Cognito user/password and
client-credentials), static bearer, IAM, and no-auth paths.

Error handling wraps every provider creation in try/except so that a
single misconfigured external agent never prevents the remaining agents
from being registered.  A timeout is applied to all outbound HTTP requests
(see ``A2A_REQUEST_TIMEOUT_SECONDS``).
"""

import hashlib
import logging
import os
import re
from typing import Any, Dict, List, Optional
from uuid import uuid4

from strands_tools.a2a_client import A2AClientToolProvider
from shared.a2a_auth import A2ATokenManager
from shared.agent_invocation_plan import (
    ENDPOINT_URL,
    plan_for_entry,
    signing_service_for,
    sigv4_headers,
)

logger = logging.getLogger(__name__)

# Timeout in seconds for all outbound A2A HTTP requests (Requirement 9.1).
#
# The original 120s was tuned for A2A peers that answer in one model turn. It is
# too short for a CrewAI crew runtime: the AAMP buyer agent's deal-booking flow
# runs a multi-agent crew that regularly exceeds two minutes, so the invoke tool
# aborted a healthy, still-running call and reported it to the model as
#     Error invoking AAMPBuyerAgent: ... Read timed out. (read timeout=120)
# (observed end to end against the deployed runtimes). The default now matches
# AgentCore Runtime's own default maximum invocation duration of 900s, so this
# client stops being the first thing to give up. Override with the
# A2A_REQUEST_TIMEOUT_SECONDS environment variable.
def _a2a_request_timeout_seconds(default: int = 900) -> int:
    """Read the outbound A2A request timeout from the environment.

    Falls back to ``default`` when unset, non-numeric, or non-positive, so a bad
    value degrades to a working timeout rather than disabling the bound.
    """
    raw = os.environ.get("A2A_REQUEST_TIMEOUT_SECONDS", "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


A2A_REQUEST_TIMEOUT_SECONDS = _a2a_request_timeout_seconds()

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
    - oauth: retrieves a bearer token via A2ATokenManager (Cognito
      USER_PASSWORD_AUTH).
    - oauth_m2m: retrieves a bearer token via A2ATokenManager (OAuth 2.0
      client-credentials grant against the peer's own token endpoint).
    - bearer: sends an operator-supplied static token verbatim.
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

    # Decide per entry, not globally. A2AClientToolProvider performs A2A
    # discovery over HTTP, so it only works for an entry that both speaks A2A and
    # is addressed by a URL. Everything else — any HTTP-protocol entry, and every
    # ARN-addressed entry regardless of protocol — is invoked by a direct tool.
    region = os.environ.get("AWS_REGION", "us-east-1")
    invoke_entries: List[dict] = []

    for entry in external_configs:
        if not entry.get("enabled", True):
            continue

        entry_name = entry.get("name", "unknown")
        plan = plan_for_entry(entry, region)

        if plan.problem:
            logger.warning(
                "⚠️ A2A_TOOLS: Skipping entry '%s' for %s — %s",
                entry_name,
                agent_name,
                plan.problem,
            )
            continue

        if not (plan.is_a2a and plan.endpoint_kind == ENDPOINT_URL):
            invoke_entries.append(entry)
            continue

        arn = plan.endpoint
        auth_type = plan.auth_type

        try:
            # Base httpx client args with 120-second timeout (Req 9.1)
            httpx_args: Dict[str, Any] = {
                "timeout": A2A_REQUEST_TIMEOUT_SECONDS,
            }

            if auth_type in ("oauth", "oauth_m2m"):
                # Both OAuth modes resolve to a bearer token; they differ only in
                # the exchange, which A2ATokenManager picks from the stored
                # document's grant_type. 'oauth_m2m' records its reference under
                # oauthClientCredentials, 'oauth' under oauthCredentials.
                oauth_creds = (
                    entry.get("oauthClientCredentials")
                    or entry.get("oauthCredentials")
                    or {}
                )
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
                            "(agent=%s, auth=%s)",
                            entry_name,
                            agent_name,
                            auth_type,
                        )
                        continue

                    httpx_args["headers"] = build_bearer_auth_header(token)
                else:
                    logger.warning(
                        "⚠️ A2A_TOOLS: %s configured but no credentials for '%s'",
                        auth_type,
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

    # Entries that cannot go through A2A HTTP discovery get a direct invoke tool.
    # Previously a single ARN-addressed entry forced EVERY entry down this path,
    # which silently dropped working URL-addressed A2A peers.
    direct_invoke_tools: List = []
    if invoke_entries:
        logger.info(
            "🔗 A2A_TOOLS: %d entr%s invoked directly (ARN endpoint or HTTP protocol)",
            len(invoke_entries),
            "y" if len(invoke_entries) == 1 else "ies",
        )
        direct_invoke_tools = _build_agentcore_invoke_tools(
            agent_name, invoke_entries, session_id=session_id
        )

    if not providers:
        return direct_invoke_tools

    # For A2A-over-URL entries, extract tools from providers.
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

    return all_tools + direct_invoke_tools


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

    # Parts within one artifact are streaming text deltas, not paragraphs, so
    # _parts_text concatenates them with no separator. A newline between deltas
    # lands mid-word wherever the tokenizer split, and one next to a `**` stops
    # CommonMark reading it as an emphasis delimiter. Only separate artifacts
    # are distinct blocks and get the newline.
    texts: List[str] = []

    # Task response: result.artifacts[].parts[].text
    for artifact in result.get("artifacts", []) or []:
        text = _parts_text(artifact.get("parts"))
        if text:
            texts.append(text)

    # Message response: result.parts[].text
    if not texts:
        text = _parts_text(result.get("parts"))
        if text:
            texts.append(text)

    return "\n".join(texts) if texts else raw


def _parts_text(parts) -> str:
    """Join the text of A2A message/artifact ``parts`` (kind == 'text')."""
    out: List[str] = []
    for part in parts or []:
        if isinstance(part, dict):
            if part.get("kind") == "text" and part.get("text"):
                out.append(part["text"])
            elif isinstance(part.get("text"), str) and part["text"]:
                out.append(part["text"])
    return "".join(out)


def _consume_a2a_sse(resp, on_status=None) -> str:
    """Consume an A2A ``message/stream`` SSE response and return the final text.

    Iterates the ``text/event-stream`` body (one ``data: <json-rpc>`` object per
    event). Two kinds of events matter:

    - ``status-update`` — interim progress (the agent's working narration). Each
      one's text is passed to ``on_status`` (if given) so callers can surface
      progress; it is NOT part of the returned answer.
    - ``artifact-update`` / final ``message`` — the actual answer. Artifact text
      is accumulated across chunks and returned.

    Streaming keeps the connection alive with incremental events, so a long
    (~1-2 min) response no longer trips the data-plane single-response deadline
    that makes a synchronous ``message/send`` return 424.

    Returns the assembled final text (falls back to the last status text, then
    to the raw body, so a caller always gets something non-empty).
    """
    import json as _json

    artifact_text: List[str] = []
    last_status_text = ""
    raw_lines: List[str] = []

    # Works for both requests.Response and botocore StreamingBody: iter_lines()
    # yields bytes on both; decode defensively.
    for line in resp.iter_lines():
        if not line:
            continue
        if isinstance(line, bytes):
            line = line.decode("utf-8", "replace")
        raw_lines.append(line)
        if not line.startswith("data:"):
            continue
        data = line[len("data:"):].strip()
        if not data or data == "[DONE]":
            continue
        try:
            evt = _json.loads(data)
        except _json.JSONDecodeError:
            continue

        if isinstance(evt.get("error"), dict):
            err = evt["error"]
            return f"A2A error {err.get('code', 'unknown')}: {err.get('message', 'failed')}"

        result = evt.get("result")
        if not isinstance(result, dict):
            continue

        kind = result.get("kind")
        if kind == "artifact-update":
            txt = _parts_text((result.get("artifact") or {}).get("parts"))
            if txt:
                artifact_text.append(txt)
        elif kind == "status-update":
            status = result.get("status") or {}
            txt = _parts_text((status.get("message") or {}).get("parts"))
            if txt:
                last_status_text = txt
                if on_status is not None:
                    try:
                        on_status(txt)
                    except Exception:  # noqa: BLE001 - never let status relay break the invoke
                        pass
        elif kind in ("message", "task"):
            for artifact in result.get("artifacts", []) or []:
                txt = _parts_text(artifact.get("parts"))
                if txt:
                    artifact_text.append(txt)
            if not artifact_text:
                txt = _parts_text(result.get("parts"))
                if txt:
                    artifact_text.append(txt)

    if artifact_text:
        return "".join(artifact_text)
    if last_status_text:
        return last_status_text
    return "\n".join(raw_lines)


def _resolve_oauth_ssm_path(entry: dict) -> str:
    """Return the SSM path holding the target's inbound OAuth credentials.

    Prefers an explicit ``ssmPath`` set on the entry — under
    ``oauthClientCredentials`` for the client-credentials mode, or
    ``oauthCredentials`` for the Cognito username/password mode (the UI writes
    one of these when an operator configures an agent's Inbound Authentication
    settings). Falls back to the repo's path convention
    ``/{STACK_PREFIX}/a2a-inbound-tokens/{UNIQUE_ID}/{name}`` when both env
    vars are present. Returns "" when it cannot be resolved — the caller then
    surfaces an explicit "not configured" error rather than guessing.
    """
    for field in ("oauthClientCredentials", "oauthCredentials"):
        ssm_path = (entry.get(field) or {}).get("ssmPath") or ""
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


# AgentCore returns 424 (Failed Dependency) or 503 while a target runtime is
# cold-starting / not yet READY. The first call after an idle period hits this,
# and a bare "HTTP 424" would otherwise surface to the user as a failure. Retry
# the POST a few times with a short backoff — the status arrives before any
# stream body, so re-POSTing never double-delivers a response.
_COLDSTART_STATUSES = (424, 503)
_COLDSTART_MAX_ATTEMPTS = 4
_COLDSTART_BACKOFF_SECONDS = 8


def _post_with_coldstart_retry(endpoint: str, payload: bytes, headers: dict, stream: bool):
    """POST to an AgentCore data-plane endpoint, retrying cold-start 424/503.

    Returns ``(response, error)`` with exactly one non-None. Waits and re-POSTs
    on the retryable statuses; returns the response once it is not a cold-start
    status or attempts are exhausted (the caller then surfaces the error).
    """
    import time as _time

    import requests

    for attempt in range(_COLDSTART_MAX_ATTEMPTS):
        try:
            resp = requests.post(
                endpoint,
                data=payload,
                headers=headers,
                timeout=A2A_REQUEST_TIMEOUT_SECONDS,
                stream=stream,
            )
        except Exception as e:  # noqa: BLE001 - sanitized transport error
            return None, _sanitize_error_message(e)

        if resp.status_code in _COLDSTART_STATUSES and attempt < _COLDSTART_MAX_ATTEMPTS - 1:
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass
            logger.warning(
                "🔁 AGENTCORE_INVOKE: target not ready (HTTP %s); retry %d/%d in %ds",
                resp.status_code,
                attempt + 1,
                _COLDSTART_MAX_ATTEMPTS - 1,
                _COLDSTART_BACKOFF_SECONDS,
            )
            _time.sleep(_COLDSTART_BACKOFF_SECONDS)
            continue
        return resp, None


def _invoke_agentcore_oauth(
    arn: str,
    region: str,
    payload: bytes,
    ssm_path: str,
    client_id: str = "",
    session_id: str = "",
    request_url: str = "",
    stream: bool = False,
    on_status=None,
):
    """Invoke an OAuth-protected AgentCore runtime over HTTPS with a bearer.

    Mirrors the UI's OAuth invoke path: acquire a bearer from the stored
    credentials (via A2ATokenManager, which runs either the Cognito
    USER_PASSWORD_AUTH or the OAuth client-credentials exchange depending on the
    stored document) and POST to the runtime's data-plane invocations endpoint.
    Returns ``(response_text, error)`` where exactly one is non-None. The bearer
    never appears in the returned error.

    ``session_id`` is the AgentCore runtimeSessionId to use — pass the derived
    front-end session id so the external runtime keeps a continuous session.

    ``request_url`` overrides the derived AgentCore data-plane URL, which is how
    a URL-addressed agent is reached.
    """
    import requests
    from urllib.parse import quote

    if not ssm_path:
        return None, (
            "OAuth credentials not configured for this agent. Store either the "
            "inbound Cognito credentials (Auth Client ID, Username, Password) or "
            "the OAuth M2M credentials (Client ID, Client Secret, Token URL) via "
            "the agent's Inbound Authentication settings."
        )

    token, err = _get_token_manager().get_bearer_token(ssm_path, client_id=client_id)
    if err or not token:
        return None, (err or "Failed to acquire OAuth bearer token")

    endpoint = request_url or (
        f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/"
        f"{quote(arn, safe='')}/invocations?qualifier=DEFAULT"
    )
    # Use the caller-provided (front-end-derived) runtime session id so the
    # external runtime maintains a continuous session across turns. Only fall
    # back to a random id when none was supplied.
    runtime_session_id = session_id or f"a2a-{uuid4().hex}{uuid4().hex}"
    # For A2A `message/stream`, ask for SSE and read incrementally: a long
    # (~1-2 min) response streams keep-alive events instead of tripping the
    # data-plane single-response deadline that returns 424 for `message/send`.
    accept = "text/event-stream" if stream else "application/json"
    headers = {
        "Content-Type": "application/json",
        "Accept": accept,
        "Authorization": f"Bearer {token}",
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": runtime_session_id,
    }
    resp, err = _post_with_coldstart_retry(endpoint, payload, headers, stream)
    if err:
        return None, err
    if resp.status_code >= 400:
        # Body may echo request detail; keep it short and free of the bearer.
        return None, f"OAuth invocation failed (HTTP {resp.status_code})"
    if stream:
        return _consume_a2a_sse(resp, on_status=on_status), None
    return resp.text, None


def _invoke_agentcore_bearer(
    arn: str,
    region: str,
    payload: bytes,
    token: str,
    session_id: str = "",
    request_url: str = "",
    stream: bool = False,
    on_status=None,
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

    endpoint = request_url or (
        f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/"
        f"{quote(arn, safe='')}/invocations?qualifier=DEFAULT"
    )
    runtime_session_id = session_id or f"a2a-{uuid4().hex}{uuid4().hex}"
    accept = "text/event-stream" if stream else "application/json"
    headers = {
        "Content-Type": "application/json",
        "Accept": accept,
        **build_bearer_auth_header(token),
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": runtime_session_id,
    }
    resp, err = _post_with_coldstart_retry(endpoint, payload, headers, stream)
    if err:
        return None, err
    if resp.status_code >= 400:
        # Keep the message short and free of the token value.
        return None, f"Bearer invocation failed (HTTP {resp.status_code})"
    if stream:
        return _consume_a2a_sse(resp, on_status=on_status), None
    return resp.text, None


def _invoke_https_endpoint(
    plan,
    entry: dict,
    payload: bytes,
    session_id: str = "",
):
    """POST to a URL-addressed agent using SigV4 or no credentials.

    Covers the combinations the AgentCore SDK cannot: a URL endpoint with either
    ``iam`` (signed here, since ``invoke_agent_runtime`` cannot address a URL) or
    ``none``. Returns ``(response_text, error)`` where exactly one is non-None.
    """
    import requests

    headers = {
        "Content-Type": "application/json",
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id
        or f"a2a-{uuid4().hex}{uuid4().hex}",
    }

    if plan.auth_type == "iam":
        try:
            headers = sigv4_headers(
                url=plan.request_url,
                body=payload,
                headers=headers,
                service=signing_service_for(plan, entry),
                region=os.environ.get("AWS_REGION", "us-east-1"),
            )
        except Exception as e:  # noqa: BLE001 - surface a sanitized signing error
            return None, f"could not SigV4-sign the request: {_sanitize_error_message(e)}"

    try:
        resp = requests.post(
            plan.request_url,
            data=payload,
            headers=headers,
            timeout=A2A_REQUEST_TIMEOUT_SECONDS,
        )
    except Exception as e:  # noqa: BLE001 - surface a sanitized transport error
        return None, _sanitize_error_message(e)

    if resp.status_code >= 400:
        return None, f"invocation failed (HTTP {resp.status_code})"
    return resp.text, None


def wrap_as_agent_message(agent_name: str, text: str) -> str:
    """Wrap an external agent's reply in the tag the UI reads as an agent turn.

    `invoke_specialist` in handler.py returns
    ``<agent-message agent='NAME'>...</agent-message>``, and the front end matches
    exactly that to emit a collaborator-response attributed to NAME. External
    agents returned bare text, so their replies rendered as anonymous tool output
    instead of as the agent speaking.

    Already-wrapped text is returned unchanged, so a remote agent that emits the
    tag itself does not end up double-wrapped.
    """
    body = "" if text is None else str(text)
    if "<agent-message" in body:
        return body
    return f"<agent-message agent='{agent_name}'>{body}</agent-message>"


# Marker prefixed to interim progress lines so handler.py can tell them apart
# from the final answer. It carries the agent name so the UI can attribute the
# status to the sub-agent, and is stripped before the text reaches the UI (no
# marker or emoji in user-visible output). handler.py must match this format.
_PROGRESS_PREFIX = "[[AGENT_STATUS|"
_PROGRESS_SUFFIX = "]] "


def session_checkpoint(
    stage: str,
    session_id: str,
    sender: str,
    recipient: str,
    message: str = "",
    detail: str = "",
    elapsed_s: Optional[float] = None,
) -> None:
    """Log one hop of a conversation on a single greppable line.

    Same line format as handler.py's ``_session_checkpoint`` so that filtering
    CloudWatch on ``SESSION_TRACE`` — or on a session id — reconstructs a whole
    conversation across every runtime it touched, in order.
    """
    try:
        from datetime import datetime as _datetime

        text = " ".join(str(message or "").split())
        if len(text) > 300:
            text = text[:300] + "..."
        parts = [
            f"🧭 SESSION_TRACE {stage}",
            f"session_id={session_id or 'MISSING'}",
            f"sender={sender or '-'}",
            f"recipient={recipient or '-'}",
            f"at={_datetime.now().isoformat()}",
        ]
        if elapsed_s is not None:
            parts.append(f"elapsed_s={elapsed_s:.1f}")
        if detail:
            parts.append(detail)
        if text:
            parts.append(f'message="{text}"')
        line = " | ".join(parts)
        print(line, flush=True)
        logger.info(line)
    except Exception as log_err:  # noqa: BLE001
        print(f"SESSION_TRACE log failure: {log_err}", flush=True)


def _progress_line(entry_name: str, message: str) -> str:
    """Format one interim progress line for handler.py to parse and strip."""
    return f"{_PROGRESS_PREFIX}{entry_name}{_PROGRESS_SUFFIX}{message}"


# How often to check the progress table for new peer milestones while an A2A call
# is in flight, and the cap on how many interim lines one call may produce
# (milestones + heartbeats together) so a pathological peer cannot flood the chat.
_PROGRESS_POLL_SECONDS = 5
_MAX_PROGRESS_LINES = 40
# Stop reporting after this long; the call itself keeps running to its own timeout.
_MAX_PROGRESS_WINDOW_SECONDS = 600


class _ProgressPoller:
    """Read a peer's real milestones for one A2A context from DynamoDB.

    The peer (e.g. the AAMP buyer) appends a row per completed step keyed by the
    context id we send in the A2A envelope. We poll forward from the last sort key
    we have seen, so each milestone is surfaced exactly once and in order.

    Disabled (and silent) when no progress table is configured, in which case the
    caller falls back to elapsed-time heartbeats.
    """

    def __init__(self, context_id: str) -> None:
        self._context_id = (context_id or "").strip()
        self._table_name = (os.environ.get("AAMP_PROGRESS_TABLE") or "").strip()
        self._last_sk = "0"
        self._table = None
        self._broken = False
        # The context id is now the conversation's session id, so it is stable
        # across turns and this partition already holds earlier turns' rows (TTL
        # is an hour). Start the cursor at whatever is already there so turn 2
        # reports only its own milestones instead of replaying turn 1's.
        self._seek_to_end()

    def _seek_to_end(self) -> None:
        """Advance the cursor past any rows already in this partition."""
        if not self.enabled:
            return
        try:
            from boto3.dynamodb.conditions import Key

            resp = self._get_table().query(
                KeyConditionExpression=Key("pk").eq(f"PROGRESS#{self._context_id}"),
                ConsistentRead=True,
                ScanIndexForward=False,
                Limit=1,
            )
            items = resp.get("Items") or []
            if items:
                self._last_sk = str(items[0].get("sk") or self._last_sk)
                logger.info(
                    "A2A_PROGRESS: resuming context %s after sk=%s",
                    self._context_id,
                    self._last_sk,
                )
        except Exception as e:  # noqa: BLE001
            # Same degradation as fetch(): no progress reporting, heartbeats only.
            logger.warning(
                "A2A_PROGRESS: cursor seek failed (%s: %s)", type(e).__name__, e
            )
            self._broken = True

    @property
    def enabled(self) -> bool:
        return bool(self._context_id and self._table_name and not self._broken)

    def _get_table(self):
        if self._table is None:
            import boto3

            region = os.environ.get("AWS_REGION", "us-east-1")
            self._table = boto3.resource("dynamodb", region_name=region).Table(
                self._table_name
            )
        return self._table

    def fetch(self) -> List[str]:
        """Return milestone messages recorded since the last call. Never raises."""
        if not self.enabled:
            return []
        try:
            from boto3.dynamodb.conditions import Key

            resp = self._get_table().query(
                KeyConditionExpression=(
                    Key("pk").eq(f"PROGRESS#{self._context_id}")
                    & Key("sk").gt(self._last_sk)
                ),
                ConsistentRead=True,
                Limit=_MAX_PROGRESS_LINES,
            )
        except Exception as e:  # noqa: BLE001
            # One failure (missing table, no permission) disables polling for the
            # rest of the call so we degrade to heartbeats instead of retrying.
            logger.warning(
                "A2A_PROGRESS: polling disabled (%s: %s)", type(e).__name__, e
            )
            self._broken = True
            return []

        messages: List[str] = []
        for item in resp.get("Items", []) or []:
            sk = str(item.get("sk") or "")
            if sk > self._last_sk:
                self._last_sk = sk
            text = str(item.get("message") or "").strip()
            if text:
                messages.append(text)
        return messages


# Fallback narration for when the peer publishes no milestones: how long to go
# without news before saying the call is still running, and the cap on how many
# such reports to send.
#
# We deliberately do NOT relay the peer's A2A status text. A Strands A2AServer
# streams the agent's whole answer as status-update deltas, so echoing that text
# reproduces the entire final answer as a series of partial "progress" bubbles
# (every Markdown table row and rule arrives as its own short line) and then the
# UI shows the same content again as the final turn. Real step-by-step progress
# comes from the progress table instead (see _ProgressPoller).
_HEARTBEAT_SECONDS = 20
_MAX_HEARTBEATS = 9

# Rotated so a long wait does not read as a stuck loop. Each line says who we are
# waiting on and what we expect back; none of them claim to know which internal
# step the peer is on, because in this path the peer has not reported one.
_WAITING_LINES = (
    "{name} is still working on it. Requests like this normally take one to two"
    " minutes, so this is expected.",
    "Still waiting on {name} to finish and hand back its response. Nothing has"
    " failed — it has not replied yet.",
    "{name} has not responded yet. Holding the connection open and will show its"
    " answer here as soon as it arrives.",
)


def _make_runtime_tool(
    _do_invoke, _is_a2a, _entry_name, tool_name, description, _session_id=""
):
    """Create one decorated invoke tool bound to a single external-agent entry.

    Defining the tool inside this factory (rather than directly in the build
    loop) freezes ``_do_invoke``/``_is_a2a``/``_entry_name`` as factory
    parameters, so each entry's tool invokes ITS OWN runtime. The decorated
    function keeps the public signature ``(prompt: str)`` expected by the model.
    """
    from strands import tool as strands_tool

    @strands_tool(name=tool_name, description=description)
    async def _invoke_runtime(prompt: str):
        """Forward a request to the external agent runtime and return its response.

        For A2A agents this streams real progress as tool-stream events while the
        call runs (~1-2 min), then returns the agent's final answer as the tool
        result. Progress comes from milestones the peer records against the
        context id we send, polled from the progress table; when the peer records
        nothing we fall back to saying the call is still in flight and for how
        long. The peer's own A2A status text is never relayed: it is the answer
        being streamed, so echoing it would show the answer twice. Non-A2A
        runtimes just return the final answer.

        Args:
            prompt: The request to send to the agent runtime.
        """
        import asyncio
        import threading
        import time

        loop = asyncio.get_event_loop()

        if not _is_a2a:
            # No interim status for crew/http runtimes: run the blocking
            # invoke off the event loop and return the result.
            yield await loop.run_in_executor(None, lambda: _do_invoke(prompt))
            return

        # A2A: run the blocking streamed invoke in a worker thread and report
        # progress while it runs. The final answer is the last yield (Strands
        # treats it as the tool result).
        #
        # We mint the A2A context id here instead of letting the server generate
        # one: it is the correlation key the peer writes its milestones under, and
        # a server-generated id only reaches us mid-stream, too late to poll with.
        #
        # It is the conversation's session id, not a fresh uuid. The peer keys its
        # conversation state on this value (strands' StrandsA2AExecutor caches one
        # Agent per A2A context_id), so a per-call id gave the peer a brand-new
        # agent with no history on every turn. Reusing the session id means the
        # peer's continuity matches the front-end conversation. Falls back to a
        # random id when there is no session — continuity is impossible then, and
        # colliding unrelated conversations would be worse.
        context_id = get_active_runtime_session_id(_session_id) or uuid4().hex
        poller = _ProgressPoller(context_id)

        holder: dict = {}
        done = threading.Event()

        def _run() -> None:
            try:
                holder["result"] = _do_invoke(prompt, context_id=context_id)
            except Exception as e:  # noqa: BLE001
                holder["result"] = wrap_as_agent_message(
                    _entry_name,
                    f"Error invoking {_entry_name}: {_sanitize_error_message(e)}",
                )
            finally:
                done.set()

        threading.Thread(target=_run, daemon=True).start()

        started = time.monotonic()
        yield _progress_line(
            _entry_name,
            f"Passing your request to {_entry_name} and waiting for its reply."
            " This usually takes a minute or two.",
        )

        # Poll on a short interval so real milestones appear promptly, and only
        # fall back to a heartbeat after _HEARTBEAT_SECONDS of actual silence.
        wait_step = _PROGRESS_POLL_SECONDS if poller.enabled else _HEARTBEAT_SECONDS
        beats = 0
        lines = 0
        last_news = started
        while lines < _MAX_PROGRESS_LINES:
            if time.monotonic() - started >= _MAX_PROGRESS_WINDOW_SECONDS:
                break
            finished = await loop.run_in_executor(
                None, lambda: done.wait(wait_step)
            )
            if finished:
                break

            milestones = (
                await loop.run_in_executor(None, poller.fetch)
                if poller.enabled
                else []
            )
            if milestones:
                for milestone in milestones:
                    lines += 1
                    last_news = time.monotonic()
                    yield _progress_line(_entry_name, milestone)
                continue

            if (
                time.monotonic() - last_news >= _HEARTBEAT_SECONDS
                and beats < _MAX_HEARTBEATS
            ):
                elapsed = int(time.monotonic() - started)
                message = _WAITING_LINES[beats % len(_WAITING_LINES)].format(
                    name=_entry_name
                )
                beats += 1
                lines += 1
                last_news = time.monotonic()
                yield _progress_line(
                    _entry_name, f"{message} (Waiting {elapsed}s so far.)"
                )
            elif not poller.enabled and beats >= _MAX_HEARTBEATS:
                # Nothing left to report and nothing to poll: wait quietly.
                break

        # Make sure the worker has published its result before returning it.
        await loop.run_in_executor(None, done.wait)
        yield holder.get("result", wrap_as_agent_message(_entry_name, ""))

    return _invoke_runtime


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

        # The request envelope follows the entry's protocol: `a2a` speaks
        # JSON-RPC 2.0 and rejects the legacy {"prompt", "routing_mode"}
        # envelope that CrewAI crew runtimes (AAMP seller agents) expect. The
        # transport is a separate question, answered by the plan below.
        plan = plan_for_entry(entry, region)
        if plan.problem:
            logger.warning(
                "⚠️ AGENTCORE_INVOKE: Skipping '%s' for %s — %s",
                entry_name,
                agent_name,
                plan.problem,
            )
            continue

        is_a2a = plan.is_a2a
        auth_type = plan.auth_type
        oauth_ssm_path = _resolve_oauth_ssm_path(entry)
        oauth_client_id = entry.get("cognitoClientId") or os.environ.get(
            "A2A_CLIENT_ID", ""
        )
        # Static bearer token path (SSM SecureString written by the UI).
        bearer_ssm_path = (entry.get("bearerToken") or {}).get("ssmPath", "")

        # Capture variables in closure
        _arn = plan.endpoint
        _plan = plan
        _entry = entry
        _region = region
        _entry_name = entry_name
        _is_a2a = is_a2a
        _auth_type = auth_type
        _ssm_path = oauth_ssm_path
        _client_id = oauth_client_id
        _bearer_ssm_path = bearer_ssm_path
        _session_id = runtime_session_id

        def _do_invoke(
            prompt: str,
            on_status=None,
            context_id: str = "",
            # Bind every per-entry value as a default so each tool closes over
            # ITS OWN entry. Without this, these names resolve from the enclosing
            # loop scope at call time and all hold the LAST entry's values — so
            # every external tool (buyer AND seller) would invoke the last-wired
            # runtime (the buyer), which is why seller-tool calls were answered
            # by the buyer.
            _arn=_arn,
            _plan=_plan,
            _entry=_entry,
            _region=_region,
            _entry_name=_entry_name,
            _is_a2a=_is_a2a,
            _auth_type=_auth_type,
            _ssm_path=_ssm_path,
            _client_id=_client_id,
            _bearer_ssm_path=_bearer_ssm_path,
            _session_id=_session_id,
            _caller_name=agent_name,
        ) -> str:
            """Forward a request to the external agent runtime; return final text.

            ``on_status``, when given, is called with each interim A2A
            status-update text as it streams (progress narration), so a caller
            can surface live progress. It never affects the returned answer.

            ``context_id``, when given, is sent as the A2A message ``contextId``.
            The peer uses it as its task context and as the key it records
            progress milestones under, so the caller can poll them while waiting.

            Args:
                prompt: The request to send to the agent runtime.
            """
            import time as _time

            import boto3
            import json as _json
            from uuid import uuid4

            # Resolve the session id for THIS call rather than using the value
            # captured when the tool was built. A warm agent is reused across
            # conversations, so the build-time id can belong to an earlier chat.
            active_session_id = get_active_runtime_session_id(_session_id)

            try:
                if _is_a2a:
                    # A2A JSON-RPC 2.0 message/stream envelope. Streaming keeps
                    # the connection alive with incremental SSE events, so a long
                    # (~1-2 min) plan does not trip the data-plane single-response
                    # deadline that returns 424 for message/send. AgentCore passes
                    # this body through to the A2A container unmodified.
                    a2a_message = {
                        "role": "user",
                        "parts": [{"kind": "text", "text": prompt}],
                        "messageId": uuid4().hex,
                    }
                    # Send our own contextId so the peer's task context matches the
                    # key we poll for progress milestones. Without it the server
                    # mints one and only reveals it mid-stream, too late to use.
                    if context_id:
                        a2a_message["contextId"] = context_id
                    payload = _json.dumps({
                        "jsonrpc": "2.0",
                        "id": uuid4().hex,
                        "method": "message/stream",
                        "params": {"message": a2a_message},
                    }).encode("utf-8")
                else:
                    # handler.py reads session_id from the body and nowhere else,
                    # so a crew/HTTP peer that is itself a handler.py runtime was
                    # being called without any conversation identity.
                    payload = _json.dumps({
                        "prompt": prompt,
                        "routing_mode": "crew",
                        "session_id": active_session_id,
                        "agent_name": _entry_name,
                    }).encode("utf-8")

                logger.info(
                    "🔗 AGENTCORE_INVOKE: Calling %s at %s "
                    "(protocol=%s, endpoint=%s, transport=%s, auth=%s)",
                    _entry_name,
                    _arn[:80],
                    "A2A" if _is_a2a else "crew",
                    _plan.endpoint_kind,
                    _plan.transport,
                    _auth_type,
                )
                session_checkpoint(
                    "SEND",
                    session_id=active_session_id,
                    sender=_caller_name,
                    recipient=_entry_name,
                    message=prompt,
                    detail=(
                        f"protocol={'a2a' if _is_a2a else 'crew'} auth={_auth_type} "
                        f"transport={_plan.transport} "
                        f"contextId={context_id if _is_a2a else '-'}"
                    ),
                )
                _hop_started = _time.monotonic()

                # OAuth runtimes are fronted by a JWT authorizer and must be
                # invoked over the HTTPS data-plane endpoint with a bearer token
                # — SigV4 invoke_agent_runtime does not satisfy a JWT
                # authorizer. This mirrors the UI's OAuth invoke path and reuses
                # A2ATokenManager, which runs either the Cognito
                # USER_PASSWORD_AUTH or the client-credentials exchange
                # depending on the stored document.
                if _auth_type in ("oauth", "oauth_m2m"):
                    response_body, err = _invoke_agentcore_oauth(
                        arn=_arn,
                        region=_region,
                        payload=payload,
                        ssm_path=_ssm_path,
                        client_id=_client_id,
                        session_id=active_session_id,
                        request_url=_plan.request_url,
                        stream=_is_a2a,
                        on_status=on_status,
                    )
                    if err:
                        logger.error(
                            "❌ AGENTCORE_INVOKE: OAuth invoke failed for %s",
                            _entry_name,
                        )
                        return wrap_as_agent_message(
                            _entry_name, f"Error invoking {_entry_name}: {err}"
                        )
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
                        return wrap_as_agent_message(
                            _entry_name,
                            f"Error invoking {_entry_name}: bearer token not "
                            "configured or unavailable",
                        )
                    response_body, err = _invoke_agentcore_bearer(
                        arn=_arn,
                        region=_region,
                        payload=payload,
                        token=token,
                        session_id=active_session_id,
                        request_url=_plan.request_url,
                        stream=_is_a2a,
                        on_status=on_status,
                    )
                    if err:
                        logger.error(
                            "❌ AGENTCORE_INVOKE: Bearer invoke failed for %s",
                            _entry_name,
                        )
                        return wrap_as_agent_message(
                            _entry_name, f"Error invoking {_entry_name}: {err}"
                        )
                elif not _plan.uses_sdk:
                    # URL endpoint with `iam` or `none`: the SDK cannot address a
                    # URL, so POST directly (signing here when auth is iam).
                    response_body, err = _invoke_https_endpoint(
                        plan=_plan,
                        entry=_entry,
                        payload=payload,
                        session_id=active_session_id,
                    )
                    if err:
                        logger.error(
                            "❌ AGENTCORE_INVOKE: Direct invoke failed for %s",
                            _entry_name,
                        )
                        return wrap_as_agent_message(
                            _entry_name, f"Error invoking {_entry_name}: {err}"
                        )
                else:
                    # botocore's default read timeout is 60s — shorter still than
                    # the OAuth/bearer paths' bound and far shorter than a crew
                    # runtime's turnaround, so give the SigV4 path the same
                    # A2A_REQUEST_TIMEOUT_SECONDS budget. Retries stay off: a
                    # retried invoke would re-run the remote agent's work.
                    from botocore.config import Config as _BotoConfig

                    client = boto3.client(
                        "bedrock-agentcore",
                        region_name=_region,
                        config=_BotoConfig(
                            connect_timeout=30,
                            read_timeout=A2A_REQUEST_TIMEOUT_SECONDS,
                            retries={"max_attempts": 0},
                        ),
                    )
                    # Pass the front-end-derived runtime session id so the
                    # external runtime keeps one continuous session per
                    # front-end conversation (SigV4 path also honors it). For
                    # A2A, request SSE so a long response streams incrementally
                    # instead of tripping the single-response 424 deadline.
                    response = client.invoke_agent_runtime(
                        agentRuntimeArn=_arn,
                        payload=payload,
                        contentType="application/json",
                        accept="text/event-stream" if _is_a2a else "application/json",
                        runtimeSessionId=active_session_id,
                    )
                    # AgentCore returns the body under the "response" key
                    # (StreamingBody); fall back to "body" for older shapes.
                    response_stream = response.get("response", response.get("body", b""))
                    if _is_a2a and hasattr(response_stream, "iter_lines"):
                        # Consume the SSE stream; final assembled text is returned
                        # already-extracted, so the A2A parse below is a no-op.
                        response_body = _consume_a2a_sse(response_stream, on_status=on_status)
                    else:
                        response_body = response_stream
                        if hasattr(response_body, "read"):
                            response_body = response_body.read()
                        if isinstance(response_body, bytes):
                            response_body = response_body.decode("utf-8")

                session_checkpoint(
                    "REPLY",
                    session_id=active_session_id,
                    sender=_entry_name,
                    recipient=_caller_name,
                    message=str(response_body),
                    detail=(
                        f"status=ok chars={len(str(response_body or ''))} "
                        f"contextId={context_id if _is_a2a else '-'}"
                    ),
                    elapsed_s=_time.monotonic() - _hop_started,
                )

                # Parse the response to extract the actual content
                try:
                    parsed = _json.loads(response_body)
                except _json.JSONDecodeError:
                    # The streaming paths (message/stream over OAuth/bearer/SDK)
                    # pre-consume the SSE and hand back the already-extracted
                    # final text, which is not a JSON-RPC envelope — so parsing
                    # fails here on the normal, successful streamed response.
                    return wrap_as_agent_message(_entry_name, response_body)

                if _is_a2a:
                    return wrap_as_agent_message(
                        _entry_name, _extract_a2a_text(parsed, response_body)
                    )

                content = parsed.get("response", response_body)
                return wrap_as_agent_message(_entry_name, str(content))

            except Exception as e:
                safe_msg = _sanitize_error_message(e)
                logger.error(
                    "❌ AGENTCORE_INVOKE: Failed to invoke %s: %s",
                    _entry_name,
                    safe_msg,
                )
                # Wrapped like the success path and like invoke_specialist's error
                # return, so the failure is attributed to the agent that failed
                # rather than surfacing as unattributed tool output.
                return wrap_as_agent_message(
                    _entry_name, f"Error invoking {_entry_name}: {safe_msg}"
                )

        # Build the decorated tool in a factory so it closes over THIS entry's
        # values (see _make_runtime_tool) rather than the loop's last-iteration
        # values.
        tools.append(
            _make_runtime_tool(
                _do_invoke=_do_invoke,
                _is_a2a=_is_a2a,
                _entry_name=_entry_name,
                tool_name=tool_name,
                description=description,
                _session_id=_session_id,
            )
        )
        logger.info(
            "✅ AGENTCORE_INVOKE: Created direct invoke tool '%s' for %s → %s",
            tool_name,
            agent_name,
            arn[:80],
        )

    return tools
