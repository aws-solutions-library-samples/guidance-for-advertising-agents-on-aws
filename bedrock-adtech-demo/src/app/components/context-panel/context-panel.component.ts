import { Component, Input, Output, EventEmitter, OnInit } from '@angular/core';
import { Publisher, Content } from '../../models/advertising'

@Component({
  selector: 'app-context-panel',
  templateUrl: './context-panel.component.html',
  styleUrls: ['./context-panel.component.scss']
})
export class ContextPanelComponent implements OnInit {
  @Input() tabConfig: any;
  @Input() currentContext: any ={}
  @Output() contextPanelShow = new EventEmitter<void>();
  @Output() contextPanelHide = new EventEmitter<void>();
  @Output() publisherSelect = new EventEmitter<any>();

  showContextPanel: boolean = false;
  isContextExpanded: boolean = false;
  showContextModal: boolean = false;
  showVisualizationModal: boolean = false;
  showPreview: boolean = false;
  previewVisualization: any = null;
  selectedVisualization: any = null;
  previewPosition = { bottom: 120, left: 0 };
  visualizationsToShow: any[] = [];
  private contextHoverTimeout: any = null;

  // Sticky preview state
  isPreviewSticky: boolean = false;
  stickyPreviewVisualization: any = null;
  private previewHoverTimeout: any = null;

  constructor() { }

  collapseAfter5Seconds()
  {
    setTimeout(()=>{
      this.isContextExpanded=true
    },5000)
  }
  ngOnInit(): void {
    console.log('ContextPanelComponent initialized');
    console.log('tabConfig:', this.tabConfig);
   
    this.visualizationsToShow = this.getVisualizationsToShow();
    console.log('🎨 Context Panel Initialized:');
    console.log('  - Tab Config:', this.tabConfig?.title);
    console.log('  - Visualizations to show:', this.visualizationsToShow.length);
    console.log('  - Visualization types:', this.visualizationsToShow.map(v => v.type));
    
    // Test visualization data processing
    if (this.visualizationsToShow.length > 0) {
      const testViz = this.visualizationsToShow[0];
      const processedTest = this.processVisualizationData(testViz);
      console.log('🧪 Test visualization processing:', {
        original: testViz.type,
        hasProcessedData: Object.keys(processedTest).length > Object.keys(testViz).length
      });
    }
  }

  onContextPanelShow(): void {
    console.log('Context panel show triggered');
    this.contextPanelShow.emit();
  }

  onContextPanelHide(): void {
    this.contextPanelHide.emit();
    this.visualizationsToShow = this.getVisualizationsToShow();
  }

  onContextHover(isHovering: boolean): void {
    if (this.contextHoverTimeout) {
      clearTimeout(this.contextHoverTimeout);
      this.contextHoverTimeout = null;
    }

    if (isHovering) {
      this.isContextExpanded = true;
    } else {
      // Delay collapse to allow moving between buttons
      this.contextHoverTimeout = setTimeout(() => {
        this.isContextExpanded = false;
        this.hideVisualizationPreview();
      }, 300);
    }
  }

  openContextModal(): void {
    this.showContextModal = true;
    this.isContextExpanded = false;
    this.hideVisualizationPreview();
  }

  closeContextModal(event?: Event): void {
    if (event) {
      event.preventDefault();
      event.stopPropagation();
    }
    this.showContextModal = false;
  }

  openVisualizationModal(visualization: any): void {
    console.log(this.visualizationsToShow)
    this.selectedVisualization = this.processVisualizationData(visualization);
    this.showVisualizationModal = true;
    this.isContextExpanded = false;
    this.hideVisualizationPreview();
  }

  closeVisualizationModal(event?: Event): void {
    if (event) {
      event.preventDefault();
      event.stopPropagation();
    }
    this.showVisualizationModal = false;
    this.selectedVisualization = null;
  }

