#!/usr/bin/env bash
# Force the local global_configuration.json's configured_colors into every place
# the UI reads them, then invalidate the CDN.
#
# Why this exists: global_configuration.json is a build artifact that several
# deploy steps rewrite, and a colour added to global_configuration.template.json
# only reaches a deployment if the resolved file happens to carry it at upload
# time. When it does not, agents render grey and nothing reports an error. This
# script closes that gap after a deploy without re-running any phase.
#
# It writes colours ONLY. Every other key in the deployed config is left byte
# for byte as it was, so it cannot disturb agent configs, instructions or
# knowledge bases.
#
# Usage:
#   scripts/force_push_agent_colors.sh --stack-prefix dm1 --unique-id 7e6w5f [--region us-east-1] [--profile NAME] [--dry-run]

set -euo pipefail

STACK_PREFIX=""
UNIQUE_ID=""
REGION="${AWS_REGION:-us-east-1}"
PROFILE=""
DRY_RUN="false"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --stack-prefix) STACK_PREFIX="$2"; shift 2 ;;
        --unique-id)    UNIQUE_ID="$2";    shift 2 ;;
        --region)       REGION="$2";       shift 2 ;;
        --profile)      PROFILE="$2";      shift 2 ;;
        --dry-run)      DRY_RUN="true";    shift ;;
        -h|--help)      sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

if [[ -z "$STACK_PREFIX" || -z "$UNIQUE_ID" ]]; then
    echo "❌ --stack-prefix and --unique-id are required" >&2
    exit 1
fi

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_DIR="${PROJECT_ROOT}/agentcore/deployment/agent"
TABLE="${STACK_PREFIX}-AgentConfig-${UNIQUE_ID}"
UI_BUCKET="${STACK_PREFIX}-ui-${UNIQUE_ID}"
DATA_BUCKET="${STACK_PREFIX}-data-${UNIQUE_ID}"

AWS_ARGS=(--region "$REGION")
[[ -n "$PROFILE" ]] && AWS_ARGS+=(--profile "$PROFILE")

PYTHON_CMD="python3"
[[ -x "${PROJECT_ROOT}/.venv-deployment/bin/python" ]] && PYTHON_CMD="${PROJECT_ROOT}/.venv-deployment/bin/python"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Force-push agent colours: ${STACK_PREFIX}-${UNIQUE_ID} (${REGION})"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# 1. Make sure the local resolved config carries every colour the template defines.
echo ""
echo "▸ Backfilling template colours into the local resolved config..."
BACKFILL_ARGS=(--stack-prefix "$STACK_PREFIX" --unique-id "$UNIQUE_ID"
               --region "$REGION" --config-dir "$CONFIG_DIR" --backfill-colors-only)
[[ "$DRY_RUN" == "true" ]] && BACKFILL_ARGS+=(--dry-run)
"$PYTHON_CMD" "${PROJECT_ROOT}/scripts/resolve_config.py" "${BACKFILL_ARGS[@]}"

# 2. Merge those colours into the live DynamoDB GLOBAL_CONFIG item, colours only.
echo ""
echo "▸ Merging colours into DynamoDB ${TABLE} (GLOBAL_CONFIG/v1)..."
DRY_RUN="$DRY_RUN" TABLE="$TABLE" REGION="$REGION" PROFILE="$PROFILE" \
CONFIG_DIR="$CONFIG_DIR" "$PYTHON_CMD" - <<'PY'
import datetime, json, os, sys

import boto3

dry = os.environ["DRY_RUN"] == "true"
profile = os.environ.get("PROFILE") or None
session = boto3.Session(profile_name=profile) if profile else boto3.Session()
table = session.resource("dynamodb", region_name=os.environ["REGION"]).Table(os.environ["TABLE"])

local_path = os.path.join(os.environ["CONFIG_DIR"], "global_configuration.json")
with open(local_path) as f:
    local_colors = json.load(f).get("configured_colors") or {}

