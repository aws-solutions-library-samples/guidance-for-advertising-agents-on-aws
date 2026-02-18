import { Injectable, OnInit } from '@angular/core';
import { BehaviorSubject, combineLatest, Subject } from 'rxjs';
import { map } from 'rxjs/operators';
import { HttpClient } from '@angular/common/http';
import { AwsConfigService } from './aws-config.service';
import { AgentDynamoDBService } from './agent-dynamodb.service';
import { TextUtils } from '../utils/text-utils';
import { AgentConfig, DeployedAgent, EnrichedAgent, TabConfiguration, TabsConfiguration } from '../models/application-models';
import { SessionManagerService } from './session-manager.service';
import { userInfo } from 'os';


@Injectable({
  providedIn: 'root'
})
export class AgentConfigService implements OnInit {
  getAgentByAgentNameAndTeam(name: string, team: string | null): EnrichedAgent | null {
    let match = this.enrichedAgents$.value.find((agent: EnrichedAgent) => agent.agentType === name && agent.teamName === team);
    if (!match) {
      //try any name match
      match = this.enrichedAgents$.value.find((agent: EnrichedAgent) => agent.agentType === name);
    }
    return match || null;
  }
  private agentConfig$ = new BehaviorSubject<any | null>(null);
  private enrichedAgents$ = new BehaviorSubject<EnrichedAgent[]>([]);

  // AppConfig integration subjects for real-time updates
  public agentConfigUpdated = new Subject<AgentConfig>();
  public tabConfigUpdated = new Subject<TabsConfiguration>();

  global_config: any;

  constructor(
    private http: HttpClient,
    private awsConfig: AwsConfigService,
    private sessionManager:SessionManagerService,
    private agentDynamoDBService: AgentDynamoDBService
  ) {
    this.loadAgentConfig();
    // Load global config first, then setup enriched agents
    // This ensures DynamoDB agents are available before enrichment
    this.initializeAgents();
  }

  /**
   * Initialize agents by loading global config first, then setting up enriched agents
   * This ensures DynamoDB-sourced agents are available during enrichment
   */
  private async initializeAgents(): Promise<void> {
    try {
      // Wait for global config to load (from DynamoDB or fallback)
      await this.getGlobalConfig();
      console.log('✅ Global config loaded, setting up enriched agents...');
      this.setupEnrichedAgents();
    } catch (error) {
      console.error('❌ Error initializing agents:', error);
      // Still setup enriched agents even if global config fails
      this.setupEnrichedAgents();
    }
  }
  ngOnInit(

  ) {

  }

  private loadAgentConfig(): void {
    // With AppConfig migration, agent config is loaded dynamically via getAgentsConfiguration()
    // Set null initially to indicate no static config available - the service will use AppConfig
    this.agentConfig$.next(null);
  }

  private setupEnrichedAgents(): void {
    // Combine AWS config (deployed agents), AgentCore config, and agent config (UI config)
    // AWS config is required, others are optional
    combineLatest([
      this.awsConfig.config$,
      this.agentConfig$
    ]).pipe(
      map(([awsConfig, agentConfig]) => {
        // Only AWS config is required - others are optional
        if (!awsConfig?.bedrock?.allAgents) {
          return [];
        }

        // Clean up agent names by removing stack prefix/suffix
        const allDeployedAgents = [...awsConfig.bedrock.allAgents];
        const stackPrefix = this.awsConfig.getStackPrefix();
        const stackSuffix = this.awsConfig.getStackSuffix();

        allDeployedAgents.forEach(agent => {
          // Clean both name and displayName
          if (agent.name) {
            agent.name = TextUtils.removeStackPrefixSuffix(agent.name, stackPrefix, stackSuffix);
          }
          if (agent.displayName) {
            agent.displayName = TextUtils.removeStackPrefixSuffix(agent.displayName, stackPrefix, stackSuffix);
          }
        });


        // Pass agentConfig even if null - the enrichment method handles fallbacks
        return this.enrichDeployedAgents(allDeployedAgents, agentConfig);
      })
    ).subscribe(enrichedAgents => {
      this.enrichedAgents$.next(enrichedAgents);
      // Emit update event for any listening components
      this.agentConfigUpdated.next(this.global_config);
    });
  }

  /**
   * Force refresh enriched agents from current global config
   * This directly updates the enrichedAgents$ BehaviorSubject without waiting for observables
   */
  private forceRefreshEnrichedAgents(): void {
    const awsConfig = this.awsConfig.getConfig();
    if (!awsConfig?.bedrock?.allAgents) {
      console.warn('⚠️ Cannot refresh enriched agents - no AWS config available');
      return;
    }

    // Clean up agent names by removing stack prefix/suffix
    const allDeployedAgents = [...awsConfig.bedrock.allAgents];
    const stackPrefix = this.awsConfig.getStackPrefix();
    const stackSuffix = this.awsConfig.getStackSuffix();

    allDeployedAgents.forEach(agent => {
      if (agent.name) {
        agent.name = TextUtils.removeStackPrefixSuffix(agent.name, stackPrefix, stackSuffix);
      }
      if (agent.displayName) {
        agent.displayName = TextUtils.removeStackPrefixSuffix(agent.displayName, stackPrefix, stackSuffix);
      }
    });

    // Enrich agents with current config
    const enrichedAgents = this.enrichDeployedAgents(allDeployedAgents, this.agentConfig$.value);
    
    // Update the BehaviorSubject directly
    this.enrichedAgents$.next(enrichedAgents);
    
    console.log(`✅ Force refreshed ${enrichedAgents.length} enriched agents`);
  }

