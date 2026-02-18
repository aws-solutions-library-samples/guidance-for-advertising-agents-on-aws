from strands import Agent, tool
from strands_tools import use_llm, memory, http_request, generate_image, file_read
from strands.models import BedrockModel
from strands.multiagent.a2a import A2AServer
from strands_tools.a2a_client import A2AClientToolProvider
from botocore.config import Config
import uvicorn
from fastapi import FastAPI
import logging
import os
import sys
from boto3 import Session as AWSSession
import requests
import base64
import uuid
from typing import Dict, List, Optional, Any, Union
import copy
from opentelemetry import baggage, context
from strands.agent.conversation_manager import SummarizingConversationManager
from shared.response_model import (
    Source,
    SourceSet,
    StructuredDataContent,
    ResponseModel,
)
from shared.image_generator import generate_image_from_descriptions
from shared.adcp_tools import (
    ADCP_TOOLS, 
    get_adcp_mcp_tools,
    get_products,
    get_signals,
    activate_signal,
    create_media_buy,
    get_media_buy_delivery,
    verify_brand_safety,
    resolve_audience_reach,
    configure_brand_lift_study,
)

from shared.file_processor import get_s3_as_base64_and_extract_summary_and_facts
# DynamoDB configuration loader for fast agent config access
from shared.dynamodb_config_loader import (
    load_agent_instructions as ddb_load_instructions,
    load_agent_card as ddb_load_card,
    load_all_agent_cards as ddb_load_all_cards,
    load_global_config as ddb_load_global_config,
    preload_all_configs as ddb_preload_all,
    get_agent_config_table_name,
    clear_config_cache as ddb_clear_cache,
)
import re
import json
import asyncio
from datetime import datetime
from functools import lru_cache

# Add the parent directory to path for shared imports
sys.path.append(os.path.join(os.path.dirname(__file__), "..", ".."))

# S3 Configuration for loading agent configs
def get_s3_config_bucket():
    """Get the S3 bucket name for agent configurations."""
    stack_prefix = os.environ.get("STACK_PREFIX", "sim")
    unique_id = os.environ.get("UNIQUE_ID", "")
    if unique_id:
        return f"{stack_prefix}-data-{unique_id}"
    return None

S3_CONFIG_PREFIX = "configs"

# S3 client for loading configurations
_s3_client = None

def get_s3_client():
    """Get or create S3 client for config loading."""
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    return _s3_client


def load_from_s3(bucket: str, key: str) -> Optional[str]:
    """
    Load a file from S3 and return its contents as a string.
    
    Args:
        bucket: S3 bucket name
        key: S3 object key
        
    Returns:
        File contents as string, or None if not found
    """
    try:
        s3 = get_s3_client()
        response = s3.get_object(Bucket=bucket, Key=key)
        content = response["Body"].read().decode("utf-8")
        logger.info(f"✅ S3_LOAD: Loaded {key} from s3://{bucket}")
        return content
    except s3.exceptions.NoSuchKey:
        logger.warning(f"⚠️ S3_LOAD: Key not found: s3://{bucket}/{key}")
        return None
    except Exception as e:
        logger.error(f"❌ S3_LOAD: Failed to load s3://{bucket}/{key}: {e}")
        return None


def load_json_from_s3(bucket: str, key: str) -> Optional[dict]:
    """
    Load a JSON file from S3 and return as dict.
    
    Args:
        bucket: S3 bucket name
        key: S3 object key
        
    Returns:
        Parsed JSON as dict, or None if not found/invalid
    """
    content = load_from_s3(bucket, key)
    if content:
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            logger.error(f"❌ S3_LOAD: Invalid JSON in s3://{bucket}/{key}: {e}")
            return None
    return None


def list_s3_objects(bucket: str, prefix: str) -> list:
    """
    List objects in S3 bucket with given prefix.
    
    Args:
        bucket: S3 bucket name
        prefix: S3 key prefix
        
    Returns:
        List of object keys
    """
    try:
        s3 = get_s3_client()
        paginator = s3.get_paginator("list_objects_v2")
        keys = []
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        return keys
    except Exception as e:
        logger.error(f"❌ S3_LIST: Failed to list s3://{bucket}/{prefix}: {e}")
        return []


from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands.tools.mcp import MCPClient
from shared.short_term_memory_hook import ShortTermMemoryHook
from bedrock_agentcore.memory import MemoryClient

import boto3
from mcp import stdio_client, StdioServerParameters

# AgentCore Memory Integration

try:
    from bedrock_agentcore.memory import MemoryClient
    from strands.hooks import (
        AgentInitializedEvent,
        HookProvider,
        HookRegistry,
        MessageAddedEvent,
    )

    MEMORY_AVAILABLE = True
except ImportError:
    print("Warning: AgentCore Memory not available, continuing without memory")
    MEMORY_AVAILABLE = False

# AgentCore Memory Integration
from shared.memory_integration import (
    MemoryHookProvider,
    get_memory_configuration,
    create_memory_hooks_and_state,
    extract_session_id_and_memory_id_and_actor_from_payload,
    MEMORY_AVAILABLE,
)

