"""
DynamoDB-based configuration loader for AgentCore agents.

Provides fast access to agent configurations stored in DynamoDB:
- Agent instructions (system prompts)
- Agent cards (metadata and descriptions)
- Visualization maps and templates
- Global configuration

DynamoDB Table Schema (AgentConfig table):
- pk (partition key): Config type prefix (e.g., "INSTRUCTION#AgentName", "CARD#AgentName")
- sk (sort key): Version or sub-item identifier (e.g., "v1", "template#metrics")
- config_type: GSI for querying by type ("instruction", "card", "visualization", "global_config")
- content: The actual configuration content (text or JSON string)
- updated_at: ISO timestamp of last update

Config Types:
- INSTRUCTION#{agent_name} / v1 -> Agent instruction text
- CARD#{agent_name} / v1 -> Agent card JSON
- VIZ_MAP#{agent_name} / v1 -> Visualization map JSON
- VIZ_TEMPLATE#{agent_name} / {template_id} -> Visualization template JSON
- GLOBAL_CONFIG / v1 -> Global configuration JSON
"""

import os
import json
import boto3
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# Module-level caches
_dynamodb_client = None
_dynamodb_table = None
_ssm_client = None
_config_cache: Dict[str, Any] = {}
_cache_initialized = False

# Config type constants
CONFIG_TYPE_INSTRUCTION = "instruction"
CONFIG_TYPE_CARD = "card"
CONFIG_TYPE_VIZ_MAP = "visualization_map"
CONFIG_TYPE_VIZ_TEMPLATE = "visualization_template"
CONFIG_TYPE_GLOBAL = "global_config"


def get_agent_config_table_name() -> Optional[str]:
    """Get the DynamoDB table name for agent configurations."""
    # First check environment variable (set by AgentCore runtime)
    table_name = os.environ.get("AGENT_CONFIG_TABLE")
    if table_name:
        return table_name
    
    # Fall back to constructing from stack prefix and unique ID
    stack_prefix = os.environ.get("STACK_PREFIX", "")
    unique_id = os.environ.get("UNIQUE_ID", "")
    if stack_prefix and unique_id:
        return f"{stack_prefix}-AgentConfig-{unique_id}"
    
    return None


def get_dynamodb_client():
    """Get or create DynamoDB client."""
    global _dynamodb_client
    if _dynamodb_client is None:
        _dynamodb_client = boto3.client(
            "dynamodb",
            region_name=os.environ.get("AWS_REGION", "us-east-1")
        )
    return _dynamodb_client


def get_dynamodb_table():
    """Get or create DynamoDB table resource."""
    global _dynamodb_table
    if _dynamodb_table is None:
        table_name = get_agent_config_table_name()
        if table_name:
            dynamodb = boto3.resource(
                "dynamodb",
                region_name=os.environ.get("AWS_REGION", "us-east-1")
            )
            _dynamodb_table = dynamodb.Table(table_name)
    return _dynamodb_table


def get_ssm_client():
    """Get or create SSM client for reading OAuth tokens."""
    global _ssm_client
    if _ssm_client is None:
        _ssm_client = boto3.client(
            "ssm",
            region_name=os.environ.get("AWS_REGION", "us-east-1")
        )
    return _ssm_client


def clear_config_cache(key: Optional[str] = None):
    """Clear the configuration cache."""
    global _config_cache, _cache_initialized
    if key:
        _config_cache.pop(key, None)
        logger.info(f"🗑️ DDB_CACHE: Cleared cache for {key}")
    else:
        _config_cache.clear()
        _cache_initialized = False
        logger.info("🗑️ DDB_CACHE: Cleared all config cache")


def _get_item(pk: str, sk: str, consistent_read: bool = False) -> Optional[Dict[str, Any]]:
    """Get a single item from DynamoDB."""
    table = get_dynamodb_table()
    if not table:
        logger.warning("⚠️ DDB_LOADER: DynamoDB table not configured")
        return None
    
    try:
        response = table.get_item(
            Key={"pk": pk, "sk": sk},
            ConsistentRead=consistent_read  # Use consistent read when refreshing cache
        )
        return response.get("Item")
    except ClientError as e:
        logger.error(f"❌ DDB_LOADER: Error getting item {pk}/{sk}: {e}")
        return None


