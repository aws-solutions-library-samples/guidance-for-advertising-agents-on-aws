#!/usr/bin/env python3
"""
Generate AWS Config Script
This script generates the UI's aws-config.json file from deployed CloudFormation stacks
and agent configurations.
"""

import json
import os
import sys
import subprocess
from pathlib import Path
from datetime import datetime


def check_and_upgrade_aws_cli(profile=None):
    """Check if AWS CLI supports required services and upgrade if needed"""
    try:
        print("🔍 Checking AWS CLI compatibility...")

        # Test if cloudformation service is available (basic check)
        test_cmd = ["aws", "cloudformation", "help"]

        # Add profile if specified
        if (
            profile
            and profile.strip()
            and profile.strip().lower() not in ["default", ""]
        ):
            test_cmd.extend(["--profile", profile.strip()])

        # nosemgrep: dangerous-subprocess-use-audit - Validated AWS CLI test
        result = subprocess.run(test_cmd, capture_output=True, text=True, timeout=10)

        if result.returncode == 0:
            print("✅ AWS CLI is functional")
            return True

        # Check if error indicates opsworkscm bug or other service issues
        if "opsworkscm" in result.stderr.lower() or "KeyError" in result.stderr:
            print("⚠️  AWS CLI has opsworkscm bug - upgrading...")

            # Upgrade boto3, botocore, and awscli via pip (this fixes the venv AWS CLI)
            print("Upgrading boto3, botocore, and awscli...")
            upgrade_cmd = [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--upgrade",
                "--quiet",
                "boto3",
                "botocore",
                "awscli",
            ]

            # nosemgrep: dangerous-subprocess-use-audit - Hardcoded pip upgrade
            upgrade_result = subprocess.run(
                upgrade_cmd,
                capture_output=True,
                text=True,
                timeout=120,  # 2 minute timeout
            )

            if upgrade_result.returncode == 0:
                print("✅ Successfully upgraded AWS CLI components")

                # Verify the upgrade worked
                # nosemgrep: dangerous-subprocess-use-audit - Validated AWS CLI verify
                verify_result = subprocess.run(
                    test_cmd, capture_output=True, text=True, timeout=10
                )
                if verify_result.returncode == 0:
                    print("✅ Verified: AWS CLI is now functional")
                    return True
                else:
                    print("⚠️  AWS CLI upgraded but still has issues")
                    print(f"Error: {verify_result.stderr[:200]}")
                    return False
            else:
                print(f"❌ Failed to upgrade AWS CLI: {upgrade_result.stderr}")
                return False

        return True

    except subprocess.TimeoutExpired:
        print("❌ AWS CLI check timed out")
        return False
    except Exception as e:
        print(f"⚠️  Could not check/upgrade AWS CLI: {e}")
        return True  # Continue anyway


def validate_aws_parameter(param, param_name, allow_empty=False):
    """Validate AWS CLI parameters to prevent command injection"""
    if not param and not allow_empty:
        raise ValueError(f"{param_name} cannot be empty")
    
    if param:
        # Only allow alphanumeric, hyphens, underscores, and dots
        import re
        if not re.match(r'^[a-zA-Z0-9._-]+$', str(param)):
            raise ValueError(f"{param_name} contains invalid characters: {param}")
    
    return str(param) if param else ""


def get_stack_output(stack_name, output_key, region, profile):
    """Get CloudFormation stack output value"""
    try:
        # Security: Validate all parameters before using in subprocess
        stack_name = validate_aws_parameter(stack_name, "stack_name")
        output_key = validate_aws_parameter(output_key, "output_key")
        region = validate_aws_parameter(region, "region")
        
        cmd = [
            "aws",
            "cloudformation",
            "describe-stacks",
            "--stack-name",
            stack_name,
            "--query",
            f"Stacks[0].Outputs[?OutputKey=='{output_key}'].OutputValue",
            "--output",
            "text",
            "--region",
            region,
        ]

        # Only add profile if it's not empty and not "default"
        # Handle both empty strings and "default" profile name
        if (
            profile
            and profile.strip()
            and profile.strip().lower() not in ["default", ""]
        ):
            validated_profile = validate_aws_parameter(profile.strip(), "profile")
            cmd.extend(["--profile", validated_profile])

        # nosemgrep: dangerous-subprocess-use-audit - Validated AWS parameters
        result = subprocess.run(cmd, capture_output=True, text=True)

        if (
            result.returncode == 0
            and result.stdout.strip()
            and result.stdout.strip() != "None"
        ):
            return result.stdout.strip()
        elif result.returncode != 0:
            print(f"    Error: AWS CLI failed with return code {result.returncode}")
            if result.stderr.strip():
                print(f"    Error details: {result.stderr.strip()}")
    except Exception as e:
        print(f"Warning: Could not get {output_key} from {stack_name}: {e}")
    return None


def stack_exists(stack_name, region, profile):
    """Check if CloudFormation stack exists"""
    try:
        # Security: Validate all parameters before using in subprocess
        stack_name = validate_aws_parameter(stack_name, "stack_name")
        region = validate_aws_parameter(region, "region")
        
        cmd = [
            "aws",
            "cloudformation",
            "describe-stacks",
            "--stack-name",
            stack_name,
            "--region",
            region,
        ]

        # Always add profile parameter - AWS CLI will use it correctly
        if profile and profile.strip():
            validated_profile = validate_aws_parameter(profile.strip(), "profile")
            cmd.extend(["--profile", validated_profile])

        # nosemgrep: dangerous-subprocess-use-audit - Validated AWS parameters
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.returncode == 0
    except Exception as e:
        return False


