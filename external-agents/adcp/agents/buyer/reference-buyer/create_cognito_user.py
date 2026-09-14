"""
Creates an additional user in the Buyer Agent's Cognito user pool with a
*temporary* password, so the user is forced to choose their own password
the first time they sign in through the chat UI.

    source .venv/bin/activate
    python3 create_cognito_user.py alice --email alice@example.com

This is deliberately an admin-side CLI script, not a UI feature: creating
users requires the Cognito *admin* APIs (AdminCreateUser), which need AWS
credentials. Exposing those to the browser would mean shipping admin
credentials to every visitor, so user creation stays here, with AWS
credentials that never leave your machine.

What the created user's first sign-in looks like:

  1. AdminCreateUser (below) leaves the account in
     FORCE_CHANGE_PASSWORD status — a temporary password only.
  2. The UI's login screen calls InitiateAuth with it. Cognito returns the
     NEW_PASSWORD_REQUIRED challenge and a short-lived Session, NOT tokens.
  3. The UI shows its "choose a new password" step and answers the
     challenge with RespondToAuthChallenge. Cognito makes that password
     permanent and issues the token set (see static/index.html's
     cognitoSetNewPassword).

The temporary password is printed once, because there is no other way to
hand it to the new user — it is single-use by design (unusable after step 3)
and expires on its own (7 days by default, per the pool's
TemporaryPasswordValidityDays). It is not written to .env: only the
deploy/test user's credentials live there, and adding real people's
passwords to a file read by deploy scripts would be worse than printing
one temporary string.

Distinct from deploy_cognito_setup.py, which creates/reuses the pool, the
app client, and the single `testuser` service account used by the
verification scripts (that one gets a *permanent* password, since automated
tests can't answer an interactive challenge). Run that first; this script
only adds people to the pool it already made.
"""


from aws_region import region
import argparse
import re
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

# Shared with the server-side admin path (app.py's create_user action) so
# the temporary-password rules and the admin group name have exactly one
# definition, not two that can drift.
from user_admin import ADMIN_GROUP_NAME, generate_temporary_password

ENV_PATH = Path(__file__).parent / ".env"
load_dotenv(ENV_PATH)


def grant_admin(cognito, pool_id: str, username: str) -> None:
    """Add the user to the admin group, creating the group if it's missing.

    Granting admin is deliberately CLI-only: the runtime's execution role is
    not given AdminAddUserToGroup (see deploy_user_admin_policy.py), so an
    admin using the UI cannot mint further admins, and a compromised runtime
    cannot escalate its own privileges.
    """
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
    print(f"Adding '{username}' to group '{ADMIN_GROUP_NAME}'")
    cognito.admin_add_user_to_group(
        UserPoolId=pool_id, Username=username, GroupName=ADMIN_GROUP_NAME
    )


