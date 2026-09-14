"""The AWS region to operate in, resolved rather than assumed.

    from aws_region import region
    REGION = region()

Vendored into each agent package by `deploy_all.py` so there is one definition. Uses boto3 (present in
every package) rather than the `aws` CLI that root-level `aws_identity.py` shells out to, because inside
a container there is no CLI and no profile -- only the environment AgentCore injects.

## Why there is no default

`REGION = "us-east-1"` appeared in roughly thirty files. A literal default is worse than no default: a
caller working in another region gets resources silently created in the wrong one, with names that look
right and ARNs that do not match anything else in the deployment. Every bucket name in this project also
embeds the region, so a wrong region produces a bucket that exists but that nothing else references.

Resolution order is the SDK's own: `AWS_REGION`, then `AWS_DEFAULT_REGION`, then the active profile's
configured region. If none of those answer, that is a real misconfiguration and this raises instead of
guessing.
"""

from __future__ import annotations

import functools
import os


class RegionNotConfigured(RuntimeError):
    """No region could be resolved from the environment or the active profile."""


@functools.lru_cache(maxsize=1)
def _region_from_profile() -> str | None:
    """The active profile's region. Cached because constructing a Session reads config files.

    Only the profile lookup is cached. The environment is re-read on every call -- see `region()`.
    """
    # Imported here rather than at module scope: this module is imported by code that must stay free of
    # import-time side effects, and by a Lambda where a needless import costs cold-start time.
    import boto3

    return boto3.session.Session().region_name


def region() -> str:
    """The region to operate in.

    Deliberately NOT cached on the environment. `state.py` keys its table cache on (name, region) so
    that changing `AWS_REGION` yields a different table, and its tests change the variable at runtime to
    prove it. An `lru_cache` here returned the first value forever, so those tests failed and, worse, a
    process that legitimately switched region would have kept talking to the old one.
    """
    for key in ("AWS_REGION", "AWS_DEFAULT_REGION"):
        value = os.environ.get(key, "").strip()
        if value:
            return value

    resolved = _region_from_profile()
    if not resolved:
        raise RegionNotConfigured(
            "No AWS region configured. Set AWS_REGION, or set one on the active profile "
            "(`aws configure set region <region>`). There is deliberately no default: resources here "
            "carry the region in their names, so a guessed region creates things nothing else can find."
        )
    return resolved


#: CloudFront's control plane is global but only answers in us-east-1, whatever region the rest of the
#: deployment uses. This is a property of the service, not a default, so it is named as such.
CLOUDFRONT_API_REGION = "us-east-1"