  private enrichDeployedAgents(deployedAgents: DeployedAgent[], agentConfig: any | null): EnrichedAgent[] {
    const enrichedAgents: EnrichedAgent[] = [];

    // Find the AdFabricAgent's runtime ARN to use as default for agents without their own
    const adFabricAgent = deployedAgents.find(agent => 
      agent.agentType.toLowerCase().includes('adfabricagent') && agent.runtimeArn
    );
    const defaultRuntimeArn = adFabricAgent?.runtimeArn;
    const defaultRuntimeId = adFabricAgent?.runtimeId;
    const defaultRuntimeName = adFabricAgent?.runtimeName;
    
    if (defaultRuntimeArn) {
      console.log(`📍 Using AdFabricAgent runtime ARN as default: ${defaultRuntimeArn}`);
    }

    // First, process all deployed agents (filter out AdFabricAgent)
    const mainAgents = deployedAgents
      .filter(agent => agent.status === 'active' && !agent.agentType.toLowerCase().includes('adfabricagent'))
      .map((deployedAgent, index) => {
        // Try to find matching config (optional - graceful fallback if not found)
        const configKey = agentConfig ? this.findConfigKey(deployedAgent.agentType, agentConfig) : null;
        const config = configKey && agentConfig ? agentConfig.agents[configKey] : null;
        //console.log(config)
        // Generate display name with robust fallbacks
        let displayName: string;
        if (config?.displayName) {
          displayName = config.displayName;

        } else {
          // Use the deployed agent name directly, or generate from agentType
          displayName = TextUtils.pascalOrCamelToDisplayName(deployedAgent.displayName ? deployedAgent.displayName : deployedAgent.name);
        }


        // Stack prefix/suffix already removed in setupEnrichedAgents
        // Always use deterministic color from palette
        let color = ""
        if (!this.global_config || !this.global_config.configured_colors) {
          // Global config not loaded yet - use default color
          deployedAgent.color = '#9C1453FF';
        } else {
          deployedAgent.color = this.global_config.configured_colors[(deployedAgent as any).serviceName] || '#9C1453FF';
        }
        // Use config icon or deployed agent icon or intelligent default based on agent type
        const icon = config?.icon || deployedAgent.icon || this.getDefaultIconForAgentType(deployedAgent.agentType);

        // Get alternative names with fallbacks
        const alternativeNames = config?.alternativeNames || [];

        // Generate description with fallbacks
        const description = config?.description || this.generateDefaultDescription(displayName, deployedAgent.agentType);

        const enrichedAgent: EnrichedAgent = {
          // From deployment (source of truth)
          name: deployedAgent.name,
          agentType: deployedAgent.agentType,
          status: deployedAgent.status,
          id: deployedAgent.agentType,
          aliasId: deployedAgent.agentType || (deployedAgent.deploymentType === 'agentcore' ? undefined : 'latest'),
          deploymentType: deployedAgent.deploymentType,

          // AgentCore specific fields - use default runtime ARN if agent doesn't have its own
          runtimeId: deployedAgent.runtimeId || defaultRuntimeId,
          runtimeArn: deployedAgent.runtimeArn || defaultRuntimeArn,
          runtimeName: deployedAgent.runtimeName || defaultRuntimeName,

          // From config + computed (with fallbacks)
          displayName,
          color: (deployedAgent.color as string),
          icon,
          alternativeNames,
          description,

          // Computed key for lookups (use agentType as canonical key)
          key: deployedAgent.agentType,

          // Generate agent-specific session ID
          sessionId: ''
        };

        return enrichedAgent;
      });

    enrichedAgents.push(...mainAgents);

    // Then, add orchestrator agents from global config that aren't already deployed
    if (this.global_config?.agent_configs) {
      for (const [agentKey, agentConfigData] of Object.entries(this.global_config.agent_configs)) {
        const config = agentConfigData as any;

        // Check if this orchestrator agent already exists in enrichedAgents
        let existingOrchestrator = enrichedAgents.find(agent =>
          agent.key === agentKey ||
          agent.agentType === agentKey ||
          agent.name === agentKey
        );

        if (!existingOrchestrator) {
          // Create the orchestrator agent from global config
          // Use AdFabricAgent's runtime ARN as default for agents without their own
          existingOrchestrator = {
            name: agentKey,
            agentType: agentKey,
            status: 'active',
            id: config.agent_id || `${agentKey}`,
            aliasId: 'latest',
            deploymentType: 'agentcore',
            displayName: agentKey,
            color: this.global_config.configured_colors?.[agentKey] || '#9C1453FF',
            icon: this.getDefaultIconForAgentType(agentKey),
            alternativeNames: [],
            description: config.agent_description || this.generateDefaultDescription(config.agent_display_name || agentKey, agentKey),
            key: agentKey,
            sessionId: '',
            teamName: config.team_name || 'Unknown Team',
            runtimeArn: defaultRuntimeArn,
            runtimeId: defaultRuntimeId,
            runtimeName: defaultRuntimeName
          };

          enrichedAgents.push(existingOrchestrator);
        }

        // Add collaborator agents
        if (config.tool_agent_names && Array.isArray(config.tool_agent_names)) {
          for (const collaboratorName of config.tool_agent_names) {
            const existingCollaborator = enrichedAgents.find(agent =>
              agent.key === collaboratorName ||
              agent.agentType === collaboratorName ||
              agent.name === collaboratorName
            );

            if (!existingCollaborator) {
              const collaboratorAgent: EnrichedAgent = {
                name: collaboratorName,
                agentType: collaboratorName,
                status: 'active',
                id: collaboratorName,
                aliasId: collaboratorName,
                deploymentType: 'agentcore',
                displayName: collaboratorName,
                color: (this.global_config.configured_colors as Array<any>)[collaboratorName] || '#9C1453FF',
                icon: this.getDefaultIconForAgentType(collaboratorName),
                alternativeNames: [],
                description: this.generateDefaultDescription(TextUtils.pascalOrCamelToDisplayName(collaboratorName), collaboratorName),
                key: collaboratorName,
                sessionId: '',
                teamName: config.team_name || 'Unknown Team',
                orchestratorAgent: agentKey,
                runtimeArn: existingOrchestrator?.runtimeArn || defaultRuntimeArn,  // Collaborators use orchestrator's runtime ARN or default
                runtimeId: existingOrchestrator?.runtimeId || defaultRuntimeId,
                runtimeName: existingOrchestrator?.runtimeName || defaultRuntimeName
              };

              enrichedAgents.push(collaboratorAgent);

            }
          }
        }

        // Update existing agents with team info and ensure they have runtime ARN
        const mainAgent = enrichedAgents.find(agent =>
          agent.key === agentKey ||
          agent.agentType === agentKey ||
          agent.name === agentKey
        );
        if (mainAgent) {
          if (config.team_name) {
            (mainAgent as any).teamName = config.team_name;
          }
          // Ensure agent has runtime ARN (use default if not set)
          if (!mainAgent.runtimeArn && defaultRuntimeArn) {
            mainAgent.runtimeArn = defaultRuntimeArn;
            mainAgent.runtimeId = mainAgent.runtimeId || defaultRuntimeId;
            mainAgent.runtimeName = mainAgent.runtimeName || defaultRuntimeName;
          }
        }
      }
    }

    return enrichedAgents;
  }

  private findConfigKey(agentType: string, agentConfig: any): string | null {
    if (!agentType || !agentConfig?.agents) return null;

    // Normalize the search term using our centralized method
    const stackPrefix = this.awsConfig.getStackPrefix();
    const stackSuffix = this.awsConfig.getStackSuffix();
    const normalizedSearchTerm = TextUtils.pascalOrCamelToDisplayName(TextUtils.removeStackPrefixSuffix(agentType, stackPrefix, stackSuffix));
    const searchTermLower = normalizedSearchTerm.toLowerCase();

    // 1. Direct key match (case-insensitive)
    for (const [key, config] of Object.entries(agentConfig.agents)) {
      const normalizedKey = TextUtils.pascalOrCamelToDisplayName(TextUtils.removeStackPrefixSuffix(key, stackPrefix, stackSuffix));
      if (normalizedKey.toLowerCase() === searchTermLower) {
        return key;
      }
    }

    // 2. Search by agentType in config values (case-insensitive)
    for (const [key, config] of Object.entries(agentConfig.agents)) {
      if ((config as any).agentType) {
        const normalizedConfigType = TextUtils.pascalOrCamelToDisplayName(TextUtils.removeStackPrefixSuffix((config as any).agentType, stackPrefix, stackSuffix));
        if (normalizedConfigType.toLowerCase() === searchTermLower) {
          return key;
        }
      }
    }
    const originalLower = agentType.toLowerCase();
    for (const [key, config] of Object.entries(agentConfig.agents)) {
      if (key.toLowerCase() === originalLower ||
        (config as any).agentType?.toLowerCase() === originalLower ||
        (config as any).displayName?.toLowerCase() === originalLower) {
        return key;
      }
    }

    return null;
  }

  private getColorFromConfig(index: number) {
    // if(!this.global_config) await this.getGlobalConfig();    
    // if(this.global_config && this.global_config.configured_colors) {
    //   const colorKeys = Object.keys(this.global_config.configured_colors);
    //   if (colorKeys.length > 0) {
    //     const colorKey = colorKeys[index % colorKeys.length];
    //     return this.global_config.configured_colors[colorKey] || "#9C1453FF";
    //   }
    // }

    return "#9C1453FF";
  }

  private getColorForAgent(agent: string) {
    if (!this.global_config || !this.global_config.configured_colors) {
      this.getGlobalConfig().then(config=>{console.log('loaded config')});
    }
    return this.global_config.configured_colors[agent] || "#9C1453FF";

  }

