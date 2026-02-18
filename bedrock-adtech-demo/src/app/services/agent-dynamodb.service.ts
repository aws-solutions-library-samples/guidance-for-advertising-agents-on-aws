import { Injectable } from '@angular/core';
import { AwsConfigService } from './aws-config.service';
import { DynamoDBClient, GetItemCommand, PutItemCommand, DeleteItemCommand, QueryCommand } from '@aws-sdk/client-dynamodb';
import { marshall, unmarshall } from '@aws-sdk/util-dynamodb';

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
 * Agent configuration interface matching the DynamoDB schema
 * Validates: Requirements 3.2, 4.2
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
}

/**
 * Global configuration interface for DynamoDB storage
 * Validates: Requirements 6.7, 6.8
 */
export interface GlobalConfiguration {
  knowledge_bases: Record<string, string>;
  configured_colors: Record<string, string>;
  agent_configs: Record<string, AgentConfiguration>;
}

/**
 * Cache entry for DynamoDB data
 */
interface CacheEntry<T> {
  data: T;
  timestamp: number;
  ttl: number;
}

/**
 * Operation result wrapper for error handling
 * Validates: Requirements 6.4, 9.3
 */
export interface OperationResult<T> {
  success: boolean;
  data?: T;
  error?: {
    code: string;
    message: string;
    details?: Record<string, string>;
    retryable: boolean;
  };
}

/**
 * AgentDynamoDBService - Service for DynamoDB operations specific to agent configurations
 * 
 * This service handles all DynamoDB interactions for the agent management UI,
 * including CRUD operations for agents, instructions, and global configuration.
 * 
 * Validates: Requirements 6.1, 6.2, 6.3, 6.6, 6.7, 6.8
 */
@Injectable({
  providedIn: 'root'
})
export class AgentDynamoDBService {
  private dynamoDBClient: DynamoDBClient | null = null;
  private tableName: string | null = null;
  private region: string = 'us-east-1';
  
  // Cache configuration
  private readonly CACHE_TTL = 5 * 60 * 1000; // 5 minutes
  private globalConfigCache: CacheEntry<GlobalConfiguration> | null = null;
  private instructionsCache = new Map<string, CacheEntry<string>>();
  
  // Retry configuration
  private readonly MAX_RETRIES = 3;

  constructor(private awsConfigService: AwsConfigService) {
    // Initialize will be called lazily when needed
  }

  /**
   * Initialize DynamoDB client with Cognito credentials
   * Validates: Requirements 6.3
   */
  private async initializeClient(): Promise<boolean> {
    try {
      // Get AWS config to check for agentConfigTable
      const config = this.awsConfigService.getConfig();
      
      // Check if agentConfigTable is configured
      const agentConfigTable = (config as any)?.agentConfigTable;
      if (!agentConfigTable?.tableName) {
        console.warn('⚠️ AgentDynamoDBService: agentConfigTable not configured in aws-config.json');
        return false;
      }
      
      this.tableName = agentConfigTable.tableName;
      this.region = agentConfigTable.region || config?.aws?.region || 'us-east-1';
      
      // Get Cognito credentials using cached auth session
      const session = await this.awsConfigService.getCachedAuthSession();
      
      if (!session?.credentials) {
        console.warn('⚠️ AgentDynamoDBService: No valid credentials available');
        return false;
      }
      
      // Initialize DynamoDB client with Cognito credentials
      this.dynamoDBClient = new DynamoDBClient({
        region: this.region,
        credentials: session.credentials,
        maxAttempts: this.MAX_RETRIES
      });
      
      console.log(`✅ AgentDynamoDBService: Initialized with table ${this.tableName} in ${this.region}`);
      return true;
    } catch (error) {
      console.error('❌ AgentDynamoDBService: Failed to initialize client:', error);
      return false;
    }
  }

  /**
   * Ensure client is initialized before operations
   */
  private async ensureClient(): Promise<boolean> {
    if (this.dynamoDBClient && this.tableName) {
      return true;
    }
    return await this.initializeClient();
  }

  /**
   * Check if cache entry is still valid
   */
  private isCacheValid<T>(cache: CacheEntry<T> | null): boolean {
    if (!cache) return false;
    return (Date.now() - cache.timestamp) < cache.ttl;
  }

  // ============================================
  // Global Config Operations
  // ============================================

