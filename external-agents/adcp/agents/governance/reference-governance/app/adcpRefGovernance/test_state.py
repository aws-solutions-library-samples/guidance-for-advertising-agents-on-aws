"""Tests for the governance store, against a mock DynamoDB.

The load-bearing assertions are about **what consumes authority**. AdCP tracks committed budget from
reported outcomes rather than from approved checks, and getting that wrong in either direction is
serious: counting approvals would strand authority the buyer never spent, and ignoring outcomes would let
a plan be spent twice over.
"""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

import state


TABLE = "adcp-reference-governance-state-test"

PLAN = {
    "plan_id": "plan-1",
    "budget": {"total": 100000.0, "currency": "USD"},
    "flight": {"start_date": "2026-12-05", "end_date": "2026-12-10"},
}


@pytest.fixture()
def store(monkeypatch):
    with mock_aws():
        monkeypatch.setenv("STATE_TABLE_NAME", TABLE)
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
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
        yield
        state.reset_for_tests()


def test_a_plan_round_trips(store):
    state.put_plan(PLAN)
    stored = state.get_plan("plan-1")
    assert stored is not None
    assert stored["plan"]["budget"]["total"] == 100000
    assert stored["version"] == 1


def test_an_unsynced_plan_is_none_not_an_empty_plan(store):
    # None is PLAN_NOT_FOUND, which is a correctable error. An empty plan would be a plan with no
    # constraints, which would approve everything.
    assert state.get_plan("never-synced") is None


def test_re_syncing_increments_the_version(store):
    state.put_plan(PLAN)
    assert state.put_plan(PLAN)["version"] == 2
    assert state.put_plan(PLAN)["version"] == 3


def test_the_version_is_derived_not_taken_from_the_caller(store):
    state.put_plan(PLAN)
    # A caller-supplied version could silently overwrite a later amendment.
    result = state.put_plan({**PLAN, "version": 99})
    assert result["version"] == 2


def test_floats_survive_the_round_trip_exactly(store):
    # Via str, not Decimal(float): the binary form of 0.1 is not 0.1, and a budget that comes back
    # microscopically different then fails its own equality check.
    state.put_plan({**PLAN, "budget": {"total": 0.1, "currency": "USD"}})
    assert state.get_plan("plan-1")["plan"]["budget"]["total"] == 0.1


def test_committed_total_is_zero_before_any_outcome(store):
    state.put_plan(PLAN)
    assert state.committed_total("plan-1") == 0.0


def test_approved_checks_do_not_consume_authority(store):
    # The rule that matters. An approval that is never acted on must leave the budget untouched.
    state.put_plan(PLAN)
    state.put_check(plan_id="plan-1", verdict="approved", explanation="fine")
    state.put_check(plan_id="plan-1", verdict="approved", explanation="also fine")
    assert state.committed_total("plan-1") == 0.0


def test_completed_outcomes_consume_authority(store):
    state.put_plan(PLAN)
    state.put_outcome(plan_id="plan-1", outcome="completed", committed_budget=40000.0, check_id="c1")
    state.put_outcome(plan_id="plan-1", outcome="completed", committed_budget=25000.0, check_id="c2")
    assert state.committed_total("plan-1") == 65000.0


def test_failed_outcomes_consume_nothing(store):
    # A rejected request spent no money. Counting it would strand authority permanently.
    state.put_plan(PLAN)
    state.put_outcome(plan_id="plan-1", outcome="failed", committed_budget=40000.0, check_id="c1")
    assert state.committed_total("plan-1") == 0.0


def test_an_outcome_with_no_amount_is_absent_not_zero(store):
    state.put_plan(PLAN)
    state.put_outcome(plan_id="plan-1", outcome="completed", committed_budget=None, check_id="c1")
    stored = state.list_outcomes("plan-1")[0]
    assert "committed_budget" not in stored
    assert state.committed_total("plan-1") == 0.0


def test_a_real_zero_is_stored_as_zero(store):
    # Absent and zero are different, and both are legitimate.
    state.put_plan(PLAN)
    state.put_outcome(plan_id="plan-1", outcome="completed", committed_budget=0.0, check_id="c1")
    assert state.list_outcomes("plan-1")[0]["committed_budget"] == 0


def test_checks_and_outcomes_are_listed_separately(store):
    state.put_plan(PLAN)
    state.put_check(plan_id="plan-1", verdict="approved", explanation="x")
    state.put_outcome(plan_id="plan-1", outcome="completed", committed_budget=1.0, check_id="c")
    assert len(state.list_checks("plan-1")) == 1
    assert len(state.list_outcomes("plan-1")) == 1


def test_state_is_scoped_per_plan(store):
    state.put_plan(PLAN)
    state.put_plan({**PLAN, "plan_id": "plan-2"})
    state.put_outcome(plan_id="plan-1", outcome="completed", committed_budget=10.0, check_id="c")
    assert state.committed_total("plan-2") == 0.0


def test_a_check_id_is_returned_so_an_outcome_can_cite_it(store):
    state.put_plan(PLAN)
    check_id = state.put_check(plan_id="plan-1", verdict="approved", explanation="x")
    assert check_id.startswith("chk_")
    assert state.list_checks("plan-1")[0]["check_id"] == check_id


