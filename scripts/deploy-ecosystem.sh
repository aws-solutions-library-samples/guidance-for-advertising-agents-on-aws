#!/bin/bash

# Agentic Advertising Ecosystem (ID SO9631) - Complete Deployment Script (AgentCore-focused)
# This script deploys the complete agent ecosystem using AgentCore agents exclusively
#
# NON-INTERACTIVE MODE:
# - Deploys AgentCore agents by default
# - Set INTERACTIVE_MODE=false to skip prompts
#
# USAGE EXAMPLES:
# Interactive deployment with agent selection:
#   ./scripts/deploy-ecosystem.sh
#
# Non-interactive deployment (all agents):
#   INTERACTIVE_MODE=false ./scripts/deploy-ecosystem.sh
#
# Deploy with specific stack prefix:
#   ./scripts/deploy-ecosystem.sh --stack-prefix mystack

set -e

# Set project root directory (parent of scripts directory)  
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Ensure we're working from the project root directory
cd "$PROJECT_ROOT" || {
    echo "Error: Could not change to project root directory: $PROJECT_ROOT"
    exit 1
}

# Configuration defaults
STACK_PREFIX="${STACK_PREFIX:-sim}"
AWS_REGION="${AWS_REGION:-us-east-1}"
AWS_PROFILE="${AWS_PROFILE:-}"
DEMO_USER_EMAIL="${DEMO_USER_EMAIL:-}"
IMAGE_GENERATION_MODEL="${IMAGE_GENERATION_MODEL:-amazon.nova-canvas-v1:0}"
INTERACTIVE_MODE="${INTERACTIVE_MODE:-true}"
SKIP_CONFIRMATIONS="${SKIP_CONFIRMATIONS:-false}"
RESUME_AT_STEP=1
UNIQUE_ID="${UNIQUE_ID:-}"
CLEAN_DEPLOYMENT=true
CLEANUP_MODE=false

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_step() {
    echo -e "${BLUE}[STEP]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

# Python environment setup
PYTHON_CMD="python3"
VENV_PATH="${PROJECT_ROOT}/.venv-deployment"

# Function to setup Python environment with required dependencies
setup_python_environment() {
    print_status "Setting up Python environment for AWS operations..."
    
    # Check if virtual environment exists
    if [ ! -d "$VENV_PATH" ]; then
        print_status "Creating Python virtual environment..."
        python3 -m venv "$VENV_PATH"
        
        if [ $? -ne 0 ]; then
            print_error "Failed to create virtual environment. Please ensure python3-venv is installed."
            print_error "On Ubuntu/Debian: sudo apt-get install python3-venv"
            print_error "On macOS: python3 should include venv by default"
            exit 1
        fi
    fi
    
    # Activate virtual environment and set Python command
    source "$VENV_PATH/bin/activate"
    PYTHON_CMD="$VENV_PATH/bin/python"
    
    # Check if boto3 is installed
    if ! $PYTHON_CMD -c "import boto3" 2>/dev/null; then
        print_status "Installing required Python dependencies (boto3, botocore)..."
        $PYTHON_CMD -m pip install --upgrade pip
        $PYTHON_CMD -m pip install boto3 botocore
        
        if [ $? -ne 0 ]; then
            print_error "Failed to install Python dependencies"
            exit 1
        fi
        
        print_success "✅ Python dependencies installed successfully"
    else
        print_status "✅ Python dependencies already available"
    fi
}

# Function to build AWS CLI command with optional profile
aws_cmd() {
    if [ -n "$AWS_PROFILE" ]; then
        AWS_PAGER="" aws --profile "$AWS_PROFILE" "$@"
    else
        AWS_PAGER="" aws "$@"
    fi
}

# Function to check if stack exists
stack_exists() {
    aws_cmd cloudformation describe-stacks --stack-name "$1" --region "$AWS_REGION" > /dev/null 2>&1
}

# Function to get stack status
get_stack_status() {
    local stack_name="$1"
    aws_cmd cloudformation describe-stacks --stack-name "$stack_name" --region "$AWS_REGION" --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo "DOES_NOT_EXIST"
}

# Function to handle stacks in ROLLBACK_COMPLETE state
handle_rollback_complete() {
    local stack_name="$1"
    local stack_status=$(get_stack_status "$stack_name")
    
    if [ "$stack_status" = "ROLLBACK_COMPLETE" ] || [ "$stack_status" = "DELETE_FAILED" ]; then
        print_warning "Stack $stack_name is in $stack_status state, deleting before recreation..."
        
        # Delete the stack with force delete if it's in DELETE_FAILED state
        local delete_cmd="aws_cmd cloudformation delete-stack --stack-name $stack_name --region $AWS_REGION"
        if [ "$stack_status" = "DELETE_FAILED" ]; then
            delete_cmd="$delete_cmd --deletion-mode FORCE_DELETE_STACK"
            print_status "Using FORCE_DELETE_STACK mode for DELETE_FAILED stack"
        fi
        
        eval "$delete_cmd"
        
        # Wait for deletion to complete
        print_status "Waiting for stack deletion to complete..."
        aws_cmd cloudformation wait stack-delete-complete --stack-name "$stack_name" --region "$AWS_REGION"
        
        if [ $? -eq 0 ]; then
            print_success "✅ Stack $stack_name deleted successfully"
            return 0
        else
            print_error "❌ Failed to delete stack $stack_name"
            return 1
        fi
    fi
    
    return 0
}

# Function to check if IAM role exists
iam_role_exists() {
    local role_name="$1"
    aws_cmd iam get-role --role-name "$role_name" --region "$AWS_REGION" > /dev/null 2>&1
}

# Function to check if knowledge base exists
knowledge_base_exists() {
    local kb_name="$1"
    local kb_list=$(aws_cmd bedrock-agent list-knowledge-bases --region "$AWS_REGION" --query "knowledgeBaseSummaries[?name=='$kb_name'].knowledgeBaseId" --output text 2>/dev/null)
    [ -n "$kb_list" ] && [ "$kb_list" != "None" ]
}

# Function to check if agent exists
agent_exists() {
    local agent_name="$1"
    local agent_list=$(aws_cmd bedrock-agent list-agents --region "$AWS_REGION" --query "agentSummaries[?agentName=='$agent_name'].agentId" --output text 2>/dev/null)
    [ -n "$agent_list" ] && [ "$agent_list" != "None" ]
}

# Function to check if an agent is A2A-enabled
# Returns 0 (true) if agent has both use_handler_template=true AND protocol="A2A"
# Returns 1 (false) otherwise
is_a2a_agent() {
    local config_file="$1"
    
    # Check if config file exists
    if [ ! -f "$config_file" ]; then
        return 1
    fi
    
    # Extract use_handler_template and protocol values from config
    local use_handler=$(jq -r '.use_handler_template // false' "$config_file" 2>/dev/null)
    local protocol=$(jq -r '.protocol // ""' "$config_file" 2>/dev/null)
    
    # Check if both conditions are met for A2A
    if [[ "$use_handler" == "true" && "$protocol" == "A2A" ]]; then
        return 0  # Is A2A agent
    fi
    
    # Log warning if protocol is A2A but use_handler_template is false/missing
    if [[ "$protocol" == "A2A" && "$use_handler" != "true" ]]; then
        local agent_name=$(basename "$(dirname "$config_file")")
        print_warning "⚠️  Agent '$agent_name' has protocol=A2A but use_handler_template is not true"
        print_warning "    This agent will be treated as a standard agent. Set use_handler_template=true to enable A2A."
    fi
    
    return 1  # Not A2A agent
}

# Function to deploy A2A handler template to agent directory
# Copies a2ahandler.template.py to the agent's directory as handler.py
# Returns 0 on success, 1 on failure
deploy_a2a_handler() {
    local agent_dir="$1"
    local agent_name=$(basename "$agent_dir")
    
    print_status "Deploying A2A handler template for agent: $agent_name"
    
    # Define paths
    local template_path="${PROJECT_ROOT}/agentcore/a2ahandler.template.py"
    local target_path="${agent_dir}/handler.py"
    
    # Verify template file exists
    if [ ! -f "$template_path" ]; then
        print_error "❌ A2A handler template not found at: $template_path"
        print_error "   Expected location: agentcore/a2ahandler.template.py"
        print_error "   Please ensure the A2A handler template file exists before deploying A2A agents."
        return 1
    fi
    
    # Verify agent directory exists
    if [ ! -d "$agent_dir" ]; then
        print_error "❌ Agent directory not found: $agent_dir"
        return 1
    fi
    
    # Copy template to agent directory as handler.py
    print_status "Copying A2A handler template to: $target_path"
    if cp "$template_path" "$target_path"; then
        print_success "✅ A2A handler template deployed successfully for $agent_name"
        print_status "   Template variables and placeholders preserved for runtime substitution"
        return 0
    else
        print_error "❌ Failed to copy A2A handler template to $target_path"
        return 1
    fi

    
}

# Function to store A2A configuration in deployment tracking file
# Updates the .agentcore-agents-{stack-prefix}-{unique-id}.json file with A2A details
# Returns 0 on success, 1 on failure
store_a2a_configuration() {
    local agentcore_agent_name="$1"
    local agent_name="$2"
    local runtime_arn="${3:-}"
    local runtime_url="${4:-}"
    
    print_status "Storing A2A configuration for agent: $agentcore_agent_name"
    
    local tracking_file="${PROJECT_ROOT}/.agentcore-agents-${STACK_PREFIX}-${UNIQUE_ID}.json"
    
    # Check local tracking file first
    if [ ! -f "$tracking_file" ]; then
        print_warning "⚠️  Local AgentCore tracking file not found: $tracking_file"
        print_status "   Checking SSM Parameter Store for AgentCore configuration..."
        
        # Try to retrieve from SSM
        local ssm_param_name="/${STACK_PREFIX}/agentcore_values/${UNIQUE_ID}"
        local ssm_config
        
        if ssm_config=$(aws ssm get-parameter --name "$ssm_param_name" --with-decryption --query 'Parameter.Value' --output text 2>/dev/null); then
            print_success "✅ Found AgentCore configuration in SSM: $ssm_param_name"
            
            # Create local tracking file from SSM data
            echo "$ssm_config" > "$tracking_file"
            print_status "   Created local tracking file from SSM data"
        else
            print_error "❌ AgentCore configuration not found in SSM: $ssm_param_name"
            print_warning "   A2A configuration will not be persisted"
            return 1
        fi
    fi
    
    # Verify required A2A configuration variables are set
    if [[ -z "$BEARER_TOKEN" || "$BEARER_TOKEN" == "null" ]]; then
        print_error "❌ BEARER_TOKEN not set - cannot store A2A configuration"
        return 1
    fi
    
    if [[ -z "$POOL_ID" || "$POOL_ID" == "null" ]]; then
        print_error "❌ POOL_ID not set - cannot store A2A configuration"
        return 1
    fi
    
    if [[ -z "$CLIENT_ID" || "$CLIENT_ID" == "null" ]]; then
        print_error "❌ CLIENT_ID not set - cannot store A2A configuration"
        return 1
    fi
    
    if [[ -z "$DISCOVERY_URL" || "$DISCOVERY_URL" == "null" ]]; then
        print_error "❌ DISCOVERY_URL not set - cannot store A2A configuration"
        return 1
    fi
    
    # Use Python to update the JSON file with A2A configuration
    setup_python_environment
    
    local update_result
    update_result=$($PYTHON_CMD << EOF
import json
import sys
from datetime import datetime

tracking_file = "$tracking_file"
agent_name = "$agentcore_agent_name"
bearer_token = "$BEARER_TOKEN"
pool_id = "$POOL_ID"
client_id = "$CLIENT_ID"
discovery_url = "$DISCOVERY_URL"
runtime_arn = "$runtime_arn"
runtime_url = "$runtime_url"

try:
    # Read existing tracking file
    with open(tracking_file, 'r') as f:
        data = json.load(f)
    
    # Find the agent in deployed_agents array
    deployed_agents = data.get('deployed_agents', [])
    agent_found = False
    
    for agent in deployed_agents:
        if isinstance(agent, dict) and agent.get('name') == agent_name:
            # Update agent with A2A configuration
            agent['protocol'] = 'A2A'
            agent['bearer_token'] = bearer_token
            agent['pool_id'] = pool_id
            agent['client_id'] = client_id
            agent['discovery_url'] = discovery_url
            agent['a2a_deployment_timestamp'] = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
            
            # Update runtime_arn and runtime_url if provided
            if runtime_arn:
                agent['runtime_arn'] = runtime_arn
            if runtime_url:
                agent['runtime_url'] = runtime_url
            
            agent_found = True
            print(f"Updated agent: {agent_name}", file=sys.stderr)
            print(f"  - protocol: A2A", file=sys.stderr)
            print(f"  - pool_id: {pool_id}", file=sys.stderr)
            print(f"  - client_id: {client_id}", file=sys.stderr)
            print(f"  - discovery_url: {discovery_url}", file=sys.stderr)
            print(f"  - bearer_token: {bearer_token[:20]}... (truncated)", file=sys.stderr)
            if runtime_arn:
                print(f"  - runtime_arn: {runtime_arn}", file=sys.stderr)
            if runtime_url:
                print(f"  - runtime_url: {runtime_url}", file=sys.stderr)
            break
    
    if not agent_found:
        print(f"WARNING: Agent {agent_name} not found in local tracking file", file=sys.stderr)
        print(f"Available agents in local file: {[a.get('name') for a in deployed_agents if isinstance(a, dict)]}", file=sys.stderr)
        print(f"Checking SSM Parameter Store...", file=sys.stderr)
        
        # Try to retrieve from SSM
        import subprocess
        ssm_param_name = f"/$STACK_PREFIX/agentcore_values/$UNIQUE_ID"
        try:
            result = subprocess.run(
                ['aws', 'ssm', 'get-parameter', '--name', ssm_param_name, '--with-decryption', '--query', 'Parameter.Value', '--output', 'text'],
                capture_output=True,
                text=True,
                check=True
            )
            ssm_data = json.loads(result.stdout.strip())
            print(f"Found AgentCore configuration in SSM: {ssm_param_name}", file=sys.stderr)
            
            # Look for agent in SSM data
            ssm_agents = ssm_data.get('agents', [])
            for ssm_agent in ssm_agents:
                if isinstance(ssm_agent, dict) and ssm_agent.get('name') == agent_name:
                    # Update SSM agent data
                    ssm_agent['protocol'] = 'A2A'
                    ssm_agent['bearer_token'] = bearer_token
                    ssm_agent['pool_id'] = pool_id
                    ssm_agent['client_id'] = client_id
                    ssm_agent['discovery_url'] = discovery_url
                    ssm_agent['a2a_deployment_timestamp'] = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
                    
                    if runtime_arn:
                        ssm_agent['runtime_arn'] = runtime_arn
                    if runtime_url:
                        ssm_agent['runtime_url'] = runtime_url
                    
                    agent_found = True
                    print(f"Updated agent in SSM data: {agent_name}", file=sys.stderr)
                    
                    # Write updated SSM data back to SSM
                    updated_ssm_json = json.dumps(ssm_data)
                    subprocess.run(
                        ['aws', 'ssm', 'put-parameter', '--name', ssm_param_name, '--value', updated_ssm_json, '--type', 'SecureString', '--overwrite'],
                        capture_output=True,
                        text=True,
                        check=True
                    )
                    print(f"Updated SSM parameter: {ssm_param_name}", file=sys.stderr)
                    
                    # Also update local tracking file with SSM data
                    data = ssm_data
                    with open(tracking_file, 'w') as f:
                        json.dump(data, f, indent=2)
                    print(f"Synced local tracking file with SSM data", file=sys.stderr)
                    break
            
            if not agent_found:
                print(f"ERROR: Agent {agent_name} not found in SSM either", file=sys.stderr)
                print(f"Available agents in SSM: {[a.get('name') for a in ssm_agents if isinstance(a, dict)]}", file=sys.stderr)
                sys.exit(1)
                
        except subprocess.CalledProcessError as e:
            print(f"ERROR: Failed to retrieve from SSM: {e.stderr}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"ERROR: Failed to process SSM data: {str(e)}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            sys.exit(1)
    
    # Write updated data back to file with proper formatting
    with open(tracking_file, 'w') as f:
        json.dump(data, f, indent=2)
    
    print("SUCCESS")
    sys.exit(0)
    
except Exception as e:
    print(f"ERROR: {str(e)}", file=sys.stderr)
    import traceback
    traceback.print_exc(file=sys.stderr)
    sys.exit(1)
EOF
)
    
    local exit_code=$?
    
    if [ $exit_code -eq 0 ] && [ "$update_result" = "SUCCESS" ]; then
        print_success "✅ A2A configuration stored successfully for: $agentcore_agent_name"
        print_status "   Configuration persisted to: $tracking_file"
        print_status "   Fields added:"
        print_status "     - protocol: A2A"
        print_status "     - bearer_token: ${BEARER_TOKEN:0:20}... (truncated)"
        print_status "     - pool_id: $POOL_ID"
        print_status "     - client_id: $CLIENT_ID"
        print_status "     - discovery_url: $DISCOVERY_URL"
        print_status "     - a2a_deployment_timestamp: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
        if [ -n "$runtime_arn" ]; then
            print_status "     - runtime_arn: $runtime_arn"
        fi
        if [ -n "$runtime_url" ]; then
            print_status "     - runtime_url: $runtime_url"
        fi
        return 0
    else
        print_error "❌ Failed to store A2A configuration for: $agentcore_agent_name"
        print_error "   Exit code: $exit_code"
        print_error "   Update result: $update_result"
        print_error "   Tracking file: $tracking_file"
        return 1
    fi
}

# Function to configure A2A protocol for an agent
# Executes agentcore configure command with A2A protocol setting
# Returns 0 on success, 1 on failure
prepare_a2a_handler() {
    local agent_dir="$1"
    local agent_name=$(basename "$agent_dir")
    
    print_status "Preparing A2A handler for agent: $agent_name (pre-deployment configuration)"
    
    # Verify agent directory exists
    if [ ! -d "$agent_dir" ]; then
        print_error "❌ Agent directory not found: $agent_dir"
        return 1
    fi
    
    # Verify handler.py exists in agent directory
    if [ ! -f "$agent_dir/handler.py" ]; then
        print_error "❌ handler.py not found in agent directory: $agent_dir"
        print_error "   A2A handler template must be deployed before configuring protocol"
        return 1
    fi
    
    # Change to agent directory for agentcore configure command
    cd "$agent_dir" || {
        print_error "❌ Failed to change to agent directory: $agent_dir"
        return 1
    }
    
    print_status "Executing: agentcore configure -e handler.py --protocol A2A"
    
    # Execute agentcore configure command and capture output
    local configure_output
    local configure_exit_code
    configure_output=$(agentcore configure -e handler.py --protocol A2A 2>&1)
    configure_exit_code=$?
    
    # Return to project root
    cd "$PROJECT_ROOT" || {
        print_error "❌ Failed to return to project root directory"
        return 1
    }
    
    # Check if command succeeded
    if [ $configure_exit_code -eq 0 ]; then
        print_success "✅ A2A handler prepared successfully for agent: $agent_name"
        return 0
    else
        print_error "❌ Failed to prepare A2A handler for agent: $agent_name"
        print_error "AgentCore CLI exited with code: $configure_exit_code"
        print_error "AgentCore CLI output:"
        echo "$configure_output" >&2
        print_error ""
        print_error "Troubleshooting tips:"
        print_error "  1. Verify agentcore CLI is installed and in PATH"
        print_error "  2. Check that handler.py exists in agent directory"
        print_error "  3. Ensure handler.py is a valid A2A handler template"
        print_error "  4. Review AgentCore CLI documentation for handler preparation"
        return 1
    fi
}

# Function to generate bearer token for A2A authentication
# Calls setup_bearer_token.sh and parses JSON output
# Returns 0 on success, 1 on failure
# Outputs: Sets global variables BEARER_TOKEN, POOL_ID, CLIENT_ID, DISCOVERY_URL
generate_bearer_token() {
    local agent_name="$1"
    
    print_status "Generating bearer token for A2A agent: $agent_name"
    
    # Define path to bearer token setup script
    local bearer_token_script="${PROJECT_ROOT}/agentcore/deployment/setup_bearer_token.sh"
    
    # Verify script exists
    if [ ! -f "$bearer_token_script" ]; then
        print_error "❌ Bearer token setup script not found at: $bearer_token_script"
        print_error "   Expected location: agentcore/deployment/setup_bearer_token.sh"
        print_error "   Please ensure the bearer token setup script exists before deploying A2A agents."
        return 1
    fi
    
    # Get demo user credentials from infrastructure stack or use defaults
    local demo_username="${DEMO_USER_EMAIL:-user@example.com}"
    local demo_password="demoUser123!"
    
    # Build command with appropriate parameters
    local bearer_cmd="$bearer_token_script"
    bearer_cmd="$bearer_cmd --region $AWS_REGION"
    bearer_cmd="$bearer_cmd --stack-prefix $STACK_PREFIX"
    bearer_cmd="$bearer_cmd --unique-id $UNIQUE_ID"
    
    # Add AWS profile if specified
    if [ -n "$AWS_PROFILE" ]; then
        bearer_cmd="$bearer_cmd --profile $AWS_PROFILE"
    fi
    
    print_status "Executing bearer token setup script..."
    print_status "  Stack: ${STACK_PREFIX}-infrastructure-core"
    print_status "  Region: $AWS_REGION"
    print_status "  Username: $demo_username"
    
    # Execute the bearer token setup script and capture output
    # Note: Only capture stdout (JSON), let stderr (debug messages) pass through
    local bearer_output
    local bearer_exit_code
    bearer_output=$(eval "$bearer_cmd")
    bearer_exit_code=$?
    
    # Check if command succeeded
    if [ $bearer_exit_code -ne 0 ]; then
        print_error "❌ Failed to generate bearer token for agent: $agent_name"
        print_error "Bearer token setup script exited with code: $bearer_exit_code"
        print_error "Script output:"
        echo "$bearer_output" >&2
        return 1
    fi
    
    # Extract JSON from output (should be the complete JSON object)
    # The bearer token script outputs only JSON to stdout, all other messages go to stderr
    local json_output="$bearer_output"
    
    # Verify JSON is valid
    if ! echo "$json_output" | jq empty 2>/dev/null; then
        print_error "❌ Failed to parse bearer token JSON output"
        print_error "Expected valid JSON, got:"
        echo "$json_output" >&2
        return 1
    fi
    
    # Parse JSON output and set global variables
    export BEARER_TOKEN=$(echo "$json_output" | jq -r '.bearer_token')
    export POOL_ID=$(echo "$json_output" | jq -r '.pool_id')
    export CLIENT_ID=$(echo "$json_output" | jq -r '.client_id')
    export DISCOVERY_URL=$(echo "$json_output" | jq -r '.discovery_url')
    
    # Verify all required values were extracted
    if [[ -z "$BEARER_TOKEN" || "$BEARER_TOKEN" == "null" ]]; then
        print_error "❌ Failed to extract bearer_token from JSON output"
        return 1
    fi
    
    if [[ -z "$POOL_ID" || "$POOL_ID" == "null" ]]; then
        print_error "❌ Failed to extract pool_id from JSON output"
        return 1
    fi
    
    if [[ -z "$CLIENT_ID" || "$CLIENT_ID" == "null" ]]; then
        print_error "❌ Failed to extract client_id from JSON output"
        return 1
    fi
    
    if [[ -z "$DISCOVERY_URL" || "$DISCOVERY_URL" == "null" ]]; then
        print_error "❌ Failed to extract discovery_url from JSON output"
        return 1
    fi
    
    print_success "✅ Bearer token generated successfully for agent: $agent_name"
    print_status "   Pool ID: $POOL_ID"
    print_status "   Client ID: $CLIENT_ID"
    print_status "   Discovery URL: $DISCOVERY_URL"
    print_status "   Bearer token: ${BEARER_TOKEN:0:20}... (truncated)"
    
    return 0
}

# Function to check for resource conflicts before deployment
check_resource_conflicts() {
    local stack_name="$1"
    local template_file="$2"
    
    print_status "Checking for resource conflicts for stack: $stack_name"
    
    # If stack already exists, we're doing an update, so skip conflict checks
    if stack_exists "$stack_name"; then
        print_status "Stack exists - will attempt update"
        return 0
    fi
    
    # Extract resource names from template and check for conflicts
    # This is a basic implementation - could be enhanced with more sophisticated parsing
    local conflicts_found=false
    
    # Check for common IAM role naming patterns that might conflict
    if [[ "$template_file" == *"knowledge-base"* ]] || [[ "$template_file" == *"agent"* ]]; then
        # Extract potential role names from stack name pattern
        local base_name=$(echo "$stack_name" | sed "s/${STACK_PREFIX}-//g" | sed "s/-${UNIQUE_ID}//g")
        local potential_role_names=(
            "${STACK_PREFIX}-KB-${base_name}-Role-${UNIQUE_ID}"
            "${STACK_PREFIX}-${base_name}-Role-${UNIQUE_ID}"
            "BedrockExecutionRole-${STACK_PREFIX}-${base_name}-${UNIQUE_ID}"
        )
        
        for role_name in "${potential_role_names[@]}"; do
            if iam_role_exists "$role_name"; then
                print_warning "⚠️  IAM Role conflict detected: $role_name already exists"
                print_warning "This may cause deployment failure. Consider using a different UNIQUE_ID or cleaning up existing resources."
                conflicts_found=true
            fi
        done
    fi
    
    if [ "$conflicts_found" = true ]; then
        print_warning "Resource conflicts detected. Deployment may fail."
        if [ "$INTERACTIVE_MODE" = true ]; then
            printf "Continue with deployment anyway? (y/N): "
            read -r response
            if [[ ! "$response" =~ ^[Yy]$ ]]; then
                print_error "Deployment cancelled due to resource conflicts"
                exit 1
            fi
        fi
    fi
    
    return 0
}

# Function to wait for stack operation to complete
wait_for_stack() {
    local stack_name=$1
    local operation=$2
    
    print_status "Waiting for stack $stack_name to $operation..."
    
    aws_cmd cloudformation wait "stack-${operation}-complete" \
        --stack-name "$stack_name" \
        --region "$AWS_REGION"
    
    if [ $? -eq 0 ]; then
        print_status "Stack $stack_name ${operation} completed successfully"
    else
        local stack_status=$(aws_cmd cloudformation describe-stacks --stack-name "$stack_name" --region "$AWS_REGION" --query 'Stacks[0].StackStatus' --output text 2>/dev/null)
        print_error "Stack $stack_name ${operation} failed with status: $stack_status"
        print_error "Check the CloudFormation console for detailed error information:"
        print_error "https://${AWS_REGION}.console.aws.amazon.com/cloudformation/home?region=${AWS_REGION}#/stacks"
        exit 1
    fi
}

# Function to deploy a CloudFormation stack
deploy_stack() {
    local stack_name="$1"
    local template_file="$2"
    local parameters="$3"
    
    print_status "Deploying CloudFormation stack: $stack_name"
    print_status "Template: $template_file"
    
    # Check for resource conflicts before deployment
    check_resource_conflicts "$stack_name" "$template_file"
    
    # Check and handle problematic stack states
    if ! check_and_handle_stack_state "$stack_name" "deploy"; then
        print_error "Failed to handle stack state for: $stack_name"
        return 1
    fi
    
    # Check if stack exists
    if stack_exists "$stack_name"; then
        local stack_status=$(get_stack_status "$stack_name")
        print_status "Stack $stack_name already exists with status: $stack_status. Updating..."
        local operation="update"
        
        local update_cmd="aws_cmd cloudformation update-stack --stack-name $stack_name --template-body file://$template_file --region $AWS_REGION --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM"
        
        if [ -n "$parameters" ]; then
            update_cmd="$update_cmd $parameters"
        fi
        
        # Capture the output and error from the update command
        local update_output
        local update_exit_code
        update_output=$(eval "$update_cmd" 2>&1)
        update_exit_code=$?
        
        if [ $update_exit_code -eq 0 ]; then
            wait_for_stack "$stack_name" "$operation"
            print_success "✅ Stack $stack_name updated successfully"
            return 0
        else
            # Check if the error is "No updates are to be performed"
            if echo "$update_output" | grep -q "No updates are to be performed"; then
                print_status "✅ Stack $stack_name is already up to date"
                return 0
            fi
            print_error "❌ Failed to update stack $stack_name"
            print_error "Error: $update_output"
            return $update_exit_code
        fi
    else
        print_status "Stack $stack_name does not exist. Creating..."
        local operation="create"
        
        local create_cmd="aws_cmd cloudformation create-stack --stack-name $stack_name --template-body file://$template_file --region $AWS_REGION --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM"
        
        if [ -n "$parameters" ]; then
            create_cmd="$create_cmd $parameters"
        fi
        
        # Capture the output and error from the create command
        local create_output
        local create_exit_code
        create_output=$(eval "$create_cmd" 2>&1)
        create_exit_code=$?
        
        if [ $create_exit_code -eq 0 ]; then
            wait_for_stack "$stack_name" "$operation"
            print_success "✅ Stack $stack_name created successfully"
            return 0
        else
            print_error "❌ Failed to create stack $stack_name"
            print_error "Error: $create_output"
            return $create_exit_code
        fi
    fi
}

# Function to get CloudFormation stack output
get_stack_output() {
    local stack_name="$1"
    local output_key="$2"
    
    aws_cmd cloudformation describe-stacks \
        --stack-name "$stack_name" \
        --region "$AWS_REGION" \
        --query "Stacks[0].Outputs[?OutputKey=='$output_key'].OutputValue" \
        --output text 2>/dev/null || echo ""
}

# Function to orchestrate A2A agent deployment
# This function coordinates all A2A-specific deployment steps in the correct sequence
# Returns 0 on success, 1 on failure
deploy_a2a_agent() {
    local agent_name="$1"
    local agent_dir="$2"
    local agentcore_agent_name="$3"
    
    print_status "🔗 Starting A2A deployment orchestration for: $agent_name"
    print_status "   Agent directory: $agent_dir"
    print_status "   AgentCore name: $agentcore_agent_name"
    
    # Step 1: Detect A2A configuration
    local agent_config_file="${agent_dir}/config.json"
    if ! is_a2a_agent "$agent_config_file"; then
        print_error "❌ Agent is not configured for A2A deployment"
        print_error "   Expected: use_handler_template=true AND protocol=A2A in config.json"
        return 1
    fi
    print_status "✅ [1/6] A2A configuration detected"
    
    # Step 2: Generate bearer token
    if ! generate_bearer_token "$agent_name"; then
        print_error "❌ [2/6] Failed to generate bearer token"
        print_error "   A2A deployment cannot proceed without authentication token"
        return 1
    fi
    print_status "✅ [2/6] Bearer token generated successfully"
    
    # Step 3: Copy A2A handler template
    if ! deploy_a2a_handler "$agent_dir"; then
        print_error "❌ [3/6] Failed to deploy A2A handler template"
        print_error "   A2A deployment cannot proceed without handler template"
        return 1
    fi
    print_status "✅ [3/6] A2A handler template deployed"
    
    # Step 4: Prepare A2A handler (local file configuration before deployment)
    # SKIP: The A2A handler template is already pre-configured for A2A protocol
    # Running 'agentcore configure' is not necessary and can hang
    print_status "⏭️  [4/6] Skipping A2A handler preparation (template already configured)"
    print_status "   A2A handler template is pre-configured with protocol=A2A"
    
    # Step 5: Deploy runtime (handled by build_and_deploy.sh)
    # Export A2A configuration for runtime deployment
    export A2A_BEARER_TOKEN="$BEARER_TOKEN"
    export A2A_POOL_ID="$POOL_ID"
    export A2A_CLIENT_ID="$CLIENT_ID"
    export A2A_DISCOVERY_URL="$DISCOVERY_URL"
    export A2A_PROTOCOL="A2A"
    
    print_status "✅ [5/6] A2A environment variables exported for runtime deployment"
    print_status "   Runtime deployment will be handled by build_and_deploy.sh"
    
    # Step 6: Configuration persistence will be handled after runtime deployment
    print_status "✅ [6/6] A2A orchestration complete - ready for runtime deployment"
    
    # Report A2A deployment status
    print_success "🎉 A2A deployment orchestration completed successfully for: $agent_name"
    print_status "   Next step: Runtime deployment via build_and_deploy.sh"
    print_status "   A2A Configuration:"
    print_status "     - Protocol: A2A"
    print_status "     - Pool ID: $POOL_ID"
    print_status "     - Client ID: $CLIENT_ID"
    print_status "     - Discovery URL: $DISCOVERY_URL"
    print_status "     - Bearer Token: ${BEARER_TOKEN:0:20}... (truncated)"
    
    return 0
}

# Function to construct runtime URL from stored ARN
# Resolves A2A agent runtime URL from SSM Parameter Store
# Returns runtime URL on success, empty string on failure
get_a2a_runtime_url() {
    local agentcore_agent_name="$1"
    local bearer_token="${2:-}"
    
    print_status "Resolving runtime URL for A2A agent: $agentcore_agent_name"
    
    # Retrieve AgentCore configuration from SSM Parameter Store
    local parameter_name="/${STACK_PREFIX}/agentcore_values/${UNIQUE_ID}"
    
    print_status "Retrieving configuration from SSM: $parameter_name"
    
    local ssm_config
    ssm_config=$(aws ssm get-parameter \
        --name "$parameter_name" \
        --with-decryption \
        --region "$AWS_REGION" \
        --query 'Parameter.Value' \
        --output text 2>/dev/null)
    
    if [ -z "$ssm_config" ]; then
        print_error "❌ Failed to retrieve AgentCore configuration from SSM"
        print_error "   Parameter: $parameter_name"
        return 1
    fi
    
    # Extract runtime ARN for the specific agent
    local runtime_arn
    runtime_arn=$(echo "$ssm_config" | jq -r ".agents[] | select(.name == \"$agentcore_agent_name\") | .runtime_arn" 2>/dev/null)
    
    if [[ -z "$runtime_arn" || "$runtime_arn" == "null" ]]; then
        print_error "❌ Runtime ARN not found for agent: $agentcore_agent_name"
        print_error "   Agent may not be deployed or SSM configuration is incomplete"
        return 1
    fi
    
    print_status "Found runtime ARN: $runtime_arn"
    
    # Method 1: Construct runtime URL from ARN
    print_status "Attempting Method 1: Construct URL from ARN..."
    
    # Extract region from ARN (format: arn:aws:bedrock-agentcore:REGION:ACCOUNT:runtime/RUNTIME_ID)
    local region
    region=$(echo "$runtime_arn" | cut -d':' -f4)
    
    if [[ -z "$region" ]]; then
        print_warning "⚠️  Could not extract region from ARN, using default: $AWS_REGION"
        region="$AWS_REGION"
    fi
    
    # URL-encode the runtime ARN
    local encoded_arn
    encoded_arn=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$runtime_arn', safe=''))")
    
    if [[ -z "$encoded_arn" ]]; then
        print_error "❌ Failed to URL-encode runtime ARN"
    else
        # Construct runtime URL
        local runtime_url="https://bedrock-agentcore.${region}.amazonaws.com/runtimes/${encoded_arn}/invocations"
        
        print_success "✅ Method 1 succeeded: Constructed runtime URL from ARN"
        print_status "   Runtime URL: $runtime_url"
        echo "$runtime_url"
        return 0
    fi
    
    # Method 2: Fallback to fetch_agent_card.py utility
    print_status "Attempting Method 2: Fallback to fetch_agent_card.py..."
    
    # Verify bearer token is available
    if [[ -z "$bearer_token" || "$bearer_token" == "null" ]]; then
        print_warning "⚠️  Bearer token not provided for fallback method"
        print_warning "   Attempting to retrieve from tracking file..."
        
        bearer_token=$(jq -r ".deployed_agents[] | select(.name == \"$agentcore_agent_name\") | .bearer_token" "$tracking_file" 2>/dev/null)
        
        if [[ -z "$bearer_token" || "$bearer_token" == "null" ]]; then
            print_error "❌ Bearer token not found in tracking file"
            print_error "   Cannot use fallback method without bearer token"
            return 1
        fi
    fi
    
    # Path to fetch_agent_card.py utility
    local fetch_agent_card_script="${PROJECT_ROOT}/agentcore/shared/fetch_agent_card.py"
    
    if [ ! -f "$fetch_agent_card_script" ]; then
        print_error "❌ fetch_agent_card.py utility not found at: $fetch_agent_card_script"
        print_error "   Expected location: agentcore/shared/fetch_agent_card.py"
        print_error "   Both resolution methods failed"
        return 1
    fi
    
    # Setup Python environment
    setup_python_environment
    
    # Generate a session ID for the request
    local session_id
    session_id=$($PYTHON_CMD -c "import uuid; print(str(uuid.uuid4()))")
    
    print_status "Calling fetch_agent_card.py with:"
    print_status "  Runtime ARN: $runtime_arn"
    print_status "  Session ID: $session_id"
    print_status "  Bearer token: ${bearer_token:0:20}... (truncated)"
    
    # Call fetch_agent_card.py and capture output
    local agent_card_output
    local fetch_exit_code
    agent_card_output=$($PYTHON_CMD "$fetch_agent_card_script" "$runtime_arn" "$bearer_token" "$session_id" 2>&1)
    fetch_exit_code=$?
    
    if [ $fetch_exit_code -ne 0 ]; then
        print_error "❌ Method 2 failed: fetch_agent_card.py exited with code: $fetch_exit_code"
        print_error "Output:"
        echo "$agent_card_output" >&2
        print_error ""
        print_error "Both resolution methods failed for agent: $agentcore_agent_name"
        return 1
    fi
    
    # Parse agent card JSON to extract runtime URL
    local runtime_url_from_card
    runtime_url_from_card=$(echo "$agent_card_output" | jq -r '.runtime_url // .endpoint // empty' 2>/dev/null)
    
    if [[ -n "$runtime_url_from_card" && "$runtime_url_from_card" != "null" ]]; then
        print_success "✅ Method 2 succeeded: Retrieved runtime URL from agent card"
        print_status "   Runtime URL: $runtime_url_from_card"
        echo "$runtime_url_from_card"
        return 0
    else
        print_error "❌ Method 2 failed: Could not extract runtime URL from agent card"
        print_error "Agent card output:"
        echo "$agent_card_output" >&2
        print_error ""
        print_error "Both resolution methods failed for agent: $agentcore_agent_name"
        return 1
    fi
}

# Function to check and handle stacks in problematic states before deployment
check_and_handle_stack_state() {
    local stack_name="$1"
    local operation="${2:-deploy}"  # deploy, update, or create
    
    local stack_status=$(get_stack_status "$stack_name")
    
    case "$stack_status" in
        "ROLLBACK_COMPLETE"|"CREATE_FAILED"|"ROLLBACK_FAILED"|"DELETE_FAILED")
            print_warning "Stack $stack_name is in $stack_status state"
            
            if [ "$operation" = "deploy" ] || [ "$operation" = "create" ]; then
                print_status "Deleting stack $stack_name before recreation..."
                
                # Use force delete for DELETE_FAILED stacks
                local delete_cmd="aws_cmd cloudformation delete-stack --stack-name $stack_name --region $AWS_REGION"
                if [ "$stack_status" = "DELETE_FAILED" ]; then
                    delete_cmd="$delete_cmd --deletion-mode FORCE_DELETE_STACK"
                    print_status "Using FORCE_DELETE_STACK mode for DELETE_FAILED stack"
                fi
                
                eval "$delete_cmd"
                
                print_status "Waiting for stack deletion to complete..."
                if aws_cmd cloudformation wait stack-delete-complete --stack-name "$stack_name" --region "$AWS_REGION"; then
                    print_success "✅ Stack $stack_name deleted successfully"
                    return 0
                else
                    print_error "❌ Failed to delete stack $stack_name"
                    return 1
                fi
            fi
            ;;
        "DELETE_IN_PROGRESS"|"CREATE_IN_PROGRESS"|"UPDATE_IN_PROGRESS")
            print_warning "Stack $stack_name is currently in progress ($stack_status)"
            print_status "Waiting for current operation to complete..."
            
            # Wait for the current operation to complete
            case "$stack_status" in
                "DELETE_IN_PROGRESS")
                    aws_cmd cloudformation wait stack-delete-complete --stack-name "$stack_name" --region "$AWS_REGION"
                    ;;
                "CREATE_IN_PROGRESS")
                    aws_cmd cloudformation wait stack-create-complete --stack-name "$stack_name" --region "$AWS_REGION"
                    ;;
                "UPDATE_IN_PROGRESS")
                    aws_cmd cloudformation wait stack-update-complete --stack-name "$stack_name" --region "$AWS_REGION"
                    ;;
            esac
            
            return $?
            ;;
        "DOES_NOT_EXIST")
            # Stack doesn't exist, which is fine for create operations
            return 0
            ;;
        "CREATE_COMPLETE"|"UPDATE_COMPLETE")
            # Stack is in a good state
            return 0
            ;;
        *)
            print_warning "Stack $stack_name is in unexpected state: $stack_status"
            return 0
            ;;
    esac
}

