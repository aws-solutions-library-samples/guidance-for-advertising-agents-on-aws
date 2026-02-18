#!/bin/bash

# AgentCore Agent Build and Deploy Script
# Builds Docker image and deploys to ECR and AgentCore

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

# Function to check if Docker is running
check_docker() {
    print_status "Checking Docker status..."
    
    # Check if Docker command exists
    if ! command -v docker &> /dev/null; then
        print_error "Docker is not installed. Please install Docker Desktop first."
        print_error "Download from: https://www.docker.com/products/docker-desktop"
        exit 1
    fi
    
    # Check if Docker daemon is running
    if ! docker info &> /dev/null; then
        print_warning "Docker daemon is not running. Attempting to start Docker..."
        
        # Detect OS and try to start Docker
        if [[ "$OSTYPE" == "darwin"* ]]; then
            # macOS
            print_status "Detected macOS. Starting Docker Desktop..."
            open -a Docker
            
            # Wait for Docker to start (max 60 seconds)
            print_status "Waiting for Docker to start..."
            local max_wait=60
            local waited=0
            while ! docker info &> /dev/null; do
                if [ $waited -ge $max_wait ]; then
                    print_error "Docker failed to start within ${max_wait} seconds"
                    print_error "Please start Docker Desktop manually and try again"
                    exit 1
                fi
                echo -n "."
                sleep 2
                waited=$((waited + 2))
            done
            echo ""
            print_status "✅ Docker is now running"
            
        elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
            # Linux
            print_status "Detected Linux. Attempting to start Docker service..."
            if command -v systemctl &> /dev/null; then
                sudo systemctl start docker
                sleep 3
                if docker info &> /dev/null; then
                    print_status "✅ Docker service started successfully"
                else
                    print_error "Failed to start Docker service"
                    print_error "Please start Docker manually: sudo systemctl start docker"
                    exit 1
                fi
            else
                print_error "Cannot automatically start Docker on this Linux system"
                print_error "Please start Docker manually and try again"
                exit 1
            fi
            
        else
            print_error "Unsupported operating system: $OSTYPE"
            print_error "Please start Docker manually and try again"
            exit 1
        fi
    else
        print_status "✅ Docker is running"
    fi
}

# Configuration
AGENT_NAME="${1:-AdFabricAgent}"
AWS_REGION="${AWS_REGION:-us-east-1}"
AWS_PROFILE="${AWS_PROFILE:-a2w}"
STACK_PREFIX="${STACK_PREFIX:-sim}"
UNIQUE_ID="${UNIQUE_ID:-}"

# Determine AgentCore region - use eu-central-1 for all EU regions
AGENTCORE_REGION="$AWS_REGION"
if [[ "$AWS_REGION" == eu-* ]]; then
    AGENTCORE_REGION="eu-central-1"
    print_status "EU region detected ($AWS_REGION), using AgentCore region: $AGENTCORE_REGION"
fi

# Validate required parameters
if [ -z "$UNIQUE_ID" ]; then
    print_error "UNIQUE_ID environment variable is required for AgentCore deployment"
    print_error "Please set UNIQUE_ID before running this script"
    print_error "Example: export UNIQUE_ID=abc123"
    exit 1
fi

# Use the provided AgentCore agent name if available, otherwise construct it
if [ -n "$AGENTCORE_AGENT_NAME" ]; then
    FULL_AGENT_NAME="$AGENTCORE_AGENT_NAME"
elif [ -n "$UNIQUE_ID" ]; then
    FULL_AGENT_NAME="${STACK_PREFIX}-${AGENT_NAME}-${UNIQUE_ID}"
else
    FULL_AGENT_NAME="${STACK_PREFIX}-${AGENT_NAME}"
fi

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
AGENTCORE_DIR="${PROJECT_ROOT}/agentcore"

# Validate inputs
if [ -z "$AGENT_NAME" ]; then
    print_error "Agent name is required"
    echo "Usage: $0 <agent_name>"
    exit 1
fi

print_status "Agent configuration:"
print_status "  Base agent name: $AGENT_NAME"
print_status "  Full agent name: $FULL_AGENT_NAME"
print_status "  Stack prefix: $STACK_PREFIX"
print_status "  Unique ID: ${UNIQUE_ID:-not set}"
print_status "  AWS Region: $AWS_REGION"
print_status "  AgentCore Region: $AGENTCORE_REGION"
print_status "  AWS Profile: $AWS_PROFILE"

# Check if agent config exists
# AGENT_CONFIG="${AGENTCORE_DIR}/deployment/agent/config.json"
# if [ ! -f "$AGENT_CONFIG" ]; then
#     print_error "Agent config not found: $AGENT_CONFIG"
#     exit 1
# fi

print_status "Building and deploying AgentCore agent: $AGENT_NAME as $FULL_AGENT_NAME"

# Check Docker before proceeding
check_docker

# Get AWS account ID
if [ -n "$AWS_PROFILE" ]; then
    ACCOUNT_ID=$(aws sts get-caller-identity --profile "$AWS_PROFILE" --query Account --output text)
else
    ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
fi

if [ -z "$ACCOUNT_ID" ]; then
    print_error "Failed to get AWS account ID"
    exit 1
fi

# ECR repository details - use full agent name for uniqueness
ECR_REPO_NAME="agentcore-$(echo "${FULL_AGENT_NAME}" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9-]/-/g')"
ECR_URI="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO_NAME}"

print_status "ECR Repository: $ECR_URI"

# Create ECR repository if it doesn't exist
print_status "Creating ECR repository if needed..."
if [ -n "$AWS_PROFILE" ]; then
    aws ecr create-repository --repository-name "$ECR_REPO_NAME" --region "$AWS_REGION" --profile "$AWS_PROFILE" 2>/dev/null || true
else
    aws ecr create-repository --repository-name "$ECR_REPO_NAME" --region "$AWS_REGION" 2>/dev/null || true
fi

# Get ECR login token
print_status "Logging into ECR..."
if [ -n "$AWS_PROFILE" ]; then
    aws ecr get-login-password --region "$AWS_REGION" --profile "$AWS_PROFILE" | docker login --username AWS --password-stdin "$ECR_URI"
else
    aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$ECR_URI"
fi

# Copy shared Dockerfile template to agent directory
# print_status "Copying shared Dockerfile template..."
# DOCKERFILE_TEMPLATE="${AGENTCORE_DIR}/Dockerfile.template"
# AGENT_DOCKERFILE="${AGENTCORE_DIR}/agents/${AGENT_NAME}/Dockerfile"

# if [ -f "$DOCKERFILE_TEMPLATE" ]; then
#     cp "$DOCKERFILE_TEMPLATE" "$AGENT_DOCKERFILE"
#     print_status "✅ Copied shared Dockerfile template to $AGENT_NAME"
# else
#     print_error "Dockerfile template not found at: $DOCKERFILE_TEMPLATE"
#     print_error "Please ensure agentcore/Dockerfile.template exists"
#     exit 1
# fi


# # Copy global config file to agent directory
# print_status "Copying shared global_configuration file..."
# GLOBAL_CONFIG="${AGENTCORE_DIR}/global_configuration.json"
# AGENT_CONFIG="${AGENTCORE_DIR}/agents/${AGENT_NAME}/global_configuration.json"

# if [ -f "$GLOBAL_CONFIG" ]; then
#     cp "$GLOBAL_CONFIG" "$AGENT_CONFIG"
#     print_status "✅ Copied shared Dockerfile template to $AGENT_NAME"
# else
#     print_error "Dockerfile template not found at: $GLOBAL_CONFIG"
#     print_error "Please ensure agentcore/global_configuration.json exists"
#     exit 1
# fi

# # Copy shared agent VISUALIZATIONS library to agent directory
# print_status "Copying agent-visualizations-library..."
# VISUALIZATIONS_LIBRARY="${AGENTCORE_DIR}/agent-visualizations-library"
# AGENT_VISUALIZATIONSLIBRARY="${AGENTCORE_DIR}/agents/${AGENT_NAME}/agent-visualizations-library"
# if [ -d "$VISUALIZATIONS_LIBRARY" ]; then
#     # Remove existing shared directory in build context
#     rm -rf "$AGENT_VISUALIZATIONSLIBRARY"
    
#     # Copy shared directory to agent build context
#     cp -r "$VISUALIZATIONS_LIBRARY" "$AGENT_VISUALIZATIONSLIBRARY"
#     print_status "✅ Shared visualization files copied to build context"
# else
#     print_warning "No shared visualization files found at: $VISUALIZATIONS_LIBRARY"
# fi


