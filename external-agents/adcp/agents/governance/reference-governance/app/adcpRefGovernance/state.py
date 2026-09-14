"""Durable state: plans, checks and outcomes.

One DynamoDB table, single-table style, mirroring what both sellers do. Keys:

    PK                    SK                     what it is
    PLAN#<plan_id>        META                   the synced plan, plus its version
    PLAN#<plan_id>        CHECK#<check_id>       one governance check and its verdict
    PLAN#<plan_id>        OUTCOME#<outcome_id>   one reported outcome, with committed budget

Why the state matters rather than being incidental: **committed budget is tracked from reported outcomes,
not from approved checks.** AdCP is explicit about that, and it is the difference between an agent that
knows what was spent and one that knows what was permitted. An approval that is never acted on must not
consume authority, and an approval acted on for a different amount than requested must consume the
amount the seller actually confirmed.

The table resource is created lazily so importing this module makes no AWS call — the deploy scripts and
the tests both import it before any table exists.
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

import boto3
from boto3.dynamodb.conditions import Key
from pydantic import AnyUrl

TABLE_ENV = "STATE_TABLE_NAME"

_table = None


def table():
    """The table resource, built on first use."""
    global _table
    if _table is None:
        name = os.environ.get(TABLE_ENV)
        if not name:
            raise RuntimeError(f"{TABLE_ENV} is not set")
        _table = boto3.resource("dynamodb").Table(name)
    return _table


def reset_for_tests() -> None:
    """Drop the cached resource. Tests point at a fresh mock table per case."""
    global _table
    _table = None


def _numbers_to_decimal(value: Any) -> Any:
    """DynamoDB rejects floats. Converts on the way in, recursively.

    Via `str` rather than `Decimal(float)`, because the binary form of 0.1 is not 0.1 and a budget that
    arrives as 100000.0 must be stored as 100000 rather than as something microscopically different that
    then fails an equality check against the plan.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _numbers_to_decimal(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_numbers_to_decimal(v) for v in value]
    # Pydantic's scalar wrappers. `AnyUrl`, `datetime`, `date` and `Enum` all reach here from SDK models
    # and DynamoDB accepts none of them. Converted narrowly, by type, so a genuinely unexpected
    # structure still raises rather than being flattened into a string that looks like data.
    if isinstance(value, Enum):
        return _numbers_to_decimal(value.value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (AnyUrl, UUID)):
        return str(value)
    return value


