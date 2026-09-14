"""DynamoDB storage for the AdCP SDK's idempotency store.

`report_plan_outcome` carries a required `idempotency_key`, described in the schema as preventing
duplicate outcome reports on retries. Until now this agent accepted it and ignored it, which was nearly
harmless: a retry produced a duplicate outcome row and inflated `outcomes_reported`, but no budget
figure was wrong.

That changes with the cross-plan aggregate. It increments with an atomic `ADD` and has no decrement, so
one retried outcome permanently over-counts committed spend -- and the fragmentation defense then starts
denying legitimate buys using a number nobody can correct.

Only the **storage** is written here. The SDK owns the parts that are easy to get subtly wrong:

    IdempotencyStore(DynamoIdempotencyBackend(), ttl_seconds=86400).wrap(handler)

canonical request hashing (`canonical_json_sha256` over the payload with excluded fields stripped),
replay of the original response, the `replayed: true` envelope marker, and `IDEMPOTENCY_CONFLICT` when a
key is reused with a different payload. Reimplementing any of that would be a second source of truth for
behaviour a caller may depend on.

    PK                  SK           what it is
    IDEM#<scope_key>    KEY#<key>    one cached response, with its payload hash and expiry

**Neither shipped backend fits.** `MemoryBackend` is per-process, and AgentCore Runtime is not one
process -- a retry landing on another instance would double-count, which is the failure being fixed. It
would pass every local test and be wrong in deployment. `PgBackend` needs Postgres, which this stack
does not have.

**Expiry is checked here, not left to TTL.** DynamoDB deletes an expired item within a few days of
expiry rather than at expiry, so an expired entry stays readable in the interim. The SDK's contract is
that `get` returns None once expired, and `CachedResponse.expires_at_epoch` is documented as "reads after
this time return None". TTL reclaims storage; it does not decide validity. Without the comparison below,
a response would replay long past `replay_ttl_seconds`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from decimal import Decimal
from typing import Any

import boto3
from botocore.config import Config

from adcp.server.idempotency import CachedResponse, IdempotencyBackend

_log = logging.getLogger(__name__)

TABLE_ENV = "STATE_TABLE_NAME"

_CONNECT_TIMEOUT_SECONDS = 0.5
_READ_TIMEOUT_SECONDS = 1.0
_MAX_ATTEMPTS = 2

_table = None


def table():
    """The table resource, built on first use with explicit timeouts (NFR-2)."""
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
    global _table
    _table = None


class DynamoIdempotencyBackend(IdempotencyBackend):
    """`IdempotencyBackend` over the agent's existing state table."""

    def __init__(self, *, clock=time.time) -> None:
        self._clock = clock

    @staticmethod
    def _key(scope_key: str, key: str) -> dict[str, str]:
        return {"PK": f"IDEM#{scope_key}", "SK": f"KEY#{key}"}

    async def get(self, scope_key: str, key: str) -> CachedResponse | None:
        """The cached entry, or None if missing **or expired**.

        The expiry comparison is the whole reason this is not a bare `GetItem`. An item past its
        `expires_at_epoch` is still in the table for days before TTL removes it, and returning it would
        replay a response after the window the SDK validated on construction.

        The stale item is left in place rather than deleted. `MemoryBackend` evicts eagerly because that
        is free in a dict; here a delete costs a write on the read path, and TTL already reclaims it.
        """
        item = await asyncio.to_thread(
            lambda: table().get_item(Key=self._key(scope_key, key)).get("Item")
        )
        if not item:
            return None

        expires_at_epoch = float(item["expires_at"])
        if expires_at_epoch <= self._clock():
            return None

        return CachedResponse(
            payload_hash=str(item["payload_hash"]),
            response=json.loads(item["response"]),
            expires_at_epoch=expires_at_epoch,
        )

    async def put(self, scope_key: str, key: str, entry: CachedResponse) -> None:
        """Store an entry, overwriting any prior one for the same key.

        `response` is stored as a JSON string rather than a DynamoDB map. The payload is an arbitrary
        AdCP response, and round-tripping it through DynamoDB's type system would turn every float into
        a `Decimal` and reject an empty set -- so the replayed response would not be byte-identical to
        the one originally returned, which is the one thing a replay has to be.

        `expires_at` is written as a **Number** so DynamoDB TTL recognises it. A string would not
        error; the items would simply never be reclaimed.
        """
        await asyncio.to_thread(
            lambda: table().put_item(
                Item={
                    **self._key(scope_key, key),
                    "payload_hash": entry.payload_hash,
                    "response": json.dumps(entry.response, separators=(",", ":")),
                    "expires_at": Decimal(str(int(entry.expires_at_epoch))),
                }
            )
        )

    async def delete_expired(self, now_epoch: float | None = None) -> int:
        """No-op sweep: DynamoDB TTL reclaims these items.

        Returns 0 truthfully -- this call removed nothing. A real sweep would need a `Scan`, whose cost
        scales with table size, and TTL already does the work for free. `get` is what guarantees an
        expired entry is never served, so reclamation timing has no effect on correctness.
        """
        return 0


#: The replay window, declared on `get_adcp_capabilities` as
#: `adcp.idempotency.replay_ttl_seconds` and used as the store's actual TTL. One constant so the
#: declaration cannot promise a window the store does not honour -- the same discipline as
#: `aggregation.AGGREGATION_WINDOW_DAYS`. Must stay inside AdCP's `[3600, 604800]` range, which the
#: store validates on construction.
REPLAY_TTL_SECONDS = 86400


def make_store(*, ttl_seconds: int = REPLAY_TTL_SECONDS):
    """The SDK store wired to this backend.

    `ttl_seconds` must sit in AdCP's `[3600, 604800]` range for
    `capabilities.idempotency.replay_ttl_seconds`; the store validates that on construction, so an
    out-of-range value fails at startup rather than on the first retry. 86400 is the SDK's default and
    its documented floor.
    """
    from adcp.server import IdempotencyStore  # noqa: PLC0415 -- keeps import cost off module load

    return IdempotencyStore(DynamoIdempotencyBackend(), ttl_seconds=ttl_seconds)
