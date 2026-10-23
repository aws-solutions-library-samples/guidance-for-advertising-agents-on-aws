"""The KMS signing path, and the DER-to-JOSE conversion it depends on.

Why KMS at all: it is the pattern AWS documents for agent signing keys (AgentCore Identity's Private Key
JWT authentication) -- public key published, private key never leaving KMS, `kms:Sign` per signature,
CloudTrail recording each use. It also settles the JWKS-publication question, because the public half is
retrievable with `kms:GetPublicKey` rather than being something we must handle as a secret.

Why the conversion needs its own tests: `kms:Sign` returns ECDSA signatures **DER-encoded**, while JWS
requires raw ``R || S`` at fixed width. Getting that wrong produces a token that is structurally valid,
the wrong length, and rejected by every verifier -- a silent wrong answer rather than a signing error.

The padding case is the one that matters. DER omits leading zero bytes, so a small r or s yields a short
integer that must be re-padded to 32 bytes. That happens for roughly one signature in 256, which is
often enough to pass a handful of manual checks and fail once it is live.

No AWS credentials are used: KMS is exercised through a stub that returns real DER signatures produced by
a locally generated key, so the conversion and the wiring are both tested for real.
"""

from __future__ import annotations

import os

import pytest
from adcp.signing.crypto import (
    ALG_ED25519,
    ALG_ES256,
    b64url_decode,
    encode_dss_signature,
    verify_signature,
)
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

import jws


@pytest.fixture()
def p256_key():
    return ec.generate_private_key(ec.SECP256R1())


def _clear_signing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "GOVERNANCE_SIGNING_KMS_KEY_ID",
        "GOVERNANCE_SIGNING_SECRET_ID",
        "GOVERNANCE_SIGNING_KEY_PEM",
        "GOVERNANCE_SIGNING_ALG",
        "GOVERNANCE_SIGNING_KID",
    ):
        monkeypatch.delenv(name, raising=False)


class TestDerToJose:
    def test_produces_the_fixed_64_byte_width_jose_requires(self, p256_key):
        der = p256_key.sign(b"payload", ec.ECDSA(hashes.SHA256()))
        assert len(jws._der_to_jose(der)) == 64

    def test_converted_signature_actually_verifies(self, p256_key):
        # The end-to-end property: a DER signature, once converted, is accepted by the SDK's own
        # verifier. This is what proves the conversion, not the length alone.
        base = b"eyJhbGciOiJFUzI1NiJ9.eyJzdWIiOiJwbGFuLTEifQ"
        der = p256_key.sign(base, ec.ECDSA(hashes.SHA256()))
        assert verify_signature(
            alg=ALG_ES256,
            public_key=p256_key.public_key(),
            signature_base=base,
            signature=jws._der_to_jose(der),
        ) is True

    @pytest.mark.parametrize(
        "r, s",
        [
            (1, 1),  # both minimal: DER encodes each in one byte
            (2**248, 2**248),  # r and s short by a byte
            (2**255 - 1, 1),  # s minimal, r nearly full width
            (1, 2**255 - 1),  # the mirror case
        ],
    )
    def test_pads_short_integers_back_to_full_width(self, r, s):
        # The failure this guards. DER drops leading zeros, so a small r or s is shorter than 32 bytes
        # and must be left-padded. Without padding the halves misalign and the signature is garbage.
        jose = jws._der_to_jose(encode_dss_signature(r, s))
        assert len(jose) == 64
        assert int.from_bytes(jose[:32], "big") == r
        assert int.from_bytes(jose[32:], "big") == s

    def test_round_trips_every_signature_from_a_run_of_signings(self, p256_key):
        # Signs repeatedly so a short r or s is hit in practice rather than only in the crafted cases
        # above. Each one must verify.
        base = b"repeated-signing-base"
        for _ in range(64):
            der = p256_key.sign(base, ec.ECDSA(hashes.SHA256()))
            assert verify_signature(
                alg=ALG_ES256,
                public_key=p256_key.public_key(),
                signature_base=base,
                signature=jws._der_to_jose(der),
            ) is True


