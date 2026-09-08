#!/usr/bin/env python3
# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Rivian R2 Launch Campaign — CTV + Linear TV Cross-Platform Example

Demonstrates the seller agent evaluating a buyer proposal that includes
linear TV alongside CTV and digital. This extends the original Rivian R2
buyer demo (which was CTV + display + mobile) with a linear TV broadcast
and cable component.

Scenario: Agency ABC (Publicis Groupe) submits a cross-platform proposal
for the Rivian R2 launch campaign that includes:
- CTV Premium Streaming: $3,500,000 (PG + PMP)
- Linear TV Broadcast + Cable: $2,800,000 (upfront commitment)
- Digital Display/Video: $800,000 (PMP)
- Mobile App: $400,000

The seller agent represents NBCU, who can fulfill:
- CTV via Peacock streaming inventory
- Linear TV via NBC broadcast primetime + NBCU cable (Bravo, USA, CNBC)
- Addressable linear via Comcast MVPD STB targeting
- Digital display/video via nbcuniversal.com properties

This example runs the seller-side evaluation only (no live buyer agent needed).

Usage:
    python examples/rivian_r2_linear_tv.py
"""

import json
from decimal import Decimal

from ad_seller.models.linear_tv import (
    Daypart,
    LinearDeal,
    LinearTVProduct,
    MakegoodTerms,
    SupplyPoolEntry,
)
from ad_seller.models.flow_state import ProductDefinition
from ad_seller.models.core import DealType, PricingModel
from ad_seller.models.buyer_identity import BuyerContext, BuyerIdentity
from ad_seller.constants.dma_codes import DMA_CODES
from ad_seller.tools.linear import (
    LinearPricingTool,
    ScatterPricingTool,
    UpfrontDealCalculator,
    LinearAvailsTool,
    DMAAvailsTool,
    MakegoodPoolTool,
    LinearAudienceForecastTool,
    LinearReachFrequencyTool,
    AddressableTargetingTool,
    LinearOrderTool,
)


# =============================================================================
# Step 1: Define NBCU's Linear TV Product Catalog
# =============================================================================

def create_nbcu_linear_products() -> list[LinearTVProduct]:
    """Create NBCU's linear TV product catalog for the R2 campaign."""

    products = [
        # NBC Broadcast Primetime
        LinearTVProduct(
            product_id="ltv-nbc-prime",
            name="NBC Primetime :30",
            description="NBC broadcast primetime 30-second national spot. "
                        "Includes top-rated scripted and unscripted programming.",
            medium="broadcast",
            network_name="NBC",
            network_domain="nbc.com",
            network_group="NBCUniversal",
            seller_type="DIRECT",
            coverage="national",
            primary_demo="A25-54",
            secondary_demos=["W25-54", "M25-54", "A18-49"],
            rate_card_cpm=Decimal("55.00"),
            floor_cpm=Decimal("40.00"),
            rate_card_cpp=Decimal("45000"),
            floor_cpp=Decimal("32000"),
            upfront_rate_cpp=Decimal("42000"),
            scatter_rate_multiplier=1.20,
            measurement_currency="nielsen_c3",
            market_types=["upfront", "scatter"],
            programmatic_enabled=True,
            programmatic_deal_types=["pg"],
            dayparts=[
                Daypart(
                    name="primetime",
                    start_time="20:00:00",
                    end_time="23:00:00",
                    days_of_week=["M", "T", "W", "Th", "F"],
                    available_units=150,
                    sold_units=128,  # 85% sellthrough
                    base_rate_cpp=Decimal("45000"),
                    base_rate_cpm=Decimal("55.00"),
                ),
            ],
        ),

        # NBCU Cable Bundle (Bravo, USA, CNBC)
        LinearTVProduct(
            product_id="ltv-nbcu-cable",
            name="NBCU Cable Network :30 (Bravo/USA/CNBC)",
            description="NBCU cable portfolio 30-second spot. "
                        "Rotates across Bravo, USA Network, and CNBC.",
            medium="cable",
            network_name="NBCU Cable Portfolio",
            network_group="NBCUniversal",
            seller_type="DIRECT",
            coverage="national",
            primary_demo="A25-54",
            secondary_demos=["W25-54"],
            rate_card_cpm=Decimal("22.00"),
            floor_cpm=Decimal("15.00"),
            rate_card_cpp=Decimal("18000"),
            floor_cpp=Decimal("12000"),
            scatter_rate_multiplier=1.15,
            measurement_currency="nielsen_c3",
            market_types=["upfront", "scatter"],
            programmatic_enabled=True,
            programmatic_deal_types=["pg", "pmp"],
            dayparts=[
                Daypart(
                    name="primetime",
                    start_time="20:00:00",
                    end_time="23:00:00",
                    available_units=300,
                    sold_units=180,  # 60% sellthrough
                    base_rate_cpp=Decimal("18000"),
                    base_rate_cpm=Decimal("22.00"),
                ),
                Daypart(
                    name="daytime",
                    start_time="10:00:00",
                    end_time="16:00:00",
                    available_units=400,
                    sold_units=160,  # 40% sellthrough
                    base_rate_cpp=Decimal("8000"),
                    base_rate_cpm=Decimal("10.00"),
                ),
            ],
        ),

        # Comcast Addressable Linear
        LinearTVProduct(
            product_id="ltv-comcast-addr",
            name="Comcast Addressable Linear — National",
            description="Household-level addressable linear TV via Comcast Xfinity "
                        "set-top box targeting. 60M+ addressable households.",
            medium="mvpd_avail",
            network_name="Comcast Xfinity",
            network_group="Comcast",
            seller_type="DIRECT",
            coverage="national",
            primary_demo="HH",
            rate_card_cpm=Decimal("55.00"),
            floor_cpm=Decimal("40.00"),
            measurement_currency="impression",
            addressable_enabled=True,
            addressable_type="mvpd_stb",
            addressable_hh_count=60_000_000,
            market_types=["scatter"],
            programmatic_enabled=True,
            programmatic_deal_types=["pg", "pmp"],
        ),

        # Telemundo (Hispanic audience)
        LinearTVProduct(
            product_id="ltv-telemundo",
            name="Telemundo Primetime :30",
            description="Telemundo Spanish-language primetime 30-second spot. "
                        "Reaches US Hispanic audience for Rivian R2 multicultural strategy.",
            medium="broadcast",
            network_name="Telemundo",
            network_domain="telemundo.com",
            network_group="NBCUniversal",
            seller_type="DIRECT",
            coverage="national",
            primary_demo="A18-49",
            secondary_demos=["A25-54"],
            rate_card_cpm=Decimal("18.00"),
            floor_cpm=Decimal("12.00"),
            measurement_currency="nielsen_c3",
            market_types=["upfront", "scatter"],
            programmatic_deal_types=["pg", "pmp"],
        ),
    ]

    return products


