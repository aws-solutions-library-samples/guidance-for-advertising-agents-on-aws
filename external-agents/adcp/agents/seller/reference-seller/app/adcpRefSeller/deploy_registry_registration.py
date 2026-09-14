"""
Registers this reference seller in the AgentCore Registry (R7 of
.kiro/specs/seller-agent-adcp-compliance/design.md and tasks.md task 11) -
the AWS-native substitute this spec uses for the AAO registry (see
design.md's "Why the AgentCore Registry, not AAO").

    uv run python deploy_registry_registration.py

Idempotent: reuses an existing registry named REGISTRY_NAME and an
existing record named RECORD_NAME if either already exists, otherwise
creates them.

## What this script does NOT do, and why (empirically verified, not assumed)

design.md's original plan was `synchronizationType: URL` pointed at this
seller's live MCP endpoint, so the registry's crawler keeps the tool
listing current automatically. That was verified against the real
preview API before writing this script (a throwaway registry + several
throwaway records were created and deleted in the same investigation
session that produced this file - not part of this project's persistent
state):

  - The registry's URL-sync crawler only supports two outbound credential
    modes: `IAM` (SigV4 request signing) or `OAUTH` (AgentCore Identity
    credential provider, client_credentials grant against an OAuth2
    authorization server).
  - This seller's deployed AgentCore Runtime endpoint requires a Cognito
    **JWT bearer token** (`authorizerType: CUSTOM_JWT` in
    ../../agentcore/agentcore.json) - not SigV4, and not an OAuth
    client_credentials grant.
  - `IAM` mode: confirmed with a live `CreateRegistryRecord` call against
    a throwaway registry + this seller's real runtime URL. The record
    reached `CREATE_FAILED` with `statusReason: "MCP server returned HTTP
    403"` - SigV4-signed requests don't carry the Cognito JWT this
    runtime's authorizer requires, so the runtime correctly rejects them.
  - `OAUTH` mode: at the time the above was written, this project's only
    Cognito app client (`adcp-buyer-agent-client`, the one this seller's
    authorizer trusts) had no client secret, no configured OAuth flows and
    no resource server - it was created for `USER_PASSWORD_AUTH`
    (`get_test_user_access_token()`'s username/password flow), not
    `client_credentials`, so `CreateOauth2CredentialProvider` against it
    failed validation. Standing up the missing pieces was judged to be new
    identity infrastructure, which design.md's "no new identity
    infrastructure" intent and this spec's R7 explicitly avoided.

## UPDATE: that decision was reversed, deliberately, and URL sync now works

The R7 avoidance was reconsidered and the identity infrastructure was
built. `agents/buyer/reference-buyer/deploy_cognito_m2m.py` adds, beside
the untouched public browser client:

  - a Cognito hosted **domain** (there is no `/oauth2/token` endpoint
    without one),
  - a **resource server** with the single scope `adcp-agents/invoke`
    (a `client_credentials` token carries scopes and nothing else, so a
    scopeless token would be meaningless),
  - a **confidential app client** with a secret and
    `AllowedOAuthFlows=['client_credentials']`.

`deploy_registry_oauth_provider.py` then registers those credentials as an
AgentCore Identity credential provider, which is what the crawler uses to
mint tokens. `render_agentcore_auth.py` puts the new client on every
agent's `allowedClients`, so the runtime accepts it.

The public client was **not** converted: `GenerateSecret` is immutable in
Cognito, and a browser client must stay public regardless - a secret
shipped to a browser is not a secret. Two clients for two genuinely
different threat models.

Verified end to end before enabling this path: a real token was requested
from the token endpoint (`token_use=access`, `scope=adcp-agents/invoke`,
no `aud` claim - `client_credentials` tokens carry `client_id` instead),
and `auth.py` accepted it against the live JWKS while still rejecting both
a non-allowlisted client and a tampered signature.

**One consequence worth knowing:** a `client_credentials` token's `sub`
IS the app client id, not a user - confirmed against the real token. This
file's own `caller_identity` derivation uses `sub` for idempotency
scoping, so every caller sharing one app client shares one idempotency
namespace, which is the cross-principal collision that scoping exists to
prevent. Use one app client per calling principal
(`deploy_cognito_m2m.py --client-name`) rather than sharing this one.

The tool listing below is still fetched live from the deployed seller, so
the record is correct at creation time regardless of sync; URL sync is
what keeps it current afterwards without a re-run.

## Descriptor format notes (also empirically discovered, not documented
   clearly in the API reference at the time of writing)

- `descriptors.mcp.server.inlineContent` must be an MCP Registry
  server.json-shaped document (`name`, `description`, `version`,
  `remotes: [{type, url}]`) - a bare MCP `initialize` response's
  `serverInfo` shape (`{name, version}`) is rejected with "does not match
  any supported version".
- `descriptors.mcp.tools.inlineContent` must be `{"tools": [...]}` where
  each tool has `name`, `description`, and `inputSchema` (a bare
  `{type: "object"}` placeholder is accepted - the registry doesn't
  require the full JSON Schema, just the key's presence). Each tool's raw
  `list_tools()` schema is far too large for the 100KB inlineContent
  limit (this seller's `create_media_buy`/`update_media_buy` schemas
  alone are >2MB each, thanks to full AdCP schema refs) - so only
  `name`+`description` are carried through, with a placeholder
  `inputSchema`, to stay under that limit while keeping the tool listing
  itself real and current.
"""

