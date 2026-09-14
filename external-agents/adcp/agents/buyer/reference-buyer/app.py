"""
AgentCore Runtime HTTP server for the AdCP buyer agent.

Exposes:
- POST /invocations  - also carries small JSON "actions" the UI needs but
                        that AgentCore Runtime gives no separate route for:
                        list_sessions / get_session_steps (the Sessions
                        viewer) and whoami / list_users / create_user
                        (Cognito user administration, restricted to members
                        of the `admin` group — see user_admin.py).
- POST /invocations  - AgentCore Runtime's standard entrypoint. Streams
                        Server-Sent Events as the agent reasons and calls
                        AdCP tools against whichever seller agent the
                        request selected (payload's seller_agent_id, see
                        seller_agents.py). Requires a valid Cognito access
                        token in the Authorization header. Used when this
                        container is invoked as an AgentCore Runtime
                        (deployed or `python app.py` locally acting as its
                        own local runtime); NOT used by the browser UI
                        below.
- GET  /ping          - AgentCore Runtime health check (added automatically
                        by BedrockAgentCoreApp). Not auth-gated.
- GET  /              - the chat UI (static/index.html), includes a login
                        screen backed by real Cognito auth and a seller
                        agent dropdown. The UI calls the DEPLOYED AgentCore
                        Runtime's invoke endpoint directly from the browser
                        (bedrock-agentcore.{region}.amazonaws.com/runtimes/
                        {arn}/invocations), not this process's own
                        /invocations. This process is only a static file
                        host for the UI in that flow.
- GET  /config        - public non-secret config the browser needs: Cognito
                        pool/client IDs (to call Cognito directly), the
                        deployed runtime's ARN/region (to call it directly),
                        and the seller agent registry (id/name/url per
                        entry, no auth tokens) for the dropdown.
- static assets under /static/*

On the deployed AgentCore Runtime, inbound auth is enforced by the platform
(see deploy_configure.py's authorizer_configuration: a Cognito JWT
authorizer) — invoke_agent_runtime calls without a valid token never reach
the agent code. This file's own verify_bearer_token check (auth.py) exists
so that if this same /invocations endpoint is ever invoked directly as a
local AgentCore Runtime (bypassing the platform-level gate that only exists
on the deployed instance), it enforces the identical rule itself.

Run locally with:
    python app.py
Then open http://localhost:8080/ in a browser and sign in. The UI will
talk to the deployed runtime in AGENT_RUNTIME_ARN, not to this process.
"""


from aws_region import region
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from bedrock_agentcore.runtime import BedrockAgentCoreApp, RequestContext
from starlette.responses import FileResponse, JSONResponse
from starlette.staticfiles import StaticFiles

import agent_registry_store
import session_store
import user_admin
from adcp_tools import DIRECT_CALL_TOOL_NAMES, call_seller_tool
from agent import build_agent
from agents_registry import (
    BUYER_HTTP_AGENT_ID,
    BUYER_HTTP_AGENT_NAME,
    AgentRegistryError,
    get_default_agent_id,
    list_agents_public,
    resolve_agent,
)
from auth import AuthError, verify_bearer_token
from user_admin import NotAuthorizedError, UserAdminError
from seller_agents import (
    SellerAgentError,
    get_default_seller_agent_id,
    list_seller_agents_public,
    resolve_seller_agent,
    validate_seller_entry,
)
from governance_agents import (
    GovernanceAgentError,
    list_governance_agents_public,
    resolve_governance_agent,
    validate_governance_entry,
)
from session_store import SessionStoreError

app = BedrockAgentCoreApp()

STATIC_DIR = Path(__file__).parent / "static"


def _has_payload_errors(result: Any) -> bool:
    """True when a tool result carries a fatal entry in AdCP's payload `errors[]` array.

    The canonical error layer. `adcp_error` on the envelope is the extractable companion signal, and an
    agent may populate either or both -- so a reader watching only the envelope misses a failure from an
    agent that used the array, which is what AdCP calls the normative shape.

    A future `severity: "warning"` entry is NOT fatal: the two-layer model reserves the payload array
    alone for non-fatal warnings. `core/error.json` in 3.1 has no `severity` field yet (checked against
    the installed SDK: `Error.model_fields` is code/details/field/issues/message/recovery/retry_after/
    sdk_id/source/suggestion), so today every entry counts -- but honouring the key if it appears means
    an SDK bump does not silently start reporting warnings as failures.

    Fan-out results are also checked: a per-seller entry can carry its own `errors[]` while the outer
    response looks clean.
    """
    if not isinstance(result, dict):
        return False

    def _fatal(errors: Any) -> bool:
        if not isinstance(errors, list):
            return False
        return any(
            isinstance(e, dict) and e.get("severity") != "warning" and e.get("code")
            for e in errors
        )

    if _fatal(result.get("errors")):
        return True
    # `{task, results: [{seller_id, response: {...}}]}` -- the shape every seller fan-out uses.
    for entry in result.get("results") or []:
        if not isinstance(entry, dict):
            continue
        if _fatal(entry.get("errors")):
            return True
        response = entry.get("response")
        if isinstance(response, dict) and _fatal(response.get("errors")):
            return True
    return False


