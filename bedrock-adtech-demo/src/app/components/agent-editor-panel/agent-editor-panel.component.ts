import { Component, Input, Output, EventEmitter, OnInit, OnChanges, SimpleChanges, ChangeDetectorRef, ChangeDetectionStrategy } from '@angular/core';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { AgentConfiguration, MCPServerConfig } from '../agent-management-modal/agent-management-modal.component';
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
  generateInstructionsText, generateVisualizationMappingsText
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

  @Output() onSave = new EventEmitter<AgentConfiguration>();
  @Output() onCancel = new EventEmitter<void>();
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
  mcpToolListResults: Map<string, MCPToolListResult> = new Map();

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
      this.editingAgent.runtime_arn = this.editingAgent.runtime_arn || '';
      this.editingAgent.knowledge_base = this.editingAgent.knowledge_base || '';
      this.editingAgent.instructions = this.editingAgent.instructions || '';

      if (!this.editingAgent.model_inputs || Object.keys(this.editingAgent.model_inputs).length === 0) {
        this.editingAgent.model_inputs = {
          default: { model_id: 'anthropic.claude-3-5-sonnet-20241022-v2:0', max_tokens: 8000, temperature: 0.3 }
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
    this.showVisualizationJsonEditor = false;
    this.visualizationJsonText = '';
    this.visualizationJsonError = null;
    this.showMcpServerEditor = false;
    this.editingMcpServer = null;
    this.editingMcpServerIndex = -1;
    this.mcpServerJsonText = '';
    this.mcpServerJsonError = null;
    this.runtimeArnDropdownOpen = false;
    this.runtimeArnFilter = '';
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
        default: { model_id: 'anthropic.claude-3-5-sonnet-20241022-v2:0', max_tokens: 8000, temperature: 0.3 }
      },
      agent_tools: [], injectable_values: {}, instructions: '', color: '#6842ff',
      mcp_servers: [], runtime_arn: '', knowledge_base: ''
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

  getDefaultModelInputs(): { model_id: string; max_tokens: number; temperature: number; top_p?: number } | null {
    if (!this.editingAgent.model_inputs) return null;
    if (this.editingAgent.model_inputs['default']) return this.editingAgent.model_inputs['default'];
    const keys = Object.keys(this.editingAgent.model_inputs);
    return keys.length > 0 ? this.editingAgent.model_inputs[keys[0]] : null;
  }

  updateModelInput(field: 'model_id' | 'max_tokens' | 'temperature', value: string | number): void {
    if (!this.editingAgent.model_inputs) {
      this.editingAgent.model_inputs = {
        default: { model_id: 'anthropic.claude-3-5-sonnet-20241022-v2:0', max_tokens: 8000, temperature: 0.3 }
      };
    }
    let key = 'default';
    if (!this.editingAgent.model_inputs['default']) {
      const keys = Object.keys(this.editingAgent.model_inputs);
      if (keys.length > 0) { key = keys[0]; }
      else { this.editingAgent.model_inputs['default'] = { model_id: 'anthropic.claude-3-5-sonnet-20241022-v2:0', max_tokens: 8000, temperature: 0.3 }; }
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
  }

  removeInjectableValue(key: string): void {
    if (this.editingAgent.injectable_values) delete this.editingAgent.injectable_values[key];
  }

  updateInjectableValue(key: string, value: string): void {
    if (this.editingAgent.injectable_values) this.editingAgent.injectable_values[key] = value;
  }

  getInjectableValuesArray(): { key: string; value: string }[] {
    if (!this.editingAgent.injectable_values) return [];
    return Object.entries(this.editingAgent.injectable_values).map(([key, value]) => ({ key, value }));
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
    this.mcpEditorOpened.emit({ server: this.editingMcpServer!, index });
  }

  closeMcpServerEditor(): void {
    this.showMcpServerEditor = false;
    this.editingMcpServer = null;
    this.editingMcpServerIndex = -1;
    this.mcpServerJsonText = '';
    this.mcpServerJsonError = null;
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
    const result = await listMcpServerToolsHelper(server, this.awsConfigService);
    this.mcpToolListResults.set(server.id, result);
    this.cdr.markForCheck();
  }

  dismissMcpToolList(serverId: string, event?: Event): void {
    if (event) event.stopPropagation();
    this.mcpToolListResults.delete(serverId);
  }

  // ============================================
  // AI Generation Methods (delegates to helpers)
  // ============================================

  showGenerateInstructionsDialog(): void {
    this.showInstructionsPrompt = true;
    this.instructionsPromptText = '';
    this.aiGenerationError = null;
  }

  hideGenerateInstructionsDialog(): void {
    this.showInstructionsPrompt = false;
    this.instructionsPromptText = '';
  }

  async generateInstructions(): Promise<void> {
    this.isGeneratingInstructions = true;
    this.aiGenerationError = null;
    try {
      this.editingAgent.instructions = await generateInstructionsText(
        this.editingAgent, this.instructionsPromptText, this.bedrockService
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
    this.aiGenerationError = null;
  }

  hideGenerateMappingsDialog(): void {
    this.showMappingsPrompt = false;
    this.mappingsPromptText = '';
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
        this.bedrockService
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
}
