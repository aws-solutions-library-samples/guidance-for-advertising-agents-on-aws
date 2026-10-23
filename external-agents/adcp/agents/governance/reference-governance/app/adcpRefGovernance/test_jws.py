"""Tests for the signed governance context.

Round-trip plus tamper, which is PBT-02's shape applied to a security boundary: an invertible operation
tested by inverting it, and then tested by proving it does NOT invert when the bytes change.

The negative cases carry the weight. A signer that produces a token nobody can verify still looks
correct from the inside, and every failure here is one a counterparty would discover instead of us.
"""

from __future__ import annotations

import json

import pytest
from adcp.signing.crypto import ALG_ED25519, b64url_decode, b64url_encode
from adcp.signing.jws import JwsError, JwsMalformedError
from adcp.signing.keygen import generate_signing_keypair

import jws


PLAN = {
    "plan_id": "plan-1",
    "budget": {"total": 100000.0, "currency": "USD"},
    "flight": {"start_date": "2026-12-05", "end_date": "2026-12-10"},
}


@pytest.fixture()
def signing_env(monkeypatch):
    """A real Ed25519 keypair from the SDK's own keygen, so the algorithms cannot disagree."""
    # `generate_signing_keypair` returns (private_pem_bytes, public_jwk).
    private_pem, public_jwk = generate_signing_keypair(alg=ALG_ED25519)
    monkeypatch.setenv("GOVERNANCE_SIGNING_KEY_PEM", private_pem.decode("utf-8"))
    monkeypatch.setenv("GOVERNANCE_SIGNING_ALG", ALG_ED25519)
    monkeypatch.setenv("GOVERNANCE_SIGNING_KID", public_jwk["kid"])
    return {"private_pem": private_pem, "public_jwk": public_jwk}


def issue(**overrides):
    kwargs = {
        "plan_id": "plan-1",
        "audience": "https://triton-seller.example/adcp/mcp",
        "phase": "intent",
        "plan": PLAN,
        "issuer": "https://governance.example/adcp/mcp",
    }
    kwargs.update(overrides)
    return jws.issue_governance_context(**kwargs)


def test_a_token_verifies_against_the_public_jwk(signing_env):
    token = issue()
    claims = jws.verify_governance_context(token, signing_env["public_jwk"])
    assert claims["sub"] == "plan-1"
    assert claims["phase"] == "intent"
    assert claims["aud"] == "https://triton-seller.example/adcp/mcp"


def test_it_carries_the_claims_a_seller_checks(signing_env):
    claims = jws.read_governance_context(issue())
    # The 15-step seller checklist reads these. A missing one fails at the counterparty, not here.
    for claim in ("iss", "sub", "aud", "iat", "exp", "jti", "phase", "plan_hash"):
        assert claim in claims, f"{claim} missing"
    assert claims["exp"] > claims["iat"]


def test_a_tampered_payload_does_not_verify(signing_env):
    token = issue()
    header, payload, signature = token.split(".")
    claims = json.loads(b64url_decode(payload))
    # Raise the plan's authority after approval — the attack this signature exists to stop.
    claims["sub"] = "plan-someone-elses"
    forged = f"{header}.{b64url_encode(jws.canonical_json(claims))}.{signature}"
    with pytest.raises(JwsError):
        jws.verify_governance_context(forged, signing_env["public_jwk"])


def test_a_token_from_another_key_does_not_verify(signing_env):
    token = issue()
    _, other_jwk = generate_signing_keypair(alg=ALG_ED25519)
    with pytest.raises(JwsError):
        jws.verify_governance_context(token, other_jwk)


