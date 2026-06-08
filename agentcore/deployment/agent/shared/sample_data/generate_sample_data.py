#!/usr/bin/env python3
"""
Generate TTD REDS Sample Data based on SAMPLE_DATA_SCHEMA.md
"""

import csv
import uuid
import random
from datetime import datetime, timedelta
from pathlib import Path

# Set seed for reproducibility
random.seed(42)

# Output directory
OUTPUT_DIR = Path(__file__).parent

# Date range for data (30 days)
END_DATE = datetime(2024, 12, 15, 23, 59, 59)
START_DATE = END_DATE - timedelta(days=30)

# Reference data - Expanded for richer interconnected examples
ADVERTISERS = [
    # Retail
    {"id": "adv_retail_brand_001", "name": "RetailMax", "currency": "USD", "vertical": "Retail"},
    {"id": "adv_retail_brand_002", "name": "ShopSmart", "currency": "USD", "vertical": "Retail"},
    {"id": "adv_retail_brand_003", "name": "MegaMart", "currency": "USD", "vertical": "Retail"},
    # Automotive
    {"id": "adv_auto_brand_001", "name": "AutoDrive", "currency": "USD", "vertical": "Automotive"},
    {"id": "adv_auto_brand_002", "name": "SpeedMotors", "currency": "USD", "vertical": "Automotive"},
    {"id": "adv_auto_brand_003", "name": "ElectraCar", "currency": "USD", "vertical": "Automotive"},
    # Travel
    {"id": "adv_travel_brand_001", "name": "TravelEase", "currency": "USD", "vertical": "Travel"},
    {"id": "adv_travel_brand_002", "name": "VacationHub", "currency": "USD", "vertical": "Travel"},
    {"id": "adv_travel_brand_003", "name": "FlightDeals", "currency": "USD", "vertical": "Travel"},
    # Finance
    {"id": "adv_finance_brand_001", "name": "SecureBank", "currency": "USD", "vertical": "Finance"},
    {"id": "adv_finance_brand_002", "name": "InvestPro", "currency": "USD", "vertical": "Finance"},
    {"id": "adv_finance_brand_003", "name": "CreditMax", "currency": "USD", "vertical": "Finance"},
    # Technology
    {"id": "adv_tech_brand_001", "name": "TechGiant", "currency": "USD", "vertical": "Technology"},
    {"id": "adv_tech_brand_002", "name": "CloudSoft", "currency": "USD", "vertical": "Technology"},
    {"id": "adv_tech_brand_003", "name": "AppMaster", "currency": "USD", "vertical": "Technology"},
    # Healthcare
    {"id": "adv_health_brand_001", "name": "HealthPlus", "currency": "USD", "vertical": "Healthcare"},
    {"id": "adv_health_brand_002", "name": "WellnessFirst", "currency": "USD", "vertical": "Healthcare"},
    # Entertainment
    {"id": "adv_ent_brand_001", "name": "StreamMax", "currency": "USD", "vertical": "Entertainment"},
    {"id": "adv_ent_brand_002", "name": "GameZone", "currency": "USD", "vertical": "Entertainment"},
    # CPG
    {"id": "adv_cpg_brand_001", "name": "FreshFoods", "currency": "USD", "vertical": "CPG"},
    {"id": "adv_cpg_brand_002", "name": "CleanHome", "currency": "USD", "vertical": "CPG"},
]

