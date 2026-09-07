# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Google Ad Manager tools for CrewAI agents.

Provides tools for:
- Inventory management (list ad units, sync inventory, get pricing)
- Reserved deal booking (orders, line items for PG/Preferred deals)
- Non-reserved deal booking (private auction deals)
- Audience segment management (list, sync, IAB taxonomy mapping)
"""

from .book_deal import BookDealInGAMTool
from .create_line_item import CreateGAMLineItemTool
from .create_order import CreateGAMOrderTool
from .create_private_auction_deal import CreatePrivateAuctionDealTool
from .get_pricing import GetGAMPricingTool
from .list_ad_units import ListAdUnitsTool
from .list_audience_segments import ListAudienceSegmentsTool
from .list_private_auctions import ListPrivateAuctionsTool
from .sync_audiences import SyncGAMAudiencesTool
from .sync_inventory import SyncGAMInventoryTool

__all__ = [
    # Inventory tools
    "ListAdUnitsTool",
    "GetGAMPricingTool",
    "SyncGAMInventoryTool",
    # Reserved deals (PG, Preferred) - via Order/LineItem
    "CreateGAMOrderTool",
    "CreateGAMLineItemTool",
    "BookDealInGAMTool",
    # Non-reserved deals (Private Auction) - via PrivateAuction API
    "ListPrivateAuctionsTool",
    "CreatePrivateAuctionDealTool",
    # Audience
    "ListAudienceSegmentsTool",
    "SyncGAMAudiencesTool",
]
