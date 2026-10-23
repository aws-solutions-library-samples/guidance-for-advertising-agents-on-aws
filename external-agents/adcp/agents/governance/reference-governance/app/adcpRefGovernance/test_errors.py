"""Tests for SDK-sourced error construction.

Behavioural tests only. The canaries that fire when the underlying SDK gaps are fixed live in
`test_sdk_gaps.py`, deliberately apart: deleting a canary when a gap is retired must not also delete
the test that proves this module works.

The assertion that earns its place is `test_recovery_values_are_not_written_in_this_module`. Everything
else here would still pass if someone replaced the manifest read with a hardcoded dict -- and that dict
is exactly the failure this module exists to avoid, because it would keep passing while drifting from
the SDK on the next version bump.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

import errors


def _manifest_recovery(code: str) -> str:
    """Read the expected value from the SDK, so the test cannot encode its own copy."""
    from adcp.types.generated_poc.manifest_schema import AdcpManifest  # noqa: PLC0415
    from importlib.resources import files  # noqa: PLC0415

    from adcp import get_adcp_spec_version  # noqa: PLC0415
    from adcp.validation.schema_loader import resolve_bundle_key  # noqa: PLC0415

    bundle = resolve_bundle_key(get_adcp_spec_version())
    manifest = AdcpManifest.model_validate_json(
        (files("adcp") / "_schemas" / bundle / "manifest.json").read_text(encoding="utf-8")
    )
    root = getattr(manifest.error_codes, "root", manifest.error_codes)
    recovery = root[code].recovery
    return getattr(recovery, "value", recovery)


@pytest.mark.parametrize(
    "code",
    ["CAMPAIGN_SUSPENDED", "GOVERNANCE_DENIED", "GOVERNANCE_UNAVAILABLE"],
)
def test_recovery_matches_the_sdk_manifest(code: str) -> None:
    """The three governance codes, which `STANDARD_ERROR_CODES` does not carry."""
    recovery, suggestion = errors.code_metadata(code)
    assert recovery == _manifest_recovery(code)
    assert suggestion, "the manifest carries a suggestion for every governance code"


def test_the_emitted_error_carries_that_recovery() -> None:
    payload = errors.governance_error("CAMPAIGN_SUSPENDED", "suspended pending human review")
    entry = payload["errors"][0]
    assert entry["code"] == "CAMPAIGN_SUSPENDED"
    assert entry["recovery"] == _manifest_recovery("CAMPAIGN_SUSPENDED")
    assert entry["suggestion"]


def test_suspension_is_transient_and_denial_is_correctable() -> None:
    """Pinned as behaviour, not as literals: a buyer's retry logic dispatches on these.

    `transient` on suspension is what tells a caller to wait rather than give up; `correctable` on a
    denial is what tells it to restructure rather than retry unchanged.
    """
    assert errors.governance_error("CAMPAIGN_SUSPENDED")["errors"][0]["recovery"] == "transient"
    assert errors.governance_error("GOVERNANCE_DENIED")["errors"][0]["recovery"] == "correctable"


def test_retry_after_is_top_level_on_the_error_entry() -> None:
    entry = errors.governance_error("CAMPAIGN_SUSPENDED", retry_after=300)["errors"][0]
    assert entry["retry_after"] == 300
    assert "details" not in entry or "retry_after" not in (entry.get("details") or {})


def test_an_unknown_code_defers_to_the_sdk_rather_than_guessing() -> None:
    """A code outside the SDK's vocabulary is one this agent should not be minting."""
    assert errors.code_metadata("NOT_A_REAL_ADCP_CODE") == (None, None)


def test_details_holds_only_what_the_caller_passed() -> None:
    """`message` and `field` must not be copied into `details`.

    `adcp_error`'s own docstring: values in `details` reach the caller's LLM context, so request
    parameters and caller-supplied strings do not belong there.
    """
    entry = errors.governance_error(
        "CAMPAIGN_SUSPENDED",
        "a message mentioning plan-42",
        field="payload.account",
        details={"threshold": 30},
    )["errors"][0]
    assert entry["details"] == {"threshold": 30}


def test_recovery_values_are_not_written_in_this_module() -> None:
    """G1: the classification has one source, and it is the SDK.

    A hardcoded `recovery="transient"` would satisfy every other test in this file and silently drift
    on the next SDK bump, which is the whole reason the manifest is read at all.
    """
    tree = ast.parse(Path(inspect.getfile(errors)).read_text(encoding="utf-8"))
    # Drop docstrings: they discuss these words legitimately, and a text scan would flag the prose
    # that explains the design rather than a hardcoded value.
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body:
            first = body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                body.pop(0)

    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    for banned in ("transient", "correctable", "terminal"):
        assert banned not in literals, (
            f"{banned!r} is a hardcoded recovery value; read it from the manifest instead"
        )
