"""Seller-side governance verification (U3, BR-U3-3 through BR-U3-9).

Two independent checks happen here, in order, per the AdCP spec's seller-enforcement sequencing
(confirmed against `security.mdx` and the campaign governance specification):

1. **Verify the buyer's intent-phase token** attached to an incoming spend-commit request
   (`verify_intent_token` below). This is pure crypto + cached lookups -- no call to the governance
   agent itself, only to its published JWKS/`brand.json`/revocation-list documents.
2. **The seller's own execution check** -- a separate outbound `check_governance` MCP call this
   seller makes AFTER step 1 passes, carrying its OWN `planned_delivery` (BR-U3-9). That call is made
   from `main.py::create_media_buy` directly (mirrors the buyer's `_call_seller_tool_mcp` shape); this
   module owns step 1 and the supporting pieces (persisted-cache bootstrap, jti replay ledger,
   planned_delivery builder), not the outbound MCP call itself.

## Composed verification, never hand-rolled crypto (NFR-U3-5)

Every cryptographic and SSRF-sensitive operation below is delegated to the installed `adcp` SDK:
`adcp.signing.brand_jwks.BrandJsonJwksResolver` (buyer identity -> governance agent's JWKS),
`adcp.signing.jws.averify_jws_document` (signature + alg/typ + exp/iat), and
`adcp.signing.revocation_fetcher.AsyncCachingRevocationChecker` (kid/jti revocation, fail-closed on
staleness). No code path here constructs a raw HTTP client for any of the three counterparty-supplied
document fetches -- that discipline is what NFR-U3-5 requires and this module's own imports are the
enforcement of it.
"""

from __future__ import annotations

import os

import base64
import binascii
import json
import time
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from adcp.signing.brand_jwks import BrandJsonJwksResolver
from adcp.signing.jwks import async_default_jwks_fetcher
from adcp.signing.jws import JwsError, averify_jws_document
from adcp.signing.revocation_fetcher import (
    AsyncCachingRevocationChecker,
    RevocationListFreshnessError,
    async_default_revocation_list_fetcher,
)

from fixtures import DEFAULT_AGENT_URL
from state import _table, _from_dynamo, _to_dynamo  # noqa: F401 -- _to_dynamo used below

#: This seller's own canonical identity -- the `aud` a buyer's intent token must name, and the
#: `caller` this seller identifies itself as on its own outbound execution check. Reuses the same
#: constant `fixtures.py`'s format_ids already declare as this agent's URL, rather than a second,
#: possibly-drifting definition of "this seller's own address."
THIS_SELLER_URL = DEFAULT_AGENT_URL

#: This buyer's brand.json -- the trust anchor a seller resolves BEFORE trusting any JWKS a token's
#: `iss` claims to use (see module docstring). This project has a single buyer, so a single value
#: rather than a per-account lookup; a multi-buyer deployment would resolve this from account setup.
#:
#: Read from the environment because it names a CloudFront distribution, which only exists in the
#: account that created it. `deploy_all.py` writes it into each seller's `agentcore.json` envVars from
#: the buyer UI origin it created, so the value always matches the buyer actually deployed alongside.
BUYER_BRAND_JSON_URL_ENV = "BUYER_BRAND_JSON_URL"


def buyer_brand_json_url() -> str:
    """Where to fetch the buyer's brand.json.

    Raises rather than defaulting. This is the trust anchor for every governance token this seller
    accepts, so guessing an origin would mean either resolving keys from somewhere unintended or
    silently accepting tokens without the anchor the spec requires.
    """
    url = os.environ.get(BUYER_BRAND_JSON_URL_ENV, "").strip()
    if not url:
        raise RuntimeError(
            f"{BUYER_BRAND_JSON_URL_ENV} is not set. It is the trust anchor for governance token "
            "verification, so there is no safe default.\n"
            "  Set by: python3 deploy_all.py --only buyer-ui-origin"
        )
    return url

#: Governance context tokens are typed "adcp-gov+jws" per the AdCP JWS profile -- see
#: agents/governance/reference-governance/app/adcpRefGovernance/jws.py's own header construction.
GOVERNANCE_CONTEXT_TYP = "adcp-gov+jws"

