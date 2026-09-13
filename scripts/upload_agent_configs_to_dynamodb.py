#!/usr/bin/env python3
"""
Upload agent configurations to DynamoDB AgentConfigTable.

This script uploads agent instructions, cards, visualization maps/templates,
and global configuration to the DynamoDB AgentConfigTable for fast access
during agent runtime.

Usage:
    python scripts/upload_agent_configs_to_dynamodb.py \
        --table-name <table-name> \
        --region <aws-region> \
        --agent-config-dir <path-to-agent-config-dir> \
        --mode <merge|overwrite|prompt>

Modes:
    - merge: Add new agents from file without overwriting existing configurations
    - overwrite: Replace DynamoDB configuration entirely with file contents
    - prompt: Interactively ask user to choose merge or overwrite (default)

The script expects the following directory structure:
    agent-config-dir/
    ├── agent-instructions-library/
    │   ├── AgentName1.txt
    │   └── AgentName2.txt
    ├── agent_cards/
    │   ├── AgentName1.agent.card.json
    │   └── AgentName2.agent.card.json
    ├── agent-visualizations-library/
    │   ├── agent-visualization-maps/
    │   │   ├── AgentName1.json
    │   │   └── AgentName2.json
    │   ├── AgentName1-metrics-visualization.json
    │   └── AgentName2-allocations-visualization.json
    └── global_configuration.json
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from typing import Dict, Any, List, Tuple, Optional

import boto3
from botocore.exceptions import ClientError


def get_dynamodb_table(table_name: str, region: str, profile: str = None):
    """Get DynamoDB table resource."""
    if profile:
        session = boto3.Session(profile_name=profile)
        dynamodb = session.resource("dynamodb", region_name=region)
    else:
        dynamodb = boto3.resource("dynamodb", region_name=region)
    return dynamodb.Table(table_name)


def looks_like_kb_id(value: str) -> bool:
    """Whether a config value is shaped like a Bedrock KB id rather than a name.

    KB ids are 10-character uppercase alphanumerics (e.g. "QUHGJKGV0C"); the names
    used in config are human-readable and contain lowercase (e.g. "Advertising").
    """
    return bool(value) and len(value) >= 10 and value.isalnum() and value.isupper()


def resolve_knowledge_base_ids(config: Dict[str, Any], stack_prefix: str, 
                                unique_id: str, region: str, 
                                profile: str = None) -> Dict[str, Any]:
    """
    Resolve knowledge base name references to actual KB IDs using the Bedrock API.
    
    For each entry in the knowledge_bases map, the value is a KB name (e.g., "Advertising").
    The deployed KB has the name pattern: <stack-prefix>-<value>-<unique-id>
    (e.g., "cbi-Advertising-ibc226"). This function looks up the real KB ID for each.
    
    Also resolves knowledge_base references inside agent_configs entries.
    
    Args:
        config: The global configuration dict (will be modified in-place)
        stack_prefix: The deployment stack prefix (e.g., "cbi")
        unique_id: The deployment unique ID (e.g., "ibc226")
        region: AWS region
        profile: Optional AWS profile name
        
    Returns:
        The modified config dict with resolved KB IDs
    """
    if not stack_prefix or not unique_id:
        print("⚠️  --stack-prefix and --unique-id not provided, skipping KB ID resolution")
        return config
    
    knowledge_bases_map = config.get("knowledge_bases", {})
    if not knowledge_bases_map:
        return config
    
    print(f"🔍 Resolving knowledge base IDs using pattern: {stack_prefix}-<name>-{unique_id}")
    
    # Build a Bedrock client to list knowledge bases
    try:
        if profile:
            session = boto3.Session(profile_name=profile)
            bedrock_client = session.client("bedrock-agent", region_name=region)
        else:
            bedrock_client = boto3.client("bedrock-agent", region_name=region)
    except Exception as e:
        print(f"⚠️  Could not create Bedrock client: {e}", file=sys.stderr)
        print("   KB IDs will not be resolved. Values will be uploaded as-is.", file=sys.stderr)
        return config
    
    # List all knowledge bases and build a name -> ID lookup
    kb_name_to_id: Dict[str, str] = {}
    # Only an authoritative listing lets us judge an existing id as stale. If the
    # API call fails we must leave ids alone rather than rewrite valid ones.
    api_listing_ok = False
    try:
        next_token = None
        while True:
            kwargs = {"maxResults": 50}
            if next_token:
                kwargs["nextToken"] = next_token
            response = bedrock_client.list_knowledge_bases(**kwargs)
            
            for kb in response.get("knowledgeBaseSummaries", []):
                kb_name_to_id[kb["name"]] = kb["knowledgeBaseId"]
            
            next_token = response.get("nextToken")
            if not next_token:
                break
        api_listing_ok = True
    except Exception as e:
        print(f"⚠️  Could not list knowledge bases: {e}", file=sys.stderr)
        print("   Will try .kb-ids file fallback.", file=sys.stderr)
    
    # Fallback: also load KB IDs from the .kb-ids file saved during Step 4
    # This maps base KB names (e.g., "Advertising") directly to their IDs
    kb_ids_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               '..', f'.kb-ids-{stack_prefix}-{unique_id}.json')
    kb_base_name_to_id: Dict[str, str] = {}
    if os.path.exists(kb_ids_file):
        try:
            with open(kb_ids_file, 'r') as f:
                kb_base_name_to_id = json.load(f)
            print(f"   📂 Loaded {len(kb_base_name_to_id)} KB ID(s) from {os.path.basename(kb_ids_file)}")
        except Exception as e:
            print(f"   ⚠️  Could not read KB IDs file: {e}")
    
    if not kb_name_to_id and not kb_base_name_to_id:
        print("   ⚠️  No KB IDs available from API or file. Values will be uploaded as-is.", file=sys.stderr)
        return config

    # Every KB id that exists for this account/region, from either source.
    known_ids = set(kb_name_to_id.values()) | set(kb_base_name_to_id.values())

    # KBs belonging to THIS stack, keyed by the middle segment of
    # <stack-prefix>-<kb-name>-<unique-id>. Used to repair a stale id, where the
    # config records an id and therefore no longer carries the name to look up.
    stack_kb_pattern = re.compile(
        rf"^{re.escape(stack_prefix)}-(.+)-{re.escape(unique_id)}$"
    )
    stack_kbs: Dict[str, str] = {}
    for name, kb_id in kb_name_to_id.items():
        m = stack_kb_pattern.match(name)
        if m:
            stack_kbs[m.group(1)] = kb_id

    def repair_stale_kb_id(label: str, kb_value: str) -> Optional[str]:
        """Return this stack's KB id when kb_value is an id that does not exist.

        A config can carry a real id that belongs to a different account — copied
        from another deployment, or written by the UI against a previous stack.
        Bedrock rejects retrieval against it, and because it is shaped like a valid
        id the name-based resolution below never runs. Re-resolve from the stack's
        own KB naming instead of leaving agents pointed at a dead reference.

        Returns None when the value is fine, cannot be judged, or is ambiguous.
        """
        if not api_listing_ok:
            # No authoritative listing, so "missing" cannot be distinguished from
            # "not enumerated".
            return None
        if kb_value in known_ids:
            return None

        if len(stack_kbs) == 1:
            only_name, only_id = next(iter(stack_kbs.items()))
            print(
                f"   🔧 {label}: id '{kb_value}' does not exist in this account — "
                f"re-resolved to {only_id} ({stack_prefix}-{only_name}-{unique_id})"
            )
            return only_id

        # More than one candidate: prefer a name the .kb-ids file recorded for this
        # stack, which is written by the KB deployment step.
        preferred = [n for n in stack_kbs if n in kb_base_name_to_id]
        if len(preferred) == 1:
            name = preferred[0]
            print(
                f"   🔧 {label}: id '{kb_value}' does not exist in this account — "
                f"re-resolved to {stack_kbs[name]} ({stack_prefix}-{name}-{unique_id}, via .kb-ids)"
            )
            return stack_kbs[name]

        if not stack_kbs:
            print(
                f"   ❌ {label}: id '{kb_value}' does not exist in this account and no "
                f"KB named {stack_prefix}-<name>-{unique_id} was found. This agent "
                f"cannot retrieve until a knowledge base is deployed for the stack."
            )
        else:
            print(
                f"   ❌ {label}: id '{kb_value}' does not exist in this account and "
                f"{len(stack_kbs)} stack KBs could match ({', '.join(sorted(stack_kbs))}). "
                f"Set the KB name explicitly in the config to disambiguate."
            )
        return None

    # Resolve each entry in the knowledge_bases map
    resolved_count = 0
    repaired_count = 0
    for agent_name, kb_value in list(knowledge_bases_map.items()):
        if looks_like_kb_id(kb_value):
            repaired = repair_stale_kb_id(f"{agent_name}", kb_value)
            if repaired:
                knowledge_bases_map[agent_name] = repaired
                repaired_count += 1
            continue
        
        # Construct the expected deployed KB name
        expected_kb_name = f"{stack_prefix}-{kb_value}-{unique_id}"
        
        if expected_kb_name in kb_name_to_id:
            real_kb_id = kb_name_to_id[expected_kb_name]
            print(f"   ✅ {agent_name}: {kb_value} -> {real_kb_id} (via Bedrock API: {expected_kb_name})")
            knowledge_bases_map[agent_name] = real_kb_id
            resolved_count += 1
        elif kb_value in kb_base_name_to_id:
            # Fallback: use the .kb-ids file from Step 4
            real_kb_id = kb_base_name_to_id[kb_value]
            print(f"   ✅ {agent_name}: {kb_value} -> {real_kb_id} (via .kb-ids file)")
            knowledge_bases_map[agent_name] = real_kb_id
            resolved_count += 1
        else:
            print(f"   ⚠️  {agent_name}: KB '{expected_kb_name}' not found in Bedrock or .kb-ids file, keeping value '{kb_value}'")
    
    config["knowledge_bases"] = knowledge_bases_map
    
    # Also resolve knowledge_base references inside agent_configs
    agent_configs = config.get("agent_configs", {})
    for agent_name, agent_cfg in agent_configs.items():
        kb_ref = agent_cfg.get("knowledge_base", "")
        if not kb_ref:
            continue
        
        if looks_like_kb_id(kb_ref):
            repaired = repair_stale_kb_id(
                f"agent_configs.{agent_name}.knowledge_base", kb_ref
            )
            if repaired:
                agent_cfg["knowledge_base"] = repaired
                repaired_count += 1
            continue
        
        # If the value matches a deployed KB name pattern already (e.g., "cbi-Advertising-ibc226"),
        # look it up directly
        if kb_ref in kb_name_to_id:
            agent_cfg["knowledge_base"] = kb_name_to_id[kb_ref]
            print(f"   ✅ agent_configs.{agent_name}.knowledge_base: {kb_ref} -> {kb_name_to_id[kb_ref]}")
            resolved_count += 1
        else:
            # Try constructing the name from the value
            expected_name = f"{stack_prefix}-{kb_ref}-{unique_id}"
            if expected_name in kb_name_to_id:
                agent_cfg["knowledge_base"] = kb_name_to_id[expected_name]
                print(f"   ✅ agent_configs.{agent_name}.knowledge_base: {kb_ref} -> {kb_name_to_id[expected_name]}")
                resolved_count += 1
            elif kb_ref in kb_base_name_to_id:
                # Fallback: use the .kb-ids file from Step 4
                agent_cfg["knowledge_base"] = kb_base_name_to_id[kb_ref]
                print(f"   ✅ agent_configs.{agent_name}.knowledge_base: {kb_ref} -> {kb_base_name_to_id[kb_ref]} (via .kb-ids file)")
                resolved_count += 1
    
    if resolved_count or repaired_count:
        parts = []
        if resolved_count:
            parts.append(f"resolved {resolved_count} name(s)")
        if repaired_count:
            parts.append(f"re-resolved {repaired_count} stale id(s)")
        print(f"   📊 Knowledge base references: {', '.join(parts)}")
    else:
        print(f"   ℹ️  No knowledge base references needed resolution")
    
    return config


def put_item(table, pk: str, sk: str, config_type: str, content: str, 
             agent_name: str = None, template_id: str = None) -> bool:
    """Put a single item to DynamoDB."""
    try:
        item = {
            "pk": pk,
            "sk": sk,
            "config_type": config_type,
            "content": content,
            "updated_at": datetime.utcnow().isoformat()
        }
        if agent_name:
            item["agent_name"] = agent_name
        if template_id:
            item["template_id"] = template_id
        
        table.put_item(Item=item)
        return True
    except ClientError as e:
        print(f"❌ Error putting item {pk}/{sk}: {e}", file=sys.stderr)
        return False


def check_existing_config(table) -> Optional[Dict[str, Any]]:
    """
    Check if GLOBAL_CONFIG exists in DynamoDB.
    
    Returns:
        The existing global configuration if found, None otherwise.
    """
    try:
        response = table.get_item(
            Key={
                "pk": "GLOBAL_CONFIG",
                "sk": "v1"
            }
        )
        if "Item" in response:
            item = response["Item"]
            content = item.get("content", "{}")
            return json.loads(content)
        return None
    except ClientError as e:
        print(f"⚠️  Warning: Could not check existing config: {e}", file=sys.stderr)
        return None
    except json.JSONDecodeError as e:
        print(f"⚠️  Warning: Could not parse existing config: {e}", file=sys.stderr)
        return None


def prompt_merge_or_overwrite() -> str:
    """
    Prompt user to choose between merge and overwrite modes.
    
    Returns:
        'merge' or 'overwrite' based on user input.
    """
    print()
    print("=" * 60)
    print("⚠️  EXISTING CONFIGURATION DETECTED IN DYNAMODB")
    print("=" * 60)
    print()
    print("Choose how to handle the existing configuration:")
    print()
    print("  [M] MERGE - Add new agents without overwriting existing ones")
    print("      • New agents from file will be added")
    print("      • Existing agent configurations in DynamoDB are preserved")
    print("      • New color/knowledge base mappings are added")
    print()
    print("  [O] OVERWRITE - Replace DynamoDB config entirely with file contents")
    print("      • All existing configurations will be replaced")
    print("      • Runtime modifications will be lost")
    print()
    
    while True:
        choice = input("Enter your choice (M/O): ").strip().upper()
        if choice in ('M', 'MERGE'):
            return 'merge'
        elif choice in ('O', 'OVERWRITE'):
            return 'overwrite'
        else:
            print("Invalid choice. Please enter 'M' for merge or 'O' for overwrite.")


def merge_configurations(existing_config: Dict[str, Any], 
                         file_config: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Merge file configuration into existing DynamoDB configuration.
    
    Merge behavior:
    - New agents defined in the file are added to DynamoDB
    - Existing agent configurations in DynamoDB are NOT overwritten
    - New color mappings are added (existing colors preserved)
    - New knowledge base mappings are added (existing mappings preserved)
    
    Args:
        existing_config: The current configuration from DynamoDB
        file_config: The configuration from the local file
        
    Returns:
        Tuple of (merged_config, changes_summary)
    """
    merged = {
        "agent_configs": dict(existing_config.get("agent_configs", {})),
        "configured_colors": dict(existing_config.get("configured_colors", {})),
        "knowledge_bases": dict(existing_config.get("knowledge_bases", {}))
    }
    
    changes = {
        "agents_added": [],
        "agents_skipped": [],
        "colors_added": [],
        "colors_skipped": [],
        "knowledge_bases_added": [],
        "knowledge_bases_skipped": []
    }
    
    # Merge agent_configs - only add new agents
    file_agents = file_config.get("agent_configs", {})
    for agent_name, agent_config in file_agents.items():
        if agent_name in merged["agent_configs"]:
            changes["agents_skipped"].append(agent_name)
        else:
            merged["agent_configs"][agent_name] = agent_config
            changes["agents_added"].append(agent_name)
    
    # Merge configured_colors - only add new colors
    file_colors = file_config.get("configured_colors", {})
    for agent_name, color in file_colors.items():
        if agent_name in merged["configured_colors"]:
            changes["colors_skipped"].append(agent_name)
        else:
            merged["configured_colors"][agent_name] = color
            changes["colors_added"].append(agent_name)
    
    # Merge knowledge_bases - only add new mappings
    file_kb = file_config.get("knowledge_bases", {})
    for kb_name, kb_id in file_kb.items():
        if kb_name in merged["knowledge_bases"]:
            changes["knowledge_bases_skipped"].append(kb_name)
        else:
            merged["knowledge_bases"][kb_name] = kb_id
            changes["knowledge_bases_added"].append(kb_name)
    
    return merged, changes


