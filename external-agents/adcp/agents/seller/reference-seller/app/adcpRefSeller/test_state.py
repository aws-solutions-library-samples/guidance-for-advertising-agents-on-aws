"""
Unit tests for state.py, against moto's mocked DynamoDB (no live AWS calls,
no DynamoDB Local process required — see design.md's "Testing approach").

Every test builds its own table (matching deploy_state_table.py's schema
exactly: pk/sk keys, on-demand billing) inside a moto mock context, so tests
never touch the real `adcp-reference-seller-state` table or need
STATE_TABLE_NAME/AWS credentials set.

Idempotency tests (task 2 rework) exercise `DynamoDBIdempotencyBackend`
two ways:

- Directly against the `IdempotencyBackend` contract (`get`/`put`) — the
  same shape moto lets us test without a live AWS call.
- Through the real `adcp.server.idempotency.IdempotencyStore.wrap()`
  decorator with a fake handler and a real `adcp.server.base.ToolContext` —
  this is the actual call pattern `main.py`'s task handlers (task 3+) will
  use, so a bug in wiring this backend to the SDK's expectations (payload
  hashing, per-principal scoping, conflict/replay semantics) gets caught
  here rather than surfacing later in task 3.

Validates: Requirements R1, R2, R3, R5, R6, R8 (task 2 of
.kiro/specs/seller-agent-adcp-compliance/tasks.md).
"""


from aws_region import region
import asyncio
import time

import boto3
import pytest
from adcp.exceptions import IdempotencyConflictError
from adcp.server.base import ToolContext
from adcp.server.idempotency import IdempotencyStore, canonical_json_sha256
from adcp.server.idempotency.backends import CachedResponse
from moto import mock_aws

from state import (
    AccountStore,
    DynamoDBIdempotencyBackend,
    IDEMPOTENCY_TTL_SECONDS,
    MediaBuyNotFoundError,
    MediaBuyStore,
    new_media_buy_id,
)

TABLE_NAME = "adcp-reference-seller-state-test"
REGION = region()

