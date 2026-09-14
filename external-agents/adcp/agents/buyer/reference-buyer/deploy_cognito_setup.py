"""
Creates (idempotently) the Amazon Cognito user pool, app client, and a test
user used to authenticate against the deployed AgentCore Runtime agent.

This is a deployment asset, not a one-off manual step: run it before
deploy_launch.py (or re-run it any time — it's safe to run repeatedly,
it looks up existing resources by name before creating new ones).

    source .venv/bin/activate
    python3 deploy_cognito_setup.py

Writes the resulting pool ID, client ID, and test user credentials into
.env (creating it from .env.example if missing) so deploy_launch.py and
the local dev server both pick them up automatically. The test user's
password is generated randomly and never printed in full to the terminal;
it is stored only in .env (gitignored).
"""


from aws_region import region
import os
import re
import secrets
import string
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from user_admin import ADMIN_GROUP_NAME

REGION = region()
#: Instance prefix (see deploy_all.resolve_prefix). Cognito user-pool names are NOT unique (two pools
#: can share a name), so the "find existing by name" lookup here would otherwise reuse another
#: instance's pool. Prefixing the name keeps each instance's pool separate. deploy_cognito_m2m.py must
#: use the same name. Defaults to 'adcp' for a standalone run.
INSTANCE_PREFIX = os.environ.get("INSTANCE_PREFIX", "adcp").strip() or "adcp"
USER_POOL_NAME = f"{INSTANCE_PREFIX}-buyer-agent-users"
APP_CLIENT_NAME = f"{INSTANCE_PREFIX}-buyer-agent-client"
TEST_USERNAME = "testuser"
TEST_USER_EMAIL = "testuser@example.com"

ENV_PATH = Path(__file__).parent / ".env"
ENV_EXAMPLE_PATH = Path(__file__).parent / ".env.example"

cognito = boto3.client("cognito-idp", region_name=REGION)


def _generate_password(length: int = 20) -> str:
    """Generate a password satisfying the pool's policy (upper+lower+digit)."""
    alphabet = string.ascii_letters + string.digits
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(c.isupper() for c in pw)
            and any(c.islower() for c in pw)
            and any(c.isdigit() for c in pw)
        ):
            return pw


def get_or_create_user_pool() -> str:
    paginator = cognito.get_paginator("list_user_pools")
    for page in paginator.paginate(MaxResults=60):
        for pool in page["UserPools"]:
            if pool["Name"] == USER_POOL_NAME:
                print(f"Using existing user pool: {pool['Id']}")
                return pool["Id"]

    print(f"Creating user pool: {USER_POOL_NAME}")
    response = cognito.create_user_pool(
        PoolName=USER_POOL_NAME,
        Policies={
            "PasswordPolicy": {
                "MinimumLength": 8,
                "RequireUppercase": True,
                "RequireLowercase": True,
                "RequireNumbers": True,
                "RequireSymbols": False,
            }
        },
        AutoVerifiedAttributes=["email"],
    )
    pool_id = response["UserPool"]["Id"]
    print(f"Created user pool: {pool_id}")
    return pool_id


def get_or_create_admin_group(pool_id: str) -> None:
    """Create the `admin` group used to gate user administration.

    Membership of this group is what user_admin.require_admin() checks (via
    the access token's cognito:groups claim) before allowing the UI's admin
    panel to create users, and it is also what the chat header keys off to
    show the "Agents" configuration link. The `testuser` service account is
    added to it by add_test_user_to_admin_group() below, so a freshly
    deployed instance can reach the configuration view without a manual
    grant. Add other people deliberately with
    `create_cognito_user.py <username> --admin`.
    """
    try:
        cognito.get_group(GroupName=ADMIN_GROUP_NAME, UserPoolId=pool_id)
        print(f"Using existing group: {ADMIN_GROUP_NAME}")
        return
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceNotFoundException":
            raise

    print(f"Creating group: {ADMIN_GROUP_NAME}")
    cognito.create_group(
        GroupName=ADMIN_GROUP_NAME,
        UserPoolId=pool_id,
        Description="Members may create Cognito users from the Buyer Agent chat UI.",
    )


