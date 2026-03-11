#!/usr/bin/env python3
"""
Upload tab configurations to DynamoDB AgentConfigTable.

Reads from synthetic_data/configs/tab-configurations.json and writes to DynamoDB
with pk=TAB_CONFIG, sk=v1. Skips upload if config already exists unless --force.

Usage:
    python scripts/upload_tab_configs_to_dynamodb.py \
        --table-name <table-name> \
        --region <aws-region> \
        [--profile <aws-profile>] \
        [--force]
"""

import argparse
import json
import os
import sys
from datetime import datetime

import boto3
from botocore.exceptions import ClientError

# Constants
PK = "TAB_CONFIG"
SK = "v1"
CONFIG_TYPE = "tab_config"
SOURCE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "synthetic_data", "configs", "tab-configurations.json"
)


def get_dynamodb_table(table_name: str, region: str, profile: str = None):
    """Get DynamoDB table resource."""
    if profile:
        session = boto3.Session(profile_name=profile)
        dynamodb = session.resource("dynamodb", region_name=region)
    else:
        dynamodb = boto3.resource("dynamodb", region_name=region)
    return dynamodb.Table(table_name)


def validate_tab_config(data: dict) -> bool:
    """Validate the tab configuration JSON structure."""
    if not isinstance(data, dict):
        return False
    if "tabConfigurations" not in data or not isinstance(data["tabConfigurations"], dict):
        return False
    for key, tab in data["tabConfigurations"].items():
        if not isinstance(tab, dict):
            return False
        for field in ("id", "title", "icon", "defaultAgent", "availableAgents"):
            if field not in tab:
                print(f"❌ Tab '{key}' missing required field: {field}", file=sys.stderr)
                return False
    return True


def check_existing(table) -> bool:
    """Check if TAB_CONFIG already exists in DynamoDB."""
    try:
        response = table.get_item(Key={"pk": PK, "sk": SK})
        return "Item" in response
    except ClientError as e:
        print(f"⚠️  Warning: Could not check existing config: {e}", file=sys.stderr)
        return False


def upload_tab_config(table, config_data: dict) -> bool:
    """Write tab configuration to DynamoDB."""
    try:
        table.put_item(Item={
            "pk": PK,
            "sk": SK,
            "config_type": CONFIG_TYPE,
            "content": json.dumps(config_data),
            "updated_at": datetime.utcnow().isoformat(),
            "version": 1,
            "updated_by": "deployment-seed"
        })
        return True
    except ClientError as e:
        print(f"❌ Failed to write to DynamoDB: {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="Upload tab configurations to DynamoDB")
    parser.add_argument("--table-name", required=True, help="DynamoDB table name")
    parser.add_argument("--region", required=True, help="AWS region")
    parser.add_argument("--profile", default=None, help="AWS profile name")
    parser.add_argument("--force", action="store_true", help="Overwrite existing config")
    args = parser.parse_args()

    # Read source file
    if not os.path.isfile(SOURCE_FILE):
        print(f"❌ Source file not found: {SOURCE_FILE}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(SOURCE_FILE, "r") as f:
            config_data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"❌ Failed to read/parse source file: {e}", file=sys.stderr)
        sys.exit(1)

    # Validate structure
    if not validate_tab_config(config_data):
        print("❌ Tab configuration validation failed", file=sys.stderr)
        sys.exit(1)

    tab_count = len(config_data.get("tabConfigurations", {}))
    print(f"✅ Loaded {tab_count} tab configuration(s) from source file")

    # Connect to DynamoDB
    table = get_dynamodb_table(args.table_name, args.region, args.profile)

    # Check for existing config
    if check_existing(table):
        if not args.force:
            print("ℹ️  Tab configuration already exists in DynamoDB - skipping upload")
            print("   Use --force to overwrite existing configuration")
            sys.exit(0)
        else:
            print("⚠️  Overwriting existing tab configuration (--force)")

    # Upload
    if upload_tab_config(table, config_data):
        print(f"✅ Tab configuration uploaded to DynamoDB ({args.table_name})")
        print(f"   pk={PK}, sk={SK}, tabs={tab_count}")
    else:
        print("❌ Upload failed", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
