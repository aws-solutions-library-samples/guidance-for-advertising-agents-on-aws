import { Component, Input, ChangeDetectionStrategy, ChangeDetectorRef, OnChanges, SimpleChanges } from '@angular/core';
import { TextUtils } from '../../../utils/text-utils';
import { VisualizationCacheService } from '../../../services/visualization-cache.service';

/**
 * Segments Visualization Component
 * 
 * Displays audience segments, content categories, and market segments analysis
 * in a comprehensive card-based layout with performance metrics and insights.
 * 
 * @example
 * ```html
 * <app-segments-visualization [segmentCards]="segmentData"></app-segments-visualization>
 * ```
 * 
 * @example JSON Structure:
 * ```json
 * {
 *   "visualizationType": "segments",
 *   "title": "Audience Segment Performance",
 *   "subtitle": "Q4 2024 Targeting Analysis",
 *   "segments": [
 *     {
 *       "primaryLabel": "Premium Shoppers",
 *       "secondaryLabel": "High-Value Customers",
 *       "description": "Affluent consumers with high purchase intent and brand loyalty",
 *       "actualValue": "2.8% CTR",
 *       "targetValue": "2.5% CTR",
 *       "forecastedValue": "3.1% CTR",
 *       "actualLabel": "Current Performance",
 *       "targetLabel": "Target Goal",
 *       "forecastedLabel": "Projected Performance",
 *       "primaryMetric": {
 *         "label": "Conversion Rate",
 *         "value": "4.2%"
 *       },
 *       "secondaryMetric": {
 *         "label": "Average Order Value",
 *         "value": "$185"
 *       },
 *       "budgetAmount": "$750,000",
 *       "statusIndicator": "optimized",
 *       "confidenceLevel": "high",
 *       "performanceLevel": "high",
 *       "insights": [
 *         "Exceeding performance targets consistently",
 *         "Strong engagement with premium product lines",
 *         "High lifetime value potential"
 *       ],
 *       "risks": [
 *         "Limited audience size may constrain scale",
 *         "Premium pricing sensitivity during economic uncertainty"
 *       ],
 *       "recommendations": [
 *         "Increase budget allocation by 15%",
 *         "Expand to lookalike audiences",
 *         "Test premium creative variations"
 *       ],
 *       "metadata": {
 *         "size": "2.3M users",
 *         "growth": "+12% QoQ",
 *         "engagement": "High"
 *       }
 *     },
 *     {
 *       "primaryLabel": "Young Professionals",
 *       "secondaryLabel": "Career-Focused 25-35",
 *       "description": "Urban professionals with disposable income and tech-savvy behavior",
 *       "actualValue": "2.1% CTR",
 *       "targetValue": "2.3% CTR",
 *       "forecastedValue": "2.4% CTR",
 *       "primaryMetric": {
 *         "label": "Engagement Rate",
 *         "value": "6.8%"
 *       },
 *       "secondaryMetric": {
 *         "label": "Cost per Acquisition",
 *         "value": "$42"
 *       },
 *       "budgetAmount": "$500,000",
 *       "statusIndicator": "active",
 *       "confidenceLevel": "medium",
 *       "performanceLevel": "medium",
 *       "insights": [
 *         "Strong mobile engagement patterns",
 *         "Responsive to video content formats"
 *       ],
 *       "risks": [
 *         "High competition for attention",
 *         "Platform algorithm changes impact reach"
 *       ],
 *       "recommendations": [
 *         "Optimize for mobile-first experience",
 *         "Increase video content allocation"
 *       ],
 *       "metadata": {
 *         "size": "4.1M users",
 *         "growth": "+8% QoQ",
 *         "engagement": "Medium-High"
 *       }
 *     }
 *   ],
 *   "summary": {
 *     "totalSegments": 6,
 *     "activeSegments": 5,
 *     "topPerformer": "Premium Shoppers",
 *     "growthOpportunity": "Young Professionals expansion potential"
 *   }
 * }
 * ```
 * 
 * @features
 * - Segment performance cards with dual metrics
 * - Target vs actual vs forecasted comparisons
 * - Budget allocation and status indicators
 * - Performance level visualization
 * - Detailed insights and risk assessment
 * - Actionable recommendations
 * - Metadata display (size, growth, engagement)
 * 
 * @useCases
 * - Audience segment analysis
 * - Content type performance
 * - Market segment optimization
 * - Behavioral cohorts
 * - Demographic targeting
 */
