#!/usr/bin/env bash
# =============================================================================
# AAMP Phase 12 deployment — sourced by scripts/deploy-ecosystem.sh
# =============================================================================
# Defines deploy_aamp_agents(): clones/locates the IAB Tech Lab seller & buyer
# agent repos, deploys their AgentCore HTTP runtimes via each repo's own
# infra/aws/agentcore/deploy.sh, captures the runtime ARNs, resolves
# global_configuration.json from the template (resolve_config.py), uploads the
# AAMP agent configs to DynamoDB, and syncs config to the S3 data/UI buckets +
# invalidates CloudFront.
#
# This is kept in its own file (sourced, not executed) to avoid growing the
# already-large deploy-ecosystem.sh. It relies on helpers/vars defined by the
# parent script at source/run time:
#   print_step, print_status, print_success, print_error, print_warning,
#   setup_python_environment, get_stack_output, aws_cmd,
#   PYTHON_CMD, SCRIPT_DIR, PROJECT_ROOT, STACK_PREFIX, UNIQUE_ID, AWS_REGION,
#   AWS_PROFILE, DEPLOY_MCP
#
# Inputs (set by the parent via CLI flags --local-aamp / --aamp-branch):
#   LOCAL_AAMP_PATH  Path to a directory containing seller-agent/ and buyer-agent/
#                    (if empty, the IAB repos are cloned at $AAMP_BRANCH).
#   AAMP_BRANCH      Branch to clone the IAB repos at (default: main).
# =============================================================================

# Safe defaults so this file works even if the parent didn't set them.
: "${AAMP_BRANCH:=main}"
: "${LOCAL_AAMP_PATH:=}"

# LLM used by the IAB seller/buyer CrewAI crews.
# --------------------------------------------------------------------------
# The IAB deploy scripts read $DEFAULT_LLM_MODEL and pass it to the runtime as
# BOTH DEFAULT_LLM_MODEL and MANAGER_LLM_MODEL (litellm-style "bedrock/<id>").
# Their own default is Nova Pro, which reliably fails CrewAI tool calling on the
# Bedrock Converse API with:
#   ModelErrorException: Model produced invalid sequence as part of ToolUse
# (observed in both AAMP runtimes' CloudWatch logs). Claude Opus 5 via the
# global cross-region inference profile handles the tool-use contract, so we
# default to it. Override with AAMP_LLM_MODEL to use a different model.
: "${AAMP_LLM_MODEL:=bedrock/global.anthropic.claude-opus-5}"

# Inbound auth for the AAMP runtimes: "oauth" (Cognito JWT authorizer, works
# cross-account/anywhere) or "iam" (SigV4, same-account only). Defaults to
# oauth so the AAMP agents can be hosted anywhere, matching the external-agents
# pattern. Set AAMP_INBOUND_AUTH=iam to keep the legacy SigV4 behavior.
: "${AAMP_INBOUND_AUTH:=oauth}"

# Extract runtime ARNs from a .bedrock_agentcore.yaml, keyed by the authoritative
# `aws.protocol_configuration.server_protocol` field (HTTP vs MCP) rather than a
# guess based on the agent name. Prints two space-separated fields:
#   "<MCP_ARN> <HTTP_ARN>"   (either may be empty)
# Falls back to a name-substring heuristic only when the protocol field is absent.
_aamp_extract_runtime_arns() {
    local yaml_file="$1"
    "${PYTHON_CMD:-python3}" -c "
import sys
try:
    import yaml
    with open('${yaml_file}') as f:
        data = yaml.safe_load(f) or {}
    mcp = http = ''
    for name, cfg in (data.get('agents') or {}).items():
        cfg = cfg or {}
        arn = ((cfg.get('bedrock_agentcore') or {}).get('agent_arn') or '')
        if not arn:
            continue
        proto = (((cfg.get('aws') or {}).get('protocol_configuration') or {}).get('server_protocol') or '').upper()
        if proto == 'MCP':
            mcp = arn
        elif proto == 'HTTP':
            http = arn
        elif 'mcp' in name.lower():
            mcp = arn
        else:
            http = arn
    # Pipe-delimited so an empty MCP field is preserved positionally when the
    # caller parses with 'IFS=| read'. A whitespace delimiter (space/tab) would
    # be trimmed by read's IFS-whitespace handling and shift the HTTP ARN into
    # the MCP slot. ARNs never contain '|'.
    print(f'{mcp}|{http}')
except Exception:
    print('|')
" 2>/dev/null || printf '|'
}

