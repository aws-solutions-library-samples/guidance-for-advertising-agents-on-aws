import { Injectable, OnInit } from '@angular/core';
import { BehaviorSubject } from 'rxjs';


// AWS Amplify v6 imports
import { Amplify } from 'aws-amplify';
import { signIn, signOut, getCurrentUser, updatePassword, confirmSignIn } from 'aws-amplify/auth';
import { fetchAuthSession } from 'aws-amplify/auth';

// AWS SDK imports for AppConfig
import { AppConfigClient, CreateHostedConfigurationVersionCommand, StartDeploymentCommand, ListDeploymentsCommand, GetDeploymentCommand, GetApplicationCommand, GetEnvironmentCommand, GetConfigurationProfileCommand } from '@aws-sdk/client-appconfig';
import { AppConfigDataClient, StartConfigurationSessionCommand, GetLatestConfigurationCommand } from '@aws-sdk/client-appconfigdata';
import { AgentsConfiguration, AwsConfig, CacheEntry, AgentConfig, AppConfigSettings } from '../models/application-models';

@Injectable({
  providedIn: 'root'
})
export class AwsConfigService implements OnInit {
  private configSubject = new BehaviorSubject<AwsConfig | null>(null);
  public config$ = this.configSubject.asObservable();

  private userSubject = new BehaviorSubject<any>(null);
  public user$ = this.userSubject.asObservable();
  public amplifyConfig: any | null = null;
  private agentsConfig: AgentsConfiguration | null = null;

  // AppConfig clients and cache
  private appConfigClient: AppConfigClient | null = null;
  private appConfigDataClient: AppConfigDataClient | null = null;
  private configCache = new Map<string, CacheEntry>();
  private readonly CACHE_TTL = 5 * 60 * 1000; // 5 minutes
  private readonly MAX_CACHE_SIZE = 50; // Prevent memory leaks
  private readonly MAX_RETRIES = 3;
  private readonly RETRY_DELAY = 1000; // 1 second
  
  // Auth session caching to prevent excessive fetchAuthSession calls
  private cachedAuthSession: { session: any; timestamp: number } | null = null;
  private readonly AUTH_SESSION_CACHE_TTL = 60 * 1000; // 1 minute cache for auth session
  private authSessionPromise: Promise<any> | null = null; // Prevent concurrent fetches
  
  // SSM data caching to prevent rate limiting
  private ssmDataCache: { data: any; timestamp: number } | null = null;
  private readonly SSM_CACHE_TTL = 10 * 60 * 1000; // 10 minutes for SSM data
  private ssmLoadPromise: Promise<void> | null = null; // Prevent concurrent SSM loads
  
  global_config: any={};
  constructor() {
    this.loadConfig().then((config) => {
    });
    // Start periodic cache cleanup
    this.startCacheCleanup();
  }

  ngOnInit() {

  }

  /**
   * Get cached auth session to prevent excessive fetchAuthSession calls
   * This method ensures only one fetch happens at a time and caches the result
   */
  public async getCachedAuthSession(): Promise<any> {
    // Check if we have a valid cached session
    if (this.cachedAuthSession) {
      const age = Date.now() - this.cachedAuthSession.timestamp;
      if (age < this.AUTH_SESSION_CACHE_TTL) {
        return this.cachedAuthSession.session;
      }
    }
    
    // If there's already a fetch in progress, wait for it
    if (this.authSessionPromise) {
      return this.authSessionPromise;
    }
    
    // Start a new fetch
    this.authSessionPromise = (async () => {
      try {
        const session = await fetchAuthSession();
        this.cachedAuthSession = {
          session,
          timestamp: Date.now()
        };
        return session;
      } finally {
        this.authSessionPromise = null;
      }
    })();
    
    return this.authSessionPromise;
  }

  /**
   * Invalidate the cached auth session (call on sign out)
   */
  public invalidateAuthSessionCache(): void {
    this.cachedAuthSession = null;
    this.authSessionPromise = null;
  }

  private async loadConfig(): Promise<void> {
    try {
      // Load config from Amplify environment variables or fallback to assets
      let config: AwsConfig;
      // Try to get config from Amplify environment first
      if (this.isAmplifyConfigured()) {
        config = this.getAmplifyConfig();
      } else {
        // Fallback to loading from assets
        const response = await fetch('/assets/aws-config.json');
        if (response.ok) {
          config = await response.json();
          this.amplifyConfig = config;
          
        } else {
          console.warn('AWS config not found. Using default configuration.');
          this.useDefaultConfig();
          return;
        }
      }
      // Configure Amplify v6 with the loaded config
      this.configureAmplify(config);

      // Set the basic config first so getConfig() works
      this.configSubject.next(config);

      // Check if user is already authenticated
      const user = await this.checkAuthState();
      
      // Only load AgentCore agents from SSM if user is already authenticated
      if (user) {
        await this.loadAgentCoreAgentsFromSSM(config);
        // Re-emit config after SSM data is loaded
        this.configSubject.next(config);
      }
      // Check if user is already authenticated
      this.userSubject.next(this.checkAuthState());
      return;
    } catch (error) {
      console.error('Error loading AWS config:', error);
      this.useDefaultConfig();
    }
  }

  /**
   * Load AgentCore agents from SSM Parameter Store and merge with config
   * Uses caching to prevent rate limiting from excessive SSM calls
   */
  private async loadAgentCoreAgentsFromSSM(config: AwsConfig): Promise<void> {
    // Check if we have cached SSM data
    if (this.ssmDataCache) {
      const age = Date.now() - this.ssmDataCache.timestamp;
      if (age < this.SSM_CACHE_TTL) {
        console.log('📦 Using cached SSM AgentCore data');
        this.mergeAgentCoreDataIntoConfig(config, this.ssmDataCache.data);
        return;
      }
    }
    
    // If there's already an SSM load in progress, wait for it
    if (this.ssmLoadPromise) {
      console.log('⏳ SSM load already in progress, waiting...');
      await this.ssmLoadPromise;
      // After waiting, the cache should be populated
      if (this.ssmDataCache) {
        this.mergeAgentCoreDataIntoConfig(config, this.ssmDataCache.data);
      }
      return;
    }
    
    // Start a new SSM load
    this.ssmLoadPromise = this.loadAgentCoreAgentsFromSSMInternal(config);
    
    try {
      await this.ssmLoadPromise;
    } finally {
      this.ssmLoadPromise = null;
    }
  }
  
