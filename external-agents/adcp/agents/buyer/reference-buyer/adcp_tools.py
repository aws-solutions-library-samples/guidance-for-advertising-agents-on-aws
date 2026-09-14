"""
AdCP tool wrappers for calling a seller agent over MCP or A2A.

Each Strands @tool here calls the selected seller agent using whichever
transport that seller's registry entry declares (see seller_agents.py):
  - "mcp" opens a short-lived MCP session (streamable-http) and calls the
    tool by name.
  - "a2a" sends a JSONRPC message/send request with a structured
    {skill, input} DataPart (parameters also sent for legacy sellers), per
    AdCP's A2A binding. The AdCP tool name doubles as the skill name.

Core AdCP task calls (get_products, get_adcp_capabilities,
list_creative_formats) are stamped with adcp_major_version on every
request, per AdCP's version-negotiation contract (see _with_version()).
A seller's own vendor-namespaced extension tools are not core AdCP tasks
and are sent unmodified.

AgentCore Runtime invocations are stateless per-request, so there's no
long-lived connection to maintain between calls; a fresh session/request
per tool call keeps this simple and matches how the working TypeScript
buyer agent (../../../src/client.ts) treats the connection.

Which seller agent (and which transport) is called is resolved per-
conversation via seller_agents.py (see build_adcp_tools() below), so the
same tool implementations work against any AdCP seller agent in the
registry regardless of whether it speaks MCP or A2A.

Every tool below returns what the selected seller agent's live endpoint
returned, including error responses. Nothing is cached or stubbed.

Response parsing on both transports implements AdCP's normative
extraction algorithms verbatim, not an approximation of them:
  - MCP: https://docs.adcontextprotocol.org/docs/building/by-layer/L0/mcp-response-extraction
  - A2A: https://docs.adcontextprotocol.org/docs/building/by-layer/L0/a2a-response-extraction
Both implementations pass every vector in AdCP's published test-vector
suites (mcp-response-extraction.json, a2a-response-extraction.json).
"""

import asyncio
import base64
import binascii
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, get_args

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from strands import tool

# Same token mechanism `seller_agents.py`'s `cognito_bearer` uses, for the governance agent -- which is
# gated by this project's own Cognito pool and on the runtime's `allowedClients`.
from auth import get_test_user_access_token

#: AdCP's `buying_mode` values for `get_products`. One definition, two uses: the type annotation that
#: shapes the tool schema the model sees, and the runtime guard that rejects anything else.
#:
#: This restates the SDK's `BuyingMode` enum, which `use-the-adcp-sdk.md` normally forbids. The exception
#: is that the SDK is not installable in this runtime at all — `adcp==6.6.0` requires
#: a2a-sdk>=1.0.1,<1.0.2 while `strands-agents[a2a]==1.48.0` requires a2a-sdk>=0.3.0,<0.4.0, so the image
#: build fails on the resolution. `test_buying_mode.py` imports the real enum from the dev venv and
#: asserts these are equal, so the restatement cannot drift without a test failing.
BuyingModeLiteral = Literal["brief", "wholesale", "refine"]
BUYING_MODES: tuple[str, ...] = get_args(BuyingModeLiteral)

logger = logging.getLogger(__name__)


class AdcpToolError(RuntimeError):
    """Raised when the seller agent's call itself fails (network/protocol level)."""


# --- Downstream session correlation ---------------------------------------
#
# Every tool call below used to be fully independent: a fresh transport, no
# session identifier of any kind. That left the seller unable to tell that
# several tool calls belong to one buyer conversation, and — for sellers we
# deploy on AgentCore Runtime — made every call land on a brand-new microVM
# (a cold start each time), because AgentCore routes on a session header the
# client never sent.
#
# Per AWS's session docs the header differs by protocol: A2A runtimes route on
# X-Amzn-Bedrock-AgentCore-Runtime-Session-Id, MCP runtimes on Mcp-Session-Id
# (which the platform returns and the client is expected to echo back).
#
# This is the mirror image of the fix already made on this project's own A2A
# *server* side (a2a_runtime/a2a_entrypoint.py): there, turns of one runtime
# session had to stop being separate conversations. Here, calls of one buyer
# conversation have to stop being separate sessions at the seller.

AGENTCORE_SESSION_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"
MCP_SESSION_HEADER = "Mcp-Session-Id"

# Our own correlation header, sent on every transport in addition to the
# platform ones above.
#
# Why a custom header is necessary rather than belt-and-braces: AgentCore
# Runtime does not forward a header into an agent's container unless that
# agent allowlists it (this project already learned that the hard way for
# Authorization itself — see deploy_configure.py's
# build_request_header_configuration). Both seller runtimes allowlist only
# Authorization, so neither platform session header above can be read by
# seller code. On top of that, Mcp-Session-Id is platform-managed in stateless
# MCP: AWS's contract says the platform generates it, so even when a seller
# does see one it identifies a microVM session, not the buyer's conversation.
#
# A custom header is exactly what requestHeaderAllowlist exists for, so this
# is the one identifier a seller can rely on meaning "the buyer conversation
# this call belongs to". Both sellers allowlist it (agentcore.json).
BUYER_SESSION_HEADER = "X-Adcp-Buyer-Session-Id"

# AgentCore Runtime's runtimeSessionId bounds (InvokeAgentRuntime API).
_MIN_SESSION_ID_LENGTH = 33
_MAX_SESSION_ID_LENGTH = 256

# Reusing one session pins calls to one microVM, and AgentCore answers a
# second operation that arrives while that session is being provisioned or
# torn down with a retryable HTTP 409. The buyer agent can issue tool calls
# concurrently within a single turn, so that window is genuinely reachable.
_CONFLICT_STATUS = 409
_CONFLICT_MAX_ATTEMPTS = 4
_CONFLICT_BASE_DELAY_SECONDS = 0.4


def derive_seller_session_id(buyer_session_id: str | None, seller_agent_id: str) -> str | None:
    """A stable downstream session id for one (buyer conversation, seller) pair.

    Deterministic, so every tool call in the same buyer conversation resolves
    to the same value with nothing to store. Scoped per seller so two sellers
    in one conversation never share a session. Returns None when the buyer has
    no session of its own to derive from, in which case callers send no session
    header at all. A per-call id generated here would carry no correlation while
    looking as though it did.
    """
    if not buyer_session_id:
        return None
    candidate = f"adcp-seller-{seller_agent_id}-{buyer_session_id}"
    if len(candidate) < _MIN_SESSION_ID_LENGTH:
        candidate = candidate.ljust(_MIN_SESSION_ID_LENGTH, "0")
    return candidate[:_MAX_SESSION_ID_LENGTH]


# NOTE: there is deliberately no Mcp-Session-Id cache here. Reusing a
# client-generated MCP session id was tried and removed — see the comment in
# _call_seller_tool_mcp. Doing it properly would mean echoing back the id the
# server issues, which the MCP client used here doesn't surface through
# streamablehttp_client; until it does, we send only our own header.


def _is_retryable_conflict(exc: BaseException) -> bool:
    """True for AgentCore's retryable 409 on a session in transition."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status == _CONFLICT_STATUS:
        return True
    text = str(exc)
    return "RetryableConflictException" in text or (
        "409" in text and "Session operation in progress" in text
    )


async def _with_conflict_retry(operation, description: str):
    """Run `operation()`, retrying AgentCore's retryable 409 with backoff.

    Only 409 is retried. Every other failure propagates unchanged so a real
    seller error is never masked as a transient one.
    """
    delay = _CONFLICT_BASE_DELAY_SECONDS
    last_exc: BaseException | None = None
    for attempt in range(1, _CONFLICT_MAX_ATTEMPTS + 1):
        try:
            return await operation()
        except Exception as exc:  # noqa: BLE001 - re-raised below unless retryable
            if not _is_retryable_conflict(exc) or attempt == _CONFLICT_MAX_ATTEMPTS:
                raise
            last_exc = exc
            logger.warning(
                "%s: session busy (attempt %d/%d), retrying in %.1fs",
                description, attempt, _CONFLICT_MAX_ATTEMPTS, delay,
            )
            await asyncio.sleep(delay)
            delay *= 2
    raise AdcpToolError(f"{description} kept returning 409: {last_exc}")


# AdCP major version this buyer agent's request shapes conform to (get_products'
# `brand`/`buying_mode` fields, etc). Declared on every request per AdCP's
# version-negotiation contract: buyers SHOULD emit adcp_major_version on 3.x
# requests so sellers validate/serve against the right major, and it becomes
# a compliance-grader requirement at 3.2. We emit the legacy integer field
# (not release-precision adcp_version) since this agent doesn't yet pin to a
# specific release and the integer form is still honored through all of 3.x.
ADCP_MAJOR_VERSION = 3


# Core AdCP task names - the only ones stamped with adcp_major_version (see
# _with_version below). A seller's vendor-namespaced tools are proprietary
# extensions, not AdCP core tasks; their request schemas don't declare this
# field and may use additionalProperties: false, so it's not sent on those calls.
_CORE_ADCP_TASKS = frozenset({"get_products", "get_adcp_capabilities", "list_creative_formats"})


def _with_version(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Add adcp_major_version to a core AdCP task's arguments (see
    _CORE_ADCP_TASKS), without overwriting a caller-supplied value if one is
    ever passed explicitly. Non-core (vendor extension) tool calls are passed
    through unchanged.
    """
    if tool_name not in _CORE_ADCP_TASKS:
        return arguments
    return {"adcp_major_version": ADCP_MAJOR_VERSION, **arguments}


