"""
Shared memory integration utility for AgentCore agents.
Provides standardized memory configuration using environment variables.
"""

import os
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path

try:
    from bedrock_agentcore.memory import MemoryClient
    from strands.hooks import (
        AgentInitializedEvent,
        HookProvider,
        HookRegistry,
        MessageAddedEvent,
    )

    # Import memory utilities
    from .memory import ensure_memory_exists_with_strategy
    from .memory import get_all_memories_for_stack

    MEMORY_AVAILABLE = True
except ImportError as e:
    print(f"Warning: AgentCore Memory not available, continuing without memory: {e}")
    MEMORY_AVAILABLE = False

    # Define a dummy function if memory is not available
    def ensure_memory_exists_with_strategy(memory_client, memory_id):
        return False


logger = logging.getLogger(__name__)
agentsDeploymentLogger = logging.getLogger(__name__)


class MemoryHookProvider(HookProvider):
    """Memory hook provider for AgentCore memory integration"""

    def __init__(self, memory_client, memory_id):
        self.memory_client = memory_client
        self.memory_id = memory_id
        # self.memories = get_all_memories_for_stack(memory_client = memory_client, memory_id=memory_id)
        # if len(self.memories)>0:
        #     self.memory_id = self.memories[0].get('id')

    def register_hooks(self, registry: HookRegistry):
        if not MEMORY_AVAILABLE or not self.memory_client:
            print("Memory not available, skipping hook registration")
            return

        # Use the correct HookRegistry API
        try:
            # Use add_callback method which is the correct API
            registry.add_callback(MessageAddedEvent, self._on_message_added)
            registry.add_callback(AgentInitializedEvent, self._on_agent_initialized)
            print("Memory hooks registered successfully")
        except AttributeError as e:
            print(f"add_callback method not found, trying add_hook: {e}")
            # Fallback: try add_hook method
            try:
                registry.add_hook(MessageAddedEvent, self._on_message_added)
                registry.add_hook(AgentInitializedEvent, self._on_agent_initialized)
                print("Memory hooks registered successfully (fallback method)")
            except Exception as e2:
                print(f"Failed to register hooks with fallback method: {e2}")
        except Exception as e:
            print(f"Failed to register hooks: {e}")

    def _on_message_added(self, event: MessageAddedEvent):
        """Store messages in memory when added"""
        try:
            # Get session info from agent state if available
            state = getattr(event.agent, "state", {})

            # Try different ways to access the state
            try:
                # First try as attributes
                actor_id = getattr(state, "actor_id", None)
                session_id = getattr(state, "session_id", None)

                # If not found, try as dictionary-like access
                if actor_id is None and hasattr(state, "__getitem__"):
                    try:
                        actor_id = state["actor_id"]
                    except (KeyError, TypeError):
                        pass

                if session_id is None and hasattr(state, "__getitem__"):
                    try:
                        session_id = state["session_id"]
                    except (KeyError, TypeError):
                        pass

                # Use defaults if still not found
                actor_id = actor_id or "default-actor"
                session_id = session_id or "default-session"

            except Exception as e:
                logging.warning(f"Error accessing agent state: {e}")
                actor_id = "default-actor"
                session_id = "default-session"

            # Ensure memory exists before trying to store
            if not ensure_memory_exists_with_strategy(
                self.memory_client, self.memory_id
            ):
                logging.warning(f"Could not ensure memory exists: {self.memory_id}")
                return

            # Try to store the conversation turn in memory
            try:
                self.memory_client.create_event(
                    memory_id=self.memory_id,
                    actor_id=actor_id,
                    session_id=session_id,
                    messages=[(event.message.content, event.message.role.upper())],
                )
                logging.info("Stored message in memory")
            except Exception as memory_error:
                logging.warning(f"Failed to store message in memory: {memory_error}")

        except Exception as e:
            logging.warning(f"Failed to handle message storage: {e}")

    def _on_agent_initialized(self, event: AgentInitializedEvent):
        """Initialize memory context when agent starts"""
        try:
            # Get session info from agent state if available
            state = getattr(event.agent, "state", {})
            # Try different ways to access the state
            try:
                # First try as attributes
                actor_id = getattr(state, "actor_id", None)
                session_id = getattr(state, "session_id", None)
                self.memory_id = getattr(state, "memory_id", self.memory_id)
                # If not found, try as dictionary-like access
                if actor_id is None and hasattr(state, "__getitem__"):
                    try:
                        actor_id = state["actor_id"]
                    except (KeyError, TypeError):
                        pass

                if session_id is None and hasattr(state, "__getitem__"):
                    try:
                        session_id = state["session_id"]
                    except (KeyError, TypeError):
                        pass

                # Use defaults if still not found
                actor_id = actor_id or "default-actor"
                session_id = session_id or "default-session"

            except Exception as e:
                logging.warning(f"Error accessing agent state: {e}")
                actor_id = "default-actor"
                session_id = "default-session"

            # Ensure memory exists before trying to retrieve
            if not ensure_memory_exists_with_strategy(
                self.memory_client, self.memory_id
            ):
                logging.warning(f"Could not ensure memory exists: {self.memory_id}")
                return

            # Try to get last 5 conversation turns for context
            try:
                recent_turns = self.memory_client.get_last_k_turns(
                    memory_id=self.memory_id,
                    actor_id=actor_id,
                    session_id=session_id,
                    k=5,
                    branch_name="main",
                )

                if recent_turns:
                    # Format conversation history for context
                    context_messages = []
                    for turn in recent_turns:
                        for message in turn:
                            role = message["role"].lower()
                            content = message["content"]["text"]
                            context_messages.append(f"{role.title()}: {content}")

                    context = "\n".join(context_messages)
                    logging.info(
                        f"Loaded {len(recent_turns)} recent conversation turns"
                    )

                    # Add context to agent's system prompt if available
                    if hasattr(event.agent, "system_prompt"):
                        event.agent.system_prompt += f"\n\nRecent conversation history:\n{context}\n\nContinue the conversation naturally based on this context."
                else:
                    logging.info("No previous conversation history found")

            except Exception as memory_error:
                logging.warning(f"Failed to load conversation history: {memory_error}")

        except Exception as e:
            logging.warning(f"Failed to handle memory context loading: {e}")