# Make an IAB repo's src/-layout package importable inside the AgentCore
# starter-toolkit container.
# --------------------------------------------------------------------------
# WHY: The IAB seller/buyer repos use a src/ layout (src/ad_seller, src/ad_buyer)
# and their package __init__.py performs an absolute self-import on line 1 of the
# package (e.g. `from ad_seller import _telemetry_shim`). Each repo's own
# infra/aws/agentcore/deploy.sh configures the runtime with
#   agentcore configure -e src/ad_seller/interfaces/agentcore/http_main.py ...
# which the starter toolkit turns into `python -m src.ad_seller.<...>` executed
# from WORKDIR /app inside a container built from the toolkit's Dockerfile
# template. That template only runs `pip install -r <requirements file>` (before
# it even COPYs the project in) — it never installs the package itself — so the
# top-level name `ad_seller` / `ad_buyer` is not on sys.path. The absolute
# self-import then raises, crashing the runtime on startup:
#     ModuleNotFoundError: No module named 'ad_seller'    (buyer: 'ad_buyer')
# (The flat-layout AdFabricAgent has no src/ dir, so it never hit this.)
#
# FIX: inject a runtime env var PYTHONPATH=/app/src:/app into each `agentcore
# deploy` call in the cloned deploy.sh, so /app/src is on sys.path and the
# package is importable as a top-level module — exactly what each repo's
# pyproject (packages = ["src/ad_seller"] / ["src/ad_buyer"]) already intends.
#
# This patches the CLONED copy on every deploy (clones are ephemeral and are
# re-fetched each run), so the upstream rkmaws repos are never modified and the
# fix re-applies itself automatically. It makes no assumption about how the user
# authenticates or deploys — it only adds a container env var. Idempotent, and it
# leaves a .aamp-orig backup so a user-provided (--local-aamp) checkout can be
# restored pristine after deployment (see cleanup at the end of deploy_aamp_agents).
_aamp_inject_src_pythonpath() {
    local repo_dir="$1"
    local label="$2"
    local ds="${repo_dir}/infra/aws/agentcore/deploy.sh"

    if [ ! -f "$ds" ]; then
        return 0  # the deploy step below warns about a missing script separately
    fi
    if grep -q 'PYTHONPATH=/app/src' "$ds" 2>/dev/null; then
        print_status "   ${label}: src-layout PYTHONPATH fix already present — skipping"
        return 0
    fi

    cp "$ds" "${ds}.aamp-orig" 2>/dev/null || true

    local count
    count=$("${PYTHON_CMD:-python3}" - "$ds" << 'PYPATCH'
import re, sys
path = sys.argv[1]
with open(path) as f:
    original = f.read()
# Insert a runtime env var immediately after every `agentcore deploy` command
# (matches both the seller's array form `agentcore deploy "${env_args[@]}" ...`
# and the buyer's backslash-continuation form `agentcore deploy \`).
patched, n = re.subn(
    r'(?m)^(\s*agentcore deploy)\b',
    r'\1 --env "PYTHONPATH=/app/src:/app"',
    original,
)
if n:
    with open(path, 'w') as f:
        f.write(patched)
print(n)
PYPATCH
) || count=0

    if [ "${count:-0}" -gt 0 ]; then
        print_success "   ${label}: injected PYTHONPATH=/app/src:/app into ${count} 'agentcore deploy' call(s)"
    else
        print_warning "   ${label}: no 'agentcore deploy' line found to patch — the runtime may fail to import its src/ package. Review ${ds}."
    fi
}

# Attach a Cognito JWT (OAuth) inbound authorizer to the IAB runtimes.
# --------------------------------------------------------------------------
# WHY: the IAB deploy.sh calls `agentcore configure` with no authorizer, so the
# runtime defaults to IAM/SigV4 inbound — only callers in the SAME account can
# invoke it. To let the AAMP agents be hosted anywhere (different account/org)
# without cross-account IAM trust, we attach the same `customJWTAuthorizer` the
# external-agents deployer uses, so callers present a Cognito bearer token that
# the runtime validates against the pool's OIDC discovery document.
#
# The starter toolkit exposes this as `agentcore configure --authorizer-config`
# (-ac) taking a JSON string. We inject it into every `agentcore configure` line
# in the CLONED deploy.sh (idempotent; backed up to .aamp-orig like the
# PYTHONPATH patch), so the upstream repos are never modified.
_aamp_inject_jwt_authorizer() {
    local repo_dir="$1"
    local label="$2"
    local discovery_url="$3"
    local client_id="$4"
    local ds="${repo_dir}/infra/aws/agentcore/deploy.sh"

    if [ ! -f "$ds" ]; then
        return 0
    fi
    if [ -z "$discovery_url" ] || [ -z "$client_id" ]; then
        print_warning "   ${label}: no Cognito discovery URL/client id — leaving inbound auth as IAM/SigV4 (same-account callers only)."
        return 1
    fi
    if grep -q 'authorizer-config' "$ds" 2>/dev/null; then
        print_status "   ${label}: JWT authorizer already present — skipping"
        return 0
    fi

    cp "$ds" "${ds}.aamp-orig" 2>/dev/null || true

    local count
    count=$("${PYTHON_CMD:-python3}" - "$ds" "$discovery_url" "$client_id" << 'PYAUTH'
import json, re, sys
path, discovery_url, client_id = sys.argv[1], sys.argv[2], sys.argv[3]
authorizer = json.dumps(
    {"customJWTAuthorizer": {"discoveryUrl": discovery_url, "allowedClients": [client_id]}}
)
# Single-quote the JSON for the shell; JSON never contains a single quote here.
injection = f" --authorizer-config '{authorizer}'"
with open(path) as f:
    original = f.read()
# Append the flag to every `agentcore configure` invocation. Matches both the
# seller's array form (`agentcore configure "${configure_args[@]}"`) and the
# buyer's backslash-continuation form (`agentcore configure \`).
patched, n = re.subn(
    r'(?m)^(\s*agentcore configure)\b', lambda m: m.group(1) + injection, original
)
if n:
    with open(path, 'w') as f:
        f.write(patched)
print(n)
PYAUTH
) || count=0

    if [ "${count:-0}" -gt 0 ]; then
        print_success "   ${label}: attached Cognito JWT authorizer to ${count} 'agentcore configure' call(s)"
        return 0
    fi
    print_warning "   ${label}: no 'agentcore configure' line found to patch — inbound auth stays IAM/SigV4. Review ${ds}."
    return 1
}

