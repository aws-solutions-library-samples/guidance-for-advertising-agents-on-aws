# AdCP Reference Test Seller Agent

A minimal, spec-conformant AdCP sales agent, built on the official
[`adcp`](https://pypi.org/project/adcp/) Python SDK, deployed to AWS
Bedrock AgentCore Runtime as an MCP server. Exists so the buyer agent in
`../../../../buyer/reference-buyer` has a seller to talk to without needing any
third-party seller's production credentials — the buyer agent's seller
registry (`SELLER_AGENTS_JSON`) can point at any AdCP seller; this is just
one built-in, always-available option, useful for demos and local
development.

This is explicitly a **sandbox/reference seller, not a production one**:

- Its inventory (`fixtures.py`) is a small, fixed, hand-authored catalog of
  4 products (CTV, mobile rewarded video, display, podcast audio) — not
  live market data.
- Every response is built with `adcp.server.responses` builders, which set
  `sandbox: true` by default, so callers can see on the wire that this
  isn't a production seller.
- `get_products` matches the brief deterministically (keyword overlap —
  see `fixtures.py::match_products`). No LLM, no randomness: the same
  brief always returns the same products.

Implements only the three read-only discovery tasks the buyer agent
actually calls: `get_adcp_capabilities`, `get_products`,
`list_creative_formats`. No mutating tasks (`create_media_buy`, etc.) —
calling one returns the SDK's built-in "not supported" response, since
this agent doesn't implement them.

## Local development

```bash
uv sync
uv run python main.py
```

Server starts on `http://0.0.0.0:8000/mcp` (stateless streamable-HTTP),
matching [AgentCore Runtime's MCP container
contract](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-mcp-protocol-contract.html)
exactly, so the same code runs locally and deployed.

Test it without a bearer token (no auth enforced locally — the deployed
runtime enforces Cognito JWT auth at the platform level, see "Deployment"
below):

```python
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async with streamablehttp_client("http://localhost:8000/mcp", headers={}) as (read, write, _):
    async with ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool("get_adcp_capabilities", {})
        print(result)
```

## Deployment

Deployed via the [AgentCore CLI](https://github.com/aws/agentcore-cli)
(`@aws/agentcore`), not the older `bedrock-agentcore-starter-toolkit`
(deprecated — AWS's current recommended path is the CLI). From
`../../` (the `reference-seller/` project root):

```bash
agentcore deploy
```

**Note:** this manual command is still correct and unchanged. The
repo-root `deploy_all.py` (see the root `README.md`'s "Deploying
everything" section) also calls it as part of the full orchestrated
deploy, but first runs `../../agentcore/render_agentcore_config.py` to
sync this project's `agentcore.json` Cognito authorizer block
(`discoveryUrl`/`allowedClients`) from the buyer agent's `.env` — the
actual source of truth for the shared Cognito pool/client — since
`agentcore.json` has no env-var interpolation support of its own. After
`agentcore deploy` succeeds, `deploy_all.py` also reads the resulting
runtime ARN out of `agentcore/.cli/deployed-state.json` and writes it
into `.env` (as `SELLER_RUNTIME_ARN`) automatically, instead of requiring
a manual copy-paste.

This synthesizes and deploys a CDK stack (`AgentCore-RefSeller-default`)
that creates:

- An ECR repository and CodeBuild project (ARM64 container image build)
- An IAM execution role, auto-created and scoped by the CLI (no Bedrock
  model permissions needed — this agent doesn't call an LLM)
- The AgentCore Runtime resource itself, configured with `protocol: "MCP"`

### Authentication

The deployed runtime requires a valid Cognito access token on every
call — see `../../agentcore/agentcore.json`'s `authorizerConfiguration`. It
**reuses the same Cognito user pool and app client** the buyer agent
(`../../../../buyer/reference-buyer`) already has, set up via that project's
`deploy_cognito_setup.py`. This means the buyer's existing test user
credentials work against this seller too, without provisioning a second
identity provider. `requestHeaderAllowlist: ["Authorization"]` is also set
so the platform forwards the header into the container — without it, the
platform validates the header but strips it before the container sees it
(the same issue the buyer agent's own auth setup hit).

**Note:** `adcp.server.serve()`'s built-in DNS-rebinding protection had to
be disabled (`enable_dns_rebinding_protection=False` in `main.py`) — the
SDK's default `Host` header allowlist (`localhost`/`127.0.0.1`) doesn't
match what AgentCore Runtime's edge proxy sends, which otherwise produces
an HTTP 421 "Misdirected Request" on every call before it reaches this
agent's code. This is safe here because the only network path to this
container is AgentCore Runtime's own trusted proxy layer — the inbound
security boundary is the Cognito JWT authorizer above, not this
host-header check.

To exercise the deployed instance (from a Python environment with the
buyer agent's dependencies, since it reuses
`auth.get_test_user_access_token()` to mint a Cognito token):

```python
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
# import get_test_user_access_token from ../../../../buyer/reference-buyer/auth.py

token = get_test_user_access_token()
url = "https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/{escaped_arn}/invocations?qualifier=DEFAULT"
headers = {"authorization": f"Bearer {token}", "Content-Type": "application/json"}

async with streamablehttp_client(url, headers, timeout=120, terminate_on_close=False) as (read, write, _):
    async with ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool("get_products", {"brief": "CTV sports inventory", "buying_mode": "brief"})
        print(result)
```

## Files

```
main.py       - ADCPHandler subclass (get_adcp_capabilities, get_products,
                list_creative_formats) + serve() entrypoint
fixtures.py   - fixed sandbox product catalog + deterministic brief matching
pyproject.toml - depends only on adcp==6.6.0
```

## Wiring into the buyer agent

The buyer agent's seller registry
(`../../../../buyer/reference-buyer/.env`'s `SELLER_AGENTS_JSON`) has an entry:

```json
{
  "id": "reference",
  "name": "AdCP Reference Test Seller (sandbox)",
  "url": "https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/{escaped_arn}/invocations?qualifier=DEFAULT",
  "auth_type": "cognito_bearer"
}
```

`auth_type: "cognito_bearer"` tells the buyer agent to mint a fresh token
per call using its own Cognito test user (same pool this runtime trusts),
rather than reading a static token from an env var. This is the current
`DEFAULT_SELLER_AGENT_ID`, so the buyer's UI and API default to this
seller unless a different registry entry is explicitly selected.
