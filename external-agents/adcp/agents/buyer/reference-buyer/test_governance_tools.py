"""The buyer's governance tools: flight normalisation and governance-agent resolution.

Both behaviours here were forced by the LIVE governance agent rejecting a request, not by reading a spec
and guessing. Recorded so the next change does not reintroduce either:

    INVALID_REQUEST[plans.0.flight.start]: Input should have timezone info
    INVALID_REQUEST[plans.0.budget.Budget.reallocation_threshold]: Field required

No network calls: `_as_aware_iso` is pure, and `resolve_governance_agent` (now in governance_agents.py,
BR-U2-3/BR-U2-4) is exercised only for its configuration handling.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timezone
from typing import Any

import pytest

import adcp_tools
import governance_agents


class TestAsAwareIso:
    def test_a_bare_date_becomes_the_start_of_that_day_in_utc(self):
        assert adcp_tools._as_aware_iso("2026-01-01", end_of_day=False) == "2026-01-01T00:00:00+00:00"

    def test_a_bare_end_date_covers_the_whole_final_day(self):
        # An inclusive range 1 Jan..30 Jan must include all of 30 January. Treating the end as midnight
        # would expire the flight as it began -- a real spend error, not a formatting nit.
        assert adcp_tools._as_aware_iso("2026-01-30", end_of_day=True) == "2026-01-30T23:59:59+00:00"

    def test_always_carries_timezone_info(self):
        # The exact property the live agent rejected. Asserted on tzinfo rather than on the string, so a
        # different-but-valid offset format still satisfies it.
        from datetime import datetime

        for value, eod in (("2026-01-01", False), ("2026-06-05T12:30:00", True)):
            parsed = datetime.fromisoformat(adcp_tools._as_aware_iso(value, end_of_day=eod))
            assert parsed.tzinfo is not None

    def test_preserves_an_explicit_offset_rather_than_forcing_utc(self):
        # A caller who stated a zone meant it.
        out = adcp_tools._as_aware_iso("2026-01-01T09:00:00+09:00", end_of_day=False)
        assert out == "2026-01-01T09:00:00+09:00"

    def test_accepts_a_trailing_z(self):
        out = adcp_tools._as_aware_iso("2026-01-01T00:00:00Z", end_of_day=False)
        assert out.endswith("+00:00")

    def test_does_not_shift_the_time_of_an_explicit_datetime(self):
        # end_of_day only applies to a BARE date. Rewriting a stated time to 23:59:59 would silently
        # move a flight boundary the caller chose.
        out = adcp_tools._as_aware_iso("2026-01-30T08:15:00", end_of_day=True)
        assert out == "2026-01-30T08:15:00+00:00"

    def test_rejects_nonsense_with_a_useful_message(self):
        with pytest.raises(adcp_tools.AdcpToolError, match="ISO-8601"):
            adcp_tools._as_aware_iso("next tuesday", end_of_day=False)


class TestResolveGovernanceAgent:
    def test_refuses_when_the_registry_is_unset_rather_than_calling_nothing(self, monkeypatch):
        # A governance agent can now come from the environment OR the runtime registration store, so
        # "unset" here means neither source has one. The message changed to say so; the refusal
        # behaviour (raise rather than silently call nothing) is unchanged.
        monkeypatch.delenv("GOVERNANCE_AGENTS_JSON", raising=False)
        monkeypatch.setattr(governance_agents, "_load_stored_entries", lambda: [])
        with pytest.raises(governance_agents.GovernanceAgentError, match="No governance agent is configured"):
            adcp_tools.resolve_governance_agent()

    def test_governance_configured_is_false_when_unset(self, monkeypatch):
        monkeypatch.delenv("GOVERNANCE_AGENTS_JSON", raising=False)
        # governance_configured now also consults the registration store; stub it empty so this stays
        # a deterministic unit test rather than depending on a live DynamoDB read.
        monkeypatch.setattr(governance_agents, "_load_stored_entries", lambda: [])
        assert governance_agents.governance_configured() is False

    def test_governance_configured_is_true_when_set(self, monkeypatch):
        monkeypatch.setenv(
            "GOVERNANCE_AGENTS_JSON",
            '[{"id":"boltive","name":"Boltive","url":"https://example.test/mcp",'
            '"auth_type":"static_bearer","auth_token_env":"BOLTIVE_TOK"}]',
        )
        assert governance_agents.governance_configured() is True

    def test_resolves_to_a_seller_shaped_target_static_bearer(self, monkeypatch):
        monkeypatch.setenv(
            "GOVERNANCE_AGENTS_JSON",
            '[{"id":"boltive","name":"Boltive Campaign Governance",'
            '"url":"https://example.test/mcp","auth_type":"static_bearer",'
            '"auth_token_env":"BOLTIVE_TOK"}]',
        )
        monkeypatch.setenv("BOLTIVE_TOK", "tok-abc")
        agent = adcp_tools.resolve_governance_agent()
        # Same shape _call_seller_tool takes, so the governance agent reuses the existing transport.
        assert {"id", "name", "url", "transport", "headers"} <= set(agent)
        assert agent["transport"] == "mcp"
        assert agent["headers"]["Authorization"] == "Bearer tok-abc"
        assert agent["name"] == "Boltive Campaign Governance"

    def test_resolves_to_a_seller_shaped_target_cognito_bearer(self, monkeypatch):
        monkeypatch.setenv(
            "GOVERNANCE_AGENTS_JSON",
            '[{"id":"ref-gov","name":"Reference Governance Agent",'
            '"url":"https://example.test/mcp","auth_type":"cognito_bearer"}]',
        )
        monkeypatch.setattr(governance_agents, "get_test_user_access_token", lambda: "tok-xyz")
        agent = adcp_tools.resolve_governance_agent()
        assert agent["headers"]["Authorization"] == "Bearer tok-xyz"

    def test_static_bearer_with_empty_token_env_raises(self, monkeypatch):
        monkeypatch.setenv(
            "GOVERNANCE_AGENTS_JSON",
            '[{"id":"boltive","name":"Boltive","url":"https://example.test/mcp",'
            '"auth_type":"static_bearer","auth_token_env":"BOLTIVE_TOK"}]',
        )
        monkeypatch.delenv("BOLTIVE_TOK", raising=False)
        with pytest.raises(governance_agents.GovernanceAgentError, match="BOLTIVE_TOK"):
            adcp_tools.resolve_governance_agent()


class TestRegistration:
    def test_both_governance_tools_are_registered_under_their_adcp_names(self):
        # Defined-but-unregistered is the failure this catches: the factory returns an explicit list, and
        # the first version of this change added the functions without adding them to it. The UI's phase
        # bindings match on these exact names.
        sellers = [{"id": "ref", "name": "Ref", "url": "https://x/mcp", "transport": "mcp", "headers": {}}]
        tools = adcp_tools.build_adcp_tools(sellers, buyer_session_id=None)
        names = {getattr(t, "tool_name", None) or t.__name__ for t in tools}
        assert "adcp_sync_governance" in names
        assert "adcp_sync_plans" in names

    def test_the_new_booking_and_reporting_tools_are_registered(self):
        # Same failure class this project has hit twice already (a defined tool absent from the
        # factory's return list, and a registry binding naming a tool that was never registered) -- see
        # phases.tools.test.ts on the UI side. Pinned here on the Python side for the four new tools.
        sellers = [{"id": "ref", "name": "Ref", "url": "https://x/mcp", "transport": "mcp", "headers": {}}]
        tools = adcp_tools.build_adcp_tools(sellers, buyer_session_id=None)
        names = {getattr(t, "tool_name", None) or t.__name__ for t in tools}
        assert "adcp_check_governance" in names
        assert "adcp_create_media_buy" in names
        assert "adcp_report_plan_outcome" in names
        assert "adcp_get_media_buy_delivery" in names
        assert "adcp_get_signals" in names


def _build_tools(monkeypatch, governance_json, seller_url="https://seller.test/mcp"):
    """Shared setup: configure a governance agent and one seller, build the tool set."""
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


_BOLTIVE_JSON = (
    '[{"id":"boltive","name":"Boltive","url":"https://example.test/mcp",'
    '"auth_type":"static_bearer","auth_token_env":"BOLTIVE_TOK"}]'
)


def _unsigned_jws(payload: dict) -> str:
    """A well-formed compact-JWS-shaped string with no real signature.

    _governance_context_expired never verifies a signature (it is a staleness check, not an
    authenticity check -- see its docstring), so a fixture only needs the right SHAPE: three
    base64url segments, with a real JSON payload in the middle one.
    """
    import base64 as _b64
    import json as _json

    def _seg(obj) -> str:
        raw = _json.dumps(obj).encode()
        return _b64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    header = _seg({"alg": "ES256", "typ": "JWT"})
    body = _seg(payload)
    return f"{header}.{body}.fake-signature"


class TestGovernanceContextExpired:
    def test_a_future_exp_is_not_expired(self):
        import time

        token = _unsigned_jws({"exp": int(time.time()) + 900})
        assert adcp_tools._governance_context_expired(token) is False

    def test_a_past_exp_is_expired(self):
        import time

        token = _unsigned_jws({"exp": int(time.time()) - 900})
        assert adcp_tools._governance_context_expired(token) is True

    def test_wrong_segment_count_is_treated_as_expired(self):
        assert adcp_tools._governance_context_expired("only.two") is True
        assert adcp_tools._governance_context_expired("no-dots-at-all") is True

    def test_undecodable_base64_payload_is_treated_as_expired(self):
        assert adcp_tools._governance_context_expired("header.!!!not-base64!!!.sig") is True

    def test_non_json_payload_is_treated_as_expired(self):
        import base64 as _b64

        not_json = _b64.urlsafe_b64encode(b"not json at all").rstrip(b"=").decode()
        assert adcp_tools._governance_context_expired(f"header.{not_json}.sig") is True

    def test_payload_missing_exp_is_treated_as_expired(self):
        token = _unsigned_jws({"sub": "plan-1"})
        assert adcp_tools._governance_context_expired(token) is True


class TestCheckGovernanceCallerBinding:
    """The seller a check is about goes in `target_agent`. `caller` is this buyer.

    This class previously asserted the opposite: that `caller` must carry the TARGET SELLER's identity,
    because the governance agent signed `caller` into the token's `aud`. The observed behaviour was
    real -- our own agent did read the audience off `caller` -- but both sides were wrong together, so
    each looked like evidence for the other, and these tests locked the pair in place.

    AdCP 3.1 (docs/governance/campaign/responsibilities, Buyer-side intent check):

        caller        "Identifies the buyer-side orchestrator making the governance check"
        target_agent  "Exact downstream service URL; becomes the signed token audience and stays
                       outside the business payload"

    The SDK agrees on `caller`: its field description is "URL of the agent making the request". The same
    field is also what a plan's `delegations[].agent_url` is matched against, which only means anything
    for the agent acting on the plan.

    What the original bug was really about survives, and is still asserted here: the token's audience
    must be the seller's own canonical identity, never a synthetic buyer string. It now travels in the
    field the spec gives it.
    """

    def test_target_agent_is_the_sellers_canonical_agent_url_and_caller_is_this_buyer(
        self, monkeypatch
    ):
        tools = _build_tools(monkeypatch, _BOLTIVE_JSON)
        monkeypatch.setenv("BOLTIVE_TOK", "tok-abc")

        seen_args: dict[str, Any] = {}

        async def fake_call(seller_agent, tool_name, args, buyer_session_id=None):
            if tool_name == "check_governance":
                seen_args.update(args)
                return {"verdict": "approved", "check_id": "chk_1", "governance_context": "tok"}
            raise AssertionError(f"only check_governance should be called; got {tool_name}")

        monkeypatch.setattr(adcp_tools, "_call_seller_tool", fake_call)

        asyncio.run(
            tools["adcp_check_governance"](
                seller_id="ref",
                plan_id="plan-1",
                tool_name="create_media_buy",
                payload={"packages": [{"product_id": "prod-1", "budget": 500.0,
                                        "pricing_option_id": "po-1"}]},
            )
        )
        # The seller's own declared agent_url (its canonical AdCP identity) -- what a real seller's
        # verify_intent_token compares an incoming token's `aud` against.
        assert seen_args["target_agent"] == "https://ref-seller.example/adcp/mcp"
        # And it is NOT in `caller`, which is where it used to be.
        assert seen_args.get("caller") != "https://ref-seller.example/adcp/mcp"
        # `payload` must not carry it either: the spec says `target_agent` "stays outside the business
        # payload", so a seller key smuggled in there would defeat the point of the separate field.
        assert "seller" not in seen_args["payload"]

    def test_check_governance_targets_the_specific_seller_named_by_seller_id(self, monkeypatch):
        """Two sellers configured; the check must bind to whichever one seller_id names, not the
        first registry entry or some averaged/default value."""
        monkeypatch.setenv("GOVERNANCE_AGENTS_JSON", _BOLTIVE_JSON)
        monkeypatch.setenv("BOLTIVE_TOK", "tok-abc")
        sellers = [
            {
                "id": "ref",
                "name": "Ref Seller",
                "url": "https://ref.test/mcp",
                "agent_url": "https://ref-seller.example/adcp/mcp",
                "transport": "mcp",
                "headers": {},
            },
            {
                "id": "other",
                "name": "Other Seller",
                "url": "https://other.test/mcp",
                "agent_url": "https://other-seller.example/adcp/mcp",
                "transport": "mcp",
                "headers": {},
            },
        ]
        tools = {
            getattr(t, "tool_name", None) or t.__name__: t
            for t in adcp_tools.build_adcp_tools(sellers, buyer_session_id=None)
        }

        seen_args: dict[str, Any] = {}

        async def fake_call(seller_agent, tool_name, args, buyer_session_id=None):
            seen_args.update(args)
            return {"verdict": "approved", "check_id": "chk_1", "governance_context": "tok"}

        monkeypatch.setattr(adcp_tools, "_call_seller_tool", fake_call)

        asyncio.run(
            tools["adcp_check_governance"](
                seller_id="other",
                plan_id="plan-1",
                tool_name="create_media_buy",
                payload={"packages": [{"product_id": "prod-1", "budget": 500.0,
                                        "pricing_option_id": "po-1"}]},
            )
        )
        assert seen_args["target_agent"] == "https://other-seller.example/adcp/mcp"

    def test_an_unknown_seller_id_is_refused_rather_than_silently_defaulting(self, monkeypatch):
        tools = _build_tools(monkeypatch, _BOLTIVE_JSON)
        monkeypatch.setenv("BOLTIVE_TOK", "tok-abc")

        async def fake_call(seller_agent, tool_name, args, buyer_session_id=None):
            raise AssertionError("must not reach the seller with an unknown seller_id")

        monkeypatch.setattr(adcp_tools, "_call_seller_tool", fake_call)

        with pytest.raises(adcp_tools.AdcpToolError, match="Unknown sales agent id"):
            asyncio.run(
                tools["adcp_check_governance"](
                    seller_id="does-not-exist",
                    plan_id="plan-1",
                    tool_name="create_media_buy",
                    payload={"packages": [{"product_id": "prod-1", "budget": 500.0,
                                            "pricing_option_id": "po-1"}]},
                )
            )

    def test_falls_back_to_url_when_a_seller_entry_declares_no_agent_url(self, monkeypatch):
        """seller_agents.py falls back agent_url -> url for entries with no separately declared
        canonical identity (a third-party seller, whose invoke URL IS its canonical identity)."""
        monkeypatch.setenv("GOVERNANCE_AGENTS_JSON", _BOLTIVE_JSON)
        monkeypatch.setenv("BOLTIVE_TOK", "tok-abc")
        sellers = [
            {
                "id": "ref",
                "name": "Ref Seller",
                "url": "https://ref.test/mcp",
                # No agent_url key at all.
                "transport": "mcp",
                "headers": {},
            }
        ]
        tools = {
            getattr(t, "tool_name", None) or t.__name__: t
            for t in adcp_tools.build_adcp_tools(sellers, buyer_session_id=None)
        }

        seen_args: dict[str, Any] = {}

        async def fake_call(seller_agent, tool_name, args, buyer_session_id=None):
            seen_args.update(args)
            return {"verdict": "approved", "check_id": "chk_1", "governance_context": "tok"}

        monkeypatch.setattr(adcp_tools, "_call_seller_tool", fake_call)

        asyncio.run(
            tools["adcp_check_governance"](
                seller_id="ref",
                plan_id="plan-1",
                tool_name="create_media_buy",
                payload={"packages": [{"product_id": "prod-1", "budget": 500.0,
                                        "pricing_option_id": "po-1"}]},
            )
        )
        assert seen_args["target_agent"] == "https://ref.test/mcp"

    def test_caller_names_this_buyer_when_the_registry_declares_one(self, monkeypatch):
        """`caller` is this agent's own URL, read from AGENTS_JSON's internal buyer entry.

        Read from the registry rather than composed from an ARN because `AGENTS_JSON` is already in the
        deployed runtime's environment while `AGENT_RUNTIME_ARN` is not passed to the container at all.
        """
        monkeypatch.setenv("GOVERNANCE_AGENTS_JSON", _BOLTIVE_JSON)
        monkeypatch.setenv("BOLTIVE_TOK", "tok-abc")
        monkeypatch.setenv(
            "AGENTS_JSON",
            json.dumps(
                [
                    {
                        "id": "buyer-a2a",
                        "name": "AdCP Buyer Agent (A2A)",
                        "kind": "buyer",
                        "url": "https://buyer.example/a2a",
                        "auth_type": "cognito_bearer",
                        "origin": "internal",
                    }
                ]
            ),
        )
        sellers = [
            {
                "id": "ref",
                "name": "Ref Seller",
                "url": "https://ref.test/mcp",
                "agent_url": "https://ref-seller.example/adcp/mcp",
                "transport": "mcp",
                "headers": {},
            }
        ]
        tools = {
            getattr(t, "tool_name", None) or t.__name__: t
            for t in adcp_tools.build_adcp_tools(sellers, buyer_session_id=None)
        }

        seen_args: dict[str, Any] = {}

        async def fake_call(seller_agent, tool_name, args, buyer_session_id=None):
            seen_args.update(args)
            return {"verdict": "approved", "check_id": "chk_1", "governance_context": "tok"}

        monkeypatch.setattr(adcp_tools, "_call_seller_tool", fake_call)

        asyncio.run(
            tools["adcp_check_governance"](
                seller_id="ref",
                plan_id="plan-1",
                tool_name="create_media_buy",
                payload={"packages": [{"product_id": "prod-1", "budget": 500.0,
                                        "pricing_option_id": "po-1"}]},
            )
        )
        assert seen_args["caller"] == "https://buyer.example/a2a"
        assert seen_args["target_agent"] == "https://ref-seller.example/adcp/mcp"

    def test_a_conditions_handle_is_sent_back_in_its_own_field(self, monkeypatch):
        """A `consultation_context` must not be folded into `governance_context`: one authorises and the
        other explicitly does not, and merging them would let a non-authorising handle be presented as
        an authorising one."""
        tools = _build_tools(monkeypatch, _BOLTIVE_JSON)
        monkeypatch.setenv("BOLTIVE_TOK", "tok-abc")

        seen_args: dict[str, Any] = {}

        async def fake_call(seller_agent, tool_name, args, buyer_session_id=None):
            seen_args.update(args)
            return {"verdict": "approved", "check_id": "chk_2", "governance_context": "tok"}

        monkeypatch.setattr(adcp_tools, "_call_seller_tool", fake_call)

        asyncio.run(
            tools["adcp_check_governance"](
                seller_id="ref",
                plan_id="plan-1",
                tool_name="create_media_buy",
                payload={"packages": [{"product_id": "prod-1", "budget": 500.0,
                                        "pricing_option_id": "po-1"}]},
                consultation_context="handle-abc",
            )
        )
        assert seen_args["consultation_context"] == "handle-abc"
        assert "governance_context" not in seen_args


class TestApprovedCheckGate:
    """NFR-U2-G3/NFR-U2-G4: adcp_create_media_buy must refuse to call the seller unless a matching,
    unexpired adcp_check_governance approval was recorded earlier in the session."""

    def test_an_approved_check_populates_the_ledger_and_unblocks_booking(self, monkeypatch):
        import time

        tools = _build_tools(monkeypatch, _BOLTIVE_JSON)
        monkeypatch.setenv("BOLTIVE_TOK", "tok-abc")

        approved_context = _unsigned_jws({"exp": int(time.time()) + 900})
        seller_calls: list[str] = []

        async def fake_call(seller_agent, tool_name, args, buyer_session_id=None):
            seller_calls.append(tool_name)
            if tool_name == "check_governance":
                return {
                    "verdict": "approved",
                    "check_id": "chk_1",
                    "governance_context": approved_context,
                }
            return {"status": "completed", "media_buy_id": "mb_1"}

        monkeypatch.setattr(adcp_tools, "_call_seller_tool", fake_call)

        check_result = asyncio.run(
            tools["adcp_check_governance"](
                seller_id="ref",
                plan_id="plan-1",
                tool_name="create_media_buy",
                payload={"packages": [{"product_id": "prod-1", "budget": 500.0,
                                        "pricing_option_id": "po-1"}]},
            )
        )
        assert check_result["verdict"] == "approved"

        buy_result = asyncio.run(
            tools["adcp_create_media_buy"](
                seller_id="ref",
                plan_id="plan-1",
                governance_context=approved_context,
                product_id="prod-1",
                budget=500.0,
                pricing_option_id="po-1",
                brand_domain="acme.example",
                operator="acme-agency",
                flight_start="2026-01-01",
                flight_end="2026-01-31",
            )
        )
        assert buy_result == {"status": "completed", "media_buy_id": "mb_1"}
        assert seller_calls == ["check_governance", "create_media_buy"]

    def test_booking_without_any_prior_check_is_refused_before_calling_the_seller(self, monkeypatch):
        tools = _build_tools(monkeypatch, _BOLTIVE_JSON)
        monkeypatch.setenv("BOLTIVE_TOK", "tok-abc")

        async def fake_call(seller_agent, tool_name, args, buyer_session_id=None):
            raise AssertionError(f"seller must not be called; got {tool_name}")

        monkeypatch.setattr(adcp_tools, "_call_seller_tool", fake_call)

        result = asyncio.run(
            tools["adcp_create_media_buy"](
                seller_id="ref",
                plan_id="plan-1",
                governance_context="whatever",
                product_id="prod-1",
                budget=500.0,
                pricing_option_id="po-1",
                brand_domain="acme.example",
                operator="acme-agency",
                flight_start="2026-01-01",
                flight_end="2026-01-31",
            )
        )
        assert result["error"] == "no_prior_check"

    def test_a_check_approved_for_a_different_product_does_not_authorize_this_booking(self, monkeypatch):
        import time

        tools = _build_tools(monkeypatch, _BOLTIVE_JSON)
        monkeypatch.setenv("BOLTIVE_TOK", "tok-abc")
        approved_context = _unsigned_jws({"exp": int(time.time()) + 900})

        async def fake_call(seller_agent, tool_name, args, buyer_session_id=None):
            if tool_name == "check_governance":
                return {
                    "verdict": "approved",
                    "check_id": "chk_1",
                    "governance_context": approved_context,
                }
            raise AssertionError("seller must not be called for the mismatched booking")

        monkeypatch.setattr(adcp_tools, "_call_seller_tool", fake_call)

        # Approve product "prod-1", then try to book "prod-2" under the same plan.
        asyncio.run(
            tools["adcp_check_governance"](
                seller_id="ref",
                plan_id="plan-1",
                tool_name="create_media_buy",
                payload={"packages": [{"product_id": "prod-1", "budget": 500.0,
                                        "pricing_option_id": "po-1"}]},
            )
        )
        result = asyncio.run(
            tools["adcp_create_media_buy"](
                seller_id="ref",
                plan_id="plan-1",
                governance_context=approved_context,
                product_id="prod-2",
                budget=500.0,
                pricing_option_id="po-1",
                brand_domain="acme.example",
                operator="acme-agency",
                flight_start="2026-01-01",
                flight_end="2026-01-31",
            )
        )
        assert result["error"] == "no_prior_check"

    def test_an_expired_approval_is_refused_and_reported_distinctly(self, monkeypatch):
        import time

        tools = _build_tools(monkeypatch, _BOLTIVE_JSON)
        monkeypatch.setenv("BOLTIVE_TOK", "tok-abc")
        expired_context = _unsigned_jws({"exp": int(time.time()) - 900})

        async def fake_call(seller_agent, tool_name, args, buyer_session_id=None):
            if tool_name == "check_governance":
                return {
                    "verdict": "approved",
                    "check_id": "chk_1",
                    "governance_context": expired_context,
                }
            raise AssertionError("seller must not be called for an expired approval")

        monkeypatch.setattr(adcp_tools, "_call_seller_tool", fake_call)

        asyncio.run(
            tools["adcp_check_governance"](
                seller_id="ref",
                plan_id="plan-1",
                tool_name="create_media_buy",
                payload={"packages": [{"product_id": "prod-1", "budget": 500.0,
                                        "pricing_option_id": "po-1"}]},
            )
        )
        result = asyncio.run(
            tools["adcp_create_media_buy"](
                seller_id="ref",
                plan_id="plan-1",
                governance_context=expired_context,
                product_id="prod-1",
                budget=500.0,
                pricing_option_id="po-1",
                brand_domain="acme.example",
                operator="acme-agency",
                flight_start="2026-01-01",
                flight_end="2026-01-31",
            )
        )
        assert result["error"] == "governance_context_expired"

    def test_a_conditions_verdict_does_not_open_the_gate(self, monkeypatch):
        """BR-U2-15: 'conditions' must never be treated as approval."""
        tools = _build_tools(monkeypatch, _BOLTIVE_JSON)
        monkeypatch.setenv("BOLTIVE_TOK", "tok-abc")

        async def fake_call(seller_agent, tool_name, args, buyer_session_id=None):
            if tool_name == "check_governance":
                return {
                    "verdict": "conditions",
                    "check_id": "chk_1",
                    "governance_context": "irrelevant-should-not-be-stored",
                    "conditions": [{"field": "budget", "reason": "over allocation"}],
                }
            raise AssertionError("seller must not be called after only a conditions verdict")

        monkeypatch.setattr(adcp_tools, "_call_seller_tool", fake_call)

        asyncio.run(
            tools["adcp_check_governance"](
                seller_id="ref",
                plan_id="plan-1",
                tool_name="create_media_buy",
                payload={"packages": [{"product_id": "prod-1", "budget": 500.0,
                                        "pricing_option_id": "po-1"}]},
            )
        )
        result = asyncio.run(
            tools["adcp_create_media_buy"](
                seller_id="ref",
                plan_id="plan-1",
                governance_context="irrelevant-should-not-be-stored",
                product_id="prod-1",
                budget=500.0,
                pricing_option_id="po-1",
                brand_domain="acme.example",
                operator="acme-agency",
                flight_start="2026-01-01",
                flight_end="2026-01-31",
            )
        )
        assert result["error"] == "no_prior_check"


class TestSyncPlansHumanReview:
    """`adcp_sync_plans`'s two routes into human review.

    These exist because the governance agent's suspension behaviour had **no user-facing trigger**: the
    tool exposed neither `human_review_required` nor `custom_policies`, so no prompt could produce a
    suspended plan and the whole M1/M2 path was unreachable outside the agent's own unit suite.

    Both fields are real and caller-settable on AdCP 3.1's `sync_plans_request.Plan` — verified against
    the installed SDK, not assumed. `human_review_required` is `bool | None` defaulting to false, and
    `custom_policies` is a list of `PolicyEntry`.
    """

    GOVERNANCE_JSON = (
        '[{"id":"gov","name":"Gov","url":"https://gov.test/mcp",'
        '"auth_type":"static_bearer","auth_token_env":"GOV_TOK"}]'
    )

    def _tool(self, monkeypatch):
        monkeypatch.setenv("GOV_TOK", "x" * 40)
        return _build_tools(monkeypatch, self.GOVERNANCE_JSON)["adcp_sync_plans"]

    def _captured_plan(self, monkeypatch, **kwargs):
        """Call the tool with the outbound MCP hop stubbed, and return the plan it would have sent."""
        sent: dict = {}

        async def fake_call(agent, task, args, session_id):
            sent.update(args)
            return {"plans": [{"plan_id": args["plans"][0]["plan_id"], "status": "active"}]}

        monkeypatch.setattr(adcp_tools, "_call_seller_tool", fake_call)
        tool = self._tool(monkeypatch)
        base = {
            "plan_id": "p1",
            "brand_domain": "acme.example",
            "objectives": "Reach adults 18-49.",
            "budget_total": 80000.0,
            "flight_start": "2026-12-05",
            "flight_end": "2026-12-20",
            "channels": ["streaming_audio"],
        }
        result = asyncio.run(tool(**{**base, **kwargs}))
        return result, (sent.get("plans") or [{}])[0]

    def test_neither_field_is_sent_when_not_asked_for(self, monkeypatch):
        """Absent, not false/empty. `human_review_required` already defaults to false in the schema, so
        sending it says nothing; an empty `custom_policies` would assert "this campaign has no bespoke
        policies", which is a claim the caller never made."""
        _result, plan = self._captured_plan(monkeypatch)
        assert "human_review_required" not in plan
        assert "custom_policies" not in plan

    def test_the_flag_is_forwarded_when_set(self, monkeypatch):
        _result, plan = self._captured_plan(monkeypatch, human_review_required=True)
        assert plan["human_review_required"] is True

    def test_a_valid_custom_policy_is_forwarded(self, monkeypatch):
        _result, plan = self._captured_plan(
            monkeypatch,
            custom_policies=[
                {
                    "policy_id": "eu-ai-act-annex-iii",
                    "enforcement": "must",
                    "policy": "Credit-product advertising requires human oversight.",
                    "requires_human_review": True,
                }
            ],
        )
        policy = plan["custom_policies"][0]
        assert policy["policy_id"] == "eu-ai-act-annex-iii"
        assert policy["requires_human_review"] is True
        # The SDK fills `source: inline` for an inline policy. Asserted so a future change that stopped
        # round-tripping through the model — and therefore stopped validating — would fail here.
        assert policy["source"] == "inline"

    def test_enforcement_may_is_accepted(self, monkeypatch):
        """`may` is in `PolicyEnforcementLevel` even though the `custom_policies` field description says
        "(must|should)". The enum is the contract; that prose is out of date. Pinned so nobody "fixes"
        the tool to match the description and starts rejecting a legal value."""
        _result, plan = self._captured_plan(
            monkeypatch,
            custom_policies=[{"policy_id": "p", "enforcement": "may", "policy": "text"}],
        )
        assert plan["custom_policies"][0]["enforcement"] == "may"

    def test_a_missing_required_field_is_a_correctable_error_not_a_remote_failure(self, monkeypatch):
        """Validated locally against the SDK model. Posting it would fail at the governance agent with a
        nested schema pointer the model has to decode; this returns a sentence naming the fix."""
        result, plan = self._captured_plan(
            monkeypatch,
            custom_policies=[{"policy_id": "p", "enforcement": "must"}],  # no `policy`
        )
        assert result["error"] == "invalid_custom_policy"
        assert "policy" in result["message"]
        assert plan == {}, "nothing should have been sent"

    def test_a_bad_enforcement_value_is_refused(self, monkeypatch):
        result, _plan = self._captured_plan(
            monkeypatch,
            custom_policies=[{"policy_id": "p", "enforcement": "mandatory", "policy": "t"}],
        )
        assert result["error"] == "invalid_custom_policy"

    def test_an_invented_key_is_refused(self, monkeypatch):
        """`PolicyEntry` sets `extra="forbid"`, and a stray plausible-sounding key is the most likely
        thing an LLM produces here. Better refused locally with the allowed fields named."""
        result, _plan = self._captured_plan(
            monkeypatch,
            custom_policies=[
                {"policy_id": "p", "enforcement": "must", "policy": "t", "reason": "because"}
            ],
        )
        assert result["error"] == "invalid_custom_policy"
        assert "No other keys" in result["message"] or "not accepted" in result["message"]

    def test_the_index_of_the_bad_entry_is_named(self, monkeypatch):
        """With several policies, "one of them is wrong" is not actionable."""
        result, _plan = self._captured_plan(
            monkeypatch,
            custom_policies=[
                {"policy_id": "ok", "enforcement": "must", "policy": "t"},
                {"policy_id": "bad", "enforcement": "must"},
            ],
        )
        assert "custom_policies[1]" in result["message"]