# Strip the hardcoded `temperature=` argument from the IAB crews' LLM calls.
# --------------------------------------------------------------------------
# WHY: every IAB agent factory constructs its CrewAI LLM with an explicit
# temperature, e.g.
#     llm=LLM(model=settings.default_llm_model, temperature=0.5)
# (~11 sites per repo, plus one inline call in the seller's http_main.py).
# Claude Opus 5 — the model this deploy selects — REJECTS that parameter, so
# every crew invocation fails the request outright:
#     Request validation failed: temperature is deprecated for this model.
# and the agent surfaces a failed/placeholder result instead of real output.
#
# Removing the argument makes the calls use the model's own default. This is a
# post-clone patch because the clones are ephemeral and we have no write access
# to the upstream repos. Only files under src/ are touched — the repos' own unit
# tests assert specific temperatures and are intentionally left alone (they are
# not run by this deploy).
#
# Idempotent: a second run finds nothing to strip and reports it. Every modified
# file is backed up alongside itself as <file>.aamp-orig and recorded in a
# manifest so a user-supplied (--local-aamp) checkout can be restored exactly.
_aamp_strip_llm_temperature() {
    local repo_dir="$1"
    local label="$2"
    local src_dir="${repo_dir}/src"

    if [ ! -d "$src_dir" ]; then
        print_warning "   ${label}: no src/ directory — skipping temperature strip"
        return 0
    fi

    local count
    count=$("${PYTHON_CMD:-python3}" - "$src_dir" "$repo_dir" << 'PYTEMP'
import os
import re
import sys

src_dir, repo_dir = sys.argv[1], sys.argv[2]
manifest_path = os.path.join(repo_dir, ".aamp-temperature-patched")

# Two shapes appear in these repos:
#   1. its own line inside a multi-line LLM(...) call, optionally with a
#      trailing comment:   "    temperature=0.3,  # Low temperature for ..."
#   2. inline in a single-line call:
#      "LLM(model=bedrock_model, temperature=0.3, max_tokens=4096)"
own_line = re.compile(r'(?m)^[ \t]*temperature\s*=\s*[0-9.]+\s*,?[ \t]*(#[^\n]*)?\n')
inline = re.compile(r'temperature\s*=\s*[0-9.]+\s*,\s*')

patched_files = []
total = 0
for root, dirs, files in os.walk(src_dir):
    dirs[:] = [d for d in dirs if d not in {"__pycache__"}]
    for name in files:
        if not name.endswith(".py"):
            continue
        path = os.path.join(root, name)
        try:
            with open(path, encoding="utf-8") as f:
                original = f.read()
        except OSError:
            continue
        if "temperature" not in original:
            continue
        text, n1 = own_line.subn("", original)
        text, n2 = inline.subn("", text)
        if n1 + n2 == 0:
            continue
        # Back up the pristine file next to itself so --local-aamp checkouts can
        # be restored byte-for-byte.
        backup = path + ".aamp-orig"
        if not os.path.exists(backup):
            with open(backup, "w", encoding="utf-8") as f:
                f.write(original)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        patched_files.append(os.path.relpath(path, repo_dir))
        total += n1 + n2

if patched_files:
    with open(manifest_path, "a", encoding="utf-8") as f:
        for rel in patched_files:
            f.write(rel + "\n")
print(total)
PYTEMP
) || count=0

    if [ "${count:-0}" -gt 0 ]; then
        print_success "   ${label}: removed ${count} hardcoded 'temperature=' argument(s) from LLM calls (Opus 5 rejects it)"
    else
        print_status "   ${label}: no hardcoded 'temperature=' found in src/ — nothing to strip"
    fi
    return 0
}

