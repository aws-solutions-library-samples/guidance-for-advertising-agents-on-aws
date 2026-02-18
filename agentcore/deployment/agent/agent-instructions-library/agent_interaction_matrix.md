# Agentic Advertising: Agent Interaction Matrix

## Overview

This document maps how the seven agent types in the agentic advertising ecosystem interact with each other, which protocols they use, and what data flows between them.

---

## The Seven Agent Types

| Agent | Role | Primary Protocol(s) |
|-------|------|---------------------|
| **Advertiser Agent** | Principal - defines objectives, approves strategy | A2A |
| **Agency Agent** | Orchestrator - plans, coordinates, executes | A2A, AdCP, MCP |
| **Publisher Agent** | Inventory provider - offers products, delivers campaigns | AdCP, A2A |
| **Signal Agent** | Data provider - audience and contextual signals | AdCP Signals |
| **Identity Agent** | Identity infrastructure - resolution, tokens, reach | MCP, ARTF |
| **Verification Agent** | Quality assurance - brand safety, fraud, viewability | MCP, ARTF, A2A |
| **Measurement Agent** | Outcomes measurement - brand lift, attribution | MCP, A2A |

---

## Interaction Matrix

### Who Talks to Whom

```
                    ADVERTISER  AGENCY  PUBLISHER  SIGNAL  IDENTITY  VERIFICATION  MEASUREMENT
ADVERTISER             —         A2A       —         —        —           —             —
AGENCY                A2A         —       AdCP     AdCP      MCP         MCP           MCP
                                          A2A     Signals                 A2A           A2A
PUBLISHER              —        AdCP       —         —        —           —             —
                                A2A
SIGNAL                 —       AdCP        —         —        —           —             —
                              Signals
IDENTITY               —        MCP        —         —        —           —             —
                               ARTF*
VERIFICATION           —        MCP        —         —        —           —             —
                                A2A
                               ARTF*
MEASUREMENT            —        MCP        —         —        —           —             —
                                A2A

* ARTF = Container deployed in exchange infrastructure, not direct agent communication
```

---

## Detailed Interaction Flows

### 1. Advertiser Agent ↔ Agency Agent

**Protocol:** A2A (Agent-to-Agent)

**Why A2A:** Both parties need to reason. Campaign briefs involve negotiation, clarification, and judgment.

| Direction | Content |
|-----------|---------|
| Advertiser → Agency | Campaign briefs, objectives, guardrails |
| Advertiser → Agency | Creative assets for approval |
| Advertiser → Agency | Approval decisions (media plans, budgets) |
| Agency → Advertiser | Media plan recommendations |
| Agency → Advertiser | Performance reports |
| Agency → Advertiser | Budget reallocation requests |
| Agency → Advertiser | Incident alerts (escalated) |


---

### 2. Agency Agent ↔ Publisher Agent

**Protocol:** AdCP Media Buy Protocol + A2A

**Why Both:**
- **AdCP** for structured operations (get_products, create_media_buy)
- **A2A** for negotiations and relationship management

| Direction | Protocol | Content |
|-----------|----------|---------|
| Agency → Publisher | AdCP | `get_products` (discovery) |
| Publisher → Agency | AdCP | Product catalog response |
| Agency → Publisher | AdCP | `create_media_buy` |
| Publisher → Agency | AdCP | Confirmation, creative deadlines |
| Agency → Publisher | AdCP | `sync_creatives` |
| Agency → Publisher | AdCP | `get_media_buy_delivery` |
| Publisher → Agency | AdCP | Delivery metrics |
| Publisher → Agency | A2A | Premium inventory alerts |
| Agency ↔ Publisher | A2A | Rate negotiations |
| Agency ↔ Publisher | A2A | Delivery issue resolution |


---

### 3. Agency Agent ↔ Signal Agent

**Protocol:** AdCP Signals Activation Protocol

**Why AdCP Signals:** Standardized protocol for signal discovery and activation across the ecosystem.

| Direction | Protocol | Content |
|-----------|----------|---------|
| Agency → Signal | AdCP Signals | `get_signals` (discovery) |
| Signal → Agency | AdCP Signals | Available segments, pricing |
| Agency → Signal | AdCP Signals | `activate_signal` |
| Signal → Agency | AdCP Signals | Activation status, segment IDs |


---

### 4. Agency Agent ↔ Identity Agent

**Protocol:** MCP (Model Context Protocol)

**Why MCP:** Deterministic tool calls. Reach estimation and token resolution are lookup/calculation operations, not negotiations.

| Direction | Protocol | Content |
|-----------|----------|---------|
| Agency → Identity | MCP | Reach estimation request |
| Identity → Agency | MCP | Deduplicated reach projections |
| Agency → Identity | MCP | Token resolution request |
| Identity → Agency | MCP | Privacy-compliant tokens |
| Agency → Identity | MCP | Frequency cap query |
| Identity → Agency | MCP | Frequency status |