  /**
   * Internal method to actually load from SSM (called only when cache is invalid)
   */
  private async loadAgentCoreAgentsFromSSMInternal(config: AwsConfig): Promise<void> {
    try {
      console.log('🔍 Loading AgentCore agents from SSM Parameter Store...');

      // Get AWS credentials using cached session
      const session = await this.getCachedAuthSession();
      if (!session.credentials) {
        console.warn('⚠️  No credentials available, skipping AgentCore SSM load');
        return;
      }

      // Import SSM client
      const { SSMClient, GetParameterCommand } = await import('@aws-sdk/client-ssm');

      const ssmClient = new SSMClient({
        region: config.aws.region,
        credentials: session.credentials
      });

      // Build parameter name
      const parameterName = `/${config.stackPrefix}/agentcore_values/${config.uniqueId}`;
      console.log(`📋 Retrieving parameter: ${parameterName}`);

      try {
        const command = new GetParameterCommand({
          Name: parameterName,
          WithDecryption: true
        });

        const response = await ssmClient.send(command);

        if (response.Parameter?.Value) {
          const agentcoreData = JSON.parse(response.Parameter.Value);
          console.log(`✅ Found ${agentcoreData.agents?.length || 0} AgentCore agents in SSM`);
          
          // Cache the SSM data
          this.ssmDataCache = {
            data: agentcoreData,
            timestamp: Date.now()
          };

          // Merge AgentCore agents into config.bedrock.allAgents
          if (agentcoreData.agents && Array.isArray(agentcoreData.agents) && agentcoreData.agents.length > 0) {
            let agentcoreAgent = agentcoreData.agents[0]
            const response = await fetch('/assets/global_configuration.json');
            if (response.ok) {
              this.global_config = await response.json();
              //console.log(this.global_config)
            } else {
              console.warn('AWS config not found. Using default configuration.');
              this.useDefaultConfig();
              return;
            }
            
            //console.log(this.global_config);
            // for agentcore agents, we are loading the agent instructions at runtime based on the agent that is mentioned by the user. The global_config.agent_configs JSON keeps track of the orchestration teams. We will load an agent for each team.

            // Check if this agent is already in allAgents (avoid duplicates)
            Object.keys(this.global_config.agent_configs).forEach((agentName) => {
              const existingIndex = config.bedrock.allAgents.findIndex(
                a => (a.name.indexOf(agentName)>-1||agentName.indexOf(a.name)>-1)
              );
              console.log(agentName + " added to agents list")
              const agentEntry = {
                name: agentName,
                agentType: agentName,
                status: 'active',
                id: agentcoreAgent.runtime_arn || '',
                aliasId: '',
                displayName: agentName,
                icon: 'smart_toy',
                color: this.global_config.configured_colors[agentName],
                deploymentType: 'agentcore',
                serviceName: agentName,
                runtimeArn: agentcoreAgent.runtime_arn || '',
                runtimeId: agentcoreAgent.runtime_arn?.split('/').pop() || '',
                collaborators:this.global_config.agent_configs[agentName].tool_agent_names
              };

              if (existingIndex >= 0) {
                // Update existing entry
                config.bedrock.allAgents[existingIndex] = agentEntry;
                console.log(`  ✅ Updated AgentCore agent: ${agentName}`);
              } else {
                // Add new entry
                config.bedrock.allAgents.push(agentEntry);
                console.log(`  ✅ Added AgentCore agent: ${agentName}`);
              }
            });
            // for (const agentcoreAgent of agentcoreData.agents) {
            //   // Check if this agent is already in allAgents (avoid duplicates)
            //   const existingIndex = config.bedrock.allAgents.findIndex(
            //     a => a.name === agentcoreAgent.name || a.runtimeArn === agentcoreAgent.runtime_arn
            //   );

            //   const agentEntry = {
            //     name: agentcoreAgent.name,
            //     agentType: agentcoreAgent.name,
            //     status: 'active',
            //     id: agentcoreAgent.runtime_arn || '',
            //     aliasId: '',
            //     displayName: agentcoreAgent.name,
            //     icon: 'smart_toy',
            //     color: '#6B46C1',
            //     deploymentType: 'agentcore',
            //     serviceName: agentcoreAgent.name,
            //     runtimeArn: agentcoreAgent.runtime_arn || '',
            //     runtimeId: agentcoreAgent.runtime_arn?.split('/').pop() || ''
            //   };

            //   if (existingIndex >= 0) {
            //     // Update existing entry
            //     config.bedrock.allAgents[existingIndex] = agentEntry;
            //     console.log(`  ✅ Updated AgentCore agent: ${agentcoreAgent.name}`);
            //   } else {
            //     // Add new entry
            //     config.bedrock.allAgents.push(agentEntry);
            //     console.log(`  ✅ Added AgentCore agent: ${agentcoreAgent.name}`);
            //   }
            // }
            return;
          }
        }
      } catch (ssmError: any) {
        if (ssmError.name === 'ParameterNotFound') {
          console.warn(`⚠️  SSM parameter not found: ${parameterName}`);
          console.warn('   AgentCore agents may not be deployed yet or SSM storage failed');
        } else {
          console.error('❌ Error retrieving AgentCore agents from SSM:', ssmError);
        }
      }
    } catch (error) {
      console.error('❌ Error loading AgentCore agents from SSM:', error);
      // Don't fail the entire config load if SSM retrieval fails
    }
  }

  /**
   * Merge cached AgentCore data into config (used when loading from cache)
   */
  private async mergeAgentCoreDataIntoConfig(config: AwsConfig, agentcoreData: any): Promise<void> {
    if (!agentcoreData?.agents || !Array.isArray(agentcoreData.agents) || agentcoreData.agents.length === 0) {
      return;
    }
    
    const agentcoreAgent = agentcoreData.agents[0];
    
    // Load global config if not already loaded
    if (!this.global_config || !this.global_config.agent_configs) {
      const response = await fetch('/assets/global_configuration.json');
      if (response.ok) {
        this.global_config = await response.json();
      } else {
        console.warn('Global config not found for merge');
        return;
      }
    }
    
    // Merge agents into config
    Object.keys(this.global_config.agent_configs).forEach((agentName) => {
      const existingIndex = config.bedrock.allAgents.findIndex(
        a => (a.name.indexOf(agentName) > -1 || agentName.indexOf(a.name) > -1)
      );
      
      const agentEntry = {
        name: agentName,
        agentType: agentName,
        status: 'active',
        id: agentcoreAgent.runtime_arn || '',
        aliasId: '',
        displayName: agentName,
        icon: 'smart_toy',
        color: this.global_config.configured_colors[agentName],
        deploymentType: 'agentcore',
        serviceName: agentName,
        runtimeArn: agentcoreAgent.runtime_arn || '',
        runtimeId: agentcoreAgent.runtime_arn?.split('/').pop() || '',
        collaborators: this.global_config.agent_configs[agentName].tool_agent_names
      };

      if (existingIndex >= 0) {
        config.bedrock.allAgents[existingIndex] = agentEntry;
      } else {
        config.bedrock.allAgents.push(agentEntry);
      }
    });
    return;
  }