class TestSyncPlansMarketsAndChannels(TestSyncPlansHumanReview):
    """Feature 8 / FR-3.1: authorised markets (countries/regions) and channels.required on a plan.

    Inherits the `_captured_plan` helper (same stubbed outbound hop). These are what let the governance
    agent's geo/channel checks run instead of reporting the category unevaluated -- and, per the
    campaign-governance spec, `countries`/`regions` are also the plan's enforcement boundary.
    """

    def test_markets_omitted_when_not_set(self, monkeypatch):
        """An unbounded plan stays unbounded -- countries/regions absent, not sent empty."""
        _result, plan = self._captured_plan(monkeypatch)
        assert "countries" not in plan
        assert "regions" not in plan

    def test_countries_and_regions_are_forwarded(self, monkeypatch):
        _result, plan = self._captured_plan(
            monkeypatch, countries=["US"], regions=["US-CA", "US-NY"]
        )
        assert plan["countries"] == ["US"]
        assert plan["regions"] == ["US-CA", "US-NY"]

    def test_required_channels_are_forwarded_alongside_allowed(self, monkeypatch):
        _result, plan = self._captured_plan(
            monkeypatch, channels=["streaming_audio", "podcast"], required_channels=["podcast"]
        )
        assert plan["channels"]["allowed"] == ["streaming_audio", "podcast"]
        assert plan["channels"]["required"] == ["podcast"]

    def test_an_invalid_country_code_is_a_correctable_error(self, monkeypatch):
        """'USA' is not ISO 3166-1 alpha-2. Caught locally against the SDK `Plan` model as a sentence,
        not posted and left to fail at the governance agent."""
        result, plan = self._captured_plan(monkeypatch, countries=["USA"])
        assert result["error"] == "invalid_plan"
        assert plan == {}, "nothing should have been sent"

    def test_an_invalid_channel_name_is_refused(self, monkeypatch):
        """'audio' is not in AdCP's MediaChannel vocabulary -- streaming audio is 'streaming_audio'."""
        result, _plan = self._captured_plan(monkeypatch, channels=["audio"])
        assert result["error"] == "invalid_plan"
