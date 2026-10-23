"""Unit tests for governance_verification.py (U3, BR-U3-3 through BR-U3-9).

Real keypairs and real signing throughout -- mirrors the reference governance agent's own
test_jws.py `signing_env`/`issue()` fixture pattern, not hand-built JWS strings, since this module's
whole point is testing REAL cryptographic verification against the SDK.

DynamoDB access (jti replay ledger, persisted-doc cache) uses moto, same pattern as test_main.py.
Network fetches (brand.json, JWKS, revocation list, the outbound check_governance MCP call) are
never made in these tests -- each is monkeypatched or resolved via a static/in-memory substitute, so
this suite runs with no network access and no AWS credentials beyond moto's mock.
"""

from __future__ import annotations

from aws_region import region

import asyncio
import base64
import json
import time
from typing import Any

import boto3
import pytest
from adcp.signing.crypto import ALG_ES256, b64url_encode
from adcp.signing.jwks import StaticJwksResolver
from adcp.signing.keygen import generate_signing_keypair
from moto import mock_aws

import governance_verification as gv

TABLE_NAME = "adcp-reference-seller-state-test"
REGION = region()
THIS_SELLER = gv.THIS_SELLER_URL
# The governance agent's real `iss` value -- a bare origin (deploy_governance_origin.py), NOT the
# AgentCore invoke URL used as the outbound transport endpoint. Kept distinct in these tests on
# purpose; see test_revocation_check_uses_the_tokens_own_iss_not_the_configured_governance_agent_url.
GOVERNANCE_AGENT_URL = "https://d222222abcdef8.cloudfront.net"
PLAN_ID = "plan-1"


