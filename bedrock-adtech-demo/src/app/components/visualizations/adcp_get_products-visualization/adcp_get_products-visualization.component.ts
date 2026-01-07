import { Component, Input, ChangeDetectionStrategy, ChangeDetectorRef, OnChanges, SimpleChanges } from '@angular/core';
import { VisualizationCacheService } from '../../../services/visualization-cache.service';

/**
 * ADCP Inventory Visualization Component
 * 
 * Displays advertising inventory products from AdCP get_products responses.
 * Shows product cards with pricing options, publisher properties, and format details.
 * 
 * @example
 * ```html
 * <app-adcp_get_products-visualization [inventoryData]="adcpInventoryData"></app-adcp_get_products-visualization>
 * ```
 * 
 * @example JSON Structure (from AdCP get_products - official schema):
 * ```json
 * {
 *   "products": [
 *     {
 *       "product_id": "prod_espn_ctv_001",
 *       "name": "Premium Sports CTV - Live Events",
 *       "description": "High-impact CTV inventory during live sports events",
 *       "publisher_properties": [
 *         {
 *           "publisher_domain": "espn.com",
 *           "selection_type": "by_tag",
 *           "property_tags": ["ctv", "live_sports"]
 *         }
 *       ],
 *       "format_ids": [
 *         {"agent_url": "https://creatives.adcontextprotocol.org", "id": "video_hosted", "duration_ms": 30000},
 *         {"agent_url": "https://creatives.adcontextprotocol.org", "id": "video_hosted", "duration_ms": 15000}
 *       ],
 *       "delivery_type": "guaranteed",
 *       "pricing_options": [
 *         {
 *           "pricing_option_id": "cpm_fixed_001",
 *           "pricing_model": "cpm",
 *           "rate": 42.50,
 *           "currency": "USD",
 *           "min_spend": 10000
 *         }
 *       ],
 *       "estimated_exposures": 2500000,
 *       "delivery_measurement": {
 *         "provider": "Nielsen DAR",
 *         "notes": "Panel-based demographic measurement"
 *       },
 *       "brief_relevance": "Premium sports inventory matches target male 25-54 demographic"
 *     }
 *   ],
 *   "errors": []
 * }
 * ```
 */
