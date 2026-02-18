import { Component, OnInit, OnDestroy } from '@angular/core';
import { Router } from '@angular/router';
import { AwsConfigService } from './services/aws-config.service';
import { TourService } from './services/tour.service';
import { BedrockService } from './services/bedrock.service';
import { HttpClient } from '@angular/common/http';
import { environment } from '../environments/environment';
import { AgentConfigService } from './services/agent-config.service';
import { DemoTrackingService } from './services/demo-tracking.service';
import { EnrichedAgent } from './models/application-models';

interface TabConfig {
  id: string;
  title: string;
  description: string;
  icon: string;
  config: any;
  messages?: Array<any>;
}

interface VersionConfig {
  id: string;
  name: string;
  timestamp: string;
  description: string;
  s3Key?: string;
}

@Component({
  selector: 'app-root',
  templateUrl: './app.component.html',
  styleUrls: ['./app.component.scss']
})
export class AppComponent implements OnInit, OnDestroy {
  title = 'Agents for ' + environment.industryType;
  activeTab = 'generic-tab-1'; // Start with first tab from config
  isAuthenticated = false;
  currentUser: any = null;

  tabs: TabConfig[] = [];
  isLoadingTabs = true;

  // Quick Setup properties
  showQuickSetup = false;
  showGenerationPrompt = false;
  showAgentConfig = false;
  showScenarioEditor = false;

  // Demo Modal properties
  showDemoModal = false;
  isGenerating = false;
  configTabs: any[] = [];
  selectedTabId = '';
  selectedTab: any = null;
  generationPrompt = '';
  editingScenario: any = {};
  editingScenarioIndex = -1;
  originalTabConfigurations: any = null;
  editingAgentAtIndex = -1;

  // Version management properties
  availableVersions: VersionConfig[] = [];
  selectedVersionId = 'current';
  isLoadingVersions = false;

  // JSON editing properties
  isJsonEditMode = false;
  configJsonString = '';
  jsonValidationError = '';

  // Expandable menu properties
  isMenuExpanded = false;
  private menuHoverTimeout: any = null;
  private menuCollapseTimeout: any = null;

  // Context panel properties
  currentPublisher: any = null;
  contextData: any = null;
  publishers: any[] = [];
  contextDetailsPosition: { bottom: string; right: string; } = { bottom: '110px', right: '47px' };
  currentTab: any;

  constructor(
    private awsConfig: AwsConfigService,
    private router: Router,
    private tourService: TourService,
    private bedrockService: BedrockService,
    private agentConfigService: AgentConfigService,
    private demoTrackingService: DemoTrackingService,
    private http: HttpClient
  ) {
    // Make AwsConfigService available for debugging in browser console
    (window as any).awsConfigService = this.awsConfig;
  }
  getIndustry() {
    return environment.industryType;
  }
  
  onAgentSelected(): void {
    const selectedAgent = this.agentConfigService.getAgent(this.editingScenario.agentType);
    if (selectedAgent) {
      this.editingScenario.category = selectedAgent.teamName || '';
    }
  }


  ngOnInit() {
    // Apply light theme to body
    document.body.classList.add('light-theme');

    // Subscribe to authentication state
    this.awsConfig.user$.subscribe((user) => {
      this.currentUser = user;
      this.isAuthenticated = !!user;

      if (!user) {
        this.router.navigate(['/login']);
        // Clear tabs when not authenticated
        this.tabs = [];
        this.isLoadingTabs = false;
      } else {
        // Load tab configurations after authentication
        this.loadTabConfigurations().then((tabs) => {
          tabs.forEach((tab)=>
          {
            tab.config.scenarios.forEach((scenario: any) => {
              scenario.agentObject = this.agentConfigService.getAgentByAgentNameAndTeam(scenario.agentType, scenario.category || null)
              scenario.agent = scenario.agentObject;
              if(scenario.agentObject) {
                scenario.category = scenario.agentObject.teamName || scenario.category;
              }
            });
          });
          this.tabs = tabs;
          // Set the first tab as active if we don't have a valid active tab
          if (this.tabs.length > 0 && !this.tabs.find(tab => tab.id === this.activeTab)) {
            this.activeTab = this.tabs[0].id;
            this.currentTab = this.tabs[0];
          }
          else if (this.activeTab)
            this.currentTab = this.tabs.find(tab => tab.id === this.activeTab);

          if (this.currentTab) {
            this.contextData = this.currentTab.contextData;
          }

          // Check if we should show demo modal after authentication
          this.checkDemoModal();
        });
      }
    });


  }