  private isAmplifyConfigured(): boolean {
    // Check if we're running in an Amplify environment with proper env vars
    const envVars = (window as any).awsConfig;
    return envVars && envVars.cognito && envVars.bedrock;
  }

  public getAmplifyConfig(): AwsConfig {
    // Get configuration from Amplify environment variables
    if (!this.amplifyConfig) {
      this.loadConfig();
    }
    const envVars = this.amplifyConfig;
    return {
      aws: {
        region: envVars.region || 'us-east-1',
        cognito: {
          userPoolId: envVars.cognito.userPoolId,
          userPoolWebClientId: envVars.cognito.userPoolWebClientId,
          identityPoolId: envVars.cognito.identityPoolId,
          mandatorySignIn: true
        }
      },
      bedrock: {
        allAgents: envVars.bedrock.allAgents || [],
        stackPrefix: envVars.bedrock.stackPrefix || 'sim',
        stackSuffix: envVars.bedrock.stackSuffix || '1234',
        creativesDynamoDBTable: envVars.bedrock.creativesDynamoDBTable || ''
      },
      ui: {
        bucketName: envVars.ui?.bucketName || '',
        cloudFrontDistributionId: envVars.ui?.cloudFrontDistributionId || ''
      },
      appSyncEvents: {
        apiId: envVars.appSyncEvents?.apiId || '',
        eventsEndpoint: envVars.appSyncEvents?.eventsEndpoint || '',
        realtimeEndpoint: envVars.appSyncEvents?.realtimeEndpoint || ''
      },
      stackPrefix: envVars.stackPrefix || 'sim',
      stackSuffix: envVars.stackSuffix || '1234',
      memoryRecordId: 'simmemory1234',
      uniqueId: envVars.uniqueId || envVars.stackSuffix || '1234',
      creativesBucket: envVars.creativesBucket || '',
      creativesDynamoDBTable: envVars.creativesDynamoDBTable || ''
    };
  }

  private configureAmplify(config: AwsConfig): void {
    Amplify.configure({
      Auth: {
        Cognito: {
          userPoolId: config.aws.cognito.userPoolId,
          userPoolClientId: config.aws.cognito.userPoolWebClientId,
          identityPoolId: config.aws.cognito.identityPoolId,
          loginWith: {
            email: true
          },
          signUpVerificationMethod: 'code',
          userAttributes: {
            email: {
              required: true
            }
          },
          allowGuestAccess: false,
          passwordFormat: {
            minLength: 8,
            requireLowercase: true,
            requireUppercase: true,
            requireNumbers: true,
            requireSpecialCharacters: true
          }
        }
      }
    }, {
      ssr: false
    });
  }

  private useDefaultConfig(): void {
    // Default configuration for development (no hardcoded credentials)
    const defaultConfig: AwsConfig = {
      aws: {
        region: 'us-east-1',
        cognito: {
          userPoolId: '',
          userPoolWebClientId: '',
          identityPoolId: '',
          mandatorySignIn: true
        }
      },
      bedrock: {
        allAgents: [],
        stackPrefix: 'sim',
        stackSuffix: '1234',
        creativesDynamoDBTable: ''
      },
      ui: {
        bucketName: '',
        cloudFrontDistributionId: ''
      },
      appSyncEvents: {
        apiId: '',
        eventsEndpoint: '',
        realtimeEndpoint: ''
      },
      stackPrefix: 'sim',
      stackSuffix: '1234',
      memoryRecordId: 'simmemory1234',
      creativesBucket: '',
      creativesDynamoDBTable: '',
      uniqueId: ''
    };

    this.configSubject.next(defaultConfig);
  }

  private async checkAuthState(): Promise<any> {
    try {
      const user = await getCurrentUser();
      this.userSubject.next(user);
      return user;
    } catch (error) {
      this.userSubject.next(null);
      return null;
    }
  }

  /**
   * Check if cached data is still valid based on TTL
   */
  private isCacheValid(cacheEntry: CacheEntry): boolean {
    const now = Date.now();
    return (now - cacheEntry.timestamp) < cacheEntry.ttl;
  }

  /**
   * Get data from cache if valid, otherwise return null
   */
  private getCachedData(cacheKey: string): any | null {
    const cached = this.configCache.get(cacheKey);

    if (!cached) {
      return null;
    }

    if (this.isCacheValid(cached)) {
      return cached.data;
    }

    // Remove expired cache entry
    this.configCache.delete(cacheKey);
    return null;
  }

  /**
   * Store data in cache with TTL and optional session token
   */
  private setCachedData(cacheKey: string, data: any, sessionToken?: string): void {
    // Check cache size and clean up if necessary
    if (this.configCache.size >= this.MAX_CACHE_SIZE) {
      this.cleanupExpiredCache();

      // If still at max size, remove oldest entries
      if (this.configCache.size >= this.MAX_CACHE_SIZE) {
        const oldestKey = this.configCache.keys().next().value;
        if (oldestKey) {
          this.configCache.delete(oldestKey);
        }
      }
    }

    const cacheEntry: CacheEntry = {
      data,
      timestamp: Date.now(),
      ttl: this.CACHE_TTL,
      sessionToken
    };

    this.configCache.set(cacheKey, cacheEntry);
  }

  /**
   * Invalidate cache entry for a specific key
   */
  private invalidateCache(cacheKey: string): void {
    if (this.configCache.has(cacheKey)) {
      this.configCache.delete(cacheKey);
    }
  }

