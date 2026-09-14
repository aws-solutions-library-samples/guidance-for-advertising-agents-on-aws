# AdCP 3.0 Compliance — Buyer Agent

Subject: `agents/buyer/reference-buyer/` — the LLM-driven Strands Agent (`agent.py`,
`adcp_tools.py`) deployed to AWS Bedrock AgentCore Runtime twice: as an
HTTP runtime (`app.py`, ARN `.../runtime/adcp_buyer_agent-<runtime-id>`) for
this project's own chat UI, and as an A2A server
(`a2a_runtime/a2a_entrypoint.py`, ARN `.../runtime/adcp_buyer_agent_a2a-TU2G7j8xtB`)
so external agents can invoke it directly.

Graded against AdCP's own normative references:
[Required tasks by protocol](https://docs.adcontextprotocol.org/docs/protocol/required-tasks)
("Orchestrator (buyer)" section), the buyer wire-invariants skill
([`skills/call-adcp-agent/SKILL.md`](https://github.com/adcontextprotocol/adcp/blob/main/skills/call-adcp-agent/SKILL.md)),
and [Registering an agent](https://docs.adcontextprotocol.org/docs/registry/registering-an-agent)
(both fetched live for this review).

## Verdict

**Conformant as an AdCP orchestrator.** For the three discovery tasks this
agent calls (`get_products`, `get_adcp_capabilities`,
`list_creative_formats`), both normative response-extraction algorithms —
MCP and A2A — are implemented in full per spec, not approximated, and
version negotiation plus every required request field for those tasks is
correct and verified live, including a real A2A agent card and genuine
`message/send` calls from external callers.

This agent is deliberately read-only: no mutating task is implemented, so
the orchestrator requirements that only govern mutating flows (async task
polling, webhook delivery, `media_buy_id` continuity) are out of scope by
design rather than unmet obligations. One specific, minor gap remains
honestly on the books: structured error recovery via `adcp_error.issues[]`
is not implemented (see the wire-invariants table). The agent is not
registered in the AAO registry, which is an organizational step, not a
code gap (see "AAO registry status" below).

Unlike the reference seller, AdCP's spec does not define a fixed task list
a buyer/orchestrator must implement — "conformant orchestrator" is defined
by behavioral MUSTs (below), not a checklist of task names, since a buyer
only calls whichever seller tasks its use case needs.

## Orchestrator (buyer) requirements, per the spec

> "Orchestrators are not MCP/A2A servers — they call sales agent tasks.
> Conformant orchestrators MUST:"

| Requirement | Status | Evidence |
|---|---|---|
| Authenticate with sales agents | ✅ Yes | `seller_agents.py::_build_auth_headers` — `static_bearer` (env-sourced token) or `cognito_bearer` (minted per call via `auth.get_test_user_access_token()`), sent as `Authorization: Bearer <token>` on every MCP/A2A call. |
| Include required fields per request schemas | ✅ Yes, for the tasks called | `adcp_get_products` sends `brief`, `buying_mode` (correct enum: `browse\|brief\|refine`), and `brand: {domain}` when known; every core-task call is stamped with `adcp_major_version` (`adcp_tools.py::_with_version`). |
| Handle async task-level responses (`submitted`, `working`, `input-required`) and webhook delivery of completion artifacts | N/A — no mutating tasks implemented | This agent's task surface is deliberately limited to synchronous discovery calls (`get_products`, `get_adcp_capabilities`, `list_creative_formats`), none of which return those states, so no webhook receiver or async-polling code is needed and none exists. This scope decision is stated in `README.md`; it would need to be revisited if a mutating task (e.g. `create_media_buy`) is ever added. |
| Use `media_buy_id` for all subsequent operations | N/A | No `create_media_buy` call exists, so there is no `media_buy_id` to carry forward — a direct consequence of this agent's read-only scope. |
| Respect `creative_deadline` for creative uploads | N/A | No creative tasks (`sync_creatives`, `build_creative`) are implemented, by the same scope decision. |

## Wire-level correctness (buyer invariants skill)

Beyond the orchestrator MUSTs above, AdCP's buyer-side skill documents
several non-obvious wire invariants. Checked against this implementation:

| Invariant | Status | Notes |
|---|---|---|
| `adcp_major_version` on requests | ✅ Verified correct this session | `_with_version()` stamps it on `get_products`, `get_adcp_capabilities`, `list_creative_formats` — the three core AdCP tasks this agent calls. Deliberately **not** sent on a seller's vendor-namespaced extension tools, since those aren't core AdCP tasks and their schemas may reject an unrecognized field. |
| `brand: {domain}` on `get_products` | ✅ Verified correct this session | `adcp_get_products(brand_domain=...)`, falling back to `BUYER_BRAND_DOMAIN`; the system prompt instructs the agent to ask the user rather than omit it silently. |
| `buying_mode` real enum values | ✅ Verified correct this session | Sends the real `browse\|brief\|refine` enum, caught and corrected in place of an earlier fabricated `"wholesale"` value — the same kind of live-verification catch that keeps this table honest. |
| MCP response extraction (`isError` → `structuredContent` → text-fallback, `adcp_error`-only rejection) | ✅ Implemented in full | `adcp_tools.py::_extract_mcp_success_data` — matches AdCP's normative algorithm, not an approximation of it. |
| A2A response extraction (final vs. interim state, `TASK_STATE_*` normalization, last-vs-first DataPart, `{response: {...}}` wrapper rejection) | ✅ Implemented in full | `adcp_tools.py::extract_adcp_response_from_a2a` and helpers. This is the exact class of bug this project found and fixed in a *third-party* seller's endpoint during earlier testing — this agent's own extraction was written to the spec from the start, not discovered by trial and error. |
| A2A explicit-skill DataPart uses current field name | ✅ Verified correct this session | Sends `{skill, input}` (current) with `parameters` (legacy) alongside it for sellers still on the older field name, per the spec's compatibility-period guidance. |
| `idempotency_key` on mutating tasks | N/A | Only required on mutating tasks (`create_media_buy`, `sync_creatives`, etc.), none of which this agent implements. Would become a real, hard requirement the moment one is added — the spec's own guidance is unambiguous: "Missing the key → `adcp_error.code: 'VALIDATION_ERROR'`." |
| `account` `oneOf` variant discipline | N/A | Only relevant to `create_media_buy`/`update_media_buy`, not implemented by design. |
| Structured error recovery via `adcp_error.issues[]` (`pointer`/`keyword`/`variants`) | ❌ Not implemented | This is the one genuine gap in this document. Errors from a seller are passed through to the LLM as an opaque dict (`{"mcp_error": ...}`, `{"a2a_error": ...}`, or the raw `adcp_error` object) and handled via natural-language reasoning rather than the spec's recommended deterministic pointer-based retry. It works in practice for the read-only calls this agent makes today (the model reports the error plainly, per its system prompt), but the SDK's self-correcting retry pattern isn't built. Specific and scoped, but real. |

## What's intentionally out of scope

This agent is explicitly read-only by design (README: "it does not create
media buys or spend money. There is no `create_media_buy` tool available to
you"). AdCP does not mandate that a buyer implement any particular task
set — a buyer that only ever discovers inventory is not thereby
non-conformant, since the spec's orchestrator requirements are conditioned
on the tasks actually called ("Handle async... responses" only applies if
you call something that returns them). The N/A items above (async/webhook
handling, `media_buy_id` continuity, `creative_deadline`, `idempotency_key`,
the `account` `oneOf` variant) all follow directly from that one scope
decision — they are the deliberate boundary of what this agent does, not
gaps inside it.

## AAO registry status

**Not registered — an organizational step, not a code gap.** No
`brand.json` exists for this project, and this buyer agent's A2A endpoint
does not appear in the AAO registry catalog.

The registry's agent-type taxonomy includes `buying` alongside `brand`,
`sales`, `measurement`, `creative`, and `signals` — so a buyer/orchestrator
agent like this one is a registrable agent type in principle. Registration
itself requires the same organizational prerequisite as the seller side:
"Your operator must be an AAO member to enroll your agent in the registry
catalog," via the dashboard (`agenticadvertising.org/dashboard/agents`) or
the programmatic `POST /api/me/agents` endpoint. Nothing in this codebase
blocks registration — it's an account/organizational step that sits
outside this repo, not a code change — and it simply hasn't been pursued
for this project yet.

If registered, this agent's A2A endpoint
(`arn:aws:bedrock-agentcore:us-east-1:<aws-account-id>:runtime/adcp_buyer_agent_a2a-TU2G7j8xtB`,
invocable per `README.md`'s "Buyer Agent as an A2A server" section) is the
URL that would be submitted — it already serves a real, spec-shaped
[A2A agent card](https://a2a-protocol.org/) at
`/.well-known/agent-card.json` and handles genuine `message/send` calls
from external callers, verified live in this session.

## Bottom line

For what it actually does — LLM-driven inventory discovery against one
selected AdCP seller, callable either from this project's own UI or from
an external agent over A2A — this buyer agent correctly implements AdCP's
wire-level invariants for the three tasks it calls, including both
response-extraction algorithms in full per spec (not approximated) and the
version/brand/buying-mode fields those calls require, all confirmed live.
The entire async/webhook/idempotency surface is out of scope by deliberate
design, not by omission, since no mutating task exists to trigger it. The
one specific, honest gap left in this document is structured `issues[]`
error recovery, which isn't built yet. The agent is not registered with
the AAO registry, which requires an organizational AAO membership this
project does not have — not a code gap.

---

## Signature verification: what this buyer actually does, and what it does not

The buyer sits at a different point in the trust chain than a seller, so the gap is a different shape. It
is still a gap.

### What is verified today

**Cognito tokens are genuinely verified, against Cognito's real JWKS.** `auth.py` fetches
`https://cognito-idp.{region}.amazonaws.com/{pool}/.well-known/jwks.json`, caches the keys by `kid` for an
hour, and checks signature, issuer, audience and expiry with PyJWT. That is real verification, not a
platform delegation — unlike the reference seller, which trusts the AgentCore authorizer and decodes with
`verify_signature: False` because the platform has already done the work.

Two details worth keeping: the JWKS is fetched with `requests` rather than PyJWKClient's default transport
(some environments block the latter), and the hour-long cache is what stops every invocation reaching for
the network.

### What is NOT verified — the workaround, stated plainly

**No AdCP JWS is produced, verified, or forwarded by this buyer.** There is no `governance_context` on any
outbound call, and `adcp_tools.py` neither requests nor carries one.

This is the buyer-side half of the same availability problem: the **governance agent** mints the
`governance_context`, and until the reference governance agent existed there was none to obtain. The buyer's
job is to *ask* for it — by running an intent check — and then attach it to the request it sends the seller.

So the honest summary, and it is a strong statement: **this buyer currently cannot prove that any spend
commit it sends was approved by anything.** Cognito verification proves the buyer is who it says it is to
AWS. It says nothing about whether a governance agent authorised the action. Those are different
guarantees, and only the first is in place.

That also means the seller-side enforcement described in the seller's compliance document has nothing to
bite on yet — not because the sellers are permissive, but because there is no token in flight for them to
reject.

### What has to change once the governance agent is registered

In dependency order.

**1. Resolve the governance agent.** `GOVERNANCE_AGENTS_JSON`, mirroring `SELLER_AGENTS_JSON`, with
`auth_type` supporting `static_bearer` and `cognito_bearer`. One governance agent per account — AdCP is
explicit about the cardinality.

**2. Sync the plan before checking against it.** `sync_plans` must succeed first, or `check_governance`
answers `PLAN_NOT_FOUND` — a correctable ordering fault, not a denial. Two obligations the SDK enforces and
that are easy to miss until validation rejects them:

- `sync_plans` requires an `idempotency_key` of **at least 16 characters**.
- `report_plan_outcome` requires `governance_context`, so the token has to be retained from the check, not
  discarded once the call succeeds.

Three required plan fields have no honest source in a spoken brief — `brand`, `budget.reallocation_threshold`
and often `flight`. The settled answer is to **ask the user** rather than infer, and to sync no plan at all
when they are unavailable. Only `channels` and the geo fields are genuinely optional.

**3. Run an intent check before every spend commit.** AdCP: when a governance agent is configured, the buyer
MUST call `check_governance` before every spend-commit request — `create_media_buy`, `update_media_buy`,
`acquire_rights`, `update_rights`, `activate_signal`, `build_creative`. No dollar floor, no anomaly
threshold, no cold-start exemption. Send `tool` + `payload` and **not** `planned_delivery`; sending both
sets yields `AMBIGUOUS_CHECK_TYPE`.

**4. Attach the token, then honour the verdict.**

| Verdict | Buyer behaviour |
|---|---|
| `approved` | attach `governance_context` to the request, act before `expires_at` |
| `conditions` | apply the adjustments, **re-call** `check_governance`, then proceed |
| `denied` | do not send the tool call at all |
| async `submitted`/`working` | human review is in progress; **pending, not a verdict** |

`expires_at` must be respected: a lapsed approval is no approval, so a session idle between check and commit
needs a re-check.

**5. Report the outcome.** `report_plan_outcome` after the seller responds. This is the call that actually
commits budget — the governance agent tracks spend from confirmed outcomes, not from approved checks — so a
buyer that checks and books but never reports leaves the plan showing authority it has already spent. Note
the seller may confirm a **different** amount than requested; the agent commits the seller's figure and
returns findings on the difference.

**6. Do not mint tokens.** The buyer never signs a `governance_context`. It obtains one from the governance
agent and forwards it verbatim. Any code path here that produced its own signed token would be forging the
approval it is supposed to be carrying.

### Verifying, rather than only forwarding

Forwarding is the buyer's obligation; verifying is optional for it and worth doing anyway, because the buyer
is the party that would otherwise present a bad token to a seller and be blamed for it. If it is added, use
`adcp.signing` — `CachingJwksResolver` for key discovery, `jws.verify_detached_jws` /
`verify_jws_document` for the check, `crypto.verify_signature` underneath — and **not** PyJWT.

PyJWT is correct for Cognito and wrong here, and the reason is specific rather than stylistic: the SDK
constrains algorithms to Ed25519 and ES256 and rejects the rest, and its `parse_compact_jws` returns the
**original** base64url substrings instead of re-encoding them, because base64 decoding is lenient and a
round trip can change the bytes that were signed. Its resolvers also carry SSRF validation, blocked metadata
IPs, allowed-port restrictions and fetch cooldowns. Two verification stacks for two different token types is
correct; one stack doing both badly is not.

### What must change in the registry, and why that is the blocker

Same blocker as the seller's, from the other side: **nothing publishes the governance agent's public JWK.**
The private key lives in Secrets Manager. Until a JWKS is published at a stable HTTPS URL — a static object
on the existing CloudFront distribution is the simplest honest option, since an AgentCore invoke URL is
authenticated and therefore a poor place to serve a key a counterparty must fetch *before* it trusts
anything — no counterparty can verify a thing, and forward-only compliance is the only available state
rather than a choice.

The registry record for the governance agent then has to carry or lead to that key location, or every buyer
and seller needs the URL configured out of band, which is the manual step a registry exists to remove.

### One namespace caveat

The governance agent's registration script writes to the **`bedrock-agentcore`** namespace, which retires
**17 September 2026**. Deliberate — `agent-registry` is absent from the pinned botocore — and it must be
migrated before then. See `.kiro/steering/aws-agent-registry-namespace.md`, including the trap that the same
`bedrock-agentcore-control` client also serves Runtime calls which are **not** affected.
