"""Publish this buyer's brand.json so sellers can resolve the governance agent's JWKS.

    uv run python deploy_brand_json.py            # dry run: builds and prints brand.json, uploads nothing
    uv run python deploy_brand_json.py --apply

## Why this exists (BR-U3-1)

AdCP's seller verification checklist requires a seller to resolve a governance_context token's `iss`
to a JWKS it can trust -- trusting a JWKS URL embedded in the token itself would let an attacker mint
a token pointing at a self-hosted fake JWKS. The spec's answer is: resolve the BUYER's domain (the
seller already knows which buyer it's talking to, from account setup) and fetch THAT buyer's
brand.json, then require the token's `iss` to byte-match a `governance`-typed entry there. brand.json
is the trust anchor; the token is not self-authenticating.

This project's buyer has exactly one governance agent configured (the reference governance agent),
so brand.json here declares exactly one `governance`-typed `agents[]` entry.

## Where this goes, and why it's a different path than the governance agent's own JWKS

`agents/governance/reference-governance/app/adcpRefGovernance/deploy_jwks.py` publishes the
GOVERNANCE AGENT's own JWKS at `governance/.well-known/jwks.json` on this same CloudFront
distribution/bucket. This script publishes the BUYER's brand.json at the distribution ROOT
(`.well-known/brand.json`), because brand.json is the buyer's document (it names which governance
agent the buyer uses), not the governance agent's.

## Why the `url` value here MUST byte-match the governance agent's own `iss`

A seller verifying a token checks that the token's `iss` claim appears as a `governance`-typed
`agents[].url` entry in the resolved brand.json (spec: "byte-for-byte match... including path
component"). If the two drift, every governance_context token the agent issues becomes unverifiable by
any seller resolving via brand.json.

That match is now structural rather than maintained: this script and the governance agent's
`deploy_jwks.py` both resolve the origin of the distribution `deploy_governance_origin.py` created,
so there are no longer two literals that have to agree. It used to be a pair of hardcoded URLs with a
comment asking the reader to keep them identical.

**Corrected 2026-08-12**: this used to be a path (`.../governance`) on the BUYER's own shared
distribution. That is incompatible with the SDK's `{origin of iss}/.well-known/...`-style revocation-
list resolution (RFC 6454 origin has no path) -- see `deploy_governance_origin.py`'s module docstring
for the live failure this caused and why a dedicated bare origin is the correct fix, not a narrower
one scoped only to the revocation list.

## The CloudFront trap this script verifies rather than assumes

Same as `deploy_jwks.py`: the distribution rewrites 403 to `index.html` for SPA routing, so a missing
`.well-known` object returns the UI's HTML with status 200, not a 404. This script fetches the
published URL afterward and asserts it parses as JSON containing the expected governance agent entry,
because "the upload returned no error" is not evidence.
"""

from __future__ import annotations

from aws_region import region

import argparse
import functools
import json
import os
import sys

import boto3
import httpx
from dotenv import load_dotenv

load_dotenv()

# Same source as deploy_ui.py, which owns this bucket: the account is read from .env, where
# deploy_all.py writes it from the live credentials rather than anyone typing it in.
REGION = region()
ACCOUNT_ID = os.environ["AWS_ACCOUNT_ID"]
#: Instance prefix (see deploy_all.resolve_prefix). Must match deploy_ui.py's BUCKET_NAME exactly,
#: or brand.json publishes to a bucket the UI never created. Defaults to 'adcp' for a standalone run.
INSTANCE_PREFIX = os.environ.get("INSTANCE_PREFIX", "adcp").strip() or "adcp"
BUCKET_NAME = f"{INSTANCE_PREFIX}-buyer-agent-ui"

GOVERNANCE_AGENT_ID = "reference-governance"
S3_KEY = ".well-known/brand.json"

s3 = boto3.client("s3", region_name=REGION)
cloudfront = boto3.client("cloudfront", region_name=REGION)


def governance_agent_url() -> str:
    """The governance agent's `iss`, i.e. the origin of its own CloudFront distribution.

    This used to be a literal that a comment required to "byte-match deploy_jwks.py's ISSUER" in a
    different package. Both now read one value, written by deploy_governance_origin.py when it creates
    the distribution, so there is no pair of copies to keep in agreement -- a mismatch here publishes a
    brand.json naming an issuer whose keys live somewhere else, and every seller's verification fails
    while this script reports success.
    """
    url = os.environ.get("GOVERNANCE_ORIGIN_URL", "").strip()
    if not url:
        raise SystemExit(
            "GOVERNANCE_ORIGIN_URL is not set. It is written by the governance origin step:\n"
            "  python3 deploy_all.py --only governance-origin"
        )
    return url.rstrip("/")