  /**
   * Invalidate all cache entries (useful for logout or major config changes)
   */
  private invalidateAllCache(): void {
    const cacheSize = this.configCache.size;
    this.configCache.clear();
  }

  /**
   * Get cache statistics for debugging
   */
  public getCacheStats(): { totalEntries: number; validEntries: number; expiredEntries: number } {
    const now = Date.now();
    let validEntries = 0;
    let expiredEntries = 0;

    this.configCache.forEach((entry) => {
      if (this.isCacheValid(entry)) {
        validEntries++;
      } else {
        expiredEntries++;
      }
    });

    return {
      totalEntries: this.configCache.size,
      validEntries,
      expiredEntries
    };
  }

  /**
   * Clean up expired cache entries
   */
  private cleanupExpiredCache(): void {
    const keysToDelete: string[] = [];

    this.configCache.forEach((entry, key) => {
      if (!this.isCacheValid(entry)) {
        keysToDelete.push(key);
      }
    });

    keysToDelete.forEach(key => {
      this.configCache.delete(key);
    });

    if (keysToDelete.length > 0) {
    }
  }

  /**
   * Start periodic cache cleanup
   */
  private startCacheCleanup(): void {
    // Clean up expired entries every 10 minutes
    setInterval(() => {
      this.cleanupExpiredCache();
    }, 10 * 60 * 1000);
  }

  /**
   * Get configuration data from AppConfig with fallback (used during initialization)
   */
  private async getAppConfigDataWithConfig(profileType: keyof AppConfigSettings['profiles'], appConfigSettings: AppConfigSettings): Promise<any> {
    try {
      const profileId = appConfigSettings.profiles[profileType];
      if (!profileId) {
        throw new Error(`Profile ID not found for ${profileType}`);
      }

      // Skip cache for UI and agents configs - always fetch fresh data
      if (profileType === 'uiSettings' || profileType === 'agents') {
      }

      // Retrieve configuration using retry mechanism
      const configData = this.getAppConfig(appConfigSettings.applicationId, appConfigSettings.environmentId, profileId, appConfigSettings.region);

      return configData;

    } catch (error) {
      console.warn(`⚠️ Failed to retrieve AppConfig data for ${profileType}, falling back to static:`, error);

      // Fall back to static configuration
      return await this.getStaticConfigData(profileType);
    }
  }

  /**
   * Retrieve configuration data from AppConfig with fallback to static files
   * Implements error handling with graceful degradation
   */
  public async getAppConfigData(profileType: keyof AppConfigSettings['profiles']): Promise<any> {
    // Skip cache for UI and agents configs - always fetch fresh data
    if (profileType === 'uiSettings' || profileType === 'agents') {
    } else {
      // Use cache for other profile types (like tabs)
      const cacheKey = `appconfig-${profileType}`;
      const cachedData = this.getCachedData(cacheKey);
      if (cachedData) {
        return cachedData;
      }
    }

    try {
      const config = this.getConfig();
      if (!config?.appConfig) {
        throw new Error('AppConfig settings not found in configuration');
      }

      const profileId = config.appConfig.profiles[profileType];
      if (!profileId) {
        throw new Error(`Profile ID not found for ${profileType}`);
      }

      // Retrieve configuration using retry mechanism
      const configData = this.getAppConfig(config.appConfig?.applicationId, config.appConfig?.environmentId, profileId, config.aws?.region || config.appConfig?.region);

      // Only cache for non-UI/agents configs
      if (profileType !== 'uiSettings' && profileType !== 'agents') {
        const cacheKey = `appconfig-${profileType}`;
        this.setCachedData(cacheKey, configData);
      }

      return configData;

    } catch (error) {
      console.error(`❌ Error retrieving AppConfig data for ${profileType}:`, error);

      // Fall back to static files
      return await this.getStaticConfigData(profileType);
    }
  }

  async getAppConfig(applicationId: string, environmentId: string, profileId: string, region: string): Promise<any> {
    try {
      const session = await this.getCachedAuthSession();

      if (!session.credentials) {
        throw new Error('No valid credentials available for AppConfig');
      }

      // Validate parameters
      if (!applicationId || !environmentId || !profileId) {
        throw new Error(`Missing required AppConfig parameters: app=${applicationId}, env=${environmentId}, profile=${profileId}`);
      }

      const client = new AppConfigDataClient({
        region: region,
        credentials: session.credentials,
        maxAttempts: this.MAX_RETRIES,
        retryMode: 'adaptive' as const
      });

      // 1. Start a configuration session with required minimum poll interval
      const startSessionCommand = new StartConfigurationSessionCommand({
        ApplicationIdentifier: applicationId,
        EnvironmentIdentifier: environmentId,
        ConfigurationProfileIdentifier: profileId,
        RequiredMinimumPollIntervalInSeconds: 15 // This is required according to AWS docs
      });

      const sessionResponse = await client.send(startSessionCommand);

      if (!sessionResponse.InitialConfigurationToken) {
        throw new Error('Failed to get initial configuration token from session response');
      }

      // 2. Get the latest configuration using the token
      const getLatestCommand = new GetLatestConfigurationCommand({
        ConfigurationToken: sessionResponse.InitialConfigurationToken,
      });

      const configResponse = await client.send(getLatestCommand);

      if (configResponse.Configuration && configResponse.Configuration.length > 0) {
        const configJsonString = new TextDecoder().decode(configResponse.Configuration);

        try {
          const configurationData = JSON.parse(configJsonString);
          return configurationData;
        } catch (parseError) {
          console.error('❌ Failed to parse configuration JSON:', parseError);
          throw new Error(`Invalid JSON in configuration: ${parseError}`);
        }
      } else {

        // Check if there's actually a deployment for this configuration profile
        // This is where the "404: Deployment not found" error typically occurs
        try {
          // Use the management client to check if there are any deployments
          const managementClient = new AppConfigClient({
            region: region,
            credentials: session.credentials
          });

          const { ListDeploymentsCommand } = await import('@aws-sdk/client-appconfig');
          const listDeploymentsCommand = new ListDeploymentsCommand({
            ApplicationId: applicationId,
            EnvironmentId: environmentId
          });

          const deploymentsResponse = await managementClient.send(listDeploymentsCommand);
          const deployments = deploymentsResponse.Items || [];

          // Check if any deployment is for our configuration profile
          // Note: DeploymentSummary doesn't include ConfigurationProfileId, so we'll check if any deployments exist
          console.log(`📋 Deployments found:`, deployments.map(d => ({
            deploymentNumber: d.DeploymentNumber,
            state: d.State,
            startedAt: d.StartedAt
          })));

          if (deployments.length === 0) {
            console.warn(`⚠️ No deployments found for this environment`);
            console.warn(`This means no configurations have been deployed yet.`);
            return null; // Return null instead of throwing error
          }

          // If deployments exist but no data, it might be a timing issue or the specific profile hasn't been deployed

          if (configResponse.NextPollConfigurationToken) {
            const retryCommand = new GetLatestConfigurationCommand({
              ConfigurationToken: configResponse.NextPollConfigurationToken,
            });

            const retryResponse = await client.send(retryCommand);

            if (retryResponse.Configuration && retryResponse.Configuration.length > 0) {
              const configJsonString = new TextDecoder().decode(retryResponse.Configuration);
              const configurationData = JSON.parse(configJsonString);
              return configurationData;
            }
          }

        } catch (deploymentError) {
          console.warn("⚠️ Could not check deployments:", deploymentError);
        }

        // If still no data, return null (this might be expected behavior)

        return null;
      }
    } catch (error) {
      console.error("❌ Error retrieving AppConfig data:", error);

      // Log more details about the error
      if (error instanceof Error) {
        console.error("❌ Error details:", {
          name: error.name,
          message: error.message,
          stack: error.stack
        });

        // Handle specific error cases
        if (error.message.includes('404') || error.message.includes('Deployment not found')) {
          console.error("❌ This is likely because no configuration has been deployed to this profile yet");
          console.error("❌ Run the deployment script to upload configurations to AppConfig");
          return null; // Return null instead of throwing for 404 errors
        }
      }

      throw error; // Re-throw to allow caller to handle
    }
  }

