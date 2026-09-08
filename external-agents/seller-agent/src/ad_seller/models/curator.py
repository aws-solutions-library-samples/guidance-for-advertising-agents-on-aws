# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Curator models.

A curator packages/selects inventory they don't own on behalf of buyers.
They add value through audience data, content curation, brand safety,
or cross-publisher packaging. The curator appears as a node in the
OpenRTB schain and may take a fee on transactions.

Day-one curator: Agent Range — optimizes Deal Library deals and curates
inventory for agencies using their proprietary models.
"""

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel


class CuratorType(str, Enum):
    """Type of curation service provided."""

    AUDIENCE = "audience"  # Layers audience segments (e.g., Audigent)
    CONTENT = "content"  # Curates content verticals / brand safety
    PACKAGE = "package"  # Bundles inventory across publishers
    OPTIMIZATION = "optimization"  # Deal/supply path optimization (e.g., Agent Range)
    FULL_SERVICE = "full_service"  # Combination of above


class CuratorFeeType(str, Enum):
    """How the curator charges for their service."""

    CPM_FLAT = "cpm_flat"  # Flat CPM added on top (e.g., +$2.00 CPM)
    PERCENT = "percent"  # Percentage of deal price (e.g., 10%)
    FIXED = "fixed"  # Fixed fee per deal
    NONE = "none"  # No fee (value-add curator)


class CuratorFee(BaseModel):
    """Curator fee structure."""

    fee_type: CuratorFeeType = CuratorFeeType.PERCENT
    fee_value: float = 0.0  # Amount: CPM dollars, percentage (0-100), or fixed dollars
    currency: str = "USD"

    def calculate_fee(self, base_cpm: float, impressions: int = 0) -> float:
        """Calculate the curator fee for a given deal.

        Returns:
            Fee amount in dollars.
        """
        if self.fee_type == CuratorFeeType.CPM_FLAT:
            return self.fee_value * (impressions / 1000) if impressions else self.fee_value
        elif self.fee_type == CuratorFeeType.PERCENT:
            return base_cpm * (self.fee_value / 100)
        elif self.fee_type == CuratorFeeType.FIXED:
            return self.fee_value
        return 0.0

    def calculate_curated_cpm(self, base_cpm: float) -> float:
        """Calculate the buyer-facing CPM including curator fee.

        Returns:
            Total CPM the buyer pays (publisher CPM + curator fee).
        """
        if self.fee_type == CuratorFeeType.CPM_FLAT:
            return base_cpm + self.fee_value
        elif self.fee_type == CuratorFeeType.PERCENT:
            return base_cpm * (1 + self.fee_value / 100)
        elif self.fee_type == CuratorFeeType.FIXED:
            return base_cpm  # Fixed fee doesn't change CPM
        return base_cpm


class Curator(BaseModel):
    """A registered curator in the seller agent.

    Curators can create deals against the publisher's inventory,
    apply their own audience/content targeting, and appear in the
    deal's schain as a node.
    """

    curator_id: str
    name: str
    domain: str  # Curator's canonical domain (for schain asi)
    curator_type: CuratorType = CuratorType.FULL_SERVICE
    description: Optional[str] = None

    # Fee structure
    fee: CuratorFee = CuratorFee()

    # Identity
    contact_email: Optional[str] = None
    api_key: Optional[str] = None  # Curator's API key for authenticated access

    # Capabilities
    audience_segments: list[str] = []  # Curator's proprietary segments
    content_categories: list[str] = []  # Content verticals curator covers
    supported_deal_types: list[str] = ["pmp", "preferred", "pg"]

    # Status
    is_active: bool = True

    # Metadata
    tags: list[str] = []
    metadata: Optional[dict[str, Any]] = None


class CuratedDeal(BaseModel):
    """A deal with curator overlay applied.

    Extends the base deal with curator identity, fee, and
    curator-specific targeting.
    """

    deal_id: str
    curator_id: str
    curator_name: str
    curator_domain: str

    # Pricing with curator fee
    base_cpm: float  # Publisher's price
    curator_fee_cpm: float  # Curator's fee as CPM equivalent
    total_cpm: float  # What the buyer pays

    # Curator's targeting overlay
    curator_audience_segments: list[str] = []
    curator_content_categories: list[str] = []

    # schain node for the curator
    curator_schain_node: Optional[dict[str, Any]] = None


# =============================================================================
# Pre-configured curators
# =============================================================================

# Agent Range — day-one curator for Deal Library optimization
AGENT_RANGE_CURATOR = Curator(
    curator_id="agent-range",
    name="Agent Range",
    domain="agentrange.com",
    curator_type=CuratorType.OPTIMIZATION,
    description=(
        "AI-powered deal and supply path optimization. "
        "Curates inventory using proprietary models to maximize "
        "deal performance for agencies and advertisers."
    ),
    fee=CuratorFee(fee_type=CuratorFeeType.PERCENT, fee_value=10.0),
    supported_deal_types=["pmp", "preferred", "pg", "auction_package"],
    tags=["optimization", "ai", "deal-library"],
)
