"""Canaries for the SDK gaps this agent works around.

**Every test here asserts the gap STILL EXISTS.** That inversion is the point. A workaround whose
justification has quietly disappeared is worse than no workaround: it is code nobody can explain, kept
because deleting it feels risky. When an SDK release closes a gap, the matching canary fails, names the
gap, and says exactly what to delete.

So a failure in this file is **good news**. Read the failure message, do what it says, then delete the
canary and its entry in `SDK-GAPS.md`.

Two rules that keep this file honest:

1. **Canaries are named `test_sdk_gap_N_...`, behavioural tests are not.** The behaviour each workaround
   produces is tested elsewhere, under its own name. Retiring a gap must not delete the test that proves
   the agent still behaves correctly without it (D5=C). Grep for the gap id, not for the behaviour.
2. **Each canary asserts the SDK's state, never this agent's.** A canary that passed because our own code
   still had the workaround in it would never fail, which is the one way this file could be useless.

Verified against **two** SDK versions, deliberately: the pinned `adcp==6.6.0` (spec 3.1.1) and the latest
release at the time of writing, `adcp==7.0.2` (spec 3.1.15). All four gaps are present in both, so none of
them is an artefact of the pin. `SDK-GAPS.md` records the commands.
"""

from __future__ import annotations

import pytest


def test_sdk_gap_1_governance_error_codes_are_absent_from_the_recovery_table():
    """SDK-GAP-1 — `adcp_error` cannot classify governance codes.

    `adcp.server.helpers.adcp_error` populates `recovery` from `STANDARD_ERROR_CODES`. None of the
    governance codes is in it, so the helper defaults them to `terminal` — telling a buyer to stop
    retrying `CAMPAIGN_SUSPENDED`, a condition that resolves by a human clearing an escalation.

    **Retire by**: deleting `errors.code_metadata` and its manifest read, and calling
    `adcp.server.helpers.adcp_error` directly.
    """
    from adcp.server.helpers import STANDARD_ERROR_CODES

    still_missing = [
        code
        for code in ("CAMPAIGN_SUSPENDED", "PLAN_NOT_FOUND", "GOVERNANCE_CHECK_REQUIRED")
        if code not in STANDARD_ERROR_CODES
    ]
    assert still_missing, (
        "SDK-GAP-1 IS FIXED: STANDARD_ERROR_CODES now carries the governance codes. "
        "Delete `errors.code_metadata` and `errors._manifest`, have `errors.governance_error` call "
        "`adcp.server.helpers.adcp_error` without the `recovery`/`suggestion` overrides, then delete "
        "this canary and SDK-GAP-1 from SDK-GAPS.md. Keep `test_errors.py` — it tests the recovery "
        "classification, which must still be correct afterwards."
    )


def test_sdk_gap_2_the_schema_manifest_has_no_public_accessor():
    """SDK-GAP-2 — the manifest is only reachable through a private path.

    The recovery/suggestion table lives in `adcp/_schemas/<bundle>/manifest.json`. `adcp.schemas` exposes
    `load_schema`, which reaches only `adcp/schemas/` (one bundled file, `adcp-agents.json`), and
    `AdcpManifest` is not re-exported from `adcp.types` — it is not mentioned in that module at all.

    Two independent halves, asserted separately, because the SDK could fix either one alone.

    **Retire by**: reading the manifest through whatever public accessor appears, and dropping the
    `importlib.resources` reach into `_schemas` in `errors._manifest`.
    """
    import adcp.schemas
    import adcp.types

    assert not hasattr(adcp.types, "AdcpManifest"), (
        "SDK-GAP-2 (half 1) IS FIXED: `AdcpManifest` is now importable from `adcp.types`. "
        "Change the import in `errors.py` from "
        "`adcp.types.generated_poc.manifest_schema` to `adcp.types`, and update this canary — the "
        "private `_schemas` path (half 2) may still be a gap."
    )
    assert not hasattr(adcp.schemas, "load_manifest"), (
        "SDK-GAP-2 (half 2) MAY BE FIXED: `adcp.schemas` now exposes a manifest accessor. Check its "
        "signature, then replace the `importlib.resources` reach into `adcp/_schemas/` in "
        "`errors._manifest` with it. That reach is the ONLY private-path access in this agent, so "
        "retiring it changes one function."
    )


