# AdCP SDK gaps this agent works around

Every gap below is **measured, not assumed**, and each has a canary in `test_sdk_gaps.py` that asserts
the gap *still exists*. When an SDK release closes one, that canary fails and its message names what to
delete. A failing canary is good news; read it and follow it.

**Verified against two SDK versions**, deliberately — a gap that only appears under our pin is a pin
problem, not an SDK problem:

| | Version | Spec |
|---|---|---|
| Pinned here | `adcp==6.6.0` | 3.1.1 |
| Latest at time of writing | `adcp==7.0.2` | 3.1.15 |

All four live gaps are present in **both**. The governance schemas are byte-identical between the two
bundles, so upgrading the pin would not retire any of them.

Reproduce the cross-version check:

```bash
uv venv /tmp/adcplatest --python 3.12
uv pip install --python /tmp/adcplatest/bin/python adcp
/tmp/adcplatest/bin/python - <<'PY'
import adcp, adcp.types as t
from adcp.server.helpers import STANDARD_ERROR_CODES
from adcp.types.generated_poc.governance.get_plan_audit_logs_response import Finding as AF
from adcp.types.generated_poc.governance.check_governance_response import Finding as RF
print('SDK', adcp.get_adcp_sdk_version(), 'SPEC', adcp.get_adcp_spec_version())
print('GAP-1 fixed:', 'CAMPAIGN_SUSPENDED' in STANDARD_ERROR_CODES)
print('GAP-2 fixed:', hasattr(t, 'AdcpManifest'))
print('GAP-4 fixed:', sorted(AF.model_fields) == sorted(RF.model_fields))
print('GAP-5 fixed:', hasattr(t, 'Entry'))
PY
```

---

## SDK-GAP-1 — governance error codes are absent from the recovery table

**Symptom.** `adcp.server.helpers.adcp_error` fills `recovery` from `STANDARD_ERROR_CODES`, which covers
38 of AdCP's 92 codes. None of the governance codes is among them, so the helper defaults them to
`terminal` — telling a buyer to stop retrying `CAMPAIGN_SUSPENDED`, which resolves by a human clearing an
escalation. `PLAN_NOT_FOUND` and `GOVERNANCE_CHECK_REQUIRED` are equally misclassified.

**Evidence.**
```bash
uv run python -c "from adcp.server.helpers import STANDARD_ERROR_CODES; \
print('CAMPAIGN_SUSPENDED' in STANDARD_ERROR_CODES)"   # -> False
```

**Measure.** `errors.code_metadata` reads `recovery` and `suggestion` from the SDK's own bundled
manifest (`adcp/_schemas/<bundle>/manifest.json`) and passes them to `adcp_error` explicitly. The
classification therefore has one source and it is the SDK's — this agent does not restate it. 3.1 makes
populating `recovery` the sender's job, so this is conformance, not merely a patch.

**Retirement.** Delete `errors.code_metadata` and `errors._manifest`; have `errors.governance_error` call
`adcp_error` without the overrides. Keep `test_errors.py` — the recovery classification must still be
correct afterwards.

**Depends on.** SDK-GAP-2, which is the reason reading the manifest requires a private path.

---

## SDK-GAP-2 — the schema manifest has no public accessor

**Symptom.** Two independent halves, either of which the SDK could fix alone:

1. `AdcpManifest` is not re-exported from `adcp.types` — it is not mentioned in that module at all, so it
   must be imported from `adcp.types.generated_poc.manifest_schema`.
2. The manifest file itself lives under the private `adcp/_schemas/`. `adcp.schemas.load_schema` reaches
   only `adcp/schemas/`, which bundles a single file (`adcp-agents.json`).

**Evidence.**
```bash
uv run python -c "import adcp.types as t, adcp.schemas as s; \
print(hasattr(t,'AdcpManifest'), hasattr(s,'load_manifest'))"   # -> False False
```

**Measure.** `errors._manifest` resolves the bundle key with the SDK's public
`adcp.validation.schema_loader.resolve_bundle_key`, then reads the manifest through
`importlib.resources`. **This is the only private-path access anywhere in the agent**, kept in one
function on purpose so retiring the gap changes one file.

