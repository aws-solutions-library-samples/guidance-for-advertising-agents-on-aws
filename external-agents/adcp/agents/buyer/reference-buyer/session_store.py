"""
DynamoDB-backed store for Buyer Agent reasoning sessions.

A "session" here is one runtime conversation (the same id used as the
AgentCore Runtime session / Strands S3SessionManager key elsewhere in this
project — see agent.py). A conversation can have multiple turns (multiple
/invocations calls sharing the same session id); each turn appends more
step items under the same session, and the session's meta status reflects
whichever turn most recently ran.

Table layout (single table, on-demand billing):

    pk                      sk              attributes
    ---------------------   -------------   ------------------------------
    SESSION#<session_id>    META            status, invoker, mode,
                                             seller_agent_id, request_preview,
                                             started_at, updated_at,
                                             completed_at, error_message,
                                             step_counter, gsi1pk, gsi1sk, ttl
    SESSION#<session_id>    STEP#000001     session_id, step_index, step_type,
                            STEP#000002     timestamp, content, ttl
                            ...

`gsi1pk`/`gsi1sk` are only set on META items (a sparse GSI), so querying the
GSI for a constant partition value returns one item per session, sorted by
start time — that's how list_recent_sessions() finds "recent sessions"
without a full table scan.

Every write here is a real DynamoDB call. No step or session state is
substituted; this module records only events that happened
(hooks firing on a real Strands Agent invocation, or a real direct tool
call).
"""

import uuid
from decimal import Decimal
from typing import Any

from boto3.dynamodb.conditions import Key
from dotenv import load_dotenv

import session_records

load_dotenv()


def _from_dynamo(value: Any) -> Any:
    """Recursively convert DynamoDB's Decimal numbers back to plain int/float
    so callers (e.g. app.py's json.dumps for SSE) don't choke on them.
    """
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, list):
        return [_from_dynamo(v) for v in value]
    if isinstance(value, dict):
        return {k: _from_dynamo(v) for k, v in value.items()}
    return value


# The item shape, TTL and step vocabulary are defined once, in
# session_records.py, and re-exported here so existing importers of
# session_store keep working unchanged.
StepType = session_records.StepType
SessionStatus = session_records.SessionStatus
GSI1_PK_VALUE = session_records.GSI1_PK_VALUE
PLAN_GSI1_PK_VALUE = session_records.PLAN_GSI1_PK_VALUE
SELLER_SESSION_PREFIX = session_records.SELLER_SESSION_PREFIX
TTL_SECONDS = session_records.TTL_SECONDS


class SessionStoreError(RuntimeError):
    """Raised when the sessions table isn't configured or a DynamoDB call fails."""


def _table():
    """The sessions table, for this module's READ queries.

    Writes go through session_records.py (see this module's docstring), which
    resolves the same table from the same env var. Its errors are surfaced as
    SessionStoreError so every caller here keeps one exception type to catch.
    """
    try:
        return session_records.table()
    except session_records.SessionRecordError as exc:
        raise SessionStoreError(str(exc)) from exc


# --- Writers -------------------------------------------------------------
# Re-exported from session_records so every agent in the project writes the
# identical item shape (see that module for why it's separate). Kept as
# module-level names here so existing callers (app.py, reasoning_hooks.py,
# a2a_runtime/a2a_entrypoint.py) are unchanged.

start_turn = session_records.start_turn
complete_turn = session_records.complete_turn
error_turn = session_records.error_turn
record_step = session_records.record_step


def _session_summary(item: dict[str, Any]) -> dict[str, Any]:
    """The META attributes the UI shows for one session.

    `agent_id`/`agent_name` identify which agent ran the session. They are
    absent on records written before agents reported their identity, and on
    any writer that doesn't supply them — reported as None so the UI can say
    "unknown agent" rather than attributing the session to the buyer agent by
    assumption.
    """
    return {
        "session_id": item.get("session_id"),
        "status": item.get("status"),
        "invoker": item.get("invoker"),
        "mode": item.get("mode"),
        "agent_id": item.get("agent_id"),
        "agent_name": item.get("agent_name"),
        "seller_agent_id": item.get("seller_agent_id"),
        "request_preview": item.get("request_preview"),
        "started_at": item.get("started_at"),
        "updated_at": item.get("updated_at"),
        "completed_at": item.get("completed_at"),
        "error_message": item.get("error_message"),
        # Which AdCP plan(s) this conversation worked on, stamped as turns were recorded. A set in
        # DynamoDB, returned as a sorted list so it is JSON-serialisable; absent on sessions recorded
        # before plan tracking, or on ones that never touched a plan (pure discovery). latest_plan_id
        # is the plan a plan-scoped step last named, when it named exactly one.
        "plan_ids": sorted(item.get("plan_ids") or []),
        "latest_plan_id": item.get("latest_plan_id"),
    }