  private async getGlobalConfig() {
    if (!this.global_config) {
      // First, check if AgentDynamoDBService is configured (agentConfigTable in aws-config.json)
      // Validates: Requirements 6.9, 12.4, 12.5
      const isDynamoDBConfigured = await this.agentDynamoDBService.isConfigured();
      
      if (isDynamoDBConfigured) {
        try {
          // Try to load from DynamoDB first
          console.log('🔍 Attempting to load global config from DynamoDB...');
          const dynamoConfig = await this.agentDynamoDBService.getGlobalConfig();
          
          if (dynamoConfig) {
            console.log('✅ Global config loaded successfully from DynamoDB');
            this.global_config = dynamoConfig;
            return this.global_config;
          } else {
            console.warn('⚠️ DynamoDB returned no config, falling back to local file');
          }
        } catch (dynamoError) {
          console.warn('⚠️ Failed to load from DynamoDB, falling back to local file:', dynamoError);
        }
      } else {
        console.log('ℹ️ agentConfigTable not configured in aws-config.json, using local file');
      }
      
      // Fallback to local file if DynamoDB unavailable or not configured
      // Validates: Requirement 12.5
      try {
        const response = await fetch('/assets/global_configuration.json');
        if (response.ok) {
          const agentcore_config = await response.json();
          console.log('✅ Global config loaded successfully from local file');
          this.global_config = agentcore_config;
          return this.global_config;
        } else {
          console.warn('Global config not found in local file.');
          return undefined;
        }
      } catch (error) {
        console.error('❌ Error loading global config from local file:', error);
        return undefined;
      }
    }
    return this.global_config;
  }

  /**
   * Force reload global configuration from DynamoDB and refresh enriched agents
   * Call this after saving/updating/deleting agents to refresh the typeahead and agent stores
   */
  public async reloadGlobalConfig(): Promise<void> {
    console.log('🔄 Reloading global configuration...');
    
    // Clear the cached global config to force a fresh load
    this.global_config = null;
    
    // Clear the DynamoDB cache as well
    this.agentDynamoDBService.clearCache();
    
    // Reload the global config
    await this.getGlobalConfig();
    
    // Clear agent color cache to pick up any color changes
    this.clearAgentColorCache();
    
    // Directly rebuild enriched agents with fresh data
    await this.rebuildEnrichedAgents();
    
    console.log('✅ Global configuration reloaded successfully');
  }

  /**
   * Generate intelligent default icon based on agent type
   */
  private getDefaultIconForAgentType(agentType: string): string {
    const type = agentType.toLowerCase();

    if (type.includes('bid') || type.includes('optimization')) return 'trending_up';
    if (type.includes('creative') || type.includes('design')) return 'palette';
    if (type.includes('audience') || type.includes('targeting')) return 'people';
    if (type.includes('campaign')) return 'campaign';
    if (type.includes('channel') || type.includes('media')) return 'tv';
    if (type.includes('analysis') || type.includes('analytics')) return 'analytics';
    if (type.includes('timing') || type.includes('schedule')) return 'schedule';
    if (type.includes('revenue') || type.includes('format')) return 'monetization_on';
    if (type.includes('inventory') || type.includes('forecast')) return 'inventory';
    if (type.includes('contextual') || type.includes('context')) return 'security';

    return 'psychology'; // Default fallback
  }

  /**
   * Generate default description based on display name and agent type
   */
  private generateDefaultDescription(displayName: string, agentType: string): string {
    const type = agentType.toLowerCase();
    const name = displayName.toLowerCase();

    // Specific agent descriptions
    if (name.includes('media planning')) {
      return 'Strategic media planning and revenue optimization from publisher perspective';
    }
    if (name.includes('yield optimization')) {
      return 'Maximizes publisher revenue through intelligent yield management and pricing';
    }
    if (name.includes('campaign optimization')) {
      return 'End-to-end campaign strategy and optimization for advertisers and agencies';
    }
    if (name.includes('inventory optimization')) {
      return 'Forecasts inventory demand and optimizes supply-side revenue strategies';
    }
    if (name.includes('audience intelligence')) {
      return 'Consumer behavior analysis and privacy-compliant audience targeting strategies';
    }
    if (name.includes('audience strategy')) {
      return 'Develops data-driven audience segmentation and targeting recommendations';
    }
    if (name.includes('channel mix')) {
      return 'Optimizes media mix allocation and cross-channel attribution strategies';
    }
    if (name.includes('campaign architecture')) {
      return 'Designs campaign structure, measurement frameworks, and implementation plans';
    }
    if (name.includes('creative selection')) {
      return 'AI-powered creative optimization with real-time image generation capabilities';
    }
    if (name.includes('campaign execution') || name.includes('timing')) {
      return 'Optimizes campaign timing, pacing strategies, and execution scheduling';
    }
    if (name.includes('contextual analysis')) {
      return 'Brand safety assessment and contextual content classification';
    }
    if (name.includes('bid optimization')) {
      return 'Dynamic bid adjustment and performance optimization for maximum ROI';
    }
    if (name.includes('ad format')) {
      return 'Selects optimal ad formats and revenue strategies based on context';
    }
    if (name.includes('format strategy')) {
      return 'Strategic ad format optimization and performance analysis';
    }
    if (name.includes('timing strategy')) {
      return 'Optimal timing analysis for campaign execution and audience engagement';
    }
    if (name.includes('ad load')) {
      return 'Balances ad load optimization with viewer experience and revenue goals';
    }

    // Generic fallback
    return `Specialized ${displayName.toLowerCase()} for advertising optimization`;
  }

  // Public API methods
  public getEnrichedAgents(): EnrichedAgent[] {
    return (this.enrichedAgents$.value);
  }

  getEnrichedAgentsSync(): EnrichedAgent[] {
    return this.enrichedAgents$.value;
  }

  getAgentByKey(key: string): EnrichedAgent | null {
    const agents = this.enrichedAgents$.value;
    if (agents.length === 0) {
      return null;
    }
    return agents.find(agent => agent.key === key) || null;
  }

  getAgentById(id: string): EnrichedAgent | null {
    return this.enrichedAgents$.value.find(agent => agent.key === id || agent.name === id || agent.id == id || agent.agentType == id) || null;
  }

  getAgentByAliasId(aliasId: string): EnrichedAgent | null {
    return this.enrichedAgents$.value.find(agent => agent.aliasId && agent.aliasId === aliasId) || null;
  }

  getAgentByDisplayName(displayName: string): EnrichedAgent | null {
    return this.enrichedAgents$.value.find(agent =>
      agent.displayName.toLowerCase() === displayName.toLowerCase()
    ) || null;
  }

  getAgentByAlternativeName(name: string): EnrichedAgent | null {
    if (!name) return null;

    const agents = this.enrichedAgents$.value;
    if (agents.length === 0) {
      return null;
    }

    const searchName = name.toLowerCase();
    return agents.find(agent =>
      agent.alternativeNames.some(altName => altName.toLowerCase() === searchName) ||
      agent.agentType.toLowerCase() === searchName ||
      agent.key.toLowerCase() === searchName
    ) || null;
  }

  async getAgentBGColor(agent: EnrichedAgent): Promise<string> {
    // Background color is same as agent color with slight transparency
    let color = agent.color;
    if (agent.color.endsWith(")") && color.indexOf("rgba") == -1)
      color = color.replace(')', '.2)').replace('rgb', 'rgba');
    else if (color.indexOf("#") > -1)
      color = color + "20";
    return color; // Add 20% opacity
  }

  getAgentColorSync(agentKey: string, type = 'text'): string {
    // Check cache first
    if (this.agentColorCache.has(agentKey)) {
      return this.agentColorCache.get(agentKey)!;
    }

    // Try to get from global config synchronously
    if (this.global_config && this.global_config.configured_colors) {
      const color = this.global_config.configured_colors[agentKey];
      if (color) {
        this.agentColorCache.set(agentKey, color);
        return color;
      }
    }

    // Fallback to default color
    return "#9C1453FF";
  }

  getAgentDisplayName(agentKey: string): string {
    return TextUtils.pascalOrCamelToDisplayName(agentKey);
  }

  getDefaultAgent(): EnrichedAgent | null {
    const config = this.agentConfig$.value;

    // Try to get default from config
    if (config?.defaultAgent?.agentType) {
      const defaultAgent = this.getAgentById(config.defaultAgent.agentType);
      if (defaultAgent) return defaultAgent;
    }

    // Fallback to first available active agent
    const activeAgents = this.getActiveAgents();
    if (activeAgents.length > 0) {
      return activeAgents[0];
    }

    return null;
  }

