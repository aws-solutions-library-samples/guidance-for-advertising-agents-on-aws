import { MCPServerConfig } from '../agent-management-modal/agent-management-modal.component';

/**
 * Static data extracted from AgentEditorPanelComponent to reduce component size
 * and prevent large inline objects from being re-evaluated during change detection.
 */

/** Represents a tool discovered from an MCP server via tools/list */
export interface MCPToolInfo {
  name: string;
  description?: string;
  inputSchema?: any;
}

/** State for MCP server tool listing results */
export interface MCPToolListResult {
  serverId: string;
  tools: MCPToolInfo[];
  error?: string;
  loading: boolean;
  expanded: boolean;
}

export const AVAILABLE_TEMPLATES: string[] = [
  'adcp_get_products-visualization',
  'allocations-visualization',
  'bar-chart-visualization',
  'channels-visualization',
  'creative-visualization',
  'decision-tree-visualization',
  'donut-chart-visualization',
  'double-histogram-visualization',
  'histogram-visualization',
  'metrics-visualization',
  'segments-visualization',
  'timeline-visualization'
];

export const AVAILABLE_TOOL_OPTIONS: string[] = [
  'invoke_specialist',
  'invoke_specialist_with_RAG',
  'retrieve_knowledge_base_results_tool',
  'lookup_events',
  'http_request',
  'get_products',
  'create_media_buy',
  'get_media_buy_delivery',
  'get_signals',
  'activate_signal',
  'generate_image_from_descriptions',
  'file_read'
];

export const PRESET_COLORS: string[] = [
  '#6842ff', '#491782', '#7a42a9', '#c300e0',
  '#df51a9', '#be51ff', '#ff6200', '#fda83b',
  '#ffc675', '#007e94', '#22c55e', '#3b82f6',
  '#ef4444', '#f59e0b', '#8b5cf6', '#ec4899'
];

export const MCP_SERVER_PRESETS: { name: string; config: Partial<MCPServerConfig> }[] = [
  {
    name: 'AWS Documentation',
    config: {
      transport: 'stdio',
      command: 'uvx',
      args: ['awslabs.aws-documentation-mcp-server@latest'],
      description: 'Search and read AWS documentation'
    }
  },
  {
    name: 'Bedrock KB Retrieval',
    config: {
      transport: 'stdio',
      command: 'uvx',
      args: ['awslabs.bedrock-kb-retrieval-mcp-server@latest'],
      description: 'Retrieve from Bedrock Knowledge Bases'
    }
  },
  {
    name: 'Custom HTTP Server',
    config: {
      transport: 'http',
      url: 'http://localhost:8000/mcp',
      description: 'Custom HTTP-based MCP server'
    }
  },
  {
    name: 'Custom SSE Server',
    config: {
      transport: 'sse',
      url: 'http://localhost:8000/sse',
      description: 'Custom SSE-based MCP server'
    }
  },
  {
    name: 'AWS IAM Gateway',
    config: {
      transport: 'http',
      url: '',
      awsAuth: {
        region: 'us-east-1',
        service: 'bedrock-agentcore'
      },
      description: 'AWS IAM authenticated MCP gateway'
    }
  }
];