#: Grace window added to a jti's DynamoDB TTL beyond its own exp claim (BR-U3-7). Generous on purpose
#: -- this bounds ledger-entry lifetime, not a security-relevant window; a jti seen once is rejected
#: on replay regardless of how close to exp it is.
_JTI_TTL_GRACE_SECONDS = 24 * 60 * 60

#: Bootstrap-only staleness window for persisted documents that declare no freshness contract of
#: their own (brand.json, JWKS -- see nfr-design-patterns.md pattern 3). The revocation list uses its
#: own declared `next_update` instead, handled separately.
_BOOTSTRAP_ONLY_WINDOW_SECONDS = 60 * 60

FailureReason = Literal[
    "malformed",
    "alg_not_allowed",
    "signature_invalid",
    "expired",
    "wrong_audience",
    "wrong_subject",
    "wrong_phase",
    "issuer_not_in_brand_json",
    "revoked",
    "replayed",
    "unverifiable",
]


@dataclass(frozen=True)
class IntentTokenVerificationResult:
    valid: bool
    claims: dict[str, Any] | None = None
    failure_reason: FailureReason | None = None


# --- persisted document cache (NFR-U3-6, NFR Design pattern 2) ---------------------------------


def get_persisted_doc(doc_type: str, key: str) -> dict[str, Any] | None:
    """A previously-fetched brand.json/JWKS/revocation-list document, or `None` if never persisted.

    `doc_type` is one of "brandjson", "jwks", "revocation" (matches logical-components.md's item
    table). `key` is the buyer domain for "brandjson", or the governance agent URL for the other two.
    """
    item = _table().get_item(Key={"pk": f"GOVDOC#{doc_type}#{key}", "sk": "META"}).get("Item")
    return _from_dynamo(item) if item is not None else None