def list_infrastructure_stacks(stack_prefix, region, profile):
    """List all stacks that might be infrastructure stacks"""
    try:
        # Security: Validate all parameters before using in subprocess
        stack_prefix = validate_aws_parameter(stack_prefix, "stack_prefix")
        region = validate_aws_parameter(region, "region")
        
        cmd = [
            "aws",
            "cloudformation",
            "list-stacks",
            "--stack-status-filter",
            "CREATE_COMPLETE",
            "UPDATE_COMPLETE",
            "UPDATE_ROLLBACK_COMPLETE",
            "DELETE_FAILED",
            "--query",
            f"StackSummaries[?contains(StackName, '{stack_prefix}') && contains(StackName, 'infrastructure')].StackName",
            "--output",
            "text",
            "--region",
            region,
        ]

        # Only add profile if it's not empty and not "default"
        # Handle both empty strings and "default" profile name
        if (
            profile
            and profile.strip()
            and profile.strip().lower() not in ["default", ""]
        ):
            validated_profile = validate_aws_parameter(profile.strip(), "profile")
            cmd.extend(["--profile", validated_profile])

        # nosemgrep: dangerous-subprocess-use-audit - Validated AWS parameters
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            stacks = [s.strip() for s in result.stdout.strip().split("\n") if s.strip()]
            return stacks
    except Exception as e:
        print(f"Error listing infrastructure stacks: {e}")
    return []


def get_agent_config_table_name(stack_prefix, unique_id, region, profile):
    """Get AgentConfigTable name from CloudFormation services stack"""
    services_stack_name = f"{stack_prefix}-infrastructure-services"
    
    print(f"    🔍 Retrieving AgentConfigTable name from {services_stack_name}...")
    
    # Try to get from CloudFormation output
    table_name = get_stack_output(
        services_stack_name, "AgentConfigTableName", region, profile
    )
    
    if table_name:
        print(f"    ✅ AgentConfigTable name: {table_name}")
        return table_name
    
    # Fallback: construct the expected table name based on naming convention
    if unique_id:
        fallback_name = f"{stack_prefix}-AgentConfig-{unique_id}"
        print(f"    ⚠️  Could not retrieve AgentConfigTable from CloudFormation, using fallback: {fallback_name}")
        return fallback_name
    
    print(f"    ⚠️  Could not determine AgentConfigTable name")
    return None