@pytest.fixture
def dynamo_table():
    """A fresh in-memory DynamoDB table per test, same key schema as
    deploy_state_table.py (pk/sk, on-demand billing — TTL is a describe_ttl
    API concept moto doesn't need for these tests since expiry-by-TTL isn't
    exercised here)."""
    with mock_aws():
        client = boto3.client("dynamodb", region_name=REGION)
        client.create_table(
            TableName=TABLE_NAME,
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        resource = boto3.resource("dynamodb", region_name=REGION)
        yield resource.Table(TABLE_NAME)


# ---------------------------------------------------------------------------
# DynamoDBIdempotencyBackend — direct backend-contract tests
# ---------------------------------------------------------------------------


class TestDynamoDBIdempotencyBackendContract:
    """Exercises `get`/`put` directly against the `IdempotencyBackend`
    abstract contract, independent of the SDK's `IdempotencyStore`
    coordinator — these tests catch backend-level bugs (key composition,
    TTL/expiry, first-writer-wins) without involving hashing/scoping, which
    the `IdempotencyStore.wrap()` tests below cover instead."""

    def test_get_missing_key_returns_none(self, dynamo_table):
        backend = DynamoDBIdempotencyBackend(table=dynamo_table)
        result = asyncio.run(backend.get("scope-a", "key-1"))
        assert result is None

    def test_put_then_get_round_trips(self, dynamo_table):
        backend = DynamoDBIdempotencyBackend(table=dynamo_table)
        entry = CachedResponse(
            payload_hash="hash-1",
            response={"media_buy_id": "mb_abc", "status": "completed"},
            expires_at_epoch=time.time() + IDEMPOTENCY_TTL_SECONDS,
        )
        asyncio.run(backend.put("scope-a", "key-1", entry))

        fetched = asyncio.run(backend.get("scope-a", "key-1"))
        assert fetched is not None
        assert fetched.payload_hash == "hash-1"
        assert fetched.response == {"media_buy_id": "mb_abc", "status": "completed"}

    def test_get_expired_entry_returns_none(self, dynamo_table):
        backend = DynamoDBIdempotencyBackend(table=dynamo_table)
        entry = CachedResponse(
            payload_hash="hash-1",
            response={"media_buy_id": "mb_abc"},
            expires_at_epoch=time.time() - 1,  # already expired
        )
        asyncio.run(backend.put("scope-a", "key-1", entry))

        assert asyncio.run(backend.get("scope-a", "key-1")) is None

    def test_different_scope_keys_do_not_collide(self, dynamo_table):
        """Two different scope_keys (i.e. two different buyers) using the
        same idempotency key must never see each other's cached entry —
        this is the per-principal scoping property the hand-rolled version
        this replaced was missing entirely."""
        backend = DynamoDBIdempotencyBackend(table=dynamo_table)
        entry_a = CachedResponse(
            payload_hash="hash-a",
            response={"media_buy_id": "mb_buyer_a"},
            expires_at_epoch=time.time() + IDEMPOTENCY_TTL_SECONDS,
        )
        asyncio.run(backend.put("buyer-a", "shared-key", entry_a))

        assert asyncio.run(backend.get("buyer-b", "shared-key")) is None
        fetched_a = asyncio.run(backend.get("buyer-a", "shared-key"))
        assert fetched_a is not None
        assert fetched_a.response == {"media_buy_id": "mb_buyer_a"}

    def test_concurrent_identical_writes_only_first_wins(self, dynamo_table):
        """Two `put()` calls racing to fill the same, still-empty
        `(scope_key, key)` slot: the first writer's row must stick — the
        second `put()` must not overwrite it (first-writer-wins, matching
        the abstract method's "don't lose data" contract)."""
        backend = DynamoDBIdempotencyBackend(table=dynamo_table)
        first_entry = CachedResponse(
            payload_hash="hash-1",
            response={"media_buy_id": "mb_first"},
            expires_at_epoch=time.time() + IDEMPOTENCY_TTL_SECONDS,
        )
        second_entry = CachedResponse(
            payload_hash="hash-1",  # same canonical payload
            response={"media_buy_id": "mb_second"},
            expires_at_epoch=time.time() + IDEMPOTENCY_TTL_SECONDS,
        )

        asyncio.run(backend.put("scope-a", "key-race", first_entry))
        # Second writer's put() must not raise and must not clobber the
        # first writer's stored response.
        asyncio.run(backend.put("scope-a", "key-race", second_entry))

        stored = asyncio.run(backend.get("scope-a", "key-race"))
        assert stored is not None
        assert stored.response == {"media_buy_id": "mb_first"}

    def test_put_over_expired_entry_replaces_it(self, dynamo_table):
        """A `put()` targeting a slot whose previous entry has already
        expired should succeed and replace it — expiry, not "row exists at
        all", is what governs whether an overwrite is legitimate."""
        backend = DynamoDBIdempotencyBackend(table=dynamo_table)
        expired_entry = CachedResponse(
            payload_hash="hash-old",
            response={"media_buy_id": "mb_old"},
            expires_at_epoch=time.time() - 1,
        )
        asyncio.run(backend.put("scope-a", "key-1", expired_entry))

        fresh_entry = CachedResponse(
            payload_hash="hash-new",
            response={"media_buy_id": "mb_new"},
            expires_at_epoch=time.time() + IDEMPOTENCY_TTL_SECONDS,
        )
        asyncio.run(backend.put("scope-a", "key-1", fresh_entry))

        stored = asyncio.run(backend.get("scope-a", "key-1"))
        assert stored is not None
        assert stored.response == {"media_buy_id": "mb_new"}

    def test_delete_expired_is_a_no_op(self, dynamo_table):
        """TTL cleanup is delegated to the table's own `ttl` attribute (a
        DynamoDB-service-level background sweep), so `delete_expired` is
        documented as a legitimate no-op per the abstract contract."""
        backend = DynamoDBIdempotencyBackend(table=dynamo_table)
        assert asyncio.run(backend.delete_expired()) == 0


# ---------------------------------------------------------------------------
# IdempotencyStore.wrap() — the real call pattern main.py's task handlers use
# ---------------------------------------------------------------------------


class TestIdempotencyStoreWrap:
    """Wraps a tiny fake async handler with the SDK's real
    `IdempotencyStore(backend=DynamoDBIdempotencyBackend(...)).wrap(...)`
    and calls it with a real `ToolContext`, exercising the exact pattern
    `main.py`'s task handlers (task 3+) will use — catches wiring bugs
    (payload hashing, per-principal scoping, conflict/replay semantics)
    that backend-only tests can't see."""

    @staticmethod
    def _make_store(dynamo_table) -> IdempotencyStore:
        return IdempotencyStore(
            backend=DynamoDBIdempotencyBackend(table=dynamo_table),
            ttl_seconds=IDEMPOTENCY_TTL_SECONDS,
        )

    def test_first_call_runs_handler_and_caches(self, dynamo_table):
        store = self._make_store(dynamo_table)
        calls = []

        @store.wrap
        async def create_media_buy(self, params, context=None):
            calls.append(params)
            return {"media_buy_id": "mb_abc", "status": "completed"}

        ctx = ToolContext(caller_identity="buyer-a")
        params = {"idempotency_key": "key-1", "product_id": "p1", "budget": 100}

        result = asyncio.run(create_media_buy(None, params, ctx))
        assert result == {"media_buy_id": "mb_abc", "status": "completed"}
        assert len(calls) == 1

    def test_exact_replay_returns_cached_response_without_rerunning_handler(self, dynamo_table):
        store = self._make_store(dynamo_table)
        calls = []

        @store.wrap
        async def create_media_buy(self, params, context=None):
            calls.append(params)
            return {"media_buy_id": "mb_abc", "status": "completed"}

        ctx = ToolContext(caller_identity="buyer-a")
        params = {"idempotency_key": "key-1", "product_id": "p1", "budget": 100}

        first = asyncio.run(create_media_buy(None, params, ctx))
        second = asyncio.run(create_media_buy(None, dict(params), ctx))

        # Handler only actually ran once — the second call was a pure replay.
        assert len(calls) == 1
        # Replay envelope carries `replayed: true` per AdCP L1/security rule 4.
        assert second["media_buy_id"] == first["media_buy_id"]
        assert second["replayed"] is True

    def test_conflicting_replay_raises_idempotency_conflict_error(self, dynamo_table):
        store = self._make_store(dynamo_table)

        @store.wrap
        async def create_media_buy(self, params, context=None):
            return {"media_buy_id": "mb_abc", "status": "completed"}

        ctx = ToolContext(caller_identity="buyer-a")
        first_params = {"idempotency_key": "key-1", "product_id": "p1", "budget": 100}
        conflicting_params = {"idempotency_key": "key-1", "product_id": "p1", "budget": 999}

        asyncio.run(create_media_buy(None, first_params, ctx))
        with pytest.raises(IdempotencyConflictError):
            asyncio.run(create_media_buy(None, conflicting_params, ctx))

    def test_missing_idempotency_key_falls_through_with_no_dedup(self, dynamo_table):
        """No `idempotency_key` in params -> the wrap has nothing to dedup
        on, so it runs the handler every time (schema validation above this
        layer is responsible for rejecting the missing key)."""
        store = self._make_store(dynamo_table)
        calls = []

        @store.wrap
        async def create_media_buy(self, params, context=None):
            calls.append(params)
            return {"media_buy_id": "mb_abc", "status": "completed"}

        ctx = ToolContext(caller_identity="buyer-a")
        params = {"product_id": "p1", "budget": 100}

        asyncio.run(create_media_buy(None, params, ctx))
        asyncio.run(create_media_buy(None, params, ctx))
        assert len(calls) == 2

    def test_different_principals_do_not_collide_on_same_key(self, dynamo_table):
        """Two different buyers (`caller_identity`) using the identical
        `idempotency_key` must each get their own handler execution — this
        is the per-principal scoping property the hand-rolled version this
        replaced was missing entirely (any two buyers could previously
        collide on the same idempotency_key namespace)."""
        store = self._make_store(dynamo_table)
        calls = []

        @store.wrap
        async def create_media_buy(self, params, context=None):
            calls.append(context.caller_identity)
            return {"media_buy_id": f"mb_{context.caller_identity}", "status": "completed"}

        params = {"idempotency_key": "shared-key", "product_id": "p1", "budget": 100}

        result_a = asyncio.run(create_media_buy(None, params, ToolContext(caller_identity="buyer-a")))
        result_b = asyncio.run(create_media_buy(None, dict(params), ToolContext(caller_identity="buyer-b")))

        assert len(calls) == 2  # both ran — no cross-buyer replay
        assert result_a["media_buy_id"] == "mb_buyer-a"
        assert result_b["media_buy_id"] == "mb_buyer-b"

    def test_concurrent_identical_calls_only_one_handler_execution_wins_same_response(self, dynamo_table):
        """Two truly concurrent calls with the same key + payload: both
        callers must end up with byte-for-byte the same response, and the
        backend must not lose either handler's cached result to a lost
        write race."""
        store = self._make_store(dynamo_table)
        call_count = 0

        @store.wrap
        async def create_media_buy(self, params, context=None):
            nonlocal call_count
            call_count += 1
            # Simulate two requests actually overlapping in time: both see
            # an empty cache slot before either has committed its result.
            await asyncio.sleep(0)
            return {"media_buy_id": f"mb_{call_count}", "status": "completed"}

        ctx = ToolContext(caller_identity="buyer-a")
        params = {"idempotency_key": "key-race", "product_id": "p1", "budget": 100}

        async def run_both():
            return await asyncio.gather(
                create_media_buy(None, dict(params), ctx),
                create_media_buy(None, dict(params), ctx),
            )

        result_a, result_b = asyncio.run(run_both())
        # Both callers must observe the same media_buy_id from whichever
        # writer's put() actually stuck first.
        assert result_a["media_buy_id"] == result_b["media_buy_id"]

    def test_capability_reports_supported_with_configured_ttl(self, dynamo_table):
        store = self._make_store(dynamo_table)
        assert store.capability() == {"supported": True, "replay_ttl_seconds": IDEMPOTENCY_TTL_SECONDS}

    def test_canonical_json_sha256_matches_sdk_equivalence_rule(self):
        """Sanity check that this module's callers get the SDK's real
        RFC 8785 JCS canonicalization, not a hand-rolled `json.dumps` hash —
        key/value order must not affect the hash, and the spec's exclusion
        list (idempotency_key, context, governance_context) must be
        stripped before hashing."""
        a = {"idempotency_key": "k1", "budget": 100, "product_id": "p1"}
        b = {"product_id": "p1", "idempotency_key": "k2", "budget": 100}
        assert canonical_json_sha256(a) == canonical_json_sha256(b)

        c = {"idempotency_key": "k1", "budget": 999, "product_id": "p1"}
        assert canonical_json_sha256(a) != canonical_json_sha256(c)


# ---------------------------------------------------------------------------
# MediaBuyStore
# ---------------------------------------------------------------------------


class TestMediaBuyStore:
    def test_create_and_get(self, dynamo_table):
        store = MediaBuyStore(table=dynamo_table)
        media_buy_id = new_media_buy_id()
        record = store.create(
            media_buy_id,
            product_id="ref-ctv-sports-01",
            pricing_option_id="cpm-ref-ctv-sports-01",
            budget=1000.0,
            packages=[{"package_id": "pkg-1"}],
        )
        assert record["media_buy_id"] == media_buy_id
        assert record["status"] == "completed"

        fetched = store.get(media_buy_id)
        assert fetched is not None
        assert fetched["product_id"] == "ref-ctv-sports-01"
        assert fetched["budget"] == 1000.0

    def test_get_missing_returns_none(self, dynamo_table):
        store = MediaBuyStore(table=dynamo_table)
        assert store.get("mb_does_not_exist") is None

    def test_get_many_no_filter_returns_all(self, dynamo_table):
        store = MediaBuyStore(table=dynamo_table)
        ids = [new_media_buy_id() for _ in range(3)]
        for mb_id in ids:
            store.create(mb_id, product_id="p1", pricing_option_id="cpm-p1", budget=50.0, packages=[])

        all_records = store.get_many()
        assert {r["media_buy_id"] for r in all_records} == set(ids)

    def test_get_many_with_filter_returns_only_matching(self, dynamo_table):
        store = MediaBuyStore(table=dynamo_table)
        ids = [new_media_buy_id() for _ in range(3)]
        for mb_id in ids:
            store.create(mb_id, product_id="p1", pricing_option_id="cpm-p1", budget=50.0, packages=[])

        filtered = store.get_many(media_buy_ids=[ids[0], "mb_unknown"])
        assert len(filtered) == 1
        assert filtered[0]["media_buy_id"] == ids[0]

    def test_update_existing_record(self, dynamo_table):
        store = MediaBuyStore(table=dynamo_table)
        mb_id = new_media_buy_id()
        store.create(mb_id, product_id="p1", pricing_option_id="cpm-p1", budget=100.0, packages=[])

        updated = store.update(mb_id, status="paused", budget=50.0)
        assert updated["status"] == "paused"
        assert updated["budget"] == 50.0

        # Reflects immediately -- no caching, per R3's "no stale state" rule.
        assert store.get(mb_id)["status"] == "paused"

    def test_update_missing_raises(self, dynamo_table):
        store = MediaBuyStore(table=dynamo_table)
        with pytest.raises(MediaBuyNotFoundError):
            store.update("mb_does_not_exist", status="paused")

    def test_add_feedback_to_existing_media_buy(self, dynamo_table):
        store = MediaBuyStore(table=dynamo_table)
        mb_id = new_media_buy_id()
        store.create(mb_id, product_id="p1", pricing_option_id="cpm-p1", budget=100.0, packages=[])

        feedback = store.add_feedback(mb_id, metric="ctr", value=0.02)
        assert feedback["metric"] == "ctr"
        assert feedback["value"] == 0.02
        assert feedback["media_buy_id"] == mb_id

    def test_add_feedback_missing_media_buy_raises(self, dynamo_table):
        store = MediaBuyStore(table=dynamo_table)
        with pytest.raises(MediaBuyNotFoundError):
            store.add_feedback("mb_does_not_exist", metric="ctr", value=0.02)


# ---------------------------------------------------------------------------
# AccountStore
# ---------------------------------------------------------------------------


class TestAccountStore:
    def test_upsert_then_list(self, dynamo_table):
        store = AccountStore(table=dynamo_table)
        store.upsert("brand.example.com", "operator-a")

        accounts = store.list()
        assert len(accounts) == 1
        assert accounts[0]["brand"] == "brand.example.com"
        assert accounts[0]["operator"] == "operator-a"

    def test_upsert_is_idempotent_on_created_at(self, dynamo_table):
        store = AccountStore(table=dynamo_table)
        first = store.upsert("brand.example.com", "operator-a")
        second = store.upsert("brand.example.com", "operator-a")

        assert first["created_at"] == second["created_at"]

    def test_list_returns_multiple_accounts(self, dynamo_table):
        store = AccountStore(table=dynamo_table)
        store.upsert("brand-a.example.com", "operator-a")
        store.upsert("brand-b.example.com", "operator-b")

        accounts = store.list()
        pairs = {(a["brand"], a["operator"]) for a in accounts}
        assert pairs == {("brand-a.example.com", "operator-a"), ("brand-b.example.com", "operator-b")}
