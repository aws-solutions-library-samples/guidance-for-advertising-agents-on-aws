"""Governance evaluation: does a proposed action comply with its plan?

Pure functions, no I/O, no clock of its own. Everything a decision depends on is passed in, which is
what makes the rules below assertable without a database or a deployed runtime.

## What this agent can decide, and what it cannot

Every check here is **arithmetic or set membership**:

  * is the committed amount inside the plan's remaining authority?
  * are the requested flight dates inside the plan's authorised window?
  * is every targeted market in the plan's authorised markets?
  * is every requested channel one the plan allows?

For questions of that kind this agent is **certain**, so every finding it emits carries
``confidence = 1.0`` and no ``uncertainty_reason``: that is the confidence of an arithmetic
comparison.

**The consequence is worth stating plainly, because it is visible.** AdCP's ``findings[].confidence``
drives the journey view's radial arcs, and the clickable prototype those arcs were designed from shows
four rings at different lengths. This agent will draw them all full, because the quantity they
encode is exactly 1.0 for every check it performs. A varied ring would have to come from somewhere
other than a measurement.

Spread requires a semantic evaluator: a model judging strategic alignment or brand safety against a
plan's natural-language ``objectives``, reporting its own confidence. ``SEMANTIC_CATEGORIES`` below
names the categories such an evaluator would own, and this module deliberately emits **nothing** for
them rather than guessing. A seam, not a gap filled with decoration.

## Category identifiers are opaque by mandate

AdCP is explicit that ``category_id`` and ``categories_evaluated`` are agent-internal labels, not
protocol enums, and must not be pattern-matched by callers. The constants here are therefore *this
agent's* vocabulary; nothing downstream may branch on them, and the journey view assigns arc colours
by position for exactly this reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Any, Iterable, Literal

if TYPE_CHECKING:  # pragma: no cover - typing only
    # Imported for annotations only. `aggregation` pulls in boto3, and this module's value is that it
    # imports nothing that can fail or reach the network, so the dependency stays at the type level.
    from aggregation import AggregateRead

Verdict = Literal["approved", "denied", "conditions"]
Severity = Literal["info", "warning", "critical"]

#: This agent's own category labels. Opaque to callers by protocol mandate.
CATEGORY_BUDGET = "budget_authority"
CATEGORY_FLIGHT = "flight_window"
CATEGORY_GEO = "geo_compliance"
CATEGORY_CHANNEL = "channel_compliance"

#: Every category this agent evaluates deterministically, in a stable order.
#:
#: Stable because it is reported as `categories_evaluated`, and a set's iteration order would make an
#: audit log differ between two identical checks.
DETERMINISTIC_CATEGORIES: tuple[str, ...] = (
    CATEGORY_BUDGET,
    CATEGORY_FLIGHT,
    CATEGORY_GEO,
    CATEGORY_CHANNEL,
)

#: Categories that need a semantic evaluator this agent does not have.
#:
#: Named so the absence is legible, and NOT reported in `categories_evaluated` — claiming to have
#: evaluated brand safety without doing so would be the most consequential possible lie from a
#: governance agent.
SEMANTIC_CATEGORIES: tuple[str, ...] = (
    "strategic_alignment",
    "brand_safety",
)

#: Confidence of a deterministic comparison. Certain, because arithmetic is.
CERTAIN = 1.0


@dataclass(frozen=True)
class Finding:
    """One issue or observation, shaped for `CheckGovernanceResponse.findings`."""

    category_id: str
    severity: Severity
    explanation: str
    #: 0..1. `CERTAIN` for every arithmetic or set-membership check; see the module docstring.
    #:
    #: Lower only where the inputs to the comparison were themselves unavailable — the aggregate read
    #: failing is the one such case, and it pairs with `uncertainty_reason`.
    confidence: float = CERTAIN
    details: dict[str, Any] = field(default_factory=dict)
    #: The policy that triggered this finding, when one did. Absent for findings that come from the
    #: plan's own parameters (a budget total, a flight window) rather than from a resolved policy.
    policy_id: str | None = None
    #: Why `confidence` is below 1.0. AdCP pairs these two, and a reduced confidence without a stated
    #: reason tells a reader that something was uncertain but not what.
    uncertainty_reason: str | None = None

    def to_wire(self) -> dict[str, Any]:
        wire: dict[str, Any] = {
            "category_id": self.category_id,
            "severity": self.severity,
            "explanation": self.explanation,
            "confidence": self.confidence,
        }
        if self.details:
            wire["details"] = self.details
        if self.policy_id:
            wire["policy_id"] = self.policy_id
        if self.uncertainty_reason:
            wire["uncertainty_reason"] = self.uncertainty_reason
        return wire


@dataclass(frozen=True)
class Condition:
    """An adjustment that would make a `conditions` verdict pass on re-call.

    `required_value` present means the caller can apply it programmatically; absent means it is
    advisory and the caller has to interpret `reason`. That distinction is AdCP's, and it is the
    difference between a caller that can self-correct and one that needs a human.
    """

    field_name: str
    reason: str
    required_value: Any | None = None
    #: Which finding category this condition corrects. Internal, never sent: AdCP's condition shape is
    #: `field`/`reason`/`required_value`. It exists so the verdict rule can tell whether every critical
    #: finding actually has a remedy, rather than assuming any condition remedies everything.
    corrects: str = ""

    def to_wire(self) -> dict[str, Any]:
        wire: dict[str, Any] = {"field": self.field_name, "reason": self.reason}
        if self.required_value is not None:
            wire["required_value"] = self.required_value
        return wire


@dataclass(frozen=True)
class Evaluation:
    verdict: Verdict
    explanation: str
    findings: tuple[Finding, ...]
    conditions: tuple[Condition, ...]
    categories_evaluated: tuple[str, ...]
    #: Policy ids actually evaluated, for `policies_evaluated`. Distinct from the *resolved* set: a
    #: natural-language policy can be resolved as applicable and still not be evaluated by an agent
    #: with no semantic evaluator. Reporting the resolved set here would claim evaluation that did not
    #: happen.
    policies_evaluated: tuple[str, ...] = ()
    #: True when the aggregate crossed its threshold and a human must review before the action can
    #: proceed. The caller suspends the plan and opens an escalation; this module only reports it,
    #: because deciding to suspend is a state change and this module performs none.
    escalate: bool = False


# ------------------------------------------------------------------------------- policy resolution
#
# AdCP's resolution algorithm, in order:
#
#   1. load registry policies referenced by `policy_ids`
#   2. intersect with the plan's `countries` / `regions` -- only policies applicable to the plan's
#      markets are active
#   3. include all `custom_policies`, which apply regardless of geography
#
# This agent has **no policy registry**, so step 1 cannot resolve content. Step 2 is therefore moot for
# registry ids, and step 3 is what it can do: `custom_policies` arrive on the plan as full `PolicyEntry`
# objects, so their `enforcement` and `requires_human_review` are readable without any registry.
#
# Resolution is not evaluation. A natural-language policy can be correctly resolved as applicable and
# still not be *evaluated* by an agent with no semantic evaluator -- so it belongs in
# `resolved_policies` and not in `policies_evaluated`. Conflating the two would be the most consequential
# kind of overclaim available to a governance agent: asserting a compliance check it never ran.


@dataclass(frozen=True)
class PolicyResolution:
    """The outcome of resolving a plan's declared policies."""

    #: Policies this agent can account for, as SDK `ResolvedPolicy` models.
    resolved: tuple[Any, ...]
    #: Ids declared via `policy_ids` that could not be resolved, because there is no registry to
    #: resolve them against. Reported rather than dropped: a plan that declares `uk_hfss` and gets
    #: silence has been told its policy is in force when nothing checked it.
    unresolvable_ids: tuple[str, ...]
    #: True when any resolved policy carries `requires_human_review`. AdCP requires the agent to set
    #: `plan.human_review_required` itself in that case, overriding a caller-supplied false.
    requires_human_review: bool


