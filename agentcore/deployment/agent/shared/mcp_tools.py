"""MCP tool provider construction for Strands agents.

Extracts MCP client creation logic from handler.py to keep the main
handler focused on orchestration.  All public functions accept plain
dicts (agent config / server config) so they stay decoupled from the
handler's global state.
"""

import logging
import os
import traceback
from typing import Dict, Optional

from strands.tools.mcp import MCPClient
from mcp import stdio_client, StdioServerParameters

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_mcp_tools_for_agent(agent_name: str, agent_config: dict) -> list:
    """Build MCP tool providers from the agent's mcp_servers configuration.

    Follows the Strands Agents MCP integration pattern:
    https://strandsagents.com/latest/documentation/docs/user-guide/concepts/tools/mcp-tools/

    Args:
        agent_name: Name of the agent
        agent_config: Agent configuration dict containing mcp_servers

    Returns:
        List of MCPClient instances configured for this agent
    """
    mcp_servers = agent_config.get("mcp_servers", [])

    logger.info(f"🔍 MCP_TOOLS: Checking mcp_servers for {agent_name}")
    logger.info(f"   Agent config keys: {list(agent_config.keys())}")
    logger.info(f"   mcp_servers count: {len(mcp_servers)}")

    if not mcp_servers:
        logger.info(f"⚠️ MCP_TOOLS: No mcp_servers configured for {agent_name}")
        return []

    logger.info(f"🔌 MCP_TOOLS: Found {len(mcp_servers)} MCP server(s) for {agent_name}")
    for i, srv in enumerate(mcp_servers):
        logger.info(
            f"   [{i+1}] {srv.get('name', 'unnamed')} - "
            f"transport: {srv.get('transport', 'unknown')}, "
            f"enabled: {srv.get('enabled', True)}"
        )

    mcp_tools = []

    for server_config in mcp_servers:
        if not server_config.get("enabled", True):
            logger.info(
                f"⏭️ MCP_TOOLS: Skipping disabled MCP server "
                f"'{server_config.get('name', 'unknown')}' for {agent_name}"
            )
            continue

        try:
            mcp_client = create_mcp_client_from_config(server_config, agent_name)
            if mcp_client:
                mcp_tools.append(mcp_client)
                logger.info(f"✅ MCP_TOOLS: Created MCP client '{server_config.get('name')}' for {agent_name}")
        except Exception as e:
            logger.error(
                f"❌ MCP_TOOLS: Failed to create MCP client "
                f"'{server_config.get('name')}' for {agent_name}: {e}"
            )
            logger.error(f"   Traceback: {traceback.format_exc()}")

    return mcp_tools


# ---------------------------------------------------------------------------
# Client factory
# ---------------------------------------------------------------------------

def create_mcp_client_from_config(server_config: dict, agent_name: str) -> Optional[MCPClient]:
    """Create an MCPClient instance from a server configuration.

    Supports three transport types:
    - stdio: Command-line tools (uvx, python, npx, etc.)
    - http: HTTP-based MCP servers (Streamable HTTP)
    - sse: Server-Sent Events transport
    """
    transport = server_config.get("transport", "stdio")
    server_name = server_config.get("name", "unknown")
    prefix = server_config.get("prefix", "")

    tool_filters: Dict[str, list] = {}
    if server_config.get("allowedTools"):
        tool_filters["allowed"] = server_config["allowedTools"]
    if server_config.get("rejectedTools"):
        tool_filters["rejected"] = server_config["rejectedTools"]

    try:
        if transport == "stdio":
            return _create_stdio(server_config, prefix, tool_filters)
        elif transport == "http":
            return _create_http(server_config, prefix, tool_filters)
        elif transport == "sse":
            return _create_sse(server_config, prefix, tool_filters)
        else:
            logger.error(f"❌ MCP_TOOLS: Unknown transport type '{transport}' for server '{server_name}'")
            return None
    except Exception as e:
        logger.error(f"❌ MCP_TOOLS: Failed to create {transport} MCP client for '{server_name}': {e}")
        return None


# ---------------------------------------------------------------------------
# Transport-specific helpers
# ---------------------------------------------------------------------------

