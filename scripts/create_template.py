#!/usr/bin/env python3
"""
Create global_configuration.template.json from the resolved global_configuration.json.

Replaces deployment-specific values with placeholders:
  - AAMP seller MCP runtime ARN  → ${AAMP_SELLER_MCP_RUNTIME_ARN}
  - AAMP seller HTTP runtime ARN → ${AAMP_SELLER_HTTP_RUNTIME_ARN}
  - AWS region in MCP awsAuth    → ${AWS_REGION}
  - Resolved KB IDs              → KB name (e.g., "Advertising")

Usage:
    python scripts/create_template.py
    python scripts/create_template.py --config-dir agentcore/deployment/agent
"""
import argparse
import json
import os
import re
import sys


def main():
    parser = argparse.ArgumentParser(description="Create template from resolved config")
    parser.add_argument("--config-dir", default="agentcore/deployment/agent",
                        help="Path to agent config directory")
    args = parser.parse_args()

    src = os.path.join(args.config_dir, "global_configuration.json")
    dst = os.path.join(args.config_dir, "global_configuration.template.json")

    if not os.path.exists(src):
        print(f"❌ Source not found: {src}")
        sys.exit(1)

    with open(src) as f:
        config = json.load(f)

    changes = 0

    # 1. Replace AAMP seller runtime ARNs in mcp_servers and external_agent_configs
    for agent_name, ac in config.get("agent_configs", {}).items():
        for mcp in ac.get("mcp_servers", []):
            url = mcp.get("url", "")
            if re.search(r"aamp.*seller.*_mcp", url, re.IGNORECASE):
                mcp["url"] = "${AAMP_SELLER_MCP_RUNTIME_ARN}"
                changes += 1
            auth = mcp.get("awsAuth", {})
            if auth.get("region") and auth["region"] != "${AWS_REGION}":
                auth["region"] = "${AWS_REGION}"
                changes += 1

        for ext in ac.get("external_agent_configs", []):
            for field in ("runtime_arn", "arn"):
                val = ext.get(field, "")
                if re.search(r"aamp.*seller.*_http", val, re.IGNORECASE):
                    ext[field] = "${AAMP_SELLER_HTTP_RUNTIME_ARN}"
                    changes += 1

    # 2. Replace resolved KB IDs with ${KB_ID_*} placeholders
    kb_map = config.get("knowledge_bases", {})
    # Build reverse map: KB ID → KB name (from the .kb-ids file convention)
    # Default KB name is "Advertising" for this project
    for agent_name, kb_id in list(kb_map.items()):
        if kb_id and len(kb_id) >= 8 and kb_id.isalnum() and kb_id.isupper():
            kb_map[agent_name] = "${KB_ID_ADVERTISING}"
            changes += 1

    for agent_name, ac in config.get("agent_configs", {}).items():
        kb = ac.get("knowledge_base", "")
        if kb and len(kb) >= 8 and kb.isalnum() and kb.isupper():
            ac["knowledge_base"] = "${KB_ID_ADVERTISING}"
            changes += 1

    with open(dst, "w") as f:
        json.dump(config, f, indent=4)
        f.write("\n")

    # Verify no real ARNs or KB IDs remain
    content = json.dumps(config)
    remaining_arns = re.findall(r"arn:aws:bedrock-agentcore:[^\"]+aamp[^\"]+", content)
    if remaining_arns:
        print(f"⚠️  Warning: {len(remaining_arns)} AAMP ARN(s) still in template")
        for a in remaining_arns:
            print(f"  {a}")

    print(f"✅ Template created: {dst} ({changes} values templatized)")


if __name__ == "__main__":
    main()