CAMPAIGNS = [
    # RetailMax campaigns
    {"CampaignId": "cmp_holiday_promo_2024", "AdvertiserId": "adv_retail_brand_001", "CampaignName": "Holiday Promo 2024", "Budget": 500000.00, "Goal": "Conversions", "StartDate": "2024-11-15", "EndDate": "2024-12-31", "Status": "Active", "PacingMode": "Even", "AutoAllocatorEnabled": True},
    {"CampaignId": "cmp_brand_awareness_q4", "AdvertiserId": "adv_retail_brand_001", "CampaignName": "Brand Awareness Q4", "Budget": 250000.00, "Goal": "Reach", "StartDate": "2024-10-01", "EndDate": "2024-12-31", "Status": "Active", "PacingMode": "Even", "AutoAllocatorEnabled": True},
    {"CampaignId": "cmp_retail_clearance", "AdvertiserId": "adv_retail_brand_001", "CampaignName": "Year End Clearance", "Budget": 150000.00, "Goal": "Conversions", "StartDate": "2024-12-01", "EndDate": "2024-12-31", "Status": "Active", "PacingMode": "ASAP", "AutoAllocatorEnabled": True},
    # ShopSmart campaigns
    {"CampaignId": "cmp_shopsmart_blackfriday", "AdvertiserId": "adv_retail_brand_002", "CampaignName": "Black Friday Blitz", "Budget": 400000.00, "Goal": "Conversions", "StartDate": "2024-11-20", "EndDate": "2024-12-02", "Status": "Completed", "PacingMode": "ASAP", "AutoAllocatorEnabled": True},
    {"CampaignId": "cmp_shopsmart_loyalty", "AdvertiserId": "adv_retail_brand_002", "CampaignName": "Loyalty Program Push", "Budget": 200000.00, "Goal": "CPA", "StartDate": "2024-11-01", "EndDate": "2025-01-31", "Status": "Active", "PacingMode": "Even", "AutoAllocatorEnabled": False},
    # MegaMart campaigns
    {"CampaignId": "cmp_megamart_grocery", "AdvertiserId": "adv_retail_brand_003", "CampaignName": "Grocery Savings", "Budget": 180000.00, "Goal": "Reach", "StartDate": "2024-11-01", "EndDate": "2024-12-31", "Status": "Active", "PacingMode": "Even", "AutoAllocatorEnabled": True},
    # AutoDrive campaigns
    {"CampaignId": "cmp_new_model_launch", "AdvertiserId": "adv_auto_brand_001", "CampaignName": "New Model Launch", "Budget": 750000.00, "Goal": "CPA", "StartDate": "2024-11-01", "EndDate": "2025-01-31", "Status": "Active", "PacingMode": "ASAP", "AutoAllocatorEnabled": True},
    {"CampaignId": "cmp_auto_yearend_sale", "AdvertiserId": "adv_auto_brand_001", "CampaignName": "Year End Sale Event", "Budget": 450000.00, "Goal": "Conversions", "StartDate": "2024-12-01", "EndDate": "2024-12-31", "Status": "Active", "PacingMode": "Even", "AutoAllocatorEnabled": True},
    # SpeedMotors campaigns
    {"CampaignId": "cmp_speed_performance", "AdvertiserId": "adv_auto_brand_002", "CampaignName": "Performance Series Launch", "Budget": 600000.00, "Goal": "Reach", "StartDate": "2024-11-15", "EndDate": "2025-02-28", "Status": "Active", "PacingMode": "Even", "AutoAllocatorEnabled": True},
    # ElectraCar campaigns
    {"CampaignId": "cmp_electra_ev_awareness", "AdvertiserId": "adv_auto_brand_003", "CampaignName": "EV Awareness Campaign", "Budget": 800000.00, "Goal": "Reach", "StartDate": "2024-10-01", "EndDate": "2025-03-31", "Status": "Active", "PacingMode": "Even", "AutoAllocatorEnabled": True},
    {"CampaignId": "cmp_electra_test_drive", "AdvertiserId": "adv_auto_brand_003", "CampaignName": "Test Drive Signups", "Budget": 350000.00, "Goal": "CPA", "StartDate": "2024-11-01", "EndDate": "2025-01-31", "Status": "Active", "PacingMode": "Even", "AutoAllocatorEnabled": True},
    # TravelEase campaigns
    {"CampaignId": "cmp_winter_getaway", "AdvertiserId": "adv_travel_brand_001", "CampaignName": "Winter Getaway Deals", "Budget": 300000.00, "Goal": "Conversions", "StartDate": "2024-11-20", "EndDate": "2025-02-28", "Status": "Active", "PacingMode": "Even", "AutoAllocatorEnabled": False},
    {"CampaignId": "cmp_travel_newyear", "AdvertiserId": "adv_travel_brand_001", "CampaignName": "New Year Escapes", "Budget": 250000.00, "Goal": "Conversions", "StartDate": "2024-12-15", "EndDate": "2025-01-15", "Status": "Active", "PacingMode": "ASAP", "AutoAllocatorEnabled": True},
    # VacationHub campaigns
    {"CampaignId": "cmp_vacation_cruise", "AdvertiserId": "adv_travel_brand_002", "CampaignName": "Cruise Deals 2025", "Budget": 400000.00, "Goal": "CPA", "StartDate": "2024-11-01", "EndDate": "2025-03-31", "Status": "Active", "PacingMode": "Even", "AutoAllocatorEnabled": True},
    # FlightDeals campaigns
    {"CampaignId": "cmp_flight_holiday", "AdvertiserId": "adv_travel_brand_003", "CampaignName": "Holiday Flight Deals", "Budget": 350000.00, "Goal": "Conversions", "StartDate": "2024-11-15", "EndDate": "2024-12-31", "Status": "Active", "PacingMode": "Even", "AutoAllocatorEnabled": True},
    # SecureBank campaigns
    {"CampaignId": "cmp_bank_savings", "AdvertiserId": "adv_finance_brand_001", "CampaignName": "High Yield Savings", "Budget": 500000.00, "Goal": "CPA", "StartDate": "2024-10-01", "EndDate": "2025-03-31", "Status": "Active", "PacingMode": "Even", "AutoAllocatorEnabled": True},
    {"CampaignId": "cmp_bank_mortgage", "AdvertiserId": "adv_finance_brand_001", "CampaignName": "Mortgage Refinance", "Budget": 600000.00, "Goal": "CPA", "StartDate": "2024-11-01", "EndDate": "2025-02-28", "Status": "Active", "PacingMode": "Even", "AutoAllocatorEnabled": True},
    # InvestPro campaigns
    {"CampaignId": "cmp_invest_retirement", "AdvertiserId": "adv_finance_brand_002", "CampaignName": "Retirement Planning", "Budget": 450000.00, "Goal": "CPA", "StartDate": "2024-11-01", "EndDate": "2025-01-31", "Status": "Active", "PacingMode": "Even", "AutoAllocatorEnabled": True},
    # CreditMax campaigns
    {"CampaignId": "cmp_credit_rewards", "AdvertiserId": "adv_finance_brand_003", "CampaignName": "Rewards Card Launch", "Budget": 550000.00, "Goal": "CPA", "StartDate": "2024-11-15", "EndDate": "2025-02-28", "Status": "Active", "PacingMode": "Even", "AutoAllocatorEnabled": True},
    # TechGiant campaigns
    {"CampaignId": "cmp_tech_product_launch", "AdvertiserId": "adv_tech_brand_001", "CampaignName": "New Product Launch", "Budget": 900000.00, "Goal": "Reach", "StartDate": "2024-11-01", "EndDate": "2025-01-31", "Status": "Active", "PacingMode": "Even", "AutoAllocatorEnabled": True},
    {"CampaignId": "cmp_tech_holiday_bundle", "AdvertiserId": "adv_tech_brand_001", "CampaignName": "Holiday Bundle Deals", "Budget": 400000.00, "Goal": "Conversions", "StartDate": "2024-11-20", "EndDate": "2024-12-31", "Status": "Active", "PacingMode": "ASAP", "AutoAllocatorEnabled": True},
    # CloudSoft campaigns
    {"CampaignId": "cmp_cloud_enterprise", "AdvertiserId": "adv_tech_brand_002", "CampaignName": "Enterprise Cloud Solutions", "Budget": 700000.00, "Goal": "CPA", "StartDate": "2024-10-01", "EndDate": "2025-03-31", "Status": "Active", "PacingMode": "Even", "AutoAllocatorEnabled": True},
    # AppMaster campaigns
    {"CampaignId": "cmp_app_install", "AdvertiserId": "adv_tech_brand_003", "CampaignName": "App Install Campaign", "Budget": 300000.00, "Goal": "CPA", "StartDate": "2024-11-01", "EndDate": "2025-01-31", "Status": "Active", "PacingMode": "Even", "AutoAllocatorEnabled": True},
    # HealthPlus campaigns
    {"CampaignId": "cmp_health_enrollment", "AdvertiserId": "adv_health_brand_001", "CampaignName": "Open Enrollment 2025", "Budget": 650000.00, "Goal": "CPA", "StartDate": "2024-11-01", "EndDate": "2024-12-15", "Status": "Active", "PacingMode": "ASAP", "AutoAllocatorEnabled": True},
    # WellnessFirst campaigns
    {"CampaignId": "cmp_wellness_newyear", "AdvertiserId": "adv_health_brand_002", "CampaignName": "New Year Wellness", "Budget": 250000.00, "Goal": "Conversions", "StartDate": "2024-12-15", "EndDate": "2025-02-28", "Status": "Active", "PacingMode": "Even", "AutoAllocatorEnabled": True},
    # StreamMax campaigns
    {"CampaignId": "cmp_stream_holiday", "AdvertiserId": "adv_ent_brand_001", "CampaignName": "Holiday Streaming Promo", "Budget": 500000.00, "Goal": "CPA", "StartDate": "2024-11-15", "EndDate": "2025-01-15", "Status": "Active", "PacingMode": "Even", "AutoAllocatorEnabled": True},
    # GameZone campaigns
    {"CampaignId": "cmp_game_launch", "AdvertiserId": "adv_ent_brand_002", "CampaignName": "New Game Launch", "Budget": 450000.00, "Goal": "Conversions", "StartDate": "2024-11-20", "EndDate": "2025-01-31", "Status": "Active", "PacingMode": "ASAP", "AutoAllocatorEnabled": True},
    # FreshFoods campaigns
    {"CampaignId": "cmp_fresh_holiday", "AdvertiserId": "adv_cpg_brand_001", "CampaignName": "Holiday Recipes", "Budget": 200000.00, "Goal": "Reach", "StartDate": "2024-11-15", "EndDate": "2024-12-31", "Status": "Active", "PacingMode": "Even", "AutoAllocatorEnabled": True},
    # CleanHome campaigns
    {"CampaignId": "cmp_clean_newyear", "AdvertiserId": "adv_cpg_brand_002", "CampaignName": "New Year Clean Start", "Budget": 180000.00, "Goal": "Reach", "StartDate": "2024-12-15", "EndDate": "2025-02-28", "Status": "Active", "PacingMode": "Even", "AutoAllocatorEnabled": False},
]

