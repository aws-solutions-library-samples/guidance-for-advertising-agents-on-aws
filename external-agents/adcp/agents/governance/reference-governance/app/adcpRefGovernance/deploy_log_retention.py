"""Set CloudWatch log retention on the governance runtime's log groups.

## Why 731 days

A governance agent's logs are the record of what it decided and why. AdCP's audit trail lives in DynamoDB,
but the operational half -- a refusal, an escalation opening, an aggregate read failing -- is in the logs,
and an auditor asking about a decision is usually asking about something that happened a while ago.
SECURITY-14 sets a 90-day floor; 731 days (two years plus a leap day) is CloudWatch's own retention tier
nearest a two-year audit window, and picking a tier CloudWatch already offers avoids the silent rounding
that a non-tier value gets.

**A log group created without a retention policy retains FOREVER.** That is the default this script exists
to correct: it is a cost problem rather than a compliance one, but "forever" is not a decision anyone made.

## Why a script rather than agentcore.json

AgentCore creates the runtime's log groups itself, so their names are not known until the runtime exists
and there is no field in `agentcore.json` to declare retention on. Hence: discover by prefix, after the
deploy. Discovery rather than a hardcoded name for the same reason -- the group name embeds the runtime id,
which changes when a runtime is recreated, and a stale hardcoded name would report success while setting
retention on nothing.

Idempotent: a group already at the target retention is reported and left alone. Run with `--apply`; without
it nothing is changed, matching `deploy_state_table.py`'s convention.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

import boto3

#: Two years plus a leap day. Must be one of CloudWatch's accepted retention values, or the API rejects it.
RETENTION_DAYS = 731

#: AgentCore's own log-group prefix, plus the runtime name from `agentcore.json`. Both halves matter: the
#: prefix alone would match every agent in the account, and the runtime name alone is not a group name.
LOG_GROUP_PREFIX = "/aws/bedrock-agentcore/runtimes/"
#: The per-instance PROJECT name `<prefix>RefGovernance`. The deployed log group is named from the
#: runtimeId `<prefix>RefGovernance_RefGovernance-<suffix>`, and this project name is the substring
#: that is both present in it and unique to this instance (the bare base `RefGovernance` would also
#: match every other instance's governance group). Defaults to 'adcp'.
RUNTIME_NAME = f"{os.environ.get('INSTANCE_PREFIX', 'adcp').strip() or 'adcp'}RefGovernance"


def _matching_groups(logs: Any) -> list[dict[str, Any]]:
    """Every AgentCore log group belonging to this runtime.

    Filtered on the runtime name appearing in the group name, because AgentCore appends a runtime id and a
    suffix (`-DEFAULT`) that this script cannot predict. Listing by prefix and filtering is what keeps it
    correct across a runtime being recreated.
    """
    groups: list[dict[str, Any]] = []
    paginator = logs.get_paginator("describe_log_groups")
    for page in paginator.paginate(logGroupNamePrefix=LOG_GROUP_PREFIX):
        for group in page.get("logGroups", []):
            if RUNTIME_NAME.lower() in group.get("logGroupName", "").lower():
                groups.append(group)
    return groups


def set_retention(apply: bool) -> int:
    logs = boto3.client("logs")
    groups = _matching_groups(logs)

    if not groups:
        # Not an error, and specifically not silence. The usual cause is that the runtime has not been
        # deployed yet or has never been invoked, and saying so is more useful than reporting success for
        # zero groups.
        print(
            f"No log groups found under {LOG_GROUP_PREFIX} matching {RUNTIME_NAME!r}. "
            "The runtime may not be deployed yet, or may never have logged. Nothing to do."
        )
        return 0

    changed = 0
    for group in groups:
        name = group["logGroupName"]
        current = group.get("retentionInDays")
        if current == RETENTION_DAYS:
            print(f"  {name}: already {RETENTION_DAYS} days")
            continue
        described = "never expires" if current is None else f"{current} days"
        if not apply:
            print(f"  {name}: would change from {described} to {RETENTION_DAYS} days")
            continue
        logs.put_retention_policy(logGroupName=name, retentionInDays=RETENTION_DAYS)
        print(f"  {name}: {described} -> {RETENTION_DAYS} days")
        changed += 1

    if not apply:
        print("\nRe-run with --apply to make these changes.")
    elif changed == 0:
        print(f"\nAll {len(groups)} group(s) already at {RETENTION_DAYS} days.")
    else:
        print(f"\nUpdated {changed} of {len(groups)} group(s).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help=f"actually set retention to {RETENTION_DAYS} days"
    )
    args = parser.parse_args()
    return set_retention(args.apply)


if __name__ == "__main__":
    sys.exit(main())
