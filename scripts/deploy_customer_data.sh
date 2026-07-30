#!/bin/bash
# =============================================================================
# Deploy Customer-Specific Data for A4A Agents
# =============================================================================
# Uploads KB docs, redeploys AdCP Lambda (with merged CSVs), and optionally
# redeploys the AAMP seller runtime.
#
# Usage:
#   bash scripts/deploy_customer_data.sh nineseven
#   bash scripts/deploy_customer_data.sh nineseven --revert
#   bash scripts/deploy_customer_data.sh nineseven --profile genai --region us-west-2
#
# Prerequisites:
#   - Customer CSV overlays in synthetic_data/mcp_mocks/<type>_<customer>.csv
#   - Customer KB docs in synthetic_data/customers/<customer>/kb/*.json
#   - (Optional) AAMP seller overlays in seller-agent/data/csv/samples/aws_workshop/
# =============================================================================

set -e

# ── Config ──────────────────────────────────────────────────────────
CUSTOMER_ID="${1:?Usage: $0 <customer_id> [--revert] [--profile PROFILE] [--region REGION]}"
shift

PROFILE="${AWS_PROFILE:-genai}"
REGION="${AWS_REGION:-us-west-2}"
STACK_PREFIX="${STACK_PREFIX:-a4a}"
UNIQUE_ID="${UNIQUE_ID:-omixaj}"
REVERT=false
SKIP_ADCP=false
SKIP_SELLER=false

# Parse remaining args
while [[ $# -gt 0 ]]; do
    case "$1" in
        --revert)    REVERT=true; shift ;;
        --profile)   PROFILE="$2"; shift 2 ;;
        --region)    REGION="$2"; shift 2 ;;
        --skip-adcp) SKIP_ADCP=true; shift ;;
        --skip-seller) SKIP_SELLER=true; shift ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ "$REVERT" = true ]; then
    echo "🗑️  REVERTING customer data: $CUSTOMER_ID"
else
    echo "🚀 DEPLOYING customer data: $CUSTOMER_ID"
fi
echo "  Profile: $PROFILE | Region: $REGION"
echo "  Stack: ${STACK_PREFIX}-${UNIQUE_ID}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── Step 1: KB docs (S3 upload + ingestion) ─────────────────────────
echo "[Step 1] KB documents → S3 + ingestion"
if [ "$REVERT" = true ]; then
    python3 "$SCRIPT_DIR/deploy_customer_kb.py" "$CUSTOMER_ID" --revert --profile "$PROFILE" --region "$REGION"
else
    python3 "$SCRIPT_DIR/deploy_customer_kb.py" "$CUSTOMER_ID" --profile "$PROFILE" --region "$REGION"
fi
echo ""

# ── Step 2: Redeploy AdCP Lambda (globs merged CSVs) ───────────────
if [ "$SKIP_ADCP" = false ]; then
    echo "[Step 2] Redeploying AdCP Lambda (merged CSV data)..."
    cd "$PROJECT_ROOT"
    AWS_PROFILE="$PROFILE" AWS_DEFAULT_REGION="$REGION" python3 agentcore/deployment/deploy_adcp_gateway.py \
        --stack-prefix "$STACK_PREFIX" --unique-id "$UNIQUE_ID" \
        --region "$REGION" --profile "$PROFILE" --lambda-only
    echo ""
else
    echo "[Step 2] ⏭️  Skipping AdCP Lambda redeploy (--skip-adcp)"
    echo ""
fi

# ── Step 3: Redeploy AAMP Seller (if overlays exist) ───────────────
SELLER_REPO="${LOCAL_AAMP_PATH:-/Users/bkrishnr/repos/github/iab-aamp}/seller-agent"
SELLER_DATA_DIR="$SELLER_REPO/data/csv/samples/aws_workshop"

if [ "$SKIP_SELLER" = false ] && [ -d "$SELLER_REPO" ]; then
    # Check if customer overlay files exist in the seller data dir
    SELLER_OVERLAYS=$(find "$SELLER_DATA_DIR" -name "*_${CUSTOMER_ID}*" 2>/dev/null | wc -l)
    if [ "$SELLER_OVERLAYS" -gt 0 ]; then
        echo "[Step 3] Redeploying AAMP Seller (${SELLER_OVERLAYS} overlay file(s) detected)..."
        cd "$SELLER_REPO"
        bash infra/aws/agentcore/deploy.sh --mode http --profile "$PROFILE" --region "$REGION"
        echo ""
    else
        echo "[Step 3] ⏭️  No seller overlay files for '$CUSTOMER_ID' — skipping seller redeploy"
        echo ""
    fi
else
    echo "[Step 3] ⏭️  Skipping AAMP Seller redeploy"
    echo ""
fi

# ── Done ────────────────────────────────────────────────────────────
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ "$REVERT" = true ]; then
    echo "✅ Customer data REVERTED: $CUSTOMER_ID"
    echo "   KB docs removed from S3 (ingestion re-triggered)"
    echo "   Note: CSV overlays in mcp_mocks/ must be manually deleted + redeployed"
else
    echo "✅ Customer data DEPLOYED: $CUSTOMER_ID"
    echo "   KB ingestion running (~5-15 min)"
    echo "   AdCP Lambda updated with merged inventory"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
