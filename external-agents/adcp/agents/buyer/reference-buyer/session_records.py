"""
The session-record WRITE contract, shared by every agent in this project.

Why this module exists separately from session_store.py
-------------------------------------------------------
More than one agent now writes reasoning sessions into the single DynamoDB
table the chat UI reads: the buyer agent's HTTP and A2A runtimes, and (per
the multi-agent-chat plan's Q3/Q4 answers) the seller agents too. Only the
buyer *reads* the table — `session_store.py` keeps the read side, and
delegates its writes here so there is exactly one definition of the item
shape rather than one per agent that can drift.

Vendoring, and why this file lives here rather than in a repo-root shared/
-------------------------------------------------------------------------
Each AgentCore Runtime is built from an isolated build context (the toolkit
rejects a dependency path that resolves outside the project root — the same
constraint that makes `a2a_runtime/requirements.txt` a real copy rather than
a symlink). A repo-root `shared/` package therefore cannot be imported by a
seller's container. So this file is the single source, and
`deploy_all.py`'s `vendor-session-module` step copies it into each seller
package immediately before that seller deploys. The copies are generated
build artifacts: gitignored, never hand-edited, and regenerated on every
deploy so a stale copy cannot ship (the same discipline the generated
Dockerfiles get — see .kiro/steering/agentcore-deployment.md).

Keeping the source here rather than generating the buyer's copy too means
local dev (`python app.py`) works with no deploy step having been run.

Table layout (unchanged — see session_store.py for the full description):

    pk                      sk              attributes
    ---------------------   -------------   ------------------------------
    SESSION#<session_id>    META            status, invoker, mode, agent_id,
                                             agent_name, seller_agent_id,
                                             request_preview, started_at,
                                             updated_at, completed_at,
                                             error_message, step_counter,
                                             gsi1pk, gsi1sk, ttl

`gsi1sk` is the **last activity** timestamp, refreshed on every turn — not the
start time. `started_at` is the start time. They were the same attribute until a
conversation in progress was found sorting below a newer abandoned one.
    SESSION#<session_id>    STEP#000001     session_id, step_index, step_type,
                            STEP#000002     timestamp, content, ttl
                            ...

Every write here is a real DynamoDB call. No step or session state is
substituted; this module records only events that happened.
"""


from aws_region import region as resolve_region
import json
import os
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal

import boto3

StepType = Literal["incoming_request", "thought", "tool_call", "tool_result", "response"]
SessionStatus = Literal["active", "completed", "error"]

GSI1_PK_VALUE = "SESSION"
#: Partition value for the plan-link items on the SAME sparse GSI1. A plan-link item ties one plan_id
#: to one session it appeared in: pk=PLAN#<plan_id>, sk=SESSION#<session_id>, carrying gsi1pk="PLAN"
#: so `list_recent_plans` can enumerate plans newest-first off the existing index — no second GSI, so
#: no table migration. A plan can appear in more than one session (worked on across conversations),
#: which is the whole point of keying the journey on the plan rather than the session.
PLAN_GSI1_PK_VALUE = "PLAN"

#: Session ids the seller-side recorder writes (adcp-seller-<sellerid>-<buyersession> and the
#: uncorrelated variant). Plan links are created only for BUYER conversations, so the journey's
#: plan view is the buyer's own flow and not the sellers' separate child recordings.
SELLER_SESSION_PREFIX = "adcp-seller-"

# How long a session record (and its steps) survive before DynamoDB TTL
# reclaims them. This is a live/recent view, not an audit log — see
# pick_up_external_invocation.md's "No persistence required" NFR.
TTL_SECONDS = 24 * 60 * 60

# Minimum length AgentCore Runtime enforces on runtimeSessionId, which is
# what session ids are keyed on throughout this project.
MIN_SESSION_ID_LENGTH = 33


class SessionRecordError(RuntimeError):
    """Raised when the sessions table isn't configured or a DynamoDB call fails."""


def to_dynamo(value: Any) -> Any:
    """Recursively convert plain Python floats to Decimal before a put/
    update — boto3's DynamoDB resource rejects float directly. Tool output
    recorded as a reasoning step's content (e.g. market-rate CPMs) can
    legitimately contain floats, so this runs on every step write.
    """
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, list):
        return [to_dynamo(v) for v in value]
    if isinstance(value, dict):
        return {k: to_dynamo(v) for k, v in value.items()}
    return value


