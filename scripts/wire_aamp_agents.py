#!/usr/bin/env python3
"""Wire deployed AAMP runtime ARNs + authentication into the live agent config.

The AAMP buyer and seller run in **their own AgentCore runtimes**, so they are
declared as ``external_agent_configs`` entries on the agents that use them —
NOT as top-level ``agent_configs`` entries.

Why that distinction matters: a top-level ``agent_configs`` entry tells the
AdFabric runtime the agent is a **local, config-based collaborator**, and it
builds a Strands agent / routes ``invoke_specialist`` to it. The AAMP agents are
remote runtimes reached over their own endpoint, so their connection details
(ARN + authentication) belong on the consumer's external-agent entry, which is
the code path that actually carries them
(``shared/a2a_client_tools.build_a2a_client_tools``).

This mirrors the external-agents deployer
(``external-agents/deploy_external_agents.py`` — ``wire_into_global_config`` /
``wire_into_dynamodb``): after the IAB runtimes deploy, patch each AAMP entry
directly into

  1. the local ``global_configuration.json`` source file, and
  2. the live ``GLOBAL_CONFIG``/``v1`` item in the DynamoDB AgentConfig table
     (which the running app and handler actually read).

The entry must already exist on a consumer (declared in
``global_configuration.template.json``) for wiring to apply — that is what
declares *which* agents may call the AAMP runtimes. Per-agent and independent: a
missing or failed runtime is skipped with an honest warning and is never written
as a placeholder or fabricated value (see steering: no-fabricated-data).

DynamoDB writes are surgical read-modify-writes of the single ``content`` JSON,
so UI-side edits to other agents are never clobbered. A missing table/item is
reported and skipped, never silently "succeeded".
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

# The two AAMP agents this script knows how to wire, mapped to the ARN it needs.
AAMP_AGENTS = ["AAMPSellerAgent", "AAMPBuyerAgent"]

# Custom, operator-editable property on the AAMP **seller** entry.
# ---------------------------------------------------------------------------
# The IAB buyer's inventory-discovery tool (search_advertising_products) talks
# OpenDirect REST to `opendirect_base_url`, which upstream defaults to
# http://localhost:3000/api/v2.1. An AgentCore runtime does not serve that
# surface, so out of the box there is no reachable inventory endpoint and the
# tool fails with "All connection attempts failed".
#
# Rather than fabricate a value or silently point at something that isn't
# there, the deploy writes the honest sentinel below. Operators set the real
# endpoint in the Agent Management console (the field renders only for entries
# that carry this property). Until then the value states plainly that it is not
# configured.
AAMP_INVENTORY_ENDPOINT_FIELD = "aampInventoryEndpoint"
AAMP_INVENTORY_ENDPOINT_UNSET = "not defined"


def _log(msg: str) -> None:
    print(msg, flush=True)


def load_template_defs(
    template_path: str,
) -> Tuple[Dict[str, dict], Dict[str, List[str]]]:
    """Return (entry defs, consumers) for the AAMP agents from the template.

    - defs:      {aamp_name: the external_agent_configs entry from the template}
    - consumers: {agent_name: [aamp names declared in its external_agent_configs]}

    Derived from the template so this script carries no hardcoded copy of the
    AAMP entry beyond the two names — including *which* agents may call them.
    """
    with open(template_path) as f:
        tpl = json.load(f)

    defs: Dict[str, dict] = {}
    consumers: Dict[str, List[str]] = {}
    for agent_name, cfg in (tpl.get("agent_configs") or {}).items():
        listed = []
        for entry in cfg.get("external_agent_configs") or []:
            name = entry.get("name")
            if name in AAMP_AGENTS:
                listed.append(name)
                defs.setdefault(name, entry)
        if listed:
            consumers[agent_name] = listed
    return defs, consumers


def _apply_entry_auth(
    entry: dict,
    agent_name: str,
    auth_mode: str,
    pool_id: str,
    client_id: str,
    ssm_path: str,
) -> None:
    """Set the connection auth on one AAMP external-agent entry.

    Deploy-owned: always rewritten to match the inbound authorizer the runtime
    was actually deployed with, so the console can never show "None" for a
    runtime that in fact requires a bearer token.
    """
    if auth_mode == "oauth":
        entry["authType"] = "oauth"
        # hasCredentials honestly reflects whether the inbound login was really
        # provisioned and stored; when it wasn't, the console shows
        # "No credentials stored" instead of implying a working configuration.
        entry["oauthCredentials"] = {
            "hasCredentials": bool(ssm_path),
            "ssmPath": ssm_path,
        }
        if pool_id:
            entry["cognitoPoolId"] = pool_id
        if client_id:
            entry["cognitoClientId"] = client_id
        # OAuth callers don't sign with SigV4; keep only the region.
        aws_auth = entry.setdefault("awsAuth", {})
        aws_auth.pop("service", None)
        _log(
            f"     ↳ {agent_name}: auth = oauth"
            f" (credentials {'stored' if ssm_path else 'MISSING'})"
        )
    else:
        entry["authType"] = "iam"
        entry.setdefault("awsAuth", {})["service"] = "bedrock-agentcore"
        # Drop any stale OAuth material from a previous oauth deployment.
        entry.pop("oauthCredentials", None)
        entry.pop("cognitoPoolId", None)
        entry.pop("cognitoClientId", None)
        _log(f"     ↳ {agent_name}: auth = iam (SigV4, same-account only)")


def apply_aamp_wire(
    data: dict,
    defs: Dict[str, dict],
    consumers: Dict[str, List[str]],
    arns: Dict[str, Optional[str]],
    region: str = "us-east-1",
    auth_mode: str = "iam",
    pool_id: str = "",
    client_id: str = "",
    ssm_paths: Optional[Dict[str, str]] = None,
) -> List[str]:
    """Upsert the AAMP external-agent entries that have a real ARN, in place.

    Returns the list of AAMP agent names that were wired (on at least one
    consumer). Agents whose ARN is missing are skipped — no placeholder, no
    fabricated value. Also migrates away from the old top-level shape.
    """
    agent_configs = data.setdefault("agent_configs", {})
    ssm_paths = ssm_paths or {}
    wired: set = set()

    # ── Migration: drop any legacy top-level AAMP agent_configs entry ──
    # A top-level entry would make the runtime treat the AAMP agent as a local
    # config-based collaborator and never use the external-agent connection.
    for name in AAMP_AGENTS:
        if agent_configs.pop(name, None) is not None:
            _log(f"  🧹 {name}: removed legacy top-level agent_configs entry")
        for cfg in agent_configs.values():
            tan = cfg.get("tool_agent_names")
            if isinstance(tan, list) and name in tan:
                cfg["tool_agent_names"] = [x for x in tan if x != name]
                _log(f"  🧹 {name}: removed from tool_agent_names")
    colors = data.get("configured_colors") or {}
    for name in AAMP_AGENTS:
        if colors.pop(name, None) is not None:
            _log(f"  🧹 {name}: removed stale configured_colors entry")

    for consumer, listed in consumers.items():
        target = agent_configs.get(consumer)
        if not target:
            _log(f"  ⚠️  consumer {consumer} not present in config — skipping")
            continue
        ext = target.setdefault("external_agent_configs", [])

        for name in listed:
            arn = (arns.get(name) or "").strip()
            if not arn:
                _log(f"  ⏭️  {name}: no runtime ARN — skipping (not wired)")
                continue
            if name not in defs:
                _log(f"  ⚠️  {name}: no template entry — cannot wire, skipping")
                continue

            # Preserve an operator-set inventory endpoint rather than resetting
            # it to the sentinel on every redeploy.
            preserved = ""
            for existing in ext:
                if existing.get("name") == name:
                    preserved = (
                        existing.get(AAMP_INVENTORY_ENDPOINT_FIELD) or ""
                    ).strip()

            entry = json.loads(json.dumps(defs[name]))  # deep copy
            entry["arn"] = arn
            entry.setdefault("awsAuth", {})["region"] = region
            _apply_entry_auth(
                entry,
                agent_name=name,
                auth_mode=auth_mode,
                pool_id=pool_id,
                client_id=client_id,
                ssm_path=ssm_paths.get(name, ""),
            )

            if AAMP_INVENTORY_ENDPOINT_FIELD in entry:
                if preserved and preserved != AAMP_INVENTORY_ENDPOINT_UNSET:
                    entry[AAMP_INVENTORY_ENDPOINT_FIELD] = preserved
                    _log(f"     ↳ {name}: kept operator-set inventory endpoint")
                else:
                    entry[AAMP_INVENTORY_ENDPOINT_FIELD] = (
                        AAMP_INVENTORY_ENDPOINT_UNSET
                    )
                    _log(
                        f"     ↳ {name}: inventory endpoint = "
                        f"'{AAMP_INVENTORY_ENDPOINT_UNSET}' — set it in the Agent "
                        "Management console to enable inventory discovery"
                    )

            ext[:] = [e for e in ext if e.get("name") != name]
            ext.append(entry)
            wired.add(name)
            _log(f"  ✅ {consumer} → {name}: {arn}")

    return sorted(wired)


def wire_into_global_config(
    config_path: str,
    defs: Dict[str, dict],
    consumers: Dict[str, List[str]],
    arns: Dict[str, Optional[str]],
    **kwargs,
) -> List[str]:
    """Patch the local global_configuration.json source file in place."""
    if not os.path.exists(config_path):
        _log(f"  ⚠️  Local config not found: {config_path} — skipping local wire")
        return []

    with open(config_path) as f:
        data = json.load(f)

    wired = apply_aamp_wire(data, defs, consumers, arns, **kwargs)

    if wired:
        with open(config_path, "w", encoding="utf-8") as f:
            # ensure_ascii=False preserves unicode already in the config (agent
            # descriptions/instructions) to keep the diff minimal.
            json.dump(data, f, indent=4, ensure_ascii=False)
            f.write("\n")
        _log(f"  ✅ Local config updated: {config_path}")
    return wired


def wire_into_dynamodb(
    table_name: str,
    region: str,
    profile: Optional[str],
    defs: Dict[str, dict],
    consumers: Dict[str, List[str]],
    arns: Dict[str, Optional[str]],
    **kwargs,
) -> List[str]:
    """Surgically patch the live GLOBAL_CONFIG/v1 item in DynamoDB.

    Read-modify-write of the single `content` JSON so other agents' UI-side
    edits are never clobbered. Honestly reports and skips when the table or
    item is missing rather than implying the write succeeded.
    """
    try:
        import boto3
        from botocore.exceptions import ClientError
    except ImportError:
        _log("  ⚠️  boto3 not available — skipping DynamoDB registration")
        return []

    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    table = session.resource("dynamodb", region_name=region).Table(table_name)

    try:
        resp = table.get_item(Key={"pk": "GLOBAL_CONFIG", "sk": "v1"})
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", str(e))
        _log(
            f"  ⚠️  Could not read GLOBAL_CONFIG from table {table_name} ({code}); "
            "skipping DynamoDB registration. The live app will not see the AAMP agents."
        )
        return []

    item = resp.get("Item")
    if not item:
        _log(
            f"  ⚠️  GLOBAL_CONFIG/v1 not found in {table_name} — base config not "
            "uploaded to DynamoDB yet. Skipping DynamoDB registration."
        )
        return []

    content = item.get("content", "{}")
    try:
        data = json.loads(content) if isinstance(content, str) else content
    except json.JSONDecodeError as e:
        _log(f"  ⚠️  GLOBAL_CONFIG content in {table_name} is not valid JSON ({e}); skipping")
        return []

    wired = apply_aamp_wire(data, defs, consumers, arns, region=region, **kwargs)
    if not wired:
        return []

    table.put_item(
        Item={
            "pk": "GLOBAL_CONFIG",
            "sk": "v1",
            "config_type": "global_config",
            "content": json.dumps(data),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    _log(f"  ✅ Registered {', '.join(wired)} into live DynamoDB GLOBAL_CONFIG ({table_name})")
    return wired


def resolve_arns_from_runtime_file(
    runtime_file: str,
) -> Dict[str, Optional[str]]:
    """Fallback: read the two ARNs from a .aamp-runtime-*.json deploy record."""
    arns: Dict[str, Optional[str]] = {n: None for n in AAMP_AGENTS}
    if not runtime_file or not os.path.exists(runtime_file):
        return arns
    try:
        with open(runtime_file) as f:
            agents = json.load(f).get("agents", {})
    except (OSError, json.JSONDecodeError):
        return arns
    for name in AAMP_AGENTS:
        if name in agents:
            arns[name] = agents[name].get("runtime_arn")
    return arns


def main() -> int:
    p = argparse.ArgumentParser(description="Wire AAMP runtime ARNs into live config")
    p.add_argument("--template", required=True, help="Path to global_configuration.template.json")
    p.add_argument("--config", required=True, help="Path to local global_configuration.json to patch")
    p.add_argument("--region", required=True)
    p.add_argument("--profile", default=None)
    p.add_argument("--dynamodb-table", default=None, help="AgentConfig table (GLOBAL_CONFIG/v1)")
    p.add_argument("--seller-arn", default="", help="AAMP seller runtime ARN (empty to skip)")
    p.add_argument("--buyer-arn", default="", help="AAMP buyer runtime ARN (empty to skip)")
    p.add_argument("--runtime-file", default="", help="Fallback .aamp-runtime-*.json to read ARNs from")
    p.add_argument("--skip-dynamodb", action="store_true", help="Only patch the local file")
    p.add_argument(
        "--auth-mode",
        choices=["oauth", "iam"],
        default="iam",
        help=(
            "Inbound auth the AAMP runtimes were actually deployed with. "
            "'oauth' records a Cognito bearer contract on the external-agent "
            "entry; 'iam' records SigV4."
        ),
    )
    p.add_argument("--cognito-pool-id", default="", help="Cognito user pool id (oauth)")
    p.add_argument("--cognito-client-id", default="", help="Cognito app client id (oauth)")
    p.add_argument(
        "--seller-ssm-path",
        default="",
        help="SSM path holding the seller's inbound OAuth credentials",
    )
    p.add_argument(
        "--buyer-ssm-path",
        default="",
        help="SSM path holding the buyer's inbound OAuth credentials",
    )
    args = p.parse_args()

    if not os.path.exists(args.template):
        _log(f"❌ Template not found: {args.template}")
        return 1

    defs, consumers = load_template_defs(args.template)
    if not defs:
        _log(
            "❌ The template declares no AAMP external_agent_configs entries "
            f"({' / '.join(AAMP_AGENTS)}) on any agent — nothing to wire. Add the "
            "entry to the agent(s) that should be able to call the AAMP runtimes."
        )
        return 1

    # ARNs: explicit flags win; fall back to the runtime file per agent.
    file_arns = resolve_arns_from_runtime_file(args.runtime_file)
    arns: Dict[str, Optional[str]] = {
        "AAMPSellerAgent": (args.seller_arn or "").strip() or file_arns.get("AAMPSellerAgent"),
        "AAMPBuyerAgent": (args.buyer_arn or "").strip() or file_arns.get("AAMPBuyerAgent"),
    }

    available = [n for n in AAMP_AGENTS if (arns.get(n) or "").strip()]
    if not available:
        _log("⚠️  No AAMP runtime ARNs available (seller and buyer both missing).")
        _log("    Nothing wired. Deploy the IAB runtimes first, then re-run.")
        # Not a hard failure: an honest no-op is preferable to a fabricated write.
        return 0

    ssm_paths = {
        "AAMPSellerAgent": (args.seller_ssm_path or "").strip(),
        "AAMPBuyerAgent": (args.buyer_ssm_path or "").strip(),
    }
    auth_kwargs = dict(
        auth_mode=args.auth_mode,
        pool_id=(args.cognito_pool_id or "").strip(),
        client_id=(args.cognito_client_id or "").strip(),
        ssm_paths=ssm_paths,
    )

    if args.auth_mode == "oauth":
        missing = [n for n in available if not ssm_paths.get(n)]
        if missing:
            _log(
                "  ⚠️  OAuth mode but no stored credential path for: "
                f"{', '.join(missing)}. Those runtimes require a bearer token, "
                "so calls to them will fail until credentials are provisioned."
            )

    _log(f"Consumers declaring AAMP entries: {', '.join(consumers) or '(none)'}")
    _log("Wiring AAMP agents into local config...")
    local_wired = wire_into_global_config(
        args.config, defs, consumers, arns, region=args.region, **auth_kwargs
    )

    dynamo_wired: List[str] = []
    if not args.skip_dynamodb and args.dynamodb_table:
        _log(f"Wiring AAMP agents into live DynamoDB ({args.dynamodb_table})...")
        dynamo_wired = wire_into_dynamodb(
            args.dynamodb_table, args.region, args.profile, defs, consumers,
            arns, **auth_kwargs
        )
    elif not args.skip_dynamodb:
        _log("  ⚠️  No --dynamodb-table given — the live app will NOT see the AAMP agents "
             "until the GLOBAL_CONFIG item is updated.")

    # Report honestly which agents did NOT get wired.
    all_wired = set(local_wired) | set(dynamo_wired)
    for name in AAMP_AGENTS:
        if name not in all_wired:
            _log(f"  ❌ {name}: NOT wired (no runtime ARN) — will not appear in the marketplace")

    # Action required: inventory discovery needs a real endpoint. Say so plainly
    # rather than leaving the operator to discover it via a failed tool call.
    if "AAMPSellerAgent" in all_wired:
        _log("")
        _log("  ⚠️  ACTION REQUIRED — AAMP inventory endpoint is 'not defined'.")
        _log("      The IAB buyer's inventory discovery (search_advertising_products)")
        _log("      calls an OpenDirect REST endpoint. No reachable endpoint ships with")
        _log("      this deployment, so discovery will fail until you set one.")
        _log("      Set it on the AAMP Seller external agent entry in the Agent")
        _log(f"      Management console ('{AAMP_INVENTORY_ENDPOINT_FIELD}'),")
        _log("      e.g. https://host/api/v2.1")

    return 0


if __name__ == "__main__":
    sys.exit(main())