def get_infrastructure_config(stack_prefix, stack_suffix, region, profile):
    """Get infrastructure configuration from CloudFormation stacks"""
    core_stack_name = f"{stack_prefix}-infrastructure-core"
    services_stack_name = f"{stack_prefix}-infrastructure-services"

    print(f"🔍 Retrieving infrastructure configuration from multiple stacks...")
    print(
        f"    Debug: stack_prefix='{stack_prefix}', region='{region}', profile='{profile}'"
    )

    # Check which stacks exist
    print(f"    🔍 Checking for core stack: {core_stack_name}")
    core_stack_exists = stack_exists(core_stack_name, region, profile)
    print(f"    🔍 Checking for services stack: {services_stack_name}")
    services_stack_exists = stack_exists(services_stack_name, region, profile)

    # Handle different deployment scenarios
    if core_stack_exists and services_stack_exists:
        print(
            f"    ✅ Found split infrastructure: {core_stack_name} and {services_stack_name}"
        )
        infrastructure_stack_name = core_stack_name  # Primary stack for most resources
        services_stack_name_actual = services_stack_name  # Services stack
    elif core_stack_exists:
        print(f"    ✅ Found core stack only: {core_stack_name}")
        infrastructure_stack_name = core_stack_name
        services_stack_name_actual = None
    elif services_stack_exists:
        print(f"    ✅ Found services stack only: {services_stack_name}")
        infrastructure_stack_name = services_stack_name
        services_stack_name_actual = None
    else:
        # Fallback to legacy naming or alternative stacks
        print(f"    ❌ Neither {core_stack_name} nor {services_stack_name} found!")
        print(f"    🔍 Looking for alternative infrastructure stacks...")

        alt_stacks = list_infrastructure_stacks(stack_prefix, region, profile)
        if alt_stacks:
            print(f"    Found potential infrastructure stacks:")
            for stack in alt_stacks:
                print(f"      - {stack}")
            # Use the first one found
            infrastructure_stack_name = alt_stacks[0]
            services_stack_name_actual = None
            print(f"    Using stack: {infrastructure_stack_name}")
        else:
            print(f"    ❌ No infrastructure stacks found with prefix '{stack_prefix}'")
            return {}

    infrastructure_config = {}

    # Get Cognito configuration (typically in core stack)
    print(
        f"    🔍 Retrieving Cognito configuration from {infrastructure_stack_name}..."
    )
    user_pool_id = get_stack_output(
        infrastructure_stack_name, "UserPoolId", region, profile
    )
    user_pool_client_id = get_stack_output(
        infrastructure_stack_name, "UserPoolClientId", region, profile
    )
    identity_pool_id = get_stack_output(
        infrastructure_stack_name, "IdentityPoolId", region, profile
    )

    if user_pool_id and user_pool_client_id and identity_pool_id:
        infrastructure_config["cognito"] = {
            "userPoolId": user_pool_id,
            "userPoolWebClientId": user_pool_client_id,
            "identityPoolId": identity_pool_id,
            "mandatorySignIn": True,
        }
        print(
            f"    ✅ Cognito configuration retrieved from {infrastructure_stack_name}"
        )
    else:
        print(
            f"    ⚠️  Could not retrieve complete Cognito configuration from {infrastructure_stack_name}"
        )

    # Get DynamoDB table for creatives (could be in either stack)
    print(f"    🔍 Retrieving DynamoDB configuration...")
    creatives_table_name = get_stack_output(
        infrastructure_stack_name, "GeneratedContentTableName", region, profile
    )
    if not creatives_table_name and services_stack_name_actual:
        creatives_table_name = get_stack_output(
            services_stack_name_actual, "GeneratedContentTableName", region, profile
        )

    if creatives_table_name:
        infrastructure_config["creativesDynamoDBTable"] = creatives_table_name
        print(f"    ✅ Creatives DynamoDB table: {creatives_table_name}")
    else:
        print(f"    ⚠️  Could not retrieve creatives DynamoDB table name")
        infrastructure_config["creativesDynamoDBTable"] = ""

    # Get UI bucket and CloudFront distribution (typically in core stack)
    print(
        f"    🔍 Retrieving UI hosting configuration from {infrastructure_stack_name}..."
    )
    ui_bucket_name = get_stack_output(
        infrastructure_stack_name, "UIBucketName", region, profile
    )
    if ui_bucket_name:
        infrastructure_config["uiBucketName"] = ui_bucket_name
        print(f"    ✅ UI bucket name: {ui_bucket_name}")
    else:
        print(
            f"    ⚠️  Could not retrieve UI bucket name from {infrastructure_stack_name}"
        )
        infrastructure_config["uiBucketName"] = ""

    cloudfront_distribution_id = get_stack_output(
        infrastructure_stack_name, "UICloudFrontDistributionId", region, profile
    )
    if cloudfront_distribution_id:
        infrastructure_config["cloudFrontDistributionId"] = cloudfront_distribution_id
        print(f"    ✅ CloudFront distribution ID: {cloudfront_distribution_id}")
    else:
        print(
            f"    ⚠️  Could not retrieve CloudFront distribution ID from {infrastructure_stack_name}"
        )
        infrastructure_config["cloudFrontDistributionId"] = ""

    # Get demo log group name (could be in either stack)
    print(f"    🔍 Retrieving CloudWatch configuration...")
    demo_log_group_name = get_stack_output(
        infrastructure_stack_name, "DemoLogGroupName", region, profile
    )
    if not demo_log_group_name and services_stack_name_actual:
        demo_log_group_name = get_stack_output(
            services_stack_name_actual, "DemoLogGroupName", region, profile
        )

    if demo_log_group_name:
        infrastructure_config["demoLogGroupName"] = demo_log_group_name
        print(f"    ✅ Demo log group name: {demo_log_group_name}")
    else:
        print(f"    ⚠️  Could not retrieve demo log group name")
        infrastructure_config["demoLogGroupName"] = ""
    return infrastructure_config


def get_unique_id(stack_prefix, region):
    """Get unique ID from local file"""
    unique_id_file = f".unique-id-{stack_prefix}-{region}"
    print(f"    Looking for unique ID file: {unique_id_file}")

    if os.path.exists(unique_id_file):
        try:
            with open(unique_id_file, "r") as f:
                unique_id = f.read().strip()
                print(f"    Found unique ID: {unique_id}")
                return unique_id
        except Exception as e:
            print(f"    Error reading unique ID file: {e}")
            return None
    else:
        print(f"    Unique ID file not found: {unique_id_file}")
        return None


def get_agentcore_agents(stack_prefix, unique_id):
    """Get AgentCore agents from local deployment file or discover from filesystem"""
    agentcore_file = f".agentcore-agents-{stack_prefix}-{unique_id}.json"

    # First try to load from deployment file
    if os.path.exists(agentcore_file):
        try:
            with open(agentcore_file, "r") as f:
                agentcore_data = json.load(f)
            print(f"    ✅ Loaded AgentCore agents from {agentcore_file}")

            # Check if AgentCore deployment was explicitly skipped
            if agentcore_data.get("skipped", False):
                print(
                    f"    ⚠️  AgentCore deployment was skipped: {agentcore_data.get('reason', 'No reason provided')}"
                )
                return []

            deployed_agents = agentcore_data.get("deployed_agents", [])

            # Handle both old format (list of strings) and new format (list of objects)
            agent_names = []
            for agent in deployed_agents:
                if isinstance(agent, dict):
                    # New format: object with runtime information
                    agent_name = agent.get("name")
                    if agent_name:
                        agent_names.append(agent_name)
                        print(
                            f"    ✅ Found AgentCore agent with runtime info: {agent_name}"
                        )
                elif isinstance(agent, str):
                    # Old format: just agent name
                    agent_names.append(agent)
                    print(f"    ✅ Found AgentCore agent: {agent}")

            return agent_names
        except Exception as e:
            print(f"    ❌ Error loading AgentCore agents from {agentcore_file}: {e}")
    else:
        print(f"    ⚠️  AgentCore agents file not found: {agentcore_file}")

    # Fallback: discover AgentCore agents from filesystem
    agentcore_agents_dir = Path("agentcore/agents")
    if agentcore_agents_dir.exists():
        print(f"    🔍 Discovering AgentCore agents from filesystem...")
        agents = []
        for agent_dir in agentcore_agents_dir.iterdir():
            if agent_dir.is_dir() and agent_dir.name != "__pycache__":
                config_file = agent_dir / "config.json"
                if config_file.exists():
                    agents.append(agent_dir.name)
                    print(f"    ✅ Found AgentCore agent: {agent_dir.name}")
        return agents

    return []


