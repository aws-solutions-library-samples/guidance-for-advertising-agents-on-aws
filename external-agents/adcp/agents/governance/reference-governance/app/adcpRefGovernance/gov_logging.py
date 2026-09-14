"""Structured signals for the five governance events worth querying after the fact.

One function per signal, and the field names exist only here. A field name spelled two ways is worse
than a missing one: a Logs Insights query written against the first spelling silently returns nothing
for events logged under the second, and the gap looks like an absence of events rather than a naming
mistake.

Emitted as single-line JSON, which AgentCore forwards from stdout to CloudWatch Logs and Logs Insights
parses without a custom pattern. No alarms are configured -- these are for asking questions afterwards.

The last signal is the one most worth having. An agent left in `audit` or `advisory` passes every health
check while blocking nothing, and there is no other way to tell that apart from an agent that simply had
nothing to block.

Values here are agent-side facts: plan ids, verdicts, mode, error codes, counts. Caller-supplied strings
are not logged.
"""

from __future__ import annotations

import json
import logging
from typing import Any

_log = logging.getLogger("governance.signals")

#: Field carrying the signal name, so one Logs Insights filter selects a signal.
EVENT = "event"

AGGREGATE_READ_FAILED = "aggregate_read_failed"
ESCALATION_OPENED = "escalation_opened"
ESCALATION_RESOLVED = "escalation_resolved"
SUSPENDED_CALL_REFUSED = "suspended_call_refused"
VERDICT_DENIED = "verdict_denied"
MODE_NOT_ENFORCING = "mode_not_enforcing"


def _emit(event: str, level: int = logging.INFO, **fields: Any) -> None:
    _log.log(level, json.dumps({EVENT: event, **fields}, separators=(",", ":"), default=str))


def aggregate_read_failed(*, plan_id: str, reason: str) -> None:
    """The budget category is degrading. A sustained rate means budget is going unverified."""
    _emit(AGGREGATE_READ_FAILED, logging.WARNING, plan_id=plan_id, reason=reason)


def escalation_opened(*, plan_id: str, check_id: str, reason: str) -> None:
    """A plan now requires human review. Someone has to act out of band for it to transact again."""
    _emit(ESCALATION_OPENED, logging.WARNING, plan_id=plan_id, check_id=check_id, reason=reason)


def escalation_resolved(*, plan_id: str, check_id: str, resolution: str) -> None:
    """Pairs with `escalation_opened`, so time-to-resolution is answerable from the logs alone."""
    _emit(ESCALATION_RESOLVED, plan_id=plan_id, check_id=check_id, resolution=resolution)


def suspended_call_refused(*, plan_id: str, tool: str, mode: str) -> None:
    """A refusal on a suspended plan.

    A suspension nobody is resolving looks exactly like a broken agent from the caller's side, so the
    rate of this is what distinguishes the two.
    """
    _emit(SUSPENDED_CALL_REFUSED, logging.WARNING, plan_id=plan_id, tool=tool, mode=mode)


def verdict_denied(*, plan_id: str, categories: list[str], mode: str) -> None:
    """A denial. A spike is either abuse or a policy misconfiguration, and the categories say which."""
    _emit(VERDICT_DENIED, plan_id=plan_id, categories=sorted(categories), mode=mode)


def mode_not_enforcing(*, mode: str) -> None:
    """The agent is not blocking anything.

    Logged once at startup rather than per request: per request it would drown the signal it exists to
    provide.
    """
    _emit(MODE_NOT_ENFORCING, logging.WARNING, mode=mode)
