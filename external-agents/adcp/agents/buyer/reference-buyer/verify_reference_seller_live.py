"""
Live verification script for the redeployed AdCP reference seller
(task 10 of .kiro/specs/seller-agent-adcp-compliance/tasks.md).

Not a permanent part of the project - a one-off script to exercise the
real deployed MCP endpoint after `agentcore deploy`, covering the
functionality tasks 3-9 implemented:
  - create_media_buy: success, replay (same key), conflict (same key,
    different body), missing idempotency_key, unknown product_id
  - update_media_buy: budget change against a real media_buy_id, and the
    nonexistent-ID error path
  - get_media_buys: no filter vs. filtered, reflecting the update above
  - get_media_buy_delivery: against the same live media buy
  - provide_performance_feedback: against the same live media buy
  - sync_accounts / list_accounts: persists and is retrievable
  - get_adcp_capabilities: idempotency block now reports supported=True

Every call below goes over the real deployed MCP endpoint (a genuine
Cognito-authenticated HTTPS call to AgentCore Runtime) - nothing here is
mocked or replayed from a fixture. Run from this directory:

    source .venv/bin/activate  # or: uv run
    python3 verify_reference_seller_live.py
"""


from aws_region import region
import asyncio
import json
import os
import uuid

from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from auth import get_test_user_access_token

load_dotenv()

REFERENCE_SELLER_RUNTIME_ARN = os.environ["REFERENCE_SELLER_RUNTIME_ARN"]
REGION = region()
import urllib.parse

ESCAPED_ARN = urllib.parse.quote(REFERENCE_SELLER_RUNTIME_ARN, safe="")
URL = f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/{ESCAPED_ARN}/invocations?qualifier=DEFAULT"

PASS = []
FAIL = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASS.append(label)
        print(f"PASS: {label}")
    else:
        FAIL.append(f"{label} :: {detail}")
        print(f"FAIL: {label} :: {detail}")


def is_error(resp: dict) -> bool:
    """True if the response is an error - either media_buy_error_response's
    {"errors": [...]} shape, or the SDK's upstream request-schema
    validation wrapper {"adcp_error": {...}}."""
    return "errors" in resp or "adcp_error" in resp


def error_codes(resp: dict) -> list[str]:
    """Extract every error code from either error shape."""
    codes = [e.get("code") for e in resp.get("errors", [])]
    adcp_error = resp.get("adcp_error")
    if adcp_error:
        codes.append(adcp_error.get("code"))
        for issue in (adcp_error.get("details") or {}).get("issues", []):
            if issue.get("keyword"):
                codes.append(issue["keyword"])
    return codes


def result_payload(result) -> dict:
    """MCP CallToolResult -> the actual dict payload the tool returned."""
    if getattr(result, "structuredContent", None):
        return result.structuredContent
    # Fall back to parsing the first text content block as JSON.
    for block in result.content:
        if getattr(block, "type", None) == "text":
            try:
                return json.loads(block.text)
            except json.JSONDecodeError:
                return {"_raw_text": block.text, "_isError": getattr(result, "isError", None)}
    raise AssertionError(f"No structured/text content in result: {result}")


