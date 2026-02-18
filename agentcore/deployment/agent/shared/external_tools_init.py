"""
Simple External Tools Initialization for AgentCore Agents
Sets up external tool integrations based on agent configuration.
"""

import os
import json
import logging
from typing import Dict, Any, List

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def load_agent_config() -> Dict[str, Any]:
    """Load agent configuration from config.json."""
    config_path = "/app/config.json"
    try:
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                return json.load(f)
        else:
            logger.info("No agent config.json found")
            return {}
    except Exception as e:
        logger.error(f"Failed to load agent config: {e}")
        return {}

def initialize_external_tools() -> None:
    """Initialize external tool integrations."""
    try:
        # Load agent configuration
        config = load_agent_config()
        
        # Get external tool agents from config
        external_agents = config.get('external_tool_agents', [])
        
        if not external_agents:
            logger.info("No external tool agents configured")
            return
        
        logger.info(f"Found {len(external_agents)} external tool agents: {external_agents}")
        
        # Create external tools config directory
        tools_config_dir = "/app/config/external_tools"
        os.makedirs(tools_config_dir, exist_ok=True)
        
        # Save external tools configuration
        tools_config = {
            "external_agents": external_agents,
            "stack_prefix": os.getenv('STACK_PREFIX', 'default'),
            "region": os.getenv('AWS_REGION', 'us-east-1'),
            "initialized": True
        }
        
        config_file = os.path.join(tools_config_dir, "runtime_config.json")
        with open(config_file, 'w') as f:
            json.dump(tools_config, f, indent=2)
        
        logger.info(f"External tools configuration saved to {config_file}")
        
    except Exception as e:
        logger.error(f"Failed to initialize external tools: {e}")

def main():
    """Main initialization function."""
    logger.info("Starting external tools initialization...")
    initialize_external_tools()
    logger.info("External tools initialization completed")

if __name__ == "__main__":
    main()