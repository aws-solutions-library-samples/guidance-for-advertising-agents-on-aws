import os
import json
import logging
from typing import Optional, Dict, Any
from bedrock_agentcore.memory import MemoryClient
from datetime import datetime
from strands.hooks import (
    AgentInitializedEvent,
    HookProvider,
    HookRegistry,
    MessageAddedEvent,
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ShortTermMemoryHook(HookProvider):
    def __init__(
        self,
        memory_client: MemoryClient,
        memory_id: str,
        actor_id: str,
        session_id: str,
        logger: Optional[logging.Logger] = None,
        region: Optional[str] = None,
    ):
        self.logger = logger or logging.getLogger(__name__)
        self.region = region or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")

        self.logger.info(
            f"Initializing ShortTermMemoryHook with memory_id: {memory_id}"
        )
        self.memory_client = memory_client
        self.memory_id = memory_id
        self.actor_id = actor_id
        self.session_id = session_id

    def on_agent_initialized(self, event: AgentInitializedEvent):
        """Load recent conversation history when agent starts"""
        try:
            # Get session info from agent state
            actor_id = event.agent.state.get("actor_id")
            session_id = event.agent.state.get("session_id")

            if not actor_id or not session_id:
                logger.warning("Missing actor_id or session_id in agent state")
                return

            # Get last 5 conversation turns
            recent_turns = self.memory_client.get_last_k_turns(
                memory_id=self.memory_id,
                actor_id=actor_id,
                session_id=session_id,
                k=5,
                branch_name="main",
                max_results=10
            )

            if recent_turns:
                # Format conversation history for context
                context_messages = []
                for turn in recent_turns:
                    for message in turn:
                        role = message["role"].lower()
                        content = message["content"]["text"]
                        if "'toolResult'" not in content: 
                            context_messages.append(f"{role.title()}: {content}")

                context = "\n".join(context_messages)
                logger.info(f"Context from memory: {context}")

                # Add context to agent's system prompt
                event.agent.system_prompt += f"\n\Conversation history:\n{context}\n\nContinue the conversation naturally based on this context."

                logger.info(f"✅ Loaded {len(recent_turns)} recent conversation turns")
            else:
                logger.info("No previous conversation history found")

        except Exception as e:
            logger.error(f"Failed to load conversation history via memory hook: {e}")

    def on_message_added(self, event: MessageAddedEvent):
        """Store conversation turns in memory"""
        messages = event.agent.messages
        try:
            # Get session info from agent state
            actor_id = event.agent.state.get("actor_id")
            session_id = event.agent.state.get("session_id")

            if not actor_id or not session_id:
                logger.warning("Missing actor_id or session_id in agent state")
                return
            logger.info(
                f"Storing message in memory: {self.memory_id} for session {session_id} and actor {actor_id}"
            )
            logger.info(f"event: {event}")

            # Safely extract message content with proper error handling
            try:
                if not messages:
                    logger.warning("No messages provided to store")
                    return

                last_message = messages[-1]

                # Handle different message content structures
                if (
                    isinstance(last_message.get("content"), list)
                    and len(last_message["content"]) > 0
                ):
                    # Content is a list of objects with text property
                    content_item = last_message["content"][0]
                    if isinstance(content_item, dict) and "text" in content_item:
                        text_content = content_item["text"]
                    else:
                        # Content item might be a string or have different structure
                        text_content = str(content_item)
                elif isinstance(last_message.get("content"), str):
                    # Content is directly a string
                    text_content = last_message["content"]
                else:
                    # Fallback: convert whatever content we have to string
                    text_content = str(last_message.get("content", ""))

                role = last_message.get("role", "user")
                formatted_messages = [(text_content[-9000:], role)]

            except (KeyError, IndexError, TypeError) as e:
                logger.error(f"Could not extract message content: {e}")
                logger.error(
                    f"Message structure: {last_message if 'last_message' in locals() else 'N/A'}"
                )
                return

            self.memory_client.create_event(
                memory_id=self.memory_id,
                actor_id=actor_id,
                session_id=session_id,
                messages=formatted_messages,
            )

        except Exception as e:
            logger.error(f"Failed to store message: {e}")

    def register_hooks(self, registry: HookRegistry) -> None:
        # Register memory hooks
        registry.add_callback(MessageAddedEvent, self.on_message_added)
        # Uncomment the below when not using the AgentCoreMemoryConversationManager
        # registry.add_callback(AgentInitializedEvent, self.on_agent_initialized)
        