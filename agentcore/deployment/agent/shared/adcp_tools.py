"""
AdCP MCP Tools for Agentic Advertising Ecosystem

This module provides tools for the Ad Context Protocol (AdCP).

IMPORTANT: When ADCP_GATEWAY_URL is set, MCP is REQUIRED and failures will raise errors.
There is NO silent fallback to mock data in production mode.

Environment Variables:
- ADCP_GATEWAY_URL: URL for AgentCore MCP Gateway (REQUIRED for production)
- ADCP_USE_MCP: Set to "false" ONLY for local development without MCP

Reference: https://docs.adcontextprotocol.org
"""

import json
import logging
import os
from typing import Optional, List, Dict, Any
from strands import tool

logger = logging.getLogger(__name__)

# Lazy initialization - MCP client is created on first use
_mcp_client = None
_mcp_client_initialized = False
_mcp_available = True
_mcp_required = False  # Set to True when ADCP_GATEWAY_URL is configured

# Try to import MCP client module
try:
    from .adcp_mcp_client import create_adcp_mcp_client, MCP_AVAILABLE, SIGV4_AVAILABLE
    _mcp_available = MCP_AVAILABLE
    logger.info(f"AdCP MCP module loaded: MCP_AVAILABLE={MCP_AVAILABLE}, SIGV4_AVAILABLE={SIGV4_AVAILABLE}")
except ImportError as e:
    logger.warning(f"MCP client module not available: {e}")
    _mcp_available = False
    MCP_AVAILABLE = False
    SIGV4_AVAILABLE = False


class MCPConnectionError(Exception):
    """Raised when MCP is required but connection fails"""
    pass


def _get_mcp_client():
    """
    Lazy initialization of MCP client.
    
    When ADCP_GATEWAY_URL is set, MCP is REQUIRED and this will raise
    an error if the client cannot be created.
    """
    global _mcp_client, _mcp_client_initialized, _mcp_required
    
    if _mcp_client_initialized:
        return _mcp_client
    
    _mcp_client_initialized = True
    
    # Log all ADCP-related environment variables for debugging
    logger.info("=" * 60)
    logger.info("🔍 AdCP MCP Client Initialization")
    logger.info(f"   ADCP_GATEWAY_URL: {os.environ.get('ADCP_GATEWAY_URL', 'NOT SET')}")
    logger.info(f"   ADCP_USE_MCP: {os.environ.get('ADCP_USE_MCP', 'NOT SET')}")
    logger.info(f"   AWS_REGION: {os.environ.get('AWS_REGION', 'NOT SET')}")
    logger.info(f"   MCP_AVAILABLE: {_mcp_available}")
    logger.info("=" * 60)
    
    # Check if MCP is explicitly disabled
    use_mcp = os.environ.get("ADCP_USE_MCP", "true").lower() == "true"
    if not use_mcp:
        logger.info("AdCP MCP disabled via ADCP_USE_MCP=false (development mode)")
        _mcp_required = False
        return None
    
    # Get gateway URL - if set, MCP is REQUIRED
    gateway_url = os.environ.get("ADCP_GATEWAY_URL")
    server_path = os.environ.get("ADCP_MCP_SERVER_PATH")
    
    # If gateway URL is configured, MCP is required - no fallback allowed
    if gateway_url:
        _mcp_required = True
        logger.info(f"ADCP_GATEWAY_URL is set to: {gateway_url}")
        logger.info(f"MCP is REQUIRED (no fallback)")
    
    if not _mcp_available:
        if _mcp_required:
            raise MCPConnectionError(
                "MCP dependencies not available but ADCP_GATEWAY_URL is set. "
                "Install MCP dependencies or unset ADCP_GATEWAY_URL."
            )
        logger.warning("MCP dependencies not available. Running in development mode.")
        return None
    
    logger.info(f"Initializing AdCP MCP client: gateway_url={gateway_url}")
    
    try:
        if gateway_url:
            _mcp_client = create_adcp_mcp_client(
                transport="http",
                gateway_url=gateway_url
            )
            if _mcp_client:
                logger.info(f"✅ AdCP MCP client created: {gateway_url}")
            else:
                raise MCPConnectionError(
                    f"Failed to create MCP client for gateway: {gateway_url}. "
                    "Check gateway URL and AWS credentials."
                )
        elif server_path:
            _mcp_client = create_adcp_mcp_client(
                transport="stdio",
                server_path=server_path
            )
            if not _mcp_client:
                logger.warning("Failed to create stdio MCP client")
        else:
            logger.info("No ADCP_GATEWAY_URL set - running in development mode")
            
    except MCPConnectionError:
        raise
    except Exception as e:
        if _mcp_required:
            raise MCPConnectionError(f"MCP client creation failed: {e}")
        logger.error(f"Error creating MCP client: {e}")
        import traceback
        logger.error(traceback.format_exc())
    
    return _mcp_client


