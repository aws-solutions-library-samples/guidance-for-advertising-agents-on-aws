import { Component, Input, ChangeDetectionStrategy, ChangeDetectorRef, OnChanges, SimpleChanges } from '@angular/core';
import { VisualizationCacheService } from '../../../services/visualization-cache.service';

/**
 * Presentation Visualization Component
 * 
 * Displays comprehensive media plan presentations compiled from all specialist agents.
 * Shows executive summary, advertiser profile, audience strategy, channel recommendations,
 * creative strategy, budget allocation, KPI expectations, and implementation timeline.
 * 
 * @example
 * ```html
 * <app-presentation-visualization [presentationData]="mediaPlanPresentation"></app-presentation-visualization>
 * ```
 * 
 * @example JSON Structure:
 * ```json
 * {
 *   "visualizationType": "presentation",
 *   "title": "Comprehensive Media Plan",
 *   "subtitle": "Q4 2024 Campaign Strategy",
 *   "executiveSummary": {
 *     "campaignObjective": "Drive brand awareness and conversions",
 *     "totalInvestment": "$2,500,000",
 *     "keyKPIs": [
 *       { "label": "Target Impressions", "value": "50M" },
 *       { "label": "Expected ROAS", "value": "4.2x" }
 *     ],
 *     "timeline": "Oct 1 - Dec 31, 2024"
 *   },
 *   "sections": [
 *     {
 *       "sectionId": "advertiser-profile",
 *       "sectionTitle": "Advertiser Profile",
 *       "sectionIcon": "business",
 *       "content": { ... },
 *       "status": "complete"
 *     }
 *   ],
 *   "adjustmentImpact": {
 *     "originalValues": { ... },
 *     "newValues": { ... },
 *     "changes": [ ... ]
 *   }
 * }
 * ```
 */

export interface PresentationKPI {
  label: string;
  value: string;
  trend?: 'up' | 'down' | 'stable';
  change?: string;
}

export interface ExecutiveSummary {
  campaignObjective: string;
  totalInvestment: string;
  keyKPIs: PresentationKPI[];
  timeline: string;
  highlights?: string[];
}

export interface PresentationSection {
  sectionId: string;
  sectionTitle: string;
  sectionIcon?: string;
  content: any;
  status: 'complete' | 'pending' | 'in-progress' | 'needs-review';
  sourceAgent?: string;
  lastUpdated?: string;
}

export interface AdjustmentChange {
  metric: string;
  originalValue: string;
  newValue: string;
  absoluteChange: string;
  percentageChange: string;
  impact: 'positive' | 'negative' | 'neutral';
}

export interface AdjustmentImpact {
  adjustmentType: string;
  description: string;
  originalValues: Record<string, string>;
  newValues: Record<string, string>;
  changes: AdjustmentChange[];
  kpiImpact: PresentationKPI[];
}

export interface PresentationData {
  visualizationType: 'presentation';
  title: string;
  subtitle?: string;
  executiveSummary: ExecutiveSummary;
  sections: PresentationSection[];
  adjustmentImpact?: AdjustmentImpact;
  recommendations?: string[];
  nextSteps?: string[];
}