async def _call_seller_tool_mcp(
    url: str,
    headers: dict[str, str],
    tool_name: str,
    arguments: dict[str, Any],
    session_id: str | None = None,
) -> dict[str, Any]:
    """Open an MCP session against a seller agent, call one tool, return the
    extracted AdCP response.

    Extraction follows AdCP's MCP Response Extraction spec exactly
    (https://docs.adcontextprotocol.org/docs/building/by-layer/L0/mcp-response-extraction):
    isError is checked first (an error response is never processed as
    success, even if it happens to carry non-error structuredContent);
    then structuredContent, if present and not an adcp_error-only object;
    then a text-fallback pass over content[] in order, parsing each text
    item as JSON and skipping any that don't parse, aren't a JSON object,
    or are adcp_error-only. If the seller returned isError=True, the
    returned dict includes an "mcp_error" key with the error text — this
    is not synthesized, it is what the seller agent actually returned.
    """
    arguments = _with_version(tool_name, arguments)

    # Mcp-Session-Id is what AgentCore routes MCP requests on. Sending a
    # consistent value for the whole buyer conversation keeps its tool calls on
    # one warm microVM and lets the seller correlate them; sending none (the
    # previous behaviour) meant a cold start per call. Omitted entirely when
    # the buyer has no session of its own — see derive_seller_session_id.
    call_headers = dict(headers)
    if session_id:
        # Only our own header. We deliberately do NOT send Mcp-Session-Id:
        # per the MCP spec a client may only echo back a session id the server
        # issued, and generating one breaks a standards-compliant server outright
        # — verified live against a third-party (non-AgentCore) MCP endpoint,
        # which succeeds with
        # no session headers and with our own header, and fails with a
        # client-generated Mcp-Session-Id. AgentCore tolerated it (while still logging
        # a 404 on teardown, since it never recognised the id), so this was
        # invisible until a non-AgentCore seller was called.
        #
        # Nothing proven is lost: Phase D's own conclusion was that the
        # microVM-affinity benefit of that header was unproven, whereas
        # BUYER_SESSION_HEADER is the identifier a seller actually reads (it's
        # on both sellers' requestHeaderAllowlist) and is confirmed to group a
        # conversation's calls.
        call_headers[BUYER_SESSION_HEADER] = session_id

    async def _call():
        async with streamablehttp_client(url, headers=call_headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool(tool_name, arguments)

    result = await _with_conflict_retry(_call, f"MCP {tool_name}")

    if result.isError:
        text = ""
        if result.content:
            text = getattr(result.content[0], "text", "") or ""
        return {"mcp_error": text or f"Unknown tool: '{tool_name}'"}

    structured_content = getattr(result, "structuredContent", None)
    extracted = _extract_mcp_success_data(structured_content, result.content)
    if extracted is not None:
        return extracted

    return {}


def _is_adcp_error_only(obj: dict[str, Any]) -> bool:
    """True if `obj`'s only key is "adcp_error" - per spec, this is an
    error response that may be missing the isError flag, not success data.
    """
    return list(obj.keys()) == ["adcp_error"]


def _extract_mcp_success_data(
    structured_content: Any, content: list[Any] | None
) -> dict[str, Any] | None:
    """AdCP's MCP success-extraction algorithm (isError already checked by caller).

    1. structuredContent, if a non-array object and not adcp_error-only.
    2. Text fallback: iterate content[] in order, JSON-parse each "text"
       item, returning the first result that's a non-array object and not
       adcp_error-only. Non-JSON or non-object items are skipped, not
       treated as fatal.
    3. None if nothing matched - a plain-text response with no machine-
       readable AdCP data, not an error.
    """
    if isinstance(structured_content, dict) and not _is_adcp_error_only(structured_content):
        return structured_content

    for item in content or []:
        text = getattr(item, "text", None)
        if not text:
            continue
        if len(text) > 1_048_576:  # 1MB size limit, per spec
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and not _is_adcp_error_only(parsed):
            return parsed

    return None


async def _call_seller_tool_a2a(
    url: str,
    headers: dict[str, str],
    tool_name: str,
    arguments: dict[str, Any],
    session_id: str | None = None,
) -> dict[str, Any]:
    """Send a structured AdCP skill-invocation request over A2A's JSONRPC binding.

    Per AdCP's A2A guide (Explicit Skill / Deterministic invocation), a
    buyer invokes a skill deterministically with a DataPart shaped like
    {"data": {"skill": ..., "input": ...}} - "input" is the current field
    name for the task's arguments; "parameters" is the legacy name some
    sellers still only read, so both are sent with the same value. The
    "kind": "data" key is the v0.3 wire form; A2A 1.0 carries no "kind"
    discriminator at all (both are sent here so the request round-trips
    with either a v0.3 or 1.0 server, matching the compatibility-period
    guidance in the guide). The AdCP tool name (get_products,
    get_adcp_capabilities, etc.) is the skill name. Sellers that only
    implement free-text natural-language handling (not the structured
    contract) will return whatever they return for that shape — including
    a canned/unhelpful response — and that response is passed through
    as-is, not corrected or reinterpreted.

    Response parsing follows AdCP's A2A Response Extraction spec exactly
    (https://docs.adcontextprotocol.org/docs/building/by-layer/L0/a2a-response-extraction):
    unwrap the A2A 1.0 StreamResponse envelope if present, normalize
    status.state, then branch on final vs. interim status - final states
    read the *last* DataPart from artifacts[0] (falling back to
    status.message.parts if artifacts are empty) and reject the
    {response: {...}} framework-wrapper shape as a server bug; interim
    states read the *first* DataPart from status.message.parts. See
    extract_adcp_response_from_a2a() below for the actual algorithm.
    """
    versioned_arguments = _with_version(tool_name, arguments)
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [
                    {
                        "kind": "data",
                        "data": {
                            "skill": tool_name,
                            # "input" is the current AdCP A2A field name for
                            # explicit skill invocation; "parameters" is kept
                            # alongside it since it's the legacy key some
                            # sellers still only read, and AdCP's spec still
                            # documents it as accepted during the
                            # compatibility period.
                            "input": versioned_arguments,
                            "parameters": versioned_arguments,
                        },
                    }
                ],
                "messageId": f"msg-{uuid.uuid4()}",
                "kind": "message",
            }
        },
    }

    # A2A's own conversation identifier. messageId is per-message and must stay
    # unique; contextId is what ties messages into one conversation, and
    # omitting it (the previous behaviour) makes a spec-compliant seller treat
    # every call as unrelated — exactly the defect fixed on this project's own
    # A2A server side. Sent only when there's a real buyer session to derive it
    # from, so a session-less call still behaves as before.
    call_headers = {**headers, "Content-Type": "application/json"}
    if session_id:
        body["params"]["message"]["contextId"] = session_id
        # And the platform-level session, for microVM affinity on A2A runtimes
        # we deploy. Harmless to an external A2A server, which ignores it.
        call_headers[AGENTCORE_SESSION_HEADER] = session_id
        # Plus the header a seller's own code can read regardless of what the
        # platform forwards (see BUYER_SESSION_HEADER).
        call_headers[BUYER_SESSION_HEADER] = session_id

    async def _post():
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, headers=call_headers, json=body)
        response.raise_for_status()
        return response

    resp = await _with_conflict_retry(_post, f"A2A {tool_name}")
    envelope = resp.json()

    if "error" in envelope:
        return {"a2a_error": envelope["error"]}

    result = envelope.get("result", {})
    extracted = extract_adcp_response_from_a2a(result)
    if extracted is not None:
        return extracted

    return {"raw_response": envelope}


def _normalize_a2a_state(state: Any) -> str | None:
    """Normalize an A2A status.state value to lowercase-hyphen form.

    Per AdCP's A2A Response Extraction spec: strip a leading "TASK_STATE_"
    prefix (the A2A 1.0 ProtoJSON form, e.g. "TASK_STATE_INPUT_REQUIRED"),
    lowercase, and replace underscores with hyphens. This maps both 1.0
    and v0.3 ("input-required") wire values onto the same normalized
    string so the rest of the extraction algorithm doesn't need to know
    which wire version produced the response.
    """
    if not isinstance(state, str):
        return None
    stripped = state[len("TASK_STATE_") :] if state.startswith("TASK_STATE_") else state
    return stripped.lower().replace("_", "-")


def _is_data_part(part: Any) -> bool:
    """True if `part` is an A2A DataPart, detected by field presence.

    Per AdCP's spec, a 1.0 DataPart has a non-null object `data` field and
    no `kind`; a v0.3 DataPart has `kind: "data"` and a `data` field. Both
    satisfy "the `data` field is a non-null, non-array object" - checking
    `kind` alone would silently miss every 1.0-wire-format DataPart.
    """
    if not isinstance(part, dict):
        return False
    data = part.get("data")
    return isinstance(data, dict)


