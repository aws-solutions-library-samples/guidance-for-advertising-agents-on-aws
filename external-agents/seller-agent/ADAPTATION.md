# ADAPTATION — seller-agent (Strands + A2A)

This directory is a **vendored adaptation** of the upstream IAB Tech Lab seller agent
(`https://github.com/rkmaws/seller-agent`, branch `main`), modified so its Amazon Bedrock
AgentCore **runtime** runs on **Strands** and is served over the **A2A** protocol instead of the
original CrewAI HTTP crew. It was brought in-repo to stop maintaining the agent as an
externally-cloned, patched-at-deploy dependency.

## What the A2A runtime uses (new, added here)
- `src/ad_seller/interfaces/agentcore/a2a_main.py` — Strands `Agent` (Bedrock Sonnet 5) served via
  `strands.multiagent.a2a.A2AServer`. **This is the deployed entrypoint.**
- `src/ad_seller/interfaces/agentcore/deal_logic.py` — crewai-free deal creation (extracted from
  `crew_tools.CreateDealTool._create_deal_direct`).
- `src/ad_seller/interfaces/agentcore/internal_server.py` — crewai-free launcher for the internal
  FastAPI+MCP server (extracted from `http_main._start_fastapi_background`).
- `infra/aws/agentcore/requirements-a2a.txt` — Strands deps, **no crewai**.
- `infra/aws/agentcore/deploy.sh` — added `--mode a2a`.

Inventory **reads** come from the retained internal MCP server (`interfaces/api` + `mcp_server.py`,
mounted at `/mcp`) via a Strands `MCPClient`; **deal creation** is a native Strands tool.

## What was NOT changed (retained, not on the runtime path)
The original CrewAI code stays for reference and future work (the roadmap has the buyer reaching
the seller over the seller's **MCP**, so the MCP server must remain): `interfaces/agentcore/http_main.py`,
`interfaces/agentcore/crew_tools.py`, `crews/`, `agents/`, `flows/`, `patches/`, and the CLI/chat/api
interfaces. These are **not imported** by `a2a_main.py`, so the A2A runtime image installs no crewai.

## Deploy
Via `scripts/deploy_aamp_agents.sh` with `AAMP_PROTOCOL=a2a` (default) and `--local-aamp external-agents`,
which calls `deploy.sh --mode a2a`. The deploy keeps the Cognito JWT inbound authorizer and the
`PYTHONPATH=/app/src` src-layout fix, and skips the CrewAI-only source patches.

## Re-syncing from upstream
Re-clone upstream, then re-apply this adaptation: add the four files above and the `a2a` deploy
mode. The upstream domain code (models/clients/engines/storage/tools) is used unchanged.

> Migration tracked in `aidlc-docs/` under the `aamp-strands-migration` feature.
