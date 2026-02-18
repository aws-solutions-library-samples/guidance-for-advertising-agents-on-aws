"""
External Agent Tool Registry

This module provides functionality for AgentCore agents to invoke other AgentCore agents as tools.
It handles tool registration, runtime ARN resolution, and agent-to-agent invocations.

Usage:
    # Initialize the registry
    registry = ExternalAgentToolRegistry(agent_config)

    # Register external agent tools from configuration
    registry.register_tools_from_config()

    # Get tool definitions for agent setup
    tool_definitions = registry.get_tool_definitions()

    # Invoke an external agent
    result = registry.invoke_external_agent("audience_strategy_analyzer", "Analyze audience for campaign")
"""

import json
import logging
from typing import List, Dict, Any, Optional
from pathlib import Path

try:
    from bedrock_agentcore.runtime import AgentCoreRuntimeClient
except ImportError:
    # Fallback for development/testing
    AgentCoreRuntimeClient = None
DEFAULT_TIMEOUT = 600  # set request timeout to 10 minutes


class ExternalAgentToolRegistry:
    """Registry for managing external agent tools and their invocations."""

    def __init__(
        self,
        agent_config: Dict[str, Any],
        stack_prefix: Optional[str] = None,
        unique_id: Optional[str] = None,
        logger: Optional[logging.Logger] = None,
    ):
        """Initialize the external agent tool registry.

        Args:
            agent_config: The agent configuration dictionary
            stack_prefix: The stack prefix for runtime ARN resolution
            unique_id: The unique identifier for the stack
            logger: Optional logger instance
        """
        self.agent_config = agent_config
        self.stack_prefix = stack_prefix
        self.unique_id = unique_id
        self.logger = logger or logging.getLogger(__name__)

        # Registry of external agent tools
        self.registered_tools: Dict[str, Dict[str, Any]] = {}

        # Runtime client for agent invocations
        self.runtime_client = None
        if AgentCoreRuntimeClient:
            try:
                self.runtime_client = AgentCoreRuntimeClient()
                self.logger.debug("Initialized AgentCore Runtime client")
            except Exception as e:
                self.logger.warning(
                    f"Failed to initialize AgentCore Runtime client: {e}"
                )

        # Runtime ARN resolver
        try:
            from .runtime_resolver import RuntimeARNResolver
        except ImportError:
            from runtime_resolver import RuntimeARNResolver

        self.runtime_resolver = RuntimeARNResolver(
            stack_prefix=stack_prefix, unique_id=unique_id, logger=logger
        )

    def register_tools_from_config(self) -> None:
        """Register external agent tools from the agent configuration."""
        external_tools = self.agent_config.get("external_agent_tools", [])

        if not external_tools:
            self.logger.debug("No external agent tools configured")
            return

        self.logger.info(f"Registering {len(external_tools)} external agent tools")

        for tool_name in external_tools:
            try:
                self._register_external_agent_tool(tool_name)
            except Exception as e:
                self.logger.error(
                    f"Failed to register external agent tool '{tool_name}': {e}"
                )
                continue

    def _register_external_agent_tool(self, agent_name: str) -> None:
        """Register a single external agent tool.

        Args:
            agent_name: The name of the external agent to register as a tool
        """
        # Resolve runtime ARN for the agent
        runtime_arn = self.runtime_resolver.resolve_runtime_arn(agent_name)

        if not runtime_arn:
            self.logger.warning(
                f"Could not resolve runtime ARN for agent '{agent_name}', skipping tool registration"
            )
            return

        # Generate tool name and description
        tool_name = f"invoke_{agent_name}"
        tool_description = f"Invoke the {agent_name.replace('_', ' ').title()} agent"

        # Create tool definition
        tool_definition = {
            "agent_name": agent_name,
            "runtime_arn": runtime_arn,
            "tool_name": tool_name,
            "description": tool_description,
            "parameters": {
                "query": {
                    "type": "string",
                    "description": "Query or request to send to the agent",
                    "required": True,
                },
                "context": {
                    "type": "string",
                    "description": "Additional context for the agent (optional)",
                    "required": False,
                },
            },
        }

        self.registered_tools[agent_name] = tool_definition
        self.logger.debug(
            f"Registered external agent tool: {tool_name} -> {runtime_arn}"
        )

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """Get tool definitions for all registered external agent tools.

        Returns:
            List of tool definitions suitable for agent tool registration
        """
        tool_definitions = []

        for agent_name, tool_config in self.registered_tools.items():
            tool_def = {
                "name": tool_config["tool_name"],
                "description": tool_config["description"],
                "parameters": {
                    "type": "object",
                    "properties": tool_config["parameters"],
                    "required": [
                        param_name
                        for param_name, param_config in tool_config[
                            "parameters"
                        ].items()
                        if param_config.get("required", False)
                    ],
                },
            }
            tool_definitions.append(tool_def)

        self.logger.debug(f"Generated {len(tool_definitions)} tool definitions")
        return tool_definitions

    def invoke_external_agent(
        self,
        agent_name: str,
        query: str,
        context: Optional[str] = None,
        timeout: int = 600,
    ) -> Dict[str, Any]:
        """Invoke an external agent tool.

        Args:
            agent_name: The name of the external agent to invoke
            query: The query or request to send to the agent
            context: Optional additional context for the agent
            timeout: Timeout for the invocation in seconds

        Returns:
            Dictionary containing the agent's response and metadata
        """
        if agent_name not in self.registered_tools:
            error_msg = f"External agent tool '{agent_name}' is not registered"
            self.logger.error(error_msg)
            return {"success": False, "error": error_msg, "response": None}

        if not self.runtime_client:
            error_msg = "AgentCore Runtime client is not available"
            self.logger.error(error_msg)
            return {"success": False, "error": error_msg, "response": None}

        tool_config = self.registered_tools[agent_name]
        runtime_arn = tool_config["runtime_arn"]

        # Prepare the invocation request
        request_payload = {"query": query}

        if context:
            request_payload["context"] = context

        self.logger.info(
            f"Invoking external agent '{agent_name}' with runtime ARN: {runtime_arn}"
        )

        try:
            # Invoke the external agent using AgentCore Runtime SDK
            response = self.runtime_client.invoke_agent_runtime(
                runtime_arn=runtime_arn,
                input_text=json.dumps(request_payload),
                timeout=timeout,
            )

            self.logger.info(f"Successfully invoked external agent '{agent_name}'")

            return {
                "success": True,
                "error": None,
                "response": response,
                "agent_name": agent_name,
                "runtime_arn": runtime_arn,
            }

        except Exception as e:
            error_msg = f"Failed to invoke external agent '{agent_name}': {str(e)}"
            self.logger.error(error_msg, exc_info=True)

            return {
                "success": False,
                "error": error_msg,
                "response": None,
                "agent_name": agent_name,
                "runtime_arn": runtime_arn,
            }

    def handle_tool_invocation(
        self, tool_name: str, parameters: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle a tool invocation request from the agent framework.

        This method is called when the agent framework invokes one of the registered tools.

        Args:
            tool_name: The name of the tool being invoked
            parameters: The parameters passed to the tool

        Returns:
            Dictionary containing the tool invocation result
        """
        # Find the agent name from the tool name
        agent_name = None
        for registered_agent, tool_config in self.registered_tools.items():
            if tool_config["tool_name"] == tool_name:
                agent_name = registered_agent
                break

        if not agent_name:
            error_msg = f"Unknown tool: {tool_name}"
            self.logger.error(error_msg)
            return {"success": False, "error": error_msg, "response": None}

        # Extract parameters
        query = parameters.get("query", "")
        context = parameters.get("context")

        if not query:
            error_msg = "Missing required parameter: query"
            self.logger.error(error_msg)
            return {"success": False, "error": error_msg, "response": None}

        # Invoke the external agent
        return self.invoke_external_agent(agent_name, query, context)

    def get_registered_tools(self) -> Dict[str, Dict[str, Any]]:
        """Get all registered external agent tools.

        Returns:
            Dictionary of registered tools with their configurations
        """
        return self.registered_tools.copy()

    def is_tool_registered(self, agent_name: str) -> bool:
        """Check if an external agent tool is registered.

        Args:
            agent_name: The name of the agent to check

        Returns:
            True if the tool is registered, False otherwise
        """
        return agent_name in self.registered_tools

    def validate_tool_configuration(self) -> Dict[str, Any]:
        """Validate the external agent tool configuration.

        Returns:
            Dictionary containing validation results
        """
        validation_results = {
            "valid": True,
            "errors": [],
            "warnings": [],
            "registered_tools": len(self.registered_tools),
            "runtime_client_available": self.runtime_client is not None,
        }

        # Check if runtime client is available
        if not self.runtime_client:
            validation_results["warnings"].append(
                "AgentCore Runtime client is not available"
            )

        # Validate each registered tool
        for agent_name, tool_config in self.registered_tools.items():
            try:
                # Check runtime ARN format
                runtime_arn = tool_config.get("runtime_arn")
                if not runtime_arn or not runtime_arn.startswith(
                    "arn:aws:bedrock-agentcore:"
                ):
                    validation_results["errors"].append(
                        f"Invalid runtime ARN for agent '{agent_name}': {runtime_arn}"
                    )
                    validation_results["valid"] = False

                # Check tool definition completeness
                required_fields = ["tool_name", "description", "parameters"]
                for field in required_fields:
                    if field not in tool_config:
                        validation_results["errors"].append(
                            f"Missing required field '{field}' for agent '{agent_name}'"
                        )
                        validation_results["valid"] = False

            except Exception as e:
                validation_results["errors"].append(
                    f"Validation error for agent '{agent_name}': {str(e)}"
                )
                validation_results["valid"] = False

        return validation_results


class ExternalAgentInvoker:
    """Simplified interface for invoking external agents."""

    def __init__(self, registry: ExternalAgentToolRegistry):
        """Initialize the invoker with a tool registry.

        Args:
            registry: The ExternalAgentToolRegistry instance
        """
        self.registry = registry
        self.logger = registry.logger

    def invoke(
        self, agent_name: str, message: str, context: Optional[str] = None
    ) -> str:
        """Invoke an external agent and return the response as a string.

        Args:
            agent_name: The name of the external agent to invoke
            message: The message to send to the agent
            context: Optional additional context

        Returns:
            The agent's response as a string, or an error message
        """
        result = self.registry.invoke_external_agent(agent_name, message, context)

        if result["success"]:
            response = result.get("response", "")
            if isinstance(response, dict):
                return json.dumps(response, indent=2)
            return str(response)
        else:
            error_msg = result.get("error", "Unknown error occurred")
            self.logger.error(f"External agent invocation failed: {error_msg}")
            return f"Error invoking {agent_name}: {error_msg}"

    def is_available(self, agent_name: str) -> bool:
        """Check if an external agent is available for invocation.

        Args:
            agent_name: The name of the agent to check

        Returns:
            True if the agent is available, False otherwise
        """
        return self.registry.is_tool_registered(agent_name)

    def list_available_agents(self) -> List[str]:
        """Get a list of available external agents.

        Returns:
            List of agent names that can be invoked
        """
        return list(self.registry.get_registered_tools().keys())
