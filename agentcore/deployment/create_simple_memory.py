#!/usr/bin/env python3
"""
Simple memory creation script for AgentCore ecosystem deployment.
Creates ONE memory record that all AgentCore agents can share.
Stores the memory ID in SSM Parameter Store for retrieval by agents.
"""

import sys
import argparse
import boto3
from botocore.exceptions import ClientError


def store_memory_id_in_ssm(stack_prefix, unique_id, memory_id, aws_region="us-east-1"):
    """
    Store the memory ID in SSM Parameter Store for retrieval by agents.
    
    Args:
        stack_prefix: Stack prefix for resource naming
        unique_id: Unique identifier for resource naming
        memory_id: The AgentCore memory ID to store
        aws_region: AWS region
        
    Returns:
        bool: True if successful, False otherwise
    """
    parameter_name = f"/{stack_prefix}/{unique_id}/agentcore_memory_id"
    
    try:
        ssm_client = boto3.client("ssm", region_name=aws_region)
        
        ssm_client.put_parameter(
            Name=parameter_name,
            Value=memory_id,
            Type="String",
            Overwrite=True,
            Description=f"AgentCore shared memory ID for {stack_prefix}-{unique_id}"
        )
        
        print(f"✅ Memory ID stored in SSM: {parameter_name}")
        return True
        
    except ClientError as e:
        print(f"❌ Failed to store memory ID in SSM: {e}")
        return False
    except Exception as e:
        print(f"❌ Unexpected error storing memory ID in SSM: {e}")
        return False


def create_shared_memory(stack_prefix, unique_id, aws_region="us-east-1"):
    """Create a single shared memory record for all AgentCore agents."""

    memory_id = f"{stack_prefix}memory{unique_id}"

    print(f"Creating shared memory: {memory_id}")
    print(f"Region: {aws_region}")

    try:
        # Import AgentCore memory client
        from bedrock_agentcore.memory import MemoryClient

        memory_client = MemoryClient()
        print("✅ AgentCore memory client initialized")
    except ImportError as e:
        print(f"❌ AgentCore memory client not available: {e}")
        print("This is expected if bedrock-agentcore package is not installed")
        print("To install: pip install bedrock-agentcore")
        return None
    except Exception as e:
        error_msg = str(e)
        print(f"❌ Failed to initialize memory client: {error_msg}")
        
        # Check if it's the unknown service error
        if "Unknown service" in error_msg or "bedrock-agentcore-control" in error_msg:
            print("⚠️  The bedrock-agentcore-control service is not available in your boto3/botocore version")
            print("Attempting to upgrade boto3 and botocore automatically...")
            
            # Try to upgrade boto3/botocore
            import subprocess
            import sys
            
            try:
                # Security: Validate sys.executable before using in subprocess
                if not sys.executable or not __import__('os').path.isfile(sys.executable):
                    raise ValueError("Invalid Python executable path")
                
                # Security: Use only hardcoded, validated package names
                upgrade_cmd = [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--upgrade",
                    "--quiet",
                    "boto3",
                    "botocore"
                ]
                
                # nosemgrep: dangerous-subprocess-use-audit - Hardcoded pip upgrade
                result = subprocess.run(
                    upgrade_cmd,
                    capture_output=True,
                    text=True,
                    timeout=120
                )
                
                if result.returncode == 0:
                    print("✅ Successfully upgraded boto3 and botocore")
                    print("Retrying memory client initialization...")
                    
                    # Reload the module after upgrade
                    import importlib
                    import boto3
                    importlib.reload(boto3)
                    
                    # Try again
                    from bedrock_agentcore.memory import MemoryClient
                    memory_client = MemoryClient()
                    print("✅ AgentCore memory client initialized after upgrade")
                else:
                    print(f"❌ Failed to upgrade boto3: {result.stderr}")
                    print("Please upgrade manually: pip install --upgrade boto3 botocore")
                    return None
                    
            except Exception as upgrade_error:
                print(f"❌ Error during upgrade: {upgrade_error}")
                print("Please upgrade manually: pip install --upgrade boto3 botocore")
                return None
        else:
            return None

    try:
        # Create the memory store with simple configuration
        memory_config = {
            "name": memory_id,
            "strategies": [
                {
                    "summaryMemoryStrategy": {
                        "name": "SessionSummarizer",
                        "namespaces": ["/summaries/{sessionId}/{actorId}"],
                    },
                },
                {
                    "semanticMemoryStrategy": {
                        "name": "SessionFacts",
                        "namespaces": ["/facts/{sessionId}/{actorId}"],
                    }
                }
                
            ],
        }

        print(f"Creating memory with config: {memory_config}")
        memories = memory_client.list_memories()
        real_memory_id = ""
        for memory in memories:
            if memory_id in memory.get("id"):
                real_memory_id = memory.get("id")
                print(f"Found existing memory: {real_memory_id}")
                return real_memory_id
        # Use create_memory_and_wait to create the memory
        if real_memory_id == "":
            created_memory = memory_client.create_memory_and_wait(**memory_config)

            print(f"✅ Memory created successfully!")
            print(f"   Memory ID: {created_memory.get('id')}")
            print(f"   Status: {created_memory.get('status')}")

            return created_memory.get("id")

    except Exception as e:
        error_str = str(e).lower()
        if "already exists" in error_str or "conflict" in error_str:
            print(f"✅ Memory already exists: {memory_id}")
            # Try to find the existing memory ID
            try:
                memories = memory_client.list_memories()
                for memory in memories:
                    if memory_id in memory.get("id"):
                        existing_id = memory.get("id")
                        print(f"Found existing memory ID: {existing_id}")
                        return existing_id
            except Exception as list_error:
                # If listing fails, log and continue with fallback
                print(f"Warning: Could not list memories to find existing ID: {list_error}")
            return memory_id  # Fallback to constructed ID
        else:
            print(f"❌ Failed to create memory: {e}")
            return None


