"""
Live verification of U3 `seller-governance` (Code Generation plan Step 16): the deployed
reference seller must actually run its OWN governance verification code
(`governance_verification.py`, wired into `main.py::create_media_buy`) against the real deployed
reference governance agent -- not merely rely on the buyer's own U2 gate to keep bad requests from
arriving.

Two checks, deliberately different in what they can prove:

1. **Positive, buyer-mediated.** Drive the full
   `sync_accounts -> sync_governance -> sync_plans -> check_governance -> create_media_buy`
   sequence through the deployed buyer agent (already proven correct for U2's own gate in
   `verify_governance_gate_live.py`), then read this SELLER's own DynamoDB table directly and
   confirm a `GOVJTI#<jti>` item was written. That item can only exist if `jti_first_seen` ran
   inside `verify_intent_token`, on the seller side -- proof this seller's new code path executed,
   which the buyer-mediated success alone does not prove (U2's gate would already refuse an
   improperly-checked request before it ever reached this seller, so a buyer-mediated success is
   consistent with U3's seller code being entirely absent).

2. **Negative, buyer-bypassed.** Call the seller directly over its own MCP endpoint (a raw
   `streamablehttp_client`, no buyer agent involved) with a `create_media_buy` request carrying a
   syntactically-garbage `governance_context` against a governed account. This is the one behavior
   the buyer-mediated path cannot exercise: the buyer's own U2 gate would refuse to send a garbage
   token in the first place, so only a direct call proves the SELLER independently refuses one too,
   without ever calling the governance agent (confirmed by asserting no new `GOVJTI#` item appears
   for this attempt -- `jti_first_seen` is only reached after signature verification already
   passed).

Not part of the pytest suite (test_governance_verification.py / test_main.py's
TestCreateMediaBuyGovernance already cover the logic directly, without a network hop) -- a one-shot
deploy verification script, matching this project's established `verify_*_live.py` convention. Run
from this directory:

    .venv/bin/python3 verify_seller_governance_gate_live.py
"""

import asyncio
import json
import os
import sys
import time
import urllib.parse
import uuid

import boto3
import requests
from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from aws_region import region

load_dotenv()

# This seller's own env (STATE_TABLE_NAME, SELLER_RUNTIME_ARN) plus Cognito discovery/client_id --
# see this file's own .env, same directory.
STATE_TABLE_NAME = os.environ["STATE_TABLE_NAME"]
SELLER_RUNTIME_ARN = os.environ["SELLER_RUNTIME_ARN"]
REGION = region()
# The buyer agent's own directory holds the Cognito test-user credentials and the deployed HTTP
# runtime ARN this script drives for the positive, buyer-mediated check. Same cross-package reuse
# pattern deploy_registry_registration.py already uses for the same reason.
_BUYER_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "..", "buyer", "reference-buyer"
)
sys.path.insert(0, os.path.abspath(_BUYER_DIR))
load_dotenv(os.path.join(_BUYER_DIR, ".env"))
from auth import get_test_user_access_token  # noqa: E402  (path set up above)

BUYER_AGENT_RUNTIME_ARN = os.environ["AGENT_RUNTIME_ARN"]

_ESCAPED_SELLER_ARN = urllib.parse.quote(SELLER_RUNTIME_ARN, safe="")
SELLER_INVOKE_URL = (
    f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/{_ESCAPED_SELLER_ARN}"
    "/invocations?qualifier=DEFAULT"
)

_ESCAPED_BUYER_ARN = urllib.parse.quote(BUYER_AGENT_RUNTIME_ARN, safe="")
BUYER_INVOKE_URL = (
    f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/{_ESCAPED_BUYER_ARN}"
    "/invocations?qualifier=DEFAULT"
)

PASS = []
FAIL = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASS.append(label)
        print(f"PASS: {label}")
    else:
        FAIL.append(f"{label} :: {detail}")
        print(f"FAIL: {label} :: {detail}")


def _existing_govjti_keys() -> set[str]:
    """Every `GOVJTI#*` pk currently in this seller's state table -- read directly via boto3, not
    through the agent, so this script can tell whether a NEW one appears after each check below."""
    table = boto3.resource("dynamodb", region_name=REGION).Table(STATE_TABLE_NAME)
    keys: set[str] = set()
    scan_kwargs: dict = {}
    while True:
        resp = table.scan(**scan_kwargs)
        for item in resp.get("Items", []):
            pk = item.get("pk", "")
            if pk.startswith("GOVJTI#"):
                keys.add(pk)
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break
        scan_kwargs["ExclusiveStartKey"] = last_key
    return keys


