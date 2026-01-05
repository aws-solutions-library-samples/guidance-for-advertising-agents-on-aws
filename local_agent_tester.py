#!/usr/bin/env python3
"""
Local Agent Tester - Test agents without deploying to AgentCore Runtime

Usage:
    python local_agent_tester.py --agent MediaPlanningAgent --query "Help me optimize my media plan"
    python local_agent_tester.py --list-agents
    python local_agent_tester.py --agent CampaignOptimizationAgent --scenario product_launch
"""

import os
import sys
import argparse
import json
from pathlib import Path

# Check if we're in a virtual environment, if not try to activate the deployment one
if not hasattr(sys, 'real_prefix') and not (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix):
    venv_path = Path(__file__).parent / ".venv-deployment"
    if venv_path.exists():
        # Add the virtual environment's site-packages to Python path
        site_packages = venv_path / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
        if site_packages.exists():
            sys.path.insert(0, str(site_packages))
            print(f"🔧 Using virtual environment: {venv_path}")

# Add the agent directory to Python path
AGENT_DIR = Path(__file__).parent / "agentcore" / "deployment" / "agent"
sys.path.insert(0, str(AGENT_DIR))

# Set up minimal environment for local testing
os.environ.setdefault('MEMORY_ID', 'default')
os.environ.setdefault('AWS_REGION', 'us-east-1') 
os.environ.setdefault('STACK_PREFIX', 'test')
os.environ.setdefault('UNIQUE_ID', 'local')
os.environ.setdefault('BYPASS_TOOL_CONSENT', 'true')

def setup_environment():
    """Set up environment variables for local testing"""
    print("🔧 Environment configured for local testing")
    print(f"   Memory ID: {os.environ.get('MEMORY_ID')}")
    print(f"   AWS Region: {os.environ.get('AWS_REGION')}")
    
    # Check if AWS credentials are available
    import boto3
    try:
        session = boto3.Session()
        credentials = session.get_credentials()
        if credentials and credentials.access_key:
            print("✅ AWS credentials found - full testing available")
            return True
        else:
            print("⚠️  No AWS credentials found")
            print("   Configure with: aws configure")
            print("   Agent creation will work, but Bedrock calls will fail")
            return False
    except Exception as e:
        print(f"⚠️  Could not check AWS credentials: {e}")
        return False

def get_available_agents():
    """Get list of available agents from instruction files"""
    instructions_dir = AGENT_DIR / "agent-instructions-library"
    if not instructions_dir.exists():
        return []
    
    agents = []
    for file_path in instructions_dir.glob("*.txt"):
        if not file_path.name.startswith("_"):  # Skip helper files
            agent_name = file_path.stem
            agents.append(agent_name)
    
    return sorted(agents)

def get_test_scenarios():
    """Get predefined test scenarios for different agent types"""
    return {
        "MediaPlanningAgent": [
            "Develop strategic media plan for Q4 holiday season",
            "Optimize inventory utilization for maximum yield",
            "Analyze advertiser-publisher value alignment"
        ],
        "CampaignOptimizationAgent": [
            "Create integrated campaign strategy for product launch with $1M budget",
            "Develop brand repositioning campaign targeting younger audience", 
            "Balance brand awareness and performance objectives"
        ],
        "YieldOptimizationAgent": [
            "Optimize yield for premium video inventory - current $12 CPM, target $18 CPM",
            "Analyze competitive yield positioning and pricing strategies",
            "Develop seasonal yield optimization for holiday shopping"
        ],
        "InventoryOptimizationAgent": [
            "Forecast inventory availability for Q1 2025 across all formats",
            "Identify premium inventory packaging opportunities",
            "Optimize inventory utilization and fill rate improvement"
        ],
        "CreativeSelectionAgent": [
            "Generate creative concepts for luxury automotive campaign",
            "Select optimal creative formats for mobile-first audience",
            "Create visual examples for seasonal holiday promotion"
        ],
        "AudienceIntelligenceAgent": [
            "Analyze audience segments for premium lifestyle brand",
            "Identify high-value audience overlap opportunities",
            "Segment analysis for cross-platform campaign targeting"
        ]
    }

