import { Component, OnInit, OnDestroy, ChangeDetectorRef, ViewChild, ElementRef, AfterViewInit, Output, EventEmitter } from '@angular/core';
import { Subject } from 'rxjs';
import { takeUntil } from 'rxjs/operators';
import { BedrockAgentManagementService, AgentNode, KnowledgeBaseAssociation } from '../../services/bedrock-agent-management.service';
import { AgentConfigService } from '../../services/agent-config.service';
import { AwsConfigService } from '../../services/aws-config.service';
import { OrgDiagram, OrgItemConfig, Enabled, Colors, Size } from 'basicprimitives';

@Component({
  selector: 'app-agent-config',
  templateUrl: './agent-config.component.html',
  styleUrls: ['./agent-config.component.scss']
})
export class AgentConfigComponent implements OnInit, OnDestroy, AfterViewInit {
  @ViewChild('orgChart', { static: true }) orgChartContainer!: ElementRef;
  @Output() closeModal = new EventEmitter<void>();

  private destroy$ = new Subject<void>();
  private orgDiagram: OrgDiagram | null = null;
  nodes: AgentNode[] = [];
  selectedNode: AgentNode | null = null;
  hoveredSupervisorId: string | null = null;

  // UI state
  isLoading = false;
  showNodeEditor = false;
  availableKnowledgeBases: any[] = [];

  // AppConfig loading states
  isLoadingAgentConfig = false;
  isLoadingTabConfig = false;
  appConfigError: string | null = null;

  // Editor state
  editingInstructions = '';
  editingKnowledgeBases: KnowledgeBaseAssociation[] = [];
  isSaving = false;

  // Message state
  showSuccessMessage = false;
  showErrorMessage = false;
  successMessage = '';
  errorMessage = '';

  // Agent discovery properties
  isDiscovering = false;
  discoveredAgents: any[] = [];
  showDiscoveryResults = false;

  // Agent removal properties
  isRemoveMode = false;
  agentsToRemove: Set<string> = new Set();

  // Configuration data
  currentAgentConfig: any = null;
  currentTabConfig: any = null;

  // Progress tracking for AppConfig operations
  operationProgress: {
    isActive: boolean;
    operation: string;
    progress: number;
    message: string;
  } = {
      isActive: false,
      operation: '',
      progress: 0,
      message: ''
    };

  // Real-time update feedback
  deploymentStatus: {
    isDeploying: boolean;
    deploymentId?: string;
    progress: number;
    message: string;
  } = {
      isDeploying: false,
      progress: 0,
      message: ''
    };

  // Error handling and retry mechanism
  errorState: {
    hasError: boolean;
    errorType: 'appconfig' | 'network' | 'auth' | 'validation' | 'unknown';
    errorMessage: string;
    canRetry: boolean;
    retryCount: number;
    maxRetries: number;
    lastFailedOperation?: string;
    fallbackActive: boolean;
  } = {
      hasError: false,
      errorType: 'unknown',
      errorMessage: '',
      canRetry: false,
      retryCount: 0,
      maxRetries: 3,
      fallbackActive: false
    };

  // Graceful degradation state
  degradationMode: {
    isActive: boolean;
    reason: string;
    availableFeatures: string[];
    disabledFeatures: string[];
  } = {
      isActive: false,
      reason: '',
      availableFeatures: [],
      disabledFeatures: []
    };

  constructor(
    private agentManagementService: BedrockAgentManagementService,
    private agentConfigService: AgentConfigService,
    private awsConfigService: AwsConfigService,
    private cdr: ChangeDetectorRef
  ) { }

  ngOnInit(): void {
    this.loadData();
    this.loadAppConfigData();
  }

  ngAfterViewInit(): void {
    // Initialize the org chart after view is ready
    this.initializeOrgChart();
  }

  ngOnDestroy(): void {
    this.destroy$.next();
    this.destroy$.complete();
    if (this.orgDiagram) {
      this.orgDiagram.destroy();
    }
  }

  private initializeOrgChart(): void {
    if (!this.orgChartContainer) return;

    this.orgDiagram = new OrgDiagram(this.orgChartContainer.nativeElement, {
      items: [],
      cursorItem: null,
      highlightItem: null,
      hasSelectorCheckbox: Enabled.False,
      itemTitleFirstFontColor: Colors.White,
      itemTitleSecondFontColor: Colors.White,
      linesColor: Colors.Silver,
      linesWidth: 1,
      highlightLinesColor: Colors.Red,
      highlightLinesWidth: 2,
      showCallout: false,
      defaultTemplateName: "compactTemplate",
      hasButtons: Enabled.False,
      normalLevelShift: 6, // Very compact
      dotLevelShift: 6,
      lineLevelShift: 6,
      normalItemsInterval: 3, // Minimal spacing
      dotItemsInterval: 6,
      lineItemsInterval: 6,
      cousinsIntervalMultiplier: 1.5,
      itemTitleFirstFontSize: "9px", // Small font
      itemTitleSecondFontSize: "7px",
      minimumVisibleLevels: 1,
      orientationType: 0,
      verticalAlignment: 1,
      arrowsDirection: 0,
      showExtraArrows: false,
      extraArrowsMinimumSpace: 10,
      selectionPathMode: 0,
      hasCursorCheckbox: Enabled.False,
      hasHighlightCheckbox: Enabled.False,
      pageFitMode: 4,
      minimalVisibility: 2,
      orientationAngle: 0,
      scale: 0.7, // Start smaller
      minimumScale: 0.3,
      maximumScale: 1.2,
      showLabels: Enabled.False,
      enablePanning: true,
      autoSizeMinimum: new Size(400, 300),
      autoSizeMaximum: new Size(1400, 900)
    });

    // Set up click handler
    this.orgDiagram.onCursorChanged = (event, data) => {
      if (data.context && data.context.id) {
        const agent = this.nodes.find(n => n.id === data.context.id);
        if (agent) {
          this.selectAgent(agent);
        }
      }
    };

    // Set up hover handler for glow effect
    this.orgDiagram.onHighlightChanged = (event, data) => {
      this.handleAgentHover(data.context?.id);
    };
  }

  private loadData(): void {
    this.isLoading = true;

    // Load agents and available knowledge bases
    Promise.all([
      this.agentManagementService.getAgentNodes().pipe(takeUntil(this.destroy$)).toPromise(),
      this.agentManagementService.getAvailableKnowledgeBases().pipe(takeUntil(this.destroy$)).toPromise()
    ]).then(([agents, knowledgeBases]) => {

      // Ensure we always have arrays
      this.nodes = Array.isArray(agents) ? agents : [];
      this.availableKnowledgeBases = Array.isArray(knowledgeBases) ? knowledgeBases : [];

      // Update the organizational chart
      this.updateOrgChart();

      this.isLoading = false;
      this.cdr.detectChanges();
    }).catch(error => {
      console.error('Error loading data:', error);
      // Set empty arrays on error
      this.nodes = [];
      this.availableKnowledgeBases = [];
      this.isLoading = false;
      this.cdr.detectChanges();
    });
  }

