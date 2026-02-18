import { Component, Input, OnInit, OnDestroy, AfterViewInit, ViewChildren, ViewChild, QueryList, ElementRef, Output, EventEmitter } from '@angular/core';
import { BedrockService } from '../../services/bedrock.service';
import { AwsConfigService } from '../../services/aws-config.service';
import { AgentConfigService } from '../../services/agent-config.service';
import { SessionManagerService, SessionInfo } from '../../services/session-manager.service';
import { DemoTrackingService } from '../../services/demo-tracking.service';
import { TextUtils } from '../../utils/text-utils';
import { Publisher, Content } from '../../models/advertising';
import { ChatInterfaceComponent } from '../chat-interface/chat-interface.component';
import { VisibilitySettings } from '../visibility-settings-modal/visibility-settings-modal.component';
import { SessionSummary } from '../session-prompt-modal/session-prompt-modal.component';
import { EnrichedAgent, TabConfiguration } from 'src/app/models/application-models';



@Component({
  selector: 'app-generic-tab',
  templateUrl: './generic-tab.component.html',
  styleUrls: ['./generic-tab.component.scss']
})
export class GenericTabComponent implements OnInit, OnDestroy, AfterViewInit {
  @Input() tabId: string = 'generic-tab-1';
  @ViewChildren('visualizationCard') visualizationCards!: QueryList<ElementRef>;
  @ViewChild(ChatInterfaceComponent) chatInterface!: ChatInterfaceComponent;
  @Input() currentUser: any;
  @Output() messagesUpdated: EventEmitter<any> = new EventEmitter<any>();
  // Configuration data
  @Input() tabConfig: TabConfiguration | null = null;

  // Agent selection
  selectedAgent: EnrichedAgent|null = null;
  availableAgents: any[] = [];
  showAgentSelector = false;

  // State management
  isProcessing = false;
  lastResponse: any = null;
  error: string | null = null;
  isLoading = true;
  showScenariosPanel = false;
  showSessionsPanel = false;
  showPreferencesSelector = false;
  showPreferencesPanel = false;

  // Visibility Settings
  showVisibilitySettings = false;
  visibilitySettings: VisibilitySettings = {
    hiddenMessageTypes: ['tool-trace', 'error'],
    includedContextSections: ['currentCampaign', 'selectedAgent', 'userProfile'],
    hiddenAgents: []
  };

  // Session Prompt Modal
  showSessionPrompt = false;
  currentSessionInfo: SessionInfo | null = null;
  sessionSummary: SessionSummary | null = null;
  isLoadingSessionSummary = false;
  private hasCheckedSession = false;

  // Export state - getter to sync with chat interface
  get isExportingPdf(): boolean {
    return this.chatInterface?.isExportingPdf || false;
  }

  // Context section hover modal state
  hoveredContextSection: string | null = null;
  stickyContextSection: string | null = null;
  hoverTimeout: any = null;
  isClickingContextSection = false;

  // Context data
  currentPublisher: any | null = null;
  currentCampaign: any = null;
  currentContent: any | null = null;
  //publishers: Publisher[] = [];
  //campaigns: any[] = [];
  contextData: any = undefined;
  content: Content[] = [];


  // Track first message per agent to only inject context data once
  private agentFirstMessages = new Map<string, boolean>();

  selectedScenarioIndex: number | null = null;

  // Agent color assignment moved to AwsConfigService for shared access

  // Expose Math to template
  Math = Math;

  constructor(
    private bedrockService: BedrockService,
    private awsConfig: AwsConfigService,
    private agentConfig: AgentConfigService,
    private sessionManager: SessionManagerService,
    private demoTrackingService: DemoTrackingService
  ) { }

  async ngOnInit(): Promise<void> {
    // Ensure panels start closed
    this.showScenariosPanel = false;
    this.showPreferencesSelector = false;
    this.showAgentSelector = false;
    this.showPreferencesPanel = false;
    this.initializeAgentSelection();
    this.loadTabConfig(this.tabConfig);

    // Session check moved to ngAfterViewInit to ensure chatInterface ViewChild is available
  }

  ngOnDestroy(): void {
    // Cleanup if needed
    this.removeHoverListeners();
    if (this.hoverTimeout) {
      clearTimeout(this.hoverTimeout);
    }
    this.isClickingContextSection = false;
  }

  triggerMessagesUpdatedEvent(messages) {
    this.messagesUpdated.emit(messages);
  }

  ngAfterViewInit(): void {
    // Wait for view to be fully rendered before setting up hover effects
    setTimeout(() => {
      this.adjustPositions();
    }, 100);

    // Re-setup hover effects when the visualization cards change
    this.visualizationCards.changes.subscribe(() => {
      setTimeout(() => {
        this.adjustPositions();
      }, 50);
    });

    // Check for existing session after view is initialized (chatInterface ViewChild is now available)
    setTimeout(() => {
      this.checkForExistingSession();
    }, 0);
  }

  private loadTabConfig(tabConfig): void {
    // Set up data from configuration
    this.content = tabConfig.contextData?.content || [];

    // Set defaults
    this.contextData = tabConfig.contextData;
    this.currentContent = this.content.length > 0 ? this.content[0] : null;

    // Ensure panels are closed after loading
    this.showScenariosPanel = false;
    this.showPreferencesSelector = false;
    this.showAgentSelector = false;
    this.showPreferencesPanel = false;

    // Force view update with a small delay
    setTimeout(() => {
      this.showScenariosPanel = false;
      this.showPreferencesSelector = false;

      // Setup hover effects after view updates
      setTimeout(() => {
        this.adjustPositions();
      }, 3000);
    }, 50);

    // Log agent configuration details
    if (tabConfig.defaultAgent) {
      const agentInfo = this.awsConfig.getAgentByOtherNames(tabConfig.defaultAgent);
      if (agentInfo && agentInfo.name) {
        // Agent configuration loaded successfully
      }
    }
    this.isLoading = false;
  }