def _filter_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Reduce a raw Strands event into a compact shape the UI understands.

    Only forwards: text deltas, tool-call start, tool-result, and the final
    result. Everything else (raw model deltas, lifecycle flags, reasoning
    internals) is dropped to keep the wire payload small.
    """
    if "data" in event and event.get("data"):
        return {"type": "text", "data": event["data"]}

    if "current_tool_use" in event:
        tool_use = event["current_tool_use"] or {}
        name = tool_use.get("name")
        tool_input = tool_use.get("input")
        if name:
            return {"type": "tool_call", "name": name, "input": tool_input}

    if "message" in event:
        message = event["message"]
        if isinstance(message, dict) and message.get("role") == "user":
            for block in message.get("content", []):
                if isinstance(block, dict) and "toolResult" in block:
                    tool_result = block["toolResult"]
                    content = tool_result.get("content", [])
                    text = ""
                    for c in content:
                        if isinstance(c, dict) and "text" in c:
                            text = c["text"]
                            break
                    parsed: Any = text
                    try:
                        parsed = json.loads(text)
                    except (json.JSONDecodeError, TypeError):
                        pass
                    return {
                        "type": "tool_result",
                        "status": tool_result.get("status"),
                        "result": parsed,
                    }

    if "result" in event:
        result = event["result"]
        return {"type": "done", "stop_reason": str(getattr(result, "stop_reason", ""))}

    return None


@app.entrypoint
async def invoke(payload: dict[str, Any], context: RequestContext):
    """AgentCore Runtime entrypoint. Streams filtered events as SSE.

    Two modes, selected by payload["mode"] ("agent" if omitted, preserving
    the original contract for existing callers):

    - "agent" (default): the original behavior — an LLM-driven Strands
      Agent (agent.py) decides which AdCP tools to call. Every step of its
      reasoning is also recorded to DynamoDB (session_store.py, via
      agent.py's ReasoningRecorder hook) so it shows up in the UI's
      Sessions viewer, live or on replay.
    - "direct": a thin client, no LLM in the loop. payload["tool_name"]
      (one of adcp_tools.DIRECT_CALL_TOOL_NAMES) is called directly against
      the selected seller agent with payload["tool_input"], and the raw
      result is streamed back — this is the pre-existing "just call the
      endpoint" behavior this project started with, kept available as an
      explicit mode alongside the Buyer Agent rather than replaced by it.

    Authentication: the deployed runtime rejects unauthenticated calls at
    the platform level (Cognito JWT authorizer). This process verifies the
    same Cognito token independently so the local dev server enforces the
    identical rule — a request with no token, a bad signature, an expired
    token, or a token from a different app client is rejected here too,
    not just on the deployed instance.

    The session ID comes from AgentCore Runtime's own runtimeSessionId (the
    X-Amzn-Bedrock-AgentCore-Runtime-Session-Id header, exposed here as
    context.session_id) - it's the caller-supplied ID that ties multiple
    /invocations calls together into one conversation, and AgentCore Runtime
    enforces it be at least 33 characters. We reuse that same ID as the key
    for the Strands S3SessionManager and for reasoning-step recording, so a
    real ongoing conversation is persisted and restored across calls, not
    just within a single call.
    """
    headers = context.request_headers or {}
    auth_header = headers.get("Authorization") or headers.get("authorization")
    try:
        claims = verify_bearer_token(auth_header)
    except AuthError as exc:
        yield {"type": "error", "message": f"401 Unauthorized: {exc.message}"}
        return

    # Read-only queries against the reasoning-session store. Handled here
    # (rather than as separate Starlette routes) because AgentCore Runtime
    # only exposes /invocations and /ping to callers of the *deployed*
    # runtime — a plain app.add_route("/sessions", ...) would work against
    # this local dev server but be unreachable once deployed. Routing these
    # through the same authenticated entrypoint keeps one auth story and
    # needs no separate credential plumbing for the browser (e.g. a Cognito
    # Identity Pool for direct DynamoDB access) — see R1/R3's polling need
    # in pick_up_external_invocation.md.
    action = payload.get("action")
    if action == "list_sessions":
        async for event in _handle_list_sessions(payload):
            yield event
        return
    if action == "get_session_steps":
        async for event in _handle_get_session_steps(payload):
            yield event
        return
    if action == "list_plans":
        async for event in _handle_list_plans(payload):
            yield event
        return
    if action == "get_plan_steps":
        async for event in _handle_get_plan_steps(payload):
            yield event
        return

    # Cognito user administration, restricted to members of the `admin`
    # group. The check is on the *verified* token's cognito:groups claim
    # (user_admin.require_admin) — the UI hiding its admin panel from
    # non-admins is cosmetic, this is the actual boundary.
    if action == "whoami":
        async for event in _handle_whoami(claims):
            yield event
        return
    if action == "list_users":
        async for event in _handle_list_users(payload, claims):
            yield event
        return
    if action == "create_user":
        async for event in _handle_create_user(payload, claims):
            yield event
        return

    # Agent registration administration (admin group only). Lets an admin edit the resources that let
    # the buyer reach specific sellers and governance agents at runtime. These actions ONLY touch the
    # connection registry entries (url/transport/auth); they never touch the AdCP call/response path.
    if action == "list_agent_registrations":
        async for event in _handle_list_agent_registrations(payload, claims):
            yield event
        return
    if action == "upsert_agent_registration":
        async for event in _handle_upsert_agent_registration(payload, claims):
            yield event
        return
    if action == "delete_agent_registration":
        async for event in _handle_delete_agent_registration(payload, claims):
            yield event
        return
    if action == "hide_agent_registration":
        async for event in _handle_hide_agent_registration(payload, claims):
            yield event
        return
    if action == "unhide_agent_registration":
        async for event in _handle_unhide_agent_registration(payload, claims):
            yield event
        return
    if action == "test_agent_connection":
        async for event in _handle_test_agent_connection(payload, claims):
            yield event
        return

    # Relay a chat turn to an A2A agent whose credential must not reach a
    # browser (agents_registry.requires_proxy) — e.g. a remote agent behind a
    # static bearer token. Agents authenticated with the signed-in user's own
    # Cognito token are called straight from the browser instead.
    if action == "a2a_chat":
        async for event in _handle_a2a_chat(payload, context):
            yield event
        return

    mode = payload.get("mode", "agent")
    session_id = context.session_id
    seller_agent_id = payload.get("seller_agent_id")
    invoker = payload.get("invoker") or "Chat UI"

    if mode == "direct":
        async for event in _invoke_direct(payload, session_id, seller_agent_id, invoker):
            yield event
        return

    prompt = payload.get("prompt")
    if not prompt:
        yield {"type": "error", "message": "Missing 'prompt' in request payload."}
        return

    if session_id:
        try:
            session_store.start_turn(
                session_id,
                invoker=invoker,
                mode="agent",
                seller_agent_id=seller_agent_id or "",
                request_preview=prompt,
                agent_id=BUYER_HTTP_AGENT_ID,
                agent_name=BUYER_HTTP_AGENT_NAME,
            )
            session_store.record_step(session_id, "incoming_request", {"from": invoker, "text": prompt})
        except SessionStoreError:
            pass  # best-effort; see reasoning_hooks.py's _safe()

    try:
        agent = build_agent(session_id=session_id, seller_agent_id=seller_agent_id, invoker=invoker)
    except ValueError as exc:
        yield {"type": "error", "message": str(exc)}
        return
    except SellerAgentError as exc:
        yield {"type": "error", "message": str(exc)}
        return

    try:
        async for event in agent.stream_async(prompt):
            filtered = _filter_event(event)
            if filtered:
                yield filtered
    except Exception as exc:  # noqa: BLE001 - surface the real error to the UI
        if session_id:
            try:
                session_store.error_turn(session_id, f"{type(exc).__name__}: {exc}")
            except SessionStoreError:
                pass
        yield {"type": "error", "message": f"{type(exc).__name__}: {exc}"}


async def _invoke_direct(payload: dict[str, Any], session_id: str | None, seller_agent_id: str | None, invoker: str):
    """Thin-client mode: call one AdCP tool directly against the selected
    seller agent, no LLM/agent reasoning involved. Same real network call
    every Buyer Agent tool makes (adcp_tools.call_seller_tool) — this mode
    only skips the model deciding which tool/arguments to use.
    """
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input") or {}

    if tool_name not in DIRECT_CALL_TOOL_NAMES:
        yield {
            "type": "error",
            "message": f"mode=direct requires tool_name to be one of {DIRECT_CALL_TOOL_NAMES}, got {tool_name!r}.",
        }
        return

    if session_id:
        try:
            session_store.start_turn(
                session_id,
                invoker=invoker,
                mode="direct",
                seller_agent_id=seller_agent_id or "",
                request_preview=f"{tool_name}({json.dumps(tool_input)})",
                agent_id=BUYER_HTTP_AGENT_ID,
                agent_name=BUYER_HTTP_AGENT_NAME,
            )
            session_store.record_step(
                session_id, "incoming_request", {"from": invoker, "text": f"Direct call: {tool_name}"}
            )
        except SessionStoreError:
            pass

    try:
        seller_agent = resolve_seller_agent(seller_agent_id)
    except SellerAgentError as exc:
        yield {"type": "error", "message": str(exc)}
        return

    call_id = f"direct-{tool_name}"
    if session_id:
        try:
            session_store.record_step(session_id, "tool_call", {"toolName": tool_name, "input": tool_input, "callId": call_id})
        except SessionStoreError:
            pass

    yield {"type": "tool_call", "name": tool_name, "input": tool_input}

    started = time.monotonic()
    try:
        result = await call_seller_tool(seller_agent, tool_name, tool_input, session_id)
    except Exception as exc:  # noqa: BLE001 - surface the real error, this is a thin client
        duration_ms = int((time.monotonic() - started) * 1000)
        message = f"{type(exc).__name__}: {exc}"
        if session_id:
            try:
                session_store.record_step(
                    session_id, "tool_result", {"callId": call_id, "status": "error", "output": message, "durationMs": duration_ms}
                )
                session_store.error_turn(session_id, message)
            except SessionStoreError:
                pass
        yield {"type": "error", "message": message}
        return

    duration_ms = int((time.monotonic() - started) * 1000)
    # BOTH of AdCP's error layers, not just the envelope.
    #
    # This used to watch only `mcp_error`/`a2a_error`/`adcp_error` -- the transport and envelope layer --
    # and AdCP's CANONICAL shape is the payload's `errors[]` array. A seller returning
    # `{"errors": [{"code": "VALIDATION_ERROR", ...}], "status": "completed"}` was therefore recorded as
    # a SUCCESS, and the Live Tool Activity panel showed it green. Found live: a `create_media_buy` that
    # Poseidon refused for a missing field was logged as succeeding.
    #
    # `status: "completed"` on that response is not a contradiction -- it is the TASK status, meaning the
    # task finished. Whether it finished by booking or by refusing is what `errors[]` says.
    error_key = next((k for k in ("mcp_error", "a2a_error", "adcp_error") if k in result), None)
    if error_key is None and _has_payload_errors(result):
        error_key = "errors"
    status = "error" if error_key else "success"

    if session_id:
        try:
            session_store.record_step(
                session_id, "tool_result", {"callId": call_id, "status": status, "output": result, "durationMs": duration_ms}
            )
            session_store.record_step(session_id, "response", {"to": invoker, "text": json.dumps(result)})
            session_store.complete_turn(session_id)
        except SessionStoreError:
            pass

    yield {"type": "tool_result", "status": status, "result": result}
    yield {"type": "done", "stop_reason": "direct_call"}


async def _handle_list_sessions(payload: dict[str, Any]):
    """action=list_sessions: recent Buyer Agent sessions (any invoker, any
    mode) for the UI's session dropdown (R1). Single response, not a
    stream — yields one event then ends.
    """
    limit = int(payload.get("limit") or 20)
    # agent_id scopes the list to one agent's sessions (the UI's per-agent
    # filter); omitted or empty means "All agents".
    agent_id = (payload.get("agent_id") or "").strip() or None
    # Drop standalone tool-call records (a seller call that arrived with no buyer
    # correlation id). Off unless asked, so an API caller still sees everything;
    # the UI asks, because a list of conversations is what its control is for.
    conversations_only = bool(payload.get("conversations_only"))
    try:
        sessions = session_store.list_recent_sessions(
            limit=limit, agent_id=agent_id, conversations_only=conversations_only
        )
    except SessionStoreError as exc:
        yield {"type": "error", "message": str(exc)}
        return
    yield {
        "type": "sessions",
        "sessions": sessions,
        "agent_id": agent_id or "",
        # Echoed so the UI renders what it actually received rather than what it
        # believes it asked for — the two diverge the moment a stale page is on screen.
        "conversations_only": conversations_only,
    }


async def _handle_get_session_steps(payload: dict[str, Any]):
    """action=get_session_steps: one session's meta + steps after
    since_index (R3/polling). Single response, not a stream.
    """
    session_id = payload.get("session_id")
    if not session_id:
        yield {"type": "error", "message": "Missing 'session_id' in request payload."}
        return
    since_index = int(payload.get("since_index", -1))
    try:
        meta = session_store.get_session_meta(session_id)
        steps = session_store.get_session_steps(session_id, since_index=since_index)
    except SessionStoreError as exc:
        yield {"type": "error", "message": str(exc)}
        return
    yield {"type": "session_steps", "meta": meta, "steps": steps}


async def _handle_list_plans(payload: dict[str, Any]):
    """action=list_plans: distinct AdCP plans, most-recently-worked-on first, for the journey's plan
    picker. A plan can span several conversations, so this is keyed on the plan rather than the
    session. Single response, not a stream.
    """
    limit = int(payload.get("limit") or 20)
    try:
        plans = session_store.list_recent_plans(limit=limit)
    except SessionStoreError as exc:
        yield {"type": "error", "message": str(exc)}
        return
    yield {"type": "plans", "plans": plans}


async def _handle_get_plan_steps(payload: dict[str, Any]):
    """action=get_plan_steps: every recorded step for one plan, merged across all the sessions it was
    worked on in, so the journey can render a plan's whole flow even when it spans conversations.
    Single response, not a stream.
    """
    plan_id = payload.get("plan_id")
    if not plan_id:
        yield {"type": "error", "message": "Missing 'plan_id' in request payload."}
        return
    try:
        result = session_store.get_plan_steps(plan_id)
    except SessionStoreError as exc:
        yield {"type": "error", "message": str(exc)}
        return
    yield {"type": "plan_steps", **result}


def _a2a_text_deltas(chunk: dict[str, Any]) -> list[str]:
    """Text carried by one A2A stream event, in order.

    A2A streams a turn as `artifact-update` events whose artifact parts hold
    incremental text (confirmed live against the deployed A2A runtime). The
    initial `task` event and status-only events carry none, and are skipped.
    Nothing is synthesised — if an event has no text, this returns nothing for
    it rather than substituting a placeholder.
    """
    result = chunk.get("result", chunk)
    if not isinstance(result, dict):
        return []
    texts: list[str] = []
    artifact = result.get("artifact")
    if isinstance(artifact, dict):
        for part in artifact.get("parts") or []:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                texts.append(part["text"])
    return texts


async def _handle_a2a_chat(payload: dict[str, Any], context: RequestContext):
    """action=a2a_chat: relay one chat turn to a proxied A2A agent.

    Streams the upstream A2A response back in the same event vocabulary the
    browser already consumes ({text}, {done}, {error}), so the UI renders a
    proxied agent's reply exactly as it renders one it called directly.

    Only agents the registry marks `requires_proxy` are relayed: for anything
    else the browser holds a usable credential of its own, and relaying would
    put this runtime's identity on a call the user could make as themselves.
    """
    prompt = payload.get("prompt")
    if not prompt:
        yield {"type": "error", "message": "Missing 'prompt' in request payload."}
        return

    try:
        agent = resolve_agent(payload.get("agent_id"))
    except AgentRegistryError as exc:
        yield {"type": "error", "message": str(exc)}
        return

    if not agent["requires_proxy"]:
        yield {
            "type": "error",
            "message": (
                f"Agent {agent['id']!r} is called directly by the browser with the "
                "signed-in user's own token; it is not relayed through this runtime."
            ),
        }
        return

    # The browser's session id doubles as the A2A contextId, so a proxied
    # conversation keeps its continuity across turns the same way a direct one
    # does.
    session_id = context.session_id or session_store.new_session_id("adcp-a2a-proxy")
    body = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "message/stream",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": prompt}],
                "messageId": str(uuid.uuid4()),
                "kind": "message",
                "contextId": session_id,
            },
            "metadata": {"invokerAgent": payload.get("invoker") or "Chat UI"},
        },
    }
    headers = {
        **agent["headers"],
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    got_text = False
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            async with client.stream("POST", agent["url"], headers=headers, json=body) as resp:
                if resp.status_code >= 400:
                    detail = (await resp.aread()).decode(errors="replace")[:400]
                    yield {
                        "type": "error",
                        "message": f"{agent['name']} returned HTTP {resp.status_code}: {detail}",
                    }
                    return
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw:
                        continue
                    try:
                        chunk = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(chunk, dict) and chunk.get("error"):
                        yield {"type": "error", "message": f"{agent['name']}: {chunk['error']}"}
                        return
                    for text in _a2a_text_deltas(chunk):
                        got_text = True
                        yield {"type": "text", "data": text}
    except httpx.HTTPError as exc:
        yield {"type": "error", "message": f"Could not reach {agent['name']}: {exc}"}
        return

    if not got_text:
        # Say so rather than leaving an empty bubble that looks like a reply.
        yield {
            "type": "error",
            "message": (
                f"{agent['name']} completed the turn but returned no text content."
            ),
        }
        return

    yield {"type": "done", "stop_reason": "a2a_stream_end"}


async def _handle_whoami(claims: dict[str, Any]):
    """action=whoami: who the verified token says the caller is, and which
    Cognito groups it puts them in.

    The UI uses this to decide whether to offer the admin panel. It is
    authoritative about identity (the claims come from a token whose
    signature auth.py verified against Cognito's JWKS) but it is not the
    access control — every admin action re-checks membership itself.
    """
    yield {
        "type": "whoami",
        "username": claims.get("username") or claims.get("sub", ""),
        "groups": user_admin.groups_from_claims(claims),
        "is_admin": user_admin.is_admin(claims),
    }


async def _handle_list_users(payload: dict[str, Any], claims: dict[str, Any]):
    """action=list_users (admin only): the pool's users and their real
    Cognito status, for the admin panel.
    """
    try:
        user_admin.require_admin(claims)
        users = user_admin.list_users(limit=int(payload.get("limit") or user_admin.MAX_LIST_LIMIT))
    except NotAuthorizedError as exc:
        yield {"type": "error", "message": f"403 Forbidden: {exc}"}
        return
    except UserAdminError as exc:
        yield {"type": "error", "message": str(exc)}
        return
    yield {"type": "users", "users": users, "admin_group": user_admin.ADMIN_GROUP_NAME}


async def _handle_create_user(payload: dict[str, Any], claims: dict[str, Any]):
    """action=create_user (admin only): create a user with a temporary
    password they must replace at first sign-in.

    The generated temporary password is returned to the caller because the
    pool has no email delivery configured — the admin hands it over
    out-of-band. It is single-use: answering Cognito's
    NEW_PASSWORD_REQUIRED challenge replaces it.
    """
    try:
        user_admin.require_admin(claims)
        result = user_admin.create_user(payload.get("username", ""), payload.get("email", ""))
    except NotAuthorizedError as exc:
        yield {"type": "error", "message": f"403 Forbidden: {exc}"}
        return
    except UserAdminError as exc:
        yield {"type": "error", "message": str(exc)}
        return
    yield {"type": "user_created", **result}


# --- Agent registration administration -------------------------------------
# All four handlers re-check admin group membership on the VERIFIED token, exactly like the user-admin
# handlers above. The UI hiding the screen from non-admins is cosmetic; this is the boundary. They
# operate only on the connection registry (seller/governance URLs, transports, auth) and never on the
# AdCP call/response path — so a misuse cannot break AdCP compliance, only the buyer's agent list.

_REG_KINDS = (agent_registry_store.KIND_SELLER, agent_registry_store.KIND_GOVERNANCE)


def _agent_registrations_view(kind: str) -> list[dict[str, Any]]:
    """The admin view of one registry: environment built-ins (read-only) plus stored entries.

    Stored entries are editable/deletable and carry `has_stored_token` (never the token itself).
    Environment entries are shown read-only, since they come from configuration and are changed by
    redeploying — though an admin can override one by creating a stored entry with the same id.
    Nothing here exposes a secret value (FR-7): token values live only server-side.
    """
    stored = agent_registry_store.list_public(kind)  # source="stored", has_stored_token, no token value
    stored_ids = {e["id"] for e in stored}
    # include_hidden=True so a REMOVED built-in still appears here, marked hidden, and can be restored.
    # The effective registry the buyer uses excludes hidden ids; this admin view is the one place that
    # must still see them.
    effective = (
        list_seller_agents_public(include_hidden=True)
        if kind == agent_registry_store.KIND_SELLER
        else list_governance_agents_public(include_hidden=True)
    )
    try:
        hidden = agent_registry_store.hidden_ids(kind)
    except Exception:  # noqa: BLE001 - a store hiccup should not blank the whole admin view
        hidden = set()
    view: list[dict[str, Any]] = []
    for entry in effective:
        # Only the env-sourced built-ins here; stored entries are added below with richer metadata.
        # A built-in is editable only via a stored override, so it stays `editable: False`; `hidden`
        # tells the UI to offer Restore instead of Remove.
        if entry.get("source") == "env" and entry["id"] not in stored_ids:
            view.append(
                {
                    **entry,
                    "has_stored_token": False,
                    "has_stored_secret": False,
                    "editable": False,
                    "hidden": entry["id"] in hidden,
                }
            )
    for entry in stored:
        view.append({**entry, "editable": True, "hidden": entry["id"] in hidden})
    return view


async def _handle_list_agent_registrations(payload: dict[str, Any], claims: dict[str, Any]):
    """action=list_agent_registrations (admin only): the editable registry for one kind."""
    try:
        user_admin.require_admin(claims)
    except NotAuthorizedError as exc:
        yield {"type": "error", "message": f"403 Forbidden: {exc}"}
        return
    kind = (payload.get("kind") or "").strip()
    if kind not in _REG_KINDS:
        yield {"type": "error", "message": f"kind must be one of {_REG_KINDS}, got {kind!r}."}
        return
    try:
        agents = _agent_registrations_view(kind)
    except Exception as exc:  # noqa: BLE001 - surface a store problem rather than a blank list
        yield {"type": "error", "message": f"Could not read the {kind} registry: {exc}"}
        return
    yield {"type": "agent_registrations", "kind": kind, "agents": agents}


async def _handle_upsert_agent_registration(payload: dict[str, Any], claims: dict[str, Any]):
    """action=upsert_agent_registration (admin only): create or edit one stored registration.

    The entry is validated with the SAME validator the registry uses for environment entries, so a
    stored entry can never reach the resolve path malformed. The bearer token (for an external
    static_bearer agent) is stored server-side and never echoed back.
    """
    try:
        user_admin.require_admin(claims)
    except NotAuthorizedError as exc:
        yield {"type": "error", "message": f"403 Forbidden: {exc}"}
        return
    kind = (payload.get("kind") or "").strip()
    entry = payload.get("entry")
    if kind not in _REG_KINDS:
        yield {"type": "error", "message": f"kind must be one of {_REG_KINDS}, got {kind!r}."}
        return
    if not isinstance(entry, dict):
        yield {"type": "error", "message": "Missing 'entry' object in request payload."}
        return
    # Editing a static_bearer entry without re-typing its token: the token cannot be read back to the
    # browser, so a blank token field on an edit means "keep the existing one". Fill it from the stored
    # entry before validation so the entry still satisfies static_bearer's token requirement.
    if (
        isinstance(entry, dict)
        and entry.get("auth_type") == "static_bearer"
        and not entry.get("auth_token")
        and not entry.get("auth_token_env")
    ):
        existing = agent_registry_store.get(kind, str(entry.get("id", "")).strip())
        if existing and existing.get("auth_token"):
            entry = {**entry, "auth_token": existing["auth_token"]}
    # Same rule for an m2m_oauth entry's client_secret: it is never read back to the browser, so a
    # blank secret field on an edit means "keep the existing one". Refill it before validation so the
    # entry still satisfies m2m_oauth's client_secret requirement.
    if (
        isinstance(entry, dict)
        and entry.get("auth_type") == "m2m_oauth"
        and not entry.get("client_secret")
    ):
        existing = agent_registry_store.get(kind, str(entry.get("id", "")).strip())
        if existing and existing.get("client_secret"):
            entry = {**entry, "client_secret": existing["client_secret"]}
    try:
        if kind == agent_registry_store.KIND_SELLER:
            validated = validate_seller_entry({**entry, "source": "stored"})
        else:
            validated = validate_governance_entry({**entry, "source": "stored"})
    except (SellerAgentError, GovernanceAgentError) as exc:
        yield {"type": "error", "message": str(exc)}
        return
    updated_by = claims.get("username") or claims.get("sub", "")
    try:
        stored = agent_registry_store.put(kind, dict(validated), updated_by=updated_by)
    except agent_registry_store.AgentRegistryStoreError as exc:
        yield {"type": "error", "message": f"Could not save the registration: {exc}"}
        return
    yield {
        "type": "agent_registration_saved",
        "kind": kind,
        "agent": stored,
        "agents": _agent_registrations_view(kind),
    }


async def _handle_delete_agent_registration(payload: dict[str, Any], claims: dict[str, Any]):
    """action=delete_agent_registration (admin only): remove a stored registration.

    Only stored entries are removable; an environment built-in of the same id (if any) reappears in
    the effective registry afterwards.
    """
    try:
        user_admin.require_admin(claims)
    except NotAuthorizedError as exc:
        yield {"type": "error", "message": f"403 Forbidden: {exc}"}
        return
    kind = (payload.get("kind") or "").strip()
    agent_id = (payload.get("id") or "").strip()
    if kind not in _REG_KINDS:
        yield {"type": "error", "message": f"kind must be one of {_REG_KINDS}, got {kind!r}."}
        return
    if not agent_id:
        yield {"type": "error", "message": "Missing 'id' in request payload."}
        return
    try:
        existed = agent_registry_store.delete(kind, agent_id)
    except agent_registry_store.AgentRegistryStoreError as exc:
        yield {"type": "error", "message": f"Could not delete the registration: {exc}"}
        return
    yield {
        "type": "agent_registration_deleted",
        "kind": kind,
        "id": agent_id,
        "existed": existed,
        "agents": _agent_registrations_view(kind),
    }


async def _handle_hide_agent_registration(payload: dict[str, Any], claims: dict[str, Any]):
    """action=hide_agent_registration (admin only): remove an ENVIRONMENT built-in from the effective
    registry.

    An env built-in comes from configuration (SELLER_AGENTS_JSON / GOVERNANCE_AGENTS_JSON) and cannot
    be deleted, but it can be hidden: a durable tombstone that seller_agents/governance_agents honor,
    so the buyer stops fanning out to and resolving it — the removal takes effect on the next request,
    reversibly (see unhide). A stored (custom) entry is removed with delete_agent_registration, not
    this. Returns the refreshed registry view.
    """
    try:
        user_admin.require_admin(claims)
    except NotAuthorizedError as exc:
        yield {"type": "error", "message": f"403 Forbidden: {exc}"}
        return
    kind = (payload.get("kind") or "").strip()
    agent_id = (payload.get("id") or "").strip()
    if kind not in _REG_KINDS:
        yield {"type": "error", "message": f"kind must be one of {_REG_KINDS}, got {kind!r}."}
        return
    if not agent_id:
        yield {"type": "error", "message": "Missing 'id' in request payload."}
        return
    updated_by = claims.get("username") or claims.get("sub", "")
    try:
        agent_registry_store.hide(kind, agent_id, updated_by=updated_by)
    except agent_registry_store.AgentRegistryStoreError as exc:
        yield {"type": "error", "message": f"Could not remove the agent: {exc}"}
        return
    yield {"type": "agent_registrations", "kind": kind, "agents": _agent_registrations_view(kind)}


async def _handle_unhide_agent_registration(payload: dict[str, Any], claims: dict[str, Any]):
    """action=unhide_agent_registration (admin only): restore a hidden environment built-in to the
    effective registry. Returns the refreshed registry view."""
    try:
        user_admin.require_admin(claims)
    except NotAuthorizedError as exc:
        yield {"type": "error", "message": f"403 Forbidden: {exc}"}
        return
    kind = (payload.get("kind") or "").strip()
    agent_id = (payload.get("id") or "").strip()
    if kind not in _REG_KINDS:
        yield {"type": "error", "message": f"kind must be one of {_REG_KINDS}, got {kind!r}."}
        return
    if not agent_id:
        yield {"type": "error", "message": "Missing 'id' in request payload."}
        return
    try:
        agent_registry_store.unhide(kind, agent_id)
    except agent_registry_store.AgentRegistryStoreError as exc:
        yield {"type": "error", "message": f"Could not restore the agent: {exc}"}
        return
    yield {"type": "agent_registrations", "kind": kind, "agents": _agent_registrations_view(kind)}


async def _handle_test_agent_connection(payload: dict[str, Any], claims: dict[str, Any]):
    """action=test_agent_connection (admin only): call get_adcp_capabilities against a registration.

    Uses the buyer's existing, AdCP-compliant tool path (adcp_tools.call_seller_tool) — no new wire
    code. Reports the real result or the real error; nothing is simulated.
    """
    try:
        user_admin.require_admin(claims)
    except NotAuthorizedError as exc:
        yield {"type": "error", "message": f"403 Forbidden: {exc}"}
        return
    kind = (payload.get("kind") or "").strip()
    agent_id = (payload.get("id") or "").strip()
    if kind not in _REG_KINDS:
        yield {"type": "error", "message": f"kind must be one of {_REG_KINDS}, got {kind!r}."}
        return
    try:
        if kind == agent_registry_store.KIND_SELLER:
            target = resolve_seller_agent(agent_id or None)
        else:
            target = resolve_governance_agent()
    except (SellerAgentError, GovernanceAgentError) as exc:
        yield {"type": "error", "message": str(exc)}
        return

    started = time.monotonic()
    try:
        result = await call_seller_tool(target, "get_adcp_capabilities", {}, None)
    except Exception as exc:  # noqa: BLE001 - a reachability test reports the real failure
        yield {
            "type": "agent_connection_test",
            "kind": kind,
            "id": agent_id,
            "ok": False,
            "message": f"{type(exc).__name__}: {exc}",
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
        return
    error_key = next((k for k in ("mcp_error", "a2a_error", "adcp_error") if k in result), None)
    ok = error_key is None and not _has_payload_errors(result)
    yield {
        "type": "agent_connection_test",
        "kind": kind,
        "id": agent_id,
        "ok": ok,
        "message": "Reachable" if ok else "The agent returned an error response.",
        "result": result,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }


# --- UI routes -------------------------------------------------------------
# These are plain Starlette routes added on top of the AgentCore app so the
# same process serves both the agent API and its UI locally. In production
# on AgentCore Runtime, only /invocations and /ping are reachable directly;
# fronting a separate static host (e.g. S3 + CloudFront) is the deploy path
# described in the README for that case.


async def index(request):
    """The React app, which is the UI this project builds on.

    Falls through to the frozen single-file UI when the React build is absent, rather than failing.
    That is the state inside the deployed container, where `ui/` is excluded from the image on
    purpose (the built app is served by CloudFront, not from here), and it is also the state in a
    fresh checkout before `npm run build` has run. Serving something that works beats a 503 for a
    route people reach by typing the bare host.
    """
    built = REACT_DIST_DIR / "index.html"
    if built.is_file():
        return FileResponse(built)
    return FileResponse(STATIC_DIR / "index.html")


async def legacy_index(request):
    """The frozen single-file UI.

    Kept reachable because it is the reference the React app was checked against, and comparing the
    two against one runtime is the reason it still exists. It is not deployed; see
    .kiro/steering/buyer-ui-react-is-canonical.md.
    """
    return FileResponse(STATIC_DIR / "index.html")


async def spa_fallback(request):
    """Serve the app shell for any path the router owns, so a refresh works.

    The React app now has real routes: the journey view at `/` and the chat view at `/chat`. A
    client-side route only exists once the shell has loaded, so navigating to `/chat` inside the app
    works without this, and **refreshing** or pasting the link does not. That distinction matters when
    verifying a deploy: clicking through to a route never touches the server, so a check that stops at
    "the Chat link works" would pass against a broken configuration.

    CloudFront does the same job in production, and for a reason worth recording here: the site is a
    private S3 bucket reached over OAC with a bucket policy granting `s3:GetObject` only. Without
    `s3:ListBucket`, S3 answers a missing key with **403 AccessDenied**, not 404, so the distribution
    has to map 403 as well as 404 to `/index.html`. See deploy_ui.py's converge step.

    Registered last, and only for paths this app does not otherwise serve, so it cannot shadow
    `/config`, `/static`, `/assets`, `/legacy`, `/ping` or `/invocations`.
    """
    built = REACT_DIST_DIR / "index.html"
    if built.is_file():
        return FileResponse(built)
    # No build on disk. The frozen UI has no routes of its own, so a deep link cannot be honoured;
    # serving the shell it does have beats a 404 for someone who followed a link.
    return FileResponse(STATIC_DIR / "index.html")


#: The React build: the UI this project develops and deploys.
#:
#: Present only when built. `npm run build` in `ui/` writes it, and it is gitignored build output
#: rather than source, so a fresh checkout has no copy of it.
REACT_DIST_DIR = Path(__file__).parent / "ui" / "dist"


def seller_priority() -> list[str]:
    """Seller ids in the order the journey view prefers to display them.

    The journey view shows one seller's journey at a time and names the others beside it, so something
    has to choose. Read from SELLER_PRIORITY as a comma-separated list of seller ids.

    Absent or empty means "no configured preference", which is not an error: sellers then keep the
    order the fan-out returned them in. A seller missing from the list ranks last rather than being
    hidden, because this is a display preference and not an allowlist — dropping an unlisted seller
    would conceal the very thing a fan-out exists to reveal.
    """
    raw = os.environ.get("SELLER_PRIORITY", "")
    return [part.strip() for part in raw.split(",") if part.strip()]


async def healthcheck_info(request):
    # Cognito user pool ID and app client ID are not secrets — this is the
    # standard public-client browser auth pattern (no client secret exists
    # for this app client; see deploy_cognito_setup.py). The browser needs
    # these to call Cognito's InitiateAuth API directly and build the JWKS/
    # issuer URLs used to validate its own token client-side if desired.
    #
    # agent_runtime_arn / aws_region: the browser uses these to call the
    # deployed AgentCore Runtime's invoke endpoint directly
    # (bedrock-agentcore.{region}.amazonaws.com/runtimes/{arn}/invocations),
    # rather than routing through this local server's own /invocations.
    # The ARN identifies which runtime to call, not a credential.
    try:
        seller_agents = list_seller_agents_public()
        default_seller_agent_id = get_default_seller_agent_id()
    except SellerAgentError:
        # Surface an empty state rather than a 500 - the rest of
        # /config (Cognito config, runtime ARN) is still useful even if
        # SELLER_AGENTS_JSON hasn't been set up yet.
        seller_agents = []
        default_seller_agent_id = ""

    # The A2A agents the UI can chat to (agents_registry.py). Same
    # empty-state handling: a missing/malformed AGENTS_JSON leaves the agent
    # selector empty and says so, rather than failing the whole config fetch.
    try:
        agents = list_agents_public()
        default_agent_id = get_default_agent_id()
        agents_error = ""
    except AgentRegistryError as exc:
        agents = []
        default_agent_id = ""
        agents_error = str(exc)

    # The configured governance agents (env + any runtime registrations), public metadata only — no
    # token values (list_governance_agents_public never includes one). Additive to /config so the
    # admin screen can display governance the way it already displays sellers; [] when none exist.
    governance_agents = list_governance_agents_public()

    return JSONResponse(
        {
            "seller_priority": seller_priority(),
            "model_id": os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-5"),
            "cognito_region": os.environ.get("COGNITO_REGION", ""),
            "cognito_client_id": os.environ.get("COGNITO_CLIENT_ID", ""),
            "agent_runtime_arn": os.environ.get("AGENT_RUNTIME_ARN", ""),
            "aws_region": region(),
            # The identity pool and table the browser reads recorded sessions from directly, rather
            # than paying the ~843 ms AgentCore hop the `get_session_steps` action costs. Neither is
            # a secret: the pool id is a public client identifier (like the app client id above) and
            # grants nothing without a valid user-pool ID token, and the table name is useless
            # without credentials scoped to it.
            #
            # Empty means the pool has not been deployed. The UI treats that as "fall back to the
            # /invocations actions" rather than as an error, so a stack without the pool still works.
            "identity_pool_id": os.environ.get("IDENTITY_POOL_ID", ""),
            "sessions_table_name": os.environ.get("SESSIONS_TABLE_NAME", ""),
            "seller_agents": seller_agents,
            "default_seller_agent_id": default_seller_agent_id,
            "agents": agents,
            "default_agent_id": default_agent_id,
            "agents_error": agents_error,
            "governance_agents": governance_agents,
            # Shape-parity with deploy_ui.py's generated config.json, which carries the publish
            # time so the UI can show which build is loaded. Served from disk here, so there is
            # no publish time to report: the answer is "served locally", which is the
            # value rather than a timestamp implying a deployment that did not happen.
            "ui_published_at": "",
        }
    )


app.add_route("/", index, methods=["GET"])
app.add_route("/config", healthcheck_info, methods=["GET"])
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# The frozen single-file UI, kept reachable for comparison against the React app.
app.add_route("/legacy", legacy_index, methods=["GET"])

# Client-side routes. Registered explicitly rather than as a catch-all, so nothing this app serves
# can be shadowed and an unknown path still 404s instead of silently returning a page.
app.add_route("/chat", spa_fallback, methods=["GET"])

if REACT_DIST_DIR.is_dir():
    # The React app's hashed assets. Mounted at /assets to match where the shell looks for them:
    # vite's `base: './'` makes the references relative, so a shell served from / resolves
    # `./assets/...` to `/assets/...`.
    #
    # Mounted only when the build exists, because StaticFiles raises at construction on a missing
    # directory, which would stop the whole app from starting in a checkout that has not built the UI.
    app.mount(
        "/assets",
        StaticFiles(directory=str(REACT_DIST_DIR / "assets")),
        name="assets",
    )


if __name__ == "__main__":
    app.run()
