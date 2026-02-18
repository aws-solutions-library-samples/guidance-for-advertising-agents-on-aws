#!/bin/bash

# Store AgentCore Runtime ARNs and Bearer Tokens in SSM Parameter Store
# This script stores runtime configuration in a centralized SSM parameter
# for easy retrieval by the UI and other components

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_status() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

# Default values
REGION="us-east-1"
STACK_PREFIX=""
UNIQUE_ID=""
PROFILE=""

# Parse named parameters
while [[ $# -gt 0 ]]; do
  case $1 in
    --region)
      REGION="$2"
      shift 2
      ;;
    --stack-prefix)
      STACK_PREFIX="$2"
      shift 2
      ;;
    --unique-id)
      UNIQUE_ID="$2"
      shift 2
      ;;
    --profile)
      PROFILE="$2"
      shift 2
      ;;
    --help)
      echo "Usage: $0 [OPTIONS]"
      echo ""
      echo "Options:"
      echo "  --region REGION            AWS region (default: us-east-1)"
      echo "  --stack-prefix PREFIX      Stack prefix for resource naming (required)"
      echo "  --unique-id ID             Unique identifier for resource naming (required)"
      echo "  --profile PROFILE          AWS CLI profile to use (optional)"
      echo "  --help                     Show this help message"
      echo ""
      echo "Example:"
      echo "  $0 --stack-prefix prod --unique-id abc123 --region us-east-1 --profile rtbag"
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      echo "Use --help for usage information"
      exit 1
      ;;
  esac
done

# Validate required parameters
if [ -z "$STACK_PREFIX" ]; then
    print_error "STACK_PREFIX is required"
    echo "Use --help for usage information"
    exit 1
fi

if [ -z "$UNIQUE_ID" ]; then
    print_error "UNIQUE_ID is required"
    echo "Use --help for usage information"
    exit 1
fi

# Build profile flag if provided
PROFILE_FLAG=""
if [ -n "$PROFILE" ]; then
  PROFILE_FLAG="--profile $PROFILE"
fi

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Define tracking file path
TRACKING_FILE="${PROJECT_ROOT}/.agentcore-agents-${STACK_PREFIX}-${UNIQUE_ID}.json"
CONFIG_FILE="${PROJECT_ROOT}/agentcore/deployment/agent/global_configuration.json"
print_status "Storing AgentCore values in SSM Parameter Store"
print_status "  Stack Prefix: $STACK_PREFIX"
print_status "  Unique ID: $UNIQUE_ID"
print_status "  Region: $REGION"
print_status "  Tracking File: $TRACKING_FILE"

# Check if tracking file exists
if [ ! -f "$TRACKING_FILE" ]; then
    print_error "Tracking file not found: $TRACKING_FILE"
    print_error "No AgentCore agents have been deployed yet"
    exit 1
fi

# First, merge tracking file with existing SSM data to preserve all agents
print_status "Merging tracking file with existing SSM data..."

# Try to get existing SSM data
PARAMETER_NAME="/${STACK_PREFIX}/agentcore_values/${UNIQUE_ID}"

EXISTING_SSM_DATA=$(aws ssm get-parameter \
    --name "$PARAMETER_NAME" \
    --with-decryption \
    --region "$REGION" \
    $PROFILE_FLAG \
    --query 'Parameter.Value' \
    --output text 2>/dev/null || echo "")

# Export variables for Python script
export TRACKING_FILE
export EXISTING_SSM_DATA
export CONFIG_FILE

# Merge tracking file with SSM data
python3 << 'MERGE_EOF'
import json
import sys
import os

tracking_file = os.environ.get('TRACKING_FILE')
existing_ssm = os.environ.get('EXISTING_SSM_DATA', '')

