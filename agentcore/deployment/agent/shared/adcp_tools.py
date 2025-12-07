"""
AdCP MCP Tools for Agentic Advertising Ecosystem

This module provides tools for the Ad Context Protocol (AdCP) that can work in two modes:
1. MCP Mode: Connects to a real MCP server (local or via AgentCore Gateway)
2. Fallback Mode: Uses hardcoded mock data when MCP is not available

Protocols Implemented:
- AdCP Media Buy Protocol: get_products, create_media_buy, get_media_buy_delivery
- AdCP Signals Protocol: get_signals, activate_signal
- MCP Services: verify_brand_safety, resolve_audience_reach, configure_brand_lift_study

Environment Variables:
- ADCP_USE_MCP: Set to "false" to disable MCP and use fallback (default: "true" for MCP)
- ADCP_GATEWAY_URL: URL for AgentCore MCP Gateway (enables HTTP transport)
- ADCP_MCP_SERVER_PATH: Path to local MCP server script (for stdio transport)

Reference: https://docs.adcontextprotocol.org
"""

import json
import logging
import os
from typing import Optional, List, Dict, Any
from strands import tool

logger = logging.getLogger(__name__)

# Lazy initialization - MCP client is created on first use, not at module load time
# This allows the gateway URL to be set after the module is imported
_mcp_client = None
_mcp_client_initialized = False
_mcp_available = True

# Try to import MCP client module (but don't create client yet)
try:
    from .adcp_mcp_client import create_adcp_mcp_client, MCP_AVAILABLE, SIGV4_AVAILABLE
    _mcp_available = MCP_AVAILABLE
    logger.info(f"AdCP MCP module loaded: MCP_AVAILABLE={MCP_AVAILABLE}, SIGV4_AVAILABLE={SIGV4_AVAILABLE}")
except ImportError as e:
    logger.warning(f"MCP client module not available: {e}. Using fallback mode.")
    _mcp_available = False
    MCP_AVAILABLE = False
    SIGV4_AVAILABLE = False


def _get_mcp_client():
    """
    Lazy initialization of MCP client.
    
    This is called on first tool use, not at module load time.
    This allows environment variables to be set after the module is imported,
    which is important when the gateway is deployed after the runtime starts.
    """
    global _mcp_client, _mcp_client_initialized
    
    # Only try to initialize once
    if _mcp_client_initialized:
        return _mcp_client
    
    _mcp_client_initialized = True
    
    # Check if MCP is enabled (re-read from environment each time)
    use_mcp = os.environ.get("ADCP_USE_MCP", "true").lower() == "true"
    if not use_mcp:
        logger.info("AdCP MCP disabled via ADCP_USE_MCP=false")
        return None
    
    if not _mcp_available:
        logger.warning("MCP dependencies not available. Using fallback mode.")
        return None
    
    # Get gateway URL from environment (re-read each time)
    gateway_url = os.environ.get("ADCP_GATEWAY_URL")
    server_path = os.environ.get("ADCP_MCP_SERVER_PATH")
    
    logger.info(f"Initializing AdCP MCP client: gateway_url={gateway_url}, server_path={server_path}")
    
    try:
        if gateway_url:
            _mcp_client = create_adcp_mcp_client(
                transport="http",
                gateway_url=gateway_url
            )
            if _mcp_client:
                logger.info(f"✅ AdCP MCP client created with HTTP transport: {gateway_url}")
            else:
                logger.error(f"❌ Failed to create AdCP MCP client for gateway: {gateway_url}")
        elif server_path or os.path.exists(_get_default_server_path()):
            _mcp_client = create_adcp_mcp_client(
                transport="stdio",
                server_path=server_path
            )
            if _mcp_client:
                logger.info("✅ AdCP MCP client created with stdio transport")
            else:
                logger.warning("❌ Failed to create stdio MCP client")
        else:
            logger.info("No ADCP_GATEWAY_URL or local server found. Using fallback mode.")
    except Exception as e:
        logger.error(f"❌ Error creating MCP client: {e}")
        import traceback
        logger.error(traceback.format_exc())
    
    return _mcp_client


def _get_default_server_path() -> str:
    """Get the default path to the local MCP server"""
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    return os.path.join(base_dir, "synthetic_data", "mcp_mocks", "adcp_mcp_server.py")


