"""
Cognito user administration for members of the `admin` group.

Why this lives server-side
--------------------------
Creating users needs Cognito's *admin* APIs (AdminCreateUser, ListUsers),
which require AWS credentials. The browser has none and must never have
any — so the UI calls the already-authenticated /invocations entrypoint
(app.py) with an `action`, and this module performs the operation using the
AgentCore Runtime execution role's credentials.

Authorization
-------------
`require_admin()` is the security boundary, and it runs here on the server,
on the *verified* token's claims (auth.py has already checked the RS256
signature, issuer, client_id and expiry against Cognito's JWKS). The UI
also hides its admin panel from non-admins, but that is cosmetic only: a
non-admin who calls the action directly is rejected here.

Group membership comes from the `cognito:groups` claim that Cognito itself
puts in the access token for users in a group — it is not something the
caller can assert. See deploy_cognito_setup.py, which creates the `admin`
group, and create_cognito_user.py --admin, which puts users in it.

Deliberately NOT exposed here
-----------------------------
- Granting admin (AdminAddUserToGroup): promoting users stays a CLI-only
  action so the runtime role holds no privilege-escalation permission.
- Deleting or disabling users, and setting passwords: also CLI-only. The
  runtime's IAM policy (deploy_user_admin_policy.py) grants create + read
  only, so even a compromised runtime cannot take an account over.
"""


from aws_region import region
import os
import re
import secrets
import string
from typing import Any

import boto3
from botocore.exceptions import ClientError

ADMIN_GROUP_NAME = "admin"

# Cognito's own username constraints are broader than this; this is a
# deliberately conservative allowlist for a value that gets used as an
# identifier throughout the pool.
_USERNAME_RE = re.compile(r"^[A-Za-z0-9._@+-]{1,128}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Matches the pool's PasswordPolicy in deploy_cognito_setup.py
# (MinimumLength 8, upper + lower + digits required, symbols optional).
# Longer than the minimum since it's machine-generated and transcribed
# once, not typed daily.
TEMP_PASSWORD_LENGTH = 16

# ListUsers page size cap, to keep one response bounded.
MAX_LIST_LIMIT = 60


class UserAdminError(Exception):
    """A user-administration request that failed for a reportable reason."""


class NotAuthorizedError(UserAdminError):
    """The caller is authenticated but not a member of the admin group."""


def generate_temporary_password() -> str:
    alphabet = string.ascii_letters + string.digits
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(TEMP_PASSWORD_LENGTH))
        if (
            any(c.isupper() for c in pw)
            and any(c.islower() for c in pw)
            and any(c.isdigit() for c in pw)
        ):
            return pw


def groups_from_claims(claims: dict[str, Any]) -> list[str]:
    """The verified token's Cognito group memberships.

    `cognito:groups` is normally a JSON array in the access token. It is
    absent entirely for a user in no groups.
    """
    groups = claims.get("cognito:groups")
    if groups is None:
        return []
    if isinstance(groups, str):  # defensive: some flows deliver it space/comma separated
        return [g for g in re.split(r"[,\s]+", groups) if g]
    if isinstance(groups, list):
        return [str(g) for g in groups]
    return []


def is_admin(claims: dict[str, Any]) -> bool:
    return ADMIN_GROUP_NAME in groups_from_claims(claims)


def require_admin(claims: dict[str, Any]) -> None:
    """Raise unless the verified token belongs to an `admin` group member."""
    if not is_admin(claims):
        raise NotAuthorizedError(
            f"This action requires membership of the '{ADMIN_GROUP_NAME}' Cognito group."
        )


def _pool_id() -> str:
    pool_id = os.environ.get("COGNITO_USER_POOL_ID", "").strip()
    if not pool_id:
        raise UserAdminError(
            "COGNITO_USER_POOL_ID is not set in this runtime's environment, so user "
            "administration is unavailable."
        )
    return pool_id


def _client():
    region = os.environ.get("COGNITO_REGION") or region()
    return boto3.client("cognito-idp", region_name=region)


def _attribute(user: dict[str, Any], name: str) -> str:
    for attr in user.get("Attributes") or user.get("UserAttributes") or []:
        if attr.get("Name") == name:
            return attr.get("Value", "")
    return ""


def validate_username(username: str) -> str:
    username = (username or "").strip()
    if not username:
        raise UserAdminError("Username is required.")
    if not _USERNAME_RE.match(username):
        raise UserAdminError(
            "Username may only contain letters, digits and . _ @ + - (max 128 characters)."
        )
    return username


def validate_email(email: str) -> str:
    email = (email or "").strip()
    if not email:
        raise UserAdminError("Email is required.")
    if not _EMAIL_RE.match(email):
        raise UserAdminError(f"{email!r} does not look like an email address.")
    return email