---

### 5. Agency Agent ↔ Verification Agent

**Protocol:** MCP + A2A

**Why Both:**
- **MCP** for verification requests (deterministic tool calls)
- **A2A** for incident alerts and investigation discussions

| Direction | Protocol | Content |
|-----------|----------|---------|
| Agency → Verification | MCP | Brand safety verification request |
| Verification → Agency | MCP | Safety scores, classifications |
| Verification → Agency | A2A | Brand safety incident alerts |
| Verification → Agency | A2A | Fraud spike alerts |
| Verification → Agency | A2A | Daily quality digests |
| Agency → Verification | A2A | Investigation requests |
| Verification → Agency | A2A | Investigation reports |


---

### 6. Agency Agent ↔ Measurement Agent

**Protocol:** MCP + A2A

**Why Both:**
- **MCP** for study configuration (deterministic setup)
- **A2A** for results communication and insights discussion

| Direction | Protocol | Content |
|-----------|----------|---------|
| Agency → Measurement | MCP | Study configuration request |
| Measurement → Agency | MCP | Study confirmation, cost |
| Measurement → Agency | A2A | Interim results reports |
| Measurement → Agency | A2A | Final study report |
| Measurement → Agency | A2A | Data quality alerts |
| Agency → Measurement | A2A | Questions, follow-ups |


---

## ARTF Container Interactions

ARTF containers don't communicate via traditional agent protocols. They're deployed **inside** exchange infrastructure and process bid requests in the data path.

### Identity Agent ARTF Container
**Intent:** `audienceSegmentation`

```
Bid Request → [Identity Container] → Enriched with tokens, household data
                    │
                    └── Adds: uid2, rampid, household_id, device_count
```

### Verification Agent ARTF Container
**Intent:** `metadataEnhancement`

```
Bid Request → [Verification Container] → Enriched with safety/fraud signals
                    │
                    └── Adds: brand_safety_score, ivt_risk, viewability_pred
```

### Container Chain Example
```
Raw Bid        Identity         Verification      Enriched Bid
Request   →   Container    →    Container    →   Request
   │             │                  │                │
   │        +identity          +brand_safety        │
   │        +household         +fraud_score         │
   │        +tokens            +viewability         │
                                                    ↓
                                              Sent to DSPs
```

---

## Protocol Decision Tree

```
                    ┌─────────────────────────────────┐
                    │  What type of interaction?       │
                    └─────────────────────────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                    │
              ▼                    ▼                    ▼
     ┌─────────────┐      ┌─────────────┐      ┌─────────────┐
     │ Negotiation │      │ Tool Call   │      │ Real-time   │
     │ or Reasoning│      │ (Lookup/    │      │ Bidstream   │
     │ Required?   │      │  Calculate) │      │ Processing? │
     └─────────────┘      └─────────────┘      └─────────────┘
           │                    │                    │
           ▼                    ▼                    ▼
        ┌─────┐            ┌─────────┐          ┌──────┐
        │ A2A │            │   MCP   │          │ ARTF │
        └─────┘            └─────────┘          └──────┘
                                │
                                │ Is it ad-specific?
                                │
                    ┌───────────┴───────────┐
                    │                       │
                    ▼                       ▼
               ┌─────────┐            ┌──────────┐
               │  AdCP   │            │ MCP      │
               │ (Media  │            │ (General │
               │  Buy or │            │  tools)  │
               │ Signals)│            └──────────┘
               └─────────┘
```

---

## Complete Campaign Flow: All Agent Interactions