def _call_mcp_tool(tool_name: str, arguments: Dict[str, Any]) -> Optional[str]:
    """Call an MCP tool if available, return None to use fallback"""
    # Lazy initialization - get or create client on first use
    client = _get_mcp_client()
    
    if client is None:
        logger.debug(f"MCP client not available for {tool_name}, using fallback")
        return None
    
    try:
        logger.info(f"🔌 Calling MCP tool: {tool_name}")
        with client:
            result = client.call_tool_sync(
                tool_use_id=f"adcp_{tool_name}",
                name=tool_name,
                arguments=arguments
            )
            if result and result.get("content"):
                logger.info(f"✅ MCP tool {tool_name} succeeded")
                return result["content"][0].get("text", json.dumps(result))
            else:
                logger.warning(f"⚠️ MCP tool {tool_name} returned empty result")
    except Exception as e:
        logger.warning(f"❌ MCP call failed for {tool_name}: {e}. Using fallback.")
        import traceback
        logger.debug(traceback.format_exc())
    
    return None


def reinitialize_mcp_client():
    """
    Force re-initialization of the MCP client.
    
    Call this after setting ADCP_GATEWAY_URL environment variable
    to pick up the new configuration.
    """
    global _mcp_client, _mcp_client_initialized
    _mcp_client = None
    _mcp_client_initialized = False
    logger.info("MCP client marked for re-initialization")
    return _get_mcp_client()


# ============================================================================
# Fallback Mock Data (used when MCP is not available)
# ============================================================================

MOCK_PRODUCTS = [
    {"product_id": "prod_espn_ctv_001", "product_name": "Premium Sports CTV - Live Events", "publisher_name": "ESPN", "channel": "ctv", "cpm_usd": 42.50, "min_spend_usd": 10000, "estimated_daily_impressions": 2500000, "brand_safety_tier": "tier_1", "audience_composition": "sports_enthusiasts:0.92,male_25_54:0.68"},
    {"product_id": "prod_fox_ctv_001", "product_name": "Fox Sports CTV - Premium Live", "publisher_name": "Fox Sports", "channel": "ctv", "cpm_usd": 45.00, "min_spend_usd": 15000, "estimated_daily_impressions": 3200000, "brand_safety_tier": "tier_1", "audience_composition": "sports_enthusiasts:0.89,male_25_54:0.71"},
    {"product_id": "prod_paramount_ctv_001", "product_name": "Paramount+ Sports CTV - NFL AFC", "publisher_name": "Paramount", "channel": "ctv", "cpm_usd": 40.00, "min_spend_usd": 10000, "estimated_daily_impressions": 2000000, "brand_safety_tier": "tier_1", "audience_composition": "sports_enthusiasts:0.87,male_25_54:0.65"},
    {"product_id": "prod_youtube_olv_001", "product_name": "YouTube Sports Content - Premium", "publisher_name": "Google", "channel": "online_video", "cpm_usd": 22.00, "min_spend_usd": 5000, "estimated_daily_impressions": 8500000, "brand_safety_tier": "tier_1", "audience_composition": "sports_enthusiasts:0.72,male_18_49:0.55"},
    {"product_id": "prod_youtube_env_001", "product_name": "YouTube Environmental Documentaries", "publisher_name": "Google", "channel": "online_video", "cpm_usd": 18.00, "min_spend_usd": 3000, "estimated_daily_impressions": 4200000, "brand_safety_tier": "tier_1", "audience_composition": "eco_conscious:0.85,hhi_75k_plus:0.62"},
]

MOCK_SIGNALS = [
    {"signal_id": "sig_lr_001", "signal_name": "Environmentally Conscious Homeowners", "signal_type": "audience", "data_provider": "LiveRamp/Experian", "size_individuals": 42000000, "cpm_usd": 1.75, "accuracy_score": 0.88, "is_live_ttd": True, "ttd_segment_id": "lr_exp_eco_homeowners"},
    {"signal_id": "sig_lr_002", "signal_name": "High Income HH $150K+", "signal_type": "audience", "data_provider": "LiveRamp/Experian", "size_individuals": 38000000, "cpm_usd": 1.85, "accuracy_score": 0.92, "is_live_ttd": True, "ttd_segment_id": "lr_exp_hhi_150k"},
    {"signal_id": "sig_lr_003", "signal_name": "Sports Enthusiasts - Active Lifestyle", "signal_type": "audience", "data_provider": "LiveRamp/Experian", "size_individuals": 51000000, "cpm_usd": 1.50, "accuracy_score": 0.85, "is_live_ttd": True, "ttd_segment_id": "lr_exp_sports_active"},
    {"signal_id": "sig_oracle_001", "signal_name": "Green Technology Intenders", "signal_type": "audience", "data_provider": "Oracle Data Cloud", "size_individuals": 28000000, "cpm_usd": 2.25, "accuracy_score": 0.87, "is_live_ttd": True, "ttd_segment_id": "oracle_green_tech"},
    {"signal_id": "sig_p39_001", "signal_name": "Contextual - Environmental Content", "signal_type": "contextual", "data_provider": "Peer39", "size_individuals": 0, "cpm_usd": 0.75, "accuracy_score": 0.94, "is_live_ttd": True, "ttd_segment_id": "p39_ctx_environmental"},
]


