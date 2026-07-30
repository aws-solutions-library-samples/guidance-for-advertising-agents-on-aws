#!/usr/bin/env python3
"""
Upload (or re-upload) only the visualization templates and visualization maps
to the DynamoDB AgentConfigTable.

This is a focused helper that does NOT touch agent_configs, instructions, or
agent cards. Use it when:
  - A new generic template file has been added under
    agentcore/deployment/agent/agent-visualizations-library/generic-visualization-templates/
    and you want to push it to DynamoDB without running the full config upload.
  - You just want to resync viz maps/templates after editing their JSON locally.

Schema written (matches what visualization_loader.py + agent-dynamodb.service.ts
expect):

  Agent-specific template:
    pk = "VIZ_TEMPLATE#{agent_name}"    sk = "{template_id}"
  Generic template (shared schema):
    pk = "VIZ_TEMPLATE#_GENERIC"         sk = "{template_id}"
  Visualization map:
    pk = "VIZ_MAP#{agent_name}"          sk = "v1"

Usage:
    python scripts/upload_visualization_templates_to_dynamodb.py \
        --table-name <stack-prefix>-AgentConfig-<unique-id> \
        --region us-east-1 \
        --agent-config-dir agentcore/deployment/agent

The table name can be derived from your deploy: look in
.unique-id-<prefix>-<region> or CloudFormation output AgentConfigTableName.
"""

import argparse
import os
import sys
from datetime import datetime
from typing import Tuple

import boto3
from botocore.exceptions import ClientError


def get_dynamodb_table(table_name: str, region: str, profile: str = None):
    """Build a DynamoDB table resource with optional profile."""
    if profile:
        session = boto3.Session(profile_name=profile)
        dynamodb = session.resource("dynamodb", region_name=region)
    else:
        dynamodb = boto3.resource("dynamodb", region_name=region)
    return dynamodb.Table(table_name)


def put_item(
    table,
    pk: str,
    sk: str,
    config_type: str,
    content: str,
    **extra_attrs,
) -> bool:
    """Write one item using the schema the runtime reads from."""
    item = {
        "pk": pk,
        "sk": sk,
        "config_type": config_type,
        "content": content,
        "updated_at": datetime.utcnow().isoformat(),
    }
    item.update(extra_attrs)
    try:
        table.put_item(Item=item)
        return True
    except ClientError as e:
        print(f"❌ put_item failed for {pk}/{sk}: {e}", file=sys.stderr)
        return False


def upload_visualization_maps(table, config_dir: str) -> Tuple[int, int]:
    """Upload every agent-visualization-maps/<Agent>.json record."""
    success, failed = 0, 0
    maps_dir = os.path.join(
        config_dir, "agent-visualizations-library", "agent-visualization-maps"
    )
    if not os.path.exists(maps_dir):
        print(f"⚠️  No visualization-maps directory at {maps_dir}")
        return success, failed

    for filename in sorted(os.listdir(maps_dir)):
        if not filename.endswith(".json"):
            continue
        agent_name = filename[: -len(".json")]
        filepath = os.path.join(maps_dir, filename)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            pk = f"VIZ_MAP#{agent_name}"
            if put_item(
                table, pk, "v1", "visualization_map", content,
                agent_name=agent_name,
            ):
                print(f"  ✅ {pk}")
                success += 1
            else:
                failed += 1
        except Exception as e:
            print(f"❌ Error reading {filepath}: {e}", file=sys.stderr)
            failed += 1
    return success, failed


def upload_agent_specific_templates(table, config_dir: str) -> Tuple[int, int]:
    """Upload every AgentName-templateId.json file as VIZ_TEMPLATE#AgentName."""
    success, failed = 0, 0
    viz_dir = os.path.join(config_dir, "agent-visualizations-library")
    if not os.path.exists(viz_dir):
        print(f"⚠️  No visualizations directory at {viz_dir}")
        return success, failed

    for filename in sorted(os.listdir(viz_dir)):
        filepath = os.path.join(viz_dir, filename)
        if os.path.isdir(filepath) or not filename.endswith(".json"):
            continue
        # Expected pattern: AgentName-template-id.json
        stem = filename[: -len(".json")]
        parts = stem.split("-", 1)
        if len(parts) != 2:
            continue
        agent_name, template_id = parts[0], parts[1]
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            pk = f"VIZ_TEMPLATE#{agent_name}"
            if put_item(
                table, pk, template_id, "visualization_template", content,
                agent_name=agent_name, template_id=template_id,
            ):
                success += 1
            else:
                failed += 1
        except Exception as e:
            print(f"❌ Error reading {filepath}: {e}", file=sys.stderr)
            failed += 1
    print(f"  ✅ {success} agent-specific templates, ❌ {failed}")
    return success, failed


def upload_generic_templates(table, config_dir: str) -> Tuple[int, int]:
    """Upload every generic-visualization-templates/*.json as VIZ_TEMPLATE#_GENERIC."""
    success, failed = 0, 0
    generic_dir = os.path.join(
        config_dir, "agent-visualizations-library", "generic-visualization-templates"
    )
    if not os.path.exists(generic_dir):
        print(f"⚠️  No generic-templates directory at {generic_dir}")
        return success, failed

    for filename in sorted(os.listdir(generic_dir)):
        if not filename.endswith(".json"):
            continue
        template_id = filename[: -len(".json")]
        filepath = os.path.join(generic_dir, filename)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            pk = "VIZ_TEMPLATE#_GENERIC"
            if put_item(
                table, pk, template_id, "visualization_template", content,
                agent_name="_GENERIC", template_id=template_id,
            ):
                print(f"  ✅ {template_id}")
                success += 1
            else:
                failed += 1
        except Exception as e:
            print(f"❌ Error reading {filepath}: {e}", file=sys.stderr)
            failed += 1
    return success, failed


def main():
    parser = argparse.ArgumentParser(
        description="Upload visualization maps and templates (both agent-specific "
        "and generic) to the DynamoDB AgentConfigTable."
    )
    parser.add_argument("--table-name", required=True, help="DynamoDB table name")
    parser.add_argument("--region", default="us-east-1", help="AWS region")
    parser.add_argument(
        "--agent-config-dir",
        default="agentcore/deployment/agent",
        help="Path to agent configuration directory "
        "(default: agentcore/deployment/agent)",
    )
    parser.add_argument("--profile", default=None, help="AWS profile to use")

    args = parser.parse_args()

    if not os.path.exists(args.agent_config_dir):
        print(
            f"❌ Agent config directory not found: {args.agent_config_dir}",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"📦 Uploading visualization maps + templates to DynamoDB")
    print(f"   Table:  {args.table_name}")
    print(f"   Region: {args.region}")
    print(f"   Source: {args.agent_config_dir}")
    if args.profile:
        print(f"   Profile: {args.profile}")
    print()

    table = get_dynamodb_table(args.table_name, args.region, args.profile)

    total_success, total_failed = 0, 0

    print("🗺️  Uploading visualization maps…")
    s, f = upload_visualization_maps(table, args.agent_config_dir)
    total_success += s
    total_failed += f
    print()

    print("📊 Uploading agent-specific visualization templates…")
    s, f = upload_agent_specific_templates(table, args.agent_config_dir)
    total_success += s
    total_failed += f
    print()

    print("🧩 Uploading generic visualization templates…")
    s, f = upload_generic_templates(table, args.agent_config_dir)
    total_success += s
    total_failed += f
    print()

    print("=" * 50)
    print(f"📊 Upload Summary: ✅ {total_success}  ❌ {total_failed}")
    print("=" * 50)

    if total_failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
