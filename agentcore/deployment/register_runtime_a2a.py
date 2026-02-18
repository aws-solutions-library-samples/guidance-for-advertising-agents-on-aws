#!/usr/bin/env python3
"""
Register A2A Runtime in Registry

This script registers a runtime with its A2A auth configuration in the centralized registry.
Called after runtime deployment to make Cognito credentials available to all agents.
Bearer tokens are generated on demand at invocation time, not stored.
"""

import argparse
import sys
import os

# Add deployment directory to path
deployment_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, deployment_dir)

from runtime_registry import RuntimeRegistry


def main():
    parser = argparse.ArgumentParser(description="Register A2A runtime in registry")
    parser.add_argument("--stack-prefix", required=True, help="Stack prefix")
    parser.add_argument("--unique-id", required=True, help="Unique ID")
    parser.add_argument("--runtime-arn", required=True, help="Runtime ARN")
    parser.add_argument("--agent-name", required=True, help="Agent name")
    parser.add_argument("--pool-id", help="Cognito pool ID")
    parser.add_argument("--client-id", help="Cognito client ID")
    parser.add_argument("--discovery-url", help="OIDC discovery URL")

    args = parser.parse_args()

    # Get project root (two levels up from deployment directory)
    project_root = os.path.dirname(os.path.dirname(deployment_dir))

    # Create registry
    registry = RuntimeRegistry(args.stack_prefix, args.unique_id, project_root)

    # Register runtime
    info = registry.register_runtime(
        args.runtime_arn,
        args.agent_name,
        args.pool_id,
        args.client_id,
        args.discovery_url,
    )

    print(f"✅ Registered runtime in registry: {args.runtime_arn}")
    if args.pool_id and args.client_id:
        print(f"   Protocol: A2A")
        print(f"   Pool ID: {args.pool_id}")
        print(f"   Client ID: {args.client_id}")
    else:
        print(f"   Protocol: Standard (no A2A)")

    # Build and print updated RUNTIMES env value
    runtimes_env = registry.build_runtimes_env_value()
    print(f"\n📋 Updated RUNTIMES environment variable:")
    print(f"   {runtimes_env[:100]}... (truncated)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