def table():
    table_name = os.environ.get("SESSIONS_TABLE_NAME", "").strip()
    if not table_name:
        raise SessionRecordError(
            "SESSIONS_TABLE_NAME is not set. Run deploy_sessions_table.py and add the "
            "resulting table name to this agent's environment."
        )
    region = resolve_region()
    resource = boto3.resource("dynamodb", region_name=region)
    return resource.Table(table_name)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_ms() -> int:
    return int(time.time() * 1000)


def new_session_id(prefix: str = "adcp-session") -> str:
    """A random session id long enough to satisfy AgentCore Runtime's
    runtimeSessionId minimum, for callers that have no runtime session of
    their own to key on.
    """
    candidate = f"{prefix}-{uuid.uuid4()}"
    return (
        candidate
        if len(candidate) >= MIN_SESSION_ID_LENGTH
        else candidate.ljust(MIN_SESSION_ID_LENGTH, "0")
    )


def start_turn(
    session_id: str,
    *,
    invoker: str,
    mode: Literal["agent", "direct"],
    seller_agent_id: str,
    request_preview: str,
    agent_id: str = "",
    agent_name: str = "",
) -> None:
    """Mark a session as actively processing a new turn.

    Creates the META item on the first turn of a conversation, or refreshes
    it (status back to "active", new request_preview/invoker) on later turns
    of the same session id. step_counter is preserved across turns via
    `if_not_exists` so step ordering stays monotonic for the whole
    conversation, not just the current turn.

    `agent_id`/`agent_name` identify which agent RAN the turn (the buyer
    agent's HTTP runtime, its A2A runtime, a seller agent, ...), as opposed
    to `invoker`, which is who asked. They are written with `if_not_exists`
    semantics deliberately absent — a later turn of the same session is
    still run by the same agent, so overwriting with the same value is
    harmless, and an agent that starts reporting its identity should be able
    to correct a record written before it did.

    An agent that does not supply them leaves them unset rather than
    defaulted: a session with no agent_id is reported by the UI as "unknown
    agent", never attributed to the buyer agent by assumption.
    """
    tbl = table()
    now = _now_iso()
    ttl = int(time.time()) + TTL_SECONDS

    set_parts = [
        "session_id = :sid",
        "#status = :active",
        "invoker = :invoker",
        "#mode = :mode",
        "seller_agent_id = :seller",
        "request_preview = :preview",
        "updated_at = :now",
        "gsi1pk = :gsi1pk",
        # **Last activity, not first.** This was `if_not_exists(gsi1sk, :now)`, which pinned the GSI's
        # sort key to the first turn of a conversation — so `list_recent_sessions`, which orders by it
        # and documents itself as returning "the most recently started/updated sessions", actually
        # ordered by start time alone. A conversation you were in the middle of did not rise above one
        # started a minute later and abandoned, and the UI's "newest first" list quietly meant
        # "first-seen first".
        #
        # `started_at` below keeps its `if_not_exists`, so the *start* time is still the start time and
        # the UI's "Started" column is unaffected. The two facts are now separate, which they always
        # should have been: one item cannot express both from a single attribute.
        "gsi1sk = :now",
        "started_at = if_not_exists(started_at, :now)",
        "step_counter = if_not_exists(step_counter, :zero)",
        "#ttl = :ttl",
    ]
    names = {"#status": "status", "#mode": "mode", "#ttl": "ttl"}
    values: dict[str, Any] = {
        ":sid": session_id,
        ":active": "active",
        ":invoker": invoker,
        ":mode": mode,
        ":seller": seller_agent_id,
        ":preview": request_preview[:80],
        ":now": now,
        ":gsi1pk": GSI1_PK_VALUE,
        ":zero": 0,
        ":ttl": ttl,
    }

    # Only written when actually provided, so a legacy/unidentified writer
    # leaves the attribute absent instead of storing an empty string that
    # would read as a real (blank-named) agent.
    if agent_id:
        set_parts.append("agent_id = :agent_id")
        values[":agent_id"] = agent_id
    if agent_name:
        set_parts.append("agent_name = :agent_name")
        values[":agent_name"] = agent_name

    tbl.update_item(
        Key={"pk": f"SESSION#{session_id}", "sk": "META"},
        UpdateExpression="SET " + ", ".join(set_parts) + " REMOVE error_message",
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


def complete_turn(session_id: str) -> None:
    tbl = table()
    tbl.update_item(
        Key={"pk": f"SESSION#{session_id}", "sk": "META"},
        UpdateExpression=(
            "SET #status = :completed, updated_at = :now, completed_at = :now "
            "REMOVE error_message"
        ),
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={":completed": "completed", ":now": _now_iso()},
    )


def error_turn(session_id: str, message: str) -> None:
    tbl = table()
    tbl.update_item(
        Key={"pk": f"SESSION#{session_id}", "sk": "META"},
        UpdateExpression="SET #status = :error, updated_at = :now, error_message = :msg",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={":error": "error", ":now": _now_iso(), ":msg": str(message)[:2000]},
    )


# DynamoDB caps an item at 400KB. A fan-out tool result — one AdCP task run
# against every sales agent — measured 470,310 bytes live, so the whole step
# write failed and the UI was left with a tool card that never completed.
# Budget the content well under the limit to leave room for the item's other
# attributes and for UTF-8 expansion.
MAX_CONTENT_BYTES = 300_000
# A single string inside a step (a tool's serialized output) is truncated at
# this length while shrinking. Large enough to keep a readable payload, small
# enough that several of them still fit the budget.
MAX_STRING_CHARS = 40_000


def _sizeof(value: Any) -> int:
    return len(json.dumps(value, default=str))


# A short list is treated as structure (one entry per sales agent, one entry per
# placement) and every entry is kept, each shrunk to an equal share of the
# budget. A long list is treated as data (a product catalogue) and is shortened
# to whole entries that fit. Dropping a whole sales agent from a fan-out result
# would read as "that agent returned nothing", which is a different claim from
# "we didn't store what it returned" — so breadth is preserved and depth is
# what gives way.
STRUCTURAL_LIST_MAX_LEN = 8
# Floor on any child's share, so a wide object still records something real per
# field rather than everything being cut to nothing.
MIN_SHARE_BYTES = 500


def _shares(sizes: list[int], budget: int) -> list[int]:
    """Split a budget across children in proportion to how big each one is.

    Splitting it equally instead compounds badly: at every level the small
    scalar fields are handed a share they can't use while the one field that
    holds the payload (a product catalogue) is squeezed, so a 280KB budget ends
    up recording a few hundred bytes per seller.
    """
    total = sum(sizes) or 1
    return [max(MIN_SHARE_BYTES, budget * size // total) for size in sizes]


def _shrink(value: Any, budget: int) -> Any:
    """Return `value` reduced to roughly `budget` bytes, with every reduction
    stated in the data.

    Nothing is silently dropped: a shortened list gets a sibling
    `<key>_recording_truncated: {kept, total}` and a truncated string gets
    `<key>_recording_truncated: {kept_chars, total_chars}`. A reader (the chat
    UI, or a person) can therefore tell "this seller returned 5 products" apart
    from "this seller returned 200 and we stored 5" — different claims, and
    conflating them would report one as the other.

    Shape is preserved so per-seller rendering keeps working: a shrunk fan-out
    payload is still `{results: [{seller_name, response|error}, ...]}` with one
    entry per sales agent, just with smaller leaves.
    """
    if isinstance(value, str):
        limit = max(MIN_SHARE_BYTES, min(MAX_STRING_CHARS, budget))
        return value if len(value) <= limit else value[:limit]
    if isinstance(value, list):
        return _shrink_list(value, budget)[0]
    if isinstance(value, dict):
        shares = _shares([_sizeof(v) for v in value.values()], budget)
        out: dict[str, Any] = {}
        for (key, item), share in zip(value.items(), shares):
            shrunk = _shrink(item, share)
            out[key] = shrunk
            if isinstance(item, str) and len(shrunk) != len(item):
                out[f"{key}_recording_truncated"] = {
                    "kept_chars": len(shrunk),
                    "total_chars": len(item),
                }
            elif isinstance(item, list) and len(shrunk) != len(item):
                out[f"{key}_recording_truncated"] = {
                    "kept": len(shrunk),
                    "total": len(item),
                }
        return out
    return value


def _shrink_list(items: list[Any], budget: int) -> tuple[list[Any], int]:
    """Shrink a list to fit `budget`. Returns (kept items, original length)."""
    if len(items) <= STRUCTURAL_LIST_MAX_LEN:
        shares = _shares([_sizeof(item) for item in items], budget)
        return [_shrink(item, share) for item, share in zip(items, shares)], len(items)

    kept: list[Any] = []
    used = 0
    for item in items:
        shrunk = _shrink(item, budget)
        size = _sizeof(shrunk)
        if kept and used + size > budget:
            break
        kept.append(shrunk)
        used += size
    return kept, len(items)


def _fit_content(content: dict[str, Any]) -> dict[str, Any]:
    """Bring a step's content under the item-size budget, truthfully.

    The buyer records a tool's output as the JSON *string* the tool returned
    (reasoning_hooks reads the result's text block), so an oversized payload
    has to be parsed before it can be shrunk structurally — truncating the
    string instead would leave invalid JSON that no reader could attribute to
    a seller.
    """
    original_bytes = _sizeof(content)
    if original_bytes <= MAX_CONTENT_BYTES:
        return content

    fitted = dict(content)
    output = fitted.get("output")
    parsed_output = None
    if isinstance(output, str):
        try:
            parsed_output = json.loads(output)
        except (json.JSONDecodeError, TypeError):
            parsed_output = None

    # Headroom for the `_recording` note and the step's other fields.
    budget = MAX_CONTENT_BYTES - 20_000
    if parsed_output is not None:
        # Stored as a map, not as a re-serialised JSON string. Every quote in a
        # string payload has to be escaped when that string is itself written
        # inside an item, which nearly doubles it — a payload trimmed to 283KB
        # measured 511KB once escaped, so it still failed to fit. Written as a
        # map it costs what it weighs. Readers already accept either form (the
        # seller-side recorder has always written objects).
        fitted["output"] = _shrink(parsed_output, budget)
    else:
        fitted = _shrink(fitted, budget)

    fitted["_recording"] = {
        "truncated": True,
        "original_bytes": original_bytes,
        "reason": f"step content exceeded the {MAX_CONTENT_BYTES} byte recording budget",
    }

    # Shrinking is bounded per list and per string, not globally, so a payload
    # with very many small entries can still be over budget. Say so rather
    # than attempting a write that DynamoDB will reject.
    if _sizeof(fitted) > MAX_CONTENT_BYTES:
        keep = {
            k: v
            for k, v in content.items()
            if k in ("toolName", "callId", "status", "durationMs", "from", "to")
        }
        keep["_not_recorded"] = {
            "original_bytes": original_bytes,
            "reason": "payload too large to record, even after trimming",
        }
        return keep
    return fitted


def _extract_plan_ids(content: dict[str, Any]) -> set[str]:
    """The AdCP plan id(s) a step's content refers to, if any.

    plan_id is never a top-level item attribute in AdCP payloads — it lives inside a tool call's
    input (`plan_id`, or `plan_ids` for get_plan_audit_logs) or a tool result's output
    (`plan_id`, or `plans[].plan_id`). This reads all of those, tolerantly: the output may be the
    JSON string the tool returned or an already-parsed map, and anything that is not one of the known
    shapes contributes nothing rather than guessing.
    """
    ids: set[str] = set()

    def add(value: Any) -> None:
        if isinstance(value, str) and value.strip():
            ids.add(value.strip())

    inp = content.get("input")
    if isinstance(inp, dict):
        add(inp.get("plan_id"))
        plan_ids = inp.get("plan_ids")
        if isinstance(plan_ids, list):
            for value in plan_ids:
                add(value)

    output = content.get("output")
    parsed = output
    if isinstance(output, str):
        try:
            parsed = json.loads(output)
        except (json.JSONDecodeError, TypeError):
            parsed = None
    if isinstance(parsed, dict):
        add(parsed.get("plan_id"))
        plans = parsed.get("plans")
        if isinstance(plans, list):
            for plan in plans:
                if isinstance(plan, dict):
                    add(plan.get("plan_id"))

    return ids


def _link_plans(tbl: Any, session_id: str, plan_ids: set[str]) -> None:
    """Record that `session_id` worked on each of `plan_ids`.

    Writes one plan-link item per plan (pk=PLAN#<plan_id>, sk=SESSION#<session_id>) so the plan can
    be found across every session it appeared in, and stamps the ids onto the session's META
    (`plan_ids` set, plus `latest_plan_id` when the step named exactly one) so the sessions list can
    show which plan a conversation belongs to.

    Only for buyer conversations: a seller's child recording is the seller's own view, not part of
    the buyer's plan journey.
    """
    if str(session_id).startswith(SELLER_SESSION_PREFIX):
        return

    now = _now_iso()
    ttl = int(time.time()) + TTL_SECONDS
    for plan_id in plan_ids:
        tbl.put_item(
            Item={
                "pk": f"PLAN#{plan_id}",
                "sk": f"SESSION#{session_id}",
                "plan_id": plan_id,
                "session_id": session_id,
                "gsi1pk": PLAN_GSI1_PK_VALUE,
                # Last activity, refreshed each time the plan is touched in this session, so
                # `list_recent_plans` orders by most-recent work — the same choice META's gsi1sk makes.
                "gsi1sk": now,
                "updated_at": now,
                "ttl": ttl,
            }
        )

    update_expr = "SET updated_at = :now ADD plan_ids :p"
    values: dict[str, Any] = {":now": now, ":p": set(plan_ids)}
    if len(plan_ids) == 1:
        # "latest" is only unambiguous when the step named a single plan. A multi-plan step (e.g.
        # get_plan_audit_logs over several) adds them all to the set but does not claim one is current.
        update_expr = "SET updated_at = :now, latest_plan_id = :lp ADD plan_ids :p"
        values[":lp"] = next(iter(plan_ids))
    tbl.update_item(
        Key={"pk": f"SESSION#{session_id}", "sk": "META"},
        UpdateExpression=update_expr,
        ExpressionAttributeValues=values,
    )


def record_step(session_id: str, step_type: StepType, content: dict[str, Any]) -> int:
    """Append one reasoning step, atomically assigning it the next step_index
    for this session (via an atomic counter on the META item, so concurrent
    tool calls in the same turn never collide on ordering).

    Returns the assigned step_index.
    """
    tbl = table()
    counter_resp = tbl.update_item(
        Key={"pk": f"SESSION#{session_id}", "sk": "META"},
        UpdateExpression="SET step_counter = if_not_exists(step_counter, :zero) + :one, updated_at = :now",
        ExpressionAttributeValues={":zero": 0, ":one": 1, ":now": _now_iso()},
        ReturnValues="UPDATED_NEW",
    )
    step_index = int(counter_resp["Attributes"]["step_counter"]) - 1

    ttl = int(time.time()) + TTL_SECONDS

    def item(step_content: dict[str, Any]) -> dict[str, Any]:
        return {
            "pk": f"SESSION#{session_id}",
            "sk": f"STEP#{step_index:06d}",
            "session_id": session_id,
            "step_index": step_index,
            "step_type": step_type,
            "timestamp": _now_ms(),
            "content": to_dynamo(step_content),
            "ttl": ttl,
        }

    try:
        tbl.put_item(Item=item(_fit_content(content)))
    except Exception as exc:  # noqa: BLE001 - see below; re-raised after the marker
        # The index has already been consumed by the atomic counter above, so
        # giving up here leaves a permanent hole in the session: the UI raises a
        # tool card on the tool_call step and never sees the result, so the card
        # spins for ever. Write a small marker in that slot instead — it states
        # that the step could not be recorded and why, which is the truth, and
        # lets the reader close the card.
        marker = {
            k: v
            for k, v in content.items()
            if k in ("toolName", "callId", "status", "durationMs", "from", "to")
        }
        marker["_not_recorded"] = {
            "reason": f"{type(exc).__name__}: {exc}"[:400],
            "original_bytes": _sizeof(content),
        }
        try:
            tbl.put_item(Item=item(marker))
        except Exception:  # noqa: BLE001 - nothing further can be done here
            raise exc from None
        raise

    # The step is recorded; now index any plan it referred to. Best-effort and after the fact: a
    # failure to link a plan must not lose the step that already wrote successfully, and reaching
    # here means the put above did not raise.
    try:
        plan_ids = _extract_plan_ids(content)
        if plan_ids:
            _link_plans(tbl, session_id, plan_ids)
    except Exception:  # noqa: BLE001 - plan indexing is an add-on, never the reason a step fails
        pass

    return step_index
