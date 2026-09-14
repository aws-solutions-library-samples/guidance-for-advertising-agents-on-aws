#!/usr/bin/env python3
"""
Root orchestration script for deploying the whole AdCP buyer/seller demo:
shared Cognito setup, DynamoDB tables, both seller agents' AgentCore
runtimes, and the buyer agent's two AgentCore runtimes (HTTP + A2A) — in
the order dependencies actually require (Cognito before anything that
authorizes against it, tables before the agents that read/write them,
seller runtimes before the buyer agent's SELLER_AGENTS_JSON can point at
their real ARNs).

This script has NO Python dependencies of its own (stdlib only — json,
subprocess, re, argparse, pathlib, urllib.parse). It never imports boto3
or python-dotenv directly. Every AWS-touching or toolkit-touching step is
delegated via `subprocess` to the *target project's own* venv/uv-managed
Python (agents/buyer/reference-buyer/.venv, agents/seller/*/app/*/.venv)
or to the `agentcore` CLI binary already on PATH — this avoids needing a
root-level Python dependency story (pyproject.toml/requirements.txt at the
repo root) just to run this one orchestrator. If you ever want to import
this script's helpers from other root-level tooling, you'll need to add a
root venv with python-dotenv/boto3 at that point — not needed today.

Usage:

    python3 deploy_all.py                  # run everything, in order
    python3 deploy_all.py --dry-run        # print the plan, do nothing
    python3 deploy_all.py --only cognito   # run just one step
    python3 deploy_all.py --only 6         # steps can also be addressed by number

Steps (see STEPS below for the canonical list of names/numbers):
  1.  cognito              - create/reuse the shared Cognito user pool+client
  2.  propagate-cognito    - copy COGNITO_* from buyer .env into root .env
  3.  tables               - create/reuse the three DynamoDB tables
  3.7 cache-buckets        - create each ranking seller's semantic-cache S3
                             bucket and record the account-scoped prefix the
                             policy ARNs and CACHE_BUCKET are rendered from
  4.62 corpus              - build each seller's benchmark corpus the first
                             time, verify it every time after (the corpus is
                             version controlled, so this usually only verifies)
  4.65 publish-cache       - build and publish one cache artifact per ranking
                             seller, so a cold start finds a ranker rather
                             than falling back to keyword matching
  4.  vendor-session-module - copy the session-recording modules into both
                             seller packages (single source lives in the
                             buyer package; copies are gitignored)
  4.5 render-agentcore     - sync both sellers' agentcore.json auth blocks
  5.  deploy-ref-seller    - `agentcore deploy` for reference-seller
  6.  capture-ref-seller   - read its runtime ARN, write to 3 .env files
  9.  update-seller-json   - point each SELLER_AGENTS_JSON entry at its real
                             invoke URL, adding any entry that is missing
  10. deploy-buyer-http    - deploy_launch.py (HTTP runtime)
  11. deploy-buyer-a2a     - deploy_buyer_agent_a2a.py (A2A runtime)
  12. capture-buyer-arns   - read both runtimes' ARNs from their
                             .bedrock_agentcore.yaml files, write to 2 .env files
  13. deploy-ui            - publish the static chat UI (S3 + CloudFront)
                             via deploy_ui.py, using the just-captured
                             AGENT_RUNTIME_ARN (step 12) in .env
  14. verify-journey       - invoke the deployed buyer for real and assert the
                             Bind/Plan journey phases have genuine recorded
                             steps behind them (verify_governance_journey_live.py)

Explicitly OUT OF SCOPE for this script (per its own design — run this
manually, on purpose, when you actually need it):
  - agents/seller/reference-seller/app/adcpRefSeller/deploy_registry_registration.py
This is an optional registry-publish step, not part of the required
deploy sequence above.

Every step fails loudly and stops the whole run on a non-zero subprocess
exit or a missing expected file/value — deploys here have real ordering
dependencies (e.g. step 9 needs step 6's ARN), so silently continuing past
a failed step would just produce a more confusing failure two steps later.
"""

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
from pathlib import Path
from typing import Any

import aws_identity
import cache_buckets
import deployments

REPO_ROOT = Path(__file__).parent
ROOT_ENV_PATH = REPO_ROOT / ".env"

BUYER_DIR = REPO_ROOT / "agents" / "buyer" / "reference-buyer"
BUYER_ENV_PATH = BUYER_DIR / ".env"
BUYER_VENV_PYTHON = BUYER_DIR / ".venv" / "bin" / "python"

REF_SELLER_DIR = REPO_ROOT / "agents" / "seller" / "reference-seller"
REF_SELLER_APP_DIR = REF_SELLER_DIR / "app" / "adcpRefSeller"
REF_SELLER_APP_ENV_PATH = REF_SELLER_APP_DIR / ".env"
REF_SELLER_VENV_PYTHON = REF_SELLER_APP_DIR / ".venv" / "bin" / "python"
REF_SELLER_DEPLOYED_STATE_PATH = (
    REF_SELLER_DIR / "agentcore" / ".cli" / "deployed-state.json"
)

# The triton seller has no constants here because this script no longer touches it. It is absent from
# `STEPS`, `SELLER_REGISTRY`, the vendoring sources and targets, the cache steps and the trust-anchor
# step. Poseidon -- its anonymised duplicate -- carries the audio inventory.
#
# The tree is still present and still deployable by hand:
#
#     cd agents/seller/triton-seller && agentcore deploy -y
#
# Two things this removal does NOT do, both of which need a deliberate decision rather than a code
# change. It does not delete triton's already-deployed AWS resources (runtime, state table, cache
# bucket, ECR repo, CloudFormation stack); they persist until someone removes them. And it does not
# touch the `adcp-triton-seller-slm` bucket, which holds a copy of the unreproducible 9,000-brief audio
# corpus. Poseidon's bucket holds a byte-identical copy, so nothing depends on the triton one, but it
# should not be reclaimed casually.

#: The Poseidon seller: the audio inventory seller, and the home of the `cache_contract` and
#: `context_cache` sources that Gotham is vendored from. Named to leave "triton" free for the real,
#: external Triton agent. It shares NO AWS resource with the retired triton deploy -- its own state
#: table, cache bucket, CloudFormation stack, IAM role and ECR repo, all derived from
#: `agentcore.json`'s `name` (`PoseidonSeller`). It does share the Cognito pool and the sessions
#: table, deliberately: one pool authenticates the buyer against every seller, and the UI reads every
#: agent's recorded steps from one table.
POSEIDON_SELLER_DIR = REPO_ROOT / "agents" / "seller" / "poseidon-seller"
POSEIDON_SELLER_APP_DIR = POSEIDON_SELLER_DIR / "app" / "adcpPoseidonSeller"
POSEIDON_SELLER_APP_ENV_PATH = POSEIDON_SELLER_APP_DIR / ".env"
POSEIDON_SELLER_VENV_PYTHON = POSEIDON_SELLER_APP_DIR / ".venv" / "bin" / "python"
POSEIDON_SELLER_DEPLOYED_STATE_PATH = (
    POSEIDON_SELLER_DIR / "agentcore" / ".cli" / "deployed-state.json"
)

HTTP_RUNTIME_YAML_PATH = BUYER_DIR / ".bedrock_agentcore.yaml"
A2A_RUNTIME_YAML_PATH = BUYER_DIR / "a2a_runtime" / ".bedrock_agentcore.yaml"


def region() -> str:
    """The region to deploy into, from the active credentials.

    A function rather than a module constant because this module is imported by a test
    (`gotham_mock/tests/test_vendored_copies.py`) and must not reach AWS at import time.
    `aws_identity.region()` caches, so repeated calls cost nothing.
    """
    return aws_identity.region()


#: `aws_region.py` is copied into every package that resolves a region, so there is one definition of
#: "which region" rather than a literal in each of ~30 files. Vendored rather than imported because the
#: packages deploy as separate containers with flat imports and no shared parent on the path.
#:
#: Listed here with its destinations so a new package is a one-line change. The governance Lambda's
#: Dockerfile also names it explicitly in its COPY list -- that image has a narrow allowlist, and a
#: module missing from it fails on the first scheduled invocation rather than at build time.
VENDORED_REGION_MODULE = "aws_region.py"
REGION_MODULE_DESTINATIONS = (
    "agents/buyer/reference-buyer",
    "agents/buyer/reference-buyer/a2a_runtime",
    "agents/governance/reference-governance/app/adcpRefGovernance",
    "agents/seller/reference-seller/app/adcpRefSeller",
    # The poseidon and gotham sellers are not part of this repo -- only the reference buyer, seller
    # and governance agents are vendored here. Their entries are gone rather than commented, because
    # this tuple is iterated and a missing directory is a hard error ("REGION_MODULE_DESTINATIONS is
    # stale"), not a skip. Their step functions and SELLER_REGISTRY entries are already inactive.
)

# Single-source modules copied into each seller package before it deploys
# (see step_vendor_session_module). Source of truth lives in the buyer package
# because that's the only consumer that also needs them for local dev.
VENDORED_SESSION_MODULES = ("session_records.py", "seller_session_recorder.py")

GOTHAM_SELLER_DIR = REPO_ROOT / "agents" / "seller" / "gotham-seller"
GOTHAM_SELLER_APP_DIR = GOTHAM_SELLER_DIR / "app" / "adcpGothamSeller"
GOTHAM_SELLER_VENV_PYTHON = GOTHAM_SELLER_APP_DIR / ".venv" / "bin" / "python"
GOTHAM_MOCK_DIR = GOTHAM_SELLER_DIR / "app" / "gotham_mock"
GOTHAM_SELLER_APP_ENV_PATH = GOTHAM_SELLER_APP_DIR / ".env"
GOTHAM_SELLER_DEPLOYED_STATE_PATH = (
    GOTHAM_SELLER_DIR / "agentcore" / ".cli" / "deployed-state.json"
)

#: The reach service (Unit 4). Its **own** directory, its own `agentcore.json`, its own generated
#: Dockerfile — the steering rule about AgentCore Dockerfiles applies: two runtimes sharing one
#: Dockerfile path silently deployed the wrong entrypoint once already, with a READY status and
#: zero log output.
GOTHAM_REACH_DIR = REPO_ROOT / "agents" / "seller" / "gotham-reach-service"

#: The reference governance agent. Deployed through the `agentcore` CLI like the sellers (NOT the starter
#: toolkit the buyer uses), so its Dockerfile is tracked source rather than generated output.
GOVERNANCE_DIR = REPO_ROOT / "agents" / "governance" / "reference-governance"
GOVERNANCE_APP_DIR = GOVERNANCE_DIR / "app" / "adcpRefGovernance"
GOVERNANCE_DEPLOYED_STATE_PATH = (
    GOVERNANCE_DIR / "agentcore" / ".cli" / "deployed-state.json"
)
GOTHAM_REACH_APP_DIR = GOTHAM_REACH_DIR / "app" / "adcpGothamReach"
GOTHAM_REACH_DEPLOYED_STATE_PATH = (
    GOTHAM_REACH_DIR / "agentcore" / ".cli" / "deployed-state.json"
)

#: Every package that receives the vendored session modules.
SESSION_MODULE_TARGET_DIRS = (REF_SELLER_APP_DIR,)

#: The cache pipeline. Two near-identical copies exist in the repo, each defaulting to its own seller;
#: only poseidon's is used. Its copy knows `{poseidon, gotham}`, which since triton left the deploy is
#: exactly the set of sellers that read a cache artifact -- so one copy now covers every invocation
#: where three across two copies were needed before.
POSEIDON_CACHE_PIPELINE_DIR = POSEIDON_SELLER_DIR / "app" / "cache_pipeline"

#: `corpus_kit`, the seller-agnostic benchmark-corpus engine. A **neutral** home rather than one
#: seller's package, which is what `step_vendor_cache_modules` records as the option it wanted and
#: deferred. It could be deferred there and not here for one reason: nothing was already importing
#: `corpus_kit` from a seller tree, so choosing the neutral location cost no migration.
#:
#: Under `seller/` rather than a repo-level `agents/shared/` because the scope is exactly that: shared
#: *among sellers*. No leading underscore -- an earlier `_shared` collided with the meaning `_` carries
#: on `_retired-triton-seller`, where it marks something parked, and read as though this directory were
#: itself a seller.
#:
#: Deliberately **not vendored into the sellers.** The vendoring the cache packages need exists because
#: each AgentCore build context is isolated, and `corpus_kit` never enters an image -- it is offline
#: tooling, and a retriever must not be able to read the ground truth it is scored against. Each
#: seller's corpus tooling puts this directory on `sys.path` (see the seller's `_paths.py`), the same
#: way it already reads `context_cache` from the audio seller's source rather than a copy. One source,
#: no drift test needed, and a fresh clone can build and test a corpus before any deploy has run.
SHARED_SELLER_DIR = REPO_ROOT / "agents" / "seller" / "shared"
CORPUS_KIT_DIR = SHARED_SELLER_DIR / "corpus_kit"

#: Sellers with a benchmark corpus, and the script that builds or verifies it. Each entry is
#: `(directory, seller)`; the script is `deploy_corpus.py` in that directory by convention.
#:
#: Poseidon is absent on purpose: its corpus predates this tooling, is seeded into the cache bucket by
#: `deploy_cache_corpus.py` during step 4.65, and was produced by a generator that no longer exists.
#: Adding it here would imply this step could rebuild it, which it cannot.
CORPUS_SELLER_DIRS = ((GOTHAM_SELLER_DIR / "app" / "gotham_corpus", "gotham"),)

#: The context-cache packages, and where their single source lives. The poseidon seller's package is
#: their home; other sellers get copies (see step_vendor_cache_modules for why the source was not
#: relocated to a neutral directory).
#:
#: **The source moved from triton to poseidon when triton left the deploy.** The two trees held tracked
#: forks of these packages that were functionally identical apart from naming in docstrings, so the move
#: is not a rewrite -- but it was not a no-op either: triton's copy carried
#: `ContextCache.join_values_for` and poseidon's did not, so the method was ported before the source
#: changed. Vendoring from a copy missing it would have removed a method Gotham's measurement path calls
#: and left `filter_precision` reporting 0.0000 for a retriever answering correctly, which is the exact
#: defect that method was added to fix.
VENDORED_CACHE_PACKAGES = ("cache_contract", "context_cache")
CACHE_MODULE_SOURCE_DIR = POSEIDON_SELLER_APP_DIR
CACHE_MODULE_TARGET_DIRS = (GOTHAM_SELLER_APP_DIR,)