import json
import os
import sys
import time
import urllib.parse
from pathlib import Path

import boto3
from dotenv import load_dotenv

from aws_region import region

REGION = region()
REGISTRY_NAME = "adcp-reference-seller-registry"
RECORD_NAME = "adcp-reference-test-seller"

load_dotenv(Path(__file__).parent / ".env")

SELLER_RUNTIME_ARN = os.environ["SELLER_RUNTIME_ARN"]
COGNITO_DISCOVERY_URL = os.environ["COGNITO_DISCOVERY_URL"]
COGNITO_ALLOWED_CLIENT_ID = os.environ["COGNITO_CLIENT_ID"]

# Same directory the buyer agent's other deploy_*.py scripts read Cognito
# credentials from (agents/buyer/reference-buyer/.env, via
# auth.get_test_user_access_token - see this project's README "Deployment"
# section).
AGENTCORE_DIR = Path(__file__).resolve().parents[5] / "agents" / "buyer" / "reference-buyer"

control = boto3.client("bedrock-agentcore-control", region_name=REGION)


#: Name of the AgentCore Identity credential provider holding the confidential app client's
#: credentials. Created by agents/buyer/reference-buyer/deploy_registry_oauth_provider.py, which
#: names it per-instance -- so this must derive from the same INSTANCE_PREFIX to find it.
OAUTH_PROVIDER_NAME = f"{os.environ.get('INSTANCE_PREFIX', 'adcp').strip() or 'adcp'}-agents-m2m"


def oauth_provider_arn() -> str:
    """The credential provider's ARN, composed from the account in use.

    `REGISTRY_OAUTH_PROVIDER_ARN` overrides. The default used to be a literal ARN naming one account,
    which is the account it would keep pointing at after being deployed anywhere else.
    """
    override = os.environ.get("REGISTRY_OAUTH_PROVIDER_ARN", "").strip()
    if override:
        return override
    account = os.environ.get("AWS_ACCOUNT_ID", "").strip() or boto3.client(
        "sts"
    ).get_caller_identity()["Account"]
    return (
        f"arn:aws:bedrock-agentcore:{REGION}:{account}:token-vault/default"
        f"/oauth2credentialprovider/{OAUTH_PROVIDER_NAME}"
    )

#: The scope the crawler must request. Cognito refuses to issue a client_credentials token without one,
#: and the credential provider has no scope field of its own — scope is supplied per token request, so
#: it has to be named here rather than at provider creation.
OAUTH_SCOPES = [os.environ.get("REGISTRY_OAUTH_SCOPE", "adcp-agents/invoke")]

#: URL sync is opt-in rather than the default.
#:
#: The crawler calls this seller and sets the record's status from the outcome, so a misconfigured sync
#: does not degrade quietly — it moves a working, APPROVED record to a FAILED state. That is exactly what
#: the earlier IAM attempt produced ("MCP server returned HTTP 403"). Enabling it is therefore a
#: deliberate act with a verification step, not a default that flips on for anyone re-running this script
#: to refresh a tool listing.
URL_SYNC_ENABLED = os.environ.get("REGISTRY_URL_SYNC", "").lower() in ("1", "true", "yes")


def _record_description() -> str:
    """Describe how this record is actually maintained.

    Kept in one place and derived from URL_SYNC_ENABLED because the previous hard-coded text ("URL-sync
    verified not viable...") outlived the fact it described: the sync path works now, and a record whose
    own description tells a reader the opposite is worse than one with no description.
    """
    if URL_SYNC_ENABLED:
        return (
            "AdCP reference test seller - tool listing kept current by the registry's URL-sync crawler, "
            "authenticated with a Cognito client_credentials token via AgentCore Identity."
        )
    return (
        "AdCP reference test seller - manually registered; URL sync is available but not enabled for "
        "this record (set REGISTRY_URL_SYNC=1). Re-run this script after tool changes to refresh the "
        "listing below."
    )


