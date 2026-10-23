"""Tests for the DynamoDB idempotency backend.

The one that carries the requirement is `test_an_expired_entry_is_not_served_even_though_it_is_still
_stored`. DynamoDB deletes an expired item within a few days of expiry rather than at expiry, so relying
on TTL for validity would replay a cached response long after `replay_ttl_seconds`. That is the exact
gap between "the item is gone" and "the item is no longer valid", and only the backend can close it.
"""

from __future__ import annotations

import time

import boto3
import pytest
from moto import mock_aws

import idempotency_backend
from adcp.server.idempotency import CachedResponse

TABLE = "adcp-reference-governance-state-test"

SCOPE = "tenant-a|buyer-1"
KEY = "outcome-abc123"


@pytest.fixture()
def store(monkeypatch):
    with mock_aws():
        monkeypatch.setenv(idempotency_backend.TABLE_ENV, TABLE)
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
        idempotency_backend.reset_for_tests()
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
        idempotency_backend.reset_for_tests()


def _entry(*, ttl: float = 3600.0, response: dict | None = None) -> CachedResponse:
    return CachedResponse(
        payload_hash="sha256:deadbeef",
        response=response if response is not None else {"status": "completed", "amount": 71400.0},
        expires_at_epoch=time.time() + ttl,
    )


async def test_a_missing_key_reads_as_none(store):
    backend = idempotency_backend.DynamoIdempotencyBackend()
    assert await backend.get(SCOPE, "never-seen") is None


async def test_an_entry_round_trips_within_its_ttl(store):
    backend = idempotency_backend.DynamoIdempotencyBackend()
    original = _entry()
    await backend.put(SCOPE, KEY, original)

    got = await backend.get(SCOPE, KEY)
    assert got is not None
    assert got.payload_hash == original.payload_hash
    assert got.response == original.response
    assert got.expires_at_epoch == int(original.expires_at_epoch)


async def test_an_expired_entry_is_not_served_even_though_it_is_still_stored(store):
    """The requirement DynamoDB TTL cannot satisfy.

    TTL removes an expired item within a few days, not at expiry. If validity were left to TTL, this
    entry would replay for days past its window.
    """
    backend = idempotency_backend.DynamoIdempotencyBackend()
    await backend.put(SCOPE, KEY, _entry(ttl=-1.0))

    # Still physically present...
    raw = boto3.resource("dynamodb").Table(TABLE).get_item(
        Key={"PK": f"IDEM#{SCOPE}", "SK": f"KEY#{KEY}"}
    )
    assert "Item" in raw

    # ...and correctly not served.
    assert await backend.get(SCOPE, KEY) is None


async def test_expiry_is_evaluated_against_the_injected_clock(store):
    """Pins that expiry is a comparison this backend makes, not a side effect of storage."""
    now = time.time()
    backend = idempotency_backend.DynamoIdempotencyBackend(clock=lambda: now)
    await backend.put(SCOPE, KEY, CachedResponse("h", {"a": 1}, now + 10))
    assert await backend.get(SCOPE, KEY) is not None

    later = idempotency_backend.DynamoIdempotencyBackend(clock=lambda: now + 11)
    assert await later.get(SCOPE, KEY) is None


async def test_expires_at_is_stored_as_a_number(store):
    """A non-Number TTL attribute is ignored by DynamoDB -- silently, with no error."""
    from decimal import Decimal

    backend = idempotency_backend.DynamoIdempotencyBackend()
    await backend.put(SCOPE, KEY, _entry())

    item = boto3.resource("dynamodb").Table(TABLE).get_item(
        Key={"PK": f"IDEM#{SCOPE}", "SK": f"KEY#{KEY}"}
    )["Item"]
    assert isinstance(item["expires_at"], Decimal)


async def test_a_float_in_the_response_survives_the_round_trip_unchanged(store):
    """Stored as JSON rather than a DynamoDB map on purpose.

    A map would return floats as `Decimal`, so the replayed response would not equal the one originally
    returned -- which is the single thing a replay has to guarantee.
    """
    backend = idempotency_backend.DynamoIdempotencyBackend()
    payload = {"committed_budget": 71400.55, "packages": [], "nested": {"rate": 0.1}}
    await backend.put(SCOPE, KEY, _entry(response=payload))

    got = await backend.get(SCOPE, KEY)
    assert got is not None
    assert got.response == payload
    assert isinstance(got.response["committed_budget"], float)


async def test_putting_the_same_key_overwrites(store):
    backend = idempotency_backend.DynamoIdempotencyBackend()
    await backend.put(SCOPE, KEY, _entry(response={"v": 1}))
    await backend.put(SCOPE, KEY, _entry(response={"v": 2}))

    got = await backend.get(SCOPE, KEY)
    assert got is not None and got.response == {"v": 2}


async def test_scopes_are_isolated(store):
    """`scope_key` carries the caller identity; two callers must not read each other's replays."""
    backend = idempotency_backend.DynamoIdempotencyBackend()
    await backend.put("tenant-a", KEY, _entry(response={"who": "a"}))
    await backend.put("tenant-b", KEY, _entry(response={"who": "b"}))

    a = await backend.get("tenant-a", KEY)
    b = await backend.get("tenant-b", KEY)
    assert a is not None and a.response == {"who": "a"}
    assert b is not None and b.response == {"who": "b"}


async def test_delete_expired_reports_what_it_actually_removed(store):
    """TTL owns reclamation, so this removes nothing and says so."""
    backend = idempotency_backend.DynamoIdempotencyBackend()
    await backend.put(SCOPE, KEY, _entry(ttl=-1.0))
    assert await backend.delete_expired() == 0
    # Correctness is unaffected: an expired entry is still never served.
    assert await backend.get(SCOPE, KEY) is None


def test_the_store_rejects_a_ttl_outside_the_spec_range(store):
    """AdCP bounds `replay_ttl_seconds` to [3600, 604800]; the SDK enforces it at construction, so a
    bad value fails at startup rather than on the first retry."""
    idempotency_backend.make_store(ttl_seconds=3600)
    idempotency_backend.make_store(ttl_seconds=604800)
    with pytest.raises(ValueError):
        idempotency_backend.make_store(ttl_seconds=60)


def test_hashing_and_conflict_semantics_are_not_reimplemented_here():
    """The SDK owns canonical hashing, replay and IDEMPOTENCY_CONFLICT.

    Checked against executable code only. Docstrings here discuss replay legitimately, so a plain text
    scan would flag the prose that explains the design rather than a reimplementation of it.
    """
    import ast
    import inspect
    from pathlib import Path

    tree = ast.parse(Path(inspect.getfile(idempotency_backend)).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body.pop(0)

    code = ast.unparse(tree)
    for smell in ("sha256", "hashlib", "IDEMPOTENCY_CONFLICT", "replayed"):
        assert smell not in code, f"{smell} belongs to the SDK, not this backend"
