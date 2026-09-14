"""Assemble each agent's `agentcore.json` from one shared base and a per-agent fragment.

    python3 render_agentcore_json.py            # render every agent's agentcore.json
    python3 render_agentcore_json.py --check    # render in memory, fail if any on-disk file differs

`agentcore.json` is the `agentcore` CLI's source of truth, but almost all of it is identical across
the five CLI-deployed agents: the build type, entrypoint, runtime version, network mode, the JWT
authorizer shape and the dozen empty resource arrays. Committing that skeleton five times meant a
change to the common shape was five edits, and the copies drifted. So the shared skeleton lives once
in `agents/_shared/agentcore.base.json`, each agent commits only what differs in
`agentcore/agentcore.fragment.json`, and this script merges the two into the `agentcore.json` the CLI
reads.

The rendered `agentcore.json` is gitignored and regenerated every deploy, the same treatment
`aws-targets.json` and the `*-policy.json` documents already get from `render_aws_config.py`. Account
values are NOT this script's job: the auth block's `discoveryUrl`/`allowedClients` come from
`render_agentcore_auth.py`, `CACHE_BUCKET` from `render_aws_config.py`, and the CloudFront/reach/brand
URLs from their capture steps in `deploy_all.py`. This script only lays down the account-agnostic
shape those steps then fill in place, so it must run before any of them.

Agents are discovered by globbing the committed fragments, not the generated `agentcore.json` -- the
latter is absent in a fresh clone, so globbing it would render nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent

#: Instance prefix (see deploy_all.resolve_prefix): the auto-generated unique id every resource name
#: derives from. This script bakes it into each agent's runtime name, CloudFormation stack/project
#: name, and any env-var value containing the literal token `{prefix}` (table names, the sessions
#: table, the reach-credential secret id). Defaults to 'adcp' (the original instance) for a
#: standalone render; deploy_all.py exports INSTANCE_PREFIX so orchestrated runs inherit the right
#: one. The prefix is bare lowercase alphanumeric, so `<prefix>RefSeller` is a valid AgentCore
#: runtime name (which forbids hyphens) and `AgentCore-<prefix>RefSeller-default` a valid stack name.
INSTANCE_PREFIX = os.environ.get("INSTANCE_PREFIX", "adcp").strip() or "adcp"
BASE_PATH = REPO_ROOT / "agents" / "_shared" / "agentcore.base.json"
FRAGMENT_GLOB = "agents/*/*/agentcore/agentcore.fragment.json"

#: Added to the generated file (not the committed base) so the CLI and editors get schema validation.
#: Kept out of the committed base because that value is a remote URL some tooling refuses to author.
SCHEMA_URL = "https://schema.agentcore.aws.dev/v1/agentcore.json"

#: Runtime keys copied straight from the base unless the fragment's runtime overrides them.
_RUNTIME_BASE_KEYS = ("build", "entrypoint", "runtimeVersion", "networkMode", "authorizerType")


class RenderError(RuntimeError):
    """A fragment is malformed or missing a required field."""


def load_base() -> dict:
    return json.loads(BASE_PATH.read_text())


def fragments() -> list[Path]:
    return sorted(REPO_ROOT.glob(FRAGMENT_GLOB))


def _require(fragment: dict, path: Path, *keys: str) -> None:
    for key in keys:
        if key not in fragment:
            raise RenderError(f"{path.relative_to(REPO_ROOT)}: missing required key {key!r}")


def _apply_prefix_token(value: str) -> str:
    """Substitute the literal token `{prefix}` in an env-var value with the instance prefix.

    Env values are a mix of resource names that must be namespaced (`{prefix}-gotham-seller-state`)
    and plain config that must not be touched (`CACHE_REFRESH_SECONDS=300`). The token makes the
    distinction explicit in the fragment rather than guessing from the value, so a value with no
    token passes through unchanged.
    """
    return value.replace("{prefix}", INSTANCE_PREFIX)


def render_one(base: dict, fragment: dict, path: Path) -> dict:
    """Merge the shared base with one agent fragment into a full agentcore.json structure."""
    _require(fragment, path, "name", "runtime")
    frag_rt = fragment["runtime"]
    _require({"runtime.codeLocation": frag_rt.get("codeLocation"),
              "runtime.protocol": frag_rt.get("protocol")}, path,
             "runtime.codeLocation", "runtime.protocol")

    base_rt = base["runtimes"][0]
    # Two names come out of this, and they carry the prefix DIFFERENTLY on purpose:
    #   - the project/stack name is prefixed -> `<prefix><Base>`  (e.g. 'dev' + 'RefSeller')
    #   - the runtime name is the bare base  -> `<Base>`          (e.g. 'RefSeller')
    # AgentCore's deployed runtime name is `<projectName>_<runtimeName>`, so this yields
    # `<prefix><Base>_<Base>` (e.g. `devRefSeller_RefSeller`) rather than the doubled
    # `<prefix><Base>_<prefix><Base>` that putting the prefix on both would give. The prefix on the
    # project name is what makes the CloudFormation stack, the execution role and the deployed runtime
    # name unique per instance; the runtime name -- which is ALSO the deployed-state lookup key and the
    # log-group stem -- stays the bare base. Verified against a real deployed-state.json: the runtimes
    # key is the runtime name, runtimeId is `<projectName>_<runtimeName>-<suffix>`, and stackName is
    # `AgentCore-<projectName>-default`.
    project_name = f"{INSTANCE_PREFIX}{fragment['name']}"
    runtime_name = fragment["name"]

    # Runtime, assembled in a stable, readable key order. The order is cosmetic -- the CLI parses JSON
    # regardless -- but a stable order keeps the generated file diffable when someone inspects it.
    rt: dict = {"name": runtime_name}
    rt["build"] = base_rt["build"]
    rt["entrypoint"] = base_rt["entrypoint"]
    rt["codeLocation"] = frag_rt["codeLocation"]
    if "buildContextPath" in frag_rt:
        rt["buildContextPath"] = frag_rt["buildContextPath"]
    if "dockerfile" in frag_rt:
        rt["dockerfile"] = frag_rt["dockerfile"]
    rt["runtimeVersion"] = base_rt["runtimeVersion"]
    rt["networkMode"] = base_rt["networkMode"]
    rt["instrumentation"] = {
        "enableOtel": bool(frag_rt.get("enableOtel", base_rt["instrumentation"]["enableOtel"]))
    }
    rt["protocol"] = frag_rt["protocol"]
    rt["envVars"] = [
        {**entry, "value": _apply_prefix_token(str(entry.get("value", "")))}
        for entry in frag_rt.get("envVars", [])
    ]
    rt["additionalPolicies"] = frag_rt.get("additionalPolicies", [])
    rt["requestHeaderAllowlist"] = frag_rt.get("requestHeaderAllowlist", ["Authorization"])
    rt["authorizerType"] = base_rt["authorizerType"]
    rt["authorizerConfiguration"] = base_rt["authorizerConfiguration"]

    config: dict = {"$schema": SCHEMA_URL, "name": project_name, "version": base["version"],
                    "managedBy": base["managedBy"]}
    config["tags"] = {
        **base["tags"],
        "agentcore:project-name": fragment.get("projectName", project_name),
    }
    config["runtimes"] = [rt]
    # The empty resource arrays and anything else the base declares at the top level.
    for key, value in base.items():
        if key not in ("version", "managedBy", "tags", "runtimes"):
            config[key] = value
    return config


def target_for(fragment_path: Path) -> Path:
    return fragment_path.with_name("agentcore.json")


def serialize(config: dict) -> str:
    return json.dumps(config, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="render in memory and fail if any on-disk agentcore.json differs; writes nothing",
    )
    args = parser.parse_args()

    base = load_base()
    frags = fragments()
    if not frags:
        raise SystemExit(f"No fragments matched {FRAGMENT_GLOB!r} -- is the repo layout as expected?")

    drift = 0
    for frag_path in frags:
        fragment = json.loads(frag_path.read_text())
        config = render_one(base, fragment, frag_path)
        target = target_for(frag_path)
        rendered = serialize(config)
        rel = target.relative_to(REPO_ROOT)

        if args.check:
            current = target.read_text() if target.exists() else None
            if current != rendered:
                print(f"  DRIFT   {rel}")
                drift += 1
            else:
                print(f"  ok      {rel}")
            continue

        target.write_text(rendered)
        print(f"  render  {rel}  ({config['name']})")

    if args.check and drift:
        print(f"\n{drift} file(s) differ from what the base + fragment would render.")
        return 1
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