@pytest.fixture
def dynamo_table(monkeypatch):
    with mock_aws():
        client = boto3.client("dynamodb", region_name=REGION)
        client.create_table(
            TableName=TABLE_NAME,
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        monkeypatch.setenv("STATE_TABLE_NAME", TABLE_NAME)
        monkeypatch.setenv("AWS_REGION", REGION)
        resource = boto3.resource("dynamodb", region_name=REGION)
        yield resource.Table(TABLE_NAME)


@pytest.fixture(autouse=True)
def _clear_module_caches():
    """These resolvers are cached module-level per governance-agent URL (real production behavior --
    see governance_verification.py's own docstrings on why). Clear between tests so one test's
    monkeypatched resolver doesn't leak into the next."""
    gv._brand_jwks_resolvers.clear()
    gv._revocation_checkers.clear()
    yield
    gv._brand_jwks_resolvers.clear()
    gv._revocation_checkers.clear()


def _signing_keypair():
    # generate_signing_keypair's own alg vocabulary ("es256") differs from adcp.signing.crypto's
    # internal name (ALG_ES256 == "ecdsa-p256-sha256") -- same shape distinction as the wire-format
    # vs internal alg names fixed in the governance agent's jws.py this session.
    private_pem, public_jwk = generate_signing_keypair(alg="es256")
    return private_pem, public_jwk


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sign_token(
    private_pem: bytes,
    public_jwk: dict[str, Any],
    *,
    aud: str = THIS_SELLER,
    sub: str = PLAN_ID,
    phase: str = "intent",
    alg_header: str = "ES256",
    typ: str = "adcp-gov+jws",
    exp_delta: int = 900,
    jti: str = "jti-1",
) -> str:
    """Build and sign a real governance_context-shaped token with an arbitrary claim set -- lets
    tests exercise each BR-U3-3 failure branch by varying exactly one claim/header field."""
    from adcp.signing.crypto import load_private_key_pem, sign_signature_base

    key = load_private_key_pem(private_pem)
    header = {"alg": alg_header, "kid": public_jwk["kid"], "typ": typ}
    now = int(time.time())
    payload = {
        "iss": GOVERNANCE_AGENT_URL,
        "sub": sub,
        "aud": aud,
        "iat": now,
        "exp": now + exp_delta,
        "jti": jti,
        "phase": phase,
        "plan_hash": "irrelevant-for-seller-verification",
    }
    b64_header = b64url_encode(_canonical_json(header))
    b64_payload = b64url_encode(_canonical_json(payload))
    signing_input = f"{b64_header}.{b64_payload}".encode("ascii")
    signature = sign_signature_base(alg=ALG_ES256, private_key=key, signature_base=signing_input)
    return f"{b64_header}.{b64_payload}.{b64url_encode(signature)}"


def _resolver_stub(public_jwk: dict[str, Any]):
    """A BrandJsonJwksResolver-shaped stub that skips the real brand.json/JWKS network walk and
    resolves directly to the given JWK -- used to isolate BR-U3-3's claim-comparison logic (steps
    6-10) from steps 3-5's resolution machinery, which is exercised separately."""

    class _Stub:
        async def __call__(self, kid: str):
            return public_jwk if kid == public_jwk["kid"] else None

    return _Stub()


class TestVerifyIntentToken:
    def test_a_well_formed_correctly_signed_matching_token_is_valid(self, dynamo_table, monkeypatch):
        private_pem, public_jwk = _signing_keypair()
        token = _sign_token(private_pem, public_jwk)
        monkeypatch.setattr(gv, "_brand_jwks_resolver_for", lambda url: _resolver_stub(public_jwk))
        monkeypatch.setattr(
            gv,
            "_revocation_checker_for",
            lambda issuer, url: _NeverRevokedChecker(),
        )

        result = asyncio.run(
            gv.verify_intent_token(
                token,
                plan_id=PLAN_ID,
                this_seller_url=THIS_SELLER,
                governance_agent_url=GOVERNANCE_AGENT_URL,
            )
        )
        assert result.valid is True
        assert result.claims["sub"] == PLAN_ID

    def test_malformed_input_is_rejected(self, dynamo_table):
        result = asyncio.run(
            gv.verify_intent_token(
                "not-a-real-token",
                plan_id=PLAN_ID,
                this_seller_url=THIS_SELLER,
                governance_agent_url=GOVERNANCE_AGENT_URL,
            )
        )
        assert result.valid is False
        assert result.failure_reason == "malformed"

    def test_revocation_check_uses_the_tokens_own_iss_not_the_configured_governance_agent_url(
        self, dynamo_table, monkeypatch
    ):
        """Real bug, found live (U3 verification, 2026-08-12): `governance_agent_url` is the
        AgentCore invoke URL this seller calls check_governance at
        (`https://bedrock-agentcore.../runtimes/.../invocations`) -- a transport endpoint, NOT the
        agent's cryptographic identity. The revocation list lives at `{origin of the token's
        VERIFIED iss claim}/.well-known/governance-revocations.json` per spec. Using
        governance_agent_url for that lookup 404'd on every live execution check. Pinned here with
        deliberately DIFFERENT values for iss and governance_agent_url, unlike every other test in
        this file where they coincide and so could not have caught this."""
        private_pem, public_jwk = _signing_keypair()
        token = _sign_token(private_pem, public_jwk)  # iss = GOVERNANCE_AGENT_URL (the constant)
        distinct_invoke_url = (
            "https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/"
            "arn%3Aaws%3Abedrock-agentcore%3Aus-east-1%3A123456789012%3Aruntime%2F"
            "RefGovernance_adcpRefGovernance-riyqeEFLEG/invocations?qualifier=DEFAULT"
        )
        monkeypatch.setattr(gv, "_brand_jwks_resolver_for", lambda url: _resolver_stub(public_jwk))

        seen: list[tuple[str, str]] = []

        def fake_revocation_checker_for(issuer, governance_agent_url):
            seen.append((issuer, governance_agent_url))
            return _NeverRevokedChecker()

        monkeypatch.setattr(gv, "_revocation_checker_for", fake_revocation_checker_for)

        result = asyncio.run(
            gv.verify_intent_token(
                token,
                plan_id=PLAN_ID,
                this_seller_url=THIS_SELLER,
                governance_agent_url=distinct_invoke_url,
            )
        )
        assert result.valid is True
        assert seen == [(GOVERNANCE_AGENT_URL, distinct_invoke_url)]

    def test_disallowed_alg_is_rejected(self, dynamo_table, monkeypatch):
        private_pem, public_jwk = _signing_keypair()
        token = _sign_token(private_pem, public_jwk, alg_header="HS256")
        monkeypatch.setattr(gv, "_brand_jwks_resolver_for", lambda url: _resolver_stub(public_jwk))

        result = asyncio.run(
            gv.verify_intent_token(
                token,
                plan_id=PLAN_ID,
                this_seller_url=THIS_SELLER,
                governance_agent_url=GOVERNANCE_AGENT_URL,
            )
        )
        assert result.valid is False
        assert result.failure_reason == "signature_invalid"

    def test_wrong_typ_is_rejected(self, dynamo_table, monkeypatch):
        private_pem, public_jwk = _signing_keypair()
        token = _sign_token(private_pem, public_jwk, typ="JWT")
        monkeypatch.setattr(gv, "_brand_jwks_resolver_for", lambda url: _resolver_stub(public_jwk))

        result = asyncio.run(
            gv.verify_intent_token(
                token,
                plan_id=PLAN_ID,
                this_seller_url=THIS_SELLER,
                governance_agent_url=GOVERNANCE_AGENT_URL,
            )
        )
        assert result.valid is False
        assert result.failure_reason == "signature_invalid"

    def test_a_token_signed_by_a_different_key_is_rejected(self, dynamo_table, monkeypatch):
        private_pem, public_jwk = _signing_keypair()
        _, other_public_jwk = _signing_keypair()
        token = _sign_token(private_pem, public_jwk)
        # Resolver returns a DIFFERENT key than the one that actually signed -- simulates a forged
        # kid or a resolver returning stale/wrong key material.
        monkeypatch.setattr(
            gv, "_brand_jwks_resolver_for", lambda url: _resolver_stub(other_public_jwk)
        )

        result = asyncio.run(
            gv.verify_intent_token(
                token,
                plan_id=PLAN_ID,
                this_seller_url=THIS_SELLER,
                governance_agent_url=GOVERNANCE_AGENT_URL,
            )
        )
        assert result.valid is False

    def test_an_expired_token_is_rejected(self, dynamo_table, monkeypatch):
        private_pem, public_jwk = _signing_keypair()
        token = _sign_token(private_pem, public_jwk, exp_delta=-900)
        monkeypatch.setattr(gv, "_brand_jwks_resolver_for", lambda url: _resolver_stub(public_jwk))

        result = asyncio.run(
            gv.verify_intent_token(
                token,
                plan_id=PLAN_ID,
                this_seller_url=THIS_SELLER,
                governance_agent_url=GOVERNANCE_AGENT_URL,
            )
        )
        assert result.valid is False
        assert result.failure_reason == "expired"

    def test_wrong_audience_is_rejected(self, dynamo_table, monkeypatch):
        private_pem, public_jwk = _signing_keypair()
        token = _sign_token(private_pem, public_jwk, aud="https://a-different-seller.example/mcp")
        monkeypatch.setattr(gv, "_brand_jwks_resolver_for", lambda url: _resolver_stub(public_jwk))

        result = asyncio.run(
            gv.verify_intent_token(
                token,
                plan_id=PLAN_ID,
                this_seller_url=THIS_SELLER,
                governance_agent_url=GOVERNANCE_AGENT_URL,
            )
        )
        assert result.valid is False
        assert result.failure_reason == "wrong_audience"

    def test_wrong_subject_is_rejected(self, dynamo_table, monkeypatch):
        private_pem, public_jwk = _signing_keypair()
        token = _sign_token(private_pem, public_jwk, sub="a-different-plan")
        monkeypatch.setattr(gv, "_brand_jwks_resolver_for", lambda url: _resolver_stub(public_jwk))

        result = asyncio.run(
            gv.verify_intent_token(
                token,
                plan_id=PLAN_ID,
                this_seller_url=THIS_SELLER,
                governance_agent_url=GOVERNANCE_AGENT_URL,
            )
        )
        assert result.valid is False
        assert result.failure_reason == "wrong_subject"

    def test_wrong_phase_is_rejected(self, dynamo_table, monkeypatch):
        private_pem, public_jwk = _signing_keypair()
        token = _sign_token(private_pem, public_jwk, phase="purchase")
        monkeypatch.setattr(gv, "_brand_jwks_resolver_for", lambda url: _resolver_stub(public_jwk))

        result = asyncio.run(
            gv.verify_intent_token(
                token,
                plan_id=PLAN_ID,
                this_seller_url=THIS_SELLER,
                governance_agent_url=GOVERNANCE_AGENT_URL,
            )
        )
        assert result.valid is False
        assert result.failure_reason == "wrong_phase"

    def test_a_revoked_kid_is_rejected(self, dynamo_table, monkeypatch):
        private_pem, public_jwk = _signing_keypair()
        token = _sign_token(private_pem, public_jwk)
        monkeypatch.setattr(gv, "_brand_jwks_resolver_for", lambda url: _resolver_stub(public_jwk))
        monkeypatch.setattr(gv, "_revocation_checker_for", lambda issuer, url: _AlwaysRevokedChecker())

        result = asyncio.run(
            gv.verify_intent_token(
                token,
                plan_id=PLAN_ID,
                this_seller_url=THIS_SELLER,
                governance_agent_url=GOVERNANCE_AGENT_URL,
            )
        )
        assert result.valid is False
        assert result.failure_reason == "revoked"

    def test_a_replayed_jti_is_rejected_on_the_second_use(self, dynamo_table, monkeypatch):
        private_pem, public_jwk = _signing_keypair()
        token = _sign_token(private_pem, public_jwk, jti="replay-me")
        monkeypatch.setattr(gv, "_brand_jwks_resolver_for", lambda url: _resolver_stub(public_jwk))
        monkeypatch.setattr(gv, "_revocation_checker_for", lambda issuer, url: _NeverRevokedChecker())

        first = asyncio.run(
            gv.verify_intent_token(
                token,
                plan_id=PLAN_ID,
                this_seller_url=THIS_SELLER,
                governance_agent_url=GOVERNANCE_AGENT_URL,
            )
        )
        second = asyncio.run(
            gv.verify_intent_token(
                token,
                plan_id=PLAN_ID,
                this_seller_url=THIS_SELLER,
                governance_agent_url=GOVERNANCE_AGENT_URL,
            )
        )
        assert first.valid is True
        assert second.valid is False
        assert second.failure_reason == "replayed"


class _NeverRevokedChecker:
    async def __call__(self, kid: str) -> bool:
        return False

    async def is_jti_revoked(self, jti: str) -> bool:
        return False


class _AlwaysRevokedChecker:
    async def __call__(self, kid: str) -> bool:
        return True

    async def is_jti_revoked(self, jti: str) -> bool:
        return False


class TestJtiFirstSeen:
    def test_the_first_call_for_a_jti_returns_true(self, dynamo_table):
        assert gv.jti_first_seen("jti-a", int(time.time()) + 900) is True

    def test_a_second_call_for_the_same_jti_returns_false(self, dynamo_table):
        exp = int(time.time()) + 900
        assert gv.jti_first_seen("jti-b", exp) is True
        assert gv.jti_first_seen("jti-b", exp) is False


class TestBuildPlannedDelivery:
    def test_only_the_four_honest_fields_are_present(self, dynamo_table):
        delivery = gv.build_planned_delivery(
            [{"budget": 500.0}, {"budget": 250.0}],
            currency="USD",
            start_time="2026-01-01T00:00:00Z",
            end_time="2026-01-31T23:59:59Z",
        )
        assert set(delivery.keys()) == {"total_budget", "currency", "start_time", "end_time"}
        assert delivery["total_budget"] == 750.0
        assert delivery["currency"] == "USD"

    def test_booked_product_channels_are_stated_when_supplied(self, dynamo_table):
        # The booked products' own declared channels are real inventory data, so planned_delivery
        # states them and the governance channel_compliance check can run. Geo stays absent.
        delivery = gv.build_planned_delivery(
            [{"budget": 500.0}],
            currency="USD",
            start_time="2026-01-01T00:00:00Z",
            end_time="2026-01-31T23:59:59Z",
            channels=["podcast"],
        )
        assert delivery["channels"] == ["podcast"]
        assert "geo" not in delivery

    def test_empty_channels_are_omitted_not_sent_empty(self, dynamo_table):
        delivery = gv.build_planned_delivery(
            [{"budget": 500.0}],
            currency="USD",
            start_time="2026-01-01T00:00:00Z",
            end_time="2026-01-31T23:59:59Z",
            channels=[],
        )
        assert "channels" not in delivery


class TestPersistedDocCache:
    def test_a_cold_start_with_no_persisted_entry_fetches_live_and_then_writes_one(
        self, dynamo_table, monkeypatch
    ):
        calls = []

        async def fake_live_fetch(uri, *, allow_private=False):
            calls.append(uri)
            return {"keys": [{"kid": "k1"}]}

        monkeypatch.setattr(gv, "async_default_jwks_fetcher", fake_live_fetch)
        result = asyncio.run(gv._persisted_bootstrap_jwks_fetcher("https://example/jwks.json"))
        assert result == {"keys": [{"kid": "k1"}]}
        assert calls == ["https://example/jwks.json"]
        assert gv.get_persisted_doc("jwks", "https://example/jwks.json") is not None

    def test_a_fresh_persisted_entry_is_used_without_a_live_fetch(self, dynamo_table, monkeypatch):
        gv.put_persisted_doc("jwks", "https://example/jwks.json", {"keys": [{"kid": "cached"}]})
        calls = []

        async def fake_live_fetch(uri, *, allow_private=False):
            calls.append(uri)
            return {"keys": [{"kid": "should-not-be-used"}]}

        monkeypatch.setattr(gv, "async_default_jwks_fetcher", fake_live_fetch)
        result = asyncio.run(gv._persisted_bootstrap_jwks_fetcher("https://example/jwks.json"))
        assert result == {"keys": [{"kid": "cached"}]}
        assert calls == []

    def test_a_stale_persisted_entry_triggers_a_live_fetch(self, dynamo_table, monkeypatch):
        from datetime import datetime, timedelta, timezone

        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        gv._table().put_item(
            Item={
                "pk": "GOVDOC#jwks#https://example/jwks.json",
                "sk": "META",
                "fetched_at": old,
                "document": {"keys": [{"kid": "stale"}]},
            }
        )
        calls = []

        async def fake_live_fetch(uri, *, allow_private=False):
            calls.append(uri)
            return {"keys": [{"kid": "fresh"}]}

        monkeypatch.setattr(gv, "async_default_jwks_fetcher", fake_live_fetch)
        result = asyncio.run(gv._persisted_bootstrap_jwks_fetcher("https://example/jwks.json"))
        assert result == {"keys": [{"kid": "fresh"}]}
        assert calls == ["https://example/jwks.json"]
