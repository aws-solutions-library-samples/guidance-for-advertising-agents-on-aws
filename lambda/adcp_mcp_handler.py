#!/usr/bin/env python3
"""
AdCP MCP Lambda Handler

This Lambda function implements the Ad Context Protocol (AdCP) for MCP Gateway.
It handles tool calls from the MCP Gateway and returns structured responses.

The Lambda reads data from CSV files that are bundled with the deployment package.
These CSV files come from synthetic_data/mcp_mocks/ and contain the actual
advertising ecosystem data (products, signals, campaigns, etc.).

Deployed via: agentcore/deployment/deploy_adcp_gateway.py
"""

import csv
import json
import logging
import os
from io import StringIO
from typing import Any, Dict, List, Optional

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ============================================================================
# Data Loading - Load from bundled CSV files
# ============================================================================

# Get the directory where this Lambda is deployed
LAMBDA_DIR = os.path.dirname(os.path.abspath(__file__))

def load_csv_data(filename: str) -> List[Dict[str, Any]]:
    """Load data from a CSV file bundled with the Lambda."""
    filepath = os.path.join(LAMBDA_DIR, "data", filename)
    
    # Try alternate filename if primary not found
    if not os.path.exists(filepath):
        # Try without spaces/parentheses variations
        alt_filename = filename.replace(" (1)", "").replace(" ", "_")
        alt_filepath = os.path.join(LAMBDA_DIR, "data", alt_filename)
        if os.path.exists(alt_filepath):
            filepath = alt_filepath
        else:
            logger.warning(f"CSV file not found: {filepath} or {alt_filepath}, using empty list")
            return []
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            data = []
            for row in reader:
                # Convert numeric fields
                processed_row = {}
                for key, value in row.items():
                    if value == '':
                        processed_row[key] = None
                    elif key in ['cpm_usd', 'avg_cpm_usd', 'min_spend_usd', 'accuracy_score', 
                                 'revenue_share_pct', 'coverage_percentage']:
                        try:
                            processed_row[key] = float(value)
                        except (ValueError, TypeError):
                            processed_row[key] = value
                    elif key in ['estimated_daily_impressions', 'estimated_daily_reach', 
                                 'size_individuals', 'size_households']:
                        try:
                            processed_row[key] = int(value)
                        except (ValueError, TypeError):
                            processed_row[key] = value
                    elif key in ['is_live_ttd', 'is_live_dv360', 'is_live_xandr']:
                        processed_row[key] = value.lower() == 'true'
                    else:
                        processed_row[key] = value
                data.append(processed_row)
            logger.info(f"Loaded {len(data)} records from {filename}")
            return data
    except Exception as e:
        logger.error(f"Error loading {filename}: {e}")
        return []


# Lazy-load data on first use
_PRODUCTS = None
_SIGNALS = None
_CAMPAIGNS = None
_MEDIA_BUYS = {}  # In-memory storage for created media buys


def get_products() -> List[Dict]:
    """Get products data, loading from CSV on first call."""
    global _PRODUCTS
    if _PRODUCTS is None:
        _PRODUCTS = load_csv_data("products.csv")
    return _PRODUCTS


def get_signals() -> List[Dict]:
    """Get signals data, loading from CSV on first call."""
    global _SIGNALS
    if _SIGNALS is None:
        _SIGNALS = load_csv_data("signals.csv")
    return _SIGNALS


def get_campaigns() -> List[Dict]:
    """Get campaigns data, loading from CSV on first call."""
    global _CAMPAIGNS
    if _CAMPAIGNS is None:
        _CAMPAIGNS = load_csv_data("campaigns.csv")
    return _CAMPAIGNS


# ============================================================================
# Lambda Handler
# ============================================================================

