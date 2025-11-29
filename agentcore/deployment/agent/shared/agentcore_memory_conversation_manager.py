"""
AgentCore Memory Conversation Manager for Strands Agents.

This module provides a ConversationManager implementation that integrates with
AWS Bedrock AgentCore Memory for persistent conversation history across sessions
and process restarts.

Key Features:
- Persists conversation history to AgentCore Memory
- Retrieves conversation history when resuming sessions
- Supports multi-agent scenarios with different actor_ids
- Complements in-memory context switching for fast agent switches
- Falls back gracefully when memory is unavailable
"""

import os
import logging
from typing import Any, Dict, List, Optional
from datetime import datetime

try:
    from strands.agent.conversation_manager import ConversationManager
    from strands.types.content import Message
    STRANDS_AVAILABLE = True
except ImportError:
    STRANDS_AVAILABLE = False
    ConversationManager = object
    Message = dict

try:
    from bedrock_agentcore.memory import MemorySessionManager, MemorySession
    from bedrock_agentcore.memory.constants import ConversationalMessage, MessageRole
    MEMORY_AVAILABLE = True
except ImportError:
    MEMORY_AVAILABLE = False
    MemorySessionManager = None
    MemorySession = None

logger = logging.getLogger(__name__)


class AgentCoreMemoryConversationManager(ConversationManager):
    """
    A ConversationManager that persists conversation history to AgentCore Memory.
    
    This manager:
    1. Retrieves conversation history from AgentCore Memory when restoring a session
    2. Stores conversation turns to AgentCore Memory during apply_management
    3. Handles context reduction by summarizing and persisting to long-term memory
    
    Usage:
        manager = AgentCoreMemoryConversationManager(
            memory_id="my-memory-id",
            actor_id="user-agent",
            session_id="session-123",
            region_name="us-east-1"
        )
        
        agent = Agent(
            model=model,
            conversation_manager=manager,
            ...
        )
    """
    
    def __init__(
        self,
        memory_id: str,
        actor_id: str,
        session_id: str,
        region_name: Optional[str] = None,
        max_turns_to_retrieve: int = 10,
        auto_persist: bool = True,
        fallback_manager: Optional[ConversationManager] = None,
    ):
        """
        Initialize the AgentCore Memory Conversation Manager.
        
        Args:
            memory_id: The AgentCore Memory ID to use for persistence
            actor_id: The actor identifier (typically agent name, normalized)
            session_id: The session identifier
            region_name: AWS region (defaults to AWS_REGION env var)
            max_turns_to_retrieve: Maximum conversation turns to retrieve on restore
            auto_persist: Whether to automatically persist messages on apply_management
            fallback_manager: Optional fallback ConversationManager if memory is unavailable
        """
        if STRANDS_AVAILABLE:
            super().__init__()
        else:
            self.removed_message_count = 0
            
        self.memory_id = memory_id
        self.actor_id = actor_id
        self.session_id = session_id
        self.region_name = region_name or os.environ.get("AWS_REGION", "us-east-1")
        self.max_turns_to_retrieve = max_turns_to_retrieve
        self.auto_persist = auto_persist
        self.fallback_manager = fallback_manager
        
        # Track which messages have been persisted to avoid duplicates
        self._persisted_message_count = 0
        self._last_persisted_index = -1
        
        # Initialize memory session manager
        self._session_manager: Optional[MemorySessionManager] = None
        self._memory_session: Optional[MemorySession] = None
        self._memory_available = False
        
        self._initialize_memory()
    
    def _initialize_memory(self) -> None:
        """Initialize the AgentCore Memory session manager."""
        if not MEMORY_AVAILABLE:
            logger.warning("AgentCore Memory SDK not available, using fallback")
            return
            
        if not self.memory_id or "default" in self.memory_id.lower():
            logger.info("Skipping memory initialization for default/empty memory_id")
            return
            
        try:
            self._session_manager = MemorySessionManager(
                memory_id=self.memory_id,
                region_name=self.region_name
            )
            self._memory_session = self._session_manager.create_memory_session(
                actor_id=self.actor_id,
                session_id=self.session_id
            )
            self._memory_available = True
            logger.info(
                f"✅ AgentCoreMemoryConversationManager initialized: "
                f"memory_id={self.memory_id}, actor_id={self.actor_id}, session_id={self.session_id}"
            )
        except Exception as e:
            logger.error(f"❌ Failed to initialize AgentCore Memory: {e}")
            self._memory_available = False
    
    def restore_from_session(self, state: Dict[str, Any]) -> Optional[List[Message]]:
        """
        Restore conversation history from AgentCore Memory.
        
        This method is called when an agent is initialized with a previous session.
        It retrieves the conversation history from AgentCore Memory and returns
        messages to prepend to the agent's message list.
        
        Args:
            state: Previous state of the conversation manager
            
        Returns:
            List of messages to prepend, or None if no history available
        """
        # First, restore base state
        if STRANDS_AVAILABLE:
            try:
                if state.get("__name__") == self.__class__.__name__:
                    self.removed_message_count = state.get("removed_message_count", 0)
                    self._persisted_message_count = state.get("_persisted_message_count", 0)
                    self._last_persisted_index = state.get("_last_persisted_index", -1)
            except (ValueError, KeyError):
                pass
        
        if not self._memory_available:
            logger.info("Memory not available, skipping restore")
            if self.fallback_manager:
                return self.fallback_manager.restore_from_session(state)
            return None
        
        try:
            # Retrieve recent conversation turns from AgentCore Memory
            recent_turns = self._memory_session.get_last_k_turns(
                k=self.max_turns_to_retrieve,
                branch_name="main",
                max_results=self.max_turns_to_retrieve * 2
            )
            
            if not recent_turns:
                logger.info(f"No conversation history found for session {self.session_id}")
                return None
            
            # Convert AgentCore Memory format to strands Message format
            messages = []
            for turn in recent_turns:
                for msg in turn:
                    role = msg.get("role", "user").lower()
                    content = msg.get("content", {})
                    
                    # Extract text content
                    if isinstance(content, dict) and "text" in content:
                        text = content["text"]
                    elif isinstance(content, str):
                        text = content
                    else:
                        text = str(content)
                    
                    # Create message in strands format
                    message = {
                        "role": role,
                        "content": [{"text": text}]
                    }
                    messages.append(message)
            
            logger.info(
                f"📂 Restored {len(messages)} messages from AgentCore Memory "
                f"for actor={self.actor_id}, session={self.session_id}"
            )
            
            # Update persisted count to avoid re-persisting restored messages
            self._persisted_message_count = len(messages)
            self._last_persisted_index = len(messages) - 1
            
            return messages
            
        except Exception as e:
            logger.error(f"❌ Failed to restore from AgentCore Memory: {e}")
            if self.fallback_manager:
                return self.fallback_manager.restore_from_session(state)
            return None
    
    def get_state(self) -> Dict[str, Any]:
        """Get the current state for session persistence."""
        state = {
            "__name__": self.__class__.__name__,
            "removed_message_count": self.removed_message_count,
            "_persisted_message_count": self._persisted_message_count,
            "_last_persisted_index": self._last_persisted_index,
            "memory_id": self.memory_id,
            "actor_id": self.actor_id,
            "session_id": self.session_id,
        }
        return state
    
    def apply_management(self, agent: "Agent", **kwargs: Any) -> None:
        """
        Apply conversation management and persist new messages to AgentCore Memory.
        
        This method is called after each agent turn. It:
        1. Persists any new messages to AgentCore Memory
        2. Optionally applies the fallback manager's logic
        
        Args:
            agent: The agent whose conversation history to manage
            **kwargs: Additional arguments
        """
        if not self.auto_persist:
            return
            
        if not self._memory_available:
            if self.fallback_manager:
                self.fallback_manager.apply_management(agent, **kwargs)
            return
        
        try:
            messages = agent.messages if hasattr(agent, 'messages') else []
            
            # Find new messages that haven't been persisted yet
            new_messages_start = self._last_persisted_index + 1
            new_messages = messages[new_messages_start:] if new_messages_start < len(messages) else []
            
            if not new_messages:
                return
            
            # Convert to AgentCore Memory format and persist
            conversational_messages = []
            for msg in new_messages:
                role_str = msg.get("role", "user").lower()
                role = MessageRole.USER if role_str == "user" else MessageRole.ASSISTANT
                
                # Extract text content
                content = msg.get("content", [])
                if isinstance(content, list) and len(content) > 0:
                    if isinstance(content[0], dict) and "text" in content[0]:
                        text = content[0]["text"]
                    else:
                        text = str(content[0])
                elif isinstance(content, str):
                    text = content
                else:
                    text = str(content)
                
                # Truncate very long messages to avoid API limits
                text = text[:9000] if len(text) > 9000 else text
                
                conversational_messages.append(
                    ConversationalMessage(text, role)
                )
            
            if conversational_messages:
                self._memory_session.add_turns(messages=conversational_messages)
                self._last_persisted_index = len(messages) - 1
                self._persisted_message_count = len(messages)
                logger.debug(
                    f"💾 Persisted {len(conversational_messages)} messages to AgentCore Memory"
                )
                
        except Exception as e:
            logger.error(f"❌ Failed to persist messages to AgentCore Memory: {e}")
        
        # Apply fallback manager if configured
        if self.fallback_manager:
            self.fallback_manager.apply_management(agent, **kwargs)
    
    def reduce_context(self, agent: "Agent", e: Optional[Exception] = None, **kwargs: Any) -> None:
        """
        Reduce context when the model's context window is exceeded.
        
        This method:
        1. Delegates to the fallback manager for actual context reduction
        2. Optionally stores summarized context to long-term memory
        
        Args:
            agent: The agent whose context to reduce
            e: The exception that triggered the reduction
            **kwargs: Additional arguments
        """
        if self.fallback_manager:
            # Let the fallback manager handle the actual reduction
            self.fallback_manager.reduce_context(agent, e, **kwargs)
            
            # Update our tracking after reduction
            messages = agent.messages if hasattr(agent, 'messages') else []
            self._last_persisted_index = len(messages) - 1
            self._persisted_message_count = len(messages)
        else:
            # Simple fallback: remove oldest messages
            messages = agent.messages if hasattr(agent, 'messages') else []
            if len(messages) > 10:
                # Keep the most recent 10 messages
                removed_count = len(messages) - 10
                agent.messages = messages[-10:]
                self.removed_message_count += removed_count
                self._last_persisted_index = len(agent.messages) - 1
                self._persisted_message_count = len(agent.messages)
                logger.info(f"✂️ Reduced context by removing {removed_count} oldest messages")
    
    def update_session_info(self, actor_id: str, session_id: str) -> None:
        """
        Update the actor and session IDs (useful when switching agents).
        
        Args:
            actor_id: New actor identifier
            session_id: New session identifier
        """
        if actor_id != self.actor_id or session_id != self.session_id:
            self.actor_id = actor_id
            self.session_id = session_id
            
            # Reinitialize memory session with new IDs
            if self._session_manager:
                try:
                    self._memory_session = self._session_manager.create_memory_session(
                        actor_id=self.actor_id,
                        session_id=self.session_id
                    )
                    logger.info(
                        f"🔄 Updated memory session: actor_id={actor_id}, session_id={session_id}"
                    )
                except Exception as e:
                    logger.error(f"❌ Failed to update memory session: {e}")


def create_agentcore_memory_manager(
    memory_id: str,
    actor_id: str,
    session_id: str,
    region_name: Optional[str] = None,
    use_summarizing_fallback: bool = True,
) -> ConversationManager:
    """
    Factory function to create an AgentCore Memory Conversation Manager.
    
    This function creates a properly configured conversation manager with
    optional summarizing fallback for context window management.
    
    Args:
        memory_id: The AgentCore Memory ID
        actor_id: The actor identifier (agent name)
        session_id: The session identifier
        region_name: AWS region
        use_summarizing_fallback: Whether to use SummarizingConversationManager as fallback
        
    Returns:
        Configured ConversationManager instance
    """
    fallback = None
    
    if use_summarizing_fallback:
        try:
            from strands.agent.conversation_manager import SummarizingConversationManager
            fallback = SummarizingConversationManager(
                summary_ratio=0.3,
                preserve_recent_messages=5,
                summarization_system_prompt="Summarize the conversation context concisely."
            )
        except ImportError:
            logger.warning("SummarizingConversationManager not available")
    
    return AgentCoreMemoryConversationManager(
        memory_id=memory_id,
        actor_id=actor_id,
        session_id=session_id,
        region_name=region_name,
        fallback_manager=fallback,
    )