  private async loadTabConfigurations(): Promise<any> {
    try {
      this.isLoadingTabs = true;

      // Check if user is authenticated before trying to load from AppConfig
      if (!this.isAuthenticated) {
        console.warn('⚠️ User not authenticated, cannot load tab configurations from AppConfig');
        this.tabs = [];
        return;
      }

      // Get tab configuration from AppConfig via AgentConfigService
      const configData = await this.agentConfigService.getTabsConfiguration();

      // Extract tab metadata from configuration
      this.tabs = Object.values(configData.tabConfigurations).map((config: any) => ({
        id: config.id,
        title: config.title,
        description: config.description,
        icon: config.icon,
        config: config,
        messages: []
      }));

      return this.tabs;

    } catch (error) {
      console.error('❌ Error loading tab configurations from AppConfig:', error);

      // Try to fallback to local static configuration if AppConfig fails
      try {
        const fallbackConfig = await this.loadFallbackTabConfiguration();
        if (fallbackConfig && fallbackConfig.tabConfigurations) {
          this.tabs = Object.values(fallbackConfig.tabConfigurations).map((config: any) => ({
            id: config.id,
            title: config.title,
            description: config.description,
            icon: config.icon,
            config: config,
            messages: []
          }));

          if (this.tabs.length > 0 && !this.tabs.find(tab => tab.id === this.activeTab)) {
            this.activeTab = this.tabs[0].id;
            this.currentTab = this.tabs[0];
          }
          return this.tabs;

          //console.log(`✅ Loaded ${this.tabs.length} tabs from fallback configuration`);
        } else {
          throw new Error('Fallback configuration is empty or invalid');
        }
      } catch (fallbackError) {
        console.error('❌ Fallback configuration also failed:', fallbackError);
        // Final fallback to a basic tab if everything fails
        this.tabs = [{
          id: 'generic-tab-1',
          title: 'Default Tab',
          description: 'Configuration loading failed - using default tab',
          icon: 'error',
          config: {},
          messages: []
        }];
        this.activeTab = 'generic-tab-1';
        //console.log('⚠️ Using default fallback tab');
      }
    } finally {
      this.isLoadingTabs = false;
      setTimeout(() => {
        let chatInput = document.getElementsByClassName("chat-input");
        //console.log(chatInput)
        if (chatInput && chatInput.length > 0) {
          //console.log('chat input found')
          let position = chatInput.item(0)?.getBoundingClientRect();
          //console.log(position)

          this.contextDetailsPosition = { bottom: '0', right: '0' };
          if (position) {
            this.contextDetailsPosition.bottom = (position.height + 10) + "px !important";
            this.contextDetailsPosition.right = "20px"

          }


        }
      }, 1000);
    }
  }

  private async loadFallbackTabConfiguration(): Promise<any> {
    try {
      // Try to load from local assets as fallback
      const response = await this.http.get('/assets/tab-configurations.json').toPromise();
      return response;
    } catch (error) {
      console.error('❌ Could not load fallback tab configuration:', error);
      return null;
    }
  }

  selectTab(tabId: string): void {
    this.activeTab = tabId;
    this.currentTab = this.tabs.find((t) => t.id == tabId);
  }

  isActiveTab(tabId: string): boolean {
    return this.activeTab === tabId;
  }

  async signOut(): Promise<void> {
    try {
      await this.awsConfig.signOut();
      this.router.navigate(['/login']);
    } catch (error) {
      console.error('Sign out error:', error);
    }
  }

  startTour(): void {
    this.tourService.startInterfaceTour();
  }

  stopTour(): void {
    this.tourService.stopTour();
  }

  clearDemoStorage(): void {
    const skipConfirm = true;
    if (skipConfirm || confirm('Are you sure you want to clear all demo data from local storage? This will reset all demo tracking and preferences.')) {
      try {
        // Clear demo-related localStorage items
        const keysToRemove: Array<string> = [];
        for (let i = 0; i < localStorage.length; i++) {
          let key = localStorage.key(i)?.toString();
          if (key && (key.startsWith('demo_') || key.startsWith('tour_') || key.includes('demo') || key.includes('tracking'))) {
            keysToRemove.push(key);
          }
        }

        keysToRemove.forEach(key => localStorage.removeItem(key));

        // Also clear demo tracking service data
        this.demoTrackingService.clearAllData();

        alert(`Demo storage cleared successfully! Removed ${keysToRemove.length} items from local storage.`);
        this.checkDemoModal();

      } catch (error) {
        console.error('Error clearing demo storage:', error);
        alert('Error clearing demo storage. Please try again.');
      }
    }
  }

  // Quick Setup Methods
  async openQuickSetup(): Promise<void> {
    this.showQuickSetup = true;
    await this.loadConfigurationForEditing();
    await this.loadAvailableVersions();
  }

