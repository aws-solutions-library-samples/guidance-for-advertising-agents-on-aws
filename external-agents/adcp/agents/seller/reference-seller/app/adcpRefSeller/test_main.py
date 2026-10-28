"""
Unit tests for main.py's create_media_buy (task 3 of
.kiro/specs/seller-agent-adcp-compliance/tasks.md, R1).

Same moto-mocked-DynamoDB pattern as test_state.py (no live AWS calls, no
DynamoDB Local process required). Calls `ReferenceSellerAgent().create_media_buy(...)`
directly with a real `adcp.server.base.ToolContext` - the same "local/unit
verification" bar the task calls for, since hitting the live deployed MCP
endpoint is explicitly deferred to task 10 (redeploy + live verify).

Covers exactly the five cases task 3 calls out: success (real DynamoDB
record + media_buy_response with a real media_buy_id), replay (same
idempotency_key, same body), conflict (same key, different body), missing
idempotency_key, unknown product_id. Also covers `validate_account_oneof`
directly (fixtures.py's oneOf helper, reused by task 4) and
`fixtures.find_product`.

Validates: Requirements R1, R8 (task 3 of
.kiro/specs/seller-agent-adcp-compliance/tasks.md).
"""


from aws_region import region
import os
from datetime import datetime, timezone
from unittest.mock import patch

import boto3
import pytest
from boto3.dynamodb.conditions import Key
from adcp.exceptions import IdempotencyConflictError
from adcp.server.base import ToolContext
from moto import mock_aws

import main
from fixtures import find_product
from validation import validate_account_oneof

TABLE_NAME = "adcp-reference-seller-state-test"
REGION = region()
VALID_PACKAGE = {
    "product_id": "ref-ctv-sports-01",
    "budget": 10000,
    "pricing_option_id": "cpm-ref-ctv-sports-01",
    "buyer_ref": "pkg_1",
}


def _base_params(**overrides):
    params = {
        "idempotency_key": "11111111-1111-1111-1111-111111111111",
        "account": {"account_id": "acc_1"},
        "brand": {"domain": "acme.example"},
        "start_time": "2026-05-01T00:00:00Z",
        "end_time": "2026-05-31T23:59:59Z",
        "packages": [dict(VALID_PACKAGE)],
    }
    params.update(overrides)
    return params


@pytest.fixture
def dynamo_table(monkeypatch):
    """Fresh in-memory DynamoDB table, same key schema as
    deploy_state_table.py. Points main.py's module-level idempotency_store
    / media_buy_store at it via STATE_TABLE_NAME (the same env var
    state.py._table() reads lazily on every call)."""
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
        monkeypatch.setenv("STATE_TABLE_NAME", TABLE_NAME)
        monkeypatch.setenv("AWS_REGION", REGION)
        resource = boto3.resource("dynamodb", region_name=REGION)
        yield resource.Table(TABLE_NAME)


@pytest.fixture
def agent():
    return main.ReferenceSellerAgent()


class TestCreateMediaBuySuccess:
    def test_creates_real_dynamodb_record_and_returns_media_buy_response(self, agent, dynamo_table):
        ctx = ToolContext(caller_identity="buyer-a")
        params = _base_params()

        import asyncio

        result = asyncio.run(agent.create_media_buy(params, ctx))

        assert "errors" not in result
        assert result["media_buy_id"].startswith("mb_")
        assert result["packages"][0]["product_id"] == "ref-ctv-sports-01"
        assert result["packages"][0]["budget"] == 10000
        assert result["sandbox"] is True

        # Asserts against the stored DynamoDB record, not just the response.
        stored = dynamo_table.get_item(
            Key={"pk": f"MEDIABUY#{result['media_buy_id']}", "sk": "META"}
        ).get("Item")
        assert stored is not None
        assert stored["product_id"] == "ref-ctv-sports-01"
        assert stored["status"] == "completed"


class TestCreateMediaBuyReplay:
    def test_same_key_same_body_replays_byte_for_byte(self, agent, dynamo_table):
        import asyncio

        ctx = ToolContext(caller_identity="buyer-a")
        params = _base_params()

        first = asyncio.run(agent.create_media_buy(dict(params), ctx))
        second = asyncio.run(agent.create_media_buy(dict(params), ctx))

        assert second["media_buy_id"] == first["media_buy_id"]
        assert second["packages"] == first["packages"]
        assert second["replayed"] is True


class TestCreateMediaBuyConflict:
    def test_same_key_different_body_raises_conflict(self, agent, dynamo_table):
        import asyncio

        ctx = ToolContext(caller_identity="buyer-a")
        first_params = _base_params()
        conflicting_params = _base_params(packages=[{**VALID_PACKAGE, "budget": 99999}])

        asyncio.run(agent.create_media_buy(first_params, ctx))
        with pytest.raises(IdempotencyConflictError):
            asyncio.run(agent.create_media_buy(conflicting_params, ctx))


class TestCreateMediaBuyMissingIdempotencyKey:
    def test_missing_idempotency_key_returns_validation_error(self, agent, dynamo_table):
        import asyncio

        ctx = ToolContext(caller_identity="buyer-a")
        params = _base_params()
        del params["idempotency_key"]

        result = asyncio.run(agent.create_media_buy(params, ctx))

        assert result["errors"][0]["code"] == "VALIDATION_ERROR"
        assert result["errors"][0]["issues"][0]["pointer"] == "/idempotency_key"


class TestCreateMediaBuyUnknownProduct:
    def test_unknown_product_id_returns_product_not_found(self, agent, dynamo_table):
        import asyncio

        ctx = ToolContext(caller_identity="buyer-a")
        params = _base_params(packages=[{**VALID_PACKAGE, "product_id": "does-not-exist"}])

        result = asyncio.run(agent.create_media_buy(params, ctx))

        assert result["errors"][0]["code"] == "PRODUCT_NOT_FOUND"


