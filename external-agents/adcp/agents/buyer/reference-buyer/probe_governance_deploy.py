"""One-shot probe: is the governance-sdk-compliance feature actually serving?

Not part of the pytest suite. It answers one question a green `verify_governance_journey_live.py` cannot:
**that run passes against the PREVIOUS build too**, because it only exercises `sync_governance` and
`sync_plans`, which both existed before. This calls surfaces the feature ADDED, so a pass here is evidence
the new image is actually serving rather than that the deploy command exited 0.

Kept rather than deleted after the 2026-09-01 deploy: "the runtime reports READY" and "the new code is
running" are different claims, and this is the cheapest way to tell them apart on any future governance
deploy. `get_adcp_capabilities` is the sharpest single probe — the previous build answered it
`NOT_IMPLEMENTED`.
"""

from __future__ import annotations

import asyncio
import json
import urllib.parse

from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

load_dotenv()

from auth import get_test_user_access_token  # noqa: E402
from governance_agents import resolve_governance_agent  # noqa: E402


def _mcp_url(agent: dict) -> str:
    """The governance agent's MCP endpoint, as the buyer already resolves it."""
    url = agent.get("url") or agent.get("mcp_url") or ""
    if "bedrock-agentcore" in url and "/invocations" not in url and url.startswith("arn:"):
        encoded = urllib.parse.quote(url, safe="")
        return f"https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/{encoded}/invocations"
    return url


async def main() -> int:
    agent = resolve_governance_agent()
    url = _mcp_url(agent)
    token = get_test_user_access_token()
    print(f"governance endpoint: {url[:90]}...")

    headers = {"Authorization": f"Bearer {token}"}
    failures: list[str] = []

    async with streamablehttp_client(url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print(f"\ntools advertised ({len(names)}): {', '.join(names)}")

            # --- M9: the capability declaration. Absent on the previous build entirely.
            result = await session.call_tool("get_adcp_capabilities", {})
            payload = json.loads(result.content[0].text) if result.content else {}
            governance = (payload or {}).get("governance") or {}
            window = governance.get("aggregation_window_days")
            adcp = (payload or {}).get("adcp") or {}
            idem = adcp.get("idempotency") or {}

            print("\n=== M9 / capabilities ===")
            print(f"  aggregation_window_days : {window}")
            print(f"  supported_protocols     : {payload.get('supported_protocols')}")
            print(f"  supported_versions      : {adcp.get('supported_versions')}")
            print(f"  idempotency.supported   : {idem.get('supported')}")
            print(f"  replay_ttl_seconds      : {idem.get('replay_ttl_seconds')}")

            if window != 30:
                failures.append(f"aggregation_window_days is {window!r}, expected 30")
            if payload.get("supported_protocols") != ["governance"]:
                failures.append(f"supported_protocols is {payload.get('supported_protocols')!r}")
            if idem.get("supported") is not True:
                failures.append("idempotency.supported is not true")

            # --- M4 / SDK-GAP-4: the rewritten audit response. An unsynced plan must be OMITTED,
            # not filled in, so an empty `plans` here is the correct answer and proves the handler ran.
            audit = await session.call_tool(
                "get_plan_audit_logs",
                {"plan_ids": ["probe-plan-does-not-exist"], "include_entries": True},
            )
            audit_payload = json.loads(audit.content[0].text) if audit.content else {}
            print("\n=== M4 / audit log (unknown plan) ===")
            print(f"  plans   : {audit_payload.get('plans')}")
            print(f"  message : {audit_payload.get('message')}")
            if audit_payload.get("plans") != []:
                failures.append(f"expected plans == [] for an unsynced plan, got {audit_payload.get('plans')!r}")

    print()
    if failures:
        for f in failures:
            print(f"  FAIL  {f}")
        return 1
    print("All probes passed: the new build is serving.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