@Component({
  selector: 'app-segments-visualization',
  templateUrl: './segments-visualization.component.html',
  styleUrls: ['./segments-visualization.component.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class SegmentsVisualizationComponent implements OnChanges {
  @Input() segmentCards: any;
  @Input() compactMode: boolean = false;

  // Processed data cache
  private processedData: any = null;
  private lastInputHash: string = '';

  constructor(
    private cdr: ChangeDetectorRef,
    private cacheService: VisualizationCacheService
  ) { }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['segmentCards']) {
      this.processVisualizationData();
      this.cdr.markForCheck();
    }
  }

  private processVisualizationData(): void {
    if (!this.segmentCards) {
      this.processedData = null;
      return;
    }

    const currentHash = this.cacheService.generateKey('segments', this.segmentCards);
    
    // Only reprocess if data has changed
    if (this.lastInputHash === currentHash && this.processedData) {
      return;
    }

    // Check cache first
    const cached = this.cacheService.getCachedVisualizationData('segments', this.segmentCards);
    if (cached) {
      this.processedData = cached;
      this.lastInputHash = currentHash;
      return;
    }

    // Process data once and cache it
    const segments = this.segmentCards?.segments || [];
    this.processedData = {
      segments: segments,
      processedSegments: segments.map((segment: any) => ({
        ...segment,
        name: this.getSegmentName(segment),
        description: this.getSegmentDescription(segment),
        primaryMetrics: this.getPrimaryMetrics(segment),
        detailedProperties: this.getDetailedProperties(segment)
      }))
    };

    // Cache the processed data
    this.cacheService.cacheVisualizationData('segments', this.segmentCards, this.processedData);
    this.lastInputHash = currentHash;
  }

  // Helper method to get segment name (generic or specific)
  getSegmentName(segment: any): string {
    return segment.primaryLabel || segment.name || segment.title || 'Unnamed Segment';
  }

  // Helper method to get segment description (generic or specific)
  getSegmentDescription(segment: any): string {
    return segment.description || segment.secondaryLabel || '';
  }

  // Helper method to get primary metrics for a segment
  getPrimaryMetrics(segment: any): Array<{label: string, value: string, type: string}> {
    const metrics: Array<{label: string, value: string, type: string}> = [];
    
    // Generic metrics first
    if (segment.actualValue) {
      metrics.push({label: segment.actualLabel || 'Current', value: segment.actualValue, type: 'actual'});
    }
    if (segment.targetValue) {
      metrics.push({label: segment.targetLabel || 'Target', value: segment.targetValue, type: 'target'});
    }
    if (segment.forecastedValue) {
      metrics.push({label: segment.forecastedLabel || 'Forecast', value: segment.forecastedValue, type: 'forecast'});
    }
    
    // Legacy metrics as fallback
    if (segment.win_rate) {
      metrics.push({label: 'Win Rate', value: segment.win_rate, type: 'win-rate'});
    }
    if (segment.expected_ctr) {
      metrics.push({label: 'CTR', value: segment.expected_ctr, type: 'ctr'});
    }
    if (segment.expected_cvr) {
      metrics.push({label: 'CVR', value: segment.expected_cvr, type: 'cvr'});
    }
    if (segment.expected_roas) {
      metrics.push({label: 'ROAS', value: segment.expected_roas, type: 'roas'});
    }
    
    return metrics;
  }

  // Helper method to get detailed properties for a segment
  getDetailedProperties(segment: any): Array<{label: string, value: string, isPrimary?: boolean}> {
    const properties: Array<{label: string, value: string, isPrimary?: boolean}> = [];
    
    // Generic properties first
    if (segment.primaryMetric) {
      properties.push({
        label: segment.primaryMetric.label || 'Primary Metric',
        value: segment.primaryMetric.value || '',
        isPrimary: true
      });
    }
    if (segment.secondaryMetric) {
      properties.push({
        label: segment.secondaryMetric.label || 'Secondary Metric',
        value: segment.secondaryMetric.value || ''
      });
    }
    if (segment.budgetAmount || segment.allocation) {
      const budget = segment.budgetAmount || (segment.allocation && (segment.allocation.recommendedBudget || segment.allocation));
      properties.push({
        label: 'Budget Allocation',
        value: typeof budget === 'string' ? budget : this.formatBudget(budget),
        isPrimary: true
      });
    }
    if (segment.statusIndicator) {
      properties.push({label: 'Status', value: segment.statusIndicator});
    }
    if (segment.confidenceLevel) {
      properties.push({label: 'Confidence Level', value: segment.confidenceLevel});
    }
    
    // Legacy properties as fallback
    if (segment.bid_range || segment.new_bid_range) {
      properties.push({
        label: segment.new_bid_range ? 'New Bid Range' : 'Recommended Bid Range',
        value: segment.bid_range || segment.new_bid_range,
        isPrimary: true
      });
    }
    if (segment.channels?.length) {
      properties.push({label: 'Channels', value: segment.channels.join(', ')});
    }
    if (segment.target_win_rate) {
      properties.push({label: 'Target Win Rate', value: segment.target_win_rate});
    }
    if (segment.required_cvr) {
      properties.push({label: 'Required CVR', value: segment.required_cvr});
    }
    if (segment.min_roas) {
      properties.push({label: 'Min ROAS', value: segment.min_roas});
    }
    if (segment.overlap_risk) {
      properties.push({label: 'Overlap Risk', value: segment.overlap_risk});
    }
    if (segment.monitoring_frequency) {
      properties.push({label: 'Monitoring Frequency', value: segment.monitoring_frequency});
    }
    if (segment.adjustment_threshold) {
      properties.push({label: 'Adjustment Threshold', value: segment.adjustment_threshold});
    }
    if (segment.expected_performance) {
      properties.push({label: 'Expected Performance', value: segment.expected_performance});
    }
    if (segment.confidence) {
      properties.push({label: 'Confidence Level', value: segment.confidence});
    }
    if (segment.audience_size) {
      properties.push({label: 'Audience Size', value: segment.audience_size});
    }
    if (segment.competitive_position) {
      properties.push({label: 'Competitive Position', value: segment.competitive_position});
    }
    if (segment.seasonal_trend) {
      properties.push({label: 'Seasonal Trend', value: segment.seasonal_trend});
    }
    
    return properties;
  }

  // Helper method to get insights (generic or specific)
  getInsights(segment: any): string[] {
    return segment.insights || segment.key_insights || [];
  }

  // Helper method to get risks (generic or specific)
  getRisks(segment: any): string[] {
    return segment.risks || segment.risk_factors || [];
  }

  // Helper method to get recommendations (generic or specific)
  getRecommendations(segment: any): string[] {
    if (segment.recommendations && Array.isArray(segment.recommendations)) {
      return segment.recommendations;
    }
    if (segment.optimization_strategies && Array.isArray(segment.optimization_strategies)) {
      return segment.optimization_strategies;
    }
    if (segment.recommendation && typeof segment.recommendation === 'string') {
      return [segment.recommendation];
    }
    return [];
  }

  // Helper method to format budget values using centralized TextUtils
  formatBudget(budget: any): string {
    return TextUtils.formatBudget(budget);
  }

  // Legacy method - replaced with centralized formatting
  _oldFormatBudget(budget: any): string {
    if (!budget) return '';
    
    const budgetStr = budget.toString();
    
    // If it already starts with $, return as is
    if (budgetStr.startsWith('$')) {
      return budgetStr;
    }
    
    // If it's a number, format it with $ prefix and commas
    const numericValue = parseFloat(budgetStr.replace(/[^0-9.-]/g, ''));
    if (!isNaN(numericValue)) {
      return `$${numericValue.toLocaleString()}`;
    }
    
    // Otherwise, just add $ prefix
    return `$${budgetStr}`;
  }
} 