import { Component, Input, Output, EventEmitter, OnInit, OnChanges, SimpleChanges } from '@angular/core';
import { AgentConfiguration, MCPServerConfig } from '../agent-management-modal/agent-management-modal.component';
import { AgentDynamoDBService, VisualizationMapping, VisualizationTemplate } from '../../services/agent-dynamodb.service';
import { BedrockService } from '../../services/bedrock.service';
import { AwsConfigService } from '../../services/aws-config.service';

/**
 * Represents a tool discovered from an MCP server via tools/list
 */
export interface MCPToolInfo {
  name: string;
  description?: string;
  inputSchema?: any;
}

/**
 * State for MCP server tool listing results
 */
export interface MCPToolListResult {
  serverId: string;
  tools: MCPToolInfo[];
  error?: string;
  loading: boolean;
  expanded: boolean;
}

/**
 * AgentEditorPanelComponent - Editor panel for creating and modifying agent configurations
 * 
 * This component provides a form-based UI for editing agent properties including:
 * - Basic info: display name, team name, description
 * - Tool agents: multi-select for tool_agent_names
 * - Model inputs: model_id, max_tokens, temperature
 * - Instructions: multi-line textarea with markdown preview + AI generation
 * - Color: color picker for agent display color
 * - Visualization mappings: template assignments with AI generation
 * 
 * Validates: Requirements 3.1, 3.2, 3.3, 3.5, 3.7, 7.1, 7.4, 7.5, 10.1
 */
@Component({
  selector: 'app-agent-editor-panel',
  templateUrl: './agent-editor-panel.component.html',
  styleUrls: ['./agent-editor-panel.component.scss']
})
export class AgentEditorPanelComponent implements OnInit, OnChanges {
  // Inputs from design document
  @Input() agent: AgentConfiguration | null = null;
  @Input() isNew: boolean = false;
  @Input() availableAgents: string[] = [];
  @Input() isLoading: boolean = false; // Validates: Requirement 9.1 - Disable form inputs during save/delete
  @Input() currentUser: string = ''; // Current user ID for author tracking
  @Input() availableRuntimeArns: string[] = []; // Existing runtime ARNs for combobox dropdown

  // Outputs from design document
  @Output() onSave = new EventEmitter<AgentConfiguration>();
  @Output() onCancel = new EventEmitter<void>();
  @Output() mcpEditorOpened = new EventEmitter<{ server: MCPServerConfig; index: number }>();
  @Output() mcpEditorClosed = new EventEmitter<void>();
  @Output() mcpEditorSaved = new EventEmitter<{ server: MCPServerConfig; index: number }>();

  // State properties from design document
  editingAgent: AgentConfiguration = this.createEmptyAgent();
  validationErrors: Map<string, string> = new Map();
  isMarkdownPreview: boolean = false;

  // Visualization mappings state
  visualizationMappings: VisualizationMapping | null = null;
  isLoadingMappings: boolean = false;
  
