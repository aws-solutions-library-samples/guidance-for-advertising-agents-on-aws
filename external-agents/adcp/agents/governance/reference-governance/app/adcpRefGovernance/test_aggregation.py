"""Tests for the cross-plan committed-budget aggregate.

The assertions weighted most heavily are the ones proving a failed read cannot become a zero. That is
the specific way this defense would fail quietly: an aggregate that reads as 0 approves everything, and
nothing in the response would say the number was never actually read.

Also pinned: the absence of a decrement. A helper that subtracted would undo the defense, and the test
exists so adding one is a visible decision rather than a convenience.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3
import pytest
from moto import mock_aws

import aggregation

TABLE = "adcp-reference-governance-state-test"

BUYER = "https://buyer.example"
SELLER = "https://seller.example"
ACCOUNT = "acct-1"

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def store(monkeypatch):
    with mock_aws():
        monkeypatch.setenv(aggregation.TABLE_ENV, TABLE)
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
        aggregation.reset_for_tests()
        boto3.resource("dynamodb").create_table(
            TableName=TABLE,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield
        aggregation.reset_for_tests()


def _commit(amount: float, when: datetime) -> None:
    aggregation.commit(
        buyer_agent=BUYER,
        seller_agent=SELLER,
        account_id=ACCOUNT,
        amount=amount,
        occurred_at=when,
    )


async def _read(now: datetime = NOW) -> aggregation.AggregateRead:
    return await aggregation.read(
        buyer_agent=BUYER, seller_agent=SELLER, account_id=ACCOUNT, now=now
    )


# ------------------------------------------------------------------------------------------- reading


async def test_nothing_committed_reads_as_a_known_zero(store):
    """Zero is a real answer, and distinguishable from "could not read"."""
    result = await _read()
    assert result.committed == 0.0
    assert result.known is True
    assert result.reason is None


async def test_commits_sum_across_days_inside_the_window(store):
    _commit(10_000.0, NOW)
    _commit(5_000.0, NOW - timedelta(days=5))
    _commit(1_000.0, NOW - timedelta(days=29))

    result = await _read()
    assert result.committed == 16_000.0
    assert result.known is True


async def test_a_commit_older_than_the_window_is_excluded(store):
    """The rolling boundary: 29 days ago counts, 30 does not."""
    _commit(1_000.0, NOW - timedelta(days=29))
    _commit(9_999.0, NOW - timedelta(days=30))

    assert (await _read()).committed == 1_000.0


async def test_the_window_rolls_rather_than_resetting(store):
    """A fixed 30-day bucket would drop this commit at a period boundary; a rolling window does not."""
    _commit(2_000.0, NOW - timedelta(days=20))

    assert (await _read(now=NOW)).committed == 2_000.0
    assert (await _read(now=NOW + timedelta(days=5))).committed == 2_000.0
    # ...and leaves only once it is genuinely 30 days behind.
    assert (await _read(now=NOW + timedelta(days=15))).committed == 0.0


async def test_reads_are_strongly_consistent(store, monkeypatch):
    """An eventually consistent read can miss a just-written commit, under-count, and approve."""
    seen = {}
    real_query = aggregation.table().query

    def spy(**kwargs):
        seen.update(kwargs)
        return real_query(**kwargs)

    monkeypatch.setattr(aggregation.table(), "query", spy)
    await _read()
    assert seen["ConsistentRead"] is True


# ------------------------------------------------------------------------------------------ committing


async def test_the_bucket_comes_from_the_outcome_timestamp_not_wall_clock(store):
    """A replayed or late outcome must land on the day it belongs to."""
    _commit(700.0, NOW - timedelta(days=3))

    item = boto3.resource("dynamodb").Table(TABLE).get_item(
        Key={
            "PK": aggregation.aggregate_key(
                buyer_agent=BUYER, seller_agent=SELLER, account_id=ACCOUNT
            ),
            "SK": f"DAY#{(NOW - timedelta(days=3)).date().isoformat()}",
        }
    )["Item"]
    assert float(item["committed"]) == 700.0


async def test_concurrent_commits_on_one_day_both_land(store):
    """`ADD` is atomic, so neither update is lost."""
    _commit(100.0, NOW)
    _commit(250.0, NOW)
    assert (await _read()).committed == 350.0


async def test_expires_at_is_a_number(store):
    """DynamoDB ignores a TTL attribute of any other type -- a string would silently never expire."""
    _commit(1.0, NOW)
    item = boto3.resource("dynamodb").Table(TABLE).get_item(
        Key={
            "PK": aggregation.aggregate_key(
                buyer_agent=BUYER, seller_agent=SELLER, account_id=ACCOUNT
            ),
            "SK": f"DAY#{NOW.date().isoformat()}",
        }
    )["Item"]
    assert isinstance(item["expires_at"], Decimal)
    assert int(item["expires_at"]) > 0


async def test_retention_outlives_the_window(store):
    """Expiring a bucket as it leaves the window would destroy the evidence for a past denial."""
    assert aggregation.RETENTION_DAYS > aggregation.WINDOW_DAYS


# --------------------------------------------------------------------------------- failure and degradation


async def test_a_failed_read_is_unknown_and_never_zero(store, monkeypatch):
    def boom(**_kwargs):
        raise RuntimeError("throttled")

    monkeypatch.setattr(aggregation.table(), "query", boom)
    result = await _read()

    assert result.committed is None
    assert result.known is False
    assert result.reason and "failed" in result.reason
    assert result.committed != 0.0


async def test_a_timed_out_read_is_unknown_with_its_own_reason(store, monkeypatch):
    """Timeout and hard failure are different explanations, and `uncertainty_reason` shows the caller
    which one happened."""
    import time as _time

    def slow(**_kwargs):
        _time.sleep(aggregation.READ_BUDGET_SECONDS + 0.5)
        return {"Items": []}

    monkeypatch.setattr(aggregation.table(), "query", slow)
    monkeypatch.setattr(aggregation, "READ_BUDGET_SECONDS", 0.2)

    result = await _read()
    assert result.committed is None
    assert "budget" in (result.reason or "")


async def test_the_read_never_raises(store, monkeypatch):
    """A caller forced to catch exceptions here will eventually catch one and substitute a zero."""

    def boom(**_kwargs):
        raise KeyError("unexpected shape")

    monkeypatch.setattr(aggregation.table(), "query", boom)
    result = await _read()  # must not raise
    assert result.known is False


async def test_the_breaker_opens_and_short_circuits(store, monkeypatch):
    calls = {"n": 0}

    def boom(**_kwargs):
        calls["n"] += 1
        raise RuntimeError("down")

    monkeypatch.setattr(aggregation.table(), "query", boom)

    for _ in range(aggregation._BREAKER_THRESHOLD):
        assert (await _read()).known is False
    attempts_before = calls["n"]

    result = await _read()
    assert result.known is False
    assert "repeated read failures" in (result.reason or "")
    assert calls["n"] == attempts_before, "an open breaker must not attempt the read"


async def test_the_breaker_probes_after_its_cooldown(store, monkeypatch):
    def boom(**_kwargs):
        raise RuntimeError("down")

    monkeypatch.setattr(aggregation.table(), "query", boom)
    for _ in range(aggregation._BREAKER_THRESHOLD):
        await _read()

    monkeypatch.setattr(aggregation, "_BREAKER_COOLDOWN_SECONDS", 0.0)
    monkeypatch.setattr(aggregation.table(), "query", lambda **_k: {"Items": []})

    assert (await _read()).committed == 0.0


async def test_a_recovered_read_resets_the_failure_count(store, monkeypatch):
    def boom(**_kwargs):
        raise RuntimeError("down")

    monkeypatch.setattr(aggregation.table(), "query", boom)
    await _read()
    monkeypatch.setattr(aggregation.table(), "query", lambda **_k: {"Items": []})
    await _read()
    assert aggregation._consecutive_failures == 0


# -------------------------------------------------------------------------------------------- structure


def test_there_is_no_decrement_operation():
    """M3.5: stickiness is structural. Adding a subtract helper must be a visible decision.

    A decrement would let an over-count be "corrected" by hand, and with it the guarantee that
    committed spend only ever grows -- which is what stops a buyer reclaiming authority by reporting a
    reversal.
    """
    forbidden = {"decrement", "subtract", "release", "refund", "uncommit", "reverse"}
    public = {name for name in dir(aggregation) if not name.startswith("_")}
    assert not (forbidden & public), f"unexpected reversal helper: {forbidden & public}"


def test_the_aggregate_key_needs_all_three_parties():
    """A key missing one part would merge distinct relationships and deny unrelated buys."""
    key = aggregation.aggregate_key(
        buyer_agent=BUYER, seller_agent=SELLER, account_id=ACCOUNT
    )
    assert BUYER in key and SELLER in key and ACCOUNT in key
    with pytest.raises(TypeError):
        aggregation.aggregate_key(buyer_agent=BUYER, seller_agent=SELLER)  # type: ignore[call-arg]
