"""Clear deploy-derived values out of every `.env`, so a deploy repopulates them for its own account.

    python3 reset_derived_env.py             # show what would be removed
    python3 reset_derived_env.py --apply     # back up, then remove

Every `.env` in this repo mixes two kinds of value:

* **authored** -- chosen by a human and true regardless of where this is deployed: a third-party
  seller's bearer token, the seller display order, the model id, table names.
* **derived** -- produced by a deploy step: runtime ARNs, the Cognito pool, bucket names, origins.

Retargeting to a different account invalidates every derived value and none of the authored ones. This
removes the derived keys rather than blanking them, because the deploy steps upsert by key and an empty
value reads as a configured-empty rather than absent -- `deploy_execution_role.py` refuses to build a
role around a session bucket naming a different account, which is exactly the failure a stale value
causes.

## Why removal matters more than tidiness

`deploy_all.py`'s seller-URL step prefers a `.env` value over the deployed state, so a leftover ARN
does not get corrected -- it gets *preferred*. The result is a freshly deployed buyer wired to the
previous account's sellers: every runtime healthy, every check green, answers coming from the wrong
place.

## Backups

Each file is copied to `<name>.backup-<account>-<timestamp>` before being touched, because these files
are the only record of how to reach the deployment they describe. The account in the filename is read
from the file itself, so the backup says which deployment it belongs to.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent

#: Values a deploy step produces. Removed on reset.
DERIVED_KEYS = frozenset(
    {
        # Identity of the target account itself.
        "AWS_ACCOUNT_ID",
        # Cognito, from deploy_cognito_setup.py / deploy_cognito_m2m.py.
        "COGNITO_USER_POOL_ID",
        "COGNITO_CLIENT_ID",
        "COGNITO_DISCOVERY_URL",
        "COGNITO_TEST_USER_PASSWORD",
        "COGNITO_M2M_CLIENT_ID",
        "COGNITO_M2M_TOKEN_ENDPOINT",
        "COGNITO_ADDITIONAL_CLIENT_IDS",
        # AgentCore runtime ARNs, from each agent's capture step.
        "AGENT_RUNTIME_ARN",
        "BUYER_AGENT_A2A_RUNTIME_ARN",
        "REFERENCE_SELLER_RUNTIME_ARN",
        "TRITON_SELLER_RUNTIME_ARN",
        "POSEIDON_SELLER_RUNTIME_ARN",
        "GOTHAM_SELLER_RUNTIME_ARN",
        "GOTHAM_REACH_RUNTIME_ARN",
        "GOVERNANCE_RUNTIME_ARN",
        "SELLER_RUNTIME_ARN",
        # IAM/S3/CloudFront, from the prerequisite and origin steps.
        "EXECUTION_ROLE_ARN",
        "SESSION_STORAGE_BUCKET",
        "REACH_SERVICE_URL",
        "GOVERNANCE_ORIGIN_URL",
        "BUYER_UI_ORIGIN",
        "BUYER_BRAND_JSON_URL",
        "REGISTRY_OAUTH_PROVIDER_ARN",
        # The semantic-cache buckets, from deploy_all.py's cache-buckets step. The prefix defaults to
        # `adcp-<account>`, so a leftover value names another account's buckets -- and the failure is
        # quiet: the runtime gets 403 on `latest.json`, falls back to keyword matching, and answers
        # every brief with unranked results. `CACHE_BUCKET*` covers the per-seller keys the
        # provisioning script writes (`CACHE_BUCKET_GOTHAM` and friends) alongside the bare one.
        "CACHE_STACK_PREFIX",
        "CACHE_BUCKET",
        "CACHE_BUCKET_TRITON",
        "CACHE_BUCKET_POSEIDON",
        "CACHE_BUCKET_GOTHAM",
        "CACHE_LOG_GROUP",
        "CACHE_LOG_GROUP_TRITON",
        "CACHE_LOG_GROUP_POSEIDON",
        "CACHE_LOG_GROUP_GOTHAM",
    }
)

#: Registries whose entries are partly in-account (rebuilt per deploy) and partly external (authored).
#: The key is kept with only its external entries so the rebuilding step has something to merge into.
JSON_REGISTRY_KEYS = ("SELLER_AGENTS_JSON", "AGENTS_JSON", "GOVERNANCE_AGENTS_JSON")

#: An in-account entry: its url is an AgentCore invoke endpoint, so it names a runtime ARN in whichever
#: account it was deployed to. Anything else (a third-party seller's public endpoint, say) is
#: authored and kept.
IN_ACCOUNT_URL = re.compile(r"bedrock-agentcore\.[a-z0-9-]+\.amazonaws\.com/runtimes/")


#: Deployment state written by tooling rather than by us, recording what was last deployed and where.
#:
#: `.bedrock_agentcore.yaml` is the starter toolkit's own record of the runtime it created. Retargeting
#: without clearing it makes the toolkit try to UPDATE a runtime id that exists only in the previous
#: account, which fails with `ResourceNotFoundException: Agent '<id>' was not found` -- an error that
#: names the agent rather than the account, so it reads as a broken deployment rather than a stale file.
#:
#: `deployed-state.json` is the `agentcore` CLI's equivalent. It is listed for completeness, though the
#: CLI keys off CloudFormation stack names and so recreates cleanly on its own.
STATE_FILE_GLOBS = (
    "agents/*/*/.bedrock_agentcore.yaml",
    "agents/*/*/*/.bedrock_agentcore.yaml",
    "agents/*/*/agentcore/.cli/deployed-state.json",
)


def env_files() -> list[Path]:
    """Every real `.env`, excluding CDK staging output."""
    return sorted(
        p
        for p in REPO_ROOT.rglob(".env")
        if ".venv" not in p.parts and "node_modules" not in p.parts and "cdk.out" not in p.parts
    )


#: `agentcore.json` env vars a deploy step writes. `agentcore.json` is now generated from
#: `agents/_shared/agentcore.base.json` + each agent's `agentcore.fragment.json` by
#: `render_agentcore_json.py` and is gitignored, so scrubbing it here is belt-and-suspenders: a clean
#: tree has no committed copy to publish, and the next deploy re-renders it from the account-agnostic
#: source anyway. Kept because the file still exists on disk locally between deploys, and stripping the
#: derived values keeps a local inspection honest. Every one of these is re-derived on the next deploy.
DERIVED_AGENTCORE_ENV_VARS = frozenset(
    {
        "GOVERNANCE_AGENT_URL",
        "GOVERNANCE_ORIGIN_DISTRIBUTION_ID",
        "REACH_SERVICE_URL",
        "BUYER_BRAND_JSON_URL",
        # Rendered by render_aws_config.py from an account-scoped prefix. It used to be a committed
        # literal (`adcp-triton-seller-slm`), which S3's global namespace made creatable in exactly one
        # account -- every other account got 403 and a silent keyword fallback. Removed rather than
        # blanked, like the rest: the seller runtimes read this with no default and disclose "cache
        # unavailable", whereas a stale value points at a bucket they cannot open.
        "CACHE_BUCKET",
    }
)

#: Matches render_agentcore_auth.py's PLACEHOLDER_DISCOVERY_URL. `.invalid` is reserved by RFC 2606, so
#: it can never resolve, and the shape still satisfies the CLI's schema.
PLACEHOLDER_DISCOVERY_URL = "https://cognito-idp.invalid/.well-known/openid-configuration"


def agentcore_configs() -> list[Path]:
    return sorted(REPO_ROOT.glob("agents/*/*/agentcore/agentcore.json"))


def process_agentcore_config(path: Path, apply: bool) -> bool:
    """Strip deploy-derived values out of an `agentcore.json`, leaving the committed shape."""
    config = json.loads(path.read_text())
    removed: list[str] = []
    reset_auth: list[str] = []

    for runtime in config.get("runtimes", []):
        kept = []
        for entry in runtime.get("envVars", []):
            if entry.get("name") in DERIVED_AGENTCORE_ENV_VARS:
                removed.append(f"{runtime.get('name')}.{entry['name']}")
                continue
            kept.append(entry)
        if kept != runtime.get("envVars", []):
            runtime["envVars"] = kept

        auth = (runtime.get("authorizerConfiguration") or {}).get("customJwtAuthorizer")
        if auth is None:
            continue
        if auth.get("discoveryUrl") != PLACEHOLDER_DISCOVERY_URL or "allowedClients" in auth:
            auth["discoveryUrl"] = PLACEHOLDER_DISCOVERY_URL
            auth.pop("allowedClients", None)
            reset_auth.append(str(runtime.get("name")))

    if not removed and not reset_auth:
        print(f"  unchanged  {path.relative_to(REPO_ROOT)}")
        return False

    print(f"  {path.relative_to(REPO_ROOT)}")
    for name in removed:
        print(f"      remove   envVar {name}")
    for name in reset_auth:
        print(f"      reset    auth block on {name}")

    if apply:
        path.write_text(json.dumps(config, indent=2) + "\n")
    return True


def state_files() -> list[Path]:
    found: list[Path] = []
    for pattern in STATE_FILE_GLOBS:
        found.extend(REPO_ROOT.glob(pattern))
    return sorted(set(found))


def process_state_file(path: Path, apply: bool, live_account: str | None) -> bool:
    """Move a state file aside only when it records a different account than the one in use.

    A state file naming the *current* account is the record of what was just deployed, and the capture
    steps read it -- moving that aside would throw away the ARNs the rest of the deploy depends on. So
    the account is compared rather than the file being cleared unconditionally.
    """
    text = path.read_text(errors="replace")
    account = account_in(text)
    rel = path.relative_to(REPO_ROOT)

    if live_account and account == live_account:
        print(f"  keep       {rel}   (already this account, {account})")
        return False
    if account == "unknown-account":
        print(f"  keep       {rel}   (no account recorded; nothing to compare)")
        return False

    print(f"  {rel}   (records {account}, live is {live_account or 'unknown'})")
    print("      move aside (tooling will recreate it for this account)")
    if not apply:
        return True

    backup = path.with_name(f"{path.name}.backup-{account}-{time.strftime('%Y%m%dT%H%M%S')}")
    shutil.move(str(path), str(backup))
    print(f"      backup   {backup.name}")
    return True


def account_in(text: str) -> str:
    """The account this file describes.

    An ARN is preferred over a bare 12-digit run, because these files can hold both and disagree. The
    starter toolkit's `.bedrock_agentcore.yaml` is the case that matters: after a retarget it carried
    `account: '<new>'` near the top and `agent_arn: arn:...:<old>:runtime/<old-id>` further down. Reading
    the first number found says "already the right account" and keeps a file whose only load-bearing
    field still names the previous deployment -- which is what produced
    `ResourceNotFoundException: Agent '<old-id>' was not found`.
    """
    # Most specific first: the runtime ARN is the resource whose existence decides whether the tooling
    # updates or creates, so it is the only field that can make a state file unusable. These files hold
    # several accounts at once -- after a retarget the buyer's yaml had a freshly-written IAM role ARN
    # in the NEW account on line 14 and the stale agent ARN in the OLD one on line 34, so both "first
    # number" and "first ARN" answer with the new account and conclude, wrongly, that nothing is stale.
    runtime_arn = re.search(r"arn:aws[a-z-]*:bedrock-agentcore:[^:]*:(\d{12}):runtime/", text)
    if runtime_arn:
        return runtime_arn.group(1)
    arn = re.search(r"arn:aws[a-z-]*:[^:]+:[^:]*:(\d{12}):", text)
    if arn:
        return arn.group(1)
    bare = re.search(r"(?<!\d)(\d{12})(?!\d)", text)
    return bare.group(1) if bare else "unknown-account"


def filter_registry(raw: str) -> tuple[str | None, list[str]]:
    """Drop in-account entries, keep the rest. Returns (new_raw or None, dropped ids)."""
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError:
        return None, []
    if not isinstance(entries, list):
        return None, []

    kept = [e for e in entries if not IN_ACCOUNT_URL.search(str(e.get("url", "")))]
    dropped = [
        str(e.get("id", "?")) for e in entries if IN_ACCOUNT_URL.search(str(e.get("url", "")))
    ]
    if not dropped:
        return None, []
    return json.dumps(kept), dropped


def process(path: Path, apply: bool) -> bool:
    original = path.read_text()
    lines = original.splitlines()
    account = account_in(original)

    out: list[str] = []
    removed: list[str] = []
    rewritten: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out.append(line)
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()

        if key in DERIVED_KEYS:
            removed.append(key)
            continue

        if key in JSON_REGISTRY_KEYS:
            new_raw, dropped = filter_registry(value)
            if new_raw is not None:
                out.append(f"{key}={new_raw}")
                rewritten.append(f"{key} (dropped: {', '.join(dropped)})")
                continue

        out.append(line)

    if not removed and not rewritten:
        print(f"  unchanged  {path.relative_to(REPO_ROOT)}")
        return False

    print(f"  {path.relative_to(REPO_ROOT)}   (account {account})")
    for key in removed:
        print(f"      remove   {key}")
    for note in rewritten:
        print(f"      rewrite  {note}")

    if not apply:
        return True

    backup = path.with_name(f"{path.name}.backup-{account}-{time.strftime('%Y%m%dT%H%M%S')}")
    shutil.copy2(path, backup)
    print(f"      backup   {backup.name}")
    path.write_text("\n".join(out).rstrip("\n") + "\n")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="back up and rewrite; else report only")
    parser.add_argument(
        "--scope",
        choices=("all", "env", "state", "agentcore", "publishable"),
        default="all",
        help=(
            "which files to reset. `state` is the one to reach for mid-deploy: clearing .env after "
            "some steps have run discards the ARNs they just captured, which the remaining steps read."
        ),
    )
    args = parser.parse_args()

    changed = 0
    if args.scope in ("all", "agentcore", "publishable"):
        configs = agentcore_configs()
        print(f"{len(configs)} agentcore.json file(s)\n")
        changed += sum(process_agentcore_config(p, args.apply) for p in configs)
        print()

    if args.scope in ("all", "env"):
        files = env_files()
        if not files:
            print("No .env files found.")
        else:
            print(f"{len(files)} .env file(s)\n")
            changed += sum(process(p, args.apply) for p in files)

    states = state_files() if args.scope in ("all", "state") else []
    if states:
        try:
            import aws_identity

            live_account: str | None = aws_identity.account_id()
        except Exception as exc:  # noqa: BLE001 - no credentials is a normal case for a dry run
            live_account = None
            print(f"\n(could not resolve the live account: {exc})")
        print(f"\n{len(states)} tooling state file(s)   live account: {live_account or 'unknown'}\n")
        changed += sum(process_state_file(p, args.apply, live_account) for p in states)

    print()
    if args.apply:
        print(f"{changed} file(s) rewritten. Backups alongside each original.")
        print("Run `python3 deploy_all.py` to repopulate for the current account.")
    else:
        print(f"Dry run. {changed} file(s) would change. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