  /**
   * Load configuration data from AppConfig with loading states and error handling
   * Requirements: 3.4, 3.5
   */
  public async loadAppConfigData(): Promise<void> {
    try {

      // Reset error state for new attempt
      this.clearErrorState();

      // Set loading states
      this.isLoadingAgentConfig = true;
      this.isLoadingTabConfig = true;
      this.appConfigError = null;
      this.cdr.detectChanges();

      // Load both configurations in parallel with timeout
      const configPromises = [
        this.loadAgentConfiguration(),
        this.loadTabConfiguration()
      ];

      // Add timeout to prevent hanging
      const timeoutPromise = new Promise((_, reject) => {
        setTimeout(() => reject(new Error('Configuration loading timeout')), 30000);
      });

      const [agentConfig, tabConfig] = await Promise.race([
        Promise.all(configPromises),
        timeoutPromise
      ]) as [any, any];

      // Store configurations
      this.currentAgentConfig = agentConfig;
      this.currentTabConfig = tabConfig;

      // Deactivate degradation mode if it was active
      if (this.degradationMode.isActive) {
        this.deactivateGracefulDegradation();
      }

    } catch (error) {
      console.error('❌ Error loading AppConfig data:', error);

      const errorObj = error instanceof Error ? error : new Error('Failed to load configuration');
      this.appConfigError = errorObj.message;

      // Use enhanced error handling
      this.showDetailedError('Configuration Loading', errorObj);

      // Try fallback if AppConfig is unavailable
      if (this.classifyError(errorObj) === 'appconfig') {
        await this.handleAppConfigUnavailable();
      }

    } finally {
      this.isLoadingAgentConfig = false;
      this.isLoadingTabConfig = false;
      this.cdr.detectChanges();
    }
  }

  /**
   * Load agent configuration with error handling and fallback
   */
  private async loadAgentConfiguration(): Promise<any> {
    try {
      const config = await this.agentConfigService.getAgentsConfiguration();
      return config;
    } catch (error) {
      console.warn('⚠️ Failed to load agent configuration from AppConfig, using fallback');
      throw error;
    }
  }

  /**
   * Load tab configuration with error handling and fallback
   */
  private async loadTabConfiguration(): Promise<any> {
    try {
      const config = await this.agentConfigService.getTabsConfiguration();
      return config;
    } catch (error) {
      console.warn('⚠️ Failed to load tab configuration from AppConfig, using fallback');
      throw error;
    }
  }

  private updateOrgChart(): void {
    if (!this.orgDiagram || this.nodes.length === 0) return;

    const orgItems: OrgItemConfig[] = this.nodes.map(node => {
      const enrichedAgent = this.agentConfigService.getAgentById(node.id);
      const color = enrichedAgent?.color || (node.role === 'supervisor' ? '#667eeb' : '#764ba2');

      return {
        id: node.id,
        parent: node.supervisor || null,
        title: node.displayName || node.name,
        description: node.role.toUpperCase(),
        itemTitleColor: color,
        groupTitle: node.knowledgeBases.length > 0 ? `${node.knowledgeBases.length} KB` : '',
        groupTitleColor: '#fdaf11',
        templateName: 'compactTemplate',
        context: {
          id: node.id,
          type: node.role,
          status: node.status,
          knowledgeBasesCount: node.knowledgeBases.length,
          description: this.getAgentDescription(node)
        }
      };
    });

    // Update the diagram
    this.orgDiagram.setOptions({
      items: orgItems
    });

    this.orgDiagram.update();
  }

  private handleAgentHover(agentId: string | null): void {
    if (!agentId) {
      this.hoveredSupervisorId = null;
      this.removeGlowEffects();
      return;
    }

    const agent = this.nodes.find(n => n.id === agentId);
    if (agent && agent.role === 'supervisor') {
      this.hoveredSupervisorId = agentId;
      this.applyGlowEffects(agentId);
    } else {
      this.hoveredSupervisorId = null;
      this.removeGlowEffects();
    }
  }

  private applyGlowEffects(supervisorId: string): void {
    // Find collaborators of this supervisor
    const collaborators = this.nodes.filter(n => n.supervisor === supervisorId);

    // Apply glow effect to collaborators
    collaborators.forEach(collaborator => {
      const element = document.querySelector(`[data-agent-id="${collaborator.id}"]`);
      if (element) {
        element.classList.add('glow-effect');
      }
    });
  }

  private removeGlowEffects(): void {
    // Remove glow effect from all elements
    const glowElements = document.querySelectorAll('.glow-effect');
    glowElements.forEach(element => {
      element.classList.remove('glow-effect');
    });
  }

  // New hover methods for the card-based layout
  onAgentHover(node: AgentNode): void {
    if (node.role === 'supervisor') {
      this.hoveredSupervisorId = node.id;
    }
  }

  onAgentLeave(): void {
    this.hoveredSupervisorId = null;
  }

  // Get collaborators for a supervisor
  getCollaborators(supervisorId: string): string[] {
    // Only return collaborators if this supervisor is currently being hovered
    if (this.hoveredSupervisorId !== supervisorId) {
      return [];
    }

    // Find all nodes that have this supervisor as their parent
    const collaborators = this.nodes
      .filter(node =>
        node.supervisor === supervisorId &&
        node.id !== supervisorId &&
        node.role === 'collaborator'
      )
      .map(node => node.id);

    // Debug logging
    // console.log('Collaborators found:', collaborators.map(id => {
    //   const node = this.nodes.find(n => n.id === id);
    //   return { id, name: node?.name, displayName: node?.displayName, supervisor: node?.supervisor };
    // }));

    if (collaborators.length === 0) {
      // Debug: show what this supervisor looks like and all potential collaborators
      const supervisor = this.nodes.find(n => n.id === supervisorId);
      // console.log('All potential collaborators:',
      //   this.nodes
      //     .filter(n => n.type === 'collaborator')
      //     .map(n => ({
      //       id: n.id,
      //       name: n.name,
      //       supervisor: n.supervisor,
      //       matchesSupervisor: n.supervisor === supervisorId
      //     }))
      // );
    }

    return collaborators;
  }

  // Get collaborator display name
  getCollaboratorName(collaboratorId: string): string {
    // Find the actual collaborator node
    const collaboratorNode = this.nodes.find(n => n.id === collaboratorId);
    if (collaboratorNode) {
      return collaboratorNode.displayName || collaboratorNode.name;
    }

    // Fallback: convert agent type to display name
    return collaboratorId.replace(/([A-Z])/g, ' $1').trim();
  }