# AgentCore Memory Conversation Manager for persistent session management
from shared.agentcore_memory_conversation_manager import (
    AgentCoreMemoryConversationManager,
    create_agentcore_memory_manager,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger.addHandler(logging.StreamHandler(sys.stdout))

logging.basicConfig(
    format="%(levelname)s | %(name)s | %(message)s", handlers=[logging.StreamHandler()]
)

os.environ["DEFAULT_TIMEOUT"] = "600"  # set request timeout to 10 minutes
region = os.environ.get("AWS_REGION", "us-east-1")

os.environ["BYPASS_TOOL_CONSENT"] = "true"

# Cache for SSM parameter values to avoid repeated API calls
_ssm_cache: Dict[str, str] = {}

# Cache for loaded configurations to avoid repeated S3 calls
_config_cache: Dict[str, dict] = {}

# Cache for agent instructions to avoid repeated S3/filesystem reads
_instructions_cache: Dict[str, str] = {}

# Flag to track if initialization has completed
_initialization_complete = False

# Load the config once at module level
CONFIG = {}
GLOBAL_CONFIG = {}


def get_memory_id_from_ssm() -> str:
    """
    Retrieve the AgentCore memory ID from SSM Parameter Store.
    
    The memory ID is stored at /{stack_prefix}/{unique_id}/agentcore_memory_id
    during deployment by the deploy-ecosystem.sh script.
    
    Falls back to MEMORY_ID environment variable if SSM retrieval fails.
    
    Returns:
        str: The memory ID, or empty string if not found
    """
    global _ssm_cache
    
    stack_prefix = os.environ.get("STACK_PREFIX", "")
    unique_id = os.environ.get("UNIQUE_ID", "")
    aws_region = os.environ.get("AWS_REGION", "us-east-1")
    
    # If stack_prefix or unique_id not set, fall back to environment variable
    if not stack_prefix or not unique_id:
        logger.warning("⚠️ MEMORY_SSM: STACK_PREFIX or UNIQUE_ID not set, falling back to MEMORY_ID env var")
        return os.environ.get("MEMORY_ID", "")
    
    # Build SSM parameter name
    ssm_param_name = f"/{stack_prefix}/{unique_id}/agentcore_memory_id"
    
    # Check cache first
    if ssm_param_name in _ssm_cache:
        logger.debug(f"📦 MEMORY_SSM: Using cached memory ID for {ssm_param_name}")
        return _ssm_cache[ssm_param_name]
    
    try:
        logger.info(f"📥 MEMORY_SSM: Retrieving memory ID from SSM: {ssm_param_name}")
        
        ssm_client = boto3.client("ssm", region_name=aws_region)
        response = ssm_client.get_parameter(
            Name=ssm_param_name,
            WithDecryption=False  # Memory ID is stored as String, not SecureString
        )
        
        memory_id = response["Parameter"]["Value"]
        
        # Cache the result
        _ssm_cache[ssm_param_name] = memory_id
        
        logger.info(f"✅ MEMORY_SSM: Retrieved memory ID from SSM: {memory_id}")
        return memory_id
        
    except boto3.client("ssm").exceptions.ParameterNotFound:
        logger.warning(f"⚠️ MEMORY_SSM: Parameter not found: {ssm_param_name}")
        logger.warning("   Falling back to MEMORY_ID environment variable")
        return os.environ.get("MEMORY_ID", "")
        
    except Exception as e:
        logger.error(f"❌ MEMORY_SSM: Failed to retrieve memory ID from SSM: {e}")
        logger.warning("   Falling back to MEMORY_ID environment variable")
        return os.environ.get("MEMORY_ID", "")


def clear_ssm_cache(param_name: Optional[str] = None):
    """
    Clear the SSM parameter cache.
    
    Args:
        param_name: Specific parameter to clear, or None to clear all
    """
    global _ssm_cache
    if param_name:
        _ssm_cache.pop(param_name, None)
        logger.info(f"🗑️ SSM_CACHE: Cleared cache for {param_name}")
    else:
        _ssm_cache.clear()
        logger.info("🗑️ SSM_CACHE: Cleared all SSM cache")


knowledgebaseMcpClient = MCPClient(
    lambda: stdio_client(
        StdioServerParameters(
            command="uvx", args=["awslabs.bedrock-kb-retrieval-mcp-server@latest"]
        )
    )
)

# Add the parent directory to path for shared imports
sys.path.append(os.path.join(os.path.dirname(__file__), "..", ".."))

app = BedrockAgentCoreApp()
client = MemoryClient(region_name=os.environ.get("AWS_REGION", "us-east-1"))
memory_name = "Agents_for_Advertising_%s" % datetime.now().strftime("%Y%m%d%H%M%S")

os.environ["AGENT_OBSERVABILITY_ENABLED"] = "true"


def load_external_agents():
    return CONFIG.get("external_agents")


def _normalize_name(name):
    """Normalize filename by lowercasing and replacing separators with a common character."""
    return name.lower().replace("_", "-").replace(" ", "-")


def _find_file_flexible(directory, filename):
    """
    Find a file in directory with flexible matching for underscores, spaces, and hyphens.
    Returns the actual filename if found, None otherwise.
    """
    if not os.path.exists(directory):
        return None

    target_normalized = _normalize_name(filename)

    try:
        files = os.listdir(directory)
        for file in files:
            if _normalize_name(file) == target_normalized:
                return file
    except OSError:
        return None

    return None


def inject_data_into_placeholder(instructions: str, agent_name: str) -> str:
    """
    Replace {{AGENT_NAME}} placeholder with actual agent name in instructions.

    Args:
        instructions: The instruction text containing placeholders
        agent_name: The actual agent name to inject

    Returns:
        Instructions with placeholders replaced
    """
    if not instructions:
        return instructions

    global orchestrator_instance

    # Check if placeholder exists
    if (
        "{{AGENT_NAME}}" not in instructions
        and "{{AGENT_NAME_LIST}}" not in instructions
    ):
        logger.debug(f"No placeholders found in instructions for {agent_name}")
        return instructions

    # Replace {{AGENT_NAME}} placeholder with actual agent name
    if "{{AGENT_NAME}}" in instructions:
        instructions = instructions.replace("{{AGENT_NAME}}", agent_name)
        logger.info(f"✓ Injected agent name '{agent_name}' into instructions")

    # Inject custom values from injectable_values configuration
    injectable_values = get_agent_config(agent_name=agent_name).get(
        "injectable_values", {}
    )
    if injectable_values:
        for key, value in injectable_values.items():
            placeholder = f"{{{{{key}}}}}"  # Creates {{key}} pattern
            if placeholder in instructions:
                instructions = instructions.replace(placeholder, str(value))
                logger.info(
                    f"✓ Injected '{key}' value into instructions for {agent_name}"
                )
        logger.debug(
            f"Processed {len(injectable_values)} injectable values for {agent_name}"
        )

    # Get list of agent names from tool_agent_names and inject into {{AGENT_NAME_LIST}} placeholder
    if "{{AGENT_NAME_LIST}}" in instructions:
        try:
            # Load all agent cards and create a list of objects
            # following the format [{agent_name:string, agent_description:string}]
            agent_name_list = []
            
            # Try DynamoDB AgentConfigTable first (fastest - new primary source)
            ddb_cards = ddb_load_all_cards(use_cache=True)
            if ddb_cards:
                for card_data in ddb_cards:
                    if card_data and "agent_name" in card_data and "agent_description" in card_data:
                        agent_name_list.append({
                            "agent_name": card_data["agent_name"],
                            "agent_description": card_data["agent_description"],
                        })
                if agent_name_list:
                    logger.info(f"✅ AGENT_CARDS: Loaded {len(agent_name_list)} agent cards from DynamoDB AgentConfigTable")
            
            # Try S3 if DynamoDB didn't return results
            if not agent_name_list:
                s3_bucket = get_s3_config_bucket()
                if s3_bucket:
                    s3_prefix = f"{S3_CONFIG_PREFIX}/agent_cards/"
                    logger.info(f"📥 AGENT_CARDS: Attempting to load from s3://{s3_bucket}/{s3_prefix}")
                    
                    card_keys = list_s3_objects(s3_bucket, s3_prefix)
                    for key in card_keys:
                        if key.endswith(".agent.card.json"):
                            try:
                                card_data = load_json_from_s3(s3_bucket, key)
                                if card_data and "agent_name" in card_data and "agent_description" in card_data:
                                    agent_name_list.append({
                                        "agent_name": card_data["agent_name"],
                                        "agent_description": card_data["agent_description"],
                                    })
                            except Exception as e:
                                logger.warning(f"Failed to load agent card {key}: {e}")
                    
                    if agent_name_list:
                        logger.info(f"✅ AGENT_CARDS: Loaded {len(agent_name_list)} agent cards from S3")
            
            # Fall back to local filesystem if S3 didn't work or returned empty
            if not agent_name_list:
                agent_cards_dir = os.path.join(os.path.dirname(__file__), "agent_cards")
                if os.path.exists(agent_cards_dir):
                    for filename in os.listdir(agent_cards_dir):
                        if filename.endswith(".agent.card.json"):
                            card_path = os.path.join(agent_cards_dir, filename)
                            try:
                                with open(card_path, "r") as f:
                                    card_data = json.load(f)
                                    if (
                                        "agent_name" in card_data
                                        and "agent_description" in card_data
                                    ):
                                        agent_name_list.append(
                                            {
                                                "agent_name": card_data["agent_name"],
                                                "agent_description": card_data[
                                                    "agent_description"
                                                ],
                                            }
                                        )
                            except Exception as e:
                                logger.warning(f"Failed to load agent card {filename}: {e}")

            if agent_name_list:
                instructions = instructions.replace(
                    "{{AGENT_NAME_LIST}}", json.dumps(agent_name_list)
                )
                logger.info(
                    f"✓ Injected agent name list into instructions: {agent_name_list}"
                )
            else:
                # If no tool agents, replace with empty string or a default message
                instructions = instructions.replace("{{AGENT_NAME_LIST}}", "")
                logger.debug(
                    f"No agent cards found to replace in instructions for {agent_name}. Replaced with empty string"
                )
        except Exception as e:
            logger.error(f"Error injecting agent name list for {agent_name}: {e}")
            # Leave placeholder as-is if there's an error

    return instructions


def load_instructions_for_agent(agent_name: str, use_cache: bool = True):
    """
    Load agent instructions from cache, DynamoDB, S3 bucket, or local filesystem.
    
    Priority:
    1. In-memory cache (if use_cache=True)
    2. DynamoDB AgentConfigTable (fastest)
    3. S3 bucket: {stack_prefix}-data-{unique_id}/configs/agent-instructions-library/{agent_name}.txt
    4. Local filesystem: agent-instructions-library/{agent_name}.txt
    
    Args:
        agent_name: Name of the agent to load instructions for
        use_cache: Whether to use cached instructions (default True). 
                   When False, forces a consistent read from DynamoDB.
        
    Returns:
        Instructions string with placeholders injected
    """
    global _instructions_cache
    
    # Check handler's local cache first
    if use_cache and agent_name in _instructions_cache:
        logger.info(f"📦 INSTRUCTIONS: Cache HIT for {agent_name}")
        return _instructions_cache[agent_name]
    
    # Try DynamoDB AgentConfigTable first (fastest)
    # Pass use_cache through - when False, DynamoDB loader uses consistent read
    content = ddb_load_instructions(agent_name, use_cache=use_cache)
    if content:
        content = content.strip()
        content = inject_data_into_placeholder(content, agent_name)
        logger.info(f"✅ INSTRUCTIONS: Loaded {agent_name} instructions from DynamoDB ({len(content)} chars, use_cache={use_cache})")
        _instructions_cache[agent_name] = content
        return content
    
    # Try S3 second
    s3_bucket = get_s3_config_bucket()
    if s3_bucket:
        s3_key = f"{S3_CONFIG_PREFIX}/agent-instructions-library/{agent_name}.txt"
        logger.info(f"📥 INSTRUCTIONS: Attempting to load from s3://{s3_bucket}/{s3_key}")
        
        content = load_from_s3(s3_bucket, s3_key)
        if content:
            content = content.strip()
            content = inject_data_into_placeholder(content, agent_name)
            logger.info(f"✅ INSTRUCTIONS: Loaded {agent_name} instructions from S3 ({len(content)} chars)")
            _instructions_cache[agent_name] = content
            return content
        else:
            logger.info(f"⚠️ INSTRUCTIONS: Not found in S3, falling back to local filesystem")
    
    # Fall back to local filesystem
    try:
        base_dir = os.path.dirname(__file__)
        library_dir = os.path.join(base_dir, "agent-instructions-library")

        # Try to list the directory to see what's actually there
        if os.path.exists(library_dir):
            files = os.listdir(library_dir)
        else:
            return "Couldn't load instructions - library directory not found."

        # Try flexible filename matching
        actual_filename = f"{agent_name}.txt"
        instructions_path = os.path.join(library_dir, actual_filename)
        path_exists = os.path.exists(instructions_path)

        if actual_filename and path_exists:
            logging.info(f"Loading instructions from {instructions_path}")
            with open(instructions_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                # Inject agent name placeholder
                content = inject_data_into_placeholder(content, agent_name)
                # Cache the result
                _instructions_cache[agent_name] = content
                return content
        else:
            # File doesn't exist
            logging.warning(f"Instructions file not found: {instructions_path}")
            return "Couldn't load instructions - file not found."

    except FileNotFoundError as e:
        logging.error(f"Warning: instructions.txt not found at {instructions_path}")
        return "Couldn't load instructions."
    except Exception as e:
        logging.error(f"Error loading instructions: {e}")
        return "Couldn't load instructions."


# Load configuration from file
def load_configs(file_name, use_cache: bool = True):
    """
    Load configuration from DynamoDB, S3 bucket, or fall back to local filesystem.
    
    Priority:
    1. In-memory cache (if use_cache=True)
    2. DynamoDB AgentConfigTable (fastest)
    3. S3 bucket: {stack_prefix}-data-{unique_id}/configs/{file_name}
    4. Local filesystem: {file_name} (relative to handler.py)
    
    Args:
        file_name: Name of the configuration file (e.g., "global_configuration.json")
        use_cache: Whether to use cached config (default True). Set False to force reload.
        
    Returns:
        Parsed JSON configuration as dict, or empty dict if not found
    """
    global _config_cache
    
    # Check cache first
    if use_cache and file_name in _config_cache:
        logger.debug(f"📦 CONFIG: Using cached {file_name}")
        return _config_cache[file_name]
    
    # Try DynamoDB AgentConfigTable first (fastest)
    # Pass use_cache through to DynamoDB loader - when False, it uses consistent read
    if file_name == "global_configuration.json":
        config_data = ddb_load_global_config(use_cache=use_cache)
        if config_data is not None:
            logger.info(f"✅ CONFIG: Loaded {file_name} from DynamoDB (use_cache={use_cache})")
            _config_cache[file_name] = config_data
            return config_data
    
    # DynamoDB is the primary config source - if not found, return empty dict
    logger.warning(f"⚠️ CONFIG: {file_name} not found in DynamoDB. Check AGENT_CONFIG_TABLE, STACK_PREFIX, UNIQUE_ID env vars and execution role permissions.")
    return {}


def clear_config_cache(file_name: Optional[str] = None):
    """
    Clear the configuration cache.
    
    Args:
        file_name: Specific file to clear from cache, or None to clear all
    """
    global _config_cache
    if file_name:
        _config_cache.pop(file_name, None)
        logger.info(f"🗑️ CONFIG: Cleared cache for {file_name}")
    else:
        _config_cache.clear()
        logger.info(f"🗑️ CONFIG: Cleared all config cache")


def clear_instructions_cache(agent_name: Optional[str] = None):
    """
    Clear the instructions cache.
    
    Args:
        agent_name: Specific agent to clear from cache, or None to clear all
    """
    global _instructions_cache
    if agent_name:
        _instructions_cache.pop(agent_name, None)
        logger.info(f"🗑️ INSTRUCTIONS: Cleared cache for {agent_name}")
    else:
        _instructions_cache.clear()
        logger.info(f"🗑️ INSTRUCTIONS: Cleared all instructions cache")

def refresh_all_caches(force_reinitialize: bool = False) -> Dict[str, Any]:
    """
    Refresh all configuration caches to load the latest data from DynamoDB/S3.

    This function clears all in-memory caches and reloads configurations from
    the primary data sources (DynamoDB first, then S3, then filesystem).

    Use this when agent configurations have been updated in DynamoDB and you
    want the running agent to pick up the changes without restarting.

    Args:
        force_reinitialize: If True, also reset the initialization flag to force
                           full re-initialization on next request

    Returns:
        Dict with refresh statistics including counts of reloaded items
    """
    global _config_cache, _instructions_cache, _initialization_complete, GLOBAL_CONFIG, CONFIG

    logger.info("=" * 60)
    logger.info("🔄 CACHE REFRESH: Starting full cache refresh...")
    logger.info("=" * 60)

    refresh_start = datetime.now()
    stats = {
        "timestamp": refresh_start.isoformat(),
        "caches_cleared": [],
        "items_reloaded": {},
        "errors": []
    }

    try:
        # Step 1: Clear all local caches
        logger.info("🗑️ CACHE_REFRESH: Clearing local caches...")

        # Clear handler.py local caches
        old_config_count = len(_config_cache)
        old_instructions_count = len(_instructions_cache)
        _config_cache.clear()
        _instructions_cache.clear()
        stats["caches_cleared"].append(f"config_cache ({old_config_count} entries)")
        stats["caches_cleared"].append(f"instructions_cache ({old_instructions_count} entries)")

        # Clear DynamoDB config loader cache - this also resets _cache_initialized
        ddb_clear_cache()
        stats["caches_cleared"].append("dynamodb_config_cache")

        # Clear SSM cache
        clear_ssm_cache()
        stats["caches_cleared"].append("ssm_cache")

        logger.info(f"✅ CACHE_REFRESH: Cleared {len(stats['caches_cleared'])} caches")

        # Step 2: Reset initialization flags - ALWAYS reset to force fresh loads
        _initialization_complete = False
        GLOBAL_CONFIG = {}
        CONFIG = {}
        stats["force_reinitialize"] = True
        logger.info("🔄 CACHE_REFRESH: Reset all initialization flags for fresh reload")

        # Step 3: Reload global configuration with use_cache=False to force DynamoDB consistent read
        logger.info("📥 CACHE_REFRESH: Reloading global configuration (consistent read)...")
        GLOBAL_CONFIG = load_configs("global_configuration.json", use_cache=False)
        agent_configs = GLOBAL_CONFIG.get("agent_configs", {})
        agent_names = list(agent_configs.keys())
        stats["items_reloaded"]["global_config"] = 1
        stats["items_reloaded"]["agent_configs_found"] = len(agent_names)
        stats["items_reloaded"]["agent_names"] = agent_names[:10]  # First 10 for logging
        logger.info(f"✅ CACHE_REFRESH: Loaded global config with {len(agent_names)} agents: {agent_names[:5]}...")

        # Step 4: Reload agent instructions directly with use_cache=False
        # This bypasses the preload mechanism and forces fresh reads from DynamoDB
        logger.info("📥 CACHE_REFRESH: Reloading agent instructions (consistent read)...")
        instructions_loaded = 0
        for agent_name in agent_names:
            try:
                # Force fresh read from DynamoDB with consistent read
                content = ddb_load_instructions(agent_name, use_cache=False)
                if content:
                    # Apply placeholder injection and cache in handler's _instructions_cache
                    content = content.strip()
                    content = inject_data_into_placeholder(content, agent_name)
                    _instructions_cache[agent_name] = content
                    instructions_loaded += 1
                    logger.debug(f"✅ CACHE_REFRESH: Reloaded instructions for {agent_name} ({len(content)} chars)")
            except Exception as instr_err:
                logger.warning(f"⚠️ CACHE_REFRESH: Failed to reload instructions for {agent_name}: {instr_err}")
                stats["errors"].append(f"Instructions {agent_name}: {str(instr_err)}")
        
        stats["items_reloaded"]["instructions_from_ddb"] = instructions_loaded
        logger.info(f"✅ CACHE_REFRESH: Reloaded {instructions_loaded} agent instructions from DynamoDB")

        # Step 5: Preload remaining agent instructions from S3/filesystem (fallback for agents not in DynamoDB)
        logger.info("📥 CACHE_REFRESH: Loading remaining instructions from S3/filesystem...")
        fallback_count = preload_all_agent_instructions()
        stats["items_reloaded"]["total_instructions"] = len(_instructions_cache)
        stats["items_reloaded"]["instructions_from_fallback"] = fallback_count - instructions_loaded if fallback_count > instructions_loaded else 0

        # Step 6: Preload agent cards from DynamoDB
        logger.info("📥 CACHE_REFRESH: Reloading agent cards...")
        try:
            cards = ddb_load_all_cards(use_cache=False)
            stats["items_reloaded"]["cards"] = len(cards) if cards else 0
            logger.info(f"✅ CACHE_REFRESH: Reloaded {len(cards) if cards else 0} agent cards")
        except Exception as cards_err:
            logger.warning(f"⚠️ CACHE_REFRESH: Failed to reload agent cards: {cards_err}")
            stats["errors"].append(f"Agent cards: {str(cards_err)}")

        # Mark initialization as complete
        _initialization_complete = True

        elapsed = (datetime.now() - refresh_start).total_seconds()
        stats["elapsed_seconds"] = elapsed
        stats["success"] = True

        logger.info("=" * 60)
        logger.info(f"🔄 CACHE REFRESH COMPLETE in {elapsed:.2f}s")
        logger.info(f"   - Caches cleared: {len(stats['caches_cleared'])}")
        logger.info(f"   - Instructions reloaded: {stats['items_reloaded'].get('total_instructions', 0)}")
        logger.info(f"   - Instructions from DynamoDB: {stats['items_reloaded'].get('instructions_from_ddb', 0)}")
        if stats["errors"]:
            logger.warning(f"   - Errors: {len(stats['errors'])}")
        logger.info("=" * 60)

        return stats

    except Exception as e:
        elapsed = (datetime.now() - refresh_start).total_seconds()
        stats["elapsed_seconds"] = elapsed
        stats["success"] = False
        stats["errors"].append(f"Fatal error: {str(e)}")

        logger.error(f"❌ CACHE_REFRESH: Failed after {elapsed:.2f}s: {e}")
        import traceback
        logger.error(f"❌ CACHE_REFRESH: Traceback: {traceback.format_exc()}")

        return stats


def get_cache_diagnostics() -> Dict[str, Any]:
    """
    Get diagnostic information about all caches for debugging.
    
    Returns:
        Dict with cache statistics and sample data
    """
    global _config_cache, _instructions_cache, _initialization_complete, GLOBAL_CONFIG
    
    # Get DynamoDB loader stats
    from shared.dynamodb_config_loader import get_cache_stats as ddb_get_stats
    
    diagnostics = {
        "timestamp": datetime.now().isoformat(),
        "initialization_complete": _initialization_complete,
        "handler_caches": {
            "config_cache_entries": len(_config_cache),
            "config_cache_keys": list(_config_cache.keys())[:10],
            "instructions_cache_entries": len(_instructions_cache),
            "instructions_cache_keys": list(_instructions_cache.keys())[:20],
            "instructions_sample": {}
        },
        "global_config": {
            "loaded": GLOBAL_CONFIG is not None and len(GLOBAL_CONFIG) > 0,
            "agent_count": len(GLOBAL_CONFIG.get("agent_configs", {})) if GLOBAL_CONFIG else 0,
            "agent_names": list(GLOBAL_CONFIG.get("agent_configs", {}).keys())[:10] if GLOBAL_CONFIG else []
        },
        "dynamodb_loader": ddb_get_stats(),
        "environment": {
            "AGENT_CONFIG_TABLE": os.environ.get("AGENT_CONFIG_TABLE", "NOT_SET"),
            "STACK_PREFIX": os.environ.get("STACK_PREFIX", "NOT_SET"),
            "UNIQUE_ID": os.environ.get("UNIQUE_ID", "NOT_SET"),
        }
    }
    
    # Add sample instruction lengths (first 5 agents)
    for agent_name in list(_instructions_cache.keys())[:5]:
        content = _instructions_cache.get(agent_name)
        if content:
            diagnostics["handler_caches"]["instructions_sample"][agent_name] = {
                "length": len(content),
                "preview": content[:100] + "..." if len(content) > 100 else content
            }
    
    return diagnostics


response_model_parsed = {}


def get_agent_config(agent_name):
    """Get the configuration for a specific agent"""
    global GLOBAL_CONFIG
    # Use cached GLOBAL_CONFIG if available, otherwise load it
    if not GLOBAL_CONFIG:
        GLOBAL_CONFIG = load_configs("global_configuration.json")
    return GLOBAL_CONFIG.get("agent_configs", {}).get(agent_name, {})


def get_collaborator_agent_model_inputs(agent_name, orchestrator_name):
    """Get the model inputs for a collaborator agent"""
    global GLOBAL_CONFIG
    # Use cached GLOBAL_CONFIG if available, otherwise load it
    if not GLOBAL_CONFIG:
        GLOBAL_CONFIG = load_configs("global_configuration.json")
    orchestrator_config = GLOBAL_CONFIG.get("agent_configs", {}).get(
        orchestrator_name, {}
    )
    model_inputs = orchestrator_config.get("model_inputs", {})
    return model_inputs.get(agent_name, {})


def get_collaborator_agent_config(agent_name, orchestrator_name):
    """Get the configuration for a collaborator agent"""
    global GLOBAL_CONFIG
    # Use cached GLOBAL_CONFIG if available, otherwise load it
    if not GLOBAL_CONFIG:
        GLOBAL_CONFIG = load_configs("global_configuration.json")
    orchestrator_config = GLOBAL_CONFIG.get("agent_configs", {}).get(
        orchestrator_name, {}
    )
    # For collaborators, we need to build a config from the orchestrator's settings
    return {
        "agent_name": agent_name,
        "agent_description": f"Collaborator agent: {agent_name}",
        "model_inputs": orchestrator_config.get("model_inputs", {}).get(agent_name, {}),
    }


# Import shared knowledge base helper
from shared.knowledge_base_helper import (
    setup_agent_knowledge_base,
    get_knowledge_base_tool,
    format_kb_result_with_sources,
    retrieve_knowledge_base_results,
    KnowledgeBaseResult,
    KnowledgeBaseHelper,
)


def load_all_stack_knowledgebase_ids() -> Optional[str]:
    """
    Get knowledge base ID by name pattern (case-insensitive partial match)
    Args:
        name_pattern: Pattern to match against knowledge base names
    Returns:
        str: Knowledge base ID if found, None otherwise
    """
    knowledgebase_ids = {}
    # Discover knowledge bases from AWS
    helper = KnowledgeBaseHelper(logger, os.environ.get("AWS_REGION", "us-east-1"))
    kb_mapping = helper._discover_knowledge_bases_from_aws()
    if not kb_mapping:
        return None
    for kb_name, kb_id in kb_mapping.items():
        if f'{os.environ.get("STACK_PREFIX","XXX")}-' not in kb_name:
            continue
        knowledgebase_ids[kb_name] = kb_id
    return knowledgebase_ids


KNOWLEDGEBASE_IDS = load_all_stack_knowledgebase_ids()


def get_matching_kb_id(name: str) -> Optional[str]:
    return KNOWLEDGEBASE_IDS.get(
        f'{os.environ.get("STACK_PREFIX", "XXX")}-{name}-{os.environ.get("UNIQUE_ID", "XXX")}',
        None,
    )


def preload_all_agent_instructions():
    """
    Pre-load all agent instructions into cache at startup.
    This eliminates DynamoDB/S3/filesystem reads during agent creation.
    
    Note: DynamoDB batch preloading is now handled by ddb_preload_all() in initialize_handler().
    This function handles S3 and filesystem fallback for any agents not in DynamoDB.
    """
    global _instructions_cache, GLOBAL_CONFIG
    
    logger.info("🚀 PRELOAD: Starting pre-load of all agent instructions...")
    start_time = datetime.now()
    
    # Ensure global config is loaded
    if not GLOBAL_CONFIG:
        GLOBAL_CONFIG = load_configs("global_configuration.json")
    
    # Get all agent names from config
    agent_configs = GLOBAL_CONFIG.get("agent_configs", {})
    agent_names = list(agent_configs.keys())
    
    # Check which agents already have cached instructions (from DynamoDB preload)
    missing_agents = [name for name in agent_names if name not in _instructions_cache]
    if not missing_agents:
        elapsed = (datetime.now() - start_time).total_seconds()
        logger.info(f"🚀 PRELOAD: All {len(_instructions_cache)} agent instructions already cached in {elapsed:.2f}s")
        return len(_instructions_cache)
    
    logger.info(f"⚠️ PRELOAD: {len(missing_agents)} agents need instructions from S3/filesystem")
    agent_names = missing_agents  # Only load missing agents from other sources
    
    # Also check for agent cards to get additional agent names
    s3_bucket = get_s3_config_bucket()
    if s3_bucket:
        s3_prefix = f"{S3_CONFIG_PREFIX}/agent_cards/"
        card_keys = list_s3_objects(s3_bucket, s3_prefix)
        for key in card_keys:
            if key.endswith(".agent.card.json"):
                # Extract agent name from filename
                filename = key.split("/")[-1]
                agent_name = filename.replace(".agent.card.json", "")
                if agent_name not in agent_names and agent_name not in _instructions_cache:
                    agent_names.append(agent_name)
    
    # Also scan local agent-instructions-library directory
    base_dir = os.path.dirname(__file__)
    library_dir = os.path.join(base_dir, "agent-instructions-library")
    if os.path.exists(library_dir):
        for filename in os.listdir(library_dir):
            if filename.endswith(".txt"):
                agent_name = filename.replace(".txt", "")
                if agent_name not in agent_names and not agent_name.startswith("_") and agent_name not in _instructions_cache:
                    agent_names.append(agent_name)
    
    logger.info(f"🚀 PRELOAD: Found {len(agent_names)} additional agents to pre-load instructions for")
    
    # Pre-load instructions for each remaining agent
    loaded_count = len(_instructions_cache)
    for agent_name in agent_names:
        if agent_name in _instructions_cache:
            continue
        try:
            # Force load (bypass cache) to populate cache
            instructions = load_instructions_for_agent(agent_name, use_cache=False)
            if instructions and "Couldn't load" not in instructions:
                loaded_count += 1
                logger.debug(f"✅ PRELOAD: Loaded instructions for {agent_name}")
        except Exception as e:
            logger.warning(f"⚠️ PRELOAD: Failed to load instructions for {agent_name}: {e}")
    
    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info(f"🚀 PRELOAD: Completed pre-loading {loaded_count} agent instructions in {elapsed:.2f}s")
    
    return loaded_count


def initialize_handler():
    """
    Initialize the handler by pre-loading all configurations and instructions.
    This should be called once at module load time.
    
    Priority for loading:
    1. DynamoDB AgentConfigTable (fastest - new primary source)
    2. S3 bucket
    3. Local filesystem
    """
    global _initialization_complete, GLOBAL_CONFIG, CONFIG, KNOWLEDGEBASE_IDS
    
    if _initialization_complete:
        logger.debug("⏭️ INIT: Handler already initialized, skipping")
        return
    
    logger.info("=" * 60)
    logger.info("🚀 HANDLER INITIALIZATION STARTING")
    logger.info("=" * 60)
    
    init_start = datetime.now()
    
    try:
        # Step 1: Load global configuration (tries DynamoDB first)
        logger.info("📥 INIT: Loading global configuration...")
        GLOBAL_CONFIG = load_configs("global_configuration.json")
        logger.info(f"✅ INIT: Global config loaded with {len(GLOBAL_CONFIG.get('agent_configs', {}))} agent configs")
        
        # Get agent names for preloading
        agent_names = list(GLOBAL_CONFIG.get("agent_configs", {}).keys())
        
        # Step 2: Try DynamoDB batch preload first (fastest)
        ddb_table_name = get_agent_config_table_name()
        if ddb_table_name:
            logger.info(f"📥 INIT: Attempting DynamoDB batch preload from {ddb_table_name}...")
            try:
                ddb_counts = ddb_preload_all(agent_names)
                if ddb_counts.get("status") != "already_initialized":
                    logger.info(f"✅ INIT: DynamoDB preload completed:")
                    logger.info(f"   - Instructions: {ddb_counts.get('instructions', 0)}")
                    logger.info(f"   - Cards: {ddb_counts.get('cards', 0)}")
            except Exception as e:
                logger.warning(f"⚠️ INIT: DynamoDB preload failed, falling back to S3/filesystem: {e}")
        
        # Step 3: Pre-load any remaining agent instructions (S3/filesystem fallback)
        logger.info("📥 INIT: Pre-loading remaining agent instructions...")
        preload_all_agent_instructions()
        
        # Step 5: Refresh knowledge base IDs if needed
        if not KNOWLEDGEBASE_IDS:
            logger.info("📥 INIT: Loading knowledge base IDs...")
            KNOWLEDGEBASE_IDS = load_all_stack_knowledgebase_ids() or {}
            logger.info(f"✅ INIT: Loaded {len(KNOWLEDGEBASE_IDS)} knowledge base IDs")
        
        _initialization_complete = True
        
        elapsed = (datetime.now() - init_start).total_seconds()
        logger.info("=" * 60)
        logger.info(f"🚀 HANDLER INITIALIZATION COMPLETE in {elapsed:.2f}s")
        logger.info(f"   - Config cache entries: {len(_config_cache)}")
        logger.info(f"   - Instructions cache entries: {len(_instructions_cache)}")
        logger.info(f"   - Knowledge bases: {len(KNOWLEDGEBASE_IDS)}")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error(f"❌ INIT: Handler initialization failed: {e}")
        import traceback
        logger.error(f"❌ INIT: Traceback: {traceback.format_exc()}")
        # Don't set _initialization_complete so it can retry


# Run initialization at module load time
initialize_handler()


def get_tool_agent_names():
    """Get the list of tool names that should be wrapped as agent messages"""
    return CONFIG.get("tool_agent_names", [])


all_agents = get_tool_agent_names()
agent_actors = {}
for agent_name in all_agents:
    # Use simple agent name as actor_id to comply with validation pattern
    actor_id = agent_name.replace("_", "-")
    agent_actors[agent_name] = actor_id


from shared.runtime_resolver import RuntimeARNResolver


def get_agentcore_config_from_ssm():
    """
    Retrieve AgentCore configuration from SSM Parameter Store.

    Returns:
        dict: Configuration with agents list containing runtime ARNs and auth config
    """
    try:
        stack_prefix = os.environ.get("STACK_PREFIX", "sim")
        unique_id = os.environ.get("UNIQUE_ID", "")
        region = os.environ.get("AWS_REGION", "us-east-1")

        if not unique_id:
            logging.warning("[get_agentcore_config_from_ssm] UNIQUE_ID not set")
            return None

        parameter_name = f"/{stack_prefix}/agentcore_values/{unique_id}"
        ssm = boto3.client("ssm", region_name=region)

        # Retrieve parameter with decryption
        response = ssm.get_parameter(Name=parameter_name, WithDecryption=True)

        # Parse JSON value
        config_json = response["Parameter"]["Value"]
        config = json.loads(config_json)

        agent_count = len(config.get("agents", []))
        return config

    except Exception as e:
        logging.error(f"[get_agentcore_config_from_ssm] Error: {e}")
        return None


def get_memory_id_from_ssm() -> str:
    """
    Retrieve AgentCore memory ID from SSM Parameter Store.
    
    The memory ID is stored at: /{stack_prefix}/{unique_id}/agentcore_memory_id
    
    Falls back to MEMORY_ID environment variable if SSM retrieval fails.

    Returns:
        str: Memory ID if found, empty string otherwise
    """
    try:
        stack_prefix = os.environ.get("STACK_PREFIX", "sim")
        unique_id = os.environ.get("UNIQUE_ID", "")
        region = os.environ.get("AWS_REGION", "us-east-1")

        if not unique_id:
            logging.warning("[get_memory_id_from_ssm] UNIQUE_ID not set, falling back to env var")
            return os.environ.get("MEMORY_ID", "")

        parameter_name = f"/{stack_prefix}/{unique_id}/agentcore_memory_id"
        ssm = boto3.client("ssm", region_name=region)

        # Retrieve parameter
        response = ssm.get_parameter(Name=parameter_name)
        memory_id = response["Parameter"]["Value"]
        
        logging.info(f"[get_memory_id_from_ssm] Retrieved memory ID from SSM: {memory_id}")
        return memory_id

    except Exception as e:
        logging.warning(f"[get_memory_id_from_ssm] SSM retrieval failed: {e}, falling back to env var")
        return os.environ.get("MEMORY_ID", "")


def get_runtime_arn_and_auth_config(agent_name: str):
    """
    Get runtime ARN and A2A auth config for an agent from SSM Parameter Store or RUNTIMES env var.

    This function retrieves runtime configuration from SSM Parameter Store first,
    then falls back to the RUNTIMES environment variable if SSM is unavailable.

    A2A authentication credentials (pool_id, client_id) are retrieved from
    environment variables — bearer tokens are generated on demand at invocation time.

    Args:
        agent_name: Name of the agent to look up

    Returns:
        tuple: (runtime_arn, auth_config_dict) or (None, None) if not found.
               auth_config_dict contains pool_id, client_id, discovery_url from env vars.
    """
    region_name = os.environ.get("AWS_REGION", "us-east-1")

    # Build auth config from environment variables (shared across all A2A agents)
    auth_config = {
        "pool_id": os.environ.get("A2A_POOL_ID", ""),
        "client_id": os.environ.get("A2A_CLIENT_ID", ""),
        "discovery_url": os.environ.get("A2A_DISCOVERY_URL", ""),
    }

    # Try SSM first
    ssm_config = get_agentcore_config_from_ssm()
    if ssm_config:
        agents = ssm_config.get("agents", "{}")
        for agent in agents:
            agent_name_normalized = agent.get("name", "").lower().replace("-", "_")
            search_name_normalized = agent_name.lower().replace("-", "_")
            # Match agent name (handle both hyphen and underscore variations)
            if search_name_normalized in agent_name_normalized:
                runtime_arn = agent.get("runtime_arn", "")

                if runtime_arn:
                    # Override auth config with per-agent values from SSM if present
                    if agent.get("pool_id"):
                        auth_config["pool_id"] = agent["pool_id"]
                    if agent.get("client_id"):
                        auth_config["client_id"] = agent["client_id"]
                    if agent.get("discovery_url"):
                        auth_config["discovery_url"] = agent["discovery_url"]
                    return runtime_arn, auth_config

    runtimes_env = os.environ.get("RUNTIMES", "")

    if not runtimes_env:
        return None, None

    # Parse RUNTIMES: format is "arn1,arn2,..." (no bearer tokens)
    for entry in runtimes_env.split(","):
        arn = entry.strip()
        if not arn:
            continue

        # Handle legacy format "arn|token" by stripping the token part
        if "|" in arn:
            arn = arn.split("|", 1)[0]

        # Check if this runtime matches the agent name
        if agent_name.lower().replace("_", "-") in arn.lower():
            print(f"Found runtime for {agent_name} in RUNTIMES env var: {arn[:60]}...")
            return arn, auth_config

    print(f"No runtime found for {agent_name}")
    return None, None


# @tool
# async def invoke_external_agent_with_a2a(
#     agent_name: str, prompt: str, session_id: str
# ) -> str:
#     """
#     Invoke an external A2A agent, authenticating on demand via Cognito.
#
#     Args:
#         agent_name: Name of the external agent to invoke
#         prompt: The prompt/message to send to the agent
#
#     Returns:
#         Response from the external agent
#     """
#     print(f"[invoke_external_agent_with_a2a] ===== STARTING A2A INVOCATION =====")
#     print(f"[invoke_external_agent_with_a2a] Invoking external A2A agent: {agent_name}")
#
#     # Get runtime ARN and auth config
#     runtime_arn, auth_config = get_runtime_arn_and_auth_config(agent_name)
#
#     if not runtime_arn:
#         error_msg = f"Could not find runtime URL for agent: {agent_name}"
#         print(error_msg)
#         return f"Error: {error_msg}"
#
#     if not auth_config.get("pool_id") or not auth_config.get("client_id"):
#         error_msg = f"No A2A auth config (pool_id/client_id) found for agent: {agent_name}. Agent may not be A2A-enabled."
#         print(error_msg)
#         return f"Error: {error_msg}"
#
#     try:
#         # Authenticate on demand to get a fresh bearer token
#         from shared.a2a_auth import get_bearer_token_from_cognito
#         bearer_token = get_bearer_token_from_cognito(
#             pool_id=auth_config["pool_id"],
#             client_id=auth_config["client_id"],
#             region=os.environ.get("AWS_REGION", "us-east-1"),
#         )
#
#         from shared.a2a_agent_as_tool import send_sync_message
#
#         print(f"Creating A2A tool for {agent_name} with fresh bearer token")
#
#         response = await send_sync_message(
#             message=prompt,
#             region=os.environ.get("AWS_REGION", "us-east-1"),
#             agent_arn=runtime_arn,
#             bearer_token=bearer_token,
#             session_id=session_id,
#         )
#         print(f"Received response from {agent_name}: {response[:100]}...")
#         return f"<agent-message agent='{agent_name}'>{response}</agent-message>"
#
#     except Exception as e:
#         error_msg = f"Error invoking A2A agent {agent_name}: {str(e)}"
#         print(error_msg)
#         return f"Error: {error_msg}"


@tool
def invoke_specialist_with_RAG(
    agent_prompt: str, agent_name: str, is_collaborator: bool = True
) -> str:
    global orchestrator_instance
    global collected_sources
    global GLOBAL_CONFIG

    if GLOBAL_CONFIG is None:
        GLOBAL_CONFIG = load_configs("global_configuration.json")
    kb_name = GLOBAL_CONFIG.get("knowledge_bases", {}).get(agent_name)
    if kb_name is not None:
        try:
            logger.info(f"🔧 TOOL: KB name for {agent_name}: {kb_name}")
        except Exception as e:
            print(
                f"agent KB setup failed - this could just be because the agent is not configured to use a knowledgebase: {e}"
            )
    # Get memory configuration from the orchestrator instance if available
    session_id = orchestrator_instance.session_id
    memory_id = orchestrator_instance.memory_id
    orchestrator_name = orchestrator_instance.agent_name
    # Normalize actor_id to comply with validation pattern
    normalized_actor_id = agent_name.replace("_", "-")
    state = {
        "actor_id": normalized_actor_id,
        "session_id": session_id,
        "memory_id": memory_id,
    }
    # Tools are added in create_agent() based on agent_name - no need to add them here
    agent = create_agent(
        agent_name=agent_name, conversation_context="", is_collaborator=True
    )
    
    result = agent(f"""{agent_prompt}""")

    response_wrapper = (
        f"<agent-message agent='{agent_name}'>{str(result)}</agent-message>"
    )
    return response_wrapper


@tool
def invoke_specialist(agent_prompt: str, agent_name: str) -> str:
    """
    Invoke a specialist agent for collaboration without requiring a knowledge base query.
    
    Use this tool to collaborate with other agents in the agentic advertising ecosystem.
    This is ideal for agents that don't need to query knowledge bases but need to 
    coordinate with other specialists (e.g., VerificationAgent, IdentityAgent, etc.)
    
    Args:
        agent_prompt: The prompt/request to send to the specialist agent
        agent_name: Name of the specialist agent to invoke (e.g., "VerificationAgent", "IdentityAgent")
    
    Returns:
        Response from the specialist agent wrapped in agent-message tags
    """
    global orchestrator_instance
    
    logger.info(f"🔧 TOOL: Invoking specialist agent: {agent_name}")
    
    # Get memory configuration from the orchestrator instance if available
    session_id = orchestrator_instance.session_id
    memory_id = orchestrator_instance.memory_id
    
    # Normalize actor_id to comply with validation pattern
    normalized_actor_id = agent_name.replace("_", "-")
    
    # Tools are added in create_agent() based on agent_name - no need to add them here
    agent = create_agent(
        agent_name=agent_name, conversation_context="", is_collaborator=True
    )
    
    result = agent(f"""{agent_prompt}""")

    response_wrapper = (
        f"<agent-message agent='{agent_name}'>{str(result)}</agent-message>"
    )
    return response_wrapper


@tool
def lookup_events(agent_name: str, max_results: int = 5) -> str:
    """
    Look up the last things said by a specific agent in the current session.

    Args:
        agent_name: Name of the agent whose events to retrieve
        max_results: Maximum number of events to retrieve (default: 5)

    Returns:
        str: Formatted string containing the agent's recent messages
    """
    global orchestrator_instance

    try:
        # Get session_id and memory_id from orchestrator instance
        session_id = orchestrator_instance.session_id
        memory_id = orchestrator_instance.memory_id

        if not session_id or session_id == "new_session-12345678901234567890":
            return f"No active session found. Cannot retrieve events for {agent_name}."

        # Initialize bedrock-agentcore client
        bedrock_agentcore_client = boto3.client(
            "bedrock-agentcore", region_name=os.environ.get("AWS_REGION", "us-east-1")
        )

        actor_id = agent_name.replace("_", "-")

        logger.info(
            f"🔍 LOOKUP_EVENTS: Looking up events for {agent_name} (actor: {actor_id}) in session {session_id}"
        )

        # Call list_events API
        response = bedrock_agentcore_client.list_events(
            memoryId=memory_id,
            sessionId=session_id,
            actorId=actor_id,
            maxResults=max_results,
        )

        events = response.get("events", [])

        if not events:
            return f"No recent events found for {agent_name} in this session."

        # Try using get_last_k_turns from memory client instead
        # This is more reliable than get_event for retrieving conversation history
        try:
            from bedrock_agentcore.memory import MemoryClient

            memory_client = MemoryClient(
                region_name=os.environ.get("AWS_REGION", "us-east-1")
            )

            recent_turns = memory_client.get_last_k_turns(
                memory_id=memory_id,
                actor_id=actor_id,
                session_id=session_id,
                k=max_results,
                branch_name="main",
                max_results=max_results
                * 2,  # Get more messages to ensure we have enough turns
            )

            if recent_turns:
                result = f"Recent messages from {agent_name}:\n\n"
                for idx, turn in enumerate(recent_turns, 1):
                    for message in turn:
                        role = message.get("role", "unknown")
                        content = message.get("content", {})

                        # Extract text from content
                        if isinstance(content, dict) and "text" in content:
                            text = content["text"]
                        elif isinstance(content, str):
                            text = content
                        else:
                            text = str(content)

                        result += f"{idx}. {role.upper()}:\n{text}\n\n"

                logger.info(
                    f"✅ LOOKUP_EVENTS: Retrieved {len(recent_turns)} turns for {agent_name}"
                )
                return result
            else:
                return f"No recent conversation turns found for {agent_name} in this session."

        except Exception as memory_error:
            logger.warning(
                f"⚠️ LOOKUP_EVENTS: get_last_k_turns failed: {memory_error}, falling back to event-by-event retrieval"
            )

            # Fallback: try get_event for each event
            result = f"Recent messages from {agent_name}:\n\n"

            for idx, event_summary in enumerate(events, 1):
                event_id = event_summary.get("eventId")
                event_type = event_summary.get("eventType", "UNKNOWN")
                timestamp = event_summary.get("timestamp", "N/A")

                # Fetch full event details using get_event
                try:
                    logger.info(
                        f"🔍 LOOKUP_EVENTS: Fetching event {event_id} for {agent_name}"
                    )
                    event_response = bedrock_agentcore_client.get_event(
                        memoryId=memory_id, sessionId=session_id, eventId=event_id
                    )

                    full_event = event_response.get("event", {})
                    logger.info(
                        f"✅ LOOKUP_EVENTS: Got event data: {full_event.keys()}"
                    )

                    # Extract message content based on event type
                    if event_type == "MESSAGE":
                        message_data = full_event.get("message", {})
                        role = message_data.get("role", "unknown")
                        content = message_data.get("content", [])

                        # Extract text from content blocks
                        text_content = []
                        for block in content:
                            if isinstance(block, dict) and "text" in block:
                                text_content.append(block["text"])

                        if text_content:
                            result += f"{idx}. [{timestamp}] {role.upper()}:\n"
                            result += "\n".join(text_content)
                            result += "\n\n"
                    else:
                        # Handle other event types if needed
                        result += f"{idx}. [{timestamp}] Event type: {event_type}\n\n"

                except Exception as event_error:
                    logger.error(
                        f"❌ LOOKUP_EVENTS: Failed to get event {event_id}: {str(event_error)}"
                    )
                    import traceback

                    logger.error(f"   Traceback: {traceback.format_exc()}")
                    result += f"{idx}. [{timestamp}] [Error: {str(event_error)}]\n\n"

            logger.info(
                f"✅ LOOKUP_EVENTS: Retrieved {len(events)} events for {agent_name}"
            )
            return result

    except Exception as e:
        error_msg = f"Error looking up events for {agent_name}: {str(e)}"
        logger.error(f"❌ LOOKUP_EVENTS: {error_msg}")
        return error_msg


@tool
def retrieve_knowledge_base_results_tool(
    agent_name: str, knowledge_base_query: str
) -> str:
    global collected_sources
    global orchestrator_instance
    global response_model_parsed
    global GLOBAL_CONFIG
    
    # Use cached GLOBAL_CONFIG if available
    if not GLOBAL_CONFIG:
        GLOBAL_CONFIG = load_configs("global_configuration.json")
    
    kb_name = GLOBAL_CONFIG.get("knowledge_bases", {}).get(agent_name)
    logger.info(f"🔧 TOOL: KB name: {kb_name}")
    result_string = "<sources>"
    kb_id = get_matching_kb_id(kb_name)
    if kb_id is None:
        return ""
    else:
        logger.info(f"🔧 TOOL: KB ID: {kb_id}")

    if collected_sources is None:
        collected_sources = {}
    # Pass the full configs dict which contains the knowledge_bases key
    os.environ["STRANDS_KNOWLEDGE_BASE_ID"] = kb_id
    kb_result = retrieve_knowledge_base_results(
        knowledge_base_query,
        agent_name,
        min_score=0.4,
        max_results=3,
        include_metadata=True,
    )

    citations = kb_result.get("citations", [])
    for citation in citations:
        generatedResponse = (
            citation.get("generatedResponsePart", {})
            .get("textResponsePart", {})
            .get("text")
        )
        if "I am unable to" not in generatedResponse:
            result_string += f"<source>{generatedResponse}</source>"
    result_string += "</sources>"
    # generated_text = kb_result.get('output', {}).get('text', '')
    print(f"\n\n\ncitations:\n{result_string}\n\n\n")
    if agent_name not in collected_sources:
        collected_sources[agent_name] = []
    kb_result["query"] = knowledge_base_query
    collected_sources[agent_name].append(kb_result)

    return result_string


def build_tools_for_agent(agent_name: str) -> list:
    """
    Build the tools list for an agent based on its agent_tools configuration.
    Also includes MCP tools if mcp_servers are configured.
    
    Args:
        agent_name: Name of the agent to build tools for
        
    Returns:
        List of tool functions configured for this agent
    """
    # Get agent configuration
    agent_config = get_agent_config(agent_name)
    configured_tools = agent_config.get("agent_tools", [])
    
    # If no tools configured, provide sensible defaults
    if not configured_tools:
        configured_tools = ["retrieve_knowledge_base_results_tool", "lookup_events"]
        logger.warning(f"⚠️ BUILD_TOOLS: No agent_tools configured for {agent_name}, using defaults")
    
    # Map tool names to actual tool functions
    TOOL_REGISTRY = {
        # Core tools
        "lookup_events": lookup_events,
        "retrieve_knowledge_base_results_tool": retrieve_knowledge_base_results_tool,
        "invoke_specialist": invoke_specialist,
        "invoke_specialist_with_RAG": invoke_specialist_with_RAG,
        
        # File and media tools
        "file_read": file_read,
        "generate_image_from_descriptions": generate_image_from_descriptions,
        "http_request": http_request,
        
        # AdCP Media Buy Protocol tools
        "get_products": get_products,
        "create_media_buy": create_media_buy,
        "get_media_buy_delivery": get_media_buy_delivery,
        
        # AdCP Signals Protocol tools
        "get_signals": get_signals,
        "activate_signal": activate_signal,
        
        # MCP Verification/Identity/Measurement tools
        "verify_brand_safety": verify_brand_safety,
        "resolve_audience_reach": resolve_audience_reach,
        "configure_brand_lift_study": configure_brand_lift_study,
        
    }
    
    tools = []
    
    for tool_name in configured_tools:
        if tool_name in TOOL_REGISTRY:
            tools.append(TOOL_REGISTRY[tool_name])
        else:
            logger.warning(f"⚠️ BUILD_TOOLS: Unknown tool '{tool_name}' for {agent_name}")
    
    # Build MCP tools from mcp_servers configuration
    mcp_tools = build_mcp_tools_for_agent(agent_name, agent_config)
    if mcp_tools:
        tools.extend(mcp_tools)
        logger.info(f"🔌 BUILD_TOOLS: Added {len(mcp_tools)} MCP tool providers for {agent_name}")
    
    logger.info(f"🔧 BUILD_TOOLS: {agent_name} configured with {len(tools)} tools: {[getattr(t, '__name__', str(t)) for t in tools]}")
    return tools


def build_mcp_tools_for_agent(agent_name: str, agent_config: dict) -> list:
    """
    Build MCP tool providers from the agent's mcp_servers configuration.
    
    Follows the Strands Agents MCP integration pattern:
    https://strandsagents.com/latest/documentation/docs/user-guide/concepts/tools/mcp-tools/
    
    Args:
        agent_name: Name of the agent
        agent_config: Agent configuration dict containing mcp_servers
        
    Returns:
        List of MCPClient instances configured for this agent
    """
    mcp_servers = agent_config.get("mcp_servers", [])
    
    # Debug logging to trace MCP config loading
    logger.info(f"🔍 MCP_TOOLS: Checking mcp_servers for {agent_name}")
    logger.info(f"   Agent config keys: {list(agent_config.keys())}")
    logger.info(f"   mcp_servers count: {len(mcp_servers)}")
    
    if not mcp_servers:
        logger.info(f"⚠️ MCP_TOOLS: No mcp_servers configured for {agent_name}")
        return []
    
    logger.info(f"🔌 MCP_TOOLS: Found {len(mcp_servers)} MCP server(s) for {agent_name}")
    for i, srv in enumerate(mcp_servers):
        logger.info(f"   [{i+1}] {srv.get('name', 'unnamed')} - transport: {srv.get('transport', 'unknown')}, enabled: {srv.get('enabled', True)}")
    
    mcp_tools = []
    
    for server_config in mcp_servers:
        # Skip disabled servers
        if not server_config.get("enabled", True):
            logger.info(f"⏭️ MCP_TOOLS: Skipping disabled MCP server '{server_config.get('name', 'unknown')}' for {agent_name}")
            continue
        
        try:
            mcp_client = create_mcp_client_from_config(server_config, agent_name)
            if mcp_client:
                mcp_tools.append(mcp_client)
                logger.info(f"✅ MCP_TOOLS: Created MCP client '{server_config.get('name')}' for {agent_name}")
        except Exception as e:
            logger.error(f"❌ MCP_TOOLS: Failed to create MCP client '{server_config.get('name')}' for {agent_name}: {e}")
            import traceback
            logger.error(f"   Traceback: {traceback.format_exc()}")
    
    return mcp_tools


def create_mcp_client_from_config(server_config: dict, agent_name: str) -> Optional[MCPClient]:
    """
    Create an MCPClient instance from a server configuration.
    
    Supports three transport types:
    - stdio: Command-line tools (uvx, python, npx, etc.)
    - http: HTTP-based MCP servers (Streamable HTTP)
    - sse: Server-Sent Events transport
    
    Args:
        server_config: MCP server configuration dict
        agent_name: Name of the agent (for logging)
        
    Returns:
        MCPClient instance or None if creation fails
    """
    transport = server_config.get("transport", "stdio")
    server_name = server_config.get("name", "unknown")
    prefix = server_config.get("prefix", "")
    
    # Build tool filters if configured
    tool_filters = {}
    if server_config.get("allowedTools"):
        tool_filters["allowed"] = server_config["allowedTools"]
    if server_config.get("rejectedTools"):
        tool_filters["rejected"] = server_config["rejectedTools"]
    
    try:
        if transport == "stdio":
            return create_stdio_mcp_client(server_config, prefix, tool_filters)
        elif transport == "http":
            return create_http_mcp_client(server_config, prefix, tool_filters)
        elif transport == "sse":
            return create_sse_mcp_client(server_config, prefix, tool_filters)
        else:
            logger.error(f"❌ MCP_TOOLS: Unknown transport type '{transport}' for server '{server_name}'")
            return None
    except Exception as e:
        logger.error(f"❌ MCP_TOOLS: Failed to create {transport} MCP client for '{server_name}': {e}")
        return None


def create_stdio_mcp_client(server_config: dict, prefix: str, tool_filters: dict) -> Optional[MCPClient]:
    """
    Create an MCPClient with stdio transport for command-line MCP servers.
    
    Example configuration:
    {
        "transport": "stdio",
        "command": "uvx",
        "args": ["awslabs.aws-documentation-mcp-server@latest"],
        "env": {"FASTMCP_LOG_LEVEL": "ERROR"}
    }
    """
    command = server_config.get("command")
    args = server_config.get("args", [])
    env = server_config.get("env", {})
    
    if not command:
        logger.error(f"❌ MCP_TOOLS: stdio transport requires 'command' field")
        return None
    
    logger.info(f"🔌 MCP_TOOLS: Creating stdio MCP client: {command} {' '.join(args)}")
    
    # Build environment dict - merge with current environment
    full_env = dict(os.environ)
    full_env.update(env)
    
    # Create the MCPClient with stdio transport
    mcp_client_kwargs = {}
    if prefix:
        mcp_client_kwargs["prefix"] = prefix
    if tool_filters:
        mcp_client_kwargs["tool_filters"] = tool_filters
    
    return MCPClient(
        lambda cmd=command, a=args, e=full_env: stdio_client(
            StdioServerParameters(
                command=cmd,
                args=a,
                env=e
            )
        ),
        **mcp_client_kwargs
    )


def create_http_mcp_client(server_config: dict, prefix: str, tool_filters: dict) -> Optional[MCPClient]:
    """
    Create an MCPClient with HTTP (Streamable HTTP) transport.
    
    Supports:
    - AWS IAM authentication via mcp-proxy-for-aws
    - Custom headers (e.g., OAuth tokens)
    
    Example configuration:
    {
        "transport": "http",
        "url": "https://api.example.com/mcp/",
        "headers": {
            "Authorization": "Bearer your-token-here"
        },
        "awsAuth": {
            "region": "us-east-1",
            "service": "bedrock-agentcore"
        }
    }
    """
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
    
    mcp_client_kwargs = {}
    if prefix:
        mcp_client_kwargs["prefix"] = prefix
    if tool_filters:
        mcp_client_kwargs["tool_filters"] = tool_filters
    
    if aws_auth:
        # Use AWS IAM authentication via mcp-proxy-for-aws
        try:
            from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client
            
            aws_region = aws_auth.get("region", os.environ.get("AWS_REGION", "us-east-1"))
            aws_service = aws_auth.get("service", "bedrock-agentcore")
            
            logger.info(f"🔐 MCP_TOOLS: Using AWS IAM auth for {url} (region={aws_region}, service={aws_service})")
            
            return MCPClient(
                lambda u=url, r=aws_region, s=aws_service: aws_iam_streamablehttp_client(
                    endpoint=u,
                    aws_region=r,
                    aws_service=s
                ),
                **mcp_client_kwargs
            )
        except ImportError:
            logger.error(f"❌ MCP_TOOLS: mcp-proxy-for-aws not installed. Install with: pip install mcp-proxy-for-aws")
            return None
    else:
        # Standard HTTP transport (with optional custom headers for auth)
        try:
            from mcp.client.streamable_http import streamablehttp_client
            
            logger.info(f"✅ MCP_TOOLS: Successfully imported streamablehttp_client, creating MCPClient for '{server_name}'")
            
            # If headers are provided, pass them to streamablehttp_client
            if headers:
                logger.info(f"🔑 MCP_TOOLS: Using custom headers for authentication")
                mcp_client = MCPClient(
                    lambda u=url, h=headers: streamablehttp_client(url=u, headers=h),
                    **mcp_client_kwargs
                )
            else:
                mcp_client = MCPClient(
                    lambda u=url: streamablehttp_client(u),
                    **mcp_client_kwargs
                )
            logger.info(f"✅ MCP_TOOLS: MCPClient created successfully for '{server_name}'")
            return mcp_client
        except ImportError as e:
            logger.error(f"❌ MCP_TOOLS: mcp.client.streamable_http not available: {e}")
            return None
        except Exception as e:
            logger.error(f"❌ MCP_TOOLS: Failed to create MCPClient for '{server_name}': {e}")
            import traceback
            logger.error(f"   Traceback: {traceback.format_exc()}")
            return None


def create_sse_mcp_client(server_config: dict, prefix: str, tool_filters: dict) -> Optional[MCPClient]:
    """
    Create an MCPClient with Server-Sent Events (SSE) transport.
    
    Example configuration:
    {
        "transport": "sse",
        "url": "http://localhost:8000/sse"
    }
    """
    url = server_config.get("url")
    
    if not url:
        logger.error(f"❌ MCP_TOOLS: sse transport requires 'url' field")
        return None
    
    logger.info(f"🔌 MCP_TOOLS: Creating SSE MCP client: {url}")
    
    mcp_client_kwargs = {}
    if prefix:
        mcp_client_kwargs["prefix"] = prefix
    if tool_filters:
        mcp_client_kwargs["tool_filters"] = tool_filters
    
    try:
        from mcp.client.sse import sse_client
        
        return MCPClient(
            lambda u=url: sse_client(u),
            **mcp_client_kwargs
        )
    except ImportError:
        logger.error(f"❌ MCP_TOOLS: mcp.client.sse not available")
        return None


def create_agent(agent_name, conversation_context, is_collaborator):
    global orchestrator_instance

    model_inputs = {}
    if is_collaborator:
        model_inputs = get_collaborator_agent_model_inputs(
            agent_name=agent_name, orchestrator_name=orchestrator_instance.agent_name
        )
    else:
        agent_config = get_agent_config(agent_name=agent_name)
        model_inputs = agent_config.get("model_inputs", {}).get(agent_name, {})

    model = BedrockModel(
        model_id=model_inputs.get(
            "model_id", "us.anthropic.claude-sonnet-4-20250514-v1:0"
        ),
        max_tokens=model_inputs.get("max_tokens", 8000),
        temperature=model_inputs.get("temperature", 0.8),
        cache_prompt="default",
        cache_tools="default",
    )

    hooks = []
    if "default" in orchestrator_instance.memory_id:
        logger.info(f"🏗️ CREATE_AGENT: Skipping memory hook for default memory_id")
    else: 
        if not is_collaborator:
            # Normalize actor_id to comply with validation pattern
            normalized_actor_id = agent_name.replace("_", "-")
            hooks = [
                ShortTermMemoryHook(
                    memory_client=client,
                    memory_id=orchestrator_instance.memory_id,
                    actor_id=normalized_actor_id,
                    session_id=orchestrator_instance.session_id,
                )
            ]

    # Load base instructions and add conversation context if available
    base_instructions = load_instructions_for_agent(agent_name=agent_name)
    enhanced_system_prompt = base_instructions + conversation_context

    # Build tools list based on agent_tools configuration
    tools = build_tools_for_agent(agent_name)
    
    collaborator_config = get_collaborator_agent_config(
        agent_name=agent_name, orchestrator_name=orchestrator_instance.agent_name
    )
    if collaborator_config is None:
        collaborator_config = get_agent_config(agent_name=agent_name)

    # Normalize actor_id to comply with validation pattern
    normalized_actor_id = agent_name.replace("_", "-")

    # Create conversation manager for collaborator agents
    # Use AgentCoreMemoryConversationManager when memory is configured
    if "default" not in orchestrator_instance.memory_id.lower():
        conversation_manager = create_agentcore_memory_manager(
            memory_id=orchestrator_instance.memory_id,
            actor_id=normalized_actor_id,
            session_id=orchestrator_instance.session_id,
            region_name=os.environ.get("AWS_REGION", "us-east-1"),
            use_summarizing_fallback=False,
        )
    else:
        conversation_manager = None  # Use default behavior

    return Agent(
        model=model,
        name=agent_name,
        system_prompt=enhanced_system_prompt,
        tools=tools,
        description=collaborator_config.get("agent_description", ""),
        hooks=hooks,
        conversation_manager=conversation_manager,
        state={
            "session_id": orchestrator_instance.session_id,
            "actor_id": normalized_actor_id,
            "memory_id": orchestrator_instance.memory_id,
        },
    )


session = boto3.session.Session()
credentials = session.get_credentials()


def transform_response_handler(**event):
    logger.info("transforming response to a structured result")
    yield (ResponseModel.parse_event_loop_structure_to_response_model(event))


def set_session_context(session_id):
    """Set the session ID in OpenTelemetry baggage for trace correlation"""
    ctx = baggage.set_baggage("session.id", session_id)
    token = context.attach(ctx)
    logging.info(f"Session ID '{session_id}' attached to telemetry context")
    return token


# Global variable to collect sources from tool calls
collected_sources = {}

# Agent context storage for maintaining conversation history across agent switches
# Structure: {session_id: {agent_name: List[messages]}}
# This enables continuous conversation when users switch between agent types
agent_context_store: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}

# Maximum messages to store per agent context (to prevent unbounded memory growth)
MAX_CONTEXT_MESSAGES = 30


def get_context_store_stats() -> Dict[str, Any]:
    """Get statistics about the current context store for debugging."""
    global agent_context_store
    stats = {
        "total_sessions": len(agent_context_store),
        "sessions": {}
    }
    for session_id, agents in agent_context_store.items():
        stats["sessions"][session_id] = {
            "agents": list(agents.keys()),
            "message_counts": {agent: len(msgs) for agent, msgs in agents.items()}
        }
    return stats


def clear_session_context(session_id: str) -> bool:
    """Clear all agent contexts for a specific session."""
    global agent_context_store
    if session_id in agent_context_store:
        del agent_context_store[session_id]
        logger.info(f"🗑️ CONTEXT_CLEAR: Cleared all contexts for session {session_id}")
        return True
    return False


def trim_context_messages(messages: List[Dict[str, Any]], max_messages: int = MAX_CONTEXT_MESSAGES) -> List[Dict[str, Any]]:
    """
    Trim messages to prevent unbounded memory growth.
    Keeps the most recent messages while preserving conversation flow.
    """
    if len(messages) <= max_messages:
        return messages
    
    # Keep the most recent messages
    trimmed = messages[-max_messages:]
    logger.info(f"✂️ CONTEXT_TRIM: Trimmed {len(messages) - max_messages} old messages, keeping {len(trimmed)}")
    return trimmed


class GenericAgent:
    def __init__(self):
        self.logger = logger or logging.getLogger(__name__)
        self.region = region or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")

        # Initialize memory-related properties
        self.bedrock_client = boto3.client("bedrock-runtime")

        # Initialize conversation context for memory integration
        self.conversation_context = ""
        # Get memory ID from SSM Parameter Store (falls back to env var)
        self.memory_id = get_memory_id_from_ssm()
        self.session_id = "new_session"

        # Initialize sources collection
        global collected_sources
        collected_sources = {}
        global CONFIG
        # Create the summarizing conversation manager with default settings

    def create_orchestrator(self, session_id, memory_id, agent_name, saved_messages: Optional[List[Dict[str, Any]]] = None):
        """
        Create an orchestrator agent with optional conversation history restoration.
        
        Args:
            session_id: The session identifier (shared across all agents in a conversation)
            memory_id: The AgentCore memory identifier
            agent_name: Name of the agent to create
            saved_messages: Optional list of previous messages to restore conversation context
        """
        if agent_name == "default":
            return Agent()
        self.agent_name = agent_name
        # Load configuration
        try:
            config = get_agent_config(agent_name=agent_name)
            self.team_name = config.get("team_name", "")
        except Exception as e:
            config = {
                "agent_name": agent_name,
                "agent_display_name": agent_name,
                "agent_description": "Default agent description",
                "team_name": "Default team",
                "use_handler_template": True,
                "tool_agent_names": [],
                "external_agents": [],
                "model_inputs": {
                    f"{agent_name}": {
                        "model_id": "us.anthropic.claude-sonnet-4-20250514-v1:0",
                        "max_tokens": 12000,
                        "temperature": 0.3
                    }
                },
            }

        # Extract model inputs
        try:
            model_inputs = config.get("model_inputs", {}).get(agent_name, {})
        except Exception as e:
            model_inputs = {
                "model_id": "us.anthropic.claude-sonnet-4-20250514-v1:0",
                "max_tokens": 12000,
                "temperature": 0.3
                }

        try:
            model = BedrockModel(
                model_id=model_inputs.get(
                    "model_id", "us.anthropic.claude-sonnet-4-20250514-v1:0"
                ),
                max_tokens=model_inputs.get("max_tokens", 12000),
                cache_prompt="default",
                cache_tools="default",
            )
            if model_inputs.get("temperature"):
                model.temperature = model_inputs.get("temperature")
        except Exception as e:
            logger.error(f"✗ Failed to create Bedrock model: {e}")
            import traceback

            logger.error(f"   Traceback: {traceback.format_exc()}")
        # Setup memory hooks
        hooks = []
        self.session_id = session_id
        self.memory_id = memory_id

        if "default" in memory_id:
            logger.info(f"⊘ Skipping memory hook for default memory_id")
        else:
            try:
                # Normalize actor_id to comply with validation pattern
                normalized_actor_id = agent_name.replace("_", "-")
                hooks = [
                    ShortTermMemoryHook(
                        client, memory_id, normalized_actor_id, session_id
                    )
                ]
            except Exception as e:
                logger.error(f"✗ Failed to create memory hook: {e}")
                import traceback

                logger.error(f"   Traceback: {traceback.format_exc()}")

        # Load instructions
        logger.info(f"\n📝 Loading agent instructions...")
        print(f"\n📝 Loading agent instructions...")
        try:
            base_instructions = load_instructions_for_agent(agent_name=agent_name)

            instruction_length = len(base_instructions) if base_instructions else 0
            if instruction_length == 0:
                logger.warning(f"⚠️  WARNING: Instructions are empty!")
        except Exception as e:
            logger.error(f"✗ Failed to load instructions: {e}")
            import traceback

            logger.error(f"   Traceback: {traceback.format_exc()}")

        try:
            enhanced_system_prompt = base_instructions + self.conversation_context
        except Exception as e:
            logger.error(f"✗ Failed to build system prompt: {e}")
            print(f"✗ Failed to build system prompt: {e}")
        try:
            # Use build_tools_for_agent to get tools from agent_tools configuration
            # This ensures tools are driven by global_configuration.json, not hardcoded
            tools = build_tools_for_agent(agent_name)
            logger.info(f"🔧 CREATE_ORCHESTRATOR: {agent_name} configured with {len(tools)} tools from config")
        except Exception as e:
            logger.error(f"✗ Failed to build tools list: {e}")

        try:
            agent_description = config.get("agent_description", "")

            # Normalize actor_id to comply with validation pattern
            actor_name = agent_name or config.get("agent_id", config.get("agent_name"))
            normalized_actor_id = actor_name.replace("_", "-")

            # Create AgentCore Memory Conversation Manager for persistent session management
            # This provides:
            # 1. Automatic persistence of conversation history to AgentCore Memory
            # 2. Retrieval of conversation history when resuming sessions (survives process restarts)
            # 3. Falls back to SummarizingConversationManager for context window management
            if "default" not in self.memory_id.lower():
                conversation_manager = create_agentcore_memory_manager(
                    memory_id=self.memory_id,
                    actor_id=normalized_actor_id,
                    session_id=self.session_id,
                    region_name=os.environ.get("AWS_REGION", "us-east-1"),
                    use_summarizing_fallback=True,  # Use SummarizingConversationManager for context reduction
                )
                logger.info(
                    f"✅ Using AgentCoreMemoryConversationManager for {agent_name} "
                    f"(memory_id={self.memory_id}, session_id={self.session_id})"
                )
            else:
                # Fall back to simple SummarizingConversationManager when memory is not configured
                conversation_manager = SummarizingConversationManager(
                    summary_ratio=0.3,
                    preserve_recent_messages=5,
                    summarization_system_prompt="summarize the current conversation context.",
                )
                logger.info(f"⊘ Using SummarizingConversationManager (no memory configured) for {agent_name}")

            # Build agent kwargs
            agent_kwargs = {
                "model": model,
                "name": actor_name,
                "system_prompt": enhanced_system_prompt,
                "tools": tools,
                "description": agent_description,
                "hooks": hooks,
                "state": {
                    "session_id": self.session_id,
                    "actor_id": normalized_actor_id,
                    "memory_id": self.memory_id,
                },
                "conversation_manager": conversation_manager,
            }
            
            # Restore conversation history - priority order:
            # 1. In-memory saved_messages (for fast agent switching within same process)
            # 2. AgentCore Memory (for persistence across process restarts)
            if saved_messages:
                agent_kwargs["messages"] = saved_messages
                logger.info(f"📂 CONTEXT_RESTORE: Restored {len(saved_messages)} in-memory messages for {agent_name}")
            elif hasattr(conversation_manager, 'restore_from_session') and "default" not in self.memory_id.lower():
                # Explicitly restore from AgentCore Memory if no in-memory messages
                try:
                    logger.info(f"📂 CONTEXT_RESTORE: Attempting to restore from AgentCore Memory for {agent_name}...")
                    restored_messages = conversation_manager.restore_from_session({})
                    if restored_messages:
                        agent_kwargs["messages"] = restored_messages
                        logger.info(f"📂 CONTEXT_RESTORE: Restored {len(restored_messages)} messages from AgentCore Memory for {agent_name}")
                    else:
                        logger.info(f"📂 CONTEXT_RESTORE: No messages found in AgentCore Memory for {agent_name}")
                except Exception as restore_err:
                    logger.warning(f"⚠️ CONTEXT_RESTORE: Failed to restore from AgentCore Memory: {restore_err}")
            
            agent = Agent(**agent_kwargs)
            return agent
        except Exception as e:
            logger.error(f"CREATE_ORCHESTRATOR: FAILED")
            logger.error(f"Error: {e}")
            import traceback

            logger.error(f"\nFull traceback:")
            logger.error(traceback.format_exc())
            logger.error(f"{'='*80}\n")


print(f"DEBUG: Module load - All env vars: {list(os.environ.keys())}")
print("=" * 60)
print("🔍 MODULE LOAD - ENVIRONMENT VARIABLES:")
for key, value in sorted(os.environ.items()):
    # Mask sensitive values but show they exist
    if any(sensitive in key.upper() for sensitive in ['SECRET', 'PASSWORD', 'TOKEN', 'KEY', 'CREDENTIAL']):
        print(f"   {key} = ***MASKED*** (length={len(value)})")
    else:
        # Truncate long values for readability
        display_value = value[:100] + '...' if len(value) > 100 else value
        print(f"   {key} = {display_value}")
print("=" * 60)

# Create the orchestrator instance
orchestrator_instance = GenericAgent()
agent = None  # Will be lazily initialized on first invocation
current_agent_name = None  # Track which agent is currently loaded


def _flush_log(message: str, level: str = "INFO"):
    """Force-flush a log message to ensure it appears in CloudWatch immediately."""
    import sys
    try:
        timestamp = datetime.now().isoformat()
        formatted = f"[{timestamp}] {level}: {message}"
        print(formatted, flush=True)
        sys.stdout.flush()
        sys.stderr.flush()
        if level == "ERROR":
            logger.error(message)
        elif level == "WARNING":
            logger.warning(message)
        elif level == "DEBUG":
            logger.debug(message)
        else:
            logger.info(message)
    except Exception as log_err:
        # Last resort - just print
        print(f"LOG_ERROR: {log_err} | Original message: {message}", flush=True)


@app.entrypoint
async def agent_invocation(payload, context):
    """
    Invoke the orchestrator, unless directed otherwise
    Returns complete response chunks instead of streaming tokens
    """
    
    try:
        global collected_sources
        global agent
        global CONFIG
        global GLOBAL_CONFIG
        global memory_id
        global orchestrator_instance
        global current_agent_name
        
        # ============================================
        # CHECK FOR CACHE REFRESH REQUEST
        # ============================================
        # If the payload contains refresh_cache=True, refresh all caches
        # to load the latest agent configurations from DynamoDB
        refresh_cache_requested = payload.get("refresh_cache", False)
        force_reinitialize = payload.get("force_reinitialize", False)
        get_diagnostics = payload.get("get_cache_diagnostics", False)
        
        # Handle cache diagnostics request
        if get_diagnostics:
            _flush_log("🔍 AGENT_INVOCATION: Cache diagnostics requested")
            try:
                diagnostics = get_cache_diagnostics()
                yield {
                    "type": "cache_diagnostics",
                    "data": diagnostics,
                    "timestamp": datetime.now().isoformat(),
                    "message": "Cache diagnostics retrieved successfully"
                }
                return
            except Exception as diag_err:
                _flush_log(f"❌ AGENT_INVOCATION: Cache diagnostics failed: {diag_err}", "ERROR")
                yield {"type": "error", "message": f"Failed to get cache diagnostics: {diag_err}"}
                return
        
        if refresh_cache_requested:
            _flush_log("🔄 AGENT_INVOCATION: Cache refresh requested via payload flag")
            try:
                refresh_stats = refresh_all_caches(force_reinitialize=force_reinitialize)
                _flush_log(f"✅ AGENT_INVOCATION: Cache refresh completed: {refresh_stats.get('items_reloaded', {})}")
                
                # CRITICAL: Null out the agent so it gets recreated with fresh config
                # Without this, the old agent object (with stale tools/instructions) persists
                agent = None
                _flush_log("🔄 AGENT_INVOCATION: Agent nulled — will be recreated with fresh config")
                
                # If this is a cache-refresh-only request (no prompt), return the stats with diagnostics
                if not payload.get("prompt"):
                    _flush_log("📤 AGENT_INVOCATION: Cache refresh only request - returning stats")
                    # Include diagnostics in the response for verification
                    diagnostics = get_cache_diagnostics()
                    yield {
                        "type": "cache_refresh_complete",
                        "data": refresh_stats,
                        "diagnostics": diagnostics,
                        "timestamp": datetime.now().isoformat(),
                        "message": "Cache refresh completed successfully"
                    }
                    return
                    
            except Exception as refresh_err:
                _flush_log(f"❌ AGENT_INVOCATION: Cache refresh failed: {refresh_err}", "ERROR")
                # Continue with the request even if cache refresh fails
                # The agent will use whatever cached data is available
        
        # Process the prompt - it can be a string or a list of content blocks
        raw_prompt = payload.get("prompt")
        media = payload.get("media", {})

        # Parse the prompt to extract text and file attachments
        user_input = ""
        file_attachments = []
        seen_files = set()  # Track file names to avoid duplicates
        
        if isinstance(raw_prompt, str):
            # Simple string prompt
            user_input = raw_prompt
        elif isinstance(raw_prompt, list):
            # Content blocks format (Bedrock format)
            for idx, block in enumerate(raw_prompt):
                _flush_log(f"📝 AGENT_INVOCATION: Processing block {idx}: type={type(block)}")
                if isinstance(block, dict):
                    if "text" in block:
                        # Extract text content
                        text_content = block["text"]
                        user_input = text_content
                        _flush_log(f"📝 AGENT_INVOCATION: Found text block, length={len(text_content)}")
                    if "document" in block:
                        # Extract document content
                        _flush_log(f"📝 AGENT_INVOCATION: Found document block")
                        document_content = block["document"]
                        file_attachments.append(document_content)
                elif isinstance(block, str):
                    user_input += block
        else:
            _flush_log(f"📝 AGENT_INVOCATION: Prompt is other type: {type(raw_prompt)}")
            user_input = str(raw_prompt)
        _flush_log(f"📝 AGENT_INVOCATION: user_input length={len(user_input)}, file_attachments={len(file_attachments)}")
        
        # Process file attachments - download from S3 and convert to bytes
        processed_attachments = []
        s3_client = boto3.client(
            "s3", region_name=os.environ.get("AWS_REGION", "us-east-1")
        )

        # Check for explicit direct mention flag from frontend (clean approach)
        direct_mention_target = payload.get("direct_mention_target")
        direct_mention_mode = False

        # Clear collected sources from previous invocations
        collected_sources = {}

        # Extract session information from payload for memory integration
        _flush_log("🔍 AGENT_INVOCATION: Extracting session info from payload...")
        _flush_log(f"🔍 AGENT_INVOCATION: Raw payload keys: {list(payload.keys()) if isinstance(payload, dict) else 'not a dict'}")
        try:
            session_id, extracted_memory_id, agent_name = (
                extract_session_id_and_memory_id_and_actor_from_payload(payload)
            )
            _flush_log(f"🔍 AGENT_INVOCATION: Extracted - session_id={session_id}, memory_id={extracted_memory_id}, agent_name={agent_name}")
            
            # Log the raw memory_id from payload for debugging
            raw_memory_id = payload.get("memory_id") or payload.get("session_metadata", {}).get("memory_id")
            _flush_log(f"🔍 AGENT_INVOCATION: Raw memory_id from payload: '{raw_memory_id}'")
            
            if not extracted_memory_id or "default" in str(extracted_memory_id).lower():
                _flush_log(f"⚠️ AGENT_INVOCATION: Memory ID is empty or default - conversation persistence may not work!", "WARNING")
            elif "-" not in str(extracted_memory_id):
                _flush_log(f"⚠️ AGENT_INVOCATION: Memory ID '{extracted_memory_id}' missing '-' character - was resolved from SSM", "WARNING")
            else:
                _flush_log(f"✅ AGENT_INVOCATION: Valid memory_id received: {extracted_memory_id}")
        except Exception as extract_err:
            _flush_log(f"❌ AGENT_INVOCATION: Failed to extract session info: {extract_err}", "ERROR")
            import traceback
            _flush_log(f"❌ AGENT_INVOCATION: Traceback: {traceback.format_exc()}", "ERROR")
            raise

        _flush_log("📂 AGENT_INVOCATION: Using pre-loaded global configuration...")
        # Use pre-loaded GLOBAL_CONFIG instead of loading again
        if not GLOBAL_CONFIG:
            GLOBAL_CONFIG = load_configs("global_configuration.json")
        _flush_log(f"📂 AGENT_INVOCATION: GLOBAL_CONFIG keys: {list(GLOBAL_CONFIG.keys()) if GLOBAL_CONFIG else 'None'}")

        _flush_log(f"📂 AGENT_INVOCATION: Getting agent config for {agent_name}...")
        CONFIG = get_agent_config(agent_name=agent_name)
        _flush_log(f"📂 AGENT_INVOCATION: CONFIG keys: {list(CONFIG.keys()) if CONFIG else 'None'}")

        # Check if we need to create or recreate the agent
        agent_type_changed = (current_agent_name is not None and current_agent_name != agent_name)
        _flush_log(f"🔄 AGENT_INVOCATION: agent_type_changed={agent_type_changed}, current={current_agent_name}, new={agent_name}")
        
        # Access global context store
        global agent_context_store
        
        # Determine the session key for context storage (use session_id, shared across all agents)
        context_session_key = session_id if session_id else "default_session"
        _flush_log(f"🔄 AGENT_INVOCATION: context_session_key={context_session_key}")
        
        if agent is None or agent_type_changed:
            _flush_log(f"🏗️ AGENT_INVOCATION: Need to create agent (agent is None: {agent is None}, type_changed: {agent_type_changed})")
            # Need to create a new agent (first time or agent type changed)
            
            # SAVE current agent's context before switching (if switching)
            if agent_type_changed and agent is not None and current_agent_name:
                # Initialize session entry if needed
                if context_session_key not in agent_context_store:
                    agent_context_store[context_session_key] = {}
                
                # Deep copy messages to preserve state
                try:
                    saved_messages = copy.deepcopy(agent.messages) if hasattr(agent, 'messages') and agent.messages else []
                    # Trim to prevent unbounded memory growth
                    saved_messages = trim_context_messages(saved_messages, 8)
                    agent_context_store[context_session_key][current_agent_name] = saved_messages
                    _flush_log(f"💾 CONTEXT_SAVE: Saved {len(saved_messages)} messages for {current_agent_name}")
                except Exception as e:
                    _flush_log(f"⚠️ CONTEXT_SAVE: Failed to save context for {current_agent_name}: {e}", "WARNING")
            
            if agent_type_changed:
                _flush_log(f"🔄 Agent type changed from {current_agent_name} to {agent_name}")
            
            # Set up session info
            if session_id:
                orchestrator_instance.session_id = session_id
                orchestrator_instance.memory_id = extracted_memory_id
                orchestrator_instance.direct_mention_mode = direct_mention_mode
                orchestrator_instance.direct_mention_target = direct_mention_target
                memory_id = extracted_memory_id
            else:
                orchestrator_instance.session_id = "new_session-12345678901234567890"
                orchestrator_instance.memory_id = "default"
                memory_id = "default"
            
            # RESTORE saved context for the new agent (if available)
            saved_messages = None
            if context_session_key in agent_context_store and agent_name in agent_context_store[context_session_key]:
                saved_messages = agent_context_store[context_session_key][agent_name]
                _flush_log(f"📂 CONTEXT_RESTORE: Found {len(saved_messages)} saved messages for {agent_name}")
            
            # Create agent with restored context
            _flush_log(f"🏗️ AGENT_INVOCATION: Calling create_orchestrator for {agent_name}...")
            try:
                agent = orchestrator_instance.create_orchestrator(
                    orchestrator_instance.session_id, 
                    orchestrator_instance.memory_id, 
                    agent_name,
                    saved_messages=saved_messages
                )
                _flush_log(f"✅ AGENT_INVOCATION: Agent created successfully, type={type(agent)}")
            except Exception as create_err:
                _flush_log(f"❌ AGENT_INVOCATION: create_orchestrator failed: {create_err}", "ERROR")
                import traceback
                _flush_log(f"❌ AGENT_INVOCATION: Traceback: {traceback.format_exc()}", "ERROR")
                raise
            
            current_agent_name = agent_name
            _flush_log(f"✅ Agent created for {agent_name} with session {orchestrator_instance.session_id}")
        else:
            # Agent already exists and type hasn't changed, just update session info if needed
            _flush_log(f"♻️ AGENT_INVOCATION: Reusing existing agent {agent_name}")
            if session_id:
                orchestrator_instance.session_id = session_id
                orchestrator_instance.memory_id = extracted_memory_id
                orchestrator_instance.direct_mention_mode = direct_mention_mode
                orchestrator_instance.direct_mention_target = direct_mention_target
                memory_id = extracted_memory_id
                
                # Update agent state without recreating
                normalized_actor_id = agent_name.replace("_", "-")
                agent.state = {
                    "session_id": session_id,
                    "actor_id": normalized_actor_id,
                    "memory_id": extracted_memory_id,
                }
                
                # CRITICAL: Update the conversation manager's session info for memory persistence
                if hasattr(agent, 'conversation_manager') and agent.conversation_manager:
                    if hasattr(agent.conversation_manager, 'update_session_info'):
                        agent.conversation_manager.update_session_info(
                            actor_id=normalized_actor_id,
                            session_id=session_id
                        )
                        _flush_log(f"♻️ Updated conversation_manager session info for {agent_name}")
                    # Also update memory_id if the manager has it
                    if hasattr(agent.conversation_manager, 'memory_id'):
                        agent.conversation_manager.memory_id = extracted_memory_id
                        _flush_log(f"♻️ Updated conversation_manager memory_id to {extracted_memory_id}")
                    
                    # Retrieve conversation history from memory and inject into system prompt
                    if hasattr(agent.conversation_manager, 'retrieve_conversation_history'):
                        try:
                            history = agent.conversation_manager.retrieve_conversation_history()
                            if history and len(history) > 0:
                                # Format history for context injection
                                # Only include clean text messages, skip tool-related content
                                context_messages = []
                                for msg in history:
                                    role = msg.get("role", "user").title()
                                    content = msg.get("content", [])
                                    if isinstance(content, list) and len(content) > 0:
                                        text = content[0].get("text", "") if isinstance(content[0], dict) else str(content[0])
                                    else:
                                        text = str(content)
                                    
                                    # Skip tool-related messages entirely
                                    if any(marker in text for marker in [
                                        "'toolUse'", '"toolUse"', "toolUse",
                                        "'toolResult'", '"toolResult"', "toolResult",
                                        "tooluse_", "tool_use_id"
                                    ]):
                                        continue
                                    
                                    # Skip empty or very short messages
                                    if not text or len(text.strip()) < 3:
                                        continue
                                    
                                    # Truncate long messages for context
                                    context_messages.append(f"{role}: {text[:500]}")
                                
                                if context_messages:
                                    history_context = "\n".join(context_messages[-10:])  # Last 10 messages
                                    if hasattr(agent, 'system_prompt') and agent.system_prompt:
                                        agent.system_prompt += f"\n\n## Recent Conversation History\n{history_context}\n\nContinue the conversation naturally based on this context."
                                        _flush_log(f"📂 Injected {len(context_messages)} history messages into system prompt")
                        except Exception as hist_err:
                            _flush_log(f"⚠️ Failed to retrieve/inject conversation history: {hist_err}", "WARNING")
                
                _flush_log(f"♻️ Agent reused for {agent_name} with session {session_id}")
        
        if session_id:
            try:
                context_token = set_session_context(session_id)
                _flush_log(f"🔧 AGENT_INVOCATION: Session context set for {session_id}")
            except Exception as ctx_err:
                _flush_log(f"⚠️ AGENT_INVOCATION: Failed to set session context: {ctx_err}", "WARNING")
                context_token = None
        
        # Get tool agent names from config
        stream = payload.get("stream", True)
        _flush_log(f"🎬 AGENT_INVOCATION: stream={stream}, about to invoke agent...")

        if stream:
            _flush_log("🎬 AGENT_INVOCATION: Starting STREAMING mode...")
            try:
                # Build the input for the agent
                agent_input = user_input
                _flush_log(f"🎬 AGENT_INVOCATION: agent_input length={len(str(agent_input))}")

                # If there are file attachments, format them for the agent
                if file_attachments:
                    _flush_log(f"📎 AGENT_INVOCATION: Processing {len(file_attachments)} file attachments...")
                    # Convert to ConverseStream content format
                    content_blocks = []

                    # Process each document attachment
                    document_analyses = []
                    for file_info in file_attachments:
                        try:
                            # Extract S3 location from document block
                            if isinstance(file_info, dict) and "source" in file_info:
                                s3_location = file_info.get("source", {}).get(
                                    "s3Location", {}
                                )
                                s3_uri = s3_location.get("uri", "")

                                if s3_uri.startswith("s3://"):
                                    # Parse S3 URI: s3://bucket/key
                                    s3_parts = s3_uri[5:].split("/", 1)
                                    if len(s3_parts) == 2:
                                        bucket_name = s3_parts[0]
                                        object_key = s3_parts[1]

                                        # Pre-process the document
                                        _flush_log(f"📎 Pre-processing document from s3://{bucket_name}/{object_key}")
                                        analysis = get_s3_as_base64_and_extract_summary_and_facts(
                                            bucket_name, object_key
                                        )

                                        if analysis:
                                            document_name = file_info.get("name", "document")
                                            document_analyses.append(
                                                f"\n\n--- Document: {document_name} ---\n{analysis}"
                                            )
                                            _flush_log(f"📎 Successfully pre-processed document: {document_name}")
                        except Exception as e:
                            _flush_log(f"❌ Failed to pre-process document: {e}", "ERROR")

                    # Append document analyses to user input
                    if document_analyses:
                        enhanced_input = (
                            user_input
                            + "\n\nHere is additional context from attached documents I pre-processed for you:"
                            + "".join(document_analyses)
                        )
                        content_blocks.append({"text": enhanced_input})
                    else:
                        content_blocks.append({"text": user_input})

                    # Add cache point if needed
                    content_blocks.append({"cachePoint": {"type": "default"}})
                    agent_input = content_blocks

                _flush_log(f"🎬 AGENT_INVOCATION: Calling agent.stream_async()...")
                stream_obj = agent.stream_async(agent_input)
                _flush_log(f"🎬 AGENT_INVOCATION: stream_async returned, type={type(stream_obj)}")
                event_count = 0

                events_yielded = False
                _flush_log("🎬 AGENT_INVOCATION: Starting async iteration over stream...")
                async for event in stream_obj:
                    event_count += 1
                    if event_count <= 3:  # Log first few events
                        _flush_log(f"🎬 AGENT_INVOCATION: Event #{event_count}, keys={list(event.keys()) if isinstance(event, dict) else type(event)}")
                    if event.get("message") and event.get("message").get("content"):
                        event["teamName"] = orchestrator_instance.team_name
                        yield event
                        # After stream completes, yield sources as a separate event
                        if collected_sources and collected_sources != {}:
                            _flush_log(f"📦 STREAM: Yielding sources")
                            yield {"type": "sources", "sources": collected_sources}
                
                _flush_log(f"✅ STREAM: Completed with {event_count} events")
                
            except Exception as e:
                _flush_log(f"❌ STREAM: Streaming failed: {e}", "ERROR")
                import traceback
                _flush_log(f"❌ STREAM: Traceback: {traceback.format_exc()}", "ERROR")
                _flush_log("⚠️ STREAM: Falling back to non-streaming mode")
                try:
                    # Build the input for the agent (same as streaming path)
                    agent_input = user_input
                    if file_attachments:
                        content_blocks = [{"text": user_input}]
                        for file_info in file_attachments:
                            content_blocks.append({"document": file_info})
                        content_blocks.append({"cachePoint": {"type": "default"}})
                        agent_input = content_blocks

                    _flush_log(f"🔄 FALLBACK: About to call agent() with input type: {type(agent_input)}")
                    # Fallback to non-streaming response
                    response = agent(agent_input)
                    _flush_log(f"🔄 FALLBACK: agent() returned: {type(response)}")
                    
                    # Safely extract response text
                    try:
                        if hasattr(response, "message") and response.message:
                            content = response.message.get("content")
                            if isinstance(content, list) and len(content) > 0:
                                if isinstance(content[0], dict) and "text" in content[0]:
                                    response_text = content[0]["text"]
                                else:
                                    response_text = str(content[0])
                            elif isinstance(content, str):
                                response_text = content
                            else:
                                response_text = str(content)
                        else:
                            response_text = "No response content available"
                    except (KeyError, IndexError, AttributeError) as e:
                        response_text = f"Error extracting response content: {e}"

                    # Yield the response
                    yield response_text

                    # Yield sources after response
                    if collected_sources:
                        _flush_log(f"📦 FALLBACK: Yielding sources with {len(collected_sources)} sources")
                        yield {"type": "sources", "sources": collected_sources}
                        
                except Exception as fallback_error:
                    _flush_log(f"❌ FALLBACK: Non-streaming failed: {fallback_error}", "ERROR")
                    import traceback
                    _flush_log(f"❌ FALLBACK: Traceback: {traceback.format_exc()}", "ERROR")
                    # Yield an error message
                    yield f"Error processing request: {fallback_error}"
        else:
            # Non-streaming path
            _flush_log("🎬 AGENT_INVOCATION: Starting NON-STREAMING mode...")
            try:
                # Build the input for the agent (same as streaming path)
                agent_input = user_input
                if file_attachments:
                    content_blocks = [{"text": user_input}]
                    for file_info in file_attachments:
                        content_blocks.append({"document": file_info})
                    content_blocks.append({"cachePoint": {"type": "default"}})
                    agent_input = content_blocks

                response = agent(agent_input)
                _flush_log(f"✅ NON-STREAM: Complete response received")

                # Extract the response text
                response_text = ""
                if hasattr(response, "message") and response["message"]:
                    content = response.message.get("content", [])
                    if content and len(content) > 0:
                        response_text = content[0].get("text", "")
                else:
                    response_text = str(response)

                if response_text:
                    # The response may contain agent-message tags from the tools
                    # Split by lines and yield each meaningful chunk
                    lines = response_text.split("\n")
                    current_chunk = ""

                    for line in lines:
                        line_stripped = line.strip()
                        if not line_stripped:
                            continue

                        # Check if this line contains an agent message tag
                        if "<agent-message agent=" in line_stripped:
                            # Yield any accumulated chunk first
                            if current_chunk.strip():
                                yield f"💬 RESPONSE: {current_chunk.strip()}"
                                current_chunk = ""

                            # Yield the agent message as-is
                            yield line_stripped

                        else:
                            # Accumulate regular content
                            current_chunk += line_stripped + " "

                            # Yield when we have a complete thought
                            if (
                                line_stripped.endswith((".", "!", "?"))
                                and len(current_chunk.strip()) > 50
                            ):
                                yield f"💬 RESPONSE: {current_chunk.strip()}"
                                current_chunk = ""

                    # Yield any remaining content
                    if current_chunk.strip():
                        yield f"💬 RESPONSE: {current_chunk.strip()}"

                else:
                    yield "💬 RESPONSE: Analysis completed successfully."

                # Yield sources at the end
                if collected_sources:
                    yield {"type": "sources", "sources": collected_sources}

            except Exception as response_error:
                _flush_log(f"❌ NON-STREAM: Response processing failed: {response_error}", "ERROR")
                import traceback
                _flush_log(f"❌ NON-STREAM: Traceback: {traceback.format_exc()}", "ERROR")
                yield f"❌ ERROR: {response_error}"
            finally:
                # Detach context when done
                try:
                    if context_token:
                        context.detach(context_token)
                        _flush_log(f"🔧 AGENT_INVOCATION: Session context detached")
                except Exception as detach_err:
                    _flush_log(f"⚠️ AGENT_INVOCATION: Failed to detach context: {detach_err}", "WARNING")
    
    except Exception as top_level_error:
        # Top-level exception handler to catch ANY unhandled errors
        _flush_log(f"💥 AGENT_INVOCATION: TOP-LEVEL EXCEPTION: {top_level_error}", "ERROR")
        import traceback
        _flush_log(f"💥 AGENT_INVOCATION: Full traceback:\n{traceback.format_exc()}", "ERROR")
        yield f"❌ FATAL ERROR: {top_level_error}"


# resolver = RuntimeARNResolver(unique_id=os.environ.get("UNIQUE_ID",'1234'), stack_prefix=os.environ.get("STACK_PREFIX",'sim'))
# runtime_url = resolver.resolve_runtime_endpoint(CONFIG.get('agent_id', CONFIG.get('agent_name')))

# host, port = "0.0.0.0", 9000

# Pass runtime_url to http_url parameter AND use serve_at_root=True
# a2a_server = A2AServer(
#    agent=agent,
#    http_url=runtime_url.replace('/invocations',''),
#    serve_at_root=True,  # Serves locally at root (/) regardless of remote URL path complexity
#    port=9000
# )
if __name__ == "__main__":
    app.run()
