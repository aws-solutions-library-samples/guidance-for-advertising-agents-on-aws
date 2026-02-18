#!/bin/bash

# Script to copy the shared Dockerfile template to all agent directories
# This eliminates the need to maintain identical Dockerfiles in each agent directory

set -e

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENTCORE_DIR="$(dirname "$SCRIPT_DIR")"
DOCKERFILE_TEMPLATE="$AGENTCORE_DIR/Dockerfile.template"

# Check if template exists
if [ ! -f "$DOCKERFILE_TEMPLATE" ]; then
    echo "❌ Error: Dockerfile template not found at $DOCKERFILE_TEMPLATE"
    exit 1
fi

echo "📋 Copying shared Dockerfile template to all agent directories..."
echo "Template: $DOCKERFILE_TEMPLATE"

# Find all agent directories
AGENT_DIRS=$(find "$AGENTCORE_DIR/agents" -mindepth 1 -maxdepth 1 -type d)

if [ -z "$AGENT_DIRS" ]; then
    echo "❌ No agent directories found in $AGENTCORE_DIR/agents"
    exit 1
fi

COPIED_COUNT=0
SKIPPED_COUNT=0

for agent_dir in $AGENT_DIRS; do
    agent_name=$(basename "$agent_dir")
    dockerfile_path="$agent_dir/Dockerfile"
    
    echo "Processing agent: $agent_name"
    
    # Check if agent directory has a handler.py (to confirm it's a valid agent)
    if [ ! -f "$agent_dir/handler.py" ]; then
        echo "  ⚠️  Skipping $agent_name (no handler.py found)"
        ((SKIPPED_COUNT++))
        continue
    fi
    
    # Copy the template
    cp "$DOCKERFILE_TEMPLATE" "$dockerfile_path"
    echo "  ✅ Copied Dockerfile to $agent_name"
    ((COPIED_COUNT++))
done

echo ""
echo "📊 Summary:"
echo "  ✅ Copied to $COPIED_COUNT agents"
echo "  ⚠️  Skipped $SKIPPED_COUNT directories"
echo ""

if [ $COPIED_COUNT -gt 0 ]; then
    echo "🎉 Successfully updated Dockerfiles for all AgentCore agents!"
    echo ""
    echo "💡 Benefits:"
    echo "  - Eliminated duplicate Dockerfile maintenance"
    echo "  - Consistent Docker configuration across all agents"
    echo "  - Single source of truth for container setup"
    echo "  - Easier updates and maintenance"
    echo ""
    echo "📝 To update all Dockerfiles in the future:"
    echo "  1. Edit agentcore/Dockerfile.template"
    echo "  2. Run this script: $0"
else
    echo "⚠️  No Dockerfiles were copied. Please check the agent directories."
fi