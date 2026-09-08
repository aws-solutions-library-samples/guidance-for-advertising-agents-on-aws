# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""List Ad Units Tool - Retrieve available inventory from GAM."""

from typing import Optional, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ...config import get_settings


class ListAdUnitsInput(BaseModel):
    """Input schema for listing GAM ad units."""

    include_archived: bool = Field(
        default=False,
        description="Include archived ad units in the results",
    )
    parent_id: Optional[str] = Field(
        default=None,
        description="Filter by parent ad unit ID (for hierarchical navigation)",
    )
    page_size: int = Field(
        default=100,
        description="Number of results to return (max 500)",
    )


class ListAdUnitsTool(BaseTool):
    """Tool for listing available ad units from Google Ad Manager.

    Returns a formatted list of ad units with their IDs, names, sizes,
    and targeting information. Used for inventory discovery and
    mapping products to GAM ad units.
    """

    name: str = "list_gam_ad_units"
    description: str = """List available ad units (inventory) from Google Ad Manager.
    Returns ad unit IDs, names, sizes, and hierarchy for targeting line items."""
    args_schema: Type[BaseModel] = ListAdUnitsInput

    def _run(
        self,
        include_archived: bool = False,
        parent_id: Optional[str] = None,
        page_size: int = 100,
    ) -> str:
        """Execute the ad units listing."""
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

            async def fetch_ad_units():
                async with GAMRestClient(
                    network_code=settings.gam_network_code,
                    credentials_path=settings.gam_json_key_path,
                ) as client:
                    ad_units, _ = await client.list_ad_units(
                        page_size=min(page_size, 500),
                        include_archived=include_archived,
                        parent_id=parent_id,
                    )
                    return ad_units

            # Run async function
            ad_units = asyncio.get_event_loop().run_until_complete(fetch_ad_units())

            if not ad_units:
                return "No ad units found matching the criteria."

            # Format results
            lines = [f"Found {len(ad_units)} ad units:\n"]
            for unit in ad_units:
                sizes = ", ".join(f"{s.width}x{s.height}" for s in (unit.ad_unit_sizes or []))
                lines.append(
                    f"- {unit.name} (ID: {unit.id})\n"
                    f"  Sizes: {sizes or 'Not specified'}\n"
                    f"  Status: {unit.status or 'ACTIVE'}"
                )
                if unit.parent_id:
                    lines.append(f"  Parent: {unit.parent_id}")

            return "\n".join(lines)

        except ImportError as e:
            return f"GAM client dependencies not installed: {e}"
        except Exception as e:
            return f"Error listing ad units: {e}"
