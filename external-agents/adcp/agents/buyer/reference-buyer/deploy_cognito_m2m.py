"""Add machine-to-machine (client_credentials) auth to the existing Cognito pool.

    uv run python deploy_cognito_m2m.py            # dry run, changes nothing
    uv run python deploy_cognito_m2m.py --apply

## Why this is a SECOND app client and not a change to the existing one

`adcp-buyer-agent-client` was created with `GenerateSecret=False` for `USER_PASSWORD_AUTH`, and it is what
the browser UI uses. Two reasons it cannot become the M2M client:

1. **`GenerateSecret` is fixed at creation.** Cognito has no operation that adds a secret to an existing app
   client, so conversion is not on the table even if it were desirable.
2. **It must stay public.** A secret shipped to a browser is not a secret. The human sign-in path and the
   agent-to-agent path have different threat models, and one client cannot serve both.

So the public client is left exactly as it is and this adds a confidential one beside it.

## Why M2M is needed at all — narrower than it first appears

Registering a record in the AWS Agent Registry already works with our IAM credentials; the seller's record
is live and approved. What does **not** work is the registry's **URL-sync crawler**, which keeps a record's
tool listing current by calling the agent itself. That crawler authenticates outbound in exactly two ways,
and this project tested both:

* `IAM` (SigV4) — **fails.** Confirmed live: the record reached `CREATE_FAILED` with
  `"MCP server returned HTTP 403"`, because a SigV4 request carries no Cognito JWT and the runtime's
  `CUSTOM_JWT` authorizer correctly rejects it.
* `OAUTH` (AgentCore Identity credential provider, `client_credentials`) — **fails today** for the reason
  this script fixes: no client secret, no OAuth flows, no domain, no resource server.

So the benefit is automatic tool-listing sync, replacing a manual re-run. It is not what makes registration
possible, and it is worth being precise about that because it sets how much this is worth.

## Three pieces of infrastructure, not one

`client_credentials` needs all of these, and the pool has none of them:

| Piece | Why |
|---|---|
| A **domain** | There is no `/oauth2/token` endpoint without one |
| A **resource server** with scopes | `client_credentials` tokens carry scopes and nothing else; a token with no scope has no meaning |
| A **confidential client** | The secret, plus `AllowedOAuthFlows=['client_credentials']` |

This is the "new identity infrastructure" the seller spec's R7 deliberately avoided. Adding it now reverses
that decision knowingly.

## Where the secret goes

**Secrets Manager, not `.env`.** The existing setup script writes a test-user password into `.env`, and that
is defensible for a throwaway human test account. A client secret that authenticates a machine principal
against every agent in the account is a different class of credential. Only non-secret identifiers are
written to `.env`; the secret is put in Secrets Manager and never printed.

## The consequence to handle before relying on this

`main.py` in the reference seller derives `caller_identity` from the access token's `sub`, and its own
docstring records why a shared identity is dangerous: it collapses distinct buyers into one idempotency
namespace, which is the cross-principal replay the SDK warns about.

**A `client_credentials` token has no human principal** — `sub` is the app client. So every caller sharing
one M2M client shares one idempotency namespace, and the bug that docstring was written to fix returns by a
different route. The mitigation is one app client per calling principal, which is why the client name below
is a parameter rather than a constant. Verified against a real token at the end of this script, which prints
the `sub` it actually observes rather than asserting what it should be.
"""

from __future__ import annotations

from aws_region import region

import argparse
import base64
import json
import re
import ssl
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import os

import boto3
import certifi
from botocore.exceptions import ClientError

REGION = region()
#: Instance prefix (see deploy_all.resolve_prefix). Must match deploy_cognito_setup.py's pool name.
INSTANCE_PREFIX = os.environ.get("INSTANCE_PREFIX", "adcp").strip() or "adcp"
USER_POOL_NAME = f"{INSTANCE_PREFIX}-buyer-agent-users"

