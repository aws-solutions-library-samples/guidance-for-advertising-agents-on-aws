"""Close a governance escalation. The operator surface for a human review.

## Why this is a script and not an AdCP task

`CAMPAIGN_SUSPENDED`'s own recovery hint is *"contact the plan operator"*. AdCP deliberately places the
human **outside** the protocol: there is no task a buyer or seller can call to un-suspend a plan, because
a party that could clear its own suspension would not be suspended in any meaningful sense.

So this script **registers no MCP tool and is imported by nothing in the runtime**. Grep for it: the only
references should be this file, its tests, and documentation. If a handler ever imports it, that is the
bug — the review boundary has been moved inside the protocol.

## What it does, and what the buyer sees afterwards

Resolving an escalation:

1. records the human's `resolution` and `resolved_at` on the escalation;
2. applies the resolved verdict to the escalated check, **preserving the agent's original recommendation**
   as `recommended_verdict` (see `state._apply_resolved_verdict` — without that, `human_override_rate`
   would report perfect calibration for every plan a human ever reversed);
3. clears the plan's `suspended` flag, but **only when no other escalation is outstanding**.

All three are visible to the buyer on the next `get_plan_audit_logs` with no extra plumbing: `status`
returns to `active`, `summary.escalations[]` carries the resolution and its timestamp,
`statuses.human_reviewed` counts the check, and `drift_metrics.human_override_rate` becomes computable.
That is what makes the journey UI update once a resolution happens — it reads the audit response it
already reads.

## Vocabulary

`approved_by_human` and `rejected_by_human` are AdCP's own examples, from the `Escalation.resolution`
field description. They are not invented here, and they are the two values
`main._human_override_rate` can classify — a resolution outside this pair is recorded verbatim but left
unclassified rather than guessed at, and it does not change the check's verdict.

Run with `--apply` to make a change. Without it, everything below is read-only, matching
`deploy_state_table.py`'s convention: a state change to a governed plan should take a deliberate flag.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

import state

#: The resolutions this agent understands. Reused from `state` rather than restated, so the script and the
#: metric cannot disagree about which strings mean what.
RESOLUTIONS = tuple(state._RESOLVED_VERDICTS)


def _iso(epoch_seconds: int | None) -> str:
    if not epoch_seconds:
        return "-"
    return datetime.fromtimestamp(int(epoch_seconds), tz=timezone.utc).isoformat()


def list_escalations(plan_id: str) -> int:
    """Print every escalation on a plan, outstanding first."""
    escalations = state.list_escalations(plan_id)
    if not escalations:
        print(f"Plan {plan_id}: no escalations recorded.")
        # Not an error. "No escalations" and "no such plan" are different, so say which.
        if state.get_plan(plan_id) is None:
            print("  (this plan has never been synced, so there is nothing to review)")
        return 0

    suspended = state.is_suspended(plan_id)
    print(f"Plan {plan_id}: {'SUSPENDED' if suspended else 'not suspended'}")
    verdicts = {c["check_id"]: c for c in state.list_checks(plan_id)}

    outstanding = [e for e in escalations if not e.get("resolved_at")]
    resolved = [e for e in escalations if e.get("resolved_at")]

    for label, group in (("OUTSTANDING", outstanding), ("resolved", resolved)):
        if not group:
            continue
        print(f"\n  {label} ({len(group)}):")
        for escalation in sorted(group, key=lambda e: e.get("created_at", 0)):
            check = verdicts.get(escalation.get("check_id", "")) or {}
            print(f"    check_id   : {escalation.get('check_id')}")
            print(f"      opened   : {_iso(escalation.get('created_at'))}")
            print(f"      reason   : {escalation.get('reason')}")
            # Both, when they differ: the agent's recommendation is what a reviewer is being asked to
            # agree or disagree with, and it is gone from `verdict` once a resolution is applied.
            recommended = check.get("recommended_verdict")
            if recommended:
                print(f"      agent    : recommended {recommended}")
                print(f"      verdict  : {check.get('verdict')} (after review)")
            else:
                print(f"      agent    : recommended {check.get('verdict', '(no check on record)')}")
            if escalation.get("resolved_at"):
                print(f"      resolved : {escalation['resolution']} at {_iso(escalation['resolved_at'])}")
    return 0


def resolve(*, plan_id: str, check_id: str, resolution: str, apply: bool) -> int:
    if resolution not in RESOLUTIONS:
        # Refused rather than recorded. An unclassifiable resolution silently excludes itself from
        # `human_override_rate`, so accepting a typo here would quietly degrade the metric instead of
        # telling the operator.
        print(f"Unknown resolution {resolution!r}. Use one of: {', '.join(RESOLUTIONS)}")
        return 2

    escalations = {e.get("check_id"): e for e in state.list_escalations(plan_id)}
    escalation = escalations.get(check_id)
    if escalation is None:
        print(f"Plan {plan_id} has no escalation for check {check_id}.")
        print("Run without --check-id to list what is outstanding.")
        return 1
    if escalation.get("resolved_at"):
        print(
            f"Escalation {check_id} was already resolved "
            f"({escalation['resolution']} at {_iso(escalation['resolved_at'])})."
        )
        print("Re-resolving would overwrite a recorded human decision; refusing.")
        return 1

    final_verdict = state._RESOLVED_VERDICTS[resolution]
    others = [
        e for e in state.unresolved_escalations(plan_id) if e.get("check_id") != check_id
    ]

    if not apply:
        print(f"Would resolve escalation {check_id} on plan {plan_id} as {resolution!r}:")
        print(f"  - record resolution + resolved_at on the escalation")
        print(f"  - set check {check_id} verdict to {final_verdict}, keeping the agent's recommendation")
        if others:
            print(
                f"  - LEAVE the plan suspended: {len(others)} other escalation(s) still outstanding "
                f"({', '.join(str(e.get('check_id')) for e in others)})"
            )
        else:
            print("  - clear the plan's suspension (nothing else outstanding)")
        print("\nRe-run with --apply to make these changes.")
        return 0

    if not state.resolve_escalation(plan_id=plan_id, check_id=check_id, resolution=resolution):
        # Should be unreachable given the lookup above, but a concurrent resolution is possible and
        # reporting success for a write that did not happen is the one outcome worth guarding against.
        print(f"Escalation {check_id} could not be resolved; it may have been resolved concurrently.")
        return 1

    print(f"Resolved escalation {check_id} on plan {plan_id} as {resolution!r}.")
    print(f"  check {check_id} verdict is now {final_verdict}")
    if state.is_suspended(plan_id):
        print(
            f"  plan remains SUSPENDED: {len(others)} other escalation(s) outstanding "
            f"({', '.join(str(e.get('check_id')) for e in others)})"
        )
    else:
        print("  plan suspension cleared; it can transact again")
    print("\nThe buyer sees this on its next get_plan_audit_logs call. No further action is needed.")
    return 0


def _default_table_name() -> None:
    """Point `state` at the deployed table when the operator has not set the variable.

    The runtime gets `STATE_TABLE_NAME` from its AgentCore config; a human running this from a laptop has
    no reason to know that. The name is imported from `deploy_state_table`, the script that CREATES the
    table, so the two cannot drift into pointing at different tables.
    """
    import os

    if os.environ.get(state.TABLE_ENV):
        return
    from deploy_state_table import TABLE_NAME

    os.environ[state.TABLE_ENV] = TABLE_NAME
    print(f"({state.TABLE_ENV} was unset; using the deployed table {TABLE_NAME})")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "List or resolve governance escalations. Read-only unless --apply is given. "
            "This is an operator tool: AdCP has no task for resolving an escalation, deliberately."
        )
    )
    parser.add_argument("plan_id", help="the plan whose escalations to list or resolve")
    parser.add_argument(
        "--check-id",
        default=None,
        help="the escalated check to resolve. Omit to list the plan's escalations.",
    )
    parser.add_argument(
        "--resolution",
        default=None,
        choices=RESOLUTIONS,
        help="the human's decision. Required with --check-id.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually record the resolution. Without it, nothing is written.",
    )
    args = parser.parse_args()
    _default_table_name()

    if args.check_id is None:
        if args.resolution is not None:
            print("--resolution needs --check-id: a resolution applies to one escalated check.")
            return 2
        return list_escalations(args.plan_id)

    if args.resolution is None:
        print("--check-id needs --resolution. Nothing was changed.")
        return 2

    return resolve(
        plan_id=args.plan_id,
        check_id=args.check_id,
        resolution=args.resolution,
        apply=args.apply,
    )


if __name__ == "__main__":
    sys.exit(main())