#: Session-id prefix the seller-side recorder uses when a tool call arrives with no buyer
#: correlation id (`seller_session_recorder.observe_request`).
#:
#: Such a record is one standalone tool call, not a conversation. That is deliberate on the recorder's
#: side — lumping unrelated calls under a shared placeholder would be worse — but it means a raw MCP
#: client hammering a seller produces one "session" per call. A verification run did exactly that: 95
#: of 101 records were single-call seller sessions, which pushed every real conversation off the
#: newest-50 page the UI fetches. The dropdown was working perfectly and showing nothing anyone wanted.
UNCORRELATED_SESSION_PREFIX = "adcp-seller-uncorrelated-"


def is_uncorrelated_tool_call(session_id: Any) -> bool:
    """Whether this record is a standalone tool call rather than a conversation."""
    return str(session_id or "").startswith(UNCORRELATED_SESSION_PREFIX)


def list_recent_sessions(
    limit: int = 20,
    agent_id: str | None = None,
    conversations_only: bool = False,
) -> list[dict[str, Any]]:
    """The most recently **active** sessions, newest first, for the UI's sessions dropdown.

    Ordered by `gsi1sk`, which is the last-activity timestamp — refreshed on every turn. It used to be
    pinned to the first turn, so this function ordered by start time while its docstring claimed
    "started/updated"; a conversation in progress sorted below a newer abandoned one.

    Uses the sparse GSI1 (only META items carry gsi1pk/gsi1sk), so this never scans STEP items.

    `agent_id` filters to sessions run by one agent; None (the UI's "All agents") returns every
    agent's sessions.

    `conversations_only` drops standalone tool-call records (see `UNCORRELATED_SESSION_PREFIX`).
    Off by default so an API caller still sees everything recorded; the UI turns it on, because a list
    of conversations is what that control is for. Nothing is hidden silently — the UI carries a visible
    toggle, and turning it off returns every record.

    Both filters are applied after the query rather than as a KeyCondition, since gsi1's key is a
    constant partition value plus a timestamp; adding a dimension to the index would be a table
    migration for a list already capped at `limit`. Because of that, a filtered query fetches a
    larger page and then narrows it, so filtering cannot silently return fewer than `limit` results
    just because other records crowded the page.
    """
    table = _table()
    # Over-fetch when filtering so the requested number of matching sessions can still be returned.
    # Capped so one call stays bounded. `conversations_only` needs the same headroom as `agent_id`,
    # and for the same reason — it was the missing over-fetch here that made the UI's list look
    # truncated rather than filtered.
    filtering = bool(agent_id) or conversations_only
    query_limit = min(limit * 5, 200) if filtering else limit
    try:
        resp = table.query(
            IndexName="gsi1",
            KeyConditionExpression=Key("gsi1pk").eq(GSI1_PK_VALUE),
            ScanIndexForward=False,
            Limit=query_limit,
        )
    except table.meta.client.exceptions.ResourceNotFoundException as exc:
        raise SessionStoreError(f"Sessions table/index not found: {exc}") from exc

    sessions = []
    for item in resp.get("Items", []):
        if agent_id and item.get("agent_id") != agent_id:
            continue
        if conversations_only and is_uncorrelated_tool_call(item.get("session_id")):
            continue
        sessions.append(_session_summary(item))
        if len(sessions) >= limit:
            break
    return _from_dynamo(sessions)


def get_session_meta(session_id: str) -> dict[str, Any] | None:
    table = _table()
    resp = table.get_item(Key={"pk": f"SESSION#{session_id}", "sk": "META"})
    item = resp.get("Item")
    if not item:
        return None
    return _from_dynamo(_session_summary(item))


def get_session_steps(session_id: str, since_index: int = -1) -> list[dict[str, Any]]:
    """Steps for one session with step_index > since_index, oldest first —
    pass the last index you already have to fetch only new steps.
    """
    table = _table()
    resp = table.query(
        KeyConditionExpression=(
            # sk > STEP#<since_index>, so the next step (since_index + 1) is
            # included. Comparing against STEP#<since_index + 1> instead would
            # make every incremental poll silently skip exactly one step.
            Key("pk").eq(f"SESSION#{session_id}") & Key("sk").gt(f"STEP#{since_index:06d}")
            if since_index >= 0
            else Key("pk").eq(f"SESSION#{session_id}") & Key("sk").begins_with("STEP#")
        ),
        ScanIndexForward=True,
    )
    steps = []
    for item in resp.get("Items", []):
        steps.append(
            {
                "step_index": int(item.get("step_index", 0)),
                "step_type": item.get("step_type"),
                "timestamp": int(item.get("timestamp", 0)),
                "content": _from_dynamo(item.get("content", {})),
            }
        )
    return steps