def handler(event, context):
    """Main Lambda handler for AdCP MCP tools."""
    logger.info(f"Received event: {json.dumps(event)}")
    
    # Extract tool name from context (AgentCore Gateway passes it here)
    # See: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-add-target-lambda.html
    raw_tool_name = None
    
    # Primary method: Get from context.client_context.custom (AgentCore Gateway format)
    if context and hasattr(context, 'client_context') and context.client_context:
        custom_context = getattr(context.client_context, 'custom', None)
        if custom_context:
            raw_tool_name = custom_context.get('bedrockAgentCoreToolName')
            logger.info(f"Got tool name from context.client_context.custom: {raw_tool_name}")
    
    # Fallback: Try to get from event (for direct invocation/testing)
    if not raw_tool_name:
        raw_tool_name = (
            event.get("tool_name") or 
            event.get("name") or 
            event.get("toolName") or
            event.get("tool", {}).get("name", "")
        )
        if raw_tool_name:
            logger.info(f"Got tool name from event: {raw_tool_name}")
    
    # MCP Gateway prefixes tool names with target name: "target-name___tool_name"
    # Strip the prefix to get the actual tool name
    if raw_tool_name and "___" in raw_tool_name:
        tool_name = raw_tool_name.split("___")[-1]
        logger.info(f"Stripped tool name prefix: {raw_tool_name} -> {tool_name}")
    else:
        tool_name = raw_tool_name or ""
    
    # For AgentCore Gateway, the event IS the arguments (not wrapped)
    # For direct invocation, arguments might be nested
    if "arguments" in event or "input" in event or "toolInput" in event:
        arguments = (
            event.get("arguments") or 
            event.get("input") or 
            event.get("toolInput") or
            event.get("tool", {}).get("input", {})
        )
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
    else:
        # AgentCore Gateway: event IS the arguments directly
        arguments = event if event else {}
    
    logger.info(f"Tool: {tool_name}, Arguments: {json.dumps(arguments)}")
    
    # Route to appropriate handler
    handlers = {
        "get_products": handle_get_products,
        "get_signals": handle_get_signals,
        "activate_signal": handle_activate_signal,
        "create_media_buy": handle_create_media_buy,
        "get_media_buy_delivery": handle_get_media_buy_delivery,
        "verify_brand_safety": handle_verify_brand_safety,
        "resolve_audience_reach": handle_resolve_audience_reach,
        "configure_brand_lift_study": handle_configure_study,
    }
    
    if tool_name in handlers:
        try:
            result = handlers[tool_name](arguments)
            return format_response(200, result)
        except Exception as e:
            logger.error(f"Error handling {tool_name}: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return format_response(500, {"error": str(e)})
    
    return format_response(400, {"error": f"Unknown tool: {tool_name}"})


def format_response(status_code: int, result: Dict) -> Dict:
    """Format response for MCP Gateway."""
    body = json.dumps(result)
    return {
        "statusCode": status_code,
        "body": body,
        "content": [{"type": "text", "text": json.dumps(result, indent=2)}]
    }


# ============================================================================
# Tool Handlers
# ============================================================================

def handle_get_products(args: Dict) -> Dict:
    """AdCP Media Buy Protocol - get_products (Official Schema)
    
    Discover publisher inventory products matching campaign criteria.
    Reference: system reference/schemas/source/media-buy/get-products-response.json
    """
    # Parse official schema request format
    brief = args.get("brief", "")
    brand_manifest = args.get("brand_manifest")
    filters = args.get("filters", {})
    
    # Extract filters (official schema)
    channels = filters.get("channels", [])
    delivery_type = filters.get("delivery_type")
    budget_range = filters.get("budget_range", {})
    min_budget = budget_range.get("min")
    max_budget = budget_range.get("max")
    countries = filters.get("countries", [])
    
    products = get_products()
    results = []
    
    for p in products:
        # Filter by channel (map CSV channel to official channels)
        if channels:
            product_channel = p.get("channel", "")
            # Map old channel names to official enum
            channel_map = {"ctv": "ctv", "online_video": "video", "display": "display", "audio": "audio"}
            mapped_channel = channel_map.get(product_channel, product_channel)
            if mapped_channel not in channels and product_channel not in channels:
                continue
        
        # Filter by delivery type
        if delivery_type:
            # Infer delivery type from product data
            product_delivery = "guaranteed" if p.get("brand_safety_tier") == "tier_1" else "non_guaranteed"
            if product_delivery != delivery_type:
                continue
        
        # Filter by budget
        product_min_spend = p.get("min_spend_usd", 0)
        if min_budget and product_min_spend > min_budget:
            continue
        
        # Build official schema product response
        cpm = p.get("avg_cpm_usd") or p.get("cpm_usd") or 25.0
        publisher_domain = p.get("publisher_name", "").lower().replace(" ", "") + ".com"
        
        # Build format_ids from format_types
        format_types_str = p.get("format_types", "")
        format_types = format_types_str.split(",") if format_types_str else ["video_30s", "video_15s"]
        format_ids = []
        for ft in format_types:
            ft = ft.strip()
            if "video" in ft.lower() or "30s" in ft or "15s" in ft:
                duration = 30000 if "30" in ft else 15000 if "15" in ft else 6000 if "6" in ft else 30000
                format_ids.append({
                    "agent_url": "https://creatives.adcontextprotocol.org",
                    "id": "video_hosted",
                    "duration_ms": duration
                })
            elif "display" in ft.lower() or "banner" in ft.lower():
                format_ids.append({
                    "agent_url": "https://creatives.adcontextprotocol.org",
                    "id": "display_static",
                    "width": 300,
                    "height": 250
                })
        
        if not format_ids:
            format_ids = [{"agent_url": "https://creatives.adcontextprotocol.org", "id": "video_hosted", "duration_ms": 30000}]
        
        # Build official schema product
        product_result = {
            "product_id": p.get("product_id"),
            "name": p.get("product_name"),
            "description": f"{p.get('product_name')} inventory from {p.get('publisher_name')}",
            "publisher_properties": [{
                "publisher_domain": publisher_domain,
                "selection_type": "by_tag",
                "property_tags": [p.get("channel", "video"), "premium"]
            }],
            "format_ids": format_ids,
            "delivery_type": "guaranteed" if p.get("brand_safety_tier") == "tier_1" else "non_guaranteed",
            "pricing_options": [{
                "pricing_option_id": f"cpm_{p.get('product_id', 'default')}",
                "pricing_model": "cpm",
                "rate": cpm,
                "currency": "USD",
                "min_spend": product_min_spend
            }],
            "estimated_exposures": p.get("estimated_daily_impressions") or 1000000,
            "delivery_measurement": {
                "provider": "Nielsen DAR" if p.get("brand_safety_tier") == "tier_1" else "Google Ad Manager",
                "notes": "MRC-accredited viewability measurement"
            }
        }
        
        # Add brief_relevance if brief provided
        if brief:
            product_result["brief_relevance"] = f"Matches campaign requirements: {brief[:80]}..."
        
        results.append(product_result)
    
    # Return official schema response
    return {"products": results}


def handle_get_signals(args: Dict) -> Dict:
    """AdCP Signals Protocol - get_signals (Official Schema)
    
    Discover audience and contextual signals for targeting.
    Reference: system reference/schemas/source/signals/get-signals-response.json
    """
    # Parse official schema request format
    signal_spec = args.get("signal_spec", args.get("brief", ""))
    deliver_to = args.get("deliver_to", {})
    filters = args.get("filters", {})
    max_results = args.get("max_results")
    
    # Extract filters
    catalog_types = filters.get("catalog_types", [])
    data_providers = filters.get("data_providers", [])
    max_cpm = filters.get("max_cpm")
    min_coverage = filters.get("min_coverage_percentage", 0)
    
    # Get requested platforms from deliver_to
    requested_deployments = deliver_to.get("deployments", [])
    requested_platforms = []
    for dep in requested_deployments:
        if dep.get("type") == "platform":
            requested_platforms.append(dep.get("platform"))
    
    # Default to TTD if no platforms specified
    if not requested_platforms:
        requested_platforms = ["the-trade-desk"]
    
    signals = get_signals()
    results = []
    
    for s in signals:
        # Filter by catalog type (map signal_type to catalog_type)
        signal_type = s.get("signal_type", "audience")
        catalog_type = "marketplace" if signal_type == "audience" else "custom" if signal_type == "contextual" else "owned"
        if catalog_types and catalog_type not in catalog_types:
            continue
        
        # Filter by data provider
        if data_providers and s.get("data_provider") not in data_providers:
            continue
        
        # Filter by max CPM
        cpm = s.get("cpm_usd", 0)
        if max_cpm and cpm > max_cpm:
            continue
        
        # Calculate coverage percentage from size
        size = s.get("size_individuals", 0) or 0
        coverage = (size / 335000000) * 100 if size > 0 else 5.0  # US population base
        if coverage < min_coverage:
            continue
        
        # Build deployments array for requested platforms
        deployments = []
        for platform in requested_platforms:
            # Map platform names
            platform_key = platform.replace("-", "_").replace("the_trade_desk", "ttd")
            is_live_key = f"is_live_{platform_key}"
            segment_id_key = f"{platform_key}_segment_id"
            
            is_live = s.get(is_live_key, s.get("is_live_ttd", False))
            segment_id = s.get(segment_id_key, s.get("ttd_segment_id", ""))
            
            deployment = {
                "type": "platform",
                "platform": platform,
                "is_live": is_live
            }
            if is_live and segment_id:
                deployment["activation_key"] = {"segment_id": segment_id}
            else:
                deployment["estimated_activation_duration_minutes"] = 240
            
            deployments.append(deployment)
        
        # Build official schema signal response
        results.append({
            "signal_agent_segment_id": s.get("signal_id"),
            "name": s.get("signal_name"),
            "description": f"{s.get('signal_name')} - {s.get('data_provider', 'Unknown provider')}",
            "signal_type": catalog_type,
            "data_provider": s.get("data_provider"),
            "coverage_percentage": round(coverage, 1),
            "deployments": deployments,
            "pricing": {
                "cpm": cpm,
                "currency": "USD"
            }
        })
        
        if max_results and len(results) >= max_results:
            break
    
    # Return official schema response
    return {"signals": results}


def handle_activate_signal(args: Dict) -> Dict:
    """AdCP Signals Protocol - activate_signal (Official Schema)
    
    Activate a signal segment on deployment targets.
    Reference: system reference/schemas/source/signals/activate-signal-response.json
    """
    signal_id = args.get("signal_agent_segment_id")
    deployments_requested = args.get("deployments", [])
    
    # Backward compatibility: support old format
    if not deployments_requested and args.get("decisioning_platform"):
        platform = args.get("decisioning_platform", "ttd")
        deployments_requested = [{"type": "platform", "platform": platform}]
    
    signals = get_signals()
    signal = next((s for s in signals if s.get("signal_id") == signal_id), None)
    
    if not signal:
        # Return error response per official schema
        return {
            "errors": [{
                "code": "SIGNAL_NOT_FOUND",
                "message": f"Signal {signal_id} not found"
            }]
        }
    
    # Build deployment results
    deployment_results = []
    for dep in deployments_requested:
        dep_type = dep.get("type", "platform")
        platform = dep.get("platform", "the-trade-desk")
        
        # Map platform names
        platform_key = platform.replace("-", "_").replace("the_trade_desk", "ttd")
        is_live_key = f"is_live_{platform_key}"
        segment_id_key = f"{platform_key}_segment_id"
        
        is_live = signal.get(is_live_key, signal.get("is_live_ttd", False))
        segment_id = signal.get(segment_id_key, signal.get("ttd_segment_id", ""))
        
        deployment_result = {
            "type": dep_type,
            "platform": platform,
            "is_live": is_live
        }
        
        if is_live and segment_id:
            deployment_result["activation_key"] = {"segment_id": segment_id}
            deployment_result["deployed_at"] = "2025-01-15T14:30:00Z"
        else:
            deployment_result["estimated_activation_duration_minutes"] = 240
        
        if dep.get("account"):
            deployment_result["account"] = dep.get("account")
        
        deployment_results.append(deployment_result)
    
    # Return success response per official schema
    return {"deployments": deployment_results}


def handle_create_media_buy(args: Dict) -> Dict:
    """AdCP Media Buy Protocol - create_media_buy (Official Schema)
    
    Create a media buy with publisher packages.
    Reference: system reference/schemas/source/media-buy/create-media-buy-response.json
    """
    import uuid
    
    buyer_ref = args.get("buyer_ref", "unknown")
    packages_input = args.get("packages", [])
    brand_manifest = args.get("brand_manifest")
    start_time = args.get("start_time")
    end_time = args.get("end_time")
    po_number = args.get("po_number")
    
    media_buy_id = f"mb_{buyer_ref[:10].replace(' ', '_')}_{uuid.uuid4().hex[:6]}"
    created_packages = []
    
    products = get_products()
    product_map = {p.get("product_id"): p for p in products}
    
    for i, pkg in enumerate(packages_input):
        budget = pkg.get("budget", 50000)
        product_id = pkg.get("product_id")
        product = product_map.get(product_id, {})
        cpm = product.get("avg_cpm_usd") or product.get("cpm_usd") or 40
        
        # Build official schema package response
        created_packages.append({
            "package_id": f"pkg_{media_buy_id}_{i+1:03d}",
            "buyer_ref": pkg.get("buyer_ref", f"{buyer_ref}_pkg_{i+1}"),
            "product_id": product_id,
            "budget": budget,
            "pacing": pkg.get("pacing", "even"),
            "pricing_option_id": pkg.get("pricing_option_id", f"cpm_{product_id}")
        })
    
    # Store media buy for later retrieval
    _MEDIA_BUYS[media_buy_id] = {
        "media_buy_id": media_buy_id,
        "buyer_ref": buyer_ref,
        "packages": created_packages,
        "start_time": start_time or "asap",
        "end_time": end_time or "2025-03-15T23:59:59Z",
        "status": "active"
    }
    
    # Return official schema success response
    return {
        "media_buy_id": media_buy_id,
        "buyer_ref": buyer_ref,
        "creative_deadline": "2025-01-25T23:59:59Z",
        "packages": created_packages
    }


def handle_get_media_buy_delivery(args: Dict) -> Dict:
    """AdCP Media Buy Protocol - get_media_buy_delivery (Official Schema)
    
    Get delivery metrics for media buys.
    Reference: system reference/schemas/source/media-buy/get-media-buy-delivery-response.json
    """
    media_buy_ids = args.get("media_buy_ids", [])
    buyer_refs = args.get("buyer_refs", [])
    status_filter = args.get("status_filter")
    start_date = args.get("start_date", "2025-02-01")
    end_date = args.get("end_date", "2025-02-15")
    
    # Backward compatibility: support old single media_buy_id format
    if not media_buy_ids and args.get("media_buy_id"):
        media_buy_ids = [args.get("media_buy_id")]
    
    # Build media buy deliveries
    media_buy_deliveries = []
    total_impressions = 0
    total_spend = 0
    
    for mb_id in media_buy_ids or ["mb_default_001"]:
        media_buy = _MEDIA_BUYS.get(mb_id, {})
        packages = media_buy.get("packages", [])
        
        # Generate realistic delivery metrics
        package_deliveries = []
        mb_impressions = 0
        mb_spend = 0
        
        for pkg in packages or [{"package_id": "pkg_001", "budget": 50000}]:
            budget = pkg.get("budget", 50000)
            spend = budget * 0.5  # 50% spent
            impressions = int(budget / 42.5 * 1000 * 0.5)  # Based on avg CPM, 50% delivered
            
            mb_impressions += impressions
            mb_spend += spend
            
            package_deliveries.append({
                "package_id": pkg.get("package_id", "pkg_001"),
                "buyer_ref": pkg.get("buyer_ref"),
                "impressions": impressions,
                "spend": spend,
                "pacing_index": 1.0,
                "pricing_model": "cpm",
                "rate": 42.50,
                "currency": "USD",
                "delivery_status": "delivering",
                "paused": False
            })
        
        total_impressions += mb_impressions
        total_spend += mb_spend
        
        media_buy_deliveries.append({
            "media_buy_id": mb_id,
            "buyer_ref": media_buy.get("buyer_ref"),
            "status": "active",
            "pricing_model": "cpm",
            "totals": {
                "impressions": mb_impressions,
                "spend": mb_spend,
                "effective_rate": 42.50
            },
            "by_package": package_deliveries
        })
    
    # Return official schema response
    return {
        "reporting_period": {
            "start": f"{start_date}T00:00:00Z",
            "end": f"{end_date}T23:59:59Z"
        },
        "currency": "USD",
        "aggregated_totals": {
            "impressions": total_impressions,
            "spend": total_spend,
            "media_buy_count": len(media_buy_deliveries)
        },
        "media_buy_deliveries": media_buy_deliveries
    }


def handle_verify_brand_safety(args: Dict) -> Dict:
    """MCP Verification Service - verify_brand_safety
    
    Verify brand safety for publisher properties.
    """
    properties = args.get("properties", [])
    tier_required = args.get("brand_safety_tier", "tier_1")
    
    products = get_products()
    product_urls = {p.get("property_url", "").lower(): p for p in products}
    
    results = []
    for prop in properties:
        url = prop.get("url", "") if isinstance(prop, dict) else str(prop)
        url_lower = url.lower()
        
        # Check if URL matches a known product
        matched_product = None
        for product_url, product in product_urls.items():
            if product_url and product_url in url_lower:
                matched_product = product
                break
        
        if matched_product:
            tier = matched_product.get("brand_safety_tier", "tier_2")
            score = 96 if tier == "tier_1" else 85 if tier == "tier_2" else 70
        elif "espn" in url_lower or "fox" in url_lower or "nbc" in url_lower:
            score = 96
            tier = "tier_1"
        elif "youtube" in url_lower or "google" in url_lower:
            score = 89
            tier = "tier_1"
        elif "twitch" in url_lower:
            score = 85
            tier = "tier_2"
        else:
            score = 75
            tier = "tier_2"
        
        risk_flags = []
        if "youtube" in url_lower or "twitch" in url_lower:
            risk_flags.append({
                "flag": "ugc_content_variability",
                "severity": "low",
                "description": "User-generated content requires real-time contextual filtering"
            })
        
        recommendation = "approved" if score >= 90 else "approved_with_conditions" if score >= 75 else "blocked"
        
        results.append({
            "url": url,
            "brand_safety_score": score,
            "brand_safety_tier": tier,
            "risk_flags": risk_flags,
            "historical_incidents_90d": 0 if score >= 90 else 3,
            "recommendation": recommendation
        })
    
    return {
        "verification_id": f"ver_{hash(str(properties)) % 10000:04d}",
        "timestamp": "2025-01-15T14:30:00Z",
        "properties": results,
        "summary": {
            "total_properties": len(results),
            "approved": sum(1 for r in results if r["recommendation"] == "approved"),
            "approved_with_conditions": sum(1 for r in results if r["recommendation"] == "approved_with_conditions"),
            "blocked": sum(1 for r in results if r["recommendation"] == "blocked")
        },
        "source": "mcp_gateway"
    }


def handle_resolve_audience_reach(args: Dict) -> Dict:
    """MCP Identity Service - resolve_audience_reach
    
    Estimate cross-device reach for audience segments.
    """
    audience_segments = args.get("audience_segments", [])
    channels = args.get("channels", ["ctv", "mobile", "desktop"])
    identity_types = args.get("identity_types", ["uid2", "rampid"])
    
    # Calculate reach based on signals data
    signals = get_signals()
    total_individuals = 0
    total_households = 0
    
    for seg_id in audience_segments:
        signal = next((s for s in signals if s.get("signal_id") == seg_id), None)
        if signal:
            total_individuals += signal.get("size_individuals", 0) or 0
            total_households += signal.get("size_households", 0) or 0
    
    # If no specific segments, use average
    if total_individuals == 0:
        total_individuals = 4800000
        total_households = 2100000
    
    channel_reach = []
    for ch in channels:
        if ch == "ctv":
            reach = int(total_households * 0.33)
            match_rate = 0.78
        elif ch == "mobile":
            reach = int(total_households * 0.57)
            match_rate = 0.85
        else:  # desktop
            reach = int(total_households * 0.24)
            match_rate = 0.72
        
        channel_reach.append({
            "channel": ch,
            "reach_households": reach,
            "match_rate": match_rate
        })
    
    return {
        "total_reach_households": total_households,
        "total_reach_individuals": total_individuals,
        "channels": channel_reach,
        "identity_types_used": identity_types,
        "cross_device_overlap": 0.15,
        "frequency_recommendation": 5,
        "confidence_interval": "±8%",
        "segments_analyzed": audience_segments,
        "source": "mcp_gateway",
        "message": "Reach estimation complete. Match rates strong across all channels."
    }


def handle_configure_study(args: Dict) -> Dict:
    """MCP Measurement Service - configure_brand_lift_study
    
    Configure a brand lift or attribution measurement study.
    """
    study_name = args.get("study_name", "Unnamed Study")
    study_type = args.get("study_type", "brand_lift")
    provider = args.get("provider", "lucid")
    metrics = args.get("metrics", ["brand_awareness", "ad_recall", "purchase_intent"])
    flight_start = args.get("flight_start")
    flight_end = args.get("flight_end")
    
    cost_map = {
        "brand_lift": 8000,
        "foot_traffic": 10000,
        "sales_lift": 15000,
        "attribution": 12000
    }
    
    methodology_map = {
        "brand_lift": "survey_control_exposed",
        "foot_traffic": "location_attribution",
        "sales_lift": "purchase_data_matching",
        "attribution": "multi_touch_attribution"
    }
    
    return {
        "status": "configured",
        "study_id": f"study_{provider[:3]}_{study_type[:4]}_{hash(study_name) % 1000:03d}",
        "study_name": study_name,
        "configuration": {
            "study_type": study_type,
            "provider": provider,
            "methodology": methodology_map.get(study_type, "survey_control_exposed"),
            "metrics": metrics,
            "flight_dates": {
                "start": flight_start or "2025-02-01",
                "end": flight_end or "2025-03-15"
            },
            "sample_targets": {"control": 4500, "exposed": 12500},
            "expected_precision": {
                "min_detectable_effect": 0.08,
                "confidence_level": 0.95
            }
        },
        "cost_usd": cost_map.get(study_type, 8000),
        "reporting_schedule": {
            "interim_reports": ["2025-02-15", "2025-02-28"],
            "final_report": "2025-03-29"
        },
        "source": "mcp_gateway",
        "message": f"{study_type} study configured successfully. First interim report available Feb 15."
    }