#: The resource server and its single scope.
#:
#: One coarse scope rather than a per-agent set: every agent in this account trusts the same pool and none of
#: them distinguishes scopes today, so finer scopes would be decoration. Add real ones when an agent actually
#: authorises on them.
RESOURCE_SERVER_ID = "adcp-agents"
RESOURCE_SERVER_NAME = "AdCP agents"
SCOPE_NAME = "invoke"
FULL_SCOPE = f"{RESOURCE_SERVER_ID}/{SCOPE_NAME}"

ENV_PATH = Path(__file__).parent / ".env"
ENV_EXAMPLE_PATH = Path(__file__).parent / ".env.example"

cognito = boto3.client("cognito-idp", region_name=REGION)
secrets = boto3.client("secretsmanager", region_name=REGION)
sts = boto3.client("sts", region_name=REGION)


def account_id() -> str:
    return sts.get_caller_identity()["Account"]


def domain_prefix() -> str:
    """A globally-unique-within-region domain prefix.

    Cognito rejects prefixes containing "cognito" and requires lowercase alphanumerics and hyphens. The
    account id makes it unique without encoding anything sensitive — an account id is not a secret. The
    instance prefix keeps two instances in the same account from colliding on this globally-unique
    domain (the account id alone would be identical for both).
    """
    return f"{INSTANCE_PREFIX}-agents-{account_id()}"


def token_endpoint() -> str:
    return f"https://{domain_prefix()}.auth.{REGION}.amazoncognito.com/oauth2/token"


def find_user_pool() -> str:
    for page in cognito.get_paginator("list_user_pools").paginate(MaxResults=60):
        for pool in page["UserPools"]:
            if pool["Name"] == USER_POOL_NAME:
                return pool["Id"]
    raise SystemExit(
        f"User pool {USER_POOL_NAME!r} not found. Run deploy_cognito_setup.py first — this script adds to "
        "an existing pool rather than creating one, so the human sign-in path stays untouched."
    )


def ensure_domain(pool_id: str, apply: bool) -> bool:
    """Create the hosted domain if absent. Returns True when it is usable."""
    prefix = domain_prefix()
    try:
        described = cognito.describe_user_pool_domain(Domain=prefix)
        status = (described.get("DomainDescription") or {}).get("Status")
        if status:
            print(f"Domain exists: {prefix} (status={status})")
            # Reported rather than waited on. A domain still provisioning will make the token request below
            # fail with a clear DNS error, which is more informative than this script guessing a timeout.
            return status == "ACTIVE"
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in ("ResourceNotFoundException", "InvalidParameterException"):
            raise

    if not apply:
        print(f"Would create Cognito domain: {prefix}")
        return False

    print(f"Creating Cognito domain: {prefix}")
    cognito.create_user_pool_domain(Domain=prefix, UserPoolId=pool_id)
    print(
        "  Domain created. Cognito provisions it behind CloudFront, so it can take a few minutes to become\n"
        "  ACTIVE. If the token check at the end fails with a DNS error, that is why — re-run then."
    )
    return False


def ensure_resource_server(pool_id: str, apply: bool) -> bool:
    try:
        existing = cognito.describe_resource_server(
            UserPoolId=pool_id, Identifier=RESOURCE_SERVER_ID
        )["ResourceServer"]
        scopes = {s["ScopeName"] for s in existing.get("Scopes", [])}
        print(f"Resource server exists: {RESOURCE_SERVER_ID} (scopes: {sorted(scopes)})")
        if SCOPE_NAME in scopes:
            return True
        if not apply:
            print(f"Would add scope {SCOPE_NAME!r} to {RESOURCE_SERVER_ID}")
            return False
        print(f"Adding scope {SCOPE_NAME!r}")
        cognito.update_resource_server(
            UserPoolId=pool_id,
            Identifier=RESOURCE_SERVER_ID,
            Name=RESOURCE_SERVER_NAME,
            Scopes=[{"ScopeName": SCOPE_NAME, "ScopeDescription": "Invoke AdCP agents"}],
        )
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceNotFoundException":
            raise

    if not apply:
        print(f"Would create resource server {RESOURCE_SERVER_ID} with scope {FULL_SCOPE}")
        return False

    print(f"Creating resource server: {RESOURCE_SERVER_ID}")
    cognito.create_resource_server(
        UserPoolId=pool_id,
        Identifier=RESOURCE_SERVER_ID,
        Name=RESOURCE_SERVER_NAME,
        Scopes=[{"ScopeName": SCOPE_NAME, "ScopeDescription": "Invoke AdCP agents"}],
    )
    return True