# ============================================================================
# Tool Implementations
# ============================================================================

@tool
def adcp_get_products(
    brief: str,
    channels: Optional[List[str]] = None,
    geo_required: Optional[List[str]] = None,
    brand_safety_tier: str = "tier_1",
    min_budget: Optional[float] = None,
    max_budget: Optional[float] = None
) -> str:
    """
    Discover publisher inventory products matching campaign brief (AdCP Media Buy Protocol).
    
    Args:
        brief: Natural language campaign brief describing target audience and objectives
        channels: Target channels (ctv, online_video, display, audio)
        geo_required: Required geographies (e.g., ["US:CA", "US:OR"])
        brand_safety_tier: Minimum brand safety tier (tier_1, tier_2, tier_3)
        min_budget: Minimum budget per publisher
        max_budget: Maximum budget per publisher
    
    Returns:
        JSON string with matching products from publisher inventory
    """
    logger.info(f"AdCP get_products: brief='{brief[:50]}...', channels={channels}")
    
    # Try MCP first
    mcp_result = _call_mcp_tool("get_products", {
        "brief": brief,
        "channels": channels,
        "brand_safety_tier": brand_safety_tier,
        "min_budget": min_budget,
        "max_budget": max_budget
    })
    if mcp_result:
        return mcp_result
    
    # Fallback to mock data
    results = []
    for p in MOCK_PRODUCTS:
        if channels and p["channel"] not in channels:
            continue
        if brand_safety_tier == "tier_1" and p["brand_safety_tier"] != "tier_1":
            continue
        if min_budget and p["min_spend_usd"] > min_budget:
            continue
        results.append(p)
    
    return json.dumps({
        "products": results,
        "total_found": len(results),
        "source": "fallback",
        "message": f"Found {len(results)} products matching '{brief[:30]}...'"
    }, indent=2)


@tool
def adcp_get_signals(
    brief: str,
    signal_types: Optional[List[str]] = None,
    decisioning_platform: str = "ttd",
    principal_id: Optional[str] = None
) -> str:
    """
    Discover audience and contextual signals for targeting (AdCP Signals Protocol).
    
    Args:
        brief: Natural language audience description
        signal_types: Signal types to filter (audience, contextual)
        decisioning_platform: Target DSP (ttd, dv360, xandr)
        principal_id: Advertiser/principal ID
    
    Returns:
        JSON string with matching audience segments and signals
    """
    logger.info(f"AdCP get_signals: brief='{brief[:50]}...', types={signal_types}")
    
    # Try MCP first
    mcp_result = _call_mcp_tool("get_signals", {
        "brief": brief,
        "signal_types": signal_types,
        "decisioning_platform": decisioning_platform
    })
    if mcp_result:
        return mcp_result
    
    # Fallback
    results = []
    for s in MOCK_SIGNALS:
        if signal_types and s["signal_type"] not in signal_types:
            continue
        
        is_live = s.get(f"is_live_{decisioning_platform}", False)
        segment_id = s.get(f"{decisioning_platform}_segment_id", "")
        
        results.append({
            "signal_id": s["signal_id"],
            "signal_name": s["signal_name"],
            "signal_type": s["signal_type"],
            "data_provider": s["data_provider"],
            "size_individuals": s["size_individuals"],
            "cpm_usd": s["cpm_usd"],
            "accuracy_score": s["accuracy_score"],
            "is_live": is_live,
            "segment_id": segment_id
        })
    
    return json.dumps({"signals": results, "total_found": len(results), "source": "fallback"}, indent=2)