**Retirement.** Read the manifest through whatever public accessor appears; drop the `importlib.resources`
reach. Fixing half 1 alone still removes the generated-module import.

---

## SDK-GAP-3 — RETRACTED (kept so it is not rediscovered)

**The claim was** that the SDK could not express a suspended plan, because
`sync_plans_response.Status` is `['active', 'error']`, and that this agent therefore had to signal
suspension out of band.

**It was wrong**, and the mistake was conflating two different statuses:

| Enum | Question it answers | Members |
|---|---|---|
| `sync_plans_response.Status` | Did the **sync** succeed? | `active`, `error` |
| `get_plan_audit_logs_response.Status` | What state is the **plan** in? | `active`, `suspended`, `completed` |

`suspended` would not belong in the first — a sync either wrote or it did not. The second already has
exactly the member thought to be missing, and `main._plan_status` reports real suspension through it.

**There is no workaround to retire.** The canary asserts both enums still say what they say, so the
phantom gap cannot be re-derived from the `sync_plans` side alone. The id is not reused and the entry
stays: a deleted retraction is an invitation to repeat the error.

**Residual, and genuinely unrelated to the above.** An unresolved entry in `summary.escalations[]` is what
makes suspension visible on a `sync_plans` response, since that response has no plan-lifecycle field at
all. That is a shape observation about `sync_plans`, not a missing enum member.

---

## SDK-GAP-4 — the audit `Finding` diverges from the response `Finding`

**Symptom.** `get_plan_audit_logs_response.Entry.findings` describes its items as the "same structure as
check_governance response findings". They are not:

| | Fields |
|---|---|
| `check_governance_response.Finding` | `category_id`, `policy_id`, `severity`, `explanation`, `confidence`, `details`, `source_plan_id`, `uncertainty_reason` |
| audit-log `Finding` | `category_id`, `policy_id`, `severity`, `explanation`, `confidence` |

Both set `extra='forbid'`, so passing a response finding into an audit entry raises three
`extra_forbidden` errors — and it raises them at the response boundary, against a nested path like
`plans.0.entries.0.findings.0.details`, far from the line that built it.

**This one may be intentional.** It is a schema divergence rather than an SDK packaging slip: an audit
trail arguably should not repeat `details` payloads. The actionable item is therefore **raise the
misleading description upstream**, not assume the models are wrong.

**Evidence.**
```bash
uv run python -c "
from adcp.types.generated_poc.governance.get_plan_audit_logs_response import Finding as AF
from adcp.types.generated_poc.governance.check_governance_response import Finding as RF
print(sorted(set(RF.model_fields) - set(AF.model_fields)))"
# -> ['details', 'source_plan_id', 'uncertainty_reason']
```

**Measure.** `main._audit_findings` **constructs** the audit `Finding` by naming its arguments. There is
no copied field list and no `if value is not None` filter standing in for required-field checks — the
model enforces both. An SDK release that widens the audit variant is picked up by naming the new field,
not by editing a copy of its field set.

**Retirement.** Only if the two converge: pass findings through unchanged. If instead the audit variant
merely stops forbidding extras, **keep the projection** — a response finding would then pass through
silently, which is worse than the current failure.

---

## SDK-GAP-5 — governance component types are not re-exported from `adcp.types`

**Symptom.** `adcp.types` re-exports the top-level request/response models but none of their components.
Absent: `Entry`, `Finding`, `Summary`, `Statuses`, `Escalation`, `DriftMetrics`, `Adcp`, `Governance`,
`Idempotency`.

**This is a re-export gap, not a missing-type gap**, and the distinction matters more than the
inconvenience. Reading "not in `adcp.types`" as "the SDK does not have it" is what leads to hand-building
a dict whose keys mirror a model — the exact failure `use-the-adcp-sdk.md` exists to prevent. The types
exist and are complete.

**Evidence.**
```bash
uv run python -c "import adcp.types as t; \
print([n for n in ('Entry','Summary','Statuses','Escalation','DriftMetrics','Adcp','Governance', \
'Idempotency') if not hasattr(t, n)])"
```

**Measure.** Two clearly-marked import blocks in `main.py` reach into
`adcp.types.generated_poc.governance.get_plan_audit_logs_response` and
`adcp.types.generated_poc.protocol.get_adcp_capabilities_response`. Both carry an `SDK-GAP-5` comment
saying they are a workaround rather than evidence of a missing type.