  showVisualizationPreview(visualization: any, event: MouseEvent): void {
    // Clear any existing hover timeout
    if (this.previewHoverTimeout) {
      clearTimeout(this.previewHoverTimeout);
    }

    // Don't show hover preview if this visualization is already sticky
    if (this.isPreviewSticky && this.stickyPreviewVisualization?.type === visualization.type) {
      return;
    }

    // Process the visualization data to match component expectations
    this.previewVisualization = this.processVisualizationData(visualization);
    this.showPreview = true;
    
    // Position the preview to the right of the button
    const buttonRect = (event.target as HTMLElement).getBoundingClientRect();
    this.previewPosition = {
      bottom: (buttonRect.top - 10),
      left: buttonRect.left
    };
  }

  private processVisualizationData(visualization: any): any {
    let processed = { ...visualization };

    // Map whichever visualization data exists to generic data property
    processed = this.mapVisualizationData(processed);

    /* switch (visualization.type) {
      case 'metrics-visualization':
        if (visualization.data && !processed.metricData) {
          processed.metricData = this.processDynamicMetrics(visualization.data);
        }
        break;
      case 'metrics-grid':
        // Legacy support for metrics-grid format
        processed.metricData = {
          visualizationType: 'metrics',
          title: visualization.title,
          metrics: [{
            primaryLabel: 'Performance Metrics',
            items: visualization.items?.map((item: any) => ({
              primaryLabel: item.label,
              actualValue: this.formatMetricValue(item.valueKey, item.format),
              icon: item.icon,
              format: item.format
            })) || []
          }]
        };
        break;

      case 'timeline-visualization':
        // Timeline data needs to be formatted for the timeline component
        processed.timelineData = {
          visualizationType: 'timeline',
          title: visualization.title,
          subtitle: visualization.subtitle,
          startDate: '2024-01-01',
          endDate: '2024-12-31',
          totalDuration: visualization.timelineData?.totalDuration?.toString() || '8 weeks',
          phases: visualization.timelineData?.phases?.map((phase: any) => ({
            primaryLabel: phase.name,
            description: phase.focus,
            startDate: '2024-01-01',
            endDate: '2024-12-31',
            duration: phase.weeks,
            status: 'upcoming' as const,
            priority: 'medium' as const,
            budget: this.formatRevenue(phase.budget || 0),
            expectedOutcome: phase.focus
          })) || []
        };
        break;

      case 'allocations-visualization':
        // Allocations data needs to be formatted for the allocations component
        processed.channelAllocations = {
          visualizationType: 'allocations',
          title: visualization.title,
          subtitle: visualization.subtitle,
          allocations: [
            ...(visualization.allocationData?.publisherTiers?.map((tier: any) => ({
              name: tier.name,
              budget: tier.publisherRevenue,
              percentage: tier.percentage,
              performance: tier.percentage > 40 ? 'high' : tier.percentage > 25 ? 'medium' : 'low',
              description: tier.publishers,
              additionalMetrics: {
                avgYield: tier.avgYield,
                publishers: tier.publishers
              }
            })) || []),
            ...(visualization.allocationData?.contentCategories?.map((category: any) => ({
              name: category.name,
              budget: category.publisherRevenue,
              percentage: category.percentage,
              performance: category.percentage > 30 ? 'high' : category.percentage > 20 ? 'medium' : 'low',
              description: `${category.brandFit} brand fit`,
              additionalMetrics: {
                avgYield: category.avgYield,
                brandFit: category.brandFit
              }
            })) || [])
          ]
        };
        break;

      case 'bar-chart-visualization':
        processed.barChartData = {
          visualizationType: 'bar-chart',
          title: visualization.title,
          subtitle: visualization.subtitle,
          orientation: 'vertical',
          xAxisLabel: 'Categories',
          yAxisLabel: 'Values',
          data: [],
          colorScheme: 'agent-palette',
          showValues: true,
          sortOrder: 'none'
        };
        break;

      case 'donut-chart-visualization':
        processed.donutChartData = {
          visualizationType: 'donut-chart',
          title: visualization.title,
          subtitle: visualization.subtitle,
          centerText: {
            primary: 'Total',
            secondary: '100%'
          },
          data: [],
          colorScheme: 'agent-palette',
          showPercentages: true,
          showLegend: true,
          innerRadius: 0.6
        };
        break;

      case 'segments-visualization':
        processed.segmentCards = {
          visualizationType: 'segments',
          title: visualization.title,
          subtitle: visualization.subtitle,
          segments: []
        };
        break;

      case 'channels-visualization':
        processed.channelCards = {
          visualizationType: 'channels',
          title: visualization.title,
          subtitle: visualization.subtitle,
          channels: []
        };
        break;

      case 'creative-visualization':
        processed.creativeData = {
          visualizationType: 'creative',
          title: visualization.title,
          subtitle: visualization.subtitle,
          creatives: []
        };
        break;

      case 'histogram-visualization':
        processed.histogramData = {
          visualizationType: 'histogram',
          title: visualization.title,
          subtitle: visualization.subtitle,
          xAxisLabel: 'Values',
          yAxisLabel: 'Frequency',
          data: [],
          binCount: 10
        };
        break;

      case 'double-histogram-visualization':
        processed.doubleHistogramData = {
          visualizationType: 'double-histogram',
          title: visualization.title,
          subtitle: visualization.subtitle,
          xAxisLabel: 'Values',
          yAxisLabel: 'Frequency',
          dataset1: { label: 'Dataset 1', color: '#4CAF50', data: [] },
          dataset2: { label: 'Dataset 2', color: '#2196F3', data: [] },
          binCount: 10
        };
        break;
    } */

    return processed;
  }

