# AgentCore Project

This project was created with the [AgentCore CLI](https://github.com/aws/agentcore-cli).

It hosts `adcpRefSeller`, the **reference AdCP seller agent** — a minimal conformant
implementation, meant as the thing a richer seller is built against rather than as a
production seller itself. It is the only seller in this repository. Its AdCP conformance
notes live in `SELLER-AGENT-ADCP-COMPLIANCE.md`.

Everything below this section is the AgentCore CLI's own scaffold documentation.

## No corpus, no cache, no ranking — deliberately

A richer AdCP seller typically answers `get_products` from a pre-built **context cache**:
an embedding index plus a SQLite sidecar, fetched from S3 at runtime and scored offline
against a benchmark corpus of briefs. This seller has none of that, and its absence is a
decision rather than an omission.

Ranking has nothing to do here. The catalogue is **five hand-authored products** in
`app/adcpRefSeller/fixtures.py` — CTV, rewarded mobile, two display sizes (300x250 and
the 728x90 leaderboard) and podcast audio — chosen to exercise the protocol rather than
to be searched: every AdCP tool, every required field, every error shape. `get_products`
matches them by deterministic keyword overlap, and an absent brief returns all five,
which is what the AdCP spec's wholesale semantics call for.

There is no ordering problem among five products. So there is nothing for a semantic
index to contribute and nothing for a benchmark corpus to measure. Adding either would
mean maintaining an artifact pipeline, a bucket, an IAM grant and a multi-thousand-brief
answer key in order to rank a catalogue that fits on one screen.

Consequences worth knowing:

- `get_products` does **not** emit `ext.search_metadata` or any `search_method`. There
  are no two retrieval paths to disclose between.
- There is no `CACHE_BUCKET` environment variable, no `context-cache/` S3 prefix, and
  no cache-read IAM policy in its `agentcore.json`.
- `cache_buckets.py` at the subtree root has no entry for this seller, so asking it for
  a bucket raises rather than returning a name nothing would read.
- `deploy_all.py`'s cache and corpus steps (3.7 `cache-buckets`, 4.6
  `vendor-cache-modules`, 4.62 `corpus`, 4.65 `publish-cache`) are **inactive in this
  repository** — they served sellers that are not part of it. Nothing is missing from
  this seller's deploy.

## Project Structure

```
my-project/
├── AGENTS.md               # AI coding assistant context
├── agentcore/
│   ├── agentcore.json      # Project config (agents, memories, credentials, gateways, evaluators).
│   │                       #   Generated + gitignored — assembled from agentcore.fragment.json
│   ├── agentcore.fragment.json  # What differs from agents/_shared/agentcore.base.json (committed)
│   ├── aws-targets.json    # Deployment targets (account + region). Generated + gitignored
│   ├── .env.local          # Secrets — API keys (gitignored)
│   ├── .llm-context/       # TypeScript type definitions for AI assistants
│   │   ├── agentcore.ts    # AgentCoreProjectSpec types
│   │   ├── aws-targets.ts  # Deployment target types
│   │   └── mcp.ts          # Gateway and MCP tool types
│   └── cdk/                # CDK infrastructure (@aws/agentcore-cdk)
├── app/                    # Agent application code
└── evaluators/             # Custom evaluator code (if any)
```

## Getting Started

### Prerequisites

- **Node.js** 20.x or later
- **Python 3.10+** and **uv** for Python agents ([install uv](https://docs.astral.sh/uv/getting-started/installation/))
- **AWS credentials** configured (`aws configure` or environment variables)
- **Docker** (only for Container build agents)

### Development

Run your agent locally:

```bash
agentcore dev
```

### Deployment

Deploy to AWS:

```bash
agentcore deploy
```

## Commands

| Command | Description |
| --- | --- |
| `agentcore create` | Create a new AgentCore project |
| `agentcore add` | Add resources (agent, memory, credential, gateway, evaluator, policy) |
| `agentcore remove` | Remove resources |
| `agentcore dev` | Run agent locally with hot-reload |
| `agentcore deploy` | Deploy to AWS via CDK |
| `agentcore status` | Show deployment status |
| `agentcore invoke` | Invoke agent (local or deployed) |
| `agentcore logs` | View agent logs |
| `agentcore traces` | View agent traces |
| `agentcore eval` | Run evaluations |
| `agentcore package` | Package agent artifacts |
| `agentcore validate` | Validate configuration |
| `agentcore pause` | Pause a deployed agent |
| `agentcore resume` | Resume a paused agent |
| `agentcore fetch` | Fetch remote resource definitions |
| `agentcore import` | Import existing resources |
| `agentcore update` | Check for CLI updates |

## Configuration

Edit the JSON files in `agentcore/` to configure your project. See `agentcore/.llm-context/` for type definitions and validation constraints.

The project uses a **flat resource model** — agents, memories, credentials, gateways, evaluators, and policies are top-level arrays in `agentcore.json`. Resources are independent; agents discover memories and credentials at runtime via environment variables or SDK calls.

## Resources

| Resource | Purpose |
| --- | --- |
| Agent (runtime) | HTTP, MCP, or A2A agent deployed to AgentCore Runtime |
| Memory | Persistent context storage with configurable strategies |
| Credential | API key or OAuth credential providers |
| Gateway | MCP gateway that routes tool calls to targets |
| Gateway Target | Tool implementation (Lambda, MCP server, OpenAPI, Smithy, API Gateway) |
| Evaluator | Custom LLM-as-a-Judge or code-based evaluation |
| Online Eval Config | Continuous evaluation pipeline for deployed agents |
| Policy | Cedar authorization policies for gateway tools |

### Agent Types

- **Template agents**: Created from framework templates (Strands, LangChain/LangGraph, GoogleADK, OpenAI Agents, Autogen)
- **BYO agents**: Bring your own code with `agentcore add agent --type byo`
- **Import agents**: Import existing Bedrock agents with `agentcore import`

### Build Types

- **CodeZip**: Python source packaged as a zip and deployed directly to AgentCore Runtime
- **Container**: Docker image built via CodeBuild (ARM64), pushed to ECR, and deployed to AgentCore Runtime

## Documentation

- [AgentCore CLI](https://github.com/aws/agentcore-cli)
- [AgentCore CDK Constructs](https://github.com/aws/agentcore-l3-cdk-constructs)
- [Amazon Bedrock AgentCore](https://aws.amazon.com/bedrock/agentcore/)
