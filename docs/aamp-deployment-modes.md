# IAB AAMP Agent Integration — Architecture & Deployment

This document describes how the IAB Tech Lab AAMP buyer and seller agents are
integrated into the Agents for Advertising guidance solution on Amazon Bedrock
AgentCore.

> **What changed:** The AAMP buyer and seller now run as **Strands A2A** agents
> (not CrewAI/HTTP), are protected by **OAuth (custom JWT)**, and the
> orchestrator (AgencyAgent) calls **only the buyer**. The buyer reaches the
> seller internally. See [Migration notes](#migration-notes) at the end.

---

## Architecture

```
+---------------------------------------------------------------+
|                    Angular UI (CloudFront)                    |
+------------------------------+--------------------------------+
                               | invoke_agent_runtime (SSE)
                               | agent_name = "AgencyAgent"
+------------------------------v--------------------------------+
|            GUIDANCE AGENT RUNTIME (AdFabricAgent)             |
|                    Strands + Bedrock                          |
|                                                               |
|  AgencyAgent (orchestrator)                                   |
|    +-- invoke_aampbuyeragent  (A2A, OAuth bearer)             |
|          -> message/stream (JSON-RPC 2.0 over SSE)            |
|          (no direct seller tool -- see Routing below)         |
+------------------------------+--------------------------------+
                               | A2A message/stream (OAuth)
+------------------------------v--------------------------------+
|                    AAMP BUYER RUNTIME                         |
|              Strands A2AServer (OAuth / custom JWT)           |
|                                                               |
|  Coordinator agent: parses brief -> allocate_budget           |
|    -> search_inventory (per funded channel)                   |
|         +-- seller_client -> AAMP SELLER RUNTIME (A2A, OAuth)  |
|  Returns: final media-plan JSON (budget + real inventory)     |
+------------------------------+--------------------------------+
                               | A2A message/stream (OAuth)
+------------------------------v--------------------------------+
|                    AAMP SELLER RUNTIME                        |
|              Strands A2AServer (OAuth / custom JWT)           |
|                                                               |
|  Inventory manager: real catalog + tiered pricing            |
|    -> MCP read tools (inventory) + create_deal (PG/PD/PA)     |
+---------------------------------------------------------------+
```

---

## Protocol & Authentication

| Aspect | Detail |
|--------|--------|
| Protocol | **A2A** (Strands `A2AServer`). Requests are JSON-RPC 2.0 `message/stream` consumed as an SSE stream, so a long (~1–2 min) plan streams incrementally instead of tripping the single-response deadline. |
| Transport | AgentCore data-plane HTTPS endpoint (`invoke_agent_runtime` for SigV4/IAM targets; direct HTTPS POST with a bearer token for OAuth targets). |
| Auth | **OAuth (custom JWT)**. The buyer and seller runtimes are fronted by a Cognito-backed JWT authorizer. The caller obtains a bearer token via `A2ATokenManager` (client-credentials or username/password, per the stored credential document) and sends it on every invoke. SigV4 alone does not satisfy a JWT authorizer. |
| Buyer → seller credentials | The buyer reads the seller's inbound OAuth credential from SSM. `scripts/deploy_aamp_agents.sh` grants the buyer runtime role `ssm:GetParameter` + `kms:Decrypt` on that parameter after the buyer deploys. |
| Cold-start resilience | Both hops (AgencyAgent → buyer, buyer → seller) retry on `424`/`503` (runtime still initializing) with backoff, so the first call after an idle period is transparent instead of surfacing a failure. |

---

## Routing (buyer-only orchestration)

The AgencyAgent orchestrates the AAMP marketplace through the **buyer only**:

- **AgencyAgent → AAMPBuyerAgent** is the single external tool for AAMP. The
  buyer plans the campaign *and* returns real inventory and pricing, which it
  sources from the seller internally (`search_inventory` → `seller_client`).
- **AAMPSellerAgent is not wired as an AgencyAgent tool.** Its
  `external_agent_configs` entry is present but `enabled: false`, so the tool
  builder skips it. The seller runtime is still deployed — the buyer calls it
  directly — but the orchestrator never invokes it, which avoids routing
  ambiguity between the two AAMP agents.

> To re-enable direct AgencyAgent → seller calls, set the seller entry's
> `enabled` back to `true` in `global_configuration.template.json` and redeploy.

The per-external-agent invoke tools are built in
`agentcore/deployment/agent/shared/a2a_client_tools.py`. Each tool is created by
the `_make_runtime_tool` factory so it closes over **its own** entry (ARN, auth,
name); this prevents a late-binding bug where every tool would otherwise invoke
the last-wired runtime.

---

## Progressive status & visualizations

**Live progress.** During the long buyer call, the buyer's A2A `status-update`
narration is relayed to the UI as a small, capped set of `⏳` milestone lines
(`agent_status` events rendered as a transient "thinking" bubble). Relaying is
capped (`_MAX_PROGRESS_LINES`) and skips long paragraph-like lines so the whole
answer is not echoed as progress and then again as the final turn.

**Final plan as cards.** The buyer returns a media-plan JSON object. The relay
deterministically converts it into the UI's `visualization-data` blocks so it
renders as cards rather than raw JSON:

- `allocations-visualization` — per-channel budget split
- `adcp_get_products-visualization` — recommended inventory (real product IDs,
  publishers, CPMs, impressions)

Free-form seller/agent prose is additionally analyzed client-side against the
agent's visualization templates (DynamoDB `VIZ_MAP#{agent}` / `VIZ_TEMPLATE#{agent}`).

**AgencyAgent visualization map** (`agent-visualizations-library/agent-visualization-maps/AgencyAgent.json`)
advertises these templates: `adcp_get_products`, `allocations`,
`adcp_create_media_buy`, `timeline`, `adcp_get_media_buy_delivery`.

**Response formatting.** The buyer, seller, and AgencyAgent prompts require:
no emojis, a character cap, and any tabular/CSV data rendered as a Markdown
table (never raw CSV).

---

## Agent Configuration

### AAMPBuyerAgent (wired)

| Field | Value |
|-------|-------|
| Runtime | Own AgentCore runtime, Strands A2A, OAuth (custom JWT) |
| `isA2A` / `authType` | `true` / `oauth` |
| Wired to AgencyAgent | **Yes** (single AAMP tool) |
| Returns | Media-plan JSON: per-channel budget allocation + recommended inventory/pricing (sourced from the seller) + audience coverage/gaps + approval flag |

### AAMPSellerAgent (deployed, not wired to AgencyAgent)

| Field | Value |
|-------|-------|
| Runtime | Own AgentCore runtime, Strands A2A, OAuth (custom JWT) |
| `isA2A` / `authType` | `true` / `oauth` |
| Wired to AgencyAgent | **No** (`enabled: false`) — reached only by the buyer |
| Handles | Inventory catalog, tiered pricing, deal creation (PG/PD/PA) |

---

## Configuration Resolution

`global_configuration.template.json` contains
`${AAMP_SELLER_HTTP_RUNTIME_ARN}` and `${AAMP_BUYER_HTTP_RUNTIME_ARN}`
placeholders, resolved from the `.aamp-runtime-*.json` file produced during AAMP
runtime deployment (Phase 9 of `deploy-ecosystem.sh`, optional/opt-in).

`scripts/wire_aamp_agents.py` patches the resolved ARNs and OAuth credentials
into each consumer's `external_agent_configs` (deep-copying the template entry,
so the seller's `enabled: false` is preserved).

---

## Deployment

### Full ecosystem (recommended)

```bash
bash scripts/deploy-ecosystem.sh \
  --stack-prefix ${PREFIX} --unique-id ${ID} --region ${REGION} --profile ${PROFILE} \
  --demo-email you@example.com --deploy-aamp --skip-confirmations
```

Relevant phases:

| Phase | Purpose |
|-------|---------|
| 7 | Upload agent instructions, visualization maps/templates, and the resolved global config to DynamoDB |
| 8 | Deploy the guidance AgentCore agent (AgencyAgent / AdFabricAgent runtime — includes `a2a_client_tools.py`) |
| 9 | Deploy the AAMP buyer & seller runtimes and wire them (buyer-only) |
| 10 | Build & deploy the Angular UI |
| 11 | Warm up runtimes (reduces cold-start latency) |

### Re-deploy just the AAMP-related changes

```bash
bash scripts/deploy-ecosystem.sh --resume-at 9 --skip-confirmations \
  --deploy-aamp \
  --stack-prefix ${PREFIX} --unique-id ${ID} --region ${REGION} --profile ${PROFILE}
```

### Where the AAMP agent source comes from

The Strands A2A buyer and seller ship **in this repo** under `external-agents/`
(`a2a_main.py`, `requirements-a2a.txt`, and `deploy.sh --mode a2a`). Phase 9 uses
that directory automatically — `--local-aamp` is not required.

The upstream IAB repos do **not** contain the A2A adapter, so cloning them while
`AAMP_PROTOCOL=a2a` (the default) fails with:

```
ERROR: Invalid mode 'a2a'. Must be one of: all mcp http crew chat
```

| To do this | Use |
|---|---|
| Deploy the in-repo Strands A2A agents (default) | nothing — Phase 9 finds `./external-agents` |
| Point at AAMP repos elsewhere on disk | `--local-aamp /abs/or/relative/path` (relative paths are resolved automatically) |
| Clone the upstream IAB repos for the legacy CrewAI HTTP path | `AAMP_LOCAL_DEFAULT=0 AAMP_PROTOCOL=http … --aamp-branch main` |

### Runtime name length

AAMP runtime names are capped at 26 characters by `_aamp_runtime_name()` in
`scripts/deploy_aamp_agents.sh`. The AgentCore toolkit derives CloudWatch Logs
delivery names from the runtime's memory id, and the longest one it builds —
`<runtime>_mem-<10-char id>-traces-destination` — must fit the 60-character
limit on `PutDeliveryDestination`. Over the limit, the memory is still created
and active but traces delivery is skipped with a `ValidationException`. The
helper shortens descriptive words first (`buyer` → `buy`, `seller` → `sell`,
drop `_aamp`) and trims the prefix/id only as a last resort, always keeping the
role marker so buyer and seller cannot collapse to the same name.

---

## IAB AAMP SDK Repos

| Repo | What's Added |
|------|-------------|
| [seller-agent](https://github.com/IABTechLab/seller-agent) | `interfaces/agentcore/a2a_main.py` — Strands A2A seller runtime (inventory read tools + `create_deal`) |
| [buyer-agent](https://github.com/IABTechLab/buyer-agent) | `interfaces/agentcore/a2a_main.py` — Strands A2A buyer runtime (`allocate_budget`, `search_inventory` → `seller_client`) |

All new code lives under `interfaces/agentcore/` (and `patches/`) so the
community-maintained agent/crew/flow code is not modified.

---

## Migration notes

- **CrewAI/HTTP → Strands A2A.** The buyer and seller were re-implemented as
  Strands `A2AServer` runtimes. The invoke path uses JSON-RPC `message/stream`
  (SSE), which fixed the `424` on long buyer responses.
- **IAM → OAuth (custom JWT).** Both AAMP runtimes are OAuth-protected; callers
  use bearer tokens via `A2ATokenManager`.
- **Both-agents orchestration → buyer-only.** The AgencyAgent now calls only the
  buyer; the seller is reached internally by the buyer. The seller's AgencyAgent
  tool entry is disabled rather than removed, so it can be re-enabled without
  re-adding the configuration.
