# AdCP MCP Integration Guide

This guide explains how to set up and use the Ad Context Protocol (AdCP) MCP integration for the Agentic Advertising Ecosystem.

## Overview

The AdCP MCP integration provides two modes of operation:

1. **Fallback Mode** (Default): Uses hardcoded mock data directly in the tools
2. **MCP Mode**: Connects to a real MCP server (local or via AgentCore Gateway)

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Agent Handler                                │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                   adcp_tools.py                          │   │
│  │  ┌─────────────┐    ┌─────────────────────────────────┐ │   │
│  │  │ ADCP_TOOLS  │───>│ _call_mcp_tool() or Fallback    │ │   │
│  │  └─────────────┘    └─────────────────────────────────┘ │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ (if MCP enabled)
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    MCP Transport Layer                           │
│  ┌─────────────────────┐    ┌─────────────────────────────────┐ │
│  │   stdio (local)     │ OR │   HTTP (AgentCore Gateway)      │ │
│  └─────────────────────┘    └─────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    AdCP MCP Server                               │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  FastMCP Server (adcp_mcp_server.py)                     │   │
│  │  - get_products (AdCP Media Buy Protocol)                │   │
│  │  - get_signals (AdCP Signals Protocol)                   │   │
│  │  - activate_signal                                       │   │
│  │  - create_media_buy                                      │   │
│  │  - get_media_buy_delivery                                │   │
│  │  - verify_brand_safety (MCP Verification)                │   │
│  │  - resolve_audience_reach (MCP Identity)                 │   │
│  │  - configure_brand_lift_study (MCP Measurement)          │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

## Quick Start

### Option 1: Fallback Mode (No Setup Required)

By default, the tools use hardcoded mock data. No additional setup needed:

```python
from shared.adcp_tools import ADCP_TOOLS

# Tools work immediately with mock data
agent = Agent(tools=ADCP_TOOLS)
```

### Option 2: Local MCP Server (Development)

1. Install MCP dependencies:
```bash
pip install mcp strands-agents
```

2. Start the MCP server:
```bash
cd synthetic_data/mcp_mocks
python adcp_mcp_server.py
```

3. Enable MCP in your environment:
```bash
export ADCP_USE_MCP=true
```

4. The tools will now connect to the local MCP server via stdio.

### Option 3: AgentCore Gateway (Production)

AgentCore Gateway uses **AWS IAM (SigV4) authentication** for secure access. The MCP client automatically signs requests with your AWS credentials.

1. Deploy the MCP Gateway:
```bash
cd agentcore/deployment
python deploy_adcp_gateway.py \
    --stack-prefix myapp \
    --unique-id abc123 \
    --region us-east-1 \
    --profile your-aws-profile  # optional
```

2. Set environment variables from deployment output:
```bash
export ADCP_USE_MCP=true
export ADCP_GATEWAY_URL=https://<gateway-id>.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp
export AWS_REGION=us-east-1
```

3. Ensure your AWS credentials have the `bedrock-agentcore:InvokeGateway` permission:
```json
{
    "Effect": "Allow",
    "Action": "bedrock-agentcore:InvokeGateway",
    "Resource": "arn:aws:bedrock-agentcore:us-east-1:ACCOUNT_ID:gateway/GATEWAY_ID"
}
```

4. The tools will now connect to the AgentCore Gateway via HTTP with SigV4 authentication.

## Authentication

### AWS IAM (SigV4) Authentication

AgentCore Gateway uses AWS IAM for authentication. The MCP client automatically:
1. Retrieves AWS credentials from the default credential chain (env vars, ~/.aws/credentials, IAM role, etc.)
2. Signs each request with AWS SigV4 using the `bedrock-agentcore` service name
3. Includes the signature in request headers

**Required IAM Permissions:**
- `bedrock-agentcore:InvokeGateway` on the gateway resource