def new_session_id(prefix: str = "adcp-buyer-agent-direct") -> str:
    """A random session id long enough to satisfy AgentCore Runtime's
    runtimeSessionId minimum (33 chars) — for callers that have no
    runtimeSessionId of their own to key on.
    """
    return session_records.new_session_id(prefix)


# --- Plans ---------------------------------------------------------------
# A plan is worked on across one or more conversations. These queries let the journey key on the plan
# rather than a single session, so continuing an existing plan in a new conversation still shows the
# plan's whole flow. Backed by the plan-link items session_records writes (see PLAN_GSI1_PK_VALUE).


def list_recent_plans(limit: int = 20) -> list[dict[str, Any]]:
    """Distinct plans, most-recently-worked-on first, for the journey's plan picker.

    Queries the same sparse GSI1 the sessions list uses, on the "PLAN" partition. A plan has one
    link item per session it appeared in; those are deduped to one row per plan (keeping the newest,
    since the query is newest-first) and each row reports how many sessions the plan spans.
    """
    table = _table()
    query_limit = min(limit * 10, 400)
    try:
        resp = table.query(
            IndexName="gsi1",
            KeyConditionExpression=Key("gsi1pk").eq(PLAN_GSI1_PK_VALUE),
            ScanIndexForward=False,
            Limit=query_limit,
        )
    except table.meta.client.exceptions.ResourceNotFoundException as exc:
        raise SessionStoreError(f"Sessions table/index not found: {exc}") from exc

    plans: list[dict[str, Any]] = []
    session_counts: dict[str, set[str]] = {}
    order: list[str] = []
    for item in resp.get("Items", []):
        plan_id = item.get("plan_id")
        if not plan_id:
            continue
        session_id = item.get("session_id")
        if plan_id not in session_counts:
            session_counts[plan_id] = set()
            order.append(plan_id)
            plans.append(
                {"plan_id": plan_id, "last_activity": item.get("gsi1sk") or item.get("updated_at")}
            )
        if session_id:
            session_counts[plan_id].add(str(session_id))
        if len(order) >= limit and plan_id not in session_counts:
            break

    for plan in plans:
        plan["session_count"] = len(session_counts.get(plan["plan_id"], set()))
    return _from_dynamo(plans[:limit])


def list_plan_sessions(plan_id: str) -> list[str]:
    """The buyer session ids that worked on this plan, seller child recordings excluded."""
    table = _table()
    resp = table.query(
        KeyConditionExpression=Key("pk").eq(f"PLAN#{plan_id}") & Key("sk").begins_with("SESSION#")
    )
    ids: list[str] = []
    for item in resp.get("Items", []):
        session_id = str(item.get("session_id") or "")
        if session_id and not session_id.startswith(SELLER_SESSION_PREFIX):
            ids.append(session_id)
    return sorted(set(ids))


def get_plan_steps(plan_id: str) -> dict[str, Any]:
    """Every recorded step for a plan, merged across its sessions, oldest-first by time.

    Cross-session by design: the point of keying on the plan is that a plan continued in a new
    conversation still shows its whole flow. callIds are unique per tool call, so merging sessions
    does not confuse the exchange pairing the journey does; ordering by timestamp keeps "latest"
    correct across sessions. Each step carries its originating `session_id`, and a monotonic
    `global_index` is assigned after the merge so a client can still track a high-water mark. The
    most recently updated session's META is returned as `meta` for the header.
    """
    session_ids = list_plan_sessions(plan_id)
    merged: list[dict[str, Any]] = []
    latest_meta: dict[str, Any] | None = None
    for session_id in session_ids:
        meta = get_session_meta(session_id)
        if meta and (
            latest_meta is None
            or str(meta.get("updated_at") or "") > str(latest_meta.get("updated_at") or "")
        ):
            latest_meta = meta
        for step in get_session_steps(session_id, since_index=-1):
            step = dict(step)
            step["session_id"] = session_id
            merged.append(step)

    merged.sort(
        key=lambda s: (
            int(s.get("timestamp") or 0),
            str(s.get("session_id") or ""),
            int(s.get("step_index") or 0),
        )
    )
    for index, step in enumerate(merged):
        step["global_index"] = index

    return {
        "plan_id": plan_id,
        "sessions": session_ids,
        "meta": latest_meta,
        "steps": merged,
    }
