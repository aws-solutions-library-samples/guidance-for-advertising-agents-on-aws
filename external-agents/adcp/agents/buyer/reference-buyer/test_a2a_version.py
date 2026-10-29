"""The per-seller `a2a_version` opt-in: A2A 0.3 by default, A2A 1.0 when a seller entry asks for it.

The 0.3 tests pin the request this buyer has always sent, so the opt-in cannot change it. The 1.0 tests pin
the four places A2A 1.0 changed the wire format for this call (method, version header, role, `kind`), each
of which a 1.0-only server rejects. No network calls: httpx is given a MockTransport that records the
request and answers with a canned JSONRPC envelope.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

import adcp_tools
import seller_agents

_ENTRY = {
    "id": "a2a-seller",
    "name": "A2A seller",
    "url": "https://seller.example/a2a",
    "transport": "a2a",
    "auth_type": "static_bearer",
    "auth_token_env": "A2A_SELLER_TOKEN",
}

_PRODUCTS = {"products": [{"product_id": "p1"}]}

#: A 1.0 SendMessage result: the SendMessageResponse oneof wrapping a Task, ProtoJSON state, no `kind`.
_V1_RESULT = {
    "task": {
        "id": "t1",
        "contextId": "c1",
        "status": {"state": "TASK_STATE_COMPLETED"},
        "artifacts": [{"artifactId": "a1", "parts": [{"data": _PRODUCTS}]}],
    }
}

#: A 0.3 message/send result: the bare Task, lowercase state, `kind` discriminators.
_V03_RESULT = {
    "kind": "task",
    "id": "t1",
    "contextId": "c1",
    "status": {"state": "completed"},
    "artifacts": [{"artifactId": "a1", "parts": [{"kind": "data", "data": _PRODUCTS}]}],
}


def _capture(monkeypatch, result: dict[str, Any]) -> dict[str, Any]:
    """Route adcp_tools' httpx client through a MockTransport; return what the request carried."""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        adcp_tools.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    return seen


def _has_key(value: Any, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(_has_key(v, key) for v in value.values())
    if isinstance(value, list):
        return any(_has_key(v, key) for v in value)
    return False


class TestRegistryEntry:
    def test_a2a_version_defaults_to_0_3(self):
        assert seller_agents.validate_seller_entry(dict(_ENTRY))["a2a_version"] == "0.3"

    def test_a2a_version_1_0_is_accepted(self):
        entry = seller_agents.validate_seller_entry({**_ENTRY, "a2a_version": "1.0"})
        assert entry["a2a_version"] == "1.0"

    def test_an_unknown_a2a_version_is_rejected_with_the_allowed_values(self):
        with pytest.raises(seller_agents.SellerAgentError, match="a2a_version"):
            seller_agents.validate_seller_entry({**_ENTRY, "a2a_version": "2.0"})

    def test_resolve_carries_a2a_version(self, monkeypatch):
        monkeypatch.setenv("SELLER_AGENTS_JSON", json.dumps([{**_ENTRY, "a2a_version": "1.0"}]))
        monkeypatch.setenv("A2A_SELLER_TOKEN", "tok-1")
        resolved = seller_agents.resolve_seller_agent("a2a-seller")
        assert resolved["a2a_version"] == "1.0"
        assert resolved["headers"] == {"Authorization": "Bearer tok-1"}

    def test_a2a_version_is_a_stored_registration_field(self):
        # An admin-registered seller must keep its version across the store round trip.
        import agent_registry_store

        assert "a2a_version" in agent_registry_store._STORED_FIELDS


class TestA2A03Default:
    def test_request_is_the_0_3_message_send_shape(self, monkeypatch):
        seen = _capture(monkeypatch, _V03_RESULT)
        result = asyncio.run(
            adcp_tools._call_seller_tool_a2a(
                "https://seller.example/a2a", {"Authorization": "Bearer t"}, "get_products", {"brief": "ctv"}
            )
        )
        assert result == _PRODUCTS
        body = seen["body"]
        assert body["method"] == "message/send"
        message = body["params"]["message"]
        assert message["role"] == "user"
        assert message["kind"] == "message"
        part = message["parts"][0]
        assert part["kind"] == "data"
        expected_args = {"adcp_major_version": 3, "brief": "ctv"}
        assert part["data"] == {"skill": "get_products", "input": expected_args, "parameters": expected_args}
        assert "a2a-version" not in seen["headers"]

    def test_a_seller_without_the_field_is_called_with_0_3(self, monkeypatch):
        # Caller-built seller dicts and governance agent entries carry no a2a_version.
        seen = _capture(monkeypatch, _V03_RESULT)
        seller = {"id": "s", "url": "https://seller.example/a2a", "transport": "a2a", "headers": {}}
        asyncio.run(adcp_tools._call_seller_tool(seller, "get_adcp_capabilities", {}))
        assert seen["body"]["method"] == "message/send"


class TestA2A10OptIn:
    def _call(self, monkeypatch, session_id=None):
        seen = _capture(monkeypatch, _V1_RESULT)
        seller = {
            "id": "s",
            "url": "https://seller.example/a2a",
            "transport": "a2a",
            "a2a_version": "1.0",
            "headers": {"Authorization": "Bearer t"},
        }
        result = asyncio.run(
            adcp_tools._call_seller_tool(seller, "get_products", {"brief": "ctv"}, buyer_session_id=session_id)
        )
        return seen, result

    def test_method_is_send_message_with_the_version_header(self, monkeypatch):
        seen, _ = self._call(monkeypatch)
        assert seen["body"]["method"] == "SendMessage"
        assert seen["headers"]["a2a-version"] == "1.0"
        assert seen["headers"]["authorization"] == "Bearer t"

    def test_message_uses_protojson_role_and_no_kind_anywhere(self, monkeypatch):
        seen, _ = self._call(monkeypatch)
        message = seen["body"]["params"]["message"]
        assert message["role"] == "ROLE_USER"
        assert not _has_key(seen["body"], "kind")

    def test_data_part_carries_skill_and_input_only(self, monkeypatch):
        seen, _ = self._call(monkeypatch)
        parts = seen["body"]["params"]["message"]["parts"]
        assert parts == [
            {"data": {"skill": "get_products", "input": {"adcp_major_version": 3, "brief": "ctv"}}}
        ]

    def test_context_id_and_session_headers_are_sent_as_on_0_3(self, monkeypatch):
        seen, _ = self._call(monkeypatch, session_id="buyer-session-0123456789abcdef0123456789")
        expected = adcp_tools.derive_seller_session_id("buyer-session-0123456789abcdef0123456789", "s")
        assert seen["body"]["params"]["message"]["contextId"] == expected
        assert seen["headers"][adcp_tools.BUYER_SESSION_HEADER.lower()] == expected

    def test_the_send_message_task_envelope_is_unwrapped(self, monkeypatch):
        _, result = self._call(monkeypatch)
        assert result == _PRODUCTS

    def test_a_jsonrpc_error_is_returned_as_a2a_error(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "Method not found"}}
            )

        real_client = httpx.AsyncClient
        monkeypatch.setattr(
            adcp_tools.httpx,
            "AsyncClient",
            lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
        )
        result = asyncio.run(
            adcp_tools._call_seller_tool_a2a(
                "https://seller.example/a2a", {}, "get_products", {}, a2a_version="1.0"
            )
        )
        assert result == {"a2a_error": {"code": -32601, "message": "Method not found"}}