def _url_sync_kwargs(*, wrapped: bool = False) -> dict:
    """The `synchronizationType`/`synchronizationConfiguration` pair, or nothing at all.

    Returned as kwargs so the no-sync path passes literally no sync arguments, rather than passing
    something empty and relying on the API to treat that as absent.

    `wrapped=True` for `UpdateRegistryRecord`, which takes its optional parameters inside
    `{"optionalValue": ...}` while `CreateRegistryRecord` takes them directly — the same asymmetry this
    file already documents for `description` and `descriptors`. Found the hard way here too: passing the
    create shape to update fails validation with "Unknown parameter in synchronizationConfiguration:
    fromUrl, must be one of: optionalValue".
    """
    if not URL_SYNC_ENABLED:
        print("  URL sync: disabled (set REGISTRY_URL_SYNC=1 to enable)")
        return {}
    print(f"  URL sync: OAUTH via {oauth_provider_arn()}")
    print(f"            scopes={OAUTH_SCOPES}")
    sync_type = "URL"
    sync_config = {
            "fromUrl": {
                "url": _seller_invoke_url(),
                "credentialProviderConfigurations": [
                    {
                        "credentialProviderType": "OAUTH",
                        "credentialProvider": {
                            "oauthCredentialProvider": {
                                "providerArn": oauth_provider_arn(),
                                "grantType": "CLIENT_CREDENTIALS",
                                "scopes": OAUTH_SCOPES,
                            }
                        },
                    }
                ],
            }
    }
    if wrapped:
        return {
            "synchronizationType": {"optionalValue": sync_type},
            "synchronizationConfiguration": {"optionalValue": sync_config},
        }
    return {"synchronizationType": sync_type, "synchronizationConfiguration": sync_config}


def _seller_invoke_url() -> str:
    escaped = urllib.parse.quote(SELLER_RUNTIME_ARN, safe="")
    return (
        f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/"
        f"{escaped}/invocations?qualifier=DEFAULT"
    )


def get_or_create_registry() -> str:
    """Returns the registry's ARN. Idempotent via ListRegistries by name."""
    paginator = control.get_paginator("list_registries")
    for page in paginator.paginate():
        for reg in page["registries"]:
            if reg["name"] == REGISTRY_NAME:
                print(f"Using existing registry: {reg['registryArn']} (status={reg['status']})")
                return reg["registryArn"]

    print(f"Creating registry: {REGISTRY_NAME}")
    resp = control.create_registry(
        name=REGISTRY_NAME,
        description=(
            "AgentCore Registry catalog for AdCP sellers deployed in this "
            "AWS account (AAO-registry substitute, see "
            ".kiro/specs/seller-agent-adcp-compliance/design.md's "
            "'Why the AgentCore Registry, not AAO')."
        ),
        authorizerType="CUSTOM_JWT",
        authorizerConfiguration={
            "customJWTAuthorizer": {
                "discoveryUrl": COGNITO_DISCOVERY_URL,
                "allowedClients": [COGNITO_ALLOWED_CLIENT_ID],
            }
        },
        # Auto-approve: this is a single-operator sandbox registry, not a
        # multi-tenant marketplace with a separate curator role.
        approvalConfiguration={"autoApproval": True},
    )
    registry_arn = resp["registryArn"]

    print("Waiting for registry to become READY...")
    for _ in range(60):
        status = control.get_registry(registryId=registry_arn)["status"]
        if status == "READY":
            break
        if status not in ("CREATING",):
            raise RuntimeError(f"Registry entered unexpected status: {status}")
        time.sleep(3)
    else:
        raise RuntimeError("Timed out waiting for registry to become READY.")

    print(f"Registry READY: {registry_arn}")
    return registry_arn


