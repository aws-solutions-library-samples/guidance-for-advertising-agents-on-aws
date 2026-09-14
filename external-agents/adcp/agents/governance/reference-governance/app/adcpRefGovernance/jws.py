"""The signed ``governance_context``.

AdCP 3.0 requires the **governance agent** to emit a compact JWS so sellers can verify authenticity,
authorisation scope and freshness. The buyer does not mint it; it receives one and forwards it. A seller
that has not implemented verification must still persist and forward the token unchanged, because
auditors depend on the chain being unbroken.

## Built on the SDK's crypto, not on a second JWT library

Every primitive here comes from ``adcp.signing``: ``sign_signature_base``, ``verify_signature``,
``b64url_encode``/``b64url_decode``, ``load_private_key_pem``, ``alg_for_jwk``, and
``parse_compact_jws``. Two reasons that matters beyond following instructions:

1. **Algorithm agreement.** The SDK constrains itself to ``Ed25519`` and ``ES256`` and its verifiers
   reject anything else. A token signed with a library that defaults elsewhere — ``RS256``, say —
   parses fine and then fails verification at the counterparty, which is a failure that shows up in
   someone else's system rather than ours.
2. **Byte-exactness.** ``parse_compact_jws`` deliberately returns the *original* base64url substrings
   rather than re-encoding them, because base64 decoding is lenient and a round trip can change the
   bytes that were hashed. A hand-rolled verifier is unlikely to preserve that, and the bug it causes
   is intermittent.

There is deliberately **no `alg: none` path and no unsigned fallback**. A governance agent that can emit
an unverifiable token has no security property left to offer, and "signing was not configured" must be a
startup failure rather than a silently weaker token.
"""

from __future__ import annotations

from aws_region import region

import hashlib
import json
import os
import time
import uuid
from typing import Any

from adcp.signing.crypto import (
    ALG_ED25519,
    ALG_ES256,
    decode_dss_signature,
    b64url_decode,
    b64url_encode,
    load_private_key_pem,
    public_key_from_jwk,
    sign_signature_base,
    verify_signature,
)
from adcp.signing.jws import JWS_ALG_TO_INTERNAL, JwsError, JwsMalformedError, parse_compact_jws

#: This project's crypto primitives (adcp.signing.crypto) speak an internal alg vocabulary
#: ("ed25519", "ecdsa-p256-sha256"); the JWS wire format requires the RFC 7518 names ("EdDSA",
#: "ES256") -- see adcp.signing.jws's own module comment on JWS_ALG_TO_INTERNAL for why the two
#: differ. Every header this module writes MUST carry the wire name, never the internal one.
#: Derived by inverting the SDK's own mapping (imported, not retyped) so the two names can never
#: silently drift apart from each other.
_INTERNAL_ALG_TO_JWS: dict[str, str] = {internal: wire for wire, internal in JWS_ALG_TO_INTERNAL.items()}


def _wire_alg(internal_alg: str) -> str:
    """The RFC 7518 JWS header value for this project's internal alg name.

    Found 2026-08-12 while adding issue_revocation_list: every token issue_governance_context has
    ever signed carries the INTERNAL alg name ("ed25519"/"ecdsa-p256-sha256") in its `alg` header
    instead of the wire name ("EdDSA"/"ES256") the AdCP JWS profile and adcp.signing.jws's own
    verify_jws_document require. A seller verifying via the SDK -- which is what U3 builds -- would
    reject every one of this agent's tokens as JwsMalformedError before reaching the signature check.
    This was invisible until something actually called the SDK's own verifier against a real issued
    token; this agent's own hand-rolled verify_governance_context never checked the alg against the
    SDK's allowlist, only against this project's own ALG_ED25519/ALG_ES256 constants -- so both sides
    of the mismatch used the same (wrong) vocabulary and agreed with each other while being wrong.
    """
    try:
        return _INTERNAL_ALG_TO_JWS[internal_alg]
    except KeyError:
        raise SigningNotConfigured(
            f"internal alg {internal_alg!r} has no JWS wire-format mapping; expected one of "
            f"{sorted(_INTERNAL_ALG_TO_JWS)}"
        ) from None

#: Lifetime of an intent-phase approval. Short on purpose: AdCP is explicit that a lapsed approval is no
#: approval, and a long window turns a check into a licence.
DEFAULT_TTL_SECONDS = 15 * 60

#: Phases AdCP defines for the lifecycle. `intent` is the buyer's; the rest are the seller's.
PHASES = ("intent", "purchase", "modification", "delivery")


