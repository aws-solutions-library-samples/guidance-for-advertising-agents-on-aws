import { Injectable } from '@angular/core';

/**
 * Visualization Cache Service
 * 
 * Provides caching for expensive visualization data processing operations
 * to prevent repeated calculations during Angular change detection cycles.
 */
@Injectable({
  providedIn: 'root'
})
export class VisualizationCacheService {
  private cache = new Map<string, any>();
  private processingFlags = new Set<string>();

  constructor() { }

  /**
   * Get cached result or compute and cache if not exists
   */
  getOrCompute<T>(key: string, computeFn: () => T): T {
    if (this.cache.has(key)) {
      return this.cache.get(key);
    }

    // Prevent recursive computation
    if (this.processingFlags.has(key)) {
      return computeFn(); // Return uncached result to avoid infinite loop
    }

    this.processingFlags.add(key);
    try {
      const result = computeFn();
      this.cache.set(key, result);
      this.enforceCacheLimit();
      return result;
    } finally {
      this.processingFlags.delete(key);
    }
  }

  /**
   * Generate a cache key from input data
   */
  generateKey(prefix: string, data: any): string {
    try {
      // Create a stable hash of the data
      const dataStr = JSON.stringify(data);
      const hash = this.simpleHash(dataStr);
      return `${prefix}_${hash}`;
    } catch (error) {
      // Fallback to timestamp if JSON.stringify fails
      return `${prefix}_${Date.now()}_${Math.random()}`;
    }
  }

  /**
   * Clear cache for specific prefix or all cache
   */
  clearCache(prefix?: string): void {
    if (prefix) {
      const keysToDelete = Array.from(this.cache.keys()).filter(key => key.startsWith(prefix));
      keysToDelete.forEach(key => this.cache.delete(key));
    } else {
      this.cache.clear();
    }
  }

  /**
   * Get cache size for debugging
   */
  getCacheSize(): number {
    return this.cache.size;
  }

  /**
   * Get cache statistics for debugging
   */
  getCacheStats(): { size: number; keys: string[] } {
    return {
      size: this.cache.size,
      keys: Array.from(this.cache.keys())
    };
  }

  /**
   * Log cache statistics to console (for debugging)
   */
  logCacheStats(): void {
    const stats = this.getCacheStats();
    console.log('Visualization Cache Stats:', stats);
  }

  /**
   * Set maximum cache size to prevent memory leaks
   */
  private maxCacheSize = 100;

  /**
   * Enforce cache size limit by removing oldest entries
   */
  private enforceCacheLimit(): void {
    if (this.cache.size > this.maxCacheSize) {
      const keysToDelete = Array.from(this.cache.keys()).slice(0, this.cache.size - this.maxCacheSize);
      keysToDelete.forEach(key => this.cache.delete(key));
    }
  }

  /**
   * Simple hash function for cache keys
   */
  private simpleHash(str: string): string {
    let hash = 0;
    if (str.length === 0) return hash.toString();
    
    for (let i = 0; i < str.length; i++) {
      const char = str.charCodeAt(i);
      hash = ((hash << 5) - hash) + char;
      hash = hash & hash; // Convert to 32-bit integer
    }
    
    return Math.abs(hash).toString(36);
  }

  /**
   * Cache visualization data processing results
   */
  cacheVisualizationData(componentName: string, inputData: any, processedData: any): void {
    const key = this.generateKey(`viz_${componentName}`, inputData);
    this.cache.set(key, processedData);
  }

  /**
   * Get cached visualization data
   */
  getCachedVisualizationData(componentName: string, inputData: any): any {
    const key = this.generateKey(`viz_${componentName}`, inputData);
    return this.cache.get(key);
  }

  /**
   * Check if visualization data is cached
   */
  hasVisualizationData(componentName: string, inputData: any): boolean {
    const key = this.generateKey(`viz_${componentName}`, inputData);
    return this.cache.has(key);
  }
}