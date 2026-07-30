import { Component, Input, ChangeDetectionStrategy, ChangeDetectorRef, OnChanges, SimpleChanges } from '@angular/core';
import { VisualizationCacheService } from '../../../services/visualization-cache.service';
import { VisualizationComponent } from '../visualization-component';

/**
 * ADCP Content Standards Visualization
 *
 * Collapsed frosted pill with a subtle shimmer border.
 * Expands to a glass panel with rule rows.
 */
@Component({
  selector: 'app-adcp-content-standards',
  templateUrl: './adcp-content-standards.component.html',
  styleUrls: ['./adcp-content-standards.component.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class AdcpContentStandardsComponent extends VisualizationComponent implements OnChanges {
  @Input() standardsData: any;

  standards: any[] = [];
  expandedIndex: number = -1;
  private lastInputHash: string = '';

  constructor(
    private cdr: ChangeDetectorRef,
    private cacheService: VisualizationCacheService
  ) {
    super();
  }

  ngOnChanges(ch: SimpleChanges): void {
    if (ch['standardsData']) {
      this.processVisualizationData();
      this.cdr.markForCheck();
    }
  }

  private processVisualizationData(): void {
    if (!this.standardsData) { this.standards = []; return; }
    const key = 'adcp-content-standards' + this.toolUseId;
    const hash = this.cacheService.generateKey(key, this.standardsData);
    if (this.lastInputHash === hash && this.standards.length) return;

    const raw = this.standardsData.standards || this.standardsData.categories || (Array.isArray(this.standardsData) ? this.standardsData : [this.standardsData]);
    this.standards = raw.map((s: any) => ({
      name: s.name || s.category || 'Standard',
      icon: this.getIcon(s.name || s.category || ''),
      rules: (s.rules || s.items || s.requirements || []).map((r: any) => ({
        name: r.name || r.rule || r.title || '',
        description: r.description || r.detail || '',
        severity: (r.severity || r.level || 'info').toLowerCase()
      }))
    }));

    this.lastInputHash = hash;
  }

  toggle(i: number): void {
    this.expandedIndex = this.expandedIndex === i ? -1 : i;
    this.cdr.markForCheck();
  }

  private getIcon(name: string): string {
    const n = name.toLowerCase();
    if (n.includes('brand')) return 'branding_watermark';
    if (n.includes('legal') || n.includes('compliance')) return 'gavel';
    if (n.includes('format')) return 'aspect_ratio';
    if (n.includes('content')) return 'description';
    return 'rule';
  }

  trackByName = (i: number, s: any): string => s.name || i.toString();
  trackByRuleIndex = (i: number): number => i;
}