#: Single-file modules vendored alongside the cache packages.
#:
#: **Empty, and that is a finding rather than an oversight.** `state.py` was going to be
#: vendored here on the assumption that it is seller-agnostic — it reads its table name from
#: `STATE_TABLE_NAME` and stores AdCP media buys, which have nothing to do with what kind of
#: inventory was sold. It is not: it imports `triton_api` and `triton_types`, and its
#: `Flight` entity carries `delivery`, `priority`, `pricing`, `pacing`, `targeting` and
#: `capping` from the audio seller's domain. Vendoring it would have dragged audio types
#: into a news publisher to reuse a DynamoDB wrapper. Gotham has its own `gotham_state.py`
#: instead.
VENDORED_CACHE_MODULES: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Small, dependency-free .env helpers (mirrors the pattern already used
# throughout this codebase — see deploy_cognito_setup.py::_upsert_env_values)
# ---------------------------------------------------------------------------


def read_env_value(env_path: Path, key: str) -> str | None:
    if not env_path.exists():
        return None
    pattern = re.compile(rf"^{re.escape(key)}=(.*)$")
    for line in env_path.read_text().splitlines():
        m = pattern.match(line.strip())
        if m:
            return m.group(1)
    return None


def upsert_env_values(env_path: Path, values: dict[str, str], dry_run: bool) -> None:
    """Write/replace key=value lines in env_path, preserving everything else.
    Same approach as deploy_cognito_setup.py::_upsert_env_values, reused here
    (not imported from there) so this script has no import-path dependency
    on any per-project venv.
    """
    if dry_run:
        for key, value in values.items():
            print(f"  [dry-run] would write {env_path}: {key}={value}")
        return

    if not env_path.exists():
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_text("")

    lines = env_path.read_text().splitlines()
    seen: set[str] = set()
    for i, line in enumerate(lines):
        stripped = line.strip()
        for key, value in values.items():
            if stripped.startswith(f"{key}="):
                lines[i] = f"{key}={value}"
                seen.add(key)

    for key, value in values.items():
        if key not in seen:
            lines.append(f"{key}={value}")

    env_path.write_text("\n".join(lines) + "\n")
    for key, value in values.items():
        print(f"  wrote {env_path}: {key}={value}")


def run_subprocess(cmd: list[str], cwd: Path, dry_run: bool, step_name: str) -> None:
    printable = " ".join(str(c) for c in cmd)
    if dry_run:
        print(f"  [dry-run] would run: {printable}")
        print(f"  [dry-run]   (cwd={cwd})")
        return

    print(f"  running: {printable}")
    # Flushed before the child inherits this fd, so "running: X" precedes X's own output in a
    # redirected log instead of appearing after it. See header() for the full reasoning.
    print(f"    (cwd={cwd})", flush=True)
    result = subprocess.run(cmd, cwd=str(cwd))
    if result.returncode != 0:
        print(f"\n!!! Step '{step_name}' FAILED (exit code {result.returncode}): {printable}")
        print("!!! Stopping deploy_all.py — later steps depend on this one having succeeded.")
        sys.exit(result.returncode)


#: Warnings raised by steps that do not stop the deploy.
#:
#: Collected rather than only printed inline, because this script runs ~30 steps and several of them are
#: minutes long. An inline warning at step 4.9 has scrolled well past the terminal's buffer by the time
#: `=== Done ===` appears, so a warning nobody re-prints is a warning nobody reads.
_WARNINGS: list[str] = []


def warn(message: str) -> None:
    """Record a non-fatal problem and print it now, using the script's existing `!!! WARNING:` shape.

    Printed AND collected: printed so it appears next to the step that caused it, collected so it is
    repeated at the end where it will actually be seen.
    """
    text = f"!!! WARNING: {message}"
    print(text)
    _WARNINGS.append(text)


def header(step_num: int, title: str) -> None:
    # flush=True is load-bearing when stdout is redirected. Python block-buffers a non-tty stdout while
    # subprocesses write to the same fd immediately, so without this a captured log shows each step's
    # header long after the output it labels -- during this deploy, headers for steps 3.55 to 4.98
    # were still sitting in the buffer while pytest results from 4.98 were already in the file. A log
    # whose failures cannot be attributed to a step is not much use for diagnosing a failed deploy.
    print(f"\n=== Step {step_num}: {title} ===", flush=True)


# ---------------------------------------------------------------------------
# Step implementations
# ---------------------------------------------------------------------------


def step_identity(dry_run: bool) -> None:
    """Resolve the target account from the credentials, and write it where the scripts read it.

    First on purpose, and not only for tidiness: several scripts read `AWS_ACCOUNT_ID` from `.env` at
    import (`deploy_ui.py` does `os.environ["AWS_ACCOUNT_ID"]`), and every bucket name in this project
    embeds the account because S3 bucket names are globally unique. Deriving it from
    `sts:GetCallerIdentity` rather than trusting whatever `.env` last held is what stops a deploy
    pointing at the account it was previously deployed to.
    """
    header(0, "Resolve the target AWS account and region from the current credentials")
    try:
        account = aws_identity.account_id()
        resolved_region = aws_identity.region()
        print(f"  {aws_identity.describe()}")
    except aws_identity.IdentityError as exc:
        print(f"!!! {exc}")
        sys.exit(1)

    # No region check here any more: there is nothing pinned for the credentials to disagree with. The
    # region is written into .env below, and every script reads it from there or resolves it the same
    # way, so the whole deploy follows one answer.
    values = {"AWS_ACCOUNT_ID": account, "AWS_REGION": resolved_region}
    upsert_env_values(ROOT_ENV_PATH, values, dry_run=dry_run)
    upsert_env_values(BUYER_ENV_PATH, values, dry_run=dry_run)


def step_governance_origin(dry_run: bool) -> None:
    """The governance agent's own CloudFront origin, and its `iss`.

    Runs early because it depends on nothing -- `deploy_governance_origin.py` reads no environment at
    all -- and because four other things reference the origin it creates: the JWKS publish, the
    revocation-list publish, the runtime's `GOVERNANCE_AGENT_URL`, and the buyer's brand.json. Creating
    it first means each of those simply reads a value that already exists.
    """
    header(2.5, "Governance agent CloudFront origin (its iss, and where its .well-known docs live)")
    run_subprocess(
        ["uv", "run", "python", "deploy_governance_origin.py", "--apply"],
        cwd=GOVERNANCE_APP_DIR,
        dry_run=dry_run,
        step_name="governance-origin",
    )
    _capture_governance_origin(dry_run)


def _capture_governance_origin(dry_run: bool) -> None:
    """Read the origin back out of agentcore.json and put it in the .env files that reference it."""
    config_path = GOVERNANCE_DIR / "agentcore" / "agentcore.json"
    if dry_run and not config_path.exists():
        print(f"  [dry-run] would read GOVERNANCE_AGENT_URL from {config_path}")
        return

    config = json.loads(config_path.read_text())
    origin = None
    for runtime in config.get("runtimes", []):
        for entry in runtime.get("envVars", []):
            if entry.get("name") == "GOVERNANCE_AGENT_URL":
                origin = entry.get("value")
    if not origin:
        if dry_run:
            print("  [dry-run] would capture GOVERNANCE_ORIGIN_URL once the origin exists")
            return
        print(f"!!! GOVERNANCE_AGENT_URL was not written into {config_path}.")
        sys.exit(1)

    print(f"  governance origin: {origin}")
    values = {"GOVERNANCE_ORIGIN_URL": origin}
    upsert_env_values(ROOT_ENV_PATH, values, dry_run=dry_run)
    upsert_env_values(BUYER_ENV_PATH, values, dry_run=dry_run)


def step_buyer_ui_origin(dry_run: bool) -> None:
    """The buyer's UI bucket, OAC and distribution -- without publishing anything to it.

    Split from the publish (step 13) because the two have different dependencies: the origin needs
    nothing, while `config.json` needs `AGENT_RUNTIME_ARN`, which does not exist until the runtime is
    deployed. Creating the origin here lets brand.json, the creative fixtures and the sellers' trust
    anchor all reference a real URL well before the runtime exists.
    """
    header(2.6, "Buyer UI CloudFront origin (bucket + OAC + distribution, no publish)")
    run_subprocess(
        [str(BUYER_VENV_PYTHON), "deploy_ui.py", "--origin-only"],
        cwd=BUYER_DIR,
        dry_run=dry_run,
        step_name="buyer-ui-origin",
    )
    _capture_buyer_ui_origin(dry_run)


def _capture_buyer_ui_origin(dry_run: bool) -> None:
    """Resolve the UI origin from CloudFront and distribute it to everything that reads it.

    `BUYER_BRAND_JSON_URL` is derived here rather than assembled in three places: the sellers read it
    as their governance trust anchor, and it is just this origin plus a fixed path.
    """
    if dry_run:
        print("  [dry-run] would resolve the UI distribution and write BUYER_UI_ORIGIN / "
              "BUYER_BRAND_JSON_URL")
        return

    # Must match deploy_ui.py's BUCKET_NAME exactly (both derive from INSTANCE_PREFIX), or this
    # lookup would search for a distribution in front of a bucket name the UI step never created.
    bucket = f"{os.environ['INSTANCE_PREFIX']}-buyer-agent-ui"

    result = subprocess.run(
        [
            "aws", "cloudfront", "list-distributions",
            "--query",
            f"DistributionList.Items[?contains(Origins.Items[0].DomainName, '{bucket}')]"
            ".DomainName | [0]",
            "--output", "text",
        ],
        capture_output=True,
        text=True,
    )
    domain = result.stdout.strip()
    if result.returncode != 0 or not domain or domain == "None":
        print(f"!!! No CloudFront distribution found in front of {bucket}.")
        print(f"!!!   aws: {result.stderr.strip()}")
        sys.exit(1)

    origin = f"https://{domain}"
    print(f"  buyer UI origin: {origin}")
    values = {
        "BUYER_UI_ORIGIN": origin,
        "BUYER_BRAND_JSON_URL": f"{origin}/.well-known/brand.json",
    }
    upsert_env_values(ROOT_ENV_PATH, values, dry_run=dry_run)
    upsert_env_values(BUYER_ENV_PATH, values, dry_run=dry_run)


def step_governance_signing_key(dry_run: bool) -> None:
    # Before the governance runtime and before the JWKS publish: the runtime refuses to start without a
    # resolvable key (jws.py raises SigningNotConfigured rather than issuing unsigned context), and
    # publishing a JWKS means reading this key's public half.
    header(2.7, "Governance agent KMS signing key (alias/<prefix>-governance-signing)")
    run_subprocess(
        ["uv", "run", "python", "deploy_signing_key.py", "--apply"],
        cwd=GOVERNANCE_APP_DIR,
        dry_run=dry_run,
        step_name="governance-signing-key",
    )


def step_identity_pool(dry_run: bool) -> None:
    """The Cognito Identity Pool the browser uses to read recorded sessions straight from DynamoDB.

    After step 3 (which creates the sessions table, whose ARN the policy names) and after step 1
    (which creates the user pool and app client the pool federates). Before step 13, which publishes
    `IDENTITY_POOL_ID` into the UI's `config.json` -- publish it earlier and the deployed UI carries
    an empty pool id and silently stays on the slower read path.

    This is what took the dashboard's poll from 1,089 ms to ~24 ms; ~843 ms of the old figure was
    AgentCore transport rather than work. See the script's docstring for what it grants, which is
    read-only but table-wide, and why per-user scoping is not expressible against this table's keys.
    """
    header(3.55, "Cognito Identity Pool for direct session reads from the browser")
    run_subprocess(
        [str(BUYER_VENV_PYTHON), "deploy_identity_pool.py", "--apply"],
        cwd=BUYER_DIR,
        dry_run=dry_run,
        step_name="identity-pool",
    )


def step_buyer_prerequisites(dry_run: bool) -> None:
    """The session bucket and execution role both buyer runtimes need before they can be configured.

    `deploy_configure.py` reads `EXECUTION_ROLE_ARN` at import and `runtime_env.py` reads
    `SESSION_STORAGE_BUCKET`, so neither runtime can be configured without these. Nothing created them
    before this step existed -- they were made by hand in the original account, which is invisible until
    the first deploy somewhere else.
    """
    header(3.5, "Buyer prerequisites: session bucket, then execution role")
    # Bucket first: the role's S3 grant is scoped to it, and creating the grant before the bucket would
    # be a policy naming something that does not exist.
    run_subprocess(
        [str(BUYER_VENV_PYTHON), "deploy_session_bucket.py", "--apply"],
        cwd=BUYER_DIR,
        dry_run=dry_run,
        step_name="buyer-prerequisites (session bucket)",
    )
    account = read_env_value(BUYER_ENV_PATH, "AWS_ACCOUNT_ID")
    if account:
        upsert_env_values(
            BUYER_ENV_PATH,
            # Must match deploy_session_bucket.py's bucket_name() (both derive from INSTANCE_PREFIX).
            {"SESSION_STORAGE_BUCKET": f"{os.environ['INSTANCE_PREFIX']}-buyer-agent-sessions"},
            dry_run=dry_run,
        )
    run_subprocess(
        [str(BUYER_VENV_PYTHON), "deploy_execution_role.py", "--apply"],
        cwd=BUYER_DIR,
        dry_run=dry_run,
        step_name="buyer-prerequisites (execution role)",
    )
    if account:
        upsert_env_values(
            BUYER_ENV_PATH,
            {
                # Must match ROLE_NAME in deploy_execution_role.py (both derive from INSTANCE_PREFIX).
                "EXECUTION_ROLE_ARN": (
                    f"arn:aws:iam::{account}:role/"
                    f"{os.environ['INSTANCE_PREFIX']}-buyer-agent-agentcore-execution"
                )
            },
            dry_run=dry_run,
        )


def step_vendor_region_module(dry_run: bool) -> None:
    """Copy `aws_region.py` into every package that resolves a region.

    Before any container is built, so each image carries the same resolver. Kept single-source for the
    reason `render_agentcore_auth.py`'s docstring gives about per-seller copies: a duplicated definition
    is one somebody edits in one place, and a region that disagrees between two packages produces
    resources whose names look right and whose ARNs match nothing.
    """
    header(3.6, "Vendor aws_region.py into every package that resolves a region")
    source = REPO_ROOT / VENDORED_REGION_MODULE
    if not source.exists():
        print(f"!!! {VENDORED_REGION_MODULE} is missing from the repo root.")
        sys.exit(1)

    for rel in REGION_MODULE_DESTINATIONS:
        destination = REPO_ROOT / rel / VENDORED_REGION_MODULE
        if not destination.parent.is_dir():
            print(f"!!! {rel} does not exist; REGION_MODULE_DESTINATIONS is stale.")
            sys.exit(1)
        if destination.exists() and destination.read_bytes() == source.read_bytes():
            print(f"  ok      {rel}/{VENDORED_REGION_MODULE}")
            continue
        if dry_run:
            print(f"  [dry-run] would copy -> {rel}/{VENDORED_REGION_MODULE}")
        else:
            shutil.copy2(source, destination)
            print(f"  copied  {rel}/{VENDORED_REGION_MODULE}")


#: The instance prefix: lowercase letters and digits, starts with a letter, <=20 chars. NO hyphens
#: or underscores -- it is embedded in AgentCore runtime names (which forbid hyphens) AND
#: CloudFormation stack names (which forbid underscores), so bare alphanumeric is the only charset
#: valid in every resource type. Each site adds its own separator. The <=20 cap keeps the longest
#: derived name (`<prefix>-poseidon-seller-slm`) under S3's 63-char ceiling.
_INSTANCE_PREFIX_PATTERN = re.compile(r"^[a-z][a-z0-9]{0,19}$")


def resolve_instance_prefix(value: str) -> str:
    """Validate the deployer-supplied `--prefix` (default 'adcp').

    This one string namespaces EVERY resource so multiple instances can share an account. It is the
    operator's global-uniqueness knob for S3 (bucket names are global across all accounts), so a
    second instance -- or a second account, or a fork -- MUST pass a distinct `--prefix`. Validated
    here, before any resource is touched, because a bad prefix that only fails at the first S3 create
    leaves a half-built instance behind.
    """
    prefix = (value or "").strip()
    if not _INSTANCE_PREFIX_PATTERN.match(prefix):
        raise ValueError(
            f"{prefix!r}: use lowercase letters and digits only, start with a letter, <=20 chars, "
            "NO hyphens or underscores (e.g. 'adcp', 'demo2', 'acme'). It is embedded in every S3 "
            "bucket, DynamoDB table, CloudFormation stack, AgentCore runtime, Cognito pool, IAM "
            "role, KMS alias and ECR repo -- and runtime names forbid hyphens while stack names "
            "forbid underscores, so only bare alphanumeric is safe everywhere."
        )
    return prefix


def resolve_prefix(explicit: str | None, new: bool, dry_run: bool) -> str:
    """Decide which instance this run targets, and record it in the local manifest.

    The prefix is an opaque unique id, not something a human is expected to remember, so this is
    where "which instance am I deploying?" is answered from the local manifest
    (`deployments.local.json`) rather than from a flag every time. Precedence, highest first:

      1. `--prefix X` (or exported `INSTANCE_PREFIX`) -- the operator named an instance explicitly.
         Validate it and upsert its (account, region) mapping.
      2. `--new` -- mint a fresh unique id, record it, and deploy a brand-new instance.
      3. Otherwise consult the manifest for THIS account+region:
         - exactly one recorded instance -> reuse it (the common re-deploy case);
         - none -> adopt `adcp`, the original single instance, so a pre-manifest deployment keeps
           its names (pass `--new` instead to start a second instance here);
         - more than one -> refuse to guess; the operator must pass `--prefix` or `--new`.

    Manifest writes are skipped on `--dry-run` so a preview never mutates local state. The account
    and region come from the live credentials via `aws_identity`, which is also what every resource
    name is derived against, so the manifest cannot record a mapping that disagrees with the deploy.
    """
    explicit = (explicit or "").strip() or os.environ.get("INSTANCE_PREFIX", "").strip()

    if explicit:
        prefix = resolve_instance_prefix(explicit)
        if not dry_run:
            deployments.record(prefix, aws_identity.account_id(), aws_identity.region())
        return prefix

    if new:
        prefix = resolve_instance_prefix(deployments.generate_prefix())
        if not dry_run:
            deployments.record(prefix, aws_identity.account_id(), aws_identity.region())
        else:
            print(f"  [dry-run] would mint and record a new instance id: {prefix}")
        return prefix

    account = aws_identity.account_id()
    resolved_region = aws_identity.region()
    matches = deployments.find(account, resolved_region)

    if len(matches) == 1:
        # Silent reuse is the common and correct case for a redeploy, but it is also how a
        # `deployments.local.json` copied from another checkout gets adopted without anyone noticing:
        # the prefix is inherited, every resource name derives from it, and the agents that already
        # carry those names belong to whoever created them. `Runtime.launch(auto_update_on_conflict=
        # True)` then UPDATES them instead of refusing, so the first sign of trouble is somebody else's
        # deployment changing under them.
        #
        # So say which instance is being adopted and when it was last touched. An operator who did not
        # create it has the information to stop, and `--new` mints a separate one.
        entry = matches[0]
        prefix = resolve_instance_prefix(entry["prefix"])
        print(
            f"  instance '{prefix}' (recorded in this account/region"
            f"{', created ' + str(entry.get('created_at')) if entry.get('created_at') else ''}"
            f"{', last deployed ' + str(entry.get('last_deployed_at')) if entry.get('last_deployed_at') else ''})"
        )
        print(
            "  Every resource name derives from it, and an existing runtime of that name will be "
            "UPDATED.\n"
            "  If this instance is not yours -- e.g. deployments.local.json came from another "
            "checkout --\n"
            "  stop now and re-run with --new, or --prefix <your-own-id>."
        )
        return prefix

    if not matches:
        prefix = "adcp"
        if not dry_run:
            deployments.record(prefix, account, resolved_region)
        return prefix

    known = ", ".join(sorted(e.get("prefix", "?") for e in matches))
    raise ValueError(
        f"{len(matches)} instances are recorded in account {account} / {resolved_region} "
        f"({known}). Pass --prefix <id> to target one of them, or --new to start another."
    )


def _runtime_name(base: str) -> str:
    """The AgentCore runtime name for a seller/governance runtime, given its base role name.

    render_agentcore_json.py names the runtime the BARE base (`RefSeller`, `RefGovernance`, ...) and
    prefixes only the project/stack name (`<prefix>RefSeller`). The runtime name is what the CLI uses
    as the deployed-state lookup key and as the `envVars` target in agentcore.json, so both the ARN
    captures and the env-var writes here key off the base, NOT the prefixed name. Verified against a
    real deployed-state.json (the `runtimes` key is the bare runtime name). This is deliberately a
    pass-through so the call sites read clearly and there is one documented place to change if the
    naming convention ever moves.
    """
    return base


def _resolve_cache_stack_prefix() -> str:
    """The prefix this account's cache buckets are named from.

    Now derived from the instance prefix: `INSTANCE_PREFIX` (set by `main()` from `--prefix`) is the
    single knob, and the cache buckets are just `<prefix>-poseidon-seller-slm` etc. A legacy exported
    `CACHE_STACK_PREFIX` still wins if someone pins it, for backward compatibility, but the default is
    no longer `adcp-<account>` -- it is the instance prefix itself, so caches namespace per instance
    like every other resource. The `.env` value is deliberately NOT consulted (it is written by this
    step; preferring it would keep a stale prefix across a retarget).
    """
    legacy = os.environ.get("CACHE_STACK_PREFIX", "").strip()
    if legacy:
        return cache_buckets.validate_prefix(legacy)
    return cache_buckets.validate_prefix(os.environ.get("INSTANCE_PREFIX", "adcp").strip() or "adcp")


def step_cache_buckets(dry_run: bool) -> None:
    """Create the semantic-cache bucket each ranking seller reads, and record the prefix.

    Nothing created these before. The consequence was not an error: the three sellers came up READY,
    got 403 reading `context-cache/latest.json` from a bucket name that only one account can own, fell
    back to keyword matching, and returned unranked results with no relevance score indefinitely.

    Before 4.55 (`render-aws-config`), which reads `CACHE_STACK_PREFIX` out of the buyer `.env` to
    render both the IAM policy documents and each seller's `CACHE_BUCKET`. After 3.6, because
    `deploy_cache_infrastructure.py` imports the vendored `aws_region`.
    """
    header(3.7, "Create the semantic-cache S3 buckets and record the account-scoped prefix")
    if dry_run:
        try:
            prefix = _resolve_cache_stack_prefix()
        except (aws_identity.IdentityError, cache_buckets.CacheBucketError) as exc:
            print(f"  [dry-run] could not resolve the prefix yet: {exc}")
            return
    else:
        try:
            prefix = _resolve_cache_stack_prefix()
        except (aws_identity.IdentityError, cache_buckets.CacheBucketError) as exc:
            print(f"!!! {exc}")
            sys.exit(1)

    print(f"  prefix: {prefix}")
    for seller, bucket in cache_buckets.all_bucket_names(prefix).items():
        print(f"    {seller:<9} s3://{bucket}")

    # Written where render_aws_config.py reads it, so the policy ARNs, the runtime's CACHE_BUCKET and
    # the bucket that actually gets created cannot disagree.
    upsert_env_values(BUYER_ENV_PATH, {"CACHE_STACK_PREFIX": prefix}, dry_run=dry_run)

    # Poseidon's copy of the provisioning script knows {poseidon, gotham}, which since triton left the
    # deploy is every seller that reads a cache artifact -- so one copy now covers both invocations
    # where three were needed across two copies before. Still driven from `cache_buckets.sellers()` so a
    # seller cannot be silently left without a bucket, which is the state that produced the 403.
    invocations = (
        (POSEIDON_CACHE_PIPELINE_DIR, "poseidon"),
        (POSEIDON_CACHE_PIPELINE_DIR, "gotham"),
    )
    covered = {seller for _, seller in invocations}
    missing = set(cache_buckets.sellers()) - covered
    if missing:
        print(f"!!! no invocation provisions {sorted(missing)}; this list is stale.")
        sys.exit(1)

    for cwd, seller in invocations:
        run_subprocess(
            [
                "uv",
                "run",
                "python",
                "deploy_cache_infrastructure.py",
                "--stack-prefix",
                prefix,
                "--seller",
                seller,
            ],
            cwd=cwd,
            dry_run=dry_run,
            step_name=f"cache-buckets ({seller})",
        )


def step_corpus(dry_run: bool) -> None:
    """Build each seller's benchmark corpus the first time; verify it every time after.

    **Normally builds nothing.** A corpus is source, not a build artifact: it cannot be regenerated
    identically once an LLM has phrased part of it, and every quality figure a seller has published is
    only interpretable against the exact file it was measured on. So `corpus/` is version controlled and
    this step's usual job is to prove the file on disk is still that file.

    The build path is for a **new seller** -- or for a corpus deliberately deleted to start a new
    baseline. It costs one Bedrock call per seed, which is why it is behind `--apply` in the script this
    invokes and why that script is invoked with `--apply` only here.

    Three checks, in increasing order of how quietly they fail:

    - the corpus parses, so a truncated file breaks on the line it was cut at;
    - its digest matches what `provenance.json` recorded, catching a hand-edit or a bad merge;
    - the **catalogue fingerprint** matches. Ground truth was computed against one catalogue. Move it
      and the corpus still parses, still hashes correctly, and now scores against inventory that may not
      exist -- the one failure here that produces plausible numbers rather than an error.

    Before 4.65 on purpose: the corpus is what scores an artifact, so it should exist before the
    artifact does. It needs no bucket and no cache prefix -- unlike the audio seller's corpus, this one
    is never uploaded, because a retriever must not be able to read the ground truth it is scored
    against.
    """
    header(4.62, "Build or verify each seller's benchmark corpus")

    if not CORPUS_KIT_DIR.is_dir():
        print(f"!!! {CORPUS_KIT_DIR} is missing; the corpus tooling cannot import corpus_kit.")
        sys.exit(1)

    for directory, seller in CORPUS_SELLER_DIRS:
        script = directory / "deploy_corpus.py"
        if not script.exists():
            print(f"!!! {script} is missing; CORPUS_SELLER_DIRS is stale.")
            sys.exit(1)
        run_subprocess(
            [sys.executable, "deploy_corpus.py", "--apply"],
            cwd=directory,
            dry_run=dry_run,
            step_name=f"corpus ({seller})",
        )


def step_publish_cache(dry_run: bool) -> None:
    """Build and publish one cache artifact per ranking seller, so the first request finds a cache.

    The heaviest step in the deploy: it embeds each seller's whole catalogue locally through ONNX
    Runtime (no Bedrock, no training) and uploads a ~30 MB archive. Minutes per seller.

    Before the seller deploys rather than after, so a cold start finds `latest.json` already in place.
    A runtime that starts without one is not broken -- it discloses `artifact_unavailable` and keyword
    matches until the next 300 s refresh -- but "correct within five minutes" is a worse thing to hand
    someone than "correct on the first request", and the verification at step 14 would race it.

    Gotham is published last on purpose: `build_gotham_artifact.py` reuses the ONNX model poseidon's
    pipeline downloads, rather than fetching a second copy of the same weights.
    """
    header(4.65, "Build and publish a semantic-cache artifact for each ranking seller")
    prefix = read_env_value(BUYER_ENV_PATH, "CACHE_STACK_PREFIX")
    if not prefix:
        if dry_run:
            print("  [dry-run] would read CACHE_STACK_PREFIX (written by step 3.7)")
            return
        print("!!! CACHE_STACK_PREFIX is not set. Run step 3.7 (cache-buckets) first.")
        sys.exit(1)

    # Poseidon seeds from the copy of the corpus tracked in its own tree, so no `--source` is needed.
    #
    # It used to be seeded from triton's *bucket*, because only triton's tree carried a local copy and
    # poseidon's `.local/` was empty -- which failed on the first run at poseidon rather than at the
    # seller that had the file. That was fixed by tracking the corpus in both trees, and it is why this
    # no longer has an ordering dependency between two sellers: poseidon's
    # `cache_pipeline/corpus/briefs-20260728-135410.jsonl` is byte-identical to the copy triton held.
    for cwd, seller, source in ((POSEIDON_CACHE_PIPELINE_DIR, "poseidon", None),):
        # The corpus gate (BR-18) blocks every stage that could produce a rankable cache until the
        # verified 9,000-brief corpus is in THIS seller's bucket. Satisfied rather than bypassed.
        run_subprocess(
            [
                "uv",
                "run",
                "python",
                "deploy_cache_corpus.py",
                "--stack-prefix",
                prefix,
                "--seller",
                seller,
                "--apply",
                *(["--source", source] if source else []),
            ],
            cwd=cwd,
            dry_run=dry_run,
            step_name=f"publish-cache corpus ({seller})",
        )
        # `snapshot` opens a new run and prints its id; the rest join it with `--latest`. Passing an
        # explicit run id from here would mean this script inventing one, and `run_pipeline.py` treats
        # a run id as the key its stage artifacts are stored under.
        for stage, extra in (
            ("snapshot", []),
            ("records", ["--latest"]),
            ("build", ["--latest"]),
            # `--yes` is required: `_confirm_publish` refuses a non-interactive publish without it,
            # and `stage_publish` then raises rather than moving the pointer unasked.
            ("publish", ["--latest", "--yes"]),
        ):
            run_subprocess(
                ["uv", "run", "python", "run_pipeline.py", stage, "--stack-prefix", prefix, *extra],
                cwd=cwd,
                dry_run=dry_run,
                step_name=f"publish-cache {stage} ({seller})",
            )

    gotham_bucket = cache_buckets.bucket_name(prefix, "gotham")
    run_subprocess(
        [
            "uv",
            "run",
            "--project",
            str(POSEIDON_CACHE_PIPELINE_DIR),
            "python",
            "build_gotham_artifact.py",
            "--publish",
            gotham_bucket,
        ],
        cwd=GOTHAM_MOCK_DIR,
        dry_run=dry_run,
        step_name="publish-cache (gotham)",
    )


def step_materialize_agentcore(dry_run: bool) -> None:
    # Must run before anything reads or writes an agentcore.json: the file is gitignored and therefore
    # absent in a fresh clone, so it has to be assembled from the shared base + per-agent fragment
    # first. Everything that fills account-specific values into it -- render-agentcore (auth block),
    # render-aws-config (CACHE_BUCKET + its agent-discovery glob), seller-trust-anchor, governance-origin
    # -- runs later and edits the file this step lays down. Rendering here also resets any values a
    # previous deploy wrote, which the later steps then repopulate for the current account.
    header(0.2, "Assemble each agent's agentcore.json from the shared base + per-agent fragment")
    run_subprocess(
        [sys.executable, "render_agentcore_json.py"],
        cwd=REPO_ROOT,
        dry_run=dry_run,
        step_name="materialize-agentcore",
    )


def step_render_aws_config(dry_run: bool) -> None:
    # Must precede every `agentcore deploy`: aws-targets.json is generated and therefore absent in a
    # fresh clone, and the CLI cannot load a deployment target without it. It also renders the IAM
    # policy documents the runtimes attach, which the CDK inlines verbatim.
    header(4.55, "Render aws-targets.json and the IAM policy documents for this account")
    # Not executed with its own --dry-run during a dry run: it resolves COGNITO_USER_POOL_ID, which
    # step 1 creates. In a dry run nothing has been created, so running it would report a missing pool
    # as a failure of a step that has not been reached yet.
    run_subprocess(
        [sys.executable, "render_aws_config.py"],
        cwd=REPO_ROOT,
        dry_run=dry_run,
        step_name="render-aws-config",
    )


def step_seller_trust_anchor(dry_run: bool) -> None:
    """Hand every seller the buyer's brand.json URL.

    Each seller resolves this document before trusting any JWKS a governance token names, and
    `governance_verification.py` raises rather than defaulting if it is unset -- an unset trust anchor
    would otherwise mean either resolving keys from somewhere unintended or accepting tokens with no
    anchor at all. Written into `agentcore.json` because the container does not receive `.env`.
    """
    header(4.56, "Give each seller the buyer's brand.json URL (governance trust anchor)")
    url = read_env_value(BUYER_ENV_PATH, "BUYER_BRAND_JSON_URL")
    if not url:
        if dry_run:
            print("  [dry-run] would write BUYER_BRAND_JSON_URL into each seller's agentcore.json")
            return
        print("!!! BUYER_BRAND_JSON_URL is not set. Run step 2.6 (buyer-ui-origin) first.")
        sys.exit(1)

    # Only the sellers this script deploys. Writing a trust anchor into the `agentcore.json` of a
    # runtime nothing deploys would leave a config edit with no deploy behind it, which is the kind of
    # half-applied state that reads as configured and is not. Poseidon and gotham are not vendored in
    # this repo at all, so they are absent here for the same reason triton was.
    for agent_dir, runtime_base in ((REF_SELLER_DIR, "RefSeller"),):
        _set_agentcore_env_var(
            agent_dir / "agentcore" / "agentcore.json",
            runtime_name=_runtime_name(runtime_base),
            name="BUYER_BRAND_JSON_URL",
            value=url,
            dry_run=dry_run,
        )


def step_governance_revocations(dry_run: bool) -> None:
    # After the key and the origin, before the runtime: a seller that receives a signed token fails
    # closed once the revocation list's declared next_update passes, so the document needs to exist
    # from the moment tokens can be issued.
    header(4.85, "Publish the governance agent's revocation list")
    run_subprocess(
        ["uv", "run", "python", "deploy_revocations.py", "--apply"],
        cwd=GOVERNANCE_APP_DIR,
        dry_run=dry_run,
        step_name="governance-revocations",
    )


def step_capture_governance(dry_run: bool) -> None:
    """Point GOVERNANCE_AGENTS_JSON at the governance runtime that was just deployed.

    Nothing wrote this before: it was hand-maintained, so it kept naming the runtime of whichever
    account it was first written in. The buyer resolves exactly one governance agent from it
    (AdCP's `account.governance_agents` carries `maxItems: 1`).
    """
    header(4.95, "Capture the governance runtime ARN into GOVERNANCE_AGENTS_JSON")
    arn = _read_runtime_arn_from_deployed_state(
        GOVERNANCE_DEPLOYED_STATE_PATH, _runtime_name("RefGovernance"), dry_run
    )
    if arn is None:
        print("  [dry-run] would rebuild GOVERNANCE_AGENTS_JSON from the governance runtime ARN")
        return
    print(f"  runtime ARN: {arn}")

    entry = {
        "id": "reference",
        "name": "AdCP Reference Governance Agent",
        "url": _build_invoke_url(arn, region()),
        "transport": "mcp",
        "auth_type": "cognito_bearer",
    }
    raw = read_env_value(BUYER_ENV_PATH, "GOVERNANCE_AGENTS_JSON")
    entries = json.loads(raw) if raw else []
    # Preserve any externally-hosted governance agent someone added by hand; replace only ours.
    others = [e for e in entries if e.get("id") != "reference"]
    upsert_env_values(
        BUYER_ENV_PATH,
        {"GOVERNANCE_AGENTS_JSON": json.dumps([entry, *others])},
        dry_run=dry_run,
    )
    upsert_env_values(ROOT_ENV_PATH, {"GOVERNANCE_RUNTIME_ARN": arn}, dry_run=dry_run)


def step_brand_json(dry_run: bool) -> None:
    # Before the sellers deploy, so their trust anchor resolves to a document that already exists. Its
    # content needs only the governance origin, which step 2.5 created.
    header(4.96, "Publish the buyer's brand.json (the sellers' governance trust anchor)")
    run_subprocess(
        [str(BUYER_VENV_PYTHON), "deploy_brand_json.py", "--apply"],
        cwd=BUYER_DIR,
        dry_run=dry_run,
        step_name="brand-json",
    )


def step_creative_fixtures(dry_run: bool) -> None:
    # Publishes the creative images verify_creative_and_media_buy_live.py fetches. Needs the UI origin
    # (step 2.6) and nothing else.
    header(13.5, "Publish the creative fixtures to the buyer UI origin")
    run_subprocess(
        [str(REF_SELLER_VENV_PYTHON), "deploy_creative_fixtures.py", "--apply"],
        cwd=REF_SELLER_APP_DIR,
        dry_run=dry_run,
        step_name="creative-fixtures",
    )


def step_agents_registry(dry_run: bool) -> None:
    # Rebuilds AGENTS_JSON from the A2A runtime ARN captured in step 12. Was never wired in, so the
    # chat UI's agent selector kept whatever ARN was last written by hand.
    header(12.5, "Rebuild AGENTS_JSON from the captured A2A runtime ARN")
    run_subprocess(
        [str(BUYER_VENV_PYTHON), "deploy_agents_registry.py", "--apply"],
        cwd=BUYER_DIR,
        dry_run=dry_run,
        step_name="agents-registry",
    )


#: Each package that needs its own virtualenv, and how to build it. Two shapes, because the packages
#: are not uniform: the buyer pins with `requirements.txt` and has no `pyproject.toml`, so `uv sync`
#: does not apply to it; the seller and governance apps have `pyproject.toml` + `uv.lock`.
#: The buyer installs `requirements-deploy.txt`, not `requirements.txt`. The latter is the container
#: image's manifest and the Dockerfile installs it; the former adds the deploy-time toolkit this
#: virtualenv needs to run deploy_launch.py, without shipping it inside the image.
VENV_PACKAGES: tuple[tuple[str, Path, str], ...] = (
    ("reference-buyer", BUYER_DIR, "requirements-deploy.txt"),
    ("reference-seller", REF_SELLER_APP_DIR, "sync"),
    ("reference-governance", GOVERNANCE_APP_DIR, "sync"),
)


#: Resolved once by `agentcore_cli()`.
_AGENTCORE_CLI: str | None = None


def agentcore_cli() -> str:
    """Return a path to the **npm** AgentCore CLI (`@aws/agentcore`), which this project deploys with.

    Not simply `"agentcore"`, because PATH order is not trustworthy here. Two different tools claim
    that name:

    * `@aws/agentcore` -- the npm CLI this project's agents are built for, and the only one with
      `deploy -y`.
    * `bedrock-agentcore-starter-toolkit` -- the older Python CLI. The parent repo installs it into
      `.venv-deployment`, and `deploy-ecosystem.sh` runs with that virtualenv active, so its
      `agentcore` shadows the npm one and every `agentcore deploy -y` fails with
      `No such option: -y` (exit 2).

    Candidates are probed with `deploy --help` and the first that actually accepts `-y` wins. That
    tests the capability being relied on rather than inferring it from a version string or a path.
    """
    global _AGENTCORE_CLI
    if _AGENTCORE_CLI:
        return _AGENTCORE_CLI

    candidates: list[str] = []
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(directory) / "agentcore"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            candidates.append(str(candidate))
    # npm's global bin is often absent from a PATH assembled by an activated virtualenv.
    try:
        prefix = subprocess.run(
            ["npm", "prefix", "-g"], capture_output=True, text=True, timeout=15
        ).stdout.strip()
        if prefix:
            candidate = Path(prefix) / "bin" / "agentcore"
            if candidate.is_file() and str(candidate) not in candidates:
                candidates.append(str(candidate))
    except Exception:  # noqa: BLE001 - npm being absent is a normal case
        pass

    rejected: list[str] = []
    for candidate in candidates:
        try:
            probe = subprocess.run(
                [candidate, "deploy", "--help"], capture_output=True, text=True, timeout=60
            )
        except Exception:  # noqa: BLE001
            rejected.append(f"{candidate} (could not be run)")
            continue
        if "-y" in (probe.stdout + probe.stderr):
            _AGENTCORE_CLI = candidate
            return candidate
        rejected.append(f"{candidate} (no -y option; likely the Python starter toolkit)")

    print("!!! No usable AgentCore CLI found. This project deploys with the npm CLI, `@aws/agentcore`.")
    for entry in rejected:
        print(f"!!!   rejected: {entry}")
    print("!!! Install it:  npm install -g @aws/agentcore")
    sys.exit(1)


def step_bootstrap_venvs(dry_run: bool) -> None:
    """Create each package's virtualenv before any step tries to run a Python out of one.

    `deploy_all.py` addresses interpreters by absolute path (`BUYER_VENV_PYTHON` and friends), and 14
    of its steps use the buyer's. Nothing created those virtualenvs, and `.venv` is gitignored, so on a
    fresh clone the first step to use one died with

        FileNotFoundError: .../agents/buyer/reference-buyer/.venv/bin/python

    at step 1, before anything was provisioned. The test step at 4.98 already tolerated the same
    absence ("no .venv (run `uv sync` in ...)"), so the gap was visible there but never fixed for the
    steps that cannot proceed without one.

    Idempotent: `uv` is content-addressed and caches, so re-running is cheap and keeps dependencies
    current rather than silently reusing a stale environment.
    """
    header(0.1, "Create each package's virtualenv (uv)")

    if not shutil.which("uv"):
        print("!!! `uv` is not on PATH, and every agent package needs a virtualenv built with it.")
        print("!!! Install it: curl -LsSf https://astral.sh/uv/install.sh | sh")
        print("!!!   or see https://docs.astral.sh/uv/getting-started/installation/")
        sys.exit(1)

    for label, directory, mode in VENV_PACKAGES:
        if not directory.exists():
            warn(f"{label}: {directory} does not exist; no virtualenv was created.")
            continue

        if mode == "sync":
            run_subprocess(
                ["uv", "sync"], cwd=directory, dry_run=dry_run, step_name=f"bootstrap-venvs ({label})"
            )
        else:
            # No pyproject.toml here, so `uv sync` has nothing to resolve: build the env, then install
            # the pinned requirements into it.
            run_subprocess(
                ["uv", "venv"], cwd=directory, dry_run=dry_run, step_name=f"bootstrap-venvs ({label})"
            )
            # `--python .venv/bin/python` is load-bearing. Without it `uv pip install` targets an
            # already-active VIRTUAL_ENV, so running this from an activated virtualenv -- which
            # deploy-ecosystem.sh does -- installed the buyer's dependencies into that environment and
            # left `.venv` with an interpreter and nothing else. It reported success while doing it.
            run_subprocess(
                [
                    "uv", "pip", "install",
                    "--python", str(Path(".venv") / "bin" / "python"),
                    "-r", mode,
                ],
                cwd=directory,
                dry_run=dry_run,
                step_name=f"bootstrap-venvs ({label} requirements)",
            )

        interpreter = directory / ".venv" / "bin" / "python"
        if dry_run:
            continue
        if not interpreter.exists():
            print(f"!!! {label}: {interpreter} does not exist after bootstrapping.")
            print("!!! Later steps address that interpreter by absolute path and would fail on it.")
            sys.exit(1)
        # Existence is not enough. An install killed part-way leaves the interpreter in place with no
        # dependencies, and the next step then fails with ModuleNotFoundError instead of the missing
        # interpreter this step exists to prevent. Import boto3: every package's deploy scripts use it,
        # so it is the honest smoke test for "this environment is usable".
        probe = subprocess.run(
            [str(interpreter), "-c", "import boto3"], capture_output=True, text=True
        )
        if probe.returncode != 0:
            print(f"!!! {label}: {interpreter} exists but cannot import boto3.")
            print("!!! The environment is incomplete -- most likely an install that did not finish.")
            print(f"!!!   {probe.stderr.strip().splitlines()[-1] if probe.stderr.strip() else ''}")
            sys.exit(1)
        print(f"  ok      {interpreter}")


def step_cognito(dry_run: bool) -> None:
    header(1, "Cognito setup (create/reuse shared user pool + app client)")
    run_subprocess(
        [str(BUYER_VENV_PYTHON), "deploy_cognito_setup.py"],
        cwd=BUYER_DIR,
        dry_run=dry_run,
        step_name="cognito",
    )


def step_cognito_m2m(dry_run: bool) -> None:
    # Machine-to-machine (client_credentials) auth: a hosted domain, a resource server with one scope,
    # and a CONFIDENTIAL app client alongside the browser's public one. Separate from step 1 because
    # `GenerateSecret` is immutable, so the public client cannot become the confidential one -- and
    # because a browser client must stay public. Writes COGNITO_ADDITIONAL_CLIENT_IDS into the buyer
    # .env, which step 4 then renders into every agent's allowlist.
    #
    # Idempotent, and safe to run before the domain finishes provisioning: it reuses whatever exists and
    # re-verifies by requesting a real token.
    header(1.5, "Cognito machine-to-machine client (client_credentials)")
    run_subprocess(
        [str(BUYER_VENV_PYTHON), "deploy_cognito_m2m.py", "--apply"],
        cwd=BUYER_DIR,
        dry_run=dry_run,
        step_name="cognito-m2m",
    )


def step_registry_oauth_provider(dry_run: bool) -> None:
    # The AgentCore Identity credential provider that the Agent Registry's URL-sync crawler uses to mint
    # tokens for outbound calls. Depends on step 1.5 having stored the client secret, and on the hosted
    # domain existing -- the pool's OIDC discovery document only advertises a token_endpoint once it does.
    header(1.6, "AgentCore Identity OAuth2 credential provider (registry URL sync)")
    run_subprocess(
        [str(BUYER_VENV_PYTHON), "deploy_registry_oauth_provider.py", "--apply"],
        cwd=BUYER_DIR,
        dry_run=dry_run,
        step_name="registry-oauth-provider",
    )


def step_propagate_cognito(dry_run: bool) -> None:
    header(2, "Propagate COGNITO_* values from buyer .env into root .env")
    keys = [
        "COGNITO_USER_POOL_ID",
        "COGNITO_CLIENT_ID",
        "COGNITO_DISCOVERY_URL",
        "COGNITO_REGION",
        "COGNITO_TEST_USERNAME",
        "COGNITO_TEST_USER_PASSWORD",
    ]
    if dry_run:
        print(f"  [dry-run] would read {keys} from {BUYER_ENV_PATH}")
        values = {k: (read_env_value(BUYER_ENV_PATH, k) or "<unset>") for k in keys}
    else:
        values = {}
        for key in keys:
            value = read_env_value(BUYER_ENV_PATH, key)
            if value is None:
                print(f"!!! {key} not found in {BUYER_ENV_PATH}. Run step 1 (cognito) first.")
                sys.exit(1)
            values[key] = value
    upsert_env_values(ROOT_ENV_PATH, values, dry_run=dry_run)


def step_tables(dry_run: bool) -> None:
    header(3, "DynamoDB tables (independent, run in any order)")
    run_subprocess(
        [str(REF_SELLER_VENV_PYTHON), "deploy_state_table.py"],
        cwd=REF_SELLER_APP_DIR,
        dry_run=dry_run,
        step_name="tables (reference-seller)",
    )
    # The poseidon and gotham state tables are gone with those sellers. Each ran
    # `deploy_state_table.py` with that seller's app dir as cwd, so on a tree without them a real
    # deploy raised FileNotFoundError here -- at step 3, before anything was created.
    run_subprocess(
        [str(BUYER_VENV_PYTHON), "deploy_sessions_table.py"],
        cwd=BUYER_DIR,
        dry_run=dry_run,
        step_name="tables (buyer sessions)",
    )


def step_vendor_session_module(dry_run: bool) -> None:
    header(4, "Vendor the session-recording modules into both seller packages")
    print(
        "  Both sellers record reasoning sessions into the buyer's sessions table\n"
        "  so the chat UI can show their progression. They can't import the\n"
        "  buyer's modules (each AgentCore build context is isolated), so the\n"
        "  single source is copied in here, immediately before each deploy, and\n"
        "  the copies are gitignored build artifacts — never hand-edited, and\n"
        "  regenerated every run so a stale copy can't ship."
    )
    for filename in VENDORED_SESSION_MODULES:
        source = BUYER_DIR / filename
        if not source.exists():
            print(f"!!! Source module {source} is missing.")
            sys.exit(1)
        for target_dir in SESSION_MODULE_TARGET_DIRS:
            target = target_dir / filename
            if dry_run:
                print(f"  [dry-run] would copy {filename} -> {target}")
                continue
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            print(f"  copied {filename} -> {target}")


def step_vendor_cache_modules(dry_run: bool) -> None:
    header(4.6, "Vendor the context-cache packages into the Gotham seller package")
    print(
        "  `cache_contract` and `context_cache` have one source, in the audio\n"
        "  seller's package, and the Gotham seller gets copies — the same\n"
        "  discipline the session modules already follow, and for the same reason:\n"
        "  each AgentCore build context is isolated, so a shared import path does\n"
        "  not exist at build time. The copies are gitignored build artifacts,\n"
        "  regenerated every run, and a test asserts they are byte-identical to the\n"
        "  source so the asymmetry cannot rot unnoticed.\n"
        "\n"
        "  Why the source was not relocated to a neutral shared home instead:\n"
        "  vendoring happens at deploy time, while the audio seller's 737 tests\n"
        "  import the contract locally and run before any deploy. A stale local\n"
        "  copy silently used by those tests would be a worse failure than an\n"
        "  asymmetric directory layout. Recorded as a follow-up, not a preference."
    )
    for package in VENDORED_CACHE_PACKAGES:
        source = CACHE_MODULE_SOURCE_DIR / package
        if not source.is_dir():
            print(f"!!! Source package {source} is missing.")
            sys.exit(1)
        target = GOTHAM_SELLER_APP_DIR / package
        if dry_run:
            print(f"  [dry-run] would copy {package}/ -> {target}")
            continue
        if target.exists():
            shutil.rmtree(target)
        # `__pycache__` and `.venv` are build output of the *source* tree; copying
        # them would put stale bytecode compiled against a different interpreter
        # into a container image.
        shutil.copytree(
            source,
            target,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".venv", ".pytest_cache"),
        )
        file_count = sum(1 for _ in target.rglob("*.py"))
        print(f"  copied {package}/ -> {target} ({file_count} .py files)")

    # Single-file modules, same source and same reasoning (Unit 5).
    for filename in VENDORED_CACHE_MODULES:
        source = CACHE_MODULE_SOURCE_DIR / filename
        if not source.exists():
            print(f"!!! Source module {source} is missing.")
            continue
        for target_dir in CACHE_MODULE_TARGET_DIRS:
            target = target_dir / filename
            if dry_run:
                print(f"  [dry-run] would copy {filename} -> {target}")
                continue
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            print(f"  copied {filename} -> {target}")


