#!/usr/bin/env python3
"""Wire deployed AAMP runtime ARNs + authentication into the live agent config.

Each AAMP runtime is wired in **two** shapes, because they serve two different
callers:

1. **Consumer-side** ``external_agent_configs`` entries (``AAMPSellerAgent`` /
   ``AAMPBuyerAgent``) on the agents that call them — e.g. ``AgencyAgent``. This
   is what ``shared/a2a_client_tools.build_a2a_client_tools`` reads to give the
   AdFabric runtime an invoke tool for the remote runtime.

   These names must NOT also exist as top-level ``agent_configs`` keys: a
   top-level entry under the same name tells the AdFabric runtime the agent is a
   **local, config-based collaborator**, so it builds a Strands agent and routes
   ``invoke_specialist`` to it instead of using the external connection. The
   migration block in apply_aamp_wire removes any such legacy entry.

2. **Top-level, directly selectable** ``agent_configs`` entries under separate
   names (``AAMPBuyer`` / ``AAMPSeller``, see TOP_LEVEL_AGENTS) so an operator
   can pick the AAMP runtime straight from the UI's agent list and talk to it
   without going through an orchestrator. These carry ``agent_hosting:
   "external"`` + ``agent_protocol: "a2a"``, so the UI invokes the runtime's own
   HTTPS data-plane endpoint directly rather than routing through AdFabric.

Both shapes are written in the same pass, from the same deployed state, so the
two can never disagree about a runtime's ARN or its inbound authentication.

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


def _declare_external_connection(data: dict, consumer: str, entry_name: str) -> None:
    """Make a wired external-agent connection visible to the UI.

    An entry in ``external_agent_configs`` is enough for the runtime to build the
    invoke tool, but nothing in the UI reads that list: the Angular app derives an
    agent's collaborators from ``tool_agent_names`` and colours its chat bubbles
    from ``configured_colors[<name>]``. Without both, a correctly wired external
    agent shows no connection on its consumer and renders grey when it speaks.

    Idempotent, and never overwrites an operator-chosen colour.
    """
    cfg = (data.get("agent_configs") or {}).get(consumer)
    if not isinstance(cfg, dict):
        return

    tan = cfg.setdefault("tool_agent_names", [])
    if isinstance(tan, list) and entry_name not in tan:
        tan.append(entry_name)
        _log(f"     ↳ {consumer}: declared {entry_name} in tool_agent_names")

    colors = data.setdefault("configured_colors", {})
    if entry_name not in colors:
        # Reuse the colour defined for this runtime's selectable counterpart, so
        # both names for one runtime render identically.
        spec = TOP_LEVEL_AGENTS.get(entry_name) or {}
        color = spec.get("color") or (data.get("configured_colors") or {}).get(
            spec.get("id", "")
        )
        if color:
            colors[entry_name] = color
            _log(f"     ↳ {entry_name}: colour {color} recorded")


def repair_external_connections(data: dict) -> List[str]:
    """Re-declare every already-wired external agent, without needing any ARN.

    Runs over whatever ``external_agent_configs`` entries the config already
    holds, so it repairs a config that an earlier version of this script stripped
    — and it lets any step that only touches the config (the UI update in
    particular) restore the connection without redeploying a runtime or
    re-resolving credentials.

    Returns the ``consumer -> entry`` pairs it declared.
    """
    repaired: List[str] = []
    for consumer, cfg in (data.get("agent_configs") or {}).items():
        if not isinstance(cfg, dict):
            continue
        for entry in cfg.get("external_agent_configs") or []:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            arn = str(entry.get("arn") or entry.get("runtime_arn") or "").strip()
            # Disabled or address-less entries are not connections yet, so
            # declaring them would advertise a collaborator that cannot answer.
            if not name or not arn or entry.get("enabled") is False:
                continue
            before = list(cfg.get("tool_agent_names") or [])
            _declare_external_connection(data, consumer, name)
            if name not in before:
                repaired.append(f"{consumer} → {name}")
    return repaired

# Top-level, directly selectable agent_configs entries for the same runtimes.
# ---------------------------------------------------------------------------
# Keyed by the runtime name above (which is where the ARN comes from), valued by
# the top-level entry to create. The ids deliberately differ from AAMP_AGENTS:
# reusing those names would trip the legacy-entry purge in apply_aamp_wire and
# would make the AdFabric runtime treat the remote runtime as a local agent.
#
# The colours match the AAMP entries in the template's configured_colors, so a
# top-level agent renders in the same colour as its consumer-side counterpart
# instead of the default grey.
TOP_LEVEL_AGENTS = {
    "AAMPBuyerAgent": {
        "id": "AAMPBuyer",
        "display_name": "AAMP Buyer",
        "description": (
            "IAB AAMP buyer agent — plans and creates media buys, negotiates "
            "deals, and activates them with a DSP over OpenDirect/AdCOM."
        ),
        "team_name": "AAMP Agents",
        "color": "#0F7B6CFF",
    },
    "AAMPSellerAgent": {
        "id": "AAMPSeller",
        "display_name": "AAMP Seller",
        "description": (
            "IAB AAMP seller agent — manages publisher inventory, pricing, "
            "deals and orders, and responds to buyer requests."
        ),
        "team_name": "AAMP Agents",
        "color": "#B25E00FF",
    },
}

DEFAULT_TOP_LEVEL_MODEL = "global.anthropic.claude-sonnet-5"

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


def _apply_top_level_agent(
    data: dict,
    spec: dict,
    arn: str,
    auth_mode: str,
    ssm_path: str,
    model_id: str = DEFAULT_TOP_LEVEL_MODEL,
) -> str:
    """Upsert a directly selectable top-level agent_configs entry for a runtime.

    The entry is an *external* agent: the UI reads ``agent_protocol`` +
    ``agent_endpoint`` and POSTs the A2A JSON-RPC envelope to the runtime's own
    HTTPS data-plane endpoint, and reads ``a2a_auth_type`` +
    ``a2a_oauth_credentials`` to decide what to put on the wire. Without those
    last two the UI's Inbound Authentication panel shows "None" and the call goes
    out unauthenticated against a runtime that requires a bearer token.

    Descriptive fields are seeded once and then left alone, so console edits
    (display name, description, model) survive a redeploy. The runtime and auth
    fields are deploy-owned and always rewritten. Returns the agent id.

    Note the credential lookup is keyed on this entry's own **agent name**: both
    the UI (getA2AInboundOAuthCredentials) and A2ATokenManager read
    ``/{prefix}/a2a-inbound-tokens/{uid}/{agent_name}``. ``ssm_path`` is recorded
    for the console to display, but the caller must have provisioned the login
    under this agent's name for it to resolve.
    """
    agent_id = spec["id"]
    agents = data.setdefault("agent_configs", {})
    entry: dict = dict(agents.get(agent_id, {}))

    # Descriptive fields — seeded on first wire, preserved afterwards.
    entry.setdefault("agent_id", agent_id)
    entry.setdefault("agent_name", agent_id)
    entry.setdefault("agent_display_name", spec["display_name"])
    entry.setdefault("agent_description", spec["description"])
    entry.setdefault("team_name", spec["team_name"])
    entry.setdefault("tool_agent_names", [])
    entry.setdefault("external_agents", [])
    entry.setdefault("external_agent_configs", [])
    entry.setdefault("agent_tools", [])
    entry.setdefault("mcp_servers", [])
    entry.setdefault("injectable_values", {})
    entry.setdefault("knowledge_base", "")
    entry.setdefault("color", spec["color"])
    # Empty instructions: the request goes straight to the remote runtime, which
    # carries its own prompt. A prompt here would never be used.
    entry.setdefault("instructions", "")
    entry.setdefault(
        "model_inputs",
        {
            "default": {"model_id": model_id, "max_tokens": 8000},
            agent_id: {"model_id": model_id, "max_tokens": 8000},
        },
    )

    # Deploy-owned: the runtime this entry addresses and how to authenticate.
    entry["is_a2a"] = True
    entry["agent_hosting"] = "external"
    entry["agent_protocol"] = "a2a"
    entry["agent_endpoint"] = arn
    entry["runtime_arn"] = arn
    entry["a2a_auth_type"] = auth_mode if auth_mode in ("oauth", "iam") else "none"
    if auth_mode == "oauth":
        # hasCredentials reflects whether a login was actually provisioned and
        # stored, so the console shows "No credentials stored" when it wasn't.
        entry["a2a_oauth_credentials"] = {
            "hasCredentials": bool(ssm_path),
            "ssmPath": ssm_path,
        }
    else:
        entry.pop("a2a_oauth_credentials", None)

    agents[agent_id] = entry

    # configured_colors is what the UI actually reads for the agent's colour;
    # the per-entry "color" alone leaves it grey. Additive only.
    colors = data.setdefault("configured_colors", {})
    colors.setdefault(agent_id, spec["color"])

    _log(
        f"  ✅ top-level {agent_id} ('{entry['agent_display_name']}'): {arn}"
        f" [auth={entry['a2a_auth_type']}"
        f"{'' if auth_mode != 'oauth' else (', credentials stored' if ssm_path else ', credentials MISSING')}]"
    )
    return agent_id


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
    top_level_ssm_paths: Optional[Dict[str, str]] = None,
) -> List[str]:
    """Upsert the AAMP entries that have a real ARN, in place.

    Writes both shapes described in the module docstring: the consumer-side
    external_agent_configs entries, and the directly selectable top-level
    agent_configs entries from TOP_LEVEL_AGENTS.

    Returns the names that were wired — AAMP runtime names for the consumer-side
    entries plus the top-level agent ids. Agents whose ARN is missing are skipped
    — no placeholder, no fabricated value.
    """
    agent_configs = data.setdefault("agent_configs", {})
    ssm_paths = ssm_paths or {}
    top_level_ssm_paths = top_level_ssm_paths or {}
    wired: set = set()

    # ── Migration: drop any legacy top-level AAMP agent_configs entry ──
    # The runtime name must not be an agent_configs KEY: the selectable entries
    # are the separate ids in TOP_LEVEL_AGENTS (AAMPBuyer / AAMPSeller), and a
    # key under the runtime name would collide with them.
    #
    # `tool_agent_names` and `configured_colors` are deliberately NOT stripped
    # here any more. Removing them was destroying the only record of the
    # connection that the UI can see: the Angular app builds each agent's
    # collaborators from `tool_agent_names`
    # (aws-config.service.ts), and colours chat bubbles from
    # `configured_colors[<name>]`. So every run of this script left AgencyAgent
    # looking like it had no AAMP connection at all and rendered the AAMP agent
    # grey, even though the external_agent_configs entry was correctly wired.
    # The strip also guaranteed the runtime name was absent from agent_configs,
    # which is what made `invoke_specialist("AAMPBuyerAgent")` fall through to a
    # locally-built stand-in that fabricated media plans.
    #
    # Being listed in `tool_agent_names` does not create a local agent: tools are
    # built from `agent_tools` and `external_agent_configs` only
    # (handler.py::build_tools_for_agent). It is a declaration of who this agent
    # talks to, which is exactly what a wired external agent is.
    for name in AAMP_AGENTS:
        if agent_configs.pop(name, None) is not None:
            _log(f"  🧹 {name}: removed legacy top-level agent_configs entry")

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
            _declare_external_connection(data, consumer, name)

    # ── Top-level, directly selectable entries for the same runtimes ────
    # Independent of the consumer-side wiring above: a runtime with a real ARN
    # gets its top-level entry even when no consumer declares it, since the two
    # serve different callers (UI direct-invoke vs AdFabric invoke tool).
    for runtime_name, spec in TOP_LEVEL_AGENTS.items():
        arn = (arns.get(runtime_name) or "").strip()
        if not arn:
            _log(
                f"  ⏭️  top-level {spec['id']}: no runtime ARN for {runtime_name}"
                " — skipping (not wired)"
            )
            continue
        wired.add(
            _apply_top_level_agent(
                data,
                spec,
                arn=arn,
                auth_mode=auth_mode,
                ssm_path=top_level_ssm_paths.get(spec["id"], ""),
            )
        )

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


def _run_repair_only(args) -> int:
    """`--repair-only`: re-declare existing external connections, no ARNs needed.

    Deliberately requires nothing but the config itself — no template, no runtime
    ARNs, no Cognito ids, no SSM paths — so a step that only refreshes the UI can
    call it unconditionally and it is a genuine no-op when nothing is wired.
    """
    changed_local: List[str] = []

    if os.path.exists(args.config):
        with open(args.config) as f:
            data = json.load(f)
        changed_local = repair_external_connections(data)
        if changed_local:
            with open(args.config, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
                f.write("\n")
            _log(f"  ✅ Local config: declared {', '.join(changed_local)}")
        else:
            _log("  ✓ Local config: external connections already declared")
    else:
        _log(f"  ⚠️  Local config not found: {args.config}")

    if args.skip_dynamodb or not args.dynamodb_table:
        return 0

    try:
        import boto3
        from botocore.exceptions import ClientError
    except ImportError:
        _log("  ⚠️  boto3 not available — skipping DynamoDB repair")
        return 0

    session = (
        boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
    )
    table = session.resource("dynamodb", region_name=args.region).Table(
        args.dynamodb_table
    )
    try:
        resp = table.get_item(Key={"pk": "GLOBAL_CONFIG", "sk": "v1"})
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", str(e))
        _log(f"  ⚠️  Could not read GLOBAL_CONFIG ({code}) — skipping DynamoDB repair")
        return 0

    item = resp.get("Item")
    if not item:
        _log("  ⚠️  GLOBAL_CONFIG/v1 not in DynamoDB yet — skipping DynamoDB repair")
        return 0

    content = item.get("content", "{}")
    try:
        live = json.loads(content) if isinstance(content, str) else content
    except json.JSONDecodeError as e:
        _log(f"  ⚠️  GLOBAL_CONFIG content is not valid JSON ({e}) — skipping")
        return 0

    changed_live = repair_external_connections(live)
    if not changed_live:
        _log("  ✓ DynamoDB: external connections already declared")
        return 0

    table.put_item(
        Item={
            "pk": "GLOBAL_CONFIG",
            "sk": "v1",
            "config_type": "global_config",
            "content": json.dumps(live),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    _log(f"  ✅ DynamoDB: declared {', '.join(changed_live)} ({args.dynamodb_table})")
    return 0


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
        "--repair-only",
        action="store_true",
        help=(
            "Do not wire any ARN. Re-declare the external-agent connections that "
            "the config already holds, so the UI sees them (tool_agent_names + "
            "configured_colors). Safe to run from any step that touches the "
            "config; a no-op when there is nothing wired."
        ),
    )
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
    # The top-level entries look their credentials up under their OWN agent name
    # (see _apply_top_level_agent), so they need their own provisioned logins —
    # the runtime-name paths above will not resolve for them.
    p.add_argument(
        "--buyer-top-level-ssm-path",
        default="",
        help=(
            "SSM path holding the inbound OAuth credentials for the top-level "
            f"{TOP_LEVEL_AGENTS['AAMPBuyerAgent']['id']} entry"
        ),
    )
    p.add_argument(
        "--seller-top-level-ssm-path",
        default="",
        help=(
            "SSM path holding the inbound OAuth credentials for the top-level "
            f"{TOP_LEVEL_AGENTS['AAMPSellerAgent']['id']} entry"
        ),
    )
    args = p.parse_args()

    if args.repair_only:
        return _run_repair_only(args)

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
    top_level_ssm_paths = {
        TOP_LEVEL_AGENTS["AAMPSellerAgent"]["id"]: (
            args.seller_top_level_ssm_path or ""
        ).strip(),
        TOP_LEVEL_AGENTS["AAMPBuyerAgent"]["id"]: (
            args.buyer_top_level_ssm_path or ""
        ).strip(),
    }
    auth_kwargs = dict(
        auth_mode=args.auth_mode,
        pool_id=(args.cognito_pool_id or "").strip(),
        client_id=(args.cognito_client_id or "").strip(),
        ssm_paths=ssm_paths,
        top_level_ssm_paths=top_level_ssm_paths,
    )

    if args.auth_mode == "oauth":
        missing = [n for n in available if not ssm_paths.get(n)]
        if missing:
            _log(
                "  ⚠️  OAuth mode but no stored credential path for: "
                f"{', '.join(missing)}. Those runtimes require a bearer token, "
                "so calls to them will fail until credentials are provisioned."
            )
        missing_tl = [
            TOP_LEVEL_AGENTS[n]["id"]
            for n in available
            if not top_level_ssm_paths.get(TOP_LEVEL_AGENTS[n]["id"])
        ]
        if missing_tl:
            _log(
                "  ⚠️  OAuth mode but no stored credential path for the top-level "
                f"entries: {', '.join(missing_tl)}. Selecting them in the UI will "
                "fail until a login is provisioned under each of those names."
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
    for runtime_name, spec in TOP_LEVEL_AGENTS.items():
        if spec["id"] not in all_wired:
            _log(
                f"  ❌ {spec['id']}: NOT wired (no {runtime_name} runtime ARN)"
                " — will not be selectable in the UI"
            )

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
