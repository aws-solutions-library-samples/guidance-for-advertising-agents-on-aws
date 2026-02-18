import { Component, Input, Output, EventEmitter, OnChanges, ChangeDetectionStrategy } from '@angular/core';
import { VisualizationCacheService } from '../../../services/visualization-cache.service';

/**
 * Visualization Popover Component
 * 
 * A popover container that displays multiple visualization components in a compact overlay.
 * Used to show additional visualizations without navigating away from the main interface.
 * 
 * @example
 * ```html
 * <app-visualization-popover 
 *   [displayName]="agentName"
 *   [color]="agentColor"
 *   [visualData]="visualizationData"
 *   (close)="onPopoverClose()">
 * </app-visualization-popover>
 * ```
 * 
 * @example Usage with Visualization Data:
 * ```typescript
 * visualizationData = {
 *   metricData: {
 *     visualizationType: 'metrics',
 *     title: 'Performance Metrics',
 *     metrics: [...]
 *   },
 *   channelAllocations: {
 *     visualizationType: 'allocations',
 *     title: 'Budget Allocation',
 *     allocations: [...]
 *   },
 *   channelCards: {
 *     visualizationType: 'channels',
 *     title: 'Channel Analysis',
 *     channels: [...]
 *   }
 * };
 * ```
 * 
 * @features
 * - Compact popover overlay design
 * - Support for multiple visualization types
 * - Agent-specific branding with colors
 * - Click-outside-to-close functionality
 * - Responsive layout adaptation
 * - Visualization count indicator
 * - Smooth open/close animations
 * 
 * @useCases
 * - Additional metrics display
 * - Detailed breakdowns
 * - Comparative analysis
 * - Supplementary charts
 * - Context-sensitive data
 */
