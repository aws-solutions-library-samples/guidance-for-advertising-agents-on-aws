"""Structured AdCP errors, with recovery semantics read from the SDK rather than restated here.

`adcp.server.helpers.adcp_error` auto-populates `recovery` from its own code table, which covers 38 of
the 92 codes in the AdCP error vocabulary. All three governance codes are outside that table, so
`adcp_error("CAMPAIGN_SUSPENDED", ...)` alone classifies it `terminal` -- telling a buyer to stop
retrying a condition that resolves by waiting.

The correct values are in the SDK, in the schema bundle it ships:

    adcp/_schemas/<bundle>/manifest.json  ->  .error_codes[<CODE>].{recovery, suggestion}

with `AdcpManifest` as the model for it. This module reads that once and passes the values through, so
the classification has one source and it is the SDK's.

3.1 makes this the sender's job explicitly: `core/error.py` states that `error.recovery` on the wire is
authoritative and that senders SHOULD populate it from 3.1 onward, with the schema's `enumMetadata` as
the documentary mirror. Populating it is conformance, not a workaround.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from importlib.resources import files
from typing import Any

from adcp import ErrorCode, get_adcp_spec_version
from adcp.server.helpers import adcp_error
from adcp.types.generated_poc.manifest_schema import AdcpManifest
from adcp.validation.schema_loader import resolve_bundle_key

_log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _manifest() -> AdcpManifest:
    """The SDK's bundled manifest for the spec version this SDK targets.

    SDK-GAP-2: there is no public accessor. `adcp.schemas.load_schema` reaches only `adcp/schemas/`,
    which bundles a single file (`adcp-agents.json`); the manifest lives under the private
    `adcp/_schemas/`. `AdcpManifest` is not re-exported from `adcp.types` either -- it is not mentioned
    in that module at all -- so it comes from the generated module. Both are asserted by canaries in
    `test_sdk_gaps.py`.

    This is the only place in the agent that touches the private path, so retiring SDK-GAP-2 changes
    one file.
    """
    bundle_key = resolve_bundle_key(get_adcp_spec_version())
    path = files("adcp") / "_schemas" / bundle_key / "manifest.json"
    return AdcpManifest.model_validate_json(path.read_text(encoding="utf-8"))


def _error_codes() -> dict[str, Any]:
    codes = _manifest().error_codes
    return getattr(codes, "root", codes)


def code_metadata(code: str) -> tuple[str | None, str | None]:
    """`(recovery, suggestion)` for an AdCP error code, from the SDK's manifest.

    Returns `(None, None)` for a code the manifest does not carry, which leaves `adcp_error`'s own
    defaulting in charge. That is the right fallback: a code absent from the SDK's vocabulary is one
    this agent should not be minting.
    """
    entry = _error_codes().get(code)
    if entry is None:
        _log.warning("error code %s is not in the SDK manifest; recovery left to adcp_error", code)
        return None, None
    recovery = getattr(entry, "recovery", None)
    return (
        # `recovery` is an enum on the model; the wire wants its value.
        getattr(recovery, "value", recovery),
        getattr(entry, "suggestion", None),
    )


def governance_error(
    code: ErrorCode | str,
    message: str | None = None,
    *,
    field: str | None = None,
    retry_after: int | None = None,
    details: dict[str, str | int | float | bool | None] | None = None,
) -> dict[str, Any]:
    """An `errors[]` payload whose `recovery` and `suggestion` come from the SDK manifest.

    SDK-GAP-1: `adcp_error` would classify every governance code `terminal`, because
    `STANDARD_ERROR_CODES` does not carry them.

    `details` must hold server-generated values only. `adcp_error`'s own docstring is explicit that
    request parameters and caller strings reach the caller's LLM context from here.
    """
    code_str = getattr(code, "value", code)
    recovery, suggestion = code_metadata(code_str)
    return adcp_error(
        code_str,
        message,
        field=field,
        suggestion=suggestion,
        recovery=recovery,
        retry_after=retry_after,
        details=details,
    )
