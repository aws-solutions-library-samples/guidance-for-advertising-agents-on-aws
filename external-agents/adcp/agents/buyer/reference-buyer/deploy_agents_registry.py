"""
Generates AGENTS_JSON — the registry of A2A agents the chat UI can chat to —
from the runtime ARNs already captured in .env, and writes it back to .env.

    source .venv/bin/activate
    python3 deploy_agents_registry.py            # print what it would write
    python3 deploy_agents_registry.py --apply    # write it to .env

Run after the A2A runtime has been deployed (deploy_buyer_agent_a2a.py, or
deploy_all.py step 11/12), since the buyer's A2A entry needs its real ARN.

Generated rather than hand-maintained because the URL is a function of an ARN
that changes per deployment, and a stale URL in AGENTS_JSON would show up as
an agent that appears selectable but can't be reached. Re-running is safe: it
rebuilds the entries and preserves any extra ones you added by hand (matched
on id).

Entries produced:
  buyer-a2a       this project's buyer agent over A2A (cognito_bearer, internal)

A further A2A seller is emitted as a commented-out template in .env.example
rather than a live entry — it has no runtime yet, and listing an undeployed
agent as selectable would be a UI that lies. Add it here once its runtime ARN
exists.
"""

import argparse
import json
import re
import sys
import urllib.parse
from pathlib import Path

from aws_region import region

ENV_PATH = Path(__file__).parent / ".env"
REGION = region()

def read_env_value(key: str) -> str | None:
    if not ENV_PATH.exists():
        return None
    pattern = re.compile(rf"^{re.escape(key)}=(.*)$")
    for line in ENV_PATH.read_text().splitlines():
        m = pattern.match(line.strip())
        if m:
            return m.group(1)
    return None


def upsert_env_value(key: str, value: str) -> None:
    lines = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    replaced = False
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            replaced = True
    if not replaced:
        lines.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(lines) + "\n")
    print(f"  wrote {ENV_PATH}: {key}=<{len(value)} chars>")


def invoke_url(arn: str) -> str:
    return (
        f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/"
        f"{urllib.parse.quote(arn, safe='')}/invocations"
    )


def build_entries() -> list[dict]:
    a2a_arn = read_env_value("BUYER_AGENT_A2A_RUNTIME_ARN")
    if not a2a_arn:
        print(
            "!!! BUYER_AGENT_A2A_RUNTIME_ARN is not set in .env. Deploy the A2A runtime "
            "first (deploy_buyer_agent_a2a.py)."
        )
        sys.exit(1)

    entries = [
        {
            "id": "buyer-a2a",
            "name": "AdCP Buyer Agent (A2A)",
            "kind": "buyer",
            # No ?qualifier=DEFAULT: the A2A binding is mounted at
            # /invocations itself, and the agent-card path hangs off it.
            "url": invoke_url(a2a_arn),
            "auth_type": "cognito_bearer",
            "origin": "internal",
        },
        # A third-party A2A seller goes here, e.g.
        #     {
        #         "id": "external-seller-a2a",
        #         "name": "Third-Party Seller Agent (A2A)",
        #         "kind": "seller",
        #         "url": "https://a2a.example.com/a2a",
        #         "auth_type": "static_bearer",
        #         "auth_token_env": "EXTERNAL_SELLER_AUTH_TOKEN",
        #         "origin": "external",
        #     }
        # Its token variable must also be added to runtime_env.build_env_vars, or the container
        # will not receive it.
    ]
    return entries


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate AGENTS_JSON into .env.")
    parser.add_argument("--apply", action="store_true", help="Write to .env (default: print only).")
    args = parser.parse_args()

    generated = build_entries()
    generated_ids = {e["id"] for e in generated}

    # Preserve hand-added entries (e.g. a triton-a2a you added yourself)
    # rather than silently dropping them on a re-run.
    existing_raw = read_env_value("AGENTS_JSON")
    preserved = []
    if existing_raw:
        try:
            for entry in json.loads(existing_raw):
                if isinstance(entry, dict) and entry.get("id") not in generated_ids:
                    preserved.append(entry)
        except json.JSONDecodeError:
            print("  (existing AGENTS_JSON is not valid JSON; it will be replaced)")

    entries = generated + preserved
    if preserved:
        print(f"  preserving {len(preserved)} hand-added entry/entries: "
              f"{', '.join(e.get('id', '?') for e in preserved)}")

    print("AGENTS_JSON entries:")
    for entry in entries:
        print(f"  {entry['id']:<16} {entry['kind']:<7} {entry['origin']:<9} {entry['url']}")

    value = json.dumps(entries)
    if not args.apply:
        print("\nDry run — nothing written. Re-run with --apply.")
        return
    upsert_env_value("AGENTS_JSON", value)
    print("\nSet DEFAULT_AGENT_ID in .env if you want a specific default (defaults to the first entry).")


if __name__ == "__main__":
    main()
