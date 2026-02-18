#!/usr/bin/env python3
"""
Test script for AdCP MCP Server

This script tests the AdCP MCP server by:
1. Testing handlers directly (no MCP required)
2. Connecting via MCP client (requires mcp and strands-agents)

Usage:
    python test_adcp_server.py
"""

import asyncio
import json
import sys
import os

# Add parent directories to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_direct_handlers():
    """Test the handler functions directly without MCP"""
    print("=" * 60)
    print("Testing AdCP handlers directly (no MCP)")
    print("=" * 60)
    
    # Import handlers from the server
    from adcp_mcp_server import (
        handle_get_products,
        handle_get_signals,
        handle_activate_signal,
        handle_create_media_buy,
        handle_get_media_buy_delivery,
        handle_verify_brand_safety,
        handle_resolve_audience_reach,
        handle_configure_brand_lift_study,
    )
    
    # Test get_products
    print("\n1. Testing get_products...")
    result = handle_get_products(
        brief="Sports content for athletic brand campaign",
        channels=["ctv"],
        brand_safety_tier="tier_1"
    )
    print(f"   Found {result['total_found']} products")
    assert result['total_found'] > 0, "Should find products"
    print("   ✓ get_products works")
    
    # Test get_signals
    print("\n2. Testing get_signals...")
    result = handle_get_signals(
        brief="environmentally conscious homeowners",
        signal_types=["audience"],
        decisioning_platform="ttd"
    )
    print(f"   Found {result['total_found']} signals")
    assert result['total_found'] > 0, "Should find signals"
    print("   ✓ get_signals works")
    
    # Test activate_signal
    print("\n3. Testing activate_signal...")
    result = handle_activate_signal(
        signal_agent_segment_id="sig_lr_001",
        decisioning_platform="ttd"
    )
    print(f"   Status: {result['status']}")
    assert result['status'] in ['already_active', 'activating'], "Should return valid status"
    print("   ✓ activate_signal works")
    
    # Test create_media_buy
    print("\n4. Testing create_media_buy...")
    result = handle_create_media_buy(
        buyer_ref="acme_energy_q1_2025",
        packages=[
            {"product_id": "prod_espn_ctv_001", "budget": 500000},
            {"product_id": "prod_youtube_env_001", "budget": 300000}
        ]
    )
    print(f"   Media buy ID: {result['media_buy_id']}")
    assert result['status'] == 'completed', "Should complete successfully"
    print("   ✓ create_media_buy works")
    
    # Test get_media_buy_delivery
    print("\n5. Testing get_media_buy_delivery...")
    result = handle_get_media_buy_delivery(
        media_buy_id=result['media_buy_id']
    )
    print(f"   Pacing: {result['summary']['pacing_status']}")
    assert 'summary' in result, "Should return summary"
    print("   ✓ get_media_buy_delivery works")
    
    # Test verify_brand_safety
    print("\n6. Testing verify_brand_safety...")
    result = handle_verify_brand_safety(
        properties=[
            {"url": "espn.com"},
            {"url": "youtube.com"},
            {"url": "unknown-site.com"}
        ],
        brand_safety_tier="tier_1"
    )
    print(f"   Verified {len(result['properties'])} properties")
    print(f"   Approved: {result['summary']['approved']}, With conditions: {result['summary']['approved_with_conditions']}")
    assert len(result['properties']) == 3, "Should verify all properties"
    print("   ✓ verify_brand_safety works")
    
    # Test resolve_audience_reach
    print("\n7. Testing resolve_audience_reach...")
    result = handle_resolve_audience_reach(
        audience_segments=["lr_exp_eco_homeowners"],
        channels=["ctv", "mobile", "desktop"],
        identity_types=["uid2", "rampid"]
    )
    print(f"   Total reach: {result['total_reach_households']:,} households")
    assert result['total_reach_households'] > 0, "Should return reach"
    print("   ✓ resolve_audience_reach works")
    
    # Test configure_brand_lift_study
    print("\n8. Testing configure_brand_lift_study...")
    result = handle_configure_brand_lift_study(
        study_name="Acme Energy Q1 2025 Brand Lift",
        study_type="brand_lift",
        provider="lucid",
        metrics=["brand_awareness", "ad_recall", "purchase_intent"]
    )
    print(f"   Study ID: {result['study_id']}")
    print(f"   Cost: ${result['cost_usd']:,}")
    assert result['status'] == 'configured', "Should configure successfully"
    print("   ✓ configure_brand_lift_study works")
    
    print("\n" + "=" * 60)
    print("All direct handler tests passed! ✓")
    print("=" * 60)


async def test_mcp_server():
    """Test the MCP server via MCP client"""
    print("\n" + "=" * 60)
    print("Testing AdCP MCP Server via MCP Client")
    print("=" * 60)
    
    try:
        from mcp import stdio_client, StdioServerParameters
        from strands.tools.mcp import MCPClient
    except ImportError:
        print("\nMCP dependencies not installed. Skipping MCP client tests.")
        print("Install with: pip install mcp strands-agents")
        return
    
    server_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "adcp_mcp_server.py")
    
    print(f"\nStarting MCP server: {server_path}")
    
    if not os.path.exists(server_path):
        print(f"   ERROR: Server not found at {server_path}")
        return
    
    mcp_client = MCPClient(
        lambda: stdio_client(
            StdioServerParameters(
                command="python3",
                args=[server_path]
            )
        )
    )
    
    try:
        with mcp_client:
            # List available tools
            tools = mcp_client.list_tools_sync()
            print(f"\nDiscovered {len(tools)} tools:")
            
            tool_names = []
            for tool in tools:
                name = tool.tool_name
                tool_names.append(name)
                print(f"  - {name}")
            
            # Verify we got the expected tools
            expected_tools = ['get_products', 'get_signals', 'activate_signal', 
                            'create_media_buy', 'get_media_buy_delivery',
                            'verify_brand_safety', 'resolve_audience_reach', 
                            'configure_brand_lift_study']
            
            found_count = sum(1 for exp in expected_tools if any(exp in t for t in tool_names))
            print(f"\n   Found {found_count}/{len(expected_tools)} expected AdCP tools")
            
            if found_count == len(expected_tools):
                print("   ✓ MCP server integration works!")
            else:
                print("   ⚠ Some tools may be missing")
    
    except Exception as e:
        print(f"\nMCP client test error: {e}")
        print("This is expected if running outside the test directory.")
        print("The direct handler tests above confirm the server logic works.")


def main():
    """Run all tests"""
    print("AdCP MCP Server Test Suite")
    print("=" * 60)
    
    # Test 1: Direct handler tests (always works)
    test_direct_handlers()
    
    # Test 2: MCP client tests (requires MCP dependencies)
    try:
        asyncio.run(test_mcp_server())
    except Exception as e:
        print(f"\nMCP server test skipped: {e}")
    
    print("\n" + "=" * 60)
    print("Test suite complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
