# IAB AAMP Agent Integration — Deployment Modes

This document describes how the IAB AAMP buyer and seller agents are integrated into the Agents for Advertising guidance solution via Amazon Bedrock AgentCore.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Angular UI (CloudFront)                   │
│                    AAMP Marketplace Tab                      │
└──────────────────────────┬──────────────────────────────────┘
                           │ WebSocket
┌──────────────────────────▼──────────────────────────────────┐
│              GUIDANCE AGENT (Strands + Bedrock)              │
│                                                              │
│  AgencyAgent (orchestrator)                                  │
│    ├─ invoke_specialist(AAMPBuyerAgent)                  │
│    │    → invoke_agent_runtime (buyer ARN)                   │
│    └─ invoke_specialist(AAMPSellerAgent)                 │
│         → invoke_agent_runtime (seller ARN)                  │
└────────┬─────────────────────────────┬──────────────────────┘
         │                             │
┌────────▼──────────────┐    ┌─────────▼──────────────────────┐
│  BUYER RUNTIME        │    │    SELLER RUNTIME               │
│  (AgentCore HTTP)     │    │    (AgentCore HTTP)             │
│                       │    │                                 │
│  DealBookingFlow      │    │  CrewAI PublisherCrew           │
│  → PortfolioCrew      │    │  → Bedrock Converse LLM        │
│  → Budget allocation  │    │  → MCP tools (read)            │
│                       │    │  → CreateDealTool (write)       │
│  Returns: JSON plan   │    │  → Real inventory data          │
└───────────────────────┘    └─────────────────────────────────┘
```

---

## Agent Configuration

### AAMPSellerAgent

| Field | Value |
|-------|-------|
| `runtime_arn` | Seller HTTP runtime ARN |
| `agent_family` | AAMP Marketplace |
| `agent_tools` | `["lookup_events"]` |
| Description | Publisher sell-side agent with real inventory from Meridian Media Group |

The seller runtime handles: inventory catalog, pricing, rate cards, deal creation (PG/PD/PA).

### AAMPBuyerAgent

| Field | Value |
|-------|-------|
| `runtime_arn` | Buyer HTTP runtime ARN |
| `agent_family` | AAMP Marketplace |
| `agent_tools` | `["lookup_events"]` |
| Description | Campaign planning with budget allocation across channels |

The buyer runtime handles: campaign planning via DealBookingFlow with PortfolioCrew.

### AgencyAgent (Orchestrator)

The Agency Agent has both AAMP agents in its `tool_agent_names`. When it calls `invoke_specialist`, the handler detects the `runtime_arn` and calls `invoke_agent_runtime` directly — no proxy Strands agent needed.

Few-shot examples in the Agency Agent instructions ensure correct parameter names:
```
invoke_specialist(agent_name="AAMPSellerAgent", agent_prompt="Show available CTV inventory")
```

---

## AAMP Marketplace Tab — 5-Step Flow

| Step | Agent | What Happens |
|------|-------|-------------|
| 1. Plan Campaign | AgencyAgent → Buyer | Budget allocation across CTV, digital video, mobile, performance |
| 2. Discover Inventory | AgencyAgent → Seller | Real product catalog with IDs, CPMs, deal types |
| 3. Get Pricing | AgencyAgent → Seller | Tiered pricing with volume discounts |
| 4. Negotiate Deal | AgencyAgent → Seller | Price validation against floor, deal terms |
| 5. Book Deals | AgencyAgent → Seller | Deal IDs generated for DSP activation |

All steps route through AgencyAgent. Both buyer and seller agent bubbles are visible in the UI conversation.

---

## Configuration Resolution

The `global_configuration.template.json` contains `${AAMP_SELLER_HTTP_RUNTIME_ARN}` and `${AAMP_BUYER_HTTP_RUNTIME_ARN}` placeholders. These are resolved by `scripts/resolve_config.py` from the `.aamp-runtime-*.json` file produced during AAMP runtime deployment (Phase 12 of `deploy-ecosystem.sh`).

```bash
# Resolve template → config
python scripts/resolve_config.py

# Upload to DynamoDB
python scripts/upload_agent_configs_to_dynamodb.py \
  --table-name ${STACK_PREFIX}-AgentConfig-${UNIQUE_ID} \
  --region ${REGION} --profile ${PROFILE}

# Upload tab configs
python scripts/upload_tab_configs_to_dynamodb.py \
  --table-name ${STACK_PREFIX}-AgentConfig-${UNIQUE_ID} \
  --region ${REGION} --profile ${PROFILE} --force
```

---

## Deployment

### IAB Runtimes (Phase 12)

```bash
# Seller
cd iab-aamp/seller-agent
bash infra/aws/agentcore/deploy.sh --mode http --name ${PREFIX}_aamp_seller_${ID}_http --profile ${PROFILE}

# Buyer
cd iab-aamp/buyer-agent
bash infra/aws/agentcore/deploy.sh --mode http --name ${PREFIX}_aamp_buyer_${ID}_http --profile ${PROFILE}
```

### Guidance Agent

```bash
bash scripts/deploy-ecosystem.sh --resume-at 9 --skip-confirmations \
  --profile ${PROFILE} --region ${REGION} --unique-id ${ID} --stack-prefix ${PREFIX}
```

---

## IAB AAMP SDK Repos

| Repo | Branch | What's Added |
|------|--------|-------------|
| [seller-agent](https://github.com/IABTechLab/seller-agent) | `feat/agentcore-adapter` | `interfaces/agentcore/` + `patches/` — HTTP runtime with CrewAI + Bedrock |
| [buyer-agent](https://github.com/IABTechLab/buyer-agent) | `feat/agentcore-adapter` | `interfaces/agentcore/` + `patches/` — HTTP runtime with DealBookingFlow + Bedrock |

Both PRs follow the same principle: all new code in `interfaces/agentcore/` and `patches/`. No modifications to community-maintained agent, crew, or flow code.
