"""
Programmatic visualization loader for AgentCore agents.
Loads visualization maps and template data from S3 or local filesystem.
"""

import os
import json
import boto3
import logging
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


class VisualizationLoader:
    """Load visualization configurations from S3 or local filesystem."""
    
    def __init__(self, base_dir: Optional[str] = None):
        """
        Initialize the visualization loader.
        
        Args:
            base_dir: Base directory for visualization library (local fallback). 
                     Defaults to agent-visualizations-library in the same directory as handler.py
        """
        if base_dir is None:
            # Default to the agent-visualizations-library directory
            handler_dir = os.path.dirname(os.path.dirname(__file__))
            self.base_dir = os.path.join(handler_dir, "agent-visualizations-library")
        else:
            self.base_dir = base_dir
            
        self.maps_dir = os.path.join(self.base_dir, "agent-visualization-maps")
        
        # S3 configuration
        self.s3_bucket = self._get_s3_config_bucket()
        self.s3_prefix = "configs/agent-visualizations-library"
        self._s3_client = None
    
    def _get_s3_config_bucket(self) -> Optional[str]:
        """Get the S3 bucket name for agent configurations."""
        stack_prefix = os.environ.get("STACK_PREFIX", "")
        unique_id = os.environ.get("UNIQUE_ID", "")
        if stack_prefix and unique_id:
            return f"{stack_prefix}-data-{unique_id}"
        return None
    
    def _get_s3_client(self):
        """Get or create S3 client."""
        if self._s3_client is None:
            self._s3_client = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-1"))
        return self._s3_client
    
    def _load_from_s3(self, key: str) -> Optional[str]:
        """Load a file from S3."""
        if not self.s3_bucket:
            return None
        try:
            s3 = self._get_s3_client()
            response = s3.get_object(Bucket=self.s3_bucket, Key=key)
            content = response["Body"].read().decode("utf-8")
            logger.info(f"✅ VIZ_LOADER: Loaded {key} from S3")
            return content
        except Exception as e:
            logger.debug(f"⚠️ VIZ_LOADER: Could not load {key} from S3: {e}")
            return None
    
    def _load_json_from_s3(self, key: str) -> Optional[dict]:
        """Load a JSON file from S3."""
        content = self._load_from_s3(key)
        if content:
            try:
                return json.loads(content)
            except json.JSONDecodeError as e:
                logger.error(f"❌ VIZ_LOADER: Invalid JSON in S3 key {key}: {e}")
        return None
    
    def load_agent_visualization_map(self, agent_name: str) -> Optional[Dict[str, Any]]:
        """
        Load the visualization map for a specific agent.
        
        Priority:
        1. S3: configs/agent-visualizations-library/agent-visualization-maps/{agent_name}.json
        2. Local filesystem
        
        Args:
            agent_name: Name of the agent (e.g., "AdLoadOptimizationAgent")
            
        Returns:
            Dictionary containing agent visualization map, or None if not found
        """
        # Try S3 first
        if self.s3_bucket:
            s3_key = f"{self.s3_prefix}/agent-visualization-maps/{agent_name}.json"
            data = self._load_json_from_s3(s3_key)
            if data:
                logger.info(f"✅ VIZ_LOADER: Loaded visualization map for {agent_name} from S3")
                return data
        
        # Fall back to local filesystem
        map_path = os.path.join(self.maps_dir, f"{agent_name}.json")
        
        if not os.path.exists(map_path):
            logger.debug(f"⚠️ VIZ_LOADER: No visualization map found for {agent_name}")
            return None
        
        try:
            with open(map_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"❌ VIZ_LOADER: Error loading visualization map for {agent_name}: {e}")
            return None
    
    def load_template_data(self, agent_name: str, template_id: str) -> Optional[Dict[str, Any]]:
        """
        Load the data mapping for a specific agent template.
        
        Priority:
        1. S3: configs/agent-visualizations-library/{agent_name}-{template_id}.json
        2. Local filesystem
        
        Args:
            agent_name: Name of the agent (e.g., "AdLoadOptimizationAgent")
            template_id: Template ID (e.g., "metrics-visualization")
            
        Returns:
            Dictionary containing the dataMapping field, or None if not found
        """
        # Try S3 first
        if self.s3_bucket:
            s3_key = f"{self.s3_prefix}/{agent_name}-{template_id}.json"
            data = self._load_json_from_s3(s3_key)
            if data:
                logger.info(f"✅ VIZ_LOADER: Loaded template {template_id} for {agent_name} from S3")
                return data.get("dataMapping")
        
        # Fall back to local filesystem
        template_path = os.path.join(self.base_dir, f"{agent_name}-{template_id}.json")
        
        if not os.path.exists(template_path):
            logger.debug(f"⚠️ VIZ_LOADER: No template data found for {agent_name}/{template_id}")
            return None
        
        try:
            with open(template_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get("dataMapping")
        except Exception as e:
            logger.error(f"❌ VIZ_LOADER: Error loading template data for {agent_name}/{template_id}: {e}")
            return None
    
    def load_generic_template(self, template_id: str) -> Optional[Dict[str, Any]]:
        """
        Load a generic visualization template (not agent-specific).
        
        Priority:
        1. S3: configs/agent-visualizations-library/generic-visualization-templates/{template_id}.json
        2. Local filesystem
        
        Args:
            template_id: Template ID (e.g., "adcp_get_products-visualization")
            
        Returns:
            Full template data dictionary, or None if not found
        """
        # Try S3 first
        if self.s3_bucket:
            s3_key = f"{self.s3_prefix}/generic-visualization-templates/{template_id}.json"
            data = self._load_json_from_s3(s3_key)
            if data:
                logger.info(f"✅ VIZ_LOADER: Loaded generic template {template_id} from S3")
                return data
        
        # Fall back to local filesystem
        template_path = os.path.join(self.base_dir, "generic-visualization-templates", f"{template_id}.json")
        
        if not os.path.exists(template_path):
            logger.debug(f"⚠️ VIZ_LOADER: No generic template found for {template_id}")
            return None
        
        try:
            with open(template_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"❌ VIZ_LOADER: Error loading generic template {template_id}: {e}")
            return None
    
    def load_all_templates_for_agent(self, agent_name: str) -> Dict[str, Dict[str, Any]]:
        """
        Load all visualization templates for a specific agent.
        
        Args:
            agent_name: Name of the agent
            
        Returns:
            Dictionary mapping template_id to dataMapping content
        """
        # First load the agent's visualization map
        viz_map = self.load_agent_visualization_map(agent_name)
        
        if not viz_map:
            return {}
        
        templates = viz_map.get("templates", [])
        result = {}
        
        for template in templates:
            template_id = template.get("templateId")
            if template_id:
                data_mapping = self.load_template_data(agent_name, template_id)
                if data_mapping:
                    result[template_id] = {
                        "usage": template.get("usage", ""),
                        "dataMapping": data_mapping
                    }
        
        return result
    
    def get_visualization_instructions(self, agent_name: str) -> str:
        """
        Generate instructions for an agent on how to use its visualizations.
        
        Args:
            agent_name: Name of the agent
            
        Returns:
            Formatted instruction string for the agent
        """
        viz_map = self.load_agent_visualization_map(agent_name)
        
        if not viz_map:
            return f"No visualizations configured for {agent_name}."
        
        templates = viz_map.get("templates", [])
        
        if not templates:
            return f"No visualization templates available for {agent_name}."
        
        instructions = [
            f"\n## Available Visualizations for {agent_name}\n",
            "You have access to the following visualization templates:\n"
        ]
        
        for template in templates:
            template_id = template.get("templateId")
            usage = template.get("usage", "No description")
            instructions.append(f"- **{template_id}**: {usage}")
        
        instructions.append("\n## How to Use Visualizations\n")
        instructions.append("1. Determine which template best fits your analysis")
        instructions.append("2. Load the template data mapping programmatically")
        instructions.append("3. Map your analysis results to the template fields")
        instructions.append("4. Wrap the result in XML: <visualization-data type='[template-id]'>[JSON_RESULT]</visualization-data>\n")
        
        return "\n".join(instructions)
    
    def get_template_structure(self, agent_name: str, template_id: str) -> Optional[str]:
        """
        Get a formatted string showing the structure of a template.
        
        Args:
            agent_name: Name of the agent
            template_id: Template ID
            
        Returns:
            Formatted JSON string showing template structure, or None if not found
        """
        data_mapping = self.load_template_data(agent_name, template_id)
        
        if not data_mapping:
            return None
        
        return json.dumps(data_mapping, indent=2)


# Convenience function for quick access
def load_visualizations_for_agent(agent_name: str) -> Dict[str, Dict[str, Any]]:
    """
    Quick helper to load all visualizations for an agent.
    
    Args:
        agent_name: Name of the agent
        
    Returns:
        Dictionary mapping template_id to template data
    """
    loader = VisualizationLoader()
    return loader.load_all_templates_for_agent(agent_name)


def get_visualization_prompt_addition(agent_name: str) -> str:
    """
    Get prompt addition text for visualization instructions.
    
    Args:
        agent_name: Name of the agent
        
    Returns:
        Formatted instruction text to add to agent prompt
    """
    loader = VisualizationLoader()
    return loader.get_visualization_instructions(agent_name)