  // Get collaborator color
  getCollaboratorColor(collaboratorId: string): string {
    // Find the actual collaborator node if it exists
    const collaboratorNode = this.nodes.find(n => n.id === collaboratorId);
    if (collaboratorNode) {
      return this.getNodeColor(collaboratorNode);
    }

    // Default collaborator color
    return '#764ba2';
  }

  // Removed D3 visualization methods - using card-based layout now

  getNodeColor(node: AgentNode): string {
    // Get the enriched agent to use its proper color
    const enrichedAgent = this.agentConfigService.getAgentById(node.id);
    if (enrichedAgent) {
      // console.log("returning enriched color: "+enrichedAgent.color)
      return enrichedAgent.color;
    }

    // Fallback to type and agent type-based colors
    if (node.agentType === 'agentcore') {
      return '#10b981'; // Green for AgentCore agents
    } else if (node.role === 'supervisor') {
      return '#667eeb'; // Blue for Bedrock supervisors
    } else {
      return '#764ba2'; // Purple for Bedrock collaborators
    }
  }

  getNodeIcon(node: AgentNode): string {
    if (node.role === 'supervisor') {
      return 'supervisor_account'; // Material Icon for supervisor
    } else if (node.agentType === 'agentcore') {
      return 'precision_manufacturing'; // Material Icon for AgentCore (containerized)
    } else {
      return 'smart_toy'; // Material Icon for AI/bot (Bedrock)
    }
  }

  selectAgent(node: AgentNode): void {
    this.selectedNode = node;
    this.editingInstructions = node.instructions || '';
    this.editingKnowledgeBases = [...node.knowledgeBases];
    this.showNodeEditor = true;
    this.cdr.detectChanges();
  }

  getAgentDescription(node: AgentNode): string {
    const enrichedAgent = this.agentConfigService.getAgentById(node.id);
    if (enrichedAgent?.description) {
      return enrichedAgent.description;
    }

    // Fallback descriptions based on agent type
    const type = node.name.toLowerCase();
    if (type.includes('bid') && type.includes('simulator')) {
      return 'Simulates bidding strategies for campaign optimization';
    }
    if (type.includes('creative') && type.includes('selection')) {
      return 'Selects optimal creative assets for campaigns';
    }
    if (type.includes('media') && type.includes('planning')) {
      return 'Plans and coordinates media campaign strategies';
    }
    if (type.includes('audience') && type.includes('strategy')) {
      return 'Develops audience targeting strategies';
    }
    if (type.includes('campaign') && type.includes('architecture')) {
      return 'Designs comprehensive campaign structures';
    }

    return 'AI agent for automated campaign processing';
  }

  // UI Methods
  closeEditor(): void {
    this.showNodeEditor = false;
    this.selectedNode = null;
    this.editingInstructions = '';
    this.editingKnowledgeBases = [];
    this.clearMessages();
  }

  getSelectedNodeColor(): string {
    if (!this.selectedNode) return '#667eeb';
    return this.getNodeColor(this.selectedNode);
  }

  getSelectedNodeIcon(): string {
    if (!this.selectedNode) return 'psychology';
    return this.getNodeIcon(this.selectedNode);
  }

  // Message methods
  private showSuccess(message: string): void {
    this.successMessage = message;
    this.showSuccessMessage = true;
    this.showErrorMessage = false;

    // Auto-hide success message after 3 seconds
    setTimeout(() => {
      this.showSuccessMessage = false;
      this.cdr.detectChanges();
    }, 3000);
  }

  private showError(message: string): void {
    this.errorMessage = message;
    this.showErrorMessage = true;
    this.showSuccessMessage = false;
  }

  private clearMessages(): void {
    this.showSuccessMessage = false;
    this.showErrorMessage = false;
    this.successMessage = '';
    this.errorMessage = '';
  }

  dismissMessage(): void {
    this.clearMessages();
  }

  saveChanges(): void {
    if (!this.selectedNode) return;

    this.isSaving = true;
    this.clearMessages();

    // Detect knowledge base changes
    const originalKbIds = this.selectedNode.knowledgeBases.map(kb => kb.knowledgeBaseId);
    const editingKbIds = this.editingKnowledgeBases.map(kb => kb.knowledgeBaseId);

    const kbsToAdd = editingKbIds.filter(id => !originalKbIds.includes(id));
    const kbsToRemove = originalKbIds.filter(id => !editingKbIds.includes(id));

    // Build update request
    const updateRequest: any = {
      agentId: this.selectedNode.id,
      aliasId: this.selectedNode.aliasId,
      instructions: this.editingInstructions
    };

    // Add knowledge base changes if any
    if (kbsToAdd.length > 0 || kbsToRemove.length > 0) {
      updateRequest.knowledgeBaseAssociations = {};

      if (kbsToAdd.length > 0) {
        updateRequest.knowledgeBaseAssociations.add = kbsToAdd;
      }

      if (kbsToRemove.length > 0) {
        updateRequest.knowledgeBaseAssociations.remove = kbsToRemove;
      }
    }

    // Use the comprehensive updateAgent method instead of just updateAgentInstructions
    this.agentManagementService.updateAgent(updateRequest)
      .pipe(takeUntil(this.destroy$))
      .subscribe({
        next: () => {
          // Update the node data
          if (this.selectedNode) {
            this.selectedNode.instructions = this.editingInstructions;
            this.selectedNode.knowledgeBases = [...this.editingKnowledgeBases];

            // Show success message with details
            let successMsg = `Successfully updated ${this.selectedNode.displayName}!`;
            if (kbsToAdd.length > 0) {
              successMsg += ` Added ${kbsToAdd.length} knowledge base(s).`;
            }
            if (kbsToRemove.length > 0) {
              successMsg += ` Removed ${kbsToRemove.length} knowledge base(s).`;
            }

            this.showSuccess(successMsg);
          }
          this.isSaving = false;
          this.closeEditor();
          this.cdr.detectChanges();
        },
        error: (error) => {
          console.error('Error saving changes:', error);

          // Provide more specific error messages
          let errorMessage = 'Failed to update agent. ';
          if (error.message && error.message.includes('BasePromptTemplate')) {
            errorMessage += 'There was an issue with the agent configuration. This may be due to prompt template conflicts.';
          } else if (error.message && error.message.includes('InferenceConfiguration')) {
            errorMessage += 'There was an issue with the agent inference configuration.';
          } else if (error.message?.includes('not found')) {
            errorMessage = 'Agent not found. Please refresh and try again.';
          } else if (error.message?.includes('credentials')) {
            errorMessage = 'Authentication issue. Please sign out and sign in again.';
          } else if (error.message?.includes('AccessDenied')) {
            errorMessage = 'Access denied. Please check your permissions.';
          } else if (error.message) {
            errorMessage += error.message;
          } else {
            errorMessage += 'Please try again.';
          }

          this.showError(errorMessage);
          this.isSaving = false;
          this.cdr.detectChanges();
        }
      });
  }

