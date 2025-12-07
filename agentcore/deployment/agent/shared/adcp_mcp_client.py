"""
AdCP MCP Client Integration for Agentic Advertising Ecosystem

This module provides MCP client integration for connecting to AdCP MCP servers.
It supports both local MCP servers (via stdio) and remote MCP Gateways (via HTTP).

Usage:
    # For local development with stdio server
    mcp_client = create_adcp_mcp_client(transport="stdio")
    
    # For AgentCore Gateway (uses AWS IAM SigV4 authentication)
    mcp_client = create_adcp_mcp_client(
        transport="http",
        gateway_url="https://your-gateway-url.bedrock-agentcore.us-east-1.amazonaws.com"
    )
"""

import os
import logging
from typing import Optional, List, Callable, Any

logger = logging.getLogger(__name__)

# Check if MCP dependencies are available
MCP_AVAILABLE = False
SIGV4_AVAILABLE = False

try:
    from mcp import stdio_client, StdioServerParameters
    from mcp.client.streamable_http import streamablehttp_client
    from strands.tools.mcp import MCPClient
    MCP_AVAILABLE = True
except ImportError:
    logger.warning("MCP dependencies not available. Install with: pip install mcp strands-agents")

# Check if SigV4 dependencies are available for IAM authentication
try:
    import boto3
    from botocore.credentials import Credentials
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    import httpx
    SIGV4_AVAILABLE = True
except ImportError:
    logger.warning("SigV4 dependencies not available. Install with: pip install boto3 httpx")


def create_adcp_mcp_client(
    transport: str = "stdio",
    gateway_url: Optional[str] = None,
    server_path: Optional[str] = None,
    auth_token: Optional[str] = None,
    tool_prefix: str = "adcp",
) -> Optional[Any]:
    """
    Create an MCP client for AdCP protocol tools.
    
    Args:
        transport: Transport type - "stdio" for local server, "http" for gateway
        gateway_url: URL for HTTP gateway (required if transport="http")
        server_path: Path to local MCP server script (for stdio transport)
        auth_token: Authentication token for gateway
        tool_prefix: Prefix for tool names to avoid conflicts
    
    Returns:
        MCPClient instance or None if MCP not available
    """
    if not MCP_AVAILABLE:
        logger.error("MCP dependencies not installed")
        return None
    
    if transport == "stdio":
        return _create_stdio_client(server_path, tool_prefix)
    elif transport == "http":
        return _create_http_client(gateway_url, auth_token, tool_prefix)
    else:
        raise ValueError(f"Unknown transport: {transport}")


def _create_stdio_client(server_path: Optional[str], prefix: str) -> Any:
    """Create stdio-based MCP client for local development"""
    if server_path is None:
        # Default to the mock server in synthetic_data
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        server_path = os.path.join(base_dir, "synthetic_data", "mcp_mocks", "adcp_mcp_server.py")
    
    if not os.path.exists(server_path):
        logger.error(f"MCP server not found at: {server_path}")
        return None
    
    logger.info(f"Creating stdio MCP client with server: {server_path}")
    
    return MCPClient(
        lambda: stdio_client(
            StdioServerParameters(
                command="python",
                args=[server_path]
            )
        ),
        prefix=prefix
    )


