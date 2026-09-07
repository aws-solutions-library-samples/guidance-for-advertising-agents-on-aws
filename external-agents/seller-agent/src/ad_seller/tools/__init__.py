# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""CrewAI tools for the Ad Seller System."""

from .audience import (
    AudienceCapabilityTool,
    AudienceValidationTool,
    CoverageCalculatorTool,
)
from .availability import AvailsCheckTool, ForecastTool
from .deal_library import (
    BulkDealOperationsTool,
    CreateDealFromTemplateTool,
    GetDealPerformanceTool,
    GetSupplyChainTool,
)
from .gam import (
    BookDealInGAMTool,
    CreateGAMLineItemTool,
    # Booking tools (reserved deals)
    CreateGAMOrderTool,
    CreatePrivateAuctionDealTool,
    GetGAMPricingTool,
    # Inventory tools
    ListAdUnitsTool,
    # Audience tools
    ListAudienceSegmentsTool,
    # Private auction tools (non-reserved deals)
    ListPrivateAuctionsTool,
    SyncGAMAudiencesTool,
    SyncGAMInventoryTool,
)
from .pricing import FloorPriceCheckTool, PricingLookupTool
from .proposal import CounterProposalTool, ProposalValidationTool

__all__ = [
    # Pricing tools
    "PricingLookupTool",
    "FloorPriceCheckTool",
    # Availability tools
    "AvailsCheckTool",
    "ForecastTool",
    # Proposal tools
    "ProposalValidationTool",
    "CounterProposalTool",
    # Audience tools
    "AudienceValidationTool",
    "AudienceCapabilityTool",
    "CoverageCalculatorTool",
    # GAM Inventory tools
    "ListAdUnitsTool",
    "GetGAMPricingTool",
    "SyncGAMInventoryTool",
    # GAM Booking tools (Programmatic Guaranteed, Preferred Deal)
    "CreateGAMOrderTool",
    "CreateGAMLineItemTool",
    "BookDealInGAMTool",
    # GAM Private Auction tools
    "ListPrivateAuctionsTool",
    "CreatePrivateAuctionDealTool",
    # GAM Audience tools (with IAB Audience Taxonomy 1.1 support)
    "ListAudienceSegmentsTool",
    "SyncGAMAudiencesTool",
    # Deal Library tools
    "GetSupplyChainTool",
    "GetDealPerformanceTool",
    "BulkDealOperationsTool",
    "CreateDealFromTemplateTool",
]
