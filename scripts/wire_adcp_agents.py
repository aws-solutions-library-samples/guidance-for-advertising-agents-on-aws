#!/usr/bin/env python3
"""Wire the deployed AdCP reference buyer into this repo's live agent configuration.

Parallel to ``wire_aamp_agents.py`` rather than a generalisation of it. The two differ in the one
place that matters: AAMP authenticates with a Cognito username/password (``oauth``), while AdCP keeps
its own Cognito pool and a machine-to-machine confidential client (``oauth_m2m``). ``wire_aamp_agents``
constrains ``a2a_auth_type`` to ``("oauth", "iam")``, so it cannot express AdCP's mode at all.

Design: aidlc-docs/construction/adcp-config-wiring/functional-design/

WHAT IS WRITTEN
---------------
Two config shapes, because they are reached two different ways:

1. ``external_agent_configs`` entry ``AdCPBuyerAgent`` on AgencyAgent — reached with
   ``invoke_specialist(agent_name="AdCPBuyerAgent")``.
2. Top-level ``agent_configs`` entry ``AdCPBuyer`` — directly selectable in the UI.

The two ids MUST differ. A single name cannot be both an ``external_agent_configs`` entry and a
top-level ``agent_configs`` key, and when a name is missing from ``agent_configs`` the orchestrator
builds a local stub that fabricates a reply instead of failing. That is the AAMPBuyerAgent defect.

Plus two SSM SecureString parameters, one per name:

    /{prefix}/a2a-inbound-tokens/{uid}/AdCPBuyerAgent
    /{prefix}/a2a-inbound-tokens/{uid}/AdCPBuyer

Two parameters for one runtime because the reader derives the path from the name it is invoking
under -- see ``a2a_client_tools._resolve_ssm_path`` and the UI's ``getA2AInboundOAuthCredentials``.
Provisioning only one leaves the other name resolving an unpopulated path.

THE CREDENTIAL
--------------
``A2ATokenManager.get_bearer_token`` reads a JSON document out of one SSM parameter
(``a2a_auth.py``)::

    {"grant_type": "client_credentials", "client_id": ..., "client_secret": ...,
     "token_url": ..., "scope": ...}

AdCP's ``deploy_cognito_m2m.py`` puts the client secret in **Secrets Manager**, not its ``.env``, and
writes only non-secret identifiers there. So the secret is read from Secrets Manager here and written
into the SSM document, which means it exists in two stores. That is deliberate: the alternative is
teaching ``a2a_auth.py`` to resolve a Secrets Manager reference, and that is shared code on AAMP's
path. Both parameters are rewritten on every run, so the copies cannot drift.

The secret is never printed. Reports name the parameter and whether it was written, never the value.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, List, Tuple

CONSUMER_AGENT = "AgencyAgent"
EXTERNAL_ENTRY_NAME = "AdCPBuyerAgent"
TOP_LEVEL_ID = "AdCPBuyer"
AUTH_TYPE = "oauth_m2m"
DEFAULT_TOP_LEVEL_MODEL = "global.anthropic.claude-sonnet-5"

TOP_LEVEL_SPEC = {
    "id": TOP_LEVEL_ID,
    "display_name": "AdCP Buyer",
    "description": (
        "AdCP reference buyer agent — discovers and evaluates advertising inventory from AdCP "
        "seller agents (get_products, get_adcp_capabilities, list_creative_formats). Read-only: "
        "it does not create media buys or spend money."
    ),
    "team_name": "AdCP Agents",
    "color": "#1F6FEBFF",
}

#: Non-secret identifiers deploy_cognito_m2m.py writes into the AdCP buyer's .env.
ENV_CLIENT_ID = "COGNITO_M2M_CLIENT_ID"
ENV_SECRET_NAME = "COGNITO_M2M_SECRET_NAME"
ENV_TOKEN_ENDPOINT = "COGNITO_M2M_TOKEN_ENDPOINT"
ENV_SCOPE = "COGNITO_M2M_SCOPE"
ENV_BUYER_A2A_ARN = "BUYER_AGENT_A2A_RUNTIME_ARN"


def _log(msg: str) -> None:
    print(msg, flush=True)


# --------------------------------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------------------------------

def read_env_file(path: str) -> Dict[str, str]:
    """Parse a dotenv file into a dict. Tolerates `export `, quotes, comments and blank lines.

    Deliberately not python-dotenv: this script runs from the deploy shell, which has no guarantee of
    that package, and the file is simple enough that a dependency would be the riskier choice.
    """
    values: Dict[str, str] = {}
    if not os.path.exists(path):
        return values
    with open(path, "r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            key, _, value = line.partition("=")
            value = value.strip().strip('"').strip("'")
            values[key.strip()] = value
    return values


def fetch_client_secret(secret_name: str, region: str, profile: str | None) -> Tuple[str, str]:
    """Read the M2M client secret from Secrets Manager. Returns (secret, error)."""
    try:
        import boto3
    except ImportError:
        return "", "boto3 is not available"

    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    client = session.client("secretsmanager", region_name=region)
    try:
        resp = client.get_secret_value(SecretId=secret_name)
    except Exception as exc:  # noqa: BLE001 - the reason is reported, not handled
        return "", f"could not read secret {secret_name!r}: {type(exc).__name__}"

    raw = resp.get("SecretString") or ""
    if not raw:
        return "", f"secret {secret_name!r} has no string value"

    # deploy_cognito_m2m.py may store either the bare secret or a JSON object holding it.
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw.strip(), ""
    if isinstance(parsed, str):
        return parsed.strip(), ""
    if isinstance(parsed, dict):
        for key in ("client_secret", "clientSecret", "secret", "COGNITO_M2M_CLIENT_SECRET"):
            if parsed.get(key):
                return str(parsed[key]).strip(), ""
        return "", f"secret {secret_name!r} is JSON but holds no recognisable client secret key"
    return "", f"secret {secret_name!r} has an unexpected shape"


def build_credential_document(env: Dict[str, str], client_secret: str) -> Dict[str, str]:
    """The exact shape A2ATokenManager.get_bearer_token parses."""
    doc = {
        "grant_type": "client_credentials",
        "client_id": env.get(ENV_CLIENT_ID, ""),
        "client_secret": client_secret,
        "token_url": env.get(ENV_TOKEN_ENDPOINT, ""),
    }
    scope = env.get(ENV_SCOPE, "")
    if scope:
        doc["scope"] = scope
    return doc


def ssm_path_for(stack_prefix: str, unique_id: str, agent_name: str) -> str:
    return f"/{stack_prefix}/a2a-inbound-tokens/{unique_id}/{agent_name}"


def put_credential(
    path: str, document: Dict[str, str], region: str, profile: str | None
) -> str:
    """Write the credential document to one SSM SecureString. Returns an error string, or ""."""
    try:
        import boto3
    except ImportError:
        return "boto3 is not available"

    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    client = session.client("ssm", region_name=region)
    try:
        client.put_parameter(
            Name=path,
            Value=json.dumps(document),
            Type="SecureString",
            Overwrite=True,  # BR-3: always overwrite, so no name can hold a stale credential
        )
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}"
    return ""


# --------------------------------------------------------------------------------------------------
# Config shapes
# --------------------------------------------------------------------------------------------------

def apply_external_connection(data: dict, arn: str, ssm_path: str) -> List[str]:
    """Upsert the AdCPBuyerAgent entry in AgencyAgent's external_agent_configs.

    Also adds the entry name to ``tool_agent_names`` and gives it a ``configured_colors`` value: the
    UI reads both, and a correctly wired connection with neither is invisible in the console.
    """
    notes: List[str] = []
    agents = data.setdefault("agent_configs", {})
    consumer = agents.get(CONSUMER_AGENT)
    if consumer is None:
        notes.append(
            f"⚠️  {CONSUMER_AGENT} is absent from agent_configs; the external connection was not "
            f"written. Nothing can reach {EXTERNAL_ENTRY_NAME} via invoke_specialist."
        )
        return notes

    entries = consumer.setdefault("external_agent_configs", [])
    entry = next(
        (e for e in entries if str(e.get("name", "")).strip() == EXTERNAL_ENTRY_NAME), None
    )
    if entry is None:
        entry = {"name": EXTERNAL_ENTRY_NAME}
        entries.append(entry)

    entry["arn"] = arn
    entry["runtime_arn"] = arn
    # The envelope is chosen from this marker, and getting it wrong is silent until the peer rejects the
    # request. `resolve_entry_protocol` (agent_invocation_plan.py) reads "protocol", falls back to the
    # legacy "isA2A" flag, and defaults to HTTP — which sends the crew envelope
    # {"prompt", "routing_mode", ...}. The AdCP buyer is a JSON-RPC `message/send` endpoint, so without
    # this it answered:
    #   -32600 Request payload validation error: missing field "method"
    # Both keys are written: "protocol" is the current field, "isA2A" is what the AAMP entries use and
    # what older readers check.
    entry["protocol"] = "a2a"
    entry["isA2A"] = True
    entry["authType"] = AUTH_TYPE
    entry["oauthClientCredentials"] = {
        "hasCredentials": bool(ssm_path),
        "ssmPath": ssm_path,
    }
    # 'oauth' records its reference under oauthCredentials; drop any left by an earlier mode so the
    # reader cannot pick up a stale document (it checks oauthClientCredentials first, then falls back).
    entry.pop("oauthCredentials", None)

    tool_names = consumer.setdefault("tool_agent_names", [])
    if EXTERNAL_ENTRY_NAME not in tool_names:
        tool_names.append(EXTERNAL_ENTRY_NAME)

    colors = data.setdefault("configured_colors", {})
    colors.setdefault(EXTERNAL_ENTRY_NAME, TOP_LEVEL_SPEC["color"])

    _log(f"  ✅ {CONSUMER_AGENT} → {EXTERNAL_ENTRY_NAME}: {arn} [auth={AUTH_TYPE}]")
    return notes


def apply_top_level_agent(
    data: dict, arn: str, ssm_path: str, model_id: str = DEFAULT_TOP_LEVEL_MODEL
) -> None:
    """Upsert the directly selectable AdCPBuyer entry.

    Descriptive fields are seeded once and then left alone so console edits survive a redeploy. The
    runtime and auth fields are deploy-owned and always rewritten.
    """
    agents = data.setdefault("agent_configs", {})
    entry: dict = dict(agents.get(TOP_LEVEL_ID, {}))

    entry.setdefault("agent_id", TOP_LEVEL_ID)
    entry.setdefault("agent_name", TOP_LEVEL_ID)
    entry.setdefault("agent_display_name", TOP_LEVEL_SPEC["display_name"])
    entry.setdefault("agent_description", TOP_LEVEL_SPEC["description"])
    entry.setdefault("team_name", TOP_LEVEL_SPEC["team_name"])
    entry.setdefault("tool_agent_names", [])
    entry.setdefault("external_agents", [])
    entry.setdefault("external_agent_configs", [])
    entry.setdefault("agent_tools", [])
    entry.setdefault("mcp_servers", [])
    entry.setdefault("injectable_values", {})
    entry.setdefault("knowledge_base", "")
    entry.setdefault("color", TOP_LEVEL_SPEC["color"])
    # Empty instructions: the request goes straight to the remote runtime, which carries its own
    # prompt. A prompt here would never be used.
    entry.setdefault("instructions", "")
    entry.setdefault(
        "model_inputs",
        {
            "default": {"model_id": model_id, "max_tokens": 8000},
            TOP_LEVEL_ID: {"model_id": model_id, "max_tokens": 8000},
        },
    )

    entry["is_a2a"] = True
    entry["agent_hosting"] = "external"
    entry["agent_protocol"] = "a2a"
    entry["agent_endpoint"] = arn
    entry["runtime_arn"] = arn
    entry["a2a_auth_type"] = AUTH_TYPE
    entry["a2a_oauth_credentials"] = {
        "hasCredentials": bool(ssm_path),
        "ssmPath": ssm_path,
    }
    agents[TOP_LEVEL_ID] = entry

    # configured_colors is what the UI actually reads; the per-entry "color" alone leaves it grey.
    colors = data.setdefault("configured_colors", {})
    colors.setdefault(TOP_LEVEL_ID, TOP_LEVEL_SPEC["color"])

    _log(f"  ✅ top-level {TOP_LEVEL_ID} ('{entry['agent_display_name']}'): {arn} [auth={AUTH_TYPE}]")


def verify_paths_provisioned(data: dict, provisioned: set) -> List[str]:
    """BR-1: fail loudly if any AdCP entry names an ssmPath that was not provisioned."""
    problems: List[str] = []
    agents = data.get("agent_configs") or {}

    top = agents.get(TOP_LEVEL_ID) or {}
    top_path = (top.get("a2a_oauth_credentials") or {}).get("ssmPath") or ""
    if top_path and top_path not in provisioned:
        problems.append(f"{TOP_LEVEL_ID} names {top_path}, which was not provisioned")

    consumer = agents.get(CONSUMER_AGENT) or {}
    for e in consumer.get("external_agent_configs") or []:
        if str(e.get("name", "")).strip() != EXTERNAL_ENTRY_NAME:
            continue
        path = (e.get("oauthClientCredentials") or {}).get("ssmPath") or ""
        if path and path not in provisioned:
            problems.append(f"{EXTERNAL_ENTRY_NAME} names {path}, which was not provisioned")
    return problems


# --------------------------------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------------------------------

def write_local_config(config_path: str, data: dict) -> None:
    with open(config_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=4, ensure_ascii=False)
    _log(f"  ✅ Patched {config_path}")


def write_dynamodb(table_name: str, data: dict, region: str, profile: str | None) -> None:
    """Patch the live GLOBAL_CONFIG/v1 item, which is what the running UI actually reads."""
    try:
        import boto3
    except ImportError:
        _log("  ⚠️  boto3 not available — skipping DynamoDB registration")
        return

    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    table = session.resource("dynamodb", region_name=region).Table(table_name)
    try:
        table.put_item(
            Item={"pk": "GLOBAL_CONFIG", "sk": "v1", "content": json.dumps(data)}
        )
    except Exception as exc:  # noqa: BLE001
        _log(f"  ⚠️  Could not write GLOBAL_CONFIG to {table_name} ({type(exc).__name__})")
        return
    _log(f"  ✅ Registered {TOP_LEVEL_ID} + {EXTERNAL_ENTRY_NAME} into GLOBAL_CONFIG ({table_name})")


# --------------------------------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description="Wire the AdCP reference buyer into live config")
    p.add_argument("--config", required=True, help="global_configuration.json to patch")
    p.add_argument("--region", required=True)
    p.add_argument("--profile", default=None)
    p.add_argument("--stack-prefix", required=True)
    p.add_argument("--unique-id", required=True)
    p.add_argument("--dynamodb-table", default=None, help="AgentConfig table (GLOBAL_CONFIG/v1)")
    p.add_argument("--skip-dynamodb", action="store_true", help="Only patch the local file")
    p.add_argument(
        "--adcp-env",
        required=True,
        help=(
            "Path to the AdCP buyer's .env "
            "(external-agents/adcp/agents/buyer/reference-buyer/.env). Source of the buyer A2A "
            "runtime ARN and the COGNITO_M2M_* identifiers."
        ),
    )
    p.add_argument(
        "--buyer-arn",
        default="",
        help="Override the buyer A2A runtime ARN instead of reading it from --adcp-env",
    )
    args = p.parse_args()

    env = read_env_file(args.adcp_env)
    arn = (args.buyer_arn or env.get(ENV_BUYER_A2A_ARN, "")).strip()

    # BR-5: the AdCP phase is optional. No runtime means no wiring, and that is a success.
    if not arn:
        _log(
            "  ⏭️  No AdCP buyer A2A runtime ARN found "
            f"({ENV_BUYER_A2A_ARN} in {args.adcp_env}) — nothing wired. "
            "This is expected when the optional AdCP phase was skipped."
        )
        return 0

    if not os.path.exists(args.config):
        _log(f"❌ Config not found: {args.config}")
        return 1

    missing = [k for k in (ENV_CLIENT_ID, ENV_SECRET_NAME, ENV_TOKEN_ENDPOINT) if not env.get(k)]
    if missing:
        _log(
            f"❌ {args.adcp_env} is missing {', '.join(missing)}. "
            "Run the AdCP deploy's cognito-m2m step (deploy_all.py --only cognito-m2m)."
        )
        return 1

    secret, err = fetch_client_secret(env[ENV_SECRET_NAME], args.region, args.profile)
    if err:
        _log(f"❌ Could not obtain the AdCP M2M client secret: {err}")
        return 1

    document = build_credential_document(env, secret)
    del secret  # not needed beyond this point

    # Q5=B: provision exactly the names that are configured, and no others.
    paths = {
        EXTERNAL_ENTRY_NAME: ssm_path_for(args.stack_prefix, args.unique_id, EXTERNAL_ENTRY_NAME),
        TOP_LEVEL_ID: ssm_path_for(args.stack_prefix, args.unique_id, TOP_LEVEL_ID),
    }
    provisioned = set()
    for name, path in paths.items():
        err = put_credential(path, document, args.region, args.profile)
        if err:
            _log(f"❌ Could not write the credential for {name} to {path} ({err})")
            return 1
        provisioned.add(path)
        _log(f"  ✅ credential stored for {name} at {path}")

    with open(args.config, "r", encoding="utf-8") as handle:
        data: Dict[str, Any] = json.load(handle)

    notes = apply_external_connection(data, arn, paths[EXTERNAL_ENTRY_NAME])
    apply_top_level_agent(data, arn, paths[TOP_LEVEL_ID])

    problems = verify_paths_provisioned(data, provisioned)
    if problems:
        for problem in problems:
            _log(f"❌ {problem}")
        _log("   Refusing to write a config entry whose credential path was not provisioned.")
        return 1

    write_local_config(args.config, data)
    if args.dynamodb_table and not args.skip_dynamodb:
        write_dynamodb(args.dynamodb_table, data, args.region, args.profile)

    for note in notes:
        _log(f"  {note}")

    # BR-8: the config entry alone does not make the agent appear in a tab.
    _log("")
    _log(f"  ℹ️  {TOP_LEVEL_ID} is configured and coloured, but a tab must also list it:")
    _log(f"      add '{TOP_LEVEL_ID}' to a tab's availableAgents in")
    _log("      synthetic_data/configs/tab-configurations.json, then run")
    _log("      scripts/upload_tab_configs_to_dynamodb.py --force")
    _log("      (the running UI reads tab config from DynamoDB, not the file).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
