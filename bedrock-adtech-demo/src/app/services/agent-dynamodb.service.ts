import { Injectable } from '@angular/core';
import { AwsConfigService } from './aws-config.service';
import { DynamoDBClient, GetItemCommand, PutItemCommand, DeleteItemCommand, QueryCommand } from '@aws-sdk/client-dynamodb';
import { marshall, unmarshall } from '@aws-sdk/util-dynamodb';
import { SSMClient, PutParameterCommand, DeleteParameterCommand, GetParameterCommand } from '@aws-sdk/client-ssm';
import { OAuthClientCredentialsRef } from './oauth-client-credentials';

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
  /**
   * Selected authentication mode.
   *
   * Older records have no value here; the editor and the runtime both fall back
   * to inferring the mode from which credential field is populated. New records
   * always set it, because `oauth_m2m` and `bearer` both authenticate with an
   * `Authorization` header and cannot be told apart by presence alone.
   */
  authType?: 'none' | 'bearer' | 'aws_iam' | 'oauth_m2m';
  /**
   * OAuth 2.0 client-credentials reference. The secret lives in SSM under
   * `ssmPath`; the runtime mints a token per server and sends it as a bearer.
   */
  oauthClientCredentials?: OAuthClientCredentialsRef;
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
  /**
   * Where to send requests: an AgentCore runtime ARN (`arn:...`) or an absolute
   * agent URL (`https://...`). Takes precedence over `arn`, which is ARN-only
   * and kept for existing entries.
   */
  endpoint?: string;
  /**
   * Wire protocol used to invoke this peer: `a2a` for JSON-RPC 2.0
   * `message/send`, `http` for a plain JSON request body. Selects the request
   * envelope and response parsing only — independent of endpoint and auth.
   */
  protocol?: 'a2a' | 'http';
  /**
   * @deprecated Superseded by `protocol`. Still read so existing entries keep
   * working: `true` means `a2a`.
   */
  isA2A: boolean;
  /** Optional description of what this external agent provides */
  description?: string;
  /** Whether this external agent is enabled */
  enabled: boolean;
  /**
   * Authentication type. 'oauth' mints a Cognito token via
   * USER_PASSWORD_AUTH; 'oauth_m2m' uses the OAuth 2.0 client-credentials
   * grant against any provider's token endpoint.
   */
  authType?: 'none' | 'oauth' | 'oauth_m2m' | 'iam' | 'bearer';
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
  /**
   * OAuth 2.0 client-credentials reference for `authType: 'oauth_m2m'`. Shares
   * the outbound A2A parameter path with `oauthCredentials`; the stored
   * document's `grant_type` tells the two apart.
   */
  oauthClientCredentials?: OAuthClientCredentialsRef;
  /**
   * Static Bearer Token authentication for A2A agents. The raw token lives
   * only in SSM SecureString; the record keeps a reference plus an optional
   * operator-supplied expiry used for UI warnings (no auto-refresh).
   */
  bearerToken?: {
    /** Whether a token has been stored in SSM Parameter Store */
    hasToken: boolean;
    /** SSM parameter path (set after token storage) */
    ssmPath?: string;
    /** ISO 8601 expiry timestamp; optional; drives UI expiry awareness only */
    expiresAt?: string;
  };
  /** AWS IAM authentication config. `service` only applies to SigV4 (iam) auth. */
  awsAuth?: {
    region: string;
    service?: string;
  };
  /** Cognito user pool id recorded when this entry's runtime uses OAuth */
  cognitoPoolId?: string;
  /** Cognito app client id recorded when this entry's runtime uses OAuth */
  cognitoClientId?: string;
  /**
   * AAMP-only: base URL of the OpenDirect inventory endpoint the IAB AAMP
   * agents use for inventory discovery (their `search_advertising_products`
   * tool speaks OpenDirect REST).
   *
   * The deployment writes the literal string `"not defined"` because no
   * reachable OpenDirect endpoint ships with this stack — an AgentCore runtime
   * does not serve that surface. Inventory discovery genuinely does not work
   * until an operator sets a real endpoint here, and the UI says so rather than
   * implying a working default. Only entries that carry this property render
   * the field in the editor, so it stays out of the way for every other
   * external agent.
   */
  aampInventoryEndpoint?: string;
}

/**
 * Optional webhook-style notification hook fired whenever an agent is
 * invoked with a real user prompt. Completely independent of the A2A
 * external-agent/is_a2a features above — the receiving system is assumed to
 * be arbitrary (a display, a logger, anything) and is never awaited for a
 * response. See spec: a2a-invocation-notify-hook.
 */
