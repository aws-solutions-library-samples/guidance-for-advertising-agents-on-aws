"""Create the governance agent's DynamoDB state table.

Idempotent: an existing table is reported and left alone. Mirrors the sellers' equivalent script so the
whole repo is deployed the same way.

Run with `--apply`. Without it the script prints what it would do and changes nothing, because creating
infrastructure should take a deliberate flag rather than a bare invocation.

## `--enable-ttl`

Separate from creation, and separately flagged, because it is the one operation here that can DELETE data.
DynamoDB allows **exactly one TTL attribute per table**, and this table has two item families that should
expire on different schedules -- `AGG#` (90 d) and `IDEM#` (24 h) -- plus four that must never expire
(`PLAN#`, `CHECK#`, `OUTCOME#`, `ESCALATION#`). All of the expiring ones therefore write the SAME attribute
name, `expires_at`, with different values.

That single-name constraint is the trap: a family that writes `ttl` instead silently stops expiring, with
no error and no symptom other than a bill. And the inverse is worse -- a durable item that acquires
`expires_at` by mistake is deleted with no recovery beyond point-in-time restore. So `--enable-ttl` scans
for that mistake and REFUSES rather than enabling and hoping.

Enabling TTL does not make expiry correct on its own: `idempotency_backend.get` compares the timestamp
itself, because DynamoDB's deletion is asynchronous (documented as typically within a few days) and an
expired-but-not-yet-deleted item must not replay.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

import boto3
from botocore.exceptions import ClientError

INSTANCE_PREFIX = os.environ.get("INSTANCE_PREFIX", "adcp").strip() or "adcp"
TABLE_NAME = f"{INSTANCE_PREFIX}-reference-governance-state"


def create(apply: bool) -> int:
    dynamodb = boto3.client("dynamodb")
    try:
        existing = dynamodb.describe_table(TableName=TABLE_NAME)["Table"]
        print(f"Table already exists: {TABLE_NAME} (status {existing['TableStatus']})")
        # Reported, not "fixed". Changing the key schema of a live table is not something a deploy
        # script should attempt silently.
        keys = [(k["AttributeName"], k["KeyType"]) for k in existing["KeySchema"]]
        print(f"  key schema: {keys}")
        return 0
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceNotFoundException":
            raise

    if not apply:
        print(f"Would create table {TABLE_NAME} (PK/SK, on-demand billing). Re-run with --apply.")
        return 0

    print(f"Creating table {TABLE_NAME}")
    dynamodb.create_table(
        TableName=TABLE_NAME,
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
        # SECURITY-01: encryption at rest. DynamoDB encrypts by default with an AWS-owned key; stated
        # explicitly so the choice is visible in the template rather than inherited silently.
        SSESpecification={"Enabled": True},
        # A governance audit trail is the thing an auditor comes back for. Point-in-time recovery is the
        # difference between "we can show you what was approved" and "we used to be able to".
        Tags=[{"Key": "adcp:component", "Value": "reference-governance"}],
    )
    dynamodb.get_waiter("table_exists").wait(TableName=TABLE_NAME)
    dynamodb.update_continuous_backups(
        TableName=TABLE_NAME,
        PointInTimeRecoverySpecification={"PointInTimeRecoveryEnabled": True},
    )
    print(f"Created {TABLE_NAME} with point-in-time recovery enabled.")
    return 0


#: The TTL attribute. **One name per table**, which is the constraint that makes this delicate:
#: DynamoDB allows exactly one TTL attribute per table, so every item family that should expire has to
#: write THAT name. Two families here do, with different values -- `AGG#` (90 d) and `IDEM#` (24 h) --
#: and the durable families (`PLAN#`, `CHECK#`, `OUTCOME#`, `ESCALATION#`) write it never.
#:
#: The failure mode this constant exists to prevent: a family that writes `ttl` instead of `expires_at`
#: silently stops expiring. Nothing errors, the items accumulate, and the only symptom is a bill.
TTL_ATTRIBUTE = "expires_at"

#: Item families that must NEVER carry a TTL. A governance audit trail with an expiry is not an audit
#: trail -- the retained plan revision is what an auditor recomputes a `plan_hash` against.
DURABLE_PREFIXES = ("PLAN#", "CHECK#", "OUTCOME#", "ESCALATION#")


def enable_ttl(apply: bool) -> int:
    """Turn on DynamoDB TTL for `expires_at`. Idempotent, and refuses when the data says it is unsafe.

    Enabling TTL is not reversible in the sense that matters: once it is on, DynamoDB will delete any
    item carrying the attribute, and a durable record that carries it by mistake is gone with no
    recovery beyond point-in-time restore. So this scans for that mistake FIRST and refuses rather than
    enabling and hoping.

    The scan is a real scan, deliberately. A `Select=COUNT` with a filter would tell us how many bad
    items exist but not which ones, and "3 items would be deleted" is not something an operator can
    act on.
    """
    dynamodb = boto3.client("dynamodb")

    try:
        current = dynamodb.describe_time_to_live(TableName=TABLE_NAME)["TimeToLiveDescription"]
    except ClientError as exc:
        # Both spellings: real DynamoDB raises `ResourceNotFoundException`, moto raises
        # `ResourceNotFound` for this particular operation. Matching only the real one made the
        # missing-table case raise a traceback under test while reporting cleanly in production -- the
        # wrong way round for a guard.
        if exc.response["Error"]["Code"] in ("ResourceNotFoundException", "ResourceNotFound"):
            print(f"Table {TABLE_NAME} does not exist. Run with --apply first to create it.")
            return 1
        raise

    status = current.get("TimeToLiveStatus")
    attribute = current.get("AttributeName")
    if status in ("ENABLED", "ENABLING"):
        if attribute == TTL_ATTRIBUTE:
            print(f"TTL is already {status} on {TABLE_NAME}.{TTL_ATTRIBUTE}; nothing to do.")
            return 0
        # Reported, not "fixed". Switching the attribute would orphan every item written under the old
        # name, and choosing which family to strand is not a deploy script's call.
        print(
            f"!!! TTL is {status} on {TABLE_NAME} but for attribute {attribute!r}, not "
            f"{TTL_ATTRIBUTE!r}. Only one TTL attribute is allowed per table. Items written with "
            f"{TTL_ATTRIBUTE!r} will NEVER expire while this is the case. Resolve deliberately."
        )
        return 1

    offenders = _durable_items_with_ttl(dynamodb)
    if offenders:
        print(
            f"!!! REFUSING to enable TTL: {len(offenders)} durable item(s) carry {TTL_ATTRIBUTE!r} and "
            f"would be DELETED by DynamoDB. Durable families are {', '.join(DURABLE_PREFIXES)}."
        )
        for pk, sk in offenders[:20]:
            print(f"      {pk} / {sk}")
        if len(offenders) > 20:
            print(f"      ... and {len(offenders) - 20} more")
        return 1

    if not apply:
        print(
            f"Would enable TTL on {TABLE_NAME}.{TTL_ATTRIBUTE}. No durable item carries it, so only "
            f"AGG# (90 d) and IDEM# (24 h) items would expire. Re-run with --apply."
        )
        return 0

    dynamodb.update_time_to_live(
        TableName=TABLE_NAME,
        TimeToLiveSpecification={"Enabled": True, "AttributeName": TTL_ATTRIBUTE},
    )
    print(f"Enabled TTL on {TABLE_NAME}.{TTL_ATTRIBUTE}.")
    # Stated because it changes what a reader should expect from a test: TTL deletion is asynchronous
    # and AWS documents it as typically within a few days of expiry. It reclaims storage; it does not
    # decide validity, which is why `idempotency_backend.get` compares the timestamp itself.
    print(
        "  Deletion is asynchronous (AWS: typically within a few days of expiry). Expiry is enforced "
        "in code at read time; TTL only reclaims storage."
    )
    return 0


def _durable_items_with_ttl(dynamodb: Any) -> list[tuple[str, str]]:
    """Every `PLAN#` / `CHECK#` / `OUTCOME#` / `ESCALATION#` item carrying the TTL attribute."""
    offenders: list[tuple[str, str]] = []
    kwargs: dict[str, Any] = {
        "TableName": TABLE_NAME,
        "ProjectionExpression": "PK, SK",
        "FilterExpression": f"attribute_exists({TTL_ATTRIBUTE})",
    }
    while True:
        response = dynamodb.scan(**kwargs)
        for item in response.get("Items", []):
            pk = item.get("PK", {}).get("S", "")
            sk = item.get("SK", {}).get("S", "")
            if any(pk.startswith(prefix) or sk.startswith(prefix) for prefix in DURABLE_PREFIXES):
                offenders.append((pk, sk))
        start = response.get("LastEvaluatedKey")
        if not start:
            return offenders
        kwargs["ExclusiveStartKey"] = start


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="actually create the table")
    parser.add_argument(
        "--enable-ttl",
        action="store_true",
        help=(
            f"enable DynamoDB TTL on {TTL_ATTRIBUTE!r} (for AGG# and IDEM# items). Refuses if any "
            "durable item carries the attribute. Combine with --apply to make the change."
        ),
    )
    args = parser.parse_args()
    if args.enable_ttl:
        return enable_ttl(args.apply)
    return create(args.apply)


if __name__ == "__main__":
    sys.exit(main())