  /**
   * Get global configuration from DynamoDB
   * Validates: Requirements 6.7, 6.8
   */
  async getGlobalConfig(): Promise<GlobalConfiguration | null> {
    // Check cache first
    if (this.isCacheValid(this.globalConfigCache)) {
      console.log('📦 AgentDynamoDBService: Using cached global config');
      return this.globalConfigCache!.data;
    }
    
    if (!await this.ensureClient()) {
      return null;
    }
    
    try {
      const command = new GetItemCommand({
        TableName: this.tableName!,
        Key: marshall({
          pk: 'GLOBAL_CONFIG',
          sk: 'v1'
        })
      });
      
      const response = await this.dynamoDBClient!.send(command);
      
      if (!response.Item) {
        console.warn('⚠️ AgentDynamoDBService: No global config found in DynamoDB');
        return null;
      }
      
      const item = unmarshall(response.Item);
      const globalConfig: GlobalConfiguration = JSON.parse(item['content'] as string);
      
      // Update cache
      this.globalConfigCache = {
        data: globalConfig,
        timestamp: Date.now(),
        ttl: this.CACHE_TTL
      };
      
      console.log('✅ AgentDynamoDBService: Loaded global config from DynamoDB');
      return globalConfig;
    } catch (error) {
      console.error('❌ AgentDynamoDBService: Failed to get global config:', error);
      return null;
    }
  }

  /**
   * Save global configuration to DynamoDB
   * Validates: Requirements 6.2, 6.6
   */
  async saveGlobalConfig(config: GlobalConfiguration): Promise<boolean> {
    if (!await this.ensureClient()) {
      return false;
    }
    
    try {
      const command = new PutItemCommand({
        TableName: this.tableName!,
        Item: marshall({
          pk: 'GLOBAL_CONFIG',
          sk: 'v1',
          config_type: 'global_config',
          content: JSON.stringify(config),
          updated_at: new Date().toISOString()
        })
      });
      
      await this.dynamoDBClient!.send(command);
      
      // Update cache
      this.globalConfigCache = {
        data: config,
        timestamp: Date.now(),
        ttl: this.CACHE_TTL
      };
      
      console.log('✅ AgentDynamoDBService: Saved global config to DynamoDB');
      return true;
    } catch (error) {
      console.error('❌ AgentDynamoDBService: Failed to save global config:', error);
      return false;
    }
  }

  /**
   * Clear all cached data to force fresh loads from DynamoDB
   * Call this after saving/updating/deleting agents
   */
  clearCache(): void {
    this.globalConfigCache = null;
    this.instructionsCache.clear();
    console.log('🗑️ AgentDynamoDBService: Cache cleared');
  }

  /**
   * Merge file configuration with existing DynamoDB configuration
   * Validates: Requirements 11.2, 11.4, 11.5
   */
  async mergeGlobalConfig(fileConfig: GlobalConfiguration): Promise<GlobalConfiguration> {
    const existingConfig = await this.getGlobalConfig();
    
    if (!existingConfig) {
      // No existing config, use file config directly
      return fileConfig;
    }
    
    // Merge configurations - preserve existing, add new
    const mergedConfig: GlobalConfiguration = {
      knowledge_bases: {
        ...fileConfig.knowledge_bases,
        ...existingConfig.knowledge_bases // Existing takes precedence
      },
      configured_colors: {
        ...fileConfig.configured_colors,
        ...existingConfig.configured_colors // Existing takes precedence
      },
      agent_configs: {
        ...fileConfig.agent_configs,
        ...existingConfig.agent_configs // Existing takes precedence
      }
    };
    
    return mergedConfig;
  }

  // ============================================
  // Agent CRUD Operations
  // ============================================

  /**
   * Get all agents from global configuration
   * Validates: Requirements 6.1
   */
  async getAllAgents(): Promise<AgentConfiguration[]> {
    const globalConfig = await this.getGlobalConfig();
    
    if (!globalConfig?.agent_configs) {
      return [];
    }
    
    return Object.values(globalConfig.agent_configs);
  }

  /**
   * Get a specific agent by name
   */
  async getAgent(agentName: string): Promise<AgentConfiguration | null> {
    const globalConfig = await this.getGlobalConfig();
    
    if (!globalConfig?.agent_configs) {
      return null;
    }
    
    return globalConfig.agent_configs[agentName] || null;
  }

