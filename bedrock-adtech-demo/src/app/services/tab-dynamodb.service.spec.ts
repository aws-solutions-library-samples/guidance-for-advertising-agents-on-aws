/**
 * Property-based tests for TabDynamoDBService
 * Feature: tab-config-dynamodb-migration
 *
 * These tests validate correctness properties using fast-check arbitraries
 * against an in-memory mock of the DynamoDB operations.
 */
import * as fc from 'fast-check';

// ============================================
// Types (mirrored from application-models.ts to avoid Angular DI)
// ============================================

interface TabConfiguration {
  id: string;
  title: string;
  description: string;
  icon: string;
  defaultAgent: string;
  availableAgents: string[];
  scenarios?: any[];
  visualizations?: any[];
  contextData?: any;
  contextButtonLabel?: string;
  availableCampaigns?: any[];
  agentType?: string;
}

interface TabsConfiguration {
  tabConfigurations: { [key: string]: TabConfiguration };
}

// ============================================
// Arbitraries
// ============================================

const tabConfigArb: fc.Arbitrary<TabConfiguration> = fc.record({
  id: fc.string({ minLength: 1, maxLength: 30 }).filter(s => s.trim().length > 0),
  title: fc.string({ minLength: 1, maxLength: 50 }).filter(s => s.trim().length > 0),
  description: fc.string({ minLength: 0, maxLength: 100 }),
  icon: fc.string({ minLength: 1, maxLength: 20 }).filter(s => s.trim().length > 0),
  defaultAgent: fc.string({ minLength: 1, maxLength: 30 }).filter(s => s.trim().length > 0),
  availableAgents: fc.array(fc.string({ minLength: 1, maxLength: 30 }).filter(s => s.trim().length > 0), { minLength: 1, maxLength: 5 }),
});

const tabsConfigArb: fc.Arbitrary<TabsConfiguration> = fc
  .array(
    fc.tuple(
      fc.string({ minLength: 1, maxLength: 20 }).filter(s => /^[a-zA-Z0-9_-]+$/.test(s) && !['constructor', 'prototype', '__proto__', 'toString', 'valueOf', 'hasOwnProperty'].includes(s)),
      tabConfigArb
    ),
    { minLength: 1, maxLength: 5 }
  )
  .map(entries => {
    const map: { [key: string]: TabConfiguration } = {};
    for (const [key, tab] of entries) {
      map[key] = { ...tab, id: key };
    }
    return { tabConfigurations: map };
  });

// Non-empty tabs config (at least 1 tab) for operations that need existing tabs
const nonEmptyTabsConfigArb = tabsConfigArb.filter(
  c => Object.keys(c.tabConfigurations).length >= 1
);


// ============================================
// In-memory DynamoDB mock for pure logic testing
// ============================================

class InMemoryTabStore {
  private store: Map<string, any> = new Map();
  private currentVersion = 0;

  private key(pk: string, sk: string) { return `${pk}#${sk}`; }

  getItem(pk: string, sk: string): any | null {
    return this.store.get(this.key(pk, sk)) ?? null;
  }

  putItem(pk: string, sk: string, item: any): void {
    this.store.set(this.key(pk, sk), { ...item });
  }

  getTabConfig(): TabsConfiguration | null {
    const item = this.getItem('TAB_CONFIG', 'v1');
    if (!item) return null;
    return JSON.parse(item.content);
  }