# # Copy shared agent instructions library to agent directory
# print_status "Copying agent-instructions-library..."
# INSTRUCTIONS_LIBRARY="${AGENTCORE_DIR}/agent-instructions-library"
# AGENT_INSTRUCTIONSLIBRARY="${AGENTCORE_DIR}/agents/${AGENT_NAME}/agent-instructions-library"
# if [ -d "$INSTRUCTIONS_LIBRARY" ]; then
#     # Remove existing shared directory in build context
#     rm -rf "$AGENT_INSTRUCTIONSLIBRARY"
    
#     # Copy shared directory to agent build context
#     cp -r "$INSTRUCTIONS_LIBRARY" "$AGENT_INSTRUCTIONSLIBRARY"
#     print_status "✅ Shared instructions files copied to build context"
# else
#     print_warning "No shared instructions files found at: $INSTRUCTIONS_LIBRARY"
# fi
# Copy shared handler template to agent directory (unless use_handler_template is false or protocol is A2A)
# print_status "Checking if handler template should be used..."
# HANDLER_CHECK=$(python3 -c "
# import json
# import sys
# try:
#     with open('${AGENT_CONFIG}', 'r') as f:
#         config = json.load(f)
    
#     # Check protocol configuration first - A2A agents use their own handler
#     protocol = config.get('protocol', '').upper()
#     if protocol == 'A2A':
#         print('skip_a2a')
#         sys.exit(0)
#     use_handler_template = true

#     # Check use_handler_template flag
#     use_template = config.get('use_handler_template')
#     if use_template not in ['false','False',False]:
#         use_handler_template = True  # Handle string values
#     else:
#         use_handler_template = False
#     print('true' if use_handler_template else 'false')
#     sys.exit(0)
# except Exception as e:
#     print('true')  # Default to true if config can't be read
#     sys.exit(0)

# ")

# if [ "$HANDLER_CHECK" == "skip_a2a" ]; then
#     print_status "⏭️  Skipping handler template copy (protocol is A2A)"
#     print_status "Using existing A2A handler.py for $AGENT_NAME"
# elif [ "$HANDLER_CHECK" == "true" ]; then
#     print_status "Copying shared handler template..."
#     HANDLER_TEMPLATE="${AGENTCORE_DIR}/handler.template.py"
#     AGENT_HANDLER="${AGENTCORE_DIR}/agents/${AGENT_NAME}/handler.py"

#     if [ -f "$HANDLER_TEMPLATE" ]; then
#         cp "$HANDLER_TEMPLATE" "$AGENT_HANDLER"
#         print_status "✅ Copied shared handler template to $AGENT_NAME"
#     else
#         print_error "Handler template not found at: $HANDLER_TEMPLATE"
#         print_error "Please ensure agentcore/handler.template.py exists"
#         exit 1
#     fi
# else
#     print_status "⏭️  Skipping handler template copy (use_handler_template is false)"
#     print_status "Using existing handler.py for $AGENT_NAME"
# fi

# # Copy agent-specific config to Docker build context
# print_status "Preparing build context..."
# mkdir -p "${AGENTCORE_DIR}/agents/${AGENT_NAME}/docker/config"
# cp "$AGENT_CONFIG" "${AGENTCORE_DIR}/agents/${AGENT_NAME}/docker/config/"

# # Copy agent instructions if they exist
# AGENT_INSTRUCTIONS="${AGENTCORE_DIR}/agents/${AGENT_NAME}/instructions.txt"
# if [ -f "$AGENT_INSTRUCTIONS" ]; then
#     cp "$AGENT_INSTRUCTIONS" "${AGENTCORE_DIR}/agents/${AGENT_NAME}/docker/config/"
# fi

# # Copy shared libraries into agent directory for Docker build context
# print_status "Copying shared libraries to build context..."
# if [ -d "${AGENTCORE_DIR}/agent-instructions-library" ]; then
#     cp -r "${AGENTCORE_DIR}/agent-instructions-library" "${AGENTCORE_DIR}/agents/${AGENT_NAME}/"
#     print_status "✅ Copied agent-instructions-library"
# else
#     print_warning "agent-instructions-library not found at ${AGENTCORE_DIR}/agent-instructions-library"
# fi

# if [ -d "${AGENTCORE_DIR}/agent-visualizations-library" ]; then
#     cp -r "${AGENTCORE_DIR}/agent-visualizations-library" "${AGENTCORE_DIR}/agents/${AGENT_NAME}/"
#     print_status "✅ Copied agent-visualizations-library"
# else
#     print_warning "agent-visualizations-library not found at ${AGENTCORE_DIR}/agent-visualizations-library"
# fi

# Setup Python virtual environment for deployment operations
print_status "Setting up Python virtual environment for deployment..."
VENV_DIR="${AGENTCORE_DIR}/deployment/.venv"

# Create virtual environment if it doesn't exist
if [ ! -d "$VENV_DIR" ]; then
    print_status "Creating Python virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

# Activate virtual environment
print_status "Activating virtual environment..."
source "$VENV_DIR/bin/activate"

# Verify virtual environment is active
if [ -z "$VIRTUAL_ENV" ]; then
    print_error "Failed to activate virtual environment"
    exit 1
fi

print_status "Virtual environment active: $VIRTUAL_ENV"

# Install/upgrade basic requirements in venv
print_status "Installing basic Python requirements in virtual environment..."
pip install --upgrade pip setuptools wheel --quiet

# Install deployment requirements
if [ -f "${AGENTCORE_DIR}/deployment/agent/requirements.txt" ]; then
    print_status "Installing deployment requirements..."
    pip install -r "${AGENTCORE_DIR}/deployment/agent/requirements.txt" --quiet
else
    print_warning "No deployment requirements.txt found, installing basic packages..."
    pip install boto3 pyyaml requests --quiet
fi

# No memory setup - memory is handled by ecosystem deployment only
print_status "Skipping memory setup - handled by ecosystem deployment"

# Setup External Agent Tools Configuration
# print_status "Setting up external agent tool configuration for agent: $AGENT_NAME"
# python3 -c "
# import json
# import os
# import shutil
# from datetime import datetime

# def setup_external_tools():
#     print('Setting up external agent tool configuration...')
    
#     # Load agent configuration to check for external_agent_tools
#     agent_config_path = '${AGENT_CONFIG}'
#     try:
#         with open(agent_config_path, 'r') as f:
#             agent_config = json.load(f)
#     except Exception as e:
#         print(f'Warning: Could not load agent config: {e}')
#         return False
    
#     external_tools = agent_config.get('external_agent_tools', [])
#     if not external_tools:
#         print('No external agent tools configured')
#         return True
    
#     print(f'Found {len(external_tools)} external agent tools: {external_tools}')
    
#     # Create external tools configuration directory
#     tools_config_dir = '${AGENTCORE_DIR}/agents/${AGENT_NAME}/docker/config/external_tools'
#     os.makedirs(tools_config_dir, exist_ok=True)
    
#     # Copy shared external agent tools modules
#     shared_tools_file = '${AGENTCORE_DIR}/shared/external_agent_tools.py'
#     if os.path.exists(shared_tools_file):
#         shutil.copy2(shared_tools_file, os.path.join(tools_config_dir, 'external_agent_tools.py'))
#         print(f'Copied external agent tools module')
    
#     shared_resolver_file = '${AGENTCORE_DIR}/shared/runtime_resolver.py'
#     if os.path.exists(shared_resolver_file):
#         shutil.copy2(shared_resolver_file, os.path.join(tools_config_dir, 'runtime_resolver.py'))
#         print(f'Copied runtime resolver module')
    
#     # Generate external tools configuration
#     tools_config = {
#         'stack_prefix': '${STACK_PREFIX}',
#         'unique_id': '${UNIQUE_ID}',
#         'agent_name': '${AGENT_NAME}',
#         'external_agent_tools': external_tools,
#         'generated_at': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
#     }
    
#     # Write external tools configuration file
#     tools_config_file = os.path.join(tools_config_dir, 'external_tools_config.json')
#     with open(tools_config_file, 'w') as f:
#         json.dump(tools_config, f, indent=2)
    
#     print(f'Created external tools configuration at: {tools_config_file}')
#     return True

# setup_external_tools()
# "


# Copy shared utilities to agent build context
# print_status "Copying shared utilities to build context..."
# SHARED_SOURCE_DIR="${AGENTCORE_DIR}/shared"
# SHARED_DEST_DIR="${AGENTCORE_DIR}/agents/${AGENT_NAME}/shared"

# if [ -d "$SHARED_SOURCE_DIR" ]; then
#     # Remove existing shared directory in build context
#     rm -rf "$SHARED_DEST_DIR"
    