def main():
    parser = argparse.ArgumentParser(
        description="Create shared memory for AgentCore agents"
    )
    parser.add_argument("--stack-prefix", required=True, help="Stack prefix")
    parser.add_argument("--unique-id", required=True, help="Unique ID")
    parser.add_argument("--aws-region", default="us-east-1", help="AWS region")
    parser.add_argument("--output-file", help="File to save memory record ID")

    args = parser.parse_args()

    memory_record_id = create_shared_memory(
        args.stack_prefix, args.unique_id, args.aws_region
    )

    if memory_record_id:
        print(f"✅ Memory record ID: {memory_record_id}")
        
        # Store memory ID in SSM Parameter Store for agent retrieval
        ssm_success = store_memory_id_in_ssm(
            args.stack_prefix, args.unique_id, memory_record_id, args.aws_region
        )
        
        if not ssm_success:
            print("⚠️  Memory created but SSM storage failed - agents may need MEMORY_ID env var")

        # Save memory record ID to file if specified
        if args.output_file:
            try:
                import json
                import os

                # Create directory if it doesn't exist
                output_dir = os.path.dirname(args.output_file)
                if output_dir:
                    os.makedirs(output_dir, exist_ok=True)

                memory_info = {
                    "memory_record_id": memory_record_id,
                    "stack_prefix": args.stack_prefix,
                    "unique_id": args.unique_id,
                    "aws_region": args.aws_region,
                    "ssm_parameter": f"/{args.stack_prefix}/agentcore_memory_id/{args.unique_id}",
                    "created_at": __import__("datetime").datetime.utcnow().isoformat()
                    + "Z",
                }

                with open(args.output_file, "w") as f:
                    json.dump(memory_info, f, indent=2)

                print(f"✅ Memory record ID saved to: {args.output_file}")
            except Exception as e:
                print(f"⚠️  Could not save memory record ID to file: {e}")

        sys.exit(0)
    else:
        print("❌ Failed to create or find memory record")
        sys.exit(1)


if __name__ == "__main__":
    main()