def step_render_agentcore(dry_run: bool) -> None:
    # One renderer for every agent, not one per seller. This used to invoke a per-seller
    # `render_agentcore_config.py` for reference-seller and triton-seller only, which left
    # gotham-seller, gotham-reach-service and reference-governance hand-maintained -- and pinned
    # `allowedClients` to a single app client, so a machine-to-machine client added by hand was
    # reverted on the next render. `render_agentcore_auth.py` discovers all agentcore.json files by
    # glob, so a new agent is covered without editing this step.
    header(4.5, "Render agentcore.json Cognito auth block for every agent")
    run_subprocess(
        [sys.executable, "render_agentcore_auth.py"],
        cwd=REPO_ROOT,
        dry_run=dry_run,
        step_name="render-agentcore (all agents)",
    )


def ensure_cdk_dependencies(agent_dir: Path, dry_run: bool, step_name: str) -> None:
    """Install the CDK project's npm dependencies if they are absent.

    `agentcore deploy` compiles `agentcore/cdk` with `tsc` as its "Build CDK project" phase, so a missing
    `node_modules` is not a warning -- it is a wall of TS2307 "cannot find module" errors that read like
    broken source rather than absent dependencies.

    This is easy to hit precisely because the layout is correct: `cdk/` is tracked source while
    `cdk/node_modules/` is ignored, so a fresh clone -- or a checkout that restores `cdk/` from history --
    has the project and none of its dependencies. Installing here rather than documenting a manual
    `npm ci` keeps `deploy_all.py` runnable from a clean tree.

    `npm ci` when a lockfile is present, for a reproducible tree that matches the committed
    `package-lock.json`; `npm install` only as a fallback when there is no lockfile.
    """
    cdk_dir = agent_dir / "agentcore" / "cdk"
    if not cdk_dir.exists():
        # No CDK project at all. Deliberately not running `agentcore create` here: for an agent whose
        # cdk/ was committed and has since gone missing, `create` would substitute default scaffolding
        # for the committed definition. Restore it from git instead.
        print(f"!!! No CDK project at {cdk_dir}.")
        print("!!!   If this agent was deployed before, its cdk/ is tracked source that has gone missing —")
        print("!!!   restore it (git log --diff-filter=D -- '<path>/cdk/*') rather than regenerating.")
        print("!!!   Only run `agentcore create` if this agent has genuinely never been deployed.")
        sys.exit(1)

    if (cdk_dir / "node_modules").exists():
        print(f"  cdk dependencies present: {cdk_dir / 'node_modules'}")
        return

    cmd = ["npm", "ci"] if (cdk_dir / "package-lock.json").exists() else ["npm", "install"]
    print(f"  cdk node_modules absent (correctly gitignored) — installing with `{' '.join(cmd)}`")
    run_subprocess(cmd, cwd=cdk_dir, dry_run=dry_run, step_name=f"{step_name} (cdk npm install)")


