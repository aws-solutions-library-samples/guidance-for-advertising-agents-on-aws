"""Tests for the operator escalation script.

Driven through `state` against a moto-backed table, the same way the handler tests are, because what is
worth testing here is the state transition rather than the argument parsing.
"""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

import resolve_escalation
import state

TABLE = "adcp-reference-governance-state-test-resolve"

PLAN = {
    "plan_id": "plan-r1",
    "brand": {"domain": "nike.com"},
    "objectives": "Reach adults 18-49.",
    "budget": {"total": 100000.0, "currency": "USD", "reallocation_threshold": 0},
    "flight": {"start": "2026-12-05T00:00:00Z", "end": "2026-12-10T23:59:59Z"},
}


@pytest.fixture()
def table(monkeypatch):
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


def _suspended_plan_with_escalation(*, plan_id: str = "plan-r1", reason: str = "needs review") -> str:
    state.put_plan({**PLAN, "plan_id": plan_id}, requires_human_review=True)
    check_id = state.put_check(
        plan_id=plan_id,
        verdict="denied",
        explanation="Registered requiring human review.",
        payload={"registration": True},
        findings=[],
        categories_evaluated=[],
    )
    state.put_escalation(plan_id=plan_id, check_id=check_id, reason=reason)
    return check_id


def test_listing_changes_nothing(table, capsys):
    check_id = _suspended_plan_with_escalation()
    assert resolve_escalation.list_escalations("plan-r1") == 0
    out = capsys.readouterr().out
    assert "SUSPENDED" in out
    assert check_id in out
    assert state.is_suspended("plan-r1") is True
    assert state.unresolved_escalations("plan-r1")


def test_a_plan_with_no_escalations_says_so_without_erroring(table, capsys):
    state.put_plan(PLAN)
    assert resolve_escalation.list_escalations("plan-r1") == 0
    assert "no escalations recorded" in capsys.readouterr().out


def test_a_plan_that_was_never_synced_is_distinguished_from_one_with_nothing_outstanding(
    table, capsys
):
    """Two very different situations that would otherwise print identically. An operator told "no
    escalations" about a plan id they mistyped would go looking for the wrong problem."""
    assert resolve_escalation.list_escalations("plan-does-not-exist") == 0
    assert "never been synced" in capsys.readouterr().out


def test_without_apply_nothing_is_written(table, capsys):
    check_id = _suspended_plan_with_escalation()
    assert (
        resolve_escalation.resolve(
            plan_id="plan-r1", check_id=check_id, resolution="approved_by_human", apply=False
        )
        == 0
    )
    assert "Re-run with --apply" in capsys.readouterr().out
    assert state.is_suspended("plan-r1") is True
    assert state.unresolved_escalations("plan-r1"), "the escalation is still open"


def test_applying_records_the_decision_and_clears_the_suspension(table):
    check_id = _suspended_plan_with_escalation()
    assert (
        resolve_escalation.resolve(
            plan_id="plan-r1", check_id=check_id, resolution="approved_by_human", apply=True
        )
        == 0
    )

    escalation = state.list_escalations("plan-r1")[0]
    assert escalation["resolution"] == "approved_by_human"
    assert escalation["resolved_at"]
    assert state.is_suspended("plan-r1") is False

    check = next(c for c in state.list_checks("plan-r1") if c["check_id"] == check_id)
    assert check["verdict"] == "approved", "the human's decision becomes the check's verdict"
    assert check["recommended_verdict"] == "denied", (
        "and the agent's recommendation survives it -- without this, human_override_rate compares the "
        "human's decision against itself and reports 0.0 for every reviewed plan"
    )


def test_a_rejection_resolves_to_denied(table):
    check_id = _suspended_plan_with_escalation()
    resolve_escalation.resolve(
        plan_id="plan-r1", check_id=check_id, resolution="rejected_by_human", apply=True
    )
    check = next(c for c in state.list_checks("plan-r1") if c["check_id"] == check_id)
    assert check["verdict"] == "denied"
    # Agreement, not an override: the agent recommended denied and the human agreed. Both recorded so the
    # metric can tell this case from the reversal above.
    assert check["recommended_verdict"] == "denied"
    assert state.is_suspended("plan-r1") is False