# Generate ad groups dynamically based on campaigns
def generate_adgroups():
    """Generate ad groups for each campaign with multiple channels."""
    adgroups = []
    channel_configs = {
        "Display": {"BaseBidCPM": 5.00, "DailyBudget": 5000.00},
        "Video": {"BaseBidCPM": 12.00, "DailyBudget": 8000.00},
        "CTV": {"BaseBidCPM": 28.00, "DailyBudget": 12000.00},
        "Native": {"BaseBidCPM": 6.00, "DailyBudget": 4000.00},
    }
    
    adgroup_types = [
        ("prospecting", "Prospecting", ["Display", "Video", "Native"]),
        ("retargeting", "Retargeting", ["Display", "Native"]),
        ("awareness", "Awareness", ["Video", "CTV"]),
        ("performance", "Performance", ["Display", "Video"]),
    ]
    
    for campaign in CAMPAIGNS:
        cmp_id = campaign["CampaignId"]
        adv_id = campaign["AdvertiserId"]
        cmp_short = cmp_id.replace("cmp_", "")[:12]
        
        # Each campaign gets 2-4 ad groups
        num_adgroups = random.randint(2, 4)
        selected_types = random.sample(adgroup_types, num_adgroups)
        
        for ag_type, ag_name_prefix, channels in selected_types:
            channel = random.choice(channels)
            config = channel_configs[channel]
            
            ag_id = f"ag_{cmp_short}_{ag_type}_{channel.lower()}"
            
            adgroups.append({
                "AdGroupId": ag_id,
                "CampaignId": cmp_id,
                "AdvertiserId": adv_id,
                "AdGroupName": f"{ag_name_prefix} {channel}",
                "BaseBidCPM": round(config["BaseBidCPM"] * random.uniform(0.8, 1.3), 2),
                "Channel": channel,
                "DailyBudget": round(config["DailyBudget"] * random.uniform(0.7, 1.5), 2),
                "Status": "Active" if random.random() > 0.1 else "Paused",
                "KoaEnabled": random.random() > 0.3,
                "PredictiveClearingEnabled": random.random() > 0.4,
            })
    
    return adgroups

