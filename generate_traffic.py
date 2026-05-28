#!/usr/bin/env python3
"""Generate agentic traffic to produce OTEL traces in AgentCore observability."""

import boto3
from botocore.config import Config as BotoConfig
import json
import uuid
import time
import sys
import glob
from concurrent.futures import ThreadPoolExecutor, as_completed

REGION = "us-east-1"
PER_INVOCATION_TIMEOUT = 180  # 3 minutes max per invocation
OVERALL_TIMEOUT = 420  # 7 minutes max total

PROMPTS = [
    {
        "agent": "MediaPlanningAgent",
        "prompt": "@MediaPlanningAgent What are the top 3 audience segments for luxury auto campaigns?"
    },
    {
        "agent": "YieldOptimizationAgent",
        "prompt": "@YieldOptimizationAgent What is the current CPM benchmark for premium video inventory?"
    },
    {
        "agent": "CampaignOptimizationAgent",
        "prompt": "@CampaignOptimizationAgent Summarize key bid optimization strategies for CTV."
    },
    {
        "agent": "InventoryOptimizationAgent",
        "prompt": "@InventoryOptimizationAgent What formats have the highest fill rates?"
    },
]

start_time = time.time()


def load_deployment_config():
    """Load runtime ARN and memory ID from the deployment tracking file."""
    tracking_files = glob.glob(".agentcore-agents-*.json")
    if not tracking_files:
        print("❌ No .agentcore-agents-*.json tracking file found. Run deployment first.")
        sys.exit(1)
    # Use the most recently modified tracking file
    tracking_file = max(tracking_files, key=lambda f: __import__('os').path.getmtime(f))
    with open(tracking_file) as f:
        data = json.load(f)
    agents = data.get("deployed_agents", [])
    if not agents:
        print(f"❌ No deployed agents found in {tracking_file}")
        sys.exit(1)
    agent = agents[0]
    runtime_arn = agent["runtime_arn"]
    memory_id = agent.get("memory_config", {}).get("memory_id", "")
    if not memory_id:
        print("⚠️  No memory_id found in tracking file, proceeding without it")
    return runtime_arn, memory_id


def invoke_agent(prompt_info, runtime_arn, memory_id):
    """Invoke the agent and stream the response with timeout."""
    if time.time() - start_time > OVERALL_TIMEOUT:
        return prompt_info["agent"], False, "Skipped (overall timeout)"

    client = boto3.client(
        'bedrock-agentcore', region_name=REGION,
        config=BotoConfig(read_timeout=PER_INVOCATION_TIMEOUT, connect_timeout=30)
    )
    session_id = f"traffic-gen-{uuid.uuid4()}"
    payload = {
        "prompt": prompt_info["prompt"],
        "session_id": session_id,
        "user_id": "traffic-generator",
        "agent_name": prompt_info["agent"],
        "memory_id": memory_id
    }

    try:
        response = client.invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            runtimeSessionId=session_id,
            runtimeUserId="traffic-generator",
            qualifier='DEFAULT',
            payload=json.dumps(payload).encode('utf-8')
        )

        char_count = 0
        resp_body = response.get('response')
        if resp_body and hasattr(resp_body, 'read'):
            data = resp_body.read().decode('utf-8')
            char_count = len(data)

        return prompt_info["agent"], True, f"{char_count} chars"

    except Exception as e:
        return prompt_info["agent"], False, str(e)[:100]


def main():
    runtime_arn, memory_id = load_deployment_config()

    print("🚀 Generating agentic traffic for OTEL traces...")
    print(f"Runtime: {runtime_arn}")
    print(f"Sending {len(PROMPTS)} invocations (timeout: {PER_INVOCATION_TIMEOUT}s each, {OVERALL_TIMEOUT}s total)\n")

    results = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {executor.submit(invoke_agent, p, runtime_arn, memory_id): p for p in PROMPTS}
        for future in as_completed(futures, timeout=OVERALL_TIMEOUT):
            agent, success, detail = future.result()
            status = "✅" if success else "❌"
            print(f"  {status} {agent}: {detail}")
            results.append(success)

    elapsed = time.time() - start_time
    successes = sum(results)
    print(f"\n📊 Summary: {successes}/{len(PROMPTS)} succeeded in {elapsed:.0f}s")
    print(f"💡 Check traces in the AgentCore observability dashboard")
    return 0 if successes > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