#     # Copy shared directory to agent build context
#     cp -r "$SHARED_SOURCE_DIR" "$SHARED_DEST_DIR"
#     print_status "✅ Shared utilities copied to build context"
# else
#     print_warning "No shared utilities found at: $SHARED_SOURCE_DIR"
# fi

# # Ensure MCP configuration is available in Docker build context
# MCP_CONFIG_DIR="${AGENTCORE_DIR}/agents/${AGENT_NAME}/docker/config/mcp"
# if [ -d "$MCP_CONFIG_DIR" ]; then
#     print_status "MCP configuration ready for Docker build"
#     print_status "MCP config directory: $MCP_CONFIG_DIR"
# else
#     print_warning "No MCP configuration found, agent will deploy without MCP server access"
# fi

# # Ensure Memory configuration is available in Docker build context
# MEMORY_CONFIG_DIR="${AGENTCORE_DIR}/agents/${AGENT_NAME}/docker/config/memory"
# if [ -d "$MEMORY_CONFIG_DIR" ]; then
#     print_status "Memory configuration ready for Docker build"
#     print_status "Memory config directory: $MEMORY_CONFIG_DIR"
# else
#     print_warning "No Memory configuration found, agent will deploy without memory capabilities"
# fi

# Build Docker image
print_status "Building Docker image..."
cd "${AGENTCORE_DIR}/deployment/agent"
# Set memory environment variables for all AgentCore agents using consistent pattern
MEMORY_ID="${STACK_PREFIX}memory${UNIQUE_ID}"

# Get the visualizations table name from CloudFormation stack output
# print_status "Retrieving visualizations table name from infrastructure stack..."
# INFRASTRUCTURE_SERVICES_STACK="${STACK_PREFIX}-infrastructure-services"

# if [ -n "$AWS_PROFILE" ]; then
#     VISUALIZATIONS_TABLE_NAME=$(aws cloudformation describe-stacks --stack-name "$INFRASTRUCTURE_SERVICES_STACK" --region "$AWS_REGION" --profile "$AWS_PROFILE" --query "Stacks[0].Outputs[?OutputKey=='VisualizationsTableName'].OutputValue" --output text 2>/dev/null || echo "")
# else
#     VISUALIZATIONS_TABLE_NAME=$(aws cloudformation describe-stacks --stack-name "$INFRASTRUCTURE_SERVICES_STACK" --region "$AWS_REGION" --query "Stacks[0].Outputs[?OutputKey=='VisualizationsTableName'].OutputValue" --output text 2>/dev/null || echo "")
# fi

# # Clean up the table name (remove any whitespace/newlines)
# VISUALIZATIONS_TABLE_NAME=$(echo "$VISUALIZATIONS_TABLE_NAME" | tr -d '\n\r\t ')

# if [ -n "$VISUALIZATIONS_TABLE_NAME" ] && [ "$VISUALIZATIONS_TABLE_NAME" != "None" ] && [ "$VISUALIZATIONS_TABLE_NAME" != "" ]; then
#     print_status "Found visualizations table: $VISUALIZATIONS_TABLE_NAME"
# else
#     print_warning "Could not retrieve visualizations table name. Visualization features will be disabled."
#     VISUALIZATIONS_TABLE_NAME=""
# fi

# Gather knowledge base IDs for the stack
print_status "Gathering knowledge base IDs for stack: ${STACK_PREFIX}-*-${UNIQUE_ID}"
KNOWLEDGEBASES=""
if [ -n "$AWS_PROFILE" ]; then
    KB_LIST=$(aws bedrock-agent list-knowledge-bases --profile "$AWS_PROFILE" --region "$AWS_REGION" --max-results 100 --query "knowledgeBaseSummaries[?starts_with(name, '${STACK_PREFIX}-') && ends_with(name, '-${UNIQUE_ID}')].{name:name,id:knowledgeBaseId}" --output json 2>/dev/null || echo "[]")
else
    KB_LIST=$(aws bedrock-agent list-knowledge-bases --region "$AWS_REGION" --max-results 100 --query "knowledgeBaseSummaries[?starts_with(name, '${STACK_PREFIX}-') && ends_with(name, '-${UNIQUE_ID}')].{name:name,id:knowledgeBaseId}" --output json 2>/dev/null || echo "[]")
fi