# Function to initialize or load unique ID
initialize_unique_id() {
    # If UNIQUE_ID was provided via command line, use it and save to file
    if [ -n "$UNIQUE_ID" ]; then
        # Validate the provided unique ID format
        if [[ "$UNIQUE_ID" =~ ^[a-z0-9]{6}$ ]]; then
            local id_file="${PROJECT_ROOT}/.unique-id-${STACK_PREFIX}-${AWS_REGION}"
            echo "$UNIQUE_ID" > "$id_file"
            print_status "Using provided unique ID: $UNIQUE_ID"
        else
            print_error "Invalid unique ID format. Must be 6 characters (lowercase letters and numbers only)"
            exit 1
        fi
    else
        local id_file="${PROJECT_ROOT}/.unique-id-${STACK_PREFIX}-${AWS_REGION}"
        
        if [ "$CLEAN_DEPLOYMENT" = true ]; then
            # Generate new unique ID for clean deployment
            setup_python_environment
            UNIQUE_ID=$($PYTHON_CMD -c "import random, string; print(''.join(random.choices(string.ascii_lowercase + string.digits, k=6)))")
            echo "$UNIQUE_ID" > "$id_file"
            print_status "Generated new unique ID for clean deployment: $UNIQUE_ID"
        else
            # Try to load existing unique ID
            if [ -f "$id_file" ]; then
                UNIQUE_ID=$(cat "$id_file" 2>/dev/null)
                if [ -n "$UNIQUE_ID" ] && [[ "$UNIQUE_ID" =~ ^[a-z0-9]{6}$ ]]; then
                    print_status "Loaded existing unique ID: $UNIQUE_ID"
                else
                    print_warning "Invalid unique ID in file, generating new one"
                    setup_python_environment
                    UNIQUE_ID=$($PYTHON_CMD -c "import random, string; print(''.join(random.choices(string.ascii_lowercase + string.digits, k=6)))")
                    echo "$UNIQUE_ID" > "$id_file"
                    print_status "Generated new unique ID: $UNIQUE_ID"
                fi
            else
                print_warning "No existing unique ID found, generating new one"
                setup_python_environment
                UNIQUE_ID=$($PYTHON_CMD -c "import random, string; print(''.join(random.choices(string.ascii_lowercase + string.digits, k=6)))")
                echo "$UNIQUE_ID" > "$id_file"
                print_status "Generated new unique ID: $UNIQUE_ID"
            fi
        fi
    fi
    
    # Validate that the unique ID will create unique resource names
    validate_unique_id
}


