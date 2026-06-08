"""
A4A MCP Handler Lambda — Target 2 on the AdCP Gateway.

Exposes 3 MCP tools:
  - list_agents: DynamoDB scan → agent catalog grouped by family
  - get_agent_schema: Static input schema for a named agent
  - invoke_agent: InvokeAgentRuntime → raw text response

This Lambda follows the same handler pattern as adcp_mcp_handler.py (Target 1).
It reads from the same DynamoDB AgentConfigTable and invokes the same AgentCore
HTTP runtime — zero agent logic duplication.

Environment variables:
  AGENT_CONFIG_TABLE: DynamoDB table name (e.g., a4a-AgentConfig-omixaj)
  GUIDANCE_RUNTIME_ARN: AgentCore runtime ARN for the guidance agent
  STACK_PREFIX: Stack prefix (e.g., a4a)
  UNIQUE_ID: Unique deployment ID (e.g., omixaj)
  AWS_REGION: AWS region (e.g., us-west-2)
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict
from uuid import uuid4

import boto3
from botocore.exceptions import ClientError, ReadTimeoutError

logger = logging.getLogger("a4a-mcp-handler")
logger.setLevel(logging.INFO)

# Environment
AGENT_CONFIG_TABLE = os.environ.get("AGENT_CONFIG_TABLE", "")
GUIDANCE_RUNTIME_ARN = os.environ.get("GUIDANCE_RUNTIME_ARN", "")
AWS_REGION = os.environ.get("A4A_REGION", os.environ.get("AWS_REGION", "us-west-2"))

# === Async Invoke + Long-Poll Configuration ===
# Proactive timeout: if agent doesn't respond within this window, return session_id for polling
INVOKE_TIMEOUT_SECONDS = int(os.environ.get("INVOKE_TIMEOUT_SECONDS", "60"))
# Long-poll: max time get_agent_conversation blocks waiting for completion
LONG_POLL_MAX_SECONDS = int(os.environ.get("LONG_POLL_MAX_SECONDS", "55"))
# Long-poll: interval between memory checks
LONG_POLL_INTERVAL_SECONDS = int(os.environ.get("LONG_POLL_INTERVAL_SECONDS", "5"))
# Give-up: if no new messages for this long, mark as failed
GIVE_UP_AFTER_SECONDS = int(os.environ.get("GIVE_UP_AFTER_SECONDS", "300"))
# AgentCore Memory ID for conversation retrieval (orchestrator's memory)
AGENTCORE_MEMORY_ID = os.environ.get("AGENTCORE_MEMORY_ID", os.environ.get("MEMORY_ID", ""))


# ============================================================================
# CloudWatch Metrics
# ============================================================================

_cloudwatch_client = None


def _emit_metric(metric_name: str, value: float = 1, unit: str = "Count"):
    """Emit a CloudWatch metric to the A4A/MCPHandler namespace."""
    global _cloudwatch_client
    try:
        if _cloudwatch_client is None:
            _cloudwatch_client = boto3.client("cloudwatch", region_name=AWS_REGION)
        _cloudwatch_client.put_metric_data(
            Namespace="A4A/MCPHandler",
            MetricData=[{
                "MetricName": metric_name,
                "Value": value,
                "Unit": unit,
            }],
        )
    except Exception as e:
        logger.warning(f"Failed to emit metric {metric_name}: {e}")


# ============================================================================
# Main Handler
# ============================================================================


def handler(event, context):
    """Main Lambda handler for A4A MCP agent tools."""
    logger.info(f"Received event: {json.dumps(event)}")

    # Extract tool name from context (AgentCore Gateway passes it here)
    raw_tool_name = None

    # Primary: context.client_context.custom.bedrockAgentCoreToolName
    if context and hasattr(context, "client_context") and context.client_context:
        custom_context = getattr(context.client_context, "custom", None)
        if custom_context:
            raw_tool_name = custom_context.get("bedrockAgentCoreToolName")
            logger.info(f"Tool name from context: {raw_tool_name}")

    # Fallback: event fields (for direct invocation/testing)
    if not raw_tool_name:
        raw_tool_name = (
            event.get("tool_name")
            or event.get("name")
            or event.get("toolName")
            or event.get("tool", {}).get("name", "")
        )
        if raw_tool_name:
            logger.info(f"Tool name from event: {raw_tool_name}")

    # Strip gateway target prefix: "target-name___tool_name" → "tool_name"
    if raw_tool_name and "___" in raw_tool_name:
        tool_name = raw_tool_name.split("___")[-1]
        logger.info(f"Stripped prefix: {raw_tool_name} -> {tool_name}")
    else:
        tool_name = raw_tool_name or ""

    # Extract arguments (same pattern as Target 1)
    if "arguments" in event or "input" in event or "toolInput" in event:
        arguments = (
            event.get("arguments")
            or event.get("input")
            or event.get("toolInput")
            or event.get("tool", {}).get("input", {})
        )
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
    else:
        # AgentCore Gateway: event IS the arguments directly
        arguments = event if event else {}

    logger.info(f"Tool: {tool_name}, Arguments: {json.dumps(arguments)}")

    # Route to handler
    handlers = {
        "list_agents": handle_list_agents,
        "get_agent_schema": handle_get_agent_schema,
        "invoke_agent": handle_invoke_agent,
        "get_agent_conversation": handle_get_agent_conversation,
    }

    if tool_name in handlers:
        try:
            result = handlers[tool_name](arguments)
            return format_response(200, result)
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            logger.error(f"AWS error in {tool_name}: {error_code} - {e}")
            if error_code in ("ResourceNotFoundException", "TableNotFoundException"):
                return format_response(503, {"error": "Agent configuration temporarily unavailable. Retry in 30s."})
            return format_response(502, {"error": f"AWS service error: {error_code}"})
        except Exception as e:
            logger.error(f"Error in {tool_name}: {e}")
            return format_response(500, {"error": str(e)})

    return format_response(400, {"error": f"Unknown tool: {tool_name}"})


def format_response(status_code: int, result: Dict) -> Dict:
    """Format response for MCP Gateway (same format as Target 1)."""
    # If result has a "content" key, use it directly (for raw text responses)
    if "content" in result and isinstance(result["content"], list):
        return {
            "statusCode": status_code,
            "body": json.dumps(result),
            "content": result["content"],
        }
    # Otherwise wrap as JSON text
    body = json.dumps(result)
    return {
        "statusCode": status_code,
        "body": body,
        "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
    }


# ============================================================================
# Tool Handlers
# ============================================================================


def handle_list_agents(args: Dict) -> Dict:
    """List available agents from DynamoDB AgentConfigTable.

    Reads from GLOBAL_CONFIG record which contains all agent_configs.
    Filters by category (team_name mapping), groups by family (team_name),
    sorts alphabetically.
    """
    category = args.get("category", "orchestrator")

    dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
    table = dynamodb.Table(AGENT_CONFIG_TABLE)

    # Read global config (contains all agent_configs)
    try:
        response = table.get_item(Key={"pk": "GLOBAL_CONFIG", "sk": "v1"})
    except ClientError as e:
        logger.error(f"DynamoDB get_item failed: {e}")
        raise

    item = response.get("Item")
    if not item:
        # Fallback: try scanning for INSTRUCTION# records
        try:
            scan_response = table.scan(
                FilterExpression="begins_with(pk, :prefix) AND sk = :sk",
                ExpressionAttributeValues={":prefix": "INSTRUCTION#", ":sk": "v1"},
            )
            agents = []
            for record in scan_response.get("Items", []):
                name = record["pk"].replace("INSTRUCTION#", "")
                agents.append({
                    "name": name,
                    "description": "",
                    "category": "specialist",
                    "family": "General",
                    "best_for": [],
                })
            agents.sort(key=lambda a: a["name"].lower())
            return {"agents": agents, "grouped_by_family": {"General": [a["name"] for a in agents]}, "total": len(agents), "category_filter": category}
        except ClientError:
            raise

    # Parse global config
    content = item.get("content", "{}")
    if isinstance(content, str):
        try:
            global_config = json.loads(content)
        except json.JSONDecodeError:
            global_config = {}
    else:
        global_config = content

    agent_configs = global_config.get("agent_configs", {})

    # Build agent list from configs
    # Orchestrators have tool_agent_names (they route to other agents)
    agents = []
    for agent_key, config in agent_configs.items():
        tool_agents = config.get("tool_agent_names", [])
        # Agents with tool_agent_names are orchestrators; others are specialists
        agent_category = "orchestrator" if tool_agents else "specialist"

        agent = {
            "name": config.get("agent_name", agent_key),
            "description": config.get("agent_description", ""),
            "category": agent_category,
            "family": config.get("agent_family", config.get("team_name", "General")),
            "best_for": [],
            "display_name": config.get("agent_display_name", agent_key),
        }
        agents.append(agent)

    # Filter by category
    if category == "orchestrator":
        agents = [a for a in agents if a["category"] == "orchestrator"]
    elif category == "specialist":
        agents = [a for a in agents if a["category"] == "specialist"]
    # "all" returns everything

    # Sort: families alphabetically, agents alphabetically within each family
    agents.sort(key=lambda a: (a["family"].lower(), a["name"].lower()))

    # Build grouped_by_family
    grouped = {}
    for agent in agents:
        family = agent["family"]
        if family not in grouped:
            grouped[family] = []
        grouped[family].append(agent["name"])

    return {
        "agents": agents,
        "grouped_by_family": grouped,
        "total": len(agents),
        "category_filter": category,
    }


def handle_get_agent_schema(args: Dict) -> Dict:
    """Return structured input schema for a named agent.
    
    Reads from GLOBAL_CONFIG in DynamoDB. Since agents don't store explicit
    input_schema fields, this returns the agent's metadata (description,
    model config, tools) and notes that all agents accept free-text prompts.
    """
    agent_name = args.get("agent_name", "")
    if not agent_name:
        return {"error": "Missing required parameter: agent_name"}

    dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
    table = dynamodb.Table(AGENT_CONFIG_TABLE)

    # Read global config
    try:
        response = table.get_item(Key={"pk": "GLOBAL_CONFIG", "sk": "v1"})
    except ClientError as e:
        logger.error(f"DynamoDB get_item failed: {e}")
        raise

    item = response.get("Item")
    if not item:
        return {"error": f"Agent '{agent_name}' not found. Use list_agents to see available agents."}

    content = item.get("content", "{}")
    if isinstance(content, str):
        try:
            global_config = json.loads(content)
        except json.JSONDecodeError:
            global_config = {}
    else:
        global_config = content

    agent_configs = global_config.get("agent_configs", {})
    config = agent_configs.get(agent_name)

    if not config:
        return {"error": f"Agent '{agent_name}' not found. Use list_agents to see available agents."}

    # Build schema response from agent config metadata
    tool_agents = config.get("tool_agent_names", [])
    agent_tools = config.get("agent_tools", [])

    return {
        "agent_name": agent_name,
        "display_name": config.get("agent_display_name", agent_name),
        "description": config.get("agent_description", ""),
        "team": config.get("team_name", ""),
        "category": "orchestrator" if tool_agents else "specialist",
        "collaborators": tool_agents,
        "tools": agent_tools,
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Natural language task description"},
                "context": {"type": "string", "description": "Optional additional context (RFP text, account data, etc.)"},
            },
            "required": ["prompt"],
        },
        "note": "All agents accept free-text prompts via invoke_agent. No structured parameters required.",
    }


def handle_invoke_agent(args: Dict) -> Dict:
    """Invoke an agent via AgentCore runtime — supports sync and async modes.

    Async mode (default): Uses INVOKE_TIMEOUT_SECONDS (60s) read timeout.
      - If agent responds within timeout → returns {status: "completed", ...}
      - If timeout reached → returns {status: "processing", session_id, ...} for polling
    Sync mode (async=False): Uses 280s read timeout (backward compatible).
    """
    agent_name = args.get("agent_name", "")
    prompt = args.get("prompt", "")
    session_id = args.get("session_id", "")
    context_text = args.get("context", "")
    async_mode = args.get("async", True)

    # Normalize async param (may come as string from MCP)
    if isinstance(async_mode, str):
        async_mode = async_mode.lower() != "false"

    # Validate required params
    if not agent_name:
        return {"error": "Missing required parameter: agent_name"}
    if not prompt:
        return {"error": "Missing required parameter: prompt"}

    # Validate agent exists
    dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
    table = dynamodb.Table(AGENT_CONFIG_TABLE)
    try:
        check = table.get_item(Key={"pk": "GLOBAL_CONFIG", "sk": "v1"})
        item = check.get("Item")
        if item:
            content = item.get("content", "{}")
            if isinstance(content, str):
                try:
                    global_config = json.loads(content)
                except json.JSONDecodeError:
                    global_config = {}
            else:
                global_config = content
            agent_configs = global_config.get("agent_configs", {})
            if agent_name not in agent_configs:
                return {"error": f"Agent '{agent_name}' not found. Use list_agents to see available agents."}
    except ClientError:
        pass  # Proceed anyway — don't block invocation on config read failure

    # Session management — use session_id directly as runtime session for multi-turn support.
    # AgentCore requires runtimeSessionId to be at least 33 chars.
    if session_id and len(session_id) > 0:
        # Deterministic: same session_id always maps to same runtime session
        runtime_session = f"mcp-runtime-{session_id}"
        # Ensure minimum 33 chars
        if len(runtime_session) < 33:
            runtime_session = runtime_session.ljust(33, "0")
    else:
        session_id = f"session-{uuid4().hex}"  # Full UUID hex = 32 chars
        runtime_session = f"mcp-runtime-{session_id}"  # 44+ chars

    # Build prompt with @agent routing (same format as Angular UI)
    full_prompt = f"@[{agent_name}] {prompt}"
    if context_text:
        full_prompt += f"\n\nAdditional context:\n{context_text}"

    logger.info(f"Invoking {agent_name} (session={runtime_session}, async={async_mode})")

    # Choose timeout based on mode
    if async_mode:
        read_timeout = INVOKE_TIMEOUT_SECONDS  # 60s — proactive timeout
    else:
        read_timeout = 280  # Full sync — backward compatible

    from botocore.config import Config
    agentcore_config = Config(
        read_timeout=read_timeout,
        connect_timeout=10,
        retries={"max_attempts": 0},  # No retries — let the caller decide
    )
    client = boto3.client("bedrock-agentcore", region_name=AWS_REGION, config=agentcore_config)

    try:
        response = client.invoke_agent_runtime(
            agentRuntimeArn=GUIDANCE_RUNTIME_ARN,
            runtimeSessionId=runtime_session,
            qualifier="DEFAULT",
            contentType="application/json",
            accept="application/json",
            payload=json.dumps({
                "prompt": full_prompt,
                "session_id": runtime_session,
                "memory_id": "default",
                "agent_name": agent_name,
            }).encode("utf-8"),
        )
    except ReadTimeoutError:
        # Async fallback — agent is still running, return session_id for polling
        logger.info(f"invoke_agent: timeout after {read_timeout}s for {agent_name} (session={session_id})")
        _emit_metric("InvokeAgent.AsyncFallback")
        return {
            "status": "processing",
            "session_id": session_id,
            "agent_name": agent_name,
            "message": f"Agent is still processing your request. Poll with get_agent_conversation(session_id='{session_id}') after 30 seconds to retrieve the full response.",
            "content": [{"type": "text", "text": f"\u23f3 {agent_name} is working on your request (session: {session_id}). Use get_agent_conversation to retrieve results."}],
        }
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        logger.error(f"invoke_agent: ClientError {error_code} for {agent_name} (session={session_id}): {e}")
        _emit_metric("InvokeAgent.Error")
        return {"status": "failed", "session_id": session_id, "error": f"Agent runtime error: {error_code} - {e}"}

    # Collect response from stream — ReadTimeoutError can also fire here during iteration
    try:
        full_response = _collect_response(response)
    except ReadTimeoutError:
        # Async fallback — agent is still running, stream timed out during collection
        logger.info(f"invoke_agent: stream read timeout after {read_timeout}s for {agent_name} (session={session_id})")
        _emit_metric("InvokeAgent.AsyncFallback")
        return {
            "status": "processing",
            "session_id": session_id,
            "agent_name": agent_name,
            "message": f"Agent is still processing your request. Poll with get_agent_conversation(session_id='{session_id}') after 30 seconds to retrieve the full response.",
            "content": [{"type": "text", "text": f"\u23f3 {agent_name} is working on your request (session: {session_id}). Use get_agent_conversation to retrieve results."}],
        }

    # Extract text from SSE data lines (minimal parsing)
    raw_text = _extract_text_from_sse(full_response)

    logger.info(f"invoke_agent: {len(raw_text)} chars returned for {agent_name}")
    _emit_metric("InvokeAgent.SyncSuccess")

    # Return completed response with status
    return {
        "status": "completed",
        "session_id": session_id,
        "agent_name": agent_name,
        "content": [
            {"type": "text", "text": f"{raw_text}\n\n---\n*Session: `{session_id}` \u00b7 Agent: {agent_name}*"}
        ],
    }


# ============================================================================
# Helpers
# ============================================================================


def _collect_response(response: Dict) -> str:
    """Collect full response text from AgentCore runtime response object."""
    full_response = ""

    # Try EventStream (output)
    if "output" in response:
        for event in response["output"]:
            if "chunk" in event:
                chunk_bytes = event["chunk"].get("bytes", b"")
                if isinstance(chunk_bytes, bytes):
                    full_response += chunk_bytes.decode("utf-8")
                else:
                    full_response += str(chunk_bytes)
            elif "bytes" in event:
                chunk_bytes = event["bytes"]
                if isinstance(chunk_bytes, bytes):
                    full_response += chunk_bytes.decode("utf-8")
                else:
                    full_response += str(chunk_bytes)

    # Try body
    if not full_response and "body" in response:
        body = response["body"]
        if hasattr(body, "read"):
            full_response = body.read().decode("utf-8")
        elif isinstance(body, bytes):
            full_response = body.decode("utf-8")
        elif isinstance(body, str):
            full_response = body

    # Try response (StreamingBody)
    if not full_response and "response" in response:
        resp_body = response["response"]
        if hasattr(resp_body, "read"):
            full_response = resp_body.read().decode("utf-8")
        elif isinstance(resp_body, bytes):
            full_response = resp_body.decode("utf-8")
        elif isinstance(resp_body, str):
            full_response = resp_body

    return full_response


def _extract_text_from_sse(raw: str) -> str:
    """Extract text content from SSE data lines. Returns raw text."""
    if not raw:
        return ""

    assistant_texts = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line.startswith("data:"):
            continue
        json_str = line[5:].strip()
        if not json_str:
            continue
        try:
            data = json.loads(json_str)
            message = data.get("message", {})
            for block in message.get("content", []):
                if "text" in block:
                    assistant_texts.append(block["text"])
                if "toolResult" in block:
                    for tc in block["toolResult"].get("content", []):
                        if "text" in tc:
                            assistant_texts.append(tc["text"])
        except json.JSONDecodeError:
            continue

    # If we extracted text from SSE, join it; otherwise return raw (might not be SSE format)
    if assistant_texts:
        return "\n\n".join(assistant_texts)
    return raw


# ============================================================================
# get_agent_conversation — Long-Poll for Agent Results
# ============================================================================


def handle_get_agent_conversation(args: Dict) -> Dict:
    """Poll for results of a previously started agent conversation.

    Uses long-polling: blocks up to LONG_POLL_MAX_SECONDS (55s), checking
    AgentCore Memory every LONG_POLL_INTERVAL_SECONDS (5s) until the
    conversation completes or the poll window expires.

    Status determination:
      - not_found: No messages in memory for this session
      - completed: Final assistant message with substantive content (>50 chars)
      - in_progress: Messages exist but agent hasn't finished
      - failed: No new messages for GIVE_UP_AFTER_SECONDS (5 min)
    """
    session_id = args.get("session_id", "")
    no_wait = args.get("no_wait", False)

    # Normalize no_wait (may come as string)
    if isinstance(no_wait, str):
        no_wait = no_wait.lower() == "true"

    if not session_id:
        return {"error": "Missing required parameter: session_id"}

    # Construct the runtime session key (must match invoke_agent's format)
    runtime_session = f"mcp-runtime-{session_id}"
    if len(runtime_session) < 33:
        runtime_session = runtime_session.ljust(33, "0")

    if not AGENTCORE_MEMORY_ID:
        return {"status": "error", "session_id": session_id, "error": "Memory service not configured (AGENTCORE_MEMORY_ID not set)"}

    # Initialize memory client
    try:
        from bedrock_agentcore.memory import MemoryClient
        memory_client = MemoryClient(region_name=AWS_REGION)
    except ImportError:
        # Fallback to boto3 if SDK not available in Lambda
        memory_client = None
        agentcore_client = boto3.client("bedrock-agentcore", region_name=AWS_REGION)

    # Determine actor_id — use agent_name from args if provided (caller knows which agent was invoked)
    actor_id = args.get("agent_name", "")

    poll_start = time.time()
    last_status = "not_found"
    last_messages = []

    # Actor IDs to try — prioritize the provided agent_name, then common orchestrator targets
    actor_ids_to_try = [actor_id] if actor_id else []
    actor_ids_to_try.extend([
        "MediaPlanningAgent", "AgencyAgent", "CampaignOptimizationAgent",
        "InventoryOptimizationAgent", "YieldOptimizationAgent",
        "AAMPBuyerCrewAgent", "AAMPSellerCrewAgent", "AdFabricAgent",
    ])
    # Deduplicate while preserving order
    seen = set()
    actor_ids_to_try = [x for x in actor_ids_to_try if x and not (x in seen or seen.add(x))]

    poll_start = time.time()
    last_status = "not_found"
    last_messages = []

    while True:
        # Read from memory — try multiple actor IDs until we find events
        turns = []
        try:
            if memory_client:
                for try_actor in actor_ids_to_try:
                    turns = memory_client.get_last_k_turns(
                        memory_id=AGENTCORE_MEMORY_ID,
                        actor_id=try_actor,
                        session_id=runtime_session,
                        k=20,
                        branch_name="main",
                        max_results=40,
                    )
                    if turns:
                        logger.info(f"get_agent_conversation: found events under actor_id={try_actor}")
                        break
            else:
                # Boto3 fallback — use list_events API (data plane)
                for try_actor in actor_ids_to_try:
                    try:
                        resp = agentcore_client.list_events(
                            memoryId=AGENTCORE_MEMORY_ID,
                            actorId=try_actor,
                            sessionId=runtime_session,
                            maxResults=20,
                        )
                        events = resp.get("events", [])
                        if events:
                            # Convert to turns format
                            turns = []
                            for evt in events:
                                payload = evt.get("payload", [])
                                turn_msgs = []
                                for p in payload:
                                    conv = p.get("conversational", {})
                                    if conv:
                                        role = conv.get("role", "USER").lower()
                                        text = conv.get("content", {}).get("text", "")
                                        turn_msgs.append({"role": role, "content": {"text": text}})
                                if turn_msgs:
                                    turns.append(turn_msgs)
                            if turns:
                                logger.info(f"get_agent_conversation: found {len(events)} events under actor_id={try_actor}")
                                break
                    except ClientError as ce:
                        if ce.response["Error"]["Code"] == "ResourceNotFoundException":
                            continue
                        raise
        except Exception as e:
            logger.error(f"get_agent_conversation: memory read failed: {e}")
            return {"status": "error", "session_id": session_id, "error": f"Memory service temporarily unavailable: {e}"}

        # Flatten turns into messages, filter out tool messages
        messages = []
        last_message_time = None
        specialists = set()

        logger.info(f"get_agent_conversation: processing {len(turns)} turns, structure: {[type(t).__name__ for t in (turns or [])]}")

        # Reverse turns to ensure chronological order (API returns newest first)
        for turn in reversed(turns or []):
            turn_items = turn if isinstance(turn, list) else [turn]
            for msg in turn_items:
                role = msg.get("role", "user").lower()
                content = msg.get("content", {})

                # Extract text
                if isinstance(content, dict):
                    text = content.get("text", "")
                elif isinstance(content, str):
                    text = content
                else:
                    text = str(content)

                # Skip tool-related messages
                if any(marker in text for marker in ["toolUse", "toolResult", "tooluse_", "tool_use_id"]):
                    continue

                # Skip empty/short messages
                if not text or len(text.strip()) < 3:
                    continue

                messages.append({"role": role, "text": text})  # Full text — no truncation

                # Track timestamp (use current time as approximation since memory doesn't always return timestamps)
                last_message_time = datetime.now(timezone.utc)

                # Track specialists from agent-message tags
                if "<agent-message agent=" in text:
                    agent_match = re.search(r"<agent-message agent='([^']+)'", text)
                    if agent_match:
                        specialists.add(agent_match.group(1))

        # Determine status
        logger.info(f"get_agent_conversation: {len(messages)} messages after filtering. Last role: {messages[-1]['role'] if messages else 'N/A'}, last text len: {len(messages[-1]['text']) if messages else 0}")
        if not messages:
            last_status = "not_found"
        elif messages and messages[-1]["role"] == "assistant" and len(messages[-1]["text"]) > 50:
            last_status = "completed"
        else:
            # Check give-up threshold (approximate — based on poll duration)
            elapsed_total = time.time() - poll_start
            # If we've been polling for a while and still no completion, check if agent might be dead
            # In practice, the give-up is based on the invoke time, not poll time
            # For now, use in_progress
            last_status = "in_progress"

        last_messages = messages

        # Terminal states — return immediately
        if last_status in ("completed", "not_found", "error"):
            break

        # no_wait mode — return current status immediately
        if no_wait:
            break

        # Check if long-poll window expired
        elapsed = time.time() - poll_start
        if elapsed >= LONG_POLL_MAX_SECONDS:
            break

        # Wait before next check
        time.sleep(LONG_POLL_INTERVAL_SECONDS)

    # Build response based on status
    elapsed_seconds = round(time.time() - poll_start, 1)

    if last_status == "completed":
        _emit_metric("GetConversation.Completed")
        result_summary = last_messages[-1]["text"] if last_messages else ""
        return {
            "status": "completed",
            "session_id": session_id,
            "messages": last_messages[-10:],  # Last 10 messages to keep response size manageable
            "specialists_invoked": sorted(list(specialists)),
            "result_summary": result_summary,
            "turn_count": len(last_messages),
            "content": [{"type": "text", "text": result_summary}],
        }
    elif last_status == "not_found":
        _emit_metric("GetConversation.NotFound")
        return {
            "status": "not_found",
            "session_id": session_id,
            "message": f"No conversation found for session '{session_id}'. The agent may not have started yet — try again in a few seconds.",
            "content": [{"type": "text", "text": f"No conversation found for session '{session_id}'."}],
        }
    else:  # in_progress
        _emit_metric("GetConversation.InProgress")
        partial_count = len(last_messages)
        return {
            "status": "in_progress",
            "session_id": session_id,
            "message": f"Agent is still processing ({partial_count} messages so far, polled for {elapsed_seconds}s). Call get_agent_conversation again to continue waiting.",
            "partial_messages": partial_count,
            "elapsed_seconds": elapsed_seconds,
            "content": [{"type": "text", "text": f"\u23f3 Agent still processing ({partial_count} messages, {elapsed_seconds}s elapsed). Call get_agent_conversation(session_id='{session_id}') again."}],
        }