def step_deploy_ref_seller(dry_run: bool) -> None:
    header(5, "Deploy reference-seller (`agentcore deploy`)")
    ensure_cdk_dependencies(REF_SELLER_DIR, dry_run, "deploy-ref-seller")
    run_subprocess(
        [agentcore_cli(), "deploy", "-y"],
        cwd=REF_SELLER_DIR,
        dry_run=dry_run,
        step_name="deploy-ref-seller",
    )


def _read_runtime_arn_from_deployed_state(
    deployed_state_path: Path, runtime_key: str, dry_run: bool
) -> str | None:
    if dry_run and not deployed_state_path.exists():
        print(f"  [dry-run] would read runtime ARN from {deployed_state_path}")
        return None
    if not deployed_state_path.exists():
        print(f"!!! {deployed_state_path} does not exist. Did the deploy step run/succeed?")
        sys.exit(1)
    state = json.loads(deployed_state_path.read_text())
    try:
        arn = state["targets"]["default"]["resources"]["runtimes"][runtime_key]["runtimeArn"]
    except KeyError as exc:
        print(f"!!! Could not find runtimes.{runtime_key}.runtimeArn in {deployed_state_path}: {exc}")
        sys.exit(1)
    return arn


def step_capture_ref_seller(dry_run: bool) -> None:
    header(6, "Capture reference-seller's runtime ARN")
    arn = _read_runtime_arn_from_deployed_state(
        REF_SELLER_DEPLOYED_STATE_PATH, _runtime_name("RefSeller"), dry_run
    )
    if arn is None:
        arn = "<reference-seller-runtime-arn>"
    print(f"  runtime ARN: {arn}")
    upsert_env_values(ROOT_ENV_PATH, {"REFERENCE_SELLER_RUNTIME_ARN": arn}, dry_run=dry_run)
    upsert_env_values(BUYER_ENV_PATH, {"REFERENCE_SELLER_RUNTIME_ARN": arn}, dry_run=dry_run)
    upsert_env_values(REF_SELLER_APP_ENV_PATH, {"SELLER_RUNTIME_ARN": arn}, dry_run=dry_run)


