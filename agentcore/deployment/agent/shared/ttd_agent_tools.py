"""
TTD REDS Tools for Strands Agents
=================================
Tools for querying The Trade Desk REDS feeds and managing campaigns.
Import these into your AgentCore handler file.

Usage:
    from ttd_tools import (
        query_impressions,
        query_clicks,
        query_conversions,
        query_video_events,
        query_viewability,
        get_campaign_settings,
        get_adgroup_settings,
        update_bid_factors,
        reallocate_budget,
        generate_report
    )
    
    agent = Agent(tools=[
        query_impressions,
        query_clicks,
        query_conversions,
        query_video_events,
        query_viewability,
        get_campaign_settings,
        get_adgroup_settings,
        update_bid_factors,
        reallocate_budget,
        generate_report
    ])
"""

from strands import tool
from typing import Optional, List, Dict, Any
from datetime import datetime
import json

# =============================================================================
# DATA LOADING (Replace with actual TTD API/data source in production)
# =============================================================================

def _load_reds_data(feed_name: str) -> List[Dict]:
    """
    Load REDS feed data. 
    In production, replace with actual TTD REDS API or data warehouse query.
    """
    import pandas as pd
    
    feed_files = {
        "impressions": "shared/sample_data/reds_impressions.csv",
        "clicks": "shared/sample_data/reds_clicks.csv",
        "conversions": "shared/sample_data/reds_conversions.csv",
        "video_events": "shared/sample_data/reds_video_events.csv",
        "viewability": "shared/sample_data/reds_viewability.csv",
    }
    
    file_path = feed_files.get(feed_name)
    if not file_path:
        return []
    
    try:
        df = pd.read_csv(file_path)
        return df.to_dict(orient="records")
    except Exception as e:
        return [{"error": str(e)}]


def _load_reference_data(entity_type: str) -> List[Dict]:
    """Load reference/configuration data."""
    import pandas as pd
    
    files = {
        "campaigns": "shared/sample_data/ref_campaigns.csv",
        "adgroups": "shared/sample_data/ref_adgroups.csv",
        "creatives": "shared/sample_data/ref_creatives.csv",
        "advertisers": "shared/sample_data/ref_advertisers.csv",
    }
    
    file_path = files.get(entity_type)
    if not file_path:
        return []
    
    try:
        df = pd.read_csv(file_path)
        return df.to_dict(orient="records")
    except Exception as e:
        return [{"error": str(e)}]


# =============================================================================
# QUERY TOOLS
# =============================================================================

