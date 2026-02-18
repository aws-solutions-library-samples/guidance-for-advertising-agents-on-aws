#!/bin/bash

# Retrieve AgentCore Runtime ARNs and Bearer Tokens from SSM Parameter Store
# This script retrieves runtime configuration stored in SSM

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_status() {
    echo -e "${GREEN}[INFO]${NC} $1" >&2
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1" >&2
}

# Default values
REGION="us-east-1"
STACK_PREFIX=""
UNIQUE_ID=""
PROFILE=""
OUTPUT_FORMAT="json"

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
    --format)
      OUTPUT_FORMAT="$2"
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
      echo "  --format FORMAT            Output format: json|env|summary (default: json)"
      echo "  --help                     Show this help message"
      echo ""
      echo "Output Formats:"
      echo "  json     - Full JSON configuration (default)"
      echo "  env      - Environment variable format (RUNTIMES=arn|token,arn|token,...)"
      echo "  summary  - Human-readable summary"
      echo ""
      echo "Example:"
      echo "  $0 --stack-prefix prod --unique-id abc123 --region us-east-1 --profile rtbag"
      echo "  $0 --stack-prefix prod --unique-id abc123 --format env"
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      echo "Use --help for usage information" >&2
      exit 1
      ;;
  esac
done

# Validate required parameters
if [ -z "$STACK_PREFIX" ]; then
    print_error "STACK_PREFIX is required"
    echo "Use --help for usage information" >&2
    exit 1
fi

if [ -z "$UNIQUE_ID" ]; then
    print_error "UNIQUE_ID is required"
    echo "Use --help for usage information" >&2
    exit 1
fi

# Build profile flag if provided
PROFILE_FLAG=""
if [ -n "$PROFILE" ]; then
  PROFILE_FLAG="--profile $PROFILE"
fi

# Define SSM parameter name
PARAMETER_NAME="/${STACK_PREFIX}/agentcore_values/${UNIQUE_ID}"

print_status "Retrieving AgentCore values from SSM Parameter Store"
print_status "  Parameter Name: $PARAMETER_NAME"
print_status "  Region: $REGION"

# Retrieve from SSM Parameter Store
PARAMETER_VALUE=$(aws ssm get-parameter \
    --name "$PARAMETER_NAME" \
    --with-decryption \
    --region "$REGION" \
    $PROFILE_FLAG \
    --query 'Parameter.Value' \
    --output text 2>/dev/null)

if [ $? -ne 0 ] || [ -z "$PARAMETER_VALUE" ]; then
    print_error "Failed to retrieve AgentCore values from SSM Parameter Store"
    print_error "  Parameter: $PARAMETER_NAME"
    print_error "  Check that the parameter exists and you have ssm:GetParameter permissions"
    exit 1
fi

# Validate JSON
if ! echo "$PARAMETER_VALUE" | jq empty 2>/dev/null; then
    print_error "Invalid JSON retrieved from SSM Parameter Store"
    exit 1
fi

print_status "✅ Successfully retrieved AgentCore values"

# Output based on format
case "$OUTPUT_FORMAT" in
    json)
        # Pretty-print JSON to stdout
        echo "$PARAMETER_VALUE" | jq '.'
        ;;
    
    env)
        # Generate RUNTIMES environment variable format
        print_status "Generating RUNTIMES environment variable format..."
        RUNTIMES=$(echo "$PARAMETER_VALUE" | python3 << 'EOF'
import json
import sys

try:
    data = json.load(sys.stdin)
    agents = data.get('agents', [])
    
    runtime_entries = []
    for agent in agents:
        runtime_arn = agent.get('runtime_arn', '')
        bearer_token = agent.get('bearer_token', '')
        
        if runtime_arn:
            if bearer_token:
                runtime_entries.append(f'{runtime_arn}|{bearer_token}')
            else:
                runtime_entries.append(f'{runtime_arn}|')
    
    print(','.join(runtime_entries))
    sys.exit(0)
    
except Exception as e:
    print(f'ERROR: {str(e)}', file=sys.stderr)
    sys.exit(1)
EOF
)
        
        if [ $? -eq 0 ]; then
            echo "export RUNTIMES=\"$RUNTIMES\""
        else
            print_error "Failed to generate RUNTIMES format"
            exit 1
        fi
        ;;
    
    summary)
        # Human-readable summary
        echo "$PARAMETER_VALUE" | python3 << 'EOF'
import json
import sys

try:
    data = json.load(sys.stdin)
    
    print(f"\n📊 AgentCore Configuration Summary")
    print(f"   Stack: {data.get('stack_prefix')}-{data.get('unique_id')}")
    print(f"   Region: {data.get('region')}")
    print(f"\n🤖 Deployed Agents:")
    
    agents = data.get('agents', [])
    for i, agent in enumerate(agents, 1):
        name = agent.get('name', 'Unknown')
        runtime_arn = agent.get('runtime_arn', 'N/A')
        protocol = agent.get('protocol', 'Standard')
        
        print(f"\n   {i}. {name}")
        print(f"      Protocol: {protocol}")
        print(f"      Runtime ARN: {runtime_arn[:60]}...")
        
        if protocol == 'A2A':
            pool_id = agent.get('pool_id', 'N/A')
            client_id = agent.get('client_id', 'N/A')
            print(f"      Pool ID: {pool_id}")
            print(f"      Client ID: {client_id}")
            print(f"      Bearer Token: {'✅ Present' if agent.get('bearer_token') else '❌ Missing'}")
    
    print(f"\n   Total Agents: {len(agents)}")
    print()
    
    sys.exit(0)
    
except Exception as e:
    print(f'ERROR: {str(e)}', file=sys.stderr)
    sys.exit(1)
EOF
        ;;
    
    *)
        print_error "Unknown output format: $OUTPUT_FORMAT"
        print_error "Valid formats: json, env, summary"
        exit 1
        ;;
esac