  /**
   * Fetch configuration data from AppConfig using the data client
   */
  private async fetchAppConfigData(appConfigSettings: AppConfigSettings, profileId: string): Promise<any> {
    if (!this.appConfigDataClient) {
      const session = await this.getCachedAuthSession();
      this.appConfigDataClient = new AppConfigDataClient({
        region: appConfigSettings.region,
        credentials: session.credentials,
        maxAttempts: this.MAX_RETRIES,
        retryMode: 'adaptive' as const
      })
    }

    // Start configuration session
    const sessionCommand = new StartConfigurationSessionCommand({
      ApplicationIdentifier: appConfigSettings.applicationId,
      EnvironmentIdentifier: appConfigSettings.environmentId,
      ConfigurationProfileIdentifier: profileId,
      RequiredMinimumPollIntervalInSeconds: 15
    });

    let sessionResponse: any = undefined;
    try {
      sessionResponse = await this.appConfigDataClient.send(sessionCommand);
    }
    catch (error) {
    }

    if (!sessionResponse || !sessionResponse.InitialConfigurationToken) {
      throw new Error('Failed to start configuration session');
    }

    // Get the latest configuration
    const configCommand = new GetLatestConfigurationCommand({
      ConfigurationToken: sessionResponse.InitialConfigurationToken
    });

    const configResponse = await this.appConfigDataClient.send(configCommand);

    if (!configResponse.Configuration) {
      throw new Error('No configuration data received');
    }

    // Parse the configuration data
    const configData = JSON.parse(new TextDecoder().decode(configResponse.Configuration));

    return configData;
  }

  /**
   * Get configuration data from static files as fallback
   */
  private async getStaticConfigData(profileType: keyof AppConfigSettings['profiles']): Promise<any> {
    try {
      switch (profileType) {
        case 'uiSettings':
          // For UI settings, extract relevant parts from aws-config.json
          const response = await fetch('/assets/aws-config.json');
          if (response.ok) {
            const config = await response.json();
            return {
              stackPrefix: config.stackPrefix,
              uniqueId: config.uniqueId,
              region: config.aws?.region,
              features: {
                enableRealTimeUpdates: false, // Static fallback
                cacheTimeout: this.CACHE_TTL,
                fallbackToStatic: true
              }
            };
          }
          throw new Error('Failed to load aws-config.json');
        default:
          throw new Error(`Unknown profile type: ${profileType}`);
      }

    } catch (error) {
      console.error(`❌ Error loading static config for ${profileType}:`, error);

      // Return default/empty configuration as last resort
      return this.getDefaultConfigData(profileType);
    }
    return undefined;
  }

  /**
   * Get default configuration data as last resort
   */
  private getDefaultConfigData(profileType: keyof AppConfigSettings['profiles']): any {
    console.warn(`⚠️ Using default configuration for ${profileType}`);

    switch (profileType) {
      case 'agents':
        return {
          agents: {},
          defaultAgent: {
            agentType: 'default',
            displayName: 'Default Agent',
            color: '#6B46C1',
            icon: 'smart_toy'
          }
        };
      case 'tabs':
        return {
          tabConfigurations: {}
        };
      case 'uiSettings':
        return {
          stackPrefix: 'sim',
          uniqueId: '1234',
          region: 'us-east-1',
          features: {
            enableRealTimeUpdates: false,
            cacheTimeout: this.CACHE_TTL,
            fallbackToStatic: true
          }
        };
      default:
        return {};
    }
  }

  /**
   * Merge AppConfig data with static configuration
   * This allows for hybrid configurations where some data comes from AppConfig
   * and other data comes from static files
   */
  public async mergeConfigurations(baseConfig: AwsConfig, appConfigData: {
    agentsConfig?: any;
    tabsConfig?: any;
    uiSettings?: any;
  }): Promise<AwsConfig> {
    try {
      const mergedConfig = { ...baseConfig };

      // Merge agents configuration if available
      if (appConfigData.agentsConfig) {
        // Store agents config for later use
        this.agentsConfig = appConfigData.agentsConfig;
      }

      // Merge UI settings if available
      if (appConfigData.uiSettings) {
        mergedConfig.stackPrefix = appConfigData.uiSettings.stackPrefix || mergedConfig.stackPrefix;
        mergedConfig.uniqueId = appConfigData.uiSettings.uniqueId || mergedConfig.uniqueId;

        if (appConfigData.uiSettings.region) {
          mergedConfig.aws.region = appConfigData.uiSettings.region;
        }
      }

      return mergedConfig;

    } catch (error) {
      console.error('❌ Error merging configurations:', error);
      return baseConfig; // Return original config on error
    }
  }