def _create_http_client(gateway_url: Optional[str], auth_token: Optional[str], prefix: str) -> Any:
    """
    Create HTTP-based MCP client for AgentCore Gateway.
    
    AgentCore Gateway uses AWS IAM authentication (SigV4), so we need to sign
    requests with AWS credentials. This is different from OAuth/Bearer token auth.
    
    The gateway expects requests signed with the 'bedrock-agentcore' service name.
    """
    if not gateway_url:
        gateway_url = os.environ.get("ADCP_GATEWAY_URL")
    
    if not gateway_url:
        logger.error("Gateway URL required for HTTP transport. Set ADCP_GATEWAY_URL env var.")
        return None
    
    # Determine region from gateway URL or environment
    region = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
    
    # Try to extract region from gateway URL if present
    # URL format: https://<gateway-id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp
    if "bedrock-agentcore" in gateway_url:
        try:
            parts = gateway_url.split("bedrock-agentcore.")
            if len(parts) > 1:
                region = parts[1].split(".")[0]
        except Exception:
            pass
    
    logger.info(f"Creating HTTP MCP client with gateway: {gateway_url} (region: {region})")
    
    # Check if we should use SigV4 authentication (default for AgentCore Gateway)
    use_sigv4 = os.environ.get("ADCP_USE_SIGV4", "true").lower() == "true"
    
    if use_sigv4 and SIGV4_AVAILABLE:
        return _create_sigv4_http_client(gateway_url, region, prefix)
    elif auth_token or os.environ.get("ADCP_AUTH_TOKEN"):
        # Fallback to Bearer token if explicitly provided (for OAuth-based gateways)
        headers = {}
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        elif os.environ.get("ADCP_AUTH_TOKEN"):
            headers["Authorization"] = f"Bearer {os.environ['ADCP_AUTH_TOKEN']}"
        
        logger.warning("Using Bearer token auth instead of SigV4. This may not work with IAM-authenticated gateways.")
        return MCPClient(
            lambda: streamablehttp_client(
                url=gateway_url,
                headers=headers if headers else None
            ),
            prefix=prefix
        )
    else:
        logger.error("SigV4 authentication required but dependencies not available. Install boto3 and httpx.")
        return None


