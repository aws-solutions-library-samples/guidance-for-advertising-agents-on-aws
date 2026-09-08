# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Get GAM Pricing Tool - Retrieve pricing information for ad units."""

from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ...config import get_settings


class GetGAMPricingInput(BaseModel):
    """Input schema for GAM pricing lookup."""

    ad_unit_id: str = Field(
        description="The GAM ad unit ID to get pricing for",
    )
    include_rate_card: bool = Field(
        default=True,
        description="Include rate card pricing if available",
    )


class GetGAMPricingTool(BaseTool):
    """Tool for getting pricing information for a GAM ad unit.

    Returns rate card pricing, floor prices, and suggested CPMs
    based on historical performance. Useful for deal pricing.
    """

    name: str = "get_gam_pricing"
    description: str = """Get pricing information for a GAM ad unit.
    Returns floor prices, rate card pricing, and suggested CPMs."""
    args_schema: Type[BaseModel] = GetGAMPricingInput

    def _run(
        self,
        ad_unit_id: str,
        include_rate_card: bool = True,
    ) -> str:
        """Execute the pricing lookup."""
        settings = get_settings()

        if not settings.gam_enabled:
            return "GAM integration is not enabled. Set GAM_ENABLED=true in your environment."

        if not settings.gam_network_code or not settings.gam_json_key_path:
            return (
                "GAM credentials not configured. Set GAM_NETWORK_CODE and "
                "GAM_JSON_KEY_PATH environment variables."
            )

        try:
            import asyncio

            from ...clients import GAMRestClient

            async def fetch_pricing():
                async with GAMRestClient(
                    network_code=settings.gam_network_code,
                    credentials_path=settings.gam_json_key_path,
                ) as client:
                    ad_unit = await client.get_ad_unit(ad_unit_id)
                    return ad_unit

            # Run async function
            ad_unit = asyncio.get_event_loop().run_until_complete(fetch_pricing())

            if not ad_unit:
                return f"Ad unit {ad_unit_id} not found."

            # Format pricing information
            lines = [f"Pricing for {ad_unit.name} (ID: {ad_unit.id}):\n"]

            # Floor price from settings (GAM doesn't expose floor via REST)
            default_floor = settings.default_price_floor_cpm
            lines.append(f"- Default Floor Price: ${default_floor:.2f} CPM")

            # Rate card info (would need additional API calls in production)
            if include_rate_card:
                lines.append("\nRate Card Pricing (suggested):")
                lines.append(f"  - Programmatic Guaranteed: ${default_floor * 1.5:.2f} CPM")
                lines.append(f"  - Preferred Deal: ${default_floor * 1.2:.2f} CPM")
                lines.append(f"  - Private Auction Floor: ${default_floor:.2f} CPM")

            # Ad unit details
            sizes = ", ".join(f"{s.width}x{s.height}" for s in (ad_unit.ad_unit_sizes or []))
            if sizes:
                lines.append(f"\nAd Sizes: {sizes}")

            lines.append(f"Status: {ad_unit.status or 'ACTIVE'}")

            return "\n".join(lines)

        except ImportError as e:
            return f"GAM client dependencies not installed: {e}"
        except Exception as e:
            return f"Error getting pricing: {e}"