**Retirement.** Move the names into the `from adcp.types import (...)` block and delete both
`generated_poc` blocks. The canary reports a partial fix by listing which names moved.

---

## SDK-GAP-6 — `target_agent` and `consultation_context` are in the spec but not in the SDK

**Symptom.** Two fields AdCP 3.1 declares on `check_governance` have no counterpart in the generated
models, so nothing validates them and nothing hints they exist.

From the spec's **Buyer-side intent check** table (`docs/governance/campaign/responsibilities`):

| Field | Spec text | In `CheckGovernanceRequest`? |
|---|---|---|
| `caller` | "Identifies the buyer-side orchestrator making the governance check" | yes |
| `target_agent` | "Exact downstream service URL; becomes the signed token audience and stays outside the business payload" | **no** |

And on the response:

> Only `approved` returns a `governance_context` token. […] A `conditions` response instead carries
> `consultation_context`, a non-authorizing negotiation handle.

`CheckGovernanceResponse` has `governance_context` and **no** `consultation_context`.

**Why this one bit hard.** With no declared `target_agent`, the seller a check is *about* had nowhere
obvious to go, so this project put it in `caller` — the field whose own SDK description is "URL of the
agent making the request". The governance agent then read the token audience back out of `caller`, so both
halves agreed with each other and neither agreed with the spec. Two consequences, both found only by a
live end-to-end run:

1. Every first intent check failed to resolve the spend-aggregate key, degraded `budget_authority` to a
   `critical` unknown at confidence 0.0, and returned `conditions` instead of `approved`.
2. The token's `aud` was whatever the buyer had in `caller` — the AgentCore invoke endpoint — so every
   governed `create_media_buy` was rejected `PERMISSION_DENIED` by the seller, moments after the
   governance agent approved it.

A declared field would have made the right place obvious.

**Evidence.** Absent from **both** versions:

```bash
uv run python -c "
from adcp.types.generated_poc.governance.check_governance_request import CheckGovernanceRequest as Q
from adcp.types.generated_poc.governance.check_governance_response import CheckGovernanceResponse as R
print('target_agent:', 'target_agent' in Q.model_fields)
print('consultation_context:', 'consultation_context' in R.model_fields)"
```

A `model_fields` check only covers declared fields, so the 7.0.2 wheel was also inspected whole:

```bash
python3 -m pip download adcp==7.0.2 --no-deps -d /tmp/adcp702 && cd /tmp/adcp702 && unzip -qo *.whl -d x
grep -rl "target_agent\|consultation_context" x/   # no output: absent from the entire package
```

**Measure.** Both fields travel on `extra="allow"`, which both models set. `main.TARGET_AGENT_FIELD` names
the request field once and `_target_agent` reads it via `model_extra`; the buyer sets it in
`adcp_tools.adcp_check_governance`. `consultation_context` is written into the response in
`handle_check_governance` and read back by `_audience_from_governance_context`.

**Retirement.** When the fields are declared, read them as ordinary attributes, drop the `model_extra`
fallbacks, and delete `TARGET_AGENT_FIELD`.

---

## What is deliberately NOT recorded here

Absences that are AdCP's design rather than SDK shortfalls. Filing them as gaps would misdirect whoever
reads this next.

| Absence | Why it is not a gap |
|---|---|
| No task marks a plan `completed` | `flight.end` is required on the plan, so the lifecycle end is derivable. `main._plan_status` uses it. |
| No task resolves an escalation | `CAMPAIGN_SUSPENDED`'s own recovery hint is "contact the plan operator" — the human is deliberately out of band. Hence `resolve_escalation.py` and no MCP tool. |
| `DriftMetrics.thresholds` unpopulated | By its own description, organisation configuration echoed for visibility. There is none to echo. |
| `DriftMetrics.escalation_rate_trend` unpopulated | Needs a band for `stable`, which is a judgment rather than a measurement. |
| No per-agent delegation aggregate | The spec explicitly forbids it — it reopens the fragmentation hole the `(buyer_agent, seller_agent, account_id)` key closes. |
