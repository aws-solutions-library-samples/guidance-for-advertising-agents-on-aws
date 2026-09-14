"""
State access layer for the AdCP reference seller — backs `create_media_buy`,
`update_media_buy`, `get_media_buys`, `get_media_buy_delivery`,
`provide_performance_feedback`, `sync_accounts`, and `list_accounts` (tasks
3-8 of `.kiro/specs/seller-agent-adcp-compliance/design.md`) with the single
DynamoDB table `deploy_state_table.py` provisions
(`adcp-reference-seller-state`, `pk`/`sk`, on-demand billing, TTL on `ttl`).

Same wrapping pattern as `agents/buyer/reference-buyer/session_store.py`
(one module, one table, plain functions/classes around boto3), adapted to
this project's item types
(see `deploy_state_table.py`'s module docstring):

    pk                                  sk                  item type
    ----------------------------------  ------------------  -----------------
    MEDIABUY#<media_buy_id>             META                media buy record
    MEDIABUY#<media_buy_id>             FEEDBACK#<iso_ts>   performance feedback
    IDEMPOTENCY#<scope_key>#<key>       META                idempotency record
    ACCOUNT#<brand>#<operator>          META                buyer-declared account

**Idempotency, reworked (task 2 rework):** this module no longer hand-rolls
idempotency. The `adcp` SDK already ships a spec-correct implementation
(`adcp.server.idempotency`) covering AdCP #2315's actual equivalence rule
(RFC 8785 JCS canonicalization + a closed field-exclusion list — see
`adcp.server.idempotency.canonicalize.canonical_json_sha256`) and
per-principal cache scoping (`IdempotencyStore` composes
`(tenant_id, caller_identity)` from `ToolContext` into a scope key, so two
buyers can never collide on the same `idempotency_key` namespace — the
hand-rolled version this replaced had no such scoping). This module now only
supplies the missing piece: a DynamoDB-backed `IdempotencyBackend`
(`DynamoDBIdempotencyBackend` below), so `main.py`'s task handlers (task 3+)
should construct `adcp.server.idempotency.IdempotencyStore(backend=
DynamoDBIdempotencyBackend(), ttl_seconds=IDEMPOTENCY_TTL_SECONDS)` once at
module scope and decorate each mutating handler with `@idempotency_store.wrap`
— NOT the old `check()`/`record()` pattern design.md's original pseudocode
described (that pseudocode predates this SDK capability being discovered).
`get_adcp_capabilities` (task 9) should call `idempotency_store.capability()`
rather than hand-writing the `{"supported": ..., "replay_ttl_seconds": ...}`
dict.

`MediaBuyStore` and `AccountStore` are unchanged — they're genuine sandbox
business state with no SDK equivalent, not idempotency machinery.

This module is deliberately "dumb persistence": no AdCP business logic
(product lookup, `media_buy_id` generation, response building) lives here —
that belongs to the task handlers in `main.py` (task 3+), which call these
stores and hand the result to `adcp.server.responses` builders. Every method
here either reflects a real DynamoDB read/write or raises; nothing here
substitutes a value that isn't in the table.
"""

from __future__ import annotations

from aws_region import region as resolve_region

import os
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

from adcp.server.idempotency.backends import CachedResponse, IdempotencyBackend

# 24h replay window, matching the `replay_ttl_seconds: 86400` this agent's
# `get_adcp_capabilities` advertises once idempotency is real (task 9). Also
# the `ttl_seconds` this module's callers should pass to the SDK's
# `IdempotencyStore` constructor.
IDEMPOTENCY_TTL_SECONDS = 24 * 60 * 60


class StateStoreError(RuntimeError):
    """Raised when the state table isn't configured or a DynamoDB call fails
    in a way that isn't one of this module's more specific exceptions."""


class MediaBuyNotFoundError(StateStoreError):
    """Raised by `MediaBuyStore.update`/`add_feedback` when `media_buy_id`
    doesn't exist — callers (task handlers) turn this into an AdCP error
    response rather than a success."""


