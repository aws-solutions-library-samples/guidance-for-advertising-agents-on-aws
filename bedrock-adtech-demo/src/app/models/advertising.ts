export interface Publisher {
    id: string;
    name: string;
    tier: string;
    cpmFloor: number;
    brandSafety: number;
    viewability: number;
    availableInventory: number;
    demandPressure: number;
    fillRateForecast: number;
    seasonalMultiplier: number;
    inventoryGrowth: number;
    peakDemandPeriods: string[];
    yieldOptimization: number;
    revenuePrediction: number;
    competitivePressure: number;
    premiumInventoryShare: number;
    dynamicPricingEnabled: boolean;
    revenueGrowthTarget: number;
    
    // Optional generic properties for flexible context display
    primaryLabel?: string;
    category?: string;
    type?: string;
    budget?: number;
    value?: number;
    performance?: string;
    status?: string;
  }
  
  export interface Content {
    id: string;
    title: string;
    category: string;
    audience_size: number;
    engagement_rate: number;
    brand_safety_score: number;
    demographics: string;
    cpm_potential: number;
    completion_rate: number;
    seasonal_multiplier: number;
    advertiser_alignment: string[];
    content_themes: string[];
    optimal_formats: string[];
    
    // Optional generic properties for flexible context display
    name?: string;
    value?: number;
    budget?: number;
    performance?: string;
    status?: string;
  }