def _fetch_live_tool_listing() -> list[dict]:
    """Fetch this seller's real, currently-advertised tools via a live MCP
    call (not cached) - same auth path this project's other
    verification scripts use. Falls back to an empty list (with a loud
    warning, never a fake list) if the live call fails, so a transient
    failure here can't silently write stale tool data.
    """
    sys.path.insert(0, str(AGENTCORE_DIR))
    try:
        from dotenv import load_dotenv

        load_dotenv(AGENTCORE_DIR / ".env")
        from auth import get_test_user_access_token  # type: ignore
    except ImportError as exc:
        print(f"WARNING: could not import buyer agent's auth helper ({exc}); "
              f"tool listing will be empty in this record. Re-run task 11's "
              f"verification manually if this happens.")
        return []

    import asyncio

    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async def _fetch() -> list[dict]:
        token = get_test_user_access_token()
        headers = {"authorization": f"Bearer {token}"}
        async with streamablehttp_client(_seller_invoke_url(), headers=headers) as (
            read,
            write,
            _,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                return [
                    {
                        "name": t.name,
                        "description": t.description or "",
                        # Real per-tool JSON Schemas are far too large for
                        # the registry's 100KB inlineContent limit (see
                        # module docstring) - a placeholder satisfies the
                        # registry's schema requirement without truncating
                        # or substituting the actual schema content.
                        "inputSchema": {"type": "object"},
                    }
                    for t in result.tools
                ]

    try:
        return asyncio.run(_fetch())
    except Exception as exc:  # noqa: BLE001 - report rather than substitute a fallback list
        print(f"WARNING: live tools/list call failed ({exc}); "
              f"tool listing will be empty in this record.")
        return []


def get_or_create_record(registry_arn: str) -> str:
    """Returns the record's ARN. Idempotent via ListRegistryRecords by name.
    Always re-fetches the live tool listing and updates the record so a
    re-run picks up newly added/removed tools (manual re-registration step,
    since URL-sync isn't available here - see module docstring).
    """
    existing_record_id: str | None = None
    paginator = control.get_paginator("list_registry_records")
    for page in paginator.paginate(registryId=registry_arn):
        for rec in page["registryRecords"]:
            if rec["name"] == RECORD_NAME:
                existing_record_id = rec["recordArn"]
                print(f"Found existing record: {existing_record_id} (status={rec['status']})")

    tools = _fetch_live_tool_listing()
    print(f"Fetched {len(tools)} live tool(s) from the deployed seller: "
          f"{[t['name'] for t in tools]}")

    server_content = json.dumps(
        {
            "name": "io.adcp/reference-test-seller",
            # The registry's schema-version auto-detection for
            # mcp.server.inlineContent silently rejects the whole document
            # ("does not match any supported version") once this field
            # exceeds 100 characters - confirmed empirically by bisecting a
            # filler string against this same registry, not documented in
            # the API reference at the time of writing. Kept short and
            # factual rather than truncated mid-word.
            "description": "AdCP reference test seller (sandbox, MCP, AgentCore Runtime).",
            "version": "1.0.0",
            "remotes": [{"type": "streamable-http", "url": _seller_invoke_url()}],
        }
    )
    tools_content = json.dumps({"tools": tools})

    descriptors = {
        "mcp": {
            "server": {"inlineContent": server_content},
            "tools": {"inlineContent": tools_content},
        }
    }

    if existing_record_id is not None:
        print("Updating existing record with the current live tool listing...")
        # UpdateRegistryRecord wraps nullable fields in an
        # {"optionalValue": ...} envelope (unlike CreateRegistryRecord,
        # which takes them directly) - confirmed against the live service
        # model, not documented as a difference in the API reference.
        resp = control.update_registry_record(
            registryId=registry_arn,
            recordId=existing_record_id,
            # Same sync kwargs as the create path. `UpdateRegistryRecord` accepts
            # `synchronizationType`/`synchronizationConfiguration` (verified against the live service
            # model), so enabling sync on an already-registered record does not require deleting it.
            **_url_sync_kwargs(wrapped=True),
            description={
                "optionalValue": _record_description()
            },
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
        record_arn = existing_record_id
    else:
        print(f"Creating registry record: {RECORD_NAME}")
        resp = control.create_registry_record(
            registryId=registry_arn,
            name=RECORD_NAME,
            description=_record_description(),
            descriptorType="MCP",
            descriptors=descriptors,
            recordVersion="1.0",
            **_url_sync_kwargs(),
        )
        record_arn = resp["recordArn"]

    print("Waiting for record to leave CREATING/UPDATING...")
    for _ in range(20):
        status = control.get_registry_record(registryId=registry_arn, recordId=record_arn)["status"]
        if status not in ("CREATING", "UPDATING"):
            break
        time.sleep(2)
    else:
        raise RuntimeError("Timed out waiting for record to finish (un)creating.")

    if status in ("CREATE_FAILED", "UPDATE_FAILED"):
        detail = control.get_registry_record(registryId=registry_arn, recordId=record_arn)
        raise RuntimeError(f"Record failed: {detail.get('statusReason')}")

    print(f"Record status: {status}")

    if status == "DRAFT":
        print("Submitting record for approval...")
        approval = control.submit_registry_record_for_approval(
            registryId=registry_arn, recordId=record_arn
        )
        print(f"Record status after submission: {approval['status']}")

    return record_arn


def main() -> None:
    registry_arn = get_or_create_registry()
    record_arn = get_or_create_record(registry_arn)
    print("\nRegistration complete.")
    print(f"  Registry ARN: {registry_arn}")
    print(f"  Record ARN:   {record_arn}")


if __name__ == "__main__":
    main()
