# External Agents

Self-contained, separately-deployed AgentCore agents that the main agent
ecosystem reaches over the **A2A protocol** (via each agent's
`external_agent_configs` entry in
`agentcore/deployment/agent/global_configuration.json`).

These are deployed independently from the main `agentcore/deployment`
stack so they can live in their own runtime (and, if desired, their own
account/region) while still being invoked as A2A tools.

## Layout

```
external-agents/
├── deploy_external_agents.py     # idempotent deployer (config object -> AgentCore runtime)
├── AdCreationAgent/
│   ├── agentcore.json            # the config object describing the agent
│   ├── ad_creation_agent.py      # Strands A2AServer (port 9000, message/send)
│   ├── creative_builder.py       # composites IAB display formats -> S3 -> presigned URLs
│   ├── requirements.txt
│   ├── Dockerfile                # ARM64, serves A2A at 0.0.0.0:9000/
│   └── .dockerignore
└── AdCPSellerAgent/               # fully AdCP 3.1-compliant sales agent (A2A + MCP)
    ├── agentcore.json            # TWO runtimes from one image: A2A + MCP
    ├── adcp_seller_agent.py      # entrypoint; A2A or MCP by $ADCP_TRANSPORT
    ├── adcp/                     # schemas/registry, store (DynamoDB), idempotency, tasks, RFC 9421 signing
    ├── catalog/                  # real inventory (brief match) + deterministic delivery
    ├── tasks/                    # AdCP task handlers + shared router
    ├── fixtures/                 # sample publisher inventory + creative formats (real, labeled)
    ├── discovery/                # adagents.json / brand.json generator (well-known records)
    ├── test_adcp_conformance.py  # transport-independent conformance tests (no AWS needed)
    ├── requirements.txt
    ├── Dockerfile                # ARM64
    └── .dockerignore
```

State is recorded in `.external-agents.json` at the repo root, keyed by
`<AgentName>:<region>`, which makes re-runs idempotent (an existing
runtime is **updated** to a new version rather than recreated).

## AdCreationAgent

A Strands agent served over A2A. Its `build_ad_creatives` tool does real
work — no fabricated output:

1. Extracts source image URLs (`http(s)://` or `s3://`) and a brand brief
   (brand name, headline, colors) from the request.
2. Composites each source image onto every standard **IAB display ad
   unit**: Medium Rectangle (300x250), Leaderboard (728x90), Wide
   Skyscraper (160x600), Half Page (300x600), Billboard (970x250), and
   Mobile Leaderboard (320x50).
3. Uploads each PNG to S3 and returns, per creative: the **object name**,
   the **IAB ad-unit category**, the pixel size, and a **presigned URL**
   (never raw image bytes).

If no usable image assets are supplied, it returns an explicit error
instead of inventing creatives.

> **Moved:** The IAB AAMP buyer/seller marketplace no longer ships as an
> in-repo Strands agent under `external-agents/`. It is now deployed from the
> upstream IAB Tech Lab **seller-agent** and **buyer-agent** repos as their own
> AgentCore HTTP runtimes, registered as `AAMPSellerAgent` /
> `AAMPBuyerAgent`, and invoked by the **AgencyAgent** over IAM/SigV4. See
> [`docs/aamp-deployment-modes.md`](../docs/aamp-deployment-modes.md) and the
> root README's "IAB AAMP Marketplace" section for details.

## AdCP Seller Agent

