"""Live check: is `display_728x90` discoverable AND evaluable, alongside `display_300x250`?

A second evaluable format is what makes the journey's Creative scan show a real verdict on more than one
card. Three things have to line up, in three different places, and only a live run proves all three:

  1. The seller declares the format via `list_creative_formats` (deployed seller).
  2. A fixture asset of exactly the declared dimensions is fetchable (published to CloudFront).
  3. The governance agent's evaluator measures that asset and returns real feature values (deployed
     governance agent).

Both formats are checked, not just the new one — adding a format must not disturb the existing one, and
the two verdicts must be genuinely different measurements rather than the same result twice.

    .venv/bin/python3 verify_second_format_live.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.parse

import httpx
from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

load_dotenv()

from auth import get_test_user_access_token  # noqa: E402
from governance_agents import resolve_governance_agent  # noqa: E402
from seller_agents import resolve_seller_agent  # noqa: E402

SELLER_ID = "reference"

#: The formats expected to be evaluable, and the fixture asset for each. Restated here rather than read
#: from the seller, because this is the claim under test.
EXPECTED = {
    "display_300x250": ("clean_banner.png", 300, 250),
    "display_728x90": ("clean_leaderboard.png", 728, 90),
}

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        if detail:
            print(f"        {detail}")
        failures.append(label)
    return ok


def mcp_url(url: str) -> str:
    if url.startswith("arn:"):
        return (
            "https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/"
            f"{urllib.parse.quote(url, safe='')}/invocations"
        )
    return url


def payload(result) -> dict:
    if not result.content:
        return {}
    text = getattr(result.content[0], "text", "") or ""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"_raw": text}
    return parsed if isinstance(parsed, dict) else {"_raw": text}


def asset_url(filename: str) -> str:
    origin = os.environ.get("BUYER_UI_ORIGIN", "").strip()
    if not origin:
        raise SystemExit("BUYER_UI_ORIGIN is not set (python3 deploy_all.py --only buyer-ui-origin)")
    return f"{origin.rstrip('/')}/.well-known/creatives/{filename}"


async def main() -> int:
    print("=== 1. The seller declares both formats ===")
    seller = resolve_seller_agent(SELLER_ID)
    declared: dict[str, dict] = {}
    async with streamablehttp_client(mcp_url(seller["url"]), headers=seller["headers"]) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            formats = (payload(await session.call_tool("list_creative_formats", {})).get("formats")) or []
            for entry in formats:
                fid = (entry.get("format_id") or {}).get("id")
                if fid:
                    declared[fid] = entry
    print(f"  formats advertised: {sorted(declared)}")

    for format_id, (_, width, height) in EXPECTED.items():
        if not check(f"{format_id}: declared by the seller", format_id in declared):
            continue
        dimensions = declared[format_id].get("dimensions") or {}
        check(
            f"{format_id}: declared dimensions are {width}x{height}",
            (dimensions.get("width"), dimensions.get("height")) == (width, height),
            f"got {dimensions}",
        )
        # The evaluator reads `assets.main_image.url`, so the slot has to be there and be an image.
        slots = declared[format_id].get("assets") or []
        slot = next((s for s in slots if s.get("asset_id") == "main_image"), None)
        check(f"{format_id}: has a required main_image asset slot", slot is not None and slot.get("required") is True)

    print("\n=== 2. Each fixture asset is really fetchable at its declared size ===")
    for format_id, (filename, width, height) in EXPECTED.items():
        url = asset_url(filename)
        response = httpx.get(url, timeout=20, follow_redirects=True)
        content_type = response.headers.get("content-type", "")
        # Checked on content type and PNG header, not on status: this origin rewrites 403 to index.html
        # with a 200, so a missing object returns the SPA's HTML and a green status code.
        is_png = response.content[:8] == b"\x89PNG\r\n\x1a\n"
        check(
            f"{format_id}: {filename} is served as a real PNG",
            response.status_code == 200 and content_type.startswith("image/") and is_png,
            f"status={response.status_code} type={content_type!r} png_header={is_png}",
        )
        if is_png:
            import struct

            actual = struct.unpack(">II", response.content[16:24])
            check(f"{format_id}: the served asset is {width}x{height}", actual == (width, height), f"got {actual}")

    print("\n=== 3. The governance evaluator measures each asset for real ===")
    governance = resolve_governance_agent()
    token = get_test_user_access_token()
    verdicts: dict[str, dict] = {}
    async with streamablehttp_client(
        mcp_url(governance.get("url") or ""), headers={"Authorization": f"Bearer {token}"}
    ) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            for format_id, (filename, width, height) in EXPECTED.items():
                request = {
                    "creative_manifest": {
                        "format_id": {"id": format_id, "agent_url": seller["agent_url"]},
                        "assets": {
                            "main_image": {
                                "asset_type": "image",
                                "url": asset_url(filename),
                                "width": width,
                                "height": height,
                            }
                        },
                        # The FORMAT's own required dimensions, which is what `dimension_conformance` is
                        # measured against -- deliberately not the asset's self-reported size, or a
                        # mis-sized image whose metadata matches itself would always pass.
                        "ext": {"format_dimensions": {"width": width, "height": height}},
                    }
                }
                response = payload(await session.call_tool("get_creative_features", request))
                verdicts[format_id] = response
                results = response.get("results")
                if not isinstance(results, list) or not results:
                    check(
                        f"{format_id}: the evaluator returned real results",
                        False,
                        f"response={json.dumps(response)[:300]}",
                    )
                    continue
                check(f"{format_id}: the evaluator returned real results", True)
                by_id = {r.get("feature_id"): r for r in results if isinstance(r, dict)}
                print(f"        {format_id}: {json.dumps({k: v.get('value') for k, v in by_id.items()})}")
                check(
                    f"{format_id}: dimension_conformance is true (asset matches the format)",
                    by_id.get("dimension_conformance", {}).get("value") is True,
                    f"got {by_id.get('dimension_conformance')}",
                )
                contrast = by_id.get("contrast_ratio", {}).get("value")
                check(
                    f"{format_id}: contrast_ratio is a real number on WCAG's scale",
                    isinstance(contrast, (int, float)) and 1.0 <= contrast <= 21.0,
                    f"got {contrast!r}",
                )
                check(
                    f"{format_id}: urgency_claim is false on a clean asset",
                    by_id.get("urgency_claim", {}).get("value") is False,
                    f"got {by_id.get('urgency_claim')}",
                )

    print("\n=== 4. The two verdicts are independent measurements ===")
    if len(verdicts) == 2 and all(v.get("results") for v in verdicts.values()):
        def dims(format_id: str) -> tuple:
            details = next(
                r for r in verdicts[format_id]["results"] if r.get("feature_id") == "dimension_conformance"
            )["details"]
            return details["measured_width"], details["measured_height"]

        measured = {fid: dims(fid) for fid in EXPECTED}
        print(f"        measured: {measured}")
        check(
            "each format was measured at its OWN size, not the same asset twice",
            measured["display_300x250"] == (300, 250) and measured["display_728x90"] == (728, 90),
            f"got {measured}",
        )

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("All checks passed. Two formats are declared, fetchable and independently evaluated,")
    print("so the journey's Creative scan can show a real verdict on either card.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
