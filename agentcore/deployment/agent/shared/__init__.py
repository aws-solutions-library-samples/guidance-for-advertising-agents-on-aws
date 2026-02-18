"""
Shared utilities for AgentCore agents
"""

from .external_agent_tools import (
    ExternalAgentToolRegistry,
    ExternalAgentInvoker
)

from .runtime_resolver import RuntimeARNResolver

from .memory import MemoryManager, MemoryConfig

from .knowledge_base_helper import (
    KnowledgeBaseHelper,
    KnowledgeBaseResult,
    KnowledgeBaseSource,
    knowledge_base_helper,
    setup_agent_knowledge_base,
    get_knowledge_base_tool,
    enhance_agent_response_with_kb,
    format_kb_result_with_sources,
    list_available_knowledge_bases
)

__all__ = [
    'VisualizationHelper',
    'get_agent_visualization_prompt_injection', 
    'inject_visualizations_into_prompt',
    'get_visualization_tool_for_agent',
    'create_visualization_agent_tool',
    'create_generic_visualization_tool',
    'ExternalAgentToolRegistry',
    'ExternalAgentInvoker',
    'RuntimeARNResolver',
    'MemoryManager',
    'MemoryConfig',
    'KnowledgeBaseHelper',
    'KnowledgeBaseResult',
    'KnowledgeBaseSource',
    'knowledge_base_helper',
    'setup_agent_knowledge_base',
    'get_knowledge_base_tool',
    'enhance_agent_response_with_kb',
    'format_kb_result_with_sources',
    'list_available_knowledge_bases'
]