# Process knowledge bases into name:ID format
if [ "$KB_LIST" != "[]" ] && [ -n "$KB_LIST" ]; then
    KNOWLEDGEBASES=$(echo "$KB_LIST" | python3 -c "
import json
import sys
try:
    kbs = json.load(sys.stdin)
    kb_pairs = []
    stack_prefix = '${STACK_PREFIX}-'
    unique_suffix = '-${UNIQUE_ID}'
    for kb in kbs:
        name = kb.get('name', '')
        kb_id = kb.get('id', '')
        if name and kb_id and name.startswith(stack_prefix) and name.endswith(unique_suffix):
            # Extract the middle part as the KB type name
            kb_type = name[len(stack_prefix):-len(unique_suffix)]
            kb_pairs.append(f'{kb_type}:{kb_id}')
    print(','.join(kb_pairs))
except:
    print('')
")
fi

if [ -n "$KNOWLEDGEBASES" ]; then
    print_status "Found knowledge bases: $KNOWLEDGEBASES"
    print_status "Knowledge bases will be available in container via KNOWLEDGEBASES environment variable"
else
    print_warning "No knowledge bases found for stack pattern"
fi

# Gather runtime ARNs from SSM Parameter Store
print_status "Gathering runtime ARNs for stack: ${STACK_PREFIX}-*-${UNIQUE_ID}"
RUNTIMES=""

# Try to retrieve from SSM Parameter Store first
SSM_PARAMETER_NAME="/${STACK_PREFIX}/agentcore_values/${UNIQUE_ID}"
SSM_CONFIG_PARAMETER_NAME="/${STACK_PREFIX}/global_config/${UNIQUE_ID}"
print_status "Attempting to retrieve runtime configuration from SSM Parameter Store..."
print_status "  Parameter: $SSM_PARAMETER_NAME"

if [ -n "$AWS_PROFILE" ]; then
    SSM_VALUE=$(aws ssm get-parameter --name "$SSM_PARAMETER_NAME" --with-decryption --region "$AWS_REGION" --profile "$AWS_PROFILE" --query 'Parameter.Value' --output text 2>/dev/null || echo "")
else
    SSM_VALUE=$(aws ssm get-parameter --name "$SSM_PARAMETER_NAME" --with-decryption --region "$AWS_REGION" --query 'Parameter.Value' --output text 2>/dev/null || echo "")
fi

if [ -n "$SSM_VALUE" ] && [ "$SSM_VALUE" != "None" ]; then
    print_status "✅ Found runtime configuration in SSM Parameter Store"
    
    # Parse SSM JSON and generate RUNTIMES format (ARNs only, no bearer tokens)
    RUNTIMES=$(echo "$SSM_VALUE" | python3 -c "
import json
import sys

try:
    data = json.load(sys.stdin)
    agents = data.get('agents', [])
    
    runtime_entries = []
    for agent in agents:
        runtime_arn = agent.get('runtime_arn', '')
        
        if runtime_arn:
            runtime_entries.append(runtime_arn)
    
    print(','.join(runtime_entries))
    sys.exit(0)
    
except Exception as e:
    print('', file=sys.stderr)
    print(f'Error parsing SSM value: {e}', file=sys.stderr)
    sys.exit(0)
" 2>&1)
    
    if [ -n "$RUNTIMES" ]; then
        print_status "Loaded RUNTIMES from SSM"
        print_status "RUNTIMES format: arn,arn,... (${#RUNTIMES} characters)"
    else
        print_warning "SSM parameter exists but no runtimes could be parsed"
    fi
else
    print_warning "No runtime configuration found in SSM Parameter Store"
    print_status "Falling back to local registry file..."
    
    # Fallback to local registry file
    REGISTRY_FILE="${PROJECT_ROOT}/.agentcore-agents-${STACK_PREFIX}-${UNIQUE_ID}.json"
    if [ -f "$REGISTRY_FILE" ]; then
        print_status "Loading runtime registry from: $REGISTRY_FILE"
        RUNTIMES=$(python3 -c "
import json
import sys

try:
    with open('${REGISTRY_FILE}', 'r') as f:
        registry = json.load(f)
    
    agents = registry.get('deployed_agents', [])
    runtime_entries = []
    
    for agent in agents:
        runtime_arn = agent.get('runtime_arn','')
        if runtime_arn:
            runtime_entries.append(runtime_arn)
    
    print(','.join(runtime_entries))
except Exception as e:
    print('', file=sys.stderr)
    print(f'Error loading runtime registry: {e}', file=sys.stderr)
    sys.exit(0)
" 2>&1)
        
        if [ -n "$RUNTIMES" ]; then
            print_status "Loaded RUNTIMES from local registry"
            print_status "RUNTIMES format: arn,arn,... (${#RUNTIMES} characters)"
        else
            print_warning "Runtime registry exists but no runtimes found"
        fi
    else
        print_warning "Runtime registry not found: $REGISTRY_FILE"
        print_warning "RUNTIMES environment variable will be empty"
        print_warning "A2A agent invocation will not work until runtimes are registered"
    fi
fi

# Get AdCP Gateway URL from SSM or environment variable
print_status "Checking for AdCP MCP Gateway URL..."
ADCP_GATEWAY_URL_VALUE="${ADCP_GATEWAY_URL:-}"

if [ -z "$ADCP_GATEWAY_URL_VALUE" ]; then
    # Try to get from SSM
    SSM_GATEWAY_PARAM="/${STACK_PREFIX}/adcp_gateway/${UNIQUE_ID}"
    if [ -n "$AWS_PROFILE" ]; then
        ADCP_GATEWAY_URL_VALUE=$(aws ssm get-parameter --name "$SSM_GATEWAY_PARAM" --region "$AWS_REGION" --profile "$AWS_PROFILE" --query 'Parameter.Value' --output text 2>/dev/null || echo "")
    else
        ADCP_GATEWAY_URL_VALUE=$(aws ssm get-parameter --name "$SSM_GATEWAY_PARAM" --region "$AWS_REGION" --query 'Parameter.Value' --output text 2>/dev/null || echo "")
    fi
fi

if [ -n "$ADCP_GATEWAY_URL_VALUE" ] && [ "$ADCP_GATEWAY_URL_VALUE" != "None" ]; then
    print_status "✅ AdCP Gateway URL found: $ADCP_GATEWAY_URL_VALUE"
    print_status "   Agents will use MCP Gateway for AdCP tools"
else
    print_warning "⚠️  AdCP Gateway URL not found"
    print_warning "   Agents will use fallback local tools for AdCP"
    ADCP_GATEWAY_URL_VALUE=""
fi

docker build \
    --build-arg MEMORY_ID="$MEMORY_ID" \
    --build-arg ACTOR_ID="AdFabricAgent" \
    --build-arg STACK_PREFIX="$STACK_PREFIX" \
    --build-arg UNIQUE_ID="$UNIQUE_ID" \
    --build-arg KNOWLEDGEBASES="$KNOWLEDGEBASES" \
    --build-arg RUNTIMES="$RUNTIMES" \
    --build-arg ADCP_GATEWAY_URL="$ADCP_GATEWAY_URL_VALUE" \
    --build-arg ADCP_USE_MCP="true" \
    -t "$ECR_REPO_NAME:latest" .

# # Clean up copied libraries from build context
# print_status "Cleaning up build context..."
# rm -rf "${AGENTCORE_DIR}/agents/${AGENT_NAME}/agent-instructions-library"
# rm -rf "${AGENTCORE_DIR}/agents/${AGENT_NAME}/agent-visualizations-library"

# Tag for ECR
docker tag "$ECR_REPO_NAME:latest" "$ECR_URI:latest"

# Push to ECR
print_status "Pushing image to ECR..."
docker push "$ECR_URI:latest"

# Check for existing AgentCore runtime and update or create
print_status "Checking for existing AgentCore runtime..."
cd "${AGENTCORE_DIR}/deployment/agent"

# Convert agent name to valid runtime name (replace hyphens with underscores)
RUNTIME_NAME=$(echo "${FULL_AGENT_NAME}" | sed 's/-/_/g')
print_status "Runtime name: $RUNTIME_NAME"

# Check if runtime already exists - first check SSM, then fall back to AWS API
EXISTING_RUNTIME_ID=""
print_status "Checking for existing AgentCore runtime: $RUNTIME_NAME"

# First, try to get runtime ID from SSM Parameter Store (faster and more reliable)
print_status "Checking SSM Parameter Store for existing runtime..."
SSM_AGENTCORE_PARAM="/${STACK_PREFIX}/agentcore_values/${UNIQUE_ID}"

if [ -n "$AWS_PROFILE" ]; then
    SSM_AGENTCORE_VALUE=$(aws ssm get-parameter --name "$SSM_AGENTCORE_PARAM" --with-decryption --region "$AWS_REGION" --profile "$AWS_PROFILE" --query 'Parameter.Value' --output text 2>/dev/null || echo "")
else
    SSM_AGENTCORE_VALUE=$(aws ssm get-parameter --name "$SSM_AGENTCORE_PARAM" --with-decryption --region "$AWS_REGION" --query 'Parameter.Value' --output text 2>/dev/null || echo "")
fi

if [ -n "$SSM_AGENTCORE_VALUE" ] && [ "$SSM_AGENTCORE_VALUE" != "None" ]; then
    print_status "✅ Found AgentCore configuration in SSM: $SSM_AGENTCORE_PARAM"
    
    # Parse SSM JSON to find runtime ID for this agent
    # Also validate that the runtime ARN matches the expected stack prefix
    EXISTING_RUNTIME_ID=$(echo "$SSM_AGENTCORE_VALUE" | python3 -c "
import json
import sys

try:
    data = json.load(sys.stdin)
    agents = data.get('agents', [])
    target_name = '${FULL_AGENT_NAME}'
    stack_prefix = '${STACK_PREFIX}'
    
    for agent in agents:
        if agent.get('name') == target_name:
            runtime_arn = agent.get('runtime_arn', '')
            if runtime_arn:
                # Extract runtime ID from ARN: arn:aws:bedrock-agentcore:region:account:runtime/ID
                parts = runtime_arn.split('/')
                if len(parts) >= 2:
                    runtime_id = parts[-1]
                    # Validate that runtime ID starts with the correct stack prefix
                    # Runtime names use underscores: stack_prefix_AgentName_uniqueid
                    expected_prefix = stack_prefix.replace('-', '_') + '_'
                    if runtime_id.startswith(expected_prefix):
                        print(runtime_id)
                    else:
                        # Runtime ARN doesn't match expected stack prefix - data is stale/wrong
                        print(f'MISMATCH: runtime {runtime_id} does not match stack prefix {stack_prefix}', file=sys.stderr)
                    break
except Exception as e:
    print(f'Error parsing SSM: {e}', file=sys.stderr)
" 2>&1)
    
    # Check if we got a mismatch warning
    if echo "$EXISTING_RUNTIME_ID" | grep -q "MISMATCH"; then
        print_warning "⚠️  SSM contains stale runtime ARN from different stack"
        print_warning "   $EXISTING_RUNTIME_ID"
        EXISTING_RUNTIME_ID=""
    elif [ -n "$EXISTING_RUNTIME_ID" ] && [ "$EXISTING_RUNTIME_ID" != "None" ]; then
        print_status "✅ Found runtime ID from SSM: $EXISTING_RUNTIME_ID"
    else
        print_status "Agent '$FULL_AGENT_NAME' not found in SSM configuration"
        EXISTING_RUNTIME_ID=""
    fi
else
    print_status "SSM parameter not found: $SSM_AGENTCORE_PARAM"
fi

# Fall back to AWS API query if SSM didn't have the runtime
if [ -z "$EXISTING_RUNTIME_ID" ] || [ "$EXISTING_RUNTIME_ID" = "None" ]; then
    print_status "Querying AWS API for existing runtime: $RUNTIME_NAME"
    
    # Query all runtimes and filter by exact name match
    if [ -n "$AWS_PROFILE" ]; then
        ALL_RUNTIMES=$(aws bedrock-agentcore-control list-agent-runtimes --profile "$AWS_PROFILE" --region "$AGENTCORE_REGION" --output json 2>/dev/null || echo "{}")
    else
        ALL_RUNTIMES=$(aws bedrock-agentcore-control list-agent-runtimes --region "$AGENTCORE_REGION" --output json 2>/dev/null || echo "{}")
    fi
    
    # Parse and find exact match for our runtime name
    EXISTING_RUNTIME_ID=$(echo "$ALL_RUNTIMES" | python3 -c "
import json
import sys

try:
    data = json.load(sys.stdin)
    runtimes = data.get('agentRuntimes', [])
    target_name = '${RUNTIME_NAME}'
    
    for runtime in runtimes:
        runtime_name = runtime.get('agentRuntimeName', '')
        if runtime_name == target_name:
            print(runtime.get('agentRuntimeId', ''))
            break
except Exception as e:
    pass
" 2>/dev/null || echo "")
fi

# Clean up the runtime ID (remove any whitespace/newlines)
EXISTING_RUNTIME_ID=$(echo "$EXISTING_RUNTIME_ID" | tr -d '\n\r\t ' | head -1)

print_status "Runtime lookup result for '$RUNTIME_NAME': '${EXISTING_RUNTIME_ID:-not found}'"

if [ -n "$EXISTING_RUNTIME_ID" ] && [ "$EXISTING_RUNTIME_ID" != "None" ] && [ "$EXISTING_RUNTIME_ID" != "" ]; then
    print_status "Found existing AgentCore runtime: $EXISTING_RUNTIME_ID"
    print_status "Updating existing runtime with new container image..."
    
    # Get the existing role ARN
    print_status "Retrieving existing role ARN..."
    if [ -n "$AWS_PROFILE" ]; then
        EXISTING_ROLE_ARN=$(aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$EXISTING_RUNTIME_ID" --profile "$AWS_PROFILE" --region "$AGENTCORE_REGION" --query "roleArn" --output text 2>/dev/null || echo "")
    else
        EXISTING_ROLE_ARN=$(aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$EXISTING_RUNTIME_ID" --region "$AGENTCORE_REGION" --query "roleArn" --output text 2>/dev/null || echo "")
    fi
    
    # Clean up the role ARN
    EXISTING_ROLE_ARN=$(echo "$EXISTING_ROLE_ARN" | tr -d '\n\r\t ')
    
    if [ -z "$EXISTING_ROLE_ARN" ] || [ "$EXISTING_ROLE_ARN" = "None" ]; then
        print_error "Could not retrieve existing role ARN for runtime $EXISTING_RUNTIME_ID"
        exit 1
    fi
    
    print_status "Using existing role ARN: $EXISTING_ROLE_ARN"
    
    # Check if the existing role is the default SDK role (lacks DynamoDB, SSM, etc. permissions)
    # If so, create/ensure a custom role with full permissions and switch to it
    if echo "$EXISTING_ROLE_ARN" | grep -q "AmazonBedrockAgentCoreSDKRuntime"; then
        print_warning "⚠️  Runtime is using the default AgentCore SDK role which lacks DynamoDB/SSM permissions"
        print_status "Creating/ensuring custom execution role with full permissions..."
        
        CUSTOM_ROLE_NAME="AgentCoreRole-${FULL_AGENT_NAME}"
        # Truncate role name to 64 chars (IAM limit)
        CUSTOM_ROLE_NAME=$(echo "$CUSTOM_ROLE_NAME" | cut -c1-64)
        
        CUSTOM_ROLE_ARN=$(python3 << ROLE_EOF
import json
import boto3
import sys
import time

stack_prefix = "${STACK_PREFIX}"
unique_id = "${UNIQUE_ID}"
region = "${AWS_REGION}"
agentcore_region = "${AGENTCORE_REGION}"
role_name = "${CUSTOM_ROLE_NAME}"
profile = "${AWS_PROFILE}" if "${AWS_PROFILE}" else None

session_kwargs = {}
if profile:
    session_kwargs["profile_name"] = profile
session = boto3.Session(**session_kwargs)
iam = session.client("iam")
sts = session.client("sts")
account_id = sts.get_caller_identity()["Account"]

trust_policy = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }
    ],
}

permissions_policy = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "PassRoleAccess",
            "Effect": "Allow",
            "Action": ["iam:PassRole"],
            "Resource": f"arn:aws:iam::{account_id}:role/*",
        },
        {
            "Sid": "SpecificNamespaceAccess",
            "Effect": "Allow",
            "Action": [
                "bedrock-agentcore:RetrieveMemoryRecords",
                "bedrock-agentcore:ListMemoryRecords",
                "bedrock-agentcore:CreateEvent",
                "s3:GetObject", "s3:ListBucket", "s3:PutObject",
            ],
            "Resource": [
                f"arn:aws:bedrock-agentcore:{agentcore_region}:{account_id}:memory/*",
                f"arn:aws:s3:::{stack_prefix}-data-*",
                f"arn:aws:s3:::{stack_prefix}-data-*/*",
                f"arn:aws:s3:::{stack_prefix}-generated-content-*",
                f"arn:aws:s3:::{stack_prefix}-generated-content-*/*",
            ],
        },
        {
            "Sid": "AllowAgentCoreMemoryKMS",
            "Effect": "Allow",
            "Action": ["kms:DescribeKey", "kms:CreateGrant", "kms:Decrypt", "kms:GenerateDataKey"],
            "Resource": "arn:aws:kms:*:*:key/*",
            "Condition": {"StringEquals": {"kms:ViaService": f"bedrock-agentcore.{agentcore_region}.amazonaws.com"}},
        },
        {
            "Sid": "ECRAccess",
            "Effect": "Allow",
            "Action": [
                "ecr:GetAuthorizationToken", "ecr:BatchCheckLayerAvailability",
                "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage",
                "ecr:DescribeRepositories", "ecr:DescribeImages", "ecr:ListImages",
            ],
            "Resource": "*",
        },
        {
            "Sid": "GenerateImagesLambda",
            "Effect": "Allow",
            "Action": ["lambda:InvokeFunction"],
            "Resource": [f"arn:aws:lambda:{region}:{account_id}:function:{stack_prefix}-CreativeImageGenerator-*"],
        },
        {
            "Sid": "CloudWatchLogs",
            "Effect": "Allow",
            "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogGroups", "logs:DescribeLogStreams"],
            "Resource": [f"arn:aws:logs:{region}:{account_id}:*"],
        },
        {
            "Sid": "XRayTracing",
            "Effect": "Allow",
            "Action": ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"],
            "Resource": "*",
        },
        {
            "Sid": "CloudWatchMetrics",
            "Effect": "Allow",
            "Resource": [f"arn:aws:cloudwatch:*:{account_id}:*:*"],
            "Action": ["cloudwatch:PutMetricData"],
        },
        {
            "Sid": "GetAgentAccessToken",
            "Effect": "Allow",
            "Action": [
                "bedrock-agentcore:GetWorkloadAccessToken",
                "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
            ],
            "Resource": [
                f"arn:aws:bedrock-agentcore:{region}:{account_id}:workload-identity-directory/default",
                f"arn:aws:bedrock-agentcore:{region}:{account_id}:workload-identity-directory/default/workload-identity/*",
            ],
        },
        {
            "Sid": "BedrockModelInvocation",
            "Effect": "Allow",
            "Action": [
                "bedrock:InvokeAgent", "bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream",
                "bedrock:ApplyGuardrail", "bedrock:Retrieve", "bedrock:RetrieveAndGenerate",
                "bedrock:ListFoundationModels", "bedrock:ListKnowledgeBases", "bedrock:ListDataSources",
            ],
            "Resource": ["arn:aws:bedrock:*::*/*", "arn:aws:bedrock:*:*:*", f"arn:aws:bedrock:*:{account_id}:inference-profile/*"],
        },
        {
            "Sid": "AgentCoreGatewayInvoke",
            "Effect": "Allow",
            "Action": [
                "bedrock-agentcore:InvokeGateway", "bedrock-agentcore:GetGateway",
                "bedrock-agentcore:ListGateways", "bedrock-agentcore:ListGatewayTargets",
                "bedrock-agentcore:GetGatewayTarget",
            ],
            "Resource": f"arn:aws:bedrock-agentcore:*:{account_id}:*",
        },
        {
            "Sid": "AgentCoreMemory",
            "Effect": "Allow",
            "Action": [
                "bedrock-agentcore:List*", "bedrock-agentcore:Create*",
                "bedrock-agentcore:Delete*", "bedrock-agentcore:Update*",
                "bedrock-agentcore:Start*", "bedrock-agentcore:Stop*",
            ],
            "Resource": f"arn:aws:bedrock-agentcore:*:{account_id}:*memory*",
        },
        {
            "Sid": "DynamoDBAccess",
            "Effect": "Allow",
            "Action": [
                "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem",
                "dynamodb:DeleteItem", "dynamodb:Query", "dynamodb:Scan",
                "dynamodb:DescribeTable",
            ],
            "Resource": f"arn:aws:dynamodb:{region}:{account_id}:*/*",
        },
        {
            "Sid": "SSMParameterAccess",
            "Effect": "Allow",
            "Action": ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath", "ssm:PutParameter", "ssm:DeleteParameter"],
            "Resource": [f"arn:aws:ssm:{region}:{account_id}:parameter/*/*"],
        },
    ],
}

try:
    # Create role
    try:
        response = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Custom IAM role for AgentCore runtime with full permissions",
        )
        role_arn = response["Role"]["Arn"]
        print(f"Created new role: {role_arn}", file=sys.stderr)
    except iam.exceptions.EntityAlreadyExistsException:
        response = iam.get_role(RoleName=role_name)
        role_arn = response["Role"]["Arn"]
        print(f"Using existing role: {role_arn}", file=sys.stderr)

    # Create or update policy
    policy_name = f"{role_name}-Policy"
    policy_arn_full = f"arn:aws:iam::{account_id}:policy/{policy_name}"
    
    try:
        iam.create_policy(
            PolicyName=policy_name,
            PolicyDocument=json.dumps(permissions_policy),
            Description="Permissions for AgentCore runtime",
        )
        print(f"Created policy: {policy_name}", file=sys.stderr)
    except iam.exceptions.EntityAlreadyExistsException:
        print(f"Policy exists, updating...", file=sys.stderr)
        try:
            versions = iam.list_policy_versions(PolicyArn=policy_arn_full).get("Versions", [])
            non_default = [v for v in versions if not v.get("IsDefaultVersion", False)]
            if len(versions) >= 5:
                non_default.sort(key=lambda x: str(x.get("CreateDate", "")))
                for old in non_default[:len(versions) - 4]:
                    iam.delete_policy_version(PolicyArn=policy_arn_full, VersionId=old["VersionId"])
            iam.create_policy_version(PolicyArn=policy_arn_full, PolicyDocument=json.dumps(permissions_policy), SetAsDefault=True)
            print(f"Updated policy version", file=sys.stderr)
        except Exception as e:
            print(f"Warning: Could not update policy: {e}", file=sys.stderr)

    # Attach policy to role
    try:
        iam.attach_role_policy(RoleName=role_name, PolicyArn=policy_arn_full)
    except Exception:
        pass  # Already attached

    # Attach X-Ray policy
    try:
        iam.attach_role_policy(RoleName=role_name, PolicyArn="arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess")
    except Exception:
        pass

    # Wait for IAM propagation
    print("Waiting 15 seconds for IAM propagation...", file=sys.stderr)
    time.sleep(15)

    # Output the role ARN to stdout
    print(role_arn)

