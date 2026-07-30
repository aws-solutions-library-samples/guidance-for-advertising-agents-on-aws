#!/usr/bin/env python3
"""
Config Consistency Validator — checks global_configuration.json across all three sources.

Compares DynamoDB (GLOBAL_CONFIG/v1), the S3 data bucket, and the S3 UI bucket to detect:
- Unresolved AAMP placeholder ARNs (${AAMP_*_HTTP_RUNTIME_ARN})
- Missing AAMP agent configs (AAMPSellerAgent / AAMPBuyerAgent)
- Old, retired AAMP agent configs still present (AAMPSellerAgent / AAMPBuyerAgent — the
  previous self-contained Strands seller agent)
- Timestamp/content mismatches between sources

Usage:
    python scripts/validate_config_consistency.py \
        --stack-prefix a4a --unique-id omixaj \
        --region us-east-1 --profile agnts4ad
"""

import argparse
import json
import hashlib
import re
import sys

import boto3


def get_config_from_dynamodb(table_name, region, profile=None):
    """Load global config from DynamoDB GLOBAL_CONFIG/v1."""
    try:
        session = boto3.Session(profile_name=profile) if profile else boto3.Session()
        table = session.resource("dynamodb", region_name=region).Table(table_name)
        resp = table.get_item(Key={"pk": "GLOBAL_CONFIG", "sk": "v1"})
        item = resp.get("Item")
        if item and "content" in item:
            content = item["content"]
            updated = item.get("updated_at", "unknown")
            return json.loads(content) if isinstance(content, str) else content, updated
    except Exception as e:
        print(f"  ❌ DynamoDB error: {e}")
    return None, None


def get_config_from_s3(bucket, key, region, profile=None):
    """Load global config from S3."""
    try:
        session = boto3.Session(profile_name=profile) if profile else boto3.Session()
        s3 = session.client("s3", region_name=region)
        resp = s3.get_object(Bucket=bucket, Key=key)
        content = resp["Body"].read().decode("utf-8")
        last_modified = resp["ResponseMetadata"]["HTTPHeaders"].get("last-modified", "unknown")
        return json.loads(content), last_modified
    except Exception as e:
        print(f"  ❌ S3 error ({bucket}/{key}): {e}")
    return None, None


def check_placeholders(config, source_name):
    """Check for unresolved ${...} placeholders (AAMP runtime ARNs)."""
    issues = []
    config_str = json.dumps(config)
    placeholders = set(re.findall(r"\$\{[^}]+\}", config_str))
    if placeholders:
        issues.append(
            f"  ❌ {source_name}: {len(placeholders)} unresolved placeholder(s): {', '.join(placeholders)}"
        )
    return issues


def check_aamp_agents(config, source_name):
    """Check AAMP agent configs exist, have required fields, and old agent is retired."""
    issues = []
    agent_configs = config.get("agent_configs", {})

    expected_agents = ["AAMPSellerAgent", "AAMPBuyerAgent"]
    for agent in expected_agents:
        if agent not in agent_configs:
            issues.append(f"  ❌ {source_name}: Missing agent config: {agent}")
            continue

        cfg = agent_configs[agent]
        runtime_arn = cfg.get("runtime_arn", "")
        if not runtime_arn:
            issues.append(f"  ❌ {source_name}: {agent} has no runtime_arn set")
        elif "${" in runtime_arn:
            issues.append(f"  ❌ {source_name}: {agent}.runtime_arn has an unresolved placeholder: {runtime_arn}")
        elif not runtime_arn.startswith("arn:aws:bedrock-agentcore"):
            issues.append(f"  ⚠️  {source_name}: {agent}.runtime_arn does not look like an AgentCore runtime ARN: {runtime_arn}")

    # The old, retired self-contained Strands seller agent must be gone (Requirement 1)
    old_names = ["AAMPSellerAgent", "AAMPBuyerAgent"]
    for old in old_names:
        if old in agent_configs:
            issues.append(f"  ⚠️  {source_name}: Old (retired) agent config still present: {old}")

    return issues


def content_hash(config):
    """Get a hash of the config for comparison."""
    return hashlib.md5(json.dumps(config, sort_keys=True).encode()).hexdigest()[:12]


def main():
    parser = argparse.ArgumentParser(
        description="Validate AAMP config consistency across DynamoDB, S3 data bucket, and S3 UI bucket"
    )
    parser.add_argument("--stack-prefix", required=True)
    parser.add_argument("--unique-id", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--profile", default=None)
    args = parser.parse_args()

    table_name = f"{args.stack_prefix}-AgentConfig-{args.unique_id}"
    data_bucket = f"{args.stack_prefix}-data-{args.unique_id}"
    ui_bucket = f"{args.stack_prefix}-ui-{args.unique_id}"
    data_key = "configs/global_configuration.json"
    ui_key = "assets/global_configuration.json"

    sources = {}

    print("1️⃣  DynamoDB (GLOBAL_CONFIG/v1)...")
    ddb_config, ddb_ts = get_config_from_dynamodb(table_name, args.region, args.profile)
    if ddb_config:
        sources["DynamoDB"] = (ddb_config, ddb_ts)
        print(f"  ✅ Loaded ({len(ddb_config.get('agent_configs', {}))} agents, updated: {ddb_ts})")
    else:
        print("  ❌ Not found")

    print(f"\n2️⃣  S3 data bucket ({data_bucket})...")
    s3_config, s3_ts = get_config_from_s3(data_bucket, data_key, args.region, args.profile)
    if s3_config:
        sources["S3-data"] = (s3_config, s3_ts)
        print(f"  ✅ Loaded ({len(s3_config.get('agent_configs', {}))} agents, modified: {s3_ts})")
    else:
        print("  ❌ Not found")

    print(f"\n3️⃣  S3 UI bucket ({ui_bucket})...")
    ui_config, ui_ts = get_config_from_s3(ui_bucket, ui_key, args.region, args.profile)
    if ui_config:
        sources["S3-UI"] = (ui_config, ui_ts)
        print(f"  ✅ Loaded ({len(ui_config.get('agent_configs', {}))} agents, modified: {ui_ts})")
    else:
        print("  ❌ Not found")

    print("\n" + "=" * 60)
    print("📊 CONTENT COMPARISON")
    print("=" * 60)
    hashes = {}
    for name, (cfg, ts) in sources.items():
        h = content_hash(cfg)
        hashes[name] = h
        print(f"  {name}: hash={h}")

    unique_hashes = set(hashes.values())
    if len(unique_hashes) <= 1:
        print("  ✅ All sources are in sync")
    else:
        print(f"  ❌ MISMATCH — {len(unique_hashes)} different versions detected")

    print("\n" + "=" * 60)
    print("🔍 VALIDATION CHECKS")
    print("=" * 60)

    all_issues = []
    for name, (cfg, ts) in sources.items():
        print(f"\n--- {name} ---")
        issues = []
        issues.extend(check_placeholders(cfg, name))
        issues.extend(check_aamp_agents(cfg, name))

        if issues:
            for i in issues:
                print(i)
            all_issues.extend(issues)
        else:
            print("  ✅ All checks passed")

    print("\n" + "=" * 60)
    print("📋 SUMMARY")
    print("=" * 60)
    if not sources:
        print("  ❌ No sources could be loaded — nothing to validate")
        sys.exit(1)
    if all_issues:
        print(f"  ❌ {len(all_issues)} issue(s) found across {len(sources)} source(s)")
        sys.exit(1)
    else:
        print(f"  ✅ All {len(sources)} source(s) are consistent and valid")
        sys.exit(0)


if __name__ == "__main__":
    main()