def get_agentcore_runtime_info(stack_prefix, unique_id):
    """Get AgentCore runtime information from local deployment file"""
    agentcore_file = f".agentcore-agents-{stack_prefix}-{unique_id}.json"
    runtime_info = {}

    if os.path.exists(agentcore_file):
        try:
            with open(agentcore_file, "r") as f:
                agentcore_data = json.load(f)

            # Check if AgentCore deployment was explicitly skipped
            if agentcore_data.get("skipped", False):
                return {}

            deployed_agents = agentcore_data.get("deployed_agents", [])

            # Extract runtime information for each agent
            for agent in deployed_agents:
                if isinstance(agent, dict):
                    agent_name = agent.get("name")
                    if agent_name:
                        runtime_info[agent_name] = {
                            "runtime_id": agent.get("runtime_id"),
                            "runtime_arn": agent.get("runtime_arn"),
                            "container_uri": agent.get("container_uri"),
                            "deployment_time": agent.get("deployment_time"),
                        }
                        print(
                            f"    ✅ Loaded runtime info for AgentCore agent: {agent_name}"
                        )

            return runtime_info
        except Exception as e:
            print(
                f"    ❌ Error loading AgentCore runtime info from {agentcore_file}: {e}"
            )

    return {}


def get_memory_record_id(stack_prefix, unique_id):
    """Get AgentCore memory record ID from local deployment file"""
    memory_record_file = f".memory-record-{stack_prefix}-{unique_id}.json"

    if os.path.exists(memory_record_file):
        try:
            with open(memory_record_file, "r") as f:
                memory_data = json.load(f)

            memory_record_id = memory_data.get("memory_record_id")
            if memory_record_id:
                print(f"    ✅ Loaded memory record ID: {memory_record_id}")
                return memory_record_id
            else:
                print(f"    ⚠️  No memory record ID found in {memory_record_file}")
        except Exception as e:
            print(
                f"    ❌ Error loading memory record ID from {memory_record_file}: {e}"
            )
    else:
        print(f"    ⚠️  Memory record file not found: {memory_record_file}")

    return None


def get_agentcore_runtime_arns(region, profile):
    """Get AgentCore runtime ARNs from AWS"""
    runtime_arns = {}

    try:
        import boto3

        session = boto3.Session(profile_name=profile) if profile else boto3.Session()

        # Try to get account ID for ARN construction
        try:
            account_id = session.client("sts").get_caller_identity()["Account"]
        except Exception as e:
            print(f"    ⚠️  Could not get account ID: {e}")
            account_id = "123456789012"  # Placeholder

        # Try the bedrock-agentcore-control client first
        try:
            agentcore_client = session.client(
                "bedrock-agentcore-control", region_name=region
            )
            print(f"    🔍 Looking up AgentCore runtime ARNs...")

            # List all agent runtimes
            response = agentcore_client.list_agent_runtimes(maxResults=100)
            runtimes = response.get("agentRuntimes", [])

            for runtime in runtimes:
                runtime_name = runtime.get("agentRuntimeName", "")
                runtime_id = runtime.get("agentRuntimeId", "")

                # Convert runtime name back to agent name (replace underscores with hyphens)
                agent_name = runtime_name.replace("_", "-")

                # Construct runtime ARN
                runtime_arn = f"arn:aws:bedrock-agentcore:{region}:{account_id}:agent-runtime/{runtime_id}"

                runtime_arns[agent_name] = {
                    "runtimeId": runtime_id,
                    "runtimeArn": runtime_arn,
                    "runtimeName": runtime_name,
                }

                print(f"    ✅ Found AgentCore runtime: {agent_name} -> {runtime_id}")

            print(f"    ✅ Retrieved {len(runtime_arns)} AgentCore runtime ARNs")

        except Exception as e:
            error_msg = str(e)
            if "Unknown service" in error_msg:
                print(
                    f"    ⚠️  bedrock-agentcore-control service not available in current boto3 version"
                )
                print(
                    f"    This is expected - AgentCore is a newer service that may not be in all SDK versions"
                )
            else:
                print(
                    f"    ⚠️  Could not retrieve AgentCore runtime ARNs via bedrock-agentcore-control: {e}"
                )

            # Fallback: Generate placeholder runtime information for discovered agents
            print(f"    🔄 Generating placeholder runtime information...")

            # Discover AgentCore agents from filesystem
            discovered_agents = []
            agentcore_agents_dir = Path("agentcore/agents")
            if agentcore_agents_dir.exists():
                for agent_dir in agentcore_agents_dir.iterdir():
                    if agent_dir.is_dir() and agent_dir.name != "__pycache__":
                        config_file = agent_dir / "config.json"
                        if config_file.exists():
                            discovered_agents.append(agent_dir.name)

            if not discovered_agents:
                print(f"    ⚠️  No AgentCore agents discovered from filesystem")
                return runtime_arns

            for agent_name in discovered_agents:
                runtime_name = agent_name.replace("-", "_")
                runtime_id = f"{runtime_name}_runtime_id"
                runtime_arn = f"arn:aws:bedrock-agentcore:{region}:{account_id}:agent-runtime/{runtime_id}"

                runtime_arns[agent_name] = {
                    "runtimeId": runtime_id,
                    "runtimeArn": runtime_arn,
                    "runtimeName": runtime_name,
                }

                print(
                    f"    ✅ Generated placeholder runtime: {agent_name} -> {runtime_id}"
                )

    except ImportError:
        print(f"    ⚠️  boto3 not available for AgentCore runtime lookup")
    except Exception as e:
        print(f"    ⚠️  Could not retrieve AgentCore runtime ARNs: {e}")

    return runtime_arns