def display_changes_summary(mode: str, changes: Dict[str, Any], 
                           file_config: Dict[str, Any], 
                           existing_config: Optional[Dict[str, Any]] = None,
                           config_dir: Optional[str] = None) -> bool:
    """
    Display a summary of changes that will be applied.
    
    Args:
        mode: 'merge' or 'overwrite'
        changes: Dictionary of changes (for merge mode)
        file_config: The configuration from the local file
        existing_config: The existing configuration from DynamoDB (for overwrite mode)
        
    Returns:
        True if user confirms, False otherwise
    """
    print()
    print("=" * 60)
    print(f"📋 CONFIGURATION CHANGES SUMMARY ({mode.upper()} MODE)")
    print("=" * 60)
    print()
    
    if mode == 'merge':
        # Display merge changes
        if changes["agents_added"]:
            print(f"✅ Agents to be ADDED ({len(changes['agents_added'])}):")
            for agent in sorted(changes["agents_added"]):
                print(f"   + {agent}")
            print()
        
        if changes["agents_skipped"]:
            print(f"⏭️  Agents to be SKIPPED (already exist) ({len(changes['agents_skipped'])}):")
            for agent in sorted(changes["agents_skipped"]):
                print(f"   ~ {agent}")
            print()
        
        if changes["colors_added"]:
            print(f"🎨 Colors to be ADDED ({len(changes['colors_added'])}):")
            for agent in sorted(changes["colors_added"]):
                print(f"   + {agent}")
            print()
        
        if changes["knowledge_bases_added"]:
            print(f"📚 Knowledge bases to be ADDED ({len(changes['knowledge_bases_added'])}):")
            for kb in sorted(changes["knowledge_bases_added"]):
                print(f"   + {kb}")
            print()
        
        # Check if there are any changes to apply
        total_changes = (len(changes["agents_added"]) + 
                        len(changes["colors_added"]) + 
                        len(changes["knowledge_bases_added"]))
        
        if total_changes == 0:
            print("ℹ️  No new configurations to add. All items already exist in DynamoDB.")
            print()
            return True
            
    else:  # overwrite mode
        file_agents = file_config.get("agent_configs", {})
        file_colors = file_config.get("configured_colors", {})
        file_kb = file_config.get("knowledge_bases", {})
        
        print(f"⚠️  The following will REPLACE existing DynamoDB configuration:")
        print()
        print(f"   📦 Agents: {len(file_agents)}")
        for agent in sorted(file_agents.keys()):
            print(f"      • {agent}")
        print()
        print(f"   🎨 Colors: {len(file_colors)}")
        print(f"   📚 Knowledge bases: {len(file_kb)}")
        print()
        
        if existing_config:
            existing_agents = existing_config.get("agent_configs", {})
            agents_to_lose = set(existing_agents.keys()) - set(file_agents.keys())
            if agents_to_lose:
                print(f"⚠️  WARNING: The following agents will be REMOVED:")
                for agent in sorted(agents_to_lose):
                    print(f"      ❌ {agent}")
                print()
    
    print("=" * 60)
    
    # Offer to save current DynamoDB config to local file before proceeding
    if existing_config and config_dir:
        offer_local_config_update(existing_config, file_config, config_dir)
    
    # Ask for confirmation
    while True:
        confirm = input("Do you want to proceed? (Y/N): ").strip().upper()
        if confirm in ('Y', 'YES'):
            return True
        elif confirm in ('N', 'NO'):
            print("Operation cancelled by user.")
            return False
        else:
            print("Please enter 'Y' for yes or 'N' for no.")