def test_sdk_gap_4_the_audit_finding_still_diverges_from_the_response_finding():
    """SDK-GAP-4 — two different `Finding` models, one description claiming they are the same.

    `get_plan_audit_logs_response.Entry.findings` describes its items as the "same structure as
    check_governance response findings". They are not: the audit variant omits `details`,
    `source_plan_id` and `uncertainty_reason`, and sets `extra='forbid'`, so passing a response finding
    through raises three `extra_forbidden` errors.

    This is a **schema** divergence, not an SDK packaging one, so it may well be intentional — the entry
    in `SDK-GAPS.md` says to raise it upstream rather than assume it is a bug. Either way the projection
    in `main._audit_findings` is required while the two differ.

    **Retire by**: passing findings straight through, if and only if the two models converge.
    """
    from adcp.types.generated_poc.governance.check_governance_response import (
        Finding as ResponseFinding,
    )
    from adcp.types.generated_poc.governance.get_plan_audit_logs_response import (
        Finding as AuditFinding,
    )

    audit_fields = set(AuditFinding.model_fields)
    response_fields = set(ResponseFinding.model_fields)
    assert response_fields - audit_fields, (
        "SDK-GAP-4 IS FIXED: the audit `Finding` now accepts every field the response `Finding` has. "
        "`main._audit_findings` can pass findings through unchanged. Delete it, this canary, and the "
        "SDK-GAP-4 entry — but keep "
        "`test_the_audit_finding_rejects_the_response_findings_extra_fields`'s replacement: whatever "
        "the audit entry accepts, the projection must still be constructed rather than dict-copied."
    )
    assert AuditFinding.model_config.get("extra") == "forbid", (
        "SDK-GAP-4 partially changed: the audit `Finding` no longer forbids extra fields, so a "
        "response finding would now pass through SILENTLY rather than failing. That is worse than the "
        "gap. Keep `main._audit_findings` and re-read the schema before touching it."
    )


def test_sdk_gap_5_governance_component_types_are_not_re_exported():
    """SDK-GAP-5 — the component models are only importable from `generated_poc`.

    `adcp.types` re-exports the top-level request/response models but none of their components. So
    `Entry`, `Finding`, `Summary`, `Statuses`, `Escalation`, `DriftMetrics`, `Adcp`, `Governance` and
    `Idempotency` all import from `adcp.types.generated_poc.*`.

    Worth stating plainly, because the absence invites the wrong conclusion: this is a **re-export** gap,
    not a missing-type gap. The types exist and are complete. Anyone who reads "not in `adcp.types`" as
    "the SDK does not have it" ends up hand-building a dict, which is the failure
    `use-the-adcp-sdk.md` exists to prevent.

    **Retire by**: moving these names into the `adcp.types` import block in `main.py`.
    """
    import adcp.types

    names = (
        "Entry",
        "Finding",
        "Summary",
        "Statuses",
        "Escalation",
        "DriftMetrics",
        "Adcp",
        "Governance",
        "Idempotency",
    )
    still_absent = [name for name in names if not hasattr(adcp.types, name)]
    assert still_absent, (
        "SDK-GAP-5 IS FIXED: every governance component type is now re-exported from `adcp.types`. "
        "Move them into the `from adcp.types import (...)` block in `main.py`, delete the two "
        "`generated_poc` import blocks and their SDK-GAP-5 comments, then delete this canary."
    )
    # Named individually so a partial fix reports WHICH ones moved rather than just "still broken".
    assert set(still_absent) == set(names), (
        "SDK-GAP-5 is partially fixed. These are now re-exported from `adcp.types` and should be "
        f"imported from there: {sorted(set(names) - set(still_absent))}"
    )


def test_sdk_gap_3_was_retracted_and_must_not_be_reinstated():
    """SDK-GAP-3 — **retracted**, kept as a test so the mistake cannot be made twice.

    The claim was that the SDK had no way to express a suspended plan, because
    `sync_plans_response.Status` is `['active', 'error']`. That was a misreading of two different
    statuses:

    - `sync_plans_response.Status` reports whether the **sync succeeded**. `active | error` is complete
      for that question, and a `suspended` member would not belong in it.
    - `get_plan_audit_logs_response.Status` is the **plan lifecycle**, and it already has
      `active | suspended | completed` — exactly what was thought to be missing.

    So `main._plan_status` reports real suspension through the SDK's own enum, and there is no workaround
    to retire. This test asserts the enums still say what they say, so nobody re-derives the phantom gap
    from the `sync_plans` side alone.
    """
    from adcp.types.generated_poc.governance.get_plan_audit_logs_response import (
        Status as AuditStatus,
    )
    from adcp.types.generated_poc.governance.sync_plans_response import Status as SyncStatus

    assert {m.value for m in SyncStatus} == {"active", "error"}, (
        "The sync-result enum changed. Re-read it before concluding anything about suspension: this "
        "enum answers 'did the sync work', not 'what state is the plan in'."
    )
    assert {m.value for m in AuditStatus} == {"active", "suspended", "completed"}, (
        "The plan-lifecycle enum changed. `main._plan_status` maps onto it directly and must be "
        "re-checked."
    )


def test_every_documented_gap_has_a_canary_and_vice_versa():
    """The registry check (G4). A gap in `SDK-GAPS.md` with no canary is a workaround that can outlive
    its reason silently, and a canary for an undocumented gap is a failure message pointing at nothing.

    Parses the ids out of both sides rather than restating them, so adding a gap to one place and
    forgetting the other fails here.
    """
    import re
    from pathlib import Path

    doc = Path(__file__).with_name("SDK-GAPS.md")
    assert doc.exists(), "SDK-GAPS.md is missing; the canaries' failure messages refer to it."

    documented = set(re.findall(r"SDK-GAP-(\d+)", doc.read_text(encoding="utf-8")))
    canaried = set(re.findall(r"def test_sdk_gap_(\d+)_", Path(__file__).read_text(encoding="utf-8")))
    assert documented == canaried, (
        f"SDK-GAPS.md documents {sorted(documented)} but this file canaries {sorted(canaried)}. "
        "Every gap needs both, including a retracted one — the retraction is what stops it being "
        "rediscovered."
    )