@Component({
  selector: 'app-adcp_get_products-visualization',
  templateUrl: './adcp_get_products-visualization.component.html',
  styleUrls: ['./adcp_get_products-visualization.component.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class AdcpInventoryVisualizationComponent implements OnChanges {
  @Input() inventoryData: any;
  @Input() compactMode: boolean = false;

  private processedData: any = null;
  private lastInputHash: string = '';

  constructor(
    private cdr: ChangeDetectorRef,
    private cacheService: VisualizationCacheService
  ) { }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['inventoryData']) {
      this.processVisualizationData();
      this.cdr.markForCheck();
    }
  }

  private processVisualizationData(): void {
    if (!this.inventoryData) {
      this.processedData = null;
      return;
    }

    const currentHash = this.cacheService.generateKey('adcp_get_products-visualization', this.inventoryData);
    
    if (this.lastInputHash === currentHash && this.processedData) {
      return;
    }

    const cached = this.cacheService.getCachedVisualizationData('adcp_get_products-visualization', this.inventoryData);
    if (cached) {
      this.processedData = cached;
      this.lastInputHash = currentHash;
      return;
    }

    const products = this.getProducts();
    this.processedData = {
      products: products.map(product => ({
        ...product,
        // Extract primary pricing option
        primaryPricing: this.getPrimaryPricing(product.pricing_options),
        // Format publisher properties for display
        publisherDisplay: this.formatPublisherProperties(product.publisher_properties),
        // Format format_ids for display
        formatsDisplay: this.formatFormatIds(product.format_ids),
        // Format estimated exposures
        formattedExposures: this.formatNumber(product.estimated_exposures)
      })),
      totalFound: products.length,
      errors: this.inventoryData.errors
    };

    this.cacheService.cacheVisualizationData('adcp_get_products-visualization', this.inventoryData, this.processedData);
    this.lastInputHash = currentHash;
  }

  getProducts(): any[] {
    if (!this.inventoryData) return [];
    if (this.inventoryData.products) return this.inventoryData.products;
    if (Array.isArray(this.inventoryData)) return this.inventoryData;
    return [];
  }

  getProcessedProducts(): any[] {
    return this.processedData?.products || this.getProducts();
  }

  getTotalFound(): number {
    return this.processedData?.totalFound || this.getProducts().length;
  }

  getErrors(): any[] {
    return this.processedData?.errors || this.inventoryData?.errors || [];
  }

  hasErrors(): boolean {
    return this.getErrors().length > 0;
  }

  /**
   * Get primary pricing option from pricing_options array
   */
  getPrimaryPricing(pricingOptions: any[]): any {
    if (!pricingOptions || pricingOptions.length === 0) return null;
    // Return first pricing option as primary
    const primary = pricingOptions[0];
    return {
      model: primary.pricing_model,
      rate: primary.rate,
      currency: primary.currency || 'USD',
      minSpend: primary.min_spend,
      floorPrice: primary.floor_price,
      formattedRate: this.formatCurrency(primary.rate, primary.currency),
      formattedMinSpend: primary.min_spend ? this.formatCurrency(primary.min_spend, primary.currency) : null
    };
  }

  /**
   * Format publisher_properties array for display
   */
  formatPublisherProperties(publisherProperties: any[]): any {
    if (!publisherProperties || publisherProperties.length === 0) return null;
    
    return publisherProperties.map(pp => ({
      domain: pp.publisher_domain,
      selectionType: pp.selection_type,
      propertyIds: pp.property_ids,
      propertyTags: pp.property_tags
    }));
  }

  /**
   * Get primary publisher domain for display
   */
  getPrimaryPublisher(product: any): string {
    if (product.publisherDisplay && product.publisherDisplay.length > 0) {
      return product.publisherDisplay[0].domain;
    }
    if (product.publisher_properties && product.publisher_properties.length > 0) {
      return product.publisher_properties[0].publisher_domain;
    }
    return '';
  }

  /**
   * Format format_ids array for display
   */
  formatFormatIds(formatIds: any[]): any[] {
    if (!formatIds || formatIds.length === 0) return [];
    
    return formatIds.map(f => {
      let label = f.id;
      if (f.width && f.height) {
        label += ` (${f.width}x${f.height})`;
      }
      if (f.duration_ms) {
        label += ` ${f.duration_ms / 1000}s`;
      }
      return {
        id: f.id,
        agentUrl: f.agent_url,
        width: f.width,
        height: f.height,
        durationMs: f.duration_ms,
        label: label
      };
    });
  }

  /**
   * Get channel from format_ids (infer from format type)
   */
  getChannelFromFormats(product: any): string {
    const formats = product.formatsDisplay || this.formatFormatIds(product.format_ids);
    if (!formats || formats.length === 0) return 'display';
    
    const firstFormat = formats[0].id?.toLowerCase() || '';
    if (firstFormat.includes('video') || firstFormat.includes('vast')) return 'video';
    if (firstFormat.includes('audio') || firstFormat.includes('daast')) return 'audio';
    if (firstFormat.includes('native')) return 'native';
    if (firstFormat.includes('dooh')) return 'dooh';
    return 'display';
  }

  formatCurrency(value: number, currency: string = 'USD'): string {
    if (!value && value !== 0) return '';
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: currency,
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    }).format(value);
  }

  formatNumber(value: number): string {
    if (!value && value !== 0) return '';
    if (value >= 1000000) {
      return `${(value / 1000000).toFixed(1)}M`;
    }
    if (value >= 1000) {
      return `${(value / 1000).toFixed(0)}K`;
    }
    return value.toLocaleString();
  }

  getChannelIcon(channel: string): string {
    const icons: { [key: string]: string } = {
      'ctv': 'tv',
      'video': 'play_circle',
      'display': 'web',
      'audio': 'headphones',
      'native': 'article',
      'dooh': 'storefront',
      'podcast': 'podcasts',
      'retail': 'shopping_cart',
      'social': 'share'
    };
    return icons[channel?.toLowerCase()] || 'campaign';
  }

  getChannelColor(channel: string): string {
    const colors: { [key: string]: string } = {
      'ctv': '#667eea',
      'video': '#10b981',
      'display': '#f59e0b',
      'audio': '#8b5cf6',
      'native': '#06b6d4',
      'dooh': '#ec4899',
      'podcast': '#14b8a6',
      'retail': '#f97316',
      'social': '#3b82f6'
    };
    return colors[channel?.toLowerCase()] || '#6b7280';
  }

  getDeliveryTypeColor(deliveryType: string): string {
    return deliveryType === 'guaranteed' ? '#10b981' : '#f59e0b';
  }

  formatDeliveryType(deliveryType: string): string {
    if (!deliveryType) return '';
    return deliveryType.replace('_', ' ').replace(/\b\w/g, l => l.toUpperCase());
  }

  getPricingModelLabel(model: string): string {
    const labels: { [key: string]: string } = {
      'cpm': 'CPM',
      'vcpm': 'vCPM',
      'cpc': 'CPC',
      'cpcv': 'CPCV',
      'cpv': 'CPV',
      'cpp': 'CPP',
      'flat_rate': 'Flat Rate'
    };
    return labels[model?.toLowerCase()] || model?.toUpperCase() || '';
  }

  trackByProductId = (index: number, product: any): string => {
    return product.product_id || index.toString();
  }

  trackByIndex = (index: number): number => {
    return index;
  }
}
