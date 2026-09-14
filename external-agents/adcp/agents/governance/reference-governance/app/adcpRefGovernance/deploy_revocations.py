"""Publish this agent's revocation list so sellers can check for revoked keys/tokens (BR-U3-2).

    uv run python deploy_revocations.py            # dry run: builds and prints the list, uploads nothing
    uv run python deploy_revocations.py --apply

## This document must be republished before its `next_update`, or every seller fails closed

The AdCP SDK's `AsyncCachingRevocationChecker` treats a revocation list past its declared
`next_update` (+ a small grace window) as `RevocationListFreshnessError` and refuses to answer --
which `governance_verification.verify_intent_token` maps to `failure_reason="unverifiable"`, and
every seller's `create_media_buy` then rejects the buyer's (perfectly valid, unexpired,
correctly-signed) governance_context with `PERMISSION_DENIED`. Confirmed live 2026-08-16: a document
published with the CLI's own default `next_update_seconds` (the spec FLOOR, 60s) went stale within
about a minute of the last manual `--apply` run and stayed that way, breaking every booking attempt
across the whole account until republished -- not a token bug, not a Part 1 regression, a staleness
bug in an operational document nothing was re-publishing.

Two independent things fix this, both applied here:
1. `_publish()` now declares a much longer cadence (`DEFAULT_NEXT_UPDATE_SECONDS`, 24h) so a single
   manual run stays valid for a full day rather than a single minute.
2. `scheduled_revocations/handler.py` (a small dedicated Lambda, wired into
   `agentcore/cdk/lib/cdk-stack.ts`) calls `_publish()` on a 12h EventBridge schedule -- half the
   declared cadence, so an isolated missed invocation still leaves margin before the previous
   document's `next_update` lapses. This script remains the manual/dry-run entry point; the Lambda
   reuses its exact publish logic rather than duplicating it.

## Why this exists

The AdCP spec's revocation section requires exp-based expiry alone is insufficient for
execution-phase tokens that live for a media buy's lifecycle -- a compromised signing key needs a way
to be invalidated before its outstanding tokens' `exp` naturally elapses. Sellers poll this document
and reject any token whose `kid` or `jti` appears in it.

This agent has never revoked anything, so both arrays start empty. The document's EXISTENCE and
VERIFIABILITY is what the seller-side 15-step checklist needs (step 9 in this project's BR-U3-3) --
an empty-but-signed, correctly-cadenced list is the honest starting state, not a placeholder standing
in for revocations that don't exist.

## Reuses the SAME signer as governance_context tokens -- no new key

`jws.py::issue_revocation_list` calls the same `_signer()` this agent already uses for
`issue_governance_context`. This is a new SIGNED DOCUMENT, not a new signing key -- no new KMS
permission, no new IAM policy (see Infrastructure Design's shared-infra question, answered A: no
change needed).

## Where this goes, and why it is on this agent's OWN dedicated origin

Unlike brand.json (the BUYER's document, at the buyer's distribution root), the revocation list is
THIS agent's own document, per spec: `{origin of iss}/.well-known/governance-revocations.json`.
"Origin" is RFC 6454 scheme+host+port -- no path. This agent's `iss` is a bare origin, the CloudFront
distribution `deploy_governance_origin.py` creates and `governance_origin.origin_url()` resolves, so
the revocation list goes at that origin's own `.well-known/`, alongside `.well-known/jwks.json`.

**Real bug this replaces**: an earlier version published under a `/governance/` path prefix on the
BUYER's shared distribution, with `iss` carrying that same path. The AdCP SDK's
`AsyncCachingRevocationChecker.from_issuer_origin` strips any path from `iss` before appending
`.well-known/governance-revocations.json` (confirmed by reading its `_normalize_issuer`), so every
real seller resolved the BUYER's own bare root instead -- which served the buyer UI's `index.html`
and failed JWS verification with "must have exactly 3 dot-separated segments, got 6". Found live
during U3 verification, not by inspection. A bare-origin `iss` is the only fix that is correct for
every current and future consumer of `{origin of iss}/.well-known/...`, not just this one document.

## The CloudFront trap this script verifies rather than assumes

Same as deploy_jwks.py/deploy_brand_json.py: the distribution rewrites 403 to `index.html` for SPA
routing, so a missing `.well-known` object returns the UI's HTML with status 200, not a 404. This
script fetches the published URL afterward and asserts it is a real, JWS-verifiable revocation list --
verified against this agent's OWN published JWKS (fetched independently, not assumed), because "the
upload returned no error" is not evidence.
"""

from __future__ import annotations

import argparse
import sys

import httpx

import boto3

import governance_origin

#: This agent's OWN bare-origin bucket (deploy_governance_origin.py), matching deploy_jwks.py. Both
#: resolve it through governance_origin so the two cannot publish to different places.
# Resolved on use rather than at import. deploy_revocations is imported by the republisher
# Lambda's handler, so an eager account lookup would add an STS call to every cold start, and
# importing either module would do network I/O.
REGION = governance_origin.region()
bucket_name = governance_origin.bucket_name

S3_KEY = ".well-known/governance-revocations.json"


def issuer() -> str:
    return governance_origin.origin_url()


def published_url() -> str:
    return governance_origin.published_url(S3_KEY)


def jwks_url() -> str:
    return governance_origin.published_url(".well-known/jwks.json")