A fully [Ad Context Protocol](https://docs.adcontextprotocol.org) (**AdCP 3.1**)
compliant **sales agent** (publisher/SSP sell-side). It implements the AdCP
Media Buy sell-side task surface to the letter of the spec
(schemas under `https://adcontextprotocol.org/schemas/v3/`). Design spec:
[`.kiro/specs/adcp-seller-agent/`](../.kiro/specs/adcp-seller-agent/design.md).

Highlights:

- **Dual transport** — the same task set + v3 schemas over **A2A and MCP**,
  from one image via two runtimes: `AdCPSellerAgent` (A2A, wired into the
  stack) and `AdCPSellerAgentMcp` (MCP, for external buyers). Both are listed
  in `adagents.json`.
- **Required sell-side tasks** — `get_adcp_capabilities`, `get_products`
  (brief + refine), `list_creative_formats`, `create_media_buy`,
  `update_media_buy`, `get_media_buys`, `get_media_buy_delivery` (with
  `reporting_capabilities` on every product), `provide_performance_feedback`,
  and `sync_accounts`. Capabilities are reported from the live registry so
  declared vs implemented can't drift.
- **Trust** — Cognito JWT inbound auth **plus** RFC 9421 HTTP
  Message Signature verification on mutating tasks and idempotency
  (`replayed: true` on replay; one `media_buy_id` per key). Fails closed when
  a required signature can't be verified.
- **Persistence** — DynamoDB (`ADCP_TABLE_PREFIX`); a non-durable in-process
  store backs local tests only.
- **No fabricated data** — products/pricing come from a real seeded catalog
  (`fixtures/`), delivery is computed deterministically from bookings + the
  clock (zeros before flight, never invented numbers).

Run the conformance tests locally (no runtime needed):

```bash
python external-agents/AdCPSellerAgent/test_adcp_conformance.py
```

Deploy both runtimes (same image, different `protocol`), then publish the
discovery records:

```bash
python external-agents/deploy_external_agents.py --agent AdCPSellerAgent \
    --stack-prefix <prefix> --unique-id <id> --region us-east-1
python external-agents/deploy_external_agents.py --agent AdCPSellerAgentMcp \
    --stack-prefix <prefix> --unique-id <id> --region us-east-1

python external-agents/AdCPSellerAgent/discovery/generate_discovery.py \
    --publisher-domain <domain> --a2a-url <url> --mcp-url <url> \
    --jwks-uri <url> --s3-bucket <ui-bucket> --cloudfront-distribution <dist-id>
```

The A2A runtime wires into `PublisherAgent.external_agent_configs`
(`AdCPSellerAgent_Runtime`, tagged `adcp: true`). The deployer **auto-provisions
the DynamoDB tables** (`<prefix>-AdCPSeller-{Catalog,MediaBuys,Tasks,Idempotency,Accounts}-<id>`)
and scopes the execution role to them — driven by the `dynamodbStore` block in
`agentcore.json` — so `--stack-prefix`/`--unique-id` are required. For
cross-account OAuth it uses the same `A2A_DISCOVERY_URL`/`A2A_CLIENT_ID`/
`A2A_POOL_ID` env the ecosystem script exports.

The demo ships with `ADCP_REQUIRE_SIGNATURES=false` baked into `agentcore.json`
(the buyer-side RFC 9421 signing isn't wired yet); flip it to `true` once a
signing buyer is in place to enforce the full AdCP trust surface. The stack's
`PublisherAgent` cannot yet call this agent end-to-end (buyer-side AdCP client
mode pending) — the runtimes deploy and are reachable/testable standalone.

## Inbound auth (IAM vs OAuth)

Each agent declares its inbound auth in `agentcore.json` via `inboundAuth`:

- **`oauth` (AdCPSellerAgent)** — the runtime is created with a Cognito **JWT
  authorizer**, so callers authenticate with a **Cognito OAuth bearer token**.
  This is the right choice for a genuinely external agent: it may live in a
  **different account or organization**, and OAuth needs **no cross-account
  IAM trust** — the runtime validates the bearer against the Cognito
  discovery URL. The caller's `external_agent_configs` entry is marked
  `authType: "oauth"`; the repo's `A2ATokenManager` mints the bearer from
  credentials in SSM (`oauthCredentials.ssmPath`) using the configured
  Cognito pool/client.
- **`iam` (AdCreationAgent)** — SigV4 inbound. Simplest for a **same-account**
  agent reached over `bedrock-agentcore:InvokeAgentRuntime`.

`--auth oauth|iam` overrides the per-agent default. OAuth requires a Cognito
discovery URL and client id, supplied via `--cognito-discovery-url` /
`--cognito-client-id` or the `$A2A_DISCOVERY_URL` / `$A2A_CLIENT_ID` env vars
that `scripts/deploy-ecosystem.sh` already exports. The deployer **fails
loudly** if `oauth` is requested without them rather than silently shipping a
SigV4-only runtime.

### Inbound "Bearer Token" for self-deployed agents

The agent editor UI offers a **Bearer Token** inbound auth option. It is
important to be precise about what that does and does not do:

- **It applies only to agents you deploy yourself here** (via
  `deploy_external_agents.py`). It does **not** add any inbound enforcement to
  the shared **AdFabric orchestrator runtime** — that runtime is never modified
  by this feature, and its `handler.py` does not inspect an `Authorization`
  header in application code.
- **Enforcement is the deploy-time runtime authorizer, not application code.**
  Inbound auth on an AgentCore runtime is enforced by the
  `authorizerConfiguration` set at deploy time (`deploy_runtime(authorizer=…)`),
  exactly like the `oauth` path above. There is no app-level string comparison
  of a pasted token.
- **AgentCore validates a JWT, not an arbitrary opaque string.** A Cognito/JWT
  `customJWTAuthorizer` validates a **JWT issued by the configured issuer**
  (the Cognito discovery URL) and checks the allowed client/audience. So a
  pasted "bearer token" is only accepted inbound **if it is a JWT that the
  configured authorizer accepts**. Pasting an arbitrary opaque string and
  selecting "Bearer Token" inbound does **not** make the endpoint enforce that
  string — the UI states this and does not show a "secured/enforced" badge for
  a configuration the authorizer cannot validate.

Practically, to secure a self-deployed agent's inbound A2A endpoint, deploy it
with a JWT authorizer (the `oauth`/`customJWTAuthorizer` path) and have callers
present a JWT from that issuer. The inbound "Bearer Token" UI stores an
operator-supplied token reference (encrypted SecureString) plus optional expiry
for that caller; it is the deploy-time authorizer selection — not storing the
token — that actually enforces inbound auth. Wiring a concrete
`authorizerConfiguration` for a specific self-deployed agent (e.g.
AdCPSellerAgent) is handled by that agent's own deploy/spec, not by this
bearer-token feature.

## Deploy

Prerequisites: Docker with `buildx`, AWS credentials, an S3 bucket the
runtime may write to (only for agents that use S3), and — for `oauth` agents
— a Cognito user pool (discovery URL + app client).

The `--stack-prefix`/`--unique-id` flags let the deployer find the live
DynamoDB `AgentConfig` table (`<prefix>-AgentConfig-<id>`) so the running app
actually picks up the agent — see "Live registration" below.

```bash
# AdCreationAgent — same-account, IAM/SigV4; needs an S3 bucket for creatives
python external-agents/deploy_external_agents.py \
    --agent AdCreationAgent \
    --s3-bucket <your-generated-content-bucket> \
    --stack-prefix <prefix> --unique-id <id> \
    --region us-east-1 \
    [--profile <aws-profile>]

# AdCPSellerAgent — A2A + MCP runtimes, Cognito OAuth bearer (no S3). Cognito values
# come from the env vars deploy-ecosystem.sh exports, or pass them explicitly.
export A2A_DISCOVERY_URL="https://cognito-idp.us-east-1.amazonaws.com/<pool-id>/.well-known/openid-configuration"
export A2A_CLIENT_ID="<app-client-id>"
export A2A_POOL_ID="<pool-id>"
python external-agents/deploy_external_agents.py \
    --agent AdCPSellerAgent \
    --stack-prefix <prefix> --unique-id <id> \
    --region us-east-1 \
    [--profile <aws-profile>]
```

The deployer will:

1. Ensure a least-privilege execution role (ECR pull, logs, X-Ray,
   Bedrock model invoke, workload-identity token). For agents deployed
   with `--s3-bucket`, it also grants `s3:GetObject` / `s3:PutObject`
   scoped to that bucket only — never a wildcard.
2. Build and push the **ARM64** container to ECR.
3. Create or update the AgentCore runtime with `serverProtocol: A2A` using
   the `agentcore.json` config object. For `oauth` agents it attaches a
   `customJWTAuthorizer` (Cognito discovery URL + allowed client) so the
   runtime requires a bearer token instead of SigV4.
4. Record the result in `.external-agents.json`.
5. Wire the deployed **runtime ARN** (and, for `oauth`, the `authType` +
   Cognito pool/client ids) into the matching `external_agent_configs` entry
   of `global_configuration.json` — the entry named by each agent's
   `agentcore.json` `wireInto` block (`AdCreationAgent_Runtime` on
   `MediaPlanningAgent`, `AdCPSellerAgent_Runtime` on `PublisherAgent`). The
   entry must already exist for wiring to apply; otherwise the deployer logs
   a warning and skips it. Pass `--skip-wire` to leave the config untouched.
6. **Register the same change into the live DynamoDB `AgentConfig` table**
   (`GLOBAL_CONFIG`/`v1`) so the running app sees it — see below.

### Live registration (DynamoDB)

`global_configuration.json` is only the **source** that the ecosystem deploy
copies into DynamoDB. The running "Agents for Advertising" app loads agent
configs from the DynamoDB `AgentConfig` table (single item
`pk=GLOBAL_CONFIG`, `sk=v1`), **not** from the file. Because this deployer
runs independently of `scripts/deploy-ecosystem.sh`, it also patches that live
item directly — a surgical read-modify-write of only the one
`external_agent_configs` entry, so UI-side edits to other agents are never
clobbered.

The table is resolved from, in order: `--dynamodb-table`, then
`--stack-prefix`/`--unique-id` (→ `<prefix>-AgentConfig-<id>`), then the
`$AGENT_CONFIG_TABLE` / `$STACK_PREFIX`+`$UNIQUE_ID` env vars (which
`deploy-ecosystem.sh` exports). If none resolve, the deployer **warns and
skips** — it does not silently claim the agent was registered. Pass
`--skip-dynamodb` to opt out.

> Running agents/UI may serve a cached copy of the global config until their
> cache refreshes, so the change can take a moment to appear live.

## Cross-account / cross-region note

For an **`oauth`** agent (e.g. AdCPSellerAgent), no cross-account IAM trust is needed:
the caller presents a Cognito bearer token and the runtime's JWT authorizer
validates it. Operators store the caller's Cognito credentials in SSM (the
`oauthCredentials.ssmPath` on the `external_agent_configs` entry) and the
caller mints a bearer at invoke time.

For an **`iam`** agent (AdCreationAgent), the caller's AgentCore execution
role needs `bedrock-agentcore:InvokeAgentRuntime` on the runtime ARN. If it
lives in a different account, attach a resource-based policy on the runtime
granting the caller account invoke access — never a wildcard principal.

> **Caller path (OAuth end-to-end):** the backend agent→agent helper in
> `agentcore/deployment/agent/shared/a2a_client_tools.py` invokes `oauth`
> entries over the runtime's **HTTPS data-plane endpoint with a Cognito bearer**
> (mirroring the UI's OAuth invoke), reusing `A2ATokenManager` to mint the
> token from the inbound credentials the operator stores via the agent's
> Inbound Authentication settings. `iam`/`none` entries continue to use the
> SigV4 `invoke_agent_runtime` path. So an `oauth` entry (e.g.
> `AdCPSellerAgent_Runtime` on `PublisherAgent`) is OAuth end-to-end once that
> agent's inbound Cognito credentials are stored (`oauthCredentials.ssmPath`
> populated) — no cross-account IAM trust needed.

