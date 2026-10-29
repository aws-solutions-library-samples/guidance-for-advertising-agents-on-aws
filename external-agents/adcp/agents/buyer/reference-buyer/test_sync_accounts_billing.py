"""`adcp_sync_accounts` sends the `billing` party AdCP requires on a provisioning-mode entry.

Mirrors the fixture pattern of the other tool tests (`_build_tools`, a fake `_call_seller_tool` that records
what it was sent). No network calls.
"""

from __future__ import annotations

import asyncio
from typing import Any

import adcp_tools

_SELLERS = [
    {"id": "ref", "name": "Ref Seller", "url": "https://seller.test/mcp", "transport": "mcp", "headers": {}},
    {"id": "other", "name": "Other Seller", "url": "https://other.test/mcp", "transport": "mcp", "headers": {}},
]


def _tools() -> dict[str, Any]:
    tools = adcp_tools.build_adcp_tools(_SELLERS, buyer_session_id=None)
    return {getattr(t, "tool_name", None) or t.__name__: t for t in tools}


def _record_calls(monkeypatch) -> list[tuple[str, str, dict[str, Any]]]:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    async def fake_call(seller_agent, tool_name, args, buyer_session_id=None):
        calls.append((seller_agent["id"], tool_name, args))
        return {"accounts": [{"action": "created", "status": "active"}]}

    monkeypatch.setattr(adcp_tools, "_call_seller_tool", fake_call)
    return calls


def _sync(**kwargs: Any) -> dict[str, Any]:
    return asyncio.run(
        _tools()["adcp_sync_accounts"](brand_domain="acme.example", operator="agency.example", **kwargs)
    )


class TestBillingParty:
    def test_default_is_operator_on_every_entry_sent_to_every_seller(self, monkeypatch):
        monkeypatch.delenv(adcp_tools.ACCOUNT_BILLING_ENV, raising=False)
        calls = _record_calls(monkeypatch)
        _sync()
        assert [seller for seller, _, _ in calls] == ["ref", "other"]
        for _, tool_name, args in calls:
            assert tool_name == "sync_accounts"
            assert args["accounts"] == [
                {"brand": {"domain": "acme.example"}, "operator": "agency.example", "billing": "operator"}
            ]

    def test_the_deployment_default_comes_from_the_env_var(self, monkeypatch):
        monkeypatch.setenv(adcp_tools.ACCOUNT_BILLING_ENV, "advertiser")
        calls = _record_calls(monkeypatch)
        _sync()
        assert {args["accounts"][0]["billing"] for _, _, args in calls} == {"advertiser"}

    def test_an_explicit_billing_argument_overrides_the_default(self, monkeypatch):
        monkeypatch.setenv(adcp_tools.ACCOUNT_BILLING_ENV, "advertiser")
        calls = _record_calls(monkeypatch)
        _sync(billing="agent")
        assert {args["accounts"][0]["billing"] for _, _, args in calls} == {"agent"}

    def test_an_invalid_configured_value_is_refused_before_any_seller_is_called(self, monkeypatch):
        monkeypatch.setenv(adcp_tools.ACCOUNT_BILLING_ENV, "brand")
        calls = _record_calls(monkeypatch)
        result = _sync()
        assert result["error"] == "invalid_billing"
        assert adcp_tools.ACCOUNT_BILLING_ENV in result["message"]
        assert calls == []

    def test_the_value_set_is_the_specs(self):
        # enums/billing-party.json in AdCP 3.1.
        assert set(adcp_tools.BILLING_PARTIES) == {"operator", "agent", "advertiser"}
        assert adcp_tools.DEFAULT_ACCOUNT_BILLING in adcp_tools.BILLING_PARTIES

    def test_the_rest_of_the_request_is_unchanged(self, monkeypatch):
        calls = _record_calls(monkeypatch)
        _sync(seller_ids=["ref"])
        assert len(calls) == 1
        _, _, args = calls[0]
        assert set(args) == {"idempotency_key", "accounts"}
        assert len(args["idempotency_key"]) >= 16