@tool
def adcp_activate_signal(
    signal_agent_segment_id: str,
    decisioning_platform: str,
    principal_id: Optional[str] = None,
    account_id: Optional[str] = None
) -> str:
    """
    Activate a signal segment on a decisioning platform (AdCP Signals Protocol).
    
    Args:
        signal_agent_segment_id: Signal segment ID to activate
        decisioning_platform: Target platform (ttd, dv360, xandr)
        principal_id: Advertiser/principal ID
        account_id: Platform account ID
    
    Returns:
        JSON string with activation status and DSP segment ID
    """
    logger.info(f"AdCP activate_signal: {signal_agent_segment_id} on {decisioning_platform}")
    
    # Try MCP first
    mcp_result = _call_mcp_tool("activate_signal", {
        "signal_agent_segment_id": signal_agent_segment_id,
        "decisioning_platform": decisioning_platform,
        "principal_id": principal_id
    })
    if mcp_result:
        return mcp_result
    
    # Fallback
    signal = next((s for s in MOCK_SIGNALS if s["signal_id"] == signal_agent_segment_id), None)
    
    if not signal:
        return json.dumps({
            "status": "error",
            "error_code": "SIGNAL_NOT_FOUND",
            "message": f"Signal {signal_agent_segment_id} not found"
        })
    
    is_live = signal.get(f"is_live_{decisioning_platform}", False)
    segment_id = signal.get(f"{decisioning_platform}_segment_id", f"{decisioning_platform}_{signal_agent_segment_id}")
    
    if is_live:
        return json.dumps({
            "status": "already_active",
            "segment_id": segment_id,
            "message": "Signal already activated on this platform"
        })
    
    return json.dumps({
        "status": "activating",
        "task_id": f"task_{signal_agent_segment_id}_{decisioning_platform}",
        "estimated_completion_hours": 4,
        "message": "Signal activation initiated"
    })


@tool
def adcp_create_media_buy(
    buyer_ref: str,
    packages: List[Dict[str, Any]],
    brand_manifest: Optional[Dict[str, str]] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None
) -> str:
    """
    Create a media buy with publisher packages (AdCP Media Buy Protocol).
    
    Args:
        buyer_ref: Buyer reference ID for the media buy
        packages: List of package definitions with product_id, budget, targeting
        brand_manifest: Brand information (name, url)
        start_time: Campaign start time (ISO8601)
        end_time: Campaign end time (ISO8601)
    
    Returns:
        JSON string with media buy confirmation and package details
    """
    logger.info(f"AdCP create_media_buy: buyer_ref={buyer_ref}, packages={len(packages)}")
    
    # Try MCP first
    mcp_result = _call_mcp_tool("create_media_buy", {
        "buyer_ref": buyer_ref,
        "packages": packages,
        "start_time": start_time,
        "end_time": end_time
    })
    if mcp_result:
        return mcp_result
    
    # Fallback
    media_buy_id = f"mb_{buyer_ref[:10].replace(' ', '_')}"
    created_packages = []
    
    for i, pkg in enumerate(packages):
        budget = pkg.get("budget", 50000)
        cpm = 40
        estimated_impressions = int(budget / cpm * 1000)
        
        created_packages.append({
            "package_id": f"pkg_{i+1:03d}",
            "buyer_ref": pkg.get("buyer_ref", f"pkg_{i}"),
            "product_id": pkg.get("product_id"),
            "budget": budget,
            "status": "active",
            "estimated_impressions": estimated_impressions,
            "format_ids_to_provide": pkg.get("format_ids", ["video_standard_30s", "video_standard_15s"])
        })
    
    return json.dumps({
        "status": "completed",
        "media_buy_id": media_buy_id,
        "buyer_ref": buyer_ref,
        "creative_deadline": "2025-01-25T23:59:59Z",
        "packages": created_packages,
        "source": "fallback",
        "message": f"Media buy created with {len(created_packages)} packages"
    }, indent=2)