  /**
   * Create a new hosted configuration version
   */
  private async createHostedConfigurationVersion(
    appConfigSettings: AppConfigSettings,
    profileId: string,
    content: string
  ): Promise<string> {
    if (!this.appConfigClient) {
      throw new Error('AppConfig client not initialized');
    }

    const command = new CreateHostedConfigurationVersionCommand({
      ApplicationId: appConfigSettings.applicationId,
      ConfigurationProfileId: profileId,
      ContentType: 'application/json',
      Content: new TextEncoder().encode(content),
      Description: `Updated via UI at ${new Date().toISOString()}`
    });

    const response = await this.appConfigClient.send(command);

    if (!response.VersionNumber) {
      throw new Error('Failed to create configuration version');
    }

    return response.VersionNumber.toString();
  }

  /**
   * Refresh configuration data by invalidating cache and reloading from AppConfig
   */
  public async refreshAppConfigData(profileType?: keyof AppConfigSettings['profiles']): Promise<void> {
    if (profileType) {
      // Refresh specific profile
      this.invalidateCache(`appconfig-${profileType}`);
      await this.getAppConfigData(profileType);
    } else {
      // Refresh all profiles
      const config = this.getConfig();
      if (config?.appConfig) {
        const profileTypes = Object.keys(config.appConfig.profiles) as Array<keyof AppConfigSettings['profiles']>;

        for (const type of profileTypes) {
          this.invalidateCache(`appconfig-${type}`);
        }

        // Reload all configurations
        await Promise.all(profileTypes.map(type => this.getAppConfigData(type)));
      }
    }
  }

  /**
   * Force reload agents configuration (useful when agents are updated)
   */
  public async reloadAgentsConfig(): Promise<void> {
    this.agentsConfig = null;
    await this.loadAgentsConfig();
  }

  /**
   * Validate AppConfig setup by checking application, environment, and profiles
   */
  public async validateAppConfigSetup(): Promise<{ success: boolean; validation: any }> {
    const config = this.getConfig();
    if (!config?.appConfig) {
      return {
        success: false,
        validation: { error: 'No AppConfig settings found' }
      };
    }

    try {
      const session = await this.getCachedAuthSession();
      if (!session.credentials) {
        return {
          success: false,
          validation: { error: 'No valid credentials available' }
        };
      }

      const client = new AppConfigClient({
        region: config.appConfig.region,
        credentials: session.credentials
      });

      const validation: any = {
        applicationId: config.appConfig.applicationId,
        environmentId: config.appConfig.environmentId,
        region: config.appConfig.region,
        profiles: config.appConfig.profiles
      };

      // Check if application exists
      try {
        const appCommand = new GetApplicationCommand({
          ApplicationId: config.appConfig.applicationId
        });
        const appResponse = await client.send(appCommand);
        validation.applicationExists = true;
        validation.applicationName = appResponse.Name;
      } catch (error) {
        validation.applicationExists = false;
        validation.applicationError = error instanceof Error ? error.message : 'Unknown error';
      }

      // Check if environment exists
      try {
        const envCommand = new GetEnvironmentCommand({
          ApplicationId: config.appConfig.applicationId,
          EnvironmentId: config.appConfig.environmentId
        });
        const envResponse = await client.send(envCommand);
        validation.environmentExists = true;
        validation.environmentName = envResponse.Name;
      } catch (error) {
        validation.environmentExists = false;
        validation.environmentError = error instanceof Error ? error.message : 'Unknown error';
      }

      // Check each configuration profile
      validation.profileValidation = {};
      for (const [profileType, profileId] of Object.entries(config.appConfig.profiles)) {
        try {
          const profileCommand = new GetConfigurationProfileCommand({
            ApplicationId: config.appConfig.applicationId,
            ConfigurationProfileId: profileId
          });
          const profileResponse = await client.send(profileCommand);
          validation.profileValidation[profileType] = {
            exists: true,
            name: profileResponse.Name,
            type: profileResponse.Type,
            locationUri: profileResponse.LocationUri
          };
        } catch (error) {
          validation.profileValidation[profileType] = {
            exists: false,
            error: error instanceof Error ? error.message : 'Unknown error'
          };
        }
      }

      return {
        success: true,
        validation
      };

    } catch (error) {
      return {
        success: false,
        validation: {
          error: error instanceof Error ? error.message : 'Unknown error'
        }
      };
    }
  }

  /**
   * Check AppConfig deployments to verify they exist
   */
  public async checkAppConfigDeployments(): Promise<{ success: boolean; deployments: any }> {
    const config = this.getConfig();
    if (!config?.appConfig) {
      return {
        success: false,
        deployments: { error: 'No AppConfig settings found' }
      };
    }

    try {
      const session = await this.getCachedAuthSession();
      if (!session.credentials) {
        return {
          success: false,
          deployments: { error: 'No valid credentials available' }
        };
      }

      const client = new AppConfigClient({
        region: config.appConfig.region,
        credentials: session.credentials
      });

      // List all deployments for this application and environment
      const listCommand = new ListDeploymentsCommand({
        ApplicationId: config.appConfig.applicationId,
        EnvironmentId: config.appConfig.environmentId
      });

      const deployments = await client.send(listCommand);

      const deploymentDetails: any = {
        applicationId: config.appConfig.applicationId,
        environmentId: config.appConfig.environmentId,
        totalDeployments: deployments.Items?.length || 0,
        deployments: []
      };

      // Get details for each deployment
      if (deployments.Items) {
        for (const deployment of deployments.Items) {
          try {
            const detailCommand = new GetDeploymentCommand({
              ApplicationId: config.appConfig.applicationId,
              EnvironmentId: config.appConfig.environmentId,
              DeploymentNumber: deployment.DeploymentNumber!
            });

            const detail = await client.send(detailCommand);
            deploymentDetails.deployments.push({
              deploymentNumber: deployment.DeploymentNumber,
              configurationProfileId: detail.ConfigurationProfileId,
              configurationVersion: detail.ConfigurationVersion,
              state: detail.State,
              deploymentStrategyId: detail.DeploymentStrategyId,
              startedAt: detail.StartedAt,
              completedAt: detail.CompletedAt
            });
          } catch (error) {
            console.error(`Error getting deployment ${deployment.DeploymentNumber} details:`, error);
          }
        }
      }

      return {
        success: true,
        deployments: deploymentDetails
      };

    } catch (error) {
      return {
        success: false,
        deployments: {
          error: error instanceof Error ? error.message : 'Unknown error'
        }
      };
    }
  }

