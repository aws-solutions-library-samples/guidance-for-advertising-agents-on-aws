import { Component, Input, ChangeDetectionStrategy, ChangeDetectorRef, OnChanges, SimpleChanges } from '@angular/core';
import { TextUtils } from '../../../utils/text-utils';
import { VisualizationCacheService } from '../../../services/visualization-cache.service';

/**
 * Allocations Visualization Component
 * 
 * Displays budget distribution, resource allocation, and investment strategies in a card-based layout.
 * Each allocation shows percentage, budget amount, performance indicators, and actionable insights.
 * 
 * @example
 * ```html
 * <app-allocations-visualization [channelAllocations]="allocationData"></app-allocations-visualization>
 * ```
 * 
 * @example JSON Structure:
 * ```json
 * {
 *   "visualizationType": "allocations",
 *   "title": "Campaign Budget Allocation",
 *   "subtitle": "Q4 2024 Media Investment Strategy",
 *   "totalBudget": "$2,500,000",
 *   "currency": "$",
 *   "allocations": [
 *     {
 *       "primaryLabel": "Digital Video",
 *       "secondaryLabel": "Premium Inventory",
 *       "description": "High-impact video placements across premium publishers",
 *       "allocationPercentage": 45,
 *       "budgetAmount": "$1,125,000",
 *       "performanceLevel": "high",
 *       "statusIndicator": "verified",
 *       "confidenceLevel": "high",
 *       "riskLevel": "low",
 *       "insights": [
 *         "Strong performance in target demographics",
 *         "Premium inventory ensures brand safety"
 *       ],
 *       "risks": [
 *         "Limited inventory during peak seasons"
 *       ],
 *       "recommendations": [
 *         "Secure inventory early for holiday campaigns",
 *         "Consider expanding to emerging video formats"
 *       ],
 *       "metadata": {
 *         "lastUpdated": "2024-12-01T10:00:00Z",
 *         "dataSource": "Campaign Intelligence System"
 *       }
 *     },
 *     {
 *       "primaryLabel": "Social Media",
 *       "secondaryLabel": "Multi-Platform",
 *       "description": "Cross-platform social advertising campaign",
 *       "allocationPercentage": 30,
 *       "budgetAmount": "$750,000",
 *       "performanceLevel": "medium",
 *       "statusIndicator": "pending",
 *       "confidenceLevel": "medium",
 *       "riskLevel": "medium",
 *       "insights": [
 *         "High engagement rates on visual content",
 *         "Strong performance in 18-34 demographic"
 *       ],
 *       "risks": [
 *         "Platform algorithm changes may impact reach"
 *       ],
 *       "recommendations": [
 *         "Diversify content formats",
 *         "Increase video content allocation"
 *       ]
 *     }
 *   ],
 *   "summary": {
 *     "totalAllocations": 5,
 *     "optimizedAllocations": 3,
 *     "riskAllocations": 1,
 *     "recommendedAdjustment": "Increase digital video allocation by 5%"
 *   }
 * }
 * ```
 * 
 * @features
 * - Budget allocation cards with performance indicators
 * - Risk assessment and confidence levels
 * - Actionable insights and recommendations
 * - Progress bars for allocation percentages
 * - Color-coded performance status
 * - Responsive card layout
 * 
 * @useCases
 * - Budget distribution across channels
 * - Resource allocation optimization
 * - Investment strategies
 * - Audience segment budgets
 * - Creative variation budgets
 */
