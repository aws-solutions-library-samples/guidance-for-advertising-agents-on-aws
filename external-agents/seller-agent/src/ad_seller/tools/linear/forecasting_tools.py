# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Linear TV Forecasting Tools.

Three tools for linear TV audience and reach forecasting:
- LinearAudienceForecastTool: Forecast GRPs/impressions
- LinearReachFrequencyTool: Calculate reach/frequency curves
- AddressableTargetingTool: Configure household targeting

Stubs return realistic mock data.
TODO: Integrate with Nielsen forecast APIs
TODO: Integrate with FreeWheel Publisher forecasting
TODO: Integrate with Go Addressable / INVIDI for addressable HH data
"""

import random
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

# =============================================================================
# LinearAudienceForecastTool
# =============================================================================


class AudienceForecastInput(BaseModel):
    """Input for linear TV audience forecast."""

    networks: list[str] = Field(description="Networks to forecast")
    dayparts: list[str] = Field(description="Dayparts to forecast")
    demo: str = Field(default="A25-54", description="Target demographic")
    flight_start: str = Field(description="Flight start date (YYYY-MM-DD)")
    flight_end: str = Field(description="Flight end date (YYYY-MM-DD)")
    spots_per_week: int = Field(default=10, description="Number of spots per week")


# Base GRP ratings by daypart (A25-54)
BASE_GRP_RATINGS: dict[str, float] = {
    "primetime": 2.5,
    "late_news": 1.5,
    "daytime": 0.8,
    "early_morning": 1.0,
    "early_fringe": 1.2,
    "prime_access": 1.8,
    "late_fringe": 0.6,
    "overnight": 0.3,
    "weekend": 1.4,
}


class LinearAudienceForecastTool(BaseTool):
    """Forecast GRPs and impressions for linear TV plans.

    Returns estimates based on daypart ratings × spots × weeks.

    TODO: Integrate with Nielsen audience forecast API
    TODO: Integrate with FreeWheel Publisher forecasting endpoint
    """

    name: str = "linear_audience_forecast"
    description: str = """Forecast GRPs and impressions for a linear TV plan
    based on networks, dayparts, demo target, and flight dates."""
    args_schema: Type[BaseModel] = AudienceForecastInput

    def _run(
        self,
        networks: list[str],
        dayparts: list[str],
        demo: str = "A25-54",
        flight_start: str = "",
        flight_end: str = "",
        spots_per_week: int = 10,
    ) -> str:
        # Estimate weeks from flight dates (simplified)
        weeks = 4  # Default to 4 weeks

        lines = [
            f"Audience Forecast — {demo}",
            f"Flight: {flight_start} to {flight_end} (~{weeks} weeks)",
            f"Spots/Week: {spots_per_week}",
            "",
        ]

        total_grps = 0.0
        total_impressions = 0

        for network in networks:
            lines.append(f"  {network}:")
            for daypart in dayparts:
                base_grp = BASE_GRP_RATINGS.get(daypart, 1.0)
                # Add network variation
                grp = base_grp * random.uniform(0.85, 1.15)
                weekly_grps = grp * spots_per_week
                flight_grps = weekly_grps * weeks
                # 1 GRP = ~1.3M impressions for A25-54 national
                impressions = int(flight_grps * 1_300_000)

                total_grps += flight_grps
                total_impressions += impressions

                lines.append(f"    {daypart}:")
                lines.append(f"      Rating: {grp:.1f}")
                lines.append(f"      Weekly GRPs: {weekly_grps:.1f}")
                lines.append(f"      Flight GRPs: {flight_grps:.1f}")
                lines.append(f"      Est. Impressions: {impressions:,}")
            lines.append("")

        lines.append(f"TOTAL GRPs: {total_grps:.1f}")
        lines.append(f"TOTAL Impressions: {total_impressions:,}")

        return "\n".join(lines).strip()


# =============================================================================
# LinearReachFrequencyTool
# =============================================================================


class ReachFrequencyInput(BaseModel):
    """Input for reach/frequency calculation."""

    total_grps: float = Field(description="Total GRPs for the plan")
    demo: str = Field(default="A25-54", description="Target demographic")
    num_networks: int = Field(default=1, description="Number of networks in plan")
    num_dayparts: int = Field(default=1, description="Number of dayparts")


class LinearReachFrequencyTool(BaseTool):
    """Calculate reach and frequency for a linear TV plan.

    Returns a mock R/F curve based on GRP level.

    TODO: Integrate with Nielsen reach/frequency model
    TODO: Support multi-platform (linear + CTV + digital) R/F
    """

    name: str = "linear_reach_frequency"
    description: str = """Calculate reach and frequency estimates for a linear
    TV plan based on total GRPs and plan diversity."""
    args_schema: Type[BaseModel] = ReachFrequencyInput

    def _run(
        self,
        total_grps: float,
        demo: str = "A25-54",
        num_networks: int = 1,
        num_dayparts: int = 1,
    ) -> str:
        # Simplified R/F model — more networks/dayparts = better reach
        diversity_factor = min(1.3, 1.0 + (num_networks * 0.05) + (num_dayparts * 0.03))

        # Diminishing returns reach curve
        if total_grps <= 50:
            reach = min(35.0, total_grps * 0.7) * diversity_factor
        elif total_grps <= 100:
            reach = (35 + (total_grps - 50) * 0.4) * diversity_factor
        elif total_grps <= 200:
            reach = (55 + (total_grps - 100) * 0.2) * diversity_factor
        else:
            reach = (75 + (total_grps - 200) * 0.05) * diversity_factor

        reach = min(95.0, reach)
        frequency = round(total_grps / reach, 1) if reach > 0 else 0

        return f"""
