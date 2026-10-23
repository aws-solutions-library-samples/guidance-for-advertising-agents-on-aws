"""Handler tests, driven through the SDK's own request models.

Validating against the SDK's types rather than hand-built dicts is the point: it is what proves this
agent's responses satisfy AdCP's schema, not merely this author's reading of it. `serve()` runs the same
`model_validate` on the way out, so a response that constructs here is a response that will serialise
there.
"""

from __future__ import annotations

import boto3
import pytest
from adcp.server.base import NotImplementedResponse
from adcp.signing.crypto import ALG_ED25519
from adcp.signing.keygen import generate_signing_keypair
from adcp.types import (
    CheckGovernanceRequest,
    GetPlanAuditLogsRequest,
    ReportPlanOutcomeRequest,
    SyncPlansRequest,
)
from moto import mock_aws

import aggregation
import idempotency_backend
import jws
import main
import policy
import state


TABLE = "adcp-reference-governance-state-test"
SELLER = "https://triton-seller.example/adcp/mcp"

# The SDK's own plan shape, which is not the one I first guessed. `flight` is `start`/`end` (not
# `*_date`), `channels` is an object with `allowed`, and `budget.reallocation_threshold` is required.
# Discovering this by validating against the SDK rather than by hand-writing fixtures is the reason the
# handler agrees with the protocol instead of with me.
PLAN = {
    "plan_id": "plan-1",
    "brand": {"domain": "nike.com"},
    "objectives": "Reach adults 18-49 with audio during December.",
    "budget": {"total": 100000.0, "currency": "USD", "reallocation_threshold": 0},
    # Timezone-aware datetimes, not dates: the SDK requires tz info on both ends.
    "flight": {"start": "2026-12-05T00:00:00Z", "end": "2026-12-10T23:59:59Z"},
    "countries": ["US"],
    # `allowed` is an ENUM, and `audio` is not in it. AdCP's channel vocabulary says
    # `streaming_audio`. Worth noting for the journey view, whose format groups key on `audio` — the
    # two vocabularies are different and must not be cross-matched.
    "channels": {"allowed": ["streaming_audio", "podcast"]},
}

INTENT_PAYLOAD = {
    "budget": {"total": 71400.0, "currency": "USD"},
    "start_date": "2026-12-05",
    "end_date": "2026-12-10",
    "countries": ["US"],
    "channels": ["streaming_audio"],
}