  private async getDefaultColor(): Promise<string> {
    return await this.getColorFromConfig(0);
  }

  // For backward compatibility with existing services
  getAvailableAgents(tabConfig: any = null): EnrichedAgent[] {
    var agents = this.getEnrichedAgentsSync();
    if (tabConfig == null) return agents;

    return agents.filter(a => tabConfig.availableAgents.indexOf(a.agentType) > -1);
  }

  // Session management - Agent-specific session IDs
  private agentSessions = new Map<string, string>(); // agentId -> sessionId
  private sessionColors = new Map<string, string>();

  // Agent color caching to ensure consistency within a session
  private agentColorCache = new Map<string, string>(); // agentKey -> color
  /**
   * Clear all agent sessions
   */
  clearAllAgentSessions(): void {
    this.agentSessions.clear();
    this.sessionColors.clear();
    this.agentColorCache.clear();
  }

  /**
   * Clear agent color cache to reset color assignments
   */
  clearAgentColorCache(): void {
    this.agentColorCache.clear();
  }

  /**
   * Get all cached agent colors for debugging
   */
  getAgentColorCache(): Map<string, string> {
    return new Map(this.agentColorCache);
  }

  getSessionColor(sessionId: string, agentKey?: EnrichedAgent): string {
    if (this.sessionColors.has(sessionId)) {
      return this.sessionColors.get(sessionId)!;
    }

    const color = agentKey ? agentKey.color : this.getColorFromConfig(0);
    this.sessionColors.set(sessionId, color);
    return color;
  }

  setSessionColor(sessionId: string, color: string): void {
    this.sessionColors.set(sessionId, color);
  }

  clearSessionColor(sessionId: string): void {
    this.sessionColors.delete(sessionId);
  }

  /**
   * Get agent by any identifier (key, id, displayName, alternativeName)
   * This is the main lookup method that should be used throughout the app
   * 
   * This method handles all the complexity of agent name normalization,
   * stack prefix/suffix removal, and fuzzy matching in one place.
   */
  getAgent(agentName: string): EnrichedAgent | null {
    if (!agentName) return null;
    agentName = agentName.replace(' ', '')
    const agents = this.enrichedAgents$.value;
    if (agents.length === 0) return null;

    // Get stack prefix/suffix for normalization
    const stackPrefix = this.awsConfig.getStackPrefix();
    const stackSuffix = this.awsConfig.getStackSuffix();

    // Normalize the search identifier
    const normalizedIdentifier = TextUtils.removeStackPrefixSuffix(agentName, stackPrefix, stackSuffix);
    const searchTerm = normalizedIdentifier.toLowerCase().replaceAll(" ", "").replaceAll("-", "").replaceAll("_", "");

    let agentResult: any = null;
    // 1. Try exact matches first (highest priority)
    for (const agent of agents) {
      if (agent.name?.toLowerCase().replaceAll(" ", "").replaceAll("-", "").replaceAll("_", "") === searchTerm ||
        agent.aliasId?.toLowerCase().replaceAll(" ", "").replaceAll("-", "").replaceAll("_", "") === searchTerm ||
        agent.key?.toLowerCase().replaceAll(" ", "").replaceAll("-", "").replaceAll("_", "") === searchTerm) {
        agentResult = agent;
        break;
      }
    }
    if (!agentResult) {
      // 2. Try display name matches
      for (const agent of agents) {
        if (agent.displayName.toLowerCase() === searchTerm ||
          agent.displayName.toLowerCase().replace(/\s+/g, '') === searchTerm) {
          agentResult = agent;
          break;
        }
      }
    }

    // 4. Try partial matches as fallback
    if (!agentResult) {
      for (const agent of agents) {
        if ((agent.name?.toLowerCase().replaceAll(" ", "").includes(searchTerm.toLowerCase().replaceAll(" ", "")) ||
          agent.aliasId?.toLowerCase().replaceAll(" ", "").includes(searchTerm.toLowerCase().replaceAll(" ", "")) ||
          agent.key?.toLowerCase().replaceAll(" ", "").includes(searchTerm.toLowerCase().replaceAll(" ", "")))) {
          agentResult = agent
          break;
        }
      }
    }
    if (agentResult && (!agentResult.orchestratorAgent || agentResult.agentType == agentResult.orchestratorAgent)) return agentResult
    if (agentResult != null && !agentResult.runtimeArn) {
      let orchestrator = this.getAgent(agentResult.orchestratorAgent);
      if (orchestrator) {
        agentResult.runtimeArn = orchestrator?.runtimeArn;
        agentResult.runtimeId = orchestrator?.runtimeId;
      }
    }
    if (agentResult) return agentResult
    console.log("Could not match identifier: " + agentName);
    return this.enrichedAgents$.value[0];
  }


  /**
   * Check if an agent exists by any identifier
   */
  hasAgent(identifier: string): boolean {
    return this.getAgent(identifier) !== null;
  }

  /**
   * Get all active agents (filtered by status)
   */
  getActiveAgents(): EnrichedAgent[] {
    return (this.enrichedAgents$.value);
  }


  /**
   * Get all AgentCore agents
   */
  getAgentCoreAgents(): EnrichedAgent[] {
    return this.enrichedAgents$.value.filter(agent => agent.deploymentType === 'agentcore');
  }

  /**
   * Get agent by runtime ID (for AgentCore agents)
   */
  getAgentByRuntimeId(runtimeId: string): EnrichedAgent | null {
    return this.enrichedAgents$.value.find(agent => agent.runtimeId === runtimeId) || null;
  }

  /**
   * Search agents by text (for typeahead functionality)
   */
  searchAgents(searchText: string, maxResults: number = 10): EnrichedAgent[] {
    if (!searchText.trim()) {
      return this.getActiveAgents().slice(0, maxResults);
    }

    const searchLower = searchText.toLowerCase();
    const agents = this.getActiveAgents();

    // Filter and score agents based on search relevance
    const scored = agents.map(agent => {
      let score = 0;

      // Exact display name match (highest priority)
      if (agent.displayName.toLowerCase() === searchLower || searchLower == '') {
        score += 100;
      } else if (agent.displayName.toLowerCase().startsWith(searchLower)) {
        score += 80;
      } else if (agent.displayName.toLowerCase().includes(searchLower)) {
        score += 60;
      }

      // Alternative names
      for (const altName of agent.alternativeNames) {
        if (altName.toLowerCase() === searchLower) {
          score += 90;
        } else if (altName.toLowerCase().startsWith(searchLower)) {
          score += 70;
        } else if (altName.toLowerCase().includes(searchLower)) {
          score += 50;
        }
      }

      // Agent type
      if (agent.agentType.toLowerCase().includes(searchLower)) {
        score += 40;
      }

      // Description
      if (agent.description.toLowerCase().includes(searchLower)) {
        score += 30;
      }

      return { agent, score };
    })
      .filter(item => item.score > 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, maxResults);

    return scored.map(item => item.agent);
  }

  // ========================================
  // AppConfig Integration Methods (Task 5.1)
  // ========================================

  /**
   * Get agents configuration from AppConfig with fallback strategies
   * Requirements: 3.1, 3.2
   */
  async getAgentsConfiguration(): Promise<any> {
    try {

      // Try to get from AppConfig first
      const appConfigData = await this.awsConfig.getAppConfigData('agents');

      if (appConfigData && this.validateAgentsConfiguration(appConfigData)) {
        return appConfigData;
      }


    } catch (error) {
      console.error('❌ Error retrieving agents configuration from AppConfig:', error);

    }
  }

  /**
   * Get tabs configuration from AppConfig with fallback strategies
   * Requirements: 3.1, 3.2
   */
  async getTabsConfiguration(): Promise<TabsConfiguration> {
    try {

      // Try to get from AppConfig first
      //const appConfigData = await this.awsConfig.getAppConfigData('tabs');

      /*if (appConfigData && this.validateTabsConfiguration(appConfigData)) {
        return appConfigData;
      }*/

      // If AppConfig data is invalid, fall back to static file
      //console.warn('⚠️ AppConfig tabs data invalid, falling back to static file');
      return await this.getStaticTabsConfiguration();

    } catch (error) {
      console.error('❌ Error retrieving tabs configuration from AppConfig:', error);

      // Fall back to static file on error
      return await this.getStaticTabsConfiguration();
    }
  }