  // Available visualization templates
  availableTemplates: string[] = [
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

  // AI generation state
  isGeneratingInstructions: boolean = false;
  isGeneratingMappings: boolean = false;
  aiGenerationError: string | null = null;
  showInstructionsPrompt: boolean = false;
  showMappingsPrompt: boolean = false;
  instructionsPromptText: string = '';
  mappingsPromptText: string = '';

  // Visualization preview state
  showVisualizationPreview: boolean = false;
  previewTemplateId: string | null = null;
  previewSampleData: any = null;
  previewTemplateUsage: string = '';

  // Agent tools state
  newToolName: string = '';
  availableToolOptions: string[] = [
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

  // Injectable values state
  newInjectableKey: string = '';
  newInjectableValue: string = '';

  // Runtime ARN combobox state
  runtimeArnDropdownOpen: boolean = false;
  runtimeArnFilter: string = '';

  // Visualization JSON editor state
  showVisualizationJsonEditor: boolean = false;
  visualizationJsonText: string = '';
  visualizationJsonError: string | null = null;

  // MCP Server configuration state
  showMcpServerEditor: boolean = false;
  editingMcpServer: MCPServerConfig | null = null;
  editingMcpServerIndex: number = -1;
  mcpServerJsonText: string = '';
  mcpServerJsonError: string | null = null;

  // MCP Tool listing state
  mcpToolListResults: Map<string, MCPToolListResult> = new Map();

  // Preset MCP server templates for quick setup
  mcpServerPresets: { name: string; config: Partial<MCPServerConfig> }[] = [
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

  // Sample data for visualization previews
  private readonly sampleDataByTemplate: Record<string, any> = {
    'adcp_get_products-visualization': {
      visualizationType: 'adcp_get_products',
      templateId: 'adcp_get_products-visualization',
      title: 'Sample Product Inventory',
      products: [
        { name: 'Premium Video - Sports', reach: 2500000, price: 45.00, format: 'Video', audience: 'Sports Enthusiasts' },
        { name: 'Display Banner - News', reach: 5000000, price: 12.50, format: 'Display', audience: 'News Readers' },
        { name: 'Native Content - Lifestyle', reach: 1800000, price: 28.00, format: 'Native', audience: 'Lifestyle Seekers' },
        { name: 'Audio Spot - Podcast', reach: 800000, price: 18.00, format: 'Audio', audience: 'Podcast Listeners' }
      ]
    },
    'allocations-visualization': {
      visualizationType: 'allocations',
      templateId: 'allocations-visualization',
      title: 'Budget Allocation Preview',
      allocations: [
        { channel: 'Digital Video', percentage: 35, budget: 350000, color: '#6842ff' },
        { channel: 'Display', percentage: 25, budget: 250000, color: '#c300e0' },
        { channel: 'Social Media', percentage: 20, budget: 200000, color: '#ff6200' },
        { channel: 'Search', percentage: 15, budget: 150000, color: '#007e94' },
        { channel: 'Audio', percentage: 5, budget: 50000, color: '#22c55e' }
      ]
    },
    'bar-chart-visualization': {
      visualizationType: 'bar-chart',
      templateId: 'bar-chart-visualization',
      title: 'Performance Comparison',
      data: [
        { label: 'Campaign A', value: 85, color: '#6842ff' },
        { label: 'Campaign B', value: 72, color: '#c300e0' },
        { label: 'Campaign C', value: 91, color: '#ff6200' },
        { label: 'Campaign D', value: 68, color: '#007e94' }
      ],
      xAxisLabel: 'Campaigns',
      yAxisLabel: 'Performance Score'
    },
    'channels-visualization': {
      visualizationType: 'channels',
      templateId: 'channels-visualization',
      title: 'Channel Performance',
      channels: [
        { name: 'CTV', impressions: 1200000, clicks: 24000, ctr: 2.0, spend: 45000 },
        { name: 'Mobile', impressions: 3500000, clicks: 52500, ctr: 1.5, spend: 28000 },
        { name: 'Desktop', impressions: 2100000, clicks: 37800, ctr: 1.8, spend: 32000 },
        { name: 'Tablet', impressions: 800000, clicks: 12000, ctr: 1.5, spend: 15000 }
      ]
    },
    'creative-visualization': {
      visualizationType: 'creative',
      templateId: 'creative-visualization',
      title: 'Creative Assets',
      creatives: [
        { name: 'Hero Banner 1', format: '300x250', status: 'Active', impressions: 450000, ctr: 2.1 },
        { name: 'Video Pre-roll', format: '1920x1080', status: 'Active', impressions: 280000, ctr: 3.5 },
        { name: 'Native Card', format: '1200x628', status: 'Pending', impressions: 0, ctr: 0 }
      ]
    },
    'decision-tree-visualization': {
      visualizationType: 'decision-tree',
      templateId: 'decision-tree-visualization',
      title: 'Decision Flow',
      nodes: [
        { id: 'root', label: 'Start', type: 'start' },
        { id: 'check1', label: 'Budget > $50K?', type: 'decision', parent: 'root' },
        { id: 'yes1', label: 'Premium Inventory', type: 'action', parent: 'check1' },
        { id: 'no1', label: 'Standard Inventory', type: 'action', parent: 'check1' }
      ]
    },
    'donut-chart-visualization': {
      visualizationType: 'donut-chart',
      templateId: 'donut-chart-visualization',
      title: 'Audience Distribution',
      segments: [
        { label: 'Ages 18-24', value: 22, color: '#6842ff' },
        { label: 'Ages 25-34', value: 35, color: '#c300e0' },
        { label: 'Ages 35-44', value: 25, color: '#ff6200' },
        { label: 'Ages 45+', value: 18, color: '#007e94' }
      ]
    },
    'double-histogram-visualization': {
      visualizationType: 'double-histogram',
      templateId: 'double-histogram-visualization',
      title: 'Before vs After Comparison',
      series1: { label: 'Before', data: [12, 25, 38, 45, 32, 18], color: '#6842ff' },
      series2: { label: 'After', data: [18, 32, 48, 52, 41, 28], color: '#ff6200' },
      labels: ['Week 1', 'Week 2', 'Week 3', 'Week 4', 'Week 5', 'Week 6']
    },
    'histogram-visualization': {
      visualizationType: 'histogram',
      templateId: 'histogram-visualization',
      title: 'Frequency Distribution',
      data: [5, 12, 28, 45, 62, 48, 35, 22, 15, 8],
      labels: ['0-10', '10-20', '20-30', '30-40', '40-50', '50-60', '60-70', '70-80', '80-90', '90-100'],
      xAxisLabel: 'Score Range',
      yAxisLabel: 'Frequency'
    },
    'metrics-visualization': {
      visualizationType: 'metrics',
      templateId: 'metrics-visualization',
      title: 'Campaign Metrics',
      metrics: [
        { label: 'Impressions', value: '12.5M', change: '+15%', trend: 'up' },
        { label: 'Clicks', value: '245K', change: '+8%', trend: 'up' },
        { label: 'CTR', value: '1.96%', change: '+0.12%', trend: 'up' },
        { label: 'Spend', value: '$125K', change: '-5%', trend: 'down' },
        { label: 'CPC', value: '$0.51', change: '-12%', trend: 'down' },
        { label: 'Conversions', value: '8.2K', change: '+22%', trend: 'up' }
      ]
    },
    'segments-visualization': {
      visualizationType: 'segments',
      templateId: 'segments-visualization',
      title: 'Audience Segments',
      segments: [
        { name: 'High-Value Shoppers', size: 2500000, match_rate: 85, affinity: 'High' },
        { name: 'Sports Enthusiasts', size: 4200000, match_rate: 72, affinity: 'Medium' },
        { name: 'Tech Early Adopters', size: 1800000, match_rate: 91, affinity: 'High' },
        { name: 'Travel Intenders', size: 3100000, match_rate: 68, affinity: 'Medium' }
      ]
    },
    'timeline-visualization': {
      visualizationType: 'timeline',
      templateId: 'timeline-visualization',
      title: 'Campaign Timeline',
      events: [
        { date: '2026-01-15', label: 'Campaign Launch', type: 'milestone' },
        { date: '2026-02-01', label: 'Mid-Flight Optimization', type: 'action' },
        { date: '2026-02-15', label: 'Creative Refresh', type: 'action' },
        { date: '2026-03-01', label: 'Campaign End', type: 'milestone' }
      ]
    }
  };

  // Preset brand colors from frontend-styles.md
  presetColors: string[] = [
    '#6842ff', // Purple (primary)
    '#491782', // Purple dark
    '#7a42a9', // Purple gradient start
    '#c300e0', // Fuchsia
    '#df51a9', // Fuchsia light
    '#be51ff', // Fuchsia purple
    '#ff6200', // Orange
    '#fda83b', // Orange light
    '#ffc675', // Orange pale
    '#007e94', // Teal
    '#22c55e', // Green
    '#3b82f6', // Blue
    '#ef4444', // Red
    '#f59e0b', // Amber
    '#8b5cf6', // Violet
    '#ec4899'  // Pink
  ];

  constructor(
    private agentDynamoDBService: AgentDynamoDBService,
    private bedrockService: BedrockService,
    private awsConfigService: AwsConfigService
  ) {}

  ngOnInit(): void {
    this.initializeForm();
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['agent']) {
      this.initializeForm();
    }
  }

  /**
   * Initialize form with agent data or empty defaults
   */
  private initializeForm(): void {
    if (this.agent) {
      // Deep clone the agent to avoid mutating the original
      this.editingAgent = JSON.parse(JSON.stringify(this.agent));
      
      // Ensure model_inputs has at least a default entry
      if (!this.editingAgent.model_inputs || Object.keys(this.editingAgent.model_inputs).length === 0) {
        this.editingAgent.model_inputs = {
          default: {
            model_id: 'anthropic.claude-3-5-sonnet-20241022-v2:0',
            max_tokens: 8000,
            temperature: 0.3
          }
        };
      }
      
      // Load visualization mappings for existing agents
      if (!this.isNew && this.agent.agent_name) {
        this.loadVisualizationMappings(this.agent.agent_name);
      }
    } else {
      this.editingAgent = this.createEmptyAgent();
      this.visualizationMappings = null;
    }
    
    // Clear validation errors when form is initialized
    this.validationErrors.clear();
    this.isMarkdownPreview = false;
    this.aiGenerationError = null;
    this.showInstructionsPrompt = false;
    this.showMappingsPrompt = false;
    this.instructionsPromptText = '';
    this.mappingsPromptText = '';
    
    // Reset agent tools and injectable values input state
    this.newToolName = '';
    this.newInjectableKey = '';
    this.newInjectableValue = '';
    
    // Reset JSON editor state
    this.showVisualizationJsonEditor = false;
    this.visualizationJsonText = '';
    this.visualizationJsonError = null;
    
    // Reset MCP server editor state
    this.showMcpServerEditor = false;
    this.editingMcpServer = null;
    this.editingMcpServerIndex = -1;
    this.mcpServerJsonText = '';
    this.mcpServerJsonError = null;

    // Reset runtime ARN combobox state
    this.runtimeArnDropdownOpen = false;
    this.runtimeArnFilter = '';
  }

  /**
   * Load visualization mappings for an agent from DynamoDB
   */
  private async loadVisualizationMappings(agentName: string): Promise<void> {
    this.isLoadingMappings = true;
    try {
      const mappings = await this.agentDynamoDBService.getVisualizationMappings(agentName);
      this.visualizationMappings = mappings || {
        agentName: agentName,
        agentId: this.editingAgent.agent_id || agentName,
        templates: []
      };
    } catch (error) {
      console.error('Error loading visualization mappings:', error);
      this.visualizationMappings = {
        agentName: agentName,
        agentId: this.editingAgent.agent_id || agentName,
        templates: []
      };
    } finally {
      this.isLoadingMappings = false;
    }
  }

  /**
   * Creates an empty agent configuration with default values
   * Validates: Requirement 4.3 - Default values for optional fields
   */
  private createEmptyAgent(): AgentConfiguration {
    return {
      agent_id: '',
      agent_name: '',
      agent_display_name: '',
      team_name: '',
      agent_description: '',
      tool_agent_names: [],
      external_agents: [],
      model_inputs: {
        default: {
          model_id: 'anthropic.claude-3-5-sonnet-20241022-v2:0',
          max_tokens: 8000,
          temperature: 0.3
        }
      },
      agent_tools: [],
      injectable_values: {},
      instructions: '',
      color: '#6842ff',
      mcp_servers: [],
      runtime_arn: ''
    };
  }

  /**
   * Validates all form fields
   * Validates: Requirements 3.3, 3.5, 4.2, 9.4
   * @returns true if all validations pass
   */
  validate(): boolean {
    this.validationErrors.clear();

    // Required field validations
    if (!this.editingAgent.agent_display_name?.trim()) {
      this.validationErrors.set('agent_display_name', 'Display name is required');
    } else if (this.editingAgent.agent_display_name.length > 128) {
      this.validationErrors.set('agent_display_name', 'Display name must be 128 characters or less');
    }

    if (!this.editingAgent.team_name?.trim()) {
      this.validationErrors.set('team_name', 'Team name is required');
    } else if (this.editingAgent.team_name.length > 128) {
      this.validationErrors.set('team_name', 'Team name must be 128 characters or less');
    }

    if (!this.editingAgent.agent_description?.trim()) {
      this.validationErrors.set('agent_description', 'Description is required');
    } else if (this.editingAgent.agent_description.length > 1024) {
      this.validationErrors.set('agent_description', 'Description must be 1024 characters or less');
    }

    // For new agents, validate agent_id and agent_name
    if (this.isNew) {
      if (!this.editingAgent.agent_id?.trim()) {
        this.validationErrors.set('agent_id', 'Agent ID is required for new agents');
      } else if (!/^[A-Za-z][A-Za-z0-9_]*$/.test(this.editingAgent.agent_id)) {
        this.validationErrors.set('agent_id', 'Agent ID must start with a letter and contain only letters, numbers, and underscores');
      } else if (this.editingAgent.agent_id.length > 64) {
        this.validationErrors.set('agent_id', 'Agent ID must be 64 characters or less');
      }

      if (!this.editingAgent.agent_name?.trim()) {
        this.validationErrors.set('agent_name', 'Agent name is required for new agents');
      } else if (!/^[A-Za-z][A-Za-z0-9_]*$/.test(this.editingAgent.agent_name)) {
        this.validationErrors.set('agent_name', 'Agent name must start with a letter and contain only letters, numbers, and underscores');
      } else if (this.editingAgent.agent_name.length > 64) {
        this.validationErrors.set('agent_name', 'Agent name must be 64 characters or less');
      }
    }

    // Model inputs validation
    const modelInputs = this.getDefaultModelInputs();
    if (modelInputs) {
      if (!modelInputs.model_id?.trim()) {
        this.validationErrors.set('model_id', 'Model ID is required');
      }

      if (modelInputs.max_tokens === undefined || modelInputs.max_tokens === null) {
        this.validationErrors.set('max_tokens', 'Max tokens is required');
      } else if (modelInputs.max_tokens < 100 || modelInputs.max_tokens > 200000) {
        this.validationErrors.set('max_tokens', 'Max tokens must be between 100 and 200,000');
      }

      if (modelInputs.temperature === undefined || modelInputs.temperature === null) {
        this.validationErrors.set('temperature', 'Temperature is required');
      } else if (modelInputs.temperature < 0 || modelInputs.temperature > 1) {
        this.validationErrors.set('temperature', 'Temperature must be between 0 and 1');
      }
    }

    return this.validationErrors.size === 0;
  }

  /**
   * Resets the form to initial state
   */
  resetForm(): void {
    this.initializeForm();
  }

  /**
   * Toggles between edit and markdown preview mode for instructions
   * Validates: Requirement 7.4 - Markdown preview toggle
   */
  toggleMarkdownPreview(): void {
    this.isMarkdownPreview = !this.isMarkdownPreview;
  }

  /**
   * Handles cancel button click
   */
  handleCancel(): void {
    this.onCancel.emit();
  }

  /**
   * Gets the default model inputs (first entry or 'default' key)
   */
  getDefaultModelInputs(): { model_id: string; max_tokens: number; temperature: number; top_p?: number } | null {
    if (!this.editingAgent.model_inputs) return null;
    
    // Try to get 'default' key first, then first available key
    if (this.editingAgent.model_inputs['default']) {
      return this.editingAgent.model_inputs['default'];
    }
    
    const keys = Object.keys(this.editingAgent.model_inputs);
    if (keys.length > 0) {
      return this.editingAgent.model_inputs[keys[0]];
    }
    
    return null;
  }

  /**
   * Updates model input value
   */
  updateModelInput(field: 'model_id' | 'max_tokens' | 'temperature', value: string | number): void {
    if (!this.editingAgent.model_inputs) {
      this.editingAgent.model_inputs = {
        default: {
          model_id: 'anthropic.claude-3-5-sonnet-20241022-v2:0',
          max_tokens: 8000,
          temperature: 0.3
        }
      };
    }

    // Get the key to update (prefer 'default', otherwise first key)
    let key = 'default';
    if (!this.editingAgent.model_inputs['default']) {
      const keys = Object.keys(this.editingAgent.model_inputs);
      if (keys.length > 0) {
        key = keys[0];
      } else {
        this.editingAgent.model_inputs['default'] = {
          model_id: 'anthropic.claude-3-5-sonnet-20241022-v2:0',
          max_tokens: 8000,
          temperature: 0.3
        };
      }
    }

    (this.editingAgent.model_inputs[key] as any)[field] = value;
  }

  /**
   * Checks if a tool agent is selected
   */
  isToolAgentSelected(agentName: string): boolean {
    return this.editingAgent.tool_agent_names?.includes(agentName) || false;
  }

  /**
   * Toggles a tool agent selection
   */
  toggleToolAgent(agentName: string): void {
    if (!this.editingAgent.tool_agent_names) {
      this.editingAgent.tool_agent_names = [];
    }

    const index = this.editingAgent.tool_agent_names.indexOf(agentName);
    if (index === -1) {
      this.editingAgent.tool_agent_names.push(agentName);
    } else {
      this.editingAgent.tool_agent_names.splice(index, 1);
    }
  }

  /**
   * Selects a color from the preset palette
   */
  selectColor(color: string): void {
    this.editingAgent.color = color;
  }

  /**
   * Checks if a color is currently selected
   */
  isColorSelected(color: string): boolean {
    return this.editingAgent.color === color;
  }

  /**
   * Auto-generates agent_name from agent_id when agent_id changes
   * This helps users by automatically filling in the agent_name field
   * Validates: Requirement 4.4 - Generate unique agent_id if not provided
   */
  onAgentIdChange(value: string): void {
    this.editingAgent.agent_id = value;
    
    // Auto-fill agent_name if it's empty or matches the previous agent_id pattern
    if (!this.editingAgent.agent_name || this.editingAgent.agent_name === '') {
      this.editingAgent.agent_name = value;
    }
  }

  /**
   * Auto-generates agent_id from display name when display name changes (for new agents)
   * Validates: Requirement 4.4 - Generate unique agent_id if not provided
   */
  onDisplayNameChange(value: string): void {
    this.editingAgent.agent_display_name = value;
    
    // For new agents, auto-generate agent_id and agent_name from display name if they're empty
    if (this.isNew && (!this.editingAgent.agent_id || this.editingAgent.agent_id === '')) {
      const generatedId = this.generateAgentIdFromDisplayName(value);
      this.editingAgent.agent_id = generatedId;
      this.editingAgent.agent_name = generatedId;
    }
  }

  /**
   * Generates a valid agent ID from a display name
   * Converts "My Custom Agent" to "MyCustomAgent"
   */
  private generateAgentIdFromDisplayName(displayName: string): string {
    if (!displayName?.trim()) {
      return '';
    }
    
    // Convert display name to PascalCase without special characters
    return displayName
      .trim()
      .replace(/[^a-zA-Z0-9\s]/g, '') // Remove special characters
      .split(/\s+/) // Split by whitespace
      .map(word => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase()) // Capitalize each word
      .join(''); // Join without spaces
  }

  /**
   * Gets validation error for a field
   */
  getError(field: string): string | undefined {
    return this.validationErrors.get(field);
  }

  /**
   * Checks if a field has an error
   */
  hasError(field: string): boolean {
    return this.validationErrors.has(field);
  }

  /**
   * Gets filtered available agents (excludes current agent)
   */
  getFilteredAvailableAgents(): string[] {
    return this.availableAgents.filter(name => name !== this.editingAgent.agent_name);
  }

  // ============================================
  // Agent Tools Methods
  // ============================================

  /**
   * Add a new tool to agent_tools array
   */
  addAgentTool(toolName?: string): void {
    const tool = toolName || this.newToolName.trim();
    if (!tool) return;
    
    if (!this.editingAgent.agent_tools) {
      this.editingAgent.agent_tools = [];
    }
    
    if (!this.editingAgent.agent_tools.includes(tool)) {
      this.editingAgent.agent_tools.push(tool);
    }
    
    this.newToolName = '';
  }

  /**
   * Remove a tool from agent_tools array
   */
  removeAgentTool(index: number): void {
    if (this.editingAgent.agent_tools) {
      this.editingAgent.agent_tools.splice(index, 1);
    }
  }

  /**
   * Check if a tool is already added
   */
  isToolAdded(toolName: string): boolean {
    return this.editingAgent.agent_tools?.includes(toolName) || false;
  }

  /**
   * Get available tools that haven't been added yet
   */
  getAvailableTools(): string[] {
    return this.availableToolOptions.filter(tool => !this.isToolAdded(tool));
  }

  // ============================================
  // Injectable Values Methods
  // ============================================

  /**
   * Add a new injectable value
   */
  addInjectableValue(): void {
    const key = this.newInjectableKey.trim();
    const value = this.newInjectableValue.trim();
    
    if (!key) return;
    
    if (!this.editingAgent.injectable_values) {
      this.editingAgent.injectable_values = {};
    }
    
    this.editingAgent.injectable_values[key] = value;
    this.newInjectableKey = '';
    this.newInjectableValue = '';
  }

  /**
   * Remove an injectable value
   */
  removeInjectableValue(key: string): void {
    if (this.editingAgent.injectable_values) {
      delete this.editingAgent.injectable_values[key];
    }
  }

  /**
   * Update an injectable value
   */
  updateInjectableValue(key: string, value: string): void {
    if (this.editingAgent.injectable_values) {
      this.editingAgent.injectable_values[key] = value;
    }
  }

  /**
   * Get injectable values as array for iteration
   */
  getInjectableValuesArray(): { key: string; value: string }[] {
    if (!this.editingAgent.injectable_values) return [];
    return Object.entries(this.editingAgent.injectable_values).map(([key, value]) => ({ key, value }));
  }

  // ============================================
  // Visualization Mapping Methods
  // ============================================

  /**
   * Add a new visualization template mapping
   */
  addVisualizationTemplate(): void {
    if (!this.visualizationMappings) {
      this.visualizationMappings = {
        agentName: this.editingAgent.agent_name || '',
        agentId: this.editingAgent.agent_id || '',
        templates: []
      };
    }
    
    this.visualizationMappings.templates.push({
      templateId: '',
      usage: ''
    });
  }

  /**
   * Remove a visualization template mapping
   */
  removeVisualizationTemplate(index: number): void {
    if (this.visualizationMappings?.templates) {
      this.visualizationMappings.templates.splice(index, 1);
    }
  }

  /**
   * Update a visualization template mapping
   */
  updateVisualizationTemplate(index: number, field: 'templateId' | 'usage', value: string): void {
    if (this.visualizationMappings?.templates[index]) {
      this.visualizationMappings.templates[index][field] = value;
    }
  }

  /**
   * Save visualization mappings to DynamoDB
   */
  async saveVisualizationMappings(): Promise<boolean> {
    if (!this.visualizationMappings || !this.editingAgent.agent_name) {
      return false;
    }
    
    // Update agent name/id in mappings
    this.visualizationMappings.agentName = this.editingAgent.agent_name;
    this.visualizationMappings.agentId = this.editingAgent.agent_id || this.editingAgent.agent_name;
    
    try {
      return await this.agentDynamoDBService.saveVisualizationMappings(
        this.editingAgent.agent_name,
        this.visualizationMappings
      );
    } catch (error) {
      console.error('Error saving visualization mappings:', error);
      return false;
    }
  }

  // ============================================
  // Visualization JSON Editor Methods
  // ============================================

  /**
   * Open the JSON editor for visualization mappings
   */
  openVisualizationJsonEditor(): void {
    if (!this.visualizationMappings) {
      this.visualizationMappings = {
        agentName: this.editingAgent.agent_name || '',
        agentId: this.editingAgent.agent_id || '',
        templates: []
      };
    }
    this.visualizationJsonText = JSON.stringify(this.visualizationMappings, null, 2);
    this.visualizationJsonError = null;
    this.showVisualizationJsonEditor = true;
  }

  /**
   * Close the JSON editor
   */
  closeVisualizationJsonEditor(): void {
    this.showVisualizationJsonEditor = false;
    this.visualizationJsonText = '';
    this.visualizationJsonError = null;
  }

  /**
   * Apply changes from JSON editor
   */
  applyVisualizationJson(): void {
    try {
      const parsed = JSON.parse(this.visualizationJsonText);
      
      // Validate structure
      if (!parsed.agentName || !parsed.agentId || !Array.isArray(parsed.templates)) {
        throw new Error('Invalid structure. Required: agentName, agentId, templates[]');
      }
      
      // Validate templates
      for (const template of parsed.templates) {
        if (!template.templateId || typeof template.templateId !== 'string') {
          throw new Error('Each template must have a templateId string');
        }
      }
      
      this.visualizationMappings = parsed;
      this.visualizationJsonError = null;
      this.closeVisualizationJsonEditor();
    } catch (error: any) {
      this.visualizationJsonError = error.message || 'Invalid JSON';
    }
  }

  /**
   * Format the JSON in the editor
   */
  formatVisualizationJson(): void {
    try {
      const parsed = JSON.parse(this.visualizationJsonText);
      this.visualizationJsonText = JSON.stringify(parsed, null, 2);
      this.visualizationJsonError = null;
    } catch (error: any) {
      this.visualizationJsonError = 'Cannot format: Invalid JSON';
    }
  }

  // ============================================
  // MCP Server Configuration Methods
  // ============================================

  /**
   * Add a new MCP server configuration
   */
  addMcpServer(preset?: { name: string; config: Partial<MCPServerConfig> }): void {
    if (!this.editingAgent.mcp_servers) {
      this.editingAgent.mcp_servers = [];
    }
    
    const newServer: MCPServerConfig = {
      id: this.generateMcpServerId(),
      name: preset?.name || 'New MCP Server',
      transport: preset?.config?.transport || 'stdio',
      command: preset?.config?.command || '',
      args: preset?.config?.args || [],
      url: preset?.config?.url || '',
      env: {},
      prefix: '',
      allowedTools: [],
      rejectedTools: [],
      enabled: true,
      description: preset?.config?.description || '',
      awsAuth: preset?.config?.awsAuth
    };
    
    this.editingAgent.mcp_servers.push(newServer);
    
    // Open editor for the new server
    this.openMcpServerEditor(this.editingAgent.mcp_servers.length - 1);
  }

  /**
   * Generate a unique ID for an MCP server
   */
  private generateMcpServerId(): string {
    return `mcp_${Date.now().toString(36)}_${Math.random().toString(36).substr(2, 5)}`;
  }

  /**
   * Remove an MCP server configuration
   */
  removeMcpServer(index: number): void {
    if (this.editingAgent.mcp_servers) {
      this.editingAgent.mcp_servers.splice(index, 1);
    }
  }

  /**
   * Toggle MCP server enabled state
   */
  toggleMcpServerEnabled(index: number): void {
    if (this.editingAgent.mcp_servers?.[index]) {
      this.editingAgent.mcp_servers[index].enabled = !this.editingAgent.mcp_servers[index].enabled;
    }
  }

  /**
   * Open the MCP server editor modal
   */
  openMcpServerEditor(index: number): void {
    if (!this.editingAgent.mcp_servers?.[index]) {
      return;
    }
    
    this.editingMcpServerIndex = index;
    this.editingMcpServer = JSON.parse(JSON.stringify(this.editingAgent.mcp_servers[index]));
    this.mcpServerJsonText = JSON.stringify(this.editingMcpServer, null, 2);
    this.mcpServerJsonError = null;
    this.showMcpServerEditor = true;

    // Emit to parent so it can render the editor outside the scrollable content
    this.mcpEditorOpened.emit({ server: this.editingMcpServer!, index });
  }

  /**
   * Close the MCP server editor modal
   */
  closeMcpServerEditor(): void {
    this.showMcpServerEditor = false;
    this.editingMcpServer = null;
    this.editingMcpServerIndex = -1;
    this.mcpServerJsonText = '';
    this.mcpServerJsonError = null;
    this.mcpEditorClosed.emit();
  }

  /**
   * Save changes from the MCP server editor
   */
  saveMcpServerChanges(): void {
    if (!this.editingMcpServer || this.editingMcpServerIndex < 0) {
      return;
    }
    
    // Validate required fields based on transport type
    if (!this.editingMcpServer.name?.trim()) {
      this.mcpServerJsonError = 'Server name is required';
      return;
    }
    
    if (this.editingMcpServer.transport === 'stdio') {
      if (!this.editingMcpServer.command?.trim()) {
        this.mcpServerJsonError = 'Command is required for stdio transport';
        return;
      }
    } else if (this.editingMcpServer.transport === 'http' || this.editingMcpServer.transport === 'sse') {
      if (!this.editingMcpServer.url?.trim()) {
        this.mcpServerJsonError = 'URL is required for HTTP/SSE transport';
        return;
      }
    }
    
    if (!this.editingAgent.mcp_servers) {
      this.editingAgent.mcp_servers = [];
    }
    
    this.editingAgent.mcp_servers[this.editingMcpServerIndex] = this.editingMcpServer;
    this.closeMcpServerEditor();
  }

  /**
   * Apply JSON changes to the MCP server being edited
   */
  applyMcpServerJson(): void {
    try {
      const parsed = JSON.parse(this.mcpServerJsonText);
      
      // Validate structure
      if (!parsed.id || !parsed.name || !parsed.transport) {
        throw new Error('Invalid structure. Required: id, name, transport');
      }
      
      if (!['stdio', 'http', 'sse'].includes(parsed.transport)) {
        throw new Error('Transport must be one of: stdio, http, sse');
      }
      
      this.editingMcpServer = parsed;
      this.mcpServerJsonError = null;
    } catch (error: any) {
      this.mcpServerJsonError = error.message || 'Invalid JSON';
    }
  }

  /**
   * Format the MCP server JSON in the editor
   */
  formatMcpServerJson(): void {
    try {
      const parsed = JSON.parse(this.mcpServerJsonText);
      this.mcpServerJsonText = JSON.stringify(parsed, null, 2);
      this.mcpServerJsonError = null;
    } catch (error: any) {
      this.mcpServerJsonError = 'Cannot format: Invalid JSON';
    }
  }

  /**
   * Update MCP server JSON text when form fields change
   */
  updateMcpServerJsonFromForm(): void {
    if (this.editingMcpServer) {
      this.mcpServerJsonText = JSON.stringify(this.editingMcpServer, null, 2);
    }
  }

  /**
   * Add an argument to the MCP server command
   */
  addMcpServerArg(arg: string): void {
    if (!arg?.trim() || !this.editingMcpServer) {
      return;
    }
    
    if (!this.editingMcpServer.args) {
      this.editingMcpServer.args = [];
    }
    
    this.editingMcpServer.args.push(arg.trim());
    this.updateMcpServerJsonFromForm();
  }

  /**
   * Remove an argument from the MCP server command
   */
  removeMcpServerArg(index: number): void {
    if (this.editingMcpServer?.args) {
      this.editingMcpServer.args.splice(index, 1);
      this.updateMcpServerJsonFromForm();
    }
  }

  /**
   * Add an environment variable to the MCP server
   */
  addMcpServerEnv(key: string, value: string): void {
    if (!key?.trim() || !this.editingMcpServer) {
      return;
    }
    
    if (!this.editingMcpServer.env) {
      this.editingMcpServer.env = {};
    }
    
    this.editingMcpServer.env[key.trim()] = value;
    this.updateMcpServerJsonFromForm();
  }

  /**
   * Remove an environment variable from the MCP server
   */
  removeMcpServerEnv(key: string): void {
    if (this.editingMcpServer?.env) {
      delete this.editingMcpServer.env[key];
      this.updateMcpServerJsonFromForm();
    }
  }

  /**
   * Get environment variables as array for iteration
   */
  getMcpServerEnvArray(): { key: string; value: string }[] {
    if (!this.editingMcpServer?.env) return [];
    return Object.entries(this.editingMcpServer.env).map(([key, value]) => ({ key, value }));
  }

  /**
   * Add an HTTP header to the MCP server (for authentication)
   */
  addMcpServerHeader(key: string, value: string): void {
    if (!key?.trim() || !this.editingMcpServer) {
      return;
    }
    
    if (!this.editingMcpServer.headers) {
      this.editingMcpServer.headers = {};
    }
    
    this.editingMcpServer.headers[key.trim()] = value;
    this.updateMcpServerJsonFromForm();
  }

  /**
   * Remove an HTTP header from the MCP server
   */
  removeMcpServerHeader(key: string): void {
    if (this.editingMcpServer?.headers) {
      delete this.editingMcpServer.headers[key];
      this.updateMcpServerJsonFromForm();
    }
  }

  /**
   * Get HTTP headers as array for iteration
   */
  getMcpServerHeadersArray(): { key: string; value: string }[] {
    if (!this.editingMcpServer?.headers) return [];
    return Object.entries(this.editingMcpServer.headers).map(([key, value]) => ({ key, value }));
  }

  /**
   * Get transport icon for display
   */
  getMcpTransportIcon(transport: string): string {
    switch (transport) {
      case 'stdio': return 'terminal';
      case 'http': return 'http';
      case 'sse': return 'stream';
      default: return 'extension';
    }
  }

  /**
   * Get transport display name
   */
  getMcpTransportName(transport: string): string {
    switch (transport) {
      case 'stdio': return 'Command Line (stdio)';
      case 'http': return 'HTTP';
      case 'sse': return 'Server-Sent Events';
      default: return transport;
    }
  }

  // ============================================
  // MCP Tool Listing Methods
  // ============================================

  /**
   * Get the tool list result for a given MCP server
   */
  getMcpToolListResult(serverId: string): MCPToolListResult | undefined {
    return this.mcpToolListResults.get(serverId);
  }

  /**
   * Toggle the expanded state of tool list results for a server
   */
  toggleMcpToolList(serverId: string): void {
    const result = this.mcpToolListResults.get(serverId);
    if (result) {
      result.expanded = !result.expanded;
    }
  }

  /**
   * List tools from an MCP server by sending a JSON-RPC tools/list request.
   * Supports HTTP and SSE transports. For AWS IAM authenticated endpoints,
   * uses SigV4 signing via the AWS SDK credentials.
   */
  async listMcpServerTools(server: MCPServerConfig, event?: Event): Promise<void> {
    if (event) {
      event.stopPropagation();
    }

    // Only HTTP and SSE transports support remote tool listing
    if (server.transport === 'stdio') {
      this.mcpToolListResults.set(server.id, {
        serverId: server.id,
        tools: [],
        error: 'Tool listing is only available for HTTP and SSE transport servers. stdio servers require a local runtime.',
        loading: false,
        expanded: true
      });
      return;
    }

    if (!server.url?.trim()) {
      this.mcpToolListResults.set(server.id, {
        serverId: server.id,
        tools: [],
        error: 'No URL configured for this server.',
        loading: false,
        expanded: true
      });
      return;
    }

    // Set loading state
    this.mcpToolListResults.set(server.id, {
      serverId: server.id,
      tools: [],
      loading: true,
      expanded: true
    });

    try {
      const tools = await this.fetchMcpTools(server);
      this.mcpToolListResults.set(server.id, {
        serverId: server.id,
        tools,
        loading: false,
        expanded: true
      });
    } catch (error: any) {
      console.error('Error listing MCP tools:', error);
      this.mcpToolListResults.set(server.id, {
        serverId: server.id,
        tools: [],
        error: error.message || 'Failed to connect to MCP server.',
        loading: false,
        expanded: true
      });
    }
  }

  /**
   * Fetch tools from an MCP server endpoint using the JSON-RPC protocol.
   * Handles both plain HTTP and AWS IAM SigV4 authenticated requests.
   */
  private async fetchMcpTools(server: MCPServerConfig): Promise<MCPToolInfo[]> {
    const url = server.url!;

    // Build the JSON-RPC request for tools/list
    const jsonRpcBody = JSON.stringify({
      jsonrpc: '2.0',
      id: 1,
      method: 'tools/list',
      params: {}
    });

    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      'Accept': 'application/json'
    };

    // Add custom headers from server config (e.g., Bearer token auth)
    if (server.headers) {
      Object.assign(headers, server.headers);
    }

    let response: Response;

    if (server.awsAuth) {
      // Use SigV4 signing for AWS IAM authenticated endpoints
      response = await this.fetchWithSigV4(url, jsonRpcBody, headers, server.awsAuth);
    } else {
      // Plain HTTP request
      response = await fetch(url, {
        method: 'POST',
        headers,
        body: jsonRpcBody
      });
    }

    if (!response.ok) {
      const errorText = await response.text().catch(() => '');
      throw new Error(`HTTP ${response.status}: ${response.statusText}${errorText ? ' - ' + errorText.substring(0, 200) : ''}`);
    }

    const responseText = await response.text();
    let data: any;

    try {
      data = JSON.parse(responseText);
    } catch {
      throw new Error('Invalid JSON response from MCP server');
    }

    // Handle JSON-RPC error response
    if (data.error) {
      throw new Error(`MCP error: ${data.error.message || JSON.stringify(data.error)}`);
    }

    // Extract tools from the response
    const tools: MCPToolInfo[] = (data.result?.tools || data.tools || []).map((tool: any) => ({
      name: tool.name,
      description: tool.description || '',
      inputSchema: tool.inputSchema
    }));

    return tools;
  }

  /**
   * Make a SigV4-signed HTTP request to an AWS IAM authenticated endpoint.
   * Uses the current user's Amplify credentials for signing.
   */
  private async fetchWithSigV4(
    url: string,
    body: string,
    headers: Record<string, string>,
    awsAuth: { region: string; service: string }
  ): Promise<Response> {
    // Dynamically import SigV4 signing utilities
    const { Sha256 } = await import('@aws-crypto/sha256-js');
    const { SignatureV4 } = await import('@smithy/signature-v4');
    const { HttpRequest } = await import('@smithy/protocol-http');

    // Get current AWS credentials from Amplify
    const awsConfig = await this.awsConfigService.getAwsConfig();
    if (!awsConfig?.credentials) {
      throw new Error('AWS credentials not available. Please sign in again.');
    }

    const parsedUrl = new URL(url);

    // Build the HTTP request for signing
    const request = new HttpRequest({
      method: 'POST',
      protocol: parsedUrl.protocol,
      hostname: parsedUrl.hostname,
      port: parsedUrl.port ? parseInt(parsedUrl.port) : undefined,
      path: parsedUrl.pathname,
      query: Object.fromEntries(parsedUrl.searchParams.entries()),
      headers: {
        ...headers,
        host: parsedUrl.hostname
      },
      body
    });

    // Sign the request with SigV4
    const signer = new SignatureV4({
      credentials: awsConfig.credentials,
      region: awsAuth.region,
      service: awsAuth.service,
      sha256: Sha256
    });

    const signedRequest = await signer.sign(request);

    // Execute the signed request using fetch
    const signedHeaders: Record<string, string> = {};
    for (const [key, value] of Object.entries(signedRequest.headers)) {
      signedHeaders[key] = value as string;
    }

    return fetch(url, {
      method: 'POST',
      headers: signedHeaders,
      body
    });
  }

  /**
   * Dismiss/clear tool list results for a server
   */
  dismissMcpToolList(serverId: string, event?: Event): void {
    if (event) {
      event.stopPropagation();
    }
    this.mcpToolListResults.delete(serverId);
  }

  // ============================================
  // AI Generation Methods
  // ============================================

  /**
   * Show the instructions generation prompt dialog
   */
  showGenerateInstructionsDialog(): void {
    this.showInstructionsPrompt = true;
    this.instructionsPromptText = '';
    this.aiGenerationError = null;
  }

  /**
   * Hide the instructions generation prompt dialog
   */
  hideGenerateInstructionsDialog(): void {
    this.showInstructionsPrompt = false;
    this.instructionsPromptText = '';
  }

  /**
   * Generate agent instructions using Claude Opus 4.5
   */
  async generateInstructions(): Promise<void> {
    this.isGeneratingInstructions = true;
    this.aiGenerationError = null;
    
    try {
      const hasExistingInstructions = this.editingAgent.instructions && this.editingAgent.instructions.trim().length > 0;
      
      let prompt = `You are an expert at creating agent system prompts for AI agents in an advertising technology platform.

Agent Details:
- Display Name: ${this.editingAgent.agent_display_name || 'Not specified'}
- Description: ${this.editingAgent.agent_description || 'Not specified'}
- Team: ${this.editingAgent.team_name || 'Not specified'}
- Tool Agents Available: ${this.editingAgent.tool_agent_names?.join(', ') || 'None'}

`;

      if (hasExistingInstructions) {
        prompt += `Current Instructions:
${this.editingAgent.instructions}

User's Requested Changes:
${this.instructionsPromptText || 'Improve and enhance the existing instructions'}

Please update the instructions based on the user's request while maintaining the core functionality. Return ONLY the updated instructions text, no explanations.`;
      } else {
        prompt += `User's Requirements:
${this.instructionsPromptText || 'Create comprehensive instructions for this agent based on its description'}

Please generate comprehensive system instructions for this agent. The instructions should:
1. Define the agent's role and responsibilities
2. Specify how it should interact with users
3. Outline its capabilities and limitations
4. Include any relevant domain knowledge for advertising technology
5. Define output formats and response styles

Return ONLY the instructions text, no explanations or preamble.`;
      }

      const generatedText = await this.bedrockService.invokeClaudeOpus(prompt);
      this.editingAgent.instructions = generatedText.trim();
      this.hideGenerateInstructionsDialog();
      
    } catch (error: any) {
      console.error('Error generating instructions:', error);
      this.aiGenerationError = error.message || 'Failed to generate instructions. Please try again.';
    } finally {
      this.isGeneratingInstructions = false;
    }
  }

  /**
   * Show the visualization mappings generation prompt dialog
   */
  showGenerateMappingsDialog(): void {
    this.showMappingsPrompt = true;
    this.mappingsPromptText = '';
    this.aiGenerationError = null;
  }

  /**
   * Hide the visualization mappings generation prompt dialog
   */
  hideGenerateMappingsDialog(): void {
    this.showMappingsPrompt = false;
    this.mappingsPromptText = '';
  }

  /**
   * Generate visualization mappings using Claude Opus 4.5
   */
  async generateVisualizationMappings(): Promise<void> {
    this.isGeneratingMappings = true;
    this.aiGenerationError = null;
    
    try {
      const hasExistingMappings = this.visualizationMappings?.templates && this.visualizationMappings.templates.length > 0;
      
      let prompt = `You are an expert at configuring visualization mappings for AI agents in an advertising technology platform.

Agent Details:
- Name: ${this.editingAgent.agent_name || 'Not specified'}
- Display Name: ${this.editingAgent.agent_display_name || 'Not specified'}
- Description: ${this.editingAgent.agent_description || 'Not specified'}
- Instructions Summary: ${(this.editingAgent.instructions || '').substring(0, 500)}...

Available Visualization Templates:
${this.availableTemplates.map(t => `- ${t}`).join('\n')}

Template Descriptions:
- adcp_get_products-visualization: Displays product inventory with pricing, reach, and audience data
- allocations-visualization: Shows budget allocation across channels/publishers
- bar-chart-visualization: Generic bar chart for comparing values
- channels-visualization: Channel performance and distribution
- creative-visualization: Creative assets and variations display
- decision-tree-visualization: Decision flow and logic trees
- donut-chart-visualization: Proportional data visualization
- double-histogram-visualization: Comparative histogram data
- histogram-visualization: Distribution data visualization
- metrics-visualization: KPIs and performance metrics
- segments-visualization: Audience segment analysis
- timeline-visualization: Temporal data and milestones

`;

      if (hasExistingMappings) {
        prompt += `Current Mappings:
${JSON.stringify(this.visualizationMappings?.templates, null, 2)}

User's Requested Changes:
${this.mappingsPromptText || 'Improve and optimize the visualization mappings'}

Please update the mappings based on the user's request. Return ONLY a JSON array of template mappings in this format:
[{"templateId": "template-name", "usage": "Description of when to use this visualization"}]`;
      } else {
        prompt += `User's Requirements:
${this.mappingsPromptText || 'Suggest appropriate visualizations based on the agent description'}

Based on the agent's purpose and capabilities, suggest appropriate visualization templates. Return ONLY a JSON array of template mappings in this format:
[{"templateId": "template-name", "usage": "Description of when to use this visualization"}]

Select 2-5 most relevant templates for this agent.`;
      }

      const generatedText = await this.bedrockService.invokeClaudeOpus(prompt, 2000);
      
      // Parse the JSON response
      const jsonMatch = generatedText.match(/\[[\s\S]*\]/);
      if (jsonMatch) {
        const templates: VisualizationTemplate[] = JSON.parse(jsonMatch[0]);
        
        if (!this.visualizationMappings) {
          this.visualizationMappings = {
            agentName: this.editingAgent.agent_name || '',
            agentId: this.editingAgent.agent_id || '',
            templates: []
          };
        }
        
        this.visualizationMappings.templates = templates;
      } else {
        throw new Error('Could not parse visualization mappings from response');
      }
      
      this.hideGenerateMappingsDialog();
      
    } catch (error: any) {
      console.error('Error generating visualization mappings:', error);
      this.aiGenerationError = error.message || 'Failed to generate visualization mappings. Please try again.';
    } finally {
      this.isGeneratingMappings = false;
    }
  }

  /**
   * Handles save button click - also saves visualization mappings
   */
  handleSave(): void {
    if (this.validate()) {
      // Save visualization mappings if they exist
      if (this.visualizationMappings && this.editingAgent.agent_name) {
        this.saveVisualizationMappings();
      }
      this.onSave.emit(this.editingAgent);
    }
  }

  // ============================================
  // Visualization Preview Methods
  // ============================================

  /**
   * Open visualization preview for a specific template
   */
  openVisualizationPreview(templateId: string, usage: string): void {
    if (!templateId) {
      return;
    }
    
    this.previewTemplateId = templateId;
    this.previewTemplateUsage = usage || 'No usage description provided';
    this.previewSampleData = this.sampleDataByTemplate[templateId] || this.generateGenericSampleData(templateId);
    this.showVisualizationPreview = true;
  }

  /**
   * Close visualization preview
   */
  closeVisualizationPreview(): void {
    this.showVisualizationPreview = false;
    this.previewTemplateId = null;
    this.previewSampleData = null;
    this.previewTemplateUsage = '';
  }

  /**
   * Generate generic sample data for unknown templates
   */
  private generateGenericSampleData(templateId: string): any {
    return {
      visualizationType: templateId.replace('-visualization', ''),
      templateId: templateId,
      title: `Preview: ${templateId}`,
      message: 'Sample data for this visualization template',
      data: [
        { label: 'Item 1', value: 100 },
        { label: 'Item 2', value: 75 },
        { label: 'Item 3', value: 50 }
      ]
    };
  }

  /**
   * Get a friendly display name for a template
   */
  getTemplateDisplayName(templateId: string): string {
    if (!templateId) return 'Unknown';
    
    return templateId
      .replace('-visualization', '')
      .split(/[-_]/)
      .map(word => word.charAt(0).toUpperCase() + word.slice(1))
      .join(' ');
  }

  // Runtime ARN combobox methods
  getFilteredRuntimeArns(): string[] {
    if (!this.runtimeArnFilter) return this.availableRuntimeArns;
    const filter = this.runtimeArnFilter.toLowerCase();
    return this.availableRuntimeArns.filter(arn => arn.toLowerCase().includes(filter));
  }

  toggleRuntimeArnDropdown(): void {
    this.runtimeArnDropdownOpen = !this.runtimeArnDropdownOpen;
    this.runtimeArnFilter = '';
  }

  selectRuntimeArn(arn: string): void {
    this.editingAgent.runtime_arn = arn;
    this.runtimeArnDropdownOpen = false;
    this.runtimeArnFilter = '';
  }

  onRuntimeArnInput(value: string): void {
    this.editingAgent.runtime_arn = value;
    this.runtimeArnFilter = value;
    this.runtimeArnDropdownOpen = true;
  }

  clearRuntimeArn(): void {
    this.editingAgent.runtime_arn = '';
    this.runtimeArnFilter = '';
    this.runtimeArnDropdownOpen = false;
  }

  closeRuntimeArnDropdown(): void {
    // Small delay to allow click events on dropdown items to fire first
    setTimeout(() => {
      this.runtimeArnDropdownOpen = false;
    }, 200);
  }
}