def resolve_policies(plan: dict[str, Any]) -> PolicyResolution:
    """Resolve a plan's declared policies into SDK `ResolvedPolicy` models.

    `custom_policies` are inline `PolicyEntry` objects on the plan, so `enforcement` and
    `requires_human_review` come straight from the caller's own declaration -- no registry needed, and
    nothing inferred.

    `source` here is `ResolvedPolicy.source` (`explicit` | `auto_applied`), which is **not** the same
    enum as `PolicyEntry.source` (`registry` | `inline`). The first records *how the policy came to be
    included*; the second records *where the policy came from*. Both are declared explicitly on the plan
    in this path, so `explicit` is correct.
    """
    from adcp.types.generated_poc.governance.sync_plans_response import (  # noqa: PLC0415
        ResolvedPolicy,
        Source,
    )

    resolved: list[Any] = []
    requires_review = False

    for entry in plan.get("custom_policies") or []:
        # Accept either a PolicyEntry model or the dict form a caller's payload arrives as.
        policy_id = _entry_attr(entry, "policy_id")
        enforcement = _entry_attr(entry, "enforcement")
        if not policy_id or not enforcement:
            # Both are required by `policy-entry.json`. An entry missing either is not a policy this
            # agent can account for, and inventing a default enforcement would silently downgrade a
            # `must` to a `should`.
            continue
        resolved.append(
            ResolvedPolicy(
                policy_id=str(policy_id),
                source=Source.explicit,
                enforcement=_as_enforcement(enforcement),
                reason="Declared inline on the plan via custom_policies.",
            )
        )
        if bool(_entry_attr(entry, "requires_human_review")):
            requires_review = True

    unresolvable = tuple(str(pid) for pid in (plan.get("policy_ids") or []))

    return PolicyResolution(
        resolved=tuple(resolved),
        unresolvable_ids=unresolvable,
        requires_human_review=requires_review,
    )


