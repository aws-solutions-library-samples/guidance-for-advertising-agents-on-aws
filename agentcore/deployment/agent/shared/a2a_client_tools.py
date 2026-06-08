"""A2A client tool provider construction for Strands agents.

Builds A2AClientToolProvider instances from an agent's
``external_agent_configs`` list, handling OAuth, IAM, and no-auth paths.

Error handling wraps every provider creation in try/except so that a
single misconfigured external agent never prevents the remaining agents
from being registered.  A 120-second timeout is applied to all outbound
HTTP requests.
"""

import logging
import os
from typing import Any, Dict, List

from strands_tools.a2a_client import A2AClientToolProvider
from shared.a2a_auth import A2ATokenManager

logger = logging.getLogger(__name__)

# Timeout in seconds for all outbound A2A HTTP requests (Requirement 9.1)
A2A_REQUEST_TIMEOUT_SECONDS = 120


def _sanitize_error_message(error: Exception) -> str:
    """Return a safe error description that never leaks credentials.

    Strips common credential-bearing fields from the string representation
    of the exception so that passwords, tokens, and secrets are not
    propagated to the calling agent or logs.
    """
    msg = str(error)
    # Remove anything that looks like a bearer token or password value
    for sensitive_keyword in ("password", "secret", "token", "credential", "Bearer"):
        if sensitive_keyword.lower() in msg.lower():
            msg = f"{type(error).__name__}: [details redacted for security]"
            break
    return msg