  closeQuickSetup(): void {
    this.showQuickSetup = false;
    this.showGenerationPrompt = false;
    this.showScenarioEditor = false;
    this.isJsonEditMode = false;
    this.configJsonString = '';
    this.jsonValidationError = '';
    this.resetEditingState();
  }

  // Agent Config Methods
  openAgentConfig(): void {
    this.showAgentConfig = true;
  }

  closeAgentConfig(): void {
    this.showAgentConfig = false;
  }

  // Agent Management Modal Methods
  // Validates: Requirements 1.1, 1.2, 1.3, 1.4
  showAgentManagement = false;

  openAgentManagement(): void {
    this.showAgentManagement = true;
  }

  closeAgentManagement(): void {
    this.showAgentManagement = false;
  }

  // Demo Modal Methods
  private checkDemoModal(): void {
    // Only show demo modal if user is authenticated
    if (this.isAuthenticated && this.currentUser && this.demoTrackingService.shouldShowDemoModal()) {
      this.showDemoModal = true;
    }
  }

  closeDemoModal(): void {
    this.showDemoModal = false;
  }

  private async loadConfigurationForEditing(): Promise<void> {
    try {
      // Check if user is authenticated before trying to load from AppConfig
      if (!this.isAuthenticated) {
        console.warn('⚠️ User not authenticated, cannot load configuration for editing');
        return;
      }

      // Get tab configuration from AppConfig via AgentConfigService
      const configData = await this.agentConfigService.getTabsConfiguration();
      this.originalTabConfigurations = configData;
      this.configTabs = Object.values(configData.tabConfigurations);

      if (this.configTabs.length > 0) {
        this.selectedTabId = this.configTabs[0].id;
        this.selectedTab = { ...this.configTabs[0] };
      }

      //console.log('✅ Configuration loaded for editing');
    } catch (error) {
      console.error('❌ Error loading configuration for editing:', error);

      // Try fallback for editing as well
      try {
        const fallbackConfig = await this.loadFallbackTabConfiguration();
        if (fallbackConfig && fallbackConfig.tabConfigurations) {
          this.originalTabConfigurations = fallbackConfig;
          this.configTabs = Object.values(fallbackConfig.tabConfigurations);

          if (this.configTabs.length > 0) {
            this.selectedTabId = this.configTabs[0].id;
            this.selectedTab = { ...this.configTabs[0] };
          }

          //console.log('✅ Fallback configuration loaded for editing');
        }
      } catch (fallbackError) {
        console.error('❌ Fallback configuration for editing also failed:', fallbackError);
      }
    }
  }

  onTabSelectionChange(): void {
    const selected = this.configTabs.find(tab => tab.id === this.selectedTabId);
    if (selected) {
      this.selectedTab = { ...selected };
    }
  }

  addNewTab(): void {
    const newTabId = `generic-tab-${Date.now()}`;
    const newTab = {
      id: newTabId,
      title: 'New Tab',
      description: 'New tab description',
      icon: 'tab',
      defaultAgent: '',
      availableAgents: this.agentConfigService.getActiveAgents(),
      scenarios: [],
      availableCampaigns: [],
      visualizations: [],
      contextData: {
      }
    };

    this.configTabs.push(newTab);
    this.selectedTabId = newTabId;
    this.selectedTab = { ...newTab };
  }

  deleteTab(): void {
    if (!this.selectedTab || this.configTabs.length <= 1) {
      alert('Cannot delete the last remaining tab. At least one tab must exist.');
      return;
    }

    const tabTitle = this.selectedTab.title;
    if (!confirm(`Are you sure you want to delete the tab "${tabTitle}"? This action cannot be undone.`)) {
      return;
    }

    // Find the index of the tab to delete
    const tabIndex = this.configTabs.findIndex(tab => tab.id === this.selectedTab.id);
    if (tabIndex === -1) {
      console.error('Tab not found for deletion');
      return;
    }

    // Remove the tab from the array
    this.configTabs.splice(tabIndex, 1);

    // Select a different tab (prefer the previous one, or the first one if we deleted the first)
    const newSelectedIndex = tabIndex > 0 ? tabIndex - 1 : 0;
    this.selectedTabId = this.configTabs[newSelectedIndex].id;
    this.selectedTab = { ...this.configTabs[newSelectedIndex] };

  }

  messagesUpdated(updatedObject: any) {
    const tab = this.tabs.find(t => t.id == updatedObject.tabId);
    if (tab) {
      tab.messages = updatedObject.messages;
    }
  }

  generateScenarios(): void {
    if (!this.selectedTab) return;

    this.generationPrompt = `Please generate several scenarios that illustrate how a persona working with ${this.selectedTab.title}, specifically with ${this.selectedTab.description}, would ask questions from an AI agent to solve business problems.`;
    this.showGenerationPrompt = true;
  }