Reach/Frequency Estimate — {demo}
Total GRPs: {total_grps:.0f}
Plan Diversity: {num_networks} networks × {num_dayparts} dayparts

Reach: {reach:.1f}% of {demo}
Frequency: {frequency:.1f}x average
Effective Reach (3+): {reach * 0.65:.1f}%

R/F Curve:
  1+ exposure: {reach:.1f}%
  2+ exposure: {reach * 0.75:.1f}%
  3+ exposure: {reach * 0.55:.1f}%
  5+ exposure: {reach * 0.35:.1f}%
  10+ exposure: {reach * 0.15:.1f}%
""".strip()


# =============================================================================
# AddressableTargetingTool
# =============================================================================


class AddressableTargetingInput(BaseModel):
    """Input for addressable TV targeting configuration."""

    audience_segments: list[str] = Field(
        description="Audience segments (e.g. ['auto intenders', 'luxury shoppers'])",
    )
    geo_targeting: list[str] = Field(
        default_factory=list,
        description="Geographic targeting (DMA names or 'national')",
    )
    data_provider: str = Field(
        default="go_addressable",
        description="Data provider: go_addressable, invidi, freewheel",
    )
    base_cpm: float = Field(default=40.0, description="Base CPM before addressable uplift")


class AddressableTargetingTool(BaseTool):
    """Configure addressable TV household targeting.

    Estimates addressable HH count, coverage, and CPM uplift.

    TODO: Integrate with Go Addressable API for real HH counts
    TODO: Integrate with INVIDI for STB addressable targeting
    TODO: Integrate with FreeWheel for ACR-based targeting
    """

    name: str = "addressable_targeting"
    description: str = """Configure addressable TV household targeting.
    Returns addressable HH count, coverage percentage, and CPM uplift."""
    args_schema: Type[BaseModel] = AddressableTargetingInput

    def _run(
        self,
        audience_segments: list[str],
        geo_targeting: list[str] | None = None,
        data_provider: str = "go_addressable",
        base_cpm: float = 40.0,
    ) -> str:
        geo_targeting = geo_targeting or ["national"]

        # Go Addressable baseline: 69.5M HH addressable
        total_addressable_hh = 69_500_000

        # Segment targeting reduces universe
        segment_factor = max(0.05, 1.0 - (len(audience_segments) * 0.15))
        targeted_hh = int(total_addressable_hh * segment_factor)

        # Geo targeting further reduces
        if "national" not in geo_targeting:
            geo_factor = max(0.1, len(geo_targeting) * 0.08)
            targeted_hh = int(targeted_hh * geo_factor)

        coverage_pct = round((targeted_hh / 131_000_000) * 100, 1)  # vs total US HH

        # Addressable CPM uplift: 30-60% premium
        uplift = 1.0 + (len(audience_segments) * 0.10)
        uplift = min(1.60, max(1.30, uplift))
        addressable_cpm = round(base_cpm * uplift, 2)

        segments_str = ", ".join(audience_segments)
        geo_str = ", ".join(geo_targeting)

        return f"""
Addressable TV Targeting — {data_provider}
Segments: {segments_str}
Geography: {geo_str}

Addressable Universe: {total_addressable_hh:,} HH (Go Addressable footprint)
Targeted Households: {targeted_hh:,} HH
Coverage: {coverage_pct}% of US households

CPM Uplift: {uplift:.0%} over base
Base CPM: ${base_cpm:.2f}
Addressable CPM: ${addressable_cpm:.2f}

Delivery Method: MVPD set-top box + ACR smart TV
Data Refresh: Weekly segment updates
""".strip()