def read_env_value(key: str) -> str | None:
    if not ENV_PATH.exists():
        return None
    pattern = re.compile(rf"^{re.escape(key)}=(.*)$")
    for line in ENV_PATH.read_text().splitlines():
        m = pattern.match(line.strip())
        if m:
            return m.group(1)
    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Create a Cognito user with a temporary password. The user sets their own "
            "password at first sign-in via the chat UI."
        )
    )
    parser.add_argument("username", help="Username for the new user, e.g. 'alice'.")
    parser.add_argument(
        "--email",
        default=None,
        help=(
            "Email address for the user. Defaults to <username>@example.com. Marked "
            "email_verified so the pool's AutoVerifiedAttributes don't block sign-in."
        ),
    )
    parser.add_argument(
        "--temporary-password",
        default=None,
        help=(
            "Use this temporary password instead of generating one. Must satisfy the "
            "pool policy (8+ chars, upper, lower, digit)."
        ),
    )
    parser.add_argument(
        "--send-email",
        action="store_true",
        help=(
            "Let Cognito email the invitation (default suppresses it and prints the "
            "temporary password here instead). Requires the pool to have a working "
            "email configuration."
        ),
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help=(
            "If the user already exists, issue a new temporary password for them "
            "(AdminSetUserPassword with Permanent=False), putting them back into "
            "FORCE_CHANGE_PASSWORD so they pick a new password at next sign-in."
        ),
    )
    parser.add_argument(
        "--admin",
        action="store_true",
        help=(
            f"Add the user to the '{ADMIN_GROUP_NAME}' Cognito group, which lets them "
            "create users from the chat UI's admin panel. Works on an existing user too "
            "(grants admin without touching their password)."
        ),
    )
    args = parser.parse_args()

    pool_id = read_env_value("COGNITO_USER_POOL_ID")
    region = read_env_value("COGNITO_REGION") or region()
    if not pool_id:
        print(
            "!!! COGNITO_USER_POOL_ID is not set in .env. Run deploy_cognito_setup.py first."
        )
        sys.exit(1)

    username = args.username
    email = args.email or f"{username}@example.com"
    temporary_password = args.temporary_password or generate_temporary_password()

    cognito = boto3.client("cognito-idp", region_name=region)

    try:
        existing = cognito.admin_get_user(UserPoolId=pool_id, Username=username)
        user_exists = True
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "UserNotFoundException":
            raise
        existing = None
        user_exists = False

    if user_exists and not args.reset and not args.admin:
        print(
            f"User '{username}' already exists in pool {pool_id} "
            f"(status: {existing.get('UserStatus')})."
        )
        print(
            "Nothing changed. Re-run with --reset to issue a new temporary password, "
            f"or --admin to add them to the '{ADMIN_GROUP_NAME}' group."
        )
        sys.exit(1)

    # --admin on an existing user is a pure group grant: don't touch their
    # password (that would lock out someone who is already signing in fine).
    if user_exists and args.admin and not args.reset:
        grant_admin(cognito, pool_id, username)
        print(f"\n'{username}' is now a member of the '{ADMIN_GROUP_NAME}' group.")
        print(
            "Cognito groups are carried in the access token, so they must sign out and "
            "back in before the UI's admin panel appears."
        )
        return

    if user_exists:
        print(f"Resetting '{username}' to a new temporary password (pool {pool_id})")
        cognito.admin_set_user_password(
            UserPoolId=pool_id,
            Username=username,
            Password=temporary_password,
            Permanent=False,
        )
    else:
        print(f"Creating user '{username}' in pool {pool_id}")
        create_kwargs = {
            "UserPoolId": pool_id,
            "Username": username,
            "TemporaryPassword": temporary_password,
            "UserAttributes": [
                {"Name": "email", "Value": email},
                {"Name": "email_verified", "Value": "true"},
            ],
        }
        # AdminCreateUser emails the invitation by default; MessageAction
        # only has SUPPRESS (don't send) and RESEND (re-send to an existing
        # user) — so "send it" means omitting the parameter, not passing
        # RESEND, which would be rejected for a user being created.
        if not args.send_email:
            create_kwargs["MessageAction"] = "SUPPRESS"
        cognito.admin_create_user(**create_kwargs)

    if args.admin:
        grant_admin(cognito, pool_id, username)

    status = cognito.admin_get_user(UserPoolId=pool_id, Username=username).get("UserStatus")
    print(f"\nUser status: {status}")
    if args.admin:
        print(f"Group:       {ADMIN_GROUP_NAME} (may create users from the chat UI)")
    if status != "FORCE_CHANGE_PASSWORD":
        print(
            "!!! Expected FORCE_CHANGE_PASSWORD. This user will NOT be prompted to change "
            "their password at sign-in — check whether a permanent password was set for them."
        )

    print("\n=== Hand these to the user (temporary, single-use) ===")
    print(f"  username:           {username}")
    if args.send_email:
        print("  temporary password: sent by Cognito email (not printed here)")
    else:
        print(f"  temporary password: {temporary_password}")
    print(
        "\nAt first sign-in the UI will require them to set their own password before "
        "any token is issued. This temporary password stops working at that point."
    )


if __name__ == "__main__":
    main()