  /**
   * Get static agents configuration as fallback
   */
  private async getStaticAgentsConfiguration(): Promise<AgentConfig> {
    try {
      const response = await fetch('/assets/agents-config.json');
      if (!response.ok) {
        throw new Error(`Failed to fetch agents-config.json: ${response.statusText}`);
      }

      const data = await response.json();

      if (this.validateAgentsConfiguration(data)) {
        return data;
      }

      throw new Error('Static agents configuration is invalid');

    } catch (error) {
      console.error('❌ Error loading static agents configuration:', error);

      // Return default configuration as last resort
      return this.getDefaultAgentsConfiguration();
    }
  }

  /**
   * Get static tabs configuration as fallback - now loads from S3 creatives bucket
   */
  private async getStaticTabsConfiguration(): Promise<TabsConfiguration> {
    try {
      // Try to load from S3 creatives bucket first
      const s3Config = await this.loadTabsFromS3();
      if (s3Config) {
        return s3Config;
      }

      // Fallback to local assets if S3 fails
      const response = await fetch('/assets/tab-configurations.json');
      if (!response.ok) {
        throw new Error(`Failed to fetch tab-configurations.json: ${response.statusText}`);
      }

      const data = await response.json();

      if (this.validateTabsConfiguration(data)) {
        return data;
      }


      throw new Error('Static tabs configuration is invalid');

    } catch (error) {
      console.error('❌ Error loading static tabs configuration:', error);

      // Return default configuration as last resort
      return this.getDefaultTabsConfiguration();
    }
  }

  /**
   * Load tabs configuration from S3 creatives bucket
   */
  private async loadTabsFromS3(): Promise<TabsConfiguration | null> {
    try {
      // Import AWS SDK dynamically
      const { S3Client, GetObjectCommand } = await import('@aws-sdk/client-s3');

      // Get AWS config and credentials using cached session
      const awsConfig = await this.awsConfig.getConfig();
      const awsCredentials = await this.awsConfig.getAwsConfig();

      if (!awsCredentials?.credentials) {
        console.warn('No AWS credentials available for S3 access');
        return null;
      }

      if (!awsConfig || !awsConfig.creativesBucket) {
        console.warn('No creatives bucket configured in AWS config');
        return null;
      }

      // Initialize S3 client
      const s3Client = new S3Client({
        region: awsConfig.aws.region,
        credentials: awsCredentials.credentials
      });

      // Get the configuration file from S3
      const getCommand = new GetObjectCommand({
        Bucket: awsConfig.creativesBucket,
        Key: 'configurations/tab-configurations.json',

      });

      const response = await s3Client.send(getCommand);

      if (response.Body) {
        const configText = await response.Body.transformToString();
        const data = JSON.parse(configText);
        console.log("current config for tabs: ", data);
        if (this.validateTabsConfiguration(data)) {
          return data;
        } else {
          console.warn('⚠️  Tab configurations from S3 failed validation');
        }
      }

      return null;

    } catch (error: any) {
      // Log different types of errors with appropriate detail
      if (error.name === 'NoSuchKey') {
        console.warn('📁 Tab configurations file not found in S3 - this is normal for first deployment');
      } else if (error.name === 'AccessDenied') {
        console.warn('🔒 Access denied to S3 bucket - check permissions');
      } else {
        console.warn('⚠️  Could not load tabs configuration from S3:', error.message || error);
      }
      return null;
    }
  }

  /**
   * Get default agents configuration as last resort
   */
  private getDefaultAgentsConfiguration(): any {
    console.warn('⚠️ Using default agents configuration');

    return {
      agents: {},
      defaultAgent: {
        agentType: 'default',
        displayName: 'Default Agent',
        color: '#6B46C1',
        icon: 'smart_toy',
        aliasId: '',
        id: '',
        name: '',
        status: 'inactive'
      }
    };
  }

  /**
   * Get default tabs configuration as last resort
   */
  private getDefaultTabsConfiguration(): TabsConfiguration {
    console.warn('⚠️ Using default tabs configuration');

    return {
      tabConfigurations: {}
    };
  }

  /**
   * Validate agents configuration structure
   */
  private validateAgentsConfiguration(config: any): config is AgentConfig {
    if (!config || typeof config !== 'object') {
      console.error('❌ Agents configuration is not an object');
      return false;
    }

    if (!config.agents || typeof config.agents !== 'object') {
      console.error('❌ Agents configuration missing or invalid agents object');
      return false;
    }

    if (!config.defaultAgent || typeof config.defaultAgent !== 'object') {
      console.error('❌ Agents configuration missing or invalid defaultAgent object');
      return false;
    }

    // Validate defaultAgent structure
    const defaultAgent = config.defaultAgent;
    if (!defaultAgent.agentType || !defaultAgent.displayName ||
      !defaultAgent.color || !defaultAgent.icon) {
      console.error('❌ Default agent missing required properties');
      return false;
    }

    // Validate each agent in the agents object
    for (const [key, agent] of Object.entries(config.agents)) {
      if (!agent || typeof agent !== 'object') {
        console.error(`❌ Agent ${key} is not an object`);
        return false;
      }

      const agentData = agent as any;
      if (!agentData.agentType || !agentData.displayName ||
        !agentData.color || !agentData.icon) {
        console.error(`❌ Agent ${key} missing required properties`);
        return false;
      }

      // Validate alternativeNames if present
      if (agentData.alternativeNames && !Array.isArray(agentData.alternativeNames)) {
        console.error(`❌ Agent ${key} alternativeNames is not an array`);
        return false;
      }
    }

    return true;
  }

  /**
   * Validate tabs configuration structure
   */
  private validateTabsConfiguration(config: any): config is TabsConfiguration {
    if (!config || typeof config !== 'object') {
      console.error('❌ Tabs configuration is not an object');
      return false;
    }

    if (!config.tabConfigurations || typeof config.tabConfigurations !== 'object') {
      console.error('❌ Tabs configuration missing or invalid tabConfigurations object');
      return false;
    }

    // Validate each tab configuration
    for (const [key, tab] of Object.entries(config.tabConfigurations)) {
      if (!tab || typeof tab !== 'object') {
        console.error(`❌ Tab ${key} is not an object`);
        return false;
      }

      const tabData = tab as any;
      if (!tabData.id || !tabData.title || !tabData.description || !tabData.icon) {
        console.error(`❌ Tab ${key} missing required properties (id, title, description, icon)`);
        return false;
      }

      // Validate availableAgents if present
      if (tabData.availableAgents && !Array.isArray(tabData.availableAgents)) {
        console.error(`❌ Tab ${key} availableAgents is not an array`);
        return false;
      }
    }

    return true;
  }

  /**
   * Handle configuration retrieval errors with appropriate fallback strategies
   */
  private async handleConfigurationError(
    error: any,
    configType: 'agents' | 'tabs',
    fallbackMethod: () => Promise<any>
  ): Promise<any> {
    console.error(`❌ Error retrieving ${configType} configuration:`, error);

    // Log error details for debugging
    if (error instanceof Error) {
      console.error(`Error message: ${error.message}`);
      console.error(`Error stack: ${error.stack}`);
    }

    // Determine if this is a network error, authentication error, or data error
    const errorMessage = error instanceof Error ? error.message.toLowerCase() : '';

    if (errorMessage.includes('network') || errorMessage.includes('timeout')) {
      console.warn(`⚠️ Network error detected for ${configType} configuration, using fallback`);
    } else if (errorMessage.includes('auth') || errorMessage.includes('credential')) {
      console.warn(`⚠️ Authentication error detected for ${configType} configuration, using fallback`);
    } else if (errorMessage.includes('json') || errorMessage.includes('parse')) {
      console.warn(`⚠️ Data parsing error detected for ${configType} configuration, using fallback`);
    } else {
      console.warn(`⚠️ Unknown error detected for ${configType} configuration, using fallback`);
    }

    // Use the provided fallback method
    try {
      return await fallbackMethod();
    } catch (fallbackError) {
      console.error(`❌ Fallback method also failed for ${configType}:`, fallbackError);

      // Return appropriate default configuration
      if (configType === 'agents') {
        return this.getDefaultAgentsConfiguration();
      } else {
        return this.getDefaultTabsConfiguration();
      }
    }
  }