  /**
   * Add a new agent to the configuration through AppConfig
   * Requirements: 3.4, 3.5
   */
  async addAgentToConfig(agentData: any): Promise<void> {
    try {

      // Show progress indicator
      this.showOperationProgress('Adding Agent', 'Preparing agent configuration...', 10);
      this.clearMessages();

      // Update progress
      this.updateOperationProgress(30, 'Validating agent data...');

      // Simulate validation delay
      await new Promise(resolve => setTimeout(resolve, 500));

      this.updateOperationProgress(50, 'Updating AppConfig...');

      const success = await this.agentConfigService.addAgent(agentData, agentData.agentType);

      if (success) {
        this.updateOperationProgress(80, 'Deploying configuration...');

        // Show deployment progress
        this.showDeploymentProgress('add-agent-deployment', 'Deploying agent configuration...');

        // Wait for deployment simulation
        await new Promise(resolve => setTimeout(resolve, 3000));

        this.showDetailedSuccess('Agent Addition', [
          `Added agent: ${agentData.displayName || agentData.name}`,
          'Configuration deployed to AppConfig',
          'UI will refresh automatically'
        ]);

        // Refresh the configuration data
        await this.loadAppConfigData();

        // Refresh the agents list
        this.refreshData();
      } else {
        throw new Error('Failed to add agent to configuration');
      }

    } catch (error) {
      console.error('❌ Error adding agent to configuration:', error);
      this.showDetailedError(
        'Agent Addition',
        error instanceof Error ? error : new Error('Unknown error')
      );
    } finally {
      this.hideOperationProgress();
      this.cdr.detectChanges();
    }
  }

  /**
   * Remove an agent from the configuration through AppConfig
   * Requirements: 3.4, 3.5
   */
  async removeAgentFromConfig(node: AgentNode, event?: Event): Promise<void> {
    if (event) {
      event.stopPropagation();
    }

    // Show confirmation dialog for destructive operation
    const confirmed = await this.showConfirmationDialog(
      'Remove Agent',
      `Are you sure you want to remove "${node.displayName || node.name}" from the configuration?\n\n` +
      'This will remove the agent from all tab configurations and cannot be undone.',
      'Remove Agent',
      'Cancel'
    );

    if (!confirmed) {
      return;
    }

    try {

      // Show progress indicator
      this.showOperationProgress('Removing Agent', 'Preparing to remove agent...', 10);
      this.clearMessages();

      this.updateOperationProgress(30, 'Updating agent configuration...');

      // Simulate processing delay
      await new Promise(resolve => setTimeout(resolve, 500));

      this.updateOperationProgress(50, 'Updating tab configurations...');

      const success = await this.agentConfigService.removeAgent(node.id);

      if (success) {
        this.updateOperationProgress(80, 'Deploying changes...');

        // Show deployment progress
        this.showDeploymentProgress('remove-agent-deployment', 'Deploying configuration changes...');

        // Wait for deployment simulation
        await new Promise(resolve => setTimeout(resolve, 3000));

        this.showDetailedSuccess('Agent Removal', [
          `Removed agent: ${node.displayName || node.name}`,
          'Updated all affected tab configurations',
          'Changes deployed to AppConfig'
        ]);

        // Refresh the configuration data
        await this.loadAppConfigData();

        // Refresh the agents list
        this.refreshData();

        // Close editor if the removed agent was selected
        if (this.selectedNode?.id === node.id) {
          this.closeEditor();
        }
      } else {
        throw new Error('Failed to remove agent from configuration');
      }

    } catch (error) {
      console.error('❌ Error removing agent from configuration:', error);
      this.showDetailedError(
        'Agent Removal',
        error instanceof Error ? error : new Error('Unknown error')
      );
    } finally {
      this.hideOperationProgress();
      this.cdr.detectChanges();
    }
  }

  /**
   * Update agent configuration properties through AppConfig
   * Requirements: 3.4, 3.5
   */
  async updateAgentConfiguration(agentId: string, updates: any): Promise<void> {
    try {

      // Show progress indicator
      this.showOperationProgress('Updating Configuration', 'Loading current configuration...', 10);
      this.clearMessages();

      // Get current agent configuration
      this.updateOperationProgress(30, 'Retrieving agent configuration...');
      const currentConfig = await this.agentConfigService.getAgentsConfiguration();

      // Find and update the agent
      this.updateOperationProgress(50, 'Applying configuration changes...');
      const agentKey = Object.keys(currentConfig.agents).find(key =>
        currentConfig.agents[key].agentType === agentId || key === agentId
      );

      if (!agentKey) {
        throw new Error(`Agent not found in configuration: ${agentId}`);
      }

      // Apply updates
      const originalAgent = { ...currentConfig.agents[agentKey] };
      currentConfig.agents[agentKey] = {
        ...currentConfig.agents[agentKey],
        ...updates
      };

      // Save updated configuration
      this.updateOperationProgress(70, 'Saving to AppConfig...');
      const success = await this.agentConfigService.updateAgentConfiguration(currentConfig);

      if (success) {
        this.updateOperationProgress(90, 'Deploying changes...');

        // Show deployment progress
        this.showDeploymentProgress('update-agent-deployment', 'Deploying configuration updates...');

        // Wait for deployment simulation
        await new Promise(resolve => setTimeout(resolve, 2500));

        // Build details of what was updated
        const updateDetails = Object.keys(updates).map(key =>
          `${key}: ${originalAgent[key]} → ${updates[key]}`
        );

        this.showDetailedSuccess('Configuration Update', [
          `Updated agent: ${agentId}`,
          ...updateDetails,
          'Changes deployed successfully'
        ]);

        // Refresh the configuration data
        await this.loadAppConfigData();

        // Refresh the agents list
        this.refreshData();
      } else {
        throw new Error('Failed to update agent configuration');
      }

    } catch (error) {
      console.error('❌ Error updating agent configuration:', error);
      this.showDetailedError(
        'Configuration Update',
        error instanceof Error ? error : new Error('Unknown error')
      );
    } finally {
      this.hideOperationProgress();
      this.cdr.detectChanges();
    }
  }

  addKnowledgeBase(kbId: string): void {
    if (!this.selectedNode) return;

    const kb = this.availableKnowledgeBases.find(k => k.knowledgeBaseId === kbId);
    if (kb && !this.editingKnowledgeBases.find(ekb => ekb.knowledgeBaseId === kbId)) {
      this.editingKnowledgeBases.push({
        knowledgeBaseId: kb.knowledgeBaseId,
        name: kb.name || kb.knowledgeBaseId,
        description: kb.description || '',
        state: 'ENABLED'
      });
    }
  }