try:
    # Validate tracking file path
    if not tracking_file:
        print('ERROR merging: TRACKING_FILE environment variable not set', file=sys.stderr)
        sys.exit(1)
    
    # Load tracking file
    with open(tracking_file, 'r') as f:
        tracking_data = json.load(f)
    
    # Start with agents from tracking file
    all_agents = {agent['name']: agent for agent in tracking_data.get('deployed_agents', []) if isinstance(agent, dict)}
    
    # Merge with existing SSM data if it exists
    if existing_ssm and existing_ssm != 'null':
        ssm_data = json.loads(existing_ssm)
        for ssm_agent in ssm_data.get('agents', []):
            agent_name = ssm_agent.get('name')
            # Only add if not already in tracking file (tracking file takes precedence)
            if agent_name and agent_name not in all_agents:
                # Convert SSM format to tracking format
                all_agents[agent_name] = {
                    'name': agent_name,
                    'runtime_arn': ssm_agent.get('runtime_arn', ''),
                    'runtime_id': ssm_agent.get('runtime_arn', '').split('/')[-1] if ssm_agent.get('runtime_arn') else '',
                    'container_uri': '',
                    'deployment_time': tracking_data.get('deployment_time', ''),
                    'memory_config': {},
                    'external_tools': [],
                    'runtime_name': agent_name.replace('-', '_'),
                }
                # Preserve A2A config if present
                if ssm_agent.get('protocol') == 'A2A':
                    all_agents[agent_name]['protocol'] = 'A2A'
                    all_agents[agent_name]['bearer_token'] = ssm_agent.get('bearer_token', '')
                    all_agents[agent_name]['pool_id'] = ssm_agent.get('pool_id', '')
                    all_agents[agent_name]['client_id'] = ssm_agent.get('client_id', '')
                    all_agents[agent_name]['discovery_url'] = ssm_agent.get('discovery_url', '')
    
    # Write merged data back to tracking file
    tracking_data['deployed_agents'] = list(all_agents.values())

    with open(tracking_file, 'w') as f:
        json.dump(tracking_data, f, indent=2)
    
    print(f"Merged {len(all_agents)} total agents", file=sys.stderr)
    sys.exit(0)
    
except Exception as e:
    print(f'ERROR merging: {str(e)}', file=sys.stderr)
    import traceback
    traceback.print_exc(file=sys.stderr)
    sys.exit(1)
MERGE_EOF

# Extract runtime ARNs and bearer tokens from merged tracking file
print_status "Extracting runtime configuration from merged tracking file..."

# Debug: Show what we're working with
print_status "  Stack Prefix: $STACK_PREFIX"
print_status "  Unique ID: $UNIQUE_ID"
print_status "  Region: $REGION"
print_status "  Tracking File: $TRACKING_FILE"

# Export environment variables for Python script (TRACKING_FILE already exported above)
export STACK_PREFIX
export UNIQUE_ID
export REGION

AGENTCORE_VALUES=$(python3 << 'EOF'
import json
import sys
import os

tracking_file = os.environ.get('TRACKING_FILE')
stack_prefix = os.environ.get('STACK_PREFIX')
unique_id = os.environ.get('UNIQUE_ID')
region = os.environ.get('REGION')

# Debug output
print(f'DEBUG: TRACKING_FILE={tracking_file}', file=sys.stderr)
print(f'DEBUG: STACK_PREFIX={stack_prefix}', file=sys.stderr)
print(f'DEBUG: UNIQUE_ID={unique_id}', file=sys.stderr)
print(f'DEBUG: REGION={region}', file=sys.stderr)

try:
    if not tracking_file:
        print('ERROR: TRACKING_FILE environment variable not set', file=sys.stderr)
        sys.exit(1)
    
    with open(tracking_file, 'r') as f:
        data = json.load(f)
    
    deployed_agents = data.get('deployed_agents', [])
    
    if not deployed_agents:
        print('ERROR: No deployed agents found in tracking file', file=sys.stderr)
        sys.exit(1)
    
    print(f'DEBUG: Found {len(deployed_agents)} agents in tracking file', file=sys.stderr)
    
    # Build configuration object
    config = {
        'stack_prefix': stack_prefix,
        'unique_id': unique_id,
        'region': region,
        'agents': []
    }
    
    for agent in deployed_agents:
        if not isinstance(agent, dict):
            continue
        
        agent_config = {
            'name': agent.get('name', ''),
            'runtime_arn': agent.get('runtime_arn', ''),
            'runtime_url': agent.get('runtime_url', '')
        }
        
        # Add A2A configuration if present
        if agent.get('protocol') == 'A2A':
            agent_config['protocol'] = 'A2A'
            agent_config['bearer_token'] = agent.get('bearer_token', '')
            agent_config['pool_id'] = agent.get('pool_id', '')
            agent_config['client_id'] = agent.get('client_id', '')
            agent_config['discovery_url'] = agent.get('discovery_url', '')
        
        config['agents'].append(agent_config)

        
    # Output as JSON string
    print(json.dumps(config, separators=(',', ':')))
    sys.exit(0)
    
except Exception as e:
    print(f'ERROR: {str(e)}', file=sys.stderr)
    import traceback
    traceback.print_exc(file=sys.stderr)
    sys.exit(1)
EOF
)

EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    print_error "Failed to extract runtime configuration from tracking file"
    exit 1
fi

if [ -z "$AGENTCORE_VALUES" ]; then
    print_error "No runtime configuration extracted from tracking file"
    exit 1