def save_existing_config_to_local(existing_config: Dict[str, Any], config_dir: str) -> bool:
    """
    Save the existing DynamoDB configuration to the local global_configuration.json file.

    Args:
        existing_config: The current configuration from DynamoDB
        config_dir: Path to agent configuration directory

    Returns:
        True if saved successfully, False otherwise
    """
    config_path = os.path.join(config_dir, "global_configuration.json")

    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(existing_config, f, indent=4, ensure_ascii=False)
        print(f"   ✅ Local file updated: {config_path}")
        return True
    except Exception as e:
        print(f"   ❌ Failed to update local file: {e}", file=sys.stderr)
        return False


def offer_local_config_update(existing_config: Dict[str, Any],
                               file_config: Dict[str, Any],
                               config_dir: str):
    """
    Offer to update the local global_configuration.json with the current DynamoDB config.

    Compares the existing DynamoDB config with the local file config and, if they differ,
    asks the user whether they want to save the live environment config locally before
    proceeding with the upload.

    Args:
        existing_config: The current configuration from DynamoDB
        file_config: The configuration from the local file
        config_dir: Path to agent configuration directory
    """
    # Quick diff: compare agent counts and names
    existing_agents = set(existing_config.get("agent_configs", {}).keys())
    file_agents = set(file_config.get("agent_configs", {}).keys())

    only_in_ddb = existing_agents - file_agents
    only_in_file = file_agents - existing_agents

    # Check if configs differ at all (simple JSON comparison)
    existing_json = json.dumps(existing_config, sort_keys=True)
    file_json = json.dumps(file_config, sort_keys=True)
    configs_differ = existing_json != file_json

    if not configs_differ:
        print("   ℹ️  Local file already matches DynamoDB configuration.")
        return

    print()
    print("-" * 60)
    print("💾 LOCAL FILE SYNC OPTION")
    print("-" * 60)
    print()
    print("   Your local global_configuration.json differs from the live")
    print("   DynamoDB configuration.")
    print()

    if only_in_ddb:
        print(f"   Agents in DynamoDB but NOT in local file ({len(only_in_ddb)}):")
        for agent in sorted(only_in_ddb):
            print(f"      + {agent}")
        print()

    if only_in_file:
        print(f"   Agents in local file but NOT in DynamoDB ({len(only_in_file)}):")
        for agent in sorted(only_in_file):
            print(f"      - {agent}")
        print()

    common = existing_agents & file_agents
    if common and not only_in_ddb and not only_in_file:
        print("   Agent lists match, but configuration details differ")
        print("   (e.g. instructions, colors, model inputs, mcp_servers)")
        print()

    config_path = os.path.join(config_dir, "global_configuration.json")
    print(f"   Target: {config_path}")
    print()

    while True:
        choice = input("   Save current DynamoDB config to local file? (Y/N): ").strip().upper()
        if choice in ('Y', 'YES'):
            save_existing_config_to_local(existing_config, config_dir)
            break
        elif choice in ('N', 'NO'):
            print("   ⏭️  Skipping local file update.")
            break
        else:
            print("   Please enter 'Y' for yes or 'N' for no.")

    print()
    print("-" * 60)