def build_a2a_client_tools(agent_name: str, agent_config: dict) -> List:
    """Build tools for connecting to external agents.

    Supports three config patterns:

    1. **Agent-level runtime_arn** (simplest): The agent config itself has
       a ``runtime_arn`` field pointing to an AgentCore runtime. No
       ``external_agent_configs`` needed. Set via the UI's "Runtime ARN" field.

    2. **external_agent_configs with runtime_arn** (template schema): entries
       with a ``runtime_arn`` field. Used by the resolve_config pipeline.

    3. **external_agent_configs with isA2A** (UI schema): entries where
       ``isA2A`` is True with an ``arn`` field. Used when editing via UI.

    For AgentCore ARNs, creates direct ``invoke_agent_runtime`` tools
    (boto3 API). For HTTP URLs, uses ``A2AClientToolProvider`` (A2A protocol).
    
    - Failed invocations are **not** retried automatically.

    Args:
        agent_name: Name of the owning agent (for logging).
        agent_config: Agent configuration dict with ``external_agent_configs``
                      and/or ``runtime_arn``.

    Returns:
        List of tool functions for the Strands agent.
    """
    # ── Pattern 1: Agent-level runtime_arn (set via UI "Runtime ARN" field) ──
    agent_runtime_arn = agent_config.get("runtime_arn", "")
    if agent_runtime_arn and agent_runtime_arn.startswith("arn:aws:bedrock-agentcore"):
        logger.info(
            "🔗 A2A_TOOLS: Agent-level runtime_arn found for %s: %s",
            agent_name,
            agent_runtime_arn[:80],
        )
        return _build_agentcore_invoke_tools(agent_name, [{
            "arn": agent_runtime_arn,
            "agent_name": agent_name,
            "name": f"{agent_name}Runtime",
            "description": agent_config.get("agent_description", f"Invoke {agent_name} runtime"),
        }])

    # ── Pattern 2 & 3: external_agent_configs ──
    external_configs = agent_config.get("external_agent_configs", [])
    if not external_configs:
        return []

    providers: List = []
    token_manager = None

    for entry in external_configs:
        if not entry.get("isA2A", False) or not entry.get("enabled", False):
            continue

        entry_name = entry.get("name", "unknown")
        arn = entry.get("arn", "")
        if not arn:
            logger.warning(
                "⚠️ A2A_TOOLS: Skipping entry '%s' for %s — missing ARN",
                entry_name,
                agent_name,
            )
            continue

        auth_type = entry.get("authType", "none")

        try:
            # Base httpx client args with 120-second timeout (Req 9.1)
            httpx_args: Dict[str, Any] = {
                "timeout": A2A_REQUEST_TIMEOUT_SECONDS,
            }

            if auth_type == "oauth":
                oauth_creds = entry.get("oauthCredentials", {})
                if oauth_creds.get("hasCredentials") and oauth_creds.get("ssmPath"):
                    if token_manager is None:
                        token_manager = A2ATokenManager()

                    pool_id = entry.get(
                        "cognitoPoolId", os.environ.get("A2A_POOL_ID", "")
                    )
                    client_id = entry.get(
                        "cognitoClientId", os.environ.get("A2A_CLIENT_ID", "")
                    )
                    token, err = token_manager.get_bearer_token(
                        oauth_creds["ssmPath"], pool_id, client_id
                    )
                    if err:
                        # Auth error — log without credential details
                        logger.error(
                            "❌ A2A_TOOLS: OAuth token acquisition failed for '%s' "
                            "(agent=%s)",
                            entry_name,
                            agent_name,
                        )
                        continue

                    httpx_args["headers"] = {
                        "Authorization": f"Bearer {token}"
                    }
                else:
                    logger.warning(
                        "⚠️ A2A_TOOLS: OAuth configured but no credentials for '%s'",
                        entry_name,
                    )
                    continue

            # Create the provider with timeout-enabled httpx args
            provider = A2AClientToolProvider(
                known_agent_urls=[arn],
                httpx_client_args=httpx_args,
            )

            providers.append(provider)
            logger.info(
                "✅ A2A_TOOLS: Created provider for '%s' "
                "(auth=%s) targeting %s",
                entry_name,
                auth_type,
                arn,
            )

        except Exception as e:
            # Catch-all: log at ERROR with agent name and endpoint,
            # but sanitize the message to avoid leaking credentials (Req 9.2, 9.4)
            safe_msg = _sanitize_error_message(e)
            logger.error(
                "❌ A2A_TOOLS: Failed to create provider for '%s' "
                "(agent=%s, endpoint=%s): %s",
                entry_name,
                agent_name,
                arn,
                safe_msg,
            )
            # Continue processing remaining entries — do not fail the whole list

    # Handle entries with runtime_arn (e.g. AAMPSellerCrewAgent)
    # These use a different schema than the isA2A entries above:
    #   { "agent_name": "...", "runtime_arn": "...", "payload_template": {...} }
    for entry in external_configs:
        # Skip entries already handled by the isA2A path
        if entry.get("isA2A"):
            continue

        runtime_arn = entry.get("runtime_arn")
        if not runtime_arn:
            continue

        entry_name = entry.get("agent_name", entry.get("name", "unknown"))

        try:
            httpx_args: Dict[str, Any] = {
                "timeout": A2A_REQUEST_TIMEOUT_SECONDS,
            }

            provider = A2AClientToolProvider(
                known_agent_urls=[runtime_arn],
                httpx_client_args=httpx_args,
            )

            providers.append(provider)
            logger.info(
                "✅ A2A_TOOLS: Created runtime provider for '%s' targeting %s",
                entry_name,
                runtime_arn,
            )
        except Exception as e:
            safe_msg = _sanitize_error_message(e)
            logger.error(
                "❌ A2A_TOOLS: Failed to create runtime provider for '%s' "
                "(agent=%s, endpoint=%s): %s",
                entry_name,
                agent_name,
                runtime_arn,
                safe_msg,
            )

    if providers:
        logger.info(
            "🔗 A2A_TOOLS: Built %d A2A client tool provider(s) for %s",
            len(providers),
            agent_name,
        )

    # Check if any entries use AgentCore ARNs (not HTTP URLs).
    # A2AClientToolProvider expects HTTP URLs for A2A protocol discovery.
    # AgentCore runtimes use ARNs and must be invoked via the
    # bedrock-agentcore InvokeAgentRuntime API instead.
    has_agentcore_arns = any(
        (entry.get("arn", "") or entry.get("runtime_arn", "")).startswith("arn:aws:bedrock-agentcore")
        for entry in external_configs
    )

    if has_agentcore_arns:
        # Skip A2A HTTP tools — they'll fail against AgentCore ARNs.
        # Use direct InvokeAgentRuntime instead.
        logger.info(
            "🔗 A2A_TOOLS: AgentCore ARNs detected — using direct invoke instead of A2A HTTP protocol"
        )
        agentcore_tools = _build_agentcore_invoke_tools(agent_name, external_configs)
        return agentcore_tools

    # For non-AgentCore entries (real HTTP URLs), extract tools from providers
    all_tools = []
    for provider in providers:
        try:
            provider_tools = provider.tools
            if provider_tools:
                all_tools.extend(provider_tools)
                logger.info(
                    "🔗 A2A_TOOLS: Extracted %d tool(s) from provider for %s",
                    len(provider_tools),
                    agent_name,
                )
            else:
                logger.warning(
                    "⚠️ A2A_TOOLS: Provider returned no tools for %s",
                    agent_name,
                )
        except Exception as e:
            safe_msg = _sanitize_error_message(e)
            logger.error(
                "❌ A2A_TOOLS: Failed to extract tools from provider for %s: %s",
                agent_name,
                safe_msg,
            )

    return all_tools


