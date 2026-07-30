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

---

# Post-v2 Updates (A2A, External Agents, Invocation Notifications)

This section covers changes made after the v2 release described above — spanning A2A protocol hardening, a new external-agents deployment model, an invocation-notification hook, and a two-phase visualization generation rewrite. These are incremental to v2, not a v3 rearchitecture.

## 9. A2A Protocol Hardening

### What changed
The existing A2A (Agent-to-Agent) support was extended with a genuine JSON-RPC transport, an additional inbound/outbound auth type, and session continuity across turns.

### JSON-RPC 2.0 transport
- `agentcore/deployment/agent/shared/a2a_client_tools.py` now builds a proper A2A **`message/send`** JSON-RPC 2.0 envelope for external agents flagged `isA2A: true` in `global_configuration.json`, instead of the legacy `{"prompt", "routing_mode": "crew"}` envelope used by older CrewAI-based agents. `_extract_a2a_text` parses the JSON-RPC response (`result.artifacts[].parts[].text` or `result.parts[].text`) back into plain text for the calling agent.
- The legacy "crew" envelope remains the default for agents without `isA2A` set, so existing CrewAI-style runtimes keep working unmodified.

### New auth type: Bearer Token
- Alongside `none`/`oauth`/`iam`, external agent configs and this agent's own inbound A2A auth now support **`bearer`**: an operator pastes a static token in the UI, it's stored as an SSM `SecureString`, and sent verbatim as `Authorization: Bearer <token>` — no Cognito token minting, no SigV4. This is the only auth type that works against a peer hosted outside AWS.
- The token is re-read fresh from SSM on every call (`resolve_ssm_parameter(..., use_cache=False)`), so a re-pasted (rotated) token takes effect without an agent restart.
- `agentcore/deployment/agent/shared/a2a_auth.py`'s `A2ATokenManager` was also updated so the OAuth path can read an embedded `client_id`/`clientId` from the stored credentials JSON (the schema the UI writes), rather than always requiring it passed separately.
- Both new/updated auth paths fail closed: if a required credential is missing, the tool returns an explicit error rather than sending an unauthenticated request.

### OAuth invoke path over HTTPS
- OAuth-authenticated external AgentCore runtimes are now invoked over the **HTTPS data-plane endpoint** with a Cognito bearer token, instead of the SigV4 `invoke_agent_runtime` API — necessary because a runtime fronted by a Cognito JWT authorizer rejects SigV4-signed requests.

### Session continuity for external agents
- `build_a2a_client_tools`/`_build_agentcore_invoke_tools` now thread the front-end conversation's `session_id` through to a deterministically-derived, stable `runtimeSessionId` (`_derive_runtime_session_id`) for every call to an external runtime. Because the same front-end session always maps to the same runtime session, the external agent retains conversation memory across turns instead of starting fresh each time.

---

## 10. External Agents: Independent Deployment Model

### What changed
A new top-level `external-agents/` directory was added, holding self-contained AgentCore agents that are deployed **outside** `scripts/deploy-ecosystem.sh` and reached by the main agent stack purely over A2A.

### Why separate deployment
These agents may live in their own account/region, use a different runtime/protocol stack, or need independent versioning — so they're deployed via a standalone script, `external-agents/deploy_external_agents.py`, rather than as another phase of the main ecosystem script.

