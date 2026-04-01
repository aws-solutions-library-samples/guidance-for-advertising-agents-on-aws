import { Component, Input, Output, EventEmitter, OnInit, OnChanges, SimpleChanges, ChangeDetectorRef, ChangeDetectionStrategy } from '@angular/core';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { AgentConfiguration, MCPServerConfig, ExternalAgentConfig } from '../agent-management-modal/agent-management-modal.component';
import { KnowledgeBaseInfo } from '../../models/application-models';
import { AgentDynamoDBService, VisualizationMapping } from '../../services/agent-dynamodb.service';
import { BedrockService } from '../../services/bedrock.service';
import { AwsConfigService } from '../../services/aws-config.service';
import { marked } from 'marked';

// Extracted modules
import {
  MCPToolInfo, MCPToolListResult,
  PRESET_COLORS, AVAILABLE_TEMPLATES, AVAILABLE_TOOL_OPTIONS,
  MCP_SERVER_PRESETS
} from './agent-editor-panel.constants';
import { SAMPLE_DATA_BY_TEMPLATE } from './agent-editor-panel.sample-data';
import {
  generateMcpServerId, getMcpTransportIcon, getMcpTransportName,
  listMcpServerTools as listMcpServerToolsHelper
} from './agent-editor-mcp.helpers';
import {
  generateInstructionsText, generateVisualizationMappingsText,
  AttachedDocument
} from './agent-editor-ai.helpers';

// Re-export interfaces for consumers
export { MCPToolInfo, MCPToolListResult } from './agent-editor-panel.constants';