def test_the_plan_stays_suspended_while_another_escalation_is_outstanding(table, capsys):
    """The rule that makes suspension safe to clear at all. Clearing on the first resolution would let a
    plan with an unreviewed escalation transact."""
    first = _suspended_plan_with_escalation()
    second = state.put_check(
        plan_id="plan-r1",
        verdict="denied",
        explanation="Second escalation.",
        payload={},
        findings=[],
        categories_evaluated=[],
    )
    state.put_escalation(plan_id="plan-r1", check_id=second, reason="also needs review")

    resolve_escalation.resolve(
        plan_id="plan-r1", check_id=first, resolution="approved_by_human", apply=True
    )
    assert state.is_suspended("plan-r1") is True
    assert "remains SUSPENDED" in capsys.readouterr().out

    resolve_escalation.resolve(
        plan_id="plan-r1", check_id=second, resolution="approved_by_human", apply=True
    )
    assert state.is_suspended("plan-r1") is False


def test_an_unknown_resolution_is_refused_not_recorded(table, capsys):
    """Recording it would silently exclude the escalation from `human_override_rate` -- a degraded metric
    instead of a message the operator can act on."""
    check_id = _suspended_plan_with_escalation()
    assert (
        resolve_escalation.resolve(
            plan_id="plan-r1", check_id=check_id, resolution="looked fine to me", apply=True
        )
        == 2
    )
    assert "Unknown resolution" in capsys.readouterr().out
    assert state.unresolved_escalations("plan-r1")


def test_resolving_a_missing_escalation_reports_it_rather_than_succeeding(table, capsys):
    _suspended_plan_with_escalation()
    assert (
        resolve_escalation.resolve(
            plan_id="plan-r1", check_id="chk_nonexistent", resolution="approved_by_human", apply=True
        )
        == 1
    )
    assert "no escalation for check" in capsys.readouterr().out
    assert state.is_suspended("plan-r1") is True


def test_an_already_resolved_escalation_is_not_overwritten(table, capsys):
    """A recorded human decision is evidence. Re-resolving would replace one human's answer with
    another's and leave no trace of the first."""
    check_id = _suspended_plan_with_escalation()
    resolve_escalation.resolve(
        plan_id="plan-r1", check_id=check_id, resolution="approved_by_human", apply=True
    )
    assert (
        resolve_escalation.resolve(
            plan_id="plan-r1", check_id=check_id, resolution="rejected_by_human", apply=True
        )
        == 1
    )
    assert "already resolved" in capsys.readouterr().out
    escalation = state.list_escalations("plan-r1")[0]
    assert escalation["resolution"] == "approved_by_human"


def test_the_script_is_not_reachable_from_the_runtime():
    """AdCP puts the human out of band, so this module must stay out of the request path.

    Asserted rather than trusted: an import added for convenience would move the review boundary inside
    the protocol, letting a party clear a suspension it is subject to.
    """
    import ast
    from pathlib import Path

    # Parsed rather than substring-matched. A comment in `main.py` explaining that this script is what
    # overwrites a recommended verdict is exactly the documentation that should be there; only a real
    # import or attribute access moves the boundary.
    runtime_modules = ("main", "policy", "state", "aggregation", "jws", "errors", "gov_logging")
    for name in runtime_modules:
        tree = ast.parse(Path(__file__).with_name(f"{name}.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(alias.name != "resolve_escalation" for alias in node.names), (
                    f"{name}.py imports resolve_escalation. The operator script must not be reachable "
                    "from the request path -- see its module docstring."
                )
            elif isinstance(node, ast.ImportFrom):
                assert node.module != "resolve_escalation", (
                    f"{name}.py imports from resolve_escalation."
                )

    # It registers no tool either: no `advertised_tools`, no handler methods, no `serve` call.
    script = Path(__file__).with_name("resolve_escalation.py").read_text(encoding="utf-8")
    assert "advertised_tools" not in script
    assert "serve(" not in script
