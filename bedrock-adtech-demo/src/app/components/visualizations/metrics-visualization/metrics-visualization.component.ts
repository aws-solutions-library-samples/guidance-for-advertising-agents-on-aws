import { Component, Input, ChangeDetectionStrategy, ChangeDetectorRef, OnChanges, SimpleChanges } from '@angular/core';
import { TextUtils } from '../../../utils/text-utils';
import { VisualizationCacheService } from '../../../services/visualization-cache.service';

@Component({
  selector: 'app-metrics-visualization',
  templateUrl: './metrics-visualization.component.html',
  styleUrls: ['./metrics-visualization.component.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class MetricsVisualizationComponent implements OnChanges {
  @Input() metricData: any;
  @Input() compactMode: boolean = false;

  // Processed data cache
  private processedData: any = null;
  private lastInputHash: string = '';

  constructor(
    private cdr: ChangeDetectorRef,
    private cacheService: VisualizationCacheService
  ) { }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['metricData']) {
      this.processVisualizationData();
      this.cdr.markForCheck();
    }
  }

  private processVisualizationData(): void {
    if (!this.metricData) {
      this.processedData = null;
      return;
    }

    const currentHash = this.cacheService.generateKey('metrics', this.metricData);

    // Only reprocess if data has changed
    if (this.lastInputHash === currentHash && this.processedData) {
      return;
    }

    // Check cache first
    const cached = this.cacheService.getCachedVisualizationData('metrics', this.metricData);
    if (cached) {
      this.processedData = cached;
      this.lastInputHash = currentHash;
      return;
    }

    // Process data once and cache it
    this.processedData = {
      isStructured: this.computeIsStructuredMetrics(),
      metricsArray: this.computeMetricsArray(),
      processedMetrics: this.computeProcessedMetrics()
    };

    // Cache the processed data
    this.cacheService.cacheVisualizationData('metrics', this.metricData, this.processedData);
    this.lastInputHash = currentHash;
  }

  // TrackBy functions
  trackByIndex = (index: number): number => {
    return index;
  }

  trackByKey = (index: number, item: { key: string; value: any }): string => {
    return item.key;
  }

  // Public getter methods for template
  isStructuredMetrics(): boolean {
    return this.processedData?.isStructured || false;
  }

  getMetricsArray(): any[] {
    return this.processedData?.metricsArray || [];
  }

  getProcessedMetrics(): any[] {
    return this.processedData?.processedMetrics || [];
  }

  // Private computation methods (called once during processing)
  private computeIsStructuredMetrics(): boolean {
    const metrics = this.metricData;

    // Handle the case where metrics is a single object with visualizationType and metrics array
    if (metrics && typeof metrics === 'object' && !Array.isArray(metrics)) {
      if (metrics.metrics && Array.isArray(metrics.metrics)) {
        // Check the first metric in the array to determine structure
        const firstMetric = metrics.metrics[0];
        if (firstMetric && typeof firstMetric === 'object') {
          return !!(firstMetric.primaryLabel && firstMetric.items);
        }
        return true;
      }
    }

    // Handle case where metricData itself is an array
    if (!Array.isArray(metrics) || metrics.length === 0) {
      return false;
    }

    const firstMetric = metrics[0];

    // Check if it's NEW STANDARDIZED structured format (has subMetrics array)
    if (firstMetric && typeof firstMetric === 'object') {
      // NEW FORMAT: Check for standardized subMetrics structure
      const hasStandardizedFormat = !!(
        firstMetric.name &&
        Array.isArray(firstMetric.subMetrics) &&
        firstMetric.subMetrics.length > 0
      );

      // GENERIC FORMAT: Check for generic card structure
      const hasGenericFormat = !!(
        firstMetric.primaryLabel &&
        (firstMetric.items || firstMetric.metrics || firstMetric.subMetrics)
      );

      // LEGACY FORMAT: Check for legacy structured properties
      const hasLegacyStructuredProps = !!(
        firstMetric.product_line ||
        firstMetric.projected_roas ||
        firstMetric.win_rate ||
        firstMetric.recommended_budget ||
        firstMetric.expected_cvr ||
        (firstMetric.name && !firstMetric.subMetrics) ||
        firstMetric.segment
      );

      // SIMPLE FORMAT: Check for simple label/value format
      const hasSimpleProps = !!(firstMetric.label && (firstMetric.value !== undefined));

      // Return true if it's standardized format OR generic format OR legacy structured format (but not simple format)
      return hasStandardizedFormat || hasGenericFormat || (hasLegacyStructuredProps && !hasSimpleProps);
    }

    return false;
  }

  // Helper method to extract metric properties dynamically
  getMetricProperties(metricItem: any): { key: string; value: any }[] {
    if (!metricItem || typeof metricItem !== 'object') {
      return [];
    }

    // Exclude header properties that shouldn't be displayed as metrics
    const excludedProps = ['product_line', 'name', 'segment', 'id', 'title', 'primaryLabel', 'items', 'metrics', 'subMetrics'];

    return Object.keys(metricItem)
      .filter(key => !excludedProps.includes(key) && metricItem[key] != null)
      .map(key => ({ key, value: metricItem[key] }));
  }

  // Helper method to get generic metric items
  getGenericMetricItems(metricItem: any): any[] {
    if (!metricItem) return [];

    // Check for generic structure
    if (metricItem.items) return metricItem.items;
    if (metricItem.metrics) return metricItem.metrics;
    if (metricItem.subMetrics) return metricItem.subMetrics;

    return [];
  }

  // Helper method to format metric values
  formatMetricValue(key: string, value: any): string {
    const keyLower = key.toLowerCase();

    if (keyLower.includes('roas') || keyLower.includes('ratio')) {
      return `${value}${value.toString().includes('x') ? '' : 'x'}`;
    }

    if (keyLower.includes('rate') || keyLower.includes('cvr') || keyLower.includes('ctr') || keyLower.includes('percentage')) {
      return value.toString().includes('%') ? value : `${value}%`;
    }

    if (keyLower.includes('budget') || keyLower.includes('cost') || keyLower.includes('price') || keyLower.includes('cpa')) {
      return value.toString().replace(/[$€£]/g, '');
    }

    return value.toString();
  }

  // Helper method to format metric labels
  formatMetricLabel(key: string): string {
    const labelMap: { [key: string]: string } = {
      'projected_roas': 'ROAS',
      'win_rate': 'Win Rate',
      'recommended_budget': 'Budget',
      'expected_cvr': 'CVR',
      'expected_ctr': 'CTR',
      'cost_per_acquisition': 'CPA',
      'cost_per_click': 'CPC',
      'conversion_rate': 'CVR',
      'click_through_rate': 'CTR',
      'return_on_ad_spend': 'ROAS'
    };

    return labelMap[key] || key.split('_').map(word =>
      word.charAt(0).toUpperCase() + word.slice(1)
    ).join(' ');
  }

  getKeys(obj: any): string[] {
    return Object.keys(obj);
  }

  private computeMetricsArray(): any[] {
    if (!this.metricData) return [];

    // Handle new visualizationType format where metrics is an array
    if (this.metricData.metrics && Array.isArray(this.metricData.metrics)) {
      return this.metricData.metrics;
    }

    // Handle case where metricData itself is an array
    if (Array.isArray(this.metricData)) {
      return this.metricData;
    }

    return [];
  }

  private computeProcessedMetrics(): any[] {
    const metricsArray = this.computeMetricsArray();
    return metricsArray.map(metric => ({
      ...metric,
      properties: this.getMetricProperties(metric),
      genericItems: this.getGenericMetricItems(metric)
    }));
  }
}