  async submitGeneration(): Promise<void> {
    if (!this.selectedTab || !this.generationPrompt.trim()) return;

    this.isGenerating = true;

    try {
      const fullPrompt = `${this.generationPrompt}

Please return ONLY a JSON array of scenarios in this exact format:
[
  {
    "title": "Scenario Title",
    "description": "Brief description of the scenario",
    "query": "The actual question a user would ask the AI agent in the natural form of a question or an ask.",
    "category": "Category Name",
    "applicableCampaigns": ["camp-12345", "camp-67890"],
    "agentType": "<choose agentType value from most applicable agent from below list>"
  }
]

<all agents list>
${JSON.stringify(this.getAllAgents())}
</all agents list>
Make sure each scenario has realistic business problems and detailed queries that would help solve them.`;

      // Use the Bedrock service to generate scenarios with Nova Pro
      const response = await this.bedrockService.invokeLLMForScenarios(fullPrompt);

      // Parse the response to extract JSON
      const jsonMatch = response.match(/\[[\s\S]*\]/);
      if (jsonMatch) {
        const generatedScenarios = JSON.parse(jsonMatch[0]);

        // Add generated scenarios to the selected tab
        if (!this.selectedTab.scenarios) {
          this.selectedTab.scenarios = [];
        }

        this.selectedTab.scenarios.push(...generatedScenarios);

        // Update the configTabs array
        const tabIndex = this.configTabs.findIndex(tab => tab.id === this.selectedTab.id);
        if (tabIndex >= 0) {
          this.configTabs[tabIndex] = { ...this.selectedTab };
        }

      } else {
        console.error('Could not parse generated scenarios from response');
      }

    } catch (error) {
      console.error('Error generating scenarios:', error);
    } finally {
      this.isGenerating = false;
      this.showGenerationPrompt = false;
    }
  }

  getAllAgents(): Array<EnrichedAgent> {
    return this.agentConfigService.getActiveAgents()
  }

  cancelGeneration(): void {
    this.showGenerationPrompt = false;
    this.isGenerating = false;
  }

  addManualScenario(): void {
    this.editingScenario = {
      title: '',
      description: '',
      query: '',
      category: '',
      applicableContexts: [],
      agentType: ''
    };
    this.editingScenarioIndex = -1;
    this.showScenarioEditor = true;
  }

  editScenario(index: number): void {
    if (!this.selectedTab || !this.selectedTab.scenarios) return;

    this.editingScenario = { ...this.selectedTab.scenarios[index] };
    this.editingScenarioIndex = index;
    this.showScenarioEditor = true;
  }

  deleteScenario(index: number): void {
    if (!this.selectedTab || !this.selectedTab.scenarios) return;

    if (confirm('Are you sure you want to delete this scenario?')) {
      this.selectedTab.scenarios.splice(index, 1);

      // Update the configTabs array
      const tabIndex = this.configTabs.findIndex(tab => tab.id === this.selectedTab.id);
      if (tabIndex >= 0) {
        this.configTabs[tabIndex] = { ...this.selectedTab };
      }
    }
  }

  saveScenario(): void {
    if (!this.selectedTab) return;

    if (!this.selectedTab.scenarios) {
      this.selectedTab.scenarios = [];
    }

    if (this.editingScenarioIndex >= 0) {
      // Edit existing scenario
      this.selectedTab.scenarios[this.editingScenarioIndex] = { ...this.editingScenario };
    } else {
      // Add new scenario
      this.selectedTab.scenarios.push({ ...this.editingScenario });
    }

    // Update the configTabs array
    const tabIndex = this.configTabs.findIndex(tab => tab.id === this.selectedTab.id);
    if (tabIndex >= 0) {
      this.configTabs[tabIndex] = { ...this.selectedTab };
    }

    this.closeScenarioEditor();
  }

  closeScenarioEditor(): void {
    this.showScenarioEditor = false;
    this.resetEditingState();
  }

  private resetEditingState(): void {
    this.editingScenario = {};
    this.editingScenarioIndex = -1;
  }

  // JSON Editing Methods
  toggleJsonEditMode(): void {
    if (!this.isJsonEditMode) {
      // Switching to JSON mode - convert current config to JSON string
      try {
        const fullConfig = {
          tabConfigurations: {}
        };

        this.configTabs.forEach(tab => {
          fullConfig.tabConfigurations[tab.id] = tab;
        });

        this.configJsonString = JSON.stringify(fullConfig, null, 2);
        this.jsonValidationError = '';
        this.isJsonEditMode = true;
      } catch (error) {
        console.error('Error converting config to JSON:', error);
        alert('Error converting configuration to JSON format.');
      }
    } else {
      // Switching back to form mode - validate and parse JSON
      this.applyJsonChanges();
    }
  }

