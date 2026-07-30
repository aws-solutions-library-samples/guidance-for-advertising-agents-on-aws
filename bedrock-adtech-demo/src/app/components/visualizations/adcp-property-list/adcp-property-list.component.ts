import { Component, Input, ChangeDetectionStrategy, ChangeDetectorRef, OnChanges, SimpleChanges } from '@angular/core';
import { VisualizationCacheService } from '../../../services/visualization-cache.service';
import { VisualizationComponent } from '../visualization-component';

/**
 * ADCP Property List Visualization
 *
 * Frosted chip cluster. Each domain chip is a mini glass pill.
 * Overflow chip matches the same style.
 */
@Component({
  selector: 'app-adcp-property-list',
  templateUrl: './adcp-property-list.component.html',
  styleUrls: ['./adcp-property-list.component.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class AdcpPropertyListComponent extends VisualizationComponent implements OnChanges {
  @Input() propertyData: any;

  visibleProperties: any[] = [];
  overflowCount: number = 0;
  showAll: boolean = false;
  allProperties: any[] = [];
  maxVisible: number = 12;
  private lastInputHash: string = '';

  constructor(
    private cdr: ChangeDetectorRef,
    private cacheService: VisualizationCacheService
  ) {
    super();
  }

  ngOnChanges(ch: SimpleChanges): void {
    if (ch['propertyData']) {
      this.processVisualizationData();
      this.cdr.markForCheck();
    }
  }

  private processVisualizationData(): void {
    if (!this.propertyData) { this.allProperties = []; this.visibleProperties = []; return; }
    const key = 'adcp-property-list' + this.toolUseId;
    const hash = this.cacheService.generateKey(key, this.propertyData);
    if (this.lastInputHash === hash && this.allProperties.length) return;

    const raw = this.propertyData.properties || this.propertyData.domains || (Array.isArray(this.propertyData) ? this.propertyData : []);
    this.allProperties = raw.map((p: any) => ({
      domain: p.domain || p.publisher_domain || p.name || '',
      type: p.type || p.channel || '',
      icon: this.getTypeIcon(p.type || p.channel || '')
    }));

    this.updateVisible();
    this.lastInputHash = hash;
  }

  private updateVisible(): void {
    if (this.showAll) {
      this.visibleProperties = this.allProperties;
      this.overflowCount = 0;
    } else {
      this.visibleProperties = this.allProperties.slice(0, this.maxVisible);
      this.overflowCount = Math.max(0, this.allProperties.length - this.maxVisible);
    }
  }

  toggleShowAll(): void {
    this.showAll = !this.showAll;
    this.updateVisible();
    this.cdr.markForCheck();
  }

  private getTypeIcon(type: string): string {
    const t = (type || '').toLowerCase();
    if (t.includes('ctv') || t.includes('tv')) return 'tv';
    if (t.includes('video')) return 'play_circle';
    if (t.includes('audio') || t.includes('podcast')) return 'headphones';
    if (t.includes('display')) return 'web';
    if (t.includes('mobile')) return 'smartphone';
    return 'language';
  }

  trackByDomain = (i: number, p: any): string => p.domain || i.toString();
}
