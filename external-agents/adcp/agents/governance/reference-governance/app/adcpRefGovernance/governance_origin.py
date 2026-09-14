"""Where this agent's `.well-known/` documents live, and the `iss` they are published under.

Three scripts publish to this origin -- `deploy_governance_origin.py` creates it, `deploy_jwks.py`
puts the public JWKS on it, `deploy_revocations.py` puts the revocation list on it -- and the
governance runtime's `GOVERNANCE_AGENT_URL` names it. All four read from here.

## What this replaces

Each of those files carried its own `ACCOUNT_ID`, `BUCKET_NAME` and
`ISSUER = "https://<something>.cloudfront.net"`, and `deploy_governance_origin.py` ended by printing
instructions to go and edit the other three by hand. Four copies of one value, kept in agreement by
copy-paste, in a place where disagreement is not loudly wrong: a JWKS published to one origin while
tokens claim another produces tokens no counterparty can verify, and the publish step still reports
success.

## The origin is discovered, not recorded

`origin_url()` finds the distribution by its `Comment`, which is how `deploy_governance_origin.py`
already identifies its own distribution when deciding whether to create one. So there is no value to
propagate between steps and no second place for it to go stale -- the answer to "what is my issuer"
is whatever is actually deployed.

`GOVERNANCE_ORIGIN_URL` overrides, for two cases: the republisher Lambda, which is handed the value by
CDK rather than paying a CloudFront list call on every cold start, and pinning a custom domain in
front of the distribution later.
"""

from __future__ import annotations

import functools
import json
import os
from pathlib import Path

import boto3

#: Instance prefix (see deploy_all.resolve_prefix): the auto-generated unique id every resource name
#: derives from. It replaces the old `{account}-{region}` bucket suffix -- S3 names are globally
#: unique and the prefix now supplies that uniqueness, so account/region no longer belong in the
#: name (they are tracked in the local deployment manifest instead). Defaults to 'adcp' for a
#: standalone run; deploy_all.py exports INSTANCE_PREFIX so orchestrated runs inherit the right one.
INSTANCE_PREFIX = os.environ.get("INSTANCE_PREFIX", "adcp").strip() or "adcp"

#: How the distribution is identified. `deploy_governance_origin.py` sets this on create and matches
#: on it to stay idempotent, so it is the distribution's de facto name. The prefix MUST be in the
#: Comment, or a second instance would adopt the first instance's distribution.
DISTRIBUTION_COMMENT = f"AdCP reference governance agent well-known documents ({INSTANCE_PREFIX})"

#: CloudFront's API is global but only answers in us-east-1, independent of where anything is deployed.
CLOUDFRONT_API_REGION = "us-east-1"


class OriginNotDeployed(RuntimeError):
    """The distribution does not exist yet."""


@functools.lru_cache(maxsize=1)
def account_id() -> str:
    override = os.environ.get("AWS_ACCOUNT_ID", "").strip()
    if override:
        return override
    return boto3.client("sts").get_caller_identity()["Account"]


@functools.lru_cache(maxsize=1)
def region() -> str:
    for key in ("AWS_REGION", "AWS_DEFAULT_REGION"):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    resolved = boto3.session.Session().region_name
    if not resolved:
        raise RuntimeError(
            "No region configured. Set AWS_REGION or configure one on the active profile."
        )
    return resolved


@functools.lru_cache(maxsize=1)
def bucket_name() -> str:
    return f"{INSTANCE_PREFIX}-reference-governance"


def find_distribution() -> dict | None:
    """The distribution carrying DISTRIBUTION_COMMENT, or None."""
    cloudfront = boto3.client("cloudfront", region_name=CLOUDFRONT_API_REGION)
    paginator = cloudfront.get_paginator("list_distributions")
    for page in paginator.paginate():
        for item in page.get("DistributionList", {}).get("Items", []):
            if item.get("Comment") == DISTRIBUTION_COMMENT:
                return item
    return None


@functools.lru_cache(maxsize=1)
def origin_url() -> str:
    """The bare origin, no trailing slash, e.g. `https://d111111abcdef8.cloudfront.net`.

    Bare on purpose: AdCP resolves `{origin of iss}/.well-known/...`, so a path here would be stripped
    back to the origin and the documents would be looked for somewhere else entirely.
    """
    override = os.environ.get("GOVERNANCE_ORIGIN_URL", "").strip()
    if override:
        return override.rstrip("/")

    distribution = find_distribution()
    if distribution is None:
        raise OriginNotDeployed(
            f"No CloudFront distribution found with comment {DISTRIBUTION_COMMENT!r} in account "
            f"{account_id()}.\n"
            "  Create it first:  uv run python deploy_governance_origin.py --apply\n"
            "  (deploy_all.py runs this in phase 1, before anything publishes to it.)"
        )
    return f"https://{distribution['DomainName']}"


def published_url(s3_key: str) -> str:
    return f"{origin_url()}/{s3_key}"


#: This agent's `agentcore.json`, two levels up from the app package. Absent inside the republisher
#: Lambda's image, which is why every read of it is guarded.
AGENTCORE_CONFIG_PATH = Path(__file__).resolve().parents[2] / "agentcore" / "agentcore.json"
#: The runtime name render_agentcore_json.py assigns is the bare base `RefGovernance` (only the
#: project/stack name is prefixed). Must match agentcore.json's runtime `name`, or the signing env
#: vars are looked up under a runtime name that is not there.
RUNTIME_NAME = "RefGovernance"

#: What the runtime signs with. The CLI publishers need the same three values, because a JWKS published
#: from one key while the runtime signs with another produces tokens no counterparty can verify.
SIGNING_ENV_VARS = (
    "GOVERNANCE_SIGNING_KMS_KEY_ID",
    "GOVERNANCE_SIGNING_ALG",
    "GOVERNANCE_SIGNING_KID",
)


def load_runtime_signing_env() -> list[str]:
    """Fill the signing variables from `agentcore.json` where they are not already set.

    Returns the names it set, for reporting.

    AgentCore injects these into the runtime and CDK injects them into the republisher Lambda, so both
    of those arrive with them present. The CLI publishers (`deploy_jwks.py`, `deploy_revocations.py`)
    run on a developer machine with no injection at all, and previously required the operator to export
    them by hand -- an undocumented prerequisite, and the reason a fresh-account `deploy_all.py` stopped
    at the JWKS step with "No KMS key id available for the public JWK."

    Reading them from `agentcore.json` rather than from a separate `.env` is deliberate: that file is
    what the runtime is actually configured with, so the CLI cannot publish a JWKS for a different key
    than the one the runtime will sign with.

    Values already in the environment win, so an explicit override still works and the Lambda is
    unaffected.
    """
    if not AGENTCORE_CONFIG_PATH.exists():
        return []

    config = json.loads(AGENTCORE_CONFIG_PATH.read_text())
    declared: dict[str, str] = {}
    for runtime in config.get("runtimes", []):
        if runtime.get("name") != RUNTIME_NAME:
            continue
        for entry in runtime.get("envVars", []):
            name = entry.get("name")
            if name in SIGNING_ENV_VARS:
                declared[name] = str(entry.get("value", ""))

    applied = []
    for name in SIGNING_ENV_VARS:
        if os.environ.get(name, "").strip():
            continue
        value = declared.get(name, "").strip()
        if value:
            os.environ[name] = value
            applied.append(name)
    return applied
