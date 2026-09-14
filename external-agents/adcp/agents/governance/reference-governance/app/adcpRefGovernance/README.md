# AdCP Reference Campaign-Governance Agent

An AdCP campaign-governance agent, deployed as an AgentCore MCP server.

It is built on the **AdCP SDK** (`adcp==6.6.0`): `ReferenceGovernanceAgent` subclasses
`adcp.server.governance.GovernanceHandler`, and `adcp.server.serve` runs it. Request validation, typed
models, protocol envelope and tool registration all come from the SDK — none of it is hand-rolled.

## What it implements

| Task | Status |
|---|---|
| `sync_plans` | implemented |
| `check_governance` | implemented — intent and execution checks |
| `report_plan_outcome` | implemented |
| `get_plan_audit_logs` | implemented |
| `get_adcp_capabilities` | implemented — declares the aggregation window and the idempotency contract |
| `get_creative_features` | implemented — three genuinely computed features |
| property list CRUD (5 tasks) | reports unsupported |

"Reports unsupported" is a true statement rather than a stub returning a plausible answer. The SDK's own
`GovernanceHandler` docstring describes this as the intended shape for operations an agent does not offer.

`sync_governance` is **not** here, and that is not an omission: it is an *accounts* task on the
**seller**. The buyer syncs governance agent endpoints to a seller account, and the seller then calls
that agent for its execution checks.

## What it can honestly decide

Every check is arithmetic or set membership — budget authority, flight window, authorised markets,
allowed channels. For those the agent is certain, so each finding carries `confidence: 1.0`.

**The radial arcs in the journey view will therefore be uniform, not varied like the clickable
prototype's.** Varied confidence needs a real semantic evaluator judging strategic alignment or brand
safety against a plan's natural-language objectives. Inventing spread would fabricate the single
quantity that visual is built on. `policy.SEMANTIC_CATEGORIES` names the categories such an evaluator
would own, and this agent emits nothing for them — a seam, not a gap papered over.

## The three modes

`GOVERNANCE_MODE` (an env var, read per request) selects one of three. An unrecognised value **refuses to
run** rather than defaulting — a governance agent silently falling back to the most permissive mode is the
one failure here nobody would notice.

| Mode | Verdict | Aggregate | Suspension |
|---|---|---|---|
| `enforce` | as evaluated | committed | refuses |
| `advisory` | as evaluated, not binding on the caller | committed | refuses |
| `audit` | always `approved` | committed | refuses |

Two columns are the same in all three rows, deliberately:

- **The aggregate is always committed.** The mode governs what the agent *does* about spend, never whether
  it counts it. An agent that stopped aggregating in `audit` would come back from an audit period with a
  trailing window missing exactly the spend that happened during it, and under-count every fragmentation
  check for the next 30 days. Counting is bookkeeping; blocking is policy.
- **Suspension refuses in every mode.** `audit`'s "always returns `approved`" governs the verdict of an
  *evaluation*, and a suspended plan is never evaluated — there is no verdict for `audit` to force.
  Manufacturing one would make `audit` the single mode in which a plan awaiting human review transacts
  anyway. Suspension is a gate, not an outcome.

## Suspension, and why resolving it is a shell script

A plan suspends when it declares `human_review_required`, when a resolved policy demands review, or when
the trailing-window aggregate crosses its escalation threshold. While suspended, both governed tasks answer
`CAMPAIGN_SUSPENDED` with `recovery: transient` — transient because a human clearing the escalation fixes
it, and telling a buyer to give up on a plan one decision away from working would be worse than refusing.

**There is no AdCP task to un-suspend a plan, and that is the spec's design.**
`CAMPAIGN_SUSPENDED`'s own recovery hint is *"contact the plan operator"*, which places the human outside
the protocol. A party able to clear its own suspension is not suspended in any meaningful sense.

So resolution is an operator tool:

```
uv run python resolve_escalation.py plan-1                       # list what is outstanding
uv run python resolve_escalation.py plan-1 --check-id chk_… \
    --resolution approved_by_human --apply                       # record a decision
```

It registers no MCP tool and is imported by nothing in the runtime — a test asserts both, by parsing each
runtime module's imports rather than by trusting the convention.

Resolving records the human's decision as the check's verdict while **preserving the agent's original
recommendation** as `recommended_verdict`. That is not tidiness: `drift_metrics.human_override_rate`
compares the two, so an in-place overwrite would make them identical by construction and report perfect
calibration for every plan a human ever reversed.

The buyer sees all of it on its next `get_plan_audit_logs` — `status` back to `active`,
`summary.escalations[]` carrying the resolution and its timestamp. No notification is sent and none is
needed; the journey UI reads the audit log it already reads.

## The spend aggregate

`check_governance` compares a proposed commit against **committed spend over a trailing 30-day window**,
keyed on `(buyer_agent, seller_agent, account_id)` — AdCP's own key, quoted verbatim in the
`aggregation_window_days` capability description. Without it, a buyer can split one large spend into many
sub-threshold commits across plans, task surfaces and time, and bypass every dollar-gated escalation.

The window is declared on `get_adcp_capabilities` as `governance.aggregation_window_days`, read from the
same constant the aggregate queries over. That declaration is not optional politeness: the field's own
description says a buyer seeing no window **MUST assume per-commit evaluation only**, and that 30 "is not
implied by omission". An undeclared window makes a working defense indistinguishable from an absent one.

When the aggregate cannot be read, the budget category **degrades** — never `approved`, no headroom figure
in `details`, and a condition with no `required_value`, because there is nothing the caller can set to fix
our outage.

## Layout

```
policy.py                evaluation. Pure functions, no I/O, no clock of its own
state.py                 DynamoDB store for plans, checks, outcomes and escalations
aggregation.py           the trailing-window committed-spend aggregate (M3)
idempotency_backend.py   DynamoDB backend for the SDK's IdempotencyStore (M8)
errors.py                AdCP errors, with recovery read from the SDK's manifest
gov_logging.py           the structured operational signals
jws.py                   the signed governance_context, per AdCP's JWS profile
main.py                  the SDK handler and serve()

resolve_escalation.py    OPERATOR tool. Not part of the runtime.
deploy_state_table.py    creates the table; --enable-ttl turns on expiry
deploy_log_retention.py  731-day retention on the runtime's log groups
SDK-GAPS.md              the four SDK gaps worked around, and how to retire each
```

## Running the tests

```
uv run pytest -q
```

`test_sdk_gaps.py` is worth knowing about before it fails: every test in it asserts that an SDK gap
**still exists**. A failure there is good news — read the message, which names what to delete.