  // ========================================
  // Configuration Update Methods (Task 5.2)
  // ========================================

  /**
   * Update agent configuration in AppConfig with UI refresh triggers
   * Requirements: 3.4, 3.5
   */
  async updateAgentConfiguration(agentConfig: AgentConfig): Promise<boolean> {
    try {

      // Validate configuration before updating
      if (!this.validateAgentsConfiguration(agentConfig)) {
        console.error('❌ Invalid agent configuration provided');
        return false;
      }
      return true;
    } catch (error) {
      console.error('❌ Error updating agent configuration:', error);
      return false;
    }
  }

  /**
   * Update tab configuration in AppConfig with UI refresh triggers
   * Requirements: 3.4, 3.5
   */
  async updateTabConfiguration(tabConfig: TabsConfiguration): Promise<boolean> {
    try {

      // Validate configuration before updating
      if (!this.validateTabsConfiguration(tabConfig)) {
        console.error('❌ Invalid tab configuration provided');
        return false;
      }
      return true;
    } catch (error) {
      console.error('❌ Error updating tab configuration:', error);
      return false;
    }
  }

  /**
   * Batch update both agent and tab configurations
   * Useful for operations that need to update related configurations atomically
   */
  async updateBothConfigurations(
    agentConfig: AgentConfig,
    tabConfig: TabsConfiguration
  ): Promise<{ overallSuccess: boolean }> {
    try {

      // Validate both configurations before updating
      const agentsValid = this.validateAgentsConfiguration(agentConfig);
      const tabsValid = this.validateTabsConfiguration(tabConfig);

      if (!agentsValid || !tabsValid) {
        console.error('❌ One or both configurations are invalid');
        return { overallSuccess: false };
      }

      // Trigger UI updates for successful updates
      this.agentConfig$.next(agentConfig);
      this.agentConfigUpdated.next(agentConfig);
      this.refreshEnrichedAgents();

      this.tabConfigUpdated.next(tabConfig);

      return { overallSuccess: true };

    } catch (error) {
      console.error('❌ Error in batch configuration update:', error);
      return { overallSuccess: false };
    }
  }

  /**
   * Refresh enriched agents after configuration changes
   */
  private refreshEnrichedAgents(): void {
    try {

      // Trigger the enriched agents recalculation by re-emitting current values
      // This will cause the combineLatest in setupEnrichedAgents to re-execute
      const currentAgentConfig = this.agentConfig$.value;
      if (currentAgentConfig) {
        this.agentConfig$.next(currentAgentConfig);
      }

    } catch (error) {
      console.error('❌ Error refreshing enriched agents:', error);
    }
  }

  /**
   * Public method to reload agent configurations from DynamoDB
   * Call this after saving/updating/deleting agents to refresh the typeahead and local stores
   */
  public async reloadAgentConfigurations(): Promise<void> {
    try {
      console.log('🔄 Reloading agent configurations...');
      
      // Clear the cached global config to force a fresh load
      this.global_config = null;
      
      // Clear the DynamoDB service cache as well
      this.agentDynamoDBService.clearCache();
      
      // Reload global config from DynamoDB
      await this.getGlobalConfig();
      
      // Clear agent color cache to pick up any color changes
      this.clearAgentColorCache();
      
      // Directly rebuild enriched agents with fresh data
      // This is more reliable than setupEnrichedAgents() which relies on observable emissions
      await this.rebuildEnrichedAgents();
      
      // Emit update event for any listening components
      this.agentConfigUpdated.next(this.global_config);
      
      console.log('✅ Agent configurations reloaded successfully');
      console.log(`📊 Total enriched agents: ${this.enrichedAgents$.value.length}`);
    } catch (error) {
      console.error('❌ Error reloading agent configurations:', error);
    }
  }

  /**
   * Directly rebuild enriched agents from current AWS config and global config
   * This bypasses the observable subscription to ensure immediate updates
   */
  private async rebuildEnrichedAgents(): Promise<void> {
    const awsConfig = this.awsConfig.getConfig();
    
    if (!awsConfig?.bedrock?.allAgents) {
      console.warn('⚠️ No deployed agents found in AWS config');
      return;
    }
    
    // Ensure global config is loaded (from DynamoDB or fallback)
    if (!this.global_config) {
      console.log('🔄 Loading global config before rebuilding enriched agents...');
      await this.getGlobalConfig();
    }
    
    // Clean up agent names by removing stack prefix/suffix
    const allDeployedAgents = [...awsConfig.bedrock.allAgents];
    const stackPrefix = this.awsConfig.getStackPrefix();
    const stackSuffix = this.awsConfig.getStackSuffix();
    
    allDeployedAgents.forEach(agent => {
      if (agent.name) {
        agent.name = TextUtils.removeStackPrefixSuffix(agent.name, stackPrefix, stackSuffix);
      }
      if (agent.displayName) {
        agent.displayName = TextUtils.removeStackPrefixSuffix(agent.displayName, stackPrefix, stackSuffix);
      }
    });
    
    // Enrich agents with the fresh global config
    const enrichedAgents = this.enrichDeployedAgents(allDeployedAgents, this.agentConfig$.value);
    
    // Update the BehaviorSubject directly
    this.enrichedAgents$.next(enrichedAgents);
    
    console.log(`🔄 Rebuilt ${enrichedAgents.length} enriched agents from fresh config`);
  }

  /**
   * Create UI refresh triggers after successful configuration updates
   * This method can be extended to trigger specific UI component updates
   */
  private triggerUIRefresh(configType: 'agents' | 'tabs', config: any): void {
    try {

      // Emit configuration update events
      if (configType === 'agents') {
        this.agentConfigUpdated.next(config);
      } else if (configType === 'tabs') {
        this.tabConfigUpdated.next(config);
      }

      // Additional UI refresh logic can be added here
      // For example, triggering specific component updates or notifications

    } catch (error) {
      console.error(`❌ Error triggering UI refresh for ${configType}:`, error);
    }
  }

  /**
   * Validate configuration update before sending to AppConfig
   * Performs comprehensive validation including cross-references
   */
  private async validateConfigurationUpdate(
    configType: 'agents' | 'tabs',
    newConfig: any,
    performCrossValidation: boolean = true
  ): Promise<{ isValid: boolean; errors: string[] }> {
    const errors: string[] = [];

    try {
      // Basic structure validation
      let structureValid = false;

      if (configType === 'agents') {
        structureValid = this.validateAgentsConfiguration(newConfig);
        if (!structureValid) {
          errors.push('Agent configuration structure is invalid');
        }
      } else if (configType === 'tabs') {
        structureValid = this.validateTabsConfiguration(newConfig);
        if (!structureValid) {
          errors.push('Tab configuration structure is invalid');
        }
      }

      if (!structureValid) {
        return { isValid: false, errors };
      }

      // Cross-validation if requested
      if (performCrossValidation) {
        const crossValidationErrors = await this.performCrossValidation(configType, newConfig);
        errors.push(...crossValidationErrors);
      }

      const isValid = errors.length === 0;

      if (isValid) {
      } else {
        console.warn(`⚠️ Configuration validation failed for ${configType}:`, errors);
      }

      return { isValid, errors };

    } catch (error) {
      console.error(`❌ Error during configuration validation for ${configType}:`, error);
      errors.push(`Validation error: ${error instanceof Error ? error.message : 'Unknown error'}`);
      return { isValid: false, errors };
    }
  }

