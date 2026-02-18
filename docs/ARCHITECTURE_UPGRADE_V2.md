# Architecture Upgrade: v1-baseline → v2

This document describes the architectural changes between the `main-v1-baseline` branch (the previous GitHub main) and the current `v2-candidate` branch.

## Summary

The v2 release introduces DynamoDB-backed agent configuration management, a Nova Sonic voice interface, a full CRUD agent management UI, modular frontend refactoring, and streamlined agent instructions. The net change is +19,434 / -6,882 lines across 76 files.

---

## 1. DynamoDB-Backed Agent Configuration

### What changed
The agent configuration system moved from S3-only storage to a DynamoDB-first architecture with S3 fallback.

### New components
- `agentcore/deployment/agent/shared/dynamodb_config_loader.py` (655 lines) — Module-level cached loader for agent instructions, cards, visualization maps, and global config from DynamoDB.
- `bedrock-adtech-demo/src/app/services/agent-dynamodb.service.ts` (854 lines) — Angular service providing full CRUD operations against the AgentConfig DynamoDB table via AWS SDK.
- `scripts/upload_agent_configs_to_dynamodb.py` — Bulk uploader for seeding agent configs from local files into DynamoDB.
- `scripts/upload_configs_to_dynamodb.py` — Uploads tab configurations and global config to DynamoDB.

### Infrastructure
- `cloudformation/infrastructure-services.yml` — New `AgentConfigTable` DynamoDB table with `pk/sk` key schema and `ConfigTypeIndex` GSI for querying by config type.
- `cloudformation/infrastructure-core.yml` — Expanded IAM permissions for AgentCore Gateway operations (`InvokeGateway`, `GetGateway`, `ListGateways`, `ListGatewayTargets`, etc.) and removed deprecated AppSync Events permissions.

### DynamoDB Schema
| pk | sk | config_type | content |
|---|---|---|---|
| `INSTRUCTION#AgentName` | `v1` | `instruction` | Agent prompt text |
| `CARD#AgentName` | `v1` | `card` | Agent card JSON |
| `VIZ_MAP#AgentName` | `v1` | `visualization` | Visualization map JSON |
| `VIZ_TEMPLATE#AgentName` | `{template_id}` | `visualization` | Template JSON |
| `GLOBAL_CONFIG` | `v1` | `global_config` | Global configuration JSON |

### Handler migration
`agentcore/deployment/agent/handler.py` now imports from `dynamodb_config_loader` instead of `visualization_loader` for agent config resolution. The agent card injection (`{{AGENT_NAME_LIST}}`) loads from DynamoDB first, falling back to S3 and then local files.

---

## 2. Nova Sonic Voice Interface

### What changed
A complete real-time voice interface was added using Amazon Nova Sonic for speech-to-speech agent interaction.

### New components
- `bedrock-adtech-demo/src/app/services/nova-sonic.service.ts` (874 lines) — Full bidirectional streaming service using the Bedrock `ConverseStream` API. Handles audio capture, tool-use routing, turn management, and session lifecycle.

### Voice routing architecture
- The chat interface now supports a voice mode where user speech is transcribed, routed to the appropriate agent via a `route_to_agent` tool-use pattern, and the agent's response is spoken back.
- Tool choice is set to `any` (forced tool use) so Nova Sonic always routes to an agent rather than answering directly.
- Voice routing is deferred: tool-use events stash the routing info (`pendingVoiceRouting`) and the actual agent invocation happens after the model finishes its spoken acknowledgement (`turn-complete` event).

### Chat interface changes
- `chat-interface.component.ts` gained ~125 lines for voice event handling, pending routing state, and fallback query tracking.
- New event types: `turn-complete`, `toolUseId` tracking on tool-use events.

---

## 3. Agent Management UI (CRUD)

### What changed
A full agent management interface was added for creating, editing, and deleting agent configurations through the frontend.

### New components
- `agent-management-modal/` — Modal component (677 lines TS, 401 lines HTML, 1481 lines SCSS) for listing agents, selecting for edit, and triggering create/delete operations. Loads agents from DynamoDB global config.
- `agent-editor-panel/` — Editor panel (1738 lines TS in v1, refactored to ~500 lines + helpers in v2) for editing agent properties: display name, team, description, tool agents, model config, instructions, color, MCP servers, visualization mappings.