def _entry_attr(entry: Any, name: str) -> Any:
    """Read a field from a `PolicyEntry` model or from its dict form."""
    if isinstance(entry, dict):
        return entry.get(name)
    return getattr(entry, name, None)


def _as_enforcement(value: Any):
    """Coerce to the SDK's enforcement enum, letting it reject anything outside `must|should|may`."""
    from adcp.types.generated_poc.enums.policy_enforcement import (  # noqa: PLC0415
        PolicyEnforcementLevel,
    )

    return PolicyEnforcementLevel(getattr(value, "value", value))


#: How a policy's enforcement level maps to finding severity.
#:
#: The spec defines the consequences, so this is a translation rather than a policy of ours:
#: `must` -- "Governance agents reject actions that violate this policy"
#: `should` -- "warn on violations but do not block"
#: `may` -- "log for informational purposes only"
ENFORCEMENT_SEVERITY: dict[str, Severity] = {
    "must": "critical",
    "should": "warning",
    "may": "info",
}


def as_date(value: Any) -> date | None:
    """A date from a wire value, or None when it is not one.

    None means "cannot check", and the caller reports that as an unevaluated category rather than as
    a pass. An unparseable date silently treated as in-window is precisely the fail-open this whole
    agent exists to prevent.
    """
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return None