def _call_mcp_tool(tool_name: str, arguments: Dict[str, Any]) -> str:
    """
    Call an MCP tool.
    
    When MCP is required (ADCP_GATEWAY_URL is set), this will raise an error
    if the call fails. There is NO silent fallback.
    
    This function first tries the direct gateway call (proven to work),
    then falls back to MCPClient if direct call is not available.
    """
    arguments['record_direct_tool_call']=False

    gateway_url = os.environ.get("ADCP_GATEWAY_URL")
    region = os.environ.get("AWS_REGION", "us-east-1")
    
    logger.info(f"🔌 _call_mcp_tool: {tool_name}")
    logger.info(f"   Gateway URL: {gateway_url or 'NOT SET'}")
    logger.info(f"   Region: {region}")
    logger.info(f"   Arguments: {json.dumps(arguments)[:200]}...")
    
    # If gateway URL is set, try direct gateway call first (proven to work)
    if gateway_url:
        try:
            from .adcp_mcp_client import call_gateway_tool_sync
            logger.info(f"🔌 Attempting direct gateway call for: {tool_name}")
            result = call_gateway_tool_sync(tool_name, arguments, gateway_url, region)
            if result:
                logger.info(f"✅ Direct gateway call succeeded for {tool_name}")
                result_str = f"<visualization-data type='adcp_{tool_name}'>{json.dumps(result) if isinstance(result, dict) else str(result)}</visualization-data>"
                logger.info(f"   Result preview: {result_str[:200]}...")
                return result_str
            else:
                logger.warning(f"⚠️ Direct gateway call returned None for {tool_name}")
        except ImportError as e:
            logger.warning(f"Direct gateway call not available: {e}")
            logger.warning("Falling back to MCPClient approach")
        except Exception as e:
            logger.error(f"❌ Direct gateway call failed: {e}")
            import traceback
            logger.error(f"   Traceback: {traceback.format_exc()}")
            logger.warning("Falling back to MCPClient approach")
    
    # Fall back to MCPClient approach
    client = _get_mcp_client()
    
    if client is None:
        if _mcp_required:
            raise MCPConnectionError(
                f"MCP client not available for {tool_name} but MCP is required. "
                "Check ADCP_GATEWAY_URL configuration."
            )
        # Only return None (allowing fallback) if MCP is not required
        logger.debug(f"MCP not configured, using development fallback for {tool_name}")
        return None
    
    try:
        # Try to get the prefixed tool name for gateway
        full_tool_name = tool_name
        if gateway_url:
            try:
                from .adcp_mcp_client import get_gateway_tool_name
                full_tool_name = get_gateway_tool_name(tool_name, gateway_url, region)
                logger.info(f"🔌 Calling MCP tool via MCPClient: {full_tool_name} (base: {tool_name})")
            except Exception as e:
                logger.warning(f"Could not get gateway tool name, using base name: {e}")
                full_tool_name = tool_name
        else:
            logger.info(f"🔌 Calling MCP tool: {tool_name}")
        
        with client:
            result = client.call_tool_sync(
                tool_use_id=f"adcp_{tool_name}",
                name=full_tool_name,
                arguments=arguments
            )
            if result and result.get("content"):
                logger.info(f"✅ MCP tool {tool_name} succeeded via MCPClient")
                return result["content"][0].get("text", json.dumps(result))
            else:
                error_msg = f"MCP tool {tool_name} returned empty result"
                if _mcp_required:
                    raise MCPConnectionError(error_msg)
                logger.warning(f"⚠️ {error_msg}")
                return None
                
    except MCPConnectionError:
        raise
    except Exception as e:
        error_msg = f"MCP call failed for {tool_name}: {e}"
        if _mcp_required:
            raise MCPConnectionError(error_msg)
        logger.warning(f"❌ {error_msg}")
        import traceback
        logger.debug(traceback.format_exc())
        return None


