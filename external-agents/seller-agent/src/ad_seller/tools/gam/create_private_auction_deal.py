# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Create Private Auction Deal Tool - Create private auction deals in GAM."""

from typing import Optional, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ...config import get_settings


class CreatePrivateAuctionDealInput(BaseModel):
    """Input schema for creating a GAM private auction deal."""

    private_auction_id: str = Field(
        description="The GAM private auction ID (use list_private_auctions to find)",
    )
    buyer_account_id: str = Field(
        description="The buyer's account ID (authorized buyer network ID)",
    )
    floor_price: float = Field(
        description="Floor price CPM in dollars (e.g., 10.00 for $10 CPM floor)",
    )
    currency: str = Field(
        default="USD",
        description="Currency code (ISO 4217)",
    )
    ad_unit_ids: Optional[list[str]] = Field(
        default=None,
        description="GAM ad unit IDs to target (optional, defaults to auction targeting)",
    )
    external_deal_id: Optional[str] = Field(
        default=None,
        description="External reference ID (e.g., OpenDirect proposal line ID)",
    )


class CreatePrivateAuctionDealTool(BaseTool):
    """Tool for creating private auction deals in Google Ad Manager.

    Private auction deals allow specific buyers to bid on inventory
    at or above a floor price. Unlike PG/Preferred deals, private
    auctions do NOT create orders or line items - they use a
    separate PrivateAuctionDeal structure.

    Important notes:
    - Private auctions are non-reserved (no impression guarantee)
    - Can use first-price or second-price auction mechanics
    - Floor price is the minimum CPM for bidding
    """

    name: str = "create_private_auction_deal"
    description: str = """Create a private auction deal in Google Ad Manager.
    For OpenDirect private_auction deal type. Returns the deal ID."""
    args_schema: Type[BaseModel] = CreatePrivateAuctionDealInput

    def _run(
        self,
        private_auction_id: str,
        buyer_account_id: str,
        floor_price: float,
        currency: str = "USD",
        ad_unit_ids: Optional[list[str]] = None,
        external_deal_id: Optional[str] = None,
    ) -> str:
        """Execute private auction deal creation."""
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

            async def create_deal():
                async with GAMRestClient(
                    network_code=settings.gam_network_code,
                    credentials_path=settings.gam_json_key_path,
                ) as client:
                    deal = await client.create_private_auction_deal(
                        private_auction_id=private_auction_id,
                        buyer_account_id=buyer_account_id,
                        floor_price=floor_price,
                        currency=currency,
                        ad_unit_ids=ad_unit_ids,
                        external_deal_id=external_deal_id,
                    )
                    return deal

            # Run async function
            deal = asyncio.get_event_loop().run_until_complete(create_deal())

            # Format response
            lines = [
                "Private auction deal created successfully:\n",
                f"- Deal ID: {deal.id}",
                f"- Private Auction ID: {deal.private_auction_id}",
                f"- Buyer Account ID: {deal.buyer_account_id}",
                f"- Floor Price: ${floor_price:.2f} {currency} CPM",
                f"- Status: {deal.status or 'ACTIVE'}",
            ]

            if deal.external_deal_id:
                lines.append(f"- External Deal ID: {deal.external_deal_id}")

            if ad_unit_ids:
                lines.append(f"- Targeted ad units: {len(ad_unit_ids)}")

            lines.append(
                "\nNote: Private auction deals are non-reserved. "
                "Buyers can bid at or above the floor price."
            )

            return "\n".join(lines)

        except ImportError as e:
            return f"GAM client dependencies not installed: {e}"
        except ValueError as e:
            return f"Input error: {e}"
        except Exception as e:
            return f"Error creating private auction deal: {e}"