def convert_agent_name_to_camel_case(agent_name):
    """Convert agent name to camelCase for agentType"""
    name = agent_name
    words = name.split()
    if not words:
        return agent_name.lower()

    # First word lowercase, subsequent words capitalized
    camel_case = words[0].lower()
    for word in words[1:]:
        camel_case += word.capitalize()

    return camel_case


def generate_color_from_name(agent_name):
    """Generate a consistent color based on agent name"""
    # Predefined color palette for consistency
    colors = [
        "#7A4B8C",
        "#47297B",
        "#8736AA",
        "#5D2689",
        "#6B3A9A",
        "#5A3B8E",
        "#6D4C92",
        "#7F5D96",
        "#6A4A94",
        "#8A5B9E",
        "#7B5A9C",
        "#8C6BA0",
        "#7C3AED",
        "#5D2689",
    ]

    # Generate hash from agent name for consistent color assignment
    hash_value = 0
    for char in agent_name:
        hash_value = ((hash_value << 5) - hash_value) + ord(char)
        hash_value = hash_value & 0xFFFFFFFF  # Keep it 32-bit

    return colors[abs(hash_value) % len(colors)]


def generate_icon_from_name(agent_name):
    """Generate an appropriate icon based on agent name keywords"""
    name_lower = agent_name.lower()

    # Icon mapping based on keywords in agent name
    if "analytics" in name_lower or "analysis" in name_lower:
        return "analytics"
    elif "coordinator" in name_lower or "orchestrat" in name_lower:
        return "hub"
    elif "campaign" in name_lower and "optim" in name_lower:
        return "tune"
    elif "audience" in name_lower or "strategy" in name_lower:
        return "groups"
    elif "bid" in name_lower or "optim" in name_lower:
        return "trending_up"
    elif "creative" in name_lower or "content" in name_lower:
        return "palette"
    elif "forecast" in name_lower or "predict" in name_lower:
        return "insights"
    elif "timing" in name_lower or "schedule" in name_lower:
        return "schedule"
    elif "revenue" in name_lower or "monetiz" in name_lower:
        return "monetization_on"
    elif "inventory" in name_lower:
        return "inventory"
    elif "channel" in name_lower or "media" in name_lower:
        return "tv"
    elif "context" in name_lower or "security" in name_lower:
        return "security"
    else:
        return "smart_toy"  # Default robot icon


def load_agentcore_config(agent_name):
    """Load AgentCore agent configuration"""
    config_path = Path(f"agentcore/deployment/agent/global_configuration.json")
    if config_path.exists():
        try:
            with open(config_path, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"    ⚠️  Error loading AgentCore config for {agent_name}: {e}")
    else:
        print(f"    ⚠️  AgentCore config not found: {config_path}")
    return None