@tool
def adcp_get_media_buy_delivery(
    media_buy_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
) -> str:
    """
    Get delivery metrics for a media buy (AdCP Media Buy Protocol).
    
    Args:
        media_buy_id: Media buy ID to query
        start_date: Report start date
        end_date: Report end date
    
    Returns:
        JSON string with delivery metrics, pacing status, and projections
    """
    logger.info(f"AdCP get_media_buy_delivery: {media_buy_id}")
    
    # Try MCP first
    mcp_result = _call_mcp_tool("get_media_buy_delivery", {
        "media_buy_id": media_buy_id,
        "start_date": start_date,
        "end_date": end_date
    })
    if mcp_result:
        return mcp_result
    
    # Fallback
    return json.dumps({
        "media_buy_id": media_buy_id,
        "reporting_period": {"start": start_date or "2025-02-01", "end": end_date or "2025-02-15"},
        "summary": {
            "impressions_delivered": 1764706,
            "impressions_target": 3529412,
            "pacing_status": "on_track",
            "spend_usd": 75000,
            "budget_usd": 150000,
            "budget_utilized_pct": 50
        },
        "packages": [{
            "package_id": "pkg_001",
            "impressions_delivered": 1764706,
            "reach": 600000,
            "frequency": 2.94,
            "completion_rate": 0.82,
            "viewability_rate": 0.91,
            "ivt_rate": 0.011,
            "brand_safety_incidents": 0
        }],
        "projection": {
            "expected_final_impressions": 3529412,
            "expected_final_reach": 1200000,
            "confidence": "high"
        },
        "source": "fallback",
        "message": "Campaign pacing on track. No delivery concerns."
    }, indent=2)


@tool
def mcp_verify_brand_safety(
    properties: List[Dict[str, str]],
    brand_safety_tier: str = "tier_1",
    categories_blocked: Optional[List[str]] = None
) -> str:
    """
    Verify brand safety for publisher properties (MCP Verification Service).
    
    Args:
        properties: List of properties to verify (each with url and property_type)
        brand_safety_tier: Required brand safety tier
        categories_blocked: Categories to block (adult, violence, hate_speech, etc.)
    
    Returns:
        JSON string with verification results, scores, and recommendations
    """
    logger.info(f"MCP verify_brand_safety: {len(properties)} properties")
    
    # Try MCP first
    mcp_result = _call_mcp_tool("verify_brand_safety", {
        "properties": properties,
        "brand_safety_tier": brand_safety_tier
    })
    if mcp_result:
        return mcp_result
    
    # Fallback
    results = []
    for prop in properties:
        url = prop.get("url", "") if isinstance(prop, dict) else str(prop)
        
        if "espn" in url.lower() or "fox" in url.lower():
            score = 96
        elif "youtube" in url.lower():
            score = 89
        elif "twitch" in url.lower():
            score = 85
        else:
            score = 75
        
        tier = "tier_1" if score >= 90 else "tier_2" if score >= 75 else "tier_3"
        
        risk_flags = []
        if "youtube" in url.lower() or "twitch" in url.lower():
            risk_flags.append({"flag": "ugc_content_variability", "severity": "low", "description": "User-generated content requires real-time contextual filtering"})
        
        recommendation = "approved" if score >= 90 else "approved_with_conditions" if score >= 75 else "blocked"
        
        results.append({
            "url": url,
            "property_type": prop.get("property_type", "unknown") if isinstance(prop, dict) else "unknown",
            "brand_safety_score": score,
            "brand_safety_tier": tier,
            "risk_flags": risk_flags,
            "historical_incidents_90d": 0 if score >= 90 else 3,
            "recommendation": recommendation
        })
    
    return json.dumps({
        "verification_id": "ver_001",
        "timestamp": "2025-01-15T14:30:00Z",
        "properties": results,
        "summary": {
            "total_properties": len(results),
            "approved": sum(1 for r in results if r["recommendation"] == "approved"),
            "approved_with_conditions": sum(1 for r in results if r["recommendation"] == "approved_with_conditions"),
            "blocked": sum(1 for r in results if r["recommendation"] == "blocked")
        },
        "source": "fallback"
    }, indent=2)


@tool
def mcp_resolve_audience_reach(
    audience_segments: List[str],
    channels: Optional[List[str]] = None,
    geo: Optional[List[str]] = None,
    identity_types: Optional[List[str]] = None
) -> str:
    """
    Estimate cross-device reach for audience segments (MCP Identity Service).
    
    Args:
        audience_segments: List of audience segment IDs
        channels: Channels to estimate reach for (ctv, mobile, desktop)
        geo: Geographic regions
        identity_types: Identity types to use (uid2, rampid)
    
    Returns:
        JSON string with reach estimation, match rates, and frequency recommendations
    """
    logger.info(f"MCP resolve_audience_reach: segments={audience_segments}")
    
    # Try MCP first
    mcp_result = _call_mcp_tool("resolve_audience_reach", {
        "audience_segments": audience_segments,
        "channels": channels,
        "identity_types": identity_types
    })
    if mcp_result:
        return mcp_result
    
    # Fallback
    channels = channels or ["ctv", "mobile", "desktop"]
    
    channel_reach = []
    for ch in channels:
        if ch == "ctv":
            reach = 700000
            match_rate = 0.78
        elif ch == "mobile":
            reach = 1200000
            match_rate = 0.85
        else:
            reach = 500000
            match_rate = 0.72
        
        channel_reach.append({"channel": ch, "reach": reach, "match_rate": match_rate})
    
    return json.dumps({
        "total_reach_households": 2100000,
        "total_reach_individuals": 4800000,
        "channels": channel_reach,
        "cross_device_overlap": 0.15,
        "frequency_recommendation": 5,
        "confidence_interval": "±8%",
        "source": "fallback",
        "message": "Reach estimation complete. Match rates strong across all channels."
    }, indent=2)