  // Map whichever visualization data exists to visualData.data
  private mapVisualizationData(visualData: any):any {
    if (!visualData) return;

    // Find the first available visualization data and map it to data
    if (visualData.metricData) {
      visualData.data = visualData.metricData;
    } else if (visualData.channelAllocations) {
      visualData.data = visualData.channelAllocations;
    } else if (visualData.channelCards) {
      visualData.data = visualData.channelCards;
    } else if (visualData.segmentCards) {
      visualData.data = visualData.segmentCards;
    } else if (visualData.creativeData) {
      visualData.data = visualData.creativeData;
    } else if (visualData.timelineData) {
      visualData.data = visualData.timelineData;
    } else if (visualData.histogramData) {
      visualData.data = visualData.histogramData;
    } else if (visualData.doubleHistogramData) {
      visualData.data = visualData.doubleHistogramData;
    } else if (visualData.barChartData) {
      visualData.data = visualData.barChartData;
    } else if (visualData.donutChartData) {
      visualData.data = visualData.donutChartData;
    }
    return visualData
  }

  hideVisualizationPreview(): void {
    // Don't hide if preview is sticky
    if (this.isPreviewSticky) {
      return;
    }

    // Add a small delay to allow moving to the preview modal
    this.previewHoverTimeout = setTimeout(() => {
      if (!this.isPreviewSticky) {
        this.showPreview = false;
        this.previewVisualization = null;
      }
    }, 200);
  }

  // New methods for sticky preview functionality
  onVisualizationButtonClick(visualization: any, event: MouseEvent): void {
    event.stopPropagation();
    event.preventDefault();

    // Clear any hover timeout
    if (this.previewHoverTimeout) {
      clearTimeout(this.previewHoverTimeout);
    }

    // Make the preview sticky
    this.isPreviewSticky = true;
    this.stickyPreviewVisualization = this.processVisualizationData(visualization);
    this.previewVisualization = this.stickyPreviewVisualization;
    this.showPreview = true;

    // Position as centered modal for sticky mode
    this.previewPosition = {
      bottom: 50, // Center vertically
      left: 50   // Center horizontally
    };
  }

  onPreviewModalMouseEnter(): void {
    // Keep modal open when hovering over it
    if (this.previewHoverTimeout) {
      clearTimeout(this.previewHoverTimeout);
    }
  }

  onPreviewModalMouseLeave(): void {
    // Don't hide if modal is sticky
    if (this.isPreviewSticky) {
      return;
    }

    // Hide modal when leaving it (if not sticky)
    this.previewHoverTimeout = setTimeout(() => {
      if (!this.isPreviewSticky) {
        this.showPreview = false;
        this.previewVisualization = null;
      }
    }, 200);
  }