  private initializeAgentSelection(): void {
    if (!this.tabConfig) return;

    // Get all available agents from the centralized service
    const allAgents = this.agentConfig.getActiveAgents();

    // Filter agents based on tab configuration
    if (this.tabConfig.availableAgents && this.tabConfig.availableAgents.length > 0) {
      // Use tab-specific filtered agents for better UX
      this.availableAgents = this.tabConfig.availableAgents.map(agentType =>
        allAgents.find(agent => agent.agentType === agentType || agent.key === agentType)
      ).filter(agent => agent) as any[];
    } else {
      // No filtering specified - show all agents (flexible mode)
      this.availableAgents = allAgents;
    }
  }

  // Agent selection methods
  toggleAgentSelector(): void {
    this.showAgentSelector = !this.showAgentSelector;
    // Close other panels
    if (this.showAgentSelector) {
      this.showScenariosPanel = false;
      this.showPreferencesSelector = false;
    }
  }

  selectAgent(agentType: EnrichedAgent): void {
    this.selectedAgent = agentType;
    this.showAgentSelector = false;
  }

  closeAgentSelector(): void {
    this.showAgentSelector = false;
  }

  // Get current agent information
  getCurrentAgent(): any {
    if (this.selectedAgent) {
      // Use the centralized agent lookup
      return this.agentConfig.getAgent(this.selectedAgent.name);
    }

    // Fallback to default agent
    return this.agentConfig.getDefaultAgent();
  }

  // Helper methods for the template
  get hasValidResponse(): boolean {
    return !!this.lastResponse?.response && !this.error;
  }

  get agentResponseText(): string {
    return this.lastResponse?.response || '';
  }

  get agentCitations(): any[] {
    return this.lastResponse?.citations || [];
  }

  clearResponse(): void {
    this.lastResponse = null;
    this.error = null;
  }

  // Get context data combining tab context with selected agent
  getContextData(): any {
    if (!this.tabConfig) return {};

    return this.tabConfig.contextData;
  }

  // Get visualizations to show based on configuration
  getVisualizationsToShow(): any[] {
    if (this.tabConfig && this.tabConfig.visualizations) return this.tabConfig.visualizations;
    if (!this.tabConfig?.visualizations) {
      // Default visualizations if none configured
      const defaultViz = [
        {
          type: 'metrics-visualization',
          visualizationType: 'metrics',
          title: 'Key Metrics',
          data: {
            visualizationType: 'metrics',
            title: 'Key Performance Indicators',
            metrics: [
              {
                primaryLabel: 'Performance Overview',
                items: [
                  { primaryLabel: 'Revenue Forecast', actualValue: this.formatMetricValue('totalRevenueForecast', 'currency') },
                  { primaryLabel: 'Fill Rate', actualValue: this.formatMetricValue('averageFillRate', 'percentage') },
                  { primaryLabel: 'Yield Optimization', actualValue: this.formatMetricValue('averageYieldOptimization', 'percentage') },
                  { primaryLabel: 'Avg CPM Floor', actualValue: this.formatMetricValue('averageCPMFloor', 'currency') }
                ]
              }
            ]
          }
        }
      ];

      // Only add context cards if there's data for them
      if (this.currentPublisher) {
        defaultViz.push({ type: 'current-publisher' } as any);
      }
      if (this.currentCampaign) {
        defaultViz.push({ type: 'current-campaign' } as any);
      }

      return defaultViz;
    }

    // Return configured visualizations, filtering out context selectors and processing data
    const configuredViz = this.tabConfig.visualizations
      .filter((viz: any) => viz.type !== 'context-selector')
      .map((viz: any) => this.processVisualizationData(viz));

    // Only add context cards if there's data for them
    if (this.currentPublisher) {
      configuredViz.push({ type: 'current-publisher' } as any);
    }
    if (this.currentCampaign) {
      configuredViz.push({ type: 'current-campaign' } as any);
    }
    if (this.currentContent) {
      configuredViz.push({ type: 'current-content' } as any);
    }

    return configuredViz;
  }

  // Process visualization data to ensure it's in the correct format for components
  processVisualizationData(visualization: any): any {
    const processed = { ...visualization };

    // Ensure visualizationType is set
    if (!processed.visualizationType && processed.type) {
      processed.visualizationType = processed.type.replace('-visualization', '');
    }

    // Process data based on visualization type
    switch (processed.type) {
      case 'metrics-visualization':
        if (processed.data && !processed.metricData) {
          processed.metricData = this.processDynamicMetrics(processed.data);
        }
        break;
      case 'timeline-visualization':
        if (processed.data && !processed.timelineData) {
          processed.timelineData = processed.data;
        }
        break;
      case 'allocations-visualization':
        if (processed.data && !processed.channelAllocations) {
          processed.channelAllocations = processed.data;
        }
        break;
      case 'segments-visualization':
        if (processed.data && !processed.segmentCards) {
          processed.segmentCards = processed.data;
        }
        break;
      case 'channels-visualization':
        if (processed.data && !processed.channelCards) {
          processed.channelCards = processed.data;
        }
        break;
    }

    return processed;
  }

