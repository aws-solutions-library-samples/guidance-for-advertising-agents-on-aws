# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Pricing tier models for identity-based pricing rules.

Sellers can configure tiered pricing based on buyer identity:
- Public pricing with ranges (MSRP-like)
- Agency-level fixed pricing
- Advertiser-level pricing with volume incentives
"""

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from .buyer_identity import AccessTier


class DiscountType(str, Enum):
    """Type of discount applied."""

    PERCENTAGE = "percentage"
    FIXED_AMOUNT = "fixed_amount"
    FIXED_PRICE = "fixed_price"


class VolumeDiscount(BaseModel):
    """Volume-based discount tier."""

    min_impressions: int
    max_impressions: Optional[int] = None
    discount_type: DiscountType = DiscountType.PERCENTAGE
    discount_value: float  # Percentage (0-1) or fixed amount


class PricingRule(BaseModel):
    """Pricing rule for a specific context."""

    rule_id: str
    rule_name: str
    priority: int = 0  # Higher priority rules evaluated first

    # Matching criteria
    access_tier: Optional[AccessTier] = None
    agency_ids: list[str] = Field(default_factory=list)
    advertiser_ids: list[str] = Field(default_factory=list)
    holding_company_ids: list[str] = Field(default_factory=list)
    product_ids: list[str] = Field(default_factory=list)
    inventory_types: list[str] = Field(default_factory=list)

    # Pricing output
    base_price_override: Optional[float] = None
    discount_percentage: float = 0.0
    price_floor: Optional[float] = None
    price_ceiling: Optional[float] = None

    # Volume discounts
    volume_discounts: list[VolumeDiscount] = Field(default_factory=list)

    # Negotiation limits
    negotiation_enabled: bool = False
    max_negotiation_discount: float = 0.0  # Maximum additional discount from negotiation

    # Validity
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    is_active: bool = True

    def matches(
        self,
        tier: AccessTier,
        agency_id: Optional[str] = None,
        advertiser_id: Optional[str] = None,
        holding_company: Optional[str] = None,
        product_id: Optional[str] = None,
        inventory_type: Optional[str] = None,
    ) -> bool:
        """Check if this rule matches the given context."""
        # Check access tier
        if self.access_tier and self.access_tier != tier:
            return False

        # Check agency
        if self.agency_ids and agency_id not in self.agency_ids:
            return False

        # Check advertiser
        if self.advertiser_ids and advertiser_id not in self.advertiser_ids:
            return False

        # Check holding company
        if self.holding_company_ids and holding_company not in self.holding_company_ids:
            return False

        # Check product
        if self.product_ids and product_id not in self.product_ids:
            return False

        # Check inventory type
        if self.inventory_types and inventory_type not in self.inventory_types:
            return False

        return True


class PricingTier(BaseModel):
    """Pricing tier configuration for an access level."""

    tier: AccessTier
    tier_name: str
    description: str

    # Price display mode
    show_exact_price: bool = False  # False = show ranges
    price_range_variance: float = 0.2  # +/- 20% for ranges

    # Base discount from MSRP
    tier_discount: float = 0.0

    # Features
    negotiation_enabled: bool = False
    premium_inventory_access: bool = False
    custom_deals_enabled: bool = False
    volume_discounts_enabled: bool = False

    # Avails granularity
    avails_granularity: str = "high_level"  # high_level, moderate, detailed


class TieredPricingConfig(BaseModel):
    """Complete tiered pricing configuration for a seller."""

    seller_organization_id: str

    # Tier configurations
    tiers: dict[AccessTier, PricingTier] = Field(default_factory=dict)

    # Global pricing rules (applied after tier discounts)
    rules: list[PricingRule] = Field(default_factory=list)

    # Default pricing
    default_currency: str = "USD"
    global_floor_cpm: float = 1.0
    global_ceiling_cpm: Optional[float] = None

    # Cross-agency consistency
    advertiser_pricing_consistent: bool = True  # Same advertiser = same price across agencies

    def __init__(self, **data: Any) -> None:
        """Initialize with default tier configurations if not provided."""
        super().__init__(**data)
        if not self.tiers:
            self.tiers = self._default_tiers()

    def _default_tiers(self) -> dict[AccessTier, PricingTier]:
        """Create default tier configurations."""
        return {
            AccessTier.PUBLIC: PricingTier(
                tier=AccessTier.PUBLIC,
                tier_name="Public",
                description="General product catalog with price ranges",
                show_exact_price=False,
                price_range_variance=0.2,
                tier_discount=0.0,
                negotiation_enabled=False,
                premium_inventory_access=False,
                custom_deals_enabled=False,
                volume_discounts_enabled=False,
                avails_granularity="high_level",
            ),
            AccessTier.SEAT: PricingTier(
                tier=AccessTier.SEAT,
                tier_name="Seat",
                description="Authenticated DSP seat with standard pricing",
                show_exact_price=True,
                tier_discount=0.05,  # 5% off MSRP
                negotiation_enabled=False,
                premium_inventory_access=False,
                custom_deals_enabled=True,
                volume_discounts_enabled=False,
                avails_granularity="moderate",
            ),
            AccessTier.AGENCY: PricingTier(
                tier=AccessTier.AGENCY,
                tier_name="Agency",
                description="Agency-specific pricing with negotiation",
                show_exact_price=True,
                tier_discount=0.10,  # 10% off MSRP
                negotiation_enabled=True,
                premium_inventory_access=True,
                custom_deals_enabled=True,
                volume_discounts_enabled=True,
                avails_granularity="detailed",
            ),
            AccessTier.ADVERTISER: PricingTier(
                tier=AccessTier.ADVERTISER,
                tier_name="Advertiser",
                description="Best available rates with full negotiation",
                show_exact_price=True,
                tier_discount=0.15,  # 15% off MSRP
                negotiation_enabled=True,
                premium_inventory_access=True,
                custom_deals_enabled=True,
                volume_discounts_enabled=True,
                avails_granularity="detailed",
            ),
        }

    def get_tier_config(self, tier: AccessTier) -> PricingTier:
        """Get configuration for a specific tier."""
        return self.tiers.get(tier, self.tiers[AccessTier.PUBLIC])

    def find_matching_rules(
        self,
        tier: AccessTier,
        agency_id: Optional[str] = None,
        advertiser_id: Optional[str] = None,
        holding_company: Optional[str] = None,
        product_id: Optional[str] = None,
        inventory_type: Optional[str] = None,
    ) -> list[PricingRule]:
        """Find all rules matching the given context, sorted by priority."""
        matching = [
            rule
            for rule in self.rules
            if rule.is_active
            and rule.matches(
                tier=tier,
                agency_id=agency_id,
                advertiser_id=advertiser_id,
                holding_company=holding_company,
                product_id=product_id,
                inventory_type=inventory_type,
            )
        ]
        return sorted(matching, key=lambda r: r.priority, reverse=True)
