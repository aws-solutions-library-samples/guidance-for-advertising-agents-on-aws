"""
Shared request-shape validators for the AdCP reference seller's mutating
media-buy tasks (`create_media_buy` — task 3, `update_media_buy` — task 4
of `.kiro/specs/seller-agent-adcp-compliance/design.md`).

Kept out of `main.py` (rather than growing that module unbounded) since
this validator is reused verbatim by task 4 — a single shared home avoids
duplicating the oneOf logic across two task handlers.
"""

from __future__ import annotations

from typing import Any

# AdCP's `account` field is a discriminated oneOf with exactly two
# variants (see AdCP's buyer skill, `skills/call-adcp-agent/SKILL.md`,
# "account is a oneOf" section, and `adcp.types.generated_poc.core
# .account_ref.AccountReference1`/`AccountReference2`):
#   - variant 0: {"account_id": "..."}
#   - variant 1: {"brand": {...}, "operator": "...", "sandbox": bool?}
# `additionalProperties: false` on each variant means mixing fields from
# both (e.g. {account_id, brand}) fails BOTH variants, not either one.
_VARIANT_0_FIELDS = {"account_id"}
_VARIANT_1_REQUIRED_FIELDS = {"brand", "operator"}
_VARIANT_1_OPTIONAL_FIELDS = {"sandbox"}
_VARIANT_1_FIELDS = _VARIANT_1_REQUIRED_FIELDS | _VARIANT_1_OPTIONAL_FIELDS
_KNOWN_FIELDS = _VARIANT_0_FIELDS | _VARIANT_1_FIELDS


def validate_account_oneof(account: dict[str, Any] | None) -> str | None:
    """Validate an AdCP `account` object against its two `oneOf` variants.

    Returns `None` when `account` is absent (not every mutating task
    requires it) or valid. Returns a human-readable validation error
    message — never raises — when `account` is present but invalid, so
    callers can feed the message straight into
    `adcp.server.responses.media_buy_error_response`/
    `update_media_buy_response`'s error path without a try/except.

    Rejects:
      - An `account` object with fields from both variants (e.g.
        `{"account_id": ..., "brand": ...}`).
      - An `account` object with fields from neither recognized variant
        (e.g. `{"foo": "bar"}`), or fully empty (`{}`).
      - Variant 1 (`brand`/`operator`) missing one of its two required
        fields (e.g. `{"brand": {...}}` alone).

    Does NOT deep-validate `brand`'s own shape (`{domain: ...}`) — that's
    `adcp.types.CreateMediaBuyRequest`'s job via normal Pydantic
    validation upstream of this check; this function only enforces the
    oneOf discipline at the `account` level.
    """
    if account is None:
        return None
    if not isinstance(account, dict):
        return "account must be an object matching one of the two AdCP account oneOf variants"

    present_fields = set(account.keys())
    unknown_fields = present_fields - _KNOWN_FIELDS
    has_variant_0_fields = bool(present_fields & _VARIANT_0_FIELDS)
    has_variant_1_fields = bool(present_fields & _VARIANT_1_FIELDS)

    if has_variant_0_fields and has_variant_1_fields:
        return (
            "account mixes fields from both oneOf variants "
            "({account_id} and {brand, operator}) - pick exactly one variant "
            "and send only its fields"
        )

    if has_variant_0_fields:
        if unknown_fields:
            return f"account has unrecognized field(s) for the account_id variant: {sorted(unknown_fields)}"
        return None

    if has_variant_1_fields:
        missing_required = _VARIANT_1_REQUIRED_FIELDS - present_fields
        if missing_required:
            return f"account is missing required field(s) for the brand/operator variant: {sorted(missing_required)}"
        if unknown_fields:
            return f"account has unrecognized field(s) for the brand/operator variant: {sorted(unknown_fields)}"
        return None

    return (
        "account does not match either AdCP oneOf variant "
        "({account_id} or {brand, operator}) - it has fields from neither"
    )