ADGROUPS = generate_adgroups()

# Generate creatives dynamically based on ad groups
def generate_creatives():
    """Generate multiple creatives for each ad group."""
    creatives = []
    
    size_configs = {
        "Display": ["300x250", "728x90", "300x600", "160x600", "320x50", "970x250"],
        "Video": ["1280x720", "1920x1080", "640x360"],
        "CTV": ["1920x1080"],
        "Native": ["1200x627", "1200x628", "600x600"],
    }
    
    creative_names = {
        "Display": ["Banner", "Rich Media", "Dynamic", "Static", "Animated"],
        "Video": ["Pre-roll", "Mid-roll", "Bumper", "Skippable", "Non-skip"],
        "CTV": ["Connected TV", "OTT", "Smart TV", "Streaming"],
        "Native": ["In-feed", "Content", "Recommendation", "Sponsored"],
    }
    
    advertiser_domains = {
        "adv_retail_brand_001": "retailmax.com",
        "adv_retail_brand_002": "shopsmart.com",
        "adv_retail_brand_003": "megamart.com",
        "adv_auto_brand_001": "autodrive.com",
        "adv_auto_brand_002": "speedmotors.com",
        "adv_auto_brand_003": "electracar.com",
        "adv_travel_brand_001": "travelease.com",
        "adv_travel_brand_002": "vacationhub.com",
        "adv_travel_brand_003": "flightdeals.com",
        "adv_finance_brand_001": "securebank.com",
        "adv_finance_brand_002": "investpro.com",
        "adv_finance_brand_003": "creditmax.com",
        "adv_tech_brand_001": "techgiant.com",
        "adv_tech_brand_002": "cloudsoft.com",
        "adv_tech_brand_003": "appmaster.com",
        "adv_health_brand_001": "healthplus.com",
        "adv_health_brand_002": "wellnessfirst.com",
        "adv_ent_brand_001": "streammax.com",
        "adv_ent_brand_002": "gamezone.com",
        "adv_cpg_brand_001": "freshfoods.com",
        "adv_cpg_brand_002": "cleanhome.com",
    }
    
    for adgroup in ADGROUPS:
        ag_id = adgroup["AdGroupId"]
        cmp_id = adgroup["CampaignId"]
        adv_id = adgroup["AdvertiserId"]
        channel = adgroup["Channel"]
        
        # Each ad group gets 2-4 creatives
        num_creatives = random.randint(2, 4)
        sizes = random.sample(size_configs[channel], min(num_creatives, len(size_configs[channel])))
        names = random.sample(creative_names[channel], min(num_creatives, len(creative_names[channel])))
        
        domain = advertiser_domains.get(adv_id, "example.com")
        
        for i, (size, name) in enumerate(zip(sizes, names)):
            cr_id = f"cr_{ag_id}_{i+1}"
            
            creatives.append({
                "CreativeId": cr_id,
                "AdGroupId": ag_id,
                "CampaignId": cmp_id,
                "AdvertiserId": adv_id,
                "CreativeName": f"{name} {size}",
                "Format": channel,
                "Size": size,
                "Status": "Active" if random.random() > 0.05 else "Paused",
                "LandingPageUrl": f"https://{domain}/promo",
            })
    
    return creatives

CREATIVES = generate_creatives()

# Lookup helpers
ADGROUP_MAP = {ag["AdGroupId"]: ag for ag in ADGROUPS}
CREATIVE_MAP = {cr["CreativeId"]: cr for cr in CREATIVES}

DEVICE_TYPES = ["Desktop", "Mobile", "Tablet", "CTV", "SmartTV"]
OS_LIST = ["Windows", "macOS", "iOS", "Android", "tvOS", "Roku", "FireTV"]
BROWSERS = ["Chrome", "Safari", "Firefox", "Edge", "Samsung Internet", "App"]
COUNTRIES = ["US", "CA", "UK", "DE", "FR", "AU"]
US_REGIONS = ["CA", "NY", "TX", "FL", "IL", "PA", "OH", "GA", "NC", "MI"]
US_METROS = ["Los Angeles", "New York", "Chicago", "Houston", "Phoenix", "Philadelphia", "San Antonio", "San Diego", "Dallas", "San Jose"]
SUPPLY_VENDORS = ["Google AdX", "Magnite", "PubMatic", "OpenX", "Index Exchange", "Xandr", "Amazon TAM", "SpotX", "FreeWheel"]
SITES = ["cnn.com", "espn.com", "weather.com", "nytimes.com", "foxnews.com", "nbcnews.com", "usatoday.com", "washingtonpost.com", "bbc.com", "theguardian.com"]
APPS = ["CNN App", "ESPN App", "Weather Channel", "NYT App", "Fox News App", "NBC News", "USA Today", "WaPo App", "BBC News", "Guardian"]
FOLDS = ["Above", "Below", "Unknown"]
AUCTION_TYPES = ["FirstPrice", "SecondPrice"]
VIDEO_DURATIONS = [15, 30, 60]
VIDEO_EVENT_TYPES = ["Start", "FirstQuartile", "Midpoint", "ThirdQuartile", "Complete", "Mute", "Unmute", "Pause", "Resume", "Skip"]
VIEWABILITY_VENDORS = ["IAS", "DoubleVerify", "Moat", "ComScore"]
CONVERSION_TYPES = ["Purchase", "Add to Cart", "Lead Form", "Newsletter Signup", "Page View", "App Install"]


