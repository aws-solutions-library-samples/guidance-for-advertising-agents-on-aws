import { Component, Input, ChangeDetectionStrategy, ChangeDetectorRef, OnChanges, SimpleChanges } from '@angular/core';
import { VisualizationCacheService } from '../../../services/visualization-cache.service';
import { VisualizationComponent } from '../visualization-component';

/**
 * ADCP Update Media Buy Visualization
 *
 * Glass chip per changed field. Old value in muted opacity, arrow, new value in full brightness.
 * Amber inner glow for the change state.
 */
@Component({
  selector: 'app-adcp-update-media-buy',
  templateUrl: './adcp-update-media-buy.component.html',
  styleUrls: ['./adcp-update-media-buy.component.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class AdcpUpdateMediaBuyComponent extends VisualizationComponent implements OnChanges {
  @Input() updateData: any;

  changes: any[] = [];
  mediaBuyId: string = '';
  private lastInputHash: string = '';

  constructor(
    private cdr: ChangeDetectorRef,
    private cacheService: VisualizationCacheService
  ) {
    super();
  }

  ngOnChanges(ch: SimpleChanges): void {
    if (ch['updateData']) {
      this.processVisualizationData();
      this.cdr.markForCheck();
    }
  }

  private processVisualizationData(): void {
    if (!this.updateData) { this.changes = []; return; }
    const key = 'adcp-update-media-buy' + this.toolUseId;
    const hash = this.cacheService.generateKey(key, this.updateData);
    if (this.lastInputHash === hash && this.changes.length) return;

    this.mediaBuyId = this.updateData.media_buy_id || this.updateData.id || '';

    const raw = this.updateData.changes || this.updateData.updated_fields || this.updateData.fields || [];
    if (Array.isArray(raw)) {
      this.changes = raw.map((c: any) => ({
        field: c.field || c.field_name || c.name || '',
        oldValue: c.old_value ?? c.previous ?? c.from ?? '',
        newValue: c.new_value ?? c.current ?? c.to ?? ''
      }));
    } else if (typeof raw === 'object') {
      this.changes = Object.keys(raw).map(k => ({
        field: k,
        oldValue: raw[k].old_value ?? raw[k].from ?? '',
        newValue: raw[k].new_value ?? raw[k].to ?? ''
      }));
    }

    this.lastInputHash = hash;
  }

  formatLabel(field: string): string {
    return field.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
  }

  trackByField = (i: number, c: any): string => c.field || i.toString();
}
