#!/bin/bash

# Default values
USERNAME="user@example.com"
PASSWORD="demoUser123!"
REGION="us-east-1"
STACK_PREFIX="demo"
UNIQUE_ID="default"
USER_POOL_ID=""
CLIENT_ID=""
CREATE_COGNITO_RESOURCES='n'
PROFILE=""

# Parse named parameters
while [[ $# -gt 0 ]]; do
  case $1 in
    --username)
      USERNAME="$2"
      shift 2
      ;;
    --password)
      PASSWORD="$2"
      shift 2
      ;;
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
    --create-cognito-resources)
      CREATE_COGNITO_RESOURCES="$2"
      shift 2
      ;;
    --client-id)
      CLIENT_ID="$2"
      shift 2
      ;;
    --pool-id)
      USER_POOL_ID="$2"
      shift 2
      ;;
    --help)
      echo "Usage: $0 [OPTIONS]"
      echo ""
      echo "Options:"
      echo "  --username USERNAME        Cognito username (default: user@example.com)"
      echo "  --password PASSWORD        User password (default: demoUser123!)"
      echo "  --region REGION            AWS region (default: us-east-1)"
      echo "  --stack-prefix PREFIX      Stack prefix for resource naming (default: demo)"
      echo "  --unique-id ID             Unique identifier for resource naming (default: default)"
      echo "  --profile PROFILE          AWS CLI profile to use (optional)"
      echo "  --pool-id POOL_ID          Existing Cognito User Pool ID (optional)"
      echo "  --client-id CLIENT_ID      Existing Cognito Client ID (optional)"
      echo "  --create-cognito-resources Force creation of new Cognito resources (y/n, default: n)"
      echo "  --help                     Show this help message"
      echo ""
      echo "Example:"
      echo "  $0 --username myuser@example.com --password MyPass123! --region us-east-1 --stack-prefix prod --unique-id abc123 --profile rtbag"
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      echo "Use --help for usage information"
      exit 1
      ;;
  esac
done


# Build profile flag if provided
PROFILE_FLAG=""
if [ -n "$PROFILE" ]; then
  PROFILE_FLAG="--profile $PROFILE"
fi

# Function to check for existing Cognito resources from infrastructure stack
check_infrastructure_stack() {
  local stack_name="${STACK_PREFIX}-infrastructure-core"
  
  echo "Checking for existing Cognito resources in stack: $stack_name" >&2
  
  # Try to get User Pool ID from CloudFormation outputs
  local pool_id=$(aws cloudformation describe-stacks \
    --stack-name "$stack_name" \
    --region "$REGION" \
    $PROFILE_FLAG \
    --query 'Stacks[0].Outputs[?OutputKey==`UserPoolId`].OutputValue' \
    --output text 2>/dev/null)
  
  # Try to get Client ID from CloudFormation outputs
  local client_id=$(aws cloudformation describe-stacks \
    --stack-name "$stack_name" \
    --region "$REGION" \
    $PROFILE_FLAG \
    --query 'Stacks[0].Outputs[?OutputKey==`UserPoolClientId`].OutputValue' \
    --output text 2>/dev/null)
  
  # Check if we got valid values (not empty and not "None")
  if [[ -n "$pool_id" && "$pool_id" != "None" && -n "$client_id" && "$client_id" != "None" ]]; then
    echo "Found existing Cognito resources from infrastructure stack" >&2
    echo "  User Pool ID: $pool_id" >&2
    echo "  Client ID: $client_id" >&2
    export POOL_ID="$pool_id"
    export CLIENT_ID="$client_id"
    return 0
  else
    echo "No existing Cognito resources found in infrastructure stack" >&2
    return 1
  fi
}

