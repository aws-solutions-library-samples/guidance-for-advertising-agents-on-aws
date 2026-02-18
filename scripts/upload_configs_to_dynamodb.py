#!/usr/bin/env python3
"""
Upload agent configurations to DynamoDB for faster agent creation.

Uses the AgentConfig table from infrastructure-services.yml with schema:
- pk (HASH): "{config_type}#{config_id}"
- sk (RANGE): "v0" (version)
- config_type: For GSI queries
- config_data: The actual content

This script uploads:
- global_configuration.json -> pk="global_config#global_configuration"
- agent-instructions-library/*.txt -> pk="instruction#{agent_name}"
- agent_cards/*.agent.card.json -> pk="agent_card#{agent_name}"
- agent-visualizations-library/agent-visualization-maps/*.json -> pk="visualization_map#{agent_name}"
- agent-visualizations-library/*-*-visualization.json -> pk="visualization_template#{filename}"
- agent-visualizations-library/generic-visualization-templates/*.json -> pk="generic_template#{template_id}"

Usage:
    python scripts/upload_configs_to_dynamodb.py --stack-prefix sim --unique-id abc123 --region us-east-1
"""

import argparse
import boto3
import json
import os
import sys
from datetime import datetime
from pathlib import Path


def get_dynamodb_table(stack_prefix: str, unique_id: str, region: str):
    """Get DynamoDB table resource."""
    table_name = f"{stack_prefix}-AgentConfig-{unique_id}"
    dynamodb = boto3.resource("dynamodb", region_name=region)
    return dynamodb.Table(table_name)


def build_pk(config_type: str, config_id: str) -> str:
    """Build partition key for DynamoDB item."""
    return f"{config_type}#{config_id}"


def put_config_item(table, config_type: str, config_id: str, content: str) -> bool:
    """Put a configuration item into DynamoDB."""
    try:
        table.put_item(Item={
            "pk": build_pk(config_type, config_id),
            "sk": "v0",
            "config_type": config_type,
            "config_id": config_id,
            "config_data": content,
            "updated_at": datetime.utcnow().isoformat()
        })
        return True
    except Exception as e:
        print(f"❌ Failed to put {config_type}/{config_id}: {e}")
        return False


def upload_global_config(table, config_path: str) -> int:
    """Upload global_configuration.json to DynamoDB."""
    if not os.path.exists(config_path):
        print(f"⚠️  Global config not found: {config_path}")
        return 0
    
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        if put_config_item(table, "global_config", "global_configuration", content):
            print(f"✅ Uploaded global_configuration.json")
            return 1
        return 0
    except Exception as e:
        print(f"❌ Failed to upload global_configuration.json: {e}")
        return 0


def upload_instructions(table, instructions_dir: str) -> int:
    """Upload agent instructions to DynamoDB."""
    if not os.path.exists(instructions_dir):
        print(f"⚠️  Instructions directory not found: {instructions_dir}")
        return 0
    
    count = 0
    for filename in os.listdir(instructions_dir):
        if not filename.endswith(".txt"):
            continue
        if filename.startswith("_"):
            continue  # Skip helper files like _confirm_before_skipping_collaboration.txt
        
        agent_name = filename.replace(".txt", "")
        filepath = os.path.join(instructions_dir, filename)
        
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            
            if put_config_item(table, "instruction", agent_name, content):
                count += 1
        except Exception as e:
            print(f"❌ Failed to upload instruction {agent_name}: {e}")
    
    print(f"✅ Uploaded {count} agent instructions")
    return count


def upload_agent_cards(table, cards_dir: str) -> int:
    """Upload agent cards to DynamoDB."""
    if not os.path.exists(cards_dir):
        print(f"⚠️  Agent cards directory not found: {cards_dir}")
        return 0
    
    count = 0
    for filename in os.listdir(cards_dir):
        if not filename.endswith(".agent.card.json"):
            continue
        
        agent_name = filename.replace(".agent.card.json", "")
        filepath = os.path.join(cards_dir, filename)
        
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            
            if put_config_item(table, "agent_card", agent_name, content):
                count += 1
        except Exception as e:
            print(f"❌ Failed to upload agent card {agent_name}: {e}")
    
    print(f"✅ Uploaded {count} agent cards")
    return count