  // Process metrics with dynamic values
  private processDynamicMetrics(metricsData: any): any {
    const processed = { ...metricsData };

    if (processed.metrics) {
      processed.metrics = processed.metrics.map((metricGroup: any) => {
        const processedGroup = { ...metricGroup };

        if (processedGroup.items) {
          processedGroup.items = processedGroup.items.map((item: any) => {
            const processedItem = { ...item };

            // If actualValue is "dynamic", calculate it from valueKey
            if (processedItem.actualValue === 'dynamic' && processedItem.valueKey) {
              processedItem.actualValue = this.formatMetricValue(processedItem.valueKey, processedItem.format || 'number');
            }

            return processedItem;
          });
        }

        return processedGroup;
      });
    }

    return processed;
  }

  // TrackBy function for ngFor optimization
  trackVisualization(_index: number, item: any): any {
    return item.type + (item.title || '');
  }

  // Format metric values based on the configuration
  formatMetricValue(valueKey: string, format: string): string {
    const contextData = this.tabConfig?.contextData;
    if (!contextData) {
      // console.warn(`❌ No context data available for metric: ${valueKey}`);
      return 'N/A';
    }

    // Calculate values based on valueKey
    let value: number = 0;

    switch (valueKey) {
      // Publisher Inventory metrics
      case 'totalRevenueForecast':
        value = contextData.publishers ? contextData.publishers.reduce((sum, p) => sum + p.revenuePrediction, 0) : 0
        break;
      case 'averageFillRate':
        value = contextData.publishers.length > 0
          ? contextData.publishers.reduce((sum, p) => sum + p.fillRateForecast, 0) / contextData.publishers.length
          : 0;
        break;
      case 'averageYieldOptimization':
        value = contextData.publishers.length > 0
          ? contextData.publishers.reduce((sum, p) => sum + p.yieldOptimization, 0) / contextData.publishers.length
          : 0;
        break;
      case 'averageCPMFloor':
        value = contextData.publishers.length > 0
          ? contextData.publishers.reduce((sum, p) => sum + p.cpmFloor, 0) / contextData.publishers.length
          : 0;
        break;

      // RTB metrics
      case 'winRate':
        value = 22; // Mock win rate
        break;
      case 'avgCPM':
        value = 6.75; // Mock average CPM
        break;
      case 'responseTime':
        value = 45; // Mock response time in ms
        break;
      case 'qualityScore':
        value = 8.2; // Mock quality score
        break;

      // Campaign Planning metrics
      case 'targetROAS':
        value = this.currentCampaign?.expectedMetrics?.roas || 3.5;
        break;
      case 'expectedCTR':
        value = (this.currentCampaign?.expectedMetrics?.ctr || 0.03) * 100;
        break;
      case 'budgetUtilization':
        value = 87; // Mock budget utilization
        break;
      case 'conversionRate':
        value = (this.currentCampaign?.expectedMetrics?.cvr || 0.04) * 100;
        break;

      // Media Planning metrics
      case 'totalBudget':
        value = contextData.mediaPlans?.[0]?.totalBudget || contextData.mediaChannels?.reduce((sum: number, ch: any) => sum + ((ch.allocation * 125000) / 100), 0) || 125000;
        break;
      case 'activeChannels':
        value = contextData.mediaChannels?.length || contextData.mediaPlans?.[0]?.channels?.length || 4;
        break;
      case 'totalReach':
        value = contextData.mediaChannels?.reduce((sum: number, ch: any) => sum + ch.reach, 0) || 8300000;
        break;
      case 'inventoryUtilization':
        value = contextData.mediaMetrics?.inventoryUtilization || 92;
        break;
      case 'advertiserSatisfaction':
        value = contextData.mediaMetrics?.advertiserSatisfaction || 87;
        break;
      case 'averageCPM':
        value = contextData.mediaMetrics?.averageCPM || 18.75;
        break;

      default:
        // Try to get from contextData.inventoryMetrics or other sources
        if (contextData.inventoryMetrics && contextData.inventoryMetrics[valueKey] !== undefined) {
          value = contextData.inventoryMetrics[valueKey];
        } else if (contextData[valueKey] !== undefined) {
          value = contextData[valueKey];
        } else {
          // console.warn(`❌ Unknown metric key: ${valueKey}`);
          value = 0;
        }
        break;
    }

    // Format based on type
    switch (format) {
      case 'currency':
        return this.formatRevenue(value);
      case 'percentage':
        // Show decimal places for rates/percentages for better precision
        if (valueKey === 'conversionRate' || valueKey === 'expectedCTR') {
          return `${value.toFixed(2)}`;
        }
        return `${Math.round(value)}`;
      case 'number':
        if (valueKey === 'responseTime') {
          return `${value}ms`;
        } else if (valueKey === 'qualityScore') {
          return `${value}/10`;
        }
        return value.toLocaleString();
      default:
        return value.toString();
    }
  }

  // Agent type helper methods
  getAgentDisplayName(agentType: string): string {
    return TextUtils.pascalOrCamelToDisplayName(agentType);
  }