  closeStickyPreview(): void {
    this.isPreviewSticky = false;
    this.stickyPreviewVisualization = null;
    this.showPreview = false;
    this.previewVisualization = null;

    if (this.previewHoverTimeout) {
      clearTimeout(this.previewHoverTimeout);
    }
  }

  getVizButtonTransform(index: number): string {
    if (this.isContextExpanded) {
      return 'translateX(0)';
    } else {
      // Stack buttons to the left (off-screen) - animate from left to right
      const offset = -((index + 1) * 30); // Negative offset to move left, increasing distance for each button
      return `translateX(${offset}px)`;
    }
  }

  getVizButtonOpacity(index: number): number {
    return this.isContextExpanded ? 1 : 0;
  }

  getVizButtonAnimationDelay(index: number): string {
    // Stagger the animation from left to right
    return `${index * 0.1}s`;
  }

  getVisualizationIcon(type: string): string {
    const iconMap: { [key: string]: string } = {
      'metrics-grid': 'dashboard',
      'timeline-visualization': 'timeline',
      'allocations-visualization': 'pie_chart',
      'current-publisher': 'business',
      'current-campaign': 'campaign',
      'current-content': 'article',
      'publisher-cards': 'hub',
      'channel-cards': 'tv',
      'bidding-metrics': 'trending_up',
      'campaign-performance': 'analytics'
    };
    return iconMap[type] || 'bar_chart';
  }

  getContextButtonLabel(): string {
    return this.tabConfig?.title ? `${this.tabConfig.title} context` : 'tab context';
  }

  getVisualizationsToShow(): any[] {
    console.log('tabConfig:', this.tabConfig);
    console.log('tabConfig.visualizations:', this.tabConfig?.visualizations);

    // If no visualizations configured, create generic defaults based on available data
    if (!this.tabConfig?.visualizations) {
      let defaultViz: any[] = [];

      // Generic metrics visualization based on available data
      defaultViz.push({
        type: 'metrics-visualization',
        visualizationType: 'metrics',
        title: 'Key Performance Indicators',
        gridSpan: 2,
        metricData: {
          visualizationType: 'metrics',
          title: 'Key Performance Indicators',
          metrics: [
            {
              primaryLabel: 'Performance Overview',
              items: this.generateGenericMetrics()
            }
          ]
        }
      });

      

      console.log('Returning default visualizations:', defaultViz);
      return defaultViz;
    }

    // Return configured visualizations, processing them for component compatibility
    const configuredViz = this.tabConfig.visualizations
      .filter((viz: any) => viz.type !== 'context-selector')
      .map((viz: any) => this.processVisualizationData(viz));

    // Add context cards if data is available
    // if (this.currentPublisher && this.currentPublisher.name) {
    //   configuredViz.push({ type: 'current-publisher', gridSpan: 1 } as any);
    // }
    // if (this.currentCampaign && this.currentCampaign.id) {
    //   configuredViz.push({ type: 'current-campaign', gridSpan: 1 } as any);
    // }
    // if (this.currentContent && this.currentContent.id) {
    //   configuredViz.push({ type: 'current-content', gridSpan: 2 } as any);
    // }

    // Ensure we always have at least one visualization
    if (configuredViz.length === 0) {
      configuredViz.push({
        type: 'metrics-visualization',
        visualizationType: 'metrics',
        title: 'System Status',
        gridSpan: 1,
        metricData: {
          visualizationType: 'metrics',
          title: 'System Status',
          metrics: [
            {
              primaryLabel: 'Status',
              items: [
                { primaryLabel: 'Configuration', actualValue: this.tabConfig?.title || 'Loaded' }
              ]
            }
          ]
        }
      });
    }

    console.log('Returning configured visualizations:', configuredViz);
    return configuredViz;
  }

  // Generate generic metrics based on available data
  private generateGenericMetrics(): any[] {
    const metrics: any[] = [];

    
    // Fallback metrics if no specific data is available
    if (metrics.length === 0) {
      metrics.push(
        { primaryLabel: 'System Status', actualValue: 'Active' },
        { primaryLabel: 'Configuration', actualValue: 'Loaded' }
      );
    }

    return metrics;
  }