  applyJsonChanges(): void {
    try {
      // Validate JSON syntax
      const parsedConfig = JSON.parse(this.configJsonString);

      // Validate structure
      if (!parsedConfig.tabConfigurations || typeof parsedConfig.tabConfigurations !== 'object') {
        throw new Error('Invalid configuration structure. Expected "tabConfigurations" object.');
      }

      // Convert back to configTabs array
      this.configTabs = Object.values(parsedConfig.tabConfigurations);

      // Update selected tab if it still exists
      const selectedExists = this.configTabs.find(tab => tab.id === this.selectedTabId);
      if (!selectedExists && this.configTabs.length > 0) {
        this.selectedTabId = this.configTabs[0].id;
        this.selectedTab = { ...this.configTabs[0] };
      } else if (selectedExists) {
        this.selectedTab = { ...selectedExists };
      }

      // Clear validation error and switch back to form mode
      this.jsonValidationError = '';
      this.isJsonEditMode = false;

    } catch (error) {
      // Show validation error but stay in JSON mode
      this.jsonValidationError = error instanceof Error ? error.message : 'Invalid JSON format';
      console.error('JSON validation error:', error);
    }
  }

  cancelJsonChanges(): void {
    // Discard JSON changes and switch back to form mode
    this.isJsonEditMode = false;
    this.configJsonString = '';
    this.jsonValidationError = '';
  }

  formatJson(): void {
    try {
      const parsed = JSON.parse(this.configJsonString);
      this.configJsonString = JSON.stringify(parsed, null, 2);
      this.jsonValidationError = '';
    } catch (error) {
      this.jsonValidationError = 'Cannot format invalid JSON';
    }
  }

  // Version Management Methods
  private async loadAvailableVersions(): Promise<void> {
    try {
      this.isLoadingVersions = true;

      // Try to load versions list from S3
      const awsConfig = this.awsConfig.getConfig();
      if (awsConfig?.ui?.bucketName) {
        await this.loadVersionsFromS3(awsConfig);
        //console.log(this.availableVersions);
      } else {
        // Fallback to local storage or default
        this.availableVersions = [
          {
            id: 'current',
            name: 'Current Version',
            timestamp: new Date().toLocaleTimeString(),
            description: 'Currently active configuration'
          }
        ];
      }
    } catch (error) {
      console.error('Error loading versions:', error);
      this.availableVersions = [
        {
          id: 'current',
          name: 'Current Version',
          timestamp: new Date().toISOString(),
          description: 'Currently active configuration'
        }
      ];
    } finally {
      this.isLoadingVersions = false;
    }
  }