def upload_visualization_maps(table, viz_dir: str) -> int:
    """Upload visualization maps to DynamoDB."""
    maps_dir = os.path.join(viz_dir, "agent-visualization-maps")
    if not os.path.exists(maps_dir):
        print(f"⚠️  Visualization maps directory not found: {maps_dir}")
        return 0
    
    count = 0
    for filename in os.listdir(maps_dir):
        if not filename.endswith(".json"):
            continue
        
        agent_name = filename.replace(".json", "")
        filepath = os.path.join(maps_dir, filename)
        
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            
            if put_config_item(table, "visualization_map", agent_name, content):
                count += 1
        except Exception as e:
            print(f"❌ Failed to upload visualization map {agent_name}: {e}")
    
    print(f"✅ Uploaded {count} visualization maps")
    return count


def upload_visualization_templates(table, viz_dir: str) -> int:
    """Upload visualization templates to DynamoDB."""
    if not os.path.exists(viz_dir):
        print(f"⚠️  Visualizations directory not found: {viz_dir}")
        return 0
    
    count = 0
    for filename in os.listdir(viz_dir):
        # Skip directories and non-JSON files
        filepath = os.path.join(viz_dir, filename)
        if os.path.isdir(filepath):
            continue
        if not filename.endswith(".json"):
            continue
        
        # Template files follow pattern: {AgentName}-{template-id}.json
        # e.g., AdLoadOptimizationAgent-metrics-visualization.json
        template_id = filename.replace(".json", "")
        
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            
            if put_config_item(table, "visualization_template", template_id, content):
                count += 1
        except Exception as e:
            print(f"❌ Failed to upload visualization template {template_id}: {e}")
    
    print(f"✅ Uploaded {count} visualization templates")
    return count


def upload_generic_templates(table, viz_dir: str) -> int:
    """Upload generic visualization templates to DynamoDB."""
    generic_dir = os.path.join(viz_dir, "generic-visualization-templates")
    if not os.path.exists(generic_dir):
        print(f"⚠️  Generic templates directory not found: {generic_dir}")
        return 0
    
    count = 0
    for filename in os.listdir(generic_dir):
        if not filename.endswith(".json"):
            continue
        
        template_id = filename.replace(".json", "")
        filepath = os.path.join(generic_dir, filename)
        
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            
            if put_config_item(table, "generic_template", template_id, content):
                count += 1
        except Exception as e:
            print(f"❌ Failed to upload generic template {template_id}: {e}")
    
    print(f"✅ Uploaded {count} generic templates")
    return count


def main():
    parser = argparse.ArgumentParser(description="Upload agent configurations to DynamoDB")
    parser.add_argument("--stack-prefix", required=True, help="Stack prefix (e.g., sim)")
    parser.add_argument("--unique-id", required=True, help="Unique ID for the deployment")
    parser.add_argument("--region", default="us-east-1", help="AWS region")
    parser.add_argument("--profile", help="AWS profile to use")
    parser.add_argument("--base-dir", help="Base directory for agent configs (default: agentcore/deployment/agent)")
    
    args = parser.parse_args()
    
    # Set AWS profile if provided
    if args.profile:
        boto3.setup_default_session(profile_name=args.profile)
    
    # Determine base directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    
    if args.base_dir:
        base_dir = args.base_dir
    else:
        base_dir = os.path.join(project_root, "agentcore", "deployment", "agent")
    
    print(f"📦 Uploading agent configurations to DynamoDB")
    print(f"   Table: {args.stack_prefix}-AgentConfig-{args.unique_id}")
    print(f"   Region: {args.region}")
    print(f"   Base directory: {base_dir}")
    print()
    
    # Get DynamoDB table
    try:
        table = get_dynamodb_table(args.stack_prefix, args.unique_id, args.region)
        # Test table access
        table.table_status
    except Exception as e:
        print(f"❌ Failed to access DynamoDB table: {e}")
        print("   Make sure the table exists and you have proper permissions.")
        sys.exit(1)
    
    # Upload all configurations
    total = 0
    
    # Global configuration
    global_config_path = os.path.join(base_dir, "global_configuration.json")
    total += upload_global_config(table, global_config_path)
    
    # Agent instructions
    instructions_dir = os.path.join(base_dir, "agent-instructions-library")
    total += upload_instructions(table, instructions_dir)
    
    # Agent cards
    cards_dir = os.path.join(base_dir, "agent_cards")
    total += upload_agent_cards(table, cards_dir)
    
    # Visualization maps
    viz_dir = os.path.join(base_dir, "agent-visualizations-library")
    total += upload_visualization_maps(table, viz_dir)
    
    # Visualization templates
    total += upload_visualization_templates(table, viz_dir)
    
    # Generic templates
    total += upload_generic_templates(table, viz_dir)
    
    print()
    print(f"🎉 Upload complete! Total items uploaded: {total}")


if __name__ == "__main__":
    main()
