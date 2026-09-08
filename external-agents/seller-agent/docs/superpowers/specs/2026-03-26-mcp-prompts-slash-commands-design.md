# MCP Prompts (Slash Commands) for Seller Agent

**Date:** 2026-03-26
**Status:** Approved
**Scope:** `src/ad_seller/interfaces/mcp_server.py` + 3 new composite tools

## Problem

Business users (ad ops, sales) connecting to the seller agent via Claude Desktop, Claude web, or ChatGPT have no discoverable entry points. The MCP server exposes 33 tools but zero prompts. Users don't know what the agent can do or how to start.

Setup relies on Claude proactively calling `get_setup_status` based on system instructions — which is unreliable. There's no guaranteed first-run experience.

## Solution

Add **9 MCP prompts** that surface as `/` commands in Claude Desktop/web, plus **3 new composite tools** that fill data gaps for the prompts that don't have adequate tool coverage today.

### Platform Behavior

| Platform | `/` commands visible? | How user triggers |
|----------|----------------------|-------------------|
| Claude Desktop | Yes — `/` menu | Click or type `/setup` |
| Claude Web | Yes — same integration | Click or type `/setup` |
| ChatGPT | No — prompts not surfaced | Natural language; tools still work |
| Cursor / IDEs | Varies | Depends on IDE MCP support |

The prompts are the UX layer; the tools underneath make it work everywhere.

## The 9 Prompts

### 1. `/setup` — First-Time Guided Wizard

**When:** Day 1, after developer deploys and business user connects.

**Prompt message:**
> Check setup status and walk me through configuring everything that's incomplete. Go step by step: publisher identity, ad server, SSPs, media kit, pricing, approval gates, and buyer agent access. Ask me one question at a time.

**Tools called by Claude:** `get_setup_status`, `set_publisher_identity`, `update_rate_card`, `create_package`, `set_approval_gates`, `register_buyer_agent`

### 2. `/status` — Configuration and Health Overview

**When:** "Is everything working?" — periodic check.

**Prompt message:**
> Show me a complete status overview: configuration state, system health, ad server connection, SSP connectors, and any issues that need attention.

**Tools called:** `get_setup_status`, `health_check`, `get_config`

### 3. `/inventory` — What Do I Have to Sell?

**When:** Daily check on available inventory.

**Prompt message:**
> Show me my current inventory: products, media kit packages, and sync status. Highlight anything that needs attention.

**Tools called:** `list_products`, `list_packages`, `list_inventory`, `get_sync_status`

### 4. `/deals` — Full Deal Status Report

**When:** Daily operations — everything happening across all deals.

**Prompt message:**
> Give me a full status report on all deal activity: active deals, deals in negotiation, recently completed deals, and any deals with issues. Include SSP distribution status.

**Tools called:** `export_deals`, `list_orders`, `list_pending_approvals`, `get_inbound_queue`

### 5. `/queue` — Inbound Items Needing Action

**When:** Ad ops checking what's new and needs publisher response.

**Prompt message:**
> Show me everything in the inbound queue that needs my action: pending deal requests, approvals waiting for my decision, and proposals I need to review. Most urgent first.

**Tools called:** `get_inbound_queue` (new composite tool)

### 6. `/new-deal` — Guided Deal Creation

**When:** Publisher wants to proactively create a deal.

**Prompt message:**
> Help me create a new deal. Walk me through it step by step: which inventory, deal type (PG/PD/PA), pricing, targeting, and which buyers or SSPs to distribute to.

**Tools called:** `list_packages`, `get_pricing`, `create_deal_from_template`, `distribute_deal_via_ssp`, `push_deal_to_buyers`

### 7. `/configure` — Event Bus Flows, Approval Gates, Guard Conditions

**When:** Publisher wants to customize automation rules.

**Prompt message:**
> Show me all configurable automation rules: event bus flows, approval gates, and guard conditions. Tell me what each one does and let me add, modify, or remove them.

**Tools called:** `list_configurable_flows` (new composite tool), `set_approval_gates`

### 8. `/buyers` — Buyer Agent Activity and Inbound Interest

**When:** Publisher wants to see who's been looking at their inventory.

**Prompt message:**
> Show me which buyer agents have been accessing my media kit and inventory recently. For each buyer, show what they looked at, whether they initiated any deals, and their current trust level. I want to know who to follow up with.

**Tools called:** `get_buyer_activity` (new composite tool), `list_buyer_agents`

### 9. `/help` — What Can This Agent Do?

**When:** User is unsure what's available.

**Prompt message:**
> List all the things I can do with this seller agent, organized by category. Include the slash commands available and a brief description of each.

**Tools called:** None — Claude responds from its knowledge of the available prompts and tools.

## 3 New Composite Tools

### `get_inbound_queue(limit: int = 50) -> str`

Aggregates everything waiting for publisher action into one sorted list.