# Step numbers 7 and 8 -- the triton seller's deploy and capture -- were removed and are deliberately
# NOT reassigned. The numbers appear in `--only N` invocations in shell history and in this repo's docs,
# so reusing 7 for a different agent would silently deploy the wrong thing for anyone holding the old
# command. Same reasoning that keeps the rest of the numbering fractional rather than tidy.
#
# The triton tree is still deployable by hand: `cd agents/seller/triton-seller && agentcore deploy -y`.


def step_deploy_poseidon_seller(dry_run: bool) -> None:
    header(8.1, "Deploy poseidon-seller (`agentcore deploy`)")
    ensure_cdk_dependencies(POSEIDON_SELLER_DIR, dry_run, "deploy-poseidon-seller")
    run_subprocess(
        [agentcore_cli(), "deploy", "-y"],
        cwd=POSEIDON_SELLER_DIR,
        dry_run=dry_run,
        step_name="deploy-poseidon-seller",
    )


def step_capture_poseidon_seller(dry_run: bool) -> None:
    header(8.2, "Capture poseidon-seller's runtime ARN")
    arn = _read_runtime_arn_from_deployed_state(
        POSEIDON_SELLER_DEPLOYED_STATE_PATH, _runtime_name("PoseidonSeller"), dry_run
    )
    if arn is None:
        arn = "<poseidon-seller-runtime-arn>"
    print(f"  runtime ARN: {arn}")
    upsert_env_values(ROOT_ENV_PATH, {"POSEIDON_SELLER_RUNTIME_ARN": arn}, dry_run=dry_run)
    upsert_env_values(
        POSEIDON_SELLER_APP_ENV_PATH, {"POSEIDON_SELLER_RUNTIME_ARN": arn}, dry_run=dry_run
    )