  /**
   * Test AppConfig connectivity and configuration
   * This method helps debug AppConfig issues by testing each step
   */
  public async testAppConfigConnection(): Promise<{ success: boolean; details: any }> {
    const config = this.getConfig();
    if (!config?.appConfig) {
      return {
        success: false,
        details: { error: 'No AppConfig settings found in configuration' }
      };
    }

    const testResults: any = {
      configFound: true,
      applicationId: config.appConfig.applicationId,
      environmentId: config.appConfig.environmentId,
      region: config.appConfig.region,
      profiles: config.appConfig.profiles
    };

    try {
      const session = await this.getCachedAuthSession();
      testResults.credentialsAvailable = !!session.credentials;

      if (!session.credentials) {
        return {
          success: false,
          details: { ...testResults, error: 'No valid credentials available' }
        };
      }

      // Test each profile
      for (const [profileType, profileId] of Object.entries(config.appConfig.profiles)) {
        try {

          const client = new AppConfigDataClient({
            region: config.appConfig.region,
            credentials: session.credentials,
            maxAttempts: 1 // Single attempt for testing
          });

          // Test session creation
          const sessionCommand = new StartConfigurationSessionCommand({
            ApplicationIdentifier: config.appConfig.applicationId,
            EnvironmentIdentifier: config.appConfig.environmentId,
            ConfigurationProfileIdentifier: profileId,
            RequiredMinimumPollIntervalInSeconds: 15
          });

          const sessionResponse = await client.send(sessionCommand);

          testResults[`${profileType}_sessionSuccess`] = !!sessionResponse.InitialConfigurationToken;

          if (sessionResponse.InitialConfigurationToken) {
            // Test configuration retrieval
            const configCommand = new GetLatestConfigurationCommand({
              ConfigurationToken: sessionResponse.InitialConfigurationToken
            });

            const configResponse = await client.send(configCommand);
            testResults[`${profileType}_configAvailable`] = !!configResponse.Configuration;
            testResults[`${profileType}_configSize`] = configResponse.Configuration?.length || 0;
          }

        } catch (error) {
          testResults[`${profileType}_error`] = error instanceof Error ? error.message : 'Unknown error';
          console.error(`❌ Test failed for profile ${profileType}:`, error);
        }
      }

      return {
        success: true,
        details: testResults
      };

    } catch (error) {
      return {
        success: false,
        details: {
          ...testResults,
          error: error instanceof Error ? error.message : 'Unknown error'
        }
      };
    }
  }

  async signIn(email: string, password: string): Promise<any> {
    try {
      const result = await signIn({ username: email, password });

      // Handle new password required challenge
      if (result.nextStep?.signInStep === 'CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED') {
        return { challengeName: 'NEW_PASSWORD_REQUIRED', session: result };
      }

      // Get the user after successful sign in
      const user = await getCurrentUser();
      this.userSubject.next(user);
      
      // Load AgentCore agents from SSM now that user is authenticated
      const config = this.configSubject.value;
      if (config) {
        await this.loadAgentCoreAgentsFromSSM(config);
        // Re-emit config to trigger subscribers with updated agent data
        this.configSubject.next(config);
      }
      
      return user;
    } catch (error) {
      console.error('Sign in error:', error);
      throw error;
    }
  }

  async signOut(): Promise<void> {
    try {
      await signOut();
      this.userSubject.next(null);

      // Invalidate all cached AppConfig data on sign out
      this.invalidateAllCache();
      
      // Invalidate auth session cache
      this.invalidateAuthSessionCache();
      
      // Invalidate SSM cache
      this.ssmDataCache = null;
      this.ssmLoadPromise = null;

      // Reset AppConfig clients
      this.appConfigClient = null;
      this.appConfigDataClient = null;

    } catch (error) {
      console.error('Sign out error:', error);
      throw error;
    }
  }

  async changePassword(oldPassword: string, newPassword: string): Promise<void> {
    try {
      await updatePassword({ oldPassword, newPassword });
    } catch (error) {
      console.error('Change password error:', error);
      throw error;
    }
  }

  async completeNewPassword(session: any, newPassword: string): Promise<any> {
    try {
      const result = await confirmSignIn({ challengeResponse: newPassword });

      // Get the user after successful password change
      const user = await getCurrentUser();
      this.userSubject.next(user);
      
      // Load AgentCore agents from SSM now that user is authenticated
      const config = this.configSubject.value;
      if (config) {
        await this.loadAgentCoreAgentsFromSSM(config);
        // Re-emit config to trigger subscribers with updated agent data
        this.configSubject.next(config);
      }
      
      return user;
    } catch (error) {
      console.error('Complete new password error:', error);
      throw error;
    }
  }

  public getConfig(): AwsConfig | null {
    return this.configSubject.value;
  }

  getCurrentUser(): any {
    return this.userSubject.value;
  }

  isAuthenticated(): boolean {
    return this.userSubject.value !== null;
  }

  private async loadAgentsConfig(): Promise<void> {
    try {

      // Try to load from AppConfig first, then fall back to static files
      this.agentsConfig = await this.getAppConfigData('agents');

      if (this.agentsConfig) {
      } else {
        console.warn('❌ Agents config not found. Using fallback configuration.');
        this.agentsConfig = null;
      }
    } catch (error) {
      console.error('❌ Error loading agents config:', error);
      this.agentsConfig = null;
    }
  }
  normalizeAndCompare(agentName: string, agentType: string): boolean {
    if (!agentName || !agentType) return false;

    const normalizedAgentName = (agentName.indexOf('-') > 0 ? agentName.split('-')[1] : agentName)
      .toLowerCase().replace(/\s/g, '');
    const normalizedAgentType = (agentType.indexOf('-') > 0 ? agentType.split('-')[1] : agentType)
      .toLowerCase().replace(/\s/g, '');

    return normalizedAgentName === normalizedAgentType;
  }

  getAgentId(agentType: string) {
    const config = this.getConfig();
    let agent: any = config?.bedrock.allAgents[agentType];
    return agent?.id || agent?.runtimeId;
  }

