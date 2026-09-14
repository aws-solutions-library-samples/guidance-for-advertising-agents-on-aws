"""Live probe: a human's out-of-band resolution is visible to the buyer, and the plan transacts again.

The second half of the suspension scenario, and the assertion RQ3 rests on. `resolve_escalation.py` writes
to DynamoDB directly and sends no notification, so the ONLY way the buyer (and therefore the journey UI)
can learn a human answered is by reading the audit log it already reads. This proves it can.

Usage:  .venv/bin/python3 probe_resolution_visible.py <plan_id>
"""

from __future__ import annotations

import asyncio
import json
import sys
import urllib.parse
import uuid

from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

load_dotenv()

from auth import get_test_user_access_token  # noqa: E402
from governance_agents import resolve_governance_agent  # noqa: E402


def _mcp_url(agent: dict) -> str:
    url = agent.get("url") or agent.get("mcp_url") or ""
    if url.startswith("arn:"):
        return (
            "https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/"
            f"{urllib.parse.quote(url, safe='')}/invocations"
        )
    return url


def _payload(result) -> dict:
    return json.loads(result.content[0].text) if result.content else {}


async def main(plan_id: str) -> int:
    agent = resolve_governance_agent()
    token = get_test_user_access_token()
    failures: list[str] = []

    async with streamablehttp_client(_mcp_url(agent), headers={"Authorization": f"Bearer {token}"}) as (
        read,
        write,
        _,
    ):
        async with ClientSession(read, write) as session:
            await session.initialize()

            audit = _payload(
                await session.call_tool(
                    "get_plan_audit_logs", {"plan_ids": [plan_id], "include_entries": True}
                )
            )
            plan = (audit.get("plans") or [{}])[0]
            summary = plan.get("summary") or {}
            statuses = summary.get("statuses") or {}
            drift = summary.get("drift_metrics") or {}
            escalations = summary.get("escalations") or []

            print(f"=== audit for {plan_id}")
            print(f"  status              : {plan.get('status')}")
            print(f"  statuses            : {statuses}")
            print(f"  human_override_rate : {drift.get('human_override_rate')}")
            print(f"  auto_approval_rate  : {drift.get('auto_approval_rate')}")
            print(f"  escalation_rate     : {drift.get('escalation_rate')}")
            for e in escalations:
                print(f"  escalation          : {e.get('check_id')}")
                print(f"    reason            : {e.get('reason')}")
                print(f"    resolution        : {e.get('resolution')}")
                print(f"    resolved_at       : {e.get('resolved_at')}")

            if plan.get("status") != "active":
                failures.append(f"status is {plan.get('status')!r}; resolving the last escalation should clear suspension")
            if not escalations or not escalations[0].get("resolution"):
                failures.append("the escalation carries no resolution, so the UI cannot show one")
            if not escalations or not escalations[0].get("resolved_at"):
                failures.append("the escalation carries no resolved_at")
            if statuses.get("human_reviewed") != 1:
                failures.append(f"human_reviewed is {statuses.get('human_reviewed')!r}, expected 1")
            # The agent recommended `denied` and the human approved, so this is an override. 0.0 here
            # would mean the recommendation was overwritten in place -- the metric would then report
            # perfect calibration for every plan a human ever reversed.
            if drift.get("human_override_rate") != 1.0:
                failures.append(
                    f"human_override_rate is {drift.get('human_override_rate')!r}, expected 1.0 -- the "
                    "agent's pre-review recommendation was not preserved"
                )

            # And the plan must actually transact again.
            checked = _payload(
                await session.call_tool(
                    "check_governance",
                    {
                        "plan_id": plan_id,
                        "caller": "https://reference-buyer.example/adcp",
                        "tool": "create_media_buy",
                        "payload": {
                            "budget": {"total": 50000.0, "currency": "USD"},
                            "start_date": "2026-12-05",
                            "end_date": "2026-12-20",
                            "channels": ["streaming_audio"],
                            "seller": "https://triton-seller.example/adcp/mcp",
                        },
                    },
                )
            )
            codes = [e.get("code") for e in (checked.get("errors") or []) if isinstance(e, dict)]
            print(f"\n=== re-check after resolution")
            print(f"  verdict         : {checked.get('verdict')}")
            print(f"  error codes     : {codes or 'none'}")
            print(f"  gov context     : {'issued' if checked.get('governance_context') else 'none'}")

            if "CAMPAIGN_SUSPENDED" in codes:
                failures.append("still refusing with CAMPAIGN_SUSPENDED after resolution")
            if checked.get("verdict") != "approved":
                failures.append(f"verdict after resolution is {checked.get('verdict')!r}, expected approved")
            if not checked.get("governance_context"):
                failures.append("an approved check must issue a governance_context")

    print()
    if failures:
        for f in failures:
            print(f"  FAIL  {f}")
        return 1
    print("Resolution is visible to the buyer and the plan transacts again.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(asyncio.run(main(sys.argv[1])))
