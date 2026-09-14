"""AdCP reference campaign-governance agent.

Built on the **AdCP SDK**: `ReferenceGovernanceAgent` subclasses `adcp.server.governance.
GovernanceHandler`, and `adcp.server.serve` runs it as an MCP server on AgentCore Runtime. Request
validation, typed request/response models, the protocol envelope and tool registration all come from the
SDK. The only thing written here is the part that is actually this agent's own: what it decides, what it
remembers, and what it signs.

## What it implements, and what it says about the rest

`sync_plans`, `check_governance`, `report_plan_outcome` and `get_plan_audit_logs` are real.
`get_creative_features` and the five property-list operations report that they are not supported — which
is a true statement about this agent and is the shape `GovernanceHandler`'s own docstring prescribes for
operations an agent does not offer. A stub returning a plausible-looking approval would be far worse than
one saying "not supported": a caller can handle the second.

`sync_governance` is deliberately absent. It is an **accounts** task on the **seller** — the buyer syncs
governance agent endpoints to a seller account, and the seller then calls that agent for its execution
checks. It is not a governance-agent operation at all.

## The two rules this agent will not bend

**Committed budget comes from reported outcomes, not from approved checks.** An approval that is never
acted on consumes nothing, and an approval acted on for a different amount consumes the amount the seller
actually confirmed. AdCP is explicit, and it is the difference between knowing what was spent and knowing
what was permitted.

**When it cannot evaluate something, it says so rather than passing it.** An unstated budget, an
unparseable date, a plan that was never synced: each produces a stated absence or a correctable error, and
none produces an approval. A governance agent's characteristic failure is not a wrong number, it is
approving what it should have stopped.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx
import pytesseract
from PIL import Image

import jwt
from adcp.server import (  # noqa: F401  (ADCPHandler re-exported for typing)
    ADCPHandler,
    AccountAwareToolContext,
    RequestMetadata,
    serve,
)
from adcp import get_adcp_spec_version
from adcp.server.base import NotImplementedResponse
from adcp.server.governance import GovernanceHandler
from adcp.types import (
    CheckGovernanceRequest,
    CheckGovernanceResponse,
    CreatePropertyListRequest,
    DeletePropertyListRequest,
    Error,
    GetAdcpCapabilitiesRequest,
    GetCreativeFeaturesRequest,
    GetPlanAuditLogsRequest,
    GetPlanAuditLogsResponse,
    GetPropertyListRequest,
    ListPropertyListsRequest,
    ReportPlanOutcomeRequest,
    ReportPlanOutcomeResponse,
    SyncPlansRequest,
    SyncPlansResponse,
    UpdatePropertyListRequest,
)


# SDK-GAP-5: the audit-log component models are not re-exported from `adcp.types` (checked:
# `hasattr(adcp.types, "Entry")` is False), so they import from the generated module. This is the
# documented workaround, not evidence the types are missing -- see SDK-GAPS.md. When a release
# re-exports them, this block collapses into the `adcp.types` import above and the canary in
# `test_sdk_gaps.py` fails to tell us so.
from adcp.types.generated_poc.protocol.get_adcp_capabilities_response import (
    Adcp,
    CreativeFeature,
    GetAdcpCapabilitiesResponse,
    Governance,
    Idempotency,
    Range as CreativeFeatureRange,
)
from adcp.types.generated_poc.governance.get_plan_audit_logs_response import (
    Budget as AuditBudget,
    DriftMetrics,
    Entry,
    Escalation,
    Finding as AuditFinding,
    Plan as AuditPlan,
    Statuses,
    Summary,
)
# `GetCreativeFeaturesResponse` is a two-variant union (`oneOf`), so the variants are imported by name
# rather than the alias: variant 1 requires `results`, variant 2 requires `errors`, and a body carrying
# NEITHER matches no variant. Constructing them is what makes that a construction-time error instead of
# an `oneOf composition failed` at the wire, which is how it was actually found.
from adcp.types.generated_poc.creative.creative_feature_result import CreativeFeatureResult
from adcp.types.generated_poc.creative.get_creative_features_response import (
    GetCreativeFeaturesResponse1 as CreativeFeaturesResult,
    GetCreativeFeaturesResponse2 as CreativeFeaturesError,
)

import aggregation
import errors
import gov_logging
import idempotency_backend
import jws
import policy
import state

#: Single module-level idempotency store for `report_plan_outcome` (M8).
#:
#: `report_plan_outcome` carries a required `idempotency_key`, described in the schema as preventing
#: duplicate outcome reports on retries. Without it a retried report increments the cross-plan aggregate a
#: second time -- and the aggregate has no decrement path by design, so the over-count is permanent and
#: the fragmentation defense starts denying legitimate buys using a number nobody can correct.
#:
#: `check_governance` is deliberately NOT wrapped: it carries no `idempotency_key`, being an evaluation
#: rather than a mutation, and wrapping it would invent a contract.
#:
#: The store needs `context.caller_identity` to scope its cache. `IdempotencyStore._prepare` fails closed
#: and skips dedup entirely when that is `None` -- documented, intentional, and silent. That is why the
#: `context_factory` this agent now passes to `serve()` is a prerequisite for M8 rather than a nicety: the
#: reference seller shipped four wrapped handlers that deduped nothing over the real transport for exactly
#: this reason.
_idempotency_store = idempotency_backend.make_store()

_log = logging.getLogger(__name__)

AGENT_NAME = "adcp-reference-governance"

#: Governance mode, recorded on every check.
#:
#: AdCP defines `audit`, `advisory` and `enforce`, and says the value is taken from the agent's runtime
#: configuration at check time rather than from a plan field. It exists so an auditor can tell an
#: `approved` that was enforced from an `approved` that was only logged.
#:
#: Defaults to `enforce` **only because this agent does enforce** — it returns `denied` and means it. An
#: agent that did not enforce must not report that it did, which is why this is read from configuration
#: and validated rather than hardcoded optimistically.
_ALLOWED_MODES = ("audit", "advisory", "enforce")


def governance_mode() -> str:
    mode = os.environ.get("GOVERNANCE_MODE", "enforce").strip().lower()
    if mode not in _ALLOWED_MODES:
        raise RuntimeError(
            f"GOVERNANCE_MODE={mode!r} is not one of {_ALLOWED_MODES}. Refusing to start rather than "
            "recording an unrecognised mode on every audit entry."
        )
    return mode


def issuer_url() -> str:
    """This agent's own URL, for the token's `iss` claim.

    Required rather than defaulted: a token whose issuer is wrong cannot be traced back to the agent that
    made the decision, which defeats the audit trail the signature exists to support.
    """
    url = os.environ.get("GOVERNANCE_AGENT_URL", "").strip()
    if not url:
        raise RuntimeError("GOVERNANCE_AGENT_URL is not set; a signed context needs a real issuer.")
    return url


def _plan_not_found(plan_id: str) -> Error:
    return Error(
        code="PLAN_NOT_FOUND",
        message=(
            f"No plan {plan_id!r} has been synced to this agent. Call sync_plans before checking "
            "against it."
        ),
    )


def _action_from_request(request: CheckGovernanceRequest) -> dict[str, Any]:
    """The fields `policy.evaluate` needs, pulled from whichever half of the request carries them.

    AdCP infers the CHECK TYPE from which fields are present: `tool` + `payload` is an orchestrator's
    intent check, `governance_context` + `planned_delivery` is a seller's execution check. Both describe
    an action, in different shapes, and the evaluation is the same either way — so the shapes are
    normalised here rather than duplicating the policy for each.

    Nothing is defaulted. A field neither shape carries stays absent, and `policy` reports the category
    as unevaluated rather than as a pass.
    """
    payload: dict[str, Any] = {}
    raw_payload = getattr(request, "payload", None)
    if isinstance(raw_payload, dict):
        payload = raw_payload

    planned = getattr(request, "planned_delivery", None)
    planned_dict: dict[str, Any] = {}
    if planned is not None:
        planned_dict = (
            planned
            if isinstance(planned, dict)
            else planned.model_dump(mode="json", exclude_none=True)
        )

    source = {**payload, **planned_dict}

    return {
        "amount": _amount_from_source(source),
        # Flight: AdCP's create_media_buy body and PlannedDelivery both name the window `start_time`/
        # `end_time` (ISO 8601 datetimes). Reading only `start_date` here meant a spec-correct payload
        # never satisfied the flight check — it always reported "no flight dates". The date-shaped names
        # stay as fallbacks for callers that send them.
        "start_date": (
            source.get("start_time") or source.get("start_date") or source.get("flight_start_date")
        ),
        "end_date": (
            source.get("end_time") or source.get("end_date") or source.get("flight_end_date")
        ),
        # Markets: PlannedDelivery nests geo under `geo.{countries,regions}`; a create_media_buy body
        # expresses it per-package under `packages[].targeting_overlay.{geo_countries,geo_regions}`.
        # Top-level `countries`/`markets`/`regions` stay accepted for simple callers.
        "markets": _markets_from_source(source),
        # Channels: PlannedDelivery carries a top-level `channels` list. A create_media_buy body has no
        # channel field (channel is a product property), so a bare intent check leaves this absent and
        # channel_compliance is reported unevaluated rather than guessed.
        "channels": source.get("channels"),
    }


def _amount_from_source(source: dict[str, Any]) -> Any:
    """The committed amount, from whichever budget shape the caller used.

    `budget` (scalar or `{total}`) is the plan-ish shape; `total_budget` (scalar or `{amount}`) is the
    create_media_buy shape used with a proposal; a create_media_buy body authored with explicit packages
    instead carries per-package `packages[].budget`, whose sum is the committed amount. Only finite
    numbers count toward the package sum — a malformed value is skipped, and `policy._as_amount` rejects
    a non-number for the scalar shapes downstream, so nothing here is trusted as a budget that is not one.
    """
    budget = source.get("budget")
    if isinstance(budget, dict):
        if budget.get("total") is not None:
            return budget.get("total")
    elif budget is not None:
        return budget

    total_budget = source.get("total_budget")
    if isinstance(total_budget, dict):
        if total_budget.get("amount") is not None:
            return total_budget.get("amount")
    elif total_budget is not None:
        return total_budget

    if source.get("committed_budget") is not None:
        return source.get("committed_budget")

    packages = source.get("packages")
    if isinstance(packages, list):
        total = 0.0
        seen = False
        for pkg in packages:
            if not isinstance(pkg, dict):
                continue
            value = pkg.get("budget")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            total += float(value)
            seen = True
        if seen:
            return total

    return None


def _markets_from_source(source: dict[str, Any]) -> Any:
    """Targeted markets, from whichever shape the caller used (see `_action_from_request`)."""
    geo = source.get("geo")
    if isinstance(geo, dict):
        markets = list(geo.get("countries") or []) + list(geo.get("regions") or [])
        if markets:
            return markets

    top = source.get("countries") or source.get("markets") or source.get("regions")
    if top:
        return top

    packages = source.get("packages")
    if isinstance(packages, list):
        collected: list[Any] = []
        for pkg in packages:
            if not isinstance(pkg, dict):
                continue
            overlay = pkg.get("targeting_overlay")
            if isinstance(overlay, dict):
                collected.extend(overlay.get("geo_countries") or [])
                collected.extend(overlay.get("geo_regions") or [])
        if collected:
            return collected

    return None


def _phase_for(request: CheckGovernanceRequest) -> str:
    """The lifecycle phase this check belongs to.

    Derived from the CHECK TYPE, not read from `request.phase`, and the difference is not cosmetic.
    AdCP infers the check type from which fields are present — `tool` + `payload` is an orchestrator's
    intent check, `governance_context` + `planned_delivery` is a seller's execution check — and it
    documents `phase` as defaulting to `purchase` and being *present on execution checks*.

    So `request.phase` reads `purchase` on an intent check that never set it. Minting a token stamped
    `purchase` for a buyer's intent check would hand the buyer a token a seller should only ever have
    produced, and a conforming seller checks `phase: "intent"` before accepting a spend commit. The
    token would be rejected, and the reason would be invisible from this side.
    """
    has_intent_fields = getattr(request, "tool", None) is not None
    if has_intent_fields:
        return "intent"
    phase = getattr(request, "phase", None)
    return str(getattr(phase, "value", phase) or "purchase")


def _suspended_error(plan_id: str) -> dict[str, Any]:
    """`CAMPAIGN_SUSPENDED`, with recovery and suggestion read from the SDK manifest.

    `adcp_error` alone would classify this `terminal`, because `STANDARD_ERROR_CODES` carries 38 of the
    92 spec codes and not this one. `terminal` would tell a buyer to stop retrying a condition that
    resolves by waiting, which is the opposite of what the spec says.
    """
    return errors.governance_error(
        "CAMPAIGN_SUSPENDED",
        (
            f"Plan {plan_id} is suspended pending human review. It will accept checks and outcome "
            "reports again once the escalation is resolved."
        ),
        details={"plan_id_suspended": True},
    )


#: The spec's dedicated field for the seller a check is ABOUT. Not in the SDK (absent from 6.6.0 and
#: from 7.0.2 -- see SDK-GAPS.md gap 5), so it arrives only because `CheckGovernanceRequest` is
#: `extra="allow"`. Named once here rather than spelled at each read site.
TARGET_AGENT_FIELD = "target_agent"


def _target_agent(request: Any) -> str | None:
    """The seller a check is about, per AdCP 3.1's `target_agent`.

    From the Buyer-side intent check table (docs/governance/campaign/responsibilities):
    `target_agent` is the "Exact downstream service URL; becomes the signed token audience and stays
    outside the business payload." That last clause is why `payload.seller` is a fallback and not the
    primary: the spec deliberately keeps this out of the business payload.
    """
    value = getattr(request, TARGET_AGENT_FIELD, None)
    if value is None:
        extra = getattr(request, "model_extra", None) or {}
        value = extra.get(TARGET_AGENT_FIELD)
    return str(value) if value else None


def _seller_subject(
    request: Any,
    *,
    plan: dict[str, Any] | None = None,
    context: Any = None,
    buyer_agent: str | None = None,
) -> str | None:
    """The seller a check concerns. The ONE resolution order, shared by every caller.

    `_token_audience` (which decides the token's `aud`) and `_seller_for` (which decides the spend
    aggregate's key) each had their own answer to this question and disagreed. `_token_audience` read
    `payload.seller` then `caller`; `_seller_for` read `payload.seller`, then the prior token's `aud`,
    then a non-buyer principal, then a sole approved seller -- so on a FIRST intent check it resolved
    nothing while `_token_audience`, one function away, had already resolved the same seller from
    `caller`. The aggregate then degraded to a `critical` unknown and every first check came back
    `conditions`. Two internally-consistent layers disagreeing, with every unit test green.

    Order, most to least direct:

      1. `target_agent` -- the spec's own field for exactly this (F1).
      2. `payload.seller` -- legacy: what this project sent before `target_agent`. Kept so plans and
         tokens issued by the previous build still resolve.
      3. The `governance_context` token's `aud`, which this agent bound at issuance. The only source
         available on `report_plan_outcome`, which carries neither `payload` nor `target_agent` --
         without it the aggregate never commits and the fragmentation defense reads zero forever.
      4. The authenticated caller when it is NOT the buyer, i.e. a seller's own execution or delivery
         check. Per the spec's Seller-side execution check table, `caller` there identifies the seller.
      5. A plan with exactly one `approved_sellers` entry.

    `caller` is deliberately NOT consulted for the buyer's intent check: the SDK documents it as "URL of
    the agent making the request" and the spec as "the buyer-side orchestrator", so on that path it is
    the buyer. Step 4 is guarded on `principal != buyer_agent` for that reason.

    Returns None rather than guessing. The caller degrades the affected category to an explicit unknown,
    because aggregating under a wrong key would deny buys that have nothing to do with each other.
    """
    target = _target_agent(request)
    if target:
        return target

    payload = getattr(request, "payload", None)
    if isinstance(payload, dict) and payload.get("seller"):
        return str(payload["seller"])

    audience = _audience_from_governance_context(request)
    if audience:
        return audience

    principal, _ = _context_identity(context)
    if principal and buyer_agent and principal != buyer_agent:
        return principal

    approved = (plan or {}).get("approved_sellers") or []
    if len(approved) == 1:
        return str(approved[0])
    return None


def _token_audience(request: CheckGovernanceRequest) -> str:
    """The `aud` claim: the seller the token is addressed to.

    Resolved by `_seller_subject`, the same helper the aggregate key uses, so the two cannot diverge
    again (`test_main.py` asserts they agree).

    Falls back to `caller` only when nothing else resolves. On a seller's execution check that IS the
    seller and is correct; on a buyer's intent check it is the buyer, and a token minted with the buyer
    as `aud` is one every conformant seller correctly rejects. So this last step is a last resort, not a
    source -- reaching it on an intent check means the buyer sent no `target_agent`.
    """
    return _seller_subject(request) or str(request.caller)


def _refused_check_response(
    plan_id: str,
    *,
    explanation: str,
    error: dict[str, Any],
    check_id: str | None = None,
) -> CheckGovernanceResponse:
    """A refusal that still satisfies the response contract.

    `CheckGovernanceResponse` requires `check_id`, `verdict`, `plan_id` AND `explanation`, so a refusal
    carries all four alongside `adcp_error` rather than the error alone. Returning just the error raises a
    `ValidationError` on the way out -- which is exactly how a hand-built error dict fails, and it failed
    that way here before a test caught it.

    The verdict is `denied` because the action must not proceed. That is not the agent concluding the
    action breaches the plan; the accompanying error code and its recovery classification are what
    distinguish "this plan will not transact right now" from "this action is non-compliant".

    **Both layers, in their own places.** `errors.governance_error` returns the PAYLOAD array,
    `{"errors": [...]}`. This previously assigned that whole wrapper to the envelope's `adcp_error`,
    producing `adcp_error: {errors: [...]}` -- which is neither layer: the envelope field is a single
    typed `Error`, and the payload array belongs at the response root. AdCP's two-layer model asks for
    both on a fatal failure, so the array goes to `errors` and its first entry to `adcp_error`.
    """
    return CheckGovernanceResponse.model_validate(
        _with_error_layers(
            {
                "check_id": check_id or f"refused_{plan_id}",
                "verdict": "denied",
                "plan_id": plan_id,
                "explanation": explanation,
                "findings": [],
                "categories_evaluated": [],
                "mode": governance_mode(),
            },
            error,
        )
    )


def _with_error_layers(body: dict[str, Any], error: dict[str, Any]) -> dict[str, Any]:
    """Place an `errors.governance_error(...)` payload on both of AdCP's error layers.

    `governance_error` returns the PAYLOAD array, `{"errors": [...]}`. Assigning that whole wrapper to
    the envelope's `adcp_error` -- which every refusal here used to do -- produces
    `adcp_error: {errors: [...]}`, which is neither layer: the envelope field is a single typed `Error`
    and the array belongs at the response root. A client reading either layer finds nothing.

    AdCP asks for both on a fatal failure: `errors[]` is the canonical normative shape, `adcp_error` the
    extractable envelope signal so MCP/A2A clients can dispatch on `code` without re-parsing the
    payload. One helper for both tasks, because they had drifted into two spellings of the same mistake.
    """
    payload_errors = list(error.get("errors") or [])
    body["errors"] = payload_errors
    if payload_errors:
        # The SDK's `Error`, not a passed-through dict, so a code or recovery value the model rejects
        # fails here rather than on the wire.
        body["adcp_error"] = Error.model_validate(payload_errors[0])
    return body


def _suspended_check_response(plan_id: str, *, check_id: str | None = None) -> CheckGovernanceResponse:
    """The refusal for a suspended plan."""
    return _refused_check_response(
        plan_id,
        explanation=(
            f"Plan {plan_id} is suspended pending human review. No action may proceed until the "
            "escalation is resolved."
        ),
        error=_suspended_error(plan_id),
        check_id=check_id or f"suspended_{plan_id}",
    )


def _sibling_account_error() -> dict[str, Any]:
    """M6. AdCP 3.1 changed `check-governance-request.additionalProperties` from false to true.

    Under 3.0 the schema rejected a stray `account` beside `payload`; under 3.1 it is accepted and
    ignored, so a caller that misplaces it would have its account silently dropped and the check
    evaluated against something it did not ask about. Validation can no longer catch this, so this does.
    """
    return errors.governance_error(
        "INVALID_REQUEST",
        (
            "`account` was sent as a sibling of `payload`. It belongs inside `payload`. AdCP 3.1 permits "
            "unknown top-level fields, so this would otherwise be accepted and ignored."
        ),
        field="account",
    )


def _has_sibling_account(request: CheckGovernanceRequest) -> bool:
    """True when `account` arrived at the top level rather than inside `payload`."""
    extra = getattr(request, "model_extra", None) or {}
    return "account" in extra


def _aggregate_threshold_for(
    request: CheckGovernanceRequest, plan: dict[str, Any]
) -> float | None:
    """The threshold the trailing-window aggregate is compared against, if any.

    `budget.reallocation_threshold` governs **budget reallocation autonomy** -- the spec's own words, and
    its table is written entirely about reallocations ("Agent may reallocate up to this amount without
    escalation"). A `create_media_buy` is not a reallocation, so applying it to an initial purchase would
    escalate every first buy on a plan whose threshold is `0`, which means "every *reallocation* requires
    human approval" rather than "approve nothing".
    Getting this wrong is not subtle: with `reallocation_threshold: 0` it turns the agent into one that
    denies all traffic.

    A reallocation is `update_media_buy` / `update_rights`, which AdCP maps to `phase: modification`, and
    the spec confirms the composition directly: *"A reallocation is itself a spend-commit:
    `update_media_buy` carries an incremental commit delta… and that delta enters the aggregate and counts
    toward `reallocation_threshold` evaluation."*

    For a purchase there is **no schema field** carrying an aggregate threshold. The spec's conformance
    example says "Plan sets a `human_review_required` trigger at $10,000 committed spend", but no such
    field exists on `Plan` -- it is governance-agent configuration. So on a purchase the aggregate is
    computed and reported with no consequence attached, rather than borrowing a threshold that means
    something else.
    """
    phase = _phase_hint(request)
    if phase != "modification":
        return None
    budget = plan.get("budget") or {}
    if budget.get("reallocation_unlimited"):
        # An explicit full-autonomy declaration. Mutually exclusive with the threshold by schema.
        return None
    return _amount(budget.get("reallocation_threshold"))


def _phase_hint(request: CheckGovernanceRequest) -> str:
    """The AdCP governance phase: `purchase` | `modification` | `delivery`, defaulting to `purchase`.

    Taken from `phase` when present, and otherwise inferred from `tool` the way the spec describes --
    `update_media_buy` and `update_rights` are modifications.
    """
    declared = getattr(request, "phase", None)
    value = str(getattr(declared, "value", declared) or "")
    if value:
        return value
    tool = str(getattr(request, "tool", "") or "")
    return "modification" if tool in ("update_media_buy", "update_rights") else "purchase"


def _credential_from_authorization_header(request: Any) -> tuple[str | None, str | None]:
    """`(caller_identity, credential_id)` from the request's bearer token.

    Mirrors `adcpRefSeller/main.py`'s equivalent deliberately, including its trust model: AgentCore
    Runtime's Cognito authorizer validates the token's signature, expiry and client allowlist *before*
    forwarding the request, and `requestHeaderAllowlist: ["Authorization"]` is what makes the
    already-verified header visible here at all. So this decodes claims rather than re-verifying a
    signature -- re-verification would mean this container also holding the pool's JWKS and repeating
    work the platform already did as the security boundary. Same posture the README documents for
    `enable_dns_rebinding_protection=False`.

    `sub` is the caller identity where present -- a stable, globally-unique Cognito UUID, never reused
    across principals unlike an email or username. `client_id` is the fallback, and is the only one
    present on a `client_credentials` token, which carries no `sub`.

    `credential_id` is the Cognito app client. AdCP's account binding is *"the Bearer token matches a
    registered credential for the account"*, and the app client is that credential's identity: the buyer
    registers its token with the seller via `sync_governance`, and the seller presents that same token
    back here -- so both sides of a plan's lifecycle resolve to the same credential.
    """
    headers = getattr(request, "headers", None)
    auth_header = None
    if headers is not None:
        auth_header = headers.get("authorization") or headers.get("Authorization")
    if not auth_header:
        # No bearer token: local development, where no platform authorizer sits in front of this code.
        return None, None

    parts = auth_header.split(maxsplit=1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None, None
    token = parts[1].strip()
    if not token:
        return None, None

    try:
        claims = jwt.decode(
            token,
            options={
                "verify_signature": False,
                "verify_aud": False,
                "verify_exp": False,
                "verify_iss": False,
            },
        )
    except jwt.exceptions.DecodeError:
        return None, None

    credential_id = claims.get("client_id")
    return (claims.get("sub") or credential_id, credential_id)


def _build_tool_context(meta: RequestMetadata) -> AccountAwareToolContext:
    """`adcp.server.ContextFactory`: the authenticated caller on every `ToolContext`.

    Without this the committed-spend aggregate has no identity to key on, and the budget category
    degrades on every check -- correct but useless. It is also what makes the SDK's idempotency store
    scope its cache per caller rather than globally.

    `account_id` carries the credential's identity so `_require_matching_account` can enforce AdCP's
    binding of a plan to the credential registered for it.
    """
    caller_identity, credential_id = _credential_from_authorization_header(
        getattr(meta, "request_context", None) or meta
    )
    return AccountAwareToolContext(
        caller_identity=caller_identity,
        account_id=credential_id,
        tenant_id=credential_id,
        request_id=getattr(meta, "request_id", None),
    )


def _account_mismatch_error(plan_id: str) -> dict[str, Any]:
    """M12. The credential presented is not the one this plan is registered to.

    AdCP: *"The governance agent MUST verify that the Bearer token matches a registered credential for the
    account associated with the `plan_id`, and MUST reject requests with unrecognized or mismatched
    credentials."*

    `PERMISSION_DENIED` rather than `PLAN_NOT_FOUND`: the plan exists and the refusal is about
    authorisation, and answering "not found" would leak whether a plan id is in use to a caller with no
    right to know.
    """
    return errors.governance_error(
        "PERMISSION_DENIED",
        (
            f"The presented credential is not registered for the account that owns plan {plan_id}."
        ),
        details={"plan_id_credential_mismatch": True},
    )


def _account_mismatch(stored: dict[str, Any], context: Any) -> bool:
    """True when the caller's credential does not match the one the plan was registered under.

    Returns False when either side is unknown. A plan synced before this check existed carries no
    recorded account, and refusing every call on it would break plans that are otherwise fine -- the
    check tightens as plans are re-synced rather than failing closed on absent data. Absent is not a
    mismatch.
    """
    recorded = str(stored.get("account_id") or "")
    _, presented = _context_identity(context)
    if not recorded or not presented:
        return False
    return recorded != presented


def _context_identity(context: Any) -> tuple[str | None, str | None]:
    """`(principal, account_id)` from the SDK's `ToolContext`.

    `ToolContext.caller_identity` is the SDK's own carrier for the authenticated principal, documented as
    "a stable, globally-unique identifier within the seller's tenant -- never an email, display name, or
    any other mutable handle". `AccountAwareToolContext.account_id` carries the resolved account scope.

    Both are read here rather than derived from the request, because the identity the aggregate needs is
    a property of the credential and not of the message body: neither `sync_plans` nor
    `report_plan_outcome` carries a caller at all.

    `tenant_id` is the fallback for the account, since a governance agent's tenant IS the account whose
    plans it governs. Returning `None` rather than inventing a value is deliberate -- the caller degrades
    visibly instead of aggregating under a guess.
    """
    principal = getattr(context, "caller_identity", None)
    account = getattr(context, "account_id", None) or getattr(context, "tenant_id", None)
    return (str(principal) if principal else None, str(account) if account else None)


def _aggregate_identity(
    request: CheckGovernanceRequest | ReportPlanOutcomeRequest,
    stored: dict[str, Any],
    context: Any = None,
) -> tuple[str, str, str] | None:
    """The `(buyer_agent, seller_agent, account_id)` tuple AdCP keys the spend aggregate on.

    Returns None when the tuple cannot be formed. The caller then degrades the budget category exactly as
    an unreadable aggregate does, rather than aggregating under a wrong key: merging two relationships
    into one would deny buys that have nothing to do with each other, and inventing a key would enforce a
    limit no buyer declared and no auditor could reproduce.

    **`buyer_agent` and `account_id` come from what was recorded at `sync_plans` time**, because that is
    the buyer's own call and the only point at which its authenticated identity is available. Neither is
    on the check or outcome request.

    **`buyer_agent` is the delegating principal, never a sub-agent's URL.** The spec is explicit that a
    delegation does not mint a fresh per-agent aggregation window -- otherwise the delegation surface
    reopens the hole, one large spend split across many sub-agents each with its own headroom. Reading the
    buyer from the plan rather than from `caller` is what guarantees that.
    """
    plan = stored.get("plan") or {}

    buyer_agent = str(stored.get("buyer_principal") or "")
    account_id = str(stored.get("account_id") or "")
    if not buyer_agent or not account_id:
        return None

    seller_agent = _seller_for(request, plan, context, buyer_agent)
    if not seller_agent:
        return None

    return buyer_agent, seller_agent, account_id


def _seller_for(
    request: CheckGovernanceRequest | ReportPlanOutcomeRequest,
    plan: dict[str, Any],
    context: Any,
    buyer_agent: str,
) -> str | None:
    """The seller side of the aggregate key. Delegates to `_seller_subject`, the single resolution
    order -- see that function for why this used to have its own, and what that cost."""
    return _seller_subject(request, plan=plan, context=context, buyer_agent=buyer_agent)


def _audience_from_governance_context(request: Any) -> str | None:
    """The `aud` claim of the request's governance or consultation context: the seller it was issued for.

    Read rather than trusted for authorisation: this agent signed the token itself, so the claim is its
    own record of which seller a check was about. Failures are swallowed because a malformed or absent
    token is not an error on this path -- it just means this source cannot supply the seller.

    `consultation_context` is checked too, because that is what a `conditions` verdict now hands back and
    therefore what an adjusted re-check carries. Reading only `governance_context` would have made the
    seller unresolvable on exactly the re-check the conditions verdict asked for.
    """
    token = getattr(request, "governance_context", None)
    if not token:
        extra = getattr(request, "model_extra", None) or {}
        token = extra.get("consultation_context") or getattr(request, "consultation_context", None)
    if not token:
        return None
    try:
        claims = jws.read_governance_context(str(token))
    except Exception:  # noqa: BLE001 -- an unreadable token simply is not a source of the seller
        return None
    audience = claims.get("aud")
    if isinstance(audience, list):
        return str(audience[0]) if audience else None
    return str(audience) if audience else None


def _delegation_refusal(request: CheckGovernanceRequest, plan: dict[str, Any]) -> str | None:
    """M11. The reason the caller is not authorised to act on this plan, or None.

    When `delegations` is absent the agent imposes no restriction -- the spec says so explicitly, and
    inventing one would deny callers on a plan whose owner declared no delegation policy at all.
    """
    delegations = plan.get("delegations") or []
    if not delegations:
        return None

    caller = _normalise_uri(str(getattr(request, "caller", "") or ""))
    matched: dict[str, Any] | None = None
    for delegation in delegations:
        entry = delegation if isinstance(delegation, dict) else {}
        if _normalise_uri(str(entry.get("agent_url") or "")) == caller:
            matched = entry
            break

    if matched is None:
        return f"Caller {caller} does not match any delegation authorised for this plan."

    expires_at = matched.get("expires_at")
    if expires_at:
        expiry = _as_datetime(expires_at)
        if expiry is not None and policy.utc_now() > expiry:
            return f"The delegation for {caller} expired at {expires_at}."

    action = _action_from_request(request)
    permitted = {str(m).upper() for m in (matched.get("markets") or [])}
    if permitted:
        requested = {str(m).upper() for m in (action.get("markets") or [])}
        outside = sorted(requested - permitted)
        if outside:
            return (
                f"The delegation for {caller} does not cover market(s) {', '.join(outside)}."
            )

    authority = str(matched.get("authority") or "")
    if authority == "propose_only" and str(getattr(request, "tool", "") or ""):
        return (
            f"The delegation for {caller} carries `propose_only` authority, which cannot execute a "
            "spend commit without explicit approval."
        )

    return None


def _normalise_uri(value: str) -> str:
    """RFC 3986 normalisation, to the extent the comparison needs.

    The spec requires exact, case-sensitive comparison after normalisation -- so scheme and host case
    and a trailing slash are normalised, and nothing else is touched. Lowercasing the path would make
    two genuinely different agent URLs compare equal.
    """
    from urllib.parse import urlsplit, urlunsplit  # noqa: PLC0415

    if not value:
        return ""
    parts = urlsplit(value.strip())
    if not parts.scheme:
        return value.strip().rstrip("/")
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), parts.query, "")
    )


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class ReferenceGovernanceAgent(GovernanceHandler[AccountAwareToolContext]):
    """The four implemented handlers, plus explicit refusals for the rest.

    Typed on `AccountAwareToolContext` rather than `Any` because the committed-spend aggregate is keyed on
    an identity that lives on the context, not in the request body: `caller_identity` is the authenticated
    principal and `account_id` the resolved account scope. Declaring it as `Any` is what let the aggregate
    be keyed on invented values.
    """

    # ---------------------------------------------------------------- sync_plans

    async def handle_sync_plans(
        self, request: SyncPlansRequest, context: Any | None = None
    ) -> SyncPlansResponse:
        results = []
        for plan in request.plans:
            # `mode="json"` matters: the SDK's plan carries real `datetime` objects and enum members,
            # and DynamoDB accepts neither. Serialising here rather than teaching the store about every
            # pydantic type keeps the conversion in one place and keeps what is stored identical to what
            # would go on the wire.
            as_dict = (
                plan if isinstance(plan, dict) else plan.model_dump(mode="json", exclude_none=True)
            )
            # Resolve the plan's declared policies before storing, because whether any of them demands
            # human review decides whether the plan is stored suspended. AdCP requires the agent to set
            # the flag itself from resolved policies and to override a caller-supplied `false`.
            resolution = policy.resolve_policies(as_dict)
            # `sync_plans` is the buyer's own call, so the authenticated principal here IS this plan's
            # buyer. Recorded now because the aggregate key needs it later on two paths that carry no
            # caller at all.
            principal, account_id = _context_identity(context)
            stored = state.put_plan(
                as_dict,
                requires_human_review=resolution.requires_human_review,
                buyer_principal=principal,
                account_id=account_id,
            )

            if state.is_suspended(stored["plan_id"]) and not state.unresolved_escalations(
                stored["plan_id"]
            ):
                # A real check is recorded, not just an id. `Escalation.check_id` is required, and an id
                # pointing at nothing would leave `human_override_rate` uncomputable -- there would be no
                # agent recommendation for the human's decision to be compared against.
                reason = (
                    "A resolved policy requires human review."
                    if resolution.requires_human_review
                    else "The plan declares human_review_required."
                )
                registration_check = state.put_check(
                    plan_id=stored["plan_id"],
                    verdict="denied",
                    explanation=(
                        f"Plan registered requiring human review before any action may proceed. {reason}"
                    ),
                    payload={"registration": True},
                    findings=[],
                    categories_evaluated=[],
                )
                state.put_escalation(
                    plan_id=stored["plan_id"], check_id=registration_check, reason=reason
                )
                gov_logging.escalation_opened(
                    plan_id=stored["plan_id"], check_id=registration_check, reason=reason
                )

            results.append(
                {
                    "plan_id": stored["plan_id"],
                    # The SYNC result, not a campaign lifecycle status. AdCP distinguishes the two and a
                    # reader who conflates them sees a healthy campaign where there is only a healthy
                    # write.
                    "status": "active",
                    "version": stored["version"],
                    # Exactly the categories this agent will actually evaluate for the plan. Listing
                    # categories it cannot evaluate would promise review that never happens.
                    "categories": [
                        {"category_id": category, "status": "active"}
                        for category in policy.DETERMINISTIC_CATEGORIES
                    ],
                    # Policies this agent can actually account for, as SDK `ResolvedPolicy` models.
                    # Inline `custom_policies` carry their own `enforcement`, so nothing is inferred.
                    # Registry-only `policy_ids` are absent here because there is no registry to resolve
                    # them against -- listing them would claim a resolution that did not happen.
                    "resolved_policies": [
                        p.model_dump(mode="json", exclude_none=True) for p in resolution.resolved
                    ],
                }
            )
        return SyncPlansResponse.model_validate({"plans": results})

    # ---------------------------------------------------------- check_governance

    async def handle_check_governance(
        self, request: CheckGovernanceRequest, context: Any | None = None
    ) -> CheckGovernanceResponse:
        # Gate order is a correctness property, not a style choice.
        #
        # The shape gate goes before plan existence: a request we could not parse is malformed whether
        # or not the plan exists, and answering `PLAN_NOT_FOUND` for it would attribute the fault to the
        # plan instead of to the request.
        if _has_sibling_account(request):
            # Through `_refused_check_response`, not hand-built. This gate previously assembled
            # `{plan_id, adcp_error}` directly and was three required fields short of valid
            # (`check_id`, `verdict`, `explanation`) -- it raised a ValidationError on the way out
            # rather than refusing the request. Nothing caught it because no test had reached the gate,
            # which is precisely the failure mode that helper's docstring describes.
            return _refused_check_response(
                request.plan_id,
                explanation=(
                    "`account` was sent as a sibling of `payload`, so the request was not evaluated."
                ),
                error=_sibling_account_error(),
            )

        stored = state.get_plan(request.plan_id)

        # M12, before anything about the action: a caller presenting a credential the plan is not
        # registered under has no standing to ask, so nothing about the plan is evaluated or revealed.
        if stored is not None and _account_mismatch(stored, context):
            return _refused_check_response(
                request.plan_id,
                explanation=(
                    "The presented credential is not registered for the account that owns this plan."
                ),
                error=_account_mismatch_error(request.plan_id),
            )

        if stored is None:
            # A correctable error, and specifically NOT a denial: "you have not synced this plan" and
            # "this action breaches your plan" are different facts, and only one of them is about the
            # action.
            # Recorded even though there is no plan. The attempt is exactly the kind of thing an audit
            # trail is for: a buyer checking against a plan it never synced is a real ordering fault, and
            # leaving no trace of it would hide a class of integration bug.
            return CheckGovernanceResponse.model_validate(
                {
                    "check_id": state.put_check(
                        plan_id=request.plan_id,
                        verdict="denied",
                        explanation=_plan_not_found(request.plan_id).message,
                        payload={"caller": str(request.caller), "plan_missing": True},
                        # Evaluated, and it produced nothing: there was no plan to evaluate against.
                        findings=[],
                        categories_evaluated=[],
                    ),
                    "verdict": "denied",
                    "plan_id": request.plan_id,
                    "explanation": _plan_not_found(request.plan_id).message,
                    "findings": [],
                    # Nothing was evaluated, and saying so keeps a denial for a missing plan
                    # distinguishable from a denial for a breach.
                    "categories_evaluated": [],
                    "mode": governance_mode(),
                }
            )

        # Suspension, before evaluation. A suspended plan must not be evaluated: computing a verdict we
        # are forbidden to act on would also inflate `checks_performed` with a check that never informed
        # anything.
        #
        # Unconditional across all three modes. The spec's rule carries no mode qualifier, and `audit`'s
        # "always returns approved" governs the verdict of an evaluation -- a suspended plan is never
        # evaluated, so no verdict exists for it to force. Suspension is a gate, not an outcome.
        if stored.get("suspended"):
            gov_logging.suspended_call_refused(
                plan_id=request.plan_id, tool="check_governance", mode=governance_mode()
            )
            return _suspended_check_response(request.plan_id)

        plan = stored["plan"]

        # M11. Only when the plan declares delegations; absent, the agent imposes no restriction.
        refusal = _delegation_refusal(request, plan)
        if refusal is not None:
            check_id = state.put_check(
                plan_id=request.plan_id,
                verdict="denied",
                explanation=refusal,
                payload={"caller": str(request.caller), "delegation_refused": True},
                findings=[],
                categories_evaluated=[],
            )
            gov_logging.verdict_denied(
                plan_id=request.plan_id,
                categories=[policy.CATEGORY_BUDGET],
                mode=governance_mode(),
            )
            return CheckGovernanceResponse.model_validate(
                {
                    "check_id": check_id,
                    "verdict": "denied",
                    "plan_id": request.plan_id,
                    "explanation": refusal,
                    "findings": [],
                    "categories_evaluated": [],
                    "mode": governance_mode(),
                }
            )

        # The cross-plan aggregate (M3). Read before evaluation because `policy.evaluate` is pure and
        # takes the result as an argument, so it cannot partially fail.
        identity = _aggregate_identity(request, stored, context)
        if identity is None:
            # The key could not be formed, so the fragmentation defense cannot cover this check. Reported
            # as an explicit unknown rather than skipped: an aggregate that is quietly not consulted looks
            # identical in the response to one that returned zero, and only one of those is safe.
            aggregate = aggregation.AggregateRead(
                None,
                "the (buyer_agent, seller_agent, account_id) identity for this plan could not be "
                "resolved, so trailing-window committed spend was not consulted",
            )
            gov_logging.aggregate_read_failed(
                plan_id=request.plan_id, reason="identity unresolved"
            )
        else:
            buyer_agent, seller_agent, account_id = identity
            aggregate = await aggregation.read(
                buyer_agent=buyer_agent, seller_agent=seller_agent, account_id=account_id
            )
            if not aggregate.known:
                gov_logging.aggregate_read_failed(
                    plan_id=request.plan_id, reason=aggregate.reason or "unknown"
                )

        evaluation = policy.evaluate(
            plan=plan,
            action=_action_from_request(request),
            already_committed=state.committed_total(request.plan_id),
            aggregate=aggregate,
            aggregate_threshold=_aggregate_threshold_for(request, plan),
            aggregation_window_days=aggregation.AGGREGATION_WINDOW_DAYS,
        )

        findings_wire = [f.to_wire() for f in evaluation.findings]
        check_id = state.put_check(
            plan_id=request.plan_id,
            verdict=evaluation.verdict,
            explanation=evaluation.explanation,
            payload={"caller": str(request.caller), "tool": getattr(request, "tool", None) or ""},
            # Persisted so `get_plan_audit_logs` reports the findings this check actually produced,
            # rather than re-running today's policy over yesterday's decision.
            findings=findings_wire,
            categories_evaluated=list(evaluation.categories_evaluated),
            policies_evaluated=list(evaluation.policies_evaluated),
        )

        if evaluation.escalate:
            # The trailing-window aggregate crossed its threshold. The spec's consequence is escalation
            # to human review, so the plan suspends and this call is refused -- by the time we answer,
            # the plan IS suspended, and M1/M2's rule is unconditional.
            #
            # The verdict recorded above is the agent's pre-review RECOMMENDATION, not a final answer:
            # `resolve_escalation` overwrites it with what the human decides, which is also what makes
            # `human_override_rate` meaningful.
            reason = "Trailing-window committed spend crossed the escalation threshold."
            state.put_escalation(plan_id=request.plan_id, check_id=check_id, reason=reason)
            state.put_plan(plan, requires_human_review=True)
            gov_logging.escalation_opened(
                plan_id=request.plan_id, check_id=check_id, reason=reason
            )
            gov_logging.suspended_call_refused(
                plan_id=request.plan_id, tool="check_governance", mode=governance_mode()
            )
            return _suspended_check_response(request.plan_id, check_id=check_id)

        if evaluation.verdict == "denied":
            gov_logging.verdict_denied(
                plan_id=request.plan_id,
                categories=[f.category_id for f in evaluation.findings if f.severity == "critical"],
                mode=governance_mode(),
            )

        response: dict[str, Any] = {
            "check_id": check_id,
            "verdict": evaluation.verdict,
            "plan_id": request.plan_id,
            "explanation": evaluation.explanation,
            "findings": findings_wire,
            "categories_evaluated": list(evaluation.categories_evaluated),
            # Empty by construction: this agent evaluates plan parameters, not policy text. A policy
            # resolved as applicable appears in `resolved_policies` at sync time; naming it here would
            # assert an evaluation that needs a semantic evaluator this agent does not have.
            "policies_evaluated": list(evaluation.policies_evaluated),
            "mode": governance_mode(),
        }
        if evaluation.conditions:
            response["conditions"] = [c.to_wire() for c in evaluation.conditions]

        # ONLY `approved` gets a `governance_context`. `conditions` gets a `consultation_context`.
        #
        # The spec is explicit (docs/governance/campaign/responsibilities): "Only approved returns a
        # governance_context token. […] A conditions response instead carries consultation_context, a
        # non-authorizing negotiation handle that the orchestrator returns only with the adjusted intent
        # re-check." And on the seller-side path: "a conditions response is invalid on this path and does
        # not authorize a commit."
        #
        # This used to issue a `governance_context` for both, which handed the authorising artefact to a
        # verdict that withholds authorisation -- the same reasoning that already (correctly) withholds it
        # from a denial, one step short. Both tokens are still signed and still bound to the plan and
        # target seller; the difference is what the name licenses the holder to do, and a seller that
        # accepts a token cannot tell from the bytes which verdict produced it.
        #
        # `consultation_context` is not in the SDK's response model (absent from 6.6.0 and 7.0.2 --
        # SDK-GAPS.md gap 5); it travels because `CheckGovernanceResponse` is `extra="allow"`.
        if evaluation.verdict in ("approved", "conditions"):
            issued = jws.issue_governance_context(
                plan_id=request.plan_id,
                # `aud` is the TARGET SELLER, not the caller.
                #
                # The spec: "aud is bound byte-for-byte to the target seller", and an orchestrator
                # fanning one plan out to several sellers "produce[s] one intent token per seller"
                # precisely because of that binding. On a buyer's intent check the caller is the buyer,
                # so using `caller` here would mint a token addressed to the buyer itself -- which every
                # conforming seller would reject, since its verification checklist requires `aud` to be
                # itself.
                #
                # Coerced to `str` explicitly rather than relying on a serialiser default, so the claim
                # is the exact byte string a seller compares against.
                audience=_token_audience(request),
                phase=_phase_for(request),
                plan=plan,
                issuer=issuer_url(),
            )
            claims = jws.read_governance_context(issued)
            if evaluation.verdict == "approved":
                response["governance_context"] = issued
            else:
                response["consultation_context"] = issued
            response["expires_at"] = _iso(claims["exp"])

        return CheckGovernanceResponse.model_validate(response)

    # ------------------------------------------------------- report_plan_outcome

    @_idempotency_store.wrap
    async def handle_report_plan_outcome(
        self, request: ReportPlanOutcomeRequest, context: Any | None = None
    ) -> ReportPlanOutcomeResponse:
        stored = state.get_plan(request.plan_id)

        # M12, before the outcome is recorded: a caller presenting the wrong credential must not be able
        # to write to another account's plan, which would corrupt both its committed total and its
        # aggregate.
        if stored is not None and _account_mismatch(stored, context):
            return ReportPlanOutcomeResponse.model_validate(
                _with_error_layers(
                    {
                        "outcome_id": f"refused_{request.plan_id}",
                        "outcome_state": "findings",
                    },
                    _account_mismatch_error(request.plan_id),
                )
            )

        # The spec names both tasks in the same unconditional sentence, so the gate is identical to
        # `check_governance`'s and applies in every mode.
        if stored is not None and stored.get("suspended"):
            gov_logging.suspended_call_refused(
                plan_id=request.plan_id, tool="report_plan_outcome", mode=governance_mode()
            )
            # `outcome_id` and `outcome_state` are required, so a refusal carries both. `findings` is the
            # accurate state -- an issue was detected and nothing was recorded, which `accepted` would
            # misreport as a clean write.
            return ReportPlanOutcomeResponse.model_validate(
                _with_error_layers(
                    {
                        "outcome_id": f"suspended_{request.plan_id}",
                        "outcome_state": "findings",
                    },
                    _suspended_error(request.plan_id),
                )
            )

        seller_response = getattr(request, "seller_response", None)
        seller = (
            seller_response
            if isinstance(seller_response, dict)
            else seller_response.model_dump(mode="json", exclude_none=True)
            if seller_response is not None
            else {}
        )

        # The seller's OWN confirmed amount, which may differ from what was requested. AdCP commits the
        # seller's figure and reports the difference as a finding, so using the requested amount here
        # would misstate the plan's remaining authority in exactly the case worth catching.
        committed = seller.get("committed_budget")
        if committed is None and isinstance(seller.get("packages"), list):
            amounts = [
                p.get("budget", {}).get("total") if isinstance(p.get("budget"), dict) else p.get("budget")
                for p in seller["packages"]
            ]
            usable = [float(a) for a in amounts if isinstance(a, (int, float)) and not isinstance(a, bool)]
            committed = sum(usable) if usable else None

        # U4/Part 1: a "delivery" outcome carries no seller_response at all -- its real content is the
        # `delivery` object (reporting_period, impressions, spend, cpm, viewability_rate,
        # completion_rate). Capture it into `detail` alongside seller_response, or handle_get_plan_
        # audit_logs's outcome-type entries would have nothing to project for it -- confirmed live gap,
        # not present before this fix.
        delivery_field = getattr(request, "delivery", None)
        delivery = (
            delivery_field
            if isinstance(delivery_field, dict)
            else delivery_field.model_dump(mode="json", exclude_none=True)
            if delivery_field is not None
            else None
        )

        detail: dict[str, Any] = {"seller_response": seller}
        if delivery is not None:
            detail["delivery"] = delivery

        outcome_id = state.put_outcome(
            plan_id=request.plan_id,
            outcome=str(request.outcome),
            committed_budget=float(committed) if committed is not None else None,
            check_id=getattr(request, "check_id", None),
            detail=detail,
        )

        # Commit to the cross-plan aggregate (M3.3): budget is consumed by confirmed outcomes, never by
        # approved checks. An approval that is never acted on must not hold authority forever, and with
        # no decrement path a commit made at approval time could never be released.
        #
        # Only outcomes that actually committed something contribute. A `failed` outcome consumed no
        # budget, and per M3.8 a denied commit does not enter the aggregate either.
        if (
            stored is not None
            and committed is not None
            and str(request.outcome) == "completed"
        ):
            identity = _aggregate_identity(request, stored, context)
            if identity is None:
                # Nothing to key the commit under. Logged rather than dropped silently: a commit missing
                # from the aggregate is a permanent hole in the defense, since there is no backfill path.
                gov_logging.aggregate_read_failed(
                    plan_id=request.plan_id,
                    reason="identity unresolved; committed amount not added to the aggregate",
                )
            else:
                buyer_agent, seller_agent, account_id = identity
                aggregation.commit(
                    buyer_agent=buyer_agent,
                    seller_agent=seller_agent,
                    account_id=account_id,
                    amount=float(committed),
                    # The outcome's own timestamp, so a late or replayed report lands on the day it
                    # belongs to rather than today.
                    occurred_at=_as_datetime(getattr(request, "timestamp", None))
                    or policy.utc_now(),
                )

        response: dict[str, Any] = {"outcome_id": outcome_id, "outcome_state": "accepted"}
        if committed is not None:
            response["committed_budget"] = float(committed)

        if stored is not None:
            total = _amount(stored["plan"].get("budget", {}).get("total"))
            spent = state.committed_total(request.plan_id)
            if total is not None:
                # Exactly the two fields AdCP's `PlanSummary` defines. A third would be rejected, and
                # guessing at names is how a response ends up valid-looking and unparseable.
                response["plan_summary"] = {
                    "total_committed": spent,
                    "budget_remaining": total - spent,
                }
        return ReportPlanOutcomeResponse.model_validate(response)

    # ------------------------------------------------------ get_plan_audit_logs

    async def handle_get_plan_audit_logs(
        self, request: GetPlanAuditLogsRequest, context: Any | None = None
    ) -> GetPlanAuditLogsResponse:
        plan_ids = list(getattr(request, "plan_ids", None) or [])
        include_entries = bool(getattr(request, "include_entries", False))

        plans: list[AuditPlan] = []
        truncated_plans: list[tuple[str, int]] = []
        for plan_id in plan_ids:
            stored = state.get_plan(plan_id)
            if stored is None:
                # OMITTED, and this is a limitation of the response shape rather than a choice I like.
                # AdCP's audit-log `Plan` requires `plan_version`, `budget`, `summary` and
                # `governed_actions`; none of those exist for a plan that was never synced, and a
                # version for a plan that does not exist is worse than saying nothing. A caller can
                # diff the ids it asked for against the ids it got back.
                continue

            checks = sorted(state.list_checks(plan_id), key=lambda c: c["created_at"])
            outcomes = state.list_outcomes(plan_id)
            total = _amount(stored["plan"].get("budget", {}).get("total"))
            spent = state.committed_total(plan_id)

            escalations = state.list_escalations(plan_id)

            budget_kwargs: dict[str, Any] = {"committed": spent}
            if total is not None:
                budget_kwargs["authorized"] = total
                budget_kwargs["remaining"] = total - spent
                # Guarded: a zero-authority plan would divide by zero, and reporting 0% utilisation of a
                # zero budget is meaningless rather than merely wrong.
                if total > 0:
                    budget_kwargs["utilization_pct"] = round(spent / total * 100, 2)

            audit_plan_kwargs: dict[str, Any] = {
                "plan_id": plan_id,
                "plan_version": stored["version"],
                "status": _plan_status(stored),
                "budget": AuditBudget(**budget_kwargs),
                "summary": _plan_summary(
                    checks=checks, outcomes=outcomes, escalations=escalations
                ),
                # Empty until a governed action carries its own context through to an outcome. An
                # entry here would claim a buy that never happened.
                "governed_actions": [],
            }

            if include_entries:
                # Resolved escalations record the human's decision; the check they hang off records the
                # agent's pre-review recommendation.
                check_entries = [
                    Entry(
                        id=check["check_id"],
                        type="check",
                        timestamp=_iso(check["created_at"]),
                        # AdCP's own discriminator, literally 'intent' or 'execution'. Derived from
                        # whether a tool name was recorded, which is exactly what distinguishes an
                        # orchestrator's intent check from a seller's execution check — and it is what
                        # tells the journey view's Govern panel from its Score panel.
                        check_type="intent" if check.get("payload", {}).get("tool") else "execution",
                        verdict=check["verdict"],
                        explanation=check["explanation"],
                        mode=governance_mode(),
                        tool=check.get("payload", {}).get("tool") or None,
                        caller=check.get("payload", {}).get("caller") or None,
                        # M4.2. `findings` absent (not empty) for a check written before findings were
                        # persisted, because "none found" and "not recorded" are different facts.
                        findings=_audit_findings(check.get("findings")),
                        categories_evaluated=check.get("categories_evaluated") or None,
                        policies_evaluated=check.get("policies_evaluated") or None,
                    )
                    for check in checks
                ]
                # U4/Part 1: outcome-type entries, previously never emitted even though the data has
                # been stored via state.put_outcome since U2. `outcome_status` is this schema's own
                # generic status field for an outcome entry (distinct from `verdict`, which is
                # check-entry-only) -- "accepted" mirrors handle_report_plan_outcome's own response
                # shape (`outcome_state`), the only status this handler currently ever assigns an
                # outcome.
                outcome_entries = [
                    Entry(
                        id=outcome["outcome_id"],
                        type="outcome",
                        timestamp=_iso(outcome["created_at"]),
                        outcome=outcome["outcome"],
                        outcome_status="accepted",
                        # M4.1. Absent when the outcome reported no amount, which is not the same as
                        # an outcome that committed zero.
                        committed_budget=(
                            float(outcome["committed_budget"])
                            if outcome.get("committed_budget") is not None
                            else None
                        ),
                    )
                    for outcome in outcomes
                ]
                ordered = sorted(check_entries + outcome_entries, key=lambda e: e.timestamp)
                # NFR-11. Most recent retained: a caller reading an audit trail is answering "what
                # happened lately", and silently dropping the newest entries would answer the opposite
                # question while looking complete.
                if len(ordered) > MAX_AUDIT_ENTRIES:
                    truncated_plans.append((plan_id, len(ordered)))
                    ordered = ordered[-MAX_AUDIT_ENTRIES:]
                audit_plan_kwargs["entries"] = ordered

            plans.append(AuditPlan(**audit_plan_kwargs))

        response_kwargs: dict[str, Any] = {"plans": plans}
        if truncated_plans:
            # Declared, not implied. A truncated list that says nothing about being truncated is the
            # one failure mode here that a caller cannot detect: 200 entries looks like a complete
            # trail of 200 entries.
            response_kwargs["message"] = "; ".join(
                f"Plan {plan_id}: {total_entries} entries recorded, most recent "
                f"{MAX_AUDIT_ENTRIES} returned"
                for plan_id, total_entries in truncated_plans
            )
        return GetPlanAuditLogsResponse.model_validate(response_kwargs)

    # --------------------------------------------------- get_adcp_capabilities

    async def get_adcp_capabilities(
        self, params: GetAdcpCapabilitiesRequest | dict[str, Any], context: Any | None = None
    ) -> GetAdcpCapabilitiesResponse:
        """Declare what this agent actually does, including its aggregation window (M9).

        **`governance.aggregation_window_days` is why this override exists.** The field's own schema
        description makes omission meaningful rather than merely quiet: a buyer that sees no declared
        window "MUST assume per-commit evaluation only (the fragmentation attack surface is open)", and
        30 "is not implied by omission". So without this declaration the trailing-window aggregate is
        unobservable — it would defend against fragmentation while every buyer was entitled to conclude
        it does not.

        The value is read from `aggregation.AGGREGATION_WINDOW_DAYS`, the same constant the aggregate
        actually queries over, so the declaration cannot drift from the behaviour. A test asserts the
        two are equal for exactly that reason.

        `supported_protocols` claims `governance` only. Per the schema that claim also commits the agent
        to the governance compliance storyboard, which is a commitment about the protocol it implements;
        claiming `media_buy` or `signals` would be a claim about protocols it does not.
        """
        # Release precision, not patch: `supported_versions` is patterned `^\\d+\\.\\d+...$`, and the
        # SDK reports its spec version as MAJOR.MINOR.PATCH ("3.1.1"). Derived rather than hardcoded so
        # an SDK bump that moves the spec version is reflected without an edit here.
        spec_version = get_adcp_spec_version()
        parts = spec_version.split(".")
        release = ".".join(parts[:2])
        return GetAdcpCapabilitiesResponse(
            adcp=Adcp(
                # Deprecated in favour of `supported_versions`, but the schema is explicit that servers
                # MUST keep emitting it through 3.x.
                major_versions=[int(parts[0])],
                supported_versions=[release],
                # `supported=True` because this agent really does deduplicate: `report_plan_outcome` is
                # wrapped in the SDK's `IdempotencyStore` (M8). The window is read from the same
                # constant the store is built with, so the declaration cannot promise a replay window
                # the store does not honour.
                idempotency=Idempotency(
                    supported=True,
                    replay_ttl_seconds=idempotency_backend.REPLAY_TTL_SECONDS,
                ),
                # `build_version` omitted: it means the semver of THIS deployment, which is not the
                # SDK's version. Reporting the SDK's would misattribute it.
            ),
            supported_protocols=["governance"],
            governance=Governance(
                aggregation_window_days=aggregation.AGGREGATION_WINDOW_DAYS,
                # Declared because `handle_get_creative_features` really evaluates these three, and an
                # undeclared capability is invisible: a buyer has no way to discover the evaluation
                # exists. Previously omitted -- an omission rather than a false claim, but a buyer
                # cannot act on either.
                #
                # Exactly the three the handler computes, no more. Declaring a feature this agent does
                # not measure would be the worse failure: a buyer would submit creatives expecting an
                # assessment that never comes back.
                creative_features=_CREATIVE_FEATURES,
            ),
        )

    # -------------------------------------------------------------- unsupported

    async def handle_get_creative_features(
        self, request: GetCreativeFeaturesRequest, context: Any | None = None
    ) -> dict[str, Any]:
        """U4/Part 1: a real evaluation over a real submitted creative manifest (R22).

        Every feature returned here is genuinely computed from the fetched image's own pixel data --
        never a hardcoded verdict. Three real, independently-measurable features, chosen because each
        has a real computation this agent can perform without a third-party vendor call:

        - `dimension_conformance`: measured width/height vs. the manifest's own declared `format_id`
          dimensions (the format the buyer said this asset is for) -- a real equality check, not an
          assumption that a submitted asset matches its claimed format.
        - `contrast_ratio`: the real WCAG 2.x contrast-ratio formula (same formula
          `fixtures/creatives/gen_fixtures.py` used to BUILD the reference fixtures and assert their
          own properties before saving them -- this handler and that generator must agree on what
          "real" means here, or the fixtures would not test anything). Sampled from the image's own
          two most common colors (a crude but genuine text/background approximation for a flat-color
          banner; a production evaluator would need real text-region detection, out of scope for this
          fixture-scale agent -- see this method's own limitation note below).
        - `urgency_claim`: real OCR text extraction (pytesseract/tesseract-ocr) over the fetched image,
          checked against a small set of genuine urgency/scarcity phrases. A real absence-of-detection
          on a clean image is a real "false", not an omission.

        On a fetch failure or an unsupported asset type, returns the error variant (`errors: [...]`)
        rather than fabricating a result — this agent's fail-honestly discipline applies to creative
        evaluation the same way it applies to every other handler here.

        **Always terminal here**, carrying either `results` or `errors`.

        That is a fact about THIS agent's workload, not a rule about the task. AdCP's creative-governance
        page does describe a `working` path for genuinely slow evaluation ("sandboxed execution for
        malware scanning"), with results delivered over the standard webhook mechanism. Two reasons it is
        not what this handler does:

        - The work is an HTTPS fetch of a fixture image plus three local measurements — well inside the
          30-second rule's `completed`-inline band.
        - This agent implements no webhook delivery, and `working` without it leaves the caller with
          nothing to wait for. The old code returned `working` and expected the caller to retry, which
          the async-operations page rules out in terms: "Don't poll for `working`".

        Note that a conformant `working` would still need `results` (an empty array is valid) — the
        response union has no variant carrying neither `results` nor `errors`, which is what made the old
        body unrepresentable. See the fetch block below.
        """
        manifest = request.creative_manifest
        assets = manifest.assets if hasattr(manifest, "assets") else manifest.get("assets", {})
        main_image = assets.get("main_image") if isinstance(assets, dict) else getattr(assets, "main_image", None)
        if main_image is None:
            return _creative_features_error(
                "VALIDATION_ERROR",
                "creative_manifest.assets.main_image is required for evaluation",
            )
        image_dict = main_image if isinstance(main_image, dict) else main_image.model_dump(mode="json")
        asset_url = image_dict.get("url")
        if not asset_url:
            return _creative_features_error("VALIDATION_ERROR", "asset has no url to fetch")

        format_id = manifest.format_id if hasattr(manifest, "format_id") else manifest.get("format_id")
        format_dict = format_id if isinstance(format_id, dict) else format_id.model_dump(mode="json")

        # The FORMAT's own required dimensions -- see adcp_get_creative_features's docstring for why
        # this must be a separate value from the asset's own claimed width/height (`image_dict`,
        # below): checking a submitted asset against its own self-reported size would trivially
        # "pass" a mis-sized image whose metadata happens to (honestly or not) match itself. Carried
        # in `ext` since AdCP's creative-manifest schema has no dedicated field for "the format this
        # manifest targets' own required dimensions" -- that lives on the Format object returned by
        # list_creative_formats, not on a submitted manifest.
        ext = manifest.ext if hasattr(manifest, "ext") else manifest.get("ext")
        ext_dict = ext if isinstance(ext, dict) else ext.model_dump(mode="json") if ext else {}
        format_dimensions = ext_dict.get("format_dimensions") or {}

        # Fetch, then evaluate on THIS call. The cache is a performance optimisation, not a status.
        #
        # This branch used to return `{"status": "working", "message": ...}` on a cold cache and expect
        # the caller to retry. Two things were wrong with that, and the first is why every real call
        # failed:
        #
        # 1. That payload is unrepresentable. `GetCreativeFeaturesResponse` is a two-variant union --
        #    variant 1 requires `results`, variant 2 requires `errors` -- so a body carrying neither
        #    matches no variant, and the SDK rejected it with
        #    `VALIDATION_ERROR[/]: oneOf composition failed`. Found live, not by inspection.
        # 2. The retry it asked for is not AdCP's async contract. Per the 30-second rule
        #    (docs/building/implementation/async-operations): under-30-second work returns `completed`
        #    with the result inline; `working` is an out-of-band progress signal sent while the server
        #    keeps processing, and the spec says in terms "Don't poll for `working`". For this task
        #    specifically, docs/governance/creative pairs `working` with WEBHOOK delivery, not polling.
        #    An HTTPS image fetch is sub-30s work and this agent has no webhook path, so neither applies.
        #
        # So: await the fetch and answer for real. `_fetch_and_cache` records failures as the exception
        # object, which the branch below reports as the error variant.
        cached = _creative_fetch_cache.get(asset_url)
        if cached is None:
            inflight = _creative_fetch_inflight.get(asset_url)
            if inflight is None:
                inflight = asyncio.create_task(_fetch_and_cache(asset_url))
                _creative_fetch_inflight[asset_url] = inflight
            # Shielded so a concurrent caller awaiting the same task cannot cancel the shared fetch.
            await asyncio.shield(inflight)
            cached = _creative_fetch_cache.get(asset_url)
        if cached is None:
            # The fetch finished without recording either bytes or an exception. Reported rather than
            # retried: an unexplained empty cache is a real unknown, and a second attempt here would
            # loop on whatever caused it.
            return _creative_features_error(
                "UNSUPPORTED_FEATURE",
                f"creative asset fetch for {asset_url} produced no result",
            )
        if isinstance(cached, Exception):
            _log.warning("get_creative_features: fetch failed for %s", asset_url, exc_info=cached)
            return _creative_features_error(
                "UNSUPPORTED_FEATURE", f"could not fetch creative asset: {cached}"
            )
        image_bytes = cached

        try:
            image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        except Exception as exc:  # noqa: BLE001 - any decode failure is reported, never raised out
            _log.warning("get_creative_features: image decode failed", exc_info=True)
            return _creative_features_error(
                "UNSUPPORTED_FEATURE", f"could not decode image: {exc}"
            )

        now = datetime.now(timezone.utc).isoformat()
        # `CreativeFeatureResult` is `extra="forbid"`, so each of these is constructed rather than
        # hand-built: a misspelled field name fails on the line that writes it.
        results: list[CreativeFeatureResult] = []

        # dimension_conformance: real equality between the FETCHED image's own measured pixels and
        # the FORMAT's required dimensions (format_dimensions, above) -- not the asset's own claimed
        # width/height, which would make this check unable to ever catch a mis-sized asset whose own
        # metadata (honestly or not) matches its actual size. `wrong_size_banner.png` genuinely fails
        # this; the other three fixtures genuinely pass it.
        required_width = format_dimensions.get("width")
        required_height = format_dimensions.get("height")
        if required_width and required_height:
            conforms = image.width == required_width and image.height == required_height
            results.append(
                CreativeFeatureResult(
                    feature_id="dimension_conformance",
                    value=conforms,
                    confidence=1.0,
                    measured_at=now,
                    details={
                        "measured_width": image.width,
                        "measured_height": image.height,
                        "format_required_width": required_width,
                        "format_required_height": required_height,
                    },
                )
            )

        # contrast_ratio: real WCAG luminance-based contrast between the image's two most common
        # colors -- see this method's docstring for why this is a crude approximation, not full
        # text-region detection.
        contrast = _measure_dominant_contrast(image)
        if contrast is not None:
            results.append(
                CreativeFeatureResult(
                    feature_id="contrast_ratio",
                    value=round(contrast, 2),
                    unit="ratio",
                    confidence=0.7,
                    measured_at=now,
                    details={
                        "methodology": "WCAG 2.x relative luminance contrast between the image's two "
                        "most frequent colors (foreground/background approximation for a flat-color "
                        "banner)",
                        "wcag_aa_threshold": 4.5,
                    },
                )
            )

        # urgency_claim: real OCR text extraction, checked against real urgency/scarcity phrases.
        detected_text = pytesseract.image_to_string(image).strip()
        urgency_hit = _detect_urgency_claim(detected_text)
        results.append(
            CreativeFeatureResult(
                feature_id="urgency_claim",
                value=urgency_hit is not None,
                confidence=0.85 if detected_text else 0.5,
                measured_at=now,
                methodology_version="ocr-tesseract-v1",
                # No match means no `details`. An empty details object would imply a measurement was
                # recorded and found nothing to report, which is a different claim.
                details={"matched_phrase": urgency_hit} if urgency_hit else None,
            )
        )

        return _creative_features_result(results)

    async def handle_create_property_list(
        self, request: CreatePropertyListRequest, context: Any | None = None
    ) -> Any:
        return self._unsupported("create_property_list")

    async def handle_get_property_list(
        self, request: GetPropertyListRequest, context: Any | None = None
    ) -> Any:
        return self._unsupported("get_property_list")

    async def handle_list_property_lists(
        self, request: ListPropertyListsRequest, context: Any | None = None
    ) -> Any:
        return self._unsupported("list_property_lists")

    async def handle_update_property_list(
        self, request: UpdatePropertyListRequest, context: Any | None = None
    ) -> Any:
        return self._unsupported("update_property_list")

    async def handle_delete_property_list(
        self, request: DeletePropertyListRequest, context: Any | None = None
    ) -> Any:
        return self._unsupported("delete_property_list")

    @staticmethod
    def _unsupported(task: str) -> NotImplementedResponse:
        """Reports the task as unsupported, with the reason.

        Property lists are a separate governance specialism and creative feature evaluation needs an
        evaluator this agent does not have. A caller can act on "not supported"; a pass from an
        evaluator that does not exist tells it nothing.
        """
        return NotImplementedResponse(
            supported=False,
            reason=(
                f"This reference governance agent implements campaign governance "
                f"(sync_plans, check_governance, report_plan_outcome, get_plan_audit_logs). "
                f"{task} is not implemented."
            ),
            error=Error(code="NOT_IMPLEMENTED", message=f"{task} is not supported by this agent"),
        )


# --------------------------------------------------------------------- audit-log helpers (Step 15)

#: NFR-11 / C2. The cap on `entries[]` per plan.
MAX_AUDIT_ENTRIES = 200

#: The resolution vocabulary. AdCP's own `Escalation.resolution` description gives
#: `'approved_by_human'` / `'rejected_by_human'` as its examples, so this follows the spec's examples
#: rather than inventing a vocabulary. `resolve_escalation.py` writes exactly these values; a
#: resolution outside this map is not classified rather than guessed, which is why
#: `human_override_rate` can be absent while `escalation_rate` is present.
_HUMAN_APPROVALS = ("approved_by_human",)
_HUMAN_REJECTIONS = ("rejected_by_human",)


def _plan_status(stored: dict[str, Any]) -> str:
    """The plan's lifecycle status, from `Status` = `active | suspended | completed`.

    Precedence is suspended, then completed, then active:

    - **suspended** first because it is the state with a remedy. A suspended plan past its flight end
      still has an escalation a human has to close, and reporting it as `completed` would hide that.
    - **completed** when the plan's authorised flight window has ended. `flight.end` is a required
      field on AdCP's plan, and once it has passed `check_flight` rejects every action, so the plan can
      never authorise anything again. Reporting `active` would claim authority that no longer exists.
    - **active** otherwise.

    Note what is deliberately NOT used: budget exhaustion. A fully-committed plan may still be
    modified, and a plan with budget left that the buyer simply stopped using is not complete. AdCP has
    no task that marks a plan complete (`sync_plans` `status` is the sync result, `active | error`), so
    the flight window is the only completion signal the protocol actually provides.
    """
    if stored.get("suspended"):
        return "suspended"
    flight = (stored.get("plan") or {}).get("flight") or {}
    # Reusing `policy.as_date` rather than parsing here: this reads the same flight window
    # `check_flight` enforces, and two parsers would eventually disagree about the same plan.
    end = policy.as_date(flight.get("end_date") or flight.get("end"))
    if end is not None and end < datetime.now(timezone.utc).date():
        return "completed"
    return "active"


def _audit_findings(stored: Any) -> list[AuditFinding] | None:
    """Project stored findings onto the audit-log `Finding`.

    SDK-GAP-4: the audit `Finding` is NOT the `check_governance` response `Finding`, despite the
    schema's own description saying "same structure as check_governance response findings". The audit
    variant has five fields and sets `extra='forbid'`,
    so `details`, `source_plan_id` and `uncertainty_reason` are rejected. Constructing the type is what
    enforces that -- there is no hand-written field list here, and an SDK release that widens the
    audit variant is picked up by naming the new field rather than by editing a copy of its field set.

    Returns None for a check with no persisted findings, so the entry omits the field instead of
    asserting an evaluated zero.
    """
    if not isinstance(stored, list):
        return None
    return [
        AuditFinding(
            category_id=finding["category_id"],
            policy_id=finding.get("policy_id"),
            severity=finding["severity"],
            explanation=finding["explanation"],
            confidence=finding.get("confidence"),
        )
        for finding in stored
        if isinstance(finding, dict)
    ]


def _plan_summary(
    *,
    checks: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
    escalations: list[dict[str, Any]],
) -> Summary:
    """The plan's aggregate statistics, every figure derived from the stored records.

    Nothing here is estimated: a metric with no data to compute it from is absent, which is why
    `Summary`'s fields are individually optional.
    """
    verdicts = _count_by(checks, "verdict")
    escalated_check_ids = {e["check_id"] for e in escalations if e.get("check_id")}
    reviewed_check_ids = {
        e["check_id"] for e in escalations if e.get("check_id") and e.get("resolved_at")
    }

    findings_count = 0
    confidences: list[float] = []
    for check in checks:
        stored_findings = check.get("findings")
        if not isinstance(stored_findings, list):
            continue
        findings_count += len(stored_findings)
        for finding in stored_findings:
            confidence = _amount(finding.get("confidence")) if isinstance(finding, dict) else None
            if confidence is not None:
                confidences.append(confidence)

    return Summary(
        checks_performed=len(checks),
        outcomes_reported=len(outcomes),
        # Counted from the verdicts actually recorded, so this cannot disagree with the entries it
        # summarises. `human_reviewed` is supplementary per the SDK's own description -- these checks
        # are ALSO counted in approved or denied, so the three verdict counts still sum to
        # `checks_performed`.
        statuses=Statuses(
            approved=verdicts.get("approved", 0),
            denied=verdicts.get("denied", 0),
            conditions=verdicts.get("conditions", 0),
            human_reviewed=len(reviewed_check_ids),
        ),
        findings_count=findings_count,
        escalations=[
            Escalation(
                check_id=escalation["check_id"],
                reason=escalation["reason"],
                resolution=escalation.get("resolution"),
                resolved_at=(
                    _iso(escalation["resolved_at"]) if escalation.get("resolved_at") else None
                ),
            )
            for escalation in sorted(escalations, key=lambda e: e.get("created_at", 0))
        ],
        drift_metrics=_drift_metrics(
            checks=checks,
            escalations=escalations,
            escalated_check_ids=escalated_check_ids,
            confidences=confidences,
        ),
    )


def _drift_metrics(
    *,
    checks: list[dict[str, Any]],
    escalations: list[dict[str, Any]],
    escalated_check_ids: set[str],
    confidences: list[float],
) -> DriftMetrics | None:
    """Aggregate oversight metrics, or None when there are no checks to compute them over.

    Two SDK fields are deliberately absent:

    - **`escalation_rate_trend`** needs a band for what counts as `stable`, and that band is a
      judgment about the organisation's tolerance rather than a measurement. Classifying a rate change
      as `stable` on a threshold this agent picked for itself would report an opinion as an
      observation.
    - **`thresholds`** is, by its own description, set by the organisation in governance agent
      configuration. This agent has no such configuration, so there is nothing to echo.
    """
    if not checks:
        return None

    metrics: dict[str, Any] = {
        "escalation_rate": round(len(escalated_check_ids) / len(checks), 4),
        # "Approved without human intervention" -- an approval on a check that was escalated had human
        # intervention by definition, whether or not the human has answered yet.
        "auto_approval_rate": round(
            sum(
                1
                for check in checks
                if check.get("verdict") == "approved"
                and check.get("check_id") not in escalated_check_ids
            )
            / len(checks),
            4,
        ),
    }
    if confidences:
        metrics["mean_confidence"] = round(sum(confidences) / len(confidences), 4)

    override_rate = _human_override_rate(checks=checks, escalations=escalations)
    if override_rate is not None:
        metrics["human_override_rate"] = override_rate
    return DriftMetrics(**metrics)


def _human_override_rate(
    *, checks: list[dict[str, Any]], escalations: list[dict[str, Any]]
) -> float | None:
    """Fraction of resolved escalations where the human disagreed with the agent's recommendation.

    Denominator is resolved escalations whose resolution is in the known vocabulary AND whose check is
    still on record: an unresolved escalation has no human decision yet, and an unrecognised resolution
    string has no comparable meaning. Returns None when that leaves nothing to divide by, rather than
    reporting 0.0 -- "no overrides" and "no resolved escalations" are different facts, and only the
    first is evidence about calibration.
    """
    # `recommended_verdict` when the check has been reviewed, because by then `verdict` holds the HUMAN's
    # decision -- comparing that against itself would report 0.0 overrides for every plan.
    verdict_by_check = {
        check["check_id"]: check.get("recommended_verdict") or check.get("verdict")
        for check in checks
        if check.get("check_id")
    }
    comparable = 0
    overridden = 0
    for escalation in escalations:
        resolution = escalation.get("resolution")
        if not escalation.get("resolved_at") or not resolution:
            continue
        if resolution in _HUMAN_APPROVALS:
            human_allowed = True
        elif resolution in _HUMAN_REJECTIONS:
            human_allowed = False
        else:
            continue
        recommended = verdict_by_check.get(escalation.get("check_id", ""))
        if recommended is None:
            continue
        comparable += 1
        agent_allowed = recommended in ("approved", "conditions")
        if agent_allowed != human_allowed:
            overridden += 1
    if not comparable:
        return None
    return round(overridden / comparable, 4)


def _count_by(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    """Tally a field's values. Derived from the records themselves, so a summary cannot disagree with
    the entries it summarises."""
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key, "") or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return counts


def _amount(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _iso(epoch_seconds: int) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(int(epoch_seconds), tz=timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


# --------------------------------------------------------- get_creative_features helpers (U4/Part 1)


#: What `get_adcp_capabilities` declares under `governance.creative_features`, and it must stay the set
#: `handle_get_creative_features` actually computes -- `test_creative_features_and_outcome_entries.py`
#: asserts the two agree, so adding a feature to one without the other fails rather than shipping a
#: capability claim the handler cannot honour.
#:
#: Built through the SDK's `CreativeFeature` (`feature_id` and `type` are required; `type` is an enum of
#: binary/quantitative/categorical), so a wrong type fails here rather than being dropped by the
#: capabilities model's `extra="ignore"`.
_CREATIVE_FEATURES: list[CreativeFeature] = [
    CreativeFeature(
        feature_id="dimension_conformance",
        type="binary",
        description=(
            "Whether the fetched asset's measured pixel dimensions equal the dimensions the target "
            "format requires. Measured from the image itself, not from the asset's self-reported size."
        ),
    ),
    CreativeFeature(
        feature_id="contrast_ratio",
        type="quantitative",
        # The WCAG 2.x contrast ratio's own defined bounds: 1:1 for identical colours, 21:1 for pure
        # black on pure white. Not a confidence or a percentage.
        range=CreativeFeatureRange(min=1.0, max=21.0),
        description=(
            "WCAG 2.x relative-luminance contrast ratio between the image's two most frequent colours, "
            "a foreground/background approximation for a flat-colour banner. AA threshold is 4.5."
        ),
    ),
    CreativeFeature(
        feature_id="urgency_claim",
        type="binary",
        description=(
            "Whether OCR-extracted text contains a known urgency or scarcity phrase. A real "
            "absence-of-detection on a clean asset is a real false, not an omission."
        ),
    ),
]


def _creative_features_error(code: str, message: str) -> dict[str, Any]:
    """The `errors` variant of `get_creative_features`'s response union.

    Built through the SDK type so the variant's own required field is enforced here. The status is
    `completed` because the TASK finished -- it finished by refusing, which is what `errors` says.
    """
    return CreativeFeaturesError.model_validate(
        {"status": "completed", "errors": [Error(code=code, message=message).model_dump(exclude_none=True)]}
    ).model_dump(mode="json", exclude_none=True)


def _creative_features_result(results: list[CreativeFeatureResult]) -> dict[str, Any]:
    """The `results` variant.

    `results` is required by this variant, so an empty list is still a valid body and a missing key is
    not -- the distinction the old `working` branch got wrong.
    """
    return CreativeFeaturesResult.model_validate(
        {"status": "completed", "results": [r.model_dump(mode="json", exclude_none=True) for r in results]}
    ).model_dump(mode="json", exclude_none=True)


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    """The real WCAG 2.x relative luminance formula -- byte-identical to the one
    `fixtures/creatives/gen_fixtures.py` used to build and self-assert the reference fixtures, so this
    handler and that generator agree on what "real" means here."""

    def _lin(component: int) -> float:
        c = component / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (_lin(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast_ratio(fg: tuple[int, int, int], bg: tuple[int, int, int]) -> float:
    """The real WCAG contrast-ratio formula: (L1 + 0.05) / (L2 + 0.05), lighter over darker."""
    l1, l2 = sorted((_relative_luminance(fg), _relative_luminance(bg)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def _measure_dominant_contrast(image: Image.Image) -> float | None:
    """A real (if crude) foreground/background approximation: the image's two most frequent colors,
    by pixel count. Genuine text-region detection is out of scope for this fixture-scale agent -- see
    `handle_get_creative_features`'s docstring -- but this still measures REAL pixel data, never a
    placeholder ratio. Downsamples first so large images don't cost an expensive full-resolution
    color count for what is, at fixture scale, a small number of distinct flat colors."""
    sample = image.copy()
    sample.thumbnail((150, 150))
    colors = sample.getcolors(maxcolors=256 * 256 * 256)
    if not colors or len(colors) < 2:
        return None
    colors.sort(key=lambda item: item[0], reverse=True)
    (_, bg), (_, fg) = colors[0], colors[1]
    return _contrast_ratio(fg, bg)


#: Real, small vocabulary of urgency/scarcity phrases this evaluator can genuinely detect via OCR.
#: Deliberately literal substring matches against extracted text -- no fuzzy/semantic matching, so a
#: "hit" always corresponds to text this image actually, verifiably contains.
_URGENCY_PHRASES = (
    "sale ends",
    "hours left",
    "hours!",
    "only 3 left",
    "only a few left",
    "left in stock",
    "limited time",
    "act now",
    "while supplies last",
    "before it's gone",
    "buy before it",
)


def _detect_urgency_claim(extracted_text: str) -> str | None:
    """The first urgency phrase genuinely found (case-insensitively) in OCR-extracted text, or None.
    Returns the matched phrase itself (not just True/False) so `details.matched_phrase` in the caller
    can show what was actually read, not just that something was."""
    lowered = extracted_text.lower()
    for phrase in _URGENCY_PHRASES:
        if phrase in lowered:
            return phrase
    return None


#: Per-process cache of fetched creative-asset bytes, keyed by URL. `None` value = not yet
#: attempted; an `Exception` instance = the fetch failed and should be reported, not retried forever
#: silently; `bytes` = a real successful fetch, safe to evaluate against repeatedly without refetching
#: on every call for the same asset within this container's lifetime.
#:
#: A cache hit makes a repeat call cheaper. It does NOT change the response status: every call fetches
#: if it has to, then answers `completed`, per AdCP's 30-second rule. The earlier design keyed a
#: `working` status off a cold cache and is described in `handle_get_creative_features`.
_creative_fetch_cache: dict[str, bytes | Exception] = {}
_creative_fetch_inflight: dict[str, asyncio.Task[None]] = {}


async def _fetch_and_cache(asset_url: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(asset_url)
            response.raise_for_status()
            _creative_fetch_cache[asset_url] = response.content
    except httpx.HTTPError as exc:
        _creative_fetch_cache[asset_url] = exc
    finally:
        _creative_fetch_inflight.pop(asset_url, None)


if __name__ == "__main__":
    # Fail fast on misconfiguration, before the runtime reports READY.
    #
    # This ordering is deliberate and it is the AgentCore lesson from this repo's steering notes: a
    # container that starts happily and then fails per-request gets reported as a healthy deployment,
    # and the failure surfaces as someone else's timeout. Signing key, issuer and mode are all checked
    # here so a misconfigured agent never reaches READY.
    governance_mode()
    issuer_url()
    # Resolves whichever signing path is configured -- KMS when GOVERNANCE_SIGNING_KMS_KEY_ID is set,
    # otherwise a local PEM -- and validates alg and kid for both. `_load_signing_key()` was checked
    # here before, which would have passed a KMS deployment straight through with no key at all and
    # failed on the first signature instead: a container reporting READY while unable to do its job.
    jws._signer()

    # host/port/stateless_http match AgentCore Runtime's MCP container contract exactly
    # (0.0.0.0:8000/mcp, stateless streamable-http).
    serve(
        ReferenceGovernanceAgent(),
        name=AGENT_NAME,
        host="0.0.0.0",
        port=8000,
        stateless_http=True,
        description=(
            "AdCP reference campaign-governance agent: syncs plans, checks intent and execution "
            "against them, records outcomes, and serves the audit trail. Deterministic checks only."
        ),
        # AgentCore Runtime's edge proxies requests to this container with a Host header that does not
        # match FastMCP's DNS-rebinding allowlist (loopback only: 127.0.0.1:*, localhost:*, [::1]:*), so
        # `TransportSecurityMiddleware` rejects EVERY request with HTTP 421 "Misdirected Request" before
        # it reaches this agent's code. Both sellers already carry this line and the same comment; this
        # agent was deployed without it and every call failed 421 while the container looked perfectly
        # healthy -- it logged `listening on 0.0.0.0:8000/mcp` and `mcp server advertising 11 of 16
        # tools`, and the access log showed `POST /mcp 421`.
        #
        # Safe because the only network path to this container is AgentCore Runtime's own trusted proxy;
        # the platform's Cognito JWT authorizer (agentcore/agentcore.json) is the real inbound boundary,
        # not a host-header check.
        enable_dns_rebinding_protection=False,
        # Puts the authenticated caller on every ToolContext. Without it the committed-spend aggregate
        # has no identity to key on and every budget check degrades -- fail-safe, but an agent that
        # approves nothing. It is also what lets the SDK's idempotency store scope its cache per caller
        # instead of globally.
        context_factory=_build_tool_context,
    )