def step_deploy_gotham_seller(dry_run: bool) -> None:
    header(8.5, "Deploy gotham-seller (`agentcore deploy`)")
    ensure_cdk_dependencies(GOTHAM_SELLER_DIR, dry_run, "deploy-gotham-seller")
    run_subprocess(
        [agentcore_cli(), "deploy", "-y"],
        cwd=GOTHAM_SELLER_DIR,
        dry_run=dry_run,
        step_name="deploy-gotham-seller",
    )


def step_capture_gotham_seller(dry_run: bool) -> None:
    header(8.6, "Capture gotham-seller's runtime ARN")
    arn = _read_runtime_arn_from_deployed_state(
        GOTHAM_SELLER_DEPLOYED_STATE_PATH, _runtime_name("GothamSeller"), dry_run
    )
    if arn is None:
        arn = "<gotham-seller-runtime-arn>"
    print(f"  runtime ARN: {arn}")
    upsert_env_values(ROOT_ENV_PATH, {"GOTHAM_SELLER_RUNTIME_ARN": arn}, dry_run=dry_run)
    upsert_env_values(
        GOTHAM_SELLER_APP_ENV_PATH, {"GOTHAM_SELLER_RUNTIME_ARN": arn}, dry_run=dry_run
    )


def step_deploy_gotham_reach(dry_run: bool) -> None:
    header(8.7, "Deploy gotham-reach-service (`agentcore deploy`)")
    ensure_cdk_dependencies(GOTHAM_REACH_DIR, dry_run, "deploy-gotham-reach")
    run_subprocess(
        [agentcore_cli(), "deploy", "-y"],
        cwd=GOTHAM_REACH_DIR,
        dry_run=dry_run,
        step_name="deploy-gotham-reach",
    )


def step_capture_gotham_reach(dry_run: bool) -> None:
    """Capture the reach runtime's ARN and hand the seller its URL.

    This is the step that actually turns reach on. Until `REACH_SERVICE_URL` is set in the seller's
    environment, `_build_reach_port()` returns `NoReachConfigured` and every product reports reach
    as not configured, which is exactly what the deployed seller did before
    this unit existed.

    The reach service is deliberately **not** added to `SELLER_AGENTS_JSON`: it is not a seller and
    speaks no AdCP.
    """
    header(8.8, "Capture gotham-reach-service's runtime ARN and wire it into the seller")
    arn = _read_runtime_arn_from_deployed_state(
        GOTHAM_REACH_DEPLOYED_STATE_PATH, _runtime_name("GothamReach"), dry_run
    )
    if arn is None:
        arn = "<gotham-reach-runtime-arn>"
    print(f"  runtime ARN: {arn}")
    upsert_env_values(ROOT_ENV_PATH, {"GOTHAM_REACH_RUNTIME_ARN": arn}, dry_run=dry_run)

    url = _build_invoke_url(arn, region())
    print(f"  reach service URL: {url}")

    # **Both** places, and the second is the one that matters.
    #
    # `.env` is what a local `python main.py` reads. The *deployed* container reads
    # `agentcore.json`'s `envVars` — a `.env` file is not shipped into the image. Writing only
    # `.env` produced a seller that reported reach as "not configured" in production while
    # looking correctly configured on a laptop, which is the worst kind of half-wired.
    upsert_env_values(
        GOTHAM_SELLER_APP_ENV_PATH, {"REACH_SERVICE_URL": url}, dry_run=dry_run
    )
    _set_agentcore_env_var(
        GOTHAM_SELLER_DIR / "agentcore" / "agentcore.json",
        runtime_name=_runtime_name("GothamSeller"),
        name="REACH_SERVICE_URL",
        value=url,
        dry_run=dry_run,
    )


def _set_agentcore_env_var(
    config_path: Path, *, runtime_name: str, name: str, value: str, dry_run: bool
) -> None:
    """Upsert one `envVars` entry in an `agentcore.json`, leaving everything else untouched.

    Rewrites the file rather than patching text, so the JSON stays valid; `indent=2` matches what
    the `agentcore` CLI itself writes, keeping the diff to the one changed value.
    """
    config = json.loads(config_path.read_text())
    runtimes = [
        runtime for runtime in config.get("runtimes", []) if runtime.get("name") == runtime_name
    ]
    if not runtimes:
        print(f"!!! no runtime named {runtime_name!r} in {config_path}")
        sys.exit(1)

    for runtime in runtimes:
        env_vars = runtime.setdefault("envVars", [])
        for entry in env_vars:
            if entry.get("name") == name:
                if entry.get("value") == value:
                    print(f"  {config_path.name}: {name} already matches")
                    return
                entry["value"] = value
                break
        else:
            env_vars.append({"name": name, "value": value})

    if dry_run:
        print(f"  [dry-run] would write {config_path}: {name}={value}")
        return
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    print(f"  wrote {config_path}: {name}")


def _build_invoke_url(runtime_arn: str, region: str) -> str:
    encoded_arn = urllib.parse.quote(runtime_arn, safe="")
    return (
        f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/"
        f"{encoded_arn}/invocations?qualifier=DEFAULT"
    )


#: The sellers the buyer agent knows about, and where to find each one's ARN.
#:
#: **This replaced three near-identical 25-line blocks.** `step_update_seller_json` had one
#: hand-written block per seller — read the env var, fall back to deployed state, warn if
#: missing, find-or-append the entry — and adding The Gotham Gazette would have made a
#: fourth copy of a body that differed only in five strings. Worse, the copies had already
#: started to diverge in their warning text, which is how a maintainer learns there are
#: copies at all.
#:
#: `env_var` is read from the root or buyer `.env` first, with the deployed-state file as
#: fallback, exactly as before: the env var is what a human may have pinned deliberately,
#: and the state file is what the last deploy actually produced.
#: Sellers this stack used to deploy and no longer does. `step_update_seller_json` removes their entries
#: from the buyer's `SELLER_AGENTS_JSON`, so the buyer stops offering a seller nothing deploys.
#:
#: An explicit list, not "everything missing from `SELLER_REGISTRY`". A third-party production seller
#: that this script does not deploy is legitimately absent from the registry; inferring retirement from
#: absence would delete it.
RETIRED_SELLER_IDS: tuple[str, ...] = ("triton",)

SELLER_REGISTRY: tuple[dict[str, Any], ...] = (
    {
        "id": "reference",
        "name": "AdCP Reference Test Seller (sandbox, MCP)",
        "env_var": "REFERENCE_SELLER_RUNTIME_ARN",
        "state_path": REF_SELLER_DEPLOYED_STATE_PATH,
        "runtime_base": "RefSeller",
        "capture_step": "6 (capture-ref-seller)",
        "app_dir": REF_SELLER_APP_DIR,
    },
    # The triton seller is absent: it is no longer deployed here, so there is no ARN for this script to
    # resolve. See `RETIRED_SELLER_IDS` for how its stale entry is removed from the buyer's seller list
    # -- absence from this registry stops the entry being refreshed but does not remove it.
    # {
    #     "id": "poseidon",
    #     "name": "Poseidon Inventory Seller (sandbox, MCP)",
    #     "env_var": "POSEIDON_SELLER_RUNTIME_ARN",
    #     "state_path": POSEIDON_SELLER_DEPLOYED_STATE_PATH,
    #     "runtime_base": "PoseidonSeller",
    #     "capture_step": "8.2 (capture-poseidon-seller)",
    #     "app_dir": POSEIDON_SELLER_APP_DIR,
    # },
    # {
    #     "id": "gotham",
    #     "name": "The Gotham Gazette (sandbox, MCP)",
    #     "env_var": "GOTHAM_SELLER_RUNTIME_ARN",
    #     "state_path": GOTHAM_SELLER_DEPLOYED_STATE_PATH,
    #     "runtime_base": "GothamSeller",
    #     "capture_step": "8.6 (capture-gotham-seller)",
    #     "app_dir": GOTHAM_SELLER_APP_DIR,
    # },
)


def _seller_canonical_identity(seller: dict[str, Any]) -> str | None:
    """The seller's own `THIS_SELLER_URL`, read from its `governance_verification.py`.

    This is the value the seller's `verify_intent_token` compares an incoming governance token's `aud`
    against, so it is the only correct thing to put in the buyer's `agent_url` -- and it must be READ
    from the seller rather than restated here, or the two drift and every governed buy fails.

    They did drift, and this is the fix. No `SELLER_AGENTS_JSON` entry declared an `agent_url` at all, so
    `seller_agents.py` fell back to `url` -- the AgentCore *invocation* endpoint
    (`https://bedrock-agentcore.../runtimes/<arn>/invocations?qualifier=DEFAULT`). The governance agent
    duly minted tokens with that as `aud`, and every seller rejected them with
    `PERMISSION_DENIED: governance verification failed` because it expects
    `https://<name>-seller.example/adcp/mcp`. Both halves were internally consistent and disagreed with
    each other; nothing asserted they matched.

    Parsed rather than imported: this script runs on the repo's own interpreter, and importing a seller
    module would need that seller's venv and would execute its module-level configuration checks.

    Returns None when it cannot be resolved. The caller refuses to write a fallback, because a wrong
    `agent_url` produces exactly the silent-rejection failure above.
    """
    module = seller["app_dir"] / "governance_verification.py"
    if not module.exists():
        return None
    tree = ast.parse(module.read_text(encoding="utf-8"))

    def _assigned(source: ast.Module, name: str) -> ast.expr | None:
        for node in source.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets
            ):
                return node.value
        return None

    value = _assigned(tree, "THIS_SELLER_URL")
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    # One level of aliasing, which is how reference-seller declares it:
    # `from fixtures import DEFAULT_AGENT_URL` then `THIS_SELLER_URL = DEFAULT_AGENT_URL`.
    if isinstance(value, ast.Name):
        for sibling in sorted(seller["app_dir"].glob("*.py")):
            alias = _assigned(ast.parse(sibling.read_text(encoding="utf-8")), value.id)
            if isinstance(alias, ast.Constant) and isinstance(alias.value, str):
                return alias.value
    return None


def _resolve_seller_arn(seller: dict[str, Any], dry_run: bool) -> str | None:
    """This seller's runtime ARN: a pinned env value if there is one, else last deploy's."""
    arn = read_env_value(ROOT_ENV_PATH, seller["env_var"]) or read_env_value(
        BUYER_ENV_PATH, seller["env_var"]
    )
    if arn:
        return arn
    return _read_runtime_arn_from_deployed_state(
        seller["state_path"], _runtime_name(seller["runtime_base"]), dry_run
    )