def _query_items(pk: str, sk_prefix: Optional[str] = None) -> List[Dict[str, Any]]:
    """Query items by partition key with optional sort key prefix."""
    table = get_dynamodb_table()
    if not table:
        return []
    
    try:
        if sk_prefix:
            response = table.query(
                KeyConditionExpression="pk = :pk AND begins_with(sk, :sk_prefix)",
                ExpressionAttributeValues={
                    ":pk": pk,
                    ":sk_prefix": sk_prefix
                }
            )
        else:
            response = table.query(
                KeyConditionExpression="pk = :pk",
                ExpressionAttributeValues={":pk": pk}
            )
        return response.get("Items", [])
    except ClientError as e:
        logger.error(f"❌ DDB_LOADER: Error querying {pk}: {e}")
        return []


def _query_by_config_type(config_type: str) -> List[Dict[str, Any]]:
    """Query items by config type using GSI."""
    table = get_dynamodb_table()
    if not table:
        return []
    
    try:
        response = table.query(
            IndexName="ConfigTypeIndex",
            KeyConditionExpression="config_type = :ct",
            ExpressionAttributeValues={":ct": config_type}
        )
        return response.get("Items", [])
    except ClientError as e:
        logger.error(f"❌ DDB_LOADER: Error querying by config_type {config_type}: {e}")
        return []


# ============================================
# Agent Instructions
# ============================================

def load_agent_instructions(agent_name: str, use_cache: bool = True) -> Optional[str]:
    """
    Load agent instructions from DynamoDB.
    
    Args:
        agent_name: Name of the agent
        use_cache: Whether to use cached data. When False, uses consistent read.
        
    Returns:
        Agent instruction text, or None if not found
    """
    cache_key = f"instruction:{agent_name}"
    
    if use_cache and cache_key in _config_cache:
        logger.debug(f"📦 DDB_CACHE: Cache HIT for instructions: {agent_name}")
        return _config_cache[cache_key]
    
    pk = f"INSTRUCTION#{agent_name}"
    sk = "v1"
    
    # Use consistent read when not using cache (i.e., during refresh)
    item = _get_item(pk, sk, consistent_read=not use_cache)
    if item:
        content = item.get("content", "")
        _config_cache[cache_key] = content
        logger.info(f"✅ DDB_LOADER: Loaded instructions for {agent_name} ({len(content)} chars)")
        return content
    
    logger.debug(f"⚠️ DDB_LOADER: No instructions found for {agent_name}")
    _config_cache[cache_key] = None
    return None


# ============================================
# Agent Cards
# ============================================

def load_agent_card(agent_name: str, use_cache: bool = True) -> Optional[Dict[str, Any]]:
    """
    Load agent card from DynamoDB.
    
    Args:
        agent_name: Name of the agent
        use_cache: Whether to use cached data
        
    Returns:
        Agent card as dict, or None if not found
    """
    cache_key = f"card:{agent_name}"
    
    if use_cache and cache_key in _config_cache:
        logger.debug(f"📦 DDB_CACHE: Cache HIT for card: {agent_name}")
        return _config_cache[cache_key]
    
    pk = f"CARD#{agent_name}"
    sk = "v1"
    
    item = _get_item(pk, sk)
    if item:
        content = item.get("content", "{}")
        try:
            card_data = json.loads(content) if isinstance(content, str) else content
            _config_cache[cache_key] = card_data
            logger.info(f"✅ DDB_LOADER: Loaded card for {agent_name}")
            return card_data
        except json.JSONDecodeError as e:
            logger.error(f"❌ DDB_LOADER: Invalid JSON in card for {agent_name}: {e}")
    
    logger.debug(f"⚠️ DDB_LOADER: No card found for {agent_name}")
    _config_cache[cache_key] = None
    return None


