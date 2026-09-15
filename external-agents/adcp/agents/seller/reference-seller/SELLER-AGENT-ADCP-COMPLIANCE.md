# AdCP 3.x Compliance — Reference Seller Agent

> **Assessment refreshed.** Since the previous revision this agent gained `sync_governance` and a
> complete seller-side governance verification path (`governance_verification.py`, 545 lines, wired
> into `create_media_buy`). The section formerly titled "Signature verification: what this seller
> actually does, and what it does not" described that work as future and named the missing JWKS
> publication as its blocker. Both have landed. That section is now
> ["Governance verification"](#governance-verification-implemented) and describes what runs.

Subject: `agents/seller/reference-seller/app/adcpRefSeller/` (`ReferenceSellerAgent`), deployed
to AWS Bedrock AgentCore Runtime as
`arn:aws:bedrock-agentcore:us-east-1:<aws-account-id>:runtime/RefSeller_adcpRefSeller-<runtime-id>`
(MCP transport, Cognito JWT authorizer, container build).

Built against the official `adcp` SDK, pinned at **`adcp==6.6.0`** (`app/adcpRefSeller/pyproject.toml`).
Graded against AdCP's own normative references:
[Required tasks by protocol](https://docs.adcontextprotocol.org/docs/protocol/required-tasks)
and [Registering an agent](https://docs.adcontextprotocol.org/docs/registry/registering-an-agent)
(both fetched live for the original review, current as of the AdCP 3.0/3.1 doc set).

## Verdict

**Conformant as a Media Buy Protocol sales agent, now with governance enforcement.** Three things
are implemented:

1. **All 7 required Media Buy tasks**, plus `get_adcp_capabilities`.
2. **The Accounts Protocol** — both `sync_accounts` and `list_accounts`, plus **`sync_governance`**,
   the Bind-phase task through which a buyer tells this seller which governance agent owns its plans.
3. **Seller-side governance verification** — this agent verifies a buyer's intent-phase
   `governance_context` JWS and then runs its own outbound execution check before it will book a
   media buy. It no longer relies on the buyer having behaved. See
   [Governance verification](#governance-verification-implemented).

`advertised_tools` in `main.py` declares **11 tools**, and
`test_advertised_tools_matches_dispatcher` pins that set to the dispatcher's own view — a guard added
after `sync_governance` shipped with a complete handler and 11 passing unit tests but no entry in the
set, so every real invocation answered `Unknown tool: sync_governance` while the unit tests passed by
calling the method directly.

`sync_creatives` remains correctly scoped out: this agent declares no creative library, so the
condition that would make it required never applies. It does declare **five creative formats**, two of
which are backed by published fixture assets the governance agent can actually fetch and evaluate.

The agent is registered in AWS's own AgentCore Registry, confirmed live via `SearchRegistryRecords`.
It is not registered in the AAO registry; see "Registry status" below for what that does and doesn't
mean for this codebase.

## What "conformant sales agent" requires

Per AdCP's Required Tasks reference, every agent implements `get_adcp_capabilities`
regardless of role. A **sales agent (seller)** on the Media Buy Protocol
additionally must implement:

| Task | Requirement | Implemented? |
|---|---|---|
| `get_products` | Required | ✅ Yes |
| `list_creative_formats` | Required | ✅ Yes |
| `create_media_buy` | Required | ✅ Yes |
| `update_media_buy` | Required | ✅ Yes |
| `get_media_buys` | Required | ✅ Yes |
| `get_media_buy_delivery` | Required | ✅ Yes |
| `provide_performance_feedback` | Required | ✅ Yes |
| `sync_creatives` | Conditional (required if hosting a creative library) | N/A — no creative library declared, by design |
| `list_creatives` | Optional | ❌ No |
| `sync_catalogs` | Optional | ❌ No |
| `sync_event_sources` / `log_event` | Optional / conditional | ❌ No |
| `sync_audiences` | Optional | ❌ No |

Accounts Protocol tasks, all advertised:

| Task | Requirement | Implemented? |
|---|---|---|
| `sync_accounts` | At least one of `sync_accounts` / `list_accounts` | ✅ Yes |
| `list_accounts` | At least one of `sync_accounts` / `list_accounts` | ✅ Yes |
| `sync_governance` | Required to participate in campaign governance as a seller | ✅ Yes — **new since the previous revision** |

**7 of 7 required Media Buy tasks are implemented.** The agent explicitly
declares which tools it advertises via `advertised_tools` in `main.py` — 11
in total, covering all required tasks plus the three Accounts Protocol
tasks — rather than inheriting the SDK's full ~60-tool surface and leaving
the rest unimplemented. `sync_creatives` is the one conditional task, and
it's correctly left unimplemented: this agent's scope is a media-buy
sandbox, not a creative library host, so the condition that would trigger
the requirement never applies.

### Test coverage

132 unit tests across six suites in `app/adcpRefSeller/`, runnable without AWS:

| Suite | Tests | Covers |
|---|---|---|
| `test_main.py` | 56 | Task handlers, dispatcher, response shapes, the advertised-tools/dispatcher pin |
| `test_state.py` | 26 | DynamoDB-backed media-buy, account and idempotency state |
| `test_governance_verification.py` | 20 | Intent-token verification: each failure mode, revocation, jti replay |
| `test_sync_governance.py` | 13 | The Bind task, including the one-agent-per-account invariant |
| `test_creative_formats.py` | 9 | Format declarations against real fixture PNG dimensions |
| `test_delivery.py` | 8 | Delivery projection and pacing |

Two live scripts exercise the deployed endpoint:
`agents/buyer/reference-buyer/verify_reference_seller_live.py` (task conformance) and
`app/adcpRefSeller/verify_seller_governance_gate_live.py` (the governance gate).

> **Live results are not restated here as current.** The previous revision recorded "19 of 19 live
> checks passed" for `verify_reference_seller_live.py`. That run predates `sync_governance` and the
> governance gate, so the count no longer describes the current surface, and re-running it needs a
> live deployment. Treat the figure as historical and re-run both scripts against your own deployment
> rather than reading a pass count from this document.

The original live run did earn its keep — it caught two real defects that reading the implementation
would not have:

- `get_media_buys` failed schema validation for accounts created via the `{brand, operator}` `oneOf`
  variant (missing `account_id`), fixed by a `_media_buy_account_view()` helper deriving `account_id`
  the same way `sync_accounts` does.
- Idempotency was not deduping over live MCP traffic because the request context carried no caller
  identity, fixed by a `context_factory` deriving `caller_identity` from the Cognito JWT `sub` claim.

Live verification also did its job: it caught and drove fixes for two real
issues before they could affect a real caller.

- `get_media_buys` was failing schema validation for accounts created via
  the `{brand, operator}` `oneOf` variant (missing `account_id`) — fixed by
  a `_media_buy_account_view()` helper that derives `account_id` the same
  way `sync_accounts` does.
- Idempotency (see below) wasn't deduping over live MCP traffic because the
  request context was missing caller identity — fixed by adding a
  `context_factory` that derives `caller_identity` from the Cognito JWT's
  `sub` claim.

Both were caught by testing against the real, redeployed endpoint rather
than assumed correct from the implementation alone, and both are fixed in
the current deployment.

### Accounts Protocol — implemented and verified

Per the spec: "Agents MUST implement at least one of `sync_accounts` or
`list_accounts` depending on their account model." This reference seller's
`get_adcp_capabilities` response declares an `account` block
(`require_operator_auth: false`, `supported_billing: ["operator", "agent"]`),
and implements both `sync_accounts` and `list_accounts`, backed by an
`AccountStore`. Verified live: a `sync_accounts` call with a
`{brand, operator}` pair persists and is retrievable via `list_accounts`.
The account model is fully backed by working tasks, matching the
declaration in its capabilities response.

**`sync_governance` — new since the previous revision.** The Bind phase of the buyer's journey: the
buyer tells this seller which governance agent owns its plans, and the seller stores it on the account
so a later `check_governance` call has somewhere to go. Shaped against
`adcp.types.generated_poc.account.sync_governance_request.SyncGovernanceRequest` in the pinned SDK.
Three details worth recording, because each is a place a looser implementation would be wrong:

- **One agent per account is a protocol invariant, not a simplification.** `Account.governance_agents`
  carries `minItems: 1, maxItems: 1`; the SDK's own field description calls the array shape
  wire-compatibility with 3.0 while `maxItems: 1` is load-bearing. A request carrying two agents is
  rejected rather than silently taking the first, which would leave the buyer believing both were
  registered.
- **Credentials are write-only.** They are stored, because the seller must present them when calling
  the agent, and never echoed. The response schema's `_serialize` would strip them anyway; the handler
  also simply never puts them in the response, since relying on a downstream stripper to hide a
  credential is one refactor away from a leak.
- **`categories` is omitted.** The response schema allows a per-agent list of what the agent
  evaluates. This seller cannot know that without querying the agent's own capabilities surface, and
  it is not in the request, so the field is absent rather than guessed.

### What it does correctly, in detail

- **Transport**: MCP only, satisfying "sales agents MUST support at least
  one transport (MCP or A2A)."
- **`get_adcp_capabilities`** is correctly shaped: `adcp.major_versions: [3]`,
  `supported_protocols: ["media_buy"]`, a `features` object
  (`pricing_models`, `channels`), and an honest
  `idempotency: {supported: true, replay_ttl_seconds: 86400}` — idempotency
  is real, backed by `adcp.server.idempotency.IdempotencyStore` on a
  DynamoDB backend, and dedupes per-caller over the live MCP transport,
  confirmed after the `context_factory` fix noted above.
- **Product schema correctness**: every product in `fixtures.py` includes
  `publisher_properties` and `reporting_capabilities` (both required by the
  core Product schema, and specifically the fields `get_media_buy_delivery`
  relies on, e.g. `reporting_capabilities.date_range_support`).
- **`format_id` is a structured object** (`{agent_url, id}`) everywhere,
  never a bare string — a common conformance mistake the spec calls out
  explicitly.
- **Deterministic, disclosed sandbox data**: every response sets
  `sandbox: true` (via the official `adcp` SDK's response builders), and
  `get_products` matches briefs by deterministic keyword overlap — no LLM,
  no randomness, same brief always returns the same products. This isn't
  an AdCP requirement, but it's what makes this agent usable as a stable
  test fixture rather than a source of flaky non-conformance findings for
  whoever builds against it. The catalogue is five products:
  `ref-ctv-sports-01`, `ref-mobile-rewarded-01`, `ref-display-news-01`,
  `ref-display-leaderboard-01`, `ref-audio-podcast-01`.
- **Creative formats backed by fetchable assets** — new since the previous
  revision. `list_creative_formats` declares five formats, and two of them
  (`display_300x250` and the `display_728x90` leaderboard) are *evaluable*:
  the governance agent's `get_creative_features` requires a real
  `creative_manifest.assets.main_image.url` and fetches the bytes over
  HTTPS, so `deploy_creative_fixtures.py` publishes the fixture PNGs to the
  buyer's existing CloudFront distribution. `test_creative_formats.py`
  asserts the three things that must line up — a `CREATIVE_FORMATS`
  declaration, an asset slot with exact required dimensions, and a
  published image of exactly those dimensions — reading real pixel
  dimensions out of each PNG's IHDR header. Before those tests, a fixture
  resize or a forgotten publish-list entry surfaced only as an unexplained
  `dimension_conformance: false` in a live session.

## Registry status: AgentCore Registry, not AAO

This agent is **registered in AWS's AgentCore Registry**, chosen as this
spec's AAO-registry substitute (see design.md's "Why the AgentCore
Registry, not AAO"):

- Registry: `arn:aws:bedrock-agentcore:us-east-1:<aws-account-id>:registry/lPDe9Zhqza89Meqi`
  (`adcp-reference-seller-registry`).
- Record: `arn:aws:bedrock-agentcore:us-east-1:<aws-account-id>:registry/lPDe9Zhqza89Meqi/record/THa8OtsidTfE`
  (`adcp-reference-test-seller`, status `APPROVED`).
- Verified live: `SearchRegistryRecords` resolves this record by name with
  the real tool listing `deploy_registry_registration.py` fetched from a live
  `tools/list` call. That listing was 10 tools at the time; the agent now
  advertises **11** (`sync_governance` was added), so the record needs the
  refresh described below to match.

Registration is manual/metadata-based rather than URL-sync'd, and that
scope was determined by live testing rather than assumption: real
`CreateRegistryRecord` calls against a throwaway registry confirmed the
registry's URL-sync crawler only supports `IAM` (SigV4) or `OAUTH`
(client_credentials) credential modes. This seller's `CUSTOM_JWT` (Cognito
bearer token) authorizer is a deliberate choice made elsewhere in this
project, and it doesn't fit either crawler mode. `IAM` mode was tried live
against the real registry and returned an HTTP 403, confirming it doesn't
work against a `CUSTOM_JWT`-authorized endpoint. Standing up a second
Cognito app client just to satisfy OAuth `client_credentials` was
considered and deliberately not done, consistent with design.md's stated
intent to avoid adding identity infrastructure solely for registry
plumbing. Given that, the record's tool listing is refreshed by re-running
`deploy_registry_registration.py` (which fetches the live `tools/list`)
rather than kept current automatically — a known, intentional operating
model for this registration, not an outstanding defect.

**This agent is not registered in the AAO registry.** That's a distinct,
out-of-scope surface: no `brand.json` or `adagents.json` exists anywhere in
this repository (verified by search), and this agent's URL does not appear
in the AAO registry catalog. Per AAO's own registration docs, that
registration can't be completed by writing code — it runs through AAO
membership, not this codebase:

1. An AAO member profile for the operating organization (created via the
   AAO dashboard or `POST /api/me/member-profile`).
2. Either the dashboard flow (sign in → `/dashboard/agents` → "Register
   agent" → provide URL, auth method, protocol) or the programmatic
   `POST /api/me/agents` endpoint (WorkOS API key or OAuth user JWT).
3. For `visibility: "public"` specifically, a paid AAO tier and a
   `primary_brand_domain` set on the member profile.

That sequence is an organizational/account action that sits with the AAO
member operator, not with this codebase, so it's correctly out of scope
here.

**Scope, stated plainly:** this agent is **AWS-discoverable, via the
AgentCore Registry and `SearchRegistryRecords`, and not AAO-discoverable**.
Buyers who browse `agenticadvertising.org`'s AAO catalog specifically won't
find it there — that's the boundary of what this codebase's registry work
covers, by design, not a gap inside it.

## Bottom line

Read as what it says it is — a fixed sandbox for exercising a buyer agent's
discovery, media-buy lifecycle and **governance** flows — this agent
implements the full required Media Buy Protocol task set plus the Accounts
Protocol, and it now enforces campaign governance rather than trusting the
buyer to have done so. `sync_creatives` is a deliberate, correctly-scoped
N/A since no creative library is declared; five creative formats are
declared and two are backed by fetchable fixture assets the governance
agent can evaluate. Idempotency is real, was tested against live traffic,
and is honestly advertised in capabilities.

What changed since the previous revision:

| | Then | Now |
|---|---|---|
| Advertised tools | 10 | **11** (`sync_governance`) |
| Buyer's intent token | Not verified — forward-only compliance | **Verified**: signature, `alg`/`typ`, `exp`/`iat`, `aud`, `sub`, `phase`, `kid`/`jti` revocation, `jti` replay |
| Seller's own execution check | Not performed | **Performed**, fail-closed on denial, conditions, unreachability and unrecognised verdicts |
| Governance key discovery | The stated blocker | **Resolved** via the buyer's `brand.json` → the governance agent's published JWKS |
| Creative formats | Declared only | Two **evaluable**, with published fixtures and dimension tests |

Two things remain open, and neither is blocked on anything external:

- **`plan_hash` is not checked**, so a plan amended after approval could be presented as the approved
  plan.
- **The registry record's tool listing predates `sync_governance`** and needs a
  `deploy_registry_registration.py` re-run.

AAO registry listing is still outside this codebase's scope, gated by AAO membership rather than by any
remaining engineering work here. Live pass counts from the previous revision are historical — re-run
`verify_reference_seller_live.py` and `verify_seller_governance_gate_live.py` against your own
deployment rather than reading a number from this document.

---

<a id="governance-verification-implemented"></a>

## Governance verification

This section exists because "the requests are authenticated" and "the AdCP signatures are verified" are
different claims. **Both are now true.** The previous revision of this document recorded only the first,
and listed the second as future work blocked on key publication; that work has landed, and this section
describes what runs rather than what should.

### What is verified today

**Inbound request authentication is verified — by the platform, not by this container.**

The runtime is deployed with `authorizerType: CUSTOM_JWT` (see `agentcore/agentcore.json`). AgentCore
Runtime validates the Cognito JWT — signature, issuer, and audience against the allowed-clients list —
**before** the request reaches this process. Only then does `requestHeaderAllowlist: ["Authorization"]`
forward the header in.

So `main.py` deliberately decodes that token **without** re-checking the signature:

```python
claims = jwt.decode(token, options={"verify_signature": False, "verify_aud": False,
                                    "verify_exp": False, "verify_iss": False})
```

That is not a shortcut with a security cost. The container reads the claims only to derive
`caller_identity` for idempotency scoping, and re-verifying would mean shipping the pool's JWKS into the
image to repeat work the platform already did and would not let the request through without. What it does
mean is that **this seller's trust in the caller is entirely delegated to the AgentCore authorizer.** Take
the authorizer away and the token becomes unchecked input.

### What the seller verifies about AdCP governance

`governance_verification.py` implements the seller-enforcement sequence in the order AdCP specifies:
verify the buyer's token first, then run the seller's own check. `create_media_buy` invokes both.

**The gate only engages for governed accounts.** The stored account's `governance_agents` list is what
arms it — an account with none is completely unaffected and the whole block is skipped. For a governed
account, a request arriving with no `plan_id` or no `governance_context` is refused with
`PERMISSION_DENIED` before any crypto runs.

**Step 1 — verify the buyer's intent-phase token** (`verify_intent_token`), short-circuiting on the
first failure. Every failure collapses to `PERMISSION_DENIED` for the caller; the specific reason
(`malformed`, `signature_invalid`, `unverifiable`, `expired`, `wrong_audience`, `wrong_subject`,
`wrong_phase`, `revoked`, `replayed`) is logged, not returned.

| Check | How |
|---|---|
| Signature, `alg` allowlist, `typ` match | `adcp.signing.jws.averify_jws_document` |
| `iss` → `brand.json` → `jwks_uri` → `kid` | `adcp.signing.brand_jwks.BrandJsonJwksResolver` |
| `exp` / `iat`, ±60s skew | This module's own check — see below |
| `aud` == this seller's URL | Claim comparison against `THIS_SELLER_URL` |
| `sub` == the `plan_id` being committed | Claim comparison |
| `phase` == `"intent"` | Claim comparison; a `purchase`-stamped token from a buyer is rejected |
| `kid` and `jti` revocation | `adcp.signing.revocation_fetcher.AsyncCachingRevocationChecker` |
| `jti` replay | `jti_first_seen`, a DynamoDB ledger keyed `GOVJTI#<jti>` |

Two details worth keeping:

- **The SDK does not check `exp`/`iat`, so this module does.** Found while writing this module's tests
  and confirmed against `averify_jws_document`'s docstring, which lists exactly four checks — header
  parse, `alg` allowlist, `typ` match, signature — none of them time-based. That is deliberate on the
  SDK's part, since the same generic verifier also handles revocation lists, whose freshness semantics
  are `next_update` rather than `exp`. A `governance_context`-specific caller therefore has to check
  time itself.
- **Stale revocation lists fail closed.** `RevocationListFreshnessError` past its grace window returns
  `unverifiable`, never "assume not revoked".

No code path here builds a raw HTTP client for any of the three counterparty-supplied document fetches
(`brand.json`, JWKS, revocation list). Every cryptographic and SSRF-sensitive operation is delegated to
the SDK, which is what keeps the algorithm allowlist, the blocked-metadata-IP checks, the port
restrictions and the fetch cooldowns in force. The one piece of local base64 work,
`_decode_header_unverified`, reads the `kid` out of a header that `averify_jws_document` has already
validated.

**Step 2 — the seller's own execution check** (`run_execution_check`): an outbound `check_governance`
MCP call carrying `plan_id`, `caller`, the buyer's `governance_context`, this seller's own
`planned_delivery` (built by `build_planned_delivery`), and `phase: "purchase"`. Outbound auth comes
from the `authentication` object `sync_governance` stored — `{schemes, credentials}` — and a scheme
other than `Bearer` is rejected rather than guessed at.

**Fail-closed on every non-approval.** The outcome maps to a distinct AdCP error:

| Execution-check outcome | `create_media_buy` result |
|---|---|
| `approved` | Proceeds; the purchase-phase token governs the rest of the lifecycle |
| `denied` / `conditions` | `GOVERNANCE_DENIED` |
| Transport failure, error result, non-dict payload, **or an unrecognised verdict value** | `GOVERNANCE_UNAVAILABLE` |

Treating an unrecognised verdict as unreachable rather than as approval is the point: AdCP makes
governance a gate, and when the gate cannot be read the default is halt.

`test_governance_verification.py` covers 20 cases across those failure modes, and
`verify_seller_governance_gate_live.py` proves the seller's own code actually ran against the deployed
governance agent — it drives the full
`sync_accounts → sync_governance → sync_plans → check_governance → create_media_buy` sequence through
the deployed buyer, then reads this seller's DynamoDB table directly and confirms a `GOVJTI#<jti>` item
exists. That item can only be written by `jti_first_seen` inside `verify_intent_token`, which is the
distinction that matters: a buyer-mediated success on its own is also consistent with the seller's
verification being entirely absent, because the buyer's own gate would have refused a bad request
before it ever arrived.

### What is still not verified

**`plan_hash` is not checked.** The claim comparisons cover `aud`, `sub`, `phase`, `exp`/`iat` and
revocation, but not that the plan being executed is byte-for-byte the plan that was approved. A plan
amended after approval could still be presented as the approved plan. Worth closing.

**Forward-only obligations still apply.** Verification is an addition to the audit chain, not a
replacement: the token is persisted and forwarded unchanged regardless of the verdict, because
auditors rely on that chain being unbroken.

### Key discovery: how the former blocker was resolved

The previous revision named this the blocker — verification needs the governance agent's public keys,
and nothing published them. Three pieces closed it, and they are worth knowing because the trust path
runs through the buyer, not the seller's configuration:

1. The reference governance agent publishes its **JWKS** (`deploy_jwks.py`) and its **revocation list**
   (`deploy_revocations.py`) as static objects on its own CloudFront origin
   (`deploy_governance_origin.py`). A static document was the right answer over an endpoint on the agent:
   an AgentCore Runtime MCP endpoint is reached through an authenticated invoke URL, which is a poor fit
   for a key document a counterparty must fetch *before* it trusts anything.
2. The buyer publishes **`brand.json`** (`deploy_brand_json.py`), which is the trust anchor. The seller
   resolves it *before* trusting any JWKS a token's `iss` claims to use, which is why
   `BrandJsonJwksResolver` is the resolver in use rather than a pinned `StaticJwksResolver`.
3. `deploy_all.py`'s `seller-trust-anchor` step (4.56) hands each seller that `brand.json` URL, so the
   anchor arrives by deploy rather than by hand-configured constant.

The registry does not have to carry the key location, because `brand.json` leads to it.

### One namespace caveat for whoever implements this

`agents/governance/reference-governance/app/adcpRefGovernance/deploy_registry_registration.py` writes to
the **`bedrock-agentcore`** namespace, which retires **17 September 2026**. That is deliberate — the
replacement `agent-registry` namespace is not present in the pinned botocore — and both this seller's
record and the governance agent's must be migrated before that date. See
`.kiro/steering/aws-agent-registry-namespace.md`, and note the trap recorded there: the same
`bedrock-agentcore-control` client is used for Runtime calls that are **not** affected, so a blanket rename
breaks every runtime lookup.