def ensure_m2m_client(pool_id: str, client_name: str, apply: bool) -> tuple[str, str] | None:
    """Create or find the confidential client. Returns (client_id, client_secret)."""
    for page in cognito.get_paginator("list_user_pool_clients").paginate(
        UserPoolId=pool_id, MaxResults=60
    ):
        for client in page["UserPoolClients"]:
            if client["ClientName"] == client_name:
                client_id = client["ClientId"]
                described = cognito.describe_user_pool_client(
                    UserPoolId=pool_id, ClientId=client_id
                )["UserPoolClient"]
                secret = described.get("ClientSecret")
                if not secret:
                    raise SystemExit(
                        f"App client {client_name!r} exists but has NO secret. Cognito cannot add one to an "
                        "existing client, so this needs a differently-named client — pass --client-name."
                    )
                print(f"Using existing M2M client: {client_id}")
                return client_id, secret

    if not apply:
        print(f"Would create confidential app client {client_name!r} with a secret and {FULL_SCOPE}")
        return None

    # Ordering is load-bearing and was confirmed by probing this pool: referencing a scope whose resource
    # server does not exist yet fails with ScopeDoesNotExistException. The resource server is therefore
    # created before this call, and must stay that way.
    print(f"Creating confidential app client: {client_name}")
    created = cognito.create_user_pool_client(
        UserPoolId=pool_id,
        ClientName=client_name,
        # The whole point. Immutable after creation.
        GenerateSecret=True,
        # No user-auth flows at all: this client authenticates a machine, and a user flow on it would let a
        # stolen secret be used against user accounts. Verified by probe against this pool that `[]` is
        # accepted and stores as absent. Omitting the parameter happens to behave identically here rather
        # than falling back to the legacy SRP defaults, so this is explicitness for the reader, not a fix.
        ExplicitAuthFlows=[],
        AllowedOAuthFlows=["client_credentials"],
        AllowedOAuthScopes=[FULL_SCOPE],
        AllowedOAuthFlowsUserPoolClient=True,
        SupportedIdentityProviders=["COGNITO"],
        # An hour, matching the human path's default rather than introducing a second lifetime.
        AccessTokenValidity=1,
        TokenValidityUnits={"AccessToken": "hours"},
        PreventUserExistenceErrors="ENABLED",
    )
    client = created["UserPoolClient"]
    return client["ClientId"], client["ClientSecret"]


def _scope_missing(exc: ClientError) -> bool:
    return exc.response["Error"]["Code"] == "ScopeDoesNotExistException"


def store_secret(client_name: str, client_id: str, client_secret: str, apply: bool) -> str:
    """Put the secret in Secrets Manager. Returns the secret name."""
    name = f"adcp/cognito/{client_name}"
    payload = json.dumps(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "token_endpoint": token_endpoint(),
            "scope": FULL_SCOPE,
        }
    )
    if not apply:
        print(f"Would store the client secret in Secrets Manager as {name}")
        return name
    try:
        secrets.create_secret(
            Name=name,
            Description=(
                "Cognito client_credentials credentials for AdCP agent-to-agent calls and the AWS Agent "
                "Registry URL-sync crawler."
            ),
            SecretString=payload,
        )
        print(f"Stored secret: {name}")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceExistsException":
            raise
        secrets.put_secret_value(SecretId=name, SecretString=payload)
        print(f"Updated secret: {name}")
    return name