@tool
def query_impressions(
    campaign_id: Optional[str] = None,
    adgroup_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    group_by: str = "adgroup",
    include_raw_data: bool = False,
    limit: int = 100
) -> Dict[str, Any]:
    """
    Query impression data from TTD REDS Impressions feed. Returns aggregated summary by default.
    
    Args:
        campaign_id: Filter by campaign ID (e.g., 'cmp_holiday_promo_2024')
        adgroup_id: Filter by ad group ID (e.g., 'ag_prospecting_display')
        start_date: Start date filter (YYYY-MM-DD format)
        end_date: End date filter (YYYY-MM-DD format)
        group_by: Aggregate by dimension (default 'adgroup'): 'campaign', 'adgroup', 'creative', 'device_type', 'country', 'site', 'none'
        include_raw_data: If True, include raw records in addition to aggregates (default False)
        limit: Maximum records to return (default 100)
    
    Returns:
        Dictionary with aggregated impression metrics including impressions, spend, CPM
    """
    import pandas as pd
    
    data = _load_reds_data("impressions")
    if not data:
        return {"error": "Failed to load impressions data: no data returned", "data": []}
    if "error" in data[0]:
        return {"error": f"Failed to load impressions data: {data[0]['error']}", "data": []}
    
    df = pd.DataFrame(data)
    
    # Apply filters
    if campaign_id:
        df = df[df["CampaignId"] == campaign_id]
    if adgroup_id:
        df = df[df["AdGroupId"] == adgroup_id]
    if start_date:
        df["LogTimestamp"] = pd.to_datetime(df["LogTimestamp"])
        df = df[df["LogTimestamp"] >= pd.to_datetime(start_date)]
    if end_date:
        df["LogTimestamp"] = pd.to_datetime(df["LogTimestamp"])
        df = df[df["LogTimestamp"] <= pd.to_datetime(end_date)]
    
    # Calculate overall summary
    total_impressions = len(df)
    total_spend = (df["TotalCostCPM"].sum() / 1000) if "TotalCostCPM" in df.columns else 0
    avg_cpm = df["TotalCostCPM"].mean() if "TotalCostCPM" in df.columns else 0
    avg_win_cpm = df["WinningBidPriceCPM"].mean() if "WinningBidPriceCPM" in df.columns else 0
    
    result = {
        "summary": {
            "total_impressions": int(total_impressions),
            "total_spend": round(total_spend, 2),
            "avg_cpm": round(avg_cpm, 2),
            "avg_win_cpm": round(avg_win_cpm, 2)
        }
    }
    
    # Aggregate by dimension (default behavior)
    if group_by and group_by != "none":
        group_col_map = {
            "campaign": "CampaignId",
            "adgroup": "AdGroupId", 
            "creative": "CreativeId",
            "device_type": "DeviceType",
            "country": "Country",
            "site": "Site"
        }
        group_col = group_col_map.get(group_by, group_by)
        
        if group_col in df.columns:
            agg_df = df.groupby(group_col).agg({
                "ImpressionId": "count",
                "TotalCostCPM": "sum",
                "BidPriceCPM": "mean",
                "WinningBidPriceCPM": "mean"
            }).reset_index()
            
            agg_df = agg_df.rename(columns={
                "ImpressionId": "impressions",
                "TotalCostCPM": "total_cost_cpm",
                "BidPriceCPM": "avg_bid_cpm",
                "WinningBidPriceCPM": "avg_win_cpm"
            })
            agg_df["total_spend"] = (agg_df["total_cost_cpm"] / 1000).round(2)
            agg_df = agg_df.sort_values("impressions", ascending=False)
            
            result["grouped_by"] = group_by
            result["breakdown"] = agg_df.head(limit).to_dict(orient="records")
    
    # Optionally include raw data
    if include_raw_data:
        result["raw_record_count"] = len(df)
        result["raw_data"] = df.head(limit).to_dict(orient="records")
    
    return result


@tool
def query_clicks(
    campaign_id: Optional[str] = None,
    adgroup_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    group_by: str = "adgroup",
    include_raw_data: bool = False,
    limit: int = 100
) -> Dict[str, Any]:
    """
    Query click data from TTD REDS Clicks feed. Returns aggregated summary by default.
    
    Args:
        campaign_id: Filter by campaign ID
        adgroup_id: Filter by ad group ID
        start_date: Start date filter (YYYY-MM-DD format)
        end_date: End date filter (YYYY-MM-DD format)
        group_by: Aggregate by dimension (default 'adgroup'): 'campaign', 'adgroup', 'creative', 'landing_page', 'none'
        include_raw_data: If True, include raw records in addition to aggregates (default False)
        limit: Maximum records to return
    
    Returns:
        Dictionary with aggregated click metrics including total clicks, valid clicks, CTR
    """
    import pandas as pd
    
    data = _load_reds_data("clicks")
    if not data:
        return {"error": "Failed to load clicks data: no data returned", "data": []}
    if "error" in data[0]:
        return {"error": f"Failed to load clicks data: {data[0]['error']}", "data": []}
    
    df = pd.DataFrame(data)
    
    if campaign_id:
        df = df[df["CampaignId"] == campaign_id]
    if adgroup_id:
        df = df[df["AdGroupId"] == adgroup_id]
    if start_date:
        df["ClickTimestamp"] = pd.to_datetime(df["ClickTimestamp"])
        df = df[df["ClickTimestamp"] >= pd.to_datetime(start_date)]
    if end_date:
        df["ClickTimestamp"] = pd.to_datetime(df["ClickTimestamp"])
        df = df[df["ClickTimestamp"] <= pd.to_datetime(end_date)]
    
    # Calculate overall summary
    total_clicks = len(df)
    valid_clicks = len(df[df["IsValid"] == True]) if "IsValid" in df.columns else total_clicks
    invalid_clicks = total_clicks - valid_clicks
    
    result = {
        "summary": {
            "total_clicks": int(total_clicks),
            "valid_clicks": int(valid_clicks),
            "invalid_clicks": int(invalid_clicks),
            "valid_rate_percent": round(valid_clicks / total_clicks * 100, 1) if total_clicks > 0 else 0
        }
    }
    
    # Aggregate by dimension (default behavior)
    if group_by and group_by != "none":
        group_col_map = {
            "campaign": "CampaignId",
            "adgroup": "AdGroupId",
            "creative": "CreativeId",
            "landing_page": "LandingPageUrl"
        }
        group_col = group_col_map.get(group_by, group_by)
        
        if group_col in df.columns:
            agg_dict = {"ClickId": "count"}
            if "IsValid" in df.columns:
                agg_dict["IsValid"] = "sum"
            
            agg_df = df.groupby(group_col).agg(agg_dict).reset_index()
            agg_df = agg_df.rename(columns={"ClickId": "clicks", "IsValid": "valid_clicks"})
            agg_df = agg_df.sort_values("clicks", ascending=False)
            
            result["grouped_by"] = group_by
            result["breakdown"] = agg_df.head(limit).to_dict(orient="records")
    
    # Optionally include raw data
    if include_raw_data:
        result["raw_record_count"] = len(df)
        result["raw_data"] = df.head(limit).to_dict(orient="records")
    
    return result