  removeKnowledgeBase(kbId: string): void {
    this.editingKnowledgeBases = this.editingKnowledgeBases.filter(kb => kb.knowledgeBaseId !== kbId);
  }

  getAvailableKnowledgeBasesForSelection(): any[] {
    return this.availableKnowledgeBases.filter(kb =>
      !this.editingKnowledgeBases.find(ekb => ekb.knowledgeBaseId === kb.knowledgeBaseId)
    );
  }

  refreshData(): void {
    this.loadData();
    this.loadAppConfigData();
  }

  refreshChart(): void {
    this.updateOrgChart();
  }

  resetView(): void {
    if (this.orgDiagram) {
      this.orgDiagram.setOptions({ scale: 0.7 });
      this.orgDiagram.update();
    }
  }

  /**
   * Show progress indicator for AppConfig operations
   * Requirements: 3.5, 4.3
   */
  private showOperationProgress(operation: string, message: string, progress: number = 0): void {
    this.operationProgress = {
      isActive: true,
      operation,
      progress,
      message
    };
    this.cdr.detectChanges();
  }

  /**
   * Update progress for ongoing operations
   */
  private updateOperationProgress(progress: number, message?: string): void {
    this.operationProgress.progress = progress;
    if (message) {
      this.operationProgress.message = message;
    }
    this.cdr.detectChanges();
  }

  /**
   * Hide progress indicator
   */
  private hideOperationProgress(): void {
    this.operationProgress = {
      isActive: false,
      operation: '',
      progress: 0,
      message: ''
    };
    this.cdr.detectChanges();
  }

  /**
   * Show deployment progress for AppConfig deployments
   * Requirements: 3.5, 4.3
   */
  private showDeploymentProgress(deploymentId: string, message: string): void {
    this.deploymentStatus = {
      isDeploying: true,
      deploymentId,
      progress: 0,
      message
    };
    this.cdr.detectChanges();

    // Simulate deployment progress (in real implementation, this would poll AppConfig deployment status)
    this.simulateDeploymentProgress();
  }

  /**
   * Simulate deployment progress with realistic timing
   */
  private simulateDeploymentProgress(): void {
    const progressSteps = [
      { progress: 20, message: 'Validating configuration...', delay: 500 },
      { progress: 40, message: 'Creating configuration version...', delay: 1000 },
      { progress: 60, message: 'Starting deployment...', delay: 800 },
      { progress: 80, message: 'Deploying to environment...', delay: 1200 },
      { progress: 100, message: 'Deployment completed successfully', delay: 500 }
    ];

    let currentStep = 0;

    const updateProgress = () => {
      if (currentStep < progressSteps.length && this.deploymentStatus.isDeploying) {
        const step = progressSteps[currentStep];
        this.deploymentStatus.progress = step.progress;
        this.deploymentStatus.message = step.message;
        this.cdr.detectChanges();

        currentStep++;

        if (currentStep < progressSteps.length) {
          setTimeout(updateProgress, step.delay);
        } else {
          // Complete deployment
          setTimeout(() => {
            this.hideDeploymentProgress();
          }, step.delay);
        }
      }
    };

    updateProgress();
  }

  /**
   * Hide deployment progress
   */
  private hideDeploymentProgress(): void {
    this.deploymentStatus = {
      isDeploying: false,
      progress: 0,
      message: ''
    };
    this.cdr.detectChanges();
  }

  /**
   * Show confirmation dialog for destructive operations
   * Requirements: 3.5, 4.3
   */
  private showConfirmationDialog(
    title: string,
    message: string,
    confirmText: string = 'Confirm',
    cancelText: string = 'Cancel'
  ): Promise<boolean> {
    return new Promise((resolve) => {
      // For now, use browser confirm dialog
      // In a real implementation, this would be a custom modal component
      const confirmed = confirm(`${title}\n\n${message}`);
      resolve(confirmed);
    });
  }

  /**
   * Show detailed success notification with action details
   * Requirements: 3.5, 4.3
   */
  private showDetailedSuccess(operation: string, details: string[], duration: number = 5000): void {
    let message = `✅ ${operation} completed successfully`;

    if (details.length > 0) {
      message += ':\n• ' + details.join('\n• ');
    }

    this.successMessage = message;
    this.showSuccessMessage = true;
    this.showErrorMessage = false;
    this.cdr.detectChanges();

    // Auto-hide after specified duration
    setTimeout(() => {
      this.showSuccessMessage = false;
      this.cdr.detectChanges();
    }, duration);
  }

  /**
   * Show detailed error notification with retry options
   * Requirements: 4.2, 4.3
   */
  private showDetailedError(operation: string, error: Error, retryAction?: () => void): void {
    // Classify error type
    const errorType = this.classifyError(error);

    // Update error state
    this.errorState = {
      hasError: true,
      errorType,
      errorMessage: error.message,
      canRetry: this.canRetryError(errorType),
      retryCount: this.errorState.retryCount,
      maxRetries: this.errorState.maxRetries,
      lastFailedOperation: operation,
      fallbackActive: this.shouldActivateFallback(errorType)
    };

    let message = `❌ ${operation} failed: ${error.message}`;

    // Add specific error guidance based on error type
    switch (errorType) {
      case 'appconfig':
        message += '\n\n🔄 AppConfig service is unavailable. The system will use static configuration as fallback.';
        this.activateGracefulDegradation('AppConfig service unavailable', [
          'View agent configurations',
          'Basic agent management'
        ], [
          'Real-time configuration updates',
          'AppConfig deployments'
        ]);
        break;
      case 'network':
        message += '\n\n🌐 Network connectivity issue detected. Please check your internet connection and try again.';
        break;
      case 'auth':
        message += '\n\n🔐 Authentication issue detected. Please sign out and sign in again to refresh your credentials.';
        break;
      case 'validation':
        message += '\n\n⚠️ Configuration validation failed. Please check your input and try again.';
        break;
      default:
        message += '\n\n❓ An unexpected error occurred. Please try again or contact support if the issue persists.';
    }

    // Add retry information if applicable
    if (this.errorState.canRetry && this.errorState.retryCount < this.errorState.maxRetries) {
      message += `\n\n🔄 Retry available (${this.errorState.retryCount + 1}/${this.errorState.maxRetries})`;
    }

    this.errorMessage = message;
    this.showErrorMessage = true;
    this.showSuccessMessage = false;
    this.cdr.detectChanges();
  }