def _unwrap_a2a_stream_envelope(value: Any) -> Any:
    """Unwrap an A2A 1.0 StreamResponse oneof, exactly once.

    A StreamResponse is a single-key object with key "task", "message",
    "statusUpdate", or "artifactUpdate" wrapping the real Task /
    TaskStatusUpdateEvent / Message. Our synchronous message/send calls
    get the bare object back (this wrapper only applies to SSE/webhook
    delivery), so this is a no-op for the common case here - kept for
    correctness if a seller's JSONRPC result ever comes back wrapped.
    """
    if not isinstance(value, dict):
        return value
    keys = list(value.keys())
    if len(keys) != 1:
        return value
    key = keys[0]
    if key in ("task", "message", "statusUpdate", "artifactUpdate") and isinstance(value[key], dict):
        return value[key]
    return value


def extract_adcp_response_from_a2a(task_or_event: dict[str, Any]) -> dict[str, Any] | None:
    """Extract the AdCP response payload from an A2A Task or TaskStatusUpdateEvent.

    Implements AdCP's normative extraction algorithm
    (https://docs.adcontextprotocol.org/docs/building/by-layer/L0/a2a-response-extraction)
    verbatim: unwrap the stream envelope, normalize status.state, then
    branch on final vs. interim status. Returns None if status.state is
    absent or unrecognized (an "unknown state" per the spec, not an
    error) or if no DataPart is present anywhere applicable.
    """
    task = _unwrap_a2a_stream_envelope(task_or_event)
    if not isinstance(task, dict):
        return None

    state = _normalize_a2a_state(task.get("status", {}).get("state"))
    if state is None:
        return None

    final_states = ("completed", "failed", "canceled", "rejected")
    interim_states = ("working", "submitted", "input-required", "auth-required")

    if state in final_states:
        artifacts = task.get("artifacts") or []
        if artifacts:
            parts = artifacts[0].get("parts", [])
            data_parts = [p["data"] for p in parts if _is_data_part(p)]
            if data_parts:
                last = data_parts[-1]
                # Wrapper rejection: {"data": {"response": {...}}} (single
                # key "response") is a framework-wrapper bug per spec, not
                # a valid payload - surfaced plainly rather than silently
                # unwrapped, since silently unwrapping would hide a real
                # seller-side conformance bug from the person testing it.
                if list(last.keys()) == ["response"] and isinstance(last["response"], dict):
                    return {
                        "adcp_error": {
                            "code": "framework_wrapper_detected",
                            "message": (
                                "Seller's A2A DataPart wraps the AdCP payload in a "
                                "{response: {...}} object, which AdCP's A2A spec "
                                "explicitly disallows. This is a server-side bug, "
                                "not a client parsing issue."
                            ),
                        }
                    }
                return last
        # Fallback: final state with no (or empty) artifacts - check
        # status.message.parts, same extraction as the interim path.
        return _extract_from_a2a_message(task, prefer_first=True)

    if state in interim_states:
        return _extract_from_a2a_message(task, prefer_first=True)

    return None


def _extract_from_a2a_message(task: dict[str, Any], *, prefer_first: bool) -> dict[str, Any] | None:
    """Extract a DataPart from status.message.parts[].

    Per spec, interim states (and the final-state fallback when artifacts
    are empty) use the *first* DataPart in status.message.parts, not the
    last - interim updates are single-event snapshots, not accumulated.
    """
    parts = task.get("status", {}).get("message", {}).get("parts", [])
    data_parts = [p["data"] for p in parts if _is_data_part(p)]
    if not data_parts:
        return None
    return data_parts[0] if prefer_first else data_parts[-1]


async def _call_seller_tool(
    seller_agent: dict[str, Any],
    tool_name: str,
    arguments: dict[str, Any],
    buyer_session_id: str | None = None,
) -> dict[str, Any]:
    url = seller_agent["url"]
    headers = seller_agent["headers"]
    transport = seller_agent.get("transport", "mcp")

    # One stable downstream session per (buyer conversation, seller), so this
    # seller sees the conversation's tool calls as one session instead of N
    # unrelated ones. None when the buyer has no session to derive from.
    session_id = derive_seller_session_id(buyer_session_id, seller_agent.get("id", "seller"))

    if transport == "a2a":
        return await _call_seller_tool_a2a(url, headers, tool_name, arguments, session_id)
    return await _call_seller_tool_mcp(url, headers, tool_name, arguments, session_id)


# Public name for the same function, used by app.py's "direct_call" action
# (the UI's "Direct" mode - a thin client that calls a seller agent's AdCP
# tool directly over MCP/A2A, with no LLM/agent reasoning in between). Same
# real network call as every tool the Buyer Agent uses; nothing here is a
# separate/mocked code path.
call_seller_tool = _call_seller_tool

DIRECT_CALL_TOOL_NAMES = (
    "get_products",
    "get_adcp_capabilities",
    "list_creative_formats",
)


async def _call_all_sellers(
    seller_agents: list[dict[str, Any]],
    tool_name: str,
    arguments: dict[str, Any],
    buyer_session_id: str | None = None,
) -> dict[str, Any]:
    """Run one AdCP task against EVERY configured sales agent, in parallel.

    This is the buyer-side shape AdCP's own SDK defines: `ADCPMultiAgentClient`
    holds a list of sales agents and executes a task across all of them with
    `asyncio.gather`, returning one result per agent for the buyer to compare.
    A buyer agent is configured with its sellers; it does not have one "current"
    seller chosen for it.

    Returns one entry per seller, each either a real response or that seller's
    real error — never a partial success dressed up as a whole one. A seller
    that doesn't implement a task reports its own error alongside the others
    that did answer.
    """
    async def call_one(seller: dict[str, Any]) -> dict[str, Any]:
        entry = {
            "seller_id": seller.get("id"),
            "seller_name": seller.get("name"),
            "transport": seller.get("transport", "mcp"),
        }
        try:
            entry["response"] = await _call_seller_tool(
                seller, tool_name, arguments, buyer_session_id
            )
        except Exception as exc:  # noqa: BLE001 - reported per seller, never hidden
            entry["error"] = f"{type(exc).__name__}: {exc}"
        return entry

    results = await asyncio.gather(*[call_one(s) for s in seller_agents])
    return {
        "task": tool_name,
        "sellers_queried": len(results),
        "results": list(results),
    }


def _as_aware_iso(value: str, *, end_of_day: bool) -> str:
    """Normalise a date or datetime string into a TIMEZONE-AWARE ISO-8601 datetime.

    AdCP's plan `flight` requires tz-aware datetimes, and the governance agent rejects anything else.
    Confirmed against the live agent, which answered a bare `"2026-01-01"` with:

        INVALID_REQUEST[plans.0.flight.start]: Input should have timezone info

    A naive value is interpreted as **UTC**, stated here rather than left implicit: guessing the caller's
    local zone would silently shift a flight window by hours, and a campaign that starts a day early is a
    real spend error, not a formatting nit.

    A bare date becomes the START of that day, or the END of it for `flight.end`, so an inclusive range
    like 1 Jan..30 Jan covers all of 30 January instead of expiring at midnight as it began.
    """
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AdcpToolError(
            f"{value!r} is not an ISO-8601 date or datetime (expected e.g. '2026-01-01' or "
            "'2026-01-01T00:00:00Z')."
        ) from exc

    date_only = len(text) == 10
    if date_only and end_of_day:
        parsed = parsed.replace(hour=23, minute=59, second=59)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


# Clock-skew tolerance applied to the `exp` comparison below, matching the ±60s tolerance the AdCP JWS
# profile uses elsewhere for exp/iat/nbf checks (see security.mdx's governance JWS profile).
_GOVERNANCE_CONTEXT_EXP_SKEW_SECONDS = 60