  // Helper method to normalize agent names for consistent color calculation
  private normalizeAgentNameForColor(agentName: string): string {
    // First, use the centralized normalization utility
    const normalizedName = TextUtils.pascalOrCamelToDisplayName(TextUtils.removeStackPrefixSuffix(agentName, this.awsConfig.getStackPrefix(), this.awsConfig.getStackSuffix()));

    // Then try to get the canonical agent info from AWS config
    try {
      const agentInfo = this.awsConfig.getAgentByOtherNames(normalizedName);
      if (agentInfo && agentInfo.name) {
        return agentInfo.name;
      }
    } catch (error) {
      // If lookup fails, use the normalized name for consistency
    }

    return normalizedName;
  }

  getAgentIcon(): string {
    return 'psychology';
  }

  exportChatToPdf(): void {
    if (this.chatInterface) {
      this.chatInterface.showPdfExportOptions();
    }
  }

  // Panel control methods
  toggleScenariosPanel(): void {
    this.showScenariosPanel = !this.showScenariosPanel;
    // if (this.showScenariosPanel) {
    //   this.showPreferencesSelector = false;
    // }
  }

  togglePreferencesSelector(): void {
    this.showVisibilitySettings = true;
  }

  // Visibility Settings Methods
  onVisibilitySettingsChanged(settings: VisibilitySettings): void {
    this.visibilitySettings = settings;

    // Pass the settings to the chat interface if it exists
    if (this.chatInterface) {
      this.chatInterface.onVisibilitySettingsChanged(settings);
    }
  }

  onVisibilitySettingsClosed(): void {
    this.showVisibilitySettings = false;
  }

  closeContextSelector(): void {
    this.showPreferencesSelector = false;
  }

  // Context panel methods
  getContextButtonLabel(): string {
    return this.tabConfig?.contextButtonLabel || '... context';
  }

  onContextTriggerLeave(event: MouseEvent): void {
    // Check if mouse is moving towards the panel
    const rect = (event.target as HTMLElement).getBoundingClientRect();
    const mouseX = event.clientX;
    const mouseY = event.clientY;

    // If mouse is moving right (towards panel), keep panel open briefly
    if (mouseX > rect.right && mouseY >= rect.top && mouseY <= rect.bottom) {
      setTimeout(() => {
        if (!this.showPreferencesPanel) {
          this.showPreferencesPanel = false;
        }
      }, 100);
    } else {
      this.showPreferencesPanel = false;
    }
  }

  // Method to trigger panel expansion animation
  onContextPanelShow(): void {
    // Add expanding class to trigger height animation
    setTimeout(() => {
      const panel = document.querySelector('.floating-context-panel');
      if (panel) {
        panel.classList.add('expanding');
      }
    }, 100);
  }

  onContextPanelHide(): void {
    // Remove expanding class
    const panel = document.querySelector('.floating-context-panel');
    if (panel) {
      panel.classList.remove('expanding');
    }
  }

  selectPublisher(publisher: Publisher): void {
    this.currentPublisher = publisher;
    this.closeContextSelector();
    this.closeContextModal();
  }

  selectCampaign(campaign: any): void {
    this.currentCampaign = campaign;
    this.closeContextSelector();
    this.closeContextModal();
  }

  selectContent(content: Content): void {
    this.currentContent = content;
    this.closeContextSelector();
    this.closeContextModal();
  }

  // Generic context helper methods
  getContextEntityLabel(entityType: string): string {
    const labels: { [key: string]: string } = {
      'publishers': 'Publisher',
      'campaigns': 'Campaign',
      'content': 'Content',
      'channels': 'Channel',
      'segments': 'Segment',
      'audiences': 'Audience',
      'creatives': 'Creative',
      'formats': 'Format'
    };
    return labels[entityType] || entityType.charAt(0).toUpperCase() + entityType.slice(1, -1);
  }

  formatContextValue(value: any): string {
    if (typeof value === 'number') {
      if (value > 1000000) {
        return `$${(value / 1000000).toFixed(1)}M`;
      } else if (value > 1000) {
        return `$${(value / 1000).toFixed(0)}K`;
      } else if (value > 0) {
        return `$${value.toFixed(2)}`;
      }
    }
    return value?.toString() || '';
  }

  getGenericContextItems(): any[] {
    // Return any additional context items from tabConfig that aren't publishers, campaigns, or content
    const contextData = this.tabConfig?.contextData;
    if (!contextData) return [];

    const genericItems: any[] = [];

    // Look for other arrays in contextData that could be context items
    Object.keys(contextData).forEach(key => {
      if (Array.isArray(contextData[key]) &&
        !['publishers', 'campaigns', 'content', 'scenarios'].includes(key)) {
        contextData[key].forEach((item: any, index: number) => {
          genericItems.push({
            ...item,
            contextType: key,
            selected: false,
            id: item.id || `${key}-${index}`
          });
        });
      }
    });

    return genericItems;
  }

  selectGenericContext(item: any): void {
    // Handle selection of generic context items
    // You can extend this to handle different context types as needed
    this.closeContextSelector();
    this.closeContextModal();
  }

  // Context section hover modal methods
  onContextSectionMouseEnter(sectionType: string): void {
    // Clear any existing timeout
    if (this.hoverTimeout) {
      clearTimeout(this.hoverTimeout);
    }

    // Don't show hover if section is already sticky
    if (this.stickyContextSection === sectionType) {
      return;
    }

    // Show hover modal after a short delay
    this.hoverTimeout = setTimeout(() => {
      this.hoveredContextSection = sectionType;
    }, 300);
  }

