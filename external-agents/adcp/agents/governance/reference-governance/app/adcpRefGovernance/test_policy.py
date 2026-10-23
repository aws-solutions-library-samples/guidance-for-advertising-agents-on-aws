"""Tests for governance evaluation.

The ones that matter most are the negative and ordering assertions. A governance agent's failure mode
is not "wrong number on screen", it is **approving something it should have stopped**, so the tests are
weighted towards proving that cannot happen quietly:

  * an unstated budget never counts as within authority;
  * an unparseable date never counts as inside the flight window;
  * a category that was not checked is never reported as evaluated;
  * an uncorrectable breach is never softened to `conditions`.
"""

from __future__ import annotations

from datetime import date

import policy


PLAN = {
    "plan_id": "plan-1",
    "budget": {"total": 100000.0, "currency": "USD", "reallocation_threshold": 0},
    "flight": {"start_date": "2026-12-05", "end_date": "2026-12-10"},
    "countries": ["US"],
    "channels": ["audio", "podcast"],
}

ACTION = {
    "amount": 71400.0,
    "start_date": "2026-12-05",
    "end_date": "2026-12-10",
    "markets": ["US"],
    "channels": ["audio"],
}


def test_a_compliant_action_is_approved():
    result = policy.evaluate(plan=PLAN, action=ACTION)
    assert result.verdict == "approved"
    assert result.conditions == ()


def test_approved_carries_informational_findings_with_confidence():
    # FQ2=A. Without these the journey view's radial has nothing to draw in a healthy session.
    result = policy.evaluate(plan=PLAN, action=ACTION)
    assert result.findings, "an approved verdict must still report what was checked"
    assert all(f.confidence == policy.CERTAIN for f in result.findings)
    assert all(f.severity == "info" for f in result.findings)


def test_every_reported_category_was_actually_evaluated():
    result = policy.evaluate(plan=PLAN, action=ACTION)
    assert set(result.categories_evaluated) <= set(policy.DETERMINISTIC_CATEGORIES)
    # Claiming to have evaluated brand safety without doing so would be the most consequential lie
    # this agent could tell.
    for semantic in policy.SEMANTIC_CATEGORIES:
        assert semantic not in result.categories_evaluated


def test_semantic_categories_produce_no_findings_at_all():
    result = policy.evaluate(plan=PLAN, action=ACTION)
    emitted = {f.category_id for f in result.findings}
    assert emitted.isdisjoint(set(policy.SEMANTIC_CATEGORIES))


def test_over_authority_becomes_conditions_with_a_machine_actionable_value():
    over = {**ACTION, "amount": 150000.0}
    result = policy.evaluate(plan=PLAN, action=over)
    assert result.verdict == "conditions"
    assert len(result.conditions) == 1
    # A caller able to correct itself must be given the value rather than prose.
    assert result.conditions[0].required_value == 100000.0


def test_no_remaining_authority_is_denied_not_conditions():
    # Nothing left to spend, so there is no smaller amount that would pass. Softening this to
    # `conditions` would invite an endless re-call loop against a plan that is exhausted.
    result = policy.evaluate(plan=PLAN, action=ACTION, already_committed=100000.0)
    assert result.verdict == "denied"
    assert result.conditions == ()


def test_already_committed_reduces_remaining_authority():
    result = policy.evaluate(plan=PLAN, action={**ACTION, "amount": 60000.0}, already_committed=50000.0)
    assert result.verdict == "conditions"
    assert result.conditions[0].required_value == 50000.0


def test_an_unstated_amount_is_not_treated_as_within_authority():
    # The fail-open this agent exists to prevent.
    result = policy.evaluate(plan=PLAN, action={**ACTION, "amount": None})
    assert policy.CATEGORY_BUDGET not in result.categories_evaluated
    budget = [f for f in result.findings if f.category_id == policy.CATEGORY_BUDGET]
    assert budget and budget[0].severity == "warning"


