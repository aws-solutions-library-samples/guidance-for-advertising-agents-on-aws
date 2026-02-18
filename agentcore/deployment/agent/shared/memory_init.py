"""
Simple Memory Initialization for AgentCore Agents
Based on the provided sample code pattern.
"""

import os
import json
import logging
from typing import Optional, Dict, Any
from bedrock_agentcore.memory import MemoryClient

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def initialize_agent_memory() -> Optional[Dict[str, Any]]:
    """
    Initialize AgentCore memory using environment variables.
    Returns memory configuration if successful, None otherwise.
    """
    try:
        # Get environment variables
        memory_id = os.getenv('MEMORY_ID')
        actor_id = os.getenv('ACTOR_ID', 'default-actor')
        region = os.getenv('AWS_REGION', 'us-east-1')
        
        if not memory_id:
            logger.warning("No MEMORY_ID found in environment variables")
            return None
        
        logger.info(f"Initializing memory with ID: {memory_id}, Actor: {actor_id}, Region: {region}")
        
        # Create memory client
        client = MemoryClient(region_name=region)
        
        return {
            "memory_id": memory_id,
            "actor_id": actor_id,
            "region": region,
            "client": client
        }
        
    except Exception as e:
        logger.error(f"Failed to initialize memory: {e}")
        return None

def create_memory_config_file(memory_config: Dict[str, Any]) -> None:
    """Create a memory configuration file for the agent."""
    try:
        config_dir = "/app/config/memory"
        os.makedirs(config_dir, exist_ok=True)
        
        config_file = os.path.join(config_dir, "runtime_config.json")
        
        config_data = {
            "memory_id": memory_config["memory_id"],
            "actor_id": memory_config["actor_id"],
            "region": memory_config["region"],
            "initialized": True
        }
        
        with open(config_file, 'w') as f:
            json.dump(config_data, f, indent=2)
        
        logger.info(f"Memory configuration saved to {config_file}")
        
    except Exception as e:
        logger.error(f"Failed to create memory config file: {e}")

def main():
    """Main initialization function."""
    logger.info("Starting AgentCore memory initialization...")
    
    memory_config = initialize_agent_memory()
    if memory_config:
        create_memory_config_file(memory_config)
        logger.info("Memory initialization completed successfully")
    else:
        logger.warning("Memory initialization failed or skipped")

if __name__ == "__main__":
    main()