def test_the_alg_header_is_the_jws_wire_format_name_not_the_internal_one(signing_env):
    # Regression pin: from when this module was first written until this test was added
    # (2026-08-12), the header carried this project's INTERNAL alg name ("ed25519") instead of the
    # RFC 7518 wire name ("EdDSA") the AdCP JWS profile and adcp.signing.jws.verify_jws_document
    # require. Found while adding issue_revocation_list, when the SDK's own verifier -- which is what
    # a real seller uses -- rejected a freshly issued token as JwsMalformedError. This agent's own
    # verify_governance_context never caught it because it checked the header against this project's
    # ALG_ED25519 constant directly, the same wrong vocabulary the signer wrote -- so signer and
    # (hand-rolled) verifier agreed with each other while disagreeing with every real seller.
    header, _, _ = issue().split(".")
    assert json.loads(b64url_decode(header))["alg"] == "EdDSA"


def test_a_real_seller_side_verifier_accepts_the_alg_header(signing_env):
    from adcp.signing.jws import verify_jws_document
    from adcp.signing.jwks import StaticJwksResolver

    token = issue()
    resolver = StaticJwksResolver({"keys": [signing_env["public_jwk"]]})
    payload = verify_jws_document(token, jwks_resolver=resolver, expected_typ="adcp-gov+jws")
    assert payload["sub"] == "plan-1"


def test_the_typed_header_is_the_spec_value_not_a_generic_jwt(signing_env):
    # Regression pin: this header was "JWT" from when this module was first written until this test was
    # added (2026-08-11), found while designing the seller-side verifier that reads it. A seller
    # checking the spec's required "adcp-gov+jws" value would have rejected every token this agent ever
    # issued.
    header, _, _ = issue().split(".")
    assert json.loads(b64url_decode(header))["typ"] == "adcp-gov+jws"


def test_a_generic_jwt_typ_is_refused(signing_env):
    token = issue()
    _, payload, signature = token.split(".")
    wrong_typ_header = b64url_encode(jws.canonical_json({"alg": ALG_ED25519, "typ": "JWT"}))
    with pytest.raises(JwsMalformedError):
        jws.verify_governance_context(
            f"{wrong_typ_header}.{payload}.{signature}", signing_env["public_jwk"]
        )


def test_alg_none_is_refused(signing_env):
    token = issue()
    _, payload, signature = token.split(".")
    downgraded_header = b64url_encode(jws.canonical_json({"alg": "none", "typ": "JWT"}))
    with pytest.raises(JwsMalformedError):
        jws.verify_governance_context(
            f"{downgraded_header}.{payload}.{signature}", signing_env["public_jwk"]
        )


def test_it_refuses_to_start_without_a_key(monkeypatch):
    # A downgrade to unsigned would leave the agent with no security property while still answering.
    monkeypatch.delenv("GOVERNANCE_SIGNING_KEY_PEM", raising=False)
    with pytest.raises(jws.SigningNotConfigured):
        issue()


def test_it_refuses_an_algorithm_verifiers_reject(signing_env, monkeypatch):
    monkeypatch.setenv("GOVERNANCE_SIGNING_ALG", "RS256")
    with pytest.raises(jws.SigningNotConfigured):
        issue()


def test_it_refuses_a_token_with_no_audience(signing_env):
    # An audience-less token is replayable at any seller.
    with pytest.raises(ValueError):
        issue(audience="")


def test_it_refuses_an_unknown_phase(signing_env):
    with pytest.raises(ValueError):
        issue(phase="whenever")


def test_plan_hash_is_stable_across_identical_plans():
    assert jws.plan_hash(dict(PLAN)) == jws.plan_hash(dict(PLAN))


def test_plan_hash_ignores_key_order():
    reordered = {k: PLAN[k] for k in reversed(list(PLAN))}
    assert jws.plan_hash(reordered) == jws.plan_hash(PLAN)


def test_plan_hash_ignores_bookkeeping_but_not_substance():
    # Re-syncing a plan whose only change is its version must not change the hash...
    assert jws.plan_hash({**PLAN, "version": 4}) == jws.plan_hash(PLAN)
    # ...while a change to the authority it grants must.
    raised = {**PLAN, "budget": {"total": 999999.0, "currency": "USD"}}
    assert jws.plan_hash(raised) != jws.plan_hash(PLAN)