def test_a_boolean_is_not_an_amount():
    # `True` coerces to 1.0 in Python, which would sail under any budget.
    result = policy.evaluate(plan=PLAN, action={**ACTION, "amount": True})
    assert policy.CATEGORY_BUDGET not in result.categories_evaluated


def test_flight_outside_the_window_is_denied():
    result = policy.evaluate(
        plan=PLAN, action={**ACTION, "start_date": "2026-11-01", "end_date": "2026-12-31"}
    )
    assert result.verdict == "denied"
    flight = [f for f in result.findings if f.category_id == policy.CATEGORY_FLIGHT]
    assert flight and flight[0].severity == "critical"
    # Both ends reported, not just the first one found.
    assert "before" in flight[0].explanation and "after" in flight[0].explanation


def test_an_unparseable_date_is_not_treated_as_in_window():
    result = policy.evaluate(plan=PLAN, action={**ACTION, "start_date": "next Tuesday"})
    assert policy.CATEGORY_FLIGHT not in result.categories_evaluated


def test_unauthorised_market_is_denied_and_named():
    result = policy.evaluate(plan=PLAN, action={**ACTION, "markets": ["US", "CA"]})
    assert result.verdict == "denied"
    geo = [f for f in result.findings if f.category_id == policy.CATEGORY_GEO]
    assert geo and "CA" in geo[0].explanation


def test_market_comparison_ignores_case_and_whitespace():
    result = policy.evaluate(plan=PLAN, action={**ACTION, "markets": [" us "]})
    assert result.verdict == "approved"


def test_disallowed_channel_is_denied():
    result = policy.evaluate(plan=PLAN, action={**ACTION, "channels": ["video"]})
    assert result.verdict == "denied"


def test_absent_plan_constraint_means_nothing_to_check_not_a_pass():
    bare = {"plan_id": "p", "budget": {}, "flight": {}}
    result = policy.evaluate(plan=bare, action=ACTION)
    assert result.categories_evaluated == ()
    # Approved, but the explanation says why rather than implying a clean bill of health.
    assert result.verdict == "approved"
    assert "nothing was evaluated" in result.explanation


def test_a_denial_always_names_at_least_one_category():
    for action in (
        {**ACTION, "markets": ["CA"]},
        {**ACTION, "channels": ["video"]},
        {**ACTION, "start_date": "2020-01-01", "end_date": "2020-01-02"},
    ):
        result = policy.evaluate(plan=PLAN, action=action)
        assert result.verdict == "denied"
        assert any(f.severity == "critical" for f in result.findings)
        assert all(f.category_id for f in result.findings)


def test_an_uncorrectable_breach_is_never_softened_to_conditions():
    # A geo breach has no corrective value this agent can supply, so it must stay denied even when a
    # budget condition exists alongside it.
    result = policy.evaluate(
        plan=PLAN, action={**ACTION, "amount": 150000.0, "markets": ["CA"]}
    )
    assert result.verdict == "denied"


def test_findings_serialise_without_empty_details():
    result = policy.evaluate(plan=PLAN, action=ACTION)
    for finding in result.findings:
        wire = finding.to_wire()
        assert set(wire) >= {"category_id", "severity", "explanation", "confidence"}
        if "details" in wire:
            assert wire["details"], "an empty details object should be omitted, not sent"


def test_condition_omits_required_value_when_advisory():
    advisory = policy.Condition(field_name="x", reason="interpret this")
    assert "required_value" not in advisory.to_wire()


def test_category_order_is_stable_across_identical_checks():
    # `categories_evaluated` lands in an audit log. A set's iteration order would make two identical
    # checks differ on the record.
    first = policy.evaluate(plan=PLAN, action=ACTION).categories_evaluated
    second = policy.evaluate(plan=PLAN, action=ACTION).categories_evaluated
    assert first == second
    assert list(first) == [c for c in policy.DETERMINISTIC_CATEGORIES if c in first]


