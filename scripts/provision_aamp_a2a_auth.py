#!/usr/bin/env python3
"""Provision inbound A2A (Cognito OAuth) logins for the AAMP agents.

The IAB seller/buyer runtimes are deployed with a Cognito **JWT authorizer**
(attached by ``scripts/deploy_aamp_agents.sh``), so callers must present a
Cognito bearer token rather than SigV4. This script creates the Cognito user
each runtime accepts and stores the credential JSON as an SSM SecureString at
the repo's conventional path::

    /{stack_prefix}/a2a-inbound-tokens/{unique_id}/{AgentName}

which is exactly where the caller side looks — ``A2ATokenManager`` (via
``shared/a2a_client_tools.py`` and the handler's external-runtime invoke) and
the UI's OAuth invoke path.

Rather than duplicating the credential schema, password policy, and
app-client-flow handling, this **reuses** the implementation that already backs
the external agents: ``ExternalAgentDeployer.ensure_inbound_cognito_credentials``
from ``external-agents/deploy_external_agents.py``. One source of truth means
the AAMP credentials can never drift from what the callers expect.

The generated password is never printed or returned — only written to the
encrypted SecureString parameter.

Usage:
    python scripts/provision_aamp_a2a_auth.py \
        --region us-east-1 --stack-prefix ppp --unique-id omc79u \
        --pool-id us-east-1_ABC --client-id abc123 \
        --agent AAMPSellerAgent --agent AAMPBuyerAgent
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_external_deployer():
    """Import ExternalAgentDeployer from the external-agents deployer module.

    Loaded by file path because ``external-agents`` contains a hyphen and so is
    not importable as a package. Supports the directory being renamed with a
    leading underscore (``_external-agents``), which the repo uses when the
    external agents are temporarily backed out.
    """
    for dirname in ("external-agents", "_external-agents"):
        candidate = REPO_ROOT / dirname / "deploy_external_agents.py"
        if not candidate.exists():
            continue
        spec = importlib.util.spec_from_file_location(
            "aamp_external_deployer", candidate
        )
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.ExternalAgentDeployer
    return None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--region", required=True)
    p.add_argument("--stack-prefix", required=True)
    p.add_argument("--unique-id", required=True)
    p.add_argument("--pool-id", required=True, help="Cognito user pool id")
    p.add_argument("--client-id", required=True, help="Cognito app client id")
    p.add_argument(
        "--agent",
        action="append",
        default=[],
        help="Agent name to provision (repeatable, e.g. AAMPSellerAgent)",
    )
    p.add_argument("--profile", default=None)
    args = p.parse_args()

    if not args.agent:
        print("❌ No --agent given; nothing to provision", flush=True)
        return 1

    deployer_cls = _load_external_deployer()
    if deployer_cls is None:
        print(
            "❌ Could not locate external-agents/deploy_external_agents.py — "
            "cannot reuse the inbound credential provisioning. AAMP inbound "
            "credentials were NOT created.",
            flush=True,
        )
        return 1

    try:
        deployer = deployer_cls(region=args.region, profile=args.profile)
    except Exception as e:  # noqa: BLE001 - surface an actionable message
        print(f"❌ Could not initialize AWS clients: {e}", flush=True)
        return 1

    # Fail fast on a stale/deleted pool rather than creating a user in the wrong
    # place (or failing opaquely later at token-mint time).
    try:
        deployer.verify_cognito_pool_exists(args.pool_id)
    except RuntimeError as e:
        print(f"❌ {e}", flush=True)
        return 1

    failures = []
    for agent in args.agent:
        ssm_path = (
            f"/{args.stack_prefix}/a2a-inbound-tokens/{args.unique_id}/{agent}"
        )
        username = f"a2a-{agent.lower()}@example.com"
        try:
            deployer.ensure_inbound_cognito_credentials(
                pool_id=args.pool_id,
                client_id=args.client_id,
                ssm_path=ssm_path,
                username=username,
            )
            print(f"✅ {agent}: inbound A2A credentials stored at {ssm_path}", flush=True)
        except Exception as e:  # noqa: BLE001 - report per agent, keep going
            print(f"❌ {agent}: could not provision inbound credentials: {e}", flush=True)
            failures.append(agent)

    if failures:
        print(
            f"❌ Failed to provision: {', '.join(failures)}. Those runtimes "
            "require a bearer token, so calls to them will fail until their "
            "credentials exist.",
            flush=True,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
