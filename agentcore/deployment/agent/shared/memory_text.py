"""
Text that a conversation turn contributes to AgentCore Memory.

A strands message holds a list of content blocks. Only ``text`` blocks are
conversation; ``toolUse``, ``toolResult`` and ``reasoningContent`` blocks are
the agent's working and are not stored. This module has no third-party
imports so it can be unit tested without strands or bedrock_agentcore.
"""

from typing import Any, Optional


def persistable_text(message: Any) -> Optional[str]:
    """
    Return the message's text blocks joined with blank lines.

    Returns None when the message has no text to store: it holds only tool
    use, tool result or reasoning blocks, or its text is empty.
    """
    if not isinstance(message, dict):
        return None

    content = message.get("content")
    if isinstance(content, str):
        return content.strip() or None
    if not isinstance(content, list):
        return None

    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        text = block.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())

    joined = "\n\n".join(parts)
    return joined or None