def test_dates_may_arrive_as_date_objects():
    result = policy.evaluate(
        plan=PLAN, action={**ACTION, "start_date": date(2026, 12, 5), "end_date": date(2026, 12, 10)}
    )
    assert result.verdict == "approved"


# ============================================================ M3: the fragmentation defense
#
# The two rows below are AdCP's own conformance example, reproduced. The spec says an agent that
# approves row 2 without escalation "is non-conformant: it has either failed to aggregate across the
# 30-day window, failed to key on the correct tuple, or failed to include the incoming commit in the
# sum." Row 2 is the whole mechanism in one assertion.

import pytest  # noqa: E402

from aggregation import AggregateRead  # noqa: E402

#: Supplied to `evaluate` explicitly: which threshold applies depends on the request's phase, which
#: is a property of the request rather than of the plan.
AGG_THRESHOLD = 10_000.0

THRESHOLD_PLAN = {
    **PLAN,
    "budget": {"total": 1_000_000.0, "currency": "USD", "reallocation_threshold": 10_000.0},
}
SMALL_ACTION = {**ACTION, "amount": 2_500.0}


def test_conformance_row_1_below_threshold_auto_approves():
    """Prior aggregate 4,000 + incoming 2,500 = 6,500, under the 10,000 threshold."""
    result = policy.evaluate(
        plan=THRESHOLD_PLAN,
        action=SMALL_ACTION,
        aggregate=AggregateRead(4_000.0),
        aggregate_threshold=AGG_THRESHOLD,
    )
    assert result.verdict == "approved"
    assert result.escalate is False


def test_conformance_row_2_aggregate_crosses_threshold_and_escalates():
    """Prior 8,000 + incoming 2,500 = 10,500. Escalates even though 2,500 is small in isolation.

    This is the assertion the spec names non-conformance against.
    """
    result = policy.evaluate(
        plan=THRESHOLD_PLAN,
        action=SMALL_ACTION,
        aggregate=AggregateRead(8_000.0),
        aggregate_threshold=AGG_THRESHOLD,
    )
    assert result.escalate is True
    assert result.verdict != "approved"


def test_the_incoming_commit_is_included_in_the_sum():
    """M3.6. Without this the last sub-threshold commit is judged against a sum excluding itself.

    8,000 alone is under 10,000; 8,000 + 2,500 is not. An agent that omitted the incoming commit would
    approve here, which is exactly the fragmentation attack succeeding.
    """
    under = policy.evaluate(
        plan=THRESHOLD_PLAN,
        action=SMALL_ACTION,
        aggregate=AggregateRead(7_000.0),
        aggregate_threshold=AGG_THRESHOLD,
    )
    over = policy.evaluate(
        plan=THRESHOLD_PLAN,
        action=SMALL_ACTION,
        aggregate=AggregateRead(8_000.0),
        aggregate_threshold=AGG_THRESHOLD,
    )
    assert under.escalate is False
    assert over.escalate is True

    finding = next(f for f in over.findings if "Trailing" in f.explanation)
    assert finding.details["aggregate_committed"] == 8_000.0
    assert finding.details["aggregate_including_incoming"] == 10_500.0
    assert finding.details["incoming_commit_in_isolation"] == 2_500.0


def test_many_commits_each_within_plan_authority_still_escalate_collectively():
    """The attack this defends against: fragmenting one large spend into sub-threshold commits.

    Each 2,500 commit is trivially within a 1,000,000 plan budget, so the per-plan check passes every
    time. Only the aggregate stops it.
    """
    running = 0.0
    escalated_at = None
    for i in range(1, 8):
        result = policy.evaluate(
            plan=THRESHOLD_PLAN,
        action=SMALL_ACTION,
        aggregate=AggregateRead(running),
        aggregate_threshold=AGG_THRESHOLD,
        )
        if result.escalate and escalated_at is None:
            escalated_at = i
        running += 2_500.0

    # 10,000 threshold / 2,500 per commit: the fourth commit takes the sum to 10,000 (still within),
    # the fifth to 12,500 (over).
    assert escalated_at == 5


