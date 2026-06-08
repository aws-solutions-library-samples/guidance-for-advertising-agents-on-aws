# TTD REDS Sample Data Schema Reference

This document describes the sample data files that simulate TTD REDS (Raw Event Data Streams) feeds. These can be used to test the agent tools before connecting to real TTD data.

## Data Relationships

All data is cross-referenceable using common identifiers:

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Entity Hierarchy                            │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ref_advertisers.csv                                                │
│       │                                                             │
│       └──► ref_campaigns.csv (AdvertiserId)                         │
│                 │                                                   │
│                 └──► ref_adgroups.csv (CampaignId)                  │
│                           │                                         │
│                           └──► ref_creatives.csv (AdGroupId)        │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│                         Event Data Joins                            │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  reds_impressions.csv ◄──┬── ImpressionId ──► reds_clicks.csv       │
│         │                │                          │               │
│         │                └────────────────► reds_conversions.csv    │
│         │                                   (MatchedImpressionId)   │
│         │                                                           │
│         ├── ImpressionId ──► reds_video_events.csv                  │
│         │                                                           │
│         └── ImpressionId ──► reds_viewability.csv                   │
│                                                                     │
│  reds_gdpr_consent.csv ◄── ImpressionId (EU traffic only)           │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Reference Data Files

### ref_advertisers.csv
| Column | Type | Description |
|--------|------|-------------|
| id | string | Advertiser ID (e.g., `adv_retail_brand_001`) |
| name | string | Advertiser name |
| currency | string | Currency code (USD) |
| vertical | string | Industry vertical |

### ref_campaigns.csv
| Column | Type | Description |
|--------|------|-------------|
| CampaignId | string | Campaign ID (e.g., `cmp_holiday_promo_2024`) |
| AdvertiserId | string | Parent advertiser ID |
| CampaignName | string | Campaign display name |
| Budget | float | Total campaign budget |
| Goal | string | Campaign goal (CPA, Reach, Conversions) |
| StartDate | date | Campaign start date |
| EndDate | date | Campaign end date |
| Status | string | Active/Paused/Completed |
| PacingMode | string | Even/ASAP |
| AutoAllocatorEnabled | bool | Whether auto-allocator is enabled |

### ref_adgroups.csv
| Column | Type | Description |
|--------|------|-------------|
| AdGroupId | string | Ad group ID (e.g., `ag_prospecting_display`) |
| CampaignId | string | Parent campaign ID |
| AdvertiserId | string | Advertiser ID |
| AdGroupName | string | Ad group display name |
| BaseBidCPM | float | Base bid in CPM |
| Channel | string | Display/Video/CTV/Native |
| DailyBudget | float | Daily budget cap |
| Status | string | Active/Paused |
| KoaEnabled | bool | Koa AI optimization enabled |
| PredictiveClearingEnabled | bool | Predictive clearing enabled |

### ref_creatives.csv
| Column | Type | Description |
|--------|------|-------------|
| CreativeId | string | Creative ID |
| AdGroupId | string | Parent ad group ID |
| CampaignId | string | Campaign ID |
| AdvertiserId | string | Advertiser ID |
| CreativeName | string | Creative display name |
| Format | string | Display/Video/CTV/Native |
| Size | string | Dimensions (e.g., 300x250) |
| Status | string | Active/Paused |
| LandingPageUrl | string | Click-through URL |

---

## REDS Event Data Files