def get_agent_ids_and_aliases(stack_prefix, stack_suffix, region, profile):
    """Get agent IDs and alias IDs from local files with AWS lookup fallback for missing aliases"""
    # ecosystem_config = load_ecosystem_config()
    # all_agents = (
    #     ecosystem_config["agent_hierarchy"]["supervisors"]
    #     + ecosystem_config["agent_hierarchy"]["collaborators"]
    # )

    agent_data = {}

    # First try to get unique ID
    unique_id = stack_suffix

    # Validate that we have a valid unique_id
    if not unique_id or unique_id.strip() == "":
        print(
            f"❌ Error: Could not determine unique ID for stack prefix '{stack_prefix}' in region '{region}'"
        )
        print(f"    Expected file: .unique-id-{stack_prefix}-{region}")
        print(
            f"    Or agent ID files matching pattern: .agent-ids-{stack_prefix}-*.json"
        )
        return {}

    if unique_id:
        print(
            f"🔍 Retrieving agent deployment information from local files (unique ID: {unique_id})..."
        )

        # Load agent and alias IDs directly from local deployment files
        print("    Loading agent and alias IDs from local deployment files...")

        agent_ids_file = f".agent-ids-{stack_prefix}-{unique_id}.json"
        alias_ids_file = f".agent-alias-ids-{stack_prefix}-{unique_id}.json"

        agent_ids = {}
        alias_ids = {}

        # Load agent IDs
        if os.path.exists(agent_ids_file):
            try:
                with open(agent_ids_file, "r") as f:
                    agent_ids = json.load(f)
                print(f"    ✅ Loaded agent IDs from {agent_ids_file}")
            except Exception as e:
                print(f"    ❌ Error loading agent IDs from {agent_ids_file}: {e}")
        else:
            print(f"    ⚠️  Agent IDs file not found: {agent_ids_file}")

        # Load alias IDs
        if os.path.exists(alias_ids_file):
            try:
                with open(alias_ids_file, "r") as f:
                    alias_ids = json.load(f)
                print(f"    ✅ Loaded alias IDs from {alias_ids_file}")
            except Exception as e:
                print(f"    ❌ Error loading alias IDs from {alias_ids_file}: {e}")
        else:
            print(f"    ⚠️  Alias IDs file not found: {alias_ids_file}")

        print(f"    ✅ Found {len(agent_ids)} agents from local files")
        print(f"    ✅ Found {len(alias_ids)} agent aliases from local files")

        # Get AgentCore runtime ARNs
        agentcore_runtime_arns = get_agentcore_runtime_arns(region, profile)

        # Process each Bedrock agent
        # for agent_name in all_agents:
        #     print(f"  Processing Bedrock agent: {agent_name}...")

        #     agent_id = agent_ids.get(agent_name)
        #     alias_id_full = alias_ids.get(agent_name)

        #     # Extract alias ID from the format "AGENT_ID|ALIAS_ID" if it's in that format
        #     alias_id = None
        #     if alias_id_full:
        #         if "|" in alias_id_full:
        #             alias_id = alias_id_full.split("|")[1]
        #         else:
        #             # If it's not in the format "AGENT_ID|ALIAS_ID", use it directly
        #             alias_id = alias_id_full

        #     if agent_id:
        #         print(f"    ✅ Agent ID: {agent_id}")
        #     else:
        #         print(f"    ⚠️  Agent ID not found for {agent_name}")

        #     # If alias ID not found in local file, look it up via AWS CLI
        #     if not alias_id and agent_id:
        #         print(
        #             f"    🔍 Alias ID not in local file, looking up 'latest' alias via AWS..."
        #         )
        #         alias_id = get_latest_agent_alias(agent_id, region, profile)
        #         if alias_id:
        #             print(f"    ✅ Found latest alias ID: {alias_id}")
        #         else:
        #             print(f"    ❌ No aliases found for agent {agent_name}")
        #     elif alias_id:
        #         print(f"    ✅ Alias ID: {alias_id}")
        #     else:
        #         print(f"    ⚠️  Alias ID not found for {agent_name}")

        #     agent_data[agent_name] = {
        #         "agent_id": agent_id,
        #         "alias_id": alias_id,
        #         "agent_stack": f"{stack_prefix}-{agent_name.lower()}",
        #         "alias_stack": f"{stack_prefix}-{agent_name.lower()}-alias",
        #         "agent_type": "bedrock",
        #     }

        # Process AgentCore agents
        agentcore_agents = get_agentcore_agents(stack_prefix, unique_id)
        agentcore_runtime_info = get_agentcore_runtime_info(stack_prefix, unique_id)

        for agent_name in agentcore_agents:
            print(f"  Processing AgentCore agent: {agent_name}...")

            # Get runtime information for this agent from local deployment file
            runtime_info = agentcore_runtime_info.get(agent_name, {})

            # AgentCore agents don't have traditional agent IDs or aliases
            # They are containerized services with different deployment patterns
            agent_data[agent_name] = {
                "agent_id": None,  # AgentCore agents don't have Bedrock agent IDs
                "alias_id": None,  # AgentCore agents don't have Bedrock aliases
                "agent_stack": f"{stack_prefix}-agentcore-{agent_name.lower()}",
                "alias_stack": None,  # AgentCore agents don't have alias stacks
                "agent_type": "agentcore",
                "runtime_id": runtime_info.get("runtime_id"),
                "runtime_arn": runtime_info.get("runtime_arn"),
                "container_uri": runtime_info.get("container_uri"),
                "deployment_time": runtime_info.get("deployment_time"),
            }

            if runtime_info.get("runtime_id"):
                print(
                    f"    ✅ AgentCore agent registered with runtime: {agent_name} -> {runtime_info.get('runtime_id')}"
                )
            else:
                print(
                    f"    ⚠️  AgentCore agent registered without runtime info: {agent_name}"
                )
    else:
        print("🔍 No local files found, attempting SDK lookup by agent name prefix...")
        # Fallback to SDK lookup if local files don't exist
        agent_data = get_agent_ids_via_sdk(stack_prefix, all_agents, region)

    return agent_data