  onContextSectionMouseLeave(sectionType: string): void {
    // Don't process mouse leave if we're in the middle of clicking
    if (this.isClickingContextSection) {
      return;
    }

    // Clear timeout if mouse leaves before delay
    if (this.hoverTimeout) {
      clearTimeout(this.hoverTimeout);
    }

    // Don't hide if section is sticky
    if (this.stickyContextSection === sectionType) {
      return;
    }

    // Hide hover modal after a short delay to allow moving to modal
    this.hoverTimeout = setTimeout(() => {
      if (this.hoveredContextSection === sectionType && this.stickyContextSection !== sectionType) {
        console.log('Hiding modal for section:', sectionType);
        this.hoveredContextSection = null;
      }
    }, 200);
  }

  onContextSectionClick(sectionType: string, event?: Event): void {
    // Prevent event bubbling
    if (event) {
      event.stopPropagation();
      event.preventDefault();
    }

    // Set flag to prevent mouse leave from interfering
    this.isClickingContextSection = true;

    // Clear any hover timeout first
    if (this.hoverTimeout) {
      clearTimeout(this.hoverTimeout);
    }

    // Make the modal sticky immediately
    this.stickyContextSection = sectionType;
    this.hoveredContextSection = sectionType;

    // Reset the clicking flag after a short delay
    setTimeout(() => {
      this.isClickingContextSection = false;
    }, 100);
  }

  onContextModalMouseEnter(sectionType: string): void {
    // Keep modal open when hovering over it
    if (this.hoverTimeout) {
      clearTimeout(this.hoverTimeout);
    }
  }

  onContextModalMouseLeave(sectionType: string): void {
    // Don't hide if modal is sticky
    if (this.stickyContextSection === sectionType) {
      return;
    }

    // Hide modal when leaving it (if not sticky)
    this.hoverTimeout = setTimeout(() => {
      if (this.hoveredContextSection === sectionType && this.stickyContextSection !== sectionType) {
        console.log('Hiding modal after leaving modal area');
        this.hoveredContextSection = null;
      }
    }, 200);
  }

  closeContextModal(): void {
    // Close any open context modal
    this.hoveredContextSection = null;
    this.stickyContextSection = null;

    if (this.hoverTimeout) {
      clearTimeout(this.hoverTimeout);
    }
  }

  isContextModalVisible(sectionType: string): boolean {
    const visible = this.hoveredContextSection === sectionType || this.stickyContextSection === sectionType;
    return visible;
  }

  isContextModalSticky(sectionType: string): boolean {
    const sticky = this.stickyContextSection === sectionType;
    return sticky;
  }

  // Helper methods for publisher display (similar to publisher-inventory component)
  getTierColor(tier: string): string {
    return tier === 'Premium' ? '#667eea' : '#28a745';
  }

  getScoreColor(score: number): string {
    if (score >= 90) return '#28a745';
    if (score >= 80) return '#ffc107';
    return '#dc3545';
  }

  getDemandPressureColor(pressure: number): string {
    if (pressure >= 15) return '#dc3545';
    if (pressure >= 10) return '#ffc107';
    return '#28a745';
  }

  getFillRateColor(fillRate: number): string {
    if (fillRate >= 85) return '#28a745';
    if (fillRate >= 75) return '#ffc107';
    return '#dc3545';
  }

  getYieldOptimizationColor(score: number): string {
    if (score >= 90) return '#28a745';
    if (score >= 80) return '#ffc107';
    return '#dc3545';
  }

  formatRevenue(revenue: number): string {
    if (revenue >= 1000000) {
      return `$${(revenue / 1000000).toFixed(1)}M`;
    } else if (revenue >= 1000) {
      return `$${(revenue / 1000).toFixed(0)}K`;
    }
    return `$${revenue.toLocaleString()}`;
  }

  getRevenueGrowthColor(growth: number): string {
    if (growth >= 20) return '#28a745';
    if (growth >= 10) return '#ffc107';
    return '#dc3545';
  }

  getDynamicPricingStatus(enabled: boolean): { text: string; color: string; icon: string } {
    return enabled
      ? { text: 'Active', color: '#28a745', icon: 'autorenew' }
      : { text: 'Static', color: '#6c757d', icon: 'lock' };
  }

  async onMessageSent(messageData: any): Promise<void> {
    if (!this.tabConfig) return;

    // Reset state
    this.error = null;
    this.isProcessing = true;

    try {
      // Check if user is authenticated
      if (!this.awsConfig.isAuthenticated()) {
        this.error = 'Please sign in to use the agent';
        return;
      }

      // Use the selected agent instead of messageData.agentType
      let agentToUse:EnrichedAgent = this.getCurrentAgent();
      // Update selectedAgent if an agent was specified in the message event
      agentToUse = messageData.agent||agentToUse
      
      // Get session ID from centralized session manager
      const loginId = this.currentUser?.signInDetails?.loginId;
      const customerName = this.demoTrackingService.getCurrentCustomer();
      const customSessionId = this.sessionManager.getCurrentSessionId(loginId, customerName);

      // Prepare the final query with context data for the selected agent (only on first message to this agent)
      let finalQuery = messageData.message;
      const isFirstMessageToAgent = !this.agentFirstMessages.has(`${agentToUse.agentType}`);

      if (isFirstMessageToAgent) {
        const contextData = this.getContextData();
        let subContextData = (contextData ? contextData.contextData || contextData : {});
        const contextJson = this.formatContextData(subContextData);
        finalQuery = `User: ${loginId}\n\n User's Question: ${messageData.message}\n\n${contextJson}`;

        // Mark that we've sent the first message to this agent
        this.agentFirstMessages.set(`${agentToUse.agentType}`, true);
      } else {
        // For subsequent messages, just include the user info without full context
        finalQuery = `User: ${loginId}\n\n User's Question: ${messageData.message}`;
      }


      // Call the real Bedrock agent with context-enhanced query and custom session ID
      const response = await this.bedrockService.invokeAgent(agentToUse, finalQuery, undefined, customSessionId);

      // Store the response
      this.lastResponse = {
        response: response.response,
        sessionId: response.sessionId,
        citations: response.citations
      };

      /*console.log('✅ Agent response received:', {
        agentUsed: agentToUse,
        responseLength: response.response.length,
        sessionId: response.sessionId,
        hasCitations: !!response.citations?.length,
        includedContext: !!contextData && Object.keys(contextData).length > 0
      });*/

    } catch (error: any) {
      console.error('❌ Agent error:', error);
      this.error = error.message || 'Failed to get response from agent';

      // Provide helpful error context
      if (error.message?.includes('Agent ID not found')) {
        this.error = 'Agent not deployed. Using simulated responses.';
      } else if (error.message?.includes('credentials')) {
        this.error = 'Authentication issue. Please sign out and sign in again.';
      }
    } finally {
      this.isProcessing = false;
    }
  }

