import { Component, Input, Output, EventEmitter, OnInit, OnChanges, SimpleChanges, ViewChild, ChangeDetectorRef, NgZone } from '@angular/core';
import { AgentDynamoDBService } from '../../services/agent-dynamodb.service';
import { AgentConfigService } from '../../services/agent-config.service';
import { BedrockService } from '../../services/bedrock.service';
import { AgentEditorPanelComponent } from '../agent-editor-panel/agent-editor-panel.component';
import { AttachedDocument, generateFullAgentConfig } from '../agent-editor-panel/agent-editor-ai.helpers';
import { AVAILABLE_TEMPLATES } from '../agent-editor-panel/agent-editor-panel.constants';

/**
 * MCP Server configuration for connecting to external MCP tools
 * Follows the Strands Agents MCP integration pattern
 */
export interface MCPServerConfig {
  /** Unique identifier for this MCP server configuration */
  id: string;
  /** Display name for the MCP server */
  name: string;
  /** Transport type: 'stdio' for command-line tools, 'http' for HTTP-based servers */
  transport: 'stdio' | 'http' | 'sse';
  /** For stdio transport: the command to run (e.g., 'uvx', 'python', 'npx') */
  command?: string;
  /** For stdio transport: arguments to pass to the command */
  args?: string[];
  /** For http/sse transport: the URL of the MCP server */
  url?: string;
  /** Optional environment variables to set when running the command */
  env?: Record<string, string>;
  /** Optional HTTP headers for authentication (e.g., {"Authorization": "Bearer token"}) */
  headers?: Record<string, string>;
  /** Optional prefix to add to all tool names from this server (prevents conflicts) */
  prefix?: string;
  /** Optional list of tool names to allow (whitelist) */
  allowedTools?: string[];
  /** Optional list of tool names to reject (blacklist) */
  rejectedTools?: string[];
  /** Whether this MCP server is enabled */
  enabled: boolean;
  /** Optional description of what this MCP server provides */
  description?: string;
  /** For AWS IAM authenticated endpoints */
  awsAuth?: {
    region: string;
    service: string;
  };
  /** OAuth Bearer Token authentication */
  oauthToken?: {
    /** Whether a token has been stored in SSM Parameter Store */
    hasToken: boolean;
    /** SSM parameter path (set by backend after token storage) */
    ssmPath?: string;
  };
}

/**
 * External A2A (Agent-to-Agent) agent configuration
 * Allows connecting to remote agents via ARN with optional OAuth authentication
 */
export interface ExternalAgentConfig {
  /** Unique identifier for this external agent entry */
  id: string;
  /** Display name for the external agent */
  name: string;
  /** ARN of the remote agent (e.g., AgentCore runtime ARN or A2A endpoint) */
  arn: string;
  /** Whether this agent is an A2A (Agent-to-Agent) protocol agent */
  isA2A: boolean;
  /** Optional description of what this external agent provides */
  description?: string;
  /** Whether this external agent is enabled */
  enabled: boolean;
  /** Authentication type: 'none', 'oauth', or 'iam' */
  authType?: 'none' | 'oauth' | 'iam';
  /** OAuth Bearer Token authentication for A2A agents */
  oauthToken?: {
    /** Whether a token has been stored in SSM Parameter Store */
    hasToken: boolean;
    /** SSM parameter path (set after token storage) */
    ssmPath?: string;
  };
  /** OAuth credentials stored in SSM (username/password for token acquisition) */
  oauthCredentials?: {
    /** Whether credentials have been stored in SSM Parameter Store */
    hasCredentials: boolean;
    /** SSM parameter path for the credentials */
    ssmPath?: string;
  };
  /** AWS IAM authentication config */
  awsAuth?: {
    region: string;
    service: string;
  };
}

/**
 * Agent configuration interface matching the design document
 */
