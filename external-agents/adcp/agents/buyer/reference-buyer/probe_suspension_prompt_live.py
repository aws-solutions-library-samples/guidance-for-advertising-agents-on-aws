"""Live probe: can the deployed buyer's MODEL reach suspension from natural language?

`probe_suspension_live.py` proves the governance AGENT suspends when asked. This proves the other half,
which is a different failure: whether the buyer's LLM populates `human_review_required` /
`custom_policies` from a prompt that never names them. A tool the model cannot fill is no better than a
tool that does not exist.

Deliberately does NOT name the parameters or the tools. The prompt states the campaign's regulatory
character in a user's own words; picking the right argument from that is exactly what is under test, and
spelling it out would only prove the model can follow a script.

Asserts against the GOVERNANCE AGENT's own state rather than the model's prose, because a model that says
"I have flagged it for review" while sending nothing is the failure mode that matters.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import uuid

import requests
from dotenv import load_dotenv

load_dotenv()

from auth import get_test_user_access_token  # noqa: E402

RUN = uuid.uuid4().hex[:8]
SESSION_ID = f"probe-hrr-prompt-{uuid.uuid4()}"
BRAND_DOMAIN = f"probe-{RUN}.example"
OPERATOR = f"probe-op-{RUN}"
PLAN_ID = f"probe-prompt-{RUN}"

# Same env var and URL shape `verify_governance_journey_live.py` uses, including the `qualifier`.
ARN = os.environ["AGENT_RUNTIME_ARN"]
INVOKE_URL = (
    "https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/"
    f"{urllib.parse.quote(ARN, safe='')}/invocations?qualifier=DEFAULT"
)


def invoke(prompt: str, token: str) -> requests.Response:
    return requests.post(
        INVOKE_URL,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": SESSION_ID,
        },
        data=json.dumps({"prompt": prompt}),
        timeout=300,
    )


def extract_text(body: str) -> str:
    out = []
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload:
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "text":
            out.append(event["data"])
        elif event.get("type") == "error":
            print("  ERROR EVENT:", event)
    return "".join(out)


token = get_test_user_access_token()
print(f"Session: {SESSION_ID}")
print(f"Brand:   {BRAND_DOMAIN}   operator: {OPERATOR}   plan: {PLAN_ID}\n")

prompt = (
    f"Set up governance for a new campaign. The advertiser's brand domain is {BRAND_DOMAIN} "
    f"and the operator declaring the account is {OPERATOR}. Declare that account at the "
    f"reference sales agent, register our governance agent against it there, then register the "
    f"campaign plan with the governance agent using plan id {PLAN_ID}: objectives \"reach "
    f"commuters on streaming audio\", total budget 50000 USD, flight 2026-09-01 to 2026-09-30, "
    f"authorised channel streaming_audio. "
    # The only hint. No parameter named, no tool named -- this is a fact about the campaign, which is
    # how a user would actually say it.
    f"This is advertising for a consumer credit product, so under GDPR Article 22 no decision on it "
    f"may be made by automation alone -- a person has to sign off before anything proceeds. "
    f"Use only the reference sales agent. Report exactly what each call returned."
)

print("=== Invoking the deployed buyer agent ===")
started = time.time()
resp = invoke(prompt, token)
print(f"status {resp.status_code} in {time.time() - started:.1f}s\n")
text = extract_text(resp.text)
print("--- agent said (last 1200 chars) ---")
print(text[-1200:])

# --- What the governance agent actually recorded. The only evidence that counts.
print("\n=== Reading the governance agent's own state ===")
import asyncio  # noqa: E402

from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamablehttp_client  # noqa: E402

from governance_agents import resolve_governance_agent  # noqa: E402


async def read_plan() -> dict:
    agent = resolve_governance_agent()
    url = agent.get("url") or agent.get("mcp_url") or ""
    if url.startswith("arn:"):
        url = (
            "https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/"
            f"{urllib.parse.quote(url, safe='')}/invocations"
        )
    async with streamablehttp_client(url, headers={"Authorization": f"Bearer {token}"}) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool(
                "get_plan_audit_logs", {"plan_ids": [PLAN_ID], "include_entries": True}
            )
            return json.loads(res.content[0].text) if res.content else {}


audit = asyncio.run(read_plan())
plans = audit.get("plans") or []
failures: list[str] = []

if not plans:
    failures.append(
        f"the governance agent has no plan {PLAN_ID!r} at all -- the model did not register it, so "
        "this probe cannot say anything about the review flag"
    )
else:
    plan = plans[0]
    summary = plan.get("summary") or {}
    escalations = summary.get("escalations") or []
    print(f"  plan status : {plan.get('status')}")
    print(f"  escalations : {len(escalations)}")
    for e in escalations:
        print(f"      reason  : {e.get('reason')}")

    if plan.get("status") != "suspended":
        failures.append(
            f"plan status is {plan.get('status')!r}, expected 'suspended'. The model registered the "
            "plan but did not carry the human-review requirement into it."
        )
    if not escalations:
        failures.append("no escalation was recorded, so nothing is awaiting review")

print()
if failures:
    for f in failures:
        print(f"  FAIL  {f}")
    raise SystemExit(1)
print("The model reached suspension from natural language. The scenario is promptable.")
