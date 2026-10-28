"""The `no_prior_check` refusal says why booking cannot proceed when no governance agent is configured.

The gate itself is unchanged and still applies with no governance agent; only its message differs. No
network calls: the fake `_call_seller_tool` fails the test if the seller is ever contacted.
"""

from __future__ import annotations

import asyncio
from typing import Any

import adcp_tools

_GOVERNANCE_JSON = (
    '[{"id":"gov","name":"Governance","url":"https://gov.test/mcp",'
    '"auth_type":"static_bearer","auth_token_env":"GOV_TOK"}]'
)

_BOOKING = {
    "seller_id": "ref",
    "plan_id": "plan-1",
    "governance_context": "",
    "product_id": "p1",
    "budget": 500.0,
    "pricing_option_id": "cpm_usd_fixed",
    "brand_domain": "acme.example",
    "operator": "agency.example",
    "flight_start": "2026-01-01",
    "flight_end": "2026-01-31",
}


def _book(monkeypatch) -> dict[str, Any]:
    async def fake_call(seller_agent, tool_name, args, buyer_session_id=None):
        raise AssertionError(f"the seller was contacted ({tool_name}) without an approved check")

    monkeypatch.setattr(adcp_tools, "_call_seller_tool", fake_call)
    sellers = [{"id": "ref", "name": "Ref", "url": "https://seller.test/mcp", "transport": "mcp", "headers": {}}]
    tools = {getattr(t, "tool_name", None) or t.__name__: t for t in adcp_tools.build_adcp_tools(sellers)}
    return asyncio.run(tools["adcp_create_media_buy"](**_BOOKING))


def test_without_a_governance_agent_the_refusal_names_the_missing_configuration(monkeypatch):
    monkeypatch.delenv("GOVERNANCE_AGENTS_JSON", raising=False)
    result = _book(monkeypatch)
    assert result["error"] == "no_prior_check"
    assert isinstance(result["message"], str)
    assert "no governance agent is configured" in result["message"]
    assert "GOVERNANCE_AGENTS_JSON" in result["message"]


def test_with_a_governance_agent_the_refusal_asks_for_the_check(monkeypatch):
    monkeypatch.setenv("GOVERNANCE_AGENTS_JSON", _GOVERNANCE_JSON)
    result = _book(monkeypatch)
    assert result["error"] == "no_prior_check"
    assert isinstance(result["message"], str)
    assert "Call adcp_check_governance" in result["message"]
    assert "plan_id='plan-1'" in result["message"]