  // Process metrics with dynamic values
  private processDynamicMetrics(metricsData: any): any {
    const processed = { ...metricsData };
    
    if (processed.metrics) {
      processed.metrics = processed.metrics.map((metricGroup: any) => {
        const processedGroup = { ...metricGroup };
        
        if (processedGroup.items) {
          processedGroup.items = processedGroup.items.map((item: any) => {
            const processedItem = { ...item };
            
            // If actualValue is "dynamic", calculate it from valueKey
            if (processedItem.actualValue === 'dynamic' && processedItem.valueKey) {
              processedItem.actualValue = this.formatMetricValue(processedItem.valueKey, processedItem.format || 'number');
            }
            
            return processedItem;
          });
        }
        
        return processedGroup;
      });
    }
    
    return processed;
  }

  trackVisualization(index: number, item: any): any {
    return item.id || index;
  }

  formatMetricValue(valueKey: string, format: string): string {
    const contextData = this.tabConfig?.contextData;
    console.log('🔍 formatMetricValue called:', {
      valueKey,
      format,
      hasContextData: !!contextData,
      hasPublisherMonetizationData: !!contextData?.publisherMonetizationData,
      publisherRevenue: contextData?.publisherMonetizationData?.publisherRevenue,
      tabConfigTitle: this.tabConfig?.title
    });
    
    if (!contextData) {
      console.log('❌ No contextData found');
      return 'N/A';
    }

    // Calculate values based on valueKey
    let value: number = 0;

    switch (valueKey) {
      // Publisher Revenue metrics
      case 'publisherRevenue':
        value = contextData.publisherMonetizationData?.publisherRevenue || 1850000;
        break;
      case 'networkPublishers':
        value = 450;
        break;
      case 'averageYield':
        value = contextData.publisherMonetizationData?.inventoryMetrics?.averageYield || 18.50;
        break;
      case 'fillRate':
        value = contextData.publisherMonetizationData?.inventoryMetrics?.fillRate || 94.2;
        break;
      // RTB metrics
      case 'winRate':
        value = 22; // Mock win rate
        break;
      case 'avgCPM':
        value = 6.75; // Mock average CPM
        break;
      case 'responseTime':
        value = 45; // Mock response time in ms
        break;
      case 'qualityScore':
        value = 8.2; // Mock quality score
        break;

      case 'budgetUtilization':
        value = 87; // Mock budget utilization
        break;
      
      // Media Planning metrics
      case 'totalBudget':
        value = contextData.mediaPlans?.[0]?.totalBudget || contextData.mediaChannels?.reduce((sum: number, ch: any) => sum + ((ch.allocation * 125000) / 100), 0) || 125000;
        break;
      case 'activeChannels':
        value = contextData.mediaChannels?.length || contextData.mediaPlans?.[0]?.channels?.length || 4;
        break;
      case 'totalReach':
        value = contextData.mediaChannels?.reduce((sum: number, ch: any) => sum + ch.reach, 0) || 8300000;
        break;
      case 'inventoryUtilization':
        value = contextData.mediaMetrics?.inventoryUtilization || 92;
        break;
      case 'advertiserSatisfaction':
        value = contextData.mediaMetrics?.advertiserSatisfaction || 87;
        break;
      case 'averageCPM':
        value = contextData.mediaMetrics?.averageCPM || 18.75;
        break;

      case 'test2':
        value = this.tabConfig?.title?.length || 0;
        break;

      default:
        // Try to get from contextData.inventoryMetrics or other sources
        if (contextData.inventoryMetrics && contextData.inventoryMetrics[valueKey] !== undefined) {
          value = contextData.inventoryMetrics[valueKey];
        } else if (contextData[valueKey] !== undefined) {
          value = contextData[valueKey];
        } else {
          value = 0;
        }
        break;
    }

    // Format based on type
    switch (format) {
      case 'currency':
        return this.formatRevenue(value);
      case 'percentage':
        // Show decimal places for rates/percentages for better precision
        if (valueKey === 'conversionRate' || valueKey === 'expectedCTR') {
          return `${value.toFixed(2)}`;
        }
        return `${Math.round(value)}`;
      case 'number':
        if (valueKey === 'responseTime') {
          return `${value}ms`;
        } else if (valueKey === 'qualityScore') {
          return `${value}/10`;
        }
        return value.toLocaleString();
      default:
        return value.toString();
    }
  }

