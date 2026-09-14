"""Render every account-scoped deploy config from the credentials in use.

    python3 render_aws_config.py --dry-run
    python3 render_aws_config.py

Two kinds of file are generated here, both from `aws_identity`:

1. `agents/*/*/agentcore/aws-targets.json` -- the account+region the `agentcore` CLI deploys to.
2. `**/app/*/*-policy.json` -- the IAM policy documents listed in each runtime's `additionalPolicies`,
   rendered from a committed `*-policy.template.json` sibling.

The generated files are gitignored. The templates are the source.

A third thing is written in place rather than generated: `CACHE_BUCKET` in each cache-reading seller's
`agentcore.json`. That file is committed -- it is the `agentcore` CLI's own source of truth -- so it
cannot be gitignored, and `reset_derived_env.py --scope publishable` removes the key again before the
tree is published. It is rendered here rather than committed because the bucket name is account-scoped
and the previous committed literal could only ever be created in one account; see `cache_buckets.py`.

## Why aws-targets.json cannot simply be committed

Its schema requires a literal 12-digit account (`// @regex ^[0-9]{12}` in
`agentcore/.llm-context/aws-targets.ts`), and the CLI does not check that value against the caller's
credentials. `agentcore deploy --dry-run` run under one account's credentials synthesised a template
naming a different account and reported success -- the account reached the CDK stack env, the asset
bucket, and every generated IAM policy. So a committed account number is not a note about where this
was once deployed, it is an instruction to deploy somewhere else.

## Why the policy documents need rendering rather than a wildcard

The `agentcore` CDK inlines these documents verbatim into the synthesised CloudFormation -- no
variable substitution of any kind, confirmed by finding our exact ARN strings in `cdk.out`. Writing
`${AWS::AccountId}` into one would produce that literal text in an ARN. The alternative,
`arn:aws:dynamodb:*:*:table/...`, would drop the account from the resource entirely, which
`.kiro/steering` forbids without a documented exception. Rendering keeps the grant exact.

## Discovery, not a list

Agents are found by globbing for the files that are always committed (`agentcore.json`,
`*-policy.template.json`), so a new agent is covered without editing this script. This mirrors
`render_agentcore_auth.py`, and for the same reason: a hard-coded list is a list someone has to
remember to extend, and the failure of forgetting shows up far from the cause.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import aws_identity
import cache_buckets

REPO_ROOT = Path(__file__).resolve().parent
BUYER_ENV_PATH = REPO_ROOT / "agents" / "buyer" / "reference-buyer" / ".env"

AGENT_CONFIG_GLOB = "agents/*/*/agentcore/agentcore.json"
POLICY_TEMPLATE_GLOB = "agents/*/*/app/*/*-policy.template.json"

#: The grant that identifies a cache-reading runtime. Used instead of a list of seller names, so the
#: variable and the permission cannot end up describing different sets of runtimes.
CACHE_POLICY_FILENAME = "context-cache-policy.json"

PLACEHOLDER_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")


class RenderError(RuntimeError):
    pass


def read_env_value(env_path: Path, key: str) -> str | None:
    if not env_path.exists():
        return None
    pattern = re.compile(rf"^{re.escape(key)}=(.*)$")
    for line in env_path.read_text().splitlines():
        m = pattern.match(line.strip())
        if m:
            return m.group(1).strip()
    return None


def _cognito_user_pool_id() -> str:
    value = read_env_value(BUYER_ENV_PATH, "COGNITO_USER_POOL_ID")
    if not value:
        raise RenderError(
            "COGNITO_USER_POOL_ID is not in "
            f"{BUYER_ENV_PATH.relative_to(REPO_ROOT)}. Run `python3 deploy_all.py --only cognito`."
        )
    return value


def _cache_stack_prefix() -> str:
    """The prefix the context-cache buckets are named from.

    `cache_pipeline/deploy_cache_infrastructure.py` composes bucket names as `prefix + suffix`, and
    the static policy documents have to agree with whatever that pipeline used, so they read the same
    value rather than repeating the resolved name.

    An authored `CACHE_STACK_PREFIX` still wins. Absent one, this derives `adcp-<account>` rather than
    failing: an unscoped prefix is claimable by exactly one account, and the literal `adcp` is already
    claimed, so requiring the operator to invent a prefix meant a fresh account got a 403 on every
    cache bucket and three sellers silently serving keyword results. See `cache_buckets.py` for the
    whole failure and why this reverses Infrastructure Design Q3=X for the default only.
    """
    value = read_env_value(BUYER_ENV_PATH, "CACHE_STACK_PREFIX")
    if value:
        return cache_buckets.validate_prefix(value)
    return cache_buckets.derive_stack_prefix(aws_identity.account_id())


def _instance_prefix() -> str:
    """The instance prefix (see deploy_all.resolve_prefix), for `${INSTANCE_PREFIX}` in policy
    templates that name per-instance tables, secrets and KMS aliases. deploy_all.py exports it;
    defaults to 'adcp' for a standalone render."""
    return os.environ.get("INSTANCE_PREFIX", "adcp").strip() or "adcp"


#: Resolved on demand, so an agent whose templates never mention a value does not require it. Each
#: entry is a zero-argument callable for that reason.
RESOLVERS: dict[str, callable] = {
    "AWS_ACCOUNT_ID": aws_identity.account_id,
    "AWS_REGION": aws_identity.region,
    "COGNITO_USER_POOL_ID": _cognito_user_pool_id,
    "CACHE_STACK_PREFIX": _cache_stack_prefix,
    "INSTANCE_PREFIX": _instance_prefix,
}


def resolve(name: str, cache: dict[str, str]) -> str:
    """One placeholder's value: an environment override if set, else the live lookup.

    The override exists so templates can be rendered and validated without AWS access at all -- a CI
    job checking that every template still produces valid JSON should not need credentials, or a KMS
    key. A deploy ignores these overrides for anything that matters, because the CLI and boto3 use the
    real credentials regardless of what was rendered.
    """
    if name in cache:
        return cache[name]
    override = os.environ.get(name, "").strip()
    if override:
        cache[name] = override
        return override
    resolver = RESOLVERS.get(name)
    if resolver is None:
        raise RenderError(f"Unknown placeholder ${{{name}}}. Known: {', '.join(sorted(RESOLVERS))}")
    cache[name] = resolver()
    return cache[name]


def substitute(text: str, cache: dict[str, str]) -> str:
    return PLACEHOLDER_RE.sub(lambda m: resolve(m.group(1), cache), text)


def render_policies(dry_run: bool, cache: dict[str, str]) -> int:
    templates = sorted(REPO_ROOT.glob(POLICY_TEMPLATE_GLOB))
    if not templates:
        raise RenderError(f"No policy templates matched {POLICY_TEMPLATE_GLOB!r}.")

    written = 0
    for template in templates:
        target = template.with_name(template.name.replace("-policy.template.json", "-policy.json"))
        rendered = substitute(template.read_text(), cache)

        # Parse before writing: a placeholder substituted into the wrong position produces invalid
        # JSON, and the agentcore CLI's failure for that is a schema error a long way from this file.
        try:
            json.loads(rendered)
        except json.JSONDecodeError as exc:
            raise RenderError(f"{template.relative_to(REPO_ROOT)} rendered to invalid JSON: {exc}")

        rel = target.relative_to(REPO_ROOT)
        if target.exists() and target.read_text() == rendered:
            print(f"  ok      {rel}")
            continue
        if dry_run:
            print(f"  WOULD   {rel}")
        else:
            target.write_text(rendered)
            print(f"  WROTE   {rel}")
        written += 1
    return written


def _seller_key(config_path: Path) -> str | None:
    """The `cache_buckets` seller key for an agent directory, or None if it isn't one.

    `agents/seller/poseidon-seller/agentcore/agentcore.json` -> `poseidon`. Derived from the directory
    rather than from a table, so a new seller is covered by adding its suffix in one place. Anything
    that is not a `*-seller` directory, or is a seller with no cache (the reference seller), returns
    None and is skipped.
    """
    name = config_path.parents[1].name
    if not name.endswith("-seller"):
        return None
    key = name[: -len("-seller")]
    return key if key in cache_buckets.SELLER_BUCKET_SUFFIXES else None


def render_cache_buckets(dry_run: bool, cache: dict[str, str]) -> int:
    """Upsert `CACHE_BUCKET` on every runtime that is granted read access to a cache bucket.

    Unlike `aws-targets.json` and the policy documents, `agentcore.json` is **committed** — the CLI's
    own source of truth — so this edits it in place and `reset_derived_env.py --scope publishable`
    removes the key again before the tree is published. `CACHE_BUCKET` is in that script's
    `DERIVED_AGENTCORE_ENV_VARS` for exactly this reason.

    A runtime is identified by its own `additionalPolicies` naming `context-cache-policy.json`, not by
    a list here. That grant and this variable have to describe the same bucket: the policy is what the
    platform enforces and the variable is what the process reads, so a runtime that has one without
    the other either cannot read the bucket it was told to use, or is granted a bucket it never opens.
    Keying off the grant makes the two unable to disagree about *which* runtimes are involved.
    """
    configs = sorted(REPO_ROOT.glob(AGENT_CONFIG_GLOB))
    if not configs:
        raise RenderError(f"No agentcore.json matched {AGENT_CONFIG_GLOB!r}.")

    written = 0
    touched = False
    candidates = 0
    for config_path in configs:
        seller = _seller_key(config_path)
        if seller is None:
            continue
        candidates += 1
        config = json.loads(config_path.read_text())
        changed = False
        for runtime in config.get("runtimes", []):
            if CACHE_POLICY_FILENAME not in (runtime.get("additionalPolicies") or []):
                continue
            touched = True
            bucket = cache_buckets.bucket_name(resolve("CACHE_STACK_PREFIX", cache), seller)
            rel = f"{config_path.relative_to(REPO_ROOT)}::{runtime.get('name')}"
            env_vars = runtime.setdefault("envVars", [])
            for entry in env_vars:
                if entry.get("name") == "CACHE_BUCKET":
                    if entry.get("value") == bucket:
                        print(f"  ok      {rel}   CACHE_BUCKET={bucket}")
                        break
                    entry["value"] = bucket
                    changed = True
                    break
            else:
                env_vars.append({"name": "CACHE_BUCKET", "value": bucket})
                changed = True
            if changed:
                print(f"  {'WOULD  ' if dry_run else 'WROTE  '} {rel}   CACHE_BUCKET={bucket}")

        if changed and not dry_run:
            config_path.write_text(json.dumps(config, indent=2) + "\n")
        if changed:
            written += 1

    if not touched:
        if candidates == 0:
            # No cache-reading seller is vendored in this tree at all. `cache_buckets`
            # `SELLER_BUCKET_SUFFIXES` names only poseidon and gotham, and neither is part of this
            # repo -- which is also why step 3.7 (cache-buckets) is inactive in deploy_all.py. There is
            # no runtime that could lose a bucket here, so there is nothing to report as a regression.
            print("  none    no cache-reading seller in this tree; no CACHE_BUCKET to write")
            return 0
        # A cache-capable seller IS present but no runtime carries the grant. That is the regression
        # this guard exists for: silence would leave those runtimes with no bucket configured and a
        # keyword fallback nobody asked for.
        raise RenderError(
            f"no runtime lists {CACHE_POLICY_FILENAME} in additionalPolicies, so no CACHE_BUCKET was "
            f"written, despite {candidates} cache-capable seller config(s) being present. Either the "
            "grant was removed from every seller, or this glob is stale."
        )
    return written


def render_aws_targets(dry_run: bool, cache: dict[str, str]) -> int:
    """One `aws-targets.json` per agent that has an `agentcore.json`.

    Keyed off `agentcore.json` rather than off existing `aws-targets.json` files, because the latter
    are generated and so absent in a fresh clone -- globbing for them would render nothing and report
    success.
    """
    configs = sorted(REPO_ROOT.glob(AGENT_CONFIG_GLOB))
    if not configs:
        raise RenderError(f"No agentcore.json matched {AGENT_CONFIG_GLOB!r}.")

    account = resolve("AWS_ACCOUNT_ID", cache)
    region = resolve("AWS_REGION", cache)
    payload = json.dumps([{"name": "default", "account": account, "region": region}], indent=2) + "\n"

    written = 0
    for config in configs:
        target = config.parent / "aws-targets.json"
        rel = target.relative_to(REPO_ROOT)
        if target.exists() and target.read_text() == payload:
            print(f"  ok      {rel}")
            continue
        if dry_run:
            existing = "absent"
            if target.exists():
                try:
                    existing = json.loads(target.read_text())[0].get("account", "?")
                except (json.JSONDecodeError, IndexError, KeyError):
                    existing = "unparseable"
            print(f"  WOULD   {rel}   ({existing} -> {account})")
        else:
            target.write_text(payload)
            print(f"  WROTE   {rel}   (account {account}, region {region})")
        written += 1
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Render account-scoped deploy config.")
    parser.add_argument(
        "--dry-run", action="store_true", help="report what would change, write nothing"
    )
    args = parser.parse_args()

    cache: dict[str, str] = {}
    try:
        # Best effort: with every placeholder overridden this runs with no credentials at all, which is
        # the point of the overrides. Failing here would defeat that.
        try:
            print(aws_identity.describe())
        except aws_identity.IdentityError as exc:
            print(f"(no live identity: {exc})")
        print()
        print("aws-targets.json")
        targets = render_aws_targets(args.dry_run, cache)
        print()
        print("IAM policy documents")
        policies = render_policies(args.dry_run, cache)
        print()
        print("agentcore.json CACHE_BUCKET")
        buckets = render_cache_buckets(args.dry_run, cache)
    except (RenderError, aws_identity.IdentityError) as exc:
        print(f"\n!!! {exc}", file=sys.stderr)
        return 1

    print()
    resolved = ", ".join(f"{k}={v}" for k, v in sorted(cache.items()))
    print(f"resolved: {resolved}")
    verb = "would change" if args.dry_run else "changed"
    print(
        f"{targets} target file(s), {policies} policy file(s) and {buckets} agentcore.json "
        f"{verb}."
    )
    if not args.dry_run and buckets:
        print(
            "  CACHE_BUCKET is a derived value in a committed file. "
            "`python3 reset_derived_env.py --scope publishable --apply` removes it again."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
