"""
External Tool Integration Template

This module provides a template and utilities for integrating external agent tools
into AgentCore agent handlers. It handles tool registration, invocation, and error handling.

Usage in agent handlers:
    from shared.external_tool_integration import ExternalToolIntegration
    
    # Initialize external tool integration
    external_tools = ExternalToolIntegration(config, logger)
    
    # Register tools from configuration
    external_tools.register_tools_from_config()
    
    # Create tool functions for agent
    tool_functions = external_tools.create_tool_functions()
    
    # Add to agent tools list
    agent_tools = [existing_tools] + tool_functions
"""

import json
import logging
import os
from typing import List, Dict, Any, Optional, Callable
from functools import wraps

try:
    from .external_agent_tools import ExternalAgentToolRegistry, ExternalAgentInvoker
    from .runtime_resolver import RuntimeARNResolver
except ImportError:
    from external_agent_tools import ExternalAgentToolRegistry, ExternalAgentInvoker
    from runtime_resolver import RuntimeARNResolver


class ExternalToolIntegration:
    """Template class for integrating external agent tools into agent handlers."""
    
    def __init__(
        self,
        agent_config: Dict[str, Any],
        logger: Optional[logging.Logger] = None,
        stack_prefix: Optional[str] = None,
        unique_id: Optional[str] = None
    ):
        """Initialize external tool integration.
        
        Args:
            agent_config: The agent configuration dictionary
            logger: Optional logger instance
            stack_prefix: Stack prefix for runtime ARN resolution
            unique_id: Unique identifier for the stack
        """
        self.agent_config = agent_config
        self.logger = logger or logging.getLogger(__name__)
        
        # Extract stack information from environment or config
        self.stack_prefix = stack_prefix or os.environ.get("STACK_PREFIX")
        self.unique_id = unique_id or os.environ.get("UNIQUE_ID")
        
        # Initialize external agent tool registry
        self.tool_registry = ExternalAgentToolRegistry(
            agent_config=agent_config,
            stack_prefix=self.stack_prefix,
            unique_id=self.unique_id,
            logger=self.logger
        )
        
        # Initialize external agent invoker
        self.invoker = ExternalAgentInvoker(self.tool_registry)
        
        # Track registered tools
        self.registered_tools: Dict[str, Callable] = {}
        
        #self.logger.info(f"Initialized external tool integration for agent: {agent_config.get('name', 'unknown')}")
    
    def register_tools_from_config(self) -> None:
        """Register external agent tools from the agent configuration."""
        try:
            self.tool_registry.register_tools_from_config()
            
            # Log registration results
            registered_tools = self.tool_registry.get_registered_tools()
            if registered_tools:
                #self.logger.info(f"Successfully registered {len(registered_tools)} external agent tools:")
                for agent_name, tool_config in registered_tools.items():
                    self.logger.info(f"  - {tool_config['tool_name']} -> {agent_name}")
            else:
                self.logger.debug("No external agent tools to register")
                
        except Exception as e:
            self.logger.error(f"Failed to register external agent tools: {e}", exc_info=True)
    
    def create_tool_functions(self) -> List[Callable]:
        """Create tool functions for registered external agents.
        
        Returns:
            List of tool functions that can be added to an agent's tools
        """
        tool_functions = []
        registered_tools = self.tool_registry.get_registered_tools()
        
        for agent_name, tool_config in registered_tools.items():
            try:
                tool_function = self._create_external_agent_tool_function(agent_name, tool_config)
                tool_functions.append(tool_function)
                self.registered_tools[agent_name] = tool_function
                
                self.logger.debug(f"Created tool function for external agent: {agent_name}")
                
            except Exception as e:
                self.logger.error(f"Failed to create tool function for agent '{agent_name}': {e}")
                continue
        
        self.logger.info(f"Created {len(tool_functions)} external agent tool functions")
        return tool_functions
    
    def _create_external_agent_tool_function(self, agent_name: str, tool_config: Dict[str, Any]) -> Callable:
        """Create a tool function for a specific external agent.
        
        Args:
            agent_name: The name of the external agent
            tool_config: The tool configuration dictionary
            
        Returns:
            A callable tool function
        """
        tool_name = tool_config["tool_name"]
        tool_description = tool_config["description"]
        
        def external_agent_tool(query: str, context: str = None) -> str:
            """
            Dynamically created tool function for invoking external agents.
            
            Args:
                query: The query or request to send to the external agent
                context: Optional additional context for the agent
                
            Returns:
                The external agent's response as a string
            """
            try:
                self.logger.info(f"Invoking external agent tool: {tool_name}")
                self.logger.debug(f"Query: {query}")
                if context:
                    self.logger.debug(f"Context: {context}")
                
                # Invoke the external agent
                result = self.tool_registry.invoke_external_agent(agent_name, query, context)
                
                if result["success"]:
                    response = result.get("response", "")
                    
                    # Format the response appropriately
                    if isinstance(response, dict):
                        formatted_response = json.dumps(response, indent=2)
                    else:
                        formatted_response = str(response)
                    
                    self.logger.info(f"Successfully invoked external agent: {agent_name}")
                    return formatted_response
                    
                else:
                    error_msg = result.get("error", "Unknown error occurred")
                    self.logger.error(f"External agent invocation failed: {error_msg}")
                    return f"Error invoking {agent_name}: {error_msg}"
                    
            except Exception as e:
                error_msg = f"Exception during external agent invocation: {str(e)}"
                self.logger.error(error_msg, exc_info=True)
                return f"Error invoking {agent_name}: {error_msg}"
        
        # Set function metadata for tool registration
        external_agent_tool.__name__ = tool_name
        external_agent_tool.__doc__ = f"{tool_description}\n\nArgs:\n    query (str): Query or request to send to the agent\n    context (str, optional): Additional context for the agent\n\nReturns:\n    str: The agent's response"
        
        # Add tool decorator if available (for strands framework)
        try:
            from strands import tool
            external_agent_tool = tool(external_agent_tool)
        except ImportError:
            # If strands is not available, return the function as-is
            pass
        
        return external_agent_tool
    
    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """Get tool definitions for all registered external agent tools.
        
        Returns:
            List of tool definitions suitable for agent framework registration
        """
        return self.tool_registry.get_tool_definitions()
    
    def invoke_external_agent(self, agent_name: str, message: str, context: Optional[str] = None) -> str:
        """Directly invoke an external agent (convenience method).
        
        Args:
            agent_name: The name of the external agent to invoke
            message: The message to send to the agent
            context: Optional additional context
            
        Returns:
            The agent's response as a string
        """
        return self.invoker.invoke(agent_name, message, context)
    
    def is_external_agent_available(self, agent_name: str) -> bool:
        """Check if an external agent is available for invocation.
        
        Args:
            agent_name: The name of the agent to check
            
        Returns:
            True if the agent is available, False otherwise
        """
        return self.invoker.is_available(agent_name)
    
    def list_available_external_agents(self) -> List[str]:
        """Get a list of available external agents.
        
        Returns:
            List of agent names that can be invoked
        """
        return self.invoker.list_available_agents()
    
    def validate_configuration(self) -> Dict[str, Any]:
        """Validate the external tool configuration.
        
        Returns:
            Dictionary containing validation results
        """
        validation_results = self.tool_registry.validate_tool_configuration()
        
        # Add integration-specific validation
        validation_results["integration_info"] = {
            "stack_prefix": self.stack_prefix,
            "unique_id": self.unique_id,
            "agent_name": self.agent_config.get("name", "unknown"),
            "configured_external_tools": self.agent_config.get("external_agent_tools", []),
            "created_tool_functions": len(self.registered_tools)
        }
        
        return validation_results
    
    def get_integration_status(self) -> Dict[str, Any]:
        """Get the current status of external tool integration.
        
        Returns:
            Dictionary containing integration status information
        """
        registered_tools = self.tool_registry.get_registered_tools()
        
        status = {
            "initialized": True,
            "stack_prefix": self.stack_prefix,
            "unique_id": self.unique_id,
            "agent_name": self.agent_config.get("name", "unknown"),
            "configured_external_tools": self.agent_config.get("external_agent_tools", []),
            "registered_tools_count": len(registered_tools),
            "registered_tools": list(registered_tools.keys()),
            "created_tool_functions": len(self.registered_tools),
            "runtime_client_available": self.tool_registry.runtime_client is not None
        }
        
        return status