except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr)
    sys.exit(1)
ROLE_EOF
)
        
        if [ $? -eq 0 ] && [ -n "$CUSTOM_ROLE_ARN" ]; then
            CUSTOM_ROLE_ARN=$(echo "$CUSTOM_ROLE_ARN" | tr -d '\n\r\t ')
            print_status "✅ Custom role ready: $CUSTOM_ROLE_ARN"
            print_status "Switching runtime from default SDK role to custom role with DynamoDB/SSM permissions"
            EXISTING_ROLE_ARN="$CUSTOM_ROLE_ARN"
        else
            print_warning "⚠️  Could not create custom role, continuing with existing role"
            print_warning "   DynamoDB access may fail at runtime"
        fi
    else
        # Existing custom role - update its IAM policy to ensure latest permissions
        print_status "Updating IAM policy on existing custom role to ensure latest permissions..."
        EXISTING_ROLE_NAME=$(echo "$EXISTING_ROLE_ARN" | awk -F'/' '{print $NF}')
        
        if [ -n "$EXISTING_ROLE_NAME" ] && [ "$EXISTING_ROLE_NAME" != "None" ]; then
            python3 << POLICY_UPDATE_EOF
import json
import boto3
import sys

role_name = "${EXISTING_ROLE_NAME}"
stack_prefix = "${STACK_PREFIX}"
region = "${AWS_REGION}"
agentcore_region = "${AGENTCORE_REGION}"
profile = "${AWS_PROFILE}" if "${AWS_PROFILE}" else None