# =============================================================================
# Step 2: Simulate Buyer Proposal (Agency ABC / Publicis for Rivian)
# =============================================================================

def create_rivian_r2_proposal() -> dict:
    """Create the Rivian R2 cross-platform proposal with linear TV component.

    This simulates what Agency ABC (Publicis Groupe) would send to NBCU's
    seller agent for the Rivian R2 launch.
    """
    return {
        "proposal_id": "prop-rivian-r2-2026",
        "campaign_name": "Rivian R2 Launch Campaign — Q2 2026",
        "advertiser": "Rivian Automotive",
        "agency": "Agency ABC (Publicis Groupe)",
        "holding_company": "publicis",
        "total_budget": 7_500_000,
        "flight_dates": {
            "start": "2026-04-01",
            "end": "2026-06-30",
        },
        "target_audience": {
            "primary_demo": "A25-54",
            "hhi": "$125,000+",
            "interests": [
                "Electric vehicles", "Luxury SUV", "Outdoor adventure",
                "Technology early adopters", "Sustainability",
            ],
            "first_party_segments": [
                "1P-RIVIAN-CONFIGURATOR",  # Visited R2 configurator
                "1P-RIVIAN-R1-OWNERS",     # Existing R1T/R1S owners
            ],
        },
        "channels": {
            "ctv_streaming": {
                "budget": 3_500_000,
                "deal_types": ["pg", "pmp"],
                "notes": "Peacock premium + FAST channels, HH frequency cap 3x/day",
            },
            "linear_tv": {
                "budget": 2_800_000,
                "deal_type": "upfront",
                "networks_requested": ["NBC", "NBCU Cable (Bravo/USA)", "Telemundo"],
                "dayparts_requested": ["primetime", "late_news"],
                "demo_guarantee": "A25-54",
                "measurement": "nielsen_c3",
                "addressable_overlay": True,
                "addressable_budget": 500_000,
                "addressable_segments": [
                    "Auto intenders - luxury SUV",
                    "EV considerers",
                    "HHI $150K+",
                ],
                "makegood_requirements": "Standard upfront — same sales element, within flight dates",
                "notes": "Cross-platform reach extension with CTV. "
                         "Want NBC primetime for launch week tentpoles. "
                         "Bravo/USA for sustained cable presence. "
                         "Telemundo for Hispanic audience strategy.",
            },
            "digital_display_video": {
                "budget": 800_000,
                "deal_types": ["pmp"],
                "notes": "nbcuniversal.com pre-roll + display, retargeting R2 configurator visitors",
            },
            "mobile_app": {
                "budget": 400_000,
                "notes": "Rivian app install campaign, rewarded video",
            },
        },
        "kpis": {
            "linear_tv": "500 GRPs A25-54, 70%+ reach, 3+ effective frequency",
            "ctv": "5M HH reach, 3x frequency cap",
            "digital": "100M impressions, 75%+ viewability",
            "overall": "Incremental reach vs linear-only: +15% via CTV/digital",
        },
    }


