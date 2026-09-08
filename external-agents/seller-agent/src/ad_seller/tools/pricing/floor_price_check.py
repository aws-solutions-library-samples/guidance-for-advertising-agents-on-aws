# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Floor Price Check Tool - Verify price against floors."""

from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ...engines.pricing_rules_engine import PricingRulesEngine
from ...models.buyer_identity import BuyerContext, BuyerIdentity
from ...models.pricing_tiers import TieredPricingConfig


class FloorPriceCheckInput(BaseModel):
    """Input schema for floor price check."""

    offered_price: float = Field(description="Price offered by buyer (CPM)")
    product_floor: float = Field(description="Product's floor price (CPM)")
    buyer_tier: str = Field(
        default="public",
        description="Buyer's access tier: public, seat, agency, or advertiser",
    )


class FloorPriceCheckTool(BaseTool):
    """Tool for checking if an offered price meets floor requirements.

    Validates the offered price against:
    - Global floor price
    - Product-specific floor
    - Negotiation limits based on buyer tier
    """

    name: str = "floor_price_check"
    description: str = """Check if an offered price meets the minimum floor requirements.
    Returns whether the price is acceptable and explains why if not."""
    args_schema: Type[BaseModel] = FloorPriceCheckInput

    def _run(
        self,
        offered_price: float,
        product_floor: float,
        buyer_tier: str = "public",
    ) -> str:
        """Execute the floor price check."""
        # Create buyer context
        identity = BuyerIdentity()
        context = BuyerContext(
            identity=identity,
            is_authenticated=buyer_tier != "public",
        )

        # Create engine
        config = TieredPricingConfig(seller_organization_id="default")
        engine = PricingRulesEngine(config)

        # Check price
        acceptable, reason = engine.is_price_acceptable(
            offered_price=offered_price,
            product_floor=product_floor,
            buyer_context=context,
        )

        if acceptable:
            return f"✓ Price ${offered_price:.2f} CPM is ACCEPTABLE. {reason}"
        else:
            return f"✗ Price ${offered_price:.2f} CPM is NOT ACCEPTABLE. Reason: {reason}"
