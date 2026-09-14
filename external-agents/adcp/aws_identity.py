"""The AWS account and region of the credentials actually in use.

    python3 aws_identity.py            # print what the current credentials resolve to

Every account-scoped value in this repo is derived from here rather than written down. The account
number is a property of the credentials a deploy runs under, so reading it from those credentials is
the only form that cannot disagree with where the deploy is going.

## The failure this replaces

`agentcore/aws-targets.json` pins the target account as a literal, and the CLI does not check it
against the caller's credentials. A dry run under one account's credentials synthesised a
CloudFormation template naming a *different* account, and reported success: the account appeared in
the CDK stack env, the asset bucket, and every generated IAM policy. A committed account number is
therefore not documentation, it is a deploy targeting whatever was last committed.

## Stdlib only, via the AWS CLI

`deploy_all.py` imports this and is itself dependency-free by design, delegating all AWS work to each
project's own venv. So this shells out to the `aws` CLI rather than importing boto3, which keeps that
property intact. The CLI resolves profiles and environment variables the same way `agentcore deploy`
does, so both see one identity.

Resolution is lazy and cached: `deploy_all.py` is imported by a test
(`gotham_mock/tests/test_vendored_copies.py`), and `deploy_revocations.py` is imported by a Lambda
handler, so nothing here may reach the network at import time.
"""

from __future__ import annotations

import functools
import json
import os
import shutil
import subprocess
import sys

#: The AWS docs' reserved example account. Test fixtures and documentation use this so that a real
#: account number never becomes the thing a reader copies.
EXAMPLE_ACCOUNT_ID = "123456789012"


class IdentityError(RuntimeError):
    """Raised when the current credentials cannot be resolved to an account or region."""


def _run(args: list[str]) -> str:
    if shutil.which("aws") is None:
        raise IdentityError(
            "The `aws` CLI is not on PATH. It is how this repo determines which account a deploy "
            "is targeting. Install it, or set AWS_ACCOUNT_ID and AWS_REGION explicitly."
        )
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise IdentityError(
            f"`{' '.join(args)}` failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout.strip()


@functools.lru_cache(maxsize=1)
def account_id() -> str:
    """The account the current credentials belong to.

    `AWS_ACCOUNT_ID` overrides, for the one case that needs it: rendering templates for an account
    you are not currently authenticated against. Anything that then deploys will still use the real
    credentials, so an override that disagrees is visible as a mismatch rather than silently applied.
    """
    override = os.environ.get("AWS_ACCOUNT_ID", "").strip()
    if override:
        return override
    identity = json.loads(_run(["aws", "sts", "get-caller-identity", "--output", "json"]))
    account = str(identity.get("Account", "")).strip()
    if len(account) != 12 or not account.isdigit():
        raise IdentityError(f"sts:GetCallerIdentity returned an implausible account: {account!r}")
    return account


@functools.lru_cache(maxsize=1)
def region() -> str:
    """The region the current credentials/profile resolve to.

    Same precedence the AWS SDKs use: `AWS_REGION`, then `AWS_DEFAULT_REGION`, then the profile's
    configured region. There is no fallback default -- a region guessed here would put resources
    somewhere the caller did not choose, and every agent in this repo is single-region by design.
    """
    for key in ("AWS_REGION", "AWS_DEFAULT_REGION"):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    configured = _run(["aws", "configure", "get", "region"])
    if not configured:
        raise IdentityError(
            "No region configured. Set AWS_REGION, or set one on the active profile "
            "(`aws configure set region <region>`)."
        )
    return configured


@functools.lru_cache(maxsize=1)
def caller_arn() -> str:
    """The full caller ARN, for reporting which principal a deploy ran as."""
    identity = json.loads(_run(["aws", "sts", "get-caller-identity", "--output", "json"]))
    return str(identity.get("Arn", ""))


def describe() -> str:
    return f"account {account_id()}  region {region()}  as {caller_arn()}"


def main() -> int:
    try:
        print(describe())
    except IdentityError as exc:
        print(f"!!! {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