def _create_sigv4_http_client(gateway_url: str, region: str, prefix: str) -> Any:
    """
    Create MCP client with AWS SigV4 authentication for AgentCore Gateway.
    
    This uses the streamablehttp_client_with_sigv4 pattern from the AWS examples.
    The service name for AgentCore Gateway is 'bedrock-agentcore'.
    """
    try:
        # Import the SigV4 transport - try local module first, then fall back to inline implementation
        try:
            from ...streamable_http_sigv4 import streamablehttp_client_with_sigv4
            logger.info("Using streamable_http_sigv4 module for SigV4 authentication")
        except ImportError:
            # Use inline implementation if module not available
            from contextlib import asynccontextmanager
            from collections.abc import AsyncGenerator
            from datetime import timedelta
            from typing import Generator
            from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
            from mcp.shared.message import SessionMessage
            from mcp.client.streamable_http import GetSessionIdCallback
            
            class SigV4HTTPXAuth(httpx.Auth):
                """HTTPX Auth class that signs requests with AWS SigV4."""
                
                def __init__(self, credentials: Credentials, service: str, region: str):
                    self.credentials = credentials
                    self.service = service
                    self.region = region
                    self.signer = SigV4Auth(credentials, service, region)
                
                def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
                    headers = dict(request.headers)
                    headers.pop("connection", None)  # Remove keep-alive header
                    
                    aws_request = AWSRequest(
                        method=request.method,
                        url=str(request.url),
                        data=request.content,
                        headers=headers,
                    )
                    self.signer.add_auth(aws_request)
                    request.headers.update(dict(aws_request.headers))
                    yield request
            
            @asynccontextmanager
            async def streamablehttp_client_with_sigv4(
                url: str,
                credentials: Credentials,
                service: str,
                region: str,
                headers: dict = None,
                timeout: float = 30,
                sse_read_timeout: float = 300,
                terminate_on_close: bool = True,
                httpx_client_factory = None,
            ) -> AsyncGenerator:
                """Streamable HTTP client with SigV4 authentication."""
                async with streamablehttp_client(
                    url=url,
                    headers=headers,
                    timeout=timeout,
                    sse_read_timeout=sse_read_timeout,
                    terminate_on_close=terminate_on_close,
                    httpx_client_factory=httpx_client_factory,
                    auth=SigV4HTTPXAuth(credentials, service, region),
                ) as result:
                    yield result
            
            logger.info("Using inline SigV4 implementation for authentication")
        
        # Get AWS credentials from the default credential chain
        session = boto3.Session()
        credentials = session.get_credentials()
        
        if credentials is None:
            logger.error("No AWS credentials found. Configure AWS credentials for SigV4 authentication.")
            return None
        
        # Get frozen credentials (handles refresh for assumed roles)
        frozen_credentials = credentials.get_frozen_credentials()
        
        botocore_credentials = Credentials(
            access_key=frozen_credentials.access_key,
            secret_key=frozen_credentials.secret_key,
            token=frozen_credentials.token
        )
        
        service_name = "bedrock-agentcore"
        
        logger.info(f"Creating SigV4-authenticated MCP client for service '{service_name}' in region '{region}'")
        
        # Create the MCP client with SigV4 transport
        return MCPClient(
            lambda: streamablehttp_client_with_sigv4(
                url=gateway_url,
                credentials=botocore_credentials,
                service=service_name,
                region=region,
            ),
            prefix=prefix
        )
        
    except Exception as e:
        logger.error(f"Failed to create SigV4 HTTP client: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None


def get_adcp_tools_from_mcp(mcp_client: Any) -> List[Callable]:
    """
    Get tools from MCP client for use with Strands agent.
    
    Args:
        mcp_client: MCPClient instance
    
    Returns:
        List of tool functions
    """
    if mcp_client is None:
        logger.warning("MCP client is None, returning empty tools list")
        return []
    
    try:
        # Use the managed approach - MCPClient implements ToolProvider
        # The agent will handle lifecycle automatically
        return [mcp_client]
    except Exception as e:
        logger.error(f"Failed to get tools from MCP client: {e}")
        return []


class AdCPMCPToolProvider:
    """
    Tool provider that wraps AdCP MCP client for use with Strands agents.
    
    This class provides a clean interface for integrating AdCP MCP tools
    into the agent handler, supporting both local and gateway deployments.
    """
    
    def __init__(
        self,
        transport: str = "stdio",
        gateway_url: Optional[str] = None,
        server_path: Optional[str] = None,
        auth_token: Optional[str] = None,
    ):
        self.transport = transport
        self.gateway_url = gateway_url
        self.server_path = server_path
        self.auth_token = auth_token
        self._client = None
        self._tools = None
    
    @property
    def client(self) -> Optional[Any]:
        """Lazy initialization of MCP client"""
        if self._client is None:
            self._client = create_adcp_mcp_client(
                transport=self.transport,
                gateway_url=self.gateway_url,
                server_path=self.server_path,
                auth_token=self.auth_token,
            )
        return self._client
    
    def get_tools(self) -> List[Any]:
        """Get tools for agent integration"""
        if self.client is None:
            return []
        return [self.client]
    
    def is_available(self) -> bool:
        """Check if MCP integration is available"""
        return MCP_AVAILABLE and self.client is not None


# Singleton instance for easy access
_default_provider: Optional[AdCPMCPToolProvider] = None


def get_default_adcp_provider() -> AdCPMCPToolProvider:
    """Get or create the default AdCP MCP tool provider"""
    global _default_provider
    if _default_provider is None:
        # Determine transport based on environment
        gateway_url = os.environ.get("ADCP_GATEWAY_URL")
        if gateway_url:
            _default_provider = AdCPMCPToolProvider(
                transport="http",
                gateway_url=gateway_url
            )
            logger.info(f"Created HTTP MCP provider with gateway: {gateway_url}")
        else:
            _default_provider = AdCPMCPToolProvider(transport="stdio")
            logger.info("Created stdio MCP provider for local development")
    return _default_provider


def check_sigv4_auth_available() -> bool:
    """Check if SigV4 authentication is available and configured"""
    if not SIGV4_AVAILABLE:
        return False
    try:
        session = boto3.Session()
        credentials = session.get_credentials()
        return credentials is not None
    except Exception:
        return False
