"""Lambda handler: republish the governance agent's revocation list on a schedule.

Wired into `agentcore/cdk/lib/cdk-stack.ts` as a `DockerImageFunction` behind an EventBridge `Rule`
on a 12h `rate()` schedule (see that file for the construct). This exists because the document
`deploy_revocations.py` publishes has a declared `next_update` -- the AdCP SDK's
`AsyncCachingRevocationChecker` fails closed once that passes, and nothing was republishing it
between manual `--apply` runs. See `deploy_revocations.py`'s own module docstring for the live
outage this fixes.

## Same publish path as the manual CLI, not a second implementation

This handler calls `deploy_revocations.publish()` -- the exact function `deploy_revocations.py
--apply` calls -- so there is one implementation of "build, upload, invalidate, verify", not a CLI
version and a Lambda version that could quietly drift apart.

## Deliberately excludes main.py's heavier dependencies

This Lambda's own Dockerfile (sibling) copies only `jws.py`, `deploy_revocations.py` and this
handler from the parent package -- not `main.py`, `state.py`, `policy.py`, or their Pillow/
pytesseract/DynamoDB-touching imports, none of which `issue_revocation_list`/`publish()` needs.
The parent's `pyproject.toml`/`uv.lock` are still reused as-is (not a second, possibly-drifting
lockfile) so `adcp`/`boto3`/`httpx` stay pinned to the exact same versions as the main agent
runtime, even though several of that lockfile's other dependencies go unused here.
"""

from __future__ import annotations

import json
from typing import Any

import deploy_revocations


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """EventBridge invokes this with a scheduled-event payload; the payload's content is not
    used -- this Lambda does exactly one thing regardless of what triggered it."""
    payload = deploy_revocations.publish()
    result = {
        "published_url": deploy_revocations.published_url(),
        "issuer": payload["issuer"],
        "updated": payload["updated"],
        "next_update": payload["next_update"],
    }
    print(f"Revocation list republished: {json.dumps(result)}")
    return result