def test_each_token_is_uniquely_identified(signing_env):
    # `jti` is what lets an auditor tell two approvals of the same plan apart.
    assert jws.read_governance_context(issue())["jti"] != jws.read_governance_context(issue())["jti"]


def test_reading_is_not_verifying(signing_env):
    # `read_governance_context` must not be mistaken for a check. Asserted by showing it succeeds on a
    # token whose signature is nonsense.
    header, payload, _ = issue().split(".")
    junk = f"{header}.{payload}.{b64url_encode(b'not-a-signature')}"
    assert jws.read_governance_context(junk)["sub"] == "plan-1"
    with pytest.raises(JwsError):
        jws.verify_governance_context(junk, signing_env["public_jwk"])


class TestIssueRevocationList:
    """BR-U3-2: the revocation list is signed with the SAME key that signs governance_context, and
    verifies against the SDK's own revocation-list JWS contract -- not a hand-checked shape."""

    def test_verifies_with_the_sdks_own_verifier_and_typ(self, signing_env):
        from adcp.signing.jws import verify_jws_document
        from adcp.signing.jwks import StaticJwksResolver
        from adcp.signing.revocation_fetcher import REVOCATION_LIST_TYP

        token = jws.issue_revocation_list(issuer="https://governance.example/adcp/mcp")

        resolver = StaticJwksResolver({"keys": [signing_env["public_jwk"]]})
        payload = verify_jws_document(
            token, jwks_resolver=resolver, expected_typ=REVOCATION_LIST_TYP
        )
        assert payload["issuer"] == "https://governance.example/adcp/mcp"
        assert payload["revoked_kids"] == []
        assert payload["revoked_jtis"] == []

    def test_builds_a_valid_revocationlist_via_the_sdks_own_parser(self, signing_env):
        from adcp.signing.jws import verify_jws_document
        from adcp.signing.jwks import StaticJwksResolver
        from adcp.signing.revocation import RevocationList
        from adcp.signing.revocation_fetcher import REVOCATION_LIST_TYP

        token = jws.issue_revocation_list(
            issuer="https://governance.example/adcp/mcp",
            revoked_kids=frozenset({"old-key-2025"}),
            revoked_jtis=frozenset({"abc123"}),
        )
        resolver = StaticJwksResolver({"keys": [signing_env["public_jwk"]]})
        payload = verify_jws_document(
            token, jwks_resolver=resolver, expected_typ=REVOCATION_LIST_TYP
        )
        revocation_list = RevocationList.from_dict(payload)
        assert revocation_list.is_revoked("old-key-2025")
        assert not revocation_list.is_revoked("some-other-key")
        assert "abc123" in revocation_list.revoked_jtis

    def test_refuses_a_cadence_below_the_spec_floor(self, signing_env):
        with pytest.raises(ValueError, match="spec floor"):
            jws.issue_revocation_list(
                issuer="https://governance.example/adcp/mcp", next_update_seconds=30
            )

    def test_default_cadence_is_exactly_the_spec_floor(self, signing_env):
        from adcp.signing.jws import verify_jws_document
        from adcp.signing.jwks import StaticJwksResolver
        from adcp.signing.revocation_fetcher import (
            MIN_POLLING_INTERVAL_SECONDS,
            REVOCATION_LIST_TYP,
        )

        fixed_now = 1_800_000_000
        token = jws.issue_revocation_list(
            issuer="https://governance.example/adcp/mcp", now=fixed_now
        )
        resolver = StaticJwksResolver({"keys": [signing_env["public_jwk"]]})
        payload = verify_jws_document(
            token, jwks_resolver=resolver, expected_typ=REVOCATION_LIST_TYP
        )
        import time as _time

        updated = _time.strptime(payload["updated"], "%Y-%m-%dT%H:%M:%SZ")
        next_update = _time.strptime(payload["next_update"], "%Y-%m-%dT%H:%M:%SZ")
        delta = _time.mktime(next_update) - _time.mktime(updated)
        assert delta == MIN_POLLING_INTERVAL_SECONDS
