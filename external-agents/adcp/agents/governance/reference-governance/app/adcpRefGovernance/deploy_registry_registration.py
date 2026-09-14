"""Register this reference governance agent in the AWS Agent Registry.

Follows `agents/seller/reference-seller/app/adcpRefSeller/deploy_registry_registration.py` deliberately —
same registry, same idempotency approach, same two undocumented quirks worked around below. That script
paid for those findings empirically; re-deriving them here would be a waste and re-discovering them the
hard way would be worse.

    uv run python deploy_registry_registration.py            # dry run
    uv run python deploy_registry_registration.py --apply

## Namespace: deliberately the OLD one, with a deadline attached

This writes to `bedrock-agentcore-control`, the namespace being retired. That is a decision, not an
oversight:

  * AWS Agent Registry has moved to the `agent-registry` namespace. The old one shuts down
    **17 September 2026**.
  * **The new namespace is not callable from here yet.** With the pinned `botocore 1.43.56`,
    `boto3.client("agent-registry-control")` raises `UnknownServiceError`, and the installed AWS CLI does
    not know `aws agent-registry`. So there is no way to write the new contract today without first
    upgrading botocore — which is our own pin, unrelated to the AdCP SDK (`adcp` declares no boto3
    dependency at all).
  * This account already holds a registry and an approved record from before 6 August 2026, so it keeps
    dual access for the migration window rather than being locked out.

**This record must be migrated with the seller's before 17 September 2026.** See
`.kiro/steering/aws-agent-registry-namespace.md` for the full surface-by-surface mapping, and note the trap
recorded there: the same `bedrock-agentcore-control` client is used for Runtime calls that are *not*
affected, so a blanket rename would break every runtime lookup.

## Why it reuses the seller's registry rather than creating another

One more registry is one more thing to migrate before the deadline, and the migration recreates records
anyway. The cost is that the registry is *named* for sellers and now catalogues a governance agent, which
is a naming inaccuracy — fix it when the records are recreated on the new namespace. Both the registry and
record names are constants below so that migration can place this wherever it belongs.
"""

from __future__ import annotations

from aws_region import region

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.parse
from pathlib import Path

import boto3
from dotenv import load_dotenv

REGION = region()
#: Shared with the reference seller. See the module docstring for why.
REGISTRY_NAME = "adcp-reference-seller-registry"
RECORD_NAME = "adcp-reference-governance-agent"

load_dotenv(Path(__file__).parent / ".env")

#: The deployed runtime. Absent until the agent is deployed, and this script refuses rather than
#: registering a record that points nowhere — a catalogue entry for an endpoint that does not answer is
#: worse than no entry, because a consumer trusts the catalogue.
GOVERNANCE_RUNTIME_ARN = os.environ.get("GOVERNANCE_RUNTIME_ARN", "")

BUYER_DIR = Path(__file__).resolve().parents[4] / "buyer" / "reference-buyer"

control = boto3.client("bedrock-agentcore-control", region_name=REGION)


def _cognito_config() -> tuple[str, str]:
    """Discovery URL and allowed client id, read from the buyer's `.env`.

    Same source every other deploy script in this repo uses, so one Cognito pool governs the whole set.
    """
    load_dotenv(BUYER_DIR / ".env")
    discovery = os.environ.get("COGNITO_DISCOVERY_URL", "")
    client_id = os.environ.get("COGNITO_CLIENT_ID", "")
    if not discovery or not client_id:
        raise SystemExit(
            "COGNITO_DISCOVERY_URL and COGNITO_CLIENT_ID must be set in "
            f"{BUYER_DIR / '.env'} — the registry's authorizer uses the same pool as the agents."
        )
    return discovery, client_id


def _invoke_url() -> str:
    escaped = urllib.parse.quote(GOVERNANCE_RUNTIME_ARN, safe="")
    # Runtime invoke URL. This `bedrock-agentcore` host is the RUNTIME endpoint and is NOT part of the
    # registry namespace migration — it stays exactly as it is.
    return (
        f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/"
        f"{escaped}/invocations?qualifier=DEFAULT"
    )


def get_or_create_registry(apply: bool) -> str | None:
    """The registry's ARN. Idempotent via ListRegistries by name."""
    for page in control.get_paginator("list_registries").paginate():
        for reg in page["registries"]:
            if reg["name"] == REGISTRY_NAME:
                print(f"Using existing registry: {reg['registryArn']} (status={reg['status']})")
                return reg["registryArn"]

    if not apply:
        print(f"Would create registry {REGISTRY_NAME}. Re-run with --apply.")
        return None

    discovery, client_id = _cognito_config()
    print(f"Creating registry: {REGISTRY_NAME}")
    resp = control.create_registry(
        name=REGISTRY_NAME,
        description=(
            "AgentCore Registry catalog for AdCP agents deployed in this AWS account."
        ),
        authorizerType="CUSTOM_JWT",
        authorizerConfiguration={
            "customJWTAuthorizer": {
                "discoveryUrl": discovery,
                "allowedClients": [client_id],
            }
        },
        # Single-operator sandbox, not a multi-tenant marketplace with a curator role.
        approvalConfiguration={"autoApproval": True},
    )
    registry_arn = resp["registryArn"]
    print("Waiting for registry to become READY...")
    for _ in range(60):
        status = control.get_registry(registryId=registry_arn)["status"]
        if status == "READY":
            break
        if status != "CREATING":
            raise RuntimeError(f"Registry entered unexpected status: {status}")
        time.sleep(3)
    else:
        raise RuntimeError("Timed out waiting for registry to become READY.")
    print(f"Registry READY: {registry_arn}")
    return registry_arn