# Function to create new Cognito resources
create_cognito_resources() {
  echo "Creating new Cognito resources..." >&2
  
  # Construct resource names
  USER_POOL_NAME="${STACK_PREFIX}_userPool_${UNIQUE_ID}"
  CLIENT_NAME="${STACK_PREFIX}_client_${UNIQUE_ID}"

  # Create User Pool and capture Pool ID directly
  echo "Creating User Pool: $USER_POOL_NAME" >&2
  local pool_output
  pool_output=$(aws cognito-idp create-user-pool \
    --pool-name "$USER_POOL_NAME" \
    --policies '{"PasswordPolicy":{"MinimumLength":8}}' \
    --region "$REGION" \
    $PROFILE_FLAG 2>&1)
  
  echo "$pool_output" >&2
  export POOL_ID=$(echo "$pool_output" | jq -r '.UserPool.Id')
  
  if [[ -z "$POOL_ID" || "$POOL_ID" == "null" ]]; then
    echo "ERROR: Failed to create User Pool" >&2
    return 1
  fi
  
  echo "Created User Pool with ID: $POOL_ID" >&2

  # Create App Client and capture Client ID directly
  echo "Creating App Client: $CLIENT_NAME" >&2
  local client_output
  client_output=$(aws cognito-idp create-user-pool-client \
    --user-pool-id "$POOL_ID" \
    --client-name "$CLIENT_NAME" \
    --no-generate-secret \
    --explicit-auth-flows "ALLOW_USER_PASSWORD_AUTH" "ALLOW_REFRESH_TOKEN_AUTH" \
    --region "$REGION" \
    $PROFILE_FLAG 2>&1)
  
  echo "$client_output" >&2
  export CLIENT_ID=$(echo "$client_output" | jq -r '.UserPoolClient.ClientId')
  
  if [[ -z "$CLIENT_ID" || "$CLIENT_ID" == "null" ]]; then
    echo "ERROR: Failed to create App Client" >&2
    return 1
  fi
  
  echo "Created App Client with ID: $CLIENT_ID" >&2
  return 0
}

# Determine which Cognito resources to use
if [[ -n "$USER_POOL_ID" && -n "$CLIENT_ID" ]]; then
  # Use explicitly provided Pool ID and Client ID
  echo "Using provided Cognito resources" >&2
  export POOL_ID="$USER_POOL_ID"
  export CLIENT_ID="$CLIENT_ID"
elif [[ "$CREATE_COGNITO_RESOURCES" == "y" ]]; then
  # Force creation of new resources
  if ! create_cognito_resources; then
    echo "ERROR: Failed to create Cognito resources" >&2
    exit 1
  fi
else
  # Try to reuse existing resources from infrastructure stack
  if ! check_infrastructure_stack; then
    # No existing resources found, create new ones
    echo "Creating new Cognito resources as fallback..." >&2
    if ! create_cognito_resources; then
      echo "ERROR: Failed to create Cognito resources" >&2
      exit 1
    fi
  fi
fi

# Verify we have valid Pool ID and Client ID
if [[ -z "$POOL_ID" || "$POOL_ID" == "null" ]]; then
  echo "ERROR: No valid User Pool ID available" >&2
  exit 1
fi

if [[ -z "$CLIENT_ID" || "$CLIENT_ID" == "null" ]]; then
  echo "ERROR: No valid Client ID available" >&2
  exit 1
fi

# Create User (or update if exists)
echo "Creating/updating user: $USERNAME" >&2
CREATE_USER_OUTPUT=$(aws cognito-idp admin-create-user \
  --user-pool-id "$POOL_ID" \
  --username "$USERNAME" \
  --temporary-password "$PASSWORD" \
  --region "$REGION" \
  $PROFILE_FLAG \
  --message-action SUPPRESS 2>&1)

CREATE_USER_EXIT_CODE=$?

# Check if user already exists
if [[ $CREATE_USER_EXIT_CODE -ne 0 ]]; then
  if echo "$CREATE_USER_OUTPUT" | grep -q "UsernameExistsException"; then
    echo "User already exists, updating password..." >&2
  else
    echo "ERROR: Failed to create user" >&2
    echo "$CREATE_USER_OUTPUT" >&2
    exit 1
  fi
fi

# Construct discovery URL
DISCOVERY_URL="https://cognito-idp.$REGION.amazonaws.com/$POOL_ID/.well-known/openid-configuration"

# Output JSON with Cognito auth configuration (to stdout only)
# Bearer tokens are NOT generated here — they must be obtained on demand at invocation time
echo "Cognito auth configuration resolved successfully" >&2
echo "Outputting JSON configuration..." >&2

# Use printf to ensure clean JSON output to stdout
printf '{\n'
printf '  "pool_id": "%s",\n' "$POOL_ID"
printf '  "client_id": "%s",\n' "$CLIENT_ID"
printf '  "discovery_url": "%s"\n' "$DISCOVERY_URL"
printf '}\n'