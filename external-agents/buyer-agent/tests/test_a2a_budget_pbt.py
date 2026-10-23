"""Property-based tests for the A2A buyer budget allocation (PBT-02/03/07).

These exercise crewai-free, strands-free helpers, so they run without AWS or the
agent frameworks — only `hypothesis` + `pytest` (see requirements-a2a-dev.txt).

Covers:
- PBT-03 invariant: clamped allocations are non-negative and never exceed the total.
- PBT-02 round-trip: BudgetAllocationOutput survives model_dump -> model_validate.
- PBT-07: domain-appropriate generators (bounded money, real channels).
"""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ad_buyer.interfaces.agentcore.budget_models import (
    BudgetAllocationOutput,
    ChannelAllocationOut,
)
from ad_buyer.interfaces.agentcore.planning_helpers import (
    CHANNELS,
    clamp_allocations,
    parse_brief_from_prompt,
)

# PBT-07: bounded, domain-appropriate generators (money in a realistic range).
money = st.floats(min_value=0, max_value=1e9, allow_nan=False, allow_infinity=False)
_raw = st.fixed_dictionaries(
    {ch: st.fixed_dictionaries({"budget": money, "rationale": st.text(max_size=24)}) for ch in CHANNELS}
)


@given(raw=_raw, total=money)
@settings(max_examples=300)
def test_clamp_never_exceeds_total_and_non_negative(raw, total):
    out = clamp_allocations(raw, total)
    budgets = [v["budget"] for v in out.values()]
    assert all(b >= 0 for b in budgets), "PBT-03: negative allocation"
    assert sum(budgets) <= total + 1e-6, "PBT-03: aggregate exceeds total budget"
    # only funded channels are returned, and they are real channels
    assert all(ch in CHANNELS for ch in out)
    assert all(v["budget"] > 0 for v in out.values())


@given(raw=_raw, total=st.floats(min_value=1.0, max_value=1e9, allow_nan=False, allow_infinity=False))
@settings(max_examples=200)
def test_percentages_within_bounds(raw, total):
    out = clamp_allocations(raw, total)
    assert all(0 <= v["percentage"] <= 100.0 + 1e-6 for v in out.values())


@given(
    b=money, m=money, c=money, p=money,
)
@settings(max_examples=200)
def test_budget_output_roundtrip(b, m, c, p):
    model = BudgetAllocationOutput(
        branding=ChannelAllocationOut(budget=b, percentage=0, rationale="x"),
        mobile_app=ChannelAllocationOut(budget=m, percentage=0, rationale="y"),
        ctv=ChannelAllocationOut(budget=c, percentage=0, rationale="z"),
        performance=ChannelAllocationOut(budget=p, percentage=0, rationale="w"),
    )
    # PBT-02: structured-output round-trip is identity.
    assert BudgetAllocationOutput.model_validate(model.model_dump()) == model


# ---------------------------------------------------------------------------
# Budget extraction from the natural-language brief.
#
# Regression: the parser understood only the abbreviated "$500K" / "$1.5M"
# forms. An orchestrating agent typically expands the user's "$500K" to
# "$500,000" before forwarding the brief, which matched neither pattern, so the
# stated budget was dropped and the whole plan was built on the default.
# ---------------------------------------------------------------------------

BRIEF_SUFFIX = " Q4 automotive campaign targeting adults 25-54."


@pytest.mark.parametrize(
    "amount_text,expected",
    [
        ("$500K", 500_000.0),
        ("$500k", 500_000.0),
        ("$1.5M", 1_500_000.0),
        ("$2M", 2_000_000.0),
        ("$500,000", 500_000.0),      # the expanded form that used to be missed
        ("$500000", 500_000.0),
        ("$1,250,000", 1_250_000.0),
        ("$750", 750.0),
        ("$ 500,000", 500_000.0),
    ],
)
def test_budget_parsed_from_written_amount(amount_text, expected):
    brief = parse_brief_from_prompt(f"Plan a {amount_text}{BRIEF_SUFFIX}")
    assert brief["budget"] == expected


def test_abbreviated_form_keeps_its_multiplier():
    """The plain-number branch must not strip the K off "$500K" (ordering matters)."""
    assert parse_brief_from_prompt("Plan a $500K campaign.")["budget"] == 500_000.0
    assert parse_brief_from_prompt("Plan a $1.5M campaign.")["budget"] == 1_500_000.0


def test_budget_defaults_only_when_no_amount_present():
    brief = parse_brief_from_prompt("Plan a Q4 automotive campaign for adults 25-54.")
    assert brief["budget"] == 100_000.0


# PBT-02 round-trip: a written amount survives formatting -> parsing.
@given(amount=st.integers(min_value=1, max_value=999_999_999))
@settings(max_examples=200)
def test_plain_amount_roundtrip(amount):
    """Any whole-dollar figure rendered with thousands separators parses back exactly."""
    brief = parse_brief_from_prompt(f"Plan a ${amount:,} campaign for Q4.")
    assert brief["budget"] == float(amount)


@given(thousands=st.integers(min_value=1, max_value=999_999))
@settings(max_examples=200)
def test_k_suffix_roundtrip(thousands):
    """"$<n>K" always means n * 1000."""
    brief = parse_brief_from_prompt(f"Plan a ${thousands}K campaign for Q4.")
    assert brief["budget"] == float(thousands) * 1000