def _build_agentcore_invoke_tools(agent_name: str, external_configs: list) -> List:
    """Build @tool functions that invoke AgentCore runtimes via boto3.

    When A2AClientToolProvider fails (because AgentCore ARNs are not HTTP URLs),
    this creates simple tool functions that call the runtime via the
    bedrock-agentcore InvokeAgentRuntime API.

    Each external agent config with an ARN gets a dedicated tool function.
    """
    from strands import tool as strands_tool

    tools = []

    for entry in external_configs:
        arn = entry.get("arn", entry.get("runtime_arn", ""))
        if not arn or not arn.startswith("arn:aws:bedrock-agentcore"):
            continue

        entry_name = entry.get("name", entry.get("agent_name", "seller_agent"))
        # Create a clean tool name from the entry name
        tool_name = f"invoke_{entry_name.lower().replace(' ', '_').replace('-', '_')}"
        description = entry.get("description", f"Invoke the {entry_name} runtime")
        region = entry.get("awsAuth", {}).get("region", os.environ.get("AWS_REGION", "us-west-2"))

        # Capture variables in closure
        _arn = arn
        _region = region
        _entry_name = entry_name

        @strands_tool(name=tool_name, description=description)
        def _invoke_runtime(prompt: str) -> str:
            """Forward a request to the external agent runtime and return its response.

            Args:
                prompt: The request to send to the agent runtime.
            """
            import boto3
            import json as _json

            try:
                client = boto3.client("bedrock-agentcore", region_name=_region)
                payload = _json.dumps({
                    "prompt": prompt,
                    "routing_mode": "crew",
                }).encode("utf-8")

                logger.info(
                    "🔗 AGENTCORE_INVOKE: Calling %s at %s",
                    _entry_name,
                    _arn[:80],
                )

                response = client.invoke_agent_runtime(
                    agentRuntimeArn=_arn,
                    payload=payload,
                    contentType="application/json",
                    accept="application/json",
                )

                response_body = response.get("body", b"")
                if hasattr(response_body, "read"):
                    response_body = response_body.read()
                if isinstance(response_body, bytes):
                    response_body = response_body.decode("utf-8")

                # Parse the response to extract the actual content
                try:
                    parsed = _json.loads(response_body)
                    content = parsed.get("response", response_body)
                    return str(content)
                except _json.JSONDecodeError:
                    return response_body

            except Exception as e:
                safe_msg = _sanitize_error_message(e)
                logger.error(
                    "❌ AGENTCORE_INVOKE: Failed to invoke %s: %s",
                    _entry_name,
                    safe_msg,
                )
                return f"Error invoking {_entry_name}: {safe_msg}"

        tools.append(_invoke_runtime)
        logger.info(
            "✅ AGENTCORE_INVOKE: Created direct invoke tool '%s' for %s → %s",
            tool_name,
            agent_name,
            arn[:80],
        )

    return tools
