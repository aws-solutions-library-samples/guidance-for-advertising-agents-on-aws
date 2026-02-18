/* import { Injectable } from '@angular/core';
import { Observable, BehaviorSubject, from, throwError } from 'rxjs';
import { map, catchError, tap } from 'rxjs/operators';
import { BedrockService } from './bedrock.service';

export interface AudienceSegment {
  segment_id: string;
  description: string;
  vector: number[];
  demographic_affinity: string[];
  interest_categories: string[];
  purchase_intent: string[];
  average_value: number;
  conversion_rate: number;
  device_preferences: {
    mobile: number;
    desktop: number;
    tablet: number;
  };
  active_hours: {
    morning: number;
    afternoon: number;
    evening: number;
    night: number;
  };
  content_affinities: { [key: string]: number };
}

export interface BiddingStrategy {
  strategy_id: string;
  name: string;
  type: string;
  parameters: {
    target_cpc?: number;
    target_roas?: number;
    bid_adjustment_factor: number;
    frequency_cap?: number;
    daypart_modifiers?: { [key: string]: number };
    audience_value_weighting?: boolean;
    creative_performance_boost?: number;
  };
  performance_expectations: {
    volume_multiplier: number;
    efficiency_score: number;
    conversion_rate_impact: number;
  };
}

export interface BidRequestSample {
  request_id: string;
  timestamp: string;
  device: {
    type: string;
    os: string;
    browser: string;
  };
  user: {
    segments: string[];
    location: {
      country: string;
      region: string;
      city: string;
    };
  };
  site: {
    domain: string;
    page_url: string;
    content_categories: string[];
  };
  imp: {
    id: string;
    banner: {
      w: number;
      h: number;
      pos: number;
    };
    bidfloor: number;
    bidfloorcur: string;
  };
}

export interface PublisherInventory {
  publisher_id: string;
  name: string;
  domain: string;
  content_categories: string[];
  audience_reach: number;
  ad_formats: string[];
  pricing: {
    floor_cpm: number;
    premium_multiplier: number;
  };
  performance_metrics: {
    viewability_rate: number;
    click_through_rate: number;
    completion_rate: number;
  };
}

export interface CampaignScenario {
  scenario_id: string;
  name: string;
  description: string;
  objectives: string[];
  target_audience: string[];
  budget: {
    total: number;
    daily: number;
  };
  duration: {
    start_date: string;
    end_date: string;
  };
  kpis: {
    primary: string;
    secondary: string[];
    targets: { [key: string]: number };
  };
}

export interface UrlContentSample {
  url_id: string;
  url: string;
  title: string;
  content_categories: string[];
  sentiment: string;
  brand_safety_score: number;
  content_quality: string;
  engagement_metrics: {
    time_on_page: number;
    bounce_rate: number;
    social_shares: number;
  };
}

export interface BrandSafetyRule {
  rule_id: string;
  name: string;
  description: string;
  category: string;
  severity: string;
  conditions: {
    keywords: string[];
    categories: string[];
    domains: string[];
  };
  actions: {
    block: boolean;
    adjust_bid: number;
    require_review: boolean;
  };
}

export interface IndustryData {
  industry_id: string;
  name: string;
  description: string;
  segments: string[];
  seasonal_trends: { [key: string]: number };
  competitive_landscape: {
    major_players: string[];
    market_share: { [key: string]: number };
  };
  content_preferences: {
    formats: string[];
    channels: string[];
    messaging: string[];
  };
}

@Injectable({
  providedIn: 'root'
})
export class ConfigurationService {
  private audienceSegmentsSubject = new BehaviorSubject<AudienceSegment[]>([]);
  private biddingStrategiesSubject = new BehaviorSubject<BiddingStrategy[]>([]);
  private bidRequestSamplesSubject = new BehaviorSubject<BidRequestSample[]>([]);
  private publisherInventorySubject = new BehaviorSubject<PublisherInventory[]>([]);
  private campaignScenariosSubject = new BehaviorSubject<CampaignScenario[]>([]);
  private urlContentSamplesSubject = new BehaviorSubject<UrlContentSample[]>([]);
  private brandSafetyRulesSubject = new BehaviorSubject<BrandSafetyRule[]>([]);
  private industryDataSubject = new BehaviorSubject<IndustryData[]>([]);

  public audienceSegments$ = this.audienceSegmentsSubject.asObservable();
  public biddingStrategies$ = this.biddingStrategiesSubject.asObservable();
  public bidRequestSamples$ = this.bidRequestSamplesSubject.asObservable();
  public publisherInventory$ = this.publisherInventorySubject.asObservable();
  public campaignScenarios$ = this.campaignScenariosSubject.asObservable();
  public urlContentSamples$ = this.urlContentSamplesSubject.asObservable();
  public brandSafetyRules$ = this.brandSafetyRulesSubject.asObservable();
  public industryData$ = this.industryDataSubject.asObservable();

  private loadingSubject = new BehaviorSubject<boolean>(false);
  public loading$ = this.loadingSubject.asObservable();

  constructor(private bedrockService: BedrockService) {
    this.loadAllConfigurations();
  }

  private loadAllConfigurations(): void {
    this.loadingSubject.next(true);
    
    // Load all configuration data in parallel
    Promise.all([
      this.loadAudienceSegments(),
      this.loadBiddingStrategies(),
      this.loadBidRequestSamples(),
      this.loadPublisherInventory(),
      this.loadCampaignScenarios(),
      this.loadUrlContentSamples(),
      this.loadBrandSafetyRules(),
      this.loadIndustryData()
    ]).finally(() => {
      this.loadingSubject.next(false);
    });
  }

  private async loadAudienceSegments(): Promise<void> {
    try {
      const message = "Please provide all available audience segments from your knowledge base. Include demographic profiles, interest categories, device preferences, and performance metrics for each segment.";
      
      const response = await this.bedrockService.invokeAgent('supervisor', message);
      
      if (response && response.response) {
        // Parse the agent response to extract audience segments
        const segments = this.parseAgentResponse<AudienceSegment[]>(response.response, 'segments');
        this.audienceSegmentsSubject.next(segments);
      }
    } catch (error) {
      console.error('Error loading audience segments:', error);
      // Fallback to empty array
      this.audienceSegmentsSubject.next([]);
    }
  }

  private async loadBiddingStrategies(): Promise<void> {
    try {
      const message = "Please provide all available bidding strategies from your knowledge base. Include strategy types, parameters, performance expectations, and optimization recommendations for different campaign objectives.";
      
      const response = await this.bedrockService.invokeAgent('supervisor', message);
      
      if (response && response.response) {
        const strategies = this.parseAgentResponse<BiddingStrategy[]>(response.response, 'bidding_strategies');
        this.biddingStrategiesSubject.next(strategies);
      }
    } catch (error) {
      console.error('Error loading bidding strategies:', error);
      this.biddingStrategiesSubject.next([]);
    }
  }

  private async loadBidRequestSamples(): Promise<void> {
    try {
      const message = "Please provide sample bid requests from your knowledge base. Include device information, user segments, site details, and impression specifications. Limit to 20 representative samples.";
      
      const response = await this.bedrockService.invokeAgent('supervisor', message);
      
      if (response && response.response) {
        const samples = this.parseAgentResponse<BidRequestSample[]>(response.response, 'samples');
        this.bidRequestSamplesSubject.next(samples);
      }
    } catch (error) {
      console.error('Error loading bid request samples:', error);
      this.bidRequestSamplesSubject.next([]);
    }
  }

  private async loadPublisherInventory(): Promise<void> {
    try {
      const message = "Please provide available publisher inventory from your knowledge base. Include publisher details, content categories, audience reach, ad formats, pricing, and performance metrics.";
      
      const response = await this.bedrockService.invokeAgent('supervisor', message);
      
      if (response && response.response) {
        const publishers = this.parseAgentResponse<PublisherInventory[]>(response.response, 'publishers');
        this.publisherInventorySubject.next(publishers);
      }
    } catch (error) {
      console.error('Error loading publisher inventory:', error);
      this.publisherInventorySubject.next([]);
    }
  }

  private async loadCampaignScenarios(): Promise<void> {
    try {
      const message = "Please provide campaign scenarios from your knowledge base. Include scenario objectives, target audiences, budget structures, duration, and key performance indicators for different campaign types.";
      
      const response = await this.bedrockService.invokeAgent('supervisor', message);
      
      if (response && response.response) {
        const scenarios = this.parseAgentResponse<CampaignScenario[]>(response.response, 'scenarios');
        this.campaignScenariosSubject.next(scenarios);
      }
    } catch (error) {
      console.error('Error loading campaign scenarios:', error);
      this.campaignScenariosSubject.next([]);
    }
  }

  private async loadUrlContentSamples(): Promise<void> {
    try {
      const message = "Please provide URL content samples from your knowledge base. Include page URLs, titles, content categories, brand safety scores, and engagement metrics. Limit to 50 representative samples.";
      
      const response = await this.bedrockService.invokeAgent('supervisor', message);
      
      if (response && response.response) {
        const samples = this.parseAgentResponse<UrlContentSample[]>(response.response, 'samples');
        this.urlContentSamplesSubject.next(samples);
      }
    } catch (error) {
      console.error('Error loading URL content samples:', error);
      this.urlContentSamplesSubject.next([]);
    }
  }

  private async loadBrandSafetyRules(): Promise<void> {
    try {
      const message = "Please provide brand safety rules from your knowledge base. Include rule categories, severity levels, keyword conditions, domain restrictions, and recommended actions for each safety rule.";
      
      const response = await this.bedrockService.invokeAgent('supervisor', message);
      
      if (response && response.response) {
        const rules = this.parseAgentResponse<BrandSafetyRule[]>(response.response, 'rules');
        this.brandSafetyRulesSubject.next(rules);
      }
    } catch (error) {
      console.error('Error loading brand safety rules:', error);
      this.brandSafetyRulesSubject.next([]);
    }
  }

  private async loadIndustryData(): Promise<void> {
    try {
      const message = "Please provide industry data from your knowledge base. Include industry segments, seasonal trends, competitive landscape, major players, and content preferences for different industries.";
      
      const response = await this.bedrockService.invokeAgent('supervisor', message);
      
      if (response && response.response) {
        const industries = this.parseAgentResponse<IndustryData[]>(response.response, 'industries');
        this.industryDataSubject.next(industries);
      }
    } catch (error) {
      console.error('Error loading industry data:', error);
      this.industryDataSubject.next([]);
    }
  }

  private parseAgentResponse<T>(response: string, dataKey: string): T {
    try {
      // Try to find JSON in the response
      const jsonMatch = response.match(/\{[\s\S]*\}/);
      if (jsonMatch) {
        const parsed = JSON.parse(jsonMatch[0]);
        return parsed[dataKey] || [];
      }
      
      // If no JSON found, return empty array
      return [] as unknown as T;
    } catch (error) {
      console.error('Error parsing agent response:', error);
      return [] as unknown as T;
    }
  }

  private generateSessionId(): string {
    return `config-session-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
  }

  // Public methods for refreshing specific data types
  public refreshAudienceSegments(): Promise<void> {
    return this.loadAudienceSegments();
  }

  public refreshBiddingStrategies(): Promise<void> {
    return this.loadBiddingStrategies();
  }

  public refreshBidRequestSamples(): Promise<void> {
    return this.loadBidRequestSamples();
  }

  public refreshPublisherInventory(): Promise<void> {
    return this.loadPublisherInventory();
  }

  public refreshCampaignScenarios(): Promise<void> {
    return this.loadCampaignScenarios();
  }

  public refreshUrlContentSamples(): Promise<void> {
    return this.loadUrlContentSamples();
  }

  public refreshBrandSafetyRules(): Promise<void> {
    return this.loadBrandSafetyRules();
  }

  public refreshIndustryData(): Promise<void> {
    return this.loadIndustryData();
  }

  public refreshAllConfigurations(): void {
    this.loadAllConfigurations();
  }

  // Convenience methods for getting current values
  public getCurrentAudienceSegments(): AudienceSegment[] {
    return this.audienceSegmentsSubject.value;
  }

  public getCurrentBiddingStrategies(): BiddingStrategy[] {
    return this.biddingStrategiesSubject.value;
  }

  public getCurrentBidRequestSamples(): BidRequestSample[] {
    return this.bidRequestSamplesSubject.value;
  }

  public getCurrentPublisherInventory(): PublisherInventory[] {
    return this.publisherInventorySubject.value;
  }

  public getCurrentCampaignScenarios(): CampaignScenario[] {
    return this.campaignScenariosSubject.value;
  }

  public getCurrentUrlContentSamples(): UrlContentSample[] {
    return this.urlContentSamplesSubject.value;
  }

  public getCurrentBrandSafetyRules(): BrandSafetyRule[] {
    return this.brandSafetyRulesSubject.value;
  }

  public getCurrentIndustryData(): IndustryData[] {
    return this.industryDataSubject.value;
  }

  // Method to get specific segment by ID
  public getAudienceSegmentById(segmentId: string): Observable<AudienceSegment | undefined> {
    return this.audienceSegments$.pipe(
      map(segments => segments.find(segment => segment.segment_id === segmentId))
    );
  }

  // Method to get specific strategy by ID
  public getBiddingStrategyById(strategyId: string): Observable<BiddingStrategy | undefined> {
    return this.biddingStrategies$.pipe(
      map(strategies => strategies.find(strategy => strategy.strategy_id === strategyId))
    );
  }
}  */