#: How far out this document declares its own next refresh. The spec floor
#: (MIN_POLLING_INTERVAL_SECONDS, 60s) is technically conformant but operationally unworkable for a
#: document nothing was continuously republishing -- see this module's docstring for the live outage
#: that produced this constant. 24h gives a full day of validity per manual/scheduled publish; the
#: scheduled Lambda (scheduled_revocations/handler.py) republishes at half that cadence (12h) so a
#: single missed invocation still leaves margin.
DEFAULT_NEXT_UPDATE_SECONDS = 24 * 60 * 60

s3 = boto3.client("s3", region_name=REGION)
cloudfront = boto3.client("cloudfront", region_name=REGION)


def distribution_id() -> str:
    for page in cloudfront.get_paginator("list_distributions").paginate():
        for item in (page.get("DistributionList") or {}).get("Items") or []:
            origins = [o.get("DomainName", "") for o in item["Origins"]["Items"]]
            if any(bucket_name() in o for o in origins):
                return item["Id"]
    raise SystemExit(f"No CloudFront distribution found in front of {bucket_name()}.")


def build_revocation_list(*, next_update_seconds: int = DEFAULT_NEXT_UPDATE_SECONDS) -> str:
    """This agent's revocation list, signed. Both arrays empty -- nothing has ever been revoked."""
    import jws

    return jws.issue_revocation_list(issuer=issuer(), next_update_seconds=next_update_seconds)


def verify_published() -> dict:
    """Fetch what was actually published and check it verifies against this agent's own live JWKS.

    Returns the verified payload so a caller (main() or the Lambda handler) can log/report on it
    without re-fetching.
    """
    from adcp.signing.jws import verify_jws_document
    from adcp.signing.jwks import StaticJwksResolver
    from adcp.signing.revocation_fetcher import REVOCATION_LIST_TYP

    response = httpx.get(published_url(), timeout=15)
    body = response.text.strip()
    content_type = response.headers.get("content-type", "")
    if body.startswith("<"):
        raise RuntimeError(
            f"{published_url()} returned HTML (Content-Type: {content_type!r}), not a revocation "
            "list JWS. This is the CloudFront 403-to-index.html rewrite serving the UI, which means "
            "the object is not at the expected key."
        )

    jwks_response = httpx.get(jwks_url(), timeout=15)
    jwks = jwks_response.json()
    resolver = StaticJwksResolver(jwks)
    payload = verify_jws_document(body, jwks_resolver=resolver, expected_typ=REVOCATION_LIST_TYP)
    if payload["issuer"] != issuer():
        raise RuntimeError(f"Published list's issuer {payload['issuer']!r} != expected {issuer()!r}.")
    print(f"  verified: {published_url()} is a valid, signed revocation list")
    print(f"  issuer: {payload['issuer']}")
    print(f"  updated: {payload['updated']}, next_update: {payload['next_update']}")
    print(f"  revoked_kids: {payload['revoked_kids']}, revoked_jtis: {payload['revoked_jtis']}")
    return payload


def publish(*, next_update_seconds: int = DEFAULT_NEXT_UPDATE_SECONDS) -> dict:
    """Build, upload, invalidate and verify -- the one publish path both the CLI (`--apply`) and the
    scheduled Lambda call, so there is exactly one implementation of "how this document gets
    republished" rather than a CLI copy and a Lambda copy that could drift apart.

    Returns the verified published payload.
    """
    token = build_revocation_list(next_update_seconds=next_update_seconds)
    body = token.encode()
    s3.put_object(
        Bucket=bucket_name(),
        Key=S3_KEY,
        Body=body,
        ContentType="application/jose",
        # Short cache: sellers poll this on their own cadence and cache it themselves per the SDK's
        # own resolver; a long CDN TTL only delays visibility of a real revocation without saving
        # meaningful traffic on a document this small. Independent of DEFAULT_NEXT_UPDATE_SECONDS,
        # which is the DOCUMENT's own declared freshness contract, not this CDN's cache lifetime.
        CacheControl="public, max-age=60",
    )
    print(f"Uploaded {len(body)}B to s3://{bucket_name()}/{S3_KEY}")

    dist = distribution_id()
    cloudfront.create_invalidation(
        DistributionId=dist,
        InvalidationBatch={
            "Paths": {"Quantity": 1, "Items": [f"/{S3_KEY}"]},
            "CallerReference": f"revocations-{hash(body) & 0xFFFFFFFF}",
        },
    )
    print(f"Invalidated /{S3_KEY} on distribution {dist}")

    print("\n=== Verifying what is actually served ===")
    return verify_published()


def main() -> int:
    # Same reason as deploy_jwks.py: the runtime gets its signing config injected, this CLI does not.
    applied = governance_origin.load_runtime_signing_env()
    if applied:
        print(f"signing config from agentcore.json: {', '.join(applied)}")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--next-update-seconds",
        type=int,
        default=DEFAULT_NEXT_UPDATE_SECONDS,
        help=f"Declared freshness window (default {DEFAULT_NEXT_UPDATE_SECONDS}s / 24h).",
    )
    args = parser.parse_args()

    token = build_revocation_list(next_update_seconds=args.next_update_seconds)
    print("Revocation list built and signed:")
    print(f"  s3  : s3://{bucket_name()}/{S3_KEY}")
    print(f"  url : {published_url()}")
    print(f"  jws : {token[:60]}...")

    if not args.apply:
        print("\nDry run. Nothing uploaded.")
        return 0

    publish(next_update_seconds=args.next_update_seconds)
    print(f"\nSellers poll this at: {published_url()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
