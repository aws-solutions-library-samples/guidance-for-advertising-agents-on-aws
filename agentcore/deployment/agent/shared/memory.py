"""
AgentCore Memory Manager

This module provides unified memory functionality for all AgentCore frameworks, including:
1. Loading previous conversation context during initialization
2. Retrieving relevant memories before message processing
3. Storing new messages after each response
4. Creating memory strategies when they don't exist

Usage:
    memory_manager = MemoryManager()

    # Get memory context for any framework
    memory_context = memory_manager.get_memory_context(
        user_input="User's current message",
        actor_id="user-123",
        session_id="session-456"
    )

    # Store conversation after response
    memory_manager.store_conversation(
        user_input="User's message",
        response="Agent's response",
        actor_id="user-123",
        session_id="session-456"
    )

"""

import json
import logging
import os
import time
from typing import List, Dict, Any, Optional
from pathlib import Path

from bedrock_agentcore.memory import MemoryClient


class MemoryConfig:
    """Manages memory configuration from JSON file with caching and stack-aware patterns."""

    _cached_config: Optional[Dict[str, Any]] = None
    _cached_path: Optional[str] = None

    def __init__(self, config_path: str = "memory-config.json"):
        """Initialize memory configuration.

        Args:
            config_path: Path to the memory configuration JSON file
        """
        self.config_path = config_path
        self._load_config()

    def _load_config(self) -> None:
        """Load configuration from JSON file with caching."""
        if (
            MemoryConfig._cached_config is not None
            and MemoryConfig._cached_path == self.config_path
        ):
            return

        try:
            config_file = Path(self.config_path)
            if not config_file.exists():
                raise FileNotFoundError(
                    f"Memory config file not found: {self.config_path}"
                )

            with open(config_file, "r") as f:
                config = json.load(f)

            MemoryConfig._cached_config = config
            MemoryConfig._cached_path = self.config_path

            logger = logging.getLogger(__name__)
            logger.debug(f"Loaded memory configuration from {self.config_path}")

        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to load memory configuration: {e}")
            raise

    @property
    def memory_id(self) -> str:
        """Get the memory ID."""
        return MemoryConfig._cached_config["memory_id"]

    @property
    def namespace(self) -> str:
        """Get the stack-aware namespace pattern."""
        return MemoryConfig._cached_config.get("namespace", "/stack/default/context/")

    @property
    def actor_id_pattern(self) -> str:
        """Get the actor ID pattern for consistent naming."""
        return MemoryConfig._cached_config.get(
            "actor_id_pattern", "default_actor_{unique_id}"
        )

    @property
    def session_id_pattern(self) -> str:
        """Get the session ID pattern for consistent naming."""
        return MemoryConfig._cached_config.get(
            "session_id_pattern", "default_{unique_id}_{runtime_session_id}"
        )

    @property
    def cross_agent_access(self) -> bool:
        """Check if cross-agent access is enabled."""
        return MemoryConfig._cached_config.get("cross_agent_access", True)

    def generate_memory_id(self, stack_prefix: str, unique_id: str) -> str:
        """Generate consistent memory ID using stack-specific pattern.

        Args:
            stack_prefix: The stack prefix (e.g., 'sim', 'demo3')
            unique_id: The unique identifier for the stack

        Returns:
            Formatted memory ID: {stack_prefix}memory{unique_id}
        """
        return f"{stack_prefix}memory{unique_id}"

    @classmethod
    def from_environment(cls) -> "MemoryConfig":
        """Create MemoryConfig from environment variables."""
        stack_prefix = os.environ.get("STACK_PREFIX", "default")
        unique_id = os.environ.get("UNIQUE_ID", "default")

        # Create a temporary config with the memory ID pattern
        memory_id = f"{stack_prefix}memory{unique_id}"

        # Create a temporary config file content
        config_content = {
            "memory_id": memory_id,
            "namespace": "/facts/{sessionId}/{actorId}/",
            "actor_id_pattern": "{actorId}",
            "session_id_pattern": "{sessionId}",
            "cross_agent_access": True,
        }

        # Save to temporary config file
        config_path = "memory-config.json"
        with open(config_path, "w") as f:
            json.dump(config_content, f, indent=2)

        return cls(config_path)

    def generate_namespace(self, session_id: str, actor_id: str) -> str:
        """Generate stack-aware namespace pattern.

        Args:
            stack_prefix: The stack prefix (e.g., 'sim', 'demo3')
            unique_id: The unique identifier for the stack

        Returns:
            Formatted namespace: /stack/{stack_prefix}_{unique_id}/context/
        """
        return f"/facts/{session_id}/{actor_id}/"

    def generate_actor_id(
        self, stack_prefix: str, agent_name: str, unique_id: str
    ) -> str:
        """Generate consistent actor ID using stack-specific pattern.

        Args:
            stack_prefix: The stack prefix (e.g., 'sim', 'demo3')
            agent_name: The agent name with hyphens converted to underscores
            unique_id: The unique identifier for the stack

        Returns:
            Formatted actor ID: {stack_prefix}_{agent_name}_actor_{unique_id}
        """
        # Convert hyphens to underscores for consistency
        agent_name_normalized = agent_name.replace("-", "_")
        return f"{agent_name_normalized}"

    def generate_session_id(
        self, stack_prefix: str, unique_id: str, runtime_session_id: str
    ) -> str:
        """Generate consistent session ID using stack-specific pattern.

        Args:
            stack_prefix: The stack prefix (e.g., 'sim', 'demo3')
            unique_id: The unique identifier for the stack
            runtime_session_id: The runtime-specific session identifier

        Returns:
            Formatted session ID: {stack_prefix}_{unique_id}_{runtime_session_id}
        """
        return f"{runtime_session_id}"

    def validate_stack_configuration(self, stack_prefix: str, unique_id: str) -> bool:
        """Validate that the configuration supports stack-specific settings.

        Args:
            stack_prefix: The stack prefix to validate
            unique_id: The unique identifier to validate

        Returns:
            True if configuration is valid for stack-aware operation
        """
        try:
            # Check that we can generate all required patterns
            memory_id = self.generate_memory_id(stack_prefix, unique_id)
            actor_id = self.generate_actor_id(stack_prefix, "test-agent", unique_id)
            session_id = self.generate_session_id(
                stack_prefix, unique_id, "test-session"
            )
            namespace = self.generate_namespace(session_id, actor_id)
            
            # Validate patterns are not empty and follow expected format
            if not all([memory_id, namespace, actor_id, session_id]):
                return False

            # Validate namespace format
            if not namespace.startswith("/stack/") or not namespace.endswith(
                "/context/"
            ):
                return False

            # Validate memory_id format
            if not memory_id.endswith(unique_id):
                return False

            return True

        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.error(f"Stack configuration validation failed: {e}")
            return False