def load_all_agent_cards(use_cache: bool = True) -> List[Dict[str, Any]]:
    """
    Load all agent cards from DynamoDB.
    
    Returns:
        List of agent card dictionaries
    """
    cache_key = "all_cards"
    
    if use_cache and cache_key in _config_cache:
        logger.debug("📦 DDB_CACHE: Cache HIT for all cards")
        return _config_cache[cache_key]
    
    items = _query_by_config_type(CONFIG_TYPE_CARD)
    cards = []
    
    for item in items:
        content = item.get("content", "{}")
        try:
            card_data = json.loads(content) if isinstance(content, str) else content
            cards.append(card_data)
        except json.JSONDecodeError:
            continue
    
    _config_cache[cache_key] = cards
    logger.info(f"✅ DDB_LOADER: Loaded {len(cards)} agent cards")
    return cards


# ============================================
# Visualization Maps and Templates
# ============================================

def load_visualization_map(agent_name: str, use_cache: bool = True) -> Optional[Dict[str, Any]]:
    """
    Load visualization map for an agent from DynamoDB.
    
    Args:
        agent_name: Name of the agent
        use_cache: Whether to use cached data
        
    Returns:
        Visualization map as dict, or None if not found
    """
    cache_key = f"viz_map:{agent_name}"
    
    if use_cache and cache_key in _config_cache:
        logger.debug(f"📦 DDB_CACHE: Cache HIT for viz map: {agent_name}")
        return _config_cache[cache_key]
    
    pk = f"VIZ_MAP#{agent_name}"
    sk = "v1"
    
    item = _get_item(pk, sk)
    if item:
        content = item.get("content", "{}")
        try:
            viz_map = json.loads(content) if isinstance(content, str) else content
            _config_cache[cache_key] = viz_map
            logger.info(f"✅ DDB_LOADER: Loaded viz map for {agent_name}")
            return viz_map
        except json.JSONDecodeError as e:
            logger.error(f"❌ DDB_LOADER: Invalid JSON in viz map for {agent_name}: {e}")
    
    _config_cache[cache_key] = None
    return None


def load_visualization_template(
    agent_name: str, 
    template_id: str, 
    use_cache: bool = True
) -> Optional[Dict[str, Any]]:
    """
    Load a specific visualization template from DynamoDB.
    
    Args:
        agent_name: Name of the agent
        template_id: Template identifier
        use_cache: Whether to use cached data
        
    Returns:
        Template data as dict, or None if not found
    """
    cache_key = f"viz_template:{agent_name}:{template_id}"
    
    if use_cache and cache_key in _config_cache:
        logger.debug(f"📦 DDB_CACHE: Cache HIT for template: {agent_name}/{template_id}")
        return _config_cache[cache_key]
    
    pk = f"VIZ_TEMPLATE#{agent_name}"
    sk = template_id
    
    item = _get_item(pk, sk)
    if item:
        content = item.get("content", "{}")
        try:
            template_data = json.loads(content) if isinstance(content, str) else content
            _config_cache[cache_key] = template_data
            logger.info(f"✅ DDB_LOADER: Loaded template {template_id} for {agent_name}")
            return template_data
        except json.JSONDecodeError as e:
            logger.error(f"❌ DDB_LOADER: Invalid JSON in template {agent_name}/{template_id}: {e}")
    
    _config_cache[cache_key] = None
    return None


def load_all_visualization_templates(
    agent_name: str, 
    use_cache: bool = True
) -> Dict[str, Dict[str, Any]]:
    """
    Load all visualization templates for an agent.
    
    Args:
        agent_name: Name of the agent
        use_cache: Whether to use cached data
        
    Returns:
        Dict mapping template_id to template data
    """
    cache_key = f"all_viz_templates:{agent_name}"
    
    if use_cache and cache_key in _config_cache:
        logger.debug(f"📦 DDB_CACHE: Cache HIT for all templates: {agent_name}")
        return _config_cache[cache_key]
    
    # First get the visualization map to know which templates exist
    viz_map = load_visualization_map(agent_name, use_cache)
    if not viz_map:
        _config_cache[cache_key] = {}
        return {}
    
    templates = viz_map.get("templates", [])
    result = {}
    
    for template_info in templates:
        template_id = template_info.get("templateId")
        if template_id:
            template_data = load_visualization_template(agent_name, template_id, use_cache)
            if template_data:
                result[template_id] = {
                    "usage": template_info.get("usage", ""),
                    "dataMapping": template_data.get("dataMapping", template_data)
                }
    
    _config_cache[cache_key] = result
    logger.info(f"✅ DDB_LOADER: Loaded {len(result)} templates for {agent_name}")
    return result


