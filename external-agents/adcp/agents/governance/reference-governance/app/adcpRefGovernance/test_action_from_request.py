"""`_action_from_request` must read the real AdCP SDK shapes, not just the date-shaped fallbacks.

The bug these pin: the normalizer read `start_date`/top-level `countries`, but a spec-correct
create_media_buy body uses `start_time`/`end_time` and nests geo under `packages[].targeting_overlay`,
and a PlannedDelivery nests geo under `geo`. Reading the wrong names made flight report "no flight
dates" and geo/channel never fire, on both the intent and execution checks.
"""

from __future__ import annotations

from types import SimpleNamespace

import main
import policy


def _evaluated(action: dict) -> tuple[str, ...]:
    plan = {
        "budget": {"total": 30000.0, "currency": "USD", "reallocation_threshold": 0},
        "flight": {"start": "2026-10-01T00:00:00Z", "end": "2026-10-31T23:59:59Z"},
        "countries": ["US"],
        "channels": {"allowed": ["podcast"]},
    }
    return policy.evaluate(plan=plan, action=action).categories_evaluated


def test_intent_create_media_buy_body_evaluates_budget_flight_and_geo():
    request = SimpleNamespace(
        payload={
            "packages": [
                {
                    "product_id": "p1",
                    "budget": 5600.0,
                    "pricing_option_id": "po1",
                    "targeting_overlay": {"geo_countries": ["US"]},
                }
            ],
            "start_time": "2026-10-01T00:00:00Z",
            "end_time": "2026-10-31T23:59:59Z",
        },
        planned_delivery=None,
    )
    action = main._action_from_request(request)
    assert action["amount"] == 5600.0
    assert action["start_date"] == "2026-10-01T00:00:00Z"
    assert action["markets"] == ["US"]
    # No channel field exists in a create_media_buy body, so channel stays unevaluated on intent.
    assert action["channels"] is None
    evaluated = _evaluated(action)
    assert "budget_authority" in evaluated
    assert "flight_window" in evaluated
    assert "geo_compliance" in evaluated


def test_execution_planned_delivery_evaluates_all_four_including_channel():
    request = SimpleNamespace(
        payload=None,
        planned_delivery={
            "total_budget": 5600.0,
            "currency": "USD",
            "start_time": "2026-10-01T00:00:00Z",
            "end_time": "2026-10-31T23:59:59Z",
            "geo": {"countries": ["US"]},
            "channels": ["podcast"],
        },
    )
    action = main._action_from_request(request)
    assert action["amount"] == 5600.0
    assert action["start_date"] == "2026-10-01T00:00:00Z"
    assert action["markets"] == ["US"]
    assert action["channels"] == ["podcast"]
    assert _evaluated(action) == (
        "budget_authority",
        "flight_window",
        "geo_compliance",
        "channel_compliance",
    )


def test_date_shaped_fallbacks_still_read():
    request = SimpleNamespace(
        payload={
            "budget": {"total": 100.0, "currency": "USD"},
            "start_date": "2026-10-05",
            "end_date": "2026-10-06",
            "countries": ["US"],
        },
        planned_delivery=None,
    )
    action = main._action_from_request(request)
    assert action["amount"] == 100.0
    assert action["start_date"] == "2026-10-05"
    assert action["markets"] == ["US"]


def test_packages_budget_is_summed_when_no_total_is_given():
    request = SimpleNamespace(
        payload={"packages": [{"budget": 1000.0}, {"budget": 250.0}]},
        planned_delivery=None,
    )
    assert main._action_from_request(request)["amount"] == 1250.0


def test_a_bare_budget_only_payload_leaves_flight_geo_channel_absent():
    # The pre-fix failure mode: a payload with only a budget. Budget evaluates; the rest do not.
    request = SimpleNamespace(payload={"budget": 5600.0}, planned_delivery=None)
    action = main._action_from_request(request)
    assert action["amount"] == 5600.0
    assert action["start_date"] is None
    assert action["markets"] is None
    assert action["channels"] is None
