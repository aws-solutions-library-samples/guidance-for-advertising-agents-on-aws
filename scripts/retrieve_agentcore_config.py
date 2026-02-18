#!/usr/bin/env python3
"""
Retrieve AgentCore Runtime Configuration from SSM Parameter Store

This script retrieves runtime ARNs and bearer tokens stored in SSM
and can output them in various formats for use by the UI and other components.
"""

import argparse
import json
import sys
import boto3
from botocore.exceptions import ClientError


def retrieve_agentcore_config(stack_prefix, unique_id, region='us-east-1', profile=None):
    """
    Retrieve AgentCore configuration from SSM Parameter Store.
    
    Args:
        stack_prefix: Stack prefix used in deployment
        unique_id: Unique identifier used in deployment
        region: AWS region
        profile: AWS CLI profile name (optional)
    
    Returns:
        dict: Configuration data from SSM
    """
    # Create SSM client
    session_kwargs = {'region_name': region}
    if profile:
        session_kwargs['profile_name'] = profile
    
    session = boto3.Session(**session_kwargs)
    ssm = session.client('ssm')
    
    # Construct parameter name
    parameter_name = f'/{stack_prefix}/agentcore_values/{unique_id}'
    
    try:
        # Retrieve parameter with decryption
        response = ssm.get_parameter(
            Name=parameter_name,
            WithDecryption=True
        )
        
        # Parse JSON value
        config_json = response['Parameter']['Value']
        config = json.loads(config_json)
        
        return config
    
    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == 'ParameterNotFound':
            print(f"ERROR: Parameter not found: {parameter_name}", file=sys.stderr)
            print(f"Make sure AgentCore agents have been deployed and SSM storage completed", file=sys.stderr)
        else:
            print(f"ERROR: Failed to retrieve parameter: {e}", file=sys.stderr)
        return None
    
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON in SSM parameter: {e}", file=sys.stderr)
        return None


def format_as_env_vars(config):
    """
    Format configuration as environment variables.
    
    Args:
        config: Configuration dictionary
    
    Returns:
        str: Environment variable format
    """
    agents = config.get('agents', [])
    
    runtime_entries = []
    for agent in agents:
        runtime_arn = agent.get('runtime_arn', '')
        bearer_token = agent.get('bearer_token', '')
        
        if runtime_arn:
            if bearer_token:
                runtime_entries.append(f'{runtime_arn}|{bearer_token}')
            else:
                runtime_entries.append(f'{runtime_arn}|')
    
    runtimes = ','.join(runtime_entries)
    return f'export RUNTIMES="{runtimes}"'


def format_as_summary(config):
    """
    Format configuration as human-readable summary.
    
    Args:
        config: Configuration dictionary
    
    Returns:
        str: Human-readable summary
    """
    lines = []
    lines.append("\n📊 AgentCore Configuration Summary")
    lines.append(f"   Stack: {config.get('stack_prefix')}-{config.get('unique_id')}")
    lines.append(f"   Region: {config.get('region')}")
    lines.append("Deployed Agents:")
    
    agents = config.get('agents', [])
    for i, agent in enumerate(agents, 1):
        name = agent.get('name', 'Unknown')
        runtime_arn = agent.get('runtime_arn', 'N/A')
        protocol = agent.get('protocol', 'Standard')
        
        lines.append(f"\n   {i}. {name}")
        lines.append(f"      Protocol: {protocol}")
        lines.append(f"      Runtime ARN: {runtime_arn[:60]}...")
        
        if protocol == 'A2A':
            pool_id = agent.get('pool_id', 'N/A')
            client_id = agent.get('client_id', 'N/A')
            has_token = '✅ Present' if agent.get('bearer_token') else '❌ Missing'
            lines.append(f"      Pool ID: {pool_id}")
            lines.append(f"      Client ID: {client_id}")
            lines.append(f"      Bearer Token: {has_token}")
    
    lines.append(f"\n   Total Agents: {len(agents)}\n")
    
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(
        description='Retrieve AgentCore runtime configuration from SSM Parameter Store'
    )
    parser.add_argument(
        '--stack-prefix',
        required=True,
        help='Stack prefix used in deployment'
    )
    parser.add_argument(
        '--unique-id',
        required=True,
        help='Unique identifier used in deployment'
    )
    parser.add_argument(
        '--region',
        default='us-east-1',
        help='AWS region (default: us-east-1)'
    )
    parser.add_argument(
        '--profile',
        help='AWS CLI profile name (optional)'
    )
    parser.add_argument(
        '--format',
        choices=['json', 'env', 'summary'],
        default='json',
        help='Output format (default: json)'
    )
    parser.add_argument(
        '--output',
        help='Output file path (optional, defaults to stdout)'
    )
    
    args = parser.parse_args()
    
    # Retrieve configuration
    config = retrieve_agentcore_config(
        args.stack_prefix,
        args.unique_id,
        args.region,
        args.profile
    )
    
    if not config:
        sys.exit(1)
    
    # Format output
    if args.format == 'json':
        output = json.dumps(config, indent=2)
    elif args.format == 'env':
        output = format_as_env_vars(config)
    elif args.format == 'summary':
        output = format_as_summary(config)
    else:
        print(f"ERROR: Unknown format: {args.format}", file=sys.stderr)
        sys.exit(1)
    
    # Write output
    if args.output:
        with open(args.output, 'w') as f:
            f.write(output)
            f.write('\n')
        print(f"Configuration written to: {args.output}", file=sys.stderr)
    else:
        print(output)
    
    sys.exit(0)


if __name__ == '__main__':
    main()
