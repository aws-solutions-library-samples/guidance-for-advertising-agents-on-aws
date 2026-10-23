"""U4/Part 1: the real `get_creative_features` evaluator, and the `handle_get_plan_audit_logs` fix
that makes reported outcomes (including the new `delivery` outcome type) actually readable back.

Same `agent` fixture and request-model-validated style as `test_main.py` -- constructing requests
through the SDK's own models, not hand-built dicts, is what proves these handlers agree with the
protocol rather than with this file's own guess at its shape.

The four real fixture images (`fixtures/creatives/*.png`, checked into
`agents/seller/reference-seller/app/adcpRefSeller/`) are used directly here via a `file://` URL and a
monkeypatched fetch -- `handle_get_creative_features` itself only ever fetches over HTTPS in
production (see its own docstring), so these tests substitute a local read for the network call while
exercising every other real code path: real WCAG contrast, real OCR, real dimension comparison.
"""

from __future__ import annotations

from pathlib import Path

import boto3
import pytest
from adcp.signing.crypto import ALG_ED25519
from adcp.signing.keygen import generate_signing_keypair
from adcp.types import (
    GetCreativeFeaturesRequest,
    GetPlanAuditLogsRequest,
    ReportPlanOutcomeRequest,
    SyncPlansRequest,
)
from moto import mock_aws

import main
import state

TABLE = "adcp-reference-governance-state-test-creative"

FIXTURES_DIR = (
    Path(__file__).parent.parent.parent.parent.parent
    / "seller"
    / "reference-seller"
    / "app"
    / "adcpRefSeller"
    / "fixtures"
    / "creatives"
)

#: The buyer identity the spend aggregate is keyed on. Same values and same reason as `test_main.py`'s.
BUYER_PRINCIPAL = "https://reference-buyer.example/adcp"
ACCOUNT_ID = "acct-nike-001"


def _buyer_context():
    from adcp.server import AccountAwareToolContext

    return AccountAwareToolContext(
        caller_identity=BUYER_PRINCIPAL, account_id=ACCOUNT_ID, tenant_id=ACCOUNT_ID
    )


