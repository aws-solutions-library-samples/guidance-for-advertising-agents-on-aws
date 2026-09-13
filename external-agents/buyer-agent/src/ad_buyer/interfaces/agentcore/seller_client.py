"""Crewai-free client for reaching the AAMP **seller** over A2A (buyer-side).

Implements FR-4 / D4: the buyer reaches the seller by sending an A2A
``message/send`` to the seller's AgentCore runtime, authenticated with a Cognito
bearer — the **same pattern the AgencyAgent uses** (see the guidance repo's
``a2a_client_tools._invoke_agentcore_oauth`` / ``A2ATokenManager``).

Transport note (deviation from functional-design Q1=B, recorded):
The functional design chose the Strands ``A2AAgent`` wrapper as primary. On
inspection, ``A2AAgent`` fetches an agent card and does not cleanly attach the
Cognito bearer + ``X-Amzn-Bedrock-AgentCore-Runtime-Session-Id`` header required
by the AgentCore OAuth data-plane endpoint. So the **working transport here is
the raw OAuth A2A JSON-RPC POST** (== D4's "same pattern the AgencyAgent uses").
An ``A2AAgent`` path can be slotted in once its client-factory auth is confirmed
on ``dm1``. Fail-closed on missing/failed auth (business-rules BR-6).
"""

import json
import logging
import os
import time
import uuid
from typing import Callable, Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)

_TOKEN_TIMEOUT = 30
_token_cache: dict[str, tuple[str, float]] = {}

# AgentCore returns these while the seller runtime is cold-starting / not READY.
_COLDSTART_STATUSES = (424, 503)
_COLDSTART_MAX_ATTEMPTS = 4
_COLDSTART_BACKOFF_SECONDS = 8


def _region() -> str:
    return os.environ.get("AWS_REGION", "us-east-1")


def _seller_endpoint() -> Optional[str]:
    """Resolve the seller A2A invoke endpoint from env (D4/Q2=A).

    Prefers an explicit URL; else builds the AgentCore data-plane invocations URL
    from the seller runtime ARN.
    """
    url = os.environ.get("AAMP_SELLER_ENDPOINT", "").strip()
    if url:
        return url
    arn = os.environ.get("AAMP_SELLER_RUNTIME_ARN", "").strip()
    if arn:
        return (
            f"https://bedrock-agentcore.{_region()}.amazonaws.com/runtimes/"
            f"{quote(arn, safe='')}/invocations?qualifier=DEFAULT"
        )
    return None


def _mint_bearer() -> tuple[Optional[str], Optional[str]]:
    """Mint a Cognito bearer from the SSM-stored inbound credentials.

    Supports both credential shapes (same as the guidance A2ATokenManager):
    client-credentials (grant_type=client_credentials) or USER_PASSWORD_AUTH.
    Returns (token, error); exactly one is non-None. Cached with a 60s buffer.
    """
    ssm_path = os.environ.get("A2A_SELLER_SSM_PATH", "").strip()
    if not ssm_path:
        return None, "A2A_SELLER_SSM_PATH not set — cannot authenticate to seller"

    cached = _token_cache.get(ssm_path)
    if cached and cached[1] - 60 > time.time():
        return cached[0], None

    try:
        import boto3

        ssm = boto3.client("ssm", region_name=_region())
        doc = json.loads(
            ssm.get_parameter(Name=ssm_path, WithDecryption=True)["Parameter"]["Value"]
        )
    except Exception as e:  # noqa: BLE001 - never leak details
        logger.error("seller auth: SSM credential fetch failed (%s)", type(e).__name__)
        return None, "could not retrieve seller credentials from parameter store"

    try:
        if doc.get("grant_type") == "client_credentials":
            import httpx

            resp = httpx.post(
                doc["token_url"],
                data={
                    "grant_type": "client_credentials",
                    "client_id": doc["client_id"],
                    **({"scope": doc["scope"]} if doc.get("scope") else {}),
                },
                auth=(doc["client_id"], doc["client_secret"]),
                headers={"Accept": "application/json"},
                timeout=_TOKEN_TIMEOUT,
            )
            if resp.status_code >= 400:
                return None, f"seller OAuth token request rejected (HTTP {resp.status_code})"
            payload = resp.json()
            token = payload.get("access_token")
            expires_in = payload.get("expires_in", 3600)
        else:
            import boto3

            cognito = boto3.client("cognito-idp", region_name=_region())
            client_id = doc.get("client_id") or os.environ.get("A2A_CLIENT_ID", "")
            r = cognito.initiate_auth(
                AuthFlow="USER_PASSWORD_AUTH",
                AuthParameters={"USERNAME": doc["username"], "PASSWORD": doc["password"]},
                ClientId=client_id,
            )
            auth = r.get("AuthenticationResult", {})
            token = auth.get("AccessToken")
            expires_in = auth.get("ExpiresIn", 3600)

        if not token:
            return None, "seller auth returned no access token"
        _token_cache[ssm_path] = (token, time.time() + int(expires_in))
        return token, None
    except Exception as e:  # noqa: BLE001
        logger.error("seller auth: token acquisition failed (%s)", type(e).__name__)
        return None, "seller authentication failed"


