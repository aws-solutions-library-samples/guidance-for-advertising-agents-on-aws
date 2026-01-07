import { 
  Component, 
  Input, 
  Output, 
  EventEmitter, 
  OnChanges, 
  SimpleChanges,
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  OnDestroy
} from '@angular/core';

export interface VisualizationMessage {
  id: string;
  agentName: string;
  visualData: any;
  timestamp: Date;
}

/**
 * Visualization Companion Panel Component
 * 
 * A glassmorphic floating panel that displays visualizations beside the chat.
 * Floats in from the bottom when a message with visualization enters view,
 * and floats out when the message exits view.
 * 
 * Features:
 * - Glassmorphic design with frosted glass effect
 * - Float-in/float-out animations tied to message visibility
 * - Summary/detail view modes for large visualizations
 * - Hover-to-expand detail toggle with crossfade morph
 * - No nested panel backgrounds - renders directly on glass surface
 */
@Component({
  selector: 'app-visualization-companion-panel',
  templateUrl: './visualization-companion-panel.component.html',
  styleUrls: ['./visualization-companion-panel.component.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class VisualizationCompanionPanelComponent implements OnChanges, OnDestroy {
  @Input() visibleMessage: VisualizationMessage | null = null;
  @Input() agentColor: string = '#667eea';
  @Input() isVisible: boolean = false;
  
  @Output() closePanel = new EventEmitter<void>();
  @Output() expandVisualization = new EventEmitter<string>();

  // View mode: 'summary' or 'detail'
  viewMode: 'summary' | 'detail' = 'summary';
  
  // Track which visualization sections are expanded
  expandedSections: Set<string> = new Set();
  
  // Animation state
  animationState: 'entering' | 'visible' | 'exiting' | 'hidden' = 'hidden';
  
  private animationTimeout: any = null;

  constructor(private cdr: ChangeDetectorRef) {}

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['isVisible'] || changes['visibleMessage']) {
      this.handleVisibilityChange();
    }
  }

  ngOnDestroy(): void {
    if (this.animationTimeout) {
      clearTimeout(this.animationTimeout);
    }
  }

  private handleVisibilityChange(): void {
    if (this.isVisible && this.visibleMessage && this.hasVisualizationData()) {
      // Float in
      if (this.animationState === 'hidden' || this.animationState === 'exiting') {
        this.animationState = 'entering';
        this.cdr.markForCheck();
        
        if (this.animationTimeout) clearTimeout(this.animationTimeout);
        this.animationTimeout = setTimeout(() => {
          this.animationState = 'visible';
          this.cdr.markForCheck();
        }, 400);
      }
    } else {
      // Float out
      if (this.animationState === 'visible' || this.animationState === 'entering') {
        this.animationState = 'exiting';
        this.cdr.markForCheck();
        
        if (this.animationTimeout) clearTimeout(this.animationTimeout);
        this.animationTimeout = setTimeout(() => {
          this.animationState = 'hidden';
          this.viewMode = 'summary'; // Reset to summary on hide
          this.expandedSections.clear();
          this.cdr.markForCheck();
        }, 400);
      }
    }
  }

  hasVisualizationData(): boolean {
    if (!this.visibleMessage?.visualData) return false;
    const vd = this.visibleMessage.visualData;
    return !!(
      vd.metricData || vd.channelAllocations || vd.channelCards || 
      vd.segmentCards || vd.creativeData || vd.timelineData ||
      vd.histogramData || vd.doubleHistogramData || vd.barChartData ||
      vd.donutChartData || vd.adcpInventoryData
    );
  }

  getVisualizationCount(): number {
    if (!this.visibleMessage?.visualData) return 0;
    const vd = this.visibleMessage.visualData;
    let count = 0;
    if (vd.metricData) count++;
    if (vd.channelAllocations) count++;
    if (vd.channelCards) count++;
    if (vd.segmentCards) count++;
    if (vd.creativeData) count++;
    if (vd.timelineData) count++;
    if (vd.histogramData) count++;
    if (vd.doubleHistogramData) count++;
    if (vd.barChartData) count++;
    if (vd.donutChartData) count++;
    if (vd.adcpInventoryData) count++;
    return count;
  }

  // Check if a visualization type is "large" and needs summary/detail modes
  isLargeVisualization(type: string): boolean {
    return ['timeline', 'inventory', 'channels', 'segments', 'creative'].includes(type);
  }

  // Toggle between summary and detail view
  toggleViewMode(): void {
    this.viewMode = this.viewMode === 'summary' ? 'detail' : 'summary';
    this.cdr.markForCheck();
  }

  // Toggle individual section expansion
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

  onClose(): void {
    this.closePanel.emit();
  }

  // Prevent panel from closing when clicking inside
  onPanelClick(event: Event): void {
    event.stopPropagation();
  }

  // Get display name for agent
  getAgentDisplayName(): string {
    return this.visibleMessage?.agentName || 'Agent';
  }

  // Check if panel should be rendered at all
  shouldRender(): boolean {
    return this.animationState !== 'hidden';
  }
}
