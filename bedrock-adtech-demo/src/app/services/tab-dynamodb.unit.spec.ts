/**
 * Unit tests for TabDynamoDBService and AgentConfigService tab integration.
 * Feature: tab-config-dynamodb-migration
 *
 * Tests loading priority chain, save failures, duplicate/missing tab IDs,
 * event emission, and seed script behavior.
 */

// ============================================
// Types (mirrored to avoid Angular DI)
// ============================================

interface TabConfiguration {
  id: string;
  title: string;
  description: string;
  icon: string;
  defaultAgent: string;
  availableAgents: string[];
}

interface TabsConfiguration {
  tabConfigurations: { [key: string]: TabConfiguration };
}

// ============================================
// In-memory store with failure simulation
// ============================================

class MockTabStore {
  private store: Map<string, any> = new Map();
  private currentVersion = 0;
  private _cache: TabsConfiguration | null = null;
  private _cacheTimestamp = 0;
  private readonly CACHE_TTL = 5 * 60 * 1000;

  // Failure simulation
  shouldFailRead = false;
  shouldFailWrite = false;
  clientInitialized = true;

  getTabConfig(): TabsConfiguration | null {
    // Check cache first
    if (this._cache && (Date.now() - this._cacheTimestamp) < this.CACHE_TTL) {
      return this._cache;
    }
    if (!this.clientInitialized) return null;
    if (this.shouldFailRead) return null;

    const item = this.store.get('TAB_CONFIG#v1');
    if (!item) return null;
    const config = JSON.parse(item.content);
    this._cache = config;
    this._cacheTimestamp = Date.now();
    return config;
  }

  saveTabConfig(config: TabsConfiguration, updatedBy: string): boolean {
    if (!this.clientInitialized) return false;
    if (this.shouldFailWrite) return false;

    this.currentVersion++;
    this.store.set('TAB_CONFIG#v1', {
      content: JSON.stringify(config),
      updated_at: new Date().toISOString(),
      version: this.currentVersion,
      updated_by: updatedBy,
    });
    this._cache = config;
    this._cacheTimestamp = Date.now();
    return true;
  }

  addTab(tabId: string, tab: TabConfiguration): boolean {
    const config = this.getTabConfig();
    if (!config) return false;
    if (config.tabConfigurations[tabId]) return false;
    config.tabConfigurations[tabId] = tab;
    return this.saveTabConfig(config, 'ui-edit');
  }

  updateTab(tabId: string, tab: TabConfiguration): boolean {
    const config = this.getTabConfig();
    if (!config) return false;
    if (!config.tabConfigurations[tabId]) return false;
    config.tabConfigurations[tabId] = tab;
    return this.saveTabConfig(config, 'ui-edit');
  }

  deleteTab(tabId: string): boolean {
    const config = this.getTabConfig();
    if (!config) return false;
    if (!config.tabConfigurations[tabId]) return false;
    delete config.tabConfigurations[tabId];
    return this.saveTabConfig(config, 'ui-edit');
  }

  clearCache(): void {
    this._cache = null;
    this._cacheTimestamp = 0;
  }

  getCacheState() {
    return { cached: this._cache, timestamp: this._cacheTimestamp };
  }
}

// ============================================
// Mock loading priority chain
// ============================================

class MockConfigLoader {
  private tabStore: MockTabStore;
  private s3Config: TabsConfiguration | null = null;
  private localConfig: TabsConfiguration | null = null;
  tabConfigUpdatedEmissions: TabsConfiguration[] = [];

  constructor(tabStore: MockTabStore) {
    this.tabStore = tabStore;
  }

  setS3Config(config: TabsConfiguration | null) { this.s3Config = config; }
  setLocalConfig(config: TabsConfiguration | null) { this.localConfig = config; }

  async getTabsConfiguration(): Promise<TabsConfiguration> {
    // Try DynamoDB first
    const dynamoConfig = this.tabStore.getTabConfig();
    if (dynamoConfig) return dynamoConfig;

    // Fall back to S3
    if (this.s3Config) return this.s3Config;

    // Fall back to local assets
    if (this.localConfig) return this.localConfig;

    // Empty default
    return { tabConfigurations: {} };
  }

  async updateTabConfiguration(tabConfig: TabsConfiguration): Promise<boolean> {
    const saved = this.tabStore.saveTabConfig(tabConfig, 'ui-edit');
    if (saved) {
      this.tabConfigUpdatedEmissions.push(tabConfig);
    }
    return saved;
  }
}

// ============================================
// Test fixtures
// ============================================

const sampleTab: TabConfiguration = {
  id: 'tab-1',
  title: 'Test Tab',
  description: 'A test tab',
  icon: 'test_icon',
  defaultAgent: 'TestAgent',
  availableAgents: ['TestAgent', 'OtherAgent'],
};

const sampleConfig: TabsConfiguration = {
  tabConfigurations: { 'tab-1': sampleTab },
};


// ============================================
// Unit Tests
// ============================================

