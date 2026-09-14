"""
Verifies the Bind and Plan journey phases populate from recorded steps.

What this proves, end to end, against deployed infrastructure only:

  1. The deployed buyer agent really calls `adcp_sync_accounts`,
     `adcp_sync_governance` and `adcp_sync_plans` -- the three tools the
     journey's Bind and Plan panels bind to.
  2. Each call reached a real counterparty and came back without an error:
     sync_accounts and sync_governance to the SELLERS, sync_plans to the
     GOVERNANCE agent. Different parties, so a pass here is evidence about
     both hops.
  3. The reasoning recorder wrote real `tool_call` / `tool_result` steps to
     DynamoDB with `content.toolName` set to those tool names, which is the
     exact field `ui/src/lib/journey` matches a phase against. If this passes,
     the panels have data to render; if the tool names drifted, the panels
     would go dark and this fails rather than the UI quietly reporting an
     absence that looks intentional.

The agent is invoked over its HTTPS `/invocations` endpoint with a Cognito
access token, and every assertion reads back what the agent recorded. If a
hop fails, the script reports the failure.

Why an invocation rather than calling the tools directly: the thing under
test is that the *deployed* agent chooses and records these tools. Calling
the Python functions in-process would exercise none of the runtime, the
authorizer, the recorder, or the tool registration.

The account is declared with a run-unique brand domain so a pass cannot be
inherited from a previous run's leftovers, and so the run never mutates a
brand anyone might mistake for real.

Run from this directory:
    .venv/bin/python verify_governance_journey_live.py
"""


from aws_region import region
import json
import os
import sys
import time
import urllib.parse
import uuid

import requests
from dotenv import load_dotenv

import session_store
from auth import get_test_user_access_token

load_dotenv()

REGION = region()
AGENT_RUNTIME_ARN = os.environ["AGENT_RUNTIME_ARN"]

ESCAPED_ARN = urllib.parse.quote(AGENT_RUNTIME_ARN, safe="")
INVOKE_URL = (
    f"https://bedrock-agentcore.{REGION}.amazonaws.com"
    f"/runtimes/{ESCAPED_ARN}/invocations?qualifier=DEFAULT"
)

# The session id doubles as the AgentCore runtimeSessionId (33 char minimum) and as the
# DynamoDB session key the recorder writes under, so the steps read back below are
# provably this run's.
SESSION_ID = f"adcp-buyer-agent-{uuid.uuid4()}"
assert len(SESSION_ID) >= 33, len(SESSION_ID)

# Unique per run: a pass must come from work this run did, not from an account or plan a
# previous run left behind. `.example` is the reserved documentation TLD, so this can never
# collide with a real advertiser.
RUN = uuid.uuid4().hex[:8]
BRAND_DOMAIN = f"verify-journey-{RUN}.example"
OPERATOR = f"verify-journey-operator-{RUN}"
PLAN_ID = f"verify-journey-plan-{RUN}"

# The three tools the Bind and Plan panels bind to, in the order the dependency actually
# runs: an account must exist at the seller before a governance agent can be bound to it.
EXPECTED_TOOLS = ["adcp_sync_accounts", "adcp_sync_governance", "adcp_sync_plans"]

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        if detail:
            print(f"        {detail}")
        failures.append(label)
    return ok


def invoke(prompt: str, token: str) -> requests.Response:
    return requests.post(
        INVOKE_URL,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": SESSION_ID,
        },
        data=json.dumps({"prompt": prompt}),
        # Three sequential tool calls, each a network round trip to another agent, plus model
        # time. The generous ceiling is so a slow-but-working run is not reported as a failure.
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


print(f"Session: {SESSION_ID}")
print(f"Brand:   {BRAND_DOMAIN}   operator: {OPERATOR}   plan: {PLAN_ID}")

token = get_test_user_access_token()
print(f"Got a real Cognito access token ({len(token)} chars).\n")