  // Content category icon mapping
  getCategoryIcon(category: string): string {
    const categoryIcons: { [key: string]: string } = {
      'Documentary': 'movie',
      'Environmental': 'eco',
      'Tech': 'computer',
      'Technology': 'computer',
      'Innovation': 'lightbulb',
      'Lifestyle': 'home',
      'Entertainment': 'theaters',
      'News': 'newspaper',
      'Sports': 'sports',
      'Education': 'school',
      'Health': 'health_and_safety',
      'Finance': 'account_balance',
      'Travel': 'flight',
      'Food': 'restaurant',
      'Fashion': 'checkroom',
      'Gaming': 'sports_esports',
      'Music': 'music_note',
      'Art': 'palette',
      'Science': 'science',
      'Business': 'business',
      'Politics': 'how_to_vote',
      'Culture': 'museum',
      'History': 'history_edu'
    };
    return categoryIcons[category] || 'article';
  }

  // Content category color mapping
  getCategoryColor(category: string): string {
    const categoryColors: { [key: string]: string } = {
      'Documentary': '#6366f1',
      'Environmental': '#10b981',
      'Tech': '#3b82f6',
      'Technology': '#3b82f6',
      'Innovation': '#f59e0b',
      'Lifestyle': '#ec4899',
      'Entertainment': '#8b5cf6',
      'News': '#ef4444',
      'Sports': '#f97316',
      'Education': '#06b6d4',
      'Health': '#84cc16',
      'Finance': '#14b8a6',
      'Travel': '#0ea5e9',
      'Food': '#f97316',
      'Fashion': '#ec4899',
      'Gaming': '#8b5cf6',
      'Music': '#f59e0b',
      'Art': '#ec4899',
      'Science': '#06b6d4',
      'Business': '#374151',
      'Politics': '#dc2626',
      'Culture': '#7c3aed',
      'History': '#92400e'
    };
    return categoryColors[category] || '#6b7280';
  }

  // Ad format icon mapping
  getFormatIcon(format: string): string {
    const formatIcons: { [key: string]: string } = {
      'Native': 'integration_instructions',
      'Pre-roll': 'play_circle',
      'Mid-roll': 'pause_circle',
      'Post-roll': 'stop_circle',
      'Sponsored Content': 'article',
      'Display': 'crop_landscape',
      'Banner': 'view_headline',
      'Video': 'videocam',
      'Audio': 'mic',
      'Interactive': 'touch_app',
      'Rich Media': 'widgets',
      'Overlay': 'layers',
      'Popup': 'open_in_new',
      'Interstitial': 'fullscreen',
      'Rewarded': 'card_giftcard',
      'Social': 'share',
      'Search': 'search',
      'Shopping': 'shopping_cart',
      'Connected TV': 'tv',
      'Mobile': 'smartphone',
      'Desktop': 'desktop_windows',
      'Tablet': 'tablet'
    };
    return formatIcons[format] || 'ads_click';
  }

  // Ad format color mapping
  getFormatColor(format: string): string {
    const formatColors: { [key: string]: string } = {
      'Native': '#10b981',
      'Pre-roll': '#3b82f6',
      'Mid-roll': '#f59e0b',
      'Post-roll': '#ef4444',
      'Sponsored Content': '#8b5cf6',
      'Display': '#06b6d4',
      'Banner': '#84cc16',
      'Video': '#ec4899',
      'Audio': '#f97316',
      'Interactive': '#6366f1',
      'Rich Media': '#14b8a6',
      'Overlay': '#a855f7',
      'Popup': '#f43f5e',
      'Interstitial': '#0ea5e9',
      'Rewarded': '#eab308',
      'Social': '#f59e0b',
      'Search': '#6b7280',
      'Shopping': '#059669',
      'Connected TV': '#7c3aed',
      'Mobile': '#3b82f6',
      'Desktop': '#374151',
      'Tablet': '#6b7280'
    };
    return formatColors[format] || '#6b7280';
  }