  /**
   * Get agent instructions from DynamoDB
   * Validates: Requirements 7.2
   */
  async getAgentInstructions(agentName: string): Promise<string | null> {
    // Check cache first
    const cached = this.instructionsCache.get(agentName);
    if (this.isCacheValid(cached || null)) {
      console.log(`📦 AgentDynamoDBService: Using cached instructions for ${agentName}`);
      return cached!.data;
    }
    
    if (!await this.ensureClient()) {
      return null;
    }
    
    try {
      const command = new GetItemCommand({
        TableName: this.tableName!,
        Key: marshall({
          pk: `INSTRUCTION#${agentName}`,
          sk: 'v1'
        })
      });
      
      const response = await this.dynamoDBClient!.send(command);
      
      if (!response.Item) {
        console.warn(`⚠️ AgentDynamoDBService: No instructions found for ${agentName}`);
        return null;
      }
      
      const item = unmarshall(response.Item);
      const instructions = item['content'] as string;
      
      // Update cache
      this.instructionsCache.set(agentName, {
        data: instructions,
        timestamp: Date.now(),
        ttl: this.CACHE_TTL
      });
      
      console.log(`✅ AgentDynamoDBService: Loaded instructions for ${agentName}`);
      return instructions;
    } catch (error) {
      console.error(`❌ AgentDynamoDBService: Failed to get instructions for ${agentName}:`, error);
      return null;
    }
  }

  /**
   * Save agent configuration to DynamoDB
   * Updates both the global config and instruction record
   * Validates: Requirements 3.4, 4.5, 6.2, 6.6
   */
  async saveAgent(agent: AgentConfiguration): Promise<boolean> {
    if (!await this.ensureClient()) {
      return false;
    }
    
    try {
      // Get current global config
      const globalConfig = await this.getGlobalConfig();
      
      if (!globalConfig) {
        console.error('❌ AgentDynamoDBService: Cannot save agent - no global config found');
        return false;
      }
      
      // Update agent in global config
      globalConfig.agent_configs[agent.agent_name] = agent;
      
      // Sync color to configured_colors so it's picked up by agent-config.service
      if (agent.color) {
        if (!globalConfig.configured_colors) {
          globalConfig.configured_colors = {};
        }
        globalConfig.configured_colors[agent.agent_name] = agent.color;
      }
      
      // Sync knowledge_base to knowledge_bases map
      if (!globalConfig.knowledge_bases) {
        globalConfig.knowledge_bases = {};
      }
      if (agent.knowledge_base) {
        globalConfig.knowledge_bases[agent.agent_name] = agent.knowledge_base;
      } else {
        // Remove entry if knowledge_base was cleared
        delete globalConfig.knowledge_bases[agent.agent_name];
      }
      
      // Save updated global config
      const saved = await this.saveGlobalConfig(globalConfig);
      
      if (!saved) {
        return false;
      }
      
      // If agent has instructions, save them separately
      if (agent.instructions) {
        await this.saveAgentInstructions(agent.agent_name, agent.instructions);
      }
      
      console.log(`✅ AgentDynamoDBService: Saved agent ${agent.agent_name}`);
      return true;
    } catch (error) {
      console.error(`❌ AgentDynamoDBService: Failed to save agent ${agent.agent_name}:`, error);
      return false;
    }
  }

  /**
   * Save agent instructions to DynamoDB
   * Validates: Requirements 7.3
   */
  async saveAgentInstructions(agentName: string, instructions: string): Promise<boolean> {
    if (!await this.ensureClient()) {
      return false;
    }
    
    try {
      const command = new PutItemCommand({
        TableName: this.tableName!,
        Item: marshall({
          pk: `INSTRUCTION#${agentName}`,
          sk: 'v1',
          config_type: 'instruction',
          content: instructions,
          agent_name: agentName,
          updated_at: new Date().toISOString()
        })
      });
      
      await this.dynamoDBClient!.send(command);
      
      // Update cache
      this.instructionsCache.set(agentName, {
        data: instructions,
        timestamp: Date.now(),
        ttl: this.CACHE_TTL
      });
      
      console.log(`✅ AgentDynamoDBService: Saved instructions for ${agentName}`);
      return true;
    } catch (error) {
      console.error(`❌ AgentDynamoDBService: Failed to save instructions for ${agentName}:`, error);
      return false;
    }
  }

