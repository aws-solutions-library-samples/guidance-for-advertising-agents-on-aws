# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Linear TV Pricing Tools.

Three tools for linear TV pricing calculations:
- LinearPricingTool: Calculate CPP/CPM for linear inventory
- ScatterPricingTool: Real-time scatter market pricing
- UpfrontDealCalculator: Upfront deal economics with rate-of-change

All output dual currency (CPP + CPM). Uses static rate card data now;
seam for FreeWheel Publisher API when 1A integration lands.
"""

from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

# =============================================================================
# Daypart pricing multipliers (industry reference data)
# =============================================================================

DAYPART_MULTIPLIERS: dict[str, float] = {
    "early_morning": 0.35,
    "daytime": 0.45,
    "early_fringe": 0.65,
    "prime_access": 0.85,
    "primetime": 1.0,
    "late_news": 0.70,
    "late_fringe": 0.50,
    "overnight": 0.25,
    "weekend": 0.75,
}

BUYER_TIER_DISCOUNTS: dict[str, float] = {
    "holding_company": 0.90,  # 10% volume discount
    "independent_agency": 0.95,
    "brand_direct": 1.0,
    "dsp": 0.92,
}


# =============================================================================
# LinearPricingTool
# =============================================================================


class LinearPricingInput(BaseModel):
    """Input for linear TV pricing calculation."""

    network: str = Field(description="Network name (e.g. NBC, Bravo, USA)")
    daypart: str = Field(description="Daypart name (e.g. primetime, daytime)")
    market_type: str = Field(default="scatter", description="upfront or scatter")
    buyer_type: str = Field(default="holding_company", description="Buyer tier")
    base_rate_cpm: float = Field(default=40.0, description="Base CPM rate card price")
    base_rate_cpp: float = Field(default=35000.0, description="Base CPP rate card price")
    demo: str = Field(default="A25-54", description="Target demographic")


class LinearPricingTool(BaseTool):
    """Calculate CPP/CPM pricing for linear TV inventory.

    Applies daypart multipliers, scatter premiums, and buyer tier
    adjustments. Always outputs both CPP and CPM.
    """

    name: str = "linear_pricing"
    description: str = """Calculate linear TV pricing with dual currency (CPP + CPM).
    Factors in daypart, market type (upfront/scatter), and buyer tier."""
    args_schema: Type[BaseModel] = LinearPricingInput

    def _run(
        self,
        network: str,
        daypart: str,
        market_type: str = "scatter",
        buyer_type: str = "holding_company",
        base_rate_cpm: float = 40.0,
        base_rate_cpp: float = 35000.0,
        demo: str = "A25-54",
    ) -> str:
        daypart_mult = DAYPART_MULTIPLIERS.get(daypart, 0.75)
        buyer_disc = BUYER_TIER_DISCOUNTS.get(buyer_type, 1.0)
        scatter_mult = 1.15 if market_type == "scatter" else 1.0

        calc_cpm = round(base_rate_cpm * daypart_mult * scatter_mult * buyer_disc, 2)
        calc_cpp = round(base_rate_cpp * daypart_mult * scatter_mult * buyer_disc, 0)
        floor_cpm = round(calc_cpm * 0.70, 2)
        floor_cpp = round(calc_cpp * 0.70, 0)

        return f"""
Linear TV Pricing — {network} / {daypart} / {demo}
Market Type: {market_type.upper()}
Buyer Tier: {buyer_type}

CPM Pricing:
  Rate Card: ${base_rate_cpm:.2f}
  Calculated: ${calc_cpm:.2f} (daypart {daypart_mult}x, scatter {scatter_mult}x, buyer {buyer_disc}x)
  Floor: ${floor_cpm:.2f}
  Negotiation Range: ${floor_cpm:.2f} – ${calc_cpm:.2f}

CPP Pricing:
  Rate Card: ${base_rate_cpp:,.0f}
  Calculated: ${calc_cpp:,.0f}
  Floor: ${floor_cpp:,.0f}
  Negotiation Range: ${floor_cpp:,.0f} – ${calc_cpp:,.0f}