PLAN = {
    "plan_id": "plan-creative-1",
    "brand": {"domain": "nike.com"},
    "objectives": "Reach adults 18-49 with audio during December.",
    "budget": {"total": 100000.0, "currency": "USD", "reallocation_threshold": 0},
    "flight": {"start": "2026-12-05T00:00:00Z", "end": "2026-12-10T23:59:59Z"},
    "countries": ["US"],
    "channels": {"allowed": ["streaming_audio", "podcast"]},
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
        yield main.ReferenceGovernanceAgent()
        state.reset_for_tests()
        main._creative_fetch_cache.clear()
        main._creative_fetch_inflight.clear()


def _manifest(image_name: str, *, asset_width: int, asset_height: int) -> dict:
    return {
        "creative_manifest": {
            "format_id": {
                "agent_url": "https://reference-seller.example/adcp/mcp",
                "id": "display_300x250",
            },
            "assets": {
                "main_image": {
                    "asset_type": "image",
                    "url": f"https://fixture.test/{image_name}",
                    "width": asset_width,
                    "height": asset_height,
                }
            },
            "ext": {"format_dimensions": {"width": 300, "height": 250}},
        }
    }


async def _evaluate(handler, monkeypatch, image_name: str, *, asset_width=300, asset_height=250):
    """Runs handle_get_creative_features against a real local fixture image, standing in for the
    HTTPS fetch this handler does in production (see this module's own docstring)."""
    image_bytes = (FIXTURES_DIR / image_name).read_bytes()
    main._creative_fetch_cache[f"https://fixture.test/{image_name}"] = image_bytes
    request = GetCreativeFeaturesRequest.model_validate(
        _manifest(image_name, asset_width=asset_width, asset_height=asset_height)
    )
    return await handler.handle_get_creative_features(request)


class TestRealEvaluation:
    """Every one of these asserts against the REAL fixture images this project generated and
    published (deploy_creative_fixtures.py) -- not synthetic pixel data, so a change to the fixture
    generator that quietly breaks its own contrast/size assertions would also break these."""

    @pytest.mark.asyncio
    async def test_an_unreachable_url_reports_the_error_variant_not_a_working_status(self, agent):
        """A cold cache is not a status.

        This asserted `status == "working"` with no `results`, and that response could never have
        validated: `GetCreativeFeaturesResponse` is a two-variant union -- variant 1 requires
        `results`, variant 2 requires `errors` -- so a body with neither matches no variant. The live
        call failed with `VALIDATION_ERROR[/]: oneOf composition failed` while this test passed,
        because the test read the dict the handler returned and never validated it.

        The retry it expected is also not AdCP's async contract. The 30-second rule
        (docs/building/implementation/async-operations) says "Don't poll for `working`", and for this task
        docs/governance/creative pairs `working` with WEBHOOK delivery. `working` is therefore a real,
        conformant option for a slow evaluator -- it just needs `results` present (an empty array is
        valid) and a webhook to deliver into. This agent has neither a slow path nor a webhook: the work
        is one HTTPS fetch plus three local measurements, so it answers `completed` on the same call.
        """
        payload = _manifest("clean_banner.png", asset_width=300, asset_height=250)
        # A host that genuinely does not resolve, so this exercises the real fetch-failure path.
        payload["creative_manifest"]["assets"]["main_image"]["url"] = "https://never-fetched.invalid/x.png"
        request = GetCreativeFeaturesRequest.model_validate(payload)
        result = await agent.handle_get_creative_features(request)

        assert result["status"] == "completed"
        assert result.get("errors"), result
        assert "results" not in result
        assert result["errors"][0]["code"] == "UNSUPPORTED_FEATURE"

    @pytest.mark.asyncio
    async def test_every_response_validates_against_the_response_union(self, agent, monkeypatch):
        """The check the old test lacked: validate the handler's output, not just read it.

        Had this existed, the `working` branch would have failed here instead of in production.
        """
        from pydantic import TypeAdapter
        from adcp.types.generated_poc.creative.get_creative_features_response import (
            GetCreativeFeaturesResponse,
        )

        union = TypeAdapter(GetCreativeFeaturesResponse)

        # The success path, over a real fixture image.
        union.validate_python(await _evaluate(agent, monkeypatch, "clean_banner.png"))

        # The error path: no main_image at all.
        payload = _manifest("clean_banner.png", asset_width=300, asset_height=250)
        payload["creative_manifest"]["assets"] = {}
        union.validate_python(
            await agent.handle_get_creative_features(
                GetCreativeFeaturesRequest.model_validate(payload)
            )
        )

    @pytest.mark.asyncio
    async def test_clean_banner_conforms_and_has_no_urgency_claim(self, agent, monkeypatch):
        result = await _evaluate(agent, monkeypatch, "clean_banner.png")
        assert result["status"] == "completed"
        by_id = {r["feature_id"]: r for r in result["results"]}
        assert by_id["dimension_conformance"]["value"] is True
        assert by_id["urgency_claim"]["value"] is False
        # Real WCAG AA pass, matching gen_fixtures.py's own assertion when it built this fixture.
        assert by_id["contrast_ratio"]["value"] >= 4.5

    @pytest.mark.asyncio
    async def test_urgency_claim_banner_is_genuinely_detected_via_ocr(self, agent, monkeypatch):
        result = await _evaluate(agent, monkeypatch, "urgency_claim_banner.png")
        by_id = {r["feature_id"]: r for r in result["results"]}
        assert by_id["urgency_claim"]["value"] is True
        # The real matched phrase, not just a boolean -- proves this is a real OCR read, not a
        # filename-triggered shortcut.
        assert by_id["urgency_claim"]["details"]["matched_phrase"] == "sale ends"
        assert by_id["dimension_conformance"]["value"] is True

    @pytest.mark.asyncio
    async def test_low_contrast_banner_genuinely_fails_contrast(self, agent, monkeypatch):
        result = await _evaluate(agent, monkeypatch, "low_contrast_banner.png")
        by_id = {r["feature_id"]: r for r in result["results"]}
        # Real WCAG AA failure, matching gen_fixtures.py's own assertion (<1.5) when it built this
        # fixture.
        assert by_id["contrast_ratio"]["value"] < 1.5
        assert by_id["dimension_conformance"]["value"] is True
        assert by_id["urgency_claim"]["value"] is False

    @pytest.mark.asyncio
    async def test_wrong_size_banner_genuinely_fails_dimension_conformance(self, agent, monkeypatch):
        # The bug this test pins: dimension_conformance MUST compare against the FORMAT's required
        # dimensions (ext.format_dimensions), never against the asset's own self-reported
        # width/height -- comparing against the asset's own claim would trivially "pass" a
        # mis-sized image whose metadata happens to match itself.
        result = await _evaluate(
            agent, monkeypatch, "wrong_size_banner.png", asset_width=320, asset_height=250
        )
        by_id = {r["feature_id"]: r for r in result["results"]}
        assert by_id["dimension_conformance"]["value"] is False
        assert by_id["dimension_conformance"]["details"]["measured_width"] == 320
        assert by_id["dimension_conformance"]["details"]["format_required_width"] == 300

    @pytest.mark.asyncio
    async def test_a_missing_main_image_asset_is_a_validation_error_not_a_fabricated_result(
        self, agent
    ):
        payload = _manifest("clean_banner.png", asset_width=300, asset_height=250)
        payload["creative_manifest"]["assets"] = {}
        request = GetCreativeFeaturesRequest.model_validate(payload)
        result = await agent.handle_get_creative_features(request)
        assert result["errors"][0]["code"] == "VALIDATION_ERROR"
        assert "results" not in result

    @pytest.mark.asyncio
    async def test_an_unfetchable_url_reports_an_error_not_a_fabricated_result(self, agent):
        main._creative_fetch_cache["https://unreachable.test/x.png"] = ConnectionError("refused")
        payload = _manifest("clean_banner.png", asset_width=300, asset_height=250)
        payload["creative_manifest"]["assets"]["main_image"]["url"] = "https://unreachable.test/x.png"
        request = GetCreativeFeaturesRequest.model_validate(payload)
        result = await agent.handle_get_creative_features(request)
        assert result["errors"][0]["code"] == "UNSUPPORTED_FEATURE"
        assert "results" not in result


class TestGetPlanAuditLogsOutcomeEntries:
    """The real gap this unit fixes: outcome-type entries were never emitted even though the data has
    been stored via state.put_outcome since U2."""

    @pytest.mark.asyncio
    async def test_a_completed_outcome_appears_as_an_outcome_type_entry(self, agent):
        await agent.sync_plans(
            SyncPlansRequest.model_validate(
                {"idempotency_key": "idem-sync-0000000001", "plans": [PLAN]}
            )
        )
        await agent.report_plan_outcome(
            ReportPlanOutcomeRequest.model_validate(
                {
                    "plan_id": "plan-creative-1",
                    "idempotency_key": "idem-outcome-000000001",
                    "outcome": "completed",
                    "governance_context": "irrelevant-for-this-test",
                    "seller_response": {"seller_reference": "mb_1", "committed_budget": 5000.0},
                }
            )
        )
        response = await agent.get_plan_audit_logs(
            GetPlanAuditLogsRequest.model_validate(
                {"plan_ids": ["plan-creative-1"], "include_entries": True}
            )
        )
        plan = response.plans[0]
        outcome_entries = [e for e in plan.entries if e.type == "outcome"]
        assert len(outcome_entries) == 1
        assert outcome_entries[0].outcome == "completed"
        assert outcome_entries[0].committed_budget == 5000.0
        assert outcome_entries[0].outcome_status == "accepted"

    @pytest.mark.asyncio
    async def test_a_delivery_outcome_appears_with_no_committed_budget_field(self, agent):
        # A delivery outcome carries no committed_budget (see report_plan_outcome-request.json's
        # `delivery` object -- confirmed it has no such field) -- the entry must not fabricate one.
        await agent.sync_plans(
            SyncPlansRequest.model_validate(
                {"idempotency_key": "idem-sync-0000000002", "plans": [PLAN]}
            )
        )
        await agent.report_plan_outcome(
            ReportPlanOutcomeRequest.model_validate(
                {
                    "plan_id": "plan-creative-1",
                    "idempotency_key": "idem-outcome-000000002",
                    "outcome": "delivery",
                    "governance_context": "irrelevant-for-this-test",
                    "delivery": {
                        "reporting_period": {
                            "start": "2026-12-05T00:00:00Z",
                            "end": "2026-12-06T00:00:00Z",
                        },
                        "impressions": 10000,
                        "spend": 500.0,
                    },
                }
            )
        )
        response = await agent.get_plan_audit_logs(
            GetPlanAuditLogsRequest.model_validate(
                {"plan_ids": ["plan-creative-1"], "include_entries": True}
            )
        )
        plan = response.plans[0]
        outcome_entries = [e for e in plan.entries if e.type == "outcome"]
        assert len(outcome_entries) == 1
        assert outcome_entries[0].outcome == "delivery"
        assert outcome_entries[0].committed_budget is None

    @pytest.mark.asyncio
    async def test_check_and_outcome_entries_coexist_sorted_by_timestamp(self, agent):
        from adcp.types import CheckGovernanceRequest

        # An authenticated context, because the spend aggregate is keyed on an identity that lives there
        # rather than in the request body: `caller_identity` is the buyer principal recorded at
        # `sync_plans` time, `account_id` the resolved account. Without it the key cannot be formed and
        # the budget category correctly degrades to `conditions` -- an aggregate that was never consulted
        # must not read as zero. The other tests in this class do not need it because they do not assert
        # a verdict.
        await agent.sync_plans(
            SyncPlansRequest.model_validate(
                {"idempotency_key": "idem-sync-0000000003", "plans": [PLAN]}
            ),
            _buyer_context(),
        )
        checked = await agent.check_governance(
            CheckGovernanceRequest.model_validate(
                {
                    "plan_id": "plan-creative-1",
                    # A buyer-side intent check names BOTH parties, and they are different fields:
                    # `caller` is the orchestrator making the call, `target_agent` is the seller the
                    # check is about and what the token's `aud` binds to. This used to put the seller in
                    # `caller` and send no `target_agent` -- which resolved a seller only because the
                    # agent also read `caller`. Both sides were wrong together, so each looked like
                    # evidence for the other. With that corrected, an intent check without
                    # `target_agent` cannot resolve the seller, the spend aggregate degrades to an
                    # explicit unknown, and the verdict is `conditions` rather than `approved`.
                    "caller": "https://buyer.example/a2a",
                    "target_agent": "https://reference-seller.example/adcp/mcp",
                    "tool": "create_media_buy",
                    "payload": {
                        "budget": {"total": 5000.0, "currency": "USD"},
                        "start_date": "2026-12-05",
                        "end_date": "2026-12-10",
                        "countries": ["US"],
                        "channels": ["streaming_audio"],
                    },
                }
            ),
            _buyer_context(),
        )
        # The point of sending `target_agent` with an authenticated context: the aggregate key forms, so
        # the check can actually be authorised. A `conditions` here would mean identity failed again --
        # which is precisely the defect that made every first check refuse.
        assert checked.verdict == "approved", checked.explanation
        assert checked.governance_context
        await agent.report_plan_outcome(
            ReportPlanOutcomeRequest.model_validate(
                {
                    "plan_id": "plan-creative-1",
                    "idempotency_key": "idem-outcome-000000003",
                    "outcome": "completed",
                    "governance_context": checked.governance_context,
                    "check_id": checked.check_id,
                    "seller_response": {"seller_reference": "mb_1", "committed_budget": 5000.0},
                }
            )
        )
        response = await agent.get_plan_audit_logs(
            GetPlanAuditLogsRequest.model_validate(
                {"plan_ids": ["plan-creative-1"], "include_entries": True}
            )
        )
        plan = response.plans[0]
        types = [e.type for e in plan.entries]
        assert types == ["check", "outcome"]
        # Summary counts must agree with the entries actually returned.
        assert plan.summary.checks_performed == 1
        assert plan.summary.outcomes_reported == 1


class TestDeclaredCreativeFeaturesMatchWhatIsComputed:
    """`get_adcp_capabilities` must declare exactly the features the evaluator produces.

    Declaring one the handler cannot measure is the worse of the two failures: a buyer would submit
    creatives expecting an assessment that never arrives. Under-declaring is the state this was in
    before — `governance.creative_features` was omitted entirely while three features were really being
    computed, so the evaluation existed and no buyer could discover it.

    Asserted as set equality against a real evaluation over a real fixture, so neither side can drift.
    """

    @pytest.mark.asyncio
    async def test_the_declaration_is_exactly_the_computed_set(self, agent, monkeypatch):
        from adcp.types import GetAdcpCapabilitiesRequest

        result = await _evaluate(agent, monkeypatch, "clean_banner.png")
        computed = {r["feature_id"] for r in result["results"]}

        capabilities = await agent.get_adcp_capabilities(
            GetAdcpCapabilitiesRequest.model_validate({})
        )
        declared = {f.feature_id for f in (capabilities.governance.creative_features or [])}

        assert declared == computed, (
            f"declared={sorted(declared)} computed={sorted(computed)}. A declared-but-uncomputed "
            "feature promises an assessment that never arrives; a computed-but-undeclared one is "
            "invisible to buyers."
        )

    @pytest.mark.asyncio
    async def test_each_declared_feature_carries_a_type_the_enum_allows(self, agent):
        from adcp.types import GetAdcpCapabilitiesRequest

        capabilities = await agent.get_adcp_capabilities(
            GetAdcpCapabilitiesRequest.model_validate({})
        )
        features = {f.feature_id: f for f in (capabilities.governance.creative_features or [])}

        # `contrast_ratio` is a real number on a defined scale, so it must not be declared binary.
        assert features["contrast_ratio"].type.value == "quantitative"
        assert features["dimension_conformance"].type.value == "binary"
        assert features["urgency_claim"].type.value == "binary"

    @pytest.mark.asyncio
    async def test_the_contrast_range_is_wcags_own_scale(self, agent, monkeypatch):
        """1:1 for identical colours to 21:1 for black on white, and a real measurement must fall in it."""
        from adcp.types import GetAdcpCapabilitiesRequest

        capabilities = await agent.get_adcp_capabilities(
            GetAdcpCapabilitiesRequest.model_validate({})
        )
        contrast = next(
            f for f in capabilities.governance.creative_features if f.feature_id == "contrast_ratio"
        )
        assert (contrast.range.min, contrast.range.max) == (1.0, 21.0)

        result = await _evaluate(agent, monkeypatch, "clean_banner.png")
        measured = next(r for r in result["results"] if r["feature_id"] == "contrast_ratio")["value"]
        assert contrast.range.min <= measured <= contrast.range.max