@tool
def query_conversions(
    campaign_id: Optional[str] = None,
    adgroup_id: Optional[str] = None,
    attribution_type: Optional[str] = None,
    conversion_type: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    group_by: str = "adgroup",
    include_raw_data: bool = False,
    limit: int = 100
) -> Dict[str, Any]:
    """
    Query conversion data from TTD REDS Conversions feed. Returns aggregated summary by default.
    
    Args:
        campaign_id: Filter by campaign ID
        adgroup_id: Filter by ad group ID
        attribution_type: Filter by 'ClickThrough' or 'ViewThrough'
        conversion_type: Filter by type: 'Purchase', 'Add to Cart', 'Lead Form', etc.
        start_date: Start date filter (YYYY-MM-DD format)
        end_date: End date filter (YYYY-MM-DD format)
        group_by: Aggregate by (default 'adgroup'): 'campaign', 'adgroup', 'attribution_type', 'conversion_type', 'none'
        include_raw_data: If True, include raw records in addition to aggregates (default False)
        limit: Maximum records to return
    
    Returns:
        Dictionary with aggregated conversion metrics including count, revenue, ROAS, and attribution breakdown
    """
    import pandas as pd
    
    data = _load_reds_data("conversions")
    if not data:
        return {"error": "Failed to load conversions data: no data returned", "data": []}
    if "error" in data[0]:
        return {"error": f"Failed to load conversions data: {data[0]['error']}", "data": []}
    
    df = pd.DataFrame(data)
    
    if campaign_id:
        df = df[df["CampaignId"] == campaign_id]
    if adgroup_id:
        df = df[df["AdGroupId"] == adgroup_id]
    if attribution_type:
        df = df[df["AttributionType"] == attribution_type]
    if conversion_type:
        df = df[df["ConversionType"] == conversion_type]
    if start_date:
        df["ConversionTimestamp"] = pd.to_datetime(df["ConversionTimestamp"])
        df = df[df["ConversionTimestamp"] >= pd.to_datetime(start_date)]
    if end_date:
        df["ConversionTimestamp"] = pd.to_datetime(df["ConversionTimestamp"])
        df = df[df["ConversionTimestamp"] <= pd.to_datetime(end_date)]
    
    # Calculate overall summary
    total_conversions = len(df)
    total_revenue = df["ConversionValue"].sum() if "ConversionValue" in df.columns else 0
    click_through = len(df[df["AttributionType"] == "ClickThrough"]) if "AttributionType" in df.columns else 0
    view_through = len(df[df["AttributionType"] == "ViewThrough"]) if "AttributionType" in df.columns else 0
    avg_order_value = total_revenue / total_conversions if total_conversions > 0 else 0
    
    result = {
        "summary": {
            "total_conversions": int(total_conversions),
            "total_revenue": round(total_revenue, 2),
            "avg_order_value": round(avg_order_value, 2),
            "click_through_conversions": int(click_through),
            "view_through_conversions": int(view_through),
            "click_through_percent": round(click_through / total_conversions * 100, 1) if total_conversions > 0 else 0
        }
    }
    
    # Add conversion type breakdown
    if "ConversionType" in df.columns:
        type_breakdown = df["ConversionType"].value_counts().to_dict()
        result["summary"]["conversion_types"] = type_breakdown
    
    # Aggregate by dimension (default behavior)
    if group_by and group_by != "none":
        group_col_map = {
            "campaign": "CampaignId",
            "adgroup": "AdGroupId",
            "attribution_type": "AttributionType",
            "conversion_type": "ConversionType"
        }
        group_col = group_col_map.get(group_by, group_by)
        
        if group_col in df.columns:
            agg_df = df.groupby(group_col).agg({
                "ConversionId": "count",
                "ConversionValue": "sum"
            }).reset_index()
            
            agg_df = agg_df.rename(columns={
                "ConversionId": "conversions",
                "ConversionValue": "total_revenue"
            })
            agg_df["avg_order_value"] = (agg_df["total_revenue"] / agg_df["conversions"]).round(2)
            agg_df = agg_df.sort_values("conversions", ascending=False)
            
            result["grouped_by"] = group_by
            result["breakdown"] = agg_df.head(limit).to_dict(orient="records")
    
    # Optionally include raw data
    if include_raw_data:
        result["raw_record_count"] = len(df)
        result["raw_data"] = df.head(limit).to_dict(orient="records")
    
    return result