# ============================================
# Global Configuration
# ============================================

def load_global_config(use_cache: bool = True) -> Optional[Dict[str, Any]]:
    """
    Load global configuration from DynamoDB.
    
    Args:
        use_cache: Whether to use cached data. When False, uses consistent read.
    
    Returns:
        Global config as dict, or None if not found
    """
    cache_key = "global_config"
    
    if use_cache and cache_key in _config_cache:
        logger.debug("📦 DDB_CACHE: Cache HIT for global config")
        return _config_cache[cache_key]
    
    pk = "GLOBAL_CONFIG"
    sk = "v1"
    
    # Use consistent read when not using cache (i.e., during refresh)
    item = _get_item(pk, sk, consistent_read=not use_cache)
    if item:
        content = item.get("content", "{}")
        try:
            config = json.loads(content) if isinstance(content, str) else content
            _config_cache[cache_key] = config
            logger.info(f"✅ DDB_LOADER: Loaded global config (consistent_read={not use_cache})")
            return config
        except json.JSONDecodeError as e:
            logger.error(f"❌ DDB_LOADER: Invalid JSON in global config: {e}")
    
    _config_cache[cache_key] = None
    return None


# ============================================
# Batch Pre-loading
# ============================================

def preload_all_configs(agent_names: List[str]) -> Dict[str, int]:
    """
    Pre-load all configurations into cache for fast access.
    
    Args:
        agent_names: List of agent names to pre-load
        
    Returns:
        Dict with counts of loaded items by type
    """
    global _cache_initialized
    
    if _cache_initialized:
        logger.debug("⏭️ DDB_PRELOAD: Already initialized, skipping")
        return {"status": "already_initialized"}
    
    logger.info(f"🚀 DDB_PRELOAD: Starting pre-load for {len(agent_names)} agents...")
    start_time = datetime.now()
    
    counts = {
        "instructions": 0,
        "cards": 0,
        "viz_maps": 0,
        "viz_templates": 0,
        "global_config": 0
    }
    
    # Load global config
    if load_global_config(use_cache=False):
        counts["global_config"] = 1
    
    # Load all agent cards at once
    cards = load_all_agent_cards(use_cache=False)
    counts["cards"] = len(cards)
    
    # Load per-agent configs
    for agent_name in agent_names:
        # Instructions
        if load_agent_instructions(agent_name, use_cache=False):
            counts["instructions"] += 1
        
        # Visualization map
        if load_visualization_map(agent_name, use_cache=False):
            counts["viz_maps"] += 1
        
        # All visualization templates
        templates = load_all_visualization_templates(agent_name, use_cache=False)
        counts["viz_templates"] += len(templates)
    
    _cache_initialized = True
    elapsed = (datetime.now() - start_time).total_seconds()
    
    logger.info(f"🚀 DDB_PRELOAD: Completed in {elapsed:.2f}s")
    logger.info(f"   - Instructions: {counts['instructions']}")
    logger.info(f"   - Cards: {counts['cards']}")
    logger.info(f"   - Viz maps: {counts['viz_maps']}")
    logger.info(f"   - Viz templates: {counts['viz_templates']}")
    logger.info(f"   - Global config: {counts['global_config']}")
    
    return counts


def get_cache_stats() -> Dict[str, Any]:
    """Get statistics about the config cache."""
    return {
        "cache_entries": len(_config_cache),
        "cache_initialized": _cache_initialized,
        "table_name": get_agent_config_table_name(),
        "cache_keys": list(_config_cache.keys())[:20]  # First 20 keys
    }


# ============================================
# MCP Server OAuth Token Resolution
# ============================================

