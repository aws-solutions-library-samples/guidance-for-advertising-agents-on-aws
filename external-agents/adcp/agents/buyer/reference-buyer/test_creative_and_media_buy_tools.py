"""U4/Part 1: the three new/fixed buyer tools -- `adcp_get_media_buys`, `adcp_report_plan_outcome`'s
new `delivery` outcome branch, `adcp_get_plan_audit_logs`, and `adcp_get_creative_features`.

Mirrors `test_governance_tools.py`'s own fixture pattern (`_build_tools`, `_BOLTIVE_JSON`,
`monkeypatch.setattr(adcp_tools, "_call_seller_tool", fake_call)`) rather than inventing a second one.
No network calls -- every seller/governance-agent call is a fake recording what args it received.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

import adcp_tools

_BOLTIVE_JSON = (
    '[{"id":"boltive","name":"Boltive","url":"https://example.test/mcp",'
    '"auth_type":"static_bearer","auth_token_env":"BOLTIVE_TOK"}]'
)


def _build_tools(monkeypatch, governance_json, seller_url="https://seller.test/mcp"):
    monkeypatch.setenv("GOVERNANCE_AGENTS_JSON", governance_json)
    sellers = [
        {
            "id": "ref",
            "name": "Ref Seller",
            "url": seller_url,
            "agent_url": "https://ref-seller.example/adcp/mcp",
            "transport": "mcp",
            "headers": {},
        }
    ]
    tools = adcp_tools.build_adcp_tools(sellers, buyer_session_id=None)
    return {getattr(t, "tool_name", None) or t.__name__: t for t in tools}


class TestRegistration:
    def test_all_three_new_tools_are_registered_under_their_adcp_names(self):
        # Same failure class this project has hit before: a defined tool absent from the factory's
        # return list. Pinned here for the three tools this unit adds/fixes.
        sellers = [{"id": "ref", "name": "Ref", "url": "https://x/mcp", "transport": "mcp", "headers": {}}]
        tools = adcp_tools.build_adcp_tools(sellers, buyer_session_id=None)
        names = {getattr(t, "tool_name", None) or t.__name__ for t in tools}
        assert "adcp_get_media_buys" in names
        assert "adcp_get_plan_audit_logs" in names
        assert "adcp_get_creative_features" in names


class TestGetMediaBuys:
    def test_calls_the_named_sellers_get_media_buys_task(self, monkeypatch):
        tools = _build_tools(monkeypatch, _BOLTIVE_JSON)

        seen: dict[str, Any] = {}

        async def fake_call(seller_agent, tool_name, args, buyer_session_id=None):
            seen["tool_name"] = tool_name
            seen["args"] = args
            return {"media_buys": []}

        monkeypatch.setattr(adcp_tools, "_call_seller_tool", fake_call)

        asyncio.run(tools["adcp_get_media_buys"](seller_id="ref"))
        assert seen["tool_name"] == "get_media_buys"
        # A read, not a spend-commit action -- no governance gate, no idempotency_key required by
        # this task's own schema (confirmed against get-media-buys-request.json).
        assert seen["args"]["include_snapshot"] is False

    def test_media_buy_ids_and_status_filter_are_passed_through_when_given(self, monkeypatch):
        tools = _build_tools(monkeypatch, _BOLTIVE_JSON)
        seen: dict[str, Any] = {}

        async def fake_call(seller_agent, tool_name, args, buyer_session_id=None):
            seen.update(args)
            return {"media_buys": []}

        monkeypatch.setattr(adcp_tools, "_call_seller_tool", fake_call)

        asyncio.run(
            tools["adcp_get_media_buys"](
                seller_id="ref",
                media_buy_ids=["mb_1", "mb_2"],
                status_filter="active",
                include_snapshot=True,
            )
        )
        assert seen["media_buy_ids"] == ["mb_1", "mb_2"]
        assert seen["status_filter"] == "active"
        assert seen["include_snapshot"] is True

    def test_omits_media_buy_ids_and_status_filter_when_not_given(self, monkeypatch):
        # No empty-list/empty-string standing in for "the caller did not ask for a filter" -- absent
        # means absent on the wire, same discipline the rest of this project's decoders follow.
        tools = _build_tools(monkeypatch, _BOLTIVE_JSON)
        seen: dict[str, Any] = {}

        async def fake_call(seller_agent, tool_name, args, buyer_session_id=None):
            seen.update(args)
            return {"media_buys": []}

        monkeypatch.setattr(adcp_tools, "_call_seller_tool", fake_call)

        asyncio.run(tools["adcp_get_media_buys"](seller_id="ref"))
        assert "media_buy_ids" not in seen
        assert "status_filter" not in seen


class TestReportPlanOutcomeDeliveryBranch:
    """The real gap this unit fixes: `outcome="delivery"` previously built no request body at all."""

    def test_delivery_outcome_builds_a_real_delivery_request_body(self, monkeypatch):
        tools = _build_tools(monkeypatch, _BOLTIVE_JSON)
        monkeypatch.setenv("BOLTIVE_TOK", "tok-abc")
        seen: dict[str, Any] = {}

        async def fake_call(seller_agent, tool_name, args, buyer_session_id=None):
            seen.update(args)
            return {"outcome_id": "out_1", "outcome_state": "accepted"}

        monkeypatch.setattr(adcp_tools, "_call_seller_tool", fake_call)

        asyncio.run(
            tools["adcp_report_plan_outcome"](
                plan_id="plan-1",
                governance_context="tok",
                outcome="delivery",
                reporting_period_start="2026-01-01T00:00:00Z",
                reporting_period_end="2026-01-07T23:59:59Z",
                impressions=10000,
                spend=500.0,
                cpm=50.0,
                viewability_rate=0.92,
                completion_rate=0.87,
            )
        )
        assert seen["outcome"] == "delivery"
        assert seen["delivery"]["reporting_period"] == {
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-01-07T23:59:59Z",
        }
        assert seen["delivery"]["impressions"] == 10000
        assert seen["delivery"]["spend"] == 500.0
        assert seen["delivery"]["cpm"] == 50.0
        assert seen["delivery"]["viewability_rate"] == 0.92
        assert seen["delivery"]["completion_rate"] == 0.87
        # check_id is optional for a delivery outcome -- not tied to a single prior check.
        assert "check_id" not in seen

    def test_delivery_outcome_omits_optional_metrics_not_supplied(self, monkeypatch):
        # A caller reporting only spend, with no impression/cpm/viewability/completion figures, must
        # not have those fields fabricated as zero or null-standing-in-for-absent.
        tools = _build_tools(monkeypatch, _BOLTIVE_JSON)
        monkeypatch.setenv("BOLTIVE_TOK", "tok-abc")
        seen: dict[str, Any] = {}

        async def fake_call(seller_agent, tool_name, args, buyer_session_id=None):
            seen.update(args)
            return {"outcome_id": "out_1"}

        monkeypatch.setattr(adcp_tools, "_call_seller_tool", fake_call)

        asyncio.run(
            tools["adcp_report_plan_outcome"](
                plan_id="plan-1",
                governance_context="tok",
                outcome="delivery",
                reporting_period_start="2026-01-01T00:00:00Z",
                reporting_period_end="2026-01-07T23:59:59Z",
                spend=500.0,
            )
        )
        assert seen["delivery"]["spend"] == 500.0
        assert "impressions" not in seen["delivery"]
        assert "cpm" not in seen["delivery"]
        assert "viewability_rate" not in seen["delivery"]
        assert "completion_rate" not in seen["delivery"]

    def test_completed_outcome_still_works_unchanged(self, monkeypatch):
        # Regression guard: adding the delivery branch must not disturb the existing two.
        tools = _build_tools(monkeypatch, _BOLTIVE_JSON)
        monkeypatch.setenv("BOLTIVE_TOK", "tok-abc")
        seen: dict[str, Any] = {}

        async def fake_call(seller_agent, tool_name, args, buyer_session_id=None):
            seen.update(args)
            return {"outcome_id": "out_1"}

        monkeypatch.setattr(adcp_tools, "_call_seller_tool", fake_call)

        asyncio.run(
            tools["adcp_report_plan_outcome"](
                plan_id="plan-1",
                check_id="chk_1",
                governance_context="tok",
                outcome="completed",
                seller_reference="mb_1",
                committed_budget=1000.0,
            )
        )
        assert seen["outcome"] == "completed"
        assert seen["check_id"] == "chk_1"
        assert seen["seller_response"]["seller_reference"] == "mb_1"
        assert seen["seller_response"]["committed_budget"] == 1000.0
        assert "delivery" not in seen

    def test_failed_outcome_still_works_unchanged(self, monkeypatch):
        tools = _build_tools(monkeypatch, _BOLTIVE_JSON)
        monkeypatch.setenv("BOLTIVE_TOK", "tok-abc")
        seen: dict[str, Any] = {}

        async def fake_call(seller_agent, tool_name, args, buyer_session_id=None):
            seen.update(args)
            return {"outcome_id": "out_1"}

        monkeypatch.setattr(adcp_tools, "_call_seller_tool", fake_call)

        asyncio.run(
            tools["adcp_report_plan_outcome"](
                plan_id="plan-1",
                check_id="chk_1",
                governance_context="tok",
                outcome="failed",
                error_code="INVENTORY_UNAVAILABLE",
                error_message="no inventory left",
            )
        )
        assert seen["outcome"] == "failed"
        assert seen["error"] == {"code": "INVENTORY_UNAVAILABLE", "message": "no inventory left"}
        assert "delivery" not in seen


class TestGetPlanAuditLogs:
    def test_calls_the_governance_agents_get_plan_audit_logs_task(self, monkeypatch):
        tools = _build_tools(monkeypatch, _BOLTIVE_JSON)
        monkeypatch.setenv("BOLTIVE_TOK", "tok-abc")
        seen: dict[str, Any] = {}

        async def fake_call(seller_agent, tool_name, args, buyer_session_id=None):
            seen["tool_name"] = tool_name
            seen["args"] = args
            return {"plans": []}

        monkeypatch.setattr(adcp_tools, "_call_seller_tool", fake_call)

        asyncio.run(tools["adcp_get_plan_audit_logs"](plan_ids=["plan-1", "plan-2"]))
        assert seen["tool_name"] == "get_plan_audit_logs"
        assert seen["args"]["plan_ids"] == ["plan-1", "plan-2"]
        # Defaults to true -- the summary alone rarely answers what a caller actually wants.
        assert seen["args"]["include_entries"] is True

    def test_include_entries_can_be_disabled(self, monkeypatch):
        tools = _build_tools(monkeypatch, _BOLTIVE_JSON)
        monkeypatch.setenv("BOLTIVE_TOK", "tok-abc")
        seen: dict[str, Any] = {}

        async def fake_call(seller_agent, tool_name, args, buyer_session_id=None):
            seen.update(args)
            return {"plans": []}

        monkeypatch.setattr(adcp_tools, "_call_seller_tool", fake_call)

        asyncio.run(tools["adcp_get_plan_audit_logs"](plan_ids=["plan-1"], include_entries=False))
        assert seen["include_entries"] is False

    def test_targets_the_governance_agent_not_a_seller(self, monkeypatch):
        # A read against the GOVERNANCE agent, same as adcp_check_governance/adcp_report_plan_outcome
        # -- must go through resolve_governance_agent(), never scope()/a seller lookup.
        tools = _build_tools(monkeypatch, _BOLTIVE_JSON)
        monkeypatch.setenv("BOLTIVE_TOK", "tok-abc")
        seen_agent: dict[str, Any] = {}

        async def fake_call(seller_agent, tool_name, args, buyer_session_id=None):
            seen_agent.update(seller_agent)
            return {"plans": []}

        monkeypatch.setattr(adcp_tools, "_call_seller_tool", fake_call)

        asyncio.run(tools["adcp_get_plan_audit_logs"](plan_ids=["plan-1"]))
        assert seen_agent["id"] == "boltive"


class TestGetCreativeFeatures:
    def test_builds_a_real_creative_manifest_with_format_dimensions_in_ext(self, monkeypatch):
        tools = _build_tools(monkeypatch, _BOLTIVE_JSON)
        monkeypatch.setenv("BOLTIVE_TOK", "tok-abc")
        seen: dict[str, Any] = {}

        async def fake_call(seller_agent, tool_name, args, buyer_session_id=None):
            seen["tool_name"] = tool_name
            seen["args"] = args
            return {"status": "completed", "results": []}

        monkeypatch.setattr(adcp_tools, "_call_seller_tool", fake_call)

        asyncio.run(
            tools["adcp_get_creative_features"](
                format_id="display_300x250",
                format_agent_url="https://reference-seller.example/adcp/mcp",
                format_width=300,
                format_height=250,
                asset_url="https://d111111abcdef8.cloudfront.net/.well-known/creatives/clean_banner.png",
                asset_width=300,
                asset_height=250,
            )
        )
        assert seen["tool_name"] == "get_creative_features"
        manifest = seen["args"]["creative_manifest"]
        assert manifest["format_id"] == {
            "agent_url": "https://reference-seller.example/adcp/mcp",
            "id": "display_300x250",
        }
        assert manifest["assets"]["main_image"]["url"].endswith("clean_banner.png")
        assert manifest["assets"]["main_image"]["width"] == 300
        assert manifest["assets"]["main_image"]["height"] == 250
        # The FORMAT's required dimensions, kept distinct from the asset's own claimed dimensions
        # above -- see adcp_get_creative_features's own docstring for why a dimension-conformance
        # check needs both, not just one.
        assert manifest["ext"]["format_dimensions"] == {"width": 300, "height": 250}

    def test_feature_ids_filter_is_passed_through_only_when_given(self, monkeypatch):
        tools = _build_tools(monkeypatch, _BOLTIVE_JSON)
        monkeypatch.setenv("BOLTIVE_TOK", "tok-abc")
        seen: dict[str, Any] = {}

        async def fake_call(seller_agent, tool_name, args, buyer_session_id=None):
            seen.update(args)
            return {"status": "completed", "results": []}

        monkeypatch.setattr(adcp_tools, "_call_seller_tool", fake_call)

        asyncio.run(
            tools["adcp_get_creative_features"](
                format_id="display_300x250",
                format_agent_url="https://reference-seller.example/adcp/mcp",
                format_width=300,
                format_height=250,
                asset_url="https://x.example/img.png",
                asset_width=300,
                asset_height=250,
            )
        )
        assert "feature_ids" not in seen

        seen.clear()
        asyncio.run(
            tools["adcp_get_creative_features"](
                format_id="display_300x250",
                format_agent_url="https://reference-seller.example/adcp/mcp",
                format_width=300,
                format_height=250,
                asset_url="https://x.example/img.png",
                asset_width=300,
                asset_height=250,
                feature_ids=["dimension_conformance"],
            )
        )
        assert seen["feature_ids"] == ["dimension_conformance"]

    def test_targets_the_governance_agent_not_a_seller(self, monkeypatch):
        tools = _build_tools(monkeypatch, _BOLTIVE_JSON)
        monkeypatch.setenv("BOLTIVE_TOK", "tok-abc")
        seen_agent: dict[str, Any] = {}

        async def fake_call(seller_agent, tool_name, args, buyer_session_id=None):
            seen_agent.update(seller_agent)
            return {"status": "completed", "results": []}

        monkeypatch.setattr(adcp_tools, "_call_seller_tool", fake_call)

        asyncio.run(
            tools["adcp_get_creative_features"](
                format_id="display_300x250",
                format_agent_url="https://reference-seller.example/adcp/mcp",
                format_width=300,
                format_height=250,
                asset_url="https://x.example/img.png",
                asset_width=300,
                asset_height=250,
            )
        )
        assert seen_agent["id"] == "boltive"