deploy_aamp_agents() {
    print_step "Step 11b: Deploying AAMP agents (IAB buyer & seller)..."

    # ── Environment for the IAB sub-deploys ─────────────────────────────────
    # The IAB repos' infra/aws/agentcore/deploy.sh invokes `agentcore configure`
    # (bedrock-agentcore-starter-toolkit), whose credential preflight is a boto3
    # STS get_caller_identity via the DEFAULT session. The sub-deploys inherit
    # THIS process's environment, so whatever credential mechanism the caller
    # already uses — env vars, shared profile, SSO, credential_process, container
    # or instance role, etc. — is passed through unchanged. We make no assumption
    # about how credentials are provided and never manufacture or rewrite them.
    #
    # We only (a) forward the region the caller already selected, and (b) silence
    # the toolkit's "@aws/agentcore is now recommended" migration banner.
    export AGENTCORE_SUPPRESS_RECOMMENDATION=1
    if [ -n "$AWS_REGION" ]; then
        export AWS_REGION="$AWS_REGION"
        export AWS_DEFAULT_REGION="$AWS_REGION"
    fi

    # Fail fast with ONE clear message if the ambient credentials aren't
    # resolvable by the AWS SDK (boto3) — the exact check `agentcore configure`
    # performs — instead of letting the toolkit emit its opaque error once per
    # agent and continue. This is credential-mechanism agnostic: it validates
    # whatever the standard AWS SDK credential chain resolves in this environment,
    # without prescribing HOW the caller authenticates. Skipped if boto3 isn't
    # importable by the chosen interpreter (then the toolkit does its own check).
    local _aamp_py="${PYTHON_CMD:-python3}"
    if "$_aamp_py" -c "import boto3" >/dev/null 2>&1; then
        if ! "$_aamp_py" -c "import boto3; boto3.client('sts').get_caller_identity()" >/dev/null 2>&1; then
            print_error "❌ AWS credentials are not resolvable by the AWS SDK (boto3) in this environment."
            print_error "   'agentcore configure' (used by the IAB seller/buyer sub-deploys) requires them and would fail with:"
            print_error "     \"agentcore configure requires valid aws credentials to run successfully.\""
            print_error "   Configure AWS credentials for the SDK's standard credential chain, then re-run this phase."
            print_error "   Reference: https://docs.aws.amazon.com/cli/latest/userguide/cli-chap-authentication.html"
            print_warning "   Skipping AAMP agent deployment (no partial/staged state written)."
            return 1
        fi
    fi

    local aamp_seller_dir=""
    local aamp_buyer_dir=""
    local aamp_clone_dir="${PROJECT_ROOT}/.aamp-repos-${UNIQUE_ID}"
    local aamp_runtime_file="${PROJECT_ROOT}/.aamp-runtime-${STACK_PREFIX}-${UNIQUE_ID}.json"

    # Initialize runtime tracking
    cat > "$aamp_runtime_file" << REOF
{
  "stack_prefix": "$STACK_PREFIX",
  "unique_id": "$UNIQUE_ID",
  "region": "$AWS_REGION",
  "aamp_branch": "$AAMP_BRANCH",
  "deployment_time": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "agents": {}
}
REOF

    # ── Resolve source directories ──────────────────────────────────────
    if [ -n "$LOCAL_AAMP_PATH" ]; then
        print_status "🏠 Using local AAMP repos: $LOCAL_AAMP_PATH"

        # Validate local path exists
        if [ ! -d "$LOCAL_AAMP_PATH" ]; then
            print_error "❌ Local AAMP path does not exist: $LOCAL_AAMP_PATH"
            return 1
        fi

        aamp_seller_dir="${LOCAL_AAMP_PATH}/seller-agent"
        aamp_buyer_dir="${LOCAL_AAMP_PATH}/buyer-agent"

        # Validate seller repo
        if [ ! -d "$aamp_seller_dir" ]; then
            print_error "❌ Seller agent directory not found: $aamp_seller_dir"
            return 1
        fi
        if [ ! -f "$aamp_seller_dir/infra/aws/agentcore/deploy.sh" ]; then
            print_error "❌ Seller deploy script not found: $aamp_seller_dir/infra/aws/agentcore/deploy.sh"
            return 1
        fi

        # Validate buyer repo
        if [ ! -d "$aamp_buyer_dir" ]; then
            print_error "❌ Buyer agent directory not found: $aamp_buyer_dir"
            return 1
        fi
        if [ ! -f "$aamp_buyer_dir/infra/aws/agentcore/deploy.sh" ]; then
            print_error "❌ Buyer deploy script not found: $aamp_buyer_dir/infra/aws/agentcore/deploy.sh"
            return 1
        fi

        # Clean stale .bedrock_agentcore.yaml to avoid CLI picking up old agent entries
        for repo_dir in "$aamp_seller_dir" "$aamp_buyer_dir"; do
            if [ -f "$repo_dir/.bedrock_agentcore.yaml" ]; then
                print_status "🧹 Removing stale .bedrock_agentcore.yaml from $(basename $repo_dir)"
                rm -f "$repo_dir/.bedrock_agentcore.yaml"
            fi
        done
    else
        print_status "📦 Cloning IAB repos at branch: $AAMP_BRANCH"

        # Clean up any previous clone directory
        if [ -d "$aamp_clone_dir" ]; then
            rm -rf "$aamp_clone_dir"
        fi
        mkdir -p "$aamp_clone_dir"

        # Clone seller agent
        print_status "Cloning IAB seller-agent..."
        if git clone -b "$AAMP_BRANCH" --depth 1 \
            "https://github.com/rkmaws/seller-agent.git" \
            "$aamp_clone_dir/seller-agent" 2>&1; then
            print_success "✅ Seller agent cloned successfully"
        else
            print_error "❌ Failed to clone seller-agent at branch: $AAMP_BRANCH"
            print_warning "   Skipping AAMP seller agent deployment"
        fi

        # Clone buyer agent
        print_status "Cloning IAB buyer-agent..."
        if git clone -b "$AAMP_BRANCH" --depth 1 \
            "https://github.com/rkmaws/buyer-agent.git" \
            "$aamp_clone_dir/buyer-agent" 2>&1; then
            print_success "✅ Buyer agent cloned successfully"
        else
            print_error "❌ Failed to clone buyer-agent at branch: $AAMP_BRANCH"
            print_warning "   Skipping AAMP buyer agent deployment"
        fi

        aamp_seller_dir="$aamp_clone_dir/seller-agent"
        aamp_buyer_dir="$aamp_clone_dir/buyer-agent"
    fi

    # ── Patch cloned IAB deploy scripts for the src/-layout import crash ─
    # See _aamp_inject_src_pythonpath (top of this file) for the full rationale.
    # Applied to whichever repos we resolved above, for BOTH cloned and
    # --local-aamp modes; idempotent and, for local checkouts, reverted in the
    # cleanup step so the user's working tree is left untouched.
    print_status "Applying src-layout PYTHONPATH fix to IAB deploy scripts..."
    _aamp_inject_src_pythonpath "$aamp_seller_dir" "Seller"
    _aamp_inject_src_pythonpath "$aamp_buyer_dir" "Buyer"

    # ── Remove the crews' hardcoded temperature= (rejected by Opus 5) ────
    # See _aamp_strip_llm_temperature (top of this file) for the full rationale.
    print_status "Removing hardcoded LLM temperature from IAB crews..."
    _aamp_strip_llm_temperature "$aamp_seller_dir" "Seller"
    _aamp_strip_llm_temperature "$aamp_buyer_dir" "Buyer"

    # ── Model: override the IAB default (Nova Pro) ───────────────────────
    # Both IAB deploy scripts read $DEFAULT_LLM_MODEL and forward it to the
    # runtime as DEFAULT_LLM_MODEL + MANAGER_LLM_MODEL. Exporting it here is the
    # supported override path — no patching required.
    export DEFAULT_LLM_MODEL="$AAMP_LLM_MODEL"
    print_status "AAMP crew LLM: $AAMP_LLM_MODEL (overrides the IAB Nova Pro default)"

    # ── Inbound auth: resolve Cognito + attach the JWT authorizer ────────
    # Mirrors the external-agents pattern: the runtime is deployed with a
    # Cognito JWT authorizer, an inbound login is provisioned and stored in SSM,
    # and the agent's config records authType + credential path so callers (the
    # AdFabric handler and the UI) can mint a bearer.
    local aamp_auth_mode="iam"
    local aamp_pool_id="" aamp_client_id="" aamp_discovery_url=""
    local seller_ssm_path="" buyer_ssm_path=""

    if [ "$AAMP_INBOUND_AUTH" = "oauth" ]; then
        # setup_a2a_auth (defined by the parent deploy-ecosystem.sh) resolves the
        # deployment's Cognito pool/client and exports POOL_ID/CLIENT_ID/
        # DISCOVERY_URL. Skip gracefully if the parent didn't provide it.
        if declare -f setup_a2a_auth >/dev/null 2>&1 && setup_a2a_auth "aamp-agents"; then
            aamp_pool_id="$POOL_ID"
            aamp_client_id="$CLIENT_ID"
            aamp_discovery_url="$DISCOVERY_URL"
            export A2A_POOL_ID="$aamp_pool_id"
            export A2A_CLIENT_ID="$aamp_client_id"
            export A2A_DISCOVERY_URL="$aamp_discovery_url"
        else
            # Fall back to whatever the environment already carries.
            aamp_pool_id="${A2A_POOL_ID:-}"
            aamp_client_id="${A2A_CLIENT_ID:-}"
            aamp_discovery_url="${A2A_DISCOVERY_URL:-}"
            if [ -z "$aamp_discovery_url" ] || [ -z "$aamp_client_id" ]; then
                print_warning "⚠️  Could not resolve Cognito OAuth config for the AAMP agents."
                print_warning "   Falling back to IAM/SigV4 inbound auth (same-account callers only)."
            fi
        fi

        if [ -n "$aamp_discovery_url" ] && [ -n "$aamp_client_id" ]; then
            print_status "Attaching Cognito JWT (OAuth) inbound authorizer to the AAMP runtimes..."
            local _seller_auth_ok=0 _buyer_auth_ok=0
            _aamp_inject_jwt_authorizer "$aamp_seller_dir" "Seller" \
                "$aamp_discovery_url" "$aamp_client_id" || _seller_auth_ok=1
            _aamp_inject_jwt_authorizer "$aamp_buyer_dir" "Buyer" \
                "$aamp_discovery_url" "$aamp_client_id" || _buyer_auth_ok=1

            if [ "$_seller_auth_ok" -eq 0 ] || [ "$_buyer_auth_ok" -eq 0 ]; then
                aamp_auth_mode="oauth"
                # Provision the inbound Cognito login + SSM credential for each
                # AAMP agent so callers can mint a bearer with no manual step.
                # Reuses the external-agents provisioning code (single source of
                # truth for the credential schema and SSM path convention).
                local provision_script="${SCRIPT_DIR}/provision_aamp_a2a_auth.py"
                if [ -f "$provision_script" ]; then
                    local prov_cmd="$PYTHON_CMD \"$provision_script\" \
                        --region $AWS_REGION \
                        --stack-prefix $STACK_PREFIX \
                        --unique-id $UNIQUE_ID \
                        --pool-id \"$aamp_pool_id\" \
                        --client-id \"$aamp_client_id\" \
                        --agent AAMPSellerAgent --agent AAMPBuyerAgent"
                    if [ -n "$AWS_PROFILE" ]; then
                        prov_cmd="$prov_cmd --profile $AWS_PROFILE"
                    fi
                    if eval "$prov_cmd"; then
                        seller_ssm_path="/${STACK_PREFIX}/a2a-inbound-tokens/${UNIQUE_ID}/AAMPSellerAgent"
                        buyer_ssm_path="/${STACK_PREFIX}/a2a-inbound-tokens/${UNIQUE_ID}/AAMPBuyerAgent"
                        print_success "✅ Provisioned AAMP inbound A2A credentials in SSM"
                    else
                        print_warning "⚠️  Failed to provision AAMP inbound credentials — callers will have no stored login."
                        print_warning "   The runtimes will still require a bearer token, so calls will fail until credentials exist."
                    fi
                else
                    print_warning "⚠️  $provision_script not found — inbound credentials not provisioned."
                fi
            fi
        fi
    else
        print_status "AAMP inbound auth: IAM/SigV4 (AAMP_INBOUND_AUTH=$AAMP_INBOUND_AUTH)"
    fi

    # ── Deploy Seller Agent ─────────────────────────────────────────────
    local seller_mcp_runtime_arn=""
    local seller_http_runtime_arn=""
    local seller_runtime_arn=""
    local seller_agent_name="${STACK_PREFIX}_aamp_seller_${UNIQUE_ID}"

    if [ -d "$aamp_seller_dir" ] && [ -f "$aamp_seller_dir/infra/aws/agentcore/deploy.sh" ]; then
        print_status "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        print_status "Deploying AAMP Seller Agent: $seller_agent_name"
        print_status "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

        local seller_deploy_cmd="bash $aamp_seller_dir/infra/aws/agentcore/deploy.sh"
        # Default to HTTP-only deployment. MCP runtime is optional (--deploy-mcp flag)
        # because the MCP runtime-to-runtime pattern causes guidance agent OOM (Issue 19).
        # The MCP runtime is still useful for direct MCP clients (Claude Desktop, etc.)
        if [ "${DEPLOY_MCP:-false}" = true ]; then
            seller_deploy_cmd="$seller_deploy_cmd --mode all"
            print_status "   Deploy mode: all (MCP + HTTP) — DEPLOY_MCP=true"
        else
            seller_deploy_cmd="$seller_deploy_cmd --mode http"
            print_status "   Deploy mode: http only (set DEPLOY_MCP=true to include MCP runtime)"
        fi
        seller_deploy_cmd="$seller_deploy_cmd --region $AWS_REGION"
        seller_deploy_cmd="$seller_deploy_cmd --name ${STACK_PREFIX}_aamp_seller_${UNIQUE_ID}"

        if [ -n "$AWS_PROFILE" ]; then
            seller_deploy_cmd="$seller_deploy_cmd --profile $AWS_PROFILE"
        fi

        # Run deploy from the seller repo root (required by agentcore CLI).
        # Capture the exit status but DON'T gate ARN extraction on it: the IAB
        # deploy.sh can return non-zero on a non-fatal post-deploy step (smoke
        # test, memory setup, etc.) even after the runtime was created. The
        # authoritative record of what got created is .bedrock_agentcore.yaml.
        local seller_deploy_rc=0
        (cd "$aamp_seller_dir" && eval "$seller_deploy_cmd") || seller_deploy_rc=$?

        # Extract runtime ARNs keyed by server_protocol (HTTP/MCP), regardless
        # of the deploy's exit code.
        if [ -f "$aamp_seller_dir/.bedrock_agentcore.yaml" ]; then
            IFS='|' read -r seller_mcp_runtime_arn seller_http_runtime_arn < <(_aamp_extract_runtime_arns "$aamp_seller_dir/.bedrock_agentcore.yaml")
        fi
        # Backward compat: seller_runtime_arn points to the HTTP runtime.
        seller_runtime_arn="${seller_http_runtime_arn}"

        if [ "$seller_deploy_rc" -ne 0 ]; then
            if [ -n "$seller_http_runtime_arn" ] || [ -n "$seller_mcp_runtime_arn" ]; then
                print_warning "⚠️  Seller deploy.sh exited $seller_deploy_rc, but a runtime ARN was recorded in .bedrock_agentcore.yaml — proceeding with it."
            else
                print_error "❌ Failed to deploy AAMP Seller Agent (exit $seller_deploy_rc); no runtime ARN recorded."
                print_warning "   Continuing with buyer agent deployment..."
            fi
        else
            print_success "✅ AAMP Seller Agent deploy completed."
        fi

        if [ -n "$seller_mcp_runtime_arn" ]; then
            print_status "   MCP Runtime ARN: $seller_mcp_runtime_arn"
        fi
        if [ -n "$seller_http_runtime_arn" ]; then
            print_status "   HTTP Runtime ARN: $seller_http_runtime_arn"
        else
            print_warning "   Could not determine seller HTTP runtime ARN from .bedrock_agentcore.yaml"
        fi
    else
        print_warning "⚠️  Seller agent directory or deploy script not found — skipping"
    fi

    # ── Deploy Buyer Agent ──────────────────────────────────────────────
    local buyer_runtime_arn=""
    local buyer_agent_name="${STACK_PREFIX}_aamp_buyer_${UNIQUE_ID}_http"

    if [ -d "$aamp_buyer_dir" ] && [ -f "$aamp_buyer_dir/infra/aws/agentcore/deploy.sh" ]; then
        print_status "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        print_status "Deploying AAMP Buyer Agent: $buyer_agent_name"
        print_status "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

        local buyer_deploy_cmd="bash $aamp_buyer_dir/infra/aws/agentcore/deploy.sh"
        buyer_deploy_cmd="$buyer_deploy_cmd --mode http"
        buyer_deploy_cmd="$buyer_deploy_cmd --region $AWS_REGION"
        buyer_deploy_cmd="$buyer_deploy_cmd --name $buyer_agent_name"

        if [ -n "$AWS_PROFILE" ]; then
            buyer_deploy_cmd="$buyer_deploy_cmd --profile $AWS_PROFILE"
        fi

        # Pass seller URL if we have the seller runtime ARN
        if [ -n "$seller_runtime_arn" ]; then
            buyer_deploy_cmd="$buyer_deploy_cmd --seller-url $seller_runtime_arn"
        fi

        # Run deploy from the buyer repo root (required by agentcore CLI).
        # As with the seller, capture the exit status but extract the ARN from
        # .bedrock_agentcore.yaml regardless — the runtime may exist even if a
        # non-fatal post-deploy step returned non-zero. The buyer is HTTP-only.
        local buyer_deploy_rc=0
        (cd "$aamp_buyer_dir" && eval "$buyer_deploy_cmd") || buyer_deploy_rc=$?

        if [ -f "$aamp_buyer_dir/.bedrock_agentcore.yaml" ]; then
            local _buyer_mcp_arn=""
            IFS='|' read -r _buyer_mcp_arn buyer_runtime_arn < <(_aamp_extract_runtime_arns "$aamp_buyer_dir/.bedrock_agentcore.yaml")
        fi

        if [ "$buyer_deploy_rc" -ne 0 ]; then
            if [ -n "$buyer_runtime_arn" ]; then
                print_warning "⚠️  Buyer deploy.sh exited $buyer_deploy_rc, but a runtime ARN was recorded in .bedrock_agentcore.yaml — proceeding with it."
            else
                print_error "❌ Failed to deploy AAMP Buyer Agent (exit $buyer_deploy_rc); no runtime ARN recorded."
                print_warning "   Continuing with remaining deployment steps..."
            fi
        else
            print_success "✅ AAMP Buyer Agent deploy completed."
        fi

        if [ -n "$buyer_runtime_arn" ]; then
            print_status "   Runtime ARN: $buyer_runtime_arn"
        else
            print_warning "   Could not determine buyer runtime ARN from .bedrock_agentcore.yaml"
        fi
    else
        print_warning "⚠️  Buyer agent directory or deploy script not found — skipping"
    fi

    # ── Store runtime ARNs ──────────────────────────────────────────────
    setup_python_environment

    $PYTHON_CMD << PYEOF
import json, sys

runtime_file = "$aamp_runtime_file"
seller_mcp_arn = "$seller_mcp_runtime_arn"
seller_http_arn = "$seller_http_runtime_arn"
buyer_arn = "$buyer_runtime_arn"

try:
    with open(runtime_file, 'r') as f:
        data = json.load(f)

    if seller_http_arn:
        data['agents']['AAMPSellerAgent'] = {
            'name': 'AAMPSellerAgent',
            'runtime_arn': seller_http_arn,
            'agent_name': '${STACK_PREFIX}_aamp_seller_http_${UNIQUE_ID}',
            'protocol': 'HTTP'
        }
    if buyer_arn:
        data['agents']['AAMPBuyerAgent'] = {
            'name': 'AAMPBuyerAgent',
            'runtime_arn': buyer_arn,
            'runtime_name': '$buyer_agent_name',
            'protocol': 'HTTP'
        }

    with open(runtime_file, 'w') as f:
        json.dump(data, f, indent=2)

    print(f"Stored {len(data['agents'])} AAMP agent runtime(s)", file=sys.stderr)
except Exception as e:
    print(f"ERROR storing runtime ARNs: {e}", file=sys.stderr)
    sys.exit(1)
PYEOF

    # ── Merge AAMP agents into main AgentCore tracking file ─────────────
    local agentcore_info_file="${PROJECT_ROOT}/.agentcore-agents-${STACK_PREFIX}-${UNIQUE_ID}.json"

    if [ -f "$agentcore_info_file" ]; then
        print_status "Merging AAMP agents into main AgentCore tracking file..."

        $PYTHON_CMD << PYEOF2
import json, sys
from datetime import datetime

tracking_file = "$agentcore_info_file"
seller_mcp_arn = "$seller_mcp_runtime_arn"
seller_http_arn = "$seller_http_runtime_arn"
buyer_arn = "$buyer_runtime_arn"
seller_name = "$seller_agent_name"
buyer_name = "$buyer_agent_name"

try:
    with open(tracking_file, 'r') as f:
        data = json.load(f)

    deployed = data.get('deployed_agents', [])
    existing_names = {a.get('name') for a in deployed if isinstance(a, dict)}

    seller_mcp_name = "${STACK_PREFIX}_aamp_seller_mcp_${UNIQUE_ID}"
    seller_http_name = "${STACK_PREFIX}_aamp_seller_http_${UNIQUE_ID}"

    if seller_mcp_arn and seller_mcp_name not in existing_names:
        deployed.append({
            'name': seller_mcp_name,
            'runtime_arn': seller_mcp_arn,
            'runtime_id': seller_mcp_arn.split('/')[-1] if seller_mcp_arn else '',
            'deployment_time': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
            'source': 'aamp_phase_12',
            'protocol': 'MCP'
        })

    if seller_http_arn and seller_http_name not in existing_names:
        deployed.append({
            'name': seller_http_name,
            'runtime_arn': seller_http_arn,
            'runtime_id': seller_http_arn.split('/')[-1] if seller_http_arn else '',
            'deployment_time': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
            'source': 'aamp_phase_12',
            'protocol': 'HTTP'
        })

    if buyer_arn and buyer_name not in existing_names:
        deployed.append({
            'name': buyer_name,
            'runtime_arn': buyer_arn,
            'runtime_id': buyer_arn.split('/')[-1] if buyer_arn else '',
            'deployment_time': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
            'source': 'aamp_phase_12'
        })

    data['deployed_agents'] = deployed

    with open(tracking_file, 'w') as f:
        json.dump(data, f, indent=2)

    print(f"Merged AAMP agents into tracking file", file=sys.stderr)
except Exception as e:
    print(f"WARNING: Could not merge into tracking file: {e}", file=sys.stderr)
PYEOF2
    fi

    # ── Store in SSM Parameter Store ────────────────────────────────────
    if [ -n "$seller_runtime_arn" ] || [ -n "$buyer_runtime_arn" ]; then
        local ssm_store_script="${PROJECT_ROOT}/agentcore/deployment/store_agentcore_values.sh"
        if [ -f "$ssm_store_script" ]; then
            print_status "Storing AAMP runtime ARNs in SSM Parameter Store..."
            local ssm_cmd="$ssm_store_script --stack-prefix $STACK_PREFIX --unique-id $UNIQUE_ID --region $AWS_REGION"
            if [ -n "$AWS_PROFILE" ]; then
                ssm_cmd="$ssm_cmd --profile $AWS_PROFILE"
            fi

            if $ssm_cmd; then
                print_success "✅ AAMP runtime ARNs stored in SSM"
            else
                print_warning "⚠️  Failed to store AAMP runtime ARNs in SSM — continuing"
            fi
        fi
    fi

    # ── Wire AAMP agents into live config (direct, per-agent) ──────────
    # Mirrors the external-agents deployer pattern (see _external-agents/
    # deploy_external_agents.py: wire_into_global_config / wire_into_dynamodb):
    # patch each AAMP agent's entry — with its real runtime ARN — directly into
    #   (1) the local agentcore/.../global_configuration.json source, and
    #   (2) the live GLOBAL_CONFIG/v1 item in the DynamoDB AgentConfig table.
    # Per-agent and independent: a missing seller ARN no longer blocks the buyer,
    # nothing is written as a placeholder, and there is no all-or-nothing abort.
    # This replaces the old resolve_config.py (template placeholder) + general
    # upload_agent_configs_to_dynamodb.py steps for AAMP.
    print_status "Wiring AAMP agents into live configuration (per-agent)..."
    local agent_config_dir="${PROJECT_ROOT}/agentcore/deployment/agent"
    local infrastructure_services_stack="${STACK_PREFIX}-infrastructure-services"
    local config_table
    config_table=$(get_stack_output "$infrastructure_services_stack" "AgentConfigTableName")

    local wire_cmd="$PYTHON_CMD ${SCRIPT_DIR}/wire_aamp_agents.py \
        --template ${agent_config_dir}/global_configuration.template.json \
        --config ${agent_config_dir}/global_configuration.json \
        --region $AWS_REGION \
        --seller-arn \"${seller_http_runtime_arn}\" \
        --buyer-arn \"${buyer_runtime_arn}\" \
        --runtime-file \"${aamp_runtime_file}\" \
        --auth-mode \"${aamp_auth_mode}\" \
        --cognito-pool-id \"${aamp_pool_id}\" \
        --cognito-client-id \"${aamp_client_id}\" \
        --seller-ssm-path \"${seller_ssm_path}\" \
        --buyer-ssm-path \"${buyer_ssm_path}\""
    if [ -n "$config_table" ] && [ "$config_table" != "None" ]; then
        wire_cmd="$wire_cmd --dynamodb-table $config_table"
    else
        print_warning "⚠️  AgentConfig table not resolved — will patch the local config only."
    fi
    if [ -n "$AWS_PROFILE" ]; then
        wire_cmd="$wire_cmd --profile $AWS_PROFILE"
    fi

    if eval "$wire_cmd"; then
        print_success "✅ AAMP agents wired (local config + live DynamoDB, per-agent)"
    else
        print_warning "⚠️  AAMP wiring reported an error — see output above"
    fi

    # ── Summary ─────────────────────────────────────────────────────────
    print_status "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    print_status "Phase 12: AAMP Deployment Summary"
    print_status "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    if [ -n "$seller_http_runtime_arn" ]; then
        print_success "  ✅ AAMPSellerAgent (HTTP): $seller_http_runtime_arn"
    else
        print_warning "  ❌ AAMPSellerAgent (HTTP): not deployed"
    fi

    if [ -n "$buyer_runtime_arn" ]; then
        print_success "  ✅ AAMPBuyerAgent (HTTP): $buyer_runtime_arn"
    else
        print_warning "  ❌ AAMPBuyerAgent (HTTP): not deployed"
    fi

    print_status "  Runtime cache: $aamp_runtime_file"
    print_status "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    # ── Post-deploy: Sync configs to all three sources ──────────────────
    # The deploy script uploads to DynamoDB (Step 9) but the S3 data bucket
    # and S3 UI bucket can get stale after Phase 12 deploys new AAMP runtimes.
    # This step ensures all three config sources are consistent.

    local agent_config_dir="${PROJECT_ROOT}/agentcore/deployment/agent"
    local data_bucket="${STACK_PREFIX}-data-${UNIQUE_ID}"
    local ui_bucket="${STACK_PREFIX}-ui-${UNIQUE_ID}"

    print_status "📤 Post-deploy: Syncing configs to S3 data and UI buckets..."

    # Sync global_configuration.json to S3 data bucket
    if aws_cmd s3 cp "${agent_config_dir}/global_configuration.json" \
        "s3://${data_bucket}/configs/global_configuration.json" 2>/dev/null; then
        print_success "  ✅ S3 data bucket config synced"
    else
        print_warning "  ⚠️  Failed to sync S3 data bucket config"
    fi

    # Sync global_configuration.json to S3 UI bucket
    if aws_cmd s3 cp "${agent_config_dir}/global_configuration.json" \
        "s3://${ui_bucket}/assets/global_configuration.json" 2>/dev/null; then
        print_success "  ✅ S3 UI bucket config synced"
    else
        print_warning "  ⚠️  Failed to sync S3 UI bucket config"
    fi

    # Sync tab-configurations.json to S3 UI bucket
    local tab_config="${PROJECT_ROOT}/synthetic_data/configs/tab-configurations.json"
    if [ -f "$tab_config" ]; then
        if aws_cmd s3 cp "$tab_config" \
            "s3://${ui_bucket}/assets/tab-configurations.json" 2>/dev/null; then
            print_success "  ✅ S3 UI bucket tab config synced"
        else
            print_warning "  ⚠️  Failed to sync S3 UI bucket tab config"
        fi
    fi

    # Invalidate CloudFront cache
    local cf_distribution_id=""
    cf_distribution_id=$(aws_cmd cloudfront list-distributions \
        --query "DistributionList.Items[?contains(Origins.Items[0].DomainName, '${ui_bucket}')].Id" \
        --output text 2>/dev/null)

    if [ -n "$cf_distribution_id" ] && [ "$cf_distribution_id" != "None" ]; then
        if aws_cmd cloudfront create-invalidation \
            --distribution-id "$cf_distribution_id" \
            --paths "/*" >/dev/null 2>&1; then
            print_success "  ✅ CloudFront cache invalidated (distribution: $cf_distribution_id)"
        else
            print_warning "  ⚠️  Failed to invalidate CloudFront cache"
        fi
    else
        print_warning "  ⚠️  CloudFront distribution not found for ${ui_bucket}"
    fi

    # Restore any deploy.sh we patched in a user-provided (--local-aamp) checkout
    # so the src-layout PYTHONPATH fix never persists in the user's working tree.
    # (Cloned repos are removed wholesale just below, so they need no restore.)
    if [ -n "$LOCAL_AAMP_PATH" ]; then
        for _ds in "$aamp_seller_dir/infra/aws/agentcore/deploy.sh" \
                   "$aamp_buyer_dir/infra/aws/agentcore/deploy.sh"; do
            if [ -f "${_ds}.aamp-orig" ]; then
                mv -f "${_ds}.aamp-orig" "${_ds}"
                print_status "Restored original local deploy.sh: ${_ds}"
            fi
        done

        # Restore every src/ file the temperature strip modified, using the
        # per-file .aamp-orig backups listed in each repo's manifest, so a local
        # checkout is left byte-for-byte as the user had it.
        for _repo in "$aamp_seller_dir" "$aamp_buyer_dir"; do
            local _manifest="${_repo}/.aamp-temperature-patched"
            if [ -f "$_manifest" ]; then
                local _restored=0
                while IFS= read -r _rel; do
                    [ -z "$_rel" ] && continue
                    if [ -f "${_repo}/${_rel}.aamp-orig" ]; then
                        mv -f "${_repo}/${_rel}.aamp-orig" "${_repo}/${_rel}"
                        _restored=$((_restored + 1))
                    fi
                done < "$_manifest"
                rm -f "$_manifest"
                if [ "$_restored" -gt 0 ]; then
                    print_status "Restored ${_restored} original source file(s) in $(basename "$_repo")"
                fi
            fi
        done
    fi

    # Clean up cloned repos if not using local path
    if [ -z "$LOCAL_AAMP_PATH" ] && [ -d "$aamp_clone_dir" ]; then
        print_status "Cleaning up cloned AAMP repos..."
        rm -rf "$aamp_clone_dir"
    fi

    return 0
}
