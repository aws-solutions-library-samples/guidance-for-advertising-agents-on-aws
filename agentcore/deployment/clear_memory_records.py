#!/usr/bin/env python3
"""
Clear all memory records from an AgentCore Memory.

Usage:
    python clear_memory_records.py <memory-id> <aws-profile> [region]

Example:
    python clear_memory_records.py xixmemory420842-QtKuPI8c62 rtbag2 us-east-1
"""

import sys
import boto3
from botocore.config import Config

def get_clients(profile: str, region: str):
    """Create boto3 clients for AgentCore."""
    session = boto3.Session(profile_name=profile, region_name=region)
    
    # Control plane client for get_memory
    control_client = session.client('bedrock-agentcore-control')
    
    # Data plane client for memory operations
    data_client = session.client('bedrock-agentcore')
    
    return control_client, data_client


def get_memory_details(control_client, memory_id: str):
    """Get memory details including strategies."""
    try:
        response = control_client.get_memory(memoryId=memory_id)
        return response.get('memory', {})
    except Exception as e:
        print(f"Error getting memory details: {e}")
        return None


def list_memory_records(data_client, memory_id: str, namespace: str):
    """List all memory records in a namespace using pagination.
    
    Uses list_memory_records API for listing all records (no semantic query).
    Note: retrieve_memory_records is for semantic search with a query.
    """
    records = []
    next_token = None
    
    try:
        while True:
            params = {
                'memoryId': memory_id,
                'namespace': namespace,
                'maxResults': 100
            }
            if next_token:
                params['nextToken'] = next_token
            
            response = data_client.list_memory_records(**params)
            
            # API returns memoryRecordSummaries
            summaries = response.get('memoryRecordSummaries', [])
            for summary in summaries:
                record_id = summary.get('memoryRecordId')
                if record_id:
                    records.append(record_id)
            
            next_token = response.get('nextToken')
            if not next_token:
                break
                
    except Exception as e:
        # Namespace might not exist or be empty
        if 'ValidationException' not in str(e) and 'ResourceNotFoundException' not in str(e):
            print(f"  Warning: {e}")
    
    return records


def batch_delete_records(data_client, memory_id: str, record_ids: list):
    """Delete memory records in batches of 100."""
    batch_size = 100
    total_deleted = 0
    total_failed = 0
    
    for i in range(0, len(record_ids), batch_size):
        batch = record_ids[i:i + batch_size]
        records_to_delete = [{'memoryRecordId': rid} for rid in batch]
        
        try:
            response = data_client.batch_delete_memory_records(
                memoryId=memory_id,
                records=records_to_delete
            )
            
            successful = len(response.get('successfulRecords', []))
            failed = len(response.get('failedRecords', []))
            total_deleted += successful
            total_failed += failed
            
            print(f"  Batch {i//batch_size + 1}: {successful} deleted, {failed} failed")
            
        except Exception as e:
            print(f"  Batch {i//batch_size + 1} error: {e}")
            total_failed += len(batch)
    
    return total_deleted, total_failed


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    
    memory_id = sys.argv[1]
    profile = sys.argv[2]
    region = sys.argv[3] if len(sys.argv) > 3 else 'us-east-1'
    
    print("=" * 50)
    print("AgentCore Memory Records Cleaner")
    print("=" * 50)
    print(f"Memory ID: {memory_id}")
    print(f"AWS Profile: {profile}")
    print(f"Region: {region}")
    print("=" * 50)
    
    # Create clients
    print("\nConnecting to AWS...")
    control_client, data_client = get_clients(profile, region)
    
    # Get memory details
    print("\nFetching memory details...")
    memory = get_memory_details(control_client, memory_id)
    
    if not memory:
        print("Failed to get memory details. Exiting.")
        sys.exit(1)
    
    strategies = memory.get('strategies', [])
    print(f"\nFound {len(strategies)} strategy(ies):")
    for s in strategies:
        print(f"  - {s.get('name')} ({s.get('strategyId')})")
    
    # Namespace patterns to search (based on built-in strategy patterns)
    namespace_patterns = [
        '/facts/*',
        '/summaries/*', 
        '/user_preferences/*',
        '/episodic/*',
    ]
    
    print("\nSearching namespace patterns:")
    for ns in namespace_patterns:
        print(f"  {ns}")
    
    # Collect all record IDs
    print("\nFetching memory records...")
    all_record_ids = set()
    
    for namespace in namespace_patterns:
        print(f"  Checking '{namespace}'... ", end='', flush=True)
        records = list_memory_records(data_client, memory_id, namespace)
        print(f"{len(records)} record(s)")
        all_record_ids.update(records)
    
    if not all_record_ids:
        print("\nNo memory records found to delete.")
        sys.exit(0)
    
    record_list = list(all_record_ids)
    print(f"\nTotal unique memory records to delete: {len(record_list)}")
    
    # Confirm deletion
    confirm = input("\nDelete all records? (y/N): ").strip().lower()
    if confirm != 'y':
        print("Aborted.")
        sys.exit(0)
    
    # Delete records
    print("\nDeleting memory records...")
    deleted, failed = batch_delete_records(data_client, memory_id, record_list)
    
    print("\n" + "=" * 50)
    print("Deletion complete!")
    print(f"  Successfully deleted: {deleted}")
    print(f"  Failed: {failed}")
    print("=" * 50)


if __name__ == '__main__':
    main()