### reds_impressions.csv (10,000 records)
| Column | Type | Description |
|--------|------|-------------|
| ImpressionId | string (UUID) | Unique impression identifier |
| LogTimestamp | datetime | When impression was served |
| ProcessedTimestamp | datetime | When event was processed |
| TDID | string | Trade Desk ID (user identifier) |
| UID2 | string | Unified ID 2.0 |
| PartnerId | string | Partner ID |
| AdvertiserId | string | Advertiser ID |
| CampaignId | string | Campaign ID |
| AdGroupId | string | Ad group ID |
| CreativeId | string | Creative ID |
| BidPriceCPM | float | Bid price in CPM |
| WinningBidPriceCPM | float | Winning/clearing price in CPM |
| MediaCostCPM | float | Media cost in CPM |
| DataCostCPM | float | Data cost in CPM |
| FeeCostCPM | float | Fee cost in CPM |
| TotalCostCPM | float | Total cost in CPM |
| DeviceType | string | Desktop/Mobile/Tablet/CTV/SmartTV |
| OS | string | Operating system |
| OSVersion | string | OS version |
| Browser | string | Browser name |
| BrowserVersion | string | Browser version |
| Country | string | Country code (US, CA, UK, etc.) |
| Region | string | State/region (US only) |
| Metro | string | Metro area (US only) |
| SupplyVendor | string | Exchange/SSP name |
| Site | string | Publisher domain |
| AppName | string | App name (if mobile app) |
| AdFormat | string | Display/Video/CTV/Native |
| AdSize | string | Creative dimensions |
| Fold | string | Above/Below/Unknown |
| DealId | string | Deal ID (if PMP) |
| AuctionType | string | FirstPrice/SecondPrice |
| Viewable | bool | Viewability status |
| Frequency | int | User frequency count |
| RecencyMinutes | int | Minutes since last impression |
| HourOfDay | int | Hour (0-23) |
| DayOfWeek | int | Day (0-6, 0=Sunday) |

### reds_clicks.csv (~200 records)
| Column | Type | Description |
|--------|------|-------------|
| ClickId | string (UUID) | Unique click identifier |
| ImpressionId | string | Associated impression ID |
| ClickTimestamp | datetime | When click occurred |
| TDID | string | Trade Desk ID |
| UID2 | string | Unified ID 2.0 |
| PartnerId | string | Partner ID |
| AdvertiserId | string | Advertiser ID |
| CampaignId | string | Campaign ID |
| AdGroupId | string | Ad group ID |
| CreativeId | string | Creative ID |
| DeviceType | string | Device type |
| OS | string | Operating system |
| Browser | string | Browser |
| Country | string | Country code |
| Region | string | State/region |
| LandingPageUrl | string | Click destination URL |
| ClickType | string | Standard/VideoCompanion/Expandable |
| IsValid | bool | Click validity flag |

### reds_conversions.csv (~177 records)
| Column | Type | Description |
|--------|------|-------------|
| ConversionId | string (UUID) | Unique conversion identifier |
| ConversionTimestamp | datetime | When conversion occurred |
| TDID | string | Trade Desk ID |
| UID2 | string | Unified ID 2.0 |
| MatchedImpressionId | string | Associated impression ID |
| MatchedClickId | string | Associated click ID (if click-through) |
| PartnerId | string | Partner ID |
| AdvertiserId | string | Advertiser ID |
| CampaignId | string | Campaign ID |
| AdGroupId | string | Ad group ID |
| CreativeId | string | Creative ID |
| ConversionType | string | Purchase/Add to Cart/Lead Form/etc. |
| ConversionValue | float | Revenue value |
| ConversionCurrency | string | Currency code |
| AttributionType | string | ClickThrough/ViewThrough |
| AttributionWindowDays | int | Attribution window (1, 7, etc.) |
| OrderId | string | Order ID (for purchases) |
| ProductId | string | Product ID |
| Quantity | int | Quantity (for purchases) |
| TrackingTagId | string | Conversion pixel ID |
| IsDeduplicated | bool | Deduplication status |

### reds_video_events.csv (~20,000 records)
| Column | Type | Description |
|--------|------|-------------|
| VideoEventId | string (UUID) | Unique event identifier |
| ImpressionId | string | Associated impression ID |
| VideoEventType | string | Start/FirstQuartile/Midpoint/ThirdQuartile/Complete/Mute/Unmute/Pause/Resume/Skip |
| EventTimestamp | datetime | When event occurred |
| TDID | string | Trade Desk ID |
| UID2 | string | Unified ID 2.0 |
| PartnerId | string | Partner ID |
| AdvertiserId | string | Advertiser ID |
| CampaignId | string | Campaign ID |
| AdGroupId | string | Ad group ID |
| CreativeId | string | Creative ID |
| VideoPlayDuration | int | Seconds played |
| VideoDuration | int | Total video length (15/30/60) |
| VideoPlayerSize | string | Player dimensions |
| VideoPlayerType | string | InStream/OutStream |
| SoundOn | bool | Audio enabled |
| Autoplay | bool | Autoplay enabled |