class SigningNotConfigured(RuntimeError):
    """Raised at startup when no signing key is available.

    A hard failure, not a downgrade. See this module's docstring.
    """


def canonical_json(value: Any) -> bytes:
    """Deterministic JSON bytes: sorted keys, no incidental whitespace.

    Used for the ``plan_hash`` preimage. Two syncs of the same plan must produce the same hash, and
    Python's dict order or a stray space would otherwise make an audit record that cannot be
    reproduced.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def plan_hash(plan: dict[str, Any]) -> str:
    """SHA-256 over the canonical plan, as the ``plan_hash`` audit claim.

    AdCP specifies a closed exclusion list for bookkeeping fields, so that re-syncing a plan whose only
    change is its version does not change the hash. The fields below are the ones this agent itself
    writes; a fuller implementation should validate against the spec's published test vectors, and that
    is recorded as the remaining gap rather than presented as done.
    """
    bookkeeping = {"version", "updated_at", "created_at", "status"}
    preimage = {k: v for k, v in plan.items() if k not in bookkeeping}
    return hashlib.sha256(canonical_json(preimage)).hexdigest()


def _pem_from_secrets_manager(secret_id: str) -> str:
    """The signing key from Secrets Manager.

    The private key must not live in `agentcore.json`: that file is committed, and `envVars` in it are
    plain text. So the deployed runtime is given a secret *id* — which is not itself a secret — and
    fetches the material at startup.

    Imported here rather than at module scope so the local path, and the tests, need no boto3 call and
    no AWS credentials.
    """
    import boto3

    response = boto3.client("secretsmanager").get_secret_value(SecretId=secret_id)
    return response.get("SecretString") or ""


#: KMS signing algorithm for each alg this agent supports.
#:
#: Only ES256 is mapped. KMS *can* hold an Ed25519 key (`ECC_NIST_EDWARDS25519`), but its two Ed25519
#: signing algorithms take different `MessageType` values -- `ED25519_SHA_512` requires `RAW` while
#: `ED25519_PH_SHA_512` requires `DIGEST`, and the docs state they are not interchangeable. Rather than
#: half-implement that, Ed25519 stays on the local-PEM path and KMS is ES256 only. ES256 is also the
#: intersection of what AdCP verifiers accept and what AgentCore Identity's Private Key JWT supports,
#: so it is the spec that keeps the most doors open.
_KMS_SIGNING_ALGORITHM = {ALG_ES256: "ECDSA_SHA_256"}

#: Byte length of each half of a P-256 JOSE signature.
_P256_COORDINATE_BYTES = 32


def _kms_key_id() -> str:
    return os.environ.get("GOVERNANCE_SIGNING_KMS_KEY_ID", "").strip()


def _der_to_jose(der_signature: bytes) -> bytes:
    """Convert KMS's DER-encoded ECDSA signature to the raw ``R || S`` JOSE expects.

    This conversion is the whole reason a KMS signer is not a drop-in replacement for a local key.
    ``kms:Sign`` returns ECDSA signatures **DER-encoded**, while JWS requires the two integers
    concatenated as fixed-width big-endian bytes. Passing DER straight through produces a token that
    looks structurally valid, is the wrong length, and fails every verifier -- a silent-wrong-answer
    failure rather than an error at signing time.

    Each half is left-padded to exactly 32 bytes: DER omits leading zero bytes, so an r or s that
    happens to be small yields a short integer that must be padded back to full width. Roughly one
    signature in 256 has a short r or s, which is exactly often enough to pass a casual test and fail
    in production.
    """
    r, s = decode_dss_signature(der_signature)
    return r.to_bytes(_P256_COORDINATE_BYTES, "big") + s.to_bytes(_P256_COORDINATE_BYTES, "big")


def _sign_with_kms(*, alg: str, key_id: str, signature_base: bytes) -> bytes:
    """Sign via KMS. The private key never leaves KMS, and every call is recorded in CloudTrail."""
    kms_algorithm = _KMS_SIGNING_ALGORITHM.get(alg)
    if kms_algorithm is None:
        raise SigningNotConfigured(
            f"GOVERNANCE_SIGNING_ALG={alg!r} has no KMS signing algorithm mapped. Use {ALG_ES256} with "
            "KMS, or the local PEM path for other algorithms."
        )
    import boto3  # imported lazily so the local-PEM path needs no AWS SDK at all

    client = boto3.client("kms", region_name=region())
    response = client.sign(
        KeyId=key_id,
        Message=signature_base,
        # RAW, so KMS does the hashing. The JWS signing input is far below the 4096-byte RAW limit, and
        # letting KMS hash removes a place where our digest choice could drift from the algorithm's.
        MessageType="RAW",
        SigningAlgorithm=kms_algorithm,
    )
    return _der_to_jose(response["Signature"])


def public_jwk_from_kms(key_id: str | None = None) -> dict[str, Any]:
    """This agent's public key as a JWK, fetched from KMS.

    The reason KMS also settles the JWKS-publication question that was previously open: the public half
    is retrievable on demand, so publishing a JWKS is deriving a public artifact rather than handling a
    secret. `kms:GetPublicKey` returns DER-encoded SPKI, which is loaded and re-expressed as a JWK here.
    """
    import boto3
    from cryptography.hazmat.primitives.serialization import load_der_public_key

    resolved = (key_id or _kms_key_id()).strip()
    if not resolved:
        raise SigningNotConfigured("No KMS key id available for the public JWK.")
    client = boto3.client("kms", region_name=region())
    der = client.get_public_key(KeyId=resolved)["PublicKey"]
    public_key = load_der_public_key(der)
    numbers = public_key.public_numbers()
    return {
        "kty": "EC",
        "crv": "P-256",
        "x": b64url_encode(numbers.x.to_bytes(_P256_COORDINATE_BYTES, "big")),
        "y": b64url_encode(numbers.y.to_bytes(_P256_COORDINATE_BYTES, "big")),
        "use": "sig",
        "alg": "ES256",
        "kid": os.environ.get("GOVERNANCE_SIGNING_KID", "").strip(),
    }


def _load_signing_key() -> tuple[Any, str, str]:
    """The private key, its algorithm and its key id, for the LOCAL PEM path.

    Deployment uses KMS instead -- see `_signer()`. This path remains for local development and tests,
    where a key that never leaves the process is appropriate and no AWS call should be required.

    From Secrets Manager when `GOVERNANCE_SIGNING_SECRET_ID` is set, otherwise from
    `GOVERNANCE_SIGNING_KEY_PEM`. Never from a file baked into the image, and never generated on the
    fly: a key minted at startup would verify against nothing a counterparty had ever seen, so every
    token would be unverifiable while looking perfectly well formed.
    """
    secret_id = os.environ.get("GOVERNANCE_SIGNING_SECRET_ID", "").strip()
    pem = _pem_from_secrets_manager(secret_id) if secret_id else os.environ.get(
        "GOVERNANCE_SIGNING_KEY_PEM", ""
    )
    if not pem.strip():
        raise SigningNotConfigured(
            "No signing key available. Set GOVERNANCE_SIGNING_SECRET_ID (deployed) or "
            "GOVERNANCE_SIGNING_KEY_PEM (local). This agent will not issue unsigned governance "
            "context: an unverifiable token offers no guarantee, so it refuses to start."
        )
    key = load_private_key_pem(pem.encode("utf-8"))
    alg = os.environ.get("GOVERNANCE_SIGNING_ALG", ALG_ED25519)
    if alg not in (ALG_ED25519, ALG_ES256):
        raise SigningNotConfigured(
            f"GOVERNANCE_SIGNING_ALG={alg!r} is not one AdCP verifiers accept "
            f"({ALG_ED25519} or {ALG_ES256})."
        )
    kid = os.environ.get("GOVERNANCE_SIGNING_KID", "").strip()
    if not kid:
        raise SigningNotConfigured("GOVERNANCE_SIGNING_KID is not set; verifiers select keys by kid.")
    return key, alg, kid


def _validated_alg_and_kid() -> tuple[str, str]:
    """The algorithm and key id, validated, independent of where the key lives."""
    alg = os.environ.get("GOVERNANCE_SIGNING_ALG", ALG_ED25519)
    if alg not in (ALG_ED25519, ALG_ES256):
        raise SigningNotConfigured(
            f"GOVERNANCE_SIGNING_ALG={alg!r} is not one AdCP verifiers accept "
            f"({ALG_ED25519} or {ALG_ES256})."
        )
    kid = os.environ.get("GOVERNANCE_SIGNING_KID", "").strip()
    if not kid:
        raise SigningNotConfigured("GOVERNANCE_SIGNING_KID is not set; verifiers select keys by kid.")
    return alg, kid


def _signer() -> tuple[Any, str, str]:
    """Resolve how this process signs: KMS when configured, otherwise a local PEM.

    Returns ``(sign, alg, kid)`` where ``sign(signature_base) -> bytes``.

    KMS is preferred and is what a deployment uses. It is the pattern AWS itself documents for agent
    signing keys (AgentCore Identity's Private Key JWT authentication): the public key is published, the
    private key stays in KMS, `kms:Sign` is called per signature, and CloudTrail records each use. It
    also removes the failure mode that a PEM in an environment variable or a container layer invites.

    The local PEM path is kept rather than deleted because the unit tests must sign without AWS
    credentials, and because a developer running the agent locally should not need a KMS key. The
    selection is explicit -- presence of a KMS key id -- so it is never ambiguous which key signed a
    token, and startup validation covers whichever path is in use.
    """
    alg, kid = _validated_alg_and_kid()
    key_id = _kms_key_id()
    if key_id:
        def sign(signature_base: bytes) -> bytes:
            return _sign_with_kms(alg=alg, key_id=key_id, signature_base=signature_base)

        return sign, alg, kid

    key, pem_alg, pem_kid = _load_signing_key()

    def sign_local(signature_base: bytes) -> bytes:
        return sign_signature_base(alg=pem_alg, private_key=key, signature_base=signature_base)

    return sign_local, pem_alg, pem_kid


def issue_governance_context(
    *,
    plan_id: str,
    audience: str,
    phase: str,
    plan: dict[str, Any],
    issuer: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: int | None = None,
) -> str:
    """Mint a compact JWS carrying the claims a seller's 15-step check reads.

    ``sub`` is the plan id and ``aud`` the seller, so a token minted for one seller cannot be replayed
    at another — which is the property that makes forwarding it safe.
    """
    if phase not in PHASES:
        raise ValueError(f"phase must be one of {PHASES}, got {phase!r}")
    if not audience:
        # An audience-less token is replayable at any seller. Refused rather than issued.
        raise ValueError("a governance context must name the seller it is addressed to")

    sign, alg, kid = _signer()
    issued = int(time.time()) if now is None else now

    # AdCP's JWS profile requires the typed header value "adcp-gov+jws" (byte-for-byte, no
    # normalization) rather than a generic "JWT" -- the typed value is what stops a governance
    # signing key from being tricked into validating an unrelated JWT for a different purpose.
    # Found and fixed 2026-08-11: this had been "JWT" since the module was written, which would have
    # failed every seller's typ check the moment a seller implemented one (U3).
    #
    # `alg` MUST be the JWS wire-format name (_wire_alg), not this project's internal one -- see
    # _wire_alg's docstring for the identical-shaped bug found and fixed here on 2026-08-12.
    header = {"alg": _wire_alg(alg), "kid": kid, "typ": "adcp-gov+jws"}
    payload = {
        "iss": issuer,
        "sub": plan_id,
        "aud": audience,
        "iat": issued,
        "exp": issued + ttl_seconds,
        "jti": uuid.uuid4().hex,
        "phase": phase,
        # The audit-layer claim. Binds the token to the exact plan text that was evaluated, so a plan
        # amended after approval cannot be presented as the plan that was approved.
        "plan_hash": plan_hash(plan),
    }

    b64_header = b64url_encode(canonical_json(header))
    b64_payload = b64url_encode(canonical_json(payload))
    signing_input = f"{b64_header}.{b64_payload}".encode("ascii")
    signature = sign(signing_input)
    return f"{b64_header}.{b64_payload}.{b64url_encode(signature)}"


#: Spec floor: an issuer that declares next_update - updated < 60s is non-conformant (the SDK's own
#: revocation-list parser, adcp.signing.revocation_fetcher, rejects a declared cadence below this).
#: Kept in sync with adcp.signing.revocation_fetcher.MIN_POLLING_INTERVAL_SECONDS by import, not by
#: retyping the literal -- BR-U3-11's typ mismatch is exactly the failure mode a retyped constant
#: risks repeating.
from adcp.signing.revocation_fetcher import MIN_POLLING_INTERVAL_SECONDS, REVOCATION_LIST_TYP


def issue_revocation_list(
    *,
    issuer: str,
    revoked_kids: frozenset[str] = frozenset(),
    revoked_jtis: frozenset[str] = frozenset(),
    next_update_seconds: int = MIN_POLLING_INTERVAL_SECONDS,
    now: int | None = None,
) -> str:
    """Sign this agent's revocation list (BR-U3-2), reusing the SAME signer that issues
    governance_context tokens -- a new document under an existing key, not a new key.

    ``typ`` is ``adcp-gov-revocation+jws`` (the SDK's own REVOCATION_LIST_TYP constant, imported
    rather than retyped -- see the constant's own comment for why that matters here specifically).
    Sellers verify this document with a plain JWKS resolver pointed at this agent's published JWKS
    (the SAME one governance_context tokens verify against), NOT via brand.json indirection -- the
    revocation list's issuer IS this agent, so there is no third party's identity to resolve.

    ``next_update_seconds`` MUST be >= the spec floor (60s); the SDK's own parser
    (adcp.signing.revocation_fetcher._build_list_from_payload) rejects a declared cadence below that,
    so a caller passing a smaller value here would produce a document no conformant seller could
    accept -- this function does not silently clamp it, it is the caller's job to pass a valid value.
    """
    if next_update_seconds < MIN_POLLING_INTERVAL_SECONDS:
        raise ValueError(
            f"next_update_seconds={next_update_seconds} is below the spec floor of "
            f"{MIN_POLLING_INTERVAL_SECONDS}s; every conformant seller verifier will reject a list "
            "with a shorter declared cadence."
        )

    sign, alg, kid = _signer()
    issued = int(time.time()) if now is None else now
    updated_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(issued))
    next_update_iso = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(issued + next_update_seconds)
    )

    header = {"alg": _wire_alg(alg), "kid": kid, "typ": REVOCATION_LIST_TYP}
    payload = {
        "version": 1,
        "issuer": issuer,
        "updated": updated_iso,
        "next_update": next_update_iso,
        "revoked_kids": sorted(revoked_kids),
        "revoked_jtis": sorted(revoked_jtis),
    }

    b64_header = b64url_encode(canonical_json(header))
    b64_payload = b64url_encode(canonical_json(payload))
    signing_input = f"{b64_header}.{b64_payload}".encode("ascii")
    signature = sign(signing_input)
    return f"{b64_header}.{b64_payload}.{b64url_encode(signature)}"


def read_governance_context(token: str) -> dict[str, Any]:
    """Claims from a token WITHOUT verifying it.

    Named to say so. This agent correlates its own lifecycle by ``sub`` and ``phase``, and it issued the
    token itself, so reading is legitimate here — but a caller must never mistake this for verification,
    which is why it does not share a name with one.
    """
    _, b64_payload, _ = parse_compact_jws(token)
    return json.loads(b64url_decode(b64_payload))


def verify_governance_context(token: str, public_jwk: dict[str, Any]) -> dict[str, Any]:
    """Verify a token against a public JWK and return its claims.

    Present so the property this agent claims is actually testable, and so a seller implementation has a
    reference. Raises rather than returning a boolean: a caller that forgets to check a return value
    fails open, and there is no safe way to fail open here.
    """
    b64_header, b64_payload, signature = parse_compact_jws(token)
    header = json.loads(b64url_decode(b64_header))
    wire_alg = header.get("alg")
    # The header carries the JWS wire-format name ("EdDSA"/"ES256"), per RFC 7518 -- NOT this
    # project's internal name ("ed25519"/"ecdsa-p256-sha256"). Convert via the SDK's own mapping
    # (imported, not retyped) before calling verify_signature, which expects the internal name. See
    # _wire_alg's docstring for the bug this fixes: prior to 2026-08-12 this function checked the
    # header against ALG_ED25519/ALG_ES256 directly, which happened to "work" only because
    # issue_governance_context ALSO wrote the wrong (internal) name into the header -- both sides of
    # the mismatch agreed with each other while disagreeing with the spec and with the SDK's own
    # verify_jws_document, which is what a real seller uses.
    if wire_alg not in JWS_ALG_TO_INTERNAL:
        # Covers `alg: none` and every downgrade dressed as a header value.
        raise JwsMalformedError(f"unsupported alg: {wire_alg!r}")
    alg = JWS_ALG_TO_INTERNAL[wire_alg]
    typ = header.get("typ")
    if typ != "adcp-gov+jws":
        # Byte-for-byte per the spec, no normalization -- the typed value is what stops a governance
        # signing key from being tricked into validating a generic JWT for another purpose. A seller
        # verifier MUST reject on any other value; this reference verifier does the same rather than
        # being more lenient than the implementations it's meant to model.
        raise JwsMalformedError(f"unexpected typ: {typ!r}, expected 'adcp-gov+jws'")

    signing_input = f"{b64_header}.{b64_payload}".encode("ascii")
    if not verify_signature(
        alg=alg,
        public_key=public_key_from_jwk(public_jwk),
        signature_base=signing_input,
        signature=signature,
    ):
        raise JwsError("governance context signature does not verify")
    return json.loads(b64url_decode(b64_payload))
