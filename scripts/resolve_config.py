#!/usr/bin/env python3
"""
Resolve global_configuration.template.json → global_configuration.json

Reads deployment-specific values from cached files and replaces placeholders:
  - ${AAMP_SELLER_MCP_RUNTIME_ARN}  ← .aamp-runtime-{prefix}-{id}.json
  - ${AAMP_SELLER_HTTP_RUNTIME_ARN} ← .aamp-runtime-{prefix}-{id}.json
  - ${AWS_REGION}                   ← --region argument
  - KB names ("Advertising")        ← .kb-ids-{prefix}-{id}.json

Usage:
    python scripts/resolve_config.py \
        --stack-prefix a4a --unique-id omixaj --region us-west-2

    # Dry run — show what would change without writing:
    python scripts/resolve_config.py \
        --stack-prefix a4a --unique-id omixaj --region us-west-2 --dry-run
"""
import argparse
import json
import os
import re
import sys


def load_runtime_arns(project_root, stack_prefix, unique_id):
    """Load AAMP seller runtime ARNs from the cached deploy file.
    
    Supports two file formats:
      - .aamp-runtime-*.json: { "agents": { "AgentName": { "runtime_arn": "...", "protocol": "MCP|HTTP" } } }
      - .agentcore-agents-*.json: { "deployed_agents": [ { "name": "...", "runtime_arn": "..." } ] }
    """
    arns = {"mcp": None, "http": None, "buyer_http": None}

    # Primary: .aamp-runtime file (has protocol info)
    runtime_file = os.path.join(project_root, f".aamp-runtime-{stack_prefix}-{unique_id}.json")
    if os.path.exists(runtime_file):
        with open(runtime_file) as f:
            data = json.load(f)
        for agent_name, agent_info in data.get("agents", {}).items():
            arn = agent_info.get("runtime_arn", "")
            protocol = agent_info.get("protocol", "")
            runtime_name = agent_info.get("runtime_name", agent_info.get("agent_name", ""))
            if protocol == "MCP" or "_mcp" in runtime_name:
                arns["mcp"] = arns["mcp"] or arn
            elif "buyer" in runtime_name.lower() and ("_http" in runtime_name or protocol == "HTTP"):
                arns["buyer_http"] = arns["buyer_http"] or arn
            elif protocol == "HTTP" or "_http" in runtime_name:
                arns["http"] = arns["http"] or arn
        # Also check deployed_agents array format
        for agent in data.get("deployed_agents", []):
            name = agent.get("runtime_name", agent.get("name", ""))
            arn = agent.get("runtime_arn", "")
            if "_mcp" in name:
                arns["mcp"] = arns["mcp"] or arn
            elif "buyer" in name.lower() and "_http" in name:
                arns["buyer_http"] = arns["buyer_http"] or arn
            elif "_http" in name:
                arns["http"] = arns["http"] or arn
    else:
        print(f"⚠️  Runtime file not found: {runtime_file}")

    # Fallback: .agentcore-agents file
    agents_file = os.path.join(project_root, f".agentcore-agents-{stack_prefix}-{unique_id}.json")
    if os.path.exists(agents_file):
        with open(agents_file) as f:
            agents_data = json.load(f)
        for agent in agents_data.get("deployed_agents", []):
            name = agent.get("runtime_name", agent.get("name", ""))
            arn = agent.get("runtime_arn", "")
            if "aamp" in name.lower() and "seller" in name.lower():
                if "_mcp" in name:
                    arns["mcp"] = arns["mcp"] or arn
                elif "_http" in name:
                    arns["http"] = arns["http"] or arn
            elif "aamp" in name.lower() and "buyer" in name.lower() and "_http" in name:
                arns["buyer_http"] = arns["buyer_http"] or arn

    return arns


def load_kb_ids(project_root, stack_prefix, unique_id):
    """Load knowledge base IDs from the cached deploy file."""
    kb_file = os.path.join(project_root, f".kb-ids-{stack_prefix}-{unique_id}.json")
    if not os.path.exists(kb_file):
        print(f"⚠️  KB IDs file not found: {kb_file}")
        return {}

    with open(kb_file) as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(description="Resolve template to live config")
    parser.add_argument("--stack-prefix", required=True)
    parser.add_argument("--unique-id", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--config-dir", default="agentcore/deployment/agent")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing")
    args = parser.parse_args()

    project_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    template = os.path.join(args.config_dir, "global_configuration.template.json")
    output = os.path.join(args.config_dir, "global_configuration.json")

    if not os.path.exists(template):
        print(f"❌ Template not found: {template}")
        sys.exit(1)

    with open(template) as f:
        content = f.read()

    # Load deployment-specific values
    arns = load_runtime_arns(project_root, args.stack_prefix, args.unique_id)
    kb_ids = load_kb_ids(project_root, args.stack_prefix, args.unique_id)

    changes = 0
    missing = []

    # 1. Replace placeholder strings
    replacements = {
        "${AAMP_SELLER_MCP_RUNTIME_ARN}": arns.get("mcp"),
        "${AAMP_SELLER_HTTP_RUNTIME_ARN}": arns.get("http"),
        "${AAMP_BUYER_HTTP_RUNTIME_ARN}": arns.get("buyer_http"),
        "${AWS_REGION}": args.region,
    }

    for placeholder, value in replacements.items():
        count = content.count(placeholder)
        if count > 0:
            if value:
                content = content.replace(placeholder, value)
                changes += count
                print(f"  ✅ {placeholder} → {value} ({count}x)")
            else:
                missing.append(placeholder)
                print(f"  ❌ {placeholder} — no value available ({count} occurrence(s))")

    # 2. Resolve ${KB_ID_*} placeholders using .kb-ids file
    config = json.loads(content)

    for kb_name, kb_id in kb_ids.items():
        placeholder = "${KB_ID_" + kb_name.upper() + "}"
        count = content.count(placeholder)
        if count > 0:
            content = content.replace(placeholder, kb_id)
            changes += count
            print(f"  ✅ {placeholder} → {kb_id} ({count}x)")

    config = json.loads(content)

    # 3. Verify no placeholders remain (excluding ${input})
    final_content = json.dumps(config)
    remaining = [p for p in re.findall(r'\$\{[^}]+\}', final_content) if p != '${input}']
    if remaining:
        print(f"\n  ❌ Unresolved placeholders remain: {set(remaining)}")
        if not args.dry_run:
            print("  Aborting — will not write a config with unresolved placeholders.")
            sys.exit(1)

    if missing:
        print(f"\n  ❌ {len(missing)} placeholder(s) could not be resolved.")
        print(f"  Run deploy-ecosystem.sh first to generate the cached deploy files.")
        sys.exit(1)

    # Write output
    if args.dry_run:
        print(f"\n  🔍 Dry run — {changes} values would be resolved. No file written.")
    else:
        with open(output, "w") as f:
            json.dump(config, f, indent=4)
            f.write("\n")
        print(f"\n  ✅ Written: {output} ({changes} values resolved)")


if __name__ == "__main__":
    main()
