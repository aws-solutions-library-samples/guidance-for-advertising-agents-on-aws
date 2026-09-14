"""Create the KMS asymmetric key this agent signs governance context with.

    uv run python deploy_signing_key.py            # dry run
    uv run python deploy_signing_key.py --apply

## Why KMS rather than a PEM in Secrets Manager

This reverses an earlier design in `jws.py`, and the reason is that AWS documents this exact pattern for
agent signing keys. AgentCore Identity's [Private Key JWT client
authentication](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/private-key-jwt.html)
registers a public key with the relying party while the private key stays in KMS, calls `kms:Sign` per
assertion, and records every use in CloudTrail. Applying the same shape here means:

* the private key is **non-exportable** -- there is no PEM to leak from an environment variable, a
  container layer, or a secret someone can read;
* every signature is auditable in CloudTrail, which is the point of signing a governance decision;
* **the JWKS question is settled.** `kms:GetPublicKey` returns the public half on demand, so publishing a
  JWKS becomes deriving a public artifact rather than handling a secret. That was the open decision
  blocking verification.

## Why ES256 / ECC_NIST_P256

The AdCP SDK accepts exactly two algorithms, Ed25519 and ES256, and KMS can hold either
(`ECC_NIST_EDWARDS25519` or `ECC_NIST_P256`). ES256 is chosen because:

* it is the intersection of what AdCP verifiers accept **and** what AgentCore Identity's Private Key JWT
  supports (RS256/PS256/ES256), so the same key spec stays usable if that outbound path is ever wanted;
* KMS's Ed25519 support has a sharp edge -- `ED25519_SHA_512` requires `MessageType: RAW` while
  `ED25519_PH_SHA_512` requires `DIGEST`, and the docs state they are not interchangeable. `jws.py`
  therefore refuses Ed25519-over-KMS rather than guessing.

One consequence worth knowing: `kms:Sign` returns ECDSA signatures **DER-encoded**, while JWS needs raw
``R || S``. `jws._der_to_jose` does that conversion and `test_jws_kms.py` covers the padding case, which
is the one that would otherwise pass a handful of manual checks and fail roughly one signature in 256.
"""

from __future__ import annotations

from aws_region import region

import argparse
import json
import os
import sys

import boto3
from botocore.exceptions import ClientError

REGION = region()
#: Instance prefix (see deploy_all.resolve_prefix). Must match the `alias/{prefix}-governance-signing`
#: value render_agentcore_json.py injects into GOVERNANCE_SIGNING_KMS_KEY_ID, or the runtime signs
#: with a key this script never created. Defaults to 'adcp' for a standalone run.
INSTANCE_PREFIX = os.environ.get("INSTANCE_PREFIX", "adcp").strip() or "adcp"
ALIAS = f"alias/{INSTANCE_PREFIX}-governance-signing"
DESCRIPTION = "AdCP reference governance agent: signs governance context tokens (ES256)."
KEY_SPEC = "ECC_NIST_P256"

kms = boto3.client("kms", region_name=REGION)


def find_key_by_alias() -> str | None:
    try:
        return kms.describe_key(KeyId=ALIAS)["KeyMetadata"]["KeyId"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("NotFoundException", "AccessDeniedException"):
            return None
        raise


def create_key() -> str:
    created = kms.create_key(
        Description=DESCRIPTION,
        # SIGN_VERIFY, not ENCRYPT_DECRYPT: an ECC key in KMS does one or the other, never both.
        KeyUsage="SIGN_VERIFY",
        KeySpec=KEY_SPEC,
        Tags=[{"TagKey": "Project", "TagValue": "adcp-reference-governance"}],
    )
    key_id = created["KeyMetadata"]["KeyId"]
    kms.create_alias(AliasName=ALIAS, TargetKeyId=key_id)
    return key_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    existing = find_key_by_alias()
    if existing:
        print(f"Key already exists: {existing}  (alias {ALIAS})")
        key_id = existing
    elif not args.apply:
        print(f"Would create a {KEY_SPEC} SIGN_VERIFY key with alias {ALIAS}")
        print("\nDry run. Nothing created.")
        return 0
    else:
        key_id = create_key()
        print(f"Created key: {key_id}  (alias {ALIAS})")

    meta = kms.describe_key(KeyId=key_id)["KeyMetadata"]
    print(f"  arn      : {meta['Arn']}")
    print(f"  spec     : {meta['KeySpec']}")
    print(f"  usage    : {meta['KeyUsage']}")
    print(f"  algorithms: {meta.get('SigningAlgorithms')}")

    # Proof rather than assumption: sign something and check the public key verifies it. This is the
    # only way to know the key is usable for what the agent will do with it.
    if args.apply or existing:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
        from cryptography.hazmat.primitives.serialization import load_der_public_key

        sample = b"adcp-governance-signing-check"
        signature = kms.sign(
            KeyId=key_id, Message=sample, MessageType="RAW", SigningAlgorithm="ECDSA_SHA_256"
        )["Signature"]
        public = load_der_public_key(kms.get_public_key(KeyId=key_id)["PublicKey"])
        public.verify(signature, sample, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(signature)
        print("\n  live signature check: OK (verified against the KMS public key)")
        print(f"  DER signature is {len(signature)}B; JOSE form will be 64B (r={r.bit_length()}b, s={s.bit_length()}b)")

    print("\nSet these on the governance runtime:")
    print(f"  GOVERNANCE_SIGNING_KMS_KEY_ID={meta['Arn']}")
    print("  GOVERNANCE_SIGNING_ALG=ecdsa-p256-sha256")
    print("  GOVERNANCE_SIGNING_KID=<a stable id, e.g. adcp-gov-2026-08>")
    print("\nThe execution role needs kms:Sign and kms:GetPublicKey on that key ARN.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