item = table.get_item(Key={"pk": "GLOBAL_CONFIG", "sk": "v1"}).get("Item")
if not item:
    print("   ⚠️  No GLOBAL_CONFIG/v1 item found — run the DynamoDB upload phase first.")
    sys.exit(0)

deployed = json.loads(item["content"])
colors = deployed.get("configured_colors") or {}
before = len(colors)

changed = {k: v for k, v in local_colors.items() if colors.get(k) != v}
if not changed:
    print(f"   ℹ️  Already in sync ({before} colours). Nothing to do.")
    sys.exit(0)

for name, colour in sorted(changed.items()):
    print(f"   🎨 {name}: {colors.get(name)} → {colour}")

if dry:
    print(f"   🔍 Dry run — {len(changed)} colour(s) would change. Nothing written.")
    sys.exit(0)

colors.update(changed)
deployed["configured_colors"] = colors
table.update_item(
    Key={"pk": "GLOBAL_CONFIG", "sk": "v1"},
    UpdateExpression="SET content = :c, updated_at = :u",
    ExpressionAttributeValues={
        ":c": json.dumps(deployed),
        ":u": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    },
)

# Verify, and prove nothing outside configured_colors moved.
after_cfg = json.loads(table.get_item(Key={"pk": "GLOBAL_CONFIG", "sk": "v1"})["Item"]["content"])
rest_before = {k: v for k, v in deployed.items() if k != "configured_colors"}
rest_after = {k: v for k, v in after_cfg.items() if k != "configured_colors"}
same = json.dumps(rest_before, sort_keys=True) == json.dumps(rest_after, sort_keys=True)
print(f"   ✅ DynamoDB now has {len(after_cfg['configured_colors'])} colours "
      f"(was {before}); all other config keys unchanged: {same}")
PY

if [[ "$DRY_RUN" == "true" ]]; then
    echo ""
    echo "🔍 Dry run complete — no S3 upload or CDN invalidation performed."
    exit 0
fi

# 3. Push the same file to both S3 copies the UI can fall back to.
echo ""
echo "▸ Pushing the resolved config to S3 (UI asset + data bucket)..."
aws s3 cp "${CONFIG_DIR}/global_configuration.json" \
    "s3://${UI_BUCKET}/assets/global_configuration.json" "${AWS_ARGS[@]}" --only-show-errors \
    && echo "   ✅ s3://${UI_BUCKET}/assets/global_configuration.json"
aws s3 cp "${CONFIG_DIR}/global_configuration.json" \
    "s3://${DATA_BUCKET}/configs/global_configuration.json" "${AWS_ARGS[@]}" --only-show-errors \
    && echo "   ✅ s3://${DATA_BUCKET}/configs/global_configuration.json"

# 4. Invalidate the CDN so a cached asset does not mask the change.
echo ""
echo "▸ Invalidating CloudFront..."
DIST_ID=$(aws cloudfront list-distributions "${AWS_ARGS[@]}" \
    --query "DistributionList.Items[?contains(Origins.Items[0].DomainName,'${UI_BUCKET}')].Id | [0]" \
    --output text 2>/dev/null || echo "")
if [[ -n "$DIST_ID" && "$DIST_ID" != "None" ]]; then
    INV=$(aws cloudfront create-invalidation --distribution-id "$DIST_ID" \
        --paths "/index.html" "/assets/global_configuration.json" \
        "${AWS_ARGS[@]}" --query 'Invalidation.Id' --output text)
    echo "   ✅ Distribution ${DIST_ID}, invalidation ${INV}"
else
    echo "   ⚠️  Could not resolve the CloudFront distribution for ${UI_BUCKET} — skipped."
    echo "      Hard-reload the browser instead."
fi

echo ""
echo "✅ Done. Reload the app (the agent runtime picks up the new config revision on its next call)."