def reinitialize_mcp_client():
    """Force re-initialization of the MCP client."""
    global _mcp_client, _mcp_client_initialized, _mcp_required
    _mcp_client = None
    _mcp_client_initialized = False
    _mcp_required = False
    logger.info("MCP client marked for re-initialization")
    return _get_mcp_client()


# ============================================================================
# Tool Implementations
# ============================================================================

@tool
def get_products(
    brief: Optional[str] = None,
    brand_manifest: Optional[Dict[str, Any]] = None,
    filters: Optional[Dict[str, Any]] = None
) -> str:
    """
    Discover available advertising products matching campaign criteria (AdCP Media Buy Protocol).
    
    Args:
        brief: Natural language description of campaign requirements
        brand_manifest: Brand information manifest (inline object or URL string) providing 
                       brand context, assets, and product catalog
        filters: Structured filters for product discovery:
            - delivery_type: "guaranteed" or "non_guaranteed"
            - is_fixed_price: Filter for fixed price vs auction products
            - format_types: Array of format types (audio, video, display, native, dooh, rich_media, universal)
            - channels: Array of channels (display, video, audio, native, dooh, ctv, podcast, retail, social)
            - countries: Array of ISO 3166-1 alpha-2 country codes (e.g., ["US", "CA"])
            - budget_range: Object with min, max, and currency (ISO 4217)
            - start_date: Campaign start date (YYYY-MM-DD)
            - end_date: Campaign end date (YYYY-MM-DD)
    
    Returns:
        JSON string with products array containing:
        - product_id, name, description
        - publisher_properties: Array of {publisher_domain, selection_type, property_ids/property_tags}
        - format_ids: Array of {agent_url, id, width?, height?, duration_ms?}
        - delivery_type: "guaranteed" or "non_guaranteed"
        - pricing_options: Array of pricing models with rates
        - delivery_measurement: {provider, notes?}
        - brief_relevance: Explanation of match (when brief provided)
    """
    brief_preview = brief[:50] if brief else "None"
    logger.info(f"AdCP get_products: brief='{brief_preview}...', filters={filters}")
    
    # Build request per official schema
    request = {}
    if brief:
        request["brief"] = brief
    if brand_manifest:
        request["brand_manifest"] = brand_manifest
    if filters:
        request["filters"] = filters
    
    
    result = _call_mcp_tool("get_products", request)
    
    if result:
        return result
    
    # Development-only fallback (only reached if MCP is not required)
    return json.dumps({
        "products": [],
        "errors": [{
            "code": "MCP_NOT_CONFIGURED",
            "message": "Set ADCP_GATEWAY_URL for production use"
        }]
    }, indent=2)


@tool
def get_signals(
    signal_spec: str,
    deliver_to: Dict[str, Any],
    filters: Optional[Dict[str, Any]] = None,
    max_results: Optional[int] = None
) -> str:
    """
    Discover audience and contextual signals for targeting (AdCP Signals Protocol).
    
    Args:
        signal_spec: Natural language description of the desired signals
        deliver_to: Deployment targets where signals need to be activated:
            - deployments: Array of destination objects:
                - type: "platform" or "agent"
                - platform: Platform ID for DSPs (e.g., "the-trade-desk", "amazon-dsp") - required if type="platform"
                - agent_url: URL for agent deployment - required if type="agent"
                - account: Optional account identifier
            - countries: Array of ISO 3166-1 alpha-2 country codes (e.g., ["US", "CA"])
        filters: Optional filters to refine results:
            - catalog_types: Array of "marketplace", "custom", or "owned"
            - data_providers: Array of provider names
            - max_cpm: Maximum CPM price filter
            - min_coverage_percentage: Minimum coverage requirement (0-100)
        max_results: Maximum number of results to return
    
    Returns:
        JSON string with signals array containing:
        - signal_agent_segment_id, name, description
        - signal_type: "marketplace", "custom", or "owned"
        - data_provider, coverage_percentage
        - deployments: Array with is_live status and activation_key (if authorized)
        - pricing: {cpm, currency}
    """
    logger.info(f"AdCP get_signals: signal_spec='{signal_spec[:50]}...', deliver_to={deliver_to}")
    
    # Build request per official schema
    request = {
        "signal_spec": signal_spec,
        "deliver_to": deliver_to
    }
    if filters:
        request["filters"] = filters
    if max_results:
        request["max_results"] = max_results
    
    result = _call_mcp_tool("get_signals", request)
    
    if result:
        return result
    
    return json.dumps({
        "signals": [],
        "errors": [{
            "code": "MCP_NOT_CONFIGURED",
            "message": "Set ADCP_GATEWAY_URL for production use"
        }]
    }, indent=2)


