import { Component, Input, ChangeDetectionStrategy, ChangeDetectorRef, OnChanges, SimpleChanges } from '@angular/core';
import { TextUtils } from '../../../utils/text-utils';
import { VisualizationCacheService } from '../../../services/visualization-cache.service';

/**
 * Channels Visualization Component
 * 
 * Displays channel/publisher analysis, platform performance, and placement optimization
 * in a card-based layout with detailed metrics and actionable insights.
 * 
 * @example
 * ```html
 * <app-channels-visualization [channelCards]="channelData"></app-channels-visualization>
 * ```
 * 
 * @example JSON Structure:
 * ```json
 * {
 *   "visualizationType": "channels",
 *   "title": "Publisher Safety Assessment",
 *   "subtitle": "Brand Safety Analysis for Premium Inventory",
 *   "channels": [
 *     {
 *       "primaryLabel": "Premium News Network",
 *       "secondaryLabel": "News & Information",
 *       "description": "Leading news publisher with high-quality editorial content",
 *       "primaryScore": {
 *         "label": "Safety Score",
 *         "value": "95/100"
 *       },
 *       "secondaryScore": {
 *         "label": "Viewability",
 *         "value": "87%"
 *       },
 *       "qualityScore": "Excellent",
 *       "statusIndicator": "verified",
 *       "targetValue": "90%",
 *       "actualValue": "92%",
 *       "forecastedValue": "94%",
 *       "targetLabel": "Target Viewability",
 *       "actualLabel": "Current Viewability",
 *       "forecastedLabel": "Projected Viewability",
 *       "confidenceLevel": "high",
 *       "insights": [
 *         "Consistently high brand safety scores",
 *         "Strong audience engagement metrics"
 *       ],
 *       "risks": [
 *         "Premium pricing may limit scale"
 *       ],
 *       "opportunities": [
 *         "Expand to mobile inventory",
 *         "Negotiate preferred rates for volume"
 *       ],
 *       "recommendation": "Increase allocation to premium inventory",
 *       "metadata": {
 *         "category": "News & Information",
 *         "reach": "2.5M monthly visitors",
 *         "engagement": "4.2 minutes average session"
 *       }
 *     },
 *     {
 *       "primaryLabel": "Social Video Platform",
 *       "secondaryLabel": "Social Media",
 *       "description": "Popular video-first social platform with young demographics",
 *       "primaryScore": {
 *         "label": "Engagement Rate",
 *         "value": "8.5%"
 *       },
 *       "secondaryScore": {
 *         "label": "Completion Rate",
 *         "value": "72%"
 *       },
 *       "qualityScore": "Good",
 *       "statusIndicator": "active",
 *       "confidenceLevel": "medium",
 *       "insights": [
 *         "High engagement with video content",
 *         "Strong performance in 18-34 demographic"
 *       ],
 *       "risks": [
 *         "Algorithm changes may impact reach",
 *         "Brand safety concerns with user-generated content"
 *       ],
 *       "opportunities": [
 *         "Leverage trending content formats",
 *         "Expand to emerging markets"
 *       ],
 *       "recommendation": "Monitor brand safety closely while scaling"
 *     }
 *   ],
 *   "summary": {
 *     "totalChannels": 8,
 *     "activeChannels": 6,
 *     "topPerformer": "Premium News Network",
 *     "recommendedFocus": "Premium inventory with strong safety scores"
 *   }
 * }
 * ```
 * 
 * @features
 * - Channel performance cards with dual metrics
 * - Quality scores and status indicators
 * - Target vs actual vs forecasted values
 * - Confidence levels and risk assessment
 * - Actionable insights and opportunities
 * - Detailed metadata display
 * 
 * @useCases
 * - Publisher safety assessments
 * - Channel performance analysis
 * - Platform-specific strategies
 * - Inventory availability
 * - Cross-channel optimization
 */