class TestCreateMediaBuyNoCallerIdentity:
    def test_missing_caller_identity_still_creates_media_buy_without_dedup(self, agent, dynamo_table):
        """Documents the caller_identity gap: when context has no
        caller_identity (the current production reality for this agent -
        see main.py/state.py's IMPORTANT notes), the idempotency wrap
        falls through with no dedup, but the handler's own
        missing-idempotency_key check and business logic still run
        normally - it's not silently broken, just not deduplicated."""
        import asyncio

        ctx = ToolContext()  # caller_identity=None
        params = _base_params()

        result = asyncio.run(agent.create_media_buy(dict(params), ctx))
        assert "errors" not in result
        first_media_buy_id = result["media_buy_id"]

        # Same key, same body, but no caller_identity -> re-executes
        # (creates a second media buy) rather than replaying, since the
        # wrap can't safely scope the cache without a principal.
        result2 = asyncio.run(agent.create_media_buy(dict(params), ctx))
        assert "errors" not in result2
        assert result2["media_buy_id"] != first_media_buy_id


class TestValidateAccountOneof:
    def test_account_id_variant_is_valid(self):
        assert validate_account_oneof({"account_id": "acc_1"}) is None

    def test_brand_operator_variant_is_valid(self):
        assert validate_account_oneof({"brand": {"domain": "acme.example"}, "operator": "sales.example"}) is None

    def test_none_account_is_valid(self):
        assert validate_account_oneof(None) is None

    def test_mixed_variants_is_rejected(self):
        error = validate_account_oneof(
            {"account_id": "acc_1", "brand": {"domain": "acme.example"}, "operator": "sales.example"}
        )
        assert error is not None
        assert "mixes fields from both" in error

    def test_neither_variant_is_rejected(self):
        error = validate_account_oneof({"foo": "bar"})
        assert error is not None

    def test_brand_operator_variant_missing_operator_is_rejected(self):
        error = validate_account_oneof({"brand": {"domain": "acme.example"}})
        assert error is not None
        assert "missing required field" in error


class TestFindProduct:
    def test_known_product_id_returns_product_without_keywords(self):
        product = find_product("ref-ctv-sports-01")
        assert product is not None
        assert product["product_id"] == "ref-ctv-sports-01"
        assert "keywords" not in product

    def test_unknown_product_id_returns_none(self):
        assert find_product("does-not-exist") is None


# ---------------------------------------------------------------------------
# update_media_buy (task 4 of .kiro/specs/seller-agent-adcp-compliance/tasks.md, R2)
# ---------------------------------------------------------------------------
#
# Covers the cases task 4 explicitly calls out: budget change, pause,
# cancel (all against a media_buy_id created via create_media_buy in the
# same test, mirroring the task's literal instruction), and the
# nonexistent-ID error path. Also covers missing idempotency_key, account
# oneOf validation reuse, and replay/conflict, mirroring create_media_buy's
# coverage in the same file.

UPDATE_ACCOUNT = {"account_id": "acc_1"}


def _update_params(media_buy_id, **overrides):
    params = {
        "idempotency_key": "22222222-2222-2222-2222-222222222222",
        "account": dict(UPDATE_ACCOUNT),
        "media_buy_id": media_buy_id,
    }
    params.update(overrides)
    return params


def _create_a_media_buy(agent, ctx):
    import asyncio

    create_params = _base_params(idempotency_key="33333333-3333-3333-3333-333333333333")
    result = asyncio.run(agent.create_media_buy(create_params, ctx))
    assert "errors" not in result
    return result


class TestUpdateMediaBuyBudgetChange:
    def test_package_budget_change_updates_stored_record_and_returns_response(self, agent, dynamo_table):
        import asyncio

        ctx = ToolContext(caller_identity="buyer-a")
        created = _create_a_media_buy(agent, ctx)
        media_buy_id = created["media_buy_id"]
        package_id = created["packages"][0]["package_id"]

        params = _update_params(
            media_buy_id,
            packages=[{"package_id": package_id, "budget": 5000}],
        )
        result = asyncio.run(agent.update_media_buy(params, ctx))

        assert "errors" not in result
        assert result["media_buy_id"] == media_buy_id
        assert result["revision"] == 2
        assert result["affected_packages"][0]["budget"] == 5000

        stored = dynamo_table.get_item(Key={"pk": f"MEDIABUY#{media_buy_id}", "sk": "META"}).get("Item")
        assert stored["budget"] == 5000
        assert stored["packages"][0]["budget"] == 5000


class TestUpdateMediaBuyPause:
    def test_pause_sets_status_paused(self, agent, dynamo_table):
        import asyncio

        ctx = ToolContext(caller_identity="buyer-a")
        created = _create_a_media_buy(agent, ctx)
        media_buy_id = created["media_buy_id"]

        params = _update_params(media_buy_id, paused=True)
        result = asyncio.run(agent.update_media_buy(params, ctx))

        assert "errors" not in result
        assert result["media_buy_status"] == "paused"

        stored = dynamo_table.get_item(Key={"pk": f"MEDIABUY#{media_buy_id}", "sk": "META"}).get("Item")
        assert stored["status"] == "paused"


class TestUpdateMediaBuyCancel:
    def test_cancel_sets_status_canceled(self, agent, dynamo_table):
        import asyncio

        ctx = ToolContext(caller_identity="buyer-a")
        created = _create_a_media_buy(agent, ctx)
        media_buy_id = created["media_buy_id"]

        params = _update_params(media_buy_id, canceled=True, cancellation_reason="budget cut")
        result = asyncio.run(agent.update_media_buy(params, ctx))

        assert "errors" not in result
        assert result["media_buy_status"] == "canceled"

        stored = dynamo_table.get_item(Key={"pk": f"MEDIABUY#{media_buy_id}", "sk": "META"}).get("Item")
        assert stored["status"] == "canceled"
        assert stored["cancellation_reason"] == "budget cut"


class TestUpdateMediaBuyNonexistentId:
    def test_nonexistent_media_buy_id_returns_error_not_success(self, agent, dynamo_table):
        import asyncio

        ctx = ToolContext(caller_identity="buyer-a")
        params = _update_params("mb_does_not_exist", paused=True)

        result = asyncio.run(agent.update_media_buy(params, ctx))

        assert result["errors"][0]["code"] == "MEDIA_BUY_NOT_FOUND"


