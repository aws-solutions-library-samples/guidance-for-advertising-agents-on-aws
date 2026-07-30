import { Component, Input, ChangeDetectionStrategy, ChangeDetectorRef, OnChanges, SimpleChanges } from '@angular/core';
import { VisualizationCacheService } from '../../../services/visualization-cache.service';
import { VisualizationComponent } from '../visualization-component';

/**
 * ADCP Check Governance Visualization
 *
 * Full-width glass banner. PASS/HOLD/FAIL communicated via inner glow color intensity —
 * green, amber, red respectively. No filled background color, just the glow doing the work.
 */
@Component({
  selector: 'app-adcp-check-governance',
  templateUrl: './adcp-check-governance.component.html',
  styleUrls: ['./adcp-check-governance.component.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class AdcpCheckGovernanceComponent extends VisualizationComponent implements OnChanges {
  @Input() governanceData: any;

  verdict: string = '';
  verdictIcon: string = '';
  glowClass: string = '';
  message: string = '';
  checks: any[] = [];
  private lastInputHash: string = '';

  constructor(
    private cdr: ChangeDetectorRef,
    private cacheService: VisualizationCacheService
  ) {
    super();
  }

  ngOnChanges(ch: SimpleChanges): void {
    if (ch['governanceData']) {
      this.processVisualizationData();
      this.cdr.markForCheck();
    }
  }

  private processVisualizationData(): void {
    if (!this.governanceData) { this.verdict = ''; return; }
    const key = 'adcp-governance' + this.toolUseId;
    const hash = this.cacheService.generateKey(key, this.governanceData);
    if (this.lastInputHash === hash && this.verdict) return;

    const raw = (this.governanceData.verdict || this.governanceData.status || this.governanceData.result || '').toLowerCase();
    if (raw.includes('pass') || raw.includes('approved') || raw.includes('compliant')) {
      this.verdict = 'PASS';
      this.verdictIcon = 'check_circle';
      this.glowClass = 'glow-pass';
    } else if (raw.includes('hold') || raw.includes('review') || raw.includes('pending')) {
      this.verdict = 'HOLD';
      this.verdictIcon = 'pause_circle';
      this.glowClass = 'glow-hold';
    } else {
      this.verdict = 'FAIL';
      this.verdictIcon = 'cancel';
      this.glowClass = 'glow-fail';
    }

    this.message = this.governanceData.message || this.governanceData.summary || '';
    this.checks = (this.governanceData.checks || this.governanceData.rules || this.governanceData.items || []).map((c: any) => ({
      name: c.name || c.rule || c.check || '',
      status: (c.status || c.result || 'info').toLowerCase(),
      detail: c.detail || c.message || ''
    }));

    this.lastInputHash = hash;
  }

  getCheckIcon(status: string): string {
    if (status.includes('pass') || status.includes('ok')) return 'check_circle';
    if (status.includes('fail') || status.includes('error')) return 'cancel';
    return 'warning';
  }

  getCheckClass(status: string): string {
    if (status.includes('pass') || status.includes('ok')) return 'check-pass';
    if (status.includes('fail') || status.includes('error')) return 'check-fail';
    return 'check-warn';
  }

  trackByIndex = (i: number): number => i;
}