def result_payload(result) -> dict:
    """MCP CallToolResult -> the actual dict payload the tool returned."""
    if getattr(result, "structuredContent", None):
        return result.structuredContent
    for block in result.content:
        if getattr(block, "type", None) == "text":
            try:
                return json.loads(block.text)
            except json.JSONDecodeError:
                return {"_raw_text": block.text, "_isError": getattr(result, "isError", None)}
    raise AssertionError(f"No structured/text content in result: {result}")


def invoke_buyer(prompt: str, token: str, session_id: str) -> str:
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
    }
    resp = requests.post(
        BUYER_INVOKE_URL, headers=headers, data=json.dumps({"prompt": prompt}), timeout=90
    )
    resp.raise_for_status()
    text_out = []
    for line in resp.text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        json_str = line[5:].strip()
        if not json_str:
            continue
        try:
            event = json.loads(json_str)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "text":
            text_out.append(event["data"])
    return "".join(text_out)


async def positive_buyer_mediated_check(token: str) -> None:
    """Drive the full check/book sequence through the deployed buyer, then confirm this seller's
    own DynamoDB shows a new GOVJTI# item -- proof the SELLER's verify_intent_token ran, not just
    that the buyer's U2 gate passed."""
    print("\n=== 1: positive, buyer-mediated check/book sequence ===")
    before_keys = _existing_govjti_keys()

    session_id = f"adcp-seller-gov-verify-{uuid.uuid4()}"
    assert len(session_id) >= 33, session_id
    plan_id = f"verify-seller-gate-{uuid.uuid4().hex[:8]}"
    brand_domain = f"verify-seller-gate-{uuid.uuid4().hex[:8]}.example"
    operator = "verify-seller-gate-agency"

    reply = invoke_buyer(
        "Do the following in order, calling the real tools each time, and report each tool's raw "
        "result: "
        f"1) adcp_sync_accounts(brand_domain=\"{brand_domain}\", operator=\"{operator}\", "
        "seller_ids=[\"reference\"]). "
        f"2) adcp_sync_governance(brand_domain=\"{brand_domain}\", operator=\"{operator}\", "
        "seller_ids=[\"reference\"]). "
        f"3) adcp_sync_plans(plan_id=\"{plan_id}\", brand_domain=\"{brand_domain}\", "
        "objectives=\"live verification of the seller governance gate\", budget_total=500.0, "
        "flight_start=\"2026-01-01\", flight_end=\"2026-01-31\", channels=[\"display\"]). "
        f"4) adcp_check_governance(seller_id=\"reference\", plan_id=\"{plan_id}\", "
        "tool_name=\"create_media_buy\", "
        "payload={\"packages\": [{\"product_id\": \"ref-display-news-01\", \"budget\": 500.0, "
        "\"pricing_option_id\": \"cpm-ref-display-news-01\"}]}). "
        "5) If step 4's verdict is approved, call adcp_create_media_buy with seller_id=\"reference\", "
        f"plan_id=\"{plan_id}\", governance_context set to the governance_context field from step 4's "
        f"result, product_id=\"ref-display-news-01\", budget=500.0, "
        "pricing_option_id=\"cpm-ref-display-news-01\", "
        f"brand_domain=\"{brand_domain}\", operator=\"{operator}\", "
        "flight_start=\"2026-01-01\", flight_end=\"2026-01-31\".",
        token,
        session_id,
    )
    print(reply)
    check(
        "buyer-mediated sequence completed without a governance refusal",
        "no_prior_check" not in reply
        and "governance_context_expired" not in reply
        and "PERMISSION_DENIED" not in reply
        and "GOVERNANCE_DENIED" not in reply
        and "GOVERNANCE_UNAVAILABLE" not in reply,
        reply,
    )

    # Give DynamoDB a moment (eventually-consistent scan) before reading back.
    time.sleep(2)
    after_keys = _existing_govjti_keys()
    new_keys = after_keys - before_keys
    check(
        "a NEW GOVJTI# item was written to the seller's own state table "
        "(proof verify_intent_token/jti_first_seen ran server-side, not just the buyer's U2 gate)",
        len(new_keys) >= 1,
        f"before={len(before_keys)} after={len(after_keys)} new={new_keys}",
    )