def _runtime_session_id(context_id: str) -> str:
    """AgentCore runtimeSessionId (>=33 chars). Reuse the A2A context id when valid."""
    sid = context_id or ""
    if len(sid) >= 33:
        return sid[:256]
    return f"buyer-{sid}-{uuid.uuid4().hex}"[:256]


def search_seller_inventory(
    query: str,
    context_id: str = "",
    timeout: float = 240.0,
    on_status: Optional[Callable[[str], None]] = None,
) -> str:
    """Send an A2A message/send to the seller runtime and return its text reply.

    ``query`` is a natural-language inventory request (channel, budget, dates,
    audience). Returns the seller's text, or an explicit error string. Never
    raises; fails closed on auth errors (BR-6).

    ``on_status`` receives a short note when the call has to wait on the seller
    (cold start), so callers can surface the real reason for the delay.
    """

    def _status(message: str) -> None:
        if on_status is None:
            return
        try:
            on_status(message)
        except Exception:  # noqa: BLE001
            pass

    endpoint = _seller_endpoint()
    if not endpoint:
        return "Error: seller endpoint not configured (set AAMP_SELLER_RUNTIME_ARN or AAMP_SELLER_ENDPOINT)."

    token, err = _mint_bearer()
    if err or not token:
        return f"Error: cannot reach seller (auth): {err or 'no token'}"

    import time

    import httpx

    body = {
        "jsonrpc": "2.0",
        "id": uuid.uuid4().hex,
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": query}],
                "messageId": uuid.uuid4().hex,
            }
        },
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": _runtime_session_id(context_id),
    }
    # AgentCore returns 424 (Failed Dependency) / 503 while the seller runtime is
    # cold-starting (container + Strands + catalog load). The first call after an
    # idle period hits this; retry with a short backoff so a cold seller surfaces
    # as a brief wait, not "temporary unavailability". The status arrives before
    # any body, so re-POSTing never double-books anything (reads are idempotent).
    content = json.dumps(body).encode("utf-8")
    resp = None
    for attempt in range(_COLDSTART_MAX_ATTEMPTS):
        try:
            resp = httpx.post(endpoint, content=content, headers=headers, timeout=timeout)
        except Exception as e:  # noqa: BLE001
            return f"Error: seller request failed ({type(e).__name__})"
        if resp.status_code in _COLDSTART_STATUSES and attempt < _COLDSTART_MAX_ATTEMPTS - 1:
            logger.warning(
                "seller not ready (HTTP %s); retry %d/%d in %ds",
                resp.status_code, attempt + 1, _COLDSTART_MAX_ATTEMPTS - 1, _COLDSTART_BACKOFF_SECONDS,
            )
            _status(
                f"Seller runtime is still starting up; retrying in "
                f"{_COLDSTART_BACKOFF_SECONDS}s "
                f"(attempt {attempt + 1} of {_COLDSTART_MAX_ATTEMPTS - 1})"
            )
            time.sleep(_COLDSTART_BACKOFF_SECONDS)
            continue
        break

    if resp.status_code >= 400:
        return f"Error: seller invocation failed (HTTP {resp.status_code})"

    return _extract_a2a_text(resp.text)


def _extract_a2a_text(raw: str) -> str:
    """Pull human-readable text from an A2A JSON-RPC response (message or task)."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw

    if isinstance(parsed.get("error"), dict):
        err = parsed["error"]
        return f"Seller A2A error {err.get('code', '?')}: {err.get('message', 'failed')}"

    result = parsed.get("result")
    if not isinstance(result, dict):
        return raw

    texts: list[str] = []
    for part in result.get("parts", []) or []:
        if part.get("kind") == "text":
            texts.append(part.get("text", ""))
    for artifact in result.get("artifacts", []) or []:
        for part in artifact.get("parts", []) or []:
            if part.get("kind") == "text":
                texts.append(part.get("text", ""))
    if texts:
        return "\n".join(t for t in texts if t)
    # Status message fallback
    status_msg = (result.get("status") or {}).get("message") or {}
    for part in status_msg.get("parts", []) or []:
        if part.get("kind") == "text":
            texts.append(part.get("text", ""))
    return "\n".join(t for t in texts if t) or raw