@Component({
  selector: 'app-visualization-popover',
  templateUrl: './visualization-popover.component.html',
  styleUrls: ['./visualization-popover.component.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class VisualizationPopoverComponent implements OnChanges {
  @Input() displayName: string = '';
  @Input() color: string = '#667eea';
  @Input() visualData: any = {};

  @Input() isVisible: boolean = false;
  @Output() closePopover = new EventEmitter<void>();

  private previousVisualizationTypes: Set<string> = new Set();
  private newItems: Set<string> = new Set();
  private processedData: any = null;
  private lastInputHash: string = '';

  constructor(private cacheService: VisualizationCacheService) { }
ngOnInit(){}
  ngOnChanges() {
    this.processVisualizationData();
    this.detectNewItems();
  }

  private processVisualizationData(): void {
    if (!this.visualData) {
      this.processedData = null;
      return;
    }

    const currentHash = this.cacheService.generateKey('popover', this.visualData);
    
    // Only reprocess if data has changed
    if (this.lastInputHash === currentHash && this.processedData) {
      return;
    }

    // Check cache first
    const cached = this.cacheService.getCachedVisualizationData('popover', this.visualData);
    if (cached) {
      this.processedData = cached;
      this.lastInputHash = currentHash;
      return;
    }

    // Process data once and cache it
    this.processedData = {
      hasVisualizationData: this.computeHasVisualizationData(),
      visualizationCount: this.computeVisualizationCount(),
      mappedData: this.computeMappedVisualizationData()
    };

    // Cache the processed data
    this.cacheService.cacheVisualizationData('popover', this.visualData, this.processedData);
    this.lastInputHash = currentHash;
  }

  // Public getter methods for template
  hasVisualizationData(): boolean {
    return this.processedData?.hasVisualizationData || false;
  }

  getVisualizationCount(): number {
    return this.processedData?.visualizationCount || 0;
  }

  // Private computation methods (called once during processing)
  private computeMappedVisualizationData(): any {
    if (!this.visualData) return null;

    // Find the first available visualization data and map it to data
    if (this.visualData.metricData) {
      this.visualData.data = this.visualData.metricData;
    } else if (this.visualData.channelAllocations) {
      this.visualData.data = this.visualData.channelAllocations;
    } else if (this.visualData.channelCards) {
      this.visualData.data = this.visualData.channelCards;
    } else if (this.visualData.segmentCards) {
      this.visualData.data = this.visualData.segmentCards;
    } else if (this.visualData.creativeData) {
      this.visualData.data = this.visualData.creativeData;
    } else if (this.visualData.timelineData) {
      this.visualData.data = this.visualData.timelineData;
    } else if (this.visualData.histogramData) {
      this.visualData.data = this.visualData.histogramData;
    } else if (this.visualData.doubleHistogramData) {
      this.visualData.data = this.visualData.doubleHistogramData;
    } else if (this.visualData.barChartData) {
      this.visualData.data = this.visualData.barChartData;
    } else if (this.visualData.donutChartData) {
      this.visualData.data = this.visualData.donutChartData;
    } else if (this.visualData.adcpInventoryData) {
      this.visualData.data = this.visualData.adcpInventoryData;
    }

    return this.visualData;
  }

  onClose(): void {
    this.closePopover.emit();
  }

  // Prevent popover from closing when clicking inside it
  onPopoverClick(event: Event): void {
    event.stopPropagation();
  }

  private computeHasVisualizationData(): boolean {
    return !!(
      this.visualData.metricData || 
      this.visualData.channelAllocations || 
      this.visualData.channelCards || 
      this.visualData.segmentCards || 
      this.visualData.creativeData ||
      this.visualData.timelineData ||
      this.visualData.histogramData ||
      this.visualData.doubleHistogramData ||
      this.visualData.barChartData ||
      this.visualData.donutChartData ||
      // ADCP Protocol Visualizations
      this.visualData.adcpInventoryData
    );
  }

  private computeVisualizationCount(): number {
    let count = 0;
    if (this.visualData.metricData) count++;
    if (this.visualData.channelAllocations) count++;
    if (this.visualData.channelCards) count++;
    if (this.visualData.segmentCards) count++;
    if (this.visualData.creativeData) count++;
    if (this.visualData.timelineData) count++;
    if (this.visualData.histogramData) count++;
    if (this.visualData.doubleHistogramData) count++;
    if (this.visualData.barChartData) count++;
    if (this.visualData.donutChartData) count++;
    // ADCP Protocol Visualizations
    if (this.visualData.adcpInventoryData) count++;
    return count;
  }

  // Detect new visualization types for glow effect
  private detectNewItems(): void {
    const currentTypes = new Set<string>();
    
    if (this.visualData.metricData) currentTypes.add('metrics');
    if (this.visualData.channelAllocations) currentTypes.add('allocations');
    if (this.visualData.channelCards) currentTypes.add('channels');
    if (this.visualData.segmentCards) currentTypes.add('segments');
    if (this.visualData.creativeData) currentTypes.add('creative');
    if (this.visualData.timelineData) currentTypes.add('timeline');
    if (this.visualData.histogramData) currentTypes.add('histogram');
    if (this.visualData.doubleHistogramData) currentTypes.add('doubleHistogram');
    if (this.visualData.barChartData) currentTypes.add('barChart');
    if (this.visualData.donutChartData) currentTypes.add('donutChart');
    // ADCP Protocol Visualizations
    if (this.visualData.adcpInventoryData) currentTypes.add('adcpInventory');

    // Find new items that weren't in the previous set
    this.newItems.clear();
    currentTypes.forEach(type => {
      if (!this.previousVisualizationTypes.has(type)) {
        this.newItems.add(type);
      }
    });

    // Update previous types for next comparison
    this.previousVisualizationTypes = new Set(currentTypes);

    // Clear new items after glow animation duration (3 seconds)
    if (this.newItems.size > 0) {
      setTimeout(() => {
        this.newItems.clear();
      }, 3000);
    }
  }

  // Check if an item is new (for glow effect)
  isNewItem(type: string): boolean {
    return this.newItems.has(type);
  }
} 