  /*
  // Method to get current context data
  private getContextData(): any {
    const context: any = {};
    
    // Add current publisher if selected
    if (this.currentPublisher) {
      context.currentPublisher = {
        name: this.currentPublisher.name,
        tier: this.currentPublisher.tier,
        cpmFloor: this.currentPublisher.cpmFloor,
        brandSafety: this.currentPublisher.brandSafety,
        viewability: this.currentPublisher.viewability,
        availableInventory: this.currentPublisher.availableInventory,
        demandPressure: this.currentPublisher.demandPressure,
        fillRateForecast: this.currentPublisher.fillRateForecast,
        yieldOptimization: this.currentPublisher.yieldOptimization,
        revenuePrediction: this.currentPublisher.revenuePrediction
      };
    }
    
    // Add current campaign if selected
    if (this.currentCampaign) {
      context.currentCampaign = this.currentCampaign;
    }
    
    // Add tab configuration context
    if (this.tabConfig) {
      context.tabContext = {
        title: this.tabConfig.title,
        description: this.tabConfig.description,
        selectedAgent: this.selectedAgent
      };
    }
    
    // Add any additional context data from tab config
    if (this.tabConfig?.contextData) {
      context.additionalData = this.tabConfig.contextData;
    }
    
    return context;
  }*/

  // Method to format context data as JSON for the agent
  private formatContextData(contextData: any): string {
    if (!contextData || Object.keys(contextData).length === 0) {
      return '';
    }

    return `Context for original prompt: ${JSON.stringify(contextData, null, 2)}`;
  }

  // Helper methods for RTB bidding metrics
  getTrendIcon(trend: string): string {
    switch (trend) {
      case 'up': return 'trending_up';
      case 'down': return 'trending_down';
      default: return 'trending_flat';
    }
  }

  getTrendColor(trend: string): string {
    switch (trend) {
      case 'up': return '#28a745';
      case 'down': return '#dc3545';
      default: return '#6c757d';
    }
  }

  getInventoryShare(publisher: Publisher): number {
    const totalInventory = this.contextData.publishers.reduce((sum, p) => sum + p.availableInventory, 0);
    return Math.round((publisher.availableInventory / totalInventory) * 100);
  }

  getPortfolioRevenue(publishers: Publisher[]): number {
    return publishers.reduce((sum, p) => sum + p.revenuePrediction, 0);
  }

  getFillRate(publishers: Publisher[]): number {
    const totalInventory = publishers.reduce((sum, p) => sum + p.availableInventory, 0);
    const totalFillRate = publishers.reduce((sum, p) => sum + (p.availableInventory * p.fillRateForecast), 0);
    return Math.round(totalFillRate / totalInventory);
  }

  // Dynamic card stacking hover effects (COMMENTED OUT - causing layout issues)
  private adjustPositions(): void {
    // Commented out to fix layout issues - cards now use simple normal flow layout
    // if (!this.visualizationCards) return;

    // const cards = this.visualizationCards.toArray();

    // cards.forEach((cardRef, index) => {
    //   const cardElement = cardRef.nativeElement;

    //   // Add hover event listeners
    //   cardElement.addEventListener('mouseenter', () => this.onCardHover(index, cards));
    //   cardElement.addEventListener('mouseleave', () => this.onCardLeave(cards));
    // });

  }

  // Commented out animated hover methods to fix layout issues
  private removeHoverListeners(): void {
    // Commented out - using simple layout now
    // if (!this.visualizationCards) return;

    // const cards = this.visualizationCards.toArray();
    // cards.forEach((cardRef, index) => {
    //   const cardElement = cardRef.nativeElement;

    //   // Remove event listeners
    //   cardElement.removeEventListener('mouseenter', () => this.onCardHover(index, cards));
    //   cardElement.removeEventListener('mouseleave', () => this.onCardLeave(cards));
    // });
  }

  private onCardHover(_hoveredIndex: number, _cards: ElementRef[]): void {
    // Commented out - causes layout issues

    // let cumulativeHeight = 0;

    // // Calculate positions based on actual card heights
    // cards.forEach((cardRef, index) => {
    //   const cardElement = cardRef.nativeElement;

    //   if (index <= hoveredIndex) {
    //     // Cards above and including hovered card
    //     if (index === hoveredIndex) {
    //       cardElement.style.top = `${cumulativeHeight}px`;
    //       cardElement.style.zIndex = '100'; // Bring hovered card to front
    //     } else {
    //       cardElement.style.top = `${cumulativeHeight}px`;
    //     }

    //     if (index < hoveredIndex) {
    //       cumulativeHeight += cardElement.offsetHeight + 8; // Reduced gap to 8px
    //     }
    //   } else {
    //     // Cards below hovered card - stack them after the hovered card
    //     if (index === hoveredIndex && hoveredIndex === 0) {

    //     cardElement.style.top = `80px`;
    //     }
    //     //cardElement.style.zIndex = '1';
    //     if (index === hoveredIndex + 1) {
    //       cumulativeHeight += cardElement.offsetHeight + 8; // Reduced gap to 8px
    //     }
    //     cardElement.style.top = `${cumulativeHeight}px`;
    //     cardElement.style.zIndex = '1';

    //     if (index > hoveredIndex) {
    //       cumulativeHeight += cardElement.offsetHeight + 8; // Reduced gap to 8px
    //     }
    //   }
    // });
  }

