"""
Seller-side session recording: makes a seller agent's tool calls visible in
the chat UI's Sessions view, the same way the buyer agent's already are.

Where this file lives, and why
------------------------------
This is seller code, but it lives beside session_records.py (its only
dependency) because that module has to be the single definition of the item
shape, and each AgentCore build context is isolated — a seller container
cannot import from outside its own project root. `deploy_all.py`'s
`vendor-session-module` step copies both files into each seller package
immediately before that seller deploys, so a stale copy cannot ship. The
copies are generated build artifacts: gitignored, never hand-edited.

How it hooks in
---------------
Via the AdCP SDK's `SkillMiddleware` seam, which `serve(middleware=[...])`
accepts and which wraps every skill dispatch on both the MCP and A2A
transports — so one wiring covers a seller's current MCP runtime and the
planned A2A one, with no per-handler edits.

Deliberately NOT the SDK's own `make_audit_middleware`: its `AuditEvent`
carries the operation name, success flag and timing, but not the request
params or the result. The UI's tool cards show the actual input and output,
so this records from a middleware that has both.

Best-effort, always
-------------------
Every write is wrapped: a missing table, a missing IAM permission or a
DynamoDB stall must never change what a seller returns to a buyer. Recording
is observability, not part of the AdCP contract — the same trade-off
reasoning_hooks.py::_safe() already makes on the buyer side.

What a "session" means here — and the open question
---------------------------------------------------
Grouping a seller's tool calls into one conversation requires the buyer to
send a stable identifier the seller can see. The buyer now derives one and
sends it as `contextId` + the AgentCore session header on A2A, and as
`Mcp-Session-Id` on MCP (adcp_tools.derive_seller_session_id).

For MCP that propagation is **not confirmed to arrive**: AWS's stateless-MCP
contract says the platform generates `Mcp-Session-Id` itself, and a buyer-side
probe saw the platform reject our value on session teardown (404). So this
module records whichever session id it actually observes on the request and
labels where it came from (`session_id_source`), rather than assuming. If the
buyer's id arrives, one conversation produces one session with many steps; if
it doesn't, each call produces its own single-step session. Both are accurate
records of what happened — and the resulting data is what settles the
question (see the multi-agent-chat plan's open question, option A).
"""

import logging
import time
from contextvars import ContextVar
from typing import Any, Awaitable, Callable

import session_records

logger = logging.getLogger(__name__)

# Headers a buyer's correlation id could arrive on, most trustworthy first.
#
# The buyer's own header is preferred because it's the only one that reliably
# means "this buyer conversation": AgentCore forwards a header into a container
# only if the agent allowlists it (both sellers allowlist this one), and
# Mcp-Session-Id is platform-managed in stateless MCP — when present it
# identifies a microVM session, not a conversation. The platform headers are
# kept as fallbacks so a caller that sends only those still gets grouped as
# well as is possible, with `session_id_source` recording which one was used.
_BUYER_SESSION_HEADER = "x-adcp-buyer-session-id"
_A2A_SESSION_HEADER = "x-amzn-bedrock-agentcore-runtime-session-id"
_MCP_SESSION_HEADER = "mcp-session-id"

# Set by observe_request() (the context factory), read by the middleware in
# the same asyncio task — the SDK guarantees call_next() runs in the task that
# invoked it, so a ContextVar is the supported way to pass request-scoped
# facts the middleware signature doesn't carry.
_request_session: ContextVar[tuple[str, str] | None] = ContextVar(
    "adcp_seller_request_session", default=None
)


def _headers_of(request_context: Any) -> dict[str, str]:
    headers = getattr(request_context, "headers", None)
    if headers is None:
        return {}
    try:
        return {str(k).lower(): str(v) for k, v in dict(headers).items()}
    except Exception:  # noqa: BLE001 - a header mapping we don't understand is not fatal
        return {}


def resolve_request_session(request_context: Any) -> tuple[str | None, str]:
    """(session_id, source) for the request currently being handled.

    Returns the id the buyer actually propagated, and which header carried it,
    so a record never implies a grouping that didn't happen. `source` is
    "none" when no correlation id arrived at all — in which case the caller
    generates a per-call id and says so.
    """
    headers = _headers_of(request_context)
    for header, source in (
        (_BUYER_SESSION_HEADER, "buyer-session-header"),
        (_A2A_SESSION_HEADER, "agentcore-session-header"),
        (_MCP_SESSION_HEADER, "mcp-session-id (microVM session, not a conversation)"),
    ):
        value = headers.get(header, "").strip()
        if value:
            return value, source
    return None, "none"


