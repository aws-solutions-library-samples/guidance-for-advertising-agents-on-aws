import { Component, Input, Output, EventEmitter, OnInit, OnChanges, SimpleChanges, ViewChild } from '@angular/core';
import { AgentDynamoDBService } from '../../services/agent-dynamodb.service';
import { AgentConfigService } from '../../services/agent-config.service';
import { BedrockService } from '../../services/bedrock.service';
import { AgentEditorPanelComponent } from '../agent-editor-panel/agent-editor-panel.component';

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

  // Store configured colors from global config for agent color lookup
  // Validates: Requirement 2.6 - Apply agent's configured color as accent border
  private configuredColors: Record<string, string> = {};

  constructor(
    private agentDynamoDBService: AgentDynamoDBService,
    private agentConfigService: AgentConfigService,
    private bedrockService: BedrockService
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
        
        // Enrich agents with colors from configured_colors if not already set
        this.agents = this.agents.map(agent => ({
          ...agent,
          color: agent.color || this.configuredColors[agent.agent_name] || '#6842ff'
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
      
      // Load instructions from DynamoDB
      // Validates: Requirement 7.2 - Fetch instructions from DynamoDB (pk: INSTRUCTION#{agent_name})
      const instructions = await this.agentDynamoDBService.getAgentInstructions(agent.agent_name);
      if (instructions && this.selectedAgent) {
        this.selectedAgent.instructions = instructions;
      }
      
      this.isEditing = true;
      this.isAddingNew = false;
    } catch (error) {
      console.error('Error loading agent for editing:', error);
      this.errorMessage = 'Failed to load agent details. Please try again.';
    } finally {
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
   * Creates an empty agent configuration with default values
   * Validates: Requirement 4.3 - Default values for optional fields:
   * - model_id: 'anthropic.claude-3-5-sonnet-20241022-v2:0' (default model)
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
          model_id: 'anthropic.claude-3-5-sonnet-20241022-v2:0',
          max_tokens: 8000,      // Validates: Requirement 4.3 - default max_tokens
          temperature: 0.3       // Validates: Requirement 4.3 - default temperature
        }
      },
      agent_tools: [],           // Validates: Requirement 4.3 - default empty array
      instructions: '',
      color: '#6842ff'
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