  getTierColor(tier: string): string {
    const colors: { [key: string]: string } = {
      'Premium': '#4CAF50',
      'Standard': '#FF9800',
      'Basic': '#9E9E9E'
    };
    return colors[tier] || '#9E9E9E';
  }

  getScoreColor(score: number): string {
    if (score >= 90) return '#4CAF50';
    if (score >= 70) return '#FF9800';
    return '#F44336';
  }

  formatRevenue(revenue: number): string {
    if (revenue >= 1000000) {
      return `${(revenue / 1000000).toFixed(1)}M`;
    } else if (revenue >= 1000) {
      return `${(revenue / 1000).toFixed(0)}K`;
    }
    if(!revenue) revenue = 0
    return `${revenue.toLocaleString()}`;
  }

  getCategoryColor(category: string): string {
    const colors: { [key: string]: string } = {
      'Entertainment': '#E91E63',
      'News': '#2196F3',
      'Sports': '#FF5722',
      'Lifestyle': '#9C27B0',
      'Technology': '#607D8B'
    };
    return colors[category] || '#9E9E9E';
  }

  getCategoryIcon(category: string): string {
    const icons: { [key: string]: string } = {
      'Entertainment': 'movie',
      'News': 'article',
      'Sports': 'sports_soccer',
      'Lifestyle': 'favorite',
      'Technology': 'computer'
    };
    return icons[category] || 'category';
  }

  getFormatColor(format: string): string {
    const colors: { [key: string]: string } = {
      'Video': '#F44336',
      'Display': '#2196F3',
      'Native': '#4CAF50',
      'Audio': '#FF9800'
    };
    return colors[format] || '#9E9E9E';
  }

  getFormatIcon(format: string): string {
    const icons: { [key: string]: string } = {
      'Video': 'play_circle',
      'Display': 'image',
      'Native': 'article',
      'Audio': 'volume_up'
    };
    return icons[format] || 'ads_click';
  }

  getFillRateColor(fillRate: number): string {
    if (fillRate >= 90) return '#4CAF50';
    if (fillRate >= 70) return '#FF9800';
    return '#F44336';
  }

  getTrendColor(trend: string): string {
    if (trend === 'up') return '#4CAF50';
    if (trend === 'down') return '#F44336';
    return '#9E9E9E';
  }

  getTrendIcon(trend: string): string {
    if (trend === 'up') return 'trending_up';
    if (trend === 'down') return 'trending_down';
    return 'trending_flat';
  }

  selectPublisher(publisher: any): void {
    this.publisherSelect.emit(publisher);
    this.visualizationsToShow = this.getVisualizationsToShow();
  }

  // Timeline visualization methods
  getPhaseColor(phaseName: string): string {
    const colors: { [key: string]: string } = {
      'Inventory Ramp-Up': '#4CAF50',
      'Yield Optimization': '#2196F3', 
      'Peak Revenue Period': '#FF9800',
      'Performance Optimization': '#9C27B0'
    };
    return colors[phaseName] || '#9E9E9E';
  }

  // Allocations visualization methods
  getTierAllocationColor(tierName: string): string {
    const colors: { [key: string]: string } = {
      'Tier 1 Premium': '#4CAF50',
      'Tier 2 Quality': '#FF9800',
      'Tier 3 Scale': '#9E9E9E'
    };
    return colors[tierName] || '#9E9E9E';
  }

  getCategoryAllocationColor(categoryName: string): string {
    const colors: { [key: string]: string } = {
      'Cooking & Food': '#FF5722',
      'Lifestyle & Luxury': '#E91E63',
      'Premium News': '#2196F3',
      'Entertainment': '#9C27B0'
    };
    return colors[categoryName] || '#9E9E9E';
  }
}