def observe_request(request_context: Any) -> None:
    """Capture this request's session id. Call from the seller's context factory.

    Separate from the middleware because only the context factory is handed
    the transport's request metadata (and therefore its headers).
    """
    session_id, source = resolve_request_session(request_context)
    if session_id is None:
        # No correlation id from the buyer: this call stands alone. A
        # generated id keeps one call to one session
        # rather than lumping unrelated calls under a shared placeholder.
        session_id = session_records.new_session_id("adcp-seller-uncorrelated")
        source = "generated (buyer sent none)"
    _request_session.set((session_id, source))


def make_session_recording_middleware(
    *,
    agent_id: str,
    agent_name: str,
) -> Callable[[str, dict[str, Any], Any, Callable[[], Awaitable[Any]]], Awaitable[Any]]:
    """A SkillMiddleware that records each dispatch as session steps.

    One dispatch produces: `incoming_request`, `tool_call` (with the real
    params), then `tool_result` (with the real result and duration, or the real
    error). Install it OUTERMOST so a call rejected by a short-circuiting
    middleware still appears — per the SDK's composition guidance.
    """

    async def middleware(
        skill_name: str,
        params: dict[str, Any],
        context: Any,
        call_next: Callable[[], Awaitable[Any]],
    ) -> Any:
        observed = _request_session.get()
        if observed is None:
            # The context factory didn't run (or didn't observe) — record
            # against a standalone id rather than dropping the call.
            session_id = session_records.new_session_id("adcp-seller-uncorrelated")
            source = "generated (no observed request)"
        else:
            session_id, source = observed

        invoker = getattr(context, "caller_identity", None) or "Unknown buyer"
        call_id = f"{skill_name}-{int(time.time() * 1000)}"

        _safe(
            session_records.start_turn,
            session_id,
            invoker=invoker,
            mode="agent",
            seller_agent_id=agent_id,
            request_preview=f"{skill_name}",
            agent_id=agent_id,
            agent_name=agent_name,
        )
        _safe(
            session_records.record_step,
            session_id,
            "incoming_request",
            {"from": invoker, "text": skill_name, "sessionIdSource": source},
        )
        _safe(
            session_records.record_step,
            session_id,
            "tool_call",
            {"toolName": skill_name, "input": _plain(params), "callId": call_id},
        )

        started = time.monotonic()
        try:
            result = await call_next()
        except Exception as exc:  # noqa: BLE001 - recorded, then re-raised unchanged
            duration_ms = int((time.monotonic() - started) * 1000)
            message = f"{type(exc).__name__}: {exc}"
            _safe(
                session_records.record_step,
                session_id,
                "tool_result",
                # toolName is repeated on the result so a reader of a result
                # step alone can name the skill instead of only holding an
                # opaque callId.
                {
                    "toolName": skill_name,
                    "callId": call_id,
                    "status": "error",
                    "output": message,
                    "durationMs": duration_ms,
                },
            )
            _safe(session_records.error_turn, session_id, message)
            # Re-raise: swallowing here would serve a fake success for a
            # failed mutation, which the SDK's own middleware docs call out
            # as almost always wrong.
            raise

        duration_ms = int((time.monotonic() - started) * 1000)
        _safe(
            session_records.record_step,
            session_id,
            "tool_result",
            {
                "toolName": skill_name,
                "callId": call_id,
                "status": "success",
                "output": _plain(result),
                "durationMs": duration_ms,
            },
        )
        _safe(
            session_records.record_step,
            session_id,
            "response",
            {"to": invoker, "text": f"{skill_name} completed in {duration_ms}ms"},
        )
        _safe(session_records.complete_turn, session_id)
        return result

    return middleware


def _plain(value: Any) -> Any:
    """Best-effort conversion of a handler's params/result into DynamoDB-safe
    plain data. Pydantic models (which the AdCP SDK returns) become dicts;
    anything else that won't serialise is recorded as its string form rather
    than causing the write — and therefore the whole record — to be lost.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            return dump(mode="json")
        except Exception:  # noqa: BLE001
            return str(value)
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return str(value)


def _safe(fn, *args, **kwargs) -> None:
    """Run a session_records write, swallowing (and logging) any failure.

    A seller's AdCP response must never depend on the observability trail —
    see this module's docstring.
    """
    try:
        fn(*args, **kwargs)
    except session_records.SessionRecordError as exc:
        logger.warning("seller session recording skipped: %s", exc)
    except Exception:  # noqa: BLE001 - telemetry must never break the seller
        logger.exception("seller session recording failed unexpectedly")