def test_importing_makes_no_aws_call(monkeypatch):
    # The deploy scripts and the tests both import this before a table exists.
    monkeypatch.delenv("STATE_TABLE_NAME", raising=False)
    state.reset_for_tests()
    with pytest.raises(RuntimeError):
        state.table()


# ------------------------------------------------------------------- suspension and escalations (M1/M2)
#
# The assertions that matter here are the ones proving a caller cannot un-suspend its own plan. A
# suspension exists because human review is mandatory; if re-syncing without the flag cleared it, the
# review would be optional at the caller's discretion, which is the opposite of the requirement.

REVIEW_PLAN = {**PLAN, "plan_id": "plan-review", "human_review_required": True}


def test_a_plan_flagged_for_human_review_is_suspended(store):
    state.put_plan(REVIEW_PLAN)
    assert state.is_suspended("plan-review") is True
    assert state.get_plan("plan-review")["human_review_required"] is True


def test_an_unflagged_plan_is_not_suspended(store):
    state.put_plan(PLAN)
    assert state.is_suspended("plan-1") is False


def test_policy_resolution_can_suspend_a_plan_the_caller_did_not_flag(store):
    """AdCP requires the agent to set the flag itself when a resolved policy demands review."""
    state.put_plan(PLAN, requires_human_review=True)
    assert state.is_suspended("plan-1") is True


def test_resync_preserves_suspension(store):
    state.put_plan(REVIEW_PLAN)
    state.put_plan(REVIEW_PLAN)
    assert state.get_plan("plan-review")["version"] == 2
    assert state.is_suspended("plan-review") is True


def test_resync_without_the_flag_does_not_un_suspend(store):
    """The plan document is the caller's assertion; suspension is this agent's conclusion.

    Only `resolve_escalation` clears it. If a re-sync could, mandatory human review would be
    optional at the caller's discretion.
    """
    state.put_plan(REVIEW_PLAN)
    state.put_plan({**PLAN, "plan_id": "plan-review"})  # flag dropped
    assert state.get_plan("plan-review")["human_review_required"] is False
    assert state.is_suspended("plan-review") is True


def test_a_plan_never_synced_is_absent_not_suspended(store):
    assert state.is_suspended("no-such-plan") is False


def test_escalation_round_trips_and_starts_unresolved(store):
    state.put_plan(REVIEW_PLAN)
    check_id = state.put_check(
        plan_id="plan-review", verdict="conditions", explanation="registration check"
    )
    state.put_escalation(plan_id="plan-review", check_id=check_id, reason="human_review_required")

    escalations = state.list_escalations("plan-review")
    assert len(escalations) == 1
    assert escalations[0]["check_id"] == check_id
    assert escalations[0]["reason"] == "human_review_required"
    assert escalations[0].get("resolved_at") is None
    assert len(state.unresolved_escalations("plan-review")) == 1


def test_resolving_the_last_escalation_clears_suspension(store):
    state.put_plan(REVIEW_PLAN)
    check_id = state.put_check(plan_id="plan-review", verdict="conditions", explanation="reg")
    state.put_escalation(plan_id="plan-review", check_id=check_id, reason="human_review_required")

    assert state.resolve_escalation(
        plan_id="plan-review", check_id=check_id, resolution="approved_by_human"
    ) is True

    resolved = state.list_escalations("plan-review")[0]
    assert resolved["resolution"] == "approved_by_human"
    assert resolved["resolved_at"] is not None
    assert state.unresolved_escalations("plan-review") == []
    assert state.is_suspended("plan-review") is False


def test_resolving_one_of_two_escalations_leaves_the_plan_suspended(store):
    state.put_plan(REVIEW_PLAN)
    first = state.put_check(plan_id="plan-review", verdict="conditions", explanation="a")
    second = state.put_check(plan_id="plan-review", verdict="conditions", explanation="b")
    state.put_escalation(plan_id="plan-review", check_id=first, reason="one")
    state.put_escalation(plan_id="plan-review", check_id=second, reason="two")

    state.resolve_escalation(plan_id="plan-review", check_id=first, resolution="approved_by_human")

    assert len(state.unresolved_escalations("plan-review")) == 1
    assert state.is_suspended("plan-review") is True


def test_resolving_a_missing_escalation_reports_failure(store):
    """"Resolved" and "there was nothing to resolve" are different outcomes for an operator."""
    state.put_plan(REVIEW_PLAN)
    assert state.resolve_escalation(
        plan_id="plan-review", check_id="chk_nonexistent", resolution="approved_by_human"
    ) is False
    assert state.is_suspended("plan-review") is True


def test_escalations_share_the_plan_partition(store):
    """One Query serves the audit view -- the reason a GSI was rejected."""
    state.put_plan(REVIEW_PLAN)
    check_id = state.put_check(plan_id="plan-review", verdict="approved", explanation="x")
    state.put_escalation(plan_id="plan-review", check_id=check_id, reason="r")
    state.put_outcome(
        plan_id="plan-review", outcome="completed", committed_budget=10.0, check_id=check_id
    )

    assert len(state.list_checks("plan-review")) == 1
    assert len(state.list_outcomes("plan-review")) == 1
    assert len(state.list_escalations("plan-review")) == 1
