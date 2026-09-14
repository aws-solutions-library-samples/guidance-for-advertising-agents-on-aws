"""Render the Cognito auth block into every agent's `agentcore.json`.

    python3 render_agentcore_auth.py --dry-run
    python3 render_agentcore_auth.py

Single source of truth: `agents/buyer/reference-buyer/.env`'s `COGNITO_DISCOVERY_URL`,
`COGNITO_CLIENT_ID` and `COGNITO_ADDITIONAL_CLIENT_IDS`. Every `customJwtAuthorizer` in the repo is
derived from those three values, so the deployed allowlists cannot drift from what the buyer's own
`auth.py` enforces.

## Why this replaced two per-seller copies

`reference-seller` and `triton-seller` each carried their own `render_agentcore_config.py`, identical
apart from a path. `gotham-seller`, `gotham-reach-service` and `reference-governance` had none, so their
auth blocks were only ever edited by hand — which is exactly the state that lets a hand-edit be silently
reverted, or a new agent be forgotten. Both failure modes were real: the per-seller renderers pinned
`allowedClients` to a single id, so adding the machine-to-machine client by hand would have been undone
on the next render.

## Discovery rather than a hard-coded list

Configs are found by globbing `agents/*/*/agentcore/agentcore.json`. A hard-coded list is a list someone
must remember to extend; the failure mode of forgetting is an agent whose allowlist quietly stops
matching, which surfaces as a 403 far from the cause. Globbing makes a new agent covered by default.

Runtimes with no `customJwtAuthorizer` are skipped rather than given one: whether a runtime requires JWT
auth is a deliberate choice in its own config, not something this script should impose.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
BUYER_ENV_PATH = REPO_ROOT / "agents" / "buyer" / "reference-buyer" / ".env"
CONFIG_GLOB = "agents/*/*/agentcore/agentcore.json"


def read_env_value(env_path: Path, key: str) -> str | None:
    if not env_path.exists():
        return None
    pattern = re.compile(rf"^{re.escape(key)}=(.*)$")
    for line in env_path.read_text().splitlines():
        m = pattern.match(line.strip())
        if m:
            return m.group(1).strip()
    return None


def allowed_clients() -> list[str]:
    client_id = read_env_value(BUYER_ENV_PATH, "COGNITO_CLIENT_ID")
    if not client_id:
        raise SystemExit(
            f"Could not read COGNITO_CLIENT_ID from {BUYER_ENV_PATH}. "
            "Run agents/buyer/reference-buyer/deploy_cognito_setup.py first."
        )
    additional = [
        c.strip()
        for c in (read_env_value(BUYER_ENV_PATH, "COGNITO_ADDITIONAL_CLIENT_IDS") or "").split(",")
        if c.strip()
    ]
    # Browser client first, then machine clients. De-duplicated with order preserved so a value
    # repeated in .env does not produce a repeated entry in the deployed allowlist.
    return list(dict.fromkeys([client_id, *additional]))


#: What the COMMITTED state of every auth block looks like. `.invalid` is reserved by RFC 2606, so it
#: can never resolve, and the shape still satisfies the CLI's schema (HTTPS, ends with the well-known
#: path). `agentcore.json` is the CLI's own source of truth so it cannot be gitignored like the
#: generated `aws-targets.json` is -- `--reset` is how it gets back to a publishable state after a
#: deploy has written real values into it.
PLACEHOLDER_DISCOVERY_URL = "https://cognito-idp.invalid/.well-known/openid-configuration"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    parser.add_argument(
        "--reset",
        action="store_true",
        help=(
            "write placeholders instead of real values, returning every auth block to its committed "
            "state. Run before committing if a deploy has rendered real values in."
        ),
    )
    args = parser.parse_args()

    if args.reset:
        discovery_url = PLACEHOLDER_DISCOVERY_URL
        clients: list[str] = []
    else:
        discovery_url = read_env_value(BUYER_ENV_PATH, "COGNITO_DISCOVERY_URL")
        if not discovery_url:
            raise SystemExit(f"Could not read COGNITO_DISCOVERY_URL from {BUYER_ENV_PATH}.")
        clients = allowed_clients()

    print(f"discoveryUrl   : {discovery_url}")
    print(f"allowedClients : {clients}")
    print()

    configs = sorted(REPO_ROOT.glob(CONFIG_GLOB))
    if not configs:
        raise SystemExit(f"No agentcore.json found under {CONFIG_GLOB!r} — is the repo layout as expected?")

    changed_files = 0
    for path in configs:
        rel = path.relative_to(REPO_ROOT)
        config = json.loads(path.read_text())
        changed = False
        touched: list[str] = []
        for runtime in config.get("runtimes", []):
            authorizer = (runtime.get("authorizerConfiguration") or {}).get("customJwtAuthorizer")
            if authorizer is None:
                # No JWT authorizer configured for this runtime; not this script's decision to add one.
                continue
            if authorizer.get("discoveryUrl") != discovery_url:
                authorizer["discoveryUrl"] = discovery_url
                changed = True
            if args.reset:
                # Omitted rather than emptied: allowedClients is optional in the schema, and an empty
                # list would read as "allow nothing" rather than "not configured yet".
                if "allowedClients" in authorizer:
                    del authorizer["allowedClients"]
                    changed = True
            elif authorizer.get("allowedClients") != clients:
                authorizer["allowedClients"] = list(clients)
                changed = True
            touched.append(runtime.get("name", "<unnamed>"))

        if not touched:
            print(f"  skip    {rel}  (no customJwtAuthorizer)")
            continue
        if not changed:
            print(f"  ok      {rel}  {touched}")
            continue
        if args.dry_run:
            print(f"  WOULD   {rel}  {touched}")
        else:
            path.write_text(json.dumps(config, indent=2) + "\n")
            print(f"  UPDATED {rel}  {touched}")
        changed_files += 1

    print()
    if args.dry_run:
        print(f"Dry run. {changed_files} file(s) would change.")
    else:
        print(f"{changed_files} file(s) updated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