""".strip()


# =============================================================================
# ScatterPricingTool
# =============================================================================


class ScatterPricingInput(BaseModel):
    """Input for scatter market pricing."""

    network: str = Field(description="Network name")
    daypart: str = Field(description="Daypart name")
    demo: str = Field(default="A25-54", description="Target demographic")
    sellthrough_pct: float = Field(description="Current sell-through percentage (0-100)")
    base_rate_cpm: float = Field(default=40.0, description="Base CPM rate card price")
    flight_start: str = Field(default="", description="Flight start date (YYYY-MM-DD)")
    flight_end: str = Field(default="", description="Flight end date (YYYY-MM-DD)")


class ScatterPricingTool(BaseTool):
    """Real-time scatter pricing based on supply/demand dynamics.

    High sell-through (>80%) increases rate; low (<50%) allows floor approach.
    """

    name: str = "scatter_pricing"
    description: str = """Calculate real-time scatter market rates based on
    sell-through percentage and supply/demand dynamics."""
    args_schema: Type[BaseModel] = ScatterPricingInput

    def _run(
        self,
        network: str,
        daypart: str,
        demo: str = "A25-54",
        sellthrough_pct: float = 70.0,
        base_rate_cpm: float = 40.0,
        flight_start: str = "",
        flight_end: str = "",
    ) -> str:
        daypart_mult = DAYPART_MULTIPLIERS.get(daypart, 0.75)
        base = base_rate_cpm * daypart_mult

        # Scatter premium scales with sell-through
        if sellthrough_pct >= 90:
            scatter_mult = 1.40
            trend = "UP — tight inventory"
        elif sellthrough_pct >= 80:
            scatter_mult = 1.25
            trend = "UP"
        elif sellthrough_pct >= 60:
            scatter_mult = 1.15
            trend = "FLAT"
        elif sellthrough_pct >= 40:
            scatter_mult = 1.0
            trend = "FLAT — soft demand"
        else:
            scatter_mult = 0.85
            trend = "DOWN — distressed inventory"

        current_rate = round(base * scatter_mult, 2)
        recommended_floor = round(base * 0.70, 2)

        flight_info = ""
        if flight_start and flight_end:
            flight_info = f"\nFlight: {flight_start} to {flight_end}"

        return f"""
Scatter Pricing — {network} / {daypart} / {demo}{flight_info}
Sell-Through: {sellthrough_pct:.0f}%
Trend: {trend}

Current Scatter Rate: ${current_rate:.2f} CPM (scatter mult {scatter_mult}x)
Recommended Floor: ${recommended_floor:.2f} CPM
Rate Card Base: ${base_rate_cpm:.2f} × {daypart_mult} daypart = ${base:.2f}
""".strip()


# =============================================================================
# UpfrontDealCalculator
# =============================================================================


class UpfrontDealInput(BaseModel):
    """Input for upfront deal economics calculation."""

    prior_season_rate: float = Field(description="Prior season CPM or CPP rate")
    volume_commitment: float = Field(description="Total dollar commitment")
    holding_company: str = Field(default="independent", description="Holding company name")
    networks: list[str] = Field(default_factory=list, description="Networks in the deal")
    advertiser_tier: str = Field(
        default="standard",
        description="Advertiser tier: premium, standard, emerging",
    )


class UpfrontDealCalculator(BaseTool):
    """Calculate upfront deal economics with rate-of-change.

    Standard rate-of-change calculation + volume tier discounts +
    makegood terms template.
    """

    name: str = "upfront_deal_calculator"
    description: str = """Calculate upfront deal economics including rate-of-change,
    volume discounts, and makegood terms template."""
    args_schema: Type[BaseModel] = UpfrontDealInput

    def _run(
        self,
        prior_season_rate: float,
        volume_commitment: float,
        holding_company: str = "independent",
        networks: list[str] | None = None,
        advertiser_tier: str = "standard",
    ) -> str:
        networks = networks or ["NBC"]

        # Rate-of-change by holding company tier
        roc_table = {
            "wpp": 5.0,
            "omnicom": 5.5,
            "publicis": 6.0,
            "ipg": 5.0,
            "dentsu": 6.5,
            "havas": 7.0,
            "independent": 8.0,
        }
        base_roc = roc_table.get(holding_company, 8.0)

        # Volume discount tiers
        if volume_commitment >= 100_000_000:
            volume_disc = 0.06
            tier_name = "Platinum ($100M+)"
        elif volume_commitment >= 50_000_000:
            volume_disc = 0.04
            tier_name = "Gold ($50M+)"
        elif volume_commitment >= 20_000_000:
            volume_disc = 0.02
            tier_name = "Silver ($20M+)"
        else:
            volume_disc = 0.0
            tier_name = "Standard"

        # Advertiser tier adjustment
        tier_adj = {"premium": -1.0, "standard": 0.0, "emerging": 1.5}
        adj = tier_adj.get(advertiser_tier, 0.0)

        effective_roc = base_roc - (volume_disc * 100) + adj
        proposed_rate = round(prior_season_rate * (1 + effective_roc / 100), 2)

        network_list = ", ".join(networks)
        cancel_window = 90 if holding_company in ("wpp", "omnicom", "publicis") else 60

        return f"""
Upfront Deal Calculator — {network_list}
Holding Company: {holding_company.upper()}
Advertiser Tier: {advertiser_tier}
Volume: ${volume_commitment:,.0f} ({tier_name})

Rate-of-Change:
  Base ROC: +{base_roc:.1f}%
  Volume Discount: -{volume_disc * 100:.1f}%
  Tier Adjustment: {"+" if adj >= 0 else ""}{adj:.1f}%
  Effective ROC: +{effective_roc:.1f}%

Prior Season Rate: ${prior_season_rate:.2f}
Proposed Rate: ${proposed_rate:.2f} (+{effective_roc:.1f}% YoY)

Cancellation Window: {cancel_window} days

Makegood Terms Template:
  Type: Audience underdelivery
  Equivalent: Same sales element preferred, alternate acceptable
  Window: Within flight dates
  Audience Limit: 90% of original primary demo
""".strip()
