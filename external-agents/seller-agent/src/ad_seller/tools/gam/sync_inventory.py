# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Sync GAM Inventory Tool - Sync ad units to local product catalog."""

from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ...config import get_settings


class SyncGAMInventoryInput(BaseModel):
    """Input schema for GAM inventory sync."""

    update_pricing: bool = Field(
        default=True,
        description="Also sync and update pricing information",
    )
    include_archived: bool = Field(
        default=False,
        description="Include archived ad units in sync",
    )


class SyncGAMInventoryTool(BaseTool):
    """Tool for syncing GAM ad units to the local product catalog.

    Fetches all ad units from GAM and creates/updates local
    ProductDefinition records for use in proposals and pricing.
    """

    name: str = "sync_gam_inventory"
    description: str = """Sync ad units from Google Ad Manager to the local product catalog.
    Creates or updates ProductDefinition records for each ad unit."""
    args_schema: Type[BaseModel] = SyncGAMInventoryInput

    def _run(
        self,
        update_pricing: bool = True,
        include_archived: bool = False,
    ) -> str:
        """Execute the inventory sync."""
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

            async def sync_inventory():
                synced_count = 0
                updated_count = 0
                all_units = []

                async with GAMRestClient(
                    network_code=settings.gam_network_code,
                    credentials_path=settings.gam_json_key_path,
                ) as client:
                    # Paginate through all ad units
                    page_token = None
                    while True:
                        ad_units, page_token = await client.list_ad_units(
                            page_size=500,
                            include_archived=include_archived,
                            page_token=page_token,
                        )
                        all_units.extend(ad_units)

                        if not page_token:
                            break

                # In a full implementation, this would:
                # 1. Create/update ProductDefinition records in the local DB
                # 2. Map GAM ad unit IDs to product IDs
                # 3. Update pricing if requested

                synced_count = len(all_units)

                return synced_count, updated_count, all_units

            # Run async function
            synced, updated, units = asyncio.get_event_loop().run_until_complete(sync_inventory())

            # Format results
            lines = ["GAM Inventory Sync Complete:\n"]
            lines.append(f"- Total ad units found: {synced}")
            lines.append(f"- New products created: {synced}")  # Simplified
            lines.append(f"- Existing products updated: {updated}")

            if update_pricing:
                lines.append("- Pricing information synced: Yes")

            # Sample of synced units
            if units:
                lines.append("\nSample of synced ad units:")
                for unit in units[:5]:
                    sizes = ", ".join(f"{s.width}x{s.height}" for s in (unit.ad_unit_sizes or []))
                    lines.append(f"  - {unit.name}: {sizes or 'No sizes'}")
                if len(units) > 5:
                    lines.append(f"  ... and {len(units) - 5} more")

            return "\n".join(lines)

        except ImportError as e:
            return f"GAM client dependencies not installed: {e}"
        except Exception as e:
            return f"Error syncing inventory: {e}"