def _decimals_to_numbers(value: Any) -> Any:
    """The inverse, for values on their way back out to JSON."""
    if isinstance(value, Decimal):
        as_float = float(value)
        return int(as_float) if as_float.is_integer() else as_float
    if isinstance(value, dict):
        return {k: _decimals_to_numbers(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decimals_to_numbers(v) for v in value]
    return value


def put_plan(
    plan: dict[str, Any],
    *,
    requires_human_review: bool = False,
    buyer_principal: str | None = None,
    account_id: str | None = None,
) -> dict[str, Any]:
    """Store a plan, incrementing its version when it already exists.

    AdCP says re-syncing the same `plan_id` updates it and increments the version, so the version is
    read then written rather than supplied by the caller — a caller-supplied version could silently
    overwrite a later amendment.

    `requires_human_review` is the caller-independent half: the agent's own policy resolution can
    demand review even when the plan document does not ask for it. AdCP requires exactly that —
    a governance agent sets the flag automatically when any resolved policy carries
    `requires_human_review`.

    Suspension is **sticky across a re-sync, including one that drops the flag.** The plan document is
    the caller's assertion; suspension is this agent's conclusion about it, and only
    `resolve_escalation` clears it. Without this a caller could un-suspend its own plan by re-syncing
    without the flag, which would make mandatory human review optional at the caller's discretion.
    """
    plan_id = str(plan["plan_id"])
    existing = get_plan(plan_id)
    version = int(existing.get("version", 0)) + 1 if existing else 1
    human_review_required = bool(plan.get("human_review_required")) or requires_human_review
    suspended = bool(existing.get("suspended")) if existing else False
    record = {
        "PK": f"PLAN#{plan_id}",
        "SK": "META",
        "plan_id": plan_id,
        "version": version,
        "updated_at": int(time.time()),
        "plan": _numbers_to_decimal(plan),
        "human_review_required": human_review_required,
        "suspended": suspended or human_review_required,
    }

    # The authenticated buyer, recorded here because this is the only point at which it is available.
    #
    # AdCP keys the committed-spend aggregate on `(buyer_agent, seller_agent, account_id)`, but neither
    # `report_plan_outcome` nor `sync_plans` carries a caller in its body, and no `Plan` field names the
    # buyer -- the account is a property of the authenticated credential. `sync_plans` is the buyer's own
    # call, so the principal that syncs a plan IS that plan's buyer. Persisting it is what lets the
    # seller-side execution check and the outcome report resolve the buyer element later.
    #
    # Carried forward on re-sync when a later call arrives without context, so a plan does not lose its
    # buyer identity and silently drop out of the aggregate.
    for field, value in (("buyer_principal", buyer_principal), ("account_id", account_id)):
        resolved = value or (existing or {}).get(field)
        if resolved:
            record[field] = str(resolved)

    table().put_item(Item=record)
    return {"plan_id": plan_id, "version": version}


def get_plan(plan_id: str) -> dict[str, Any] | None:
    """The stored plan, or None. None means "never synced", which is `PLAN_NOT_FOUND`, not an empty plan."""
    response = table().get_item(Key={"PK": f"PLAN#{plan_id}", "SK": "META"})
    item = response.get("Item")
    if not item:
        return None
    return _decimals_to_numbers(item)


def put_check(
    *,
    plan_id: str,
    verdict: str,
    explanation: str,
    payload: dict[str, Any] | None = None,
    findings: list[dict[str, Any]] | None = None,
    categories_evaluated: list[str] | None = None,
    policies_evaluated: list[str] | None = None,
) -> str:
    """Record a check and return its `check_id`, which links an outcome back to its authorisation.

    `findings`, `categories_evaluated` and `policies_evaluated` are persisted because
    `get_plan_audit_logs` has to report them later, and recomputing an evaluation at read time would
    report today's policy against yesterday's decision. `summary.findings_count` and
    `drift_metrics.mean_confidence` are derived from what is stored here, so a check whose findings
    were not persisted contributes nothing to them rather than a guess.
    """
    check_id = f"chk_{uuid.uuid4().hex[:16]}"
    item: dict[str, Any] = {
        "PK": f"PLAN#{plan_id}",
        "SK": f"CHECK#{check_id}",
        "check_id": check_id,
        "plan_id": plan_id,
        "verdict": verdict,
        "explanation": explanation,
        "created_at": int(time.time()),
        "payload": _numbers_to_decimal(payload or {}),
    }
    # Absent rather than empty: "this check recorded no findings" and "this check predates findings
    # being persisted" are different facts, and only the first should read as an evaluated zero.
    if findings is not None:
        item["findings"] = _numbers_to_decimal(findings)
    if categories_evaluated is not None:
        item["categories_evaluated"] = list(categories_evaluated)
    if policies_evaluated is not None:
        item["policies_evaluated"] = list(policies_evaluated)
    table().put_item(Item=item)
    return check_id


def put_outcome(
    *,
    plan_id: str,
    outcome: str,
    committed_budget: float | None,
    check_id: str | None,
    detail: dict[str, Any] | None = None,
) -> str:
    """Record an outcome. `committed_budget` is what actually consumes plan authority."""
    outcome_id = f"out_{uuid.uuid4().hex[:16]}"
    item: dict[str, Any] = {
        "PK": f"PLAN#{plan_id}",
        "SK": f"OUTCOME#{outcome_id}",
        "outcome_id": outcome_id,
        "plan_id": plan_id,
        "outcome": outcome,
        "created_at": int(time.time()),
        "detail": _numbers_to_decimal(detail or {}),
    }
    if check_id:
        item["check_id"] = check_id
    # Absent rather than zero when the outcome carried no amount. Zero is a real committed budget and
    # "no amount reported" is not, so storing 0 for the second would understate nothing and overstate
    # certainty.
    if committed_budget is not None:
        item["committed_budget"] = _numbers_to_decimal(float(committed_budget))
    table().put_item(Item=item)
    return outcome_id


def committed_total(plan_id: str) -> float:
    """Sum of committed budget across a plan's outcomes.

    Only `completed` outcomes count. A `failed` outcome consumed no budget, and counting it would
    permanently strand authority the buyer never spent.
    """
    total = 0.0
    for item in _query(plan_id, "OUTCOME#"):
        if item.get("outcome") != "completed":
            continue
        amount = item.get("committed_budget")
        if amount is None:
            continue
        total += float(amount)
    return total


def list_checks(plan_id: str) -> list[dict[str, Any]]:
    return [_decimals_to_numbers(item) for item in _query(plan_id, "CHECK#")]


def list_outcomes(plan_id: str) -> list[dict[str, Any]]:
    return [_decimals_to_numbers(item) for item in _query(plan_id, "OUTCOME#")]


def _query(plan_id: str, sk_prefix: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {
        "KeyConditionExpression": Key("PK").eq(f"PLAN#{plan_id}")
        & Key("SK").begins_with(sk_prefix)
    }
    while True:
        response = table().query(**kwargs)
        items.extend(response.get("Items", []))
        start = response.get("LastEvaluatedKey")
        if not start:
            return items
        kwargs["ExclusiveStartKey"] = start


# --------------------------------------------------------------------------------------- escalations
#
# An escalation is the record of a plan awaiting human review. It is also what makes suspension
# *visible* on the wire: `sync_plans_response.Status` has no `suspended` member (SDK-GAP-3), so rather
# than invent an enum value the SDK would reject, an unresolved escalation in
# `summary.escalations[]` IS the suspension -- and it carries `reason` too, which a status flag would
# not.
#
# Same partition as the plan, so one Query serves plan, checks, outcomes and escalations for the audit
# view. That is the reason Q2 rejected a GSI: a GSI is only ever eventually consistent.
#
# Unresolved is `resolved_at is None`. There is no separate status field and none is needed.


def put_escalation(*, plan_id: str, check_id: str, reason: str) -> None:
    """Open an escalation against the check that triggered it.

    `check_id` is required by the SDK's `Escalation` model, so callers must record a real check first.
    At `sync_plans` time no check has happened yet, which is why a registration check is written and
    its id used here: an id pointing at nothing would make `human_override_rate` uncomputable, since
    there would be no agent recommendation to compare the human's decision against.
    """
    table().put_item(
        Item={
            "PK": f"PLAN#{plan_id}",
            "SK": f"ESCALATION#{check_id}",
            "plan_id": plan_id,
            "check_id": check_id,
            "reason": reason,
            "created_at": int(time.time()),
        }
    )


def list_escalations(plan_id: str) -> list[dict[str, Any]]:
    return [_decimals_to_numbers(item) for item in _query(plan_id, "ESCALATION#")]


def unresolved_escalations(plan_id: str) -> list[dict[str, Any]]:
    return [item for item in list_escalations(plan_id) if not item.get("resolved_at")]


def resolve_escalation(*, plan_id: str, check_id: str, resolution: str) -> bool:
    """Record a human decision and clear the plan's suspension when nothing else is outstanding.

    Returns False when the escalation does not exist, so a caller can tell "resolved" from "there was
    nothing to resolve" rather than reporting success either way.

    Called only by the operator script. There is no AdCP task for this, deliberately:
    `CAMPAIGN_SUSPENDED`'s own recovery hint is "contact the plan operator", which places the human
    outside the protocol.
    """
    key = {"PK": f"PLAN#{plan_id}", "SK": f"ESCALATION#{check_id}"}
    if not table().get_item(Key=key).get("Item"):
        return False
    table().update_item(
        Key=key,
        UpdateExpression="SET resolution = :r, resolved_at = :t",
        ExpressionAttributeValues={":r": resolution, ":t": int(time.time())},
    )
    _apply_resolved_verdict(plan_id=plan_id, check_id=check_id, resolution=resolution)
    if not unresolved_escalations(plan_id):
        table().update_item(
            Key={"PK": f"PLAN#{plan_id}", "SK": "META"},
            UpdateExpression="SET suspended = :f",
            ExpressionAttributeValues={":f": False},
        )
    return True


#: The human decisions this agent understands, from AdCP's own `Escalation.resolution` examples. A
#: resolution outside this map leaves the check's verdict alone rather than guessing at what was decided.
_RESOLVED_VERDICTS = {"approved_by_human": "approved", "rejected_by_human": "denied"}


def _apply_resolved_verdict(*, plan_id: str, check_id: str, resolution: str) -> None:
    """Record the human's decision as the check's verdict, PRESERVING the agent's recommendation.

    The spec says an internally-reviewed check "eventually resolves to approved or denied", so the audit
    trail's verdict for a reviewed check has to be what the human decided -- otherwise a plan a human
    approved reads as denied forever.

    But overwriting in place would destroy the only other half of the record. `human_override_rate`
    compares the human's decision against the agent's recommendation, and if the recommendation is
    replaced by the decision the two can never disagree: the metric would read 0.0 for every plan,
    reporting perfect calibration precisely when it was being reversed. So the original moves to
    `recommended_verdict` first, written only if absent so a second resolution cannot overwrite it with
    the first human's answer.
    """
    final = _RESOLVED_VERDICTS.get(resolution)
    if final is None:
        return
    key = {"PK": f"PLAN#{plan_id}", "SK": f"CHECK#{check_id}"}
    item = table().get_item(Key=key).get("Item")
    if not item:
        return
    if item.get("recommended_verdict") is None:
        table().update_item(
            Key=key,
            UpdateExpression="SET recommended_verdict = :rec, verdict = :v",
            ExpressionAttributeValues={":rec": item.get("verdict"), ":v": final},
        )
        return
    table().update_item(
        Key=key,
        UpdateExpression="SET verdict = :v",
        ExpressionAttributeValues={":v": final},
    )


def is_suspended(plan_id: str) -> bool:
    """Whether the plan is suspended. A plan that was never synced is not suspended -- it is absent,
    which `check_governance` reports as `PLAN_NOT_FOUND` rather than as a suspension."""
    plan = get_plan(plan_id)
    return bool(plan and plan.get("suspended"))