def _fetch_live_tool_listing() -> list[dict]:
    """The tool listing from the DEPLOYED agent, over MCP.

    Fetched live rather than written by hand. A hand-maintained listing drifts from the agent the moment a
    handler changes, and a catalogue that lists the wrong tools is the specific failure a registry
    exists to prevent. If the agent cannot be reached, this raises and nothing is registered.
    """
    sys.path.insert(0, str(BUYER_DIR))
    import auth  # noqa: E402  (buyer's Cognito helper, same as the seller script uses)
    from mcp import ClientSession  # noqa: E402
    from mcp.client.streamable_http import streamablehttp_client  # noqa: E402

    token = auth.get_test_user_access_token()

    async def _fetch() -> list[dict]:
        async with streamablehttp_client(
            _invoke_url(), headers={"Authorization": f"Bearer {token}"}
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listing = await session.list_tools()
                return [
                    {
                        "name": tool.name,
                        "description": (tool.description or "")[:300],
                        # The registry validates against an MCP schema; a permissive object placeholder is
                        # accepted where the real schema would blow the 100KB inlineContent limit. Same
                        # approach the seller script settled on.
                        "inputSchema": tool.inputSchema or {"type": "object"},
                    }
                    for tool in listing.tools
                ]

    return asyncio.run(_fetch())


def get_or_create_record(registry_arn: str, apply: bool) -> None:
    """Create or refresh this agent's record. Idempotent via ListRegistryRecords by name."""
    existing_record_id: str | None = None
    for page in control.get_paginator("list_registry_records").paginate(registryId=registry_arn):
        for rec in page["registryRecords"]:
            if rec["name"] == RECORD_NAME:
                existing_record_id = rec["recordArn"]
                print(f"Found existing record: {existing_record_id} (status={rec['status']})")

    if not apply:
        action = "update" if existing_record_id else "create"
        print(f"Would {action} record {RECORD_NAME} and refresh its tool listing. Re-run with --apply.")
        return

    tools = _fetch_live_tool_listing()
    print(f"Fetched {len(tools)} live tool(s): {[t['name'] for t in tools]}")

    server_content = json.dumps(
        {
            "name": "io.adcp/reference-governance",
            # MUST stay under 100 characters. Beyond that the registry's schema-version auto-detection
            # silently rejects the whole document with "does not match any supported version" — found
            # empirically by the seller script, not documented in the API reference. Counted, not eyeballed:
            # the assertion below fails the run rather than letting the rejection surface as a mystery.
            "description": "AdCP reference campaign-governance agent (MCP, AgentCore Runtime).",
            "version": "1.0.0",
            "remotes": [{"type": "streamable-http", "url": _invoke_url()}],
        }
    )
    inline_description = json.loads(server_content)["description"]
    assert len(inline_description) < 100, (
        f"inlineContent description is {len(inline_description)} chars; the registry silently rejects "
        "the document at 100 or more."
    )

    descriptors = {
        "mcp": {
            "server": {"inlineContent": server_content},
            "tools": {"inlineContent": json.dumps({"tools": tools})},
        }
    }
    description = (
        "AdCP reference campaign-governance agent: sync_plans, check_governance, "
        "report_plan_outcome, get_plan_audit_logs. Re-run the registration script after tool changes."
    )

    if existing_record_id is not None:
        print("Updating existing record with the current live tool listing...")
        # UpdateRegistryRecord wraps nullable fields in an {"optionalValue": ...} envelope;
        # CreateRegistryRecord takes them directly. An asymmetry confirmed against the live service model
        # by the seller script and not called out in the API reference.
        control.update_registry_record(
            registryId=registry_arn,
            recordId=existing_record_id,
            description={"optionalValue": description},
            descriptors={
                "optionalValue": {
                    "mcp": {
                        "optionalValue": {
                            "server": {"optionalValue": descriptors["mcp"]["server"]},
                            "tools": {"optionalValue": descriptors["mcp"]["tools"]},
                        }
                    }
                }
            },
            recordVersion="1.0",
        )
        print(f"Updated record: {existing_record_id}")
        return

    print(f"Creating registry record: {RECORD_NAME}")
    resp = control.create_registry_record(
        registryId=registry_arn,
        name=RECORD_NAME,
        description=description,
        descriptors=descriptors,
        recordVersion="1.0",
    )
    print(f"Created record: {resp.get('recordArn')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Register the reference governance agent.")
    parser.add_argument("--apply", action="store_true", help="actually write to the registry")
    args = parser.parse_args()

    print("=== AWS Agent Registry: reference governance agent ===")
    print(f"namespace : bedrock-agentcore  (retires 2026-09-17 — see steering doc)")
    print(f"registry  : {REGISTRY_NAME}")
    print(f"record    : {RECORD_NAME}")

    if not GOVERNANCE_RUNTIME_ARN:
        # Refused, not defaulted. A catalogue entry pointing at nothing is worse than no entry.
        print(
            "\n!!! GOVERNANCE_RUNTIME_ARN is not set.\n"
            "    Deploy the agent to AgentCore Runtime first, then set GOVERNANCE_RUNTIME_ARN in\n"
            "    this package's .env and re-run. Registering a record whose endpoint does not answer\n"
            "    would put a broken entry in a catalogue consumers are meant to trust."
        )
        return 1

    registry_arn = get_or_create_registry(args.apply)
    if registry_arn is None:
        return 0
    get_or_create_record(registry_arn, args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