@tool
def query_video_events(
    campaign_id: Optional[str] = None,
    adgroup_id: Optional[str] = None,
    event_type: Optional[str] = None,
    group_by: str = "adgroup",
    include_raw_data: bool = False,
    limit: int = 100
) -> Dict[str, Any]:
    """
    Query video event data from TTD REDS Video Events feed. Returns aggregated summary by default.
    
    Args:
        campaign_id: Filter by campaign ID
        adgroup_id: Filter by ad group ID
        event_type: Filter by event: 'Start', 'FirstQuartile', 'Midpoint', 'ThirdQuartile', 'Complete', 'Skip', etc.
        group_by: Aggregate by dimension (default 'adgroup'): 'campaign', 'adgroup', 'creative', 'none'
        include_raw_data: If True, include raw records in addition to aggregates (default False)
        limit: Maximum records to return
    
    Returns:
        Dictionary with aggregated video metrics including starts, completions, VCR, and quartile completion rates
    """
    import pandas as pd
    
    data = _load_reds_data("video_events")
    if not data:
        return {"error": "Failed to load video events data: no data returned", "data": []}
    if "error" in data[0]:
        return {"error": f"Failed to load video events data: {data[0]['error']}", "data": []}
    
    df = pd.DataFrame(data)
    
    if campaign_id:
        df = df[df["CampaignId"] == campaign_id]
    if adgroup_id:
        df = df[df["AdGroupId"] == adgroup_id]
    if event_type:
        df = df[df["VideoEventType"] == event_type]
    
    # Calculate overall video metrics
    event_counts = df["VideoEventType"].value_counts().to_dict() if "VideoEventType" in df.columns else {}
    starts = event_counts.get("Start", 0)
    first_quartile = event_counts.get("FirstQuartile", 0)
    midpoint = event_counts.get("Midpoint", 0)
    third_quartile = event_counts.get("ThirdQuartile", 0)
    completes = event_counts.get("Complete", 0)
    skips = event_counts.get("Skip", 0)
    
    result = {
        "summary": {
            "total_events": len(df),
            "video_starts": int(starts),
            "video_completes": int(completes),
            "video_skips": int(skips),
            "vcr_percent": round(completes / starts * 100, 1) if starts > 0 else 0,
            "skip_rate_percent": round(skips / starts * 100, 1) if starts > 0 else 0,
            "quartile_completion": {
                "25_percent": round(first_quartile / starts * 100, 1) if starts > 0 else 0,
                "50_percent": round(midpoint / starts * 100, 1) if starts > 0 else 0,
                "75_percent": round(third_quartile / starts * 100, 1) if starts > 0 else 0,
                "100_percent": round(completes / starts * 100, 1) if starts > 0 else 0
            },
            "event_breakdown": event_counts
        }
    }
    
    # Aggregate by dimension (default behavior)
    if group_by and group_by != "none":
        group_col_map = {
            "campaign": "CampaignId",
            "adgroup": "AdGroupId",
            "creative": "CreativeId"
        }
        group_col = group_col_map.get(group_by, group_by)
        
        if group_col in df.columns:
            # Pivot to get event types as columns per group
            pivot_df = df.groupby([group_col, "VideoEventType"]).size().unstack(fill_value=0).reset_index()
            
            # Calculate VCR for each group
            if "Start" in pivot_df.columns and "Complete" in pivot_df.columns:
                pivot_df["vcr_percent"] = (pivot_df["Complete"] / pivot_df["Start"] * 100).round(1)
                pivot_df["vcr_percent"] = pivot_df["vcr_percent"].fillna(0)
            
            pivot_df = pivot_df.sort_values("Start" if "Start" in pivot_df.columns else group_col, ascending=False)
            
            result["grouped_by"] = group_by
            result["breakdown"] = pivot_df.head(limit).to_dict(orient="records")
    
    # Optionally include raw data
    if include_raw_data:
        result["raw_record_count"] = len(df)
        result["raw_data"] = df.head(limit).to_dict(orient="records")
    
    return result