@tool
def activate_signal(
    signal_agent_segment_id: str,
    deployments: List[Dict[str, Any]]
) -> str:
    """
    Activate a signal segment on deployment targets (AdCP Signals Protocol).
    
    Args:
        signal_agent_segment_id: The universal identifier for the signal to activate
        deployments: Target deployment(s) for activation. Array of destination objects:
            - type: "platform" or "agent" (required)
            - platform: Platform ID for DSPs (e.g., "the-trade-desk", "amazon-dsp") - required if type="platform"
            - agent_url: URL for agent deployment - required if type="agent"
            - account: Optional account identifier
    
    Returns:
        JSON string with either:
        Success: deployments array with:
            - type, platform/agent_url
            - is_live: Whether signal is active
            - activation_key: Key for targeting (if is_live=true and authorized)
            - deployed_at: Timestamp when activation completed
        Error: errors array with error details
    """
    logger.info(f"AdCP activate_signal: {signal_agent_segment_id} to {len(deployments)} targets")
    
    result = _call_mcp_tool("activate_signal", {
        "signal_agent_segment_id": signal_agent_segment_id,
        "deployments": deployments
    })
    
    if result:
        return result
    
    return json.dumps({
        "errors": [{
            "code": "MCP_NOT_CONFIGURED",
            "message": "Set ADCP_GATEWAY_URL for production use"
        }]
    }, indent=2)


@tool
def create_media_buy(
    buyer_ref: str,
    packages: List[Dict[str, Any]],
    brand_manifest: Dict[str, Any],
    start_time: str,
    end_time: str,
    po_number: Optional[str] = None,
    reporting_webhook: Optional[Dict[str, Any]] = None
) -> str:
    """
    Create a media buy with publisher packages (AdCP Media Buy Protocol).
    
    Args:
        buyer_ref: Buyer's reference identifier for this media buy
        packages: Array of package configurations, each containing:
            - buyer_ref: Buyer's reference for this package (required)
            - product_id: Product ID for this package (required)
            - budget: Budget allocation in media buy's currency (required)
            - pricing_option_id: ID of selected pricing option from product (required)
            - bid_price: Bid price for auction-based pricing (if applicable)
            - pacing: "even", "asap", or "front_loaded"
            - format_ids: Array of {agent_url, id} for formats to use
            - targeting_overlay: Additional targeting criteria
            - creative_ids: IDs of existing library creatives to assign
            - creatives: Full creative objects to upload and assign
        brand_manifest: Brand information manifest (inline object or URL string)
        start_time: Campaign start - "asap" or ISO 8601 date-time
        end_time: Campaign end date/time in ISO 8601 format
        po_number: Optional purchase order number for tracking
        reporting_webhook: Optional webhook config for automated reporting
    
    Returns:
        JSON string with either:
        Success: media_buy_id, buyer_ref, creative_deadline, packages array
        Error: errors array with error details
    """
    logger.info(f"AdCP create_media_buy: buyer_ref={buyer_ref}, packages={len(packages)}")
    
    request = {
        "buyer_ref": buyer_ref,
        "packages": packages,
        "brand_manifest": brand_manifest,
        "start_time": start_time,
        "end_time": end_time
    }
    if po_number:
        request["po_number"] = po_number
    if reporting_webhook:
        request["reporting_webhook"] = reporting_webhook
    
    result = _call_mcp_tool("create_media_buy", request)
    
    if result:
        return result
    
    return json.dumps({
        "errors": [{
            "code": "MCP_NOT_CONFIGURED",
            "message": "Set ADCP_GATEWAY_URL for production use"
        }]
    }, indent=2)


