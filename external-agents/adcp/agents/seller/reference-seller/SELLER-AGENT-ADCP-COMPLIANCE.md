# AdCP 3.0 Compliance — Reference Seller Agent

Subject: `agents/seller/reference-seller/app/adcpRefSeller/` (`ReferenceSellerAgent`), deployed
to AWS Bedrock AgentCore Runtime as
`arn:aws:bedrock-agentcore:us-east-1:<aws-account-id>:runtime/RefSeller_adcpRefSeller-<runtime-id>`
(MCP transport, Cognito JWT authorizer, container build).

Graded against AdCP's own normative references:
[Required tasks by protocol](https://docs.adcontextprotocol.org/docs/protocol/required-tasks)
and [Registering an agent](https://docs.adcontextprotocol.org/docs/registry/registering-an-agent)
(both fetched live for this review, current as of the AdCP 3.0/3.1 doc set).

## Verdict

**Conformant as a Media Buy Protocol sales agent.** All 7 required Media
Buy tasks and the Accounts Protocol are implemented and verified live
against the redeployed MCP endpoint — 19 of 19 live checks passed
(`agents/buyer/reference-buyer/verify_reference_seller_live.py`). The agent is registered in
AWS's own AgentCore Registry, confirmed live via `SearchRegistryRecords`
resolving the current tool listing. It is not registered in the AAO
registry; see "Registry status" below for exactly what that does and
doesn't mean for this codebase. `sync_creatives` is correctly scoped out as
not applicable, since this agent deliberately declares no creative
library.

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

**7 of 7 required Media Buy tasks are implemented**, all verified live
against the redeployed MCP endpoint
(`agents/buyer/reference-buyer/verify_reference_seller_live.py`, 19/19 checks passed). The
agent explicitly declares which tools it advertises via `advertised_tools`
in `main.py` — covering all required tasks plus the Accounts Protocol
tasks below — rather than inheriting the SDK's full ~60-tool surface and
leaving the rest unimplemented. `sync_creatives` is the one conditional
task, and it's correctly left unimplemented: this agent's scope is a
media-buy sandbox, not a creative library host, so the condition that
would trigger the requirement never applies.

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
  whoever builds against it.

## Registry status: AgentCore Registry, not AAO

This agent is **registered in AWS's AgentCore Registry**, chosen as this
spec's AAO-registry substitute (see design.md's "Why the AgentCore
Registry, not AAO"):

- Registry: `arn:aws:bedrock-agentcore:us-east-1:<aws-account-id>:registry/lPDe9Zhqza89Meqi`
  (`adcp-reference-seller-registry`).
- Record: `arn:aws:bedrock-agentcore:us-east-1:<aws-account-id>:registry/lPDe9Zhqza89Meqi/record/THa8OtsidTfE`
  (`adcp-reference-test-seller`, status `APPROVED`).
- Verified live: `SearchRegistryRecords` resolves this record by name with
  the current real tool listing (all 10 advertised tools).

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
discovery and media-buy lifecycle flows — this agent implements the full
required Media Buy Protocol task set, correctly and verifiably. All 7
required Media Buy tasks and the Accounts Protocol are implemented and
confirmed live against the redeployed MCP endpoint (19/19 checks);
`sync_creatives` is a deliberate, correctly-scoped N/A since no creative
library is declared. Idempotency is real, tested against live traffic, and
honestly advertised in capabilities. The agent is registered in AWS's
AgentCore Registry and confirmed AWS-discoverable via
`SearchRegistryRecords`. AAO registry listing is the one piece left
outside this codebase's scope, gated by AAO membership rather than by any
remaining engineering work here.

---

## Signature verification: what this seller actually does, and what it does not

This section exists because "the requests are authenticated" and "the AdCP signatures are verified" are
different claims, and only the first is true here.

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

### What is NOT verified — the workaround, stated plainly

**No AdCP JWS is verified anywhere in this seller. There is no `governance_context` verification, and no
JWKS resolution for any AdCP counterparty.**

The reason is availability rather than intent: until the reference governance agent existed there was no
signed `governance_context` to verify, because it is the **governance agent** that mints one — not the
buyer, and not us.

So the current posture is what AdCP calls **forward-only compliance**, and it is a legitimate intermediate
state the spec explicitly allows:

> A seller that has not implemented verification MUST still persist and forward the `governance_context`
> token unchanged, because auditors and regulators rely on that chain being unbroken.

**The honest summary: platform-level Cognito JWT validation stands in for agent-level AdCP signature
verification. Those are different guarantees.** The Cognito token proves *who called us*. An AdCP
`governance_context` proves *that a named governance agent approved this specific action against a specific
plan*. This seller currently has the first and not the second, so it cannot tell an approved spend commit
from an unapproved one on cryptographic grounds.

### What has to change once the governance agent is registered

Concrete, in dependency order. None of it is speculative — the SDK already ships the pieces.

**1. Require the token on spend commits.** AdCP: a seller receiving a spend commit for a plan with a
configured governance agent MUST require a valid, in-date **intent-phase** token and MUST reject with
`PERMISSION_DENIED` otherwise. Today `create_media_buy` does not look for one. This is the change that
makes the buyer-side MUST real: a buyer that skips its intent check cannot produce a valid token, so the
commit fails before the seller's own execution check.

**2. Verify it, using `adcp.signing` rather than by hand.** The SDK provides the resolvers and the
verifier:

| Need | Use |
|---|---|
| Fetch and cache the governance agent's public keys | `adcp.signing.jwks.CachingJwksResolver` |
| A pinned key for a known counterparty | `adcp.signing.jwks.StaticJwksResolver` |
| Keys discovered from a brand's published document | `BrandSourcedJwksResolver` / `BrandJsonJwksResolver` |
| Verify the JWS itself | `adcp.signing.jws.verify_detached_jws`, `verify_jws_document` |
| Signature primitives | `adcp.signing.crypto` — `ALG_ED25519`, `ALG_ES256`, `verify_signature` |

Use them rather than PyJWT for two reasons the SDK's own code documents: it constrains algorithms to
Ed25519 and ES256 and rejects anything else, and `parse_compact_jws` returns the **original** base64url
substrings rather than re-encoding them, because base64 decoding is lenient and a round trip can change
the bytes that were hashed. The resolvers also carry SSRF validation, blocked metadata IPs, allowed ports
and fetch cooldowns — all of which a hand-rolled JWKS fetch would omit.

**3. Check the claims, not just the signature.** A valid signature over the wrong claims is worse than no
signature, because it looks verified:

- `phase` == `"intent"` for a buyer-presented token. **Do not accept `purchase`** — that is the phase the
  seller's *own* execution check produces. The reference governance agent derives this from the check type
  precisely because the SDK's `phase` field defaults to `purchase`, and a token stamped `purchase` from a
  buyer means the buyer did not run an intent check.
- `sub` == the `plan_id` being committed against.
- `aud` == **this seller's** URL. A token addressed to another seller must be rejected; that is the
  property that makes forwarding it safe.
- `exp` in the future. AdCP: a lapsed approval is no approval.
- `plan_hash` matches the plan the seller is being asked to execute, so a plan amended after approval
  cannot be presented as the plan that was approved.

**4. Perform the execution check.** Call `check_governance` with `governance_context` +
`planned_delivery`. Send **only** those — including `tool` and `payload` alongside them yields
`AMBIGUOUS_CHECK_TYPE`, because the agent infers the check type from which fields are present. The
response carries the purchase-phase token bound to the newly assigned `media_buy_id`, which governs the
rest of the lifecycle.

**5. Halt when the agent is unreachable.** AdCP is explicit: governance is a gate, and when the gate cannot
be reached the default is **halt**, with retry and backoff, not proceed. For a committed check the seller
sets a timeout and treats a non-response as `denied`.

**6. Keep forwarding the token verbatim regardless.** Even after verification lands, the token must be
persisted and forwarded unchanged. Verification is an addition to the audit chain, not a replacement for
it.

### What must change in the registry, and why that is the blocker

Verification needs the governance agent's **public keys**, and today there is nowhere to fetch them from.
The private key lives in Secrets Manager; nothing publishes the public JWK. So this is a discovery problem
before it is a crypto problem.

Two things have to be true before step 2 above can work at all:

1. **The governance agent publishes a JWKS** — either a `/.well-known/jwks.json`-style endpoint on the
   agent itself, or a static document at a stable HTTPS URL. Note that an AgentCore Runtime MCP endpoint is
   reached through an authenticated invoke URL, which is a poor fit for a key document a counterparty must
   fetch **before** it trusts anything; a static object on the existing CloudFront distribution is the
   simpler honest answer.
2. **The registry record carries, or leads to, that key location.** The governance agent's record in the
   AWS Agent Registry is what a seller resolves to find the agent; the key location has to be reachable
   from there, or every seller needs the URL configured out of band — which is exactly the manual step a
   registry exists to remove.

Until both exist, a seller cannot verify anything and forward-only compliance is not a choice, it is the
only available state.

### One namespace caveat for whoever implements this

`agents/governance/reference-governance/app/adcpRefGovernance/deploy_registry_registration.py` writes to
the **`bedrock-agentcore`** namespace, which retires **17 September 2026**. That is deliberate — the
replacement `agent-registry` namespace is not present in the pinned botocore — and both this seller's
record and the governance agent's must be migrated before that date. See
`.kiro/steering/aws-agent-registry-namespace.md`, and note the trap recorded there: the same
`bedrock-agentcore-control` client is used for Runtime calls that are **not** affected, so a blanket rename
breaks every runtime lookup.