  /**
   * Perform cross-validation between agent and tab configurations
   */
  private async performCrossValidation(
    configType: 'agents' | 'tabs',
    newConfig: any
  ): Promise<string[]> {
    const errors: string[] = [];

    try {
      if (configType === 'tabs') {
        // Validate that all referenced agents in tabs exist in agent configuration
        const currentAgentConfig = await this.getAgentsConfiguration();
        const availableAgentKeys = Object.keys(currentAgentConfig.agents);

        for (const [tabKey, tab] of Object.entries(newConfig.tabConfigurations)) {
          const tabData = tab as TabConfiguration;

          // Check defaultAgent
          if (tabData.defaultAgent && !availableAgentKeys.includes(tabData.defaultAgent)) {
            errors.push(`Tab ${tabKey} references non-existent default agent: ${tabData.defaultAgent}`);
          }

          // Check availableAgents
          if (tabData.availableAgents) {
            for (const agentKey of tabData.availableAgents) {
              if (!availableAgentKeys.includes(agentKey)) {
                errors.push(`Tab ${tabKey} references non-existent agent: ${agentKey}`);
              }
            }
          }
        }
      }

      // Additional cross-validation logic can be added here

    } catch (error) {
      console.error('❌ Error during cross-validation:', error);
      errors.push(`Cross-validation error: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }

    return errors;
  }

  // ========================================
  // Agent Management Operations (Task 5.3)
  // ========================================

  /**
   * Add a new agent with cross-configuration updates
   * Requirements: 3.4, 3.5
   */
  async addAgent(agent: DeployedAgent, agentKey: string): Promise<boolean> {
    try {

      // Validate agent data
      if (!this.validateAgentData(agent, agentKey)) {
        console.error('❌ Invalid agent data provided');
        return false;
      }

      // Get current agent configuration
      const currentAgentConfig = await this.getAgentsConfiguration();

      // Check if agent already exists
      if (currentAgentConfig.agents[agentKey]) {
        console.warn(`⚠️ Agent ${agentKey} already exists, use updateAgent instead`);
        return false;
      }

      // Add the new agent
      const updatedAgentConfig = {
        ...currentAgentConfig,
        agents: {
          ...currentAgentConfig.agents,
          [agentKey]: agent
        }
      };

      // Update agent configuration
      const success = await this.updateAgentConfiguration(updatedAgentConfig);

      if (success) {

        // Optionally update tab configurations to include the new agent
        await this.addAgentToRelevantTabs(agentKey, agent);

        return true;
      } else {
        console.error(`❌ Failed to add agent: ${agentKey}`);
        return false;
      }

    } catch (error) {
      console.error(`❌ Error adding agent ${agentKey}:`, error);
      return false;
    }
  }

  /**
   * Remove an agent with cleanup across configurations
   * Requirements: 3.4, 3.5
   */
  async removeAgent(agentKey: string): Promise<boolean> {
    try {

      // Get current configurations
      const [currentAgentConfig, currentTabConfig] = await Promise.all([
        this.getAgentsConfiguration(),
        this.getTabsConfiguration()
      ]);

      // Check if agent exists
      if (!currentAgentConfig.agents[agentKey]) {
        console.warn(`⚠️ Agent ${agentKey} does not exist`);
        return false;
      }

      // Remove agent from agents configuration
      const updatedAgentConfig = {
        ...currentAgentConfig,
        agents: { ...currentAgentConfig.agents }
      };
      delete updatedAgentConfig.agents[agentKey];

      // Update default agent if necessary
      if (currentAgentConfig.defaultAgent.agentType === agentKey) {
        const remainingAgentKeys = Object.keys(updatedAgentConfig.agents);
        if (remainingAgentKeys.length > 0) {
          const newDefaultAgent = updatedAgentConfig.agents[remainingAgentKeys[0]];
          updatedAgentConfig.defaultAgent = newDefaultAgent
        }
      }

      // Remove agent from tab configurations
      const updatedTabConfig = this.removeAgentFromTabs(currentTabConfig, agentKey, updatedAgentConfig);

      // Validate configurations before updating
      const agentValidation = await this.validateConfigurationUpdate('agents', updatedAgentConfig, false);
      const tabValidation = await this.validateConfigurationUpdate('tabs', updatedTabConfig, false);

      if (!agentValidation.isValid || !tabValidation.isValid) {
        console.error('❌ Configuration validation failed after agent removal');
        console.error('Agent validation errors:', agentValidation.errors);
        console.error('Tab validation errors:', tabValidation.errors);
        return false;
      }

      // Update both configurations
      const batchResult = await this.updateBothConfigurations(updatedAgentConfig, updatedTabConfig);

      if (batchResult.overallSuccess) {
        return true;
      } else {
        console.error(`❌ Failed to remove agent: ${agentKey}`);
        console.error('Batch update results:', batchResult);
        return false;
      }

    } catch (error) {
      console.error(`❌ Error removing agent ${agentKey}:`, error);
      return false;
    }
  }

  /**
   * Update an existing agent with validation
   */
  async updateAgent(agentKey: string, updatedAgent: DeployedAgent): Promise<boolean> {
    try {

      // Validate agent data
      if (!this.validateAgentData(updatedAgent, agentKey)) {
        console.error('❌ Invalid agent data provided');
        return false;
      }

      // Get current agent configuration
      const currentAgentConfig = await this.getAgentsConfiguration();

      // Check if agent exists
      if (!currentAgentConfig.agents[agentKey]) {
        console.warn(`⚠️ Agent ${agentKey} does not exist, use addAgent instead`);
        return false;
      }

      // Update the agent
      const updatedAgentConfig = {
        ...currentAgentConfig,
        agents: {
          ...currentAgentConfig.agents,
          [agentKey]: updatedAgent
        }
      };

      // Update default agent if it's the same agent
      if (currentAgentConfig.defaultAgent.agentType === agentKey) {
        updatedAgentConfig.defaultAgent = updatedAgent
      }

      // Update agent configuration
      const success = await this.updateAgentConfiguration(updatedAgentConfig);

      if (success) {
        return true;
      } else {
        console.error(`❌ Failed to update agent: ${agentKey}`);
        return false;
      }

    } catch (error) {
      console.error(`❌ Error updating agent ${agentKey}:`, error);
      return false;
    }
  }

  /**
   * Validate agent data structure and content
   */
  private validateAgentData(agent: DeployedAgent, agentKey: string): boolean {
    if (!agent || typeof agent !== 'object') {
      console.error(`❌ Agent data for ${agentKey} is not an object`);
      return false;
    }

    // Check required properties
    const requiredProperties = ['agentType', 'displayName', 'color', 'icon'];
    for (const prop of requiredProperties) {
      if (!agent[prop as keyof DeployedAgent] || typeof agent[prop as keyof DeployedAgent] !== 'string') {
        console.error(`❌ Agent ${agentKey} missing or invalid required property: ${prop}`);
        return false;
      }
    }

    // Validate agentType matches key (optional but recommended)
    if (agent.agentType !== agentKey) {
      console.warn(`⚠️ Agent ${agentKey} agentType (${agent.agentType}) does not match key`);
    }

    // Validate color format (basic hex color validation)
    const colorRegex = /^#([A-Fa-f0-9]{6}|[A-Fa-f0-9]{3})$/;
    if (!agent.color || !colorRegex.test(agent.color)) {
      console.error(`❌ Agent ${agentKey} has invalid color format: ${agent.color}`);
      return false;
    }

    // Validate alternativeNames if present
    if (agent.alternativeNames && !Array.isArray(agent.alternativeNames)) {
      console.error(`❌ Agent ${agentKey} alternativeNames is not an array`);
      return false;
    }

    // Validate description if present
    if (agent.description && typeof agent.description !== 'string') {
      console.error(`❌ Agent ${agentKey} description is not a string`);
      return false;
    }

    return true;
  }

  /**
   * Remove agent from all tab configurations
   */
  private removeAgentFromTabs(
    tabConfig: TabsConfiguration,
    agentKey: string,
    updatedAgentConfig: any
  ): TabsConfiguration {
    const updatedTabConfig = {
      tabConfigurations: { ...tabConfig.tabConfigurations }
    };

    const remainingAgentKeys = Object.keys(updatedAgentConfig.agents);
    const fallbackAgent = remainingAgentKeys.length > 0 ? remainingAgentKeys[0] : '';

    for (const [tabKey, tab] of Object.entries(updatedTabConfig.tabConfigurations)) {
      const updatedTab = { ...tab };

      // Remove from availableAgents
      if (updatedTab.availableAgents) {
        updatedTab.availableAgents = updatedTab.availableAgents.filter(a => a !== agentKey);
      }

      // Update defaultAgent if it was the removed agent
      if (updatedTab.defaultAgent === agentKey) {
        updatedTab.defaultAgent = fallbackAgent;
      }

      updatedTabConfig.tabConfigurations[tabKey] = updatedTab;
    }

    return updatedTabConfig;
  }

  /**
   * Add agent to relevant tabs (optional helper method)
   */
  private async addAgentToRelevantTabs(agentKey: string, agent: DeployedAgent): Promise<void> {
    try {

      // This is a placeholder for logic that determines which tabs should include the new agent
      // The actual implementation would depend on business rules
      // For now, we'll just log that the agent was added

    } catch (error) {
      console.error(`❌ Error adding agent ${agentKey} to tabs:`, error);
      // Don't throw error as this is optional functionality
    }
  }

  /**
   * Validate configuration consistency across agents and tabs
   * Requirements: 3.4, 3.5
   */
  async validateConfigurationConsistency(): Promise<{
    isConsistent: boolean;
    errors: string[];
    warnings: string[];
  }> {
    const errors: string[] = [];
    const warnings: string[] = [];

    try {

      // Get current configurations
      const [agentConfig, tabConfig] = await Promise.all([
        this.getAgentsConfiguration(),
        this.getTabsConfiguration()
      ]);

      const availableAgentKeys = Object.keys(agentConfig.agents);

      // Check each tab configuration
      for (const [tabKey, tab] of Object.entries(tabConfig.tabConfigurations)) {
        // Check defaultAgent
        if (tab.defaultAgent && !availableAgentKeys.includes(tab.defaultAgent)) {
          errors.push(`Tab ${tabKey} references non-existent default agent: ${tab.defaultAgent}`);
        }

        // Check availableAgents
        if (tab.availableAgents) {
          for (const agentKey of tab.availableAgents) {
            if (!availableAgentKeys.includes(agentKey)) {
              errors.push(`Tab ${tabKey} references non-existent agent: ${agentKey}`);
            }
          }

          // Check if defaultAgent is in availableAgents
          if (tab.defaultAgent && tab.availableAgents.length > 0 &&
            !tab.availableAgents.includes(tab.defaultAgent)) {
            warnings.push(`Tab ${tabKey} default agent ${tab.defaultAgent} is not in availableAgents list`);
          }
        }

        // Check for empty availableAgents
        if (tab.availableAgents && tab.availableAgents.length === 0) {
          warnings.push(`Tab ${tabKey} has empty availableAgents list`);
        }
      }

      // Check for unused agents (agents not referenced in any tab)
      const referencedAgents = new Set<string>();
      for (const tab of Object.values(tabConfig.tabConfigurations)) {
        if (tab.defaultAgent) {
          referencedAgents.add(tab.defaultAgent);
        }
        if (tab.availableAgents) {
          tab.availableAgents.forEach(agent => referencedAgents.add(agent));
        }
      }

      for (const agentKey of availableAgentKeys) {
        if (!referencedAgents.has(agentKey)) {
          warnings.push(`Agent ${agentKey} is not referenced in any tab configuration`);
        }
      }

      const isConsistent = errors.length === 0;

      if (isConsistent) {
        if (warnings.length > 0) {
          console.warn(`⚠️ Found ${warnings.length} warnings during consistency check`);
        }
      } else {
        console.error(`❌ Configuration consistency validation failed with ${errors.length} errors`);
      }

      return { isConsistent, errors, warnings };

    } catch (error) {
      console.error('❌ Error during configuration consistency validation:', error);
      errors.push(`Consistency validation error: ${error instanceof Error ? error.message : 'Unknown error'}`);
      return { isConsistent: false, errors, warnings };
    }
  }

  /**
   * Repair configuration inconsistencies automatically where possible
   */
  async repairConfigurationInconsistencies(): Promise<{
    repaired: boolean;
    repairsApplied: string[];
    remainingErrors: string[];
  }> {
    const repairsApplied: string[] = [];
    const remainingErrors: string[] = [];

    try {

      // First, validate to identify issues
      const validation = await this.validateConfigurationConsistency();

      if (validation.isConsistent) {
        return { repaired: true, repairsApplied: [], remainingErrors: [] };
      }

      // Get current configurations
      const [agentConfig, tabConfig] = await Promise.all([
        this.getAgentsConfiguration(),
        this.getTabsConfiguration()
      ]);

      const availableAgentKeys = Object.keys(agentConfig.agents);
      const updatedTabConfig = { ...tabConfig };
      let configChanged = false;

      // Repair tab configurations
      for (const [tabKey, tab] of Object.entries(updatedTabConfig.tabConfigurations)) {
        const updatedTab = { ...tab };

        // Fix defaultAgent if it doesn't exist
        if (updatedTab.defaultAgent && !availableAgentKeys.includes(updatedTab.defaultAgent)) {
          if (availableAgentKeys.length > 0) {
            const newDefaultAgent = availableAgentKeys[0];
            updatedTab.defaultAgent = newDefaultAgent;
            repairsApplied.push(`Fixed default agent for tab ${tabKey}: ${tab.defaultAgent} -> ${newDefaultAgent}`);
            configChanged = true;
          } else {
            updatedTab.defaultAgent = '';
            repairsApplied.push(`Cleared default agent for tab ${tabKey} (no agents available)`);
            configChanged = true;
          }
        }

        // Fix availableAgents
        if (updatedTab.availableAgents) {
          const originalLength = updatedTab.availableAgents.length;
          updatedTab.availableAgents = updatedTab.availableAgents.filter(agentKey =>
            availableAgentKeys.includes(agentKey)
          );

          if (updatedTab.availableAgents.length !== originalLength) {
            repairsApplied.push(`Cleaned up availableAgents for tab ${tabKey}`);
            configChanged = true;
          }

          // Ensure defaultAgent is in availableAgents if both exist
          if (updatedTab.defaultAgent && updatedTab.availableAgents.length > 0 &&
            !updatedTab.availableAgents.includes(updatedTab.defaultAgent)) {
            updatedTab.availableAgents.push(updatedTab.defaultAgent);
            repairsApplied.push(`Added default agent to availableAgents for tab ${tabKey}`);
            configChanged = true;
          }
        }

        updatedTabConfig.tabConfigurations[tabKey] = updatedTab;
      }

      // Apply repairs if any were made
      if (configChanged) {
        const updateSuccess = await this.updateTabConfiguration(updatedTabConfig);

        if (updateSuccess) {

          // Re-validate to check for remaining issues
          const postRepairValidation = await this.validateConfigurationConsistency();
          remainingErrors.push(...postRepairValidation.errors);

          return {
            repaired: postRepairValidation.isConsistent,
            repairsApplied,
            remainingErrors
          };
        } else {
          remainingErrors.push('Failed to apply configuration repairs');
          return { repaired: false, repairsApplied: [], remainingErrors };
        }
      } else {
        // No automatic repairs possible
        remainingErrors.push(...validation.errors);
        return { repaired: false, repairsApplied, remainingErrors };
      }

    } catch (error) {
      console.error('❌ Error during configuration repair:', error);
      remainingErrors.push(`Repair error: ${error instanceof Error ? error.message : 'Unknown error'}`);
      return { repaired: false, repairsApplied, remainingErrors };
    }
  }
}