def _as_amount(value: Any) -> float | None:
    """A finite number, or None. Booleans are rejected: `True` is not a budget."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        amount = float(value)
        return amount if amount == amount and abs(amount) != float("inf") else None
    if isinstance(value, str) and value.strip():
        try:
            return _as_amount(float(value))
        except ValueError:
            return None
    return None


def _upper_set(values: Iterable[Any] | None) -> set[str]:
    if not values:
        return set()
    return {str(v).strip().upper() for v in values if str(v).strip()}


def _lower_set(values: Iterable[Any] | None) -> set[str]:
    if not values:
        return set()
    return {str(v).strip().lower() for v in values if str(v).strip()}


def check_budget(
    *,
    requested: float | None,
    plan_total: float | None,
    already_committed: float,
) -> tuple[Finding | None, Condition | None, bool]:
    """Is the requested amount inside the plan's remaining authority?

    Returns `(finding, condition, evaluated)`. `evaluated` is False when the inputs do not permit a
    decision — an unstated budget is not an approved budget, and the caller reports the category as
    unevaluated instead of counting it as a pass.
    """
    if plan_total is None:
        return None, None, False

    remaining = plan_total - already_committed
    if requested is None:
        # The action named no amount. Reported, because a spend commit without a stated amount cannot
        # be checked against authority, and silence here would read as approval.
        return (
            Finding(
                category_id=CATEGORY_BUDGET,
                severity="warning",
                explanation=(
                    "The action states no amount, so it could not be checked against the plan's "
                    "remaining authority."
                ),
                details={"budget_remaining": remaining},
            ),
            None,
            False,
        )

    if requested <= remaining:
        return (
            Finding(
                category_id=CATEGORY_BUDGET,
                severity="info",
                explanation=(
                    f"Requested {requested:,.2f} is within the plan's remaining authority of "
                    f"{remaining:,.2f}."
                ),
                details={
                    "requested": requested,
                    "budget_remaining": remaining,
                    "budget_used_pct": (
                        round(already_committed / plan_total * 100, 2) if plan_total else 0.0
                    ),
                },
            ),
            None,
            True,
        )

    # Over authority. `conditions` rather than `denied` when there is authority left to spend, because
    # a smaller commit would pass and the caller can apply that value programmatically.
    if remaining > 0:
        return (
            Finding(
                category_id=CATEGORY_BUDGET,
                severity="critical",
                explanation=(
                    f"Requested {requested:,.2f} exceeds the plan's remaining authority of "
                    f"{remaining:,.2f}."
                ),
                details={"requested": requested, "budget_remaining": remaining},
            ),
            Condition(
                field_name="budget.total",
                reason="Reduce the committed amount to the plan's remaining authority.",
                required_value=remaining,
                corrects=CATEGORY_BUDGET,
            ),
            True,
        )

    return (
        Finding(
            category_id=CATEGORY_BUDGET,
            severity="critical",
            explanation="The plan has no remaining budget authority.",
            details={"requested": requested, "budget_remaining": remaining},
        ),
        None,
        True,
    )


def check_aggregate(
    *,
    incoming: float | None,
    aggregate_committed: float | None,
    aggregate_reason: str | None,
    reallocation_threshold: float | None,
    window_days: int,
) -> tuple[Finding | None, bool, bool]:
    """The fragmentation defense: is trailing-window committed spend still under the threshold?

    Returns `(finding, escalate, evaluated)`.

    **The incoming commit is included in the sum** (M3.6). This is the entire mechanism: without it the
    hundredth $9,999 commit is judged against a $999,900 aggregate that excludes itself, and passes.
    AdCP states it directly -- *"The current incoming commit is included in the sum"* -- and calls an
    agent that approves such a commit non-conformant.

    The threshold is `budget.reallocation_threshold`, which the spec names as one of the thresholds that
    MUST be evaluated against the aggregate rather than per-plan. Crossing it is an escalation to human
    review, not a denial: the spend may well be authorised, but a human has to say so.

    An unreadable aggregate produces a finding with reduced confidence and an `uncertainty_reason`, and
    `evaluated=False`. It never produces a headroom figure and never reports zero -- reporting spend we
    could not read as zero is the one outcome that would silently reopen the hole this closes.
    """
    if aggregate_committed is None:
        # Unknown, not zero. The caller must not be able to read approval out of this.
        return (
            Finding(
                category_id=CATEGORY_BUDGET,
                severity="critical",
                explanation=(
                    "Trailing-window committed spend could not be read, so this action could not be "
                    "checked against the aggregate spend threshold."
                ),
                confidence=0.0,
                uncertainty_reason=aggregate_reason
                or "The committed-spend aggregate was unavailable.",
                details={"aggregation_window_days": window_days},
            ),
            False,
            False,
        )

    if reallocation_threshold is None:
        # No threshold declared, so there is nothing to evaluate the aggregate against. Reported as
        # unevaluated rather than as a pass.
        return None, False, False

    total = aggregate_committed + (incoming or 0.0)

    if total <= reallocation_threshold:
        return (
            Finding(
                category_id=CATEGORY_BUDGET,
                severity="info",
                explanation=(
                    f"Trailing {window_days}-day committed spend of {total:,.2f} (including this "
                    f"action) is within the escalation threshold of {reallocation_threshold:,.2f}."
                ),
                details={
                    "aggregate_committed": aggregate_committed,
                    "aggregate_including_incoming": total,
                    "reallocation_threshold": reallocation_threshold,
                    "aggregation_window_days": window_days,
                },
            ),
            False,
            True,
        )

    # Over the threshold once this commit is counted. Escalate for human review -- the spec's consequence
    # for a threshold crossed by the aggregate, and it fires even when the incoming commit is small in
    # isolation. That case is the whole point.
    return (
        Finding(
            category_id=CATEGORY_BUDGET,
            severity="critical",
            explanation=(
                f"Trailing {window_days}-day committed spend of {total:,.2f} (including this action) "
                f"exceeds the escalation threshold of {reallocation_threshold:,.2f}. Human review is "
                "required before this action can proceed."
            ),
            details={
                "aggregate_committed": aggregate_committed,
                "aggregate_including_incoming": total,
                "reallocation_threshold": reallocation_threshold,
                "aggregation_window_days": window_days,
                "incoming_commit_in_isolation": incoming,
            },
        ),
        True,
        True,
    )


def check_flight(
    *,
    start: date | None,
    end: date | None,
    plan_start: date | None,
    plan_end: date | None,
) -> tuple[Finding | None, bool]:
    """Are the requested dates inside the plan's authorised window?"""
    if plan_start is None or plan_end is None:
        return None, False
    if start is None or end is None:
        return (
            Finding(
                category_id=CATEGORY_FLIGHT,
                severity="warning",
                explanation="The action states no flight dates, so they could not be checked.",
            ),
            False,
        )

    outside: list[str] = []
    if start < plan_start:
        outside.append(f"starts {start.isoformat()}, before the plan's {plan_start.isoformat()}")
    if end > plan_end:
        outside.append(f"ends {end.isoformat()}, after the plan's {plan_end.isoformat()}")

    if outside:
        return (
            Finding(
                category_id=CATEGORY_FLIGHT,
                severity="critical",
                explanation="Requested flight falls outside the authorised window: "
                + "; ".join(outside)
                + ".",
                details={
                    "requested_start": start.isoformat(),
                    "requested_end": end.isoformat(),
                    "plan_start": plan_start.isoformat(),
                    "plan_end": plan_end.isoformat(),
                },
            ),
            True,
        )

    return (
        Finding(
            category_id=CATEGORY_FLIGHT,
            severity="info",
            explanation=(
                f"Requested flight {start.isoformat()} to {end.isoformat()} is inside the "
                f"authorised window."
            ),
        ),
        True,
    )