def resolve_ssm_parameter(ssm_path: str, use_cache: bool = True) -> Optional[str]:
    """
    Fetch a single SSM parameter value (SecureString supported).

    Args:
        ssm_path: Full SSM parameter path
        use_cache: Whether to use cached value

    Returns:
        Decrypted parameter value, or None on failure
    """
    cache_key = f"ssm:{ssm_path}"

    if use_cache and cache_key in _config_cache:
        logger.debug(f"📦 DDB_CACHE: Cache HIT for SSM param: {ssm_path}")
        return _config_cache[cache_key]

    ssm = get_ssm_client()
    if not ssm:
        return None

    try:
        response = ssm.get_parameter(Name=ssm_path, WithDecryption=True)
        value = response["Parameter"]["Value"]
        _config_cache[cache_key] = value
        logger.info(f"✅ SSM_LOADER: Resolved parameter {ssm_path}")
        return value
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code == "ParameterNotFound":
            logger.warning(f"⚠️ SSM_LOADER: Parameter not found: {ssm_path}")
        else:
            logger.error(f"❌ SSM_LOADER: Error fetching {ssm_path}: {e}")
        _config_cache[cache_key] = None
        return None


def resolve_mcp_server_auth(mcp_servers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Resolve OAuth bearer tokens from SSM for MCP server configurations.

    For each server that has an oauthToken.ssmPath, fetches the token from
    SSM Parameter Store and injects it as an Authorization header.

    Args:
        mcp_servers: List of MCP server config dicts from the agent's global config

    Returns:
        The same list with Authorization headers injected where tokens exist
    """
    if not mcp_servers:
        return mcp_servers

    for server in mcp_servers:
        oauth_config = server.get("oauthToken")
        if not oauth_config:
            continue

        ssm_path = oauth_config.get("ssmPath")
        has_token = oauth_config.get("hasToken", False)

        if not has_token or not ssm_path:
            continue

        token = resolve_ssm_parameter(ssm_path)
        if token:
            if "headers" not in server:
                server["headers"] = {}
            server["headers"]["Authorization"] = f"Bearer {token}"
            logger.info(f"✅ MCP_AUTH: Injected bearer token for server '{server.get('name', server.get('id', '?'))}'")
        else:
            logger.warning(
                f"⚠️ MCP_AUTH: Could not resolve token for server '{server.get('name', server.get('id', '?'))}' "
                f"at {ssm_path}"
            )

    return mcp_servers


def load_agent_mcp_servers(agent_name: str, use_cache: bool = True) -> List[Dict[str, Any]]:
    """
    Load MCP server configs for an agent from global config and resolve OAuth tokens.

    Args:
        agent_name: Name of the agent
        use_cache: Whether to use cached data

    Returns:
        List of MCP server configs with auth headers injected
    """
    global_config = load_global_config(use_cache=use_cache)
    if not global_config:
        return []

    agent_configs = global_config.get("agent_configs", {})
    agent_config = agent_configs.get(agent_name)
    if not agent_config:
        return []

    mcp_servers = agent_config.get("mcp_servers", [])
    if not mcp_servers:
        return []

    # Deep copy to avoid mutating the cached config
    import copy
    servers_copy = copy.deepcopy(mcp_servers)

    return resolve_mcp_server_auth(servers_copy)


# ============================================
# Write Operations (for deployment script)
# ============================================

def put_agent_instructions(agent_name: str, content: str) -> bool:
    """Store agent instructions in DynamoDB."""
    table = get_dynamodb_table()
    if not table:
        return False
    
    try:
        table.put_item(Item={
            "pk": f"INSTRUCTION#{agent_name}",
            "sk": "v1",
            "config_type": CONFIG_TYPE_INSTRUCTION,
            "agent_name": agent_name,
            "content": content,
            "updated_at": datetime.utcnow().isoformat()
        })
        logger.info(f"✅ DDB_WRITER: Stored instructions for {agent_name}")
        return True
    except ClientError as e:
        logger.error(f"❌ DDB_WRITER: Failed to store instructions for {agent_name}: {e}")
        return False


def put_agent_card(agent_name: str, card_data: Dict[str, Any]) -> bool:
    """Store agent card in DynamoDB."""
    table = get_dynamodb_table()
    if not table:
        return False
    
    try:
        table.put_item(Item={
            "pk": f"CARD#{agent_name}",
            "sk": "v1",
            "config_type": CONFIG_TYPE_CARD,
            "agent_name": agent_name,
            "content": json.dumps(card_data),
            "updated_at": datetime.utcnow().isoformat()
        })
        logger.info(f"✅ DDB_WRITER: Stored card for {agent_name}")
        return True
    except ClientError as e:
        logger.error(f"❌ DDB_WRITER: Failed to store card for {agent_name}: {e}")
        return False


def put_visualization_map(agent_name: str, viz_map: Dict[str, Any]) -> bool:
    """Store visualization map in DynamoDB."""
    table = get_dynamodb_table()
    if not table:
        return False
    
    try:
        table.put_item(Item={
            "pk": f"VIZ_MAP#{agent_name}",
            "sk": "v1",
            "config_type": CONFIG_TYPE_VIZ_MAP,
            "agent_name": agent_name,
            "content": json.dumps(viz_map),
            "updated_at": datetime.utcnow().isoformat()
        })
        logger.info(f"✅ DDB_WRITER: Stored viz map for {agent_name}")
        return True
    except ClientError as e:
        logger.error(f"❌ DDB_WRITER: Failed to store viz map for {agent_name}: {e}")
        return False


def put_visualization_template(
    agent_name: str, 
    template_id: str, 
    template_data: Dict[str, Any]
) -> bool:
    """Store visualization template in DynamoDB."""
    table = get_dynamodb_table()
    if not table:
        return False
    
    try:
        table.put_item(Item={
            "pk": f"VIZ_TEMPLATE#{agent_name}",
            "sk": template_id,
            "config_type": CONFIG_TYPE_VIZ_TEMPLATE,
            "agent_name": agent_name,
            "template_id": template_id,
            "content": json.dumps(template_data),
            "updated_at": datetime.utcnow().isoformat()
        })
        logger.info(f"✅ DDB_WRITER: Stored template {template_id} for {agent_name}")
        return True
    except ClientError as e:
        logger.error(f"❌ DDB_WRITER: Failed to store template {template_id} for {agent_name}: {e}")
        return False


def put_global_config(config: Dict[str, Any]) -> bool:
    """Store global configuration in DynamoDB."""
    table = get_dynamodb_table()
    if not table:
        return False
    
    try:
        table.put_item(Item={
            "pk": "GLOBAL_CONFIG",
            "sk": "v1",
            "config_type": CONFIG_TYPE_GLOBAL,
            "content": json.dumps(config),
            "updated_at": datetime.utcnow().isoformat()
        })
        logger.info("✅ DDB_WRITER: Stored global config")
        return True
    except ClientError as e:
        logger.error(f"❌ DDB_WRITER: Failed to store global config: {e}")
        return False


def delete_agent_config(agent_name: str) -> bool:
    """Delete all configuration for an agent from DynamoDB."""
    table = get_dynamodb_table()
    if not table:
        return False
    
    try:
        # Delete instructions
        table.delete_item(Key={"pk": f"INSTRUCTION#{agent_name}", "sk": "v1"})
        
        # Delete card
        table.delete_item(Key={"pk": f"CARD#{agent_name}", "sk": "v1"})
        
        # Delete viz map
        table.delete_item(Key={"pk": f"VIZ_MAP#{agent_name}", "sk": "v1"})
        
        # Delete all viz templates (need to query first)
        templates = _query_items(f"VIZ_TEMPLATE#{agent_name}")
        for template in templates:
            table.delete_item(Key={"pk": template["pk"], "sk": template["sk"]})
        
        logger.info(f"✅ DDB_WRITER: Deleted all config for {agent_name}")
        
        # Clear from cache
        clear_config_cache()
        
        return True
    except ClientError as e:
        logger.error(f"❌ DDB_WRITER: Failed to delete config for {agent_name}: {e}")
        return False