@tool
def query_viewability(
    campaign_id: Optional[str] = None,
    adgroup_id: Optional[str] = None,
    vendor: Optional[str] = None,
    group_by: str = "adgroup",
    include_raw_data: bool = False,
    limit: int = 100
) -> Dict[str, Any]:
    """
    Query viewability data from TTD REDS Viewability feed. Returns aggregated summary by default.
    
    Args:
        campaign_id: Filter by campaign ID
        adgroup_id: Filter by ad group ID
        vendor: Filter by viewability vendor: 'IAS', 'DoubleVerify', 'Moat', 'ComScore'
        group_by: Aggregate by dimension (default 'adgroup'): 'campaign', 'adgroup', 'vendor', 'site', 'none'
        include_raw_data: If True, include raw records in addition to aggregates (default False)
        limit: Maximum records to return
    
    Returns:
        Dictionary with aggregated viewability metrics including viewability rate, avg viewable time, and bot traffic
    """
    import pandas as pd
    
    data = _load_reds_data("viewability")
    if not data:
        return {"error": "Failed to load viewability data: no data returned", "data": []}
    if "error" in data[0]:
        return {"error": f"Failed to load viewability data: {data[0]['error']}", "data": []}
    
    df = pd.DataFrame(data)
    
    if campaign_id:
        df = df[df["CampaignId"] == campaign_id]
    if adgroup_id:
        df = df[df["AdGroupId"] == adgroup_id]
    if vendor:
        df = df[df["ViewabilityVendor"] == vendor]
    
    # Calculate overall summary
    total_measured = len(df)
    viewable = df["InView"].sum() if "InView" in df.columns else 0
    viewability_rate = round(viewable / total_measured * 100, 1) if total_measured > 0 else 0
    avg_viewable_time = round(df["ViewableTime"].mean(), 1) if "ViewableTime" in df.columns else 0
    bot_traffic = df["BotTraffic"].sum() if "BotTraffic" in df.columns else 0
    bot_rate = round(bot_traffic / total_measured * 100, 2) if total_measured > 0 else 0
    
    result = {
        "summary": {
            "total_measured": int(total_measured),
            "viewable_impressions": int(viewable),
            "viewability_rate_percent": viewability_rate,
            "avg_viewable_time_seconds": avg_viewable_time,
            "suspected_bot_traffic": int(bot_traffic),
            "bot_rate_percent": bot_rate,
            "meets_mrc_standard": viewability_rate >= 70
        }
    }
    
    # Add vendor breakdown if multiple vendors
    if "ViewabilityVendor" in df.columns:
        vendor_breakdown = df.groupby("ViewabilityVendor").agg({
            "InView": ["count", "sum"]
        }).reset_index()
        vendor_breakdown.columns = ["vendor", "measured", "viewable"]
        vendor_breakdown["viewability_rate"] = (vendor_breakdown["viewable"] / vendor_breakdown["measured"] * 100).round(1)
        result["summary"]["vendor_breakdown"] = vendor_breakdown.to_dict(orient="records")
    
    # Aggregate by dimension (default behavior)
    if group_by and group_by != "none":
        group_col_map = {
            "campaign": "CampaignId",
            "adgroup": "AdGroupId",
            "vendor": "ViewabilityVendor",
            "site": "Site"
        }
        group_col = group_col_map.get(group_by, group_by)
        
        if group_col in df.columns:
            agg_dict = {"InView": ["count", "sum"]}
            if "ViewableTime" in df.columns:
                agg_dict["ViewableTime"] = "mean"
            if "BotTraffic" in df.columns:
                agg_dict["BotTraffic"] = "sum"
            
            agg_df = df.groupby(group_col).agg(agg_dict).reset_index()
            agg_df.columns = [group_col, "measured", "viewable", "avg_viewable_time", "bot_traffic"] if "ViewableTime" in df.columns else [group_col, "measured", "viewable"]
            agg_df["viewability_rate_percent"] = (agg_df["viewable"] / agg_df["measured"] * 100).round(1)
            agg_df["meets_mrc"] = agg_df["viewability_rate_percent"] >= 70
            agg_df = agg_df.sort_values("measured", ascending=False)
            
            result["grouped_by"] = group_by
            result["breakdown"] = agg_df.head(limit).to_dict(orient="records")
    
    # Optionally include raw data
    if include_raw_data:
        result["raw_record_count"] = len(df)
        result["raw_data"] = df.head(limit).to_dict(orient="records")
    
    return result


