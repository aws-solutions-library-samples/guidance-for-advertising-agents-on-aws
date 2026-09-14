"""
Creates (idempotently) the DynamoDB table backing state.py — the reference
seller's media-buy/idempotency/account records (R1/R2/R3/R6 of
.kiro/specs/seller-agent-adcp-compliance/design.md).

    uv run python deploy_state_table.py

On-demand billing (no capacity to size/manage, matching this project's
other tables) and TTL enabled on a `ttl` attribute so idempotency records
(24h replay window, per design.md) age out automatically — this table is a
single-table design keyed by `pk`/`sk` holding four item types:
  - MEDIABUY#<media_buy_id> / META            — media buy record
  - MEDIABUY#<media_buy_id> / FEEDBACK#<ts>   — performance feedback
  - IDEMPOTENCY#<idempotency_key> / META      — idempotency record (has ttl)
  - ACCOUNT#<brand>#<operator> / META         — buyer-declared account

Same pattern as agents/buyer/reference-buyer/deploy_sessions_table.py,
adapted for this project's table (no GSI needed here — see design.md's "get_media_buys"
section on why a Scan is acceptable at this agent's fixture-scale data
volume).

Writes STATE_TABLE_NAME into .env, same convention as this project's
other deploy_*.py scripts.
"""


from aws_region import region
from pathlib import Path

import os

import boto3
from botocore.exceptions import ClientError

REGION = region()
#: Instance prefix (see deploy_all.resolve_prefix). Matches the `{prefix}`-tokenized STATE_TABLE_NAME
#: render_agentcore_json.py injects into this runtime, so create side and runtime side cannot drift.
INSTANCE_PREFIX = os.environ.get("INSTANCE_PREFIX", "adcp").strip() or "adcp"
TABLE_NAME = f"{INSTANCE_PREFIX}-reference-seller-state"

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
        ],
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
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
    Same approach as
    agents/buyer/reference-buyer/deploy_sessions_table.py::_upsert_env_values.
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
    _upsert_env_values({"STATE_TABLE_NAME": table_name})
    print(f"\nWrote STATE_TABLE_NAME={table_name} to .env")


if __name__ == "__main__":
    main()
