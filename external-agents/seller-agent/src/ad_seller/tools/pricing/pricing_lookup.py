# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Pricing Lookup Tool - Get pricing for products based on buyer context."""

from typing import Optional, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ...engines.pricing_rules_engine import PricingRulesEngine
from ...models.buyer_identity import BuyerContext, BuyerIdentity
from ...models.core import DealType
from ...models.pricing_tiers import TieredPricingConfig


class PricingLookupInput(BaseModel):
    """Input schema for pricing lookup."""

    product_id: str = Field(description="Product ID to get pricing for")
    base_price: float = Field(description="Base/MSRP price for the product")
    buyer_tier: str = Field(
        default="public",
        description="Buyer's access tier: public, seat, agency, or advertiser",
    )
    agency_id: Optional[str] = Field(default=None, description="Agency ID if known")
    advertiser_id: Optional[str] = Field(default=None, description="Advertiser ID if known")
    volume: int = Field(default=0, description="Number of impressions for volume discounts")
    deal_type: str = Field(default="preferred_deal", description="Deal type: pg, pd, or pa")


class PricingLookupTool(BaseTool):
    """Tool for looking up tiered pricing based on buyer identity.

    Returns pricing appropriate for the buyer's access tier:
    - Public: Price ranges
    - Agency: Agency-specific rates
    - Advertiser: Best available rates with volume discounts
    """

    name: str = "pricing_lookup"
    description: str = """Look up pricing for a product based on buyer identity and volume.
    Returns the appropriate price or range based on the buyer's access tier."""
    args_schema: Type[BaseModel] = PricingLookupInput

    def _run(
        self,
        product_id: str,
        base_price: float,
        buyer_tier: str = "public",
        agency_id: Optional[str] = None,
        advertiser_id: Optional[str] = None,
        volume: int = 0,
        deal_type: str = "preferred_deal",
    ) -> str:
        """Execute the pricing lookup."""
        # Create buyer context
        identity = BuyerIdentity(
            agency_id=agency_id,
            advertiser_id=advertiser_id,
        )
        context = BuyerContext(
            identity=identity,
            is_authenticated=buyer_tier != "public",
        )

        # Map deal type string
        deal_type_map = {
            "pg": DealType.PROGRAMMATIC_GUARANTEED,
            "programmatic_guaranteed": DealType.PROGRAMMATIC_GUARANTEED,
            "pd": DealType.PREFERRED_DEAL,
            "preferred_deal": DealType.PREFERRED_DEAL,
            "pa": DealType.PRIVATE_AUCTION,
            "private_auction": DealType.PRIVATE_AUCTION,
        }
        deal_type_enum = deal_type_map.get(deal_type.lower(), DealType.PREFERRED_DEAL)

        # Create engine with default config
        config = TieredPricingConfig(seller_organization_id="default")
        engine = PricingRulesEngine(config)

        # Calculate price
        decision = engine.calculate_price(
            product_id=product_id,
            base_price=base_price,
            buyer_context=context,
            deal_type=deal_type_enum,
            volume=volume,
        )

        # Format response
        response = f"""
Pricing for {product_id}:
- Buyer Tier: {decision.buyer_tier}
- Base Price: ${decision.base_price:.2f} CPM
- Final Price: ${decision.final_price:.2f} CPM
- Tier Discount: {decision.tier_discount * 100:.0f}%
- Volume Discount: {decision.volume_discount * 100:.1f}%
- Rationale: {decision.rationale}
"""
        return response.strip()