def test_agent(agent_name, query, verbose=False):
    """Test a single agent with a query"""
    try:
        print(f"\n🤖 Testing {agent_name}")
        print(f"📝 Query: {query}")
        print("=" * 60)
        
        # Import handler after environment is set up
        from handler import GenericAgent
        
        # Create orchestrator
        orchestrator = GenericAgent()
        
        # Create agent
        print("🏗️  Creating agent...")
        agent = orchestrator.create_orchestrator(
            session_id="local-test-session",
            memory_id="default", 
            agent_name=agent_name
        )
        
        if agent is None:
            print(f"❌ Failed to create agent {agent_name}")
            return False
            
        print("✅ Agent created successfully")
        
        # Show agent configuration
        print(f"📋 Agent Configuration:")
        print(f"   Name: {agent.name}")
        print(f"   Model: {agent.model.model_id if hasattr(agent.model, 'model_id') else 'Unknown'}")
        print(f"   Tools: Available") 
        print(f"   System Prompt Length: {len(agent.system_prompt)} characters")
        
        # Override model for local testing if the configured model isn't available
        if hasattr(agent.model, 'model_id'):
            configured_model = agent.model.model_id
            # Map unavailable models to available ones for local testing
            model_fallbacks = {
                "global.anthropic.claude-sonnet-4-5-20250929-v1:0": "anthropic.claude-3-5-sonnet-20241022-v2:0",
                "us.anthropic.claude-sonnet-4-20250514-v1:0": "anthropic.claude-3-5-sonnet-20241022-v2:0"
            }
            
            if configured_model in model_fallbacks:
                fallback_model = model_fallbacks[configured_model]
                print(f"   Model Fallback: {configured_model} → {fallback_model}")
                agent.model.model_id = fallback_model
        
        # Test the agent with real credentials
        print("🚀 Invoking agent...")
        response = agent(query)
        
        # Display response summary and save full output
        print("\n📤 Agent Response:")
        print("-" * 40)
        
        if hasattr(response, 'message') and response.message:
            content = response.message.get('content', [])
            if content and len(content) > 0:
                response_text = content[0].get('text', str(response))
            else:
                response_text = str(response)
        else:
            response_text = str(response)
        
        # Create test-output directory if it doesn't exist
        output_dir = Path("test-output")
        output_dir.mkdir(exist_ok=True)
        
        # Generate output filename with timestamp
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = output_dir / f"{agent_name}_{timestamp}.json"
        
        # Save full response to file
        output_data = {
            "agent_name": agent_name,
            "query": query,
            "timestamp": timestamp,
            "response_text": response_text,
            "response_length": len(response_text),
            "has_visualizations": '<visualization-data' in response_text
        }
        
        # Extract visualizations for separate storage
        if '<visualization-data' in response_text:
            import re
            viz_pattern = r'<visualization-data type="([^"]+)">(.*?)</visualization-data>'
            visualizations = re.findall(viz_pattern, response_text, re.DOTALL)
            output_data["visualizations"] = []
            
            for viz_type, viz_data in visualizations:
                try:
                    parsed_viz = json.loads(viz_data.strip())
                    output_data["visualizations"].append({
                        "type": viz_type,
                        "data": parsed_viz
                    })
                except:
                    output_data["visualizations"].append({
                        "type": viz_type,
                        "data": viz_data.strip(),
                        "parse_error": True
                    })
        
        # Save to file
        with open(output_file, 'w') as f:
            json.dump(output_data, f, indent=2)
        
        # Show summary
        lines = response_text.split('\n')
        preview_lines = lines[:5]  # First 5 lines
        
        print('\n'.join(preview_lines))
        if len(lines) > 5:
            print(f"\n... [truncated - showing first 5 of {len(lines)} lines]")
        
        print(f"\n📊 Response Summary:")
        print(f"   Total Length: {len(response_text):,} characters")
        print(f"   Lines: {len(lines)}")
        if '<visualization-data' in response_text:
            viz_count = len(re.findall(r'<visualization-data', response_text))
            print(f"   Visualizations: {viz_count} found")
        print(f"   Full Output: {output_file}")
        
        # Extract and display visualizations if present
        if '<visualization-data' in response_text:
            print("\n📊 Visualizations Found:")
            print("-" * 40)
            import re
            viz_pattern = r'<visualization-data type="([^"]+)">(.*?)</visualization-data>'
            visualizations = re.findall(viz_pattern, response_text, re.DOTALL)
            
            for i, (viz_type, viz_data) in enumerate(visualizations, 1):
                print(f"{i}. Type: {viz_type}")
                try:
                    # Try to parse and show basic info
                    parsed = json.loads(viz_data.strip())
                    if 'title' in parsed:
                        print(f"   Title: {parsed['title']}")
                    print(f"   Size: {len(viz_data)} characters")
                except:
                    print(f"   Size: {len(viz_data)} characters (parse error)")
                print()
        
        print("✅ Test completed successfully")
        return True
        
    except Exception as e:
        print(f"❌ Test failed: {str(e)}")
        if verbose:
            import traceback
            print(f"Traceback: {traceback.format_exc()}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Test agents locally without AgentCore deployment")
    parser.add_argument("--agent", help="Agent name to test")
    parser.add_argument("--query", help="Query to send to the agent")
    parser.add_argument("--scenario", help="Use predefined scenario for the agent")
    parser.add_argument("--list-agents", action="store_true", help="List available agents")
    parser.add_argument("--list-scenarios", help="List scenarios for specific agent")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    
    args = parser.parse_args()
    
    # Set up environment (but don't check credentials yet for listing operations)
    has_real_credentials = setup_environment()
    
    # List available agents
    if args.list_agents:
        agents = get_available_agents()
        print(f"\n📋 Available Agents ({len(agents)}):")
        print("=" * 40)
        for agent in agents:
            print(f"  • {agent}")
        return
    
    # List scenarios for specific agent
    if args.list_scenarios:
        scenarios = get_test_scenarios()
        if args.list_scenarios in scenarios:
            print(f"\n📋 Test Scenarios for {args.list_scenarios}:")
            print("=" * 50)
            for i, scenario in enumerate(scenarios[args.list_scenarios], 1):
                print(f"  {i}. {scenario}")
        else:
            print(f"❌ No scenarios found for {args.list_scenarios}")
            available = list(scenarios.keys())
            print(f"Available agents with scenarios: {', '.join(available)}")
        return
    
    # Validate agent name
    if not args.agent:
        print("❌ Please specify an agent name with --agent")
        print("Use --list-agents to see available agents")
        return
    
    available_agents = get_available_agents()
    if args.agent not in available_agents:
        print(f"❌ Agent '{args.agent}' not found")
        print(f"Available agents: {', '.join(available_agents)}")
        return
    
    # Determine query
    query = None
    if args.query:
        query = args.query
    elif args.scenario:
        scenarios = get_test_scenarios()
        if args.agent in scenarios:
            try:
                scenario_index = int(args.scenario) - 1
                if 0 <= scenario_index < len(scenarios[args.agent]):
                    query = scenarios[args.agent][scenario_index]
                else:
                    print(f"❌ Scenario index {args.scenario} out of range")
                    return
            except ValueError:
                # Treat as scenario name/partial match
                matching_scenarios = [s for s in scenarios[args.agent] if args.scenario.lower() in s.lower()]
                if matching_scenarios:
                    query = matching_scenarios[0]
                else:
                    print(f"❌ No matching scenario found for '{args.scenario}'")
                    return
        else:
            print(f"❌ No scenarios available for {args.agent}")
            return
    else:
        # Use default query
        scenarios = get_test_scenarios()
        if args.agent in scenarios:
            query = scenarios[args.agent][0]
        else:
            query = f"Help me with {args.agent.replace('Agent', '').lower()} analysis"
    
    # Run the test
    success = test_agent(args.agent, query, args.verbose)
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