# The prompt states the facts the tools need and nothing else. It does not name the tools:
# whether the agent picks the right ones from its own instructions is part of what is under
# test, and spelling them out would test only that it can follow a script.
prompt = (
    f"Set up governance for a new campaign. The advertiser's brand domain is "
    f"{BRAND_DOMAIN} and the operator declaring the account is {OPERATOR}. "
    f"Declare that account at the reference sales agent, register our governance agent "
    f"against it there, then register the campaign plan with the governance agent using "
    f"plan id {PLAN_ID}: objectives \"reach commuters on streaming audio\", total budget "
    f"50000 USD, flight 2026-09-01 to 2026-09-30, authorised channel streaming_audio. "
    f"Use only the reference sales agent. Report exactly what each call returned."
)

print("=== Invoking the deployed buyer agent ===")
started = time.time()
resp = invoke(prompt, token)
print(f"status {resp.status_code} in {time.time() - started:.1f}s")
check("the deployed runtime accepted the authenticated invocation", resp.status_code == 200,
      resp.text[:400])
if resp.status_code == 200:
    print("\n--- agent reply ---")
    print(extract_text(resp.text)[:2500])
    print("--- end reply ---\n")

# The recorder writes steps during the turn, so they are already durable by the time the
# response completes. A short settle loop covers DynamoDB read latency only -- it does not
# retry the work, and giving up here is reported as a failure rather than passed over.
print("=== Reading back what the agent recorded (DynamoDB) ===")
steps: list[dict] = []
for attempt in range(10):
    steps = session_store.get_session_steps(SESSION_ID)
    if any(s.get("step_type") == "tool_result" for s in steps):
        break
    time.sleep(2)

print(f"  {len(steps)} steps recorded under this session")
check("the session recorded steps at all", bool(steps),
      "no steps found -- the recorder did not run, or wrote under a different session id")

by_tool: dict[str, dict[str, list[dict]]] = {}
for step in steps:
    kind = step.get("step_type")
    if kind not in ("tool_call", "tool_result"):
        continue
    name = (step.get("content") or {}).get("toolName")
    if not name:
        continue
    by_tool.setdefault(name, {"tool_call": [], "tool_result": []})[kind].append(step)

print(f"  tools recorded: {sorted(by_tool) or '(none)'}")

for tool in EXPECTED_TOOLS:
    recorded = by_tool.get(tool)
    if not check(f"{tool}: a tool_call step was recorded",
                 bool(recorded and recorded['tool_call']),
                 f"recorded tools were {sorted(by_tool)}"):
        continue
    results = recorded["tool_result"]
    if not check(f"{tool}: a tool_result step was recorded", bool(results)):
        continue

    # `content.toolName` is the field the journey registry matches on. Asserting it is present
    # and exact is the whole point: a prefix drift here is what once emptied every panel while
    # leaving the screen looking merely unfinished.
    result = results[-1]
    content = result.get("content") or {}
    check(f"{tool}: the recorded step names the tool exactly (journey binds on this)",
          content.get("toolName") == tool,
          f"got {content.get('toolName')!r}")
    check(f"{tool}: the call succeeded", content.get("status") not in ("error", "failed"),
          f"status={content.get('status')!r} output={json.dumps(content.get('output'))[:400]}")

    # A tool that returned an AdCP error payload is a failure of the hop even when the tool
    # itself "succeeded" -- ACCOUNT_NOT_FOUND from sync_governance is the case this catches.
    blob = json.dumps(content.get("output") or content)
    for code in ("ACCOUNT_NOT_FOUND", "VALIDATION_ERROR", "Unknown tool", "not_implemented"):
        check(f"{tool}: the counterparty did not answer {code}", code not in blob,
              blob[:400])

print("\n=== Summary ===")
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for f in failures:
        print(f"  - {f}")
    print(f"\nSession id for inspection in the UI: {SESSION_ID}")
    sys.exit(1)

print("All checks passed.")
print("Bind (sync_governance) and Plan (sync_plans) have real recorded steps to render.")
print(f"Session id to open in the journey UI: {SESSION_ID}")