def _create_stdio(server_config: dict, prefix: str, tool_filters: dict) -> Optional[MCPClient]:
    """Create an MCPClient with stdio transport."""
    command = server_config.get("command")
    args = server_config.get("args", [])
    env = server_config.get("env", {})

    if not command:
        logger.error("❌ MCP_TOOLS: stdio transport requires 'command' field")
        return None

    logger.info(f"🔌 MCP_TOOLS: Creating stdio MCP client: {command} {' '.join(args)}")

    full_env = dict(os.environ)
    full_env.update(env)

    mcp_client_kwargs: Dict = {}
    if prefix:
        mcp_client_kwargs["prefix"] = prefix
    if tool_filters:
        mcp_client_kwargs["tool_filters"] = tool_filters

    return MCPClient(
        lambda cmd=command, a=args, e=full_env: stdio_client(
            StdioServerParameters(command=cmd, args=a, env=e)
        ),
        **mcp_client_kwargs,
    )


def _create_http(server_config: dict, prefix: str, tool_filters: dict) -> Optional[MCPClient]:
    """Create an MCPClient with HTTP (Streamable HTTP) transport."""
    url = server_config.get("url")
    aws_auth = server_config.get("awsAuth")
    headers = server_config.get("headers", {})
    server_name = server_config.get("name", "unknown")

    if not url:
        logger.error(f"❌ MCP_TOOLS: http transport requires 'url' field for server '{server_name}'")
        return None

    logger.info(f"🔌 MCP_TOOLS: Creating HTTP MCP client for '{server_name}': {url}")
    logger.info(f"   AWS Auth: {aws_auth is not None}")
    logger.info(f"   Custom Headers: {list(headers.keys()) if headers else 'None'}")
    logger.info(f"   Prefix: '{prefix}' | Tool filters: {bool(tool_filters)}")

    mcp_client_kwargs: Dict = {}
    if prefix:
        mcp_client_kwargs["prefix"] = prefix
    if tool_filters:
        mcp_client_kwargs["tool_filters"] = tool_filters

    if aws_auth:
        try:
            from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client

            aws_region = aws_auth.get("region", os.environ.get("AWS_REGION", "us-east-1"))
            aws_service = aws_auth.get("service", "bedrock-agentcore")

            logger.info(f"🔐 MCP_TOOLS: Using AWS IAM auth for {url} (region={aws_region}, service={aws_service})")

            return MCPClient(
                lambda u=url, r=aws_region, s=aws_service: aws_iam_streamablehttp_client(
                    endpoint=u, aws_region=r, aws_service=s
                ),
                **mcp_client_kwargs,
            )
        except ImportError:
            logger.error("❌ MCP_TOOLS: mcp-proxy-for-aws not installed. Install with: pip install mcp-proxy-for-aws")
            return None
    else:
        try:
            from mcp.client.streamable_http import streamablehttp_client

            logger.info(f"✅ MCP_TOOLS: Successfully imported streamablehttp_client, creating MCPClient for '{server_name}'")

            if headers:
                logger.info("🔑 MCP_TOOLS: Using custom headers for authentication")
                mcp_client = MCPClient(
                    lambda u=url, h=headers: streamablehttp_client(url=u, headers=h),
                    **mcp_client_kwargs,
                )
            else:
                mcp_client = MCPClient(
                    lambda u=url: streamablehttp_client(u),
                    **mcp_client_kwargs,
                )
            logger.info(f"✅ MCP_TOOLS: MCPClient created successfully for '{server_name}'")
            return mcp_client
        except ImportError as e:
            logger.error(f"❌ MCP_TOOLS: mcp.client.streamable_http not available: {e}")
            return None
        except Exception as e:
            logger.error(f"❌ MCP_TOOLS: Failed to create MCPClient for '{server_name}': {e}")
            logger.error(f"   Traceback: {traceback.format_exc()}")
            return None


def _create_sse(server_config: dict, prefix: str, tool_filters: dict) -> Optional[MCPClient]:
    """Create an MCPClient with Server-Sent Events (SSE) transport."""
    url = server_config.get("url")

    if not url:
        logger.error("❌ MCP_TOOLS: sse transport requires 'url' field")
        return None

    logger.info(f"🔌 MCP_TOOLS: Creating SSE MCP client: {url}")

    mcp_client_kwargs: Dict = {}
    if prefix:
        mcp_client_kwargs["prefix"] = prefix
    if tool_filters:
        mcp_client_kwargs["tool_filters"] = tool_filters

    try:
        from mcp.client.sse import sse_client

        return MCPClient(
            lambda u=url: sse_client(u),
            **mcp_client_kwargs,
        )
    except ImportError:
        logger.error("❌ MCP_TOOLS: mcp.client.sse not available")
        return None