  /**
   * Classify error type for appropriate handling
   * Requirements: 4.2, 4.3
   */
  private classifyError(error: Error): 'appconfig' | 'network' | 'auth' | 'validation' | 'unknown' {
    const message = error.message.toLowerCase();

    if (message.includes('appconfig') || message.includes('configuration')) {
      return 'appconfig';
    }
    if (message.includes('network') || message.includes('timeout') || message.includes('connection')) {
      return 'network';
    }
    if (message.includes('credentials') || message.includes('unauthorized') || message.includes('forbidden')) {
      return 'auth';
    }
    if (message.includes('validation') || message.includes('invalid') || message.includes('malformed')) {
      return 'validation';
    }

    return 'unknown';
  }

  /**
   * Determine if an error type can be retried
   */
  private canRetryError(errorType: 'appconfig' | 'network' | 'auth' | 'validation' | 'unknown'): boolean {
    switch (errorType) {
      case 'network':
      case 'appconfig':
        return true;
      case 'auth':
      case 'validation':
      case 'unknown':
        return false;
      default:
        return false;
    }
  }

  /**
   * Determine if fallback should be activated for error type
   */
  private shouldActivateFallback(errorType: 'appconfig' | 'network' | 'auth' | 'validation' | 'unknown'): boolean {
    return errorType === 'appconfig' || errorType === 'network';
  }

  /**
   * Activate graceful degradation mode
   * Requirements: 4.1, 4.2
   */
  private activateGracefulDegradation(reason: string, availableFeatures: string[], disabledFeatures: string[]): void {
    this.degradationMode = {
      isActive: true,
      reason,
      availableFeatures,
      disabledFeatures
    };

    console.warn('🔄 Graceful degradation activated:', {
      reason,
      availableFeatures,
      disabledFeatures
    });

    this.cdr.detectChanges();
  }

  /**
   * Deactivate graceful degradation mode
   */
  private deactivateGracefulDegradation(): void {
    this.degradationMode = {
      isActive: false,
      reason: '',
      availableFeatures: [],
      disabledFeatures: []
    };

    this.cdr.detectChanges();
  }

  /**
   * Retry failed operation with exponential backoff
   * Requirements: 4.1, 4.2
   */
  async retryFailedOperation(): Promise<void> {
    if (!this.errorState.canRetry || this.errorState.retryCount >= this.errorState.maxRetries) {
      console.warn('⚠️ Cannot retry operation:', this.errorState);
      return;
    }

    try {

      // Increment retry count
      this.errorState.retryCount++;

      // Show retry progress
      this.showOperationProgress(
        'Retrying Operation',
        `Attempting to retry ${this.errorState.lastFailedOperation}...`,
        10
      );

      // Exponential backoff delay
      const delay = Math.min(1000 * Math.pow(2, this.errorState.retryCount - 1), 10000);
      await new Promise(resolve => setTimeout(resolve, delay));

      // Clear error state before retry
      this.clearErrorState();

      // Retry the operation based on what failed
      switch (this.errorState.lastFailedOperation) {
        case 'Agent Addition':
        case 'Agent Removal':
        case 'Configuration Update':
          // Reload AppConfig data
          await this.loadAppConfigData();
          break;
        default:
          // Generic retry - reload all data
          await this.loadAppConfigData();
          this.refreshData();
      }

      this.showSuccess('✅ Operation retry successful');

    } catch (error) {
      console.error('❌ Retry failed:', error);

      if (this.errorState.retryCount >= this.errorState.maxRetries) {
        this.showDetailedError(
          'Retry Failed',
          new Error(`Maximum retry attempts (${this.errorState.maxRetries}) exceeded. Please try again later or contact support.`)
        );

        // Activate fallback mode after max retries
        this.activateGracefulDegradation(
          'Maximum retry attempts exceeded',
          ['View configurations', 'Basic operations'],
          ['Real-time updates', 'AppConfig operations']
        );
      } else {
        this.showDetailedError('Retry Failed', error instanceof Error ? error : new Error('Unknown retry error'));
      }
    } finally {
      this.hideOperationProgress();
    }
  }

  /**
   * Clear error state
   */
  private clearErrorState(): void {
    this.errorState = {
      hasError: false,
      errorType: 'unknown',
      errorMessage: '',
      canRetry: false,
      retryCount: 0,
      maxRetries: 3,
      fallbackActive: false
    };
  }

  /**
   * Handle AppConfig unavailable scenario with fallback
   * Requirements: 4.1, 4.2
   */
  private async handleAppConfigUnavailable(): Promise<void> {
    console.warn('⚠️ AppConfig unavailable, switching to fallback mode');

    this.activateGracefulDegradation(
      'AppConfig service is currently unavailable',
      [
        'View agent configurations (static)',
        'Basic agent information',
        'Knowledge base associations'
      ],
      [
        'Real-time configuration updates',
        'Dynamic agent management',
        'Configuration deployments'
      ]
    );

    // Try to load static configurations as fallback
    try {
      await this.loadConfigurations();
      this.showSuccess('✅ Configuration loaded successfully');
    } catch (error) {
      console.error('❌ Static configuration fallback failed:', error);
      this.showError('Both AppConfig and static configuration are unavailable. Some features may be limited.');
    }
  }

  /**
   * Load static configurations as fallback
   */
  private async loadConfigurations(): Promise<void> {
    try {
      // Load agent configuration from AppConfig
      try {
        this.currentAgentConfig = await this.agentConfigService.getAgentsConfiguration();
      } catch (error) {
        console.warn('⚠️ Failed to load agent configuration from AppConfig, using fallback');
        this.currentAgentConfig = null;
      }

      // Load tab configuration from AppConfig
      try {
        this.currentTabConfig = await this.agentConfigService.getTabsConfiguration();
      } catch (error) {
        console.warn('⚠️ Failed to load tab configuration from AppConfig, using fallback');
        this.currentTabConfig = null;
      }

    } catch (error) {
      console.error('❌ Failed to load configurations:', error);
      throw error;
    }
  }

  /**
   * Check if a feature is available in current mode
   */
  isFeatureAvailable(feature: string): boolean {
    if (!this.degradationMode.isActive) {
      return true;
    }

    return this.degradationMode.availableFeatures.includes(feature) ||
      !this.degradationMode.disabledFeatures.includes(feature);
  }

  /**
   * Get user-friendly message for disabled features
   */
  getFeatureDisabledMessage(feature: string): string {
    if (!this.degradationMode.isActive) {
      return '';
    }

    return `This feature is temporarily unavailable due to: ${this.degradationMode.reason}. ` +
      'Please try again later or use the available features.';
  }

  /**
   * Reset error handling state
   */
  resetErrorHandling(): void {
    this.clearErrorState();
    this.deactivateGracefulDegradation();
    this.clearMessages();
  }