class TestUpdateMediaBuyMissingIdempotencyKey:
    def test_missing_idempotency_key_returns_validation_error(self, agent, dynamo_table):
        import asyncio

        ctx = ToolContext(caller_identity="buyer-a")
        created = _create_a_media_buy(agent, ctx)
        params = _update_params(created["media_buy_id"], paused=True)
        del params["idempotency_key"]

        result = asyncio.run(agent.update_media_buy(params, ctx))

        assert result["errors"][0]["code"] == "VALIDATION_ERROR"
        assert result["errors"][0]["issues"][0]["pointer"] == "/idempotency_key"


class TestUpdateMediaBuyReplayAndConflict:
    def test_same_key_same_body_replays(self, agent, dynamo_table):
        import asyncio

        ctx = ToolContext(caller_identity="buyer-a")
        created = _create_a_media_buy(agent, ctx)
        params = _update_params(created["media_buy_id"], paused=True)

        first = asyncio.run(agent.update_media_buy(dict(params), ctx))
        second = asyncio.run(agent.update_media_buy(dict(params), ctx))

        assert second["media_buy_id"] == first["media_buy_id"]
        assert second["revision"] == first["revision"]
        assert second["replayed"] is True

    def test_same_key_different_body_raises_conflict(self, agent, dynamo_table):
        import asyncio

        ctx = ToolContext(caller_identity="buyer-a")
        created = _create_a_media_buy(agent, ctx)
        media_buy_id = created["media_buy_id"]

        first_params = _update_params(media_buy_id, paused=True)
        conflicting_params = _update_params(media_buy_id, canceled=True)

        asyncio.run(agent.update_media_buy(first_params, ctx))
        with pytest.raises(IdempotencyConflictError):
            asyncio.run(agent.update_media_buy(conflicting_params, ctx))


# ---------------------------------------------------------------------------
# get_media_buys (task 5 of .kiro/specs/seller-agent-adcp-compliance/tasks.md, R3)
# ---------------------------------------------------------------------------
#
# Covers exactly what task 5 calls out: no filter returns every stored
# media buy, a filtered call (mix of an existing + nonexistent id) returns
# only the matching ones, and a prior update_media_buy call's changes are
# reflected in a subsequent get_media_buys call (proved end-to-end through
# create -> update -> get_media_buys, not just by inspecting state.py).


class TestGetMediaBuysNoFilter:
    def test_no_filter_returns_every_stored_media_buy(self, agent, dynamo_table):
        import asyncio

        ctx = ToolContext(caller_identity="buyer-a")
        created_1 = asyncio.run(
            agent.create_media_buy(
                _base_params(idempotency_key="44444444-4444-4444-4444-444444444444"), ctx
            )
        )
        created_2 = asyncio.run(
            agent.create_media_buy(
                _base_params(idempotency_key="55555555-5555-5555-5555-555555555555"), ctx
            )
        )
        created_3 = asyncio.run(
            agent.create_media_buy(
                _base_params(idempotency_key="66666666-6666-6666-6666-666666666666"), ctx
            )
        )

        result = asyncio.run(agent.get_media_buys({}, ctx))

        assert "errors" not in result
        assert result["sandbox"] is True
        returned_ids = {mb["media_buy_id"] for mb in result["media_buys"]}
        assert returned_ids == {
            created_1["media_buy_id"],
            created_2["media_buy_id"],
            created_3["media_buy_id"],
        }
        for mb in result["media_buys"]:
            assert mb["currency"] == "USD"
            assert "packages" in mb


class TestGetMediaBuysBrandOperatorAccount:
    """Bug fix (found live, task 10 verification): get_media_buys used to
    pass a `{brand, operator}`-created account straight through, which has
    no `account_id` field and fails the response schema's `account_id`
    requirement (`'account_id' is a required property`). Covers a
    `{brand, operator}`-created account round-tripping through
    get_media_buys without a schema error, and confirms the derived
    account_id matches what sync_accounts would derive for the same pair
    (same _account_id_slug helper, so a media buy and a synced account for
    the same real (brand, operator) never disagree on account_id)."""

    def test_brand_operator_account_round_trips_without_schema_error(self, agent, dynamo_table):
        import asyncio

        ctx = ToolContext(caller_identity="buyer-a")
        params = _base_params(account={"brand": {"domain": "acme.example"}, "operator": "sales.example"})

        created = asyncio.run(agent.create_media_buy(params, ctx))
        assert "errors" not in created
        media_buy_id = created["media_buy_id"]

        result = asyncio.run(agent.get_media_buys({"media_buy_ids": [media_buy_id]}, ctx))
        assert "errors" not in result

        mb = result["media_buys"][0]
        account = mb["account"]
        assert account is not None
        # The fields the AdCP Account schema requires on every account
        # object it emits (core/account.json's `required` list) -
        # account_id, name, status - even though the buyer never sent an
        # account_id on create_media_buy.
        assert account["account_id"]
        assert account["name"]
        assert account["status"] == "active"
        assert account["brand"] == {"domain": "acme.example"}
        assert account["operator"] == "sales.example"

        # Validate the actual response against the real bundled AdCP
        # schema - not just hand-picked field assertions - so this test
        # would have caught the original 'account_id is a required
        # property' live failure.
        import json as _json

        from adcp.validation.schema_validator import validate_response

        outcome = validate_response("get_media_buys", result)
        assert outcome.valid, _json.dumps([vars(i) for i in outcome.issues])

    def test_derived_account_id_matches_sync_accounts_for_same_pair(self, agent, dynamo_table):
        import asyncio

        ctx = ToolContext(caller_identity="buyer-a")
        create_params = _base_params(
            idempotency_key="ffffffff-ffff-ffff-ffff-ffffffffffff",
            account={"brand": {"domain": "acme.example"}, "operator": "sales.example"},
        )
        created = asyncio.run(agent.create_media_buy(create_params, ctx))
        media_buy_id = created["media_buy_id"]

        get_result = asyncio.run(agent.get_media_buys({"media_buy_ids": [media_buy_id]}, ctx))
        media_buy_account_id = get_result["media_buys"][0]["account"]["account_id"]

        sync_result = asyncio.run(
            agent.sync_accounts(
                {
                    "idempotency_key": "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
                    "accounts": [{"brand": {"domain": "acme.example"}, "operator": "sales.example"}],
                },
                ctx,
            )
        )
        synced_account_id = sync_result["accounts"][0]["account_id"]

        assert media_buy_account_id == synced_account_id

    def test_account_id_variant_still_round_trips(self, agent, dynamo_table):
        """The pre-existing {account_id} variant continues to work
        (regression guard for the fix above)."""
        import asyncio

        ctx = ToolContext(caller_identity="buyer-a")
        created = _create_a_media_buy(agent, ctx)
        media_buy_id = created["media_buy_id"]

        result = asyncio.run(agent.get_media_buys({"media_buy_ids": [media_buy_id]}, ctx))
        account = result["media_buys"][0]["account"]
        assert account["account_id"] == "acc_1"
        assert account["status"] == "active"