```
Step 1: Campaign Brief
┌──────────────┐      A2A       ┌──────────────┐
│  Advertiser  │ ─────────────> │    Agency    │
│    Agent     │                │    Agent     │
└──────────────┘                └──────────────┘

Step 2: Discovery
┌──────────────┐    AdCP MB     ┌──────────────┐
│    Agency    │ ─────────────> │  Publisher   │
│    Agent     │ <───────────── │    Agent     │
└──────────────┘                └──────────────┘
       │
       │ AdCP Signals
       ▼
┌──────────────┐
│    Signal    │
│    Agent     │
└──────────────┘

Step 3: Identity Resolution
┌──────────────┐      MCP       ┌──────────────┐
│    Agency    │ ─────────────> │   Identity   │
│    Agent     │ <───────────── │    Agent     │
└──────────────┘                └──────────────┘

Step 4: Brand Safety Verification
┌──────────────┐      MCP       ┌──────────────┐
│    Agency    │ ─────────────> │ Verification │
│    Agent     │ <───────────── │    Agent     │
└──────────────┘                └──────────────┘

Step 5: Human Approval
┌──────────────┐      A2A       ┌──────────────┐
│    Agency    │ ─────────────> │  Advertiser  │
│    Agent     │ <───────────── │    Agent     │
└──────────────┘   (approval)   └──────────────┘

Step 6: Execution
┌──────────────┐    AdCP MB     ┌──────────────┐
│    Agency    │ ─────────────> │  Publisher   │
│    Agent     │                │    Agent     │
└──────────────┘                └──────────────┘
                                       │
                    ┌──────────────────┴──────────────────┐
                    │            EXCHANGE                  │
                    │  ┌─────────┐      ┌─────────┐       │
                    │  │Identity │ ───> │ Verify  │       │
                    │  │Container│      │Container│       │
                    │  └─────────┘      └─────────┘       │
                    │        (ARTF Containers)            │
                    └─────────────────────────────────────┘

Step 7: Measurement Setup
┌──────────────┐      MCP       ┌──────────────┐
│    Agency    │ ─────────────> │ Measurement  │
│    Agent     │ <───────────── │    Agent     │
└──────────────┘                └──────────────┘

Step 8: Optimization
┌──────────────┐      A2A       ┌──────────────┐
│    Agency    │ <────────────> │  Publisher   │
│    Agent     │                │    Agent     │
└──────────────┘                └──────────────┘
       ▲
       │ A2A
       ▼
┌──────────────┐                ┌──────────────┐
│ Measurement  │                │ Verification │
│    Agent     │                │    Agent     │
└──────────────┘                └──────────────┘
       │                               │
       └───────────────┬───────────────┘
                       │ A2A
                       ▼
               ┌──────────────┐
               │    Agency    │
               │    Agent     │
               └──────────────┘
```

---

## Summary Table

| From | To | Protocol | Use Case |
|------|-----|----------|----------|
| Advertiser | Agency | A2A | Briefs, approvals, reports |
| Agency | Publisher | AdCP MB | Products, media buys, delivery |
| Agency | Publisher | A2A | Negotiations, alerts |
| Agency | Signal | AdCP Signals | Discovery, activation |
| Agency | Identity | MCP | Reach, tokens, frequency |
| Agency | Verification | MCP | Pre-campaign verification |
| Agency | Verification | A2A | Alerts, investigations |
| Agency | Measurement | MCP | Study setup |
| Agency | Measurement | A2A | Reports, insights |
| Identity | Exchange | ARTF | Bid enrichment |
| Verification | Exchange | ARTF | Bid enrichment |

---

## Specialist Collaboration Tools

All agents in the ecosystem can collaborate using the `invoke_specialist` tool. This enables direct agent-to-agent communication without requiring knowledge base queries.

### Tool Usage

```python
# Invoke a specialist agent
invoke_specialist(
    agent_prompt="Your request to the specialist",
    agent_name="SpecialistAgentName"
)
```

### Available Specialists

| Agent Name | Capabilities |
|------------|--------------|
| AgencyAgent | Campaign orchestration, media planning, optimization |
| AdvertiserAgent | Campaign briefs, approvals, brand stewardship |
| PublisherAgent | Inventory discovery, media buy operations, delivery |
| SignalAgent | Audience signals, contextual targeting, activation |
| IdentityAgent | Reach estimation, token resolution, frequency management |
| VerificationAgent | Brand safety, fraud detection, viewability |
| MeasurementAgent | Brand lift studies, attribution, foot traffic |

### Collaboration Guidelines

1. **Address specialists directly** using @ syntax: `@AgencyAgent, please...`
2. **Invoke in parallel** when work doesn't depend on each other
3. **Synthesize insights** from multiple specialists into unified recommendations
4. **Focus on substance** - avoid meta-commentary about coordination

### AdCP MCP Gateway

The AdCP MCP Gateway provides protocol-compliant tools for advertising operations:

- **get_products**: Discover publisher inventory
- **get_signals**: Discover audience segments
- **activate_signal**: Activate segments on DSPs
- **create_media_buy**: Create media buys
- **get_media_buy_delivery**: Get delivery metrics
- **verify_brand_safety**: Brand safety verification
- **resolve_audience_reach**: Cross-device reach estimation
- **configure_brand_lift_study**: Configure measurement studies

---

## Files Reference

All agent instruction files:
- `AgencyAgent.txt`
- `AdvertiserAgent.txt`
- `PublisherAgent.txt`
- `SignalAgent.txt`
- `IdentityAgent.txt`
- `VerificationAgent.txt`
- `MeasurementAgent.txt`

Supporting materials:
- `synthetic_data/` - Mock data for all agents
- `synthetic_data/mcp_mocks/adcp_mcp_server.py` - AdCP MCP Server implementation