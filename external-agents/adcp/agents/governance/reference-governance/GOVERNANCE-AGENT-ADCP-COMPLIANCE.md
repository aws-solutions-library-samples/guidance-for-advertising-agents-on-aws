# AdCP 3.1 Compliance — Reference Campaign-Governance Agent

Subject: `agents/governance/reference-governance/app/adcpRefGovernance/`
(`ReferenceGovernanceAgent`), deployed to AWS Bedrock AgentCore Runtime as an MCP server with a Cognito
JWT authorizer and a container build.

Graded against the campaign-governance
[specification](https://agenticadvertisingorg.mintlify.app/docs/governance/campaign/specification), the
[required-tasks reference](https://docs.adcontextprotocol.org/docs/protocol/required-tasks), and the
schemas shipped with `adcp==6.6.0` (spec 3.1.1). Every claim below was checked against one of those, and
where it could not be, this document says so.

## Verdict

**Conformant on all in-scope MUSTs.** Twelve were identified across successive readings of the
specification; eleven are implemented and one is deferred with its reason recorded. Five SDK gaps are
worked around, each with a canary that fails when the SDK closes it.

The count grew from seven to twelve, which is worth stating rather than presenting as a tidy dozen: the
first reading of the spec found seven, and M8..M12 were found by later readings of the same text. Rule
numbers were never reassigned, because they are cited from code and tests.

### Three conformance defects the unit tests could not see

Found by a live end-to-end run after the first deploy, with 255 governance tests green. Recorded here
because "the suite passes" was not evidence about any of them, and two were mutually reinforcing.

| # | Defect | Root cause | Fix |
|---|---|---|---|
| 1 | Every first intent check returned `conditions`, never `approved` | `_token_audience` and `_seller_for` each resolved "which seller is this check about?" their own way and disagreed. On a first check the former resolved a seller from `caller` while the latter resolved nothing, so the spend-aggregate key could not be formed and `budget_authority` degraded to a `critical` unknown | One helper, `_seller_subject`, preferring the spec's `target_agent`. `test_main.TestSellerResolutionIsSingleSourced` asserts the two agree |
| 2 | Every governed `create_media_buy` was rejected `PERMISSION_DENIED`, moments after approval | The buyer declared no `agent_url` per seller, so it sent the AgentCore *invoke* endpoint as the seller identity. The token's `aud` was therefore that URL, while each seller verifies against its own `THIS_SELLER_URL` | `deploy_all.py` step 9 writes `agent_url` read from each seller's own constant; `test_seller_identity_agreement.py` asserts buyer and seller agree, per seller |
| 3 | `get_creative_features` returned a response failing its own schema | The cold-cache branch returned `{"status": "working"}` — no `results`, no `errors` — which matches neither variant of the response union. Its comment claimed this was AdCP's async contract; per the 30-second rule `working` is an out-of-band signal on a held connection and is explicitly not polled | Fetch and answer `completed` on the same call, and construct the SDK's variant types so an unrepresentable body fails at the line that builds it |

Defects 1 and 2 shared a cause: AdCP declares `caller` (the buyer-side orchestrator) and `target_agent`
(the seller, which "becomes the signed token audience and stays outside the business payload") as separate
fields, and **neither is modelled by the SDK** (SDK-GAP-6). With no declared field, the seller went into
`caller`, and this agent read the audience back out of `caller` — so both sides agreed with each other and
neither agreed with the spec. Each looked like evidence for the other.

A fourth, separate fix: `conditions` now returns a `consultation_context` rather than a
`governance_context`. The spec is explicit that "Only approved returns a governance_context token", and
that on the seller's execution path "a conditions response is invalid […] and does not authorize a
commit". Issuing the authorising artefact with a verdict that withholds authorisation was one step short
of the reasoning that already withheld it from a denial.

## The twelve MUSTs

| # | Rule | Status | Where |
|---|---|---|---|
| M1 + M2 | Suspension on `human_review_required` or a policy demanding review | implemented | `main.handle_sync_plans`, both governed tasks |
| M3 | Fragmentation defense: trailing-window committed spend on `(buyer_agent, seller_agent, account_id)` | implemented | `aggregation.py`, `policy.evaluate` |
| M4 | Audit-entry content: findings, committed amounts, real plan status | implemented | `main.handle_get_plan_audit_logs` |
| M5 | Geo containment | **already satisfied** before this work; pinned by a regression test | `policy.check_geo` |
| M6 | Reject `account` sent as a sibling of `payload` | implemented | `main._has_sibling_account` |
| M7 | Honour `GOVERNANCE_MODE` | implemented | `main.governance_mode` |
| M8 | Idempotency on `report_plan_outcome` | implemented | `idempotency_backend.py` + the SDK's `IdempotencyStore` |
| M9 | Declare `aggregation_window_days` | implemented | `main.get_adcp_capabilities` |
| M10 | Portfolio `total_budget_cap` | **deferred to Feature 10** | — |
| M11 | Delegation validation | implemented | `main._delegation_refusal` |
| M12 | Credential bound to the plan's account | implemented | `main._account_mismatch` |

### M5 was already satisfied

Stated because it is the one entry that reads like an omission. Geo containment was implemented before this
feature began; the work here added a regression test so a later refactor cannot quietly drop it. No new
code was needed and none was written.

### M10 is deferred, not missed

Portfolio-level `total_budget_cap` needs a portfolio plan type this agent does not yet model. Implementing
half of it — a cap with no portfolio to apply it to — would be a declared control that does nothing, which
is the failure mode this whole document exists to avoid. K2=B records the decision.

## Three behaviours worth reading before grading the agent

**Committed budget comes from reported outcomes, never from approved checks.** An approval that is never
acted on consumes nothing, and one acted on for a different amount consumes the amount the seller actually
confirmed. AdCP is explicit, and it is the difference between knowing what was spent and knowing what was
permitted.

**Suspension refuses in every mode.** `audit`'s "always returns `approved`" governs the verdict of an
*evaluation*; a suspended plan is never evaluated, so there is no verdict for `audit` to force. The
alternative reading makes `audit` the single mode in which a plan awaiting human review transacts anyway.

**The aggregate is committed in every mode.** The mode governs what the agent does about spend, never
whether it counts it. An agent that stopped aggregating in `audit` would return from an audit period with a
trailing window missing exactly the spend that happened during it.

## What the agent will not claim

Absences that are deliberate. Each is a place where the honest answer is "no data" and the tempting answer
is a plausible number.

| Field | Why it is absent |
|---|---|
| `drift_metrics.escalation_rate_trend` | Needs a band for what counts as `stable`, which is a judgment about an organisation's tolerance rather than a measurement. |
| `drift_metrics.thresholds` | By its own description, organisation configuration echoed for visibility. There is none to echo. |
| `drift_metrics.human_override_rate`, when nothing is comparable | Null rather than 0.0. "No overrides" and "no resolved escalations" are different facts and only the first is evidence about calibration. |
| `Plan.governed_actions` | Needs a governed action's context and purchase type carried through to its outcome. An entry now would claim a buy that never happened. |
| `policies_evaluated` for registry-only policy ids | There is no registry to resolve them against. Listing them would claim a resolution that did not happen. |
| Semantic categories (`policy.SEMANTIC_CATEGORIES`) | Would need a real evaluator judging strategic alignment against natural-language objectives. Named as a seam rather than papered over. |
| A budget headroom figure when the aggregate is unreadable | Reporting spend we could not read as a number is the one outcome that silently reopens the fragmentation hole. |

Related: every finding this agent emits carries `confidence: 1.0`, because every check is arithmetic or set
membership. The journey view's radial arcs are therefore uniform. Varied confidence would be fabricated
spread on the single quantity that visual is built on.

## Resolution of a suspension is deliberately not a protocol surface

There is no AdCP task to un-suspend a plan. `CAMPAIGN_SUSPENDED`'s own recovery hint is *"contact the plan
operator"*, which places the human outside the protocol — a party able to clear its own suspension is not
suspended in any meaningful sense.

`resolve_escalation.py` is that operator surface. It registers no MCP tool and is imported by nothing in
the runtime, and a test asserts both by parsing each runtime module's imports rather than trusting the
convention.

The buyer learns of a resolution from the audit log it already reads: `status` returns to `active`,
`summary.escalations[]` carries the resolution and its timestamp, `statuses.human_reviewed` counts the
check, and `human_override_rate` becomes computable. No notification is sent, and none is needed.

## SDK gaps

Four, all present in both the pinned `adcp==6.6.0` (spec 3.1.1) and the latest release at the time of
writing, `adcp==7.0.2` (spec 3.1.15) — so none is an artefact of the pin. `SDK-GAPS.md` carries the
symptom, the evidence command, the measure and the retirement condition for each; `test_sdk_gaps.py`
carries a canary per gap that fails when the SDK closes it.

| # | Gap | Measure |
|---|---|---|
| SDK-GAP-1 | Governance error codes absent from `STANDARD_ERROR_CODES`, so `adcp_error` classifies them all `terminal` | `errors.code_metadata` reads `recovery`/`suggestion` from the SDK's own bundled manifest |
| SDK-GAP-2 | No public accessor for that manifest; `AdcpManifest` not re-exported | one function reaches `adcp/_schemas/` via `importlib.resources` — the only private-path access in the agent |
| SDK-GAP-3 | **RETRACTED** — the claim conflated the sync-result enum with the plan-lifecycle enum | none needed; the canary keeps the phantom gap from being rediscovered |
| SDK-GAP-4 | The audit `Finding` has five fields and `extra='forbid'`, while its own description says "same structure as check_governance response findings" (eight) | `main._audit_findings` constructs the audit type by naming its arguments |
| SDK-GAP-5 | Governance component types not re-exported from `adcp.types` | two clearly-marked `generated_poc` import blocks |

SDK-GAP-4 may be intentional — an audit trail arguably should not repeat `details` payloads — so the
actionable item is to raise the misleading description upstream, not to assume the models are wrong.

## Two defects this work found in code that already existed

Both were unreachable by any prior test, which is why they survived.

1. **The M6 gate emitted an invalid response.** It hand-built `{plan_id, adcp_error}`, three required
   fields short of a valid `CheckGovernanceResponse`, and raised a `ValidationError` on the way out instead
   of refusing the request. Now routed through the helper written to prevent exactly that.
2. **Both error layers were wrong.** Every refusal assigned the *payload* errors array to the *envelope*
   field, producing `adcp_error: {errors: [...]}` — neither of AdCP's two layers, and a shape no client
   reading either one would find. Now the array is at `errors[]` and its first entry is in `adcp_error` as
   a typed SDK `Error`.

## Not verified

Stated rather than assumed, because an unverified claim about conformance is worse than no claim.

- **Whether the runtime's execution role can export OTel traces.** `instrumentation.enableOtel` is now
  `true`, the distro is pinned and the Dockerfile activates it, but neither attached policy grants X-Ray or
  CloudWatch and the execution role is built by the AgentCore CDK construct. The permission may come from
  the platform; that is an inference. OTel export fails soft, so the first deploy settles it — look for
  export errors in the runtime log group with the agent otherwise healthy.
- **Live end-to-end behaviour of this feature's changes.** Everything here is verified by 248 unit and
  integration tests against moto. `deploy_all.py --only 14` is the live governance-journey verification and
  has not been run against the new code.
- **TTL enablement.** Written (`deploy_state_table.py --enable-ttl`) and tested against moto, deliberately
  **not executed**.

## Test coverage

| Suite | Count |
|---|---|
| `reference-governance` | 248 |
| `poseidon-seller` (governance gate added here) | 494 |
| buyer UI | 995 |