def _table():
    table_name = os.environ.get("STATE_TABLE_NAME", "").strip()
    if not table_name:
        raise StateStoreError(
            "STATE_TABLE_NAME is not set. Run deploy_state_table.py and add the "
            "resulting table name to .env."
        )
    region = resolve_region()
    resource = boto3.resource("dynamodb", region_name=region)
    return resource.Table(table_name)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _from_dynamo(value: Any) -> Any:
    """Recursively convert DynamoDB's Decimal numbers back to plain int/float."""
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, list):
        return [_from_dynamo(v) for v in value]
    if isinstance(value, dict):
        return {k: _from_dynamo(v) for k, v in value.items()}
    return value


def _to_dynamo(value: Any) -> Any:
    """Recursively convert plain Python floats to Decimal — boto3's DynamoDB
    resource rejects float directly. Budgets/CPMs in request bodies are
    ordinary floats, so this runs on every write."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, list):
        return [_to_dynamo(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_dynamo(v) for k, v in value.items()}
    return value


# ---------------------------------------------------------------------------
# DynamoDBIdempotencyBackend
# ---------------------------------------------------------------------------


class DynamoDBIdempotencyBackend(IdempotencyBackend):
    """`adcp.server.idempotency.backends.IdempotencyBackend` implementation
    on top of this project's shared `adcp-reference-seller-state` table.

    Item shape, same `pk`/`sk` convention as every other item type in this
    table:

        pk: IDEMPOTENCY#<scope_key>#<key>    sk: META
        payload_hash, response (JSON-ish dict), expires_at (iso), ttl (epoch)

    `scope_key` and `key` are exactly the two arguments the SDK's
    `IdempotencyStore` passes to `get`/`put` — `scope_key` is already
    `(tenant_id, caller_identity)`-composed by the SDK before it ever reaches
    this backend, so this class doesn't need (and shouldn't try) to do any
    principal-scoping itself; it just needs a stable, DynamoDB-key-safe way
    to combine the two strings the SDK hands it. `#` is reserved as this
    table's own segment separator, so scope keys/keys containing `#` are
    still safe here (DynamoDB does no parsing of `pk`) — the SDK's own scope
    separator is `\\x1e`, disjoint from `#`.

    **First-writer-wins.** The SDK's own contract
    (`IdempotencyBackend.put`'s docstring) says `put` is only called after
    the store's pre-check (`get`) already saw an empty or expired slot — but
    that pre-check is not a lock, so two concurrent identical requests can
    both observe "empty" and both race into `put`. A naive unconditional
    `put_item` would let the second writer silently overwrite the first's
    `payload_hash`/`response`, breaking the invariant "same (scope_key, key)
    always maps to the same cached response" for one of the two callers.
    `put` here uses a conditional `PutItem` (`attribute_not_exists(pk)`) to
    make the first writer's row stick; on `ConditionalCheckFailedException`
    it re-reads and returns without raising — the SDK's `IdempotencyStore`
    doesn't call `put` a second time in a way that expects a return value it
    checks, so silently deferring to the existing row is exactly "only one
    wins, both callers get the same response" (both callers' handler
    invocations already ran by the time either calls `put`, but the *cached*
    entry that governs every subsequent replay is the first writer's,
    consistent with the abstract contract).
    """

    def __init__(self, table: Any | None = None) -> None:
        self._table_override = table

    def _tbl(self):
        return self._table_override if self._table_override is not None else _table()

    @staticmethod
    def _pk(scope_key: str, key: str) -> str:
        return f"IDEMPOTENCY#{scope_key}#{key}"

    async def get(self, scope_key: str, key: str) -> CachedResponse | None:
        """Return the cached entry, or None if missing or expired.

        Expiry is enforced here (not just via the table's TTL attribute,
        which is a best-effort background sweep with no latency guarantee)
        so a row DynamoDB hasn't gotten around to deleting yet still reads
        as "gone" the moment it's past its `expires_at`."""
        item = self._tbl().get_item(Key={"pk": self._pk(scope_key, key), "sk": "META"}).get("Item")
        if item is None:
            return None
        expires_at_epoch = float(item["expires_at_epoch"])
        if expires_at_epoch <= time.time():
            return None
        return CachedResponse(
            payload_hash=item["payload_hash"],
            response=_from_dynamo(item.get("response", {})),
            expires_at_epoch=expires_at_epoch,
        )

    async def put(self, scope_key: str, key: str, entry: CachedResponse) -> None:
        """Store `entry` under `(scope_key, key)`, guarded by a conditional
        PutItem (`attribute_not_exists(pk)`) — see class docstring for why
        this diverges from the abstract method's "overwrites any prior
        entry" note: this backend additionally has to survive the race the
        SDK's own pre-check doesn't close, so it only overwrites a slot that
        is provably empty (never existed) or has already expired."""
        item = {
            "pk": self._pk(scope_key, key),
            "sk": "META",
            "payload_hash": entry.payload_hash,
            "response": _to_dynamo(entry.response),
            "expires_at_epoch": Decimal(str(entry.expires_at_epoch)),
            "created_at": _now_iso(),
            # DynamoDB TTL attribute, matching deploy_state_table.py's
            # `AttributeName: ttl` — epoch seconds, integer.
            "ttl": int(entry.expires_at_epoch),
        }
        try:
            self._tbl().put_item(Item=item, ConditionExpression=Attr("pk").not_exists())
            return
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise

        # Lost the race (or a live, unexpired row already exists): if the
        # existing row has actually expired, this "first" writer among the
        # still-live contenders should win instead of leaving a stale entry
        # in place forever. If it's live, defer to it — the SDK's own
        # equivalence check (comparing `payload_hash`) is what will
        # determine replay-vs-conflict on the *next* request; this method's
        # job is only "don't lose data, don't let two different-payload
        # writers stomp each other silently."
        existing = self._tbl().get_item(Key={"pk": self._pk(scope_key, key), "sk": "META"}).get("Item")
        if existing is not None and float(existing["expires_at_epoch"]) > time.time():
            return

        # Existing row (if any) is expired -> conditional update keyed on
        # the same "not exists OR expired" guard, expressed via an
        # attribute-comparison condition rather than attribute_not_exists,
        # so this only ever replaces a genuinely expired row.
        try:
            self._tbl().put_item(
                Item=item,
                ConditionExpression=Attr("pk").not_exists() | Attr("expires_at_epoch").lte(Decimal(str(time.time()))),
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            # A different concurrent writer won this second race too; defer
            # to whatever is now stored rather than raising — consistent
            # with "only one wins, both callers get the same response".

    async def delete_expired(self, now_epoch: float | None = None) -> int:
        """Best-effort sweep of expired idempotency entries. The table's own
        TTL attribute already does this in the background at the DynamoDB
        service level, so this is a no-op — matching the abstract method's
        explicit allowance ("Backends that have natural TTL primitives... may
        implement this as a no-op")."""
        return 0


# ---------------------------------------------------------------------------
# MediaBuyStore
# ---------------------------------------------------------------------------


class MediaBuyStore:
    """Wraps the `MEDIABUY#<id>` / `META` and `MEDIABUY#<id>` / `FEEDBACK#<ts>`
    items. Pure persistence — `media_buy_id` generation, product lookup, and
    AdCP response building are the task handlers' job (main.py), not this
    module's."""

    def __init__(self, table: Any | None = None) -> None:
        self._table_override = table

    def _tbl(self):
        return self._table_override if self._table_override is not None else _table()

    def create(
        self,
        media_buy_id: str,
        *,
        product_id: str,
        pricing_option_id: str,
        budget: Any,
        packages: list[dict[str, Any]],
        account: dict[str, Any] | None = None,
        status: str = "completed",
    ) -> dict[str, Any]:
        """Create the `META` record for a new media buy. Overwrites nothing —
        callers are expected to have already generated a fresh, unique
        `media_buy_id` (e.g. `mb_<uuid4 hex>`)."""
        now = _now_iso()
        item = {
            "pk": f"MEDIABUY#{media_buy_id}",
            "sk": "META",
            "media_buy_id": media_buy_id,
            "product_id": product_id,
            "pricing_option_id": pricing_option_id,
            "budget": _to_dynamo(budget),
            "packages": _to_dynamo(packages),
            "status": status,
            "created_at": now,
            "account": _to_dynamo(account),
        }
        self._tbl().put_item(Item=item)
        return _from_dynamo(item)

    def get(self, media_buy_id: str) -> dict[str, Any] | None:
        item = self._tbl().get_item(Key={"pk": f"MEDIABUY#{media_buy_id}", "sk": "META"}).get("Item")
        return _from_dynamo(item) if item is not None else None

    def get_many(self, media_buy_ids: list[str] | None = None) -> list[dict[str, Any]]:
        """No filter: every stored media buy (a table Scan filtered to
        `sk = META` — acceptable at this agent's fixture-scale data volume,
        see design.md). With a filter: only the requested ids, via one
        `get_item` per id (skipping any that don't exist rather than
        raising, matching AdCP's "return only the matching stored records"
        semantics for `get_media_buys`)."""
        if media_buy_ids is not None:
            results = []
            for media_buy_id in media_buy_ids:
                record = self.get(media_buy_id)
                if record is not None:
                    results.append(record)
            return results

        results: list[dict[str, Any]] = []
        scan_kwargs: dict[str, Any] = {"FilterExpression": Attr("sk").eq("META") & Attr("media_buy_id").exists()}
        while True:
            resp = self._tbl().scan(**scan_kwargs)
            results.extend(_from_dynamo(item) for item in resp.get("Items", []))
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
            scan_kwargs["ExclusiveStartKey"] = last_key
        return results

    def update(self, media_buy_id: str, **fields: Any) -> dict[str, Any]:
        """Apply `fields` (e.g. `budget=..., status="paused"`) to the
        existing record. Raises `MediaBuyNotFoundError` if `media_buy_id`
        doesn't exist — condition-checked (`attribute_exists(pk)`) rather
        than an unconditional `update_item` that would otherwise silently
        create a half-empty record for an unknown id."""
        if not fields:
            existing = self.get(media_buy_id)
            if existing is None:
                raise MediaBuyNotFoundError(f"media_buy_id {media_buy_id!r} not found")
            return existing

        update_expr_parts = []
        expr_names: dict[str, str] = {}
        expr_values: dict[str, Any] = {}
        for i, (field_name, value) in enumerate(fields.items()):
            name_placeholder = f"#f{i}"
            value_placeholder = f":v{i}"
            update_expr_parts.append(f"{name_placeholder} = {value_placeholder}")
            expr_names[name_placeholder] = field_name
            expr_values[value_placeholder] = _to_dynamo(value)

        try:
            resp = self._tbl().update_item(
                Key={"pk": f"MEDIABUY#{media_buy_id}", "sk": "META"},
                UpdateExpression="SET " + ", ".join(update_expr_parts),
                ConditionExpression=Attr("pk").exists(),
                ExpressionAttributeNames=expr_names,
                ExpressionAttributeValues=expr_values,
                ReturnValues="ALL_NEW",
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise MediaBuyNotFoundError(f"media_buy_id {media_buy_id!r} not found") from exc
            raise
        return _from_dynamo(resp["Attributes"])

    def add_feedback(
        self,
        media_buy_id: str,
        *,
        metric: str,
        value: Any,
        timestamp: str | None = None,
    ) -> dict[str, Any]:
        """Write a `FEEDBACK#<iso_timestamp>` item under the media buy's
        `pk`. Raises `MediaBuyNotFoundError` if `media_buy_id` doesn't exist
        — checked explicitly first since feedback rows have no FK
        constraint to lean on in a single-table DynamoDB design."""
        if self.get(media_buy_id) is None:
            raise MediaBuyNotFoundError(f"media_buy_id {media_buy_id!r} not found")

        ts = timestamp or _now_iso()
        item = {
            "pk": f"MEDIABUY#{media_buy_id}",
            "sk": f"FEEDBACK#{ts}",
            "media_buy_id": media_buy_id,
            "metric": metric,
            "value": _to_dynamo(value),
            "timestamp": ts,
        }
        self._tbl().put_item(Item=item)
        return _from_dynamo(item)


# ---------------------------------------------------------------------------
# AccountStore
# ---------------------------------------------------------------------------


class AccountStore:
    """Wraps the `ACCOUNT#<brand>#<operator>` / `META` item — buyer-declared
    accounts for `sync_accounts`/`list_accounts` (R6). No seller-side
    onboarding/approval step, matching this agent's existing
    `require_operator_auth: false` capability declaration."""

    def __init__(self, table: Any | None = None) -> None:
        self._table_override = table

    def _tbl(self):
        return self._table_override if self._table_override is not None else _table()

    def upsert(self, brand: str, operator: str, **extra: Any) -> dict[str, Any]:
        """Create or refresh the account record for `(brand, operator)`.
        `created_at` is preserved across repeat `sync_accounts` calls for
        the same pair (`if_not_exists`); any other field in `extra`
        (e.g. buyer-supplied metadata) is overwritten with the latest
        value, matching "sync" semantics rather than "create-once".

        All field names route through `ExpressionAttributeNames` (not
        interpolated literally into the UpdateExpression) since `operator`
        and other plausible field names are DynamoDB reserved keywords."""
        now = _now_iso()
        fields = {"brand": brand, "operator": operator, **extra}
        update_expr_parts = []
        expr_names: dict[str, str] = {}
        expr_values: dict[str, Any] = {":now": now}
        for i, (field_name, value) in enumerate(fields.items()):
            name_placeholder = f"#f{i}"
            value_placeholder = f":v{i}"
            expr_names[name_placeholder] = field_name
            if field_name == "created_at":
                # Shouldn't normally be passed via extra, but guard anyway.
                continue
            expr_values[value_placeholder] = _to_dynamo(value)
            update_expr_parts.append(f"{name_placeholder} = {value_placeholder}")
        expr_names["#created_at"] = "created_at"
        update_expr_parts.append("#created_at = if_not_exists(#created_at, :now)")

        resp = self._tbl().update_item(
            Key={"pk": f"ACCOUNT#{brand}#{operator}", "sk": "META"},
            UpdateExpression="SET " + ", ".join(update_expr_parts),
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_values,
            ReturnValues="ALL_NEW",
        )
        return _from_dynamo(resp["Attributes"])

    def get(self, brand: str, operator: str) -> dict[str, Any] | None:
        """Return the existing account record for `(brand, operator)`, or
        `None` if this pair has never been synced. Used by `sync_accounts`
        (task 8, main.py) as a real pre-upsert existence check — the
        response's `action: "created"|"updated"` field is derived
        from this call's result, never hardcoded."""
        item = self._tbl().get_item(Key={"pk": f"ACCOUNT#{brand}#{operator}", "sk": "META"}).get("Item")
        return _from_dynamo(item) if item is not None else None

    def list(self) -> list[dict[str, Any]]:
        """Every stored account (a table Scan filtered to `pk` starting with
        `ACCOUNT#` — same fixture-scale rationale as `MediaBuyStore.get_many`;
        would need a GSI at real production scale)."""
        results: list[dict[str, Any]] = []
        scan_kwargs: dict[str, Any] = {
            "FilterExpression": Attr("pk").begins_with("ACCOUNT#") & Attr("sk").eq("META")
        }
        while True:
            resp = self._tbl().scan(**scan_kwargs)
            results.extend(_from_dynamo(item) for item in resp.get("Items", []))
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
            scan_kwargs["ExclusiveStartKey"] = last_key
        return results


def new_media_buy_id() -> str:
    """A fresh seller-generated media buy id (`mb_<uuid4 hex>`) — never
    buyer-supplied, per AdCP's "seller-assigned media_buy_id" model. Lives
    here (not main.py) since it's a state-layer concern shared by every
    task that creates a media buy."""
    return f"mb_{uuid.uuid4().hex}"
