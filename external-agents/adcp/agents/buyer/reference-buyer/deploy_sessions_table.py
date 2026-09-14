"""
Creates (idempotently) the DynamoDB table backing session_store.py — the
Buyer Agent's reasoning-session record, used by the UI's Sessions list and
viewer (R1/R3 of pick_up_external_invocation.md).

    source .venv/bin/activate
    python3 deploy_sessions_table.py

On-demand billing (no capacity to size/manage), a sparse GSI for the
"recent sessions" list (see session_store.py's module docstring for the
key layout), and TTL enabled so old sessions age out automatically — this
is a live/recent view, not an audit log, per the feature's NFRs.

Writes SESSIONS_TABLE_NAME into .env, same pattern as this project's other
deploy_*.py scripts (e.g. deploy_cognito_setup.py).
"""


from aws_region import region
import os
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

REGION = region()
#: Instance prefix (see deploy_all.resolve_prefix). Matches the `{prefix}`-tokenized SESSIONS_TABLE_NAME
#: each seller/buyer runtime is configured with, so create side and runtime side cannot drift.
INSTANCE_PREFIX = os.environ.get("INSTANCE_PREFIX", "adcp").strip() or "adcp"
TABLE_NAME = f"{INSTANCE_PREFIX}-buyer-agent-sessions"

ENV_PATH = Path(__file__).parent / ".env"
ENV_EXAMPLE_PATH = Path(__file__).parent / ".env.example"

dynamodb = boto3.client("dynamodb", region_name=REGION)


def get_or_create_table() -> str:
    try:
        dynamodb.describe_table(TableName=TABLE_NAME)
        print(f"Using existing table: {TABLE_NAME}")
        return TABLE_NAME
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceNotFoundException":
            raise

    print(f"Creating table: {TABLE_NAME}")
    dynamodb.create_table(
        TableName=TABLE_NAME,
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
            {"AttributeName": "gsi1pk", "AttributeType": "S"},
            {"AttributeName": "gsi1sk", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "gsi1",
                "KeySchema": [
                    {"AttributeName": "gsi1pk", "KeyType": "HASH"},
                    {"AttributeName": "gsi1sk", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        BillingMode="PAY_PER_REQUEST",
        SSESpecification={"Enabled": True},
    )

    print("Waiting for table to become ACTIVE...")
    waiter = dynamodb.get_waiter("table_exists")
    waiter.wait(TableName=TABLE_NAME)
    print("Table is ACTIVE.")
    return TABLE_NAME


def enable_ttl() -> None:
    try:
        current = dynamodb.describe_time_to_live(TableName=TABLE_NAME)
        if current.get("TimeToLiveDescription", {}).get("TimeToLiveStatus") in ("ENABLED", "ENABLING"):
            print("TTL already enabled.")
            return
    except ClientError:
        pass

    print("Enabling TTL on attribute 'ttl'")
    dynamodb.update_time_to_live(
        TableName=TABLE_NAME,
        TimeToLiveSpecification={"Enabled": True, "AttributeName": "ttl"},
    )


def _upsert_env_values(values: dict[str, str]) -> None:
    """Write/replace key=value lines in .env, preserving everything else.
    Same approach as deploy_cognito_setup.py::_upsert_env_values.
    """
    if not ENV_PATH.exists():
        base = ENV_EXAMPLE_PATH.read_text() if ENV_EXAMPLE_PATH.exists() else ""
        ENV_PATH.write_text(base)

    lines = ENV_PATH.read_text().splitlines()
    seen = set()
    for i, line in enumerate(lines):
        stripped = line.strip()
        for key, value in values.items():
            if stripped.startswith(f"{key}="):
                lines[i] = f"{key}={value}"
                seen.add(key)

    for key, value in values.items():
        if key not in seen:
            lines.append(f"{key}={value}")

    ENV_PATH.write_text("\n".join(lines) + "\n")


def main() -> None:
    table_name = get_or_create_table()
    enable_ttl()
    _upsert_env_values({"SESSIONS_TABLE_NAME": table_name})
    print(f"\nWrote SESSIONS_TABLE_NAME={table_name} to .env")


if __name__ == "__main__":
    main()