  /**
   * Delete agent and all related records from DynamoDB
   * Validates: Requirements 5.3, 5.6
   */
  async deleteAgent(agentName: string): Promise<boolean> {
    if (!await this.ensureClient()) {
      return false;
    }
    
    try {
      // Remove from global config first
      const globalConfig = await this.getGlobalConfig();
      
      if (globalConfig?.agent_configs) {
        delete globalConfig.agent_configs[agentName];
        
        // Also remove from configured_colors
        if (globalConfig.configured_colors) {
          delete globalConfig.configured_colors[agentName];
        }
        
        await this.saveGlobalConfig(globalConfig);
      }
      
      // Delete related records: INSTRUCTION#, CARD#, VIZ_MAP#, VIZ_TEMPLATE#
      const recordPrefixes = ['INSTRUCTION#', 'CARD#', 'VIZ_MAP#'];
      
      for (const prefix of recordPrefixes) {
        try {
          const deleteCommand = new DeleteItemCommand({
            TableName: this.tableName!,
            Key: marshall({
              pk: `${prefix}${agentName}`,
              sk: 'v1'
            })
          });
          await this.dynamoDBClient!.send(deleteCommand);
        } catch (deleteError) {
          // Record might not exist, continue
          console.warn(`⚠️ Could not delete ${prefix}${agentName}:`, deleteError);
        }
      }
      
      // Delete VIZ_TEMPLATE# records (may have multiple)
      // Use Query on ConfigTypeIndex GSI to find visualization_template records for this agent
      try {
        const queryCommand = new QueryCommand({
          TableName: this.tableName!,
          IndexName: 'ConfigTypeIndex',
          KeyConditionExpression: 'config_type = :configType AND begins_with(pk, :prefix)',
          ExpressionAttributeValues: marshall({
            ':configType': 'visualization_template',
            ':prefix': `VIZ_TEMPLATE#${agentName}`
          })
        });
        
        const queryResponse = await this.dynamoDBClient!.send(queryCommand);
        
        if (queryResponse.Items) {
          for (const item of queryResponse.Items) {
            const unmarshalled = unmarshall(item);
            const deleteCommand = new DeleteItemCommand({
              TableName: this.tableName!,
              Key: marshall({
                pk: unmarshalled['pk'],
                sk: unmarshalled['sk']
              })
            });
            await this.dynamoDBClient!.send(deleteCommand);
          }
        }
      } catch (vizError) {
        console.warn(`⚠️ Could not delete VIZ_TEMPLATE records for ${agentName}:`, vizError);
      }
      
      // Invalidate caches
      this.instructionsCache.delete(agentName);
      this.globalConfigCache = null;
      
      console.log(`✅ AgentDynamoDBService: Deleted agent ${agentName} and related records`);
      return true;
    } catch (error) {
      console.error(`❌ AgentDynamoDBService: Failed to delete agent ${agentName}:`, error);
      return false;
    }
  }

  /**
   * Update agent color in global configuration
   * Validates: Requirements 10.2
   */
  async updateAgentColor(agentName: string, color: string): Promise<boolean> {
    const globalConfig = await this.getGlobalConfig();
    
    if (!globalConfig) {
      return false;
    }
    
    // Update configured_colors
    if (!globalConfig.configured_colors) {
      globalConfig.configured_colors = {};
    }
    globalConfig.configured_colors[agentName] = color;
    
    // Also update in agent_configs if agent exists
    if (globalConfig.agent_configs?.[agentName]) {
      globalConfig.agent_configs[agentName].color = color;
    }
    
    return await this.saveGlobalConfig(globalConfig);
  }

  // ============================================
  // Utility Methods
  // ============================================

  /**
   * Check if an agent exists by name or ID
   * Validates: Requirements 4.6
   */
  async checkAgentExists(agentName: string): Promise<boolean> {
    const globalConfig = await this.getGlobalConfig();
    
    if (!globalConfig?.agent_configs) {
      return false;
    }
    
    // Check by agent_name (key)
    if (globalConfig.agent_configs[agentName]) {
      return true;
    }
    
    // Check by agent_id
    for (const agent of Object.values(globalConfig.agent_configs)) {
      if (agent.agent_id === agentName) {
        return true;
      }
    }
    
    return false;
  }

  /**
   * Get agents that depend on the specified agent (reference it in tool_agent_names)
   * Validates: Requirements 5.2
   */
  async getAgentDependencies(agentName: string): Promise<string[]> {
    const globalConfig = await this.getGlobalConfig();
    
    if (!globalConfig?.agent_configs) {
      return [];
    }
    
    const dependencies: string[] = [];
    
    for (const [name, agent] of Object.entries(globalConfig.agent_configs)) {
      if (agent.tool_agent_names?.includes(agentName)) {
        dependencies.push(name);
      }
    }
    
    return dependencies;
  }

  /**
   * Check if DynamoDB has existing configuration
   * Validates: Requirements 11.1
   */
  async checkExistingConfig(): Promise<boolean> {
    const globalConfig = await this.getGlobalConfig();
    return globalConfig !== null;
  }

  /**
   * Invalidate all caches
   */
  invalidateCache(): void {
    this.globalConfigCache = null;
    this.instructionsCache.clear();
    console.log('🗑️ AgentDynamoDBService: Cache invalidated');
  }