@pytest.fixture()
def agent(monkeypatch):
    with mock_aws():
        monkeypatch.setenv("STATE_TABLE_NAME", TABLE)
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
        monkeypatch.setenv("GOVERNANCE_AGENT_URL", "https://governance.example/adcp/mcp")
        monkeypatch.setenv("GOVERNANCE_MODE", "enforce")
        private_pem, public_jwk = generate_signing_keypair(alg=ALG_ED25519)
        monkeypatch.setenv("GOVERNANCE_SIGNING_KEY_PEM", private_pem.decode("utf-8"))
        monkeypatch.setenv("GOVERNANCE_SIGNING_ALG", ALG_ED25519)
        monkeypatch.setenv("GOVERNANCE_SIGNING_KID", public_jwk["kid"])

        state.reset_for_tests()
        # `aggregation` and `idempotency_backend` cache their own table resources, so a case that ran
        # before this one would leave a handle pointing at a torn-down mock table. Reset all three or the
        # failure surfaces later, in an unrelated test, as a confusing ResourceNotFound.
        aggregation.reset_for_tests()
        idempotency_backend.reset_for_tests()
        boto3.resource("dynamodb").create_table(
            TableName=TABLE,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield main.ReferenceGovernanceAgent(), public_jwk
        state.reset_for_tests()
        aggregation.reset_for_tests()
        idempotency_backend.reset_for_tests()


#: The authenticated context every request carries in deployment.
#:
#: Supplied here because the committed-spend aggregate is keyed on an identity that lives on the context
#: rather than in the request body -- `caller_identity` is the authenticated principal, `account_id` the
#: resolved account. Without it the identity cannot be formed, and the budget category correctly degrades
#: to `conditions` instead of `approved`: an aggregate that was never consulted must not read as zero.
#:
#: `sync_plans` is the buyer's own call, so this principal is the plan's buyer.
BUYER_PRINCIPAL = "https://reference-buyer.example/adcp"
ACCOUNT_ID = "acct-nike-001"


def buyer_context():
    from adcp.server import AccountAwareToolContext

    return AccountAwareToolContext(
        caller_identity=BUYER_PRINCIPAL, account_id=ACCOUNT_ID, tenant_id=ACCOUNT_ID
    )


async def sync(handler, plan=PLAN):
    return await handler.sync_plans(
        SyncPlansRequest.model_validate(
            {"idempotency_key": "idem-sync-0000000001", "plans": [plan]}
        ),
        buyer_context(),
    )


async def check(handler, payload=None, plan_id="plan-1", tool="create_media_buy"):
    body = payload if payload is not None else INTENT_PAYLOAD
    # `target_agent` names the seller this intent check is about -- the spec's own field for it, and what
    # supplies the seller element of the aggregate key. `caller` is the buyer.
    #
    # This helper used to inject `payload["seller"]` instead. That still resolves (the agent keeps it as a
    # legacy source, covered by TestSellerResolutionIsSingleSourced), but AdCP is explicit that
    # `target_agent` "stays outside the business payload", so the default here is the conformant shape.
    return await handler.check_governance(
        CheckGovernanceRequest.model_validate(
            {
                "plan_id": plan_id,
                "caller": BUYER_PRINCIPAL,
                "target_agent": SELLER,
                "tool": tool,
                "payload": body,
            }
        ),
        buyer_context(),
    )


@pytest.mark.asyncio
async def test_sync_plans_reports_a_version_and_the_categories_it_will_evaluate(agent):
    handler, _ = agent
    response = await sync(handler)
    plan = response.plans[0]
    assert plan.plan_id == "plan-1"
    assert plan.version == 1
    ids = {c.category_id for c in plan.categories}
    # Exactly what it will evaluate. Promising brand safety review it cannot perform would be the worst
    # kind of overstatement from a governance agent.
    assert ids == set(policy.DETERMINISTIC_CATEGORIES)
    assert not set(policy.SEMANTIC_CATEGORIES) & ids


@pytest.mark.asyncio
async def test_re_syncing_increments_the_version(agent):
    handler, _ = agent
    await sync(handler)
    assert (await sync(handler)).plans[0].version == 2


@pytest.mark.asyncio
async def test_a_compliant_intent_check_is_approved_and_signed(agent):
    handler, public_jwk = agent
    await sync(handler)
    response = await check(handler)
    assert response.verdict == "approved"
    assert response.governance_context, "an approval must carry a signed context"
    claims = jws.verify_governance_context(response.governance_context, public_jwk)
    assert claims["sub"] == "plan-1"
    assert claims["aud"] == SELLER
    assert claims["phase"] == "intent"
    assert response.expires_at, "an approval without an expiry is a licence"


@pytest.mark.asyncio
async def test_an_approval_carries_findings_with_confidence(agent):
    # FQ2=A: without informational findings on approved, the journey view's radial has nothing to draw
    # in a healthy session.
    handler, _ = agent
    await sync(handler)
    response = await check(handler)
    assert response.findings
    assert all(f.confidence == 1.0 for f in response.findings)


@pytest.mark.asyncio
async def test_a_denial_grants_no_signed_context(agent):
    # The artefact a denial withholds must not be handed over with it.
    handler, _ = agent
    await sync(handler)
    response = await check(handler, payload={**INTENT_PAYLOAD, "countries": ["CA"]})
    assert response.verdict == "denied"
    assert not response.governance_context
    assert not response.expires_at


@pytest.mark.asyncio
async def test_a_channel_outside_the_plan_is_denied(agent):
    # Feature 8 / FR-4 (channel leg of the deny matrix): the plan allows streaming_audio + podcast;
    # an action requesting `display` is refused, and the channel category is reported as evaluated.
    handler, _ = agent
    await sync(handler)
    response = await check(handler, payload={**INTENT_PAYLOAD, "channels": ["display"]})
    assert response.verdict == "denied"
    assert "channel_compliance" in (response.categories_evaluated or [])
    assert not response.governance_context


@pytest.mark.asyncio
async def test_geo_and_channel_evaluate_when_the_action_carries_them(agent):
    # Feature 8 / FR-3: the compliant payload carries countries=["US"] and channels=["streaming_audio"],
    # both within the plan, so BOTH categories actually evaluate (not reported "unevaluated") and pass.
    # This is the positive side of the diagnostic finding that geo/channel were being skipped when the
    # action named no markets/channels.
    handler, _ = agent
    await sync(handler)
    response = await check(handler)
    evaluated = response.categories_evaluated or []
    assert "geo_compliance" in evaluated
    assert "channel_compliance" in evaluated
    assert response.verdict == "approved"


@pytest.mark.asyncio
async def test_over_authority_returns_conditions_with_an_applicable_value(agent):
    handler, _ = agent
    await sync(handler)
    response = await check(
        handler, payload={**INTENT_PAYLOAD, "budget": {"total": 150000.0, "currency": "USD"}}
    )
    assert response.verdict == "conditions"
    assert response.conditions
    assert response.conditions[0].required_value == 100000.0
    # `conditions` is NOT an approval path, and this asserted `governance_context` on the reasoning that
    # it was. Per AdCP: "Only approved returns a governance_context token. […] A conditions response
    # instead carries consultation_context, a non-authorizing negotiation handle". Handing over the
    # authorising artefact alongside a verdict that withholds authorisation is what that fixed.
    assert response.governance_context is None
    extra = response.model_extra or {}
    assert extra.get("consultation_context"), extra
    # Still time-bounded: the handle is a signed token, so it expires like any other.
    assert response.expires_at


@pytest.mark.asyncio
async def test_an_unsynced_plan_is_a_missing_plan_not_a_breach(agent):
    handler, _ = agent
    response = await check(handler, plan_id="never-synced")
    assert response.verdict == "denied"
    # Distinguishable from a breach: nothing was evaluated, and the text says which fault it is.
    assert response.categories_evaluated == [] or response.categories_evaluated is None
    assert "sync_plans" in response.explanation


@pytest.mark.asyncio
async def test_the_mode_is_recorded_on_every_check(agent):
    handler, _ = agent
    await sync(handler)
    assert (await check(handler)).mode == "enforce"


@pytest.mark.asyncio
async def test_an_unrecognised_mode_refuses_to_run(agent, monkeypatch):
    handler, _ = agent
    await sync(handler)
    monkeypatch.setenv("GOVERNANCE_MODE", "probably_fine")
    with pytest.raises(RuntimeError):
        await check(handler)


@pytest.mark.asyncio
async def test_approved_checks_alone_do_not_consume_authority(agent):
    # Three approvals of 71.4k against a 100k plan. If checks consumed authority the third would fail;
    # they do not, because AdCP commits budget from outcomes.
    handler, _ = agent
    await sync(handler)
    for _ in range(3):
        assert (await check(handler)).verdict == "approved"


@pytest.mark.asyncio
async def test_a_reported_outcome_consumes_authority(agent):
    handler, _ = agent
    await sync(handler)
    approved = await check(handler)
    outcome = await handler.report_plan_outcome(
        ReportPlanOutcomeRequest.model_validate(
            {
                "plan_id": "plan-1",
                "idempotency_key": "idem-outcome-000000001",
                "outcome": "completed",
                "governance_context": approved.governance_context,
                "check_id": approved.check_id,
                "seller_response": {"seller_reference": "mb_1", "committed_budget": 71400.0},
            }
        )
    )
    assert outcome.outcome_state == "accepted"
    assert outcome.committed_budget == 71400.0

    # Now the same request no longer fits the remaining 28.6k.
    again = await check(handler)
    assert again.verdict == "conditions"
    assert again.conditions[0].required_value == pytest.approx(28600.0)


@pytest.mark.asyncio
async def test_the_sellers_confirmed_amount_wins_over_the_requested_one(agent):
    # AdCP's discrepancy case: the seller confirms a different figure and the agent commits the seller's.
    # Using the requested amount would misstate remaining authority in exactly the interesting case.
    handler, _ = agent
    await sync(handler)
    approved = await check(handler)
    outcome = await handler.report_plan_outcome(
        ReportPlanOutcomeRequest.model_validate(
            {
                "plan_id": "plan-1",
                "idempotency_key": "idem-outcome-000000002",
                "outcome": "completed",
                "governance_context": approved.governance_context,
                "check_id": approved.check_id,
                "seller_response": {"seller_reference": "mb_2", "committed_budget": 120000.0},
            }
        )
    )
    assert outcome.committed_budget == 120000.0


@pytest.mark.asyncio
async def test_a_failed_outcome_consumes_nothing(agent):
    handler, _ = agent
    await sync(handler)
    approved = await check(handler)
    await handler.report_plan_outcome(
        ReportPlanOutcomeRequest.model_validate(
            {
                "plan_id": "plan-1",
                "idempotency_key": "idem-outcome-000000003",
                "outcome": "failed",
                "governance_context": approved.governance_context,
                "check_id": approved.check_id,
                "error": {"code": "REJECTED", "message": "seller declined"},
            }
        )
    )
    assert (await check(handler)).verdict == "approved"


@pytest.mark.asyncio
async def test_audit_logs_report_budget_state_and_check_types(agent):
    handler, _ = agent
    await sync(handler)
    await check(handler, tool="get_products")
    response = await handler.get_plan_audit_logs(
        GetPlanAuditLogsRequest.model_validate({"plan_ids": ["plan-1"], "include_entries": True})
    )
    plan = response.plans[0]
    assert plan.plan_id == "plan-1"
    assert plan.budget.remaining == 100000.0
    assert plan.plan_version == 1
    # AdCP's own discriminator, literally 'intent' or 'execution'. It is what tells the journey view's
    # Govern panel from its Score panel.
    assert plan.entries[0].check_type == "intent"


@pytest.mark.asyncio
async def test_audit_logs_omit_an_unsynced_plan_rather_than_filling_it_in(agent):
    handler, _ = agent
    response = await handler.get_plan_audit_logs(
        GetPlanAuditLogsRequest.model_validate({"plan_ids": ["nope"]})
    )
    # A limitation of the response shape, not a preference. AdCP's audit-log `Plan` requires
    # `plan_version`, `budget`, `summary` and `governed_actions`, and none of those exist for a plan
    # that was never synced. Omitting beats supplying a version for it; a caller diffs the ids it
    # asked for against the ids it got.
    assert response.plans == []


@pytest.mark.asyncio
async def test_unsupported_operations_say_so_rather_than_returning_a_pass(agent):
    handler, _ = agent
    for call in (
        handler.get_creative_features({"creative_manifest": {}}),
        handler.list_property_lists({}),
    ):
        response = await call
        assert isinstance(response, NotImplementedResponse)
        assert response.supported is False
        assert response.reason


async def test_an_intent_check_is_stamped_intent_not_purchase(agent):
    """The subtlest correctness point in this agent, and it would fail at the counterparty.

    AdCP documents `phase` as defaulting to `purchase` and being present on EXECUTION checks, so
    `request.phase` reads `purchase` on an intent check that never set it. A token stamped `purchase`
    for a buyer's intent check gives the buyer a token only a seller should have produced, and a
    conforming seller checks for `phase: "intent"` before accepting a spend commit. It would be
    rejected, and nothing on this side would say why.
    """
    handler, public_jwk = agent
    await sync(handler)
    response = await check(handler)  # tool + payload, so an intent check
    claims = jws.verify_governance_context(response.governance_context, public_jwk)
    assert claims["phase"] == "intent"


async def test_the_phase_comes_from_the_check_type_not_the_default(agent):
    handler, _ = agent
    request = CheckGovernanceRequest.model_validate(
        {"plan_id": "plan-1", "caller": SELLER, "tool": "create_media_buy", "payload": INTENT_PAYLOAD}
    )
    # The SDK's own default is `purchase`; the derived value must disagree with it here.
    assert main._phase_for(request) == "intent"
    assert str(getattr(request.phase, "value", request.phase)) == "purchase"


async def test_the_plan_hash_binds_a_token_to_the_plan_it_approved(agent):
    """An amended plan must not be presentable as the plan that was approved."""
    handler, public_jwk = agent
    await sync(handler)
    first = await check(handler)
    before = jws.verify_governance_context(first.governance_context, public_jwk)["plan_hash"]

    raised = {**PLAN, "budget": {"total": 900000.0, "currency": "USD", "reallocation_threshold": 0}}
    await sync(handler, plan=raised)
    second = await check(handler)
    after = jws.verify_governance_context(second.governance_context, public_jwk)["plan_hash"]

    # Same plan_id, different authority, so a different hash. A re-sync that only bumped the version
    # would leave it unchanged, which is the point of the exclusion list.
    assert before != after


# ================================================== M12: credential binding and the tool context
#
# AdCP: "The governance agent MUST verify that the Bearer token matches a registered credential for the
# account associated with the plan_id, and MUST reject requests with unrecognized or mismatched
# credentials."
#
# The credential is a Cognito access token the buyer mints and registers with the seller via
# sync_governance; the seller presents that same token back here, so both sides of a plan's lifecycle
# resolve to one credential. These tests pin the binding, and the absent-data behaviour that keeps it from
# breaking plans synced before the check existed.


class _FakeHeaders(dict):
    """Case-insensitive enough for the two spellings the header arrives under."""


def _bearer(claims: dict) -> object:
    import jwt as _jwt

    # The signature is never checked -- the platform authorizer is the verification boundary and this
    # code only decodes claims. A 32-byte secret keeps PyJWT's key-length warning out of the output.
    token = _jwt.encode(claims, "x" * 32, algorithm="HS256")

    class _Req:
        headers = _FakeHeaders({"authorization": f"Bearer {token}"})

    return _Req()


def test_the_caller_identity_comes_from_sub_when_present():
    identity, credential = main._credential_from_authorization_header(
        _bearer({"sub": "cognito-uuid-1", "client_id": "app-client-a"})
    )
    assert identity == "cognito-uuid-1"
    assert credential == "app-client-a"


def test_the_caller_identity_falls_back_to_client_id():
    """A `client_credentials` token carries no `sub`, which is the machine-to-machine case."""
    identity, credential = main._credential_from_authorization_header(
        _bearer({"client_id": "app-client-m2m"})
    )
    assert identity == "app-client-m2m"
    assert credential == "app-client-m2m"


def test_no_bearer_token_yields_no_identity():
    """Local development, where no platform authorizer sits in front of this code."""

    class _Req:
        headers = _FakeHeaders({})

    assert main._credential_from_authorization_header(_Req()) == (None, None)


def test_a_malformed_authorization_header_yields_no_identity():
    class _Bad:
        headers = _FakeHeaders({"authorization": "Bearer not-a-jwt"})

    assert main._credential_from_authorization_header(_Bad()) == (None, None)

    class _NotBearer:
        headers = _FakeHeaders({"authorization": "Basic abc123"})

    assert main._credential_from_authorization_header(_NotBearer()) == (None, None)


def test_the_context_factory_produces_an_account_aware_context():
    """Without this the aggregate has no identity to key on and every budget check degrades."""
    from adcp.server import AccountAwareToolContext

    class _Meta:
        request_context = _bearer({"sub": "cognito-uuid-2", "client_id": "app-client-b"})
        request_id = "req-1"

    ctx = main._build_tool_context(_Meta())
    assert isinstance(ctx, AccountAwareToolContext)
    assert ctx.caller_identity == "cognito-uuid-2"
    assert ctx.account_id == "app-client-b"
    assert ctx.tenant_id == "app-client-b"


def _other_account_context():
    from adcp.server import AccountAwareToolContext

    return AccountAwareToolContext(
        caller_identity="someone-else", account_id="app-client-intruder", tenant_id="app-client-intruder"
    )


@pytest.mark.asyncio
async def test_a_mismatched_credential_is_refused_on_check_governance(agent):
    """M12. `PERMISSION_DENIED`, not `PLAN_NOT_FOUND`: the plan exists and the refusal is about
    authorisation. Answering "not found" would leak whether a plan id is in use."""
    handler, _ = agent
    await sync(handler)

    response = await handler.check_governance(
        CheckGovernanceRequest.model_validate(
            {
                "plan_id": "plan-1",
                "caller": BUYER_PRINCIPAL,
                "tool": "create_media_buy",
                "payload": {**INTENT_PAYLOAD, "seller": SELLER},
            }
        ),
        _other_account_context(),
    )

    body = response.model_dump(mode="json", exclude_none=True)
    assert body["verdict"] == "denied"
    # Both layers of AdCP's error model, in their own places: the canonical payload array and the
    # envelope's extractable single error. This previously asserted `adcp_error.errors[0].code`, which
    # was the payload array nested inside the envelope field -- neither layer, and a shape no client
    # reading either one would find.
    assert body["errors"][0]["code"] == "PERMISSION_DENIED"
    assert body["adcp_error"]["code"] == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_a_mismatched_credential_is_refused_on_report_plan_outcome(agent):
    """The write path matters more: a wrong credential must not corrupt another account's aggregate."""
    handler, _ = agent
    await sync(handler)
    # A real approval first, so the outcome carries the `check_id` and `governance_context` that a
    # `completed` outcome requires. The refusal must come from the credential, not from a malformed
    # request -- otherwise the test would pass for the wrong reason.
    approved = await check(handler)

    response = await handler.report_plan_outcome(
        ReportPlanOutcomeRequest.model_validate(
            {
                "idempotency_key": "idem-outcome-mismatch1",
                "plan_id": "plan-1",
                "outcome": "completed",
                "governance_context": approved.governance_context,
                "check_id": approved.check_id,
                "seller_response": {"committed_budget": 1000.0},
            }
        ),
        _other_account_context(),
    )

    body = response.model_dump(mode="json", exclude_none=True)
    assert body["outcome_state"] == "findings"
    assert body["errors"][0]["code"] == "PERMISSION_DENIED"
    assert body["adcp_error"]["code"] == "PERMISSION_DENIED"
    # Nothing was recorded.
    assert state.committed_total("plan-1") == 0.0


@pytest.mark.asyncio
async def test_the_registered_credential_is_accepted(agent):
    handler, _ = agent
    await sync(handler)
    response = await check(handler)
    assert response.verdict == "approved"


@pytest.mark.asyncio
async def test_a_plan_with_no_recorded_credential_is_not_treated_as_a_mismatch(agent):
    """Absent is not a mismatch.

    A plan synced before this check existed carries no recorded account, and refusing every call on it
    would break plans that are otherwise fine. The binding tightens as plans are re-synced.
    """
    handler, _ = agent
    await handler.sync_plans(
        SyncPlansRequest.model_validate(
            {"idempotency_key": "idem-sync-nocontext01", "plans": [PLAN]}
        ),
        None,  # no context, so no account recorded
    )
    assert state.get_plan("plan-1").get("account_id") is None

    response = await handler.check_governance(
        CheckGovernanceRequest.model_validate(
            {
                "plan_id": "plan-1",
                "caller": BUYER_PRINCIPAL,
                "tool": "create_media_buy",
                "payload": {**INTENT_PAYLOAD, "seller": SELLER},
            }
        ),
        _other_account_context(),
    )
    body = response.model_dump(mode="json", exclude_none=True)
    assert "adcp_error" not in body


@pytest.mark.asyncio
async def test_the_buyer_principal_is_recorded_at_sync_time(agent):
    """The only point at which the buyer's identity is available: neither the check nor the outcome
    request carries a caller, so the aggregate key would otherwise be unformable on the write path."""
    handler, _ = agent
    await sync(handler)
    stored = state.get_plan("plan-1")
    assert stored["buyer_principal"] == BUYER_PRINCIPAL
    assert stored["account_id"] == ACCOUNT_ID


@pytest.mark.asyncio
async def test_a_resync_without_context_keeps_the_recorded_buyer(agent):
    """Otherwise a plan silently drops out of the aggregate on any context-less re-sync."""
    handler, _ = agent
    await sync(handler)
    await handler.sync_plans(
        SyncPlansRequest.model_validate(
            {"idempotency_key": "idem-sync-0000000002", "plans": [PLAN]}
        ),
        None,
    )
    stored = state.get_plan("plan-1")
    assert stored["buyer_principal"] == BUYER_PRINCIPAL
    assert stored["account_id"] == ACCOUNT_ID


# ============================================================================ M8: idempotency
#
# `report_plan_outcome` carries a required `idempotency_key`. Until now the agent accepted it and ignored
# it, which was nearly harmless -- a retry produced a duplicate outcome row and inflated
# `outcomes_reported`, but no budget figure was wrong.
#
# The cross-plan aggregate changes that. It increments with an atomic ADD and has no decrement path by
# design, so a retried outcome permanently over-counts committed spend and the fragmentation defense
# starts denying legitimate buys using a number nobody can correct.


async def _report(handler, approved, *, key: str, amount: float = 10_000.0):
    return await handler.report_plan_outcome(
        ReportPlanOutcomeRequest.model_validate(
            {
                "idempotency_key": key,
                "plan_id": "plan-1",
                "outcome": "completed",
                "governance_context": approved.governance_context,
                "check_id": approved.check_id,
                "seller_response": {"seller_reference": "mb_1", "committed_budget": amount},
            }
        ),
        buyer_context(),
    )


@pytest.mark.asyncio
async def test_a_retried_outcome_is_recorded_once(agent):
    """The same `idempotency_key` twice must not double-count.

    Committed spend is what the whole budget check reads, so a duplicated commit is not a cosmetic
    duplicate -- it strands authority the buyer never spent, with no way to give it back.
    """
    handler, _ = agent
    await sync(handler)
    approved = await check(handler)

    first = await _report(handler, approved, key="idem-outcome-retry0001")
    second = await _report(handler, approved, key="idem-outcome-retry0001")

    assert first.outcome_state == "accepted"
    # The replay returns the ORIGINAL cached response, as a dict rather than a re-validated model --
    # that difference is itself the evidence the second call never reached the handler.
    replayed = second if isinstance(second, dict) else second.model_dump(mode="json")
    assert replayed["outcome_id"] == first.outcome_id
    assert state.committed_total("plan-1") == 10_000.0


@pytest.mark.asyncio
async def test_a_distinct_key_is_a_distinct_report(agent):
    """Dedup must not swallow two genuinely different reports.

    An agent deciding to report twice with two fresh keys is reporting two things; only a transport-level
    retry of one logical request carries the same key.
    """
    handler, _ = agent
    await sync(handler)
    approved = await check(handler)

    await _report(handler, approved, key="idem-outcome-distinct01", amount=10_000.0)
    await _report(handler, approved, key="idem-outcome-distinct02", amount=5_000.0)

    assert state.committed_total("plan-1") == 15_000.0


@pytest.mark.asyncio
async def test_check_governance_is_not_wrapped(agent):
    """It carries no `idempotency_key`, being an evaluation rather than a mutation.

    Wrapping it would invent a contract, and would also cache a verdict across a plan amendment.
    """
    handler, _ = agent
    await sync(handler)
    first = await check(handler)
    second = await check(handler)
    assert first.check_id != second.check_id, "each evaluation is its own check"


@pytest.mark.asyncio
async def test_a_reported_outcome_reaches_the_cross_plan_aggregate(agent):
    """M3's write path, end to end.

    This was inert until the identity could be formed: `report_plan_outcome` carries no `caller` and no
    `payload`, so the seller element comes from the `governance_context` token's `aud` -- bound
    byte-for-byte to the seller at issuance -- and the buyer element from what `sync_plans` recorded.
    Without both, the aggregate read zero forever and the fragmentation defense was decorative.
    """
    import aggregation

    handler, _ = agent
    await sync(handler)
    approved = await check(handler)
    await _report(handler, approved, key="idem-outcome-aggregate1", amount=10_000.0)

    result = await aggregation.read(
        buyer_agent=BUYER_PRINCIPAL, seller_agent=SELLER, account_id=ACCOUNT_ID
    )
    assert result.known is True
    assert result.committed == 10_000.0


@pytest.mark.asyncio
async def test_a_retried_outcome_does_not_double_count_the_aggregate(agent):
    """M8 protecting M3, which is the reason M8 ships in this feature rather than after it.

    The aggregate has no decrement path by design, so a double-count is permanent: the fragmentation
    defense would start denying legitimate buys using a number nobody can correct.
    """
    import aggregation

    handler, _ = agent
    await sync(handler)
    approved = await check(handler)

    await _report(handler, approved, key="idem-outcome-nodouble1", amount=10_000.0)
    await _report(handler, approved, key="idem-outcome-nodouble1", amount=10_000.0)

    result = await aggregation.read(
        buyer_agent=BUYER_PRINCIPAL, seller_agent=SELLER, account_id=ACCOUNT_ID
    )
    assert result.committed == 10_000.0, "the retry must not increment the aggregate again"


# ================================================================== Step 16: gates, audit, readers


@pytest.mark.asyncio
async def test_a_malformed_request_is_an_invalid_request_not_a_missing_plan(agent):
    """Gate ORDER, which is the whole point of this test.

    A misplaced `account` is a caller mistake the caller can fix. If the plan lookup ran first, the same
    request would come back `PLAN_NOT_FOUND` -- sending the caller to re-sync a plan that is fine, and
    hiding the field they actually got wrong. Two correctable errors, and only one of them is true.
    """
    handler, _ = agent
    await sync(handler)
    response = await handler.check_governance(
        CheckGovernanceRequest.model_validate(
            {
                "plan_id": "plan-1",
                "caller": BUYER_PRINCIPAL,
                "tool": "create_media_buy",
                "payload": {**INTENT_PAYLOAD, "seller": SELLER},
                # Sibling of `payload`, where 3.1 accepts and IGNORES it -- so silence here would drop
                # the account and evaluate the check against the wrong scope.
                "account": {"account_id": ACCOUNT_ID},
            }
        ),
        buyer_context(),
    )
    assert response.adcp_error is not None
    assert response.adcp_error.code == "INVALID_REQUEST"
    assert response.adcp_error.field == "account"
    assert response.verdict == "denied", "a refusal still denies; it does not pass by omission"


@pytest.mark.asyncio
async def test_a_sibling_account_is_refused_before_the_plan_is_read(agent):
    """M6 on a plan that was never synced: the field error still wins.

    If the order were reversed this would answer `PLAN_NOT_FOUND`, which is true but useless -- the
    caller would sync the plan and hit the same wall.
    """
    handler, _ = agent
    response = await handler.check_governance(
        CheckGovernanceRequest.model_validate(
            {
                "plan_id": "plan-never-synced",
                "caller": BUYER_PRINCIPAL,
                "tool": "create_media_buy",
                "payload": {**INTENT_PAYLOAD, "seller": SELLER},
                "account": {"account_id": ACCOUNT_ID},
            }
        ),
        buyer_context(),
    )
    assert response.adcp_error.code == "INVALID_REQUEST"


# ------------------------------------------------------------------- suspension, in all three modes


SUSPENDING_PLAN = {**PLAN, "plan_id": "plan-suspended", "human_review_required": True}


async def _sync_suspended(handler):
    """A plan that suspends at sync time, via `human_review_required`.

    Suspending through the real code path rather than by writing `suspended` into the table: the
    registration check and escalation this creates are what the audit assertions below read.
    """
    return await handler.sync_plans(
        SyncPlansRequest.model_validate(
            {"idempotency_key": "idem-sync-suspend01", "plans": [SUSPENDING_PLAN]}
        ),
        buyer_context(),
    )


@pytest.mark.parametrize("mode", ["enforce", "advisory", "audit"])
@pytest.mark.asyncio
async def test_a_suspended_plan_is_refused_in_every_mode(agent, monkeypatch, mode):
    """K1-REVISED. Suspension is a GATE, not a verdict, so no mode relaxes it.

    The tempting reading is that `audit` -- which "always returns approved" -- should approve here too.
    It must not: that rule governs the verdict of an EVALUATION, and a suspended plan is never
    evaluated. There is no verdict for `audit` to force, and manufacturing one would have `audit` be the
    single mode in which a plan awaiting human review transacts anyway.

    `advisory` is the same argument. Advisory means the agent does not block the buy on its own
    judgment; it does not mean it hands out a signed authorisation for a plan a human has frozen.
    """
    handler, _ = agent
    monkeypatch.setenv("GOVERNANCE_MODE", mode)
    await _sync_suspended(handler)

    response = await handler.check_governance(
        CheckGovernanceRequest.model_validate(
            {
                "plan_id": "plan-suspended",
                "caller": BUYER_PRINCIPAL,
                "tool": "create_media_buy",
                "payload": {**INTENT_PAYLOAD, "seller": SELLER},
            }
        ),
        buyer_context(),
    )
    assert response.verdict == "denied"
    assert response.adcp_error is not None
    assert response.adcp_error.code == "CAMPAIGN_SUSPENDED"
    # Transient, because a human resolving the escalation clears it. Classifying it as permanent would
    # tell the buyer to give up on a plan that is one decision away from working.
    assert response.adcp_error.recovery == "transient"
    assert (
        response.governance_context is None
    ), "a refusal must not hand over the authorisation it is withholding"


@pytest.mark.asyncio
async def test_a_suspended_plan_refuses_report_plan_outcome_too(agent):
    """Both governed tasks, not just the check.

    Refusing the check while accepting outcomes would let an action authorised before the suspension
    keep consuming budget afterwards -- the suspension would stop new intent and none of the spend.
    """
    handler, _ = agent
    # Approved FIRST, then suspended. That is the real sequence this rule exists for: the token was
    # legitimately issued, the plan suspended afterwards, and the seller now reports the outcome. It is
    # also the only way to get a token for a suspended plan, since a suspended plan is never evaluated.
    await sync(handler)
    approved = await check(handler)
    stored = state.get_plan("plan-1")
    state.put_plan(stored["plan"], requires_human_review=True)

    response = await handler.report_plan_outcome(
        ReportPlanOutcomeRequest.model_validate(
            {
                "idempotency_key": "idem-outcome-susp0001",
                "plan_id": "plan-1",
                "outcome": "completed",
                "governance_context": approved.governance_context,
                "check_id": approved.check_id,
                "seller_response": {"seller_reference": "mb_1", "committed_budget": 10_000.0},
            }
        ),
        buyer_context(),
    )
    body = response.model_dump(mode="json", exclude_none=True)
    assert body["errors"][0]["code"] == "CAMPAIGN_SUSPENDED"
    assert body["errors"][0]["recovery"] == "transient"
    assert (
        state.committed_total("plan-1") == 0.0
    ), "a refused outcome consumes nothing; otherwise suspension would stop intent and no spend"


@pytest.mark.asyncio
async def test_suspension_records_an_escalation_that_names_a_real_check(agent):
    """The escalation's `check_id` has to resolve, because `human_override_rate` compares the human's
    decision against the agent's recommendation -- and there is no recommendation to compare against if
    the id points at nothing."""
    handler, _ = agent
    await _sync_suspended(handler)

    escalations = state.list_escalations("plan-suspended")
    assert len(escalations) == 1
    check_ids = {c["check_id"] for c in state.list_checks("plan-suspended")}
    assert escalations[0]["check_id"] in check_ids
    assert escalations[0].get("resolved_at") is None, "a fresh escalation is unresolved"


@pytest.mark.parametrize("mode", ["advisory", "audit"])
@pytest.mark.asyncio
async def test_the_aggregate_is_committed_in_every_mode(agent, monkeypatch, mode):
    """M7.5. The mode governs what the agent DOES about spend, never whether it counts it.

    An agent that stops aggregating in `audit` would come back from an audit period with a trailing
    window missing exactly the spend that happened during it, and would then under-count every
    fragmentation check for the next 30 days. Counting is bookkeeping; blocking is policy.
    """
    import aggregation

    handler, _ = agent
    monkeypatch.setenv("GOVERNANCE_MODE", mode)
    await sync(handler)
    approved = await check(handler)
    await _report(handler, approved, key=f"idem-outcome-{mode}01", amount=25_000.0)

    result = await aggregation.read(
        buyer_agent=BUYER_PRINCIPAL, seller_agent=SELLER, account_id=ACCOUNT_ID
    )
    assert result.committed == 25_000.0


# ------------------------------------------------------------------------------- the audit response


@pytest.mark.asyncio
async def test_the_audit_summary_is_derived_from_the_records_it_summarises(agent):
    """Every `Summary` figure recomputed from the stored checks/outcomes/escalations.

    Asserted together rather than one field per test because the failure that matters is DISAGREEMENT:
    a `findings_count` that does not match the findings in `entries`, or verdict counts that do not sum
    to `checks_performed`, is the bug this handler can actually have.
    """
    handler, _ = agent
    await sync(handler)
    approved = await check(handler)
    await _report(handler, approved, key="idem-outcome-summary01", amount=10_000.0)

    response = await handler.get_plan_audit_logs(
        GetPlanAuditLogsRequest.model_validate({"plan_ids": ["plan-1"], "include_entries": True}),
        buyer_context(),
    )
    plan = response.plans[0]
    summary = plan.summary

    assert plan.status == "active"
    assert summary.checks_performed == 1
    assert summary.outcomes_reported == 1
    statuses = summary.statuses
    assert (
        statuses.approved + statuses.denied + statuses.conditions == summary.checks_performed
    ), "the three verdict counts partition the checks; human_reviewed is supplementary to them"
    assert statuses.human_reviewed == 0

    entry_findings = sum(len(e.findings or []) for e in plan.entries if e.type == "check")
    assert summary.findings_count == entry_findings, "the count and the entries must agree"
    assert summary.escalations == []

    drift = summary.drift_metrics
    assert drift.escalation_rate == 0.0
    assert drift.auto_approval_rate == 1.0
    assert drift.mean_confidence is not None, "this agent's findings carry confidence"
    # Absent by design, both of them: a `stable` band and org thresholds are configuration this agent
    # does not have, and inventing either would report an opinion as a measurement.
    assert drift.escalation_rate_trend is None
    assert drift.thresholds is None
    assert drift.human_override_rate is None, "no resolved escalation means nothing to divide by"


@pytest.mark.asyncio
async def test_a_suspended_plan_reports_suspended_with_its_escalation(agent):
    handler, _ = agent
    await _sync_suspended(handler)

    response = await handler.get_plan_audit_logs(
        GetPlanAuditLogsRequest.model_validate({"plan_ids": ["plan-suspended"]}),
        buyer_context(),
    )
    plan = response.plans[0]
    assert plan.status == "suspended"
    assert len(plan.summary.escalations) == 1
    escalation = plan.summary.escalations[0]
    assert escalation.reason
    assert escalation.resolution is None
    assert escalation.resolved_at is None
    assert plan.summary.drift_metrics.escalation_rate == 1.0
    assert plan.summary.drift_metrics.auto_approval_rate == 0.0


@pytest.mark.asyncio
async def test_a_resolved_escalation_becomes_a_human_review_and_an_override(agent):
    """The two metrics that only exist once a human has answered.

    `human_reviewed` counts the check, and `human_override_rate` compares the human's decision against
    the agent's recorded recommendation. The registration check recommends `denied`; an
    `approved_by_human` resolution therefore IS an override, and a rate of 0.0 here would be the more
    dangerous reading -- it would say the agent's calibration is being confirmed when it is being
    reversed.
    """
    handler, _ = agent
    await _sync_suspended(handler)
    escalation = state.list_escalations("plan-suspended")[0]
    assert state.resolve_escalation(
        plan_id="plan-suspended", check_id=escalation["check_id"], resolution="approved_by_human"
    )

    response = await handler.get_plan_audit_logs(
        GetPlanAuditLogsRequest.model_validate({"plan_ids": ["plan-suspended"]}),
        buyer_context(),
    )
    summary = response.plans[0].summary
    assert summary.statuses.human_reviewed == 1
    # The check now counts as `approved`, because that is what the human decided and the spec says a
    # reviewed check resolves to approved or denied. `human_reviewed` is supplementary to that count, not
    # a fourth bucket.
    assert summary.statuses.approved == 1
    assert summary.statuses.denied == 0
    assert summary.escalations[0].resolution == "approved_by_human"
    assert summary.escalations[0].resolved_at is not None
    # 1.0, not 0.0: the agent recommended `denied` and the human approved. This is the assertion that
    # catches the tempting in-place overwrite -- if resolving replaced the recommendation instead of
    # preserving it, the two could never disagree and this would read 0.0 for every plan ever reviewed.
    assert summary.drift_metrics.human_override_rate == 1.0
    # Resolving the last escalation clears the suspension, which is what lets the plan transact again.
    assert response.plans[0].status == "active"


@pytest.mark.asyncio
async def test_an_unrecognised_resolution_is_not_counted_as_an_override(agent):
    """A resolution outside AdCP's own example vocabulary is unclassifiable, and unclassifiable is not
    zero. Returning 0.0 would report "the human agreed with us" about a decision nobody can read."""
    handler, _ = agent
    await _sync_suspended(handler)
    escalation = state.list_escalations("plan-suspended")[0]
    state.resolve_escalation(
        plan_id="plan-suspended", check_id=escalation["check_id"], resolution="handled offline"
    )

    response = await handler.get_plan_audit_logs(
        GetPlanAuditLogsRequest.model_validate({"plan_ids": ["plan-suspended"]}),
        buyer_context(),
    )
    summary = response.plans[0].summary
    assert summary.statuses.human_reviewed == 1, "it WAS reviewed; that much is recorded"
    assert summary.drift_metrics.human_override_rate is None


@pytest.mark.asyncio
async def test_a_plan_whose_flight_has_ended_reports_completed(agent):
    """The only completion signal AdCP gives a governance agent.

    Past `flight.end` the plan can never authorise another action, so `active` would claim authority
    that no longer exists. Note what is NOT used: budget exhaustion. A fully-committed plan may still be
    modified, and an unspent plan the buyer abandoned is not complete.
    """
    handler, _ = agent
    await handler.sync_plans(
        SyncPlansRequest.model_validate(
            {
                "idempotency_key": "idem-sync-expired01",
                "plans": [
                    {
                        **PLAN,
                        "plan_id": "plan-expired",
                        "flight": {"start": "2020-01-01T00:00:00Z", "end": "2020-02-01T00:00:00Z"},
                    }
                ],
            }
        ),
        buyer_context(),
    )
    response = await handler.get_plan_audit_logs(
        GetPlanAuditLogsRequest.model_validate({"plan_ids": ["plan-expired"]}),
        buyer_context(),
    )
    assert response.plans[0].status == "completed"


@pytest.mark.asyncio
async def test_the_entry_list_is_capped_and_says_so(agent):
    """NFR-11. 201 entries in, 200 out, and the truncation STATED.

    The cap alone is the dangerous half: 200 entries look exactly like a complete trail of 200 entries.
    A caller reconciling spend against the audit log has no way to detect the difference, which is why
    the assertion on `message` matters as much as the one on the length.
    """
    handler, _ = agent
    await sync(handler)
    # Written directly: driving 201 checks through the handler would take a minute of test time to
    # prove something about a list length.
    for index in range(201):
        state.put_check(
            plan_id="plan-1",
            verdict="approved",
            explanation=f"synthetic check {index}",
            payload={"caller": BUYER_PRINCIPAL, "tool": "create_media_buy"},
            findings=[],
            categories_evaluated=[],
        )

    response = await handler.get_plan_audit_logs(
        GetPlanAuditLogsRequest.model_validate({"plan_ids": ["plan-1"], "include_entries": True}),
        buyer_context(),
    )
    plan = response.plans[0]
    assert len(plan.entries) == main.MAX_AUDIT_ENTRIES
    assert plan.summary.checks_performed == 201, "the summary counts all of them; only entries are cut"
    assert response.message is not None
    assert "201" in response.message and str(main.MAX_AUDIT_ENTRIES) in response.message


@pytest.mark.asyncio
async def test_an_untruncated_response_makes_no_truncation_claim(agent):
    """The negative half of the cap. A `message` that mentions truncation on a complete trail would be
    the same defect in the other direction."""
    handler, _ = agent
    await sync(handler)
    await check(handler)
    response = await handler.get_plan_audit_logs(
        GetPlanAuditLogsRequest.model_validate({"plan_ids": ["plan-1"], "include_entries": True}),
        buyer_context(),
    )
    assert response.message is None


@pytest.mark.asyncio
async def test_check_entries_carry_the_findings_that_were_recorded_not_recomputed(agent):
    """M4.2, and the reason findings are persisted at check time.

    Re-running today's policy over yesterday's decision would report what the agent WOULD decide now,
    labelled as what it DID decide then. The audit log's only job is the second.
    """
    handler, _ = agent
    await sync(handler)
    approved = await check(handler)

    response = await handler.get_plan_audit_logs(
        GetPlanAuditLogsRequest.model_validate({"plan_ids": ["plan-1"], "include_entries": True}),
        buyer_context(),
    )
    entry = next(e for e in response.plans[0].entries if e.id == approved.check_id)
    assert entry.findings, "the check recorded findings, so the entry carries them"
    assert {f.category_id for f in entry.findings} == {
        f.category_id for f in approved.findings
    }, "the audit finding names the same categories the live response did"
    assert entry.categories_evaluated == list(approved.categories_evaluated)


def test_the_audit_finding_rejects_the_response_findings_extra_fields():
    """SDK-GAP-4, asserted rather than described.

    The schema says audit findings are the "same structure as check_governance response findings". They
    are not: the audit variant has five fields and `extra='forbid'`. This test is what stops a future
    edit from passing a response finding straight through -- it would fail at the boundary with a
    nested path, long after the line that caused it.
    """
    import pydantic

    from adcp.types.generated_poc.governance.get_plan_audit_logs_response import (
        Finding as AuditFinding,
    )

    with pytest.raises(pydantic.ValidationError) as excinfo:
        AuditFinding(
            category_id="budget_authority",
            severity="info",
            explanation="ok",
            confidence=1.0,
            details={"requested": 71400},
        )
    assert "extra_forbidden" in str(excinfo.value)


# ------------------------------------------------------- capabilities, reachability, the four readers


@pytest.mark.asyncio
async def test_the_declared_aggregation_window_is_the_one_the_aggregate_uses(agent):
    """M9. The declaration and the behaviour read the same constant, so they cannot drift.

    Omission is not neutral here: the field's own description says a buyer seeing no window MUST assume
    per-commit evaluation only, and that 30 "is not implied by omission". An undeclared window makes a
    working fragmentation defense indistinguishable from an absent one.
    """
    import aggregation

    handler, _ = agent
    response = await handler.get_adcp_capabilities({}, buyer_context())
    assert response.governance.aggregation_window_days == aggregation.AGGREGATION_WINDOW_DAYS
    assert 1 <= response.governance.aggregation_window_days <= 365, "the schema's own bounds"
    assert response.supported_protocols == ["governance"]
    # Deprecated but mandatory through 3.x, so its absence would be a conformance failure rather than
    # tidiness.
    assert [v.root for v in response.adcp.major_versions] == [3]
    assert [v.root for v in response.adcp.supported_versions] == ["3.1"]
    # Declared `supported=True` because `report_plan_outcome` really is deduplicated (M8), with the
    # window read from the same constant the store is constructed with.
    assert response.adcp.idempotency.supported is True
    assert (
        response.adcp.idempotency.replay_ttl_seconds
        == idempotency_backend.REPLAY_TTL_SECONDS
    )


def test_every_dispatched_tool_is_answerable():
    """Reachability, against the SDK's own dispatcher rather than a hand-written list.

    This agent does not narrow `advertised_tools`. It inherits `GovernanceHandler`'s surface -- 11 tools,
    not the base handler's ~60 -- and answers the five property-list operations with
    `NotImplementedResponse`, which is what that class's own docstring prescribes and is a true statement
    a caller can act on.

    So the invariant here is not set equality against a declaration; it is that **every name the
    dispatcher advertises resolves to something callable**. A dispatched tool with no method behind it is
    the `advertised_tools` failure in its other direction: discovery promises it and the call raises.
    """
    from adcp.server.mcp_tools import get_tools_for_handler

    agent_instance = main.ReferenceGovernanceAgent()
    dispatched = {tool["name"] for tool in get_tools_for_handler(agent_instance)}

    # The six this agent really implements. Named explicitly because a regression that silently stopped
    # dispatching one of them would otherwise pass the callable check below.
    assert {
        "sync_plans",
        "check_governance",
        "report_plan_outcome",
        "get_plan_audit_logs",
        "get_creative_features",
        "get_adcp_capabilities",
    } <= dispatched

    for name in dispatched:
        assert callable(getattr(agent_instance, name, None)), (
            f"{name} is advertised by the dispatcher but has no callable behind it"
        )


@pytest.mark.asyncio
async def test_the_four_working_readers_still_see_what_they_read(agent):
    """R9, the golden-diff. An unflagged plan in `enforce` must be unchanged for existing readers.

    The four readers are the buyer agent, the journey UI, and the seller-side governance verification in
    the reference/gotham sellers. None of them was updated for this feature, so any field they key on
    that moved, changed type, or disappeared is a break they would discover in production rather than
    here. Asserting the FIELDS THEY READ rather than a whole serialised blob keeps the test about
    compatibility instead of about formatting.
    """
    handler, _ = agent

    synced = await sync(handler)
    assert synced.plans[0].plan_id == "plan-1"
    assert synced.plans[0].status == "active"
    assert synced.plans[0].version == 1
    assert [c.category_id for c in synced.plans[0].categories] == list(
        policy.DETERMINISTIC_CATEGORIES
    )

    approved = await check(handler)
    # The seller's `governance_verification.py` reads exactly these three.
    assert approved.verdict == "approved"
    assert approved.governance_context, "the sellers verify this token; its absence breaks the gate"
    assert approved.expires_at is not None
    # The journey UI's Govern panel keys on these.
    assert approved.check_id.startswith("chk_")
    assert approved.plan_id == "plan-1"
    assert approved.explanation
    assert approved.mode == "enforce"
    assert isinstance(approved.findings, list)
    assert isinstance(approved.categories_evaluated, list)
    # Absent, not present-and-null: the buyer's `app.py` classifies a step as failed by testing whether
    # the key EXISTS, so an approval that carried an empty `adcp_error` would be recorded as an error.
    assert "adcp_error" not in (approved.model_extra or {})
    assert "errors" not in (approved.model_extra or {})

    outcome = await _report(handler, approved, key="idem-outcome-golden01", amount=10_000.0)
    assert outcome.outcome_id.startswith("out_")
    assert outcome.outcome_state == "accepted"

    audit = await handler.get_plan_audit_logs(
        GetPlanAuditLogsRequest.model_validate({"plan_ids": ["plan-1"], "include_entries": True}),
        buyer_context(),
    )
    plan = audit.plans[0]
    # The UI's three `awaitingHumanReview` signals and its budget readout.
    assert plan.plan_id == "plan-1"
    assert plan.plan_version == 1
    assert plan.status == "active"
    assert plan.budget.authorized == 100_000.0
    assert plan.budget.committed == 10_000.0
    assert plan.budget.remaining == 90_000.0
    assert plan.budget.utilization_pct == 10.0
    assert plan.governed_actions == []
    check_entry = next(e for e in plan.entries if e.type == "check")
    assert check_entry.check_type == "intent"
    assert check_entry.verdict == "approved"
    assert check_entry.tool == "create_media_buy"
    assert str(check_entry.caller).rstrip("/") == BUYER_PRINCIPAL
    outcome_entry = next(e for e in plan.entries if e.type == "outcome")
    assert outcome_entry.outcome == "completed"
    assert outcome_entry.committed_budget == 10_000.0
    assert outcome_entry.outcome_status == "accepted"


class TestSellerResolutionIsSingleSourced:
    """`_token_audience` and `_seller_for` must answer "which seller is this check about?" identically.

    They did not. `_token_audience` read `payload.seller` then fell back to `caller`; `_seller_for` read
    `payload.seller`, then the prior token's `aud`, then a non-buyer principal, then a sole
    `approved_sellers` entry -- and never `caller`. So on a buyer's FIRST intent check, where there is no
    prior token and the authenticated principal IS the buyer, `_token_audience` resolved a seller and
    `_seller_for` resolved nothing, one function apart.

    The consequence was not a crash. The aggregate key could not be formed, the budget category degraded
    to an explicit `critical` unknown at confidence 0.0, and the verdict became `conditions` -- so every
    first check on a fresh plan refused to authorise, the buyer's own gate correctly declined to book,
    and the journey's Score panel was empty because no seller execution check ever ran. Found by a live
    end-to-end run; all 255 unit tests were green.

    Both now delegate to `_seller_subject`. These tests pin that, and pin the spec's field precedence.
    """

    SELLER = "https://seller.example/adcp/mcp"
    BUYER = "https://buyer.example/a2a"

    def _request(self, **extra):
        """A buyer-side intent check, per AdCP 3.1's Buyer-side intent check table:
        `caller` is the buyer-side orchestrator, `target_agent` is the downstream seller.
        """
        body = {
            "plan_id": "plan-1",
            "caller": self.BUYER,
            "tool": "create_media_buy",
            "payload": {"packages": [{"product_id": "p1", "budget": 1000.0}]},
            **extra,
        }
        return CheckGovernanceRequest.model_validate(body)

    def test_target_agent_is_the_seller_on_a_first_intent_check(self):
        request = self._request(target_agent=self.SELLER)
        assert main._seller_subject(request) == self.SELLER

    def test_the_token_audience_and_the_aggregate_key_agree(self):
        request = self._request(target_agent=self.SELLER)
        audience = main._token_audience(request)
        aggregate_seller = main._seller_for(request, {}, None, self.BUYER)
        assert audience == aggregate_seller == self.SELLER

    def test_caller_is_not_read_as_the_seller_on_an_intent_check(self):
        """`caller` is the buyer here. A token minted with the buyer as `aud` is one every conformant
        seller rejects, so resolving the seller from `caller` must not happen on this path."""
        request = self._request()  # no target_agent, no payload.seller
        assert main._seller_subject(request) is None

    def test_target_agent_wins_over_a_legacy_payload_seller(self):
        """`target_agent` is the spec's field and "stays outside the business payload", so it takes
        precedence over the `payload.seller` this project used before."""
        request = self._request(
            target_agent=self.SELLER,
            payload={"seller": "https://stale.example/adcp/mcp"},
        )
        assert main._seller_subject(request) == self.SELLER

    def test_payload_seller_still_resolves_for_requests_from_the_previous_build(self):
        request = self._request(payload={"seller": self.SELLER})
        assert main._seller_subject(request) == self.SELLER

    def test_a_seller_execution_check_resolves_from_its_authenticated_principal(self):
        """On the seller-side execution check the caller IS the seller, per the spec's own table. That
        path is reached via the authenticated principal, guarded on it differing from the buyer.
        """
        request = CheckGovernanceRequest.model_validate(
            {"plan_id": "plan-1", "caller": self.SELLER, "phase": "purchase"}
        )
        context = type("Ctx", (), {"caller_identity": self.SELLER})()
        assert (
            main._seller_subject(request, context=context, buyer_agent=self.BUYER) == self.SELLER
        )

    def test_a_sole_approved_seller_is_the_last_resort(self):
        request = self._request()
        plan = {"approved_sellers": [self.SELLER]}
        assert main._seller_subject(request, plan=plan, buyer_agent=self.BUYER) == self.SELLER

    def test_two_approved_sellers_are_ambiguous_and_resolve_to_nothing(self):
        """Rather than picking one: aggregating under a wrong key would deny buys that have nothing to
        do with each other."""
        request = self._request()
        plan = {"approved_sellers": [self.SELLER, "https://other.example/adcp/mcp"]}
        assert main._seller_subject(request, plan=plan, buyer_agent=self.BUYER) is None