try:
    session = boto3.Session(profile_name=profile, region_name=region) if profile else boto3.Session(region_name=region)
    iam = session.client('iam')
    sts = session.client('sts')
    account_id = sts.get_caller_identity()['Account']

    policy_name = f"{role_name}-Policy"
    policy_arn = f"arn:aws:iam::{account_id}:policy/{policy_name}"

    permissions_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {"Sid": "PassRoleAccess", "Effect": "Allow", "Action": ["iam:PassRole"], "Resource": f"arn:aws:iam::{account_id}:role/*"},
            {"Sid": "SpecificNamespaceAccess", "Effect": "Allow",
             "Action": ["bedrock-agentcore:RetrieveMemoryRecords", "bedrock-agentcore:ListMemoryRecords", "bedrock-agentcore:CreateEvent", "s3:GetObject", "s3:ListBucket", "s3:PutObject"],
             "Resource": [f"arn:aws:bedrock-agentcore:{agentcore_region}:{account_id}:memory/*", f"arn:aws:s3:::{stack_prefix}-data-*", f"arn:aws:s3:::{stack_prefix}-data-*/*", f"arn:aws:s3:::{stack_prefix}-generated-content-*", f"arn:aws:s3:::{stack_prefix}-generated-content-*/*"]},
            {"Sid": "AllowAgentCoreMemoryKMS", "Effect": "Allow",
             "Action": ["kms:DescribeKey", "kms:CreateGrant", "kms:Decrypt", "kms:GenerateDataKey"],
             "Resource": "arn:aws:kms:*:*:key/*",
             "Condition": {"StringEquals": {"kms:ViaService": f"bedrock-agentcore.{agentcore_region}.amazonaws.com"}}},
            {"Sid": "ECRAccess", "Effect": "Allow",
             "Action": ["ecr:GetAuthorizationToken", "ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage", "ecr:DescribeRepositories", "ecr:DescribeImages", "ecr:ListImages"],
             "Resource": "*"},
            {"Sid": "GenerateImagesLambda", "Effect": "Allow", "Action": ["lambda:InvokeFunction"],
             "Resource": [f"arn:aws:lambda:{region}:{account_id}:function:{stack_prefix}-CreativeImageGenerator-*"]},
            {"Sid": "CloudWatchLogs", "Effect": "Allow",
             "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogGroups", "logs:DescribeLogStreams"],
             "Resource": [f"arn:aws:logs:{region}:{account_id}:*"]},
            {"Sid": "XRayTracing", "Effect": "Allow",
             "Action": ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"],
             "Resource": "*"},
            {"Sid": "CloudWatchMetrics", "Effect": "Allow", "Resource": [f"arn:aws:cloudwatch:*:{account_id}:*:*"], "Action": ["cloudwatch:PutMetricData"]},
            {"Sid": "GetAgentAccessToken", "Effect": "Allow",
             "Action": ["bedrock-agentcore:GetWorkloadAccessToken", "bedrock-agentcore:GetWorkloadAccessTokenForJWT", "bedrock-agentcore:GetWorkloadAccessTokenForUserId"],
             "Resource": [f"arn:aws:bedrock-agentcore:{region}:{account_id}:workload-identity-directory/default", f"arn:aws:bedrock-agentcore:{region}:{account_id}:workload-identity-directory/default/workload-identity/*"]},
            {"Sid": "BedrockModelInvocation", "Effect": "Allow",
             "Action": ["bedrock:InvokeAgent", "bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream", "bedrock:ApplyGuardrail", "bedrock:Retrieve", "bedrock:RetrieveAndGenerate", "bedrock:ListFoundationModels", "bedrock:ListKnowledgeBases", "bedrock:ListDataSources"],
             "Resource": ["arn:aws:bedrock:*::*/*", "arn:aws:bedrock:*:*:*", f"arn:aws:bedrock:*:{account_id}:inference-profile/*"]},
            {"Sid": "AgentCoreGatewayInvoke", "Effect": "Allow",
             "Action": ["bedrock-agentcore:InvokeGateway", "bedrock-agentcore:GetGateway", "bedrock-agentcore:ListGateways", "bedrock-agentcore:ListGatewayTargets", "bedrock-agentcore:GetGatewayTarget"],
             "Resource": f"arn:aws:bedrock-agentcore:*:{account_id}:*"},
            {"Sid": "AgentCoreMemory", "Effect": "Allow",
             "Action": ["bedrock-agentcore:List*", "bedrock-agentcore:Create*", "bedrock-agentcore:Delete*", "bedrock-agentcore:Update*", "bedrock-agentcore:Start*", "bedrock-agentcore:Stop*"],
             "Resource": f"arn:aws:bedrock-agentcore:*:{account_id}:*memory*"},
            {"Sid": "DynamoDBAccess", "Effect": "Allow",
             "Action": ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:Query", "dynamodb:Scan", "dynamodb:DescribeTable"],
             "Resource": f"arn:aws:dynamodb:{region}:{account_id}:table/*"},
            {"Sid": "SSMParameterAccess", "Effect": "Allow",
             "Action": ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath", "ssm:PutParameter", "ssm:DeleteParameter"],
             "Resource": [f"arn:aws:ssm:{region}:{account_id}:parameter/{stack_prefix}/*"]},
        ],
    }

    policy_doc = json.dumps(permissions_policy)

    try:
        iam.create_policy(PolicyName=policy_name, PolicyDocument=policy_doc, Description="Permissions for AgentCore runtime")
        print(f"Created new policy: {policy_name}")
    except iam.exceptions.EntityAlreadyExistsException:
        try:
            versions = iam.list_policy_versions(PolicyArn=policy_arn).get('Versions', [])
            non_default = [v for v in versions if not v.get('IsDefaultVersion', False)]
            if len(versions) >= 5:
                non_default.sort(key=lambda x: str(x.get('CreateDate', '')))
                for old in non_default[:len(versions) - 4]:
                    iam.delete_policy_version(PolicyArn=policy_arn, VersionId=old['VersionId'])
            iam.create_policy_version(PolicyArn=policy_arn, PolicyDocument=policy_doc, SetAsDefault=True)
            print(f"Updated policy with latest permissions: {policy_name}")
        except Exception as e:
            print(f"Warning: Could not update policy version: {e}", file=sys.stderr)

    try:
        iam.attach_role_policy(RoleName=role_name, PolicyArn=policy_arn)
    except Exception:
        pass

    print("IAM policy update complete")