def list_users(limit: int = MAX_LIST_LIMIT) -> list[dict[str, Any]]:
    """Users in the pool, with their real Cognito status.

    `status` is Cognito's own UserStatus, reported as-is:
    FORCE_CHANGE_PASSWORD means the account still has a temporary password
    and has not completed first sign-in; CONFIRMED means it has. Nothing
    here is inferred or defaulted — an account whose status Cognito doesn't
    report shows as empty rather than as "CONFIRMED".
    """
    limit = max(1, min(int(limit or MAX_LIST_LIMIT), MAX_LIST_LIMIT))
    client = _client()
    pool_id = _pool_id()

    try:
        resp = client.list_users(UserPoolId=pool_id, Limit=limit)
    except ClientError as exc:
        raise UserAdminError(_client_error_message(exc, "list users")) from exc

    # Which of them are admins. Read-only, one extra call, and it avoids an
    # AdminListGroupsForUser call per user.
    admin_usernames: set[str] = set()
    admin_group_readable = True
    try:
        group_resp = client.list_users_in_group(
            UserPoolId=pool_id, GroupName=ADMIN_GROUP_NAME, Limit=MAX_LIST_LIMIT
        )
        admin_usernames = {u.get("Username", "") for u in group_resp.get("Users", [])}
    except ClientError as exc:
        # A missing group or a missing permission must not be reported as
        # "nobody is an admin" — that would answer a question we could not read. Mark it
        # unknown instead and let the caller say so.
        if exc.response["Error"]["Code"] not in ("ResourceNotFoundException", "AccessDeniedException"):
            raise UserAdminError(_client_error_message(exc, "list admin group members")) from exc
        admin_group_readable = False

    users = []
    for user in resp.get("Users", []):
        username = user.get("Username", "")
        users.append(
            {
                "username": username,
                "status": user.get("UserStatus", ""),
                "enabled": user.get("Enabled"),
                "email": _attribute(user, "email"),
                "created_at": user.get("UserCreateDate").isoformat()
                if user.get("UserCreateDate")
                else "",
                # None (not False) when group membership couldn't be read,
                # so the UI can show "unknown" rather than implying "no".
                "is_admin": (username in admin_usernames) if admin_group_readable else None,
            }
        )
    users.sort(key=lambda u: u["username"].lower())
    return users


def create_user(username: str, email: str) -> dict[str, Any]:
    """Create a user with a temporary password they must change at first sign-in.

    Returns the username, email and the generated temporary password. The
    temporary password is returned because there is no other channel to
    hand it to the new user; it stops working once they answer Cognito's
    NEW_PASSWORD_REQUIRED challenge (see static/index.html's
    cognitoSetNewPassword).
    """
    username = validate_username(username)
    email = validate_email(email)
    client = _client()
    pool_id = _pool_id()
    temporary_password = generate_temporary_password()

    try:
        client.admin_create_user(
            UserPoolId=pool_id,
            Username=username,
            TemporaryPassword=temporary_password,
            UserAttributes=[
                {"Name": "email", "Value": email},
                {"Name": "email_verified", "Value": "true"},
            ],
            # SUPPRESS: the pool has no email configuration wired up, so an
            # invitation email would silently never arrive. The caller shows
            # the temporary password instead.
            MessageAction="SUPPRESS",
        )
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code == "UsernameExistsException":
            raise UserAdminError(f"A user named {username!r} already exists.") from exc
        if code == "InvalidPasswordException":
            raise UserAdminError(
                "Cognito rejected the generated temporary password: "
                f"{exc.response['Error'].get('Message', '')}"
            ) from exc
        raise UserAdminError(_client_error_message(exc, "create the user")) from exc

    # Report the status Cognito actually assigned rather than assuming
    # FORCE_CHANGE_PASSWORD — if it isn't that, the new user will not be
    # prompted to change their password and the caller needs to know.
    try:
        status = client.admin_get_user(UserPoolId=pool_id, Username=username).get("UserStatus", "")
    except ClientError:
        status = ""

    return {
        "username": username,
        "email": email,
        "temporary_password": temporary_password,
        "status": status,
        "must_change_password": status == "FORCE_CHANGE_PASSWORD",
    }


def _client_error_message(exc: ClientError, action: str) -> str:
    code = exc.response["Error"]["Code"]
    message = exc.response["Error"].get("Message", "")
    if code == "AccessDeniedException":
        return (
            f"The runtime's execution role is not allowed to {action}. Run "
            "deploy_user_admin_policy.py to grant the Cognito permissions this needs."
        )
    return f"Cognito could not {action}: {code}: {message}"