  saveTabConfig(config: TabsConfiguration, updatedBy: string): boolean {
    this.currentVersion++;
    this.putItem('TAB_CONFIG', 'v1', {
      pk: 'TAB_CONFIG',
      sk: 'v1',
      config_type: 'tab_config',
      content: JSON.stringify(config),
      updated_at: new Date().toISOString(),
      version: this.currentVersion,
      updated_by: updatedBy,
    });
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
}

// ============================================
// Validation helper (mirrors AgentConfigService.validateTabsConfiguration)
// ============================================

function validateTabsConfiguration(config: any): config is TabsConfiguration {
  if (!config || typeof config !== 'object') return false;
  if (!config.tabConfigurations || typeof config.tabConfigurations !== 'object') return false;
  for (const [, tab] of Object.entries(config.tabConfigurations)) {
    if (!tab || typeof tab !== 'object') return false;
    const t = tab as any;
    if (!t.id || !t.title || !t.icon || !t.defaultAgent || !Array.isArray(t.availableAgents)) return false;
  }
  return true;
}

// ============================================
// Property-Based Tests
// ============================================

describe('TabDynamoDBService - Property-Based Tests', () => {
  const NUM_RUNS = 100;

  // Feature: tab-config-dynamodb-migration, Property 1: Tab configuration serialization round trip
  test('Property 1: save then load produces equivalent config', () => {
    fc.assert(
      fc.property(tabsConfigArb, (config) => {
        const store = new InMemoryTabStore();
        store.saveTabConfig(config, 'test');
        const loaded = store.getTabConfig();
        expect(loaded).not.toBeNull();
        expect(loaded!.tabConfigurations).toEqual(config.tabConfigurations);
      }),
      { numRuns: NUM_RUNS }
    );
  });

  // Feature: tab-config-dynamodb-migration, Property 4: Adding a tab grows the configuration map
  test('Property 4: addTab grows map by exactly one entry', () => {
    fc.assert(
      fc.property(nonEmptyTabsConfigArb, tabConfigArb, (config, newTab) => {
        const store = new InMemoryTabStore();
        store.saveTabConfig(config, 'seed');

        const existingKeys = Object.keys(config.tabConfigurations);
        // Generate a unique key not in existing config
        const newKey = 'new-tab-' + Math.random().toString(36).slice(2, 10);
        if (existingKeys.includes(newKey)) return; // skip collision (extremely unlikely)

        const sizeBefore = Object.keys(store.getTabConfig()!.tabConfigurations).length;
        const result = store.addTab(newKey, { ...newTab, id: newKey });
        expect(result).toBe(true);

        const after = store.getTabConfig()!;
        expect(Object.keys(after.tabConfigurations).length).toBe(sizeBefore + 1);
        expect(after.tabConfigurations[newKey]).toBeDefined();
      }),
      { numRuns: NUM_RUNS }
    );
  });

  // Feature: tab-config-dynamodb-migration, Property 5: Updating a tab preserves map size and replaces content
  test('Property 5: updateTab preserves map size and replaces content', () => {
    fc.assert(
      fc.property(nonEmptyTabsConfigArb, tabConfigArb, (config, updatedTab) => {
        const store = new InMemoryTabStore();
        store.saveTabConfig(config, 'seed');

        const keys = Object.keys(config.tabConfigurations);
        const targetKey = keys[0];
        const sizeBefore = keys.length;

        const result = store.updateTab(targetKey, { ...updatedTab, id: targetKey });
        expect(result).toBe(true);

        const after = store.getTabConfig()!;
        expect(Object.keys(after.tabConfigurations).length).toBe(sizeBefore);
        expect(after.tabConfigurations[targetKey].title).toBe(updatedTab.title);
      }),
      { numRuns: NUM_RUNS }
    );
  });

  // Feature: tab-config-dynamodb-migration, Property 6: Deleting a tab shrinks the configuration map
  test('Property 6: deleteTab shrinks map by exactly one entry', () => {
    fc.assert(
      fc.property(nonEmptyTabsConfigArb, (config) => {
        const store = new InMemoryTabStore();
        store.saveTabConfig(config, 'seed');

        const keys = Object.keys(config.tabConfigurations);
        const targetKey = keys[0];
        const sizeBefore = keys.length;

        const result = store.deleteTab(targetKey);
        expect(result).toBe(true);

        const after = store.getTabConfig()!;
        expect(Object.keys(after.tabConfigurations).length).toBe(sizeBefore - 1);
        expect(after.tabConfigurations[targetKey]).toBeUndefined();
      }),
      { numRuns: NUM_RUNS }
    );
  });

  // Feature: tab-config-dynamodb-migration, Property 7: Add-then-delete is a round trip
  test('Property 7: add then delete produces original config', () => {
    fc.assert(
      fc.property(nonEmptyTabsConfigArb, tabConfigArb, (config, newTab) => {
        const store = new InMemoryTabStore();
        store.saveTabConfig(config, 'seed');

        const newKey = 'roundtrip-' + Math.random().toString(36).slice(2, 10);
        if (Object.keys(config.tabConfigurations).includes(newKey)) return;

        const original = JSON.parse(JSON.stringify(store.getTabConfig()!.tabConfigurations));

        store.addTab(newKey, { ...newTab, id: newKey });
        store.deleteTab(newKey);

        const after = store.getTabConfig()!;
        expect(after.tabConfigurations).toEqual(original);
      }),
      { numRuns: NUM_RUNS }
    );
  });

  // Feature: tab-config-dynamodb-migration, Property 8: Validation rejects invalid tab configurations
  test('Property 8: validation rejects invalid configs', () => {
    fc.assert(
      fc.property(
        fc.oneof(
          fc.constant(null),
          fc.constant(undefined),
          fc.constant(42),
          fc.constant('string'),
          fc.constant({}),
          fc.constant({ tabConfigurations: 'not-an-object' }),
          fc.constant({ tabConfigurations: { tab1: { id: 'tab1' } } }), // missing title, icon, etc.
          fc.constant({ tabConfigurations: { tab1: { id: 'tab1', title: 'T', icon: 'I', defaultAgent: 'A' } } }), // missing availableAgents
        ),
        (invalidConfig) => {
          expect(validateTabsConfiguration(invalidConfig)).toBe(false);
        }
      ),
      { numRuns: NUM_RUNS }
    );
  });
});