@tool
def mcp_configure_brand_lift_study(
    study_name: str,
    study_type: str,
    campaign_id: Optional[str] = None,
    provider: str = "lucid",
    metrics: Optional[List[str]] = None,
    flight_start: Optional[str] = None,
    flight_end: Optional[str] = None,
    sample_size_target: Optional[Dict[str, int]] = None
) -> str:
    """
    Configure a brand lift or attribution measurement study (MCP Measurement Service).
    
    Args:
        study_name: Name of the study
        study_type: Type of study (brand_lift, foot_traffic, sales_lift, attribution)
        campaign_id: Associated campaign ID
        provider: Measurement provider (lucid, foursquare, nielsen)
        metrics: Metrics to measure (brand_awareness, ad_recall, purchase_intent)
        flight_start: Study start date
        flight_end: Study end date
        sample_size_target: Target sample sizes for control and exposed groups
    
    Returns:
        JSON string with study configuration, cost, and reporting schedule
    """
    logger.info(f"MCP configure_brand_lift_study: {study_name}, type={study_type}")
    
    # Try MCP first
    mcp_result = _call_mcp_tool("configure_brand_lift_study", {
        "study_name": study_name,
        "study_type": study_type,
        "provider": provider,
        "metrics": metrics,
        "flight_start": flight_start,
        "flight_end": flight_end
    })
    if mcp_result:
        return mcp_result
    
    # Fallback
    metrics = metrics or ["brand_awareness", "ad_recall", "purchase_intent"]
    sample_size_target = sample_size_target or {"control": 4500, "exposed": 12500}
    
    cost_map = {"brand_lift": 8000, "foot_traffic": 10000, "sales_lift": 15000, "attribution": 12000}
    
    return json.dumps({
        "status": "configured",
        "study_id": f"study_{provider[:3]}_{study_type[:4]}_001",
        "study_name": study_name,
        "configuration": {
            "study_type": study_type,
            "methodology": "survey_control_exposed" if study_type == "brand_lift" else "location_attribution",
            "metrics": metrics,
            "flight_dates": {"start": flight_start or "2025-02-01", "end": flight_end or "2025-03-15"},
            "sample_targets": sample_size_target,
            "expected_precision": {"min_detectable_effect": 0.08, "confidence_level": 0.95}
        },
        "cost_usd": cost_map.get(study_type, 8000),
        "reporting_schedule": {
            "interim_reports": ["2025-02-15", "2025-02-28"],
            "final_report": "2025-03-29"
        },
        "source": "fallback",
        "message": f"{study_type} study configured successfully. First interim report available Feb 15."
    }, indent=2)


# ============================================================================
# Export tools and MCP client for handler integration
# ============================================================================

# Standard tools (always available, with MCP fallback)
ADCP_TOOLS = [
    adcp_get_products,
    adcp_get_signals,
    adcp_activate_signal,
    adcp_create_media_buy,
    adcp_get_media_buy_delivery,
    mcp_verify_brand_safety,
    mcp_resolve_audience_reach,
    mcp_configure_brand_lift_study,
]

# MCP client for direct integration (when using managed MCP approach)
def get_adcp_mcp_tools():
    """
    Get AdCP tools for agent integration.
    
    Returns either:
    - MCP client (if MCP is enabled and available) for managed integration
    - Standard tool functions (fallback mode)
    
    Note: Uses lazy initialization - MCP client is created on first call.
    """
    client = _get_mcp_client()
    if client is not None:
        # Return MCP client for managed integration
        return [client]
    else:
        # Return standard tools with fallback
        return ADCP_TOOLS


def is_mcp_enabled() -> bool:
    """
    Check if MCP integration is enabled and available.
    
    Note: Uses lazy initialization - MCP client is created on first call.
    """
    client = _get_mcp_client()
    return client is not None