def generate_aws_config(stack_prefix, stack_suffix, region, profile, output_file):
    """Generate the AWS config file"""
    infrastructure_config = get_infrastructure_config(
        stack_prefix, stack_suffix, region, profile
    )
    agent_data = get_agent_ids_and_aliases(stack_prefix, stack_suffix, region, profile)

    # Get unique ID for AgentCore lookup
    unique_id = get_unique_id(stack_prefix, region)
    agentcore_agents = (
        get_agentcore_agents(stack_prefix, unique_id) if unique_id else []
    )

    # Get memory record ID for AgentCore agents
    memory_record_id = (
        get_memory_record_id(stack_prefix, unique_id) if unique_id else None
    )

    all_agents = agentcore_agents

    # Build allAgents array
    all_agents_config = []
    skipped_agents = []

    # Process AgentCore agents
    for agent_name in agentcore_agents:
        agentcore_config = load_agentcore_config(agent_name)
        agent_info = agent_data.get(agent_name, {})

        # AgentCore agents are considered active if they're in the deployed list
        status = "active"

        agent_entry = {
            "name": agent_name,
            "agentType": agent_name,
            "displayName": agent_name,
            "status": status,
            "deploymentType": "agentcore",
            "aliasId": "",
        }

        # Add AgentCore runtime information
        if agent_info.get("runtime_id"):
            agent_entry["runtimeId"] = agent_info["runtime_id"]

        if agent_info.get("runtime_arn"):
            agent_entry["runtimeArn"] = agent_info["runtime_arn"]
            agent_entry["id"] = agent_info[
                "runtime_arn"
            ]  # Use runtime ARN as ID for AgentCore agents

        if agent_info.get("container_uri"):
            agent_entry["containerUri"] = agent_info["container_uri"]
            # For AgentCore agents, use runtime ARN as ID and leave aliasId empty
            agent_entry["id"] = agent_info["runtime_arn"]
            encoded_arn = (
                agent_info["runtime_arn"].replace(":", "%3A").replace("/", "%2F")
            )
            agent_entry["endpointUrl"] = (
                f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{encoded_arn}"
            )

        if agent_info.get("runtime_name"):
            agent_entry["runtimeName"] = agent_info["runtime_name"]

        # Add visual configuration from AgentCore config or use defaults
        if agentcore_config:
            if agentcore_config.get("color"):
                agent_entry["color"] = agentcore_config["color"]
            elif agentcore_config.get("agent_color"):
                agent_entry["color"] = agentcore_config["agent_color"]

            if agentcore_config.get("icon"):
                agent_entry["icon"] = agentcore_config["icon"]
            elif agentcore_config.get("agent_icon"):
                agent_entry["icon"] = agentcore_config["agent_icon"]

            # AgentCore agents don't have traditional Bedrock IDs
            # but we can include other identifying information
            if agentcore_config.get("service_name"):
                agent_entry["serviceName"] = agentcore_config["service_name"]

            # Add metadata from AgentCore config
            if agentcore_config.get("description") or agentcore_config.get(
                "agent_description"
            ):
                agent_entry["description"] = agentcore_config.get(
                    "description"
                ) or agentcore_config.get("agent_description")

            if agentcore_config.get("alternative_names"):
                agent_entry["alternativeNames"] = agentcore_config["alternative_names"]

            if agentcore_config.get("endpoint"):
                agent_entry["endpoint"] = agentcore_config["endpoint"]

        # Add default visual configuration if not present
        if "color" not in agent_entry:
            # Generate color based on agent name hash for consistency
            agent_entry["color"] = generate_color_from_name(agent_name)

        if "icon" not in agent_entry:
            # Generate icon based on agent name keywords
            agent_entry["icon"] = generate_icon_from_name(agent_name)

        all_agents_config.append(agent_entry)
        print(f"    ✅ Added AgentCore agent to config: {agent_name}")

        if not agentcore_config:
            print(
                f"    ⚠️  Warning: Could not load configuration for AgentCore agent {agent_name}, using defaults"
            )
            skipped_agents.append(agent_name)

    # Get AgentConfigTable name for agent management UI
    agent_config_table_name = get_agent_config_table_name(
        stack_prefix, unique_id or stack_suffix, region, profile
    )

    # Generate the complete AWS config in the requested structure
    aws_config = {
        "aws": {"region": region},
        "bedrock": {
            "allAgents": all_agents_config,
            "stackPrefix": stack_prefix,
            "stackSuffix": stack_suffix,
            "creativesDynamoDBTable": infrastructure_config.get(
                "creativesDynamoDBTable", ""
            ),
        },
        "ui": {
            "bucketName": infrastructure_config.get("uiBucketName", ""),
            "cloudFrontDistributionId": infrastructure_config.get(
                "cloudFrontDistributionId", ""
            ),
        },
        "stackPrefix": stack_prefix,
        "stackSuffix": stack_suffix,
        "uniqueId": unique_id or stack_suffix,
        "creativesBucket": f"{stack_prefix}-generated-content-{unique_id or stack_suffix}",
        "creativesDynamoDBTable": infrastructure_config.get(
            "creativesDynamoDBTable", ""
        ),
        "demoLogGroupName": infrastructure_config.get("demoLogGroupName", ""),
    }

    # Add agentConfigTable configuration for agent management UI (Requirements 12.1, 12.2, 12.3)
    if agent_config_table_name:
        aws_config["agentConfigTable"] = {
            "tableName": agent_config_table_name,
            "region": region,
        }
        print(f"    ✅ Added agentConfigTable to configuration: {agent_config_table_name}")

    # Add memory record ID if available (for AgentCore agents)
    if memory_record_id:
        aws_config["memoryRecordId"] = memory_record_id
        print(f"    ✅ Added memory record ID to configuration: {memory_record_id}")
    else:
        print(
            f"    ⚠️  No memory record ID available - AgentCore agents may not have memory functionality"
        )

    # Add Cognito configuration if available
    if "cognito" in infrastructure_config:
        aws_config["aws"]["cognito"] = infrastructure_config["cognito"]

    # Write to the output file
    os.makedirs(os.path.dirname(output_file), exist_ok=True)

    with open(output_file, "w") as f:
        json.dump(aws_config, f, indent=2)

    # Note: AgentCore agents are now included in the main allAgents array
    # No separate agentcore-agents.json file is generated

    return aws_config, skipped_agents