def ensure_memory_exists_with_strategy(
    memory_client: MemoryClient, memory_id: str
) -> bool:
    """Ensure memory exists and has the summarization strategy.

    Args:
        memory_client: The memory client instance
        memory_id: The memory ID to check/create

    Returns:
        True if memory exists or was created successfully
    """
    return True

def get_all_memories_for_stack(
    memory_client: MemoryClient, memory_id:str
) -> List[Dict[str, Any]]:
    try:
        memories = []
        # First, try to list memories to see if one with this ID already exists
        all_memories = memory_client.list_memories()
        for memory in all_memories:
            if memory_id in memory.get("id"):
                existing_memory = memory
                memories.append(existing_memory)        
        return memories
        
    except Exception as e:
        print(f"Failed to get all stack memory records: {e}")
    
    return []

def retrieve_memories_for_actor(
    memory_id: str,
    actor_id: str,
    search_query: str,
    memory_client: MemoryClient,
    namespace: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Retrieve memories for a specific actor from the memory store.

    Args:
        memory_id: The memory ID to search in.
        actor_id: The actor ID to build namespace from.
        search_query: The search query to find relevant memories.
        memory_client: The memory client instance.
        namespace: Optional stack-aware namespace (if None, uses legacy format).
        cross_agent_search: Whether to search across all agents in the stack namespace.

    Returns:
        A list of memories retrieved from the memory client.
    """
    print(f'retrieving memories for memory id {memory_id} and actor {actor_id} with search query {search_query} and namespace {namespace}')
    # search the entire stack context instead of just the specific actor
    if namespace and namespace.startswith("/facts/"):
        search_namespace = namespace

    try:
        # Ensure memory exists before trying to retrieve
        logger = logging.getLogger(__name__)
        logger.info(f'retrieving memories for memory id {memory_id} and actor {actor_id} with search query {search_query} and namespace {namespace}')
        # if not ensure_memory_exists_with_strategy(memory_client, memory_id):
        #    logger.warning(f"Could not ensure memory exists: {memory_id}")
        memories = memory_client.retrieve_memories(
            memory_id=memory_id,
            namespace=search_namespace,
            query=search_query,
        )
        logger.debug(
            f"Retrieved {len(memories)} memories from namespace {search_namespace} with query '{search_query}'"
        )
        # return memories
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to retrieve memories: {e}")
        return []


def format_memory_context(memories: List[Dict[str, Any]]) -> str:
    """Format retrieved memories into a readable context string.

    Args:
        memories: List of memory objects from the memory client

    Returns:
        Formatted string containing the memory context
    """
    if not memories:
        return "No relevant memories found."

    context_parts = []
    for i, memory in enumerate(memories, 1):
        # Extract relevant information from memory object
        content = memory.get("content", "")
        metadata = memory.get("metadata", {})

        # Format the memory entry
        memory_entry = f"{i}. {content}"

        # Add metadata if available
        if metadata:
            metadata_str = ", ".join([f"{k}: {v}" for k, v in metadata.items()])
            memory_entry += f" (Metadata: {metadata_str})"

        context_parts.append(memory_entry)

    return "\n".join(context_parts)


class MemoryManager:
    """
    Unified memory manager for all AgentCore frameworks.

    Provides comprehensive memory functionality:
    1. Load previous conversation context during initialization
    2. Retrieve relevant memories before message processing
    3. Store new messages after each response
    """

    def __init__(
        self,
        default_actor_id: str = "default-user",
        default_session_id: str = "default-session",
        max_conversation_turns: int = 100,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize the MemoryManager.

        Args:
            default_actor_id: Default actor ID if none provided
            default_session_id: Default session ID if none provided
            max_conversation_turns: Maximum number of conversation turns to load
            logger: Optional logger instance
        """
        # Load memory configuration
        self.memory_config = MemoryConfig()
        self.default_actor_id = default_actor_id
        self.default_session_id = default_session_id
        self.max_conversation_turns = max_conversation_turns
        self.logger = logger or logging.getLogger(__name__)

        # Memory client
        self.memory_id = self.memory_config.memory_id
        
        # Session tracking for conversation context loading
        self._initialized_sessions: Dict[str, bool] = {}

        self.logger.info(
            f"MemoryManager initialized with memory_id: {self.memory_id}"
        )

    def get_memory_context(
        self,
        user_input: str,
        actor_id: Optional[str] = None,
        session_id: Optional[str] = None,
        load_conversation_context: bool = True,
        retrieve_relevant_memories: bool = True,
    ) -> str:
        """
        Get memory context as a string to be added to user input.

        This method retrieves conversation context and relevant memories
        and returns them as a formatted string that can be prepended to the user input.

        Args:
            user_input: Current user input/message
            actor_id: Actor identifier (uses default if None)
            session_id: Session identifier (uses default if None)
            load_conversation_context: Whether to load previous conversation context
            retrieve_relevant_memories: Whether to retrieve relevant memories

        Returns:
            Formatted memory context string
        """
        actor_id = actor_id or self.default_actor_id
        session_id = session_id or self.default_session_id
        session_key = f"{actor_id}:{session_id}"

        context_parts = []

        if load_conversation_context and not self._initialized_sessions.get(
            session_key, False
        ):
            self.logger.info(f"Loading conversation context for session: {session_key}")
            conversation_context = self._load_conversation_context(actor_id, session_id)
            if conversation_context:
                context_parts.append(f"Recent conversation:\n{conversation_context}")
                self.logger.info("Added conversation context to memory context")

            self._initialized_sessions[session_key] = True

        if retrieve_relevant_memories and user_input:
            self.logger.info("Retrieving relevant memories for user input")
            relevant_memories = retrieve_memories_for_actor(
                memory_id=self.memory_id,
                actor_id=actor_id,
                search_query=user_input,
                memory_client=self.memory_client,
                namespace=f"/facts/{session_id}",
            )
            if relevant_memories:
                memory_context = format_memory_context(relevant_memories)
                context_parts.append(
                    f"Relevant long-term memory context:\n{memory_context}"
                )
                self.logger.info(
                    f"Added {len(relevant_memories)} relevant memories to memory context"
                )
            else:
                self.logger.info("No relevant memories found")

        return "\n\n".join(context_parts) if context_parts else ""

    def store_conversation(
        self,
        user_input: str,
        response: str,
        actor_id: Optional[str] = None,
        session_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Store conversation in memory after agent response.

        Args:
            user_input: User's input/message
            response: Agent's response
            actor_id: Actor identifier (uses default if None)
            session_id: Session identifier (uses default if None)
            metadata: Optional metadata to store with the conversation

        Returns:
            True if conversation was stored successfully, False otherwise
        """
        self.logger.info(f'storing conversation for {session_id} and actor {actor_id}')
        if not user_input or not response:
            self.logger.warning(
                "Cannot store conversation: missing user_input or response"
            )
            return False

        actor_id = actor_id or self.default_actor_id
        session_id = session_id or self.default_session_id

        self.logger.debug(
            f"Storing conversation for {actor_id}:{session_id} in memory with ID {self.memory_id}"
        )

        try:
            # Ensure memory exists before trying to store
            #has_memory = True
            # ensure_memory_exists_with_strategy(
            #     self.memory_client, self.memory_config.memory_id
            # )
            # if has_memory:
            #     self.logger.warning(
            #         f"Could not ensure memory exists: {self.memory_config.memory_id}"
            #     )
            #     return False

            # Create messages in the format expected by AgentCore memory
            messages_to_store = [(user_input, "USER"), (response, "ASSISTANT")]

            
            self.memory_client.create_event(
                memory_id=self.memory_id,
                actor_id=actor_id,
                session_id=session_id,
                messages=messages_to_store,
            )
            self.logger.info("Successfully stored conversation in memory")
            return True

        except Exception as e:
            self.logger.error(
                f"Failed to store conversation in memory: {e}", exc_info=True
            )
            return False

    def _load_conversation_context(self, actor_id: str, session_id: str) -> str:
        """Load previous conversation history from memory and return as context string."""
        self.logger.debug(f"Loading conversation history for {actor_id}:{session_id}")

        try:
            # Ensure memory exists before trying to retrieve
            # if not ensure_memory_exists_with_strategy(
            #     self.memory_client, self.memory_config.memory_id
            # ):
            #     self.logger.warning(
            #         f"Could not ensure memory exists: {self.memory_config.memory_id}"
            #     )
            #     return ""

            conversations = self.memory_client.get_last_k_turns(
                memory_id=self.memory_id,
                actor_id=actor_id,
                session_id=session_id,
                k=self.max_conversation_turns,
            )
            self.logger.debug(
                f"Retrieved {len(conversations) if conversations else 0} conversation turns"
            )
        except Exception as e:
            self.logger.warning(f"Failed to retrieve conversation history: {e}")
            return ""

        if not conversations:
            return ""

        context_messages = []
        for turn in reversed(conversations):
            for message in turn:
                try:
                    role = message["role"]
                    content = message["content"]

                    if isinstance(content, str) and content.startswith("{"):
                        try:
                            parsed_content = json.loads(content)
                            content = str(parsed_content)
                        except json.JSONDecodeError:
                            pass

                    context_messages.append(f"{role}: {content}")
                except Exception as e:
                    self.logger.warning(f"Failed to process message from memory: {e}")
                    continue

        conversation_context = "\n".join(context_messages)
        self.logger.debug(
            f"Loaded conversation context from {len(context_messages)} messages"
        )
        return conversation_context

    def get_memory_statistics(self) -> Dict[str, Any]:
        """
        Get statistics about memory usage (useful for debugging/monitoring).

        Returns:
            Dictionary with memory statistics
        """
        return {
            "memory_id": self.memory_config.memory_id,
            "default_actor_id": self.default_actor_id,
            "default_session_id": self.default_session_id,
            "max_conversation_turns": self.max_conversation_turns,
            "initialized_sessions": len(self._initialized_sessions),
        }
