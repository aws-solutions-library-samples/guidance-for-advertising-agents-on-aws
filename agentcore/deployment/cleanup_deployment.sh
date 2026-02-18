#!/bin/bash

# AgentCore Agent Cleanup Script
# Removes deployed ECS service, ALB, and related resources

set -e

# Configuration
AGENT_NAME="${1}"
AWS_REGION="${AWS_REGION:-us-east-1}"
AWS_PROFILE="${AWS_PROFILE:-rtbag}"
STACK_PREFIX="${STACK_PREFIX:-sg2}"

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

# Validate inputs
if [ -z "$AGENT_NAME" ]; then
    print_error "Agent name is required"
    echo "Usage: $0 <agent_name>"
    echo "Example: $0 weather-agent"
    exit 1
fi

STACK_NAME="${STACK_PREFIX}-agentcore-${AGENT_NAME}"

print_status "Cleaning up AgentCore agent deployment: $AGENT_NAME"
print_status "Stack name: $STACK_NAME"

# Check if stack exists
print_status "Checking if CloudFormation stack exists..."
if [ -n "$AWS_PROFILE" ]; then
    STACK_STATUS=$(aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query 'Stacks[0].StackStatus' \
        --output text \
        --region "$AWS_REGION" \
        --profile "$AWS_PROFILE" 2>/dev/null || echo "DOES_NOT_EXIST")
else
    STACK_STATUS=$(aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query 'Stacks[0].StackStatus' \
        --output text \
        --region "$AWS_REGION" 2>/dev/null || echo "DOES_NOT_EXIST")
fi

if [ "$STACK_STATUS" = "DOES_NOT_EXIST" ]; then
    print_warning "CloudFormation stack $STACK_NAME does not exist"
else
    print_status "Found stack with status: $STACK_STATUS"
    
    # Delete the CloudFormation stack
    print_status "Deleting CloudFormation stack..."
    if [ -n "$AWS_PROFILE" ]; then
        aws cloudformation delete-stack \
            --stack-name "$STACK_NAME" \
            --region "$AWS_REGION" \
            --profile "$AWS_PROFILE"
    else
        aws cloudformation delete-stack \
            --stack-name "$STACK_NAME" \
            --region "$AWS_REGION"
    fi
    
    print_status "Waiting for stack deletion to complete..."
    if [ -n "$AWS_PROFILE" ]; then
        aws cloudformation wait stack-delete-complete \
            --stack-name "$STACK_NAME" \
            --region "$AWS_REGION" \
            --profile "$AWS_PROFILE"
    else
        aws cloudformation wait stack-delete-complete \
            --stack-name "$STACK_NAME" \
            --region "$AWS_REGION"
    fi
    
    print_status "✅ CloudFormation stack deleted successfully"
fi

# Optional: Clean up ECR repository
read -p "Do you want to delete the ECR repository as well? (y/N): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    ECR_REPO_NAME="agentcore-$(echo ${AGENT_NAME} | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g')"
    
    print_status "Deleting ECR repository: $ECR_REPO_NAME"
    if [ -n "$AWS_PROFILE" ]; then
        aws ecr delete-repository \
            --repository-name "$ECR_REPO_NAME" \
            --region "$AWS_REGION" \
            --profile "$AWS_PROFILE" \
            --force 2>/dev/null || print_warning "ECR repository may not exist or already deleted"
    else
        aws ecr delete-repository \
            --repository-name "$ECR_REPO_NAME" \
            --region "$AWS_REGION" \
            --force 2>/dev/null || print_warning "ECR repository may not exist or already deleted"
    fi
fi

# Optional: Remove from agentcore-agents.json
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
AGENTCORE_CONFIG="${PROJECT_ROOT}/bedrock-adtech-demo/src/assets/agentcore-agents.json"

if [ -f "$AGENTCORE_CONFIG" ]; then
    read -p "Do you want to remove the agent from agentcore-agents.json? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        print_status "Removing agent from agentcore-agents.json..."
        
        # Create a backup
        cp "$AGENTCORE_CONFIG" "${AGENTCORE_CONFIG}.cleanup-backup"
        
        # Remove using Python
        python3 -c "
import json
import sys

config_file = '$AGENTCORE_CONFIG'
agent_name = '$AGENT_NAME'

try:
    with open(config_file, 'r') as f:
        config = json.load(f)
    
    # Remove the agent
    original_count = len(config.get('agentcore_agents', []))
    config['agentcore_agents'] = [
        agent for agent in config.get('agentcore_agents', [])
        if agent.get('name') != agent_name
    ]
    new_count = len(config.get('agentcore_agents', []))
    
    with open(config_file, 'w') as f:
        json.dump(config, f, indent=2)
    
    if original_count > new_count:
        print('✅ Removed agent from agentcore-agents.json')
    else:
        print('ℹ️  Agent was not found in agentcore-agents.json')
    
except Exception as e:
    print(f'❌ Failed to update config: {e}')
    sys.exit(1)
"
        
        if [ $? -eq 0 ]; then
            print_status "✅ agentcore-agents.json updated successfully"
            print_status "📄 Backup saved as ${AGENTCORE_CONFIG}.cleanup-backup"
        else
            print_warning "⚠️  Failed to update agentcore-agents.json automatically"
        fi
    fi
fi

print_status "🧹 Cleanup completed for AgentCore agent: $AGENT_NAME"
print_status ""
print_status "Summary of actions taken:"
print_status "- Deleted CloudFormation stack: $STACK_NAME"
print_status "- Removed ECS service, ALB, target groups, and security groups"
print_status "- Cleaned up CloudWatch log groups"
if [[ $REPLY =~ ^[Yy]$ ]]; then
    print_status "- Deleted ECR repository (if requested)"
    print_status "- Removed agent from agentcore-agents.json (if requested)"
fi
print_status ""
print_status "The agent has been completely removed from your AWS environment."