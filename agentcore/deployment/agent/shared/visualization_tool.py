"""
Tool for agents to access visualization templates programmatically.
"""

import json
from typing import Dict, Any, Optional
from shared.visualization_loader import VisualizationLoader


def get_visualization_templates(agent_name: str) -> str:
    """
    Get all available visualization templates for an agent.
    
    This tool provides agents with their visualization templates without
    needing to use file_read. The templates are loaded programmatically
    from the agent-visualizations-library directory.
    
    Args:
        agent_name: Name of the agent requesting templates
        
    Returns:
        JSON string containing all templates with their data mappings
    """
    loader = VisualizationLoader()
    
    # Load the visualization map
    viz_map = loader.load_agent_visualization_map(agent_name)
    
    if not viz_map:
        return json.dumps({
            "error": f"No visualization configuration found for {agent_name}",
            "templates": []
        })
    
    # Load all template data
    templates_data = []
    for template in viz_map.get("templates", []):
        template_id = template.get("templateId")
        usage = template.get("usage", "")
        
        # Load the data mapping for this template
        data_mapping = loader.load_template_data(agent_name, template_id)
        
        if data_mapping:
            templates_data.append({
                "templateId": template_id,
                "usage": usage,
                "dataMapping": data_mapping
            })
    
    result = {
        "agentName": agent_name,
        "agentId": viz_map.get("agentId"),
        "templates": templates_data
    }
    
    return json.dumps(result, indent=2)


def get_specific_template(agent_name: str, template_id: str) -> str:
    """
    Get a specific visualization template for an agent.
    
    Args:
        agent_name: Name of the agent
        template_id: ID of the template (e.g., "metrics-visualization")
        
    Returns:
        JSON string containing the template's data mapping
    """
    loader = VisualizationLoader()
    
    data_mapping = loader.load_template_data(agent_name, template_id)
    
    if not data_mapping:
        return json.dumps({
            "error": f"Template {template_id} not found for {agent_name}"
        })
    
    return json.dumps({
        "agentName": agent_name,
        "templateId": template_id,
        "dataMapping": data_mapping
    }, indent=2)


def format_visualization_response(
    agent_name: str,
    template_id: str,
    data: Dict[str, Any]
) -> str:
    """
    Format visualization data in the correct XML wrapper format.
    
    Args:
        agent_name: Name of the agent
        template_id: Template ID being used
        data: The mapped data to visualize
        
    Returns:
        XML-wrapped JSON string ready for the UI
    """
    json_data = json.dumps(data, indent=2)
    return f"<visualization-data type='{template_id}'>{json_data}</visualization-data>"


# Integration example for handler.py:
"""
from strands import tool
from shared.visualization_tool import get_visualization_templates, get_specific_template

@tool
def load_my_visualizations(agent_name: str) -> str:
    '''Load all visualization templates for this agent.'''
    return get_visualization_templates(agent_name)

@tool  
def load_specific_visualization(agent_name: str, template_id: str) -> str:
    '''Load a specific visualization template.'''
    return get_specific_template(agent_name, template_id)
"""
