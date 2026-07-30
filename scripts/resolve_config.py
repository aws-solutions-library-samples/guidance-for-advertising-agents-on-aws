#!/usr/bin/env python3
"""
Resolve global_configuration.template.json → global_configuration.json

Reads the deployed AAMP runtime ARNs from a cached deploy-output file and
substitutes them into the AAMP placeholder fields, producing the live
`global_configuration.json` this repo's `handler.py` and the Angular UI
actually read from (via DynamoDB upload, see upload_agent_configs_to_dynamodb.py).

Placeholders substituted:
  - ${AAMP_SELLER_HTTP_RUNTIME_ARN} <- .aamp-runtime-{prefix}-{id}.json
  - ${AAMP_BUYER_HTTP_RUNTIME_ARN}  <- .aamp-runtime-{prefix}-{id}.json

Deploy-output file format (produced by scripts/deploy-ecosystem.sh's
deploy_aamp_agents() / the IAB repos' own deploy.sh --mode http):
    {
      "agents": {
        "AAMPSellerAgent": {"runtime_arn": "arn:aws:bedrock-agentcore:...", "protocol": "HTTP"},
        "AAMPBuyerAgent":  {"runtime_arn": "arn:aws:bedrock-agentcore:...", "protocol": "HTTP"}
      }
    }

Usage:
    python scripts/resolve_config.py \
        --stack-prefix a4a --unique-id omixaj --region us-east-1

    # Dry run — show what would change without writing:
    python scripts/resolve_config.py \
        --stack-prefix a4a --unique-id omixaj --region us-east-1 --dry-run
"""
import argparse
import json
import os
import re
import sys


def load_runtime_arns(project_root, stack_prefix, unique_id):
    """Load the two AAMP runtime ARNs from the cached deploy-output file.

    Recognizes agent entries by name (AAMPSellerAgent / AAMPBuyerAgent)
    rather than guessing from a "_http" runtime-name suffix, since this repo's
    deploy_aamp_agents() writes the config-based agent name directly.
    """
    arns = {"seller": None, "buyer": None}

    runtime_file = os.path.join(
        project_root, f".aamp-runtime-{stack_prefix}-{unique_id}.json"
    )
    if not os.path.exists(runtime_file):
        print(f"⚠️  Runtime file not found: {runtime_file}")
        return arns

    with open(runtime_file) as f:
        data = json.load(f)

    agents = data.get("agents", {})
    if "AAMPSellerAgent" in agents:
        arns["seller"] = agents["AAMPSellerAgent"].get("runtime_arn")
    if "AAMPBuyerAgent" in agents:
        arns["buyer"] = agents["AAMPBuyerAgent"].get("runtime_arn")

    # Fallback: tolerate a name-substring match in case the deploy step used
    # a different key (e.g. the IAB repo's own runtime name).
    if not arns["seller"] or not arns["buyer"]:
        for name, info in agents.items():
            arn = info.get("runtime_arn")
            if not arn:
                continue
            lname = name.lower()
            if not arns["seller"] and "seller" in lname:
                arns["seller"] = arn
            if not arns["buyer"] and "buyer" in lname:
                arns["buyer"] = arn

    return arns


def main():
    parser = argparse.ArgumentParser(description="Resolve AAMP template to live config")
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

    arns = load_runtime_arns(project_root, args.stack_prefix, args.unique_id)

    changes = 0
    missing = []

    replacements = {
        "${AAMP_SELLER_HTTP_RUNTIME_ARN}": arns.get("seller"),
        "${AAMP_BUYER_HTTP_RUNTIME_ARN}": arns.get("buyer"),
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

    # Verify no placeholders remain
    config = json.loads(content)
    final_content = json.dumps(config)
    remaining = [p for p in re.findall(r"\$\{[^}]+\}", final_content)]
    if remaining:
        print(f"\n  ❌ Unresolved placeholders remain: {set(remaining)}")
        if not args.dry_run:
            print("  Aborting — will not write a config with unresolved placeholders.")
            sys.exit(1)

    if missing:
        print(f"\n  ❌ {len(missing)} placeholder(s) could not be resolved.")
        print("  Run deploy-ecosystem.sh's AAMP step first to generate the deploy-output file.")
        sys.exit(1)

    if args.dry_run:
        print(f"\n  🔍 Dry run — {changes} value(s) would be resolved. No file written.")
    else:
        with open(output, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
            f.write("\n")
        print(f"\n  ✅ Written: {output} ({changes} value(s) resolved)")


if __name__ == "__main__":
    main()
