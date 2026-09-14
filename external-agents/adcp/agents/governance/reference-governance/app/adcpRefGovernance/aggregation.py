"""Cross-plan committed-budget aggregate: the fragmentation defense.

A per-plan budget check cannot stop a buyer splitting one over-authority buy across many plans that are
each individually within authority. This module is what makes that visible: committed spend is summed
across plans for one `(buyer_agent, seller_agent, account_id)` relationship over a rolling 30 days.

    PK                          SK                  what it is
    AGG#<buyer|seller|account>   DAY#<YYYY-MM-DD>    that day's committed total

Four properties are load-bearing, and each closes a hole that would otherwise reopen quietly:

**Daily buckets, not one fixed 30-day bucket.** A fixed window resets at its boundary, so a buyer at
authority on day 29 is clear again on day 31. Daily buckets read over a trailing `SK` range are
genuinely rolling, and still one request.

**Strongly consistent reads.** An eventually consistent read can report a commit written seconds ago as
absent, under-count spend, and approve a buy that should have been denied. This is also why a GSI was
rejected: a GSI is only ever eventually consistent.

**No decrement operation.** Stickiness is structural rather than a rule someone has to remember. A
helper that subtracted would undo the whole defense, so its absence is the enforcement. Reversing a
double-count is instead prevented upstream, by idempotency on `report_plan_outcome`. The spec states the
same rule and the same reason: under-delivery, cancellation, makegoods and post-approval reductions MUST
NOT decrement, or a buyer frees fragmentation headroom by cancelling an approved commit and immediately
re-committing sub-threshold.

**The key and the window are the spec's, not choices.** AdCP requires aggregation keyed on
`(buyer_agent, seller_agent, account_id)` across every spend-commit task, over a trailing window the agent
declares as `governance.aggregation_window_days`. Delegated sub-agents share the *delegating* buyer's
aggregate -- `buyer_agent` is the delegating principal, never a sub-agent's own URL -- because a per-agent
window would reopen the hole this closes.

**A failed read is never zero.** It returns `AggregateRead(None, reason)`, and the budget category
degrades to reduced confidence with an `uncertainty_reason`. Reporting zero headroom we could not read
is the one outcome this module must never produce.

Reads are bounded and may degrade; that is deliberate. An unbounded read inside a synchronous check is
how one slow dependency becomes a stalled buy with no error to show.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config

_log = logging.getLogger(__name__)

TABLE_ENV = "STATE_TABLE_NAME"

#: The trailing window, in days. Read as a trailing `SK` range, so it slides with wall-clock time rather
#: than snapping to plan or calendar boundaries.
#:
#: **This is the same value `get_adcp_capabilities` declares as
#: `governance.aggregation_window_days`** (M9.2), and the declaration reads it from here. AdCP requires the
#: declaration and gives omission a defined meaning: a buyer seeing no declared window MUST assume
#: per-commit evaluation only, i.e. that this agent has no fragmentation defence. A declared window that
#: disagreed with the implemented one would be worse than none, since a buyer would rely on it.
#:
#: Schema bounds are [1, 365].
AGGREGATION_WINDOW_DAYS = 30

#: Retained for readability inside this module.
WINDOW_DAYS = AGGREGATION_WINDOW_DAYS

#: How long a day bucket is kept. Longer than the window on purpose: expiring a bucket the moment it
#: leaves the window destroys the evidence for why a past denial happened, which is the first thing
#: anyone asks when a denial is disputed.
RETENTION_DAYS = 90

#: Total wall-clock budget for one aggregate read, as seen by the caller.
READ_BUDGET_SECONDS = 3.0

#: Socket-level bounds. These alone do NOT bound total elapsed time -- botocore retries, and in
#: `standard` mode adds jittered backoff between attempts, so `read_timeout` describes one attempt and
#: the real worst case is roughly attempts x (connect + read) + backoff. The hard total bound comes
#: from `asyncio.wait_for` in `read()`; these keep the work underneath it small so an abandoned attempt
#: does not linger.
_CONNECT_TIMEOUT_SECONDS = 0.5
_READ_TIMEOUT_SECONDS = 1.0
_MAX_ATTEMPTS = 2

#: Consecutive failures before the breaker opens, and how long it stays open.
#:
#: Per-instance state is acceptable here because an open breaker and an expired timeout produce the
#: identical outcome downstream -- the aggregate is unknown and the budget category degrades. So a
#: breaker that opens when it should not costs accuracy in one category and can never cause an unsafe
#: approval. That asymmetry is what makes this an optimisation rather than a correctness mechanism.
_BREAKER_THRESHOLD = 3
_BREAKER_COOLDOWN_SECONDS = 30.0

_table = None
_consecutive_failures = 0
_breaker_opened_at: float | None = None


@dataclass(frozen=True)
class AggregateRead:
    """Committed spend for one relationship, or the reason it could not be read.

    `committed is None` means **unknown**; `committed == 0.0` means genuinely nothing committed. The two
    are different facts and the distinction is the point -- see `reason`, which is populated only for
    the first.

    `reason` is composed here, where the failure is observed. Timeout, throttle and breaker-open are
    different explanations, and `uncertainty_reason` is caller-visible, so a consumer handed only
    `None` would have to invent text about a failure it never saw.
    """

    committed: float | None
    reason: str | None = None

    @property
    def known(self) -> bool:
        return self.committed is not None


def table():
    """The table resource for aggregate access, built on first use with explicit timeouts."""
    global _table
    if _table is None:
        name = os.environ.get(TABLE_ENV)
        if not name:
            raise RuntimeError(f"{TABLE_ENV} is not set")
        _table = boto3.resource(
            "dynamodb",
            config=Config(
                connect_timeout=_CONNECT_TIMEOUT_SECONDS,
                read_timeout=_READ_TIMEOUT_SECONDS,
                retries={"max_attempts": _MAX_ATTEMPTS, "mode": "standard"},
            ),
        ).Table(name)
    return _table


def reset_for_tests() -> None:
    """Drop the cached resource and the breaker state."""
    global _table, _consecutive_failures, _breaker_opened_at
    _table = None
    _consecutive_failures = 0
    _breaker_opened_at = None


def aggregate_key(*, buyer_agent: str, seller_agent: str, account_id: str) -> str:
    """The spending relationship this aggregate is scoped to.

    All three parts are required. A key missing one would merge distinct relationships and deny buys
    that have nothing to do with each other.
    """
    return f"AGG#{buyer_agent}|{seller_agent}|{account_id}"


def _day(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).date().isoformat()


def _window_bounds(now: datetime) -> tuple[str, str]:
    today = now.astimezone(timezone.utc).date()
    return (today - timedelta(days=WINDOW_DAYS - 1)).isoformat(), today.isoformat()


def _breaker_is_open(now: float) -> bool:
    global _breaker_opened_at, _consecutive_failures
    if _breaker_opened_at is None:
        return False
    if now - _breaker_opened_at < _BREAKER_COOLDOWN_SECONDS:
        return True
    # Cooldown elapsed: let exactly one attempt through to see whether the dependency recovered.
    _breaker_opened_at = None
    _consecutive_failures = 0
    return False


def _record_failure() -> None:
    global _consecutive_failures, _breaker_opened_at
    _consecutive_failures += 1
    if _consecutive_failures >= _BREAKER_THRESHOLD and _breaker_opened_at is None:
        _breaker_opened_at = time.monotonic()
        _log.warning(
            "aggregate breaker opened after %d consecutive failures; "
            "budget checks will report uncertainty for %.0fs",
            _consecutive_failures,
            _BREAKER_COOLDOWN_SECONDS,
        )


def _record_success() -> None:
    global _consecutive_failures, _breaker_opened_at
    _consecutive_failures = 0
    _breaker_opened_at = None


def _query_window(key: str, first_day: str, last_day: str) -> float:
    """Sum the day buckets in the window. Strongly consistent, one request.

    No filter for expired items. DynamoDB deletes them within a few days of expiry rather than at
    expiry, so an expired item can still be returned by a read in general -- but a bucket old enough to
    have expired is already outside this `SK` range and was never selected. The idempotency store does
    need such a filter; this does not.
    """
    total = 0.0
    kwargs: dict[str, Any] = {
        "KeyConditionExpression": Key("PK").eq(key)
        & Key("SK").between(f"DAY#{first_day}", f"DAY#{last_day}"),
        "ConsistentRead": True,
    }
    while True:
        response = table().query(**kwargs)
        for item in response.get("Items", []):
            amount = item.get("committed")
            if amount is not None:
                total += float(amount)
        start = response.get("LastEvaluatedKey")
        if not start:
            return total
        kwargs["ExclusiveStartKey"] = start


async def read(
    *,
    buyer_agent: str,
    seller_agent: str,
    account_id: str,
    now: datetime | None = None,
) -> AggregateRead:
    """Committed spend over the trailing window, or an explained unknown.

    Bounded at `READ_BUDGET_SECONDS` of caller-visible time. `asyncio.wait_for` is what enforces that:
    it cannot cancel the blocking client call, but it does bound how long the check waits, which is the
    property the requirement is about. An abandoned attempt finishes in its thread and its result is
    discarded.

    Never raises. Every failure path returns an `AggregateRead` with `committed=None`, because a caller
    that has to catch exceptions here will eventually catch one and substitute a zero.
    """
    if _breaker_is_open(time.monotonic()):
        return AggregateRead(None, "aggregate unavailable: recent repeated read failures")

    moment = now or datetime.now(timezone.utc)
    first_day, last_day = _window_bounds(moment)
    key = aggregate_key(
        buyer_agent=buyer_agent, seller_agent=seller_agent, account_id=account_id
    )

    try:
        total = await asyncio.wait_for(
            asyncio.to_thread(_query_window, key, first_day, last_day),
            timeout=READ_BUDGET_SECONDS,
        )
    except asyncio.TimeoutError:
        _record_failure()
        _log.warning("aggregate read exceeded %.1fs for %s", READ_BUDGET_SECONDS, key)
        return AggregateRead(
            None, f"aggregate read exceeded its {READ_BUDGET_SECONDS:.0f}s budget"
        )
    except Exception as exc:  # noqa: BLE001 -- every failure must degrade, never propagate
        _record_failure()
        _log.warning("aggregate read failed for %s: %s", key, type(exc).__name__)
        return AggregateRead(None, f"aggregate read failed: {type(exc).__name__}")

    _record_success()
    return AggregateRead(total)


def commit(
    *,
    buyer_agent: str,
    seller_agent: str,
    account_id: str,
    amount: float,
    occurred_at: datetime,
) -> None:
    """Add a confirmed commitment to its day bucket.

    Called from `report_plan_outcome`, never from `check_governance`: budget is consumed by confirmed
    outcomes, and an approval that is never acted on must not hold authority forever.

    The bucket comes from `occurred_at` -- the outcome's own timestamp -- not from wall-clock now, so a
    late-arriving or replayed outcome lands on the day it belongs to.

    `ADD` is atomic, so simultaneous commits on the same bucket cannot lose an update. Optimistic
    locking is deliberately absent: with no decrement there is no read-modify-write to race, and a
    version check would add a failure mode without removing one.

    `expires_at` must be a **Number** in epoch seconds. DynamoDB ignores a TTL attribute of any other
    type, so a string here would not error -- the items would simply never expire.
    """
    key = aggregate_key(
        buyer_agent=buyer_agent, seller_agent=seller_agent, account_id=account_id
    )
    expires_at = int(time.time()) + RETENTION_DAYS * 86400
    table().update_item(
        Key={"PK": key, "SK": f"DAY#{_day(occurred_at)}"},
        UpdateExpression="ADD committed :amount SET expires_at = :expires",
        ExpressionAttributeValues={
            ":amount": Decimal(str(float(amount))),
            ":expires": expires_at,
        },
    )