except Exception as e:
    print(f"Warning: Failed to update IAM policy: {e}", file=sys.stderr)
    print("Continuing with deployment - existing permissions will be used")
POLICY_UPDATE_EOF
            
            if [ $? -eq 0 ]; then
                print_status "✅ IAM policy updated with latest permissions (including DynamoDB access)"
            else
                print_warning "⚠️  IAM policy update had issues - check output above"
            fi
        else
            print_warning "Could not extract role name from ARN, skipping IAM policy update"
        fi
    fi
    
    # Update the existing runtime with environment variables
    print_status "Executing update command with environment variables..."
    
    # Build environment variables JSON
    ENV_VARS="{\"STACK_PREFIX\":\"${STACK_PREFIX}\",\"UNIQUE_ID\":\"${UNIQUE_ID}\",\"KNOWLEDGEBASES\":\"${KNOWLEDGEBASES}\",\"RUNTIMES\":\"${RUNTIMES}\""
    
    # Add AdCP MCP Gateway URL if available
    if [ -n "$ADCP_GATEWAY_URL_VALUE" ] && [ "$ADCP_GATEWAY_URL_VALUE" != "None" ]; then
        ENV_VARS="${ENV_VARS},\"ADCP_GATEWAY_URL\":\"${ADCP_GATEWAY_URL_VALUE}\",\"ADCP_USE_MCP\":\"true\""
        print_status "Adding AdCP Gateway URL to runtime environment: $ADCP_GATEWAY_URL_VALUE"
    else
        print_warning "AdCP Gateway URL not available - agents will use fallback local tools"
    fi
    
    ENV_VARS="${ENV_VARS}}"
    
    if [ -n "$AWS_PROFILE" ]; then
        aws bedrock-agentcore-control update-agent-runtime \
            --agent-runtime-id "$EXISTING_RUNTIME_ID" \
            --agent-runtime-artifact "{\"containerConfiguration\": {\"containerUri\": \"${ECR_URI}:latest\"}}" \
            --environment-variables "$ENV_VARS" \
            --role-arn "$EXISTING_ROLE_ARN" \
            --network-configuration '{"networkMode": "PUBLIC"}' \
            --description "Updated agent runtime with new container image and environment variables" \
            --profile "$AWS_PROFILE" \
            --region "$AGENTCORE_REGION"
    else
        aws bedrock-agentcore-control update-agent-runtime \
            --agent-runtime-id "$EXISTING_RUNTIME_ID" \
            --agent-runtime-artifact "{\"containerConfiguration\": {\"containerUri\": \"${ECR_URI}:latest\"}}" \
            --environment-variables "$ENV_VARS" \
            --role-arn "$EXISTING_ROLE_ARN" \
            --network-configuration '{"networkMode": "PUBLIC"}' \
            --description "Updated agent runtime with new container image and environment variables" \
            --region "$AGENTCORE_REGION"
    fi
    
    if [ $? -eq 0 ]; then
        print_status "✅ AgentCore runtime '$EXISTING_RUNTIME_ID' updated successfully!"
        print_status "Agent: $AGENT_NAME (runtime: $RUNTIME_NAME)"
        print_status "Container URI: $ECR_URI:latest"
        
        # Update the tracking file with the correct runtime information
        # This ensures SSM gets the right data when store_agentcore_values.sh runs
        print_status "Updating tracking file with runtime information..."
        TRACKING_FILE="${PROJECT_ROOT}/.agentcore-agents-${STACK_PREFIX}-${UNIQUE_ID}.json"
        
        # Get account ID for ARN construction
        if [ -n "$AWS_PROFILE" ]; then
            ACCOUNT_ID=$(aws sts get-caller-identity --profile "$AWS_PROFILE" --query 'Account' --output text 2>/dev/null || echo "")
        else
            ACCOUNT_ID=$(aws sts get-caller-identity --query 'Account' --output text 2>/dev/null || echo "")
        fi
        
        RUNTIME_ARN="arn:aws:bedrock-agentcore:${AGENTCORE_REGION}:${ACCOUNT_ID}:runtime/${EXISTING_RUNTIME_ID}"
        
        python3 << TRACKING_EOF
import json
import os
from datetime import datetime

tracking_file = "${TRACKING_FILE}"
agent_name = "${FULL_AGENT_NAME}"
runtime_id = "${EXISTING_RUNTIME_ID}"
runtime_arn = "${RUNTIME_ARN}"
container_uri = "${ECR_URI}:latest"
stack_prefix = "${STACK_PREFIX}"
unique_id = "${UNIQUE_ID}"

# Load existing config or create new one
if os.path.exists(tracking_file):
    with open(tracking_file, 'r') as f:
        config = json.load(f)
else:
    config = {
        "deployed_agents": [],
        "deployment_time": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stack_prefix": stack_prefix,
        "unique_id": unique_id,
    }

# Ensure deployed_agents is a list
if "deployed_agents" not in config:
    config["deployed_agents"] = []

# Create agent runtime info
agent_runtime_info = {
    "name": agent_name,
    "runtime_id": runtime_id,
    "runtime_arn": runtime_arn,
    "container_uri": container_uri,
    "deployment_time": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    "memory_config": {"memory_id": f"{stack_prefix}memory{unique_id}"},
    "external_tools": [],
    "runtime_name": agent_name.replace("-", "_"),
}

# Check if agent already exists in the list
existing_agent_index = None
for i, agent in enumerate(config["deployed_agents"]):
    if isinstance(agent, dict) and agent.get("name") == agent_name:
        existing_agent_index = i
        break

if existing_agent_index is not None:
    config["deployed_agents"][existing_agent_index] = agent_runtime_info
    print(f"Updated existing agent in tracking file: {agent_name}")
else:
    config["deployed_agents"].append(agent_runtime_info)
    print(f"Added new agent to tracking file: {agent_name}")

# Write updated config
with open(tracking_file, 'w') as f:
    json.dump(config, f, indent=2)

print(f"Tracking file updated: {tracking_file}")
TRACKING_EOF
        
        # Configure X-Ray trace segment destination for CloudWatch Logs (fixes OTLP export error)
        print_status "Configuring X-Ray trace segment destination for CloudWatch Logs..."
        if [ -n "$AWS_PROFILE" ]; then
            aws xray update-trace-segment-destination \
                --destination CloudWatchLogs \
                --profile "$AWS_PROFILE" \
                --region "$AWS_REGION" 2>/dev/null || print_warning "⚠️  Could not update trace segment destination (may already be configured or not supported)"
        else
            aws xray update-trace-segment-destination \
                --destination CloudWatchLogs \
                --region "$AWS_REGION" 2>/dev/null || print_warning "⚠️  Could not update trace segment destination (may already be configured or not supported)"
        fi
        print_status "✅ X-Ray trace segment destination configured"
    else
        print_error "Failed to update AgentCore runtime"
        exit 1
    fi
