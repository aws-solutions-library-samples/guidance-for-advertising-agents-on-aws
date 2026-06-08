#!/bin/bash
# ============================================================================
# sync_config.sh — Push local config to DynamoDB + S3 and validate consistency
#
# Runs four steps:
#   1. Upload global_configuration.json to DynamoDB (via upload_agent_configs_to_dynamodb.py)
#   2. Sync global_configuration.json to S3 data + UI buckets
#   3. Invalidate CloudFront cache
#   4. Validate all three sources are in sync (via validate_config_consistency.py)
#
# Usage:
#   ./scripts/sync_config.sh --stack-prefix a4a --unique-id omixaj --region us-west-2
#   ./scripts/sync_config.sh --stack-prefix a4a --unique-id omixaj --region us-west-2 --profile genai
#   ./scripts/sync_config.sh --stack-prefix a4a --unique-id omixaj --region us-west-2 --skip-dynamo
#   ./scripts/sync_config.sh --stack-prefix a4a --unique-id omixaj --region us-west-2 --validate-only
# ============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Defaults
STACK_PREFIX=""
UNIQUE_ID=""
AWS_REGION="us-west-2"
AWS_PROFILE=""
SKIP_DYNAMO=false
SKIP_S3=false
SKIP_CF=false
VALIDATE_ONLY=false
PYTHON_CMD="${PYTHON_CMD:-python3}"

usage() {
    echo "Usage: $0 --stack-prefix PREFIX --unique-id ID [options]"
    echo ""
    echo "Required:"
    echo "  --stack-prefix PREFIX   Stack prefix (e.g., a4a)"
    echo "  --unique-id ID          Unique deployment ID (e.g., omixaj)"
    echo ""
    echo "Options:"
    echo "  --region REGION         AWS region (default: us-west-2)"
    echo "  --profile PROFILE       AWS CLI profile"
    echo "  --skip-dynamo           Skip DynamoDB upload (steps 2-4 only)"
    echo "  --skip-s3               Skip S3 upload (steps 1,3,4 only)"
    echo "  --skip-cf               Skip CloudFront invalidation"
    echo "  --validate-only         Only run validation (step 4)"
    echo "  -h, --help              Show this help"
    exit 1
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --stack-prefix) STACK_PREFIX="$2"; shift 2 ;;
        --unique-id) UNIQUE_ID="$2"; shift 2 ;;
        --region) AWS_REGION="$2"; shift 2 ;;
        --profile) AWS_PROFILE="$2"; shift 2 ;;
        --skip-dynamo) SKIP_DYNAMO=true; shift ;;
        --skip-s3) SKIP_S3=true; shift ;;
        --skip-cf) SKIP_CF=true; shift ;;
        --validate-only) VALIDATE_ONLY=true; shift ;;
        -h|--help) usage ;;
        *) echo "Unknown option: $1"; usage ;;
    esac
done

if [ -z "$STACK_PREFIX" ] || [ -z "$UNIQUE_ID" ]; then
    echo -e "${RED}Error: --stack-prefix and --unique-id are required${NC}"
    usage
fi

# Build AWS CLI prefix
AWS_CMD="aws"
if [ -n "$AWS_PROFILE" ]; then
    AWS_CMD="aws --profile $AWS_PROFILE"
fi

# Derived names
TABLE_NAME="${STACK_PREFIX}-AgentConfig-${UNIQUE_ID}"
DATA_BUCKET="${STACK_PREFIX}-data-${UNIQUE_ID}"
UI_BUCKET="${STACK_PREFIX}-ui-${UNIQUE_ID}"
AGENT_CONFIG_DIR="${PROJECT_ROOT}/agentcore/deployment/agent"
CONFIG_FILE="${AGENT_CONFIG_DIR}/global_configuration.json"
TAB_CONFIG_FILE="${PROJECT_ROOT}/synthetic_data/configs/tab-configurations.json"

echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  Config Sync: ${STACK_PREFIX}-${UNIQUE_ID} (${AWS_REGION})${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

if [ ! -f "$CONFIG_FILE" ]; then
    # Auto-resolve from template if live config doesn't exist
    TEMPLATE_FILE="${AGENT_CONFIG_DIR}/global_configuration.template.json"
    if [ -f "$TEMPLATE_FILE" ]; then
        echo -e "${YELLOW}  Live config not found — resolving from template...${NC}"
        RESOLVE_CMD="$PYTHON_CMD ${SCRIPT_DIR}/resolve_config.py \
            --stack-prefix $STACK_PREFIX --unique-id $UNIQUE_ID \
            --region $AWS_REGION --config-dir $AGENT_CONFIG_DIR"
        if eval "$RESOLVE_CMD"; then
            echo -e "${GREEN}  ✅ Config resolved from template${NC}"
            echo ""
        else
            echo -e "${RED}  ❌ Failed to resolve config from template${NC}"
            exit 1
        fi
    else
        echo -e "${RED}Error: Neither config nor template found in ${AGENT_CONFIG_DIR}${NC}"
        exit 1
    fi
fi

# ── Pre-flight: Block sync if local file has unresolved placeholders ────────
if [ "$VALIDATE_ONLY" = false ]; then
    # ${input} is a runtime variable used by A2A protocol — exclude it
    PLACEHOLDERS=$(grep -oE '\$\{[^}]+\}' "$CONFIG_FILE" 2>/dev/null | grep -v '^\${input}$' | sort -u)
    if [ -n "$PLACEHOLDERS" ]; then
        echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "${RED}  🛑 BLOCKED: Local config has unresolved placeholders${NC}"
        echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo ""
        echo -e "  The following placeholders were found in:"
        echo -e "  ${CONFIG_FILE}"
        echo ""
        echo "$PLACEHOLDERS" | while read -r p; do echo -e "    ${RED}${p}${NC}"; done
        echo ""
        echo -e "  Pushing this file would break the deployed environment."
        echo -e "  Resolve placeholders first using deploy-ecosystem.sh (Phase 12)"
        echo -e "  or manually replace them with values from:"
        echo -e "    .aamp-runtime-${STACK_PREFIX}-${UNIQUE_ID}.json"
        echo -e "    .kb-ids-${STACK_PREFIX}-${UNIQUE_ID}.json"
        echo ""
        exit 1
    fi

    echo -e "${GREEN}  ✅ Pre-flight check passed — no unresolved placeholders${NC}"
    echo ""
fi

STEP=0
ERRORS=0

# ── Step 1: DynamoDB Upload ─────────────────────────────────────────────────
if [ "$VALIDATE_ONLY" = false ] && [ "$SKIP_DYNAMO" = false ]; then
    STEP=$((STEP + 1))
    echo -e "${BLUE}[Step ${STEP}]${NC} Uploading to DynamoDB (${TABLE_NAME})..."

    UPLOAD_CMD="$PYTHON_CMD ${SCRIPT_DIR}/upload_agent_configs_to_dynamodb.py \
        --table-name $TABLE_NAME \
        --region $AWS_REGION \
        --agent-config-dir $AGENT_CONFIG_DIR \
        --mode overwrite \
        --stack-prefix $STACK_PREFIX \
        --unique-id $UNIQUE_ID"

    if [ -n "$AWS_PROFILE" ]; then
        UPLOAD_CMD="$UPLOAD_CMD --profile $AWS_PROFILE"
    fi

    if eval "$UPLOAD_CMD"; then
        echo -e "${GREEN}  ✅ DynamoDB upload complete${NC}"
    else
        echo -e "${RED}  ❌ DynamoDB upload failed${NC}"
        ERRORS=$((ERRORS + 1))
    fi
    echo ""
fi

# ── Step 2: S3 Upload ───────────────────────────────────────────────────────
if [ "$VALIDATE_ONLY" = false ] && [ "$SKIP_S3" = false ]; then
    STEP=$((STEP + 1))
    echo -e "${BLUE}[Step ${STEP}]${NC} Syncing to S3 buckets..."

    # S3 data bucket
    if $AWS_CMD s3 cp "$CONFIG_FILE" \
        "s3://${DATA_BUCKET}/configs/global_configuration.json" \
        --region "$AWS_REGION" 2>/dev/null; then
        echo -e "${GREEN}  ✅ S3 data bucket: ${DATA_BUCKET}/configs/global_configuration.json${NC}"
    else
        echo -e "${RED}  ❌ S3 data bucket upload failed${NC}"
        ERRORS=$((ERRORS + 1))
    fi

    # S3 UI bucket
    if $AWS_CMD s3 cp "$CONFIG_FILE" \
        "s3://${UI_BUCKET}/assets/global_configuration.json" \
        --region "$AWS_REGION" 2>/dev/null; then
        echo -e "${GREEN}  ✅ S3 UI bucket: ${UI_BUCKET}/assets/global_configuration.json${NC}"
    else
        echo -e "${RED}  ❌ S3 UI bucket upload failed${NC}"
        ERRORS=$((ERRORS + 1))
    fi

    # Tab configurations (if exists)
    if [ -f "$TAB_CONFIG_FILE" ]; then
        if $AWS_CMD s3 cp "$TAB_CONFIG_FILE" \
            "s3://${UI_BUCKET}/assets/tab-configurations.json" \
            --region "$AWS_REGION" 2>/dev/null; then
            echo -e "${GREEN}  ✅ S3 UI bucket: tab-configurations.json${NC}"
        else
            echo -e "${YELLOW}  ⚠️  Tab config upload failed (non-critical)${NC}"
        fi
    fi
    echo ""
fi

# ── Step 3: CloudFront Invalidation ─────────────────────────────────────────
if [ "$VALIDATE_ONLY" = false ] && [ "$SKIP_CF" = false ]; then
    STEP=$((STEP + 1))
    echo -e "${BLUE}[Step ${STEP}]${NC} Invalidating CloudFront cache..."

    CF_DIST_ID=$($AWS_CMD cloudfront list-distributions \
        --query "DistributionList.Items[?contains(Origins.Items[0].DomainName, '${UI_BUCKET}')].Id" \
        --output text --region "$AWS_REGION" 2>/dev/null)

    if [ -n "$CF_DIST_ID" ] && [ "$CF_DIST_ID" != "None" ]; then
        if $AWS_CMD cloudfront create-invalidation \
            --distribution-id "$CF_DIST_ID" \
            --paths "/*" --region "$AWS_REGION" >/dev/null 2>&1; then
            echo -e "${GREEN}  ✅ CloudFront invalidated (distribution: ${CF_DIST_ID})${NC}"
        else
            echo -e "${RED}  ❌ CloudFront invalidation failed${NC}"
            ERRORS=$((ERRORS + 1))
        fi
    else
        echo -e "${YELLOW}  ⚠️  No CloudFront distribution found for ${UI_BUCKET}${NC}"
    fi
    echo ""
fi

# ── Step 4: Validation ──────────────────────────────────────────────────────
STEP=$((STEP + 1))
echo -e "${BLUE}[Step ${STEP}]${NC} Validating config consistency across all sources..."

VALIDATE_CMD="$PYTHON_CMD ${SCRIPT_DIR}/validate_config_consistency.py \
    --stack-prefix $STACK_PREFIX \
    --unique-id $UNIQUE_ID \
    --region $AWS_REGION"

if [ -n "$AWS_PROFILE" ]; then
    VALIDATE_CMD="$VALIDATE_CMD --profile $AWS_PROFILE"
fi

if eval "$VALIDATE_CMD"; then
    echo ""
    echo -e "${GREEN}  ✅ All sources are consistent${NC}"
else
    echo ""
    echo -e "${RED}  ❌ Validation found issues (see above)${NC}"
    ERRORS=$((ERRORS + 1))
fi

# ── Summary ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
if [ $ERRORS -eq 0 ]; then
    echo -e "${GREEN}  ✅ Config sync complete — ${STEP} steps, 0 errors${NC}"
else
    echo -e "${RED}  ❌ Config sync finished with ${ERRORS} error(s)${NC}"
fi
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

exit $ERRORS