fi

# Validate JSON
if ! echo "$AGENTCORE_VALUES" | jq empty 2>/dev/null; then
    print_error "Invalid JSON generated from tracking file"
    exit 1
fi

# Count agents
AGENT_COUNT=$(echo "$AGENTCORE_VALUES" | jq '.agents | length')
print_status "Found $AGENT_COUNT AgentCore agent(s) in tracking file"

# Define SSM parameter name
PARAMETER_NAME="/${STACK_PREFIX}/agentcore_values/${UNIQUE_ID}"

print_status "Checking for existing SSM parameter..."
print_status "  Parameter Name: $PARAMETER_NAME"

# Try to retrieve existing parameter
EXISTING_VALUES=$(aws ssm get-parameter \
    --name "$PARAMETER_NAME" \
    --with-decryption \
    --region "$REGION" \
    $PROFILE_FLAG \
    --query 'Parameter.Value' \
    --output text 2>/dev/null || echo "")

# Merge with existing values if they exist
if [ -n "$EXISTING_VALUES" ] && [ "$EXISTING_VALUES" != "null" ]; then
    print_status "Found existing SSM parameter - merging with new deployments..."
    
    # Export variables for Python script
    export EXISTING_VALUES
    export AGENTCORE_VALUES
    export CONFIG_FILE
    # Merge the configurations
    MERGED_VALUES=$(python3 << 'EOF'
import json
import sys
import os
#config_file = os.environ.get('CONFIG_FILE', '')
#print(f'DEBUG: CONFIG_FILE={config_file}', file=sys.stderr)

try:
    # Parse existing and new values
    existing = json.loads(os.environ.get('EXISTING_VALUES', '{}'))
    new = json.loads(os.environ.get('AGENTCORE_VALUES', '{}'))
    # Load global configuration from the correct path
    #with open(config_file, 'r') as gc:
    #    global_config = json.load(gc)

    # Start with existing agents
    existing_agents = {agent['name']: agent for agent in existing.get('agents', [])}
    
    # Update/add new agents
    for agent in new.get('agents', []):
        existing_agents[agent['name']] = agent
    
    # Build merged config
    merged = {
        'stack_prefix': new.get('stack_prefix'),
        'unique_id': new.get('unique_id'),
        'region': new.get('region'),
        'agents': list(existing_agents.values())
    }
    
    print(json.dumps(merged, separators=(',', ':')))
    sys.exit(0)
    
except Exception as e:
    print(f'ERROR: {str(e)}', file=sys.stderr)
    sys.exit(1)
EOF
)
    
    if [ $? -eq 0 ] && [ -n "$MERGED_VALUES" ]; then
        AGENTCORE_VALUES="$MERGED_VALUES"
        AGENT_COUNT=$(echo "$AGENTCORE_VALUES" | jq '.agents | length')
        print_status "Merged configuration contains $AGENT_COUNT total agent(s)"
    else
        print_warning "Failed to merge configurations - using new values only"
    fi
else
    print_status "No existing SSM parameter found - creating new one"
fi

export EXISTING_VALUES
export AGENTCORE_VALUES

print_status "Storing configuration in SSM Parameter Store..."

# Store in SSM Parameter Store
if aws ssm put-parameter \
    --name "$PARAMETER_NAME" \
    --value "$AGENTCORE_VALUES" \
    --type "SecureString" \
    --overwrite \
    --region "$REGION" \
    $PROFILE_FLAG \
    --description "AgentCore runtime ARNs and bearer tokens for ${STACK_PREFIX}-${UNIQUE_ID}" \
    > /dev/null 2>&1; then
    
    print_status "✅ Successfully stored AgentCore values in SSM Parameter Store"
    print_status "   Parameter: $PARAMETER_NAME"
    print_status "   Type: SecureString"
    print_status "   Agents: $AGENT_COUNT"
    
    # Output summary
    echo ""
    print_status "📊 Configuration Summary:"
    echo "$AGENTCORE_VALUES" | jq -r '.agents[] | "   ✅ \(.name) - Runtime: \(.runtime_arn[:50])..."'
    
    echo ""
    print_status "🔐 To retrieve this configuration:"
    echo "   aws ssm get-parameter --name \"$PARAMETER_NAME\" --with-decryption --region $REGION $PROFILE_FLAG"
    
    exit 0
else
    print_error "❌ Failed to store AgentCore values in SSM Parameter Store"
    print_error "   Parameter: $PARAMETER_NAME"
    print_error "   Check IAM permissions for ssm:PutParameter"
    exit 1
fi