# Function to validate unique ID doesn't conflict with existing resources
validate_unique_id() {
    print_status "Validating unique ID for resource conflicts..."
    
    # Check for potential IAM role conflicts
    local test_role_name="BedrockExecutionRole-${STACK_PREFIX}-test-${UNIQUE_ID}"
    if iam_role_exists "$test_role_name"; then
        print_warning "⚠️  Potential IAM role naming conflict detected with unique ID: $UNIQUE_ID"
        
        if [ "$INTERACTIVE_MODE" = true ]; then
            printf "Generate a new unique ID to avoid conflicts? (Y/n): "
            read -r response
            if [[ ! "$response" =~ ^[Nn]$ ]]; then
                # Generate new unique ID
                setup_python_environment
                UNIQUE_ID=$($PYTHON_CMD -c "import random, string; print(''.join(random.choices(string.ascii_lowercase + string.digits, k=6)))")
                local id_file="${PROJECT_ROOT}/.unique-id-${STACK_PREFIX}-${AWS_REGION}"
                echo "$UNIQUE_ID" > "$id_file"
                print_status "Generated new unique ID to avoid conflicts: $UNIQUE_ID"
                
                # Recursively validate the new ID (with a depth limit)
                local validation_depth="${VALIDATION_DEPTH:-0}"
                if [ "$validation_depth" -lt 3 ]; then
                    export VALIDATION_DEPTH=$((validation_depth + 1))
                    validate_unique_id
                    unset VALIDATION_DEPTH
                fi
            fi
        fi
    fi
}

# Function to validate deployment readiness
validate_deployment_readiness() {
    print_step "Step 0: Validating deployment readiness..."
    
    local validation_errors=0
    
    # Check if required files exist
    local required_files=(
        "${PROJECT_ROOT}/cloudformation/infrastructure-core.yml"
        "${PROJECT_ROOT}/cloudformation/infrastructure-services.yml"
        "${PROJECT_ROOT}/cloudformation/generic-configs/knowledgebases/knowledgebases-with-datasources.json"
    )
    
    for file in "${required_files[@]}"; do
        if [ ! -f "$file" ]; then
            print_error "Required file not found: $file"
            validation_errors=$((validation_errors + 1))
        fi
    done
    
    # Check for existing stacks that might conflict
    print_status "Checking for existing CloudFormation stacks..."
    local existing_stacks=$(aws_cmd cloudformation list-stacks \
        --region "$AWS_REGION" \
        --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE UPDATE_ROLLBACK_COMPLETE DELETE_FAILED \
        --query "StackSummaries[?contains(StackName, '${STACK_PREFIX}-')].StackName" \
        --output text 2>/dev/null)
    
    if [ -n "$existing_stacks" ] && [ "$existing_stacks" != "None" ]; then
        print_warning "Found existing stacks with prefix '${STACK_PREFIX}-':"
        for stack in $existing_stacks; do
            print_warning "  - $stack"
        done
        
        if [ "$CLEAN_DEPLOYMENT" = true ]; then
            print_warning "⚠️  Clean deployment requested but existing stacks found!"
            print_warning "This may cause resource conflicts. Consider:"
            print_warning "  1. Using --cleanup to remove existing resources first"
            print_warning "  2. Using a different --stack-prefix"
            print_warning "  3. Setting CLEAN_DEPLOYMENT=false to update existing resources"
            
            if [ "$INTERACTIVE_MODE" = true ]; then
                printf "Continue with deployment despite existing stacks? (y/N): "
                read -r response
                if [[ ! "$response" =~ ^[Yy]$ ]]; then
                    print_error "Deployment cancelled due to existing stack conflicts"
                    exit 1
                fi
            fi
        fi
    fi
    
    # Check AWS credentials and permissions
    print_status "Validating AWS credentials and permissions..."
    if ! aws_cmd sts get-caller-identity --region "$AWS_REGION" > /dev/null 2>&1; then
        print_error "AWS credentials not configured or invalid"
        validation_errors=$((validation_errors + 1))
    fi
    
    # Check Bedrock service availability in region
    if ! aws_cmd bedrock list-foundation-models --region "$AWS_REGION" > /dev/null 2>&1; then
        print_warning "⚠️  Bedrock service may not be available in region $AWS_REGION"
        print_warning "Please ensure Bedrock is enabled in your AWS account and region"
    fi
    
    if [ $validation_errors -gt 0 ]; then
        print_error "Validation failed with $validation_errors errors. Please fix the issues above before proceeding."
        exit 1
    fi
    
    print_success "✅ Deployment readiness validation passed"
}

# Function to check service quotas and auto-adjust
check_and_adjust_service_quotas() {
    print_step "Step 1: Checking and adjusting AWS service quotas..."
    
    # Check Bedrock Agent Knowledge Bases per Agent quota (L-13143995)
    print_status "Checking Bedrock Agent quotas..."
    
    local quota_check=$(aws_cmd service-quotas get-service-quota \
        --service-code bedrock \
        --quota-code L-13143995 \
        --region "$AWS_REGION" 2>/dev/null || echo "")
    
    if [ -n "$quota_check" ] && echo "$quota_check" | jq -e '.Quota.Value' >/dev/null 2>&1; then
        local current_quota=$(echo "$quota_check" | jq -r '.Quota.Value' 2>/dev/null)
        local required_quota=5  # We need up to 5 knowledge bases per agent
        
        if [ $(echo "$current_quota < $required_quota" | bc -l 2>/dev/null || echo "1") -eq 1 ]; then
            print_warning "Current quota ($current_quota) is below required ($required_quota). Requesting increase..."
            
            # Check for existing requests
            local existing_request=$(aws_cmd service-quotas list-requested-service-quota-change-history \
                --service-code bedrock \
                --region "$AWS_REGION" \
                --query "RequestedQuotas[?QuotaCode=='L-13143995' && Status=='PENDING'].RequestId" \
                --output text 2>/dev/null || echo "")
            
            if [ -z "$existing_request" ] || [ "$existing_request" = "None" ]; then
                local request_result=$(aws_cmd service-quotas request-service-quota-increase \
                    --service-code bedrock \
                    --quota-code L-13143995 \
                    --desired-value $required_quota \
                    --region "$AWS_REGION" 2>&1 || echo "ERROR")
                
                if [ "$request_result" != "ERROR" ]; then
                    local request_id=$(echo "$request_result" | jq -r '.RequestedQuota.Id' 2>/dev/null || echo "")
                    print_success "✅ Quota increase request submitted: $request_id"
                    print_warning "⚠️  Deployment may fail if quota is not approved quickly"
                else
                    print_warning "⚠️  Could not request quota increase automatically"
                fi
            else
                print_status "Existing quota increase request found: $existing_request"
            fi
        else
            print_success "✅ Knowledge bases per agent quota is sufficient: $current_quota"
        fi
    else
        print_warning "Could not retrieve service quota information"
    fi
}

# Function to prompt for demo user email
prompt_for_demo_email() {
    if [ -z "$DEMO_USER_EMAIL" ]; then
        while true; do
            printf "Enter email address for demo user login: "
            read -r email_input
            if [[ "$email_input" =~ ^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$ ]]; then
                DEMO_USER_EMAIL="$email_input"
                break
            else
                print_error "Please enter a valid email address"
            fi
        done
    fi
    
    print_status "Demo user email: $DEMO_USER_EMAIL"
}

# Function to create AppSync Events API using AWS CLI
create_appsync_events_api() {
    local api_name="${STACK_PREFIX}-realtime-api-${UNIQUE_ID}"
    local channel_namespace="${STACK_PREFIX}-sessions-${UNIQUE_ID}"
    local ssm_param_name="/${STACK_PREFIX}/appsync/${UNIQUE_ID}"
    local appsync_file="${PROJECT_ROOT}/.appsync-${STACK_PREFIX}-${UNIQUE_ID}.json"
    
    print_status "Checking for existing AppSync Events API: $api_name"
    
    # Check if API already exists by name using AWS CLI
    local existing_api_id
    existing_api_id=$(aws_cmd appsync list-apis --region "$AWS_REGION" --query "apis[?name=='$api_name'].apiId" --output text 2>/dev/null || echo "")
    
    if [ -n "$existing_api_id" ] && [ "$existing_api_id" != "None" ]; then
        print_success "✅ AppSync Events API already exists"
        local api_id="$existing_api_id"
        local api_arn=$(aws_cmd appsync get-api --api-id "$api_id" --region "$AWS_REGION" --query 'api.apiArn' --output text)
        local http_endpoint="https://${api_id}.appsync-api.${AWS_REGION}.amazonaws.com/event"
        local realtime_endpoint="wss://${api_id}.appsync-realtime-api.${AWS_REGION}.amazonaws.com/event/realtime"
        
        print_status "   API ID: $api_id"
        print_status "   HTTP Endpoint: $http_endpoint"
        print_status "   Realtime Endpoint: $realtime_endpoint"
        
        # Store in SSM and local file
        local ssm_value=$(cat << EOJSON
{
  "apiId": "$api_id",
  "apiArn": "$api_arn",
  "httpEndpoint": "$http_endpoint",
  "realtimeEndpoint": "$realtime_endpoint",
  "channelNamespace": "$channel_namespace",
  "region": "$AWS_REGION",
  "createdAt": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
}
EOJSON
)
        aws_cmd ssm put-parameter --name "$ssm_param_name" --value "$ssm_value" --type "String" --overwrite --region "$AWS_REGION" > /dev/null 2>&1
        echo "$ssm_value" > "$appsync_file"
        return 0
    fi
    
    print_status "Creating new AppSync Events API: $api_name"
    
    local infrastructure_core_stack="${STACK_PREFIX}-infrastructure-core"
    local user_pool_id=$(get_stack_output "$infrastructure_core_stack" "UserPoolId")
    
    if [ -z "$user_pool_id" ] || [ "$user_pool_id" = "None" ]; then
        print_error "Could not retrieve User Pool ID from infrastructure stack"
        return 1
    fi
    
    print_status "API Name: $api_name"
    print_status "Channel Namespace: $channel_namespace"
    print_status "User Pool ID: $user_pool_id"
    
    # Setup Python environment for API creation
    setup_python_environment
    
    # Use Python with boto3 to create the Events API
    local api_result
    api_result=$(AWS_PROFILE="$AWS_PROFILE" AWS_REGION="$AWS_REGION" APPSYNC_API_NAME="$api_name" APPSYNC_CHANNEL_NS="$channel_namespace" APPSYNC_USER_POOL="$user_pool_id" $PYTHON_CMD << 'EOF'
import boto3
import json
import sys
import os

try:
    profile = os.environ.get('AWS_PROFILE', '')
    region = os.environ.get('AWS_REGION', 'us-east-1')
    api_name = os.environ.get('APPSYNC_API_NAME', '')
    channel_namespace = os.environ.get('APPSYNC_CHANNEL_NS', '')
    user_pool_id = os.environ.get('APPSYNC_USER_POOL', '')
    
    if profile:
        session = boto3.Session(profile_name=profile)
    else:
        session = boto3.Session()
    
    appsync = session.client('appsync', region_name=region)
    
    print(f"Creating API with name: {api_name}", file=sys.stderr)
    
    # Create Events API
    response = appsync.create_api(
        name=api_name,
        eventConfig={
            'authProviders': [
                {'authType': 'AWS_IAM'},
                {
                    'authType': 'AMAZON_COGNITO_USER_POOLS',
                    'cognitoConfig': {
                        'userPoolId': user_pool_id,
                        'awsRegion': region
                    }
                }
            ],
            'connectionAuthModes': [
                {'authType': 'AWS_IAM'},
                {'authType': 'AMAZON_COGNITO_USER_POOLS'}
            ],
            'defaultPublishAuthModes': [{'authType': 'AWS_IAM'}],
            'defaultSubscribeAuthModes': [
                {'authType': 'AWS_IAM'},
                {'authType': 'AMAZON_COGNITO_USER_POOLS'}
            ]
        }
    )
    
    api_id = response['api']['apiId']
    api_arn = response['api']['apiArn']
    
    print(f"API_ID={api_id}", file=sys.stderr)
    print(f"API_ARN={api_arn}", file=sys.stderr)
    
    # Create channel namespace
    appsync.create_channel_namespace(
        apiId=api_id,
        name=channel_namespace
    )
    
    print(f"CHANNEL_CREATED=true", file=sys.stderr)
    
    # Output JSON result
    result = {
        'apiId': api_id,
        'apiArn': api_arn,
        'channelNamespace': channel_namespace,
        'httpEndpoint': f"https://{api_id}.appsync-api.{region}.amazonaws.com/event",
        'realtimeEndpoint': f"wss://{api_id}.appsync-realtime-api.{region}.amazonaws.com/event/realtime"
    }
    
    print(json.dumps(result))
    sys.exit(0)
    
except Exception as e:
    import traceback
    print(f"ERROR: {str(e)}", file=sys.stderr)
    print(f"TRACEBACK: {traceback.format_exc()}", file=sys.stderr)
    sys.exit(1)
EOF
)
    
    local exit_code=$?
    
    if [ $exit_code -ne 0 ]; then
        print_error "Failed to create AppSync Events API"
        print_error "Error: $api_result"
        return 1
    fi
    
    # Parse the JSON result
    local api_id=$(echo "$api_result" | jq -r '.apiId')
    local api_arn=$(echo "$api_result" | jq -r '.apiArn')
    local http_endpoint=$(echo "$api_result" | jq -r '.httpEndpoint')
    local realtime_endpoint=$(echo "$api_result" | jq -r '.realtimeEndpoint')
    
    print_success "✅ AppSync Events API created successfully"
    print_status "   API ID: $api_id"
    print_status "   HTTP Endpoint: $http_endpoint"
    print_status "   Realtime Endpoint: $realtime_endpoint"
    print_status "   Channel Namespace: $channel_namespace"
    
    # Store API details in SSM Parameter Store for retrieval by other components
    print_status "Storing AppSync API details in SSM Parameter Store..."
    
    local ssm_value=$(cat << EOJSON
{
  "apiId": "$api_id",
  "apiArn": "$api_arn",
  "httpEndpoint": "$http_endpoint",
  "realtimeEndpoint": "$realtime_endpoint",
  "channelNamespace": "$channel_namespace",
  "region": "$AWS_REGION",
  "createdAt": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
}
EOJSON
)
    
    if aws_cmd ssm put-parameter \
        --name "$ssm_param_name" \
        --value "$ssm_value" \
        --type "String" \
        --overwrite \
        --region "$AWS_REGION" > /dev/null 2>&1; then
        print_success "✅ AppSync API details stored in SSM: $ssm_param_name"
    else
        print_warning "⚠️  Failed to store AppSync API details in SSM"
        print_warning "API is created but details may not be accessible to other components"
    fi
    
    # Save to local file for immediate use
    echo "$ssm_value" > "$appsync_file"
    print_status "💾 AppSync API details saved locally: $appsync_file"
    
    return 0
}

# Function to deploy infrastructure
deploy_infrastructure() {
    print_step "Step 2: Deploying infrastructure..."
    
    prompt_for_demo_email
    
    # First, package and upload Lambda functions before infrastructure deployment
    print_status "Packaging and uploading Lambda functions..."
    
    # Create a temporary S3 bucket name for Lambda deployment
    local lambda_bucket="${STACK_PREFIX}-lambda-deploy-${UNIQUE_ID}"
    
    # Create the Lambda deployment bucket if it doesn't exist
    print_status "Creating Lambda deployment bucket: $lambda_bucket"
    if ! aws_cmd s3 mb "s3://$lambda_bucket" --region "$AWS_REGION" 2>/dev/null; then
        # Bucket might already exist, check if we can access it
        if ! aws_cmd s3 ls "s3://$lambda_bucket" --region "$AWS_REGION" >/dev/null 2>&1; then
            print_error "Failed to create or access Lambda deployment bucket: $lambda_bucket"
            exit 1
        fi
    fi
    
    # Package and upload async image processor
    local package_script="${SCRIPT_DIR}/package-lambda.sh"
    if [ ! -f "$package_script" ]; then
        print_error "Lambda packaging script not found: $package_script"
        exit 1
    fi
    
    # Package and upload all Lambda functions
    local lambda_functions=(
        "async-image-processor:async_image_processor.py"
        "create-demo-user:create_demo_user.py"
        "creative-image-generator:creative_image_generator.py"
    )
    
    for lambda_func in "${lambda_functions[@]}"; do
        IFS=':' read -r func_name python_file <<< "$lambda_func"
        
        print_status "Packaging $func_name Lambda..."
        local lambda_args="$func_name $python_file $lambda_bucket $STACK_PREFIX $UNIQUE_ID"
        
        if [ -n "$AWS_PROFILE" ]; then
            lambda_args="$lambda_args $AWS_PROFILE"
        else
            lambda_args="$lambda_args default"
        fi
        
        lambda_args="$lambda_args $AWS_REGION"
        
        if ! "$package_script" $lambda_args; then
            print_error "Failed to package and upload $func_name Lambda"
            exit 1
        fi
    done
    
    print_success "✅ All Lambda functions packaged and uploaded successfully"
    
    # Deploy core infrastructure first
    print_status "Deploying core infrastructure (S3, OpenSearch, Cognito)..."
    local infrastructure_core_stack="${STACK_PREFIX}-infrastructure-core"
    local core_template_file="${PROJECT_ROOT}/cloudformation/infrastructure-core.yml"
    
    if [ ! -f "$core_template_file" ]; then
        print_error "Core infrastructure template not found: $core_template_file"
        exit 1
    fi
    
    local core_parameters="--parameters ParameterKey=StackPrefix,ParameterValue=$STACK_PREFIX ParameterKey=UniqueId,ParameterValue=$UNIQUE_ID ParameterKey=DemoUserEmail,ParameterValue=$DEMO_USER_EMAIL"
    
    # Add Lambda S3 parameters for core stack
    core_parameters="$core_parameters ParameterKey=AsyncImageProcessorS3Bucket,ParameterValue=$lambda_bucket"
    core_parameters="$core_parameters ParameterKey=CreateDemoUserS3Key,ParameterValue=lambda/create-demo-user.zip"
    
    if ! deploy_stack "$infrastructure_core_stack" "$core_template_file" "$core_parameters"; then
        print_error "Core infrastructure deployment failed"
        exit 1
    fi
    
    print_success "✅ Core infrastructure deployed successfully"
    
    # Deploy services infrastructure second
    print_status "Deploying services infrastructure (Lambda, DynamoDB)..."
    local infrastructure_services_stack="${STACK_PREFIX}-infrastructure-services"
    local services_template_file="${PROJECT_ROOT}/cloudformation/infrastructure-services.yml"
    
    if [ ! -f "$services_template_file" ]; then
        print_error "Services infrastructure template not found: $services_template_file"
        exit 1
    fi
    
    local services_parameters="--parameters ParameterKey=StackPrefix,ParameterValue=$STACK_PREFIX ParameterKey=UniqueId,ParameterValue=$UNIQUE_ID"
    
    # Add Lambda S3 parameters for services stack
    services_parameters="$services_parameters ParameterKey=AsyncImageProcessorS3Bucket,ParameterValue=$lambda_bucket"
    services_parameters="$services_parameters ParameterKey=AsyncImageProcessorS3Key,ParameterValue=lambda/async-image-processor.zip"
    # services_parameters="$services_parameters ParameterKey=VisualizationsLambdaS3Key,ParameterValue=lambda/visualizations-action-group.zip"
    
    if [ -n "$IMAGE_GENERATION_MODEL" ]; then
        services_parameters="$services_parameters ParameterKey=ImageGenerationModel,ParameterValue=$IMAGE_GENERATION_MODEL"
    fi
    
    if ! deploy_stack "$infrastructure_services_stack" "$services_template_file" "$services_parameters"; then
        print_error "Services infrastructure deployment failed"
        exit 1
    fi
    
    print_success "✅ Services infrastructure deployed successfully"
    
    # Create AppSync Events API using AWS CLI
    print_status "Creating AppSync Events API..."
    create_appsync_events_api
    
    # Get and display demo user credentials
    print_status "Retrieving demo user credentials..."
    local demo_user_password=$(get_stack_output "$infrastructure_core_stack" "DemoUserPassword")
    local user_pool_id=$(get_stack_output "$infrastructure_core_stack" "UserPoolId")
    
    if [ -n "$demo_user_password" ] && [ "$demo_user_password" != "None" ]; then
        echo ""
        print_success "🔐 Demo User Credentials:"
        print_success "   Email: $DEMO_USER_EMAIL"
        print_success "   Temporary Password: $demo_user_password"
        print_warning "   ⚠️  This password must be changed on first login"
        if [ -n "$user_pool_id" ] && [ "$user_pool_id" != "None" ]; then
            print_status "   User Pool ID: $user_pool_id"
        fi
        echo ""
    else
        print_warning "⚠️  Could not retrieve demo user password from core infrastructure stack"
    fi
    
    # Package and deploy Lambda functions
}

