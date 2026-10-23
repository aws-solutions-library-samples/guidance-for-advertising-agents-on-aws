"""
Unit tests for the plan-id extraction that drives cross-session plan tracking.

The extraction is the error-prone half of the plan-link feature: plan_id is never a top-level
attribute, it is buried inside a tool call's input or a tool result's output, and the output may be
either the JSON string a tool returned or an already-parsed map. The DynamoDB write/query half is
exercised live on deploy; this pins the parsing so a spec-shaped payload cannot silently stop
yielding a plan id.
"""

import json

from session_records import _extract_plan_ids


def test_reads_plan_id_from_tool_call_input():
    content = {"toolName": "adcp_sync_plans", "callId": "c1", "input": {"plan_id": "plan-123"}}
    assert _extract_plan_ids(content) == {"plan-123"}


def test_reads_plan_ids_list_from_audit_log_request():
    content = {"toolName": "adcp_get_plan_audit_logs", "input": {"plan_ids": ["plan-a", "plan-b"]}}
    assert _extract_plan_ids(content) == {"plan-a", "plan-b"}


def test_reads_plan_id_from_output_json_string():
    # The buyer records a tool's output as the JSON string it returned.
    output = json.dumps({"plans": [{"plan_id": "plan-xyz", "status": "registered"}]})
    content = {"toolName": "adcp_sync_plans", "callId": "c2", "status": "success", "output": output}
    assert _extract_plan_ids(content) == {"plan-xyz"}


def test_reads_plan_id_from_already_parsed_output_map():
    # The seller-side recorder writes output as an object rather than a string; both must work.
    content = {"toolName": "adcp_check_governance", "output": {"plan_id": "plan-chk", "verdict": "approved"}}
    assert _extract_plan_ids(content) == {"plan-chk"}


def test_unions_input_and_output_ids():
    content = {
        "toolName": "adcp_check_governance",
        "input": {"plan_id": "plan-1"},
        "output": json.dumps({"plan_id": "plan-1", "plans": [{"plan_id": "plan-2"}]}),
    }
    assert _extract_plan_ids(content) == {"plan-1", "plan-2"}


def test_no_plan_id_yields_empty_set():
    # get_products / sync_accounts and similar are pre-plan or plan-agnostic.
    assert _extract_plan_ids({"toolName": "adcp_get_products", "input": {"brief": "ctv"}}) == set()
    assert _extract_plan_ids({"from": "Chat UI", "text": "find inventory"}) == set()


def test_tolerates_unparseable_output_and_wrong_types():
    assert _extract_plan_ids({"output": "not json {"}) == set()
    assert _extract_plan_ids({"input": {"plan_id": 123}}) == set()  # non-string ignored
    assert _extract_plan_ids({"input": {"plan_ids": "plan-a"}}) == set()  # not a list, ignored
    assert _extract_plan_ids({"output": json.dumps({"plans": "nope"})}) == set()


def test_ignores_blank_plan_ids():
    assert _extract_plan_ids({"input": {"plan_id": "   "}}) == set()
