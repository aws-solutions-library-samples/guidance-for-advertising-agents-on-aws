import { Injectable } from '@angular/core';
import { AwsConfigService } from './aws-config.service';
import { DynamoDBClient, GetItemCommand, PutItemCommand } from '@aws-sdk/client-dynamodb';
import { marshall, unmarshall } from '@aws-sdk/util-dynamodb';
import { TabsConfiguration, TabConfiguration } from '../models/application-models';

interface CacheEntry<T> {
  data: T;
  timestamp: number;
  ttl: number;
}

/**
 * TabDynamoDBService - CRUD operations for tab configurations in DynamoDB.
 * Follows the same patterns as AgentDynamoDBService (lazy init, Cognito credentials, in-memory cache).
 */
@Injectable({
  providedIn: 'root'
})
export class TabDynamoDBService {
  private dynamoDBClient: DynamoDBClient | null = null;
  private tableName: string | null = null;
  private region: string = 'us-east-1';

  // DynamoDB key constants
  private readonly PK = 'TAB_CONFIG';
  private readonly SK = 'v1';
  private readonly CONFIG_TYPE = 'tab_config';

  // Cache
  private cache: CacheEntry<TabsConfiguration> | null = null;
  private readonly CACHE_TTL = 5 * 60 * 1000; // 5 minutes

  // Version tracking
  private currentVersion: number = 0;

  constructor(private awsConfigService: AwsConfigService) {}

  /**
   * Initialize DynamoDB client with Cognito credentials (lazy init).
   */
  private async initializeClient(): Promise<boolean> {
    try {
      const config = this.awsConfigService.getConfig();
      const agentConfigTable = (config as any)?.agentConfigTable;
      if (!agentConfigTable?.tableName) {
        console.warn('⚠️ TabDynamoDBService: agentConfigTable not configured in aws-config.json');
        return false;
      }

      this.tableName = agentConfigTable.tableName;
      this.region = agentConfigTable.region || config?.aws?.region || 'us-east-1';

      const session = await this.awsConfigService.getCachedAuthSession();
      if (!session?.credentials) {
        console.warn('⚠️ TabDynamoDBService: No valid credentials available');
        return false;
      }

      this.dynamoDBClient = new DynamoDBClient({
        region: this.region,
        credentials: session.credentials,
        maxAttempts: 3
      });

      console.log(`✅ TabDynamoDBService: Initialized with table ${this.tableName} in ${this.region}`);
      return true;
    } catch (error) {
      console.error('❌ TabDynamoDBService: Failed to initialize client:', error);
      return false;
    }
  }

  private async ensureClient(): Promise<boolean> {
    if (this.dynamoDBClient && this.tableName) {
      return true;
    }
    return await this.initializeClient();
  }

  private isCacheValid(): boolean {
    if (!this.cache) return false;
    return (Date.now() - this.cache.timestamp) < this.cache.ttl;
  }

  // ============================================
  // Core CRUD Operations
  // ============================================

  /**
   * Get tab configuration from DynamoDB with cache.
   */
  async getTabConfig(): Promise<TabsConfiguration | null> {
    

    if (!await this.ensureClient()) {
      return null;
    }

    try {
      const command = new GetItemCommand({
        TableName: this.tableName!,
        Key: marshall({ pk: this.PK, sk: this.SK })
      });

      const response = await this.dynamoDBClient!.send(command);

      if (!response.Item) {
        console.warn('⚠️ TabDynamoDBService: No tab config found in DynamoDB');
        return null;
      }

      const item = unmarshall(response.Item);
      const tabConfig: TabsConfiguration = JSON.parse(item['content'] as string);

      // Track current version
      this.currentVersion = (item['version'] as number) || 0;

      // Update cache
      this.cache = {
        data: tabConfig,
        timestamp: Date.now(),
        ttl: this.CACHE_TTL
      };

      console.log('✅ TabDynamoDBService: Loaded tab config from DynamoDB');
      return tabConfig;
    } catch (error) {
      console.error('❌ TabDynamoDBService: Failed to get tab config:', error);
      return null;
    }
  }

  /**
   * Save full tab configuration to DynamoDB with versioning.
   */
  async saveTabConfig(config: TabsConfiguration, updatedBy: string): Promise<boolean> {
    if (!await this.ensureClient()) {
      return false;
    }

    try {
      const newVersion = this.currentVersion + 1;

      const command = new PutItemCommand({
        TableName: this.tableName!,
        Item: marshall({
          pk: this.PK,
          sk: this.SK,
          config_type: this.CONFIG_TYPE,
          content: JSON.stringify(config),
          updated_at: new Date().toISOString(),
          version: newVersion,
          updated_by: updatedBy
        })
      });

      await this.dynamoDBClient!.send(command);

      // Update cache and version on success
      this.currentVersion = newVersion;
      this.cache = {
        data: config,
        timestamp: Date.now(),
        ttl: this.CACHE_TTL
      };

      console.log(`✅ TabDynamoDBService: Saved tab config v${newVersion} by ${updatedBy}`);
      return true;
    } catch (error) {
      console.error('❌ TabDynamoDBService: Failed to save tab config:', error);
      return false;
    }
  }

  // ============================================
  // Individual Tab Operations
  // ============================================

  /**
   * Add a new tab. Validates unique ID before inserting.
   */
  async addTab(tabId: string, tab: TabConfiguration): Promise<boolean> {
    const config = await this.getTabConfig();
    if (!config) {
      console.warn('⚠️ TabDynamoDBService: Cannot add tab - no existing config');
      return false;
    }

    if (config.tabConfigurations[tabId]) {
      console.warn(`⚠️ TabDynamoDBService: Tab ID "${tabId}" already exists`);
      return false;
    }

    config.tabConfigurations[tabId] = tab;
    return await this.saveTabConfig(config, 'ui-edit');
  }

  /**
   * Update an existing tab. Verifies tab exists before replacing.
   */
  async updateTab(tabId: string, tab: TabConfiguration): Promise<boolean> {
    const config = await this.getTabConfig();
    if (!config) {
      console.warn('⚠️ TabDynamoDBService: Cannot update tab - no existing config');
      return false;
    }

    if (!config.tabConfigurations[tabId]) {
      console.warn(`⚠️ TabDynamoDBService: Tab ID "${tabId}" not found`);
      return false;
    }

    config.tabConfigurations[tabId] = tab;
    return await this.saveTabConfig(config, 'ui-edit');
  }

  /**
   * Delete a tab. Verifies tab exists before removing.
   */
  async deleteTab(tabId: string): Promise<boolean> {
    const config = await this.getTabConfig();
    if (!config) {
      console.warn('⚠️ TabDynamoDBService: Cannot delete tab - no existing config');
      return false;
    }

    if (!config.tabConfigurations[tabId]) {
      console.warn(`⚠️ TabDynamoDBService: Tab ID "${tabId}" not found`);
      return false;
    }

    delete config.tabConfigurations[tabId];
    return await this.saveTabConfig(config, 'ui-edit');
  }

  // ============================================
  // Cache Management
  // ============================================

  /**
   * Invalidate in-memory cache to force fresh DynamoDB load.
   */
  clearCache(): void {
    this.cache = null;
    console.log('🗑️ TabDynamoDBService: Cache cleared');
  }
}
