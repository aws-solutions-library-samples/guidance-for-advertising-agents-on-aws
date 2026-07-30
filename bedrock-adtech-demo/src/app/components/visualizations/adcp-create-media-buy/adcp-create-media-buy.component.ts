import { Component, Input, ChangeDetectionStrategy, ChangeDetectorRef, OnChanges, SimpleChanges } from '@angular/core';
import { VisualizationCacheService } from '../../../services/visualization-cache.service';
import { VisualizationComponent } from '../visualization-component';

/**
 * ADCP Create Media Buy Visualization
 *
 * Frosted confirmation chip. Subtle green inner glow on the glass surface.
 * Checkmark + ID + budget in one row.
 */
@Component({
  selector: 'app-adcp-create-media-buy',
  templateUrl: './adcp-create-media-buy.component.html',
  styleUrls: ['./adcp-create-media-buy.component.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class AdcpCreateMediaBuyComponent extends VisualizationComponent implements OnChanges {
  @Input() mediaBuyData: any;

  private processedData: any = null;
  private lastInputHash: string = '';

  constructor(
    private cdr: ChangeDetectorRef,
    private cacheService: VisualizationCacheService
  ) {
    super();
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['mediaBuyData']) {
      this.processVisualizationData();
      this.cdr.markForCheck();
    }
  }

  private processVisualizationData(): void {
    if (!this.mediaBuyData) { this.processedData = null; return; }
    const key = 'adcp-create-media-buy' + this.toolUseId;
    const hash = this.cacheService.generateKey(key, this.mediaBuyData);
    if (this.lastInputHash === hash && this.processedData) return;
    const cached = this.cacheService.getCachedVisualizationData(key, this.mediaBuyData);
    if (cached) { this.processedData = cached; this.lastInputHash = hash; return; }

    this.processedData = {
      mediaBuyId: this.mediaBuyData.media_buy_id || this.mediaBuyData.id || '',
      status: this.mediaBuyData.status || 'confirmed',
      budget: this.mediaBuyData.budget || this.mediaBuyData.total_budget || 0,
      currency: this.mediaBuyData.currency || 'USD',
      productName: this.mediaBuyData.product_name || this.mediaBuyData.name || '',
      startDate: this.mediaBuyData.start_date || '',
      endDate: this.mediaBuyData.end_date || '',
      impressions: this.mediaBuyData.estimated_impressions || this.mediaBuyData.impressions || 0
    };

    this.cacheService.cacheVisualizationData(key, this.mediaBuyData, this.processedData);
    this.lastInputHash = hash;
  }

  get data(): any { return this.processedData || this.mediaBuyData || {}; }

  formatCurrency(value: number, currency: string = 'USD'): string {
    if (!value && value !== 0) return '';
    return new Intl.NumberFormat('en-US', { style: 'currency', currency, maximumFractionDigits: 0 }).format(value);
  }

  formatNumber(value: number): string {
    if (!value) return '';
    if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
    if (value >= 1_000) return `${(value / 1_000).toFixed(0)}K`;
    return value.toLocaleString();
  }
}
