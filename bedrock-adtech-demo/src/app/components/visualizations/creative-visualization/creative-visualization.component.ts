import { Component, Input } from '@angular/core';

/**
 * Creative Visualization Component
 * 
 * Displays creative assets, visual recommendations, and design strategies in an image gallery format.
 * Integrates with Amazon Nova Canvas for AI-generated creative content display.
 * 
 * @example
 * ```html
 * <app-creative-visualization [creativeData]="creativeAssets"></app-creative-visualization>
 * ```
 * 
 * @example JSON Structure:
 * ```json
 * {
 *   "visualizationType": "creative",
 *   "title": "AI-Generated Creative Assets",
 *   "subtitle": "Holiday Campaign Visual Concepts",
 *   "creatives": [
 *     {
 *       "imageId": "nova-canvas-12345",
 *       "primaryLabel": "Holiday Premium Banner",
 *       "secondaryLabel": "Premium Audience",
 *       "description": "Elegant holiday-themed banner with premium brand positioning",
 *       "imageUrl": "https://s3.amazonaws.com/creative-assets/holiday-premium-banner.jpg",
 *       "thumbnailUrl": "https://s3.amazonaws.com/creative-assets/thumbnails/holiday-premium-banner-thumb.jpg",
 *       "status": "ready",
 *       "metadata": {
 *         "colorScheme": ["#1a365d", "#2d3748", "#e2e8f0", "#f7fafc"],
 *         "dimensions": "728x90",
 *         "format": "JPEG",
 *         "generated": "2024-12-01T14:30:00Z",
 *         "tags": ["holiday", "premium", "banner", "elegant"]
 *       },
 *       "performance": {
 *         "expectedCTR": "2.8%",
 *         "expectedEngagement": "High",
 *         "targetAudience": "Premium shoppers 25-45"
 *       }
 *     },
 *     {
 *       "imageId": "nova-canvas-12346",
 *       "primaryLabel": "Holiday Social Video Thumbnail",
 *       "secondaryLabel": "Social Media",
 *       "description": "Eye-catching thumbnail for holiday video campaign",
 *       "imageUrl": "https://s3.amazonaws.com/creative-assets/holiday-social-thumb.jpg",
 *       "thumbnailUrl": "https://s3.amazonaws.com/creative-assets/thumbnails/holiday-social-thumb-small.jpg",
 *       "status": "pending",
 *       "metadata": {
 *         "colorScheme": ["#dc2626", "#fbbf24", "#10b981"],
 *         "dimensions": "1200x630",
 *         "format": "JPEG",
 *         "generated": "2024-12-01T14:35:00Z",
 *         "tags": ["holiday", "social", "video", "thumbnail"]
 *       },
 *       "performance": {
 *         "expectedCTR": "3.2%",
 *         "expectedEngagement": "Very High",
 *         "targetAudience": "Social media users 18-35"
 *       }
 *     }
 *   ],
 *   "summary": {
 *     "totalCreatives": 4,
 *     "readyForUse": 2,
 *     "averageScore": 8.5,
 *     "recommendedVariation": "Holiday Premium Banner",
 *     "strategy": "A/B test premium vs. playful creative approaches"
 *   },
 *   "guidelines": {
 *     "brandColors": ["#1a365d", "#2d3748", "#e2e8f0"],
 *     "tone": "Premium yet approachable",
 *     "keyMessage": "Discover holiday magic with premium quality"
 *   }
 * }
 * ```
 * 
 * @features
 * - Image gallery with thumbnails and full-size previews
 * - Creative metadata display (dimensions, colors, tags)
 * - Performance predictions and target audience info
 * - Status indicators (pending, ready, approved)
 * - Brand guideline compliance display
 * - Integration with Nova Canvas generated content
 * 
 * @useCases
 * - Creative asset generation display
 * - Visual strategy recommendations
 * - Brand guideline compliance
 * - Creative performance predictions
 * - A/B testing variations
 */
@Component({
  selector: 'app-creative-visualization',
  templateUrl: './creative-visualization.component.html',
  styleUrls: ['./creative-visualization.component.scss']
})
export class CreativeVisualizationComponent {
  @Input() creativeData: any;
  @Input() compactMode: boolean = false;
  processedCreativeData: any =[];
  constructor() { }

  // Helper method to get creative title (generic or specific)
  getCreativeTitle(): string {
    if (!this.creativeData) return 'Creative Assets';
    return this.creativeData.primaryLabel || this.creativeData.title || 'Creative Assets';
  }

  // Helper method to get creative description (generic or specific)
  getCreativeDescription(): string {
    if (!this.creativeData) return '';
    return this.creativeData.description || this.creativeData.secondaryLabel || '';
  }

  // Helper method to process creative data for the image gallery
  getProcessedCreativeData(): any {
    if (!this.creativeData) return null;

    // Create a processed version that maintains backward compatibility
    const processed = { ...this.creativeData };

    // Ensure title is set for the image gallery
    if (!processed.title) {
      processed.title = this.getCreativeTitle();
    }
    
    // Add description if available
    if (!processed.description && this.getCreativeDescription()) {
      processed.description = this.getCreativeDescription();
    }
    
    // Handle different image data properties - prioritize creatives, then imagery, then imageDescriptions
    if (!processed.creatives && !processed.imagery && processed.imageDescriptions) {
      processed.imagery = processed.imageDescriptions;
    }
    
    // Also ensure creatives is available if it's the primary data source
    if (!processed.creatives && processed.imagery) {
      processed.creatives = processed.imagery;
    }
    
    // Handle generic colorPalette field
    if (processed.colorPalette && !processed.colors) {
      processed.colors = processed.colorPalette;
    }
    
    return processed;
  }
} 