def test_the_workarounds_are_still_wired_in():
    """Not a canary: the other direction. Each gap's workaround must actually be reachable.

    A canary proves the gap exists. This proves we are still handling it. Both can pass while the agent
    is broken only if the workaround is present but wrong, which is what the behavioural tests cover.
    """
    import errors
    import main

    # SDK-GAP-1: the manifest supplies the recovery classification the helper cannot.
    recovery, _ = errors.code_metadata("CAMPAIGN_SUSPENDED")
    assert recovery == "transient", (
        "The manifest no longer classifies CAMPAIGN_SUSPENDED as transient. Either the SDK's manifest "
        "changed or `errors.code_metadata` stopped finding it -- check before trusting any recovery "
        "value this agent emits."
    )
    # SDK-GAP-4/5: the projection exists and returns the SDK's audit type, not dicts.
    projected = main._audit_findings(
        [{"category_id": "budget_authority", "severity": "info", "explanation": "ok", "details": {}}]
    )
    assert projected is not None
    assert type(projected[0]).__name__ == "Finding"
    assert not hasattr(projected[0], "details")


def test_gap_ids_are_unique_and_contiguous():
    """Housekeeping that has bitten this project before: a reused or skipped id makes every reference to
    it ambiguous, and the references live in code comments where nothing checks them."""
    import re
    from pathlib import Path

    doc = Path(__file__).with_name("SDK-GAPS.md")
    ids = sorted(int(n) for n in set(re.findall(r"SDK-GAP-(\d+)", doc.read_text(encoding="utf-8"))))
    assert ids == list(range(1, len(ids) + 1)), (
        f"gap ids are {ids}; they must be contiguous from 1. A retracted gap keeps its id and its "
        "entry rather than being renumbered."
    )


@pytest.mark.parametrize(
    "module_name",
    ["main", "errors", "policy", "aggregation", "idempotency_backend", "state", "gov_logging"],
)
def test_every_sdk_gap_comment_names_a_documented_gap(module_name):
    """A `# SDK-GAP-N` comment pointing at an id that does not exist is a dead reference, and these
    comments are the only thing telling a future reader why a line is shaped the way it is."""
    import importlib
    import inspect
    import re
    from pathlib import Path

    doc = Path(__file__).with_name("SDK-GAPS.md")
    documented = set(re.findall(r"SDK-GAP-(\d+)", doc.read_text(encoding="utf-8")))

    source = inspect.getsource(importlib.import_module(module_name))
    referenced = set(re.findall(r"SDK-GAP-(\d+)", source))
    assert referenced <= documented, (
        f"{module_name}.py references {sorted(referenced - documented)}, which SDK-GAPS.md does not "
        "document."
    )


def test_sdk_gap_6_target_agent_and_consultation_context_are_not_declared():
    """SDK-GAP-6 — two spec-declared `check_governance` fields the SDK does not model.

    AdCP 3.1 gives the seller a check is about its own field, `target_agent`, whose spec text says it
    "becomes the signed token audience and stays outside the business payload"; and it says a `conditions`
    verdict returns a `consultation_context` rather than a `governance_context`. Neither name exists in
    the generated models, so nothing validates them and nothing points at the right place to put them.

    That absence is what let this project put the seller in `caller` -- the field the SDK describes as
    "URL of the agent making the request" -- and read the token audience back out of it, so the buyer and
    the governance agent were consistent with each other and wrong together. Two live failures came from
    it: every first intent check returned `conditions` because the spend-aggregate key could not be
    formed, and every governed buy was rejected `PERMISSION_DENIED` because the token's `aud` was the
    invoke endpoint rather than the seller's own identity.
    """
    from adcp.types.generated_poc.governance.check_governance_request import CheckGovernanceRequest
    from adcp.types.generated_poc.governance.check_governance_response import CheckGovernanceResponse

    assert "target_agent" not in CheckGovernanceRequest.model_fields, (
        "SDK-GAP-6 (half 1) IS FIXED: `target_agent` is now declared on CheckGovernanceRequest. Read it "
        "as an ordinary attribute in `main._target_agent`, drop the `model_extra` fallback, delete "
        "`main.TARGET_AGENT_FIELD`, and update SDK-GAPS.md."
    )
    assert "consultation_context" not in CheckGovernanceResponse.model_fields, (
        "SDK-GAP-6 (half 2) IS FIXED: `consultation_context` is now declared on "
        "CheckGovernanceResponse. Set it as a real field in `handle_check_governance` and read it "
        "directly in `_audience_from_governance_context`, then update SDK-GAPS.md."
    )

    # Both must stay carryable, or the workaround itself stops working.
    assert CheckGovernanceRequest.model_config.get("extra") == "allow"
    assert CheckGovernanceResponse.model_config.get("extra") == "allow"