else
    print_status "No existing runtime found. Creating new AgentCore runtime..."
    
    # Set environment variables for deployer
    export AWS_REGION="$AWS_REGION"
    export AGENTCORE_REGION="$AGENTCORE_REGION"
    if [ -n "$AWS_PROFILE" ]; then
        export AWS_PROFILE="$AWS_PROFILE"
    fi
    
    # Export AdCP Gateway URL for deploy_agentcore_manual.py to pick up
    if [ -n "$ADCP_GATEWAY_URL_VALUE" ] && [ "$ADCP_GATEWAY_URL_VALUE" != "None" ]; then
        export ADCP_GATEWAY_URL="$ADCP_GATEWAY_URL_VALUE"
        print_status "Exporting ADCP_GATEWAY_URL for runtime deployment: $ADCP_GATEWAY_URL_VALUE"
    fi

    # Install deployment requirements if not already installed
    if [ -f "${AGENTCORE_DIR}/deployment/agent/requirements.txt" ]; then
        print_status "Ensuring deployment requirements are installed..."
        pip install -r "${AGENTCORE_DIR}/deployment/agent/requirements.txt" --quiet --disable-pip-version-check || {
            print_warning "Failed to install deployment requirements, continuing..."
        }
    fi
    
    # Pass the base agent name - deploy_agentcore_manual.py will construct full name
    # This ensures consistent naming across all tracking and deployment operations
    python3 ${AGENTCORE_DIR}/deployment/deploy_agentcore_manual.py \
        --profile "${AWS_PROFILE}" \
        --region "${AWS_REGION}" \
        --agentcore-region "${AGENTCORE_REGION}" \
        --stack-prefix "${STACK_PREFIX}" \
        --unique-id "${UNIQUE_ID}" \
        --agent-name "${AGENT_NAME}" \
        --agent-folder "${AGENTCORE_DIR}/deployment/agent" \
        --container-uri "${ECR_URI}:latest"
    
    if [ $? -eq 0 ]; then
        print_status "✅ AgentCore agent '$AGENT_NAME' deployed successfully as '$FULL_AGENT_NAME'!"
        print_status "Container URI: $ECR_URI:latest"
        
        # Configure X-Ray trace segment destination for CloudWatch Logs (fixes OTLP export error)
        print_status "Configuring X-Ray trace segment destination for CloudWatch Logs..."
        if [ -n "$AWS_PROFILE" ]; then
            aws xray update-trace-segment-destination \
                --destination CloudWatchLogs \
                --profile "$AWS_PROFILE" \
                --region "$AWS_REGION" 2>/dev/null || print_warning "⚠️  Could not update trace segment destination (may already be configured or not supported)"
        else
            aws xray update-trace-segment-destination \
                --destination CloudWatchLogs \
                --region "$AWS_REGION" 2>/dev/null || print_warning "⚠️  Could not update trace segment destination (may already be configured or not supported)"
        fi
        print_status "✅ X-Ray trace segment destination configured"
        
        # Register runtime in A2A registry (if A2A agent)
        if [ -n "$A2A_POOL_ID" ]; then
            print_status "Registering A2A runtime in centralized registry..."
            
            # Get runtime ARN from tracking file
            TRACKING_FILE="${PROJECT_ROOT}/.agentcore-agents-${STACK_PREFIX}-${UNIQUE_ID}.json"
            if [ -f "$TRACKING_FILE" ]; then
                RUNTIME_ARN=$(python3 -c "
import json
with open('$TRACKING_FILE', 'r') as f:
    data = json.load(f)
    for agent in data.get('deployed_agents', []):
        if agent.get('name') == '$FULL_AGENT_NAME':
            print(agent.get('runtime_arn', ''))
            break
" 2>/dev/null)
                
                if [ -n "$RUNTIME_ARN" ]; then
                    python3 "${AGENTCORE_DIR}/deployment/register_runtime_a2a.py" \
                        --stack-prefix "$STACK_PREFIX" \
                        --unique-id "$UNIQUE_ID" \
                        --runtime-arn "$RUNTIME_ARN" \
                        --agent-name "$AGENT_NAME" \
                        --pool-id "$A2A_POOL_ID" \
                        --client-id "$A2A_CLIENT_ID" \
                        --discovery-url "$A2A_DISCOVERY_URL"
                    
                    print_status "✅ A2A runtime registered in centralized registry"
                else
                    print_warning "⚠️  Could not find runtime ARN in tracking file"
                fi
            else
                print_warning "⚠️  Tracking file not found: $TRACKING_FILE"
            fi
        else
            print_status "Registering standard runtime in registry (no A2A)..."
            # Still register non-A2A runtimes for completeness
            TRACKING_FILE="${PROJECT_ROOT}/.agentcore-agents-${STACK_PREFIX}-${UNIQUE_ID}.json"
            if [ -f "$TRACKING_FILE" ]; then
                RUNTIME_ARN=$(python3 -c "
import json
with open('$TRACKING_FILE', 'r') as f:
    data = json.load(f)
    for agent in data.get('deployed_agents', []):
        if agent.get('name') == '$FULL_AGENT_NAME':
            print(agent.get('runtime_arn', ''))
            break
" 2>/dev/null)
                
                if [ -n "$RUNTIME_ARN" ]; then
                    python3 "${AGENTCORE_DIR}/deployment/register_runtime_a2a.py" \
                        --stack-prefix "$STACK_PREFIX" \
                        --unique-id "$UNIQUE_ID" \
                        --runtime-arn "$RUNTIME_ARN" \
                        --agent-name "$AGENT_NAME"
                    
                    print_status "✅ Standard runtime registered in registry"
                fi
            fi
        fi
    else
        print_error "Failed to deploy AgentCore runtime using Manual Method B"
        exit 1
    fi
fi

# Cleanup (keep shared directory for runtime use)
rm -rf "${AGENTCORE_DIR}/deployment/agent/docker/config"

# Deactivate virtual environment
if [ -n "$VIRTUAL_ENV" ]; then
    print_status "Deactivating virtual environment..."
    deactivate
fi

print_status "✅ AgentCore agent deployment completed!"
print_status "Agent: $AGENT_NAME"
print_status "Container: $ECR_URI:latest"

print_status ""
print_status "🎉 Deployment Summary:"
print_status "   ✅ AgentCore Agent: $AGENT_NAME"
print_status "   ✅ Shared Dockerfile: Used template from agentcore/Dockerfile.template"
# if [ "$HANDLER_CHECK" = "skip_a2a" ]; then
#     print_status "   ✅ A2A Handler: Using existing A2A protocol handler.py"
# elif [ "$HANDLER_CHECK" = "true" ]; then
#     print_status "   ✅ Shared Handler: Used template from agentcore/handler.template.py"
# else
#     print_status "   ✅ Custom Handler: Using existing agent-specific handler.py"
# fi
print_status "   ✅ Container Image: $ECR_URI:latest"
print_status ""
print_status "📊 Environment Variables Configured:"
if [ -n "$KNOWLEDGEBASES" ]; then
    KB_COUNT=$(echo "$KNOWLEDGEBASES" | tr ',' '\n' | wc -l)
    print_status "   ✅ KNOWLEDGEBASES: $KB_COUNT knowledge bases pre-configured"
else
    print_status "   ⚠️  KNOWLEDGEBASES: No knowledge bases found"
fi
if [ -n "$RUNTIMES" ]; then
    RUNTIME_COUNT=$(echo "$RUNTIMES" | tr ',' '\n' | wc -l)
    print_status "   ✅ RUNTIMES: $RUNTIME_COUNT runtime ARNs pre-configured"
else
    print_status "   ⚠️  RUNTIMES: No runtime ARNs found"
fi
print_status "   ✅ STACK_PREFIX: $STACK_PREFIX"
print_status "   ✅ UNIQUE_ID: $UNIQUE_ID"
print_status ""

# Store configuration in SSM Parameter Store for UI access
print_status "Storing AgentCore configuration in SSM Parameter Store..."
STORE_SCRIPT="${SCRIPT_DIR}/store_agentcore_values.sh"
if [ -f "$STORE_SCRIPT" ]; then
    if [ -n "$AWS_PROFILE" ]; then
        "$STORE_SCRIPT" --stack-prefix "$STACK_PREFIX" --unique-id "$UNIQUE_ID" --region "$AWS_REGION" --profile "$AWS_PROFILE"
    else
        "$STORE_SCRIPT" --stack-prefix "$STACK_PREFIX" --unique-id "$UNIQUE_ID" --region "$AWS_REGION"
    fi
    
    if [ $? -eq 0 ]; then
        print_status "✅ Configuration stored in SSM Parameter Store"
    else
        print_warning "⚠️  Failed to store configuration in SSM Parameter Store"
        print_warning "   Runtime configuration is still available in local tracking file"
    fi
else
    print_warning "⚠️  SSM storage script not found: $STORE_SCRIPT"
fi
print_status ""