  private async loadVersionsFromS3(awsConfig: any): Promise<void> {
    try {
      // Import AWS SDK dynamically
      const { S3Client, ListObjectsV2Command } = await import('@aws-sdk/client-s3');

      // Get AWS credentials
      const credentials = await this.awsConfig.getAwsConfig();
      if (!credentials?.credentials) {
        throw new Error('AWS credentials not available');
      }

      // Initialize S3 client
      const s3Client = new S3Client({
        region: awsConfig.aws.region,
        credentials: credentials.credentials
      });

      // List version files in the versions folder
      const listCommand = new ListObjectsV2Command({
        Bucket: awsConfig.creativesBucket,
        Prefix: 'config-versions/tab-configurations-',
        MaxKeys: 50
      });

      const response = await s3Client.send(listCommand);

      // Parse version files
      const versions: VersionConfig[] = [];

      // Add current version first
      versions.push({
        id: 'current',
        name: 'Current Version',
        timestamp: new Date().toISOString(),
        description: 'Currently active configuration'
      });

      if (response.Contents) {
        for (const object of response.Contents) {
          if (object.Key && object.Key.endsWith('.json')) {
            //("version: " + object.Key)
            // Extract version info from filename
            // Format: config-versions/tab-configurations-YYYY-MM-DDTHH-MM-SS-sssZ.json
            const filename = object.Key.split('/').pop();
            const timestampMatch = filename?.match(/tab-configurations-(.+)\.json$/);

            if (timestampMatch) {
              // Convert YYYY-MM-DDTHH-MM-SS-sssZ to YYYY-MM-DDTHH:MM:SS.sssZ
              let timestamp = timestampMatch[1];

              // Replace the first two hyphens after T with colons (for time part)
              // Format: YYYY-MM-DDTHH-MM-SS-sssZ -> YYYY-MM-DDTHH:MM:SS.sssZ
              const parts = timestamp.split('T');
              if (parts.length === 2) {
                const datePart = parts[0]; // YYYY-MM-DD
                const timePart = parts[1]; // HH-MM-SS-sssZ

                // Convert HH-MM-SS-sssZ to HH:MM:SS.sssZ
                const timeFormatted = timePart.replace(/^(\d{2})-(\d{2})-(\d{2})-(\d{3})Z$/, '$1:$2:$3.$4Z');
                timestamp = `${datePart}T${timeFormatted}`;
              }

              //console.log('Parsed timestamp:', timestamp);
              const versionDate = new Date(timestamp);
              //console.log('Version date:', versionDate);
              versions.push({
                id: object.Key,
                name: `Version ${versionDate.toLocaleDateString()} ${versionDate.toLocaleTimeString()}`,
                timestamp: timestamp,
                description: `Saved on ${versionDate.toLocaleDateString()} at ${versionDate.toLocaleTimeString()}`,
                s3Key: object.Key
              });
            }
          }
        }
      }

      // Sort versions by timestamp (newest first, but keep current at top)
      const currentVersion = versions.find(v => v.id === 'current');
      const otherVersions = versions.filter(v => v.id !== 'current')
        .sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());

      this.availableVersions = currentVersion ? [currentVersion, ...otherVersions] : otherVersions;

    } catch (error) {
      console.error('Error loading versions from S3:', error);
      // Fallback to current version only
      this.availableVersions = [
        {
          id: 'current',
          name: 'Current Version',
          timestamp: new Date().toISOString(),
          description: 'Currently active configuration'
        }
      ];
    }
  }

  async onVersionSelectionChange(): Promise<void> {
    if (this.selectedVersionId === 'current') {
      // Load current configuration
      await this.loadConfigurationForEditing();
    } else {
      // Load selected version
      await this.loadVersionConfiguration(this.selectedVersionId);
    }
  }

  private async loadVersionConfiguration(versionKey: string): Promise<void> {
    try {
      const awsConfig = this.awsConfig.getConfig();
      if (!awsConfig?.ui?.bucketName) {
        console.error('Cannot load version: UI bucket not configured');
        return;
      }

      // Import AWS SDK dynamically
      const { S3Client, GetObjectCommand } = await import('@aws-sdk/client-s3');

      // Get AWS credentials
      const credentials = await this.awsConfig.getAwsConfig();
      if (!credentials?.credentials) {
        throw new Error('AWS credentials not available');
      }

      // Initialize S3 client
      const s3Client = new S3Client({
        region: awsConfig.aws.region,
        credentials: credentials.credentials
      });

      // Get the version file
      const getCommand = new GetObjectCommand({
        Bucket: awsConfig.creativesBucket,
        Key: versionKey
      });

      const response = await s3Client.send(getCommand);

      if (response.Body) {
        const configText = await response.Body.transformToString();
        const configData = JSON.parse(configText);

        this.originalTabConfigurations = configData;
        this.configTabs = Object.values(configData.tabConfigurations);

        if (this.configTabs.length > 0) {
          this.selectedTabId = this.configTabs[0].id;
          this.selectedTab = { ...this.configTabs[0] };
        }

      }

    } catch (error) {
      console.error('Error loading version configuration:', error);
      alert('Failed to load selected version. Please try again.');
    }
  }

  private async cleanupOldVersions(s3Client: any, bucketName: string): Promise<void> {
    try {
      const { ListObjectsV2Command, DeleteObjectCommand } = await import('@aws-sdk/client-s3');

      // List all version files
      const listCommand = new ListObjectsV2Command({
        Bucket: bucketName,
        Prefix: 'config-versions/tab-configurations-',
        MaxKeys: 100
      });

      const response = await s3Client.send(listCommand);

      if (response.Contents && response.Contents.length > 10) {
        // Sort by last modified date (oldest first)
        const sortedVersions = response.Contents
          .filter(obj => obj.Key && obj.Key.endsWith('.json'))
          .sort((a, b) => (a.LastModified?.getTime() || 0) - (b.LastModified?.getTime() || 0));

        // Delete oldest versions, keeping the last 10
        const versionsToDelete = sortedVersions.slice(0, sortedVersions.length - 10);

        for (const version of versionsToDelete) {
          if (version.Key) {
            const deleteCommand = new DeleteObjectCommand({
              Bucket: bucketName,
              Key: version.Key
            });

            await s3Client.send(deleteCommand);
          }
        }

      }

    } catch (error) {
      console.error('Error cleaning up old versions:', error);
      // Don't fail the save operation if cleanup fails
    }
  }

  async deleteVersion(versionId: string): Promise<void> {
    if (versionId === 'current') {
      alert('Cannot delete the current version.');
      return;
    }

    const version = this.availableVersions.find(v => v.id === versionId);
    if (!version) {
      alert('Version not found.');
      return;
    }

    if (!confirm(`Are you sure you want to delete version "${version.name}"? This action cannot be undone.`)) {
      return;
    }

    try {
      const awsConfig = this.awsConfig.getConfig();
      if (!awsConfig?.ui?.bucketName) {
        alert('Cannot delete version: UI bucket not configured');
        return;
      }

      // Import AWS SDK dynamically
      const { S3Client, DeleteObjectCommand } = await import('@aws-sdk/client-s3');

      // Get AWS credentials
      const credentials = await this.awsConfig.getAwsConfig();
      if (!credentials?.credentials) {
        throw new Error('AWS credentials not available');
      }

      // Initialize S3 client
      const s3Client = new S3Client({
        region: awsConfig.aws.region,
        credentials: credentials.credentials
      });

      // Delete the version file
      const deleteCommand = new DeleteObjectCommand({
        Bucket: awsConfig.creativesBucket,
        Key: versionId
      });

      await s3Client.send(deleteCommand);

      // Refresh available versions
      await this.loadAvailableVersions();

      // Reset to current if deleted version was selected
      if (this.selectedVersionId === versionId) {
        this.selectedVersionId = 'current';
        await this.loadConfigurationForEditing();
      }

      alert(`Version "${version.name}" has been deleted successfully.`);

    } catch (error) {
      console.error('Error deleting version:', error);
      alert('Failed to delete version. Please try again.');
    }
  }

  async saveConfiguration(): Promise<void> {
    try {
      // If in JSON edit mode, apply changes first
      if (this.isJsonEditMode) {
        this.applyJsonChanges();
        // If there was a validation error, don't proceed with save
        if (this.jsonValidationError) {
          alert('Please fix JSON validation errors before saving.');
          return;
        }
      }

      // Create backup with timestamp
      const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
      const backupData = { ...this.originalTabConfigurations };

      // Convert configTabs array back to the original structure
      const updatedConfig = {
        tabConfigurations: {}
      };

      this.configTabs.forEach(tab => {
        updatedConfig.tabConfigurations[tab.id] = tab;
      });

      // Get AWS configuration for S3 and CloudFront
      const awsConfig = this.awsConfig.getConfig();
      if (!awsConfig?.creativesBucket) {
        console.warn('UI bucket not configured, falling back to local download');
        await this.saveConfigurationLocally(backupData, updatedConfig, timestamp);
        return;
      }

      // Upload to S3 and invalidate CloudFront
      await this.uploadConfigurationToS3(backupData, updatedConfig, timestamp, awsConfig);

    } catch (error) {
      console.error('Error saving configuration:', error);
      alert('Error saving configuration. Please try again.');
    }
  }

  private async saveConfigurationLocally(backupData: any, updatedConfig: any, timestamp: string): Promise<void> {
    // Create backup
    const backupBlob = new Blob([JSON.stringify(backupData, null, 2)], { type: 'application/json' });
    const backupUrl = URL.createObjectURL(backupBlob);
    const backupLink = document.createElement('a');
    backupLink.href = backupUrl;
    backupLink.download = `tab-configurations-backup-${timestamp}.json`;
    backupLink.click();
    URL.revokeObjectURL(backupUrl);

    // Save updated configuration
    const configBlob = new Blob([JSON.stringify(updatedConfig, null, 2)], { type: 'application/json' });
    const configUrl = URL.createObjectURL(configBlob);
    const configLink = document.createElement('a');
    configLink.href = configUrl;
    configLink.download = 'tab-configurations.json';
    configLink.click();
    URL.revokeObjectURL(configUrl);

    // Show success message
    alert('Configuration saved locally! The files have been downloaded to your computer. Note: With AppConfig integration, configurations are now managed dynamically. Use the Agent Configuration Modal to apply changes in real-time.');

  }

  private async uploadConfigurationToS3(backupData: any, updatedConfig: any, timestamp: string, awsConfig: any): Promise<void> {
    try {
      // Import AWS SDK dynamically
      const { S3Client, PutObjectCommand } = await import('@aws-sdk/client-s3');
      const { CloudFrontClient, CreateInvalidationCommand } = await import('@aws-sdk/client-cloudfront');

      // Get AWS credentials
      const credentials = await this.awsConfig.getAwsConfig();
      if (!credentials?.credentials) {
        throw new Error('AWS credentials not available');
      }

      // Initialize S3 client
      const s3Client = new S3Client({
        region: awsConfig.aws.region,
        credentials: credentials.credentials
      });

      // Save current version as a backup in the versions folder (protected from cleanup)
      if (this.originalTabConfigurations) {
        const versionKey = `config-versions/tab-configurations-${timestamp}.json`;
        await s3Client.send(new PutObjectCommand({
          Bucket: awsConfig.creativesBucket,
          Key: versionKey,
          Body: JSON.stringify(this.originalTabConfigurations, null, 2),
          ContentType: 'application/json',
          CacheControl: 'no-cache',
          Metadata: {
            'version-timestamp': timestamp,
            'version-type': 'tab-configuration',
            'created-by': 'quick-setup-ui'
          }
        }));

      }

      // Upload updated configuration to S3 creatives bucket
      const configKey = 'configurations/tab-configurations.json';
      await s3Client.send(new PutObjectCommand({
        Bucket: awsConfig.creativesBucket,
        Key: configKey,
        Body: JSON.stringify(updatedConfig, null, 2),
        ContentType: 'application/json',
        CacheControl: 'no-cache'
      }));

      // Invalidate CloudFront cache if distribution ID is available
      /*if (awsConfig.ui.cloudFrontDistributionId) {
        const cloudFrontClient = new CloudFrontClient({
          region: awsConfig.aws.region,
          credentials: credentials.credentials
        });

        const invalidationParams = {
          DistributionId: awsConfig.ui.cloudFrontDistributionId,
          InvalidationBatch: {
            Paths: {
              Quantity: 1,
              Items: [`/${configKey}`]
            },
            CallerReference: `config-update-${timestamp}`
          }
        };

        const invalidationResult = await cloudFrontClient.send(new CreateInvalidationCommand(invalidationParams));
      }*/

      // Clean up old versions (keep last 10)
      await this.cleanupOldVersions(s3Client, awsConfig.creativesBucket);

      // Refresh available versions
      await this.loadAvailableVersions();

      // Show success message and prompt for reload
      if (confirm('Configuration saved successfully to S3! A version backup has been created. Would you like to reload the UI to see the changes?')) {
        window.location.reload();
      }

    } catch (error) {
      console.error('Error uploading to S3:', error);

      // Fallback to local download if S3 upload fails
      await this.saveConfigurationLocally(backupData, updatedConfig, timestamp);
    }
  }

  // Expandable Menu Methods
  onMenuHover(isHovering: boolean): void {
    if (isHovering) {
      // Clear any existing collapse timeout
      if (this.menuCollapseTimeout) {
        clearTimeout(this.menuCollapseTimeout);
        this.menuCollapseTimeout = null;
      }

      // Expand menu immediately on hover
      this.expandMenu();
    } else {
      // Start collapse timer when mouse leaves
      this.startCollapseTimer();
    }
  }

  toggleMenu(): void {
    if (this.isMenuExpanded) {
      this.collapseMenu();
    } else {
      this.expandMenu();
    }
  }

  private expandMenu(): void {
    this.isMenuExpanded = true;
  }

  private collapseMenu(): void {
    this.isMenuExpanded = false;
  }

  private startCollapseTimer(): void {
    // Clear any existing timeout
    if (this.menuCollapseTimeout) {
      clearTimeout(this.menuCollapseTimeout);
    }

    // Set 10-second timer to collapse menu
    this.menuCollapseTimeout = setTimeout(() => {
      this.collapseMenu();
      this.menuCollapseTimeout = null;
    }, 3000);
  }

  getButtonTransform(index: number): string {
    if (this.isMenuExpanded) {
      return 'translateX(0)';
    } else {
      // Stack buttons to the right (off-screen)
      const offset = (5 - index) * 20; // Stagger the buttons (now 5 buttons total)
      return `translateX(${offset}px)`;
    }
  }

  getButtonOpacity(index: number): number {
    return this.isMenuExpanded ? 1 : 0;
  }

  // Context panel methods
  getCurrentTabConfig(): any {
    return this.currentTab;
  }

  onContextPanelShow(): void {
    // Handle context panel show event if needed
  }

  onContextPanelHide(): void {
    // Handle context panel hide event if needed
  }

  selectPublisher(publisher: any): void {
    this.currentPublisher = publisher;
    // Notify the active tab component about the publisher selection
    this.notifyActiveTabOfContextChange();
  }

  private notifyActiveTabOfContextChange(): void {
    // This method can be used to communicate context changes to the active tab
    // For now, we'll rely on the context panel's own state management
  }

  ngOnDestroy(): void {
    // Clean up timeouts to prevent memory leaks
    if (this.menuHoverTimeout) {
      clearTimeout(this.menuHoverTimeout);
    }
    if (this.menuCollapseTimeout) {
      clearTimeout(this.menuCollapseTimeout);
    }
  }
} 