def _governance_context_expired(token: str) -> bool:
    """Whether a `governance_context` compact JWS's `exp` claim has passed (NFR-U2-G4/BR-U2-16).

    Decodes the payload segment ONLY -- this never verifies the signature. The buyer trusts its own
    configured governance agent's output; this is a staleness check ("should I still be using this
    token"), not an authenticity check ("did the governance agent really issue this"). Signature
    verification against the governance agent's published JWKS is the SELLER's job (U3), which is a
    different, heavier-weight concern than this buyer-side freshness check.

    Fails CLOSED: any malformed input (wrong segment count, undecodable base64, non-JSON payload, or a
    payload with no `exp`) is treated as expired, forcing a fresh check_governance call rather than
    silently trusting a token this function could not read.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return True
    payload_b64 = parts[1]
    # Compact JWS uses base64url without padding; Python's urlsafe_b64decode needs the string padded
    # to a multiple of 4 before it will decode.
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    try:
        payload_bytes = base64.urlsafe_b64decode(padded)
    except (binascii.Error, ValueError):
        return True
    try:
        payload = json.loads(payload_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return True
    if not isinstance(payload, dict) or "exp" not in payload:
        return True
    exp = payload["exp"]
    if not isinstance(exp, (int, float)):
        return True
    return datetime.now(timezone.utc).timestamp() > exp + _GOVERNANCE_CONTEXT_EXP_SKEW_SECONDS


#: Governance agent resolution moved to governance_agents.py (BR-U2-3/BR-U2-4): a single env-var URL
#: could not express Boltive's external static_bearer shape alongside an in-account cognito_bearer agent.
#: GOVERNANCE_AGENTS_JSON (mirroring SELLER_AGENTS_JSON's shape) replaces GOVERNANCE_AGENT_MCP_URL.
from governance_agents import (  # noqa: E402
    GovernanceAgentError,
    governance_configured,
    resolve_governance_agent,
)

#: This buyer's own agent URL, for `check_governance`'s `caller` -- which AdCP defines as the buyer-side
#: orchestrator making the check, NOT the seller the check is about (that is `target_agent`).
from agents_registry import this_buyer_agent_url  # noqa: E402


def build_adcp_tools(
    seller_agents: list[dict[str, Any]] | dict[str, Any],
    buyer_session_id: str | None = None,
) -> list[Any]:
    """Build the set of AdCP tools bound to one seller agent.

    Args:
        seller_agents: The resolved sales agents this buyer represents — a list
            of {"id", "name", "url", "transport", "headers"} entries from
            seller_agents.resolve_seller_agent(). Every tool queries ALL of
            them in parallel and returns one result per seller, matching
            AdCP's own `ADCPMultiAgentClient.get_products` (see
            _call_all_sellers). The model then compares real answers from real
            sellers and decides which inventory to recommend — that choice is
            the agent's job, not a dropdown's.

            A single dict is accepted for backwards compatibility with callers
            that still pin one seller, and is treated as a one-entry list.
        buyer_session_id: This buyer conversation's own session id (the
            AgentCore runtimeSessionId). Used to derive a stable downstream
            session id so every tool call in the conversation reaches each
            seller as one session — see derive_seller_session_id. Omit it and
            each call is independent, as it was before.

    Only core AdCP tasks are bound here. A seller may also expose
    vendor-namespaced extension tools; those are not registered, because a tool
    offered to every seller but implemented by none returns an "Unknown tool"
    error on every call. To add one, register it alongside these and send the
    vendor's own tool name — _with_version leaves non-core names untouched.
    """
    sellers = [seller_agents] if isinstance(seller_agents, dict) else list(seller_agents)
    if not sellers:
        raise AdcpToolError(
            "No sales agents configured for this buyer agent (SELLER_AGENTS_JSON is empty)."
        )

    # NFR-U2-G3: per-session ledger of approved check_governance results, so adcp_create_media_buy can
    # enforce BR-U2-13/BR-U2-14 in code rather than relying solely on the model following its own tool
    # docstrings. Key is the economically material fields of the checked action (not plan_id alone) so
    # an approval for one product/budget cannot silently authorize a different one under the same plan.
    # In-memory and scoped to this build_adcp_tools() call: a check's own governance_context expiry
    # (~15 minutes for intent tokens) is the real durability boundary for this data, not the container's
    # lifetime -- see nfr-design-patterns.md pattern 1 for the full reasoning.
    _approved_checks: dict[tuple[str, str, float, str], tuple[str, str]] = {}

    def scope(seller_ids: list[str] | str | None) -> list[dict[str, Any]]:
        """Narrow a task to specific sales agents, or all of them by default.

        AdCP's own SDK exposes three access patterns, not one: a single agent
        (`client.agent(id)`), a chosen subset (`client.agents([ids])`) and every
        configured agent (`client.allAgents()`) — see @adcp/sdk's
        ADCPMultiAgentClient. Each AdCP task is a call to one sales agent's
        endpoint; "ask everyone" is a client-side fan-out, so scoping belongs
        here as a parameter of the call, not as a fixed property of the buyer.

        An unknown id is an error rather than a silent no-op or a silent
        fall-back to all sellers: quietly querying everybody when the user asked
        for one seller would attribute inventory to the wrong place.
        """
        if not seller_ids:
            return sellers
        wanted = [seller_ids] if isinstance(seller_ids, str) else list(seller_ids)
        by_id = {s.get("id"): s for s in sellers}
        unknown = [w for w in wanted if w not in by_id]
        if unknown:
            raise AdcpToolError(
                f"Unknown sales agent id(s): {', '.join(unknown)}. "
                f"Configured ids: {', '.join(str(s.get('id')) for s in sellers)}."
            )
        return [by_id[w] for w in wanted]


    @tool
    async def adcp_sync_accounts(
        brand_domain: str,
        operator: str,
        seller_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Declare the advertiser account at sales agents (do this first).

        Every later account-scoped task -- registering a governance agent, booking anything -- is keyed
        on an account that has to exist at the seller first. A seller that has never heard of the
        (brand, operator) pair rejects those calls with ACCOUNT_NOT_FOUND, so start here.

        Safe to repeat: a seller that already has this pair reports action "updated" and keeps the
        original created_at, it does not create a duplicate.

        Args:
            brand_domain: The advertiser's domain, e.g. "acme.example". Ask the user rather than
                guessing -- this is the account's identity at the seller, and the wrong domain declares
                an account for the wrong advertiser.
            operator: The operator/agency declaring the account, e.g. an agency domain or name. Ask the
                user; do not invent one.
            seller_ids: Restrict to these sales agent ids. Omit to declare at every configured agent,
                which is the normal case -- each seller keeps its own account records.
        """
        args = {
            # >= 16 chars, enforced by the seller and by the SDK model.
            "idempotency_key": f"sync-acct-{uuid.uuid4().hex}",
            "accounts": [{"brand": {"domain": brand_domain}, "operator": operator}],
        }
        return await _call_all_sellers(scope(seller_ids), "sync_accounts", args, buyer_session_id)

    @tool
    async def adcp_sync_governance(
        brand_domain: str,
        operator: str,
        seller_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Register this buyer's governance agent with sales agents (the Bind step).

        Tells each sales agent which governance agent owns this buyer's campaign plans, so the seller
        can consult it later. Do this BEFORE syncing a plan or asking a seller to book anything, since a
        seller with no registered governance agent has nothing to check against.

        The account must already exist at the seller (sync_accounts creates it); a seller that has
        never heard of the account will say so.

        Args:
            brand_domain: The advertiser's domain, e.g. "acme.example". Together with `operator` this is
                the account's natural key at the seller.
            operator: The operator/agency name that declared the account at the seller.
            seller_ids: Restrict to these sales agent ids. Omit to register with every configured agent,
                which is the normal case -- each seller needs its own registration.
        """
        governance = resolve_governance_agent()
        args = {
            # >= 16 chars, enforced by the seller and by the SDK model.
            "idempotency_key": f"sync-gov-{uuid.uuid4().hex}",
            "accounts": [
                {
                    "account": {"brand": {"domain": brand_domain}, "operator": operator},
                    # Exactly one. `maxItems: 1` on this array is load-bearing in AdCP: one agent owns an
                    # account's plans.
                    "governance_agents": [
                        {
                            "url": governance["url"],
                            "authentication": {
                                "schemes": ["Bearer"],
                                # The credential the seller will present, minted here rather than
                                # stored. NOTE the lifetime: a Cognito access token lasts one hour, so a
                                # seller holding this will eventually present an expired one. Acceptable
                                # for a demonstrator; production wants a credential the seller can
                                # refresh. Credentials are write-only in AdCP, so this value is not
                                # echoed back in the response.
                                "credentials": governance["headers"]["Authorization"].removeprefix(
                                    "Bearer "
                                ),
                            },
                        }
                    ],
                }
            ],
        }
        return await _call_all_sellers(
            scope(seller_ids), "sync_governance", args, buyer_session_id
        )

    @tool
    async def adcp_sync_plans(
        plan_id: str,
        brand_domain: str,
        objectives: str,
        budget_total: float,
        flight_start: str,
        flight_end: str,
        channels: list[str],
        currency: str = "USD",
        reallocation_threshold: float | None = None,
        human_review_required: bool = False,
        custom_policies: list[dict[str, Any]] | None = None,
        countries: list[str] | None = None,
        regions: list[str] | None = None,
        required_channels: list[str] | None = None,
    ) -> dict[str, Any]:
        """Register a campaign plan with the governance agent (the Plan step).

        Pushes budget, flight dates and authorised markets/channels to the governance agent so later
        checks have a plan to judge against. This calls the GOVERNANCE agent, not a sales agent.

        Authorised markets and channels are what let the governance agent's geo and channel checks
        actually RUN. Per the AdCP campaign-governance spec, `countries`/`regions` are both the plan's
        geo policy AND its enforcement boundary: the agent rejects a governed action targeting a market
        outside them. If you leave markets or channels unset, the agent reports those categories as
        "not evaluated" rather than passed -- so a plan that wants geo/channel governance MUST declare
        them, and the matching spend-commit check MUST carry the action's markets/channels.

        Args:
            plan_id: Stable id for this plan, e.g. "pantene-q1-audio". Re-syncing the same id updates it.
            brand_domain: The advertiser's domain, e.g. "acme.example".
            objectives: What the campaign is trying to achieve, in the user's own words.
            budget_total: Total budget as a number, in `currency`.
            flight_start: Campaign start as an ISO-8601 date or datetime, e.g. "2026-01-01".
            flight_end: Campaign end, same format. Must be after `flight_start`.
            channels: AdCP channel names the plan authorises. Use AdCP's own vocabulary, e.g.
                "streaming_audio", "podcast", "display", "ctv", "olv". Note "audio" is NOT an AdCP
                channel -- streaming audio is "streaming_audio".
            currency: ISO currency code for the budget. Defaults to USD.
            reallocation_threshold: Fraction of the budget that may be moved between packages without
                re-approval, e.g. 0.1 for 10%. AdCP requires this field and no brief ever states it
                (BR-U2-8). Defaults to BUYER_REALLOCATION_THRESHOLD_PCT (env var, default 0.1) -- a
                POLICY default, not a measurement. Pass an explicit value only if the user stated a
                tolerance; otherwise omit it and let the configured default apply.
            human_review_required: Set true ONLY when the user says this campaign needs human sign-off
                before anything proceeds, or describes it as falling in a regulated decision category.
                AdCP's own examples are credit, insurance pricing, recruitment and housing allocation --
                regulations prohibiting solely automated decisions about individuals (GDPR Art 22, EU AI
                Act Annex III). The governance agent then SUSPENDS the plan: every later check and
                outcome is refused with CAMPAIGN_SUSPENDED until a human resolves it out of band. It is
                not a "be careful" hint, so do not set it because a budget is large or a brand is
                sensitive. **Suspension is sticky**: re-syncing the same plan_id without the flag will
                NOT clear it, so use a fresh plan_id if the user wants an unsuspended plan.
            custom_policies: Campaign-specific policies, when the user names a rule this campaign must
                follow. Each entry needs three fields:
                  policy_id   short stable id, e.g. "eu-ai-act-annex-iii"
                  enforcement "must", "should" or "may"
                  policy      the rule in natural language, in the user's own terms
                Optionally `requires_human_review: true`, which mandates human oversight for this
                policy -- the governance agent then sets human_review_required on the plan itself and
                suspends it, exactly as the flag above does, but with the policy named as the reason.
                No other keys are accepted. Do not invent a policy the user did not state: a policy
                recorded here is one the governance agent reports as in force.
            countries: ISO 3166-1 alpha-2 codes the plan authorises, e.g. ["US"]. The governance agent
                denies a governed action targeting a market outside this set. Set it when the campaign
                is geographically bounded; omit it to leave geo ungoverned. Do not invent a geography
                the user did not state.
            regions: ISO 3166-2 codes for sub-national authorisation, e.g. ["US-CA", "US-NY"]. Same
                enforcement as `countries` but at region granularity. Combine with `countries` or use
                on its own.
            required_channels: AdCP channel names the plan REQUIRES (a stricter statement than
                `channels`, which is the allowed set). Use the same vocabulary as `channels`. Omit
                unless the user says the campaign must run on specific channels.
        """
        governance = resolve_governance_agent()

        # Validated HERE rather than posted and left to fail at the governance agent -- a remote schema
        # error arrives as a nested pointer the model has to decode, while this arrives as a sentence
        # naming the fix.
        #
        # Validated BY HAND, not by constructing the SDK's `PolicyEntry`. This runtime deliberately
        # cannot carry the AdCP SDK: `adcp==6.6.0` pins an a2a-sdk range that conflicts with this
        # runtime's own a2a-sdk (see requirements.txt), so `import adcp` raises ModuleNotFoundError in
        # the deployed container -- proven live when an SDK-construction version of this tool broke
        # every sync_plans with exactly that error. So we validate against small copies of AdCP's own
        # vocabulary; the governance agent (which DOES carry the SDK) is the authoritative validator.
        # adcp-spec-first's documented exception: the SDK is genuinely unavailable in this process.
        #
        # `may` is deliberately accepted alongside must/should: it is a real `PolicyEnforcementLevel`
        # member even though the field prose says "(must|should)". The enum is the contract.
        _ENFORCEMENT = {"must", "should", "may"}
        _POLICY_KEYS = {
            "policy_id", "enforcement", "policy", "requires_human_review",
            "description", "jurisdictions", "channels", "source",
        }
        policies: list[dict[str, Any]] | None = None
        if custom_policies:
            policies = []
            for index, entry in enumerate(custom_policies):
                if not isinstance(entry, dict):
                    reason = "each entry must be an object"
                elif not all(entry.get(k) for k in ("policy_id", "enforcement", "policy")):
                    reason = "policy_id, enforcement and policy are all required"
                elif entry.get("enforcement") not in _ENFORCEMENT:
                    reason = "enforcement must be one of must/should/may"
                elif set(entry) - _POLICY_KEYS:
                    reason = f"unknown key(s): {sorted(set(entry) - _POLICY_KEYS)}"
                else:
                    reason = None
                if reason is not None:
                    return {
                        "error": "invalid_custom_policy",
                        "message": (
                            f"custom_policies[{index}] is not a valid AdCP policy entry ({reason}). "
                            "Required: policy_id (string), enforcement (one of must/should/may), policy "
                            "(the rule in natural language). Optional: requires_human_review (boolean), "
                            "description, jurisdictions, channels. No other keys are accepted."
                        ),
                    }
                policy = {k: v for k, v in entry.items() if v is not None}
                policy.setdefault("source", "inline")  # AdCP's own default for an inline policy
                policies.append(policy)
        if reallocation_threshold is None:
            # BR-U2-8: no brief ever states this, so it defaults to a configured percentage of
            # budget_total rather than the protocol's bare 0.1 default -- disclosed in the Plan panel
            # as a policy default, not left silently at a hardcoded literal.
            pct = float(os.environ.get("BUYER_REALLOCATION_THRESHOLD_PCT", "0.1"))
            reallocation_threshold = budget_total * pct
        # Channels and markets validated by hand (no SDK in this runtime, see above), against a copy of
        # AdCP's MediaChannel enum and the ISO 3166 code shapes. A bad value is caught here as a
        # sentence rather than at the governance agent as a nested pointer; the governance agent
        # re-validates against the real SDK types regardless.
        _MEDIA_CHANNELS = {
            "display", "olv", "social", "search", "ctv", "linear_tv", "radio", "streaming_audio",
            "podcast", "dooh", "ooh", "print", "cinema", "email", "gaming", "retail_media",
            "influencer", "affiliate", "product_placement", "sponsored_intelligence",
        }
        bad_channels = [
            c for c in ([*channels, *(required_channels or [])]) if c not in _MEDIA_CHANNELS
        ]
        if bad_channels:
            return {
                "error": "invalid_plan",
                "message": (
                    f"Not AdCP channel names: {bad_channels}. Use AdCP's vocabulary, e.g. "
                    "'streaming_audio' (not 'audio'), 'podcast', 'display', 'ctv', 'olv'."
                ),
            }
        bad_countries = [c for c in (countries or []) if not re.fullmatch(r"[A-Z]{2}", str(c))]
        bad_regions = [r for r in (regions or []) if not re.fullmatch(r"[A-Z]{2}-[A-Z0-9]{1,3}", str(r))]
        if bad_countries or bad_regions:
            return {
                "error": "invalid_plan",
                "message": (
                    "The plan's markets are not valid ISO codes. countries must be ISO 3166-1 alpha-2 "
                    f"(e.g. 'US', not 'USA') -- rejected: {bad_countries}; regions must be ISO 3166-2 "
                    f"(e.g. 'US-CA', not 'California') -- rejected: {bad_regions}."
                ),
            }

        channels_block: dict[str, Any] = {"allowed": channels}
        if required_channels:
            channels_block["required"] = required_channels
        plan_payload: dict[str, Any] = {
            "plan_id": plan_id,
            "brand": {"domain": brand_domain},
            "objectives": objectives,
            "budget": {
                "total": budget_total,
                "currency": currency,
                # Required by AdCP, confirmed live: the governance agent rejected a budget without it
                # ("reallocation_threshold: Field required"). Surfaced as a tool parameter rather than
                # hardcoded so the value is the caller's, not ours.
                "reallocation_threshold": reallocation_threshold,
            },
            "flight": {
                "start": _as_aware_iso(flight_start, end_of_day=False),
                "end": _as_aware_iso(flight_end, end_of_day=True),
            },
            "channels": channels_block,
        }
        # Markets: both the plan's geo policy AND its enforcement boundary. Omitted (not sent empty)
        # when unset, so an unbounded plan stays unbounded rather than asserting "no countries".
        if countries:
            plan_payload["countries"] = countries
        if regions:
            plan_payload["regions"] = regions
        # human_review_required defaults to false in the schema, so sending it explicitly says nothing
        # extra; custom_policies omitted rather than [] (an empty list would claim "no bespoke policies").
        if human_review_required:
            plan_payload["human_review_required"] = True
        if policies:
            plan_payload["custom_policies"] = policies

        args = {
            "idempotency_key": f"sync-plans-{uuid.uuid4().hex}",
            "plans": [plan_payload],
        }
        return await _call_seller_tool(governance, "sync_plans", args, buyer_session_id)

    @tool
    async def adcp_check_governance(
        seller_id: str,
        plan_id: str,
        tool_name: str,
        payload: dict[str, Any],
        governance_context: str | None = None,
        consultation_context: str | None = None,
    ) -> dict[str, Any]:
        """Check whether an intended action is authorised by the campaign plan (the Govern step).

        Calls the GOVERNANCE agent's check_governance task, BEFORE sending the actual action to a
        seller. Do this before get_products on a governed session (BR-U2-11, our own policy, not an
        AdCP requirement) and ALWAYS before create_media_buy or any other spend-commit action
        (BR-U2-13, this one IS an AdCP MUST).

        The three verdicts, and what each hands back:

        - "approved" carries a `governance_context` token. That is the authorising artefact: pass it to
          adcp_create_media_buy so the seller can verify the action was checked.
        - "conditions" carries a `consultation_context` instead -- a NON-authorising negotiation handle.
          It is not a substitute for a governance_context and a seller will not accept it. Adjust the
          payload per the returned `conditions`, then call this tool again passing that handle as
          `consultation_context`. Never treat "conditions" as approval (BR-U2-15).
        - "denied" carries neither. Do not send the action to the seller; tell the user why (the
          response's `explanation`).

        This used to say `conditions` carried a `governance_context`. It did, and that was the defect:
        per AdCP, "Only approved returns a governance_context token", and on the seller's own execution
        check "a conditions response is invalid on this path and does not authorize a commit".

        Args:
            seller_id: Which sales agent this action targets (its id from the configured registry).
                On an intent check the governance agent signs its returned token's `aud` claim to this
                seller's own canonical identity, and a conformant seller REJECTS a token addressed to
                anyone else -- so this must name the actual seller `tool_name`/`payload` are about to
                be sent to, not the buyer's own identity.
            plan_id: The plan this check is against. Must have been synced first via adcp_sync_plans,
                or this fails with PLAN_NOT_FOUND.
            tool_name: The AdCP task being checked, e.g. "get_products" or "create_media_buy".
            payload: The exact request body about to be sent for that task (BR-U2-13), not an
                approximation. For create_media_buy this mirrors the create_media_buy shape so the
                governance agent can check every category the plan governs, not just budget:
                  - "packages": [{"product_id", "budget", "pricing_option_id"}] -- budget drives the
                    budget_authority check (sum across packages).
                  - "start_time"/"end_time" (ISO 8601) -- the flight window; without them the
                    flight_window check can only report "no flight dates", never a pass.
                  - When the campaign targets specific markets, put them on the package as
                    "targeting_overlay": {"geo_countries": ["US", ...]} (or "geo_regions":
                    ["US-CA", ...]) so the geo_compliance check runs. Only include markets the user
                    actually named -- never invent a geography.
                Channel is a property of the product, not a create_media_buy field, so it is not set
                here; the seller states the booked product's channel on its own execution check.
            governance_context: A prior APPROVED response's token, when re-checking after an expired
                context (BR-U2-16). Omit on a first check.
            consultation_context: The handle from a prior "conditions" response, sent with the adjusted
                re-check so the governance agent can tie the two together. Omit on a first check.
        """
        governance = resolve_governance_agent()
        target_seller = scope([seller_id])[0]
        # The seller this check is ABOUT. AdCP gives it its own field, separate from `caller`, and the
        # spec's Buyer-side intent check table is explicit about both halves:
        #
        #   caller        "Identifies the buyer-side orchestrator making the governance check"
        #   target_agent  "Exact downstream service URL; becomes the signed token audience and stays
        #                  outside the business payload"
        #
        # This used to put the SELLER's URL in `caller` and send no `target_agent`, on the reasoning
        # that `caller` is what the governance agent signs into `aud`. The observed behaviour was real
        # -- our own agent did read the audience off `caller` -- but both sides were wrong together, so
        # each looked like evidence for the other. The SDK documents `caller` as "URL of the agent
        # making the request", and the same field is what a plan's `delegations[].agent_url` is matched
        # against, which only makes sense for the agent acting on the plan.
        #
        # `target_agent` is not in the SDK (absent from 6.6.0 and 7.0.2 -- SDK-GAP-5); it travels because
        # `CheckGovernanceRequest` is `extra="allow"`.
        target_agent = target_seller.get("agent_url", target_seller["url"])
        args: dict[str, Any] = {
            "plan_id": plan_id,
            "target_agent": target_agent,
            "tool": tool_name,
            "payload": payload,
        }
        # `caller` is required, and this agent has to be able to name itself. Refused here rather than
        # omitted so the failure says what is misconfigured, instead of arriving as a remote schema error
        # about a missing field; and never substituted with the seller's URL, which is the defect this
        # whole split exists to fix.
        this_buyer = this_buyer_agent_url()
        if not this_buyer:
            raise AdcpToolError(
                "This buyer cannot name itself: AGENTS_JSON declares no entry with "
                'kind="buyer" and origin="internal". check_governance requires `caller` to identify '
                "the buyer-side orchestrator, so the check cannot be made. Run "
                "`python3 deploy_all.py --only agents-registry` to rebuild AGENTS_JSON."
            )
        args["caller"] = this_buyer
        if governance_context:
            args["governance_context"] = governance_context
        if consultation_context:
            # Carried in its own field, not folded into `governance_context`: the two mean different
            # things and merging them would let a non-authorising handle be presented as an authorising
            # one. Neither the request nor the response model declares it (SDK-GAPS.md gap 5), so it
            # travels on `extra="allow"`.
            args["consultation_context"] = consultation_context
        result = await _call_seller_tool(governance, "check_governance", args, buyer_session_id)

        # NFR-U2-G3: record an approval so a later adcp_create_media_buy for this exact action can be
        # verified in code. Only "approved" counts -- BR-U2-15 requires "conditions" to be re-checked
        # after the caller applies the stated adjustments, so a conditions verdict here must NOT open
        # the gate for the unadjusted payload.
        if (
            tool_name == "create_media_buy"
            and isinstance(result, dict)
            and result.get("verdict") == "approved"
            and result.get("governance_context")
        ):
            product_id = payload.get("packages", [{}])[0].get("product_id") if payload.get(
                "packages"
            ) else payload.get("product_id")
            budget = payload.get("packages", [{}])[0].get("budget") if payload.get(
                "packages"
            ) else payload.get("budget")
            pricing_option_id = (
                payload.get("packages", [{}])[0].get("pricing_option_id")
                if payload.get("packages")
                else payload.get("pricing_option_id")
            )
            if product_id is not None and budget is not None and pricing_option_id is not None:
                key = (plan_id, product_id, float(budget), pricing_option_id)
                _approved_checks[key] = (result.get("check_id", ""), result["governance_context"])

        return result

    @tool
    async def adcp_create_media_buy(
        seller_id: str,
        plan_id: str,
        governance_context: str,
        product_id: str,
        budget: float,
        pricing_option_id: str,
        brand_domain: str,
        operator: str,
        flight_start: str,
        flight_end: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Book a media buy against a discovered product (the Book step).

        Calls the named SELLER's create_media_buy task -- a single seller, never a fan-out, since a
        media buy is booked at one specific seller. This is a real spend commit: call
        adcp_check_governance first with tool_name="create_media_buy" and pass its governance_context
        here (BR-U2-13/BR-U2-14). This precondition is ENFORCED, not just advised (NFR-U2-G3): calling
        this without a matching prior adcp_check_governance approval for the same plan_id/product_id/
        budget/pricing_option_id returns an error without contacting the seller, and an approval whose
        governance_context has since expired is treated the same as no approval at all (NFR-U2-G4).

        All sellers configured in this project are sandbox/test accounts -- this does not spend real
        money. That is exactly why it is safe to call once governance has approved it.

        Args:
            seller_id: Which sales agent to book with (its id from the configured registry).
            plan_id: The governance plan this buy is committed against.
            governance_context: The token from the adcp_check_governance call made for this exact
                action. Required by AdCP when the account has governance_agents configured.
            product_id: The product id from a prior adcp_get_products result.
            budget: Budget for this one package, in the media buy's currency.
            pricing_option_id: The pricing_option_id from the product's get_products result.
            brand_domain: The advertiser's domain -- must match the account declared via
                adcp_sync_accounts.
            operator: The operator/agency that declared the account.
            flight_start: Campaign start as an ISO-8601 date or datetime.
            flight_end: Campaign end, same format.
            idempotency_key: Optional; a fresh UUID is generated if omitted. Supply your own only when
                deliberately retrying a specific prior attempt.
        """
        # NFR-U2-G3: enforce BR-U2-13/BR-U2-14 in code -- refuse to call the seller unless a matching
        # check_governance approval for this exact (plan_id, product_id, budget, pricing_option_id) was
        # recorded earlier in this session, and that approval's governance_context has not expired
        # (NFR-U2-G4/BR-U2-16). Keyed on the material fields, not plan_id alone, so an approval for a
        # different product/budget under the same plan cannot silently authorize this one.
        key = (plan_id, product_id, float(budget), pricing_option_id)
        entry = _approved_checks.get(key)
        if entry is None:
            return {
                "error": "no_prior_check",
                "message": (
                    f"No approved check_governance result found for plan_id={plan_id!r}, "
                    f"product_id={product_id!r}, budget={budget!r}, "
                    f"pricing_option_id={pricing_option_id!r}. Call adcp_check_governance with "
                    "tool_name=\"create_media_buy\" and this exact payload, get an \"approved\" "
                    "verdict, then retry.",
                ),
            }
        _check_id, recorded_context = entry
        if _governance_context_expired(recorded_context):
            return {
                "error": "governance_context_expired",
                "message": (
                    "The governance_context from the prior approved check_governance call for this "
                    "action has expired. Call adcp_check_governance again with the same payload before "
                    "retrying."
                ),
            }

        target = scope([seller_id])[0]
        args: dict[str, Any] = {
            "idempotency_key": idempotency_key or f"create-mb-{uuid.uuid4().hex}",
            "plan_id": plan_id,
            "governance_context": governance_context,
            "account": {"brand": {"domain": brand_domain}, "operator": operator},
            "brand": {"domain": brand_domain},
            "packages": [
                {
                    "product_id": product_id,
                    "budget": budget,
                    "pricing_option_id": pricing_option_id,
                }
            ],
            "start_time": _as_aware_iso(flight_start, end_of_day=False),
            "end_time": _as_aware_iso(flight_end, end_of_day=True),
        }
        return await _call_seller_tool(target, "create_media_buy", args, buyer_session_id)

    @tool
    async def adcp_report_plan_outcome(
        plan_id: str,
        governance_context: str,
        outcome: str,
        check_id: str | None = None,
        seller_reference: str | None = None,
        committed_budget: float | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        reporting_period_start: str | None = None,
        reporting_period_end: str | None = None,
        impressions: int | None = None,
        spend: float | None = None,
        cpm: float | None = None,
        viewability_rate: float | None = None,
        completion_rate: float | None = None,
    ) -> dict[str, Any]:
        """Report the result of a booked action back to the governance agent (the Report step).

        Closes the governance loop (BR-U2-17): the agent tracks committed budget from CONFIRMED
        outcomes, not from approved checks, so a session that checks and books but never reports leaves
        the plan showing authority it has actually spent. Call this once, right after
        adcp_create_media_buy returns.

        `outcome` is one of three values (`enums/outcome-type.json`), each with its own required
        follow-up fields:
        - "completed": the seller confirmed the buy. Supply seller_reference/committed_budget.
        - "failed": the seller did not confirm it. Supply error_code/error_message.
        - "delivery": a periodic report of delivery performance measured DURING the campaign -- not
          the booking confirmation. Supply reporting_period_start/reporting_period_end (both required
          by the seller's schema when outcome is "delivery") plus whichever of impressions/spend/cpm/
          viewability_rate/completion_rate you actually have; omit any you don't, never guess one.

        Args:
            plan_id: The plan this outcome is for.
            check_id: The check_id from the adcp_check_governance call that authorised this action.
                Required for "completed"/"failed"; not used for "delivery" (a delivery report is not
                tied to a single check).
            governance_context: The same token used on the create_media_buy call.
            outcome: "completed", "failed", or "delivery".
            seller_reference: The seller's media_buy_id, when outcome is "completed".
            committed_budget: The seller's ACTUAL confirmed amount, from its response -- never the
                originally requested budget (BR-U2-17/BR-U2-18). May differ from what was requested.
            error_code: When outcome is "failed", the seller's error code.
            error_message: When outcome is "failed", the seller's error message.
            reporting_period_start: ISO 8601 start of the delivery window this report covers. Required
                when outcome is "delivery".
            reporting_period_end: ISO 8601 end of the delivery window this report covers. Required when
                outcome is "delivery".
            impressions: Impressions delivered in the period, when outcome is "delivery".
            spend: Spend in the period, when outcome is "delivery".
            cpm: Effective CPM for the period, when outcome is "delivery".
            viewability_rate: Viewability rate (0-1), when outcome is "delivery".
            completion_rate: Video completion rate (0-1), when outcome is "delivery".
        """
        governance = resolve_governance_agent()
        args: dict[str, Any] = {
            "plan_id": plan_id,
            "idempotency_key": f"outcome-{uuid.uuid4().hex}",
            "governance_context": governance_context,
            "outcome": outcome,
        }
        if check_id:
            args["check_id"] = check_id
        if outcome == "completed":
            seller_response: dict[str, Any] = {}
            if seller_reference:
                seller_response["seller_reference"] = seller_reference
            if committed_budget is not None:
                seller_response["committed_budget"] = committed_budget
            args["seller_response"] = seller_response
        elif outcome == "failed":
            args["error"] = {"code": error_code, "message": error_message}
        elif outcome == "delivery":
            delivery: dict[str, Any] = {
                "reporting_period": {
                    "start": reporting_period_start,
                    "end": reporting_period_end,
                }
            }
            if impressions is not None:
                delivery["impressions"] = impressions
            if spend is not None:
                delivery["spend"] = spend
            if cpm is not None:
                delivery["cpm"] = cpm
            if viewability_rate is not None:
                delivery["viewability_rate"] = viewability_rate
            if completion_rate is not None:
                delivery["completion_rate"] = completion_rate
            args["delivery"] = delivery
        return await _call_seller_tool(governance, "report_plan_outcome", args, buyer_session_id)

    @tool
    async def adcp_get_plan_audit_logs(
        plan_ids: list[str],
        include_entries: bool = True,
    ) -> dict[str, Any]:
        """Read back the governance agent's audit trail for one or more plans (the Report/Activate
        read-back step).

        Calls the GOVERNANCE agent's get_plan_audit_logs task -- a read, not a seller call. Returns
        each plan's real recorded checks and outcomes, including "outcome"-type entries for every
        adcp_report_plan_outcome call previously made (completed/failed/delivery), when
        include_entries is true.

        Args:
            plan_id: Plan ids to retrieve. Must have been synced via adcp_sync_plans first.
            include_entries: Include the full per-plan audit trail (checks + outcomes), not just the
                summary counts. Defaults to true -- the summary alone rarely answers what a caller
                actually wants to know.
        """
        governance = resolve_governance_agent()
        args: dict[str, Any] = {
            "plan_ids": plan_ids,
            "include_entries": include_entries,
        }
        return await _call_seller_tool(governance, "get_plan_audit_logs", args, buyer_session_id)

    @tool
    async def adcp_get_media_buy_delivery(
        seller_id: str,
        media_buy_id: str,
    ) -> dict[str, Any]:
        """Get in-flight delivery metrics for a booked media buy (the Activate step).

        Calls the named seller's get_media_buy_delivery task -- a single seller, matching where the
        buy was booked.

        Args:
            seller_id: The sales agent the buy was booked with.
            media_buy_id: The id returned by adcp_create_media_buy.
        """
        target = scope([seller_id])[0]
        args = {"media_buy_ids": [media_buy_id]}
        return await _call_seller_tool(target, "get_media_buy_delivery", args, buyer_session_id)

    @tool
    async def adcp_get_creative_features(
        format_id: str,
        format_agent_url: str,
        format_width: int,
        format_height: int,
        asset_url: str,
        asset_width: int,
        asset_height: int,
        feature_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Submit a creative for evaluation by the configured governance agent (the Creative scan
        step, R22).

        Calls the GOVERNANCE agent's get_creative_features task -- a `Creative governance agent`
        capability per AdCP's own task reference. Only ever call this with a REAL, fetchable asset URL.
        Two of the reference seller's formats have one published (see deploy_creative_fixtures.py):

        - `display_300x250` -> `clean_banner.png`, 300x250
        - `display_728x90`  -> `clean_leaderboard.png`, 728x90

        Both live under `{BUYER_UI_ORIGIN}/.well-known/creatives/`. Pass the format's OWN required
        width/height as `format_width`/`format_height` and the asset's real size as
        `asset_width`/`asset_height` -- the evaluator compares measured pixels against the format's
        requirement, so passing the asset's size as both makes `dimension_conformance` unfalsifiable.

        The seller's other formats (`audio_30s`, `video_16x9_30s`, `rewarded_video_15s`) have no asset and
        could not be evaluated by this evaluator anyway: all three of its features are image measurements
        (pixel dimensions, WCAG contrast, OCR). Do not submit an image against an audio or video format to
        make the call succeed -- report that no asset exists for that format.

        If no governance agent is configured, or the configured one reports this task unsupported, that is
        the honest answer: do not simulate a scan result or invent a verdict -- report to the user that no
        evaluator is available.

        The response is terminal: `completed` carrying either `results` (the evaluation) or `errors`
        (why it could not be done). Those are the only two shapes this task's response union admits, so
        there is no in-progress reply to retry against.

        This used to say a `working`/`submitted` status meant "call again shortly". That was wrong on
        AdCP's own terms: per the 30-second rule, `working` is an out-of-band progress signal sent while
        the server keeps processing on a held connection and the spec says explicitly not to poll it,
        while `submitted` is for work blocked outside the server's control and is polled by `task_id`,
        not by repeating the original call. If a future evaluator does return `submitted`, report that
        honestly rather than re-issuing this tool.

        Args:
            format_id: The creative format's own id (from a prior adcp_list_creative_formats result's
                format_id.id).
            format_agent_url: The format's defining agent_url (from that same format_id object) -- the
                manifest's format_id is the pair {agent_url, id}, not id alone.
            format_width: The FORMAT's own declared width in pixels (from that prior
                adcp_list_creative_formats result's dimensions.width) -- what a conformant asset for
                this format is REQUIRED to be, used by the evaluator to check the submitted image
                against the format's real requirement, not against whatever the asset itself claims.
            format_height: The format's own declared height in pixels, same source.
            asset_url: A real, fetchable HTTPS URL to the image asset being evaluated.
            asset_width: The asset's own claimed width in pixels (manifest metadata -- what the
                submitter says this file is, separate from format_width/format_height).
            asset_height: The asset's own claimed height in pixels, same source.
            feature_ids: Optional filter to specific feature ids. Omit to get every feature the
                evaluator computes.
        """
        governance = resolve_governance_agent()
        args: dict[str, Any] = {
            "creative_manifest": {
                "format_id": {"agent_url": format_agent_url, "id": format_id},
                "assets": {
                    "main_image": {
                        "asset_type": "image",
                        "url": asset_url,
                        "width": asset_width,
                        "height": asset_height,
                    }
                },
                # Not part of AdCP's own creative-manifest schema (which validates under
                # additionalProperties rules per-object) -- carried in `ext` per every AdCP schema's
                # own escape hatch, so the evaluator has the format's real requirement to check
                # against without this project's fixture-scale governance agent needing its own
                # separate, duplicated copy of reference-seller's format catalog.
                "ext": {"format_dimensions": {"width": format_width, "height": format_height}},
            }
        }
        if feature_ids:
            args["feature_ids"] = feature_ids
        return await _call_seller_tool(governance, "get_creative_features", args, buyer_session_id)

    @tool
    async def adcp_get_media_buys(
        seller_id: str,
        media_buy_ids: list[str] | None = None,
        status_filter: str | list[str] | None = None,
        include_snapshot: bool = False,
    ) -> dict[str, Any]:
        """Get operational status, creative-approval state, and near-real-time delivery snapshots for
        media buys with a seller (a reporting task, distinct from adcp_get_media_buy_delivery's
        billing-grade delivery figures -- per AdCP's own task reference, "treat get_media_buy_delivery
        as the authoritative, billing-grade source; use get_media_buys snapshots for operational
        monitoring only").

        Calls the named seller's get_media_buys task -- a single seller, same scoping as
        adcp_get_media_buy_delivery. A read, not a spend-commit action -- no governance gate applies.

        Args:
            seller_id: The sales agent to query.
            media_buy_ids: Specific media buy ids to retrieve. When omitted, returns a paginated set
                matching status_filter (defaults to "active" media buys on the seller's side).
            status_filter: Filter by status (a single status or a list). Ignored when media_buy_ids is
                given.
            include_snapshot: When true, ask the seller for a near-real-time delivery snapshot per
                package alongside the operational status. Defaults to false.
        """
        target = scope([seller_id])[0]
        args: dict[str, Any] = {"include_snapshot": include_snapshot}
        if media_buy_ids:
            args["media_buy_ids"] = media_buy_ids
        if status_filter:
            args["status_filter"] = status_filter
        return await _call_seller_tool(target, "get_media_buys", args, buyer_session_id)

    @tool
    async def adcp_get_signals(
        seller_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """List audience signals a sales agent has available, with their coverage.

        Calls the AdCP get_signals task. Coverage figures describe how much of a seller's addressable
        audience a segment reaches -- report this as COVERAGE, never as a brief-to-audience "match"
        score, since this task does not compute the latter (R28).

        Args:
            seller_ids: Restrict the query to these sales agent ids. Omit to ask every configured
                agent.
        """
        return await _call_all_sellers(scope(seller_ids), "get_signals", {}, buyer_session_id)

    @tool
    async def adcp_get_products(
        brief: str,
        # A Literal, so the tool schema the model sees carries the three allowed values and a fourth is
        # not emittable. It used to be a bare `str` whose docstring named "browse" — a value AdCP does
        # not define — so the model dutifully sent it and Gotham answered
        # `VALIDATION_ERROR[/buying_mode]: value not in allowed enum (3 options)`.
        #
        # Restating the SDK's enum is what `use-the-adcp-sdk.md` calls a second source of truth, and it
        # is a deliberate exception, not an oversight: `adcp` CANNOT be installed in this runtime.
        # adcp==6.6.0 needs a2a-sdk>=1.0.1,<1.0.2 and strands-agents[a2a]==1.48.0 needs
        # a2a-sdk>=0.3.0,<0.4.0, so the image build fails outright (see requirements.txt).
        #
        # `test_buying_mode.py` closes the gap the usual way round: it imports the real `BuyingMode`
        # from the SDK — available in the dev venv, just not in the image — and asserts this Literal
        # equals it. So an AdCP change fails a test rather than drifting silently.
        buying_mode: BuyingModeLiteral = "brief",
        brand_domain: str | None = None,
        seller_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Discover advertising inventory from AdCP sales agents.

        Calls the AdCP get_products task against the chosen sales agents in
        parallel (all of them by default) and returns one result per seller.
        Use this when the user wants to find ad inventory, placements, or
        products matching a description (audience, geography, ad format, app
        category, etc). Compare what the sellers return and recommend the
        inventory that actually fits the brief; say which seller each product
        came from.

        Args:
            brief: Natural language description of the desired inventory, e.g.
                "rewarded video for casual games in the US" or "CTV news inventory".
            buying_mode: AdCP buying mode, one of exactly three values.
                "brief" (default): the publisher curates product
                recommendations from the brief. "wholesale": ask for raw
                product inventory to apply your own audiences — the brief is
                NOT sent in this mode and the seller omits proposals. "refine":
                iterate on the products and proposals from a previous
                get_products response.
            brand_domain: The advertiser's domain (e.g. "nike.com"), used to
                populate AdCP's required `brand` field so the seller can run
                brand-safety/policy checks against the actual advertiser. Ask
                the user for this if they haven't stated it and it isn't
                already known from earlier in the conversation. Falls back to
                BUYER_BRAND_DOMAIN if configured and the user hasn't given one.
            seller_ids: Restrict the query to these sales agent ids (the ids are
                listed in the system prompt). Omit to ask every configured
                agent, which is the right default when the user wants to see
                the market. Set it when the user names a seller, when only one
                seller is relevant to the brief, or when re-querying a single
                seller to refine what it returned.
        """
        # Checked at runtime as well as in the schema: the Literal constrains what a well-behaved
        # client emits, and this catches anything that arrives regardless, with the allowed set in the
        # message so the model can correct itself in one turn rather than relaying a remote schema error.
        mode = str(buying_mode)
        if mode not in BUYING_MODES:
            return {
                "error": "invalid_buying_mode",
                "message": f"buying_mode must be one of {list(BUYING_MODES)}, got {buying_mode!r}.",
            }

        args: dict[str, Any] = {"buying_mode": mode}
        # AdCP on `wholesale`: "buyer requests raw product inventory to apply their own audiences --
        # brief must not be provided, and proposals are omitted." That MUST NOT lives in the field's
        # prose, and the SDK does NOT enforce it — `GetProductsRequest(buying_mode="wholesale",
        # brief=...)` constructs happily, so a seller validating against the spec rather than the
        # model is the one that would reject it. Dropped here so this buyer is conformant either way.
        if mode != "wholesale":
            args["brief"] = brief
        domain = brand_domain or os.environ.get("BUYER_BRAND_DOMAIN")
        if domain:
            args["brand"] = {"domain": domain}
        return await _call_all_sellers(
            scope(seller_ids), "get_products", args, buyer_session_id
        )

    @tool
    async def adcp_get_capabilities(seller_ids: list[str] | None = None) -> dict[str, Any]:
        """Get connected sales agents' AdCP capabilities.

        Calls the AdCP get_adcp_capabilities task against the chosen sales
        agents (all of them by default) and returns one result per seller. Use
        this to check what AdCP versions, tools, specialisms and features each
        seller supports, or to confirm the connections/auth are working.

        Args:
            seller_ids: Restrict the query to these sales agent ids. Omit to ask
                every configured agent.
        """
        return await _call_all_sellers(
            scope(seller_ids), "get_adcp_capabilities", {}, buyer_session_id
        )

    @tool
    async def adcp_list_creative_formats(
        ad_type: str | None = None, seller_ids: list[str] | None = None
    ) -> dict[str, Any]:
        """List creative formats supported by sales agents, optionally filtered by ad type.

        Calls the AdCP list_creative_formats tool.

        Args:
            ad_type: Optional filter by ad format, e.g. "banner", "interstitial",
                "rewarded", "native", "video". Omit to list all formats.
            seller_ids: Restrict the query to these sales agent ids. Omit to ask
                every configured agent.
        """
        args: dict[str, Any] = {}
        if ad_type:
            args["ad_type"] = ad_type
        return await _call_all_sellers(
            scope(seller_ids), "list_creative_formats", args, buyer_session_id
        )

    return [
        # This is REGISTRATION order, which is not call order: the model calls whichever tool the
        # conversation needs. Only one real dependency is encoded here -- sync_accounts before
        # sync_governance, because sync_governance against an account the seller has never heard of
        # fails with ACCOUNT_NOT_FOUND.
        #
        # Discovery does NOT depend on either. `get_products` takes a brand and a brief, not an
        # account, and consults no governance agent, so a session normally discovers inventory first
        # and binds governance once there is a commitment to make. An earlier comment here claimed the
        # reverse ("then discover inventory against it"); it over-read AdCP's requirement, which is
        # that governance precedes a seller ACTING on a plan, not a seller being read from.
        adcp_sync_accounts,
        adcp_sync_governance,
        adcp_sync_plans,
        adcp_check_governance,
        adcp_get_products,
        adcp_get_capabilities,
        adcp_list_creative_formats,
        adcp_get_signals,
        adcp_create_media_buy,
        adcp_report_plan_outcome,
        adcp_get_plan_audit_logs,
        adcp_get_media_buy_delivery,
        adcp_get_media_buys,
        adcp_get_creative_features,
    ]
