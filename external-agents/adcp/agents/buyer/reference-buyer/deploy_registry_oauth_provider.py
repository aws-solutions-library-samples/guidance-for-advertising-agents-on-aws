"""Create the AgentCore Identity OAuth2 credential provider used for outbound machine calls.

    uv run python deploy_registry_oauth_provider.py            # dry run
    uv run python deploy_registry_oauth_provider.py --apply

## What this is for

The AWS Agent Registry keeps a record's tool listing current by calling the agent itself (URL sync). That
outbound call authenticates in one of two ways, and this project has tested both:

* `IAM` (SigV4) — **fails.** Confirmed live: the record reached `CREATE_FAILED` with
  `"MCP server returned HTTP 403"`. A SigV4 request carries no Cognito JWT, and the runtime's `CUSTOM_JWT`
  authorizer is right to reject it.
* `OAUTH` — needs a credential provider holding `client_credentials` credentials. That is this script.

`deploy_cognito_m2m.py` created the confidential app client and put its secret in Secrets Manager. This reads
that secret and hands the credentials to AgentCore Identity, which stores its own copy and mints tokens on
demand.

## Why `CustomOauth2` and not the `CognitoOauth2` vendor

`CredentialProviderVendorType` does include `CognitoOauth2`, which looks like the obvious fit. It maps to
`includedOauth2ProviderConfig`, whose only required field is `clientId` — the endpoints are inferred. That
inference is the problem: this pool's token endpoint lives on a **hosted domain** created only for this
purpose, and a provider that guesses endpoints is a provider whose behaviour changes if that guess changes.

`CustomOauth2` requires `oauthDiscovery` instead, so the endpoints come from the pool's own OIDC discovery
document. Verified before writing this: that document advertises

    token_endpoint  https://adcp-agents-<account-id>.auth.<region>.amazoncognito.com/oauth2/token

**and it only does so because the domain now exists.** Before `deploy_cognito_m2m.py` created it, the field
was absent. So discovery is sufficient and self-describing, which is worth more here than the shorter config.

## The scope has to be requested at token time, not here

`client_credentials` tokens are meaningless without a scope, and this pool's resource server defines exactly
one (`adcp-agents/invoke`). The credential provider config has no scope field — scope is supplied by whoever
requests the token. So a caller that forgets it gets a token Cognito refuses to issue, and the registry
record's OAuth configuration is where the scope must be named. Recorded here because the split is not
obvious from either API alone.
"""

from __future__ import annotations

from aws_region import region

import argparse
import json
import os
import sys

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

load_dotenv()

REGION = os.environ.get("COGNITO_REGION") or region()
#: Instance prefix (see deploy_all.resolve_prefix). The credential provider and its backing secret
#: are account-level resources, so two instances must not share a name -- otherwise one deploy's m2m
#: client credentials overwrite the other's. Defaults to 'adcp' for a standalone run.
INSTANCE_PREFIX = os.environ.get("INSTANCE_PREFIX", "adcp").strip() or "adcp"
PROVIDER_NAME = f"{INSTANCE_PREFIX}-agents-m2m"
SECRET_ID = f"adcp/cognito/{PROVIDER_NAME}"


def discovery_url() -> str:
    """The pool's OIDC discovery document.

    Taken from `.env`, which `deploy_cognito_setup.py` writes, rather than restated here: a second
    copy of the pool id is a second thing to update when the pool is recreated, and pointing a
    credential provider at the wrong pool fails only when a token is first exchanged.
    """
    url = os.environ.get("COGNITO_DISCOVERY_URL", "").strip()
    if not url:
        raise SystemExit(
            "COGNITO_DISCOVERY_URL is not set. Run `python3 deploy_all.py --only cognito` first."
        )
    return url

control = boto3.client("bedrock-agentcore-control", region_name=REGION)
secrets = boto3.client("secretsmanager", region_name=REGION)


def load_credentials() -> tuple[str, str, str]:
    """Read the app client credentials that deploy_cognito_m2m.py stored."""
    try:
        raw = secrets.get_secret_value(SecretId=SECRET_ID)["SecretString"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ResourceNotFoundException":
            raise SystemExit(
                f"Secret {SECRET_ID!r} not found. Run deploy_cognito_m2m.py --apply first: it creates the "
                "confidential app client and stores its secret, which this script consumes."
            ) from exc
        raise
    data = json.loads(raw)
    return data["client_id"], data["client_secret"], data["scope"]


def existing_provider() -> dict | None:
    try:
        return control.get_oauth2_credential_provider(name=PROVIDER_NAME)
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("ResourceNotFoundException", "ValidationException"):
            return None
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    client_id, client_secret, scope = load_credentials()
    print(f"App client   : {client_id}")
    print(f"Scope        : {scope}  (requested at token time, not stored on the provider)")
    print(f"Discovery    : {discovery_url()}")
    print(f"Provider name: {PROVIDER_NAME}")
    print()

    found = existing_provider()
    if found is not None:
        arn = found.get("credentialProviderArn") or found.get("clientSecretArn")
        print(f"Provider already exists: {arn}")
        if not args.apply:
            print("\nDry run. Nothing changed.")
            return 0
        # Updated rather than skipped: the app client's secret can be rotated, and a provider still
        # holding the previous one fails at token time rather than here, which is a slow way to find out.
        print("Updating it so a rotated client secret is picked up.")
        control.update_oauth2_credential_provider(
            name=PROVIDER_NAME,
            credentialProviderVendor="CustomOauth2",
            oauth2ProviderConfigInput={
                "customOauth2ProviderConfig": {
                    "clientId": client_id,
                    "clientSecret": client_secret,
                    "oauthDiscovery": {"discoveryUrl": discovery_url()},
                }
            },
        )
        print("Updated.")
        return 0

    if not args.apply:
        print("Would create the credential provider (CustomOauth2, discovery-based).")
        print("\nDry run. Nothing changed.")
        return 0

    print("Creating credential provider...")
    created = control.create_oauth2_credential_provider(
        name=PROVIDER_NAME,
        credentialProviderVendor="CustomOauth2",
        oauth2ProviderConfigInput={
            "customOauth2ProviderConfig": {
                "clientId": client_id,
                "clientSecret": client_secret,
                "oauthDiscovery": {"discoveryUrl": discovery_url()},
            }
        },
    )
    arn = created.get("credentialProviderArn")
    print(f"Created: {arn}")
    print(
        "\nNext: point the registry record's URL sync at this provider with authType OAUTH, naming the\n"
        f"scope {scope!r} — see deploy_registry_registration.py."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