@Component({
  selector: 'app-allocations-visualization',
  templateUrl: './allocations-visualization.component.html',
  styleUrls: ['./allocations-visualization.component.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class AllocationsVisualizationComponent implements OnChanges {
  @Input() channelAllocations: any;
  @Input() compactMode: boolean = false;

  // Processed data cache
  private processedData: any = null;
  private lastInputHash: string = '';

  constructor(
    private cdr: ChangeDetectorRef,
    private cacheService: VisualizationCacheService
  ) { }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['channelAllocations']) {
      this.processVisualizationData();
      this.cdr.markForCheck();
    }
  }

  private processVisualizationData(): void {
    if (!this.channelAllocations) {
      this.processedData = null;
      return;
    }

    const currentHash = this.cacheService.generateKey('allocations', this.channelAllocations);
    
    // Only reprocess if data has changed
    if (this.lastInputHash === currentHash && this.processedData) {
      return;
    }

    // Check cache first
    const cached = this.cacheService.getCachedVisualizationData('allocations', this.channelAllocations);
    if (cached) {
      this.processedData = cached;
      this.lastInputHash = currentHash;
      return;
    }

    // Process data once and cache it
    const allocations = this.getAllocations();
    this.processedData = {
      allocations: allocations,
      processedAllocations: allocations.map(allocation => ({
        ...allocation,
        additionalMetrics: this.computeAdditionalMetrics(allocation),
        topMetrics: this.computeTopMetrics(allocation)
      })),
      summary: this.getSummary(),
      totalBudget: this.getTotalBudget(),
      subtitle: this.getSubtitle()
    };

    // Cache the processed data
    this.cacheService.cacheVisualizationData('allocations', this.channelAllocations, this.processedData);
    this.lastInputHash = currentHash;
  }

  // Public getter methods for template
  getProcessedAllocations(): any[] {
    return this.processedData?.processedAllocations || this.getAllocations();
  }

  // Private computation methods
  private computeAdditionalMetrics(allocation: any): Array<{ label: string, value: string }> {
    const metrics: Array<{ label: string, value: string }> = [];
    
    // Add performance metrics
    if (allocation.performance || allocation.performanceStatus) {
      metrics.push({
        label: 'Performance',
        value: allocation.performance || allocation.performanceStatus
      });
    }
    
    // Add efficiency metrics
    if (allocation.efficiency) {
      metrics.push({
        label: 'Efficiency',
        value: allocation.efficiency
      });
    }
    
    // Add ROI metrics
    if (allocation.roi || allocation.roas) {
      metrics.push({
        label: 'ROI',
        value: allocation.roi || allocation.roas
      });
    }
    
    // Add reach metrics
    if (allocation.reach) {
      metrics.push({
        label: 'Reach',
        value: allocation.reach
      });
    }
    
    // Add conversion metrics
    if (allocation.conversions || allocation.conversionRate) {
      metrics.push({
        label: 'Conversions',
        value: allocation.conversions || allocation.conversionRate
      });
    }
    
    return metrics;
  }

  private computeTopMetrics(allocation: any): Array<{ label: string, value: string }> {
    const allMetrics = this.computeAdditionalMetrics(allocation);
    // Return top 2-3 most important metrics
    return allMetrics.slice(0, 3);
  }

  // TrackBy functions
  trackByIndex = (index: number): number => {
    return index;
  }

  trackByAllocationName = (index: number, allocation: any): string => {
    return this.getAllocationName(allocation) + index;
  }

  trackByMetricLabel = (index: number, metric: { label: string, value: string }): string => {
    return metric.label;
  }

  // Helper method to get allocations array from the data structure
  getAllocations(): any[] {
    if (!this.channelAllocations) return [];

    // Handle different data structures
    if (this.channelAllocations.allocations) {
      return this.channelAllocations.allocations;
    }

    // If channelAllocations is already an array
    if (Array.isArray(this.channelAllocations)) {
      return this.channelAllocations;
    }

    return [];
  }

  // Helper method to get subtitle
  getSubtitle(): string {
    return this.channelAllocations?.subtitle || '';
  }

  // Helper method to get total budget
  getTotalBudget(): string {
    return this.channelAllocations?.totalBudget || '';
  }

  // Helper method to get summary data
  getSummary(): any {
    return this.channelAllocations?.summary || null;
  }

  // Helper method to get allocation name (generic or specific)
  getAllocationName(allocation: any): string {
    return allocation.primaryLabel || allocation.channel || allocation.name || allocation.title || allocation.placement || 'Unnamed Allocation';
  }

  // Helper method to get allocation percentage (generic or specific)
  getAllocationPercentage(allocation: any): number {
    return allocation.percentage || allocation.allocationPercentage || 0;
  }

  // Helper method to get allocation budget (generic or specific)
  getAllocationBudget(allocation: any): string {
    const budget = allocation.budgetAmount || allocation.budget || allocation.allocatedBudget;
    return budget ? this.formatBudget(budget) : '';
  }

  // Helper method to get performance status (generic or specific)
  getPerformanceStatus(allocation: any): string {
    return allocation.performanceLevel || allocation.performance || allocation.status || '';
  }

  // Helper method to get performance level color for allocation cards
  getPerformanceColor(performance: string): string {
    switch (performance?.toLowerCase()) {
      case 'high':
      case 'excellent': return '#10b981';
      case 'medium':
      case 'good': return '#f59e0b';
      case 'medium-high': return '#aac406';
      case 'medium-low': return '#c45706';
      case 'low':
      case 'moderate':
      case 'poor': return '#ef4444';
      default: return '#6b7280';
    }
  }

  // Helper method to format budget values (avoiding double $ signs)
  formatBudget(budget: any): string {
    if (!budget) return '';

    let budgetStr = budget.toString();
    budgetStr = budgetStr.replace('$');
    // If it already starts with $, return as is
    if (budgetStr.startsWith('$')) {
      return budgetStr;
    }

    // If it's a number, format it with $ prefix and commas
    const numericValue = parseFloat(budgetStr.replace(/[^0-9.-]/g, ''));
    if (!isNaN(numericValue)) {
      return `${numericValue.toLocaleString()}`;
    }

    // Otherwise, just add $ prefix
    return `${budgetStr}`;
  }

  // Helper method to get numeric percentage value for progress bars
  getPercentageValue(percentage: any): number {
    if (!percentage) return 0;

    // Convert to string and remove any % symbols
    const percentageStr = percentage.toString().replace('%', '');

    // Parse as float
    const numericValue = parseFloat(percentageStr);

    // Return numeric value or 0 if invalid
    return isNaN(numericValue) ? 0 : numericValue;
  }

  // Helper method to get top 2-3 most important metrics for compact display
  getTopMetrics(allocation: any): Array<{ label: string, value: string }> {
    const metrics: Array<{ label: string, value: string }> = [];

    // Priority order: Status, Risk, Confidence (max 3 items)
    if (allocation.statusIndicator) {
      metrics.push({ label: 'Status', value: allocation.statusIndicator });
    }
    if (allocation.riskLevel && metrics.length < 3) {
      metrics.push({ label: 'Risk', value: allocation.riskLevel });
    }
    if (allocation.confidenceLevel && metrics.length < 3) {
      metrics.push({ label: 'Confidence', value: allocation.confidenceLevel });
    }

    // If we still have space, add other important metrics
    if (metrics.length < 3) {
      if (allocation.efficiency || allocation.cost_efficiency) {
        metrics.push({ label: 'Efficiency', value: allocation.efficiency || allocation.cost_efficiency });
      }
    }
    if (metrics.length < 3) {
      if (allocation.safety) {
        metrics.push({ label: 'Safety', value: allocation.safety });
      }
    }

    return metrics;
  }

  // Helper method to get additional metrics for an allocation with caching (kept for backward compatibility)
  getAdditionalMetrics(allocation: any): Array<{ label: string, value: string }> {
    // Use processed data if available
    const processedAllocation = this.processedData?.processedAllocations?.find((p: any) => p === allocation);
    if (processedAllocation?.additionalMetrics) {
      return processedAllocation.additionalMetrics;
    }
    
    // Fallback to computation

    const metrics: Array<{ label: string, value: string }> = [];

    // New data structure fields
    if (allocation.statusIndicator) {
      metrics.push({ label: 'Status', value: allocation.statusIndicator });
    }
    if (allocation.riskLevel) {
      metrics.push({ label: 'Risk Level', value: allocation.riskLevel });
    }
    if (allocation.confidenceLevel) {
      metrics.push({ label: 'Confidence', value: allocation.confidenceLevel });
    }

    // Generic fields
    if (allocation.primaryMetric) {
      metrics.push({ label: allocation.primaryMetric.label || 'Primary Metric', value: allocation.primaryMetric.value });
    }
    if (allocation.secondaryMetric) {
      metrics.push({ label: allocation.secondaryMetric.label || 'Secondary Metric', value: allocation.secondaryMetric.value });
    }

    // Legacy fields as fallback
    if (allocation.efficiency || allocation.cost_efficiency) {
      metrics.push({ label: allocation.cost_efficiency ? 'Cost Efficiency' : 'Efficiency', value: allocation.efficiency || allocation.cost_efficiency });
    }
    if (allocation.safety) {
      metrics.push({ label: 'Safety', value: allocation.safety });
    }

    return metrics;
  }

  // Helper method to get insights for an allocation
  getInsights(allocation: any): string[] {
    return allocation.insights || [];
  }

  // Helper method to get recommendations for an allocation
  getRecommendations(allocation: any): string[] {
    return allocation.recommendations || [];
  }

  // Helper method to get risks for an allocation
  getRisks(allocation: any): string[] {
    return allocation.risks || [];
  }
} 