# =============================================================================
# SETTINGS TOOLS
# =============================================================================

@tool
def get_campaign_settings(campaign_id: str) -> Dict[str, Any]:
    """
    Get current configuration for a campaign.
    
    Args:
        campaign_id: The campaign ID (e.g., 'cmp_holiday_promo_2024')
    
    Returns:
        Dictionary with campaign settings including budget, dates, pacing mode, and status
    """
    data = _load_reference_data("campaigns")
    
    for campaign in data:
        if campaign.get("CampaignId") == campaign_id:
            return {
                "found": True,
                "campaign": campaign
            }
    
    return {
        "found": False,
        "error": f"Campaign '{campaign_id}' not found"
    }


@tool
def get_adgroup_settings(adgroup_id: str) -> Dict[str, Any]:
    """
    Get current configuration for an ad group including bid factors.
    
    Args:
        adgroup_id: The ad group ID (e.g., 'ag_prospecting_display')
    
    Returns:
        Dictionary with ad group settings including base bid, daily budget, and bid factors
    """
    data = _load_reference_data("adgroups")
    
    for adgroup in data:
        if adgroup.get("AdGroupId") == adgroup_id:
            # Add simulated bid factors (in production, pull from TTD API)
            adgroup["BidFactors"] = {
                "DeviceType": {
                    "Desktop": 1.0,
                    "Mobile": 0.9,
                    "Tablet": 0.85,
                    "CTV": 1.3,
                    "SmartTV": 1.25
                },
                "DayOfWeek": {
                    "Monday": 1.0, "Tuesday": 1.0, "Wednesday": 1.1,
                    "Thursday": 1.1, "Friday": 1.2, "Saturday": 0.9, "Sunday": 0.85
                },
                "TimeOfDay": {
                    "Morning": 0.9, "Afternoon": 1.1, "Evening": 1.2, "Night": 0.7
                }
            }
            return {
                "found": True,
                "adgroup": adgroup
            }
    
    return {
        "found": False,
        "error": f"Ad group '{adgroup_id}' not found"
    }


# =============================================================================
# ACTION TOOLS
# =============================================================================

@tool
def update_bid_factors(
    adgroup_id: str,
    dimension: str,
    adjustments: List[Dict[str, Any]],
    dry_run: bool = True
) -> Dict[str, Any]:
    """
    Update bid factors for an ad group by dimension.
    
    Args:
        adgroup_id: The ad group ID to update
        dimension: Dimension to adjust: 'device', 'geo', 'time', 'day_of_week', 'site'
        adjustments: List of adjustments, each with 'value' and 'factor'. 
                     Example: [{"value": "Mobile", "factor": 1.2}, {"value": "CTV", "factor": 1.5}]
        dry_run: If True, preview changes without applying. Set False to execute.
    
    Returns:
        Dictionary with change preview or confirmation of applied changes
    """
    # Get current settings
    current = get_adgroup_settings(adgroup_id)
    if not current.get("found"):
        return {"success": False, "error": current.get("error")}
    
    base_bid = current["adgroup"].get("BaseBidCPM", 5.0)
    
    # Calculate projected changes
    projected_changes = []
    for adj in adjustments:
        value = adj.get("value")
        new_factor = adj.get("factor", 1.0)
        old_factor = 1.0  # Would pull from current bid factors in production
        
        projected_changes.append({
            "dimension_value": value,
            "old_factor": old_factor,
            "new_factor": new_factor,
            "old_effective_bid_cpm": round(base_bid * old_factor, 2),
            "new_effective_bid_cpm": round(base_bid * new_factor, 2),
            "change_percent": round((new_factor / old_factor - 1) * 100, 1) if old_factor > 0 else 0
        })
    
    result = {
        "adgroup_id": adgroup_id,
        "dimension": dimension,
        "base_bid_cpm": base_bid,
        "adjustments_count": len(adjustments),
        "projected_changes": projected_changes,
        "dry_run": dry_run
    }
    
    if dry_run:
        result["status"] = "preview"
        result["message"] = "This is a preview. Set dry_run=False to apply changes."
    else:
        # In production, call TTD API here
        result["status"] = "applied"
        result["message"] = f"Bid factors updated for {dimension}"
        result["applied_at"] = datetime.now().isoformat()
    
    return result