def governance_agent_jwks_uri() -> str:
    return f"{governance_agent_url()}/.well-known/jwks.json"


@functools.lru_cache(maxsize=1)
def _distribution() -> dict:
    for page in cloudfront.get_paginator("list_distributions").paginate():
        for item in (page.get("DistributionList") or {}).get("Items") or []:
            origins = [o.get("DomainName", "") for o in item["Origins"]["Items"]]
            if any(BUCKET_NAME in o for o in origins):
                return item
    raise SystemExit(f"No CloudFront distribution found in front of {BUCKET_NAME}.")


def distribution_id() -> str:
    return _distribution()["Id"]


def published_url() -> str:
    """Where brand.json ends up, read off the distribution that actually fronts the bucket."""
    return f"https://{_distribution()['DomainName']}/{S3_KEY}"


def build_brand_json() -> dict:
    """This buyer's brand.json: one governance-typed agent entry, no house/portfolio structure --
    this project has a single buyer with a single governance agent, so the minimal shape the SDK's
    BrandJsonJwksResolver can walk is a top-level `agents[]` array, not a `house`/`brands[]` document.
    """
    return {
        "agents": [
            {
                "type": "governance",
                "url": governance_agent_url(),
                "jwks_uri": governance_agent_jwks_uri(),
                "id": GOVERNANCE_AGENT_ID,
            }
        ]
    }


def verify_published(expected_url: str) -> None:
    """Fetch what was actually published and check it is brand.json, not the SPA's HTML.

    Uses httpx (already a project dependency via adcp) rather than stdlib urllib -- httpx verifies
    against the certifi bundle by default, avoiding local-trust-store gaps some Python builds have
    (notably python.org's macOS framework build, which does not seed the OS keychain).
    """
    response = httpx.get(published_url(), timeout=15)
    body = response.content
    content_type = response.headers.get("content-type", "")
    try:
        document = json.loads(body)
    except json.JSONDecodeError:
        head = body[:80].decode("utf-8", "replace")
        raise SystemExit(
            f"{published_url()} did not return JSON (Content-Type: {content_type!r}). First bytes: "
            f"{head!r}\nThis is the CloudFront 403-to-index.html rewrite serving the UI, which means "
            "the object is not at the expected key."
        ) from None
    agents = document.get("agents", [])
    governance_entries = [a for a in agents if a.get("type") == "governance"]
    urls = [a.get("url") for a in governance_entries]
    if expected_url not in urls:
        raise SystemExit(
            f"Published brand.json has no governance agent with url {expected_url!r}; found {urls}."
        )
    print(f"  verified: {published_url()} declares a governance agent at {expected_url!r}")
    print(f"  content-type: {content_type}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    brand_json = build_brand_json()
    print("brand.json built:")
    print(json.dumps(brand_json, indent=2))
    print(f"\ns3 : s3://{BUCKET_NAME}/{S3_KEY}")
    print(f"url: {published_url()}")

    if not args.apply:
        print("\nDry run. Nothing uploaded.")
        return 0

    body = (json.dumps(brand_json, indent=2) + "\n").encode()
    s3.put_object(
        Bucket=BUCKET_NAME,
        Key=S3_KEY,
        Body=body,
        ContentType="application/json",
        # Short cache, same reasoning as deploy_jwks.py: this object is tiny, verifiers cache it
        # themselves (BrandJsonJwksResolver has its own cooldown), and a rotated governance agent
        # entry should become visible quickly.
        CacheControl="public, max-age=300",
    )
    print(f"\nUploaded {len(body)}B to s3://{BUCKET_NAME}/{S3_KEY}")

    dist = distribution_id()
    cloudfront.create_invalidation(
        DistributionId=dist,
        InvalidationBatch={
            "Paths": {"Quantity": 1, "Items": [f"/{S3_KEY}"]},
            "CallerReference": f"brandjson-{hash(body) & 0xFFFFFFFF}",
        },
    )
    print(f"Invalidated /{S3_KEY} on distribution {dist}")

    print("\n=== Verifying what is actually served ===")
    verify_published(governance_agent_url())

    print(f"\nSellers resolve this buyer's identity via: {published_url()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