export interface NotifyOnInvocationConfig {
  /** HTTPS endpoint to POST the notification to */
  endpoint: string;
  /**
   * Auth type for the notification POST. Matches the A2A auth vocabulary, less
   * the Cognito USER_PASSWORD_AUTH mode: a webhook receiver is arbitrary, so
   * 'oauth_m2m' (client-credentials against the receiver's own provider) is the
   * OAuth flow offered here.
   */
  auth_type: 'none' | 'iam' | 'bearer' | 'oauth_m2m';
  /**
   * Static Bearer Token reference. The raw token lives only in SSM
   * SecureString; this record keeps a reference plus an optional
   * operator-supplied expiry used for UI warnings (no auto-refresh).
   * Present only when auth_type === 'bearer'.
   */
  bearer_token?: {
    hasToken: boolean;
    ssmPath?: string;
    expiresAt?: string;
  };
  /**
   * OAuth 2.0 client-credentials reference. Present only when
   * auth_type === 'oauth_m2m'. Shares the notify parameter path with
   * `bearer_token`; the stored document's `grant_type` tells the two apart.
   */
  oauth_client_credentials?: OAuthClientCredentialsRef;
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
      // NOTE: `temperature` is deliberately absent. The models in use deprecated
      // it, and sending it caused requests to be rejected with a default
      // placeholder response instead of a real completion. Model defaults apply.
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
  /**
   * Where this agent runs.
   *
   * - `adfabric`: hosted in the shared AdFabric Strands runtime, so its model,
   *   instructions, tools, and knowledge base are configured here.
   * - `external`: reachable at its own endpoint, invoked over the wire. None of
   *   the AdFabric-side behaviour settings apply.
   *
   * Defaults to `adfabric`. Records written before this field existed are read
   * through `is_a2a`, which was doubling as the "externally hosted" flag.
   */
  agent_hosting?: 'adfabric' | 'external';
  /**
   * Wire protocol used to invoke this agent: `a2a` for JSON-RPC 2.0
   * `message/send`, `http` for a plain JSON request body.
   *
   * This selects the request envelope and response parsing, nothing else — it
   * does not imply an endpoint kind or an auth mode.
   */
  agent_protocol?: 'a2a' | 'http';
  /**
   * Where to send requests: an AgentCore runtime ARN (`arn:...`) or an absolute
   * agent URL (`https://...`). A URL lets an external agent be something other
   * than an AgentCore runtime.
   *
   * Takes precedence over `runtime_arn`, which remains for records that predate
   * this field and for AdFabric-hosted agents.
   */
  agent_endpoint?: string;
  /**
   * @deprecated Superseded by `agent_protocol` (envelope) and `agent_hosting`
   * (where it runs), which this flag used to conflate. Still read so existing
   * records keep working.
   */
  is_a2a?: boolean;
  /**
   * Authentication type for inbound A2A requests to this agent's endpoint.
   * 'oauth' is Cognito USER_PASSWORD_AUTH; 'oauth_m2m' is the OAuth 2.0
   * client-credentials grant against any provider's token endpoint.
   */
  a2a_auth_type?: 'none' | 'oauth' | 'oauth_m2m' | 'iam' | 'bearer';
  /** OAuth credentials for this agent's own (self-deployed) A2A endpoint, stored in SSM */
  a2a_oauth_credentials?: {
    /** Whether credentials have been stored in SSM Parameter Store */
    hasCredentials: boolean;
    /** SSM parameter path for the credentials */
    ssmPath?: string;
  };
  /**
   * OAuth 2.0 client-credentials reference for `a2a_auth_type: 'oauth_m2m'`.
   * Shares the inbound parameter path with `a2a_oauth_credentials`; the stored
   * document's `grant_type` tells the two apart.
   */
  a2a_oauth_client_credentials?: OAuthClientCredentialsRef;
  /**
   * Static Bearer Token reference for this agent's own (self-deployed) A2A
   * endpoint. Enforcement is the deploy-time runtime authorizer's job, not
   * application code — this stores only a reference and optional expiry.
   */
  a2a_bearer_token?: {
    hasToken: boolean;
    ssmPath?: string;
    expiresAt?: string;
  };
  /**
   * Optional: notify an external system whenever this agent is invoked with
   * a real user prompt. Independent of is_a2a/external_agent_configs — see
   * NotifyOnInvocationConfig for details.
   */
  notify_on_invocation?: NotifyOnInvocationConfig;
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
 * Sort key of the instruction record the agent runtime reads.
 *
 * `dynamodb_config_loader.load_agent_instructions` fetches
 * `INSTRUCTION#<agent>` / `v1`, so this key stays the live pointer and version
 * snapshots go to other sort keys. Changing it requires a runtime change.
 */
export const INSTRUCTION_LIVE_SK = 'v1';

/** Sort key prefix for instruction version snapshots: `VERSION#000007`. */
export const INSTRUCTION_VERSION_SK_PREFIX = 'VERSION#';

/**
 * Width of the zero-padded revision in the sort key. The padding is what makes
 * DynamoDB's lexicographic sort match numeric order, so a descending query
 * returns the newest version first. Correct up to 999999 versions.
 */
const INSTRUCTION_VERSION_PAD = 6;

/** Newest-first cap on how many snapshots the version dropdown loads. */
const MAX_INSTRUCTION_VERSIONS_LISTED = 50;

/**
 * Metadata for one instruction snapshot. Deliberately excludes the instruction
 * text: prompts run to tens of KB, so the list query projects metadata only and
 * the body is fetched when a version is actually selected.
 */
export interface InstructionVersionSummary {
  /** Full DynamoDB sort key, e.g. `VERSION#000007`. */
  sk: string;
  version: number;
  updatedAt?: string;
  author?: string;
  note?: string;
  /** Length of the stored text, so the UI can show size without fetching it. */
  contentLength?: number;
}

export interface InstructionVersionHistory {
  /** Snapshots, newest first. */
  versions: InstructionVersionSummary[];
  live: {
    exists: boolean;
    updatedAt?: string;
    /**
     * Version this pointer was published from, or null when the live text was
     * written outside the versioning path — a redeploy (`--mode overwrite`) or
     * an edit made before versioning existed. Null means the live text may not
     * correspond to any snapshot, which the UI surfaces rather than papering
     * over.
     */
    version: number | null;
  };
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
  private ssmClient: SSMClient | null = null;
  private tableName: string | null = null;
  private region: string = 'us-east-1';
  private stackPrefix: string = '';
  private uniqueId: string = '';
  
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
      
