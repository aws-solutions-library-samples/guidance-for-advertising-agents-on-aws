"""`adcp_get_products`' buying_mode must be the SDK's enum, and must honour the wholesale MUST NOT.

## The bug this pins

Reported live. The first `get_products` call returned ten products; the retry came back:

    VALIDATION_ERROR[/buying_mode]: get_products request failed schema validation
    at /buying_mode: value not in allowed enum (3 options)

The parameter was declared `buying_mode: str = "brief"` and its docstring told the model that
`"browse"` meant raw catalogue access. AdCP defines no such value — the enum is
brief/wholesale/refine. So the tool's own description instructed the model to send something the
protocol rejects, and the model did exactly as it was told.

Two fixes, both pinned here. The parameter is a `Literal` of the three real values, so the tool
schema the model sees cannot express a fourth. And the docstring no longer names a value AdCP lacks.

The Literal restates the SDK's `BuyingMode`, which `use-the-adcp-sdk.md` normally forbids as a second
source of truth. The exception is that `adcp` cannot be installed in the buyer runtime at all:
`adcp==6.6.0` requires a2a-sdk>=1.0.1,<1.0.2 and `strands-agents[a2a]==1.48.0` requires
a2a-sdk>=0.3.0,<0.4.0, so the image build fails on the resolution. This file is where the two are
reconciled — `adcp` IS installed in the dev venv, so the first test below imports the real enum and
asserts the Literal equals it. A rename in AdCP fails here rather than drifting into production.

The third assertion is about a spec rule the SDK does not enforce: for `wholesale`, AdCP says
"brief must not be provided". `GetProductsRequest(buying_mode="wholesale", brief=...)` constructs
happily, so nothing local catches it — only a seller validating against the spec text would, and
that is the worst place to find out.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

import adcp_tools
import seller_agents
from adcp.types.generated_poc.media_buy.get_products_request import BuyingMode, GetProductsRequest


@pytest.fixture
def get_products(monkeypatch):
    """The real tool, with the seller fan-out replaced by a recorder of what it was asked to send."""
    sent: list[dict[str, Any]] = []

    async def fake_call_all(sellers, task, args, session_id):  # noqa: ANN001, ARG001
        sent.append(args)
        return {"results": []}

    monkeypatch.setattr(adcp_tools, "_call_all_sellers", fake_call_all)
    tools = adcp_tools.build_adcp_tools(seller_agents.list_seller_agents(), "test")
    tool = next(t for t in tools if getattr(t, "tool_name", "") == "adcp_get_products")
    return tool, sent


def _schema(tool) -> dict[str, Any]:
    spec = getattr(tool, "tool_spec", None) or tool.TOOL_SPEC
    return spec["inputSchema"]["json"]


class TestTheSchemaTheModelSees:
    def test_the_literal_matches_the_sdk_enum_exactly(self):
        """The one test that makes restating the enum safe. If AdCP renames a mode, this fails."""
        assert list(adcp_tools.BUYING_MODES) == [member.value for member in BuyingMode]

    def test_buying_mode_is_constrained_to_those_values_in_the_tool_schema(self, get_products):
        tool, _ = get_products
        prop = _schema(tool)["properties"]["buying_mode"]

        # Whether the schema inlines the enum or refs a $def, the allowed set must be exactly these.
        if "$ref" in prop:
            name = prop["$ref"].rsplit("/", 1)[-1]
            allowed = _schema(tool)["$defs"][name]["enum"]
        else:
            allowed = prop["enum"]
        assert allowed == list(adcp_tools.BUYING_MODES)

    def test_the_docstring_does_not_name_a_value_adcp_lacks(self, get_products):
        tool, _ = get_products
        description = _schema(tool)["properties"]["buying_mode"]["description"]

        # "browse" was the exact wrong value the old docstring taught the model to send.
        allowed = {member.value for member in BuyingMode}
        for word in ("browse", "catalog access", "catalogue access"):
            assert word not in description.lower(), f"docstring still mentions {word!r}"
        # And every value it does name must be real.
        for quoted in ("brief", "wholesale", "refine"):
            assert quoted in allowed


class TestWhatGetsSent:
    def test_a_valid_mode_travels_as_its_string_value(self, get_products):
        tool, sent = get_products

        asyncio.run(tool(brief="ctv news", buying_mode="refine"))

        assert sent[0]["buying_mode"] == "refine"

    def test_a_plain_string_is_accepted_too(self, get_products):
        # The model may send the raw string rather than a coerced member depending on the transport.
        tool, sent = get_products

        asyncio.run(tool(brief="ctv news", buying_mode="brief"))

        assert sent[0]["buying_mode"] == "brief"

    def test_an_invalid_mode_is_refused_locally_with_the_allowed_set(self, get_products):
        tool, sent = get_products

        result = asyncio.run(tool(brief="ctv news", buying_mode="browse"))

        assert sent == [], "an invalid mode must not reach a seller"
        assert result["error"] == "invalid_buying_mode"
        # The message carries the real values so the model can correct itself in one turn.
        for value in (member.value for member in BuyingMode):
            assert value in result["message"]

    def test_wholesale_omits_the_brief_because_the_spec_forbids_it(self, get_products):
        tool, sent = get_products

        asyncio.run(tool(brief="ctv news", buying_mode="wholesale"))

        assert sent[0]["buying_mode"] == "wholesale"
        assert "brief" not in sent[0]

    def test_the_other_modes_still_send_the_brief(self, get_products):
        tool, sent = get_products

        asyncio.run(tool(brief="ctv news", buying_mode="brief"))
        asyncio.run(tool(brief="ctv news", buying_mode="refine"))

        assert sent[0]["brief"] == "ctv news"
        assert sent[1]["brief"] == "ctv news"


def test_the_sdk_does_not_enforce_the_wholesale_rule_itself():
    """Why the previous test exists rather than trusting the model to validate.

    If this ever starts raising, the SDK has taken the rule on and the buyer-side drop becomes
    belt-and-braces rather than the only thing enforcing it.
    """
    GetProductsRequest(buying_mode="wholesale", brief="this should arguably be rejected")