def main():
    """Main function"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate or validate AWS configuration for deployed agents"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Generate command
    generate_parser = subparsers.add_parser(
        "generate", help="Generate AWS config from deployed stacks"
    )
    generate_parser.add_argument(
        "--prefix", required=True, help="Stack prefix (e.g., 'sim', 'tst1')"
    )
    generate_parser.add_argument(
        "--suffix", required=True, help="Stack suffix/unique ID (e.g., 'ecosystem')"
    )
    generate_parser.add_argument(
        "--region", required=True, help="AWS region (e.g., 'us-east-1')"
    )
    generate_parser.add_argument(
        "--profile", default="default", help="AWS profile name (default: 'default')"
    )
    generate_parser.add_argument(
        "--output",
        default="bedrock-adtech-demo/src/assets/aws-config.json",
        help="Output file path (default: bedrock-adtech-demo/src/assets/aws-config.json)",
    )

    # Validate command
    validate_parser = subparsers.add_parser("validate", help="Validate all deployments")
    validate_parser.add_argument(
        "--prefix", required=True, help="Stack prefix (e.g., 'sim', 'tst1')"
    )
    validate_parser.add_argument(
        "--suffix", required=True, help="Stack suffix/unique ID (e.g., 'ecosystem')"
    )
    validate_parser.add_argument(
        "--region", required=True, help="AWS region (e.g., 'us-east-1')"
    )
    validate_parser.add_argument(
        "--profile", default="default", help="AWS profile name (default: 'default')"
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    try:
        if args.command == "generate":
            stack_prefix = args.prefix
            stack_suffix = args.suffix
            region = args.region
            profile = args.profile
            output_file = args.output

            # Check and upgrade AWS CLI if needed before any operations
            if not check_and_upgrade_aws_cli(profile):
                print("❌ AWS CLI is not functional and could not be upgraded")
                print("Please upgrade AWS CLI manually and try again")
                sys.exit(1)

            print(f"🚀 Generating AWS Config")
            print(f"   Stack Prefix: {stack_prefix}")
            print(f"   Stack Suffix: {stack_suffix}")
            print(f"   Region: {region}")
            print(f"   Profile: {profile}")
            print(f"   Output File: {output_file}")
            print()

            aws_config, skipped_agents = generate_aws_config(
                stack_prefix=stack_prefix,
                stack_suffix=stack_suffix,
                region=region,
                profile=profile,
                output_file=output_file,
            )

            print(f"\n✅ AWS config generated successfully!")
            print(f"   Location: {output_file}")
            agentcore_agents = [
                a
                for a in aws_config["bedrock"]["allAgents"]
                if a.get("deploymentType") == "agentcore"
            ]
            print(f"   AgentCore Agents: {len(agentcore_agents)}")
            # print(
            #     f"   Agents with IDs: {len([a for a in bedrock_agents if a.get('id')])}"
            # )
            # print(
            #     f"   Agents with Aliases: {len([a for a in bedrock_agents if a.get('aliasId')])}"
            # )

            # Show Cognito configuration status
            if "cognito" in aws_config.get("aws", {}):
                print(f"   ✅ Cognito configuration included")
            else:
                print(f"   ⚠️  Cognito configuration missing")

            # Show DynamoDB table status
            creatives_table = aws_config["bedrock"].get("creativesDynamoDBTable", "")
            if creatives_table:
                print(f"   ✅ Creatives DynamoDB table: {creatives_table}")
            else:
                print(f"   ⚠️  Creatives DynamoDB table missing")

            # Show UI configuration status
            ui_bucket = aws_config["ui"].get("bucketName", "")
            cloudfront_id = aws_config["ui"].get("cloudFrontDistributionId", "")
            if ui_bucket:
                print(f"   ✅ UI bucket: {ui_bucket}")
            else:
                print(f"   ⚠️  UI bucket missing")

            if cloudfront_id:
                print(f"   ✅ CloudFront distribution: {cloudfront_id}")
            else:
                print(f"   ⚠️  CloudFront distribution missing")

            # Show AgentCore agents summary
            if agentcore_agents:
                print(f"   ✅ AgentCore agents included:")
                for agent in agentcore_agents:
                    print(f"      - {agent['name']} ({agent['agentType']})")

            # Show memory record ID status
            if "memoryRecordId" in aws_config:
                print(f"   ✅ Memory record ID: {aws_config['memoryRecordId']}")
            else:
                print(f"   ⚠️  Memory record ID not available")

            # Show agentConfigTable status
            if "agentConfigTable" in aws_config:
                print(f"   ✅ Agent Config Table: {aws_config['agentConfigTable']['tableName']}")
            else:
                print(f"   ⚠️  Agent Config Table not available")

        elif args.command == "validate":
            stack_prefix = args.prefix
            stack_suffix = args.suffix
            region = args.region
            profile = args.profile

            # Check and upgrade AWS CLI if needed before any operations
            if not check_and_upgrade_aws_cli(profile):
                print("❌ AWS CLI is not functional and could not be upgraded")
                print("Please upgrade AWS CLI manually and try again")
                sys.exit(1)

            print(f"🔍 Validating Deployments")
            print(f"   Stack Prefix: {stack_prefix}")
            print(f"   Stack Suffix: {stack_suffix}")
            print(f"   Region: {region}")
            print(f"   Profile: {profile}")
            print()
        else:
            print(f"Unknown command: {args.command}")
            parser.print_help()
            sys.exit(1)

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