def random_timestamp(start=START_DATE, end=END_DATE):
    """Generate random timestamp between start and end."""
    delta = end - start
    random_seconds = random.randint(0, int(delta.total_seconds()))
    return start + timedelta(seconds=random_seconds)

def generate_tdid():
    """Generate Trade Desk ID."""
    return f"TDID_{uuid.uuid4().hex[:16].upper()}"

def generate_uid2():
    """Generate UID2."""
    return f"UID2_{uuid.uuid4().hex[:20]}"

def generate_partner_id():
    """Generate Partner ID."""
    return f"partner_{random.randint(1000, 9999)}"

def write_csv(filename, data, fieldnames):
    """Write data to CSV file."""
    filepath = OUTPUT_DIR / filename
    with open(filepath, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(data)
    print(f"Generated {filepath} with {len(data)} records")

def generate_reference_data():
    """Generate reference data CSV files."""
    # ref_advertisers.csv
    write_csv("ref_advertisers.csv", ADVERTISERS, ["id", "name", "currency", "vertical"])
    
    # ref_campaigns.csv
    write_csv("ref_campaigns.csv", CAMPAIGNS, 
              ["CampaignId", "AdvertiserId", "CampaignName", "Budget", "Goal", "StartDate", "EndDate", "Status", "PacingMode", "AutoAllocatorEnabled"])
    
    # ref_adgroups.csv
    write_csv("ref_adgroups.csv", ADGROUPS,
              ["AdGroupId", "CampaignId", "AdvertiserId", "AdGroupName", "BaseBidCPM", "Channel", "DailyBudget", "Status", "KoaEnabled", "PredictiveClearingEnabled"])
    
    # ref_creatives.csv
    write_csv("ref_creatives.csv", CREATIVES,
              ["CreativeId", "AdGroupId", "CampaignId", "AdvertiserId", "CreativeName", "Format", "Size", "Status", "LandingPageUrl"])

def generate_impressions(count=10000):
    """Generate impression data."""
    impressions = []
    
    for _ in range(count):
        creative = random.choice(CREATIVES)
        adgroup = ADGROUP_MAP[creative["AdGroupId"]]
        
        log_ts = random_timestamp()
        processed_ts = log_ts + timedelta(seconds=random.randint(1, 60))
        
        country = random.choice(COUNTRIES)
        region = random.choice(US_REGIONS) if country == "US" else ""
        metro = random.choice(US_METROS) if country == "US" else ""
        
        device_type = random.choice(DEVICE_TYPES)
        if device_type in ["CTV", "SmartTV"]:
            os_name = random.choice(["tvOS", "Roku", "FireTV", "Android"])
            browser = "App"
        elif device_type == "Mobile":
            os_name = random.choice(["iOS", "Android"])
            browser = random.choice(["Safari", "Chrome", "Samsung Internet", "App"])
        else:
            os_name = random.choice(["Windows", "macOS"])
            browser = random.choice(["Chrome", "Safari", "Firefox", "Edge"])
        
        base_bid = adgroup["BaseBidCPM"]
        bid_price = round(base_bid * random.uniform(0.8, 1.5), 2)
        winning_bid = round(bid_price * random.uniform(0.6, 0.95), 2)
        media_cost = round(winning_bid * 0.85, 2)
        data_cost = round(random.uniform(0.10, 0.50), 2)
        fee_cost = round(random.uniform(0.05, 0.20), 2)
        total_cost = round(media_cost + data_cost + fee_cost, 2)
        
        is_app = random.random() < 0.3
        
        impression = {
            "ImpressionId": str(uuid.uuid4()),
            "LogTimestamp": log_ts.isoformat(),
            "ProcessedTimestamp": processed_ts.isoformat(),
            "TDID": generate_tdid(),
            "UID2": generate_uid2(),
            "PartnerId": generate_partner_id(),
            "AdvertiserId": creative["AdvertiserId"],
            "CampaignId": creative["CampaignId"],
            "AdGroupId": creative["AdGroupId"],
            "CreativeId": creative["CreativeId"],
            "BidPriceCPM": bid_price,
            "WinningBidPriceCPM": winning_bid,
            "MediaCostCPM": media_cost,
            "DataCostCPM": data_cost,
            "FeeCostCPM": fee_cost,
            "TotalCostCPM": total_cost,
            "DeviceType": device_type,
            "OS": os_name,
            "OSVersion": f"{random.randint(10, 17)}.{random.randint(0, 9)}",
            "Browser": browser,
            "BrowserVersion": f"{random.randint(90, 120)}.0",
            "Country": country,
            "Region": region,
            "Metro": metro,
            "SupplyVendor": random.choice(SUPPLY_VENDORS),
            "Site": "" if is_app else random.choice(SITES),
            "AppName": random.choice(APPS) if is_app else "",
            "AdFormat": creative["Format"],
            "AdSize": creative["Size"],
            "Fold": random.choice(FOLDS),
            "DealId": f"deal_{random.randint(10000, 99999)}" if random.random() < 0.2 else "",
            "AuctionType": random.choice(AUCTION_TYPES),
            "Viewable": random.random() < 0.7,
            "Frequency": random.randint(1, 15),
            "RecencyMinutes": random.randint(0, 10080),
            "HourOfDay": log_ts.hour,
            "DayOfWeek": log_ts.weekday(),
        }
        impressions.append(impression)
    
    fieldnames = ["ImpressionId", "LogTimestamp", "ProcessedTimestamp", "TDID", "UID2", "PartnerId",
                  "AdvertiserId", "CampaignId", "AdGroupId", "CreativeId", "BidPriceCPM", "WinningBidPriceCPM",
                  "MediaCostCPM", "DataCostCPM", "FeeCostCPM", "TotalCostCPM", "DeviceType", "OS", "OSVersion",
                  "Browser", "BrowserVersion", "Country", "Region", "Metro", "SupplyVendor", "Site", "AppName",
                  "AdFormat", "AdSize", "Fold", "DealId", "AuctionType", "Viewable", "Frequency", "RecencyMinutes",
                  "HourOfDay", "DayOfWeek"]
    
    write_csv("reds_impressions.csv", impressions, fieldnames)
    return impressions

def generate_clicks(impressions, ctr=0.02):
    """Generate click data (~2% CTR)."""
    clicks = []
    click_count = int(len(impressions) * ctr)
    clicked_impressions = random.sample(impressions, click_count)
    
    for imp in clicked_impressions:
        click_ts = datetime.fromisoformat(imp["LogTimestamp"]) + timedelta(seconds=random.randint(1, 30))
        creative = CREATIVE_MAP[imp["CreativeId"]]
        
        click = {
            "ClickId": str(uuid.uuid4()),
            "ImpressionId": imp["ImpressionId"],
            "ClickTimestamp": click_ts.isoformat(),
            "TDID": imp["TDID"],
            "UID2": imp["UID2"],
            "PartnerId": imp["PartnerId"],
            "AdvertiserId": imp["AdvertiserId"],
            "CampaignId": imp["CampaignId"],
            "AdGroupId": imp["AdGroupId"],
            "CreativeId": imp["CreativeId"],
            "DeviceType": imp["DeviceType"],
            "OS": imp["OS"],
            "Browser": imp["Browser"],
            "Country": imp["Country"],
            "Region": imp["Region"],
            "LandingPageUrl": creative["LandingPageUrl"],
            "ClickType": random.choice(["Standard", "VideoCompanion", "Expandable"]),
            "IsValid": random.random() < 0.95,
        }
        clicks.append(click)
    
    fieldnames = ["ClickId", "ImpressionId", "ClickTimestamp", "TDID", "UID2", "PartnerId",
                  "AdvertiserId", "CampaignId", "AdGroupId", "CreativeId", "DeviceType", "OS",
                  "Browser", "Country", "Region", "LandingPageUrl", "ClickType", "IsValid"]
    
    write_csv("reds_clicks.csv", clicks, fieldnames)
    return clicks


def generate_conversions(impressions, clicks, cvr=0.15):
    """Generate conversion data (~15% CVR from clicks + view-through)."""
    conversions = []
    
    # Click-through conversions
    click_conversions = int(len(clicks) * cvr)
    converted_clicks = random.sample(clicks, click_conversions)
    
    for click in converted_clicks:
        conv_ts = datetime.fromisoformat(click["ClickTimestamp"]) + timedelta(minutes=random.randint(5, 1440))
        
        conversion = {
            "ConversionId": str(uuid.uuid4()),
            "ConversionTimestamp": conv_ts.isoformat(),
            "TDID": click["TDID"],
            "UID2": click["UID2"],
            "MatchedImpressionId": click["ImpressionId"],
            "MatchedClickId": click["ClickId"],
            "PartnerId": click["PartnerId"],
            "AdvertiserId": click["AdvertiserId"],
            "CampaignId": click["CampaignId"],
            "AdGroupId": click["AdGroupId"],
            "CreativeId": click["CreativeId"],
            "ConversionType": random.choice(CONVERSION_TYPES),
            "ConversionValue": round(random.uniform(25, 500), 2),
            "ConversionCurrency": "USD",
            "AttributionType": "ClickThrough",
            "AttributionWindowDays": random.choice([1, 7, 14, 30]),
            "OrderId": f"ORD_{random.randint(100000, 999999)}",
            "ProductId": f"PROD_{random.randint(1000, 9999)}",
            "Quantity": random.randint(1, 5),
            "TrackingTagId": f"tag_{random.randint(1000, 9999)}",
            "IsDeduplicated": True,
        }
        conversions.append(conversion)
    
    # View-through conversions (from impressions without clicks)
    clicked_imp_ids = {c["ImpressionId"] for c in clicks}
    non_clicked_imps = [i for i in impressions if i["ImpressionId"] not in clicked_imp_ids]
    vtc_count = int(len(non_clicked_imps) * 0.015)  # 1.5% VTC rate
    vtc_impressions = random.sample(non_clicked_imps, min(vtc_count, len(non_clicked_imps)))
    
    for imp in vtc_impressions:
        conv_ts = datetime.fromisoformat(imp["LogTimestamp"]) + timedelta(hours=random.randint(1, 168))
        
        conversion = {
            "ConversionId": str(uuid.uuid4()),
            "ConversionTimestamp": conv_ts.isoformat(),
            "TDID": imp["TDID"],
            "UID2": imp["UID2"],
            "MatchedImpressionId": imp["ImpressionId"],
            "MatchedClickId": "",
            "PartnerId": imp["PartnerId"],
            "AdvertiserId": imp["AdvertiserId"],
            "CampaignId": imp["CampaignId"],
            "AdGroupId": imp["AdGroupId"],
            "CreativeId": imp["CreativeId"],
            "ConversionType": random.choice(CONVERSION_TYPES),
            "ConversionValue": round(random.uniform(25, 500), 2),
            "ConversionCurrency": "USD",
            "AttributionType": "ViewThrough",
            "AttributionWindowDays": random.choice([1, 7]),
            "OrderId": f"ORD_{random.randint(100000, 999999)}",
            "ProductId": f"PROD_{random.randint(1000, 9999)}",
            "Quantity": random.randint(1, 3),
            "TrackingTagId": f"tag_{random.randint(1000, 9999)}",
            "IsDeduplicated": True,
        }
        conversions.append(conversion)
    
    fieldnames = ["ConversionId", "ConversionTimestamp", "TDID", "UID2", "MatchedImpressionId",
                  "MatchedClickId", "PartnerId", "AdvertiserId", "CampaignId", "AdGroupId", "CreativeId",
                  "ConversionType", "ConversionValue", "ConversionCurrency", "AttributionType",
                  "AttributionWindowDays", "OrderId", "ProductId", "Quantity", "TrackingTagId", "IsDeduplicated"]
    
    write_csv("reds_conversions.csv", conversions, fieldnames)
    return conversions

def generate_video_events(impressions):
    """Generate video events (~20,000 records for video/CTV impressions)."""
    video_events = []
    video_impressions = [i for i in impressions if i["AdFormat"] in ["Video", "CTV"]]
    
    for imp in video_impressions:
        video_duration = random.choice(VIDEO_DURATIONS)
        base_ts = datetime.fromisoformat(imp["LogTimestamp"])
        
        # Generate progression events
        events_to_generate = ["Start"]
        completion_prob = random.random()
        
        if completion_prob > 0.1:
            events_to_generate.append("FirstQuartile")
        if completion_prob > 0.25:
            events_to_generate.append("Midpoint")
        if completion_prob > 0.4:
            events_to_generate.append("ThirdQuartile")
        if completion_prob > 0.6:
            events_to_generate.append("Complete")
        
        # Add interaction events
        if random.random() < 0.1:
            events_to_generate.append("Mute")
        if random.random() < 0.05:
            events_to_generate.append("Pause")
            events_to_generate.append("Resume")
        if completion_prob <= 0.6 and random.random() < 0.3:
            events_to_generate.append("Skip")
        
        sound_on = random.random() < 0.7
        autoplay = random.random() < 0.8
        
        for event_type in events_to_generate:
            event_offset = {
                "Start": 0, "FirstQuartile": video_duration * 0.25,
                "Midpoint": video_duration * 0.5, "ThirdQuartile": video_duration * 0.75,
                "Complete": video_duration, "Mute": random.randint(1, video_duration),
                "Unmute": random.randint(1, video_duration), "Pause": random.randint(1, video_duration),
                "Resume": random.randint(1, video_duration), "Skip": random.randint(5, video_duration),
            }.get(event_type, 0)
            
            event_ts = base_ts + timedelta(seconds=int(event_offset))
            
            video_event = {
                "VideoEventId": str(uuid.uuid4()),
                "ImpressionId": imp["ImpressionId"],
                "VideoEventType": event_type,
                "EventTimestamp": event_ts.isoformat(),
                "TDID": imp["TDID"],
                "UID2": imp["UID2"],
                "PartnerId": imp["PartnerId"],
                "AdvertiserId": imp["AdvertiserId"],
                "CampaignId": imp["CampaignId"],
                "AdGroupId": imp["AdGroupId"],
                "CreativeId": imp["CreativeId"],
                "VideoPlayDuration": int(event_offset),
                "VideoDuration": video_duration,
                "VideoPlayerSize": random.choice(["640x360", "1280x720", "1920x1080"]),
                "VideoPlayerType": random.choice(["InStream", "OutStream"]),
                "SoundOn": sound_on,
                "Autoplay": autoplay,
            }
            video_events.append(video_event)
    
    fieldnames = ["VideoEventId", "ImpressionId", "VideoEventType", "EventTimestamp", "TDID", "UID2",
                  "PartnerId", "AdvertiserId", "CampaignId", "AdGroupId", "CreativeId", "VideoPlayDuration",
                  "VideoDuration", "VideoPlayerSize", "VideoPlayerType", "SoundOn", "Autoplay"]
    
    write_csv("reds_video_events.csv", video_events, fieldnames)
    return video_events


def generate_viewability(impressions, coverage=0.8):
    """Generate viewability data (~80% measurement coverage)."""
    viewability_records = []
    measured_impressions = random.sample(impressions, int(len(impressions) * coverage))
    
    for imp in measured_impressions:
        measure_ts = datetime.fromisoformat(imp["LogTimestamp"]) + timedelta(seconds=random.randint(1, 10))
        is_video = imp["AdFormat"] in ["Video", "CTV"]
        in_view = random.random() < 0.65
        
        viewability = {
            "ViewabilityId": str(uuid.uuid4()),
            "ImpressionId": imp["ImpressionId"],
            "MeasurementTimestamp": measure_ts.isoformat(),
            "ViewabilityVendor": random.choice(VIEWABILITY_VENDORS),
            "TDID": imp["TDID"],
            "PartnerId": imp["PartnerId"],
            "AdvertiserId": imp["AdvertiserId"],
            "CampaignId": imp["CampaignId"],
            "AdGroupId": imp["AdGroupId"],
            "CreativeId": imp["CreativeId"],
            "InView": in_view,
            "ViewableTime": random.randint(1, 30) if in_view else 0,
            "ViewablePercent": random.randint(50, 100) if in_view else random.randint(0, 49),
            "MeasurementType": "Video" if is_video else "Display",
            "ViewabilityStandard": random.choice(["MRC", "GroupM"]),
            "PlayerSize": imp["AdSize"],
            "InViewAtStart": in_view and random.random() < 0.9 if is_video else False,
            "InViewAtComplete": in_view and random.random() < 0.7 if is_video else False,
            "AudibleAtStart": random.random() < 0.6 if is_video else False,
            "AudibleAtComplete": random.random() < 0.5 if is_video else False,
            "BotTraffic": random.random() < 0.02,
            "GIVT": random.random() < 0.03,
            "SIVT": random.random() < 0.01,
        }
        viewability_records.append(viewability)
    
    fieldnames = ["ViewabilityId", "ImpressionId", "MeasurementTimestamp", "ViewabilityVendor", "TDID",
                  "PartnerId", "AdvertiserId", "CampaignId", "AdGroupId", "CreativeId", "InView",
                  "ViewableTime", "ViewablePercent", "MeasurementType", "ViewabilityStandard", "PlayerSize",
                  "InViewAtStart", "InViewAtComplete", "AudibleAtStart", "AudibleAtComplete",
                  "BotTraffic", "GIVT", "SIVT"]
    
    write_csv("reds_viewability.csv", viewability_records, fieldnames)
    return viewability_records

def generate_gdpr_consent(impressions, eu_traffic_pct=0.25):
    """Generate GDPR consent data (EU traffic only ~25%)."""
    consent_records = []
    eu_countries = ["UK", "DE", "FR"]
    eu_impressions = [i for i in impressions if i["Country"] in eu_countries]
    
    # If not enough EU impressions, sample from all and assign EU countries
    if len(eu_impressions) < int(len(impressions) * eu_traffic_pct):
        additional_needed = int(len(impressions) * eu_traffic_pct) - len(eu_impressions)
        non_eu = [i for i in impressions if i["Country"] not in eu_countries]
        additional = random.sample(non_eu, min(additional_needed, len(non_eu)))
        for imp in additional:
            imp["Country"] = random.choice(eu_countries)
        eu_impressions.extend(additional)
    
    for imp in eu_impressions:
        consent_ts = datetime.fromisoformat(imp["LogTimestamp"]) - timedelta(seconds=random.randint(1, 5))
        has_consent = random.random() < 0.85
        
        purposes_consented = [1, 2, 3, 4, 7, 9, 10] if has_consent else random.sample([1, 2, 3, 4, 7, 9, 10], random.randint(0, 3))
        legitimate_interests = [2, 7, 8, 9, 10] if has_consent else []
        vendor_consents = list(range(1, random.randint(50, 200))) if has_consent else []
        
        consent = {
            "ConsentId": str(uuid.uuid4()),
            "ImpressionId": imp["ImpressionId"],
            "ConsentTimestamp": consent_ts.isoformat(),
            "TDID": imp["TDID"],
            "Country": imp["Country"],
            "TCFVersion": "2.2",
            "CMPId": random.randint(1, 500),
            "CMPVersion": random.randint(1, 10),
            "ConsentScreen": random.randint(1, 3),
            "ConsentLanguage": {"UK": "en", "DE": "de", "FR": "fr"}.get(imp["Country"], "en"),
            "VendorListVersion": random.randint(100, 150),
            "HasConsent": has_consent,
            "PurposesConsented": str(purposes_consented),
            "LegitimateInterests": str(legitimate_interests),
            "VendorConsents": str(vendor_consents[:20]) + "..." if len(vendor_consents) > 20 else str(vendor_consents),
            "PublisherRestrictions": "[]",
            "ConsentString": f"CP{''.join(random.choices('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789', k=80))}",
        }
        consent_records.append(consent)
    
    fieldnames = ["ConsentId", "ImpressionId", "ConsentTimestamp", "TDID", "Country", "TCFVersion",
                  "CMPId", "CMPVersion", "ConsentScreen", "ConsentLanguage", "VendorListVersion",
                  "HasConsent", "PurposesConsented", "LegitimateInterests", "VendorConsents",
                  "PublisherRestrictions", "ConsentString"]
    
    write_csv("reds_gdpr_consent.csv", consent_records, fieldnames)
    return consent_records

def main():
    """Generate all sample data files."""
    print("Generating TTD REDS Sample Data...")
    print("=" * 50)
    
    # Generate reference data
    print("\n--- Reference Data ---")
    generate_reference_data()
    
    # Generate event data
    print("\n--- Event Data ---")
    impressions = generate_impressions(10000)
    clicks = generate_clicks(impressions)
    generate_conversions(impressions, clicks)
    generate_video_events(impressions)
    generate_viewability(impressions)
    generate_gdpr_consent(impressions)
    
    print("\n" + "=" * 50)
    print("Sample data generation complete!")

if __name__ == "__main__":
    main()