@tool
def reallocate_budget(
    campaign_id: str,
    strategy: str = "performance",
    custom_allocations: Optional[Dict[str, float]] = None,
    dry_run: bool = True
) -> Dict[str, Any]:
    """
    Reallocate campaign budget across ad groups.
    
    Args:
        campaign_id: The campaign ID
        strategy: Allocation strategy - 'performance' (ROAS-weighted), 'even' (equal split), or 'custom'
        custom_allocations: For 'custom' strategy, dict of {adgroup_id: allocation_percent}
        dry_run: If True, preview changes without applying
    
    Returns:
        Dictionary with reallocation plan showing current vs proposed budgets
    """
    import pandas as pd
    
    # Get campaign
    campaigns = _load_reference_data("campaigns")
    campaign = next((c for c in campaigns if c.get("CampaignId") == campaign_id), None)
    
    if not campaign:
        return {"success": False, "error": f"Campaign '{campaign_id}' not found"}
    
    campaign_budget = campaign.get("Budget", 0)
    
    # Get ad groups for this campaign
    adgroups = _load_reference_data("adgroups")
    campaign_adgroups = [ag for ag in adgroups if ag.get("CampaignId") == campaign_id]
    
    if not campaign_adgroups:
        return {"success": False, "error": f"No ad groups found for campaign '{campaign_id}'"}
    
    # Calculate allocations
    if strategy == "even":
        n = len(campaign_adgroups)
        allocations = {ag["AdGroupId"]: 1.0 / n for ag in campaign_adgroups}
    elif strategy == "custom" and custom_allocations:
        total = sum(custom_allocations.values())
        allocations = {k: v / total for k, v in custom_allocations.items()}
    else:  # performance-based (default)
        # In production, calculate based on actual ROAS. Using placeholder scores here.
        import random
        scores = {ag["AdGroupId"]: random.uniform(0.5, 2.0) for ag in campaign_adgroups}
        total_score = sum(scores.values())
        allocations = {k: v / total_score for k, v in scores.items()}
    
    # Build reallocation plan
    plan = []
    for ag in campaign_adgroups:
        ag_id = ag["AdGroupId"]
        alloc = allocations.get(ag_id, 0)
        current_daily = ag.get("DailyBudget", 0)
        new_daily = round(campaign_budget * alloc / 30, 2)  # 30-day campaign
        
        plan.append({
            "adgroup_id": ag_id,
            "adgroup_name": ag.get("AdGroupName", ag_id),
            "allocation_percent": round(alloc * 100, 1),
            "current_daily_budget": current_daily,
            "proposed_daily_budget": new_daily,
            "change_amount": round(new_daily - current_daily, 2),
            "change_percent": round((new_daily / current_daily - 1) * 100, 1) if current_daily > 0 else 0
        })
    
    result = {
        "campaign_id": campaign_id,
        "campaign_budget": campaign_budget,
        "strategy": strategy,
        "adgroups_affected": len(plan),
        "reallocation_plan": plan,
        "dry_run": dry_run
    }
    
    if dry_run:
        result["status"] = "preview"
        result["message"] = "This is a preview. Set dry_run=False to apply changes."
    else:
        result["status"] = "applied"
        result["message"] = "Budget reallocated successfully"
        result["applied_at"] = datetime.now().isoformat()
    
    return result