export interface AgentConfiguration {
  agent_id: string;
  agent_name: string;
  agent_display_name: string;
  team_name: string;
  agent_description: string;
  tool_agent_names: string[];
  external_agents: string[];
  model_inputs: {
    [agentName: string]: {
      model_id: string;
      max_tokens: number;
      temperature: number;
      top_p?: number;
    };
  };
  agent_tools: string[];
  instructions?: string;
  color?: string;
  injectable_values?: Record<string, string>;
  author?: string; // User ID of the agent creator - only the author can edit/delete
  /** MCP server configurations for external tool integration */
  mcp_servers?: MCPServerConfig[];
  /** Optional runtime ARN override for this agent (if different from the default shared ARN) */
  runtime_arn?: string;
  /** Knowledge base name this agent uses for RAG (maps to knowledge_bases in global config) */
  knowledge_base?: string;
  /** Structured external A2A agent configurations */
  external_agent_configs?: ExternalAgentConfig[];
  /** Whether this agent is exposed via the A2A JSON-RPC protocol (defaults to false) */
  is_a2a?: boolean;
  /** Authentication type for inbound A2A requests to this agent's endpoint */
  a2a_auth_type?: 'none' | 'oauth' | 'iam';
  /** OAuth credentials for this agent's A2A endpoint (stored in SSM) */
  a2a_oauth_credentials?: {
    /** Whether credentials have been stored in SSM Parameter Store */
    hasCredentials: boolean;
    /** SSM parameter path for the credentials */
    ssmPath?: string;
  };
}

/**
 * AgentManagementModalComponent - Modal for managing agent configurations
 * 
 * This component provides a UI for viewing, editing, adding, and removing agent
 * configurations. All changes persist to DynamoDB using the AgentDynamoDBService.
 * 
 * Validates: Requirements 1.1, 1.2, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 8.1-8.7
 */
@Component({
  selector: 'app-agent-management-modal',
  templateUrl: './agent-management-modal.component.html',
  styleUrls: ['./agent-management-modal.component.scss']
})
export class AgentManagementModalComponent implements OnInit, OnChanges {
  // Input/Output for modal visibility
  @Input() isOpen: boolean = false;
  @Input() currentUser: string = ''; // Current user ID for author tracking
  @Output() closeModal = new EventEmitter<void>();

  @ViewChild(AgentEditorPanelComponent) editorPanel!: AgentEditorPanelComponent;

  // MCP editor state (rendered at this level to escape modal overflow clipping)
  showMcpEditorOverlay: boolean = false;

  // A2A editor state (rendered at this level to escape modal overflow clipping)
  showA2aEditorOverlay: boolean = false;

  // State properties from design document
  isLoading: boolean = false;
  agents: AgentConfiguration[] = [];
  selectedAgent: AgentConfiguration | null = null;
  isEditing: boolean = false;
  isAddingNew: boolean = false;

  // Error handling
  errorMessage: string | null = null;
  successMessage: string | null = null;

  // Cache refresh state
  isRefreshingCache: boolean = false;

  // Generate Agent modal state
  showGenerateAgentModal: boolean = false;
  generateAgentPrompt: string = '';
  generateAgentDocs: AttachedDocument[] = [];
  isGeneratingAgent: boolean = false;
  generateAgentError: string | null = null;

  // Store configured colors from global config for agent color lookup
  // Validates: Requirement 2.6 - Apply agent's configured color as accent border
  private configuredColors: Record<string, string> = {};

  constructor(
      private agentDynamoDBService: AgentDynamoDBService,
      private agentConfigService: AgentConfigService,
      private bedrockService: BedrockService,
      private cdr: ChangeDetectorRef,
      private ngZone: NgZone
    ) {}