### v2 refactoring of agent-editor-panel
The monolithic component was decomposed into:
- `agent-editor-panel.constants.ts` — Preset colors, available templates, tool options, MCP server presets.
- `agent-editor-panel.sample-data.ts` — Sample data by visualization template type.
- `agent-editor-mcp.helpers.ts` — MCP server ID generation, transport helpers, tool listing.
- `agent-editor-ai.helpers.ts` — AI-powered instruction and visualization mapping generation.
- Component switched to `ChangeDetectionStrategy.OnPush` for performance.

### Agent configuration model
`AgentConfiguration` interface gained:
- `knowledge_base?: string` — Maps to `knowledge_bases` in global config for RAG.
- `mcp_servers?: MCPServerConfig[]` — MCP server configurations per agent.
- `runtime_arn?: string` — Optional per-agent runtime ARN override.

### DynamoDB sync
When saving an agent, the service now syncs `color` to `configured_colors` and `knowledge_base` to `knowledge_bases` in the global config, ensuring consistency across the system.

---

## 4. Visualization Analyzer Service

### New component
- `bedrock-adtech-demo/src/app/services/visualization-analyzer.service.ts` (245+ lines) — Analyzes agent responses to detect visualization-worthy data and triggers appropriate visualization rendering. Improved in v2 with better formatting and edge case handling.

---

## 5. Agent Instructions Optimization

### What changed
Agent instruction files were significantly trimmed to reduce prompt token usage. Verbose example data, sample YAML blocks, and redundant content were removed from 15 agent instruction files, resulting in a net reduction of ~2,573 lines.

### Affected agents
AdFormatSelectorAgent, AdLoadOptimizationAgent, AdvertiserAgent, AgencyAgent, CampaignOptimizationAgent, CreativeSelectionAgent, CurrentEventsAgent, IdentityAgent, InventoryOptimizationAgent, MeasurementAgent, PublisherAgent, SignalAgent, VerificationAgent, YieldOptimizationAgent.

### Removed files
- `agent_interaction_matrix.md` — Deprecated interaction matrix (100 lines).
- `BidSimulatorAgent.txt`, `EventsAgent.txt`, `WeatherImpactAgent.txt` — Removed or consolidated.

---

## 6. Tab Configurations Overhaul

### What changed
`bedrock-adtech-demo/src/assets/tab-configurations.json` was expanded from basic tab definitions to a comprehensive scenario library with agent-specific demo scenarios.

### New structure
Each tab now includes:
- `availableAgents` — Explicit list of agents available in that tab context.
- `scenarios` — Array of pre-built demo scenarios with `id`, `title`, `description`, `query`, `category`, and `agentType`.
- Scenarios cover Campaign Management, Publisher Yield Optimization, and Creative Optimization workflows.

A copy was also added to `synthetic_data/configs/tab-configurations.json` for the Publisher Yield Optimization tab.

---

## 7. Infrastructure & Deployment

### CloudFormation changes
- Expanded AgentCore IAM permissions to cover Gateway operations, session management, memory records, and policy engines.
- Removed deprecated AppSync Events API permissions (`appsync:EventConnect`, `EventSubscribe`, `EventPublish`).
- Added inference profile ARN pattern to Bedrock permissions.

### Dockerfile
- Added `BEDROCK_AGENTCORE_MEMORY_ID` and `BEDROCK_AGENTCORE_MEMORY_NAME` environment variables.

### Deployment scripts
- `build_and_deploy.sh` — Significant expansion (~480 lines changed) for DynamoDB config upload integration.
- `deploy-ecosystem.sh` — Major expansion (~1,273 lines changed) for end-to-end ecosystem deployment.
- `deploy_agentcore_manual.py` — Updated for new config loading patterns.

---

## 8. Frontend Service Updates

### agent-config.service.ts
- Enhanced to load agent configurations from DynamoDB-backed global config with color enrichment from `configured_colors`.

### aws-config.service.ts
- Expanded with additional AWS SDK configuration for DynamoDB and AgentCore services.

### bedrock.service.ts
- Updated streaming and response handling (~325 lines changed) for improved visualization detection and formatting.

---

## Branch Reference

| Branch | Description |
|---|---|
| `main-v1-baseline` | Snapshot of the previous GitHub main (commit `dadb968`) |
| `v2-candidate` | Current version with all upgrades described above |
| `main` (GitHub) | Unchanged — still points to v1 until promotion |