@Component({
  selector: 'app-agent-editor-panel',
  templateUrl: './agent-editor-panel.component.html',
  styleUrls: ['./agent-editor-panel.component.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class AgentEditorPanelComponent implements OnInit, OnChanges {
  @Input() agent: AgentConfiguration | null = null;
  @Input() isNew: boolean = false;
  @Input() availableAgents: string[] = [];
  @Input() isLoading: boolean = false;
  @Input() currentUser: string = '';
  @Input() availableRuntimeArns: string[] = [];
  @Input() defaultRuntimeArn: string = '';

  @Output() onSave = new EventEmitter<AgentConfiguration>();
  @Output() onCancel = new EventEmitter<void>();
  @Output() onDelete = new EventEmitter<AgentConfiguration>();
  @Output() mcpEditorOpened = new EventEmitter<{ server: MCPServerConfig; index: number }>();
  @Output() mcpEditorClosed = new EventEmitter<void>();
  @Output() mcpEditorSaved = new EventEmitter<{ server: MCPServerConfig; index: number }>();

  // Core state
  editingAgent: AgentConfiguration = this.createEmptyAgent();
  validationErrors: Map<string, string> = new Map();
  isMarkdownPreview: boolean = false;

  // Visualization mappings state
  visualizationMappings: VisualizationMapping | null = null;
  isLoadingMappings: boolean = false;

  // Constants exposed to template
  availableTemplates = AVAILABLE_TEMPLATES;
  availableToolOptions = AVAILABLE_TOOL_OPTIONS;
  presetColors = PRESET_COLORS;
  mcpServerPresets = MCP_SERVER_PRESETS;

  // AI generation state
  isGeneratingInstructions: boolean = false;
  isGeneratingMappings: boolean = false;
  aiGenerationError: string | null = null;
  showInstructionsPrompt: boolean = false;
  showMappingsPrompt: boolean = false;
  instructionsPromptText: string = '';
  mappingsPromptText: string = '';

  // Document attachment state for AI generation
  instructionsAttachedDocs: AttachedDocument[] = [];
  mappingsAttachedDocs: AttachedDocument[] = [];

  // Visualization preview state
  showVisualizationPreview: boolean = false;
  previewTemplateId: string | null = null;
  previewSampleData: any = null;
  previewTemplateUsage: string = '';

  // Markdown preview cache
  renderedInstructionsHtml: SafeHtml | null = null;
  private _lastRenderedInstructions: string = '';

  // Agent tools state
  newToolName: string = '';

  // Injectable values state
  newInjectableKey: string = '';
  newInjectableValue: string = '';
  injectableValuesCache: { key: string; value: string }[] = [];

  // Delete confirmation state
  showDeleteConfirm: boolean = false;

  // Runtime ARN combobox state
  runtimeArnDropdownOpen: boolean = false;
  runtimeArnFilter: string = '';

  // Knowledge Base typeahead state
  knowledgeBases: KnowledgeBaseInfo[] = [];
  filteredKnowledgeBases: KnowledgeBaseInfo[] = [];
  isLoadingKnowledgeBases: boolean = false;
  kbDropdownOpen: boolean = false;
  kbFilterText: string = '';
  kbNotFound: boolean = false;

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
  mcpToolListResults: Map<string, MCPToolListResult> = new Map();

  // OAuth token input state (never persisted — only used during editing)
  mcpBearerTokenValue: string = '';
  mcpBearerTokenSaving: boolean = false;
  mcpBearerTokenPending: boolean = false;
  mcpBearerTokenEditing: boolean = false;
  mcpBearerTokenVisible: boolean = false;

  // A2A External Agent editor state
  showA2aAgentEditor: boolean = false;
  editingA2aAgent: ExternalAgentConfig | null = null;
  editingA2aAgentIndex: number = -1;
  a2aEditorError: string | null = null;
  a2aBearerTokenValue: string = '';
  a2aBearerTokenSaving: boolean = false;
  a2aBearerTokenPending: boolean = false;
  a2aBearerTokenEditing: boolean = false;
  a2aBearerTokenVisible: boolean = false;

  // A2A OAuth credentials state (username/password)
  a2aOAuthUsername: string = '';
  a2aOAuthPassword: string = '';
  a2aOAuthPasswordVisible: boolean = false;
  a2aOAuthCredentialsSaving: boolean = false;
  a2aOAuthCredentialsPending: boolean = false;
  a2aOAuthCredentialsEditing: boolean = false;

  // MCP OAuth credentials state (username/password)
  mcpOAuthUsername: string = '';
  mcpOAuthPassword: string = '';
  mcpOAuthPasswordVisible: boolean = false;
  mcpOAuthCredentialsSaving: boolean = false;
  mcpOAuthCredentialsPending: boolean = false;
  mcpOAuthCredentialsEditing: boolean = false;

  @Output() a2aEditorOpened = new EventEmitter<{ agent: ExternalAgentConfig; index: number }>();
  @Output() a2aEditorClosed = new EventEmitter<void>();
  @Output() a2aEditorSaved = new EventEmitter<{ agent: ExternalAgentConfig; index: number }>();

  constructor(
    private agentDynamoDBService: AgentDynamoDBService,
    private bedrockService: BedrockService,
    private awsConfigService: AwsConfigService,
    private sanitizer: DomSanitizer,
    private cdr: ChangeDetectorRef
  ) {}

  ngOnInit(): void {
    this.initializeForm();
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['agent']) {
      this.initializeForm();
    }
  }

  // ============================================
  // Form Initialization & Validation
  // ============================================

  private initializeForm(): void {
    if (this.agent) {
      this.editingAgent = JSON.parse(JSON.stringify(this.agent));
      this.editingAgent.agent_id = this.editingAgent.agent_id || '';
      this.editingAgent.agent_name = this.editingAgent.agent_name || '';
      this.editingAgent.agent_display_name = this.editingAgent.agent_display_name || '';
      this.editingAgent.team_name = this.editingAgent.team_name || '';
      this.editingAgent.agent_description = this.editingAgent.agent_description || '';
      this.editingAgent.tool_agent_names = this.editingAgent.tool_agent_names || [];
      this.editingAgent.external_agents = this.editingAgent.external_agents || [];
      this.editingAgent.agent_tools = this.editingAgent.agent_tools || [];
      this.editingAgent.color = this.editingAgent.color || '#6842ff';
      this.editingAgent.injectable_values = this.editingAgent.injectable_values || {};
      this.editingAgent.mcp_servers = this.editingAgent.mcp_servers || [];
      this.editingAgent.external_agent_configs = this.editingAgent.external_agent_configs || [];
      this.editingAgent.is_a2a = this.editingAgent.is_a2a ?? false;
      this.editingAgent.a2a_auth_type = this.editingAgent.a2a_auth_type || 'none';
      this.editingAgent.runtime_arn = this.editingAgent.runtime_arn || '';
      this.editingAgent.knowledge_base = this.editingAgent.knowledge_base || '';
      this.editingAgent.instructions = this.editingAgent.instructions || '';

      if (!this.editingAgent.model_inputs || Object.keys(this.editingAgent.model_inputs).length === 0) {
        this.editingAgent.model_inputs = {
          default: { model_id: 'global.anthropic.claude-sonnet-4-5-20250929-v1:0', max_tokens: 8000, temperature: 0.3 }
        };
      }

      if (!this.isNew && this.agent.agent_name) {
        this.loadVisualizationMappings(this.agent.agent_name);
      }
    } else {
      this.editingAgent = this.createEmptyAgent();
      this.visualizationMappings = null;
    }

    this.validationErrors.clear();
    this.isMarkdownPreview = false;
    this.renderedInstructionsHtml = null;
    this._lastRenderedInstructions = '';
    this.aiGenerationError = null;
    this.showInstructionsPrompt = false;
    this.showMappingsPrompt = false;
    this.instructionsPromptText = '';
    this.mappingsPromptText = '';
    this.newToolName = '';
    this.newInjectableKey = '';
    this.newInjectableValue = '';
    this.refreshInjectableValuesCache();
    this.showVisualizationJsonEditor = false;
    this.visualizationJsonText = '';
    this.visualizationJsonError = null;
    this.showMcpServerEditor = false;
    this.editingMcpServer = null;
    this.editingMcpServerIndex = -1;
    this.mcpServerJsonText = '';
    this.mcpServerJsonError = null;
    this.showA2aAgentEditor = false;
    this.editingA2aAgent = null;
    this.editingA2aAgentIndex = -1;
    this.a2aEditorError = null;
    this.runtimeArnDropdownOpen = false;
    this.runtimeArnFilter = '';
    this.kbDropdownOpen = false;
    this.kbFilterText = '';
    this.loadKnowledgeBases();
    this.cdr.markForCheck();
  }

  private async loadVisualizationMappings(agentName: string): Promise<void> {
    this.isLoadingMappings = true;
    try {
      const mappings = await this.agentDynamoDBService.getVisualizationMappings(agentName);
      this.visualizationMappings = mappings || {
        agentName, agentId: this.editingAgent.agent_id || agentName, templates: []
      };
    } catch (error) {
      console.error('Error loading visualization mappings:', error);
      this.visualizationMappings = {
        agentName, agentId: this.editingAgent.agent_id || agentName, templates: []
      };
    } finally {
      this.isLoadingMappings = false;
      this.cdr.markForCheck();
    }
  }

  private createEmptyAgent(): AgentConfiguration {
    return {
      agent_id: '', agent_name: '', agent_display_name: '', team_name: '',
      agent_description: '', tool_agent_names: [], external_agents: [],
      model_inputs: {
        default: { model_id: 'global.anthropic.claude-sonnet-4-5-20250929-v1:0', max_tokens: 8000, temperature: 0.3 }
      },
      agent_tools: [], injectable_values: {}, instructions: '', color: '#6842ff',
      mcp_servers: [], external_agent_configs: [], runtime_arn: '', knowledge_base: '',
      is_a2a: false,
      a2a_auth_type: 'none'
    };
  }

  validate(): boolean {
    this.validationErrors.clear();

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

    // Validate external A2A agent ARNs are non-empty
    if (this.editingAgent.external_agent_configs?.length) {
      const emptyArnAgents = this.editingAgent.external_agent_configs
        .filter(agent => !agent.arn?.trim());
      if (emptyArnAgents.length > 0) {
        this.validationErrors.set('external_agent_arn', `${emptyArnAgents.length} external A2A agent(s) missing ARN`);
      }
    }

    return this.validationErrors.size === 0;
  }

  resetForm(): void { this.initializeForm(); }

  getError(field: string): string | undefined { return this.validationErrors.get(field); }
  hasError(field: string): boolean { return this.validationErrors.has(field); }

  // ============================================
  // Markdown Preview
  // ============================================

  toggleMarkdownPreview(): void {
    this.isMarkdownPreview = !this.isMarkdownPreview;
    if (this.isMarkdownPreview) {
      this.renderInstructionsMarkdown();
    }
  }

  private renderInstructionsMarkdown(): void {
    const instructions = this.editingAgent.instructions || '';
    if (instructions === this._lastRenderedInstructions && this.renderedInstructionsHtml) return;
    try {
      const html = marked.parse(instructions, { async: false }) as string;
      this.renderedInstructionsHtml = this.sanitizer.bypassSecurityTrustHtml(html);
      this._lastRenderedInstructions = instructions;
    } catch (e) {
      console.error('Error rendering markdown:', e);
      this.renderedInstructionsHtml = this.sanitizer.bypassSecurityTrustHtml(
        `<pre style="white-space:pre-wrap;word-break:break-word;">${this.escapeHtml(instructions)}</pre>`
      );
      this._lastRenderedInstructions = instructions;
    }
  }

  private escapeHtml(text: string): string {
    return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // ============================================
  // Model & Basic Field Methods
  // ============================================

  handleCancel(): void { this.onCancel.emit(); }
  requestDelete(): void {
    this.showDeleteConfirm = true;
  }

  confirmDeleteAgent(): void {
    this.showDeleteConfirm = false;
    this.onDelete.emit(this.editingAgent);
  }

  cancelDeleteAgent(): void {
    this.showDeleteConfirm = false;
  }

  getDefaultModelInputs(): { model_id: string; max_tokens: number; temperature: number; top_p?: number } | null {
    if (!this.editingAgent.model_inputs) return null;
    if (this.editingAgent.model_inputs['default']) return this.editingAgent.model_inputs['default'];
    const keys = Object.keys(this.editingAgent.model_inputs);
    return keys.length > 0 ? this.editingAgent.model_inputs[keys[0]] : null;
  }

  updateModelInput(field: 'model_id' | 'max_tokens' | 'temperature', value: string | number): void {
    if (!this.editingAgent.model_inputs) {
      this.editingAgent.model_inputs = {
        default: { model_id: 'global.anthropic.claude-sonnet-4-5-20250929-v1:0', max_tokens: 8000, temperature: 0.3 }
      };
    }
    let key = 'default';
    if (!this.editingAgent.model_inputs['default']) {
      const keys = Object.keys(this.editingAgent.model_inputs);
      if (keys.length > 0) { key = keys[0]; }
      else { this.editingAgent.model_inputs['default'] = { model_id: 'global.anthropic.claude-sonnet-4-5-20250929-v1:0', max_tokens: 8000, temperature: 0.3 }; }
    }
    (this.editingAgent.model_inputs[key] as any)[field] = value;
  }

  isToolAgentSelected(agentName: string): boolean {
    return this.editingAgent.tool_agent_names?.includes(agentName) || false;
  }

  toggleToolAgent(agentName: string): void {
    if (!this.editingAgent.tool_agent_names) this.editingAgent.tool_agent_names = [];
    const index = this.editingAgent.tool_agent_names.indexOf(agentName);
    if (index === -1) { this.editingAgent.tool_agent_names.push(agentName); }
    else { this.editingAgent.tool_agent_names.splice(index, 1); }
  }

  selectColor(color: string): void { this.editingAgent.color = color; }
  isColorSelected(color: string): boolean { return this.editingAgent.color === color; }

  onAgentIdChange(value: string): void {
    this.editingAgent.agent_id = value;
    if (!this.editingAgent.agent_name || this.editingAgent.agent_name === '') {
      this.editingAgent.agent_name = value;
    }
  }

  onDisplayNameChange(value: string): void {
    this.editingAgent.agent_display_name = value;
    if (this.isNew && (!this.editingAgent.agent_id || this.editingAgent.agent_id === '')) {
      const generatedId = this.generateAgentIdFromDisplayName(value);
      this.editingAgent.agent_id = generatedId;
      this.editingAgent.agent_name = generatedId;
    }
  }

  private generateAgentIdFromDisplayName(displayName: string): string {
    if (!displayName?.trim()) return '';
    return displayName.trim()
      .replace(/[^a-zA-Z0-9\s]/g, '')
      .split(/\s+/)
      .map(word => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
      .join('');
  }

  getFilteredAvailableAgents(): string[] {
    return this.availableAgents.filter(name => name !== this.editingAgent.agent_name);
  }

  // ============================================
  // Agent Tools Methods
  // ============================================

  addAgentTool(toolName?: string): void {
    const tool = toolName || this.newToolName.trim();
    if (!tool) return;
    if (!this.editingAgent.agent_tools) this.editingAgent.agent_tools = [];
    if (!this.editingAgent.agent_tools.includes(tool)) this.editingAgent.agent_tools.push(tool);
    this.newToolName = '';
  }

  removeAgentTool(index: number): void {
    if (this.editingAgent.agent_tools) this.editingAgent.agent_tools.splice(index, 1);
  }

  isToolAdded(toolName: string): boolean {
    return this.editingAgent.agent_tools?.includes(toolName) || false;
  }

  getAvailableTools(): string[] {
    return this.availableToolOptions.filter(tool => !this.isToolAdded(tool));
  }

  // ============================================
  // Injectable Values Methods
  // ============================================

  addInjectableValue(): void {
    const key = this.newInjectableKey.trim();
    const value = this.newInjectableValue.trim();
    if (!key) return;
    if (!this.editingAgent.injectable_values) this.editingAgent.injectable_values = {};
    this.editingAgent.injectable_values[key] = value;
    this.newInjectableKey = '';
    this.newInjectableValue = '';
    this.refreshInjectableValuesCache();
  }

  removeInjectableValue(key: string): void {
    if (this.editingAgent.injectable_values) delete this.editingAgent.injectable_values[key];
    this.refreshInjectableValuesCache();
  }

  updateInjectableValue(key: string, value: string): void {
    if (this.editingAgent.injectable_values) this.editingAgent.injectable_values[key] = value;
  }

  getInjectableValuesArray(): { key: string; value: string }[] {
    return this.injectableValuesCache;
  }

  /** Rebuild the cached array from the injectable_values map. Call after any mutation. */
  refreshInjectableValuesCache(): void {
    if (!this.editingAgent.injectable_values) {
      this.injectableValuesCache = [];
      return;
    }
    this.injectableValuesCache = Object.entries(this.editingAgent.injectable_values).map(([key, value]) => ({ key, value }));
  }

  // ============================================
  // Visualization Mapping Methods
  // ============================================

  addVisualizationTemplate(): void {
    if (!this.visualizationMappings) {
      this.visualizationMappings = {
        agentName: this.editingAgent.agent_name || '',
        agentId: this.editingAgent.agent_id || '',
        templates: []
      };
    }
    this.visualizationMappings.templates.push({ templateId: '', usage: '' });
  }

  removeVisualizationTemplate(index: number): void {
    if (this.visualizationMappings?.templates) this.visualizationMappings.templates.splice(index, 1);
  }

  updateVisualizationTemplate(index: number, field: 'templateId' | 'usage', value: string): void {
    if (this.visualizationMappings?.templates[index]) this.visualizationMappings.templates[index][field] = value;
  }

  async saveVisualizationMappings(): Promise<boolean> {
    if (!this.visualizationMappings || !this.editingAgent.agent_name) return false;
    this.visualizationMappings.agentName = this.editingAgent.agent_name;
    this.visualizationMappings.agentId = this.editingAgent.agent_id || this.editingAgent.agent_name;
    try {
      return await this.agentDynamoDBService.saveVisualizationMappings(this.editingAgent.agent_name, this.visualizationMappings);
    } catch (error) {
      console.error('Error saving visualization mappings:', error);
      return false;
    }
  }

  // ============================================
  // Visualization JSON Editor Methods
  // ============================================

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

  closeVisualizationJsonEditor(): void {
    this.showVisualizationJsonEditor = false;
    this.visualizationJsonText = '';
    this.visualizationJsonError = null;
  }

  applyVisualizationJson(): void {
    try {
      const parsed = JSON.parse(this.visualizationJsonText);
      if (!parsed.agentName || !parsed.agentId || !Array.isArray(parsed.templates)) {
        throw new Error('Invalid structure. Required: agentName, agentId, templates[]');
      }
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
  // MCP Server Configuration Methods (delegates to helpers)
  // ============================================

  addMcpServer(preset?: { name: string; config: Partial<MCPServerConfig> }): void {
    if (!this.editingAgent.mcp_servers) this.editingAgent.mcp_servers = [];
    const newServer: MCPServerConfig = {
      id: generateMcpServerId(),
      name: 'New MCP Server',
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
    this.openMcpServerEditor(this.editingAgent.mcp_servers.length - 1);
  }

  removeMcpServer(index: number): void {
    if (this.editingAgent.mcp_servers) this.editingAgent.mcp_servers.splice(index, 1);
  }

  toggleMcpServerEnabled(index: number): void {
    if (this.editingAgent.mcp_servers?.[index]) {
      this.editingAgent.mcp_servers[index].enabled = !this.editingAgent.mcp_servers[index].enabled;
    }
  }

  openMcpServerEditor(index: number): void {
    if (!this.editingAgent.mcp_servers?.[index]) return;
    this.editingMcpServerIndex = index;
    this.editingMcpServer = JSON.parse(JSON.stringify(this.editingAgent.mcp_servers[index]));
    this.mcpServerJsonText = JSON.stringify(this.editingMcpServer, null, 2);
    this.mcpServerJsonError = null;
    this.showMcpServerEditor = true;
    this.mcpBearerTokenValue = '';
    this.mcpBearerTokenSaving = false;
    this.mcpBearerTokenPending = false;
    this.mcpBearerTokenEditing = false;
    this.mcpBearerTokenVisible = false;
    this.mcpOAuthUsername = '';
    this.mcpOAuthPassword = '';
    this.mcpOAuthPasswordVisible = false;
    this.mcpOAuthCredentialsSaving = false;
    this.mcpOAuthCredentialsPending = false;
    this.mcpOAuthCredentialsEditing = false;
    this.mcpEditorOpened.emit({ server: this.editingMcpServer!, index });
  }

  closeMcpServerEditor(): void {
    this.showMcpServerEditor = false;
    this.editingMcpServer = null;
    this.editingMcpServerIndex = -1;
    this.mcpServerJsonText = '';
    this.mcpServerJsonError = null;
    this.mcpBearerTokenValue = '';
    this.mcpBearerTokenSaving = false;
    this.mcpBearerTokenPending = false;
    this.mcpBearerTokenEditing = false;
    this.mcpBearerTokenVisible = false;
    this.mcpEditorClosed.emit();
  }

  saveMcpServerChanges(): void {
    if (!this.editingMcpServer || this.editingMcpServerIndex < 0) return;
    if (!this.editingMcpServer.name?.trim()) { this.mcpServerJsonError = 'Server name is required'; return; }
    if (this.editingMcpServer.transport === 'stdio' && !this.editingMcpServer.command?.trim()) {
      this.mcpServerJsonError = 'Command is required for stdio transport'; return;
    }
    if ((this.editingMcpServer.transport === 'http' || this.editingMcpServer.transport === 'sse') && !this.editingMcpServer.url?.trim()) {
      this.mcpServerJsonError = 'URL is required for HTTP/SSE transport'; return;
    }

    // If OAuth credentials were entered, store them in SSM before saving
    if (this.mcpOAuthUsername.trim() && this.mcpOAuthPassword.trim() && this.editingAgent.agent_name) {
      this.mcpOAuthCredentialsSaving = true;
      this.cdr.markForCheck();

      const credentialsJson = JSON.stringify({ username: this.mcpOAuthUsername.trim(), password: this.mcpOAuthPassword.trim() });
      this.agentDynamoDBService.storeMcpOAuthToken(
        this.editingAgent.agent_name,
        this.editingMcpServer.id,
        credentialsJson
      ).then(ssmPath => {
        if (ssmPath && this.editingMcpServer) {
          this.editingMcpServer.oauthToken = { hasToken: true, ssmPath };
        }
        this.finalizeMcpServerSave();
      }).catch(err => {
        console.error('Error storing OAuth credentials:', err);
        this.mcpServerJsonError = 'Failed to store credentials. Server saved without credentials.';
        this.finalizeMcpServerSave();
      }).finally(() => {
        this.mcpOAuthCredentialsSaving = false;
        this.cdr.markForCheck();
      });
    } else {
      this.finalizeMcpServerSave();
    }
  }

  private finalizeMcpServerSave(): void {
    if (!this.editingMcpServer || this.editingMcpServerIndex < 0) return;
    if (!this.editingAgent.mcp_servers) this.editingAgent.mcp_servers = [];
    this.editingAgent.mcp_servers[this.editingMcpServerIndex] = this.editingMcpServer;
    this.closeMcpServerEditor();
  }

  applyMcpServerJson(): void {
    try {
      const parsed = JSON.parse(this.mcpServerJsonText);
      if (!parsed.id || !parsed.name || !parsed.transport) throw new Error('Invalid structure. Required: id, name, transport');
      if (!['stdio', 'http', 'sse'].includes(parsed.transport)) throw new Error('Transport must be one of: stdio, http, sse');
      this.editingMcpServer = parsed;
      this.mcpServerJsonError = null;
    } catch (error: any) {
      this.mcpServerJsonError = error.message || 'Invalid JSON';
    }
  }

  formatMcpServerJson(): void {
    try {
      const parsed = JSON.parse(this.mcpServerJsonText);
      this.mcpServerJsonText = JSON.stringify(parsed, null, 2);
      this.mcpServerJsonError = null;
    } catch (error: any) {
      this.mcpServerJsonError = 'Cannot format: Invalid JSON';
    }
  }

  updateMcpServerJsonFromForm(): void {
    if (this.editingMcpServer) this.mcpServerJsonText = JSON.stringify(this.editingMcpServer, null, 2);
  }

  addMcpServerArg(arg: string): void {
    if (!arg?.trim() || !this.editingMcpServer) return;
    if (!this.editingMcpServer.args) this.editingMcpServer.args = [];
    this.editingMcpServer.args.push(arg.trim());
    this.updateMcpServerJsonFromForm();
  }

  removeMcpServerArg(index: number): void {
    if (this.editingMcpServer?.args) { this.editingMcpServer.args.splice(index, 1); this.updateMcpServerJsonFromForm(); }
  }

  addMcpServerEnv(key: string, value: string): void {
    if (!key?.trim() || !this.editingMcpServer) return;
    if (!this.editingMcpServer.env) this.editingMcpServer.env = {};
    this.editingMcpServer.env[key.trim()] = value;
    this.updateMcpServerJsonFromForm();
  }

  removeMcpServerEnv(key: string): void {
    if (this.editingMcpServer?.env) { delete this.editingMcpServer.env[key]; this.updateMcpServerJsonFromForm(); }
  }

  getMcpServerEnvArray(): { key: string; value: string }[] {
    if (!this.editingMcpServer?.env) return [];
    return Object.entries(this.editingMcpServer.env).map(([key, value]) => ({ key, value }));
  }

  addMcpServerHeader(key: string, value: string): void {
    if (!key?.trim() || !this.editingMcpServer) return;
    if (!this.editingMcpServer.headers) this.editingMcpServer.headers = {};
    this.editingMcpServer.headers[key.trim()] = value;
    this.updateMcpServerJsonFromForm();
  }

  removeMcpServerHeader(key: string): void {
    if (this.editingMcpServer?.headers) { delete this.editingMcpServer.headers[key]; this.updateMcpServerJsonFromForm(); }
  }

  getMcpServerHeadersArray(): { key: string; value: string }[] {
    if (!this.editingMcpServer?.headers) return [];
    return Object.entries(this.editingMcpServer.headers).map(([key, value]) => ({ key, value }));
  }

  getMcpTransportIcon(transport: string): string { return getMcpTransportIcon(transport); }
  getMcpTransportName(transport: string): string { return getMcpTransportName(transport); }

  getMcpToolListResult(serverId: string): MCPToolListResult | undefined {
    return this.mcpToolListResults.get(serverId);
  }

  toggleMcpToolList(serverId: string): void {
    const result = this.mcpToolListResults.get(serverId);
    if (result) result.expanded = !result.expanded;
  }

  async listMcpServerTools(server: MCPServerConfig, event?: Event): Promise<void> {
    if (event) event.stopPropagation();
    this.mcpToolListResults.set(server.id, { serverId: server.id, tools: [], loading: true, expanded: true });
    this.cdr.markForCheck();
    const result = await listMcpServerToolsHelper(
      server,
      this.awsConfigService,
      this.agentDynamoDBService,
      this.editingAgent.agent_name
    );
    this.mcpToolListResults.set(server.id, result);
    this.cdr.markForCheck();
  }

  dismissMcpToolList(serverId: string, event?: Event): void {
    if (event) event.stopPropagation();
    this.mcpToolListResults.delete(serverId);
  }

  // ============================================
  // A2A External Agent Configuration Methods
  // ============================================

  /** Generate a unique ID for a new external agent config */
  private generateA2aAgentId(): string {
    return 'a2a_' + Date.now().toString(36) + '_' + Math.random().toString(36).substring(2, 6);
  }

  addA2aAgent(): void {
    if (!this.editingAgent.external_agent_configs) this.editingAgent.external_agent_configs = [];
    const newAgent: ExternalAgentConfig = {
      id: this.generateA2aAgentId(),
      name: '',
      arn: '',
      isA2A: true,
      description: '',
      enabled: true,
      authType: 'none'
    };
    this.editingAgent.external_agent_configs.push(newAgent);
    this.openA2aAgentEditor(this.editingAgent.external_agent_configs.length - 1);
  }

  removeA2aAgent(index: number): void {
    if (this.editingAgent.external_agent_configs) {
      const agent = this.editingAgent.external_agent_configs[index];
      // Clean up SSM token if it exists
      if (agent?.oauthToken?.hasToken && this.editingAgent.agent_name) {
        this.agentDynamoDBService.deleteA2AOAuthToken(this.editingAgent.agent_name, agent.id).catch(err => {
          console.warn('⚠️ Failed to clean up A2A OAuth token on remove:', err);
        });
      }
      // Clean up SSM credentials if they exist
      if (agent?.oauthCredentials?.hasCredentials && this.editingAgent.agent_name) {
        this.agentDynamoDBService.deleteA2AOAuthToken(this.editingAgent.agent_name, agent.id).catch(err => {
          console.warn('⚠️ Failed to clean up A2A OAuth credentials on remove:', err);
        });
      }
      // Always remove from UI regardless of SSM cleanup outcome
      this.editingAgent.external_agent_configs.splice(index, 1);
    }
  }

  toggleA2aAgentEnabled(index: number): void {
    if (this.editingAgent.external_agent_configs?.[index]) {
      this.editingAgent.external_agent_configs[index].enabled = !this.editingAgent.external_agent_configs[index].enabled;
    }
  }

  openA2aAgentEditor(index: number): void {
    if (!this.editingAgent.external_agent_configs?.[index]) return;
    this.editingA2aAgentIndex = index;
    this.editingA2aAgent = JSON.parse(JSON.stringify(this.editingAgent.external_agent_configs[index]));
    this.a2aEditorError = null;
    this.showA2aAgentEditor = true;
    this.a2aBearerTokenValue = '';
    this.a2aBearerTokenSaving = false;
    this.a2aBearerTokenPending = false;
    this.a2aBearerTokenEditing = false;
    this.a2aBearerTokenVisible = false;
    this.a2aOAuthUsername = '';
    this.a2aOAuthPassword = '';
    this.a2aOAuthPasswordVisible = false;
    this.a2aOAuthCredentialsSaving = false;
    this.a2aOAuthCredentialsPending = false;
    this.a2aOAuthCredentialsEditing = false;
    this.a2aEditorOpened.emit({ agent: this.editingA2aAgent!, index });
  }

  closeA2aAgentEditor(): void {
    this.showA2aAgentEditor = false;
    this.editingA2aAgent = null;
    this.editingA2aAgentIndex = -1;
    this.a2aEditorError = null;
    this.a2aBearerTokenValue = '';
    this.a2aBearerTokenSaving = false;
    this.a2aBearerTokenPending = false;
    this.a2aBearerTokenEditing = false;
    this.a2aBearerTokenVisible = false;
    this.a2aEditorClosed.emit();
  }

  saveA2aAgentChanges(): void {
    if (!this.editingA2aAgent || this.editingA2aAgentIndex < 0) return;
    if (!this.editingA2aAgent.name?.trim()) { this.a2aEditorError = 'Agent name is required'; return; }
    if (!this.editingA2aAgent.arn?.trim()) { this.a2aEditorError = 'Agent ARN is required'; return; }

    // If OAuth credentials were entered, store them in SSM before saving
    if (this.a2aOAuthUsername.trim() && this.a2aOAuthPassword.trim() && this.editingAgent.agent_name) {
      this.a2aOAuthCredentialsSaving = true;
      this.cdr.markForCheck();

      const credentialsJson = JSON.stringify({ username: this.a2aOAuthUsername.trim(), password: this.a2aOAuthPassword.trim() });
      this.agentDynamoDBService.storeA2AOAuthToken(
        this.editingAgent.agent_name,
        this.editingA2aAgent.id,
        credentialsJson
      ).then(ssmPath => {
        if (ssmPath && this.editingA2aAgent) {
          this.editingA2aAgent.oauthCredentials = { hasCredentials: true, ssmPath };
          this.editingA2aAgent.oauthToken = { hasToken: true, ssmPath };
        }
        this.finalizeA2aAgentSave();
      }).catch(err => {
        console.error('Error storing A2A OAuth credentials:', err);
        this.a2aEditorError = 'Failed to store credentials. Agent saved without credentials.';
        this.finalizeA2aAgentSave();
      }).finally(() => {
        this.a2aOAuthCredentialsSaving = false;
        this.cdr.markForCheck();
      });
    } else {
      this.finalizeA2aAgentSave();
    }
  }

  private finalizeA2aAgentSave(): void {
    if (!this.editingA2aAgent || this.editingA2aAgentIndex < 0) return;
    if (!this.editingAgent.external_agent_configs) this.editingAgent.external_agent_configs = [];
    this.editingAgent.external_agent_configs[this.editingA2aAgentIndex] = this.editingA2aAgent;
    this.a2aEditorSaved.emit({ agent: this.editingA2aAgent, index: this.editingA2aAgentIndex });
    this.closeA2aAgentEditor();
  }

  /** Save a bearer token to SSM independently for an A2A agent (update/new token flow) */
  async saveA2aBearerToken(): Promise<void> {
    if (!this.editingA2aAgent || !this.a2aBearerTokenValue.trim() || !this.editingAgent.agent_name) return;

    this.a2aBearerTokenSaving = true;
    this.a2aEditorError = null;
    this.cdr.markForCheck();

    try {
      const ssmPath = await this.agentDynamoDBService.storeA2AOAuthToken(
        this.editingAgent.agent_name,
        this.editingA2aAgent.id,
        this.a2aBearerTokenValue.trim()
      );

      this.editingA2aAgent.oauthToken = { hasToken: true, ssmPath: ssmPath || undefined };
      this.a2aBearerTokenValue = '';
      this.a2aBearerTokenEditing = false;
      this.a2aBearerTokenPending = false;
    } catch (error: any) {
      console.error('Error saving A2A bearer token:', error);
      this.a2aEditorError = error.message || 'Failed to store token.';
    } finally {
      this.a2aBearerTokenSaving = false;
      this.cdr.markForCheck();
    }
  }

  /** Remove the OAuth bearer token from SSM for an A2A agent */
  async removeA2aBearerToken(): Promise<void> {
    if (!this.editingA2aAgent || !this.editingAgent.agent_name) return;

    this.a2aBearerTokenSaving = true;
    this.a2aEditorError = null;
    this.cdr.markForCheck();

    try {
      await this.agentDynamoDBService.deleteA2AOAuthToken(
        this.editingAgent.agent_name,
        this.editingA2aAgent.id
      );
      this.editingA2aAgent.oauthToken = undefined;
      this.a2aBearerTokenPending = false;
      this.a2aBearerTokenEditing = false;
      this.a2aBearerTokenValue = '';
    } catch (error: any) {
      console.error('Error removing A2A OAuth token:', error);
      this.a2aEditorError = error.message || 'Failed to remove token.';
    } finally {
      this.a2aBearerTokenSaving = false;
      this.cdr.markForCheck();
    }
  }

  /** Generate a unique ID for an external agent config */
  private generateExternalAgentId(): string {
    return this.generateA2aAgentId();
  }

  /** Save A2A OAuth credentials (username/password) to SSM as JSON */
  async saveA2aOAuthCredentials(): Promise<void> {
    if (!this.editingA2aAgent || !this.a2aOAuthUsername.trim() || !this.a2aOAuthPassword.trim() || !this.editingAgent.agent_name) return;

    this.a2aOAuthCredentialsSaving = true;
    this.a2aEditorError = null;
    this.cdr.markForCheck();

    try {
      const credentialsJson = JSON.stringify({ username: this.a2aOAuthUsername.trim(), password: this.a2aOAuthPassword.trim() });
      const ssmPath = await this.agentDynamoDBService.storeA2AOAuthToken(
        this.editingAgent.agent_name,
        this.editingA2aAgent.id,
        credentialsJson
      );

      this.editingA2aAgent.oauthCredentials = { hasCredentials: true, ssmPath: ssmPath || undefined };
      this.editingA2aAgent.oauthToken = { hasToken: true, ssmPath: ssmPath || undefined };
      this.a2aOAuthUsername = '';
      this.a2aOAuthPassword = '';
      this.a2aOAuthCredentialsEditing = false;
      this.a2aOAuthCredentialsPending = false;
    } catch (error: any) {
      console.error('Error saving A2A OAuth credentials:', error);
      this.a2aEditorError = error.message || 'Failed to store credentials.';
    } finally {
      this.a2aOAuthCredentialsSaving = false;
      this.cdr.markForCheck();
    }
  }

  /** Remove A2A OAuth credentials from SSM */
  async removeA2aOAuthCredentials(): Promise<void> {
    if (!this.editingA2aAgent || !this.editingAgent.agent_name) return;

    this.a2aOAuthCredentialsSaving = true;
    this.a2aEditorError = null;
    this.cdr.markForCheck();

    try {
      await this.agentDynamoDBService.deleteA2AOAuthToken(
        this.editingAgent.agent_name,
        this.editingA2aAgent.id
      );
      this.editingA2aAgent.oauthCredentials = undefined;
      this.editingA2aAgent.oauthToken = undefined;
      this.a2aOAuthCredentialsPending = false;
      this.a2aOAuthCredentialsEditing = false;
      this.a2aOAuthUsername = '';
      this.a2aOAuthPassword = '';
    } catch (error: any) {
      console.error('Error removing A2A OAuth credentials:', error);
      this.a2aEditorError = error.message || 'Failed to remove credentials.';
    } finally {
      this.a2aOAuthCredentialsSaving = false;
      this.cdr.markForCheck();
    }
  }

  /** Save MCP OAuth credentials (username/password) to SSM as JSON */
  async saveMcpOAuthCredentials(): Promise<void> {
    if (!this.editingMcpServer || !this.mcpOAuthUsername.trim() || !this.mcpOAuthPassword.trim() || !this.editingAgent.agent_name) return;

    this.mcpOAuthCredentialsSaving = true;
    this.mcpServerJsonError = null;
    this.cdr.markForCheck();

    try {
      const credentialsJson = JSON.stringify({ username: this.mcpOAuthUsername.trim(), password: this.mcpOAuthPassword.trim() });
      const ssmPath = await this.agentDynamoDBService.storeMcpOAuthToken(
        this.editingAgent.agent_name,
        this.editingMcpServer.id,
        credentialsJson
      );

      this.editingMcpServer.oauthToken = { hasToken: true, ssmPath: ssmPath || undefined };
      this.mcpOAuthUsername = '';
      this.mcpOAuthPassword = '';
      this.mcpOAuthCredentialsEditing = false;
      this.mcpOAuthCredentialsPending = false;
      this.updateMcpServerJsonFromForm();
    } catch (error: any) {
      console.error('Error saving MCP OAuth credentials:', error);
      this.mcpServerJsonError = error.message || 'Failed to store credentials.';
    } finally {
      this.mcpOAuthCredentialsSaving = false;
      this.cdr.markForCheck();
    }
  }

  /** Remove MCP OAuth credentials from SSM */
  async removeMcpOAuthCredentials(server: MCPServerConfig): Promise<void> {
    if (!server || !this.editingAgent.agent_name) return;

    this.mcpOAuthCredentialsSaving = true;
    this.mcpServerJsonError = null;
    this.cdr.markForCheck();

    try {
      await this.agentDynamoDBService.deleteMcpOAuthToken(
        this.editingAgent.agent_name,
        server.id
      );
      server.oauthToken = undefined;
      this.mcpOAuthCredentialsPending = false;
      this.mcpOAuthCredentialsEditing = false;
      this.mcpOAuthUsername = '';
      this.mcpOAuthPassword = '';
      this.updateMcpServerJsonFromForm();
    } catch (error: any) {
      console.error('Error removing MCP OAuth credentials:', error);
      this.mcpServerJsonError = error.message || 'Failed to remove credentials.';
    } finally {
      this.mcpOAuthCredentialsSaving = false;
      this.cdr.markForCheck();
    }
  }

  /** Add a new external A2A agent config (alias) */
  addExternalAgent(): void {
    this.addA2aAgent();
  }

  /** Remove an external agent config by index (alias) */
  removeExternalAgent(index: number): void {
    this.removeA2aAgent(index);
  }

  /** Toggle enabled state of an external agent (alias) */
  toggleExternalAgentEnabled(index: number): void {
    this.toggleA2aAgentEnabled(index);
  }

  /** Set the A2A auth type (none or bearer) */
  setA2aAuthType(agent: ExternalAgentConfig, type: 'none' | 'oauth' | 'iam'): void {
    agent.authType = type;
    switch (type) {
      case 'none':
        agent.awsAuth = undefined;
        this.a2aBearerTokenPending = false;
        this.a2aBearerTokenEditing = false;
        this.a2aBearerTokenValue = '';
        this.a2aOAuthUsername = '';
        this.a2aOAuthPassword = '';
        this.a2aOAuthCredentialsPending = false;
        this.a2aOAuthCredentialsEditing = false;
        break;
      case 'oauth':
        agent.awsAuth = undefined;
        if (!agent.oauthCredentials?.hasCredentials) {
          this.a2aOAuthCredentialsPending = true;
        }
        break;
      case 'iam':
        agent.awsAuth = { region: 'us-east-1', service: 'bedrock-agentcore' };
        this.a2aOAuthCredentialsPending = false;
        this.a2aOAuthCredentialsEditing = false;
        this.a2aOAuthUsername = '';
        this.a2aOAuthPassword = '';
        break;
    }
  }

  /** Get the effective A2A auth type from the agent config */
  getA2aAuthType(agent: ExternalAgentConfig): 'none' | 'oauth' | 'iam' {
    if (agent.authType) return agent.authType;
    if (agent.awsAuth) return 'iam';
    if (agent.oauthToken?.hasToken || agent.oauthCredentials?.hasCredentials) return 'oauth';
    return 'none';
  }

  // ============================================
  // OAuth Token Management
  // ============================================

  /** Switch authentication type for an MCP server */
  setMcpAuthType(server: MCPServerConfig, authType: 'none' | 'bearer' | 'aws_iam'): void {
    switch (authType) {
      case 'none':
        server.awsAuth = undefined;
        this.mcpBearerTokenPending = false;
        this.mcpBearerTokenEditing = false;
        this.mcpBearerTokenValue = '';
        this.mcpOAuthUsername = '';
        this.mcpOAuthPassword = '';
        this.mcpOAuthCredentialsPending = false;
        this.mcpOAuthCredentialsEditing = false;
        break;
      case 'bearer':
        server.awsAuth = undefined;
        if (!server.oauthToken?.hasToken) {
          this.mcpOAuthCredentialsPending = true;
        }
        break;
      case 'aws_iam':
        server.awsAuth = { region: 'us-east-1', service: 'bedrock-agentcore' };
        this.mcpBearerTokenPending = false;
        this.mcpBearerTokenEditing = false;
        this.mcpBearerTokenValue = '';
        this.mcpOAuthUsername = '';
        this.mcpOAuthPassword = '';
        this.mcpOAuthCredentialsPending = false;
        this.mcpOAuthCredentialsEditing = false;
        break;
    }
    this.updateMcpServerJsonFromForm();
  }

  /** Remove the OAuth bearer token from SSM and clear the config */
  async removeMcpBearerToken(server: MCPServerConfig): Promise<void> {
    if (!this.editingAgent.agent_name) return;

    this.mcpBearerTokenSaving = true;
    this.mcpServerJsonError = null;
    this.cdr.markForCheck();

    try {
      await this.agentDynamoDBService.deleteMcpOAuthToken(
        this.editingAgent.agent_name,
        server.id
      );
      server.oauthToken = undefined;
      this.mcpBearerTokenPending = false;
      this.mcpBearerTokenEditing = false;
      this.mcpBearerTokenValue = '';
      this.updateMcpServerJsonFromForm();
    } catch (error: any) {
      console.error('Error removing OAuth token:', error);
      this.mcpServerJsonError = error.message || 'Failed to remove token.';
    } finally {
      this.mcpBearerTokenSaving = false;
      this.cdr.markForCheck();
    }
  }

  /** Save a bearer token to SSM independently (for update/new token flow) */
  async saveMcpBearerToken(): Promise<void> {
    if (!this.editingMcpServer || !this.mcpBearerTokenValue.trim() || !this.editingAgent.agent_name) return;

    this.mcpBearerTokenSaving = true;
    this.mcpServerJsonError = null;
    this.cdr.markForCheck();

    try {
      const ssmPath = await this.agentDynamoDBService.storeMcpOAuthToken(
        this.editingAgent.agent_name,
        this.editingMcpServer.id,
        this.mcpBearerTokenValue.trim()
      );

      this.editingMcpServer.oauthToken = { hasToken: true, ssmPath: ssmPath || undefined };
      this.mcpBearerTokenValue = '';
      this.mcpBearerTokenEditing = false;
      this.mcpBearerTokenPending = false;
      this.updateMcpServerJsonFromForm();
    } catch (error: any) {
      console.error('Error saving bearer token:', error);
      this.mcpServerJsonError = error.message || 'Failed to store token.';
    } finally {
      this.mcpBearerTokenSaving = false;
      this.cdr.markForCheck();
    }
  }

  // ============================================
  // AI Generation Methods (delegates to helpers)
  // ============================================

  /** True when the agent uses an external runtime (not blank, not the AdFabricAgent default) */
  get isExternalRuntime(): boolean {
    const arn = this.editingAgent.runtime_arn?.trim();
    return !!arn && !!this.defaultRuntimeArn && arn !== this.defaultRuntimeArn;
  }

  showGenerateInstructionsDialog(): void {
    if (this.isExternalRuntime) return; // External runtime — instructions managed externally
    this.showInstructionsPrompt = true;
    this.instructionsPromptText = '';
    this.instructionsAttachedDocs = [];
    this.aiGenerationError = null;
  }

  hideGenerateInstructionsDialog(): void {
    this.showInstructionsPrompt = false;
    this.instructionsPromptText = '';
    this.instructionsAttachedDocs = [];
  }

  async generateInstructions(): Promise<void> {
    this.isGeneratingInstructions = true;
    this.aiGenerationError = null;
    try {
      this.editingAgent.instructions = await generateInstructionsText(
        this.editingAgent, this.instructionsPromptText, this.bedrockService,
        this.instructionsAttachedDocs
      );
      this.hideGenerateInstructionsDialog();
    } catch (error: any) {
      console.error('Error generating instructions:', error);
      this.aiGenerationError = error.message || 'Failed to generate instructions. Please try again.';
    } finally {
      this.isGeneratingInstructions = false;
      this.cdr.markForCheck();
    }
  }

  showGenerateMappingsDialog(): void {
      this.showMappingsPrompt = true;
      this.mappingsPromptText = '';
      this.mappingsAttachedDocs = [];
      this.aiGenerationError = null;
    }

  hideGenerateMappingsDialog(): void {
      this.showMappingsPrompt = false;
      this.mappingsPromptText = '';
      this.mappingsAttachedDocs = [];
    }

  async generateVisualizationMappings(): Promise<void> {
      this.isGeneratingMappings = true;
      this.aiGenerationError = null;
      try {
        const templates = await generateVisualizationMappingsText(
          this.editingAgent,
          this.visualizationMappings?.templates,
          this.availableTemplates,
          this.mappingsPromptText,
          this.bedrockService,
          this.mappingsAttachedDocs
        );
        if (!this.visualizationMappings) {
          this.visualizationMappings = {
            agentName: this.editingAgent.agent_name || '',
            agentId: this.editingAgent.agent_id || '',
            templates: []
          };
        }
        this.visualizationMappings.templates = templates;
        this.hideGenerateMappingsDialog();
      } catch (error: any) {
        console.error('Error generating visualization mappings:', error);
        this.aiGenerationError = error.message || 'Failed to generate visualization mappings. Please try again.';
      } finally {
        this.isGeneratingMappings = false;
        this.cdr.markForCheck();
      }
    }

  // ============================================
  // Save & Visualization Preview
  // ============================================

  // ============================================
  // Document Attachment Helpers
  // ============================================

  /** Handle file input change for instructions document attachment */
  onInstructionsFileAttach(event: Event): void {
    this.handleFileAttach(event, this.instructionsAttachedDocs);
  }

  /** Handle file input change for mappings document attachment */
  onMappingsFileAttach(event: Event): void {
    this.handleFileAttach(event, this.mappingsAttachedDocs);
  }

  /** Remove an attached document by index */
  removeInstructionsDoc(index: number): void {
    this.instructionsAttachedDocs.splice(index, 1);
  }

  /** Remove an attached document by index */
  removeMappingsDoc(index: number): void {
    this.mappingsAttachedDocs.splice(index, 1);
  }

  /** Read files from input and add to the target document array */
  private handleFileAttach(event: Event, target: AttachedDocument[]): void {
    const input = event.target as HTMLInputElement;
    if (!input.files?.length) return;

    const allowedTypes = ['.txt', '.md', '.json', '.csv', '.xml', '.yaml', '.yml', '.log'];

    Array.from(input.files).forEach(file => {
      const ext = '.' + file.name.split('.').pop()?.toLowerCase();
      if (!allowedTypes.includes(ext) && !file.type.startsWith('text/')) {
        console.warn(`Skipping unsupported file type: ${file.name}`);
        return;
      }
      if (file.size > 512 * 1024) { // 512KB limit per file
        console.warn(`File too large (max 512KB): ${file.name}`);
        return;
      }
      const reader = new FileReader();
      reader.onload = () => {
        target.push({ name: file.name, content: reader.result as string });
        this.cdr.markForCheck();
      };
      reader.readAsText(file);
    });

    // Reset input so the same file can be re-selected
    input.value = '';
  }

  handleSave(): void {
    if (this.validate()) {
      if (this.visualizationMappings && this.editingAgent.agent_name) {
        this.saveVisualizationMappings();
      }
      this.onSave.emit(this.editingAgent);
    }
  }

  openVisualizationPreview(templateId: string, usage: string): void {
    if (!templateId) return;
    this.previewTemplateId = templateId;
    this.previewTemplateUsage = usage || 'No usage description provided';
    this.previewSampleData = SAMPLE_DATA_BY_TEMPLATE[templateId] || this.generateGenericSampleData(templateId);
    this.showVisualizationPreview = true;
  }

  closeVisualizationPreview(): void {
    this.showVisualizationPreview = false;
    this.previewTemplateId = null;
    this.previewSampleData = null;
    this.previewTemplateUsage = '';
  }

  private generateGenericSampleData(templateId: string): any {
    return {
      visualizationType: templateId.replace('-visualization', ''),
      templateId,
      title: `Preview: ${templateId}`,
      message: 'Sample data for this visualization template',
      data: [
        { label: 'Item 1', value: 100 },
        { label: 'Item 2', value: 75 },
        { label: 'Item 3', value: 50 }
      ]
    };
  }

  getTemplateDisplayName(templateId: string): string {
    if (!templateId) return 'Unknown';
    return templateId.replace('-visualization', '').split(/[-_]/)
      .map(word => word.charAt(0).toUpperCase() + word.slice(1)).join(' ');
  }

  // ============================================
  // Runtime ARN Combobox
  // ============================================

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
    setTimeout(() => { this.runtimeArnDropdownOpen = false; this.cdr.markForCheck(); }, 200);
  }

  // ============================================
  // Knowledge Base Typeahead
  // ============================================

  private async loadKnowledgeBases(): Promise<void> {
    this.isLoadingKnowledgeBases = true;
    this.cdr.markForCheck();
    try {
      this.knowledgeBases = await this.awsConfigService.listKnowledgeBases();
      this.filteredKnowledgeBases = [...this.knowledgeBases];
      // Check if stored knowledge_base matches any discovered KB (by ID or legacy name)
      this.kbNotFound = !!this.editingAgent.knowledge_base &&
        !this.knowledgeBases.some(kb => kb.knowledgeBaseId === this.editingAgent.knowledge_base || kb.name === this.editingAgent.knowledge_base);
    } catch (error) {
      console.error('Failed to load knowledge bases:', error);
      this.knowledgeBases = [];
      this.filteredKnowledgeBases = [];
      this.kbNotFound = false;
    } finally {
      this.isLoadingKnowledgeBases = false;
      this.cdr.markForCheck();
    }
  }

  onKbFilterInput(value: string): void {
    this.kbFilterText = value;
    this.filteredKnowledgeBases = this.filterKnowledgeBases(value, this.knowledgeBases);
    this.kbDropdownOpen = true;
  }

  selectKnowledgeBase(kb: KnowledgeBaseInfo): void {
    console.log('selected knowledgebase',kb)
    this.editingAgent.knowledge_base = kb.knowledgeBaseId;
    this.kbDropdownOpen = false;
    this.kbFilterText = '';
    this.filteredKnowledgeBases = [...this.knowledgeBases];
    this.kbNotFound = false;
  }

  clearKnowledgeBase(): void {
    this.editingAgent.knowledge_base = '';
    this.kbFilterText = '';
    this.kbDropdownOpen = false;
    this.filteredKnowledgeBases = [...this.knowledgeBases];
    this.kbNotFound = false;
  }

  /**
   * Resolves a KB ID (or legacy name) to its display name for the combobox input.
   * Returns the name if found by ID or name match, otherwise returns the raw value.
   */
  getKbDisplayName(kbId: string | undefined): string {
    if (!kbId) return '';
    const match = this.knowledgeBases.find(kb => kb.knowledgeBaseId === kbId || kb.name === kbId);
    return match ? match.name : kbId;
  }

  toggleKbDropdown(): void {
    this.kbDropdownOpen = !this.kbDropdownOpen;
    this.kbFilterText = '';
    this.filteredKnowledgeBases = [...this.knowledgeBases];
  }

  closeKbDropdown(): void {
    setTimeout(() => { this.kbDropdownOpen = false; this.cdr.markForCheck(); }, 200);
  }

  filterKnowledgeBases(query: string, kbs: KnowledgeBaseInfo[]): KnowledgeBaseInfo[] {
    const q = query.toLowerCase().trim();
    if (!q) return kbs;
    return kbs.filter(kb =>
      kb.name.toLowerCase().includes(q) ||
      kb.knowledgeBaseId.toLowerCase().includes(q)
    );
  }
}
