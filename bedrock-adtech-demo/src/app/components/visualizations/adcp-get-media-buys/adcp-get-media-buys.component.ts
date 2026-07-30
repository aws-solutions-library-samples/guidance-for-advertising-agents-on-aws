import { Component, Input, ChangeDetectionStrategy, ChangeDetectorRef, OnChanges, SimpleChanges } from '@angular/core';
import { VisualizationCacheService } from '../../../services/visualization-cache.service';
import { VisualizationComponent } from '../visualization-component';

/**
 * ADCP Get Media Buys Visualization
 *
 * Stacked glass rows with slight gap. Status communicated via soft colored inner glow
 * per row (green/amber/grey). Pacing bar uses a frosted track with a glowing fill.
 */
@Component({
  selector: 'app-adcp-get-media-buys',
  templateUrl: './adcp-get-media-buys.component.html',
  styleUrls: ['./adcp-get-media-buys.component.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class AdcpGetMediaBuysComponent extends VisualizationComponent implements OnChanges {
  @Input() mediaBuysData: any;

  private processedData: any[] = [];
  private lastInputHash: string = '';

  constructor(
    private cdr: ChangeDetectorRef,
    private cacheService: VisualizationCacheService
  ) {
    super();
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['mediaBuysData']) {
      this.processVisualizationData();
      this.cdr.markForCheck();
    }
  }

  private processVisualizationData(): void {
    if (!this.mediaBuysData) { this.processedData = []; return; }
    const key = 'adcp-get-media-buys' + this.toolUseId;
    const hash = this.cacheService.generateKey(key, this.mediaBuysData);
    if (this.lastInputHash === hash && this.processedData.length) return;

    const buys = this.mediaBuysData.media_buys || this.mediaBuysData.buys || (Array.isArray(this.mediaBuysData) ? this.mediaBuysData : []);
    this.processedData = buys.map((b: any) => ({
      id: b.media_buy_id || b.id || '',
      name: b.name || b.product_name || '',
      status: (b.status || 'unknown').toLowerCase(),
      budget: b.budget || b.total_budget || 0,
      spent: b.spent || b.amount_spent || 0,
      currency: b.currency || 'USD',
      pacing: this.calcPacing(b),
      startDate: b.start_date || '',
      endDate: b.end_date || '',
      impressionsDelivered: b.impressions_delivered || 0,
      impressionsTarget: b.impressions_target || b.estimated_impressions || 0
    }));

    this.lastInputHash = hash;
  }

  get buys(): any[] { return this.processedData; }

  private calcPacing(b: any): number {
    const spent = b.spent || b.amount_spent || 0;
    const budget = b.budget || b.total_budget || 1;
    return Math.min(Math.round((spent / budget) * 100), 100);
  }

  getGlowClass(status: string): string {
    switch (status) {
      case 'active': case 'delivering': case 'live': return 'glow-green';
      case 'paused': case 'pending': case 'scheduled': return 'glow-amber';
      default: return 'glow-grey';
    }
  }

  formatCurrency(value: number, currency: string = 'USD'): string {
    if (!value && value !== 0) return '';
    return new Intl.NumberFormat('en-US', { style: 'currency', currency, maximumFractionDigits: 0 }).format(value);
  }

  formatNumber(value: number): string {
    if (!value) return '0';
    if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
    if (value >= 1_000) return `${(value / 1_000).toFixed(0)}K`;
    return value.toLocaleString();
  }

  trackById = (index: number, item: any): string => item.id || index.toString();
}