def fetch_token(client_id: str, client_secret: str) -> dict:
    """Actually get a token. The only way to know the flow works.

    HTTP Basic with the client id and secret, which is what Cognito expects for a confidential client — the
    credentials do not go in the body.

    Uses certifi's CA bundle explicitly. A framework Python on macOS often has no usable system trust store
    for `urllib`, which surfaces as CERTIFICATE_VERIFY_FAILED against a perfectly healthy endpoint — observed
    here, and misleading enough to be worth pinning rather than leaving to the environment.
    """
    context = ssl.create_default_context(cafile=certifi.where())
    body = urllib.parse.urlencode(
        {"grant_type": "client_credentials", "scope": FULL_SCOPE}
    ).encode()
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    request = urllib.request.Request(
        token_endpoint(),
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {basic}",
        },
    )
    with urllib.request.urlopen(request, timeout=15, context=context) as response:
        return json.loads(response.read())


def describe_token(token: str) -> dict:
    """Claims, without verifying — this is a provisioning check, not an auth path."""
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def _read_env_value(key: str) -> str | None:
    if not ENV_PATH.exists():
        return None
    pattern = re.compile(rf"^{re.escape(key)}=(.*)$")
    for line in ENV_PATH.read_text().splitlines():
        m = pattern.match(line.strip())
        if m:
            return m.group(1).strip()
    return None