def test_no_aggregate_supplied_leaves_the_defense_inactive_rather_than_reading_zero():
    """A caller that passes no aggregate must not be told its aggregate spend is zero."""
    result = policy.evaluate(plan=THRESHOLD_PLAN, action=SMALL_ACTION)
    assert result.escalate is False
    assert not [f for f in result.findings if "Trailing" in f.explanation]


# ---------------------------------------------------------- an unreadable aggregate degrades honestly


def test_an_unknown_aggregate_never_approves():
    """NFR-6. The verdict may be `conditions` or `denied`, never `approved`."""
    result = policy.evaluate(
        plan=THRESHOLD_PLAN,
        action=SMALL_ACTION,
        aggregate=AggregateRead(None, "aggregate read exceeded its 3s budget"),
    )
    assert result.verdict != "approved"


def test_an_unknown_aggregate_carries_confidence_and_uncertainty_reason():
    """Both SDK fields, together. A reduced confidence with no stated reason says something was
    uncertain but not what."""
    result = policy.evaluate(
        plan=THRESHOLD_PLAN,
        action=SMALL_ACTION,
        aggregate=AggregateRead(None, "aggregate read failed: ClientError"),
    )
    finding = next(f for f in result.findings if f.uncertainty_reason)
    assert finding.confidence < 1.0
    assert finding.uncertainty_reason == "aggregate read failed: ClientError"
    assert finding.to_wire()["uncertainty_reason"] == "aggregate read failed: ClientError"


def test_an_unknown_aggregate_is_not_reported_as_evaluated():
    """A category that could not be checked must never appear in `categories_evaluated`."""
    result = policy.evaluate(
        plan=THRESHOLD_PLAN, action=SMALL_ACTION, aggregate=AggregateRead(None, "unavailable")
    )
    assert policy.CATEGORY_BUDGET not in result.categories_evaluated


def test_an_unknown_aggregate_reports_no_headroom_figure():
    """Absent is not zero, and it is not a number either. Reporting headroom we could not read is the
    one outcome that would silently reopen the hole."""
    result = policy.evaluate(
        plan=THRESHOLD_PLAN, action=SMALL_ACTION, aggregate=AggregateRead(None, "unavailable")
    )
    finding = next(f for f in result.findings if f.uncertainty_reason)
    assert "aggregate_committed" not in finding.details
    assert "aggregate_including_incoming" not in finding.details


def test_an_unknown_aggregate_yields_an_advisory_condition_not_a_machine_actionable_one():
    """R3: no `required_value`, because there is nothing the caller can set to fix our outage."""
    result = policy.evaluate(
        plan=THRESHOLD_PLAN, action=SMALL_ACTION, aggregate=AggregateRead(None, "unavailable")
    )
    condition = next(c for c in result.conditions if c.corrects == policy.CATEGORY_BUDGET)
    assert condition.required_value is None
    assert "required_value" not in condition.to_wire()


def test_a_plan_with_no_threshold_reports_the_aggregate_as_unevaluated():
    """No declared threshold means nothing to evaluate against -- not a pass."""
    plan = {**PLAN, "budget": {"total": 100000.0, "currency": "USD"}}
    result = policy.evaluate(plan=plan, action=ACTION, aggregate=AggregateRead(50_000.0))
    assert result.escalate is False


# ------------------------------------------------------------------------------- policy resolution


POLICY_PLAN = {
    **PLAN,
    "custom_policies": [
        {
            "policy_id": "brand_tone",
            "enforcement": "must",
            "policy": "No competitor mentions.",
            "requires_human_review": True,
        },
        {"policy_id": "soft_pref", "enforcement": "should", "policy": "Prefer morning dayparts."},
    ],
    "policy_ids": ["uk_hfss"],
}