@tool
def get_media_buy_delivery(
    media_buy_ids: Optional[List[str]] = None,
    buyer_refs: Optional[List[str]] = None,
    status_filter: Optional[Any] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
) -> str:
    """
    Get delivery metrics for media buys (AdCP Media Buy Protocol).
    
    Args:
        media_buy_ids: Array of publisher media buy IDs to get delivery data for
        buyer_refs: Array of buyer reference IDs to get delivery data for
        status_filter: Filter by status - single value or array of:
            "pending", "active", "paused", "completed", "failed"
        start_date: Start date for reporting period (YYYY-MM-DD)
        end_date: End date for reporting period (YYYY-MM-DD)
    
    Returns:
        JSON string with:
        - reporting_period: {start, end} in ISO 8601 UTC
        - currency: ISO 4217 currency code
        - aggregated_totals: {impressions, spend, clicks?, video_completions?, media_buy_count}
        - media_buy_deliveries: Array of delivery data per media buy:
            - media_buy_id, buyer_ref?, status
            - pricing_model, totals: {impressions?, spend, clicks?, effective_rate?}
            - by_package: Array with package_id, spend, pricing_model, rate, currency, pacing_index?, delivery_status?, paused?
            - daily_breakdown?: Array of {date, impressions, spend}
        - errors?: Array of error objects
    """
    ids_str = media_buy_ids[0] if media_buy_ids else (buyer_refs[0] if buyer_refs else "none")
    logger.info(f"AdCP get_media_buy_delivery: {ids_str}")
    
    request = {}
    if media_buy_ids:
        request["media_buy_ids"] = media_buy_ids
    if buyer_refs:
        request["buyer_refs"] = buyer_refs
    if status_filter:
        request["status_filter"] = status_filter
    if start_date:
        request["start_date"] = start_date
    if end_date:
        request["end_date"] = end_date
    
    result = _call_mcp_tool("get_media_buy_delivery", request)
    
    if result:
        return result
    
    return json.dumps({
        "reporting_period": {"start": "", "end": ""},
        "currency": "USD",
        "media_buy_deliveries": [],
        "errors": [{
            "code": "MCP_NOT_CONFIGURED",
            "message": "Set ADCP_GATEWAY_URL for production use"
        }]
    }, indent=2)


@tool
def verify_brand_safety(
    properties: List[Dict[str, str]],
    brand_safety_tier: str = "tier_1",
    categories_blocked: Optional[List[str]] = None
) -> str:
    """
    Verify brand safety for publisher properties (MCP Verification Service).
    
    Args:
        properties: List of properties to verify, each containing:
            - publisher_domain: Domain of the publisher (required)
            - property_id: Optional specific property ID
        brand_safety_tier: Minimum brand safety tier required ("tier_1", "tier_2", "tier_3")
        categories_blocked: Optional list of content categories to block
    
    Returns:
        JSON string with verification results per property
    """
    logger.info(f"MCP verify_brand_safety: {len(properties)} properties")
    
    request = {
        "properties": properties,
        "brand_safety_tier": brand_safety_tier
    }
    if categories_blocked:
        request["categories_blocked"] = categories_blocked
    
    result = _call_mcp_tool("verify_brand_safety", request)
    
    if result:
        return result
    
    return json.dumps({
        "errors": [{
            "code": "MCP_NOT_CONFIGURED",
            "message": "Set ADCP_GATEWAY_URL for production use"
        }]
    }, indent=2)