# =============================================================================
# Step 3: Run Seller-Side Evaluation
# =============================================================================

def evaluate_linear_tv_component(proposal: dict, products: list[LinearTVProduct]):
    """Evaluate the linear TV component using seller tools."""

    linear_spec = proposal["channels"]["linear_tv"]

    print("\n" + "=" * 70)
    print("SELLER EVALUATION — Linear TV Component")
    print("=" * 70)

    # --- Pricing ---
    print("\n--- Pricing Analysis ---\n")

    pricing_tool = LinearPricingTool()
    for product in products[:2]:  # NBC Prime + NBCU Cable
        result = pricing_tool._run(
            network=product.network_name,
            daypart="primetime",
            market_type="upfront",
            buyer_type="holding_company",
            base_rate_cpm=float(product.rate_card_cpm),
            base_rate_cpp=float(product.rate_card_cpp),
            demo=product.primary_demo,
        )
        print(result)
        print()

    # Upfront deal calculation
    print("--- Upfront Deal Economics ---\n")
    upfront_tool = UpfrontDealCalculator()
    result = upfront_tool._run(
        prior_season_rate=48.0,  # Prior season CPM
        volume_commitment=linear_spec["budget"],
        holding_company="publicis",
        networks=["NBC", "Bravo", "USA", "Telemundo"],
        advertiser_tier="premium",  # Rivian is a premium EV brand
    )
    print(result)

    # --- Availability ---
    print("\n\n--- Availability Check ---\n")

    avails_tool = LinearAvailsTool()
    result = avails_tool._run(
        networks=["NBC", "NBCU Cable"],
        dayparts=["primetime", "late_news"],
        demo="A25-54",
        flight_start="2026-04-01",
        flight_end="2026-06-30",
    )
    print(result)

    # --- Addressable Overlay ---
    if linear_spec.get("addressable_overlay"):
        print("\n--- Addressable TV Overlay ---\n")

        addressable_tool = AddressableTargetingTool()
        result = addressable_tool._run(
            audience_segments=linear_spec["addressable_segments"],
            geo_targeting=["national"],
            data_provider="go_addressable",
            base_cpm=float(products[0].rate_card_cpm),
        )
        print(result)

    # --- Audience Forecast ---
    print("\n\n--- Audience Forecast ---\n")

    forecast_tool = LinearAudienceForecastTool()
    result = forecast_tool._run(
        networks=["NBC", "NBCU Cable"],
        dayparts=["primetime", "late_news"],
        demo="A25-54",
        flight_start="2026-04-01",
        flight_end="2026-06-30",
        spots_per_week=15,
    )
    print(result)

    # --- Reach/Frequency ---
    print("\n\n--- Reach/Frequency Estimate ---\n")

    rf_tool = LinearReachFrequencyTool()
    result = rf_tool._run(
        total_grps=500.0,  # Requested in KPIs
        demo="A25-54",
        num_networks=4,  # NBC + Bravo + USA + Telemundo
        num_dayparts=2,  # Primetime + Late News
    )
    print(result)


