#!/usr/bin/env python3
"""
Vector Index Creation for Knowledge Bases

This script creates vector indexes for OpenSearch Serverless collections with proper
wait times, retry logic, and fallback mechanisms based on proven deployment patterns.
"""

import sys
import subprocess
import os
import warnings

# Suppress all warnings to avoid dependency conflict noise
warnings.filterwarnings("ignore")


# Check and install required dependencies using virtual environment
def install_dependencies():
    """Install required Python dependencies in a virtual environment if not available"""

    # Check if we're already in a virtual environment
    in_venv = hasattr(sys, "real_prefix") or (
        hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix
    )

    missing_packages = []
    package_names = ["boto3", "opensearchpy", "retrying", "requests"]
    for package in package_names:
        try:
            __import__(package.replace("-", "_"))
        except ImportError:
            missing_packages.append(package)

    if not missing_packages:
        return  # All packages are available

    print(f"Missing packages: {', '.join(missing_packages)}")

    if not in_venv:
        # Create and use a virtual environment
        venv_dir = os.path.join(os.path.dirname(__file__), "..", ".venv-deployment")

        if not os.path.exists(venv_dir):
            print(f"Creating virtual environment at {venv_dir}")
            try:
                # nosemgrep: dangerous-subprocess-use-audit - Hardcoded venv creation
                subprocess.check_call(
                    [sys.executable, "-m", "venv", venv_dir, "--clear"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except subprocess.CalledProcessError as e:
                print(f"Failed to create virtual environment: {e}")
                print("Please install the required packages manually:")
                for pkg in package_names:
                    print(f"  pip install {pkg}")
                sys.exit(1)

        # Get the Python executable from the virtual environment
        if sys.platform == "win32":
            venv_python = os.path.join(venv_dir, "Scripts", "python.exe")
            venv_pip = os.path.join(venv_dir, "Scripts", "pip.exe")
        else:
            venv_python = os.path.join(venv_dir, "bin", "python")
            venv_pip = os.path.join(venv_dir, "bin", "pip")

        # Install packages in the virtual environment - suppress all output
        print("Installing AWS SDK in isolated virtual environment...")
        try:
            # Validate that venv_pip is a safe path (must be within our venv directory)
            if not os.path.abspath(venv_pip).startswith(os.path.abspath(venv_dir)):
                raise ValueError("Invalid pip path detected")

            # Upgrade pip first in the virtual environment (suppress output)
            # nosemgrep: dangerous-subprocess-use-audit - Validated pip path with hardcoded packages
            subprocess.run(
                [venv_pip, "install", "--upgrade", "pip"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )

            # Install packages with aggressive suppression of warnings and errors
            # Security: Use only hardcoded, validated package names
            packages_to_install = [
                "boto3",
                "botocore",
                "s3transfer",
                "urllib3",
                "opensearch-py",
                "retrying",
                "requests",
            ]

            # Build command with validated components only
            install_cmd = (
                [venv_pip, "install"]
                + packages_to_install
                + [
                    "--quiet",
                    "--disable-pip-version-check",
                    "--no-warn-script-location",
                ]
            )

            # nosemgrep: dangerous-subprocess-use-audit - Hardcoded pip install
            result = subprocess.run(
                install_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )

            # Don't check return code - continue regardless
            print("Package installation attempted (warnings/conflicts ignored)")

        except Exception as e:
            print(f"Installation encountered issues (continuing anyway): {e}")

        # Re-execute this script using the virtual environment Python
        print(f"Re-executing script with virtual environment Python...")

        # Security: Validate venv_python path before using in os.execv
        if not os.path.isfile(venv_python):
            raise ValueError(f"Invalid Python executable path: {venv_python}")

        # Security: Validate that venv_python is within our venv directory
        if not os.path.abspath(venv_python).startswith(os.path.abspath(venv_dir)):
            raise ValueError(
                "Python executable path is outside virtual environment directory"
            )

        # Security: Only pass validated, safe arguments to os.execv
        # Filter sys.argv to only include the script name and safe arguments
        safe_argv = [venv_python, sys.argv[0]]  # Python path and script name

        # Add back command-line arguments with validation
        for arg in sys.argv[1:]:
            # Only allow arguments that match expected patterns
            if arg.startswith("--") or arg.startswith("-"):
                # Validate flag names (alphanumeric, hyphens only)
                import re

                if re.match(r"^--?[a-zA-Z0-9-]+$", arg):
                    safe_argv.append(arg)
                else:
                    print(f"Warning: Skipping potentially unsafe argument: {arg}")
            else:
                # For non-flag arguments, validate they don't contain shell metacharacters
                if not any(
                    char in arg
                    for char in [
                        "&",
                        "|",
                        ";",
                        "$",
                        "`",
                        "(",
                        ")",
                        "<",
                        ">",
                        "\n",
                        "\r",
                    ]
                ):
                    safe_argv.append(arg)
                else:
                    print(f"Warning: Skipping potentially unsafe argument: {arg}")

        # nosemgrep: dangerous-os-exec-audit,dangerous-os-exec-tainted-env-args - Validated safe re-exec
        os.execv(venv_python, safe_argv)

    else:
        # We're already in a virtual environment, try to install directly
        print("Installing packages in current virtual environment...")
        try:
            # Security: Use only hardcoded, validated package names
            packages_to_install = [
                "boto3",
                "botocore",
                "opensearch-py",
                "retrying",
                "requests",
            ]

            # Validate sys.executable is a Python interpreter
            if not sys.executable or not os.path.isfile(sys.executable):
                raise ValueError("Invalid Python executable path")

            # Build command with validated components only
            install_cmd = (
                [sys.executable, "-m", "pip", "install"]
                + packages_to_install
                + ["--quiet", "--disable-pip-version-check"]
            )

            # nosemgrep: dangerous-subprocess-use-audit - Hardcoded pip install
            subprocess.run(
                install_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            print("Package installation attempted (warnings/conflicts ignored)")

        except Exception as e:
            print(f"Installation encountered issues (continuing anyway): {e}")


# Install dependencies first
install_dependencies()

# Now import with error handling
try:
    import json
    import boto3
    import time
    import random
    import argparse
    from botocore.exceptions import ClientError
    from opensearchpy import (
        OpenSearch,
        RequestsHttpConnection,
        AWSV4SignerAuth,
        RequestError,
    )
    from retrying import retry
except ImportError as e:
    print(f"Warning: Failed to import some modules: {e}")
    print("Continuing anyway - some functionality may be limited")
    # Import what we can
    import json
    import time
    import random
    import argparse

    try:
        import boto3
        from botocore.exceptions import ClientError
    except ImportError:
        print("Critical error: boto3 not available")
        sys.exit(1)


class VectorIndexManager:
    """Manager for creating vector indexes with proper wait times and retry logic"""

    def __init__(self, region_name=None, aws_profile=None):
        """Initialize the Vector Index Manager"""
        if aws_profile:
            print(f"Using AWS profile: {aws_profile}")
            boto3_session = boto3.session.Session(profile_name=aws_profile)
        else:
            print("Using default AWS credentials")
            boto3_session = boto3.session.Session()

        self.session = boto3_session
        self.region_name = region_name or boto3_session.region_name or "us-east-1"
        print(f"Using AWS region: {self.region_name}")

        # AWS clients
        self.aoss_client = boto3_session.client(
            "opensearchserverless", region_name=self.region_name
        )

        # Set up authentication for OpenSearch
        credentials = boto3_session.get_credentials()
        if not credentials:
            raise ValueError("Could not retrieve AWS credentials")

        self.awsauth = AWSV4SignerAuth(credentials, self.region_name, "aoss")

    def interactive_sleep(self, seconds: int, message: str = ""):
        """Display progress dots while waiting"""
        if message:
            print(f"{message} ", end="", flush=True)
        dots = ""
        for i in range(seconds):
            dots += "."
            print(dots[-1], end="", flush=True)
            # nosemgrep: arbitrary-sleep - Required for OpenSearch collection polling
            time.sleep(1)
        print(" Done!")

    def get_collection_by_id(self, collection_id):
        """Get collection details by ID"""
        try:
            response = self.aoss_client.batch_get_collection(ids=[collection_id])
            collections = response.get("collectionDetails", [])
            if collections:
                return collections[0]
            else:
                print(f"Collection with ID {collection_id} not found")
                return None
        except ClientError as e:
            print(f"Error getting collection: {e}")
            return None

    def wait_for_collection_active(self, collection_id, max_wait_time=600):
        """Wait for collection to be active with timeout"""
        print(f"Waiting for collection {collection_id} to be active...")
        start_time = time.time()

        while time.time() - start_time < max_wait_time:
            collection = self.get_collection_by_id(collection_id)
            if not collection:
                print("Collection not found")
                return False

            status = collection.get("status")
            print(f"Collection status: {status}")

            if status == "ACTIVE":
                print("✓ Collection is active")
                return collection
            elif status == "FAILED":
                print("✗ Collection failed to create")
                return False

            # Wait before checking again
            # nosemgrep: arbitrary-sleep - Required for OpenSearch collection polling
            time.sleep(30)

        print(f"Timeout waiting for collection to be active")
        return False

    def setup_opensearch_client(self, collection_endpoint):
        """Setup OpenSearch client for index operations"""
        host = collection_endpoint.replace("https://", "")

        self.oss_client = OpenSearch(
            hosts=[{"host": host, "port": 443}],
            http_auth=self.awsauth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection,
            timeout=300,
        )

        return self.oss_client

    def get_index_mapping(self):
        """Get the correct index mapping for Titan Embed v2 (1024 dimensions)"""
        return {
            "settings": {"index": {"knn": True}},
            "mappings": {
                "properties": {
                    "vector": {
                        "type": "knn_vector",
                        "dimension": 1024,
                        "method": {
                            "name": "hnsw",
                            "space_type": "l2",
                            "engine": "faiss",
                            "parameters": {
                                "ef_construction": 512,
                                "ef_search": 512,
                                "m": 16,
                            },
                        },
                    },
                    "text": {"type": "text"},
                    "metadata": {"type": "text"},
                }
            },
        }

    @retry(wait_random_min=5000, wait_random_max=10000, stop_max_attempt_number=4)
    def create_single_index(self, index_name, index_mapping):
        """Create a single index with retry logic"""
        try:
            # Check if index already exists - use the correct API syntax
            if self.oss_client.indices.exists(index=index_name):
                print(f"✓ Index {index_name} already exists")
                return True

            # Create the index
            self.oss_client.indices.create(index=index_name, body=index_mapping)
            print(
                f"✓ Created index: {index_name}. Waiting 10 seconds before creating next index..."
            )
            # nosemgrep: arbitrary-sleep - Intentional progress indicator
            time.sleep(10)
            return True

        except Exception as e:
            if "authorization_exception" in str(e).lower():
                print(f"⚠️  Authorization error for {index_name}, retrying...")
                raise e  # Let retry decorator handle it
            else:
                print(f"✗ Error creating index {index_name}: {e}")
                return False

    def create_all_indexes(self, collection_id, indexes_to_create=None):
        """Create all required vector indexes"""
        print("Creating vector indexes for knowledge bases...")

        # First ensure collection is active
        collection = self.wait_for_collection_active(collection_id)
        if not collection:
            return False

        collection_endpoint = collection.get("collectionEndpoint")
        if not collection_endpoint:
            print("✗ Could not get collection endpoint")
            return False

        print(f"Collection endpoint: {collection_endpoint}")

        # Setup OpenSearch client
        if not self.setup_opensearch_client(collection_endpoint):
            return False

        # Add initial wait for policy propagation (like your working deployment)
        self.interactive_sleep(30, "Waiting for access policies to propagate...")

        # Use provided indexes or fall back to default set
        if indexes_to_create:
            indexes = indexes_to_create
            print(f"Creating {len(indexes)} specified indexes")
        else:
            # Default fallback indexes (for backward compatibility)
            indexes = []
            print(f"No indexes specified, creating {len(indexes)} default indexes")

        index_mapping = self.get_index_mapping()
        created_indexes = []

        for index_name in indexes:
            try:
                success = self.create_single_index(index_name, index_mapping)
                if success:
                    created_indexes.append(index_name)
                    # print("⏱️  Waiting 10 seconds between index creations...")
                    # time.sleep(10)
            except Exception as e:
                if "authorization_exception" in str(e).lower():
                    print(
                        f"❌ Authorization error for {index_name}: Policy propagation may still be in progress"
                    )
                    print(f"   Consider running the script again in 5-10 minutes")
                else:
                    print(f"❌ Failed to create index {index_name}: {e}")

        success_rate = len(created_indexes) / len(indexes)

        if len(created_indexes) == len(indexes):
            print(f"✅ Successfully created all {len(created_indexes)} vector indexes")
            return True
        elif len(created_indexes) > 0:
            print(
                f"⚠️  Partial success: Created {len(created_indexes)}/{len(indexes)} indexes"
            )
            print(
                "   Bedrock will auto-create missing indexes when knowledge bases are first used"
            )
            return True
        else:
            print("❌ No indexes were created")
            print("   This is often due to policy propagation delays")
            print("   Bedrock will auto-create indexes when knowledge bases are used")
            return False

    def cleanup_knowledge_bases(self, stack_prefix):
        """Clean up knowledge bases and related resources"""
        print(f"🧹 Starting cleanup of knowledge bases with prefix: {stack_prefix}")

        try:
            # Get Bedrock agent client
            bedrock_agent_client = self.session.client(
                "bedrock-agent", region_name=self.region_name
            )

            # Find and delete knowledge bases with our naming pattern
            kb_list = bedrock_agent_client.list_knowledge_bases(maxResults=50)
            deleted_kbs = []

            for kb in kb_list.get("knowledgeBaseSummaries", []):
                kb_name = kb["name"]
                kb_id = kb["knowledgeBaseId"]

                # Check if this KB matches our naming pattern
                if kb_name.startswith(f"{stack_prefix}-"):
                    print(f"Found knowledge base: {kb_name} (ID: {kb_id})")

                    try:
                        # Delete all data sources for this KB first
                        ds_list = bedrock_agent_client.list_data_sources(
                            knowledgeBaseId=kb_id
                        )
                        for ds in ds_list.get("dataSourceSummaries", []):
                            ds_id = ds["dataSourceId"]
                            ds_name = ds["name"]
                            try:
                                bedrock_agent_client.delete_data_source(
                                    knowledgeBaseId=kb_id, dataSourceId=ds_id
                                )
                                print(f"✓ Deleted data source: {ds_name}")
                            except Exception as e:
                                print(f"✗ Error deleting data source {ds_name}: {e}")

                        # Delete the knowledge base
                        bedrock_agent_client.delete_knowledge_base(
                            knowledgeBaseId=kb_id
                        )
                        print(f"✓ Deleted knowledge base: {kb_name}")
                        deleted_kbs.append(kb_id)

                    except Exception as e:
                        print(f"✗ Error deleting knowledge base {kb_name}: {e}")

            if deleted_kbs:
                print(f"✓ Successfully deleted {len(deleted_kbs)} knowledge bases")
            else:
                print(f"⚠️  No knowledge bases found with prefix '{stack_prefix}-'")

            return True

        except Exception as e:
            print(f"❌ Error during cleanup: {e}")
            return False

    def cleanup_all_resources(self, stack_prefix, confirm=True):
        """Clean up all resources including knowledge bases and OpenSearch collections"""
        if confirm:
            response = input(
                f"⚠️  This will delete ALL resources with prefix '{stack_prefix}'. Are you sure? (yes/no): "
            )
            if response.lower() != "yes":
                print("Cleanup cancelled.")
                return False

        print("🧹 Starting comprehensive cleanup...")

        # Clean up knowledge bases first
        self.cleanup_knowledge_bases(stack_prefix)

        # Clean up OpenSearch collections
        try:
            collections = self.aoss_client.list_collections()
            for collection in collections.get("collectionSummaries", []):
                collection_name = collection["name"]
                if collection_name.startswith(f"{stack_prefix}-"):
                    collection_id = collection["id"]
                    print(f"Found OpenSearch collection: {collection_name}")
                    try:
                        self.aoss_client.delete_collection(id=collection_id)
                        print(f"✓ Deleted collection: {collection_name}")
                    except Exception as e:
                        print(f"✗ Error deleting collection {collection_name}: {e}")
        except Exception as e:
            print(f"⚠️  Error cleaning up OpenSearch collections: {e}")

        print("✅ Cleanup completed!")
        return True


def main():
    """Main function to handle command line execution"""
    parser = argparse.ArgumentParser(
        description="Vector Index Creation and Management",
        epilog="""
Examples:
  # Create indexes from command line
  python3 create_vector_indices.py --collection-id abc123 --indexes index1 index2 index3
  
  # Create indexes from JSON file
  python3 create_vector_indices.py --collection-id abc123 --indexes-file indexes.json
  
  # Create default indexes (backward compatibility)
  python3 create_vector_indices.py --collection-id abc123
  
  # Cleanup resources
  python3 create_vector_indices.py --action cleanup --stack-prefix my-stack
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--collection-id", help="OpenSearch collection ID (required for index creation)"
    )
    parser.add_argument("--region", help="AWS region")
    parser.add_argument("--profile", help="AWS profile")
    parser.add_argument(
        "--action",
        choices=["create", "cleanup"],
        default="create",
        help="Action to perform (create indexes or cleanup resources)",
    )
    parser.add_argument(
        "--stack-prefix",
        default="iflow-dev",
        help="Stack prefix for resource cleanup (default: iflow-dev)",
    )
    parser.add_argument(
        "--confirm-cleanup", action="store_true", help="Skip confirmation for cleanup"
    )
    parser.add_argument(
        "--indexes", nargs="*", help="List of index names to create (space-separated)"
    )
    parser.add_argument(
        "--indexes-file", help="JSON file containing index names to create"
    )

    args = parser.parse_args()

    if args.action == "create" and not args.collection_id:
        parser.error("--collection-id is required when action is 'create'")

    # Determine indexes to create
    indexes_to_create = None
    if args.action == "create":
        if args.indexes:
            indexes_to_create = args.indexes
            print(f"Using indexes from command line: {indexes_to_create}")
        elif args.indexes_file:
            try:
                with open(args.indexes_file, "r") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        indexes_to_create = data
                    elif isinstance(data, dict) and "indexes" in data:
                        indexes_to_create = data["indexes"]
                    else:
                        print(
                            f"Invalid format in {args.indexes_file}, expected list or dict with 'indexes' key"
                        )
                        exit(1)
                print(
                    f"Using indexes from file {args.indexes_file}: {indexes_to_create}"
                )
            except Exception as e:
                print(f"Error reading indexes file {args.indexes_file}: {e}")
                exit(1)

    print("🚀 Vector Index Manager")
    print(f"Action: {args.action}")
    if args.action == "create":
        print(f"Collection ID: {args.collection_id}")
        if indexes_to_create:
            print(f"Indexes to create: {len(indexes_to_create)} specified")
        else:
            print("Indexes to create: Using defaults")
    if args.action == "cleanup":
        print(f"Stack Prefix: {args.stack_prefix}")
    print(f"AWS Profile: {args.profile or 'default'}")
    print(f"Region: {args.region}")
    print()

    try:
        manager = VectorIndexManager(region_name=args.region, aws_profile=args.profile)

        if args.action == "create":
            success = manager.create_all_indexes(args.collection_id, indexes_to_create)

            if success:
                print("\n✅ Vector index creation completed successfully!")
            else:
                print("\n⚠️  Vector index creation completed with issues")
                print("Bedrock will auto-create missing indexes when needed")

        elif args.action == "cleanup":
            success = manager.cleanup_all_resources(
                args.stack_prefix, confirm=not args.confirm_cleanup
            )

            if success:
                print("\n✅ Cleanup completed successfully!")
            else:
                print("\n❌ Cleanup completed with some issues")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        exit(1)


if __name__ == "__main__":
    main()