class TestSignWithKms:
    def test_signs_through_kms_and_the_token_verifies(self, monkeypatch, p256_key):
        """A full token minted via the KMS path, verified against the matching public key."""
        _clear_signing_env(monkeypatch)
        monkeypatch.setenv("GOVERNANCE_SIGNING_KMS_KEY_ID", "arn:aws:kms:us-east-1:1:key/abc")
        monkeypatch.setenv("GOVERNANCE_SIGNING_ALG", ALG_ES256)
        monkeypatch.setenv("GOVERNANCE_SIGNING_KID", "gov-2026-08")

        calls: list[dict] = []

        class StubKms:
            def sign(self, **kwargs):
                calls.append(kwargs)
                der = p256_key.sign(kwargs["Message"], ec.ECDSA(hashes.SHA256()))
                return {"Signature": der}

        monkeypatch.setattr(jws, "_kms_client_for_test", None, raising=False)
        import boto3

        monkeypatch.setattr(boto3, "client", lambda *a, **k: StubKms())

        token = jws.issue_governance_context(
            plan_id="plan-1",
            audience="seller-x",
            phase="intent",
            plan={"plan_id": "plan-1", "brand": "acme"},
            issuer="https://governance.example/agent",
        )

        header_b64, payload_b64, signature_b64 = token.split(".")
        assert verify_signature(
            alg=ALG_ES256,
            public_key=p256_key.public_key(),
            signature_base=f"{header_b64}.{payload_b64}".encode("ascii"),
            signature=b64url_decode(signature_b64),
        ) is True

        # RAW, so KMS hashes. Asserted because switching to DIGEST silently changes what must be sent.
        assert calls[0]["MessageType"] == "RAW"
        assert calls[0]["SigningAlgorithm"] == "ECDSA_SHA_256"
        assert calls[0]["KeyId"] == "arn:aws:kms:us-east-1:1:key/abc"

    def test_refuses_ed25519_over_kms_rather_than_guessing_a_message_type(self, monkeypatch):
        # KMS can hold an Ed25519 key, but its two Ed25519 algorithms take different MessageType values
        # and the docs say they are not interchangeable. Guessing would produce signatures that
        # fail verification for a reason no error message would explain.
        _clear_signing_env(monkeypatch)
        monkeypatch.setenv("GOVERNANCE_SIGNING_KMS_KEY_ID", "arn:aws:kms:us-east-1:1:key/abc")
        monkeypatch.setenv("GOVERNANCE_SIGNING_ALG", ALG_ED25519)
        monkeypatch.setenv("GOVERNANCE_SIGNING_KID", "gov-2026-08")
        with pytest.raises(jws.SigningNotConfigured, match="no KMS signing algorithm"):
            jws._sign_with_kms(
                alg=ALG_ED25519, key_id="arn:aws:kms:us-east-1:1:key/abc", signature_base=b"x"
            )


class TestSignerSelection:
    def test_prefers_kms_when_a_key_id_is_present(self, monkeypatch):
        _clear_signing_env(monkeypatch)
        monkeypatch.setenv("GOVERNANCE_SIGNING_KMS_KEY_ID", "arn:aws:kms:us-east-1:1:key/abc")
        monkeypatch.setenv("GOVERNANCE_SIGNING_ALG", ALG_ES256)
        monkeypatch.setenv("GOVERNANCE_SIGNING_KID", "gov-2026-08")
        # Resolves without a PEM present, which is the point: deployment needs no local key material.
        sign, alg, kid = jws._signer()
        assert callable(sign)
        assert (alg, kid) == (ALG_ES256, "gov-2026-08")

    def test_falls_back_to_the_local_pem_when_no_kms_key_is_configured(self, monkeypatch, p256_key):
        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            NoEncryption,
            PrivateFormat,
        )

        _clear_signing_env(monkeypatch)
        pem = p256_key.private_bytes(
            Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
        ).decode()
        monkeypatch.setenv("GOVERNANCE_SIGNING_KEY_PEM", pem)
        monkeypatch.setenv("GOVERNANCE_SIGNING_ALG", ALG_ES256)
        monkeypatch.setenv("GOVERNANCE_SIGNING_KID", "local-dev")
        sign, alg, kid = jws._signer()
        assert (alg, kid) == (ALG_ES256, "local-dev")
        # And it really signs, verifiably.
        assert verify_signature(
            alg=ALG_ES256,
            public_key=p256_key.public_key(),
            signature_base=b"base",
            signature=sign(b"base"),
        ) is True

    def test_refuses_to_resolve_a_signer_with_no_key_at_all(self, monkeypatch):
        _clear_signing_env(monkeypatch)
        monkeypatch.setenv("GOVERNANCE_SIGNING_KID", "gov-2026-08")
        with pytest.raises(jws.SigningNotConfigured):
            jws._signer()

    def test_requires_a_kid_even_on_the_kms_path(self, monkeypatch):
        # Verifiers select keys by kid, so a KMS deployment without one is unusable. This is the check
        # that the startup validation previously missed: it called `_load_signing_key()`, which a KMS
        # deployment never reaches, so the container would have reported READY and failed per request.
        _clear_signing_env(monkeypatch)
        monkeypatch.setenv("GOVERNANCE_SIGNING_KMS_KEY_ID", "arn:aws:kms:us-east-1:1:key/abc")
        monkeypatch.setenv("GOVERNANCE_SIGNING_ALG", ALG_ES256)
        with pytest.raises(jws.SigningNotConfigured, match="KID"):
            jws._signer()

    def test_rejects_an_algorithm_adcp_verifiers_do_not_accept(self, monkeypatch):
        _clear_signing_env(monkeypatch)
        monkeypatch.setenv("GOVERNANCE_SIGNING_KMS_KEY_ID", "arn:aws:kms:us-east-1:1:key/abc")
        monkeypatch.setenv("GOVERNANCE_SIGNING_ALG", "RS256")
        monkeypatch.setenv("GOVERNANCE_SIGNING_KID", "gov-2026-08")
        with pytest.raises(jws.SigningNotConfigured, match="RS256"):
            jws._signer()