@tool
def resolve_audience_reach(
    audience_segments: List[str],
    channels: Optional[List[str]] = None,
    countries: Optional[List[str]] = None,
    identity_types: Optional[List[str]] = None
) -> str:
    """
    Estimate cross-device reach for audience segments (MCP Identity Service).
    
    Args:
        audience_segments: Signal segment IDs to estimate reach for
        channels: Channels to calculate reach for:
            "display", "video", "audio", "native", "dooh", "ctv", "podcast", "retail", "social"
        countries: ISO 3166-1 alpha-2 country codes to calculate reach for (e.g., ["US", "CA"])
        identity_types: Identity types to include in reach calculation
    
    Returns:
        JSON string with reach estimates per segment and channel
    """
    logger.info(f"MCP resolve_audience_reach: segments={audience_segments}")
    
    request = {"audience_segments": audience_segments}
    if channels:
        request["channels"] = channels
    if countries:
        request["countries"] = countries
    if identity_types:
        request["identity_types"] = identity_types
    
    result = _call_mcp_tool("resolve_audience_reach", request)
    
    if result:
        return result
    
    return json.dumps({
        "errors": [{
            "code": "MCP_NOT_CONFIGURED",
            "message": "Set ADCP_GATEWAY_URL for production use"
        }]
    }, indent=2)


@tool
def configure_brand_lift_study(
    study_name: str,
    study_type: str,
    campaign_id: Optional[str] = None,
    provider: Optional[str] = None,
    metrics: Optional[List[str]] = None,
    flight_start: Optional[str] = None,
    flight_end: Optional[str] = None,
    sample_size_target: Optional[Dict[str, int]] = None
) -> str:
    """
    Configure a brand lift or attribution measurement study (MCP Measurement Service).
    
    Args:
        study_name: Name of the study (required)
        study_type: Type of measurement study (required):
            "brand_lift", "foot_traffic", "sales_lift", "attribution"
        campaign_id: Associated campaign or media buy ID
        provider: Measurement provider (e.g., "lucid", "dynata")
        metrics: Metrics to measure (e.g., ["awareness", "consideration", "purchase_intent"])
        flight_start: Study start date in ISO 8601 format
        flight_end: Study end date in ISO 8601 format
        sample_size_target: Target sample sizes with "control" and "exposed" counts
    
    Returns:
        JSON string with study configuration confirmation
    """
    logger.info(f"MCP configure_brand_lift_study: {study_name}, type={study_type}")
    
    request = {
        "study_name": study_name,
        "study_type": study_type
    }
    if campaign_id:
        request["campaign_id"] = campaign_id
    if provider:
        request["provider"] = provider
    if metrics:
        request["metrics"] = metrics
    if flight_start:
        request["flight_start"] = flight_start
    if flight_end:
        request["flight_end"] = flight_end
    if sample_size_target:
        request["sample_size_target"] = sample_size_target
    
    result = _call_mcp_tool("configure_brand_lift_study", request)
    
    if result:
        return result
    
    return json.dumps({
        "errors": [{
            "code": "MCP_NOT_CONFIGURED",
            "message": "Set ADCP_GATEWAY_URL for production use"
        }]
    }, indent=2)


# ============================================================================
# Exports
# ============================================================================

ADCP_TOOLS = [
    get_products,
    get_signals,
    activate_signal,
    create_media_buy,
    get_media_buy_delivery,
    verify_brand_safety,
    resolve_audience_reach,
    configure_brand_lift_study,
]


def get_adcp_mcp_tools():
    """
    Get AdCP tools for agent integration.
    
    When MCP gateway is available, returns the wrapper tools that call the gateway
    directly with the correct prefixed tool names. This avoids issues with the
    MCPClient managed approach where tool names may not be handled correctly.
    
    When MCP is not available, returns the fallback stub tools.
    """
    gateway_url = os.environ.get("ADCP_GATEWAY_URL")
    
    if gateway_url:
        # When gateway is configured, use our wrapper tools that call the gateway
        # directly with the correct tool names
        logger.info(f"🔧 get_adcp_mcp_tools: Using wrapper tools for gateway: {gateway_url}")
        return ADCP_TOOLS
    else:
        # No gateway configured, return fallback tools
        logger.info("🔧 get_adcp_mcp_tools: No gateway configured, using fallback tools")
        return ADCP_TOOLS


def is_mcp_enabled() -> bool:
    """Check if MCP integration is enabled and available."""
    client = _get_mcp_client()
    return client is not None


def is_mcp_required() -> bool:
    """Check if MCP is required (ADCP_GATEWAY_URL is set)."""
    return _mcp_required