async def negative_direct_seller_call(token: str) -> None:
    """Bypass the buyer entirely: call the seller's own MCP endpoint directly with a governed
    account and a syntactically-garbage governance_context. The seller must refuse with
    PERMISSION_DENIED and must NOT reach jti_first_seen (no new GOVJTI# item), since signature
    verification fails before replay-checking would ever run."""
    print("\n=== 2: negative, direct-to-seller call bypassing the buyer entirely ===")
    before_keys = _existing_govjti_keys()

    brand_domain = f"verify-seller-negative-{uuid.uuid4().hex[:8]}.example"
    operator = "verify-seller-negative-agency"
    plan_id = f"verify-seller-negative-{uuid.uuid4().hex[:8]}"

    headers = {"authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with streamablehttp_client(
        SELLER_INVOKE_URL, headers, timeout=90, terminate_on_close=False
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Register the account as governed, directly against the seller, so create_media_buy's
            # governance branch is actually reached rather than short-circuiting on BR-U3-10's
            # ungoverned-account path.
            sync_accounts_result = await session.call_tool(
                "sync_accounts",
                {
                    "idempotency_key": f"verify-neg-{uuid.uuid4()}",
                    "accounts": [{"brand": {"domain": brand_domain}, "operator": operator}],
                },
            )
            sync_accounts_resp = result_payload(sync_accounts_result)
            check(
                "direct sync_accounts against the seller succeeded (prerequisite for this test)",
                bool(sync_accounts_resp.get("accounts")),
                f"got: {sync_accounts_resp}",
            )

            sync_governance_result = await session.call_tool(
                "sync_governance",
                {
                    "idempotency_key": f"verify-neg-gov-{uuid.uuid4()}",
                    "accounts": [
                        {
                            "account": {"brand": {"domain": brand_domain}, "operator": operator},
                            "governance_agents": [
                                {
                                    "url": (
                                        "https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/"
                                        "arn%3Aaws%3Abedrock-agentcore%3Aus-east-1%3A123456789012"
                                        "%3Aruntime%2FRefGovernance_adcpRefGovernance-EXAMPLE123"
                                        "/invocations?qualifier=DEFAULT"
                                    ),
                                    "authentication": {
                                        "schemes": ["Bearer"],
                                        "credentials": "irrelevant-for-this-negative-test",
                                    },
                                }
                            ],
                        }
                    ],
                },
            )
            sync_governance_resp = result_payload(sync_governance_result)
            check(
                "direct sync_governance against the seller succeeded (account is now governed)",
                bool(sync_governance_resp.get("accounts")),
                f"got: {sync_governance_resp}",
            )

            # The actual negative case: create_media_buy with a garbage governance_context.
            create_result = await session.call_tool(
                "create_media_buy",
                {
                    "idempotency_key": f"verify-neg-cmb-{uuid.uuid4()}",
                    "account": {"brand": {"domain": brand_domain}, "operator": operator},
                    "plan_id": plan_id,
                    "governance_context": "this.is-not.a-valid-jws",
                    "brand": {"domain": brand_domain},
                    "start_time": "asap",
                    "end_time": "2027-01-01T00:00:00Z",
                    "packages": [
                        {
                            "product_id": "ref-display-news-01",
                            "budget": 500.0,
                            "pricing_option_id": "cpm-ref-display-news-01",
                        }
                    ],
                },
            )
            create_resp = result_payload(create_result)
            print("\ncreate_media_buy (garbage governance_context, direct to seller):")
            print(json.dumps(create_resp, indent=2)[:800])

            error_codes = [e.get("code") for e in create_resp.get("errors", [])]
            check(
                "direct create_media_buy with a garbage governance_context is refused with "
                "PERMISSION_DENIED",
                "PERMISSION_DENIED" in error_codes,
                f"got error codes: {error_codes}, full response: {create_resp}",
            )
            check(
                "the specific failure_reason is never echoed to the caller (NFR-U3-3)",
                "signature_invalid" not in json.dumps(create_resp)
                and "malformed" not in json.dumps(create_resp),
                f"got: {create_resp}",
            )

    time.sleep(2)
    after_keys = _existing_govjti_keys()
    check(
        "NO new GOVJTI# item appeared for the refused attempt "
        "(signature verification failed before jti replay-checking would ever run -- proof the "
        "seller never reached the governance agent for this request)",
        len(after_keys - before_keys) == 0,
        f"before={len(before_keys)} after={len(after_keys)} unexpected new keys={after_keys - before_keys}",
    )


async def main() -> None:
    token = get_test_user_access_token()
    print(f"Fetched Cognito access token ({len(token)} chars).")

    await positive_buyer_mediated_check(token)
    await negative_direct_seller_call(token)

    print("\n" + "=" * 60)
    print(f"PASSED: {len(PASS)}  FAILED: {len(FAIL)}")
    if FAIL:
        print("\nFailures:")
        for f in FAIL:
            print(f" - {f}")
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