  /**
   * Check if the service is properly configured
   */
  async isConfigured(): Promise<boolean> {
    const config = this.awsConfigService.getConfig();
    const agentConfigTable = (config as any)?.agentConfigTable;
    return !!agentConfigTable?.tableName;
  }

  /**
   * Get the table name (for debugging)
   */
  getTableName(): string | null {
    return this.tableName;
  }

  // ============================================
  // Visualization Mapping Operations
  // ============================================

  /**
   * Get visualization mappings for an agent
   * Retrieves from DynamoDB (pk: VIZ_MAP#{agent_name})
   */
  async getVisualizationMappings(agentName: string): Promise<VisualizationMapping | null> {
    if (!await this.ensureClient()) {
      return null;
    }
    
    try {
      const command = new GetItemCommand({
        TableName: this.tableName!,
        Key: marshall({
          pk: `VIZ_MAP#${agentName}`,
          sk: 'v1'
        })
      });
      
      const response = await this.dynamoDBClient!.send(command);
      
      if (!response.Item) {
        console.warn(`⚠️ AgentDynamoDBService: No visualization mappings found for ${agentName}`);
        return null;
      }
      
      const item = unmarshall(response.Item);
      const mappings: VisualizationMapping = JSON.parse(item['content'] as string);
      
      console.log(`✅ AgentDynamoDBService: Loaded visualization mappings for ${agentName}`);
      return mappings;
    } catch (error) {
      console.error(`❌ AgentDynamoDBService: Failed to get visualization mappings for ${agentName}:`, error);
      return null;
    }
  }

  /**
   * Save visualization mappings for an agent
   * Persists to DynamoDB (pk: VIZ_MAP#{agent_name})
   */
  async saveVisualizationMappings(agentName: string, mappings: VisualizationMapping): Promise<boolean> {
    if (!await this.ensureClient()) {
      return false;
    }
    
    try {
      const command = new PutItemCommand({
        TableName: this.tableName!,
        Item: marshall({
          pk: `VIZ_MAP#${agentName}`,
          sk: 'v1',
          config_type: 'visualization_map',
          content: JSON.stringify(mappings),
          agent_name: agentName,
          updated_at: new Date().toISOString()
        })
      });
      
      await this.dynamoDBClient!.send(command);
      
      console.log(`✅ AgentDynamoDBService: Saved visualization mappings for ${agentName}`);
      return true;
    } catch (error) {
      console.error(`❌ AgentDynamoDBService: Failed to save visualization mappings for ${agentName}:`, error);
      return false;
    }
  }

  /**
   * Retrieve a single visualization template schema for an agent.
   * Looks up pk: VIZ_TEMPLATE#{agentName}, sk: {templateId}.
   * Falls back to generic templates (pk: VIZ_TEMPLATE#_GENERIC) if not found.
   */
  async getVisualizationTemplate(agentName: string, templateId: string): Promise<any | null> {
    if (!await this.ensureClient()) {
      return null;
    }

    try {
      // Try agent-specific template first
      const command = new GetItemCommand({
        TableName: this.tableName!,
        Key: marshall({
          pk: `VIZ_TEMPLATE#${agentName}`,
          sk: templateId
        })
      });

      const response = await this.dynamoDBClient!.send(command);

      if (response.Item) {
        const item = unmarshall(response.Item);
        const content = typeof item['content'] === 'string' ? JSON.parse(item['content']) : item['content'];
        console.log(`✅ AgentDynamoDBService: Loaded template ${templateId} for ${agentName}`);
        return content;
      }

      // Fall back to generic template
      const genericCommand = new GetItemCommand({
        TableName: this.tableName!,
        Key: marshall({
          pk: 'VIZ_TEMPLATE#_GENERIC',
          sk: templateId
        })
      });

      const genericResponse = await this.dynamoDBClient!.send(genericCommand);

      if (genericResponse.Item) {
        const item = unmarshall(genericResponse.Item);
        const content = typeof item['content'] === 'string' ? JSON.parse(item['content']) : item['content'];
        console.log(`✅ AgentDynamoDBService: Loaded generic template ${templateId} for ${agentName}`);
        return content;
      }

      console.warn(`⚠️ AgentDynamoDBService: No template ${templateId} found for ${agentName}`);
      return null;
    } catch (error) {
      console.error(`❌ AgentDynamoDBService: Failed to get template ${templateId} for ${agentName}:`, error);
      return null;
    }
  }

}

/**
 * Visualization template mapping interface
 */
export interface VisualizationTemplate {
  templateId: string;
  usage: string;
}

/**
 * Visualization mapping interface for an agent
 */
export interface VisualizationMapping {
  agentName: string;
  agentId: string;
  templates: VisualizationTemplate[];
}
