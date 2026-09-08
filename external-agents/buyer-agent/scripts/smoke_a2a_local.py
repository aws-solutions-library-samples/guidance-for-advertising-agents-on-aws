#!/usr/bin/env python3
"""Local smoke test for the buyer Strands A2A runtime (no AWS deploy needed).

Requires the a2a runtime deps (infra/aws/agentcore/requirements-a2a.txt) and AWS
credentials for Bedrock. Starts a2a_main in-process, fetches the agent card, and
(optionally) sends one campaign-planning message. The seller call will error
unless AAMP_SELLER_RUNTIME_ARN + A2A_SELLER_SSM_PATH are set — that's expected
locally; use --card-only to skip model/seller calls.

Usage:
    python scripts/smoke_a2a_local.py --card-only
    python scripts/smoke_a2a_local.py --prompt "Plan a $500K Q4 CTV campaign targeting adults 25-54"
"""

import argparse
import sys
import threading
import time
import uuid

import httpx
import uvicorn


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--prompt", default="Plan a $500K Q4 CTV campaign targeting adults 25-54")
    ap.add_argument("--card-only", action="store_true")
    args = ap.parse_args()

    from ad_buyer.interfaces.agentcore.a2a_main import app

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()

    base = f"http://127.0.0.1:{args.port}"
    card = None
    for _ in range(30):
        try:
            r = httpx.get(f"{base}/.well-known/agent-card.json", timeout=2)
            if r.status_code == 200:
                card = r.json()
                break
        except Exception:
            time.sleep(1)
    if not card:
        print("FAIL: agent card not served")
        return 1
    print("OK: agent card ->", card.get("name"))
    if args.card_only:
        return 0

    payload = {
        "jsonrpc": "2.0",
        "id": uuid.uuid4().hex,
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": args.prompt}],
                "messageId": uuid.uuid4().hex,
            }
        },
    }
    r = httpx.post(f"{base}/", json=payload, timeout=300)
    print("message/send HTTP", r.status_code)
    print(r.text[:2000])
    return 0 if r.status_code == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