def _upsert_env_values(values: dict[str, str]) -> None:
    if not ENV_PATH.exists():
        ENV_PATH.write_text(ENV_EXAMPLE_PATH.read_text() if ENV_EXAMPLE_PATH.exists() else "")
    lines = ENV_PATH.read_text().splitlines()
    seen = set()
    for i, line in enumerate(lines):
        for key, value in values.items():
            if line.strip().startswith(f"{key}="):
                lines[i] = f"{key}={value}"
                seen.add(key)
    for key, value in values.items():
        if key not in seen:
            lines.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="actually create the infrastructure")
    parser.add_argument(
        "--client-name",
        default=f"{INSTANCE_PREFIX}-agents-m2m",
        help=(
            "App client name. One client per calling PRINCIPAL is the right shape — see the module "
            "docstring on idempotency scoping — so this is a parameter, not a constant. Defaults to "
            "'<instance-prefix>-agents-m2m'; its Secrets Manager secret (adcp/cognito/<client-name>) "
            "and the AgentCore Identity provider that consumes it (deploy_registry_oauth_provider.py) "
            "are account-level, so the prefix keeps two instances from overwriting each other's."
        ),
    )
    args = parser.parse_args()

    if not re.fullmatch(r"[A-Za-z0-9_.:/=+\-]+", args.client_name):
        raise SystemExit(f"--client-name {args.client_name!r} has characters Cognito will reject.")

    pool_id = find_user_pool()
    print(f"User pool: {pool_id}  (existing — the public client is not touched)")
    print(f"Token endpoint (once the domain is ACTIVE): {token_endpoint()}")
    print()

    domain_ready = ensure_domain(pool_id, args.apply)
    resource_ready = ensure_resource_server(pool_id, args.apply)
    try:
        credentials = ensure_m2m_client(pool_id, args.client_name, args.apply)
    except ClientError as exc:
        if not _scope_missing(exc):
            raise
        raise SystemExit(
            f"Cognito rejected scope {FULL_SCOPE!r} as non-existent while creating the app client.\n"
            "The resource server was created moments ago in this same run, so this is propagation delay.\n"
            "This script is idempotent — re-run it and the client will be created."
        ) from exc

    if not args.apply:
        print("\nDry run. Nothing was created. Re-run with --apply.")
        return 0

    if credentials is None:
        raise SystemExit("App client was not created; cannot continue.")
    client_id, client_secret = credentials
    del credentials
    secret_name = store_secret(args.client_name, client_id, client_secret, args.apply)

    # COGNITO_ADDITIONAL_CLIENT_IDS is the ACCUMULATOR and the only one that grants access.
    #
    # This script is designed to be run once per calling principal (see the docstring on idempotency
    # scoping), so single-valued keys cannot express its output: an earlier version wrote only
    # COGNITO_M2M_CLIENT_ID, and running it for five agents left that key holding the fifth while the
    # other four were absent from every allowlist and would have been rejected. Appending is therefore
    # the correct behaviour, not a convenience.
    #
    # `render_agentcore_auth.py` and `auth.py` both read this key, so appending here is what actually
    # admits the client once those are re-run and the runtimes redeployed.
    existing = [c.strip() for c in (_read_env_value("COGNITO_ADDITIONAL_CLIENT_IDS") or "").split(",") if c.strip()]
    merged = list(dict.fromkeys([*existing, client_id]))
    _upsert_env_values(
        {
            "COGNITO_ADDITIONAL_CLIENT_IDS": ",".join(merged),
            # Retained for the single-client case and for a human reading the file, but NOT
            # authoritative -- deliberately last-writer-wins rather than pretending to hold a set.
            "COGNITO_M2M_CLIENT_ID": client_id,
            "COGNITO_M2M_SECRET_NAME": secret_name,
            "COGNITO_M2M_TOKEN_ENDPOINT": token_endpoint(),
            "COGNITO_M2M_SCOPE": FULL_SCOPE,
        }
    )
    print(f"\nCOGNITO_ADDITIONAL_CLIENT_IDS now lists {len(merged)} client(s).")
    print("The client secret was NOT written to .env — it is in Secrets Manager only.")

    print("\n=== Verifying the flow by actually requesting a token ===")
    if not (domain_ready and resource_ready):
        print(
            "Skipped: the domain or resource server was created in this run and may still be provisioning.\n"
            "Re-run this script (it is idempotent) to verify once the domain is ACTIVE."
        )
    else:
        try:
            token_response = fetch_token(client_id, client_secret)
            claims = describe_token(token_response["access_token"])
            print(f"  token_use : {claims.get('token_use')}")
            print(f"  client_id : {claims.get('client_id')}")
            print(f"  scope     : {claims.get('scope')}")
            print(f"  sub       : {claims.get('sub')}")
            print(f"  aud       : {claims.get('aud', '(absent, as expected for client_credentials)')}")
            if claims.get("sub") == client_id:
                print(
                    "\n  NOTE, and it is the one that matters: `sub` IS the app client id, not a user.\n"
                    "  The reference seller uses `sub` as `caller_identity` for idempotency scoping, so\n"
                    "  every caller sharing this client shares one idempotency namespace. Use one client\n"
                    "  per principal (--client-name) rather than sharing this one."
                )
        except Exception as exc:  # noqa: BLE001 - provisioning check, report and continue
            print(f"  Token request failed: {type(exc).__name__}: {exc}")
            print("  If this is a DNS failure the domain is still provisioning. Re-run shortly.")

    print("\n=== Remaining manual step ===")
    print(
        f"Add {client_id} to `authorizerConfiguration.customJwtAuthorizer.allowedClients` in every\n"
        "agentcore.json that should accept this client, then redeploy those runtimes:\n"
        "  agents/seller/reference-seller/agentcore/agentcore.json\n"
        "  agents/seller/poseidon-seller/agentcore/agentcore.json\n"
        "  agents/seller/gotham-seller/agentcore/agentcore.json\n"
        "  agents/governance/reference-governance/agentcore/agentcore.json\n"
        "Until then those runtimes will reject this client's tokens — correctly, since it is not on their\n"
        "allowlist. `auth.py` also compares a single COGNITO_CLIENT_ID and needs to accept a set."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
