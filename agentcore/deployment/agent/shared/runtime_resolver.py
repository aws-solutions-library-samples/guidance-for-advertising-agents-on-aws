"""
Runtime ARN Resolution Service

This module provides functionality to resolve agent names to runtime ARNs using stack naming conventions
and runtime registry files. It supports fallback mechanisms for runtime discovery.

Usage:
    resolver = RuntimeARNResolver(stack_prefix="sim", unique_id="abc123")
    runtime_arn = resolver.resolve_runtime_arn("WeatherImpactAgent")
    
    # Get all runtimes in the stack
    runtimes = resolver.list_stack_runtimes()
"""

import json
import logging
import glob
from typing import Dict, List, Optional, Any
from pathlib import Path
from urllib.parse import urlparse, urlencode, quote, unquote


class RuntimeARNResolver:
    """Service for resolving agent names to runtime ARNs using stack naming conventions."""
    
    def __init__(
        self,
        stack_prefix: Optional[str] = None,
        unique_id: Optional[str] = None,
        logger: Optional[logging.Logger] = None
    ):
        """Initialize the runtime ARN resolver.
        
        Args:
            stack_prefix: The stack prefix (e.g., 'sim', 'demo3')
            unique_id: The unique identifier for the stack
            logger: Optional logger instance
        """
        self.stack_prefix = stack_prefix
        self.unique_id = unique_id
        self.logger = logger or logging.getLogger(__name__)
        
        # Cache for runtime registry data
        
    def resolve_runtime_endpoint(self, agent_name) -> Optional[str]:
        """Resolve an agent name to its runtime endpoint.
        
        Args:
            agent_name: The agent name (e.g., 'WeatherImpactAgent', 'audience-strategy-analyzer')
            
        Returns:
            The runtime url if found, None otherwise
        """
        runtime_endpoint = self._resolve_endpoint(agent_name)
        if runtime_endpoint:
            return runtime_endpoint
        
        self.logger.warning(f"Could not resolve runtime endpoint for agent '{agent_name}'")
        return None
    
    def resolve_runtime_arn(self, agent_name, runtimes) -> Optional[str]:
        """Resolve an agent name to its runtime arn.
        
        Args:
            agent_name: The agent name (e.g., 'WeatherImpactAgent', 'audience-strategy-analyzer')
            
        Returns:
            The runtime arn if found, None otherwise
        """
        runtime_arn = self._resolve_arn(agent_name, runtimes)
        if runtime_arn:
            return runtime_arn
        
        self.logger.warning(f"Could not resolve runtime ARN for agent '{agent_name}'")
        return None
    
    def resolve_bearer_token(self, agent_name, runtimes) -> Optional[str]:
        """Resolve an agent name to its bearer token.
        
        Args:
            agent_name: The agent name (e.g., 'WeatherImpactAgent', 'audience-strategy-analyzer')
            runtimes: List of runtime entries from environment
            
        Returns:
            The bearer token if found, None otherwise
        """
        bearer_token = self._resolve_token_from_environment(agent_name, runtimes)
        if bearer_token:
            return bearer_token
        
        self.logger.warning(f"Could not resolve bearer token for agent '{agent_name}'")
        return None
    
    def _resolve_endpoint(self, agent_name: str) -> Optional[str]:
        """Try fallback mechanisms for runtime discovery within the same stack.
        
        Args:
            agent_name: The agent name to resolve
            
        Returns:
            The runtime endpoint if found, None otherwise
        """
        # First try to use environment variables (faster, no API calls)
        runtime_arn = self._resolve_from_environment(agent_name)
        if runtime_arn:
            region_name = 'us-east-1'
            encoded_arn = quote(runtime_arn, safe='')
            endpoint_url = f'https://bedrock-agentcore.{region_name}.amazonaws.com/runtimes/{encoded_arn}/invocations'
            self.logger.info(f"Resolved endpoint from environment: {endpoint_url}")
            return endpoint_url
        
        # Only try fallbacks within the same stack to maintain proper isolation
        if self.stack_prefix and self.unique_id:
            # Use boto3 AgentCore SDK to list agent runtimes
            try:
                import boto3
                region_name = 'us-east-1'
                agentcore_client = boto3.client('bedrock-agentcore-control', region_name=region_name)
                
                # List all agent runtimes
                response = agentcore_client.list_agent_runtimes(maxResults = 100)
                runtimes = response.get('agentRuntimes', [])
                endpoint_url = ''
                # Find runtime matching our criteria
                for runtime in runtimes:
                    runtime_name = runtime.get('agentRuntimeName', '')
                    runtime_arn = runtime.get('agentRuntimeArn', '')
                    
                    # Check if runtime name contains all required components
                    agent_name_normalized = agent_name.replace("-", "_").lower()
                    runtime_name_normalized = runtime_name.replace("-", "_").lower()
                    self.logger.warning(f'checking {runtime_name_normalized} for {agent_name_normalized}')
                    if (agent_name_normalized in runtime_name_normalized or agent_name in runtime_name and
                        self.stack_prefix.lower() in runtime_name_normalized and
                        self.unique_id.lower() in runtime_name_normalized):                        
                        self.logger.warning(f"Found matching runtime '{runtime_name}' for agent '{agent_name}' with ARN: {runtime_arn}")
                        encoded_arn = quote(runtime_arn, safe='') # URL-encode the ARN
                        endpoint_url = f'https://bedrock-agentcore.{region_name}.amazonaws.com/runtimes/{encoded_arn}/invocations'
                        self.logger.warning(f"Endpoint: {endpoint_url}")
                        return endpoint_url
            except Exception as e:
                self.logger.error(f"Failed to list agent runtimes via boto3: {e}")
                # Fall back to registry-based lookup if boto3 fails
                return None
        else:
            # If no stack info provided, use the original fallback method
            return self._resolve_without_stack_info(agent_name)
        
        return None
    
    def _resolve_arn(self, agent_name: str, runtimes) -> Optional[str]:
        """Try fallback mechanisms for runtime discovery within the same stack.
        
        Args:
            agent_name: The agent name to resolve
            
        Returns:
            The runtime ARN if found, None otherwise
        """
        # First try to use environment variables (faster, no API calls)
        runtime_arn = self._resolve_from_environment(agent_name, runtimes)
        if runtime_arn:
            self.logger.info(f"Resolved ARN from environment: {runtime_arn}")
            return runtime_arn
        
        # Only try fallbacks within the same stack to maintain proper isolation
        if self.stack_prefix and self.unique_id:
            # Use boto3 AgentCore SDK to list agent runtimes
            try:
                import boto3
                region_name = 'us-east-1'
                agentcore_client = boto3.client('bedrock-agentcore-control', region_name=region_name)
                
                # List all agent runtimes
                response = agentcore_client.list_agent_runtimes(maxResults = 100)
                runtimes = response.get('agentRuntimes', [])
                endpoint_url = ''
                # Find runtime matching our criteria
                for runtime in runtimes:
                    runtime_name = runtime.get('agentRuntimeName', '')
                    runtime_arn = runtime.get('agentRuntimeArn', '')
                    
                    # Check if runtime name contains all required components
                    agent_name_normalized = agent_name.replace("-", "_").lower()
                    runtime_name_normalized = runtime_name.replace("-", "_").lower()
                    self.logger.warning(f'checking {runtime_name_normalized} for {agent_name_normalized}')
                    if (agent_name_normalized in runtime_name_normalized or agent_name in runtime_name and
                        self.stack_prefix.lower() in runtime_name_normalized and
                        self.unique_id.lower() in runtime_name_normalized):                        
                        self.logger.warning(f"Found matching runtime '{runtime_name}' for agent '{agent_name}' with ARN: {runtime_arn}")
                        return runtime_arn
            except Exception as e:
                self.logger.error(f"Failed to list agent runtimes via boto3: {e}")
                # Fall back to registry-based lookup if boto3 fails
                return None
        else:
            return None 
        return None
    
    def _resolve_without_stack_info(self, agent_name: str) -> Optional[str]:
        """Resolve runtime ARN without stack prefix/unique_id information.
        
        Args:
            agent_name: The agent name to resolve
            
        Returns:
            The runtime ARN if found, None otherwise
        """
        # Search all registry files for the agent
        for registry_file, registry_data in self._registry_cache.items():
            deployed_agents = registry_data.get("deployed_agents", [])
            
            for agent_info in deployed_agents:
                agent_runtime_name = agent_info.get("name", "")
                
                # Check if agent name appears in the runtime name
                if agent_name.replace("-", "_") in agent_runtime_name or agent_name in agent_runtime_name:
                    runtime_arn = agent_info.get("runtime_arn")
                    if runtime_arn:
                        self.logger.debug(f"No-stack-info resolved '{agent_name}' to runtime ARN: {runtime_arn}")
                        return runtime_arn
        
        return None
    
    def _resolve_from_environment(self, agent_name: str, runtimes_env) -> Optional[str]:
        import re
        """Resolve runtime ARN from environment variables (faster than API calls).
        
        Args:
            agent_name: The agent name to resolve
            
        Returns:
            The runtime ARN if found in environment variables, None otherwise
        """
        import os
        
        self.logger.debug(f"RUNTIMES environment variable: {runtimes_env}")
        agent_name_normalized = agent_name.replace("-", "_").lower()
        
        for runtime_arn in runtimes_env:
            runtime_name_normalized = runtime_arn.replace("-", "_").lower()
            self.logger.info(f'Comparing {runtime_arn} against {agent_name}')
            # Check if the agent name appears in the runtime name
            if agent_name_normalized in runtime_name_normalized:
                self.logger.info(f"Found matching runtime ARN in environment: {runtime_arn}")
                return runtime_arn
                
        self.logger.debug(f"No matching runtime ARN found in environment for agent: {agent_name}")
        return None

    def _resolve_token_from_environment(self, agent_name: str, runtimes_env) -> Optional[str]:
        import re
        """Resolve runtime ARN from environment variables (faster than API calls).
        
        Args:
            agent_name: The agent name to resolve
            
        Returns:
            The runtime ARN if found in environment variables, None otherwise
        """
        import os
        
        self.logger.debug(f"RUNTIMES environment variable: {runtimes_env}")
        agent_name_normalized = agent_name.replace("-", "_").lower()
        
        for runtime_arn in runtimes_env:
            runtime_name_normalized = runtime_arn.replace("-", "_").lower()
            self.logger.info(f'Comparing {runtime_arn} against {agent_name}')
            # Check if the agent name appears in the runtime name
            if agent_name_normalized in runtime_name_normalized and '|' in runtime_arn:
                self.logger.info(f"Found matching runtime ARN in environment, need to pull bearer token: {runtime_arn}")
                return runtime_arn.split('|')[1]
                
        self.logger.debug(f"No matching runtime ARN found in environment for agent: {agent_name}")
        return None

    def _generate_runtime_name(self, agent_name: str) -> str:
        """Generate expected runtime name using stack naming convention.
        
        Args:
            agent_name: The agent name
            
        Returns:
            Expected runtime name in format: {stack_prefix}-{agent_name}-{unique_id}
        """
        if not self.stack_prefix or not self.unique_id:
            return agent_name
        
        return f"{self.stack_prefix}-{agent_name}-{self.unique_id}"
    
    def list_stack_runtimes(self) -> List[Dict[str, Any]]:
        """Get all runtimes in the current stack.
        
        Returns:
            List of runtime information dictionaries
        """
        self._load_registry_cache()
        
        if not self.stack_prefix or not self.unique_id:
            # Return all runtimes if no stack info
            all_runtimes = []
            for registry_data in self._registry_cache.values():
                all_runtimes.extend(registry_data.get("deployed_agents", []))
            return all_runtimes
        
        # Find the specific registry for this stack
        target_registry_pattern = f".agentcore-agents-{self.stack_prefix}-{self.unique_id}.json"
        
        for registry_file, registry_data in self._registry_cache.items():
            if target_registry_pattern in registry_file:
                return registry_data.get("deployed_agents", [])
        
        self.logger.warning(f"No registry found for stack {self.stack_prefix}-{self.unique_id}")
        return []
        
    def validate_runtime_access(self, agent_name: str) -> Dict[str, Any]:
        """Validate that a runtime is accessible.
        
        Args:
            agent_name: The agent name to validate
            
        Returns:
            Dictionary with validation results
        """
        validation_result = {
            "agent_name": agent_name,
            "accessible": False,
            "runtime_arn": None,
            "runtime_info": None,
            "error": None
        }
        
        try:
            # Try to resolve runtime ARN
            runtime_arn = self.resolve_runtime_endpoint(agent_name)
            
            if runtime_arn:
                validation_result["accessible"] = True
                validation_result["runtime_arn"] = runtime_arn
                validation_result["runtime_info"] = self.get_runtime_info(agent_name)
            else:
                validation_result["error"] = f"Could not resolve runtime ARN for agent '{agent_name}'"
            
        except Exception as e:
            validation_result["error"] = f"Validation error: {str(e)}"
        
        return validation_result
    
    def get_stack_info(self) -> Dict[str, Any]:
        """Get information about the current stack.
        
        Returns:
            Dictionary with stack information
        """
        return {
            "stack_prefix": self.stack_prefix,
            "unique_id": self.unique_id,
            "registry_files_loaded": len(self._registry_cache),
            "total_runtimes": sum(
                len(registry_data.get("deployed_agents", []))
                for registry_data in self._registry_cache.values()
            )
        }