**Data sources:**
- Pending approvals from `ApprovalGate.list_pending()` (called directly, not via HTTP)
- Proposal events from event bus: `proposal.received` and `proposal.evaluated` events that lack a subsequent `proposal.accepted`/`proposal.rejected`/`proposal.countered` event for the same `proposal_id`

> **Note:** `DealRequestFlow` does not persist to storage. Inbound deal requests are captured via event bus `proposal.received` events emitted by the proposal handling flow. There is no separate "deal request store" to query.

**Error handling:** If one data source fails (e.g., event bus query errors), return partial results with a `warnings` field indicating which source was unavailable.

**Returns:** Unified list sorted by timestamp (most urgent first), each item tagged with:
- `type`: `approval` | `proposal`
- `id`: the item's ID
- `summary`: human-readable one-liner
- `timestamp`: when it arrived
- `from`: buyer agent identity if known
- `urgency`: `high` (approaching timeout) | `normal`

### `get_buyer_activity(days: int = 7, limit: int = 50) -> str`

Shows buyer agent engagement with the publisher's inventory.

**Data sources:**
- Event bus events with non-empty `session_id` from buyer-facing flows (proposal handling, negotiation, quote requests)
- Session records with buyer agent identity (from session storage)

> **Note:** API key usage logging does not exist today. v1 derives buyer activity from event bus + session records only. API key access tracking is a future enhancement that would require adding logging middleware to `auth/dependencies.py`.

**Error handling:** If event bus or session queries fail, return partial results with a `warnings` field.

**Returns:** Buyer agents grouped by identity, each with:
- `agent_id`, `agent_url`, `trust_level`
- `last_seen`: most recent activity timestamp
- `activity_summary`: list of actions (quote requests, deal initiations, negotiation rounds)
- `deals_initiated`: count and status of deals started by this buyer

### `list_configurable_flows() -> str`

Introspects current automation configuration.

**Data sources:**
- Approval gate settings from `settings.approval_required_flows`, `settings.approval_gate_enabled`, `settings.approval_timeout_hours`
- Guard conditions from `OrderStateMachine._DEFAULT_TRANSITIONS` (importable list of `TransitionRule` with `from_status`, `to_status`, `guard`, `description`)
- Event bus active subscriptions from in-memory `_subscribers` dict (runtime only — reflects current process state, not a persistent registry)

> **Note:** Event bus subscriptions are runtime-only. On a fresh server start with no flows triggered, the event_flows section will be empty. This is expected — it shows what's actively listening, not what *could* listen.

**Returns:** Structured config with three sections:
- `approval_gates`: gate enabled/disabled, timeout, which flows require approval
- `guard_conditions`: list of transition rules with from/to states, guard description, whether active
- `event_flows`: list of event type subscriptions with subscriber count (runtime only)

Each section includes `configurable: true/false` and hints for what can be changed.

## Prompt Decorator Pattern

FastMCP `@mcp.prompt()` returns `list[Message]`. Concrete example:

```python
from mcp.server.fastmcp.prompts.base import Message

@mcp.prompt(name="setup", description="First-time guided setup wizard")
async def setup_prompt() -> list[Message]:
    return [Message(
        role="user",
        content="Check setup status and walk me through configuring everything "
                "that's incomplete. Go step by step: publisher identity, ad server, "
                "SSPs, media kit, pricing, approval gates, and buyer agent access. "
                "Ask me one question at a time.",
    )]
```

## Implementation Notes

- All prompts go in `mcp_server.py` in a new `# Prompts (Slash Commands)` section after the existing tools
- New tools go in new sections: `# Inbound Queue` for `get_inbound_queue`, `# Buyer Activity` for `get_buyer_activity`, `# Configuration Introspection` for `list_configurable_flows`
- `get_buyer_activity` queries event bus filtered by buyer sessions + session storage — no new storage tables needed
- `get_inbound_queue` calls `ApprovalGate.list_pending()` directly (not via HTTP) + event bus queries for proposal events
- `list_configurable_flows` reads from settings + `OrderStateMachine._DEFAULT_TRANSITIONS` + runtime event bus subscriber dict
- Composite tools must handle partial failures gracefully — return available data with `warnings` list
- No new dependencies required
- No database migrations required

## Required Imports for New Tools

```python
from ..events.approval import ApprovalGate
from ..events.bus import EventBus, StorageEventBus
from ..events.models import EventType
from ..models.order_state_machine import OrderStateMachine, TransitionRule
```

## Files Changed

| File | Change |
|------|--------|
| `src/ad_seller/interfaces/mcp_server.py` | Add 9 `@mcp.prompt()` decorators + 3 new `@mcp.tool()` functions |
| `docs/guides/claude-desktop-setup.md` | Update to reference `/setup` as the entry point |
| `docs/guides/chatgpt-setup.md` | Note that prompts aren't surfaced; describe natural-language equivalents |

## Testing

- Unit test each new tool with mocked storage/events
- Verify prompts return correct `list[Message]` format
- Integration test: connect to Claude Desktop, confirm all 9 prompts appear in `/` menu
- Verify ChatGPT falls back gracefully to tool-based interaction
