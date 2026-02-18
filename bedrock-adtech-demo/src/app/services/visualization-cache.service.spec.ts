import { TestBed } from '@angular/core/testing';
import { VisualizationCacheService } from './visualization-cache.service';

describe('VisualizationCacheService', () => {
  let service: VisualizationCacheService;

  beforeEach(() => {
    TestBed.configureTestingModule({});
    service = TestBed.inject(VisualizationCacheService);
  });

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  it('should cache and retrieve computed values', () => {
    const testData = { test: 'data' };
    let computeCallCount = 0;
    
    const computeFn = () => {
      computeCallCount++;
      return { processed: 'result' };
    };

    const key = service.generateKey('test', testData);
    
    // First call should compute
    const result1 = service.getOrCompute(key, computeFn);
    expect(computeCallCount).toBe(1);
    expect(result1).toEqual({ processed: 'result' });

    // Second call should use cache
    const result2 = service.getOrCompute(key, computeFn);
    expect(computeCallCount).toBe(1); // Should not increment
    expect(result2).toEqual({ processed: 'result' });
  });

  it('should generate stable cache keys', () => {
    const data1 = { a: 1, b: 2 };
    const data2 = { a: 1, b: 2 };
    const data3 = { a: 1, b: 3 };

    const key1 = service.generateKey('test', data1);
    const key2 = service.generateKey('test', data2);
    const key3 = service.generateKey('test', data3);

    expect(key1).toBe(key2); // Same data should generate same key
    expect(key1).not.toBe(key3); // Different data should generate different key
  });

  it('should enforce cache size limits', () => {
    // Set a small cache limit for testing
    (service as any).maxCacheSize = 3;

    // Add items beyond the limit
    for (let i = 0; i < 5; i++) {
      const key = `test_key_${i}`;
      service.getOrCompute(key, () => ({ value: i }));
    }

    // Cache should not exceed the limit
    expect(service.getCacheSize()).toBeLessThanOrEqual(3);
  });

  it('should clear cache correctly', () => {
    service.cacheVisualizationData('test1', { data: 1 }, { result: 1 });
    service.cacheVisualizationData('test2', { data: 2 }, { result: 2 });
    service.cacheVisualizationData('other', { data: 3 }, { result: 3 });

    expect(service.getCacheSize()).toBe(3);

    // Clear specific prefix
    service.clearCache('test');
    expect(service.getCacheSize()).toBeLessThan(3);

    // Clear all
    service.clearCache();
    expect(service.getCacheSize()).toBe(0);
  });

  it('should provide cache statistics', () => {
    service.cacheVisualizationData('metrics', { test: 1 }, { result: 1 });
    service.cacheVisualizationData('allocations', { test: 2 }, { result: 2 });

    const stats = service.getCacheStats();
    expect(stats.size).toBe(2);
    expect(stats.keys.length).toBe(2);
    expect(stats.keys.some(key => key.includes('metrics'))).toBe(true);
    expect(stats.keys.some(key => key.includes('allocations'))).toBe(true);
  });
});