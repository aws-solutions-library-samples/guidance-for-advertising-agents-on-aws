"""
Grants the AgentCore Runtime execution role the (read + create only)
Cognito permissions that the chat UI's admin panel needs.

    source .venv/bin/activate
    python3 deploy_user_admin_policy.py            # show the diff, change nothing
    python3 deploy_user_admin_policy.py --apply    # attach/update the inline policy

Without this, `action=create_user` / `action=list_users` fail at runtime with
AccessDeniedException — which user_admin.py reports rather than
pretending the user was created.

Least privilege, deliberately
-----------------------------
Every statement is scoped to the single user pool in COGNITO_USER_POOL_ID —
no wildcard resource. The action list is the minimum for "an admin-group
user can add a user and see who exists":

  cognito-idp:AdminCreateUser     create the user with a temporary password
  cognito-idp:AdminGetUser        read back the status Cognito assigned
  cognito-idp:ListUsers           the admin panel's user list
  cognito-idp:ListUsersInGroup    which of those users are admins

Explicitly NOT granted, so the runtime cannot take over or escalate:

  AdminAddUserToGroup / AdminRemoveUserFromGroup  (no minting new admins)
  AdminSetUserPassword                            (no taking over accounts)
  AdminDeleteUser / AdminDisableUser              (no destroying accounts)
  AdminUpdateUserAttributes                       (no changing emails)

Those remain CLI-only actions run with your own AWS credentials
(create_cognito_user.py --admin / --reset).

This is an IAM change to a role a live runtime uses, so it does nothing
unless you pass --apply, and it prints the exact policy document first.
"""


from aws_region import region
import argparse
import json
import os
import sys

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

load_dotenv()

#: Instance prefix (see deploy_all.resolve_prefix). Per-instance IAM policy name. Defaults to 'adcp'.
INSTANCE_PREFIX = os.environ.get("INSTANCE_PREFIX", "adcp").strip() or "adcp"
POLICY_NAME = f"{INSTANCE_PREFIX}-buyer-agent-cognito-user-admin"


def build_policy_document(pool_arn: str) -> dict:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "CreateAndReadPoolUsers",
                "Effect": "Allow",
                "Action": [
                    "cognito-idp:AdminCreateUser",
                    "cognito-idp:AdminGetUser",
                    "cognito-idp:ListUsers",
                    "cognito-idp:ListUsersInGroup",
                ],
                "Resource": pool_arn,
            }
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Grant the AgentCore execution role create+read Cognito permissions, scoped "
            "to this project's user pool."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually put the inline policy on the role. Without this, only prints it.",
    )
    args = parser.parse_args()

    role_arn = os.environ.get("EXECUTION_ROLE_ARN", "").strip()
    pool_id = os.environ.get("COGNITO_USER_POOL_ID", "").strip()
    region = os.environ.get("COGNITO_REGION") or region()
    if not role_arn:
        print("!!! EXECUTION_ROLE_ARN is not set in .env.")
        sys.exit(1)
    if not pool_id:
        print("!!! COGNITO_USER_POOL_ID is not set in .env. Run deploy_cognito_setup.py first.")
        sys.exit(1)

    role_name = role_arn.split("/")[-1]
    account_id = role_arn.split(":")[4]
    pool_arn = f"arn:aws:cognito-idp:{region}:{account_id}:userpool/{pool_id}"
    document = build_policy_document(pool_arn)

    iam = boto3.client("iam")

    print(f"Role:        {role_name}")
    print(f"Policy name: {POLICY_NAME} (inline)")
    print(f"Scoped to:   {pool_arn}")

    try:
        existing = iam.get_role_policy(RoleName=role_name, PolicyName=POLICY_NAME)
        current = existing["PolicyDocument"]
        if current == document:
            print("\nPolicy already present and identical. Nothing to do.")
            return
        print("\nAn inline policy with this name already exists and differs. Current:")
        print(json.dumps(current, indent=2))
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "NoSuchEntity":
            raise
        print("\nNo such inline policy yet; it will be created.")

    print("\nPolicy to put:")
    print(json.dumps(document, indent=2))

    if not args.apply:
        print("\nDry run — nothing was changed. Re-run with --apply to put this policy.")
        return

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=POLICY_NAME,
        PolicyDocument=json.dumps(document),
    )
    print(f"\nPut inline policy '{POLICY_NAME}' on role '{role_name}'.")
    print(
        "IAM changes can take a few seconds to propagate; a create_user call made "
        "immediately may still see AccessDenied."
    )


if __name__ == "__main__":
    main()