@tool
def generate_report(
    report_type: str,
    campaign_id: Optional[str] = None,
    adgroup_id: Optional[str] = None,
    include_recommendations: bool = True
) -> Dict[str, Any]:
    """
    Generate a performance, attribution, viewability, or video report.
    
    Args:
        report_type: Type of report - 'performance', 'attribution', 'viewability', 'video'
        campaign_id: Optional campaign filter
        adgroup_id: Optional ad group filter
        include_recommendations: Whether to include optimization recommendations
    
    Returns:
        Dictionary with report data, summary metrics, and recommendations
    """
    import pandas as pd
    
    report = {
        "report_type": report_type,
        "generated_at": datetime.now().isoformat(),
        "filters": {
            "campaign_id": campaign_id,
            "adgroup_id": adgroup_id
        }
    }
    
    if report_type == "performance":
        # Load and aggregate data
        impressions = pd.DataFrame(_load_reds_data("impressions"))
        clicks = pd.DataFrame(_load_reds_data("clicks"))
        conversions = pd.DataFrame(_load_reds_data("conversions"))
        
        if campaign_id:
            impressions = impressions[impressions["CampaignId"] == campaign_id]
            clicks = clicks[clicks["CampaignId"] == campaign_id]
            conversions = conversions[conversions["CampaignId"] == campaign_id]
        
        total_impressions = len(impressions)
        total_clicks = len(clicks)
        total_conversions = len(conversions)
        total_spend = (impressions["TotalCostCPM"].sum() / 1000) if "TotalCostCPM" in impressions.columns else 0
        total_revenue = conversions["ConversionValue"].sum() if "ConversionValue" in conversions.columns else 0
        
        report["summary"] = {
            "impressions": int(total_impressions),
            "clicks": int(total_clicks),
            "conversions": int(total_conversions),
            "spend": round(total_spend, 2),
            "revenue": round(total_revenue, 2),
            "ctr_percent": round(total_clicks / total_impressions * 100, 2) if total_impressions > 0 else 0,
            "cvr_percent": round(total_conversions / total_clicks * 100, 2) if total_clicks > 0 else 0,
            "cpa": round(total_spend / total_conversions, 2) if total_conversions > 0 else 0,
            "roas": round(total_revenue / total_spend, 2) if total_spend > 0 else 0
        }
        
        if include_recommendations:
            recommendations = []
            if report["summary"]["ctr_percent"] < 1.5:
                recommendations.append({
                    "priority": "medium",
                    "type": "creative_refresh",
                    "message": f"CTR ({report['summary']['ctr_percent']}%) is below benchmark. Consider refreshing creatives."
                })
            if report["summary"]["roas"] < 1.0:
                recommendations.append({
                    "priority": "high",
                    "type": "budget_optimization",
                    "message": f"ROAS ({report['summary']['roas']}x) is below 1.0. Review targeting and bid strategy."
                })
            report["recommendations"] = recommendations
    
    elif report_type == "viewability":
        viewability = pd.DataFrame(_load_reds_data("viewability"))
        
        if campaign_id:
            viewability = viewability[viewability["CampaignId"] == campaign_id]
        
        total = len(viewability)
        viewable = viewability["InView"].sum() if "InView" in viewability.columns else 0
        
        report["summary"] = {
            "measured_impressions": int(total),
            "viewable_impressions": int(viewable),
            "viewability_rate_percent": round(viewable / total * 100, 1) if total > 0 else 0,
            "avg_viewable_time_seconds": round(viewability["ViewableTime"].mean(), 1) if "ViewableTime" in viewability.columns else 0
        }
        
        if include_recommendations and report["summary"]["viewability_rate_percent"] < 70:
            report["recommendations"] = [{
                "priority": "high",
                "type": "viewability_improvement",
                "message": f"Viewability ({report['summary']['viewability_rate_percent']}%) below MRC standard (70%). Consider above-fold targeting."
            }]
    
    elif report_type == "video":
        video = pd.DataFrame(_load_reds_data("video_events"))
        
        if campaign_id:
            video = video[video["CampaignId"] == campaign_id]
        
        event_counts = video["VideoEventType"].value_counts().to_dict() if "VideoEventType" in video.columns else {}
        starts = event_counts.get("Start", 0)
        completes = event_counts.get("Complete", 0)
        
        report["summary"] = {
            "video_starts": int(starts),
            "video_completes": int(completes),
            "vcr_percent": round(completes / starts * 100, 1) if starts > 0 else 0,
            "event_breakdown": event_counts
        }
        
        if include_recommendations and report["summary"]["vcr_percent"] < 60:
            report["recommendations"] = [{
                "priority": "medium",
                "type": "video_optimization",
                "message": f"VCR ({report['summary']['vcr_percent']}%) below benchmark (60%). Consider shorter video lengths."
            }]
    
    else:
        report["error"] = f"Unknown report type: {report_type}. Use 'performance', 'viewability', or 'video'."
    
    return report
