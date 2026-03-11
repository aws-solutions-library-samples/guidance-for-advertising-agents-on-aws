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
    except Exception as e:
        print(f"⚠️  Could not list knowledge bases: {e}", file=sys.stderr)
        print("   KB IDs will not be resolved. Values will be uploaded as-is.", file=sys.stderr)
        return config
    
    # Resolve each entry in the knowledge_bases map
    resolved_count = 0
    for agent_name, kb_value in list(knowledge_bases_map.items()):
        # Skip if the value already looks like a KB ID (e.g., "ABCDEF1234")
        if len(kb_value) >= 10 and kb_value.isalnum():
            continue
        
        # Construct the expected deployed KB name
        expected_kb_name = f"{stack_prefix}-{kb_value}-{unique_id}"
        
        if expected_kb_name in kb_name_to_id:
            real_kb_id = kb_name_to_id[expected_kb_name]
            print(f"   ✅ {agent_name}: {kb_value} -> {real_kb_id} (via {expected_kb_name})")
            knowledge_bases_map[agent_name] = real_kb_id
            resolved_count += 1
        else:
            print(f"   ⚠️  {agent_name}: KB '{expected_kb_name}' not found in Bedrock, keeping value '{kb_value}'")
    
    config["knowledge_bases"] = knowledge_bases_map
    
    # Also resolve knowledge_base references inside agent_configs
    agent_configs = config.get("agent_configs", {})
    for agent_name, agent_cfg in agent_configs.items():
        kb_ref = agent_cfg.get("knowledge_base", "")
        if not kb_ref:
            continue
        
        # Skip if already looks like a resolved KB ID
        if len(kb_ref) >= 10 and kb_ref.isalnum():
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
    
    if resolved_count > 0:
        print(f"   📊 Resolved {resolved_count} knowledge base reference(s)")
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
