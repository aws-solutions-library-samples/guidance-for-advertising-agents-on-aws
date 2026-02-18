#!/usr/bin/env python3
"""
Cleanup script for AgentCore runtimes
Can delete specific runtimes that are causing conflicts
"""

import boto3
import sys
import argparse
import time


def cleanup_runtime():
    """Delete a specific AgentCore runtime"""

    parser = argparse.ArgumentParser(description="Delete AgentCore runtime")
    parser.add_argument("--profile", help="AWS profile name")
    parser.add_argument("--region", default="us-east-1", help="AWS region")
    parser.add_argument(
        "--agentcore-region", help="AgentCore region (defaults to --region)"
    )
    parser.add_argument("--runtime-id", required=True, help="Runtime ID to delete")
    parser.add_argument("--runtime-name", help="Runtime name (for verification)")
    parser.add_argument("--force", action="store_true", help="Skip confirmation prompt")

    args = parser.parse_args()

    region = args.region
    agentcore_region = args.agentcore_region or region

    # Handle EU regions
    if region.startswith("eu-") and not args.agentcore_region:
        agentcore_region = "eu-central-1"
        print(
            f"EU region detected ({region}), using AgentCore region: {agentcore_region}"
        )

    print(f"🗑️  AgentCore Runtime Cleanup")
    print(f"   AWS Profile: {args.profile or 'default'}")
    print(f"   AgentCore Region: {agentcore_region}")
    print(f"   Runtime ID: {args.runtime_id}")
    if args.runtime_name:
        print(f"   Runtime Name: {args.runtime_name}")
    print("=" * 50)

    try:
        # Create session and client
        if args.profile:
            session = boto3.Session(profile_name=args.profile)
        else:
            session = boto3.Session()

        agentcore_client = session.client(
            "bedrock-agentcore-control", region_name=agentcore_region
        )

        # Get runtime details for verification
        print(f"📋 Getting runtime details...")
        try:
            runtime_details = agentcore_client.get_agent_runtime(
                agentRuntimeId=args.runtime_id
            )
            actual_name = runtime_details.get("agentRuntimeName", "Unknown")
            status = runtime_details.get("status", "Unknown")

            print(f"   Runtime Name: {actual_name}")
            print(f"   Status: {status}")

            # Verify name if provided
            if args.runtime_name and actual_name != args.runtime_name:
                print(
                    f"⚠️  Warning: Expected name '{args.runtime_name}' but found '{actual_name}'"
                )
                if not args.force:
                    response = input("Continue anyway? (y/N): ").strip().lower()
                    if response not in ["y", "yes"]:
                        print("❌ Aborted by user")
                        return False

        except Exception as e:
            print(f"❌ Could not get runtime details: {e}")
            if not args.force:
                response = (
                    input("Continue with deletion anyway? (y/N): ").strip().lower()
                )
                if response not in ["y", "yes"]:
                    print("❌ Aborted by user")
                    return False

        # Confirmation prompt
        if not args.force:
            print(f"\n⚠️  You are about to DELETE AgentCore runtime:")
            print(f"   Runtime ID: {args.runtime_id}")
            print(f"   This action cannot be undone!")
            response = (
                input("\nAre you sure you want to delete this runtime? (y/N): ")
                .strip()
                .lower()
            )
            if response not in ["y", "yes"]:
                print("❌ Deletion cancelled by user")
                return False

        # Delete the runtime
        print(f"🗑️  Deleting runtime {args.runtime_id}...")
        agentcore_client.delete_agent_runtime(agentRuntimeId=args.runtime_id)

        print(f"✅ Runtime deletion initiated successfully")
        print(f"   Runtime ID: {args.runtime_id}")
        print(f"   Note: It may take a few minutes for the deletion to complete")

        # Wait a moment and check status
        print(f"⏳ Waiting 10 seconds to check deletion status...")
        # nosemgrep: arbitrary-sleep - Required for deletion status check
        time.sleep(10)

        try:
            runtime_details = agentcore_client.get_agent_runtime(
                agentRuntimeId=args.runtime_id
            )
            status = runtime_details.get("status", "Unknown")
            print(f"   Current status: {status}")
            if status in ["DELETING", "DELETE_IN_PROGRESS"]:
                print(f"✅ Runtime is being deleted")
            else:
                print(f"⚠️  Runtime status: {status}")
        except Exception as e:
            if "ResourceNotFoundException" in str(e) or "not found" in str(e).lower():
                print(f"✅ Runtime has been deleted successfully")
            else:
                print(f"⚠️  Could not check deletion status: {e}")

        return True

    except Exception as e:
        print(f"❌ Error during runtime cleanup: {e}")
        return False


if __name__ == "__main__":
    success = cleanup_runtime()
    sys.exit(0 if success else 1)
