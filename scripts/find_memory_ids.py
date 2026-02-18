#!/usr/bin/env python3
"""
Helper script to find memory IDs from AgentCore runtime configurations.
This script looks through your project files to find memory IDs that have been created.
"""

import argparse
import json
import os
import re
from pathlib import Path
from typing import List, Dict, Set

def find_memory_ids_in_files(search_paths: List[str]) -> Dict[str, List[str]]:
    """Search for memory IDs in project files."""
    memory_pattern = re.compile(r'mem-[a-zA-Z0-9]{6,}')
    found_memories = {}
    
    for search_path in search_paths:
        path = Path(search_path)
        if not path.exists():
            continue
            
        if path.is_file():
            files_to_search = [path]
        else:
            # Search common file types
            files_to_search = []
            for pattern in ['*.json', '*.py', '*.txt', '*.md', '*.log']:
                files_to_search.extend(path.rglob(pattern))
    
        for file_path in files_to_search:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    matches = memory_pattern.findall(content)
                    if matches:
                        relative_path = str(file_path.relative_to(Path.cwd()))
                        found_memories[relative_path] = list(set(matches))
            except (UnicodeDecodeError, PermissionError):
                # Skip binary files or files we can't read
                continue
            except Exception as e:
                print(f"⚠️  Error reading {file_path}: {e}")
    
    return found_memories

def find_runtime_configs() -> List[Dict[str, str]]:
    """Find AgentCore runtime configuration files."""
    configs = []
    
    # Look for .agentcore-agents-*.json files
    for config_file in Path('.').glob('.agentcore-agents-*.json'):
        try:
            with open(config_file, 'r') as f:
                data = json.load(f)
                for agent_name, config in data.items():
                    if isinstance(config, dict) and 'runtimeId' in config:
                        configs.append({
                            'agent_name': agent_name,
                            'runtime_id': config['runtimeId'],
                            'runtime_arn': config.get('runtimeArn', 'N/A'),
                            'config_file': str(config_file)
                        })
        except Exception as e:
            print(f"⚠️  Error reading {config_file}: {e}")
    
    return configs

def extract_memory_from_logs() -> Set[str]:
    """Extract memory IDs from log files and test outputs."""
    memory_ids = set()
    
    # Common log locations
    log_paths = [
        'agentcore/agents/*/test_*.py',
        'test_*.py',
        '*.log',
        'agentcore/agents/*/logs/*',
    ]
    
    memory_pattern = re.compile(r'mem-[a-zA-Z0-9]{6,}')
    
    for pattern in log_paths:
        for file_path in Path('.').glob(pattern):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    matches = memory_pattern.findall(content)
                    memory_ids.update(matches)
            except (UnicodeDecodeError, PermissionError):
                # Skip files we can't read
                continue
            except Exception as e:
                # Log unexpected errors but continue processing other files
                print(f"Warning: Error processing {file_path}: {e}", file=sys.stderr)
                continue
    
    return memory_ids

def main():
    parser = argparse.ArgumentParser(description="Find memory IDs in AgentCore project")
    parser.add_argument('--search-paths', nargs='+', 
                       default=['agentcore/', 'test_*.py', '.agentcore-*.json'],
                       help='Paths to search for memory IDs')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    
    args = parser.parse_args()
    
    print("🔍 Searching for AgentCore Memory IDs...")
    print("=" * 50)
    
    # Find runtime configurations
    print("\n📋 AgentCore Runtime Configurations:")
    runtime_configs = find_runtime_configs()
    if runtime_configs:
        for config in runtime_configs:
            print(f"  Agent: {config['agent_name']}")
            print(f"    Runtime ID: {config['runtime_id']}")
            print(f"    Config File: {config['config_file']}")
            print()
    else:
        print("  No AgentCore runtime configurations found")
    
    # Search for memory IDs in files
    print("\n🔍 Memory IDs found in project files:")
    found_memories = find_memory_ids_in_files(args.search_paths)
    
    all_memory_ids = set()
    if found_memories:
        for file_path, memory_ids in found_memories.items():
            print(f"\n📄 {file_path}:")
            for memory_id in memory_ids:
                print(f"    {memory_id}")
                all_memory_ids.add(memory_id)
    else:
        print("  No memory IDs found in project files")
    
    # Extract from logs
    print("\n📝 Memory IDs from logs and test files:")
    log_memory_ids = extract_memory_from_logs()
    if log_memory_ids:
        for memory_id in sorted(log_memory_ids):
            print(f"    {memory_id}")
            all_memory_ids.add(memory_id)
    else:
        print("  No memory IDs found in logs")
    
    # Summary
    print(f"\n📊 Summary:")
    print(f"  Total unique memory IDs found: {len(all_memory_ids)}")
    print(f"  AgentCore runtimes configured: {len(runtime_configs)}")
    
    if all_memory_ids:
        print(f"\n🎯 Test with any of these memory IDs:")
        for memory_id in sorted(all_memory_ids)[:5]:  # Show first 5
            print(f"  ./scripts/memory_helper.sh get-all {memory_id} -p rtbag")
        
        if len(all_memory_ids) > 5:
            print(f"  ... and {len(all_memory_ids) - 5} more")
    else:
        print(f"\n💡 No memory IDs found. To create memories:")
        print(f"  1. Run an AgentCore agent that uses memory")
        print(f"  2. Check the agent logs for memory IDs")
        print(f"  3. Memory IDs are typically created when agents store information")
    
    if args.verbose and all_memory_ids:
        print(f"\n📋 All found memory IDs:")
        for memory_id in sorted(all_memory_ids):
            print(f"  {memory_id}")

if __name__ == "__main__":
    main()