def generate_deal_recommendation(proposal: dict, products: list[LinearTVProduct]):
    """Generate the seller's deal recommendation."""

    print("\n" + "=" * 70)
    print("SELLER RECOMMENDATION — Linear TV Deal Structure")
    print("=" * 70)

    # Construct the deal
    deal = LinearDeal(
        market_type="upfront",
        buyer_type="holding_company",
        holding_company="publicis",
        buyer_name="Rivian Automotive",
        agency_name="Agency ABC (Publicis Groupe)",
        networks=["NBC", "Bravo", "USA", "CNBC", "Telemundo"],
        dayparts=["primetime", "late_news"],
        guaranteed_grps=500.0,
        guaranteed_impressions=650_000_000,
        negotiated_cpp=Decimal("43500"),
        negotiated_cpm=Decimal("50.50"),
        total_value=Decimal("2800000"),
        cancellation_window_days=90,
        rate_of_change_pct=6.0,
        measurement_currency="nielsen_c3",
        makegood_terms=MakegoodTerms(
            makegood_type="audience_underdelivery",
            sales_element_equivalent="same_sales_element",
            makegood_window="within_flight_dates",
            audience_limit_pct=90.0,
        ),
        start_date="2026-04-01",
        end_date="2026-06-30",
        product_ids=[p.product_id for p in products],
        status="proposed",
    )

    print(f"\nDeal ID: {deal.deal_id}")
    print(f"Market Type: {deal.market_type.upper()}")
    print(f"Buyer: {deal.buyer_name} via {deal.agency_name}")
    print(f"Holding Company: {deal.holding_company.upper()}")
    print(f"Networks: {', '.join(deal.networks)}")
    print(f"Dayparts: {', '.join(deal.dayparts)}")
    print(f"Flight: {deal.start_date} to {deal.end_date}")
    print(f"\nDual Currency Terms:")
    print(f"  Guaranteed GRPs: {deal.guaranteed_grps}")
    print(f"  Guaranteed Impressions: {deal.guaranteed_impressions:,}")
    print(f"  Negotiated CPP: ${deal.negotiated_cpp:,}")
    print(f"  Negotiated CPM: ${deal.negotiated_cpm}")
    print(f"  Total Value: ${deal.total_value:,}")
    print(f"\nRate of Change: +{deal.rate_of_change_pct}% YoY")
    print(f"Cancellation Window: {deal.cancellation_window_days} days")
    print(f"Measurement: {deal.measurement_currency}")
    print(f"\nMakegood Terms (TIP v5.1.0):")
    print(f"  Type: {deal.makegood_terms.makegood_type}")
    print(f"  Equivalent: {deal.makegood_terms.sales_element_equivalent}")
    print(f"  Window: {deal.makegood_terms.makegood_window}")
    print(f"  Audience Limit: {deal.makegood_terms.audience_limit_pct}%")

    # Generate IO
    print("\n\n--- Generating Insertion Order ---\n")

    order_tool = LinearOrderTool()
    result = order_tool._run(
        deal_id=deal.deal_id,
        advertiser_name="Rivian Automotive",
        agency_name="Agency ABC (Publicis Groupe)",
        networks=deal.networks,
        dayparts=deal.dayparts,
        flight_start=deal.start_date,
        flight_end=deal.end_date,
        total_spots=500,
        total_value=float(deal.total_value),
        demo="A25-54",
        spot_length=30,
    )
    print(result)

    return deal