def get_or_create_app_client(pool_id: str) -> str:
    paginator = cognito.get_paginator("list_user_pool_clients")
    for page in paginator.paginate(UserPoolId=pool_id, MaxResults=60):
        for client in page["UserPoolClients"]:
            if client["ClientName"] == APP_CLIENT_NAME:
                print(f"Using existing app client: {client['ClientId']}")
                return client["ClientId"]

    print(f"Creating app client: {APP_CLIENT_NAME}")
    response = cognito.create_user_pool_client(
        UserPoolId=pool_id,
        ClientName=APP_CLIENT_NAME,
        GenerateSecret=False,
        ExplicitAuthFlows=["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
    )
    client_id = response["UserPoolClient"]["ClientId"]
    print(f"Created app client: {client_id}")
    return client_id


def add_test_user_to_admin_group(pool_id: str) -> None:
    """Make `testuser` a member of the admin group (idempotent).

    The chat UI's "Agents" configuration link and the user-admin panel are
    gated on the access token's cognito:groups claim containing the admin
    group, and the runtime re-checks the same claim server-side on every
    admin action. Granting it here means the default deploy user can reach
    the configuration view out of the box. This runs unconditionally after
    get_or_create_test_user(), so an existing user created before this
    behaviour was added is brought up to date on the next deploy.

    Idempotent on two counts: the group is ensured first (created only if
    missing), and admin_add_user_to_group is a no-op when the user is
    already a member. Safe to re-run any number of times.

    The group membership only lands in a token minted after the grant, so a
    session opened before this ran must sign out and back in to see it.
    """
    # Ensure the group exists rather than assuming get_or_create_admin_group
    # ran first, so this function is correct regardless of call order.
    try:
        cognito.get_group(GroupName=ADMIN_GROUP_NAME, UserPoolId=pool_id)
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
        print(f"Creating group: {ADMIN_GROUP_NAME}")
        cognito.create_group(
            GroupName=ADMIN_GROUP_NAME,
            UserPoolId=pool_id,
            Description="Members may create Cognito users from the Buyer Agent chat UI.",
        )

    print(f"Adding '{TEST_USERNAME}' to group '{ADMIN_GROUP_NAME}'")
    cognito.admin_add_user_to_group(
        UserPoolId=pool_id, Username=TEST_USERNAME, GroupName=ADMIN_GROUP_NAME
    )


def get_or_create_test_user(pool_id: str) -> str:
    """Returns the test user's password (existing if found in .env, else fresh)."""
    try:
        cognito.admin_get_user(UserPoolId=pool_id, Username=TEST_USERNAME)
        user_exists = True
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "UserNotFoundException":
            raise
        user_exists = False

    existing_password = _read_env_value("COGNITO_TEST_USER_PASSWORD")

    if user_exists and existing_password:
        print(f"Using existing test user '{TEST_USERNAME}' with password from .env")
        return existing_password

    password = existing_password or _generate_password()

    if not user_exists:
        print(f"Creating test user: {TEST_USERNAME}")
        cognito.admin_create_user(
            UserPoolId=pool_id,
            Username=TEST_USERNAME,
            UserAttributes=[
                {"Name": "email", "Value": TEST_USER_EMAIL},
                {"Name": "email_verified", "Value": "true"},
            ],
            MessageAction="SUPPRESS",
        )

    print(f"Setting permanent password for '{TEST_USERNAME}'")
    cognito.admin_set_user_password(
        UserPoolId=pool_id,
        Username=TEST_USERNAME,
        Password=password,
        Permanent=True,
    )
    return password


def _read_env_value(key: str) -> str | None:
    if not ENV_PATH.exists():
        return None
    pattern = re.compile(rf"^{re.escape(key)}=(.*)$")
    for line in ENV_PATH.read_text().splitlines():
        m = pattern.match(line.strip())
        if m:
            return m.group(1)
    return None


def _upsert_env_values(values: dict[str, str]) -> None:
    """Write/replace key=value lines in .env, preserving everything else."""
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
    pool_id = get_or_create_user_pool()
    get_or_create_admin_group(pool_id)
    client_id = get_or_create_app_client(pool_id)
    password = get_or_create_test_user(pool_id)
    add_test_user_to_admin_group(pool_id)

    discovery_url = (
        f"https://cognito-idp.{REGION}.amazonaws.com/{pool_id}/.well-known/openid-configuration"
    )

    _upsert_env_values(
        {
            "COGNITO_USER_POOL_ID": pool_id,
            "COGNITO_CLIENT_ID": client_id,
            "COGNITO_DISCOVERY_URL": discovery_url,
            "COGNITO_REGION": REGION,
            "COGNITO_TEST_USERNAME": TEST_USERNAME,
            "COGNITO_TEST_USER_PASSWORD": password,
        }
    )

    print("\nWrote Cognito settings to .env:")
    print(f"  COGNITO_USER_POOL_ID={pool_id}")
    print(f"  COGNITO_CLIENT_ID={client_id}")
    print(f"  COGNITO_DISCOVERY_URL={discovery_url}")
    print(f"  COGNITO_TEST_USERNAME={TEST_USERNAME}")
    print("  COGNITO_TEST_USER_PASSWORD=<hidden, see .env>")
    print(
        f"\n'{TEST_USERNAME}' is a member of the '{ADMIN_GROUP_NAME}' group, so the chat UI's "
        "\"Agents\" configuration link and user-admin panel are available to it."
    )
    print(
        "  Groups ride in the access token, so an already-open session must sign out and "
        "back in to pick this up."
    )
    print(
        f"  Grant admin to other people deliberately:  python3 create_cognito_user.py <username> --admin"
    )


if __name__ == "__main__":
    main()
