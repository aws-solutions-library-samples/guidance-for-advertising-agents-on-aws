"""Regression tests for `app._has_payload_errors`.

The recorder used to classify a tool result by the envelope alone
(`mcp_error`/`a2a_error`/`adcp_error`). AdCP's canonical error layer is the payload's
`errors[]` array, so a seller that refused a task there was written to DynamoDB as a
success and the Live Tool Activity panel rendered it green. Found live: Poseidon refused
a `create_media_buy` over a missing `priority.type` and the session recorded a pass.

Error payloads here are constructed with the SDK's own `Error` model rather than as
dicts, so a field name that AdCP does not define fails at construction instead of
silently making a case vacuous.
"""

from __future__ import annotations

import pytest
from adcp.types.generated_poc.core.error import Error

from app import _has_payload_errors


def _err(**kwargs) -> dict:
    """An AdCP `errors[]` entry, validated by the SDK on the way out."""
    return Error(**kwargs).model_dump(exclude_none=True)


VALIDATION_ERROR = _err(code="VALIDATION_ERROR", message="priority.type is required")


class TestEnvelopeOnly:
    def test_clean_result_is_not_an_error(self):
        assert _has_payload_errors({"media_buy_id": "mb_1", "status": "completed"}) is False

    def test_empty_errors_array_is_not_an_error(self):
        assert _has_payload_errors({"status": "completed", "errors": []}) is False

    @pytest.mark.parametrize("result", [None, "completed", 42, ["errors"]])
    def test_non_dict_results_are_not_errors(self, result):
        assert _has_payload_errors(result) is False

    def test_errors_key_holding_a_non_list_is_ignored(self):
        # A malformed payload must not be read as a failure; only a real array counts.
        assert _has_payload_errors({"errors": {"code": "VALIDATION_ERROR"}}) is False

    def test_entry_without_a_code_is_not_fatal(self):
        # `code` is required on AdCP's Error, so an entry lacking one is not a
        # well-formed error and is not evidence of failure.
        assert _has_payload_errors({"errors": [{"message": "something"}]}) is False


class TestTopLevelErrors:
    def test_errors_array_marks_a_failure(self):
        assert _has_payload_errors({"status": "completed", "errors": [VALIDATION_ERROR]}) is True

    def test_completed_status_does_not_override_the_errors_array(self):
        # The live shape that caused the defect: `status: "completed"` is the TASK
        # status -- the task finished. `errors[]` says how it finished.
        refused = {
            "task": "create_media_buy",
            "status": "completed",
            "errors": [VALIDATION_ERROR],
        }
        assert _has_payload_errors(refused) is True

    def test_one_fatal_entry_among_warnings_is_still_fatal(self):
        payload = {
            "errors": [
                _err(code="DEPRECATED_FIELD", message="use pricing_options", severity="warning"),
                VALIDATION_ERROR,
            ]
        }
        assert _has_payload_errors(payload) is True


class TestWarningSeverity:
    """`severity` is not in AdCP 3.1's `core/error.json` -- `Error.model_fields` has no
    such key, and the model only accepts it because its config is `extra="allow"`.
    Honouring it now means an SDK bump that adds non-fatal warnings does not start
    reporting them as failures.
    """

    def test_warning_only_is_not_a_failure(self):
        payload = {"errors": [_err(code="DEPRECATED_FIELD", message="use pricing_options", severity="warning")]}
        assert _has_payload_errors(payload) is False

    def test_severity_is_not_a_declared_adcp_field(self):
        assert "severity" not in Error.model_fields


class TestFanOut:
    """`{task, results: [{seller_id, ...}]}` -- the shape every multi-seller call uses.
    The outer response can look clean while one seller refused.
    """

    def test_errors_on_a_result_entry(self):
        payload = {
            "task": "get_products",
            "results": [
                {"seller_id": "ok-seller", "products": []},
                {"seller_id": "refusing-seller", "errors": [VALIDATION_ERROR]},
            ],
        }
        assert _has_payload_errors(payload) is True

    def test_errors_nested_under_a_result_entrys_response(self):
        payload = {
            "task": "create_media_buy",
            "results": [
                {"seller_id": "refusing-seller", "response": {"status": "completed", "errors": [VALIDATION_ERROR]}}
            ],
        }
        assert _has_payload_errors(payload) is True

    def test_clean_fan_out_is_not_an_error(self):
        payload = {
            "task": "get_products",
            "results": [
                {"seller_id": "a", "response": {"products": [], "errors": []}},
                {"seller_id": "b", "products": []},
            ],
        }
        assert _has_payload_errors(payload) is False

    def test_non_dict_entries_are_skipped(self):
        assert _has_payload_errors({"results": ["oops", None, 7]}) is False

    def test_results_holding_a_non_list_does_not_raise(self):
        assert _has_payload_errors({"results": None}) is False
