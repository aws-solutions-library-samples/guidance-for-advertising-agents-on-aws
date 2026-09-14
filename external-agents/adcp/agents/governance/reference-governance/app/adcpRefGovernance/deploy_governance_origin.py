"""Provision this agent's OWN bare-origin CloudFront distribution for its published
well-known documents (JWKS, revocation list).

    uv run python deploy_governance_origin.py            # dry run: prints the plan, creates nothing
    uv run python deploy_governance_origin.py --apply

## Why this exists (real bug, found live during U3 verification)

Before this script, the governance agent's public documents were published under a PATH prefix
(`/governance/...`) on the BUYER's shared CloudFront distribution, and `iss` was set to
`https://d111111abcdef8.cloudfront.net/governance` -- an origin WITH a path component.

`adcp.signing.revocation_fetcher.AsyncCachingRevocationChecker.from_issuer_origin` (and the AdCP spec
it implements) derive the revocation list's location as `{origin of iss}/.well-known/...` -- and
"origin" is RFC 6454's strict scheme+host+port, with NO path. The SDK's own `_normalize_issuer`
strips any path before appending `.well-known/governance-revocations.json`. So every real seller
resolving this agent's revocation list computed:

    https://d111111abcdef8.cloudfront.net/.well-known/governance-revocations.json

-- the BUYER's own bare root on the SAME shared distribution, which (via that distribution's SPA
403-to-index.html rewrite) served the buyer UI's `index.html`. A seller's `AsyncCachingRevocationChecker`
then tried to JWS-verify raw HTML and failed with "compact JWS must have exactly 3 dot-separated
segments, got 6" -- confirmed live, not by inspection.

This is not fixable by changing a path string: an `iss` WITH a path is fundamentally incompatible
with `{origin}/.well-known/...`-style resolution, which is a real, spec-mandated pattern (also used
for JWKS's OWN fallback rule -- see deploy_jwks.py's docstring -- and, more importantly, is what
lets ANY conformant seller discover this agent's documents without bespoke per-agent configuration).

## The fix

Give the governance agent its OWN origin -- a dedicated S3 bucket + CloudFront distribution, so `iss`
is a bare origin (`https://{domain}`, no path) and `{origin}/.well-known/...` resolves correctly by
construction, matching every other AdCP agent's identity shape in this project (each seller's
`DEFAULT_AGENT_URL` is similarly a bare `https://{name}.example/adcp/mcp` -- wait, those DO have a
path (`/adcp/mcp`) since they're MCP endpoints, not `.well-known`-serving origins; the distinction
that matters here is specifically the WELL-KNOWN discovery path, not the agent's general identity
convention).

## Deliberately NOT the buyer's SPA rewrite behavior

The buyer's distribution (`deploy_ui.py`) maps 403/404 to `index.html` so client-side routing survives
a refresh. This distribution serves ONLY static, individually-addressed `.well-known/` documents --
a missing object SHOULD 404 for real, which is the honest signal a seller's fetcher needs to
distinguish "not published yet" from "here is a document" (a index.html fallback here would recreate
exactly the failure this script exists to fix, just at a new origin).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import boto3

import governance_origin
from governance_origin import DISTRIBUTION_COMMENT, INSTANCE_PREFIX, bucket_name, region

# Per-instance so a second instance's OAC does not collide (OAC names are unique per account).
OAC_NAME = f"{INSTANCE_PREFIX}-reference-governance-oac"

REGION = region()
BUCKET_NAME = bucket_name()

s3 = boto3.client("s3", region_name=REGION)
cloudfront = boto3.client(
    "cloudfront", region_name=governance_origin.CLOUDFRONT_API_REGION
)


def get_or_create_bucket() -> str:
    try:
        s3.head_bucket(Bucket=BUCKET_NAME)
        print(f"Using existing bucket: {BUCKET_NAME}")
    except Exception:
        print(f"Creating bucket: {BUCKET_NAME}")
        s3.create_bucket(Bucket=BUCKET_NAME)
        s3.put_public_access_block(
            Bucket=BUCKET_NAME,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            },
        )
        s3.put_bucket_encryption(
            Bucket=BUCKET_NAME,
            ServerSideEncryptionConfiguration={
                "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
            },
        )
    return BUCKET_NAME


def get_or_create_oac() -> str:
    paginator = cloudfront.get_paginator("list_origin_access_controls")
    for page in paginator.paginate():
        for item in page["OriginAccessControlList"]["Items"]:
            if item["Name"] == OAC_NAME:
                print(f"Using existing OAC: {item['Id']}")
                return item["Id"]

    print(f"Creating OAC: {OAC_NAME}")
    response = cloudfront.create_origin_access_control(
        OriginAccessControlConfig={
            "Name": OAC_NAME,
            "Description": "OAC for the reference governance agent's well-known documents bucket",
            "SigningProtocol": "sigv4",
            "SigningBehavior": "always",
            "OriginAccessControlOriginType": "s3",
        }
    )
    oac_id = response["OriginAccessControl"]["Id"]
    print(f"Created OAC: {oac_id}")
    return oac_id


def get_or_create_distribution(bucket_name: str, oac_id: str) -> tuple[str, str]:
    """Returns (distribution_id, domain_name). No SPA error-response rewrite -- see module docstring:
    a missing object here MUST 404 for real."""
    # Same lookup every consumer of this origin uses to find it, rather than a second implementation
    # of "which distribution is ours" that could disagree about the answer.
    existing = governance_origin.find_distribution()
    if existing:
        print(f"Using existing distribution: {existing['Id']} ({existing['DomainName']})")
        return existing["Id"], existing["DomainName"]

    origin_domain = f"{bucket_name}.s3.{REGION}.amazonaws.com"
    print(f"Creating CloudFront distribution for origin {origin_domain}")
    response = cloudfront.create_distribution(
        DistributionConfig={
            "CallerReference": f"adcp-reference-governance-{int(time.time())}",
            "Comment": DISTRIBUTION_COMMENT,
            "Enabled": True,
            "Origins": {
                "Quantity": 1,
                "Items": [
                    {
                        "Id": "governance-bucket-origin",
                        "DomainName": origin_domain,
                        "OriginAccessControlId": oac_id,
                        "S3OriginConfig": {"OriginAccessIdentity": ""},
                    }
                ],
            },
            "DefaultCacheBehavior": {
                "TargetOriginId": "governance-bucket-origin",
                "ViewerProtocolPolicy": "redirect-to-https",
                "AllowedMethods": {
                    "Quantity": 2,
                    "Items": ["GET", "HEAD"],
                    "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]},
                },
                "CachePolicyId": "658327ea-f89d-4fab-a63d-7e88639e58f6",  # Managed-CachingOptimized
                "Compress": True,
            },
            "PriceClass": "PriceClass_100",
        }
    )
    dist = response["Distribution"]
    print(f"Created distribution: {dist['Id']} ({dist['DomainName']}) -- status Deploying")
    return dist["Id"], dist["DomainName"]


def put_bucket_policy(bucket_name: str, distribution_id: str) -> None:
    import json

    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowCloudFrontServicePrincipalReadOnly",
                "Effect": "Allow",
                "Principal": {"Service": "cloudfront.amazonaws.com"},
                "Action": "s3:GetObject",
                "Resource": f"arn:aws:s3:::{bucket_name}/*",
                "Condition": {
                    "StringEquals": {
                        "AWS:SourceAccount": governance_origin.account_id(),
                        "AWS:SourceArn": (
                            f"arn:aws:cloudfront::{governance_origin.account_id()}"
                            f":distribution/{distribution_id}"
                        ),
                    }
                },
            }
        ],
    }
    s3.put_bucket_policy(Bucket=bucket_name, Policy=json.dumps(policy))
    print(f"Bucket policy set: cloudfront.amazonaws.com may GetObject, scoped to distribution {distribution_id}")


#: This agent's `agentcore.json`, two levels up from the app package.
AGENTCORE_CONFIG_PATH = (
    Path(__file__).resolve().parents[2] / "agentcore" / "agentcore.json"
)
#: The runtime name render_agentcore_json.py assigns is the bare base `RefGovernance` (only the
#: project/stack name is prefixed). Must match agentcore.json's runtime `name`, or the origin cannot
#: be written into the runtime's envVars.
RUNTIME_NAME = "RefGovernance"


def record_origin_in_agentcore_config(distribution_id: str, domain_name: str) -> None:
    """Write the origin into the runtime's envVars.

    `GOVERNANCE_AGENT_URL` is the runtime's `iss`; `GOVERNANCE_ORIGIN_DISTRIBUTION_ID` is what the CDK
    scopes the republisher's invalidation grant to. Both are properties of the distribution that was
    just created, so they are written here rather than left for someone to copy across by hand -- which
    is what this script used to print instructions to do, across four files.
    """
    import json

    config = json.loads(AGENTCORE_CONFIG_PATH.read_text())
    runtimes = [r for r in config.get("runtimes", []) if r.get("name") == RUNTIME_NAME]
    if not runtimes:
        print(f"!!! no runtime named {RUNTIME_NAME!r} in {AGENTCORE_CONFIG_PATH}")
        sys.exit(1)

    values = {
        "GOVERNANCE_AGENT_URL": f"https://{domain_name}",
        "GOVERNANCE_ORIGIN_DISTRIBUTION_ID": distribution_id,
    }
    for runtime in runtimes:
        env_vars = runtime.setdefault("envVars", [])
        for name, value in values.items():
            for entry in env_vars:
                if entry.get("name") == name:
                    entry["value"] = value
                    break
            else:
                env_vars.append({"name": name, "value": value})

    AGENTCORE_CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n")
    for name, value in values.items():
        print(f"  wrote {AGENTCORE_CONFIG_PATH.name}: {name}={value}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    print(f"Bucket:       {BUCKET_NAME}")
    print(f"OAC name:     {OAC_NAME}")
    print(f"Distribution: {DISTRIBUTION_COMMENT!r}")

    if not args.apply:
        print("\nDry run. Nothing created.")
        return 0

    bucket = get_or_create_bucket()
    oac_id = get_or_create_oac()
    distribution_id, domain_name = get_or_create_distribution(bucket, oac_id)
    put_bucket_policy(bucket, distribution_id)
    record_origin_in_agentcore_config(distribution_id, domain_name)

    print("\n=== Done ===")
    print(f"Bare origin for this agent's iss claim: https://{domain_name}")
    print(
        "\nNote: CloudFront distributions take several minutes to reach Deployed status. "
        "Documents uploaded to the bucket won't be served until then."
    )
    # Nothing to copy anywhere. deploy_jwks.py, deploy_revocations.py, the runtime's
    # GOVERNANCE_AGENT_URL and the buyer's brand.json all resolve this origin through
    # governance_origin.origin_url(), which finds it by the distribution's Comment.
    print(
        "\nEvery publisher and the runtime's GOVERNANCE_AGENT_URL resolve this origin by looking it "
        "up, so there is nothing to propagate by hand."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
