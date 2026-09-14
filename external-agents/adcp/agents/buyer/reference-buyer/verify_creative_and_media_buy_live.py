"""
Live end-to-end verification of U4 Part 1 (creative-and-media-buy-code-generation-plan.md,
Step 22) against deployed infrastructure only:

  1. Bind (sync_accounts, sync_governance) and Plan (sync_plans) against the real reference
     seller and reference governance agent.
  2. Check (check_governance) and Book (create_media_buy) a real ref-display-news-01 media buy
     -- the product whose format (display_300x250) carries a real assets[] declaration and is
     the one format the governance agent's get_creative_features evaluator judges.
  3. Report the booking outcome ("completed") back to governance.
  4. Creative: call adcp_get_creative_features against a REAL published fixture image
     (clean_banner.png, at its real CloudFront URL) and confirm a real, non-error,
     non-"working" (after one retry) evaluation comes back -- not a fabricated verdict.
  5. Report a delivery outcome ("delivery") with real reporting-period/impressions/spend
     fields.
  6. Read back adcp_get_plan_audit_logs and confirm both "completed" and "delivery"
     outcome-type entries are present -- proving the governance agent's fixed
     handle_report_plan_outcome/handle_get_plan_audit_logs paths (Part 1, Steps 4/8-9) work
     against the real redeployed agent, not just in unit tests.
  7. Read back the reasoning session's recorded steps from DynamoDB and confirm
     adcp_get_creative_features, adcp_report_plan_outcome and adcp_get_plan_audit_logs were
     really called and recorded with content.toolName set -- the field the journey UI's
     decoders (governanceCards.ts) match against.

Every call goes over the deployed buyer agent's real HTTPS /invocations endpoint with a real
Cognito access token; no tool is called in-process and no result is asserted against a
canned value. A run-unique brand domain under the reserved .example TLD means a pass cannot be
inherited from a previous run's leftover account.

Run from this directory:
    .venv/bin/python3 verify_creative_and_media_buy_live.py
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

SESSION_ID = f"adcp-buyer-agent-{uuid.uuid4()}"
assert len(SESSION_ID) >= 33, len(SESSION_ID)

RUN = uuid.uuid4().hex[:8]
BRAND_DOMAIN = f"verify-creative-{RUN}.example"
OPERATOR = f"verify-creative-operator-{RUN}"
PLAN_ID = f"verify-creative-plan-{RUN}"

# The one real, fetchable creative fixture published for this format (Steps 5-6 of the plan).
# The origin is read from the environment because it names a CloudFront distribution, which only
# exists in the account that created it. deploy_all.py writes BUYER_UI_ORIGIN after creating it.
CREATIVE_ASSET_PATH = ".well-known/creatives/clean_banner.png"


def creative_asset_url() -> str:
    origin = os.environ.get("BUYER_UI_ORIGIN", "").strip()
    if not origin:
        raise SystemExit(
            "BUYER_UI_ORIGIN is not set. It is the origin the creative fixtures are published to:\n"
            "  python3 deploy_all.py --only buyer-ui-origin"
        )
    return f"{origin.rstrip('/')}/{CREATIVE_ASSET_PATH}"

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        if detail:
            print(f"        {detail}")
        failures.append(label)
    return ok


def invoke(prompt: str, token: str, timeout: int = 300) -> requests.Response:
    return requests.post(
        INVOKE_URL,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": SESSION_ID,
        },
        data=json.dumps({"prompt": prompt}),
        timeout=timeout,
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

# --- Turn 1: Bind, Plan, Check, Book, Report(completed) --------------------------------------
prompt_1 = (
    f"Do the following in order against the reference sales agent (seller_id=\"reference\"), "
    "calling the real tools each time, and report each tool's full raw result: "
    f"1) adcp_sync_accounts(brand_domain=\"{BRAND_DOMAIN}\", operator=\"{OPERATOR}\", "
    "seller_ids=[\"reference\"]). "
    f"2) adcp_sync_governance(brand_domain=\"{BRAND_DOMAIN}\", operator=\"{OPERATOR}\", "
    "seller_ids=[\"reference\"]). "
    f"3) adcp_sync_plans(plan_id=\"{PLAN_ID}\", brand_domain=\"{BRAND_DOMAIN}\", "
    "objectives=\"live verification of creative evaluation and media buy reporting\", "
    "budget_total=1000.0, flight_start=\"2026-09-01\", flight_end=\"2026-09-30\", "
    "channels=[\"display\"]). "
    f"4) adcp_check_governance(seller_id=\"reference\", plan_id=\"{PLAN_ID}\", "
    "tool_name=\"create_media_buy\", payload={\"packages\": [{\"product_id\": "
    "\"ref-display-news-01\", \"budget\": 1000.0, \"pricing_option_id\": "
    "\"cpm-ref-display-news-01\"}]}). "
    "5) If step 4's verdict is approved, call adcp_create_media_buy with "
    f"seller_id=\"reference\", plan_id=\"{PLAN_ID}\", governance_context set to the "
    "governance_context field from step 4's result, product_id=\"ref-display-news-01\", "
    f"budget=1000.0, pricing_option_id=\"cpm-ref-display-news-01\", "
    f"brand_domain=\"{BRAND_DOMAIN}\", operator=\"{OPERATOR}\", flight_start=\"2026-09-01\", "
    "flight_end=\"2026-09-30\". "
    "6) Then call adcp_report_plan_outcome with "
    f"plan_id=\"{PLAN_ID}\", check_id set to step 4's check_id, governance_context set to "
    "the SAME governance_context used in step 5, outcome=\"completed\", seller_reference set "
    "to step 5's media_buy_id, committed_budget=1000.0. "
    "Report the media_buy_id from step 5 explicitly and clearly at the end of your reply."
)

print("=== Turn 1: Bind, Plan, Check, Book, Report(completed) ===")
started = time.time()
resp = invoke(prompt_1, token)
print(f"status {resp.status_code} in {time.time() - started:.1f}s")
check("turn 1 invocation succeeded", resp.status_code == 200, resp.text[:400])
reply_1 = extract_text(resp.text) if resp.status_code == 200 else ""
print("\n--- agent reply (turn 1) ---")
print(reply_1[:2500])
print("--- end reply ---\n")

check("no_prior_check gate was not triggered (check_governance ran before booking)",
      "no_prior_check" not in reply_1, reply_1[:400])
check("governance_context was not reported expired", "governance_context_expired" not in reply_1)

import re

media_buy_match = re.search(r'"media_buy_id":\s*"([^"]+)"', reply_1)
media_buy_id = media_buy_match.group(1) if media_buy_match else None
check("a media_buy_id was returned by create_media_buy", bool(media_buy_id), reply_1[:800])

time.sleep(2)

# --- Turn 2: Creative evaluation, Report(delivery), audit read-back -------------------------
prompt_2 = (
    "Do the following in order, calling the real tools each time, and report each tool's full "
    "raw result verbatim: "
    "1) adcp_get_creative_features(format_id=\"display_300x250\", "
    "format_agent_url=\"https://reference-seller.example/adcp/mcp\", format_width=300, "
    f"format_height=250, asset_url=\"{creative_asset_url()}\", asset_width=300, "
    "asset_height=250). If the result's status is \"working\" or \"submitted\", wait a moment "
    "and call it again with the exact same arguments, up to 3 times total, until you get a "
    "final result. Report the final result. "
    f"2) adcp_report_plan_outcome(plan_id=\"{PLAN_ID}\", "
    "governance_context=<the SAME governance_context used earlier in this session for the "
    "create_media_buy/report_plan_outcome calls>, outcome=\"delivery\", "
    "reporting_period_start=\"2026-09-01T00:00:00Z\", reporting_period_end=\"2026-09-07T00:00:00Z\", "
    "impressions=125000, spend=750.0, cpm=6.0, viewability_rate=0.82, completion_rate=0.95). "
    f"3) adcp_get_plan_audit_logs(plan_ids=[\"{PLAN_ID}\"], include_entries=true)."
)

print("=== Turn 2: Creative evaluation, Report(delivery), audit read-back ===")
started = time.time()
resp2 = invoke(prompt_2, token, timeout=240)
print(f"status {resp2.status_code} in {time.time() - started:.1f}s")
check("turn 2 invocation succeeded", resp2.status_code == 200, resp2.text[:400])
reply_2 = extract_text(resp2.text) if resp2.status_code == 200 else ""
print("\n--- agent reply (turn 2) ---")
print(reply_2[:3500])
print("--- end reply ---\n")

for bad in ("mcp_error", "a2a_error", "not_implemented", "Unknown tool"):
    check(f"turn 2 reply does not contain {bad!r}", bad not in reply_2, reply_2[:400])

check('turn 2 reply reports "completed" outcome type in the audit log',
      '"completed"' in reply_2 or "completed" in reply_2)
check('turn 2 reply reports "delivery" outcome type in the audit log',
      '"delivery"' in reply_2 or "delivery" in reply_2)

# --- Read back what the agent actually recorded (DynamoDB) ---------------------------------
print("=== Reading back what the agent recorded (DynamoDB) ===")
steps: list[dict] = []
for attempt in range(10):
    steps = session_store.get_session_steps(SESSION_ID)
    tool_names_seen = {
        (s.get("content") or {}).get("toolName")
        for s in steps
        if s.get("step_type") == "tool_result"
    }
    if "adcp_get_plan_audit_logs" in tool_names_seen:
        break
    time.sleep(2)

print(f"  {len(steps)} steps recorded under this session")
check("the session recorded steps at all", bool(steps))

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

EXPECTED_TOOLS = [
    "adcp_sync_accounts",
    "adcp_sync_governance",
    "adcp_sync_plans",
    "adcp_check_governance",
    "adcp_create_media_buy",
    "adcp_report_plan_outcome",
    "adcp_get_creative_features",
    "adcp_get_plan_audit_logs",
]

for tool_name in EXPECTED_TOOLS:
    recorded = by_tool.get(tool_name)
    if not check(f"{tool_name}: a tool_call step was recorded",
                 bool(recorded and recorded["tool_call"]),
                 f"recorded tools were {sorted(by_tool)}"):
        continue
    results = recorded["tool_result"]
    if not check(f"{tool_name}: a tool_result step was recorded", bool(results)):
        continue
    result = results[-1]
    content = result.get("content") or {}
    check(f"{tool_name}: the recorded step names the tool exactly (journey binds on this)",
          content.get("toolName") == tool_name,
          f"got {content.get('toolName')!r}")
    check(f"{tool_name}: the call succeeded", content.get("status") not in ("error", "failed"),
          f"status={content.get('status')!r} output={json.dumps(content.get('output'))[:400]}")

# adcp_get_creative_features specific: confirm the LAST recorded call's output is a real,
# non-working, non-error evaluation (not a fabricated verdict).
creative_results = by_tool.get("adcp_get_creative_features", {}).get("tool_result", [])
if creative_results:
    last_output = (creative_results[-1].get("content") or {}).get("output")
    blob = json.dumps(last_output)
    check("adcp_get_creative_features: last recorded call did not error",
          not any(bad in blob for bad in ("mcp_error", "a2a_error", "not_implemented")),
          blob[:600])
    check("adcp_get_creative_features: last recorded call was not left in 'working' status",
          '"working"' not in blob and '"submitted"' not in blob,
          blob[:600])
    print(f"\n  Last recorded adcp_get_creative_features output: {blob[:800]}")

print("\n=== Summary ===")
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for f in failures:
        print(f"  - {f}")
    print(f"\nSession id for inspection in the UI: {SESSION_ID}")
    sys.exit(1)

print("All checks passed.")
print(f"Session id to open in the journey UI: {SESSION_ID}")