def get_memory_configuration(session_id=None):
    """
    Get memory configuration from environment variables and optional session_id.
    Returns tuple of (memory_id, actor_id, session_id)
    """
    # Get stack prefix and unique ID from environment variables
    stack_prefix = os.environ.get("STACK_PREFIX", "default")
    unique_id = os.environ.get("UNIQUE_ID", "default")

    # Use the consistent memory naming pattern: {stack_prefix}memory{unique_id}
    # This matches the pattern used in the deployment scripts
    default_memory_id = f"{stack_prefix}memory{unique_id}"

    memory_id = os.environ.get("MEMORY_ID", default_memory_id)
    actor_id = os.environ.get("ACTOR_ID", "default-actor")
    # Use provided session_id or fall back to default
    if session_id is None:
        session_id = "default-session"

    print(
        f"Memory configuration - ID: {memory_id}, Actor: {actor_id}, Session: {session_id}"
    )

    return memory_id, actor_id, session_id


def ensure_memory_exists(memory_client, memory_name):
    """
    Ensure a memory exists, creating it if necessary.
    Returns the actual memory ID to use.

    This function is deprecated. Use ensure_memory_exists_with_strategy from memory.py instead.
    """
    # Use the new memory creation function
    return ensure_memory_exists_with_strategy(memory_client, memory_name)