def step_update_seller_json(dry_run: bool) -> None:
    ids = "/".join(f"'{seller['id']}'" for seller in SELLER_REGISTRY)
    header(9, f"Update buyer agent's SELLER_AGENTS_JSON {ids} entries")

    if dry_run and not any(seller["state_path"].exists() for seller in SELLER_REGISTRY):
        print(f"  [dry-run] would read {ids} ARNs and rebuild their invoke URLs")
        print(f"  [dry-run] would update SELLER_AGENTS_JSON's {ids} urls in {BUYER_ENV_PATH}")
        return

    arns = {
        seller["id"]: _resolve_seller_arn(seller, dry_run) for seller in SELLER_REGISTRY
    }

    if not any(arns.values()):
        print(
            "!!! No seller runtime ARN is available for any of "
            f"{ids}. Run the corresponding capture step(s) first."
        )
        sys.exit(1)

    for seller in SELLER_REGISTRY:
        if not arns[seller["id"]]:
            print(
                f"!!! WARNING: No {seller['env_var']} available. Skipping the "
                f"'{seller['id']}' entry update — run step {seller['capture_step']} to fix this."
            )

    raw = read_env_value(BUYER_ENV_PATH, "SELLER_AGENTS_JSON")
    if not raw:
        # Seed it rather than exiting. This step already knows how to add a missing seller -- the append
        # branch below writes a complete entry from `SELLER_REGISTRY` plus the ARN just resolved -- so an
        # absent key needs no more information than an absent entry does.
        #
        # Exiting here failed the deploy at step 9 with every earlier step's resources already created,
        # because one key was missing from a file the operator writes by hand. A `.env` copied from
        # `.env.example` carries the key; one written by hand, or predating its introduction, does not.
        print(f"  SELLER_AGENTS_JSON not present in {BUYER_ENV_PATH}; seeding it from this deploy.")
        entries: list[dict[str, Any]] = []
    else:
        entries = json.loads(raw)
    changed = False

    # Prune retired sellers, from an explicit list rather than by removing anything absent from
    # `SELLER_REGISTRY`. That inference would be wrong and destructive: a third-party production seller
    # this script does not deploy is not in the registry either, and dropping it would quietly remove
    # a non-sandbox seller the buyer can reach.
    #
    # Pruning is needed at all because this step otherwise only adds and updates. Removing triton from
    # the registry stopped it being refreshed but left the stale entry in place, so the buyer went on
    # offering a seller nothing deploys -- pointing at a runtime ARN that will eventually stop existing.
    for retired in RETIRED_SELLER_IDS:
        remaining = [entry for entry in entries if entry.get("id") != retired]
        if len(remaining) != len(entries):
            print(f"  removing retired '{retired}' entry")
            entries = remaining
            changed = True

    for seller in SELLER_REGISTRY:
        arn = arns[seller["id"]]
        if not arn:
            continue
        url = _build_invoke_url(arn, region())
        # `url` is WHERE to call the seller; `agent_url` is WHO the seller is. The buyer sends the latter
        # as `check_governance`'s `target_agent`, which becomes the token's `aud`, which the seller
        # compares against its own `THIS_SELLER_URL`. Two different things that must not be conflated.
        agent_url = _seller_canonical_identity(seller)
        if not agent_url:
            print(
                f"!!! Could not read '{seller['id']}'s own THIS_SELLER_URL from "
                f"{seller['app_dir'] / 'governance_verification.py'}.\n"
                "    Refusing to write the entry without it: with no `agent_url`, the buyer falls back to\n"
                "    the invoke URL, the governance token's `aud` is wrong, and every governed buy is\n"
                "    rejected PERMISSION_DENIED by the seller."
            )
            sys.exit(1)
        existing = [entry for entry in entries if entry.get("id") == seller["id"]]
        if not existing:
            entries.append(
                {
                    "id": seller["id"],
                    "name": seller["name"],
                    "url": url,
                    "agent_url": agent_url,
                    "transport": "mcp",
                    "auth_type": "cognito_bearer",
                }
            )
            changed = True
            print(f"  adding '{seller['id']}' entry -> {url}")
            print(f"    agent_url (its own identity) -> {agent_url}")
            continue
        for entry in existing:
            if entry.get("url") != url:
                entry["url"] = url
                changed = True
                print(f"  updating '{seller['id']}' url -> {url}")
            else:
                print(f"  SELLER_AGENTS_JSON's '{seller['id']}' url already matches: {url}")
            if entry.get("agent_url") != agent_url:
                entry["agent_url"] = agent_url
                changed = True
                print(f"  updating '{seller['id']}' agent_url -> {agent_url}")

    if not changed:
        print("  SELLER_AGENTS_JSON already up to date, no changes needed.")
        return

    new_raw = json.dumps(entries)
    upsert_env_values(BUYER_ENV_PATH, {"SELLER_AGENTS_JSON": new_raw}, dry_run=dry_run)


#: True when a buyer runtime was launched in this run before AGENTS_JSON existed. See
#: `step_relaunch_buyer_runtimes` for why that is a legitimate state and what resolves it.
_BUYER_LAUNCHED_WITHOUT_REGISTRY = False


def _note_registry_state_at_launch() -> None:
    global _BUYER_LAUNCHED_WITHOUT_REGISTRY
    if not read_env_value(BUYER_ENV_PATH, "AGENTS_JSON"):
        _BUYER_LAUNCHED_WITHOUT_REGISTRY = True
        print("  note: AGENTS_JSON is not set yet, so this runtime launches without it.")
        print("        Step 12.6 relaunches it once step 12.5 has built the registry.")


def step_deploy_buyer_http(dry_run: bool) -> None:
    header(10, "Deploy buyer agent HTTP runtime (deploy_launch.py, calls configure() itself)")
    if not dry_run:
        _note_registry_state_at_launch()
    run_subprocess(
        [str(BUYER_VENV_PYTHON), "deploy_launch.py"],
        cwd=BUYER_DIR,
        dry_run=dry_run,
        step_name="deploy-buyer-http",
    )


def step_deploy_buyer_a2a(dry_run: bool) -> None:
    header(11, "Deploy buyer agent A2A runtime (deploy_buyer_agent_a2a.py)")
    if not dry_run:
        _note_registry_state_at_launch()
    run_subprocess(
        [str(BUYER_VENV_PYTHON), "deploy_buyer_agent_a2a.py"],
        cwd=BUYER_DIR,
        dry_run=dry_run,
        step_name="deploy-buyer-a2a",
    )


def step_relaunch_buyer_runtimes(dry_run: bool) -> None:
    """Relaunch both buyer runtimes once AGENTS_JSON exists, but only if they came up without it.

    AGENTS_JSON and the A2A runtime are mutually dependent on a first deploy: the registry's
    `buyer-a2a` entry holds that runtime's own invoke URL, which does not exist until it is deployed,
    and deploying it builds the container's environment from the registry. Neither can genuinely go
    first, so the first pass launches without it and this step fixes the environment afterwards.

    Conditional on purpose. A relaunch rebuilds and pushes an image, which costs minutes, and on any
    redeploy AGENTS_JSON is already present at step 10 -- so the runtimes are correct from the first
    pass and there is nothing to redo. Relaunching unconditionally would add that cost to every deploy
    to fix a state that only a first deploy is ever in.
    """
    header(12.6, "Relaunch buyer runtimes with the completed AGENTS_JSON (first deploy only)")
    if dry_run:
        print("  [dry-run] would relaunch both buyer runtimes if they launched without AGENTS_JSON")
        return

    if not _BUYER_LAUNCHED_WITHOUT_REGISTRY:
        print("  AGENTS_JSON was already set when the runtimes launched; nothing to redo.")
        return

    if not read_env_value(BUYER_ENV_PATH, "AGENTS_JSON"):
        warn(
            "AGENTS_JSON is still unset after step 12.5, so the buyer runtimes carry an empty agent "
            "registry: the UI's agent selector will be empty and check_governance cannot name its "
            "caller. Run `python3 deploy_all.py --only agents-registry` then this step again."
        )
        return

    print("  The runtimes launched before AGENTS_JSON existed; relaunching both with it.")
    for script, step_name in (
        ("deploy_launch.py", "relaunch-buyer-runtimes (http)"),
        ("deploy_buyer_agent_a2a.py", "relaunch-buyer-runtimes (a2a)"),
    ):
        run_subprocess(
            [str(BUYER_VENV_PYTHON), script], cwd=BUYER_DIR, dry_run=dry_run, step_name=step_name
        )


def _read_agent_arn_from_yaml(yaml_path: Path, dry_run: bool) -> str | None:
    if dry_run and not yaml_path.exists():
        print(f"  [dry-run] would read agent_arn from {yaml_path}")
        return None
    if not yaml_path.exists():
        print(f"!!! {yaml_path} does not exist. Did the deploy step run/succeed?")
        sys.exit(1)
    # Avoid a hard dependency on PyYAML at the root level: agent_arn is a simple scalar line
    # (`agent_arn: arn:aws:...`), so targeted regexes over the raw file keep this script stdlib-only.
    #
    # Honor `default_agent:` rather than taking the first `agent_arn:`. Once a second instance is
    # deployed, this yaml holds MULTIPLE agents (e.g. `adcp_buyer_agent` AND `dev_buyer_agent`), and a
    # first-match regex would capture whichever agent the toolkit happened to write first -- the wrong
    # instance. The toolkit sets `default_agent` to the agent just configured, and its runtime id (and
    # therefore its ARN) begins with that agent's name, so we select the arn under `/runtime/<name>-`.
    text = yaml_path.read_text()
    default = re.search(r"^default_agent:\s*(\S+)\s*$", text, re.MULTILINE)
    if default:
        name = default.group(1)
        m = re.search(
            rf"^\s*agent_arn:\s*(\S*runtime/{re.escape(name)}-\S+)\s*$", text, re.MULTILINE
        )
        if m:
            return m.group(1)
        print(
            f"!!! {yaml_path} names default_agent {name!r} but has no matching agent_arn "
            f"(looked for 'runtime/{name}-...'). The deploy for this instance may not have run."
        )
        sys.exit(1)
    # No default_agent pointer (older single-agent yaml): the first agent_arn is unambiguous.
    m = re.search(r"^\s*agent_arn:\s*(\S+)\s*$", text, re.MULTILINE)
    if not m:
        print(f"!!! Could not find 'agent_arn:' in {yaml_path}")
        sys.exit(1)
    return m.group(1)


def step_capture_buyer_arns(dry_run: bool) -> None:
    header(12, "Capture buyer agent ARNs (HTTP + A2A runtimes)")
    http_arn = _read_agent_arn_from_yaml(HTTP_RUNTIME_YAML_PATH, dry_run)
    a2a_arn = _read_agent_arn_from_yaml(A2A_RUNTIME_YAML_PATH, dry_run)

    if http_arn is None:
        http_arn = "<agent-runtime-arn>"
    if a2a_arn is None:
        a2a_arn = "<buyer-agent-a2a-runtime-arn>"

    print(f"  AGENT_RUNTIME_ARN: {http_arn}")
    print(f"  BUYER_AGENT_A2A_RUNTIME_ARN: {a2a_arn}")

    values = {
        "AGENT_RUNTIME_ARN": http_arn,
        "BUYER_AGENT_A2A_RUNTIME_ARN": a2a_arn,
    }
    upsert_env_values(ROOT_ENV_PATH, values, dry_run=dry_run)
    upsert_env_values(BUYER_ENV_PATH, values, dry_run=dry_run)


def step_deploy_ui(dry_run: bool) -> None:
    header(
        13,
        "Publish the static chat UI to S3 + CloudFront (deploy_ui.py, idempotent)",
    )
    run_subprocess(
        [str(BUYER_VENV_PYTHON), "deploy_ui.py"],
        cwd=BUYER_DIR,
        dry_run=dry_run,
        step_name="deploy-ui",
    )


def step_verify_journey(dry_run: bool) -> None:
    # The only step that asserts the deployed system does what the UI claims. It invokes the real
    # buyer runtime with a real Cognito token and then reads back what the recorder wrote to
    # DynamoDB, so it exercises the buyer, both sellers' `sync_accounts`/`sync_governance`, the
    # governance agent's `sync_plans`, and the recording path the journey screen reads -- none of
    # which any earlier step verifies.
    #
    # Last on purpose: it needs every runtime already deployed and every ARN already captured.
    # It costs one model invocation per run, which is why it is a step you can run alone
    # (`--only 14`) rather than something folded into a deploy step.
    header(
        14,
        "Verify the Bind/Plan journey phases against real recorded steps",
    )
    run_subprocess(
        [str(BUYER_VENV_PYTHON), "verify_governance_journey_live.py"],
        cwd=BUYER_DIR,
        dry_run=dry_run,
        step_name="verify-journey",
    )


# ---------------------------------------------------------------------------
# Step registry + CLI
# ---------------------------------------------------------------------------


def step_governance_state_table(dry_run: bool) -> None:
    header(4.7, "Governance agent DynamoDB state table")
    run_subprocess(
        ["uv", "run", "python", "deploy_state_table.py", "--apply"],
        cwd=GOVERNANCE_APP_DIR,
        dry_run=dry_run,
        step_name="governance-state-table",
    )


def step_governance_jwks(dry_run: bool) -> None:
    # Publishes the public JWKS derived from KMS to the CloudFront distribution, and VERIFIES the
    # published URL returns JSON rather than the SPA's index.html -- the distribution rewrites 403 to
    # index.html, so a mislocated object returns HTML with status 200 and looks like success.
    #
    # Before the runtime deploy on purpose: a seller that receives a signed token must be able to fetch
    # the key, and publishing after would leave a window where issued tokens are unverifiable.
    header(4.8, "Publish the governance agent's public JWKS")
    run_subprocess(
        ["uv", "run", "python", "deploy_jwks.py", "--apply"],
        cwd=GOVERNANCE_APP_DIR,
        dry_run=dry_run,
        step_name="governance-jwks",
    )


def step_deploy_governance(dry_run: bool) -> None:
    # One deploy, not two. An earlier plan assumed GOVERNANCE_AGENT_URL could only be known after the
    # runtime existed, implying a deliberately-failed first deploy. That was wrong: the variable is only
    # the token's `iss`, and AdCP resolves keys from a DECLARED `jwks_uri`, not from `iss`. So the issuer
    # is a stable URL we choose in advance and there is no circular dependency.
    header(4.9, "Deploy the reference governance agent (`agentcore deploy`)")
    ensure_cdk_dependencies(GOVERNANCE_DIR, dry_run, "deploy-governance")
    run_subprocess(
        [agentcore_cli(), "deploy", "-y"],
        cwd=GOVERNANCE_DIR,
        dry_run=dry_run,
        step_name="deploy-governance",
    )

#: Each agent's own test suite: (label, directory, extra env, extra pytest args). The directory is where
#: `uv run pytest` finds both a virtualenv and the tests -- for every agent here the tests live beside the
#: code rather than in a `tests/` subdirectory.
#:
#: Two entries carry adjustments, and both are for the same underlying reason: a suite that imports from a
#: SIBLING package, which resolves in the container (via `agentcore.json`'s `PYTHONPATH`) and not in a bare
#: local run.
#:
#:   * **poseidon** needs `PYTHONPATH=../poseidon_mock`, because `main.py` imports `mock_backend` from it.
#:     The container gets `PYTHONPATH=/poseidon_mock` from `agentcore.json`; this is the local equivalent.
#:     With it, the suite passes.
#:   * **gotham** has `context_cache/tests` ignored. Those tests import `_paths`, which imports
#:     `catalog_snapshot`, and `artifact_publisher` -- the last of which lives in POSEIDON's
#:     `app/cache_pipeline/`, i.e. another agent's package. That is a real pre-existing defect in gotham's
#:     cache tests, not something this step should paper over with a PYTHONPATH into a different agent.
#:     Gotham's own 233 agent tests pass; the ignore is narrow and the step prints the reason every run.
#:
#: Both adjustments exist so this step does not warn on every deploy. A warning that always fires is one
#: nobody reads, which is precisely the failure this step was added to avoid.
_TEST_SUITES: list[tuple[str, Path, dict[str, str], list[str]]] = [
    ("reference-governance", GOVERNANCE_APP_DIR, {}, []),
    ("reference-seller", REF_SELLER_APP_DIR, {}, []),
    # The poseidon and gotham seller suites are gone with those sellers: a directory that is absent by
    # design produced a `warn()` on every single run, which devalues the warnings that mean something.
]