def print_cross_platform_summary(proposal: dict):
    """Print the full cross-platform campaign summary."""

    print("\n" + "=" * 70)
    print("CROSS-PLATFORM CAMPAIGN SUMMARY — Rivian R2 Launch")
    print("=" * 70)

    channels = proposal["channels"]
    total = proposal["total_budget"]

    print(f"\nAdvertiser: {proposal['advertiser']}")
    print(f"Agency: {proposal['agency']}")
    print(f"Flight: {proposal['flight_dates']['start']} to {proposal['flight_dates']['end']}")
    print(f"Total Budget: ${total:,.0f}")
    print()

    rows = [
        ("CTV Premium Streaming", channels["ctv_streaming"]["budget"], "PG + PMP"),
        ("Linear TV (NBC + Cable)", channels["linear_tv"]["budget"] - channels["linear_tv"]["addressable_budget"], "Upfront"),
        ("Addressable Linear", channels["linear_tv"]["addressable_budget"], "PG/PMP"),
        ("Digital Display/Video", channels["digital_display_video"]["budget"], "PMP"),
        ("Mobile App", channels["mobile_app"]["budget"], "Direct"),
    ]

    print(f"  {'Channel':<35} {'Budget':>12} {'Deal Type':<15} {'Share':>6}")
    print(f"  {'─' * 35} {'─' * 12} {'─' * 15} {'─' * 6}")
    for name, budget, deal_type in rows:
        share = budget / total * 100
        print(f"  {name:<35} ${budget:>10,.0f} {deal_type:<15} {share:>5.1f}%")
    print(f"  {'─' * 35} {'─' * 12} {'─' * 15} {'─' * 6}")
    print(f"  {'TOTAL':<35} ${total:>10,.0f}")

    print(f"\nKPIs:")
    for channel, kpi in proposal["kpis"].items():
        print(f"  {channel}: {kpi}")

    print(f"\nSeller Perspective: NBCU can fulfill CTV (Peacock), Linear TV")
    print(f"  (NBC broadcast + Bravo/USA/CNBC cable + Telemundo),")
    print(f"  Addressable (Comcast STB), and Digital (nbcuniversal.com).")
    print(f"  This is a single-publisher cross-platform deal opportunity")
    print(f"  worth ${total:,.0f} across 5 channels.")


# =============================================================================
# Main
# =============================================================================

def main():
    """Run the Rivian R2 + Linear TV seller evaluation example."""

    print("=" * 70)
    print("  RIVIAN R2 LAUNCH CAMPAIGN — Seller Agent Evaluation")
    print("  Linear TV + CTV + Digital Cross-Platform Proposal")
    print("=" * 70)

    # Step 1: Create NBCU product catalog
    print("\n[1] Building NBCU Linear TV Product Catalog...")
    products = create_nbcu_linear_products()
    for p in products:
        print(f"    {p.product_id}: {p.name} ({p.medium}, {p.seller_type})")
        print(f"      Rate Card: ${p.rate_card_cpm} CPM / ${p.rate_card_cpp} CPP")
        if p.addressable_enabled:
            print(f"      Addressable: {p.addressable_type} ({p.addressable_hh_count:,} HH)")

    # Step 2: Create buyer proposal
    print("\n[2] Receiving Buyer Proposal...")
    proposal = create_rivian_r2_proposal()
    print(f"    Campaign: {proposal['campaign_name']}")
    print(f"    Total Budget: ${proposal['total_budget']:,.0f}")
    print(f"    Linear TV Ask: ${proposal['channels']['linear_tv']['budget']:,.0f}")
    print(f"    Networks: {', '.join(proposal['channels']['linear_tv']['networks_requested'])}")

    # Step 3: Evaluate linear TV component
    print("\n[3] Running Seller Evaluation Tools...")
    evaluate_linear_tv_component(proposal, products)

    # Step 4: Generate deal recommendation
    print("\n[4] Generating Deal Recommendation...")
    deal = generate_deal_recommendation(proposal, products)

    # Step 5: Cross-platform summary
    print("\n[5] Cross-Platform Summary...")
    print_cross_platform_summary(proposal)

    print("\n" + "=" * 70)
    print("  Evaluation Complete — Deal ready for agent-to-agent negotiation")
    print("=" * 70)


if __name__ == "__main__":
    main()
