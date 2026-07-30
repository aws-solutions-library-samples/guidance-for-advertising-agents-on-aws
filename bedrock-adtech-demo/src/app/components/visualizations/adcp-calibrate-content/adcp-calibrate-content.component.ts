import { Component, Input, ChangeDetectionStrategy, ChangeDetectorRef, OnChanges, SimpleChanges } from '@angular/core';
import { VisualizationCacheService } from '../../../services/visualization-cache.service';
import { VisualizationComponent } from '../visualization-component';

/**
 * ADCP Calibrate Content Visualization
 *
 * Glass tile with a glowing segmented bar. Each segment uses a different accent glow color
 * (green/blue/amber).
 */
@Component({
  selector: 'app-adcp-calibrate-content',
  templateUrl: './adcp-calibrate-content.component.html',
  styleUrls: ['./adcp-calibrate-content.component.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class AdcpCalibrateContentComponent extends VisualizationComponent implements OnChanges {
  @Input() calibrationData: any;

  segments: any[] = [];
  overallScore: number = 0;
  private lastInputHash: string = '';
  private glowColors = [
    { bg: 'rgba(16, 185, 129, 0.25)', glow: '0 0 8px rgba(16, 185, 129, 0.5)', label: '#10b981' },
    { bg: 'rgba(59, 130, 246, 0.25)', glow: '0 0 8px rgba(59, 130, 246, 0.5)', label: '#3b82f6' },
    { bg: 'rgba(245, 158, 11, 0.25)', glow: '0 0 8px rgba(245, 158, 11, 0.5)', label: '#f59e0b' },
    { bg: 'rgba(104, 66, 255, 0.25)', glow: '0 0 8px rgba(104, 66, 255, 0.5)', label: '#6842ff' },
    { bg: 'rgba(195, 0, 224, 0.25)', glow: '0 0 8px rgba(195, 0, 224, 0.5)', label: '#c300e0' }
  ];

  constructor(
    private cdr: ChangeDetectorRef,
    private cacheService: VisualizationCacheService
  ) {
    super();
  }

  ngOnChanges(ch: SimpleChanges): void {
    if (ch['calibrationData']) {
      this.processVisualizationData();
      this.cdr.markForCheck();
    }
  }

  private processVisualizationData(): void {
    if (!this.calibrationData) { this.segments = []; return; }
    const key = 'adcp-calibrate' + this.toolUseId;
    const hash = this.cacheService.generateKey(key, this.calibrationData);
    if (this.lastInputHash === hash && this.segments.length) return;

    const raw = this.calibrationData.segments || this.calibrationData.categories || this.calibrationData.scores || [];
    const total = raw.reduce((s: number, r: any) => s + (r.score || r.value || 0), 0);
    this.overallScore = this.calibrationData.overall_score || this.calibrationData.score || (total / (raw.length || 1));

    this.segments = raw.map((r: any, i: number) => {
      const color = this.glowColors[i % this.glowColors.length];
      return {
        name: r.name || r.category || r.label || '',
        score: r.score || r.value || 0,
        pct: total > 0 ? Math.round(((r.score || r.value || 0) / total) * 100) : 0,
        bg: color.bg,
        glow: color.glow,
        labelColor: color.label
      };
    });

    this.lastInputHash = hash;
  }

  trackByName = (i: number, s: any): string => s.name || i.toString();
}