def backfill_template_colors(file_config: Dict[str, Any], config_dir: str) -> Dict[str, Any]:
    """Add configured_colors the template defines but file_config is missing.

    Colours are presentation-only and keyed by agent name, so this only ever adds
    keys — an existing colour is left exactly as it is. Returns the config
    unchanged if there is no template or nothing is missing.
    """
    template_path = os.path.join(config_dir, "global_configuration.template.json")
    if not os.path.exists(template_path):
        return file_config

    try:
        with open(template_path, "r", encoding="utf-8") as f:
            template_colors = json.load(f).get("configured_colors") or {}
    except Exception as e:
        print(f"   ⚠️  Could not read template colours ({e}); continuing without backfill")
        return file_config

    colors = file_config.get("configured_colors")
    if not isinstance(colors, dict):
        colors = {}
    missing = {k: v for k, v in template_colors.items() if k not in colors}
    if not missing:
        return file_config

    print(f"   🎨 Backfilling {len(missing)} agent colour(s) from the template:")
    for name, colour in sorted(missing.items()):
        print(f"      + {name} → {colour}")
    colors.update(missing)
    file_config["configured_colors"] = colors
    return file_config


def _find_aamp_entry(config: Optional[Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
    """Return the first external_agent_configs entry named `name`, on any agent."""
    for agent_cfg in ((config or {}).get("agent_configs") or {}).values():
        if not isinstance(agent_cfg, dict):
            continue
        for entry in agent_cfg.get("external_agent_configs") or []:
            if isinstance(entry, dict) and entry.get("name") == name:
                return entry
    return None


def rewire_aamp_agents(file_config: Dict[str, Any], config_dir: str,
                       existing_config: Optional[Dict[str, Any]],
                       stack_prefix: str = None, unique_id: str = None,
                       region: str = None) -> Dict[str, Any]:
    """Re-apply the AAMP external-agent wiring to the config about to be published.

    The AAMP runtimes' ARN and inbound-auth contract live only in the deployed
    state, never in global_configuration.template.json — a template cannot carry
    a per-deployment ARN or an SSM path namespaced by stack prefix and unique id.
    So any writer that rebuilds the local file from the template leaves the AAMP
    entries with `authType: "oauth"` and no credential reference, and
    build_a2a_client_tools then SKIPS those entries outright (it requires
    hasCredentials + ssmPath), so the AAMP tools silently disappear.

    Re-wiring here, at the single point every upload funnels through, makes that
    impossible regardless of which writer touched the file last — the same reason
    backfill_template_colors lives here. Previously the wiring ran only in the
    deploy's opt-in AAMP phase, so any deploy that skipped it published a config
    with the AAMP entries reverted to their template shape.

    Nothing is fabricated. The ARN and the auth contract are carried forward from
    real recorded state, in this precedence:

      1. the AAMP deploy record (.aamp-runtime-{prefix}-{unique_id}.json) — ARN only
      2. the live DynamoDB config — what the running app is using now
      3. the local file — last resort

    When no ARN can be found for an agent, its live entry is carried forward
    verbatim if one exists, and otherwise it is left alone and reported. An
    operator's `enabled` choice is preserved rather than reset to the template's.
    """
    template_path = os.path.join(config_dir, "global_configuration.template.json")
    if not os.path.exists(template_path):
        return file_config

    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from wire_aamp_agents import AAMP_AGENTS, apply_aamp_wire, load_template_defs

        defs, consumers = load_template_defs(template_path)
        if not defs or not consumers:
            return file_config

        # ARNs, per the precedence in the docstring.
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        deploy_record: Dict[str, Any] = {}
        if stack_prefix and unique_id:
            record_path = os.path.join(
                project_root, f".aamp-runtime-{stack_prefix}-{unique_id}.json"
            )
            if os.path.exists(record_path):
                try:
                    with open(record_path, "r", encoding="utf-8") as f:
                        deploy_record = json.load(f).get("agents") or {}
                except (OSError, json.JSONDecodeError) as e:
                    print(f"   ⚠️  Could not read AAMP deploy record ({e}); using recorded config")

        arns: Dict[str, str] = {}
        auth_by_agent: Dict[str, Dict[str, Any]] = {}
        live_entries: Dict[str, Dict[str, Any]] = {}

        # The top-level AAMP entries (AAMP Buyer / AAMP Seller) resolve their
        # credentials under their own agent names, and that path is recorded
        # nowhere but the config itself — the template cannot carry a path
        # namespaced by stack prefix and unique id. Carry it forward from live,
        # then local, so a Phase 7 re-run does not republish these entries with
        # hasCredentials: false and lock the operator out of the agent.
        from wire_aamp_agents import TOP_LEVEL_AGENTS

        top_level_ssm_paths: Dict[str, str] = {}
        for spec in TOP_LEVEL_AGENTS.values():
            for source in (existing_config, file_config):
                entry = ((source or {}).get("agent_configs") or {}).get(spec["id"])
                if not isinstance(entry, dict):
                    continue
                path = ((entry.get("a2a_oauth_credentials") or {}).get("ssmPath") or "").strip()
                if path:
                    top_level_ssm_paths[spec["id"]] = path
                    break

        for name in AAMP_AGENTS:
            live = _find_aamp_entry(existing_config, name)
            local = _find_aamp_entry(file_config, name)
            source = live or local or {}
            if live:
                live_entries[name] = live

            arn = ((deploy_record.get(name) or {}).get("runtime_arn") or "").strip()
            if not arn:
                arn = (source.get("arn") or "").strip()
            if arn:
                arns[name] = arn

            creds = source.get("oauthCredentials") or {}
            auth_by_agent[name] = {
                "auth_mode": (source.get("authType") or "iam").lower(),
                "pool_id": source.get("cognitoPoolId") or "",
                "client_id": source.get("cognitoClientId") or "",
                "ssm_path": creds.get("ssmPath") or "",
                "enabled": source.get("enabled"),
                # apply_aamp_wire preserves this only from the local file, so a
                # value set in the console (live-only) needs carrying forward here.
                "inventory_endpoint": (source.get("aampInventoryEndpoint") or "").strip(),
            }

        if not arns:
            print("   ⏭️  AAMP: no runtime ARN recorded anywhere — entries left as-is")
            return file_config

        # apply_aamp_wire takes one auth mode / pool / client for the pair (both
        # runtimes sit behind the same authorizer); the SSM path is per agent.
        # Prefer the buyer's recorded values, since it is the entry normally enabled.
        primary = next(
            (auth_by_agent[n] for n in ("AAMPBuyerAgent", "AAMPSellerAgent")
             if n in arns and auth_by_agent[n]["auth_mode"] != "iam"),
            auth_by_agent.get(next(iter(arns))) or {},
        )
        auth_mode = primary.get("auth_mode") or "iam"
        if auth_mode == "oauth":
            unresolved = [n for n in arns if not auth_by_agent[n]["ssm_path"]]
            if unresolved:
                print(
                    f"   ⚠️  AAMP: oauth recorded but no credential path for "
                    f"{', '.join(unresolved)} — those entries will be skipped by the "
                    "runtime until credentials are provisioned"
                )

        print(f"   🔗 Re-applying AAMP wiring ({', '.join(sorted(arns))}, auth={auth_mode}):")
        apply_aamp_wire(
            file_config,
            defs,
            consumers,
            arns,
            region=region or "us-east-1",
            auth_mode=auth_mode,
            pool_id=primary.get("pool_id") or "",
            client_id=primary.get("client_id") or "",
            ssm_paths={n: auth_by_agent[n]["ssm_path"] for n in AAMP_AGENTS},
            top_level_ssm_paths=top_level_ssm_paths,
        )

        # apply_aamp_wire rebuilds each entry from the template, which carries the
        # template's `enabled` and inventory-endpoint sentinel. Restore the
        # operator's choices where one was recorded.
        from wire_aamp_agents import (
            AAMP_INVENTORY_ENDPOINT_FIELD,
            AAMP_INVENTORY_ENDPOINT_UNSET,
        )

        for name in AAMP_AGENTS:
            recorded = auth_by_agent.get(name, {})
            entry = _find_aamp_entry(file_config, name)
            if entry is None:
                continue

            if recorded.get("enabled") is not None and entry.get("enabled") != recorded["enabled"]:
                entry["enabled"] = recorded["enabled"]
                print(f"      ↳ {name}: kept operator-set enabled={recorded['enabled']}")

            endpoint = recorded.get("inventory_endpoint") or ""
            if (
                AAMP_INVENTORY_ENDPOINT_FIELD in entry
                and endpoint
                and endpoint != AAMP_INVENTORY_ENDPOINT_UNSET
                and entry.get(AAMP_INVENTORY_ENDPOINT_FIELD) != endpoint
            ):
                entry[AAMP_INVENTORY_ENDPOINT_FIELD] = endpoint
                print(f"      ↳ {name}: kept operator-set inventory endpoint")

        # An agent with no ARN was skipped by apply_aamp_wire. If the live config
        # has a working entry for it, carry that forward rather than publishing
        # whatever the local file happened to hold.
        for name, live in live_entries.items():
            if name in arns:
                continue
            for agent_name in consumers:
                agent_cfg = (file_config.get("agent_configs") or {}).get(agent_name)
                if not isinstance(agent_cfg, dict):
                    continue
                ext = agent_cfg.setdefault("external_agent_configs", [])
                ext[:] = [e for e in ext if e.get("name") != name]
                ext.append(json.loads(json.dumps(live)))
                print(f"      ↳ {name}: no ARN — carried the live entry forward unchanged")

        return file_config

    except Exception as e:
        # Never let re-wiring break an upload; publish what we have and say so.
        print(f"   ⚠️  Could not re-apply AAMP wiring ({e}); uploading config unchanged")
        return file_config


def upload_global_config(table, config_dir: str, mode: str = 'overwrite', 
                         existing_config: Optional[Dict[str, Any]] = None,
                         stack_prefix: str = None, unique_id: str = None,
                         region: str = None, profile: str = None) -> Tuple[int, int]:
    """
    Upload global configuration with merge/overwrite support.
    
    When stack_prefix and unique_id are provided, knowledge base name references
    (e.g., "Advertising") are resolved to real KB IDs by looking up the deployed
    KB named <stack-prefix>-<value>-<unique-id> via the Bedrock API.
    
    Args:
        table: DynamoDB table resource
        config_dir: Path to agent configuration directory
        mode: 'merge' or 'overwrite'
        existing_config: Existing configuration from DynamoDB (for merge mode)
        stack_prefix: Deployment stack prefix for KB ID resolution
        unique_id: Deployment unique ID for KB ID resolution
        region: AWS region for KB ID resolution
        profile: AWS profile for KB ID resolution
        
    Returns:
        Tuple of (success_count, failed_count)
    """
    success, failed = 0, 0
    config_path = os.path.join(config_dir, "global_configuration.json")
    
    if not os.path.exists(config_path):
        print(f"⚠️  global_configuration.json not found at {config_path}")
        return success, failed
    
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            file_config = json.load(f)

        # Backfill agent colours the template defines but this file lacks.
        #
        # global_configuration.json is a build artifact that several steps rewrite
        # (template resolve, AAMP wiring, and the "save DynamoDB config to local
        # file" prompt below). Any of those can leave it without a colour that was
        # added to the template, and then this upload publishes a config where the
        # new agent has no colour and renders grey — with no error anywhere.
        # Backfilling here, at the single point every upload funnels through, makes
        # that impossible regardless of which writer touched the file last.
        # Additive only: an existing colour is never changed.
        file_config = backfill_template_colors(file_config, config_dir)

        # Re-apply the AAMP external-agent wiring (ARN + inbound auth). Neither can
        # come from the template, so any template-driven rebuild of this file drops
        # them and the runtime then skips the AAMP entries entirely. See
        # rewire_aamp_agents.
        file_config = rewire_aamp_agents(
            file_config, config_dir, existing_config,
            stack_prefix=stack_prefix, unique_id=unique_id, region=region
        )

        # Resolve knowledge base name references to real KB IDs before uploading.
        # This uses the Bedrock API to look up KBs named <stack-prefix>-<value>-<unique-id>.
        if stack_prefix and unique_id and region:
            file_config = resolve_knowledge_base_ids(
                file_config, stack_prefix, unique_id, region, profile
            )
        
        if mode == 'merge' and existing_config:
            # Merge configurations
            merged_config, changes = merge_configurations(existing_config, file_config)
            
            # Display summary and get confirmation
            if not display_changes_summary('merge', changes, file_config, existing_config, config_dir):
                return success, failed
            
            content = json.dumps(merged_config)
            print()
            print("🔄 Applying merged configuration...")
        else:
            # Overwrite mode - display summary first
            if existing_config:
                if not display_changes_summary('overwrite', {}, file_config, existing_config, config_dir):
                    return success, failed
            
            content = json.dumps(file_config)
            print()
            print("🔄 Applying configuration (overwrite mode)...")
        
        if put_item(table, "GLOBAL_CONFIG", "v1", "global_config", content):
            print(f"✅ Uploaded global_configuration.json")
            success += 1
        else:
            failed += 1
            
    except json.JSONDecodeError as e:
        print(f"❌ Error parsing {config_path}: {e}", file=sys.stderr)
        failed += 1
    except Exception as e:
        print(f"❌ Error reading {config_path}: {e}", file=sys.stderr)
        failed += 1
    
    return success, failed


def upload_agent_instructions(table, config_dir: str) -> Tuple[int, int]:
    """Upload agent instructions from agent-instructions-library."""
    success, failed = 0, 0
    instructions_dir = os.path.join(config_dir, "agent-instructions-library")
    
    if not os.path.exists(instructions_dir):
        print(f"⚠️  Instructions directory not found: {instructions_dir}")
        return success, failed
    
    for filename in os.listdir(instructions_dir):
        if filename.endswith(".txt") and not filename.startswith("_"):
            agent_name = filename.replace(".txt", "")
            filepath = os.path.join(instructions_dir, filename)
            
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                
                pk = f"INSTRUCTION#{agent_name}"
                if put_item(table, pk, "v1", "instruction", content, agent_name=agent_name):
                    print(f"✅ Uploaded instructions for {agent_name}")
                    success += 1
                else:
                    failed += 1
            except Exception as e:
                print(f"❌ Error reading {filepath}: {e}", file=sys.stderr)
                failed += 1
    
    return success, failed


def upload_agent_cards(table, config_dir: str) -> Tuple[int, int]:
    """Upload agent cards from agent_cards directory."""
    success, failed = 0, 0
    cards_dir = os.path.join(config_dir, "agent_cards")
    
    if not os.path.exists(cards_dir):
        print(f"⚠️  Agent cards directory not found: {cards_dir}")
        return success, failed
    
    for filename in os.listdir(cards_dir):
        if filename.endswith(".agent.card.json"):
            agent_name = filename.replace(".agent.card.json", "")
            filepath = os.path.join(cards_dir, filename)
            
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                
                pk = f"CARD#{agent_name}"
                if put_item(table, pk, "v1", "card", content, agent_name=agent_name):
                    print(f"✅ Uploaded card for {agent_name}")
                    success += 1
                else:
                    failed += 1
            except Exception as e:
                print(f"❌ Error reading {filepath}: {e}", file=sys.stderr)
                failed += 1
    
    return success, failed


def upload_visualization_maps(table, config_dir: str) -> Tuple[int, int]:
    """Upload visualization maps from agent-visualization-maps directory."""
    success, failed = 0, 0
    maps_dir = os.path.join(config_dir, "agent-visualizations-library", "agent-visualization-maps")
    
    if not os.path.exists(maps_dir):
        print(f"⚠️  Visualization maps directory not found: {maps_dir}")
        return success, failed
    
    for filename in os.listdir(maps_dir):
        if filename.endswith(".json"):
            agent_name = filename.replace(".json", "")
            filepath = os.path.join(maps_dir, filename)
            
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                
                pk = f"VIZ_MAP#{agent_name}"
                if put_item(table, pk, "v1", "visualization_map", content, agent_name=agent_name):
                    print(f"✅ Uploaded viz map for {agent_name}")
                    success += 1
                else:
                    failed += 1
            except Exception as e:
                print(f"❌ Error reading {filepath}: {e}", file=sys.stderr)
                failed += 1
    
    return success, failed


def upload_visualization_templates(table, config_dir: str) -> Tuple[int, int]:
    """Upload visualization templates from agent-visualizations-library directory."""
    success, failed = 0, 0
    viz_dir = os.path.join(config_dir, "agent-visualizations-library")
    
    if not os.path.exists(viz_dir):
        print(f"⚠️  Visualizations directory not found: {viz_dir}")
        return success, failed
    
    for filename in os.listdir(viz_dir):
        # Skip directories and non-JSON files
        filepath = os.path.join(viz_dir, filename)
        if os.path.isdir(filepath) or not filename.endswith(".json"):
            continue
        
        # Parse agent name and template ID from filename
        # Format: AgentName-template-id.json
        parts = filename.replace(".json", "").split("-", 1)
        if len(parts) != 2:
            continue
        
        agent_name = parts[0]
        template_id = parts[1]
        
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            
            pk = f"VIZ_TEMPLATE#{agent_name}"
            if put_item(table, pk, template_id, "visualization_template", content, 
                       agent_name=agent_name, template_id=template_id):
                print(f"✅ Uploaded template {template_id} for {agent_name}")
                success += 1
            else:
                failed += 1
        except Exception as e:
            print(f"❌ Error reading {filepath}: {e}", file=sys.stderr)
            failed += 1
    
    # Also upload generic templates
    generic_dir = os.path.join(viz_dir, "generic-visualization-templates")
    if os.path.exists(generic_dir):
        for filename in os.listdir(generic_dir):
            if not filename.endswith(".json"):
                continue
            
            template_id = filename.replace(".json", "")
            filepath = os.path.join(generic_dir, filename)
            
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                
                # Store generic templates with a special agent name
                pk = "VIZ_TEMPLATE#_GENERIC"
                if put_item(table, pk, template_id, "visualization_template", content,
                           agent_name="_GENERIC", template_id=template_id):
                    print(f"✅ Uploaded generic template {template_id}")
                    success += 1
                else:
                    failed += 1
            except Exception as e:
                print(f"❌ Error reading {filepath}: {e}", file=sys.stderr)
                failed += 1
    
    return success, failed


def main():
    parser = argparse.ArgumentParser(
        description="Upload agent configurations to DynamoDB AgentConfigTable"
    )
    parser.add_argument(
        "--table-name",
        required=True,
        help="DynamoDB table name"
    )
    parser.add_argument(
        "--region",
        default="us-east-1",
        help="AWS region (default: us-east-1)"
    )
    parser.add_argument(
        "--agent-config-dir",
        required=True,
        help="Path to agent configuration directory"
    )
    parser.add_argument(
        "--mode",
        choices=["merge", "overwrite", "prompt"],
        default="prompt",
        help="How to handle existing configuration: merge (add new only), overwrite (replace all), prompt (ask user). Default: prompt"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be uploaded without actually uploading"
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="AWS profile to use for credentials"
    )
    parser.add_argument(
        "--stack-prefix",
        default=None,
        help="Deployment stack prefix for KB ID resolution (e.g., 'cbi'). "
             "When provided with --unique-id, knowledge base name references "
             "are resolved to real KB IDs via the Bedrock API."
    )
    parser.add_argument(
        "--unique-id",
        default=None,
        help="Deployment unique ID for KB ID resolution (e.g., 'ibc226'). "
             "Used with --stack-prefix to look up KBs named "
             "<stack-prefix>-<kb-name>-<unique-id>."
    )
    
    args = parser.parse_args()
    
    if not os.path.exists(args.agent_config_dir):
        print(f"❌ Agent config directory not found: {args.agent_config_dir}", file=sys.stderr)
        sys.exit(1)
    
    print(f"📦 Uploading agent configurations to DynamoDB")
    print(f"   Table: {args.table_name}")
    print(f"   Region: {args.region}")
    print(f"   Config dir: {args.agent_config_dir}")
    print(f"   Mode: {args.mode}")
    if args.profile:
        print(f"   Profile: {args.profile}")
    print()
    
    if args.dry_run:
        print("🔍 DRY RUN - No changes will be made")
        print()
    
    table = get_dynamodb_table(args.table_name, args.region, args.profile)
    
    # Check for existing configuration
    print("🔍 Checking for existing configuration in DynamoDB...")
    existing_config = check_existing_config(table)
    
    # Determine the mode to use
    mode = args.mode
    if existing_config:
        print(f"✅ Found existing GLOBAL_CONFIG in DynamoDB")
        if mode == 'prompt':
            mode = prompt_merge_or_overwrite()
        print(f"   Using mode: {mode}")
    else:
        print("ℹ️  No existing configuration found. Will upload fresh configuration.")
        mode = 'overwrite'  # No existing config, so just upload
    
    print()
    
    total_success = 0
    total_failed = 0
    
    # Upload global config (with merge/overwrite handling and KB ID resolution)
    print("📄 Uploading global configuration...")
    if args.stack_prefix and args.unique_id:
        print(f"   KB ID resolution enabled: {args.stack_prefix}-<name>-{args.unique_id}")
    s, f = upload_global_config(
        table, args.agent_config_dir, mode, existing_config,
        stack_prefix=args.stack_prefix, unique_id=args.unique_id,
        region=args.region, profile=args.profile
    )
    total_success += s
    total_failed += f
    
    # If global config upload was cancelled or failed, exit
    if f > 0 or (s == 0 and existing_config):
        if s == 0 and f == 0:
            # User cancelled
            print()
            print("=" * 50)
            print("⚠️  Upload cancelled by user")
            print("=" * 50)
            sys.exit(0)
    
    print()
    
    # Upload agent instructions
    print("📝 Uploading agent instructions...")
    s, f = upload_agent_instructions(table, args.agent_config_dir)
    total_success += s
    total_failed += f
    print()
    
    # Upload agent cards
    print("🎴 Uploading agent cards...")
    s, f = upload_agent_cards(table, args.agent_config_dir)
    total_success += s
    total_failed += f
    print()
    
    # Upload visualization maps
    print("🗺️  Uploading visualization maps...")
    s, f = upload_visualization_maps(table, args.agent_config_dir)
    total_success += s
    total_failed += f
    print()
    
    # Upload visualization templates
    print("📊 Uploading visualization templates...")
    s, f = upload_visualization_templates(table, args.agent_config_dir)
    total_success += s
    total_failed += f
    print()
    
    # Summary
    print("=" * 50)
    print(f"📊 Upload Summary")
    print(f"   ✅ Successful: {total_success}")
    print(f"   ❌ Failed: {total_failed}")
    print("=" * 50)
    
    if total_failed > 0:
        sys.exit(1)
    
    print("✅ All configurations uploaded successfully!")


if __name__ == "__main__":
    main()