def create_memory_hooks_and_state(bedrock_client=None, session_id=None):
    """
    Create memory hooks and state for an agent.
    Returns tuple of (hooks, state)
    """
    hooks = []
    memory_id, actor_id, final_session_id = get_memory_configuration(session_id)
    state = {
        "memory_id": memory_id,
        "actor_id": actor_id,
        "session_id": final_session_id,
    }

    agentsDeploymentLogger.info(f"Creating memory hooks and state for {actor_id}")
    return hooks, state


def extract_session_id_from_payload(payload):
    """Extract session ID from AgentCore payload"""
    try:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        if isinstance(payload, str):
            import json

            payload = json.loads(payload)

        # Look for session ID in various payload locations
        session_id = (
            payload.get("session_id")
            or payload.get("runtimeSessionId")
            or payload.get("session_metadata", {}).get("session_id")
        )

        return session_id
    except Exception as e:
        print(f"Failed to extract session ID from payload: {e}")
        return None


def get_memory_id_from_ssm(stack_prefix: str, unique_id: str) -> Optional[str]:
    """
    Retrieve memory ID from SSM parameter store.
    Pattern: /<stack-prefix>/<unique-id>/agentcore_memory_id
    """
    try:
        import boto3
        ssm_client = boto3.client('ssm')
        parameter_name = f"/{stack_prefix}/{unique_id}/agentcore_memory_id"
        
        logger.info(f"Retrieving memory ID from SSM parameter: {parameter_name}")
        
        response = ssm_client.get_parameter(Name=parameter_name, WithDecryption=False)
        memory_id = response['Parameter']['Value']
        
        logger.info(f"Retrieved memory ID from SSM: {memory_id}")
        return memory_id
    except Exception as e:
        logger.warning(f"Failed to retrieve memory ID from SSM: {e}")
        return None


def validate_and_resolve_memory_id(memory_id: Optional[str]) -> Optional[str]:
    """
    Validate memory ID format and resolve from SSM if invalid.
    Valid memory IDs contain a "-" character (e.g., "mem-abc123").
    If invalid, retrieves from SSM using pattern: /<stack-prefix>/<unique-id>/agentcore_memory_id
    """
    if memory_id and "-" in memory_id:
        # Memory ID is valid
        logger.info(f"Memory ID is valid: {memory_id}")
        return memory_id
    
    # Memory ID is invalid or missing, retrieve from SSM
    logger.warning(f"Memory ID '{memory_id}' is invalid (missing '-' character), retrieving from SSM")
    
    stack_prefix = os.environ.get("STACK_PREFIX", "default")
    unique_id = os.environ.get("UNIQUE_ID", "default")
    
    ssm_memory_id = get_memory_id_from_ssm(stack_prefix, unique_id)
    
    if ssm_memory_id:
        return ssm_memory_id
    
    # Fallback to environment variable if SSM fails
    env_memory_id = os.environ.get("MEMORY_ID")
    if env_memory_id and "-" in env_memory_id:
        logger.info(f"Using memory ID from environment: {env_memory_id}")
        return env_memory_id
    
    logger.error("Could not resolve valid memory ID from SSM or environment")
    return None


def extract_session_id_and_memory_id_and_actor_from_payload(payload):
    """Extract session ID, memory ID, and agent name from AgentCore payload"""
    try:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        if isinstance(payload, str):
            import json

            payload = json.loads(payload)

        # Look for session ID in various payload locations
        session_id = (
            payload.get("session_id")
            or payload.get("runtimeSessionId")
            or payload.get("session_metadata", {}).get("session_id")
        )

        memory_id = payload.get("memory_id") or payload.get("session_metadata", {}).get(
            "memory_id"
        )
        
        # Validate memory ID format - if it doesn't contain "-", retrieve from SSM
        memory_id = validate_and_resolve_memory_id(memory_id)

        agent_name = payload.get("agent_name") or payload.get(
            "session_metadata", {}
        ).get("agent_name")

        return session_id, memory_id, agent_name

    except Exception as e:
        print(f"Failed to extract session ID, memory ID, and agent name from payload: {e}")
        return None, None, None