### reds_viewability.csv (~8,000 records)
| Column | Type | Description |
|--------|------|-------------|
| ViewabilityId | string (UUID) | Unique record identifier |
| ImpressionId | string | Associated impression ID |
| MeasurementTimestamp | datetime | When measured |
| ViewabilityVendor | string | IAS/DoubleVerify/Moat/ComScore |
| TDID | string | Trade Desk ID |
| PartnerId | string | Partner ID |
| AdvertiserId | string | Advertiser ID |
| CampaignId | string | Campaign ID |
| AdGroupId | string | Ad group ID |
| CreativeId | string | Creative ID |
| InView | bool | Met viewability standard |
| ViewableTime | int | Seconds in view |
| ViewablePercent | int | Percentage of ad in view |
| MeasurementType | string | Display/Video |
| ViewabilityStandard | string | MRC/GroupM |
| PlayerSize | string | Dimensions |
| InViewAtStart | bool | Viewable at video start |
| InViewAtComplete | bool | Viewable at video complete |
| AudibleAtStart | bool | Audio on at start |
| AudibleAtComplete | bool | Audio on at complete |
| BotTraffic | bool | Suspected bot |
| GIVT | bool | General invalid traffic |
| SIVT | bool | Sophisticated invalid traffic |

### reds_gdpr_consent.csv (~2,500 records)
| Column | Type | Description |
|--------|------|-------------|
| ConsentId | string (UUID) | Unique record identifier |
| ImpressionId | string | Associated impression ID |
| ConsentTimestamp | datetime | When consent collected |
| TDID | string | Trade Desk ID |
| Country | string | EU country code (UK/DE/FR) |
| TCFVersion | string | TCF version (2.2) |
| CMPId | int | Consent management platform ID |
| CMPVersion | int | CMP version |
| ConsentScreen | int | Consent screen number |
| ConsentLanguage | string | Language code |
| VendorListVersion | int | Global vendor list version |
| HasConsent | bool | Overall consent status |
| PurposesConsented | string (JSON) | List of consented purpose IDs |
| LegitimateInterests | string (JSON) | Legitimate interest purposes |
| VendorConsents | string (JSON) | Consented vendor IDs |
| PublisherRestrictions | string (JSON) | Publisher restrictions |
| ConsentString | string | TCF consent string |

---

## Sample Entity IDs

Use these IDs when testing tools:

**Advertisers:**
- `adv_retail_brand_001` - RetailMax (Retail)
- `adv_auto_brand_002` - AutoDrive (Automotive)
- `adv_travel_brand_003` - TravelEase (Travel)

**Campaigns:**
- `cmp_holiday_promo_2024` - Holiday Promo 2024 (RetailMax)
- `cmp_brand_awareness_q4` - Brand Awareness Q4 (RetailMax)
- `cmp_new_model_launch` - New Model Launch (AutoDrive)
- `cmp_winter_getaway` - Winter Getaway Deals (TravelEase)

**Ad Groups:**
- `ag_prospecting_display` - Prospecting Display (Holiday Promo)
- `ag_retargeting_display` - Retargeting Display (Holiday Promo)
- `ag_ctv_premium` - CTV Premium Inventory (Holiday Promo)
- `ag_awareness_video` - Awareness Video (Brand Awareness)
- `ag_auto_ctv` - Auto CTV Campaign (New Model Launch)
- `ag_auto_display` - Auto Display Retargeting (New Model Launch)
- `ag_travel_native` - Travel Native Ads (Winter Getaway)
- `ag_travel_video` - Travel Video Pre-roll (Winter Getaway)

---

## Data Volume Summary

| Feed | Records | Notes |
|------|---------|-------|
| Impressions | 10,000 | 30-day period |
| Clicks | ~200 | ~2% CTR |
| Conversions | ~177 | ~15% CVR from clicks + VTC |
| Video Events | ~20,000 | Multiple events per video impression |
| Viewability | ~8,000 | ~80% measurement coverage |
| GDPR Consent | ~2,500 | EU traffic only (~25% of total) |