@Component({
  selector: 'app-channels-visualization',
  templateUrl: './channels-visualization.component.html',
  styleUrls: ['./channels-visualization.component.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class ChannelsVisualizationComponent implements OnChanges {
  @Input() channelCards: any;
  @Input() compactMode: boolean = false;

  // Processed data cache
  private processedData: any = null;
  private lastInputHash: string = '';

  constructor(
    private cdr: ChangeDetectorRef,
    private cacheService: VisualizationCacheService
  ) { }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['channelCards']) {
      this.processVisualizationData();
      this.cdr.markForCheck();
    }
  }

  private processVisualizationData(): void {
    if (!this.channelCards) {
      this.processedData = null;
      return;
    }

    const currentHash = this.cacheService.generateKey('channels', this.channelCards);
    
    // Only reprocess if data has changed
    if (this.lastInputHash === currentHash && this.processedData) {
      return;
    }

    // Check cache first
    const cached = this.cacheService.getCachedVisualizationData('channels', this.channelCards);
    if (cached) {
      this.processedData = cached;
      this.lastInputHash = currentHash;
      return;
    }

    // Process data once and cache it
    const channels = this.channelCards?.channels || [];
    this.processedData = {
      channels: channels,
      processedChannels: channels.map((channel: any) => ({
        ...channel,
        name: this.getChannelName(channel),
        description: this.getChannelDescription(channel),
        primaryScores: this.getPrimaryScores(channel),
        detailedProperties: this.getDetailedProperties(channel)
      }))
    };

    // Cache the processed data
    this.cacheService.cacheVisualizationData('channels', this.channelCards, this.processedData);
    this.lastInputHash = currentHash;
  }

  // Helper method to get channel name (generic or specific)
  getChannelName(channel: any): string {
    return channel.primaryLabel || channel.name || channel.title || 'Unnamed Channel';
  }

  // Helper method to get channel description (generic or specific)
  getChannelDescription(channel: any): string {
    return channel.description || channel.secondaryLabel || '';
  }

  // Helper method to get primary scores for a channel
  getPrimaryScores(channel: any): Array<{ label: string, value: string, type: string }> {
    const scores: Array<{ label: string, value: string, type: string }> = [];

    // Generic scores first
    if (channel.primaryScore) {
      scores.push({
        label: channel.primaryScore.label || 'Primary Score',
        value: channel.primaryScore.value,
        type: 'primary'
      });
    }
    if (channel.secondaryScore) {
      scores.push({
        label: channel.secondaryScore.label || 'Secondary Score',
        value: channel.secondaryScore.value,
        type: 'secondary'
      });
    }
    if (channel.qualityScore) {
      scores.push({
        label: 'Quality Score',
        value: channel.qualityScore,
        type: 'quality'
      });
    }

    // Legacy scores as fallback
    if (channel.safety_score) {
      scores.push({ label: 'Safety', value: `${channel.safety_score}/100`, type: 'safety' });
    }
    if (channel.win_rate) {
      scores.push({ label: 'Win Rate', value: channel.win_rate, type: 'performance' });
    }
    if (channel.expected_ctr) {
      scores.push({ label: 'CTR', value: channel.expected_ctr, type: 'ctr' });
    }
    if (channel.expected_cvr) {
      scores.push({ label: 'CVR', value: channel.expected_cvr, type: 'cvr' });
    }
    if (channel.expected_roas) {
      scores.push({ label: 'ROAS', value: channel.expected_roas, type: 'roas' });
    }
    if (channel.expected_performance) {
      scores.push({ label: 'Performance', value: channel.expected_performance, type: 'performance' });
    }

    return scores;
  }

  // Helper method to get detailed properties for a channel
  getDetailedProperties(channel: any): Array<{ label: string, value: string, isHighlight?: boolean }> {
    const properties: Array<{ label: string, value: string, isHighlight?: boolean }> = [];

    // Generic properties first
    if (channel.statusIndicator) {
      properties.push({
        label: 'Status',
        value: channel.statusIndicator,
        isHighlight: true
      });
    }
    if (channel.targetValue) {
      properties.push({
        label: channel.targetLabel || 'Target',
        value: channel.targetValue,
        isHighlight: true
      });
    }
    if (channel.actualValue) {
      properties.push({
        label: channel.actualLabel || 'Current',
        value: channel.actualValue
      });
    }
    if (channel.forecastedValue) {
      properties.push({
        label: channel.forecastedLabel || 'Forecast',
        value: channel.forecastedValue
      });
    }
    if (channel.confidenceLevel) {
      properties.push({
        label: 'Confidence',
        value: channel.confidenceLevel,
        isHighlight: channel.confidenceLevel === 'high'
      });
    }

    // Legacy properties as fallback
    if (channel.brand_fit) {
      properties.push({
        label: 'Brand Fit',
        value: channel.brand_fit,
        isHighlight: channel.brand_fit === 'excellent'
      });
    }
    if (channel.category) {
      properties.push({ label: 'Category', value: channel.category });
    }
    if (channel.audience_size) {
      properties.push({ label: 'Audience Size', value: channel.audience_size });
    }
    if (channel.audience_match) {
      properties.push({ label: 'Audience Match', value: channel.audience_match });
    }
    if (channel.recommended_bid || channel.bid_range) {
      properties.push({
        label: 'Recommended Bid',
        value: channel.recommended_bid || channel.bid_range,
        isHighlight: true
      });
    }
    if (channel.device_optimization) {
      properties.push({ label: 'Device Optimization', value: channel.device_optimization });
    }
    if (channel.confidence) {
      properties.push({
        label: 'Confidence',
        value: channel.confidence,
        isHighlight: channel.confidence === 'high'
      });
    }
    if (channel.competitive_position) {
      properties.push({ label: 'Competitive Position', value: channel.competitive_position });
    }

    return properties;
  }

  // Helper method to get insights (generic or specific)
  getInsights(channel: any): string[] {
    return channel.insights || channel.key_insights || [];
  }

  // Helper method to get key elements (generic or specific)
  getKeyElements(channel: any): string[] {
    return channel.keyElements || channel.key_elements || [];
  }

  // Helper method to get risks (generic or specific)
  getRisks(channel: any): string[] {
    return channel.risks || channel.risk_factors || [];
  }

  // Helper method to get opportunities (generic or specific)
  getOpportunities(channel: any): string[] {
    return channel.opportunities || channel.optimization_opportunities || [];
  }

  // Helper method to get recommendation (generic or specific)
  getRecommendation(channel: any): string {
    return channel.recommendation || '';
  }

  // Helper method to format budget values using centralized TextUtils
  formatBudget(budget: any): string {
    return TextUtils.formatBudget(budget);
  }
} 