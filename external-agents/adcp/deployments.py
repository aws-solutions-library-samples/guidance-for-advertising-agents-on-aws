"""Local manifest of deployed stack instances.

Every AWS resource in this project is namespaced by a single instance prefix (see
`deploy_all.resolve_instance_prefix`). To let more than one full instance coexist in one AWS
account, the prefix is an opaque auto-generated unique id (e.g. `adcp-9f3a1c`) rather than
something derived from the account or region -- the account and region are deliberately kept OUT
of resource names (S3 bucket names especially), so the id is the only thing that distinguishes
two instances.

That makes a local record necessary: without it, an operator running `deploy_all.py` a second
time has no way to know which id belongs to the instance already in a given account/region, and
would either orphan it or clobber it. This module is that record -- a per-machine JSON file mapping
each unique-id prefix to the account + region it was deployed into, plus timestamps.

It is intentionally:
  - **local and gitignored** (`deployments.local.json`): it names live accounts and is per-machine
    state, not shared source.
  - **stdlib only**: it is imported by `deploy_all.py` before anything else and must not drag in a
    dependency or reach the network.

The account/region are stored here, NOT encoded into the prefix, which is exactly the mapping the
deployer asked for: unique-id -> (account, region).
"""

from __future__ import annotations

import datetime
import json
import re
import secrets
from pathlib import Path
from typing import Any

#: Repo root. This file lives at the repo root, so its parent is the root.
_ROOT = Path(__file__).resolve().parent

#: The manifest file. Gitignored (see `.gitignore`). Absent on a fresh clone, which is the
#: "no instances yet" state and is not an error.
MANIFEST_PATH = _ROOT / "deployments.local.json"

#: Prefixes must satisfy the same rule `deploy_all` validates against: lowercase letters and digits,
#: start with a letter, <=20 chars. NO hyphens or underscores: the prefix is embedded in AgentCore
#: runtime names (which allow only `[a-zA-Z0-9_]` -- no hyphen) AND CloudFormation stack names
#: (which allow only `[a-zA-Z0-9-]` -- no underscore), so the one charset safe in every resource
#: type is bare lowercase alphanumeric. Each naming site adds its own separator (a hyphen for S3,
#: an underscore for runtimes). The <=20 cap keeps the longest derived S3 name
#: (`<prefix>-poseidon-seller-slm`) under S3's 63-char ceiling. Kept in sync with
#: `deploy_all._INSTANCE_PREFIX_PATTERN` deliberately -- this module GENERATES prefixes, so it must
#: produce only valid ones.
_PREFIX_PATTERN = re.compile(r"^[a-z][a-z0-9]{0,19}$")

#: Generated prefixes look like `adcp<6 hex>` (10 chars, e.g. `adcp9f3a1c`), no separator so the id
#: is valid as-is inside a runtime name. The stem is a human hint that the instance belongs to this
#: project; the hex suffix is what actually makes it unique.
_GENERATED_STEM = "adcp"


def _now() -> str:
    """An ISO-8601 UTC timestamp, for recording when an instance was first/last deployed."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def load() -> dict[str, Any]:
    """Read the manifest, returning `{"instances": [...]}`.

    A missing file is the normal "nothing deployed from this machine yet" case and yields an empty
    manifest rather than an error. A corrupt file IS an error: silently discarding it would orphan
    every instance it recorded, so the caller is told to look at it.
    """
    if not MANIFEST_PATH.exists():
        return {"instances": []}
    try:
        data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(
            f"{MANIFEST_PATH.name} is present but unreadable ({exc}). It maps deployed instances to "
            "their accounts; fix or delete it before deploying so instances are not orphaned."
        ) from exc
    instances = data.get("instances")
    if not isinstance(instances, list):
        return {"instances": []}
    return {"instances": instances}


def save(data: dict[str, Any]) -> None:
    """Write the manifest back, pretty-printed so a human can read and edit it."""
    MANIFEST_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def all_instances() -> list[dict[str, Any]]:
    """Every recorded instance, newest-recorded last."""
    return list(load()["instances"])


def find_prefix(prefix: str) -> dict[str, Any] | None:
    """The manifest entry for a given prefix, or None if this machine has no record of it."""
    for entry in all_instances():
        if entry.get("prefix") == prefix:
            return entry
    return None


def find(account: str, region: str) -> list[dict[str, Any]]:
    """Every recorded instance in a given account + region.

    The whole point of the prefix is that this can return MORE than one entry -- multiple instances
    in the same account/region is the supported case. Callers use the length to decide: zero means
    "first deploy here", one means "reuse it", many means "the operator must say which via --prefix".
    """
    return [
        e
        for e in all_instances()
        if e.get("account") == account and e.get("region") == region
    ]


def generate_prefix() -> str:
    """Mint a new unique id prefix, guaranteed distinct from every prefix already recorded.

    `adcp<6 hex>`: short enough to leave S3 headroom, valid against `_PREFIX_PATTERN` (bare
    alphanumeric, so it embeds in runtime names), and random rather than sequential so two machines
    deploying independently do not collide on `adcp2`.
    """
    existing = {e.get("prefix") for e in all_instances()}
    while True:
        candidate = f"{_GENERATED_STEM}{secrets.token_hex(3)}"
        if candidate not in existing and _PREFIX_PATTERN.match(candidate):
            return candidate


def record(prefix: str, account: str, region: str) -> dict[str, Any]:
    """Upsert an instance: create its entry on first sight, refresh `last_deployed_at` after.

    Returns the stored entry. This is what turns a one-off `--prefix` into a tracked instance the
    next no-argument deploy can find.
    """
    data = load()
    for entry in data["instances"]:
        if entry.get("prefix") == prefix:
            entry["account"] = account
            entry["region"] = region
            entry["last_deployed_at"] = _now()
            save(data)
            return entry
    entry = {
        "prefix": prefix,
        "account": account,
        "region": region,
        "created_at": _now(),
        "last_deployed_at": _now(),
    }
    data["instances"].append(entry)
    save(data)
    return entry