def check_markets(
    *, requested: Iterable[Any] | None, authorised: Iterable[Any] | None
) -> tuple[Finding | None, bool]:
    """Is every targeted market authorised by the plan?

    An empty `authorised` means the plan set no geographic restriction, so there is nothing to check
    and the category is not reported as evaluated.
    """
    allowed = _upper_set(authorised)
    if not allowed:
        return None, False

    asked = _upper_set(requested)
    if not asked:
        return (
            Finding(
                category_id=CATEGORY_GEO,
                severity="warning",
                explanation=(
                    "The action names no markets, so it could not be checked against the plan's "
                    "authorised markets."
                ),
                details={"authorised": sorted(allowed)},
            ),
            False,
        )

    unauthorised = sorted(asked - allowed)
    if unauthorised:
        return (
            Finding(
                category_id=CATEGORY_GEO,
                severity="critical",
                explanation=(
                    "Targets markets the plan does not authorise: " + ", ".join(unauthorised) + "."
                ),
                details={"unauthorised": unauthorised, "authorised": sorted(allowed)},
            ),
            True,
        )

    return (
        Finding(
            category_id=CATEGORY_GEO,
            severity="info",
            explanation=f"All {len(asked)} targeted market(s) are authorised by the plan.",
            details={"markets": sorted(asked)},
        ),
        True,
    )


