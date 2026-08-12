import { Component, Input, Output, EventEmitter, OnInit, OnChanges, SimpleChanges, ChangeDetectorRef, ChangeDetectionStrategy } from '@angular/core';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { AgentConfiguration, MCPServerConfig, ExternalAgentConfig } from '../agent-management-modal/agent-management-modal.component';
import { KnowledgeBaseInfo } from '../../models/application-models';
import {
  AgentDynamoDBService,
  VisualizationMapping,
  InstructionVersionSummary,
  INSTRUCTION_LIVE_SK
} from '../../services/agent-dynamodb.service';
import { BedrockService } from '../../services/bedrock.service';
import { AwsConfigService } from '../../services/aws-config.service';
import {
  OAuthClientCredentialsInput,
  OAuthClientCredentialsRef,
  buildClientCredentialsDocument,
  clearClientCredentialsTokenCache,
  parseClientCredentialsDocument
} from '../../services/oauth-client-credentials';
import {
  AgentProtocol,
  classifyEndpoint,
  describeInvocationPlan,
  planInvocation,
  resolveAgentEndpoint,
  resolveAgentProtocol,
  resolveEntryEndpoint,
  resolveEntryProtocol
} from '../../services/agent-invocation-plan';
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

  // Instruction version state. `selectedInstructionVersionSk` holds a sort key,
  // using the live pointer's key for the "current" option.
  readonly liveInstructionSk = INSTRUCTION_LIVE_SK;
  instructionVersions: InstructionVersionSummary[] = [];
  selectedInstructionVersionSk: string = INSTRUCTION_LIVE_SK;
  liveInstructionVersion: number | null = null;
  liveInstructionUpdatedAt: string | null = null;
  isLoadingInstructionVersions: boolean = false;
  isLoadingInstructionVersion: boolean = false;
  instructionVersionError: string | null = null;

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
  a2aBearerTokenExpiry: string = '';
  a2aBearerTokenSaving: boolean = false;
  a2aBearerTokenPending: boolean = false;
  a2aBearerTokenEditing: boolean = false;
  a2aBearerTokenVisible: boolean = false;

  // A2A OAuth credentials state (client id / username / password)
  a2aOAuthClientId: string = '';
  a2aOAuthUsername: string = '';
  a2aOAuthPassword: string = '';
  a2aOAuthPasswordVisible: boolean = false;
  a2aOAuthCredentialsSaving: boolean = false;
  a2aOAuthCredentialsPending: boolean = false;
  a2aOAuthCredentialsEditing: boolean = false;

  /**
   * Names of external A2A entries whose credential was written to Parameter
   * Store but whose config reference has not been persisted yet.
   *
   * Credential writes hit SSM immediately, whereas the entry config is staged on
   * `editingAgent` and only persisted when the agent itself is saved. Until that
   * save happens the runtime cannot see the entry, so the credential is inert —
   * and abandoning the editor leaves an orphaned parameter. This set drives an
   * explicit warning so a stored credential is never mistaken for an active one.
   */
  a2aCredentialsAwaitingAgentSave = new Set<string>();

  // MCP OAuth credentials state (username/password)
  mcpOAuthUsername: string = '';
  mcpOAuthPassword: string = '';
  mcpOAuthPasswordVisible: boolean = false;
  mcpOAuthCredentialsSaving: boolean = false;
  mcpOAuthCredentialsPending: boolean = false;
  mcpOAuthCredentialsEditing: boolean = false;

  // Inbound A2A OAuth credentials state (for this agent's own A2A endpoint)
  inboundA2aOAuthTokenEndpoint: string = '';
  inboundA2aOAuthUsername: string = '';
  inboundA2aOAuthPassword: string = '';
  inboundA2aOAuthPasswordVisible: boolean = false;
  inboundA2aOAuthSaving: boolean = false;
  inboundA2aOAuthEditing: boolean = false;
  inboundA2aOAuthError: string | null = null;

  // Inbound A2A Bearer Token state (self-deployed agents only). Enforcement is
  // done by the deploy-time runtime authorizer, not this app — the UI only
  // stores a reference + optional expiry and states this honestly.
  inboundA2aBearerTokenValue: string = '';
  inboundA2aBearerTokenExpiry: string = '';
  inboundA2aBearerTokenSaving: boolean = false;
  inboundA2aBearerTokenEditing: boolean = false;
  inboundA2aBearerTokenVisible: boolean = false;

  // Invocation Notification hook state (independent of A2A above — fires a
  // fire-and-forget webhook every time this agent is invoked with a real user
  // prompt). See spec: a2a-invocation-notify-hook.
  notifyEditorError: string | null = null;
  notifyBearerTokenValue: string = '';
  notifyBearerTokenExpiry: string = '';
  notifyBearerTokenSaving: boolean = false;
  notifyBearerTokenEditing: boolean = false;
  notifyBearerTokenVisible: boolean = false;

  // ============================================
  // OAuth 2.0 client-credentials (machine-to-machine) form state
  // ============================================
  //
  // One block per auth surface, matching the existing per-surface state above.
  // The client secret is held here only until it is written to SSM, then
  // cleared; the config keeps a non-secret reference (token URL, scope,
  // audience, parameter path).

  // (A) Inbound auth for this agent's own endpoint
  inboundM2mClientId: string = '';
  inboundM2mClientSecret: string = '';
  inboundM2mTokenUrl: string = '';
  inboundM2mScope: string = '';
  inboundM2mAudience: string = '';
  inboundM2mSecretVisible: boolean = false;
  inboundM2mSaving: boolean = false;
  inboundM2mEditing: boolean = false;

  // (B) Outbound auth per external A2A peer
  a2aM2mClientId: string = '';
  a2aM2mClientSecret: string = '';
  a2aM2mTokenUrl: string = '';
  a2aM2mScope: string = '';
  a2aM2mAudience: string = '';
  a2aM2mSecretVisible: boolean = false;
  a2aM2mSaving: boolean = false;
  a2aM2mEditing: boolean = false;
  a2aM2mPending: boolean = false;

  // (C) MCP server auth
  mcpM2mClientId: string = '';
  mcpM2mClientSecret: string = '';
  mcpM2mTokenUrl: string = '';
  mcpM2mScope: string = '';
  mcpM2mAudience: string = '';
  mcpM2mSecretVisible: boolean = false;
  mcpM2mSaving: boolean = false;
  mcpM2mEditing: boolean = false;
  mcpM2mPending: boolean = false;

  // (D) Invocation-notification webhook auth
  notifyM2mClientId: string = '';
  notifyM2mClientSecret: string = '';
  notifyM2mTokenUrl: string = '';
  notifyM2mScope: string = '';
  notifyM2mAudience: string = '';
  notifyM2mSecretVisible: boolean = false;
  notifyM2mSaving: boolean = false;
  notifyM2mEditing: boolean = false;

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
    // Pending-activation warnings belong to the agent currently being edited, so
    // reset them whenever a different agent is loaded into the form.
    this.a2aCredentialsAwaitingAgentSave.clear();
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
      // Hosting, protocol, and endpoint are independent. Seed each from the
      // record, falling back to what the old is_a2a flag implied.
      this.editingAgent.agent_hosting =
        this.editingAgent.agent_hosting || (this.editingAgent.is_a2a ? 'external' : 'adfabric');
      this.editingAgent.agent_protocol =
        this.editingAgent.agent_protocol || (this.editingAgent.is_a2a ? 'a2a' : 'http');
      this.editingAgent.agent_endpoint = this.editingAgent.agent_endpoint || '';
      this.editingAgent.a2a_auth_type = this.editingAgent.a2a_auth_type || 'none';
      this.editingAgent.runtime_arn = this.editingAgent.runtime_arn || '';
      this.editingAgent.knowledge_base = this.editingAgent.knowledge_base || '';
      this.editingAgent.instructions = this.editingAgent.instructions || '';
      this.editingAgent.notify_on_invocation = this.editingAgent.notify_on_invocation || undefined;

      if (!this.editingAgent.model_inputs || Object.keys(this.editingAgent.model_inputs).length === 0) {
        this.editingAgent.model_inputs = {
          default: { model_id: 'global.anthropic.claude-sonnet-5', max_tokens: 8000 }
        };
      }

      if (!this.isNew && this.agent.agent_name) {
        this.loadVisualizationMappings(this.agent.agent_name);
        this.loadInstructionVersions(this.agent.agent_name);
      }
    } else {
      this.editingAgent = this.createEmptyAgent();
      this.visualizationMappings = null;
    }

    this.instructionVersions = [];
    this.selectedInstructionVersionSk = INSTRUCTION_LIVE_SK;
    this.liveInstructionVersion = null;
    this.liveInstructionUpdatedAt = null;
    this.isLoadingInstructionVersions = false;
    this.isLoadingInstructionVersion = false;
    this.instructionVersionError = null;

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
    this.notifyEditorError = null;
    this.notifyBearerTokenValue = '';
    this.notifyBearerTokenExpiry = '';
    this.notifyBearerTokenEditing = false;
    this.clearInboundM2mFormState();
    this.clearNotifyM2mFormState();
    this.inboundM2mSaving = false;
    this.notifyM2mSaving = false;
    // Seed the non-secret fields from the config so the forms show what is
    // stored without reading SSM. Secrets stay blank until explicitly updated.
    const inboundM2m = this.editingAgent.a2a_oauth_client_credentials;
    this.inboundM2mTokenUrl = inboundM2m?.tokenUrl || '';
    this.inboundM2mScope = inboundM2m?.scope || '';
    this.inboundM2mAudience = inboundM2m?.audience || '';
    const notifyM2m = this.editingAgent.notify_on_invocation?.oauth_client_credentials;
    this.notifyM2mTokenUrl = notifyM2m?.tokenUrl || '';
    this.notifyM2mScope = notifyM2m?.scope || '';
    this.notifyM2mAudience = notifyM2m?.audience || '';
    this.notifyBearerTokenSaving = false;
    this.notifyBearerTokenVisible = false;
    this.runtimeArnDropdownOpen = false;
    this.runtimeArnFilter = '';
    this.kbDropdownOpen = false;
    this.kbFilterText = '';
    this.loadKnowledgeBases();
    this.cdr.markForCheck();
  }

  // ============================================
  // Instruction Versions
  // ============================================

  private async loadInstructionVersions(agentName: string): Promise<void> {
    this.isLoadingInstructionVersions = true;
    this.instructionVersionError = null;
    this.cdr.markForCheck();
    try {
      const history = await this.agentDynamoDBService.getInstructionVersionHistory(agentName);
      this.instructionVersions = history.versions;
      this.liveInstructionVersion = history.live.version;
      this.liveInstructionUpdatedAt = history.live.updatedAt || null;
      this.selectedInstructionVersionSk = INSTRUCTION_LIVE_SK;
    } catch (error) {
      console.error('Error loading instruction versions:', error);
      this.instructionVersionError = 'Could not load version history.';
    } finally {
      this.isLoadingInstructionVersions = false;
      this.cdr.markForCheck();
    }
  }

  /**
   * Load the selected version's text into the editor.
   *
   * Assigns onto `editingAgent` rather than reassigning the `agent` input, since
   * that would re-run initializeForm() and discard the selection.
   */
  async onInstructionVersionChange(sk: string): Promise<void> {
    const agentName = this.editingAgent.agent_name;
    if (!agentName || !sk) return;

    this.selectedInstructionVersionSk = sk;
    this.isLoadingInstructionVersion = true;
    this.instructionVersionError = null;
    this.cdr.markForCheck();

    try {
      const content = await this.agentDynamoDBService.getAgentInstructionsAtVersion(agentName, sk);
      if (content === null) {
        this.instructionVersionError = 'That version could not be loaded.';
        return;
      }
      this.editingAgent.instructions = content;
      // Drop the memoised render so Preview reflects the version just loaded.
      this._lastRenderedInstructions = '';
      this.renderedInstructionsHtml = null;
      if (this.isMarkdownPreview) this.renderInstructionsMarkdown();
    } catch (error) {
      console.error('Error loading instruction version:', error);
      this.instructionVersionError = 'That version could not be loaded.';
    } finally {
      this.isLoadingInstructionVersion = false;
      this.cdr.detectChanges();
    }
  }

  /** True when the editor is showing a snapshot rather than the live record. */
  get isViewingHistoricInstructionVersion(): boolean {
    return this.selectedInstructionVersionSk !== INSTRUCTION_LIVE_SK;
  }

  get liveInstructionOptionLabel(): string {
    const when = this.formatInstructionTimestamp(this.liveInstructionUpdatedAt);
    if (this.liveInstructionVersion !== null) {
      return `Current — v${this.liveInstructionVersion}${when ? ` · ${when}` : ''}`;
    }
    // No version attribute on the live record, so it cannot be tied to a
    // snapshot. Say so instead of implying it is one.
    return `Current — not versioned${when ? ` · ${when}` : ''}`;
  }

  instructionVersionLabel(version: InstructionVersionSummary): string {
    const parts = [`v${version.version}`];
    const when = this.formatInstructionTimestamp(version.updatedAt);
    if (when) parts.push(when);
    if (version.author) parts.push(version.author);
    if (typeof version.contentLength === 'number') {
      parts.push(`${Math.max(1, Math.round(version.contentLength / 1024))} KB`);
    }
    return parts.join(' · ');
  }

  get selectedInstructionVersionLabel(): string {
    const match = this.instructionVersions.find(v => v.sk === this.selectedInstructionVersionSk);
    return match ? `v${match.version}` : 'this version';
  }

  private formatInstructionTimestamp(iso: string | null | undefined): string {
    if (!iso) return '';
    const parsed = new Date(iso);
    if (Number.isNaN(parsed.getTime())) return '';
    return parsed.toLocaleString(undefined, {
      year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
    });
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
        default: { model_id: 'global.anthropic.claude-sonnet-5', max_tokens: 8000 }
      },
      agent_tools: [], injectable_values: {}, instructions: '', color: '#6842ff',
      mcp_servers: [], external_agent_configs: [], runtime_arn: '', knowledge_base: '',
      agent_hosting: 'adfabric', agent_protocol: 'http', agent_endpoint: '',
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
    }

    // Validate external A2A agent ARNs are non-empty
    if (this.editingAgent.external_agent_configs?.length) {
      const emptyArnAgents = this.editingAgent.external_agent_configs
        .filter(agent => !agent.arn?.trim());
      if (emptyArnAgents.length > 0) {
        this.validationErrors.set('external_agent_arn', `${emptyArnAgents.length} external A2A agent(s) missing ARN`);
      }
    }

    // Validate the invocation-notification endpoint, if configured
    if (this.editingAgent.notify_on_invocation) {
      const endpoint = this.editingAgent.notify_on_invocation.endpoint?.trim();
      if (!endpoint) {
        this.validationErrors.set('notify_endpoint', 'Endpoint URL is required when Invocation Notification is configured');
      } else if (!this.isValidHttpsUrl(endpoint)) {
        this.validationErrors.set('notify_endpoint', 'Endpoint URL must be a valid https:// URL');
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

  getDefaultModelInputs(): { model_id: string; max_tokens: number; top_p?: number } | null {
    if (!this.editingAgent.model_inputs) return null;
    if (this.editingAgent.model_inputs['default']) return this.editingAgent.model_inputs['default'];
    const keys = Object.keys(this.editingAgent.model_inputs);
    return keys.length > 0 ? this.editingAgent.model_inputs[keys[0]] : null;
  }

  // `temperature` is intentionally not an accepted field: the models used here
  // deprecated it, and passing it caused the request to be rejected (the model
  // returned a default placeholder instead of a real completion). Model
  // defaults are used instead.
  updateModelInput(field: 'model_id' | 'max_tokens' | 'top_p', value: string | number): void {
    if (!this.editingAgent.model_inputs) {
      this.editingAgent.model_inputs = {
        default: { model_id: 'global.anthropic.claude-sonnet-5', max_tokens: 8000 }
      };
    }
    let key = 'default';
    if (!this.editingAgent.model_inputs['default']) {
      const keys = Object.keys(this.editingAgent.model_inputs);
      if (keys.length > 0) { key = keys[0]; }
      else { this.editingAgent.model_inputs['default'] = { model_id: 'global.anthropic.claude-sonnet-5', max_tokens: 8000 }; }
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
    this.clearMcpM2mFormState();
    this.mcpM2mSaving = false;
    // Show the stored token URL/scope/audience without a round trip to SSM; the
    // client secret stays blank until the operator chooses to update it.
    this.mcpM2mTokenUrl = this.editingMcpServer?.oauthClientCredentials?.tokenUrl || '';
    this.mcpM2mScope = this.editingMcpServer?.oauthClientCredentials?.scope || '';
    this.mcpM2mAudience = this.editingMcpServer?.oauthClientCredentials?.audience || '';
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
    this.clearMcpM2mFormState();
    this.mcpM2mSaving = false;
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

    // Client-credentials entered but not yet written to SSM. Stop rather than
    // saving a server whose auth mode points at a parameter that was never
    // created, which would fail at connect time with no indication why.
    if (this.getMcpAuthType(this.editingMcpServer) === 'oauth_m2m' &&
        this.mcpM2mClientSecret.trim() &&
        !this.mcpM2mSaving) {
      this.mcpServerJsonError = 'Click "Save Credentials" to store the client credentials first.';
      this.cdr.markForCheck();
      return;
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
          this.editingMcpServer.authType = 'bearer';
          this.editingMcpServer.oauthClientCredentials = undefined;
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
      if (!['stdio', 'http', 'sse',"streamable_http"].includes(parsed.transport)) throw new Error('Transport must be one of: stdio, http, sse');
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
      endpoint: '',
      protocol: 'a2a',
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
    this.a2aOAuthClientId = '';
    this.a2aOAuthUsername = '';
    this.a2aOAuthPassword = '';
    this.a2aOAuthPasswordVisible = false;
    this.a2aOAuthCredentialsSaving = false;
    this.a2aOAuthCredentialsPending = false;
    this.a2aOAuthCredentialsEditing = false;
    this.clearA2aM2mFormState();
    this.a2aM2mSaving = false;
    // Show the stored token URL/scope/audience without a round trip to SSM; the
    // client secret stays blank until the operator chooses to update it.
    this.a2aM2mTokenUrl = this.editingA2aAgent?.oauthClientCredentials?.tokenUrl || '';
    this.a2aM2mScope = this.editingA2aAgent?.oauthClientCredentials?.scope || '';
    this.a2aM2mAudience = this.editingA2aAgent?.oauthClientCredentials?.audience || '';
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
    this.clearA2aM2mFormState();
    this.a2aM2mSaving = false;
    this.a2aEditorClosed.emit();
  }

  saveA2aAgentChanges(): void {
    if (!this.editingA2aAgent || this.editingA2aAgentIndex < 0) return;
    if (!this.editingA2aAgent.name?.trim()) { this.a2aEditorError = 'Agent name is required'; return; }
    const entryEndpoint = resolveEntryEndpoint(this.editingA2aAgent);
    if (entryEndpoint.kind === 'none') {
      this.a2aEditorError = entryEndpoint.value
        ? 'Endpoint must be an AgentCore runtime ARN (arn:...) or an absolute URL (https://...).'
        : 'Agent endpoint is required.';
      return;
    }

    // Client-credentials entered but not yet written to SSM. Stop rather than
    // saving an entry whose auth mode points at a parameter that was never
    // created, which would fail at invoke time with no indication why.
    if (this.getA2aAuthType(this.editingA2aAgent) === 'oauth_m2m' &&
        this.a2aM2mClientSecret.trim() &&
        !this.a2aM2mSaving) {
      this.a2aEditorError = 'Click "Save Credentials" to store the client credentials first.';
      this.cdr.markForCheck();
      return;
    }

    // If OAuth credentials were entered, store them in SSM before saving
    if (this.a2aOAuthUsername.trim() && this.a2aOAuthPassword.trim() && this.editingAgent.agent_name) {
      // The runtime needs the Cognito app client id to mint a bearer token, so
      // require it whenever OAuth credentials are being stored.
      if (!this.a2aOAuthClientId.trim()) {
        this.a2aEditorError = 'Auth Client ID is required when storing OAuth credentials.';
        return;
      }
      this.a2aOAuthCredentialsSaving = true;
      this.cdr.markForCheck();

      const credentialsJson = JSON.stringify({
        client_id: this.a2aOAuthClientId.trim(),
        username: this.a2aOAuthUsername.trim(),
        password: this.a2aOAuthPassword.trim()
      });
      this.agentDynamoDBService.storeA2AOAuthToken(
        this.editingAgent.agent_name,
        this.editingA2aAgent.id,
        credentialsJson
      ).then(ssmPath => {
        if (ssmPath && this.editingA2aAgent) {
          this.editingA2aAgent.oauthCredentials = { hasCredentials: true, ssmPath };
          this.editingA2aAgent.oauthToken = { hasToken: true, ssmPath };
          this.editingA2aAgent.oauthClientCredentials = undefined;
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

  /**
   * Record that an external entry's credential is in Parameter Store but its
   * config reference has not been persisted yet.
   */
  private markA2aCredentialAwaitingAgentSave(entry: ExternalAgentConfig): void {
    const key = entry.name?.trim() || entry.id;
    if (key) {
      this.a2aCredentialsAwaitingAgentSave.add(key);
    }
  }

  /**
   * Whether any external-agent credential has been stored but not yet activated
   * by saving the agent. Drives the warning banner; reflects real staged state
   * only — it is never set speculatively.
   */
  hasA2aCredentialsAwaitingAgentSave(): boolean {
    return this.a2aCredentialsAwaitingAgentSave.size > 0;
  }

  /** Entry names whose stored credential is not yet active, for display. */
  getA2aCredentialsAwaitingAgentSave(): string[] {
    return Array.from(this.a2aCredentialsAwaitingAgentSave).sort();
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

    // Validate the optional expiry before storing (SECURITY-05). Empty is
    // allowed (non-expiring); a provided value must parse as a valid date.
    const expiryRaw = this.a2aBearerTokenExpiry.trim();
    let expiresAt: string | undefined;
    if (expiryRaw) {
      const parsed = new Date(expiryRaw);
      if (isNaN(parsed.getTime())) {
        this.a2aEditorError = 'Expiry must be a valid date/time or left empty.';
        this.cdr.markForCheck();
        return;
      }
      expiresAt = parsed.toISOString();
    }

    this.a2aBearerTokenSaving = true;
    this.a2aEditorError = null;
    this.cdr.markForCheck();

    try {
      // Verbatim static token — stored as SecureString, never routed through
      // the Cognito/OAuth credential path (works against non-AWS peers).
      const ssmPath = await this.agentDynamoDBService.storeA2ABearerToken(
        this.editingAgent.agent_name,
        this.editingA2aAgent.id,
        this.a2aBearerTokenValue.trim()
      );

      this.editingA2aAgent.bearerToken = { hasToken: true, ssmPath: ssmPath || undefined, expiresAt };
      // The parameter now holds a static token, so a client-credentials
      // reference to the same path would misreport what is stored.
      this.editingA2aAgent.oauthClientCredentials = undefined;
      // Same staging gap as the OAuth path: token is stored, reference is not yet.
      this.markA2aCredentialAwaitingAgentSave(this.editingA2aAgent);
      this.a2aBearerTokenValue = '';
      this.a2aBearerTokenEditing = false;
      this.a2aBearerTokenPending = false;
    } catch (error: any) {
      // error.message never contains the token value (see storeA2ABearerToken).
      console.error('Error saving A2A bearer token:', error);
      this.a2aEditorError = error.message || 'Failed to store token.';
    } finally {
      this.a2aBearerTokenSaving = false;
      this.cdr.markForCheck();
    }
  }

  /** Remove the static Bearer Token from SSM for an A2A external agent */
  async removeA2aBearerToken(): Promise<void> {
    if (!this.editingA2aAgent || !this.editingAgent.agent_name) return;

    this.a2aBearerTokenSaving = true;
    this.a2aEditorError = null;
    this.cdr.markForCheck();

    try {
      await this.agentDynamoDBService.deleteA2ABearerToken(
        this.editingAgent.agent_name,
        this.editingA2aAgent.id
      );
      this.editingA2aAgent.bearerToken = undefined;
      // Same as the OAuth removal: the removal is only live once the agent saves.
      this.markA2aCredentialAwaitingAgentSave(this.editingA2aAgent);
      this.a2aBearerTokenPending = false;
      this.a2aBearerTokenEditing = false;
      this.a2aBearerTokenValue = '';
      this.a2aBearerTokenExpiry = '';
    } catch (error: any) {
      console.error('Error removing A2A bearer token:', error);
      this.a2aEditorError = error.message || 'Failed to remove token.';
    } finally {
      this.a2aBearerTokenSaving = false;
      this.cdr.markForCheck();
    }
  }

  /**
   * Pure display helper for Bearer Token expiry awareness (Req 5). Returns
   * true only when an expiry is set AND is in the past. No expiry set means
   * non-expiring (no warning). This reads the stored value only — it does not
   * fabricate a token-health status.
   */
  isBearerTokenExpired(expiresAt?: string): boolean {
    if (!expiresAt) return false;
    const t = new Date(expiresAt).getTime();
    if (isNaN(t)) return false;
    return t < Date.now();
  }

  // ============================================
  // Invocation Notification hook (independent of A2A above)
  // ============================================

  /**
   * Enable the notify_on_invocation section for this agent with sensible
   * defaults (Requirement 6.1/6.2). No-op if already enabled.
   */
  enableNotifyOnInvocation(): void {
    if (this.editingAgent.notify_on_invocation) return;
    this.editingAgent.notify_on_invocation = { endpoint: '', auth_type: 'none' };
    this.cdr.markForCheck();
  }

  /**
   * Remove the notify_on_invocation config entirely, cleaning up any stored
   * bearer token in SSM first.
   */
  async disableNotifyOnInvocation(): Promise<void> {
    if (!this.editingAgent.notify_on_invocation) return;
    // Both credential modes share the notify parameter path, so either one
    // being stored means there is a parameter to clean up.
    const hasStoredCredential =
      this.editingAgent.notify_on_invocation.bearer_token?.hasToken ||
      this.editingAgent.notify_on_invocation.oauth_client_credentials?.hasCredentials;
    if (hasStoredCredential && this.editingAgent.agent_name) {
      try {
        await this.agentDynamoDBService.deleteNotifyBearerToken(this.editingAgent.agent_name);
      } catch (err) {
        console.warn('⚠️ Failed to clean up notify credential on disable:', err);
      }
    }
    this.editingAgent.notify_on_invocation = undefined;
    this.notifyEditorError = null;
    this.notifyBearerTokenValue = '';
    this.notifyBearerTokenExpiry = '';
    this.notifyBearerTokenEditing = false;
    this.clearNotifyM2mFormState();
    this.cdr.markForCheck();
  }

  /** Set the auth type for the invocation-notification hook */
  setNotifyAuthType(type: 'none' | 'iam' | 'bearer' | 'oauth_m2m'): void {
    if (!this.editingAgent.notify_on_invocation) return;
    this.editingAgent.notify_on_invocation.auth_type = type;
    if (type !== 'bearer') {
      // Clear the in-memory pasted value only — a previously stored token
      // reference is left intact, matching the A2A bearer pattern.
      this.notifyBearerTokenValue = '';
      this.notifyBearerTokenExpiry = '';
      this.notifyBearerTokenEditing = false;
      this.notifyBearerTokenVisible = false;
    }
    if (type !== 'oauth_m2m') {
      this.clearNotifyM2mFormState();
    }
    this.notifyEditorError = null;
    this.cdr.markForCheck();
  }

  /**
   * Abandon an in-progress notify client-credentials edit, restoring the
   * displayed non-secret values from the stored reference.
   */
  cancelNotifyClientCredentialsEdit(): void {
    const stored = this.editingAgent.notify_on_invocation?.oauth_client_credentials;
    this.clearNotifyM2mFormState();
    this.notifyM2mTokenUrl = stored?.tokenUrl || '';
    this.notifyM2mScope = stored?.scope || '';
    this.notifyM2mAudience = stored?.audience || '';
    this.notifyEditorError = null;
    this.cdr.markForCheck();
  }

  /** Clear the notify client-credentials form fields, leaving any stored reference. */
  private clearNotifyM2mFormState(): void {
    this.notifyM2mEditing = false;
    this.notifyM2mClientId = '';
    this.notifyM2mClientSecret = '';
    this.notifyM2mTokenUrl = '';
    this.notifyM2mScope = '';
    this.notifyM2mAudience = '';
    this.notifyM2mSecretVisible = false;
  }

  /**
   * Store the notification hook's client-credentials document in SSM (the same
   * notify parameter path the bearer mode uses) and record the reference.
   */
  async saveNotifyClientCredentials(): Promise<void> {
    if (!this.editingAgent.notify_on_invocation) return;
    if (!this.editingAgent.agent_name) {
      this.notifyEditorError = 'Agent name is required before saving credentials.';
      this.cdr.markForCheck();
      return;
    }

    const notifyConfig = this.editingAgent.notify_on_invocation;
    this.notifyM2mSaving = true;
    this.notifyEditorError = null;
    this.cdr.markForCheck();

    try {
      notifyConfig.oauth_client_credentials = await this.persistClientCredentials(
        {
          clientId: this.notifyM2mClientId,
          clientSecret: this.notifyM2mClientSecret,
          tokenUrl: this.notifyM2mTokenUrl,
          scope: this.notifyM2mScope,
          audience: this.notifyM2mAudience
        },
        doc => this.agentDynamoDBService.storeNotifyBearerToken(this.editingAgent.agent_name, doc)
      );
      // The notify path now holds a client-credentials document, so the static
      // bearer reference no longer describes it.
      notifyConfig.bearer_token = undefined;
      this.notifyM2mClientSecret = '';
      this.notifyM2mEditing = false;
    } catch (error: any) {
      console.error('Error saving notify client credentials:', error);
      this.notifyEditorError = error.message || 'Failed to store credentials.';
    } finally {
      this.notifyM2mSaving = false;
      this.cdr.markForCheck();
    }
  }

  /** Load the notification hook's stored client-credentials document for an update. */
  async beginNotifyClientCredentialsUpdate(): Promise<void> {
    this.notifyM2mEditing = true;
    this.notifyM2mClientSecret = '';
    this.notifyEditorError = null;
    this.cdr.markForCheck();

    if (!this.editingAgent.agent_name) return;

    const existing = await this.prefillClientCredentialsForm(
      () => this.agentDynamoDBService.getNotifyBearerToken(this.editingAgent.agent_name)
    );
    const stored = this.editingAgent.notify_on_invocation?.oauth_client_credentials;
    this.notifyM2mClientId = existing.clientId ?? this.notifyM2mClientId;
    this.notifyM2mTokenUrl = existing.tokenUrl ?? stored?.tokenUrl ?? '';
    this.notifyM2mScope = existing.scope ?? stored?.scope ?? '';
    this.notifyM2mAudience = existing.audience ?? stored?.audience ?? '';
    this.cdr.markForCheck();
  }

  /** Remove the notification hook's client-credentials document from SSM. */
  async removeNotifyClientCredentials(): Promise<void> {
    if (!this.editingAgent.notify_on_invocation || !this.editingAgent.agent_name) return;

    const notifyConfig = this.editingAgent.notify_on_invocation;
    this.notifyM2mSaving = true;
    this.notifyEditorError = null;
    this.cdr.markForCheck();

    try {
      await this.agentDynamoDBService.deleteNotifyBearerToken(this.editingAgent.agent_name);
      if (notifyConfig.oauth_client_credentials?.ssmPath) {
        clearClientCredentialsTokenCache(notifyConfig.oauth_client_credentials.ssmPath);
      }
      notifyConfig.oauth_client_credentials = undefined;
      this.clearNotifyM2mFormState();
    } catch (error: any) {
      console.error('Error removing notify client credentials:', error);
      this.notifyEditorError = error.message || 'Failed to remove credentials.';
    } finally {
      this.notifyM2mSaving = false;
      this.cdr.markForCheck();
    }
  }

  /** Basic https:// URL validation shared by validate() and the endpoint field's blur handler. */
  private isValidHttpsUrl(value: string): boolean {
    try {
      const url = new URL(value);
      return url.protocol === 'https:';
    } catch {
      return false;
    }
  }

  /** Validate the endpoint field on blur and set/clear the field-level error. */
  validateNotifyEndpoint(): void {
    const endpoint = this.editingAgent.notify_on_invocation?.endpoint?.trim();
    if (!endpoint) {
      this.validationErrors.delete('notify_endpoint');
      return;
    }
    if (!this.isValidHttpsUrl(endpoint)) {
      this.validationErrors.set('notify_endpoint', 'Endpoint URL must be a valid https:// URL');
    } else {
      this.validationErrors.delete('notify_endpoint');
    }
  }

  /** Save a bearer token to SSM for the invocation-notification hook. */
  async saveNotifyBearerToken(): Promise<void> {
    if (!this.editingAgent.notify_on_invocation || !this.notifyBearerTokenValue.trim() || !this.editingAgent.agent_name) return;

    const expiryRaw = this.notifyBearerTokenExpiry.trim();
    let expiresAt: string | undefined;
    if (expiryRaw) {
      const parsed = new Date(expiryRaw);
      if (isNaN(parsed.getTime())) {
        this.notifyEditorError = 'Expiry must be a valid date/time or left empty.';
        this.cdr.markForCheck();
        return;
      }
      expiresAt = parsed.toISOString();
    }

    this.notifyBearerTokenSaving = true;
    this.notifyEditorError = null;
    this.cdr.markForCheck();

    try {
      const ssmPath = await this.agentDynamoDBService.storeNotifyBearerToken(
        this.editingAgent.agent_name,
        this.notifyBearerTokenValue.trim()
      );

      this.editingAgent.notify_on_invocation.bearer_token = { hasToken: true, ssmPath: ssmPath || undefined, expiresAt };
      // The notify parameter now holds a static token, so a client-credentials
      // reference to the same path would misreport what is stored.
      this.editingAgent.notify_on_invocation.oauth_client_credentials = undefined;
      this.notifyBearerTokenValue = '';
      this.notifyBearerTokenEditing = false;
    } catch (error: any) {
      // error.message never contains the token value (see storeNotifyBearerToken).
      console.error('Error saving notify bearer token:', error);
      this.notifyEditorError = error.message || 'Failed to store token.';
    } finally {
      this.notifyBearerTokenSaving = false;
      this.cdr.markForCheck();
    }
  }

  /** Remove the stored bearer token for the invocation-notification hook. */
  async removeNotifyBearerToken(): Promise<void> {
    if (!this.editingAgent.notify_on_invocation || !this.editingAgent.agent_name) return;

    this.notifyBearerTokenSaving = true;
    this.notifyEditorError = null;
    this.cdr.markForCheck();

    try {
      await this.agentDynamoDBService.deleteNotifyBearerToken(this.editingAgent.agent_name);
      this.editingAgent.notify_on_invocation.bearer_token = undefined;
      this.notifyBearerTokenEditing = false;
      this.notifyBearerTokenValue = '';
      this.notifyBearerTokenExpiry = '';
    } catch (error: any) {
      console.error('Error removing notify bearer token:', error);
      this.notifyEditorError = error.message || 'Failed to remove token.';
    } finally {
      this.notifyBearerTokenSaving = false;
      this.cdr.markForCheck();
    }
  }

  /** Generate a unique ID for an external agent config */
  private generateExternalAgentId(): string {
    return this.generateA2aAgentId();
  }

  /** Save A2A OAuth credentials (client id / username / password) to SSM as JSON */
  async saveA2aOAuthCredentials(): Promise<void> {
    if (!this.editingA2aAgent || !this.editingAgent.agent_name) return;

    const clientId = this.a2aOAuthClientId.trim();
    const username = this.a2aOAuthUsername.trim();
    const password = this.a2aOAuthPassword.trim();

    // All three are required to mint a Cognito bearer token at runtime.
    const missing: string[] = [];
    if (!clientId) missing.push('Auth Client ID');
    if (!username) missing.push('Username');
    if (!password) missing.push('Password');
    if (missing.length > 0) {
      this.a2aEditorError = `Please fill in: ${missing.join(', ')}.`;
      this.cdr.markForCheck();
      return;
    }

    this.a2aOAuthCredentialsSaving = true;
    this.a2aEditorError = null;
    this.cdr.markForCheck();

    try {
      const credentialsJson = JSON.stringify({ client_id: clientId, username, password });
      const ssmPath = await this.agentDynamoDBService.storeA2AOAuthToken(
        this.editingAgent.agent_name,
        this.editingA2aAgent.id,
        credentialsJson
      );

      this.editingA2aAgent.oauthCredentials = { hasCredentials: true, ssmPath: ssmPath || undefined };
      this.editingA2aAgent.oauthToken = { hasToken: true, ssmPath: ssmPath || undefined };
      // The parameter now holds a username/password document, so a
      // client-credentials reference to the same path would misreport what is
      // stored.
      this.editingA2aAgent.oauthClientCredentials = undefined;
      // The secret is now in Parameter Store, but the entry that points at it is
      // still only staged on editingAgent — flag it until the agent is saved.
      this.markA2aCredentialAwaitingAgentSave(this.editingA2aAgent);
      this.a2aOAuthClientId = '';
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
      // The parameter is gone but the persisted config may still reference it, so
      // the agent must be saved for the removal to take effect too.
      this.markA2aCredentialAwaitingAgentSave(this.editingA2aAgent);
      this.a2aOAuthCredentialsPending = false;
      this.a2aOAuthCredentialsEditing = false;
      this.a2aOAuthClientId = '';
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

  /**
   * Store the external A2A peer's client-credentials document in SSM (the same
   * outbound parameter path the username/password mode uses) and record the
   * reference on the entry.
   */
  async saveA2aClientCredentials(): Promise<void> {
    if (!this.editingA2aAgent || !this.editingAgent.agent_name) return;

    const entry = this.editingA2aAgent;
    this.a2aM2mSaving = true;
    this.a2aEditorError = null;
    this.cdr.markForCheck();

    try {
      entry.oauthClientCredentials = await this.persistClientCredentials(
        {
          clientId: this.a2aM2mClientId,
          clientSecret: this.a2aM2mClientSecret,
          tokenUrl: this.a2aM2mTokenUrl,
          scope: this.a2aM2mScope,
          audience: this.a2aM2mAudience
        },
        doc => this.agentDynamoDBService.storeA2AOAuthToken(
          this.editingAgent.agent_name, entry.id, doc
        )
      );
      entry.authType = 'oauth_m2m';
      // Both modes share the parameter path, so the username/password and static
      // bearer references must not keep pointing at a document they no longer describe.
      entry.oauthCredentials = undefined;
      entry.oauthToken = undefined;
      entry.bearerToken = undefined;
      // Same staging gap as the other credential modes: the secret is in
      // Parameter Store, the entry that points at it is not persisted yet.
      this.markA2aCredentialAwaitingAgentSave(entry);
      this.a2aM2mClientSecret = '';
      this.a2aM2mEditing = false;
      this.a2aM2mPending = false;
    } catch (error: any) {
      console.error('Error saving A2A client credentials:', error);
      this.a2aEditorError = error.message || 'Failed to store credentials.';
    } finally {
      this.a2aM2mSaving = false;
      this.cdr.markForCheck();
    }
  }

  /** Load the stored external A2A client-credentials document for an update. */
  async beginA2aClientCredentialsUpdate(): Promise<void> {
    if (!this.editingA2aAgent) return;
    const entry = this.editingA2aAgent;

    this.a2aM2mEditing = true;
    this.a2aM2mClientSecret = '';
    this.a2aEditorError = null;
    this.cdr.markForCheck();

    if (!this.editingAgent.agent_name) return;

    const existing = await this.prefillClientCredentialsForm(
      () => this.agentDynamoDBService.getA2AOAuthToken(this.editingAgent.agent_name, entry.id)
    );
    this.a2aM2mClientId = existing.clientId ?? this.a2aM2mClientId;
    this.a2aM2mTokenUrl = existing.tokenUrl ?? entry.oauthClientCredentials?.tokenUrl ?? '';
    this.a2aM2mScope = existing.scope ?? entry.oauthClientCredentials?.scope ?? '';
    this.a2aM2mAudience = existing.audience ?? entry.oauthClientCredentials?.audience ?? '';
    this.cdr.markForCheck();
  }

  /** Remove the external A2A peer's client-credentials document from SSM. */
  async removeA2aClientCredentials(): Promise<void> {
    if (!this.editingA2aAgent || !this.editingAgent.agent_name) return;

    const entry = this.editingA2aAgent;
    this.a2aM2mSaving = true;
    this.a2aEditorError = null;
    this.cdr.markForCheck();

    try {
      await this.agentDynamoDBService.deleteA2AOAuthToken(this.editingAgent.agent_name, entry.id);
      if (entry.oauthClientCredentials?.ssmPath) {
        clearClientCredentialsTokenCache(entry.oauthClientCredentials.ssmPath);
      }
      entry.oauthClientCredentials = undefined;
      this.markA2aCredentialAwaitingAgentSave(entry);
      this.clearA2aM2mFormState();
    } catch (error: any) {
      console.error('Error removing A2A client credentials:', error);
      this.a2aEditorError = error.message || 'Failed to remove credentials.';
    } finally {
      this.a2aM2mSaving = false;
      this.cdr.markForCheck();
    }
  }

  // ============================================
  // Inbound A2A OAuth Credential Methods
  // ============================================

  /**
   * Enter "update" mode for inbound A2A OAuth credentials.
   *
   * Pre-populates the client_id and username inputs from what's currently stored
   * in SSM so the user can change only the value they care about without having
   * to re-type the others. The password is intentionally left blank — SSM returns
   * it, but forcing the user to re-enter it is both safer and keeps the Save
   * button from firing a no-op write.
   *
   * On SSM read failure we still open the editor with blank fields so the user
   * isn't stuck.
   */
  async beginInboundA2aOAuthUpdate(): Promise<void> {
    this.inboundA2aOAuthError = null;
    this.inboundA2aOAuthTokenEndpoint = '';
    this.inboundA2aOAuthUsername = '';
    this.inboundA2aOAuthPassword = '';
    this.inboundA2aOAuthEditing = true;
    this.cdr.markForCheck();

    if (!this.editingAgent.agent_name) return;

    try {
      const existing = await this.agentDynamoDBService.getA2AInboundOAuthCredentials(
        this.editingAgent.agent_name
      );
      if (existing) {
        try {
          const parsed = JSON.parse(existing);
          this.inboundA2aOAuthTokenEndpoint = parsed.client_id || '';
          this.inboundA2aOAuthUsername = parsed.username || '';
          // Leave password blank — user must re-enter to confirm intent
          this.cdr.markForCheck();
        } catch (parseErr) {
          console.warn('Stored inbound A2A credentials are not valid JSON:', parseErr);
        }
      }
    } catch (err) {
      console.warn('Could not pre-populate inbound A2A credentials:', err);
    }
  }

  /** Save inbound A2A OAuth credentials (client_id, username, password) to SSM for this agent's own A2A endpoint */
  async saveInboundA2aOAuthCredentials(): Promise<void> {
    if (!this.editingAgent.agent_name) {
      this.inboundA2aOAuthError = 'Agent name is required before saving credentials.';
      return;
    }

    const clientId = this.inboundA2aOAuthTokenEndpoint.trim();
    const username = this.inboundA2aOAuthUsername.trim();
    const password = this.inboundA2aOAuthPassword.trim();

    // Surface missing fields instead of silently no-oping. The runtime
    // needs all three to acquire a Cognito bearer token.
    const missing: string[] = [];
    if (!clientId) missing.push('Auth Client ID');
    if (!username) missing.push('Username');
    if (!password) missing.push('Password');
    if (missing.length > 0) {
      this.inboundA2aOAuthError = `Please fill in: ${missing.join(', ')}. All three fields are required to update credentials.`;
      this.cdr.markForCheck();
      return;
    }

    this.inboundA2aOAuthSaving = true;
    this.inboundA2aOAuthError = null;
    this.cdr.markForCheck();

    try {
      const credentialsJson = JSON.stringify({
        client_id: clientId,
        username: username,
        password: password
      });
      const ssmPath = await this.agentDynamoDBService.storeA2AInboundOAuthCredentials(
        this.editingAgent.agent_name,
        credentialsJson
      );

      this.editingAgent.a2a_oauth_credentials = { hasCredentials: true, ssmPath: ssmPath || undefined };
      // The inbound parameter now holds a username/password document, so a
      // client-credentials reference to the same path would misreport what is
      // stored.
      this.editingAgent.a2a_oauth_client_credentials = undefined;
      this.inboundA2aOAuthTokenEndpoint = '';
      this.inboundA2aOAuthUsername = '';
      this.inboundA2aOAuthPassword = '';
      this.inboundA2aOAuthEditing = false;
    } catch (error: any) {
      console.error('Error saving inbound A2A OAuth credentials:', error);
      this.inboundA2aOAuthError = error.message || 'Failed to store credentials.';
    } finally {
      this.inboundA2aOAuthSaving = false;
      this.cdr.markForCheck();
    }
  }

  /** Remove inbound A2A OAuth credentials from SSM */
  async removeInboundA2aOAuthCredentials(): Promise<void> {
    if (!this.editingAgent.agent_name) return;

    this.inboundA2aOAuthSaving = true;
    this.inboundA2aOAuthError = null;
    this.cdr.markForCheck();

    try {
      await this.agentDynamoDBService.deleteA2AInboundOAuthCredentials(this.editingAgent.agent_name);
      this.editingAgent.a2a_oauth_credentials = undefined;
      this.inboundA2aOAuthEditing = false;
      this.inboundA2aOAuthTokenEndpoint = '';
      this.inboundA2aOAuthUsername = '';
      this.inboundA2aOAuthPassword = '';
    } catch (error: any) {
      console.error('Error removing inbound A2A OAuth credentials:', error);
      this.inboundA2aOAuthError = error.message || 'Failed to remove credentials.';
    } finally {
      this.inboundA2aOAuthSaving = false;
      this.cdr.markForCheck();
    }
  }

  /**
   * Select the inbound auth mode for this agent's own endpoint, clearing the
   * in-memory form fields of the modes not selected. Stored credential
   * references are left alone, matching the outbound editors.
   */
  setInboundAuthType(type: 'none' | 'oauth' | 'oauth_m2m' | 'iam' | 'bearer'): void {
    this.editingAgent.a2a_auth_type = type;
    this.inboundA2aOAuthError = null;

    if (type !== 'oauth') {
      this.inboundA2aOAuthEditing = false;
      this.inboundA2aOAuthTokenEndpoint = '';
      this.inboundA2aOAuthUsername = '';
      this.inboundA2aOAuthPassword = '';
    }
    if (type !== 'oauth_m2m') {
      this.clearInboundM2mFormState();
    }
    if (type !== 'bearer') {
      this.inboundA2aBearerTokenEditing = false;
      this.inboundA2aBearerTokenValue = '';
      this.inboundA2aBearerTokenExpiry = '';
    }
    this.cdr.markForCheck();
  }

  /**
   * Abandon an in-progress inbound client-credentials edit, restoring the
   * displayed non-secret values from the stored reference.
   */
  cancelInboundClientCredentialsEdit(): void {
    const stored = this.editingAgent.a2a_oauth_client_credentials;
    this.clearInboundM2mFormState();
    this.inboundM2mTokenUrl = stored?.tokenUrl || '';
    this.inboundM2mScope = stored?.scope || '';
    this.inboundM2mAudience = stored?.audience || '';
    this.inboundA2aOAuthError = null;
    this.cdr.markForCheck();
  }

  /** Clear the inbound client-credentials form fields, leaving any stored reference. */
  private clearInboundM2mFormState(): void {
    this.inboundM2mEditing = false;
    this.inboundM2mClientId = '';
    this.inboundM2mClientSecret = '';
    this.inboundM2mTokenUrl = '';
    this.inboundM2mScope = '';
    this.inboundM2mAudience = '';
    this.inboundM2mSecretVisible = false;
  }

  /**
   * Store this agent's inbound client-credentials document in SSM (the same
   * inbound parameter path the username/password mode uses) and record the
   * reference.
   */
  async saveInboundClientCredentials(): Promise<void> {
    if (!this.editingAgent.agent_name) {
      this.inboundA2aOAuthError = 'Agent name is required before saving credentials.';
      this.cdr.markForCheck();
      return;
    }

    this.inboundM2mSaving = true;
    this.inboundA2aOAuthError = null;
    this.cdr.markForCheck();

    try {
      this.editingAgent.a2a_oauth_client_credentials = await this.persistClientCredentials(
        {
          clientId: this.inboundM2mClientId,
          clientSecret: this.inboundM2mClientSecret,
          tokenUrl: this.inboundM2mTokenUrl,
          scope: this.inboundM2mScope,
          audience: this.inboundM2mAudience
        },
        doc => this.agentDynamoDBService.storeA2AInboundOAuthCredentials(
          this.editingAgent.agent_name, doc
        )
      );
      // The inbound path now holds a client-credentials document, so the
      // username/password and static-token references no longer describe it.
      this.editingAgent.a2a_oauth_credentials = undefined;
      this.editingAgent.a2a_bearer_token = undefined;
      this.inboundM2mClientSecret = '';
      this.inboundM2mEditing = false;
    } catch (error: any) {
      console.error('Error saving inbound client credentials:', error);
      this.inboundA2aOAuthError = error.message || 'Failed to store credentials.';
    } finally {
      this.inboundM2mSaving = false;
      this.cdr.markForCheck();
    }
  }

  /** Load this agent's stored inbound client-credentials document for an update. */
  async beginInboundClientCredentialsUpdate(): Promise<void> {
    this.inboundM2mEditing = true;
    this.inboundM2mClientSecret = '';
    this.inboundA2aOAuthError = null;
    this.cdr.markForCheck();

    if (!this.editingAgent.agent_name) return;

    const existing = await this.prefillClientCredentialsForm(
      () => this.agentDynamoDBService.getA2AInboundOAuthCredentials(this.editingAgent.agent_name)
    );
    const stored = this.editingAgent.a2a_oauth_client_credentials;
    this.inboundM2mClientId = existing.clientId ?? this.inboundM2mClientId;
    this.inboundM2mTokenUrl = existing.tokenUrl ?? stored?.tokenUrl ?? '';
    this.inboundM2mScope = existing.scope ?? stored?.scope ?? '';
    this.inboundM2mAudience = existing.audience ?? stored?.audience ?? '';
    this.cdr.markForCheck();
  }

  /** Remove this agent's inbound client-credentials document from SSM. */
  async removeInboundClientCredentials(): Promise<void> {
    if (!this.editingAgent.agent_name) return;

    this.inboundM2mSaving = true;
    this.inboundA2aOAuthError = null;
    this.cdr.markForCheck();

    try {
      await this.agentDynamoDBService.deleteA2AInboundOAuthCredentials(this.editingAgent.agent_name);
      const ssmPath = this.editingAgent.a2a_oauth_client_credentials?.ssmPath;
      if (ssmPath) {
        clearClientCredentialsTokenCache(ssmPath);
      }
      this.editingAgent.a2a_oauth_client_credentials = undefined;
      this.clearInboundM2mFormState();
    } catch (error: any) {
      console.error('Error removing inbound client credentials:', error);
      this.inboundA2aOAuthError = error.message || 'Failed to remove credentials.';
    } finally {
      this.inboundM2mSaving = false;
      this.cdr.markForCheck();
    }
  }

  // ============================================
  // Inbound A2A Bearer Token Methods (self-deployed agents only)
  // ============================================
  //
  // These persist a reference + optional expiry and store the verbatim token
  // in the inbound SSM SecureString path. They do NOT enforce inbound auth —
  // that is the deploy-time runtime authorizer's job (see deploy_external_agents.py).
  // The UI copy states this honestly and never claims a config is "enforced"
  // that the authorizer cannot validate.

  /** Save the inbound static Bearer Token to SSM for this agent's own A2A endpoint */
  async saveInboundA2aBearerToken(): Promise<void> {
    if (!this.editingAgent.agent_name) {
      this.inboundA2aOAuthError = 'Agent name is required before saving a token.';
      this.cdr.markForCheck();
      return;
    }
    if (!this.inboundA2aBearerTokenValue.trim()) return;

    // Validate optional expiry (SECURITY-05). Empty = non-expiring.
    const expiryRaw = this.inboundA2aBearerTokenExpiry.trim();
    let expiresAt: string | undefined;
    if (expiryRaw) {
      const parsed = new Date(expiryRaw);
      if (isNaN(parsed.getTime())) {
        this.inboundA2aOAuthError = 'Expiry must be a valid date/time or left empty.';
        this.cdr.markForCheck();
        return;
      }
      expiresAt = parsed.toISOString();
    }

    this.inboundA2aBearerTokenSaving = true;
    this.inboundA2aOAuthError = null;
    this.cdr.markForCheck();

    try {
      // Reuse the inbound SecureString path scheme/helper (Req 4.4).
      const ssmPath = await this.agentDynamoDBService.storeA2AInboundOAuthCredentials(
        this.editingAgent.agent_name,
        this.inboundA2aBearerTokenValue.trim()
      );

      this.editingAgent.a2a_bearer_token = { hasToken: true, ssmPath: ssmPath || undefined, expiresAt };
      // The inbound parameter now holds a static token, so a client-credentials
      // reference to the same path would misreport what is stored.
      this.editingAgent.a2a_oauth_client_credentials = undefined;
      this.inboundA2aBearerTokenValue = '';
      this.inboundA2aBearerTokenExpiry = '';
      this.inboundA2aBearerTokenEditing = false;
    } catch (error: any) {
      console.error('Error saving inbound A2A bearer token:', error);
      this.inboundA2aOAuthError = error.message || 'Failed to store token.';
    } finally {
      this.inboundA2aBearerTokenSaving = false;
      this.cdr.markForCheck();
    }
  }

  /** Remove the inbound static Bearer Token from SSM */
  async removeInboundA2aBearerToken(): Promise<void> {
    if (!this.editingAgent.agent_name) return;

    this.inboundA2aBearerTokenSaving = true;
    this.inboundA2aOAuthError = null;
    this.cdr.markForCheck();

    try {
      await this.agentDynamoDBService.deleteA2AInboundOAuthCredentials(this.editingAgent.agent_name);
      this.editingAgent.a2a_bearer_token = undefined;
      this.inboundA2aBearerTokenEditing = false;
      this.inboundA2aBearerTokenValue = '';
      this.inboundA2aBearerTokenExpiry = '';
    } catch (error: any) {
      console.error('Error removing inbound A2A bearer token:', error);
      this.inboundA2aOAuthError = error.message || 'Failed to remove token.';
    } finally {
      this.inboundA2aBearerTokenSaving = false;
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
      this.editingMcpServer.authType = 'bearer';
      // The parameter now holds a username/password document, so a
      // client-credentials reference to the same path would misreport what is
      // stored.
      this.editingMcpServer.oauthClientCredentials = undefined;
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

  /** Set the A2A auth type (none, oauth, oauth_m2m, iam or bearer) */
  setA2aAuthType(agent: ExternalAgentConfig, type: 'none' | 'oauth' | 'oauth_m2m' | 'iam' | 'bearer'): void {
    agent.authType = type;
    switch (type) {
      case 'none':
        agent.awsAuth = undefined;
        this.clearA2aBearerFormState();
        this.a2aOAuthUsername = '';
        this.a2aOAuthPassword = '';
        this.a2aOAuthCredentialsPending = false;
        this.a2aOAuthCredentialsEditing = false;
        this.clearA2aM2mFormState();
        break;
      case 'oauth':
        agent.awsAuth = undefined;
        // Switching away from bearer: clear the in-memory pasted value only —
        // a previously stored token reference is left intact (Req 1.5).
        this.clearA2aBearerFormState();
        this.clearA2aM2mFormState();
        if (!agent.oauthCredentials?.hasCredentials) {
          this.a2aOAuthCredentialsPending = true;
        }
        break;
      case 'oauth_m2m':
        // Client-credentials grant against the peer's own token endpoint — no
        // AWS identity and no Cognito user, so clear the IAM and
        // username/password state.
        agent.awsAuth = undefined;
        this.clearA2aBearerFormState();
        this.a2aOAuthUsername = '';
        this.a2aOAuthPassword = '';
        this.a2aOAuthCredentialsPending = false;
        this.a2aOAuthCredentialsEditing = false;
        if (!agent.oauthClientCredentials?.hasCredentials) {
          this.a2aM2mPending = true;
        }
        break;
      case 'iam':
        agent.awsAuth = { region: 'us-east-1', service: 'bedrock-agentcore' };
        this.clearA2aBearerFormState();
        this.clearA2aM2mFormState();
        this.a2aOAuthCredentialsPending = false;
        this.a2aOAuthCredentialsEditing = false;
        this.a2aOAuthUsername = '';
        this.a2aOAuthPassword = '';
        break;
      case 'bearer':
        // Verbatim static token — no AWS identity. Clear OAuth/IAM state.
        agent.awsAuth = undefined;
        this.a2aOAuthUsername = '';
        this.a2aOAuthPassword = '';
        this.a2aOAuthCredentialsPending = false;
        this.a2aOAuthCredentialsEditing = false;
        this.clearA2aM2mFormState();
        if (!agent.bearerToken?.hasToken) {
          this.a2aBearerTokenPending = true;
        }
        break;
    }
  }

  /**
   * Abandon an in-progress external A2A client-credentials edit, restoring the
   * displayed non-secret values from the stored reference.
   */
  cancelA2aClientCredentialsEdit(): void {
    const stored = this.editingA2aAgent?.oauthClientCredentials;
    this.clearA2aM2mFormState();
    this.a2aM2mTokenUrl = stored?.tokenUrl || '';
    this.a2aM2mScope = stored?.scope || '';
    this.a2aM2mAudience = stored?.audience || '';
    this.a2aEditorError = null;
    this.cdr.markForCheck();
  }

  /**
   * Clear the in-memory client-credentials fields for the external A2A editor
   * without touching a stored `oauthClientCredentials` reference, matching how
   * `clearA2aBearerFormState` treats a stored token.
   */
  private clearA2aM2mFormState(): void {
    this.a2aM2mPending = false;
    this.a2aM2mEditing = false;
    this.a2aM2mClientId = '';
    this.a2aM2mClientSecret = '';
    this.a2aM2mTokenUrl = '';
    this.a2aM2mScope = '';
    this.a2aM2mAudience = '';
    this.a2aM2mSecretVisible = false;
  }

  /**
   * Clear the in-memory bearer form fields (pasted value, expiry, edit/pending
   * flags) without touching a stored `bearerToken` reference. Used when
   * switching auth types (Req 1.5).
   */
  private clearA2aBearerFormState(): void {
    this.a2aBearerTokenPending = false;
    this.a2aBearerTokenEditing = false;
    this.a2aBearerTokenValue = '';
    this.a2aBearerTokenExpiry = '';
    this.a2aBearerTokenVisible = false;
  }

  // ============================================
  // External peer protocol / endpoint (independent of its auth mode)
  // ============================================

  /** Effective wire protocol for an external peer entry. */
  getEntryProtocol(entry: ExternalAgentConfig): AgentProtocol {
    return resolveEntryProtocol(entry);
  }

  /** Select the peer's wire protocol. Does not affect its endpoint or auth mode. */
  setEntryProtocol(entry: ExternalAgentConfig, protocol: AgentProtocol): void {
    entry.protocol = protocol;
    // Keep the deprecated flag consistent for consumers still reading it.
    entry.isA2A = protocol === 'a2a';
    this.cdr.markForCheck();
  }

  /** Current endpoint for display — `endpoint`, falling back to `arn`. */
  getEntryEndpoint(entry: ExternalAgentConfig): string {
    return resolveEntryEndpoint(entry).value;
  }

  /**
   * Record the peer's endpoint. An ARN is mirrored into `arn` because the
   * runtime's tool builder and the deployment scripts still read that field; a
   * URL has no ARN equivalent, so `arn` is cleared rather than left stale.
   */
  setEntryEndpoint(entry: ExternalAgentConfig, value: string): void {
    entry.endpoint = value;
    const classified = classifyEndpoint(value);
    entry.arn = classified.kind === 'arn' ? classified.value : '';
    this.cdr.markForCheck();
  }

  /** Plain-language description of how this peer will actually be invoked. */
  describeEntryInvocation(entry: ExternalAgentConfig): string {
    return describeInvocationPlan(planInvocation({
      endpoint: resolveEntryEndpoint(entry),
      protocol: this.getEntryProtocol(entry),
      authType: this.getA2aAuthType(entry),
      region: entry.awsAuth?.region || this.awsConfigService.getRegion()
    }));
  }

  /** Get the effective A2A auth type from the agent config */
  getA2aAuthType(agent: ExternalAgentConfig): 'none' | 'oauth' | 'oauth_m2m' | 'iam' | 'bearer' {
    if (agent.authType) return agent.authType;
    if (agent.awsAuth) return 'iam';
    if (agent.oauthClientCredentials?.hasCredentials) return 'oauth_m2m';
    if (agent.bearerToken?.hasToken) return 'bearer';
    if (agent.oauthToken?.hasToken || agent.oauthCredentials?.hasCredentials) return 'oauth';
    return 'none';
  }

  /**
   * Display label for an auth mode, used by the list-row badges so a new mode
   * cannot fall through to a neighbouring mode's label.
   */
  getAuthTypeLabel(type: string | undefined): string {
    switch (type) {
      case 'oauth': return 'OAuth';
      case 'oauth_m2m': return 'OAuth M2M';
      case 'iam':
      case 'aws_iam': return 'IAM';
      case 'bearer': return 'Bearer Token';
      default: return 'None';
    }
  }

  /**
   * Effective MCP auth mode. Older server records predate the `authType`
   * discriminator, so fall back to inferring it from whichever credential
   * field is populated.
   */
  getMcpAuthType(server: MCPServerConfig): 'none' | 'bearer' | 'aws_iam' | 'oauth_m2m' {
    if (server.authType) return server.authType;
    if (server.awsAuth) return 'aws_iam';
    if (server.oauthClientCredentials?.hasCredentials) return 'oauth_m2m';
    if (server.oauthToken?.hasToken) return 'bearer';
    return 'none';
  }

  /** True when the MCP OAuth (username/password) chip should read as selected. */
  isMcpOAuthSelected(server: MCPServerConfig): boolean {
    return this.getMcpAuthType(server) === 'bearer' || this.mcpOAuthCredentialsPending;
  }

  /** True when the MCP client-credentials chip should read as selected. */
  isMcpM2mSelected(server: MCPServerConfig): boolean {
    return this.getMcpAuthType(server) === 'oauth_m2m' || this.mcpM2mPending;
  }

  /** True when the MCP "None" chip should read as selected. */
  isMcpNoAuthSelected(server: MCPServerConfig): boolean {
    return this.getMcpAuthType(server) === 'none' &&
      !this.mcpOAuthCredentialsPending &&
      !this.mcpM2mPending;
  }

  // ============================================
  // Shared client-credentials persistence
  // ============================================

  /**
   * Validate the operator's client-credentials input, write the secret document
   * to SSM through `store`, and return the non-secret reference to record on
   * the config.
   *
   * `store` is the surface's existing SSM writer, so the document lands on the
   * parameter path that surface already uses. Throws on invalid input or a
   * failed write; neither the client secret nor the built document appears in
   * the thrown message.
   */
  private async persistClientCredentials(
    input: OAuthClientCredentialsInput,
    store: (document: string) => Promise<string | null>
  ): Promise<OAuthClientCredentialsRef> {
    const document = buildClientCredentialsDocument(input);
    const ssmPath = await store(document);

    // A token minted from the previous credentials would outlive this update,
    // so drop it and let the next call mint from what was just stored.
    if (ssmPath) {
      clearClientCredentialsTokenCache(ssmPath);
    }

    return {
      hasCredentials: true,
      ssmPath: ssmPath || undefined,
      tokenUrl: input.tokenUrl.trim(),
      scope: input.scope?.trim() || undefined,
      audience: input.audience?.trim() || undefined
    };
  }

  /**
   * Load a stored client-credentials document into the given form fields for an
   * update. The client secret is deliberately left blank so the operator has to
   * re-enter it, which also prevents an accidental Save from rewriting the
   * parameter with a value nobody confirmed.
   */
  private async prefillClientCredentialsForm(
    read: () => Promise<string | null>
  ): Promise<Partial<OAuthClientCredentialsInput>> {
    try {
      const doc = parseClientCredentialsDocument(await read());
      if (!doc) return {};
      return {
        clientId: doc.client_id,
        tokenUrl: doc.token_url,
        scope: doc.scope || '',
        audience: doc.audience || ''
      };
    } catch (err) {
      console.warn('Could not pre-populate client-credentials form:', err);
      return {};
    }
  }

  // ============================================
  // OAuth Token Management
  // ============================================

  /** Switch authentication type for an MCP server */
  setMcpAuthType(server: MCPServerConfig, authType: 'none' | 'bearer' | 'aws_iam' | 'oauth_m2m'): void {
    // Record the choice explicitly. 'bearer' and 'oauth_m2m' both end up sending
    // an Authorization header, so the mode can no longer be inferred from which
    // credential field happens to be set.
    server.authType = authType;
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
        this.clearMcpM2mFormState();
        break;
      case 'bearer':
        server.awsAuth = undefined;
        this.clearMcpM2mFormState();
        if (!server.oauthToken?.hasToken) {
          this.mcpOAuthCredentialsPending = true;
        }
        break;
      case 'oauth_m2m':
        server.awsAuth = undefined;
        this.mcpBearerTokenPending = false;
        this.mcpBearerTokenEditing = false;
        this.mcpBearerTokenValue = '';
        this.mcpOAuthUsername = '';
        this.mcpOAuthPassword = '';
        this.mcpOAuthCredentialsPending = false;
        this.mcpOAuthCredentialsEditing = false;
        if (!server.oauthClientCredentials?.hasCredentials) {
          this.mcpM2mPending = true;
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
        this.clearMcpM2mFormState();
        break;
    }
    this.updateMcpServerJsonFromForm();
  }

  /**
   * Abandon an in-progress MCP client-credentials edit, restoring the displayed
   * non-secret values from the stored reference.
   */
  cancelMcpClientCredentialsEdit(): void {
    const stored = this.editingMcpServer?.oauthClientCredentials;
    this.clearMcpM2mFormState();
    this.mcpM2mTokenUrl = stored?.tokenUrl || '';
    this.mcpM2mScope = stored?.scope || '';
    this.mcpM2mAudience = stored?.audience || '';
    this.mcpServerJsonError = null;
    this.cdr.markForCheck();
  }

  /** Clear the MCP client-credentials form fields, leaving any stored reference. */
  private clearMcpM2mFormState(): void {
    this.mcpM2mPending = false;
    this.mcpM2mEditing = false;
    this.mcpM2mClientId = '';
    this.mcpM2mClientSecret = '';
    this.mcpM2mTokenUrl = '';
    this.mcpM2mScope = '';
    this.mcpM2mAudience = '';
    this.mcpM2mSecretVisible = false;
  }

  /**
   * Store the MCP server's client-credentials document in SSM (same parameter
   * path the username/password mode uses) and record the reference.
   */
  async saveMcpClientCredentials(): Promise<void> {
    if (!this.editingMcpServer || !this.editingAgent.agent_name) return;

    const server = this.editingMcpServer;
    this.mcpM2mSaving = true;
    this.mcpServerJsonError = null;
    this.cdr.markForCheck();

    try {
      server.oauthClientCredentials = await this.persistClientCredentials(
        {
          clientId: this.mcpM2mClientId,
          clientSecret: this.mcpM2mClientSecret,
          tokenUrl: this.mcpM2mTokenUrl,
          scope: this.mcpM2mScope,
          audience: this.mcpM2mAudience
        },
        doc => this.agentDynamoDBService.storeMcpOAuthToken(
          this.editingAgent.agent_name, server.id, doc
        )
      );
      server.authType = 'oauth_m2m';
      // The username/password reference would otherwise still point at the same
      // parameter, which now holds a client-credentials document.
      server.oauthToken = undefined;
      this.mcpM2mClientSecret = '';
      this.mcpM2mEditing = false;
      this.mcpM2mPending = false;
      this.updateMcpServerJsonFromForm();
    } catch (error: any) {
      console.error('Error saving MCP client credentials:', error);
      this.mcpServerJsonError = error.message || 'Failed to store credentials.';
    } finally {
      this.mcpM2mSaving = false;
      this.cdr.markForCheck();
    }
  }

  /** Load the stored MCP client-credentials document for an update. */
  async beginMcpClientCredentialsUpdate(): Promise<void> {
    if (!this.editingMcpServer) return;
    const server = this.editingMcpServer;

    this.mcpM2mEditing = true;
    this.mcpM2mClientSecret = '';
    this.mcpServerJsonError = null;
    this.cdr.markForCheck();

    if (!this.editingAgent.agent_name) return;

    const existing = await this.prefillClientCredentialsForm(
      () => this.agentDynamoDBService.getMcpOAuthToken(this.editingAgent.agent_name, server.id)
    );
    this.mcpM2mClientId = existing.clientId ?? this.mcpM2mClientId;
    this.mcpM2mTokenUrl = existing.tokenUrl ?? server.oauthClientCredentials?.tokenUrl ?? '';
    this.mcpM2mScope = existing.scope ?? server.oauthClientCredentials?.scope ?? '';
    this.mcpM2mAudience = existing.audience ?? server.oauthClientCredentials?.audience ?? '';
    this.cdr.markForCheck();
  }

  /** Remove the MCP server's client-credentials document from SSM. */
  async removeMcpClientCredentials(server: MCPServerConfig): Promise<void> {
    if (!server || !this.editingAgent.agent_name) return;

    this.mcpM2mSaving = true;
    this.mcpServerJsonError = null;
    this.cdr.markForCheck();

    try {
      await this.agentDynamoDBService.deleteMcpOAuthToken(this.editingAgent.agent_name, server.id);
      if (server.oauthClientCredentials?.ssmPath) {
        clearClientCredentialsTokenCache(server.oauthClientCredentials.ssmPath);
      }
      server.oauthClientCredentials = undefined;
      this.clearMcpM2mFormState();
      this.updateMcpServerJsonFromForm();
    } catch (error: any) {
      console.error('Error removing MCP client credentials:', error);
      this.mcpServerJsonError = error.message || 'Failed to remove credentials.';
    } finally {
      this.mcpM2mSaving = false;
      this.cdr.markForCheck();
    }
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
      // The parameter now holds a static token, so a client-credentials
      // reference to the same path would misreport what is stored.
      this.editingMcpServer.oauthClientCredentials = undefined;
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

  // ============================================
  // Hosting / protocol / endpoint (three independent settings)
  // ============================================

  /**
   * True when the agent runs outside the AdFabric runtime, so the behaviour
   * settings configured here (model, instructions, tools, knowledge base) do not
   * apply to it.
   *
   * Reads `agent_hosting`, falling back to the old `is_a2a` flag, which was
   * doubling as this flag.
   */
  get isExternalAgent(): boolean {
    if (this.editingAgent.agent_hosting) {
      return this.editingAgent.agent_hosting === 'external';
    }
    return !!this.editingAgent.is_a2a;
  }

  /** Select where the agent runs. */
  setAgentHosting(hosting: 'adfabric' | 'external'): void {
    this.editingAgent.agent_hosting = hosting;
    // Keep the deprecated flag consistent for any consumer still reading it.
    // It only ever meant "external and speaks A2A".
    this.editingAgent.is_a2a = hosting === 'external' && this.getAgentProtocol() === 'a2a';
    this.cdr.markForCheck();
  }

  /** Effective wire protocol for this agent. */
  getAgentProtocol(): AgentProtocol {
    return resolveAgentProtocol(this.editingAgent);
  }

  /** Select the wire protocol. Does not affect the endpoint or the auth mode. */
  setAgentProtocol(protocol: AgentProtocol): void {
    this.editingAgent.agent_protocol = protocol;
    this.editingAgent.is_a2a = this.isExternalAgent && protocol === 'a2a';
    this.cdr.markForCheck();
  }

  /** Whether inbound auth applies: we call an external agent, or ours is exposed via A2A. */
  get showInboundAuthSection(): boolean {
    return this.isExternalAgent || this.getAgentProtocol() === 'a2a';
  }

  /** Current endpoint for display — `agent_endpoint`, falling back to `runtime_arn`. */
  get agentEndpointValue(): string {
    return resolveAgentEndpoint(this.editingAgent).value;
  }

  /**
   * Record the endpoint. An ARN is mirrored into `runtime_arn` because other
   * consumers (the deployment scripts and the agent list built in
   * aws-config.service) still read that field; a URL has no ARN equivalent, so
   * `runtime_arn` is cleared to avoid leaving a stale ARN behind it.
   */
  onAgentEndpointInput(value: string): void {
    this.editingAgent.agent_endpoint = value;
    const classified = classifyEndpoint(value);
    if (classified.kind === 'arn') {
      this.editingAgent.runtime_arn = classified.value;
    } else if (classified.kind === 'url') {
      this.editingAgent.runtime_arn = '';
    }
    this.runtimeArnFilter = value;
  }

  /** What kind of endpoint is currently entered, for the inline badge. */
  get agentEndpointKind(): 'arn' | 'url' | 'none' {
    return classifyEndpoint(this.agentEndpointValue).kind;
  }

  /**
   * Plain-language description of how this agent will actually be invoked, so
   * the derived transport is visible instead of having to be inferred from the
   * combination of protocol, endpoint, and auth mode.
   */
  get invocationPlanDescription(): string {
    return describeInvocationPlan(planInvocation({
      endpoint: resolveAgentEndpoint(this.editingAgent),
      protocol: this.getAgentProtocol(),
      authType: this.editingAgent.a2a_auth_type || 'none',
      region: this.awsConfigService.getRegion()
    }));
  }

  /** True when the current combination cannot be invoked at all. */
  get hasInvocationPlanProblem(): boolean {
    return !!planInvocation({
      endpoint: resolveAgentEndpoint(this.editingAgent),
      protocol: this.getAgentProtocol(),
      authType: this.editingAgent.a2a_auth_type || 'none',
      region: this.awsConfigService.getRegion()
    }).problem;
  }

  /**
   * True when the agent's behaviour is owned by something other than the shared
   * AdFabric runtime: an explicit `external` hosting choice, a URL endpoint, or
   * a runtime ARN that is not the AdFabric default.
   */
  get isExternalRuntime(): boolean {
    if (this.isExternalAgent) return true;
    const endpoint = classifyEndpoint(this.editingAgent.agent_endpoint);
    if (endpoint.kind === 'url') return true;
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
      // Persisting the agent is what publishes staged external-entry configs (and
      // therefore activates any credential already written to Parameter Store),
      // so the awaiting-save warning is cleared here and only here.
      this.a2aCredentialsAwaitingAgentSave.clear();
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
    // Route through the endpoint setter so picking a known ARN and typing one
    // land in the same place.
    this.onAgentEndpointInput(arn);
    this.runtimeArnDropdownOpen = false;
    this.runtimeArnFilter = '';
  }

  onRuntimeArnInput(value: string): void {
    this.editingAgent.runtime_arn = value;
    this.runtimeArnFilter = value;
    this.runtimeArnDropdownOpen = true;
  }

  clearRuntimeArn(): void {
    this.editingAgent.agent_endpoint = '';
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