class TestVerificationIsNotVacuous:
    """`verify_signature` RETURNS A BOOL and never raises.

    Recorded because it caught this file out: four tests here originally called it without asserting the
    result, so they passed whether or not verification succeeded. A `pytest.raises` around it is equally
    useless. Every call must be asserted, and a passing verification only means something alongside a
    failing one.
    """

    def test_returns_false_rather_than_raising_on_a_bad_signature(self, p256_key):
        base = b"some-signing-base"
        der = p256_key.sign(base, ec.ECDSA(hashes.SHA256()))
        good = jws._der_to_jose(der)
        # No exception -- a bool. This is the fact that made the vacuous tests possible.
        assert verify_signature(
            alg=ALG_ES256, public_key=p256_key.public_key(), signature_base=b"different", signature=good
        ) is False

    def test_rejects_a_signature_from_a_different_key(self, p256_key):
        other = ec.generate_private_key(ec.SECP256R1())
        base = b"same-base"
        signature = jws._der_to_jose(other.sign(base, ec.ECDSA(hashes.SHA256())))
        assert verify_signature(
            alg=ALG_ES256, public_key=p256_key.public_key(), signature_base=base, signature=signature
        ) is False

    def test_rejects_a_der_signature_passed_through_unconverted(self, p256_key):
        # The exact bug `_der_to_jose` exists to prevent: DER is not 64 bytes, so the verifier rejects
        # it. Proves the conversion is load-bearing rather than decorative.
        base = b"unconverted"
        der = p256_key.sign(base, ec.ECDSA(hashes.SHA256()))
        assert len(der) != 64
        assert verify_signature(
            alg=ALG_ES256, public_key=p256_key.public_key(), signature_base=base, signature=der
        ) is False

    def test_a_tampered_payload_invalidates_a_kms_signed_token(self, monkeypatch, p256_key):
        _clear_signing_env(monkeypatch)
        monkeypatch.setenv("GOVERNANCE_SIGNING_KMS_KEY_ID", "arn:aws:kms:us-east-1:1:key/abc")
        monkeypatch.setenv("GOVERNANCE_SIGNING_ALG", ALG_ES256)
        monkeypatch.setenv("GOVERNANCE_SIGNING_KID", "gov-2026-08")

        class StubKms:
            def sign(self, **kwargs):
                return {"Signature": p256_key.sign(kwargs["Message"], ec.ECDSA(hashes.SHA256()))}

        import boto3

        monkeypatch.setattr(boto3, "client", lambda *a, **k: StubKms())
        token = jws.issue_governance_context(
            plan_id="plan-1",
            audience="seller-x",
            phase="intent",
            plan={"plan_id": "plan-1"},
            issuer="https://governance.example/agent",
        )
        h, pay, sig = token.split(".")
        assert verify_signature(
            alg=ALG_ES256,
            public_key=p256_key.public_key(),
            signature_base=f"{h}.{pay}".encode("ascii"),
            signature=b64url_decode(sig),
        ) is True
        # Same signature, altered signing input.
        assert verify_signature(
            alg=ALG_ES256,
            public_key=p256_key.public_key(),
            signature_base=f"{h}.{pay}x".encode("ascii"),
            signature=b64url_decode(sig),
        ) is False