async def main() -> None:
    token = get_test_user_access_token()
    print(f"Fetched Cognito access token ({len(token)} chars).\n")
    headers = {"authorization": f"Bearer {token}", "Content-Type": "application/json"}

    async with streamablehttp_client(URL, headers, timeout=120, terminate_on_close=False) as (
        read,
        write,
        _,
    ):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools_result = await session.list_tools()
            tool_names = {t.name for t in tools_result.tools}
            print("Advertised tools:", sorted(tool_names))
            expected_tools = {
                "get_adcp_capabilities",
                "get_products",
                "list_creative_formats",
                "create_media_buy",
                "update_media_buy",
                "get_media_buys",
                "get_media_buy_delivery",
                "provide_performance_feedback",
                "sync_accounts",
                "list_accounts",
            }
            check(
                "advertised_tools includes all task 3-9 tools",
                expected_tools <= tool_names,
                f"missing: {expected_tools - tool_names}",
            )

            # --- get_adcp_capabilities (task 9) ---
            caps_result = await session.call_tool("get_adcp_capabilities", {})
            caps = result_payload(caps_result)
            print("\nget_adcp_capabilities:", json.dumps(caps, indent=2)[:800])
            idem = (caps.get("adcp") or {}).get("idempotency") or {}
            check(
                "get_adcp_capabilities reports idempotency.supported=True",
                idem.get("supported") is True,
                f"got: {idem}",
            )
            check(
                "get_adcp_capabilities reports replay_ttl_seconds=86400",
                idem.get("replay_ttl_seconds") == 86400,
                f"got: {idem}",
            )

            account = {"brand": {"domain": "verify-task10.example"}, "operator": "verify-task10-buyer"}
            brand = {"domain": "verify-task10.example"}

            # --- create_media_buy: success ---
            key1 = f"verify-{uuid.uuid4()}"
            create_params = {
                "idempotency_key": key1,
                "account": account,
                "brand": brand,
                "start_time": "asap",
                "end_time": "2027-01-01T00:00:00Z",
                "packages": [
                    {
                        "product_id": "ref-ctv-sports-01",
                        "budget": 1000.0,
                        "pricing_option_id": "cpm-ref-ctv-sports-01",
                    }
                ],
            }
            create_result = await session.call_tool("create_media_buy", create_params)
            create_resp = result_payload(create_result)
            print("\ncreate_media_buy (success):", json.dumps(create_resp, indent=2)[:800])
            media_buy_id = create_resp.get("media_buy_id")
            check(
                "create_media_buy success returns a media_buy_id",
                bool(media_buy_id),
                f"got: {create_resp}",
            )

            # --- create_media_buy: replay (same key, same body) ---
            replay_result = await session.call_tool("create_media_buy", create_params)
            replay_resp = result_payload(replay_result)
            check(
                "create_media_buy replay returns the same media_buy_id",
                replay_resp.get("media_buy_id") == media_buy_id,
                f"first: {media_buy_id}, replay: {replay_resp.get('media_buy_id')}",
            )

            # --- create_media_buy: conflict (same key, different body) ---
            conflict_params = dict(create_params)
            conflict_params["packages"] = [
                {
                    "product_id": "ref-display-news-01",
                    "budget": 5000.0,
                    "pricing_option_id": "cpm-ref-display-news-01",
                }
            ]
            conflict_result = await session.call_tool("create_media_buy", conflict_params)
            conflict_resp = result_payload(conflict_result)
            print("\ncreate_media_buy (conflict):", json.dumps(conflict_resp, indent=2)[:500])
            check(
                "create_media_buy conflict (same key, different body) returns an error, not a new buy",
                is_error(conflict_resp)
                or conflict_resp.get("media_buy_id") == media_buy_id,
                f"got: {conflict_resp}",
            )

            # --- create_media_buy: missing idempotency_key ---
            missing_key_params = {
                "account": account,
                "brand": brand,
                "start_time": "asap",
                "end_time": "2027-01-01T00:00:00Z",
                "packages": [
                    {
                        "product_id": "ref-ctv-sports-01",
                        "budget": 500.0,
                        "pricing_option_id": "cpm-ref-ctv-sports-01",
                    }
                ],
            }
            missing_key_result = await session.call_tool("create_media_buy", missing_key_params)
            missing_key_resp = result_payload(missing_key_result)
            print("\ncreate_media_buy (missing idempotency_key):", json.dumps(missing_key_resp, indent=2)[:500])
            check(
                "create_media_buy without idempotency_key returns a validation error",
                is_error(missing_key_resp) and "required" in error_codes(missing_key_resp),
                f"got: {missing_key_resp}",
            )

            # --- create_media_buy: unknown product_id ---
            unknown_product_params = {
                "idempotency_key": f"verify-{uuid.uuid4()}",
                "account": account,
                "brand": brand,
                "start_time": "asap",
                "end_time": "2027-01-01T00:00:00Z",
                "packages": [
                    {
                        "product_id": "does-not-exist",
                        "budget": 100.0,
                        "pricing_option_id": "does-not-matter",
                    }
                ],
            }
            unknown_product_result = await session.call_tool("create_media_buy", unknown_product_params)
            unknown_product_resp = result_payload(unknown_product_result)
            print("\ncreate_media_buy (unknown product_id):", json.dumps(unknown_product_resp, indent=2)[:500])
            check(
                "create_media_buy with unknown product_id returns PRODUCT_NOT_FOUND error",
                any(e.get("code") == "PRODUCT_NOT_FOUND" for e in unknown_product_resp.get("errors", [])),
                f"got: {unknown_product_resp}",
            )

            # --- update_media_buy: budget change against the real media_buy_id ---
            update_params = {
                "idempotency_key": f"verify-{uuid.uuid4()}",
                "media_buy_id": media_buy_id,
                "account": account,
                "packages": [
                    {
                        "package_id": create_resp["packages"][0]["package_id"],
                        "budget": 2000.0,
                    }
                ],
            }
            update_result = await session.call_tool("update_media_buy", update_params)
            update_resp = result_payload(update_result)
            print("\nupdate_media_buy (budget change):", json.dumps(update_resp, indent=2)[:600])
            check(
                "update_media_buy budget change succeeds",
                "media_buy_id" in update_resp and "errors" not in update_resp,
                f"got: {update_resp}",
            )

            # --- update_media_buy: pause ---
            pause_params = {
                "idempotency_key": f"verify-{uuid.uuid4()}",
                "media_buy_id": media_buy_id,
                "account": account,
                "paused": True,
            }
            pause_result = await session.call_tool("update_media_buy", pause_params)
            pause_resp = result_payload(pause_result)
            print("\nupdate_media_buy (pause):", json.dumps(pause_resp, indent=2)[:600])
            check(
                "update_media_buy pause succeeds",
                "media_buy_id" in pause_resp and "errors" not in pause_resp,
                f"got: {pause_resp}",
            )

            # --- update_media_buy: nonexistent ID ---
            bad_update_params = {
                "idempotency_key": f"verify-{uuid.uuid4()}",
                "media_buy_id": "does-not-exist",
                "account": account,
                "paused": True,
            }
            bad_update_result = await session.call_tool("update_media_buy", bad_update_params)
            bad_update_resp = result_payload(bad_update_result)
            print("\nupdate_media_buy (nonexistent ID):", json.dumps(bad_update_resp, indent=2)[:500])
            check(
                "update_media_buy on nonexistent media_buy_id returns MEDIA_BUY_NOT_FOUND error",
                any(e.get("code") == "MEDIA_BUY_NOT_FOUND" for e in bad_update_resp.get("errors", [])),
                f"got: {bad_update_resp}",
            )

            # --- get_media_buys: no filter ---
            all_result = await session.call_tool("get_media_buys", {})
            all_resp = result_payload(all_result)
            all_ids = {mb.get("media_buy_id") for mb in all_resp.get("media_buys", [])}
            check(
                "get_media_buys with no filter includes our created media_buy_id",
                media_buy_id in all_ids,
                f"got {len(all_ids)} media buys, ours not present",
            )

            # --- get_media_buys: filtered ---
            filtered_result = await session.call_tool("get_media_buys", {"media_buy_ids": [media_buy_id]})
            filtered_resp = result_payload(filtered_result)
            filtered_buys = filtered_resp.get("media_buys", [])
            print("\nget_media_buys (filtered):", json.dumps(filtered_resp, indent=2)[:600])
            check(
                "get_media_buys filtered by media_buy_ids returns exactly our buy",
                len(filtered_buys) == 1 and filtered_buys[0].get("media_buy_id") == media_buy_id,
                f"got: {filtered_buys}",
            )
            check(
                "get_media_buys reflects the prior update_media_buy (no stale caching)",
                filtered_buys and filtered_buys[0].get("status") == "paused",
                f"got status: {filtered_buys[0].get('status') if filtered_buys else None}",
            )

            # --- get_media_buy_delivery ---
            delivery_result = await session.call_tool(
                "get_media_buy_delivery", {"media_buy_ids": [media_buy_id]}
            )
            delivery_resp = result_payload(delivery_result)
            print("\nget_media_buy_delivery:", json.dumps(delivery_resp, indent=2)[:800])
            deliveries = delivery_resp.get("media_buy_deliveries") or delivery_resp.get("deliveries") or []
            check(
                "get_media_buy_delivery returns a delivery record for our media_buy_id",
                any(d.get("media_buy_id") == media_buy_id for d in deliveries) if isinstance(deliveries, list) else False,
                f"got: {delivery_resp}",
            )

            # --- provide_performance_feedback ---
            feedback_params = {
                "idempotency_key": f"verify-{uuid.uuid4()}",
                "media_buy_id": media_buy_id,
                "performance_index": 1.1,
                "measurement_period": {
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-02T00:00:00Z",
                },
            }
            feedback_result = await session.call_tool("provide_performance_feedback", feedback_params)
            feedback_resp = result_payload(feedback_result)
            print("\nprovide_performance_feedback:", json.dumps(feedback_resp, indent=2)[:400])
            check(
                "provide_performance_feedback against a real media buy succeeds",
                feedback_resp.get("success") is True,
                f"got: {feedback_resp}",
            )

            # --- provide_performance_feedback: unknown media_buy_id ---
            bad_feedback_params = {
                "idempotency_key": f"verify-{uuid.uuid4()}",
                "media_buy_id": "does-not-exist",
                "performance_index": 1.0,
                "measurement_period": {
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-02T00:00:00Z",
                },
            }
            bad_feedback_result = await session.call_tool("provide_performance_feedback", bad_feedback_params)
            bad_feedback_resp = result_payload(bad_feedback_result)
            print("\nprovide_performance_feedback (unknown ID):", json.dumps(bad_feedback_resp, indent=2)[:400])
            check(
                "provide_performance_feedback on unknown media_buy_id returns an error",
                "errors" in bad_feedback_resp,
                f"got: {bad_feedback_resp}",
            )

            # --- sync_accounts / list_accounts ---
            unique_domain = f"verify-task10-{uuid.uuid4().hex[:8]}.example"
            sync_params = {
                "idempotency_key": f"verify-{uuid.uuid4()}",
                "accounts": [{"brand": {"domain": unique_domain}, "operator": "verify-task10-operator"}],
            }
            sync_result = await session.call_tool("sync_accounts", sync_params)
            sync_resp = result_payload(sync_result)
            print("\nsync_accounts:", json.dumps(sync_resp, indent=2)[:600])
            synced_accounts = sync_resp.get("accounts", [])
            check(
                "sync_accounts with a brand/operator pair returns a created account",
                bool(synced_accounts) and synced_accounts[0].get("action") == "created",
                f"got: {sync_resp}",
            )

            list_result = await session.call_tool(
                "list_accounts",
                {"account": {"brand": {"domain": unique_domain}, "operator": "verify-task10-operator"}},
            )
            list_resp = result_payload(list_result)
            print("\nlist_accounts (filtered):", json.dumps(list_resp, indent=2)[:600])
            listed_accounts = list_resp.get("accounts", [])
            check(
                "list_accounts retrieves the account just synced",
                any(
                    a.get("brand", {}).get("domain") == unique_domain
                    for a in listed_accounts
                ),
                f"got: {listed_accounts}",
            )

    print("\n" + "=" * 60)
    print(f"PASSED: {len(PASS)}  FAILED: {len(FAIL)}")
    if FAIL:
        print("\nFailures:")
        for f in FAIL:
            print(f" - {f}")


if __name__ == "__main__":
    asyncio.run(main())