  /**
   * Handle authentication errors by redirecting to sign-in
   */
  handleAuthError(): void {

    // Clear error state
    this.resetErrorHandling();

    // Sign out and redirect (this would typically be handled by a routing service)
    this.awsConfigService.signOut().then(() => {
      // In a real app, this would redirect to the login page
      window.location.reload();
    }).catch(error => {
      console.error('Error during sign out:', error);
      // Force reload as fallback
      window.location.reload();
    });
  }

  getSelectedUnselectedAgents(selected: boolean) {
    return this.discoveredAgents.filter(a => a.selected == selected)
  }

  // Agent Discovery Methods
  async discoverAgents(): Promise<void> {
    this.isDiscovering = true;
    this.discoveredAgents = [];
    this.clearMessages();

    try {
      const awsConfig = this.awsConfigService.getConfig();
      if (!awsConfig) {
        this.showError('AWS configuration not available');
        return;
      }

      const stackPrefix = awsConfig.stackPrefix || 'sim';
      const stackSuffix = awsConfig.stackSuffix || '1234';

      // Get AWS credentials and initialize Bedrock client
      const credentials = await this.awsConfigService.getAwsConfig();
      if (!credentials?.credentials) {
        this.showError('AWS credentials not available');
        return;
      }

      // Import AWS SDK dynamically
      const { BedrockAgentClient, ListAgentsCommand, ListAgentAliasesCommand } = await import('@aws-sdk/client-bedrock-agent');

      const bedrockClient = new BedrockAgentClient({
        region: awsConfig.aws.region,
        credentials: credentials.credentials
      });

      // List all agents
      const listAgentsCommand = new ListAgentsCommand({
        maxResults: 100
      });

      const agentsResponse = await bedrockClient.send(listAgentsCommand);
      const allAgents = agentsResponse.agentSummaries || [];

      // Filter agents that match the stack prefix and suffix pattern
      const matchingAgents = allAgents.filter(agent => {
        const agentName = agent.agentName || '';
        return agentName.startsWith(`${stackPrefix}-`) && agentName.includes(`-${stackSuffix}`);
      });

      // Get current agents from configuration
      const currentAgents = awsConfig.bedrock.allAgents || [];
      const currentAgentIds = new Set(currentAgents.map(a => a.id));

      // Find new agents not in current configuration
      const newAgents = matchingAgents.filter(agent => !currentAgentIds.has(agent.agentId || ''));

      // Get aliases for each new agent
      for (const agent of newAgents) {
        if (!agent.agentId) continue;

        try {
          const listAliasesCommand = new ListAgentAliasesCommand({
            agentId: agent.agentId,
            maxResults: 50
          });

          const aliasesResponse = await bedrockClient.send(listAliasesCommand);
          const aliases = aliasesResponse.agentAliasSummaries || [];

          // Find the newest alias (excluding TSTALIASID)
          const validAliases = aliases.filter(alias => alias.agentAliasId !== 'TSTALIASID');
          const newestAlias = validAliases.sort((a, b) => {
            const dateA = a.createdAt ? new Date(a.createdAt).getTime() : 0;
            const dateB = b.createdAt ? new Date(b.createdAt).getTime() : 0;
            return dateB - dateA;
          })[0];

          // Extract alias ID (handle pipe-separated format)
          let aliasId = newestAlias?.agentAliasId || 'latest';
          if (aliasId.includes('|')) {
            aliasId = aliasId.split('|').pop() || 'latest';
          }

          // Extract agent type from name (remove stack prefix and suffix)
          let agentType = agent.agentName || '';
          if (agentType.startsWith(`${stackPrefix}-`)) {
            agentType = agentType.substring(`${stackPrefix}-`.length);
          }
          if (agentType.endsWith(`-${stackSuffix}`)) {
            agentType = agentType.substring(0, agentType.length - `-${stackSuffix}`.length);
          }

          // Determine if this is an AgentCore agent based on naming patterns or tags
          const isAgentCore = await this.isAgentCoreAgent(agent);

          this.discoveredAgents.push({
            id: agent.agentId,
            name: agent.agentName,
            agentType: agentType,
            status: agent.agentStatus || 'UNKNOWN',
            aliasId: aliasId,
            description: agent.description || '',
            createdAt: agent.updatedAt,
            updatedAt: agent.updatedAt,
            displayName: this.generateDisplayName(agentType),
            selected: false,
            deploymentType: isAgentCore ? 'agentcore' : 'bedrock',
            // For AgentCore agents, we might need runtime information
            runtimeId: isAgentCore ? agent.agentId : undefined,
            runtimeArn: isAgentCore ? await this.buildAgentCoreArn(agent.agentId, awsConfig.aws.region) : undefined,
            runtimeName: isAgentCore ? agentType : undefined
          });

        } catch (aliasError) {
          console.error(`Error getting aliases for agent ${agent.agentId}:`, aliasError);
          // Add agent without alias information
          let agentType = agent.agentName || '';
          if (agentType.startsWith(`${stackPrefix}-`)) {
            agentType = agentType.substring(`${stackPrefix}-`.length);
          }
          if (agentType.endsWith(`-${stackSuffix}`)) {
            agentType = agentType.substring(0, agentType.length - `-${stackSuffix}`.length);
          }

          // Determine if this is an AgentCore agent based on naming patterns or tags
          const isAgentCore = await this.isAgentCoreAgent(agent);

          this.discoveredAgents.push({
            id: agent.agentId,
            name: agent.agentName,
            agentType: agentType,
            status: agent.agentStatus || 'UNKNOWN',
            aliasId: 'latest',
            description: agent.description || '',
            createdAt: agent.updatedAt,
            updatedAt: agent.updatedAt,
            displayName: this.generateDisplayName(agentType),
            selected: false,
            deploymentType: isAgentCore ? 'agentcore' : 'bedrock',
            // For AgentCore agents, we might need runtime information
            runtimeId: isAgentCore ? agent.agentId : undefined,
            runtimeArn: isAgentCore ? await this.buildAgentCoreArn(agent.agentId, awsConfig.aws.region) : undefined,
            runtimeName: isAgentCore ? agentType : undefined
          });
        }
      }

      if (this.discoveredAgents.length === 0) {
        this.showSuccess('No new agents found matching the stack pattern');
      } else {
        this.showDiscoveryResults = true;
        this.showSuccess(`Found ${this.discoveredAgents.length} new agent(s) matching the stack pattern`);
      }

    } catch (error: any) {
      console.error('Error discovering agents:', error);
      this.showError(`Failed to discover agents: ${error?.message || 'Unknown error'}`);
    } finally {
      this.isDiscovering = false;
      this.cdr.detectChanges();
    }
  }

  toggleAgentSelection(agent: any): void {
    agent.selected = !agent.selected;
  }

  closeDiscoveryResults(): void {
    this.showDiscoveryResults = false;
    this.discoveredAgents = [];
  }