  ngOnInit(): void {}

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['isOpen'] && this.isOpen) {
      // Modal just became visible - load agents
      this.loadAgents();
    }
  }

  /**
   * Opens the modal and loads agents
   */
  open(): void {
    this.isOpen = true;
    this.loadAgents();
  }

  /**
   * Closes the modal and resets state
   */
  close(): void {
      this.isOpen = false;
      this.selectedAgent = null;
      this.isEditing = false;
      this.isAddingNew = false;
      this.errorMessage = null;
      this.successMessage = null;
      this.showMcpEditorOverlay = false;
      this.showA2aEditorOverlay = false;
      this.showGenerateAgentModal = false;
      this.closeModal.emit();
    }

  /**
   * Loads all agents from DynamoDB
   * Validates: Requirement 2.1 - Load and display all agents from DynamoDB AgentConfigTable
   */
  async loadAgents(): Promise<void> {
    this.isLoading = true;
    this.errorMessage = null;

    try {
      // Load global config to get both agents and configured colors
      // Validates: Requirement 2.6 - Apply agent's configured color
      const globalConfig = await this.agentDynamoDBService.getGlobalConfig();
      
      if (globalConfig) {
        // Store configured colors for color lookup
        this.configuredColors = globalConfig.configured_colors || {};
        
        // Get agents from global config
        this.agents = Object.values(globalConfig.agent_configs || {});
        
        // Enrich agents with safe defaults for all fields that the template accesses
        // Agents from DynamoDB may be missing optional fields which can crash Angular rendering
        const knowledgeBases = globalConfig.knowledge_bases || {};
        this.agents = this.agents.map(agent => ({
          ...agent,
          agent_id: agent.agent_id || '',
          agent_name: agent.agent_name || '',
          agent_display_name: agent.agent_display_name || '',
          team_name: agent.team_name || '',
          agent_description: agent.agent_description || '',
          tool_agent_names: agent.tool_agent_names || [],
          external_agents: agent.external_agents || [],
          agent_tools: agent.agent_tools || [],
          color: agent.color || this.configuredColors[agent.agent_name] || '#6842ff',
          knowledge_base: agent.knowledge_base || knowledgeBases[agent.agent_name] || '',
          external_agent_configs: agent.external_agent_configs || []
        }));
      } else {
        // Fallback to getAllAgents if global config not available
        const agents = await this.agentDynamoDBService.getAllAgents();
        this.agents = agents;
      }
    } catch (error) {
      console.error('Error loading agents:', error);
      this.errorMessage = 'Failed to load agents. Please try again.';
    } finally {
      this.isLoading = false;
    }
  }

  /**
   * Selects an agent for viewing/editing details
   * Clicking on an agent card opens the editor panel
   */
  async selectAgent(agent: AgentConfiguration): Promise<void> {
    // Open the agent in the editor panel for viewing/editing
    await this.editAgent(agent);
  }

  /**
   * Opens the editor for an existing agent
   * Validates: Requirements 3.1, 7.2 - Pre-populate fields and load instructions from DynamoDB
   */
  async editAgent(agent: AgentConfiguration): Promise<void> {
      this.isLoading = true;
      this.errorMessage = null;

      try {
        // Deep clone the agent to avoid mutating the original
        this.selectedAgent = JSON.parse(JSON.stringify(agent));

        // Ensure safe defaults on the cloned agent before passing to editor
        if (this.selectedAgent) {
          this.selectedAgent.agent_id = this.selectedAgent.agent_id || '';
          this.selectedAgent.agent_name = this.selectedAgent.agent_name || '';
          this.selectedAgent.agent_display_name = this.selectedAgent.agent_display_name || '';
          this.selectedAgent.team_name = this.selectedAgent.team_name || '';
          this.selectedAgent.agent_description = this.selectedAgent.agent_description || '';
          this.selectedAgent.tool_agent_names = this.selectedAgent.tool_agent_names || [];
          this.selectedAgent.external_agents = this.selectedAgent.external_agents || [];
          this.selectedAgent.agent_tools = this.selectedAgent.agent_tools || [];
          // Start with empty instructions — loaded asynchronously after editor renders
          this.selectedAgent.instructions = '';
          this.selectedAgent.color = this.selectedAgent.color || '#6842ff';
          this.selectedAgent.injectable_values = this.selectedAgent.injectable_values || {};
          this.selectedAgent.mcp_servers = this.selectedAgent.mcp_servers || [];
          this.selectedAgent.external_agent_configs = this.selectedAgent.external_agent_configs || [];
          this.selectedAgent.runtime_arn = this.selectedAgent.runtime_arn || '';
        }

        // Show editor immediately with empty instructions
        this.isEditing = true;
        this.isAddingNew = false;
        this.isLoading = false;
        this.cdr.detectChanges(); // Force render the editor with empty instructions

        // Load instructions from DynamoDB AFTER the editor has rendered.
        // Large instructions (20KB+) were crashing the browser when loaded synchronously
        // because Angular's change detection + textarea binding would OOM the renderer.
        // We defer the fetch and use NgZone.run to ensure Angular picks up the change.
        setTimeout(() => {
          this.ngZone.run(async () => {
            try {
              const instructions = await this.agentDynamoDBService.getAgentInstructions(agent.agent_name);
              if (instructions && this.selectedAgent && this.isEditing && this.editorPanel) {
                // Push instructions directly into the editor's textarea model
                // instead of reassigning selectedAgent (which re-triggers initializeForm)
                this.editorPanel.editingAgent.instructions = instructions;
                this.selectedAgent.instructions = instructions;
                this.cdr.detectChanges();
              }
            } catch (instrError) {
              console.error('Error loading agent instructions:', instrError);
              // Non-fatal: editor still works, just without instructions
            }
          });
        }, 100);

      } catch (error) {
        console.error('Error loading agent for editing:', error);
        this.errorMessage = 'Failed to load agent details. Please try again.';
        this.isLoading = false;
      }
    }




  /**
   * Opens the editor for creating a new agent
   * Validates: Requirements 4.1, 4.3 - Open editor with empty fields and default values
   */
  addNewAgent(): void {
    // Create empty agent with default values
    // Validates: Requirement 4.3 - Default values for optional fields
    this.selectedAgent = this.createEmptyAgent();
    this.isEditing = true;
    this.isAddingNew = true;
    this.errorMessage = null;
  }

  // Delete confirmation state
  // Validates: Requirements 5.1, 5.2, 5.4, 5.5
  showDeleteConfirmation: boolean = false;
  agentToDelete: AgentConfiguration | null = null;
  agentDependencies: string[] = [];

  /**
   * Initiates the delete agent flow with dependency checking
   * Validates: Requirements 5.1, 5.2 - Show confirmation dialog and check for dependencies
   */
  async deleteAgent(agent: AgentConfiguration): Promise<void> {
    this.isLoading = true;
    this.errorMessage = null;
    this.agentToDelete = agent;
    this.agentDependencies = [];

    try {
      // Check for dependencies (agents that reference this agent in tool_agent_names)
      // Validates: Requirement 5.2 - Check for dependencies before deletion
      const dependencies = await this.agentDynamoDBService.getAgentDependencies(agent.agent_name);
      this.agentDependencies = dependencies;
      
      // Show confirmation dialog
      // Validates: Requirement 5.1 - Show confirmation dialog on delete button click
      this.showDeleteConfirmation = true;
    } catch (error) {
      console.error('Error checking agent dependencies:', error);
      this.errorMessage = 'Failed to check agent dependencies. Please try again.';
      this.agentToDelete = null;
    } finally {
      this.isLoading = false;
    }
  }

  /**
   * Confirms and executes the agent deletion
   * Validates: Requirements 5.3, 5.4, 5.5, 5.6
   */
  async confirmDelete(): Promise<void> {
    if (!this.agentToDelete) {
      return;
    }

    this.isLoading = true;
    this.errorMessage = null;
    this.showDeleteConfirmation = false;

    try {
      // Delete agent and all related DynamoDB records
      // Validates: Requirement 5.3 - Delete all related DynamoDB records
      const success = await this.agentDynamoDBService.deleteAgent(this.agentToDelete.agent_name);
      
      if (success) {
        // Validates: Requirement 5.6 - Display success notification and refresh list
        this.showSuccess(`Agent "${this.agentToDelete.agent_display_name}" deleted successfully.`);
        
        // Clear selection if deleted agent was selected
        if (this.selectedAgent?.agent_name === this.agentToDelete.agent_name) {
          this.selectedAgent = null;
        }
        
        // Refresh agent list
        await this.loadAgents();
        
        // Reload agent configurations to update typeahead and local stores
        await this.agentConfigService.reloadAgentConfigurations();
        
        // Trigger backend cache refresh so the runtime picks up the changes
        // Wait a moment for DynamoDB to propagate the changes
        setTimeout(() => this.triggerBackendCacheRefresh(), 500);
      } else {
        this.errorMessage = 'Failed to delete agent. Please try again.';
      }
    } catch (error) {
      console.error('Error deleting agent:', error);
      this.errorMessage = 'Failed to delete agent. Please try again.';
    } finally {
      this.isLoading = false;
      this.agentToDelete = null;
      this.agentDependencies = [];
    }
  }

  /**
   * Cancels the delete operation
   */
  cancelDelete(): void {
    this.showDeleteConfirmation = false;
    this.agentToDelete = null;
    this.agentDependencies = [];
  }

  /**
   * Saves an agent (create or update)
   * Validates: Requirements 3.4, 3.6, 4.4, 4.5, 4.6 - Save changes to DynamoDB with uniqueness validation
   */
  async saveAgent(agent: AgentConfiguration): Promise<void> {
    this.isLoading = true;
    this.errorMessage = null;

    try {
      // For new agents, validate uniqueness and generate ID if needed
      // Validates: Requirements 4.4, 4.6 - Validate uniqueness and generate unique agent_id
      if (this.isAddingNew) {
        // Set the author to the current user for new agents
        agent.author = this.currentUser;
        
        // Generate unique agent_id if not provided
        // Validates: Requirement 4.4 - Generate unique agent_id if not provided
        if (!agent.agent_id?.trim()) {
          agent.agent_id = this.generateUniqueAgentId(agent.agent_display_name);
        }
        
        // If agent_name is not provided, use agent_id
        if (!agent.agent_name?.trim()) {
          agent.agent_name = agent.agent_id;
        }
        
        // Validate uniqueness of agent_id
        // Validates: Requirement 4.6 - Validate that agent_id and agent_name are unique
        const agentIdExists = await this.agentDynamoDBService.checkAgentExists(agent.agent_id);
        if (agentIdExists) {
          this.errorMessage = `An agent with ID "${agent.agent_id}" already exists. Please choose a different ID.`;
          this.isLoading = false;
          return;
        }
        
        // Validate uniqueness of agent_name (if different from agent_id)
        if (agent.agent_name !== agent.agent_id) {
          const agentNameExists = await this.agentDynamoDBService.checkAgentExists(agent.agent_name);
          if (agentNameExists) {
            this.errorMessage = `An agent with name "${agent.agent_name}" already exists. Please choose a different name.`;
            this.isLoading = false;
            return;
          }
        }
      }

      // Ensure model_inputs is keyed by agent_name (backend looks up model config by agent_name)
      // The "default" key is not recognized by the runtime — it expects the agent's own name as the key.
      if (agent.model_inputs && agent.agent_name) {
        const hasAgentNameKey = agent.model_inputs[agent.agent_name];
        if (!hasAgentNameKey) {
          // Move "default" entry (or first available entry) to agent_name key
          const defaultInputs = agent.model_inputs['default'] || agent.model_inputs[Object.keys(agent.model_inputs)[0]];
          if (defaultInputs) {
            agent.model_inputs[agent.agent_name] = defaultInputs;
            // Keep "default" as well for backward compatibility
          }
        }
      }

      // Ensure team_name is set — an empty team_name prevents collaborator agent resolution
      // in the chat streaming handler (the lookup is skipped when teamName is falsy)
      if (!agent.team_name?.trim()) {
        agent.team_name = agent.agent_display_name || agent.agent_name || 'Default Team';
      }

      // Save agent to DynamoDB
      // Validates: Requirement 4.5 - Save new agent to DynamoDB
      const success = await this.agentDynamoDBService.saveAgent(agent);
      if (success) {
        // Validates: Requirement 3.6 - Display success notification on save
        const message = this.isAddingNew 
          ? `Agent "${agent.agent_display_name}" created successfully.`
          : `Agent "${agent.agent_display_name}" saved successfully.`;
        this.showSuccess(message);
        
        // Validates: Requirement 3.6 - Refresh agent list after save
        await this.loadAgents();
        
        // Reload agent configurations to update typeahead and local stores
        await this.agentConfigService.reloadAgentConfigurations();
        
        // Trigger backend cache refresh so the runtime picks up the changes
        // Wait a moment for DynamoDB to propagate the changes
        setTimeout(() => this.triggerBackendCacheRefresh(), 500);
        
        // Reset editing state
        this.isEditing = false;
        this.isAddingNew = false;
        this.selectedAgent = null;
      } else {
        this.errorMessage = 'Failed to save agent. Please try again.';
      }
    } catch (error) {
      console.error('Error saving agent:', error);
      this.errorMessage = 'Failed to save agent. Please try again.';
    } finally {
      this.isLoading = false;
    }
  }

  /**
   * Generates a unique agent ID based on display name or timestamp
   * Validates: Requirement 4.4 - Generate unique agent_id if not provided
   * 
   * @param displayName - The agent's display name to base the ID on
   * @returns A unique agent ID string
   */
  private generateUniqueAgentId(displayName: string): string {
    // Convert display name to a valid ID format
    // Remove special characters, replace spaces with underscores, capitalize words
    let baseId = displayName
      .trim()
      .replace(/[^a-zA-Z0-9\s]/g, '') // Remove special characters
      .split(/\s+/) // Split by whitespace
      .map(word => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase()) // Capitalize each word
      .join(''); // Join without spaces
    
    // If the result is empty or doesn't start with a letter, use a default prefix
    if (!baseId || !/^[A-Za-z]/.test(baseId)) {
      baseId = 'Agent';
    }
    
    // Add a timestamp suffix to ensure uniqueness
    const timestamp = Date.now().toString(36).toUpperCase(); // Base36 timestamp for shorter string
    
    // Ensure the ID doesn't exceed 64 characters
    const maxBaseLength = 64 - timestamp.length - 1; // -1 for underscore separator
    if (baseId.length > maxBaseLength) {
      baseId = baseId.substring(0, maxBaseLength);
    }
    
    return `${baseId}_${timestamp}`;
  }

  /**
   * Handles save event from the editor panel
   * Validates: Requirements 3.4, 3.6
   */
  onEditorSave(agent: AgentConfiguration): void {
    this.saveAgent(agent);
  }

  /**
   * Handles cancel event from the editor panel
   * Validates: Requirement 3.7 - Cancel button discards changes and closes editor
   */
  onEditorCancel(): void {
    this.cancelEdit();
  }
  onEditorDelete(agent: AgentConfiguration): void {
    this.cancelEdit();
    this.deleteAgent(agent);
  }

  /**
   * Cancels editing and returns to list view
   * Validates: Requirement 3.7 - Cancel button discards changes
   */
  cancelEdit(): void {
    this.isEditing = false;
    this.isAddingNew = false;
    this.selectedAgent = null;
  }

  /**
   * Gets list of available agent names for tool_agent_names selection
   * Used by the editor panel to populate the tool agents multi-select
   */
  getAvailableAgentNames(): string[] {
    return this.agents.map(agent => agent.agent_name);
  }

  /**
   * Collects unique runtime ARNs from enriched agents for the combobox dropdown
   */
  getAvailableRuntimeArns(): string[] {
    const enriched = this.agentConfigService.getEnrichedAgents();
    const arns = new Set<string>();
    for (const agent of enriched) {
      if (agent.runtimeArn) {
        arns.add(agent.runtimeArn);
      }
    }
    // Also include runtime_arn values from saved agent configs
    for (const agent of this.agents) {
      if (agent.runtime_arn) {
        arns.add(agent.runtime_arn);
      }
    }
    return Array.from(arns);
  }

  /**
   * Returns the AdFabricAgent's runtime ARN (the "default" runtime).
   * Agents with a different runtime_arn are considered external.
   */
  getDefaultRuntimeArn(): string {
    const enriched = this.agentConfigService.getEnrichedAgents();
    const adFabric = enriched.find(a => a.agentType?.toLowerCase().includes('adfabricagent'));
    if (adFabric?.runtimeArn) return adFabric.runtimeArn;
    // Fallback: use the first enriched agent's runtimeArn (they all default to AdFabricAgent's)
    const first = enriched.find(a => a.runtimeArn);
    return first?.runtimeArn || '';
  }

  /**
   * Creates an empty agent configuration with default values
   * Validates: Requirement 4.3 - Default values for optional fields:
   * - model_id: 'global.anthropic.claude-sonnet-4-5-20250929-v1:0' (default model)
   * - max_tokens: 8000
   * - temperature: 0.3
   * - agent_tools: empty array
   * - color: '#6842ff' (purple accent)
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
          model_id: 'global.anthropic.claude-sonnet-4-5-20250929-v1:0',
          max_tokens: 8000,      // Validates: Requirement 4.3 - default max_tokens
          temperature: 0.3       // Validates: Requirement 4.3 - default temperature
        }
      },
      agent_tools: [],           // Validates: Requirement 4.3 - default empty array
      instructions: '',
      color: '#6842ff',
      external_agent_configs: []
    };
  }

  /**
   * Shows a success message that auto-dismisses
   */
  private showSuccess(message: string): void {
    this.successMessage = message;
    setTimeout(() => {
      this.successMessage = null;
    }, 3000);
  }

  /**
   * Gets the color for an agent from configured_colors or agent's color property
   * Validates: Requirement 2.6 - Apply agent's configured color as accent border
   * 
   * Color lookup priority:
   * 1. Agent's color property (if set)
   * 2. configured_colors mapping by agent_name
   * 3. Default purple accent (#6842ff)
   */
  getAgentColor(agent: AgentConfiguration): string {
    // First check agent's own color property
    if (agent.color) {
      return agent.color;
    }
    
    // Then check configured_colors by agent_name
    if (this.configuredColors[agent.agent_name]) {
      return this.configuredColors[agent.agent_name];
    }
    
    // Default to purple accent color
    return '#6842ff';
  }

  /**
   * Handles click outside modal to close
   */
  onOverlayClick(event: Event): void {
    if ((event.target as HTMLElement).classList.contains('agent-modal-overlay')) {
      this.close();
    }
  }

  /**
   * Checks if the current user can edit/delete the specified agent
   * Only the author of an agent can edit or delete it
   * @param agent The agent to check permissions for
   * @returns true if the current user is the author or if no author is set
   */
  canEditAgent(agent: AgentConfiguration): boolean {
    // If no author is set, allow editing (legacy agents)
    if (!agent.author) {
      return true;
    }
    // Only the author can edit
    return agent.author === this.currentUser;
  }

  /**
   * Gets the author display text for an agent
   * @param agent The agent to get author info for
   * @returns Display text for the author
   */
  getAuthorDisplay(agent: AgentConfiguration): string {
    if (!agent.author) {
      return 'System';
    }
    if (agent.author === this.currentUser) {
      return 'You';
    }
    return agent.author;
  }

  /**
   * Triggers a backend cache refresh to update the runtime with the latest agent configurations.
   * This ensures the AgentCore runtime picks up changes after save/update/delete operations.
   */
  onMcpEditorOpened(): void {
    this.showMcpEditorOverlay = true;
  }

  onMcpEditorClosed(): void {
    this.showMcpEditorOverlay = false;
  }

  onMcpEditorSaved(): void {
    this.showMcpEditorOverlay = false;
  }

  onA2aEditorOpened(): void {
    this.showA2aEditorOverlay = true;
  }

  onA2aEditorClosed(): void {
    this.showA2aEditorOverlay = false;
  }

  onA2aEditorSaved(): void {
    this.showA2aEditorOverlay = false;
  }

  // ============================================
  // Generate Agent from Prompt + Documents
  // ============================================

  openGenerateAgentModal(): void {
    this.showGenerateAgentModal = true;
    this.generateAgentPrompt = '';
    this.generateAgentDocs = [];
    this.generateAgentError = null;
  }

  closeGenerateAgentModal(): void {
    this.showGenerateAgentModal = false;
    this.generateAgentPrompt = '';
    this.generateAgentDocs = [];
    this.generateAgentError = null;
  }

  onGenerateAgentFileAttach(event: Event): void {
    const input = event.target as HTMLInputElement;
    if (!input.files?.length) return;
    const allowedTypes = ['.txt', '.md', '.json', '.csv', '.xml', '.yaml', '.yml', '.log'];
    Array.from(input.files).forEach(file => {
      const ext = '.' + file.name.split('.').pop()?.toLowerCase();
      if (!allowedTypes.includes(ext) && !file.type.startsWith('text/')) return;
      if (file.size > 512 * 1024) return;
      const reader = new FileReader();
      reader.onload = () => {
        this.generateAgentDocs.push({ name: file.name, content: reader.result as string });
        this.cdr.markForCheck();
      };
      reader.readAsText(file);
    });
    input.value = '';
  }

  removeGenerateAgentDoc(index: number): void {
    this.generateAgentDocs.splice(index, 1);
  }

  async executeGenerateAgent(): Promise<void> {
    if (!this.generateAgentPrompt.trim()) {
      this.generateAgentError = 'Please provide a description of the agent you want to create.';
      return;
    }

    this.isGeneratingAgent = true;
    this.generateAgentError = null;

    try {
      const result = await generateFullAgentConfig(
        this.generateAgentPrompt,
        this.getAvailableAgentNames(),
        AVAILABLE_TEMPLATES,
        this.bedrockService,
        this.generateAgentDocs
      );

      // Close the generate modal
      this.showGenerateAgentModal = false;

      // Set the generated agent as the selected agent and open the editor
      this.selectedAgent = result.agent;
      this.isEditing = true;
      this.isAddingNew = true;
      this.errorMessage = null;

      // After the editor panel initializes, set the visualization mappings
      setTimeout(() => {
        if (this.editorPanel && result.visualizationTemplates?.length) {
          this.editorPanel.visualizationMappings = {
            agentName: result.agent.agent_name || '',
            agentId: result.agent.agent_id || '',
            templates: result.visualizationTemplates
          };
          this.cdr.detectChanges();
        }
      }, 100);

      this.showSuccess('Agent configuration generated. Review and save when ready.');
    } catch (error: any) {
      console.error('Error generating agent:', error);
      this.generateAgentError = error.message || 'Failed to generate agent configuration. Please try again.';
    } finally {
      this.isGeneratingAgent = false;
      this.generateAgentPrompt = '';
      this.generateAgentDocs = [];
      this.cdr.markForCheck();
    }
  }

  /**
   * Triggers a backend cache refresh to update the runtime with the latest agent configurations.
   * This ensures the AgentCore runtime picks up changes after save/update/delete operations.
   */
  triggerBackendCacheRefresh(): void {
    this.isRefreshingCache = true;
    this.successMessage = null;
    this.errorMessage = null;
    
    // Get any available agent to use for the refresh request
    const enrichedAgents = this.agentConfigService.getEnrichedAgents();
    console.log(`🔍 Found ${enrichedAgents.length} enriched agents for cache refresh`);
    
    if (enrichedAgents.length === 0) {
      console.warn('⚠️ No agents available to trigger backend cache refresh');
      this.errorMessage = 'No agents available to trigger cache refresh';
      this.isRefreshingCache = false;
      return;
    }

    // Use the first available agent with a runtime ARN
    const agentWithRuntime = enrichedAgents.find(a => a.runtimeArn);
    if (!agentWithRuntime) {
      console.warn('⚠️ No agent with runtime ARN available for cache refresh');
      console.log('Available agents:', enrichedAgents.map(a => ({ name: a.name, runtimeArn: a.runtimeArn })));
      this.errorMessage = 'No agent with runtime ARN available for cache refresh';
      this.isRefreshingCache = false;
      return;
    }

    console.log(`🔄 Triggering backend cache refresh using agent: ${agentWithRuntime.name} (${agentWithRuntime.runtimeArn})`);
    this.bedrockService.refreshAgentCache(agentWithRuntime, false).subscribe({
      next: (event) => {
        console.log('✅ Backend cache refresh event:', event);
      },
      error: (error) => {
        console.error('❌ Backend cache refresh failed:', error);
        this.errorMessage = 'Backend cache refresh failed. Please try again.';
        this.isRefreshingCache = false;
      },
      complete: () => {
        console.log('✅ Backend cache refresh completed');
        this.successMessage = 'Backend cache refreshed successfully! Agents will now use the latest configurations.';
        this.isRefreshingCache = false;
        // Auto-clear success message after 5 seconds
        setTimeout(() => {
          if (this.successMessage?.includes('cache refreshed')) {
            this.successMessage = null;
          }
        }, 5000);
      }
    });
  }
}