@Component({
  selector: 'app-presentation-visualization',
  templateUrl: './presentation-visualization.component.html',
  styleUrls: ['./presentation-visualization.component.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class PresentationVisualizationComponent implements OnChanges {
  @Input() presentationData!: PresentationData;
  @Input() compactMode: boolean = false;

  // Processed data cache
  private processedData: any = null;
  private lastInputHash: string = '';

  // Track expanded sections
  expandedSections: Set<string> = new Set();

  constructor(
    private cdr: ChangeDetectorRef,
    private cacheService: VisualizationCacheService
  ) {}

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['presentationData']) {
      this.processVisualizationData();
      this.cdr.markForCheck();
    }
  }

  private processVisualizationData(): void {
    if (!this.presentationData) {
      this.processedData = null;
      return;
    }

    const currentHash = this.cacheService.generateKey('presentation', this.presentationData);

    if (this.lastInputHash === currentHash && this.processedData) {
      return;
    }

    const cached = this.cacheService.getCachedVisualizationData('presentation', this.presentationData);
    if (cached) {
      this.processedData = cached;
      this.lastInputHash = currentHash;
      return;
    }

    this.processedData = {
      executiveSummary: this.presentationData.executiveSummary,
      sections: this.presentationData.sections || [],
      adjustmentImpact: this.presentationData.adjustmentImpact,
      recommendations: this.presentationData.recommendations || [],
      nextSteps: this.presentationData.nextSteps || []
    };

    this.cacheService.cacheVisualizationData('presentation', this.presentationData, this.processedData);
    this.lastInputHash = currentHash;
  }

  // TrackBy functions
  trackByIndex = (index: number): number => index;
  trackBySectionId = (index: number, section: PresentationSection): string => section.sectionId;
  trackByLabel = (index: number, kpi: PresentationKPI): string => kpi.label;
  trackByMetric = (index: number, change: AdjustmentChange): string => change.metric;

  // Executive Summary helpers
  getExecutiveSummary(): ExecutiveSummary | null {
    return this.processedData?.executiveSummary || this.presentationData?.executiveSummary || null;
  }

  getKeyKPIs(): PresentationKPI[] {
    return this.getExecutiveSummary()?.keyKPIs || [];
  }

  // Section helpers
  getSections(): PresentationSection[] {
    return this.processedData?.sections || this.presentationData?.sections || [];
  }

  toggleSection(sectionId: string): void {
    if (this.expandedSections.has(sectionId)) {
      this.expandedSections.delete(sectionId);
    } else {
      this.expandedSections.add(sectionId);
    }
    this.cdr.markForCheck();
  }

  isSectionExpanded(sectionId: string): boolean {
    return this.expandedSections.has(sectionId);
  }

  getSectionStatusColor(status: string): string {
    switch (status) {
      case 'complete': return '#10b981';
      case 'in-progress': return '#3b82f6';
      case 'pending': return '#f59e0b';
      case 'needs-review': return '#ef4444';
      default: return '#6b7280';
    }
  }

  getSectionIcon(section: PresentationSection): string {
    if (section.sectionIcon) return section.sectionIcon;
    
    switch (section.sectionId) {
      case 'advertiser-profile': return 'business';
      case 'audience-strategy': return 'groups';
      case 'channel-recommendations': return 'tv';
      case 'creative-strategy': return 'palette';
      case 'budget-allocation': return 'payments';
      case 'kpi-expectations': return 'trending_up';
      case 'implementation-timeline': return 'schedule';
      default: return 'article';
    }
  }

  // Adjustment Impact helpers
  hasAdjustmentImpact(): boolean {
    return !!this.presentationData?.adjustmentImpact;
  }

  getAdjustmentImpact(): AdjustmentImpact | null {
    return this.processedData?.adjustmentImpact || this.presentationData?.adjustmentImpact || null;
  }

  getAdjustmentChanges(): AdjustmentChange[] {
    return this.getAdjustmentImpact()?.changes || [];
  }

  getChangeImpactColor(impact: string): string {
    switch (impact) {
      case 'positive': return '#10b981';
      case 'negative': return '#ef4444';
      default: return '#6b7280';
    }
  }

  getChangeIcon(impact: string): string {
    switch (impact) {
      case 'positive': return 'arrow_upward';
      case 'negative': return 'arrow_downward';
      default: return 'remove';
    }
  }

  // KPI helpers
  getKPITrendIcon(trend?: string): string {
    switch (trend) {
      case 'up': return 'trending_up';
      case 'down': return 'trending_down';
      default: return 'trending_flat';
    }
  }

  getKPITrendColor(trend?: string): string {
    switch (trend) {
      case 'up': return '#10b981';
      case 'down': return '#ef4444';
      default: return '#6b7280';
    }
  }

  // Recommendations and Next Steps
  getRecommendations(): string[] {
    return this.processedData?.recommendations || this.presentationData?.recommendations || [];
  }

  getNextSteps(): string[] {
    return this.processedData?.nextSteps || this.presentationData?.nextSteps || [];
  }

  // Section content helpers
  getSectionContentKeys(content: any): string[] {
    if (!content || typeof content !== 'object') return [];
    return Object.keys(content).filter(key => content[key] != null);
  }

  formatContentKey(key: string): string {
    return key
      .replace(/([A-Z])/g, ' $1')
      .replace(/_/g, ' ')
      .replace(/^./, str => str.toUpperCase())
      .trim();
  }

  formatContentValue(value: any): string {
    if (Array.isArray(value)) {
      return value.join(', ');
    }
    if (typeof value === 'object') {
      return JSON.stringify(value, null, 2);
    }
    return String(value);
  }

  isArrayValue(value: any): boolean {
    return Array.isArray(value);
  }

  isObjectValue(value: any): boolean {
    return typeof value === 'object' && value !== null && !Array.isArray(value);
  }
}