def create_external_tool_integration(
    agent_config: Dict[str, Any],
    logger: Optional[logging.Logger] = None,
    stack_prefix: Optional[str] = None,
    unique_id: Optional[str] = None
) -> ExternalToolIntegration:
    """Factory function to create external tool integration.
    
    Args:
        agent_config: The agent configuration dictionary
        logger: Optional logger instance
        stack_prefix: Stack prefix for runtime ARN resolution
        unique_id: Unique identifier for the stack
        
    Returns:
        Configured ExternalToolIntegration instance
    """
    integration = ExternalToolIntegration(
        agent_config=agent_config,
        logger=logger,
        stack_prefix=stack_prefix,
        unique_id=unique_id
    )
    
    # Automatically register tools from configuration
    integration.register_tools_from_config()
    
    return integration


def add_external_tools_to_agent_tools(
    existing_tools: List[Callable],
    agent_config: Dict[str, Any],
    logger: Optional[logging.Logger] = None,
    stack_prefix: Optional[str] = None,
    unique_id: Optional[str] = None
) -> List[Callable]:
    """Convenience function to add external agent tools to existing agent tools.
    
    Args:
        existing_tools: List of existing agent tools
        agent_config: The agent configuration dictionary
        logger: Optional logger instance
        stack_prefix: Stack prefix for runtime ARN resolution
        unique_id: Unique identifier for the stack
        
    Returns:
        Combined list of existing tools and external agent tools
    """
    try:
        # Create external tool integration
        integration = create_external_tool_integration(
            agent_config=agent_config,
            logger=logger,
            stack_prefix=stack_prefix,
            unique_id=unique_id
        )
        
        # Create external tool functions
        external_tool_functions = integration.create_tool_functions()
        
        # Combine with existing tools
        combined_tools = existing_tools + external_tool_functions
        
        #if logger:
        #    logger.info(f"Added {len(external_tool_functions)} external agent tools to {len(existing_tools)} existing tools")
        
        return combined_tools
        
    except Exception as e:
        if logger:
            logger.error(f"Failed to add external agent tools: {e}", exc_info=True)
        
        # Return existing tools if external tool integration fails
        return existing_tools


# Error handling decorators for external tool functions
def handle_external_tool_errors(func: Callable) -> Callable:
    """Decorator to handle errors in external tool functions.
    
    Args:
        func: The external tool function to wrap
        
    Returns:
        Wrapped function with error handling
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            error_msg = f"Error in external tool '{func.__name__}': {str(e)}"
            logging.getLogger(__name__).error(error_msg, exc_info=True)
            return f"Tool error: {error_msg}"
    
    return wrapper


def log_external_tool_invocation(func: Callable) -> Callable:
    """Decorator to log external tool invocations.
    
    Args:
        func: The external tool function to wrap
        
    Returns:
        Wrapped function with logging
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        logger = logging.getLogger(__name__)
        logger.info(f"Invoking external tool: {func.__name__}")
        logger.debug(f"Args: {args}, Kwargs: {kwargs}")
        
        result = func(*args, **kwargs)
        
        logger.info(f"External tool '{func.__name__}' completed successfully")
        return result
    
    return wrapper