# Function to copy tab configurations to S3 creatives bucket
copy_tab_configurations_to_s3() {
    print_status "Copying tab configurations to S3 creatives bucket..."
    
    local infrastructure_services_stack="${STACK_PREFIX}-infrastructure-services"
    local creatives_bucket=$(get_stack_output "$infrastructure_services_stack" "GeneratedContentBucketName")
    
    if [ -z "$creatives_bucket" ] || [ "$creatives_bucket" = "None" ]; then
        print_warning "Creatives bucket not found, skipping tab configurations copy"
        return 0
    fi
    
    # Check if source file exists
    local source_file="${PROJECT_ROOT}/synthetic_data/configs/tab-configurations.json"
    if [ ! -f "$source_file" ]; then
        print_warning "Source tab-configurations.json not found: $source_file"
        return 0
    fi
    
    print_status "Copying tab-configurations.json to S3 bucket: $creatives_bucket"
    
    # Copy the file to S3 under configurations folder
    if aws_cmd s3 cp "$source_file" "s3://$creatives_bucket/configurations/tab-configurations.json" --region "$AWS_REGION"; then
        print_success "✅ Tab configurations copied to S3 successfully"
        
        # Verify the file was uploaded
        if aws_cmd s3 ls "s3://$creatives_bucket/configurations/tab-configurations.json" --region "$AWS_REGION" > /dev/null; then
            print_status "✅ Verified: tab-configurations.json is available in S3"
        else
            print_warning "⚠️  Could not verify file upload"
        fi
    else
        print_error "❌ Failed to copy tab configurations to S3"
        return 1
    fi
    
    return 0
}

# Function to deploy Lambda functions
deploy_lambda_functions() {
    print_step "Step 3: Deploying Lambda functions..."
    
    local infrastructure_core_stack="${STACK_PREFIX}-infrastructure-core"
    local data_bucket=$(get_stack_output "$infrastructure_core_stack" "SyntheticDataBucketName")
    
    if [ -z "$data_bucket" ] || [ "$data_bucket" = "None" ]; then
        print_warning "Could not retrieve data bucket name, skipping Lambda deployment"
        return 0
    fi
    
    print_status "Using S3 bucket for Lambda deployment: $data_bucket"
    
    # Package and deploy async image processor
    local package_script="${SCRIPT_DIR}/package-lambda.sh"
    
    if [ -f "$package_script" ]; then
        print_status "Packaging and deploying async image processor Lambda..."
        
        local lambda_args="async-image-processor async_image_processor.py $data_bucket $STACK_PREFIX $UNIQUE_ID"
        
        if [ -n "$AWS_PROFILE" ]; then
            lambda_args="$lambda_args $AWS_PROFILE"
        else
            lambda_args="$lambda_args default"
        fi
        
        lambda_args="$lambda_args $AWS_REGION"
        
        if "$package_script" $lambda_args; then
            print_success "✅ Lambda functions deployed successfully"
        else
            print_warning "⚠️  Lambda deployment failed, but continuing with infrastructure deployment"
        fi
    else
        print_warning "Lambda packaging script not found, skipping Lambda deployment"
    fi
}