  getAgentByOtherNames(agentName: string, defaultValue: any = {}) {
    const config = this.getConfig();
    if (!config?.bedrock?.allAgents || !agentName) return defaultValue;

    const normalizedSearch = agentName.toLowerCase().replace(/[-\s]/g, '');
    const agent = config.bedrock.allAgents.find(a => {
      if (!a.agentType || !a.name) return false;
      const normalizedAgentType = a.agentType.toLowerCase().replace(/[-\s]/g, '');
      const normalizedName = a.name.toLowerCase().replace(/[-\s]/g, '');
      return normalizedAgentType.includes(normalizedSearch) ||
        normalizedName.includes(normalizedSearch);
    });

    return agent || defaultValue;
  }

  getAgentNameById(agentId: string): string {
    const config = this.getConfig();
    const agent = config?.bedrock?.allAgents?.find(a => a.id === agentId);
    return agent?.name || 'Unknown Agent';
  }

  getAgentAliasId(agentType: string): string {
    const config = this.getConfig();
    const agent = config?.bedrock?.allAgents?.find(a => this.normalizeAndCompare(a.name, agentType));
    return agent?.aliasId || '';
  }

  getRegion(): string {
    const config = this.getConfig();
    return config?.aws.region || 'us-east-1';
  }

  async getAwsConfig() {
    try {
      const session = await this.getCachedAuthSession();
      if (!this.amplifyConfig) {
        this.loadConfig();
      }
      return {
        region: this.getRegion(),
        credentials: session.credentials,
        appsyncEvents: {
          eventsEndpoint: this.amplifyConfig?.appSyncEvents?.eventsEndpoint,
          realtimeEndpoint: this.amplifyConfig?.appSyncEvents?.realtimeEndpoint
        }
      };
    } catch (error) {
      console.error('Error getting AWS config:', error);
      return null;
    }
  }

  getStackPrefix(): string {
    const config = this.getConfig();
    return config?.stackPrefix || 'sim';
  }

  getMemoryRecordId(): string {
    const config = this.getConfig();
    return config?.memoryRecordId || '';
  }

  getStackSuffix(): string {
    // This matches the BucketSuffix parameter in CloudFormation templates
    // Default value is '1234' as defined in all CloudFormation templates
    const config = this.getConfig();
    return config?.stackSuffix || '1234';
  }

  getCreativesBucket(): string {
    const config = this.getConfig();
    return config?.creativesBucket || '';
  }

  // New methods for agent mention functionality
  getAvailableAgents(): Array<any> {
    // Use configurable agents if available
    if (this.agentsConfig) {
      return Object.entries(this.agentsConfig.agents).map(([key, agent]) => ({
        key,
        agentType: agent.agentType,
        displayName: agent.displayName,
        description: agent.description || `${agent.displayName} specialist`
      }));
    }
    return [];

  }

  getAgentByDisplayName(displayName: string, defaultValue: any = null): { key: string; displayName: string; description: string } | null {
    if (!displayName) return defaultValue;

    const agents = this.getAvailableAgents();
    if (!agents.length) return defaultValue;

    const result = agents.find(agent =>
      agent.displayName?.toLowerCase() === displayName.toLowerCase() ||
      agent.displayName?.toLowerCase().includes(displayName.toLowerCase())
    ) || defaultValue;

    return result;
  }

  getAgentDisplayNameByAgentType(agentType: string): string {
    if (!agentType) return 'Outcomes Simulator Agent';

    const agents = this.getAvailableAgents();
    if (!agents.length) return 'Outcomes Simulator Agent';

    const result = agents.find(agent =>
      agent.agentType?.toLowerCase() === agentType.toLowerCase() ||
      agent.agentType?.toLowerCase().includes(agentType.toLowerCase())
    ) || null;

    return result ? result.displayName : 'Outcomes Simulator Agent';
  }

  getAgentAliasIdForAgent(agentType: string): string {
    const config = this.getConfig();
    const agent = config?.bedrock.allAgents?.find(a => this.normalizeAndCompare(a.name, agentType));
    return agent?.aliasId || 'latest';
  }

  // Get all agents from the agents-config.json
  getAllAgents(): Array<{ agentType: string; displayName: string; color: string; icon: string; description: string }> {
    if (this.agentsConfig) {
      return Object.values(this.agentsConfig.agents).map(agent => ({
        agentType: agent.agentType,
        displayName: agent.displayName,
        color: agent.color,
        icon: agent.icon,
        description: agent.description || `${agent.displayName} specialist`
      }));
    }
    return [];
  }

  /* // Agent color management methods (moved from GenericTabComponent)
  getAgentColor(agentType: string): string {
    // First try to get color from agents config
    const agentConfig = this.getAgentByOtherNames(agentType);
    if (agentConfig && agentConfig.color) {
      return agentConfig.color;
    }

    // Fallback to dynamic color assignment
    // Check if this agent already has a color assigned
    if (AwsConfigService.colorMap.has(agentType)) {
      return AwsConfigService.colorMap.get(agentType)!;
    }

    // Assign a new color from the palette
    const color = AwsConfigService.colors[AwsConfigService.colorIndex % AwsConfigService.colors.length];
    AwsConfigService.colorMap.set(agentType, color);
    AwsConfigService.colorIndex++;

    return color;
  } */

  // Reset color assignments (useful for testing or when switching contexts)
  /*resetAgentColors(): void {
    AwsConfigService.colorMap.clear();
    AwsConfigService.colorIndex = 0;
  }*/

  // Get all currently assigned colors (useful for debugging)
  /*getAssignedColors(): Map<string, string> {
    return new Map(AwsConfigService.colorMap);
  }*/

  /**
   * Debug method to test AppConfig from browser console
   * Usage: window.awsConfigService.debugAppConfig()
   */
  public async debugAppConfig(): Promise<void> {

    // Validate AppConfig setup first
    const setupValidation = await this.validateAppConfigSetup();

    // Test basic connectivity
    const connectionTest = await this.testAppConfigConnection();

    // Check deployments
    const deploymentCheck = await this.checkAppConfigDeployments();

    // Try to get each configuration
    const config = this.getConfig();
    if (config?.appConfig) {
      for (const [profileType, profileId] of Object.entries(config.appConfig.profiles)) {
        try {
          const data = await this.getAppConfig(
            config.appConfig.applicationId,
            config.appConfig.environmentId,
            profileId,
            config.appConfig.region
          );
        } catch (error) {
          console.error(`❌ ${profileType} failed:`, error);
        }
      }
    }

  }
} 