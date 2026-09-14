"""Live probe: both routes into suspension, against the deployed governance agent.

Proves the scenario works before anyone is told to try it from chat. Calls the governance agent directly
over MCP so it tests the AGENT's behaviour independently of whether the buyer's LLM chooses the right
arguments -- those are two separate failures and this isolates the first.

Route 1: plan.human_review_required = true
Route 2: plan.custom_policies[].requires_human_review = true  (agent derives the flag)

Both must SUSPEND the plan, and the suspension must refuse in all three governance modes. Mode is the
agent's own runtime config, so this cannot set it -- it asserts refusal under whatever mode is deployed
and says which one that was.

Uses run-unique plan ids: suspension is sticky across a re-sync by design, so a reused id would carry
state from the previous run and could pass for the wrong reason.
"""

from __future__ import annotations

import asyncio
import json
import urllib.parse
import uuid

from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

load_dotenv()

from auth import get_test_user_access_token  # noqa: E402
from governance_agents import resolve_governance_agent  # noqa: E402

RUN = uuid.uuid4().hex[:8]
PLAN_FLAG = f"probe-hrr-flag-{RUN}"
PLAN_POLICY = f"probe-hrr-policy-{RUN}"

PLAN_BASE = {
    "brand": {"domain": f"probe-{RUN}.example"},
    "objectives": "Probe the human-review suspension path.",
    "budget": {"total": 80000.0, "currency": "USD", "reallocation_threshold": 8000.0},
    "flight": {"start": "2026-12-05T00:00:00Z", "end": "2026-12-20T23:59:59Z"},
    "channels": {"allowed": ["streaming_audio"]},
}


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


async def _sync(session, plan: dict) -> dict:
    return _payload(
        await session.call_tool(
            "sync_plans",
            {"idempotency_key": f"probe-sync-{uuid.uuid4().hex[:16]}", "plans": [plan]},
        )
    )


async def _check(session, plan_id: str) -> dict:
    return _payload(
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


def _error_codes(response: dict) -> list[str]:
    """Codes from BOTH of AdCP's error layers, since an agent may populate either."""
    codes = [e.get("code") for e in (response.get("errors") or []) if isinstance(e, dict)]
    envelope = response.get("adcp_error")
    if isinstance(envelope, dict) and envelope.get("code"):
        codes.append(envelope["code"])
    return [c for c in codes if c]


async def main() -> int:
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

            for label, plan in (
                ("route 1: human_review_required", {**PLAN_BASE, "plan_id": PLAN_FLAG, "human_review_required": True}),
                (
                    "route 2: custom_policies[].requires_human_review",
                    {
                        **PLAN_BASE,
                        "plan_id": PLAN_POLICY,
                        "custom_policies": [
                            {
                                "policy_id": "eu-ai-act-annex-iii",
                                "enforcement": "must",
                                "policy": "Credit-product advertising requires human oversight.",
                                "requires_human_review": True,
                            }
                        ],
                    },
                ),
            ):
                plan_id = plan["plan_id"]
                print(f"\n=== {label}  ({plan_id})")

                synced = await _sync(session, plan)
                first = (synced.get("plans") or [{}])[0]
                print(f"  sync status   : {first.get('status')}  (this reports the SYNC, not the campaign)")
                resolved = first.get("resolved_policies") or []
                print(f"  resolved_policies: {[p.get('policy_id') for p in resolved]}")
                if first.get("status") != "active":
                    failures.append(f"{plan_id}: sync status was {first.get('status')!r}")

                checked = await _check(session, plan_id)
                codes = _error_codes(checked)
                print(f"  check verdict : {checked.get('verdict')}")
                print(f"  error codes   : {codes}")
                print(f"  mode          : {checked.get('mode')}")
                print(f"  gov context   : {'issued (WRONG)' if checked.get('governance_context') else 'none (correct)'}")

                if "CAMPAIGN_SUSPENDED" not in codes:
                    failures.append(f"{plan_id}: expected CAMPAIGN_SUSPENDED, got {codes!r}")
                if checked.get("governance_context"):
                    failures.append(f"{plan_id}: a refusal must not issue a governance_context")

                audit = _payload(
                    await session.call_tool(
                        "get_plan_audit_logs", {"plan_ids": [plan_id], "include_entries": True}
                    )
                )
                ap = (audit.get("plans") or [{}])[0]
                summary = ap.get("summary") or {}
                escalations = summary.get("escalations") or []
                print(f"  audit status  : {ap.get('status')}")
                print(f"  escalations   : {len(escalations)}")
                for e in escalations:
                    print(f"      reason    : {e.get('reason')}")
                    print(f"      resolution: {e.get('resolution')}")
                drift = summary.get("drift_metrics") or {}
                print(f"  escalation_rate  : {drift.get('escalation_rate')}")
                print(f"  human_reviewed   : {(summary.get('statuses') or {}).get('human_reviewed')}")

                if ap.get("status") != "suspended":
                    failures.append(f"{plan_id}: audit status was {ap.get('status')!r}, expected suspended")
                if not escalations:
                    failures.append(f"{plan_id}: no escalation recorded")

            # Route 2 must name the POLICY as the cause; route 1 names the plan's own declaration. That
            # difference is the whole reason both routes are worth having.
            print("\n=== resolve these with:")
            print("  cd agents/governance/reference-governance/app/adcpRefGovernance")
            for p in (PLAN_FLAG, PLAN_POLICY):
                print(f"  uv run python resolve_escalation.py {p}")

    print()
    if failures:
        for f in failures:
            print(f"  FAIL  {f}")
        return 1
    print("Both suspension routes work against the deployed agent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