class TestGetMediaBuysFiltered:
    def test_filtered_call_returns_only_matching_ids_skipping_missing(self, agent, dynamo_table):
        import asyncio

        ctx = ToolContext(caller_identity="buyer-a")
        created_1 = asyncio.run(
            agent.create_media_buy(
                _base_params(idempotency_key="77777777-7777-7777-7777-777777777777"), ctx
            )
        )
        asyncio.run(
            agent.create_media_buy(
                _base_params(idempotency_key="88888888-8888-8888-8888-888888888888"), ctx
            )
        )

        result = asyncio.run(
            agent.get_media_buys(
                {"media_buy_ids": [created_1["media_buy_id"], "mb_does_not_exist"]}, ctx
            )
        )

        assert "errors" not in result
        returned_ids = [mb["media_buy_id"] for mb in result["media_buys"]]
        assert returned_ids == [created_1["media_buy_id"]]


class TestGetMediaBuysReflectsUpdate:
    def test_reflects_prior_update_media_buy_call_no_stale_caching(self, agent, dynamo_table):
        import asyncio

        ctx = ToolContext(caller_identity="buyer-a")
        created = _create_a_media_buy(agent, ctx)
        media_buy_id = created["media_buy_id"]

        before = asyncio.run(agent.get_media_buys({"media_buy_ids": [media_buy_id]}, ctx))
        assert before["media_buys"][0]["status"] == "completed"

        update_result = asyncio.run(agent.update_media_buy(_update_params(media_buy_id, paused=True), ctx))
        assert "errors" not in update_result

        after = asyncio.run(agent.get_media_buys({"media_buy_ids": [media_buy_id]}, ctx))
        assert after["media_buys"][0]["status"] == "paused"


# ---------------------------------------------------------------------------
# get_media_buy_delivery (task 6 of .kiro/specs/seller-agent-adcp-compliance/tasks.md, R4)
# ---------------------------------------------------------------------------
#
# Handler-level tests: create a real media buy via create_media_buy, then
# call get_media_buy_delivery and confirm it returns real derived figures
# tied to that media buy's actual stored budget/pricing_option - not a
# placeholder. `datetime.now` inside main.py is pinned (via monkeypatch of
# main.datetime) so repeated calls at the same simulated "now" are asserted
# for exact reproducibility, matching task 6's own instruction.


