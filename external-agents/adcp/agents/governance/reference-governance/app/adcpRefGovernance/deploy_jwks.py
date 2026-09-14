"""Publish this agent's public JWKS so sellers can verify its governance_context tokens.

    uv run python deploy_jwks.py            # dry run: builds and prints the JWKS, uploads nothing
    uv run python deploy_jwks.py --apply

## Where the location comes from, and why it must be declared

AdCP's own field description for `jwks_uri` on a brand.json `agents[]` entry:

    HTTPS URL of the agent's JWKS (RFC 7517) containing public keys used to verify artifacts this agent
    signs ... Verified artifacts include signed governance_context tokens (for governance agents) ...
    When absent, verifiers MUST default to /.well-known/jwks.json on the origin of `url`.

Two consequences, both load-bearing:

1. **Discovery is DECLARED, not derived from `iss`.** An earlier design in this project assumed
   `{iss}/.well-known/jwks.json`. It is not that, and the difference matters because it removes the
   supposed circular dependency between "the agent needs its own URL" and "the URL is unknown until the
   runtime exists". `iss` is only an identifier; key discovery is a separate, declared pointer.

2. **The default is unusable for us, so `jwks_uri` MUST be declared explicitly.** The fallback anchors
   on the origin of the agent's `url`. For an AgentCore runtime that origin is
   `https://bedrock-agentcore.us-east-1.amazonaws.com`, which we do not control and cannot publish to.
   Relying on the default would produce tokens no seller could ever verify, and nothing would fail at
   signing time -- the failure would surface only in someone else's verifier.

So the JWKS goes on this agent's own dedicated CloudFront distribution (`deploy_governance_origin.py`),
not the buyer's shared UI distribution, and that URL is registered as `jwks_uri` on the agent record.

## Why a dedicated origin, not a path under the buyer's shared distribution

An earlier version of this script published under `{buyer's distribution}/governance/...` with
`ISSUER` carrying that same `/governance` path. That is incompatible with `{origin of iss}/.well-known
/...`-style resolution (RFC 6454 origin = scheme+host+port, no path) that the revocation list MUST use
per spec -- confirmed live: every seller's `AsyncCachingRevocationChecker.from_issuer_origin` stripped
the path and requested the buyer's own bare root, which served the buyer UI's `index.html` instead of
a JWS document. See `deploy_governance_origin.py`'s module docstring for the full trace.

## Why this is not a secret-handling exercise

The private key lives in KMS and is non-exportable. `kms:GetPublicKey` returns the public half, so this
script derives a public artifact rather than moving key material. That is what made KMS the right answer
over a PEM in Secrets Manager, and it is why publishing is a deploy step rather than a decision.

## The CloudFront trap this script verifies rather than assumes

The distribution rewrites 403 to `index.html` for single-page-app routing. A missing `.well-known` object
therefore returns the **UI's HTML with status 200** rather than a 404 -- so an upload that silently went
to the wrong key looks indistinguishable from success until a verifier tries to parse HTML as a JWKS.
This script fetches the published URL afterwards and asserts it parses as JSON containing the expected
`kid`, because "the upload returned no error" is not evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request

import boto3

import governance_origin

#: This agent's OWN bare-origin bucket/distribution (deploy_governance_origin.py), NOT the buyer's
#: shared UI bucket. A bare origin (no path) is required so `{origin of iss}/.well-known/...`-style
#: resolution -- which the AdCP SDK's revocation checker performs via RFC 6454 origin derivation --
#: lands on THIS agent's documents rather than stripping a path prefix down to an unrelated shared
#: root. See deploy_governance_origin.py's docstring for the live bug this fixes.
#:
#: Bucket, issuer and region all come from governance_origin so that this file, deploy_revocations.py,
#: the runtime's GOVERNANCE_AGENT_URL and the buyer's brand.json cannot name different origins. They
#: used to hold four copies of the issuer, propagated by hand.
# Resolved on use rather than at import. deploy_revocations is imported by the republisher
# Lambda's handler, so an eager account lookup would add an STS call to every cold start, and
# importing either module would do network I/O.
REGION = governance_origin.region()
bucket_name = governance_origin.bucket_name

S3_KEY = ".well-known/jwks.json"

s3 = boto3.client("s3", region_name=REGION)
cloudfront = boto3.client(
    "cloudfront", region_name=governance_origin.CLOUDFRONT_API_REGION
)


def issuer() -> str:
    """The origin this agent's tokens claim, and where its keys are published."""
    return governance_origin.origin_url()