**Credential Sources (in order of precedence):**
1. Environment variables (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`)
2. Shared credentials file (`~/.aws/credentials`)
3. IAM role (for EC2, Lambda, ECS, etc.)

### Assuming the Invoke Role

The deployment script creates an invoke role that you can assume:
```python
import boto3

sts = boto3.client('sts')
response = sts.assume_role(
    RoleArn='arn:aws:iam::ACCOUNT:role/myapp-adcp-invoke-role-abc123',
    RoleSessionName='mcp-session'
)

# Use the temporary credentials
os.environ['AWS_ACCESS_KEY_ID'] = response['Credentials']['AccessKeyId']
os.environ['AWS_SECRET_ACCESS_KEY'] = response['Credentials']['SecretAccessKey']
os.environ['AWS_SESSION_TOKEN'] = response['Credentials']['SessionToken']
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `ADCP_USE_MCP` | Enable MCP integration | `true` |
| `ADCP_GATEWAY_URL` | AgentCore Gateway URL | None |
| `ADCP_MCP_SERVER_PATH` | Path to local MCP server | Auto-detected |
| `ADCP_USE_SIGV4` | Use SigV4 authentication for gateway | `true` |
| `AWS_REGION` | AWS region for SigV4 signing | `us-east-1` |
| `AWS_DEFAULT_REGION` | Fallback region for SigV4 signing | `us-east-1` |

## Testing

### Test Direct Handlers
```bash
cd synthetic_data/mcp_mocks
python test_adcp_server.py
```

### Test MCP Server
```bash
# Terminal 1: Start server
python adcp_mcp_server.py --transport sse --port 8080

# Terminal 2: Test with curl
curl -X POST http://localhost:8080/mcp \
  -H "Content-Type: application/json" \
  -d '{"method": "tools/call", "params": {"name": "get_products", "arguments": {"brief": "test", "channels": ["ctv"]}}}'
```

### Test Lambda Handler
```bash
cd agentcore/deployment
python deploy_adcp_gateway.py --stack-prefix test --unique-id dev --lambda-only

# Test via AWS CLI
aws lambda invoke \
  --function-name test-adcp-handler-dev \
  --payload '{"tool_name": "get_products", "arguments": {"brief": "test"}}' \
  response.json
```

## Available Tools

### AdCP Media Buy Protocol

| Tool | Description |
|------|-------------|
| `adcp_get_products` | Discover publisher inventory matching campaign brief |
| `adcp_create_media_buy` | Create media buy with publisher packages |
| `adcp_get_media_buy_delivery` | Get delivery metrics for a media buy |

### AdCP Signals Protocol

| Tool | Description |
|------|-------------|
| `adcp_get_signals` | Discover audience and contextual signals |
| `adcp_activate_signal` | Activate segment on DSP platform |

### MCP Services

| Tool | Description |
|------|-------------|
| `mcp_verify_brand_safety` | Verify brand safety for properties |
| `mcp_resolve_audience_reach` | Estimate cross-device reach |
| `mcp_configure_brand_lift_study` | Configure measurement study |

## Integration with Strands Agents

### Using Standard Tools (Fallback)
```python
from strands import Agent
from shared.adcp_tools import ADCP_TOOLS

agent = Agent(
    model=bedrock_model,
    tools=ADCP_TOOLS
)

response = agent("Discover CTV inventory for sports campaign")
```

### Using MCP Client (Managed)
```python
from strands import Agent
from shared.adcp_tools import get_adcp_mcp_tools

# Returns MCP client if enabled, otherwise standard tools
tools = get_adcp_mcp_tools()

agent = Agent(
    model=bedrock_model,
    tools=tools
)
```

### Checking MCP Status
```python
from shared.adcp_tools import is_mcp_enabled

if is_mcp_enabled():
    print("Using MCP server")
else:
    print("Using fallback mode")
```

## Deployment Architecture

### Local Development
```
Agent → adcp_tools.py → stdio → adcp_mcp_server.py (local process)
```

### Production (AgentCore Gateway)
```
Agent → adcp_tools.py → HTTP → AgentCore Gateway → Lambda Handler
```

### Lambda Handler Flow
```
MCP Gateway → Lambda Function → AdCP Protocol Handlers → Response
```

## Troubleshooting

### MCP Not Connecting
1. Check `ADCP_USE_MCP=true` is set
2. Verify MCP dependencies: `pip install mcp strands-agents`
3. Check server is running: `python adcp_mcp_server.py`

### Gateway Authentication Errors (403/401)
1. **Verify AWS credentials are configured:**
   ```bash
   aws sts get-caller-identity
   ```
2. **Check IAM permissions:** Your role/user needs `bedrock-agentcore:InvokeGateway` permission
3. **Verify SigV4 is enabled:** Check `ADCP_USE_SIGV4=true` (default)
4. **Check region matches:** The region in the gateway URL must match your AWS_REGION
5. **Review CloudWatch logs:** Check the gateway and Lambda logs for detailed errors

### Common Authentication Issues

**"Signature mismatch" errors:**
- Ensure the `connection: keep-alive` header is not included in signature calculation
- Check that the service name is `bedrock-agentcore`
- Verify the region is correct

**"Access Denied" errors:**
- Your IAM principal needs `bedrock-agentcore:InvokeGateway` permission
- The gateway resource ARN in the policy must match your gateway

**"Credentials not found" errors:**
- Configure AWS credentials via environment variables, ~/.aws/credentials, or IAM role
- Install boto3: `pip install boto3`

### Gateway Errors
1. Verify gateway URL is correct (should end with `/mcp`)
2. Check that the gateway was created with `authorizerType=AWS_IAM`
3. Review CloudWatch logs for Lambda errors

### Fallback Mode Active
If you see `"source": "fallback"` in responses, MCP is not connected:
1. Check environment variables
2. Verify MCP server is accessible
3. Check for import errors in logs
4. Verify AWS credentials are valid for gateway authentication

## Files Reference

| File | Purpose |
|------|---------|
| `agentcore/deployment/agent/shared/adcp_tools.py` | Main tools with MCP/fallback support |
| `agentcore/deployment/agent/shared/adcp_mcp_client.py` | MCP client wrapper |
| `agentcore/deployment/agent/shared/adcp_mcp_server.py` | FastMCP server implementation |
| `agentcore/deployment/deploy_adcp_gateway.py` | Gateway deployment script |
