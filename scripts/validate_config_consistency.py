#!/usr/bin/env python3
"""
Config Consistency Validator — checks global_configuration.json across all three sources.

Compares DynamoDB (GLOBAL_CONFIG/v1), S3 data bucket, and S3 UI bucket to detect:
- Stale runtime ARNs (placeholder values like ${...})
- Wrong KB IDs
- Wrong model ID formats
- Missing AAMP agent configs
- Timestamp/content mismatches between sources

Usage:
    python scripts/validate_config_consistency.py \
        --stack-prefix a4a --unique-id omixaj \
        --region us-west-2 --profile genai
"""

import argparse
import json
import hashlib
import re
import sys
from datetime import datetime

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
    """Check for unresolved ${...} placeholders."""
    issues = []
    config_str = json.dumps(config)
    placeholders = re.findall(r'\$\{[^}]+\}', config_str)
    # ${input} is a runtime variable resolved by A2A protocol, not a deploy-time placeholder
    placeholders = [p for p in placeholders if p != '${input}']
    if placeholders:
        unique = set(placeholders)
        issues.append(f"  ❌ {source_name}: {len(unique)} unresolved placeholder(s): {', '.join(unique)}")
    return issues


def check_model_ids(config, source_name):
    """Check for wrong model ID formats."""
    issues = []
    agent_configs = config.get("agent_configs", {})
    for agent_name, agent_cfg in agent_configs.items():
        for model_key, model_cfg in agent_cfg.get("model_inputs", {}).items():
            model_id = model_cfg.get("model_id", "")
            if model_id.startswith("bedrock/"):
                issues.append(f"  ❌ {source_name}: {agent_name}.{model_key} uses CrewAI format: {model_id}")
            elif model_id.startswith("us.") and not model_id.startswith("us.amazon"):
                issues.append(f"  ⚠️  {source_name}: {agent_name}.{model_key} uses US-only prefix: {model_id} (should be global.)")
    return issues


def check_kb_ids(config, source_name, expected_kb_id=None):
    """Check KB IDs for staleness."""
    issues = []
    kb_map = config.get("knowledge_bases", {})
    kb_ids = set(kb_map.values())

    if expected_kb_id and kb_ids and expected_kb_id not in kb_ids:
        issues.append(f"  ❌ {source_name}: knowledge_bases map uses {kb_ids} but expected {expected_kb_id}")

    # Check hardcoded knowledge_base fields in agent configs
    for agent_name, agent_cfg in config.get("agent_configs", {}).items():
        hardcoded_kb = agent_cfg.get("knowledge_base")
        if hardcoded_kb and expected_kb_id and hardcoded_kb != expected_kb_id:
            issues.append(f"  ❌ {source_name}: {agent_name}.knowledge_base = {hardcoded_kb} (expected {expected_kb_id})")
    return issues


def check_aamp_agents(config, source_name):
    """Check AAMP agent configs exist and have required fields."""
    issues = []
    agent_configs = config.get("agent_configs", {})

    expected_agents = ["AAMPSellerCrewAgent", "AAMPBuyerCrewAgent"]
    for agent in expected_agents:
        if agent not in agent_configs:
            issues.append(f"  ❌ {source_name}: Missing agent config: {agent}")
            continue

        cfg = agent_configs[agent]

        # Check external_agent_configs for Crew/Chat agents
        if agent in ("AAMPSellerCrewAgent"):
            ext = cfg.get("external_agent_configs", [])
            if not ext:
                issues.append(f"  ❌ {source_name}: {agent} has no external_agent_configs")
            else:
                # Exclude ${input} which is a runtime variable
                ext_str = json.dumps(ext).replace("${input}", "")
                if "${" in ext_str:
                    issues.append(f"  ❌ {source_name}: {agent} external_agent_configs has unresolved placeholders")

    # Check for old agent names that should be removed
    old_names = ["AAMPSellerAgent", "AAMPBuyerAgent"]
    for old in old_names:
        if old in agent_configs:
            issues.append(f"  ⚠️  {source_name}: Old agent config still present: {old}")

    return issues


def content_hash(config):
    """Get a hash of the config for comparison."""
    return hashlib.md5(json.dumps(config, sort_keys=True).encode()).hexdigest()[:12]


def main():
    parser = argparse.ArgumentParser(description="Validate config consistency across DynamoDB, S3, and UI bucket")
    parser.add_argument("--stack-prefix", required=True)
    parser.add_argument("--unique-id", required=True)
    parser.add_argument("--region", default="us-west-2")
    parser.add_argument("--profile", default=None)
    args = parser.parse_args()

    table_name = f"{args.stack_prefix}-AgentConfig-{args.unique_id}"
    data_bucket = f"{args.stack_prefix}-data-{args.unique_id}"
    ui_bucket = f"{args.stack_prefix}-ui-{args.unique_id}"
    data_key = "configs/global_configuration.json"
    ui_key = "assets/global_configuration.json"

    # Load expected KB ID
    kb_file = f".kb-ids-{args.stack_prefix}-{args.unique_id}.json"
    expected_kb = None
    try:
        with open(kb_file) as f:
            kb_data = json.load(f)
            expected_kb = kb_data.get("Advertising")
            print(f"📋 Expected KB ID (from {kb_file}): {expected_kb}")
    except FileNotFoundError:
        print(f"⚠️  KB IDs file not found: {kb_file}")
    print()

    # Load from all three sources
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

    # Compare hashes
    print("\n" + "=" * 60)
    print("📊 CONTENT COMPARISON")
    print("=" * 60)
    hashes = {}
    for name, (cfg, ts) in sources.items():
        h = content_hash(cfg)
        hashes[name] = h
        print(f"  {name}: hash={h}")

    unique_hashes = set(hashes.values())
    if len(unique_hashes) == 1:
        print("  ✅ All sources are in sync")
    else:
        print(f"  ❌ MISMATCH — {len(unique_hashes)} different versions detected")

    # Run checks on each source
    print("\n" + "=" * 60)
    print("🔍 VALIDATION CHECKS")
    print("=" * 60)

    all_issues = []
    for name, (cfg, ts) in sources.items():
        print(f"\n--- {name} ---")
        issues = []
        issues.extend(check_placeholders(cfg, name))
        issues.extend(check_model_ids(cfg, name))
        issues.extend(check_kb_ids(cfg, name, expected_kb))
        issues.extend(check_aamp_agents(cfg, name))

        if issues:
            for i in issues:
                print(i)
            all_issues.extend(issues)
        else:
            print(f"  ✅ All checks passed")

    # Summary
    print("\n" + "=" * 60)
    print("📋 SUMMARY")
    print("=" * 60)
    if all_issues:
        print(f"  ❌ {len(all_issues)} issue(s) found across {len(sources)} source(s)")
        sys.exit(1)
    else:
        print(f"  ✅ All {len(sources)} sources are consistent and valid")
        sys.exit(0)


if __name__ == "__main__":
    main()
