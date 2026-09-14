"""Cognito Identity Pool so the browser can read the sessions table directly.

    python deploy_identity_pool.py --apply

## Why this exists

The journey dashboard polls for new steps. Those reads used to go through the AgentCore runtime's
`/invocations` endpoint, because the browser holds a Cognito **user pool** JWT and that is not an AWS
credential — it cannot call DynamoDB. Routing reads through the runtime kept one auth story, at a
measured cost:

    two DynamoDB reads, the actual work        245 ms
    the same read via /invocations           1,089 ms
    -> transport overhead                      843 ms

Payload size was irrelevant: 565 bytes and 18 KB both cost ~1.1 s. An Identity Pool exchanges the
user pool's ID token for real (temporary, scoped) IAM credentials, so the browser queries DynamoDB
itself and the 843 ms disappears.

## What this grants, stated plainly

**Every signed-in user can read every session in this table**, including other users' prompts and
tool payloads.

That is a deliberate decision, not an oversight, and it is forced by the table's shape rather than
chosen for convenience. IAM's `dynamodb:LeadingKeys` can only constrain the PARTITION key. This
table's partition key is `SESSION#<session_id>`, and the owner (`invoker`) is a plain attribute on
the META item, not part of any key. The session list is worse: it queries `gsi1` where `gsi1pk` is a
single constant for every session in the account. So there is no key expression that means "only my
own sessions", and no policy that can enforce it.

Per-user isolation would need the table re-keyed on the owner (`pk = USER#<sub>#SESSION#<id>`) plus a
backfill, or the reads kept server-side behind a Lambda where authorization can be expressed in code.
Both were considered and deferred; this is the sandbox-appropriate choice for a reference
implementation where every signed-in user is an operator.

Mitigations that ARE in place:

  - **Read-only.** Query and GetItem, nothing else. A compromised browser credential cannot write,
    delete or alter a recorded session, so the audit trail is not forgeable from the front end.
  - **Scoped to one table.** The policy names this table and its index, not `dynamodb:*`.
  - **Authenticated identities only.** Unauthenticated access to the pool is disabled, so a caller
    with no user pool token gets no credentials at all.
  - **Short-lived.** Identity Pool credentials expire and are re-fetched; there is no long-lived key
    in the browser.

Idempotent, and read-only unless `--apply` is passed — same convention as this project's other
deploy scripts.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from aws_region import region

ENV_PATH = Path(__file__).parent / ".env"
ENV_EXAMPLE_PATH = Path(__file__).parent / ".env.example"

#: Instance prefix (see deploy_all.resolve_prefix). Identity-pool name uses the underscore style it
#: already had; the IAM role/policy use the hyphen style. Defaults to 'adcp' for a standalone run.
INSTANCE_PREFIX = os.environ.get("INSTANCE_PREFIX", "adcp").strip() or "adcp"
POOL_NAME = f"{INSTANCE_PREFIX}_buyer_agent_ui"
ROLE_NAME = f"{INSTANCE_PREFIX}-buyer-agent-ui-reader"
POLICY_NAME = f"{INSTANCE_PREFIX}-buyer-agent-ui-session-reads"


def _env(key: str) -> str:
    """A required value from .env, read via os.environ after dotenv has loaded it."""
    import os

    from dotenv import load_dotenv

    load_dotenv(ENV_PATH)
    value = os.environ.get(key, "").strip()
    if not value:
        raise SystemExit(
            f"{key} is not set in {ENV_PATH}. Run the earlier deploy steps first "
            "(cognito-setup for the pool, sessions-table for the table)."
        )
    return value


def read_policy_document(table_arn: str) -> dict:
    """Read-only, this table only, plus its index.

    `Query` on the index is what the session list needs; `GetItem`/`Query` on the table itself is
    the meta read and the step range read. Nothing else is granted — notably no `Scan`, so the
    browser cannot walk the table, and no write action of any kind.
    """
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "ReadRecordedSessions",
                "Effect": "Allow",
                "Action": [
                    "dynamodb:GetItem",
                    "dynamodb:Query",
                ],
                "Resource": [table_arn, f"{table_arn}/index/*"],
            }
        ],
    }


def trust_policy(identity_pool_id: str) -> dict:
    """Only identities from THIS identity pool, and only authenticated ones.

    `amr` = "authenticated" is the condition that makes the unauthenticated path unusable even if the
    pool were later reconfigured to allow it — belt and braces with `AllowUnauthenticatedIdentities`
    being false, because that flag is one API call away from being flipped.
    """
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Federated": "cognito-identity.amazonaws.com"},
                "Action": "sts:AssumeRoleWithWebIdentity",
                "Condition": {
                    "StringEquals": {"cognito-identity.amazonaws.com:aud": identity_pool_id},
                    "ForAnyValue:StringLike": {
                        "cognito-identity.amazonaws.com:amr": "authenticated"
                    },
                },
            }
        ],
    }


def find_pool(client) -> str | None:
    paginator = {"MaxResults": 60}
    while True:
        response = client.list_identity_pools(**paginator)
        for pool in response.get("IdentityPools", []):
            if pool.get("IdentityPoolName") == POOL_NAME:
                return pool["IdentityPoolId"]
        token = response.get("NextToken")
        if not token:
            return None
        paginator["NextToken"] = token


def get_or_create_pool(client, user_pool_id: str, client_id: str, apply: bool) -> str:
    existing = find_pool(client)
    provider = {
        "ProviderName": f"cognito-idp.{region()}.amazonaws.com/{user_pool_id}",
        "ClientId": client_id,
        # The ID token must have been issued for THIS app client. Without this, a token from any
        # client on the pool would be accepted.
        "ServerSideTokenCheck": True,
    }

    if existing:
        print(f"  identity pool {POOL_NAME} exists: {existing}")
        if not apply:
            print("  (dry run) would ensure its provider list matches this user pool + client")
            return existing
        client.update_identity_pool(
            IdentityPoolId=existing,
            IdentityPoolName=POOL_NAME,
            AllowUnauthenticatedIdentities=False,
            CognitoIdentityProviders=[provider],
        )
        print("  provider list reconciled; unauthenticated identities disabled")
        return existing

    if not apply:
        print(f"  (dry run) would create identity pool {POOL_NAME}")
        return "DRY-RUN-POOL-ID"

    created = client.create_identity_pool(
        IdentityPoolName=POOL_NAME,
        AllowUnauthenticatedIdentities=False,
        CognitoIdentityProviders=[provider],
    )
    print(f"  created identity pool: {created['IdentityPoolId']}")
    return created["IdentityPoolId"]


def get_or_create_role(iam, identity_pool_id: str, table_arn: str, apply: bool) -> str:
    trust = trust_policy(identity_pool_id)
    try:
        role = iam.get_role(RoleName=ROLE_NAME)["Role"]
        print(f"  role {ROLE_NAME} exists: {role['Arn']}")
        if apply:
            # Reconciled rather than assumed: the trust policy names the pool id, and a recreated
            # pool would leave the role trusting an id that no longer exists.
            iam.update_assume_role_policy(
                RoleName=ROLE_NAME, PolicyDocument=json.dumps(trust)
            )
            print("  trust policy reconciled against the current pool id")
        role_arn = role["Arn"]
    except iam.exceptions.NoSuchEntityException:
        if not apply:
            print(f"  (dry run) would create role {ROLE_NAME}")
            return "DRY-RUN-ROLE-ARN"
        created = iam.create_role(
            RoleName=ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description=(
                "Read-only access to the buyer agent's recorded sessions, for the browser UI via "
                "Cognito Identity. Read-only by design: see deploy_identity_pool.py."
            ),
        )
        role_arn = created["Role"]["Arn"]
        print(f"  created role: {role_arn}")
        # A role is not usable the instant it exists; IAM is eventually consistent and
        # SetIdentityPoolRoles can fail with a trust-relationship error on a role created moments
        # earlier.
        time.sleep(10)

    if apply:
        iam.put_role_policy(
            RoleName=ROLE_NAME,
            PolicyName=POLICY_NAME,
            PolicyDocument=json.dumps(read_policy_document(table_arn)),
        )
        print(f"  inline policy {POLICY_NAME} written (GetItem/Query on the table + its indexes)")
    else:
        print(f"  (dry run) would write inline policy {POLICY_NAME}")

    return role_arn


def attach_role(client, identity_pool_id: str, role_arn: str, apply: bool) -> None:
    if not apply:
        print("  (dry run) would attach the authenticated role to the pool")
        return
    client.set_identity_pool_roles(
        IdentityPoolId=identity_pool_id,
        # No "unauthenticated" entry, deliberately: there is no role to assume without a token.
        Roles={"authenticated": role_arn},
    )
    print("  authenticated role attached; no unauthenticated role exists")


def _upsert_env_values(values: dict[str, str]) -> None:
    """Write/replace key=value lines in .env, preserving everything else.

    Same approach as deploy_sessions_table.py::_upsert_env_values.
    """
    if not ENV_PATH.exists():
        base = ENV_EXAMPLE_PATH.read_text() if ENV_EXAMPLE_PATH.exists() else ""
        ENV_PATH.write_text(base)

    lines = ENV_PATH.read_text().splitlines()
    seen = set()
    for i, line in enumerate(lines):
        stripped = line.strip()
        for key, value in values.items():
            if stripped.startswith(f"{key}="):
                lines[i] = f"{key}={value}"
                seen.add(key)

    for key, value in values.items():
        if key not in seen:
            lines.append(f"{key}={value}")

    ENV_PATH.write_text("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Make changes. Without it, everything is read-only and reported.",
    )
    args = parser.parse_args()

    user_pool_id = _env("COGNITO_USER_POOL_ID")
    client_id = _env("COGNITO_CLIENT_ID")
    table_name = _env("SESSIONS_TABLE_NAME")

    dynamodb = boto3.client("dynamodb", region_name=region())
    try:
        table_arn = dynamodb.describe_table(TableName=table_name)["Table"]["TableArn"]
    except ClientError as exc:
        raise SystemExit(f"Cannot read table {table_name}: {exc}") from exc

    print(f"user pool : {user_pool_id}")
    print(f"app client: {client_id}")
    print(f"table     : {table_arn}")
    print(f"mode      : {'APPLY' if args.apply else 'dry run (pass --apply to change anything)'}\n")

    identity = boto3.client("cognito-identity", region_name=region())
    iam = boto3.client("iam")

    pool_id = get_or_create_pool(identity, user_pool_id, client_id, args.apply)
    role_arn = get_or_create_role(iam, pool_id, table_arn, args.apply)
    attach_role(identity, pool_id, role_arn, args.apply)

    if args.apply:
        _upsert_env_values({"IDENTITY_POOL_ID": pool_id})
        print(f"\nWrote IDENTITY_POOL_ID={pool_id} to .env")
        print(
            "\nNOTE: every signed-in user can now read every recorded session in "
            f"{table_name}. Read-only, one table, authenticated identities only — see this "
            "script's docstring for why per-user scoping is not expressible against this "
            "table's key schema."
        )
    else:
        print("\nDry run complete. Nothing was changed.")

    if pool_id.startswith("DRY-RUN"):
        sys.exit(0)


if __name__ == "__main__":
    main()
