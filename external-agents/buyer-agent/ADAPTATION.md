# ADAPTATION — buyer-agent (Strands + A2A)

This directory is a **vendored adaptation** of the upstream IAB Tech Lab buyer agent
(`https://github.com/rkmaws/buyer-agent`, branch `main`), modified so its Amazon Bedrock AgentCore
**runtime** runs on **Strands** and is served over the **A2A** protocol instead of the original
CrewAI `DealBookingFlow`. Brought in-repo to stop maintaining it as an externally-cloned,
patched-at-deploy dependency.

## What the A2A runtime uses (new, added here)
- `src/ad_buyer/interfaces/agentcore/a2a_main.py` — a Strands **coordinator** agent (Bedrock
  Haiku 4.5) served via `A2AServer`, with two tools: `allocate_budget` (runs one Sonnet-5
  `structured_output` budget call) and `search_inventory` (calls the seller). **Deployed entrypoint.**
- `src/ad_buyer/interfaces/agentcore/budget_models.py` — crewai-free `BudgetAllocationOutput`
  (extracted from `crews/portfolio_crew.py`).
- `src/ad_buyer/interfaces/agentcore/planning_helpers.py` — crewai-free brief parse + audience
  heuristics (extracted from `crew_tools` / `flows/deal_booking_flow.py`).
- `src/ad_buyer/interfaces/agentcore/seller_client.py` — reaches the **seller's A2A runtime** with a
  Cognito bearer (OAuth A2A `message/send` POST — the same pattern the AgencyAgent uses). Replaces
  the broken `OpenDirectClient` dummy-URL path.
- `infra/aws/agentcore/requirements-a2a.txt` — Strands deps, **no crewai**.
- `infra/aws/agentcore/deploy.sh` — added `--mode a2a`.

## What was NOT changed (retained, not on the runtime path)
The original CrewAI code stays for reference and future work (roadmap: buyer→seller over the
seller's MCP): `interfaces/agentcore/{http_main,crew_tools}.py`, `crews/`, `agents/`, `flows/`,
`pipelines/`, `patches/`, and the CLI/chat/api/`mcp_server` interfaces. Not imported by `a2a_main.py`.

## Deploy
Via `scripts/deploy_aamp_agents.sh` with `AAMP_PROTOCOL=a2a` (default). After the seller deploys,
the parent injects `AAMP_SELLER_RUNTIME_ARN`, `A2A_SELLER_SSM_PATH`, `A2A_CLIENT_ID`, `AWS_REGION`,
`AGENTCORE_A2A_PORT` into the buyer runtime so `seller_client` can authenticate to the seller.

## Re-syncing from upstream
Re-clone upstream, then re-add the five files above and the `a2a` deploy mode. Upstream domain code
(models incl. `BookingState`/`ProductRecommendation`, clients, storage) is used unchanged.

> Migration tracked in `aidlc-docs/` under the `aamp-strands-migration` feature.