def put_persisted_doc(
    doc_type: str, key: str, document: dict[str, Any], *, declared_next_update: str | None = None
) -> None:
    """Write back a freshly, successfully live-fetched document (NFR-U3-6 pattern 2's "after every
    successful live fetch" step). No `ttl` attribute -- cleanup is deferred to NFR-U3-6-BACKLOG."""
    item: dict[str, Any] = {
        "pk": f"GOVDOC#{doc_type}#{key}",
        "sk": "META",
        "fetched_at": _now_iso(),
        "document": document,
    }
    if declared_next_update is not None:
        item["declared_next_update"] = declared_next_update
    _table().put_item(Item=_to_dynamo(item))


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _is_persisted_entry_usable(item: dict[str, Any]) -> bool:
    """NFR Design pattern 3: two staleness rules depending on whether the document declares its own
    freshness contract."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    declared = item.get("declared_next_update")
    if declared:
        # Revocation list: judged by its OWN declared cadence, same grace this module's revocation
        # checker already applies on the live-fetch path -- not a second, independently-tuned window.
        next_update = datetime.fromisoformat(declared.replace("Z", "+00:00"))
        # Reuse the SDK's own grace multiplier semantics loosely here: a persisted copy older than
        # its own next_update is not usable as a bootstrap at all (the live checker's grace window
        # applies to a list it already holds, not to seeding a cold cache with a stale one).
        return now <= next_update
    fetched_at = item.get("fetched_at")
    if not fetched_at:
        return False
    fetched = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    return (now - fetched).total_seconds() <= _BOOTSTRAP_ONLY_WINDOW_SECONDS


# --- jti replay ledger (BR-U3-7) --------------------------------------------------------------


def jti_first_seen(jti: str, exp: int) -> bool:
    """Record `jti` as seen; return True iff this is the FIRST time (i.e. NOT a replay).

    Conditional PutItem (`attribute_not_exists(pk)`) -- same first-writer-wins shape as
    `DynamoDBIdempotencyBackend.put` in state.py. `ttl` = `exp` + grace so DynamoDB's native TTL does
    cleanup with no scheduled job.
    """
    from botocore.exceptions import ClientError

    item = {
        "pk": f"GOVJTI#{jti}",
        "sk": "META",
        "seen_at": _now_iso(),
        "ttl": int(exp) + _JTI_TTL_GRACE_SECONDS,
    }
    try:
        _table().put_item(Item=_to_dynamo(item), ConditionExpression="attribute_not_exists(pk)")
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return False
        raise


# --- planned_delivery (BR-U3-9) ---------------------------------------------------------------


def build_planned_delivery(
    packages: list[dict[str, Any]],
    *,
    currency: str,
    start_time: str,
    end_time: str,
    channels: list[str] | None = None,
) -> dict[str, Any]:
    """The seller's own honest `planned_delivery` -- ONLY the fields this fixture-scale seller can
    actually determine.

    `channels` is the set of `MediaChannel` values the booked products actually declare (their
    fixture `channels`), resolved by the caller from the packages' products. It is a real property of
    the inventory being sold, not an inference, so stating it lets the governance agent's
    channel_compliance check run against the plan's authorised channels. When the caller cannot
    resolve any channel, it is omitted rather than sent empty.

    Geo, frequency_cap, audience_targeting and enforced_policies remain omitted: this fixture seller
    genuinely cannot determine them, and inventing them would violate the no-fabricated-data rule
    (Q4=A) -- see nfr-design-patterns.md pattern-not-applicable and domain-entities.md's table.
    """
    total_budget = sum(float(p.get("budget", 0.0)) for p in packages)
    planned: dict[str, Any] = {
        "total_budget": total_budget,
        "currency": currency,
        "start_time": start_time,
        "end_time": end_time,
    }
    if channels:
        planned["channels"] = channels
    return planned


# --- intent token verification (BR-U3-3) ------------------------------------------------------

#: Cached per governance-agent URL for the lifetime of this process -- these resolvers hold their
#: own in-memory state (JWKS cache, revocation-list cache) that should persist across calls within
#: one container, not be rebuilt per request.
_brand_jwks_resolvers: dict[str, BrandJsonJwksResolver] = {}
_revocation_checkers: dict[str, AsyncCachingRevocationChecker] = {}


async def _persisted_bootstrap_jwks_fetcher(uri: str, *, allow_private: bool = False):
    """NFR-U3-6/pattern 2: wraps the SDK's default JWKS fetcher with a persisted-cache bootstrap.

    Matches `adcp.signing.jwks.AsyncJwksFetcher`'s exact protocol -- `(uri, *, allow_private=False)`
    -- since `AsyncCachingJwksResolver` (used internally by `BrandJsonJwksResolver` for the actual
    JWKS fetch, per its own `_sync_selector`) calls its `jwks_fetcher=` override with that signature.
    This IS a documented, public seam, unlike brand.json's fetch (see this module's docstring for
    the limitation that does NOT apply to).

    First call in this process: check DynamoDB for a persisted JWKS keyed by `uri`; if present and
    within the bootstrap-only staleness window, return it without a live fetch. Otherwise (or on any
    subsequent call), fetch live via the SDK's own default fetcher, then persist the result.
    """
    persisted = get_persisted_doc("jwks", uri)
    if persisted is not None and _is_persisted_entry_usable(persisted):
        return persisted["document"]
    document = await async_default_jwks_fetcher(uri, allow_private=allow_private)
    put_persisted_doc("jwks", uri, document)
    return document


async def _persisted_bootstrap_revocation_fetcher(
    uri: str, *, if_none_match: str | None = None, if_modified_since: str | None = None
):
    """Same pattern as `_persisted_bootstrap_jwks_fetcher`, for the revocation list.

    Matches `adcp.signing.revocation_fetcher.AsyncRevocationListFetcher`'s exact protocol.

    The persisted bootstrap is used only on a genuinely cold cache -- i.e. when the CALLER (the
    SDK's `AsyncCachingRevocationChecker`) has no cached list of its own and is asking for the
    FIRST fetch, signaled here by `if_none_match`/`if_modified_since` both being absent (a
    conditional refresh always sends at least one of them once it holds any prior result). On a
    conditional call this wrapper always fetches live -- the persisted document is a cold-start seed
    only, never a substitute for the checker's own conditional-refresh cycle.

    Unlike JWKS/brand.json, the revocation list declares its OWN freshness contract
    (`next_update`) -- `_is_persisted_entry_usable` judges it by that contract, via
    `declared_next_update`, rather than the generic bootstrap-only window.

    The bootstrapped `body` is still passed through the checker's OWN JWS verification
    (`AsyncCachingRevocationChecker._refresh` calls `averify_jws_document` on whatever this fetcher
    returns) -- this cache is a bootstrap of raw bytes, never a trust shortcut.
    """
    from adcp.signing.revocation_fetcher import FetchResult

    if if_none_match is None and if_modified_since is None:
        persisted = get_persisted_doc("revocation", uri)
        if persisted is not None and _is_persisted_entry_usable(persisted):
            return FetchResult(body=persisted["document"], etag=None, not_modified=False)

    result = await async_default_revocation_list_fetcher(
        uri, if_none_match=if_none_match, if_modified_since=if_modified_since
    )
    if not result.not_modified:
        # The governance agent emits a compact JWS string (jws.py::issue_revocation_list). Persist
        # it as-is, and separately DECODE (never verify -- signature verification stays the
        # checker's job on every real use) the payload's `next_update` purely to judge the
        # PERSISTED ENTRY's own staleness on a future cold start (NFR Design pattern 3). This is the
        # same decode-without-verify pattern already established in this project (the buyer's
        # `_governance_context_expired`, the governance agent's own `read_governance_context`) --
        # never used for a trust decision, only for "should this bootstrap seed still be used."
        put_persisted_doc(
            "revocation", uri, result.body, declared_next_update=_peek_next_update(result.body)
        )
    return result


def _peek_next_update(body: str | dict[str, Any]) -> str | None:
    """Decode (never verify) a revocation-list JWS's `next_update` claim, for cache-staleness
    bookkeeping only -- see `_persisted_bootstrap_revocation_fetcher`'s docstring."""
    if isinstance(body, dict):
        return None  # general-JSON JWS shape; not emitted by this project's governance agent
    parts = body.split(".")
    if len(parts) != 3:
        return None
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    next_update = payload.get("next_update")
    return next_update if isinstance(next_update, str) else None


def _brand_jwks_resolver_for(governance_agent_url: str) -> BrandJsonJwksResolver:
    """Resolves the governance agent's JWKS via the buyer's brand.json.

    NFR-U3-6 designed a persisted bootstrap for all three documents this unit fetches. brand.json
    itself is NOT wrapped: `BrandJsonJwksResolver` only exposes its brand.json fetch behavior through
    private, docstring-labeled test seams (`_fetcher`/`_client_factory`), not a public production
    API -- using them would violate the same "compose the SDK, never touch its internals" principle
    that rules out hand-rolling verification in the first place. The inner JWKS fetch (once
    brand.json has resolved which `jwks_uri` to use) DOES get the persisted bootstrap, via
    `jwks_fetcher=` below, which IS a public, documented parameter.
    """
    resolver = _brand_jwks_resolvers.get(governance_agent_url)
    if resolver is None:
        resolver = BrandJsonJwksResolver(
            buyer_brand_json_url(),
            agent_type="governance",
            jwks_fetcher=_persisted_bootstrap_jwks_fetcher,
        )
        _brand_jwks_resolvers[governance_agent_url] = resolver
    return resolver


def _revocation_checker_for(issuer: str, governance_agent_url: str) -> AsyncCachingRevocationChecker:
    """`issuer` is the token's own verified `iss` claim -- the revocation list lives at
    `{origin of iss}/.well-known/governance-revocations.json` per spec (confirmed against
    `deploy_revocations.py`'s own docstring on the governance agent). This is NOT the same value as
    `governance_agent_url` (the stored `governance_agents[].url` this seller calls the agent's
    `check_governance` MCP tool at) -- that is a transport endpoint the account config declares,
    while `iss` is the agent's cryptographic identity from a token that has already been verified.
    Confusing the two is a real bug this project hit live: the AgentCore invoke URL's origin
    (`https://bedrock-agentcore.us-east-1.amazonaws.com`) has no revocation list at all, and every
    execution check 404'd on it. `governance_agent_url` is still needed here only to key the resolver
    cache one-per-configured-agent, consistent with `_brand_jwks_resolver_for`."""
    checker = _revocation_checkers.get(governance_agent_url)
    if checker is None:
        resolver = _brand_jwks_resolver_for(governance_agent_url)
        checker = AsyncCachingRevocationChecker.from_issuer_origin(
            issuer,
            jwks_resolver=resolver,
            fetcher=_persisted_bootstrap_revocation_fetcher,
        )
        _revocation_checkers[governance_agent_url] = checker
    return checker


def _decode_header_unverified(token: str) -> dict[str, Any] | None:
    """Peek at the header without verifying -- used only to fail fast on malformed input before
    attempting the SDK's own (heavier) verification path."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    padded = parts[0] + "=" * (-len(parts[0]) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(padded))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None


async def verify_intent_token(
    token: str, *, plan_id: str, this_seller_url: str, governance_agent_url: str
) -> IntentTokenVerificationResult:
    """BR-U3-3 steps 1-10, short-circuiting on first failure.

    Steps 1-2, 4-5 (parse, alg/typ, signature, exp/iat) and step 3 (iss -> brand.json -> jwks_uri ->
    kid) are delegated to `averify_jws_document` + `BrandJsonJwksResolver`. Step 9 (revocation) is
    delegated to `AsyncCachingRevocationChecker`. Steps 6-8 (aud/sub/phase) and step 10 (jti replay)
    are this function's own claim comparisons.
    """
    if _decode_header_unverified(token) is None:
        return IntentTokenVerificationResult(valid=False, failure_reason="malformed")

    resolver = _brand_jwks_resolver_for(governance_agent_url)
    try:
        claims = await averify_jws_document(
            token, jwks_resolver=resolver, expected_typ=GOVERNANCE_CONTEXT_TYP
        )
    except JwsError:
        # Covers steps 1-2, 4: malformed, alg not allowed, typ mismatch, signature invalid.
        # BR-U3-3/NFR-U3-3: every verification failure collapses to PERMISSION_DENIED for the caller;
        # the specific reason is for logging only.
        return IntentTokenVerificationResult(valid=False, failure_reason="signature_invalid")
    except Exception:
        # brand.json/JWKS unreachable or malformed -- resolver-level failure, not a token defect.
        return IntentTokenVerificationResult(valid=False, failure_reason="unverifiable")

    # Step 5 (exp/iat): found while writing this module's own tests that averify_jws_document does
    # NOT check exp/iat at all -- confirmed against its docstring, which lists exactly 4 checks
    # (header parse, alg allowlist, typ match, signature) and none of them are time-based. This is
    # deliberate on the SDK's part (the same generic verifier also checks the revocation list, which
    # has its own different freshness semantics via next_update, not exp/iat) -- so a
    # governance_context-specific caller must check exp/iat itself. ±60s skew tolerance matches the
    # AdCP JWS profile's own convention used elsewhere in this project (the buyer's
    # _governance_context_expired, the governance agent's own token issuance).
    now = time.time()
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)) or now > exp + 60:
        return IntentTokenVerificationResult(valid=False, failure_reason="expired")
    iat = claims.get("iat")
    if isinstance(iat, (int, float)) and iat > now + 60:
        return IntentTokenVerificationResult(valid=False, failure_reason="expired")

    if claims.get("aud") != this_seller_url:
        return IntentTokenVerificationResult(valid=False, failure_reason="wrong_audience")
    if claims.get("sub") != plan_id:
        return IntentTokenVerificationResult(valid=False, failure_reason="wrong_subject")
    if claims.get("phase") != "intent":
        return IntentTokenVerificationResult(valid=False, failure_reason="wrong_phase")

    issuer = claims.get("iss")
    if not isinstance(issuer, str) or not issuer:
        return IntentTokenVerificationResult(valid=False, failure_reason="unverifiable")
    checker = _revocation_checker_for(issuer, governance_agent_url)
    kid = _decode_header_unverified(token).get("kid")  # already validated well-formed above
    try:
        if await checker(kid):
            return IntentTokenVerificationResult(valid=False, failure_reason="revoked")
        jti = claims.get("jti")
        if jti and await checker.is_jti_revoked(jti):
            return IntentTokenVerificationResult(valid=False, failure_reason="revoked")
    except RevocationListFreshnessError:
        # N6/spec: a stale revocation list past its grace window fails CLOSED, never open.
        return IntentTokenVerificationResult(valid=False, failure_reason="unverifiable")

    jti = claims.get("jti")
    exp = claims.get("exp")
    if jti and exp and not jti_first_seen(jti, exp):
        return IntentTokenVerificationResult(valid=False, failure_reason="replayed")

    return IntentTokenVerificationResult(valid=True, claims=claims)


# --- the seller's OWN outbound execution check (BR-U3-3 step "the seller's own check") --------


@dataclass(frozen=True)
class ExecutionCheckResult:
    """Outcome of this seller's own outbound `check_governance` call."""

    outcome: Literal["approved", "denied", "conditions", "unreachable"]
    governance_context: str | None = None
    verdict_payload: dict[str, Any] | None = None


def _governance_auth_headers(authentication: dict[str, Any]) -> dict[str, str]:
    """Build the outbound `Authorization` header from a `sync_governance`-stored `authentication`
    object (`{"schemes": [...], "credentials": "..."}` -- see `sync_governance`'s own docstring in
    main.py for the exact stored shape). This project's governance agent is registered with
    `schemes: ["Bearer"]`, so this seller sends the stored credential as a bearer token; any other
    scheme is rejected rather than guessed at.
    """
    schemes = authentication.get("schemes") or []
    if "Bearer" not in schemes:
        raise ValueError(f"unsupported governance agent authentication schemes: {schemes!r}")
    return {"Authorization": f"Bearer {authentication['credentials']}"}


async def run_execution_check(
    *,
    governance_agent_url: str,
    authentication: dict[str, Any],
    plan_id: str,
    governance_context: str,
    planned_delivery: dict[str, Any],
) -> ExecutionCheckResult:
    """This seller's own outbound `check_governance` call (BR-U3-3's second step, R18.3-R18.5).

    Mirrors the buyer's `_call_seller_tool_mcp` shape (agents/buyer/reference-buyer/adcp_tools.py)
    -- same MCP transport pattern, kept as this seller's own inline code rather than a shared
    cross-package import, since BR-U3-8's shared module is `governance_verification.py` itself, not
    a second copy of MCP client plumbing (which stays per-seller, same as the buyer's own).
    """
    headers = _governance_auth_headers(authentication)
    args = {
        "plan_id": plan_id,
        "caller": THIS_SELLER_URL,
        "governance_context": governance_context,
        "planned_delivery": planned_delivery,
        "phase": "purchase",
    }
    try:
        async with streamablehttp_client(governance_agent_url, headers=headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("check_governance", args)
    except (httpx.HTTPError, OSError, TimeoutError):
        return ExecutionCheckResult(outcome="unreachable")

    if result.isError:
        return ExecutionCheckResult(outcome="unreachable")

    structured = getattr(result, "structuredContent", None)
    if not isinstance(structured, dict):
        return ExecutionCheckResult(outcome="unreachable")

    verdict = structured.get("verdict")
    if verdict == "approved":
        return ExecutionCheckResult(
            outcome="approved",
            governance_context=structured.get("governance_context"),
            verdict_payload=structured,
        )
    if verdict == "conditions":
        return ExecutionCheckResult(
            outcome="conditions",
            governance_context=structured.get("governance_context"),
            verdict_payload=structured,
        )
    if verdict == "denied":
        return ExecutionCheckResult(outcome="denied", verdict_payload=structured)
    # An unrecognized verdict value is treated the same as unreachable -- N6/fail-closed: never
    # interpret an unexpected shape as approval.
    return ExecutionCheckResult(outcome="unreachable", verdict_payload=structured)