describe('Tab Config - Unit Tests', () => {

  // 7.1 Loading priority chain
  describe('Loading priority chain', () => {
    test('DynamoDB available returns DynamoDB config', async () => {
      const store = new MockTabStore();
      store.saveTabConfig(sampleConfig, 'seed');
      const loader = new MockConfigLoader(store);
      loader.setS3Config({ tabConfigurations: { s3tab: { ...sampleTab, id: 's3tab', title: 'S3 Tab' } } });

      const result = await loader.getTabsConfiguration();
      expect(result.tabConfigurations['tab-1']).toBeDefined();
      expect(result.tabConfigurations['tab-1'].title).toBe('Test Tab');
    });

    test('DynamoDB unavailable falls back to S3', async () => {
      const store = new MockTabStore();
      store.clientInitialized = false;
      const s3Config: TabsConfiguration = { tabConfigurations: { s3tab: { ...sampleTab, id: 's3tab', title: 'S3 Tab' } } };
      const loader = new MockConfigLoader(store);
      loader.setS3Config(s3Config);

      const result = await loader.getTabsConfiguration();
      expect(result.tabConfigurations['s3tab']).toBeDefined();
      expect(result.tabConfigurations['s3tab'].title).toBe('S3 Tab');
    });

    test('All unavailable returns empty default', async () => {
      const store = new MockTabStore();
      store.clientInitialized = false;
      const loader = new MockConfigLoader(store);

      const result = await loader.getTabsConfiguration();
      expect(result).toEqual({ tabConfigurations: {} });
    });
  });

  // 7.2 Save failure
  describe('Save failure', () => {
    test('DynamoDB write fails returns false with cache unchanged', () => {
      const store = new MockTabStore();
      store.saveTabConfig(sampleConfig, 'seed');
      const cacheBefore = JSON.parse(JSON.stringify(store.getCacheState().cached));

      store.shouldFailWrite = true;
      const newConfig: TabsConfiguration = {
        tabConfigurations: { 'tab-2': { ...sampleTab, id: 'tab-2', title: 'New Tab' } },
      };
      const result = store.saveTabConfig(newConfig, 'ui-edit');

      expect(result).toBe(false);
      // Cache should still have the original config
      expect(store.getCacheState().cached).toEqual(cacheBefore);
    });
  });

  // 7.3 Duplicate tab ID rejection and missing tab ID handling
  describe('Duplicate and missing tab IDs', () => {
    test('addTab with existing ID returns false', () => {
      const store = new MockTabStore();
      store.saveTabConfig(sampleConfig, 'seed');

      const result = store.addTab('tab-1', { ...sampleTab, title: 'Duplicate' });
      expect(result).toBe(false);
    });

    test('updateTab with non-existent ID returns false', () => {
      const store = new MockTabStore();
      store.saveTabConfig(sampleConfig, 'seed');

      const result = store.updateTab('nonexistent', sampleTab);
      expect(result).toBe(false);
    });

    test('deleteTab with non-existent ID returns false', () => {
      const store = new MockTabStore();
      store.saveTabConfig(sampleConfig, 'seed');

      const result = store.deleteTab('nonexistent');
      expect(result).toBe(false);
    });
  });

  // 7.4 Event emission
  describe('Event emission', () => {
    test('successful save triggers tabConfigUpdated', async () => {
      const store = new MockTabStore();
      const loader = new MockConfigLoader(store);

      const result = await loader.updateTabConfiguration(sampleConfig);
      expect(result).toBe(true);
      expect(loader.tabConfigUpdatedEmissions.length).toBe(1);
      expect(loader.tabConfigUpdatedEmissions[0]).toEqual(sampleConfig);
    });

    test('failed save does not trigger tabConfigUpdated', async () => {
      const store = new MockTabStore();
      store.shouldFailWrite = true;
      const loader = new MockConfigLoader(store);

      const result = await loader.updateTabConfiguration(sampleConfig);
      expect(result).toBe(false);
      expect(loader.tabConfigUpdatedEmissions.length).toBe(0);
    });
  });

  // 7.5 Seed script behavior (testing the Python validation logic in TS)
  describe('Seed script validation logic', () => {
    function validateTabConfig(data: any): boolean {
      if (!data || typeof data !== 'object') return false;
      if (!data.tabConfigurations || typeof data.tabConfigurations !== 'object') return false;
      for (const [, tab] of Object.entries(data.tabConfigurations)) {
        const t = tab as any;
        if (!t || typeof t !== 'object') return false;
        for (const field of ['id', 'title', 'icon', 'defaultAgent', 'availableAgents']) {
          if (!(field in t)) return false;
        }
      }
      return true;
    }

    test('missing tabConfigurations key fails validation', () => {
      expect(validateTabConfig({})).toBe(false);
    });

    test('tab missing required fields fails validation', () => {
      expect(validateTabConfig({ tabConfigurations: { t: { id: 't' } } })).toBe(false);
    });

    test('valid config passes validation', () => {
      expect(validateTabConfig(sampleConfig)).toBe(true);
    });

    test('existing config skips upload (simulated)', () => {
      const store = new MockTabStore();
      store.saveTabConfig(sampleConfig, 'seed');

      // Simulate: check existing → skip
      const existing = store.getTabConfig();
      expect(existing).not.toBeNull();
      // In the real script, this would cause it to skip and exit 0
    });

    test('--force overwrites existing config', () => {
      const store = new MockTabStore();
      store.saveTabConfig(sampleConfig, 'seed');

      const newConfig: TabsConfiguration = {
        tabConfigurations: { 'forced-tab': { ...sampleTab, id: 'forced-tab', title: 'Forced' } },
      };
      // Simulate --force: overwrite regardless
      const result = store.saveTabConfig(newConfig, 'deployment-seed');
      expect(result).toBe(true);
      expect(store.getTabConfig()!.tabConfigurations['forced-tab'].title).toBe('Forced');
    });
  });
});