  private onCardLeave(_cards: ElementRef[]): void {
    // Commented out - causes layout issues

    // // Reset to original stacked positions
    // cards.forEach((cardRef, index) => {
    //   const cardElement = cardRef.nativeElement;

    //   // Reset to original positions
    //   switch (index) {
    //     case 0:
    //       cardElement.style.top = '80px';
    //       cardElement.style.zIndex = '1';
    //       break;
    //     case 1:
    //       cardElement.style.top = '160px';
    //       cardElement.style.zIndex = '2';
    //       break;
    //     case 2:
    //       cardElement.style.top = '260px';
    //       cardElement.style.zIndex = '3';
    //       break;
    //     case 3:
    //       cardElement.style.top = '360px';
    //       cardElement.style.zIndex = '4';
    //       break;
    //     case 4:
    //       cardElement.style.top = '460px';
    //       cardElement.style.zIndex = '5';
    //       break;
    //     default:
    //       cardElement.style.top = `${160 + (index * 100)}px`;
    //       cardElement.style.zIndex = `${index + 1}`;
    //   }
    // });
  }


  /**
   * Clear first message tracking (useful when switching contexts or resetting conversation)
   */
  clearFirstMessageTracking(): void {
    this.agentFirstMessages.clear();
  }
/**

   * Toggle sessions panel
   */
  toggleSessionsPanel(): void {
    this.showSessionsPanel = !this.showSessionsPanel;
    if (this.showSessionsPanel) {
      // Close other panels
      this.showScenariosPanel = false;
      this.showAgentSelector = false;
      this.showPreferencesSelector = false;
    }
  }

  /**
   * Check for existing session on tab load and prompt user
   */
  private async checkForExistingSession(): Promise<void> {
    // Only check once per tab load - use centralized tracking in session manager
    if (this.hasCheckedSession) return;
    
    // Check if this tab has already been initialized this page load (prevents duplicate calls across component re-renders)
    if (this.sessionManager.hasTabBeenInitialized(this.tabId)) {
      console.log(`⚠️ Tab ${this.tabId} already initialized this page load, skipping session check`);
      this.hasCheckedSession = true;
      return;
    }
    
    this.hasCheckedSession = true;
    this.sessionManager.markTabAsInitialized(this.tabId);

    try {
      const loginId = this.currentUser?.signInDetails?.loginId;
      const customerName = this.demoTrackingService.getCurrentCustomer() || undefined;
      
      // Get existing sessions for this tab
      const sessions = this.sessionManager.getTabSessions(loginId, customerName, this.tabId);
      
      if (sessions.length > 0) {
        // Found existing session(s) - get the most recent one
        const mostRecentSession = sessions[0];
        
        // Check if session has any activity (more than just creation)
        if (mostRecentSession.messageCount && mostRecentSession.messageCount > 0) {
          this.currentSessionInfo = mostRecentSession;
          
          // Load session summary from memory
          await this.loadSessionSummary(mostRecentSession.sessionId);
          
          // Show the prompt modal
          this.showSessionPrompt = true;
          return; // Wait for user decision before creating session
        }
      }
      
      // No existing session with messages - create a new one
      if (this.chatInterface) {
        this.chatInterface.createNewSession();
      }
    } catch (error) {
      console.error('Error checking for existing session:', error);
      // On error, create a new session to ensure the chat works
      if (this.chatInterface) {
        this.chatInterface.createNewSession();
      }
    }
  }

  /**
   * Load session summary from AgentCore memory
   */
  private async loadSessionSummary(sessionId: string): Promise<void> {
    this.isLoadingSessionSummary = true;
    this.sessionSummary = null;

    try {
      const memoryResult = await this.bedrockService.retrieveSessionMemory(
        sessionId,
        'summarize the conversation topics and key points'
      );

      this.sessionSummary = {
        sessionId: sessionId,
        startTime: this.currentSessionInfo?.createdAt || new Date(),
        lastActivity: this.currentSessionInfo?.lastUsed || new Date(),
        messageCount: memoryResult.messageCount || this.currentSessionInfo?.messageCount || 0,
        summary: memoryResult.summary,
        topics: memoryResult.topics,
        isLoading: false
      };
    } catch (error) {
      console.error('Error loading session summary:', error);
      this.sessionSummary = {
        sessionId: sessionId,
        startTime: this.currentSessionInfo?.createdAt || new Date(),
        lastActivity: this.currentSessionInfo?.lastUsed || new Date(),
        messageCount: this.currentSessionInfo?.messageCount || 0,
        summary: '',
        topics: [],
        isLoading: false,
        error: 'Unable to load session summary'
      };
    } finally {
      this.isLoadingSessionSummary = false;
    }
  }

  /**
   * Handle user choosing to continue the existing session
   */
  onContinueSession(session: SessionInfo): void {
    // Switch to the existing session
    const loginId = this.currentUser?.signInDetails?.loginId;
    const customerName = this.demoTrackingService.getCurrentCustomer() || undefined;
    
    this.sessionManager.switchSession(session.sessionId, loginId, customerName, this.tabId);
    this.showSessionPrompt = false;
    
    // Notify chat interface to load the session
    if (this.chatInterface) {
      this.chatInterface.switchToSession(session.sessionId);
    }
  }

  /**
   * Handle user choosing to start a new session
   */
  onStartNewSession(): void {
    this.showSessionPrompt = false;
    
    // Clear first message tracking for fresh context injection
    this.clearFirstMessageTracking();
    
    // Only call createNewSession on chat interface - it handles session manager internally
    // This prevents duplicate session creation
    if (this.chatInterface) {
      this.chatInterface.createNewSession();
    }
  }

  /**
   * Handle session prompt modal closed
   */
  onSessionPromptClosed(): void {
    this.showSessionPrompt = false;
  }
}