def test_inline_policies_resolve_to_sdk_resolved_policy_models():
    resolution = policy.resolve_policies(POLICY_PLAN)
    assert [p.policy_id for p in resolution.resolved] == ["brand_tone", "soft_pref"]
    assert [p.enforcement.value for p in resolution.resolved] == ["must", "should"]
    # `ResolvedPolicy.source` is explicit|auto_applied -- NOT PolicyEntry's registry|inline.
    assert {p.source.value for p in resolution.resolved} == {"explicit"}


def test_resolved_policies_is_no_longer_empty():
    """RQ2=A: `resolved_policies: []` was a live inaccuracy for any plan declaring policies."""
    assert policy.resolve_policies(POLICY_PLAN).resolved


def test_a_policy_requiring_human_review_is_surfaced():
    """AdCP requires the agent to set `human_review_required` itself from resolved policies."""
    assert policy.resolve_policies(POLICY_PLAN).requires_human_review is True
    assert policy.resolve_policies(PLAN).requires_human_review is False


def test_registry_ids_are_reported_as_unresolvable_rather_than_silently_dropped():
    """A plan declaring `uk_hfss` that gets silence has been told its policy is in force when nothing
    resolved it. This agent has no registry, and says so."""
    assert policy.resolve_policies(POLICY_PLAN).unresolvable_ids == ("uk_hfss",)


def test_an_entry_missing_required_fields_is_not_given_a_default_enforcement():
    """Defaulting a missing `enforcement` would silently downgrade a `must` to a `should`."""
    plan = {**PLAN, "custom_policies": [{"policy_id": "no_enforcement", "policy": "text"}]}
    assert policy.resolve_policies(plan).resolved == ()


def test_an_invalid_enforcement_value_is_rejected_by_the_sdk_enum():
    plan = {
        **PLAN,
        "custom_policies": [{"policy_id": "x", "enforcement": "mandatory", "policy": "t"}],
    }
    with pytest.raises(ValueError):
        policy.resolve_policies(plan)


def test_enforcement_maps_to_severity_per_the_spec():
    """`must` rejects, `should` warns, `may` is informational -- the spec's own consequences."""
    assert policy.ENFORCEMENT_SEVERITY["must"] == "critical"
    assert policy.ENFORCEMENT_SEVERITY["should"] == "warning"
    assert policy.ENFORCEMENT_SEVERITY["may"] == "info"


def test_policies_evaluated_stays_empty_because_there_is_no_semantic_evaluator():
    """Resolution is not evaluation. Listing a resolved natural-language policy here would assert a
    compliance check this agent never ran."""
    result = policy.evaluate(plan=POLICY_PLAN, action=ACTION)
    assert result.policies_evaluated == ()


# --------------------------------------------------------------------------------------- structure


def test_condition_field_is_a_bare_dot_path():
    """AdCP: "Dot-path to the field that needs adjustment (in payload for proposed, in
    planned_delivery for committed)". The path is relative to that object, so a `payload.` prefix would
    be wrong."""
    over_budget = {**ACTION, "amount": 500_000.0}
    result = policy.evaluate(plan=PLAN, action=over_budget)
    for condition in result.conditions:
        wire = condition.to_wire()
        assert not wire["field"].startswith("payload.")
        assert not wire["field"].startswith("planned_delivery.")


def test_policy_module_imports_nothing_that_performs_io():
    """C4's sink property: the module deciding verdicts imports nothing that can fail, which is what
    keeps these rules testable without AWS."""
    import ast
    import inspect
    from pathlib import Path

    tree = ast.parse(Path(inspect.getfile(policy)).read_text(encoding="utf-8"))
    module_level = {
        alias.name.split(".")[0]
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in getattr(node, "names", [])
    } | {
        node.module.split(".")[0]
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module
    }
    for banned in ("boto3", "botocore", "httpx", "requests", "state", "aggregation"):
        assert banned not in module_level, f"{banned} must not be imported at module level"