class _FixedDatetime(datetime):
    """A `datetime` subclass whose `now()` always returns a fixed instant,
    used to pin main.py's `datetime.now(timezone.utc)` call inside
    `get_media_buy_delivery` without needing to change that call site's
    signature - main.py imports `datetime` (the class) and calls
    `datetime.now(timezone.utc)`, so patching `main.datetime` to this
    subclass makes every `.now(...)` call inside main.py deterministic."""

    # Far enough in the future of any test's real create_media_buy call
    # that elapsed_fraction is always clamped to 1.0 regardless of when
    # this test suite actually runs (created_at is the real wall-clock
    # time at record creation, not a fixture) - this keeps the "not a
    # placeholder" assertion below (impressions > 0) robust without
    # needing to also pin create_media_buy's own clock.
    fixed_now = datetime(2100, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.fixed_now


class TestGetMediaBuyDeliverySuccess:
    def test_returns_real_derived_figures_tied_to_stored_budget_and_pricing_option(self, agent, dynamo_table):
        import asyncio

        ctx = ToolContext(caller_identity="buyer-a")
        created = _create_a_media_buy(agent, ctx)
        media_buy_id = created["media_buy_id"]

        with patch("main.datetime", _FixedDatetime):
            result = asyncio.run(agent.get_media_buy_delivery({"media_buy_ids": [media_buy_id]}, ctx))

        assert "errors" not in result
        assert result["sandbox"] is True
        assert result["currency"] == "USD"
        assert len(result["media_buy_deliveries"]) == 1

        delivery = result["media_buy_deliveries"][0]
        assert delivery["media_buy_id"] == media_buy_id
        assert delivery["status"] == "completed"

        # Not a placeholder: figures are derived from this media buy's
        # actual stored budget (10000, VALID_PACKAGE) and its product's
        # (ref-ctv-sports-01) real pricing_option fixed_price (24.0 CPM).
        # _FixedDatetime.fixed_now is far enough past the real created_at
        # that elapsed_fraction clamps to 1.0 (full budget delivered),
        # which is what makes the impressions > 0 assertion below robust.
        stored = dynamo_table.get_item(Key={"pk": f"MEDIABUY#{media_buy_id}", "sk": "META"}).get("Item")
        from delivery import derive_delivery_figures

        expected = derive_delivery_figures(
            budget=float(stored["budget"]),
            cpm=24.0,
            created_at=stored["created_at"],
            now=_FixedDatetime.fixed_now,
        )
        assert delivery["totals"]["impressions"] == expected.impressions
        assert delivery["totals"]["spend"] == expected.spend
        assert delivery["totals"]["impressions"] > 0

        assert len(delivery["by_package"]) == 1
        assert delivery["by_package"][0]["package_id"] == created["packages"][0]["package_id"]
        assert delivery["by_package"][0]["impressions"] == expected.impressions


class TestGetMediaBuyDeliveryDeterminism:
    def test_same_inputs_at_the_same_simulated_now_always_produce_the_same_output(self, agent, dynamo_table):
        """Task 6's own instruction, exercised at the handler level: two
        calls against the same underlying stored state, with `now` pinned
        to the same simulated instant, return byte-for-byte identical
        delivery figures."""
        import asyncio

        ctx = ToolContext(caller_identity="buyer-a")
        created = _create_a_media_buy(agent, ctx)
        media_buy_id = created["media_buy_id"]

        with patch("main.datetime", _FixedDatetime):
            first = asyncio.run(agent.get_media_buy_delivery({"media_buy_ids": [media_buy_id]}, ctx))
            second = asyncio.run(agent.get_media_buy_delivery({"media_buy_ids": [media_buy_id]}, ctx))

        assert first["media_buy_deliveries"] == second["media_buy_deliveries"]


class TestGetMediaBuyDeliveryNoFilter:
    def test_no_filter_returns_delivery_for_every_stored_media_buy(self, agent, dynamo_table):
        import asyncio

        ctx = ToolContext(caller_identity="buyer-a")
        created_1 = asyncio.run(
            agent.create_media_buy(
                _base_params(idempotency_key="99999999-9999-9999-9999-999999999991"), ctx
            )
        )
        created_2 = asyncio.run(
            agent.create_media_buy(
                _base_params(idempotency_key="99999999-9999-9999-9999-999999999992"), ctx
            )
        )

        with patch("main.datetime", _FixedDatetime):
            result = asyncio.run(agent.get_media_buy_delivery({}, ctx))

        returned_ids = {d["media_buy_id"] for d in result["media_buy_deliveries"]}
        assert returned_ids == {created_1["media_buy_id"], created_2["media_buy_id"]}


# ---------------------------------------------------------------------------
# provide_performance_feedback (task 7 of .kiro/specs/seller-agent-adcp-compliance/tasks.md, R5)
# ---------------------------------------------------------------------------
#
# Covers exactly what task 7 calls out: success (real FEEDBACK#<ts> item in
# DynamoDB + success acknowledgment), unknown media_buy_id (error response,
# asserted against the stored record), missing idempotency_key (VALIDATION_ERROR), and
# replay/conflict (this task IS idempotency-wrapped, per
# adcp._idempotency.IDEMPOTENT_TASKS).


def _feedback_params(media_buy_id, **overrides):
    params = {
        "idempotency_key": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "media_buy_id": media_buy_id,
        "measurement_period": {
            "start": "2026-05-01T00:00:00Z",
            "end": "2026-05-31T23:59:59Z",
        },
        "performance_index": 0.85,
        "metric_type": "overall_performance",
    }
    params.update(overrides)
    return params


class TestProvidePerformanceFeedbackSuccess:
    def test_creates_real_feedback_item_and_returns_success_ack(self, agent, dynamo_table):
        import asyncio

        ctx = ToolContext(caller_identity="buyer-a")
        created = _create_a_media_buy(agent, ctx)
        media_buy_id = created["media_buy_id"]

        params = _feedback_params(media_buy_id)
        result = asyncio.run(agent.provide_performance_feedback(params, ctx))

        assert "errors" not in result
        assert result["success"] is True
        assert result["sandbox"] is True

        # Asserts against the stored FEEDBACK# item, not just the response.
        resp = dynamo_table.query(
            KeyConditionExpression=(
                Key("pk").eq(f"MEDIABUY#{media_buy_id}") & Key("sk").begins_with("FEEDBACK#")
            )
        )
        items = resp["Items"]
        assert len(items) == 1
        assert items[0]["media_buy_id"] == media_buy_id
        assert float(items[0]["value"]) == 0.85
        assert items[0]["metric"] == "overall_performance"


class TestProvidePerformanceFeedbackUnknownMediaBuyId:
    def test_unknown_media_buy_id_returns_error_not_success(self, agent, dynamo_table):
        import asyncio

        ctx = ToolContext(caller_identity="buyer-a")
        params = _feedback_params("mb_does_not_exist")

        result = asyncio.run(agent.provide_performance_feedback(params, ctx))

        assert result["errors"][0]["code"] == "MEDIA_BUY_NOT_FOUND"


class TestProvidePerformanceFeedbackMissingIdempotencyKey:
    def test_missing_idempotency_key_returns_validation_error(self, agent, dynamo_table):
        import asyncio

        ctx = ToolContext(caller_identity="buyer-a")
        created = _create_a_media_buy(agent, ctx)
        params = _feedback_params(created["media_buy_id"])
        del params["idempotency_key"]

        result = asyncio.run(agent.provide_performance_feedback(params, ctx))

        assert result["errors"][0]["code"] == "VALIDATION_ERROR"
        assert result["errors"][0]["issues"][0]["pointer"] == "/idempotency_key"


class TestProvidePerformanceFeedbackReplayAndConflict:
    def test_same_key_same_body_replays(self, agent, dynamo_table):
        import asyncio

        ctx = ToolContext(caller_identity="buyer-a")
        created = _create_a_media_buy(agent, ctx)
        params = _feedback_params(created["media_buy_id"])

        first = asyncio.run(agent.provide_performance_feedback(dict(params), ctx))
        second = asyncio.run(agent.provide_performance_feedback(dict(params), ctx))

        assert second["success"] is True
        assert second["replayed"] is True
        assert first["success"] is True

        # Only one FEEDBACK# item is ever written - the handler body only
        # ran once, the second call was served entirely from the replay
        # cache.
        resp = dynamo_table.query(
            KeyConditionExpression=(
                Key("pk").eq(f"MEDIABUY#{created['media_buy_id']}") & Key("sk").begins_with("FEEDBACK#")
            )
        )
        assert len(resp["Items"]) == 1

    def test_same_key_different_body_raises_conflict(self, agent, dynamo_table):
        import asyncio

        ctx = ToolContext(caller_identity="buyer-a")
        created = _create_a_media_buy(agent, ctx)
        media_buy_id = created["media_buy_id"]

        first_params = _feedback_params(media_buy_id, performance_index=0.85)
        conflicting_params = _feedback_params(media_buy_id, performance_index=0.10)

        asyncio.run(agent.provide_performance_feedback(first_params, ctx))
        with pytest.raises(IdempotencyConflictError):
            asyncio.run(agent.provide_performance_feedback(conflicting_params, ctx))


# ---------------------------------------------------------------------------
# sync_accounts / list_accounts (task 8 of .kiro/specs/seller-agent-adcp-compliance/tasks.md, R6)
# ---------------------------------------------------------------------------
#
# Covers exactly what task 8 calls out: sync_accounts with a {brand,
# operator} pair persists a real DynamoDB ACCOUNT# record and is
# retrievable via list_accounts; a second sync_accounts call for the same
# pair reports action "updated" (not a duplicate/re-"created") and
# preserves the original created_at; missing idempotency_key returns
# VALIDATION_ERROR; replay/conflict, mirroring create_media_buy's coverage
# since sync_accounts IS idempotency-wrapped.


def _sync_accounts_params(*, brand_domain="acme.example.com", operator="sales.example.com", **overrides):
    params = {
        "idempotency_key": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        "accounts": [{"brand": {"domain": brand_domain}, "operator": operator, "billing": "operator"}],
    }
    params.update(overrides)
    return params


class TestSyncAccountsSuccess:
    def test_persists_real_dynamodb_account_record_with_action_created(self, agent, dynamo_table):
        import asyncio

        ctx = ToolContext(caller_identity="buyer-a")
        params = _sync_accounts_params()

        result = asyncio.run(agent.sync_accounts(params, ctx))

        assert "errors" not in result
        assert result["sandbox"] is True
        assert len(result["accounts"]) == 1
        account = result["accounts"][0]
        assert account["brand"] == {"domain": "acme.example.com"}
        assert account["operator"] == "sales.example.com"
        assert account["action"] == "created"
        assert account["status"] == "active"
        assert account["account_id"]

        # Asserts against the stored DynamoDB record, not just the response.
        stored = dynamo_table.get_item(
            Key={"pk": "ACCOUNT#acme.example.com#sales.example.com", "sk": "META"}
        ).get("Item")
        assert stored is not None
        assert stored["brand"] == "acme.example.com"
        assert stored["operator"] == "sales.example.com"


class TestSyncAccountsRetrievableViaListAccounts:
    def test_synced_account_is_retrievable_via_list_accounts(self, agent, dynamo_table):
        """The literal bar task 8 calls out: sync_accounts persists and
        is retrievable via list_accounts."""
        import asyncio

        ctx = ToolContext(caller_identity="buyer-a")
        sync_result = asyncio.run(agent.sync_accounts(_sync_accounts_params(), ctx))
        assert "errors" not in sync_result
        synced_account_id = sync_result["accounts"][0]["account_id"]

        list_result = asyncio.run(agent.list_accounts({}, ctx))

        assert "errors" not in list_result
        assert list_result["sandbox"] is True
        account_ids = {a["account_id"] for a in list_result["accounts"]}
        assert synced_account_id in account_ids
        listed = next(a for a in list_result["accounts"] if a["account_id"] == synced_account_id)
        assert listed["brand"] == {"domain": "acme.example.com"}
        assert listed["operator"] == "sales.example.com"
        assert listed["status"] == "active"


class TestSyncAccountsRepeatCallIsUpdate:
    def test_repeat_sync_reports_updated_and_does_not_duplicate_or_lose_created_at(self, agent, dynamo_table):
        import asyncio

        ctx = ToolContext(caller_identity="buyer-a")
        first = asyncio.run(
            agent.sync_accounts(
                _sync_accounts_params(idempotency_key="cccccccc-cccc-cccc-cccc-cccccccccccc"), ctx
            )
        )
        assert first["accounts"][0]["action"] == "created"

        first_stored = dynamo_table.get_item(
            Key={"pk": "ACCOUNT#acme.example.com#sales.example.com", "sk": "META"}
        ).get("Item")
        first_created_at = first_stored["created_at"]

        second = asyncio.run(
            agent.sync_accounts(
                _sync_accounts_params(idempotency_key="dddddddd-dddd-dddd-dddd-dddddddddddd"), ctx
            )
        )
        assert second["accounts"][0]["action"] == "updated"

        second_stored = dynamo_table.get_item(
            Key={"pk": "ACCOUNT#acme.example.com#sales.example.com", "sk": "META"}
        ).get("Item")
        assert second_stored["created_at"] == first_created_at

        # No duplicate record: list_accounts still returns exactly one
        # entry for this (brand, operator) pair.
        list_result = asyncio.run(agent.list_accounts({}, ctx))
        matching = [
            a
            for a in list_result["accounts"]
            if a.get("brand") == {"domain": "acme.example.com"} and a.get("operator") == "sales.example.com"
        ]
        assert len(matching) == 1


class TestSyncAccountsMissingIdempotencyKey:
    def test_missing_idempotency_key_returns_validation_error(self, agent, dynamo_table):
        import asyncio

        ctx = ToolContext(caller_identity="buyer-a")
        params = _sync_accounts_params()
        del params["idempotency_key"]

        result = asyncio.run(agent.sync_accounts(params, ctx))

        assert result["errors"][0]["code"] == "VALIDATION_ERROR"
        assert result["errors"][0]["issues"][0]["pointer"] == "/idempotency_key"


class TestSyncAccountsReplayAndConflict:
    def test_same_key_same_body_replays(self, agent, dynamo_table):
        import asyncio

        ctx = ToolContext(caller_identity="buyer-a")
        params = _sync_accounts_params()

        first = asyncio.run(agent.sync_accounts(dict(params), ctx))
        second = asyncio.run(agent.sync_accounts(dict(params), ctx))

        assert second["accounts"] == first["accounts"]
        assert second["replayed"] is True

    def test_same_key_different_body_raises_conflict(self, agent, dynamo_table):
        import asyncio

        ctx = ToolContext(caller_identity="buyer-a")
        first_params = _sync_accounts_params()
        conflicting_params = _sync_accounts_params(brand_domain="other.example.com")

        asyncio.run(agent.sync_accounts(first_params, ctx))
        with pytest.raises(IdempotencyConflictError):
            asyncio.run(agent.sync_accounts(conflicting_params, ctx))


class TestBuildToolContextCallerIdentity:
    """Bug fix (found live, task 10 verification): idempotency didn't
    dedupe over the real MCP transport because main.py never passed a
    context_factory to serve(), so ToolContext.caller_identity was always
    None. Covers main._build_tool_context deriving a real caller_identity
    from the Authorization: Bearer <jwt> header (Cognito access token,
    CUSTOM_JWT authorizer per reference-seller/agentcore/agentcore.json)
    - unit-level, without a live AWS call, using a real (locally-signed)
    JWT and a stand-in object exposing the same `.headers` shape
    `RequestMetadata.request_context` (a Starlette Request) provides."""

    class _FakeRequest:
        def __init__(self, headers: dict[str, str]):
            self.headers = headers

    def test_derives_caller_identity_from_sub_claim(self):
        import jwt as _jwt

        # nosemgrep: jwt-python-hardcoded-secret - Test fixture secret, not a credential
        token = _jwt.encode({"sub": "cognito-user-abc123", "client_id": "app-client-1"}, "test-secret", algorithm="HS256")
        request = self._FakeRequest({"authorization": f"Bearer {token}"})

        identity = main._caller_identity_from_authorization_header(request)

        assert identity == "cognito-user-abc123"

    def test_falls_back_to_client_id_when_sub_absent(self):
        import jwt as _jwt

        # nosemgrep: jwt-python-hardcoded-secret - Test fixture secret, not a credential
        token = _jwt.encode({"client_id": "app-client-1"}, "test-secret", algorithm="HS256")
        request = self._FakeRequest({"authorization": f"Bearer {token}"})

        identity = main._caller_identity_from_authorization_header(request)

        assert identity == "app-client-1"

    def test_distinct_callers_get_distinct_identities(self):
        """The requirement this bug fix exists to
        satisfy: two different authenticated callers must resolve to two
        different caller_identity values, never a shared/static one."""
        import jwt as _jwt

        # nosemgrep: jwt-python-hardcoded-secret - Test fixture secret, not a credential
        token_a = _jwt.encode({"sub": "buyer-a"}, "s", algorithm="HS256")
        # nosemgrep: jwt-python-hardcoded-secret - Test fixture secret, not a credential
        token_b = _jwt.encode({"sub": "buyer-b"}, "s", algorithm="HS256")

        identity_a = main._caller_identity_from_authorization_header(
            self._FakeRequest({"authorization": f"Bearer {token_a}"})
        )
        identity_b = main._caller_identity_from_authorization_header(
            self._FakeRequest({"authorization": f"Bearer {token_b}"})
        )

        assert identity_a == "buyer-a"
        assert identity_b == "buyer-b"
        assert identity_a != identity_b

    def test_missing_authorization_header_returns_none(self):
        """Local dev (no platform authorizer in front) falls through to
        no dedup, rather than substituting an identity."""
        request = self._FakeRequest({})
        assert main._caller_identity_from_authorization_header(request) is None

    def test_missing_request_context_returns_none(self):
        assert main._caller_identity_from_authorization_header(None) is None

    def test_malformed_bearer_token_returns_none(self):
        request = self._FakeRequest({"authorization": "Bearer not-a-jwt"})
        assert main._caller_identity_from_authorization_header(request) is None

    def test_build_tool_context_wires_caller_identity_end_to_end(self):
        import jwt as _jwt

        from adcp.server import RequestMetadata

        # nosemgrep: jwt-python-hardcoded-secret - Test fixture secret, not a credential
        token = _jwt.encode({"sub": "cognito-user-xyz"}, "s", algorithm="HS256")
        request = self._FakeRequest({"authorization": f"Bearer {token}"})
        meta = RequestMetadata(tool_name="create_media_buy", transport="mcp", request_context=request)

        context = main._build_tool_context(meta)

        assert context.caller_identity == "cognito-user-xyz"

    def test_end_to_end_dedup_now_works_with_real_context_factory(self, agent, dynamo_table):
        """Confirms the actual bug is fixed: two calls carrying the same
        idempotency_key + body, through the same context_factory a real
        deployed server would use, replay instead of creating two media
        buys - the exact failure mode task 10's live verification found."""
        import asyncio

        import jwt as _jwt

        from adcp.server import RequestMetadata

        # nosemgrep: jwt-python-hardcoded-secret - Test fixture secret, not a credential
        token = _jwt.encode({"sub": "cognito-user-dedup-test"}, "s", algorithm="HS256")
        request = self._FakeRequest({"authorization": f"Bearer {token}"})
        meta = RequestMetadata(tool_name="create_media_buy", transport="mcp", request_context=request)
        ctx = main._build_tool_context(meta)

        params = _base_params()
        first = asyncio.run(agent.create_media_buy(dict(params), ctx))
        second = asyncio.run(agent.create_media_buy(dict(params), ctx))

        assert second["media_buy_id"] == first["media_buy_id"]
        assert second.get("replayed") is True


class TestListAccountsNoAccounts:
    def test_no_synced_accounts_returns_empty_list(self, agent, dynamo_table):
        import asyncio

        ctx = ToolContext(caller_identity="buyer-a")
        result = asyncio.run(agent.list_accounts({}, ctx))

        assert "errors" not in result
        assert result["accounts"] == []


class TestCreateMediaBuyGovernance:
    """U3/BR-U3-3 through BR-U3-10: create_media_buy's new governance branch.

    An account is "governed" once account_store has a governance_agents entry for its
    (brand, operator) -- i.e. sync_governance was called first. governance_verification's own
    verify_intent_token/run_execution_check are mocked here (they have their own dedicated test
    suite in test_governance_verification.py); these tests exercise ONLY create_media_buy's own
    control flow: does it call the right things in the right order, and does each outcome map to
    the right error code.
    """

    GOVERNED_ACCOUNT = {"brand": {"domain": "governed.example"}, "operator": "gov-agency"}
    GOVERNANCE_AGENT = {
        "url": "https://governance.example/mcp",
        "authentication": {"schemes": ["Bearer"], "credentials": "x" * 32},
    }

    def _sync_governed_account(self):
        from main import account_store

        account_store.upsert(
            "governed.example", "gov-agency", governance_agents=[self.GOVERNANCE_AGENT]
        )

    def test_an_ungoverned_account_never_calls_governance_verification(
        self, agent, dynamo_table, monkeypatch
    ):
        """BR-U3-10: an account with no governance_agents is completely unaffected."""
        import asyncio

        called = []
        monkeypatch.setattr(
            main.governance_verification,
            "verify_intent_token",
            lambda *a, **k: called.append(True),
        )
        ctx = ToolContext(caller_identity="buyer-a")
        params = _base_params(account={"brand": {"domain": "acme.example"}, "operator": "sales.example"})

        result = asyncio.run(agent.create_media_buy(params, ctx))
        assert "errors" not in result
        assert called == []

    def test_a_governed_account_with_no_governance_context_is_refused_before_any_network_call(
        self, agent, dynamo_table, monkeypatch
    ):
        import asyncio

        self._sync_governed_account()
        called = []
        monkeypatch.setattr(
            main.governance_verification,
            "verify_intent_token",
            lambda *a, **k: called.append(True),
        )
        ctx = ToolContext(caller_identity="buyer-a")
        params = _base_params(account=dict(self.GOVERNED_ACCOUNT), plan_id="plan-1")
        # governance_context deliberately omitted.

        result = asyncio.run(agent.create_media_buy(params, ctx))
        assert result["errors"][0]["code"] == "PERMISSION_DENIED"
        assert called == []

    def test_a_governed_account_whose_token_fails_verification_is_refused(
        self, agent, dynamo_table, monkeypatch
    ):
        import asyncio

        self._sync_governed_account()

        async def fake_verify(*args, **kwargs):
            return main.governance_verification.IntentTokenVerificationResult(
                valid=False, failure_reason="signature_invalid"
            )

        monkeypatch.setattr(main.governance_verification, "verify_intent_token", fake_verify)
        ctx = ToolContext(caller_identity="buyer-a")
        params = _base_params(
            account=dict(self.GOVERNED_ACCOUNT), plan_id="plan-1", governance_context="bad-token"
        )

        result = asyncio.run(agent.create_media_buy(params, ctx))
        assert result["errors"][0]["code"] == "PERMISSION_DENIED"
        # NFR-U3-3: the specific failure_reason must never be echoed to the caller.
        assert "signature_invalid" not in str(result)

    def test_verified_but_the_outbound_execution_check_is_unreachable(
        self, agent, dynamo_table, monkeypatch
    ):
        import asyncio

        self._sync_governed_account()

        async def fake_verify(*args, **kwargs):
            return main.governance_verification.IntentTokenVerificationResult(valid=True, claims={})

        async def fake_execution_check(*args, **kwargs):
            return main.governance_verification.ExecutionCheckResult(outcome="unreachable")

        monkeypatch.setattr(main.governance_verification, "verify_intent_token", fake_verify)
        monkeypatch.setattr(main.governance_verification, "run_execution_check", fake_execution_check)
        ctx = ToolContext(caller_identity="buyer-a")
        params = _base_params(
            account=dict(self.GOVERNED_ACCOUNT), plan_id="plan-1", governance_context="a-token"
        )

        result = asyncio.run(agent.create_media_buy(params, ctx))
        assert result["errors"][0]["code"] == "GOVERNANCE_UNAVAILABLE"

    def test_verified_but_the_execution_check_verdict_is_denied(self, agent, dynamo_table, monkeypatch):
        import asyncio

        self._sync_governed_account()

        async def fake_verify(*args, **kwargs):
            return main.governance_verification.IntentTokenVerificationResult(valid=True, claims={})

        async def fake_execution_check(*args, **kwargs):
            return main.governance_verification.ExecutionCheckResult(outcome="denied")

        monkeypatch.setattr(main.governance_verification, "verify_intent_token", fake_verify)
        monkeypatch.setattr(main.governance_verification, "run_execution_check", fake_execution_check)
        ctx = ToolContext(caller_identity="buyer-a")
        params = _base_params(
            account=dict(self.GOVERNED_ACCOUNT), plan_id="plan-1", governance_context="a-token"
        )

        result = asyncio.run(agent.create_media_buy(params, ctx))
        assert result["errors"][0]["code"] == "GOVERNANCE_DENIED"

    def test_verified_and_approved_creates_the_media_buy_normally(self, agent, dynamo_table, monkeypatch):
        import asyncio

        self._sync_governed_account()

        async def fake_verify(*args, **kwargs):
            return main.governance_verification.IntentTokenVerificationResult(valid=True, claims={})

        async def fake_execution_check(*args, **kwargs):
            return main.governance_verification.ExecutionCheckResult(
                outcome="approved", governance_context="new-context"
            )

        monkeypatch.setattr(main.governance_verification, "verify_intent_token", fake_verify)
        monkeypatch.setattr(main.governance_verification, "run_execution_check", fake_execution_check)
        ctx = ToolContext(caller_identity="buyer-a")
        params = _base_params(
            account=dict(self.GOVERNED_ACCOUNT), plan_id="plan-1", governance_context="a-token"
        )

        result = asyncio.run(agent.create_media_buy(params, ctx))
        assert "errors" not in result
        assert "media_buy_id" in result