# Function to deploy knowledge bases using organized data structure
deploy_knowledge_bases() {
    print_step "Step 4: Deploying knowledge bases with organized data sources..."
    
    local infrastructure_core_stack="${STACK_PREFIX}-infrastructure-core"
    local data_bucket=$(get_stack_output "$infrastructure_core_stack" "SyntheticDataBucketName")
    local opensearch_collection_arn=$(get_stack_output "$infrastructure_core_stack" "OpenSearchCollectionArn")
    local opensearch_collection_endpoint=$(get_stack_output "$infrastructure_core_stack" "OpenSearchCollectionEndpoint")
    local bedrock_role_arn=$(get_stack_output "$infrastructure_core_stack" "BedrockExecutionRoleArn")
    local embedding_model="amazon.titan-embed-text-v2:0"
    
    if [ -z "$data_bucket" ] || [ -z "$opensearch_collection_arn" ] || [ -z "$opensearch_collection_endpoint" ] || [ -z "$bedrock_role_arn" ]; then
        print_error "Could not retrieve required infrastructure outputs"
        exit 1
    fi
    
    print_status "Data bucket: $data_bucket"
    print_status "OpenSearch collection: $opensearch_collection_arn"
    print_status "OpenSearch endpoint: $opensearch_collection_endpoint"
    print_status "Bedrock role: $bedrock_role_arn"
    
    # Upload synthetic data files to S3 bucket
    print_status "Uploading synthetic data files to S3 bucket..."
    
    local synthetic_data_dir="${PROJECT_ROOT}/synthetic_data"
    
    if [ ! -d "$synthetic_data_dir" ]; then
        print_error "Synthetic data directory not found: $synthetic_data_dir"
        exit 1
    fi
    
    # Upload all synthetic data files to S3
    local aws_cmd="aws s3 sync \"$synthetic_data_dir\" \"s3://$data_bucket/\" --region $AWS_REGION"
    
    if [ -n "$AWS_PROFILE" ]; then
        aws_cmd="$aws_cmd --profile $AWS_PROFILE"
    fi
    
    print_status "Running: $aws_cmd"
    
    if eval "$aws_cmd"; then
        print_success "✅ Synthetic data files uploaded successfully"
        
        # List uploaded files for verification
        print_status "Verifying uploaded files..."
        local list_cmd="aws s3 ls \"s3://$data_bucket/\" --recursive --region $AWS_REGION"
        
        if [ -n "$AWS_PROFILE" ]; then
            list_cmd="$list_cmd --profile $AWS_PROFILE"
        fi
        
        print_status "Sample uploaded files:"
        eval "$list_cmd" | head -10
    else
        print_error "❌ Failed to upload synthetic data files"
        exit 1
    fi
    
    # Read knowledge bases configuration with organized data sources
    local kb_config_file="${PROJECT_ROOT}/cloudformation/generic-configs/knowledgebases/knowledgebases-with-datasources.json"
    
    if [ ! -f "$kb_config_file" ]; then
        print_error "Knowledge bases configuration not found: $kb_config_file"
        exit 1
    fi
    
    # First, create all OpenSearch indexes at once
    print_status "Creating OpenSearch vector indexes..."
    
    # Extract collection ID from ARN (format: arn:aws:aoss:region:account:collection/collection-id)
    local collection_id=$(echo "$opensearch_collection_arn" | sed 's|.*/||')
    
    # Extract index names from knowledge base configuration
    print_status "Extracting index names from knowledge base configuration..."
    setup_python_environment
    local index_names=$($PYTHON_CMD -c "
import json
with open('${kb_config_file}', 'r') as f:
    kb_configs = json.load(f)
index_names = [kb['index_name'] for kb in kb_configs]
print(' '.join(index_names))
")
    
    print_status "Index names to create: $index_names"
    
    # Call the Python vector index creation script with the extracted index names
    local index_script="${SCRIPT_DIR}/create_vector_indices.py"
    local index_cmd="$PYTHON_CMD $index_script --collection-id $collection_id --region $AWS_REGION --action create --indexes $index_names"
    
    if [ -n "$AWS_PROFILE" ]; then
        index_cmd="$index_cmd --profile $AWS_PROFILE"
    fi
    
    print_status "Running: $index_cmd"
    
    if eval "$index_cmd"; then
        print_success "✅ OpenSearch vector indexes created successfully"
    else
        print_warning "⚠️  OpenSearch index creation had issues, but continuing (Bedrock will auto-create indexes if needed)"
    fi
    
    # Check for existing knowledge bases before deployment
    print_status "Checking for existing knowledge bases..."
    
    # Setup Python environment for deployment operations
    setup_python_environment
    
    # Deploy knowledge bases and data sources using organized structure
    "$VENV_PATH/bin/python" << EOF
import json
import subprocess
import sys
import os
def handle_rollback_complete(stack_name, region, profile):
    """Handle stacks in ROLLBACK_COMPLETE state by deleting them"""
    # Check if stack exists and get its status
    check_cmd = ["aws", "cloudformation", "describe-stacks", "--stack-name", stack_name, "--region", region]
    if profile:
        check_cmd.extend(["--profile", profile])
    
    check_result = subprocess.run(check_cmd, capture_output=True, text=True)
    if check_result.returncode == 0:
        # Stack exists, check its status
        status_cmd = [
            "aws", "cloudformation", "describe-stacks",
            "--stack-name", stack_name,
            "--query", "Stacks[0].StackStatus",
            "--output", "text",
            "--region", region
        ]
        if profile:
            status_cmd.extend(["--profile", profile])
        
        status_result = subprocess.run(status_cmd, capture_output=True, text=True)
        if status_result.returncode == 0:
            stack_status = status_result.stdout.strip()
            
            if stack_status == "ROLLBACK_COMPLETE":
                print(f"  Stack {stack_name} is in ROLLBACK_COMPLETE state, deleting before recreation...")
                
                # Delete the stack
                delete_cmd = ["aws", "cloudformation", "delete-stack", "--stack-name", stack_name, "--region", region]
                if profile:
                    delete_cmd.extend(["--profile", profile])
                
                delete_result = subprocess.run(delete_cmd, capture_output=True, text=True)
                if delete_result.returncode != 0:
                    print(f"  Error deleting stack {stack_name}: {delete_result.stderr}")
                    return False
                
                # Wait for deletion to complete
                print(f"  Waiting for stack deletion to complete...")
                wait_cmd = ["aws", "cloudformation", "wait", "stack-delete-complete", "--stack-name", stack_name, "--region", region]
                if profile:
                    wait_cmd.extend(["--profile", profile])
                
                wait_result = subprocess.run(wait_cmd, capture_output=True, text=True)
                if wait_result.returncode != 0:
                    print(f"  Error waiting for stack deletion: {wait_result.stderr}")
                    return False
                
                print(f"  ✅ Stack {stack_name} deleted successfully")
                return True
    
    return True
def deploy_knowledge_base(kb_name, kb_description, index_name, stack_prefix, unique_id, opensearch_arn, bedrock_role_arn, embedding_model, region, profile):
    """Deploy a single knowledge base"""
    print(f"Deploying Knowledge Base: {kb_name}")
    
    kb_stack_name = f"{stack_prefix}-{kb_name.lower()}-kb"
    kb_template = "cloudformation/create-single-knowledge-base.yml"
    
    # Check if stack already exists
    check_cmd = ["aws", "cloudformation", "describe-stacks", "--stack-name", kb_stack_name, "--region", region]
    if profile:
        check_cmd.extend(["--profile", profile])
    
    check_result = subprocess.run(check_cmd, capture_output=True, text=True)
    if check_result.returncode == 0:
        # Stack exists, check its status
        status_cmd = [
            "aws", "cloudformation", "describe-stacks",
            "--stack-name", kb_stack_name,
            "--query", "Stacks[0].StackStatus",
            "--output", "text",
            "--region", region
        ]
        if profile:
            status_cmd.extend(["--profile", profile])
        
        status_result = subprocess.run(status_cmd, capture_output=True, text=True)
        if status_result.returncode == 0:
            stack_status = status_result.stdout.strip()
            print(f"  Knowledge Base stack {kb_stack_name} exists with status: {stack_status}")
            
            if stack_status in ["ROLLBACK_COMPLETE", "DELETE_FAILED"]:
                print(f"  Stack {kb_stack_name} is in {stack_status} state, deleting before recreation...")
                
                # Delete the stack with force delete if needed
                delete_cmd = ["aws", "cloudformation", "delete-stack", "--stack-name", kb_stack_name, "--region", region]
                if stack_status == "DELETE_FAILED":
                    delete_cmd.extend(["--deletion-mode", "FORCE_DELETE_STACK"])
                    print(f"  Using FORCE_DELETE_STACK mode for DELETE_FAILED stack")
                if profile:
                    delete_cmd.extend(["--profile", profile])
                
                delete_result = subprocess.run(delete_cmd, capture_output=True, text=True)
                if delete_result.returncode != 0:
                    print(f"  Error deleting stack {kb_stack_name}: {delete_result.stderr}")
                    return None
                
                # Wait for deletion to complete
                print(f"  Waiting for stack deletion to complete...")
                wait_cmd = ["aws", "cloudformation", "wait", "stack-delete-complete", "--stack-name", kb_stack_name, "--region", region]
                if profile:
                    wait_cmd.extend(["--profile", profile])
                
                wait_result = subprocess.run(wait_cmd, capture_output=True, text=True)
                if wait_result.returncode != 0:
                    print(f"  Error waiting for stack deletion: {wait_result.stderr}")
                    return None
                
                print(f"  ✅ Stack {kb_stack_name} deleted successfully")
    
    cmd = [
        "aws", "cloudformation", "deploy",
        "--template-file", kb_template,
        "--stack-name", kb_stack_name,
        "--parameter-overrides",
        f"StackPrefix={stack_prefix}",
        f"UniqueID={unique_id}",
        f"KnowledgeBaseName={kb_name}",
        f"KnowledgeBaseDescription={kb_description}",
        f"OpenSearchCollectionArn={opensearch_arn}",
        f"VectorIndexName={index_name}",
        f"EmbeddingModelArn=arn:aws:bedrock:{region}::foundation-model/{embedding_model}",
        "--capabilities", "CAPABILITY_NAMED_IAM",
        "--region", region
    ]
    
    if profile:
        cmd.extend(["--profile", profile])
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error deploying KB {kb_name}: {result.stderr}")
        return None
    
    # Get KB ID from stack output
    cmd = [
        "aws", "cloudformation", "describe-stacks",
        "--stack-name", kb_stack_name,
        "--query", "Stacks[0].Outputs[?OutputKey=='KnowledgeBaseId'].OutputValue",
        "--output", "text",
        "--region", region
    ]
    
    if profile:
        cmd.extend(["--profile", profile])
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error getting KB ID for {kb_name}: {result.stderr}")
        return None
    
    kb_id = result.stdout.strip()
    print(f"Knowledge Base ID: {kb_id}")
    return kb_id

def deploy_data_source(kb_id, ds_name, ds_description, data_prefix, chunking_tokens, chunking_overlap, kb_name_lower, stack_prefix, unique_id, data_bucket, region, profile):
    """Deploy a single data source"""
    print(f"  Deploying Data Source: {ds_name} for KB: {kb_name_lower}")
    print(f"    Data Prefix: {data_prefix}")
    print(f"    Chunking: {chunking_tokens} tokens, {chunking_overlap}% overlap")
    
    ds_stack_name = f"{stack_prefix}-{kb_name_lower}-{ds_name.lower()}-ds"
    ds_template = "cloudformation/create-data-source.yml"
    
    # Handle ROLLBACK_COMPLETE state
    if not handle_rollback_complete(ds_stack_name, region, profile):
        return False
    
    cmd = [
        "aws", "cloudformation", "deploy",
        "--template-file", ds_template,
        "--stack-name", ds_stack_name,
        "--parameter-overrides",
        f"StackPrefix={stack_prefix}",
        f"UniqueID={unique_id}",
        f"DataSourceName={ds_name}",
        f"DataSourceDescription={ds_description}",
        f"KnowledgeBaseId={kb_id}",
        f"DataBucketName={data_bucket}",
        f"DataPrefix={data_prefix}",
        f"ChunkingMaxTokens={chunking_tokens}",
        f"ChunkingOverlapPercentage={chunking_overlap}",
        "--region", region
    ]
    
    if profile:
        cmd.extend(["--profile", profile])
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error deploying data source {ds_name}: {result.stderr}")
        return False
    
    print(f"  ✅ Data Source {ds_name} deployed successfully")
    return True

# Read the knowledgebases-with-datasources.json configuration
with open('${kb_config_file}', 'r') as f:
    config = json.load(f)

kb_ids = {}

# Deploy each knowledge base and its data sources
for kb_config in config:
    kb_name = kb_config['name']
    kb_description = kb_config['description']
    index_name = kb_config['index_name']
    
    # Deploy knowledge base
    kb_id = deploy_knowledge_base(
        kb_name, 
        kb_description, 
        index_name, 
        '${STACK_PREFIX}', 
        '${UNIQUE_ID}', 
        '${opensearch_collection_arn}', 
        '${bedrock_role_arn}', 
        '${embedding_model}',
        '${AWS_REGION}', 
        '${AWS_PROFILE}'
    )
    
    if kb_id:
        kb_ids[kb_name] = kb_id
        
        # Deploy data sources for this knowledge base
        for ds_config in kb_config['data_sources']:
            ds_name = ds_config['name']
            ds_description = ds_config['description']
            data_prefix = ds_config['data_prefix']
            chunking_tokens = ds_config['chunking_max_tokens']
            chunking_overlap = ds_config['chunking_overlap_percentage']
            
            deploy_data_source(
                kb_id, 
                ds_name, 
                ds_description, 
                data_prefix, 
                chunking_tokens, 
                chunking_overlap, 
                kb_name.lower(), 
                '${STACK_PREFIX}', 
                '${UNIQUE_ID}', 
                '${data_bucket}', 
                '${AWS_REGION}', 
                '${AWS_PROFILE}'
            )
    else:
        print(f"❌ Failed to deploy Knowledge Base: {kb_name}")

print("\\nKnowledge Base IDs:")
for kb_name, kb_id in kb_ids.items():
    print(f"  {kb_name}: {kb_id}")

# Save KB IDs to a file for later use
with open('${PROJECT_ROOT}/.kb-ids-${STACK_PREFIX}-${UNIQUE_ID}.json', 'w') as f:
    json.dump(kb_ids, f, indent=2)

print(f"\\n✅ Knowledge bases deployment configuration complete!")
print(f"📁 Organized data structure from knowledgebases-with-datasources.json processed successfully")

EOF
    
    print_success "✅ Knowledge bases with organized data sources deployed successfully"
}

# Function to sync data sources (start ingestion jobs)
sync_data_sources() {
    print_step "Step 5: Syncing knowledge base data sources..."
    
    #print_status "Waiting 30 seconds for knowledge bases to fully initialize..."
    #sleep 30
    
    print_status "Starting data source ingestion jobs for all knowledge bases..."
    
    # Setup Python environment for ingestion operations
    setup_python_environment
    
    # Use Python to start ingestion jobs with proper delays
    "$VENV_PATH/bin/python" << EOF
import boto3
import time
import json
from botocore.exceptions import ClientError

def sync_knowledge_base_data_sources(stack_prefix, region, profile):
    """Start ingestion jobs for all knowledge bases with the stack prefix"""
    try:
        print(f"DEBUG: stack_prefix='{stack_prefix}', region='{region}', profile='{profile}'")
        
        if profile:
            print(f"DEBUG: Creating boto3 session with profile: {profile}")
            boto3_session = boto3.session.Session(profile_name=profile)
        else:
            print(f"DEBUG: Creating boto3 session with default credentials")
            boto3_session = boto3.session.Session()
        
        bedrock_client = boto3_session.client('bedrock-agent', region_name=region)
        
        # List ALL knowledge bases with pagination
        kb_list = []
        name_filter = f"{stack_prefix}-"
        print(f"DEBUG: Searching for KBs with filter: '{name_filter}'")
        
        # Handle pagination to get all knowledge bases
        next_token = None
        total_kbs = 0
        while True:
            if next_token:
                response = bedrock_client.list_knowledge_bases(maxResults=50, nextToken=next_token)
            else:
                response = bedrock_client.list_knowledge_bases(maxResults=50)
            
            summaries = response.get('knowledgeBaseSummaries', [])
            total_kbs += len(summaries)
            
            for kb in summaries:
                kb_name = kb['name']
                if name_filter.lower() in kb_name.lower():
                    print(f"DEBUG: MATCH - {kb_name}")
                    kb_list.append(kb)
            
            next_token = response.get('nextToken')
            if not next_token:
                break
        
        print(f"DEBUG: Total KBs scanned: {total_kbs}, Matches found: {len(kb_list)}")
        
        if not kb_list:
            print(f"No knowledge bases found with prefix '{stack_prefix}-'")
            return False
        
        print(f"Found {len(kb_list)} knowledge bases to sync")
        
        jobs_started = 0
        total_jobs = 0
        
        for kb in kb_list:
            kb_id = kb['knowledgeBaseId']
            kb_name = kb['name']
            
            print(f"\\n📚 Processing Knowledge Base: {kb_name}")
            print(f"   ID: {kb_id}")
            
            # Get data sources
            try:
                ds_response = bedrock_client.list_data_sources(knowledgeBaseId=kb_id)
                data_sources = ds_response.get('dataSourceSummaries', [])
                
                if not data_sources:
                    print("   ⚠️  No data sources found - they may still be creating")
                    continue
                
                for ds in data_sources:
                    ds_id = ds['dataSourceId']
                    ds_name = ds['name']
                    total_jobs += 1
                    
                    print(f"   📄 Starting ingestion for data source: {ds_name}")
                    
                    try:
                        job_response = bedrock_client.start_ingestion_job(
                            knowledgeBaseId=kb_id,
                            dataSourceId=ds_id,
                            description=f"Ingestion job started via deployment script"
                        )
                        
                        job_id = job_response['ingestionJob']['ingestionJobId']
                        print(f"   ✅ Started ingestion job: {job_id}")
                        jobs_started += 1
                        
                        # Wait 10 seconds between starting ingestion jobs to avoid rate limits
                        if total_jobs > 1:  # Don't wait after the last job
                            print("   ⏳ Waiting 10 seconds before next ingestion job...")
                            time.sleep(10)
                        
                    except ClientError as e:
                        print(f"   ❌ Failed to start ingestion job: {e}")
                        
            except ClientError as e:
                print(f"   ❌ Failed to get data sources: {e}")
        
        print(f"\\n📊 Ingestion Summary:")
        print(f"   Total data sources: {total_jobs}")
        print(f"   Ingestion jobs started: {jobs_started}")
        
        if jobs_started > 0:
            print(f"\\n⏳ Ingestion jobs are running in the background.")
            print(f"   You can monitor progress in the Bedrock console:")
            print(f"   https://{region}.console.aws.amazon.com/bedrock/home?region={region}#/knowledge-bases")
            print(f"\\n💡 Note: Ingestion typically takes 5-15 minutes depending on data size.")
            return True
        else:
            print("\\n⚠️  No ingestion jobs were started")
            return False
            
    except Exception as e:
        print(f"Error syncing data sources: {e}")
        return False

# Call the function
success = sync_knowledge_base_data_sources('${STACK_PREFIX}', '${AWS_REGION}', '${AWS_PROFILE}' if '${AWS_PROFILE}' else None)
exit(0 if success else 1)

EOF
    
    if [ $? -eq 0 ]; then
        print_success "✅ Data source ingestion jobs started successfully"
        print_status "💡 Ingestion jobs are running in the background and will take 5-15 minutes to complete"
        print_status "💡 You can monitor progress in the AWS Bedrock console"
    else
        print_warning "⚠️  Some data source ingestion jobs may have failed"
    fi
}

create_agentcore_memory() {
    print_status "Creating shared AgentCore memory for all deployed agents..."
    
    # Setup Python environment with required dependencies
    setup_python_environment
    
    # Install bedrock-agentcore package if not already installed
    if ! $PYTHON_CMD -c "import bedrock_agentcore" 2>/dev/null; then
        print_status "Installing bedrock-agentcore package..."
        $PYTHON_CMD -m pip install bedrock-agentcore --quiet
        if [ $? -ne 0 ]; then
            print_warning "⚠️  Failed to install bedrock-agentcore package"
            print_warning "   Memory functionality may be limited"
            return 0
        fi
        print_success "✅ bedrock-agentcore package installed"
    fi
    
    # Create ONE memory record using the simple script
    local memory_script="${PROJECT_ROOT}/agentcore/deployment/create_simple_memory.py"
    if [ ! -f "$memory_script" ]; then
        print_warning "Simple memory creation script not found: $memory_script"
        return 0
    fi
    
    print_status "Creating ONE shared memory record..."
    
    # Set AWS profile if provided
    if [ -n "$AWS_PROFILE" ]; then
        export AWS_PROFILE="$AWS_PROFILE"
    fi
    
    # Define output file for memory record ID
    local memory_record_file="${PROJECT_ROOT}/.memory-record-${STACK_PREFIX}-${UNIQUE_ID}.json"
    
    if $PYTHON_CMD "$memory_script" \
        --stack-prefix "$STACK_PREFIX" \
        --unique-id "$UNIQUE_ID" \
        --aws-region "$AWS_REGION" \
        --output-file "$memory_record_file"; then
        print_success "✅ Shared AgentCore memory created successfully"
        
        # Verify the memory record file was created
        if [ -f "$memory_record_file" ]; then
            local memory_record_id=$($PYTHON_CMD -c "
import json
try:
    with open('$memory_record_file', 'r') as f:
        data = json.load(f)
    print(data.get('memory_record_id', ''))
except:
    print('')
" 2>/dev/null)
            
            if [ -n "$memory_record_id" ]; then
                print_status "💾 Memory record ID saved: $memory_record_id"
                print_status "📄 Memory record file: $memory_record_file"
            else
                print_warning "⚠️  Could not read memory record ID from file"
            fi
        else
            print_warning "⚠️  Memory record file was not created"
        fi
    else
        print_warning "⚠️  Failed to create shared AgentCore memory, agents may have limited memory functionality"
    fi
}

detect_and_deploy_agentcore_agents() {
    print_step "Step 7: Deploying AgentCore agents (after MCP Gateway)..."
    
    local agentcore_dir="${PROJECT_ROOT}/agentcore/deployment/agent"
    
    if [ ! -d "$agentcore_dir" ]; then
        print_status "No AgentCore agents directory found, skipping AgentCore deployment"
        return 0
    fi
    
    # Check if Docker is available
    if ! command -v docker &> /dev/null; then
        print_warning "Docker not found, skipping AgentCore deployment"
        return 0
    fi
    
    # Find AgentCore agents
    local agentcore_agents=("AdFabricAgent")
    # for agent_dir in "$agentcore_dir"/*; do
    #     if [ -d "$agent_dir" ] && [ -f "$agent_dir/config.json" ]; then
    #         local agent_name=$(basename "$agent_dir")
    #         agentcore_agents+=("$agent_name")
    #     fi
    # done
    
    if [ ${#agentcore_agents[@]} -eq 0 ]; then
        print_status "No AgentCore agents found, skipping AgentCore deployment"
        return 0
    fi
    
    print_status "Found ${#agentcore_agents[@]} AgentCore agents: ${agentcore_agents[*]}"
    
    # Deploy each AgentCore agent
    local deployed_agents=()
    local a2a_agents_deployed=()
    local standard_agents_deployed=()
    
    for agent_name in "${agentcore_agents[@]}"; do
        # Create AgentCore agent name with stack prefix and unique ID suffix
        local agentcore_agent_name="${STACK_PREFIX}-${agent_name}-${UNIQUE_ID}"
        local agent_dir="${agentcore_dir}"
        
        print_status "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        print_status "Deploying AgentCore agent: $agent_name as $agentcore_agent_name"
        print_status "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        
        # Check if this is an A2A-enabled agent
        # local agent_config_file="${agent_dir}/config.json"
        local is_a2a=false
        # local a2a_deployment_success=false
        
        # if is_a2a_agent "$agent_config_file"; then
        #     is_a2a=true
        #     print_status "🔗 A2A-enabled agent detected: $agent_name"
            
        #     # Use A2A deployment orchestration function
        #     if deploy_a2a_agent "$agent_name" "$agent_dir" "$agentcore_agent_name"; then
        #         a2a_deployment_success=true
        #         print_success "✅ A2A orchestration completed for: $agent_name"
        #     else
        #         print_error "❌ A2A orchestration failed for: $agent_name"
        #         print_error "   Skipping deployment of this agent"
        #         print_error "   Check logs above for specific A2A deployment errors"
        #         continue
        #     fi
        # else
        #     print_status "📦 Standard AgentCore agent: $agent_name"
        # fi
        
        # Use the build and deploy script for runtime deployment
        local deploy_script="${PROJECT_ROOT}/agentcore/deployment/build_and_deploy.sh"
        if [ -f "$deploy_script" ]; then
            export AWS_REGION="$AWS_REGION"
            export AWS_PROFILE="$AWS_PROFILE"
            export STACK_PREFIX="$STACK_PREFIX"
            export UNIQUE_ID="$UNIQUE_ID"
            export AGENTCORE_AGENT_NAME="$agentcore_agent_name"
            
            # A2A environment variables are already exported by deploy_a2a_agent if needed
            
            print_status "Deploying runtime for: $agent_name"
            if "$deploy_script" "$agent_name"; then
                deployed_agents+=("$agentcore_agent_name")
                
                # if [ "$is_a2a" = true ]; then
                #     a2a_agents_deployed+=("$agent_name")
                #     print_success "✅ A2A agent '$agent_name' deployed successfully as '$agentcore_agent_name'"
                    
                #     # Store A2A configuration in tracking file
                #     if store_a2a_configuration "$agentcore_agent_name" "$agent_name"; then
                #         print_status "✅ A2A configuration persisted for: $agentcore_agent_name"
                #     else
                #         print_warning "⚠️  Failed to persist A2A configuration for: $agentcore_agent_name"
                #         print_warning "   Agent is deployed but A2A metadata may be incomplete"
                #     fi
                # else
                standard_agents_deployed+=("$agent_name")
                print_success "✅ Standard agent '$agent_name' deployed successfully as '$agentcore_agent_name'"
                # fi
            else
                print_warning "⚠️ Failed to deploy AgentCore agent: $agent_name"
                print_warning "   Runtime deployment failed - check build_and_deploy.sh logs"
            fi
            
            # Unset A2A environment variables after each deployment
            unset A2A_BEARER_TOKEN A2A_POOL_ID A2A_CLIENT_ID A2A_DISCOVERY_URL A2A_PROTOCOL
        else
            print_warning "⚠️ AgentCore deployment script not found: $deploy_script"
        fi
    done
    
    # Report deployment summary
    print_status "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    print_status "AgentCore Deployment Summary"
    print_status "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    print_status "Total agents deployed: ${#deployed_agents[@]}"
    if [ ${#a2a_agents_deployed[@]} -gt 0 ]; then
        print_status "🔗 A2A agents deployed (${#a2a_agents_deployed[@]}): ${a2a_agents_deployed[*]}"
    fi
    if [ ${#standard_agents_deployed[@]} -gt 0 ]; then
        print_status "📦 Standard agents deployed (${#standard_agents_deployed[@]}): ${standard_agents_deployed[*]}"
    fi
    print_status "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    if [ ${#deployed_agents[@]} -gt 0 ]; then
        print_success "✅ AgentCore agents deployed: ${deployed_agents[*]}"
        
        # DO NOT CREATE THE FILE HERE - let build_and_deploy.sh handle it completely
        # The build_and_deploy.sh script will create the file with proper runtime information
        local agentcore_info_file="${PROJECT_ROOT}/.agentcore-agents-${STACK_PREFIX}-${UNIQUE_ID}.json"
        
        print_status "💾 AgentCore runtime information should be saved by build_and_deploy.sh to: $agentcore_info_file"
        
        # Check if the file was created with runtime information
        if [ -f "$agentcore_info_file" ]; then
            local has_runtime_info=$($PYTHON_CMD -c "
import json
import sys
try:
    with open('$agentcore_info_file', 'r') as f:
        data = json.load(f)
    deployed_agents = data.get('deployed_agents', [])
    # Check if any agent has runtime information (new format)
    for agent in deployed_agents:
        if isinstance(agent, dict) and agent.get('runtime_id'):
            print('true')
            sys.exit(0)
    print('false')
except:
    print('false')
" 2>/dev/null || echo 'false')
            
            if [ "$has_runtime_info" = "true" ]; then
                print_status "✅ AgentCore agents file contains proper runtime information"
            else
                print_warning "⚠️  AgentCore agents file exists but lacks runtime information"
                print_warning "This indicates the build_and_deploy.sh script may have failed to save runtime details"
            fi
        else
            print_warning "⚠️  No AgentCore agents file found"
            print_warning "This indicates the build_and_deploy.sh script may have failed completely"
        fi
        
        # AgentCore agents are now integrated into the main aws-config.json file
        # No separate agentcore-agents.json file is needed
        print_status "📄 AgentCore agents will be integrated into aws-config.json during config generation"
        
        
    else
        print_warning "⚠️ No AgentCore agents were successfully deployed"
        
        # Create empty file to indicate no agents were deployed
        local agentcore_info_file="${PROJECT_ROOT}/.agentcore-agents-${STACK_PREFIX}-${UNIQUE_ID}.json"
        cat > "$agentcore_info_file" << EOF
{
  "deployed_agents": [],
  "deployment_time": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "stack_prefix": "$STACK_PREFIX",
  "unique_id": "$UNIQUE_ID",
  "note": "No AgentCore agents were deployed"
}
EOF
        print_status "💾 Created empty AgentCore agents file: $agentcore_info_file"
    fi
    # Store AgentCore values in SSM Parameter Store
    if [ ${#deployed_agents[@]} -gt 0 ]; then
        print_status "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        print_status "Storing AgentCore runtime configuration in SSM Parameter Store"
        print_status "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        
        local ssm_store_script="${PROJECT_ROOT}/agentcore/deployment/store_agentcore_values.sh"
        if [ -f "$ssm_store_script" ]; then
            local ssm_cmd="$ssm_store_script --stack-prefix $STACK_PREFIX --unique-id $UNIQUE_ID --region $AWS_REGION"
            if [ -n "$AWS_PROFILE" ]; then
                ssm_cmd="$ssm_cmd --profile $AWS_PROFILE"
            fi
            
            if $ssm_cmd; then
                print_success "✅ AgentCore runtime configuration stored in SSM Parameter Store"
                print_status "   Parameter: /${STACK_PREFIX}/agentcore_values/${UNIQUE_ID}"
                print_status "   This configuration includes runtime ARNs and bearer tokens"
                print_status "   UI and other components can retrieve this configuration from SSM"
            else
                print_warning "⚠️  Failed to store AgentCore configuration in SSM Parameter Store"
                print_warning "   Runtime configuration is still available in local tracking file"
                print_warning "   You can manually store it later using: $ssm_store_script"
            fi
        else
            print_warning "⚠️  SSM storage script not found: $ssm_store_script"
            print_warning "   Runtime configuration is only available in local tracking file"
        fi
        
        print_status "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    fi
    
    # Now create the shared memory for all AgentCore agents
    create_agentcore_memory
    # Add user prompt after AgentCore deployment completion
    if [ "$INTERACTIVE_MODE" = true ] && [ "$SKIP_CONFIRMATIONS" != true ]; then
        echo ""
        print_success "🎉 Step 7 Complete: AgentCore agents have been deployed!"
        print_status "The following step remains:"
        print_status "  - Step 8: Generate AWS configuration"
        echo ""
        printf "Continue with remaining deployment steps? (Y/n): "
        read -r continue_response
        if [[ "$continue_response" =~ ^[Nn]$ ]]; then
            print_status "Deployment paused after Step 7. You can resume later by running the script again."
            print_status "Current progress has been saved and the script will resume from Step 8 (UI Config)."
            exit 0
        fi
        print_status "Continuing with remaining deployment steps..."
        echo ""
    fi
}

# Function to deploy AdCP MCP Gateway for agent collaboration
# NOTE: This must run BEFORE AgentCore agents so the gateway URL is available
deploy_adcp_mcp_gateway() {
    print_step "Step 6: Deploying AdCP MCP Gateway for agent collaboration..."
    
    local deploy_script="${PROJECT_ROOT}/agentcore/deployment/deploy_adcp_gateway.py"
    
    if [ ! -f "$deploy_script" ]; then
        print_warning "AdCP Gateway deployment script not found: $deploy_script"
        print_warning "Skipping AdCP MCP Gateway deployment"
        return 0
    fi
    
    # Setup Python environment
    setup_python_environment
    
    print_status "Deploying AdCP MCP Gateway..."
    print_status "  Stack Prefix: $STACK_PREFIX"
    print_status "  Unique ID: $UNIQUE_ID"
    print_status "  Region: $AWS_REGION"
    print_status "  AWS Profile: ${AWS_PROFILE:-default}"
    
    # Export AWS environment variables for Python subprocess
    export AWS_DEFAULT_REGION="$AWS_REGION"
    if [ -n "$AWS_PROFILE" ]; then
        export AWS_PROFILE="$AWS_PROFILE"
    fi
    
    # Build command
    local deploy_cmd="$PYTHON_CMD $deploy_script --stack-prefix $STACK_PREFIX --unique-id $UNIQUE_ID --region $AWS_REGION"
    
    if [ -n "$AWS_PROFILE" ]; then
        deploy_cmd="$deploy_cmd --profile $AWS_PROFILE"
    fi
    
    print_status "Executing: $deploy_cmd"
    
    # Execute deployment
    # Use a subshell with set +e to prevent script exit on command failure
    local deploy_output
    local deploy_exit_code
    set +e  # Temporarily disable exit on error
    deploy_output=$(eval "$deploy_cmd" 2>&1)
    deploy_exit_code=$?
    set -e  # Re-enable exit on error
    
    if [ $deploy_exit_code -eq 0 ]; then
        print_success "✅ AdCP MCP Gateway deployed successfully"
        
        # Extract gateway URL from output if available
        local gateway_url=$(echo "$deploy_output" | grep -o 'gateway_url.*' | head -1 | sed 's/.*: *"\([^"]*\)".*/\1/' 2>/dev/null || echo "")
        
        # Also try to extract from JSON format
        if [ -z "$gateway_url" ] || [ "$gateway_url" = "null" ]; then
            gateway_url=$(echo "$deploy_output" | $PYTHON_CMD -c "
import json, sys
try:
    data = json.load(sys.stdin)
    url = data.get('gateway_result', {}).get('gateway_url') or data.get('gateway_url')
    if url:
        print(url)
except:
    pass
" 2>/dev/null || echo "")
        fi
        
        if [ -n "$gateway_url" ] && [ "$gateway_url" != "null" ]; then
            print_status "  Gateway URL: $gateway_url"
            
            # CRITICAL: Export gateway URL for AgentCore deployment to use
            export ADCP_GATEWAY_URL="$gateway_url"
            print_status "  ✅ Exported ADCP_GATEWAY_URL for AgentCore agents"
            
            # Store gateway URL in SSM for agents to use at runtime
            local ssm_param_name="/${STACK_PREFIX}/adcp_gateway/${UNIQUE_ID}"
            if aws_cmd ssm put-parameter \
                --name "$ssm_param_name" \
                --value "$gateway_url" \
                --type "String" \
                --overwrite \
                --region "$AWS_REGION" > /dev/null 2>&1; then
                print_status "  Gateway URL stored in SSM: $ssm_param_name"
            fi
        else
            print_warning "  ⚠️  Could not extract gateway URL from deployment output"
            print_warning "  AgentCore agents will use fallback local tools"
        fi
        
        # Save deployment output to file
        local gateway_info_file="${PROJECT_ROOT}/.adcp-gateway-${STACK_PREFIX}-${UNIQUE_ID}.json"
        echo "$deploy_output" > "$gateway_info_file"
        print_status "  Deployment info saved to: $gateway_info_file"
        
    else
        print_warning "⚠️  AdCP MCP Gateway deployment had issues (exit code: $deploy_exit_code)"
        print_warning "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "$deploy_output"
        print_warning "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        print_warning "Continuing with deployment - agents will use fallback local tools"
    fi
    
    return 0
}

# Function to generate UI configuration
generate_ui_config() {
    print_step "Step 8: Generating UI configuration..."
    
    # Create assets directory
    ANGULAR_ASSETS_DIR="${PROJECT_ROOT}/bedrock-adtech-demo/src/assets"
    GLOBAL_CONFIG="${PROJECT_ROOT}/agentcore/deployment/agent/global_configuration.json"
    
    mkdir -p "$ANGULAR_ASSETS_DIR"
    cp "$GLOBAL_CONFIG" "$ANGULAR_ASSETS_DIR"
    # Generate aws-config.json 
    CONFIG_FILE="$ANGULAR_ASSETS_DIR/aws-config.json"
    
    local config_script="${SCRIPT_DIR}/generate_aws_config.py"
    
    if [ ! -f "$config_script" ]; then
        print_warning "UI config generator not found, skipping UI configuration"
        return 0
    fi
    
    print_status "Generating aws-config.json for UI..."
    
    # Setup Python environment for UI config generation
    setup_python_environment
    
    if $PYTHON_CMD "$config_script" generate --prefix "${STACK_PREFIX}" --suffix "${UNIQUE_ID}" --region "${AWS_REGION}" --profile "${AWS_PROFILE}" --output "${CONFIG_FILE}"; then
        print_success "✅ UI configuration generated successfully"
    else
        print_warning "⚠️  Failed to generate UI configuration"
    fi
    
    # Get infrastructure outputs for UI deployment
    local infrastructure_core_stack="${STACK_PREFIX}-infrastructure-core"
    local ui_bucket=$(get_stack_output "$infrastructure_core_stack" "UIBucketName")
    local cloudfront_id=$(get_stack_output "$infrastructure_core_stack" "UICloudFrontDistributionId")
    local ui_url=$(get_stack_output "$infrastructure_core_stack" "UIUrl")
    
    if [ -z "$ui_bucket" ] || [ "$ui_bucket" = "None" ]; then
        print_warning "UI bucket not found, skipping UI deployment"
        return 0
    fi
    
    print_status "UI bucket: $ui_bucket"
    print_status "CloudFront distribution: $cloudfront_id"
    print_status "UI URL: $ui_url"
    
    # Ask if user wants to build and deploy the UI
    if [ "$INTERACTIVE_MODE" = true ]; then
        printf "Build and deploy the Angular UI application? (y/N): "
        read -r response
        if [[ ! "$response" =~ ^[Yy]$ ]]; then
            print_status "Skipping UI build and deployment"
            print_status "You can manually build and deploy the UI later"
            print_status "UI configuration has been generated at: $CONFIG_FILE"
            return 0
        fi
    fi
    
    # Define paths
    local ui_path="${PROJECT_ROOT}/bedrock-adtech-demo"
    local dist_path="${ui_path}/dist"
    
    # Check if Angular project directory exists
    if [ ! -d "$ui_path" ]; then
        print_error "Angular project directory not found: $ui_path"
        return 1
    fi
    
    # Check if Node.js and npm are available
    if ! command -v node &> /dev/null; then
        print_error "Node.js is required but not installed. Please install Node.js first."
        return 1
    fi
    
    if ! command -v npm &> /dev/null; then
        print_error "npm is required but not installed. Please install npm first."
        return 1
    fi
    
    # Navigate to Angular project directory
    print_status "Building Angular application..."
    cd "$ui_path" 
    
    # Check if package.json exists and has a build script
    if [ ! -f "package.json" ]; then
        print_error "package.json not found in Angular project directory"
        cd - > /dev/null
        return 1
    fi
    
    # Check if the build script exists in package.json
    if ! grep -q '"build"' "package.json"; then
        print_error "No 'build' script found in package.json"
        print_error "Please ensure the Angular project is properly configured"
        cd - > /dev/null
        return 1
    fi
    
    print_status "Installing npm dependencies..."
    if ! npm install; then
        print_error "Failed to install npm dependencies"
        cd - > /dev/null
        return 1
    fi
    
    # Clean previous build
    print_status "Cleaning previous build..."
    if [ -d "$dist_path" ]; then
        rm -rf "$dist_path"
    fi
    
    # Build for production
    print_status "Building Angular app for production..."
    print_status "Current directory: $(pwd)"
    print_status "Node version: $(node --version)"
    print_status "NPM version: $(npm --version)"
    print_status "Angular CLI version: $(npx ng version --skip-git 2>/dev/null | head -1 || echo 'Angular CLI not found')"
    
    # Run the build command
    if ! npm run build; then
        print_error "Failed to build Angular application"
        print_error "Build logs above should show the specific error"
        cd - > /dev/null
        return 1
    fi
    
    # Check if build output exists
    if [ ! -d "$dist_path" ]; then
        print_error "Build output directory not found: $dist_path"
        cd - > /dev/null
        return 1
    fi
    
    # Find the actual build output directory
    local build_output=""
    if [ -d "${dist_path}/bedrock-adtech-demo" ]; then
        build_output="${dist_path}/bedrock-adtech-demo"
    elif [ -d "${dist_path}/browser" ]; then
        build_output="${dist_path}/browser"
    else
        # Just use the dist directory if specific subdirectories aren't found
        build_output="${dist_path}"
    fi
    
    print_status "Using build output directory: $build_output"
    
    # Check if the build output directory has files
    if [ ! -d "$build_output" ] || [ -z "$(ls -A "$build_output" 2>/dev/null)" ]; then
        print_error "Build output directory is empty or not found: $build_output"
        cd - > /dev/null
        return 1
    fi
    
    print_status "✅ Build completed successfully!"
    print_status "Build output directory: $build_output"
    print_status "Contents of build output:"
    ls -la "$build_output" 2>/dev/null
    
    # Deploy to S3
    print_status "🚀 Deploying to S3 bucket: $ui_bucket"
    
    # Count files to upload
    local file_count=$(find "$build_output" -type f | wc -l)
    print_status "Found $file_count files to upload"
    
    # Empty the bucket first (preserve config-versions)
    print_status "Clearing existing files from S3 bucket (preserving config-versions)..."
    
    # Get list of all objects except config-versions
    local objects_to_delete=$(aws_cmd s3api list-objects-v2 --bucket "$ui_bucket" --region "$AWS_REGION" --query 'Contents[?!starts_with(Key, `config-versions/`)].Key' --output text 2>/dev/null || echo "")
    
    if [ -n "$objects_to_delete" ] && [ "$objects_to_delete" != "None" ]; then
        # Delete objects that are not in config-versions folder
        for obj in $objects_to_delete; do
            if [[ "$obj" != config-versions/* ]]; then
                aws_cmd s3 rm "s3://$ui_bucket/$obj" --region "$AWS_REGION" 2>/dev/null || true
            fi
        done
        print_status "Cleared UI bucket contents (preserved config-versions folder)"
    else
        print_status "UI bucket contains only config-versions or is empty"
    fi
    
    # Sync files to S3
    print_status "📤 Uploading files to S3 bucket: $ui_bucket"
    if aws_cmd s3 sync "$build_output" "s3://$ui_bucket" --delete --region "$AWS_REGION"; then
        print_status "✅ Successfully uploaded files to S3!"
    else
        print_error "❌ Failed to sync files to S3"
        cd - > /dev/null
        return 1
    fi
    
    # Return to original directory
    cd - > /dev/null
    
    # Invalidate CloudFront cache if distribution ID is available
    if [ -n "$cloudfront_id" ] && [ "$cloudfront_id" != "None" ]; then
        print_status "Invalidating CloudFront cache..."
        if aws_cmd cloudfront create-invalidation --distribution-id "$cloudfront_id" --paths "/*" --region "$AWS_REGION" > /dev/null; then
            print_status "CloudFront invalidation created successfully"
        else
            print_warning "Failed to create CloudFront invalidation. Cache may serve old content."
        fi
    fi
    
    print_success "✅ Angular UI deployed successfully!"
    print_status "🌐 UI URL: $ui_url"
    print_status "📝 Note: It may take a few minutes for the CloudFront invalidation to complete"
    
    # Copy tab configurations to S3 creatives bucket
    copy_tab_configurations_to_s3
    
    return 0
}

# Function to cleanup the entire ecosystem
cleanup_ecosystem() {
    print_step "🧹 ECOSYSTEM CLEANUP"
    print_warning "This will delete ALL resources created by this deployment script."
    print_warning "This action is IRREVERSIBLE and will permanently delete:"
    print_warning "  - All AgentCore agents, runtimes, ECR repositories, and IAM roles"
    print_warning "  - AdCP MCP Gateway Lambda functions and resources"
    print_warning "  - All knowledge bases and data sources"
    print_warning "  - All S3 bucket contents"
    print_warning "  - All CloudFormation stacks"
    print_warning "  - All OpenSearch vector indices"
    print_warning "  - All Docker images (local)"
    echo ""
    
    # Load unique ID - prioritize user-provided ID, then fall back to file
    if [ -n "$UNIQUE_ID" ]; then
        print_status "Using user-provided unique ID: $UNIQUE_ID"
    else
        local id_file="${PROJECT_ROOT}/.unique-id-${STACK_PREFIX}-${AWS_REGION}"
        if [ -f "$id_file" ]; then
            UNIQUE_ID=$(cat "$id_file" 2>/dev/null)
            print_status "Using unique ID from file: $UNIQUE_ID"
        else
            print_error "Unique ID not provided and file not found."
            print_error "Please either:"
            print_error "  1. Provide --unique-id parameter"
            print_error "  2. Run cleanup from the same directory where deployment was run"
            print_error "Expected file: $id_file"
            exit 1
        fi
    fi
    
    # Final confirmation
    echo ""
    print_warning "⚠️  FINAL CONFIRMATION REQUIRED ⚠️"
    printf "Type 'DELETE' (in capitals) to confirm complete ecosystem cleanup: "
    read -r confirmation
    
    if [ "$confirmation" != "DELETE" ]; then
        print_status "Cleanup cancelled by user"
        return 0
    fi
    
    print_status "Starting ecosystem cleanup..."
    echo ""
    
    # Step 1: Clean up AgentCore agents and resources
    if ! cleanup_agentcore_agents; then
        print_warning "⚠️  AgentCore cleanup had some issues, but continuing with deployment cleanup..."
    fi
    
    # Step 2: Clean up AdCP MCP Gateway
    cleanup_adcp_gateway
    
    # Step 3: Delete data sources
    cleanup_data_sources
    
    # Step 4: Delete knowledge bases
    cleanup_knowledge_bases
    
    # Step 5: Delete vector indices
    cleanup_vector_indices
    
    # Step 6: Empty synthetic data bucket
    cleanup_synthetic_data_bucket
    
    # Step 7: Empty UI bucket
    cleanup_ui_bucket
    
    # Step 8: Empty generated content bucket
    cleanup_generated_content_bucket
    
    # Step 9: Delete infrastructure stack
    cleanup_infrastructure_stack
    
    # Final cleanup
    cleanup_temp_files
    
    print_success "🎉 ECOSYSTEM CLEANUP COMPLETED!"
    print_status "All resources have been successfully removed."
}

# Function to cleanup AdCP MCP Gateway
cleanup_adcp_gateway() {
    print_step "2. Cleaning up AdCP MCP Gateway..."
    
    local gateway_info_file="${PROJECT_ROOT}/.adcp-gateway-${STACK_PREFIX}-${UNIQUE_ID}.json"
    local gateway_name="${STACK_PREFIX}-adcp-gateway-${UNIQUE_ID}"
    local lambda_name="${STACK_PREFIX}-adcp-handler-${UNIQUE_ID}"
    local found_gateway=""
    local found_gateway_id=""
    
    # Query AWS directly for gateways matching our stack prefix
    print_status "Checking for MCP Gateways matching ${gateway_name}..."
    
    local gateways_json=$(aws_cmd bedrock-agentcore-control list-gateways --region "$AWS_REGION" 2>/dev/null || echo '{"items":[]}')
    
    # Find gateway matching our naming pattern
    found_gateway_id=$(echo "$gateways_json" | $PYTHON_CMD -c "
import json, sys
try:
    data = json.load(sys.stdin)
    for gw in data.get('items', []):
        if gw.get('name', '') == '${gateway_name}':
            print(gw.get('gatewayId', ''))
            break
except:
    pass
" 2>/dev/null || echo "")
    
    # Check if Lambda function exists
    local lambda_exists=""
    if aws_cmd lambda get-function --function-name "$lambda_name" --region "$AWS_REGION" > /dev/null 2>&1; then
        lambda_exists="true"
    fi
    
    # Check if IAM role exists
    local role_name="${STACK_PREFIX}-adcp-lambda-role-${UNIQUE_ID}"
    local role_exists=""
    if aws_cmd iam get-role --role-name "$role_name" > /dev/null 2>&1; then
        role_exists="true"
    fi
    
    # If nothing found, skip cleanup
    if [ -z "$found_gateway_id" ] && [ -z "$lambda_exists" ] && [ -z "$role_exists" ]; then
        print_status "No AdCP Gateway resources found for ${STACK_PREFIX}-${UNIQUE_ID}, skipping..."
        rm -f "$gateway_info_file" 2>/dev/null || true
        return 0
    fi
    
    # Show what was found
    print_status "Found AdCP Gateway resources:"
    [ -n "$found_gateway_id" ] && print_status "  - MCP Gateway: $found_gateway_id"
    [ -n "$lambda_exists" ] && print_status "  - Lambda function: $lambda_name"
    [ -n "$role_exists" ] && print_status "  - IAM role: $role_name"
    
    printf "Delete AdCP MCP Gateway resources for stack ${STACK_PREFIX}-${UNIQUE_ID}? (y/N): "
    read -r response
    if [[ ! "$response" =~ ^[Yy]$ ]]; then
        print_status "Skipping AdCP Gateway cleanup"
        return 0
    fi
    
    # Delete gateway targets first, then gateway
    if [ -n "$found_gateway_id" ]; then
        print_status "Deleting MCP Gateway targets for: $found_gateway_id"
        
        # List and delete all targets for this gateway
        local targets_json=$(aws_cmd bedrock-agentcore-control list-gateway-targets \
            --gateway-identifier "$found_gateway_id" \
            --region "$AWS_REGION" 2>/dev/null || echo '{"items":[]}')
        
        local target_ids=$(echo "$targets_json" | $PYTHON_CMD -c "
import json, sys
try:
    data = json.load(sys.stdin)
    for target in data.get('items', []):
        print(target.get('targetId', ''))
except:
    pass
" 2>/dev/null || echo "")
        
        for target_id in $target_ids; do
            if [ -n "$target_id" ]; then
                print_status "  Deleting target: $target_id"
                if aws_cmd bedrock-agentcore-control delete-gateway-target \
                    --gateway-identifier "$found_gateway_id" \
                    --target-id "$target_id" \
                    --region "$AWS_REGION" 2>/dev/null; then
                    print_success "  ✅ Target deleted: $target_id"
                else
                    print_warning "  ⚠️  Failed to delete target: $target_id"
                fi
            fi
        done
        
        # Delete the gateway
        print_status "Deleting MCP Gateway: $found_gateway_id"
        if aws_cmd bedrock-agentcore-control delete-gateway \
            --gateway-identifier "$found_gateway_id" \
            --region "$AWS_REGION" 2>/dev/null; then
            print_success "✅ MCP Gateway deleted: $found_gateway_id"
        else
            print_warning "⚠️  Failed to delete MCP Gateway: $found_gateway_id"
        fi
    fi
    
    # Delete Lambda function
    if [ -n "$lambda_exists" ]; then
        print_status "Deleting Lambda function: $lambda_name"
        if aws_cmd lambda delete-function --function-name "$lambda_name" --region "$AWS_REGION" 2>/dev/null; then
            print_success "✅ Lambda function deleted: $lambda_name"
        else
            print_warning "⚠️  Failed to delete Lambda function: $lambda_name"
        fi
    fi
    
    # Delete SSM parameter
    local ssm_param_name="/${STACK_PREFIX}/adcp_gateway/${UNIQUE_ID}"
    print_status "Deleting SSM parameter: $ssm_param_name"
    if aws_cmd ssm delete-parameter --name "$ssm_param_name" --region "$AWS_REGION" 2>/dev/null; then
        print_success "✅ SSM parameter deleted: $ssm_param_name"
    else
        print_warning "⚠️  SSM parameter not found or already deleted: $ssm_param_name"
    fi
    
    # Delete IAM role for Lambda
    if [ -n "$role_exists" ]; then
        print_status "Deleting IAM role: $role_name"
        
        # First detach policies
        local attached_policies=$(aws_cmd iam list-attached-role-policies --role-name "$role_name" --query 'AttachedPolicies[*].PolicyArn' --output text 2>/dev/null || echo "")
        for policy_arn in $attached_policies; do
            if [ -n "$policy_arn" ]; then
                aws_cmd iam detach-role-policy --role-name "$role_name" --policy-arn "$policy_arn" 2>/dev/null || true
            fi
        done
        
        # Delete inline policies
        local inline_policies=$(aws_cmd iam list-role-policies --role-name "$role_name" --query 'PolicyNames[*]' --output text 2>/dev/null || echo "")
        for policy_name in $inline_policies; do
            if [ -n "$policy_name" ]; then
                aws_cmd iam delete-role-policy --role-name "$role_name" --policy-name "$policy_name" 2>/dev/null || true
            fi
        done
        
        # Delete the role
        if aws_cmd iam delete-role --role-name "$role_name" 2>/dev/null; then
            print_success "✅ IAM role deleted: $role_name"
        else
            print_warning "⚠️  Failed to delete IAM role: $role_name"
        fi
    fi
    
    # Remove local tracking file
    rm -f "$gateway_info_file" 2>/dev/null || true
    print_success "✅ AdCP Gateway cleanup completed"
    
    return 0
}

# Function to cleanup AgentCore agents and resources
cleanup_agentcore_agents() {
    print_step "1. Cleaning up AgentCore agents and resources..."
    
    printf "Delete AgentCore agents and resources for stack ${STACK_PREFIX}-${UNIQUE_ID}? (y/N): "
    read -r response
    if [[ ! "$response" =~ ^[Yy]$ ]]; then
        print_status "Skipping AgentCore cleanup"
        return 0
    fi
    
    # Setup Python environment for AgentCore cleanup operations
    setup_python_environment
    
    print_status "Cleaning up AgentCore agents using AWS SDK..."
    
    "$VENV_PATH/bin/python" << EOF
import json
import boto3
import sys
import os
import subprocess
from botocore.exceptions import ClientError

class AgentCoreCleanup:
    """Class for cleaning up AgentCore agents and resources"""
    
    def __init__(self, region_name=None, aws_profile=None, stack_prefix=None, unique_id=None):
        """Initialize the cleanup manager"""
        if aws_profile:
            print(f"Using AWS profile: {aws_profile}")
            boto3_session = boto3.session.Session(profile_name=aws_profile)
        else:
            print("Using default AWS credentials")
            boto3_session = boto3.session.Session()
        
        self.session = boto3_session
        self.region_name = region_name or boto3_session.region_name or 'us-east-1'
        self.stack_prefix = stack_prefix or 'sim'
        self.unique_id = unique_id or ''
        print(f"Using AWS region: {self.region_name}")
        print(f"Using stack prefix: {self.stack_prefix}")
        print(f"Using unique ID: {self.unique_id}")
        
        # AWS clients
        try:
            self.agentcore_client = boto3_session.client('bedrock-agentcore-control', region_name=self.region_name)
            self.ecr_client = boto3_session.client('ecr', region_name=self.region_name)
            self.iam_client = boto3_session.client('iam', region_name=self.region_name)
            self.sts_client = boto3_session.client('sts', region_name=self.region_name)
            print("✅ AWS clients initialized successfully")
        except Exception as e:
            print(f"❌ Failed to initialize AWS clients: {e}")
            raise
    
    def list_agent_runtimes(self):
        """List all AgentCore agent runtimes"""
        try:
            response = self.agentcore_client.list_agent_runtimes()
            return response.get('agentRuntimes', [])
        except ClientError as e:
            print(f"⚠️  Error listing agent runtimes: {e}")
            return []
    
    def delete_agent_runtime(self, runtime_id, runtime_name):
        """Delete an AgentCore agent runtime"""
        try:
            print(f"  Deleting AgentCore runtime: {runtime_name} ({runtime_id})")
            self.agentcore_client.delete_agent_runtime(agentRuntimeId=runtime_id)
            print(f"  ✅ Deleted AgentCore runtime: {runtime_name}")
            return True
        except ClientError as e:
            print(f"  ❌ Failed to delete runtime {runtime_name}: {e}")
            return False
    
    def list_ecr_repositories(self):
        """List ECR repositories that match AgentCore naming pattern with stack prefix and unique ID"""
        try:
            response = self.ecr_client.describe_repositories()
            repositories = response.get('repositories', [])
            
            # Filter repositories that match AgentCore naming pattern with stack prefix and unique ID
            agentcore_repos = []
            for repo in repositories:
                repo_name = repo['repositoryName']
                # Only include repositories that contain both stack prefix and unique ID
                if (repo_name.startswith('agentcore-') and 
                    self.stack_prefix in repo_name and 
                    self.unique_id in repo_name):
                    agentcore_repos.append(repo)
            
            return agentcore_repos
        except ClientError as e:
            print(f"⚠️  Error listing ECR repositories: {e}")
            return []
    
    def delete_ecr_repository(self, repo_name):
        """Delete an ECR repository and all its images"""
        try:
            print(f"  Deleting ECR repository: {repo_name}")
            
            # First, delete all images in the repository
            try:
                images_response = self.ecr_client.list_images(repositoryName=repo_name)
                image_ids = images_response.get('imageIds', [])
                
                if image_ids:
                    print(f"    Deleting {len(image_ids)} images from repository")
                    self.ecr_client.batch_delete_image(
                        repositoryName=repo_name,
                        imageIds=image_ids
                    )
                    print(f"    ✅ Deleted {len(image_ids)} images")
            except ClientError as e:
                print(f"    ⚠️  Could not delete images: {e}")
            
            # Delete the repository
            self.ecr_client.delete_repository(
                repositoryName=repo_name,
                force=True  # Force delete even if it contains images
            )
            print(f"  ✅ Deleted ECR repository: {repo_name}")
            return True
        except ClientError as e:
            print(f"  ❌ Failed to delete ECR repository {repo_name}: {e}")
            return False
    
    def list_agentcore_iam_roles(self):
        """List IAM roles created for AgentCore agents with stack prefix and unique ID"""
        try:
            response = self.iam_client.list_roles()
            roles = response.get('Roles', [])
            
            # Filter roles that match AgentCore naming pattern with stack prefix and unique ID
            agentcore_roles = []
            for role in roles:
                role_name = role['RoleName']
                if ((role_name.startswith('AgentCoreRole-') or 'agentcore' in role_name.lower()) and 
                    self.stack_prefix in role_name and 
                    self.unique_id in role_name):
                    agentcore_roles.append(role)
            
            return agentcore_roles
        except ClientError as e:
            print(f"⚠️  Error listing IAM roles: {e}")
            return []
    
    def delete_iam_role(self, role_name):
        """Delete an IAM role and its attached policies"""
        try:
            print(f"  Deleting IAM role: {role_name}")
            
            # First, detach all attached policies
            try:
                attached_policies = self.iam_client.list_attached_role_policies(RoleName=role_name)
                for policy in attached_policies.get('AttachedPolicies', []):
                    policy_arn = policy['PolicyArn']
                    print(f"    Detaching policy: {policy_arn}")
                    self.iam_client.detach_role_policy(RoleName=role_name, PolicyArn=policy_arn)
                    
                    # If it's a custom policy we created, delete it
                    if 'ECRPolicy' in policy_arn:
                        try:
                            print(f"    Deleting custom policy: {policy_arn}")
                            self.iam_client.delete_policy(PolicyArn=policy_arn)
                            print(f"    ✅ Deleted custom policy: {policy_arn}")
                        except ClientError as e:
                            print(f"    ⚠️  Could not delete policy {policy_arn}: {e}")
            except ClientError as e:
                print(f"    ⚠️  Could not list attached policies: {e}")
            
            # Delete inline policies
            try:
                inline_policies = self.iam_client.list_role_policies(RoleName=role_name)
                for policy_name in inline_policies.get('PolicyNames', []):
                    print(f"    Deleting inline policy: {policy_name}")
                    self.iam_client.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
            except ClientError as e:
                print(f"    ⚠️  Could not delete inline policies: {e}")
            
            # Delete the role
            self.iam_client.delete_role(RoleName=role_name)
            print(f"  ✅ Deleted IAM role: {role_name}")
            return True
        except ClientError as e:
            print(f"  ❌ Failed to delete IAM role {role_name}: {e}")
            return False
    
    def cleanup_docker_images(self):
        """Clean up local Docker images for AgentCore agents"""
        try:
            print("🐳 Cleaning up local Docker images...")
            
            # Check if Docker is available
            result = subprocess.run(['docker', '--version'], capture_output=True, text=True)
            if result.returncode != 0:
                print("  ⚠️  Docker not available, skipping local image cleanup")
                return True
            
            # List Docker images that match AgentCore pattern
            result = subprocess.run(['docker', 'images', '--format', 'table {{.Repository}}:{{.Tag}}'], 
                                  capture_output=True, text=True)
            
            if result.returncode == 0:
                images = result.stdout.strip().split('\\n')[1:]  # Skip header
                agentcore_images = []
                
                for image in images:
                    if (image and 'agentcore-' in image and 
                        self.stack_prefix in image and 
                        self.unique_id in image):
                        agentcore_images.append(image)
                
                if agentcore_images:
                    print(f"  Found {len(agentcore_images)} AgentCore Docker images to remove")
                    for image in agentcore_images:
                        print(f"    Removing Docker image: {image}")
                        remove_result = subprocess.run(['docker', 'rmi', '-f', image], 
                                                     capture_output=True, text=True)
                        if remove_result.returncode == 0:
                            print(f"    ✅ Removed Docker image: {image}")
                        else:
                            print(f"    ⚠️  Could not remove Docker image {image}: {remove_result.stderr}")
                else:
                    print("  No AgentCore Docker images found")
            else:
                print("  ⚠️  Could not list Docker images")
            
            return True
        except Exception as e:
            print(f"  ⚠️  Error cleaning up Docker images: {e}")
            return True  # Don't fail the entire cleanup for Docker issues
    
    def cleanup_all_agentcore_resources(self):
        """Clean up all AgentCore resources"""
        success = True
        
        print("🧹 Starting comprehensive AgentCore cleanup...")
        
        # 1. Delete AgentCore agent runtimes
        print("\\n1️⃣  Deleting AgentCore agent runtimes...")
        runtimes = self.list_agent_runtimes()
        
        if runtimes:
            filtered_runtimes = []
            for runtime in runtimes:
                runtime_name = runtime['agentRuntimeName']
                # Filter by both stack prefix and unique ID
                if (self.stack_prefix in runtime_name and self.unique_id in runtime_name):
                    filtered_runtimes.append(runtime)
                else:
                    print(f"  Skipping runtime {runtime_name} (doesn't match {self.stack_prefix}-{self.unique_id})")
            
            if filtered_runtimes:
                print(f"  Found {len(filtered_runtimes)} matching AgentCore runtimes to delete")
                for runtime in filtered_runtimes:
                    runtime_id = runtime['agentRuntimeId']
                    runtime_name = runtime['agentRuntimeName']
                    
                    if not self.delete_agent_runtime(runtime_id, runtime_name):
                        print(f"  ⚠️  Failed to delete runtime {runtime_name}, but continuing...")
                        # Don't fail the entire cleanup for individual runtime failures
            else:
                print(f"  No AgentCore runtimes found matching {self.stack_prefix}-{self.unique_id}")
        else:
            print("  No AgentCore runtimes found")
        
        # 2. Delete ECR repositories
        print("\\n2️⃣  Deleting ECR repositories...")
        repositories = self.list_ecr_repositories()
        
        if repositories:
            print(f"  Found {len(repositories)} matching ECR repositories to delete")
            for repo in repositories:
                repo_name = repo['repositoryName']
                if not self.delete_ecr_repository(repo_name):
                    print(f"  ⚠️  Failed to delete ECR repository {repo_name}, but continuing...")
                    # Don't fail the entire cleanup for individual repository failures
        else:
            print(f"  No AgentCore ECR repositories found matching {self.stack_prefix}-{self.unique_id}")
        
        # 3. Delete IAM roles
        print("\\n3️⃣  Deleting IAM roles...")
        roles = self.list_agentcore_iam_roles()
        
        if roles:
            print(f"  Found {len(roles)} matching IAM roles to delete")
            for role in roles:
                role_name = role['RoleName']
                if not self.delete_iam_role(role_name):
                    print(f"  ⚠️  Failed to delete IAM role {role_name}, but continuing...")
                    # Don't fail the entire cleanup for individual role failures
        else:
            print(f"  No AgentCore IAM roles found matching {self.stack_prefix}-{self.unique_id}")
        
        # 4. Clean up Docker images
        print("\\n4️⃣  Cleaning up Docker images...")
        if not self.cleanup_docker_images():
            print("  ⚠️  Docker cleanup had issues, but continuing...")
            # Don't fail the entire cleanup for Docker issues
        
        print("\\n✅ AgentCore cleanup completed (some individual failures may have occurred)")
        return True  # Always return success since we handle individual failures gracefully

# Initialize cleanup manager
try:
    cleanup_manager = AgentCoreCleanup(
        region_name='${AWS_REGION}',
        aws_profile='${AWS_PROFILE}' if '${AWS_PROFILE}' else None,
        stack_prefix='${STACK_PREFIX}',
        unique_id='${UNIQUE_ID}'
    )
    
    # Clean up all AgentCore resources
    success = cleanup_manager.cleanup_all_agentcore_resources()
    
    if success:
        print("\\n✅ AgentCore cleanup completed successfully")
        sys.exit(0)
    else:
        print("\\n⚠️  Some AgentCore resource cleanups failed, but cleanup completed")
        sys.exit(0)  # Don't fail the script for individual cleanup failures
    
except Exception as e:
    print(f"❌ Error during AgentCore cleanup: {e}")
    print("Continuing with deployment script...")
    sys.exit(0)  # Don't fail the entire deployment for cleanup issues
EOF
    
    # Always return success since we handle failures gracefully
    return 0
    if [ $? -eq 0 ]; then
        print_success "✅ AgentCore agents and resources cleaned up successfully"
    else
        print_warning "⚠️ Some AgentCore resources may not have been cleaned up completely"
        print_status "You may need to manually clean up remaining AgentCore resources in the AWS console"
    fi
}

# Function to cleanup data sources
cleanup_data_sources() {
    print_step "2. Cleaning up knowledge base data sources..."
    
    printf "Delete all knowledge base data sources? (y/N): "
    read -r response
    if [[ ! "$response" =~ ^[Yy]$ ]]; then
        print_status "Skipping data sources cleanup"
        return 0
    fi
    
    # List and delete data source stacks
    local stacks=$(aws_cmd cloudformation list-stacks \
        --region "$AWS_REGION" \
        --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE UPDATE_ROLLBACK_COMPLETE DELETE_FAILED \
        --query "StackSummaries[?contains(StackName, '${STACK_PREFIX}-') && contains(StackName, '-ds')].StackName" \
        --output text 2>/dev/null)
    
    if [ -n "$stacks" ] && [ "$stacks" != "None" ]; then
        print_status "Found data source stacks:"
        for stack in $stacks; do
            print_status "  - $stack"
            
            # First, disable termination protection if enabled
            print_status "  Checking termination protection..."
            local termination_protection=$(aws_cmd cloudformation describe-stacks \
                --stack-name "$stack" \
                --region "$AWS_REGION" \
                --query 'Stacks[0].EnableTerminationProtection' \
                --output text 2>/dev/null || echo "false")
            
            if [ "$termination_protection" = "True" ] || [ "$termination_protection" = "true" ]; then
                print_status "  Disabling termination protection..."
                if aws_cmd cloudformation update-termination-protection \
                    --stack-name "$stack" \
                    --no-enable-termination-protection \
                    --region "$AWS_REGION" 2>/dev/null; then
                    print_status "  ✅ Termination protection disabled"
                else
                    print_warning "  ⚠️  Could not disable termination protection, attempting delete anyway..."
                fi
            fi
            
            # Now delete the stack
            print_status "  Deleting data source stack: $stack"
            local stack_status=$(get_stack_status "$stack")
            local delete_cmd="aws_cmd cloudformation delete-stack --stack-name $stack --region $AWS_REGION"
            
            if [ "$stack_status" = "DELETE_FAILED" ]; then
                delete_cmd="$delete_cmd --deletion-mode FORCE_DELETE_STACK"
                print_status "  Using FORCE_DELETE_STACK mode for DELETE_FAILED stack"
            fi
            
            if eval "$delete_cmd"; then
                print_status "  ✅ Delete initiated for: $stack"
            else
                print_warning "  ⚠️  Failed to delete stack: $stack"
            fi
        done
        print_success "✅ Data sources cleanup initiated"
    else
        print_status "No data source stacks found"
    fi
}

# Function to cleanup knowledge bases
cleanup_knowledge_bases() {
    print_step "3. Cleaning up knowledge bases..."
    
    printf "Delete all knowledge bases? (y/N): "
    read -r response
    if [[ ! "$response" =~ ^[Yy]$ ]]; then
        print_status "Skipping knowledge bases cleanup"
        return 0
    fi
    
    # List and delete knowledge base stacks
    local stacks=$(aws_cmd cloudformation list-stacks \
        --region "$AWS_REGION" \
        --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE UPDATE_ROLLBACK_COMPLETE DELETE_FAILED \
        --query "StackSummaries[?contains(StackName, '${STACK_PREFIX}-') && contains(StackName, '-kb')].StackName" \
        --output text 2>/dev/null)
    
    if [ -n "$stacks" ] && [ "$stacks" != "None" ]; then
        for stack in $stacks; do
            print_status "Processing KB stack: $stack"
            
            # First, disable termination protection if enabled
            print_status "  Checking termination protection..."
            local termination_protection=$(aws_cmd cloudformation describe-stacks \
                --stack-name "$stack" \
                --region "$AWS_REGION" \
                --query 'Stacks[0].EnableTerminationProtection' \
                --output text 2>/dev/null || echo "false")
            
            if [ "$termination_protection" = "True" ] || [ "$termination_protection" = "true" ]; then
                print_status "  Disabling termination protection..."
                if aws_cmd cloudformation update-termination-protection \
                    --stack-name "$stack" \
                    --no-enable-termination-protection \
                    --region "$AWS_REGION" 2>/dev/null; then
                    print_status "  ✅ Termination protection disabled"
                else
                    print_warning "  ⚠️  Could not disable termination protection, attempting delete anyway..."
                fi
            fi
            
            # Now delete the stack
            print_status "  Deleting KB stack: $stack"
            local stack_status=$(get_stack_status "$stack")
            local delete_cmd="aws_cmd cloudformation delete-stack --stack-name $stack --region $AWS_REGION"
            
            if [ "$stack_status" = "DELETE_FAILED" ]; then
                delete_cmd="$delete_cmd --deletion-mode FORCE_DELETE_STACK"
                print_status "  Using FORCE_DELETE_STACK mode for DELETE_FAILED stack"
            fi
            
            if eval "$delete_cmd"; then
                print_status "  ✅ Delete initiated for: $stack"
            else
                print_warning "  ⚠️  Failed to delete stack: $stack"
            fi
        done
        print_success "✅ Knowledge base cleanup initiated"
    else
        print_status "No Knowledge base stacks found"
    fi
}

# Function to cleanup vector indices
cleanup_vector_indices() {
    print_step "4. Cleaning up OpenSearch vector indices..."
    
    local kb_config_file="${PROJECT_ROOT}/cloudformation/generic-configs/knowledgebases/knowledgebases-with-datasources.json"
    
    if [ ! -f "$kb_config_file" ]; then
        print_warning "Knowledge bases config not found, skipping vector indices cleanup"
        return 0
    fi
    
    printf "Delete all OpenSearch vector indices? (y/N): "
    read -r response
    if [[ ! "$response" =~ ^[Yy]$ ]]; then
        print_status "Skipping vector indices cleanup"
        return 0
    fi
    
    # Get OpenSearch collection ID from core infrastructure stack
    local infrastructure_core_stack="${STACK_PREFIX}-infrastructure-core"
    local opensearch_collection_arn=$(get_stack_output "$infrastructure_core_stack" "OpenSearchCollectionArn")
    
    if [ -n "$opensearch_collection_arn" ] && [ "$opensearch_collection_arn" != "None" ]; then
        local collection_id=$(echo "$opensearch_collection_arn" | sed 's|.*/||')
        
        # Extract index names and delete them
        local index_script="${SCRIPT_DIR}/create_vector_indices.py"
        if [ -f "$index_script" ]; then
            # Setup Python environment for cleanup operations
            setup_python_environment
            
            local index_names=$($PYTHON_CMD -c "
import json
with open('${kb_config_file}', 'r') as f:
    kb_configs = json.load(f)
index_names = [kb['index_name'] for kb in kb_configs]
print(' '.join(index_names))
")
            
            local index_cmd="$PYTHON_CMD $index_script --collection-id $collection_id --region $AWS_REGION --action cleanup --indexes $index_names --stack-prefix $STACK_PREFIX"
            
            if [ -n "$AWS_PROFILE" ]; then
                index_cmd="$index_cmd --profile $AWS_PROFILE"
            fi
            
            print_status "Deleting vector indices: $index_names"
            if eval "$index_cmd"; then
                print_success "✅ Vector indices cleanup completed"
            else
                print_warning "⚠️  Some vector indices may not have been deleted"
            fi
        else
            print_warning "Vector index script not found, skipping indices cleanup"
        fi
    else
        print_warning "OpenSearch collection not found, skipping indices cleanup"
    fi
}

# Function to cleanup synthetic data bucket
cleanup_synthetic_data_bucket() {
    print_step "5. Cleaning up synthetic data bucket..."
    
    local infrastructure_core_stack="${STACK_PREFIX}-infrastructure-core"
    local data_bucket=$(get_stack_output "$infrastructure_core_stack" "SyntheticDataBucketName")
    
    if [ -z "$data_bucket" ] || [ "$data_bucket" = "None" ]; then
        print_warning "Synthetic data bucket not found, skipping"
        return 0
    fi
    
    printf "Empty synthetic data bucket ($data_bucket)? (y/N): "
    read -r response
    if [[ ! "$response" =~ ^[Yy]$ ]]; then
        print_status "Skipping synthetic data bucket cleanup"
        return 0
    fi
    
    print_status "Emptying synthetic data bucket: $data_bucket"
    
    # Delete all objects and versions
    aws_cmd s3 rm "s3://$data_bucket" --recursive --region "$AWS_REGION" 2>/dev/null || true
    
    # Delete any versioned objects
    aws_cmd s3api delete-objects \
        --bucket "$data_bucket" \
        --delete "$(aws_cmd s3api list-object-versions \
            --bucket "$data_bucket" \
            --region "$AWS_REGION" \
            --output json \
            --query '{Objects: Versions[].{Key:Key,VersionId:VersionId}}' 2>/dev/null)" \
        --region "$AWS_REGION" 2>/dev/null || true
    
    print_success "✅ Synthetic data bucket emptied"
}

# Function to cleanup UI bucket
cleanup_ui_bucket() {
    print_step "6. Cleaning up UI bucket..."
    
    local infrastructure_core_stack="${STACK_PREFIX}-infrastructure-core"
    local ui_bucket=$(get_stack_output "$infrastructure_core_stack" "UIBucketName")
    
    if [ -z "$ui_bucket" ] || [ "$ui_bucket" = "None" ]; then
        print_warning "UI bucket not found, skipping"
        return 0
    fi
    
    printf "Empty UI bucket ($ui_bucket)? (y/N): "
    read -r response
    if [[ ! "$response" =~ ^[Yy]$ ]]; then
        print_status "Skipping UI bucket cleanup"
        return 0
    fi
    
    print_status "Emptying UI bucket (preserving config-versions): $ui_bucket"
    
    # Delete all objects except config-versions folder
    # First, get list of all objects
    local all_objects=$(aws_cmd s3api list-objects-v2 --bucket "$ui_bucket" --region "$AWS_REGION" --query 'Contents[?!starts_with(Key, `config-versions/`)].Key' --output text 2>/dev/null || echo "")
    
    if [ -n "$all_objects" ] && [ "$all_objects" != "None" ]; then
        # Delete objects that are not in config-versions folder
        for obj in $all_objects; do
            if [[ "$obj" != config-versions/* ]]; then
                aws_cmd s3 rm "s3://$ui_bucket/$obj" --region "$AWS_REGION" 2>/dev/null || true
            fi
        done
        print_status "Deleted UI bucket contents (preserved config-versions folder)"
    else
        print_status "UI bucket is already empty or contains only config-versions"
    fi
    
    print_success "✅ UI bucket cleaned (config-versions preserved)"
}

# Function to cleanup generated content bucket
cleanup_generated_content_bucket() {
    print_step "7. Cleaning up generated content bucket..."
    
    local infrastructure_services_stack="${STACK_PREFIX}-infrastructure-services"
    local content_bucket=$(get_stack_output "$infrastructure_services_stack" "GeneratedContentBucketName")
    
    if [ -z "$content_bucket" ] || [ "$content_bucket" = "None" ]; then
        print_warning "Generated content bucket not found, skipping"
        return 0
    fi
    
    printf "Empty generated content bucket ($content_bucket)? (y/N): "
    read -r response
    if [[ ! "$response" =~ ^[Yy]$ ]]; then
        print_status "Skipping generated content bucket cleanup"
        return 0
    fi
    
    print_status "Emptying generated content bucket: $content_bucket"
    
    # Delete all objects
    aws_cmd s3 rm "s3://$content_bucket" --recursive --region "$AWS_REGION" 2>/dev/null || true
    
    print_success "✅ Generated content bucket emptied"
}

# Function to cleanup infrastructure stack
cleanup_infrastructure_stack() {
    print_step "8. Cleaning up infrastructure stacks..."
    
    local infrastructure_core_stack="${STACK_PREFIX}-infrastructure-core"
    local infrastructure_services_stack="${STACK_PREFIX}-infrastructure-services"
    
    printf "Delete infrastructure stacks (core and services)? (y/N): "
    read -r response
    if [[ ! "$response" =~ ^[Yy]$ ]]; then
        print_status "Skipping infrastructure stacks cleanup"
        return 0
    fi
    
    # Wait for other stacks to be deleted first
    print_status "Waiting 30 seconds for dependent stacks to be deleted..."
    sleep 30
    
    # Delete services stack first (it depends on core stack)
    print_status "Deleting services infrastructure stack: $infrastructure_services_stack"
    local services_stack_status=$(get_stack_status "$infrastructure_services_stack")
    local services_delete_cmd="aws_cmd cloudformation delete-stack --stack-name $infrastructure_services_stack --region $AWS_REGION"
    
    if [ "$services_stack_status" = "DELETE_FAILED" ]; then
        services_delete_cmd="$services_delete_cmd --deletion-mode FORCE_DELETE_STACK"
        print_status "Using FORCE_DELETE_STACK mode for DELETE_FAILED services stack"
    fi
    
    eval "$services_delete_cmd"
    
    print_status "Waiting for services stack deletion to complete..."
    aws_cmd cloudformation wait stack-delete-complete --stack-name "$infrastructure_services_stack" --region "$AWS_REGION"
    
    # Delete core stack second
    print_status "Deleting core infrastructure stack: $infrastructure_core_stack"
    local core_stack_status=$(get_stack_status "$infrastructure_core_stack")
    local core_delete_cmd="aws_cmd cloudformation delete-stack --stack-name $infrastructure_core_stack --region $AWS_REGION"
    
    if [ "$core_stack_status" = "DELETE_FAILED" ]; then
        core_delete_cmd="$core_delete_cmd --deletion-mode FORCE_DELETE_STACK"
        print_status "Using FORCE_DELETE_STACK mode for DELETE_FAILED core stack"
    fi
    
    eval "$core_delete_cmd"
    
    print_status "Waiting for core stack deletion to complete..."
    aws_cmd cloudformation wait stack-delete-complete --stack-name "$infrastructure_core_stack" --region "$AWS_REGION"
    
    print_success "✅ Infrastructure stacks deleted"
}

# Function to cleanup temporary files
cleanup_temp_files() {
    print_step "9. Cleaning up temporary files..."
    
    printf "Delete temporary deployment files? (y/N): "
    read -r response
    if [[ ! "$response" =~ ^[Yy]$ ]]; then
        print_status "Skipping temporary files cleanup"
        return 0
    fi
    
    # Remove temporary files
    local files_to_remove=(
        "${PROJECT_ROOT}/.kb-ids-${STACK_PREFIX}-${UNIQUE_ID}.json"
        "${PROJECT_ROOT}/.agentcore-agents-${STACK_PREFIX}-${UNIQUE_ID}.json"
        "${PROJECT_ROOT}/.memory-record-${STACK_PREFIX}-${UNIQUE_ID}.json"
        "${PROJECT_ROOT}/.unique-id-${STACK_PREFIX}-${AWS_REGION}"
        "${PROJECT_ROOT}/.bucket-suffix-${UNIQUE_ID}-${AWS_REGION}"
    )
    
    # Also remove AgentCore-specific temporary files
    local agentcore_temp_files=(
        "${PROJECT_ROOT}/agentcore/docker/config/"
    )
    
    for file in "${files_to_remove[@]}"; do
        if [ -f "$file" ]; then
            rm -f "$file"
            print_status "Removed: $file"
        fi
    done
    
    # Clean up AgentCore temporary files and directories
    for item in "${agentcore_temp_files[@]}"; do
        if [ -d "$item" ]; then
            rm -rf "$item"
            print_status "Removed directory: $item"
        elif [ -f "$item" ]; then
            rm -f "$item"
            print_status "Removed file: $item"
        fi
    done
    
    print_success "✅ Temporary files cleaned up"
    
    # Also cleanup Python virtual environment
    cleanup_python_environment
}

# Function to parse command line arguments
parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --stack-prefix)
                STACK_PREFIX="$2"
                shift 2
                ;;
            --unique-id)
                UNIQUE_ID="$2"
                shift 2
                ;;
            --region)
                AWS_REGION="$2"
                shift 2
                ;;
            --profile)
                AWS_PROFILE="$2"
                shift 2
                ;;
            --demo-email)
                DEMO_USER_EMAIL="$2"
                shift 2
                ;;
            --image-model)
                IMAGE_GENERATION_MODEL="$2"
                shift 2
                ;;
            --resume-at)
                RESUME_AT_STEP="$2"
                shift 2
                ;;
            --non-interactive)
                INTERACTIVE_MODE=false
                shift
                ;;
            --skip-confirmations)
                SKIP_CONFIRMATIONS=true
                INTERACTIVE_MODE=false
                shift
                ;;
            --cleanup)
                CLEANUP_MODE=true
                shift
                ;;
            -h|--help)
                show_usage
                exit 0
                ;;
            *)
                print_warning "Unknown argument: $1"
                shift
                ;;
        esac
    done
}

# Function to show usage
show_usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "OPTIONS:"
    echo "  --stack-prefix PREFIX    Stack naming prefix (default: sim)"
    echo "  --unique-id ID           Use specific unique ID instead of loading from file"
    echo "  --region REGION          AWS region (default: us-east-1)"
    echo "  --profile PROFILE        AWS CLI profile to use"
    echo "  --demo-email EMAIL       Email for demo user account"
    echo "  --image-model MODEL      Image generation model ID (default: amazon.nova-canvas-v1:0)"
    echo "  --resume-at STEP         Resume deployment at specific step (1-13)"
    echo "  --non-interactive        Disable interactive prompts"
    echo "  --skip-confirmations     Skip all update confirmations (implies --non-interactive)"
    echo "  --cleanup                Run cleanup mode to delete all resources"
    echo "  -h, --help               Show this help message"
    echo ""
    echo "EXAMPLES:"
    echo "  $0                                    # Deploy with defaults"
    echo "  $0 --stack-prefix my-demo            # Deploy with custom prefix"
    echo "  $0 --unique-id abc123                # Use specific unique ID"
    echo "  $0 --region us-east-1                # Deploy in specific region"
    echo "  $0 --resume-at 5                     # Resume from step 5"
    echo "  $0 --resume-at 6 --skip-confirmations # Resume from step 6 without update confirmations"
    echo "  $0 --cleanup                         # Delete all resources"
    echo "  $0 --cleanup --unique-id abc123      # Delete resources with specific unique ID"
    echo ""
    echo "NOTES:"
    echo "  - This script automatically creates a Python virtual environment (.venv-deployment)"
    echo "  - Required Python packages (boto3, botocore) are installed automatically"
    echo "  - The virtual environment is cleaned up during cleanup operations"
    echo "  - For cleanup: --unique-id is useful when running from a different directory"
    echo "    or when the .unique-id file is not available"
}

# Function to prompt for deployment type
prompt_deployment_type() {
    if [ "$INTERACTIVE_MODE" = true ]; then
        echo ""
        print_status "=== DEPLOYMENT TYPE ==="
        echo "Are you deploying a new clean stack? (Y/n)"
        echo "A new stack will generate a new unique ID and deploy all resources from scratch."
        echo "An existing stack will attempt to use the existing unique ID and update resources."
        echo ""
        
        read -p "Deploy new clean stack? (Y/n): " response
        case $response in
            [Nn]*)
                CLEAN_DEPLOYMENT=false
                print_status "Will attempt to use existing deployment"
                ;;
            *)
                CLEAN_DEPLOYMENT=true
                print_status "Will deploy new clean stack"
                ;;
        esac
    fi
}

# Function to prompt for image generation model
prompt_image_model() {
    if [ "$INTERACTIVE_MODE" = true ] && [ -z "$IMAGE_GENERATION_MODEL" ]; then
        echo ""
        print_status "=== IMAGE GENERATION MODEL ==="
        echo "Enter the model ID for image generation (blank for default):"
        echo "Default: amazon.nova-canvas-v1:0"
        echo "Other options: stability.sd3-5-large-v1:0, stability.stable-diffusion-xl-v1"
        echo ""
        
        read -p "Image generation model ID: " model_input
        if [ -n "$model_input" ]; then
            IMAGE_GENERATION_MODEL="$model_input"
        else
            IMAGE_GENERATION_MODEL="amazon.nova-canvas-v1:0"
        fi
    fi
    
    print_status "Using image generation model: ${IMAGE_GENERATION_MODEL:-amazon.nova-canvas-v1:0}"
}

# Function to cleanup Python virtual environment
cleanup_python_environment() {
    if [ -d "$VENV_PATH" ]; then
        print_status "Cleaning up Python virtual environment..."
        rm -rf "$VENV_PATH"
        print_success "✅ Python virtual environment cleaned up"
    fi
}

# Function to confirm deployment steps
confirm_deployment_steps() {
    print_status "=========================================="
    print_status "📋 DEPLOYMENT STEPS OVERVIEW"
    print_status "=========================================="
    echo ""
    
    local steps=(
        "Phase 1: Check and adjust AWS service quotas"
        "Phase 2: Deploy infrastructure (Core: S3, OpenSearch, Cognito; Services: Lambda, DynamoDB)"
        "Phase 3: Deploy Lambda functions and migrate visualization data"
        "Phase 4: Deploy knowledge bases with organized data sources"
        "Phase 5: Sync data sources (start ingestion jobs)"
        "Phase 6: Deploy AdCP MCP Gateway for agent collaboration (must be before agents)"
        "Phase 7: Detect and deploy AgentCore agents (uses gateway URL from step 6)"
        "Phase 8: Generate UI configuration"
    )
    
    print_status "The following steps will be executed:"
    echo ""
    
    for i in "${!steps[@]}"; do
        local step_num=$((i + 1))
        if [ "$step_num" -ge "$RESUME_AT_STEP" ]; then
            print_status "  ✅ ${steps[$i]}"
        else
            print_warning "  ⏭️  ${steps[$i]} (skipped - resuming at step $RESUME_AT_STEP)"
        fi
    done
    
    echo ""
    print_status "Configuration Summary:"
    print_status "  Stack Prefix: $STACK_PREFIX"
    print_status "  Unique ID: $UNIQUE_ID"
    print_status "  AWS Region: $AWS_REGION"
    print_status "  AWS Profile: ${AWS_PROFILE:-default}"
    print_status "  Demo Email: ${DEMO_USER_EMAIL:-will be prompted}"
    print_status "  Image Model: $IMAGE_GENERATION_MODEL"
    print_status "  Clean Deployment: $CLEAN_DEPLOYMENT"
    
    echo ""
    print_warning "⚠️  This deployment will create AWS resources that may incur costs."
    print_warning "⚠️  Estimated deployment time: 45-60 minutes"
    echo ""
    
    printf "Do you want to proceed with the deployment? (y/N): "
    read -r response
    if [[ ! "$response" =~ ^[Yy]$ ]]; then
        print_status "Deployment cancelled by user"
        exit 0
    fi
    
    echo ""
    print_success "🚀 Starting deployment..."
    echo ""
}

# Main deployment function
main() {
    # Parse command line arguments first
    parse_args "$@"
    
    # Check if cleanup mode
    if [ "$CLEANUP_MODE" = true ]; then
        print_status "=========================================="
        print_status "🧹 AGENTIC ADVERTISING ECOSYSTEM CLEANUP"
        print_status "=========================================="
        echo ""
        
        print_status "Configuration:"
        print_status "  Unique id: $$UNIQUE_ID"
        print_status "  Stack Prefix: $STACK_PREFIX"
        print_status "  AWS Region: $AWS_REGION"
        print_status "  AWS Profile: ${AWS_PROFILE:-default}"
        echo ""
        
        cleanup_ecosystem
        return 0
    fi
    
    print_status "=========================================="
    print_status "🚀 AGENTIC ADVERTISING ECOSYSTEM DEPLOYMENT"
    print_status "=========================================="
    echo ""
    
    # Interactive prompts
    prompt_deployment_type
    prompt_image_model
    
    print_status "Configuration:"
    print_status "  Stack Prefix: $STACK_PREFIX"
    print_status "  AWS Region: $AWS_REGION"
    print_status "  Unique id: $$UNIQUE_ID"
    print_status "  AWS Profile: ${AWS_PROFILE:-default}"
    print_status "  Clean Deployment: $CLEAN_DEPLOYMENT"
    print_status "  Image Model: ${IMAGE_GENERATION_MODEL:-amazon.nova-canvas-v1:0}"
    print_status "  Resume at Step: $RESUME_AT_STEP"
    echo ""
    
    # Initialize unique ID
    initialize_unique_id
    
    # DEPLOYMENT PHASES:
    # Phase 1: Check and adjust AWS service quotas
    # Phase 2: Deploy infrastructure (Core: S3, OpenSearch, Cognito; Services: Lambda, DynamoDB)
    # Phase 3: Deploy Lambda functions and migrate visualization data
    # Phase 4: Deploy knowledge bases with organized data sources
    # Phase 5: Sync data sources (start ingestion jobs)
    # Phase 6: Deploy AdCP MCP Gateway for agent collaboration (BEFORE agents!)
    # Phase 7: Detect and deploy AgentCore agents (uses gateway URL from step 6)
    # Phase 8: Generate UI configuration
    
    # Pre-deployment validation
    if [ "$RESUME_AT_STEP" -le 1 ]; then
        validate_deployment_readiness
    fi
    
    # Step-by-step confirmation
    if [ "$INTERACTIVE_MODE" = true ]; then
        confirm_deployment_steps
    fi
    
    # Export environment variables for Python subprocesses
    export INTERACTIVE_MODE
    export SKIP_CONFIRMATIONS
    
    # Execute deployment steps
    if [ "$RESUME_AT_STEP" -le 1 ]; then
        check_and_adjust_service_quotas
    fi
    
    if [ "$RESUME_AT_STEP" -le 2 ]; then
        deploy_infrastructure
        
    fi
    if [ "$RESUME_AT_STEP" -le 3 ]; then
        deploy_lambda_functions
    fi
    
    if [ "$RESUME_AT_STEP" -le 4 ]; then
        deploy_knowledge_bases
    fi
    
    if [ "$RESUME_AT_STEP" -le 5 ]; then
        sync_data_sources
    fi
    
    if [ "$RESUME_AT_STEP" -le 6 ]; then
        # Deploy AdCP MCP Gateway FIRST so agents can use it
        deploy_adcp_mcp_gateway
    fi
    
    if [ "$RESUME_AT_STEP" -le 7 ]; then
        # Deploy AgentCore agents AFTER gateway so ADCP_GATEWAY_URL is available
        detect_and_deploy_agentcore_agents
    fi
    
    if [ "$RESUME_AT_STEP" -le 8 ]; then
        generate_ui_config
    fi
    # Final summary
    echo ""
    print_success "🎉 DEPLOYMENT COMPLETED SUCCESSFULLY!"
    print_status "=========================================="
    echo ""
    
    print_status "📋 DEPLOYMENT SUMMARY:"
    print_status "  ✅ Core Infrastructure: Deployed (S3, OpenSearch, Cognito)"
    print_status "  ✅ Services Infrastructure: Deployed (Lambda, DynamoDB)"
    print_status "  ✅ Lambda Functions: Packaged and Deployed"
    print_status "  ✅ OpenSearch Indices: Created for each Knowledge Base"
    print_status "  ✅ Knowledge Bases: Deployed with Data Sources"
    print_status "  ✅ Data Source Ingestion: Triggered"
    print_status "  ✅ Visualization Data: Migrated to DynamoDB for AgentCore agents"
    
    # Check if AdCP Gateway was deployed
    local gateway_info_file="${PROJECT_ROOT}/.adcp-gateway-${STACK_PREFIX}-${UNIQUE_ID}.json"
    if [ -f "$gateway_info_file" ]; then
        print_status "  ✅ AdCP MCP Gateway: Deployed for agent collaboration"
    fi
    # Check if AgentCore agents were actually deployed by looking at the file
    local agentcore_info_file="${PROJECT_ROOT}/.agentcore-agents-${STACK_PREFIX}-${UNIQUE_ID}.json"
    if [ -f "$agentcore_info_file" ]; then
        local agentcore_count=$($PYTHON_CMD -c "
import json
try:
    with open('$agentcore_info_file', 'r') as f:
        data = json.load(f)
    if data.get('skipped', False):
        print('0')
    else:
        deployed_agents = data.get('deployed_agents', [])
        # Count non-empty entries (handle both old string format and new object format)
        count = 0
        for agent in deployed_agents:
            if isinstance(agent, dict) and agent.get('name'):
                count += 1
            elif isinstance(agent, str) and agent.strip():
                count += 1
        print(count)
except:
    print('0')
" 2>/dev/null || echo "0")
    fi
    
    print_status "  ✅ Tab Configurations: Copied to S3 creatives bucket"
    
    # Show memory record ID if available
    local memory_record_file="${PROJECT_ROOT}/.memory-record-${STACK_PREFIX}-${UNIQUE_ID}.json"
    if [ -f "$memory_record_file" ]; then
        local memory_record_id=$($PYTHON_CMD -c "
import json
try:
    with open('$memory_record_file', 'r') as f:
        data = json.load(f)
    print(data.get('memory_record_id', ''))
except:
    print('')
" 2>/dev/null)
        
        if [ -n "$memory_record_id" ]; then
            print_status "  ✅ AgentCore Memory Record: $memory_record_id"
        fi
    fi
    
    echo ""
    print_status "🌐 ACCESS INFORMATION:"
    local infrastructure_core_stack="${STACK_PREFIX}-infrastructure-core"
    local ui_url=$(get_stack_output "$infrastructure_core_stack" "UIUrl")
    local user_pool_id=$(get_stack_output "$infrastructure_core_stack" "UserPoolId")
    
    if [ -n "$ui_url" ] && [ "$ui_url" != "None" ]; then
        print_status "  Demo URL: $ui_url"
    fi
    
    if [ -n "$user_pool_id" ] && [ "$user_pool_id" != "None" ]; then
        print_status "  User Pool ID: $user_pool_id"
    fi
    
    if [ -n "$DEMO_USER_EMAIL" ]; then
        print_status "  Demo User Email: $DEMO_USER_EMAIL"
        
        # Get demo user password from core infrastructure stack
        local demo_user_password=$(get_stack_output "$infrastructure_core_stack" "DemoUserPassword")
        if [ -n "$demo_user_password" ] && [ "$demo_user_password" != "None" ]; then
            print_status "  Demo User Password: $demo_user_password"
            print_warning "  ⚠️  This password must be changed on first login"
        fi
    fi
    
    print_status "  Unique ID: $UNIQUE_ID"
    
    print_status "📚 NEXT STEPS:"
    print_status "  1. Test AgentCore agents through the deployed UI or API calls"
    print_status "  2. AgentCore agents access knowledge bases directly through their handlers"
    echo ""
    print_status "=========================================="
    print_success "🚀 AGENTIC ADVERTISING ECOSYSTEM IS READY!"
    print_status "=========================================="
}

# Run main function with all arguments
main "$@"