      // Capture stack prefix and unique ID for SSM parameter paths
      this.stackPrefix = (config as any)?.stackPrefix || '';
      this.uniqueId = (config as any)?.uniqueId || '';
      
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
      
      // Initialize SSM client for token management
      this.ssmClient = new SSMClient({
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

  // ============================================
  // Instruction Version History
  // ============================================

  /** Build the padded sort key for a revision number. */
  private instructionVersionSk(version: number): string {
    return `${INSTRUCTION_VERSION_SK_PREFIX}${String(version).padStart(INSTRUCTION_VERSION_PAD, '0')}`;
  }

  /** Parse a revision number back out of a sort key, or null if it isn't one. */
  private parseInstructionVersionSk(sk: string): number | null {
    if (!sk?.startsWith(INSTRUCTION_VERSION_SK_PREFIX)) return null;
    const parsed = Number.parseInt(sk.slice(INSTRUCTION_VERSION_SK_PREFIX.length), 10);
    return Number.isFinite(parsed) ? parsed : null;
  }

  /**
   * Read the live instruction item without consulting the cache.
   *
   * Used on the write path, where the point is to capture what is actually
   * stored before overwriting it — a cached copy could be up to the cache TTL
   * out of date and would archive the wrong text.
   */
  private async readLiveInstructionItem(
    agentName: string
  ): Promise<{ content: string; version: number | null; updatedAt?: string } | null> {
    try {
      const response = await this.dynamoDBClient!.send(new GetItemCommand({
        TableName: this.tableName!,
        Key: marshall({ pk: `INSTRUCTION#${agentName}`, sk: INSTRUCTION_LIVE_SK })
      }));
      if (!response.Item) return null;
      const item = unmarshall(response.Item);
      const rawVersion = item['instruction_version'];
      const version = typeof rawVersion === 'number' ? rawVersion : Number.parseInt(rawVersion, 10);
      return {
        content: (item['content'] as string) ?? '',
        version: Number.isFinite(version) ? version : null,
        updatedAt: item['updated_at'] as string | undefined
      };
    } catch (error) {
      console.error(`❌ AgentDynamoDBService: Failed to read live instructions for ${agentName}:`, error);
      return null;
    }
  }

  /** Write one immutable instruction snapshot. */
  private async putInstructionVersion(
    agentName: string,
    version: number,
    instructions: string,
    meta: { author?: string; note?: string; updatedAt?: string } = {}
  ): Promise<void> {
    const item: Record<string, any> = {
      pk: `INSTRUCTION#${agentName}`,
      sk: this.instructionVersionSk(version),
      config_type: 'instruction_version',
      agent_name: agentName,
      content: instructions,
      content_length: instructions.length,
      instruction_version: version,
      updated_at: meta.updatedAt || new Date().toISOString()
    };
    if (meta.author) item['author'] = meta.author;
    if (meta.note) item['note'] = meta.note;

    await this.dynamoDBClient!.send(new PutItemCommand({
      TableName: this.tableName!,
      Item: marshall(item, { removeUndefinedValues: true })
    }));
  }

  /**
   * List instruction snapshots for an agent, newest first, plus the state of the
   * live pointer.
   *
   * Projects metadata only — `content` is excluded so opening the dropdown does
   * not pull every historical prompt into the browser.
   */
  async getInstructionVersionHistory(agentName: string): Promise<InstructionVersionHistory> {
    const empty: InstructionVersionHistory = { versions: [], live: { exists: false, version: null } };

    if (!await this.ensureClient()) {
      return empty;
    }

    const history: InstructionVersionHistory = { versions: [], live: { exists: false, version: null } };

    try {
      const response = await this.dynamoDBClient!.send(new QueryCommand({
        TableName: this.tableName!,
        KeyConditionExpression: 'pk = :pk AND begins_with(sk, :prefix)',
        ExpressionAttributeValues: marshall({
          ':pk': `INSTRUCTION#${agentName}`,
          ':prefix': INSTRUCTION_VERSION_SK_PREFIX
        }),
        // Aliased throughout so no attribute name can collide with a DynamoDB
        // reserved word.
        ProjectionExpression: '#sk, #ver, #upd, #author, #note, #len',
        ExpressionAttributeNames: {
          '#sk': 'sk',
          '#ver': 'instruction_version',
          '#upd': 'updated_at',
          '#author': 'author',
          '#note': 'note',
          '#len': 'content_length'
        },
        ScanIndexForward: false,
        Limit: MAX_INSTRUCTION_VERSIONS_LISTED
      }));

      for (const raw of response.Items || []) {
        const item = unmarshall(raw);
        const sk = item['sk'] as string;
        const version = this.parseInstructionVersionSk(sk);
        if (version === null) continue;
        history.versions.push({
          sk,
          version,
          updatedAt: item['updated_at'] as string | undefined,
          author: item['author'] as string | undefined,
          note: item['note'] as string | undefined,
          contentLength: typeof item['content_length'] === 'number' ? item['content_length'] : undefined
        });
      }
    } catch (error) {
      console.error(`❌ AgentDynamoDBService: Failed to list instruction versions for ${agentName}:`, error);
      return empty;
    }

    const live = await this.readLiveInstructionItem(agentName);
    if (live) {
      history.live = { exists: true, version: live.version, updatedAt: live.updatedAt };
    }

    return history;
  }

  /**
   * Fetch the instruction text for one snapshot sort key, or for the live
   * record when passed INSTRUCTION_LIVE_SK.
   */
  async getAgentInstructionsAtVersion(agentName: string, sk: string): Promise<string | null> {
    const cacheKey = `${agentName}#${sk}`;
    const cached = this.instructionsCache.get(cacheKey);
    if (this.isCacheValid(cached || null)) {
      return cached!.data;
    }

    if (!await this.ensureClient()) {
      return null;
    }

    try {
      const response = await this.dynamoDBClient!.send(new GetItemCommand({
        TableName: this.tableName!,
        Key: marshall({ pk: `INSTRUCTION#${agentName}`, sk })
      }));

      if (!response.Item) {
        console.warn(`⚠️ AgentDynamoDBService: No instructions at ${agentName}/${sk}`);
        return null;
      }

      const instructions = (unmarshall(response.Item)['content'] as string) ?? '';
      this.instructionsCache.set(cacheKey, {
        data: instructions,
        timestamp: Date.now(),
        ttl: this.CACHE_TTL
      });
      return instructions;
    } catch (error) {
      console.error(`❌ AgentDynamoDBService: Failed to get instructions at ${agentName}/${sk}:`, error);
      return null;
    }
  }

  /**
   * Save agent configuration to DynamoDB
   * Updates both the global config and instruction record
   * Validates: Requirements 3.4, 4.5, 6.2, 6.6
   *
   * @param editedBy Identity recorded as the author of the instruction version
   *                 this save creates. Omitted means the snapshot carries no
   *                 author rather than an assumed one.
   */
  async saveAgent(agent: AgentConfiguration, editedBy?: string): Promise<boolean> {
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

      // Propagate this agent's own model_inputs entry to any orchestrator that
      // has a per-collaborator override for this agent. When AgencyAgent
      // invokes SignalAgent as a collaborator, the backend reads
      // AgencyAgent.model_inputs[SignalAgent] — not SignalAgent.model_inputs.
      // Without this propagation, model edits appear to "not take" for
      // collaborator-invoked agents.
      // See handler.py::get_collaborator_agent_model_inputs.
      const ownModelEntry = agent.model_inputs?.[agent.agent_name];
      if (ownModelEntry) {
        for (const [otherName, otherAgent] of Object.entries(globalConfig.agent_configs)) {
          if (otherName === agent.agent_name) continue;
          if (otherAgent.model_inputs && otherAgent.model_inputs[agent.agent_name]) {
            otherAgent.model_inputs[agent.agent_name] = { ...ownModelEntry };
          }
        }
      }

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
      
      // Only write instructions when there is text to write. The editor opens
      // with an empty instructions field and back-fills it asynchronously, so a
      // save issued before that fetch lands would otherwise blank the stored
      // prompt.
      if (agent.instructions) {
        await this.saveAgentInstructions(agent.agent_name, agent.instructions, { author: editedBy });
      }
      
      console.log(`✅ AgentDynamoDBService: Saved agent ${agent.agent_name}`);
      return true;
    } catch (error) {
      console.error(`❌ AgentDynamoDBService: Failed to save agent ${agent.agent_name}:`, error);
      return false;
    }
  }

  /**
   * Save agent instructions, appending a version snapshot.
   * Validates: Requirements 7.3
   *
   * Two writes per save: an immutable `VERSION#<n>` snapshot, then the `v1`
   * pointer the agent runtime reads. History is append-only — restoring an
   * earlier version publishes it as a new one rather than rewinding, so nothing
   * is ever overwritten in place.
   */
  async saveAgentInstructions(
    agentName: string,
    instructions: string,
    options: { author?: string; note?: string } = {}
  ): Promise<boolean> {
    if (!await this.ensureClient()) {
      return false;
    }

    try {
      const history = await this.getInstructionVersionHistory(agentName);
      const live = await this.readLiveInstructionItem(agentName);
      let lastVersion = history.versions.length ? history.versions[0].version : 0;

      if (live && live.version === null && live.content) {
        // Live text came from outside the versioning path (a redeploy, or an edit
        // predating this feature). Archive it before the pointer is overwritten
        // in place, otherwise the deploy-seeded prompt is unrecoverable after the
        // first edit.
        lastVersion += 1;
        await this.putInstructionVersion(agentName, lastVersion, live.content, {
          updatedAt: live.updatedAt,
          note: 'Captured from the live record when versioning began'
        });
      } else if (live && live.content === instructions) {
        // Byte-identical to what is already published; another snapshot would
        // only pad the dropdown.
        console.log(`ℹ️ AgentDynamoDBService: Instructions for ${agentName} unchanged, no new version`);
        return true;
      }

      const nextVersion = lastVersion + 1;
      const savedAt = new Date().toISOString();

      await this.putInstructionVersion(agentName, nextVersion, instructions, {
        author: options.author,
        note: options.note,
        updatedAt: savedAt
      });

      // The live pointer. `instruction_version` ties it back to the snapshot so
      // the UI can tell whether the running text corresponds to a known version.
      await this.dynamoDBClient!.send(new PutItemCommand({
        TableName: this.tableName!,
        Item: marshall({
          pk: `INSTRUCTION#${agentName}`,
          sk: INSTRUCTION_LIVE_SK,
          config_type: 'instruction',
          content: instructions,
          agent_name: agentName,
          instruction_version: nextVersion,
          updated_at: savedAt
        })
      }));

      this.instructionsCache.set(agentName, {
        data: instructions,
        timestamp: Date.now(),
        ttl: this.CACHE_TTL
      });
      this.instructionsCache.delete(`${agentName}#${INSTRUCTION_LIVE_SK}`);

      console.log(`✅ AgentDynamoDBService: Saved instructions for ${agentName} as v${nextVersion}`);
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
      
      // Delete instruction version snapshots. These live under the same pk as the
      // live instruction record but on other sort keys, so the fixed-key deletes
      // below would leave them behind for a later agent of the same name to
      // inherit.
      try {
        const versionQuery = new QueryCommand({
          TableName: this.tableName!,
          KeyConditionExpression: 'pk = :pk AND begins_with(sk, :prefix)',
          ExpressionAttributeValues: marshall({
            ':pk': `INSTRUCTION#${agentName}`,
            ':prefix': INSTRUCTION_VERSION_SK_PREFIX
          }),
          ProjectionExpression: '#sk',
          ExpressionAttributeNames: { '#sk': 'sk' }
        });
        const versionResponse = await this.dynamoDBClient!.send(versionQuery);
        for (const raw of versionResponse.Items || []) {
          const { sk } = unmarshall(raw);
          await this.dynamoDBClient!.send(new DeleteItemCommand({
            TableName: this.tableName!,
            Key: marshall({ pk: `INSTRUCTION#${agentName}`, sk })
          }));
        }
      } catch (versionError) {
        console.warn(`⚠️ Could not delete instruction versions for ${agentName}:`, versionError);
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
  // A2A Inbound OAuth Credential Management (SSM SecureString)
  // ============================================

  /**
   * Build the SSM parameter path for an agent's inbound A2A OAuth credentials.
   * Format: /{stackPrefix}/a2a-inbound-tokens/{uniqueId}/{agentName}
   */
  private getA2AInboundTokenSsmPath(agentName: string): string {
    return `/${this.stackPrefix}/a2a-inbound-tokens/${this.uniqueId}/${agentName}`;
  }

  /**
   * Store inbound A2A OAuth credentials securely in SSM Parameter Store.
   * These credentials are used by callers to authenticate to this agent's A2A endpoint.
   * Returns the SSM parameter path on success.
   */
  async storeA2AInboundOAuthCredentials(agentName: string, credentials: string): Promise<string | null> {
    if (!await this.ensureClient() || !this.ssmClient) {
      return null;
    }

    const ssmPath = this.getA2AInboundTokenSsmPath(agentName);

    try {
      await this.ssmClient.send(new PutParameterCommand({
        Name: ssmPath,
        Value: credentials,
        Type: 'SecureString',
        Overwrite: true,
        Description: `Inbound A2A OAuth credentials for agent ${agentName}`
      }));

      console.log(`✅ AgentDynamoDBService: Stored inbound A2A OAuth credentials at ${ssmPath}`);
      return ssmPath;
    } catch (error: any) {
      const errorMsg = error?.message || error?.name || 'Unknown error';
      console.error(`❌ AgentDynamoDBService: Failed to store inbound A2A OAuth credentials at ${ssmPath}:`, error);
      throw new Error(`SSM PutParameter failed for ${ssmPath}: ${errorMsg}. Ensure the Cognito AuthenticatedRole has ssm:PutParameter permission.`);
    }
  }

  /**
   * Retrieve inbound A2A OAuth credentials from SSM Parameter Store.
   * Returns the decrypted credentials JSON string, or null if not found.
   */
  async getA2AInboundOAuthCredentials(agentName: string): Promise<string | null> {
    if (!await this.ensureClient() || !this.ssmClient) {
      return null;
    }

    const ssmPath = this.getA2AInboundTokenSsmPath(agentName);

    try {
      const response = await this.ssmClient.send(new GetParameterCommand({
        Name: ssmPath,
        WithDecryption: true
      }));

      return response.Parameter?.Value || null;
    } catch (error: any) {
      if (error.name === 'ParameterNotFound') {
        return null;
      }
      console.error(`❌ AgentDynamoDBService: Failed to retrieve inbound A2A OAuth credentials at ${ssmPath}:`, error);
      return null;
    }
  }

  /**
   * Delete inbound A2A OAuth credentials from SSM Parameter Store.
   */
  async deleteA2AInboundOAuthCredentials(agentName: string): Promise<boolean> {
    if (!await this.ensureClient() || !this.ssmClient) {
      return false;
    }

    const ssmPath = this.getA2AInboundTokenSsmPath(agentName);

    try {
      await this.ssmClient.send(new DeleteParameterCommand({ Name: ssmPath }));
      console.log(`✅ AgentDynamoDBService: Deleted inbound A2A OAuth credentials at ${ssmPath}`);
      return true;
    } catch (error: any) {
      if (error.name === 'ParameterNotFound') {
        return true;
      }
      console.error(`❌ AgentDynamoDBService: Failed to delete inbound A2A OAuth credentials at ${ssmPath}:`, error);
      return false;
    }
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

  // ============================================
  // MCP OAuth Token Management (SSM SecureString)
  // ============================================

  /**
   * Build the SSM parameter path for an MCP server's OAuth token.
   * Format: /{stackPrefix}/mcp-tokens/{uniqueId}/{agentName}/{serverId}
   */
  private getMcpTokenSsmPath(agentName: string, serverId: string): string {
    return `/${this.stackPrefix}/mcp-tokens/${this.uniqueId}/${agentName}/${serverId}`;
  }

  /**
   * Store an OAuth bearer token securely in SSM Parameter Store as SecureString.
   * The token is encrypted at rest using the default AWS KMS key.
   * Returns the SSM parameter path on success.
   */
  async storeMcpOAuthToken(agentName: string, serverId: string, token: string): Promise<string | null> {
    if (!await this.ensureClient() || !this.ssmClient) {
      return null;
    }

    const ssmPath = this.getMcpTokenSsmPath(agentName, serverId);

    try {
      await this.ssmClient.send(new PutParameterCommand({
        Name: ssmPath,
        Value: token,
        Type: 'SecureString',
        Overwrite: true,
        Description: `OAuth bearer token for MCP server ${serverId} on agent ${agentName}`
      }));

      console.log(`✅ AgentDynamoDBService: Stored OAuth token at ${ssmPath}`);
      return ssmPath;
    } catch (error: any) {
      const errorMsg = error?.message || error?.name || 'Unknown error';
      console.error(`❌ AgentDynamoDBService: Failed to store OAuth token at ${ssmPath}:`, error);
      // Re-throw with a descriptive message so the UI can display it
      throw new Error(`SSM PutParameter failed for ${ssmPath}: ${errorMsg}. Ensure the Cognito AuthenticatedRole has ssm:PutParameter permission (redeploy infrastructure-core.yml).`);
    }
  }

  /**
   * Delete an OAuth bearer token from SSM Parameter Store.
   */
  async deleteMcpOAuthToken(agentName: string, serverId: string): Promise<boolean> {
    if (!await this.ensureClient() || !this.ssmClient) {
      return false;
    }

    const ssmPath = this.getMcpTokenSsmPath(agentName, serverId);

    try {
      await this.ssmClient.send(new DeleteParameterCommand({ Name: ssmPath }));
      console.log(`✅ AgentDynamoDBService: Deleted OAuth token at ${ssmPath}`);
      return true;
    } catch (error: any) {
      // ParameterNotFound is fine — token was already deleted or never existed
      if (error.name === 'ParameterNotFound') {
        return true;
      }
      console.error(`❌ AgentDynamoDBService: Failed to delete OAuth token at ${ssmPath}:`, error);
      return false;
    }
  }

  /**
   * Retrieve an OAuth bearer token from SSM Parameter Store.
   * Returns the decrypted token string, or null if not found.
   */
  async getMcpOAuthToken(agentName: string, serverId: string): Promise<string | null> {
    if (!await this.ensureClient() || !this.ssmClient) {
      return null;
    }

    const ssmPath = this.getMcpTokenSsmPath(agentName, serverId);

    try {
      const response = await this.ssmClient.send(new GetParameterCommand({
        Name: ssmPath,
        WithDecryption: true
      }));

      return response.Parameter?.Value || null;
    } catch (error: any) {
      if (error.name === 'ParameterNotFound') {
        console.warn(`⚠️ AgentDynamoDBService: No OAuth token found at ${ssmPath}`);
        return null;
      }
      console.error(`❌ AgentDynamoDBService: Failed to retrieve OAuth token at ${ssmPath}:`, error);
      return null;
    }
  }

  // ============================================
  // A2A External Agent OAuth Token Management (SSM SecureString)
  // ============================================

  /**
   * Build the SSM parameter path for an A2A external agent's OAuth token.
   * Format: /{stackPrefix}/a2a-tokens/{uniqueId}/{agentName}/{externalAgentName}
   */
  private getA2ATokenSsmPath(agentName: string, externalAgentName: string): string {
    return `/${this.stackPrefix}/a2a-tokens/${this.uniqueId}/${agentName}/${externalAgentName}`;
  }

  /**
   * Store an OAuth bearer token for an A2A external agent securely in SSM Parameter Store.
   * Returns the SSM parameter path on success.
   */
  async storeA2AOAuthToken(agentName: string, externalAgentName: string, token: string): Promise<string | null> {
    if (!await this.ensureClient() || !this.ssmClient) {
      return null;
    }

    const ssmPath = this.getA2ATokenSsmPath(agentName, externalAgentName);

    try {
      await this.ssmClient.send(new PutParameterCommand({
        Name: ssmPath,
        Value: token,
        Type: 'SecureString',
        Overwrite: true,
        Description: `OAuth bearer token for A2A external agent ${externalAgentName} on agent ${agentName}`
      }));

      console.log(`✅ AgentDynamoDBService: Stored A2A OAuth token at ${ssmPath}`);
      return ssmPath;
    } catch (error: any) {
      const errorMsg = error?.message || error?.name || 'Unknown error';
      console.error(`❌ AgentDynamoDBService: Failed to store A2A OAuth token at ${ssmPath}:`, error);
      throw new Error(`SSM PutParameter failed for ${ssmPath}: ${errorMsg}. Ensure the Cognito AuthenticatedRole has ssm:PutParameter permission.`);
    }
  }

  /**
   * Delete an A2A external agent OAuth token from SSM Parameter Store.
   */
  async deleteA2AOAuthToken(agentName: string, externalAgentName: string): Promise<boolean> {
    if (!await this.ensureClient() || !this.ssmClient) {
      return false;
    }

    const ssmPath = this.getA2ATokenSsmPath(agentName, externalAgentName);

    try {
      await this.ssmClient.send(new DeleteParameterCommand({ Name: ssmPath }));
      console.log(`✅ AgentDynamoDBService: Deleted A2A OAuth token at ${ssmPath}`);
      return true;
    } catch (error: any) {
      if (error.name === 'ParameterNotFound') {
        return true;
      }
      console.error(`❌ AgentDynamoDBService: Failed to delete A2A OAuth token at ${ssmPath}:`, error);
      return false;
    }
  }

  /**
   * Retrieve an A2A external agent OAuth token from SSM Parameter Store.
   * Returns the decrypted token string, or null if not found.
   */
  async getA2AOAuthToken(agentName: string, externalAgentName: string): Promise<string | null> {
    if (!await this.ensureClient() || !this.ssmClient) {
      return null;
    }

    const ssmPath = this.getA2ATokenSsmPath(agentName, externalAgentName);

    try {
      const response = await this.ssmClient.send(new GetParameterCommand({
        Name: ssmPath,
        WithDecryption: true
      }));

      return response.Parameter?.Value || null;
    } catch (error: any) {
      if (error.name === 'ParameterNotFound') {
        console.warn(`⚠️ AgentDynamoDBService: No A2A OAuth token found at ${ssmPath}`);
        return null;
      }
      console.error(`❌ AgentDynamoDBService: Failed to retrieve A2A OAuth token at ${ssmPath}:`, error);
      return null;
    }
  }

  // ============================================
  // A2A External Agent Bearer Token Management (SSM SecureString)
  // ============================================
  //
  // A "bearer" A2A auth entry stores an operator-pasted token verbatim and
  // sends it as `Authorization: Bearer <token>`. Unlike the OAuth path, the
  // token is NOT minted via Cognito — it works against peers hosted anywhere
  // (AWS or not). The token is written only as a SecureString; the agent
  // record keeps just { hasToken, ssmPath, expiresAt }. The token value is
  // never logged or echoed in errors.

  /** Maximum accepted bearer-token length (chars) before storage. */
  private static readonly MAX_BEARER_TOKEN_LENGTH = 8192;

  /**
   * Store a static Bearer Token for an A2A external agent securely in SSM.
   * Reuses the existing outbound path scheme
   * (/{stackPrefix}/a2a-tokens/{uniqueId}/{agentName}/{externalAgentName}).
   *
   * Validates before storage: the token must be non-empty after trimming and
   * within the length bound. Validation and permission errors are surfaced
   * WITHOUT including the token value. Returns the SSM path on success.
   */
  async storeA2ABearerToken(agentName: string, externalAgentName: string, token: string): Promise<string | null> {
    const trimmed = (token || '').trim();
    if (!trimmed) {
      throw new Error('Bearer token is required and cannot be empty.');
    }
    if (trimmed.length > AgentDynamoDBService.MAX_BEARER_TOKEN_LENGTH) {
      throw new Error(`Bearer token is too long (max ${AgentDynamoDBService.MAX_BEARER_TOKEN_LENGTH} characters).`);
    }

    if (!await this.ensureClient() || !this.ssmClient) {
      return null;
    }

    const ssmPath = this.getA2ATokenSsmPath(agentName, externalAgentName);

    try {
      await this.ssmClient.send(new PutParameterCommand({
        Name: ssmPath,
        Value: trimmed,
        Type: 'SecureString',
        Overwrite: true,
        Description: `Static A2A bearer token for external agent ${externalAgentName} on agent ${agentName}`
      }));

      console.log(`✅ AgentDynamoDBService: Stored A2A bearer token at ${ssmPath}`);
      return ssmPath;
    } catch (error: any) {
      // Never include the token value in the error.
      const errorMsg = error?.message || error?.name || 'Unknown error';
      console.error(`❌ AgentDynamoDBService: Failed to store A2A bearer token at ${ssmPath}:`, error);
      throw new Error(`SSM PutParameter failed for ${ssmPath}: ${errorMsg}. Ensure the Cognito AuthenticatedRole has ssm:PutParameter permission on that path prefix.`);
    }
  }

  /**
   * Retrieve a static A2A Bearer Token from SSM. Returns the decrypted token
   * string, or null if not found.
   */
  async getA2ABearerToken(agentName: string, externalAgentName: string): Promise<string | null> {
    if (!await this.ensureClient() || !this.ssmClient) {
      return null;
    }

    const ssmPath = this.getA2ATokenSsmPath(agentName, externalAgentName);

    try {
      const response = await this.ssmClient.send(new GetParameterCommand({
        Name: ssmPath,
        WithDecryption: true
      }));

      return response.Parameter?.Value || null;
    } catch (error: any) {
      if (error.name === 'ParameterNotFound') {
        console.warn(`⚠️ AgentDynamoDBService: No A2A bearer token found at ${ssmPath}`);
        return null;
      }
      console.error(`❌ AgentDynamoDBService: Failed to retrieve A2A bearer token at ${ssmPath}:`, error);
      return null;
    }
  }

  /**
   * Delete a static A2A Bearer Token from SSM Parameter Store.
   */
  async deleteA2ABearerToken(agentName: string, externalAgentName: string): Promise<boolean> {
    if (!await this.ensureClient() || !this.ssmClient) {
      return false;
    }

    const ssmPath = this.getA2ATokenSsmPath(agentName, externalAgentName);

    try {
      await this.ssmClient.send(new DeleteParameterCommand({ Name: ssmPath }));
      console.log(`✅ AgentDynamoDBService: Deleted A2A bearer token at ${ssmPath}`);
      return true;
    } catch (error: any) {
      if (error.name === 'ParameterNotFound') {
        return true;
      }
      console.error(`❌ AgentDynamoDBService: Failed to delete A2A bearer token at ${ssmPath}:`, error);
      return false;
    }
  }

  // ============================================
  // Invocation Notification Bearer Token Management (SSM SecureString)
  // ============================================
  //
  // Mirrors the A2A bearer-token pattern above exactly (same validation,
  // same never-leak-the-token-in-errors behavior), but stored under a
  // separate path prefix since this feature is independent of A2A. See
  // spec: a2a-invocation-notify-hook.

  /**
   * Build the SSM parameter path for an agent's invocation-notification
   * bearer token. Format: /{stackPrefix}/notify-tokens/{uniqueId}/{agentName}
   */
  private getNotifyTokenSsmPath(agentName: string): string {
    return `/${this.stackPrefix}/notify-tokens/${this.uniqueId}/${agentName}`;
  }

  /**
   * Store a static Bearer Token for an agent's invocation-notification hook
   * securely in SSM. Validates before storage: the token must be non-empty
   * after trimming and within the length bound. Validation and permission
   * errors are surfaced WITHOUT including the token value. Returns the SSM
   * path on success.
   */
  async storeNotifyBearerToken(agentName: string, token: string): Promise<string | null> {
    const trimmed = (token || '').trim();
    if (!trimmed) {
      throw new Error('Bearer token is required and cannot be empty.');
    }
    if (trimmed.length > AgentDynamoDBService.MAX_BEARER_TOKEN_LENGTH) {
      throw new Error(`Bearer token is too long (max ${AgentDynamoDBService.MAX_BEARER_TOKEN_LENGTH} characters).`);
    }

    if (!await this.ensureClient() || !this.ssmClient) {
      return null;
    }

    const ssmPath = this.getNotifyTokenSsmPath(agentName);

    try {
      await this.ssmClient.send(new PutParameterCommand({
        Name: ssmPath,
        Value: trimmed,
        Type: 'SecureString',
        Overwrite: true,
        Description: `Static invocation-notification bearer token for agent ${agentName}`
      }));

      console.log(`✅ AgentDynamoDBService: Stored notify bearer token at ${ssmPath}`);
      return ssmPath;
    } catch (error: any) {
      // Never include the token value in the error.
      const errorMsg = error?.message || error?.name || 'Unknown error';
      console.error(`❌ AgentDynamoDBService: Failed to store notify bearer token at ${ssmPath}:`, error);
      throw new Error(`SSM PutParameter failed for ${ssmPath}: ${errorMsg}. Ensure the Cognito AuthenticatedRole has ssm:PutParameter permission on that path prefix.`);
    }
  }

  /**
   * Retrieve a static invocation-notification Bearer Token from SSM.
   * Returns the decrypted token string, or null if not found.
   */
  async getNotifyBearerToken(agentName: string): Promise<string | null> {
    if (!await this.ensureClient() || !this.ssmClient) {
      return null;
    }

    const ssmPath = this.getNotifyTokenSsmPath(agentName);

    try {
      const response = await this.ssmClient.send(new GetParameterCommand({
        Name: ssmPath,
        WithDecryption: true
      }));

      return response.Parameter?.Value || null;
    } catch (error: any) {
      if (error.name === 'ParameterNotFound') {
        console.warn(`⚠️ AgentDynamoDBService: No notify bearer token found at ${ssmPath}`);
        return null;
      }
      console.error(`❌ AgentDynamoDBService: Failed to retrieve notify bearer token at ${ssmPath}:`, error);
      return null;
    }
  }

  /**
   * Delete a static invocation-notification Bearer Token from SSM Parameter Store.
   */
  async deleteNotifyBearerToken(agentName: string): Promise<boolean> {
    if (!await this.ensureClient() || !this.ssmClient) {
      return false;
    }

    const ssmPath = this.getNotifyTokenSsmPath(agentName);

    try {
      await this.ssmClient.send(new DeleteParameterCommand({ Name: ssmPath }));
      console.log(`✅ AgentDynamoDBService: Deleted notify bearer token at ${ssmPath}`);
      return true;
    } catch (error: any) {
      if (error.name === 'ParameterNotFound') {
        return true;
      }
      console.error(`❌ AgentDynamoDBService: Failed to delete notify bearer token at ${ssmPath}:`, error);
      return false;
    }
  }

  // ============================================
  // Visualization Mapping Operations
  // ============================================

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
