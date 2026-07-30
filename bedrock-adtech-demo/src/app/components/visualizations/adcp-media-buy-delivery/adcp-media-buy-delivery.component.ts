import { Component, Input, ChangeDetectionStrategy, ChangeDetectorRef, OnChanges, SimpleChanges } from '@angular/core';
import { VisualizationCacheService } from '../../../services/visualization-cache.service';
import { VisualizationComponent } from '../visualization-component';

/**
 * ADCP Media Buy Delivery Visualization
 *
 * Glass tile. KPI numbers in bright white, sparkline rendered in a glowing accent color
 * against the frosted background.
 */
@Component({
  selector: 'app-adcp-media-buy-delivery',
  templateUrl: './adcp-media-buy-delivery.component.html',
  styleUrls: ['./adcp-media-buy-delivery.component.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class AdcpMediaBuyDeliveryComponent extends VisualizationComponent implements OnChanges {
  @Input() deliveryData: any;

  kpis: any[] = [];
  sparklinePath: string = '';
  sparklineWidth: number = 200;
  sparklineHeight: number = 40;
  private lastInputHash: string = '';

  constructor(
    private cdr: ChangeDetectorRef,
    private cacheService: VisualizationCacheService
  ) {
    super();
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['deliveryData']) {
      this.processVisualizationData();
      this.cdr.markForCheck();
    }
  }

  private processVisualizationData(): void {
    if (!this.deliveryData) { this.kpis = []; return; }
    const key = 'adcp-delivery' + this.toolUseId;
    const hash = this.cacheService.generateKey(key, this.deliveryData);
    if (this.lastInputHash === hash && this.kpis.length) return;

    this.kpis = [
      { label: 'Impressions', value: this.formatNumber(this.deliveryData.impressions_delivered || this.deliveryData.impressions || 0), icon: 'visibility' },
      { label: 'Reach', value: this.formatNumber(this.deliveryData.reach || this.deliveryData.unique_reach || 0), icon: 'people' },
      { label: 'Completion', value: this.formatPct(this.deliveryData.completion_rate || this.deliveryData.vcr || 0), icon: 'check_circle' },
      { label: 'Spend', value: this.formatCurrency(this.deliveryData.spend || this.deliveryData.amount_spent || 0), icon: 'payments' }
    ].filter(k => k.value && k.value !== '$0' && k.value !== '0');

    // Build sparkline from daily data if available
    const daily = this.deliveryData.daily_delivery || this.deliveryData.daily || this.deliveryData.sparkline || [];
    if (daily.length > 1) {
      this.sparklinePath = this.buildSparkline(daily);
    }

    this.lastInputHash = hash;
  }

  private buildSparkline(data: number[]): string {
    const max = Math.max(...data);
    const min = Math.min(...data);
    const range = max - min || 1;
    const w = this.sparklineWidth;
    const h = this.sparklineHeight;
    const step = w / (data.length - 1);

    return data.map((v, i) => {
      const x = i * step;
      const y = h - ((v - min) / range) * (h - 4) - 2;
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ');
  }

  formatNumber(v: number): string {
    if (!v) return '0';
    if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
    if (v >= 1_000) return `${(v / 1_000).toFixed(0)}K`;
    return v.toLocaleString();
  }

  formatCurrency(v: number): string {
    if (!v) return '$0';
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(v);
  }

  formatPct(v: number): string {
    if (!v) return '0%';
    return `${(v * 100).toFixed(1)}%`;
  }
}