def published_url() -> str:
    return governance_origin.published_url(S3_KEY)


def distribution_id() -> str:
    for page in cloudfront.get_paginator("list_distributions").paginate():
        for item in (page.get("DistributionList") or {}).get("Items") or []:
            origins = [o.get("DomainName", "") for o in item["Origins"]["Items"]]
            if any(bucket_name() in o for o in origins):
                return item["Id"]
    raise SystemExit(f"No CloudFront distribution found in front of {bucket_name()}.")


def build_jwks() -> dict:
    """The JWKS, derived from KMS. Imports jws so there is ONE definition of the public JWK shape."""
    import jws

    jwk = jws.public_jwk_from_kms()
    if not jwk.get("kid"):
        raise SystemExit(
            "GOVERNANCE_SIGNING_KID is not set. A JWKS whose key has no kid cannot be selected by a "
            "verifier, which resolves keys by kid."
        )
    return {"keys": [jwk]}


def verify_published(expected_kid: str) -> None:
    """Fetch what was actually published and check it is a JWKS, not the SPA's HTML."""
    with urllib.request.urlopen(published_url(), timeout=15) as response:
        body = response.read()
        content_type = response.headers.get("Content-Type", "")
    try:
        document = json.loads(body)
    except json.JSONDecodeError:
        head = body[:80].decode("utf-8", "replace")
        raise SystemExit(
            f"{published_url()} did not return JSON (Content-Type: {content_type!r}). First bytes: "
            f"{head!r}\nThis is the CloudFront 403-to-index.html rewrite serving the UI, which means the "
            "object is not at the expected key."
        ) from None
    kids = [k.get("kid") for k in document.get("keys", [])]
    if expected_kid not in kids:
        raise SystemExit(f"Published JWKS does not contain kid {expected_kid!r}; found {kids}.")
    print(f"  verified: {published_url()} returns a JWKS containing kid {expected_kid!r}")
    print(f"  content-type: {content_type}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    # The runtime receives its signing configuration from agentcore.json; this CLI reads the same file
    # so the key it publishes a JWKS for is the key the runtime signs with.
    applied = governance_origin.load_runtime_signing_env()
    if applied:
        print(f"signing config from agentcore.json: {', '.join(applied)}")

    jwks = build_jwks()
    kid = jwks["keys"][0]["kid"]
    print(f"JWKS built from KMS (kid {kid}):")
    print(json.dumps(jwks, indent=2))
    print(f"\nissuer   : {issuer()}")
    print(f"jwks_uri : {published_url()}")
    print(f"s3       : s3://{bucket_name()}/{S3_KEY}")

    if not args.apply:
        print("\nDry run. Nothing uploaded.")
        return 0

    body = (json.dumps(jwks, indent=2) + "\n").encode()
    s3.put_object(
        Bucket=bucket_name(),
        Key=S3_KEY,
        Body=body,
        ContentType="application/jwk-set+json",
        # Short cache: a rotated key must become visible quickly, and this object is tiny. Verifiers
        # cache it themselves (the SDK's resolver has its own cooldown), so a long CDN TTL only delays
        # rotation without saving meaningful traffic.
        CacheControl="public, max-age=300",
    )
    print(f"\nUploaded {len(body)}B to s3://{bucket_name()}/{S3_KEY}")

    dist = distribution_id()
    cloudfront.create_invalidation(
        DistributionId=dist,
        InvalidationBatch={
            "Paths": {"Quantity": 1, "Items": [f"/{S3_KEY}"]},
            "CallerReference": f"jwks-{kid}-{hash(body) & 0xFFFFFFFF}",
        },
    )
    print(f"Invalidated /{S3_KEY} on distribution {dist}")

    print("\n=== Verifying what is actually served ===")
    verify_published(kid)

    print("\nRegister this as `jwks_uri` on the governance agent's record:")
    print(f"  {published_url()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
