# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Linear TV Availability Tools.

Three tools for checking linear TV inventory availability:
- LinearAvailsTool: National avails by network/daypart
- DMAAvailsTool: Local market avails by DMA
- MakegoodPoolTool: Search for qualifying makegood units

Stubs return realistic mock data. TODO markers for FreeWheel/WideOrbit integration.
"""

import random
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

# =============================================================================
# LinearAvailsTool — National avails
# =============================================================================


class LinearAvailsInput(BaseModel):
    """Input for national linear TV avails check."""

    networks: list[str] = Field(description="Networks to check (e.g. ['NBC', 'Bravo'])")
    dayparts: list[str] = Field(description="Dayparts to check (e.g. ['primetime', 'daytime'])")
    demo: str = Field(default="A25-54", description="Target demographic")
    flight_start: str = Field(description="Flight start date (YYYY-MM-DD)")
    flight_end: str = Field(description="Flight end date (YYYY-MM-DD)")


# Realistic sell-through rates by daypart
SELLTHROUGH_BY_DAYPART: dict[str, tuple[float, float]] = {
    "primetime": (80.0, 95.0),
    "late_news": (65.0, 80.0),
    "daytime": (35.0, 55.0),
    "early_morning": (40.0, 60.0),
    "early_fringe": (50.0, 70.0),
    "prime_access": (70.0, 85.0),
    "late_fringe": (30.0, 50.0),
    "overnight": (15.0, 30.0),
    "weekend": (55.0, 75.0),
}


class LinearAvailsTool(BaseTool):
    """Check national linear TV avails by network and daypart.

    Returns realistic mock avails with sell-through rates that vary by daypart
    (primetime 85%+ sell-through, daytime 40-60%).

    TODO: Integrate with FreeWheel Publisher API (GET /inventory/avails)
    TODO: Integrate with WideOrbit WO Traffic API for local broadcast
    """

    name: str = "linear_avails"
    description: str = """Check national linear TV inventory availability by
    network and daypart. Returns avails, estimated impressions, and sell-through."""
    args_schema: Type[BaseModel] = LinearAvailsInput

    def _run(
        self,
        networks: list[str],
        dayparts: list[str],
        demo: str = "A25-54",
        flight_start: str = "",
        flight_end: str = "",
    ) -> str:
        lines = [
            f"National Linear TV Avails — {demo}",
            f"Flight: {flight_start} to {flight_end}",
            "",
        ]

        for network in networks:
            lines.append(f"  {network}:")
            for daypart in dayparts:
                st_range = SELLTHROUGH_BY_DAYPART.get(daypart, (50.0, 70.0))
                sellthrough = round(random.uniform(st_range[0], st_range[1]), 1)
                total_units = random.randint(80, 200)
                sold = int(total_units * sellthrough / 100)
                avail = total_units - sold
                est_impressions = avail * random.randint(800_000, 2_500_000)

                lines.append(f"    {daypart}:")
                lines.append(f"      Total Units: {total_units}")
                lines.append(f"      Sold: {sold} ({sellthrough:.0f}% sell-through)")
                lines.append(f"      Available: {avail} units")
                lines.append(f"      Est. Impressions: {est_impressions:,}")
            lines.append("")

        return "\n".join(lines).strip()


# =============================================================================
# DMAAvailsTool — Local market avails
# =============================================================================


class DMAAvailsInput(BaseModel):
    """Input for local DMA avails check."""

    dma_codes: list[int] = Field(description="DMA codes to check (e.g. [501, 803])")
    station_groups: list[str] = Field(
        default_factory=list,
        description="Station groups (e.g. ['NBC O&O', 'Telemundo'])",
    )
    dayparts: list[str] = Field(description="Dayparts to check")
    flight_start: str = Field(description="Flight start date (YYYY-MM-DD)")
    flight_end: str = Field(description="Flight end date (YYYY-MM-DD)")


class DMAAvailsTool(BaseTool):
    """Check local market avails by DMA code.

    Returns mock local avails with DMA-appropriate scaling.

    TODO: Integrate with WideOrbit WO Traffic API for local broadcast avails
    """

    name: str = "dma_avails"
    description: str = """Check local linear TV inventory availability by DMA code.
    Returns local avails with market-appropriate scaling."""
    args_schema: Type[BaseModel] = DMAAvailsInput

    def _run(
        self,
        dma_codes: list[int],
        station_groups: list[str] | None = None,
        dayparts: list[str] | None = None,
        flight_start: str = "",
        flight_end: str = "",
    ) -> str:
        from ...constants.dma_codes import DMA_CODES

        station_groups = station_groups or ["All Stations"]
        dayparts = dayparts or ["primetime"]

        lines = [
            "Local DMA Avails",
            f"Flight: {flight_start} to {flight_end}",
            f"Station Groups: {', '.join(station_groups)}",
            "",
        ]

        for code in dma_codes:
            dma_info = DMA_CODES.get(code)
            if not dma_info:
                lines.append(f"  DMA {code}: Unknown market")
                continue

            name, rank = dma_info
            # Scale avails by market rank (larger markets = more inventory)
            scale = max(0.1, 1.0 - (rank / 250))

            lines.append(f"  {name} (DMA {code}, Rank #{rank}):")
            for daypart in dayparts:
                # 2 min/hour local avails = ~8-12 units per daypart per week
                base_units = int(10 * scale)
                avail = max(1, base_units + random.randint(-2, 3))
                est_hh = int(avail * 50_000 * scale)

                lines.append(f"    {daypart}: {avail} units available, ~{est_hh:,} HH")
            lines.append("")

        return "\n".join(lines).strip()


# =============================================================================
# MakegoodPoolTool — Search for qualifying makegood units
# =============================================================================


class MakegoodPoolInput(BaseModel):
    """Input for makegood pool search."""

    original_network: str = Field(description="Original network that underdelivered")
    original_daypart: str = Field(description="Original daypart")
    under_delivery_pct: float = Field(description="Percentage of audience underdelivery")
    acceptable_dayparts: list[str] = Field(
        default_factory=lambda: ["primetime", "prime_access", "late_news"],
        description="Acceptable dayparts for makegood",
    )
    acceptable_networks: list[str] = Field(
        default_factory=list,
        description="Acceptable networks (empty = same network only)",
    )
    demo: str = Field(default="A25-54", description="Target demographic")


class MakegoodPoolTool(BaseTool):
    """Search product catalog for qualifying makegood units.

    Evaluates candidate units for audience equivalence. Functional now
    (uses mock product data), no external API required.
    """

    name: str = "makegood_pool"
    description: str = """Search for qualifying makegood units to compensate
    for audience underdelivery. Returns candidates with equivalence scores."""
    args_schema: Type[BaseModel] = MakegoodPoolInput

    def _run(
        self,
        original_network: str,
        original_daypart: str,
        under_delivery_pct: float = 10.0,
        acceptable_dayparts: list[str] | None = None,
        acceptable_networks: list[str] | None = None,
        demo: str = "A25-54",
    ) -> str:
        acceptable_dayparts = acceptable_dayparts or ["primetime", "prime_access", "late_news"]
        acceptable_networks = acceptable_networks or [original_network]

        lines = [
            f"Makegood Pool Search — {original_network} / {original_daypart}",
            f"Under-delivery: {under_delivery_pct:.1f}% of {demo} audience",
            f"Acceptable: {', '.join(acceptable_dayparts)} on {', '.join(acceptable_networks)}",
            "",
            "Candidate Makegood Units:",
        ]

        # Generate mock candidates
        candidates = []
        for net in acceptable_networks:
            for dp in acceptable_dayparts:
                # Higher score for same network/daypart
                base_score = 0.7
                if net == original_network:
                    base_score += 0.15
                if dp == original_daypart:
                    base_score += 0.10
                score = min(1.0, base_score + random.uniform(-0.05, 0.10))
                avail = random.randint(1, 5)
                candidates.append((net, dp, avail, round(score, 2)))

        candidates.sort(key=lambda x: x[3], reverse=True)

        for i, (net, dp, avail, score) in enumerate(candidates[:6], 1):
            lines.append(f"  {i}. {net} / {dp}")
            lines.append(f"     Available: {avail} units")
            lines.append(f"     Audience Equivalence: {score:.0%}")
            recommend = (
                "RECOMMENDED" if score >= 0.85 else "ACCEPTABLE" if score >= 0.70 else "MARGINAL"
            )
            lines.append(f"     Status: {recommend}")

        return "\n".join(lines).strip()