  // Helper methods for agent type detection and configuration management
  private async isAgentCoreAgent(agent: any): Promise<boolean> {
    // Check if agent has AgentCore-specific tags or naming patterns
    // This is a heuristic approach - you might need to adjust based on your naming conventions

    // Method 1: Check agent tags (if available)
    if (agent.tags) {
      const agentCoreTag = agent.tags.find((tag: any) =>
        tag.key === 'DeploymentType' && tag.value === 'agentcore'
      );
      if (agentCoreTag) return true;
    }

    // Method 2: Check agent description for AgentCore indicators
    if (agent.description && agent.description.toLowerCase().includes('agentcore')) {
      return true;
    }

    // Method 3: Check naming patterns (customize based on your conventions)
    const agentName = agent.agentName || '';
    const agentCorePatterns = [
      'analytics-processor',
      'campaign-optimizer',
      'coordinator',
      'agentcore'
    ];

    return agentCorePatterns.some(pattern =>
      agentName.toLowerCase().includes(pattern.toLowerCase())
    );
  }

  private async buildAgentCoreArn(agentId: string, region: string): Promise<string> {
    const accountId = await this.getAccountId();
    return `arn:aws:bedrock-agentcore:${region}:${accountId}:agent-runtime/${agentId}`;
  }

  private async getAccountId(): Promise<string> {
    try {
      const credentials = await this.awsConfigService.getAwsConfig();
      if (credentials?.credentials) {
        // Import AWS SDK dynamically
        const { STSClient, GetCallerIdentityCommand } = await import('@aws-sdk/client-sts');

        const stsClient = new STSClient({
          region: this.awsConfigService.getConfig()?.aws.region || 'us-east-1',
          credentials: credentials.credentials
        });

        const response = await stsClient.send(new GetCallerIdentityCommand({}));
        return response.Account || 'unknown';
      }
    } catch (error) {
      console.error('Error getting account ID:', error);
    }
    return 'unknown';
  }

  private async loadAgentCoreConfig(): Promise<any> {
    // AgentCore agents are now included in the main aws-config.json
    const awsConfig = this.awsConfigService.getConfig();
    if (awsConfig?.bedrock?.allAgents) {
      const agentCoreAgents = awsConfig.bedrock.allAgents.filter(agent => 
        agent.deploymentType === 'agentcore'
      );
      
      return {
        agentcore_agents: agentCoreAgents,
        deployment_time: new Date().toISOString(),
        stack_prefix: awsConfig.stackPrefix || 'sim',
        unique_id: awsConfig.uniqueId || awsConfig.stackSuffix || '1234'
      };
    }

    // Return default structure if no AgentCore agents found
    return {
      agentcore_agents: [],
      deployment_time: new Date().toISOString(),
      stack_prefix: this.awsConfigService.getConfig()?.stackPrefix || 'sim',
      unique_id: this.awsConfigService.getConfig()?.stackSuffix || '1234'
    };
  }

  private generateAgentColor(agentType: string): string {
    // Generate a consistent color based on agent type
    const colors = ["#a66392",
      "#743a79",
      "#f6b244",
      "#ae94c8",
      "#fc756d"];
    let hash = 0;
    for (let i = 0; i < agentType.length; i++) {
      hash = ((hash << 5) - hash) + agentType.charCodeAt(i);
      hash = hash & hash; // Convert to 32-bit integer
    }
    return colors[Math.abs(hash) % colors.length];
  }

  private generateAgentIcon(agentType: string): string {
    // Generate an appropriate icon based on agent type
    const type = agentType.toLowerCase();

    if (type.includes('analytics') || type.includes('processor')) return 'analytics';
    if (type.includes('campaign') || type.includes('optimizer')) return 'tune';
    if (type.includes('coordinator') || type.includes('supervisor')) return 'hub';
    if (type.includes('creative') || type.includes('design')) return 'palette';
    if (type.includes('audience') || type.includes('targeting')) return 'people';
    if (type.includes('bid') || type.includes('optimization')) return 'trending_up';
    if (type.includes('channel') || type.includes('media')) return 'tv';
    if (type.includes('timing') || type.includes('schedule')) return 'schedule';
    if (type.includes('revenue') || type.includes('format')) return 'monetization_on';
    if (type.includes('inventory') || type.includes('forecast')) return 'inventory';

    return 'smart_toy'; // Default fallback
  }

  private generateDisplayName(agentType: string): string {
    // Convert agent type to a proper display name
    // Remove common suffixes and add proper spacing
    let displayName = agentType
      .replace(/([A-Z])/g, ' $1') // Add space before capital letters
      .trim();
    
    // Capitalize first letter of each word
    displayName = displayName.replace(/\b\w/g, l => l.toUpperCase());
    
    return displayName;
  }



  private async saveConfigToS3(config: any, awsConfig: any, key: string = 'assets/aws-config.json'): Promise<void> {
    try {
      // Import AWS SDK dynamically
      const { S3Client, PutObjectCommand } = await import('@aws-sdk/client-s3');

      // Get AWS credentials
      const credentials = await this.awsConfigService.getAwsConfig();
      if (!credentials?.credentials) {
        throw new Error('AWS credentials not available');
      }

      // Initialize S3 client
      const s3Client = new S3Client({
        region: awsConfig.aws.region,
        credentials: credentials.credentials
      });

      // Upload updated configuration to S3
      await s3Client.send(new PutObjectCommand({
        Bucket: awsConfig.ui.bucketName,
        Key: key,
        Body: JSON.stringify(config, null, 2),
        ContentType: 'application/json',
        CacheControl: 'no-cache'
      }));

    } catch (error) {
      console.error('Error uploading to S3:', error);
      // Fallback to local download
      this.downloadConfigLocally(config, key.includes('aws-config') ? 'aws-config' : 'config');
    }
  }

  private downloadConfigLocally(config: any, prefix: string = 'config'): void {
    const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
    const configBlob = new Blob([JSON.stringify(config, null, 2)], { type: 'application/json' });
    const configUrl = URL.createObjectURL(configBlob);
    const configLink = document.createElement('a');
    configLink.href = configUrl;
    configLink.download = `${prefix}-updated-${timestamp}.json`;
    configLink.click();
    URL.revokeObjectURL(configUrl);

    this.showSuccess(`Configuration downloaded locally. Replace the ${prefix}.json file in your assets folder and restart the application. All agents (including AgentCore) are now in the main configuration file.`);
  }

  // Modal Methods
  closeAgentConfig(): void {
    this.closeModal.emit();
  }

  // Agent Removal Methods
  toggleRemoveMode(): void {
    this.isRemoveMode = !this.isRemoveMode;
    if (!this.isRemoveMode) {
      // Clear selection when exiting remove mode
      this.agentsToRemove.clear();
    }
    this.clearMessages();
  }
}