#: Printed by the test step so a narrowed suite is visible rather than silent. Not a `warn()`: it fires on
#: every run by construction, and a permanent warning devalues the ones that mean something.
_TEST_SUITE_NOTES: dict[str, str] = {}


def step_agent_tests(dry_run: bool) -> None:
    """Run each agent's suite and WARN on failure. The deploy is not blocked.

    Warning rather than blocking is deliberate (N5). A test suite is evidence about the code, and this
    script's job is to get the code deployed -- an operator mid-incident should not be unable to ship a
    fix because an unrelated agent has a red test. The warning is loud, repeated at the end, and names
    the command to reproduce.

    Deliberately NOT a Kiro hook: this repo cannot assume every contributor uses Kiro, and a check that
    only fires in one editor is a check that does not exist for everyone else.

    A missing `.venv` is reported as a skip rather than a failure -- an agent nobody has set up locally
    has no evidence to offer either way, and calling that a test failure would be a false negative.
    """
    header(4.98, "Run each agent's test suite (warns, never blocks)")
    for label, directory, extra_env, extra_args in _TEST_SUITES:
        if not directory.exists():
            warn(f"{label}: {directory} does not exist; its tests were not run.")
            continue
        if not (directory / ".venv").exists():
            print(f"  skipping {label}: no .venv (run `uv sync` in {directory} to enable)")
            continue
        note = _TEST_SUITE_NOTES.get(label)
        if note:
            print(f"  note ({label}): {note}")
        prefix = "".join(f"{key}={value} " for key, value in extra_env.items())
        command = ["uv", "run", "pytest", "-q", *extra_args]
        printable = f"{prefix}{' '.join(command)}  (cwd={directory})"
        if dry_run:
            print(f"  [dry-run] would run: {printable}")
            continue
        print(f"  running: {printable}")
        env = {**os.environ, **extra_env} if extra_env else None
        result = subprocess.run(command, cwd=str(directory), env=env)
        if result.returncode != 0:
            warn(
                f"{label}: tests FAILED (exit {result.returncode}). The deploy continued. "
                f"Reproduce with: cd '{directory}' && {prefix}{' '.join(command)}"
            )


#: Ordered so that every value is created before anything reads it.
#:
#: The shape is four phases. **Phase 0** resolves which account this is. **Phase 1** (1 - 3.5) creates
#: the things that depend on nothing: Cognito, both CloudFront origins, the signing key, the tables, and
#: the buyer's role and bucket. **Phase 2** (4 - 4.96) renders per-account config and deploys governance.
#: **Phase 3** (5 - 8.8) deploys the sellers. **Phase 4** (9 - 14) deploys the buyer, publishes content
#: and verifies.
#:
#: The origins are in phase 1 deliberately. Their URLs are referenced by the JWKS publish, the
#: revocation list, the governance runtime's `iss`, the buyer's brand.json and every seller's trust
#: anchor -- creating them first means each of those reads a value that already exists, rather than
#: needing a value propagated to it or a guard for its absence.
#:
#: Numbers stay fractional when inserting: they appear in `--only N` invocations in shell history and in
#: this repo's docs, so renumbering would silently invalidate them.
STEPS: list[tuple[int, str, callable]] = [
    (0, "identity", step_identity),
    # Before everything that runs a Python: 14 steps address the buyer's interpreter by absolute path
    # and `.venv` is gitignored, so a fresh clone has no interpreter to address until this has run.
    (0.1, "bootstrap-venvs", step_bootstrap_venvs),
    # Before everything: agentcore.json is gitignored and generated from agents/_shared/agentcore.base.json
    # plus each agent's agentcore.fragment.json. Needs no AWS, so it runs first; every later step that
    # touches an agentcore.json (governance-origin at 2.5, render-agentcore at 4.5, render-aws-config at
    # 4.55, seller-trust-anchor at 4.56) depends on the file existing.
    (0.2, "materialize-agentcore", step_materialize_agentcore),
    (1, "cognito", step_cognito),
    # Fractional for the same reason as 4.5/4.6: renumbering would invalidate `--only N` invocations
    # already in shell history and in this repo's docs. Both run after the pool exists and before
    # step 4 renders the allowlists that depend on them.
    (1.5, "cognito-m2m", step_cognito_m2m),
    (1.6, "registry-oauth-provider", step_registry_oauth_provider),
    (2, "propagate-cognito", step_propagate_cognito),
    # --- origins and the signing key: no upstream dependencies, so they come first ---
    (2.5, "governance-origin", step_governance_origin),
    (2.6, "buyer-ui-origin", step_buyer_ui_origin),
    (2.7, "governance-signing-key", step_governance_signing_key),
    (3, "tables", step_tables),
    (3.5, "buyer-prerequisites", step_buyer_prerequisites),
    # After 3 (the table its policy names) and 1 (the user pool it federates); before 13, which
    # publishes IDENTITY_POOL_ID into the UI's config.json.
    (3.55, "identity-pool", step_identity_pool),
    (3.6, "vendor-region-module", step_vendor_region_module),
    # 3.7 (cache-buckets) is inactive. The semantic cache serves only the poseidon and gotham sellers,
    # which are not part of this repo: `cache_buckets.SELLER_BUCKET_SUFFIXES` names exactly those two.
    # The step ran `deploy_cache_infrastructure.py` with cwd
    # `agents/seller/poseidon-seller/app/cache_pipeline`, a directory that does not exist here, so a
    # real deploy raised FileNotFoundError -- and it would have created two S3 buckets for sellers
    # nothing deploys. `--dry-run` did NOT catch this: it prints the command without checking the cwd.
    # 4.55 (render-aws-config) tolerates the absence: _seller_key_for returns None for any seller not
    # in SELLER_BUCKET_SUFFIXES, so no CACHE_BUCKET is rendered, and _cache_stack_prefix derives
    # `adcp-<account>` when CACHE_STACK_PREFIX is unset.
    #(3.7, "cache-buckets", step_cache_buckets),
    (4, "vendor-session-module", step_vendor_session_module),
    (4.5, "render-agentcore", step_render_agentcore),
    (4.55, "render-aws-config", step_render_aws_config),
    (4.56, "seller-trust-anchor", step_seller_trust_anchor),
    #(4.6, "vendor-cache-modules", step_vendor_cache_modules),
    # Before 4.65: the corpus is what scores an artifact, so it exists before the artifact does. Usually
    # a verification and nothing more -- the corpus is version controlled, because it cannot be rebuilt
    # identically once its phrasing stage has run.
    #(4.62, "corpus", step_corpus),
    # The heaviest step: a local ONNX embed of each catalogue plus a ~30 MB upload per seller. Before
    # the seller deploys, so a cold start finds a pointer rather than keyword-matching for 300 s.
    #(4.65, "publish-cache", step_publish_cache),
    (4.7, "governance-state-table", step_governance_state_table),
    (4.8, "governance-jwks", step_governance_jwks),
    (4.85, "governance-revocations", step_governance_revocations),
    (4.9, "deploy-governance", step_deploy_governance),
    (4.95, "capture-governance", step_capture_governance),
    (4.96, "brand-json", step_brand_json),
    # After the governance deploy and before the sellers: the suites are the last cheap check available
    # before anything else is published, and they warn rather than block so ordering here costs nothing.
    (4.98, "agent-tests", step_agent_tests),
    (5, "deploy-ref-seller", step_deploy_ref_seller),
    (6, "capture-ref-seller", step_capture_ref_seller),
    # 7 and 8 were the triton seller. Removed, and not reassigned -- see the comment where its step
    # functions used to be.
    #
    # 8.1/8.2 keep their fractional numbers even though 7/8 are now free, for the same reason:
    # renumbering would invalidate `--only N` invocations already in shell history and in this repo's
    # docs, and moving poseidon onto 7 would make the old triton command deploy poseidon instead.
    #(8.1, "deploy-poseidon-seller", step_deploy_poseidon_seller),
    #(8.2, "capture-poseidon-seller", step_capture_poseidon_seller),
    # Fractional, deliberately: inserting Gotham as a new step 9 would renumber every
    # later step, and the numbers appear in `--only` invocations people have in their
    # shell history and in this repo's docs. Same reason 4.5/4.6 are fractional.
    #(8.5, "deploy-gotham-seller", step_deploy_gotham_seller),
    #(8.6, "capture-gotham-seller", step_capture_gotham_seller),
    # The reach service, and the step that hands the seller its URL. After the seller's own
    # capture, because `capture-gotham-reach` writes into the seller's `.env` and running it first
    # would have that write overwritten by a subsequent seller capture.
    #(8.7, "deploy-gotham-reach", step_deploy_gotham_reach),
    #(8.8, "capture-gotham-reach", step_capture_gotham_reach),
    (9, "update-seller-json", step_update_seller_json),
    (10, "deploy-buyer-http", step_deploy_buyer_http),
    (11, "deploy-buyer-a2a", step_deploy_buyer_a2a),
    (12, "capture-buyer-arns", step_capture_buyer_arns),
    (12.5, "agents-registry", step_agents_registry),
    # Before 13 (deploy-ui): the UI reads its agent list from the HTTP runtime's /config, so the
    # runtime must be carrying the finished registry before the UI is published against it.
    (12.6, "relaunch-buyer-runtimes", step_relaunch_buyer_runtimes),
    (13, "deploy-ui", step_deploy_ui),
    (13.5, "creative-fixtures", step_creative_fixtures),
    (14, "verify-journey", step_verify_journey),
]

# NOTE on --skip-optional: the following step is intentionally NOT part
# of STEPS above, and this script never wires it in. It's an optional,
# manual, run-when-you-need-it registry-registration step:
#   - agents/seller/reference-seller/app/adcpRefSeller/deploy_registry_registration.py
# UI publishing (deploy_ui.py) used to be excluded too, but is now step 13
# of the default flow — it's no longer optional. There's nothing for
# --skip-optional to actually skip in this script's default flow since
# registry-registration was never included — this comment documents that
# decision per the original planning discussion.


def _resolve_selected_steps(only: str | None) -> list[tuple[int, str, callable]]:
    if only is None:
        return STEPS
    for num, name, fn in STEPS:
        if only == name or only == str(num):
            return [(num, name, fn)]
    valid = ", ".join(f"{num} ({name})" for num, name, _ in STEPS)
    print(f"!!! Unknown --only value {only!r}. Valid steps: {valid}")
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Orchestrate the full AdCP buyer/seller demo deployment."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print exactly what would be run/written, without executing or writing anything.",
    )
    parser.add_argument(
        "--only",
        default=None,
        help="Run just one step, by name (e.g. 'cognito') or number (e.g. '6').",
    )
    parser.add_argument(
        "--prefix",
        default=None,
        help=(
            "Target a specific instance by its unique-id prefix. Applied to EVERY AWS resource name "
            "(stacks, runtimes, DynamoDB tables, S3 buckets, Cognito, IAM, KMS, ECR, CloudFront) so "
            "multiple full instances of this stack coexist in one account. Lowercase "
            "letters/digits/hyphens, start with a letter, <=20 chars. If omitted, the instance for "
            "the current account+region is read from the local manifest (deployments.local.json); "
            "if none exists, 'adcp' (the original instance) is adopted. It is exported as "
            "INSTANCE_PREFIX so every sub-step inherits it."
        ),
    )
    parser.add_argument(
        "--new",
        action="store_true",
        help=(
            "Deploy a brand-new instance: mint a fresh auto-generated unique-id prefix, record it in "
            "the local manifest against this account+region, and namespace every resource by it. Use "
            "this to stand up a second full instance in an account that already has one."
        ),
    )
    parser.add_argument(
        "--skip-optional",
        action="store_true",
        help=(
            "No-op: the registry-registration step is already excluded from "
            "the default flow (see module docstring). UI-publish (deploy_ui.py) "
            "is now part of the default flow as step 13, not excluded. Kept "
            "for CLI compatibility with earlier planning."
        ),
    )
    args = parser.parse_args()

    # Resolve and validate the instance prefix ONCE, then export it. `run_subprocess` does not pass an
    # explicit `env=`, so every sub-step inherits this process's environment — exporting here is what
    # threads the prefix into the ~30 naming sites in the per-agent venvs without a flag on each call.
    try:
        prefix = resolve_prefix(args.prefix, args.new, args.dry_run)
    except ValueError as exc:
        print(f"!!! Invalid instance prefix: {exc}")
        sys.exit(2)
    except aws_identity.IdentityError as exc:
        print(f"!!! Cannot resolve account/region to select an instance: {exc}")
        sys.exit(2)
    os.environ["INSTANCE_PREFIX"] = prefix

    selected = _resolve_selected_steps(args.only)

    if args.dry_run:
        print("=== DRY RUN: no subprocesses will be executed, no files will be written ===")
    print(f"=== Instance prefix: {prefix} (every resource name derives from this) ===")

    for num, name, fn in selected:
        fn(args.dry_run)

    if _WARNINGS:
        # Immediately before `=== Done ===`, which is the one place a reader is certainly looking.
        print(f"\n=== {len(_WARNINGS)} warning(s) raised during this run ===")
        for text in _WARNINGS:
            print(text)

    print("\n=== Done ===" + (" (dry run, nothing was actually deployed)" if args.dry_run else ""))


if __name__ == "__main__":
    main()