### Reference agents shipped
| Agent | Wired into | Inbound auth | Needs S3 | Notes |
|---|---|---|---|---|
| **AdCreationAgent** | `MediaPlanningAgent` | IAM/SigV4 (same-account) | Yes | Composites brand assets onto 6 standard IAB display ad units, uploads PNGs to S3, returns presigned URLs (never raw bytes). Errors explicitly if no source images are supplied — no fabricated creatives. |
| **AAMPSellerAgent** | `PublisherAgent` | Cognito OAuth (JWT bearer) | No | Implements the deterministic slice of the [IAB Tech Lab seller-agent](https://iabtechlab.github.io/seller-agent/) spec: tiered pricing by buyer identity, stateless multi-round negotiation, Deal-ID minting, and supply-chain (`schain`) transparency. All computed from caller-supplied inputs — no invented pricing. |
| **AdCPSellerAgent** | Not yet fully wired to `PublisherAgent` | Cognito OAuth + RFC 9421 message signing | No (DynamoDB-backed) | A fully [AdCP 3.1](https://docs.adcontextprotocol.org)-compliant sell-side agent, dual transport (A2A + MCP from one image, two runtimes). Runtimes deploy and are independently testable; end-to-end wiring is pending buyer-side AdCP client support in `PublisherAgent`. |

### Deployment mechanics (`deploy_external_agents.py`)
1. **Execution role**: creates (or reuses) a least-privilege IAM role per agent — ECR pull, CloudWatch Logs, X-Ray, AgentCore workload-identity token, Bedrock model invoke, and *only if declared* S3 access scoped to one bucket, or DynamoDB access scoped to that agent's own tables. Never a wildcard resource.
2. **Container build**: builds and pushes an ARM64 image to ECR.
3. **Runtime deploy**: creates/updates an AgentCore runtime with `serverProtocol: A2A`. For `oauth` agents it attaches a Cognito `customJWTAuthorizer` instead of the default IAM/SigV4 inbound auth.
4. **Wiring**: patches the deployed runtime ARN (and, for OAuth, the Cognito pool/client) into the matching `external_agent_configs` entry — both in the local `global_configuration.json` file *and* directly into the **live DynamoDB `AgentConfig` table**, since the running app reads from DynamoDB, not the file. Each step can be individually skipped (`--skip-wire`, `--skip-dynamodb`).
5. **State tracking**: idempotency is tracked in `.external-agents.json` at the repo root, keyed by `<AgentName>:<region>` — re-running updates the existing runtime rather than recreating it.

### Cross-account behavior
- **OAuth agents** need no cross-account IAM trust — the caller presents a Cognito bearer and the runtime's JWT authorizer validates it independently of account boundaries.
- **IAM agents** are same-account by default; cross-account invocation requires a resource-based policy on the runtime granting the caller account's role invoke access (never a wildcard principal — see `docs/IAM_ROLES_AND_PERMISSIONS.md`).

### Enabling the main orchestrator to call external agents
`agentcore/deployment/deploy_agentcore_manual.py` now adds an `AgentCoreRuntimeInvoke` statement to the shared AgentCore Execution Role (`bedrock-agentcore:InvokeAgentRuntime`/`InvokeAgentRuntimeForUser`), scoped to `arn:aws:bedrock-agentcore:*:{account}:runtime/*` — an account-wide *resource* wildcard, not a principal wildcard. This avoids having to append each external agent's runtime ARN to the role after every external-agent deployment, at the cost of granting invoke on any runtime in the account (acceptable since all runtimes are first-party AgentCore workloads in this architecture).

### Optional auto-deploy hook
`scripts/deploy-ecosystem.sh` now offers, at the end of an **interactive** run (after the normal 11 phases), to automatically discover and deploy any `external-agents/*/agentcore.json` it finds. This is skipped by default in non-interactive runs (`--skip-confirmations`), which print a reminder of the manual command instead — external-agent deployment remains an explicit, separate step unless an operator opts in during an interactive run.

For full detail (inbound auth model, the distinction between the deploy-time JWT authorizer and the UI's "Bearer Token" reference storage, and the exact deploy commands), see [`external-agents/README.md`](../external-agents/README.md).

---

## 11. Invocation Notification Hook

### What changed
A new, fully independent optional field, `notify_on_invocation`, was added to the agent configuration schema. When set, the frontend fires a fire-and-forget webhook POST every time that agent is invoked with a real user prompt.

### Design
- **Frontend-only**: implemented entirely in `bedrock-adtech-demo` (new `NotifyDispatchService`); no backend/`handler.py` changes.
- **Independent of A2A**: has no relationship to `external_agent_configs`/`is_a2a`. It fires for *any* invocation of the configured agent, regardless of which downstream transport (direct AgentCore, OAuth-bearer, A2A JSON-RPC) ultimately carries the request.
- **Payload**: `{ sessionId, stepIndex, stepType: "incoming_request", timestamp, content }`, where `content` is the exact invocation payload sent to the agent, and `stepIndex` is a per-session counter. Four additional `stepType` values are reserved in the type for potential future work but never emitted today.
- **Auth**: `none` / `bearer` (SSM-stored static token, same convention as A2A) / `iam` (the request is signed with SigV4 using the current browser session's Cognito credentials — no new IAM role is created).
- **Fire-and-forget contract**: never awaited by the invocation flow, never throws, and never surfaces a delivery status in the UI (a blocked-by-CORS request is indistinguishable from a dropped one in the general case, so the UI doesn't claim a delivery outcome it can't verify).

See `.kiro/specs/a2a-invocation-notify-hook/` for the full requirements/design/tasks trail.

---

## 12. Two-Phase Visualization Generation

### What changed
The `VisualizationAnalyzerService`'s single-LLM-call approach (one large prompt containing every visualization template's full JSON schema) was replaced with a two-phase pipeline to reduce prompt token usage and improve template-selection accuracy.

- **Phase 1 — selection**: a lightweight Haiku call receives only template *descriptions* (no schemas) and picks which template(s), if any, apply to the current agent response.
- **Phase 2 — generation**: a second Haiku call generates the actual data payload, one template at a time, using only that template's schema.

### Agent editor live preview
The Agent Management UI's visualization-mapping editor now renders a **live preview** of the actual visualization component (not just raw JSON) alongside a collapsible JSON detail view, making it easier to validate a mapping while editing.

### New AdCP visualization components
Eight new Angular components were added under `bedrock-adtech-demo/src/app/components/visualizations/` — one per AdCP protocol operation: `adcp-calibrate-content`, `adcp-check-governance`, `adcp-content-standards`, `adcp-create-media-buy`, `adcp-get-media-buys`, `adcp-media-buy-delivery`, `adcp-property-list`, `adcp-update-media-buy`.

---

## 13. Model ID Migration

All hardcoded, dated Claude model ID strings (e.g. `global.anthropic.claude-sonnet-4-5-20250929-v1:0`, `us.anthropic.claude-sonnet-4-20250514-v1:0`) were replaced with the rolling alias **`global.anthropic.claude-sonnet-5`** across `handler.py`, `file_processor.py`, `local_agent_tester.py`, and this repo's example configs — so agent configs don't need to be manually bumped every time Anthropic ships a new dated snapshot behind the same alias. Haiku references were similarly updated to `global.anthropic.claude-haiku-4-5-20251001-v1:0` where applicable. Explicit `temperature` parameters were also removed from several model invocation call sites, relying on model defaults instead.

---

## Branch Reference (updated)

| Branch | Description |
|---|---|
| `main-v1-baseline` | Snapshot of the previous GitHub main (commit `dadb968`) |
| `v2-candidate` / `main` (local) | Everything described in sections 1–13 above |
| `main` (GitHub, `github/main`) | Last pushed snapshot (commit `5ca53bc`, "chore: anonymize real brand/vendor/site names across codebase") — does **not** yet include sections 9–13 |