def check_channels(
    *, requested: Iterable[Any] | None, allowed: Iterable[Any] | None
) -> tuple[Finding | None, bool]:
    """Is every requested channel one the plan permits?

    An absent channel constraint means all channels are allowed, which is AdCP's own default for
    `plans[].channels`, so there is nothing to check.
    """
    permitted = _lower_set(allowed)
    if not permitted:
        return None, False

    asked = _lower_set(requested)
    if not asked:
        return None, False

    disallowed = sorted(asked - permitted)
    if disallowed:
        return (
            Finding(
                category_id=CATEGORY_CHANNEL,
                severity="critical",
                explanation="Requests channels the plan does not allow: " + ", ".join(disallowed) + ".",
                details={"disallowed": disallowed, "allowed": sorted(permitted)},
            ),
            True,
        )

    return (
        Finding(
            category_id=CATEGORY_CHANNEL,
            severity="info",
            explanation=f"All requested channel(s) are allowed by the plan.",
            details={"channels": sorted(asked)},
        ),
        True,
    )


def evaluate(
    *,
    plan: dict[str, Any],
    action: dict[str, Any],
    already_committed: float = 0.0,
    aggregate: "AggregateRead | None" = None,
    aggregate_threshold: float | None = None,
    aggregation_window_days: int = 30,
) -> Evaluation:
    """Evaluate one action against one plan.

    The verdict rule, in order:

      1. any `critical` finding with **no** corrective condition  -> ``denied``
      2. any corrective condition                                 -> ``conditions``
      3. otherwise                                                -> ``approved``

    Ordered so that a breach which cannot be corrected can never be softened into `conditions`, and a
    breach that can be corrected is never hardened into `denied` — which would stop a caller that is
    able to fix itself.

    `already_committed` is **this plan's** committed total. `aggregate` is the **cross-plan** figure for
    the whole `(buyer_agent, seller_agent, account_id)` relationship, and the two are different
    quantities answering different questions: whether this plan has authority left, and whether the
    relationship has crossed an escalation threshold. Passing one where the other belongs would either
    deny legitimate buys or reopen the fragmentation hole.

    `aggregate_threshold` is the value the trailing-window sum is compared against, supplied by the
    caller rather than read from the plan here. Which threshold applies depends on the request's phase,
    and phase is a property of the request -- so deciding it belongs with the request, not in this module.
    Passing `None` means no threshold applies and the aggregate is reported without a consequence.

    `aggregate` is typed but not imported at runtime, so this module keeps importing nothing that can
    perform I/O.
    """
    budget = plan.get("budget") or {}
    flight = plan.get("flight") or {}

    findings: list[Finding] = []
    conditions: list[Condition] = []
    evaluated: list[str] = []

    budget_finding, budget_condition, budget_done = check_budget(
        requested=_as_amount(action.get("amount")),
        plan_total=_as_amount(budget.get("total")),
        already_committed=already_committed,
    )
    if budget_finding:
        findings.append(budget_finding)
    if budget_condition:
        conditions.append(budget_condition)
    if budget_done:
        evaluated.append(CATEGORY_BUDGET)

    # Cross-plan aggregate (M3). Skipped entirely when no aggregate was supplied, which is how a caller
    # that has not adopted the fragmentation defense keeps working — rather than being told its spend is
    # zero.
    escalate = False
    if aggregate is not None:
        aggregate_finding, escalate, aggregate_done = check_aggregate(
            incoming=_as_amount(action.get("amount")),
            aggregate_committed=aggregate.committed,
            aggregate_reason=aggregate.reason,
            reallocation_threshold=aggregate_threshold,
            window_days=aggregation_window_days,
        )
        if aggregate_finding:
            findings.append(aggregate_finding)
        if aggregate_done and CATEGORY_BUDGET not in evaluated:
            evaluated.append(CATEGORY_BUDGET)
        if aggregate_finding is not None and not aggregate_done:
            # The aggregate was unreadable. The budget category cannot be reported as evaluated, and the
            # verdict must not be `approved` (NFR-6) — so an advisory condition carries it to
            # `conditions`. No `required_value`: there is nothing the caller can set to fix our outage,
            # which is exactly what an absent `required_value` means.
            conditions.append(
                Condition(
                    field_name="budget.total",
                    reason=(
                        "Committed-spend aggregate was unavailable; retry so the action can be checked "
                        "against the trailing-window threshold."
                    ),
                    corrects=CATEGORY_BUDGET,
                )
            )
            if CATEGORY_BUDGET in evaluated:
                evaluated.remove(CATEGORY_BUDGET)

    flight_finding, flight_done = check_flight(
        start=as_date(action.get("start_date")),
        end=as_date(action.get("end_date")),
        plan_start=as_date(flight.get("start_date") or flight.get("start")),
        plan_end=as_date(flight.get("end_date") or flight.get("end")),
    )
    if flight_finding:
        findings.append(flight_finding)
    if flight_done:
        evaluated.append(CATEGORY_FLIGHT)

    geo_finding, geo_done = check_markets(
        requested=action.get("markets"),
        authorised=(plan.get("regions") or []) + (plan.get("countries") or []),
    )
    if geo_finding:
        findings.append(geo_finding)
    if geo_done:
        evaluated.append(CATEGORY_GEO)

    channel_finding, channel_done = check_channels(
        requested=action.get("channels"),
        allowed=(plan.get("channels") or {}).get("allowed")
        if isinstance(plan.get("channels"), dict)
        else plan.get("channels"),
    )
    if channel_finding:
        findings.append(channel_finding)
    if channel_done:
        evaluated.append(CATEGORY_CHANNEL)

    critical = [f for f in findings if f.severity == "critical"]
    corrective = list(conditions)
    remedied = {c.corrects for c in corrective if c.corrects}

    # A critical finding with no condition addressing ITS OWN category cannot be corrected by the
    # caller, so the verdict has to be `denied`. Treating any condition as remedying everything was a
    # real bug here: a budget condition softened an unauthorised-market breach into `conditions`,
    # inviting the caller to adjust its budget and re-call into the same refusal forever. A test pins
    # it, because the failure is a governance agent letting through what it was built to stop.
    unremedied = [f for f in critical if f.category_id not in remedied]

    if unremedied:
        verdict: Verdict = "denied"
        explanation = "; ".join(f.explanation for f in unremedied)
    elif corrective:
        verdict = "conditions"
        explanation = "Approved once these adjustments are applied: " + "; ".join(
            c.reason for c in corrective
        )
    else:
        verdict = "approved"
        # Names what was actually checked. "Approved" alone tells a reader nothing about coverage, and
        # coverage is the thing a governance approval is worth exactly as much as.
        explanation = (
            "Within plan authority. Evaluated: " + ", ".join(evaluated) + "."
            if evaluated
            else (
                "No plan constraint applied to this action, so nothing was evaluated. This is not a "
                "compliance finding."
            )
        )

    return Evaluation(
        verdict=verdict,
        explanation=explanation,
        findings=tuple(findings),
        conditions=tuple(corrective),
        # Only what was really checked. Semantic categories are absent, not listed as evaluated.
        categories_evaluated=tuple(evaluated),
        # Empty for now: this agent evaluates plan parameters, not policy text. A policy resolved as
        # applicable is reported in `resolved_policies` at sync time; claiming it here would assert an
        # evaluation that needs a semantic evaluator this agent does not have.
        policies_evaluated=(),
        escalate=escalate,
    )


def utc_now() -> datetime:
    """Injected wherever a decision depends on the time, so tests are not clock-dependent."""
    return datetime.now(timezone.utc)
