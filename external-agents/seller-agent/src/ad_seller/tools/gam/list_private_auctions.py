# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""List Private Auctions Tool - List private auctions in GAM."""

from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ...config import get_settings


class ListPrivateAuctionsInput(BaseModel):
    """Input schema for listing GAM private auctions."""

    include_archived: bool = Field(
        default=False,
        description="Include archived/inactive private auctions",
    )


class ListPrivateAuctionsTool(BaseTool):
    """Tool for listing private auctions in Google Ad Manager.

    Private auctions are used for non-reserved deals where buyers
    can bid at or above a floor price. Returns auction IDs and details.
    """

    name: str = "list_private_auctions"
    description: str = """List private auctions in Google Ad Manager.
    Returns auction IDs, names, and status for creating private auction deals."""
    args_schema: Type[BaseModel] = ListPrivateAuctionsInput

    def _run(
        self,
        include_archived: bool = False,
    ) -> str:
        """Execute private auctions listing."""
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

            async def fetch_auctions():
                async with GAMRestClient(
                    network_code=settings.gam_network_code,
                    credentials_path=settings.gam_json_key_path,
                ) as client:
                    auctions = await client.list_private_auctions()
                    return auctions

            # Run async function
            auctions = asyncio.get_event_loop().run_until_complete(fetch_auctions())

            if not auctions:
                return (
                    "No private auctions found.\n"
                    "To create private auction deals, first create a private auction "
                    "in the GAM UI under Delivery > Programmatic > Private Auctions."
                )

            # Filter if needed
            if not include_archived:
                auctions = [a for a in auctions if a.status != "ARCHIVED"]

            # Format results
            lines = [f"Found {len(auctions)} private auction(s):\n"]
            for auction in auctions:
                lines.append(
                    f"- {auction.display_name or auction.id}\n"
                    f"  ID: {auction.id}\n"
                    f"  Status: {auction.status or 'ACTIVE'}"
                )
                if auction.description:
                    lines.append(f"  Description: {auction.description}")

            lines.append("\nTo create a deal, use create_private_auction_deal with the auction ID.")

            return "\n".join(lines)

        except ImportError as e:
            return f"GAM client dependencies not installed: {e}"
        except Exception as e:
            return f"Error listing private auctions: {e}"
